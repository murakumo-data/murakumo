import { Check, CircleAlert, ClipboardPaste, Download, Globe, Plus, Search, Send, Server, Trash2 } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'

import { clean, isDynamics, variableName } from './connection-names'
import { api, type Connection, type DynamicsMade, type DynamicsTable, type RequestResult, type SecretStatus, type State } from './api'

type Run = (action: () => Promise<State>) => Promise<boolean>

// A source's connection: how its data is fetched (`murakumo source connect`). It starts by asking
// what the source is: Microsoft Dynamics 365 needs only its URL, tenant, client id and secret;
// another API starts from a request that already works (a URL or curl command); anything else has
// the full form. Secrets are typed here once and kept in the project's .env, which Murakumo keeps
// out of git and reads itself: murakumo.yaml only names the variable.

type Field = {
  key: string
  label: string
  placeholder?: string
  type?: 'number' | 'select' | 'secret'
  options?: string[]
  help?: string
  what?: string // a secret's part of its variable name: CLIENT_SECRET
}

const KINDS: { value: string; label: string; help: string }[] = [
  { value: 'rest', label: 'REST API', help: 'Requests with auth and paging; each request’s records become a raw table.' },
  { value: 'database', label: 'Database', help: 'Tables or queries in a source database, copied by an incremental column.' },
  { value: 'sftp', label: 'SFTP', help: 'Files on an SFTP server, copied as they appear.' },
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
      { key: 'table', label: 'Raw table', placeholder: 'e.g. shipments' },
      { key: 'pattern', label: 'Files', placeholder: 'e.g. trips_*.csv' },
      { key: 'format', label: 'Format', type: 'select', options: ['', 'csv', 'tsv', 'json', 'jsonl', 'parquet', 'xlsx'] },
    ],
  },
  files: {
    key: 'files',
    noun: 'file set',
    fields: [
      { key: 'table', label: 'Raw table', placeholder: 'e.g. trips' },
      { key: 'pattern', label: 'Files', placeholder: 'e.g. trips_*.parquet' },
      { key: 'format', label: 'Format', type: 'select', options: ['', 'csv', 'tsv', 'json', 'jsonl', 'parquet', 'xlsx'] },
    ],
  },
  database: {
    key: 'tables',
    noun: 'table',
    fields: [
      { key: 'table', label: 'Raw table', placeholder: 'e.g. orders' },
      { key: 'source', label: 'From', placeholder: 'e.g. public.orders, or a SELECT' },
      { key: 'incremental_column', label: 'New rows by', placeholder: 'e.g. updated_at (optional)' },
    ],
  },
  kafka: {
    key: 'topics',
    noun: 'topic',
    fields: [
      { key: 'table', label: 'Raw table', placeholder: 'e.g. orders' },
      { key: 'topic', label: 'Topic', placeholder: 'e.g. orders' },
    ],
  },
  protobuf: {
    key: 'messages',
    noun: 'message type',
    fields: [
      { key: 'table', label: 'Raw table', placeholder: 'e.g. orders' },
      { key: 'message', label: 'Message', placeholder: 'e.g. Order' },
      { key: 'pattern', label: 'Files', placeholder: 'e.g. orders_*.bin' },
    ],
  },
}

const TOP: Record<string, Field[]> = {
  sftp: [
    { key: 'host', label: 'Host', placeholder: 'e.g. sftp.partner.com' },
    { key: 'port', label: 'Port', type: 'number', placeholder: '22' },
    { key: 'user_env', label: 'User', type: 'secret', what: 'sftp_user' },
    { key: 'password_env', label: 'Password', type: 'secret', what: 'sftp_password', help: 'Or a key file, below.' },
    { key: 'key_file_env', label: 'Key file path', type: 'secret', what: 'sftp_key_file', help: 'The path of the private key file.' },
    { key: 'path', label: 'Folder', placeholder: 'e.g. /outbound' },
  ],
  database: [
    {
      key: 'url_env',
      label: 'Connection URL',
      type: 'secret',
      what: 'database_url',
      help: 'A SQLAlchemy URL, such as postgresql+psycopg://user:password@host/db. It holds the password, so it is kept in .env.',
    },
  ],
  files: [{ key: 'location', label: 'Location', placeholder: 'e.g. incoming/ or s3://bucket/prefix' }],
  kafka: [
    { key: 'bootstrap_servers', label: 'Brokers', placeholder: 'e.g. localhost:19092', help: 'host:port, comma-separated.' },
    { key: 'security_protocol', label: 'Security', placeholder: 'PLAINTEXT, SSL, SASL_SSL or SASL_PLAINTEXT' },
    { key: 'sasl_mechanism', label: 'SASL mechanism', placeholder: 'PLAIN, SCRAM-SHA-256 or SCRAM-SHA-512' },
    { key: 'username_env', label: 'User', type: 'secret', what: 'kafka_user' },
    { key: 'password_env', label: 'Password', type: 'secret', what: 'kafka_password' },
    { key: 'group', label: 'Consumer group', placeholder: 'murakumo-<source> (the default)' },
  ],
  protobuf: [
    { key: 'proto', label: '.proto file', placeholder: 'e.g. protos/orders.proto' },
    { key: 'location', label: 'Messages arrive in', placeholder: 'e.g. events/ or s3://bucket/events' },
  ],
}

