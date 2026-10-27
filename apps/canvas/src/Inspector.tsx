import { useState } from 'react'

import { api, type Diagnostic, type Entity, type Relationship, type State } from './api'
import { EntityCodeView } from './Code'
import { Row, SelectField, TextField } from './Field'

type Run = (action: () => Promise<State>) => Promise<boolean>

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

type EntityProps = { state: State; entity: Entity; run: Run; diagnostics: Diagnostic[]; generatedRevision: string }

export function EntityPanel({ state, entity, run, diagnostics, generatedRevision }: EntityProps) {
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
        <EntityForm state={state} entity={entity} run={run} issues={issues} />
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

function EntityForm({ state, entity, run, issues }: { state: State; entity: Entity; run: Run; issues: Diagnostic[] }) {
  const { choices, editable } = state
  const disabled = !editable
  const save = (changes: Record<string, unknown>) => run(() => api.updateEntity(entity.id, changes))
  const saveColumn = (columnId: string, changes: Record<string, unknown>) =>
    run(() => api.updateColumn(entity.id, columnId, changes))
  const [column, setColumn] = useState({ name: '', type: 'string' })

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
          <TextField label="Domain" value={entity.domain} disabled={disabled} placeholder="none" onSave={(domain) => save({ domain })} />
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
        <h3 className="section__title">
          Columns <span className="muted">{entity.attributes.length}</span>
        </h3>
        <div className="columns">
          <div className="columns__head">
            <span>Name</span>
            <span>Type</span>
            <span title="Primary key">PK</span>
            <span title="Not null">NN</span>
            <span />
          </div>
          {entity.attributes.map((a) => (
            <div key={a.id} className="columns__row">
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
                className="icon-button"
                title={`Remove ${a.name}`}
                aria-label={`Remove ${a.name}`}
                disabled={disabled}
                onClick={() => run(() => api.removeColumn(entity.id, a.id))}
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

      {entity.layer !== 'raw' && <Mappings state={state} entity={entity} run={run} />}

      {editable && (
        <section className="section section--danger">
          <button
            className="button button--danger"
            onClick={() => {
              if (window.confirm(`Remove ${entity.name}, its file and the relationships that use it?`)) {
                run(() => api.removeEntity(entity.id))
              }
            }}
          >
            Remove entity
          </button>
        </section>
      )}
    </>
  )
}

/** How each column is derived: `entity.column` sources and an optional SQL expression (DuckDB SQL). */
function Mappings({ state, entity, run }: { state: State; entity: Entity; run: Run }) {
  const disabled = !state.editable
  const upstream = (state.model?.entities ?? [])
    .filter((e) => e.id !== entity.id)
    .flatMap((e) => e.attributes.map((a) => `${e.name}.${a.name}`))
  const mappingFor = (name: string) => entity.mappings.find((m) => m.target.toLowerCase() === name.toLowerCase())
  const save = (columnId: string, sources: string[], expression: string | null) =>
    run(() => api.setMapping(entity.id, columnId, { sources, expression }))

  return (
    <section className="section">
      <h3 className="section__title">
        Mappings <span className="muted">{entity.mappings.length} of {entity.attributes.length}</span>
      </h3>
      <p className="muted small">
        Where each column comes from: <code>entity.column</code>, separated by commas, and an optional expression in
        DuckDB SQL, translated for your warehouse.
      </p>
      <div className="mappings">
        {entity.attributes.map((a) => {
          const mapping = mappingFor(a.name)
          const sources = mapping?.sources ?? []
          const expression = mapping?.expression ?? null
          return (
            <div key={a.id} className={mapping ? 'mapping' : 'mapping mapping--missing'}>
              <code className="mapping__target">{a.name}</code>
              <TextField
                label={`${a.name} sources`}
                value={sources.join(', ') || null}
                list="upstream-columns"
                placeholder="entity.column"
                disabled={disabled}
                onSave={(text) => save(a.id, (text ?? '').split(',').map((t) => t.trim()).filter(Boolean), expression)}
              />
              <TextField
                label={`${a.name} expression`}
                value={expression}
                placeholder="expression (optional)"
                disabled={disabled}
                onSave={(text) => save(a.id, sources, text)}
              />
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
  const save = (changes: Record<string, unknown>) => run(() => api.updateRelationship(relationship.id, changes))
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
