# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

**tcpdump-as-a-service** lets Kubernetes/OpenShift users run tcpdump on their pods without needing privileged cluster access. A privileged DaemonSet agent on each node performs captures via `nsenter`; a central hub orchestrates jobs and streams results to a React UI.

---

## Development commands

### Hub (FastAPI)

```bash
cd hub
pip install -r requirements.txt
cp ../.env.example ../.env   # then edit it
uvicorn main:app --reload --port 8080
```

The hub reads config from `.env` in the working directory (or environment variables). Set `K8S_IN_CLUSTER=false` and `AUTH_MODE=kubernetes` for local dev. The hub will use your local kubeconfig.

### Frontend (React + Vite)

```bash
cd frontend
npm install
npm run dev          # dev server on :5173, proxies /api + /ws to localhost:8080
npm run build        # outputs to frontend/dist/
```

The Vite dev server proxies all `/api`, `/auth`, and `/ws` paths to the hub at `localhost:8080`, so you can run hub and frontend separately during development.

### Agent (runs on cluster nodes — not for local dev)

```bash
cd agent
pip install -r requirements.txt
HUB_WS_URL=ws://localhost:8080 NODE_NAME=local python main.py
```

The agent requires `nsenter`, `tcpdump`, and `hostPID=true` to function — it only captures real traffic when deployed as the privileged DaemonSet on a cluster node.

### Build + deploy to k3d

```bash
# First time: create the cluster
k3d cluster create tcpdump-dev --port 8080:80@loadbalancer

# Build images, import into k3d, helm install
./scripts/deploy.sh --build --k3d --deploy

# Subsequent deploys (code change)
./scripts/deploy.sh --build --k3d --deploy --tag dev

# OpenShift
REGISTRY=registry.example.com/myorg ./scripts/deploy.sh --build --push --deploy --openshift
```

The deploy script builds the React frontend, copies `frontend/dist/` to `hub/static/`, then builds the hub Docker image (which bundles the static files). The hub serves the React app from `/hub/static/` at runtime.

### Helm only (no rebuild)

```bash
helm upgrade --install tcpdump-as-a-service deploy/helm/tcpdump-as-a-service \
  --namespace tcpdump-service --create-namespace
```

---

## Architecture

```
Browser (React)
    │  REST + WebSocket (/ws/capture/...)
    ▼
hub/main.py  (FastAPI)
    │  k8s API (user-impersonated for NS/pods; hub SA for nodeName/container IDs)
    │  WebSocket (/ws/agent) ← persistent connection per cluster node
    ▼
agent/main.py  (DaemonSet, one pod per node)
    │  nsenter -t <pid> -n -- tcpdump ...
    ▼
target pod's network namespace
```

### Hub modules (`hub/`)

| File | Responsibility |
|---|---|
| `config.py` | Pydantic settings — all tunable via env vars |
| `models.py` | Shared Pydantic models (`CaptureRequest`, `CaptureState`, `CaptureJob`, etc.) |
| `auth.py` | Two auth modes: OpenShift OAuth2 redirect, or k8s TokenReview + signed cookie |
| `k8s.py` | User-impersonated calls (namespace/pod listing) vs hub-SA calls (nodeName, container IDs) |
| `agents.py` | In-memory registry `{node_name → WebSocket}`, callback routing for capture events |
| `captures.py` | Capture lifecycle state machine; dispatches jobs, broadcasts to client WebSockets |
| `storage.py` | Writes pcap chunks to PVC, builds zip downloads, runs TTL cleanup loop |
| `audit.py` | Appends JSONL audit entries; `admin_users` list gates read access |
| `main.py` | FastAPI app — all routes, WebSocket endpoints, static file mount |

### Agent modules (`agent/`)

| File | Responsibility |
|---|---|
| `capture.py` | `find_container_pid` (scans `/proc/*/cgroup`), BPF filter builder, dual tcpdump processes, base64 chunk streaming |
| `main.py` | WebSocket client with auto-reconnect; dispatches `START_CAPTURE` to `capture.py` as asyncio tasks |
| `server.py` | aiohttp HTTP server on port 8081 for direct pcap file download by the hub |

### Key data flows

**Starting a capture:**
1. Browser → `POST /api/captures` → `captures.start_capture()`
2. For each pod: hub SA calls `k8s.get_pod_capture_info()` to get `node_name` + `container_id`
3. Hub looks up the agent for that node in `agents.registry`, sends `START_CAPTURE` over WebSocket
4. Agent calls `capture.find_container_pid()` → `nsenter ... tcpdump`, streams `PACKET_LINE` events back
5. Hub forwards `PACKET_LINE` to all client WebSockets subscribed to `/ws/capture/{id}/{pod_key}`

**pcap file delivery:**
- Agent writes rotating files locally (tcpdump `-G`/`-C`/`-W` flags)
- On `CAPTURE_DONE`, agent streams file bytes as base64 `PCAP_CHUNK` messages over the existing WebSocket
- Hub writes chunks to PVC under `/pcaps/{capture_id}/{pod_key}/`
- Browser downloads via `GET /api/captures/{id}/download/{pod_key}` (returns a zip)

### Auth duality

`AUTH_MODE=kubernetes` — user pastes bearer token; hub validates via `TokenReview` API; issues signed cookie (`itsdangerous`). Cookie payload: `{token, username}`. Token is re-used for every user-impersonated k8s call.

`AUTH_MODE=openshift` — hub is registered as an `OAuthClient`; `/auth/login` redirects browser; `/auth/callback` exchanges code for token via OpenShift's OAuth server.

### Finding a container's PID (privileged, agent-side)

`capture.find_container_pid()` scans `/proc/*/cgroup` for the short container ID (first 12 chars of the container runtime ID from the k8s API, e.g. `containerd://abc123...`). Requires `hostPID: true` in the DaemonSet so all host PIDs are visible. Then `nsenter -t <pid> -n` enters that container's network namespace without modifying the target pod.

### Helm chart conditionals

- `openshift.enabled=true` → renders `scc.yaml`, `route.yaml`, `oauth-client.yaml`; skips `ingress.yaml`
- `auth.mode=openshift` → adds OpenShift env vars to ConfigMap and `OAUTH_CLIENT_SECRET` to Secret
- `storage.storageClass=local-path` is the k3d default; leave empty for OpenShift/other clusters

---

## Important constraints

- **Captures are in-memory only** on the hub — restarting the hub loses all in-flight capture state. pcap files on the PVC survive restarts.
- **One agent per node** — if a pod's node has no connected agent, the capture for that pod fails immediately with an error (not silently queued).
- **pcap files live on the agent node temporarily** (emptyDir volume), then stream to the hub's PVC. If the agent pod is deleted before streaming completes, those files are lost.
- **`AUTH_MODE` cannot be changed at runtime** — it is baked into the ConfigMap and requires a hub restart.
- The hub's `static/` directory (React build output) is populated by `scripts/deploy.sh` before the Docker image is built. Running `uvicorn` without it serves API-only (fine for local dev with `npm run dev`).

---

## Line endings

Line endings are normalized to **LF** via `.gitattributes`. This project is often edited from Windows/WSL — if `git status` shows many modified files with a perfectly balanced insertions/deletions count, it is CRLF→LF churn, not real changes. Confirm with `git diff --ignore-all-space` (empty output = pure line-ending churn) before committing.
