import { useRef } from 'react'

export interface TabDef {
  id: string
  label: string
  count?: number | null
  tone?: 'ok' | 'active' | 'warn' | 'bad' | 'idle'
}

export function Tabs({
  tabs,
  active,
  onChange,
  label,
}: {
  tabs: TabDef[]
  active: string
  onChange: (id: string) => void
  label: string
}) {
  const strip = useRef<HTMLDivElement>(null)

  const move = (delta: number) => {
    const index = tabs.findIndex((tab) => tab.id === active)
    if (index < 0) return
    const next = tabs[(index + delta + tabs.length) % tabs.length]
    onChange(next.id)
    strip.current?.querySelector<HTMLButtonElement>(`[data-tab='${next.id}']`)?.focus()
  }

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === 'ArrowRight') move(1)
    else if (event.key === 'ArrowLeft') move(-1)
    else if (event.key === 'Home') onChange(tabs[0].id)
    else if (event.key === 'End') onChange(tabs[tabs.length - 1].id)
    else return
    event.preventDefault()
  }

  return (
    <div className="tabs" role="tablist" aria-label={label} ref={strip} onKeyDown={onKeyDown}>
      {tabs.map((tab) => (
        <button
          key={tab.id}
          role="tab"
          data-tab={tab.id}
          id={`tab-${tab.id}`}
          aria-selected={tab.id === active}
          aria-controls={`panel-${tab.id}`}
          tabIndex={tab.id === active ? 0 : -1}
          data-on={tab.id === active || undefined}
          onClick={() => onChange(tab.id)}
        >
          {tab.label}
          {tab.count != null ? <span className={`tab-count mono ${tab.tone ? `tone-${tab.tone}` : ''}`}>{tab.count}</span> : null}
        </button>
      ))}
    </div>
  )
}

export function TabPanel({ id, active, children }: { id: string; active: string; children: React.ReactNode }) {
  if (id !== active) return null
  return (
    <div role="tabpanel" id={`panel-${id}`} aria-labelledby={`tab-${id}`} tabIndex={0} className="tabpanel">
      {children}
    </div>
  )
}
