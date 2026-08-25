import type { Tone } from '../types'
import { cn, TONE_TEXT } from '../ui'

export interface Segment {
  tone: Tone
  label: string
  value: number
}

const TONE_FILL: Record<Tone, string> = {
  ok: 'bg-ok',
  active: 'bg-active',
  warn: 'bg-warn',
  bad: 'bg-bad',
  idle: 'bg-idle',
}

export function ProgressStrip({
  segments,
  total,
  label,
  compact = false,
}: {
  segments: Segment[]
  total: number
  label: string
  compact?: boolean
}) {
  const shown = segments.filter((segment) => segment.value > 0)
  const done = segments.find((segment) => segment.tone === 'ok')?.value ?? 0
  const pct = total ? Math.round((done * 100) / total) : 0

  const bar = (
    <span
      className={cn(
        'flex w-full overflow-hidden rounded-[100px] bg-surface-3',
        compact ? 'h-1' : 'h-2',
      )}
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={pct}
      aria-valuetext={`${done} of ${total} done`}
    >
      {total === 0 ? (
        <span className="h-full bg-idle" style={{ width: '100%' }} />
      ) : (
        shown.map((segment) => (
          <span
            key={segment.tone}
            className={cn('h-full transition-[width] duration-200 ease-standard', TONE_FILL[segment.tone])}
            style={{ width: `${(segment.value * 100) / total}%` }}
          />
        ))
      )}
    </span>
  )

  if (compact) return <span className="flex w-full">{bar}</span>

  return (
    <div className="flex min-w-0 flex-col gap-2">
      <p className="m-0 flex items-baseline gap-2">
        <b className="font-mono text-2xl leading-none font-semibold tracking-[-0.02em] tabular-nums">{pct}%</b>
        <span className="font-mono text-xs tabular-nums text-fg-dim">
          {done}/{total} done
        </span>
      </p>
      {bar}
      <ul className="m-0 flex list-none flex-wrap gap-x-3 gap-y-1 p-0 text-[11px] text-fg-dim">
        {shown.map((segment) => (
          <li key={segment.tone} className="inline-flex items-center gap-1.5">
            <i className={cn('h-[9px] w-[9px] shrink-0 rounded-[2px]', TONE_FILL[segment.tone])} aria-hidden />
            <b className="font-mono font-semibold tabular-nums text-fg">{segment.value}</b>
            {segment.label}
          </li>
        ))}
      </ul>
    </div>
  )
}

export function Counter({ label, done, total, tone }: { label: string; done: number; total: number; tone: Tone }) {
  const shown: Tone = total > 0 && done === total ? 'ok' : total === 0 || done === 0 ? 'idle' : tone
  return (
    <span className="inline-flex items-baseline gap-1.5 whitespace-nowrap">
      <span className="text-fg-dim">{label}</span>
      <b className={cn('font-mono font-semibold tabular-nums', TONE_TEXT[shown])}>
        {done}
        <span className="text-fg-dim">/{total}</span>
      </b>
    </span>
  )
}
