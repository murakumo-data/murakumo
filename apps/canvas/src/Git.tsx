import { useCallback, useEffect, useState } from 'react'

import { api, ApiError, type GitStatus, type ImpactReport, type ModelChange, type State } from './api'

// Working with the team through git: how this branch stands against the parent branch, what
// would come in, this branch's own changes, and buttons to bring the parent in and to commit.

export function ChangeList({ changes, empty }: { changes: ModelChange[]; empty: string }) {
  if (changes.length === 0) return <p className="muted small">{empty}</p>
  return (
    <ul className="changes-list">
      {changes.map((c, i) => (
        <li key={i}>
          <span className={`class-badge class-badge--${c.class}`}>{c.class}</span> {c.summary}
        </li>
      ))}
    </ul>
  )
}

type Props = {
  onState: (state: State) => void
  onMessage: (text: string, tone: 'info' | 'error') => void
  revision: string
  onTestChange: (branch: string) => void
}

export function GitPanel({ onState, onMessage, revision, onTestChange }: Props) {
  const [info, setInfo] = useState<GitStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState<string | null>(null)

  const load = useCallback(
    async (refresh = false) => {
      try {
        setInfo(await api.git(refresh))
        setError(null)
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      }
    },
    [],
  )

  // Look again whenever the model changes (an edit here, or a file changed on disk).
  useEffect(() => {
    load()
  }, [load, revision])

  const act = async (name: string, action: () => Promise<void>) => {
    setBusy(name)
    try {
      await action()
    } catch (e) {
      onMessage(e instanceof ApiError ? e.message : String(e), 'error')
    } finally {
      setBusy(null)
      load()
    }
  }

  if (error) return <p className="issue issue--warning">{error}</p>
  if (!info) return <p className="muted">Reading git…</p>

  const sync = (resume: boolean) =>
    act('sync', async () => {
      const result = await api.sync(resume)
      onState(result.state)
      const s = result.sync
      if (s.state === 'conflicts') onMessage(`Both branches changed ${s.conflicts.join(', ')}: resolve them, then Continue.`, 'error')
      else if (s.state === 'up_to_date') onMessage(`Already up to date with ${info.base}.`, 'info')
      else
        onMessage(
          `Brought in ${info.base}: ${s.incoming.length} model change${s.incoming.length === 1 ? '' : 's'}, ` +
            `${s.generated} file${s.generated === 1 ? '' : 's'} regenerated, committed ${s.commit}.` +
            (s.migrations.length ? ` Migrations: ${s.migrations.join('; ')}.` : ''),
          'info',
        )
    })

  return (
    <div className="git">
      <div className="git__where">
        <span>
          On <code>{info.branch}</code>
          {!info.on_base && (
            <>
              {' '}
              · parent <code>{info.base}</code>
            </>
          )}
        </span>
        <button className="link-button" disabled={busy !== null} onClick={() => act('fetch', () => load(true))}>
          {busy === 'fetch' ? 'Checking…' : 'Check for updates'}
        </button>
      </div>
      <p className="muted small">
        {info.personal_schema
          ? `Your own builds go to ${info.personal_where.replace(/^personal: /, '')}, so nobody else's work is touched.`
          : "Your own builds go to the project's DuckDB file on this computer."}
      </p>
      <NewWork info={info} onState={onState} onMessage={onMessage} onChanged={() => load(false)} />
      {!info.on_base && (
        <p className="small">
          {info.behind === 0
            ? `Up to date with ${info.base}.`
            : `${info.behind} commit${info.behind === 1 ? '' : 's'} on ${info.base} not here yet.`}{' '}
          {info.ahead > 0 && `${info.ahead} commit${info.ahead === 1 ? '' : 's'} here not on ${info.base} yet.`}
        </p>
      )}

      {info.merging ? (
        <section className="section">
          <h3 className="section__title">Sync in progress</h3>
          {info.conflicts.length > 0 ? (
            <>
              <p className="small">
                These files changed on both branches. Open each in your editor, keep the right lines between the{' '}
                <code>&lt;&lt;&lt;&lt;&lt;&lt;&lt;</code> and <code>&gt;&gt;&gt;&gt;&gt;&gt;&gt;</code> markers, save, and run{' '}
                <code>git add</code> on it:
              </p>
              <ul className="changes-list">
                {info.conflicts.map((f) => (
                  <li key={f}>
                    <code>{f}</code>
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <p className="small">The conflicts are resolved.</p>
          )}
          <div className="git__actions">
            <button className="button button--small button--primary" disabled={busy !== null || info.conflicts.length > 0} onClick={() => sync(true)}>
              Continue
            </button>
            <button className="button button--small" disabled={busy !== null} onClick={() => act('abort', async () => onState((await api.abortSync()).state))}>
              Give up the sync
            </button>
          </div>
        </section>
      ) : (
        !info.on_base && (
          <section className="section">
            <h3 className="section__title">Coming in from {info.base}</h3>
            <ChangeList changes={info.incoming_changes} empty={info.behind ? 'No model changes, only other files.' : 'Nothing new.'} />
            {info.behind > 0 && (
              <button
                className="button button--small button--primary"
                disabled={busy !== null || info.uncommitted.length > 0}
                title={info.uncommitted.length ? 'Commit your changes first' : undefined}
                onClick={() => sync(false)}
              >
                {busy === 'sync' ? 'Bringing it in…' : `Get latest from ${info.base}`}
              </button>
            )}
            {info.behind > 0 && info.uncommitted.length > 0 && (
              <p className="muted small">Commit your changes first, so nothing of yours is mixed into the merge.</p>
            )}
          </section>
        )
      )}

      <section className="section">
        <h3 className="section__title">Your changes, not committed</h3>
        <ChangeList
          changes={info.local_changes}
          empty={info.uncommitted.length ? `${info.uncommitted.length} file(s) changed, but nothing in the model.` : 'None.'}
        />
        {info.uncommitted.length > 0 && !info.merging && (
          <form
            className="git__commit"
            onSubmit={(e) => {
              e.preventDefault()
              act('commit', async () => {
                const done = await api.commit(message)
                setMessage('')
                onMessage(`Committed ${done.commit}. Push it with git when you are ready.`, 'info')
              })
            }}
          >
            <input
              className="input"
              placeholder="What changed, e.g. Add zone codes"
              aria-label="Commit message"
              value={message}
              onChange={(e) => setMessage(e.target.value)}
            />
            <button className="button button--small" type="submit" disabled={!message.trim() || busy !== null}>
              Commit
            </button>
          </form>
        )}
      </section>

      <section className="section">
        <h3 className="section__title">Test this change</h3>
        <p className="muted small">
          Before it is merged: copy production, apply this branch's migrations, build and test there, then drop the
          copy, step by step.
        </p>
        <button className="button button--small" onClick={() => onTestChange(info.branch)}>
          Test my change…
        </button>
      </section>

      {info.flow.length > 1 && <Promote flow={info.flow} onMessage={onMessage} />}
      <Compare base={info.base} onMessage={onMessage} />
    </div>
  )
}

/** `murakumo diff`: what changed in the model since a branch, tag or commit, and what it reaches. */
function Compare({ base, onMessage }: { base: string; onMessage: (text: string, tone: 'info' | 'error') => void }) {
  const [ref, setRef] = useState(base)
  const [result, setResult] = useState<(ImpactReport & { against: string; markdown: string }) | null>(null)
  const [busy, setBusy] = useState(false)
  const compare = async () => {
    setBusy(true)
    try {
      setResult(await api.diff(ref.trim()))
    } catch (e) {
      onMessage(e instanceof Error ? e.message : String(e), 'error')
    } finally {
      setBusy(false)
    }
  }
  return (
    <section className="section">
      <h3 className="section__title">Compare with</h3>
      <form
        className="git__commit"
        onSubmit={(e) => {
          e.preventDefault()
          compare()
        }}
      >
        <input className="input" aria-label="Branch, tag or commit" placeholder="main, v0.5.0, a commit" value={ref} onChange={(e) => setRef(e.target.value)} />
        <button className="button button--small" type="submit" disabled={busy || !ref.trim()}>
          {busy ? 'Comparing…' : 'Compare'}
        </button>
      </form>
      {result && (
        <div className="compare">
          <p className="small">
            {result.impacts.length === 0 ? (
              <>No model changes since <code>{result.against}</code>.</>
            ) : (
              <>
                {result.impacts.length} change{result.impacts.length === 1 ? '' : 's'} since <code>{result.against}</code>
                {result.worst && (
                  <>
                    ; the riskiest is <span className={`class-badge class-badge--${result.worst}`}>{result.worst}</span>
                  </>
                )}
                .
              </>
            )}
          </p>
          <ul className="changes-list">
            {result.impacts.map((item, i) => (
              <li key={i}>
                <span className={`class-badge class-badge--${item.change.class}`}>{item.change.class}</span> {item.change.summary}
                {(item.columns.length > 0 || item.tests.length > 0 || item.schedules.length > 0) && (
                  <div className="muted small compare__reach">
                    Reaches {[
                      item.columns.length && `${item.columns.length} column${item.columns.length === 1 ? '' : 's'}`,
                      item.tests.length && `${item.tests.length} test${item.tests.length === 1 ? '' : 's'}`,
                      item.metrics.length && `metric${item.metrics.length === 1 ? '' : 's'} ${item.metrics.join(', ')}`,
                      item.schedules.length && `schedule${item.schedules.length === 1 ? '' : 's'} ${item.schedules.join(', ')}`,
                    ]
                      .filter(Boolean)
                      .join(', ')}
                  </div>
                )}
              </li>
            ))}
          </ul>
          {result.migration.length > 0 && <p className="small">Needs a migration: {result.migration.join('; ')}</p>}
          {result.impacts.length > 0 && (
            <button
              className="link-button small"
              onClick={async () => {
                try {
                  await navigator.clipboard.writeText(result.markdown)
                  onMessage('Copied the report, ready to paste into a pull request.', 'info')
                } catch {
                  onMessage('The browser did not allow copying; `murakumo diff --format markdown` prints the same.', 'info')
                }
              }}
            >
              Copy the report for a pull request
            </button>
          )}
        </div>
      )}
    </section>
  )
}

/** Start new work on a branch of its own (feature/..., fix/..., chore/...), from the latest dev. */
function NewWork(props: {
  info: GitStatus
  onState: (state: State) => void
  onMessage: (text: string, tone: 'info' | 'error') => void
  onChanged: () => void
}) {
  const { info, onState, onMessage, onChanged } = props
  const [kind, setKind] = useState('feature')
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const first = info.flow[0]?.branch
  const act = async (action: () => Promise<void>) => {
    setBusy(true)
    try {
      await action()
      onChanged()
    } catch (e) {
      onMessage(e instanceof Error ? e.message : String(e), 'error')
    } finally {
      setBusy(false)
    }
  }
  return (
    <section className="section">
      <h3 className="section__title">Start new work</h3>
      <p className="muted small">
        On a branch of its own, from the latest {first ?? info.base}; back into it through a pull request.
      </p>
      <form
        className="git__commit"
        onSubmit={(e) => {
          e.preventDefault()
          act(async () => {
            const done = await api.newBranch(kind, name)
            onState(done.state)
            setName('')
            onMessage(`On ${done.branch}. Commit your changes, push, and open a pull request into ${first ?? info.base}.`, 'info')
          })
        }}
      >
        <select className="input input--auto" value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Kind of work">
          <option value="feature">feature</option>
          <option value="fix">fix</option>
          <option value="chore">chore</option>
          <option value="hotfix">hotfix</option>
        </select>
        <input className="input" placeholder="add-zone-codes" aria-label="Branch name" value={name} onChange={(e) => setName(e.target.value)} />
        <button className="button button--small" type="submit" disabled={busy || !name.trim()}>
          Start
        </button>
      </form>
      {info.flow.length > 0 && (
        <button
          className="link-button small"
          disabled={busy}
          title="Create the environments' branches from main if they are missing (murakumo branch start)"
          onClick={() => act(async () => onMessage((await api.startFlow()).done.join(' '), 'info'))}
        >
          Create missing {info.flow.map((f) => f.branch).join(', ')} branches
        </button>
      )}
    </section>
  )
}

/** Promote an environment's branch to the next: what would move, and where to open the pull request. */
function Promote({ flow, onMessage }: { flow: GitStatus['flow']; onMessage: (text: string, tone: 'info' | 'error') => void }) {
  const [result, setResult] = useState<Awaited<ReturnType<typeof api.promotion>> | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  return (
    <section className="section">
      <h3 className="section__title">Promote</h3>
      <p className="muted small">
        {flow.map((f) => `${f.branch} (${f.environment})`).join(' → ')}. Merging into a branch deploys to its environment.
      </p>
      <div className="git__actions">
        {flow.slice(0, -1).map((f, i) => (
          <button
            key={f.branch}
            className="button button--small"
            disabled={busy !== null}
            onClick={async () => {
              setBusy(f.branch)
              try {
                setResult(await api.promotion(f.branch))
              } catch (e) {
                onMessage(e instanceof Error ? e.message : String(e), 'error')
              } finally {
                setBusy(null)
              }
            }}
          >
            {busy === f.branch ? 'Checking…' : `${f.branch} → ${flow[i + 1].branch}`}
          </button>
        ))}
      </div>
      {result && (
        <div className="compare">
          <p className="small">
            {result.commits} commit{result.commits === 1 ? '' : 's'} on <code>{result.from}</code> not on <code>{result.to}</code> yet
            {result.changes.length ? ', with these model changes:' : '; no model changes.'}
          </p>
          <ChangeList changes={result.changes} empty="" />
          {result.url ? (
            <a className="button button--small button--primary" href={result.url} target="_blank" rel="noreferrer">
              Open the pull request
            </a>
          ) : (
            <p className="muted small">
              Open a pull request from {result.from} into {result.to} where the repository is hosted.
            </p>
          )}
        </div>
      )}
    </section>
  )
}
