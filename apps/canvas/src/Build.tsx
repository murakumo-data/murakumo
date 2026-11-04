import { Upload as UploadIcon } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'

import { api, type State, type Upload } from './api'

// Build: load data into the raw tables and build every model and test into the warehouse, as
// `murakumo build` does, from a form. Files added here go to the project's data/ folder (which git
// ignores) and are loaded from there.

type Options = {
  environment: 'dev' | 'prod'
  sample: number
  data: Record<string, string> // raw table -> data/file
  schedule: string
  run_date: string
  prefix: string
}

const size = (bytes: number) =>
  bytes < 1e6 ? `${Math.max(1, Math.round(bytes / 1e3))} KB` : bytes < 1e9 ? `${(bytes / 1e6).toFixed(1)} MB` : `${(bytes / 1e9).toFixed(1)} GB`

/** Pick a file from this computer and add it to data/; says what it added. */
export function UploadButton({ label, accept, onAdded }: { label: string; accept: string; onAdded: (upload: Upload) => void }) {
  const input = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  return (
    <span className="upload">
      <input
        ref={input}
        type="file"
        accept={accept}
        hidden
        onChange={async (e) => {
          const file = e.target.files?.[0]
          e.target.value = ''
          if (!file) return
          setBusy(`Adding ${file.name} (${size(file.size)})…`)
          setError(null)
          try {
            onAdded(await api.upload(file))
          } catch (err) {
            setError(err instanceof Error ? err.message : String(err))
          } finally {
            setBusy(null)
          }
        }}
      />
      <button type="button" className="button button--small" disabled={busy !== null} onClick={() => input.current?.click()}>
        <UploadIcon size={13} /> {busy ?? label}
      </button>
      {error && <span className="upload__error">{error}</span>}
    </span>
  )
}

export function BuildDialog(props: {
  state: State
  onStart: (options: Record<string, unknown>) => void
  onClose: () => void
}) {
  const { state, onStart, onClose } = props
  const raw = useMemo(() => (state.model?.entities ?? []).filter((e) => e.layer === 'raw'), [state])
  const cumulative = (state.model?.entities ?? []).some((e) => e.kind === 'cumulative')
  const schedules = state.orchestration?.implicit ? [] : (state.orchestration?.schedules ?? [])
  const [files, setFiles] = useState<Upload[]>([])
  const [options, setOptions] = useState<Options>({ environment: 'dev', sample: 0, data: {}, schedule: '', run_date: '', prefix: '' })
  const [advanced, setAdvanced] = useState(false)
  useEffect(() => {
    api.uploads().then((r) => setFiles(r.files), () => {})
  }, [])

  const set = (changes: Partial<Options>) => setOptions({ ...options, ...changes })
  const loaded = Object.keys(options.data).length
  const command = [
    'murakumo build',
    options.environment !== 'dev' && `--env ${options.environment}`,
    options.prefix && `--prefix ${options.prefix}`,
    options.sample > 0 && `--sample ${options.sample}`,
    ...Object.entries(options.data).map(([t, f]) => `--data ${t}=${f}`),
    options.schedule && `--schedule ${options.schedule}`,
    options.run_date && `--run-date ${options.run_date}`,
  ]
    .filter(Boolean)
    .join(' ')
  const prefixOk = !options.prefix || /^[a-z][a-z0-9_]*_$/.test(options.prefix)

  return (
    <div className="dialog-backdrop" onClick={onClose}>
      <form
        className="dialog dialog--wide"
        onClick={(e) => e.stopPropagation()}
        onSubmit={(e) => {
          e.preventDefault()
          if (!prefixOk) return
          onStart({
            environment: options.environment,
            sample: options.sample || undefined,
            data: loaded ? options.data : undefined,
            schedule: options.schedule || undefined,
            run_date: options.run_date || undefined,
            prefix: options.prefix || undefined,
          })
        }}
      >
        <h2>Build</h2>
        <p className="muted small">
          Loads the raw tables, then builds every model and runs every test, into the warehouse ({state.project}'s{' '}
          <strong>{options.environment}</strong> environment). The tables are kept.
        </p>

        <fieldset className="build__group">
          <legend>Data for the raw tables</legend>
          {raw.length === 0 && <p className="muted small">The model has no raw tables.</p>}
          {raw.map((table) => (
            <label key={table.id} className="build__row">
              <code>{table.name}</code>
              <select
                className="input"
                value={options.data[table.name] ?? ''}
                onChange={(e) => {
                  const data = { ...options.data }
                  if (e.target.value) data[table.name] = e.target.value
                  else delete data[table.name]
                  set({ data })
                }}
              >
                <option value="">What is already loaded</option>
                {files.map((f) => (
                  <option key={f.path} value={f.path}>
                    {f.name} ({size(f.size)})
                  </option>
                ))}
              </select>
            </label>
          ))}
          <div className="build__files">
            <UploadButton
              label="Add a file…"
              accept=".csv,.tsv,.parquet,.json,.jsonl"
              onAdded={(upload) => setFiles([...files.filter((f) => f.path !== upload.path), upload])}
            />
            <span className="muted small">CSV, Parquet or JSON; kept in data/, which git ignores.</span>
          </div>
          <label className="build__row">
            <span>Made-up rows</span>
            <input
              className="input input--narrow"
              inputMode="numeric"
              value={options.sample || ''}
              placeholder="0"
              onChange={(e) => set({ sample: Number(e.target.value.replace(/\D/g, '')) || 0 })}
            />
          </label>
          <p className="muted small">For trying the pipeline: this many made-up rows go into each raw table that is still empty.</p>
        </fieldset>

        <fieldset className="build__group">
          <legend>What to build</legend>
          <label className="build__row">
            <span>Environment</span>
            <select className="input" value={options.environment} onChange={(e) => set({ environment: e.target.value as 'dev' | 'prod' })}>
              <option value="dev">dev: my own schemas on a shared warehouse</option>
              <option value="prod">prod: the shared schemas</option>
            </select>
          </label>
          {schedules.length > 0 && (
            <label className="build__row">
              <span>Schedule</span>
              <select className="input" value={options.schedule} onChange={(e) => set({ schedule: e.target.value })}>
                <option value="">Everything</option>
                {schedules.map((s) => (
                  <option key={s.name} value={s.name}>
                    Only what {s.name} refreshes
                  </option>
                ))}
              </select>
            </label>
          )}
          {cumulative && (
            <label className="build__row">
              <span>Day to add</span>
              <input className="input" type="date" value={options.run_date} onChange={(e) => set({ run_date: e.target.value })} />
            </label>
          )}
          <button type="button" className="link-button" onClick={() => setAdvanced(!advanced)} aria-expanded={advanced}>
            {advanced ? 'Fewer options' : 'More options'}
          </button>
          {advanced && (
            <>
              <label className="build__row">
                <span>Schema prefix</span>
                <input className="input" placeholder="pr_42_" value={options.prefix} onChange={(e) => set({ prefix: e.target.value.trim() })} />
              </label>
              <p className={prefixOk ? 'muted small' : 'small upload__error'}>
                Build into schemas starting with this, such as a pull request's clone: lower case, ending in _.
              </p>
            </>
          )}
        </fieldset>

        <p className="muted small">
          The same in a terminal: <code>{command}</code>
        </p>
        <div className="dialog__actions">
          <button type="button" className="button" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="button button--primary" disabled={!prefixOk}>
            Build
          </button>
        </div>
      </form>
    </div>
  )
}

