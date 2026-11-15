import { Plus, Send, Trash2 } from 'lucide-react'
import { useState } from 'react'

import { api, type Connection, type RequestResult, type State } from './api'

type Run = (action: () => Promise<State>) => Promise<boolean>

// A source's connection: how its data is fetched (`murakumo source connect`). For a REST API this
// is a request builder too: set the auth and paging once, send each endpoint's request, see what
// came back, and make the raw table from the records. Secrets are never typed here: each field
// names the environment variable that holds one, read on this machine when a request is sent.

type Field = { key: string; label: string; placeholder?: string; type?: 'number' | 'select'; options?: string[]; help?: string }

const KINDS: { value: string; label: string; help: string }[] = [
  { value: 'rest', label: 'REST API', help: 'Requests with auth and paging; each endpoint’s records become a raw table.' },
  { value: 'sftp', label: 'SFTP', help: 'Files on an SFTP server, copied as they appear.' },
  { value: 'database', label: 'Database', help: 'Tables or queries in a source database, copied by an incremental column.' },
  { value: 'files', label: 'Folder or S3', help: 'Files in a folder or an S3 prefix, copied as they appear.' },
  { value: 'protobuf', label: 'Protobuf', help: 'Files of length-delimited protobuf messages, decoded with the .proto file.' },
  {
    value: 'kafka',
    label: 'Kafka',
    help: 'Topics of JSON messages (Kafka, Redpanda, MSK, Confluent): each topic becomes a raw table. Stack → Streaming says what reads them.',
  },
]

const LISTS: Record<string, { key: string; noun: string; fields: Field[] }> = {
  sftp: {
    key: 'files',
    noun: 'file set',
    fields: [
      { key: 'table', label: 'Raw table' },
      { key: 'pattern', label: 'Files', placeholder: 'trips_*.csv' },
      { key: 'format', label: 'Format', type: 'select', options: ['', 'csv', 'tsv', 'json', 'jsonl', 'parquet', 'xlsx'] },
    ],
  },
  files: {
    key: 'files',
    noun: 'file set',
    fields: [
      { key: 'table', label: 'Raw table' },
      { key: 'pattern', label: 'Files', placeholder: 'trips_*.parquet' },
      { key: 'format', label: 'Format', type: 'select', options: ['', 'csv', 'tsv', 'json', 'jsonl', 'parquet', 'xlsx'] },
    ],
  },
  database: {
    key: 'tables',
    noun: 'table',
    fields: [
      { key: 'table', label: 'Raw table' },
      { key: 'source', label: 'From', placeholder: 'public.orders, or a SELECT' },
      { key: 'incremental_column', label: 'New rows by', placeholder: 'updated_at (default: the table’s watermark)' },
    ],
  },
  kafka: {
    key: 'topics',
    noun: 'topic',
    fields: [
      { key: 'table', label: 'Raw table' },
      { key: 'topic', label: 'Topic', placeholder: 'orders' },
    ],
  },
  protobuf: {
    key: 'messages',
    noun: 'message type',
    fields: [
      { key: 'table', label: 'Raw table' },
      { key: 'message', label: 'Message', placeholder: 'Order' },
      { key: 'pattern', label: 'Files', placeholder: 'orders_*.bin' },
    ],
  },
}

const TOP: Record<string, Field[]> = {
  sftp: [
    { key: 'host', label: 'Host', placeholder: 'sftp.partner.com' },
    { key: 'port', label: 'Port', type: 'number', placeholder: '22' },
    { key: 'user_env', label: 'User from', placeholder: 'PARTNER_SFTP_USER', help: 'An environment variable.' },
    { key: 'key_file_env', label: 'Key file from', placeholder: 'PARTNER_SFTP_KEY_FILE', help: 'The variable holding the private key file’s path.' },
    { key: 'password_env', label: 'Or password from', placeholder: 'PARTNER_SFTP_PASSWORD' },
    { key: 'path', label: 'Folder', placeholder: '/outbound' },
  ],
  database: [{ key: 'url_env', label: 'URL from', placeholder: 'CRM_DB_URL', help: 'The variable holding the SQLAlchemy URL.' }],
  files: [{ key: 'location', label: 'Location', placeholder: 'incoming/ or s3://bucket/prefix' }],
  kafka: [
    { key: 'bootstrap_servers', label: 'Brokers', placeholder: 'localhost:19092', help: 'host:port, comma-separated.' },
    { key: 'security_protocol', label: 'Security', placeholder: 'PLAINTEXT, SSL, SASL_SSL or SASL_PLAINTEXT' },
    { key: 'sasl_mechanism', label: 'SASL mechanism', placeholder: 'PLAIN, SCRAM-SHA-256 or SCRAM-SHA-512' },
    { key: 'username_env', label: 'User from', placeholder: 'KAFKA_USER', help: 'An environment variable.' },
    { key: 'password_env', label: 'Password from', placeholder: 'KAFKA_PASSWORD' },
    { key: 'group', label: 'Consumer group', placeholder: 'murakumo-<source> (the default)' },
  ],
  protobuf: [
    { key: 'proto', label: '.proto file', placeholder: 'protos/orders.proto' },
    { key: 'location', label: 'Messages arrive in', placeholder: 'events/ or s3://bucket/events' },
  ],
}

