/**
 * views/CaseDetail.jsx
 *
 * Full case detail page with three tabs:
 *   Overview   — case metadata, status controls, PDF export
 *   Evidence   — evidence list, upload modal, per-item verify button
 *   Audit Log  — terminal-style append-only event stream
 */

import { useState, useEffect, useCallback } from 'react'
import api, { ApiError } from '../api'
import {
  Page, Spinner, Alert, Modal, Field,
  SeverityBadge, StatusBadge, IntegrityBadge,
  HashDisplay, SectionHeader, Badge
} from '../components/ui'
import { fmtDate, fmtBytes, integrityClass, actionColor, outcomeClass, downloadBlob } from '../utils'

/* ─── Evidence Upload Modal ──────────────────────────────────────────────────── */

function UploadModal({ caseId, onClose, onUploaded }) {
  const [file,      setFile]      = useState(null)
  const [location,  setLocation]  = useState('')
  const [notes,     setNotes]     = useState('')
  const [blocker,   setBlocker]   = useState(false)
  const [loading,   setLoading]   = useState(false)
  const [error,     setError]     = useState(null)
  const [result,    setResult]    = useState(null)

  async function handleSubmit(e) {
    e.preventDefault()
    if (!file) return
    setError(null)
    setLoading(true)
    try {
      const res = await api.evidence.upload(caseId, file, {
        location_description: location,
        notes,
        write_blocker_used: blocker,
      })
      setResult(res)
      onUploaded(res.evidence)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Upload failed.')
    } finally {
      setLoading(false)
    }
  }

  if (result) {
    return (
      <Modal title="◈  Evidence Ingested" onClose={onClose}>
        <div className="p-5 space-y-4">
          <Alert type="success">
            {result.evidence.id} ingested successfully. Hash computed and stored.
          </Alert>
          <HashDisplay hash={result.evidence.sha256_hash} label="SHA-256 (computed server-side at ingestion)" />
          <div className="grid grid-cols-2 gap-3 text-sm">
            <Field label="Evidence ID"><span className="font-mono text-terminal-blue">{result.evidence.id}</span></Field>
            <Field label="File Size">{fmtBytes(result.evidence.file_size_bytes)}</Field>
            <Field label="MIME Type"><span className="font-mono text-xs">{result.evidence.file_type ?? '—'}</span></Field>
            <Field label="Write Blocker">{result.evidence.write_blocker_used ? '✓ YES' : '✗ NO'}</Field>
          </div>
          <button onClick={onClose} className="btn-primary w-full">Done</button>
        </div>
      </Modal>
    )
  }

  return (
    <Modal title="◈  Ingest Evidence File" onClose={onClose}>
      <form onSubmit={handleSubmit} className="p-5 space-y-4">
        {error && <Alert type="error">{error}</Alert>}

        <div>
          <label className="block text-xs font-mono text-terminal-muted uppercase tracking-wider mb-1.5">
            Evidence File *
          </label>
          <input
            type="file"
            required
            onChange={e => setFile(e.target.files[0])}
            className="block w-full text-sm text-terminal-muted font-mono
              file:mr-3 file:py-1.5 file:px-3 file:rounded
              file:border file:border-terminal-border file:bg-terminal-surface
              file:text-terminal-text file:text-xs file:font-mono
              file:cursor-pointer file:transition-colors
              hover:file:border-terminal-blue"
          />
          {file && (
            <p className="text-xs text-terminal-muted font-mono mt-1">
              {file.name} — {fmtBytes(file.size)}
            </p>
          )}
        </div>

        <div>
          <label className="block text-xs font-mono text-terminal-muted uppercase tracking-wider mb-1.5">
            Acquisition Location
          </label>
          <input
            value={location}
            onChange={e => setLocation(e.target.value)}
            placeholder="e.g. Seized HDD bay 2 — workstation ACCT-04"
            className="terminal-input w-full"
          />
        </div>

        <div>
          <label className="block text-xs font-mono text-terminal-muted uppercase tracking-wider mb-1.5">
            Notes
          </label>
          <textarea
            value={notes}
            onChange={e => setNotes(e.target.value)}
            rows={2}
            placeholder="Investigator notes about this evidence item..."
            className="terminal-input w-full resize-none"
          />
        </div>

        <label className="flex items-center gap-3 cursor-pointer">
          <input
            type="checkbox"
            checked={blocker}
            onChange={e => setBlocker(e.target.checked)}
            className="w-4 h-4 accent-terminal-green"
          />
          <span className="text-sm text-terminal-text">
            Write blocker was used during acquisition
          </span>
        </label>

        <div className="bg-terminal-bg border border-terminal-border/60 rounded px-3 py-2 text-xs font-mono text-terminal-muted">
          ℹ SHA-256 hash will be computed server-side at ingestion. Client-supplied hashes are never accepted.
        </div>

        <div className="flex gap-3">
          <button type="submit" disabled={loading || !file} className="btn-primary flex items-center gap-2">
            {loading ? <><Spinner size="sm" /> Ingesting...</> : '◈ Ingest File'}
          </button>
          <button type="button" onClick={onClose} className="btn-ghost">Cancel</button>
        </div>
      </form>
    </Modal>
  )
}

