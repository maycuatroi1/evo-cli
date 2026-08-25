import { AlertTriangle, Check, Circle, CircleDot, HelpCircle, Info, OctagonX, X } from 'lucide-react'
import type { Level, Tone } from '../types'
import { cn } from './cn'

const TONE_GLYPH = {
  ok: Check,
  active: CircleDot,
  warn: AlertTriangle,
  bad: OctagonX,
  idle: Circle,
} as const

const LEVEL_GLYPH = {
  ok: Check,
  info: Info,
  warn: AlertTriangle,
  error: X,
  unknown: HelpCircle,
} as const

export const TONE_TEXT: Record<Tone, string> = {
  ok: 'text-ok',
  active: 'text-active',
  warn: 'text-warn',
  bad: 'text-bad',
  idle: 'text-fg-dim',
}

export const TONE_LABEL: Record<Tone, string> = {
  ok: 'done',
  active: 'in progress',
  warn: 'needs attention',
  bad: 'blocked',
  idle: 'not started',
}

export function ToneIcon({
  tone,
  size = 13,
  className,
}: {
  tone: Tone
  size?: number
  className?: string
}) {
  const Glyph = TONE_GLYPH[tone]
  return <Glyph size={size} strokeWidth={2.4} aria-hidden className={cn('shrink-0', TONE_TEXT[tone], className)} />
}

export function LevelIcon({
  level,
  size = 13,
  className,
}: {
  level: Level
  size?: number
  className?: string
}) {
  const Glyph = LEVEL_GLYPH[level]
  return <Glyph size={size} strokeWidth={2.4} aria-hidden className={cn('shrink-0', className)} />
}
