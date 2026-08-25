import { forwardRef } from 'react'
import type { HTMLAttributes } from 'react'
import type { Tone } from '../types'
import { cn } from './cn'

export type ChipTone = Tone | 'neutral'

const TONE: Record<ChipTone, string> = {
  ok: 'border-ok/30 bg-ok-soft text-ok',
  active: 'border-active/30 bg-active-soft text-active',
  warn: 'border-warn/30 bg-warn-soft text-warn',
  bad: 'border-bad/30 bg-bad-soft text-bad',
  idle: 'border-idle/30 bg-idle-soft text-fg-dim',
  neutral: 'border-border bg-surface-3 text-fg-muted',
}

export interface ChipProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: ChipTone
}

export const Chip = forwardRef<HTMLSpanElement, ChipProps>(function Chip(
  { tone = 'neutral', className, ...rest },
  ref,
) {
  return (
    <span
      ref={ref}
      className={cn(
        'inline-flex shrink-0 items-center gap-1 rounded-sm border px-1.5 py-0.5 text-xs leading-none font-medium whitespace-nowrap',
        TONE[tone],
        className,
      )}
      {...rest}
    />
  )
})
