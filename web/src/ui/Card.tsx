import { forwardRef } from 'react'
import type { HTMLAttributes } from 'react'
import { cn } from './cn'

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  interactive?: boolean
}

export const Card = forwardRef<HTMLDivElement, CardProps>(function Card(
  { interactive = false, className, ...rest },
  ref,
) {
  return (
    <div
      ref={ref}
      className={cn(
        'rounded-lg border border-border bg-surface shadow-1',
        interactive && 'transition-colors duration-150 ease-standard hover:border-border-strong hover:bg-surface-2',
        className,
      )}
      {...rest}
    />
  )
})
