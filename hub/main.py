"""
tcpdump-as-a-service — Hub
FastAPI application entrypoint.

Endpoints
---------
Auth:
  GET  /auth/login          → Redirect to OpenShift OAuth OR return login form info
  GET  /auth/callback       → OpenShift OAuth callback
  POST /auth/token          → Accept k8s bearer token (AUTH_MODE=kubernetes)
  POST /auth/logout         → Clear session cookie

API (require valid session cookie):
  GET  /api/namespaces                        → List user's namespaces (admin access)
  GET  /api/namespaces/{ns}/pods              → List pods in a namespace
  POST /api/captures                          → Start a new capture
  GET  /api/captures                          → List user's captures
  GET  /api/captures/{id}                     → Get capture state
  DELETE /api/captures/{id}                   → Stop a capture
  GET  /api/captures/{id}/download/{pod_key}  → Download pcap zip for one pod
  GET  /api/captures/{id}/download            → Download pcap zip for all pods
  GET  /api/audit                             → Audit log (admin users only)
  GET  /api/agents                            → List connected agents (admin)

WebSocket:
  WS /ws/agent                     → Agent connects here on startup
  WS /ws/capture/{id}/{pod_key}    → Browser subscribes to live stream for one pod
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import audit
import captures
import storage
from agents import registry
from auth import (
    COOKIE_NAME,
    build_openshift_login_url,
    create_session_cookie,
    exchange_openshift_code,
    get_current_user,
    validate_k8s_token,
)
from config import settings
from k8s import list_pods, list_user_namespaces
from models import CaptureRequest

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="tcpdump-as-a-service", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten this in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log every inbound request and its response status at DEBUG level."""
    logger.debug("→ %s %s", request.method, request.url.path)
    response = await call_next(request)
    logger.debug("← %s %s %s", request.method, request.url.path, response.status_code)
    return response


# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def on_startup() -> None:
    audit.init(log_dir=os.path.join(settings.pcap_storage_path, "audit"))
    asyncio.create_task(storage.cleanup_loop())
    logger.info("Hub started. AUTH_MODE=%s", settings.auth_mode)


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------

@app.get("/auth/login")
async def auth_login(request: Request):
    """
    AUTH_MODE=openshift: redirect the browser to OpenShift's OAuth login page.
    AUTH_MODE=kubernetes: return the auth mode so the frontend shows the token form.
    """
    if settings.auth_mode == "openshift":
        url = await build_openshift_login_url()
        return RedirectResponse(url)
    return JSONResponse({"auth_mode": "kubernetes"})


@app.get("/auth/callback")
async def auth_callback(code: str, response: Response):
    """OpenShift OAuth callback. Exchanges the code for a token, sets session cookie."""
    token, username = await exchange_openshift_code(code)
    cookie = create_session_cookie(token, username)
    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie(
        COOKIE_NAME,
        cookie,
        httponly=True,
        samesite="lax",
        max_age=settings.session_max_age_hours * 3600,
    )
    return response


@app.post("/auth/token")
async def auth_token(request: Request, response: Response):
    """
    AUTH_MODE=kubernetes: accepts {"token": "<bearer>"} and validates it via
    the Kubernetes TokenReview API. On success, sets a session cookie.
    """
    body = await request.json()
    token = body.get("token", "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="token is required")

    username = await validate_k8s_token(token)
    if not username:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    cookie = create_session_cookie(token, username)
    response = JSONResponse({"username": username})
    response.set_cookie(
        COOKIE_NAME,
        cookie,
        httponly=True,
        samesite="lax",
        max_age=settings.session_max_age_hours * 3600,
    )
    return response


@app.post("/auth/logout")
async def auth_logout(response: Response):
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE_NAME)
    return response


@app.get("/auth/me")
async def auth_me(user=Depends(get_current_user)):
    username, _ = user
    return {"username": username, "is_admin": username in settings.admin_user_list}


# ---------------------------------------------------------------------------
# Kubernetes API proxies
# ---------------------------------------------------------------------------

@app.get("/api/namespaces")
async def api_namespaces(user=Depends(get_current_user)):
    username, token = user
    logger.debug("api_namespaces: listing namespaces for user=%s", username)
    ns_list = await list_user_namespaces(token)
    logger.debug("api_namespaces: user=%s got %d namespace(s): %s",
                 username, len(ns_list), [n["name"] for n in ns_list])
    return {"namespaces": ns_list}


@app.get("/api/namespaces/{namespace}/pods")
async def api_pods(namespace: str, user=Depends(get_current_user)):
    username, token = user
    logger.debug("api_pods: listing pods in namespace=%s for user=%s", namespace, username)
    pods = await list_pods(token, namespace)
    logger.debug("api_pods: namespace=%s user=%s got %d pod(s): %s",
                 namespace, username, len(pods), [p["name"] for p in pods])
    return {"pods": pods}


# ---------------------------------------------------------------------------
# Capture endpoints
# ---------------------------------------------------------------------------

@app.post("/api/captures", status_code=201)
async def api_start_capture(request: CaptureRequest, user=Depends(get_current_user)):
    username, _ = user
    try:
        state = await captures.start_capture(username, request)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return state.model_dump(mode="json")


