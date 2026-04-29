from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, field_validator


class CaptureStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


class CaptureFilters(BaseModel):
    """BPF-compatible filter fields exposed to the user."""
    host: Optional[str] = None        # matches src OR dst
    src_host: Optional[str] = None
    dst_host: Optional[str] = None
    src_port: Optional[int] = None
    dst_port: Optional[int] = None
    interface: str = "any"
    # Optional hard packet count limit (-c flag)
    packet_count: Optional[int] = None

    def to_bpf(self) -> str:
        """Build a BPF filter expression from the individual fields."""
        parts: list[str] = []
        if self.host:
            parts.append(f"host {self.host}")
        if self.src_host:
            parts.append(f"src host {self.src_host}")
        if self.dst_host:
            parts.append(f"dst host {self.dst_host}")
        if self.src_port:
            parts.append(f"src port {self.src_port}")
        if self.dst_port:
            parts.append(f"dst port {self.dst_port}")
        return " and ".join(parts)


class PodTarget(BaseModel):
    namespace: str
    pod_name: str
    # Container to capture on. If omitted the first container is used.
    container_name: Optional[str] = None


class CaptureRequest(BaseModel):
    pods: list[PodTarget]
    duration_minutes: int
    filters: CaptureFilters

    @field_validator("duration_minutes")
    @classmethod
    def positive_duration(cls, v: int) -> int:
        if v < 1:
            raise ValueError("duration_minutes must be >= 1")
        return v


# Sent from hub to agent over WebSocket to start a capture on a specific pod
class CaptureJob(BaseModel):
    capture_id: str
    pod_key: str          # "{namespace}/{pod_name}" — used as a stable identifier
    container_id: str     # Full container ID (e.g. "containerd://abc123...")
    filters: CaptureFilters
    duration_seconds: int
    output_dir: str       # Directory on the agent node where pcap files are written


# Full state of a capture session (one per user request, spans N pods)
class CaptureState(BaseModel):
    capture_id: str
    username: str
    pods: list[PodTarget]
    status: CaptureStatus
    started_at: datetime
    filters: CaptureFilters
    duration_minutes: int
    # Per-pod status: pod_key -> CaptureStatus
    pod_statuses: dict[str, CaptureStatus] = {}
    error: Optional[str] = None


# A single entry in the audit log
class AuditEntry(BaseModel):
    timestamp: datetime
    username: str
    capture_id: str
    pods: list[str]       # ["namespace/pod", ...]
    filters: CaptureFilters
    duration_minutes: int
    event: str            # "started" | "stopped" | "completed" | "error"
    detail: Optional[str] = None
