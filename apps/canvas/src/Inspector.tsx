import { useMemo, useRef, useState } from 'react'

import { api, impactText, previewOf, type Diagnostic, type Entity, type ImpactReport, type Relationship, type State } from './api'
import { EntityCodeView } from './Code'
import { ExpressionEditor } from './ExpressionEditor'
import { Row, SelectField, TextField } from './Field'

type Run = (action: () => Promise<State>) => Promise<boolean>

/** Run an edit, asking first when it would break something downstream (and always, with `ask`). */
async function careful(run: Run, action: () => Promise<State>, ask?: string): Promise<boolean> {
  let report: ImpactReport | null = null
  try {
    report = await previewOf(action)
  } catch {
    // A preview that fails is reported by the edit itself below.
  }
  if (report?.worst === 'breaking') {
    if (!window.confirm(impactText(report))) return false
  } else if (ask && !window.confirm(ask)) {
    return false
  }
  return run(action)
}

function Issues({ diagnostics }: { diagnostics: Diagnostic[] }) {
  if (diagnostics.length === 0) return null
  return (
    <ul className="issues">
      {diagnostics.map((d, i) => (
        <li key={i} className={`issue issue--${d.level}`}>
          <span className="issue__code">{d.code}</span> {d.message}
          {d.where && <span className="issue__where"> ({d.where})</span>}
        </li>
      ))}
    </ul>
  )
}

type LineageTarget = { entity: string; column: string | null } | null

type EntityProps = {
  state: State
  entity: Entity
  run: Run
  diagnostics: Diagnostic[]
  generatedRevision: string
  lineageOf: LineageTarget
  onLineage: (target: LineageTarget) => void
}

export function EntityPanel({ state, entity, run, diagnostics, generatedRevision, lineageOf, onLineage }: EntityProps) {
  const [tab, setTab] = useState<'model' | 'code'>('model')
  const issues = diagnostics.filter((d) => d.entity_id === entity.id)
  return (
    <div className={tab === 'code' ? 'panel__body panel__body--wide' : 'panel__body'}>
      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={tab === 'model'} className="tab" onClick={() => setTab('model')}>
          Model
        </button>
        <button role="tab" aria-selected={tab === 'code'} className="tab" onClick={() => setTab('code')}>
          Code
        </button>
      </div>
      {tab === 'model' ? (
        <EntityForm state={state} entity={entity} run={run} issues={issues} lineageOf={lineageOf} onLineage={onLineage} />
      ) : (
        <>
          <div className="panel__heading">
            <span className={`dot dot--${entity.layer}`} />
            <code className="panel__title">{entity.name}</code>
          </div>
          <Issues diagnostics={issues} />
          <EntityCodeView entityId={entity.id} revision={`${generatedRevision}:${state.revision}`} />
        </>
      )}
    </div>
  )
}

type FormProps = {
  state: State
  entity: Entity
  run: Run
  issues: Diagnostic[]
  lineageOf: LineageTarget
  onLineage: (target: LineageTarget) => void
}

