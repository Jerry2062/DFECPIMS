/**
 * views/Login.jsx
 * Full-page login form — only shown when no valid token exists.
 */

import { useState } from 'react'
import api, { ApiError } from '../api'
import { Spinner, Alert } from '../components/ui'

export default function Login({ onLogin }) {
  const [email,    setEmail]    = useState('')
  const [password, setPassword] = useState('')
  const [loading,  setLoading]  = useState(false)
  const [error,    setError]    = useState(null)

  async function handleSubmit(e) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      const res = await api.auth.login({ email, password })
      api.token.set(res.access_token)
      onLogin(res)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Login failed.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-terminal-bg flex items-center justify-center p-6">
      {/* Background grid texture */}
      <div className="fixed inset-0 pointer-events-none opacity-[0.03]"
        style={{
          backgroundImage: 'linear-gradient(#3fb950 1px, transparent 1px), linear-gradient(90deg, #3fb950 1px, transparent 1px)',
          backgroundSize: '40px 40px'
        }}
      />

      <div className="w-full max-w-sm relative">
        {/* Logo */}
        <div className="text-center mb-10">
          <div className="inline-flex items-center justify-center w-14 h-14 rounded-full
            border-2 border-terminal-green/40 bg-terminal-green/10 mb-4">
            <span className="text-terminal-green font-mono font-bold text-2xl">⬡</span>
          </div>
          <h1 className="font-mono font-bold text-terminal-text text-xl tracking-wide">DFECPIMS</h1>
          <p className="text-terminal-muted text-xs font-mono mt-1">
            Digital Forensics Evidence &amp; Process Integrity
          </p>
        </div>

        {/* Form card */}
        <div className="card border-terminal-border/80 p-6">
          <div className="flex items-center gap-2 mb-6">
            <div className="w-1.5 h-1.5 rounded-full bg-terminal-green animate-pulse" />
            <span className="font-mono text-xs text-terminal-muted uppercase tracking-widest">
              Secure System Access
            </span>
          </div>

          {error && <div className="mb-4"><Alert type="error">{error}</Alert></div>}

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-xs font-mono text-terminal-muted uppercase tracking-wider mb-1.5">
                Email
              </label>
              <input
                type="email"
                required
                autoFocus
                value={email}
                onChange={e => setEmail(e.target.value)}
                placeholder="investigator@agency.gov"
                className="terminal-input w-full"
              />
            </div>

            <div>
              <label className="block text-xs font-mono text-terminal-muted uppercase tracking-wider mb-1.5">
                Password
              </label>
              <input
                type="password"
                required
                value={password}
                onChange={e => setPassword(e.target.value)}
                placeholder="••••••••"
                className="terminal-input w-full"
              />
            </div>

            <button
              type="submit"
              disabled={loading}
              className="btn-primary w-full flex items-center justify-center gap-2 py-2.5 mt-2"
            >
              {loading ? (
                <><Spinner size="sm" /> Authenticating...</>
              ) : (
                <>→ Authenticate</>
              )}
            </button>
          </form>
        </div>

        <p className="text-center text-xs text-terminal-muted font-mono mt-6 opacity-50">
          All sessions are logged. Unauthorised access is prohibited.
        </p>
      </div>
    </div>
  )
}
