import { ArrowLeft, CheckCircle2, GitCommitHorizontal, LoaderCircle, RefreshCw } from 'lucide-react'
import { useLayoutEffect, useMemo, useState } from 'react'
import { completePlan, fetchGit, fetchPlan, useAsync, type Async } from '../api'
import { CopyButton as FieldCopyButton, FieldList, ITEM_HIDDEN } from '../components/FieldValue'
import { EmptyState } from '../components/EmptyState'
import { GraphPanel } from '../components/GraphPanel'
import { NextUp } from '../components/NextUp'
import { Counter, ProgressStrip } from '../components/ProgressStrip'
import { StepList } from '../components/StepList'
import { TabPanel, Tabs, type TabDef } from '../components/Tabs'
import { LevelIcon, ToneIcon } from '../components/ToneIcon'
import { frontier } from '../frontier'
import type { GitOverlay, SectionItem } from '../types'
import { Button, Chip, CopyButton, Separator } from '../ui'

const BAR_EDGE = 'mx-auto w-full max-w-[1500px] px-6'

function scrollParentOf(node: HTMLElement): HTMLElement | null {
  let parent = node.parentElement
  while (parent) {
    const overflow = getComputedStyle(parent).overflowY
    if (overflow === 'auto' || overflow === 'scroll' || overflow === 'overlay') return parent
    parent = parent.parentElement
  }
  return null
}

function useStickyBar(bar: HTMLElement | null) {
  useLayoutEffect(() => {
    if (!bar) return
    const scroller = scrollParentOf(bar)
    if (!scroller) return
    const previous = scroller.style.scrollPaddingTop
    const apply = () => {
      const inset = parseFloat(getComputedStyle(scroller).paddingTop) || 0
      bar.style.top = `${-inset}px`
      scroller.style.scrollPaddingTop = `${bar.getBoundingClientRect().height}px`
    }
    apply()
    const observer = new ResizeObserver(apply)
    observer.observe(bar)
    observer.observe(scroller)
    return () => {
      observer.disconnect()
      scroller.style.scrollPaddingTop = previous
    }
  }, [bar])
}

const NOTE_SECTIONS = ['decisions', 'tech_debt', 'open_questions', 'references'] as const

const SECTION_TITLES: Record<string, string> = {
  references: 'References',
  repos: 'Repos',
  steps: 'Steps',
  decisions: 'Decisions',
  tech_debt: 'Tech debt',
  open_questions: 'Open questions',
}

const COMMANDS: Record<string, (plan: string, index: number) => string> = {
  tech_debt: (plan, index) => `evo harness debt ${plan} ${index} fixed`,
  open_questions: (plan, index) => `evo harness question ${plan} ${index} answered`,
  repos: (plan, index) => `evo harness repo ${plan} ${index} merged`,
}

