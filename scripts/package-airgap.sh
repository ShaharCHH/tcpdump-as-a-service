#!/usr/bin/env bash
# =============================================================================
# tcpdump-as-a-service — air-gap bundle packager
#
# Produces a self-contained tar.gz with everything needed to deploy the app
# in a disconnected (air-gapped) environment:
#   - Docker images saved as .tar files
#   - Helm chart
#   - A ready-to-run deploy.sh for the target host
#
# Usage:
#   ./scripts/package-airgap.sh [--tag TAG] [--output DIR]
#
# Options:
#   --tag TAG       Image tag to build and bundle (default: git short SHA)
#   --output DIR    Directory to write the bundle (default: ./dist)
#   --skip-build    Skip Docker build — use already-built local images
#   -h, --help      Show this help
#
# Requirements (on the machine running this script):
#   docker, npm, helm
#
# On the air-gapped target host:
#   docker (or podman), helm, kubectl
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

DEFAULT_TAG=$(git -C "${ROOT}" rev-parse --short HEAD 2>/dev/null || echo "dev")
TAG="${TAG:-$DEFAULT_TAG}"
OUTPUT_DIR="${OUTPUT_DIR:-${ROOT}/dist}"
SKIP_BUILD=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tag)        TAG="$2"; shift ;;
    --output)     OUTPUT_DIR="$2"; shift ;;
    --skip-build) SKIP_BUILD=true ;;
    -h|--help)
      sed -n '2,/^# ===/p' "$0" | grep '^#' | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
  shift
done

HUB_IMAGE="tcpdump-hub:${TAG}"
AGENT_IMAGE="tcpdump-agent:${TAG}"
BUNDLE_NAME="tcpdump-as-a-service-airgap-${TAG}"
STAGING="${OUTPUT_DIR}/${BUNDLE_NAME}"

echo "=================================================="
echo " tcpdump-as-a-service air-gap packager"
echo " tag:     ${TAG}"
echo " bundle:  ${OUTPUT_DIR}/${BUNDLE_NAME}.tar.gz"
echo "=================================================="

# ---------- Build frontend + Docker images ------------------------------------
if [[ "$SKIP_BUILD" == "false" ]]; then
  echo ""
  echo ">>> Building React frontend..."
  cd "${ROOT}/frontend"
  npm ci
  npm run build
  rm -rf "${ROOT}/hub/static"
  cp -r "${ROOT}/frontend/dist" "${ROOT}/hub/static"
  echo "    Frontend built → hub/static/"
  cd "${ROOT}"

  echo ""
  echo ">>> Building Docker images..."
  docker build -t "${HUB_IMAGE}"   -f "${ROOT}/docker/hub.Dockerfile"   "${ROOT}"
  docker build -t "${AGENT_IMAGE}" -f "${ROOT}/docker/agent.Dockerfile" "${ROOT}"
  echo "    Built: ${HUB_IMAGE}"
  echo "    Built: ${AGENT_IMAGE}"
else
  echo ""
  echo ">>> Skipping build (--skip-build). Expecting images already present:"
  echo "    ${HUB_IMAGE}"
  echo "    ${AGENT_IMAGE}"
  docker image inspect "${HUB_IMAGE}"   > /dev/null 2>&1 || { echo "ERROR: image ${HUB_IMAGE} not found"; exit 1; }
  docker image inspect "${AGENT_IMAGE}" > /dev/null 2>&1 || { echo "ERROR: image ${AGENT_IMAGE} not found"; exit 1; }
fi

# ---------- Stage bundle directory --------------------------------------------
echo ""
echo ">>> Staging bundle at ${STAGING}..."
rm -rf "${STAGING}"
mkdir -p "${STAGING}/images" "${STAGING}/helm"

# Save Docker images (redirect stdout so the shell creates the file, avoiding
# Docker daemon path-resolution failures in WSL2/Docker Desktop environments)
echo "    Saving ${HUB_IMAGE}..."
docker save "${HUB_IMAGE}" > "${STAGING}/images/tcpdump-hub.tar"

echo "    Saving ${AGENT_IMAGE}..."
docker save "${AGENT_IMAGE}" > "${STAGING}/images/tcpdump-agent.tar"

