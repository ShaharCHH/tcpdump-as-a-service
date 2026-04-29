/**
 * CaptureHistory.jsx — List of past and active captures for the current user.
 *
 * Fetches from GET /api/captures and displays a table with:
 *   - Capture ID (links to the live view)
 *   - Status badge
 *   - Number of pods
 *   - Duration
 *   - Start time
 *   - [Download all] button (only shown when at least one pod has pcap files)
 *
 * The list auto-refreshes every 5 seconds when there are running captures,
 * so the user can see when a capture they started elsewhere has finished.
 */
import React, { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { downloadUrl, listCaptures } from '../api'

export default function CaptureHistory() {
  const [captures, setCaptures] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  // Ref for the polling interval so we can clear it on unmount
  const intervalRef = useRef(null)

  async function refresh() {
    try {
      const list = await listCaptures()
      setCaptures(list)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    refresh()
    // Poll every 5 seconds while the component is mounted.
    // In a production app you might use server-sent events for this.
    intervalRef.current = setInterval(refresh, 5000)
    return () => clearInterval(intervalRef.current)
  }, [])

  if (loading) return <p style={styles.loading}>Loading captures...</p>
  if (error)   return <p style={styles.error}>{error}</p>

  return (
    <div style={styles.page}>
      <h2 style={styles.heading}>Capture History</h2>
      <p style={styles.note}>
        Files are available for download for 24 hours after the capture ends.
      </p>

      {captures.length === 0 ? (
        <p style={styles.empty}>No captures yet. <Link to="/">Start one →</Link></p>
      ) : (
        <table style={styles.table}>
          <thead>
            <tr>
              <th style={styles.th}>ID</th>
              <th style={styles.th}>Status</th>
              <th style={styles.th}>Pods</th>
              <th style={styles.th}>Duration</th>
              <th style={styles.th}>Started</th>
              <th style={styles.th}>Download</th>
            </tr>
          </thead>
          <tbody>
            {[...captures].reverse().map(c => (
              <tr key={c.capture_id} style={styles.tr}>
                {/* Capture ID — link to the live view */}
                <td style={styles.td}>
                  <Link
                    to={`/captures/${c.capture_id}`}
                    style={{ fontFamily: 'var(--font-mono)', fontSize: '11px' }}
                  >
                    {c.capture_id.slice(0, 8)}…
                  </Link>
                </td>
                <td style={styles.td}>
                  <StatusBadge status={c.status} />
                </td>
                <td style={styles.td}>
                  <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
                    {c.pods.map(p => (
                      <li key={`${p.namespace}/${p.pod_name}`} style={{ fontSize: '12px', fontFamily: 'var(--font-mono)' }}>
                        {p.namespace}/{p.pod_name}
                      </li>
                    ))}
                  </ul>
                </td>
                <td style={{ ...styles.td, fontSize: '13px' }}>
                  {c.duration_minutes} min
                </td>
                <td style={{ ...styles.td, fontSize: '12px', color: 'var(--color-text-muted)' }}>
                  {new Date(c.started_at).toLocaleString()}
                </td>
                <td style={styles.td}>
                  {(c.status === 'done' || c.status === 'running') && (
                    <a href={downloadUrl(c.capture_id)} style={styles.dlBtn}>
                      ↓ All pods
                    </a>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

function StatusBadge({ status }) {
  const config = {
    pending: { label: 'Pending',  color: '#f0a84c' },
    running: { label: 'Running',  color: 'var(--color-primary)' },
    done:    { label: 'Done',     color: 'var(--color-success)' },
    error:   { label: 'Error',    color: 'var(--color-danger)' },
  }[status] || { label: status, color: 'var(--color-text-muted)' }

  return (
    <span style={{
      padding: '2px 8px',
      borderRadius: '999px',
      background: `${config.color}22`,
      color: config.color,
      fontWeight: 700,
      fontSize: '11px',
    }}>
      {config.label}
    </span>
  )
}

const styles = {
  page: {
    maxWidth: '1000px',
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
  },
  th: {
    textAlign: 'left',
    padding: '10px 16px',
    fontSize: '11px',
    fontWeight: 700,
    color: 'var(--color-text-muted)',
    textTransform: 'uppercase',
    letterSpacing: '0.05em',
    borderBottom: '1px solid var(--color-border)',
    background: 'var(--color-bg)',
  },
  tr: {
    borderBottom: '1px solid var(--color-border)',
  },
  td: {
    padding: '10px 16px',
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
  dlBtn: {
    fontSize: '12px',
    color: 'var(--color-primary)',
    textDecoration: 'none',
    fontWeight: 600,
  },
}
