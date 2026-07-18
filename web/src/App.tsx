import { Boxes, GitFork, Moon, Radio, RefreshCw, Sun, Waypoints } from 'lucide-react'
import { fetchState, useAsync, useDigest } from './api'
import { useRoute, useTheme } from './lib/route'
import { ClusterView } from './views/Cluster'
import { ContractsView } from './views/Contracts'
import { PlanDetailView } from './views/PlanDetail'
import { PlansView } from './views/Plans'

export function App() {
  const [route, go] = useRoute()
  const [theme, toggleTheme] = useTheme()
  const { digest, live } = useDigest()
  const { data: state, error, loading, reload } = useAsync(fetchState, [digest])

  const NAV = [
    { view: 'cluster', to: '#/', label: 'Cluster', icon: Boxes },
    { view: 'contracts', to: '#/contracts', label: 'Contracts', icon: GitFork },
    { view: 'plans', to: '#/plans', label: 'Plans', icon: Waypoints },
  ] as const

  return (
    <div className="shell">
      <nav className="sidebar" aria-label="Sections">
        <div className="brand">
          <span className="brand-mark" aria-hidden />
          <span className="brand-text">
            <strong>{state?.cluster.name ?? 'harness'}</strong>
            <span className="mono">{state ? `${state.cluster.repos.length} repos` : 'loading'}</span>
          </span>
        </div>

        <ul className="nav">
          {NAV.map((entry) => (
            <li key={entry.view}>
              <a href={entry.to} aria-current={route.view === entry.view ? 'page' : undefined}>
                <entry.icon size={15} aria-hidden />
                {entry.label}
                {entry.view === 'contracts' && state ? <span className="nav-count mono">{state.seams.length}</span> : null}
                {entry.view === 'plans' && state ? <span className="nav-count mono">{state.plans.length}</span> : null}
              </a>
            </li>
          ))}
        </ul>

        {state && state.plans.length > 0 ? (
          <div className="nav-group">
            <h2>Active plans</h2>
            <ul className="nav nav-sub">
              {state.plans
                .filter((plan) => plan.area === 'active')
                .map((plan) => (
                  <li key={plan.id}>
                    <a href={`#/plans/${plan.id}`} aria-current={route.id === plan.id ? 'page' : undefined} title={plan.goal}>
                      <span className="mono nav-plan">{plan.id}</span>
                      <span className={`nav-count mono ${plan.progress.steps_blocking ? 'tone-bad' : ''}`}>
                        {plan.progress.pct}%
                      </span>
                    </a>
                  </li>
                ))}
            </ul>
          </div>
        ) : null}

        <div className="sidebar-foot">
          <span className={live ? 'tone-ok' : 'tone-idle'} title={live ? 'Watching files for changes' : 'Not connected'}>
            <Radio size={12} aria-hidden /> {live ? 'live' : 'offline'}
          </span>
          <span className="mono">read-only</span>
        </div>
      </nav>

      <main className="main">
        <header className="topbar">
          <span className="topbar-path mono">{state?.cluster.root ?? ''}</span>
          <div className="topbar-tools">
            <button className="icon-btn" onClick={reload} aria-label="Reload now" title="Reload now">
              <RefreshCw size={14} className={loading ? 'spin' : undefined} aria-hidden />
            </button>
            <button
              className="icon-btn"
              onClick={toggleTheme}
              aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
              title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
            >
              {theme === 'dark' ? <Sun size={14} aria-hidden /> : <Moon size={14} aria-hidden />}
            </button>
          </div>
        </header>

        <div className="content">
          {error ? (
            <section className="panel">
              <p className="empty tone-bad">{error}</p>
            </section>
          ) : !state ? (
            <section className="panel">
              <p className="empty">Reading the harness...</p>
            </section>
          ) : route.view === 'contracts' ? (
            <ContractsView state={state} selected={route.id} go={go} />
          ) : route.view === 'plans' ? (
            route.id ? (
              <PlanDetailView id={route.id} digest={digest} go={go} />
            ) : (
              <PlansView state={state} go={go} />
            )
          ) : (
            <ClusterView state={state} go={go} />
          )}
        </div>
      </main>
    </div>
  )
}
