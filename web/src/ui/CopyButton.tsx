import { Check, Copy } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { IconButton } from './IconButton'
import type { IconButtonSize } from './IconButton'
import { cn } from './cn'

export interface CopyButtonProps {
  value: string
  label?: string
  size?: IconButtonSize
  className?: string
}

export function CopyButton({ value, label = 'Copy', size = 'sm', className }: CopyButtonProps) {
  const [copied, setCopied] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout>>()

  useEffect(() => () => clearTimeout(timer.current), [])

  const copy = useCallback(() => {
    void navigator.clipboard?.writeText(value).then(() => {
      setCopied(true)
      clearTimeout(timer.current)
      timer.current = setTimeout(() => setCopied(false), 1200)
    })
  }, [value])

  return (
    <IconButton
      label={copied ? 'Copied' : label}
      size={size}
      onClick={copy}
      className={cn(copied && 'text-ok', className)}
    >
      {copied ? <Check size={13} strokeWidth={2.4} aria-hidden /> : <Copy size={13} strokeWidth={2.4} aria-hidden />}
    </IconButton>
  )
}
