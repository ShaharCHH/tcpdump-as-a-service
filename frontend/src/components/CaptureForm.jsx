/**
 * CaptureForm.jsx — Capture configuration form.
 *
 * Lets the user configure:
 *   - Duration (minutes, 1 to MAX from server — we cap client-side at 30)
 *   - Network interface (any, eth0, eth1, ...)
 *   - Packet filters: host, src host, dst host, src port, dst port
 *   - Optional packet count limit
 *
 * The form calls props.onChange(params) every time a field changes so the
 * parent (Dashboard) always has the latest values and doesn't need to read
 * this component's state imperatively.
 *
 * Props:
 *   onChange — ({ duration_minutes, filters }) => void
 */
import React, { useEffect, useState } from 'react'

// Common network interfaces — user can also type a custom value
const COMMON_INTERFACES = ['any', 'eth0', 'eth1', 'ens3', 'lo']

export default function CaptureForm({ onChange }) {
  const [duration, setDuration] = useState(5)
  const [iface, setIface] = useState('any')
  const [customIface, setCustomIface] = useState('')

  // Individual filter fields
  const [host, setHost] = useState('')
  const [srcHost, setSrcHost] = useState('')
  const [dstHost, setDstHost] = useState('')
  const [srcPort, setSrcPort] = useState('')
  const [dstPort, setDstPort] = useState('')
  const [packetCount, setPacketCount] = useState('')

  // Notify parent whenever any value changes
  useEffect(() => {
    const effectiveIface = iface === '__custom__' ? customIface.trim() || 'any' : iface
    onChange({
      duration_minutes: duration,
      filters: {
        host: host.trim() || null,
        src_host: srcHost.trim() || null,
        dst_host: dstHost.trim() || null,
        src_port: srcPort ? parseInt(srcPort, 10) : null,
        dst_port: dstPort ? parseInt(dstPort, 10) : null,
        interface: effectiveIface,
        packet_count: packetCount ? parseInt(packetCount, 10) : null,
      },
    })
  }, [duration, iface, customIface, host, srcHost, dstHost, srcPort, dstPort, packetCount])

  return (
    <div style={styles.form}>
      {/* Row 1: Duration + Interface */}
      <div style={styles.row}>
        <label style={styles.field}>
          <span style={styles.label}>Duration (minutes)</span>
          <input
            type="number"
            min={1}
            max={30}
            value={duration}
            onChange={e => setDuration(Math.max(1, Math.min(30, parseInt(e.target.value) || 1)))}
            style={{ width: '100px' }}
          />
        </label>

        <label style={styles.field}>
          <span style={styles.label}>Interface</span>
          <select value={iface} onChange={e => setIface(e.target.value)}>
            {COMMON_INTERFACES.map(i => <option key={i} value={i}>{i}</option>)}
            <option value="__custom__">Custom...</option>
          </select>
          {iface === '__custom__' && (
            <input
              type="text"
              placeholder="e.g. ens192"
              value={customIface}
              onChange={e => setCustomIface(e.target.value)}
              style={{ marginTop: '6px', width: '120px' }}
            />
          )}
        </label>

        <label style={styles.field}>
          <span style={styles.label}>Packet limit</span>
          <span style={styles.hint}>optional — stops after N packets</span>
          <input
            type="number"
            min={1}
            placeholder="e.g. 1000"
            value={packetCount}
            onChange={e => setPacketCount(e.target.value)}
            style={{ width: '120px' }}
          />
        </label>
      </div>

      {/* Row 2: Filters */}
      <div style={styles.divider}>
        <span style={styles.dividerLabel}>BPF Filters (all fields are optional)</span>
      </div>

      <div style={styles.row}>
        <FilterField label="Host (src or dst)" placeholder="e.g. 10.0.0.1" value={host} onChange={setHost} />
        <FilterField label="Src host"           placeholder="e.g. 192.168.1.10" value={srcHost} onChange={setSrcHost} />
        <FilterField label="Dst host"           placeholder="e.g. 10.96.0.1" value={dstHost} onChange={setDstHost} />
        <FilterField label="Src port"           placeholder="e.g. 8080" value={srcPort} onChange={setSrcPort} type="number" />
        <FilterField label="Dst port"           placeholder="e.g. 443"  value={dstPort} onChange={setDstPort}  type="number" />
      </div>

      {/* Show the resulting BPF expression for user confidence */}
      <BpfPreview filters={{ host, srcHost, dstHost, srcPort, dstPort }} />
    </div>
  )
}

// Small reusable labelled input for each filter field
function FilterField({ label, placeholder, value, onChange, type = 'text' }) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: '4px', minWidth: '150px' }}>
      <span style={{ fontSize: '12px', color: 'var(--color-text-muted)', fontWeight: 600 }}>
        {label}
      </span>
      <input
        type={type}
        placeholder={placeholder}
        value={value}
        onChange={e => onChange(e.target.value)}
        style={{ width: '100%' }}
      />
    </label>
  )
}

// Shows the resulting BPF filter string as a preview
function BpfPreview({ filters }) {
  const parts = []
  if (filters.host)    parts.push(`host ${filters.host}`)
  if (filters.srcHost) parts.push(`src host ${filters.srcHost}`)
  if (filters.dstHost) parts.push(`dst host ${filters.dstHost}`)
  if (filters.srcPort) parts.push(`src port ${filters.srcPort}`)
  if (filters.dstPort) parts.push(`dst port ${filters.dstPort}`)

  const expr = parts.join(' and ') || '(no filter — capture all traffic)'

  return (
    <div style={styles.preview}>
      <span style={styles.previewLabel}>BPF expression: </span>
      <code style={styles.previewCode}>{expr}</code>
    </div>
  )
}

const styles = {
  form: {
    display: 'flex',
    flexDirection: 'column',
    gap: '20px',
  },
  row: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: '20px',
    alignItems: 'flex-start',
  },
  field: {
    display: 'flex',
    flexDirection: 'column',
    gap: '4px',
  },
  label: {
    fontSize: '12px',
    fontWeight: 600,
    color: 'var(--color-text-muted)',
    textTransform: 'uppercase',
    letterSpacing: '0.04em',
  },
  hint: {
    fontSize: '11px',
    color: 'var(--color-text-muted)',
    fontStyle: 'italic',
  },
  divider: {
    borderTop: '1px solid var(--color-border)',
    paddingTop: '4px',
  },
  dividerLabel: {
    fontSize: '11px',
    color: 'var(--color-text-muted)',
    textTransform: 'uppercase',
    letterSpacing: '0.05em',
  },
  preview: {
    background: 'var(--color-terminal-bg)',
    borderRadius: 'var(--radius)',
    padding: '10px 14px',
    fontSize: '12px',
  },
  previewLabel: {
    color: 'var(--color-text-muted)',
    marginRight: '6px',
  },
  previewCode: {
    color: 'var(--color-terminal-text)',
    fontFamily: 'var(--font-mono)',
  },
}
