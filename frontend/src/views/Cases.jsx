/**
 * views/Cases.jsx
 * Paginated, filterable case registry with create-case modal.
 */

import { useState, useEffect, useCallback } from 'react'
import api, { ApiError } from '../api'
import {
  Page, SectionHeader, Spinner, Alert, Empty,
  SeverityBadge, StatusBadge, Modal
} from '../components/ui'
import { fmtDate } from '../utils'

const STATUSES   = ['Active','UnderReview','Archived','Closed']
const SEVERITIES = ['LOW','MEDIUM','HIGH','CRITICAL']

/* ─── Create Case Modal ──────────────────────────────────────────────────────── */

function CreateCaseModal({ onClose, onCreated, user }) {
  const [form, setForm] = useState({
    title: '', description: '', severity: 'MEDIUM', investigator_id: ''
  })
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState(null)

  const isSupervisor = user?.role === 'supervisor'

  function set(k, v) { setForm(f => ({ ...f, [k]: v })) }

  async function handleSubmit(e) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      const payload = {
        title:       form.title.trim(),
        description: form.description.trim() || undefined,
        severity:    form.severity,
      }
      if (isSupervisor && form.investigator_id.trim())
        payload.investigator_id = form.investigator_id.trim()

      const created = await api.cases.create(payload)
      onCreated(created)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to create case.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <Modal title="▦  New Forensic Case" onClose={onClose}>
      <form onSubmit={handleSubmit} className="p-5 space-y-4">
        {error && <Alert type="error">{error}</Alert>}

        <div>
          <label className="block text-xs font-mono text-terminal-muted uppercase tracking-wider mb-1.5">
            Case Title *
          </label>
          <input
            required
            autoFocus
            value={form.title}
            onChange={e => set('title', e.target.value)}
            placeholder="e.g. Ransomware Incident — Finance Server"
            className="terminal-input w-full"
          />
        </div>

        <div>
          <label className="block text-xs font-mono text-terminal-muted uppercase tracking-wider mb-1.5">
            Description
          </label>
          <textarea
            value={form.description}
            onChange={e => set('description', e.target.value)}
            rows={3}
            placeholder="Incident summary, scope, initial observations..."
            className="terminal-input w-full resize-none"
          />
        </div>

        <div>
          <label className="block text-xs font-mono text-terminal-muted uppercase tracking-wider mb-1.5">
            Severity
          </label>
          <select
            value={form.severity}
            onChange={e => set('severity', e.target.value)}
            className="terminal-input w-full"
          >
            {SEVERITIES.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>

        {isSupervisor && (
          <div>
            <label className="block text-xs font-mono text-terminal-muted uppercase tracking-wider mb-1.5">
              Assign Investigator (UUID — leave blank to self-assign)
            </label>
            <input
              value={form.investigator_id}
              onChange={e => set('investigator_id', e.target.value)}
              placeholder="User UUID"
              className="terminal-input w-full"
            />
          </div>
        )}

        <div className="flex gap-3 pt-2">
          <button type="submit" disabled={loading} className="btn-primary flex items-center gap-2">
            {loading ? <><Spinner size="sm" /> Creating...</> : '▦ Create Case'}
          </button>
          <button type="button" onClick={onClose} className="btn-ghost">Cancel</button>
        </div>
      </form>
    </Modal>
  )
}

/* ─── Cases View ─────────────────────────────────────────────────────────────── */