/** Run the pipeline as Python (`murakumo run --engine`): Polars or PySpark jobs from the same SQL. */
export function PythonRunDialog(props: { state: State; onStart: (options: Record<string, unknown>) => void; onClose: () => void }) {
  const { state, onStart, onClose } = props
  const schedules = state.orchestration?.implicit ? [] : (state.orchestration?.schedules ?? [])
  const [options, setOptions] = useState({ engine: 'polars', sample: 0, schedule: '' })
  const command = ['murakumo run --engine', options.engine, options.sample > 0 && `--sample ${options.sample}`, options.schedule && `--schedule ${options.schedule}`]
    .filter(Boolean)
    .join(' ')
  return (
    <div className="dialog-backdrop" onClick={onClose}>
      <form
        className="dialog dialog--wide"
        onClick={(e) => e.stopPropagation()}
        onSubmit={(e) => {
          e.preventDefault()
          onStart({ engine: options.engine, sample: options.sample || undefined, schedule: options.schedule || undefined })
        }}
      >
        <h2>Run as Python</h2>
        <p className="muted small">
          Runs every model as a Polars or PySpark job, from the same SQL as the dbt project, reading the raw tables from
          the project's DuckDB file (or made-up rows) and writing Parquet files to <code>lake/</code>. The first PySpark run
          downloads Spark and a Java runtime (about 400 MB).
        </p>
        <label className="build__row">
          <span>Engine</span>
          <select className="input" value={options.engine} onChange={(e) => setOptions({ ...options, engine: e.target.value })}>
            <option value="polars">Polars</option>
            <option value="pyspark">PySpark</option>
          </select>
        </label>
        <label className="build__row">
          <span>Made-up rows</span>
          <input
            className="input input--narrow"
            inputMode="numeric"
            placeholder="0"
            value={options.sample || ''}
            onChange={(e) => setOptions({ ...options, sample: Number(e.target.value.replace(/\D/g, '')) || 0 })}
          />
        </label>
        <p className="muted small">0 reads the raw tables already loaded; a number runs on that many made-up rows per table instead.</p>
        {schedules.length > 0 && (
          <label className="build__row">
            <span>Schedule</span>
            <select className="input" value={options.schedule} onChange={(e) => setOptions({ ...options, schedule: e.target.value })}>
              <option value="">Everything</option>
              {schedules.map((s) => (
                <option key={s.name} value={s.name}>
                  Only what {s.name} refreshes
                </option>
              ))}
            </select>
          </label>
        )}
        <p className="muted small">
          The same in a terminal: <code>{command}</code>
        </p>
        <div className="dialog__actions">
          <button type="button" className="button" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="button button--primary">
            Run
          </button>
        </div>
      </form>
    </div>
  )
}
