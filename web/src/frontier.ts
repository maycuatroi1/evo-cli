import type { Graph, GraphNode } from './types'

export interface Frontier {
  ready: GraphNode[]
  blocked: GraphNode[]
  waiting: Map<string, string[]>
}

export function frontier(graph: Graph): Frontier {
  const done = new Set(graph.nodes.filter((node) => node.tone === 'ok').map((node) => node.id))
  const waiting = new Map<string, string[]>()

  for (const edge of graph.edges) {
    if (edge.kind !== 'declared' || done.has(edge.source)) continue
    const found = waiting.get(edge.target)
    if (found) found.push(edge.source)
    else waiting.set(edge.target, [edge.source])
  }

  const open = graph.nodes.filter((node) => node.tone !== 'ok')
  const order = (a: GraphNode, b: GraphNode) => a.rank - b.rank || a.label.localeCompare(b.label, undefined, { numeric: true })

  return {
    ready: open.filter((node) => !waiting.has(node.id) && node.tone !== 'bad').sort(order),
    blocked: open.filter((node) => waiting.has(node.id) || node.tone === 'bad').sort(order),
    waiting,
  }
}

export function labelOf(graph: Graph, id: string): string {
  return graph.nodes.find((node) => node.id === id)?.label ?? id
}
