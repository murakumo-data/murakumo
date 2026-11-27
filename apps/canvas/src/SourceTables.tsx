import { Check, ChevronLeft, Search, Table2 } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'

import { api, type SourceColumns, type SourceTable, type State } from './api'

type Run = (action: () => Promise<State>) => Promise<boolean>

// A connected source's tables, put on the canvas one at a time, with only the columns chosen.
// Each column shows what its values look like (from a sample of rows), so the choice is made from
// the data, not from names alone. For Dynamics, only the chosen columns are fetched too.

const SHORT = 20 // tables listed in full, without a search

const message = (e: unknown) => (e instanceof Error ? e.message : String(e))

export function SourceTablePicker({ source, run, onAdded }: { source: string; run: Run; onAdded?: (table: string) => void }) {
  const [tables, setTables] = useState<SourceTable[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState<SourceTable | null>(null)

  const load = () => {
    setError(null)
    api.sourceTables(source).then(
      (r) => setTables(r.tables),
      (e) => setError(message(e)),
    )
  }
  useEffect(load, [source])

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase()
    const all = tables ?? []
    // Without a search, a long list shows what is fetched or on the canvas, and the organisation's
    // own tables (Dynamics has hundreds of its own); a short one shows them all.
    const matches = q
      ? all.filter((t) => `${t.label} ${t.table} ${t.entity_set ?? ''}`.toLowerCase().includes(q))
      : all.length <= SHORT
        ? all
        : all.filter((t) => t.fetched || t.in_model || t.custom)
    return matches.slice(0, 200)
  }, [tables, query])

  if (open)
    return (
      <ColumnChooser
        source={source}
        table={open}
        run={run}
        onBack={() => {
          setOpen(null)
          load()
        }}
        onAdded={(name) => {
          setOpen(null)
          load()
          onAdded?.(name)
        }}
      />
    )

  return (
    <div className="source-tables">
      {error && <p className="stack__problem stack__problem--error">{error}</p>}
      {!tables && !error && <p className="muted small">Asking {source} for its tables…</p>}
      {tables && (
        <>
          <p className="muted small">
            {tables.length} table{tables.length === 1 ? '' : 's'}. Choose one to see its columns and what they hold, then put the ones you
            want on the canvas.
          </p>
          {tables.length > SHORT && (
            <label className="table-picker__search">
              <Search size={14} />
              <input className="input" placeholder="Search tables, such as contact" value={query} onChange={(e) => setQuery(e.target.value)} />
            </label>
          )}
          <ul className="source-tables__list">
            {shown.map((t) => (
              <li key={t.table}>
                <button className="source-tables__item" onClick={() => setOpen(t)}>
                  <span className="source-tables__name">
                    <Table2 size={13} /> {t.label}
                  </span>
                  <span className="source-tables__state">
                    {t.label !== t.table && <code>{t.table}</code>}
                    {t.in_model ? (
                      <span className="badge badge--quiet">
                        <Check size={11} /> on the canvas, {t.columns_in_model} columns
                      </span>
                    ) : t.landed ? (
                      <span className="badge badge--warning">fetched, not on the canvas</span>
                    ) : null}
                    {t.custom && t.entity_set && <span className="badge badge--quiet">yours</span>}
                  </span>
                </button>
              </li>
            ))}
            {shown.length === 0 && <li className="muted small">{query ? 'No table matches.' : 'Search to find a table.'}</li>}
          </ul>
        </>
      )}
    </div>
  )
}

