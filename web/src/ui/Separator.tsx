import { Separator as BaseSeparator } from '@base-ui-components/react/separator'
import { cn } from './cn'

export interface SeparatorProps extends Omit<BaseSeparator.Props, 'className'> {
  className?: string
}

export function Separator({ orientation = 'horizontal', className, ...rest }: SeparatorProps) {
  return (
    <BaseSeparator
      orientation={orientation}
      className={cn('shrink-0 bg-border', orientation === 'vertical' ? 'h-full w-px' : 'h-px w-full', className)}
      {...rest}
    />
  )
}
