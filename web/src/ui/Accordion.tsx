import { Accordion as BaseAccordion } from '@base-ui-components/react/accordion'
import { ChevronRight } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from './cn'

const TRIGGER_BASE =
  'flex w-full items-center gap-2 py-2 pr-2 text-left text-sm font-medium transition-colors duration-150 ease-standard outline-none focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-ring'

export interface AccordionProps extends Omit<BaseAccordion.Root.Props, 'className'> {
  className?: string
}

export function Accordion({ className, ...rest }: AccordionProps) {
  return <BaseAccordion.Root className={cn('flex flex-col', className)} {...rest} />
}

export interface AccordionItemProps extends Omit<BaseAccordion.Item.Props, 'className' | 'title'> {
  title: ReactNode
  aside?: ReactNode
  className?: string
  panelClassName?: string
}

export function AccordionItem({ title, aside, className, panelClassName, children, ...rest }: AccordionItemProps) {
  return (
    <BaseAccordion.Item className={cn('border-b border-border last:border-b-0', className)} {...rest}>
      <BaseAccordion.Header className="m-0">
        <BaseAccordion.Trigger
          className={(state) => cn(TRIGGER_BASE, state.open ? 'text-fg' : 'text-fg-muted hover:text-fg')}
          render={(props, state) => (
            <button {...props}>
              <ChevronRight
                size={14}
                strokeWidth={2.4}
                aria-hidden
                className={cn(
                  'shrink-0 transition-transform duration-150 ease-standard',
                  state.open && 'rotate-90',
                )}
              />
              <span className="min-w-0 flex-1 truncate">{title}</span>
              {aside ? <span className="shrink-0 text-xs text-fg-dim">{aside}</span> : null}
            </button>
          )}
        />
      </BaseAccordion.Header>
      <BaseAccordion.Panel className={cn('overflow-hidden pb-2', panelClassName)}>{children}</BaseAccordion.Panel>
    </BaseAccordion.Item>
  )
}
