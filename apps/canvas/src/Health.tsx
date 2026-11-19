import { Download, RefreshCw } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import { api, type Contracts, type Cost, type DbtImport, type Monitor, type State } from './api'

// Health: what is unusual in the loads and runs (`murakumo monitor`), roughly what the schedules
// cost a month (`murakumo cost`), the lineage for a catalog (`murakumo catalog export`), and
// bringing an existing dbt project into the model (`murakumo import dbt`).

function save(name: string, content: string) {
  const link = document.createElement('a')
  link.href = URL.createObjectURL(new Blob([content], { type: 'application/json' }))
  link.download = name
  link.click()
  URL.revokeObjectURL(link.href)
}

export function HealthPanel({
  refresh,
  environments,
  onState,
  onMessage,
  onModel,
}: {
  refresh: number
  environments: { personal: string; shared: string[] }
  onState: (state: State) => void
  onMessage: (text: string, tone: 'info' | 'error') => void
  onModel: (name: string) => void
}) {
  const [environment, setEnvironment] = useState(environments.personal)
  const [monitor, setMonitor] = useState<Monitor | null>(null)
  const [cost, setCost] = useState<Cost | null>(null)
  const [folder, setFolder] = useState('')
  const [imported, setImported] = useState<DbtImport | null>(null)
  const [busy, setBusy] = useState(false)
  const [contracts, setContracts] = useState<Contracts | null>(null)
  const [contractPath, setContractPath] = useState('')
  const [contractImport, setContractImport] = useState<string | null>(null)
  const fail = useCallback((e: unknown) => onMessage(e instanceof Error ? e.message : String(e), 'error'), [onMessage])

  const load = useCallback(() => {
    api.monitor(environment).then(setMonitor, fail)
    api.cost().then(setCost, fail)
    api.contracts(true, environment).then(setContracts, fail)
  }, [environment, fail])
  useEffect(() => {
    load()
  }, [load, refresh])

  const exportLineage = async (format: 'json' | 'openlineage') => {
    try {
      const data = await api.catalog(format)
      if (format === 'json') save('lineage.json', JSON.stringify(data, null, 2))
      else save('lineage.jsonl', (data as { events: unknown[] }).events.map((e) => JSON.stringify(e)).join('\n') + '\n')
    } catch (e) {
      fail(e)
    }
  }
  const importOdcs = async (dryRun: boolean) => {
    setBusy(true)
    try {
      const result = await api.importOdcs(contractPath.trim(), dryRun)
      if (result.state) onState(result.state)
      setContractImport(
        result.tables
          .map(
            (t) =>
              `${t.new ? '+' : '='} ${t.entity}: ${t.new ? `${t.added.length} column(s), ` : t.added.length ? `${t.added.length} column(s) added, ` : ''}${t.rules} rule(s)` +
              t.different.map((d) => `\n    differs: ${d}`).join(''),
          )
          .join('\n') + (result.written ? '' : '\nNothing written yet.'),
      )
    } catch (e) {
      fail(e)
    } finally {
      setBusy(false)
    }
  }
  const importDbt = async (dryRun: boolean) => {
    setBusy(true)
    try {
      const result = await api.importDbt(folder.trim(), dryRun)
      setImported(result)
      if (result.state) onState(result.state)
      if (result.written) onMessage(`Imported ${result.added.length} table${result.added.length === 1 ? '' : 's'} from ${result.name}.`, 'info')
    } catch (e) {
      fail(e)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="health">
      <section>
        <div className="runs__bar">
          <h4 className="runs__heading">What is unusual</h4>
          <select className="input input--auto" value={environment} onChange={(e) => setEnvironment(e.target.value)} aria-label="Environment">
            <option value={environments.personal}>my own</option>
            {environments.shared.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
          <button className="icon-button icon-button--small runs__refresh" title="Refresh" aria-label="Refresh" onClick={load}>
            <RefreshCw size={14} />
          </button>
        </div>
        {monitor && !monitor.recorded && <p className="muted">{monitor.reason}</p>}
        {monitor?.recorded && monitor.findings.length === 0 && (
          <p className="muted">
            Nothing unusual in {monitor.tables} table{monitor.tables === 1 ? '' : 's'}, from {monitor.loads} recorded load
            {monitor.loads === 1 ? '' : 's'}.
          </p>
        )}
        {monitor?.recorded && monitor.findings.length > 0 && (
          <ul className="health__findings">
            {monitor.findings.map((f, i) => (
              <li key={i} className={`health__finding health__finding--${f.level}`}>
                <span className="health__level">{f.level === 'error' ? 'Error' : 'Warning'}</span>
                <button className="link mono" onClick={() => onModel(f.subject.split('.')[0])}>
                  {f.subject}
                </button>
                <span>{f.message}</span>
              </li>
            ))}
          </ul>
        )}
        <p className="muted small">
          Each table is compared with its own recent loads: size, how long since the last one, and rows refused. Send
          these to Slack, Teams or a webhook with <span className="mono">murakumo monitor --notify</span> (quality.alerts).
        </p>
      </section>

      {cost && (
        <section>
          <h4 className="runs__heading">
            About {cost.total.toLocaleString(undefined, { maximumFractionDigits: 2 })} {cost.currency} a month on {cost.warehouse}
          </h4>
          {cost.lines.length > 0 && (
            <table className="runs__table">
              <thead>
                <tr>
                  <th>Schedule</th>
                  <th>Runs a month</th>
                  <th>Minutes a run</th>
                  <th>{cost.currency}</th>
                </tr>
              </thead>
              <tbody>
                {cost.lines.map((line) => (
                  <tr key={line.schedule}>
                    <td>{line.schedule}</td>
                    <td>{line.runs}</td>
                    <td>
                      {line.minutes}
                      {line.measured ? '' : ' (assumed)'}
                    </td>
                    <td>{line.cost.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <ul className="muted small health__assumptions">
            {cost.assumptions.map((a, i) => (
              <li key={i}>{a}</li>
            ))}
          </ul>
        </section>
      )}

      <section>
        <h4 className="runs__heading">Data contracts</h4>
        {contracts?.check && !contracts.check.checked && <p className="muted">{contracts.check.reason}</p>}
        {contracts?.check?.checked && contracts.check.tables.length === 0 && (
          <p className="muted">No raw table has been loaded yet: nothing to check.</p>
        )}
        {contracts?.check?.checked && contracts.check.tables.length > 0 && contracts.check.breaches.length === 0 && (
          <p className="muted">
            Every load kept to its contract ({contracts.check.tables.length} table{contracts.check.tables.length === 1 ? '' : 's'}).
          </p>
        )}
        {contracts?.check?.checked && contracts.check.breaches.length > 0 && (
          <ul className="health__findings">
            {contracts.check.breaches.map((b, i) => (
              <li key={i} className={`health__finding health__finding--${b.level}`}>
                <span className="health__level">{b.level === 'error' ? 'Error' : 'Warning'}</span>
                <button className="link mono" onClick={() => onModel(b.table)}>
                  {b.table}
                </button>
                <span>{b.message}</span>
              </li>
            ))}
          </ul>
        )}
        {contracts && Object.keys(contracts.contracts).length > 0 && (
          <div className="health__actions">
            <select
              className="input input--auto"
              aria-label="Download a contract"
              defaultValue=""
              onChange={(e) => {
                const name = e.target.value
                if (!name) return
                const link = document.createElement('a')
                link.href = URL.createObjectURL(new Blob([contracts.contracts[name]], { type: 'application/yaml' }))
                link.download = `${name}.odcs.yaml`
                link.click()
                URL.revokeObjectURL(link.href)
                e.target.value = ''
              }}
            >
              <option value="">Download a contract (ODCS)…</option>
              {Object.keys(contracts.contracts)
                .sort()
                .map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
            </select>
          </div>
        )}
        <p className="muted small">
          Each table's contract is written from the model (columns, types, keys, rules, freshness) in the Open Data Contract
          Standard. Bring in a contract a source owner sent you:
        </p>
        <input
          className="input"
          value={contractPath}
          aria-label="The contract file"
          placeholder="/path/to/orders.odcs.yaml"
          onChange={(e) => setContractPath(e.target.value)}
        />
        <div className="health__actions">
          <button className="button button--small" disabled={busy || !contractPath.trim()} onClick={() => importOdcs(true)}>
            See what it would add
          </button>
          <button className="button button--small button--primary" disabled={busy || !contractPath.trim()} onClick={() => importOdcs(false)}>
            Import
          </button>
        </div>
        {contractImport && <pre className="small health__import">{contractImport}</pre>}
      </section>

      <section>
        <h4 className="runs__heading">Lineage for a catalog</h4>
        <p className="muted small">
          Where every table and column comes from, for Marquez, DataHub, Atlan, Purview and others that read OpenLineage.
        </p>
        <div className="health__actions">
          <button className="button button--small" onClick={() => exportLineage('openlineage')}>
            <Download size={14} /> OpenLineage events
          </button>
          <button className="button button--small" onClick={() => exportLineage('json')}>
            <Download size={14} /> Plain JSON
          </button>
        </div>
      </section>

      <section>
        <h4 className="runs__heading">Bring in a dbt project</h4>
        <p className="muted small">
          Its sources, models, keys and relationships, and where each column comes from (run <span className="mono">dbt compile</span> in
          it first). The dbt project is only read.
        </p>
        <input
          className="input"
          value={folder}
          aria-label="The dbt project's folder"
          placeholder="/path/to/dbt_project"
          onChange={(e) => setFolder(e.target.value)}
        />
        <div className="health__actions">
          <button className="button button--small" disabled={busy || !folder.trim()} onClick={() => importDbt(true)}>
            See what it would add
          </button>
          <button className="button button--small button--primary" disabled={busy || !folder.trim()} onClick={() => importDbt(false)}>
            Import
          </button>
        </div>
        {imported && (
          <div className="small health__import">
            <p>
              {imported.written ? 'Added' : 'Would add'} {imported.added.length} table{imported.added.length === 1 ? '' : 's'}
              {imported.added_sources.length > 0 && ` and ${imported.added_sources.length} source${imported.added_sources.length === 1 ? '' : 's'}`}:{' '}
              {imported.mapped} column{imported.mapped === 1 ? '' : 's'} mapped, {imported.unmapped} left to map, {imported.relationships}{' '}
              relationship{imported.relationships === 1 ? '' : 's'}.
              {imported.existing.length > 0 && ` ${imported.existing.length} already in the model, left as they are.`}
              {imported.skipped.length > 0 && ` ${imported.skipped.length} staging model${imported.skipped.length === 1 ? '' : 's'} not imported: Murakumo generates staging itself.`}
            </p>
            {imported.notes.length > 0 && (
              <ul className="muted">
                {imported.notes.slice(0, 20).map((note, i) => (
                  <li key={i}>{note}</li>
                ))}
                {imported.notes.length > 20 && <li>… and {imported.notes.length - 20} more.</li>}
              </ul>
            )}
          </div>
        )}
      </section>
    </div>
  )
}
