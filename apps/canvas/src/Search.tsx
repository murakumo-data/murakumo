import { Columns3, Search as SearchIcon, Table2 } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'

import type { Entity } from './api'

// ⌘K / Ctrl+K: jump to a table or column by typing part of its name.

type Hit = { entity: Entity; column: string | null }

export function Search({ entities, onPick, onClose }: { entities: Entity[]; onPick: (hit: Hit) => void; onClose: () => void }) {
  const [query, setQuery] = useState('')
  const [index, setIndex] = useState(0)

  const hits = useMemo<Hit[]>(() => {
    const q = query.trim().toLowerCase()
    if (!q) return entities.slice(0, 12).map((entity) => ({ entity, column: null }))
    const tables = entities.filter((e) => e.name.toLowerCase().includes(q)).map((entity) => ({ entity, column: null }))
    const columns = entities.flatMap((entity) =>
      entity.attributes.filter((a) => a.name.toLowerCase().includes(q)).map((a) => ({ entity, column: a.name })),
    )
    return [...tables, ...columns].slice(0, 30)
  }, [entities, query])

  useEffect(() => setIndex(0), [query])

  const pick = (hit: Hit | undefined) => {
    if (!hit) return
    onPick(hit)
    onClose()
  }

  return (
    <div className="dialog-backdrop dialog-backdrop--top" onClick={onClose}>
      <div className="search" role="dialog" aria-label="Find a table or column" onClick={(e) => e.stopPropagation()}>
        <label className="search__field">
          <SearchIcon size={16} aria-hidden />
          <input
            autoFocus
            className="search__input"
            placeholder="Find a table or column"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') onClose()
              else if (e.key === 'ArrowDown') {
                e.preventDefault()
                setIndex(Math.min(index + 1, hits.length - 1))
              } else if (e.key === 'ArrowUp') {
                e.preventDefault()
                setIndex(Math.max(index - 1, 0))
              } else if (e.key === 'Enter') pick(hits[index])
            }}
          />
          <kbd className="kbd">esc</kbd>
        </label>
        <ul className="search__results" role="listbox">
          {hits.length === 0 && <li className="muted search__empty">Nothing matches.</li>}
          {hits.map((hit, i) => (
            <li key={`${hit.entity.id}:${hit.column ?? ''}`} role="option" aria-selected={i === index}>
              <button
                className={i === index ? 'search__hit search__hit--active' : 'search__hit'}
                onMouseEnter={() => setIndex(i)}
                onClick={() => pick(hit)}
              >
                {hit.column ? <Columns3 size={14} /> : <Table2 size={14} />}
                <span className="search__name">{hit.column ?? hit.entity.name}</span>
                <span className="muted small">
                  {hit.column ? `${hit.entity.name} · ` : ''}
                  {hit.entity.layer}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}
