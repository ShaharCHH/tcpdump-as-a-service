"""
Audit log: every capture start/stop is written to a newline-delimited JSON file.
The log can be read by admins via the /api/audit endpoint.
"""
import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from models import AuditEntry, CaptureFilters


_log_path: Path | None = None
_lock = asyncio.Lock()


def init(log_dir: str) -> None:
    """Call once at startup to set the log file location."""
    global _log_path
    os.makedirs(log_dir, exist_ok=True)
    _log_path = Path(log_dir) / "audit.jsonl"


async def log(
    username: str,
    capture_id: str,
    pods: list[str],
    filters: CaptureFilters,
    duration_minutes: int,
    event: str,
    detail: Optional[str] = None,
) -> None:
    """Append one entry to the audit log. Thread-safe via asyncio lock."""
    entry = AuditEntry(
        timestamp=datetime.now(timezone.utc),
        username=username,
        capture_id=capture_id,
        pods=pods,
        filters=filters,
        duration_minutes=duration_minutes,
        event=event,
        detail=detail,
    )
    line = entry.model_dump_json() + "\n"
    async with _lock:
        with open(_log_path, "a") as f:
            f.write(line)


async def read_all(limit: int = 500) -> list[dict]:
    """Return the most recent `limit` audit entries, newest first."""
    if _log_path is None or not _log_path.exists():
        return []
    async with _lock:
        lines = _log_path.read_text().splitlines()
    entries = []
    for line in reversed(lines[-limit:]):
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries
