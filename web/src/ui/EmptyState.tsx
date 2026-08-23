import type { LucideIcon } from 'lucide-react'
import { Inbox } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from './cn'

export interface EmptyStateProps {
  title: string
  hint?: string
  icon?: LucideIcon
  action?: ReactNode
  className?: string
}

export function EmptyState({ title, hint, icon: Icon = Inbox, action, className }: EmptyStateProps) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center gap-2 px-6 py-10 text-center text-fg-dim',
        className,
      )}
    >
      <Icon size={28} strokeWidth={1.6} aria-hidden className="opacity-60" />
      <p className="text-sm font-medium text-fg-muted">{title}</p>
      {hint ? <p className="max-w-md text-xs text-fg-dim">{hint}</p> : null}
      {action ? <div className="mt-1">{action}</div> : null}
    </div>
  )
}
