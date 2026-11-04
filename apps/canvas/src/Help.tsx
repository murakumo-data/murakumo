import { useEffect, useLayoutEffect, useState } from 'react'

// Help for new users: the keyboard shortcuts (press ?), and a short tour of the canvas the first
// time it opens, which the help button can start again.

const SHORTCUTS: [string, string][] = [
  ['⌘K', 'Find a table or column'],
  ['T', 'Add a table'],
  ['R', 'Add a relationship'],
  ['N', 'Add a note'],
  ['⌘Z', 'Undo'],
  ['⇧⌘Z', 'Redo'],
  ['⌘1 – ⌘4', 'All columns, keys only, names only, descriptions'],
  ['⌘ click', 'Add a table to the selection'],
  ['⇧ drag', 'Select the tables in a box'],
  ['?', 'These shortcuts'],
  ['Esc', 'Close a dialog'],
]

export function Shortcuts({ onClose }: { onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <div className="dialog-backdrop" onClick={onClose}>
      <div className="dialog shortcuts" role="dialog" aria-label="Keyboard shortcuts" onClick={(e) => e.stopPropagation()}>
        <h2>Keyboard shortcuts</h2>
        <p className="muted small">On Windows and Linux, Ctrl takes the place of ⌘.</p>
        <dl className="shortcuts__list">
          {SHORTCUTS.map(([keys, what]) => (
            <div key={keys} className="shortcuts__row">
              <dt>
                <kbd className="kbd">{keys}</kbd>
              </dt>
              <dd>{what}</dd>
            </div>
          ))}
        </dl>
        <div className="dialog__actions">
          <button className="button button--primary" onClick={onClose}>
            Done
          </button>
        </div>
      </div>
    </div>
  )
}

type Step = { target: string; title: string; text: string; side: 'right' | 'bottom' | 'left' }

const STEPS: Step[] = [
  {
    target: '.rail',
    title: 'Tools and panels',
    text: 'Add tables, relationships and notes from the top of this rail; open the Explorer, sources, schedules, team, CI, changes and problems below. Hover any icon for its name.',
    side: 'right',
  },
  {
    target: '.drawer',
    title: 'Explorer',
    text: 'Every table by domain, layer, colour label and source. Click a domain to show only its tables; click a table to go to it. The box at the top finds tables and columns.',
    side: 'right',
  },
  {
    target: '.segmented',
    title: 'How tables show',
    text: 'All columns, keys only, names only or descriptions (⌘1 to ⌘4). The last button collapses tables over a number of columns; each table also has its own ▾ to collapse it.',
    side: 'bottom',
  },
  {
    target: '.tabs',
    title: 'Diagrams',
    text: 'Save a view of some tables, like “Finance”, as a tab: select tables, then + Diagram. All tables is always there.',
    side: 'bottom',
  },
  {
    target: '.topbar__actions .button-group',
    title: 'Undo anything',
    text: 'Every change is saved to the model files as you make it, and ⌘Z takes it back: a removed table returns with its columns, mappings and relationships.',
    side: 'bottom',
  },
]

export function Tour({ onClose }: { onClose: () => void }) {
  const steps = STEPS.filter((s) => document.querySelector(s.target))
  const [index, setIndex] = useState(0)
  const [box, setBox] = useState<DOMRect | null>(null)
  const step = steps[index]

  useLayoutEffect(() => {
    const element = step && document.querySelector(step.target)
    setBox(element ? element.getBoundingClientRect() : null)
  }, [step])

  if (!step || !box) return null
  const place =
    step.side === 'right'
      ? { left: box.right + 12, top: Math.max(12, box.top + 24) }
      : step.side === 'left'
        ? { left: Math.max(12, box.left - 332), top: box.top + 24 }
        : { left: Math.min(Math.max(12, box.left), window.innerWidth - 332), top: box.bottom + 12 }
  const last = index === steps.length - 1
  return (
    <div className="tour" role="dialog" aria-label="Tour of the canvas">
      <div
        className="tour__spotlight"
        style={{ left: box.left - 4, top: box.top - 4, width: box.width + 8, height: box.height + 8 }}
      />
      <div className="tour__card" style={place}>
        <p className="muted small">
          {index + 1} of {steps.length}
        </p>
        <h3>{step.title}</h3>
        <p>{step.text}</p>
        <div className="dialog__actions">
          <button className="button button--small" onClick={onClose}>
            {last ? 'Close' : 'Skip'}
          </button>
          {index > 0 && (
            <button className="button button--small" onClick={() => setIndex(index - 1)}>
              Back
            </button>
          )}
          <button className="button button--small button--primary" autoFocus onClick={() => (last ? onClose() : setIndex(index + 1))}>
            {last ? 'Done' : 'Next'}
          </button>
        </div>
      </div>
    </div>
  )
}
