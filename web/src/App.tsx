import {
  Boxes,
  GitFork,
  Loader,
  Moon,
  Radio,
  RefreshCw,
  Server,
  Sun,
  TriangleAlert,
  Waypoints,
} from 'lucide-react'
import { fetchState, useAsync, useDigest } from './api'
import { BrandMark } from './components/BrandMark'
import { useRoute, useTheme } from './route'
import { CopyButton, EmptyState, IconButton, Panel, ScrollArea, cn } from './ui'
import { ClusterView } from './views/Cluster'
import { ContractsView } from './views/Contracts'
import { DeploymentsView } from './views/Deployments'
import { PlanDetailView } from './views/PlanDetail'
import { PlansView } from './views/Plans'

const NAV = [
  { view: 'cluster', to: '#/', label: 'Cluster', icon: Boxes },
  { view: 'contracts', to: '#/contracts', label: 'Contracts', icon: GitFork },
  { view: 'deployments', to: '#/deployments', label: 'Deployments', icon: Server },
  { view: 'plans', to: '#/plans', label: 'Plans', icon: Waypoints },
] as const

export function App() {
  const [route, go] = useRoute()
  const [theme, toggleTheme] = useTheme()
  const { digest, live } = useDigest()
  const { data: state, error, loading, reload } = useAsync(fetchState, [digest])

  const activePlans = state ? state.plans.filter((plan) => plan.area === 'active') : []
  const themeLabel = `Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`

  return (
    <div
      data-shell
      className="grid h-full grid-cols-[var(--spacing-sidebar)_minmax(0,1fr)] grid-rows-[minmax(0,1fr)]"
    >
      <nav
        data-sidebar
        aria-label="Sections"
        className="flex min-h-0 flex-col gap-5 overflow-hidden border-r border-border bg-surface px-3 py-4"
      >
        <div className="flex shrink-0 items-center gap-2.5 px-2 pt-1">
          <BrandMark />
          <span className="flex min-w-0 flex-col">
            <strong className="truncate text-sm tracking-[-0.01em]">{state?.cluster.name ?? 'harness'}</strong>
            <span className="truncate font-mono text-xs tabular-nums text-fg-dim">
              {state ? `${state.cluster.repos.length} repos` : 'loading'}
            </span>
          </span>
        </div>

        <ul className="nav shrink-0">
          {NAV.map((entry) => (
            <li key={entry.view}>
              <a href={entry.to} aria-current={route.view === entry.view ? 'page' : undefined}>
                <entry.icon size={15} aria-hidden />
                {entry.label}
                {entry.view === 'contracts' && state ? <span className="nav-count mono">{state.seams.length}</span> : null}
                {entry.view === 'deployments' && state ? (
                  <span className="nav-count mono">{state.deployments.deployments.length}</span>
                ) : null}
                {entry.view === 'plans' && state ? <span className="nav-count mono">{state.plans.length}</span> : null}
              </a>
            </li>
          ))}
        </ul>

        {activePlans.length > 0 ? (
          <section data-sidebar-plans className="nav-group flex min-h-0 flex-1 flex-col">
            <h2 className="shrink-0">Active plans</h2>
            <ScrollArea className="flex-1">
              <ul className="flex flex-col gap-px">
                {activePlans.map((plan) => {
                  const blocking = plan.progress.steps_blocking > 0
                  const pct = plan.progress.pct
                  return (
                    <li key={plan.id}>
                      <a
                        href={`#/plans/${plan.id}`}
                        aria-current={route.id === plan.id ? 'page' : undefined}
                        title={plan.goal}
                        data-plan-row
                        className="relative flex min-h-8 min-w-0 items-center gap-2 overflow-hidden rounded-sm px-2.5 py-1 text-xs text-fg-muted no-underline transition-colors duration-150 ease-standard hover:bg-surface-2 hover:text-fg aria-[current=page]:bg-active-soft aria-[current=page]:text-fg aria-[current=page]:shadow-[inset_2px_0_0_var(--color-active)]"
                      >
                        <span className="min-w-0 flex-1 truncate font-mono text-xs">{plan.id}</span>
                        <span
                          className={cn(
                            'shrink-0 font-mono text-[11px] tabular-nums',
                            blocking ? 'text-bad' : 'text-fg-dim',
                          )}
                        >
                          {pct}%
                        </span>
                        {pct > 0 ? (
                          <span
                            aria-hidden
                            data-plan-meter
                            className={cn(
                              'pointer-events-none absolute bottom-0 left-0 h-px transition-[width] duration-300 ease-standard',
                              blocking ? 'bg-bad' : pct === 100 ? 'bg-ok' : 'bg-active',
                            )}
                            style={{ width: `${pct}%` }}
                          />
                        ) : null}
                      </a>
                    </li>
                  )
                })}
              </ul>
            </ScrollArea>
          </section>
        ) : null}

        <div
          data-sidebar-foot
          className="mt-auto flex shrink-0 items-center justify-between gap-2 border-t border-border px-2.5 pt-2.5 pb-0.5 text-xs text-fg-dim"
        >
          <span
            className={cn('inline-flex items-center gap-1.5', live ? 'text-ok' : 'text-fg-dim')}
            title={live ? 'Watching files for changes' : 'Not connected'}
          >
            <Radio size={12} aria-hidden /> {live ? 'live' : 'offline'}
          </span>
          <span className="font-mono tabular-nums">local edits</span>
        </div>
      </nav>

      <main className="flex h-full min-h-0 min-w-0 flex-col">
        <header
          data-topbar
          className="flex h-topbar shrink-0 items-center justify-between gap-3 border-b border-border bg-bg px-5"
        >
          <span className="truncate font-mono text-xs tabular-nums text-fg-dim">{state?.cluster.root ?? ''}</span>
          <div className="flex shrink-0 items-center gap-1">
            {state ? (
              <CopyButton value={state.cluster.root} label={`Copy harness root: ${state.cluster.root}`} size="md" />
            ) : null}
            <IconButton label="Reload now" title="Reload now" onClick={reload}>
              <RefreshCw size={14} className={loading ? 'animate-spin' : undefined} aria-hidden />
            </IconButton>
            <IconButton label={themeLabel} title={themeLabel} onClick={toggleTheme}>
              {theme === 'dark' ? <Sun size={14} aria-hidden /> : <Moon size={14} aria-hidden />}
            </IconButton>
          </div>
        </header>

        <div data-content className="min-h-0 flex-1 overflow-y-auto p-6">
          {error ? (
            <Panel>
              <EmptyState icon={TriangleAlert} title={error} className="text-bad" />
            </Panel>
          ) : !state ? (
            <Panel>
              <EmptyState icon={Loader} title="Reading the harness..." />
            </Panel>
          ) : route.view === 'contracts' ? (
            <ContractsView state={state} selected={route.id} go={go} />
          ) : route.view === 'deployments' ? (
            <DeploymentsView state={state} selected={route.id} go={go} />
          ) : route.view === 'plans' ? (
            route.id ? (
              <PlanDetailView key={route.id} id={route.id} digest={digest} go={go} />
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
