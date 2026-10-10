from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any


_MIN_TIMESTAMP = datetime.min.replace(tzinfo=timezone.utc)
_ISO_DATE_WITH_SEPARATOR = re.compile(
    r"^(?P<date>\d{4}-W\d{2}(?:-[1-7])?|\d{4}W\d{2}[1-7]?|"
    r"\d{4}-\d{2}-\d{2}|\d{8})(?P<separator>[^Zz])(?P<clock>.+)$"
)
_ISO_CLOCK = re.compile(r"^(\d{2})(?:(:?)(\d{2})(?:\2(\d{2}))?)?([.,]\d+)?$")


def _stable_iso_timestamp_text(text: str) -> str | None:
    """Normalize reduced fractional clocks and reject version-specific 24:00.

    Python 3.11–3.13 accept fractions after an hour or minute, while 3.14
    accepts 24:00 as end-of-day. Keep the retained reader's wire grammar stable
    across supported Python versions: preserve the former inputs and reject the
    latter because the TypeScript Todo codec does not carry an end-of-day date
    rollover.
    """
    match = _ISO_DATE_WITH_SEPARATOR.fullmatch(text)
    if match is None:
        return text

    clock = match.group("clock")
    zone_suffix = ""
    if clock.endswith(("Z", "z")):
        clock, zone_suffix = clock[:-1], clock[-1:]
    else:
        zone_index = next((i for i, char in enumerate(clock) if i and char in "+-"), None)
        if zone_index is not None:
            clock, zone_suffix = clock[:zone_index], clock[zone_index:]

    def normalize_component(component: str, *, local: bool) -> str | None:
        parsed = _ISO_CLOCK.fullmatch(component)
        if parsed is None:
            return component
        hour, separator, minute, second, fraction = parsed.groups()
        if local and hour == "24":
            return None
        if not local and hour == "00" and minute in (None, "00") and second in (None, "00"):
            # CPython 3.11–3.13 canonicalize a zero-hour offset to UTC even
            # when a fractional component follows it. Keep that wire meaning
            # on 3.14 too, matching the TypeScript codec's zero-offset rule.
            return "00:00:00"
        if fraction is None or second is not None:
            return component
        return f"{hour}:{minute or '00'}:00{fraction}"

    normalized_clock = normalize_component(clock, local=True)
    if normalized_clock is None:
        return None
    if zone_suffix.startswith(("+", "-")):
        normalized_offset = normalize_component(zone_suffix[1:], local=False)
        if normalized_offset is None:
            return text
        zone_suffix = zone_suffix[0] + normalized_offset

    return (
        match.group("date") + match.group("separator") + normalized_clock + zone_suffix
    )


def parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    stable_text = _stable_iso_timestamp_text(text)
    if stable_text is None:
        return None
    try:
        parsed = datetime.fromisoformat(
            stable_text.replace("Z", "+00:00").replace("z", "+00:00")
        )
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def chronology_key(value: Any) -> tuple[int, datetime, str]:
    """Order timestamps by UTC instant with deterministic legacy fallbacks."""

    raw = str(value or "")
    try:
        parsed = parse_timestamp(value)
    except OverflowError:
        # UTC conversion can overflow at datetime's representable boundaries.
        parsed = None
    if parsed is None:
        return (0, _MIN_TIMESTAMP, raw)
    return (1, parsed, raw)


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def now_utc_iso() -> str:
    return utc_isoformat(now_utc())


def now_local_iso() -> str:
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def utc_isoformat(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def utc_timestamp() -> str:
    return now_utc().strftime("%Y%m%dT%H%M%SZ")
