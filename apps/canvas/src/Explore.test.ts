import { describe, expect, it } from 'vitest'

import type { Entity, State } from './api'
import { rawBehind, rowsText } from './Explore'

const table = (name: string, layer: string, sources: string[] = []) =>
  ({ name, layer, mappings: sources.length ? [{ target: 'id', sources, expression: null }] : [] }) as unknown as Entity

describe('rawBehind', () => {
  it('finds the raw tables a table is built from, all the way down', () => {
    const entities = [
      table('orders', 'raw'),
      table('customer', 'raw'),
      table('stg_orders', 'staging', ['orders.o_orderkey']),
      table('fct_orders', 'marts', ['stg_orders.order_id', 'customer.c_custkey']),
    ]
    const state = { model: { entities, relationships: [] } } as unknown as State
    expect(rawBehind(state, entities[3]).sort()).toEqual(['customer', 'orders'])
    expect(rawBehind(state, entities[0])).toEqual(['orders'])
  })

  it('says how many rows', () => {
    expect(rowsText(2964624)).toBe('2,964,624 rows')
    expect(rowsText(1)).toBe('1 row')
    expect(rowsText(null)).toBe('loaded')
  })
})
