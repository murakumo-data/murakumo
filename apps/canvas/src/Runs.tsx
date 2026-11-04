import { CircleCheck, CircleX, RefreshCw } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import { api, type RunLog } from './api'

// Runs: what every run recorded in the audit schema (as `murakumo runs` prints it): by day, each
// run, the rows each load took in or turned away, and the last run's failed tests.

const when = (text: string | null) =>
  text ? new Date(text.replace(' ', 'T')).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : ''

export function RunsPanel({ refresh, onModel }: { refresh: number; onModel: (name: string) => void }) {
  const [environment, setEnvironment] = useState('dev')
  const [log, setLog] = useState<RunLog | null>(null)
  const [error, setError] = useState<string | null>(null)
  const load = useCallback(async () => {
    try {
      setLog(await api.runs(environment))
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [environment])
  useEffect(() => {
    load()
  }, [load, refresh])

  return (
    <div className="runs">
      <div className="runs__bar">
        <select className="input input--auto" value={environment} onChange={(e) => setEnvironment(e.target.value)} aria-label="Environment">
          <option value="dev">dev</option>
          <option value="prod">prod</option>
        </select>
        <span className="muted small">{log?.audit}</span>
        <button className="icon-button icon-button--small runs__refresh" title="Refresh" aria-label="Refresh" onClick={load}>
          <RefreshCw size={14} />
        </button>
      </div>
      {error && <p className="upload__error">{error}</p>}
      {log && !log.recorded && <p className="muted">{log.reason}</p>}
      {log?.recorded && (
        <>
          <section>
            <h4 className="runs__heading">By day</h4>
            <table className="runs__table">
              <thead>
                <tr>
                  <th>Day</th>
                  <th>Runs</th>
                  <th>Rows in</th>
                  <th>Rejected</th>
                  <th>Failed</th>
                </tr>
              </thead>
              <tbody>
                {log.days.map((d) => (
                  <tr key={d.day}>
                    <td>{d.day}</td>
                    <td>{d.runs}</td>
                    <td>{d.rows_ingested.toLocaleString()}</td>
                    <td className={d.rows_rejected ? 'runs__bad' : ''}>{d.rows_rejected.toLocaleString()}</td>
                    <td className={d.models_failed + d.tests_failed ? 'runs__bad' : ''} title="models failed + tests failed">
                      {d.models_failed + d.tests_failed}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
          <section>
            <h4 className="runs__heading">Runs</h4>
            <ul className="runs__list">
              {log.runs.map((r) => (
                <li key={r.run_id} className="runs__run">
                  {r.status === 'success' ? <CircleCheck size={15} className="job--ok" /> : <CircleX size={15} className="job--failed" />}
                  <div>
                    <div>
                      <strong>{when(r.started_at)}</strong> <span className="muted">· {r.engine}</span>
                      {r.schedule && <span className="chip chip--small">{r.schedule}</span>}
                    </div>
                    <div className="muted small">
                      {r.models_ok} built{r.models_failed ? `, ${r.models_failed} failed` : ''} · {r.tests_passed} tests passed
                      {r.tests_failed ? `, ${r.tests_failed} failed` : ''}
                      {r.invoked_by ? ` · by ${r.invoked_by}` : ''}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          </section>
          {log.failed.length > 0 && (
            <section>
              <h4 className="runs__heading">Failed tests in the last run</h4>
              <ul className="runs__list">
                {log.failed.map((f, i) => (
                  <li key={i}>
                    <button className="issue issue--error issue--button" onClick={() => onModel(f.model)}>
                      <span className="issue__code">{f.failures}</span>
                      <span>
                        <strong>{f.model}</strong>
                        {f.column ? `.${f.column}` : ''}: {f.test}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          )}
          <section>
            <h4 className="runs__heading">Loads</h4>
            <table className="runs__table">
              <thead>
                <tr>
                  <th>When</th>
                  <th>Table</th>
                  <th>Rows in</th>
                  <th>Rejected</th>
                </tr>
              </thead>
              <tbody>
                {log.loads.map((l, i) => (
                  <tr key={i} title={l.reason ?? undefined}>
                    <td>{when(l.loaded_at)}</td>
                    <td>
                      <code>{l.table}</code>
                    </td>
                    <td>{l.rows_ingested.toLocaleString()}</td>
                    <td className={l.rows_rejected ? 'runs__bad' : ''}>{l.rows_rejected.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {log.loads.some((l) => l.rows_rejected) && <p className="muted small">Hover a load to see why rows were rejected.</p>}
          </section>
        </>
      )}
    </div>
  )
}
