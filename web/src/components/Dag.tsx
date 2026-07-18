import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
  type Node,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { useEffect, useMemo } from 'react'
import { layout, neighbourhood, type FlowEdgeData, type FlowNodeData } from '../lib/layout'
import type { Graph } from '../types'
import { DagNode } from './DagNode'

const NODE_TYPES = { harness: DagNode }

const MINIMAP_COLOR: Record<string, string> = {
  ok: '#22c55e',
  active: '#38bdf8',
  warn: '#f59e0b',
  bad: '#ef4444',
  idle: '#64748b',
}

interface Props {
  graph: Graph
  direction: 'LR' | 'TB'
  selected: string | null
  onSelect: (id: string | null) => void
}

function Surface({ graph, direction, selected, onSelect }: Props) {
  const { fitView } = useReactFlow()
  const base = useMemo(() => layout(graph, direction), [graph, direction])
  const focus = useMemo(() => neighbourhood(graph, selected), [graph, selected])

  const nodes: Node<FlowNodeData>[] = useMemo(
    () =>
      base.nodes.map((node) => ({
        ...node,
        selected: node.id === selected,
        className: focus && !focus.nodes.has(node.id) ? 'is-dimmed' : undefined,
      })),
    [base.nodes, focus, selected],
  )

  const edges: Edge<FlowEdgeData>[] = useMemo(
    () =>
      base.edges.map((edge) => {
        const lit = focus?.touches(edge.source, edge.target) ?? false
        return {
          ...edge,
          className: focus && !lit ? 'is-dimmed' : undefined,
          animated: lit,
          zIndex: lit ? 10 : undefined,
        }
      }),
    [base.edges, focus],
  )

  // fitView measures node boxes, not edge paths, so back-edge arcs rising above the top row get
  // clipped. Each arc is one stagger step taller than the last, so the headroom scales with them.
  const padding = Math.min(0.16 + base.backCount * 0.055, 0.42)

  useEffect(() => {
    // maxZoom 1 so a small graph is centred at natural size instead of being magnified
    // until three nodes fill the panel.
    const frame = requestAnimationFrame(() => fitView({ padding, duration: 220, maxZoom: 1 }))
    return () => cancelAnimationFrame(frame)
  }, [base.nodes, direction, fitView, padding])

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={NODE_TYPES}
      onNodeClick={(_, node) => onSelect(node.id === selected ? null : node.id)}
      onPaneClick={() => onSelect(null)}
      nodesDraggable
      nodesConnectable={false}
      nodesFocusable
      edgesFocusable={false}
      elementsSelectable
      proOptions={{ hideAttribution: true }}
      minZoom={0.2}
      maxZoom={1.9}
      fitViewOptions={{ padding, maxZoom: 1 }}
      fitView
    >
      <Background variant={BackgroundVariant.Dots} gap={18} size={1} color="var(--border)" />
      <Controls showInteractive={false} fitViewOptions={{ padding, maxZoom: 1 }} />
      {graph.nodes.length > 12 ? (
        <MiniMap
          pannable
          zoomable
          nodeColor={(node) => MINIMAP_COLOR[(node.data as FlowNodeData).node.tone] ?? '#64748b'}
          nodeStrokeWidth={0}
          maskColor="rgba(100, 116, 139, 0.22)"
        />
      ) : null}
    </ReactFlow>
  )
}

export function Dag(props: Props) {
  return (
    <ReactFlowProvider>
      <Surface {...props} />
    </ReactFlowProvider>
  )
}
