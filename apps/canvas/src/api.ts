// The canvas API (packages/python/src/murakumo/server.py). Types cover the parts of the IR the
// canvas reads; `murakumo schema` prints the full JSON Schema.

export type Layer = 'raw' | 'staging' | 'intermediate' | 'marts' | 'semantic'

// A quality rule: what it checks, its settings, and what a failure does (stop the run, warn, or
// set the failing rows aside in <table>_quarantine).
export type RuleType =
  | 'not_null'
  | 'unique'
  | 'accepted_values'
  | 'range'
  | 'regex'
  | 'length'
  | 'custom_sql'
  | 'unique_combination'
  | 'row_count'
  | 'null_share'
  | 'distinct_count'
  | 'recent'
  | 'freshness'
export type RuleAction = 'fail' | 'warn' | 'quarantine'
export type QualityRule = {
  type: RuleType
  columns?: string[]
  severity?: 'error' | 'warn'
  action?: RuleAction | null
  values?: string[] | null
  min?: number | null
  max?: number | null
  sql?: string | null
  pattern?: string | null
  max_age_hours?: number | null
  description?: string | null
}

export type Attribute = {
  id: string
  name: string
  type: string
  nullable: boolean
  key: 'primary' | 'natural' | 'surrogate' | null
  description: string | null
  pii: boolean
  classification: string | null
  example?: string | null
  tests?: QualityRule[]
}

export type Mapping = { target: string; sources: string[]; expression: string | null }

export type Entity = {
  id: string
  name: string
  layer: Layer
  kind: string
  source: string | null
  domain: string | null
  grain: string[]
  cumulative: { date: string; measures: Record<string, string> } | null
  description: string | null
  attributes: Attribute[]
  mappings: Mapping[]
  load: string // full | append | incremental | cdc | streaming: how a raw table takes new rows
  watermark: string | null
  // load: cdc. The column that says what happened to a row, its values that mean deleted, and
  // the column that orders a key's changes.
  cdc: { operation: string | null; deletes: string[]; order: string | null } | null
  incremental_by: 'load_time' | 'watermark'
  lookback_hours: number
  accumulating: { milestones: string[]; unit: 'day' | 'hour' | 'minute' } | null
  unknown_member: boolean | null // null: as the project's modelling.unknown_members says
  conformed: boolean
  history: string // none | scd1 | scd2 | scd3 | scd4 | scd6 | snapshot
  tracked: string[] // the columns scd3 keeps a previous value of, or scd6 a current value of
  access: 'private' | 'protected' | 'public' | null
  // Which rows each role may see (Snowflake): the column that decides, and each role's values.
  row_access: { column: string; allow: Record<string, string[]> } | null
  tests?: QualityRule[] // rules on the whole table
  // Its data contract (ODCS): the version, the status, and whether a load that breaks it stops the build.
  contract?: { version: string; status: string; enforce: 'block' | 'warn' } | null
}

// The query console: where a query runs, what there is to query, and what came back.
export type QueryEngine = { engine: string; label: string }
export type QueryTarget = { label: string; engine: string; stage: string; sql: string; entity: string | null; note: string | null }
// Exploring before modelling: what is loaded, a table before it is built, and what a column joins.
export type RawStatus = {
  tables: Record<string, { exists: boolean; rows: number | null }>
  built?: Record<string, boolean> // each model: whether it is in the warehouse yet
  reason: string | null
  warehouse: string
}
export type Suggested = { title: string; plain: string; sql: string }
export type Estimate = { known: boolean; free?: boolean; bytes?: number; text: string; reads: string[] }
export type RelatedCandidate = {
  entity: string
  column: string
  layer: string
  type: string
  why: string
  strength: 'related' | 'name' | 'maybe'
  unique: boolean
  overlap: {
    here_in_there: number
    there_in_here: number
    sampled: number
    cardinality: string | null
    kind: string
    likely: boolean
    chance: boolean
    shared: boolean
  } | null
}
export type RelatedNames = { entity: string; column: string; canonical: string | null; unique: boolean; candidates: RelatedCandidate[] }
export type RelatedCheck = {
  entity: string
  column: string
  unique: boolean
  matches: RelatedCandidate[]
  ruled_out: (RelatedCandidate & { reason: string })[]
  not_checked: RelatedCandidate[]
  sample: number
  wide: boolean
  others: number
}
export type FoundValue = { value: string; found: { entity: string; column: string }[]; searched: number; skipped: number }

