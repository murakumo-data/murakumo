import type { Connection } from './api'

// Small rules the connection screen follows, kept apart so they can be tested.

type Obj = Record<string, unknown>
const text = (v: unknown) => (v === undefined || v === null ? '' : String(v))

/** Drop empty values, so murakumo.yaml holds only what was set. */
export function clean(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(clean)
  if (value && typeof value === 'object') {
    const out: Obj = {}
    for (const [k, v] of Object.entries(value as Obj)) {
      const c = clean(v)
      if (c === '' || c === undefined || c === null) continue
      if (typeof c === 'object' && !Array.isArray(c) && Object.keys(c as Obj).length === 0 && k !== 'params') continue
      out[k] = c
    }
    return out
  }
  return value
}

/** The variable a source's secret is kept in: CRM_CLIENT_SECRET (as Murakumo names it). */
export function variableName(source: string, what: string): string {
  let stem = source.replace(/[^A-Za-z0-9]+/g, '_').replace(/^_+|_+$/g, '').toUpperCase() || 'SOURCE'
  if (/^\d/.test(stem)) stem = `S_${stem}`
  return `${stem}_${what.toUpperCase()}`
}

/** A Dynamics connection, as Murakumo writes one. */
export function isDynamics(connection: Connection | null): boolean {
  if (!connection) return false
  const paging = (connection.paging as Obj | undefined) ?? {}
  return text(connection.base_url).endsWith('/api/data/v9.2') && paging.next_path === '@odata.nextLink'
}

