import { Plus, X } from 'lucide-react'
import { useState } from 'react'

import { api, type Diagram, type Entity, type State } from './api'

// Diagrams: saved views of some of the tables, like "Finance" or "Trips", as tabs above the canvas.
// "All tables" is always first. A new diagram starts with the tables selected on the canvas.

type Props = {
  diagrams: Diagram[]
  active: string | null
  editable: boolean
  selectedCount: number
  onPick: (id: string | null) => void
  onAdd: (name: string) => void
  onRename: (id: string, name: string) => void
  onRemove: (id: string) => void
}

export function DiagramTabs({ diagrams, active, editable, selectedCount, onPick, onAdd, onRename, onRemove }: Props) {
  const [naming, setNaming] = useState<{ id: string | null; name: string } | null>(null)
  const finish = () => {
    const name = naming?.name.trim()
    if (naming && name) {
      if (naming.id) onRename(naming.id, name)
      else onAdd(name)
    }
    setNaming(null)
  }
  const field = (
    <input
      className="tabs__input"
      autoFocus
      value={naming?.name ?? ''}
      placeholder="Diagram name"
      onChange={(e) => setNaming({ ...naming!, name: e.target.value })}
      onBlur={finish}
      onKeyDown={(e) => {
        if (e.key === 'Enter') finish()
        if (e.key === 'Escape') setNaming(null)
      }}
    />
  )
  return (
    <div className="tabs" role="tablist" aria-label="Diagrams">
      <button role="tab" aria-selected={active === null} className={active === null ? 'tab tab--on' : 'tab'} onClick={() => onPick(null)}>
        All tables
      </button>
      {diagrams.map((d) =>
        naming?.id === d.id ? (
          <span key={d.id} className="tab tab--on">
            {field}
          </span>
        ) : (
          <span key={d.id} className={active === d.id ? 'tab tab--on' : 'tab'}>
            <button
              role="tab"
              aria-selected={active === d.id}
              className="tab__label"
              title={editable ? 'Double-click to rename' : undefined}
              onClick={() => onPick(d.id)}
              onDoubleClick={() => editable && setNaming({ id: d.id, name: d.name })}
            >
              {d.name} <span className="muted">{d.entities.length}</span>
            </button>
            {editable && (
              <button className="tab__close" aria-label={`Remove the diagram ${d.name}`} title="Remove the diagram (its tables stay)" onClick={() => onRemove(d.id)}>
                <X size={12} />
              </button>
            )}
          </span>
        ),
      )}
      {editable &&
        (naming?.id === null ? (
          <span className="tab tab--on">{field}</span>
        ) : (
          <button
            className="tab tab--add"
            title={selectedCount ? `New diagram with the ${selectedCount} selected tables` : 'New diagram (select tables first to start it with them)'}
            onClick={() => setNaming({ id: null, name: '' })}
          >
            <Plus size={13} /> Diagram
          </button>
        ))}
    </div>
  )
}

type Run = (action: () => Promise<State>) => Promise<boolean>

/** In the inspector: which diagrams a table is in, each a toggle. */
export function DiagramChips({ state, run, entity }: { state: State; run: Run; entity: Entity }) {
  const diagrams = state.canvas?.diagrams ?? []
  if (diagrams.length === 0) return <span className="muted small">None yet: add one with + Diagram above the canvas.</span>
  const toggle = (d: Diagram) => {
    const inside = d.entities.includes(entity.id)
    const next = diagrams.map((x) =>
      x.id === d.id ? { ...x, entities: inside ? x.entities.filter((i) => i !== entity.id) : [...x.entities, entity.id] } : x,
    )
    run(() => api.saveCanvas({ diagrams: next, label: `${inside ? 'take' : 'put'} ${entity.name} ${inside ? 'out of' : 'in'} ${d.name}` }))
  }
  return (
    <div className="diagram-chips">
      {diagrams.map((d) => {
        const inside = d.entities.includes(entity.id)
        return (
          <button
            key={d.id}
            className={inside ? 'chip chip--pending' : 'chip chip--off'}
            aria-pressed={inside}
            disabled={!state.editable}
            onClick={() => toggle(d)}
          >
            {inside ? '✓ ' : '+ '}
            {d.name}
          </button>
        )
      })}
    </div>
  )
}
