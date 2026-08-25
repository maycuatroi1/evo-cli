import { ChevronDown, Layers, List } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { frontier } from '../frontier'
import type { Graph, SectionItem } from '../types'
import { EmptyState } from './EmptyState'
import { StepRow, type StepState } from './StepRow'

const FILTERS = [
  { id: 'all', label: 'All' },
  { id: 'ready', label: 'Ready' },
  { id: 'blocked', label: 'Blocked' },
  { id: 'done', label: 'Done' },
] as const

type Filter = (typeof FILTERS)[number]['id']

export function keyOf(item: SectionItem): string {
  return String(item.raw.id ?? item.raw.order ?? item.index)
}

export function StepList({
  items,
  graph,
  planId,
  selected,
  onSelect,
}: {
  items: SectionItem[]
  graph: Graph
  planId: string
  selected: string | null
  onSelect: (id: string | null) => void
}) {
  const [open, setOpen] = useState<Set<string>>(new Set())
  const [filter, setFilter] = useState<Filter>('all')
  const [byRepo, setByRepo] = useState(false)
  const [showDone, setShowDone] = useState(false)
  const [scrollTarget, setScrollTarget] = useState<string | null>(null)

  const { states, waiting } = useMemo(() => {
    const edge = frontier(graph)
    const ready = new Set(edge.ready.map((node) => node.id))
    const blocked = new Set(edge.blocked.map((node) => node.id))
    const label = new Map(graph.nodes.map((node) => [node.id, node.label]))
    const states = new Map<string, StepState>()
    const waiting = new Map<string, string[]>()
    for (const item of items) {
      const key = keyOf(item)
      const id = `step-${key}`
      states.set(key, item.tone === 'ok' ? 'done' : ready.has(id) ? 'ready' : blocked.has(id) ? 'blocked' : 'open')
      const blockers = edge.waiting.get(id)
      if (blockers) waiting.set(key, blockers.map((from) => label.get(from) ?? from))
    }
    return { states, waiting }
  }, [graph, items])

  const counts = useMemo(() => {
    const tally: Record<string, number> = { all: items.length, ready: 0, blocked: 0, done: 0 }
    for (const state of states.values()) if (state in tally) tally[state] += 1
    return tally
  }, [items.length, states])

  useEffect(() => {
    if (!selected || !selected.startsWith('step-')) return
    const key = selected.slice('step-'.length)
    if (!states.has(key)) return
    setOpen((prev) => (prev.has(key) ? prev : new Set(prev).add(key)))
    if (states.get(key) === 'done') setShowDone(true)
    setScrollTarget(key)
  }, [selected, states])

  useEffect(() => {
    if (!scrollTarget) return
    const row = document.getElementById(`step-${scrollTarget}`)
    if (!row) return
    row.scrollIntoView({ behavior: 'smooth', block: 'center' })
    setScrollTarget(null)
  }, [scrollTarget, showDone, filter, byRepo])

  const toggle = (key: string) => {
    setOpen((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const jump = (key: string) => {
    if (!states.has(key)) return
    setFilter('all')
    if (states.get(key) === 'done') setShowDone(true)
    setOpen((prev) => new Set(prev).add(key))
    onSelect(`step-${key}`)
    setScrollTarget(key)
  }

  const row = (item: SectionItem) => {
    const key = keyOf(item)
    return (
      <StepRow
        key={key}
        item={item}
        itemKey={key}
        state={states.get(key) ?? 'open'}
        command={`evo harness step ${planId} ${key} done`}
        open={open.has(key)}
        onToggle={() => toggle(key)}
        onJump={jump}
        waiting={waiting.get(key)}
      />
    )
  }

  const render = (list: SectionItem[]) => {
    if (!byRepo) return list.map(row)
    const groups = new Map<string, SectionItem[]>()
    for (const item of list) {
      const repo = item.raw.repo ? String(item.raw.repo) : 'unassigned'
      groups.set(repo, [...(groups.get(repo) ?? []), item])
    }
    return [...groups].map(([repo, group]) => (
      <div className="srow-group" key={repo}>
        <h3 className="srow-group-head mono">
          {repo}
          <span className="cell-dim">{group.length}</span>
        </h3>
        {group.map(row)}
      </div>
    ))
  }

  const matching = filter === 'all' ? items : items.filter((item) => states.get(keyOf(item)) === filter)
  const live = filter === 'all' ? matching.filter((item) => states.get(keyOf(item)) !== 'done') : matching
  const done = filter === 'all' ? matching.filter((item) => states.get(keyOf(item)) === 'done') : []

  return (
    <div className="steps">
      <div className="steps-bar">
        <div className="segmented" role="group" aria-label="Filter steps by state">
          {FILTERS.map((entry) => (
            <button
              key={entry.id}
              data-on={filter === entry.id || undefined}
              aria-pressed={filter === entry.id}
              onClick={() => setFilter(entry.id)}
            >
              {entry.label}
              <span className="mono">{counts[entry.id] ?? 0}</span>
            </button>
          ))}
        </div>
        <button
          className="tool"
          onClick={() => setByRepo(!byRepo)}
          aria-pressed={byRepo}
          title={byRepo ? 'Show one flat list' : 'Group the steps by repo'}
        >
          {byRepo ? <Layers size={13} aria-hidden /> : <List size={13} aria-hidden />}
          {byRepo ? 'by repo' : 'flat'}
        </button>
      </div>

      {live.length === 0 && done.length === 0 ? (
        <EmptyState
          title={filter === 'all' ? 'This plan has no steps yet.' : `Nothing is ${filter}.`}
          hint={filter === 'all' ? 'Add a steps: block to the plan YAML.' : 'Try another filter.'}
        />
      ) : null}

      {live.length > 0 ? <div className="srows">{render(live)}</div> : null}

      {filter === 'all' && live.length === 0 && done.length > 0 ? (
        <p className="steps-clear tone-ok">Every step is done. Move the plan to completed.</p>
      ) : null}

      {done.length > 0 ? (
        <div className="done-fold" data-open={showDone || undefined}>
          <button className="done-fold-head" aria-expanded={showDone} onClick={() => setShowDone(!showDone)}>
            <ChevronDown size={14} className="srow-caret" aria-hidden />
            {done.length} done step{done.length > 1 ? 's' : ''}
          </button>
          {showDone ? <div className="srows">{render(done)}</div> : null}
        </div>
      ) : null}
    </div>
  )
}