export default function Cases({ user, onNavigate }) {
  const [data,      setData]      = useState(null)
  const [loading,   setLoading]   = useState(true)
  const [error,     setError]     = useState(null)
  const [showCreate, setShowCreate] = useState(false)

  // Filters
  const [search,   setSearch]   = useState('')
  const [status,   setStatus]   = useState('')
  const [severity, setSeverity] = useState('')
  const [page,     setPage]     = useState(1)

  const canCreate = user?.role === 'investigator' || user?.role === 'supervisor'

  const load = useCallback(() => {
    setLoading(true)
    api.cases.list({
      page,
      page_size: 20,
      status:    status   || undefined,
      severity:  severity || undefined,
      search:    search   || undefined,
    })
      .then(setData)
      .catch(e => setError(e.detail))
      .finally(() => setLoading(false))
  }, [page, status, severity, search])

  useEffect(() => { load() }, [load])

  function handleFilterChange(setter) {
    return (v) => { setter(v); setPage(1) }
  }

  return (
    <Page>
      {showCreate && (
        <CreateCaseModal
          user={user}
          onClose={() => setShowCreate(false)}
          onCreated={(c) => { setShowCreate(false); onNavigate('case-detail', c.id) }}
        />
      )}

      {/* Header */}
      <div className="flex items-start justify-between mb-6">
        <div>
          <p className="text-xs font-mono text-terminal-muted uppercase tracking-widest mb-1">
            Case Registry
          </p>
          <h1 className="text-xl font-mono font-bold text-terminal-text">Cases</h1>
        </div>
        {canCreate && (
          <button onClick={() => setShowCreate(true)} className="btn-primary">
            ＋ New Case
          </button>
        )}
      </div>

      {/* Filters */}
      <div className="card p-4 mb-5 flex flex-wrap gap-3">
        <input
          type="text"
          value={search}
          onChange={e => handleFilterChange(setSearch)(e.target.value)}
          placeholder="Search cases..."
          className="terminal-input flex-1 min-w-[180px]"
        />
        <select
          value={status}
          onChange={e => handleFilterChange(setStatus)(e.target.value)}
          className="terminal-input w-36"
        >
          <option value="">All Statuses</option>
          {STATUSES.map(s => <option key={s} value={s}>{s}</option>)}
        </select>
        <select
          value={severity}
          onChange={e => handleFilterChange(setSeverity)(e.target.value)}
          className="terminal-input w-36"
        >
          <option value="">All Severities</option>
          {SEVERITIES.map(s => <option key={s} value={s}>{s}</option>)}
        </select>
      </div>

      {/* Table */}
      {error && <Alert type="error">{error}</Alert>}

      <div className="card overflow-hidden">
        {loading ? (
          <div className="flex items-center gap-3 p-8 text-terminal-muted font-mono text-sm">
            <Spinner size="sm" /> Querying case registry...
          </div>
        ) : data?.items?.length === 0 ? (
          <Empty icon="◈" message="No cases match your filters" sub="Try adjusting the search or status filter" />
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-terminal-border">
                {['Case ID','Title','Severity','Status','Evidence','Investigator','Opened'].map(h => (
                  <th key={h} className="px-4 py-3 text-left text-xs font-mono text-terminal-muted uppercase tracking-wider whitespace-nowrap">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data?.items?.map(c => (
                <tr
                  key={c.id}
                  onClick={() => onNavigate('case-detail', c.id)}
                  className="border-b border-terminal-border/40 table-row-hover"
                >
                  <td className="px-4 py-3 font-mono text-terminal-blue text-xs whitespace-nowrap">{c.id}</td>
                  <td className="px-4 py-3 text-terminal-text max-w-[220px]">
                    <span className="truncate block">{c.title}</span>
                  </td>
                  <td className="px-4 py-3"><SeverityBadge value={c.severity} /></td>
                  <td className="px-4 py-3"><StatusBadge value={c.status} /></td>
                  <td className="px-4 py-3 text-terminal-muted text-xs font-mono">{c.evidence_count ?? 0}</td>
                  <td className="px-4 py-3 text-terminal-muted text-xs truncate max-w-[120px]">
                    {c.investigator?.name ?? '—'}
                  </td>
                  <td className="px-4 py-3 text-terminal-muted text-xs font-mono whitespace-nowrap">
                    {fmtDate(c.created_at).slice(0, 10)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Pagination */}
      {data && data.total_pages > 1 && (
        <div className="flex items-center justify-between mt-4 text-xs font-mono text-terminal-muted">
          <span>Page {data.page} of {data.total_pages} — {data.total} total</span>
          <div className="flex gap-2">
            <button
              disabled={page <= 1}
              onClick={() => setPage(p => p - 1)}
              className="btn-ghost px-3 py-1 text-xs disabled:opacity-30"
            >← Prev</button>
            <button
              disabled={page >= data.total_pages}
              onClick={() => setPage(p => p + 1)}
              className="btn-ghost px-3 py-1 text-xs disabled:opacity-30"
            >Next →</button>
          </div>
        </div>
      )}
    </Page>
  )
}
