import { BaseEdge, EdgeLabelRenderer, getBezierPath, type Edge, type EdgeProps } from '@xyflow/react'

import type { LineageResult } from './api'

// Column lineage on the canvas: links from each column to the columns made from it, drawn over the
// tables while everything that is not part of the chosen column's lineage fades.

export type LineageEdgeType = Edge<{ expression: string | null; hovered: boolean }, 'lineage'>

export function LineageEdge(props: EdgeProps<LineageEdgeType>) {
  const { sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data } = props
  const [path, labelX, labelY] = getBezierPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition })
  return (
    <>
      <BaseEdge id={props.id} path={path} className="lineage-edge" interactionWidth={14} />
      {data?.expression && data.hovered && (
        <EdgeLabelRenderer>
          <code
            className="lineage-label"
            style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
          >
            {data.expression}
          </code>
        </EdgeLabelRenderer>
      )}
    </>
  )
}

type Ref = { entity: string; column: string }

type BarProps = {
  lineage: LineageResult
  onPick: (ref: Ref) => void
  onClose: () => void
}

/** What the canvas is showing the lineage of, and the columns it comes from and feeds. */
export function LineageBar({ lineage, onPick, onClose }: BarProps) {
  const up = lineage.nodes.filter((n) => n.depth < 0)
  const down = lineage.nodes.filter((n) => n.depth > 0)
  const name = lineage.column ? `${lineage.entity}.${lineage.column}` : lineage.entity
  const list = (refs: typeof up) =>
    refs.length === 0 ? (
      <span className="muted">nothing</span>
    ) : (
      refs.map((n) => (
        <button
          key={`${n.entity}.${n.column}`}
          className="lineage-bar__ref"
          title={`${n.layer} · ${Math.abs(n.depth)} step${Math.abs(n.depth) === 1 ? '' : 's'} away`}
          onClick={() => onPick(n)}
        >
          <span className={`dot dot--${n.layer}`} />
          {n.entity}.{n.column}
        </button>
      ))
    )
  return (
    <div className="lineage-bar" role="region" aria-label="Lineage">
      <div className="lineage-bar__head">
        <span>
          Lineage of <code>{name}</code>
        </span>
        <button className="icon-button" aria-label="Stop showing lineage" onClick={onClose}>
          ×
        </button>
      </div>
      <div className="lineage-bar__row">
        <span className="lineage-bar__label">Comes from</span>
        <span className="lineage-bar__refs">{list(up)}</span>
      </div>
      <div className="lineage-bar__row">
        <span className="lineage-bar__label">Used by</span>
        <span className="lineage-bar__refs">{list(down)}</span>
      </div>
      <p className="small muted">Hover a link to see its expression. Click a column above to follow it.</p>
    </div>
  )
}
