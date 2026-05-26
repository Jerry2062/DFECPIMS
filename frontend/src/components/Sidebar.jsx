/**
 * components/Sidebar.jsx
 * Left sidebar navigation with system branding and role-aware nav items.
 */

export default function Sidebar({ currentView, onNavigate, user }) {
  const isSupervisor = user?.role === 'supervisor'

  const navItems = [
    { id: 'dashboard', label: 'Dashboard',   icon: '▦', roles: ['investigator','supervisor','readonly'] },
    { id: 'cases',     label: 'Cases',        icon: '◈', roles: ['investigator','supervisor','readonly'] },
    { id: 'audit',     label: 'Audit Log',    icon: '◉', roles: ['supervisor'] },
  ]

  const roleColor = {
    supervisor:  'text-terminal-orange',
    investigator:'text-terminal-blue',
    readonly:    'text-terminal-muted',
  }[user?.role] ?? 'text-terminal-muted'

  return (
    <aside className="w-56 bg-terminal-bg border-r border-terminal-border flex flex-col h-full flex-shrink-0">

      {/* Branding */}
      <div className="px-5 py-5 border-b border-terminal-border">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-terminal-green font-mono font-bold text-lg leading-none">⬡</span>
          <span className="font-mono font-bold text-terminal-text text-sm tracking-wide">DFECPIMS</span>
        </div>
        <p className="text-xs text-terminal-muted font-mono leading-tight pl-6">
          Evidence &amp; Integrity<br/>Management System
        </p>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-3 py-4 space-y-1">
        {navItems
          .filter(item => item.roles.includes(user?.role))
          .map(item => {
            const active = currentView === item.id ||
              (item.id === 'cases' && currentView.startsWith('case'))
            return (
              <button
                key={item.id}
                onClick={() => onNavigate(item.id)}
                className={`
                  w-full flex items-center gap-3 px-3 py-2 rounded text-left text-sm font-mono
                  transition-colors
                  ${active
                    ? 'bg-terminal-accent/20 text-terminal-blue border border-terminal-accent/30'
                    : 'text-terminal-muted hover:text-terminal-text hover:bg-terminal-surface'
                  }
                `}
              >
                <span className={active ? 'text-terminal-blue' : ''}>{item.icon}</span>
                {item.label}
              </button>
            )
          })
        }
      </nav>

      {/* User info */}
      <div className="px-4 py-4 border-t border-terminal-border">
        <div className="mb-1">
          <p className="text-xs text-terminal-text font-medium truncate">{user?.name}</p>
          <p className={`text-xs font-mono ${roleColor}`}>{user?.role}</p>
        </div>
        <button
          onClick={() => {
            localStorage.removeItem('dfecpims_token')
            window.location.reload()
          }}
          className="text-xs text-terminal-muted hover:text-terminal-red font-mono transition-colors mt-1"
        >
          ⏻ logout
        </button>
      </div>
    </aside>
  )
}