/* ─── Evidence Tab ───────────────────────────────────────────────────────────── */

function EvidenceTab({ caseId, user, caseStatus }) {
  const [items,     setItems]     = useState(null)
  const [loading,   setLoading]   = useState(true)
  const [error,     setError]     = useState(null)
  const [showUpload, setShowUpload] = useState(false)
  const [verifying, setVerifying] = useState({})
  const [verResults, setVerResults] = useState({})
  const [bulkLoading, setBulkLoading] = useState(false)
  const [bulkResult,  setBulkResult]  = useState(null)

  const canWrite = user?.role === 'investigator' || user?.role === 'supervisor'
  const isTerminal = caseStatus === 'Archived' || caseStatus === 'Closed'

  const load = useCallback(() => {
    setLoading(true)
    api.evidence.list(caseId)
      .then(d => setItems(d.items))
      .catch(e => setError(e.detail))
      .finally(() => setLoading(false))
  }, [caseId])

  useEffect(() => { load() }, [load])

  async function verifyOne(evId) {
    setVerifying(v => ({ ...v, [evId]: true }))
    try {
      const res = await api.verification.verifyOne(caseId, evId)
      setVerResults(r => ({ ...r, [evId]: res }))
      // Refresh to update integrity_status in the list
      load()
    } catch (e) {
      setVerResults(r => ({ ...r, [evId]: { error: e.detail } }))
    } finally {
      setVerifying(v => ({ ...v, [evId]: false }))
    }
  }

  async function verifyAll() {
    setBulkLoading(true)
    setBulkResult(null)
    try {
      const res = await api.verification.verifyAll(caseId)
      setBulkResult(res)
      load()
    } catch (e) {
      setBulkResult({ error: e.detail })
    } finally {
      setBulkLoading(false)
    }
  }

  if (loading) return (
    <div className="flex items-center gap-3 p-8 text-terminal-muted font-mono text-sm">
      <Spinner size="sm" /> Loading evidence...
    </div>
  )

  return (
    <div className="p-5 space-y-4">
      {showUpload && (
        <UploadModal
          caseId={caseId}
          onClose={() => setShowUpload(false)}
          onUploaded={() => { setShowUpload(false); load() }}
        />
      )}

      {error && <Alert type="error">{error}</Alert>}

      {/* Toolbar */}
      <div className="flex items-center gap-3 flex-wrap">
        {canWrite && !isTerminal && (
          <button onClick={() => setShowUpload(true)} className="btn-primary">
            ◈ Upload Evidence
          </button>
        )}
        {canWrite && items?.length > 0 && (
          <button onClick={verifyAll} disabled={bulkLoading} className="btn-ghost flex items-center gap-2">
            {bulkLoading ? <><Spinner size="sm" /> Verifying all...</> : '⊕ Verify All Hashes'}
          </button>
        )}
      </div>

      {/* Bulk result banner */}
      {bulkResult && !bulkResult.error && (
        <Alert type={bulkResult.all_passed ? 'success' : 'error'}>
          Bulk verification: {bulkResult.verified_count}/{bulkResult.total_items} passed
          {bulkResult.mismatch_count > 0 && ` — ${bulkResult.mismatch_count} MISMATCH(ES) DETECTED`}
          {bulkResult.missing_count > 0 && ` — ${bulkResult.missing_count} FILE(S) MISSING`}
        </Alert>
      )}
      {bulkResult?.error && <Alert type="error">{bulkResult.error}</Alert>}

      {/* Evidence cards */}
      {items?.length === 0 ? (
        <div className="text-terminal-muted font-mono text-sm py-8 text-center">
          No evidence items yet.{canWrite && !isTerminal ? ' Upload the first file above.' : ''}
        </div>
      ) : (
        <div className="space-y-3">
          {items?.map(ev => {
            const vr = verResults[ev.id]
            return (
              <div key={ev.id} className="card overflow-hidden">
                {/* Evidence header */}
                <div className="flex items-center gap-3 px-4 py-3 bg-terminal-bg border-b border-terminal-border">
                  <span className="font-mono text-terminal-blue text-xs font-bold">{ev.id}</span>
                  <span className="text-terminal-text text-sm font-medium flex-1 truncate">{ev.filename}</span>
                  <span className="text-terminal-muted text-xs font-mono">{fmtBytes(ev.file_size_bytes)}</span>
                  <IntegrityBadge value={ev.integrity_status} />
                </div>

                {/* Hash */}
                <div className="px-4 py-3 border-b border-terminal-border/40">
                  <p className="text-xs font-mono text-terminal-muted mb-1">SHA-256 (ingestion hash — ground truth)</p>
                  <p className="hash-display break-all">{ev.sha256_hash}</p>
                </div>

                {/* Metadata grid */}
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4 px-4 py-3 border-b border-terminal-border/40 text-xs">
                  <Field label="Acquired By">{ev.acquired_by_user?.name ?? '—'}</Field>
                  <Field label="Acquired At"><span className="font-mono">{fmtDate(ev.acquired_at).slice(0,16)}</span></Field>
                  <Field label="Write Blocker">
                    <span className={ev.write_blocker_used ? 'text-terminal-green' : 'text-terminal-muted'}>
                      {ev.write_blocker_used ? '✓ Yes' : '✗ No'}
                    </span>
                  </Field>
                  <Field label="Last Verified">
                    <span className="font-mono">{ev.last_verified_at ? fmtDate(ev.last_verified_at).slice(0,16) : 'Never'}</span>
                  </Field>
                </div>

                {(ev.location_description || ev.notes) && (
                  <div className="px-4 py-2 border-b border-terminal-border/40 text-xs text-terminal-muted">
                    {ev.location_description && <p><span className="text-terminal-text">Location:</span> {ev.location_description}</p>}
                    {ev.notes && <p className="mt-1"><span className="text-terminal-text">Notes:</span> {ev.notes}</p>}
                  </div>
                )}

                {/* Verify button + result */}
                {canWrite && (
                  <div className="px-4 py-3 flex items-center gap-4 flex-wrap">
                    <button
                      onClick={() => verifyOne(ev.id)}
                      disabled={verifying[ev.id]}
                      className="btn-ghost text-xs flex items-center gap-2"
                    >
                      {verifying[ev.id] ? <><Spinner size="sm" /> Verifying...</> : '⊕ Verify Hash'}
                    </button>

                    {vr && !vr.error && (
                      <div className={`text-xs font-mono ${outcomeClass(vr.outcome)}`}>
                        {vr.outcome} — {fmtDate(vr.verified_at).slice(0,16)} UTC
                        {vr.outcome !== 'VERIFIED' && (
                          <div className="mt-1 text-terminal-red">{vr.verdict.slice(0, 100)}</div>
                        )}
                      </div>
                    )}
                    {vr?.error && <span className="text-terminal-red text-xs font-mono">{vr.error}</span>}
                  </div>
                )}

                {/* Mismatch alert */}
                {ev.integrity_status === 'failed' && (
                  <div className="px-4 pb-3">
                    <Alert type="error">
                      Integrity failure detected — SHA-256 mismatch or file missing. This evidence may have been tampered with.
                    </Alert>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

/* ─── Audit Tab ──────────────────────────────────────────────────────────────── */

function AuditTab({ caseId }) {
  const [data,    setData]    = useState(null)
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState(null)
  const [page,    setPage]    = useState(1)
  const [action,  setAction]  = useState('')

  const load = useCallback(() => {
    setLoading(true)
    api.audit.forCase(caseId, { page, page_size: 50, action: action || undefined })
      .then(setData)
      .catch(e => setError(e.detail))
      .finally(() => setLoading(false))
  }, [caseId, page, action])

  useEffect(() => { load() }, [load])

  return (
    <div className="p-5 space-y-4">
      {error && <Alert type="error">{error}</Alert>}

      {/* Filters */}
      <div className="flex gap-3">
        <input
          value={action}
          onChange={e => { setAction(e.target.value); setPage(1) }}
          placeholder="Filter by action code (e.g. HASH_FAILED)"
          className="terminal-input flex-1 text-xs"
        />
      </div>

      {/* Terminal log */}
      <div className="card scanlines bg-terminal-bg overflow-hidden">
        {/* Terminal header bar */}
        <div className="flex items-center gap-2 px-4 py-2 border-b border-terminal-border bg-terminal-surface">
          <div className="w-2.5 h-2.5 rounded-full bg-terminal-red/60" />
          <div className="w-2.5 h-2.5 rounded-full bg-terminal-orange/60" />
          <div className="w-2.5 h-2.5 rounded-full bg-terminal-green/60" />
          <span className="ml-3 text-xs font-mono text-terminal-muted">
            audit_log — {caseId} — append-only
          </span>
          {data && (
            <span className="ml-auto text-xs font-mono text-terminal-muted">
              {data.total} entries
            </span>
          )}
        </div>

        {loading ? (
          <div className="flex items-center gap-3 p-6 text-terminal-green font-mono text-sm">
            <Spinner size="sm" className="text-terminal-green" /> Fetching audit records...
          </div>
        ) : data?.items?.length === 0 ? (
          <div className="p-6 text-terminal-muted font-mono text-sm">
            $ — no audit entries match the current filter
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs font-mono">
              <thead>
                <tr className="border-b border-terminal-border/40">
                  <th className="px-4 py-2 text-left text-terminal-muted whitespace-nowrap">Timestamp (UTC)</th>
                  <th className="px-4 py-2 text-left text-terminal-muted">Actor</th>
                  <th className="px-4 py-2 text-left text-terminal-muted">Action</th>
                  <th className="px-4 py-2 text-left text-terminal-muted">Detail</th>
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
                    <td className="px-4 py-1.5 text-terminal-text whitespace-nowrap">
                      {entry.actor_name ?? entry.actor_id?.slice(0, 8) ?? 'system'}
                    </td>
                    <td className={`px-4 py-1.5 font-bold whitespace-nowrap ${actionColor(entry.action)}`}>
                      {entry.action}
                    </td>
                    <td className="px-4 py-1.5 text-terminal-muted max-w-[320px]">
                      <span className="truncate block">
                        {entry.detail
                          ? Object.entries(entry.detail)
                              .filter(([k]) => !['sha256_hash','stored_hash','computed_hash','storage_path'].includes(k))
                              .filter(([,v]) => v != null)
                              .slice(0, 4)
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

        {/* Pagination */}
        {data && data.total_pages > 1 && (
          <div className="flex items-center justify-between px-4 py-2 border-t border-terminal-border text-xs font-mono text-terminal-muted">
            <span>page {data.page}/{data.total_pages}</span>
            <div className="flex gap-2">
              <button disabled={page <= 1} onClick={() => setPage(p => p - 1)}
                className="hover:text-terminal-text disabled:opacity-30 transition-colors">← prev</button>
              <button disabled={page >= data.total_pages} onClick={() => setPage(p => p + 1)}
                className="hover:text-terminal-text disabled:opacity-30 transition-colors">next →</button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

/* ─── Overview Tab ───────────────────────────────────────────────────────────── */

function OverviewTab({ caseData, user, onRefresh, onNavigate }) {
  const [statusModal,  setStatusModal]  = useState(false)
  const [newStatus,    setNewStatus]    = useState('')
  const [reason,       setReason]       = useState('')
  const [statusLoading, setStatusLoading] = useState(false)
  const [statusError,  setStatusError]  = useState(null)
  const [pdfLoading,   setPdfLoading]   = useState(false)
  const [pdfError,     setPdfError]     = useState(null)

  const isSupervisor = user?.role === 'supervisor'
  const canWrite     = user?.role !== 'readonly'

  const TRANSITIONS = {
    Active:      ['UnderReview','Closed'],
    UnderReview: ['Active','Archived','Closed'],
    Archived:    [],
    Closed:      [],
  }
  const allowed = TRANSITIONS[caseData.status] ?? []

  async function applyTransition() {
    if (!newStatus) return
    setStatusLoading(true)
    setStatusError(null)
    try {
      await api.cases.transition(caseData.id, { new_status: newStatus, reason: reason || undefined })
      setStatusModal(false)
      setReason('')
      setNewStatus('')
      onRefresh()
    } catch (e) {
      setStatusError(e.detail)
    } finally {
      setStatusLoading(false)
    }
  }

  async function downloadPdf() {
    setPdfLoading(true)
    setPdfError(null)
    try {
      const blob = await api.reports.download(caseData.id)
      downloadBlob(blob, `DFECPIMS-${caseData.id}-ChainOfCustody.pdf`)
    } catch (e) {
      setPdfError(e.detail)
    } finally {
      setPdfLoading(false)
    }
  }

  return (
    <div className="p-5 space-y-6">
      {/* Status transition modal */}
      {statusModal && (
        <Modal title="Transition Case Status" onClose={() => setStatusModal(false)}>
          <div className="p-5 space-y-4">
            {statusError && <Alert type="error">{statusError}</Alert>}
            <div>
              <label className="block text-xs font-mono text-terminal-muted uppercase tracking-wider mb-1.5">
                New Status
              </label>
              <select
                value={newStatus}
                onChange={e => setNewStatus(e.target.value)}
                className="terminal-input w-full"
              >
                <option value="">Select target status</option>
                {allowed.map(s => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs font-mono text-terminal-muted uppercase tracking-wider mb-1.5">
                Reason (optional)
              </label>
              <input
                value={reason}
                onChange={e => setReason(e.target.value)}
                placeholder="Reason for status change..."
                className="terminal-input w-full"
              />
            </div>
            <div className="flex gap-3">
              <button onClick={applyTransition} disabled={!newStatus || statusLoading} className="btn-primary flex items-center gap-2">
                {statusLoading ? <><Spinner size="sm" /> Applying...</> : 'Apply'}
              </button>
              <button onClick={() => setStatusModal(false)} className="btn-ghost">Cancel</button>
            </div>
          </div>
        </Modal>
      )}

      {/* Metadata grid */}
      <div className="grid grid-cols-2 md:grid-cols-3 gap-5">
        <Field label="Case ID">
          <span className="font-mono text-terminal-blue">{caseData.id}</span>
        </Field>
        <Field label="Status"><StatusBadge value={caseData.status} /></Field>
        <Field label="Severity"><SeverityBadge value={caseData.severity} /></Field>
        <Field label="Investigator">{caseData.investigator?.name ?? '—'}</Field>
        <Field label="Opened"><span className="font-mono text-xs">{fmtDate(caseData.created_at)}</span></Field>
        <Field label="Last Updated"><span className="font-mono text-xs">{fmtDate(caseData.updated_at)}</span></Field>
      </div>

      {caseData.description && (
        <div>
          <p className="text-xs font-mono text-terminal-muted uppercase tracking-wider mb-2">Description</p>
          <p className="text-sm text-terminal-text leading-relaxed bg-terminal-surface border border-terminal-border rounded p-4">
            {caseData.description}
          </p>
        </div>
      )}

      {/* Actions */}
      {(canWrite || isSupervisor) && (
        <div>
          <p className="text-xs font-mono text-terminal-muted uppercase tracking-wider mb-3">Actions</p>
          <div className="flex flex-wrap gap-3">
            {canWrite && allowed.length > 0 && (
              <button onClick={() => setStatusModal(true)} className="btn-ghost">
                ⇄ Transition Status
              </button>
            )}
            {isSupervisor && (
              <button
                onClick={downloadPdf}
                disabled={pdfLoading}
                className="btn-primary flex items-center gap-2"
              >
                {pdfLoading ? <><Spinner size="sm" /> Generating PDF...</> : '⬇ Export Chain-of-Custody PDF'}
              </button>
            )}
          </div>
          {pdfError && <div className="mt-3"><Alert type="error">{pdfError}</Alert></div>}
        </div>
      )}
    </div>
  )
}

/* ─── Case Detail (main) ─────────────────────────────────────────────────────── */

const TABS = ['Overview', 'Evidence', 'Audit Log']

export default function CaseDetail({ caseId, user, onNavigate }) {
  const [caseData, setCaseData] = useState(null)
  const [loading,  setLoading]  = useState(true)
  const [error,    setError]    = useState(null)
  const [tab,      setTab]      = useState('Overview')

  const load = useCallback(() => {
    setLoading(true)
    api.cases.get(caseId)
      .then(setCaseData)
      .catch(e => setError(e.detail))
      .finally(() => setLoading(false))
  }, [caseId])

  useEffect(() => { load() }, [load])

  if (loading) return (
    <Page>
      <div className="flex items-center gap-3 text-terminal-muted font-mono text-sm">
        <Spinner /> Loading case...
      </div>
    </Page>
  )

  if (error) return (
    <Page><Alert type="error">{error}</Alert></Page>
  )

  return (
    <div className="flex flex-col h-full animate-fade-in">
      {/* Header */}
      <div className="px-6 pt-6 pb-0 border-b border-terminal-border bg-terminal-surface/30">
        <button
          onClick={() => onNavigate('cases')}
          className="text-xs font-mono text-terminal-muted hover:text-terminal-blue transition-colors mb-3 block"
        >
          ← Back to Cases
        </button>

        <div className="flex items-start justify-between mb-4">
          <div>
            <p className="font-mono text-terminal-blue text-xs mb-1">{caseData?.id}</p>
            <h1 className="text-lg font-mono font-bold text-terminal-text">{caseData?.title}</h1>
          </div>
          <div className="flex gap-2">
            {caseData && <SeverityBadge value={caseData.severity} />}
            {caseData && <StatusBadge value={caseData.status} />}
          </div>
        </div>

        {/* Tabs */}
        <div className="flex gap-0">
          {TABS.map(t => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`
                px-4 py-2 text-xs font-mono border-b-2 transition-colors
                ${tab === t
                  ? 'border-terminal-blue text-terminal-blue'
                  : 'border-transparent text-terminal-muted hover:text-terminal-text'
                }
              `}
            >
              {t}
            </button>
          ))}
        </div>
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-y-auto">
        {tab === 'Overview' && caseData && (
          <OverviewTab caseData={caseData} user={user} onRefresh={load} onNavigate={onNavigate} />
        )}
        {tab === 'Evidence' && caseData && (
          <EvidenceTab caseId={caseId} user={user} caseStatus={caseData.status} />
        )}
        {tab === 'Audit Log' && (
          <AuditTab caseId={caseId} />
        )}
      </div>
    </div>
  )
}
