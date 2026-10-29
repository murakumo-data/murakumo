import { useCallback, useEffect, useState } from 'react'

import { api, ApiError, type GitStatus, type ModelChange, type State } from './api'

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

type Props = { onState: (state: State) => void; onMessage: (text: string, tone: 'info' | 'error') => void; revision: string }

export function GitPanel({ onState, onMessage, revision }: Props) {
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
    </div>
  )
}
