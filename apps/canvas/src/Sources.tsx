import { useEffect, useMemo, useState } from 'react'

import { api, ApiError, type BrowseSchema, type ImportReport, type SourceInfo, type State } from './api'

type Run = (action: () => Promise<State>) => Promise<boolean>

// Sources: what each one is and where it is read from; add one; browse a database's tables and
// import the ones you tick, after seeing what the import would change.

const KINDS: { value: string; label: string; placeholder: string; help: string }[] = [
  {
    value: 'database',
    label: 'Database (PostgreSQL, DuckDB)',
    placeholder: 'CRM_DB_URL',
    help: 'The name of an environment variable that holds the connection string. The string itself never goes into murakumo.yaml.',
  },
  {
    value: 'snowflake',
    label: 'Snowflake',
    placeholder: 'COMMERCE',
    help: 'The database name. The account, user and key come from the SNOWFLAKE_* environment variables.',
  },
  { value: 'csv', label: 'Data dictionary (CSV)', placeholder: 'dictionaries/erp.csv', help: 'One row per column.' },
  { value: 'excel', label: 'Data dictionary (Excel)', placeholder: 'dictionaries/erp.xlsx', help: 'One row per column.' },
  {
    value: 'file',
    label: 'Data file (Parquet, CSV, JSON)',
    placeholder: 'data/trips.parquet or https://…',
    help: 'Its columns and types become a table.',
  },
  { value: 'openapi', label: 'API (OpenAPI spec)', placeholder: 'specs/api.yaml or https://…', help: 'One table per object schema.' },
]

const browsable = (s: SourceInfo) => s.kind === 'database' || s.kind === 'snowflake'

type View = { kind: 'list' } | { kind: 'browse'; source: SourceInfo } | { kind: 'reimport'; source: SourceInfo }

export function SourcesPanel({ state, run, onMessage }: { state: State; run: Run; onMessage: (text: string) => void }) {
  const [view, setView] = useState<View>({ kind: 'list' })
  const [adding, setAdding] = useState(false)

  if (view.kind === 'browse') {
    return <Browser source={view.source} run={run} onBack={() => setView({ kind: 'list' })} onMessage={onMessage} />
  }
  if (view.kind === 'reimport') {
    return (
      <div className="sources">
        <button className="link-button" onClick={() => setView({ kind: 'list' })}>
          ← All sources
        </button>
        <h3 className="section__title">Re-import {view.source.name}</h3>
        <p className="muted small">
          Reads <code>{view.source.from}</code> again and shows what would change in the model.
        </p>
        <ImportPreview
          source={view.source}
          tables={null}
          run={run}
          onDone={(text) => {
            onMessage(text)
            setView({ kind: 'list' })
          }}
        />
      </div>
    )
  }

  return (
    <div className="sources">
      <p className="muted small">
        Where raw tables come from. Sources are declared in <code>murakumo.yaml</code>; importing one reads only its
        structure (tables, columns, keys), never its rows.
      </p>
      {state.sources.length === 0 && <p className="muted">No sources yet.</p>}
      <ul className="sources__list">
        {state.sources.map((s) => (
          <li key={s.name} className="sources__item">
            <div className="sources__head">
              <strong>{s.name}</strong>
              {s.kind && <span className="badge badge--neutral">{s.kind}</span>}
              <span className="muted small">
                {s.tables} {s.tables === 1 ? 'table' : 'tables'}
              </span>
              {state.editable && s.tables === 0 && (
                <button
                  className="icon-button sources__remove"
                  aria-label={`Remove ${s.name}`}
                  title={`Remove ${s.name} from murakumo.yaml`}
                  onClick={() => run(() => api.removeSource(s.name))}
                >
                  ×
                </button>
              )}
            </div>
            {s.from ? (
              <code className="sources__from">{s.from}</code>
            ) : (
              <span className="muted small">No from: line, so it is edited by hand or with the CLI.</span>
            )}
            {s.updated && (
              <span className="muted small">Updated {new Date(s.updated).toLocaleString()}</span>
            )}
            {state.editable && s.from && (
              <div className="sources__actions">
                {browsable(s) ? (
                  <button className="button button--small" onClick={() => setView({ kind: 'browse', source: s })}>
                    Browse tables
                  </button>
                ) : (
                  <button className="button button--small" onClick={() => setView({ kind: 'reimport', source: s })}>
                    Re-import
                  </button>
                )}
              </div>
            )}
          </li>
        ))}
      </ul>
      {state.editable &&
        (adding ? (
          <AddSource run={run} onDone={() => setAdding(false)} />
        ) : (
          <button className="button button--small" onClick={() => setAdding(true)}>
            + Add source
          </button>
        ))}
    </div>
  )
}

