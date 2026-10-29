import { Handle, Position, type Node, type NodeProps } from '@xyflow/react'

import type { Entity } from './api'
import { handleId } from './layout'

export type EntityData = {
  entity: Entity
  foreignKeys: Set<string> // lower-case column names that hold a foreign key
  errors: number
  warnings: number
  linked?: Set<string> // column ids joined by the selected or hovered relationship
}

export type EntityNodeType = Node<EntityData, 'entity'>

export function EntityNode({ data, selected }: NodeProps<EntityNodeType>) {
  const { entity, foreignKeys, errors, warnings, linked } = data
  return (
    <div className={`entity entity--${entity.layer}${selected ? ' entity--selected' : ''}`}>
      <div className="entity__header">
        <div className="entity__title">
          <span className="entity__name">{entity.name}</span>
          {errors > 0 && <span className="badge badge--error">{errors}</span>}
          {errors === 0 && warnings > 0 && <span className="badge badge--warning">{warnings}</span>}
        </div>
        <div className="entity__meta">
          {entity.layer}
          {entity.kind !== 'table' && ` · ${entity.kind}`}
          {entity.source && ` · ${entity.source}`}
          {entity.domain && ` · ${entity.domain}`}
        </div>
      </div>
      <ul className="entity__columns">
        {entity.attributes.length === 0 && <li className="entity__empty">No columns yet</li>}
        {entity.attributes.map((column) => {
          const fk = foreignKeys.has(column.name.toLowerCase())
          const pk = column.key === 'primary'
          return (
            <li key={column.id} className={linked?.has(column.id) ? 'entity__column entity__column--linked' : 'entity__column'}>
              <Handle type="target" position={Position.Left} id={handleId(column.id, 'in')} />
              <span className="entity__key">{pk ? 'PK' : fk ? 'FK' : ''}</span>
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
    </div>
  )
}