const AUTH: Record<string, { label: string; fields: Field[] }> = {
  none: { label: 'None', fields: [] },
  bearer: { label: 'Bearer token', fields: [{ key: 'env', label: 'Token', type: 'secret', what: 'token' }] },
  api_key: {
    label: 'API key',
    fields: [
      { key: 'env', label: 'Key', type: 'secret', what: 'api_key' },
      { key: 'name', label: 'Sent as', placeholder: 'e.g. X-API-Key' },
      { key: 'location', label: 'In the', type: 'select', options: ['header', 'query'] },
    ],
  },
  basic: {
    label: 'User and password',
    fields: [
      { key: 'user_env', label: 'User', type: 'secret', what: 'user' },
      { key: 'password_env', label: 'Password', type: 'secret', what: 'password' },
    ],
  },
  oauth2: {
    label: 'OAuth (client id and secret)',
    fields: [
      { key: 'token_url', label: 'Token URL', placeholder: 'e.g. https://auth.example.com/oauth/token' },
      { key: 'client_id_env', label: 'Client id', type: 'secret', what: 'client_id' },
      { key: 'client_secret_env', label: 'Client secret', type: 'secret', what: 'client_secret' },
      { key: 'scope', label: 'Scope', placeholder: 'e.g. read (optional)' },
    ],
  },
}

const PAGING: Record<string, { label: string; fields: Field[] }> = {
  none: { label: 'One page', fields: [] },
  next_url: {
    label: 'Next page’s URL in the response',
    fields: [{ key: 'next_path', label: 'Next URL at', placeholder: 'e.g. next, or @odata.nextLink' }],
  },
  page: {
    label: 'Page numbers',
    fields: [
      { key: 'param', label: 'Page parameter', placeholder: 'page' },
      { key: 'size_param', label: 'Size parameter', placeholder: 'e.g. per_page' },
      { key: 'size', label: 'Page size', type: 'number', placeholder: '100' },
      { key: 'start', label: 'First page', type: 'number', placeholder: '1' },
    ],
  },
  offset: {
    label: 'Offset and limit',
    fields: [
      { key: 'param', label: 'Offset parameter', placeholder: 'offset' },
      { key: 'size_param', label: 'Size parameter', placeholder: 'limit' },
      { key: 'size', label: 'Page size', type: 'number', placeholder: '100' },
    ],
  },
  cursor: {
    label: 'A cursor',
    fields: [
      { key: 'param', label: 'Cursor parameter', placeholder: 'cursor' },
      { key: 'cursor_path', label: 'Next cursor at', placeholder: 'e.g. meta.next_cursor' },
      { key: 'size_param', label: 'Size parameter', placeholder: 'e.g. limit' },
      { key: 'size', label: 'Page size', type: 'number', placeholder: '100' },
    ],
  },
  link: { label: 'Link header', fields: [] },
}

type Obj = Record<string, unknown>
const text = (v: unknown) => (v === undefined || v === null ? '' : String(v))

/** The connection with every secret field naming its variable: the default name where none was given. */
function withSecretNames(connection: Connection, source: string): Connection {
  const named = (holder: Obj, fields: Field[]): Obj => {
    const out = { ...holder }
    for (const f of fields) {
      if (f.type === 'secret' && !text(out[f.key])) out[f.key] = variableName(source, f.what ?? f.key.replace(/_env$/, ''))
    }
    return out
  }
  if (connection.kind === 'rest') {
    const auth = (connection.auth as Obj) ?? { type: 'none' }
    return { ...connection, auth: named(auth, AUTH[text(auth.type) || 'none']?.fields ?? []) }
  }
  // Only the secrets of the kind's own fields; a password is optional when a key file is given.
  const fields = (TOP[connection.kind] ?? []).filter((f) => !(connection.kind === 'sftp' && f.key !== 'user_env' && f.type === 'secret'))
  return named(connection, fields) as Connection
}

