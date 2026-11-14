import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { api, type State } from './api'
import { AssistantPanel } from './Assistant'

afterEach(cleanup)

const state = {
  model: {
    entities: [
      { id: '1', name: 'orders', layer: 'raw', attributes: [] },
      { id: '2', name: 'fct_orders', layer: 'marts', attributes: [] },
    ],
  },
} as unknown as State

const info = { enabled: true, provider: 'anthropic', model: 'claude-opus-5-5', key_env: 'ANTHROPIC_API_KEY', key_set: true }

describe('AssistantPanel', () => {
  it('is off until turned on, and says what it sends', async () => {
    vi.spyOn(api, 'assistant').mockResolvedValue({ ...info, enabled: false })
    const save = vi.spyOn(api, 'saveAssistant').mockResolvedValue(info)
    render(<AssistantPanel state={state} selected={null} onState={() => {}} onMessage={() => {}} />)
    expect(await screen.findByText(/Never rows of data/)).toBeTruthy()
    expect(screen.queryByText('Suggest')).toBeNull()
    fireEvent.click(screen.getByText('Turn the assistant on'))
    expect(save).toHaveBeenCalledWith({ enabled: true })
    expect(await screen.findByText('Suggest')).toBeTruthy()
  })

  it('shows suggestions before anything changes, and applies only the chosen ones', async () => {
    vi.spyOn(api, 'assistant').mockResolvedValue(info)
    const suggestions = [
      { kind: 'describe_column', entity: 'orders', column: 'id', value: 'The order.', reason: 'its name', text: 'orders.id: The order.' },
      { kind: 'describe_column', entity: 'orders', column: 'total', value: 'What was paid.', reason: 'money', text: 'orders.total: What was paid.' },
    ] as never[]
    const suggest = vi.spyOn(api, 'suggest').mockResolvedValue({ suggestions })
    const apply = vi.spyOn(api, 'applySuggestions').mockResolvedValue({ applied: ['orders.id'], refused: [], state })
    const onState = vi.fn()
    render(<AssistantPanel state={state} selected="orders" onState={onState} onMessage={() => {}} />)
    fireEvent.click(await screen.findByText('Suggest'))
    expect(await screen.findByText('orders.id: The order.')).toBeTruthy()
    expect(suggest).toHaveBeenCalledWith('describe', { entities: ['orders'] })
    expect(apply).not.toHaveBeenCalled()
    fireEvent.click(screen.getAllByRole('checkbox')[1]) // leave the second one out
    fireEvent.click(screen.getByText('Apply 1 of 2'))
    await waitFor(() => expect(onState).toHaveBeenCalledWith(state))
    expect(apply).toHaveBeenCalledWith([suggestions[0]])
  })

  it('offers only modelled tables to map, and needs one chosen', async () => {
    vi.spyOn(api, 'assistant').mockResolvedValue(info)
    render(<AssistantPanel state={state} selected={null} onState={() => {}} onMessage={() => {}} />)
    fireEvent.click(await screen.findByText('Map columns'))
    const options = screen.getAllByRole('option').map((o) => o.textContent)
    expect(options).toEqual(['Choose a table…', 'fct_orders'])
    expect((screen.getByText('Suggest').closest('button') as HTMLButtonElement).disabled).toBe(true)
  })
})