export function PlanDetailView({ id, digest, go }: { id: string; digest: string | null; go: (to: string) => void }) {
  const { data, error, loading, reload } = useAsync(() => fetchPlan(id), [id, digest])
  const [refetch, setRefetch] = useState(false)
  const git = useAsync<GitOverlay>(() => fetchGit(id, refetch), [id, digest, refetch])
  const [tab, setTab] = useState('steps')
  const [selected, setSelected] = useState<string | null>(null)
  const [completing, setCompleting] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [actionMessage, setActionMessage] = useState<string | null>(null)
  const [bar, setBar] = useState<HTMLElement | null>(null)

  useStickyBar(bar)

  const steps = useMemo(() => {
    if (!data) return null
    const graph = data.graphs.steps
    const blocked = new Set(frontier(graph).blocked.map((node) => node.id))
    const tally = { done: 0, active: 0, ready: 0, blocked: 0 }
    for (const node of graph.nodes) {
      if (node.tone === 'ok') tally.done += 1
      else if (node.tone === 'active') tally.active += 1
      else if (blocked.has(node.id)) tally.blocked += 1
      else tally.ready += 1
    }
    return tally
  }, [data])

  if (error) {
    return (
      <div className="view">
        <section className="panel">
          <p className="empty tone-bad">{error}</p>
        </section>
      </div>
    )
  }
  if (loading || !data || !steps) {
    return (
      <div className="view">
        <section className="panel">
          <p className="empty">Loading {id}...</p>
        </section>
      </div>
    )
  }

  const { plan, graphs } = data
  const p = plan.progress

  const moveToDone = async () => {
    setCompleting(true)
    setActionError(null)
    setActionMessage(null)
    try {
      await completePlan(id)
      setActionMessage('Plan moved to completed.')
      reload()
    } catch (exc) {
      setActionError(exc instanceof Error ? exc.message : 'Could not complete this plan.')
    } finally {
      setCompleting(false)
    }
  }

  const notes = NOTE_SECTIONS.map((name) => [name, plan.sections[name] ?? []] as const).filter(([, list]) => list.length > 0)
  const noteTotal = notes.reduce((sum, [, list]) => sum + list.length, 0)

  const tabs: TabDef[] = [
    { id: 'steps', label: 'Steps', count: p.steps_total },
    { id: 'repos', label: 'Repos', count: (plan.sections.repos ?? []).length },
    { id: 'graph', label: 'Graph' },
    {
      id: 'git',
      label: 'Git',
      count: git.data ? git.data.errors + git.data.warnings : null,
      tone: git.data && git.data.errors > 0 ? 'bad' : git.data && git.data.warnings > 0 ? 'warn' : 'idle',
    },
    { id: 'notes', label: 'Notes', count: noteTotal },
  ]

  const jumpToStep = (key: string) => {
    setTab('steps')
    setSelected(`step-${key}`)
  }

  return (
    <div className="-mx-6 -mt-6 flex flex-col">
      <header ref={setBar} data-plan-bar className="sticky top-0 z-20 border-b border-border bg-bg">
        <div className={`${BAR_EDGE} flex h-14 items-center gap-3`}>
          <Button size="sm" className="shrink-0" onClick={() => go('#/plans')}>
            <ArrowLeft size={13} aria-hidden /> All plans
          </Button>

          <span className="h-5 shrink-0">
            <Separator orientation="vertical" />
          </span>

          <h1 title={plan.id} className="min-w-0 truncate font-mono text-lg font-semibold tracking-[-0.02em]">
            {plan.id}
          </h1>
          <Chip tone={plan.area === 'active' ? 'active' : 'ok'} className="font-mono">
            {plan.area}
          </Chip>

          <span
            className="ml-auto shrink-0 font-mono text-sm font-semibold tabular-nums text-fg-muted"
            title={`${p.steps_done} of ${p.steps_total} steps done`}
          >
            {p.steps_done}/{p.steps_total}
          </span>
          <CopyButton value={plan.path} label={`Copy plan path: ${plan.path}`} size="md" />
          {plan.area === 'active' ? (
            <Button intent="ok" onClick={moveToDone} disabled={completing}>
              {completing ? (
                <LoaderCircle size={15} className="animate-spin" aria-hidden />
              ) : (
                <CheckCircle2 size={15} aria-hidden />
              )}
              {completing ? 'Moving...' : 'Move to done'}
            </Button>
          ) : null}
        </div>
      </header>

      <div className={`${BAR_EDGE} flex flex-col gap-4 pt-4`}>
        {actionMessage ? (
          <p className="plan-action-message tone-ok" role="status">
            <CheckCircle2 size={15} aria-hidden /> {actionMessage}
          </p>
        ) : null}
        {actionError ? (
          <p className="plan-action-message tone-bad" role="alert">
            {actionError}
          </p>
        ) : null}

        <div className="plan-hero">
          <div className="plan-brief">
            <Goal text={plan.goal} />
            <div className="plan-summary">
              <ProgressStrip
                label={`Step progress for ${plan.id}`}
                total={p.steps_total}
                segments={[
                  { tone: 'ok', label: 'done', value: steps.done },
                  { tone: 'active', label: 'in progress', value: steps.active },
                  { tone: 'warn', label: 'ready', value: steps.ready },
                  { tone: 'bad', label: 'blocked', value: steps.blocked },
                ]}
              />
              <div className="plan-counters">
                <Counter label="repos merged" done={p.repos_done} total={p.repos_total} tone="active" />
                <Counter label="debt closed" done={p.debt_total - p.debt_open} total={p.debt_total} tone="warn" />
                <Counter
                  label="questions answered"
                  done={p.questions_total - p.questions_open}
                  total={p.questions_total}
                  tone="warn"
                />
              </div>
            </div>
          </div>
          <NextUp graph={graphs.steps} planId={plan.id} onPick={jumpToStep} />
        </div>

        <section className="panel">
          <header className="panel-head">
            <Tabs tabs={tabs} active={tab} onChange={setTab} label="Plan section" />
            {tab === 'git' ? (
              <button
                className="tool"
                onClick={() => {
                  setRefetch(true)
                  git.reload()
                }}
                disabled={git.loading}
              >
                <RefreshCw size={13} className={git.loading ? 'spin' : undefined} aria-hidden />
                {git.loading ? 'checking' : 'git fetch and recheck'}
              </button>
            ) : null}
          </header>

          <TabPanel id="steps" active={tab}>
            <StepList
              items={plan.sections.steps ?? []}
              graph={graphs.steps}
              planId={plan.id}
              selected={selected}
              onSelect={setSelected}
            />
          </TabPanel>

          <TabPanel id="repos" active={tab}>
            <SectionList items={plan.sections.repos ?? []} plan={plan.id} section="repos" />
          </TabPanel>

          <TabPanel id="graph" active={tab}>
            <div className="graph-tab">
              <GraphPanel
                title="Repo merge order"
                hint="An edge means the source has to be on its base branch before the target merges."
                graph={graphs.repos}
                edgeLegend={['declared', 'inferred']}
              />
              <GraphPanel
                title="Step order"
                hint="Built from depends_on, depends_on_step, blocked_by and blocks. Where a plan declares none of those, the order is inferred from repo merge order and step numbering."
                graph={graphs.steps}
                selected={selected}
                onSelect={setSelected}
              />
            </div>
          </TabPanel>

          <TabPanel id="git" active={tab}>
            <GitCheck git={git} />
          </TabPanel>

          <TabPanel id="notes" active={tab}>
            <NoteSections notes={notes} plan={plan.id} />
          </TabPanel>
        </section>
      </div>
    </div>
  )
}