export type QueryResult = {
  engine: string
  columns: string[]
  types: string[]
  rows: unknown[][]
  truncated: boolean
  elapsed_ms: number
  sql: string
}
export type Breach = { table: string; level: 'error' | 'warn'; what: string; message: string }
export type Contracts = {
  standard: string
  enforce_on: string[]
  contracts: Record<string, string>
  check?: { checked: boolean; reason?: string; tables: string[]; breaches: Breach[] }
}

export type Exposure = {
  id?: string
  name: string
  type: 'dashboard' | 'notebook' | 'analysis' | 'ml' | 'application'
  owner: string | null
  email: string | null
  url: string | null
  maturity: 'high' | 'medium' | 'low' | null
  description: string | null
  depends_on: string[]
}

export type Metric = {
  id?: string
  name: string
  label: string | null
  entity: string
  expression: string
  time: string | null
  grain: 'hour' | 'day' | 'week' | 'month' | 'quarter' | 'year' | null
  dimensions: string[]
  filters: string[]
  description: string | null
}

export type BusMatrix = {
  dimensions: { name: string; domain: string | null; conformed: boolean; description: string | null }[]
  facts: { name: string; domain: string | null; kind: string; dimensions: string[] }[]
  problems: { code: string; entity: string; message: string }[]
}

// The replication guide (generated/guide): chapters of steps, each with why, what to run, and what to see.
export type GuideBlock = { lang: string; text: string; title: string | null }
export type GuideTable = { headers: string[]; rows: string[][] }
export type GuideStep = {
  key: string
  title: string
  why: string
  blocks: GuideBlock[]
  expect: string | null
  tables: GuideTable[]
  files: string[]
}
export type GuideChapter = {
  number: number
  slug: string
  title: string
  intro: string
  file: string
  tables: GuideTable[]
  steps: GuideStep[]
}

export type JoinKey = { from: string; to: string }

export type Relationship = {
  id: string
  name: string | null
  from: string
  to: string
  cardinality: string
  optional: boolean
  role: string | null
  description: string | null
  keys: JoinKey[]
}

export type Diagnostic = {
  level: 'error' | 'warning'
  code: string
  message: string
  file: string | null
  where: string | null
  entity_id: string | null
  column_id?: string | null // the column it is about, when it is about one
}

export type Position = { x: number; y: number }

export type SourceInfo = {
  name: string
  type: string | null
  kind: string | null // database, snowflake, csv, excel, file or openapi
  from: string | null // the from: line, without passwords
  declared: boolean
  tables: number
  description: string | null
  updated: string | null
}

export type StackPart = {
  key: string
  title: string
  question: string
  value: string
  options: { value: string; label: string }[] // empty: free text (landing)
}
export type Decision = { at: string; by?: string; what: string; from?: string | null; to?: string | null; why?: string }
// A source's connection, as murakumo.yaml holds it: kind, then that kind's settings.
export type Connection = { kind: string; [key: string]: unknown }
export type StackInfo = {
  parts: StackPart[]
  problems: { level: 'error' | 'warning'; part: string; message: string }[]
  sources: { name: string; connection: Connection | null; tables: string[] }[]
  connected: string[]
  environments: string[]
  decisions: Decision[]
  changed?: string[]
  state?: State
}
export type SecretStatus = { name: string; set: boolean; from: 'file' | 'environment' | null }
export type DynamicsTable = { entity_set: string; logical_name: string | null; label: string; custom: boolean; table: string }
export type DynamicsMade = { table: string; entity_set: string; columns?: number; added?: boolean; watermark?: boolean; error?: string }
export type Pasted = {
  connection: Connection
  endpoint: Record<string, unknown>
  dynamics: boolean
  saved: string[] // the variables the pasted secret was kept in, in the project's .env
}

export type RequestResult = {
  records_at: string | null // where the records were found in the response
  status: number
  milliseconds: number
  headers: Record<string, string>
  records: number
  sample: Record<string, unknown>[]
  response: unknown
}
export type DeployStep = {
  key: string
  title: string
  why: string
  kind: 'sql' | 'file' | 'commands'
  content: string
  role: string | null
}

export type BrowseSchema = {
  name: string
  tables: {
    name: string
    description: string | null
    in_model: boolean
    columns: { name: string; type: string; nullable: boolean; primary_key: boolean }[]
  }[]
}

export type ImportReport = {
  source: string
  added_source: boolean
  added: string[]
  changed: string[]
  removed_columns: string[]
  missing: string[]
  removed: string[]
  added_relationships: string[]
  removed_relationships: string[]
  diagnostics: { level: 'error' | 'warning'; code: string; message: string }[]
  has_changes: boolean
}

