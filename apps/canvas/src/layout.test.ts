import { describe, expect, it } from 'vitest'

import type { Entity, Relationship } from './api'
import { domainColour, ends, handleId, isCollapsed, nodeHeight, placeEntities, visibleColumns } from './layout'

const column = (name: string, key: string | null = null) => ({ id: `${name}-id`, name, key }) as Entity['attributes'][number]

const entity = (name: string, layer: string, columns: string[] = ['id'], source: string | null = null) =>
  ({ id: name, name, layer, source, attributes: columns.map((c, i) => column(c, i === 0 ? 'primary' : null)) }) as Entity

describe('visibleColumns', () => {
  const orders = entity('orders', 'raw', ['id', 'customer_id', 'total'])
  const foreign = new Set(['customer_id'])

  it('shows every column, or only keys, or none', () => {
    expect(visibleColumns(orders, 'all', false, foreign).map((a) => a.name)).toEqual(['id', 'customer_id', 'total'])
    expect(visibleColumns(orders, 'keys', false, foreign).map((a) => a.name)).toEqual(['id', 'customer_id'])
    expect(visibleColumns(orders, 'names', false, foreign)).toEqual([])
    expect(visibleColumns(orders, 'descriptions', false, foreign)).toEqual([])
  })

  it('keeps a pointed-out column on a collapsed table', () => {
    expect(visibleColumns(orders, 'all', true, foreign)).toEqual([])
    expect(visibleColumns(orders, 'all', true, foreign, 'total-id').map((a) => a.name)).toEqual(['total'])
  })
})

describe('isCollapsed', () => {
  const wide = entity('wide', 'raw', ['a', 'b', 'c'])

  it('follows the limit unless the table says otherwise', () => {
    expect(isCollapsed(wide, {})).toBe(false)
    expect(isCollapsed(wide, { collapse_over: 2 })).toBe(true)
    expect(isCollapsed(wide, { collapse_over: 2, collapsed: { wide: false } })).toBe(false)
    expect(isCollapsed(wide, { collapsed: { wide: true } })).toBe(true)
  })
})

describe('placeEntities', () => {
  const entities = [
    entity('orders', 'raw', ['id'], 'shop'),
    entity('customer', 'raw', ['id'], 'shop'),
    entity('stg_orders', 'staging'),
    entity('fct_orders', 'marts'),
  ]
  const relationships = [{ from: 'orders', to: 'customer' }] as Relationship[]

  it('keeps saved positions and gives every other table one', () => {
    const placed = placeEntities(entities, relationships, { orders: { x: 7, y: 9 } })
    expect(placed.orders).toEqual({ x: 7, y: 9 })
    expect(Object.keys(placed).sort()).toEqual(['customer', 'fct_orders', 'orders', 'stg_orders'])
  })

  it('lays layers out left to right, and a table before the one it points at', () => {
    const placed = placeEntities(entities, relationships, {})
    expect(placed.orders.x).toBeLessThan(placed.customer.x)
    expect(placed.customer.x).toBeLessThan(placed.stg_orders.x)
    expect(placed.stg_orders.x).toBeLessThan(placed.fct_orders.x)
  })

  it('never puts two tables in the same place', () => {
    const placed = Object.values(placeEntities(entities, relationships, {}))
    expect(new Set(placed.map((p) => `${p.x},${p.y}`)).size).toBe(placed.length)
  })
})

describe('relationship ends and colours', () => {
  it('draws cardinality at each end', () => {
    expect(ends('many_to_one', false)).toEqual({ fromEnd: 'many', toEnd: 'one' })
    expect(ends('one_to_many', true)).toEqual({ fromEnd: 'one', toEnd: 'zero-many' })
    expect(ends('unheard_of', false)).toEqual({ fromEnd: 'many', toEnd: 'one' })
  })

  it('gives each domain a colour by its place in the list', () => {
    expect(domainColour(['sales', 'ops'], 'OPS')).toBe(domainColour(['sales', 'ops'], 'ops'))
    expect(domainColour(['sales', 'ops'], 'sales')).not.toBe(domainColour(['sales', 'ops'], 'ops'))
    expect(domainColour(['sales'], 'unknown')).toBeUndefined()
    expect(domainColour(['sales'], null)).toBeUndefined()
  })

  it('names handles and sizes nodes', () => {
    expect(handleId('c1', 'in')).toBe('c1:in')
    expect(nodeHeight(entity('t', 'raw', ['a', 'b']))).toBeGreaterThan(nodeHeight(entity('t', 'raw', ['a'])))
  })
})
