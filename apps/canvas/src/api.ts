// The canvas API (packages/python/src/murakumo/server.py). Types cover the parts of the IR the
// canvas reads; `murakumo schema` prints the full JSON Schema.

export type Layer = 'raw' | 'staging' | 'intermediate' | 'marts' | 'semantic'

export type Attribute = {
  id: string
  name: string
  type: string
  nullable: boolean
  key: 'primary' | 'natural' | 'surrogate' | null
  description: string | null
  pii: boolean
  classification: string | null
}

export type Mapping = { target: string; sources: string[]; expression: string | null }

export type Entity = {
  id: string
  name: string
  layer: Layer
  kind: string
  source: string | null
  domain: string | null
  description: string | null
  attributes: Attribute[]
  mappings: Mapping[]
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

export type Domain = { name: string; owner: string | null; description: string | null; entities: number }

export type State = {
  version: string
  revision: string
  project: string
  editable: boolean
  model: { entities: Entity[]; relationships: Relationship[] } | null
  layout: Record<string, Position>
  domains: Domain[]
  sources: SourceInfo[]
  orchestration: Orchestration | null
  diagnostics: Diagnostic[]
  choices: {
    sources: string[]
    layers: Layer[]
    kinds: string[]
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

async function request<T>(method: string, path: string, body?: unknown, track = true): Promise<T> {
  let response: Response
  try {
    response = await fetch(`/api${path}`, {
      method,
      headers: {
        ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
        ...(revision && method !== 'GET' ? { 'X-Murakumo-Revision': revision } : {}),
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

  addEntity: (body: { name: string; layer: Layer; kind?: string; source?: string | null; position?: Position }) =>
    request<State>('POST', '/entities', body),
  updateEntity: (id: string, changes: Record<string, unknown>) => request<State>('PATCH', `/entities/${id}`, changes),
  removeEntity: (id: string) => request<State>('DELETE', `/entities/${id}`),

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

  ci: (provider: string, deploy: boolean) =>
    request<CIInfo>('GET', `/ci?provider=${provider}&deploy=${deploy}`, undefined, false),
  writeCI: (provider: string, deploy: boolean, force: boolean) =>
    request<CIInfo>('POST', '/ci', { provider, deploy, force }, false),
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
}