export type ScheduleInfo = {
  name: string
  cron: string
  select: string[]
  description: string | null
  when: string // the cron time in words
  next_runs: string[] // ISO times, in the schedule's time zone
  entities: string[] // the entities it refreshes
  rest: boolean // it refreshes whatever no other schedule selects
}

export type Orchestration = {
  timezone: string
  implicit: boolean
  schedules: ScheduleInfo[]
  orchestrator: string
  pipelines: string // where `murakumo generate` writes the orchestrator's pipelines
}

export type Settings = {
  warehouse: string
  orchestrator: string
  pattern: string
  python_engine: string
  plain_sql: boolean
  run_log: boolean
  raw_text: boolean
  base_branch: string
  personal_schemas: boolean
  personal_database: string | null
  environments: { name: string; database: string | null; branch: string | null }[]
  unknown_members: boolean
  guide: boolean
  masking: Record<string, string>
  unmasked_roles: string[]
  lakehouse: Lakehouse | null // which layers are written as open tables, and where
  quality_tools: string[] // great_expectations, soda: also write the rules for these
  reconciliation: boolean
  write_audit_publish: boolean
  contracts_enforce_on: string[] // sources, marts
  choices: { warehouses: string[]; orchestrators: string[]; patterns: string[]; python_engines: string[] }
  changed?: string[]
  state?: State
}

export type Lakehouse = {
  table_format: 'iceberg' | 'delta' | 'hudi' | 'native'
  catalog: 'polaris' | 'unity' | 'glue' | 'nessie' | 'none'
  layers: string[]
  storage: string | null
  catalog_uri: string | null
  warehouse: string | null
  external_volume: string | null
}

// One thing `murakumo setup` checks on this machine, and how to put it right.
export type SetupCheck = {
  area: string
  title: string
  status: 'ok' | 'todo' | 'warn' | 'info'
  detail: string | null
  steps: string[]
  sql: string | null
  sql_where: string | null
  docs: string[]
}

// Suggestions from the assistant (`murakumo ai`): each is a change the canvas could make by hand.
export type Suggestion = {
  kind: 'describe_table' | 'describe_column' | 'classify' | 'mapping' | 'table'
  entity: string
  column: string | null
  value: unknown
  reason: string
  text: string
}

export type AssistantInfo = { enabled: boolean; provider: string; model: string; key_env: string; key_set: boolean }

// What is unusual in the loads and the latest run (`murakumo monitor`).
export type Finding = { level: 'error' | 'warn'; check: 'volume' | 'late' | 'rejected' | 'failed'; subject: string; message: string }
export type Monitor = { recorded: boolean; reason?: string; audit?: string; tables: number; loads?: number; findings: Finding[] }

// Roughly what the schedules cost to run each month (`murakumo cost`).
export type Cost = {
  warehouse: string
  currency: string
  unit: string
  total: number
  lines: { schedule: string; cron: string; runs: number; minutes: number; measured: boolean; units: number; cost: number }[]
  assumptions: string[]
}

export type DbtImport = {
  name: string
  compiled: boolean
  written: boolean
  added: string[]
  added_sources: string[]
  existing: string[]
  skipped: string[]
  mapped: number
  unmapped: number
  relationships: number
  notes: string[]
  state?: State
}

export type RunLog = {
  audit: string
  recorded: boolean
  reason?: string
  days: { day: string; runs: number; rows_ingested: number; rows_rejected: number; models_failed: number; tests_failed: number }[]
  runs: {
    run_id: string
    started_at: string
    finished_at: string | null
    engine: string
    environment: string
    schedule: string | null
    invoked_by: string | null
    status: string
    models_ok: number
    models_failed: number
    tests_passed: number
    tests_failed: number
  }[]
  loads: { loaded_at: string; source: string; table: string; rows_ingested: number; rows_rejected: number; reason: string | null }[]
  failed: { model: string; test: string; column: string | null; failures: number; severity: string }[]
  schedules: Record<string, { started_at: string; finished_at: string | null; status: string }>
}

// Where an environment's builds go, and what each of its schemas holds.
export type EnvInfo = {
  developer: string
  environment: string
  prefix: string
  warehouse: string
  schemas: { schema: string; exists: boolean; tables: number }[]
  reason?: string
}

export type Upload = { name: string; path: string; size: number }

export type JobState = 'running' | 'succeeded' | 'failed' | 'cancelled'

// A long command (build, verify, backfill...) the server runs in the background.
export type Job = {
  id: string
  kind: string
  title: string
  command: string
  state: JobState
  started: number
  finished: number | null
  code: number | null
  next: number // the line to ask for next
  lines: string[]
}

