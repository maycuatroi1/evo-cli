import type { Tone } from '../types'

export interface Segment {
  tone: Tone
  label: string
  value: number
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
      className="pstrip-bar"
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={pct}
      aria-valuetext={`${done} of ${total} done`}
    >
      {total === 0 ? (
        <span className="tone-bg-idle" style={{ width: '100%' }} />
      ) : (
        shown.map((segment) => (
          <span key={segment.tone} className={`tone-bg-${segment.tone}`} style={{ width: `${(segment.value * 100) / total}%` }} />
        ))
      )}
    </span>
  )

  if (compact) return <span className="pstrip is-compact">{bar}</span>

  return (
    <div className="pstrip">
      <p className="pstrip-value">
        <b className="mono">{pct}%</b>
        <span className="cell-dim mono">
          {done}/{total} done
        </span>
      </p>
      {bar}
      <ul className="pstrip-legend plain">
        {shown.map((segment) => (
          <li key={segment.tone}>
            <i className={`swatch tone-${segment.tone}`} aria-hidden />
            <b className="mono">{segment.value}</b>
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
    <div className="counter">
      <span className={`counter-value mono tone-${shown}`}>
        {done}
        <span className="cell-dim">/{total}</span>
      </span>
      <span className="counter-label">{label}</span>
    </div>
  )
}