# Copy Helm chart
cp -r "${ROOT}/deploy/helm/tcpdump-as-a-service" "${STAGING}/helm/"

# Copy values template and env example
cp "${ROOT}/deploy/helm/tcpdump-as-a-service/values.yaml" "${STAGING}/values.yaml"
cp "${ROOT}/.env.example" "${STAGING}/.env.example"

# ---------- Write the target-host deploy script -------------------------------
cat > "${STAGING}/deploy.sh" << 'DEPLOY_SCRIPT'
#!/usr/bin/env bash
# =============================================================================
# tcpdump-as-a-service — air-gap deployment script
#
# Run this on the target host (must have docker/podman, helm, kubectl).
#
# Usage:
#   ./deploy.sh [OPTIONS]
#
# Options:
#   --namespace NS     Kubernetes namespace (default: tcpdump-service)
#   --values FILE      Extra values file to merge (default: values.yaml in this dir)
#   --k3d              Import images into k3d instead of the default docker daemon
#   --cluster NAME     k3d cluster name (default: tcpdump-dev)
#   --openshift        Enable OpenShift-specific resources (SCC, Route, OAuthClient)
#   --load-only        Only load images, skip Helm deploy
#   --deploy-only      Skip image load, run Helm deploy only
#   -h, --help         Show this help
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

NAMESPACE="tcpdump-service"
VALUES_FILE="${SCRIPT_DIR}/values.yaml"
DO_K3D=false
K3D_CLUSTER="tcpdump-dev"
OPENSHIFT=false
LOAD_IMAGES=true
RUN_HELM=true

while [[ $# -gt 0 ]]; do
  case "$1" in
    --namespace)   NAMESPACE="$2"; shift ;;
    --values)      VALUES_FILE="$2"; shift ;;
    --k3d)         DO_K3D=true ;;
    --cluster)     K3D_CLUSTER="$2"; shift ;;
    --openshift)   OPENSHIFT=true ;;
    --load-only)   RUN_HELM=false ;;
    --deploy-only) LOAD_IMAGES=false ;;
    -h|--help)
      sed -n '2,/^# ===/p' "$0" | grep '^#' | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
  shift
done

# Detect image tags from saved tarballs
HUB_TAR="${SCRIPT_DIR}/images/tcpdump-hub.tar"
AGENT_TAR="${SCRIPT_DIR}/images/tcpdump-agent.tar"

# ---------- Load images -------------------------------------------------------
if [[ "$LOAD_IMAGES" == "true" ]]; then
  echo ""
  echo ">>> Loading Docker images..."
  docker load -i "${HUB_TAR}"
  docker load -i "${AGENT_TAR}"

  if [[ "$DO_K3D" == "true" ]]; then
    echo ""
    echo ">>> Importing images into k3d cluster '${K3D_CLUSTER}'..."
    # Extract the image:tag that was just loaded
    HUB_REF=$(docker load -i "${HUB_TAR}" 2>&1 | grep "Loaded image" | awk '{print $NF}' || true)
    AGENT_REF=$(docker load -i "${AGENT_TAR}" 2>&1 | grep "Loaded image" | awk '{print $NF}' || true)

    # docker load was already done above; re-inspect to get the name
    HUB_REF=$(docker image ls --format '{{.Repository}}:{{.Tag}}' | grep '^tcpdump-hub:' | head -1)
    AGENT_REF=$(docker image ls --format '{{.Repository}}:{{.Tag}}' | grep '^tcpdump-agent:' | head -1)

    k3d image import "${HUB_REF}" "${AGENT_REF}" -c "${K3D_CLUSTER}"
    echo "    Imported."
  fi
fi

# ---------- Determine image refs for Helm ------------------------------------
HUB_REF=$(docker image ls --format '{{.Repository}}:{{.Tag}}' | grep '^tcpdump-hub:' | head -1)
AGENT_REF=$(docker image ls --format '{{.Repository}}:{{.Tag}}' | grep '^tcpdump-agent:' | head -1)

if [[ -z "${HUB_REF}" || -z "${AGENT_REF}" ]]; then
  echo "ERROR: Could not determine image tags from local Docker daemon."
  echo "       Run with --load-only first, or check 'docker image ls'."
  exit 1