export type ColourLabel = { name: string; colour: string; description?: string | null }
export type Note = { id: string; text: string; x: number; y: number; width: number; colour?: string | null; diagram?: string | null }
export type Diagram = { id: string; name: string; entities: string[] }

// What the canvas draws besides the model, kept in model/layout.yaml with the positions.
export type CanvasSettings = {
  collapse_over?: number
  collapsed?: Record<string, boolean> // per table: true collapsed, false expanded (over collapse_over)
  labels?: ColourLabel[]
  colours?: Record<string, string> // table id -> label name
  notes?: Note[]
  diagrams?: Diagram[]
}

export type Domain = { name: string; owner: string | null; description: string | null; entities: number }

export type State = {
  version: string
  revision: string
  project: string
  editable: boolean
  model: { entities: Entity[]; relationships: Relationship[]; exposures?: Exposure[]; metrics?: Metric[] } | null
  layout: Record<string, Position>
  canvas: CanvasSettings
  domains: Domain[]
  sources: SourceInfo[]
  orchestration: Orchestration | null
  diagnostics: Diagnostic[]
  history: { undo: string | null; redo: string | null } // what undo and redo would take back
  environments: { personal: string; shared: string[] } // everyone's own, then dev, stage, prod...
  message?: string // said by undo and redo
  choices: {
    sources: string[]
    layers: Layer[]
    kinds: string[]
    histories: string[]
    cardinalities: string[]
    classifications: string[]
    types: string[]
  }
}

export type FileStatus = 'added' | 'changed' | 'removed' | 'unchanged'

export type GeneratedFile = { path: string; status: FileStatus; entities: string[]; edited_by_hand: boolean }

export type Migration = {
  number: number
  file: string
  baseline: boolean
  changes: { summary: string; destructive: boolean }[]
  sql: string
}

// What `murakumo generate` and `murakumo migrate` would write now.
export type Generated = {
  warehouse: string
  revision: string
  blocked: string | null
  diagnostics: Diagnostic[]
  files: GeneratedFile[]
  migration: Migration | null
  notes: string[]
}

export type ExpressionCheck = {
  ok: boolean
  error: string | null
  line: number | null
  column: number | null
  references: string[]
  problems: string[]
  type: string | null
  warehouse_sql: string | null
}

export type ExpressionPreview = {
  check: ExpressionCheck
  columns: string[]
  rows: (string | number | boolean | null)[][]
  note: string | null
  error?: string
}

export type LineageNode = { entity: string; column: string; layer: Layer; depth: number }

export type LineageResult = {
  entity: string
  column: string | null
  nodes: LineageNode[]
  links: { source: { entity: string; column: string }; target: { entity: string; column: string }; expression: string | null }[]
  joins: { relationship: string; left: { entity: string; column: string }; right: { entity: string; column: string } }[]
}

export type CIStep = { name: string; command: string; state: string; output: string }
export type CIRun = { running: boolean; steps: CIStep[] }

export type CIInfo = {
  provider: 'github' | 'gitlab'
  file: string
  path: string
  repository: string
  in_git: boolean
  state: 'missing' | 'current' | 'differs'
  content: string
  diff: string
  found: Record<string, boolean>
  deploy_possible: boolean
  preview_possible: boolean
  run: CIRun
}

export type ModelChange = {
  kind: string
  class: 'additive' | 'compatible' | 'breaking'
  entity: string
  column: string | null
  summary: string
  detail: Record<string, unknown>
}

export type GitStatus = {
  branch: string
  base: string
  ref: string
  on_base: boolean
  ahead: number
  behind: number
  uncommitted: string[]
  local_changes: ModelChange[]
  incoming_changes: ModelChange[]
  merging: boolean
  conflicts: string[]
  developer: string
  personal_environment: string
  personal_prefix: string // where this person's dev builds go; empty for the shared schemas
  personal_schema: string | null // their own schema, on a shared warehouse
  personal_where: string // in words
  flow: { environment: string; branch: string }[] // dev, stage, prod with the branch that deploys each
}

export type SyncResult = {
  state: 'up_to_date' | 'synced' | 'conflicts'
  incoming: ModelChange[]
  conflicts: string[]
  migrations: string[]
  generated: number
  commit: string | null
}

export type EntityCode = {
  blocked: string | null
  files: { path: string; status: FileStatus; content: string }[]
  ddl: string | null
}

export type ImpactReport = {
  worst: 'additive' | 'compatible' | 'breaking' | null
  impacts: { change: ModelChange; columns: string[]; tables: string[]; tests: string[]; metrics: string[]; schedules: string[] }[]
  files: string[]
  migration: string[]
}

