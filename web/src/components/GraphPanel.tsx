import { GitBranch, Network, RotateCcw, Rows3, Workflow } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import type { Graph } from '../types'
import { Dag } from './Dag'
import { AdjacencyTable } from './AdjacencyTable'
import { NodeDetail } from './NodeDetail'
import { LevelIcon } from './ToneIcon'

const LEGEND = [
  { tone: 'ok', label: 'done' },
  { tone: 'active', label: 'in progress' },
  { tone: 'warn', label: 'open' },
  { tone: 'bad', label: 'blocked' },
  { tone: 'idle', label: 'not started' },
] as const

export function GraphPanel({
  title,
  hint,
  graph,
  edgeLegend = ['declared', 'inferred'],
  defaultView = 'graph',
}: {
  title: string
  hint?: string
  graph: Graph
  /** What a solid and a dashed edge mean here: seams say blocking/advisory, plans say declared/inferred. */
  edgeLegend?: [string, string]
  defaultView?: 'graph' | 'table'
}) {
  const [view, setView] = useState<'graph' | 'table'>(defaultView)
  const [direction, setDirection] = useState<'LR' | 'TB'>('LR')
  const [selected, setSelected] = useState<string | null>(null)

  useEffect(() => setSelected(null), [graph])

  const node = useMemo(() => graph.nodes.find((n) => n.id === selected) ?? null, [graph.nodes, selected])
  const problems = graph.warnings.filter((w) => w.level !== 'ok')

  if (graph.nodes.length === 0) {
    return (
      <section className="panel">
        <header className="panel-head">
          <h2>{title}</h2>
        </header>
        <p className="empty">No dependency to draw. Nothing in this harness declares one yet.</p>
      </section>
    )
  }

  return (
    <section className="panel panel-graph">
      <header className="panel-head">
        <div className="panel-title">
          <h2>{title}</h2>
          <span className="panel-meta mono">
            {graph.nodes.length} nodes / {graph.edges.length} edges / depth {graph.depth}
          </span>
          <span className={`chip ${graph.acyclic ? 'tone-ok' : 'tone-warn'}`}>
            {graph.acyclic ? 'acyclic' : `${graph.cycles.length} cycle${graph.cycles.length > 1 ? 's' : ''}`}
          </span>
        </div>
        <div className="panel-tools">
          {view === 'graph' ? (
            <button
              className="tool"
              onClick={() => setDirection(direction === 'LR' ? 'TB' : 'LR')}
              title="Switch layout direction"
              aria-label={`Layout is ${direction === 'LR' ? 'left to right' : 'top to bottom'}. Switch it.`}
            >
              {direction === 'LR' ? <Workflow size={14} aria-hidden /> : <GitBranch size={14} aria-hidden />}
              <span>{direction}</span>
            </button>
          ) : null}
          <div className="segmented" role="group" aria-label="How to display the dependency graph">
            <button data-on={view === 'graph' || undefined} onClick={() => setView('graph')} aria-pressed={view === 'graph'}>
              <Network size={14} aria-hidden />
              Graph
            </button>
            <button data-on={view === 'table' || undefined} onClick={() => setView('table')} aria-pressed={view === 'table'}>
              <Rows3 size={14} aria-hidden />
              Table
            </button>
          </div>
        </div>
      </header>

      {hint ? <p className="panel-hint">{hint}</p> : null}

      {problems.length > 0 ? (
        <ul className="warnings">
          {problems.map((warning, index) => (
            <li key={index} className={`tone-${warning.level === 'error' ? 'bad' : warning.level === 'warn' ? 'warn' : 'active'}`}>
              <LevelIcon level={warning.level} />
              <span>{warning.text}</span>
            </li>
          ))}
        </ul>
      ) : null}

      <div className="graph-body">
        <div className="graph-stage" data-dense={graph.nodes.length > 10 || undefined}>
          {view === 'graph' ? (
            <>
              <Dag graph={graph} direction={direction} selected={selected} onSelect={setSelected} />
              <div className="legend" aria-hidden>
                {LEGEND.map((item) => (
                  <span key={item.tone}>
                    <i className={`swatch tone-${item.tone}`} />
                    {item.label}
                  </span>
                ))}
                <span className="legend-sep" />
                <span>
                  <i className="swatch-line" />
                  {edgeLegend[0]}
                </span>
                <span>
                  <i className="swatch-line is-dashed" />
                  {edgeLegend[1]}
                </span>
                {!graph.acyclic ? (
                  <span className="tone-warn">
                    <RotateCcw size={11} aria-hidden />
                    cycle
                  </span>
                ) : null}
              </div>
            </>
          ) : (
            <AdjacencyTable graph={graph} selected={selected} onSelect={setSelected} />
          )}
        </div>
        {node ? <NodeDetail node={node} graph={graph} onClose={() => setSelected(null)} onSelect={setSelected} /> : null}
      </div>
    </section>
  )
}
