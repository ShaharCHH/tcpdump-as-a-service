/**
 * App.jsx — Root component.
 *
 * Responsibilities:
 *  - Set up client-side routing (React Router).
 *  - Check whether the user is logged in on first load.
 *  - Pass the current user object down to pages that need it.
 *  - Render the top navigation bar on authenticated pages.
 *
 * Routes:
 *   /login          → Login page (token paste or OpenShift OAuth)
 *   /               → Main dashboard (namespace + pod selector + capture form)
 *   /captures/:id   → Live capture view
 *   /history        → Past captures list
 *   /audit          → Audit log (admin only)
 */
import React, { createContext, useContext, useEffect, useState } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useNavigate } from 'react-router-dom'
import { getMe, logout } from './api'
import Login from './components/Login'
import Dashboard from './components/Dashboard'
import CaptureView from './components/CaptureView'
import CaptureHistory from './components/CaptureHistory'
import AuditLog from './components/AuditLog'

// ---------------------------------------------------------------------------
// Auth context — provides the current user object to all child components
// without prop-drilling.
// ---------------------------------------------------------------------------

// The context value is { user, setUser }
// user is null (not logged in) or { username, is_admin }
export const AuthContext = createContext({ user: null, setUser: () => {} })
export const useAuth = () => useContext(AuthContext)

// ---------------------------------------------------------------------------
// App shell
// ---------------------------------------------------------------------------

export default function App() {
  const [user, setUser] = useState(undefined)  // undefined = "loading"

  // On first render, ask the hub who we are.
  // If the session cookie is valid we get back { username, is_admin }.
  // If not, we get a 401 which api.js translates into a redirect to /login —
  // but we catch the error here so we can show the login page instead.
  useEffect(() => {
    getMe()
      .then(setUser)
      .catch(() => setUser(null))
  }, [])

  // Show a blank screen while we check the session
  if (user === undefined) return null

  return (
    <AuthContext.Provider value={{ user, setUser }}>
      <BrowserRouter>
        {user && <NavBar />}
        <Routes>
          {/* Public */}
          <Route path="/login" element={<Login />} />

          {/* Protected — redirect to /login if not authenticated */}
          <Route path="/"           element={<Protected><Dashboard /></Protected>} />
          <Route path="/captures/:id" element={<Protected><CaptureView /></Protected>} />
          <Route path="/history"    element={<Protected><CaptureHistory /></Protected>} />
          <Route path="/audit"      element={<Protected><AuditLog /></Protected>} />

          {/* Catch-all */}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthContext.Provider>
  )
}

// ---------------------------------------------------------------------------
// Route guard — redirects unauthenticated users to /login
// ---------------------------------------------------------------------------
function Protected({ children }) {
  const { user } = useAuth()
  if (!user) return <Navigate to="/login" replace />
  return children
}

// ---------------------------------------------------------------------------
// Top navigation bar
// ---------------------------------------------------------------------------
function NavBar() {
  const { user, setUser } = useAuth()
  const navigate = useNavigate()

  async function handleLogout() {
    await logout()
    setUser(null)
    navigate('/login')
  }

  return (
    <nav style={styles.nav}>
      <span style={styles.brand}>tcpdump-as-a-service</span>
      <div style={styles.navLinks}>
        <a href="/" style={styles.link}>Capture</a>
        <a href="/history" style={styles.link}>History</a>
        {user?.is_admin && <a href="/audit" style={styles.link}>Audit</a>}
      </div>
      <div style={styles.navRight}>
        <span style={styles.username}>{user?.username}</span>
        <button onClick={handleLogout} style={styles.logoutBtn}>Logout</button>
      </div>
    </nav>
  )
}

const styles = {
  nav: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '0 24px',
    height: '52px',
    background: 'var(--color-surface)',
    borderBottom: '1px solid var(--color-border)',
    position: 'sticky',
    top: 0,
    zIndex: 100,
  },
  brand: {
    fontWeight: 700,
    fontSize: '15px',
    color: 'var(--color-primary)',
    letterSpacing: '0.02em',
  },
  navLinks: {
    display: 'flex',
    gap: '24px',
  },
  link: {
    color: 'var(--color-text-muted)',
    fontSize: '13px',
    textDecoration: 'none',
    transition: 'color 0.15s',
  },
  navRight: {
    display: 'flex',
    alignItems: 'center',
    gap: '12px',
  },
  username: {
    fontSize: '13px',
    color: 'var(--color-text-muted)',
  },
  logoutBtn: {
    background: 'transparent',
    border: '1px solid var(--color-border)',
    borderRadius: 'var(--radius)',
    color: 'var(--color-text-muted)',
    padding: '4px 12px',
    fontSize: '12px',
    cursor: 'pointer',
  },
}
