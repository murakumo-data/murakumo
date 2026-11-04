import { Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'

import { api, type ColourLabel, type Entity, type State } from './api'

// Colour labels: named colours with a meaning ("Finance", "PII", "Deprecated"), given to tables so
// they read as a group wherever they are on the canvas. A table takes its label's colour instead of
// its layer's. Kept in model/layout.yaml, so everyone sees the same.

export const LABEL_COLOURS = ['#c2410c', '#15803d', '#be185d', '#0369a1', '#b7791f', '#7b52c7', '#0f8a8a', '#475569', '#2457d6', '#a16207']

type Run = (action: () => Promise<State>) => Promise<boolean>

export function LabelsSection({ state, run, onShowOnly }: { state: State; run: Run; onShowOnly: (label: string) => void }) {
  const labels = state.canvas?.labels ?? []
  const colours = state.canvas?.colours ?? {}
  const [draft, setDraft] = useState<ColourLabel | null>(null)
  const used = new Set(labels.map((l) => l.colour))

  const save = (next: ColourLabel[], label: string) => run(() => api.saveCanvas({ labels: next, label }))
  const add = async () => {
    const name = draft?.name.trim()
    if (!draft || !name) return setDraft(null)
    if (labels.some((l) => l.name.toLowerCase() === name.toLowerCase())) return
    if (await save([...labels, { ...draft, name }], `new colour label ${name}`)) setDraft(null)
  }

  return (
    <div className="labels">
      {labels.length === 0 && !draft && <p className="muted small tree-empty">Name a colour (Finance, PII...) and give it to tables.</p>}
      {labels.map((label) => {
        const count = Object.values(colours).filter((v) => v === label.name).length
        return (
          <div key={label.name} className="tree-row">
            <span className="tree-row__twisty" />
            <span className="tree-row__swatch" style={{ background: label.colour }} />
            <button className="tree-row__label" title={`Show only tables labelled ${label.name}`} onClick={() => onShowOnly(label.name)}>
              {label.name}
            </button>
            <span className="tree-row__count">{count}</span>
            {state.editable && (
              <button
                className="icon-button icon-button--small"
                title={`Remove the label (its ${count} table${count === 1 ? '' : 's'} keep their layer's colour)`}
                onClick={() => save(labels.filter((l) => l !== label), `remove colour label ${label.name}`)}
              >
                <Trash2 size={13} />
              </button>
            )}
          </div>
        )
      })}
      {draft ? (
        <form
          className="label-form"
          onSubmit={(e) => {
            e.preventDefault()
            add()
          }}
        >
          <input
            className="input"
            autoFocus
            placeholder="Label name"
            value={draft.name}
            onChange={(e) => setDraft({ ...draft, name: e.target.value })}
            onKeyDown={(e) => e.key === 'Escape' && setDraft(null)}
          />
          <div className="label-form__colours" role="radiogroup" aria-label="Colour">
            {LABEL_COLOURS.map((colour) => (
              <button
                type="button"
                key={colour}
                role="radio"
                aria-checked={draft.colour === colour}
                aria-label={colour}
                className={draft.colour === colour ? 'swatch swatch--on' : 'swatch'}
                style={{ background: colour, opacity: used.has(colour) && draft.colour !== colour ? 0.35 : 1 }}
                onClick={() => setDraft({ ...draft, colour })}
              />
            ))}
          </div>
          <div className="dialog__actions">
            <button type="button" className="button button--small" onClick={() => setDraft(null)}>
              Cancel
            </button>
            <button type="submit" className="button button--small button--primary" disabled={!draft.name.trim()}>
              Add label
            </button>
          </div>
        </form>
      ) : (
        state.editable && (
          <button
            className="button button--small labels__add"
            onClick={() => setDraft({ name: '', colour: LABEL_COLOURS.find((c) => !used.has(c)) ?? LABEL_COLOURS[0] })}
          >
            <Plus size={13} /> Colour label
          </button>
        )
      )}
    </div>
  )
}

/** Give one table, or several, a colour label (or none). */
export function setLabel(state: State, run: Run, entities: Entity[], label: string) {
  const colours = { ...(state.canvas?.colours ?? {}) }
  for (const e of entities) {
    if (label) colours[e.id] = label
    else delete colours[e.id]
  }
  const what = entities.length === 1 ? entities[0].name : `${entities.length} tables`
  return run(() => api.saveCanvas({ colours, label: label ? `label ${what} ${label}` : `unlabel ${what}` }))
}

export function LabelSelect({ state, run, entities }: { state: State; run: Run; entities: Entity[] }) {
  const labels = state.canvas?.labels ?? []
  const colours = state.canvas?.colours ?? {}
  const current = new Set(entities.map((e) => colours[e.id] ?? ''))
  const value = current.size === 1 ? [...current][0] : '*'
  return (
    <select
      className="input"
      value={value}
      disabled={!state.editable}
      onChange={(e) => setLabel(state, run, entities, e.target.value)}
      aria-label="Colour label"
    >
      {value === '*' && <option value="*">Mixed</option>}
      <option value="">{labels.length ? 'None (layer colour)' : 'None: add labels in the Explorer'}</option>
      {labels.map((l) => (
        <option key={l.name} value={l.name}>
          {l.name}
        </option>
      ))}
    </select>
  )
}
