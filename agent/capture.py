"""
tcpdump capture execution on the agent node.

How it works
------------
1. find_container_pid: Given a container ID (e.g. "containerd://abc123..."), search
   /proc/*/cgroup for the short container ID to find the PID of a process in that
   container. This requires the agent pod to run with hostPID: true so that all host
   PIDs are visible inside the agent's /proc.

2. run_capture: Uses `nsenter -t <pid> -n` to enter the target container's network
   namespace, then runs tcpdump there. This captures only that container's traffic
   without touching the target pod at all.

   Two tcpdump processes run in parallel:
     - A "text" process writing decoded packets to stderr for the live stream
     - A "pcap" process writing rotating .pcap files for later download

3. stream_pcap_file: After the capture ends, read each .pcap file and yield it as
   base64-encoded chunks so the hub can write them to the PVC over WebSocket.
"""
from __future__ import annotations

import asyncio
import base64
import glob
import logging
import os
import re
from pathlib import Path
from typing import AsyncGenerator

logger = logging.getLogger(__name__)

# How many bytes to send per pcap chunk over WebSocket (~256 KB)
CHUNK_SIZE = 256 * 1024


def find_container_pid(container_id: str) -> int | None:
    """
    Search /proc/*/cgroup for the container's short ID to find a PID inside it.

    container_id comes from the Kubernetes API in the form:
      "containerd://abc123def456..."  or  "docker://abc123def456..."

    We strip the scheme prefix and use the first 12 characters (like `docker ps`).
    """
    # Strip "containerd://", "docker://", "cri-o://", etc.
    short_id = re.sub(r"^[a-z\-]+://", "", container_id)[:12]

    for cgroup_path in glob.glob("/proc/*/cgroup"):
        try:
            with open(cgroup_path) as f:
                content = f.read()
            if short_id in content:
                pid_str = cgroup_path.split("/")[2]
                if pid_str.isdigit():
                    return int(pid_str)
        except (PermissionError, FileNotFoundError, ProcessLookupError):
            continue

    return None


def build_bpf_filter(filters: dict) -> str:
    """
    Construct a BPF filter expression from the user-supplied filter dict.
    Each field maps to a standard BPF primitive.
    """
    parts: list[str] = []

    if filters.get("host"):
        parts.append(f"host {filters['host']}")
    if filters.get("src_host"):
        parts.append(f"src host {filters['src_host']}")
    if filters.get("dst_host"):
        parts.append(f"dst host {filters['dst_host']}")
    if filters.get("src_port"):
        parts.append(f"src port {filters['src_port']}")
    if filters.get("dst_port"):
        parts.append(f"dst port {filters['dst_port']}")

    return " and ".join(parts)


async def run_capture(
    container_id: str,
    filters: dict,
    duration_seconds: int,
    capture_id: str,
    output_dir: str,
) -> AsyncGenerator[dict, None]:
    """
    Run a tcpdump capture inside the target container's network namespace.
    Yields event dicts:
      {"type": "PACKET_LINE", "line": "..."}         — one decoded packet per line
      {"type": "PCAP_CHUNK", "filename": "...", "data": "<base64>"}  — pcap file chunk
      {"type": "CAPTURE_DONE"}                        — all done
      {"type": "ERROR", "message": "..."}             — something went wrong
    """
    pid = find_container_pid(container_id)
    if pid is None:
        yield {"type": "ERROR", "message": f"Container {container_id[:20]} not found in /proc"}
        return

    bpf = build_bpf_filter(filters)
    interface = filters.get("interface", "any")

    os.makedirs(output_dir, exist_ok=True)

    # Rotation parameters passed to tcpdump:
    #   -G <sec>  rotate file every N seconds
    #   -C <MB>   also rotate when file exceeds N megabytes
    #   -W <n>    keep only the last N files (ring buffer)
    pcap_pattern = os.path.join(output_dir, "capture_%Y%m%d%H%M%S.pcap")

    # --- Process 1: write rotating .pcap files ---
    pcap_cmd = [
        "nsenter", "-t", str(pid), "-n", "--",
        "tcpdump", "-i", interface,
        "-w", pcap_pattern,
        "-G", str(max(duration_seconds, 10)),  # rotate at least once at the end
        "-C", "50",   # also rotate at 50 MB
        "-W", "10",   # ring buffer of 10 files
        "--immediate-mode",
    ]
    if bpf:
        pcap_cmd.append(bpf)

    # --- Process 2: text output for the live stream ---
    # tcpdump prints decoded packet lines to stderr; -l makes it line-buffered.
    text_cmd = [
        "nsenter", "-t", str(pid), "-n", "--",
        "tcpdump", "-i", interface,
        "-l", "-nn", "-v",
    ]
    if filters.get("packet_count"):
        text_cmd += ["-c", str(filters["packet_count"])]
    if bpf:
        text_cmd.append(bpf)

    try:
        pcap_proc = await asyncio.create_subprocess_exec(
            *pcap_cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        text_proc = await asyncio.create_subprocess_exec(
            *text_cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,  # tcpdump writes to stderr
        )
    except FileNotFoundError:
        yield {"type": "ERROR", "message": "nsenter or tcpdump not found on agent node"}
        return

    # Kill both processes after the user-specified duration
    async def _kill_after() -> None:
        await asyncio.sleep(duration_seconds)
        for p in (pcap_proc, text_proc):
            try:
                p.terminate()
            except ProcessLookupError:
                pass

    kill_task = asyncio.create_task(_kill_after())

    # Stream text output line by line
    assert text_proc.stderr is not None
    async for raw_line in text_proc.stderr:
        line = raw_line.decode(errors="replace").rstrip()
        if line:
            yield {"type": "PACKET_LINE", "line": line}

    # Wait for both processes to exit
    await asyncio.gather(text_proc.wait(), pcap_proc.wait())
    kill_task.cancel()

    # Stream all .pcap files back to the hub as base64 chunks
    pcap_files = sorted(glob.glob(os.path.join(output_dir, "capture_*.pcap")))
    for pcap_path in pcap_files:
        filename = os.path.basename(pcap_path)
        async for chunk_event in stream_pcap_file(pcap_path, filename):
            yield chunk_event

    yield {"type": "CAPTURE_DONE"}


async def stream_pcap_file(
    file_path: str, filename: str
) -> AsyncGenerator[dict, None]:
    """Read a pcap file in chunks and yield base64-encoded PCAP_CHUNK events."""
    try:
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                yield {
                    "type": "PCAP_CHUNK",
                    "filename": filename,
                    "data": base64.b64encode(chunk).decode(),
                }
    except OSError as e:
        logger.error("Failed to read pcap file %s: %s", file_path, e)
