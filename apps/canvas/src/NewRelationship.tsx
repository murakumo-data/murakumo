import { useState } from 'react'

import { api, type Entity, type State } from './api'

type Run = (action: () => Promise<State>) => Promise<boolean>

/** The rail's "Add relationship": pick both ends, without dragging between columns. */
export function NewRelationship(props: { state: State; from?: Entity; run: Run; onClose: () => void }) {
  const { state, run, onClose } = props
  const entities = state.model?.entities ?? []
  const [draft, setDraft] = useState({ from: props.from?.name ?? '', column: '', to: '', toColumn: '', cardinality: 'many_to_one' })
  const from = entities.find((e) => e.name === draft.from)
  const to = entities.find((e) => e.name === draft.to)
  const valid = from && to && from !== to && draft.column && draft.toColumn

  const add = async () => {
    if (!valid) return
    const ok = await run(() =>
      api.addRelationship({
        from: draft.from,
        to: draft.to,
        keys: [{ from: draft.column, to: draft.toColumn }],
        cardinality: draft.cardinality,
      }),
    )
    if (ok) onClose()
  }

  const table = (value: string, onChange: (name: string) => void, except?: string) => (
    <select className="input" value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">—</option>
      {entities
        .filter((e) => e.name !== except)
        .map((e) => (
          <option key={e.id}>{e.name}</option>
        ))}
    </select>
  )
  const columns = (entity: Entity | undefined, value: string, onChange: (name: string) => void) => (
    <select className="input" value={value} disabled={!entity} onChange={(e) => onChange(e.target.value)}>
      <option value="">—</option>
      {entity?.attributes.map((a) => (
        <option key={a.id} value={a.name}>
          {a.name}
          {a.key === 'primary' ? ' (PK)' : ''}
        </option>
      ))}
    </select>
  )

  return (
    <div className="dialog-backdrop" onClick={onClose}>
      <form
        className="dialog"
        onClick={(e) => e.stopPropagation()}
        onSubmit={(e) => {
          e.preventDefault()
          add()
        }}
      >
        <h2>New relationship</h2>
        <p className="muted small">The table whose column points at another table's key: a trip points at its zone.</p>
        <label className="row">
          <span className="row__label">From table</span>
          {table(draft.from, (name) => setDraft({ ...draft, from: name, column: '' }))}
        </label>
        <label className="row">
          <span className="row__label">Its column</span>
          {columns(from, draft.column, (name) => setDraft({ ...draft, column: name }))}
        </label>
        <label className="row">
          <span className="row__label">Points to</span>
          {table(draft.to, (name) => setDraft({ ...draft, to: name, toColumn: '' }), draft.from)}
        </label>
        <label className="row">
          <span className="row__label">Its column</span>
          {columns(to, draft.toColumn, (name) => setDraft({ ...draft, toColumn: name }))}
        </label>
        <label className="row">
          <span className="row__label">Cardinality</span>
          <select className="input" value={draft.cardinality} onChange={(e) => setDraft({ ...draft, cardinality: e.target.value })}>
            {state.choices.cardinalities.map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
        </label>
        <div className="dialog__actions">
          <button type="button" className="button" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="button button--primary" disabled={!valid}>
            Add relationship
          </button>
        </div>
      </form>
    </div>
  )
}
