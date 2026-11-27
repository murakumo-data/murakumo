import { describe, expect, it } from 'vitest'

import { clean, isDynamics, variableName } from './connection-names'

describe('connection names', () => {
  it('names a secret the way Murakumo does', () => {
    expect(variableName('veripark', 'client_secret')).toBe('VERIPARK_CLIENT_SECRET')
    expect(variableName('veripark-sit', 'client_id')).toBe('VERIPARK_SIT_CLIENT_ID')
    expect(variableName('2024 data', 'token')).toBe('S_2024_DATA_TOKEN')
  })

  it('knows a Dynamics connection', () => {
    const dynamics = { kind: 'rest', base_url: 'https://org.crm11.dynamics.com/api/data/v9.2', paging: { type: 'next_url', next_path: '@odata.nextLink' } }
    expect(isDynamics(dynamics)).toBe(true)
    expect(isDynamics({ kind: 'rest', base_url: 'https://api.example.com/v1' })).toBe(false)
    expect(isDynamics(null)).toBe(false)
  })

  it('keeps only what was set', () => {
    expect(clean({ kind: 'rest', base_url: 'x', headers: {}, auth: { type: 'bearer', env: '' }, endpoints: [{ table: 't', params: {} }] })).toEqual({
      kind: 'rest',
      base_url: 'x',
      auth: { type: 'bearer' },
      endpoints: [{ table: 't', params: {} }],
    })
  })
})
