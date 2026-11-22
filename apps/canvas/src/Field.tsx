import { useId, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'

// Inputs that save when you press Enter or leave the field, and only if the value changed.

type TextProps = {
  value: string | null
  onSave: (value: string | null) => void
  placeholder?: string
  disabled?: boolean
  list?: string // a <datalist> for boxes that hold several values
  options?: string[] // choices that open in a list on a click, whatever the box holds
  multiline?: boolean
  className?: string
  label?: string
}

export function TextField({ value, onSave, placeholder, disabled, list, options, multiline, className, label }: TextProps) {
  const [draft, setDraft] = useState(value ?? '')
  const [shown, setShown] = useState(value)
  const saved = useRef<string | null | undefined>(undefined) // what a pick saved, so leaving does not save it again
  if (value !== shown) {
    // The model changed underneath (a save, or an edit on disk): show the new value.
    setShown(value)
    setDraft(value ?? '')
  }
  const commit = (text = draft) => {
    const next = text.trim() === '' ? null : text.trim()
    if (next === (value ?? null) || next === saved.current) return
    saved.current = next
    onSave(next)
  }
  const common = {
    value: draft,
    placeholder,
    disabled,
    className: className ?? 'input',
    'aria-label': label ?? placeholder,
    onChange: (e: { target: { value: string } }) => {
      saved.current = undefined
      setDraft(e.target.value)
    },
    onBlur: () => commit(),
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
  const keys = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') e.currentTarget.blur()
    if (e.key === 'Escape') setDraft(value ?? '')
  }
  if (options) {
    return (
      <ComboInput
        {...common}
        value={draft}
        options={options}
        onChange={(text) => {
          saved.current = undefined
          setDraft(text)
        }}
        onPick={(text) => {
          setDraft(text)
          commit(text)
        }}
        onKeyDown={keys}
      />
    )
  }
  return <input {...common} list={list} onKeyDown={keys} />
}

type ComboProps = {
  value: string
  options: string[]
  onChange: (value: string) => void
  onPick?: (value: string) => void // an option was chosen from the list
  onBlur?: () => void
  onKeyDown?: (e: KeyboardEvent<HTMLInputElement>) => void
  placeholder?: string
  disabled?: boolean
  className?: string
  'aria-label'?: string
}

/** A text box with its own list of choices. A click opens the whole list, whatever the box holds;
 * typing narrows it; arrows and Enter pick. Anything else typed is kept as it is. */
export function ComboInput(props: ComboProps) {
  const { value, options, onChange, onPick, onBlur, onKeyDown, placeholder, disabled, className } = props
  const [open, setOpen] = useState(false)
  const [typed, setTyped] = useState(false) // narrow the list only once the user types
  const [active, setActive] = useState(-1)
  const id = useId()
  const words = value.trim().toLowerCase()
  const shown = typed && words ? options.filter((o) => o.toLowerCase().includes(words)) : options
  const show = () => {
    if (disabled) return
    setTyped(false)
    setActive(Math.max(options.indexOf(value), -1))
    setOpen(true)
  }
  const pick = (option: string) => {
    onChange(option)
    onPick?.(option)
    setOpen(false)
    setTyped(false)
  }
  return (
    <span className="combo">
      <input
        className={className ?? 'input'}
        value={value}
        placeholder={placeholder}
        disabled={disabled}
        aria-label={props['aria-label']}
        role="combobox"
        aria-expanded={open}
        aria-controls={id}
        aria-autocomplete="list"
        autoComplete="off"
        onFocus={show}
        onClick={() => !open && show()}
        onChange={(e) => {
          onChange(e.target.value)
          setTyped(true)
          setActive(-1)
          setOpen(true)
        }}
        onBlur={() => {
          setOpen(false)
          onBlur?.()
        }}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault()
            if (!open) return show()
            const step = e.key === 'ArrowDown' ? 1 : -1
            setActive((i) => Math.min(Math.max(i + step, 0), shown.length - 1))
            return
          }
          if (open && e.key === 'Enter' && active >= 0 && shown[active]) {
            e.preventDefault()
            pick(shown[active])
            return
          }
          if (open && e.key === 'Escape') {
            e.stopPropagation()
            setOpen(false)
            return
          }
          onKeyDown?.(e)
        }}
      />
      {open && shown.length > 0 && (
        <ul className="combo__list" role="listbox" id={id}>
          {shown.map((option, i) => (
            <li
              key={option}
              role="option"
              aria-selected={option === value}
              className={i === active ? 'combo__option combo__option--active' : 'combo__option'}
              ref={i === active ? (el) => el?.scrollIntoView({ block: 'nearest' }) : undefined}
              onMouseDown={(e) => {
                e.preventDefault() // keep the focus in the box
                pick(option)
              }}
              onMouseEnter={() => setActive(i)}
            >
              {option}
            </li>
          ))}
        </ul>
      )}
    </span>
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
