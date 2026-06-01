"""
Kubernetes API operations.

Two types of calls are made here:
  1. User-impersonated calls  — use the user's own bearer token so that Kubernetes
     enforces their RBAC. Used for listing namespaces and pods.
  2. Hub service-account calls — use the hub's own in-cluster token. Used for
     privileged lookups like getting pod nodeName and container IDs.

The hub service account needs a ClusterRole with:
  - get/list on pods (cluster-wide, for nodeName lookup)
  - create on tokenreviews (for AUTH_MODE=kubernetes)
  - create on subjectaccessreviews (to check admin access)
"""
from __future__ import annotations

import logging
from typing import Any

from kubernetes import client as k8s_client
from kubernetes import config as k8s_config

from config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Client factories
# ---------------------------------------------------------------------------

def get_hub_client() -> dict[str, Any]:
    """
    Return Kubernetes API clients using the hub's own service-account credentials.
    Loads in-cluster config when k8s_in_cluster=True, otherwise falls back to
    the local kubeconfig (useful for local development).
    """
    logger.debug("get_hub_client: loading %s config",
                 "in-cluster" if settings.k8s_in_cluster else "kubeconfig")
    if settings.k8s_in_cluster:
        k8s_config.load_incluster_config()
    else:
        k8s_config.load_kube_config()

    return {
        "core_v1": k8s_client.CoreV1Api(),
        "auth_v1": k8s_client.AuthenticationV1Api(),
        "authz_v1": k8s_client.AuthorizationV1Api(),
        "custom": k8s_client.CustomObjectsApi(),
    }


def get_user_client(user_token: str) -> dict[str, Any]:
    """
    Return Kubernetes API clients configured with the user's bearer token.
    All calls through this client are subject to the user's RBAC permissions.
    """
    logger.debug("get_user_client: building user-impersonated client")
    configuration = k8s_client.Configuration()

    if settings.k8s_in_cluster:
        k8s_config.load_incluster_config(client_configuration=configuration)
    else:
        k8s_config.load_kube_config(client_configuration=configuration)

    configuration.api_key = {"authorization": f"Bearer {user_token}"}
    configuration.api_key_prefix = {"authorization": ""}

    api_client = k8s_client.ApiClient(configuration=configuration)
    return {
        "core_v1": k8s_client.CoreV1Api(api_client=api_client),
        "custom": k8s_client.CustomObjectsApi(api_client=api_client),
    }


# ---------------------------------------------------------------------------
# Namespace listing (user-impersonated)
# ---------------------------------------------------------------------------

async def list_user_namespaces(user_token: str) -> list[dict]:
    """
    Return namespaces where the user can create pods (proxy for admin access).

    Strategy:
      1. Try listing all namespaces via the standard k8s API (works on vanilla k8s
         and for cluster-admin users on OpenShift).
      2. If that returns 403 (typical for non-cluster-admin users on OpenShift),
         fall back to the OpenShift Projects API which returns only the projects
         the user has access to — no cluster-wide list permission needed.
      3. For each namespace/project found, verify create-pods permission via
         SelfSubjectAccessReview using the user's own token.

    Returns a list of dicts: [{"name": "...", "status": "Active"}, ...]
    """
    user_clients = get_user_client(user_token)
    core = user_clients["core_v1"]

    # --- Step 1: try standard k8s namespace list ---
    raw_namespaces: list[dict] = []   # [{"name": ..., "status": ...}]
    try:
        logger.debug("list_user_namespaces: calling list_namespace() with user token")
        ns_list = core.list_namespace()
        raw_namespaces = [
            {"name": ns.metadata.name, "status": ns.status.phase}
            for ns in ns_list.items
        ]
        logger.debug("list_user_namespaces: list_namespace() returned %d entries: %s",
                     len(raw_namespaces), [n["name"] for n in raw_namespaces])

    except k8s_client.exceptions.ApiException as e:
        if e.status == 403:
            # --- Step 2: OpenShift Projects API fallback ---
            # On OpenShift, regular users (even namespace admins) cannot list ALL
            # namespaces cluster-wide. The Projects API returns only the projects
            # the authenticated user can see, filtered by their RBAC automatically.
            logger.debug(
                "list_user_namespaces: list_namespace() returned 403 — "
                "falling back to OpenShift projects.project.openshift.io API"
            )
            try:
                custom = user_clients["custom"]
                projects = custom.list_cluster_custom_object(
                    group="project.openshift.io",
                    version="v1",
                    plural="projects",
                )
                raw_namespaces = [
                    {
                        "name": p["metadata"]["name"],
                        "status": p.get("status", {}).get("phase", "Active"),
                    }
                    for p in projects.get("items", [])
                ]
                logger.debug(
                    "list_user_namespaces: OpenShift projects API returned %d project(s): %s",
                    len(raw_namespaces), [n["name"] for n in raw_namespaces]
                )
            except k8s_client.exceptions.ApiException as oe:
                # Not OpenShift, or no projects at all — return empty
                logger.warning(
                    "list_user_namespaces: OpenShift projects API also failed "
                    "(status=%s). User may have no accessible namespaces. body=%s",
                    oe.status, oe.body,
                )
                return []
        else:
            logger.error("list_user_namespaces: list_namespace() failed with status=%s body=%s",
                         e.status, e.body)
            raise

    # --- Step 3: filter by create-pods permission ---
    # Build a single user-impersonated AuthorizationV1Api for all reviews.
    user_authz = k8s_client.AuthorizationV1Api(api_client=core.api_client)
    accessible = []

    for ns in raw_namespaces:
        name = ns["name"]
        review = k8s_client.V1SelfSubjectAccessReview(
            spec=k8s_client.V1SelfSubjectAccessReviewSpec(
                resource_attributes=k8s_client.V1ResourceAttributes(
                    namespace=name,
                    verb="create",
                    resource="pods",
                )
            )
        )
        try:
            result = user_authz.create_self_subject_access_review(review)
            allowed = result.status.allowed
            logger.debug(
                "list_user_namespaces: create-pods check namespace=%s allowed=%s",
                name, allowed,
            )
            if allowed:
                accessible.append(ns)
        except Exception as ex:
            logger.warning(
                "list_user_namespaces: access review failed for namespace=%s: %s",
                name, ex,
            )
            continue

    logger.debug("list_user_namespaces: final accessible namespaces: %s",
                 [n["name"] for n in accessible])
    return accessible


