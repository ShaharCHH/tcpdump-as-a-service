"""
Authentication layer.

Two modes, controlled by AUTH_MODE env var:

  "openshift"  — Full OAuth2 redirect flow using OpenShift's built-in OAuth server.
                 The hub must be registered as an OAuthClient resource on the cluster.

  "kubernetes" — User pastes their bearer token (e.g. from `kubectl create token`).
                 The hub validates it via the Kubernetes TokenReview API and issues
                 a signed session cookie. Works on any k8s cluster including k3d.

Sessions are stored as signed cookies (itsdangerous URLSafeTimedSerializer).
The cookie value is {"token": "<k8s-bearer-token>", "username": "<k8s-username>"}.
We store the raw token so every API handler can impersonate the user against k8s.
"""
from __future__ import annotations

import json
from typing import Optional
from urllib.parse import urlencode

import httpx
from fastapi import Cookie, HTTPException, Request, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from config import settings

# Max age of the session cookie in seconds
_SESSION_MAX_AGE = settings.session_max_age_hours * 3600
_signer = URLSafeTimedSerializer(settings.secret_key, salt="session")

COOKIE_NAME = "tcpdump_session"


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def create_session_cookie(token: str, username: str) -> str:
    """Sign and serialise a session payload into a cookie value."""
    return _signer.dumps({"token": token, "username": username})


def decode_session_cookie(value: str) -> Optional[dict]:
    """Verify signature and expiry. Returns the payload dict or None."""
    try:
        return _signer.loads(value, max_age=_SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


def get_current_user(request: Request) -> tuple[str, str]:
    """
    Extract (username, token) from the session cookie.
    Raises 401 if the cookie is missing or invalid.
    Used as a FastAPI dependency.
    """
    cookie_value = request.cookies.get(COOKIE_NAME)
    if not cookie_value:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    payload = decode_session_cookie(cookie_value)
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    return payload["username"], payload["token"]


# ---------------------------------------------------------------------------
# Kubernetes token validation (AUTH_MODE=kubernetes)
# ---------------------------------------------------------------------------

async def validate_k8s_token(token: str) -> Optional[str]:
    """
    Call the Kubernetes TokenReview API with the given bearer token.
    Returns the authenticated username on success, or None if the token is invalid.

    The hub's own service account must have RBAC permission to create TokenReviews
    (verb: create, resource: tokenreviews, group: authentication.k8s.io).
    """
    from k8s import get_hub_client

    k8s = get_hub_client()
    auth_api = k8s["auth_v1"]

    body = {
        "apiVersion": "authentication.k8s.io/v1",
        "kind": "TokenReview",
        "spec": {"token": token},
    }
    try:
        result = auth_api.create_token_review(body)
        if result.status.authenticated:
            return result.status.user.username
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# OpenShift OAuth helpers (AUTH_MODE=openshift)
# ---------------------------------------------------------------------------

async def get_openshift_oauth_endpoints() -> dict:
    """
    Fetch the OAuth server metadata from OpenShift's well-known discovery URL.
    Returns a dict with "authorization_endpoint" and "token_endpoint".
    Cached in memory after first call.
    """
    if not hasattr(get_openshift_oauth_endpoints, "_cache"):
        url = f"{settings.openshift_api_url}/.well-known/oauth-authorization-server"
        async with httpx.AsyncClient(verify=False) as client:
            resp = await client.get(url, timeout=10)
            resp.raise_for_status()
            get_openshift_oauth_endpoints._cache = resp.json()
    return get_openshift_oauth_endpoints._cache


async def build_openshift_login_url(state: str = "") -> str:
    """Return the URL the browser should be redirected to for OpenShift login."""
    endpoints = await get_openshift_oauth_endpoints()
    params = {
        "response_type": "code",
        "client_id": settings.oauth_client_id,
        "redirect_uri": settings.oauth_callback_url,
        "scope": "user:info",
    }
    if state:
        params["state"] = state
    return f"{endpoints['authorization_endpoint']}?{urlencode(params)}"


async def exchange_openshift_code(code: str) -> tuple[str, str]:
    """
    Exchange an OAuth authorisation code for an access token.
    Returns (access_token, username).
    Raises HTTPException on failure.
    """
    endpoints = await get_openshift_oauth_endpoints()
    token_url = endpoints["token_endpoint"]

    async with httpx.AsyncClient(verify=False) as client:
        resp = await client.post(
            token_url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.oauth_callback_url,
                "client_id": settings.oauth_client_id,
                "client_secret": settings.oauth_client_secret,
            },
            timeout=15,
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail="OpenShift token exchange failed")
        data = resp.json()
        access_token = data["access_token"]

    # Fetch the username from the OpenShift user info endpoint
    username = await get_openshift_username(access_token)
    return access_token, username


async def get_openshift_username(token: str) -> str:
    """Call /apis/user.openshift.io/v1/users/~ to get the current user's name."""
    url = f"{settings.openshift_api_url}/apis/user.openshift.io/v1/users/~"
    async with httpx.AsyncClient(verify=False) as client:
        resp = await client.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()["metadata"]["name"]