fi

echo ""
echo "    Hub image:   ${HUB_REF}"
echo "    Agent image: ${AGENT_REF}"

# ---------- Helm deploy -------------------------------------------------------
if [[ "$RUN_HELM" == "true" ]]; then
  EXTRA_ARGS=()
  if [[ "$OPENSHIFT" == "true" ]]; then
    EXTRA_ARGS+=(--set "openshift.enabled=true")
  fi

  echo ""
  echo ">>> Running helm upgrade --install..."
  helm upgrade --install tcpdump-as-a-service "${SCRIPT_DIR}/helm/tcpdump-as-a-service" \
    --namespace "${NAMESPACE}" \
    --create-namespace \
    --values "${VALUES_FILE}" \
    --set "image.hub=${HUB_REF}" \
    --set "image.agent=${AGENT_REF}" \
    --set "image.pullPolicy=IfNotPresent" \
    "${EXTRA_ARGS[@]}"

  echo ""
  echo ">>> Waiting for hub rollout..."
  kubectl rollout status deployment/tcpdump-as-a-service-hub \
    -n "${NAMESPACE}" --timeout=120s

  echo ""
  echo ">>> Pods in namespace ${NAMESPACE}:"
  kubectl get pods -n "${NAMESPACE}"
fi

echo ""
echo "Done."
DEPLOY_SCRIPT

chmod +x "${STAGING}/deploy.sh"

# ---------- Write README ------------------------------------------------------
cat > "${STAGING}/README.txt" << README
tcpdump-as-a-service — air-gap bundle
======================================

Bundle tag: ${TAG}

Contents
--------
  images/tcpdump-hub.tar    Hub Docker image
  images/tcpdump-agent.tar  Agent Docker image
  helm/                     Helm chart
  values.yaml               Default Helm values (edit before deploying)
  .env.example              Reference env-var documentation
  deploy.sh                 Deployment script

Quick start
-----------
1. Copy this directory to the target host.

2. Edit values.yaml:
     - Set hub.secretKey to a long random string
     - Set ingress.host to your cluster hostname
     - For OpenShift: set auth.mode=openshift and fill in auth.openshift.*
     - Adjust storage.storageClass for your cluster ("" uses the default)

3. Run the deploy script:

   # Standard Kubernetes / k3d
   ./deploy.sh

   # k3d (imports images into the named cluster)
   ./deploy.sh --k3d --cluster tcpdump-dev

   # OpenShift
   ./deploy.sh --openshift

   # Custom namespace
   ./deploy.sh --namespace my-namespace

   # Load images only (useful when docker daemon is on a different host)
   ./deploy.sh --load-only

   # Deploy only (images already loaded)
   ./deploy.sh --deploy-only

   # Custom values file
   ./deploy.sh --values my-custom-values.yaml

Notes
-----
- Images are loaded via 'docker load'. If your cluster uses a private
  registry, load them with 'docker load', retag, and push before running
  deploy.sh with --deploy-only and image overrides via --values.
- The Helm chart defaults to pullPolicy=IfNotPresent, which works for
  locally loaded images. No internet access is needed after loading.
- The hub keeps all capture state in memory; a pod restart clears it.
  Completed pcap files on the PVC survive restarts.

README

# ---------- Create the tar.gz ------------------------------------------------
echo ""
echo ">>> Creating archive..."
cd "${OUTPUT_DIR}"
tar -czf "${BUNDLE_NAME}.tar.gz" "${BUNDLE_NAME}/"
rm -rf "${STAGING}"

BUNDLE_PATH="${OUTPUT_DIR}/${BUNDLE_NAME}.tar.gz"
BUNDLE_SIZE=$(du -sh "${BUNDLE_PATH}" | cut -f1)

echo ""
echo "=================================================="
echo " Bundle ready: ${BUNDLE_PATH}"
echo " Size:         ${BUNDLE_SIZE}"
echo "=================================================="
echo ""
echo "Transfer to target host and run:"
echo "  tar -xzf ${BUNDLE_NAME}.tar.gz"
echo "  cd ${BUNDLE_NAME}"
echo "  vim values.yaml          # edit before deploying"
echo "  ./deploy.sh"
