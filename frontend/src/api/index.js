/**
 * api/index.js
 *
 * Central API client for DFECPIMS.
 *
 * All backend communication goes through this module.
 * - Base URL prefix applied automatically
 * - Auth header injected from localStorage token
 * - Non-2xx responses throw a normalized ApiError
 * - File uploads handled with multipart/form-data (no JSON body)
 *
 * Usage:
 *   import api from '../api'
 *   const cases = await api.cases.list({ status: 'Active' })
 *   const token = await api.auth.login({ email, password })
 */

const BASE = '/api/v1'

// ─── Token management ─────────────────────────────────────────────────────────

export const token = {
  get:    ()      => localStorage.getItem('dfecpims_token'),
  set:    (t)     => localStorage.setItem('dfecpims_token', t),
  clear:  ()      => localStorage.removeItem('dfecpims_token'),
  /** Decode JWT payload (not verification — server handles that) */
  decode: () => {
    const t = localStorage.getItem('dfecpims_token')
    if (!t) return null
    try {
      const payload = JSON.parse(atob(t.split('.')[1]))
      return payload
    } catch {
      return null
    }
  },
}

// ─── Error class ──────────────────────────────────────────────────────────────

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail || `HTTP ${status}`)
    this.status = status
    this.detail = detail
  }
}

// ─── Core fetch wrapper ───────────────────────────────────────────────────────

async function request(method, path, { body, params, multipart } = {}) {
  const url = new URL(`${BASE}${path}`, window.location.origin)

  if (params) {
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') {
        url.searchParams.set(k, v)
      }
    })
  }

  const headers = {}
  const tok = token.get()
  if (tok) headers['Authorization'] = `Bearer ${tok}`

  let fetchBody
  if (multipart) {
    // FormData — browser sets Content-Type with boundary automatically
    fetchBody = multipart
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    fetchBody = JSON.stringify(body)
  }

  const res = await fetch(url.toString(), {
    method,
    headers,
    body: fetchBody,
  })

  // 204 No Content
  if (res.status === 204) return null

  // PDF / binary stream — return blob directly
  const contentType = res.headers.get('content-type') || ''
  if (contentType.includes('application/pdf')) {
    if (!res.ok) throw new ApiError(res.status, 'Report generation failed')
    return res.blob()
  }

  const data = await res.json().catch(() => ({ detail: res.statusText }))

  if (!res.ok) {
    const detail = typeof data.detail === 'string'
      ? data.detail
      : JSON.stringify(data.detail)
    throw new ApiError(res.status, detail)
  }

  return data
}

const get    = (path, opts)  => request('GET',    path, opts)
const post   = (path, opts)  => request('POST',   path, opts)
const patch  = (path, opts)  => request('PATCH',  path, opts)

// ─── Auth endpoints ───────────────────────────────────────────────────────────

const auth = {
  login:          (body)   => post('/auth/login', { body }),
  me:             ()       => get('/auth/me'),
  register:       (body)   => post('/auth/register', { body }),
  changePassword: (body)   => post('/auth/change-password', { body }),
  listUsers:      ()       => get('/auth/users'),
}

// ─── Cases endpoints ──────────────────────────────────────────────────────────

const cases = {
  list: (params) => get('/cases', { params }),
  get:  (id)     => get(`/cases/${id}`),
  create: (body) => post('/cases', { body }),
  update: (id, body) => patch(`/cases/${id}`, { body }),
  transition: (id, body) => post(`/cases/${id}/status`, { body }),
  reassign:   (id, body) => post(`/cases/${id}/reassign`, { body }),
}

// ─── Evidence endpoints ───────────────────────────────────────────────────────

const evidence = {
  list: (caseId) => get(`/cases/${caseId}/evidence`),
  get:  (caseId, evId) => get(`/cases/${caseId}/evidence/${evId}`),

  /** Upload a file as evidence. Metadata comes as FormData fields. */
  upload: (caseId, file, metadata) => {
    const form = new FormData()
    form.append('file', file)
    if (metadata.location_description)
      form.append('location_description', metadata.location_description)
    if (metadata.notes)
      form.append('notes', metadata.notes)
    form.append('write_blocker_used', metadata.write_blocker_used ? 'true' : 'false')
    return post(`/cases/${caseId}/evidence`, { multipart: form })
  },
}

// ─── Audit endpoints ──────────────────────────────────────────────────────────

const audit = {
  forCase:  (caseId, params) => get(`/cases/${caseId}/audit`, { params }),
  system:   (params)         => get('/audit', { params }),
}

// ─── Verification endpoints ───────────────────────────────────────────────────

const verification = {
  verifyOne: (caseId, evId) =>
    post(`/cases/${caseId}/evidence/${evId}/verify`),
  verifyAll: (caseId) =>
    post(`/cases/${caseId}/verify-all`),
}

// ─── Reports endpoint ─────────────────────────────────────────────────────────

const reports = {
  /** Returns a Blob — caller creates an object URL and triggers download */
  download: (caseId) => get(`/cases/${caseId}/report`),
}

export default { auth, cases, evidence, audit, verification, reports, token }