"""
tcpdump-as-a-service — Agent

Runs on every cluster node as part of a DaemonSet in the privileged namespace.
On startup it:
  1. Starts a small HTTP server (server.py) for pcap file download.
  2. Connects to the hub's WebSocket endpoint and registers itself by node name.
  3. Listens for START_CAPTURE jobs and runs them concurrently via capture.py.
  4. Reconnects automatically if the hub connection drops.

Environment variables (set by Helm chart / Kubernetes downward API):
  HUB_WS_URL      WebSocket URL of the hub, e.g. ws://tcpdump-hub:8080
  NODE_NAME       The Kubernetes node this pod is running on (downward API)
  AGENT_HTTP_PORT Port for the local HTTP server (default 8081)
  CAPTURE_BASE_DIR Base directory for pcap files (default /captures)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

import websockets

import capture as cap
from server import start_server

_log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, _log_level, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("agent")

HUB_WS_URL = os.environ["HUB_WS_URL"]
NODE_NAME = os.environ["NODE_NAME"]
AGENT_HTTP_PORT = int(os.environ.get("AGENT_HTTP_PORT", "8081"))

# Reconnect delay if the hub is unreachable (seconds)
RECONNECT_DELAY = 5

# Active capture tasks: (capture_id, pod_key) → asyncio.Task
_active: dict[tuple[str, str], asyncio.Task] = {}


async def handle_job(ws, job: dict) -> None:
    """
    Process a single START_CAPTURE job from the hub.
    Runs the capture and streams events back over the WebSocket.
    """
    capture_id = job["capture_id"]
    pod_key = job["pod_key"]
    task_key = (capture_id, pod_key)

    async def _run() -> None:
        try:
            async for event in cap.run_capture(
                container_id=job["container_id"],
                filters=job["filters"],
                duration_seconds=job["duration_seconds"],
                capture_id=capture_id,
                output_dir=job["output_dir"],
            ):
                # Attach routing info so the hub knows where to forward the event
                payload = {"capture_id": capture_id, "pod_key": pod_key, **event}
                event_type = event.get("type")
                if event_type == "PACKET_LINE":
                    logger.debug("PACKET_LINE capture=%s pod=%s: %s",
                                 capture_id, pod_key, event.get("line", ""))
                elif event_type == "PCAP_CHUNK":
                    logger.debug("PCAP_CHUNK capture=%s pod=%s file=%s size=%d bytes",
                                 capture_id, pod_key, event.get("filename"),
                                 len(event.get("data", "")))
                else:
                    logger.debug("EVENT capture=%s pod=%s type=%s",
                                 capture_id, pod_key, event_type)
                await ws.send(json.dumps(payload))
        except Exception as e:
            logger.error("Capture error for %s/%s: %s", capture_id, pod_key, e)
            try:
                await ws.send(json.dumps({
                    "capture_id": capture_id,
                    "pod_key": pod_key,
                    "type": "ERROR",
                    "message": str(e),
                }))
            except Exception:
                pass
        finally:
            _active.pop(task_key, None)

    task = asyncio.create_task(_run())
    _active[task_key] = task


async def handle_cancel(job: dict) -> None:
    """Cancel an in-progress capture task."""
    task_key = (job["capture_id"], job["pod_key"])
    task = _active.pop(task_key, None)
    if task:
        task.cancel()


async def run_agent() -> None:
    """Main agent loop: connect to hub, register, process messages."""
    while True:
        try:
            logger.info("Connecting to hub at %s ...", HUB_WS_URL)
            async with websockets.connect(
                f"{HUB_WS_URL}/ws/agent",
                ping_interval=20,
                ping_timeout=30,
            ) as ws:
                # Register this agent with the hub
                await ws.send(json.dumps({
                    "type": "REGISTER",
                    "node_name": NODE_NAME,
                }))
                logger.info("Registered as node=%s", NODE_NAME)

                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    msg_type = msg.get("type")

                    if msg_type == "START_CAPTURE":
                        logger.info(
                            "START_CAPTURE: capture=%s pod=%s container=%s duration=%ss",
                            msg.get("capture_id"), msg.get("pod_key"),
                            msg.get("container_id", "")[:20], msg.get("duration_seconds"),
                        )
                        logger.debug("START_CAPTURE full payload: %s", msg)
                        asyncio.create_task(handle_job(ws, msg))

                    elif msg_type == "CANCEL_CAPTURE":
                        logger.info(
                            "CANCEL_CAPTURE: capture=%s pod=%s",
                            msg.get("capture_id"), msg.get("pod_key"),
                        )
                        await handle_cancel(msg)

                    else:
                        logger.warning("Unknown message type: %s | full msg: %s", msg_type, msg)

        except (websockets.ConnectionClosed, OSError) as e:
            logger.warning("Hub connection lost (%s), reconnecting in %ds...", e, RECONNECT_DELAY)
            await asyncio.sleep(RECONNECT_DELAY)
        except Exception as e:
            logger.error("Unexpected error: %s", e, exc_info=True)
            await asyncio.sleep(RECONNECT_DELAY)


async def main() -> None:
    # Start HTTP server for pcap download in parallel with the WebSocket client
    await start_server(AGENT_HTTP_PORT)
    await run_agent()


if __name__ == "__main__":
    asyncio.run(main())
