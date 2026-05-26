/**
 * components/ui.jsx
 * Shared, reusable UI primitives.
 */

import { severityClass, statusClass, integrityClass } from '../utils'

/* ─── Spinner ──────────────────────────────────────────────────────────────── */

export function Spinner({ size = 'md', className = '' }) {
  const sz = { sm: 'w-4 h-4', md: 'w-6 h-6', lg: 'w-8 h-8' }[size]
  return (
    <svg
      className={`animate-spin ${sz} ${className}`}
      fill="none" viewBox="0 0 24 24"
    >
      <circle className="opacity-25" cx="12" cy="12" r="10"
        stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor"
        d="M4 12a8 8 0 018-8v8z" />
    </svg>
  )
}

/* ─── Alert ────────────────────────────────────────────────────────────────── */

export function Alert({ type = 'error', children }) {
  const styles = {
    error:   'border-terminal-red/40   bg-terminal-red/10   text-terminal-red',
    warning: 'border-terminal-orange/40 bg-terminal-orange/10 text-terminal-orange',
    success: 'border-terminal-green/40 bg-terminal-green/10 text-terminal-green',
    info:    'border-terminal-blue/40  bg-terminal-blue/10  text-terminal-blue',
  }[type]

  const prefix = { error: '✗', warning: '⚠', success: '✓', info: 'ℹ' }[type]

  return (
    <div className={`border rounded px-4 py-3 font-mono text-sm ${styles} animate-fade-in`}>
      <span className="mr-2 font-bold">{prefix}</span>
      {children}
    </div>
  )
}

/* ─── Empty state ──────────────────────────────────────────────────────────── */

export function Empty({ icon = '◻', message, sub }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-terminal-muted">
      <div className="text-4xl mb-3 opacity-30">{icon}</div>
      <p className="font-mono text-sm">{message}</p>
      {sub && <p className="text-xs mt-1 opacity-60">{sub}</p>}
    </div>
  )
}

/* ─── Badge ────────────────────────────────────────────────────────────────── */

export function Badge({ children, className = '' }) {
  return (
    <span className={`inline-block text-xs font-mono px-2 py-0.5 rounded ${className}`}>
      {children}
    </span>
  )
}

export function SeverityBadge({ value }) {
  return <Badge className={severityClass(value)}>{value}</Badge>
}

export function StatusBadge({ value }) {
  const label = value === 'UnderReview' ? 'UNDER REVIEW' : value.toUpperCase()
  return <Badge className={statusClass(value)}>{label}</Badge>
}

export function IntegrityBadge({ value }) {
  return <Badge className={integrityClass(value)}>{value?.toUpperCase() ?? 'UNKNOWN'}</Badge>
}

/* ─── Section header ───────────────────────────────────────────────────────── */

export function SectionHeader({ children, action }) {
  return (
    <div className="flex items-center justify-between mb-4">
      <h2 className="text-sm font-mono font-bold text-terminal-muted uppercase tracking-widest">
        {children}
      </h2>
      {action}
    </div>
  )
}

/* ─── Modal wrapper ────────────────────────────────────────────────────────── */

export function Modal({ title, onClose, children, width = 'max-w-lg' }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: 'rgba(0,0,0,0.7)' }}
      onClick={e => e.target === e.currentTarget && onClose()}
    >
      <div className={`card w-full ${width} animate-fade-in flex flex-col max-h-[90vh]`}>
        <div className="flex items-center justify-between px-5 py-4 border-b border-terminal-border">
          <h3 className="font-mono text-sm font-bold text-terminal-text">{title}</h3>
          <button onClick={onClose}
            className="text-terminal-muted hover:text-terminal-text text-lg leading-none transition-colors"
          >✕</button>
        </div>
        <div className="overflow-y-auto flex-1">
          {children}
        </div>
      </div>
    </div>
  )
}

/* ─── Field label + value row ──────────────────────────────────────────────── */

export function Field({ label, children }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xs text-terminal-muted font-mono uppercase tracking-wider">{label}</span>
      <div className="text-sm text-terminal-text">{children}</div>
    </div>
  )
}

/* ─── Hash display ─────────────────────────────────────────────────────────── */

export function HashDisplay({ hash, label = 'SHA-256' }) {
  if (!hash) return <span className="text-terminal-muted font-mono text-xs">—</span>
  return (
    <div className="flex flex-col gap-1">
      <span className="text-xs text-terminal-muted font-mono">{label}</span>
      <div className="hash-display bg-terminal-bg border border-terminal-border/60 rounded px-3 py-2 break-all">
        {hash}
      </div>
    </div>
  )
}

/* ─── Page container ───────────────────────────────────────────────────────── */

export function Page({ children, className = '' }) {
  return (
    <div className={`p-6 max-w-7xl mx-auto animate-fade-in ${className}`}>
      {children}
    </div>
  )
}

/* ─── Stat card ────────────────────────────────────────────────────────────── */

export function StatCard({ label, value, sub, accent }) {
  const accents = {
    green:  'border-l-terminal-green',
    red:    'border-l-terminal-red',
    blue:   'border-l-terminal-blue',
    orange: 'border-l-terminal-orange',
    muted:  'border-l-terminal-border',
  }
  return (
    <div className={`card border-l-2 ${accents[accent] ?? accents.muted} px-5 py-4`}>
      <p className="text-xs text-terminal-muted font-mono uppercase tracking-wider mb-1">{label}</p>
      <p className="text-2xl font-mono font-bold text-terminal-text">{value}</p>
      {sub && <p className="text-xs text-terminal-muted mt-1">{sub}</p>}
    </div>
  )
}
