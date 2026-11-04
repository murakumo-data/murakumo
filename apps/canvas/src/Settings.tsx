import { CircleAlert, CircleCheck, CircleDot, Info } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import { api, type SetupCheck, type Settings, type State } from './api'

// Settings: the project's choices (what `murakumo config` and murakumo.yaml's switches set), and
// what this machine needs to build it (what `murakumo setup` checks). Passwords and keys are never
// typed here: they stay in environment variables and key files, and the checklist says which.

const WORDS: Record<string, string> = {
  duckdb: 'DuckDB (local file)',
  snowflake: 'Snowflake',
  databricks: 'Databricks',
  bigquery: 'BigQuery',
  redshift: 'Redshift',
  fabric: 'Microsoft Fabric',
  synapse: 'Azure Synapse',
  postgres: 'Postgres',
  airflow: 'Airflow',
  dagster: 'Dagster',
  prefect: 'Prefect',
  databricks_workflows: 'Databricks Workflows',
  snowflake_tasks: 'Snowflake Tasks',
  aws_glue: 'AWS Glue',
  aws_step_functions: 'AWS Step Functions',
  gcp_composer: 'Cloud Composer',
  gcp_workflows: 'Google Workflows',
  azure_data_factory: 'Azure Data Factory',
  kimball: 'Kimball',
  datavault: 'Data Vault',
  obt: 'One big table',
  '3nf': 'Third normal form',
  mixed: 'Mixed',
  none: 'None',
  polars: 'Polars',
  pyspark: 'PySpark',
}
const say = (value: string) => WORDS[value] ?? value

export function SettingsPanel({
  revision,
  onState,
  onMessage,
}: {
  revision: string // reload when murakumo.yaml changes: an undo, an editor, `murakumo config`
  onState: (state: State) => void
  onMessage: (text: string, tone: 'info' | 'error') => void
}) {
  const [tab, setTab] = useState<'project' | 'machine'>('project')
  return (
    <div className="settings">
      <div className="tabs tabs--inline" role="tablist">
        <button role="tab" aria-selected={tab === 'project'} className={tab === 'project' ? 'tab tab--on' : 'tab'} onClick={() => setTab('project')}>
          Project
        </button>
        <button role="tab" aria-selected={tab === 'machine'} className={tab === 'machine' ? 'tab tab--on' : 'tab'} onClick={() => setTab('machine')}>
          This machine
        </button>
      </div>
      {tab === 'project' ? <ProjectSettings key={revision} onState={onState} onMessage={onMessage} /> : <Setup />}
    </div>
  )
}

