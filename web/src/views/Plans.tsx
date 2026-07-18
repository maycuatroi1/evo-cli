import { CircleHelp, Wrench } from 'lucide-react'
import type { PlanSummary, State } from '../types'

export function PlansView({ state, go }: { state: State; go: (to: string) => void }) {
  const active = state.plans.filter((plan) => plan.area === 'active')
  const completed = state.plans.filter((plan) => plan.area !== 'active')

  if (state.plans.length === 0) {
    return (
      <div className="view">
        <section className="panel">
          <p className="empty">No exec-plan under plans/. A plan is how a change that spans repos gets an order.</p>
        </section>
      </div>
    )
  }

  return (
    <div className="view">
      {[
        { title: 'Active', items: active },
        { title: 'Completed', items: completed },
      ]
        .filter((group) => group.items.length > 0)
        .map((group) => (
          <section className="panel" key={group.title}>
            <header className="panel-head">
              <div className="panel-title">
                <h2>{group.title}</h2>
                <span className="panel-meta mono">{group.items.length}</span>
              </div>
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
        <span className="plan-card-id mono">{plan.id}</span>
        <span className={`chip tone-${tone}`}>{p.pct}%</span>
      </span>
      <span className="plan-card-goal">{plan.goal}</span>
      <span className="meter" role="img" aria-label={`${p.steps_done} of ${p.steps_total} steps done`}>
        <span className={`meter-fill tone-bg-${tone}`} style={{ width: `${p.pct}%` }} />
      </span>
      <span className="plan-card-foot">
        <span className="mono">
          {p.steps_done}/{p.steps_total} steps
        </span>
        <span className="mono">
          {p.repos_done}/{p.repos_total} repos
        </span>
        {p.steps_blocking > 0 ? <span className="chip tone-bad">{p.steps_blocking} blocking</span> : null}
        {p.debt_open > 0 ? (
          <span className="chip tone-warn">
            <Wrench size={10} aria-hidden /> {p.debt_open}
          </span>
        ) : null}
        {p.questions_open > 0 ? (
          <span className="chip tone-warn">
            <CircleHelp size={10} aria-hidden /> {p.questions_open}
          </span>
        ) : null}
      </span>
    </button>
  )
}
