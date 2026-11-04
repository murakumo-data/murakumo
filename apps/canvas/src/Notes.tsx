import { NodeResizeControl, type Node, type NodeProps } from '@xyflow/react'
import { X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import type { Note } from './api'

// Sticky notes on the canvas: saved in model/layout.yaml with the diagrams and colour labels, so
// the team sees them. Double-click to write; drag to move; drag the corner to widen.

export const NOTE_COLOURS = ['#fff4b8', '#d9f2e3', '#dce8ff', '#fde2e1', '#ece4ff']

export type NoteData = {
  note: Note
  editable: boolean
  onChange: (note: Note) => void
  onRemove: (id: string) => void
}

export type NoteNodeType = Node<NoteData, 'note'>

export function NoteNode({ data, selected }: NodeProps<NoteNodeType>) {
  const { note, editable, onChange, onRemove } = data
  const [editing, setEditing] = useState(note.text === '')
  const [text, setText] = useState(note.text)
  const input = useRef<HTMLTextAreaElement>(null)
  useEffect(() => setText(note.text), [note.text])
  // A new note is ready to type in: focus once React Flow has placed it.
  useEffect(() => {
    if (!editing) return
    const timer = window.setTimeout(() => input.current?.focus(), 50)
    return () => window.clearTimeout(timer)
  }, [editing])

  const save = () => {
    setEditing(false)
    if (text !== note.text) onChange({ ...note, text })
  }

  return (
    <div
      className={selected ? 'note note--selected' : 'note'}
      style={{ width: note.width, background: note.colour ?? NOTE_COLOURS[0] }}
      onDoubleClick={() => editable && setEditing(true)}
    >
      {editable && selected && (
        <NodeResizeControl
          minWidth={120}
          position="bottom-right"
          className="note__resize"
          onResizeEnd={(_, params) => onChange({ ...note, width: Math.round(params.width) })}
        />
      )}
      {editable && (
        <div className="note__tools nodrag">
          {NOTE_COLOURS.map((colour) => (
            <button
              key={colour}
              className="note__swatch"
              style={{ background: colour }}
              aria-label="Note colour"
              onClick={() => onChange({ ...note, colour })}
            />
          ))}
          <button className="note__remove" aria-label="Remove the note" title="Remove the note" onClick={() => onRemove(note.id)}>
            <X size={12} />
          </button>
        </div>
      )}
      {editing ? (
        <textarea
          ref={input}
          className="note__input nodrag nowheel"
          value={text}
          placeholder="Write a note"
          onChange={(e) => setText(e.target.value)}
          onBlur={save}
          onKeyDown={(e) => {
            if (e.key === 'Escape') {
              setText(note.text)
              setEditing(false)
            } else if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) save()
          }}
        />
      ) : (
        <p className={note.text ? 'note__text' : 'note__text note__text--empty'}>{note.text || 'Double-click to write'}</p>
      )}
    </div>
  )
}
