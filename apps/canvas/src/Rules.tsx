import { Plus, ShieldCheck, Trash2 } from 'lucide-react'
import { useState } from 'react'

import type { Attribute, Entity, QualityRule, RuleAction, RuleType } from './api'

// Quality rules, in the inspector: every rule on the table and its columns, what each does when it
// fails (stop the run, warn, or set the failing rows aside), and a form to add one. The rules are
// written as dbt tests, checks in the PySpark and Polars jobs, Great Expectations suites, Soda
// checks and the table's data contract (`murakumo generate`).

type Kind = { type: RuleType; label: string; on: 'column' | 'table' | 'both'; rowLevel: boolean; hint: string }

export const RULES: Kind[] = [
  { type: 'not_null', label: 'Not empty', on: 'column', rowLevel: true, hint: 'Every row has a value.' },
  { type: 'unique', label: 'Unique', on: 'column', rowLevel: false, hint: 'No two rows share a value.' },
  { type: 'accepted_values', label: 'One of a list', on: 'column', rowLevel: true, hint: 'Values come from a fixed list.' },
  { type: 'range', label: 'Between', on: 'column', rowLevel: true, hint: 'Numbers or dates between a minimum and a maximum.' },
  { type: 'regex', label: 'Matches a pattern', on: 'column', rowLevel: true, hint: 'Text matches a regular expression.' },
  { type: 'length', label: 'Length', on: 'column', rowLevel: true, hint: 'Text is between a minimum and a maximum length.' },
  { type: 'custom_sql', label: 'Expression', on: 'both', rowLevel: true, hint: 'A SQL expression every row must satisfy.' },
  { type: 'null_share', label: 'Share empty', on: 'column', rowLevel: false, hint: 'At most this share (0 to 1) of values are empty.' },
  { type: 'distinct_count', label: 'Distinct values', on: 'column', rowLevel: false, hint: 'Between a minimum and a maximum of distinct values.' },
  { type: 'recent', label: 'Recent', on: 'column', rowLevel: false, hint: 'The newest date or time is at most this many hours old.' },
  { type: 'row_count', label: 'Row count', on: 'table', rowLevel: false, hint: 'The table has between a minimum and a maximum of rows.' },
  {
    type: 'unique_combination',
    label: 'Unique together',
    on: 'table',
    rowLevel: false,
    hint: 'No two rows share these columns’ values together.',
  },
]

const ACTIONS: { action: RuleAction; label: string }[] = [
  { action: 'fail', label: 'Stop the run' },
  { action: 'warn', label: 'Warn' },
  { action: 'quarantine', label: 'Set rows aside' },
]

export function actionOf(rule: QualityRule): RuleAction {
  return rule.action ?? (rule.severity === 'warn' ? 'warn' : 'fail')
}

export function describe(rule: QualityRule): string {
  const between = (unit = '') =>
    rule.min != null && rule.max != null
      ? `${rule.min} to ${rule.max}${unit}`
      : rule.min != null
        ? `at least ${rule.min}${unit}`
        : `at most ${rule.max}${unit}`
  switch (rule.type) {
    case 'not_null':
      return 'not empty'
    case 'unique':
      return 'unique'
    case 'accepted_values':
      return `one of ${(rule.values ?? []).join(', ')}`
    case 'range':
      return rule.min != null && rule.max != null ? `between ${rule.min} and ${rule.max}` : between()
    case 'regex':
      return `matches ${rule.pattern}`
    case 'length':
      return `${between(' characters')}`
    case 'custom_sql':
      return rule.description || rule.sql || 'expression'
    case 'null_share':
      return `at most ${Math.round((rule.max ?? 0) * 100)}% empty`
    case 'distinct_count':
      return `${between()} distinct values`
    case 'recent':
      return `newest at most ${rule.max_age_hours} hours old`
    case 'row_count':
      return `${between(' rows')}`
    case 'unique_combination':
      return `unique together: ${(rule.columns ?? []).join(', ')}`
    case 'freshness':
      return `loaded every ${rule.max_age_hours} hours`
  }
}

