import { ArrowRight, CircleCheckBig, Hourglass } from 'lucide-react'
import { useMemo } from 'react'
import { frontier } from '../frontier'
import type { Graph } from '../types'
import { CopyButton } from './FieldValue'

const SHOWN = 3

export function NextUp({ graph, planId, onPick }: { graph: Graph; planId: string; onPick: (key: string) => void }) {
  const edge = useMemo(() => frontier(graph), [graph])

  if (graph.nodes.length === 0) return null

  if (edge.ready.length === 0 && edge.blocked.length === 0) {
    return (
      <aside className="nextup" data-tone="ok">
        <h2 className="nextup-head tone-ok">
          <CircleCheckBig size={14} aria-hidden /> All steps done
        </h2>
        <p className="nextup-note">Nothing left to pick up. This plan is ready to move to completed.</p>
      </aside>
    )
  }

  if (edge.ready.length === 0) {
    const label = new Map(graph.nodes.map((node) => [node.id, node.label]))
    const blockers = [...new Set(edge.blocked.flatMap((node) => edge.waiting.get(node.id) ?? []))].map(
      (id) => label.get(id) ?? id,
    )
    return (
      <aside className="nextup" data-tone="warn">
        <h2 className="nextup-head tone-warn">
          <Hourglass size={14} aria-hidden /> Nothing is ready
        </h2>
        <p className="nextup-note">
          {edge.blocked.length} step{edge.blocked.length > 1 ? 's are' : ' is'} open
          {blockers.length > 0 ? `, all waiting on step ${blockers.join(', ')}` : ', and every one is marked blocked'}.
        </p>
      </aside>
    )
  }

  const ready = edge.ready.slice(0, SHOWN)
  const more = edge.ready.length - ready.length

  return (
    <aside className="nextup" data-tone="active">
      <h2 className="nextup-head tone-active">
        <ArrowRight size={14} aria-hidden /> Next up
        {edge.ready.length > 1 ? <span className="chip mono">{edge.ready.length} ready</span> : null}
      </h2>
      <ul className="nextup-list plain">
        {ready.map((node) => {
          const key = node.label
          const command = `evo harness step ${planId} ${key} done`
          return (
            <li key={node.id}>
              <button className="nextup-item" onClick={() => onPick(key)} title="Open this step">
                <span className="item-key mono">{key}</span>
                <span className="nextup-title">{String(node.meta.title ?? node.label)}</span>
                {node.meta.repo ? <span className="nextup-repo mono">{String(node.meta.repo)}</span> : null}
              </button>
              <CopyButton text={command} label={`Copy: ${command}`} />
            </li>
          )
        })}
      </ul>
      {more > 0 ? <p className="nextup-note">and {more} more ready.</p> : null}
    </aside>
  )
}
