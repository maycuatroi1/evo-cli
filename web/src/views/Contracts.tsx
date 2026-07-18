import { ArrowRight, Copy, ShieldAlert, ShieldCheck } from 'lucide-react'
import { useState } from 'react'
import { GraphPanel } from '../components/GraphPanel'
import type { Seam, State } from '../types'

export function ContractsView({ state, selected, go }: { state: State; selected: string | null; go: (to: string) => void }) {
  const { seams, seamGraph } = state
  const current = seams.find((seam) => seam.name === selected) ?? null

  if (seams.length === 0) {
    return (
      <div className="view">
        <section className="panel">
          <p className="empty">
            This harness has no contracts.yaml, so nothing declares who owns what. Seams are what turn a folder of
            repos into a cluster you can reason about.
          </p>
        </section>
      </div>
    )
  }

  return (
    <div className="view">
      <GraphPanel
        title="Seam DAG"
        hint="Solid means blocking: a drift here should stop a merge. Dashed means advisory. An amber loop means two repos own seams the other consumes, so no single merge order satisfies both."
        graph={seamGraph}
        edgeLegend={['blocking', 'advisory']}
      />

      <section className="panel">
        <header className="panel-head">
          <div className="panel-title">
            <h2>Seams</h2>
            <span className="panel-meta mono">contracts.yaml</span>
          </div>
        </header>
        <div className="seams">
          {seams.map((seam) => (
            <SeamCard key={seam.name} seam={seam} open={current?.name === seam.name} onToggle={() => go(current?.name === seam.name ? '#/contracts' : `#/contracts/${seam.name}`)} />
          ))}
        </div>
      </section>
    </div>
  )
}

function SeamCard({ seam, open, onToggle }: { seam: Seam; open: boolean; onToggle: () => void }) {
  const [copied, setCopied] = useState(false)

  const copy = async () => {
    await navigator.clipboard.writeText(seam.verify)
    setCopied(true)
    setTimeout(() => setCopied(false), 1800)
  }

  return (
    <article className="seam" data-open={open || undefined}>
      <button className="seam-head" onClick={onToggle} aria-expanded={open}>
        <span className={seam.blocking ? 'tone-bad' : 'tone-idle'} title={seam.blocking ? 'Blocking' : 'Advisory'}>
          {seam.blocking ? <ShieldAlert size={14} aria-hidden /> : <ShieldCheck size={14} aria-hidden />}
        </span>
        <span className="seam-name mono">{seam.name}</span>
        <span className="chip">{seam.kind}</span>
        <span className="seam-flow mono">
          {seam.owner}
          <ArrowRight size={12} aria-hidden />
          {seam.consumers.join(', ') || '-'}
        </span>
      </button>

      {open ? (
        <div className="seam-body">
          {seam.notes ? <p className="seam-notes">{seam.notes}</p> : null}

          <dl className="detail-fields">
            {seam.source ? (
              <div>
                <dt>source of truth</dt>
                <dd className="mono">{seam.source}</dd>
              </div>
            ) : null}
            {seam.mirrors.length ? (
              <div>
                <dt>mirrors ({seam.mirrors.length})</dt>
                <dd>
                  <ul className="plain mono">
                    {seam.mirrors.map((mirror) => (
                      <li key={mirror}>{mirror}</li>
                    ))}
                  </ul>
                </dd>
              </div>
            ) : null}
            {seam.artifacts.length ? (
              <div>
                <dt>artifacts</dt>
                <dd>
                  <ul className="plain mono">
                    {seam.artifacts.map((artifact) => (
                      <li key={artifact}>{artifact}</li>
                    ))}
                  </ul>
                </dd>
              </div>
            ) : null}
            {seam.remedy ? (
              <div>
                <dt>remedy</dt>
                <dd>{seam.remedy}</dd>
              </div>
            ) : null}
          </dl>

          {seam.keys.length > 0 ? (
            <div className="table-scroll">
              <table className="grid">
                <thead>
                  <tr>
                    <th>Key</th>
                    <th>Consumer</th>
                    <th>Path</th>
                    <th>Access</th>
                  </tr>
                </thead>
                <tbody>
                  {seam.keys.map((entry, index) => (
                    <tr key={index}>
                      <td className="mono">{entry.key ?? '-'}</td>
                      <td className="mono">{entry.consumer ?? '-'}</td>
                      <td className="mono cell-dim">{entry.path ?? '-'}</td>
                      <td className={entry.access === 'read-write' ? 'tone-warn' : 'cell-dim'}>{entry.access ?? '-'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}

          {seam.verify ? (
            <div className="verify">
              <span className="verify-label">verify</span>
              <code>{seam.verify}</code>
              <button className="icon-btn" onClick={copy} aria-label="Copy the verify command">
                <Copy size={13} aria-hidden />
              </button>
              {copied ? <span className="tone-ok" role="status">copied</span> : null}
            </div>
          ) : (
            <p className="empty-inline tone-warn">No verify command. Nothing can prove this seam still holds.</p>
          )}
        </div>
      ) : null}
    </article>
  )
}