/** The settings a new rule needs, from the form's fields; an error message when one is missing. */
export function buildRule(
  type: RuleType,
  action: RuleAction,
  fields: { values: string; min: string; max: string; pattern: string; sql: string; hours: string; columns: string[]; description: string },
): QualityRule | string {
  const number = (text: string) => (text.trim() === '' ? null : Number(text))
  const rule: QualityRule = { type, action }
  const min = number(fields.min)
  const max = number(fields.max)
  if ((min !== null && Number.isNaN(min)) || (max !== null && Number.isNaN(max))) return 'Minimum and maximum are numbers.'
  switch (type) {
    case 'accepted_values': {
      const values = fields.values.split(',').map((v) => v.trim()).filter(Boolean)
      if (!values.length) return 'List the values, separated by commas.'
      rule.values = values
      break
    }
    case 'range':
    case 'length':
    case 'row_count':
    case 'distinct_count':
      if (min === null && max === null) return 'Give a minimum, a maximum or both.'
      rule.min = min
      rule.max = max
      break
    case 'null_share':
      if (max === null || max < 0 || max > 1) return 'The share is a number from 0 to 1 (0.05 is 5%).'
      rule.max = max
      break
    case 'regex':
      if (!fields.pattern.trim()) return 'Give the pattern.'
      try {
        new RegExp(fields.pattern)
      } catch {
        return 'That is not a regular expression.'
      }
      rule.pattern = fields.pattern.trim()
      break
    case 'custom_sql':
      if (!fields.sql.trim()) return 'Write the expression every row must satisfy, such as amount >= 0.'
      rule.sql = fields.sql.trim()
      break
    case 'recent': {
      const hours = number(fields.hours)
      if (hours === null || Number.isNaN(hours) || hours <= 0) return 'Give the hours.'
      rule.max_age_hours = hours
      break
    }
    case 'unique_combination':
      if (fields.columns.length < 2) return 'Choose two or more columns.'
      rule.columns = fields.columns
      break
  }
  if (action === 'quarantine' && !RULES.find((k) => k.type === type)?.rowLevel)
    return 'Only a rule that judges each row on its own can set rows aside: choose Stop the run or Warn.'
  if (fields.description.trim()) rule.description = fields.description.trim()
  return rule
}

type Props = {
  entity: Entity
  disabled: boolean
  saveEntity: (changes: Record<string, unknown>) => Promise<boolean>
  saveColumn: (columnId: string, changes: Record<string, unknown>) => Promise<boolean>
}

