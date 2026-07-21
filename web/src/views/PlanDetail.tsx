import { ArrowLeft, CheckCircle2, Copy, GitCommitHorizontal, LoaderCircle, RefreshCw } from 'lucide-react'
import { useState } from 'react'
import { completePlan, fetchGit, fetchPlan, useAsync } from '../api'
import { GraphPanel } from '../components/GraphPanel'
import { LevelIcon, ToneIcon } from '../components/ToneIcon'
import type { GitOverlay, SectionItem } from '../types'

const SECTION_TITLES: Record<string, string> = {
  references: 'References',
  repos: 'Repos and merge order',
  steps: 'Steps',
  decisions: 'Decisions',
  tech_debt: 'Tech debt',
  open_questions: 'Open questions',
}

const HIDE_KEYS = new Set(['status', 'what', 'repo', 'id', 'order'])

export function PlanDetailView({ id, digest, go }: { id: string; digest: string | null; go: (to: string) => void }) {
  const { data, error, loading, reload } = useAsync(() => fetchPlan(id), [id, digest])
  const [section, setSection] = useState<string>('steps')
  const [completing, setCompleting] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [actionMessage, setActionMessage] = useState<string | null>(null)

  if (error) return <div className="view"><section className="panel"><p className="empty tone-bad">{error}</p></section></div>
  if (loading || !data) return <div className="view"><section className="panel"><p className="empty">Loading {id}...</p></section></div>

  const { plan, graphs } = data
  const p = plan.progress
  const available = Object.entries(plan.sections).filter(([, items]) => items.length > 0)

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

  return (
    <div className="view">
      <header className="plan-head">
        <button className="link" onClick={() => go('#/plans')}>
          <ArrowLeft size={13} aria-hidden /> All plans
        </button>
        <h1 className="mono">{plan.id}</h1>
        <span className={`chip ${plan.area === 'active' ? 'tone-active' : 'tone-ok'}`}>{plan.area}</span>
        <span className="plan-path mono">{plan.path}</span>
        {plan.area === 'active' ? (
          <button className="tool plan-complete-action" onClick={moveToDone} disabled={completing}>
            {completing ? <LoaderCircle size={15} className="spin" aria-hidden /> : <CheckCircle2 size={15} aria-hidden />}
            {completing ? 'Moving...' : 'Move to done'}
          </button>
        ) : null}
      </header>

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

      <p className="plan-goal">{plan.goal}</p>

      <div className="plan-metrics">
        <Metric label="steps" done={p.steps_done} total={p.steps_total} tone={p.steps_blocking ? 'bad' : 'ok'} />
        <Metric label="repos" done={p.repos_done} total={p.repos_total} tone="ok" />
        <Metric label="debt closed" done={p.debt_total - p.debt_open} total={p.debt_total} tone="warn" />
        <Metric label="questions answered" done={p.questions_total - p.questions_open} total={p.questions_total} tone="warn" />
      </div>

      <GraphPanel
        title="Repo merge order"
        hint="An edge means the source has to be on its base branch before the target merges. Merging out of this order is the failure this plan exists to prevent."
        graph={graphs.repos}
        edgeLegend={['declared', 'inferred']}
      />

      <GraphPanel
        title="Step order"
        hint="Built from depends_on, depends_on_step, blocked_by and blocks. Where a plan declares none of those, the order is inferred from repo merge order and step numbering, and the panel says so."
        graph={graphs.steps}
      />

      <GitCheck id={id} digest={digest} />

      <section className="panel">
        <header className="panel-head">
          <div className="panel-title">
            <h2>Plan contents</h2>
          </div>
          <div className="segmented segmented-scroll" role="group" aria-label="Plan section">
            {available.map(([name, items]) => (
              <button key={name} data-on={section === name || undefined} onClick={() => setSection(name)} aria-pressed={section === name}>
                {SECTION_TITLES[name] ?? name}
                <span className="mono">{items.length}</span>
              </button>
            ))}
          </div>
        </header>
        <div className="items">
          {(plan.sections[section] ?? []).map((item) => (
            <Item key={item.index} item={item} plan={plan.id} section={section} />
          ))}
          {(plan.sections[section] ?? []).length === 0 ? <p className="empty">Nothing in this section.</p> : null}
        </div>
      </section>
    </div>
  )
}