function AddSource({ run, onDone }: { run: Run; onDone: () => void }) {
  const [draft, setDraft] = useState({ name: '', kind: 'database', location: '' })
  const kind = KINDS.find((k) => k.value === draft.kind)!
  return (
    <form
      className="sources__add"
      onSubmit={async (e) => {
        e.preventDefault()
        if (await run(() => api.addSource({ ...draft, name: draft.name.trim(), location: draft.location.trim() }))) onDone()
      }}
    >
      <h3 className="section__title">New source</h3>
      <label className="row">
        <span className="row__label">Name</span>
        <input
          className="input"
          autoFocus
          placeholder="crm"
          value={draft.name}
          onChange={(e) => setDraft({ ...draft, name: e.target.value })}
        />
      </label>
      <label className="row">
        <span className="row__label">Kind</span>
        <select className="input" value={draft.kind} onChange={(e) => setDraft({ ...draft, kind: e.target.value })}>
          {KINDS.map((k) => (
            <option key={k.value} value={k.value}>
              {k.label}
            </option>
          ))}
        </select>
      </label>
      <label className="row">
        <span className="row__label">{draft.kind === 'database' ? 'Variable' : draft.kind === 'snowflake' ? 'Database' : 'Path or URL'}</span>
        <input
          className="input"
          placeholder={kind.placeholder}
          value={draft.location}
          onChange={(e) => setDraft({ ...draft, location: e.target.value })}
        />
      </label>
      <p className="muted small">{kind.help}</p>
      <div className="dialog__actions">
        <button type="button" className="button button--small" onClick={onDone}>
          Cancel
        </button>
        <button type="submit" className="button button--small button--primary" disabled={!draft.name.trim()}>
          Add source
        </button>
      </div>
    </form>
  )
}

type BrowserProps = { source: SourceInfo; run: Run; onBack: () => void; onMessage: (text: string) => void }

