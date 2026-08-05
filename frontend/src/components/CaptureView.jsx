/**
 * CaptureView.jsx — Live capture page.
 *
 * Reached after the user starts a capture from the Dashboard.
 * URL: /captures/:id
 *
 * Layout:
 *   ┌─────────────────────────────────────────────────────────────┐
 *   │  Capture ID | Status badge | [Stop] [Download All] buttons  │
 *   ├─────────────────────────────────────────────────────────────┤
 *   │  Pod tabs:  [namespace/pod1]  [namespace/pod2]  ...         │
 *   ├─────────────────────────────────────────────────────────────┤
 *   │  Terminal-style live stream for the active tab              │
 *   │  (auto-scrolls to the bottom as new lines arrive)           │
 *   ├─────────────────────────────────────────────────────────────┤
 *   │  [Download .pcap] button for the active pod                 │
 *   └─────────────────────────────────────────────────────────────┘
 *
 * For each pod in the capture we open a separate WebSocket connection.
 * Incoming PACKET_LINE messages are appended to that pod's line buffer.
 * CAPTURE_DONE closes the WebSocket and enables the download button.
 */
import React, { useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { downloadUrl, getCapture, openLiveStream, stopCapture } from '../api'

export default function CaptureView() {
  const { id: captureId } = useParams()

  const [capture, setCapture] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  // Active tab: the pod key currently displayed ("namespace/pod")
  const [activeTab, setActiveTab] = useState(null)

  // Per-pod state: { [podKey]: { lines: string[], done: boolean, error: string } }
  const [podState, setPodState] = useState({})

  // We keep WebSocket refs outside of React state so they aren't re-created on render
  const wsRefs = useRef({})

  // Fetch capture info once on mount
  useEffect(() => {
    getCapture(captureId)
      .then(c => {
        setCapture(c)
        // Default active tab = first pod
        const firstPod = c.pods[0]
        if (firstPod) {
          setActiveTab(`${firstPod.namespace}/${firstPod.pod_name}`)
        }
      })
      .catch(err => setError(err.message))
      .finally(() => setLoading(false))
  }, [captureId])

  // Open a WebSocket for each pod once we have the capture details
  useEffect(() => {
    if (!capture) return

    capture.pods.forEach(pod => {
      const key = `${pod.namespace}/${pod.pod_name}`
      if (wsRefs.current[key]) return  // Already open

      // Initialise per-pod state
      setPodState(prev => ({
        ...prev,
        [key]: prev[key] || { lines: [], done: false, error: '' },
      }))

      const ws = openLiveStream(
        captureId,
        key,
        // onMessage
        (msg) => {
          if (msg.type === 'PACKET_LINE') {
            setPodState(prev => ({
              ...prev,
              [key]: {
                ...prev[key],
                lines: [...(prev[key]?.lines || []), msg.line],
              },
            }))
          } else if (msg.type === 'CAPTURE_DONE') {
            setPodState(prev => ({
              ...prev,
              [key]: { ...(prev[key] || {}), done: true },
            }))
          } else if (msg.type === 'ERROR') {
            setPodState(prev => ({
              ...prev,
              [key]: { ...(prev[key] || {}), done: true, error: msg.message },
            }))
          }
        },
        // onClose
        () => {
          delete wsRefs.current[key]
        },
      )

      wsRefs.current[key] = ws
    })

    // Clean up all WebSockets when the component unmounts
    return () => {
      Object.values(wsRefs.current).forEach(ws => ws.close())
      wsRefs.current = {}
    }
  }, [capture, captureId])

  async function handleStop() {
    if (!captureId) return
    try {
      await stopCapture(captureId)
      setCapture(prev => prev ? { ...prev, status: 'done' } : prev)
    } catch (err) {
      alert('Failed to stop capture: ' + err.message)
    }
  }

  if (loading) return <p style={styles.loading}>Loading capture...</p>
  if (error)   return <p style={styles.error}>{error}</p>
  if (!capture) return null

  const isRunning = capture.status === 'running'
  const podKeys = capture.pods.map(p => `${p.namespace}/${p.pod_name}`)
  const currentPod = podState[activeTab] || { lines: [], done: false, error: '' }

  return (
    <div style={styles.page}>
      {/* Header */}
      <div style={styles.header}>
        <div>
          <h2 style={styles.title}>Capture</h2>
          <code style={styles.captureId}>{captureId}</code>
        </div>
        <div style={styles.headerActions}>
          <StatusBadge status={capture.status} />
          {isRunning && (
            <button onClick={handleStop} style={styles.stopBtn}>Stop</button>
          )}
          <a href={downloadUrl(captureId)} style={styles.dlAllBtn}>
            ↓ Download All
          </a>
        </div>
      </div>

      {/* Pod tabs */}
      <div style={styles.tabs}>
        {podKeys.map(key => {
          const ps = podState[key] || {}
          return (
            <button
              key={key}
              onClick={() => setActiveTab(key)}
              style={{
                ...styles.tab,
                ...(activeTab === key ? styles.tabActive : {}),
              }}
            >
              {key}
              {ps.done && !ps.error && <span style={styles.tabBadgeDone}> ✓</span>}
              {ps.error && <span style={styles.tabBadgeError}> ✕</span>}
            </button>
          )
        })}
      </div>

      {/* Terminal panel for the active pod */}
      {activeTab && (
        <>
          <Terminal lines={currentPod.lines} />
          {currentPod.error && (
            <p style={styles.podError}>{currentPod.error}</p>
          )}
          <div style={styles.podActions}>
            <span style={styles.lineCount}>{currentPod.lines.length} lines</span>
            <a href={downloadUrl(captureId, activeTab)} style={styles.dlBtn}>
              ↓ Download .pcap (this pod)
            </a>
          </div>
        </>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Terminal — auto-scrolling packet stream display
// ---------------------------------------------------------------------------

/**
 * Renders the live packet lines in a terminal-style box.
 * Automatically scrolls to the bottom when new lines arrive.
 *
 * Props:
 *   lines — string[] of decoded tcpdump output lines
 */
function Terminal({ lines }) {
  const bottomRef = useRef(null)

  // Auto-scroll whenever new lines are added
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [lines.length])

  return (
    <div style={styles.terminal}>
      {lines.length === 0
        ? <span style={styles.terminalMuted}>Waiting for packets...</span>
        : lines.map((line, i) => (
            <div key={i} style={styles.terminalLine}>{line}</div>
          ))
      }
      {/* Invisible element at the bottom — used as scroll target */}
      <div ref={bottomRef} />
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
      padding: '4px 12px',
      borderRadius: '999px',
      background: `${config.color}22`,
      color: config.color,
      fontWeight: 700,
      fontSize: '12px',
    }}>
      {config.label}
    </span>
  )
}

const styles = {
  page: {
    maxWidth: '1100px',
    margin: '0 auto',
    padding: '32px 24px',
  },
  header: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    marginBottom: '24px',
  },
  title: {
    fontSize: '20px',
    fontWeight: 700,
    marginBottom: '4px',
  },
  captureId: {
    fontSize: '12px',
    color: 'var(--color-text-muted)',
    fontFamily: 'var(--font-mono)',
  },
  headerActions: {
    display: 'flex',
    gap: '12px',
    alignItems: 'center',
  },
  stopBtn: {
    background: 'var(--color-danger)',
    color: '#fff',
    border: 'none',
    borderRadius: 'var(--radius)',
    padding: '6px 16px',
    fontWeight: 600,
    cursor: 'pointer',
  },
  dlAllBtn: {
    background: 'var(--color-surface)',
    color: 'var(--color-text)',
    border: '1px solid var(--color-border)',
    borderRadius: 'var(--radius)',
    padding: '6px 16px',
    fontWeight: 600,
    fontSize: '13px',
    cursor: 'pointer',
    textDecoration: 'none',
    display: 'inline-block',
  },
  tabs: {
    display: 'flex',
    gap: '4px',
    borderBottom: '2px solid var(--color-border)',
    marginBottom: '0',
    overflowX: 'auto',
  },
  tab: {
    background: 'transparent',
    border: 'none',
    borderBottom: '2px solid transparent',
    color: 'var(--color-text-muted)',
    padding: '8px 16px',
    cursor: 'pointer',
    fontFamily: 'var(--font-mono)',
    fontSize: '12px',
    marginBottom: '-2px',
    whiteSpace: 'nowrap',
  },
  tabActive: {
    color: 'var(--color-primary)',
    borderBottomColor: 'var(--color-primary)',
    fontWeight: 700,
  },
  tabBadgeDone: {
    color: 'var(--color-success)',
    fontFamily: 'var(--font-sans)',
  },
  tabBadgeError: {
    color: 'var(--color-danger)',
    fontFamily: 'var(--font-sans)',
  },
  terminal: {
    background: 'var(--color-terminal-bg)',
    color: 'var(--color-terminal-text)',
    fontFamily: 'var(--font-mono)',
    fontSize: '12px',
    padding: '16px',
    height: '480px',
    overflowY: 'auto',
    borderRadius: '0 0 var(--radius) var(--radius)',
    border: '1px solid var(--color-border)',
    borderTop: 'none',
    lineHeight: '1.5',
  },
  terminalLine: {
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-all',
  },
  terminalMuted: {
    color: 'var(--color-text-muted)',
  },
  podActions: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginTop: '12px',
  },
  lineCount: {
    fontSize: '12px',
    color: 'var(--color-text-muted)',
  },
  dlBtn: {
    background: 'var(--color-surface)',
    color: 'var(--color-text)',
    border: '1px solid var(--color-border)',
    borderRadius: 'var(--radius)',
    padding: '6px 14px',
    fontSize: '12px',
    textDecoration: 'none',
    fontWeight: 600,
    display: 'inline-block',
  },
  podError: {
    color: 'var(--color-danger)',
    fontSize: '13px',
    marginTop: '8px',
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
