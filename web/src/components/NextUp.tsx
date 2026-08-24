import { ArrowRight, CircleCheckBig, CircleSlash, Hourglass } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useMemo } from 'react'
import type { ReactNode } from 'react'
import { frontier } from '../frontier'
import type { Graph } from '../types'
import { Chip, CopyButton, cn } from '../ui'

const SHOWN = 3

function Head({
  icon: Icon,
  tone,
  children,
}: {
  icon: LucideIcon
  tone: string
  children: ReactNode
}) {
  return (
    <h2 className={cn('m-0 flex items-center gap-2 text-[11px] font-semibold tracking-[0.07em] uppercase', tone)}>
      <Icon size={14} aria-hidden />
      {children}
    </h2>
  )
}

function Note({ children }: { children: ReactNode }) {
  return <p className="m-0 max-w-[68ch] text-xs leading-relaxed text-fg-muted">{children}</p>
}

export function NextUp({ graph, planId, onPick }: { graph: Graph; planId: string; onPick: (key: string) => void }) {
  const edge = useMemo(() => frontier(graph), [graph])

  if (graph.nodes.length === 0) {
    return (
      <div className="flex flex-col gap-3">
        <Head icon={CircleSlash} tone="text-fg-dim">
          No steps
        </Head>
        <Note>This plan declares no steps, so there is nothing to pick up.</Note>
      </div>
    )
  }

  if (edge.ready.length === 0 && edge.blocked.length === 0) {
    return (
      <div className="flex flex-col gap-3">
        <Head icon={CircleCheckBig} tone="text-ok">
          All steps done
        </Head>
        <Note>Nothing left to pick up. This plan is ready to move to completed.</Note>
      </div>
    )
  }

  if (edge.ready.length === 0) {
    const label = new Map(graph.nodes.map((node) => [node.id, node.label]))
    const blockers = [...new Set(edge.blocked.flatMap((node) => edge.waiting.get(node.id) ?? []))].map(
      (id) => label.get(id) ?? id,
    )
    return (
      <div className="flex flex-col gap-3">
        <Head icon={Hourglass} tone="text-warn">
          Nothing is ready
        </Head>
        <Note>
          {edge.blocked.length} step{edge.blocked.length > 1 ? 's are' : ' is'} open
          {blockers.length > 0 ? `, all waiting on step ${blockers.join(', ')}` : ', and every one is marked blocked'}.
        </Note>
      </div>
    )
  }

  const ready = edge.ready.slice(0, SHOWN)
  const more = edge.ready.length - ready.length

  return (
    <div className="flex min-w-0 flex-col gap-3">
      <Head icon={ArrowRight} tone="text-active">
        Next up
        {edge.ready.length > 1 ? (
          <Chip className="ml-auto font-mono tracking-normal normal-case text-fg-dim">{edge.ready.length} ready</Chip>
        ) : null}
      </Head>
      <ul className="m-0 flex list-none flex-col gap-1 p-0">
        {ready.map((node) => {
          const key = node.label
          const command = `evo harness step ${planId} ${key} done`
          return (
            <li key={node.id} className="flex min-w-0 items-center gap-1">
              <button
                className="grid min-h-9 min-w-0 flex-1 grid-cols-[auto_minmax(0,1fr)] items-start gap-x-2 gap-y-0.5 rounded-sm p-2 text-left transition-colors duration-150 ease-standard hover:bg-surface-2"
                onClick={() => onPick(key)}
                title="Open this step"
              >
                <span className="row-span-2 min-w-[22px] rounded-[4px] bg-surface-3 px-1.5 py-px text-center font-mono text-[11px] font-semibold">
                  {key}
                </span>
                <span className="col-start-2 text-[13px] leading-snug font-medium">
                  {String(node.meta.title ?? node.label)}
                </span>
                {node.meta.repo ? (
                  <span className="col-start-2 truncate font-mono text-[11px] text-fg-dim">{String(node.meta.repo)}</span>
                ) : null}
              </button>
              <CopyButton value={command} label={`Copy: ${command}`} />
            </li>
          )
        })}
      </ul>
      {more > 0 ? <Note>and {more} more ready.</Note> : null}
    </div>
  )
}
