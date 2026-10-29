import { useState } from 'react'
import type { Node, NodeProps } from '@xyflow/react'

import { api, type Domain, type State } from './api'
import { TextField } from './Field'
import { domainColour } from './layout'

type Run = (action: () => Promise<State>) => Promise<boolean>

export type DomainAreaType = Node<{ name: string; colour: string; owner: string | null }, 'domain'>

/** The shaded area behind a domain's tables. Drawn under them, and not selectable. */
export function DomainArea({ data }: NodeProps<DomainAreaType>) {
  return (
    <div className="domain-area" style={{ borderColor: data.colour, background: data.colour.replace('/ 1)', '/ 0.06)') }}>
      <span className="domain-area__label" style={{ color: data.colour }}>
        {data.name}
        {data.owner && <span className="domain-area__owner"> · {data.owner}</span>}
      </span>
    </div>
  )
}

type PanelProps = {
  state: State
  run: Run
  filter: string | null
  onFilter: (name: string | null) => void
}

/** The domains listed in murakumo.yaml: add, rename (entities follow), set owners, remove. */
export function DomainsPanel({ state, run, filter, onFilter }: PanelProps) {
  const [draft, setDraft] = useState({ name: '', owner: '' })
  const names = state.domains.map((d) => d.name)
  const disabled = !state.editable

  const add = async () => {
    const name = draft.name.trim()
    if (!name) return
    if (await run(() => api.addDomain({ name, owner: draft.owner.trim() || null }))) setDraft({ name: '', owner: '' })
  }

  return (
    <div className="domains">
      <p className="muted small">
        Domains group tables by business area. Each becomes a dbt group, owned by its owner. They are listed under{' '}
        <code>project.domains</code> in murakumo.yaml.
      </p>
      {state.domains.length === 0 && <p className="muted">No domains yet.</p>}
      <ul className="domains__list">
        {state.domains.map((d: Domain) => (
          <li key={d.name} className={filter === d.name ? 'domains__item domains__item--active' : 'domains__item'}>
            <span className="domains__swatch" style={{ background: domainColour(names, d.name) }} />
            <TextField label={`${d.name} name`} value={d.name} disabled={disabled} onSave={(name) => name && run(() => api.updateDomain(d.name, { name }))} />
            <TextField
              label={`${d.name} owner`}
              value={d.owner}
              placeholder="owner"
              disabled={disabled}
              onSave={(owner) => run(() => api.updateDomain(d.name, { owner }))}
            />
            <button
              className="link-button"
              aria-pressed={filter === d.name}
              onClick={() => onFilter(filter === d.name ? null : d.name)}
              title={filter === d.name ? 'Show every domain' : `Show only ${d.name}`}
            >
              {d.entities} {d.entities === 1 ? 'table' : 'tables'}
            </button>
            <button
              className="icon-button"
              aria-label={`Remove ${d.name}`}
              title={d.entities ? 'Move its tables to another domain first' : `Remove ${d.name}`}
              disabled={disabled || d.entities > 0}
              onClick={() => run(() => api.removeDomain(d.name))}
            >
              ×
            </button>
          </li>
        ))}
      </ul>
      {state.editable && (
        <form
          className="domains__new"
          onSubmit={(e) => {
            e.preventDefault()
            add()
          }}
        >
          <input
            className="input"
            placeholder="new_domain"
            aria-label="New domain name"
            value={draft.name}
            onChange={(e) => setDraft({ ...draft, name: e.target.value })}
          />
          <input
            className="input"
            placeholder="owner (optional)"
            aria-label="New domain owner"
            value={draft.owner}
            onChange={(e) => setDraft({ ...draft, owner: e.target.value })}
          />
          <button className="button button--small" type="submit" disabled={!draft.name.trim()}>
            Add
          </button>
        </form>
      )}
    </div>
  )
}
