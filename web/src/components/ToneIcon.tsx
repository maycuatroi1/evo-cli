import { AlertTriangle, Check, Circle, CircleDot, HelpCircle, Info, OctagonX, X } from 'lucide-react'
import type { Level, Tone } from '../types'

const TONE = {
  ok: Check,
  active: CircleDot,
  warn: AlertTriangle,
  bad: OctagonX,
  idle: Circle,
} as const

const LEVEL = {
  ok: Check,
  info: Info,
  warn: AlertTriangle,
  error: X,
  unknown: HelpCircle,
} as const

/**
 * Status is never carried by colour alone: every tone has its own glyph, so the graph
 * still reads correctly in greyscale and for the ~8% of men with a colour deficiency.
 */
export function ToneIcon({ tone, size = 13 }: { tone: Tone; size?: number }) {
  const Glyph = TONE[tone]
  return <Glyph size={size} strokeWidth={2.4} aria-hidden />
}

export function LevelIcon({ level, size = 13 }: { level: Level; size?: number }) {
  const Glyph = LEVEL[level]
  return <Glyph size={size} strokeWidth={2.4} aria-hidden />
}

export const TONE_LABEL: Record<Tone, string> = {
  ok: 'done',
  active: 'in progress',
  warn: 'needs attention',
  bad: 'blocked',
  idle: 'not started',
}
