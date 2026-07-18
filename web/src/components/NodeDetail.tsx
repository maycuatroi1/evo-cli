import { ArrowLeft, ArrowRight, X } from 'lucide-react'
import { useMemo } from 'react'
import type { Graph, GraphNode } from '../types'
import { TONE_LABEL, ToneIcon } from './ToneIcon'

const HIDDEN = new Set(['what', 'role', 'index', 'key'])

function value(input: unknown): string {
  if (Array.isArray(input)) return input.join(', ')
  if (typeof input === 'boolean') return input ? 'yes' : 'no'
  return String(input ?? '')
}

export function NodeDetail({
  node,
  graph,
  onClose,
  onSelect,
}: {
  node: GraphNode
  graph: Graph
  onClose: () => void
  onSelect: (id: string) => void
}) {
  const byId = useMemo(() => new Map(graph.nodes.map((n) => [n.id, n])), [graph.nodes])
  const incoming = graph.edges.filter((edge) => edge.target === node.id)
  const outgoing = graph.edges.filter((edge) => edge.source === node.id)
  const headline = String((node.meta as Record<string, unknown>).what ?? '')

  const fields = Object.entries(node.meta).filter(
    ([key, entry]) => !HIDDEN.has(key) && entry !== '' && entry !== null && entry !== undefined && entry !== false,
  )

  return (
    <aside className="detail" aria-label={`Details for ${node.label}`}>
      <header className="detail-head">
        <span className={`tone-${node.tone}`}>
          <ToneIcon tone={node.tone} size={15} />
        </span>
        <h3 className="mono">{node.label}</h3>
        <span className={`chip tone-${node.tone}`}>{String(node.meta.status ?? TONE_LABEL[node.tone])}</span>
        <button className="icon-btn" onClick={onClose} aria-label="Close details">
          <X size={15} aria-hidden />
        </button>
      </header>

      {headline ? <p className="detail-lead">{headline}</p> : null}

      <dl className="detail-fields">
        {fields.map(([key, entry]) => (
          <div key={key}>
            <dt>{key.replace(/_/g, ' ')}</dt>
            <dd className={key === 'verify' || key === 'path' || key === 'source' ? 'mono' : undefined}>{value(entry)}</dd>
          </div>
        ))}
      </dl>

      <div className="detail-links">
        <section>
          <h4>
            <ArrowLeft size={12} aria-hidden /> Must land first ({incoming.length})
          </h4>
          {incoming.length === 0 ? (
            <p className="empty-inline">Nothing. This can start immediately.</p>
          ) : (
            <ul>
              {incoming.map((edge) => (
                <li key={edge.id}>
                  <button className="link mono" onClick={() => onSelect(edge.source)}>
                    {byId.get(edge.source)?.label ?? edge.source}
                  </button>
                  <span className="cell-dim">{edge.label}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
        <section>
          <h4>
            <ArrowRight size={12} aria-hidden /> Waiting on this ({outgoing.length})
          </h4>
          {outgoing.length === 0 ? (
            <p className="empty-inline">Nothing. This is a leaf.</p>
          ) : (
            <ul>
              {outgoing.map((edge) => (
                <li key={edge.id}>
                  <button className="link mono" onClick={() => onSelect(edge.target)}>
                    {byId.get(edge.target)?.label ?? edge.target}
                  </button>
                  <span className="cell-dim">{edge.label}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </aside>
  )
}
