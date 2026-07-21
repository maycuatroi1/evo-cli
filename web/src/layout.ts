import dagre from '@dagrejs/dagre'
import { MarkerType, Position, type Edge, type Node } from '@xyflow/react'
import type { Graph, GraphEdge, GraphNode } from './types'

const NODE_WIDTH = 216
const NODE_HEIGHT = 68
const TONE_PRIORITY = { idle: 0, ok: 1, active: 2, warn: 3, bad: 4 }
const EDGE_COLOR = {
  ok: 'var(--ok)',
  active: 'var(--active)',
  warn: 'var(--warn)',
  bad: 'var(--bad)',
  idle: 'var(--edge)',
}

export type FlowNodeData = { node: GraphNode; degree: number } & Record<string, unknown>
export type FlowEdgeData = { edges: GraphEdge[] } & Record<string, unknown>

interface LayoutResult {
  nodes: Node<FlowNodeData>[]
  edges: Edge<FlowEdgeData>[]
  backCount: number
}

function groupedEdges(edges: GraphEdge[]): GraphEdge[][] {
  const groups = new Map<string, GraphEdge[]>()
  for (const edge of edges) {
    const key = `${edge.source}\0${edge.target}`
    groups.set(key, [...(groups.get(key) ?? []), edge])
  }
  return [...groups.values()]
}

export function layout(graph: Graph, direction: 'LR' | 'TB' = 'LR'): LayoutResult {
  const dag = new dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}))
  dag.setGraph({
    rankdir: direction,
    ranksep: direction === 'LR' ? 132 : 92,
    nodesep: 30,
    edgesep: 14,
    marginx: 28,
    marginy: 28,
  })

  for (const node of graph.nodes) dag.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT })
  for (const edge of graph.edges) dag.setEdge(edge.source, edge.target)
  dagre.layout(dag)

  const degree = new Map<string, number>()
  for (const edge of graph.edges) {
    degree.set(edge.source, (degree.get(edge.source) ?? 0) + 1)
    degree.set(edge.target, (degree.get(edge.target) ?? 0) + 1)
  }

  const nodes: Node<FlowNodeData>[] = graph.nodes.map((node) => {
    const point = dag.node(node.id)
    return {
      id: node.id,
      type: 'harness',
      position: { x: point.x - NODE_WIDTH / 2, y: point.y - NODE_HEIGHT / 2 },
      data: { node, degree: degree.get(node.id) ?? 0 },
      sourcePosition: direction === 'LR' ? Position.Right : Position.Bottom,
      targetPosition: direction === 'LR' ? Position.Left : Position.Top,
      focusable: true,
    }
  })

  const axis = direction === 'LR' ? 'x' : 'y'
  let backCount = 0
  const edges: Edge<FlowEdgeData>[] = groupedEdges(graph.edges).map((group) => {
    const edge = group[0]
    const inCycle = group.some((item) => item.inCycle)
    const dashed = group.every((item) => item.dashed)
    const tone = group.reduce(
      (strongest, item) => (TONE_PRIORITY[item.tone] > TONE_PRIORITY[strongest] ? item.tone : strongest),
      'idle' as GraphEdge['tone'],
    )
    const color = inCycle ? 'var(--warn)' : EDGE_COLOR[tone]
    const back = dag.node(edge.target)[axis] <= dag.node(edge.source)[axis]
    const offset = back ? 26 + backCount++ * 26 : 0

    return {
      id: `${edge.source}->${edge.target}`,
      source: edge.source,
      target: edge.target,
      sourceHandle: back ? 'back-out' : 'out',
      targetHandle: back ? 'back-in' : 'in',
      type: 'smoothstep',
      pathOptions: back ? { offset, borderRadius: 10 } : { borderRadius: 8 },
      animated: false,
      label: group.length > 1 ? `${edge.label} +${group.length - 1}` : edge.label,
      data: { edges: group },
      style: {
        stroke: color,
        strokeWidth: inCycle ? 2.2 : dashed ? 1.3 : 1.8,
        strokeDasharray: dashed ? '5 4' : undefined,
        opacity: inCycle ? 1 : 0.62,
      },
      markerEnd: { type: MarkerType.ArrowClosed, color, width: 15, height: 15 },
      labelShowBg: true,
      labelBgPadding: [5, 3],
      labelBgBorderRadius: 4,
      labelStyle: { fontSize: 10, fill: 'var(--fg-muted)', fontFamily: 'var(--sans)' },
      labelBgStyle: { fill: 'var(--surface)', fillOpacity: 0.95 },
    }
  })

  return { nodes, edges, backCount }
}

export function neighbourhood(graph: Graph, selected: string | null) {
  if (!selected) return null
  const nodes = new Set([selected])
  for (const edge of graph.edges) {
    if (edge.source === selected || edge.target === selected) {
      nodes.add(edge.source)
      nodes.add(edge.target)
    }
  }
  return {
    nodes,
    touches: (source: string, target: string) => source === selected || target === selected,
  }
}
