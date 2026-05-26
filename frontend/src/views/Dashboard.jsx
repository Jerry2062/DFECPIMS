/**
 * views/Dashboard.jsx
 * Summary statistics and recent cases overview.
 */

import { useState, useEffect } from 'react'
import api from '../api'
import { StatCard, SectionHeader, Page, Spinner, Alert, SeverityBadge, StatusBadge } from '../components/ui'
import { fmtDate } from '../utils'

export default function Dashboard({ user, onNavigate }) {
  const [cases,   setCases]   = useState(null)
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState(null)

  useEffect(() => {
    api.cases.list({ page_size: 100 })
      .then(d => setCases(d))
      .catch(e => setError(e.detail))
      .finally(() => setLoading(false))
  }, [])

  const stats = cases ? {
    total:       cases.total,
    active:      cases.items.filter(c => c.status === 'Active').length,
    underReview: cases.items.filter(c => c.status === 'UnderReview').length,
    critical:    cases.items.filter(c => c.severity === 'CRITICAL').length,
  } : {}

  const recent = cases?.items?.slice(0, 8) ?? []

  return (
    <Page>
      {/* Header */}
      <div className="mb-8">
        <p className="text-xs font-mono text-terminal-muted uppercase tracking-widest mb-1">
          System Overview
        </p>
        <h1 className="text-xl font-mono font-bold text-terminal-text cursor-blink">
          Dashboard
        </h1>
        <p className="text-sm text-terminal-muted mt-1">
          Welcome back, <span className="text-terminal-blue">{user.name}</span>
        </p>
      </div>

      {loading && (
        <div className="flex items-center gap-3 text-terminal-muted font-mono text-sm">
          <Spinner size="sm" /> Loading system status...
        </div>
      )}
      {error && <Alert type="error">{error}</Alert>}

      {cases && (
        <>
          {/* Stat cards */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
            <StatCard label="Total Cases"   value={stats.total}       accent="muted"  />
            <StatCard label="Active"        value={stats.active}      accent="blue"   />
            <StatCard label="Under Review"  value={stats.underReview} accent="orange" />
            <StatCard label="Critical"      value={stats.critical}    accent="red"    />
          </div>

          {/* Recent cases table */}
          <div className="card overflow-hidden">
            <SectionHeader>
              <span className="px-5 pt-4 block">Recent Cases</span>
            </SectionHeader>

            {recent.length === 0 ? (
              <div className="px-5 pb-5">
                <p className="text-terminal-muted font-mono text-sm">
                  No cases found. Create your first case to get started.
                </p>
              </div>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-terminal-border">
                    {['Case ID','Title','Severity','Status','Investigator','Opened'].map(h => (
                      <th key={h} className="px-4 py-3 text-left text-xs font-mono text-terminal-muted uppercase tracking-wider">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {recent.map((c, i) => (
                    <tr
                      key={c.id}
                      className="border-b border-terminal-border/40 table-row-hover"
                      onClick={() => onNavigate('case-detail', c.id)}
                    >
                      <td className="px-4 py-3 font-mono text-terminal-blue text-xs">{c.id}</td>
                      <td className="px-4 py-3 text-terminal-text max-w-[200px] truncate">{c.title}</td>
                      <td className="px-4 py-3"><SeverityBadge value={c.severity} /></td>
                      <td className="px-4 py-3"><StatusBadge value={c.status} /></td>
                      <td className="px-4 py-3 text-terminal-muted text-xs">{c.investigator?.name ?? '—'}</td>
                      <td className="px-4 py-3 text-terminal-muted text-xs font-mono">{fmtDate(c.created_at).slice(0,10)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}
    </Page>
  )
}