export class ApiError extends Error {
  status: number
  state?: State

  constructor(status: number, message: string, state?: State) {
    super(message)
    this.status = status
    this.state = state
  }
}

// The revision of the state the canvas shows. Changes send it, so the server can refuse a change
// made against files that have since changed on disk.
let revision: string | undefined

// While true, edits are sent as previews: the server says what they would reach and saves nothing.
let previewing = false

async function request<T>(method: string, path: string, body?: unknown, track = true): Promise<T> {
  let response: Response
  try {
    response = await fetch(`/api${path}`, {
      method,
      headers: {
        ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
        ...(revision && method !== 'GET' ? { 'X-Murakumo-Revision': revision } : {}),
        ...(previewing ? { 'X-Murakumo-Preview': '1' } : {}),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new ApiError(0, 'Cannot reach the Murakumo server. Is `murakumo canvas` running?')
  }
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    if (data.state) revision = data.state.revision
    throw new ApiError(response.status, data.message ?? response.statusText, data.state)
  }
  if (track && data.revision) revision = data.revision
  return data as T
}

export const api = {
  state: () => request<State>('GET', '/state'),
  revision: () => request<{ revision: string; generated: string }>('GET', '/revision', undefined, false),
  currentRevision: () => revision,
  undo: () => request<State>('POST', '/undo'),
  settings: () => request<Settings>('GET', '/settings', undefined, false),
  saveSettings: (changes: Partial<Settings>) => request<Settings>('PUT', '/settings', changes, false),
  startFlow: () => request<{ done: string[] }>('POST', '/git/start', undefined, false),
  newBranch: (kind: string, name: string) => request<{ branch: string; state: State }>('POST', '/git/branch', { kind, name }),
  promotion: (branch: string) =>
    request<{ from: string; to: string; from_environment: string; to_environment: string; commits: number; changes: ModelChange[]; url: string | null }>(
      'GET',
      `/git/promotion?branch=${encodeURIComponent(branch)}`,
      undefined,
      false,
    ),
  provision: () => request<{ sql: string }>('GET', '/env/provision', undefined, false),
  setup: (connect = false) =>
    request<{ ready: boolean; checks: SetupCheck[] }>('GET', `/setup?connect=${connect}`, undefined, false),
  uploads: () => request<{ files: Upload[] }>('GET', '/uploads', undefined, false),
  deleteUpload: (name: string) => request<{ files: Upload[] }>('DELETE', `/uploads/${encodeURIComponent(name)}`, undefined, false),
  /** Add a file to the project's data/ folder (ignored by git), sent as its bytes. */
  upload: async (file: File): Promise<Upload> => {
    const name = file.name.replace(/[^A-Za-z0-9._-]+/g, '_').replace(/^\.+/, '')
    let response: Response
    try {
      response = await fetch(`/api/uploads/${encodeURIComponent(name)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/octet-stream' },
        body: file,
      })
    } catch {
      throw new ApiError(0, 'Cannot reach the Murakumo server. Is `murakumo canvas` running?')
    }
    const data = await response.json().catch(() => ({}))
    if (!response.ok) throw new ApiError(response.status, data.message ?? response.statusText)
    return data as Upload
  },
  env: (environment?: string, prefix?: string) =>
    request<EnvInfo>(
      'GET',
      `/env?${new URLSearchParams({ ...(environment ? { environment } : {}), ...(prefix ? { prefix } : {}) })}`,
      undefined,
      false,
    ),
  diff: (ref: string) =>
    request<ImpactReport & { against: string; markdown: string }>('GET', `/diff?ref=${encodeURIComponent(ref)}`, undefined, false),
  monitor: (environment?: string) =>
    request<Monitor>('GET', `/monitor${environment ? `?environment=${encodeURIComponent(environment)}` : ''}`, undefined, false),
  cost: () => request<Cost>('GET', '/cost', undefined, false),
  catalog: (format: 'json' | 'openlineage') => request<unknown>('GET', `/catalog?format=${format}`, undefined, false),
  assistant: () => request<AssistantInfo>('GET', '/assistant', undefined, false),
  saveAssistant: (changes: Partial<Pick<AssistantInfo, 'enabled' | 'model' | 'key_env'>>) =>
    request<AssistantInfo>('PUT', '/assistant', changes, false),
  suggest: (kind: string, options: { entities?: string[]; entity?: string; request?: string }) =>
    request<{ suggestions: Suggestion[] }>('POST', '/assistant/suggest', { kind, ...options }, false),
  applySuggestions: (suggestions: Suggestion[]) =>
    request<{ applied: string[]; refused: { text: string; why: string }[]; state: State }>('POST', '/assistant/apply', { suggestions }),
  importDbt: (folder: string, dryRun: boolean) => request<DbtImport>('POST', '/import/dbt', { folder, dry_run: dryRun }),
  queryTargets: (environment?: string) =>
    request<{ engines: QueryEngine[]; targets: QueryTarget[]; limit: number }>(
      'GET',
      `/query/targets${environment ? `?environment=${encodeURIComponent(environment)}` : ''}`,
      undefined,
      false,
    ),
  query: (sql: string, engine: string, options: { environment?: string; limit?: number } = {}) =>
    request<QueryResult>('POST', '/query', { sql, engine, ...options }, false),
  exploreStatus: (environment?: string) =>
    request<RawStatus>('GET', `/explore/status${environment ? `?environment=${encodeURIComponent(environment)}` : ''}`, undefined, false),
  previewTable: (entity: string, full = false) =>
    request<QueryResult & { sampled: boolean; sample: number | null; reads: string[] }>('POST', '/explore/preview', { entity, full }, false),
  estimate: (entity: string) => request<Estimate>('POST', '/explore/estimate', { entity }, false),
  suggested: (entity: string) =>
    request<{ queries: Suggested[] }>('GET', `/explore/suggested?entity=${encodeURIComponent(entity)}`, undefined, false),
  relatedNames: (entity: string, column: string) =>
    request<RelatedNames>(
      'GET',
      `/explore/relations?${new URLSearchParams({ entity, column })}`,
      undefined,
      false,
    ),
  relatedCheck: (entity: string, column: string, wide = false) =>
    request<RelatedCheck>('POST', '/explore/relations', { entity, column, wide }, false),
  findValue: (value: string, everyColumn = false) =>
    request<FoundValue>('POST', '/explore/find', { value, every_column: everyColumn }, false),
  contracts: (check = false, environment?: string) =>
    request<Contracts>(
      'GET',
      `/contracts?${new URLSearchParams({ check: String(check), ...(environment ? { environment } : {}) })}`,
      undefined,
      false,
    ),
  importOdcs: (path: string, dryRun: boolean, source?: string) =>
    request<{ tables: { entity: string; added: string[]; rules: number; different: string[]; new: boolean }[]; written: boolean; state?: State }>(
      'POST',
      '/import/odcs',
      { path, dry_run: dryRun, ...(source ? { source } : {}) },
    ),
  runs: (environment?: string) =>
    request<RunLog>('GET', `/runs${environment ? `?environment=${encodeURIComponent(environment)}` : ''}`, undefined, false),
  startJob: (kind: string, options: Record<string, unknown> = {}) => request<Job>('POST', '/jobs', { kind, options }, false),
  jobs: () => request<{ jobs: Job[] }>('GET', '/jobs', undefined, false),
  job: (id: string, since = 0) => request<Job>('GET', `/jobs/${id}?since=${since}`, undefined, false),
  cancelJob: (id: string) => request<Job>('POST', `/jobs/${id}/cancel`, undefined, false),
  saveCanvas: (settings: CanvasSettings & { label?: string }) => request<State>('PUT', '/canvas', settings),
  redo: () => request<State>('POST', '/redo'),

  addEntity: (body: { name: string; layer: Layer; kind?: string; source?: string | null; position?: Position }) =>
    request<State>('POST', '/entities', body),
  updateEntity: (id: string, changes: Record<string, unknown>) => request<State>('PATCH', `/entities/${id}`, changes),
  removeEntity: (id: string) => request<State>('DELETE', `/entities/${id}`),
  updateEntities: (ids: string[], changes: Record<string, unknown>) => request<State>('PATCH', '/entities', { ids, changes }),
  removeEntities: (ids: string[]) => request<State>('POST', '/entities/remove', { ids }),
  copyColumns: (entityId: string, columns: string[]) =>
    request<State>('POST', `/entities/${entityId}/columns/copy`, { columns }),

  addColumn: (entityId: string, body: { name: string; type: string; primary_key?: boolean }) =>
    request<State>('POST', `/entities/${entityId}/columns`, body),
  updateColumn: (entityId: string, columnId: string, changes: Record<string, unknown>) =>
    request<State>('PATCH', `/entities/${entityId}/columns/${columnId}`, changes),
  removeColumn: (entityId: string, columnId: string) =>
    request<State>('DELETE', `/entities/${entityId}/columns/${columnId}`),
  orderColumns: (entityId: string, columns: string[]) =>
    request<State>('PUT', `/entities/${entityId}/columns/order`, { columns }),

  addRelationship: (body: { from: string; to: string; keys: JoinKey[]; cardinality?: string }) =>
    request<State>('POST', '/relationships', body),
  updateRelationship: (id: string, changes: Record<string, unknown>) =>
    request<State>('PATCH', `/relationships/${id}`, changes),
  removeRelationship: (id: string) => request<State>('DELETE', `/relationships/${id}`),

  addSource: (body: { name: string; kind: string; location: string }) => request<State>('POST', '/sources', body),
  removeSource: (name: string) => request<State>('DELETE', `/sources/${encodeURIComponent(name)}`),
  browseSource: (name: string) =>
    request<{ schemas: BrowseSchema[] }>('GET', `/sources/${encodeURIComponent(name)}/browse`, undefined, false),
  previewImport: (name: string, tables: string[] | null) =>
    request<{ reports: ImportReport[] }>('POST', `/sources/${encodeURIComponent(name)}/import/preview`, { tables }, false),
  importSource: (name: string, tables: string[] | null) =>
    request<State>('POST', `/sources/${encodeURIComponent(name)}/import`, { tables }),

  addSchedule: (body: { name: string; cron: string; select: string[]; description?: string | null }) =>
    request<State>('POST', '/schedules', body),
  updateSchedule: (name: string, changes: Record<string, unknown>) =>
    request<State>('PATCH', `/schedules/${encodeURIComponent(name)}`, changes),
  removeSchedule: (name: string) => request<State>('DELETE', `/schedules/${encodeURIComponent(name)}`),
  setTimezone: (timezone: string) => request<State>('PUT', '/orchestration/timezone', { timezone }),

  addDomain: (body: { name: string; owner?: string | null; description?: string | null }) =>
    request<State>('POST', '/domains', body),
  updateDomain: (name: string, changes: Record<string, unknown>) =>
    request<State>('PATCH', `/domains/${encodeURIComponent(name)}`, changes),
  removeDomain: (name: string) => request<State>('DELETE', `/domains/${encodeURIComponent(name)}`),

  saveLayout: (positions: Record<string, Position>) =>
    request<{ revision: string }>('PUT', '/layout', { positions }),

  setMapping: (entityId: string, columnId: string, body: { sources: string[]; expression: string | null }) =>
    request<State>('PUT', `/entities/${entityId}/mappings/${columnId}`, body),

  checkExpression: (expression: string, sources: string[] = [], entity: string | null = null) =>
    request<ExpressionCheck>('POST', '/expressions/check', { expression, sources, entity }, false),
  previewExpression: (expression: string, sources: string[] = [], entity: string | null = null) =>
    request<ExpressionPreview>('POST', '/expressions/preview', { expression, sources, entity }, false),
  addCalculatedColumn: (entityId: string, body: { name: string; expression: string; type?: string | null }) =>
    request<State>('POST', `/entities/${entityId}/calculated`, body),

  lineage: (entity: string, column: string | null) =>
    request<LineageResult>(
      'GET',
      `/lineage?entity=${encodeURIComponent(entity)}${column ? `&column=${encodeURIComponent(column)}` : ''}`,
      undefined,
      false,
    ),

  ci: (provider: string, deploy: boolean, preview: boolean) =>
    request<CIInfo>('GET', `/ci?provider=${provider}&deploy=${deploy}&preview=${preview}`, undefined, false),
  writeCI: (provider: string, deploy: boolean, preview: boolean, force: boolean) =>
    request<CIInfo>('POST', '/ci', { provider, deploy, preview, force }, false),
  startCIRun: () => request<CIRun>('POST', '/ci/run', undefined, false),
  ciRun: () => request<CIRun>('GET', '/ci/run', undefined, false),

  git: (refresh = false) => request<GitStatus>('GET', `/git?refresh=${refresh}`, undefined, false),
  sync: (resume = false) =>
    request<{ sync: SyncResult; state: State }>('POST', `/git/sync?resume=${resume}`, undefined, false),
  abortSync: () => request<{ state: State }>('POST', '/git/abort', undefined, false),
  commit: (message: string) => request<{ commit: string }>('POST', '/git/commit', { message }, false),

  generated: () => request<Generated>('GET', '/generated', undefined, false),
  generatedFile: (path: string) =>
    request<{ path: string; content: string | null; diff: string }>(
      'GET',
      `/generated/file?path=${encodeURIComponent(path)}`,
      undefined,
      false,
    ),
  entityCode: (id: string) => request<EntityCode>('GET', `/entities/${id}/code`, undefined, false),
  generate: () => request<Generated>('POST', '/generate', undefined, false),
  writeMigration: (name: string | null) => request<Generated>('POST', '/migrations', { name }, false),

  stack: () => request<StackInfo>('GET', '/stack', undefined, false),
  saveStack: (values: Record<string, string>, why: string | null) =>
    request<StackInfo>('PUT', '/stack', { values, why }, false),
  saveConnection: (name: string, connection: Connection | null, why: string | null) =>
    request<StackInfo>('PUT', `/sources/${encodeURIComponent(name)}/connection`, { connection, why }, false),
  tryRequest: (connection: Connection, endpoint: Record<string, unknown>) =>
    request<RequestResult>('POST', '/sources/request', { connection, endpoint }, false),
  secrets: (names: string[]) =>
    request<{ secrets: SecretStatus[]; file: string }>('GET', `/secrets?names=${encodeURIComponent(names.join(','))}`, undefined, false),
  setSecret: (name: string, value: string) =>
    request<{ secrets: SecretStatus[] }>('PUT', `/secrets/${encodeURIComponent(name)}`, { value }, false),
  dynamicsStatus: (source: string) =>
    request<{ url: string; tenant: string; secrets: SecretStatus[]; tables: string[] }>(
      'GET',
      `/sources/${encodeURIComponent(source)}/dynamics`,
      undefined,
      false,
    ),
  connectDynamics: (source: string, body: { url: string; tenant: string; client_id?: string; client_secret?: string }) =>
    request<State & { tables: DynamicsTable[]; fetching: string[] }>('POST', `/sources/${encodeURIComponent(source)}/dynamics`, body),
  dynamicsTables: (source: string, tables: { entity_set: string; logical_name: string | null }[]) =>
    request<State & { made: DynamicsMade[] }>('POST', `/sources/${encodeURIComponent(source)}/dynamics/tables`, { tables }),
  pasteRequest: (source: string, text: string) =>
    request<Pasted>('POST', `/sources/${encodeURIComponent(source)}/paste`, { text }, false),
  testSource: (name: string) =>
    request<{ lines: string[] }>('POST', `/sources/${encodeURIComponent(name)}/test`, undefined, false),
  tableFromRecords: (body: { source: string; table: string; records: Record<string, unknown>[]; path?: string | null }) =>
    request<State>('POST', '/sources/table-from-records', body),
  deployPlan: (env: string) =>
    request<{ steps: DeployStep[] }>('GET', `/stack/deploy?env=${encodeURIComponent(env)}`, undefined, false),
  decisions: () => request<{ decisions: Decision[] }>('GET', '/decisions', undefined, false),
  addDecision: (what: string, why: string | null) =>
    request<{ decisions: Decision[] }>('POST', '/decisions', { what, why }, false),
  guide: () => request<{ chapters: GuideChapter[]; folder: string }>('GET', '/guide', undefined, false),
  bus: () => request<BusMatrix>('GET', '/bus', undefined, false),
  saveExposures: (exposures: Exposure[]) => request<State>('PUT', '/exposures', { exposures }),
  saveMetrics: (metrics: Metric[]) => request<State>('PUT', '/metrics', { metrics }),
}

/** What an edit would reach, without making it: the edit is sent as a preview. */
export async function previewOf(action: () => Promise<unknown>): Promise<ImpactReport | null> {
  previewing = true
  try {
    const result = (await action()) as { preview?: ImpactReport }
    return result.preview ?? null
  } finally {
    previewing = false
  }
}

/** The words for a confirmation before a breaking edit. */
export function impactText(report: ImpactReport): string {
  const lines = report.impacts
    .filter((i) => i.change.class === 'breaking')
    .map((i) => {
      const reach = [
        i.columns.length ? `${i.columns.length} column${i.columns.length === 1 ? '' : 's'} built from it` : '',
        i.tests.length ? `${i.tests.length} test${i.tests.length === 1 ? '' : 's'}` : '',
        i.schedules.length ? `schedule ${i.schedules.join(', ')}` : '',
      ].filter(Boolean)
      return `• ${i.change.summary}${reach.length ? `\n    reaches ${reach.join(', ')}` : ''}`
    })
  const tables = [...new Set(report.impacts.flatMap((i) => i.tables))]
  return (
    `This is a breaking change: something that reads ${tables.join(', ')} may fail or change meaning.\n\n` +
    lines.join('\n') +
    (report.migration.length ? `\n\nIt needs a migration: ${report.migration.join('; ')}.` : '') +
    '\n\nGo ahead?'
  )
}
