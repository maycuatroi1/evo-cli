import { Meter as BaseMeter } from '@base-ui-components/react/meter'
import type { Tone } from '../types'
import { cn } from './cn'

const TONE_FILL: Record<Tone, string> = {
  ok: 'bg-ok',
  active: 'bg-active',
  warn: 'bg-warn',
  bad: 'bg-bad',
  idle: 'bg-idle',
}

export interface MeterProps {
  value: number
  max?: number
  min?: number
  tone?: Tone
  label?: string
  showValue?: boolean
  className?: string
  trackClassName?: string
}

export function Meter({
  value,
  max = 100,
  min = 0,
  tone = 'active',
  label,
  showValue = false,
  className,
  trackClassName,
}: MeterProps) {
  return (
    <BaseMeter.Root value={value} min={min} max={max} className={cn('flex w-full flex-col gap-1', className)}>
      {label || showValue ? (
        <div className="flex items-baseline justify-between gap-2">
          {label ? <BaseMeter.Label className="text-xs text-fg-muted">{label}</BaseMeter.Label> : <span />}
          {showValue ? <BaseMeter.Value className="font-mono text-xs tabular-nums text-fg-dim" /> : null}
        </div>
      ) : null}
      <BaseMeter.Track
        className={cn('h-1.5 w-full overflow-hidden rounded-sm bg-surface-3', trackClassName)}
      >
        <BaseMeter.Indicator
          className={cn('h-full transition-[width] duration-300 ease-standard', TONE_FILL[tone])}
        />
      </BaseMeter.Track>
    </BaseMeter.Root>
  )
}