# ---------------------------------------------------------------------------
# Pod listing (user-impersonated)
# ---------------------------------------------------------------------------

async def list_pods(user_token: str, namespace: str) -> list[dict]:
    """
    List pods in a namespace using the user's own token.
    Kubernetes enforces the user's RBAC — if they can't list pods, this raises 403.

    Returns a list of dicts with pod name, status, and container names.
    """
    logger.debug("list_pods: namespace=%s", namespace)
    clients = get_user_client(user_token)
    core = clients["core_v1"]

    try:
        pod_list = core.list_namespaced_pod(namespace=namespace)
    except k8s_client.exceptions.ApiException as e:
        logger.error("list_pods: namespace=%s failed status=%s body=%s",
                     namespace, e.status, e.body)
        raise

    result = []
    for pod in pod_list.items:
        containers = [c.name for c in (pod.spec.containers or [])]
        result.append({
            "name": pod.metadata.name,
            "namespace": namespace,
            "status": pod.status.phase,
            "containers": containers,
            "node_name": pod.spec.node_name,
        })
        logger.debug("list_pods: pod=%s status=%s node=%s containers=%s",
                     pod.metadata.name, pod.status.phase,
                     pod.spec.node_name, containers)

    logger.debug("list_pods: namespace=%s returned %d pod(s)", namespace, len(result))
    return result


# ---------------------------------------------------------------------------
# Pod detail lookup (hub service-account)
# ---------------------------------------------------------------------------

async def get_pod_capture_info(pod_name: str, namespace: str) -> dict:  # noqa: D401
    """
    Return the information needed to start a capture on a pod:
      - node_name: which cluster node the pod runs on (used to route to the right agent)
      - container_id: the full container runtime ID (used by the agent for nsenter)
      - container_name: name of the first (or only) container

    This call uses the hub's own service account, not the user's token.
    """
    logger.debug("get_pod_capture_info: pod=%s/%s", namespace, pod_name)
    hub = get_hub_client()
    core = hub["core_v1"]

    pod = core.read_namespaced_pod(name=pod_name, namespace=namespace)
    node_name = pod.spec.node_name

    if not pod.status.container_statuses:
        raise ValueError(f"Pod {namespace}/{pod_name} has no running containers")

    # Use the first container by default; callers can request a specific one
    cs = pod.status.container_statuses[0]
    container_id = cs.container_id  # e.g. "containerd://abc123..."
    container_name = cs.name

    logger.debug("get_pod_capture_info: pod=%s/%s node=%s container=%s id=%s",
                 namespace, pod_name, node_name, container_name, container_id)
    return {
        "node_name": node_name,
        "container_id": container_id,
        "container_name": container_name,
    }


async def get_pod_capture_info_for_container(
    pod_name: str, namespace: str, container_name: str
) -> dict:
    """Like get_pod_capture_info but targets a specific named container."""
    hub = get_hub_client()
    core = hub["core_v1"]

    pod = core.read_namespaced_pod(name=pod_name, namespace=namespace)
    node_name = pod.spec.node_name

    for cs in pod.status.container_statuses or []:
        if cs.name == container_name:
            return {
                "node_name": node_name,
                "container_id": cs.container_id,
                "container_name": cs.name,
            }

    raise ValueError(f"Container {container_name} not found in pod {namespace}/{pod_name}")
