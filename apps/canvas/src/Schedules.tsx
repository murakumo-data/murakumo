import { useState } from 'react'

import { api, type ScheduleInfo, type State } from './api'
import { TextField } from './Field'

type Run = (action: () => Promise<State>) => Promise<boolean>

// Schedules: when each part of the project is refreshed. They live in murakumo.yaml under
// orchestration.schedules, and each becomes a dbt selector and a pipeline for the orchestrator.

const PRESETS: { label: string; cron: string }[] = [
  { label: 'Every 15 minutes', cron: '*/15 * * * *' },
  { label: 'Every hour', cron: '0 * * * *' },
  { label: 'Every day at 06:00', cron: '0 6 * * *' },
  { label: 'Weekdays at 06:00', cron: '0 6 * * 1-5' },
  { label: 'Every Monday at 06:00', cron: '0 6 * * 1' },
  { label: 'First of the month at 06:00', cron: '0 6 1 * *' },
]

function selectionChoices(state: State): string[] {
  const entities = state.model?.entities ?? []
  return [
    ...state.domains.map((d) => `domain:${d.name}`),
    ...state.choices.layers.filter((l) => entities.some((e) => e.layer === l)).map((l) => `layer:${l}`),
    ...state.sources.map((s) => `source:${s.name}`),
    ...entities.map((e) => e.name),
    ...entities.map((e) => `+${e.name}`),
  ]
}

const split = (text: string | null) =>
  (text ?? '')
    .split(',')
    .map((t) => t.trim())
    .filter(Boolean)

const formatRun = (iso: string) =>
  new Date(iso).toLocaleString(undefined, { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })

type Props = { state: State; run: Run; highlighted: string | null; onHighlight: (schedule: ScheduleInfo | null) => void }

export function SchedulesPanel({ state, run, highlighted, onHighlight }: Props) {
  const info = state.orchestration
  const [adding, setAdding] = useState(false)
  if (!info) return null
  const disabled = !state.editable
  const save = (name: string, changes: Record<string, unknown>) => run(() => api.updateSchedule(name, changes))

  return (
    <div className="schedules">
      <p className="muted small">
        When each part of the project is refreshed. Each schedule becomes a dbt selector and a pipeline for{' '}
        your orchestrator in <code>generated/</code>. Select with <code>domain:</code>, <code>layer:</code>,{' '}
        <code>source:</code> or a table name; <code>+name</code> adds what it is built from, <code>name+</code> what is built
        from it. A schedule with nothing selected refreshes the rest.
      </p>
      <label className="row">
        <span className="row__label">Time zone</span>
        <TextField
          label="Time zone"
          value={info.timezone}
          disabled={disabled}
          onSave={(timezone) => timezone && run(() => api.setTimezone(timezone))}
        />
      </label>
      {info.implicit && (
        <p className="issue issue--info">
          No schedules yet, so everything is refreshed once a day at 06:00. Add one to choose.
        </p>
      )}
      <ul className="schedules__list">
        {info.schedules.map((s) => (
          <li key={s.name} className={highlighted === s.name ? 'schedules__item schedules__item--active' : 'schedules__item'}>
            <div className="schedules__head">
              {info.implicit ? (
                <strong>{s.name}</strong>
              ) : (
                <TextField label={`${s.name} name`} value={s.name} disabled={disabled} onSave={(name) => name && save(s.name, { name })} />
              )}
              <button
                className="link-button"
                aria-pressed={highlighted === s.name}
                title="Show these tables on the canvas"
                onClick={() => onHighlight(highlighted === s.name ? null : s)}
              >
                {s.entities.length} {s.entities.length === 1 ? 'table' : 'tables'}
              </button>
              {!info.implicit && (
                <button
                  className="icon-button"
                  aria-label={`Remove ${s.name}`}
                  disabled={disabled}
                  onClick={() => run(() => api.removeSchedule(s.name))}
                >
                  ×
                </button>
              )}
            </div>
            <div className="schedules__when">
              <strong>{s.when}</strong>
              {s.next_runs[0] && <span className="muted small"> · next {formatRun(s.next_runs[0])}</span>}
            </div>
            {!info.implicit && (
              <div className="schedules__fields">
                <label className="row">
                  <span className="row__label">Cron</span>
                  <TextField label={`${s.name} cron`} value={s.cron} disabled={disabled} onSave={(cron) => cron && save(s.name, { cron })} />
                </label>
                <label className="row">
                  <span className="row__label">Refreshes</span>
                  <TextField
                    label={`${s.name} selection`}
                    value={s.select.join(', ') || null}
                    list="schedule-choices"
                    placeholder="everything else"
                    disabled={disabled}
                    onSave={(text) => save(s.name, { select: split(text) })}
                  />
                </label>
                <label className="row">
                  <span className="row__label">Description</span>
                  <TextField
                    label={`${s.name} description`}
                    value={s.description}
                    placeholder="optional"
                    disabled={disabled}
                    onSave={(description) => save(s.name, { description })}
                  />
                </label>
              </div>
            )}
          </li>
        ))}
      </ul>
      {state.editable &&
        (adding ? (
          <NewSchedule run={run} onDone={() => setAdding(false)} />
        ) : (
          <button className="button button--small" onClick={() => setAdding(true)}>
            + Schedule
          </button>
        ))}
      <datalist id="schedule-choices">
        {selectionChoices(state).map((c) => (
          <option key={c} value={c} />
        ))}
      </datalist>
    </div>
  )
}

function NewSchedule({ run, onDone }: { run: Run; onDone: () => void }) {
  const [draft, setDraft] = useState({ name: '', cron: '0 6 * * *', select: '' })
  const preset = PRESETS.find((p) => p.cron === draft.cron)?.cron ?? 'custom'
  return (
    <form
      className="schedules__new"
      onSubmit={async (e) => {
        e.preventDefault()
        const ok = await run(() =>
          api.addSchedule({ name: draft.name.trim(), cron: draft.cron.trim(), select: split(draft.select) }),
        )
        if (ok) onDone()
      }}
    >
      <h3 className="section__title">New schedule</h3>
      <label className="row">
        <span className="row__label">Name</span>
        <input
          className="input"
          autoFocus
          placeholder="hourly_sales"
          value={draft.name}
          onChange={(e) => setDraft({ ...draft, name: e.target.value })}
        />
      </label>
      <label className="row">
        <span className="row__label">When</span>
        <select
          className="input"
          value={preset}
          onChange={(e) => e.target.value !== 'custom' && setDraft({ ...draft, cron: e.target.value })}
        >
          {PRESETS.map((p) => (
            <option key={p.cron} value={p.cron}>
              {p.label}
            </option>
          ))}
          <option value="custom">Custom (cron)</option>
        </select>
      </label>
      <label className="row">
        <span className="row__label">Cron</span>
        <input
          className="input"
          value={draft.cron}
          aria-label="Cron time"
          onChange={(e) => setDraft({ ...draft, cron: e.target.value })}
        />
      </label>
      <label className="row">
        <span className="row__label">Refreshes</span>
        <input
          className="input"
          list="schedule-choices"
          placeholder="domain:sales, +fct_orders (empty: the rest)"
          value={draft.select}
          onChange={(e) => setDraft({ ...draft, select: e.target.value })}
        />
      </label>
      <div className="dialog__actions">
        <button type="button" className="button button--small" onClick={onDone}>
          Cancel
        </button>
        <button type="submit" className="button button--small button--primary" disabled={!draft.name.trim()}>
          Add schedule
        </button>
      </div>
    </form>
  )
}
