import { FileWarning, FolderGit2, GitFork, Scale } from 'lucide-react'
import { GraphPanel } from '../components/GraphPanel'
import type { State } from '../types'

export function ClusterView({ state, go }: { state: State; go: (to: string) => void }) {
  const { cluster, seams, seamGraph, plans } = state
  const missing = cluster.repos.filter((repo) => !repo.present)
  const activePlans = plans.filter((plan) => plan.area === 'active')
  const blocking = activePlans.reduce((total, plan) => total + plan.progress.steps_blocking, 0)

  return (
    <div className="view">
      <div className="stats">
        <Stat icon={<FolderGit2 size={15} />} value={cluster.repos.length} label="repos" sub={missing.length ? `${missing.length} missing` : 'all present'} tone={missing.length ? 'warn' : 'ok'} />
        <Stat icon={<GitFork size={15} />} value={seams.length} label="contract seams" sub={seamGraph.acyclic ? 'acyclic' : `${seamGraph.cycles.length} cycle`} tone={seamGraph.acyclic ? 'ok' : 'warn'} />
        <Stat icon={<Scale size={15} />} value={activePlans.length} label="active plans" sub={blocking ? `${blocking} blocking steps` : 'nothing blocking'} tone={blocking ? 'bad' : 'ok'} />
        <Stat icon={<FileWarning size={15} />} value={cluster.proposals_pending.length} label="pending proposals" sub={cluster.proposals_pending.length ? 'needs triage' : 'queue drained'} tone={cluster.proposals_pending.length ? 'warn' : 'idle'} />
      </div>

      <GraphPanel
        title="Who breaks whom"
        hint="One edge per contract seam, pointing from the repo that owns it to the repo that consumes it. An edge is the direction breakage travels, so it is also the merge order."
        graph={seamGraph}
        edgeLegend={['blocking', 'advisory']}
      />

      <section className="panel">
        <header className="panel-head">
          <div className="panel-title">
            <h2>Repos</h2>
            <span className="panel-meta mono">{cluster.root}</span>
          </div>
        </header>
        <div className="table-scroll">
          <table className="grid">
            <thead>
              <tr>
                <th>Repo</th>
                <th>Role</th>
                <th>Owns</th>
                <th>Consumes</th>
                <th>Note</th>
              </tr>
            </thead>
            <tbody>
              {cluster.repos.map((repo) => {
                const owns = seams.filter((seam) => seam.owner === repo.name)
                const consumes = seams.filter((seam) => seam.consumers.includes(repo.name))
                return (
                  <tr key={repo.name}>
                    <td>
                      <span className="cell-node">
                        <span className={repo.present ? 'tone-ok' : 'tone-bad'}>
                          <FolderGit2 size={13} aria-hidden />
                        </span>
                        <span className="mono">{repo.name}</span>
                        {!repo.present ? <span className="chip tone-bad">not on this machine</span> : null}
                      </span>
                    </td>
                    <td className="cell-dim">{repo.role || '-'}</td>
                    <td>
                      {owns.length === 0 ? (
                        <span className="cell-dim">-</span>
                      ) : (
                        <span className="cell-list">
                          {owns.map((seam) => (
                            <button key={seam.name} className="link mono" onClick={() => go(`#/contracts/${seam.name}`)}>
                              {seam.name}
                            </button>
                          ))}
                        </span>
                      )}
                    </td>
                    <td className="cell-dim mono">{consumes.length || '-'}</td>
                    <td className="cell-wrap cell-dim">{repo.note}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </section>

      {cluster.principles.length > 0 ? (
        <section className="panel">
          <header className="panel-head">
            <div className="panel-title">
              <h2>Principles</h2>
              <span className="panel-meta mono">principles/</span>
            </div>
          </header>
          <div className="principles">
            {cluster.principles.map((principle) => (
              <details key={principle.name}>
                <summary className="mono">{principle.name}</summary>
                <pre>{principle.body}</pre>
              </details>
            ))}
          </div>
        </section>
      ) : null}
    </div>
  )
}

function Stat({
  icon,
  value,
  label,
  sub,
  tone,
}: {
  icon: React.ReactNode
  value: number
  label: string
  sub: string
  tone: 'ok' | 'warn' | 'bad' | 'idle'
}) {
  return (
    <article className="stat">
      <span className={`stat-icon tone-${tone}`}>{icon}</span>
      <span className="stat-value mono">{value}</span>
      <span className="stat-label">{label}</span>
      <span className={`stat-sub tone-${tone}`}>{sub}</span>
    </article>
  )
}