const AUTH_FIELDS: Record<string, Field[]> = {
  none: [],
  api_key: [
    { key: 'env', label: 'Key from', placeholder: 'WEATHER_API_KEY' },
    { key: 'name', label: 'Sent as', placeholder: 'X-API-Key' },
    { key: 'location', label: 'In the', type: 'select', options: ['header', 'query'] },
  ],
  bearer: [{ key: 'env', label: 'Token from', placeholder: 'WEATHER_TOKEN' }],
  basic: [
    { key: 'user_env', label: 'User from', placeholder: 'API_USER' },
    { key: 'password_env', label: 'Password from', placeholder: 'API_PASSWORD' },
  ],
  oauth2: [
    { key: 'token_url', label: 'Token URL', placeholder: 'https://auth.example.com/oauth/token' },
    { key: 'client_id_env', label: 'Client id from', placeholder: 'API_CLIENT_ID' },
    { key: 'client_secret_env', label: 'Secret from', placeholder: 'API_CLIENT_SECRET' },
    { key: 'scope', label: 'Scope', placeholder: 'read' },
  ],
}

const PAGING_FIELDS: Record<string, Field[]> = {
  none: [],
  page: [
    { key: 'param', label: 'Page parameter', placeholder: 'page' },
    { key: 'size_param', label: 'Size parameter', placeholder: 'per_page' },
    { key: 'size', label: 'Page size', type: 'number', placeholder: '100' },
    { key: 'start', label: 'First page', type: 'number', placeholder: '1' },
  ],
  offset: [
    { key: 'param', label: 'Offset parameter', placeholder: 'offset' },
    { key: 'size_param', label: 'Size parameter', placeholder: 'limit' },
    { key: 'size', label: 'Page size', type: 'number', placeholder: '100' },
  ],
  cursor: [
    { key: 'param', label: 'Cursor parameter', placeholder: 'cursor' },
    { key: 'cursor_path', label: 'Next cursor at', placeholder: 'meta.next_cursor' },
    { key: 'size_param', label: 'Size parameter', placeholder: 'limit' },
    { key: 'size', label: 'Page size', type: 'number', placeholder: '100' },
  ],
  link: [],
}

type Obj = Record<string, unknown>
const text = (v: unknown) => (v === undefined || v === null ? '' : String(v))

