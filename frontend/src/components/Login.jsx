/**
 * Login.jsx — Authentication page.
 *
 * Supports two modes, determined by asking the hub at /auth/login:
 *
 *   "kubernetes" — Shows a text area where the user pastes their bearer token
 *                  (obtained via `kubectl create token <sa>` or from kubeconfig).
 *                  The token is validated server-side via the TokenReview API.
 *
 *   "openshift"  — The hub's /auth/login redirected the browser to the OpenShift
 *                  OAuth server. This page is only shown if the redirect didn't
 *                  happen (e.g. a direct visit). We show a "Login with OpenShift"
 *                  button that triggers the redirect again.
 *
 * On successful login the hub sets an HttpOnly session cookie and we redirect
 * to the main dashboard.
 */
import React, { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getAuthMode, submitToken } from '../api'
import { useAuth } from '../App'

export default function Login() {
  const { setUser } = useAuth()
  const navigate = useNavigate()

  const [authMode, setAuthMode] = useState(null)   // "kubernetes" | "openshift" | null (loading)
  const [token, setToken] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  // Ask the hub what auth mode is active so we know what to render.
  useEffect(() => {
    getAuthMode()
      .then(mode => {
        if (!mode) {
          // Hub returned a redirect (OpenShift mode) — the browser followed it.
          // If we're still on this page the redirect was intercepted by fetch,
          // so we just show the "Login with OpenShift" button.
          setAuthMode('openshift')
        } else {
          setAuthMode(mode)
        }
      })
      .catch(() => setAuthMode('kubernetes'))  // Fallback to token mode on error
  }, [])

  // Handle token form submission (kubernetes mode)
  async function handleTokenSubmit(e) {
    e.preventDefault()
    if (!token.trim()) return
    setError('')
    setLoading(true)
    try {
      const result = await submitToken(token.trim())
      // Store user info in app state and go to the dashboard
      setUser({ username: result.username, is_admin: result.is_admin })
      navigate('/')
    } catch (err) {
      setError(err.message || 'Invalid token')
    } finally {
      setLoading(false)
    }
  }

  // Trigger OpenShift OAuth redirect
  function handleOpenShiftLogin() {
    window.location.href = '/auth/login'
  }

  return (
    <div style={styles.page}>
      <div style={styles.card}>
        {/* Header */}
        <h1 style={styles.title}>tcpdump as a service</h1>
        <p style={styles.subtitle}>Capture pod network traffic on your cluster</p>

        {/* Loading */}
        {authMode === null && (
          <p style={styles.muted}>Loading...</p>
        )}

        {/* Kubernetes token mode */}
        {authMode === 'kubernetes' && (
          <form onSubmit={handleTokenSubmit} style={styles.form}>
            <label style={styles.label}>
              Kubernetes Bearer Token
              <span style={styles.hint}>
                Run: <code>kubectl create token default -n default</code>
              </span>
            </label>
            {/* Use a password input to avoid the token being shown in plain text */}
            <input
              type="password"
              value={token}
              onChange={e => setToken(e.target.value)}
              placeholder="Paste your bearer token here"
              style={styles.tokenInput}
              autoFocus
            />
            {error && <p style={styles.error}>{error}</p>}
            <button type="submit" disabled={loading || !token.trim()} style={styles.btn}>
              {loading ? 'Validating...' : 'Login'}
            </button>
          </form>
        )}

        {/* OpenShift OAuth mode */}
        {authMode === 'openshift' && (
          <div style={styles.form}>
            <p style={styles.muted}>
              Sign in using your OpenShift account.
            </p>
            <button onClick={handleOpenShiftLogin} style={styles.btn}>
              Login with OpenShift
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

const styles = {
  page: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: '100vh',
    padding: '24px',
    background: 'var(--color-bg)',
  },
  card: {
    background: 'var(--color-surface)',
    border: '1px solid var(--color-border)',
    borderRadius: 'var(--radius)',
    padding: '40px',
    width: '100%',
    maxWidth: '440px',
  },
  title: {
    fontSize: '20px',
    fontWeight: 700,
    color: 'var(--color-primary)',
    marginBottom: '6px',
  },
  subtitle: {
    color: 'var(--color-text-muted)',
    marginBottom: '32px',
    fontSize: '13px',
  },
  form: {
    display: 'flex',
    flexDirection: 'column',
    gap: '14px',
  },
  label: {
    display: 'flex',
    flexDirection: 'column',
    gap: '4px',
    fontSize: '13px',
    fontWeight: 600,
  },
  hint: {
    fontWeight: 400,
    color: 'var(--color-text-muted)',
    fontSize: '12px',
  },
  tokenInput: {
    width: '100%',
    padding: '10px 12px',
    fontFamily: 'var(--font-mono)',
    fontSize: '12px',
  },
  error: {
    color: 'var(--color-danger)',
    fontSize: '13px',
  },
  muted: {
    color: 'var(--color-text-muted)',
    fontSize: '13px',
    marginBottom: '16px',
  },
  btn: {
    background: 'var(--color-primary)',
    color: '#fff',
    border: 'none',
    borderRadius: 'var(--radius)',
    padding: '10px 20px',
    fontWeight: 600,
    fontSize: '14px',
    cursor: 'pointer',
    marginTop: '4px',
  },
}
