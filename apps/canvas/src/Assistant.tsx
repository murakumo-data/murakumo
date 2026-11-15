import { Sparkles } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import { api, type AssistantInfo, type State, type Suggestion } from './api'

// The assistant (`murakumo ai`): suggestions from a language model, when the project turns it on.
// It is sent the model (names, types, descriptions), never rows of data, and it only suggests:
// each suggestion is shown first, and applied through the same edits as anything done by hand.

const KINDS = [
  { id: 'describe', label: 'Describe', hint: 'Descriptions for the tables and columns that have none.' },
  { id: 'classify', label: 'Personal data', hint: 'Which columns hold personal data, and how sensitive each is.' },
  { id: 'map', label: 'Map columns', hint: 'Where each unmapped column of a modelled table comes from.' },
  { id: 'draft', label: 'New tables', hint: 'New tables, from a sentence or two about what is needed.' },
] as const

type Kind = (typeof KINDS)[number]['id']

export function AssistantPanel({
  state,
  selected,
  onState,
  onMessage,
}: {
  state: State
  selected: string | null
  onState: (state: State) => void
  onMessage: (text: string, tone: 'info' | 'error') => void
}) {
  const [info, setInfo] = useState<AssistantInfo | null>(null)
  const [kind, setKind] = useState<Kind>('describe')
  const [entity, setEntity] = useState(selected ?? '')
  const [request, setRequest] = useState('')
  const [found, setFound] = useState<Suggestion[] | null>(null)
  const [chosen, setChosen] = useState<Set<number>>(new Set())
  const [refused, setRefused] = useState<{ text: string; why: string }[]>([])
  const [busy, setBusy] = useState(false)

  const fail = useCallback((e: unknown) => onMessage(e instanceof Error ? e.message : String(e), 'error'), [onMessage])
  useEffect(() => {
    api.assistant().then(setInfo, fail)
  }, [fail])
  const [seen, setSeen] = useState(selected)
  if (selected !== seen) {
    setSeen(selected)
    if (selected) setEntity(selected)
  }

  const entities = state.model?.entities ?? []
  const modelled = entities.filter((e) => e.layer !== 'raw')
  const ask = async () => {
    setBusy(true)
    setRefused([])
    try {
      const options =
        kind === 'map' ? { entity } : kind === 'draft' ? { request } : { entities: entity ? [entity] : [] }
      const result = await api.suggest(kind, options)
      setFound(result.suggestions)
      setChosen(new Set(result.suggestions.map((_, i) => i)))
    } catch (e) {
      fail(e)
    } finally {
      setBusy(false)
    }
  }
  const apply = async () => {
    if (!found) return
    setBusy(true)
    try {
      const result = await api.applySuggestions(found.filter((_, i) => chosen.has(i)))
      onState(result.state)
      setRefused(result.refused)
      setFound(null)
      onMessage(`Applied ${result.applied.length} suggestion${result.applied.length === 1 ? '' : 's'}.`, 'info')
    } catch (e) {
      fail(e)
    } finally {
      setBusy(false)
    }
  }
  const toggle = (index: number) => {
    const next = new Set(chosen)
    if (next.has(index)) next.delete(index)
    else next.add(index)
    setChosen(next)
  }

  if (!info) return <p className="muted">Loading…</p>
  if (!info.enabled) {
    return (
      <div className="assistant">
        <p>
          The assistant suggests descriptions, personal-data marks, mappings and new tables. It is off for this
          project.
        </p>
        <ul className="muted small assistant__terms">
          <li>It uses your own key with the model provider, read from {info.key_env}.</li>
          <li>It is sent the model: table and column names, types and descriptions. Never rows of data.</li>
          <li>It only suggests. Nothing changes until you apply a suggestion.</li>
        </ul>
        <button
          className="button button--primary"
          disabled={busy}
          onClick={() => api.saveAssistant({ enabled: true }).then(setInfo, fail)}
        >
          Turn the assistant on
        </button>
      </div>
    )
  }
  const hint = KINDS.find((k) => k.id === kind)!.hint
  const ready = kind === 'map' ? Boolean(entity) : kind === 'draft' ? request.trim().length > 0 : true
  return (
    <div className="assistant">
      <p className="muted small">
        {info.model}. {info.key_set ? `Key: ${info.key_env}.` : `Set ${info.key_env} where the canvas runs, then start it again.`}{' '}
        <button className="link" onClick={() => api.saveAssistant({ enabled: false }).then(setInfo, fail)}>
          Turn off
        </button>
      </p>
      <div className="assistant__kinds" role="tablist" aria-label="What to suggest">
        {KINDS.map((k) => (
          <button
            key={k.id}
            role="tab"
            aria-selected={kind === k.id}
            className={`button button--small${kind === k.id ? ' button--primary' : ''}`}
            onClick={() => {
              setKind(k.id)
              setFound(null)
            }}
          >
            {k.label}
          </button>
        ))}
      </div>
      <p className="muted small">{hint}</p>
      {kind === 'draft' ? (
        <textarea
          className="input"
          rows={3}
          value={request}
          aria-label="What is needed"
          placeholder="A fact of daily sales by store and product"
          onChange={(e) => setRequest(e.target.value)}
        />
      ) : (
        <select className="input" value={entity} aria-label="Table" onChange={(e) => setEntity(e.target.value)}>
          {kind !== 'map' && <option value="">Every table</option>}
          {kind === 'map' && <option value="">Choose a table…</option>}
          {(kind === 'map' ? modelled : entities).map((e) => (
            <option key={e.id} value={e.name}>
              {e.name}
            </option>
          ))}
        </select>
      )}
      <button className="button button--primary assistant__ask" disabled={busy || !ready} onClick={ask}>
        <Sparkles size={14} /> {busy ? 'Asking…' : 'Suggest'}
      </button>
      {found && found.length === 0 && <p className="muted">Nothing to suggest.</p>}
      {found && found.length > 0 && (
        <>
          <ul className="assistant__list">
            {found.map((s, i) => (
              <li key={i}>
                <label>
                  <input type="checkbox" checked={chosen.has(i)} onChange={() => toggle(i)} />
                  <span>
                    <span className="mono">{s.text}</span>
                    <span className="muted small assistant__reason">{s.reason}</span>
                  </span>
                </label>
              </li>
            ))}
          </ul>
          <button className="button button--primary" disabled={busy || chosen.size === 0} onClick={apply}>
            Apply {chosen.size} of {found.length}
          </button>
        </>
      )}
      {refused.length > 0 && (
        <div className="assistant__refused">
          <p className="small">Not applied, because the model would not take them:</p>
          <ul className="small">
            {refused.map((r, i) => (
              <li key={i}>
                <span className="mono">{r.text}</span>: {r.why}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