@app.get("/api/captures")
async def api_list_captures(user=Depends(get_current_user)):
    username, _ = user
    return {"captures": [c.model_dump(mode="json") for c in captures.list_user_captures(username)]}


@app.get("/api/captures/{capture_id}")
async def api_get_capture(capture_id: str, user=Depends(get_current_user)):
    username, _ = user
    state = captures.get_capture(capture_id)
    if not state:
        raise HTTPException(status_code=404, detail="Capture not found")
    if state.username != username and username not in settings.admin_user_list:
        raise HTTPException(status_code=403, detail="Forbidden")
    return state.model_dump(mode="json")


@app.delete("/api/captures/{capture_id}", status_code=204)
async def api_stop_capture(capture_id: str, user=Depends(get_current_user)):
    username, _ = user
    try:
        await captures.stop_capture(capture_id, username)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


@app.get("/api/captures/{capture_id}/download/{pod_key:path}")
async def api_download_pod(capture_id: str, pod_key: str, user=Depends(get_current_user)):
    """Download a zip of all pcap rotation files for one pod."""
    username, _ = user
    state = captures.get_capture(capture_id)
    if not state:
        raise HTTPException(status_code=404, detail="Capture not found")
    if state.username != username and username not in settings.admin_user_list:
        raise HTTPException(status_code=403, detail="Forbidden")

    data = storage.build_zip(capture_id, pod_key)
    safe_pod = pod_key.replace("/", "__")
    return StreamingResponse(
        iter([data]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{capture_id}_{safe_pod}.zip"'},
    )


@app.get("/api/captures/{capture_id}/download")
async def api_download_all(capture_id: str, user=Depends(get_current_user)):
    """Download a zip of pcap files from all pods in a capture."""
    username, _ = user
    state = captures.get_capture(capture_id)
    if not state:
        raise HTTPException(status_code=404, detail="Capture not found")
    if state.username != username and username not in settings.admin_user_list:
        raise HTTPException(status_code=403, detail="Forbidden")

    data = storage.build_zip_all_pods(capture_id)
    return StreamingResponse(
        iter([data]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{capture_id}_all.zip"'},
    )


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------

@app.get("/api/audit")
async def api_audit(limit: int = 200, user=Depends(get_current_user)):
    username, _ = user
    if username not in settings.admin_user_list:
        raise HTTPException(status_code=403, detail="Admin only")
    entries = await audit.read_all(limit=limit)
    return {"entries": entries}


@app.get("/api/agents")
async def api_agents(user=Depends(get_current_user)):
    username, _ = user
    if username not in settings.admin_user_list:
        raise HTTPException(status_code=403, detail="Admin only")
    return {"nodes": registry.connected_nodes()}


# ---------------------------------------------------------------------------
# WebSocket — Agent connection
# ---------------------------------------------------------------------------

@app.websocket("/ws/agent")
async def ws_agent(websocket: WebSocket):
    """
    Persistent WebSocket connection from an agent pod.
    The agent sends a REGISTER message first, then we route all subsequent
    messages to captures.py via the registry callback system.
    """
    await websocket.accept()
    node_name: str | None = None
    try:
        # First message must be REGISTER
        raw = await websocket.receive_text()
        msg = json.loads(raw)
        if msg.get("type") != "REGISTER":
            await websocket.close(code=4000)
            return

        node_name = msg["node_name"]
        await registry.register(node_name, websocket)

        # Route all subsequent messages to the callback registered by captures.py
        async for raw in websocket.iter_text():
            try:
                data = json.loads(raw)
                await registry.route_message(data)
            except json.JSONDecodeError:
                logger.warning("Agent sent non-JSON message")

    except WebSocketDisconnect:
        pass
    finally:
        if node_name:
            await registry.unregister(node_name)


# ---------------------------------------------------------------------------
# WebSocket — Browser live stream
# ---------------------------------------------------------------------------

@app.websocket("/ws/capture/{capture_id}/{pod_key:path}")
async def ws_capture_stream(websocket: WebSocket, capture_id: str, pod_key: str):
    """
    Browser subscribes to the live tcpdump stream for a specific pod.
    Messages forwarded here are PACKET_LINE, CAPTURE_DONE, or ERROR dicts.
    """
    await websocket.accept()

    state = captures.get_capture(capture_id)
    if not state:
        await websocket.send_text(json.dumps({"type": "ERROR", "message": "Capture not found"}))
        await websocket.close()
        return

    await captures.subscribe(capture_id, pod_key, websocket)
    try:
        # Keep the connection open; the client just listens
        async for _ in websocket.iter_text():
            pass
    except WebSocketDisconnect:
        pass
    finally:
        await captures.unsubscribe(capture_id, pod_key, websocket)


# ---------------------------------------------------------------------------
# Serve React frontend (must be last — catches all unmatched routes)
# ---------------------------------------------------------------------------

_static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_static_dir):
    _index_html = os.path.join(_static_dir, "index.html")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        candidate = os.path.join(_static_dir, full_path)
        if os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(_index_html)

    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="static")
