"""``murakumo monitor``: say when the platform is not behaving as it usually does.

Tests check the data against rules someone wrote. This checks the things nobody writes a rule
for, from what every run already records in the audit schema (``runlog.py``):

- **volume**: a table's latest load is far from the size of its recent loads, or empty;
- **late**: a table has not loaded for longer than its freshness setting allows, or, with none
  set, for several times its usual gap between loads;
- **rejected**: a load refused more than a small share of its rows;
- **failed**: the latest run failed, or tests failed in it;
- **quarantined**: more than a small share of a table's latest load was set aside by a quarantine
  rule (those rows are in ``<staging model>__quarantine``, with the rule each failed).

"Usual" is each table's own history: the median of its earlier loads, and how far they spread
around it. Nothing is trained and nothing leaves the warehouse, unless ``--notify`` is given, which
sends the findings (table names and counts, never rows) to the webhooks under ``quality.alerts``.
"""

from __future__ import annotations

import json
import os
import statistics
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from murakumo.environments import EnvError, connect, placement_for
from murakumo.manifest import Monitoring, Warehouse
from murakumo.project import Project

MIN_HISTORY = 4  # fewer earlier loads than this say nothing about what is usual


@dataclass
class Finding:
    level: str  # error | warn
    check: str  # volume | late | rejected | failed | quarantined
    subject: str  # the table, or the run
    message: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Load:
    table: str
    at: datetime
    rows: int
    rejected: int = 0


def _when(value: Any) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _hours(seconds: float) -> str:
    hours = seconds / 3600
    if hours < 1:
        return f"{max(round(seconds / 60), 1)} minutes"
    return f"{hours:.0f} hours" if hours < 72 else f"{hours / 24:.0f} days"


def evaluate(
    loads: list[Load],
    freshness: dict[str, tuple[float | None, float | None]],
    settings: Monitoring,
    now: datetime,
    last_run: dict[str, Any] | None = None,
    failed_tests: list[dict[str, Any]] | None = None,
    set_aside: list[tuple[str, int, int]] | None = None,
) -> list[Finding]:
    """What is unusual in the loads and the latest run. ``freshness`` is each table's (warn, error)
    hours, where the model sets them."""
    findings: list[Finding] = []
    by_table: dict[str, list[Load]] = {}
    for load in sorted(loads, key=lambda item: item.at):
        by_table.setdefault(load.table, []).append(load)

    for table, history in sorted(by_table.items()):
        latest, earlier = history[-1], history[-1 - settings.history : -1]
        sizes = [item.rows for item in earlier]
        if len(sizes) >= MIN_HISTORY:
            usual = statistics.median(sizes)
            spread = statistics.median(abs(size - usual) for size in sizes) * 1.4826  # as a standard deviation
            allowed = max(3 * spread, settings.volume_tolerance * usual)
            if usual > 0 and latest.rows == 0:
                findings.append(
                    Finding(
                        "error", "volume", table, f"the latest load brought no rows; it usually brings {usual:,.0f}"
                    )
                )
            # A size it has had before is not unusual, however far from the middle (a table that swings).
            elif usual > 0 and abs(latest.rows - usual) > allowed and not min(sizes) <= latest.rows <= max(sizes):
                direction = "more" if latest.rows > usual else "fewer"
                change = abs(latest.rows - usual) / usual
                findings.append(
                    Finding(
                        "warn",
                        "volume",
                        table,
                        f"the latest load brought {latest.rows:,} rows, {change:.0%} {direction} than the usual "
                        f"{usual:,.0f}",
                    )
                )
        total = latest.rows + latest.rejected
        if total and latest.rejected / total > settings.rejected_tolerance:
            findings.append(
                Finding(
                    "warn",
                    "rejected",
                    table,
                    f"the latest load refused {latest.rejected:,} of {total:,} rows ({latest.rejected / total:.0%})",
                )
            )
        age = (now - latest.at).total_seconds()
        warn, fail = freshness.get(table.lower(), (None, None))
        if fail is not None and age > fail * 3600:
            findings.append(
                Finding("error", "late", table, f"last loaded {_hours(age)} ago; it should load every {fail:g} hours")
            )
        elif warn is not None and age > warn * 3600:
            findings.append(
                Finding("warn", "late", table, f"last loaded {_hours(age)} ago; it should load every {warn:g} hours")
            )
        elif warn is None and fail is None and len(history) > MIN_HISTORY:
            recent = history[-1 - settings.history :]
            gaps = [(after.at - before.at).total_seconds() for before, after in zip(recent, recent[1:], strict=False)]
            gaps = [gap for gap in gaps if gap > 0]
            usual_gap = statistics.median(gaps) if gaps else 0
            if usual_gap and age > settings.late_after * usual_gap:
                findings.append(
                    Finding(
                        "warn",
                        "late",
                        table,
                        f"last loaded {_hours(age)} ago; it usually loads every {_hours(usual_gap)}",
                    )
                )

    if last_run and last_run.get("status") not in (None, "success", "ok", "pass", "running"):
        what = f"{last_run.get('models_failed') or 0} model(s) and {last_run.get('tests_failed') or 0} test(s) failed"
        name = last_run.get("schedule") or "the latest run"
        findings.append(Finding("error", "failed", str(name), f"{last_run.get('status')}: {what}"))
    for test in failed_tests or []:
        level = "warn" if str(test.get("severity", "")).lower() == "warn" else "error"
        column = f".{test['column']}" if test.get("column") else ""
        findings.append(
            Finding(
                level,
                "failed",
                f"{test.get('model')}{column}",
                f"the test {test.get('test')} failed on {test.get('failures') or 0} row(s)",
            )
        )
    for table, rows, aside in set_aside or []:
        if rows and aside / rows > settings.rejected_tolerance:
            findings.append(
                Finding(
                    "warn",
                    "quarantined",
                    table,
                    f"quarantine rules set aside {aside:,} of the latest load's {rows:,} rows ({aside / rows:.0%})",
                )
            )
    order = {"error": 0, "warn": 1}
    return sorted(findings, key=lambda f: (order.get(f.level, 2), f.check, f.subject))