function EntityForm({ state, entity, run, issues, lineageOf, onLineage }: FormProps) {
  const { choices, editable } = state
  const disabled = !editable
  const save = (changes: Record<string, unknown>) => careful(run, () => api.updateEntity(entity.id, changes))
  const saveColumn = (columnId: string, changes: Record<string, unknown>) =>
    careful(run, () => api.updateColumn(entity.id, columnId, changes))
  const [column, setColumn] = useState({ name: '', type: 'string' })
  const [search, setSearch] = useState('')
  const [picking, setPicking] = useState(false)
  const [calculating, setCalculating] = useState(false)
  const matches = (name: string) => name.toLowerCase().includes(search.trim().toLowerCase())
  const shown = entity.attributes.filter((a) => matches(a.name))
  const [dragging, setDragging] = useState<string | null>(null)
  const [dropAt, setDropAt] = useState<{ id: string; after: boolean } | null>(null)
  const moving = useRef(false)

  // Columns move by dragging the ⋮⋮ handle, or with Alt+↑/↓ on it. The new order is sent whole.
  const move = (id: string, target: string, after: boolean) => {
    const ids = entity.attributes.map((a) => a.id).filter((x) => x !== id)
    const index = ids.indexOf(target)
    if (index < 0) return
    ids.splice(after ? index + 1 : index, 0, id)
    if (moving.current || ids.join() === entity.attributes.map((a) => a.id).join()) return
    moving.current = true // a second move before this one is saved would start from the old order
    run(() => api.orderColumns(entity.id, ids)).finally(() => (moving.current = false))
  }
  const step = (id: string, by: -1 | 1) => {
    const index = entity.attributes.findIndex((a) => a.id === id)
    const neighbour = entity.attributes[index + by]
    if (neighbour) move(id, neighbour.id, by === 1)
  }

  const addColumn = async () => {
    if (!column.name.trim()) return
    if (await run(() => api.addColumn(entity.id, { name: column.name.trim(), type: column.type.trim() }))) {
      setColumn({ name: '', type: column.type })
    }
  }

  return (
    <>
      <div className="panel__heading">
        <span className={`dot dot--${entity.layer}`} />
        <TextField
          className="input input--title"
          label="Entity name"
          value={entity.name}
          disabled={disabled}
          onSave={(name) => name && save({ name })}
        />
        <button
          className={lineageOf?.entity === entity.name && !lineageOf.column ? 'chip chip--active' : 'chip'}
          title="Show where this table's columns come from and what uses them"
          aria-pressed={lineageOf?.entity === entity.name && !lineageOf.column}
          onClick={() =>
            onLineage(lineageOf?.entity === entity.name && !lineageOf.column ? null : { entity: entity.name, column: null })
          }
        >
          Lineage
        </button>
      </div>
      <Issues diagnostics={issues} />

      <section className="section">
        <Row label="Layer">
          <SelectField label="Layer" value={entity.layer} options={choices.layers} disabled={disabled} onSave={(layer) => save({ layer })} />
        </Row>
        <Row label="Kind">
          <SelectField label="Kind" value={entity.kind} options={choices.kinds} disabled={disabled} onSave={(kind) => save({ kind })} />
        </Row>
        <Row label="Source">
          <SelectField
            label="Source"
            value={entity.source}
            options={choices.sources}
            allowEmpty
            disabled={disabled}
            onSave={(source) => save({ source })}
          />
        </Row>
        <Row label="Domain">
          <SelectField
            label="Domain"
            value={entity.domain}
            options={[
              ...state.domains.map((d) => d.name),
              ...(entity.domain && !state.domains.some((d) => d.name === entity.domain) ? [entity.domain] : []),
            ]}
            allowEmpty
            disabled={disabled}
            onSave={(domain) => save({ domain })}
          />
        </Row>
        {entity.kind === 'cumulative' && (
          <>
            <Row label="Per">
              <TextField
                label="Grain"
                value={entity.grain.join(', ') || null}
                placeholder="the key column, e.g. customer_id"
                disabled={disabled}
                onSave={(text) => save({ grain: (text ?? '').split(',').map((t) => t.trim()).filter(Boolean) })}
              />
            </Row>
            <Row label="Active on">
              <TextField
                label="Cumulative date"
                value={entity.cumulative?.date ?? null}
                placeholder="entity.column whose day counts, e.g. fct_orders.ordered_at"
                disabled={disabled}
                onSave={(date) =>
                  date && save({ cumulative: { date, measures: entity.cumulative?.measures ?? {} } })
                }
              />
            </Row>
            <Row label="Each day">
              <TextField
                label="Cumulative measures"
                multiline
                value={
                  Object.entries(entity.cumulative?.measures ?? {})
                    .map(([name, sql]) => `${name} = ${sql}`)
                    .join('\n') || null
                }
                placeholder={'orders = count(*)\nspend = sum(fct_orders.amount)'}
                disabled={disabled || !entity.cumulative}
                onSave={(text) => {
                  const measures: Record<string, string> = {}
                  for (const line of (text ?? '').split('\n')) {
                    const [name, ...rest] = line.split('=')
                    if (name.trim() && rest.length) measures[name.trim()] = rest.join('=').trim()
                  }
                  save({ cumulative: { date: entity.cumulative!.date, measures } })
                }}
              />
            </Row>
          </>
        )}
        <Row label="Refreshed">
          <span className="small">
            {(state.orchestration?.schedules ?? [])
              .filter((s) => s.entities.includes(entity.name))
              .map((s) => `${s.name}: ${s.when}`)
              .join('; ') || <span className="muted">by no schedule</span>}
          </span>
        </Row>
        <Row label="Description">
          <TextField
            label="Description"
            value={entity.description}
            multiline
            disabled={disabled}
            placeholder="What one row means"
            onSave={(description) => save({ description })}
          />
        </Row>
      </section>

      <section className="section">
        <div className="section__head">
          <h3 className="section__title">
            Columns{' '}
            <span className="muted">
              {search ? `${shown.length} of ${entity.attributes.length}` : entity.attributes.length}
            </span>
          </h3>
          {editable && (
            <div className="section__actions">
              {entity.layer !== 'raw' && (
                <button
                  className="button button--small"
                  onClick={() => {
                    setCalculating(!calculating)
                    setPicking(false)
                  }}
                  aria-expanded={calculating}
                >
                  + Calculated
                </button>
              )}
              <button
                className="button button--small"
                onClick={() => {
                  setPicking(!picking)
                  setCalculating(false)
                }}
                aria-expanded={picking}
              >
                + From another table
              </button>
            </div>
          )}
        </div>
        {picking && <CopyColumns state={state} entity={entity} run={run} onDone={() => setPicking(false)} />}
        {calculating && <CalculatedColumn state={state} entity={entity} run={run} onDone={() => setCalculating(false)} />}
        {entity.attributes.length > 6 && (
          <input
            className="input input--search"
            type="search"
            placeholder="Find a column"
            aria-label="Find a column"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        )}
        <div className="columns">
          <div className="columns__head">
            <span />
            <span>Name</span>
            <span>Type</span>
            <span title="Primary key">PK</span>
            <span title="Not null">NN</span>
            <span />
            <span />
          </div>
          {shown.map((a) => (
            <div
              key={a.id}
              className={[
                'columns__row',
                dragging === a.id ? 'columns__row--dragging' : '',
                dropAt?.id === a.id ? (dropAt.after ? 'columns__row--drop-after' : 'columns__row--drop-before') : '',
              ].join(' ')}
              onDragOver={(e) => {
                if (!dragging || dragging === a.id) return
                e.preventDefault()
                const box = e.currentTarget.getBoundingClientRect()
                setDropAt({ id: a.id, after: e.clientY > box.top + box.height / 2 })
              }}
              onDragLeave={() => setDropAt((d) => (d?.id === a.id ? null : d))}
              onDrop={(e) => {
                e.preventDefault()
                if (dragging && dropAt) move(dragging, dropAt.id, dropAt.after)
                setDragging(null)
                setDropAt(null)
              }}
            >
              <button
                className="drag-handle"
                draggable={!disabled}
                disabled={disabled}
                title="Drag to move, or Alt+↑/↓"
                aria-label={`Move ${a.name}: Alt and arrow keys`}
                onDragStart={(e) => {
                  e.dataTransfer.effectAllowed = 'move'
                  e.dataTransfer.setData('text/plain', a.name)
                  setDragging(a.id)
                }}
                onDragEnd={() => {
                  setDragging(null)
                  setDropAt(null)
                }}
                onKeyDown={(e) => {
                  if (!e.altKey || (e.key !== 'ArrowUp' && e.key !== 'ArrowDown')) return
                  e.preventDefault()
                  step(a.id, e.key === 'ArrowUp' ? -1 : 1)
                }}
              >
                ⋮⋮
              </button>
              <TextField label={`${a.name} name`} value={a.name} disabled={disabled} onSave={(name) => name && saveColumn(a.id, { name })} />
              <TextField
                label={`${a.name} type`}
                value={a.type}
                list="logical-types"
                disabled={disabled}
                onSave={(type) => type && saveColumn(a.id, { type })}
              />
              <input
                type="checkbox"
                aria-label={`${a.name} is part of the primary key`}
                checked={a.key === 'primary'}
                disabled={disabled}
                onChange={(e) => saveColumn(a.id, { key: e.target.checked ? 'primary' : null })}
              />
              <input
                type="checkbox"
                aria-label={`${a.name} is not null`}
                checked={!a.nullable}
                disabled={disabled}
                onChange={(e) => saveColumn(a.id, { nullable: !e.target.checked })}
              />
              <button
                className={
                  lineageOf?.entity === entity.name && lineageOf.column === a.name
                    ? 'icon-button lineage-button lineage-button--active'
                    : 'icon-button lineage-button'
                }
                title={`Lineage of ${a.name}: where it comes from and what uses it`}
                aria-label={`Lineage of ${a.name}`}
                aria-pressed={lineageOf?.entity === entity.name && lineageOf.column === a.name}
                onClick={() =>
                  onLineage(
                    lineageOf?.entity === entity.name && lineageOf.column === a.name
                      ? null
                      : { entity: entity.name, column: a.name },
                  )
                }
              >
                ⇄
              </button>
              <button
                className="icon-button"
                title={`Remove ${a.name}`}
                aria-label={`Remove ${a.name}`}
                disabled={disabled}
                onClick={() => careful(run, () => api.removeColumn(entity.id, a.id))}
              >
                ×
              </button>
            </div>
          ))}
          {editable && (
            <form
              className="columns__row columns__row--new"
              onSubmit={(e) => {
                e.preventDefault()
                addColumn()
              }}
            >
              <span />
              <input
                className="input"
                placeholder="new_column"
                aria-label="New column name"
                value={column.name}
                onChange={(e) => setColumn({ ...column, name: e.target.value })}
              />
              <input
                className="input"
                list="logical-types"
                aria-label="New column type"
                value={column.type}
                onChange={(e) => setColumn({ ...column, type: e.target.value })}
              />
              <button className="button button--small" type="submit" disabled={!column.name.trim()}>
                Add
              </button>
            </form>
          )}
        </div>
        <datalist id="logical-types">
          {choices.types.map((t) => (
            <option key={t} value={t} />
          ))}
        </datalist>
      </section>

      {entity.layer !== 'raw' && <Mappings state={state} entity={entity} run={run} filter={matches} />}

      <Relationships state={state} entity={entity} run={run} />

      {editable && (
        <section className="section section--danger">
          <button
            className="button button--danger"
            onClick={() =>
              careful(
                run,
                () => api.removeEntity(entity.id),
                `Remove ${entity.name}, its file and the relationships that use it?`,
              )
            }
          >
            Remove entity
          </button>
        </section>
      )}
    </>
  )
}

