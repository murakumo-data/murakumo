import { CircleAlert, Plus, Trash2 } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import { api, type BusMatrix, type Exposure, type State } from './api'

// The marts as the business sees them: the bus matrix (which processes share which dimensions,
// and which dimensions are conformed), and the exposures, the dashboards, notebooks and apps that
// read them (model/exposures.yaml: they appear in dbt, lineage and the docs).

type Props = {
  state: State
  onState: (state: State) => void
  onMessage: (text: string, tone: 'info' | 'error') => void
  onFocus: (name: string) => void
}

export function MartsPanel(props: Props) {
  const [tab, setTab] = useState<'bus' | 'exposures'>('bus')
  return (
    <div className="settings marts">
      <div className="tabs tabs--inline" role="tablist">
        {(['bus', 'exposures'] as const).map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? 'tab tab--on' : 'tab'} onClick={() => setTab(t)}>
            {{ bus: 'Bus matrix', exposures: 'Exposures' }[t]}
          </button>
        ))}
      </div>
      {tab === 'bus' ? <Bus {...props} /> : <Exposures {...props} />}
    </div>
  )
}

function Bus({ state, onMessage, onFocus }: Props) {
  const [matrix, setMatrix] = useState<BusMatrix | null>(null)
  const load = useCallback(() => {
    api.bus().then(setMatrix, (e) => onMessage(e.message, 'error'))
  }, [onMessage])
  useEffect(load, [load, state.revision])
  if (!matrix) return <p className="muted">Loading…</p>
  if (!matrix.facts.length || !matrix.dimensions.length)
    return <p className="muted">No facts and dimensions in the marts yet: the bus matrix shows which facts use which dimensions.</p>
  return (
    <div className="settings__form">
      <p className="muted small">
        A row per business process (a fact), a column per dimension, a mark where the fact uses it. Mark a dimension
        conformed when every domain shares it: one table, so numbers agree across domains.
      </p>
      <div className="guide__table">
        <table className="bus">
          <thead>
            <tr>
              <th>Fact</th>
              {matrix.dimensions.map((d) => (
                <th key={d.name} title={d.description ?? undefined}>
                  <button className="link-button" onClick={() => onFocus(d.name)}>
                    {d.name}
                  </button>
                  {d.conformed && <span className="badge">conformed</span>}
                  {d.domain && <div className="muted small">{d.domain}</div>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {matrix.facts.map((f) => (
              <tr key={f.name}>
                <th>
                  <button className="link-button" onClick={() => onFocus(f.name)}>
                    {f.name}
                  </button>
                  <div className="muted small">
                    {f.domain ?? 'no domain'}
                    {f.kind !== 'fact' ? ` · ${f.kind}` : ''}
                  </div>
                </th>
                {matrix.dimensions.map((d) => (
                  <td key={d.name} className="bus__cell" aria-label={f.dimensions.includes(d.name) ? 'uses' : 'does not use'}>
                    {f.dimensions.includes(d.name) ? '✓' : ''}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {matrix.problems.map((p) => (
        <p key={p.code + p.entity} className="stack__problem">
          <CircleAlert size={14} /> {p.message}
        </p>
      ))}
    </div>
  )
}

const BLANK: Exposure = {
  name: '',
  type: 'dashboard',
  owner: null,
  email: null,
  url: null,
  maturity: null,
  description: null,
  depends_on: [],
}

function Exposures({ state, onState, onMessage }: Props) {
  const saved = state.model?.exposures ?? []
  const [draft, setDraft] = useState<Exposure[]>(saved)
  const [saving, setSaving] = useState(false)
  useEffect(() => setDraft(state.model?.exposures ?? []), [state.model?.exposures])
  const marts = (state.model?.entities ?? []).filter((e) => e.layer === 'marts' || e.layer === 'semantic').map((e) => e.name)
  const dirty = JSON.stringify(draft) !== JSON.stringify(saved)
  const change = (index: number, changes: Partial<Exposure>) =>
    setDraft(draft.map((x, i) => (i === index ? { ...x, ...changes } : x)))
  const save = async () => {
    setSaving(true)
    try {
      onState(await api.saveExposures(draft))
      onMessage('Saved in model/exposures.yaml. Generate to write them into dbt and the docs.', 'info')
    } catch (e) {
      onMessage(e instanceof Error ? e.message : String(e), 'error')
    } finally {
      setSaving(false)
    }
  }
  return (
    <div className="settings__form">
      <p className="muted small">
        The dashboards, notebooks and apps that read the marts. dbt lists them with what they read, so a change that
        would break one shows up in lineage and the docs.
      </p>
      {draft.map((x, index) => (
        <fieldset key={x.id ?? index} className="settings__group">
          <legend>{x.name || 'New exposure'}</legend>
          <label className="settings__row">
            <span>Name</span>
            <input className="input" value={x.name} placeholder="sales_dashboard" onChange={(e) => change(index, { name: e.target.value })} />
          </label>
          <label className="settings__row">
            <span>Type</span>
            <select className="input" value={x.type} onChange={(e) => change(index, { type: e.target.value as Exposure['type'] })}>
              {['dashboard', 'notebook', 'analysis', 'ml', 'application'].map((t) => (
                <option key={t}>{t}</option>
              ))}
            </select>
          </label>
          <label className="settings__row">
            <span>Owner</span>
            <input className="input" value={x.owner ?? ''} onChange={(e) => change(index, { owner: e.target.value || null })} />
          </label>
          <label className="settings__row">
            <span>Email</span>
            <input className="input" value={x.email ?? ''} onChange={(e) => change(index, { email: e.target.value || null })} />
          </label>
          <label className="settings__row">
            <span>Link</span>
            <input className="input" value={x.url ?? ''} placeholder="https://" onChange={(e) => change(index, { url: e.target.value || null })} />
          </label>
          <label className="settings__row">
            <span>Maturity</span>
            <select
              className="input"
              value={x.maturity ?? ''}
              onChange={(e) => change(index, { maturity: (e.target.value || null) as Exposure['maturity'] })}
            >
              <option value="">(not set)</option>
              {['high', 'medium', 'low'].map((m) => (
                <option key={m}>{m}</option>
              ))}
            </select>
          </label>
          <label className="settings__row settings__row--stacked">
            <span>What it is</span>
            <textarea
              className="input"
              rows={2}
              value={x.description ?? ''}
              onChange={(e) => change(index, { description: e.target.value || null })}
            />
          </label>
          <div className="settings__row settings__row--stacked">
            <span>Reads</span>
            <div className="marts__reads">
              {marts.map((name) => (
                <label key={name} className="small">
                  <input
                    type="checkbox"
                    checked={x.depends_on.includes(name)}
                    onChange={(e) =>
                      change(index, {
                        depends_on: e.target.checked ? [...x.depends_on, name] : x.depends_on.filter((n) => n !== name),
                      })
                    }
                  />{' '}
                  {name}
                </label>
              ))}
            </div>
          </div>
          <div className="stack__buttons">
            <button className="button button--small" onClick={() => setDraft(draft.filter((_, i) => i !== index))}>
              <Trash2 size={13} /> Remove
            </button>
          </div>
        </fieldset>
      ))}
      <div className="stack__buttons">
        <button className="button button--small" onClick={() => setDraft([...draft, { ...BLANK }])}>
          <Plus size={13} /> Add an exposure
        </button>
        {dirty && (
          <>
            <button className="button button--primary" disabled={saving || draft.some((x) => !x.name.trim())} onClick={save}>
              Save exposures
            </button>
            <button className="button" disabled={saving} onClick={() => setDraft(saved)}>
              Cancel
            </button>
          </>
        )}
      </div>
    </div>
  )
}
