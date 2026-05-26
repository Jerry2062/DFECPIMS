/**
 * App.jsx
 *
 * Root component. Manages:
 *   - Auth state (token in localStorage → decoded payload)
 *   - View routing (currentView string, no router library needed)
 *   - Layout: Sidebar + main content area
 */

import { useState, useEffect } from 'react'
import api from './api'

import Sidebar       from './components/Sidebar'
import Login         from './views/Login'
import Dashboard     from './views/Dashboard'
import Cases         from './views/Cases'
import CaseDetail    from './views/CaseDetail'
import AuditLogView  from './views/AuditLogView'
import { Spinner }   from './components/ui'

export default function App() {
  const [user,        setUser]        = useState(null)   // decoded JWT payload
  const [authChecked, setAuthChecked] = useState(false)
  const [view,        setView]        = useState('dashboard')
  const [viewParams,  setViewParams]  = useState({})     // e.g. { caseId }

  /* ── Boot: check for existing token ─────────────────────────────────────── */
  useEffect(() => {
    const payload = api.token.decode()
    if (payload && payload.exp * 1000 > Date.now()) {
      setUser({ user_id: payload.sub, name: payload.name, role: payload.role })
    } else {
      api.token.clear()
    }
    setAuthChecked(true)
  }, [])

  /* ── Navigation ──────────────────────────────────────────────────────────── */
  function navigate(viewName, param) {
    setView(viewName)
    setViewParams(param ? { caseId: param } : {})
    // Scroll to top on view change
    window.scrollTo(0, 0)
  }

  /* ── Login handler ───────────────────────────────────────────────────────── */
  function handleLogin(tokenResponse) {
    setUser({
      user_id: tokenResponse.user_id,
      name:    tokenResponse.name,
      role:    tokenResponse.role,
    })
    setView('dashboard')
  }

  /* ── Loading splash ──────────────────────────────────────────────────────── */
  if (!authChecked) {
    return (
      <div className="min-h-screen bg-terminal-bg flex items-center justify-center">
        <div className="flex items-center gap-3 text-terminal-muted font-mono text-sm">
          <Spinner size="md" />
          <span>Initialising DFECPIMS...</span>
        </div>
      </div>
    )
  }

  /* ── Not logged in ───────────────────────────────────────────────────────── */
  if (!user) {
    return <Login onLogin={handleLogin} />
  }

  /* ── Render current view ─────────────────────────────────────────────────── */
  function renderView() {
    switch (view) {
      case 'dashboard':
        return <Dashboard user={user} onNavigate={navigate} />
      case 'cases':
        return <Cases user={user} onNavigate={navigate} />
      case 'case-detail':
        return <CaseDetail caseId={viewParams.caseId} user={user} onNavigate={navigate} />
      case 'audit':
        return <AuditLogView user={user} />
      default:
        return <Dashboard user={user} onNavigate={navigate} />
    }
  }

  return (
    <div className="flex h-screen overflow-hidden bg-terminal-bg">
      <Sidebar currentView={view} onNavigate={navigate} user={user} />
      <main className="flex-1 overflow-y-auto">
        {renderView()}
      </main>
    </div>
  )
}