/** How each column is derived: `entity.column` sources and an optional SQL expression (DuckDB SQL). */
/** A new column worked out by an expression: its name, its type (suggested from the expression) and the SQL. */
function CalculatedColumn({ state, entity, run, onDone }: { state: State; entity: Entity; run: Run; onDone: () => void }) {
  const [name, setName] = useState('')
  const [type, setType] = useState('')
  return (
    <ExpressionEditor
      entities={(state.model?.entities ?? []).filter((e) => e.id !== entity.id)}
      target={entity.name}
      initial=""
      saveLabel="Add column"
      onCancel={onDone}
      onSave={async (expression, check) => {
        if (!name.trim() || !expression) return false
        const ok = await run(() =>
          api.addCalculatedColumn(entity.id, { name: name.trim(), expression, type: type.trim() || check?.type || null }),
        )
        if (ok) onDone()
        return ok
      }}
    >
      <div className="expression__fields">
        <input
          className="input"
          placeholder="column_name"
          aria-label="Calculated column name"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <input
          className="input"
          list="logical-types"
          placeholder="type (from the expression)"
          aria-label="Calculated column type"
          value={type}
          onChange={(e) => setType(e.target.value)}
        />
      </div>
    </ExpressionEditor>
  )
}

function Mappings({ state, entity, run, filter }: { state: State; entity: Entity; run: Run; filter: (name: string) => boolean }) {
  const disabled = !state.editable
  const [picking, setPicking] = useState<string | null>(null) // the column whose sources are being picked
  const [editing, setEditing] = useState<string | null>(null) // the column whose expression is open
  const others = (state.model?.entities ?? []).filter((e) => e.id !== entity.id)
  const upstream = others.flatMap((e) => e.attributes.map((a) => `${e.name}.${a.name}`))
  const mappingFor = (name: string) => entity.mappings.find((m) => m.target.toLowerCase() === name.toLowerCase())
  const save = (columnId: string, sources: string[], expression: string | null) =>
    run(() => api.setMapping(entity.id, columnId, { sources, expression }))

  return (
    <section className="section">
      <h3 className="section__title">
        Mappings <span className="muted">{entity.mappings.length} of {entity.attributes.length}</span>
      </h3>
      <p className="muted small">
        Where each column comes from: type <code>entity.column</code> (several separated by commas) or click{' '}
        <strong>☰</strong> to tick them in a list. Add an expression in DuckDB SQL to combine or calculate; it is
        translated for your warehouse.
      </p>
      <div className="mappings">
        {entity.attributes.filter((a) => filter(a.name)).map((a) => {
          const mapping = mappingFor(a.name)
          const sources = mapping?.sources ?? []
          const expression = mapping?.expression ?? null
          return (
            <div key={a.id} className="mapping-block">
              <div className={mapping ? 'mapping' : 'mapping mapping--missing'}>
                <code className="mapping__target">{a.name}</code>
                <div className="mapping__sources">
                  <TextField
                    label={`${a.name} sources`}
                    value={sources.join(', ') || null}
                    list="upstream-columns"
                    placeholder="entity.column"
                    disabled={disabled}
                    onSave={(text) => save(a.id, (text ?? '').split(',').map((t) => t.trim()).filter(Boolean), expression)}
                  />
                  {!disabled && (
                    <button
                      className="icon-button"
                      title={`Pick the sources of ${a.name}`}
                      aria-label={`Pick the sources of ${a.name}`}
                      aria-expanded={picking === a.id}
                      onClick={() => setPicking(picking === a.id ? null : a.id)}
                    >
                      ☰
                    </button>
                  )}
                </div>
                <button
                  className={expression ? 'expression-button' : 'expression-button expression-button--empty'}
                  disabled={disabled && !expression}
                  aria-expanded={editing === a.id}
                  aria-label={`${a.name} expression`}
                  title={expression ?? 'Add an expression to combine or calculate'}
                  onClick={() => setEditing(editing === a.id ? null : a.id)}
                >
                  <span className="expression-button__fx">ƒx</span>
                  <code>{expression ?? 'expression'}</code>
                </button>
              </div>
              {editing === a.id && !disabled && (
                <ExpressionEditor
                  entities={others}
                  target={entity.name}
                  initial={expression ?? ''}
                  sources={sources}
                  onCancel={() => setEditing(null)}
                  onSave={async (text, check) => {
                    // The columns an expression reads become the mapping's sources.
                    const next = text && check?.references.length ? check.references : sources
                    if (await save(a.id, next, text)) setEditing(null)
                  }}
                />
              )}
              {picking === a.id && (
                <ColumnPicker
                  entities={others}
                  initial={sources}
                  doneLabel="Use these"
                  onCancel={() => setPicking(null)}
                  onDone={async (picked) => {
                    if (await save(a.id, picked.map((c) => `${c.entity}.${c.column}`), expression)) setPicking(null)
                  }}
                />
              )}
            </div>
          )
        })}
      </div>
      <datalist id="upstream-columns">
        {upstream.map((ref) => (
          <option key={ref} value={ref} />
        ))}
      </datalist>
    </section>
  )
}

