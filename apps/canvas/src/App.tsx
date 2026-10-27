import {
  Background,
  ConnectionMode,
  Controls,
  MarkerType,
  MiniMap,
  Panel,
  ReactFlow,
  ReactFlowProvider,
  useNodesState,
  useReactFlow,
  type Connection,
  type Edge,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { api, ApiError, type Entity, type Generated, type Layer, type Position, type State } from './api'
import { ChangesPanel } from './Code'
import { EntityNode, type EntityNodeType } from './EntityNode'
import { EntityPanel, RelationshipPanel } from './Inspector'
import { handleId, nodeHeight, placeEntities } from './layout'

const nodeTypes = { entity: EntityNode }
const POLL_MS = 2000

const CARDINALITY: Record<string, string> = {
  many_to_one: 'N:1',
  one_to_many: '1:N',
  one_to_one: '1:1',
  many_to_many: 'N:N',
}

type Selection = { kind: 'entity' | 'relationship'; id: string } | null

const byName = (entities: Entity[], name: string) => entities.find((e) => e.name.toLowerCase() === name.toLowerCase())
const column = (entity: Entity, name: string) => entity.attributes.find((a) => a.name.toLowerCase() === name.toLowerCase())

function Canvas() {
  const flow = useReactFlow()
  const [state, setState] = useState<State | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [message, setMessage] = useState<{ text: string; tone: 'error' | 'info' } | null>(null)
  const [selection, setSelection] = useState<Selection>(null)
  const [hidden, setHidden] = useState<Set<Layer>>(new Set())
  const [side, setSide] = useState<'issues' | 'changes' | null>(null)
  const [generated, setGenerated] = useState<Generated | null>(null)
  const [generating, setGenerating] = useState(false)
  const [adding, setAdding] = useState<{ name: string; layer: Layer; source: string } | null>(null)
  const [saving, setSaving] = useState(false)
  const [nodes, setNodes, onNodesChange] = useNodesState<EntityNodeType>([])
  const busy = useRef(0)
  const generatedRevision = useRef<string | undefined>(undefined)
  const positions = useRef<Record<string, Position>>({})
  const refreshTimer = useRef<number | undefined>(undefined)

  useEffect(() => {
    generatedRevision.current = generated?.revision
  }, [generated])

  // Validation and generation problems together, as one list.
  const diagnostics = useMemo(
    () => [...(state?.diagnostics ?? []), ...(generated?.diagnostics ?? [])],
    [state, generated],
  )

  const entities = useMemo(() => state?.model?.entities ?? [], [state])
  const relationships = useMemo(() => state?.model?.relationships ?? [], [state])

  // What generating now would change. Cheap (tens of milliseconds), so it follows every edit.
  const loadGenerated = useCallback(() => {
    window.clearTimeout(refreshTimer.current)
    refreshTimer.current = window.setTimeout(async () => {
      try {
        setGenerated(await api.generated())
      } catch {
        // Shown again on the next successful refresh.
      }
    }, 250)
  }, [])

  const load = useCallback(async () => {
    try {
      setState(await api.state())
      setLoadError(null)
      loadGenerated()
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e))
    }
  }, [loadGenerated])

  // Every change goes through here: the server saves, validates and returns the new state.
  const run = useCallback(async (action: () => Promise<State>) => {
    busy.current += 1
    setSaving(true)
    try {
      setState(await action())
      setMessage(null)
      loadGenerated()
      return true
    } catch (e) {
      if (e instanceof ApiError) {
        if (e.state) setState(e.state)
        setMessage({ text: e.message, tone: e.status === 409 ? 'info' : 'error' })
      } else {
        setMessage({ text: String(e), tone: 'error' })
      }
      return false
    } finally {
      busy.current -= 1
      setSaving(busy.current > 0)
    }
  }, [loadGenerated])

  useEffect(() => {
    load()
  }, [load])

  // Pick up changes made in an editor or with the CLI.
  useEffect(() => {
    const timer = window.setInterval(async () => {
      if (busy.current > 0 || document.visibilityState !== 'visible') return
      try {
        const revision = await api.revision()
        if (revision.revision !== api.currentRevision() && busy.current === 0) await load()
        else if (generatedRevision.current && revision.generated !== generatedRevision.current) loadGenerated()
      } catch {
        // The server stopped; the next successful poll reloads.
      }
    }, POLL_MS)
    return () => window.clearInterval(timer)
  }, [load, loadGenerated])

  // Rebuild the nodes from the model, keeping positions the user has dragged to.
  useEffect(() => {
    if (!state) return
    const placed = placeEntities(entities, relationships, { ...state.layout, ...positions.current })
    positions.current = placed
    const issues = (id: string, level: 'error' | 'warning') =>
      diagnostics.filter((d) => d.entity_id === id && d.level === level).length
    setNodes((previous) => {
      // Keep measured sizes, so React Flow does not have to measure every node again.
      const measured = new Map(previous.map((n) => [n.id, n.measured]))
      return entities
        .filter((e) => !hidden.has(e.layer))
        .map((entity) => ({
          id: entity.id,
          type: 'entity' as const,
          position: placed[entity.id],
          measured: measured.get(entity.id),
          selected: selection?.kind === 'entity' && selection.id === entity.id,
          data: {
            entity,
            foreignKeys: new Set(
              relationships
                .filter((r) => r.from.toLowerCase() === entity.name.toLowerCase())
                .flatMap((r) => r.keys.map((k) => k.from.toLowerCase())),
            ),
            errors: issues(entity.id, 'error'),
            warnings: issues(entity.id, 'warning'),
          },
        }))
    })
  }, [state, entities, relationships, diagnostics, hidden, selection, setNodes])

  const edges = useMemo<Edge[]>(() => {
    const shown = new Map(nodes.map((n) => [n.id, n]))
    return relationships.flatMap((r) => {
      const from = byName(entities, r.from)
      const to = byName(entities, r.to)
      if (!from || !to || !shown.has(from.id) || !shown.has(to.id) || r.keys.length === 0) return []
      const fromColumn = column(from, r.keys[0].from)
      const toColumn = column(to, r.keys[0].to)
      if (!fromColumn || !toColumn) return []
      // Leave from the side that faces the other table.
      const rightwards = shown.get(from.id)!.position.x <= shown.get(to.id)!.position.x
      const label = [r.role, CARDINALITY[r.cardinality], r.keys.length > 1 ? `${r.keys.length} keys` : null]
        .filter(Boolean)
        .join(' · ')
      const selected = selection?.kind === 'relationship' && selection.id === r.id
      return [
        {
          id: r.id,
          source: from.id,
          target: to.id,
          sourceHandle: handleId(fromColumn.id, rightwards ? 'out' : 'in'),
          targetHandle: handleId(toColumn.id, rightwards ? 'in' : 'out'),
          label,
          type: 'smoothstep',
          selected,
          className: r.optional ? 'edge--optional' : undefined,
          markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
        },
      ]
    })
  }, [nodes, entities, relationships, selection])

  const connect = useCallback(
    (connection: Connection) => {
      const from = entities.find((e) => e.id === connection.source)
      const to = entities.find((e) => e.id === connection.target)
      const fromColumn = from?.attributes.find((a) => connection.sourceHandle?.startsWith(a.id))
      const toColumn = to?.attributes.find((a) => connection.targetHandle?.startsWith(a.id))
      if (!from || !to || !fromColumn || !toColumn) return
      if (from.id === to.id) {
        setMessage({ text: 'Drag to a column of another entity.', tone: 'info' })
        return
      }
      run(() =>
        api.addRelationship({ from: from.name, to: to.name, keys: [{ from: fromColumn.name, to: toColumn.name }] }),
      )
    },
    [entities, run],
  )

  const savePositions = useCallback((_: unknown, __: unknown, moved: EntityNodeType[]) => {
    const changed: Record<string, Position> = {}
    for (const node of moved) {
      changed[node.id] = { x: Math.round(node.position.x), y: Math.round(node.position.y) }
    }
    positions.current = { ...positions.current, ...changed }
    api.saveLayout(changed).catch((e) => setMessage({ text: e.message, tone: 'error' }))
  }, [])

  const addEntity = async () => {
    if (!adding || !adding.name.trim()) return
    const { x, y } = flow.screenToFlowPosition({ x: window.innerWidth / 2 - 280, y: window.innerHeight / 3 })
    const ok = await run(() =>
      api.addEntity({
        name: adding.name.trim(),
        layer: adding.layer,
        source: adding.layer === 'raw' && adding.source ? adding.source : null,
        position: { x: Math.round(x), y: Math.round(y) },
      }),
    )
    if (ok) setAdding(null)
  }

  // Select the entity just added, so its columns can be filled in.
  const previous = useRef<Set<string>>(new Set())
  useEffect(() => {
    const ids = new Set(entities.map((e) => e.id))
    const added = entities.filter((e) => !previous.current.has(e.id))
    if (previous.current.size > 0 && added.length === 1) setSelection({ kind: 'entity', id: added[0].id })
    previous.current = ids
  }, [entities])

  const focus = (entity: Entity) => {
    setSelection({ kind: 'entity', id: entity.id })
    const position = positions.current[entity.id]
    if (!position) return
    if (hidden.has(entity.layer)) {
      const next = new Set(hidden)
      next.delete(entity.layer)
      setHidden(next)
    }
    // Centre it in the part of the canvas the side panels do not cover (at zoom 1, a pixel is a unit).
    const width = window.innerWidth
    const left = side ? Math.min(432, width / 3) : 0
    const right = Math.min(412, width / 3)
    const offset = width / 2 - (left + (width - left - right) / 2)
    flow.setCenter(position.x + 140 + offset, position.y + nodeHeight(entity) / 2, { zoom: 1, duration: 400 })
  }

  const generateNow = async () => {
    setGenerating(true)
    try {
      setGenerated(await api.generate())
      setMessage({ text: 'Wrote generated/.', tone: 'info' })
    } catch (e) {
      setMessage({ text: e instanceof Error ? e.message : String(e), tone: 'error' })
    } finally {
      setGenerating(false)
    }
  }

  const migrateNow = async (name: string | null) => {
    const file = generated?.migration?.file
    setGenerating(true)
    try {
      setGenerated(await api.writeMigration(name))
      setMessage({ text: `Wrote ${name ? 'the migration' : file}. Review it and commit it with the model.`, tone: 'info' })
    } catch (e) {
      setMessage({ text: e instanceof Error ? e.message : String(e), tone: 'error' })
    } finally {
      setGenerating(false)
    }
  }

  if (!state) {
    return (
      <div className="splash">
        {loadError ? (
          <>
            <p className="splash__error">{loadError}</p>
            <p className="muted">
              Start the API with <code>murakumo canvas</code> in a project folder, then reload.
            </p>
            <button className="button" onClick={load}>
              Try again
            </button>
          </>
        ) : (
          <p className="muted">Loading the model…</p>
        )}
      </div>
    )
  }

  const errors = diagnostics.filter((d) => d.level === 'error')
  const warnings = diagnostics.filter((d) => d.level === 'warning')
  const count = (n: number, word: string) => (n ? `${n} ${word}${n === 1 ? '' : 's'}` : null)
  const problemsLabel =
    [count(errors.length, 'error'), count(warnings.length, 'warning')].filter(Boolean).join(' · ') || 'Valid'
  const pendingFiles = generated?.files.filter((f) => f.status !== 'unchanged').length ?? 0
  const migration = generated?.migration
  const changesLabel = !generated
    ? 'Generating…'
    : generated.blocked
      ? 'Cannot generate'
      : pendingFiles
        ? `${pendingFiles} file${pendingFiles === 1 ? '' : 's'} to generate`
        : 'Generated'
  const selectedEntity = selection?.kind === 'entity' ? entities.find((e) => e.id === selection.id) : undefined
  const selectedRelationship =
    selection?.kind === 'relationship' ? relationships.find((r) => r.id === selection.id) : undefined

  return (
    <div className={selectedEntity || selectedRelationship ? 'app app--inspecting' : 'app'}>
      <header className="toolbar">
        <div className="toolbar__project">
          <span className="logo" aria-hidden>
            ☁
          </span>
          <strong>{state.project}</strong>
          <span className="muted">
            {entities.length} entities · {relationships.length} relationships
          </span>
        </div>
        <div className="toolbar__layers" role="group" aria-label="Show layers">
          {state.choices.layers.map((layer) => {
            const count = entities.filter((e) => e.layer === layer).length
            if (count === 0) return null
            const off = hidden.has(layer)
            return (
              <button
                key={layer}
                className={off ? 'chip chip--off' : 'chip'}
                aria-pressed={!off}
                onClick={() => {
                  const next = new Set(hidden)
                  if (off) next.delete(layer)
                  else next.add(layer)
                  setHidden(next)
                }}
              >
                <span className={`dot dot--${layer}`} />
                {layer} <span className="muted">{count}</span>
              </button>
            )
          })}
        </div>
        <div className="toolbar__actions">
          <span className="status" aria-live="polite">
            {saving ? 'Saving…' : 'Saved to model/'}
          </span>
          <button
            className={pendingFiles || (migration && !migration.baseline) ? 'chip chip--pending' : 'chip'}
            onClick={() => setSide(side === 'changes' ? null : 'changes')}
            aria-expanded={side === 'changes'}
          >
            {changesLabel}
            {migration && !migration.baseline && <span className="chip__extra"> · migration</span>}
          </button>
          <button
            className={errors.length ? 'chip chip--error' : warnings.length ? 'chip chip--warning' : 'chip chip--ok'}
            onClick={() => setSide(side === 'issues' ? null : 'issues')}
            aria-expanded={side === 'issues'}
          >
            {problemsLabel}
          </button>
          {state.editable && (
            <button className="button button--primary" onClick={() => setAdding({ name: '', layer: 'raw', source: '' })}>
              + Entity
            </button>
          )}
        </div>
      </header>

      {!state.editable && (
        <div className="banner banner--error">
          Some model files did not load, so the canvas is read-only. Fix the errors below, and it updates by itself.
        </div>
      )}
      {message && (
        <div className={`banner banner--${message.tone}`} role="alert">
          {message.text}
          <button className="icon-button" aria-label="Dismiss" onClick={() => setMessage(null)}>
            ×
          </button>
        </div>
      )}

      <main className="stage">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          onNodesChange={onNodesChange}
          onNodeDragStop={savePositions}
          onConnect={state.editable ? connect : undefined}
          onNodeClick={(_, node) => setSelection({ kind: 'entity', id: node.id })}
          onEdgeClick={(_, edge) => setSelection({ kind: 'relationship', id: edge.id })}
          onPaneClick={() => setSelection(null)}
          nodesConnectable={state.editable}
          connectionMode={ConnectionMode.Loose}
          deleteKeyCode={null}
          fitView
          fitViewOptions={{ maxZoom: 1 }}
          minZoom={0.1}
          colorMode="system"
        >
          <Background gap={24} />
          <Controls />
          <MiniMap pannable zoomable />
          {entities.length === 0 && (
            <Panel position="top-center" className="empty">
              No entities yet. Add one with <strong>+ Entity</strong>, or run <code>murakumo import</code>.
            </Panel>
          )}
          {state.editable && entities.length > 1 && relationships.length === 0 && (
            <Panel position="bottom-center" className="hint">
              Drag from a column to a column of another entity to add a relationship.
            </Panel>
          )}
        </ReactFlow>

        {side === 'changes' && (
          <aside className="issues-panel" aria-label="Changes">
            <div className="issues-panel__head">
              <strong>Changes</strong>
              <button className="icon-button" aria-label="Close" onClick={() => setSide(null)}>
                ×
              </button>
            </div>
            <ChangesPanel
              generated={generated}
              entityName={(id) => entities.find((e) => e.id === id)?.name}
              onGenerate={generateNow}
              onMigrate={migrateNow}
              busy={generating}
            />
          </aside>
        )}

        {side === 'issues' && (
          <aside className="issues-panel" aria-label="Validation">
            <div className="issues-panel__head">
              <strong>Problems</strong>
              <button className="icon-button" aria-label="Close" onClick={() => setSide(null)}>
                ×
              </button>
            </div>
            {diagnostics.length === 0 && <p className="muted">The model is valid and generates cleanly.</p>}
            <ul>
              {[...errors, ...warnings].map((d, i) => {
                const entity = entities.find((e) => e.id === d.entity_id)
                return (
                  <li key={i}>
                    <button
                      className={`issue issue--${d.level} issue--button`}
                      disabled={!entity}
                      onClick={() => entity && focus(entity)}
                    >
                      <span className="issue__code">{d.code}</span>
                      <span>
                        {entity && <strong>{entity.name}: </strong>}
                        {d.message}
                        {!entity && d.file && <span className="issue__where"> ({d.file})</span>}
                      </span>
                    </button>
                  </li>
                )
              })}
            </ul>
          </aside>
        )}

        {(selectedEntity || selectedRelationship) && (
          <aside className="panel" aria-label="Inspector">
            <button className="icon-button panel__close" aria-label="Close" onClick={() => setSelection(null)}>
              ×
            </button>
            {selectedEntity && (
              <EntityPanel
                key={selectedEntity.id}
                state={state}
                entity={selectedEntity}
                run={run}
                diagnostics={diagnostics}
                generatedRevision={generated?.revision ?? ''}
              />
            )}
            {selectedRelationship && (
              <RelationshipPanel key={selectedRelationship.id} state={state} relationship={selectedRelationship} run={run} />
            )}
          </aside>
        )}
      </main>

      {adding && (
        <div className="dialog-backdrop" onClick={() => setAdding(null)}>
          <form
            className="dialog"
            onClick={(e) => e.stopPropagation()}
            onSubmit={(e) => {
              e.preventDefault()
              addEntity()
            }}
          >
            <h2>New entity</h2>
            <label className="row">
              <span className="row__label">Name</span>
              <input
                className="input"
                autoFocus
                placeholder="dim_customer"
                value={adding.name}
                onChange={(e) => setAdding({ ...adding, name: e.target.value })}
              />
            </label>
            <label className="row">
              <span className="row__label">Layer</span>
              <select
                className="input"
                value={adding.layer}
                onChange={(e) => setAdding({ ...adding, layer: e.target.value as Layer })}
              >
                {state.choices.layers.map((l) => (
                  <option key={l}>{l}</option>
                ))}
              </select>
            </label>
            {adding.layer === 'raw' && (
              <label className="row">
                <span className="row__label">Source</span>
                <select
                  className="input"
                  value={adding.source}
                  onChange={(e) => setAdding({ ...adding, source: e.target.value })}
                >
                  <option value="">—</option>
                  {state.choices.sources.map((s) => (
                    <option key={s}>{s}</option>
                  ))}
                </select>
              </label>
            )}
            <div className="dialog__actions">
              <button type="button" className="button" onClick={() => setAdding(null)}>
                Cancel
              </button>
              <button type="submit" className="button button--primary" disabled={!adding.name.trim()}>
                Add
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  )
}

export default function App() {
  return (
    <ReactFlowProvider>
      <Canvas />
    </ReactFlowProvider>
  )
}
