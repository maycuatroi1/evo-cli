import { Handle, Position, type NodeProps, type Node } from '@xyflow/react'
import { RotateCcw, ShieldAlert } from 'lucide-react'
import type { FlowNodeData } from '../lib/layout'
import { TONE_LABEL, ToneIcon } from './ToneIcon'

type Props = NodeProps<Node<FlowNodeData>>

export function DagNode({ data, selected }: Props) {
  const { node } = data
  const meta = node.meta as Record<string, string | number | boolean | undefined>
  const isStep = node.kind === 'step'
  const subtitle = String(meta.what ?? meta.role ?? meta.note ?? '')
  const status = String(meta.status ?? '')

  return (
    <div
      className="dagnode"
      data-tone={node.tone}
      data-selected={selected || undefined}
      data-cycle={node.inCycle || undefined}
      data-kind={node.kind}
    >
      <Handle type="target" position={Position.Left} id="in" />
      <Handle type="target" position={Position.Top} id="back-in" />
      <Handle type="source" position={Position.Top} id="back-out" />
      <div className="dagnode-head">
        <span className={`dagnode-dot tone-${node.tone}`} title={TONE_LABEL[node.tone]}>
          <ToneIcon tone={node.tone} />
        </span>
        <span className={isStep ? 'dagnode-key mono' : 'dagnode-title'}>{node.label}</span>
        {isStep && subtitle ? <span className="dagnode-title">{subtitle}</span> : null}
        {node.inCycle ? (
          <span className="dagnode-flag tone-warn" title="Part of a dependency cycle">
            <RotateCcw size={11} strokeWidth={2.5} aria-hidden />
          </span>
        ) : null}
        {meta.blocking ? (
          <span className="dagnode-flag tone-bad" title="Blocking">
            <ShieldAlert size={11} strokeWidth={2.5} aria-hidden />
          </span>
        ) : null}
      </div>
      <div className="dagnode-foot">
        {status ? <span className={`chip tone-${node.tone}`}>{status}</span> : null}
        {!isStep && subtitle ? <span className="dagnode-sub">{subtitle}</span> : null}
        {isStep && meta.repo ? <span className="dagnode-sub mono">{String(meta.repo)}</span> : null}
        {node.kind === 'external' ? <span className="chip tone-warn">outside manifest</span> : null}
      </div>
      <Handle type="source" position={Position.Right} id="out" />
    </div>
  )
}