export function RelationshipPanel({ state, relationship, run }: { state: State; relationship: Relationship; run: Run }) {
  const { choices, editable } = state
  const disabled = !editable
  const save = (changes: Record<string, unknown>) =>
    careful(run, () => api.updateRelationship(relationship.id, changes))
  const issues = state.diagnostics.filter((d) => d.where?.startsWith(`relationships[${relationship.name ?? ''}`))

  return (
    <div className="panel__body">
      <div className="panel__heading panel__heading--relationship">
        <code>{relationship.from}</code>
        <span className="arrow">→</span>
        <code>{relationship.to}</code>
      </div>
      {relationship.name && <Issues diagnostics={issues} />}

      <section className="section">
        <h3 className="section__title">Join</h3>
        <ul className="keys">
          {relationship.keys.map((k) => (
            <li key={`${k.from}=${k.to}`}>
              <code>
                {relationship.from}.{k.from}
              </code>{' '}
              = <code>{relationship.to}.{k.to}</code>
            </li>
          ))}
        </ul>
      </section>

      <section className="section">
        <Row label="Cardinality">
          <SelectField
            label="Cardinality"
            value={relationship.cardinality}
            options={choices.cardinalities}
            disabled={disabled}
            onSave={(cardinality) => save({ cardinality })}
          />
        </Row>
        <Row label="Optional">
          <input
            type="checkbox"
            aria-label="A row may have no match"
            checked={relationship.optional}
            disabled={disabled}
            onChange={(e) => save({ optional: e.target.checked })}
          />
        </Row>
        <Row label="Role">
          <TextField label="Role" value={relationship.role} disabled={disabled} placeholder="e.g. pickup" onSave={(role) => save({ role })} />
        </Row>
        <Row label="Name">
          <TextField label="Name" value={relationship.name} disabled={disabled} placeholder="optional" onSave={(name) => save({ name })} />
        </Row>
        <Row label="Description">
          <TextField label="Description" value={relationship.description} multiline disabled={disabled} onSave={(description) => save({ description })} />
        </Row>
      </section>

      {editable && (
        <section className="section section--danger">
          <button className="button button--danger" onClick={() => run(() => api.removeRelationship(relationship.id))}>
            Remove relationship
          </button>
        </section>
      )}
    </div>
  )
}