type Choice = 'choose' | 'dynamics' | 'api' | 'other'

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
  const [choice, setChoice] = useState<Choice>(
    initial === null ? 'choose' : isDynamics(initial) ? 'dynamics' : initial.kind === 'rest' ? 'api' : 'other',
  )
  return (
    <div className="sources connection">
      <button className="link-button" onClick={choice === 'choose' || initial ? onBack : () => setChoice('choose')}>
        ← {choice === 'choose' || initial ? 'All sources' : 'Something else'}
      </button>
      <h3 className="section__title">Connect {source}</h3>
      {choice === 'choose' && <Choose onChoose={setChoice} />}
      {choice === 'dynamics' && (
        <DynamicsConnect source={source} connected={initial !== null && isDynamics(initial)} run={run} startJob={startJob} onMessage={onMessage} />
      )}
      {(choice === 'api' || choice === 'other') && (
        <ConnectionForm
          source={source}
          initial={initial}
          tables={tables}
          rest={choice === 'api'}
          run={run}
          onBack={onBack}
          onMessage={onMessage}
          startJob={startJob}
          onDynamics={() => setChoice('dynamics')}
        />
      )}
    </div>
  )
}

function Choose({ onChoose }: { onChoose: (choice: Choice) => void }) {
  return (
    <div className="connect-choices">
      <p className="muted small">What is the data in?</p>
      <button className="connect-choice" onClick={() => onChoose('dynamics')}>
        <Server size={18} />
        <span>
          <strong>Microsoft Dynamics 365 / Dataverse</strong>
          <span className="muted small">Its URL, the tenant, a client id and secret. Then pick the tables.</span>
        </span>
      </button>
      <button className="connect-choice" onClick={() => onChoose('api')}>
        <Globe size={18} />
        <span>
          <strong>Another API</strong>
          <span className="muted small">Paste a URL or a curl command that works, and it is filled in for you.</span>
        </span>
      </button>
      <button className="connect-choice" onClick={() => onChoose('other')}>
        <Download size={18} />
        <span>
          <strong>A database, files, SFTP, Kafka or protobuf</strong>
          <span className="muted small">Fetched as they change, into raw tables.</span>
        </span>
      </button>
    </div>
  )
}

// Microsoft Dynamics 365 -------------------------------------------------------------------------

function DynamicsConnect({
  source,
  connected,
  run,
  startJob,
  onMessage,
}: {
  source: string
  connected: boolean
  run: Run
  startJob: (kind: string, options?: Record<string, unknown>) => Promise<unknown>
  onMessage: (text: string) => void
}) {
  const [url, setUrl] = useState('')
  const [tenant, setTenant] = useState('')
  const [clientId, setClientId] = useState('')
  const [secret, setSecret] = useState('')
  const [saved, setSaved] = useState<SecretStatus[]>([])
  const [fetching, setFetching] = useState<string[]>([])
  const [found, setFound] = useState<DynamicsTable[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!connected) return
    api.dynamicsStatus(source).then(
      (s) => {
        setUrl(s.url)
        setTenant(s.tenant)
        setSaved(s.secrets)
        setFetching(s.tables)
      },
      () => {},
    )
  }, [connected, source])

  const idSaved = saved[0]?.set
  const secretSaved = saved[1]?.set
  const ready = url.trim() && tenant.trim() && (clientId.trim() || idSaved) && (secret.trim() || secretSaved)

  const connect = async () => {
    setBusy(true)
    setError(null)
    try {
      const answer = await api.connectDynamics(source, {
        url,
        tenant,
        client_id: clientId.trim() || undefined,
        client_secret: secret.trim() || undefined,
      })
      setFound(answer.tables)
      setFetching(answer.fetching)
      setClientId('')
      setSecret('')
      setSaved((await api.dynamicsStatus(source)).secrets)
      await run(async () => answer)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      api.dynamicsStatus(source).then((s) => setSaved(s.secrets), () => {})
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <p className="muted small">
        Murakumo signs in as your app (OAuth client credentials) and reads the Web API. The id and secret are kept in this project’s{' '}
        <code>.env</code>, which is never committed.
      </p>
      <label className="settings__row settings__row--stacked">
        <span>Environment URL</span>
        <input className="input" placeholder="e.g. https://yourorg.crm11.dynamics.com" value={url} onChange={(e) => setUrl(e.target.value)} />
      </label>
      <label className="settings__row settings__row--stacked">
        <span>Tenant id</span>
        <input className="input" placeholder="e.g. 72f988bf-86f1-41af-91ab-2d7cd011db47" value={tenant} onChange={(e) => setTenant(e.target.value)} />
      </label>
      <label className="settings__row settings__row--stacked">
        <span>
          Client id <SavedBadge status={saved[0]} />
        </span>
        <input
          className="input"
          placeholder={idSaved ? 'Saved. Type a new one to change it' : 'The app’s client (application) id'}
          value={clientId}
          onChange={(e) => setClientId(e.target.value)}
          autoComplete="off"
        />
      </label>
      <label className="settings__row settings__row--stacked">
        <span>
          Client secret <SavedBadge status={saved[1]} />
        </span>
        <input
          className="input"
          type="password"
          placeholder={secretSaved ? 'Saved. Type a new one to change it' : 'The secret’s value (not its id)'}
          value={secret}
          onChange={(e) => setSecret(e.target.value)}
          autoComplete="new-password"
        />
      </label>
      {error && <p className="stack__problem stack__problem--error">{error}</p>}
      <div className="stack__buttons">
        <button className="button button--primary" disabled={busy || !ready} onClick={connect}>
          {busy ? 'Connecting…' : found ? 'Connect again' : connected ? 'Connect and list the tables' : 'Connect'}
        </button>
      </div>
      {found && (
        <TablePicker
          source={source}
          found={found}
          fetching={fetching}
          run={run}
          onMade={(made) => {
            const ok = made.filter((m) => !m.error)
            setFetching([...new Set([...fetching, ...ok.map((m) => m.table)])])
            if (ok.length) onMessage(`Added ${ok.map((m) => m.table).join(', ')}. Next: Fetch and load.`)
          }}
        />
      )}
      {fetching.length > 0 && <NextSteps source={source} tables={fetching} startJob={startJob} />}
    </>
  )
}

