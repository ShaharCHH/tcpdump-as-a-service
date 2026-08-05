/**
 * Dashboard.jsx — Main page after login.
 *
 * Layout:
 *   ┌─────────────────────────────────────────────────────┐
 *   │  Step 1: Select namespaces                          │
 *   │  Step 2: Select pods (from chosen namespaces)       │
 *   │  Step 3: Configure capture (filters + duration)     │
 *   │  [Start Capture] button                             │
 *   └─────────────────────────────────────────────────────┘
 *
 * On "Start Capture":
 *   - Sends POST /api/captures to the hub.
 *   - Redirects to /captures/:id so the user sees the live stream.
 */
import React, { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { listNamespaces, startCapture } from '../api'
import PodSelector from './PodSelector'
import CaptureForm from './CaptureForm'

export default function Dashboard() {
  const navigate = useNavigate()

  // All namespaces the user can admin — fetched once on mount
  const [namespaces, setNamespaces] = useState([])
  const [nsLoading, setNsLoading] = useState(true)
  const [nsError, setNsError] = useState('')

  // Namespaces the user has selected (multi-select)
  const [selectedNamespaces, setSelectedNamespaces] = useState([])

  // Pods the user has checked in PodSelector
  const [selectedPods, setSelectedPods] = useState([])

  // Capture parameters from CaptureForm
  const [captureParams, setCaptureParams] = useState(null)

  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState('')

  // Fetch namespaces on mount
  useEffect(() => {
    listNamespaces()
      .then(setNamespaces)
      .catch(err => setNsError(err.message))
      .finally(() => setNsLoading(false))
  }, [])

  async function handleStart() {
    if (!selectedPods.length || !captureParams) return
    setSubmitError('')
    setSubmitting(true)
    try {
      const state = await startCapture(
        selectedPods,
        captureParams.duration_minutes,
        captureParams.filters,
      )
      // Navigate to the live view for this capture
      navigate(`/captures/${state.capture_id}`)
    } catch (err) {
      setSubmitError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  const canStart = selectedPods.length > 0 && captureParams !== null && !submitting

  return (
    <div style={styles.page}>
      <h2 style={styles.heading}>New Capture</h2>

      {/* Step 1: namespace selection */}
      <section style={styles.section}>
        <h3 style={styles.sectionTitle}>1. Select Namespaces</h3>
        {nsLoading && <p style={styles.muted}>Loading namespaces...</p>}
        {nsError  && <p style={styles.error}>{nsError}</p>}
        {!nsLoading && !nsError && (
          <NamespaceSelector
            namespaces={namespaces}
            selected={selectedNamespaces}
            onChange={ns => {
              setSelectedNamespaces(ns)
              // Clear pod selection when namespaces change
              setSelectedPods([])
            }}
          />
        )}
      </section>

      {/* Step 2: pod selection (only shown once at least one namespace is picked) */}
      {selectedNamespaces.length > 0 && (
        <section style={styles.section}>
          <h3 style={styles.sectionTitle}>2. Select Pods</h3>
          <PodSelector
            namespaces={selectedNamespaces}
            selectedPods={selectedPods}
            onSelectionChange={setSelectedPods}
          />
        </section>
      )}

      {/* Step 3: capture settings */}
      {selectedPods.length > 0 && (
        <section style={styles.section}>
          <h3 style={styles.sectionTitle}>3. Configure Capture</h3>
          <CaptureForm onChange={setCaptureParams} />
        </section>
      )}

      {/* Start button */}
      {selectedPods.length > 0 && (
        <div style={styles.actions}>
          {submitError && <p style={styles.error}>{submitError}</p>}
          <div style={styles.summary}>
            {selectedPods.length} pod{selectedPods.length !== 1 ? 's' : ''} selected
          </div>
          <button
            onClick={handleStart}
            disabled={!canStart}
            style={{ ...styles.startBtn, ...(canStart ? {} : styles.startBtnDisabled) }}
          >
            {submitting ? 'Starting...' : 'Start Capture'}
          </button>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// NamespaceSelector — inline component (small enough to live here)
// ---------------------------------------------------------------------------

/**
 * Renders a list of namespace pills with checkboxes.
 * Only namespaces with status="Active" are shown as selectable.
 *
 * Props:
 *   namespaces  — [{ name, status }, ...]
 *   selected    — string[] of selected namespace names
 *   onChange    — (string[]) => void
 */
function NamespaceSelector({ namespaces, selected, onChange }) {
  function toggle(name) {
    if (selected.includes(name)) {
      onChange(selected.filter(n => n !== name))
    } else {
      onChange([...selected, name])
    }
  }

  if (namespaces.length === 0) {
    return <p style={{ color: 'var(--color-text-muted)', fontSize: '13px' }}>
      No namespaces found where you have admin access.
    </p>
  }

  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
      {namespaces.map(ns => {
        const isSelected = selected.includes(ns.name)
        return (
          <button
            key={ns.name}
            onClick={() => toggle(ns.name)}
            style={{
              padding: '6px 14px',
              borderRadius: '999px',
              border: `1px solid ${isSelected ? 'var(--color-primary)' : 'var(--color-border)'}`,
              background: isSelected ? 'var(--color-primary)' : 'var(--color-surface)',
              color: isSelected ? '#fff' : 'var(--color-text)',
              cursor: 'pointer',
              fontSize: '13px',
              fontWeight: isSelected ? 600 : 400,
              transition: 'all 0.15s',
            }}
          >
            {ns.name}
          </button>
        )
      })}
    </div>
  )
}

const styles = {
  page: {
    maxWidth: '900px',
    margin: '0 auto',
    padding: '32px 24px',
  },
  heading: {
    fontSize: '20px',
    fontWeight: 700,
    marginBottom: '28px',
  },
  section: {
    background: 'var(--color-surface)',
    border: '1px solid var(--color-border)',
    borderRadius: 'var(--radius)',
    padding: '24px',
    marginBottom: '16px',
  },
  sectionTitle: {
    fontSize: '14px',
    fontWeight: 600,
    marginBottom: '16px',
    color: 'var(--color-text-muted)',
    textTransform: 'uppercase',
    letterSpacing: '0.05em',
  },
  actions: {
    display: 'flex',
    alignItems: 'center',
    gap: '16px',
    marginTop: '8px',
  },
  summary: {
    fontSize: '13px',
    color: 'var(--color-text-muted)',
  },
  startBtn: {
    background: 'var(--color-primary)',
    color: '#fff',
    border: 'none',
    borderRadius: 'var(--radius)',
    padding: '10px 24px',
    fontWeight: 700,
    fontSize: '14px',
    cursor: 'pointer',
  },
  startBtnDisabled: {
    opacity: 0.5,
    cursor: 'not-allowed',
  },
  muted: {
    color: 'var(--color-text-muted)',
    fontSize: '13px',
  },
  error: {
    color: 'var(--color-danger)',
    fontSize: '13px',
  },
}
