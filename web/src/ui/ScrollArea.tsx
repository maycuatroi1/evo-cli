import { ScrollArea as BaseScrollArea } from '@base-ui-components/react/scroll-area'
import type { ReactNode } from 'react'
import { cn } from './cn'

const SCROLLBAR = 'm-0.5 flex rounded-sm transition-opacity duration-150 ease-standard'

export interface ScrollAreaProps {
  children: ReactNode
  className?: string
  viewportClassName?: string
  horizontal?: boolean
}

export function ScrollArea({ children, className, viewportClassName, horizontal = false }: ScrollAreaProps) {
  return (
    <BaseScrollArea.Root className={cn('relative min-h-0 overflow-hidden', className)}>
      <BaseScrollArea.Viewport className={cn('h-full w-full overscroll-contain', viewportClassName)}>
        <BaseScrollArea.Content className={horizontal ? undefined : 'w-full min-w-0!'}>
          {children}
        </BaseScrollArea.Content>
      </BaseScrollArea.Viewport>
      <BaseScrollArea.Scrollbar
        orientation="vertical"
        className={(state) =>
          cn(SCROLLBAR, 'w-1.5 justify-center', state.hovering || state.scrolling ? 'opacity-100' : 'opacity-0')
        }
      >
        <BaseScrollArea.Thumb className="w-full rounded-sm bg-border-strong" />
      </BaseScrollArea.Scrollbar>
      {horizontal ? (
        <BaseScrollArea.Scrollbar
          orientation="horizontal"
          className={(state) =>
            cn(SCROLLBAR, 'h-1.5 items-center', state.hovering || state.scrolling ? 'opacity-100' : 'opacity-0')
          }
        >
          <BaseScrollArea.Thumb className="h-full rounded-sm bg-border-strong" />
        </BaseScrollArea.Scrollbar>
      ) : null}
      <BaseScrollArea.Corner />
    </BaseScrollArea.Root>
  )
}