function ProjectSettings({ onState, onMessage }: { onState: (state: State) => void; onMessage: (text: string, tone: 'info' | 'error') => void }) {
  const [settings, setSettings] = useState<Settings | null>(null)
  const [saving, setSaving] = useState(false)
  useEffect(() => {
    api.settings().then(setSettings, (e) => onMessage(e.message, 'error'))
  }, [onMessage])

  const save = async (changes: Partial<Settings>) => {
    setSaving(true)
    try {
      const next = await api.saveSettings(changes)
      setSettings(next)
      if (next.state) onState(next.state)
      if (next.changed?.length) onMessage(`Saved in murakumo.yaml: ${next.changed.join('; ')}. Generate to update the code.`, 'info')
    } catch (e) {
      onMessage(e instanceof Error ? e.message : String(e), 'error')
    } finally {
      setSaving(false)
    }
  }

  if (!settings) return <p className="muted">Loading…</p>
  const select = (key: 'warehouse' | 'orchestrator' | 'pattern' | 'python_engine', options: string[]) => (
    <select className="input" value={settings[key]} disabled={saving} onChange={(e) => save({ [key]: e.target.value })}>
      {options.map((o) => (
        <option key={o} value={o}>
          {say(o)}
        </option>
      ))}
    </select>
  )
  const toggle = (key: 'plain_sql' | 'run_log' | 'personal_schemas') => (
    <input type="checkbox" checked={settings[key]} disabled={saving} onChange={(e) => save({ [key]: e.target.checked })} />
  )
  return (
    <div className="settings__form">
      <fieldset className="settings__group">
        <legend>Where it runs</legend>
        <label className="settings__row">
          <span>Warehouse</span>
          {select('warehouse', settings.choices.warehouses)}
        </label>
        <p className="muted small">Where the tables are built. Change it, then look at <strong>This machine</strong> for what the new one needs.</p>
        <label className="settings__row">
          <span>Orchestrator</span>
          {select('orchestrator', settings.choices.orchestrators)}
        </label>
        <p className="muted small">What runs the schedules: Murakumo writes its pipelines.</p>
      </fieldset>
      <Environments settings={settings} saving={saving} onSave={save} />
      <fieldset className="settings__group">
        <legend>What it generates</legend>
        <label className="settings__row">
          <span>Modelling pattern</span>
          {select('pattern', settings.choices.patterns)}
        </label>
        <label className="settings__row">
          <span>Python pipeline</span>
          {select('python_engine', settings.choices.python_engines)}
        </label>
        <p className="muted small">Also write the pipeline as Polars or PySpark jobs, from the same SQL.</p>
        <label className="settings__check">
          {toggle('plain_sql')} Also write it as plain SQL scripts (no dbt needed)
        </label>
        <label className="settings__check">
          {toggle('run_log')} Record every run in the audit tables (rows loaded and rejected, tests)
        </label>
      </fieldset>
      <fieldset className="settings__group">
        <legend>Working as a team</legend>
        <label className="settings__row">
          <span>Parent branch</span>
          <input
            className="input"
            defaultValue={settings.base_branch}
            disabled={saving}
            onBlur={(e) => e.target.value.trim() && e.target.value.trim() !== settings.base_branch && save({ base_branch: e.target.value.trim() })}
            onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()}
          />
        </label>
        <p className="muted small">The branch people branch from, and that “Get latest” brings in: main, or dev.</p>
        <label className="settings__check">
          {toggle('personal_schemas')} On a shared warehouse, build my changes into schemas of my own
        </label>
      </fieldset>
      <p className="muted small">Each change is saved to murakumo.yaml at once, keeping its comments; ⌘Z takes it back.</p>
    </div>
  )
}

const COMMAND = /^(\$ |export |brew |pip3? |uv |npm |npx |murakumo |cd |cp |mv |mkdir |cat |git |astro |curl |python3? |source |docker |snow |winget |choco |sudo )/

