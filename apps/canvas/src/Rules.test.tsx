import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { Entity } from './api'
import { actionOf, buildRule, describe as describeRule, QualitySection } from './Rules'

afterEach(cleanup)

const empty = { values: '', min: '', max: '', pattern: '', sql: '', hours: '', columns: [] as string[], description: '' }

const zones = {
  id: 'e1',
  name: 'taxi_zone_lookup',
  layer: 'raw',
  attributes: [
    { id: 'a1', name: 'LocationID', type: 'integer', nullable: false, key: 'primary', description: null, pii: false, classification: null },
    {
      id: 'a2',
      name: 'Borough',
      type: 'string',
      nullable: true,
      key: null,
      description: null,
      pii: false,
      classification: null,
      tests: [{ type: 'accepted_values', values: ['Queens'], action: 'quarantine' }],
    },
  ],
  tests: [{ type: 'row_count', min: 1 }],
} as unknown as Entity

describe('rules', () => {
  it('builds a rule from the form, or says what is missing', () => {
    expect(buildRule('accepted_values', 'warn', { ...empty, values: 'a, b,' })).toEqual({ type: 'accepted_values', action: 'warn', values: ['a', 'b'] })
    expect(buildRule('range', 'fail', { ...empty, min: '0' })).toEqual({ type: 'range', action: 'fail', min: 0, max: null })
    expect(buildRule('range', 'fail', empty)).toMatch(/minimum, a maximum or both/)
    expect(buildRule('regex', 'fail', { ...empty, pattern: '([' })).toMatch(/not a regular expression/)
    expect(buildRule('null_share', 'warn', { ...empty, max: '2' })).toMatch(/0 to 1/)
    expect(buildRule('row_count', 'quarantine', { ...empty, min: '1' })).toMatch(/judges each row/)
    expect(buildRule('unique_combination', 'fail', { ...empty, columns: ['a'] })).toMatch(/two or more/)
  })

  it('says what a rule checks and what a failure does', () => {
    expect(describeRule({ type: 'length', min: 2, max: 20 })).toBe('2 to 20 characters')
    expect(describeRule({ type: 'null_share', max: 0.05 })).toBe('at most 5% empty')
    expect(actionOf({ type: 'not_null', severity: 'warn' })).toBe('warn')
    expect(actionOf({ type: 'not_null' })).toBe('fail')
  })

  it('lists the rules, changes what a failure does, and adds one', async () => {
    const saveEntity = vi.fn().mockResolvedValue(true)
    const saveColumn = vi.fn().mockResolvedValue(true)
    render(<QualitySection entity={zones} disabled={false} saveEntity={saveEntity} saveColumn={saveColumn} />)
    expect(screen.getByText('one of Queens')).toBeTruthy()
    expect(screen.getByText('at least 1 rows')).toBeTruthy()
    fireEvent.change(screen.getByLabelText('What a failure of one of Queens does'), { target: { value: 'warn' } })
    expect(saveColumn).toHaveBeenCalledWith('a2', { tests: [{ type: 'accepted_values', values: ['Queens'], action: 'warn' }] })
    fireEvent.click(screen.getByText('Add rule'))
    fireEvent.change(screen.getByLabelText('On'), { target: { value: 'a1' } })
    fireEvent.change(screen.getByLabelText('Rule'), { target: { value: 'range' } })
    fireEvent.change(screen.getByLabelText('Minimum'), { target: { value: '1' } })
    fireEvent.change(screen.getByLabelText('When it fails'), { target: { value: 'quarantine' } })
    fireEvent.click(screen.getAllByText('Add rule')[1])
    expect(saveColumn).toHaveBeenLastCalledWith('a1', { tests: [{ type: 'range', action: 'quarantine', min: 1, max: null }] })
  })

  it('offers setting rows aside only on raw tables', () => {
    const mart = { ...zones, layer: 'marts' } as Entity
    render(<QualitySection entity={mart} disabled={false} saveEntity={vi.fn()} saveColumn={vi.fn()} />)
    fireEvent.click(screen.getByText('Add rule'))
    const options = Array.from((screen.getByLabelText('When it fails') as HTMLSelectElement).options).map((o) => o.value)
    expect(options).toEqual(['fail', 'warn'])
  })
})
