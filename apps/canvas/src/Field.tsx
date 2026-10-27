import { useState, type ReactNode } from 'react'

// Inputs that save when you press Enter or leave the field, and only if the value changed.

type TextProps = {
  value: string | null
  onSave: (value: string | null) => void
  placeholder?: string
  disabled?: boolean
  list?: string
  multiline?: boolean
  className?: string
  label?: string
}

export function TextField({ value, onSave, placeholder, disabled, list, multiline, className, label }: TextProps) {
  const [draft, setDraft] = useState(value ?? '')
  const [shown, setShown] = useState(value)
  if (value !== shown) {
    // The model changed underneath (a save, or an edit on disk): show the new value.
    setShown(value)
    setDraft(value ?? '')
  }
  const commit = () => {
    const next = draft.trim() === '' ? null : draft.trim()
    if (next !== (value ?? null)) onSave(next)
  }
  const common = {
    value: draft,
    placeholder,
    disabled,
    className: className ?? 'input',
    'aria-label': label ?? placeholder,
    onChange: (e: { target: { value: string } }) => setDraft(e.target.value),
    onBlur: commit,
  }
  if (multiline) {
    return (
      <textarea
        {...common}
        rows={2}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) e.currentTarget.blur()
          if (e.key === 'Escape') setDraft(value ?? '')
        }}
      />
    )
  }
  return (
    <input
      {...common}
      list={list}
      onKeyDown={(e) => {
        if (e.key === 'Enter') e.currentTarget.blur()
        if (e.key === 'Escape') setDraft(value ?? '')
      }}
    />
  )
}

type SelectProps = {
  value: string | null
  options: string[]
  onSave: (value: string | null) => void
  disabled?: boolean
  allowEmpty?: boolean
  label: string
}

export function SelectField({ value, options, onSave, disabled, allowEmpty, label }: SelectProps) {
  return (
    <select
      className="input"
      value={value ?? ''}
      disabled={disabled}
      aria-label={label}
      onChange={(e) => onSave(e.target.value === '' ? null : e.target.value)}
    >
      {allowEmpty && <option value="">—</option>}
      {options.map((option) => (
        <option key={option} value={option}>
          {option}
        </option>
      ))}
    </select>
  )
}

export function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="row">
      <span className="row__label">{label}</span>
      {children}
    </label>
  )
}
