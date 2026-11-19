import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { api } from './api'
import { HealthPanel } from './Health'

afterEach(cleanup)

const environments = { personal: 'dev', shared: ['prod'] }
const contracts = {
  standard: 'odcs',
  enforce_on: ['sources', 'marts'],
  contracts: { customers: 'apiVersion: v3.0.2' },
  check: {
    checked: true,
    tables: ['customers'],
    breaches: [{ table: 'customers', level: 'error' as const, what: 'schema', message: 'status is missing; the contract promises it' }],
  },
}
const cost = {
  warehouse: 'snowflake',
  currency: 'USD',
  unit: 'credit',
  total: 91.25,
  lines: [{ schedule: 'nightly', cron: '0 6 * * *', runs: 30.4, minutes: 5, measured: false, units: 2.5, cost: 7.6 }],
  assumptions: ['One credit costs 3 USD (a list price to start from).'],
}

describe('HealthPanel', () => {
  it('lists what is unusual and opens the table', async () => {
    vi.spyOn(api, 'cost').mockResolvedValue(cost)
    vi.spyOn(api, 'contracts').mockResolvedValue(contracts)
    vi.spyOn(api, 'monitor').mockResolvedValue({
      recorded: true,
      tables: 3,
      loads: 40,
      findings: [{ level: 'error', check: 'volume', subject: 'orders', message: 'the latest load brought no rows' }],
    })
    const onModel = vi.fn()
    render(<HealthPanel refresh={0} environments={environments} onState={() => {}} onMessage={() => {}} onModel={onModel} />)
    expect(await screen.findByText('the latest load brought no rows')).toBeTruthy()
    fireEvent.click(screen.getByText('orders'))
    expect(onModel).toHaveBeenCalledWith('orders')
  })

  it('says so when nothing is recorded, and shows the cost with its assumptions', async () => {
    vi.spyOn(api, 'cost').mockResolvedValue(cost)
    vi.spyOn(api, 'contracts').mockResolvedValue(contracts)
    vi.spyOn(api, 'monitor').mockResolvedValue({ recorded: false, reason: 'No runs recorded yet.', tables: 0, findings: [] })
    render(<HealthPanel refresh={0} environments={environments} onState={() => {}} onMessage={() => {}} onModel={() => {}} />)
    expect(await screen.findByText('No runs recorded yet.')).toBeTruthy()
    expect(await screen.findByText(/About 91.25 USD a month on snowflake/)).toBeTruthy()
    expect(screen.getByText('5 (assumed)')).toBeTruthy()
    expect(screen.getByText(/a list price to start from/)).toBeTruthy()
  })

  it('previews a dbt import before writing it', async () => {
    vi.spyOn(api, 'cost').mockResolvedValue(cost)
    vi.spyOn(api, 'contracts').mockResolvedValue(contracts)
    vi.spyOn(api, 'monitor').mockResolvedValue({ recorded: false, reason: 'x', tables: 0, findings: [] })
    const importDbt = vi.spyOn(api, 'importDbt').mockResolvedValue({
      name: 'shop',
      compiled: true,
      written: false,
      added: ['fct_orders', 'dim_customer'],
      added_sources: ['erp'],
      existing: [],
      skipped: ['stg_erp__orders'],
      mapped: 12,
      unmapped: 3,
      relationships: 1,
      notes: ['fct_orders.margin: computed in the dbt model; write its expression'],
    })
    render(<HealthPanel refresh={0} environments={environments} onState={() => {}} onMessage={() => {}} onModel={() => {}} />)
    const see = screen.getAllByText('See what it would add')[1] as HTMLButtonElement // the dbt import's
    expect(see.disabled).toBe(true)
    fireEvent.change(screen.getByLabelText("The dbt project's folder"), { target: { value: '/work/shop' } })
    fireEvent.click(see)
    expect(await screen.findByText(/Would add 2 tables and 1 source/)).toBeTruthy()
    expect(importDbt).toHaveBeenCalledWith('/work/shop', true)
    expect(screen.getByText(/write its expression/)).toBeTruthy()
  })

  it('shows where a load broke its contract', async () => {
    vi.spyOn(api, 'cost').mockResolvedValue(cost)
    vi.spyOn(api, 'contracts').mockResolvedValue(contracts)
    vi.spyOn(api, 'monitor').mockResolvedValue({ recorded: false, reason: 'No runs recorded yet.', tables: 0, findings: [] })
    render(<HealthPanel refresh={0} environments={environments} onState={() => {}} onMessage={() => {}} onModel={() => {}} />)
    expect(await screen.findByText('status is missing; the contract promises it')).toBeTruthy()
    expect(screen.getByLabelText('Download a contract')).toBeTruthy()
  })
})
