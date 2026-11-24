import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { api } from './api'
import { asCsv, QueryPanel } from './Query'

afterEach(cleanup)

const environments = { personal: 'personal', shared: ['prod'] }
const targets = {
  engines: [
    { engine: 'warehouse', label: 'Warehouse (duckdb)' },
    { engine: 'files', label: 'Files and storage (DuckDB here)' },
  ],
  targets: [
    { label: 'orders (raw)', engine: 'warehouse', stage: 'raw', sql: 'select * from raw.orders limit 100', entity: 'orders', note: null },
    { label: 'fct_orders', engine: 'warehouse', stage: 'marts', sql: 'select * from marts.fct_orders limit 100', entity: 'fct_orders', note: null },
  ],
  limit: 1000,
}
const result = {
  engine: 'warehouse',
  columns: ['id', 'name'],
  types: ['INTEGER', 'VARCHAR'],
  rows: [
    [1, 'a, b'],
    [2, null],
  ],
  truncated: false,
  elapsed_ms: 4,
  sql: 'select *',
}

describe('QueryPanel', () => {
  it('lists tables by stage and previews one', async () => {
    vi.spyOn(api, 'queryTargets').mockResolvedValue(targets)
    const query = vi.spyOn(api, 'query').mockResolvedValue(result)
    render(<QueryPanel environments={environments} focus={null} />)
    fireEvent.click(await screen.findByText('orders (raw)'))
    expect(query).toHaveBeenCalledWith('select * from raw.orders limit 100', 'warehouse', { environment: 'personal', limit: 1000 })
    expect(await screen.findByText('2 rows in 4 ms')).toBeTruthy()
    expect(screen.getByText('null')).toBeTruthy()
  })

  it('shows why a query was refused', async () => {
    vi.spyOn(api, 'queryTargets').mockResolvedValue(targets)
    vi.spyOn(api, 'query').mockRejectedValue(new Error('The console only reads: SELECT, DESCRIBE, SHOW.'))
    render(<QueryPanel environments={environments} focus={null} />)
    fireEvent.change(screen.getByLabelText('SQL'), { target: { value: 'drop table x' } })
    fireEvent.click(screen.getByText('Run'))
    expect((await screen.findByRole('alert')).textContent).toMatch(/only reads/)
  })

  it('previews the table the inspector asked for', async () => {
    vi.spyOn(api, 'queryTargets').mockResolvedValue(targets)
    const query = vi.spyOn(api, 'query').mockResolvedValue(result)
    render(<QueryPanel environments={environments} focus={{ entity: 'fct_orders', at: 1 }} />)
    await screen.findByText('2 rows in 4 ms')
    expect(query).toHaveBeenCalledWith('select * from marts.fct_orders limit 100', 'warehouse', expect.anything())
  })

  it('writes results as CSV', () => {
    expect(asCsv(result)).toBe('id,name\n1,"a, b"\n2,\n')
  })
})

describe('QueryPanel and exploring', () => {
  it('runs a query the inspector sent, and lists the questions for its table', async () => {
    vi.spyOn(api, 'queryTargets').mockResolvedValue(targets)
    vi.spyOn(api, 'suggested').mockResolvedValue({
      queries: [{ title: 'Duplicate keys', plain: 'Keys that appear twice.', sql: 'select id from raw.orders group by id having count(*) > 1' }],
    })
    const query = vi.spyOn(api, 'query').mockResolvedValue(result)
    render(<QueryPanel environments={environments} focus={{ entity: 'orders', sql: 'select count(*) from raw.orders', at: 2 }} />)
    await screen.findByText('2 rows in 4 ms')
    expect(query).toHaveBeenCalledWith('select count(*) from raw.orders', 'warehouse', expect.anything())
    fireEvent.click(await screen.findByText('Duplicate keys'))
    expect(query).toHaveBeenLastCalledWith('select id from raw.orders group by id having count(*) > 1', 'warehouse', expect.anything())
  })

  it('offers to load the raw data when a table is missing', async () => {
    vi.spyOn(api, 'queryTargets').mockResolvedValue(targets)
    vi.spyOn(api, 'query').mockRejectedValue(new Error('Catalog Error: Table with name orders does not exist!'))
    const load = vi.fn()
    render(<QueryPanel environments={environments} focus={null} onLoadRaw={load} />)
    fireEvent.click(await screen.findByText('orders (raw)'))
    fireEvent.click(await screen.findByText('Load raw data…'))
    expect(load).toHaveBeenCalled()
  })
})
