import { Handle, Position, type Node, type NodeProps } from '@xyflow/react'

export type Column = {
  name: string
  type: string
  key?: 'pk' | 'fk'
}

export type EntityData = {
  name: string
  source: string
  columns: Column[]
}

export type EntityNodeType = Node<EntityData, 'entity'>

export function EntityNode({ data }: NodeProps<EntityNodeType>) {
  return (
    <div className="entity">
      <Handle type="target" position={Position.Left} />
      <div className="entity__header">
        {data.name}
        <span className="entity__source">{data.source}</span>
      </div>
      <ul className="entity__columns">
        {data.columns.map((column) => (
          <li key={column.name} className={column.key ? 'entity__column entity__column--key' : 'entity__column'}>
            <span>
              {column.key ? `${column.key.toUpperCase()} ` : ''}
              {column.name}
            </span>
            <span className="entity__type">{column.type}</span>
          </li>
        ))}
      </ul>
      <Handle type="source" position={Position.Right} />
    </div>
  )
}