/** A step from `murakumo setup`: a command to copy, a link to read, or a sentence. */
function Step({ text }: { text: string }) {
  if (COMMAND.test(text)) return <code className="setup__command">{text.replace(/^\$ /, '')}</code>
  const link = text.match(/^(.*?)(https?:\/\/\S+)(.*)$/)
  if (link)
    return (
      <>
        {link[1]}
        <a href={link[2]} target="_blank" rel="noreferrer">
          {link[2].replace(/^https:\/\/github.com\/murakumo-data\/murakumo\/blob\/main\//, '')}
        </a>
        {link[3]}
      </>
    )
  return <>{text}</>
}

/** Each shared environment's database and the branch that deploys it, and where people's own go. */
function Environments({ settings, saving, onSave }: { settings: Settings; saving: boolean; onSave: (changes: Partial<Settings>) => void }) {
  const [sql, setSql] = useState<string | null>(null)
  const change = (index: number, key: 'database' | 'branch', value: string) => {
    const environments = settings.environments.map((e, i) => (i === index ? { ...e, [key]: value.trim() || null } : e))
    if (JSON.stringify(environments) !== JSON.stringify(settings.environments)) onSave({ environments })
  }
  return (
    <fieldset className="settings__group">
      <legend>Environments</legend>
      <table className="settings__envs">
        <thead>
          <tr>
            <th>Name</th>
            <th>Database</th>
            <th>Deployed from</th>
          </tr>
        </thead>
        <tbody>
          {settings.environments.map((env, i) => (
            <tr key={env.name}>
              <td>
                <code>{env.name}</code>
              </td>
              <td>
                <input
                  className="input"
                  defaultValue={env.database ?? ''}
                  placeholder="the warehouse's"
                  disabled={saving}
                  aria-label={`${env.name} database`}
                  onBlur={(e) => change(i, 'database', e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()}
                />
              </td>
              <td>
                <input
                  className="input"
                  defaultValue={env.branch ?? ''}
                  placeholder="no branch"
                  disabled={saving}
                  aria-label={`${env.name} branch`}
                  onBlur={(e) => change(i, 'branch', e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted small">Merging into an environment's branch deploys to its database (with the CI workflow).</p>
      <label className="settings__row">
        <span>People's own</span>
        <input
          className="input"
          defaultValue={settings.personal_database ?? ''}
          placeholder="the warehouse's database"
          disabled={saving || settings.warehouse === 'duckdb'}
          onBlur={(e) => e.target.value.trim() !== (settings.personal_database ?? '') && onSave({ personal_database: e.target.value.trim() })}
          onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()}
        />
      </label>
      <p className="muted small">
        {settings.warehouse === 'duckdb'
          ? 'On DuckDB, your own builds go to the project file on this computer.'
          : 'Everyone builds into a schema of their own, named after them, in this database.'}
      </p>
      <div>
        <button type="button" className="button button--small" onClick={async () => setSql(sql ? null : (await api.provision()).sql)}>
          {sql ? 'Hide the setup SQL' : 'Setup SQL…'}
        </button>
      </div>
      {sql && (
        <>
          <p className="muted small">An administrator runs this once to create the databases, roles and grants (murakumo env provision).</p>
          <pre className="setup__sql setup__sql--block">{sql}</pre>
          <button type="button" className="link-button small" onClick={() => navigator.clipboard.writeText(sql).catch(() => {})}>
            Copy
          </button>
        </>
      )}
    </fieldset>
  )
}

const ICONS = {
  ok: <CircleCheck size={15} className="job--ok" aria-label="done" />,
  todo: <CircleAlert size={15} className="job--failed" aria-label="to do" />,
  warn: <CircleDot size={15} className="setup--warn" aria-label="worth a look" />,
  info: <Info size={15} className="muted" aria-label="for information" />,
}

function Setup() {
  const [result, setResult] = useState<{ ready: boolean; checks: SetupCheck[] } | null>(null)
  const [busy, setBusy] = useState(false)
  const load = useCallback(async (connect: boolean) => {
    setBusy(true)
    try {
      setResult(await api.setup(connect))
    } finally {
      setBusy(false)
    }
  }, [])
  useEffect(() => {
    load(false)
  }, [load])

  if (!result) return <p className="muted">Checking…</p>
  const todo = result.checks.filter((c) => c.status === 'todo').length
  const areas = [...new Set(result.checks.map((c) => c.area))]
  return (
    <div className="setup">
      <div className={result.ready ? 'setup__verdict setup__verdict--ok' : 'setup__verdict setup__verdict--todo'}>
        {result.ready ? 'Ready to build.' : `${todo} thing${todo === 1 ? '' : 's'} to do before the project can build.`}
      </div>
      <div className="activity__actions">
        <button className="button button--small" disabled={busy} onClick={() => load(false)}>
          Check again
        </button>
        <button className="button button--small" disabled={busy} onClick={() => load(true)} title="Also open the warehouse and source connections, to test them">
          {busy ? 'Checking…' : 'Test the connections'}
        </button>
      </div>
      {areas.map((area) => (
        <section key={area} className="setup__area">
          <h4>{area}</h4>
          <ul>
            {result.checks
              .filter((c) => c.area === area)
              .map((c, i) => (
                <li key={i} className={`setup__check setup__check--${c.status}`}>
                  <div className="setup__title">
                    {ICONS[c.status]} <span>{c.title}</span>
                  </div>
                  {c.detail && <p className="muted small">{c.detail}</p>}
                  {c.status !== 'ok' && c.steps.length > 0 && (
                    <ol className="setup__steps">
                      {c.steps.map((step, j) => (
                        <li key={j}>
                          <Step text={step} />
                        </li>
                      ))}
                    </ol>
                  )}
                  {c.status !== 'ok' && c.sql && (
                    <>
                      {c.sql_where && <p className="muted small">{c.sql_where}</p>}
                      <pre className="setup__sql">{c.sql}</pre>
                    </>
                  )}
                </li>
              ))}
          </ul>
        </section>
      ))}
    </div>
  )
}
