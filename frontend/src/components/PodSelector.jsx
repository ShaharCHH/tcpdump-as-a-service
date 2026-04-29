/**
 * PodSelector.jsx — Multi-namespace pod picker.
 *
 * For each selected namespace it fetches the pod list from the hub and renders
 * them in a grouped table. Users can:
 *   - Check individual pods to add them to the capture target list.
 *   - Search/filter by pod name across all namespaces.
 *   - Select a specific container within a pod (if the pod has more than one).
 *
 * Props:
 *   namespaces         — string[] of namespace names to fetch pods from
 *   selectedPods       — [{ namespace, pod_name, container_name? }, ...]
 *   onSelectionChange  — (selectedPods) => void — called whenever selection changes
 */
import React, { useEffect, useState } from 'react'
import { listPods } from '../api'

export default function PodSelector({ namespaces, selectedPods, onSelectionChange }) {
  // Keyed by namespace — { [ns]: Pod[] }
  const [podsByNs, setPodsByNs] = useState({})
  const [loadingNs, setLoadingNs] = useState(new Set())
  const [errors, setErrors] = useState({})

  // Search filter typed by the user
  const [search, setSearch] = useState('')

  // Fetch pods whenever the namespace list changes
  useEffect(() => {
    namespaces.forEach(ns => {
      if (podsByNs[ns] !== undefined) return  // already loaded
      setLoadingNs(prev => new Set([...prev, ns]))
      listPods(ns)
        .then(pods => {
          setPodsByNs(prev => ({ ...prev, [ns]: pods }))
        })
        .catch(err => {
          setErrors(prev => ({ ...prev, [ns]: err.message }))
        })
        .finally(() => {
          setLoadingNs(prev => { const s = new Set(prev); s.delete(ns); return s })
        })
    })
    // Remove namespaces that were deselected
    setPodsByNs(prev => {
      const next = {}
      namespaces.forEach(ns => { if (prev[ns]) next[ns] = prev[ns] })
      return next
    })
  }, [namespaces])

  // Build a unique key for a pod entry (used in the selectedPods array)
  function podKey(ns, name) { return `${ns}/${name}` }

  // Check if a pod is currently selected
  function isSelected(ns, podName) {
    return selectedPods.some(p => p.namespace === ns && p.pod_name === podName)
  }

  // Toggle a pod in/out of the selection
  function togglePod(ns, pod, containerName) {
    if (isSelected(ns, pod.name)) {
      onSelectionChange(selectedPods.filter(
        p => !(p.namespace === ns && p.pod_name === pod.name)
      ))
    } else {
      onSelectionChange([
        ...selectedPods,
        {
          namespace: ns,
          pod_name: pod.name,
          // Only set container_name when the user picked a specific one
          container_name: containerName || null,
        },
      ])
    }
  }

  // Filter pods by search query
  function matches(pod) {
    return !search || pod.name.toLowerCase().includes(search.toLowerCase())
  }

  const totalPods = Object.values(podsByNs).reduce((n, pods) => n + pods.length, 0)

  return (
    <div>
      {/* Search box */}
      <input
        type="search"
        placeholder="Filter pods..."
        value={search}
        onChange={e => setSearch(e.target.value)}
        style={{ marginBottom: '16px', width: '260px' }}
      />

      {totalPods === 0 && loadingNs.size === 0 && (
        <p style={{ color: 'var(--color-text-muted)', fontSize: '13px' }}>
          No pods found in the selected namespaces.
        </p>
      )}

      {/* One section per namespace */}
      {namespaces.map(ns => (
        <div key={ns} style={{ marginBottom: '20px' }}>
          <div style={styles.nsHeader}>
            <span style={styles.nsName}>{ns}</span>
            {loadingNs.has(ns) && <span style={styles.loading}>Loading...</span>}
            {errors[ns] && <span style={styles.error}>{errors[ns]}</span>}
          </div>

          {podsByNs[ns] && (
            <table style={styles.table}>
              <thead>
                <tr>
                  <th style={styles.th}></th>
                  <th style={styles.th}>Pod</th>
                  <th style={styles.th}>Status</th>
                  <th style={styles.th}>Container</th>
                  <th style={styles.th}>Node</th>
                </tr>
              </thead>
              <tbody>
                {podsByNs[ns].filter(matches).map(pod => {
                  const selected = isSelected(ns, pod.name)
                  // Find the selected entry to know which container was picked
                  const entry = selectedPods.find(
                    p => p.namespace === ns && p.pod_name === pod.name
                  )

                  return (
                    <tr
                      key={pod.name}
                      style={{
                        ...styles.tr,
                        background: selected ? 'rgba(79,142,247,0.08)' : 'transparent',
                      }}
                    >
                      {/* Checkbox */}
                      <td style={styles.td}>
                        <input
                          type="checkbox"
                          checked={selected}
                          onChange={() => togglePod(ns, pod, entry?.container_name)}
                        />
                      </td>
                      {/* Pod name */}
                      <td style={{ ...styles.td, fontFamily: 'var(--font-mono)', fontSize: '12px' }}>
                        {pod.name}
                      </td>
                      {/* Status badge */}
                      <td style={styles.td}>
                        <StatusBadge status={pod.status} />
                      </td>
                      {/* Container picker — shown when pod has multiple containers */}
                      <td style={styles.td}>
                        {pod.containers.length > 1 ? (
                          <select
                            value={entry?.container_name || ''}
                            onChange={e => {
                              // Update the container choice for this already-selected pod
                              if (selected) {
                                onSelectionChange(selectedPods.map(p =>
                                  p.namespace === ns && p.pod_name === pod.name
                                    ? { ...p, container_name: e.target.value || null }
                                    : p
                                ))
                              }
                            }}
                            style={{ fontSize: '12px' }}
                            disabled={!selected}
                          >
                            <option value="">any</option>
                            {pod.containers.map(c => (
                              <option key={c} value={c}>{c}</option>
                            ))}
                          </select>
                        ) : (
                          <span style={{ fontSize: '12px', color: 'var(--color-text-muted)' }}>
                            {pod.containers[0] || '—'}
                          </span>
                        )}
                      </td>
                      {/* Node */}
                      <td style={{ ...styles.td, color: 'var(--color-text-muted)', fontSize: '12px' }}>
                        {pod.node_name || '—'}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
        </div>
      ))}
    </div>
  )
}

// Coloured status badge for the pod's phase
function StatusBadge({ status }) {
  const colour = {
    Running:   'var(--color-success)',
    Pending:   '#f0a84c',
    Failed:    'var(--color-danger)',
    Succeeded: 'var(--color-text-muted)',
  }[status] || 'var(--color-text-muted)'

  return (
    <span style={{
      fontSize: '11px',
      padding: '2px 8px',
      borderRadius: '999px',
      background: `${colour}22`,
      color: colour,
      fontWeight: 600,
    }}>
      {status}
    </span>
  )
}

const styles = {
  nsHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: '10px',
    marginBottom: '8px',
  },
  nsName: {
    fontWeight: 600,
    fontSize: '13px',
    color: 'var(--color-primary)',
  },
  loading: {
    fontSize: '12px',
    color: 'var(--color-text-muted)',
  },
  error: {
    fontSize: '12px',
    color: 'var(--color-danger)',
  },
  table: {
    width: '100%',
    borderCollapse: 'collapse',
    fontSize: '13px',
  },
  th: {
    textAlign: 'left',
    padding: '6px 12px',
    color: 'var(--color-text-muted)',
    fontWeight: 600,
    fontSize: '11px',
    textTransform: 'uppercase',
    letterSpacing: '0.05em',
    borderBottom: '1px solid var(--color-border)',
  },
  tr: {
    borderBottom: '1px solid var(--color-border)',
    transition: 'background 0.1s',
  },
  td: {
    padding: '8px 12px',
    verticalAlign: 'middle',
  },
}
