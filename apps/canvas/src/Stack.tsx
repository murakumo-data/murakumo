import { CircleAlert, CircleCheck, TriangleAlert } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import { api, type DeployStep, type StackInfo, type State } from './api'

// The stack: the tools the platform is built from, chosen part by part (what `murakumo stack`
// shows and `murakumo stack set` changes); deploying an environment step by step (`murakumo stack
// deploy`); and the decisions log, which says why it is built the way it is. Every change asks
// why, and the answer goes in the log, model/decisions.yaml.

type Props = {
  revision: string
  onState: (state: State) => void
  onMessage: (text: string, tone: 'info' | 'error') => void
  startJob: (kind: string, options?: Record<string, unknown>) => Promise<unknown>
}

export function StackPanel(props: Props) {
  const [tab, setTab] = useState<'stack' | 'deploy' | 'decisions'>('stack')
  const [info, setInfo] = useState<StackInfo | null>(null)
  const { onMessage } = props
  const load = useCallback(() => {
    api.stack().then(setInfo, (e) => onMessage(e.message, 'error'))
  }, [onMessage])
  useEffect(load, [load, props.revision])

  return (
    <div className="settings stack">
      <div className="tabs tabs--inline" role="tablist">
        {(['stack', 'deploy', 'decisions'] as const).map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? 'tab tab--on' : 'tab'} onClick={() => setTab(t)}>
            {{ stack: 'Stack', deploy: 'Deploy', decisions: 'Decisions' }[t]}
          </button>
        ))}
      </div>
      {!info ? (
        <p className="muted">Loading…</p>
      ) : tab === 'stack' ? (
        <Choices info={info} onInfo={setInfo} {...props} />
      ) : tab === 'deploy' ? (
        <Deploy info={info} {...props} />
      ) : (
        <Decisions info={info} onInfo={setInfo} onMessage={onMessage} />
      )}
    </div>
  )
}

function Choices({ info, onInfo, onState, onMessage, startJob }: Props & { info: StackInfo; onInfo: (i: StackInfo) => void }) {
  const current = Object.fromEntries(info.parts.map((p) => [p.key, p.value]))
  const [draft, setDraft] = useState<Record<string, string>>(current)
  const [why, setWhy] = useState('')
  const [saving, setSaving] = useState(false)
  const changed = Object.fromEntries(Object.entries(draft).filter(([k, v]) => v !== current[k]))
  const dirty = Object.keys(changed).length > 0

  const save = async () => {
    setSaving(true)
    try {
      const next = await api.saveStack(changed, why.trim() || null)
      onInfo(next)
      setDraft(Object.fromEntries(next.parts.map((p) => [p.key, p.value])))
      setWhy('')
      if (next.state) onState(next.state)
      onMessage(`Saved, and recorded in the decisions log: ${next.changed?.join('; ')}. Generate to write the code for it.`, 'info')
    } catch (e) {
      onMessage(e instanceof Error ? e.message : String(e), 'error')
    } finally {
      setSaving(false)
    }
  }

  const problems = (key: string) => info.problems.filter((p) => p.part === key)
  return (
    <div className="settings__form">
      <p className="muted small">
        Pick each part of the platform. Murakumo writes the code for every choice, and the pipeline docs say how to set
        it up by hand.
      </p>
      {info.parts.map((part) => (
        <fieldset key={part.key} className="settings__group stack__part">
          <legend>{part.title}</legend>
          <p className="muted small">{part.question}</p>
          {part.options.length ? (
            <div className="stack__options" role="radiogroup" aria-label={part.title}>
              {part.options.map((o) => (
                <label key={o.value} className={draft[part.key] === o.value ? 'stack__option stack__option--on' : 'stack__option'}>
                  <input
                    type="radio"
                    name={`stack-${part.key}`}
                    value={o.value}
                    checked={draft[part.key] === o.value}
                    onChange={() => setDraft({ ...draft, [part.key]: o.value })}
                  />
                  <span>
                    <strong>{o.value}</strong> <span className="muted small">{o.label}</span>
                  </span>
                </label>
              ))}
            </div>
          ) : (
            <input
              className="input"
              aria-label={part.title}
              value={draft[part.key]}
              onChange={(e) => setDraft({ ...draft, [part.key]: e.target.value })}
            />
          )}
          {problems(part.key).map((p) => (
            <p key={p.message} className={p.level === 'error' ? 'stack__problem stack__problem--error' : 'stack__problem'}>
              {p.level === 'error' ? <CircleAlert size={14} /> : <TriangleAlert size={14} />} {p.message}
            </p>
          ))}
        </fieldset>
      ))}
      {dirty && (
        <div className="stack__save">
          <label className="settings__row settings__row--stacked">
            <span>Why?</span>
            <textarea
              className="input"
              rows={2}
              placeholder="So whoever rebuilds the platform knows: “the team is on Snowflake already”"
              value={why}
              onChange={(e) => setWhy(e.target.value)}
            />
          </label>
          <div className="stack__buttons">
            <button className="button button--primary" disabled={saving} onClick={save}>
              Save {Object.keys(changed).length} change{Object.keys(changed).length === 1 ? '' : 's'}
            </button>
            <button className="button" disabled={saving} onClick={() => setDraft(current)}>
              Cancel
            </button>
          </div>
        </div>
      )}
      {!dirty && info.connected.length > 0 && current.extract !== 'none' && (
        <fieldset className="settings__group">
          <legend>Fetch the data here</legend>
          <p className="muted small">
            Runs the generated extract code for {info.connected.join(', ')} (<code>murakumo ingest</code>), then loads
            what landed (<code>murakumo load</code>).
          </p>
          <div className="stack__buttons">
            <button className="button button--small" onClick={() => startJob('ingest')}>
              Fetch now
            </button>
            <button className="button button--small" onClick={() => startJob('load')}>
              Load what landed
            </button>
          </div>
        </fieldset>
      )}
    </div>
  )
}

