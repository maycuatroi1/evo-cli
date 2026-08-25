import { ChevronDown } from 'lucide-react'
import type { SectionItem } from '../types'
import { CopyButton, FieldList, STEP_HIDDEN } from './FieldValue'
import { ToneIcon } from './ToneIcon'

export type StepState = 'done' | 'ready' | 'blocked' | 'open'

const STATE_LABEL: Record<StepState, string> = {
  done: 'done',
  ready: 'ready now',
  blocked: 'blocked',
  open: 'not started',
}

export function StepRow({
  item,
  itemKey,
  state,
  command,
  open,
  onToggle,
  onJump,
  waiting,
}: {
  item: SectionItem
  itemKey: string
  state: StepState
  command: string | null
  open: boolean
  onToggle: () => void
  onJump?: (key: string) => void
  waiting?: string[]
}) {
  const repo = item.raw.repo ? String(item.raw.repo) : null
  const bodyId = `step-body-${itemKey}`

  return (
    <article className="srow" id={`step-${itemKey}`} data-tone={item.tone} data-state={state} data-open={open || undefined}>
      <div className="srow-bar">
        <button className="srow-head" aria-expanded={open} aria-controls={bodyId} onClick={onToggle}>
          <span className={`srow-icon tone-${item.tone}`}>
            <ToneIcon tone={item.tone} />
          </span>
          <span className="item-key mono">{itemKey}</span>
          <span className="srow-title">{item.title}</span>
          {state === 'ready' ? <span className="chip tone-active srow-ready">ready</span> : null}
          {repo ? <span className="srow-repo mono">{repo}</span> : null}
          {item.status ? <span className={`chip tone-${item.tone}`}>{item.status}</span> : null}
          {item.raw.blocking ? <span className="chip tone-bad">blocking</span> : null}
          <ChevronDown size={14} className="srow-caret" aria-hidden />
          <span className="sr-only">
            {STATE_LABEL[state]}. {open ? 'Collapse' : 'Expand'} step {itemKey}.
          </span>
        </button>
        {command ? <CopyButton text={command} label={`Copy: ${command}`} /> : null}
      </div>

      {open ? (
        <div className="srow-body" id={bodyId}>
          {waiting && waiting.length > 0 ? (
            <p className="srow-waiting tone-warn">
              Waiting on step{waiting.length > 1 ? 's' : ''} {waiting.join(', ')}.
            </p>
          ) : null}
          <FieldList raw={item.raw} hidden={STEP_HIDDEN} onJump={onJump} />
        </div>
      ) : null}
    </article>
  )
}
