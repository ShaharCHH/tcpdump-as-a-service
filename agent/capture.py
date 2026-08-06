"""
tcpdump capture execution on the agent node.

How it works
------------
1. find_container_pid: Given a container ID (e.g. "containerd://abc123..."), resolve
   the PID of a process inside that container. Two strategies, tried in order:

     a. Ask the container runtime, via the node's own crictl. We enter PID 1's mount
        namespace (`nsenter -t 1 -m`) so we see the host filesystem — that gives us
        the host's /usr/bin/crictl and its /etc/crictl.yaml, which is already
        configured for whichever runtime the node uses (CRI-O or containerd). This
        is the programmatic equivalent of `oc debug node/<n>` + `chroot /host` +
        `crictl inspect <id> | jq .info.pid`. Authoritative and O(1).

     b. Fall back to scanning /host/proc/*/cgroup for the short container ID. This
        works on nodes without crictl (e.g. k3s/k3d, where it ships as `k3s crictl`),
        but it FAILS on cgroup v2 nodes that give each pod a private cgroup namespace
        — the kernel renders /proc/<pid>/cgroup relative to the *reader's* cgroup
        namespace root, so the container ID never appears in the text at all. There
        is no pod-spec field to opt out of that, which is why (a) is preferred.

   The host /proc is mounted at /host/proc (not /proc) so that the container's own
   /proc stays intact — overwriting it read-only breaks CRI-O's SELinux labeling at
   startup. hostPID: true makes all host PIDs visible.

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
import json
import logging
import os
import re
from pathlib import Path
from typing import AsyncGenerator

logger = logging.getLogger(__name__)

# How many bytes to send per pcap chunk over WebSocket (~256 KB)
CHUNK_SIZE = 256 * 1024

# Give up on the runtime lookup rather than stalling a capture behind a wedged
# CRI socket — the cgroup scan fallback still gets a chance afterwards.
CRICTL_TIMEOUT_SECONDS = 10


async def find_container_pid(container_id: str) -> tuple[int | None, str]:
    """
    Resolve a PID inside the target container.

    container_id comes from the Kubernetes API in the form:
      "containerd://abc123def456..."  or  "docker://abc123def456..."

    Returns (pid, reason). On success reason is "". On failure pid is None and
    reason explains what each strategy tried and how it failed, so the message
    that reaches the user says something more useful than "not found".
    """
    # Strip "containerd://", "docker://", "cri-o://", etc.
    full_id = re.sub(r"^[a-z\-]+://", "", container_id or "")
    if not full_id:
        return None, "the hub supplied no container ID (is the container running?)"

    pid, crictl_reason = await _find_pid_via_crictl(full_id)
    if pid is not None:
        logger.debug("find_container_pid: crictl resolved %s → pid %d", full_id[:12], pid)
        return pid, ""

    logger.warning(
        "find_container_pid: crictl lookup failed for %s (%s) — falling back to /host/proc scan",
        full_id[:12], crictl_reason,
    )

    pid, scan_reason = _find_pid_via_cgroup_scan(full_id)
    if pid is not None:
        logger.debug("find_container_pid: cgroup scan resolved %s → pid %d", full_id[:12], pid)
        return pid, ""

    return None, f"crictl: {crictl_reason}; /host/proc scan: {scan_reason}"


async def _find_pid_via_crictl(full_id: str) -> tuple[int | None, str]:
    """
    Ask the node's container runtime for the container's PID.

    `nsenter -t 1 -m` puts us in PID 1's mount namespace, i.e. the host filesystem,
    so `crictl` resolves to the node's own binary and picks up the node's
    /etc/crictl.yaml. That means we don't have to know or configure which runtime
    socket this node uses. Requires privileged: true and hostPID: true, both of
    which the DaemonSet already sets for nsenter's sake.
    """
    cmd = [
        "nsenter", "-t", "1", "-m", "--",
        "crictl", "inspect", "-o", "json", full_id,
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return None, "nsenter not found in the agent image"

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=CRICTL_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return None, f"timed out after {CRICTL_TIMEOUT_SECONDS}s"

    if proc.returncode != 0:
        # Exit 127 here almost always means the node has no crictl on PATH
        # (k3s ships it as `k3s crictl`), which is expected on k3d dev clusters.
        detail = stderr.decode(errors="replace").strip().replace("\n", " ")[:200]
        return None, f"exited {proc.returncode}: {detail or 'no stderr'}"

    try:
        info = json.loads(stdout)["info"]
        pid = info["pid"]
    except (ValueError, KeyError, TypeError) as e:
        return None, f"could not read .info.pid from output ({e})"

    # crictl reports pid 0 for a container that exists but isn't running.
    if not isinstance(pid, int) or pid <= 0:
        return None, f"runtime reported pid={pid!r} (container not running?)"

    return pid, ""


def _find_pid_via_cgroup_scan(full_id: str) -> tuple[int | None, str]:
    """
    Search /host/proc/*/cgroup for the container's short ID to find a PID inside it.

    Uses the first 12 characters (like `docker ps`). See the module docstring for
    why this can legitimately find nothing on cgroup v2 nodes.
    """
    short_id = full_id[:12]
    scanned = 0

    # /host/proc is the host's /proc mounted read-only at a safe path.
    # Path structure: /host/proc/<pid>/cgroup → split index 3 is the PID.
    for cgroup_path in glob.glob("/host/proc/*/cgroup"):
        try:
            with open(cgroup_path) as f:
                content = f.read()
            scanned += 1
            if short_id in content:
                pid_str = cgroup_path.split("/")[3]
                if pid_str.isdigit():
                    return int(pid_str), ""
        except (PermissionError, FileNotFoundError, ProcessLookupError):
            continue

    if scanned == 0:
        return None, "read no cgroup files (is /host/proc mounted?)"
    return None, f"{short_id} not present in any of {scanned} cgroup files"


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
    pid, reason = await find_container_pid(container_id)
    if pid is None:
        yield {
            "type": "ERROR",
            "message": f"Could not resolve a PID for container {container_id[:20]} — {reason}",
        }
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
