import { Download, Lightbulb, Play, RotateCcw } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { api, type QueryEngine, type QueryResult, type QueryTarget, type Suggested } from './api'
import { recall, remember } from './storage'

// The query console: read-only SQL on any table at any stage (the source itself, what landed,
// raw, staging, quarantine, marts, snapshots, the audit tables, the Iceberg catalog) and on files
// anywhere DuckDB can read them. Nothing it runs can change data: the server refuses anything
// but SELECT, DESCRIBE and SHOW.

const STAGES = ['source', 'landed', 'raw', 'staging', 'quarantine', 'intermediate', 'marts', 'semantic', 'snapshot', 'lake', 'audit']

export function cell(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

export function asCsv(result: QueryResult): string {
  const quote = (text: string) => (/[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text)
  return [result.columns.map(quote).join(','), ...result.rows.map((row) => row.map((v) => quote(cell(v))).join(','))].join('\n') + '\n'
}

function history(): { sql: string; engine: string }[] {
  try {
    const saved = JSON.parse(recall('murakumo.queries') ?? '[]')
    return Array.isArray(saved) ? saved.slice(0, 30) : []
  } catch {
    return []
  }
}

export function QueryPanel({
  environments,
  focus,
  onLoadRaw,
}: {
  environments: { personal: string; shared: string[] }
  focus: { entity: string; sql?: string; at: number } | null // a table (or a query on it) the inspector asked for
  onLoadRaw?: () => void // load the raw tables, when a query finds them missing
}) {
  const [environment, setEnvironment] = useState(environments.personal)
  const [engines, setEngines] = useState<QueryEngine[]>([])
  const [targets, setTargets] = useState<QueryTarget[]>([])
  const [engine, setEngine] = useState('warehouse')
  const [sql, setSql] = useState('')
  const [limit, setLimit] = useState(1000)
  const [result, setResult] = useState<QueryResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [running, setRunning] = useState(false)
  const [filter, setFilter] = useState('')
  const [past, setPast] = useState(history)
  const editor = useRef<HTMLTextAreaElement>(null)
  const [about, setAbout] = useState<string | null>(null) // the table the query is on: its questions show
  const [questions, setQuestions] = useState<Suggested[]>([])
  useEffect(() => {
    if (!about) return setQuestions([])
    api.suggested(about).then((r) => setQuestions(r.queries), () => setQuestions([]))
  }, [about])

  useEffect(() => {
    api.queryTargets(environment).then(
      (found) => {
        setEngines(found.engines)
        setTargets(found.targets)
      },
      (e) => setError(e instanceof Error ? e.message : String(e)),
    )
  }, [environment])

  const run = useCallback(
    async (text = sql, on = engine) => {
      if (!text.trim()) return
      setRunning(true)
      setError(null)
      try {
        const found = await api.query(text, on, { environment, limit })
        setResult(found)
        const entry = { sql: text, engine: on }
        const next = [entry, ...past.filter((p) => p.sql !== text || p.engine !== on)].slice(0, 30)
        setPast(next)
        remember('murakumo.queries', JSON.stringify(next))
      } catch (e) {
        setResult(null)
        setError(e instanceof Error ? e.message : String(e))
      } finally {
        setRunning(false)
      }
    },
    [sql, engine, environment, limit, past],
  )

  const pick = (target: QueryTarget, now = false) => {
    setSql(target.sql)
    setEngine(target.engine)
    if (target.engine === 'warehouse' && target.entity) setAbout(target.entity)
    editor.current?.focus()
    if (now) void run(target.sql, target.engine)
  }

  // The inspector's Query button: preview that table, at its last stage that exists.
  const handled = useRef(0)
  useEffect(() => {
    if (!focus || focus.at === handled.current || !targets.length) return
    handled.current = focus.at
    setAbout(focus.entity)
    if (focus.sql) {
      setFilter(focus.entity)
      setSql(focus.sql)
      setEngine('warehouse')
      void run(focus.sql, 'warehouse')
      return
    }
    const own = targets.filter((t) => t.entity?.toLowerCase() === focus.entity.toLowerCase())
    const best = own.find((t) => t.stage === 'marts' || t.stage === 'intermediate') ?? own.find((t) => t.stage === 'staging') ?? own[0]
    if (best) {
      setFilter(focus.entity)
      pick(best, true)
    }
  }, [focus, targets]) // eslint-disable-line react-hooks/exhaustive-deps

  const shown = useMemo(() => {
    const words = filter.trim().toLowerCase()
    return targets.filter((t) => !words || t.label.toLowerCase().includes(words) || (t.entity ?? '').toLowerCase().includes(words))
  }, [targets, filter])
  const grouped = STAGES.map((stage) => ({ stage, items: shown.filter((t) => t.stage === stage) })).filter((g) => g.items.length)

  const download = () => {
    if (!result) return
    const link = document.createElement('a')
    link.href = URL.createObjectURL(new Blob([asCsv(result)], { type: 'text/csv' }))
    link.download = 'query.csv'
    link.click()
    URL.revokeObjectURL(link.href)
  }

  return (
    <div className="query">
      <div className="runs__bar">
        <select className="input input--auto" value={engine} onChange={(e) => setEngine(e.target.value)} aria-label="Where the query runs">
          {(engines.length ? engines : [{ engine: 'warehouse', label: 'Warehouse' }]).map((e) => (
            <option key={e.engine} value={e.engine}>
              {e.label}
            </option>
          ))}
        </select>
        {engine === 'warehouse' && (
          <select className="input input--auto" value={environment} onChange={(e) => setEnvironment(e.target.value)} aria-label="Environment">
            <option value={environments.personal}>my own</option>
            {environments.shared.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        )}
      </div>
      <textarea
        ref={editor}
        className="input input--code query__editor"
        rows={6}
        spellCheck={false}
        placeholder={'select * from marts.fct_trip limit 100\n\nFiles: select * from read_parquet(\'s3://bucket/path/*.parquet\')'}
        aria-label="SQL"
        value={sql}
        onChange={(e) => setSql(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
            e.preventDefault()
            void run()
          }
        }}
      />
      <div className="runs__bar">
        <button className="button button--primary button--small" onClick={() => run()} disabled={running || !sql.trim()}>
          <Play size={13} /> {running ? 'Running…' : 'Run'}
        </button>
        <span className="muted small">Ctrl+Enter · read-only</span>
        <label className="small muted">
          Rows{' '}
          <select className="input input--auto" value={limit} onChange={(e) => setLimit(Number(e.target.value))} aria-label="Row limit">
            {[100, 1000, 5000, 10000].map((n) => (
              <option key={n} value={n}>
                {n.toLocaleString()}
              </option>
            ))}
          </select>
        </label>
        {result && (
          <button className="button button--small" onClick={download}>
            <Download size={13} /> CSV
          </button>
        )}
      </div>
      {error && (
        <p className="form-error small" role="alert">
          {error}
        </p>
      )}
      {error && onLoadRaw && /does not exist|not found|no such table|catalog error|build the project first/i.test(error) && (
        <div className="query__hint">
          <p className="muted small">
            The table may not be loaded yet. Load the raw tables (only those, nothing else is built) and query them before
            you model the rest.
          </p>
          <button className="button button--small button--primary" onClick={onLoadRaw}>
            Load raw data…
          </button>
        </div>
      )}
      {error && /build the project first/.test(error) && (
        <div className="query__hint">
          <p className="muted small">
            Nothing is built yet, but you can still look at the data: DuckDB here reads a file wherever it is, Parquet,
            CSV, JSON or Excel. Put its path in the query.
          </p>
          <button
            className="button button--small"
            onClick={() => {
              setEngine('files')
              setError(null)
              setSql("select * from read_parquet('/path/to/your/file.parquet') limit 100\n\n-- a CSV: read_csv('/path/to/file.csv')")
              editor.current?.focus()
            }}
          >
            Query a file instead
          </button>
        </div>
      )}
      {result && (
        <div className="query__result">
          <p className="muted small">
            {result.rows.length.toLocaleString()} row{result.rows.length === 1 ? '' : 's'}
            {result.truncated ? ` (the first ${result.rows.length.toLocaleString()}; there are more)` : ''} in {result.elapsed_ms} ms
          </p>
          <div className="query__table">
            <table>
              <thead>
                <tr>
                  {result.columns.map((c, i) => (
                    <th key={i} title={result.types[i] || undefined}>
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {result.rows.map((row, r) => (
                  <tr key={r}>
                    {row.map((v, c) => (
                      <td key={c} className={v === null ? 'query__null' : undefined}>
                        {v === null ? 'null' : cell(v)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      {questions.length > 0 && (
        <section className="query__questions">
          <h4 className="runs__heading">
            <Lightbulb size={12} /> Questions to ask {about}
          </h4>
          <ul className="query__list">
            {questions.map((q) => (
              <li key={q.title}>
                <button
                  className="link"
                  title={q.plain}
                  onClick={() => {
                    setSql(q.sql)
                    setEngine('warehouse')
                    void run(q.sql, 'warehouse')
                  }}
                >
                  {q.title}
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
      <section className="query__targets">
        <div className="runs__bar">
          <h4 className="runs__heading">Tables</h4>
          <input className="input" placeholder="Find a table" aria-label="Find a table" value={filter} onChange={(e) => setFilter(e.target.value)} />
        </div>
        {grouped.map(({ stage, items }) => (
          <div key={stage}>
            <h5 className="query__stage">{stage}</h5>
            <ul className="query__list">
              {items.map((t) => (
                <li key={`${t.engine}:${t.label}`}>
                  <button className="link" title={t.note ?? t.sql} onClick={() => pick(t, true)}>
                    {t.label}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ))}
        {!grouped.length && <p className="muted small">Nothing matches.</p>}
      </section>
      {past.length > 0 && (
        <section>
          <h4 className="runs__heading">
            <RotateCcw size={12} /> Recent
          </h4>
          <ul className="query__list">
            {past.slice(0, 8).map((p, i) => (
              <li key={i}>
                <button
                  className="link query__past"
                  onClick={() => {
                    setSql(p.sql)
                    setEngine(p.engine)
                  }}
                >
                  {p.sql.length > 80 ? `${p.sql.slice(0, 80)}…` : p.sql}
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}