type Picked = { entity: string; column: string; type: string }

/** Columns of other tables, grouped by table, with tick boxes and a search box. */
function ColumnPicker({
  entities,
  initial,
  onDone,
  onCancel,
  doneLabel,
}: {
  entities: Entity[]
  initial: string[]
  onDone: (picked: Picked[]) => void
  onCancel: () => void
  doneLabel: string
}) {
  const [ticked, setTicked] = useState<Set<string>>(() => new Set(initial.map((s) => s.toLowerCase())))
  const [search, setSearch] = useState('')
  const all = useMemo(
    () =>
      entities.flatMap((e) =>
        e.attributes.map((a) => ({ entity: e.name, column: a.name, type: a.type, key: `${e.name}.${a.name}`.toLowerCase() })),
      ),
    [entities],
  )
  const query = search.trim().toLowerCase()
  const shown = all.filter((c) => !query || c.key.includes(query))
  const groups = [...new Set(shown.map((c) => c.entity))]
  const toggle = (keys: string[], on: boolean) => {
    const next = new Set(ticked)
    keys.forEach((k) => (on ? next.add(k) : next.delete(k)))
    setTicked(next)
  }
  return (
    <div className="picker">
      <input
        className="input input--search"
        type="search"
        autoFocus
        placeholder="Find a table or column"
        aria-label="Find a table or column"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
      />
      <div className="picker__list">
        {groups.length === 0 && <p className="muted small">No columns match.</p>}
        {groups.map((group) => {
          const columns = shown.filter((c) => c.entity === group)
          const allOn = columns.every((c) => ticked.has(c.key))
          return (
            <fieldset key={group} className="picker__group">
              <legend>
                <label>
                  <input
                    type="checkbox"
                    aria-label={`All columns of ${group}`}
                    checked={allOn}
                    onChange={(e) => toggle(columns.map((c) => c.key), e.target.checked)}
                  />
                  <code>{group}</code>
                </label>
              </legend>
              {columns.map((c) => (
                <label key={c.key} className="picker__item">
                  <input
                    type="checkbox"
                    aria-label={`${c.entity}.${c.column}`}
                    checked={ticked.has(c.key)}
                    onChange={(e) => toggle([c.key], e.target.checked)}
                  />
                  <span>{c.column}</span>
                  <span className="muted">{c.type}</span>
                </label>
              ))}
            </fieldset>
          )
        })}
      </div>
      <div className="picker__actions">
        <span className="muted small">{ticked.size} ticked</span>
        <button className="button button--small" onClick={onCancel}>
          Cancel
        </button>
        <button
          className="button button--primary button--small"
          onClick={() => onDone(all.filter((c) => ticked.has(c.key)).map(({ entity, column, type }) => ({ entity, column, type })))}
        >
          {doneLabel}
        </button>
      </div>
    </div>
  )
}

