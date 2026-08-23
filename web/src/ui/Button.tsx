import { forwardRef } from 'react'
import type { ButtonHTMLAttributes } from 'react'
import { cn } from './cn'

export type ButtonIntent = 'solid' | 'quiet' | 'danger'
export type ButtonSize = 'sm' | 'md'

const BASE =
  'inline-flex shrink-0 items-center justify-center rounded-md border font-medium whitespace-nowrap transition-colors duration-150 ease-standard outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:pointer-events-none disabled:opacity-50'

const INTENT: Record<ButtonIntent, string> = {
  solid: 'border-active bg-active text-bg hover:bg-active/85',
  quiet: 'border-border bg-surface-2 text-fg-muted hover:border-border-strong hover:bg-surface-3 hover:text-fg',
  danger: 'border-bad/40 bg-bad-soft text-bad hover:border-bad hover:bg-bad hover:text-bg',
}

const SIZE: Record<ButtonSize, string> = {
  sm: 'h-7 gap-1.5 px-2.5 text-xs',
  md: 'h-8 gap-2 px-3 text-sm',
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  intent?: ButtonIntent
  size?: ButtonSize
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { intent = 'quiet', size = 'md', type = 'button', className, ...rest },
  ref,
) {
  return <button ref={ref} type={type} className={cn(BASE, INTENT[intent], SIZE[size], className)} {...rest} />
})
