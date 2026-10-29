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

export type State = {
  version: string
  revision: string
  project: string
  editable: boolean
  model: { entities: Entity[]; relationships: Relationship[] } | null
  layout: Record<string, Position>
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

  saveLayout: (positions: Record<string, Position>) =>
    request<{ revision: string }>('PUT', '/layout', { positions }),

  setMapping: (entityId: string, columnId: string, body: { sources: string[]; expression: string | null }) =>
    request<State>('PUT', `/entities/${entityId}/mappings/${columnId}`, body),

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
