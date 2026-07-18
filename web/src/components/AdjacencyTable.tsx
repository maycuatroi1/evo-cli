import { ArrowRight, ChevronDown, ChevronUp } from 'lucide-react'
import { useMemo, useState } from 'react'
import type { Graph, GraphNode } from '../types'
import { TONE_LABEL, ToneIcon } from './ToneIcon'

type SortKey = 'rank' | 'label' | 'tone'

/**
 * A node-link diagram carries no meaning for a screen reader and none in a text copy-paste.
 * This is the same graph as an adjacency list, and it is not optional: every DAG in this app
 * is reachable in both forms.
 */
export function AdjacencyTable({
  graph,
  selected,
  onSelect,
}: {
  graph: Graph
  selected: string | null
  onSelect: (id: string | null) => void
}) {
  const [sort, setSort] = useState<SortKey>('rank')
  const [asc, setAsc] = useState(true)

  const byId = useMemo(() => new Map(graph.nodes.map((n) => [n.id, n])), [graph.nodes])

  const nodes = useMemo(() => {
    const copy = [...graph.nodes]
    copy.sort((a, b) => {
      const value =
        sort === 'rank' ? a.rank - b.rank || a.label.localeCompare(b.label) : String(a[sort]).localeCompare(String(b[sort]))
      return asc ? value : -value
    })
    return copy
  }, [graph.nodes, sort, asc])

  const detail = (node: GraphNode) => {
    const meta = node.meta as Record<string, unknown>
    return String(meta.what ?? meta.role ?? meta.note ?? '')
  }

  const head = (key: SortKey, label: string) => (
    <th aria-sort={sort === key ? (asc ? 'ascending' : 'descending') : 'none'}>
      <button
        className="th-sort"
        onClick={() => (sort === key ? setAsc(!asc) : (setSort(key), setAsc(true)))}
        aria-label={`Sort by ${label}`}
      >
        {label}
        {sort === key ? (asc ? <ChevronUp size={12} aria-hidden /> : <ChevronDown size={12} aria-hidden />) : null}
      </button>
    </th>
  )

  return (
    <div className="tables">
      <section>
        <h3 className="table-title">Nodes ({graph.nodes.length})</h3>
        <div className="table-scroll">
          <table className="grid">
            <caption className="sr-only">
              Every node in the graph, with the layer it sits on. Layer 0 has no prerequisite.
            </caption>
            <thead>
              <tr>
                {head('label', 'Node')}
                {head('tone', 'Status')}
                {head('rank', 'Layer')}
                <th>Detail</th>
              </tr>
            </thead>
            <tbody>
              {nodes.map((node) => (
                <tr
                  key={node.id}
                  tabIndex={0}
                  aria-selected={node.id === selected}
                  data-selected={node.id === selected || undefined}
                  onClick={() => onSelect(node.id === selected ? null : node.id)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault()
                      onSelect(node.id === selected ? null : node.id)
                    }
                  }}
                >
                  <td>
                    <span className="cell-node">
                      <span className={`tone-${node.tone}`}>
                        <ToneIcon tone={node.tone} />
                      </span>
                      <span className="mono">{node.label}</span>
                      {node.inCycle ? <span className="chip tone-warn">cycle</span> : null}
                    </span>
                  </td>
                  <td className={`tone-${node.tone}`}>{String(node.meta.status ?? TONE_LABEL[node.tone])}</td>
                  <td className="mono">{node.rank}</td>
                  <td className="cell-wrap">{detail(node)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section>
        <h3 className="table-title">Dependencies ({graph.edges.length})</h3>
        {graph.edges.length === 0 ? (
          <p className="empty">Nothing depends on anything else here.</p>
        ) : (
          <div className="table-scroll">
            <table className="grid">
              <caption className="sr-only">
                One row per dependency, read as: the left node must land before the right node.
              </caption>
              <thead>
                <tr>
                  <th>Must land first</th>
                  <th>Relation</th>
                  <th>Then</th>
                  <th>Kind</th>
                </tr>
              </thead>
              <tbody>
                {graph.edges.map((edge) => (
                  <tr key={edge.id} data-cycle={edge.inCycle || undefined}>
                    <td>
                      <button className="link mono" onClick={() => onSelect(edge.source)}>
                        {byId.get(edge.source)?.label ?? edge.source}
                      </button>
                    </td>
                    <td className="cell-rel">
                      <ArrowRight size={13} aria-hidden />
                      <span>{edge.label}</span>
                      {edge.inCycle ? <span className="chip tone-warn">in cycle</span> : null}
                    </td>
                    <td>
                      <button className="link mono" onClick={() => onSelect(edge.target)}>
                        {byId.get(edge.target)?.label ?? edge.target}
                      </button>
                    </td>
                    <td className="mono cell-dim">{edge.kind}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}
