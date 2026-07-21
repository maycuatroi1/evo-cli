export type Tone = 'ok' | 'active' | 'warn' | 'bad' | 'idle'
export type Level = 'ok' | 'info' | 'warn' | 'error' | 'unknown'

export interface Warning {
  level: Level
  text: string
}

export interface GraphNode {
  id: string
  label: string
  kind: string
  tone: Tone
  rank: number
  inCycle: boolean
  meta: Record<string, unknown>
}

export interface GraphEdge {
  id: string
  source: string
  target: string
  label: string
  kind: string
  tone: Tone
  dashed: boolean
  inCycle: boolean
  meta: Record<string, unknown>
}

export interface Graph {
  nodes: GraphNode[]
  edges: GraphEdge[]
  cycles: string[][]
  acyclic: boolean
  depth: number
  warnings: Warning[]
}

export interface Repo {
  name: string
  path: string
  present: boolean
  role: string
  branch: string
  origin: string
  note: string
}

export interface Principle {
  name: string
  path: string
  body: string
}

export interface Cluster {
  name: string
  root: string
  manifest: string
  created_at: string
  workspace: string
  repos: Repo[]
  principles: Principle[]
  proposals_pending: string[]
  has_contracts: boolean
  has_plans: boolean
}

export interface SeamKey {
  key?: string
  consumer?: string
  path?: string
  access?: string
  note?: string
}

export interface Seam {
  index: number
  name: string
  kind: string
  owner: string
  consumers: string[]
  source: string
  artifacts: string[]
  mirrors: string[]
  verify: string
  blocking: boolean
  remedy: string
  notes: string
  keys: SeamKey[]
}

export interface DeploymentTenant {
  id: string
  code: string
  name: string
  aliases: string[]
}

export interface Deployment {
  index: number
  deploymentId: string
  product: string
  tenantId: string
  environment: string
  kind: string
  webUrl: string
  apiUrl: string
  authUrl: string
  capabilities: string[]
  status: string
  aliases: string[]
}

export interface Deployments {
  version: number
  configVersion: string
  environments: string[]
  tenants: DeploymentTenant[]
  deployments: Deployment[]
}

export interface Progress {
  steps_total: number
  steps_done: number
  steps_active: number
  steps_blocking: number
  pct: number
  repos_total: number
  repos_done: number
  debt_open: number
  debt_total: number
  questions_open: number
  questions_total: number
}

export interface PlanSummary {
  id: string
  area: string
  goal: string
  created_at: string
  path: string
  mtime: number
  progress: Progress
}

export interface SectionItem {
  index: number
  title: string
  status: string | null
  tone: Tone
  order: number | null
  raw: Record<string, unknown>
}

export interface PlanDetail extends PlanSummary {
  sections: Record<string, SectionItem[]>
  extra: Record<string, unknown>
}

export interface State {
  digest: string
  generatedAt: number
  cluster: Cluster
  seams: Seam[]
  seamGraph: Graph
  deployments: Deployments
  plans: PlanSummary[]
}

export interface PlanPayload {
  plan: PlanDetail
  graphs: { repos: Graph; steps: Graph }
}

export interface Verdict {
  level: Level
  text: string
}

export interface RepoOverlay {
  repo: string
  branch: string
  status: string
  tone: Tone
  present: boolean
  path: string
  verdicts: Verdict[]
  commits: { text: string; sha: string | null; exists: boolean; in_head: boolean; in_base: boolean }[]
  current_branch?: string
  dirty?: boolean
  head?: string
  base_ref?: string | null
  merged?: boolean
  ahead?: number
  behind?: number
  last_fetch?: number | null
}

export interface GitOverlay {
  plan: string
  checked_at: number
  fetched: boolean
  repos: RepoOverlay[]
  errors: number
  warnings: number
  unknown: number
}
