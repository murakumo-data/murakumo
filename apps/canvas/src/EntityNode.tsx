import { Handle, Position, type Node, type NodeProps } from '@xyflow/react'
import { ChevronDown, ChevronRight, KeyRound, Link2 } from 'lucide-react'
import type { CSSProperties } from 'react'

import type { Entity } from './api'
import { handleId, visibleColumns, type ViewMode } from './layout'

export type EntityData = {
  entity: Entity
  foreignKeys: Set<string> // lower-case column names that hold a foreign key
  errors: number
  warnings: number
  view: ViewMode
  collapsed: boolean
  onToggleCollapsed?: (entity: Entity) => void // absent when the canvas is read-only
  labelColour?: string // the table's colour label, over its layer's colour
  labelName?: string
  linked?: Set<string> // column ids joined by the selected or hovered relationship
  domainColour?: string // when the canvas shows domains
  lineage?: Map<string, 'focus' | 'up' | 'down'> // column ids in the lineage being shown
  faded?: boolean // not part of the lineage being shown
}

export type EntityNodeType = Node<EntityData, 'entity'>

/** The handles relationships use when the column they join is not shown (collapsed, or keys only). */
export const tableHandle = (entityId: string, side: 'in' | 'out') => `${entityId}:table:${side}`

export function EntityNode({ data, selected }: NodeProps<EntityNodeType>) {
  const { entity, foreignKeys, errors, warnings, linked, domainColour, lineage, faded, view, collapsed } = data
  const columns = visibleColumns(entity, view, collapsed, foreignKeys)
  const hiddenCount = entity.attributes.length - columns.length
  const canToggle = data.onToggleCollapsed && view !== 'names' && view !== 'descriptions'
  const style: CSSProperties = {}
  if (domainColour) style.boxShadow = `0 0 0 3px ${domainColour}`
  if (data.labelColour) (style as Record<string, string>)['--layer'] = data.labelColour
  return (
    <div
      className={`entity entity--${entity.layer}${selected ? ' entity--selected' : ''}${faded ? ' entity--faded' : ''}`}
      style={style}
    >
      <Handle type="target" position={Position.Left} id={tableHandle(entity.id, 'in')} className="entity__table-handle" />
      <Handle type="source" position={Position.Right} id={tableHandle(entity.id, 'out')} className="entity__table-handle" />
      <div className="entity__header">
        <div className="entity__title">
          {canToggle && (
            <button
              className="entity__toggle nodrag"
              aria-label={collapsed ? `Show the columns of ${entity.name}` : `Collapse ${entity.name}`}
              title={collapsed ? 'Show the columns' : 'Collapse'}
              onClick={(e) => {
                e.stopPropagation()
                data.onToggleCollapsed!(entity)
              }}
            >
              {collapsed ? <ChevronRight size={13} /> : <ChevronDown size={13} />}
            </button>
          )}
          <span className="entity__name">{entity.name}</span>
          {errors > 0 && <span className="badge badge--error">{errors}</span>}
          {errors === 0 && warnings > 0 && <span className="badge badge--warning">{warnings}</span>}
        </div>
        <div className="entity__meta">
          {entity.layer}
          {entity.kind !== 'table' && ` · ${entity.kind}`}
          {entity.source && ` · ${entity.source}`}
          {entity.domain && ` · ${entity.domain}`}
          {data.labelName && ` · ${data.labelName}`}
        </div>
      </div>
      {view === 'descriptions' && (
        <p className={entity.description ? 'entity__description' : 'entity__description entity__description--none'}>
          {entity.description || 'No description yet.'}
        </p>
      )}
      {columns.length > 0 && (
        <ul className="entity__columns">
          {columns.map((column) => {
            const fk = foreignKeys.has(column.name.toLowerCase())
            const pk = column.key === 'primary'
            return (
              <li
                key={column.id}
                className={[
                  'entity__column',
                  linked?.has(column.id) ? 'entity__column--linked' : '',
                  lineage?.has(column.id) ? `entity__column--lineage entity__column--${lineage.get(column.id)}` : '',
                  lineage && !lineage.has(column.id) ? 'entity__column--faded' : '',
                ].join(' ')}
              >
                <Handle type="target" position={Position.Left} id={handleId(column.id, 'in')} />
                <span
                  className={pk ? 'entity__key entity__key--primary' : fk ? 'entity__key entity__key--foreign' : 'entity__key'}
                  title={pk ? 'Primary key' : fk ? 'Foreign key' : undefined}
                >
                  {pk ? <KeyRound size={12} aria-label="PK" /> : fk ? <Link2 size={12} aria-label="FK" /> : null}
                </span>
                <span className={pk ? 'entity__column-name entity__column-name--key' : 'entity__column-name'}>
                  {column.name}
                  {!column.nullable && !pk && <span className="entity__required" title="not null">*</span>}
                </span>
                <span className="entity__type">{column.type}</span>
                <Handle type="source" position={Position.Right} id={handleId(column.id, 'out')} />
              </li>
            )
          })}
        </ul>
      )}
      {entity.attributes.length === 0 && view !== 'names' && view !== 'descriptions' && (
        <p className="entity__empty">No columns yet</p>
      )}
      {hiddenCount > 0 && view !== 'names' && view !== 'descriptions' && (
        <button
          className="entity__more nodrag"
          disabled={!canToggle || !collapsed}
          onClick={(e) => {
            e.stopPropagation()
            if (collapsed) data.onToggleCollapsed?.(entity)
          }}
        >
          +{hiddenCount} column{hiddenCount === 1 ? '' : 's'}
        </button>
      )}
    </div>
  )
}
