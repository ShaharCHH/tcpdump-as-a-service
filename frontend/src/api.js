/**
 * api.js — centralised HTTP and WebSocket client for the tcpdump-as-a-service hub.
 *
 * All API calls go through the functions in this file so that:
 *  - The base URL is defined in one place (easy to change for different environments).
 *  - Error handling follows a consistent pattern.
 *  - WebSocket helpers manage reconnection and message parsing.
 *
 * The hub uses HttpOnly session cookies for auth, so no Authorization headers
 * are needed — the browser sends the cookie automatically.
 */

const BASE = ''  // Empty string → same origin. Override for local dev if needed.

// ---------------------------------------------------------------------------
// Helper: makes a fetch call and throws on non-2xx responses.
// Returns the parsed JSON body.
// ---------------------------------------------------------------------------
async function apiFetch(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    credentials: 'include',           // Send the session cookie on every request
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  })

  if (res.status === 401) {
    // Session expired — redirect to login so the user re-authenticates
    window.location.href = '/login'
    throw new Error('Unauthenticated')
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `HTTP ${res.status}`)
  }

  // 204 No Content — nothing to parse
  if (res.status === 204) return null
  return res.json()
}

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

/** Fetch the auth mode from the server ("kubernetes" or "openshift"). */
export async function getAuthMode() {
  const data = await apiFetch('/auth/login')
  return data.auth_mode   // "kubernetes" or undefined (openshift redirects)
}

/**
 * Submit a Kubernetes bearer token for validation.
 * On success the hub sets a session cookie and returns the username.
 */
export async function submitToken(token) {
  return apiFetch('/auth/token', {
    method: 'POST',
    body: JSON.stringify({ token }),
  })
}

/** Get the currently logged-in user's info. Throws 401 if not logged in. */
export async function getMe() {
  return apiFetch('/auth/me')
}

/** Clear the session cookie (log out). */
export async function logout() {
  return apiFetch('/auth/logout', { method: 'POST' })
}

// ---------------------------------------------------------------------------
// Kubernetes resources
// ---------------------------------------------------------------------------

/** List namespaces the current user has admin access to. */
export async function listNamespaces() {
  const data = await apiFetch('/api/namespaces')
  return data.namespaces  // [{ name, status }, ...]
}

/** List pods in a specific namespace. */
export async function listPods(namespace) {
  const data = await apiFetch(`/api/namespaces/${namespace}/pods`)
  return data.pods  // [{ name, namespace, status, containers, node_name }, ...]
}

// ---------------------------------------------------------------------------
// Captures
// ---------------------------------------------------------------------------

/**
 * Start a new capture session.
 *
 * @param {Array}  pods    - [{ namespace, pod_name, container_name? }, ...]
 * @param {number} duration_minutes
 * @param {Object} filters - { host?, src_host?, dst_host?, src_port?, dst_port?,
 *                             interface?, packet_count? }
 * @returns {Object} The initial CaptureState from the hub.
 */
export async function startCapture(pods, duration_minutes, filters) {
  return apiFetch('/api/captures', {
    method: 'POST',
    body: JSON.stringify({ pods, duration_minutes, filters }),
  })
}

/** List all captures started by the current user. */
export async function listCaptures() {
  const data = await apiFetch('/api/captures')
  return data.captures
}

/** Get the current state of a specific capture. */
export async function getCapture(captureId) {
  return apiFetch(`/api/captures/${captureId}`)
}

/** Request cancellation of an in-progress capture. */
export async function stopCapture(captureId) {
  return apiFetch(`/api/captures/${captureId}`, { method: 'DELETE' })
}

/**
 * Build a download URL for pcap files.
 * The browser navigates to this URL to trigger the zip download.
 *
 * @param {string} captureId
 * @param {string} podKey - "namespace/pod_name", or omit for all pods
 */
export function downloadUrl(captureId, podKey) {
  if (podKey) {
    return `${BASE}/api/captures/${captureId}/download/${podKey}`
  }
  return `${BASE}/api/captures/${captureId}/download`
}

// ---------------------------------------------------------------------------
// Admin
// ---------------------------------------------------------------------------

/** Fetch the audit log (admin users only). */
export async function getAuditLog(limit = 200) {
  const data = await apiFetch(`/api/audit?limit=${limit}`)
  return data.entries
}

/** List connected agent nodes (admin users only). */
export async function listAgents() {
  const data = await apiFetch('/api/agents')
  return data.nodes
}

// ---------------------------------------------------------------------------
// WebSocket — live packet stream
// ---------------------------------------------------------------------------

/**
 * Open a WebSocket connection to receive the live tcpdump stream for one pod.
 *
 * @param {string}   captureId  - The capture session ID
 * @param {string}   podKey     - "namespace/pod_name"
 * @param {Function} onMessage  - Called with each parsed JSON message from the hub
 * @param {Function} onClose    - Called when the WebSocket closes
 * @returns {WebSocket} The raw WebSocket — call .close() to disconnect.
 *
 * Message types the caller will receive:
 *   { type: "PACKET_LINE", line: "..." }   — one decoded packet line
 *   { type: "CAPTURE_DONE" }               — capture finished normally
 *   { type: "ERROR", message: "..." }      — something went wrong
 */
export function openLiveStream(captureId, podKey, onMessage, onClose) {
  const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
  const host = window.location.host
  // URL-encode the pod key since it contains a "/" character
  const encodedPodKey = encodeURIComponent(podKey)
  const url = `${protocol}://${host}/ws/capture/${captureId}/${encodedPodKey}`

  const ws = new WebSocket(url)

  ws.onmessage = (event) => {
    try {
      onMessage(JSON.parse(event.data))
    } catch {
      // Ignore malformed messages
    }
  }

  ws.onclose = () => {
    if (onClose) onClose()
  }

  return ws
}