function SavedBadge({ status }: { status?: SecretStatus }) {
  if (!status?.set) return null
  return (
    <span className="badge badge--quiet" title={status.from === 'file' ? `Kept in .env as ${status.name}` : `Set in the environment as ${status.name}`}>
      <Check size={11} /> {status.from === 'file' ? 'saved' : 'from the environment'}
    </span>
  )
}

function TablePicker({
  source,
  found,
  fetching,
  run,
  onMade,
}: {
  source: string
  found: DynamicsTable[]
  fetching: string[]
  run: Run
  onMade: (made: DynamicsMade[]) => void
}) {
  const [query, setQuery] = useState('')
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [made, setMade] = useState<DynamicsMade[] | null>(null)
  const [busy, setBusy] = useState(false)
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase()
    // Ticked tables stay in view while searching for more.
    const matches = found.filter(
      (t) =>
        picked.has(t.entity_set) ||
        (q ? `${t.label} ${t.entity_set} ${t.logical_name ?? ''}`.toLowerCase().includes(q) : t.custom || fetching.includes(t.table)),
    )
    return matches.slice(0, 200)
  }, [found, query, fetching, picked])
  const toggle = (name: string) => {
    const next = new Set(picked)
    if (next.has(name)) next.delete(name)
    else next.add(name)
    setPicked(next)
  }
  const add = async () => {
    setBusy(true)
    let result: DynamicsMade[] = []
    await run(async () => {
      const chosen = found.filter((t) => picked.has(t.entity_set)).map((t) => ({ entity_set: t.entity_set, logical_name: t.logical_name }))
      const answer = await api.dynamicsTables(source, chosen)
      result = answer.made
      return answer
    })
    setMade(result)
    setPicked(new Set())
    setBusy(false)
    onMade(result)
  }
  return (
    <fieldset className="settings__group table-picker">
      <legend>Tables to fetch</legend>
      <p className="muted small">
        {found.length} tables. Your organisation’s own are listed first; search for the others, such as accounts or contacts.
      </p>
      <label className="table-picker__search">
        <Search size={14} />
        <input className="input" placeholder="Search tables" value={query} onChange={(e) => setQuery(e.target.value)} />
      </label>
      <ul className="table-picker__list">
        {shown.map((t) => {
          const already = fetching.includes(t.table)
          return (
            <li key={t.entity_set}>
              <label className="settings__check">
                <input type="checkbox" checked={already || picked.has(t.entity_set)} disabled={already} onChange={() => toggle(t.entity_set)} />
                <span>
                  {t.label} <code className="muted">{t.entity_set}</code>
                  {t.custom && <span className="badge badge--quiet">yours</span>}
                  {already && <span className="badge badge--quiet">fetched</span>}
                </span>
              </label>
            </li>
          )
        })}
        {shown.length === 0 && <li className="muted small">{query ? 'No table matches.' : 'Search to find a table.'}</li>}
      </ul>
      <button className="button button--primary button--small" disabled={busy || picked.size === 0} onClick={add}>
        {busy ? 'Reading their columns…' : `Add ${picked.size || ''} table${picked.size === 1 ? '' : 's'}`}
      </button>
      {made && (
        <ul className="connection__tested">
          {made.map((m) => (
            <li key={m.entity_set} className={m.error ? 'connection__failed' : undefined}>
              {m.error ? (
                <>
                  <CircleAlert size={12} /> {m.entity_set}: {m.error}
                </>
              ) : (
                <>
                  <Check size={12} /> {m.table}: {m.columns} columns
                  {m.watermark ? ', fetched in full once, then only what changed' : ', fetched in full each time'}
                </>
              )}
            </li>
          ))}
        </ul>
      )}
    </fieldset>
  )
}