function ColumnChooser({
  source,
  table,
  run,
  onBack,
  onAdded,
}: {
  source: string
  table: SourceTable
  run: Run
  onBack: () => void
  onAdded: (table: string) => void
}) {
  const [info, setInfo] = useState<SourceColumns | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [query, setQuery] = useState('')
  const [emptyShown, setEmptyShown] = useState(false)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.sourceTableColumns(source, table.table, table.entity_set).then(
      (r) => setInfo(r),
      (e) => setError(message(e)),
    )
  }, [source, table])

  const always = new Set([info?.key, info?.watermark].filter(Boolean) as string[])
  const columns = (info?.columns ?? []).filter((c) => !c.annotation)
  const shown = columns.filter(
    (c) =>
      (emptyShown || c.filled > 0 || c.in_model || picked.has(c.key)) &&
      (!query.trim() || c.key.toLowerCase().includes(query.trim().toLowerCase())),
  )
  const empty = columns.filter((c) => c.filled === 0 && !c.in_model).length
  const choosable = shown.filter((c) => !c.in_model && !always.has(c.key))
  const toggle = (key: string) => {
    const next = new Set(picked)
    if (next.has(key)) next.delete(key)
    else next.add(key)
    setPicked(next)
  }
  const add = async () => {
    setBusy(true)
    setError(null)
    const ok = await run(() =>
      api.addSourceTable(source, { table: table.table, entity_set: table.entity_set, logical_name: table.logical_name, columns: [...picked] }),
    )
    setBusy(false)
    if (ok) onAdded(table.table)
  }

  return (
    <div className="source-tables">
      <button className="link-button" onClick={onBack}>
        <ChevronLeft size={13} /> All of {source}’s tables
      </button>
      <h4 className="source-tables__title">
        {table.label} {table.label !== table.table && <code>{table.table}</code>}
      </h4>
      {error && <p className="stack__problem stack__problem--error">{error}</p>}
      {!info && !error && <p className="muted small">Reading a sample of its rows…</p>}
      {info && (
        <>
          <p className="muted small">
            {columns.length} columns, from a sample of {info.rows} row{info.rows === 1 ? '' : 's'}. Tick the ones you want: only those go on the
            canvas{table.entity_set ? ' and are fetched' : ''}.
            {always.size > 0 && ` ${[...always].join(' and ')} ${always.size === 1 ? 'is' : 'are'} always kept: the row’s id and when it changed.`}
          </p>
          <div className="source-columns__tools">
            {columns.length > 8 && <input className="input" placeholder="Find a column" value={query} onChange={(e) => setQuery(e.target.value)} />}
            <button className="link-button" onClick={() => setPicked(new Set([...picked, ...choosable.filter((c) => c.filled > 0).map((c) => c.key)]))}>
              Tick those with values
            </button>
            <button className="link-button" onClick={() => setPicked(new Set())}>
              Clear
            </button>
            {empty > 0 && (
              <button className="link-button" onClick={() => setEmptyShown(!emptyShown)}>
                {emptyShown ? 'Hide' : 'Show'} {empty} always empty
              </button>
            )}
          </div>
          <ul className="source-columns">
            {shown.map((c) => {
              const fixed = c.in_model || always.has(c.key)
              return (
                <li key={c.key}>
                  <label className="source-columns__row">
                    <input type="checkbox" checked={fixed || picked.has(c.key)} disabled={fixed} onChange={() => toggle(c.key)} />
                    <span className="source-columns__main">
                      <span className="source-columns__name">
                        <code>{c.key}</code> <span className="muted small">{c.type}</span>
                        {c.in_model && <span className="badge badge--quiet">on the canvas</span>}
                      </span>
                      <span className="source-columns__fill" title={`${c.filled} of ${info.rows} sampled rows have a value`}>
                        <span className="source-columns__bar">
                          <span style={{ width: `${info.rows ? Math.round((100 * c.filled) / info.rows) : 0}%` }} />
                        </span>
                        <span className="muted small">{info.rows ? Math.round((100 * c.filled) / info.rows) : 0}% filled</span>
                      </span>
                      {c.examples.length > 0 && <span className="source-columns__examples">{c.examples.join(' · ')}</span>}
                    </span>
                  </label>
                </li>
              )
            })}
          </ul>
          <div className="source-columns__add">
            <button className="button button--primary" disabled={busy || picked.size === 0} onClick={add}>
              {busy
                ? 'Adding…'
                : table.in_model
                  ? `Add ${picked.size} column${picked.size === 1 ? '' : 's'} to ${table.table}`
                  : `Put ${table.table} on the canvas with ${picked.size + always.size} column${picked.size + always.size === 1 ? '' : 's'}`}
            </button>
          </div>
        </>
      )}
    </div>
  )
}