export function QualitySection({ entity, disabled, saveEntity, saveColumn }: Props) {
  const raw = entity.layer === 'raw'
  const placed: { rule: QualityRule; column: Attribute | null; index: number }[] = [
    ...(entity.tests ?? []).map((rule, index) => ({ rule, column: null, index })),
    ...entity.attributes.flatMap((a) => (a.tests ?? []).map((rule, index) => ({ rule, column: a, index }))),
  ]
  const [adding, setAdding] = useState(false)
  const [target, setTarget] = useState<string>(entity.attributes[0]?.id ?? '')
  const [type, setType] = useState<RuleType>('not_null')
  const [action, setAction] = useState<RuleAction>('fail')
  const [fields, setFields] = useState({ values: '', min: '', max: '', pattern: '', sql: '', hours: '', columns: [] as string[], description: '' })
  const [problem, setProblem] = useState<string | null>(null)
  const onTable = target === ''
  const kinds = RULES.filter((k) => (onTable ? k.on !== 'column' : k.on !== 'table'))
  const kind = RULES.find((k) => k.type === type)!
  const set = (key: keyof typeof fields, value: string | string[]) => setFields({ ...fields, [key]: value })

  const replace = (column: Attribute | null, rules: QualityRule[]) =>
    column ? saveColumn(column.id, { tests: rules }) : saveEntity({ tests: rules })
  const rulesOf = (column: Attribute | null) => [...((column ? column.tests : entity.tests) ?? [])]

  const changeAction = (column: Attribute | null, index: number, next: RuleAction) => {
    const rules = rulesOf(column)
    const { severity: _severity, ...rest } = rules[index]
    void _severity
    rules[index] = { ...rest, action: next }
    return replace(column, rules)
  }
  const remove = (column: Attribute | null, index: number) => {
    const rules = rulesOf(column)
    rules.splice(index, 1)
    return replace(column, rules)
  }
  const add = async () => {
    const built = buildRule(type, action, fields)
    if (typeof built === 'string') {
      setProblem(built)
      return
    }
    const column = entity.attributes.find((a) => a.id === target) ?? null
    if (await replace(column, [...rulesOf(column), built])) {
      setProblem(null)
      setFields({ values: '', min: '', max: '', pattern: '', sql: '', hours: '', columns: [], description: '' })
      setAdding(false)
    }
  }

  return (
    <section className="section quality">
      <div className="section__head">
        <h3 className="section__title">
          <ShieldCheck size={14} /> Quality rules <span className="muted">{placed.length}</span>
        </h3>
        {!disabled && (
          <div className="section__actions">
            <button className="button button--small" onClick={() => setAdding(!adding)} aria-expanded={adding}>
              <Plus size={13} /> Add rule
            </button>
          </div>
        )}
      </div>
      {placed.length === 0 ? (
        <p className="muted small">
          No rules yet. Keys and columns that may not be empty are checked anyway; add rules for what else the data must
          be.
        </p>
      ) : (
        <ul className="quality__list">
          {placed.map(({ rule, column, index }) => (
            <li key={`${column?.id ?? 'table'}-${index}`} className="quality__rule">
              <span className="quality__where">{column ? column.name : 'table'}</span>
              <span className="quality__what">{describe(rule)}</span>
              <select
                className="input input--auto"
                value={actionOf(rule)}
                disabled={disabled}
                aria-label={`What a failure of ${describe(rule)} does`}
                onChange={(e) => changeAction(column, index, e.target.value as RuleAction)}
              >
                {ACTIONS.filter((a) => a.action !== 'quarantine' || (raw && RULES.find((k) => k.type === rule.type)?.rowLevel)).map(
                  (a) => (
                    <option key={a.action} value={a.action}>
                      {a.label}
                    </option>
                  ),
                )}
              </select>
              {!disabled && (
                <button className="icon-button" aria-label={`Remove the rule ${describe(rule)}`} onClick={() => remove(column, index)}>
                  <Trash2 size={13} />
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {adding && !disabled && (
        <div className="quality__form">
          <label className="quality__field">
            <span>On</span>
            <select
              className="input"
              value={target}
              onChange={(e) => {
                setTarget(e.target.value)
                const next = RULES.filter((k) => (e.target.value === '' ? k.on !== 'column' : k.on !== 'table'))
                if (!next.some((k) => k.type === type)) setType(next[0].type)
              }}
            >
              <option value="">the whole table</option>
              {entity.attributes.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </select>
          </label>
          <label className="quality__field">
            <span>Rule</span>
            <select className="input" value={type} onChange={(e) => setType(e.target.value as RuleType)}>
              {kinds.map((k) => (
                <option key={k.type} value={k.type}>
                  {k.label}
                </option>
              ))}
            </select>
          </label>
          <p className="muted small">{kind.hint}</p>
          {type === 'accepted_values' && (
            <input className="input" placeholder="Values, separated by commas" aria-label="Accepted values" value={fields.values} onChange={(e) => set('values', e.target.value)} />
          )}
          {(type === 'range' || type === 'length' || type === 'row_count' || type === 'distinct_count') && (
            <div className="quality__pair">
              <input className="input" placeholder="Minimum" aria-label="Minimum" inputMode="decimal" value={fields.min} onChange={(e) => set('min', e.target.value)} />
              <input className="input" placeholder="Maximum" aria-label="Maximum" inputMode="decimal" value={fields.max} onChange={(e) => set('max', e.target.value)} />
            </div>
          )}
          {type === 'null_share' && (
            <input className="input" placeholder="Largest share empty, 0 to 1 (0.05 is 5%)" aria-label="Largest share empty" inputMode="decimal" value={fields.max} onChange={(e) => set('max', e.target.value)} />
          )}
          {type === 'regex' && (
            <input className="input input--code" placeholder="^[A-Z]{2}[0-9]{4}$" aria-label="Pattern" value={fields.pattern} onChange={(e) => set('pattern', e.target.value)} />
          )}
          {type === 'custom_sql' && (
            <input className="input input--code" placeholder="amount >= 0" aria-label="Expression" value={fields.sql} onChange={(e) => set('sql', e.target.value)} />
          )}
          {type === 'recent' && (
            <input className="input" placeholder="Hours" aria-label="Hours" inputMode="decimal" value={fields.hours} onChange={(e) => set('hours', e.target.value)} />
          )}
          {type === 'unique_combination' && (
            <select
              className="input"
              multiple
              aria-label="Columns"
              value={fields.columns}
              onChange={(e) => set('columns', Array.from(e.target.selectedOptions).map((o) => o.value))}
            >
              {entity.attributes.map((a) => (
                <option key={a.id} value={a.name}>
                  {a.name}
                </option>
              ))}
            </select>
          )}
          <label className="quality__field">
            <span>When it fails</span>
            <select className="input" value={action} onChange={(e) => setAction(e.target.value as RuleAction)}>
              {ACTIONS.filter((a) => a.action !== 'quarantine' || (raw && kind.rowLevel)).map((a) => (
                <option key={a.action} value={a.action}>
                  {a.label}
                </option>
              ))}
            </select>
          </label>
          {action === 'quarantine' && (
            <p className="muted small">
              Rows that fail go to {`<staging>__quarantine`} with the rule they failed; the rest carry on. Monitoring warns when a
              load sets many aside.
            </p>
          )}
          {!raw && (
            <p className="muted small">Setting rows aside happens as data is loaded: put such rules on the raw table.</p>
          )}
          <input className="input" placeholder="Description (optional)" aria-label="Description" value={fields.description} onChange={(e) => set('description', e.target.value)} />
          {problem && <p className="form-error small">{problem}</p>}
          <div className="quality__buttons">
            <button className="button button--primary button--small" onClick={add}>
              Add rule
            </button>
            <button className="button button--small" onClick={() => setAdding(false)}>
              Cancel
            </button>
          </div>
        </div>
      )}
      <Contract entity={entity} disabled={disabled} saveEntity={saveEntity} />
    </section>
  )
}

function Contract({ entity, disabled, saveEntity }: { entity: Entity; disabled: boolean; saveEntity: Props['saveEntity'] }) {
  const terms = entity.contract ?? { version: '1.0.0', status: 'active', enforce: 'block' as const }
  const save = (changes: Partial<typeof terms>) => saveEntity({ contract: { ...terms, ...changes } })
  return (
    <div className="quality__contract">
      <h4 className="runs__heading">Data contract (ODCS)</h4>
      <div className="quality__pair">
        <label className="quality__field">
          <span>Version</span>
          <input
            className="input"
            defaultValue={terms.version}
            key={terms.version}
            disabled={disabled}
            aria-label="Contract version"
            onBlur={(e) => e.target.value.trim() && e.target.value.trim() !== terms.version && save({ version: e.target.value.trim() })}
          />
        </label>
        <label className="quality__field">
          <span>Status</span>
          <select className="input" value={terms.status} disabled={disabled} aria-label="Contract status" onChange={(e) => save({ status: e.target.value })}>
            {['proposed', 'draft', 'active', 'deprecated', 'retired'].map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
      </div>
      {entity.layer === 'raw' && (
        <label className="quality__field">
          <span>A load that breaks it</span>
          <select
            className="input"
            value={terms.enforce}
            disabled={disabled}
            aria-label="What a load that breaks the contract does"
            onChange={(e) => save({ enforce: e.target.value as 'block' | 'warn' })}
          >
            <option value="block">stops the build</option>
            <option value="warn">is reported</option>
          </select>
        </label>
      )}
      <p className="muted small">
        Written to generated/contracts/{entity.name}.odcs.yaml.{' '}
        {entity.layer === 'raw'
          ? 'Each load is checked against it (Health, and murakumo build when contracts are enforced on sources).'
          : 'Its columns and types are enforced by dbt when contracts are enforced on marts.'}
      </p>
    </div>
  )
}
