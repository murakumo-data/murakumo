import { Fragment, useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'

import { api, type Entity, type ExpressionCheck, type ExpressionPreview } from './api'

// An editor for calculated columns: SQL expressions in DuckDB's dialect that read `entity.column`.
// It highlights the SQL, suggests entities, columns and functions as you type, checks the
// expression on the server as you go (columns, type, the warehouse's SQL) and previews it on
// made-up rows.

// `‸` marks where the cursor goes after inserting.
const FUNCTIONS: { name: string; snippet: string; help: string }[] = [
  { name: 'coalesce', snippet: 'coalesce(‸, )', help: 'the first value that is not null' },
  { name: 'nullif', snippet: 'nullif(‸, 0)', help: 'null when equal, e.g. to avoid dividing by zero' },
  { name: 'round', snippet: 'round(‸, 2)', help: 'round to a number of decimals' },
  { name: 'concat_ws', snippet: "concat_ws('|', ‸)", help: 'join values with a separator' },
  { name: 'upper', snippet: 'upper(‸)', help: 'upper case' },
  { name: 'lower', snippet: 'lower(‸)', help: 'lower case' },
  { name: 'trim', snippet: 'trim(‸)', help: 'remove spaces at both ends' },
  { name: 'date_trunc', snippet: "date_trunc('month', ‸)", help: 'the start of the day, month, year…' },
  { name: 'datediff', snippet: "datediff('day', ‸, )", help: 'days (or other units) between two dates' },
  { name: 'cast', snippet: 'cast(‸ as decimal(10,2))', help: 'change the type' },
  { name: 'case', snippet: "case when ‸ then '' else '' end", help: 'a value per condition' },
  { name: 'md5', snippet: 'md5(‸)', help: 'a hash, e.g. for a surrogate key' },
]

const KEYWORDS = new Set(
  'and or not is null case when then else end as cast in between like ilike true false distinct interval'.split(' '),
)

type Token = { text: string; kind: 'keyword' | 'string' | 'number' | 'function' | 'column' | 'plain' }

function tokenize(text: string): Token[] {
  const tokens: Token[] = []
  const pattern = /('(?:[^']|'')*'?)|(\d+(?:\.\d+)?)|([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)|(\s+|.)/g
  let match: RegExpExecArray | null
  while ((match = pattern.exec(text))) {
    const [whole, string, number, word] = match
    if (string) tokens.push({ text: whole, kind: 'string' })
    else if (number) tokens.push({ text: whole, kind: 'number' })
    else if (word) {
      const next = text.slice(pattern.lastIndex).trimStart()[0]
      const kind = KEYWORDS.has(word.toLowerCase())
        ? 'keyword'
        : next === '('
          ? 'function'
          : word.includes('.')
            ? 'column'
            : 'plain'
      tokens.push({ text: whole, kind })
    } else tokens.push({ text: whole, kind: 'plain' })
  }
  return tokens
}

type Suggestion = { insert: string; label: string; detail: string; replace: number }

function suggestionsAt(text: string, caret: number, entities: Entity[], sources: string[]): Suggestion[] {
  const before = text.slice(0, caret)
  const qualified = /([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z0-9_]*)$/.exec(before)
  if (qualified) {
    const entity = entities.find((e) => e.name.toLowerCase() === qualified[1].toLowerCase())
    if (!entity) return []
    const typed = qualified[2].toLowerCase()
    return entity.attributes
      .filter((a) => a.name.toLowerCase().startsWith(typed))
      .slice(0, 12)
      .map((a) => ({ insert: a.name, label: a.name, detail: a.type, replace: typed.length }))
  }
  const word = /([A-Za-z_][A-Za-z0-9_]*)$/.exec(before)
  if (!word) return []
  const typed = word[1].toLowerCase()
  const fromSources = sources
    .map((s) => s.split('.')[1])
    .filter((c) => c && c.toLowerCase().startsWith(typed))
    .map((c) => ({ insert: c, label: c, detail: 'source column', replace: typed.length }))
  const fromEntities = entities
    .filter((e) => e.name.toLowerCase().startsWith(typed))
    .map((e) => ({ insert: `${e.name}.`, label: `${e.name}.`, detail: e.layer, replace: typed.length }))
  const fromFunctions = FUNCTIONS.filter((f) => f.name.startsWith(typed)).map((f) => ({
    insert: f.snippet,
    label: f.name,
    detail: f.help,
    replace: typed.length,
  }))
  return [...fromSources, ...fromEntities, ...fromFunctions].slice(0, 12)
}

