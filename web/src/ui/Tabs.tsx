import { Tabs as BaseTabs } from '@base-ui-components/react/tabs'
import { cn } from './cn'

const TAB_BASE =
  'relative -mb-px inline-flex h-8 shrink-0 items-center gap-1.5 border-b-2 px-2.5 text-sm font-medium whitespace-nowrap transition-colors duration-150 ease-standard outline-none focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-ring'

export interface TabsProps extends Omit<BaseTabs.Root.Props, 'className'> {
  className?: string
}

export function Tabs({ className, ...rest }: TabsProps) {
  return <BaseTabs.Root className={cn('flex min-h-0 flex-col', className)} {...rest} />
}

export interface TabListProps extends Omit<BaseTabs.List.Props, 'className'> {
  className?: string
}

export function TabList({ className, ...rest }: TabListProps) {
  return (
    <BaseTabs.List
      className={cn('flex shrink-0 items-center gap-1 border-b border-border', className)}
      {...rest}
    />
  )
}

export interface TabProps extends Omit<BaseTabs.Tab.Props, 'className'> {
  className?: string
}

export function Tab({ className, ...rest }: TabProps) {
  return (
    <BaseTabs.Tab
      className={(state) =>
        cn(
          TAB_BASE,
          state.active ? 'border-active text-fg' : 'border-transparent text-fg-dim hover:text-fg-muted',
          className,
        )
      }
      {...rest}
    />
  )
}

export interface TabPanelProps extends Omit<BaseTabs.Panel.Props, 'className'> {
  className?: string
}

export function TabPanel({ className, ...rest }: TabPanelProps) {
  return (
    <BaseTabs.Panel
      className={cn('min-h-0 flex-1 outline-none focus-visible:outline-2 focus-visible:outline-ring', className)}
      {...rest}
    />
  )
}
