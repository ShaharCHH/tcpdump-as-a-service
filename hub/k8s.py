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

from typing import Any

from kubernetes import client as k8s_client
from kubernetes import config as k8s_config

from config import settings

# ---------------------------------------------------------------------------
# Client factories
# ---------------------------------------------------------------------------

def get_hub_client() -> dict[str, Any]:
    """
    Return Kubernetes API clients using the hub's own service-account credentials.
    Loads in-cluster config when k8s_in_cluster=True, otherwise falls back to
    the local kubeconfig (useful for local development).
    """
    if settings.k8s_in_cluster:
        k8s_config.load_incluster_config()
    else:
        k8s_config.load_kube_config()

    return {
        "core_v1": k8s_client.CoreV1Api(),
        "auth_v1": k8s_client.AuthenticationV1Api(),
        "authz_v1": k8s_client.AuthorizationV1Api(),
    }


def get_user_client(user_token: str) -> dict[str, Any]:
    """
    Return Kubernetes API clients configured with the user's bearer token.
    All calls through this client are subject to the user's RBAC permissions.
    """
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
    }


# ---------------------------------------------------------------------------
# Namespace listing (user-impersonated)
# ---------------------------------------------------------------------------

async def list_user_namespaces(user_token: str) -> list[dict]:
    """
    Return namespaces where the user has the 'admin' role (i.e. can create/delete
    most resources). We list all namespaces visible to the user and then issue a
    SelfSubjectAccessReview for each to check for the 'admin' verb on pods.

    Returns a list of dicts: [{"name": "...", "status": "Active"}, ...]
    """
    clients = get_user_client(user_token)
    core = clients["core_v1"]

    try:
        ns_list = core.list_namespace()
    except k8s_client.exceptions.ApiException as e:
        if e.status == 403:
            # User can only see namespaces they have access to on some clusters.
            # Try listing with a different scope.
            return []
        raise

    hub_clients = get_hub_client()
    authz = hub_clients["authz_v1"]

    accessible = []
    for ns in ns_list.items:
        name = ns.metadata.name
        # Check if the user can create pods in this namespace (proxy for admin access)
        review = k8s_client.V1SelfSubjectAccessReview(
            spec=k8s_client.V1SelfSubjectAccessReviewSpec(
                resource_attributes=k8s_client.V1ResourceAttributes(
                    namespace=name,
                    verb="create",
                    resource="pods",
                )
            )
        )
        # We impersonate the user by passing their token via the Impersonate header
        # using the hub's client — this is the standard k8s impersonation pattern.
        try:
            # Use the user's own client for the access review
            user_authz = k8s_client.AuthorizationV1Api(
                api_client=get_user_client(user_token)["core_v1"].api_client
            )
            result = user_authz.create_self_subject_access_review(review)
            if result.status.allowed:
                accessible.append({
                    "name": name,
                    "status": ns.status.phase,
                })
        except Exception:
            continue

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
    clients = get_user_client(user_token)
    core = clients["core_v1"]

    pod_list = core.list_namespaced_pod(namespace=namespace)
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
    return result


# ---------------------------------------------------------------------------
# Pod detail lookup (hub service-account)
# ---------------------------------------------------------------------------

async def get_pod_capture_info(pod_name: str, namespace: str) -> dict:
    """
    Return the information needed to start a capture on a pod:
      - node_name: which cluster node the pod runs on (used to route to the right agent)
      - container_id: the full container runtime ID (used by the agent for nsenter)
      - container_name: name of the first (or only) container

    This call uses the hub's own service account, not the user's token.
    """
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
