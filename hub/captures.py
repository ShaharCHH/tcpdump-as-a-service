"""
Capture lifecycle management.

A "capture" in this system is a user request to run tcpdump on one or more pods.
Internally it decomposes into one CaptureJob per pod, each dispatched to the agent
on the node where that pod runs.

State machine per capture:
  PENDING → RUNNING → DONE | ERROR

State machine per pod within a capture:
  PENDING → RUNNING → DONE | ERROR

The hub keeps all active captures in memory. Completed captures are kept in memory
until the hub restarts (the pcap files persist on the PVC independently).
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import WebSocket

import audit
import storage
from agents import registry
from config import settings
from k8s import get_pod_capture_info, get_pod_capture_info_for_container
from models import (
    CaptureFilters,
    CaptureJob,
    CaptureRequest,
    CaptureState,
    CaptureStatus,
    PodTarget,
)

logger = logging.getLogger(__name__)

# In-memory store of all captures (active and recently completed)
_captures: dict[str, CaptureState] = {}
# Per-capture, per-pod: set of client WebSockets subscribed to the live stream
_subscribers: dict[tuple[str, str], set[WebSocket]] = {}
_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Starting a capture
# ---------------------------------------------------------------------------

async def start_capture(username: str, request: CaptureRequest) -> CaptureState:
    """
    Validate limits, resolve pod→node mappings, dispatch jobs to agents,
    and return the initial CaptureState.

    Raises ValueError if any limit is exceeded or a pod can't be resolved.
    """
    # --- Enforce limits ---
    user_active = sum(
        1 for c in _captures.values()
        if c.username == username and c.status == CaptureStatus.RUNNING
    )
    if user_active >= settings.max_concurrent_per_user:
        raise ValueError(
            f"You already have {user_active} active capture(s). "
            f"Limit is {settings.max_concurrent_per_user}."
        )

    total_active = sum(1 for c in _captures.values() if c.status == CaptureStatus.RUNNING)
    if total_active >= settings.max_concurrent_captures:
        raise ValueError("Cluster-wide capture limit reached. Try again later.")

    duration_minutes = min(request.duration_minutes, settings.max_duration_minutes)

    capture_id = str(uuid.uuid4())

    state = CaptureState(
        capture_id=capture_id,
        username=username,
        pods=request.pods,
        status=CaptureStatus.PENDING,
        started_at=datetime.now(timezone.utc),
        filters=request.filters,
        duration_minutes=duration_minutes,
        pod_statuses={_pod_key(p): CaptureStatus.PENDING for p in request.pods},
    )

    async with _lock:
        _captures[capture_id] = state

    # Audit log
    await audit.log(
        username=username,
        capture_id=capture_id,
        pods=[_pod_key(p) for p in request.pods],
        filters=request.filters,
        duration_minutes=duration_minutes,
        event="started",
    )

    # Dispatch one job per pod (async, errors per-pod don't fail the whole capture)
    asyncio.create_task(_dispatch_all(capture_id, request.pods, request.filters, duration_minutes))

    state.status = CaptureStatus.RUNNING
    return state


async def _dispatch_all(
    capture_id: str,
    pods: list[PodTarget],
    filters: CaptureFilters,
    duration_minutes: int,
) -> None:
    """Resolve pod details and dispatch a job to the right agent for each pod."""
    tasks = [
        _dispatch_one(capture_id, pod, filters, duration_minutes)
        for pod in pods
    ]
    await asyncio.gather(*tasks, return_exceptions=True)

    # If all pods are done/errored, mark the capture as done
    async with _lock:
        state = _captures.get(capture_id)
        if state:
            statuses = set(state.pod_statuses.values())
            if statuses <= {CaptureStatus.DONE, CaptureStatus.ERROR}:
                state.status = (
                    CaptureStatus.DONE
                    if CaptureStatus.DONE in statuses
                    else CaptureStatus.ERROR
                )
                await audit.log(
                    username=state.username,
                    capture_id=capture_id,
                    pods=[_pod_key(p) for p in state.pods],
                    filters=state.filters,
                    duration_minutes=state.duration_minutes,
                    event="completed",
                )


async def _dispatch_one(
    capture_id: str,
    pod: PodTarget,
    filters: CaptureFilters,
    duration_minutes: int,
) -> None:
    pod_key = _pod_key(pod)
    try:
        # Get node and container details using the hub's service account
        if pod.container_name:
            info = await get_pod_capture_info_for_container(
                pod.pod_name, pod.namespace, pod.container_name
            )
        else:
            info = await get_pod_capture_info(pod.pod_name, pod.namespace)

        node_name = info["node_name"]
        container_id = info["container_id"]

        if not registry.is_node_available(node_name):
            raise RuntimeError(f"No agent available on node {node_name}")

        output_dir = f"/captures/{capture_id}/{pod_key.replace('/', '__')}"

        job = {
            "capture_id": capture_id,
            "pod_key": pod_key,
            "container_id": container_id,
            "filters": filters.model_dump(),
            "duration_seconds": duration_minutes * 60,
            "output_dir": output_dir,
        }

        # Register callback before dispatching so we don't miss the first messages
        registry.register_callback(capture_id, pod_key, _make_handler(capture_id, pod_key))
        await registry.dispatch_capture(node_name, job)

        async with _lock:
            state = _captures[capture_id]
            state.pod_statuses[pod_key] = CaptureStatus.RUNNING

    except Exception as e:
        logger.error("Failed to dispatch capture for %s: %s", pod_key, e)
        async with _lock:
            state = _captures.get(capture_id)
            if state:
                state.pod_statuses[pod_key] = CaptureStatus.ERROR
                state.error = str(e)
        await _broadcast(capture_id, pod_key, {"type": "ERROR", "message": str(e)})


def _make_handler(capture_id: str, pod_key: str):
    """Return an async callback that processes agent messages for one pod capture."""
    async def handler(msg: dict) -> None:
        msg_type = msg.get("type")

        if msg_type == "PACKET_LINE":
            # Forward the live packet text to all subscribed browser WebSockets
            await _broadcast(capture_id, pod_key, msg)

        elif msg_type == "PCAP_CHUNK":
            # Write a pcap chunk to the PVC
            await storage.receive_pcap_chunk(
                capture_id,
                pod_key,
                msg["filename"],
                msg["data"],
            )

        elif msg_type == "CAPTURE_DONE":
            async with _lock:
                state = _captures.get(capture_id)
                if state:
                    state.pod_statuses[pod_key] = CaptureStatus.DONE
            registry.unregister_callback(capture_id, pod_key)
            await _broadcast(capture_id, pod_key, {"type": "CAPTURE_DONE"})

        elif msg_type == "ERROR":
            async with _lock:
                state = _captures.get(capture_id)
                if state:
                    state.pod_statuses[pod_key] = CaptureStatus.ERROR
            registry.unregister_callback(capture_id, pod_key)
            await _broadcast(capture_id, pod_key, msg)

    return handler


# ---------------------------------------------------------------------------
# Stopping a capture
# ---------------------------------------------------------------------------

async def stop_capture(capture_id: str, username: str) -> None:
    """Request cancellation of all in-progress pod captures."""
    state = _captures.get(capture_id)
    if not state:
        raise ValueError(f"Capture {capture_id} not found")
    if state.username != username:
        raise PermissionError("Not your capture")

    # For each running pod, send CANCEL to its agent
    # We need to know the node for each pod — re-look it up
    for pod in state.pods:
        if state.pod_statuses.get(_pod_key(pod)) == CaptureStatus.RUNNING:
            try:
                info = await get_pod_capture_info(pod.pod_name, pod.namespace)
                await registry.cancel_capture(
                    info["node_name"], capture_id, _pod_key(pod)
                )
            except Exception as e:
                logger.warning("Cancel error for %s: %s", _pod_key(pod), e)

    state.status = CaptureStatus.DONE
    await audit.log(
        username=username,
        capture_id=capture_id,
        pods=[_pod_key(p) for p in state.pods],
        filters=state.filters,
        duration_minutes=state.duration_minutes,
        event="stopped",
    )


# ---------------------------------------------------------------------------
# Querying captures
# ---------------------------------------------------------------------------

def get_capture(capture_id: str) -> Optional[CaptureState]:
    return _captures.get(capture_id)


def list_user_captures(username: str) -> list[CaptureState]:
    return [c for c in _captures.values() if c.username == username]


# ---------------------------------------------------------------------------
# Live stream subscription (browser WebSocket)
# ---------------------------------------------------------------------------

async def subscribe(capture_id: str, pod_key: str, ws: WebSocket) -> None:
    """Add a client WebSocket to the subscriber set for this pod capture."""
    key = (capture_id, pod_key)
    async with _lock:
        if key not in _subscribers:
            _subscribers[key] = set()
        _subscribers[key].add(ws)


async def unsubscribe(capture_id: str, pod_key: str, ws: WebSocket) -> None:
    key = (capture_id, pod_key)
    async with _lock:
        if key in _subscribers:
            _subscribers[key].discard(ws)


async def _broadcast(capture_id: str, pod_key: str, message: dict) -> None:
    """Send a message to all browser WebSockets subscribed to this pod capture."""
    key = (capture_id, pod_key)
    subs = set(_subscribers.get(key, set()))
    dead = set()
    for ws in subs:
        try:
            await ws.send_text(json.dumps(message))
        except Exception:
            dead.add(ws)
    if dead:
        async with _lock:
            _subscribers.get(key, set()).difference_update(dead)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pod_key(pod: PodTarget) -> str:
    return f"{pod.namespace}/{pod.pod_name}"
