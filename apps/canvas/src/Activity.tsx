import { CircleCheck, CircleSlash, CircleX, LoaderCircle, Square } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { api, type Job } from './api'

// Activity: the long commands the canvas has started (build, verify, backfill, Python runs...),
// each the same `murakumo` command a terminal would run, with its output as it comes.

const POLL_MS = 700

export function stateIcon(job: Job) {
  if (job.state === 'running') return <LoaderCircle size={15} className="spin" aria-label="running" />
  if (job.state === 'succeeded') return <CircleCheck size={15} className="job--ok" aria-label="succeeded" />
  if (job.state === 'cancelled') return <CircleSlash size={15} className="muted" aria-label="stopped" />
  return <CircleX size={15} className="job--failed" aria-label="failed" />
}

function took(job: Job): string {
  const seconds = Math.round((job.finished ?? Date.now() / 1000) - job.started)
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`
}

export type Action = { label: string; title: string; onClick: () => void }

/** What can be started from here, the jobs, newest first, and the output of the one chosen. */
export function ActivityPanel({
  focus,
  onFinished,
  actions,
}: {
  focus: string | null
  onFinished: (job: Job) => void
  actions: Action[]
}) {
  const [jobs, setJobs] = useState<Job[]>([])
  const [chosen, setChosen] = useState<string | null>(focus)
  const [output, setOutput] = useState<{ id: string; job: Job; lines: string[] } | null>(null)
  const log = useRef<HTMLPreElement>(null)
  const follow = useRef(true)
  const finished = useRef(onFinished)
  useEffect(() => {
    finished.current = onFinished
  })

  useEffect(() => setChosen(focus), [focus])

  // The list, refreshed while anything runs.
  useEffect(() => {
    let live = true
    const load = async () => {
      try {
        const list = (await api.jobs()).jobs
        if (live) setJobs(list)
      } catch {
        // Shown again on the next refresh.
      }
    }
    load()
    const timer = window.setInterval(load, 1500)
    return () => {
      live = false
      window.clearInterval(timer)
    }
  }, [])

  const shown = chosen ?? jobs[0]?.id ?? null

  // The chosen job's output: only the new lines each time.
  useEffect(() => {
    if (!shown) return
    let live = true
    let since = 0
    let lines: string[] = []
    let timer: number | undefined
    const poll = async () => {
      try {
        const job = await api.job(shown, since)
        if (!live) return
        lines = since === 0 ? job.lines : [...lines, ...job.lines]
        since = job.next
        setOutput({ id: shown, job, lines })
        if (job.state === 'running') timer = window.setTimeout(poll, POLL_MS)
        else finished.current(job)
      } catch {
        if (live) timer = window.setTimeout(poll, POLL_MS * 3)
      }
    }
    follow.current = true
    poll()
    return () => {
      live = false
      window.clearTimeout(timer)
    }
  }, [shown])

  // Keep the newest output in view, unless the reader scrolled up.
  useEffect(() => {
    const el = log.current
    if (el && follow.current) el.scrollTop = el.scrollHeight
  }, [output])

  const current = output && output.id === shown ? output : null
  return (
    <div className="activity">
      <div className="activity__actions">
        {actions.map((a) => (
          <button key={a.label} className="button button--small" title={a.title} onClick={a.onClick}>
            {a.label}
          </button>
        ))}
      </div>
      {jobs.length === 0 && (
        <p className="muted">
          Nothing has run yet. Builds, checks, backfills and Python runs started from the canvas show here, with
          their output.
        </p>
      )}
      {current && (
        <section className="activity__current">
          <div className="activity__head">
            {stateIcon(current.job)}
            <strong>{current.job.title}</strong>
            <span className="muted small">{took(current.job)}</span>
            {current.job.state === 'running' && (
              <button className="button button--small activity__stop" onClick={() => api.cancelJob(current.job.id)}>
                <Square size={11} /> Stop
              </button>
            )}
          </div>
          <code className="activity__command" title="The same command, to run in a terminal">
            {current.job.command}
          </code>
          <pre
            className="activity__log"
            ref={log}
            onScroll={(e) => {
              const el = e.currentTarget
              follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24
            }}
          >
            {current.lines.join('\n') || (current.job.state === 'running' ? 'Starting…' : '(no output)')}
          </pre>
          {current.job.state === 'failed' && (
            <p className="activity__verdict activity__verdict--failed">
              It stopped with an error (exit code {current.job.code}). The last lines above say why.
            </p>
          )}
        </section>
      )}
      {jobs.length > 0 && (
        <ul className="activity__list" aria-label="Earlier jobs">
          {jobs.map((job) => (
            <li key={job.id}>
              <button
                className={job.id === shown ? 'activity__item activity__item--on' : 'activity__item'}
                onClick={() => setChosen(job.id)}
              >
                {stateIcon(job)}
                <span className="activity__title">{job.title}</span>
                <span className="muted small">{new Date(job.started * 1000).toLocaleTimeString()}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
