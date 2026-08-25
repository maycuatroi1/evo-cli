import { CircleHelp, Wrench } from 'lucide-react'
import { useMemo, useState } from 'react'
import { EmptyState } from '../components/EmptyState'
import { ProgressStrip } from '../components/ProgressStrip'
import type { PlanSummary, State } from '../types'

const SORTS = [
  { id: 'name', label: 'Name' },
  { id: 'progress', label: 'Progress' },
  { id: 'recent', label: 'Recent' },
] as const

type Sort = (typeof SORTS)[number]['id']

function humanizeId(id: string): string {
  const spaced = id.replace(/-/g, ' ')
  return spaced.charAt(0).toUpperCase() + spaced.slice(1)
}

export function PlansView({ state, go }: { state: State; go: (to: string) => void }) {
  const [sort, setSort] = useState<Sort>('name')

  const groups = useMemo(() => {
    const order = (a: PlanSummary, b: PlanSummary) => {
      if (sort === 'progress') return a.progress.pct - b.progress.pct || a.id.localeCompare(b.id)
      if (sort === 'recent') return b.mtime - a.mtime
      return a.id.localeCompare(b.id)
    }
    return [
      { title: 'Active', items: state.plans.filter((plan) => plan.area === 'active').sort(order) },
      { title: 'Completed', items: state.plans.filter((plan) => plan.area !== 'active').sort(order) },
    ].filter((group) => group.items.length > 0)
  }, [state.plans, sort])

  if (state.plans.length === 0) {
    return (
      <div className="view">
        <section className="panel">
          <EmptyState
            title="No exec-plan under plans/."
            hint="A plan is how a change that spans repos gets an order. Write one to plans/active/<slug>.yaml."
          />
        </section>
      </div>
    )
  }

  return (
    <div className="view">
      {groups.map((group, index) => (
        <section className="panel" key={group.title}>
          <header className="panel-head">
            <div className="panel-title">
              <h2>{group.title}</h2>
              <span className="panel-meta mono">{group.items.length}</span>
            </div>
            {index === 0 ? (
              <div className="segmented" role="group" aria-label="Sort plans">
                {SORTS.map((entry) => (
                  <button
                    key={entry.id}
                    data-on={sort === entry.id || undefined}
                    aria-pressed={sort === entry.id}
                    onClick={() => setSort(entry.id)}
                  >
                    {entry.label}
                  </button>
                ))}
              </div>
            ) : null}
          </header>
          <div className="plan-cards">
            {group.items.map((plan) => (
              <PlanCard key={plan.id} plan={plan} onOpen={() => go(`#/plans/${plan.id}`)} />
            ))}
          </div>
        </section>
      ))}
    </div>
  )
}

function PlanCard({ plan, onOpen }: { plan: PlanSummary; onOpen: () => void }) {
  const p = plan.progress
  const tone = p.steps_blocking > 0 ? 'bad' : p.pct === 100 ? 'ok' : p.steps_active > 0 ? 'active' : 'idle'

  return (
    <button className="plan-card" onClick={onOpen} data-tone={tone}>
      <span className="plan-card-head">
        <span className="plan-card-id mono">{humanizeId(plan.id)}</span>
        <span className={`chip tone-${tone}`}>{p.pct}%</span>
      </span>
      <span className="plan-card-goal">{plan.goal}</span>
      <ProgressStrip
        compact
        label={`${p.steps_done} of ${p.steps_total} steps done`}
        total={p.steps_total}
        segments={[
          { tone: 'ok', label: 'done', value: p.steps_done },
          { tone: 'active', label: 'in progress', value: p.steps_active },
        ]}
      />
      <span className="plan-card-foot">
        <span className="mono">
          {p.steps_done}/{p.steps_total} steps
        </span>
        <span className="mono">
          {p.repos_done}/{p.repos_total} repos
        </span>
        {p.steps_blocking > 0 ? (
          <span className="chip tone-bad" title={`${p.steps_blocking} unfinished steps other work waits on`}>
            {p.steps_blocking} blocking
          </span>
        ) : null}
        {p.debt_open > 0 ? (
          <span className="chip tone-warn" title={`${p.debt_open} open tech debt items`}>
            <Wrench size={10} aria-hidden /> {p.debt_open}
          </span>
        ) : null}
        {p.questions_open > 0 ? (
          <span className="chip tone-warn" title={`${p.questions_open} unanswered questions`}>
            <CircleHelp size={10} aria-hidden /> {p.questions_open}
          </span>
        ) : null}
      </span>
    </button>
  )
}
