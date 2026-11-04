"""The run log, read back: what every run recorded in the audit schema.

``murakumo runs`` prints it; the canvas shows it in its Runs panel and says when each schedule last
ran. Both read the same tables the dbt hooks, the loaders and the Python pipelines write.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from murakumo.environments import EnvError, connect, placement_for
from murakumo.manifest import Warehouse
from murakumo.project import Project


def _plain(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat(sep=" ", timespec="seconds") if isinstance(value, datetime) else value.isoformat()
    if isinstance(value, int | float | str) or value is None:
        return value
    return str(value)


def _rows(columns: list[str], rows: list[tuple]) -> list[dict[str, Any]]:
    return [{c: _plain(v) for c, v in zip(columns, row, strict=False)} for row in rows]


def read(
    project: Project, environment: str | None = None, prefix: str | None = None, limit: int = 20, read_only: bool = True
) -> dict[str, Any]:
    """Days, runs, loads, the last run's failed tests and each schedule's last run.

    ``recorded`` is False (with ``reason``) when nothing has been recorded where this looks."""
    warehouse = project.manifest.targets.warehouse.type
    empty: dict[str, Any] = {"recorded": False, "days": [], "runs": [], "loads": [], "failed": [], "schedules": {}}
    try:
        placement = placement_for(project, environment, warehouse, prefix)
    except EnvError as exc:
        return {**empty, "audit": "", "reason": str(exc)}
    where = placement.schema("audit")
    empty["audit"] = where
    try:
        connection = connect(project, read_only=read_only, database=placement.database)
    except EnvError as exc:
        return {**empty, "reason": str(exc)}
    audit = where if warehouse is Warehouse.DUCKDB else f"{connection.database}.{where}"
    try:
        if where not in connection.schemas():
            return {**empty, "reason": f"No runs recorded yet in {where}: build the project first."}
        n = int(limit)
        days = connection.execute(f"select * from {audit}.daily_summary order by day desc limit 14", fetch=True)
        runs = connection.execute(
            "select run_id, started_at, finished_at, engine, environment, schedule, invoked_by, status, models_ok, "
            f"models_failed, tests_passed, tests_failed from {audit}.run_log order by started_at desc limit {n}",
            fetch=True,
        )
        loads = connection.execute(
            f"select loaded_at, source, table_name, rows_ingested, rows_rejected, reason from {audit}.row_counts "
            f"order by loaded_at desc limit {n}",
            fetch=True,
        )
        failed = connection.execute(
            f"select d.model, d.test, d.column_name, d.failures, d.severity from {audit}.dq_results d join ("
            f"select run_id from {audit}.run_log order by started_at desc limit 1) r on d.run_id = r.run_id "
            "where d.status in ('fail', 'error') order by d.model, d.test",
            fetch=True,
        )
        recent = connection.execute(
            f"select schedule, started_at, finished_at, status from {audit}.run_log "
            "where schedule is not null and schedule <> '' order by started_at desc limit 500",
            fetch=True,
        )
    finally:
        connection.close()
    schedules: dict[str, dict[str, Any]] = {}
    for schedule, started, finished, status in recent:
        schedules.setdefault(
            schedule, {"started_at": _plain(started), "finished_at": _plain(finished), "status": status}
        )
    return {
        "audit": where,
        "recorded": True,
        "days": _rows(["day", "runs", "rows_ingested", "rows_rejected", "models_failed", "tests_failed"], days),
        "runs": _rows(
            [
                "run_id",
                "started_at",
                "finished_at",
                "engine",
                "environment",
                "schedule",
                "invoked_by",
                "status",
                "models_ok",
                "models_failed",
                "tests_passed",
                "tests_failed",
            ],
            runs,
        ),
        "loads": _rows(["loaded_at", "source", "table", "rows_ingested", "rows_rejected", "reason"], loads),
        "failed": _rows(["model", "test", "column", "failures", "severity"], failed),
        "schedules": schedules,
    }


__all__ = ["read"]
