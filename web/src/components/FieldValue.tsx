import { Check, Copy, Terminal } from 'lucide-react'
import { useState } from 'react'

export function CopyButton({ text, label, tool = false }: { text: string; label: string; tool?: boolean }) {
  const [copied, setCopied] = useState(false)
  const copy = async (event: React.MouseEvent) => {
    event.stopPropagation()
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1600)
    } catch {
      setCopied(false)
    }
  }
  return (
    <button className={tool ? 'tool' : 'icon-btn'} title={label} aria-label={label} onClick={copy}>
      {copied ? <Check size={13} className="tone-ok" aria-hidden /> : <Copy size={13} aria-hidden />}
      {tool ? (copied ? 'copied' : 'copy') : null}
    </button>
  )
}

const ORDER = [
  'what',
  'aim',
  'issue',
  'criterion',
  'why',
  'where',
  'scope',
  'severity',
  'verify',
  'acceptance',
  'depends_on',
  'depends_on_step',
  'blocked_by',
  'blocks',
  'blocks_step',
  'done_at',
  'fixed_at',
  'answered_at',
  'merged_at',
  'evidence',
  'note',
  'answer',
  'resolution',
  'solution',
]

const LEAD_KEYS = ['what', 'aim', 'issue', 'criterion']
const DEP_KEYS = new Set(['depends_on', 'depends_on_step', 'blocked_by', 'blocks', 'blocks_step'])
const FOLD_KEYS = new Set(['evidence', 'note', 'answer', 'resolution', 'solution'])
const MONO_KEYS = new Set(['where', 'path', 'source', 'branch', 'commit'])

export const STEP_HIDDEN = new Set(['status', 'id', 'order', 'repo', 'title', 'blocking'])
export const ITEM_HIDDEN = new Set(['status', 'id', 'order', 'title'])

function useful(value: unknown): boolean {
  if (value === null || value === undefined || value === '') return false
  if (Array.isArray(value)) return value.length > 0
  return true
}

export function orderFields(raw: Record<string, unknown>, hidden: Set<string>): [string, unknown][] {
  const rank = (name: string) => {
    const at = ORDER.indexOf(name)
    return at < 0 ? ORDER.length : at
  }
  return Object.entries(raw)
    .filter(([name, value]) => !hidden.has(name) && useful(value))
    .sort(([a], [b]) => rank(a) - rank(b))
}

function CommandBlock({ command }: { command: string }) {
  const text = command.trim()
  return (
    <div className="cmd">
      <span className="cmd-label">
        <Terminal size={11} aria-hidden /> verify
      </span>
      <code>{text}</code>
      <CopyButton text={text} label="Copy the verify command" />
    </div>
  )
}

function DepChips({ values, onJump }: { values: unknown; onJump?: (key: string) => void }) {
  const keys = (Array.isArray(values) ? values : [values]).map(String)
  return (
    <span className="dep-chips">
      {keys.map((key) => (
        <button key={key} className="chip dep-chip mono" onClick={() => onJump?.(key)} disabled={!onJump} title={`Go to step ${key}`}>
          {key}
        </button>
      ))}
    </span>
  )
}

function plain(value: unknown): string {
  return Array.isArray(value) ? value.map(String).join(', ') : String(value)
}

export function FieldList({
  raw,
  hidden,
  onJump,
}: {
  raw: Record<string, unknown>
  hidden: Set<string>
  onJump?: (key: string) => void
}) {
  const fields = orderFields(raw, hidden)
  const lead = fields.find(([name]) => LEAD_KEYS.includes(name))
  const verify = fields.find(([name]) => name === 'verify')
  const rest = fields.filter(([name]) => name !== lead?.[0] && name !== 'verify')
  const folded = rest.filter(([name]) => FOLD_KEYS.has(name))
  const rows = rest.filter(([name]) => !FOLD_KEYS.has(name))

  return (
    <>
      {lead ? <p className="field-lead">{plain(lead[1])}</p> : null}
      {verify ? <CommandBlock command={plain(verify[1])} /> : null}
      {rows.length > 0 ? (
        <dl className="fields">
          {rows.map(([name, value]) => (
            <div key={name}>
              <dt>{name.replace(/_/g, ' ')}</dt>
              <dd className={MONO_KEYS.has(name) ? 'mono' : undefined}>
                {DEP_KEYS.has(name) ? <DepChips values={value} onJump={onJump} /> : plain(value)}
              </dd>
            </div>
          ))}
        </dl>
      ) : null}
      {folded.length > 0 ? (
        <div className="folds">
          {folded.map(([name, value]) => (
            <details key={name} className="fold">
              <summary>{name.replace(/_/g, ' ')}</summary>
              <p>{plain(value)}</p>
            </details>
          ))}
        </div>
      ) : null}
    </>
  )
}
