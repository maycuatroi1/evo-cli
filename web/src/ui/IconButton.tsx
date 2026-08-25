import { forwardRef } from 'react'
import type { ButtonHTMLAttributes } from 'react'
import { cn } from './cn'

export type IconButtonIntent = 'solid' | 'quiet' | 'danger'
export type IconButtonSize = 'sm' | 'md'

const HIT_AREA =
  "before:absolute before:top-1/2 before:left-1/2 before:h-11 before:w-11 before:-translate-x-1/2 before:-translate-y-1/2 before:content-['']"

const BASE = `relative inline-flex shrink-0 items-center justify-center rounded-md border transition-colors duration-150 ease-standard focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:pointer-events-none disabled:opacity-50 ${HIT_AREA}`

const INTENT: Record<IconButtonIntent, string> = {
  solid: 'border-active bg-active text-bg hover:bg-active/85',
  quiet: 'border-transparent bg-transparent text-fg-dim hover:bg-surface-3 hover:text-fg',
  danger: 'border-transparent bg-transparent text-fg-dim hover:bg-bad-soft hover:text-bad',
}

const SIZE: Record<IconButtonSize, string> = {
  sm: 'h-6 w-6',
  md: 'h-8 w-8',
}

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  label: string
  intent?: IconButtonIntent
  size?: IconButtonSize
}

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { label, intent = 'quiet', size = 'md', type = 'button', className, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      aria-label={label}
      className={cn(BASE, INTENT[intent], SIZE[size], className)}
      {...rest}
    />
  )
})