/** What to do once a source has tables: fetch them and load them, then look. */
function NextSteps({
  source,
  tables,
  startJob,
}: {
  source: string
  tables: string[]
  startJob: (kind: string, options?: Record<string, unknown>) => Promise<unknown>
}) {
  const [started, setStarted] = useState(false)
  return (
    <div className="next-steps">
      <strong>Next: fetch and load</strong>
      <p className="muted small">
        Fetches every row of {tables.length === 1 ? tables[0] : `its ${tables.length} tables`} the first time, then only what changed, and
        loads them into your raw tables so you can query them. Only the raw tables: nothing else is built.
      </p>
      <button
        className="button button--primary"
        onClick={async () => {
          if (await startJob('ingest', { source, load: true })) setStarted(true)
        }}
        title={`murakumo ingest --source ${source} --load`}
      >
        <Download size={14} /> Fetch and load {source}
      </button>
      {started && (
        <p className="muted small">
          Running: follow it in Activity. When it finishes, click a table on the canvas and open its <strong>Data</strong> tab, or open{' '}
          <strong>Query</strong>.
        </p>
      )}
    </div>
  )
}

// Any other API, and the other kinds ----------------------------------------------------------------

function ConnectionForm({
  source,
  initial,
  tables,
  rest,
  run,
  onBack,
  onMessage,
  startJob,
  onDynamics,
}: {
  source: string
  initial: Connection | null
  tables: string[]
  rest: boolean
  run: Run
  onBack: () => void
  onMessage: (text: string) => void
  startJob: (kind: string, options?: Record<string, unknown>) => Promise<unknown>
  onDynamics: () => void
}) {
  const [connection, setConnection] = useState<Connection>(
    initial ?? (rest ? { kind: 'rest', base_url: '', endpoints: [{ table: '', path: '' }] } : { kind: 'database', tables: [{}] }),
  )
  const [why, setWhy] = useState('')
  const [saved, setSaved] = useState(initial !== null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [tested, setTested] = useState<string[] | null>(null)
  const [version, setVersion] = useState(0) // remounts the fields when a paste fills them
  const set = (next: Obj) => {
    setConnection(next as Connection)
    setSaved(false)
  }
  const kind = connection.kind

  const save = async (value: Connection | null) => {
    setBusy(true)
    setError(null)
    try {
      await api.saveConnection(source, value ? (clean(withSecretNames(value, source)) as Connection) : null, why.trim() || null)
      setSaved(value !== null)
      setWhy('')
      onMessage(value ? `Saved how ${source} is fetched.` : `${source} is no longer fetched.`)
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
  const fetched = kind === 'rest' ? ((connection.endpoints as Obj[]) ?? []).map((e) => text(e.table)).filter(Boolean) : []
  return (
    <>
      {kind === 'rest' && text(connection.base_url).includes('.dynamics.com') && (
        <p className="next-steps">
          <span>
            This is Microsoft Dynamics.{' '}
            <button className="link-button" onClick={onDynamics}>
              Use the Dynamics form
            </button>{' '}
            to fetch every row, then only what changed. Its tables carry on.
          </span>
        </p>
      )}
      {rest && <Paste source={source} onDynamics={onDynamics} onFilled={(c) => {
        set(c)
        setVersion(version + 1)
      }} />}
      {!rest && (
        <>
          <label className="settings__row">
            <span>Kind</span>
            <select
              className="input"
              value={kind}
              onChange={(e) =>
                set(e.target.value === 'rest' ? { kind: 'rest', base_url: '', endpoints: [{ table: '' }] } : { kind: e.target.value, [LISTS[e.target.value].key]: [{}] })
              }
            >
              {KINDS.map((k) => (
                <option key={k.value} value={k.value}>
                  {k.label}
                </option>
              ))}
            </select>
          </label>
          <p className="muted small">{KINDS.find((k) => k.value === kind)?.help}</p>
        </>
      )}

      {kind === 'rest' ? (
        <Rest key={version} connection={connection} onChange={set} tables={tables} source={source} run={run} onMessage={onMessage} />
      ) : (
        <>
          <Inputs fields={TOP[kind] ?? []} value={connection} onChange={set} source={source} />
          {list && (
            <fieldset className="settings__group">
              <legend>Tables</legend>
              {((connection[list.key] as Obj[]) ?? []).map((item, i) => (
                <div key={i} className="connection__item">
                  <Inputs
                    fields={list.fields}
                    value={item}
                    tables={tables}
                    source={source}
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
          <span>Why? (optional)</span>
          <textarea className="input" rows={2} placeholder="Recorded in the decisions log" value={why} onChange={(e) => setWhy(e.target.value)} />
        </label>
      )}
      {error && <p className="stack__problem stack__problem--error">{error}</p>}
      {tested && (
        <ul className="connection__tested">
          {tested.map((line) => (
            <li key={line}>
              <Check size={12} /> {line}
            </li>
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
        {initial && (
          <button className="button button--danger" disabled={busy} onClick={() => save(null)}>
            Stop fetching
          </button>
        )}
      </div>
      {saved && (kind !== 'rest' || fetched.length > 0) && <NextSteps source={source} tables={kind === 'rest' ? fetched : [source]} startJob={startJob} />}
    </>
  )
}

/** A request that already works, pasted: its URL split up, its secret kept in .env. */
function Paste({ source, onFilled, onDynamics }: { source: string; onFilled: (c: Connection) => void; onDynamics: () => void }) {
  const [pasted, setPasted] = useState('')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<{ text: string; error?: boolean; dynamics?: boolean } | null>(null)
  const fill = async () => {
    setBusy(true)
    setNote(null)
    try {
      const answer = await api.pasteRequest(source, pasted)
      onFilled({ ...answer.connection, endpoints: [answer.endpoint] })
      setPasted('')
      setNote({
        text: answer.saved.length
          ? `Filled in below. The secret was kept in .env as ${answer.saved.join(' and ')}, out of git. Press Send to try it.`
          : 'Filled in below. Press Send to try it.',
        dynamics: answer.dynamics,
      })
    } catch (e) {
      setNote({ text: e instanceof Error ? e.message : String(e), error: true })
    } finally {
      setBusy(false)
    }
  }
  return (
    <fieldset className="settings__group paste">
      <legend>Start from a request that works</legend>
      <textarea
        className="input"
        rows={3}
        placeholder={'Paste a URL or a curl command, e.g.\ncurl https://api.example.com/v1/orders -H "Authorization: Bearer …"'}
        value={pasted}
        onChange={(e) => setPasted(e.target.value)}
      />
      <button className="button button--small" disabled={busy || !pasted.trim()} onClick={fill}>
        <ClipboardPaste size={14} /> {busy ? 'Reading…' : 'Fill in from this'}
      </button>
      {note && (
        <p className={note.error ? 'stack__problem stack__problem--error' : 'muted small'}>
          {note.text}
          {note.dynamics && (
            <>
              {' '}
              This is Microsoft Dynamics:{' '}
              <button className="link-button" onClick={onDynamics}>
                connect it the simple way
              </button>
              .
            </>
          )}
        </p>
      )}
      <p className="muted small">Or fill in the fields yourself.</p>
    </fieldset>
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

  const setEndpoint = (i: number, value: Obj) => {
    const next = [...endpoints]
    next[i] = value
    onChange({ ...connection, endpoints: next })
  }
  const send = async (i: number) => {
    setSending(i)
    try {
      const result = await api.tryRequest({ ...(clean(withSecretNames(connection, source)) as Connection), endpoints: [] }, clean(endpoints[i]) as Obj)
      setResults({ ...results, [i]: result })
      if (result.records_at && !text(endpoints[i].records)) setEndpoint(i, { ...endpoints[i], records: result.records_at })
    } catch (e) {
      setResults({ ...results, [i]: e instanceof Error ? e.message : String(e) })
    } finally {
      setSending(null)
    }
  }

  return (
    <>
      <label className="settings__row settings__row--stacked">
        <span>Base URL</span>
        <input className="input" placeholder="e.g. https://api.example.com/v1" value={text(connection.base_url)} onChange={(e) => onChange({ ...connection, base_url: e.target.value })} />
      </label>
      <fieldset className="settings__group">
        <legend>Signing in</legend>
        <label className="settings__row">
          <span>How</span>
          <select className="input" value={text(auth.type) || 'none'} onChange={(e) => onChange({ ...connection, auth: { type: e.target.value } })}>
            {Object.entries(AUTH).map(([value, a]) => (
              <option key={value} value={value}>
                {a.label}
              </option>
            ))}
          </select>
        </label>
        <Inputs fields={AUTH[text(auth.type) || 'none']?.fields ?? []} value={auth} onChange={(v) => onChange({ ...connection, auth: v })} source={source} />
      </fieldset>
      <details className="settings__group connection__more">
        <summary>Paging, headers and rate limit</summary>
        <label className="settings__row">
          <span>Pages</span>
          <select className="input" value={text(paging.type) || 'none'} onChange={(e) => onChange({ ...connection, paging: { type: e.target.value } })}>
            {Object.entries(PAGING).map(([value, p]) => (
              <option key={value} value={value}>
                {p.label}
              </option>
            ))}
          </select>
        </label>
        <Inputs fields={PAGING[text(paging.type) || 'none']?.fields ?? []} value={paging} onChange={(v) => onChange({ ...connection, paging: v })} source={source} />
        <KeyValues label="Headers" hint="Sent with every request. Signing in is set above, not here." value={connection.headers} onChange={(v) => onChange({ ...connection, headers: v })} />
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
      </details>
      <datalist id="connection-tables">
        {tables.map((t) => (
          <option key={t} value={t} />
        ))}
      </datalist>
      {endpoints.map((endpoint, i) => {
        const result = results[i]
        return (
          <fieldset key={i} className="settings__group request">
            <legend>Table {i + 1}</legend>
            <label className="settings__row settings__row--stacked">
              <span>Request (the part of the URL after the base URL)</span>
              <div className="request__line">
                <select className="input request__method" aria-label="Method" value={text(endpoint.method) || 'GET'} onChange={(e) => setEndpoint(i, { ...endpoint, method: e.target.value })}>
                  <option>GET</option>
                  <option>POST</option>
                </select>
                <input className="input" aria-label="Path" placeholder="e.g. /orders" value={text(endpoint.path)} onChange={(e) => setEndpoint(i, { ...endpoint, path: e.target.value })} />
                <button className="button button--primary button--small" disabled={sending !== null || !text(connection.base_url)} onClick={() => send(i)}>
                  <Send size={14} /> {sending === i ? 'Sending…' : 'Send'}
                </button>
              </div>
            </label>
            <label className="settings__row settings__row--stacked">
              <span>Raw table (what to call it)</span>
              <input className="input" list="connection-tables" placeholder="e.g. orders" value={text(endpoint.table)} onChange={(e) => setEndpoint(i, { ...endpoint, table: e.target.value })} />
            </label>
            <KeyValues label="Parameters" hint="Added to the URL after ?, such as $select=name or status=open." value={endpoint.params} onChange={(v) => setEndpoint(i, { ...endpoint, params: v })} />
            <details className="connection__more">
              <summary>Where the records are, and fetching only what is new</summary>
              <label className="settings__row settings__row--stacked">
                <span>Records at</span>
                <input className="input" placeholder="Found when you press Send, e.g. data.items" value={text(endpoint.records)} onChange={(e) => setEndpoint(i, { ...endpoint, records: e.target.value })} />
              </label>
              <label className="settings__row settings__row--stacked" title="The parameter that gets the latest value of the table's watermark column fetched so far, so each run asks only for what is new.">
                <span>Only newer, by the parameter</span>
                <input className="input" placeholder="e.g. since (optional)" value={text(endpoint.since_param)} onChange={(e) => setEndpoint(i, { ...endpoint, since_param: e.target.value })} />
              </label>
              {text(endpoint.since_param) && (
                <label className="settings__row settings__row--stacked">
                  <span>Written as ({'{since}'} is the latest value)</span>
                  <input className="input" placeholder="{since}" value={text(endpoint.since_format)} onChange={(e) => setEndpoint(i, { ...endpoint, since_format: e.target.value })} />
                </label>
              )}
            </details>
            {typeof result === 'string' && <p className="stack__problem stack__problem--error">{result}</p>}
            {result && typeof result === 'object' && (
              <div className="request__result">
                <div className="request__status">
                  <span className={result.status < 300 ? 'badge' : 'badge badge--error'}>{result.status}</span>
                  <span className="muted small">
                    {result.milliseconds} ms · {result.records} record{result.records === 1 ? '' : 's'} on the first page
                    {result.records_at ? `, at ${result.records_at}` : ''}
                  </span>
                  <button className="link-button" onClick={() => setRaw({ ...raw, [i]: !raw[i] })}>
                    {raw[i] ? 'Records' : 'Raw response'}
                  </button>
                </div>
                {raw[i] ? <pre className="code-block request__raw">{JSON.stringify(result.response, null, 2)}</pre> : <Records rows={result.sample} />}
                {result.sample.length > 0 && (
                  <button
                    className="button button--primary button--small"
                    disabled={!text(endpoint.table)}
                    title={text(endpoint.table) ? '' : 'Name the raw table first'}
                    onClick={async () => {
                      const existed = tables.includes(text(endpoint.table))
                      const ok = await run(() =>
                        api.tableFromRecords({ source, table: text(endpoint.table), records: result.sample, path: text(endpoint.records) || result.records_at || null }),
                      )
                      if (ok) onMessage(`${existed ? 'Updated' : 'Made'} ${text(endpoint.table)} from ${result.sample.length} records. Next: save the connection, then Fetch and load.`)
                    }}
                  >
                    {tables.includes(text(endpoint.table)) ? `Update ${text(endpoint.table)} from these records` : `Make ${text(endpoint.table) || 'the table'} from these records`}
                  </button>
                )}
              </div>
            )}
            {endpoints.length > 1 && (
              <button className="link-button" onClick={() => onChange({ ...connection, endpoints: endpoints.filter((_, j) => j !== i) })}>
                Remove this table
              </button>
            )}
          </fieldset>
        )
      })}
      <button className="button button--small" onClick={() => onChange({ ...connection, endpoints: [...endpoints, { table: '' }] })}>
        <Plus size={14} /> Add a table
      </button>
    </>
  )
}

function Inputs({ fields, value, onChange, tables, source }: { fields: Field[]; value: Obj; onChange: (v: Obj) => void; tables?: string[]; source: string }) {
  return (
    <>
      {fields.map((f) =>
        f.type === 'secret' ? (
          <SecretInput
            key={f.key}
            label={f.label}
            help={f.help}
            variable={text(value[f.key]) || variableName(source, f.what ?? f.key.replace(/_env$/, ''))}
            onVariable={(name) => onChange({ ...value, [f.key]: name })}
          />
        ) : (
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
                onChange={(e) => onChange({ ...value, [f.key]: f.type === 'number' && e.target.value ? Number(e.target.value) : e.target.value })}
              />
            )}
          </label>
        ),
      )}
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

/** A secret, typed once: kept in the project's .env under a variable murakumo.yaml names. */
function SecretInput({ label, help, variable, onVariable }: { label: string; help?: string; variable: string; onVariable: (name: string) => void }) {
  const [value, setValue] = useState('')
  const [status, setStatus] = useState<SecretStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [renaming, setRenaming] = useState(false)
  useEffect(() => {
    api.secrets([variable]).then((r) => setStatus(r.secrets[0] ?? null), () => setStatus(null))
  }, [variable])
  const keep = async () => {
    if (!value) return
    setError(null)
    try {
      const answer = await api.setSecret(variable, value)
      setStatus(answer.secrets[0] ?? null)
      setValue('')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }
  return (
    <div className="settings__row secret" title={help}>
      <span>{label}</span>
      <div className="secret__field">
        <input
          className="input"
          type="password"
          autoComplete="new-password"
          placeholder={status?.set ? (status.from === 'file' ? 'Saved. Type a new one to change it' : 'Set in your environment') : 'Paste it here'}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onBlur={keep}
          onKeyDown={(e) => {
            if (e.key === 'Enter') keep()
          }}
        />
        <span className="muted small secret__where">
          {status?.set ? <Check size={11} /> : null}{' '}
          {status?.set ? (status.from === 'file' ? 'Kept in .env' : 'From the environment') : 'Not set yet'} as{' '}
          {renaming ? (
            <input className="input input--inline" defaultValue={variable} autoFocus onBlur={(e) => {
              setRenaming(false)
              const name = e.target.value.trim()
              if (/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)) onVariable(name)
            }} />
          ) : (
            <button className="link-button" title="Use another variable" onClick={() => setRenaming(true)}>
              {variable}
            </button>
          )}
        </span>
        {error && <span className="stack__problem stack__problem--error">{error}</span>}
      </div>
    </div>
  )
}

/** name = value pairs, one row each. Rows keep what is typed, even before it has both halves. */
function KeyValues({ label, hint, value, onChange }: { label: string; hint?: string; value: unknown; onChange: (v: Obj) => void }) {
  const [rows, setRows] = useState<[string, string][]>(() => {
    const entries = Object.entries((value as Obj) ?? {}).map(([k, v]) => [k, text(v)] as [string, string])
    return entries.length ? entries : [['', '']]
  })
  const update = (next: [string, string][]) => {
    setRows(next)
    onChange(Object.fromEntries(next.filter(([k]) => k.trim()).map(([k, v]) => [k.trim(), v])))
  }
  return (
    <div className="key-values">
      <span className="key-values__label">
        {label}
        {hint && <span className="muted small"> · {hint}</span>}
      </span>
      {rows.map(([k, v], i) => (
        <div key={i} className="key-values__row">
          <input className="input" aria-label={`${label} name`} placeholder="name" value={k} onChange={(e) => update(rows.map((r, j) => (j === i ? [e.target.value, r[1]] : r)))} />
          <span className="muted">=</span>
          <input className="input" aria-label={`${label} value`} placeholder="value" value={v} onChange={(e) => update(rows.map((r, j) => (j === i ? [r[0], e.target.value] : r)))} />
          <button className="icon-button" aria-label={`Remove this ${label.toLowerCase()} row`} onClick={() => update(rows.length > 1 ? rows.filter((_, j) => j !== i) : [['', '']])}>
            <Trash2 size={13} />
          </button>
        </div>
      ))}
      <button className="link-button" onClick={() => setRows([...rows, ['', '']])}>
        <Plus size={12} /> Add
      </button>
    </div>
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
