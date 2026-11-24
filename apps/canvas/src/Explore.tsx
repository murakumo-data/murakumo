import { useEffect, useState } from 'react'
import { Eye, Search as SearchIcon, Sparkles, Table2 } from 'lucide-react'

import { api, type Entity, type Estimate, type FoundValue, type QueryResult, type RawStatus, type RelatedCandidate, type RelatedCheck, type RelatedNames, type State, type Suggested } from './api'
import { cell } from './Query'

// Exploring the data before modelling it. The Data tab says whether a table's raw data is in the
// warehouse, shows its rows before it is built (on samples of the raw tables, so it costs the same
// on any size), and lists the questions worth asking it first. Related columns finds where a column
// appears again: names first (no query), then the values, on samples, only when asked.

type Run = (action: () => Promise<State>) => Promise<boolean>

function message(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

export function rowsText(n: number | null | undefined): string {
  if (n === null || n === undefined) return 'loaded'
  return `${n.toLocaleString()} row${n === 1 ? '' : 's'}`
}

function Rows({ result }: { result: QueryResult }) {
  return (
    <div className="query__table explore__table">
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
  )
}

/** The raw tables a table is built from: itself when raw, else those its mappings read, all the way down. */
export function rawBehind(state: State, entity: Entity): string[] {
  const entities = state.model?.entities ?? []
  const byName = new Map(entities.map((e) => [e.name.toLowerCase(), e]))
  const found = new Set<string>()
  const seen = new Set<string>()
  const walk = (e: Entity) => {
    if (seen.has(e.name)) return
    seen.add(e.name)
    if (e.layer === 'raw') {
      found.add(e.name)
      return
    }
    for (const m of e.mappings)
      for (const source of m.sources) {
        const upstream = byName.get(source.split('.')[0].toLowerCase())
        if (upstream) walk(upstream)
      }
  }
  walk(entity)
  return [...found]
}

export function DataTab({ state, entity, onLoadRaw, onQuery }: { state: State; entity: Entity; onLoadRaw?: () => void; onQuery?: (entity: string, sql?: string) => void }) {
  const [status, setStatus] = useState<RawStatus | null>(null)
  const [statusError, setStatusError] = useState<string | null>(null)
  const [preview, setPreview] = useState<(QueryResult & { sampled: boolean; sample: number | null; reads: string[] }) | null>(null)
  const [previewing, setPreviewing] = useState(false)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [estimate, setEstimate] = useState<Estimate | null>(null)
  const [suggested, setSuggested] = useState<Suggested[] | null>(null)
  const raw = entity.layer === 'raw'
  const behind = rawBehind(state, entity)

  useEffect(() => {
    api.exploreStatus().then(setStatus, (e) => setStatusError(message(e)))
    api.suggested(entity.name).then((r) => setSuggested(r.queries), () => setSuggested([]))
  }, [entity.name])

  const unbuilt = !raw && status?.built?.[entity.name] === false
  const holds = (name: string) => !!status?.tables[name]?.exists && status.tables[name].rows !== 0
  const missing = status ? behind.filter((name) => !holds(name)) : []
  const loaded = status ? behind.filter(holds) : []

  const show = async (full: boolean) => {
    setPreviewing(true)
    setPreviewError(null)
    try {
      setPreview(await api.previewTable(entity.name, full))
      setEstimate(null)
    } catch (e) {
      setPreview(null)
      setPreviewError(message(e))
    } finally {
      setPreviewing(false)
    }
  }

  return (
    <div className="explore">
      <section className="section">
        <h3 className="section__title">{raw ? 'Its data' : 'The data it is built from'}</h3>
        {!status && !statusError && <p className="muted small">Asking the warehouse…</p>}
        {statusError && <p className="form-error small">{statusError}</p>}
        {status && behind.length === 0 && <p className="muted small">It reads no raw table yet: map its columns first.</p>}
        {status && behind.length > 0 && (
          <ul className="explore__status">
            {behind.map((name) => {
              const table = status.tables[name]
              return (
                <li key={name}>
                  <span className={holds(name) ? 'explore__dot explore__dot--on' : 'explore__dot'} />
                  <code>{name}</code>
                  <span className="muted small">{table?.exists ? (table.rows === 0 ? 'empty' : rowsText(table.rows)) : 'not loaded'}</span>
                </li>
              )
            })}
          </ul>
        )}
        {status && missing.length > 0 && (
          <div className="explore__hint">
            <p className="small">
              {missing.length === behind.length ? 'Its raw data is not in the warehouse yet.' : `${missing.join(', ')} ${missing.length === 1 ? 'is' : 'are'} not loaded yet.`}{' '}
              Load the raw tables to look at the data before you model it: only the raw tables, nothing else is built.
            </p>
            <button className="button button--small button--primary" onClick={onLoadRaw} disabled={!state.editable || !onLoadRaw}>
              Load raw data…
            </button>
            {status.reason && <p className="muted small">{status.reason}</p>}
          </div>
        )}
        {status && missing.length === 0 && loaded.length > 0 && raw && onQuery && (
          <button className="button button--small" onClick={() => onQuery(entity.name)}>
            <Table2 size={13} /> Explore it in the query console
          </button>
        )}
      </section>

      <section className="section">
        <h3 className="section__title">{raw ? 'Its rows' : 'Its rows before it is built'}</h3>
        <p className="muted small">
          {raw
            ? 'The first rows, from a sample of the table.'
            : 'Its model run as one query on samples of the raw tables, so nothing is built and the cost does not grow with the data. Joins on samples can find fewer matches than the whole tables.'}
        </p>
        <div className="stack__buttons">
          <button className="button button--small" onClick={() => show(false)} disabled={previewing || (status !== null && missing.length > 0 && loaded.length === 0)}>
            <Eye size={13} /> {previewing ? 'Running…' : 'Preview its rows'}
          </button>
          {!raw && (
            <button
              className="button button--small"
              disabled={previewing}
              onClick={async () => {
                try {
                  setEstimate(await api.estimate(entity.name))
                } catch (e) {
                  setPreviewError(message(e))
                }
              }}
            >
              On all the data…
            </button>
          )}
        </div>
        {estimate && (
          <div className="explore__hint">
            <p className="small">{estimate.text}</p>
            <button className="button button--small button--primary" onClick={() => show(true)} disabled={previewing}>
              Run it on all the data
            </button>
            <button className="button button--small" onClick={() => setEstimate(null)}>
              Cancel
            </button>
          </div>
        )}
        {previewError && <p className="form-error small">{previewError}</p>}
        {preview && (
          <>
            <p className="muted small">
              {preview.rows.length.toLocaleString()} rows in {preview.elapsed_ms} ms
              {preview.sampled ? `, from the first ${(preview.sample ?? 0).toLocaleString()} rows of ${preview.reads.join(', ')}` : ', from all the data'}.
            </p>
            <Rows result={preview} />
          </>
        )}
      </section>

      <section className="section">
        <h3 className="section__title">Questions to ask it</h3>
        <p className="muted small">
          {unbuilt
            ? 'Written from the model: its key, dates, categories and joins. They run once it is built; until then, preview its rows above.'
            : 'Written from the model: its key, dates, categories and joins. Each opens in the query console.'}
        </p>
        {suggested === null && <p className="muted small">Loading…</p>}
        {suggested && suggested.length === 0 && <p className="muted small">None for this table.</p>}
        {suggested && suggested.length > 0 && (
          <ul className="explore__questions">
            {suggested.map((q) => (
              <li key={q.title}>
                <button className="explore__question" onClick={() => onQuery?.(entity.name, q.sql)} title={q.sql} disabled={!onQuery || unbuilt}>
                  <strong>{q.title}</strong>
                  <span className="muted small">{q.plain}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}

function share(n: number): string {
  return `${Math.round(n * 100)}%`
}

/** Where a column appears again: names and types first (free), the values on samples when asked. */
export function RelatedColumns({ state, entity, column, run }: { state: State; entity: Entity; column: string; run: Run }) {
  const [names, setNames] = useState<RelatedNames | null>(null)
  const [checked, setChecked] = useState<RelatedCheck | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [value, setValue] = useState('')
  const [found, setFound] = useState<FoundValue | null>(null)

  useEffect(() => {
    setChecked(null)
    setFound(null)
    api.relatedNames(entity.name, column).then(setNames, (e) => setError(message(e)))
  }, [entity.name, column])

  const compare = async (wide: boolean) => {
    setBusy(true)
    setError(null)
    try {
      setChecked(await api.relatedCheck(entity.name, column, wide))
    } catch (e) {
      setError(message(e))
    } finally {
      setBusy(false)
    }
  }

  const joined = (c: RelatedCandidate) =>
    (state.model?.relationships ?? []).some(
      (r) =>
        r.keys.some(
          (k) =>
            (r.from === entity.name && k.from === column && r.to === c.entity && k.to === c.column) ||
            (r.to === entity.name && k.to === column && r.from === c.entity && k.from === c.column),
        ),
    )

  const add = (c: RelatedCandidate) => {
    const overlap = c.overlap
    // The many side points at the one side: the column whose values are found in the other, unique, one.
    const towardThem = !overlap || overlap.cardinality !== 'one_to_many'
    const body = towardThem
      ? { from: entity.name, to: c.entity, keys: [{ from: column, to: c.column }], cardinality: overlap?.cardinality === 'one_to_one' ? 'one_to_one' : 'many_to_one' }
      : { from: c.entity, to: entity.name, keys: [{ from: c.column, to: column }], cardinality: 'many_to_one' }
    void run(() => api.addRelationship(body))
  }

  const strong = names?.candidates.filter((c) => c.strength !== 'maybe') ?? []
  const others = names ? names.candidates.length - strong.length : 0

  return (
    <div className="related">
      <h4 className="related__title">Related columns</h4>
      {error && <p className="form-error small">{error}</p>}
      {!checked && names && (
        <>
          {strong.length ? (
            <ul className="related__list">
              {strong.map((c) => (
                <li key={`${c.entity}.${c.column}`}>
                  <code>
                    {c.entity}.{c.column}
                  </code>
                  <span className="muted small">{c.why}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted small">No other column is named like it.</p>
          )}
          <p className="muted small">
            Found from names and types alone. Compare the values to be sure: a sample of each column, on the warehouse.
          </p>
          <div className="stack__buttons">
            <button className="button button--small" onClick={() => compare(false)} disabled={busy || !strong.length}>
              <Sparkles size={13} /> {busy ? 'Comparing…' : 'Compare the values'}
            </button>
            {others > 0 && (
              <button className="button button--small" onClick={() => compare(true)} disabled={busy} title="Every id column of a type that joins, whatever its name: slower">
                Also {others} id{others === 1 ? '' : 's'} with other names
              </button>
            )}
          </div>
        </>
      )}
      {checked && (
        <>
          {checked.matches.length === 0 && <p className="muted small">No column shares its values.</p>}
          <ul className="related__list">
            {checked.matches.map((c) => {
              const o = c.overlap!
              const label = o.likely ? 'likely' : o.shared ? 'shared values' : o.chance ? 'by chance?' : 'weak'
              const tone = o.likely ? 'likely' : o.shared ? 'shared' : o.chance ? 'chance' : 'weak'
              return (
                <li key={`${c.entity}.${c.column}`} className={`related__match related__match--${o.likely ? 'likely' : 'weak'}`}>
                  <div>
                    <span className={`related__badge related__badge--${tone}`}>{label}</span>{' '}
                    <code>
                      {c.entity}.{c.column}
                    </code>
                  </div>
                  <span className="small">
                    {share(o.here_in_there)} of {column} found there, {share(o.there_in_here)} the other way: {o.kind}.
                  </span>
                  {o.chance && <span className="muted small">The names differ, and small whole-number ids overlap by chance: check before relying on it.</span>}
                  {joined(c) ? (
                    <span className="muted small">Already a relationship.</span>
                  ) : (
                    (o.likely || o.chance) && (
                      <button className="link-button" onClick={() => add(c)} disabled={!state.editable}>
                        Add relationship
                      </button>
                    )
                  )}
                </li>
              )
            })}
          </ul>
          {checked.ruled_out.length > 0 && (
            <details className="related__ruled">
              <summary className="muted small">{checked.ruled_out.length} not compared</summary>
              <ul className="related__list">
                {checked.ruled_out.map((c) => (
                  <li key={`${c.entity}.${c.column}`} className="muted small">
                    <code>
                      {c.entity}.{c.column}
                    </code>
                    : {c.reason}
                  </li>
                ))}
              </ul>
            </details>
          )}
          <p className="muted small">
            On samples of up to {checked.sample.toLocaleString()} rows per table.{' '}
            {!checked.wide && checked.others > 0 && (
              <button className="link-button" onClick={() => compare(true)} disabled={busy}>
                Also compare {checked.others} id{checked.others === 1 ? '' : 's'} with other names
              </button>
            )}
          </p>
        </>
      )}
      <form
        className="related__find"
        onSubmit={async (e) => {
          e.preventDefault()
          if (!value.trim()) return
          setBusy(true)
          setError(null)
          try {
            setFound(await api.findValue(value.trim()))
          } catch (err) {
            setError(message(err))
          } finally {
            setBusy(false)
          }
        }}
      >
        <input className="input" placeholder="Look for a value, such as 12345" aria-label="A value to look for" value={value} onChange={(e) => setValue(e.target.value)} />
        <button className="button button--small" type="submit" disabled={busy || !value.trim()}>
          <SearchIcon size={13} /> Find
        </button>
      </form>
      {found && (
        <p className="small">
          {found.found.length ? (
            <>
              <strong>{found.value}</strong> is in{' '}
              {found.found.map((f, i) => (
                <span key={`${f.entity}.${f.column}`}>
                  {i > 0 && ', '}
                  <code>
                    {f.entity}.{f.column}
                  </code>
                </span>
              ))}
              .
            </>
          ) : (
            <>
              <strong>{found.value}</strong> is in none of the id columns.
            </>
          )}{' '}
          <span className="muted">
            Searched {found.searched} id column{found.searched === 1 ? '' : 's'}
            {found.skipped ? `, skipped ${found.skipped} whose values never reach it` : ''}.
          </span>
        </p>
      )}
    </div>
  )
}
