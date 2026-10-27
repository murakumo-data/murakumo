import type { Entity, Layer, Position, Relationship } from './api'

const LAYERS: Layer[] = ['raw', 'staging', 'intermediate', 'marts', 'semantic']
const COLUMN_WIDTH = 340
const GAP = 40

/** Each column has a handle on both sides: `in` on the left, `out` on the right. */
export const handleId = (columnId: string, side: 'in' | 'out') => `${columnId}:${side}`

export function nodeHeight(entity: Entity): number {
  return 46 + Math.max(entity.attributes.length, 1) * 22 + 10
}

/**
 * Where to draw entities that have no saved position. Left to right: raw tables (a group per
 * source), then staging, intermediate, marts and semantic. Inside a group, a table sits to the left
 * of the tables it points at, so relationships read left to right: lineitem, orders, customer,
 * nation, region. Saved positions are kept; new entities go below whatever is in their column.
 */
export function placeEntities(
  entities: Entity[],
  relationships: Relationship[],
  saved: Record<string, Position>,
): Record<string, Position> {
  const groupOf = (entity: Entity) => (entity.layer === 'raw' ? `raw:${entity.source ?? ''}` : entity.layer)
  const layerOrder = (group: string) => LAYERS.indexOf(group.startsWith('raw:') ? 'raw' : (group as Layer))
  const byName = new Map(entities.map((e) => [e.name.toLowerCase(), e]))

  // Depth inside the group: 0 for a table nothing in its group points past, +1 per hop.
  const parents = new Map<string, Entity[]>()
  for (const r of relationships) {
    const from = byName.get(r.from.toLowerCase())
    const to = byName.get(r.to.toLowerCase())
    if (from && to && from !== to && groupOf(from) === groupOf(to)) {
      parents.set(from.id, [...(parents.get(from.id) ?? []), to])
    }
  }
  const depth = new Map<string, number>()
  const depthOf = (entity: Entity, seen: Set<string>): number => {
    if (depth.has(entity.id)) return depth.get(entity.id)!
    if (seen.has(entity.id)) return 0 // a cycle: stop
    seen.add(entity.id)
    const value = Math.max(-1, ...(parents.get(entity.id) ?? []).map((p) => depthOf(p, seen))) + 1
    depth.set(entity.id, value)
    return value
  }
  entities.forEach((e) => depthOf(e, new Set()))

  // Columns: each group spans as many columns as its deepest chain.
  const groups = [...new Set(entities.map(groupOf))].sort((a, b) => layerOrder(a) - layerOrder(b))
  const width = new Map(groups.map((g) => [g, 0]))
  for (const e of entities) width.set(groupOf(e), Math.max(width.get(groupOf(e))!, depth.get(e.id)!))
  const start = new Map<string, number>()
  let next = 0
  for (const g of groups) {
    start.set(g, next)
    next += width.get(g)! + 1
  }
  const columnOf = (e: Entity) => start.get(groupOf(e))! + width.get(groupOf(e))! - depth.get(e.id)!

  const positions: Record<string, Position> = {}
  const bottom: Record<number, number> = {}
  for (const entity of entities) {
    const position = saved[entity.id]
    if (!position) continue
    positions[entity.id] = position
    const column = columnOf(entity)
    bottom[column] = Math.max(bottom[column] ?? 0, position.y + nodeHeight(entity) + GAP)
  }
  for (const entity of entities) {
    if (positions[entity.id]) continue
    const column = columnOf(entity)
    const y = bottom[column] ?? 0
    positions[entity.id] = { x: column * COLUMN_WIDTH, y }
    bottom[column] = y + nodeHeight(entity) + GAP
  }
  return positions
}
