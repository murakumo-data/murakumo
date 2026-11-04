import { ChevronDown, ChevronRight, Eye, EyeOff, Search, Settings2 } from 'lucide-react'
import { useMemo, useState, type ReactNode } from 'react'

import type { Diagnostic, Entity, Layer, State } from './api'
import { domainColour } from './layout'

// The model as a tree, like a database browser: domains, layers and sources, each opening to its
// tables and their columns. Clicking a table shows it on the canvas; clicking a domain shows only
// that domain's tables. The search box filters every section by table or column name.

type Props = {
  state: State
  diagnostics: Diagnostic[]
  hidden: Set<Layer>
  onToggleLayer: (layer: Layer) => void
  domainFilter: string | null
  onDomainFilter: (name: string | null) => void
  onFocus: (entity: Entity) => void
  onManageDomains: () => void
  showDomains: boolean
  onShowDomains: () => void
  selected: string | null
}

const NO_DOMAIN = '(no domain)'

export function Explorer(props: Props) {
  const { state, diagnostics, hidden, onToggleLayer, domainFilter, onDomainFilter, onFocus, onManageDomains, selected } = props
  const { showDomains, onShowDomains } = props
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState<Set<string>>(() => new Set(['section:domains', 'section:layers']))
  const entities = useMemo(() => state.model?.entities ?? [], [state])
  const q = query.trim().toLowerCase()

  // A table matches by its name or any column's; a matching table shows only the matching columns.
  const matches = useMemo(() => {
    if (!q) return null
    const found = new Map<string, Set<string> | null>()
    for (const e of entities) {
      if (e.name.toLowerCase().includes(q)) found.set(e.id, null)
      else {
        const columns = e.attributes.filter((a) => a.name.toLowerCase().includes(q)).map((a) => a.id)
        if (columns.length) found.set(e.id, new Set(columns))
      }
    }
    return found
  }, [entities, q])

  const issues = useMemo(() => {
    const out = new Map<string, 'error' | 'warning'>()
    for (const d of diagnostics) {
      if (!d.entity_id) continue
      if (d.level === 'error' || !out.has(d.entity_id)) out.set(d.entity_id, d.level)
    }
    return out
  }, [diagnostics])

  const isOpen = (key: string) => Boolean(q) || open.has(key)
  const toggle = (key: string) => {
    const next = new Set(open)
    if (next.has(key)) next.delete(key)
    else next.add(key)
    setOpen(next)
  }

  const shown = (list: Entity[]) => (matches ? list.filter((e) => matches.has(e.id)) : list)
  const domainNames = state.domains.map((d) => d.name)
  const byDomain = new Map<string, Entity[]>()
  for (const name of [...domainNames, NO_DOMAIN]) byDomain.set(name, [])
  for (const e of entities) {
    const name = e.domain && byDomain.has(e.domain) ? e.domain : NO_DOMAIN
    byDomain.get(name)!.push(e)
  }
  const sources = [...new Set(entities.filter((e) => e.layer === 'raw').map((e) => e.source ?? '(no source)'))].sort()

  const tables = (list: Entity[], parent: string) =>
    shown(list).map((entity) => (
      <TableRow
        key={`${parent}:${entity.id}`}
        entity={entity}
        open={isOpen(`${parent}:${entity.id}`)}
        onToggle={() => toggle(`${parent}:${entity.id}`)}
        onFocus={() => onFocus(entity)}
        columns={matches?.get(entity.id) ?? null}
        issue={issues.get(entity.id)}
        selected={selected === entity.id}
      />
    ))

  return (
    <div className="explorer">
      <label className="explorer__search">
        <Search size={14} aria-hidden />
        <input
          className="explorer__input"
          placeholder="Find a table or column"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Escape' && setQuery('')}
        />
      </label>
      {matches && matches.size === 0 && <p className="muted small explorer__none">Nothing matches “{query}”.</p>}

      <Section
        title="Domains"
        open={isOpen('section:domains')}
        onToggle={() => toggle('section:domains')}
        action={
          <>
            <button
              className="icon-button icon-button--small"
              aria-pressed={showDomains}
              title={showDomains ? 'Stop shading domains on the canvas' : 'Shade each domain on the canvas'}
              onClick={onShowDomains}
            >
              {showDomains ? <Eye size={14} /> : <EyeOff size={14} />}
            </button>
            <button className="icon-button icon-button--small" title="Add, rename or remove domains" onClick={onManageDomains}>
              <Settings2 size={14} />
            </button>
          </>
        }
      >
        {[...byDomain].map(([name, list]) => {
          if (name === NO_DOMAIN && list.length === 0) return null
          if (matches && shown(list).length === 0) return null
          const key = `domain:${name}`
          const colour = name === NO_DOMAIN ? undefined : domainColour(domainNames, name)
          const only = domainFilter?.toLowerCase() === name.toLowerCase()
          return (
            <div key={key}>
              <div className={only ? 'tree-row tree-row--group tree-row--active' : 'tree-row tree-row--group'}>
                <Twisty open={isOpen(key)} onClick={() => toggle(key)} />
                <span className="tree-row__swatch" style={{ background: colour ?? 'var(--border)' }} />
                <button
                  className="tree-row__label"
                  disabled={name === NO_DOMAIN}
                  title={only ? 'Show every domain' : `Show only ${name}`}
                  onClick={() => onDomainFilter(only ? null : name)}
                >
                  {name}
                </button>
                <span className="tree-row__count">{list.length}</span>
              </div>
              {isOpen(key) && <div className="tree-children">{tables(list, key)}</div>}
            </div>
          )
        })}
      </Section>

      <Section title="Layers" open={isOpen('section:layers')} onToggle={() => toggle('section:layers')}>
        {state.choices.layers.map((layer) => {
          const list = entities.filter((e) => e.layer === layer)
          if (list.length === 0 || (matches && shown(list).length === 0)) return null
          const key = `layer:${layer}`
          const off = hidden.has(layer)
          return (
            <div key={key}>
              <div className={off ? 'tree-row tree-row--group tree-row--off' : 'tree-row tree-row--group'}>
                <Twisty open={isOpen(key)} onClick={() => toggle(key)} />
                <span className={`dot dot--${layer}`} />
                <span className="tree-row__label">{layer}</span>
                <span className="tree-row__count">{list.length}</span>
                <button
                  className="icon-button icon-button--small"
                  aria-pressed={!off}
                  title={off ? `Show ${layer} tables` : `Hide ${layer} tables`}
                  onClick={() => onToggleLayer(layer)}
                >
                  {off ? <EyeOff size={14} /> : <Eye size={14} />}
                </button>
              </div>
              {isOpen(key) && <div className="tree-children">{tables(list, key)}</div>}
            </div>
          )
        })}
      </Section>

      <Section title="Sources" open={isOpen('section:sources')} onToggle={() => toggle('section:sources')}>
        {sources.length === 0 && <p className="muted small tree-empty">No raw tables yet.</p>}
        {sources.map((source) => {
          const list = entities.filter((e) => e.layer === 'raw' && (e.source ?? '(no source)') === source)
          if (matches && shown(list).length === 0) return null
          const key = `source:${source}`
          return (
            <div key={key}>
              <div className="tree-row tree-row--group">
                <Twisty open={isOpen(key)} onClick={() => toggle(key)} />
                <span className="tree-row__label">{source}</span>
                <span className="tree-row__count">{list.length}</span>
              </div>
              {isOpen(key) && <div className="tree-children">{tables(list, key)}</div>}
            </div>
          )
        })}
      </Section>
    </div>
  )
}

