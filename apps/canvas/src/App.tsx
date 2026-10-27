import { Background, Controls, MiniMap, ReactFlow, type Edge } from '@xyflow/react'
import '@xyflow/react/dist/style.css'

import { EntityNode, type EntityNodeType } from './EntityNode'

const nodeTypes = { entity: EntityNode }

// Static sample until the canvas reads the model through the API (milestone 4).
const initialNodes: EntityNodeType[] = [
  {
    id: 'yellow_tripdata',
    type: 'entity',
    position: { x: 0, y: 0 },
    data: {
      name: 'yellow_tripdata',
      source: 'nyc_tlc',
      columns: [
        { name: 'tpep_pickup_datetime', type: 'timestamp' },
        { name: 'PULocationID', type: 'integer', key: 'fk' },
        { name: 'DOLocationID', type: 'integer', key: 'fk' },
        { name: 'trip_distance', type: 'decimal(10,2)' },
        { name: 'total_amount', type: 'decimal(10,2)' },
      ],
    },
  },
  {
    id: 'taxi_zone_lookup',
    type: 'entity',
    position: { x: 380, y: 40 },
    data: {
      name: 'taxi_zone_lookup',
      source: 'nyc_tlc',
      columns: [
        { name: 'LocationID', type: 'integer', key: 'pk' },
        { name: 'Borough', type: 'string' },
        { name: 'Zone', type: 'string' },
        { name: 'service_zone', type: 'string' },
      ],
    },
  },
]

const initialEdges: Edge[] = [
  { id: 'pickup-zone', source: 'yellow_tripdata', target: 'taxi_zone_lookup', label: 'pickup / dropoff zone' },
]

export default function App() {
  return (
    <ReactFlow
      defaultNodes={initialNodes}
      defaultEdges={initialEdges}
      nodeTypes={nodeTypes}
      fitView
      colorMode="system"
    >
      <Background />
      <Controls />
      <MiniMap />
    </ReactFlow>
  )
}
