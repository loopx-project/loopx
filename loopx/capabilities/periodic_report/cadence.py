"""Bounded calendar evaluation for the built-in periodic report subscription.

The profile format can describe schedules for other hosts. This evaluator
deliberately accepts only daily/weekly wall-clock schedules supported locally.
It calculates boundaries; it neither runs an Automation nor authorizes delivery.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .profile import _normalize_schedule

_DAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")


def normalize_report_cadence(raw: object) -> dict[str, str] | None:
    schedule = _normalize_schedule(raw)
    if schedule is None:
        return None
    try:
        ZoneInfo(schedule["timezone"])
    except ZoneInfoNotFoundError as exc:
        raise ValueError("schedule.timezone is unknown") from exc
    fields: dict[str, str] = {}
    for item in schedule["rrule"].split(";"):
        key, separator, value = item.partition("=")
        if not separator or key in fields or not value:
            raise ValueError("schedule.rrule contains an empty or duplicate field")
        fields[key] = value
    if fields.get("FREQ") not in {"DAILY", "WEEKLY"}:
        raise ValueError("local report cadence supports FREQ=DAILY or WEEKLY")
    allowed = {"FREQ", "BYHOUR", "BYMINUTE", "INTERVAL"}
    if fields["FREQ"] == "WEEKLY":
        allowed.add("BYDAY")
        if fields.get("BYDAY") not in _DAYS:
            raise ValueError("weekly report cadence requires one BYDAY, MO through SU")
    if set(fields) - allowed or fields.get("INTERVAL", "1") != "1":
        raise ValueError("local report cadence supports one daily or weekly occurrence")
    for key, maximum in (("BYHOUR", 23), ("BYMINUTE", 59)):
        value = fields.get(key, "0" if key == "BYMINUTE" else "")
        if not value.isascii() or not value.isdigit() or not 0 <= int(value) <= maximum:
            raise ValueError(f"schedule.rrule {key} must be an integer from 0 to {maximum}")
        fields[key] = str(int(value))
    canonical = [f"FREQ={fields['FREQ']}"]
    if fields["FREQ"] == "WEEKLY":
        canonical.append(f"BYDAY={fields['BYDAY']}")
    canonical.extend(f"{key}={fields[key]}" for key in ("BYHOUR", "BYMINUTE"))
    return {**schedule, "rrule": ";".join(canonical)}


def _occurrences(schedule: dict[str, str], center: date) -> list[datetime]:
    zone = ZoneInfo(schedule["timezone"])
    fields = dict(item.split("=", 1) for item in schedule["rrule"].split(";"))
    occurrences: list[datetime] = []
    # Two previous valid weekly boundaries even across a skipped DST date,
    # and the next valid boundary. Fixed bounds avoid unbounded RRULE work.
    for offset in range(-28, 15):
        try:
            day = center + timedelta(days=offset)
        except OverflowError:
            continue
        if fields["FREQ"] == "WEEKLY" and day.weekday() != _DAYS.index(fields["BYDAY"]):
            continue
        wall = datetime.combine(day, time(int(fields["BYHOUR"]), int(fields["BYMINUTE"])))
        try:
            candidate = wall.replace(tzinfo=zone, fold=0).astimezone(timezone.utc)
            roundtrip = candidate.astimezone(zone).replace(tzinfo=None)
        except OverflowError:
            continue
        if roundtrip != wall:
            continue
        occurrences.append(candidate)
    return occurrences


def _current(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("cadence evaluation time must include a UTC offset")
    return now.astimezone(timezone.utc)


def report_cadence_next_due_at(raw: object, *, now: datetime) -> str | None:
    """Next eligible deadline, including a future first local report date."""
    schedule = normalize_report_cadence(raw)
    if schedule is None:
        return None
    current = _current(now)
    zone = ZoneInfo(schedule["timezone"])
    start = date.fromisoformat(schedule.get("start_date", "0001-01-01"))
    center = max(current.astimezone(zone).date(), start)
    future = [value for value in _occurrences(schedule, center)
              if value > current and value.astimezone(zone).date() >= start]
    if not future:
        raise ValueError("schedule has no supported next calendar deadline")
    return future[0].isoformat().replace("+00:00", "Z")


def report_cadence_window(raw: object, *, now: datetime) -> dict[str, Any] | None:
    """Latest completed interval, or no interval before the first report date.

    Spring-forward nonexistent wall times are skipped; a fall-back occurrence
    uses its first fold only. The start date fences the due boundary, not the
    beginning of its reporting interval. Publication still owns the cursor.
    """
    schedule = normalize_report_cadence(raw)
    if schedule is None:
        return None
    current = _current(now)
    zone = ZoneInfo(schedule["timezone"])
    occurrences = _occurrences(schedule, current.astimezone(zone).date())
    past = [value for value in occurrences if value <= current]
    future = [value for value in occurrences if value > current]
    if len(past) < 2 or not future:
        raise ValueError("schedule has no supported bounded calendar window")
    if schedule.get("start_date") and past[-1].astimezone(zone).date().isoformat() < schedule["start_date"]:
        return None
    def stamp(value: datetime) -> str:
        return value.isoformat().replace("+00:00", "Z")
    return {
        "schedule": schedule,
        "start_at": stamp(past[-2]),
        "due_at": stamp(past[-1]),
        "next_due_at": stamp(future[0]),
    }
