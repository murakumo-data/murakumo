"""Cron times for schedules: check them, say them in words, and work out when they next run.

Five fields, as every orchestrator reads them: minute, hour, day of the month, month and day of
the week. Each takes ``*``, a number, a range (``1-5``), a step (``*/15``, ``0-30/10``) or a list
of those; months and days also take names (``jan``, ``mon``). ``@hourly``, ``@daily``,
``@weekly``, ``@monthly`` and ``@yearly`` stand for the usual times. As in standard cron, when
both the day of the month and the day of the week are restricted, a day matching either runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MACROS = {
    "@hourly": "0 * * * *",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@weekly": "0 0 * * 0",
    "@monthly": "0 0 1 * *",
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
}
_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
_DAYS = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]
_DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
_MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]  # fmt: skip
_FIELDS = [("minute", 0, 59, None), ("hour", 0, 23, None), ("day of the month", 1, 31, None),
           ("month", 1, 12, _MONTHS), ("day of the week", 0, 6, _DAYS)]  # fmt: skip


class CronError(ValueError):
    pass


@dataclass(frozen=True)
class Cron:
    text: str
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]  # 0 is Sunday
    any_day: bool  # the day of the month is *
    any_weekday: bool  # the day of the week is *

    def matches_day(self, moment: datetime) -> bool:
        if moment.month not in self.months:
            return False
        day = moment.day in self.days
        weekday = (moment.isoweekday() % 7) in self.weekdays
        if self.any_day and self.any_weekday:
            return True
        if self.any_day:
            return weekday
        if self.any_weekday:
            return day
        return day or weekday


def _value(text: str, low: int, high: int, names: list[str] | None, field: str) -> int:
    lowered = text.lower()
    if names and lowered[:3] in names and not lowered.isdigit():
        return names.index(lowered[:3]) + low
    if not text.isdigit():
        raise CronError(f"{text!r} is not a valid {field}")
    number = int(text)
    if field == "day of the week" and number == 7:  # 7 is Sunday too
        return 0
    if not low <= number <= high:
        raise CronError(f"{field} {number} is out of range ({low}-{high})")
    return number


def _field(text: str, low: int, high: int, names: list[str] | None, field: str) -> set[int]:
    values: set[int] = set()
    for part in text.split(","):
        if not part:
            raise CronError(f"empty value in the {field}")
        base, _, step_text = part.partition("/")
        step = 1
        if step_text:
            if not step_text.isdigit() or int(step_text) == 0:
                raise CronError(f"{step_text!r} is not a valid step in the {field}")
            step = int(step_text)
        if base == "*":
            start, end = low, high
        elif "-" in base:
            first, last = base.split("-", 1)
            start, end = _value(first, low, high, names, field), _value(last, low, high, names, field)
            if end < start:
                raise CronError(f"the range {base} in the {field} runs backwards")
        else:
            start = _value(base, low, high, names, field)
            end = high if step_text else start
        values.update(range(start, end + 1, step))
    return values


def parse(text: str) -> Cron:
    expanded = MACROS.get(text.strip().lower(), text.strip())
    parts = expanded.split()
    if len(parts) != 5:
        raise CronError(
            f"{text!r} is not a cron time: it needs five fields (minute hour day month weekday), such as '0 6 * * *'"
        )
    sets = [_field(p, low, high, names, field) for p, (field, low, high, names) in zip(parts, _FIELDS, strict=True)]
    return Cron(
        text=text.strip(),
        minutes=frozenset(sets[0]),
        hours=frozenset(sets[1]),
        days=frozenset(sets[2]),
        months=frozenset(sets[3]),
        weekdays=frozenset(sets[4]),
        any_day=parts[2] == "*",
        any_weekday=parts[4] == "*",
    )


def check(text: str) -> str:
    """The cron text if it is valid; raises CronError with a reason if not."""
    parse(text)
    return text.strip()


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise CronError(f"{name!r} is not a time zone; use a name such as UTC or Europe/London") from None


def next_runs(text: str, timezone: str = "UTC", count: int = 3, after: datetime | None = None) -> list[datetime]:
    """The next ``count`` run times, in the schedule's time zone."""
    cron = parse(text)
    tz = zone(timezone)
    moment = (after.astimezone(tz) if after else datetime.now(tz)).replace(second=0, microsecond=0)
    moment += timedelta(minutes=1)
    runs: list[datetime] = []
    days_checked = 0
    while len(runs) < count and days_checked < 366 * 5:
        if cron.matches_day(moment):
            for hour in sorted(h for h in cron.hours if h >= moment.hour):
                start = moment.minute if hour == moment.hour else 0
                for minute in sorted(m for m in cron.minutes if m >= start):
                    runs.append(moment.replace(hour=hour, minute=minute))
                    if len(runs) == count:
                        return runs
        moment = (moment + timedelta(days=1)).replace(hour=0, minute=0)
        days_checked += 1
    return runs