function Metric({ label, done, total, tone }: { label: string; done: number; total: number; tone: string }) {
  const pct = total ? Math.round((done * 100) / total) : 0
  return (
    <article className="metric">
      <span className="metric-value mono">
        {done}
        <span className="cell-dim">/{total}</span>
      </span>
      <span className="metric-label">{label}</span>
      <span className="meter">
        <span className={`meter-fill tone-bg-${total && pct === 100 ? 'ok' : tone}`} style={{ width: `${pct}%` }} />
      </span>
    </article>
  )
}

function Item({ item, plan, section }: { item: SectionItem; plan: string; section: string }) {
  const [copied, setCopied] = useState(false)
  const key = item.raw.id ?? item.raw.order ?? item.index
  const command =
    section === 'steps'
      ? `evo harness step ${plan} ${key} done`
      : section === 'tech_debt'
        ? `evo harness debt ${plan} ${item.index} fixed`
        : section === 'open_questions'
          ? `evo harness question ${plan} ${item.index} answered`
          : section === 'repos'
            ? `evo harness repo ${plan} ${item.index} merged`
            : null

  const copy = async () => {
    if (!command) return
    await navigator.clipboard.writeText(command)
    setCopied(true)
    setTimeout(() => setCopied(false), 1800)
  }

  const fields = Object.entries(item.raw).filter(
    ([name, value]) => !HIDE_KEYS.has(name) && value !== null && value !== '' && value !== undefined,
  )

  return (
    <article className="item" data-tone={item.tone}>
      <header>
        <span className={`tone-${item.tone}`}>
          <ToneIcon tone={item.tone} />
        </span>
        <span className="item-key mono">{String(key)}</span>
        <span className="item-title">{item.title}</span>
        {item.status ? <span className={`chip tone-${item.tone}`}>{item.status}</span> : null}
        {item.raw.blocking ? <span className="chip tone-bad">blocking</span> : null}
        {command ? (
          <button className="icon-btn" onClick={copy} title={command} aria-label={`Copy: ${command}`}>
            {copied ? <span className="tone-ok mono">copied</span> : <Copy size={13} aria-hidden />}
          </button>
        ) : null}
      </header>
      <dl className="item-fields">
        {fields.map(([name, value]) => (
          <div key={name}>
            <dt>{name.replace(/_/g, ' ')}</dt>
            <dd className={name === 'verify' || name === 'where' ? 'mono' : undefined}>
              {Array.isArray(value) ? value.join(', ') : String(value)}
            </dd>
          </div>
        ))}
      </dl>
    </article>
  )
}

function GitCheck({ id, digest }: { id: string; digest: string | null }) {
  const [refetch, setRefetch] = useState(false)
  const { data, error, loading, reload } = useAsync<GitOverlay>(() => fetchGit(id, refetch), [id, digest, refetch])

  return (
    <section className="panel">
      <header className="panel-head">
        <div className="panel-title">
          <h2>Plan versus git</h2>
          {data ? (
            <span className="panel-meta mono">
              {data.errors} wrong / {data.warnings} warnings / {data.unknown} uncheckable
            </span>
          ) : null}
        </div>
        <button
          className="tool"
          onClick={() => {
            setRefetch(true)
            reload()
          }}
          disabled={loading}
        >
          <RefreshCw size={13} className={loading ? 'spin' : undefined} aria-hidden />
          {loading ? 'checking' : 'git fetch and recheck'}
        </button>
      </header>

      {error ? <p className="empty tone-bad">{error}</p> : null}
      {!data ? (
        <p className="empty">Reading git...</p>
      ) : (
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
                  <li key={index} className={`tone-${verdict.level === 'error' ? 'bad' : verdict.level === 'warn' ? 'warn' : verdict.level === 'ok' ? 'ok' : 'idle'}`}>
                    <LevelIcon level={verdict.level} />
                    <span>{verdict.text}</span>
                  </li>
                ))}
              </ul>
            </article>
          ))}
        </div>
      )}
    </section>
  )
}