/** Add columns copied from another table: same names and types, mapped to it when not raw. */
function CopyColumns({ state, entity, run, onDone }: { state: State; entity: Entity; run: Run; onDone: () => void }) {
  const others = (state.model?.entities ?? []).filter((e) => e.id !== entity.id)
  const existing = new Set(entity.attributes.map((a) => a.name.toLowerCase()))
  const add = async (picked: Picked[]) => {
    const fresh = picked.filter((c) => !existing.has(c.column.toLowerCase()))
    if (fresh.length === 0) {
      onDone()
      return
    }
    const ok = await run(async () => {
      let latest: State | null = null
      for (const c of fresh) {
        latest = await api.addColumn(entity.id, { name: c.column, type: c.type })
        if (entity.layer !== 'raw') {
          const added = latest.model?.entities
            .find((e) => e.id === entity.id)
            ?.attributes.find((a) => a.name.toLowerCase() === c.column.toLowerCase())
          if (added) latest = await api.setMapping(entity.id, added.id, { sources: [`${c.entity}.${c.column}`], expression: null })
        }
      }
      return latest!
    })
    if (ok) onDone()
  }
  return (
    <div className="copy-columns">
      <p className="muted small">
        Tick the columns to add to <code>{entity.name}</code>. They keep their names and types
        {entity.layer !== 'raw' ? ', and are mapped to where they come from' : ''}. Columns it already has are skipped.
      </p>
      <ColumnPicker entities={others} initial={[]} doneLabel="Add columns" onCancel={onDone} onDone={add} />
    </div>
  )
}

