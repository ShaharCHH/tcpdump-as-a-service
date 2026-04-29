/**
 * AuditLog.jsx — Admin-only audit log viewer.
 *
 * Accessible via /audit. The hub's /api/audit endpoint returns 403 for
 * non-admin users, and the NavBar only shows the link for admins — but we
 * also check on this page in case someone navigates here directly.
 *
 * Displays a table of audit entries (newest first) showing:
 *   - Timestamp
 *   - Username who triggered the event
 *   - Event type (started / stopped / completed / error)
 *   - Capture ID
 *   - Target pods
 *   - Duration & filters
 *
 * Props: none — fetches data independently.
 */
import React, { useEffect, useState } from 'react'
import { getAuditLog } from '../api'
import { useAuth } from '../App'

export default function AuditLog() {
  const { user } = useAuth()
  const [entries, setEntries] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    getAuditLog(200)
      .then(setEntries)
      .catch(err => setError(err.message))
      .finally(() => setLoading(false))
  }, [])

  // Show a clear message if a non-admin somehow lands here
  if (!user?.is_admin) {
    return (
      <div style={styles.page}>
        <p style={styles.error}>You do not have permission to view the audit log.</p>
      </div>
    )
  }

  if (loading) return <p style={styles.loading}>Loading audit log...</p>
  if (error)   return <p style={styles.error}>{error}</p>

  return (
    <div style={styles.page}>
      <h2 style={styles.heading}>Audit Log</h2>
      <p style={styles.note}>
        Showing the {entries.length} most recent events. Newest first.
      </p>

      {entries.length === 0 ? (
        <p style={styles.empty}>No audit entries yet.</p>
      ) : (
        <table style={styles.table}>
          <thead>
            <tr>
              <th style={styles.th}>Time</th>
              <th style={styles.th}>User</th>
              <th style={styles.th}>Event</th>
              <th style={styles.th}>Capture ID</th>
              <th style={styles.th}>Pods</th>
              <th style={styles.th}>Duration</th>
              <th style={styles.th}>Filter</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((entry, i) => (
              <tr key={i} style={styles.tr}>
                <td style={{ ...styles.td, fontSize: '11px', color: 'var(--color-text-muted)', whiteSpace: 'nowrap' }}>
                  {new Date(entry.timestamp).toLocaleString()}
                </td>
                <td style={{ ...styles.td, fontWeight: 600, fontSize: '13px' }}>
                  {entry.username}
                </td>
                <td style={styles.td}>
                  <EventBadge event={entry.event} />
                </td>
                <td style={{ ...styles.td, fontFamily: 'var(--font-mono)', fontSize: '11px' }}>
                  {entry.capture_id.slice(0, 8)}…
                </td>
                <td style={{ ...styles.td, fontSize: '12px', fontFamily: 'var(--font-mono)' }}>
                  {entry.pods.join(', ')}
                </td>
                <td style={{ ...styles.td, fontSize: '12px' }}>
                  {entry.duration_minutes} min
                </td>
                <td style={{ ...styles.td, fontSize: '11px', fontFamily: 'var(--font-mono)', color: 'var(--color-text-muted)' }}>
                  {buildFilterSummary(entry.filters)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

function EventBadge({ event }) {
  const config = {
    started:   { color: 'var(--color-primary)' },
    completed: { color: 'var(--color-success)' },
    stopped:   { color: '#f0a84c' },
    error:     { color: 'var(--color-danger)' },
  }[event] || { color: 'var(--color-text-muted)' }

  return (
    <span style={{
      padding: '2px 8px',
      borderRadius: '999px',
      background: `${config.color}22`,
      color: config.color,
      fontWeight: 700,
      fontSize: '11px',
    }}>
      {event}
    </span>
  )
}

// Summarise the filter object into a short string for the table cell
function buildFilterSummary(filters) {
  if (!filters) return '—'
  const parts = []
  if (filters.host)     parts.push(`host ${filters.host}`)
  if (filters.src_host) parts.push(`src ${filters.src_host}`)
  if (filters.dst_host) parts.push(`dst ${filters.dst_host}`)
  if (filters.src_port) parts.push(`:${filters.src_port}`)
  if (filters.dst_port) parts.push(`→:${filters.dst_port}`)
  return parts.join(' ') || 'none'
}

const styles = {
  page: {
    maxWidth: '1200px',
    margin: '0 auto',
    padding: '32px 24px',
  },
  heading: {
    fontSize: '20px',
    fontWeight: 700,
    marginBottom: '8px',
  },
  note: {
    fontSize: '13px',
    color: 'var(--color-text-muted)',
    marginBottom: '24px',
  },
  empty: {
    fontSize: '14px',
    color: 'var(--color-text-muted)',
  },
  table: {
    width: '100%',
    borderCollapse: 'collapse',
    background: 'var(--color-surface)',
    border: '1px solid var(--color-border)',
    borderRadius: 'var(--radius)',
    overflow: 'hidden',
    fontSize: '13px',
  },
  th: {
    textAlign: 'left',
    padding: '10px 14px',
    fontSize: '11px',
    fontWeight: 700,
    color: 'var(--color-text-muted)',
    textTransform: 'uppercase',
    letterSpacing: '0.05em',
    borderBottom: '1px solid var(--color-border)',
    background: 'var(--color-bg)',
    whiteSpace: 'nowrap',
  },
  tr: {
    borderBottom: '1px solid var(--color-border)',
  },
  td: {
    padding: '8px 14px',
    verticalAlign: 'middle',
  },
  loading: {
    padding: '48px',
    textAlign: 'center',
    color: 'var(--color-text-muted)',
  },
  error: {
    padding: '48px',
    textAlign: 'center',
    color: 'var(--color-danger)',
  },
}
