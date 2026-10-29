import {
  Background,
  ConnectionMode,
  Controls,
  MiniMap,
  Panel,
  ReactFlow,
  ReactFlowProvider,
  SelectionMode,
  useNodesState,
  useReactFlow,
  type Connection,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from 'react'

import { api, ApiError, type Entity, type Generated, type Layer, type LineageResult, type Position, type State } from './api'
import { ChangesPanel } from './Code'
import { DomainArea, DomainsPanel, type DomainAreaType } from './Domains'
import { EntityNode, type EntityNodeType } from './EntityNode'
import { EntityPanel, RelationshipPanel } from './Inspector'
import { domainColour, ends, handleId, nodeHeight, placeEntities } from './layout'
import { LineageBar, LineageEdge, type LineageEdgeType } from './Lineage'
import { SourcesPanel } from './Sources'
import { Legend, Markers, RelationshipEdge, type RelationshipEdgeType } from './Relationship'

const nodeTypes = { entity: EntityNode, domain: DomainArea }
const edgeTypes = { relationship: RelationshipEdge, lineage: LineageEdge }
const NODE_WIDTH = 280 // until React Flow has measured a node
const PANEL_WIDTH = { min: 320, default: 400 }

function savedPanelWidth(): number {
  try {
    return Number(window.localStorage.getItem('murakumo.panelWidth')) || PANEL_WIDTH.default
  } catch {
    return PANEL_WIDTH.default
  }
}

function remember(key: string, value: string) {
  try {
    window.localStorage.setItem(key, value)
  } catch {
    // Only a convenience.
  }
}

function recall(key: string): string | null {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

const AREA_PADDING = 28
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
  const [side, setSide] = useState<'issues' | 'changes' | 'domains' | 'sources' | null>(null)
  const [showDomains, setShowDomains] = useState(() => recall('murakumo.showDomains') === '1')
  const [domainFilter, setDomainFilter] = useState<string | null>(null)
  const [lineageOf, setLineageOf] = useState<{ entity: string; column: string | null } | null>(null)
  const [lineage, setLineage] = useState<LineageResult | null>(null)
  const [generated, setGenerated] = useState<Generated | null>(null)
  const [generating, setGenerating] = useState(false)
  const [adding, setAdding] = useState<{ name: string; layer: Layer; source: string } | null>(null)
  const [saving, setSaving] = useState(false)
  const [hovered, setHovered] = useState<string | null>(null)
  const [legend, setLegend] = useState(false)
  const [panelWidth, setPanelWidth] = useState(savedPanelWidth)
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

  // The lineage being shown, fetched again whenever the model changes.
  const revision = state?.revision
  useEffect(() => {
    if (!lineageOf) {
      setLineage(null)
      return
    }
    let current = true
    api
      .lineage(lineageOf.entity, lineageOf.column)
      .then((result) => current && setLineage(result))
      .catch((e) => {
        if (!current) return
        setLineage(null)
        setLineageOf(null)
        setMessage({ text: e instanceof Error ? e.message : String(e), tone: 'error' })
      })
    return () => {
      current = false
    }
  }, [lineageOf, revision])

  // Rebuild the nodes from the model, keeping positions the user has dragged to.
  useEffect(() => {
    if (!state) return
    const placed = placeEntities(entities, relationships, { ...state.layout, ...positions.current })
    positions.current = placed
    const issues = (id: string, level: 'error' | 'warning') =>
      diagnostics.filter((d) => d.entity_id === id && d.level === level).length
    const domainNames = state.domains.map((d) => d.name)
    setNodes((previous) => {
      // Keep measured sizes, so React Flow does not have to measure every node again, and which
      // nodes are selected: several can be, to move them together.
      const before = new Map(previous.map((n) => [n.id, n]))
      return entities
        .filter((e) => !hidden.has(e.layer))
        .filter((e) => !domainFilter || (e.domain ?? '').toLowerCase() === domainFilter.toLowerCase())
        .map((entity) => ({
          id: entity.id,
          type: 'entity' as const,
          position: placed[entity.id],
          measured: before.get(entity.id)?.measured,
          selected: before.get(entity.id)?.selected ?? false,
          data: {
            entity,
            foreignKeys: new Set(
              relationships
                .filter((r) => r.from.toLowerCase() === entity.name.toLowerCase())
                .flatMap((r) => r.keys.map((k) => k.from.toLowerCase())),
            ),
            errors: issues(entity.id, 'error'),
            warnings: issues(entity.id, 'warning'),
            domainColour: showDomains ? domainColour(domainNames, entity.domain) : undefined,
          },
        }))
    })
  }, [state, entities, relationships, diagnostics, hidden, domainFilter, showDomains, setNodes])

  const edges = useMemo<(RelationshipEdgeType | LineageEdgeType)[]>(() => {
    const shown = new Map(nodes.map((n) => [n.id, n]))
    const box = (id: string) => {
      const node = shown.get(id)!
      const width = node.measured?.width ?? NODE_WIDTH
      return { left: node.position.x, right: node.position.x + width }
    }
    const relationshipEdges = relationships.flatMap((r) => {
      const from = byName(entities, r.from)
      const to = byName(entities, r.to)
      if (!from || !to || !shown.has(from.id) || !shown.has(to.id) || r.keys.length === 0) return []
      const fromColumn = column(from, r.keys[0].from)
      const toColumn = column(to, r.keys[0].to)
      if (!fromColumn || !toColumn) return []
      // Leave from the side that faces the other table. When the tables overlap side to side (one
      // dragged above or below the other), both ends use the right-hand side, so the line goes
      // round the tables instead of underneath them.
      const a = box(from.id)
      const b = box(to.id)
      const [fromSide, toSide]: ['in' | 'out', 'in' | 'out'] =
        a.right + 40 <= b.left ? ['out', 'in'] : b.right + 40 <= a.left ? ['in', 'out'] : ['out', 'out']
      const label = [r.role, CARDINALITY[r.cardinality], r.keys.length > 1 ? `${r.keys.length} keys` : null]
        .filter(Boolean)
        .join(' · ')
      const selected = selection?.kind === 'relationship' && selection.id === r.id
      return [
        {
          id: r.id,
          source: from.id,
          target: to.id,
          sourceHandle: handleId(fromColumn.id, fromSide),
          targetHandle: handleId(toColumn.id, toSide),
          type: 'relationship' as const,
          selected,
          className: [r.optional ? 'edge--optional' : '', lineage ? 'edge--faded' : ''].join(' ').trim() || undefined,
          data: { ...ends(r.cardinality, r.optional), label, showLabel: selected || hovered === r.id },
        },
      ]
    })
    if (!lineage) return relationshipEdges
    // Lineage links: from each column to the column made from it.
    const columnId = (ref: { entity: string; column: string }) => {
      const entity = byName(entities, ref.entity)
      const attribute = entity && column(entity, ref.column)
      return entity && attribute && shown.has(entity.id) ? { entity: entity.id, column: attribute.id } : null
    }
    const lineageEdges = lineage.links.flatMap((link, i) => {
      const from = columnId(link.source)
      const to = columnId(link.target)
      if (!from || !to || from.entity === to.entity) return []
      const id = `lineage:${i}`
      return [
        {
          id,
          source: from.entity,
          target: to.entity,
          sourceHandle: handleId(from.column, 'out'),
          targetHandle: handleId(to.column, 'in'),
          type: 'lineage' as const,
          zIndex: 1,
          data: { expression: link.expression, hovered: hovered === id },
        },
      ]
    })
    return [...relationshipEdges, ...lineageEdges]
  }, [nodes, entities, relationships, selection, hovered, lineage])

  // While lineage is shown: which columns are in it (and how), and which tables fade.
  const lineageNodes = useMemo(() => {
    if (!lineage) return null
    const kinds = new Map<string, Map<string, 'focus' | 'up' | 'down'>>()
    for (const n of lineage.nodes) {
      const entity = byName(entities, n.entity)
      const attribute = entity && column(entity, n.column)
      if (!entity || !attribute) continue
      if (!kinds.has(entity.id)) kinds.set(entity.id, new Map())
      kinds.get(entity.id)!.set(attribute.id, n.depth === 0 ? 'focus' : n.depth < 0 ? 'up' : 'down')
    }
    return kinds
  }, [lineage, entities])

  // The joined columns of the selected (or hovered) relationship, highlighted in both tables.
  const linkedNodes = useMemo(() => {
    const id = selection?.kind === 'relationship' ? selection.id : hovered
    const r = relationships.find((rel) => rel.id === id)
    const from = r && byName(entities, r.from)
    const to = r && byName(entities, r.to)
    if (lineageNodes) {
      return nodes.map((n) => ({
        ...n,
        data: { ...n.data, lineage: lineageNodes.get(n.id), faded: !lineageNodes.has(n.id) },
      }))
    }
    if (!r || !from || !to) return nodes
    const ids = (entity: Entity, names: string[]) =>
      new Set(names.map((n) => column(entity, n)?.id).filter((c): c is string => Boolean(c)))
    const linked = new Map<string, Set<string>>([
      [from.id, ids(from, r.keys.map((k) => k.from))],
      [to.id, ids(to, r.keys.map((k) => k.to))],
    ])
    if (from.id === to.id) linked.set(from.id, new Set([...ids(from, r.keys.map((k) => k.from)), ...ids(to, r.keys.map((k) => k.to))]))
    return nodes.map((n) => (linked.has(n.id) ? { ...n, data: { ...n.data, linked: linked.get(n.id) } } : n))
  }, [nodes, relationships, entities, selection, hovered, lineageNodes])

  // A shaded area behind each domain's tables, drawn from where the tables are now.
  const domainAreas = useMemo<DomainAreaType[]>(() => {
    if (!showDomains || !state) return []
    const names = state.domains.map((d) => d.name)
    const boxes = new Map<string, { left: number; top: number; right: number; bottom: number }>()
    for (const node of nodes) {
      const domain = node.data.entity.domain
      if (!domain || !domainColour(names, domain)) continue
      const width = node.measured?.width ?? NODE_WIDTH
      const height = node.measured?.height ?? nodeHeight(node.data.entity)
      const box = boxes.get(domain)
      const { x, y } = node.position
      boxes.set(domain, {
        left: Math.min(box?.left ?? x, x),
        top: Math.min(box?.top ?? y, y),
        right: Math.max(box?.right ?? x + width, x + width),
        bottom: Math.max(box?.bottom ?? y + height, y + height),
      })
    }
    return [...boxes].map(([name, b]) => {
      // Sized here rather than measured: these nodes are not in the node state React Flow updates.
      const width = b.right - b.left + 2 * AREA_PADDING
      const height = b.bottom - b.top + 2 * AREA_PADDING + 18
      return {
        id: `domain:${name}`,
        type: 'domain' as const,
        position: { x: b.left - AREA_PADDING, y: b.top - AREA_PADDING - 18 },
        width,
        height,
        measured: { width, height },
        data: {
          name,
          colour: domainColour(names, name)!,
          owner: state.domains.find((d) => d.name === name)?.owner ?? null,
        },
        draggable: false,
        selectable: false,
        focusable: false,
        zIndex: -1,
      }
    })
  }, [showDomains, state, nodes])

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
    setNodes((all) => all.map((n) => ({ ...n, selected: n.id === entity.id })))
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
    const right = Math.min(panelWidth + 12, width / 3)
    const offset = width / 2 - (left + (width - left - right) / 2)
    flow.setCenter(position.x + 140 + offset, position.y + nodeHeight(entity) / 2, { zoom: 1, duration: 400 })
  }

  const filesToWrite = () => generated?.files.filter((f) => f.status !== 'unchanged').length ?? 0
  const written = (n: number) => `${n} file${n === 1 ? '' : 's'} to generated/`

  const generateNow = async () => {
    const count = filesToWrite()
    setGenerating(true)
    try {
      setGenerated(await api.generate())
      setMessage({ text: `Wrote ${written(count)}. Review them (git diff), then commit them with the model.`, tone: 'info' })
    } catch (e) {
      setMessage({ text: e instanceof Error ? e.message : String(e), tone: 'error' })
    } finally {
      setGenerating(false)
    }
  }

  // A model change that alters tables holding data needs its migration and its code together:
  // the migration first, so the code never expects columns the tables do not have yet.
  const migrateNow = async (name: string | null, alsoGenerate: boolean) => {
    const count = filesToWrite()
    setGenerating(true)
    try {
      const afterMigration = await api.writeMigration(name)
      const file = afterMigration.migration ? null : (generated?.migration?.file ?? 'the migration')
      const parts = [`Wrote the migration${file ? ` to ${file}` : ''}`]
      setGenerated(afterMigration)
      if (alsoGenerate && count) {
        setGenerated(await api.generate())
        parts.push(written(count))
      }
      setMessage({
        text: `${parts.join(' and ')}. Review them, run the migration on the warehouse, then commit everything with the model.`,
        tone: 'info',
      })
    } catch (e) {
      setMessage({ text: e instanceof Error ? e.message : String(e), tone: 'error' })
    } finally {
      setGenerating(false)
    }
  }

  const resizePanel = (event: ReactPointerEvent) => {
    event.preventDefault()
    const startX = event.clientX
    const startWidth = panelWidth
    let latest = startWidth
    const move = (e: PointerEvent) => {
      latest = Math.max(PANEL_WIDTH.min, Math.min(window.innerWidth - 80, startWidth + startX - e.clientX))
      setPanelWidth(latest)
    }
    const up = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      try {
        window.localStorage.setItem('murakumo.panelWidth', String(Math.round(latest)))
      } catch {
        // Only a convenience.
      }
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
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
        ? `${pendingFiles} file${pendingFiles === 1 ? '' : 's'} to write`
        : 'Code up to date'
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
          <span className="toolbar__divider" aria-hidden />
          <button
            className={showDomains ? 'chip' : 'chip chip--off'}
            aria-pressed={showDomains}
            title="Colour tables by domain and shade each domain's area"
            onClick={() => {
              remember('murakumo.showDomains', showDomains ? '0' : '1')
              setShowDomains(!showDomains)
            }}
          >
            Show domains
          </button>
          <button
            className="chip"
            onClick={() => setSide(side === 'sources' ? null : 'sources')}
            aria-expanded={side === 'sources'}
          >
            Sources <span className="muted">{state.sources.length}</span>
          </button>
          <button
            className="chip"
            onClick={() => setSide(side === 'domains' ? null : 'domains')}
            aria-expanded={side === 'domains'}
          >
            Domains <span className="muted">{state.domains.length}</span>
          </button>
          {domainFilter && (
            <button className="chip chip--pending" onClick={() => setDomainFilter(null)} title="Show every domain">
              only {domainFilter} ×
            </button>
          )}
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
        <Markers />
        <ReactFlow
          nodes={[...domainAreas, ...linkedNodes] as EntityNodeType[]}
          edges={edges}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          onNodesChange={onNodesChange}
          onNodeDragStop={savePositions}
          onConnect={state.editable ? connect : undefined}
          onNodeClick={(_, node) => node.type === 'entity' && setSelection({ kind: 'entity', id: node.id })}
          onEdgeClick={(_, edge) => {
            if (edge.type === 'lineage') return
            setSelection({ kind: 'relationship', id: edge.id })
            setNodes((all) => all.map((n) => (n.selected ? { ...n, selected: false } : n)))
          }}
          onEdgeMouseEnter={(_, edge) => setHovered(edge.id)}
          onEdgeMouseLeave={() => setHovered(null)}
          onPaneClick={() => setSelection(null)}
          selectionMode={SelectionMode.Partial}
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
          <Panel position="bottom-left" className="legend-panel">
            {legend ? (
              <Legend onClose={() => setLegend(false)} />
            ) : (
              <button className="chip" onClick={() => setLegend(true)} aria-expanded={false}>
                Notation &amp; tips
              </button>
            )}
          </Panel>
          {lineage && (
            <Panel position="top-center">
              <LineageBar
                lineage={lineage}
                onClose={() => setLineageOf(null)}
                onPick={(ref) => {
                  setLineageOf(ref)
                  const entity = byName(entities, ref.entity)
                  if (entity) focus(entity)
                }}
              />
            </Panel>
          )}
          {entities.length === 0 && (
            <Panel position="top-center" className="empty">
              No entities yet. Add one with <strong>+ Entity</strong>, or run <code>murakumo import</code>.
            </Panel>
          )}
          {state.editable && entities.length > 1 && relationships.length === 0 && (
            <Panel position="bottom-center" className="hint">
              Drag from a column to a column of another entity to add a relationship, or select an entity and use
              <strong> + Relationship</strong>.
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

        {side === 'sources' && (
          <aside className="issues-panel" aria-label="Sources">
            <div className="issues-panel__head">
              <strong>Sources</strong>
              <button className="icon-button" aria-label="Close" onClick={() => setSide(null)}>
                ×
              </button>
            </div>
            <SourcesPanel state={state} run={run} onMessage={(text) => setMessage({ text, tone: 'info' })} />
          </aside>
        )}

        {side === 'domains' && (
          <aside className="issues-panel" aria-label="Domains">
            <div className="issues-panel__head">
              <strong>Domains</strong>
              <button className="icon-button" aria-label="Close" onClick={() => setSide(null)}>
                ×
              </button>
            </div>
            <DomainsPanel state={state} run={run} filter={domainFilter} onFilter={setDomainFilter} />
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
          <aside className="panel" aria-label="Inspector" style={{ '--panel-width': `${panelWidth}px` } as CSSProperties}>
            <div
              className="panel__resize"
              role="separator"
              aria-orientation="vertical"
              aria-label="Drag to resize the panel"
              title="Drag to resize"
              onPointerDown={resizePanel}
            />
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
                lineageOf={lineageOf}
                onLineage={setLineageOf}
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