function Section(props: { title: string; open: boolean; onToggle: () => void; action?: ReactNode; children: ReactNode }) {
  return (
    <section className="explorer__section">
      <div className="explorer__heading">
        <button className="explorer__heading-button" aria-expanded={props.open} onClick={props.onToggle}>
          {props.open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          {props.title}
        </button>
        {props.action}
      </div>
      {props.open && props.children}
    </section>
  )
}

function Twisty({ open, onClick }: { open: boolean; onClick: () => void }) {
  return (
    <button className="tree-row__twisty" aria-expanded={open} aria-label={open ? 'Collapse' : 'Expand'} onClick={onClick}>
      {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
    </button>
  )
}

function TableRow(props: {
  entity: Entity
  open: boolean
  onToggle: () => void
  onFocus: () => void
  columns: Set<string> | null
  issue?: 'error' | 'warning'
  selected: boolean
}) {
  const { entity, open, onToggle, onFocus, columns, issue, selected } = props
  const list = columns ? entity.attributes.filter((a) => columns.has(a.id)) : entity.attributes
  return (
    <div>
      <div className={selected ? 'tree-row tree-row--active' : 'tree-row'}>
        <Twisty open={open || Boolean(columns)} onClick={onToggle} />
        <span className={`dot dot--${entity.layer}`} />
        <button className="tree-row__label tree-row__label--mono" onClick={onFocus} title={`Show ${entity.name} on the canvas`}>
          {entity.name}
        </button>
        {issue && <span className={`tree-row__issue tree-row__issue--${issue}`} title={`Has ${issue}s`} />}
        <span className="tree-row__count">{entity.attributes.length}</span>
      </div>
      {(open || columns) && (
        <ul className="tree-columns">
          {list.map((a) => (
            <li key={a.id} className="tree-column">
              <span className="tree-column__name">{a.name}</span>
              <span className="tree-column__type">{a.type}</span>
            </li>
          ))}
          {list.length === 0 && <li className="tree-column muted">No columns</li>}
        </ul>
      )}
    </div>
  )
}
