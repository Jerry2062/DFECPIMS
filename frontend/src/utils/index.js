/**
 * utils/index.js
 * Shared formatting and helper utilities.
 */

/** Format bytes to human-readable string */
export function fmtBytes(n) {
  if (n == null) return '—'
  if (n < 1024)        return `${n} B`
  if (n < 1024 ** 2)   return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 ** 3)   return `${(n / 1024 ** 2).toFixed(1)} MB`
  return `${(n / 1024 ** 3).toFixed(2)} GB`
}

/** Format ISO datetime to readable UTC string */
export function fmtDate(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  return d.toISOString().replace('T', ' ').slice(0, 19) + ' UTC'
}

/** Shorten a UUID for display */
export function shortId(uuid) {
  if (!uuid) return '—'
  return uuid.slice(0, 8) + '...'
}

/** Truncate a hash for inline display — returns first+last 8 chars */
export function shortHash(hash) {
  if (!hash) return '—'
  if (hash.length <= 20) return hash
  return hash.slice(0, 8) + '…' + hash.slice(-8)
}

/** Severity → Tailwind badge class */
export function severityClass(sev) {
  return {
    LOW:      'badge-low',
    MEDIUM:   'badge-medium',
    HIGH:     'badge-high',
    CRITICAL: 'badge-critical',
  }[sev] ?? 'badge-medium'
}

/** Status → Tailwind badge class */
export function statusClass(st) {
  return {
    Active:      'badge-active',
    UnderReview: 'badge-underreview',
    Archived:    'badge-archived',
    Closed:      'badge-closed',
  }[st] ?? 'badge-active'
}

/** Integrity status → Tailwind badge class */
export function integrityClass(st) {
  return {
    verified: 'badge-verified',
    failed:   'badge-failed',
    pending:  'badge-pending',
  }[st] ?? 'badge-pending'
}

/** Verification outcome → colour class */
export function outcomeClass(outcome) {
  return {
    VERIFIED:      'text-terminal-green',
    HASH_MISMATCH: 'text-terminal-red',
    FILE_MISSING:  'text-terminal-orange',
  }[outcome] ?? 'text-terminal-muted'
}

/** Audit action → colour class for terminal rendering */
export function actionColor(action) {
  if (!action) return 'text-terminal-muted'
  if (action.includes('FAIL') || action.includes('MISSING')) return 'text-terminal-red'
  if (action.includes('VERIFIED') || action.includes('LOGIN') && !action.includes('FAIL'))
    return 'text-terminal-green'
  if (action.includes('CREATED') || action.includes('UPLOADED')) return 'text-terminal-blue'
  if (action.includes('STATUS') || action.includes('REASSIGN') || action.includes('UPDATED'))
    return 'text-terminal-orange'
  if (action.includes('REPORT') || action.includes('EXPORT')) return 'text-terminal-purple'
  return 'text-terminal-muted'
}

/** Download a Blob as a file */
export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob)
  const a   = document.createElement('a')
  a.href     = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}
