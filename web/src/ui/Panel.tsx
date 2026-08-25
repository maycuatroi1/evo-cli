import { forwardRef } from 'react'
import type { HTMLAttributes, ReactNode } from 'react'
import { cn } from './cn'

export interface PanelProps extends Omit<HTMLAttributes<HTMLElement>, 'title'> {
  title?: ReactNode
  actions?: ReactNode
  bodyClassName?: string
}

export const Panel = forwardRef<HTMLElement, PanelProps>(function Panel(
  { title, actions, bodyClassName, className, children, ...rest },
  ref,
) {
  return (
    <section
      ref={ref}
      className={cn('flex min-h-0 flex-col rounded-lg border border-border bg-surface shadow-1', className)}
      {...rest}
    >
      {title || actions ? (
        <header className="flex h-10 shrink-0 items-center justify-between gap-2 border-b border-border px-3">
          <h2 className="truncate text-xs font-semibold tracking-wide text-fg-muted uppercase">{title}</h2>
          {actions ? <div className="flex shrink-0 items-center gap-1">{actions}</div> : null}
        </header>
      ) : null}
      <div className={cn('min-h-0 flex-1 p-3', bodyClassName)}>{children}</div>
    </section>
  )
})
