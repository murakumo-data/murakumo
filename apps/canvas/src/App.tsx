import {
  Background,
  BackgroundVariant,
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
import {
  AlignLeft,
  BookOpen,
  CalendarClock,
  CircleHelp,
  Cloud,
  Database,
  FileCode2,
  GitBranch,
  ImageDown,
  KeyRound,
  LayoutGrid,
  ListTree,
  Moon,
  RectangleHorizontal,
  Redo2,
  Rows3,
  Search as SearchIcon,
  SlidersHorizontal,
  Spline,
  SquarePlus,
  StickyNote,
  Sun,
  SunMoon,
  TriangleAlert,
  Undo2,
  Workflow,
} from 'lucide-react'
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from 'react'

import { api, ApiError, type Entity, type Generated, type Layer, type LineageResult, type Note, type Position, type State } from './api'
import { CIPanel } from './CI'
import { GitPanel } from './Git'
import { ChangesPanel } from './Code'
import { DomainArea, DomainsPanel, type DomainAreaType } from './Domains'
import { arrange, exportImage, type Direction } from './arrange'
import { BulkBar } from './BulkBar'
import { DiagramTabs } from './Diagrams'
import { EntityNode, tableHandle, type EntityNodeType } from './EntityNode'
import { Explorer } from './Explorer'
import { Shortcuts, Tour } from './Help'
import { EntityPanel, RelationshipPanel } from './Inspector'
import {
  domainColour,
  ends,
  handleId,
  isCollapsed,
  nodeHeight,
  placeEntities,
  VIEW_MODES,
  visibleColumns,
  type ViewMode,
} from './layout'
import { LineageBar, LineageEdge, type LineageEdgeType } from './Lineage'
import { NewRelationship } from './NewRelationship'
import { NoteNode, type NoteNodeType } from './Notes'
import { SchedulesPanel } from './Schedules'
import { Search } from './Search'
import { SourcesPanel } from './Sources'
import { Legend, Markers, RelationshipEdge, type RelationshipEdgeType } from './Relationship'

const nodeTypes = { entity: EntityNode, domain: DomainArea, note: NoteNode }
type AppNode = EntityNodeType | NoteNodeType
const isTable = (n: AppNode): n is EntityNodeType => n.type === 'entity'
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
type Side = 'explorer' | 'issues' | 'changes' | 'domains' | 'sources' | 'schedules' | 'ci' | 'git'
type Theme = 'system' | 'light' | 'dark'

const SIDES: Side[] = ['explorer', 'issues', 'changes', 'domains', 'sources', 'schedules', 'ci', 'git']

function savedSide(): Side | null {
  const saved = recall('murakumo.side')
  // The Explorer is open the first time, except on a phone, where it would cover the canvas.
  if (saved === null) return window.innerWidth < 760 ? null : 'explorer'
  return SIDES.includes(saved as Side) ? (saved as Side) : null
}

function savedView(): ViewMode {
  const saved = recall('murakumo.view')
  return VIEW_MODES.some((m) => m.id === saved) ? (saved as ViewMode) : 'all'
}

const VIEW_ICONS: Record<ViewMode, ReactNode> = {
  all: <Rows3 size={15} />,
  keys: <KeyRound size={15} />,
  names: <RectangleHorizontal size={15} />,
  descriptions: <AlignLeft size={15} />,
}

function savedTheme(): Theme {
  const saved = recall('murakumo.theme')
  return saved === 'light' || saved === 'dark' ? saved : 'system'
}

function RailButton(props: { label: string; active?: boolean; disabled?: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      className={props.active ? 'rail__button rail__button--active' : 'rail__button'}
      title={props.label}
      aria-label={props.label}
      aria-pressed={props.active}
      disabled={props.disabled}
      onClick={props.onClick}
    >
      {props.children}
    </button>
  )
}

const byName = (entities: Entity[], name: string) => entities.find((e) => e.name.toLowerCase() === name.toLowerCase())
const column = (entity: Entity, name: string) => entity.attributes.find((a) => a.name.toLowerCase() === name.toLowerCase())

function Canvas() {
  const flow = useReactFlow()
  const [state, setState] = useState<State | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [message, setMessage] = useState<{ text: string; tone: 'error' | 'info' } | null>(null)
  const [selection, setSelection] = useState<Selection>(null)
  const [hidden, setHidden] = useState<Set<Layer>>(new Set())
  const [side, setSide] = useState<Side | null>(savedSide)
  const [searching, setSearching] = useState(false)
  const [relating, setRelating] = useState(false)
  const [theme, setTheme] = useState<Theme>(savedTheme)
  const [view, setView] = useState<ViewMode>(savedView)
  const [displayOpen, setDisplayOpen] = useState(false)
  const [scheduleShown, setScheduleShown] = useState<{ name: string; entities: string[] } | null>(null)
  const shownSchedule = side === 'schedules' ? scheduleShown : null // only while its panel is open
  const [showDomains, setShowDomains] = useState(() => recall('murakumo.showDomains') === '1')
  const [domainFilter, setDomainFilter] = useState<string | null>(null)
  const [layoutUndo, setLayoutUndo] = useState<Record<string, Position> | null>(null)
  const [flash, setFlash] = useState<{ entity: string; column: string } | null>(null)
  const [lineageOf, setLineageOf] = useState<{ entity: string; column: string | null } | null>(null)
  const [lineage, setLineage] = useState<LineageResult | null>(null)
  const [generated, setGenerated] = useState<Generated | null>(null)
  const [generating, setGenerating] = useState(false)
  const [adding, setAdding] = useState<{ name: string; layer: Layer; source: string } | null>(null)
  const [saving, setSaving] = useState(false)
  const [hovered, setHovered] = useState<string | null>(null)
  const [legend, setLegend] = useState(false)
  const [panelWidth, setPanelWidth] = useState(savedPanelWidth)
  const [nodes, setNodes, onNodesChange] = useNodesState<AppNode>([])
  const tableNodes = useMemo(() => nodes.filter(isTable), [nodes])
  const [diagram, setDiagram] = useState<string | null>(() => recall('murakumo.diagram') || null)
  const [labelFilter, setLabelFilter] = useState<string | null>(null)
  const [menu, setMenu] = useState<'arrange' | 'export' | 'help' | null>(null)
  const [shortcuts, setShortcuts] = useState(false)
  const [touring, setTouring] = useState(false)
  const canvasRef = useRef<State['canvas']>({})
  const addNoteRef = useRef<() => void>(() => {})
  const busy = useRef(0)
  const editable = useRef(false)
  const showMessage = useCallback((text: string, tone: 'info' | 'error') => setMessage({ text, tone }), [])
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
  useEffect(() => {
    editable.current = Boolean(state?.editable)
  }, [state])
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
      const next = await action()
      setState(next)
      setMessage(next.message ? { text: next.message, tone: 'info' } : null)
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

  // The first time the canvas opens, a short tour; the help button starts it again.
  const loaded = state !== null
  useEffect(() => {
    if (loaded && recall('murakumo.toured') === null) setTouring(true)
  }, [loaded])

  // The theme: the system's, or light or dark as chosen, for the page and the canvas.
  useEffect(() => {
    if (theme === 'system') document.documentElement.removeAttribute('data-theme')
    else document.documentElement.setAttribute('data-theme', theme)
  }, [theme])
  const chooseView = useCallback((next: ViewMode) => {
    remember('murakumo.view', next)
    setView(next)
  }, [])
  const cycleTheme = () => {
    const next: Theme = theme === 'system' ? 'light' : theme === 'light' ? 'dark' : 'system'
    remember('murakumo.theme', next)
    setTheme(next)
  }

  // Keys: ⌘K finds, T adds a table, R a relationship; ⌘Z / Ctrl+Z undoes, ⇧⌘Z / Ctrl+Y redoes.
  // Inside a text field, keys stay the field's own.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target instanceof HTMLElement ? event.target : null
      if (target && (target.closest('input, textarea, select, [contenteditable="true"]') || target.isContentEditable)) return
      const key = event.key.toLowerCase()
      if ((event.metaKey || event.ctrlKey) && key === 'k') {
        event.preventDefault()
        setSearching(true)
        return
      }
      if (document.querySelector('.dialog-backdrop')) return
      if (event.key === '?' || (event.key === '/' && event.shiftKey)) {
        event.preventDefault()
        setShortcuts(true)
        return
      }
      if (!event.metaKey && !event.ctrlKey && !event.altKey && editable.current) {
        if (key === 't') {
          event.preventDefault()
          setAdding({ name: '', layer: 'raw', source: '' })
        } else if (key === 'r') {
          event.preventDefault()
          setRelating(true)
        } else if (key === 'n') {
          event.preventDefault()
          addNoteRef.current()
        }
        return
      }
      if (!(event.metaKey || event.ctrlKey)) return
      if (['1', '2', '3', '4'].includes(key)) {
        event.preventDefault()
        chooseView(VIEW_MODES[Number(key) - 1].id)
        return
      }
      const redo = (key === 'z' && event.shiftKey) || (key === 'y' && event.ctrlKey)
      if (key !== 'z' && !redo) return
      event.preventDefault()
      run(redo ? api.redo : api.undo)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [run, chooseView])

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
    const columnIssues = new Map<string, Map<string, { level: 'error' | 'warning'; text: string }>>()
    for (const d of diagnostics) {
      if (!d.entity_id || !d.column_id) continue
      const byColumn = columnIssues.get(d.entity_id) ?? new Map()
      const seen = byColumn.get(d.column_id)
      byColumn.set(d.column_id, {
        level: seen?.level === 'error' ? 'error' : d.level,
        text: [seen?.text, `${d.code}: ${d.message}`].filter(Boolean).join('\n'),
      })
      columnIssues.set(d.entity_id, byColumn)
    }
    const domainNames = state.domains.map((d) => d.name)
    const canvas = state.canvas ?? {}
    canvasRef.current = canvas
    const shownDiagram = canvas.diagrams?.find((d) => d.id === diagram)
    const inDiagram = shownDiagram ? new Set(shownDiagram.entities) : null
    const labels = new Map((canvas.labels ?? []).map((l) => [l.name, l.colour]))
    const toggleCollapsed = (entity: Entity) => {
      const collapsed = isCollapsed(entity, canvas)
      run(() =>
        api.saveCanvas({
          collapsed: { ...canvas.collapsed, [entity.id]: !collapsed },
          label: `${collapsed ? 'expand' : 'collapse'} ${entity.name}`,
        }),
      )
    }
    setNodes((previous) => {
      // Keep measured sizes, so React Flow does not have to measure every node again, and which
      // nodes are selected: several can be, to move them together.
      const before = new Map(previous.map((n) => [n.id, n]))
      const notes: NoteNodeType[] = (canvas.notes ?? [])
        .filter((note) => (note.diagram ?? null) === (shownDiagram ? shownDiagram.id : null))
        .map((note) => ({
          id: `note:${note.id}`,
          type: 'note' as const,
          position: { x: note.x, y: note.y },
          measured: before.get(`note:${note.id}`)?.measured,
          selected: before.get(`note:${note.id}`)?.selected ?? false,
          zIndex: -1,
          data: { note, editable: state.editable, onChange: saveNote, onRemove: removeNote },
        }))
      const tables: EntityNodeType[] = entities
        .filter((e) => !hidden.has(e.layer))
        .filter((e) => !inDiagram || inDiagram.has(e.id))
        .filter((e) => !domainFilter || (e.domain ?? '').toLowerCase() === domainFilter.toLowerCase())
        .filter((e) => !labelFilter || canvas.colours?.[e.id] === labelFilter)
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
            view,
            columnIssues: columnIssues.get(entity.id),
            flash: flash?.entity === entity.id ? flash.column : null,
            collapsed: isCollapsed(entity, canvas),
            onToggleCollapsed: state.editable ? toggleCollapsed : undefined,
            labelName: canvas.colours?.[entity.id],
            labelColour: labels.get(canvas.colours?.[entity.id] ?? ''),
            domainColour: showDomains ? domainColour(domainNames, entity.domain) : undefined,
          },
        }))
      return [...notes, ...tables]
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps -- saveNote and removeNote read canvasRef
  }, [state, entities, relationships, diagnostics, hidden, domainFilter, labelFilter, diagram, showDomains, setNodes, view, run, flash])

  // A column pointed out stays marked for a moment.
  useEffect(() => {
    if (!flash) return
    const timer = window.setTimeout(() => setFlash(null), 2600)
    return () => window.clearTimeout(timer)
  }, [flash])

  const edges = useMemo<(RelationshipEdgeType | LineageEdgeType)[]>(() => {
    const shown = new Map(tableNodes.map((n) => [n.id, n]))
    const box = (id: string) => {
      const node = shown.get(id)!
      const width = node.measured?.width ?? NODE_WIDTH
      return { left: node.position.x, right: node.position.x + width }
    }
    // A column that is not shown (collapsed table, keys only) is joined at the table's header.
    const visible = new Map(
      tableNodes.map((n) => [n.id, new Set(visibleColumns(n.data.entity, n.data.view, n.data.collapsed, n.data.foreignKeys, n.data.flash).map((a) => a.id))]),
    )
    const handle = (entityId: string, columnId: string, side: 'in' | 'out') =>
      visible.get(entityId)?.has(columnId) ? handleId(columnId, side) : tableHandle(entityId, side)
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
          sourceHandle: handle(from.id, fromColumn.id, fromSide),
          targetHandle: handle(to.id, toColumn.id, toSide),
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
          sourceHandle: handle(from.entity, from.column, 'out'),
          targetHandle: handle(to.entity, to.column, 'in'),
          type: 'lineage' as const,
          zIndex: 1,
          data: { expression: link.expression, hovered: hovered === id },
        },
      ]
    })
    return [...relationshipEdges, ...lineageEdges]
  }, [tableNodes, entities, relationships, selection, hovered, lineage])

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
    if (shownSchedule && !lineageNodes) {
      const names = new Set(shownSchedule.entities.map((n) => n.toLowerCase()))
      return tableNodes.map((n) => ({
        ...n,
        data: { ...n.data, faded: !names.has(n.data.entity.name.toLowerCase()) },
      }))
    }
    if (lineageNodes) {
      return tableNodes.map((n) => ({
        ...n,
        data: { ...n.data, lineage: lineageNodes.get(n.id), faded: !lineageNodes.has(n.id) },
      }))
    }
    if (!r || !from || !to) return tableNodes
    const ids = (entity: Entity, names: string[]) =>
      new Set(names.map((n) => column(entity, n)?.id).filter((c): c is string => Boolean(c)))
    const linked = new Map<string, Set<string>>([
      [from.id, ids(from, r.keys.map((k) => k.from))],
      [to.id, ids(to, r.keys.map((k) => k.to))],
    ])
    if (from.id === to.id) linked.set(from.id, new Set([...ids(from, r.keys.map((k) => k.from)), ...ids(to, r.keys.map((k) => k.to))]))
    return tableNodes.map((n) => (linked.has(n.id) ? { ...n, data: { ...n.data, linked: linked.get(n.id) } } : n))
  }, [tableNodes, relationships, entities, selection, hovered, lineageNodes, shownSchedule])

  // A shaded area behind each domain's tables, drawn from where the tables are now.
  const domainAreas = useMemo<DomainAreaType[]>(() => {
    if (!showDomains || !state) return []
    const names = state.domains.map((d) => d.name)
    const boxes = new Map<string, { left: number; top: number; right: number; bottom: number }>()
    for (const node of tableNodes) {
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
  }, [showDomains, state, tableNodes])

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

  const savePositions = useCallback(
    (_: unknown, __: unknown, moved: AppNode[]) => {
      const changed: Record<string, Position> = {}
      const notes = new Map<string, Position>()
      for (const node of moved) {
        const at = { x: Math.round(node.position.x), y: Math.round(node.position.y) }
        if (isTable(node)) changed[node.id] = at
        else notes.set(node.data.note.id, at)
      }
      if (Object.keys(changed).length) {
        positions.current = { ...positions.current, ...changed }
        api.saveLayout(changed).catch((e) => setMessage({ text: e.message, tone: 'error' }))
      }
      if (notes.size) {
        const all = (canvasRef.current.notes ?? []).map((n) => (notes.has(n.id) ? { ...n, ...notes.get(n.id)! } : n))
        run(() => api.saveCanvas({ notes: all, label: 'move a note' }))
      }
    },
    [run],
  )

  // Notes: added in the middle of what shows, on the diagram being shown.
  const saveNote = (note: Note) => {
    const all = (canvasRef.current.notes ?? []).map((n) => (n.id === note.id ? note : n))
    run(() => api.saveCanvas({ notes: all, label: 'edit a note' }))
  }
  const removeNote = (id: string) => {
    run(() => api.saveCanvas({ notes: (canvasRef.current.notes ?? []).filter((n) => n.id !== id), label: 'remove a note' }))
  }
  const addNote = () => {
    const stage = document.querySelector('.stage')?.getBoundingClientRect()
    const centre = flow.screenToFlowPosition({
      x: stage ? stage.left + stage.width / 2 - 110 : window.innerWidth / 2,
      y: stage ? stage.top + stage.height / 3 : window.innerHeight / 3,
    })
    const note: Note = {
      id: Math.random().toString(36).slice(2, 10),
      text: '',
      x: Math.round(centre.x),
      y: Math.round(centre.y),
      width: 220,
      diagram,
    }
    run(() => api.saveCanvas({ notes: [...(canvasRef.current.notes ?? []), note], label: 'add a note' }))
  }

  useEffect(() => {
    addNoteRef.current = addNote
  })

  // Diagrams: saved views of some tables, as tabs above the canvas.
  const pickDiagram = (id: string | null) => {
    remember('murakumo.diagram', id ?? '')
    setDiagram(id)
    setSelection(null)
    window.setTimeout(() => flow.fitView({ maxZoom: 1, duration: 300 }), 50)
  }
  const saveDiagrams = (diagrams: NonNullable<State['canvas']['diagrams']>, label: string) =>
    run(() => api.saveCanvas({ diagrams, label }))

  // Arrange: lay tables out again (the selected ones, or all shown), with a way back.
  const arrangeTables = async (direction: Direction, only?: EntityNodeType[]) => {
    const chosen = only ?? tableNodes
    if (chosen.length < 2) return
    const nameOf = (id: string) => entities.find((e) => e.id === id)?.name ?? id
    const placed = await arrange(chosen, relationships, nameOf, direction)
    setLayoutUndo(Object.fromEntries(chosen.map((n) => [n.id, { x: Math.round(n.position.x), y: Math.round(n.position.y) }])))
    positions.current = { ...positions.current, ...placed }
    setNodes((all) => all.map((n) => (placed[n.id] ? { ...n, position: placed[n.id] } : n)))
    await api.saveLayout(placed)
    setMessage({ text: `Arranged ${chosen.length} tables.`, tone: 'info' })
    if (!only) window.setTimeout(() => flow.fitView({ maxZoom: 1, duration: 300 }), 50)
  }
  const undoArrange = async () => {
    if (!layoutUndo) return
    positions.current = { ...positions.current, ...layoutUndo }
    setNodes((all) => all.map((n) => (layoutUndo[n.id] ? { ...n, position: layoutUndo[n.id] } : n)))
    await api.saveLayout(layoutUndo)
    setLayoutUndo(null)
    setMessage(null)
  }

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
    const inDiagram = state?.canvas?.diagrams?.find((d) => d.id === diagram)
    if (inDiagram && !inDiagram.entities.includes(entity.id)) pickDiagram(null)
    const position = positions.current[entity.id]
    if (!position) return
    if (hidden.has(entity.layer)) {
      const next = new Set(hidden)
      next.delete(entity.layer)
      setHidden(next)
    }
    // The panels sit beside the canvas, not over it, so its middle is the middle of what shows.
    flow.setCenter(position.x + 140, position.y + nodeHeight(entity) / 2, { zoom: Math.max(flow.getZoom(), 0.8), duration: 400 })
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

  const panels: { id: Side; label: string; icon: ReactNode; badge?: ReactNode }[] = [
    { id: 'explorer', label: 'Explorer', icon: <ListTree size={18} /> },
    { id: 'sources', label: 'Sources', icon: <Database size={18} />, badge: state.sources.length || null },
    { id: 'schedules', label: 'Schedules', icon: <CalendarClock size={18} /> },
    { id: 'git', label: 'Team', icon: <GitBranch size={18} /> },
    { id: 'ci', label: 'CI', icon: <Workflow size={18} /> },
    {
      id: 'changes',
      label: 'Changes',
      icon: <FileCode2 size={18} />,
      badge: pendingFiles || (migration && !migration.baseline) ? <span className="rail__dot rail__dot--pending" /> : null,
    },
    {
      id: 'issues',
      label: 'Problems',
      icon: <TriangleAlert size={18} />,
      badge: errors.length ? <span className="rail__dot rail__dot--error" /> : warnings.length ? <span className="rail__dot rail__dot--warning" /> : null,
    },
  ]
  const toggleSide = (id: Side) => {
    const next = side === id ? null : id
    setSide(next)
    remember('murakumo.side', next ?? '')
    if (id === 'schedules' && next === null) setScheduleShown(null)
  }
  const drawerTitle = side === 'domains' ? 'Domains' : panels.find((p) => p.id === side)?.label

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar__project">
          <span className="logo" aria-hidden>
            <Cloud size={18} />
          </span>
          <strong>{state.project}</strong>
          <span className="muted small">
            {entities.length} tables · {relationships.length} relationships
          </span>
          {domainFilter && (
            <button className="chip chip--pending" onClick={() => setDomainFilter(null)} title="Show every domain">
              only {domainFilter} ×
            </button>
          )}
          {labelFilter && (
            <button className="chip chip--pending" onClick={() => setLabelFilter(null)} title="Show every colour">
              only {labelFilter} ×
            </button>
          )}
        </div>
        <button className="topbar__search" onClick={() => setSearching(true)} title="Find a table or column (⌘K)">
          <SearchIcon size={14} />
          <span>Find a table or column</span>
          <kbd className="kbd">⌘K</kbd>
        </button>
        <div className="topbar__actions">
          <div className="segmented" role="group" aria-label="Show">
            {VIEW_MODES.map((mode, i) => (
              <button
                key={mode.id}
                className={view === mode.id ? 'segmented__button segmented__button--on' : 'segmented__button'}
                aria-pressed={view === mode.id}
                title={`${mode.label} (⌘${i + 1})`}
                aria-label={mode.label}
                onClick={() => chooseView(mode.id)}
              >
                {VIEW_ICONS[mode.id]}
              </button>
            ))}
            <span className="popover-anchor">
              <button
                className="segmented__button"
                aria-expanded={displayOpen}
                title="Collapsing tables"
                aria-label="Collapsing tables"
                onClick={() => setDisplayOpen(!displayOpen)}
              >
                <SlidersHorizontal size={15} />
              </button>
              {displayOpen && (
                <DisplayOptions state={state} entities={entities} run={run} onClose={() => setDisplayOpen(false)} />
              )}
            </span>
          </div>
          <div className="button-group" role="group" aria-label="History">
            <button
              className="icon-button"
              onClick={() => run(api.undo)}
              disabled={!state.editable || !state.history.undo}
              title={state.history.undo ? `Undo: ${state.history.undo} (⌘Z)` : 'Nothing to undo'}
              aria-label="Undo"
            >
              <Undo2 size={16} />
            </button>
            <button
              className="icon-button"
              onClick={() => run(api.redo)}
              disabled={!state.editable || !state.history.redo}
              title={state.history.redo ? `Redo: ${state.history.redo} (⇧⌘Z)` : 'Nothing to redo'}
              aria-label="Redo"
            >
              <Redo2 size={16} />
            </button>
          </div>
          <span className="status" aria-live="polite">
            {saving ? 'Saving…' : 'Saved'}
          </span>
          <button
            className={pendingFiles || (migration && !migration.baseline) ? 'chip chip--pending' : 'chip'}
            onClick={() => toggleSide('changes')}
            aria-expanded={side === 'changes'}
          >
            {changesLabel}
            {migration && !migration.baseline && <span className="chip__extra"> · migration</span>}
          </button>
          <button
            className={errors.length ? 'chip chip--error' : warnings.length ? 'chip chip--warning' : 'chip chip--ok'}
            onClick={() => toggleSide('issues')}
            aria-expanded={side === 'issues'}
          >
            {problemsLabel}
          </button>
        </div>
      </header>

      {!state.editable && (
        <div className="banner banner--error">
          Some model files did not load, so the canvas is read-only. Fix the errors below, and it updates by itself.
        </div>
      )}
      {message && (
        <div className={`banner banner--${message.tone}`} role="alert">
          <span>
            {message.text}
            {layoutUndo && message.text.startsWith('Arranged') && (
              <button className="button button--small banner__action" onClick={undoArrange}>
                Put them back
              </button>
            )}
          </span>
          <button
            className="icon-button"
            aria-label="Dismiss"
            onClick={() => {
              setMessage(null)
              setLayoutUndo(null)
            }}
          >
            ×
          </button>
        </div>
      )}

      <div className="workspace">
        <nav className="rail" aria-label="Tools and panels">
          <div className="rail__group">
            <RailButton label="Add table (T)" disabled={!state.editable} onClick={() => setAdding({ name: '', layer: 'raw', source: '' })}>
              <SquarePlus size={18} />
            </RailButton>
            <RailButton label="Add relationship (R)" disabled={!state.editable || entities.length < 2} onClick={() => setRelating(true)}>
              <Spline size={18} />
            </RailButton>
            <RailButton label="Add note (N)" disabled={!state.editable} onClick={addNote}>
              <StickyNote size={18} />
            </RailButton>
            <span className="popover-anchor">
              <RailButton label="Arrange tables" active={menu === 'arrange'} disabled={tableNodes.length < 2} onClick={() => setMenu(menu === 'arrange' ? null : 'arrange')}>
                <LayoutGrid size={18} />
              </RailButton>
              {menu === 'arrange' && (
                <div className="popover popover--side" role="menu">
                  <strong className="small">Arrange the {tableNodes.length} tables shown</strong>
                  <button className="menu-item" role="menuitem" onClick={() => { setMenu(null); arrangeTables('RIGHT') }}>
                    Left to right
                  </button>
                  <button className="menu-item" role="menuitem" onClick={() => { setMenu(null); arrangeTables('DOWN') }}>
                    Top to bottom
                  </button>
                  <p className="muted small">To arrange some tables only, select them and use Arrange in the bar above the canvas.</p>
                </div>
              )}
            </span>
            <span className="popover-anchor">
              <RailButton label="Export as an image" active={menu === 'export'} disabled={tableNodes.length === 0} onClick={() => setMenu(menu === 'export' ? null : 'export')}>
                <ImageDown size={18} />
              </RailButton>
              {menu === 'export' && (
                <div className="popover popover--side" role="menu">
                  <strong className="small">Export what shows</strong>
                  {(['png', 'svg'] as const).map((format) => (
                    <button
                      key={format}
                      className="menu-item"
                      role="menuitem"
                      onClick={() => {
                        setMenu(null)
                        const name = state.canvas?.diagrams?.find((d) => d.id === diagram)?.name
                        exportImage(nodes, format, [state.project, name].filter(Boolean).join('-').replace(/\s+/g, '_'))
                      }}
                    >
                      {format.toUpperCase()} image
                    </button>
                  ))}
                </div>
              )}
            </span>
          </div>
          <div className="rail__group">
            {panels.map((p) => (
              <RailButton key={p.id} label={p.label} active={side === p.id} onClick={() => toggleSide(p.id)}>
                {p.icon}
                {p.badge && typeof p.badge === 'number' ? <span className="rail__count">{p.badge}</span> : p.badge}
              </RailButton>
            ))}
          </div>
          <div className="rail__group rail__group--end">
            <a className="rail__button" href="/api/pipeline-docs" target="_blank" rel="noreferrer" title="Pipeline docs" aria-label="Pipeline docs">
              <BookOpen size={18} />
            </a>
            <RailButton label={`Theme: ${theme}`} onClick={cycleTheme}>
              {theme === 'dark' ? <Moon size={18} /> : theme === 'light' ? <Sun size={18} /> : <SunMoon size={18} />}
            </RailButton>
            <span className="popover-anchor">
              <RailButton label="Help" active={menu === 'help' || legend} onClick={() => setMenu(menu === 'help' ? null : 'help')}>
                <CircleHelp size={18} />
              </RailButton>
              {menu === 'help' && (
                <div className="popover popover--side popover--up" role="menu">
                  <button className="menu-item" role="menuitem" onClick={() => { setMenu(null); setTouring(true) }}>
                    Tour of the canvas
                  </button>
                  <button className="menu-item" role="menuitem" onClick={() => { setMenu(null); setShortcuts(true) }}>
                    Keyboard shortcuts <kbd className="kbd">?</kbd>
                  </button>
                  <button className="menu-item" role="menuitem" onClick={() => { setMenu(null); setLegend(!legend) }}>
                    Notation and tips
                  </button>
                </div>
              )}
            </span>
          </div>
        </nav>

        {side && (
          <aside className="drawer" aria-label={drawerTitle}>
            <div className="drawer__head">
              <strong>{drawerTitle}</strong>
              <button
                className="icon-button"
                aria-label="Close"
                onClick={() => (side === 'domains' ? setSide('explorer') : toggleSide(side))}
              >
                ×
              </button>
            </div>
            <div className="drawer__body">
              {side === 'explorer' && (
                <Explorer
                  state={state}
                  diagnostics={diagnostics}
                  hidden={hidden}
                  onToggleLayer={(layer) => {
                    const next = new Set(hidden)
                    if (next.has(layer)) next.delete(layer)
                    else next.add(layer)
                    setHidden(next)
                  }}
                  domainFilter={domainFilter}
                  onDomainFilter={setDomainFilter}
                  showDomains={showDomains}
                  onShowDomains={() => {
                    remember('murakumo.showDomains', showDomains ? '0' : '1')
                    setShowDomains(!showDomains)
                  }}
                  onFocus={focus}
                  run={run}
                  onLabelFilter={(label) => setLabelFilter(labelFilter === label ? null : label)}
                  onManageDomains={() => setSide('domains')}
                  selected={selection?.kind === 'entity' ? selection.id : null}
                />
              )}
              {side === 'changes' && (
                <ChangesPanel
                  generated={generated}
                  entityName={(id) => entities.find((e) => e.id === id)?.name}
                  onGenerate={generateNow}
                  onMigrate={migrateNow}
                  busy={generating}
                />
              )}
              {side === 'sources' && (
                <SourcesPanel state={state} run={run} onMessage={(text) => setMessage({ text, tone: 'info' })} />
              )}
              {side === 'git' && <GitPanel onState={setState} onMessage={showMessage} revision={state.revision} />}
              {side === 'ci' && <CIPanel onMessage={showMessage} />}
              {side === 'schedules' && (
                <SchedulesPanel
                  state={state}
                  run={run}
                  highlighted={scheduleShown?.name ?? null}
                  onHighlight={(s) => setScheduleShown(s ? { name: s.name, entities: s.entities } : null)}
                />
              )}
              {side === 'domains' && <DomainsPanel state={state} run={run} filter={domainFilter} onFilter={setDomainFilter} />}
              {side === 'issues' && (
                <>
                  {diagnostics.length === 0 && <p className="muted">The model is valid and generates cleanly.</p>}
                  <ul className="issue-list">
                    {[...errors, ...warnings].map((d, i) => {
                      const entity = entities.find((e) => e.id === d.entity_id)
                      return (
                        <li key={i}>
                          <button
                            className={`issue issue--${d.level} issue--button`}
                            disabled={!entity}
                            onClick={() => {
                              if (!entity) return
                              focus(entity)
                              if (d.column_id) setFlash({ entity: entity.id, column: d.column_id })
                            }}
                          >
                            <span className="issue__code">{d.code}</span>
                            <span>
                              {entity && (
                                <strong>
                                  {entity.name}
                                  {d.column_id && `.${entity.attributes.find((a) => a.id === d.column_id)?.name ?? ''}`}:{' '}
                                </strong>
                              )}
                              {d.message}
                              {!entity && d.file && <span className="issue__where"> ({d.file})</span>}
                            </span>
                          </button>
                        </li>
                      )
                    })}
                  </ul>
                </>
              )}
            </div>
          </aside>
        )}

        <main className="stage">
          {(state.canvas?.diagrams?.length || state.editable) && (
            <DiagramTabs
              diagrams={state.canvas?.diagrams ?? []}
              active={diagram}
              editable={state.editable}
              selectedCount={tableNodes.filter((n) => n.selected).length}
              onPick={pickDiagram}
              onAdd={async (name) => {
                const id = Math.random().toString(36).slice(2, 10)
                const chosen = tableNodes.filter((n) => n.selected).map((n) => n.id)
                if (await saveDiagrams([...(state.canvas?.diagrams ?? []), { id, name, entities: chosen }], `new diagram ${name}`)) pickDiagram(id)
              }}
              onRename={(id, name) =>
                saveDiagrams((state.canvas?.diagrams ?? []).map((d) => (d.id === id ? { ...d, name } : d)), `rename diagram ${name}`)
              }
              onRemove={async (id) => {
                const removed = state.canvas?.diagrams?.find((d) => d.id === id)
                if (await saveDiagrams((state.canvas?.diagrams ?? []).filter((d) => d.id !== id), `remove diagram ${removed?.name}`))
                  if (diagram === id) pickDiagram(null)
              }}
            />
          )}
          <Markers />
          <ReactFlow
            nodes={[...domainAreas, ...nodes.filter((n) => n.type === 'note'), ...linkedNodes] as AppNode[]}
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
            colorMode={theme}
          >
            <Background variant={BackgroundVariant.Dots} gap={20} size={1.2} />
            <Controls position="bottom-left" showInteractive={false} />
            <MiniMap pannable zoomable position="bottom-right" />
            {legend && (
              <Panel position="top-left" className="legend-panel">
                <Legend onClose={() => setLegend(false)} />
              </Panel>
            )}
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
            {tableNodes.filter((n) => n.selected).length > 1 && (
              <Panel position="top-center">
                <BulkBar
                  state={state}
                  run={run}
                  selected={tableNodes.filter((n) => n.selected).map((n) => n.data.entity)}
                  onArrange={() => arrangeTables('RIGHT', tableNodes.filter((n) => n.selected))}
                  onClear={() => setNodes((all) => all.map((n) => (n.selected ? { ...n, selected: false } : n)))}
                />
              </Panel>
            )}
            {diagram && tableNodes.length === 0 && entities.length > 0 && (
              <Panel position="top-center" className="empty">
                This diagram has no tables yet. Open a table and tick this diagram under <strong>Diagrams</strong>, or
                select tables on <strong>All tables</strong> and start a new diagram with them.
              </Panel>
            )}
            {entities.length === 0 && (
              <Panel position="top-center" className="empty">
                No tables yet. Add one with the <strong>+</strong> on the left (or press T), or run <code>murakumo import</code>.
              </Panel>
            )}
            {state.editable && entities.length > 1 && relationships.length === 0 && (
              <Panel position="bottom-center" className="hint">
                Drag from a column to a column of another table to relate them, or press R.
              </Panel>
            )}
          </ReactFlow>
        </main>

        {(selectedEntity || selectedRelationship) && (
          <aside className="inspector" aria-label="Inspector" style={{ '--panel-width': `${panelWidth}px` } as CSSProperties}>
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
      </div>

      {searching && (
        <Search
          entities={entities}
          onClose={() => setSearching(false)}
          onPick={(hit) => {
            focus(hit.entity)
            const column = hit.column && hit.entity.attributes.find((a) => a.name === hit.column)
            if (column) setFlash({ entity: hit.entity.id, column: column.id })
          }}
        />
      )}
      {shortcuts && <Shortcuts onClose={() => setShortcuts(false)} />}
      {touring && (
        <Tour
          onClose={() => {
            remember('murakumo.toured', '1')
            setTouring(false)
          }}
        />
      )}
      {relating && (
        <NewRelationship state={state} from={selectedEntity} run={run} onClose={() => setRelating(false)} />
      )}
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

/** Collapsing tables: by default over a number of columns, or all of them at once. */
function DisplayOptions(props: {
  state: State
  entities: Entity[]
  run: (action: () => Promise<State>) => Promise<boolean>
  onClose: () => void
}) {
  const { state, entities, run, onClose } = props
  const canvas = state.canvas ?? {}
  const [limit, setLimit] = useState(String(canvas.collapse_over ?? ''))
  const all = (collapsed: boolean) =>
    run(() =>
      api.saveCanvas({
        collapsed: Object.fromEntries(entities.map((e) => [e.id, collapsed])),
        label: collapsed ? 'collapse every table' : 'expand every table',
      }),
    )
  const saveLimit = () => {
    const n = Number(limit)
    if (limit !== '' && (!Number.isInteger(n) || n < 0)) return
    if ((limit === '' ? 0 : n) === (canvas.collapse_over ?? 0)) return
    // A new limit applies to every table again: drop the ones set one by one.
    run(() => api.saveCanvas({ collapse_over: limit === '' ? 0 : n, collapsed: {}, label: 'change when tables collapse' }))
  }
  return (
    <div className="popover" role="dialog" aria-label="Collapsing tables" onKeyDown={(e) => e.key === 'Escape' && onClose()}>
      <label className="popover__row">
        <span>Collapse tables with more than</span>
        <input
          className="input input--narrow"
          inputMode="numeric"
          placeholder="—"
          value={limit}
          disabled={!state.editable}
          onChange={(e) => setLimit(e.target.value.replace(/[^0-9]/g, ''))}
          onBlur={saveLimit}
          onKeyDown={(e) => e.key === 'Enter' && saveLimit()}
        />
        <span>columns</span>
      </label>
      <p className="muted small">Saved with the model, so everyone sees the same. Click a table's ▸ to open it.</p>
      <div className="popover__actions">
        <button className="button button--small" disabled={!state.editable} onClick={() => all(true)}>
          Collapse all
        </button>
        <button className="button button--small" disabled={!state.editable} onClick={() => all(false)}>
          Expand all
        </button>
      </div>
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
