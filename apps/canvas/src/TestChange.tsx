import { CircleCheck, CircleX, LoaderCircle } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { api, type EnvInfo, type Job } from './api'

// Test my change: what a pull request's preview does, one step at a time. Copy production into
// schemas of the change's own, apply this branch's new migrations there, build and test there,
// look, and drop it. Each step is the `murakumo` command it names, run as a job.

type Step = {
  id: string
  title: string
  text: string
  kind: string
  options: (prefix: string, env: string) => Record<string, unknown>
  command: (prefix: string, env: string) => string
  danger?: boolean
}

const STEPS: Step[] = [
  {
    id: 'clone',
    title: 'Copy production',
    text: 'Copies the shared schemas into schemas starting with the prefix. On Snowflake a clone is instant and takes no space until rows change; on DuckDB the tables are copied.',
    kind: 'env_clone',
    options: (prefix, env) => ({ environment: env, prefix }),
    command: (prefix, env) => `murakumo env clone --env ${env} --prefix ${prefix}`,
  },
  {
    id: 'migrate',
    title: "Apply this branch's migrations",
    text: "Runs the migrations production has not run yet on the copy: the copy keeps production's record, so only this branch's new ones run. If one fails here, it would have failed in production.",
    kind: 'migrate_apply',
    options: (prefix, env) => ({ environment: env, prefix }),
    command: (prefix, env) => `murakumo migrate --apply --env ${env} --prefix ${prefix}`,
  },
  {
    id: 'build',
    title: 'Build and test there',
    text: "Builds every model on the copy, with this branch's code, and runs every test.",
    kind: 'build',
    options: (prefix, env) => ({ environment: env, prefix }),
    command: (prefix, env) => `murakumo build --env ${env} --prefix ${prefix}`,
  },
  {
    id: 'drop',
    title: 'Drop the copy',
    text: 'When you have looked, drop every schema under the prefix. The shared schemas are never touched.',
    kind: 'env_drop',
    options: (prefix, env) => ({ environment: env, prefix }),
    command: (prefix, env) => `murakumo env drop --env ${env} --prefix ${prefix} --yes`,
    danger: true,
  },
]

const suggest = (branch: string) =>
  `pr_${branch
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 24) || 'change'}_`.replace(/^pr_(\d)/, 'pr_$1')

export function TestChange({
  branch,
  environments,
  onClose,
  onOpenJob,
}: {
  branch: string
  environments: string[] // the shared ones; the last (prod) is copied by default
  onClose: () => void
  onOpenJob: (id: string) => void
}) {
  const [prefix, setPrefix] = useState(() => suggest(branch))
  const [env, setEnvName] = useState(environments[environments.length - 1] ?? 'prod')
  const [jobs, setJobs] = useState<Record<string, Job>>({})
  const [error, setError] = useState<string | null>(null)
  const [look, setEnv] = useState<EnvInfo | null>(null)
  const timers = useRef<number[]>([])
  useEffect(() => () => timers.current.forEach((t) => window.clearTimeout(t)), [])
  const valid = /^[a-z][a-z0-9_]*_$/.test(prefix) && !prefix.startsWith('dev_')

  const look = async () => {
    try {
      setEnv(await api.env(env, prefix))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const start = async (step: Step) => {
    setError(null)
    try {
      const job = await api.startJob(step.kind, step.options(prefix, env))
      setJobs((all) => ({ ...all, [step.id]: job }))
      const follow = async () => {
        const now = await api.job(job.id, 1_000_000) // state only: the lines are in Activity
        setJobs((all) => ({ ...all, [step.id]: now }))
        if (now.state === 'running') timers.current.push(window.setTimeout(follow, 1000))
        else if (step.id !== 'drop') look()
        else setEnv(null)
      }
      timers.current.push(window.setTimeout(follow, 800))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const done = (id: string) => jobs[id]?.state === 'succeeded'
  const running = Object.values(jobs).some((j) => j.state === 'running')
  return (
    <div className="dialog-backdrop" onClick={onClose}>
      <div className="dialog dialog--wide" role="dialog" aria-label="Test my change" onClick={(e) => e.stopPropagation()}>
        <h2>Test my change</h2>
        <p className="muted small">
          What a pull request's preview does, here and now: try <code>{branch}</code> on a copy of production before it is
          merged. Each step is one <code>murakumo</code> command; its output is in Activity.
        </p>
        <label className="build__row">
          <span>Copy of</span>
          <select className="input" value={env} disabled={Object.keys(jobs).length > 0} onChange={(e) => setEnvName(e.target.value)}>
            {environments.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <label className="build__row">
          <span>Schema prefix</span>
          <input className="input" value={prefix} disabled={Object.keys(jobs).length > 0} onChange={(e) => setPrefix(e.target.value.trim())} />
        </label>
        {!valid && <p className="small upload__error">Lower case, ending in _, and not dev_ (that is someone's own schemas).</p>}
        <ol className="stepper">
          {STEPS.map((step, i) => {
            const job = jobs[step.id]
            const ready = valid && !running && (i === 0 || done(STEPS[i - 1].id) || (step.id === 'drop' && Object.keys(jobs).length > 0))
            return (
              <li key={step.id} className={job ? `stepper__step stepper__step--${job.state}` : 'stepper__step'}>
                <div className="stepper__head">
                  <span className="stepper__mark">
                    {job?.state === 'running' ? (
                      <LoaderCircle size={16} className="spin" />
                    ) : job?.state === 'succeeded' ? (
                      <CircleCheck size={16} className="job--ok" />
                    ) : job ? (
                      <CircleX size={16} className="job--failed" />
                    ) : (
                      i + 1
                    )}
                  </span>
                  <strong>{step.title}</strong>
                  <button
                    className={step.danger ? 'button button--small button--danger stepper__go' : 'button button--small button--primary stepper__go'}
                    disabled={!ready}
                    onClick={() => start(step)}
                  >
                    {job && job.state !== 'running' ? 'Again' : 'Run'}
                  </button>
                </div>
                <p className="muted small">{step.text}</p>
                <code className="stepper__command">{step.command(prefix, env)}</code>
                {job && job.state !== 'running' && (
                  <button className="link-button small" onClick={() => onOpenJob(job.id)}>
                    {job.state === 'succeeded' ? 'See the output' : 'See what went wrong'}
                  </button>
                )}
              </li>
            )
          })}
        </ol>
        {look && (
          <section className="stepper__look">
            <h4 className="runs__heading">What is under {look.prefix || 'the shared schemas'} in {env}</h4>
            {look.reason ? (
              <p className="muted small">{look.reason}</p>
            ) : (
              <ul className="stepper__schemas">
                {look.schemas.map((s) => (
                  <li key={s.schema}>
                    <code>{s.schema}</code> <span className="muted">{s.exists ? `${s.tables} table${s.tables === 1 ? '' : 's'}` : 'not created'}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        )}
        {error && <p className="upload__error">{error}</p>}
        <div className="dialog__actions">
          <button className="button" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  )
}
