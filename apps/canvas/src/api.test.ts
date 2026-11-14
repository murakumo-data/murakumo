import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { api, ApiError } from './api'

type Call = { url: string; init: RequestInit }

function serve(replies: { status?: number; body: unknown }[]) {
  const calls: Call[] = []
  const fetch = vi.fn(async (url: string, init: RequestInit) => {
    calls.push({ url, init })
    const reply = replies[Math.min(calls.length - 1, replies.length - 1)]
    const status = reply.status ?? 200
    return { ok: status < 400, status, statusText: 'x', json: async () => reply.body } as Response
  })
  vi.stubGlobal('fetch', fetch)
  return calls
}

const headers = (call: Call) => call.init.headers as Record<string, string>

describe('the API client', () => {
  beforeEach(() => vi.unstubAllGlobals())
  afterEach(() => vi.unstubAllGlobals())

  it('sends the revision it last saw with a change, and not with a read', async () => {
    const calls = serve([{ body: { revision: 'r1' } }, { body: { revision: 'r2' } }, { body: { revision: 'r2' } }])
    await api.state()
    expect(api.currentRevision()).toBe('r1')
    await api.undo()
    expect(calls[1].url).toBe('/api/undo')
    expect(calls[1].init.method).toBe('POST')
    expect(headers(calls[1])['X-Murakumo-Revision']).toBe('r1')
    expect(api.currentRevision()).toBe('r2')
    await api.state()
    expect(headers(calls[2])['X-Murakumo-Revision']).toBeUndefined()
  })

  it('sends a body as JSON', async () => {
    const calls = serve([{ body: {} }])
    await api.saveSettings({})
    expect(headers(calls[0])['Content-Type']).toBe('application/json')
    expect(calls[0].init.body).toBe('{}')
  })

  it('turns a refusal into an ApiError with the server\'s message, and takes the newer revision', async () => {
    serve([{ status: 409, body: { message: 'someone else saved first', state: { revision: 'r9' } } }])
    const error = await api.undo().catch((e) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect(error.status).toBe(409)
    expect(error.message).toBe('someone else saved first')
    expect(api.currentRevision()).toBe('r9')
  })

  it('says so when the server cannot be reached', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('failed to fetch')
      }),
    )
    const error = await api.state().catch((e) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect(error.status).toBe(0)
    expect(error.message).toMatch(/murakumo canvas/)
  })
})