def _time(hour: int, minute: int) -> str:
    return f"{hour:02d}:{minute:02d}"


def _list(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def describe(text: str, timezone: str = "UTC") -> str:
    """The cron time in words: ``0 6 * * 1-5`` -> ``every weekday at 06:00 UTC``."""
    cron = parse(text)
    expanded = MACROS.get(text.strip().lower(), text.strip()).split()
    minute, hour, day, month, weekday = expanded

    if hour == "*" and minute.startswith("*/") and day == month == weekday == "*":
        return f"every {minute[2:]} minutes"
    if minute == "*" and hour == "*" and day == month == weekday == "*":
        return "every minute"

    hourly = hour == "*" or (hour.startswith("*/") and len(cron.minutes) == 1)
    if hourly:
        every = "every hour" if hour == "*" else f"every {hour[2:]} hours"
        minutes = sorted(cron.minutes)
        when = f"{every}, on the hour" if minutes == [0] else f"{every} at minute {_list([str(m) for m in minutes])}"
    elif len(cron.hours) * len(cron.minutes) <= 4:
        times = [_time(h, m) for h in sorted(cron.hours) for m in sorted(cron.minutes)]
        when = f"at {_list(times)}"
    else:
        return f"on the cron schedule {text.strip()} ({timezone})"

    if day == "*" and weekday == "*":
        days = "every day"
    elif day == "*":
        if cron.weekdays == frozenset({1, 2, 3, 4, 5}):
            days = "every weekday"
        elif cron.weekdays == frozenset({0, 6}):
            days = "every weekend day"
        else:
            days = "every " + _list([_DAY_NAMES[d] for d in sorted(cron.weekdays)])
    elif weekday == "*" and day.startswith("*/"):
        days = f"every {day[2:]} days"
    elif weekday == "*":
        ordinal = [f"{d}{_suffix(d)}" for d in sorted(cron.days)]
        days = f"on the {_list(ordinal)} of the month"
    else:
        return f"on the cron schedule {text.strip()} ({timezone})"
    if month != "*":
        days += " in " + _list([_MONTH_NAMES[m - 1] for m in sorted(cron.months)])

    if hourly:
        return when if days == "every day" else f"{when}, {days} ({timezone})"
    return f"{days} {when} {timezone}"


def quartz(text: str) -> str:
    """The same time as a Quartz cron expression (Databricks, some Java schedulers).

    Quartz adds seconds, numbers weekdays 1-7 from Sunday, and needs ``?`` in one of the two day
    fields. A time that restricts both days of the month and of the week cannot be written.
    """
    parse(text)
    minute, hour, day, month, weekday = MACROS.get(text.strip().lower(), text.strip()).split()
    if day != "*" and weekday != "*":
        raise CronError(f"{text!r} sets both the day of the month and of the week, which Quartz cannot express")
    if weekday == "*":
        return f"0 {minute} {hour} {day} {month} ?"

    def convert(part: str) -> str:
        base, sep, step = part.partition("/")
        if base == "*":
            return part
        bounds = [str(_value(b, 0, 6, _DAYS, "day of the week") + 1) for b in base.split("-")]
        return "-".join(bounds) + (sep + step if sep else "")

    return f"0 {minute} {hour} ? {month} {','.join(convert(p) for p in weekday.split(','))}"


def _suffix(day: int) -> str:
    if 10 <= day % 100 <= 20:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")


__all__ = ["Cron", "CronError", "check", "describe", "next_runs", "parse", "quartz", "zone"]
