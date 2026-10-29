import { BaseEdge, EdgeLabelRenderer, getSmoothStepPath, type Edge, type EdgeProps } from '@xyflow/react'

// Relationships drawn in crow's-foot notation: each end of the line says how many rows of the
// table at that end take part. The label (role, cardinality) shows on hover and when selected.

export type End = 'one' | 'zero-one' | 'many' | 'zero-many'

export type RelationshipData = {
  fromEnd: End
  toEnd: End
  label: string
  showLabel: boolean
}

export type RelationshipEdgeType = Edge<RelationshipData, 'relationship'>

const marker = (end: End) => `url(#mk-${end})`

export function RelationshipEdge(props: EdgeProps<RelationshipEdgeType>) {
  const { sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, selected } = props
  const [path, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    offset: 28,
  })
  return (
    <>
      <BaseEdge
        id={props.id}
        path={path}
        markerStart={data ? marker(data.fromEnd) : undefined}
        markerEnd={data ? marker(data.toEnd) : undefined}
        interactionWidth={16}
      />
      {data?.showLabel && data.label && (
        <EdgeLabelRenderer>
          <div
            className={selected ? 'edge-label edge-label--selected' : 'edge-label'}
            style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
          >
            {data.label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  )
}

/** One SVG drawing per end, pointing right: the table is at x = 20, the line comes from the left. */
function Symbol({ end }: { end: End }) {
  const zero = end.startsWith('zero')
  const many = end.endsWith('many')
  return (
    <g className="mk">
      {many ? <path d="M20 3 L10 10 L20 17 M10 10 L20 10" /> : <path d="M15 4 L15 16" />}
      {!zero && !many && <path d="M11 4 L11 16" />}
      {!zero && many && <path d="M8 4 L8 16" />}
      {zero && <circle cx={many ? 6 : 9} cy={10} r={3} />}
    </g>
  )
}

const ENDS: End[] = ['one', 'zero-one', 'many', 'zero-many']

/** The marker definitions the edges point at. Rendered once, outside the canvas. */
export function Markers() {
  return (
    <svg className="markers" aria-hidden width="0" height="0">
      <defs>
        {ENDS.map((end) => (
          <marker
            key={end}
            id={`mk-${end}`}
            viewBox="0 0 20 20"
            refX={20}
            refY={10}
            markerWidth={20}
            markerHeight={20}
            markerUnits="userSpaceOnUse"
            orient="auto-start-reverse"
          >
            <Symbol end={end} />
          </marker>
        ))}
      </defs>
    </svg>
  )
}

const MEANING: Record<End, string> = {
  one: 'exactly one',
  'zero-one': 'zero or one',
  many: 'one or many',
  'zero-many': 'zero or many',
}

/** What the symbols on the canvas mean. */
export function Legend({ onClose }: { onClose: () => void }) {
  return (
    <div className="legend" role="dialog" aria-label="Notation">
      <div className="legend__head">
        <strong>Notation</strong>
        <button className="icon-button" aria-label="Close" onClick={onClose}>
          ×
        </button>
      </div>
      <p className="muted small">
        Each end of a line says how many rows of the table at that end match one row at the other end.
      </p>
      <ul className="legend__list">
        {ENDS.map((end) => (
          <li key={end}>
            <svg width="44" height="20" viewBox="0 0 44 20" aria-hidden>
              <path className="legend__line" d="M0 10 L44 10" />
              <g transform="translate(24 0)">
                <Symbol end={end} />
              </g>
            </svg>
            {MEANING[end]}
          </li>
        ))}
        <li>
          <svg width="44" height="20" viewBox="0 0 44 20" aria-hidden>
            <path className="legend__line legend__line--dashed" d="M0 10 L44 10" />
          </svg>
          optional: a row may have no match
        </li>
      </ul>
      <p className="small">
        <strong>Example:</strong> <code>fct_trip</code>{' '}
        <svg className="legend__example" width="64" height="20" viewBox="0 0 64 20" aria-hidden>
          <path className="legend__line" d="M0 10 L64 10" />
          <g transform="translate(20 0) scale(-1 1)">
            <Symbol end="many" />
          </g>
          <g transform="translate(44 0)">
            <Symbol end="one" />
          </g>
        </svg>{' '}
        <code>dim_zone</code> reads “many trips, each with exactly one zone”.
      </p>
      <ul className="legend__keys small">
        <li>
          <strong>PK</strong> primary key · <strong>FK</strong> foreign key · <strong>*</strong> not null
        </li>
        <li>Click a line to see its columns highlighted and edit it; hover to see its role.</li>
        <li>Drag from the dot at the end of a column to a column of another table to relate them.</li>
        <li>
          <strong>Shift</strong>+drag on empty canvas selects several tables; drag one to move them all.
          <strong> ⌘/Ctrl</strong>+click adds a table to the selection.
        </li>
      </ul>
    </div>
  )
}
