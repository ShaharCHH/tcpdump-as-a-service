"""
pcap file storage and cleanup.

The hub receives pcap file chunks from agents (sent as base64-encoded JSON over
the agent WebSocket) and writes them to the PVC mounted at PCAP_STORAGE_PATH.

Directory layout:
  /pcaps/<capture_id>/<pod_key_safe>/<filename>.pcap

pod_key_safe replaces "/" with "__" so it's a valid directory name.

A background task runs every hour to delete captures older than PCAP_TTL_HOURS.
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import time
import zipfile
from pathlib import Path
from typing import AsyncIterator

import aiofiles

from config import settings

logger = logging.getLogger(__name__)


def _pod_key_to_dir(pod_key: str) -> str:
    """Convert "namespace/pod" to a filesystem-safe directory name."""
    return pod_key.replace("/", "__")


def capture_dir(capture_id: str, pod_key: str) -> Path:
    return Path(settings.pcap_storage_path) / capture_id / _pod_key_to_dir(pod_key)


# ---------------------------------------------------------------------------
# Receiving pcap chunks from agent
# ---------------------------------------------------------------------------

async def receive_pcap_chunk(
    capture_id: str, pod_key: str, filename: str, chunk_b64: str
) -> None:
    """
    Write a base64-encoded chunk to the appropriate pcap file on the PVC.
    Called when the agent sends a PCAP_CHUNK message.
    """
    dest = capture_dir(capture_id, pod_key)
    dest.mkdir(parents=True, exist_ok=True)

    data = base64.b64decode(chunk_b64)
    file_path = dest / filename

    async with aiofiles.open(file_path, "ab") as f:
        await f.write(data)


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------

def list_pcap_files(capture_id: str, pod_key: str) -> list[Path]:
    """Return sorted list of .pcap files for a given pod capture."""
    d = capture_dir(capture_id, pod_key)
    if not d.exists():
        return []
    return sorted(d.glob("*.pcap"))


def build_zip(capture_id: str, pod_key: str) -> bytes:
    """
    Build an in-memory zip archive containing all .pcap rotation files for
    one pod capture. Returns the raw zip bytes.
    """
    files = list_pcap_files(capture_id, pod_key)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(f, arcname=f.name)
    return buf.getvalue()


def build_zip_all_pods(capture_id: str) -> bytes:
    """
    Build a zip archive containing pcap files from ALL pods in a capture,
    organised in per-pod subdirectories.
    """
    base = Path(settings.pcap_storage_path) / capture_id
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if base.exists():
            for pod_dir in base.iterdir():
                for f in sorted(pod_dir.glob("*.pcap")):
                    arcname = f"{pod_dir.name}/{f.name}"
                    zf.write(f, arcname=arcname)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# TTL-based cleanup (runs as a background asyncio task)
# ---------------------------------------------------------------------------

async def cleanup_loop() -> None:
    """
    Periodically scan the pcap storage root and delete capture directories
    that are older than PCAP_TTL_HOURS.
    Runs forever; intended to be launched with asyncio.create_task().
    """
    ttl_seconds = settings.pcap_ttl_hours * 3600
    while True:
        await asyncio.sleep(3600)  # check every hour
        try:
            _purge_old_captures(ttl_seconds)
        except Exception as e:
            logger.error("Cleanup error: %s", e)


def _purge_old_captures(ttl_seconds: int) -> None:
    base = Path(settings.pcap_storage_path)
    if not base.exists():
        return
    now = time.time()
    for capture_dir_path in base.iterdir():
        if not capture_dir_path.is_dir():
            continue
        age = now - capture_dir_path.stat().st_mtime
        if age > ttl_seconds:
            import shutil
            shutil.rmtree(capture_dir_path, ignore_errors=True)
            logger.info("Purged expired capture: %s", capture_dir_path.name)
