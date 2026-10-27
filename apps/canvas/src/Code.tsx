import { useEffect, useState } from 'react'

import { api, ApiError, type EntityCode, type FileStatus, type Generated } from './api'

export function CodeBlock({ code }: { code: string }) {
  return <pre className="code">{code}</pre>
}

export function Diff({ diff }: { diff: string }) {
  if (!diff) return <p className="muted">No differences.</p>
  const lines = diff.split('\n').filter((line, i, all) => line !== '' || i < all.length - 1)
  return (
    <pre className="code code--diff">
      {lines.map((line, i) => {
        const kind = line.startsWith('+++') || line.startsWith('---')
          ? 'file'
          : line.startsWith('@@')
            ? 'hunk'
            : line.startsWith('+')
              ? 'add'
              : line.startsWith('-')
                ? 'del'
                : 'same'
        return (
          <span key={i} className={`diff diff--${kind}`}>
            {line}
            {'\n'}
          </span>
        )
      })}
    </pre>
  )
}

const LABEL: Record<FileStatus, string> = { added: 'new', changed: 'changed', removed: 'removed', unchanged: '' }

export function StatusBadge({ status }: { status: FileStatus }) {
  if (status === 'unchanged') return null
  return <span className={`status-badge status-badge--${status}`}>{LABEL[status]}</span>
}

function FileDiff({ path }: { path: string }) {
  const [diff, setDiff] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let live = true
    api
      .generatedFile(path)
      .then((file) => live && setDiff(file.diff))
      .catch((e: ApiError) => live && setError(e.message))
    return () => {
      live = false
    }
  }, [path])
  if (error) return <p className="issue issue--error">{error}</p>
  if (diff === null) return <p className="muted">Loading…</p>
  return <Diff diff={diff} />
}

type ChangesProps = {
  generated: Generated | null
  entityName: (id: string) => string | undefined
  onGenerate: () => void
  onMigrate: (name: string | null) => void
  busy: boolean
}

/** What generating now would change in generated/, and the migration the model needs. */
export function ChangesPanel({ generated, entityName, onGenerate, onMigrate, busy }: ChangesProps) {
  const [open, setOpen] = useState<string | null>(null)
  const [showSql, setShowSql] = useState(false)
  const [name, setName] = useState('')
  if (!generated) return <p className="muted">Generating…</p>
  if (generated.blocked) return <p className="issue issue--warning">{generated.blocked}</p>

  const changed = generated.files.filter((f) => f.status !== 'unchanged')
  const migration = generated.migration
  return (
    <div className="changes">
      <section className="section">
        <div className="section__head">
          <h3 className="section__title">
            generated/ <span className="muted">for {generated.warehouse}</span>
          </h3>
          <button className="button button--primary button--small" disabled={busy || changed.length === 0} onClick={onGenerate}>
            Generate
          </button>
        </div>
        {changed.length === 0 ? (
          <p className="muted">Up to date with the model ({generated.files.length} files).</p>
        ) : (
          <ul className="file-list">
            {changed.map((file) => {
              const owners = file.entities.map(entityName).filter(Boolean)
              return (
                <li key={file.path}>
                  <button
                    className="file-list__item"
                    aria-expanded={open === file.path}
                    onClick={() => setOpen(open === file.path ? null : file.path)}
                  >
                    <StatusBadge status={file.status} />
                    <span className="file-list__name" title={file.path}>
                      <span className="file-list__path">{file.path.split('/').pop()}</span>
                      <span className="file-list__folder">
                        {file.path.split('/').slice(0, -1).join('/')}
                        {owners.length > 0 && owners.length < 4 && ` · ${owners.join(', ')}`}
                      </span>
                    </span>
                  </button>
                  {file.edited_by_hand && (
                    <p className="issue issue--warning">Edited by hand: generating overwrites it. Put custom code in custom/.</p>
                  )}
                  {open === file.path && <FileDiff path={file.path} />}
                </li>
              )
            })}
          </ul>
        )}
      </section>

      <section className="section">
        <div className="section__head">
          <h3 className="section__title">Migration</h3>
          {migration && (
            <button className="button button--small" disabled={busy} onClick={() => onMigrate(name.trim() || null)}>
              Write {migration.baseline ? 'baseline' : `#${migration.number}`}
            </button>
          )}
        </div>
        {!migration ? (
          <p className="muted">The tables that keep data match the model; no migration needed.</p>
        ) : (
          <>
            <p className="muted">
              {migration.baseline
                ? 'No migrations yet. The first one records the landing tables as they are now.'
                : 'Tables that keep data between runs need these changes:'}
            </p>
            {!migration.baseline && (
              <ul className="migration">
                {migration.changes.map((c, i) => (
                  <li key={i} className={c.destructive ? 'migration__change migration__change--destructive' : 'migration__change'}>
                    {c.destructive ? '! ' : ''}
                    {c.summary}
                  </li>
                ))}
              </ul>
            )}
            <input
              className="input"
              placeholder={migration.file.split('/').pop()}
              aria-label="Migration name"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
            <button className="link-button" onClick={() => setShowSql(!showSql)} aria-expanded={showSql}>
              {showSql ? 'Hide SQL' : 'Show SQL'}
            </button>
            {showSql && <CodeBlock code={migration.sql} />}
          </>
        )}
      </section>
    </div>
  )
}

/** The dbt SQL and DDL generated for one entity, refreshed whenever the generated code changes. */
export function EntityCodeView({ entityId, revision }: { entityId: string; revision: string }) {
  const [code, setCode] = useState<EntityCode | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let live = true
    api
      .entityCode(entityId)
      .then((c) => {
        if (!live) return
        setCode(c)
        setError(null)
      })
      .catch((e: ApiError) => live && setError(e.message))
    return () => {
      live = false
    }
  }, [entityId, revision])
  if (error) return <p className="issue issue--error">{error}</p>
  if (!code) return <p className="muted">Generating…</p>
  if (code.blocked) return <p className="issue issue--warning">{code.blocked}</p>
  if (code.files.length === 0 && !code.ddl) {
    return <p className="muted">Nothing is generated for this entity yet: give its columns mappings.</p>
  }
  return (
    <div className="section">
      {code.files.map((file) => (
        <div key={file.path} className="code-file">
          <div className="code-file__head">
            <span className="file-list__path">{file.path}</span>
            <StatusBadge status={file.status} />
          </div>
          <CodeBlock code={file.content} />
        </div>
      ))}
      {code.ddl && (
        <div className="code-file">
          <div className="code-file__head">
            <span className="file-list__path">DDL</span>
          </div>
          <CodeBlock code={code.ddl} />
        </div>
      )}
    </div>
  )
}
