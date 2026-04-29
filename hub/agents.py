"""
Agent registry and job dispatch.

Each cluster node runs one privileged agent pod (DaemonSet). When an agent starts,
it connects to the hub's /ws/agent WebSocket endpoint and sends a REGISTER message.
The hub keeps a map of node_name → WebSocket connection.

When a capture is requested for a pod on node X, the hub looks up the agent for
node X in this registry and sends it a START_CAPTURE message over the WebSocket.

The agent streams PACKET_LINE messages back for the live view, and a CAPTURE_DONE
message when the capture finishes (or an ERROR message on failure).
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Callable, Optional

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class AgentRegistry:
    def __init__(self) -> None:
        # node_name → WebSocket connection to the agent on that node
        self._agents: dict[str, WebSocket] = {}
        # Callbacks registered by captures.py: (capture_id, pod_key) → handler fn
        # Handler receives the parsed message dict from the agent.
        self._callbacks: dict[tuple[str, str], Callable] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Agent lifecycle
    # ------------------------------------------------------------------

    async def register(self, node_name: str, websocket: WebSocket) -> None:
        async with self._lock:
            old = self._agents.get(node_name)
            if old:
                # Stale connection from a previous pod restart — close it gracefully.
                try:
                    await old.close()
                except Exception:
                    pass
            self._agents[node_name] = websocket
        logger.info("Agent registered: node=%s", node_name)

    async def unregister(self, node_name: str) -> None:
        async with self._lock:
            self._agents.pop(node_name, None)
        logger.info("Agent disconnected: node=%s", node_name)

    def connected_nodes(self) -> list[str]:
        return list(self._agents.keys())

    def is_node_available(self, node_name: str) -> bool:
        return node_name in self._agents

    # ------------------------------------------------------------------
    # Job dispatch
    # ------------------------------------------------------------------

    async def dispatch_capture(self, node_name: str, job: dict) -> None:
        """Send a START_CAPTURE job to the agent on the given node."""
        ws = self._agents.get(node_name)
        if ws is None:
            raise RuntimeError(f"No agent connected for node {node_name}")
        await ws.send_text(json.dumps({"type": "START_CAPTURE", **job}))

    async def cancel_capture(self, node_name: str, capture_id: str, pod_key: str) -> None:
        """Ask the agent to abort an in-progress capture."""
        ws = self._agents.get(node_name)
        if ws is None:
            return
        try:
            await ws.send_text(json.dumps({
                "type": "CANCEL_CAPTURE",
                "capture_id": capture_id,
                "pod_key": pod_key,
            }))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Message routing
    # ------------------------------------------------------------------

    def register_callback(
        self, capture_id: str, pod_key: str, fn: Callable
    ) -> None:
        """
        Register a coroutine callback that is called whenever the agent sends a
        message for (capture_id, pod_key). Used by captures.py to fan-out events
        to subscribed browser WebSocket connections.
        """
        self._callbacks[(capture_id, pod_key)] = fn

    def unregister_callback(self, capture_id: str, pod_key: str) -> None:
        self._callbacks.pop((capture_id, pod_key), None)

    async def route_message(self, message: dict) -> None:
        """
        Called for every message received from any agent WebSocket.
        Routes to the appropriate registered callback.
        """
        capture_id = message.get("capture_id")
        pod_key = message.get("pod_key")
        if not capture_id or not pod_key:
            return
        fn = self._callbacks.get((capture_id, pod_key))
        if fn:
            try:
                await fn(message)
            except Exception as e:
                logger.warning("Callback error for %s/%s: %s", capture_id, pod_key, e)


# Module-level singleton — imported by main.py and captures.py
registry = AgentRegistry()