def check(
    project: Project, environment: str | None = None, prefix: str | None = None, now: datetime | None = None
) -> dict[str, Any]:
    """Read the audit schema and say what is unusual. ``recorded`` is False (with ``reason``) when
    nothing has been recorded where this looks."""
    warehouse = project.manifest.targets.warehouse.type
    settings = project.manifest.quality.monitoring
    try:
        placement = placement_for(project, environment, warehouse, prefix)
        connection = connect(project, read_only=True, database=placement.database, environment=placement.environment)
    except EnvError as exc:
        return {"recorded": False, "reason": str(exc), "findings": [], "tables": 0}
    where = placement.schema("audit")
    audit = where if warehouse is Warehouse.DUCKDB else f"{connection.database}.{where}"
    try:
        if where.lower() not in connection.schemas():
            return {
                "recorded": False,
                "reason": f"No runs recorded yet in {where}: build the project first.",
                "findings": [],
                "tables": 0,
            }
        rows = connection.execute(
            f"select table_name, loaded_at, rows_ingested, rows_rejected from {audit}.row_counts "
            "order by loaded_at desc limit 20000",
            fetch=True,
        )
        runs = connection.execute(
            f"select run_id, schedule, status, models_failed, tests_failed from {audit}.run_log "
            "order by started_at desc limit 1",
            fetch=True,
        )
        failed = []
        if runs:
            failed = connection.execute(
                f"select model, test, column_name, failures, severity from {audit}.dq_results "
                f"where run_id = '{runs[0][0]}' and status in ('fail', 'error')",
                fetch=True,
            )
        aside = _set_aside(project, connection, placement, warehouse)
    finally:
        connection.close()
    loads = [Load(str(t), _when(at), int(n or 0), int(r or 0)) for t, at, n, r in rows if at is not None]
    freshness = {}
    sources = {s.name.lower(): s for s in project.model.sources}
    for entity in project.model.entities:
        setting = entity.freshness or getattr(sources.get((entity.source or "").lower()), "freshness", None)
        if setting is not None:
            freshness[entity.name.lower()] = (setting.warn_after_hours, setting.error_after_hours)
    last = (
        dict(zip(["run_id", "schedule", "status", "models_failed", "tests_failed"], runs[0], strict=False))
        if runs
        else None
    )
    tests = [dict(zip(["model", "test", "column", "failures", "severity"], row, strict=False)) for row in failed]
    findings = evaluate(loads, freshness, settings, now or datetime.now(UTC), last, tests, aside)
    return {
        "recorded": True,
        "audit": where,
        "tables": len({load.table for load in loads}),
        "loads": len(loads),
        "findings": [f.as_dict() for f in findings],
    }


