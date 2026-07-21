import { AlertTriangle } from 'lucide-react'
import type { Deployment, State } from '../types'

function statusTone(status: string): string {
  const value = status.trim().toLowerCase()
  if (value === 'active') return 'tone-ok'
  if (value === 'provisioning' || value === 'pending') return 'tone-warn'
  if (value === 'suspended' || value === 'retired' || value === 'failed') return 'tone-bad'
  return 'tone-idle'
}

// Mirror of the Python validate_deployments checks, so the dashboard shows the same drift the
// harness.py audit would flag. Returns a human-readable issue for one row, or null.
function rowIssue(dep: Deployment): string | null {
  if (dep.kind === 'tenant') {
    if (!dep.tenantId) return 'tenant deployment has no tenant_id'
    const missing = (['webUrl', 'apiUrl', 'authUrl'] as const).filter((key) => !dep[key])
    if (missing.length) return `missing canonical URL: ${missing.join(', ')}`
  } else if (dep.kind === 'entrypoint' || dep.kind === 'shared') {
    if (dep.tenantId) return `${dep.kind} deployment must not carry a tenant`
  } else {
    return `unknown kind "${dep.kind}"`
  }
  return null
}

function UrlCell({ url }: { url: string }) {
  if (!url) return <td className="cell-dim">-</td>
  return (
    <td className="mono">
      <a href={url} target="_blank" rel="noreferrer">
        {url.replace(/^https?:\/\//, '')}
      </a>
    </td>
  )
}

export function DeploymentsView({ state }: { state: State; selected: string | null; go: (to: string) => void }) {
  const { deployments } = state
  const rows = deployments.deployments

  if (rows.length === 0) {
    return (
      <div className="view">
        <section className="panel">
          <p className="empty">
            This harness has no deployments.yaml, so nothing maps which service runs where. An arriving agent
            has to hardcode or guess every host and environment. Dimension 12 (Deployment topology) scores 0.
          </p>
        </section>
      </div>
    )
  }

  const tenantName = new Map(deployments.tenants.map((tenant) => [tenant.id, tenant.code || tenant.name || tenant.id]))
  const order = deployments.environments.length
    ? deployments.environments
    : Array.from(new Set(rows.map((row) => row.environment)))
  const byEnv = new Map<string, Deployment[]>()
  for (const row of rows) {
    const list = byEnv.get(row.environment) ?? []
    list.push(row)
    byEnv.set(row.environment, list)
  }
  const issues = rows.map(rowIssue).filter(Boolean).length

  return (
    <div className="view">
      <section className="panel">
        <header className="panel-head">
          <div className="panel-title">
            <h2>Deployment topology</h2>
            <span className="panel-meta mono">deployments.yaml</span>
          </div>
          <div className="panel-title">
            <span className="chip">{rows.length} deployments</span>
            <span className="chip">{order.length} environments</span>
            <span className="chip">{deployments.tenants.length} tenants</span>
            {deployments.configVersion ? <span className="chip mono">{deployments.configVersion}</span> : null}
            {issues ? (
              <span className="chip tone-bad">
                <AlertTriangle size={12} aria-hidden /> {issues} issue{issues > 1 ? 's' : ''}
              </span>
            ) : (
              <span className="chip tone-ok">consistent</span>
            )}
          </div>
        </header>
      </section>

      {order
        .filter((env) => byEnv.has(env))
        .map((env) => (
          <section className="panel" key={env}>
            <header className="panel-head">
              <div className="panel-title">
                <h2>{env}</h2>
                <span className="panel-meta mono">{byEnv.get(env)!.length} deployments</span>
              </div>
            </header>
            <div className="table-scroll">
              <table className="grid">
                <thead>
                  <tr>
                    <th>Deployment</th>
                    <th>Product</th>
                    <th>Kind</th>
                    <th>Tenant</th>
                    <th>Status</th>
                    <th>Web</th>
                    <th>API</th>
                    <th>Auth</th>
                    <th>Capabilities</th>
                  </tr>
                </thead>
                <tbody>
                  {byEnv.get(env)!.map((dep) => {
                    const issue = rowIssue(dep)
                    return (
                      <tr key={dep.deploymentId}>
                        <td className="mono">
                          {issue ? (
                            <span className="tone-bad" title={issue}>
                              <AlertTriangle size={12} aria-hidden />{' '}
                            </span>
                          ) : null}
                          {dep.deploymentId}
                        </td>
                        <td className="mono">{dep.product || '-'}</td>
                        <td>
                          <span className={dep.kind === 'tenant' ? 'chip' : 'chip tone-idle'}>{dep.kind || '-'}</span>
                        </td>
                        <td className="mono">{dep.tenantId ? tenantName.get(dep.tenantId) ?? dep.tenantId : '-'}</td>
                        <td>
                          <span className={statusTone(dep.status)}>{dep.status || '-'}</span>
                        </td>
                        <UrlCell url={dep.webUrl} />
                        <UrlCell url={dep.apiUrl} />
                        <UrlCell url={dep.authUrl} />
                        <td className="mono cell-dim">{dep.capabilities.join(', ') || '-'}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </section>
        ))}
    </div>
  )
}