/** The relationships this entity takes part in, and a form to add one without dragging. */
function Relationships({ state, entity, run }: { state: State; entity: Entity; run: Run }) {
  const relationships = (state.model?.relationships ?? []).filter(
    (r) => r.from.toLowerCase() === entity.name.toLowerCase() || r.to.toLowerCase() === entity.name.toLowerCase(),
  )
  const others = (state.model?.entities ?? []).filter((e) => e.id !== entity.id)
  const [adding, setAdding] = useState(false)
  const [draft, setDraft] = useState({ column: '', to: '', toColumn: '', cardinality: 'many_to_one' })
  const target = others.find((e) => e.name === draft.to)
  const valid = draft.column && target && draft.toColumn

  const add = async () => {
    if (!valid) return
    const ok = await run(() =>
      api.addRelationship({
        from: entity.name,
        to: draft.to,
        keys: [{ from: draft.column, to: draft.toColumn }],
        cardinality: draft.cardinality,
      }),
    )
    if (ok) {
      setAdding(false)
      setDraft({ column: '', to: '', toColumn: '', cardinality: 'many_to_one' })
    }
  }

  return (
    <section className="section">
      <div className="section__head">
        <h3 className="section__title">
          Relationships <span className="muted">{relationships.length}</span>
        </h3>
        {state.editable && !adding && (
          <button className="button button--small" onClick={() => setAdding(true)}>
            + Relationship
          </button>
        )}
      </div>
      {relationships.length > 0 && (
        <ul className="keys">
          {relationships.map((r) => (
            <li key={r.id}>
              <code>
                {r.from}.{r.keys.map((k) => k.from).join(', ')}
              </code>{' '}
              → <code>{r.to}.{r.keys.map((k) => k.to).join(', ')}</code>
              <span className="muted"> {r.role ? `· ${r.role}` : ''}</span>
            </li>
          ))}
        </ul>
      )}
      {adding && (
        <form
          className="relationship-form"
          onSubmit={(e) => {
            e.preventDefault()
            add()
          }}
        >
          <Row label="This column">
            <select
              className="input"
              autoFocus
              value={draft.column}
              onChange={(e) => setDraft({ ...draft, column: e.target.value })}
            >
              <option value="">—</option>
              {entity.attributes.map((a) => (
                <option key={a.id}>{a.name}</option>
              ))}
            </select>
          </Row>
          <Row label="Points to table">
            <select className="input" value={draft.to} onChange={(e) => setDraft({ ...draft, to: e.target.value, toColumn: '' })}>
              <option value="">—</option>
              {others.map((e) => (
                <option key={e.id}>{e.name}</option>
              ))}
            </select>
          </Row>
          <Row label="Its column">
            <select
              className="input"
              value={draft.toColumn}
              disabled={!target}
              onChange={(e) => setDraft({ ...draft, toColumn: e.target.value })}
            >
              <option value="">—</option>
              {target?.attributes.map((a) => (
                <option key={a.id} value={a.name}>
                  {a.name}
                  {a.key === 'primary' ? ' (PK)' : ''}
                </option>
              ))}
            </select>
          </Row>
          <Row label="Cardinality">
            <select
              className="input"
              value={draft.cardinality}
              onChange={(e) => setDraft({ ...draft, cardinality: e.target.value })}
            >
              {state.choices.cardinalities.map((c) => (
                <option key={c}>{c}</option>
              ))}
            </select>
          </Row>
          <div className="dialog__actions">
            <button type="button" className="button button--small" onClick={() => setAdding(false)}>
              Cancel
            </button>
            <button type="submit" className="button button--primary button--small" disabled={!valid}>
              Add relationship
            </button>
          </div>
        </form>
      )}
    </section>
  )
}