const GOAL_CLAMP = 260

function Goal({ text }: { text: string }) {
  const [full, setFull] = useState(false)
  const long = text.length > GOAL_CLAMP
  return (
    <div className="plan-goal-wrap">
      <p className="plan-goal" data-clamped={long && !full ? true : undefined}>
        {text}
      </p>
      {long ? (
        <button className="link plan-goal-more" onClick={() => setFull(!full)} aria-expanded={full}>
          {full ? 'less' : 'more'}
        </button>
      ) : null}
    </div>
  )
}

function SectionList({ items, plan, section }: { items: SectionItem[]; plan: string; section: string }) {
  const [open, setOpen] = useState<Set<number>>(new Set())

  if (items.length === 0) {
    return <EmptyState title="Nothing in this section." hint={`This plan declares no ${SECTION_TITLES[section] ?? section}.`} />
  }

  const toggle = (index: number) =>
    setOpen((prev) => {
      const next = new Set(prev)
      if (next.has(index)) next.delete(index)
      else next.add(index)
      return next
    })

  return (
    <div className="srows">
      {items.map((item) => {
        const key = String(item.raw.id ?? item.raw.order ?? item.index)
        const command = COMMANDS[section]?.(plan, item.index) ?? null
        const shown = open.has(item.index)
        return (
          <article key={item.index} className="srow" data-tone={item.tone} data-open={shown || undefined}>
            <div className="srow-bar">
              <button className="srow-head" aria-expanded={shown} onClick={() => toggle(item.index)}>
                <span className={`srow-icon tone-${item.tone}`}>
                  <ToneIcon tone={item.tone} />
                </span>
                <span className="item-key mono">{key}</span>
                <span className="srow-title">{item.title}</span>
                {item.status ? <span className={`chip tone-${item.tone}`}>{item.status}</span> : null}
                <span className="srow-caret-slot" aria-hidden />
              </button>
              {command ? <FieldCopyButton text={command} label={`Copy: ${command}`} /> : null}
            </div>
            {shown ? (
              <div className="srow-body">
                <FieldList raw={item.raw} hidden={ITEM_HIDDEN} />
              </div>
            ) : null}
          </article>
        )
      })}
    </div>
  )
}

function NoteSections({ notes, plan }: { notes: (readonly [string, SectionItem[]])[]; plan: string }) {
  const [which, setWhich] = useState(notes[0]?.[0] ?? 'decisions')

  if (notes.length === 0) {
    return <EmptyState title="No notes on this plan." hint="Decisions, tech debt, open questions and references all live here." />
  }

  const active = notes.find(([name]) => name === which) ?? notes[0]

  return (
    <div className="notes">
      <div className="steps-bar">
        <div className="segmented segmented-scroll" role="group" aria-label="Which notes to show">
          {notes.map(([name, list]) => (
            <button key={name} data-on={name === active[0] || undefined} aria-pressed={name === active[0]} onClick={() => setWhich(name)}>
              {SECTION_TITLES[name] ?? name}
              <span className="mono">{list.length}</span>
            </button>
          ))}
        </div>
      </div>
      <SectionList items={active[1]} plan={plan} section={active[0]} />
    </div>
  )
}

function GitCheck({ git }: { git: Async<GitOverlay> }) {
  const { data, error, loading } = git

  if (error) return <p className="empty tone-bad">{error}</p>
  if (!data) return <p className="empty">{loading ? 'Reading git...' : 'No git state yet.'}</p>

  return (
    <>
      <p className="panel-hint">
        {data.errors} wrong / {data.warnings} warnings / {data.unknown} uncheckable. A wrong line means the plan claims
        something git does not agree with.
      </p>
      <div className="verdicts">
        {data.repos.map((repo) => (
          <article key={repo.repo} className="verdict">
            <header>
              <span className="mono">{repo.repo}</span>
              <span className="chip">{repo.branch || 'no branch'}</span>
              <span className={`chip tone-${repo.tone}`}>{repo.status || '-'}</span>
              {repo.dirty ? <span className="chip tone-warn">dirty worktree</span> : null}
              {repo.head ? (
                <span className="cell-dim mono verdict-head">
                  <GitCommitHorizontal size={12} aria-hidden /> {repo.head}
                </span>
              ) : null}
            </header>
            <ul>
              {repo.verdicts.map((verdict, index) => (
                <li
                  key={index}
                  className={`tone-${
                    verdict.level === 'error' ? 'bad' : verdict.level === 'warn' ? 'warn' : verdict.level === 'ok' ? 'ok' : 'idle'
                  }`}
                >
                  <LevelIcon level={verdict.level} />
                  <span>{verdict.text}</span>
                </li>
              ))}
            </ul>
          </article>
        ))}
      </div>
    </>
  )
}
