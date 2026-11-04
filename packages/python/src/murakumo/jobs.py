"""Jobs: the CLI's long-running commands, run in the background for the canvas.

Building, verifying, backfilling or running the pipeline as Python takes minutes, so the canvas
starts them as jobs and follows their output as it comes. A job is always one ``murakumo``
command, run from the project's folder exactly as it would be in a terminal, so what the canvas
does and what the CLI does never drift apart.

The canvas never sends a command line: it names a kind of job (``build``, ``backfill``...) and its
options, and ``command()`` turns them into arguments, checking each value. One job runs at a time;
the last ``KEEP`` are kept, with their output.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

KEEP = 20
MAX_LINES = 5000
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PREFIX = re.compile(r"^[a-z][a-z0-9_]*_$")
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"  # a terminal spinner redraws one line; captured, each frame is a line
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")  # colours and cursor moves, which a log page cannot show
ENVIRONMENTS = ("dev", "prod")
ENGINES = ("polars", "pyspark")


class JobError(Exception):
    """A job that cannot start: unknown kind, a bad option, or another job still running."""


def _name(value: Any, what: str) -> str:
    if not isinstance(value, str) or not NAME.match(value):
        raise JobError(f"{what} must be a name like orders or fct_trip")
    return value


def _day(value: Any, what: str) -> str:
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        raise JobError(f"{what} must be a date like 2024-01-31") from None


def _count(value: Any, what: str, most: int = 1_000_000) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= most:
        raise JobError(f"{what} must be a number from 0 to {most}")
    return value


def _common(options: dict[str, Any], args: list[str], env: bool = True) -> None:
    """The options most jobs share: environment, schema prefix."""
    if env and options.get("environment"):
        if options["environment"] not in ENVIRONMENTS:
            raise JobError(f"environment must be one of {', '.join(ENVIRONMENTS)}")
        args += ["--env", options["environment"]]
    if options.get("prefix"):
        if not PREFIX.match(str(options["prefix"])):
            raise JobError("a schema prefix is lower case and ends in _, like pr_42_")
        args += ["--prefix", options["prefix"]]


def _data(root: Path, options: dict[str, Any], args: list[str]) -> None:
    """Raw tables read from files: {table: path}, each path inside the project folder."""
    for table, path in (options.get("data") or {}).items():
        _name(table, "a raw table")
        full = (root / str(path)).resolve()
        if not full.is_relative_to(root.resolve()) or not full.is_file():
            raise JobError(f"{path} is not a file in the project")
        args += ["--data", f"{table}={full.relative_to(root.resolve())}"]


def command(root: Path, kind: str, options: dict[str, Any]) -> tuple[str, list[str]]:
    """What a job does, in words, and the ``murakumo`` arguments that do it."""
    args: list[str]
    if kind == "build":
        args = ["build"]
        _common(options, args)
        if options.get("sample"):
            args += ["--sample", str(_count(options["sample"], "the number of made-up rows"))]
        _data(root, options, args)
        if options.get("schedule"):
            args += ["--schedule", _name(options["schedule"], "the schedule")]
        if options.get("run_date"):
            args += ["--run-date", _day(options["run_date"], "the run date")]
        what = f"Build {options.get('environment') or 'dev'}"
        if options.get("schedule"):
            what += f": {options['schedule']}"
        return what, args
    if kind == "verify":
        args = ["verify", "--rows", str(_count(options.get("rows", 200), "the number of made-up rows"))]
        _data(root, options, args)
        return "Verify on DuckDB", args
    if kind == "run":
        engine = options.get("engine")
        if engine not in ENGINES:
            raise JobError(f"engine must be one of {', '.join(ENGINES)}")
        args = ["run", "--engine", engine]
        if options.get("sample"):
            args += ["--sample", str(_count(options["sample"], "the number of made-up rows"))]
        if options.get("schedule"):
            args += ["--schedule", _name(options["schedule"], "the schedule")]
        if options.get("run_date"):
            args += ["--run-date", _day(options["run_date"], "the run date")]
        return f"Run as {engine}", args
    if kind == "backfill":
        table = _name(options.get("table"), "the table")
        start, end = _day(options.get("from"), "the first day"), _day(options.get("to"), "the last day")
        if start > end:
            raise JobError("the first day comes after the last")
        args = ["backfill", table, "--from", start, "--to", end]
        _common(options, args)
        return f"Backfill {table} {start} to {end}", args
    if kind == "migrate_apply":
        args = ["migrate", "--apply"]
        _common(options, args, env=False)
        if options.get("mark_only"):
            args.append("--mark-applied")
        return f"Apply migrations{' under ' + options['prefix'] if options.get('prefix') else ''}", args
    if kind == "env_clone":
        args = ["env", "clone"]
        _common(options, args, env=False)
        return f"Clone production into {options.get('prefix') or 'your schemas'}", args
    if kind == "env_drop":
        args = ["env", "drop", "--yes"]
        _common(options, args, env=False)
        return f"Drop {options.get('prefix') or 'your schemas'}", args
    if kind == "export":
        return "Export as plain SQL", ["export", "sql"]
    if kind == "generate":
        return "Generate", ["generate"]
    raise JobError(f"no job {kind!r}")


@dataclass
class Job:
    id: str
    kind: str
    title: str
    command: str
    state: str = "running"  # running | succeeded | failed | cancelled
    started: float = field(default_factory=time.time)
    finished: float | None = None
    code: int | None = None
    lines: list[str] = field(default_factory=list)
    dropped: int = 0  # early lines let go of, so a long job keeps its latest output
    process: subprocess.Popen | None = field(default=None, repr=False)

    def as_dict(self, since: int = 0) -> dict[str, Any]:
        start = max(since - self.dropped, 0)
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "command": self.command,
            "state": self.state,
            "started": self.started,
            "finished": self.finished,
            "code": self.code,
            "next": self.dropped + len(self.lines),  # ask for lines from here next time
            "lines": self.lines[start:],
        }


class Jobs:
    """One job at a time per project, run in a thread; the latest few kept with their output."""

    def __init__(self, root: Path):
        self.root = root
        self.jobs: list[Job] = []
        self.lock = threading.Lock()

    @property
    def running(self) -> Job | None:
        return next((j for j in self.jobs if j.state == "running"), None)

    def start(self, kind: str, options: dict[str, Any]) -> Job:
        title, args = command(self.root, kind, options)
        with self.lock:
            if self.running:
                raise JobError(f"{self.running.title} is still running; wait for it, or stop it")
            job = Job(uuid.uuid4().hex[:12], kind, title, "murakumo " + " ".join(args))
            self.jobs.append(job)
            del self.jobs[:-KEEP]
        threading.Thread(target=self._run, args=(job, args), daemon=True).start()
        return job

    def _run(self, job: Job, args: list[str]) -> None:
        env = {
            **os.environ,
            "NO_COLOR": "1",
            "COLUMNS": "110",
            "PYTHONUNBUFFERED": "1",
            "DBT_USE_COLORS": "false",
            "MURAKUMO_INVOKED_BY": "canvas",
        }
        try:
            job.process = subprocess.Popen(
                [sys.executable, "-m", "murakumo", *args],
                cwd=self.root,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                env=env,
                start_new_session=True,  # so stopping it stops what it started (dbt, Spark)
            )
            assert job.process.stdout is not None
            for line in job.process.stdout:
                text = ANSI.sub("", line.rstrip("\n"))
                if text[:1] in SPINNER and text and job.lines and job.lines[-1][1:] == text[1:]:
                    continue  # the same spinner message again: keep its first frame only
                job.lines.append(text)
                if len(job.lines) > MAX_LINES:
                    job.dropped += len(job.lines) - MAX_LINES
                    del job.lines[:-MAX_LINES]
            job.code = job.process.wait()
            if job.state == "running":
                job.state = "succeeded" if job.code == 0 else "failed"
        except OSError as exc:
            job.lines.append(f"could not start: {exc}")
            job.state = "failed"
        finally:
            job.finished = time.time()

    def cancel(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job.state == "running" and job.process is not None:
            job.state = "cancelled"
            try:
                os.killpg(job.process.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
        return job

    def get(self, job_id: str) -> Job:
        job = next((j for j in self.jobs if j.id == job_id), None)
        if job is None:
            raise JobError(f"no job {job_id}")
        return job

    def wait(self, job_id: str, timeout: float = 600) -> Job:
        """For tests and scripts: block until the job ends."""
        end = time.time() + timeout
        job = self.get(job_id)
        while job.state == "running" and time.time() < end:
            time.sleep(0.05)
        while job.finished is None and time.time() < end:
            time.sleep(0.02)
        return job


__all__ = ["ENGINES", "Job", "JobError", "Jobs", "command"]
