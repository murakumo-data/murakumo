import { LayoutGrid, Trash2 } from 'lucide-react'

import { api, type Entity, type State } from './api'
import { LabelSelect } from './Labels'

type Run = (action: () => Promise<State>) => Promise<boolean>

/** What can be done to several selected tables at once: each is one change, undone in one go. */
export function BulkBar(props: { state: State; run: Run; selected: Entity[]; onArrange: () => void; onClear: () => void }) {
  const { state, run, selected, onArrange, onClear } = props
  const ids = selected.map((e) => e.id)
  const same = <T,>(pick: (e: Entity) => T) => {
    const values = new Set(selected.map(pick))
    return values.size === 1 ? [...values][0] : undefined
  }
  const domain = same((e) => e.domain ?? '')
  const layer = same((e) => e.layer)
  const update = (changes: Record<string, unknown>) => run(() => api.updateEntities(ids, changes))
  return (
    <div className="bulk" role="toolbar" aria-label={`${selected.length} tables selected`}>
      <strong>{selected.length} tables</strong>
      <label className="bulk__field">
        Domain
        <select
          className="input"
          value={domain ?? '*'}
          disabled={!state.editable}
          onChange={(e) => update({ domain: e.target.value || null })}
        >
          {domain === undefined && <option value="*">Mixed</option>}
          <option value="">None</option>
          {state.domains.map((d) => (
            <option key={d.name}>{d.name}</option>
          ))}
        </select>
      </label>
      <label className="bulk__field">
        Layer
        <select className="input" value={layer ?? '*'} disabled={!state.editable} onChange={(e) => update({ layer: e.target.value })}>
          {layer === undefined && <option value="*">Mixed</option>}
          {state.choices.layers.map((l) => (
            <option key={l}>{l}</option>
          ))}
        </select>
      </label>
      <label className="bulk__field">
        Colour
        <LabelSelect state={state} run={run} entities={selected} />
      </label>
      <button className="button button--small" onClick={onArrange} title="Lay out the selected tables left to right">
        <LayoutGrid size={13} /> Arrange
      </button>
      {state.editable && (
        <button
          className="button button--small button--danger"
          onClick={async () => {
            const names = selected.map((e) => e.name).join(', ')
            if (!window.confirm(`Remove ${selected.length} tables (${names})? Undo (⌘Z) brings them back.`)) return
            if (await run(() => api.removeEntities(ids))) onClear()
          }}
        >
          <Trash2 size={13} /> Remove
        </button>
      )}
      <button className="icon-button" aria-label="Clear the selection" onClick={onClear}>
        ×
      </button>
    </div>
  )
}