/** Drop empty values, so murakumo.yaml holds only what was set. */
function clean(value: unknown): unknown {
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

const pairs = (o: unknown) =>
  Object.entries((o as Obj) ?? {})
    .map(([k, v]) => `${k}=${text(v)}`)
    .join('\n')
const unpairs = (s: string) =>
  Object.fromEntries(
    s
      .split('\n')
      .map((line) => line.trim())
      .filter((line) => line.includes('='))
      .map((line) => [line.slice(0, line.indexOf('=')).trim(), line.slice(line.indexOf('=') + 1).trim()]),
  )

function Inputs({ fields, value, onChange, tables }: { fields: Field[]; value: Obj; onChange: (v: Obj) => void; tables?: string[] }) {
  return (
    <>
      {fields.map((f) => (
        <label key={f.key} className="settings__row" title={f.help}>
          <span>{f.label}</span>
          {f.type === 'select' ? (
            <select className="input" value={text(value[f.key]) || f.options?.[0]} onChange={(e) => onChange({ ...value, [f.key]: e.target.value })}>
              {f.options?.map((o) => (
                <option key={o} value={o}>
                  {o || '(by extension)'}
                </option>
              ))}
            </select>
          ) : (
            <input
              className="input"
              placeholder={f.placeholder}
              list={f.key === 'table' ? 'connection-tables' : undefined}
              value={text(value[f.key])}
              inputMode={f.type === 'number' ? 'numeric' : undefined}
              onChange={(e) =>
                onChange({ ...value, [f.key]: f.type === 'number' && e.target.value ? Number(e.target.value) : e.target.value })
              }
            />
          )}
        </label>
      ))}
      {tables && (
        <datalist id="connection-tables">
          {tables.map((t) => (
            <option key={t} value={t} />
          ))}
        </datalist>
      )}
    </>
  )
}

export function ConnectionEditor({
  source,
  initial,
  tables,
  run,
  onBack,
  onMessage,
  startJob,
}: {
  source: string
  initial: Connection | null
  tables: string[] // the source's raw tables in the model
  run: Run
  onBack: () => void
  onMessage: (text: string) => void
  startJob: (kind: string, options?: Record<string, unknown>) => Promise<unknown>
}) {
  const [connection, setConnection] = useState<Connection>(initial ?? { kind: 'rest', base_url: '', endpoints: [{ table: '', path: '' }] })
  const [why, setWhy] = useState('')
  const [saved, setSaved] = useState(initial !== null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [tested, setTested] = useState<string[] | null>(null)
  const set = (next: Obj) => {
    setConnection(next as Connection)
    setSaved(false)
  }
  const kind = connection.kind

  const save = async (value: Connection | null) => {
    setBusy(true)
    setError(null)
    try {
      await api.saveConnection(source, value ? (clean(value) as Connection) : null, why.trim() || null)
      setSaved(value !== null)
      setWhy('')
      onMessage(value ? `Saved ${source}'s connection in murakumo.yaml. Generate to write its extract code.` : `${source} is no longer fetched.`)
      if (!value) onBack()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }
  const test = async () => {
    setBusy(true)
    setTested(null)
    setError(null)
    try {
      setTested((await api.testSource(source)).lines)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const list = LISTS[kind]
  return (
    <div className="sources connection">
      <button className="link-button" onClick={onBack}>
        ← All sources
      </button>
      <h3 className="section__title">How {source}’s data is fetched</h3>
      <label className="settings__row">
        <span>Kind</span>
        <select
          className="input"
          value={kind}
          onChange={(e) => set(e.target.value === 'rest' ? { kind: 'rest', base_url: '', endpoints: [{ table: '' }] } : { kind: e.target.value, [LISTS[e.target.value].key]: [{}] })}
        >
          {KINDS.map((k) => (
            <option key={k.value} value={k.value}>
              {k.label}
            </option>
          ))}
        </select>
      </label>
      <p className="muted small">{KINDS.find((k) => k.value === kind)?.help}</p>

      {kind === 'rest' ? (
        <Rest connection={connection} onChange={set} tables={tables} source={source} run={run} onMessage={onMessage} />
      ) : (
        <>
          <Inputs fields={TOP[kind] ?? []} value={connection} onChange={set} />
          {list && (
            <fieldset className="settings__group">
              <legend>Tables</legend>
              {((connection[list.key] as Obj[]) ?? []).map((item, i) => (
                <div key={i} className="connection__item">
                  <Inputs
                    fields={list.fields}
                    value={item}
                    tables={tables}
                    onChange={(v) => {
                      const items = [...((connection[list.key] as Obj[]) ?? [])]
                      items[i] = v
                      set({ ...connection, [list.key]: items })
                    }}
                  />
                  <button
                    className="icon-button"
                    aria-label={`Remove this ${list.noun}`}
                    onClick={() => set({ ...connection, [list.key]: ((connection[list.key] as Obj[]) ?? []).filter((_, j) => j !== i) })}
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              ))}
              <button className="button button--small" onClick={() => set({ ...connection, [list.key]: [...((connection[list.key] as Obj[]) ?? []), {}] })}>
                <Plus size={14} /> Add a {list.noun}
              </button>
            </fieldset>
          )}
        </>
      )}

      {!saved && (
        <label className="settings__row settings__row--stacked">
          <span>Why?</span>
          <textarea className="input" rows={2} placeholder="Recorded in the decisions log" value={why} onChange={(e) => setWhy(e.target.value)} />
        </label>
      )}
      {error && <p className="stack__problem stack__problem--error">{error}</p>}
      {tested && (
        <ul className="connection__tested">
          {tested.map((line) => (
            <li key={line}>✓ {line}</li>
          ))}
        </ul>
      )}
      <div className="stack__buttons">
        <button className="button button--primary" disabled={busy || saved} onClick={() => save(connection)}>
          {saved ? 'Saved' : 'Save connection'}
        </button>
        <button className="button" disabled={busy || !saved} onClick={test} title={`murakumo source test ${source}`}>
          Test
        </button>
        <button className="button" disabled={busy || !saved} onClick={() => startJob('ingest', { source })} title={`murakumo ingest --source ${source}`}>
          Fetch now
        </button>
        {initial && (
          <button className="button button--danger" disabled={busy} onClick={() => save(null)}>
            Stop fetching
          </button>
        )}
      </div>
    </div>
  )
}

function Rest({
  connection,
  onChange,
  tables,
  source,
  run,
  onMessage,
}: {
  connection: Connection
  onChange: (c: Obj) => void
  tables: string[]
  source: string
  run: Run
  onMessage: (text: string) => void
}) {
  const auth = (connection.auth as Obj) ?? { type: 'none' }
  const paging = (connection.paging as Obj) ?? { type: 'none' }
  const endpoints = (connection.endpoints as Obj[]) ?? []
  const [results, setResults] = useState<Record<number, RequestResult | string>>({})
  const [sending, setSending] = useState<number | null>(null)
  const [raw, setRaw] = useState<Record<number, boolean>>({})

  const send = async (i: number) => {
    setSending(i)
    try {
      const result = await api.tryRequest({ ...(clean(connection) as Connection), endpoints: [] }, clean(endpoints[i]) as Obj)
      setResults({ ...results, [i]: result })
    } catch (e) {
      setResults({ ...results, [i]: e instanceof Error ? e.message : String(e) })
    } finally {
      setSending(null)
    }
  }
  const setEndpoint = (i: number, value: Obj) => {
    const next = [...endpoints]
    next[i] = value
    onChange({ ...connection, endpoints: next })
  }

  return (
    <>
      <label className="settings__row">
        <span>Base URL</span>
        <input className="input" placeholder="https://api.example.com/v1" value={text(connection.base_url)} onChange={(e) => onChange({ ...connection, base_url: e.target.value })} />
      </label>
      <fieldset className="settings__group">
        <legend>Auth</legend>
        <label className="settings__row">
          <span>Type</span>
          <select className="input" value={text(auth.type) || 'none'} onChange={(e) => onChange({ ...connection, auth: { type: e.target.value } })}>
            {Object.keys(AUTH_FIELDS).map((t) => (
              <option key={t}>{t}</option>
            ))}
          </select>
        </label>
        <Inputs fields={AUTH_FIELDS[text(auth.type) || 'none'] ?? []} value={auth} onChange={(v) => onChange({ ...connection, auth: v })} />
        <p className="muted small">Each “from” is an environment variable on the machine that runs it, never the secret itself.</p>
      </fieldset>
      <fieldset className="settings__group">
        <legend>Paging</legend>
        <label className="settings__row">
          <span>Type</span>
          <select className="input" value={text(paging.type) || 'none'} onChange={(e) => onChange({ ...connection, paging: { type: e.target.value } })}>
            {Object.keys(PAGING_FIELDS).map((t) => (
              <option key={t}>{t}</option>
            ))}
          </select>
        </label>
        <Inputs fields={PAGING_FIELDS[text(paging.type) || 'none'] ?? []} value={paging} onChange={(v) => onChange({ ...connection, paging: v })} />
        <label className="settings__row">
          <span>Requests a second</span>
          <input
            className="input"
            inputMode="decimal"
            placeholder="no limit"
            value={text(connection.requests_per_second)}
            onChange={(e) => onChange({ ...connection, requests_per_second: e.target.value ? Number(e.target.value) : undefined })}
          />
        </label>
        <label className="settings__row settings__row--stacked">
          <span>Headers (name=value, one a line)</span>
          <textarea className="input" rows={2} value={pairs(connection.headers)} onChange={(e) => onChange({ ...connection, headers: unpairs(e.target.value) })} />
        </label>
      </fieldset>
      <datalist id="connection-tables">
        {tables.map((t) => (
          <option key={t} value={t} />
        ))}
      </datalist>
      {endpoints.map((endpoint, i) => {
        const result = results[i]
        return (
          <fieldset key={i} className="settings__group request">
            <legend>Request {i + 1}</legend>
            <div className="request__line">
              <select className="input request__method" aria-label="Method" value={text(endpoint.method) || 'GET'} onChange={(e) => setEndpoint(i, { ...endpoint, method: e.target.value })}>
                <option>GET</option>
                <option>POST</option>
              </select>
              <input className="input" aria-label="Path" placeholder="/readings" value={text(endpoint.path)} onChange={(e) => setEndpoint(i, { ...endpoint, path: e.target.value })} />
              <button className="button button--primary button--small" disabled={sending !== null || !text(connection.base_url)} onClick={() => send(i)}>
                <Send size={14} /> {sending === i ? 'Sending…' : 'Send'}
              </button>
            </div>
            <label className="settings__row">
              <span>Raw table</span>
              <input className="input" list="connection-tables" placeholder="readings" value={text(endpoint.table)} onChange={(e) => setEndpoint(i, { ...endpoint, table: e.target.value })} />
            </label>
            <label className="settings__row settings__row--stacked">
              <span>Parameters (name=value, one a line)</span>
              <textarea className="input" rows={2} value={pairs(endpoint.params)} onChange={(e) => setEndpoint(i, { ...endpoint, params: unpairs(e.target.value) })} />
            </label>
            <label className="settings__row">
              <span>Records at</span>
              <input className="input" placeholder="data.items (empty: the whole response)" value={text(endpoint.records)} onChange={(e) => setEndpoint(i, { ...endpoint, records: e.target.value })} />
            </label>
            <label className="settings__row" title="This parameter is sent the latest value of the table's watermark column fetched so far, so each run asks only for what is new.">
              <span>Only newer, by</span>
              <input className="input" placeholder="since (a parameter)" value={text(endpoint.since_param)} onChange={(e) => setEndpoint(i, { ...endpoint, since_param: e.target.value })} />
            </label>
            {typeof result === 'string' && <p className="stack__problem stack__problem--error">{result}</p>}
            {result && typeof result === 'object' && (
              <div className="request__result">
                <div className="request__status">
                  <span className={result.status < 300 ? 'badge' : 'badge badge--error'}>{result.status}</span>
                  <span className="muted small">
                    {result.milliseconds} ms · {result.records} record{result.records === 1 ? '' : 's'} on the first page
                  </span>
                  <button className="link-button" onClick={() => setRaw({ ...raw, [i]: !raw[i] })}>
                    {raw[i] ? 'Records' : 'Raw response'}
                  </button>
                </div>
                {raw[i] ? (
                  <pre className="code-block request__raw">{JSON.stringify(result.response, null, 2)}</pre>
                ) : (
                  <Records rows={result.sample} />
                )}
                {result.sample.length > 0 && (
                  <button
                    className="button button--small"
                    disabled={!text(endpoint.table)}
                    title={text(endpoint.table) ? '' : 'Name the raw table first'}
                    onClick={async () => {
                      const existed = tables.includes(text(endpoint.table))
                      const ok = await run(() =>
                        api.tableFromRecords({ source, table: text(endpoint.table), records: result.sample, path: text(endpoint.records) || null }),
                      )
                      if (ok)
                        onMessage(
                          `${existed ? 'Updated' : 'Made'} ${text(endpoint.table)} from ${result.sample.length} records: check its column types.`,
                        )
                    }}
                  >
                    {tables.includes(text(endpoint.table)) ? `Update ${text(endpoint.table)} from these records` : `Make ${text(endpoint.table) || 'a table'} from these records`}
                  </button>
                )}
              </div>
            )}
            {endpoints.length > 1 && (
              <button className="link-button" onClick={() => onChange({ ...connection, endpoints: endpoints.filter((_, j) => j !== i) })}>
                Remove this request
              </button>
            )}
          </fieldset>
        )
      })}
      <button className="button button--small" onClick={() => onChange({ ...connection, endpoints: [...endpoints, { table: '' }] })}>
        <Plus size={14} /> Add a request
      </button>
    </>
  )
}

function Records({ rows }: { rows: Record<string, unknown>[] }) {
  if (!rows.length) return <p className="muted small">No records on this page.</p>
  const columns = [...new Set(rows.flatMap((r) => Object.keys(r)))].slice(0, 12)
  return (
    <div className="request__table">
      <table className="data-table">
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, 10).map((row, i) => (
            <tr key={i}>
              {columns.map((c) => (
                <td key={c}>{typeof row[c] === 'object' ? JSON.stringify(row[c]) : text(row[c])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
