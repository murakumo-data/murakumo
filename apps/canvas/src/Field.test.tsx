import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { SelectField, TextField } from './Field'

afterEach(cleanup)

describe('TextField', () => {
  it('saves on leaving the field, trimmed, and only when the value changed', () => {
    const save = vi.fn()
    render(<TextField value="orders" onSave={save} label="Name" />)
    const input = screen.getByLabelText('Name') as HTMLInputElement
    fireEvent.blur(input)
    expect(save).not.toHaveBeenCalled()
    fireEvent.change(input, { target: { value: '  order_lines ' } })
    fireEvent.blur(input)
    expect(save).toHaveBeenCalledExactlyOnceWith('order_lines')
  })

  it('saves an emptied field as null', () => {
    const save = vi.fn()
    render(<TextField value="a note" onSave={save} label="Description" />)
    const input = screen.getByLabelText('Description')
    fireEvent.change(input, { target: { value: '   ' } })
    fireEvent.blur(input)
    expect(save).toHaveBeenCalledExactlyOnceWith(null)
  })

  it('puts the saved value back on Escape', () => {
    const save = vi.fn()
    render(<TextField value="orders" onSave={save} label="Name" />)
    const input = screen.getByLabelText('Name') as HTMLInputElement
    fireEvent.change(input, { target: { value: 'oops' } })
    fireEvent.keyDown(input, { key: 'Escape' })
    expect(input.value).toBe('orders')
    fireEvent.blur(input)
    expect(save).not.toHaveBeenCalled()
  })

  it('shows a value that changed underneath it', () => {
    const { rerender } = render(<TextField value="one" onSave={() => {}} label="Name" />)
    rerender(<TextField value="two" onSave={() => {}} label="Name" />)
    expect((screen.getByLabelText('Name') as HTMLInputElement).value).toBe('two')
  })
})

describe('SelectField', () => {
  it('saves the chosen option, and null for the empty one', () => {
    const save = vi.fn()
    render(<SelectField value="append" options={['append', 'incremental', 'full', 'cdc']} onSave={save} allowEmpty label="Load" />)
    const select = screen.getByLabelText('Load')
    fireEvent.change(select, { target: { value: 'cdc' } })
    fireEvent.change(select, { target: { value: '' } })
    expect(save.mock.calls).toEqual([['cdc'], [null]])
  })

  it('does not offer an empty option unless asked', () => {
    render(<SelectField value="a" options={['a', 'b']} onSave={() => {}} label="Pick" />)
    expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual(['a', 'b'])
  })
})
