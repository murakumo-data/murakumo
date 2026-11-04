import { Check, ChevronDown, ChevronRight, Copy } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import { api, type GuideChapter, type GuideStep, type GuideTable } from './api'

// The replication guide (`murakumo guide`, generated/guide): every step to rebuild the platform by
// hand, without Murakumo, chapter by chapter, with the SQL and commands in full. Ticks are kept in
// this browser, per project, as on the guide's own page.

type Props = {
  project: string
  revision: string
  onMessage: (text: string, tone: 'info' | 'error') => void
  startJob: (kind: string, options?: Record<string, unknown>) => Promise<unknown>
}

function useProgress(project: string) {
  const key = `murakumo-guide-${project}`
  const [done, setDone] = useState<Record<string, boolean>>(() => {
    try {
      return JSON.parse(localStorage.getItem(key) || '{}')
    } catch {
      return {}
    }
  })
  const toggle = (step: string) => {
    const next = { ...done, [step]: !done[step] }
    setDone(next)
    try {
      localStorage.setItem(key, JSON.stringify(next))
    } catch {
      // private window: the ticks last as long as the page
    }
  }
  return [done, toggle] as const
}

export function GuidePanel({ project, revision, onMessage, startJob }: Props) {
  const [chapters, setChapters] = useState<GuideChapter[] | null>(null)
  const [folder, setFolder] = useState('generated/guide')
  const [open, setOpen] = useState<number | null>(0)
  const [done, toggle] = useProgress(project)
  const load = useCallback(() => {
    api.guide().then(
      (r) => {
        setChapters(r.chapters)
        setFolder(r.folder)
      },
      (e) => onMessage(e.message, 'error'),
    )
  }, [onMessage])
  useEffect(load, [load, revision])

  if (!chapters) return <p className="muted">Loading…</p>
  const steps = chapters.flatMap((c) => c.steps)
  const finished = steps.filter((s) => done[s.key]).length
  return (
    <div className="settings guide">
      <p className="muted small">
        Everything it takes to rebuild this platform by hand, without Murakumo: every account, table, load, model, test
        and schedule, with the SQL and commands in full. It is written to <code>{folder}</code> each time you generate.
      </p>
      <div className="guide__progress">
        <div className="guide__bar" role="progressbar" aria-valuemin={0} aria-valuemax={steps.length} aria-valuenow={finished}>
          <span style={{ width: `${steps.length ? (100 * finished) / steps.length : 0}%` }} />
        </div>
        <span className="small">
          {finished} of {steps.length} steps done
        </span>
      </div>
      <div className="stack__buttons">
        <button
          className="button button--small"
          title="murakumo guide --check: rebuild from the guide's SQL alone, on DuckDB, and compare with a build"
          onClick={() => startJob('guide-check')}
        >
          Check it rebuilds the project
        </button>
      </div>
      <ol className="guide__chapters">
        {chapters.map((chapter) => {
          const count = chapter.steps.filter((s) => done[s.key]).length
          const expanded = open === chapter.number
          return (
            <li key={chapter.number} className="guide__chapter">
              <button className="deploy__head" aria-expanded={expanded} onClick={() => setOpen(expanded ? null : chapter.number)}>
                {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                <strong>
                  {chapter.number}. {chapter.title}
                </strong>
                {chapter.steps.length > 0 && (
                  <span className={count === chapter.steps.length ? 'badge' : 'badge badge--neutral'}>
                    {count}/{chapter.steps.length}
                  </span>
                )}
              </button>
              {expanded && (
                <div className="guide__body">
                  <Text text={chapter.intro} />
                  {chapter.tables.map((t, i) => (
                    <Grid key={i} table={t} />
                  ))}
                  <ol className="guide__steps">
                    {chapter.steps.map((step, index) => (
                      <StepItem
                        key={step.key}
                        number={`${chapter.number}.${index + 1}`}
                        step={step}
                        done={!!done[step.key]}
                        onToggle={() => toggle(step.key)}
                      />
                    ))}
                  </ol>
                </div>
              )}
            </li>
          )
        })}
      </ol>
    </div>
  )
}

function StepItem({ number, step, done, onToggle }: { number: string; step: GuideStep; done: boolean; onToggle: () => void }) {
  const [open, setOpen] = useState(false)
  return (
    <li className={done ? 'guide__step guide__step--done' : 'guide__step'}>
      <div className="guide__step-head">
        <input type="checkbox" checked={done} onChange={onToggle} aria-label={`Done: ${step.title}`} />
        <button className="link-button" aria-expanded={open} onClick={() => setOpen(!open)}>
          {number} {step.title}
        </button>
      </div>
      {open && (
        <div className="guide__step-body">
          <p className="small">
            <strong>Why:</strong> <Inline text={step.why} />
          </p>
          {step.tables.map((t, i) => (
            <Grid key={i} table={t} />
          ))}
          {step.blocks.map((block, i) => (
            <div key={i}>
              {block.title && <div className="muted small">{block.title}</div>}
              <CodeBlock text={block.text} />
            </div>
          ))}
          {step.expect && (
            <p className="small guide__expect">
              <strong>You should see:</strong> <Inline text={step.expect} />
            </p>
          )}
        </div>
      )}
    </li>
  )
}

function CodeBlock({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <div className="guide__code">
      <button
        className="icon-button guide__copy"
        aria-label="Copy"
        title="Copy"
        onClick={() => {
          navigator.clipboard.writeText(text).then(() => {
            setCopied(true)
            setTimeout(() => setCopied(false), 1200)
          })
        }}
      >
        {copied ? <Check size={13} /> : <Copy size={13} />}
      </button>
      <pre className="code-block">{text}</pre>
    </div>
  )
}

function Grid({ table }: { table: GuideTable }) {
  return (
    <div className="guide__table">
      <table>
        <thead>
          <tr>
            {table.headers.map((h) => (
              <th key={h}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((row, i) => (
            <tr key={i}>
              {row.map((cell, j) => (
                <td key={j}>
                  <Inline text={cell} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Text({ text }: { text: string }) {
  return (
    <>
      {text
        .split(/\n\s*\n/)
        .filter((p) => p.trim())
        .map((p, i) => (
          <p key={i} className="small">
            <Inline text={p} />
          </p>
        ))}
    </>
  )
}

/** `code` and **bold**, as the guide's Markdown writes them. */
function Inline({ text }: { text: string }) {
  const parts = text.split(/(`[^`]+`|\*\*[^*]+\*\*)/g)
  return (
    <>
      {parts.map((part, i) =>
        part.startsWith('`') && part.endsWith('`') ? (
          <code key={i}>{part.slice(1, -1)}</code>
        ) : part.startsWith('**') && part.endsWith('**') ? (
          <strong key={i}>{part.slice(2, -2)}</strong>
        ) : (
          part
        ),
      )}
    </>
  )
}
