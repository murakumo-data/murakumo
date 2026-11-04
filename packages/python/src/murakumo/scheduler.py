"""Running the schedules on this computer, at their times, until stopped.

An orchestrator runs the schedules in production (Murakumo writes its pipelines). Before that, or
for a project that lives on one machine, ``murakumo scheduler`` does the same here: it sleeps until
the next schedule is due, builds what that schedule refreshes (``murakumo build --schedule``), and
goes back to sleep. Each run is recorded in the run log like any other, with its schedule, so the
canvas's Schedules panel shows when each last ran.

``--data TABLE=FOLDER`` loads whatever new files have landed in a folder before each run.
"""

from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime

from murakumo.cron import next_runs
from murakumo.project import Project
from murakumo.schedules import effective


def due(project: Project, after: datetime) -> list[tuple[datetime, str]]:
    """Each schedule's next run after ``after``, soonest first."""
    schedules, _implicit = effective(project)
    timezone = project.manifest.orchestration.timezone
    upcoming = []
    for schedule in schedules:
        runs = next_runs(schedule.cron, timezone, count=1, after=after)
        if runs:
            upcoming.append((runs[0].astimezone(UTC), schedule.name))
    return sorted(upcoming)


def command(schedule: str, environment: str | None, data: list[str]) -> list[str]:
    args = [sys.executable, "-m", "murakumo", "build", "--schedule", schedule]
    if environment:
        args += ["--env", environment]
    for item in data:
        args += ["--data", item]
    return args


def run(
    project: Project,
    environment: str | None = None,
    data: list[str] | None = None,
    say: Callable[[str], None] = print,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], None] = time.sleep,
    runs: int | None = None,
) -> int:
    """Run each schedule at its times until stopped (or ``runs`` runs, for tests). Returns how many ran."""
    done = 0
    while runs is None or done < runs:
        upcoming = due(project, now())
        if not upcoming:
            say("No schedules to run.")
            return done
        at, name = upcoming[0]
        local = at.astimezone().strftime("%a %d %b %H:%M")
        say(f"Next: {name} at {local}" + (f" (then {upcoming[1][1]})" if len(upcoming) > 1 else ""))
        while (wait := (at - now()).total_seconds()) > 0:
            sleep(min(wait, 60))  # wake each minute, so a changed clock is noticed
        say(f"Running {name}: murakumo build --schedule {name}" + (f" --env {environment}" if environment else ""))
        finished = subprocess.run(command(name, environment, data or []), cwd=project.root, check=False)
        say(f"{name} {'succeeded' if finished.returncode == 0 else 'failed'}; recorded in the run log.")
        done += 1
    return done


__all__ = ["command", "due", "run"]
