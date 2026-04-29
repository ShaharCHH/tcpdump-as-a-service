"""
Minimal HTTP server running alongside the agent's WebSocket client.

The hub calls GET /pcap/<capture_id>/<pod_key_safe>/<filename> to download
completed pcap files directly from the agent node's local filesystem.

This avoids sending large pcap files through the WebSocket — instead, after
a CAPTURE_DONE message the hub can pull files via HTTP from the agent's pod IP.

Note: This server is only reachable within the cluster (pod IP), so no
external authentication is needed. Hub-to-agent traffic stays cluster-internal.
"""
from __future__ import annotations

import logging
import os

from aiohttp import web

logger = logging.getLogger(__name__)

# Base directory where capture files are written (must match agent/main.py)
CAPTURE_BASE = os.environ.get("CAPTURE_BASE_DIR", "/captures")


async def serve_pcap(request: web.Request) -> web.Response:
    """Serve a single pcap file by path under CAPTURE_BASE."""
    # Path components: capture_id / pod_key_safe / filename
    rel_path = request.match_info["path"]

    # Prevent path traversal attacks
    full_path = os.path.realpath(os.path.join(CAPTURE_BASE, rel_path))
    if not full_path.startswith(os.path.realpath(CAPTURE_BASE)):
        raise web.HTTPForbidden()

    if not os.path.isfile(full_path):
        raise web.HTTPNotFound()

    return web.FileResponse(full_path, headers={
        "Content-Type": "application/vnd.tcpdump.pcap",
    })


async def list_pcaps(request: web.Request) -> web.Response:
    """List pcap files for a given capture_id + pod_key_safe directory."""
    rel_dir = request.match_info["dir"]
    full_dir = os.path.realpath(os.path.join(CAPTURE_BASE, rel_dir))
    if not full_dir.startswith(os.path.realpath(CAPTURE_BASE)):
        raise web.HTTPForbidden()

    if not os.path.isdir(full_dir):
        return web.json_response({"files": []})

    files = sorted(f for f in os.listdir(full_dir) if f.endswith(".pcap"))
    return web.json_response({"files": files})


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/pcap/{path:.+}", serve_pcap)
    app.router.add_get("/list/{dir:.+}", list_pcaps)
    app.router.add_get("/health", lambda _: web.Response(text="ok"))
    return app


async def start_server(port: int) -> None:
    """Start the aiohttp HTTP server on the given port."""
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("Agent HTTP server listening on port %d", port)
