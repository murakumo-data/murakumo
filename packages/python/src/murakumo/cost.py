"""``murakumo cost``: roughly what the schedules cost to run each month, before the bill says so.

An estimate, with every assumption shown. It multiplies three things:

- how often each schedule runs in a month, from its cron time;
- how long a run takes: measured from the run log when runs have been recorded, else assumed;
- what the warehouse charges for that time (or, for warehouses that bill by data read, per run).

The starting figures per warehouse are public list prices as they were when this was written, for
the smallest size. They are a place to start, not a quote: prices differ by region, edition and
contract, and they change. Put your own under ``costs:`` in murakumo.yaml.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from murakumo.cron import CronError, parse
from murakumo.manifest import Warehouse
from murakumo.project import Project


@dataclass(frozen=True)
class Rate:
    unit: str  # what is bought
    unit_price: float  # in US dollars
    units_per_hour: float  # for the smallest compute
    minimum_seconds: int  # the least a start is billed for
    by: str = "time"  # time | scan
    note: str = ""


# Starting figures: list prices, smallest size. Each can be replaced under costs: in murakumo.yaml.
RATES: dict[Warehouse, Rate] = {
    Warehouse.SNOWFLAKE: Rate("credit", 3.0, 1.0, 60, note="an X-Small warehouse uses 1 credit an hour"),
    Warehouse.DATABRICKS: Rate("DBU", 0.70, 4.0, 60, note="a 2X-Small serverless SQL warehouse uses 4 DBUs an hour"),
    Warehouse.BIGQUERY: Rate("TiB read", 6.25, 0.0, 0, by="scan", note="on-demand queries are billed by data read"),
    Warehouse.REDSHIFT: Rate("RPU-hour", 0.375, 8.0, 60, note="Redshift Serverless starts at 8 RPUs"),
    Warehouse.FABRIC: Rate("CU-hour", 0.18, 2.0, 0, note="an F2 capacity is 2 CUs, billed while it is on"),
    Warehouse.SYNAPSE: Rate("TB read", 5.0, 0.0, 0, by="scan", note="serverless SQL is billed by data read"),
    Warehouse.POSTGRES: Rate("hour", 0.0, 1.0, 0, note="your own server: no charge per run"),
    Warehouse.DUCKDB: Rate("hour", 0.0, 1.0, 0, note="runs on this computer: no charge per run"),
}
DEFAULT_MINUTES = 5.0
DEFAULT_GB = 1.0


@dataclass
class Line:
    schedule: str
    cron: str
    runs: float  # in a month
    minutes: float  # per run
    measured: bool
    units: float
    cost: float


@dataclass
class Estimate:
    warehouse: str
    currency: str
    unit: str
    unit_price: float
    units_per_hour: float
    lines: list[Line] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)

    @property
    def total(self) -> float:
        return sum(line.cost for line in self.lines)

    def as_dict(self) -> dict[str, Any]:
        return {
            "warehouse": self.warehouse,
            "currency": self.currency,
            "unit": self.unit,
            "unit_price": self.unit_price,
            "units_per_hour": self.units_per_hour,
            "total": round(self.total, 2),
            "lines": [
                {**line.__dict__, "units": round(line.units, 3), "cost": round(line.cost, 2)} for line in self.lines
            ],
            "assumptions": self.assumptions,
        }


def runs_per_month(cron: str) -> float:
    """How many times a cron time comes round in an average month (a year's worth, over twelve)."""
    parsed = parse(cron)
    day = datetime(2027, 1, 1)  # a year that is not a leap year
    days = 0
    for _ in range(365):
        days += parsed.matches_day(day)
        day += timedelta(days=1)
    return days * len(parsed.hours) * len(parsed.minutes) / 12


def _measured(project: Project, environment: str | None) -> dict[str, float]:
    """Each schedule's usual run time in minutes, from the run log, where runs were recorded."""
    from murakumo import runlog

    log = runlog.read(project, environment, limit=200)
    times: dict[str, list[float]] = {}
    for run in log.get("runs", []):
        if not (run.get("schedule") and run.get("started_at") and run.get("finished_at")):
            continue
        try:
            started = datetime.fromisoformat(run["started_at"])
            finished = datetime.fromisoformat(run["finished_at"])
        except ValueError:
            continue
        times.setdefault(run["schedule"], []).append((finished - started).total_seconds() / 60)
    return {name: sorted(found)[len(found) // 2] for name, found in times.items() if found}


def estimate(project: Project, environment: str | None = None, measured: dict[str, float] | None = None) -> Estimate:
    warehouse = project.manifest.targets.warehouse.type
    rate, settings = RATES[warehouse], project.manifest.costs
    price = rate.unit_price if settings.unit_price is None else settings.unit_price
    per_hour = rate.units_per_hour if settings.units_per_hour is None else settings.units_per_hour
    result = Estimate(warehouse.value, settings.currency, rate.unit, price, per_hour)
    if measured is None:
        try:
            measured = _measured(project, environment)
        except Exception:  # noqa: BLE001 - no warehouse to ask is not a reason to give no estimate
            measured = {}
    assumed = settings.minutes_per_run or DEFAULT_MINUTES
    gb = DEFAULT_GB if settings.gb_scanned_per_run is None else settings.gb_scanned_per_run
    for schedule in project.manifest.orchestration.schedules:
        try:
            runs = runs_per_month(schedule.cron)
        except CronError:
            continue
        minutes = measured.get(schedule.name, assumed)
        if rate.by == "scan":
            units = runs * gb / 1024
        else:
            billed = max(minutes * 60, rate.minimum_seconds) / 3600
            units = runs * billed * per_hour
        result.lines.append(
            Line(
                schedule.name,
                schedule.cron,
                round(runs, 1),
                round(minutes, 1),
                schedule.name in measured,
                units,
                units * price,
            )
        )
    own = "yours, from murakumo.yaml" if settings.unit_price is not None else "a list price to start from"
    if price == 0 and settings.unit_price is None:
        result.assumptions.append(f"{rate.note.capitalize()}.")
    else:
        result.assumptions.append(f"One {rate.unit} costs {price:g} {settings.currency} ({own}).")
    if price == 0 and settings.unit_price is None:
        pass
    elif rate.by == "scan":
        how = "from murakumo.yaml" if settings.gb_scanned_per_run is not None else "assumed"
        result.assumptions.append(
            f"Each run reads {gb:g} GB ({how}: costs.gb_scanned_per_run). {rate.note.capitalize()}."
        )
    else:
        result.assumptions.append(f"{rate.note.capitalize()}; {per_hour:g} {rate.unit}(s) an hour is used here.")
        if rate.minimum_seconds:
            result.assumptions.append(f"Each start is billed for at least {rate.minimum_seconds} seconds.")
        unmeasured = [line.schedule for line in result.lines if not line.measured]
        if unmeasured:
            result.assumptions.append(
                f"No run of {', '.join(unmeasured)} is recorded yet, so {assumed:g} minutes a run is assumed "
                "(costs.minutes_per_run)."
            )
    if not result.lines:
        result.assumptions.append("The project has no schedules: add one to see what running it costs.")
    result.assumptions.append(
        "Storage, ingestion tools, the orchestrator and people's own queries are not counted. Prices differ by "
        "region, edition and contract: set yours under costs: in murakumo.yaml."
    )
    return result


__all__ = ["RATES", "Estimate", "Line", "estimate", "runs_per_month"]
