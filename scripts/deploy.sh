#!/usr/bin/env bash
# =============================================================================
# tcpdump-as-a-service — build & deploy script
#
# Usage:
#   ./scripts/deploy.sh [OPTIONS]
#
# Options:
#   --build         Build Docker images for hub and agent
#   --push          Push images to a container registry (set REGISTRY env var)
#   --deploy        Helm upgrade --install to the cluster
#   --k3d           Load images into k3d instead of pushing to a registry
#   --openshift     Enable OpenShift-specific resources (SCC, Route, OAuthClient)
#   --namespace NS  Kubernetes namespace to deploy into (default: tcpdump-service)
#   --cluster NAME  k3d cluster name (default: tcpdump-dev)
#   --tag TAG       Image tag to use (default: git short SHA)
#   -h, --help      Show this help
#
# Examples:
#   # Local k3d development loop
#   ./scripts/deploy.sh --build --k3d --deploy
#
#   # Production push to a registry + deploy on OpenShift
#   REGISTRY=registry.example.com/myorg ./scripts/deploy.sh --build --push --deploy --openshift
# =============================================================================
set -euo pipefail

# ---------- Defaults ---------------------------------------------------------
DO_BUILD=false
DO_PUSH=false
DO_DEPLOY=false
DO_K3D=false
OPENSHIFT=false
NAMESPACE="tcpdump-service"
K3D_CLUSTER="tcpdump-dev"
REGISTRY="${REGISTRY:-}"

# Derive the default tag from the git short SHA (falls back to "dev" if not in a repo)
DEFAULT_TAG=$(git -C "$(dirname "$0")/.." rev-parse --short HEAD 2>/dev/null || echo "dev")
TAG="${TAG:-$DEFAULT_TAG}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CHART_DIR="${ROOT}/deploy/helm/tcpdump-as-a-service"
FRONTEND_DIR="${ROOT}/frontend"
HUB_STATIC_DIR="${ROOT}/hub/static"

# ---------- Argument parsing -------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --build)      DO_BUILD=true ;;
    --push)       DO_PUSH=true ;;
    --deploy)     DO_DEPLOY=true ;;
    --k3d)        DO_K3D=true ;;
    --openshift)  OPENSHIFT=true ;;
    --namespace)  NAMESPACE="$2"; shift ;;
    --cluster)    K3D_CLUSTER="$2"; shift ;;
    --tag)        TAG="$2"; shift ;;
    -h|--help)
      sed -n '2,/^# ===/p' "$0" | grep '^#' | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
  shift
done

HUB_IMAGE="tcpdump-hub:${TAG}"
AGENT_IMAGE="tcpdump-agent:${TAG}"

if [[ -n "$REGISTRY" ]]; then
  HUB_IMAGE="${REGISTRY}/tcpdump-hub:${TAG}"
  AGENT_IMAGE="${REGISTRY}/tcpdump-agent:${TAG}"
fi

echo "=================================================="
echo " tcpdump-as-a-service deploy"
echo " tag:       ${TAG}"
echo " namespace: ${NAMESPACE}"
echo " hub image: ${HUB_IMAGE}"
echo " agent img: ${AGENT_IMAGE}"
echo "=================================================="

# ---------- Build frontend ---------------------------------------------------
build_frontend() {
  echo ""
  echo ">>> Building React frontend..."
  cd "${FRONTEND_DIR}"
  npm ci
  npm run build
  # Copy the Vite output into hub/static so it gets bundled into the hub image
  rm -rf "${HUB_STATIC_DIR}"
  cp -r "${FRONTEND_DIR}/dist" "${HUB_STATIC_DIR}"
  echo "    Frontend built → hub/static/"
  cd "${ROOT}"
}

# ---------- Build Docker images ----------------------------------------------
build_images() {
  echo ""
  echo ">>> Building Docker images..."

  build_frontend

  docker build \
    -t "${HUB_IMAGE}" \
    -f "${ROOT}/docker/hub.Dockerfile" \
    "${ROOT}"
  echo "    Built: ${HUB_IMAGE}"

  docker build \
    -t "${AGENT_IMAGE}" \
    -f "${ROOT}/docker/agent.Dockerfile" \
    "${ROOT}"
  echo "    Built: ${AGENT_IMAGE}"
}

# ---------- Push to registry -------------------------------------------------
push_images() {
  if [[ -z "$REGISTRY" ]]; then
    echo "ERROR: --push requires REGISTRY env var (e.g. REGISTRY=registry.example.com/myorg)"
    exit 1
  fi
  echo ""
  echo ">>> Pushing images to ${REGISTRY}..."
  docker push "${HUB_IMAGE}"
  docker push "${AGENT_IMAGE}"
  echo "    Pushed."
}

# ---------- Load into k3d ----------------------------------------------------
k3d_load() {
  echo ""
  echo ">>> Loading images into k3d cluster '${K3D_CLUSTER}'..."
  k3d image import "${HUB_IMAGE}" "${AGENT_IMAGE}" -c "${K3D_CLUSTER}"
  echo "    Imported."
}

# ---------- Helm deploy ------------------------------------------------------
helm_deploy() {
  echo ""
  echo ">>> Running helm upgrade --install..."

  EXTRA_ARGS=()
  if [[ "$OPENSHIFT" == "true" ]]; then
    EXTRA_ARGS+=(--set "openshift.enabled=true")
  fi

  helm upgrade --install tcpdump-as-a-service "${CHART_DIR}" \
    --namespace "${NAMESPACE}" \
    --create-namespace \
    --set "image.hub=${HUB_IMAGE}" \
    --set "image.agent=${AGENT_IMAGE}" \
    "${EXTRA_ARGS[@]}"

  echo ""
  echo ">>> Waiting for hub to be ready..."
  kubectl rollout status deployment/tcpdump-as-a-service-hub \
    -n "${NAMESPACE}" --timeout=120s

  echo ""
  echo ">>> Pods in namespace ${NAMESPACE}:"
  kubectl get pods -n "${NAMESPACE}"
}

# ---------- Run selected steps -----------------------------------------------
if [[ "$DO_BUILD" == "true" ]]; then
  build_images
fi

if [[ "$DO_PUSH" == "true" ]]; then
  push_images
fi

if [[ "$DO_K3D" == "true" ]]; then
  k3d_load
fi

if [[ "$DO_DEPLOY" == "true" ]]; then
  helm_deploy
fi

if [[ "$DO_BUILD" == "false" && "$DO_PUSH" == "false" && "$DO_K3D" == "false" && "$DO_DEPLOY" == "false" ]]; then
  echo "Nothing to do. Run with --help to see available options."
  exit 1
fi

echo ""
echo "Done."
