import { Toggle } from '@base-ui-components/react/toggle'
import { ToggleGroup } from '@base-ui-components/react/toggle-group'
import type { ReactNode } from 'react'
import { cn } from './cn'

const ITEM_BASE =
  'inline-flex h-6 shrink-0 items-center gap-1.5 rounded-sm px-2 text-xs font-medium whitespace-nowrap transition-colors duration-150 ease-standard outline-none focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring'

export interface SegmentedOption<T extends string> {
  value: T
  label: ReactNode
  hint?: string
}

export interface SegmentedControlProps<T extends string> {
  options: SegmentedOption<T>[]
  value: T
  onValueChange: (value: T) => void
  label: string
  className?: string
}

export function SegmentedControl<T extends string>({
  options,
  value,
  onValueChange,
  label,
  className,
}: SegmentedControlProps<T>) {
  return (
    <ToggleGroup
      aria-label={label}
      value={[value]}
      onValueChange={(next) => {
        const picked = next[0]
        if (typeof picked === 'string') onValueChange(picked as T)
      }}
      className={cn('inline-flex items-center gap-0.5 rounded-md border border-border bg-surface-2 p-0.5', className)}
    >
      {options.map((option) => (
        <Toggle
          key={option.value}
          value={option.value}
          title={option.hint}
          className={(state) =>
            cn(ITEM_BASE, state.pressed ? 'bg-surface-3 text-fg shadow-1' : 'text-fg-dim hover:text-fg-muted')
          }
        >
          {option.label}
        </Toggle>
      ))}
    </ToggleGroup>
  )
}