/** A database's schemas and tables, read from its catalogue, to tick the ones to import. */
function Browser({ source, run, onBack, onMessage }: BrowserProps) {
  const [schemas, setSchemas] = useState<BrowseSchema[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [open, setOpen] = useState<Set<string>>(new Set())
  const [filter, setFilter] = useState('')
  const [previewing, setPreviewing] = useState(false)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await api.browseSource(source.name)
      setSchemas(result.schemas)
      // Tables already in the model start ticked, so a re-import keeps them up to date.
      setPicked(new Set(result.schemas.flatMap((s) => s.tables.filter((t) => t.in_model).map((t) => `${s.name}.${t.name}`))))
      setOpen(new Set(result.schemas.length === 1 ? [result.schemas[0].name] : []))
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  const shown = useMemo(() => {
    const text = filter.trim().toLowerCase()
    return (schemas ?? [])
      .map((s) => ({ ...s, tables: s.tables.filter((t) => !text || t.name.toLowerCase().includes(text)) }))
      .filter((s) => s.tables.length > 0)
  }, [schemas, filter])

  const toggle = (key: string, on: boolean) => {
    const next = new Set(picked)
    if (on) next.add(key)
    else next.delete(key)
    setPicked(next)
    setPreviewing(false)
  }

  return (
    <div className="sources">
      <button className="link-button" onClick={onBack}>
        ← All sources
      </button>
      <h3 className="section__title">Tables in {source.name}</h3>
      <code className="sources__from">{source.from}</code>
      {!schemas && (
        <>
          <p className="muted small">
            Reads the database's catalogue (schemas, tables, columns and keys), not its rows. It connects with the
            credentials in your environment.
          </p>
          <button className="button button--small button--primary" onClick={load} disabled={loading}>
            {loading ? 'Connecting…' : 'Connect and list tables'}
          </button>
        </>
      )}
      {error && <p className="issue issue--error">{error}</p>}
      {schemas && (
        <>
          <input
            className="input input--search"
            type="search"
            placeholder="Find a table"
            aria-label="Find a table"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
          <ul className="browser">
            {shown.map((schema) => {
              const keys = schema.tables.map((t) => `${schema.name}.${t.name}`)
              const all = keys.every((k) => picked.has(k))
              const expanded = open.has(schema.name) || Boolean(filter.trim())
              return (
                <li key={schema.name}>
                  <div className="browser__schema">
                    <button
                      className="link-button"
                      aria-expanded={expanded}
                      onClick={() => {
                        const next = new Set(open)
                        if (expanded) next.delete(schema.name)
                        else next.add(schema.name)
                        setOpen(next)
                      }}
                    >
                      {expanded ? '▾' : '▸'} {schema.name}
                    </button>
                    <span className="muted small">
                      {schema.tables.length} {schema.tables.length === 1 ? 'table' : 'tables'}
                    </span>
                    <label className="small">
                      <input
                        type="checkbox"
                        checked={all}
                        onChange={(e) => {
                          const next = new Set(picked)
                          for (const k of keys) {
                            if (e.target.checked) next.add(k)
                            else next.delete(k)
                          }
                          setPicked(next)
                          setPreviewing(false)
                        }}
                      />{' '}
                      all
                    </label>
                  </div>
                  {expanded && (
                    <ul className="browser__tables">
                      {schema.tables.map((t) => {
                        const key = `${schema.name}.${t.name}`
                        return (
                          <li key={key}>
                            <details>
                              <summary>
                                <input
                                  type="checkbox"
                                  aria-label={`Import ${key}`}
                                  checked={picked.has(key)}
                                  onClick={(e) => e.stopPropagation()}
                                  onChange={(e) => toggle(key, e.target.checked)}
                                />
                                <code>{t.name}</code>
                                <span className="muted small">{t.columns.length} columns</span>
                                {t.in_model && <span className="badge badge--neutral">in model</span>}
                              </summary>
                              {t.description && <p className="muted small">{t.description}</p>}
                              <ul className="browser__columns">
                                {t.columns.map((c) => (
                                  <li key={c.name}>
                                    <span>{c.primary_key ? 'PK' : ''}</span>
                                    <code>{c.name}</code>
                                    <span className="muted">
                                      {c.type.toLowerCase()}
                                      {c.nullable ? '' : ' not null'}
                                    </span>
                                  </li>
                                ))}
                              </ul>
                            </details>
                          </li>
                        )
                      })}
                    </ul>
                  )}
                </li>
              )
            })}
          </ul>
          <div className="sources__actions">
            <span className="muted small">{picked.size} picked</span>
            <button
              className="button button--small button--primary"
              disabled={picked.size === 0}
              onClick={() => setPreviewing(true)}
            >
              Preview import
            </button>
          </div>
          {previewing && (
            <ImportPreview
              key={[...picked].sort().join()}
              source={source}
              tables={[...picked]}
              run={run}
              onDone={(text) => {
                onMessage(text)
                setPreviewing(false)
                load()
              }}
            />
          )}
        </>
      )}
    </div>
  )
}

function summary(report: ImportReport): string[] {
  return [
    report.added_source ? `+ source ${report.source}` : null,
    ...report.added.map((t) => `+ table ${t}`),
    ...report.changed.map((c) => `~ ${c}`),
    ...report.removed_columns.map((c) => `- column ${c}`),
    ...report.removed.map((t) => `- table ${t}`),
    ...report.added_relationships.map((r) => `+ relationship ${r}`),
    ...report.removed_relationships.map((r) => `- relationship ${r}`),
    ...report.missing.map((t) => `? ${t} is no longer in the source; kept`),
  ].filter((line): line is string => Boolean(line))
}

type PreviewProps = { source: SourceInfo; tables: string[] | null; run: Run; onDone: (message: string) => void }

/** What an import would change, then the button that does it. */
function ImportPreview({ source, tables, run, onDone }: PreviewProps) {
  const [reports, setReports] = useState<ImportReport[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const key = tables ? tables.join() : null

  useEffect(() => {
    let current = true
    api
      .previewImport(source.name, key === null ? null : key.split(','))
      .then((result) => current && setReports(result.reports))
      .catch((e) => current && setError(e instanceof Error ? e.message : String(e)))
    return () => {
      current = false
    }
  }, [source.name, key])

  if (error) return <p className="issue issue--error">{error}</p>
  if (!reports) return <p className="muted">Reading {source.name}…</p>
  const lines = reports.flatMap(summary)
  const problems = reports.flatMap((r) => r.diagnostics)
  const blocked = problems.some((d) => d.level === 'error')
  const changes = reports.some((r) => r.has_changes)
  return (
    <div className="import-preview">
      {lines.length === 0 ? (
        <p className="muted">No changes: the model already matches {tables ? 'these tables' : source.name}.</p>
      ) : (
        <ul className="import-preview__lines">
          {lines.map((line, i) => (
            <li key={i} className={`import-preview__line import-preview__line--${line[0] === '+' ? 'add' : line[0] === '-' ? 'remove' : 'change'}`}>
              {line}
            </li>
          ))}
        </ul>
      )}
      {problems.length > 0 && (
        <ul className="issues">
          {problems.map((d, i) => (
            <li key={i} className={`issue issue--${d.level}`}>
              <span className="issue__code">{d.code}</span> {d.message}
            </li>
          ))}
        </ul>
      )}
      {changes && (
        <button
          className="button button--small button--primary"
          disabled={blocked}
          onClick={async () => {
            if (await run(() => api.importSource(source.name, tables))) {
              onDone(`Imported ${source.name}: ${lines.length} change${lines.length === 1 ? '' : 's'} saved to model/.`)
            }
          }}
        >
          Import into the model
        </button>
      )}
    </div>
  )
}