type Props = {
  entities: Entity[] // the entities an expression can read
  target: string // the entity whose column the expression calculates
  initial: string
  sources?: string[] // the mapping's sources, which bare column names may refer to
  onSave: (expression: string | null, check: ExpressionCheck | null) => Promise<unknown> | void
  onCancel: () => void
  saveLabel?: string
  children?: ReactNode // extra fields shown above the editor, such as a new column's name
}

export function ExpressionEditor({ entities, target, initial, sources = [], onSave, onCancel, saveLabel = 'Save', children }: Props) {
  const [text, setText] = useState(initial)
  const [caret, setCaret] = useState(initial.length)
  const [active, setActive] = useState(0)
  const [open, setOpen] = useState(false)
  const [check, setCheck] = useState<ExpressionCheck | null>(null)
  const [preview, setPreview] = useState<ExpressionPreview | null>(null)
  const [previewing, setPreviewing] = useState(false)
  const [saving, setSaving] = useState(false)
  const area = useRef<HTMLTextAreaElement>(null)
  const highlight = useRef<HTMLPreElement>(null)

  const suggestions = useMemo(
    () => (open ? suggestionsAt(text, caret, entities, sources) : []),
    [open, text, caret, entities, sources],
  )

  // Check on the server as the expression changes, a moment after typing stops.
  useEffect(() => {
    if (!text.trim()) {
      setCheck(null)
      return
    }
    let current = true
    const timer = window.setTimeout(async () => {
      try {
        const result = await api.checkExpression(text, sources, target)
        if (current) setCheck(result)
      } catch {
        // The next change checks again.
      }
    }, 300)
    return () => {
      current = false
      window.clearTimeout(timer)
    }
  }, [text, sources, target])

  const insert = (snippet: string, replace = 0) => {
    const element = area.current
    const start = element?.selectionStart ?? caret
    const end = element?.selectionEnd ?? caret
    const marker = snippet.indexOf('‸')
    const value = snippet.replace('‸', '')
    const next = text.slice(0, start - replace) + value + text.slice(end)
    const position = start - replace + (marker >= 0 ? marker : value.length)
    setText(next)
    setPreview(null)
    window.requestAnimationFrame(() => {
      element?.focus()
      element?.setSelectionRange(position, position)
      setCaret(position)
    })
  }

  const choose = (s: Suggestion) => {
    insert(s.insert, s.replace)
    setOpen(s.insert.endsWith('.'))
    setActive(0)
  }

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (suggestions.length) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault()
        setActive((active + (e.key === 'ArrowDown' ? 1 : suggestions.length - 1)) % suggestions.length)
        return
      }
      if (e.key === 'Enter' || e.key === 'Tab') {
        e.preventDefault()
        choose(suggestions[Math.min(active, suggestions.length - 1)])
        return
      }
      if (e.key === 'Escape') {
        e.preventDefault()
        setOpen(false)
        return
      }
    }
    if (e.key === ' ' && e.ctrlKey) {
      e.preventDefault()
      setOpen(true)
      return
    }
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault()
      save()
    }
  }

  const runPreview = async () => {
    setPreviewing(true)
    try {
      setPreview(await api.previewExpression(text, sources, target))
    } catch (e) {
      setPreview({ columns: [], rows: [], note: null, error: e instanceof Error ? e.message : String(e), check: check! })
    } finally {
      setPreviewing(false)
    }
  }

  const save = async () => {
    setSaving(true)
    try {
      await onSave(text.trim() || null, text.trim() ? check : null)
    } finally {
      setSaving(false)
    }
  }

  const problems = check ? [...(check.error ? [check.error] : []), ...check.problems] : []

  return (
    <div className="expression">
      {children}
      <div className="expression__functions" role="group" aria-label="Insert a function">
        {FUNCTIONS.slice(0, 9).map((f) => (
          <button key={f.name} type="button" className="chip chip--small" title={f.help} onClick={() => insert(f.snippet)}>
            {f.name}
          </button>
        ))}
      </div>
      <div className="expression__editor">
        <pre className="expression__highlight" aria-hidden ref={highlight}>
          {tokenize(text).map((t, i) => (
            <Fragment key={i}>{t.kind === 'plain' ? t.text : <span className={`tok tok--${t.kind}`}>{t.text}</span>}</Fragment>
          ))}
          {'\n'}
        </pre>
        <textarea
          ref={area}
          className="expression__input"
          aria-label="Expression"
          spellCheck={false}
          autoFocus
          rows={3}
          value={text}
          placeholder="e.g. orders.quantity * orders.unit_price"
          onChange={(e) => {
            setText(e.target.value)
            setCaret(e.target.selectionStart)
            setOpen(true)
            setActive(0)
            setPreview(null)
          }}
          onSelect={(e) => setCaret(e.currentTarget.selectionStart)}
          onKeyDown={onKeyDown}
          onBlur={() => window.setTimeout(() => setOpen(false), 150)}
          onScroll={(e) => {
            if (highlight.current) highlight.current.scrollTop = e.currentTarget.scrollTop
          }}
        />
        {suggestions.length > 0 && (
          <ul className="expression__suggestions" role="listbox" aria-label="Suggestions">
            {suggestions.map((s, i) => (
              <li
                key={`${s.label}-${i}`}
                role="option"
                aria-selected={i === active}
                className={i === active ? 'expression__suggestion expression__suggestion--active' : 'expression__suggestion'}
                onMouseDown={(e) => {
                  e.preventDefault()
                  choose(s)
                }}
              >
                <code>{s.label}</code>
                <span className="muted">{s.detail}</span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="expression__status" aria-live="polite">
        {!text.trim() ? (
          <span className="muted">Type an entity name and a dot for its columns; Ctrl+Space suggests.</span>
        ) : !check ? (
          <span className="muted">Checking…</span>
        ) : problems.length ? (
          <ul className="expression__problems">
            {problems.map((p) => (
              <li key={p}>
                {p}
                {check.error && check.column ? <span className="muted"> (at character {check.column})</span> : null}
              </li>
            ))}
          </ul>
        ) : (
          <span className="expression__ok">
            ✓ Reads {check.references.length ? check.references.join(', ') : 'no columns'}
            {check.type && (
              <>
                {' '}
                · returns <code>{check.type}</code>
              </>
            )}
          </span>
        )}
      </div>
      {check?.ok && check.warehouse_sql && check.warehouse_sql !== text.trim() && (
        <p className="small muted">
          In your warehouse's SQL: <code>{check.warehouse_sql}</code>
        </p>
      )}

      {preview && (
        <div className="expression__preview">
          {preview.error ? (
            <p className="issue issue--error">{preview.error}</p>
          ) : (
            <>
              <table>
                <thead>
                  <tr>
                    {preview.columns.map((c) => (
                      <th key={c} className={c === 'result' ? 'expression__result' : undefined} title={c}>
                        {c.split('.').pop()}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {preview.rows.map((row, i) => (
                    <tr key={i}>
                      {row.map((v, j) => (
                        <td key={j} className={j === row.length - 1 ? 'expression__result' : undefined}>
                          {v === null ? <span className="muted">null</span> : String(v)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="small muted">Made-up rows, run in DuckDB on this computer. {preview.note}</p>
            </>
          )}
        </div>
      )}

      <div className="expression__actions">
        <button type="button" className="button button--small" onClick={runPreview} disabled={!check?.ok || previewing}>
          {previewing ? 'Running…' : 'Preview'}
        </button>
        <span className="expression__spacer" />
        <button type="button" className="button button--small" onClick={onCancel}>
          Cancel
        </button>
        <button
          type="button"
          className="button button--small button--primary"
          onClick={save}
          disabled={saving || (Boolean(text.trim()) && !check?.ok)}
          title="Ctrl/⌘+Enter"
        >
          {saving ? 'Saving…' : saveLabel}
        </button>
      </div>
    </div>
  )
}
