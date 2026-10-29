import { useCallback, useEffect, useState } from 'react'

import { api, ApiError, type CIInfo, type CIRun } from './api'
import { CodeBlock, Diff } from './Code'

// CI: a workflow at the repository root that runs the checks on every push, and a button that
// runs the same checks here. The workflow is the user's once written: replacing one with changes
// of its own needs a confirmation.

const STATES: Record<CIInfo['state'], string> = {
  missing: 'not written yet',
  current: 'up to date',
  differs: 'changed by hand',
}

const MARKS: Record<string, string> = { waiting: '○', running: '◐', passed: '✓', failed: '✗' }

export function CIPanel({ onMessage }: { onMessage: (text: string, tone: 'info' | 'error') => void }) {
  const [provider, setProvider] = useState<'github' | 'gitlab'>('github')
  const [deploy, setDeploy] = useState(false)
  const [info, setInfo] = useState<CIInfo | null>(null)
  const [run, setRun] = useState<CIRun | null>(null)
  const [showFile, setShowFile] = useState(false)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      const result = await api.ci(provider, deploy)
      setInfo(result)
      setRun(result.run)
    } catch (e) {
      onMessage(e instanceof Error ? e.message : String(e), 'error')
    }
  }, [provider, deploy, onMessage])

  useEffect(() => {
    load()
  }, [load])

  // Follow a run of the checks until it finishes.
  useEffect(() => {
    if (!run?.running) return
    const timer = window.setInterval(async () => {
      try {
        setRun(await api.ciRun())
      } catch {
        // The next tick tries again.
      }
    }, 1000)
    return () => window.clearInterval(timer)
  }, [run?.running])

  if (!info) return <p className="muted">Reading the CI settings…</p>

  const writeFile = async () => {
    if (info.state === 'differs' && !window.confirm(`Replace ${info.file}, losing the changes made to it by hand?`)) return
    setBusy(true)
    try {
      const result = await api.writeCI(provider, deploy, info.state === 'differs')
      setInfo(result)
      onMessage(`Wrote ${result.file}. Commit it, and the checks run on every push.`, 'info')
    } catch (e) {
      onMessage(e instanceof ApiError ? e.message : String(e), 'error')
    } finally {
      setBusy(false)
    }
  }

  const failed = run?.steps.filter((s) => s.state === 'failed') ?? []

  return (
    <div className="ci">
      <p className="muted small">
        CI runs these checks on every push and pull request, so a model change never lands without its code and
        migrations, or with a build that fails.
      </p>
      <div className="ci__providers" role="radiogroup" aria-label="CI service">
        {(['github', 'gitlab'] as const).map((p) => (
          <label key={p} className="small">
            <input type="radio" name="ci-provider" checked={provider === p} onChange={() => setProvider(p)} />{' '}
            {p === 'github' ? 'GitHub Actions' : 'GitLab CI'}
            {info.found[p] && <span className="muted"> (found)</span>}
          </label>
        ))}
      </div>
      <label className="small">
        <input
          type="checkbox"
          checked={deploy}
          disabled={!info.deploy_possible}
          onChange={(e) => setDeploy(e.target.checked)}
        />{' '}
        Also build production on pushes to main, after someone approves it
        {!info.deploy_possible && <span className="muted"> (not for a DuckDB file)</span>}
      </label>

      <div className="ci__file">
        <code>{info.file}</code>
        <span className={`badge ci__state ci__state--${info.state}`}>{STATES[info.state]}</span>
      </div>
      {!info.in_git && (
        <p className="issue issue--warning">The project is not in a git repository yet: run `git init` in it first.</p>
      )}
      <div className="ci__actions">
        {info.state !== 'current' && (
          <button className="button button--small button--primary" disabled={busy} onClick={writeFile}>
            {info.state === 'missing' ? 'Create CI workflow' : "Replace with Murakumo's"}
          </button>
        )}
        <button className="link-button" aria-expanded={showFile} onClick={() => setShowFile(!showFile)}>
          {showFile ? 'Hide' : info.state === 'differs' ? 'Show the difference' : 'Show the file'}
        </button>
      </div>
      {showFile && (info.state === 'differs' ? <Diff diff={info.diff} /> : <CodeBlock code={info.content} />)}

      <h3 className="section__title">Run the checks here</h3>
      <p className="muted small">The same four checks, on this computer. Building on DuckDB takes a minute or so.</p>
      <button
        className="button button--small"
        disabled={run?.running}
        onClick={async () => setRun(await api.startCIRun())}
      >
        {run?.running ? 'Running…' : 'Run checks now'}
      </button>
      {run && run.steps.length > 0 && (
        <ul className="ci__steps">
          {run.steps.map((step) => (
            <li key={step.name} className={`ci__step ci__step--${step.state}`}>
              <span className="ci__mark" aria-hidden>
                {MARKS[step.state] ?? '·'}
              </span>
              <span>
                {step.name} <code className="muted">{step.command}</code>
              </span>
            </li>
          ))}
        </ul>
      )}
      {!run?.running && run && run.steps.length > 0 && (
        <p className={failed.length ? 'issue issue--error' : 'issue issue--info'}>
          {failed.length ? `${failed.length} check${failed.length === 1 ? '' : 's'} failed:` : 'All checks passed.'}
        </p>
      )}
      {failed.map((step) => (
        <details key={step.name} open>
          <summary>{step.name}</summary>
          <CodeBlock code={step.output || '(no output)'} />
        </details>
      ))}
    </div>
  )
}
