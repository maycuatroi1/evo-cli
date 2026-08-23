import { Toolbar as BaseToolbar } from '@base-ui-components/react/toolbar'
import { cn } from './cn'

export interface ToolbarProps extends Omit<BaseToolbar.Root.Props, 'className'> {
  className?: string
}

export function Toolbar({ className, ...rest }: ToolbarProps) {
  return (
    <BaseToolbar.Root
      className={cn('flex items-center gap-1 rounded-md border border-border bg-surface-2 px-1.5 py-1', className)}
      {...rest}
    />
  )
}

export interface ToolbarGroupProps extends Omit<BaseToolbar.Group.Props, 'className'> {
  className?: string
}

export function ToolbarGroup({ className, ...rest }: ToolbarGroupProps) {
  return <BaseToolbar.Group className={cn('flex items-center gap-1', className)} {...rest} />
}

export interface ToolbarSeparatorProps extends Omit<BaseToolbar.Separator.Props, 'className'> {
  className?: string
}

export function ToolbarSeparator({ className, ...rest }: ToolbarSeparatorProps) {
  return <BaseToolbar.Separator className={cn('mx-1 h-5 w-px shrink-0 bg-border', className)} {...rest} />
}