function Deploy({ info, onMessage, startJob }: Props & { info: StackInfo }) {
  const [env, setEnv] = useState(info.environments[0] ?? 'dev')
  const [steps, setSteps] = useState<DeployStep[] | null>(null)
  const [open, setOpen] = useState<string | null>(null)
  useEffect(() => {
    setSteps(null)
    api.deployPlan(env).then((r) => setSteps(r.steps), (e) => onMessage(e.message, 'error'))
  }, [env, onMessage])
  const runnable = (steps ?? []).filter((s) => s.kind !== 'commands')
  const errors = info.problems.filter((p) => p.level === 'error')

  return (
    <div className="settings__form">
      <p className="muted small">
        Everything it takes to stand the platform up in an environment, in order. Murakumo runs the warehouse steps and
        writes the CI file; the cloud steps are commands for you to run, with your own credentials. Nothing runs until
        you say so.
      </p>
      <label className="settings__row">
        <span>Environment</span>
        <select className="input" value={env} onChange={(e) => setEnv(e.target.value)}>
          {info.environments.map((e) => (
            <option key={e}>{e}</option>
          ))}
        </select>
      </label>
      {errors.length > 0 && (
        <p className="stack__problem stack__problem--error">
          <CircleAlert size={14} /> Fix the stack first: {errors.map((e) => e.message).join('; ')}
        </p>
      )}
      {!steps ? (
        <p className="muted">Loading…</p>
      ) : (
        <ol className="deploy__steps">
          {steps.map((step) => (
            <li key={step.key} className="deploy__step">
              <button className="deploy__head" aria-expanded={open === step.key} onClick={() => setOpen(open === step.key ? null : step.key)}>
                <strong>{step.title}</strong>
                <span className={step.kind === 'commands' ? 'badge badge--neutral' : 'badge'}>
                  {{ sql: 'Murakumo runs it', file: 'Murakumo writes it', commands: 'you run it' }[step.kind]}
                </span>
              </button>
              <p className="muted small">{step.why}</p>
              {step.kind === 'sql' && step.role && <p className="muted small">Runs as {step.role}.</p>}
              {open === step.key && step.content && <pre className="code-block deploy__content">{step.content}</pre>}
            </li>
          ))}
        </ol>
      )}
      {runnable.length > 0 && (
        <div className="stack__buttons">
          <button
            className="button button--primary"
            disabled={errors.length > 0}
            onClick={() => startJob('deploy', { environment: env })}
            title={`murakumo stack deploy --env ${env} --apply`}
          >
            Run Murakumo's {runnable.length} step{runnable.length === 1 ? '' : 's'} in {env}
          </button>
        </div>
      )}
      {steps && runnable.length === 0 && (
        <p className="muted small">
          <CircleCheck size={14} /> Nothing here for Murakumo to run on this warehouse: the steps above are yours.
        </p>
      )}
    </div>
  )
}

function Decisions({ info, onInfo, onMessage }: { info: StackInfo; onInfo: (i: StackInfo) => void; onMessage: Props['onMessage'] }) {
  const [what, setWhat] = useState('')
  const [why, setWhy] = useState('')
  const add = async () => {
    try {
      const r = await api.addDecision(what.trim(), why.trim() || null)
      onInfo({ ...info, decisions: r.decisions })
      setWhat('')
      setWhy('')
    } catch (e) {
      onMessage(e instanceof Error ? e.message : String(e), 'error')
    }
  }
  return (
    <div className="settings__form">
      <p className="muted small">
        Why the platform is built the way it is. Each stack change is recorded with its reason; add your own decisions
        too. The pipeline docs print them, in <code>model/decisions.yaml</code>.
      </p>
      {info.decisions.length === 0 ? (
        <p className="muted">None recorded yet.</p>
      ) : (
        <ol className="decisions">
          {[...info.decisions].reverse().map((d, i) => (
            <li key={`${d.at}-${i}`} className="decisions__item">
              <div>
                <strong>{d.what}</strong>
                {(d.from !== undefined || d.to !== undefined) && (
                  <span className="muted">
                    {' '}
                    {d.from ?? 'none'} → {d.to ?? 'none'}
                  </span>
                )}
              </div>
              {d.why && <div>{d.why}</div>}
              <div className="muted small">
                {new Date(d.at).toLocaleString()} · {d.by}
              </div>
            </li>
          ))}
        </ol>
      )}
      <fieldset className="settings__group">
        <legend>Add a decision</legend>
        <input className="input" placeholder="Trips are fetched hourly, not daily" value={what} onChange={(e) => setWhat(e.target.value)} />
        <textarea className="input" rows={2} placeholder="Why" value={why} onChange={(e) => setWhy(e.target.value)} />
        <div className="stack__buttons">
          <button className="button button--small" disabled={!what.trim()} onClick={add}>
            Record it
          </button>
        </div>
      </fieldset>
    </div>
  )
}