def _set_aside(project: Project, connection, placement, warehouse) -> list[tuple[str, int, int]]:
    """Each quarantined table's latest load: (raw table, its rows, how many were set aside)."""
    from murakumo.generate.plan import build_plan
    from murakumo.generate.targets import target_for

    plan = build_plan(project, target_for(warehouse))
    raw_schemas = {t.entity.name: g.schema for g in plan.sources for t in g.tables}
    qualify = "" if warehouse is Warehouse.DUCKDB else f"{connection.database}."
    found = []
    for model in plan.models:
        if model.extra.get("side") != "quarantined":
            continue
        raw = f"{qualify}{placement.schema(raw_schemas[model.entity.name])}.{model.entity.name}"
        aside = f"{qualify}{placement.schema('staging')}.{model.name}"
        try:
            latest = connection.execute(
                f"select _batch_id, count(*) from {raw} group by _batch_id order by max(_ingested_at) desc limit 1",
                fetch=True,
            )
            if not latest:
                continue
            batch, rows = latest[0]
            count = connection.execute(
                f"select count(*) from {aside} where _batch_id = '{str(batch).replace(chr(39), '')}'", fetch=True
            )[0][0]
        except Exception:  # noqa: BLE001 - not built yet
            continue
        found.append((model.entity.name, int(rows), int(count)))
    return found


def message(project: Project, findings: list[dict[str, Any]], environment: str | None = None) -> str:
    name = project.manifest.project.name + (f" ({environment})" if environment else "")
    lines = [f"Murakumo: {len(findings)} thing(s) to look at in {name}"]
    lines += [f"- {f['level']}: {f['subject']}: {f['message']}" for f in findings[:25]]
    if len(findings) > 25:
        lines.append(f"... and {len(findings) - 25} more. Run `murakumo monitor` to see them all.")
    return "\n".join(lines)


def notify(project: Project, findings: list[dict[str, Any]], environment: str | None = None) -> list[str]:
    """Send the findings to each webhook under quality.alerts. Returns what happened, one line each."""
    sent = []
    for alert in project.manifest.quality.alerts:
        chosen = [f for f in findings if f["level"] in alert.on]
        if not chosen:
            continue
        url = os.environ.get(alert.url_env)
        if not url:
            sent.append(f"{alert.kind}: {alert.url_env} is not set, nothing sent")
            continue
        if not url.startswith("https://"):
            sent.append(f"{alert.kind}: {alert.url_env} must be an https:// address, nothing sent")
            continue
        text = message(project, chosen, environment)
        body: dict[str, Any] = {"text": text}
        if alert.kind == "webhook":
            body = {"project": project.manifest.project.name, "environment": environment, "findings": chosen}
        request = urllib.request.Request(  # noqa: S310 - https only, checked above
            url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=15):  # noqa: S310
                sent.append(f"{alert.kind}: sent {len(chosen)} finding(s)")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            sent.append(f"{alert.kind}: could not send ({getattr(exc, 'reason', exc)})")
    return sent


__all__ = ["Finding", "Load", "check", "evaluate", "message", "notify"]
