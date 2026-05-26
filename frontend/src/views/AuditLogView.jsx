/**
 * views/AuditLogView.jsx
 * System-wide audit log — supervisor only.
 */

import { useState, useEffect, useCallback } from 'react'
import api from '../api'
import { Page, Spinner, Alert } from '../components/ui'
import { fmtDate, actionColor } from '../utils'

export default function AuditLogView({ user }) {
  const [data,    setData]    = useState(null)
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState(null)
  const [page,    setPage]    = useState(1)
  const [action,  setAction]  = useState('')
  const [actorId, setActorId] = useState('')

  const load = useCallback(() => {
    setLoading(true)
    api.audit.system({
      page,
      page_size: 50,
      action:   action   || undefined,
      actor_id: actorId  || undefined,
    })
      .then(setData)
      .catch(e => setError(e.detail))
      .finally(() => setLoading(false))
  }, [page, action, actorId])

  useEffect(() => { load() }, [load])

  // Supervisors only
  if (user?.role !== 'supervisor') {
    return (
      <Page>
        <Alert type="error">System-wide audit log requires supervisor access.</Alert>
      </Page>
    )
  }

  return (
    <Page>
      <div className="mb-6">
        <p className="text-xs font-mono text-terminal-muted uppercase tracking-widest mb-1">System Audit</p>
        <h1 className="text-xl font-mono font-bold text-terminal-text">Audit Log</h1>
        <p className="text-sm text-terminal-muted mt-1">
          Append-only. DB-level trigger prevents modification or deletion.
        </p>
      </div>

      {/* Filters */}
      <div className="flex gap-3 mb-5 flex-wrap">
        <input
          value={action}
          onChange={e => { setAction(e.target.value); setPage(1) }}
          placeholder="Action code (e.g. HASH_FAILED)"
          className="terminal-input flex-1 min-w-[200px] text-xs"
        />
        <input
          value={actorId}
          onChange={e => { setActorId(e.target.value); setPage(1) }}
          placeholder="Actor UUID"
          className="terminal-input w-64 text-xs"
        />
        <button onClick={load} className="btn-ghost text-xs">↻ Refresh</button>
      </div>

      {error && <Alert type="error">{error}</Alert>}

      {/* Terminal table */}
      <div className="card scanlines bg-terminal-bg overflow-hidden">
        <div className="flex items-center gap-2 px-4 py-2 border-b border-terminal-border bg-terminal-surface">
          <div className="w-2.5 h-2.5 rounded-full bg-terminal-red/60" />
          <div className="w-2.5 h-2.5 rounded-full bg-terminal-orange/60" />
          <div className="w-2.5 h-2.5 rounded-full bg-terminal-green/60" />
          <span className="ml-3 text-xs font-mono text-terminal-muted">
            system_audit_log — INSERT ONLY — {data?.total ?? '…'} entries
          </span>
        </div>

        {loading ? (
          <div className="flex items-center gap-3 p-6 text-terminal-green font-mono text-sm">
            <Spinner size="sm" className="text-terminal-green" /> Querying audit log...
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs font-mono">
              <thead>
                <tr className="border-b border-terminal-border/40">
                  {['Timestamp (UTC)', 'Case', 'Actor', 'Action', 'Detail'].map(h => (
                    <th key={h} className="px-4 py-2 text-left text-terminal-muted whitespace-nowrap">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data?.items?.map((entry, i) => (
                  <tr
                    key={entry.id}
                    className={`border-b border-terminal-border/20 ${i % 2 === 0 ? 'bg-terminal-surface/20' : ''}`}
                  >
                    <td className="px-4 py-1.5 text-terminal-muted whitespace-nowrap">
                      {fmtDate(entry.timestamp).slice(0, 19)}
                    </td>
                    <td className="px-4 py-1.5 text-terminal-blue">
                      {entry.case_id ?? <span className="text-terminal-muted">system</span>}
                    </td>
                    <td className="px-4 py-1.5 text-terminal-text whitespace-nowrap">
                      {entry.actor_name ?? entry.actor_id?.slice(0, 8) ?? 'system'}
                    </td>
                    <td className={`px-4 py-1.5 font-bold whitespace-nowrap ${actionColor(entry.action)}`}>
                      {entry.action}
                    </td>
                    <td className="px-4 py-1.5 text-terminal-muted max-w-xs">
                      <span className="truncate block">
                        {entry.detail
                          ? Object.entries(entry.detail)
                              .filter(([k]) => !['sha256_hash','stored_hash','computed_hash'].includes(k))
                              .filter(([,v]) => v != null)
                              .slice(0, 3)
                              .map(([k,v]) => `${k}=${JSON.stringify(v)}`)
                              .join('  ')
                          : '—'
                        }
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {data && data.total_pages > 1 && (
          <div className="flex items-center justify-between px-4 py-2 border-t border-terminal-border text-xs font-mono text-terminal-muted">
            <span>page {data.page}/{data.total_pages} — {data.total} total entries</span>
            <div className="flex gap-3">
              <button disabled={page <= 1} onClick={() => setPage(p => p - 1)}
                className="hover:text-terminal-text disabled:opacity-30 transition-colors">← prev</button>
              <button disabled={page >= data.total_pages} onClick={() => setPage(p => p + 1)}
                className="hover:text-terminal-text disabled:opacity-30 transition-colors">next →</button>
            </div>
          </div>
        )}
      </div>
    </Page>
  )
}
