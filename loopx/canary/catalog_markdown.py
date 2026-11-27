"""Markdown catalog parsing helpers and canary report renderers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re


def mapping_list(payload: Mapping[str, object], key: str) -> list[Mapping[str, object]]:
    """Return nested mapping items under ``key``, ignoring malformed entries."""

    raw = payload.get(key)
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        return []
    return [item for item in raw if isinstance(item, Mapping)]


def string_list(payload: Mapping[str, object], key: str) -> list[str]:
    """Return stringified sequence elements under ``key``."""

    raw = payload.get(key)
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        return []
    return [str(item) for item in raw]


def slug(value: str) -> str:
    """Normalize a human label into a lowercase kebab-case identifier."""

    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def catalog_profile_id(profile: Mapping[str, object]) -> str:
    """Resolve the normalized identifier for a catalog or domain profile."""

    return slug(str(profile.get("id") or profile.get("family") or ""))


def _split_table_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _parse_markdown_table(lines: list[str]) -> list[dict[str, str]]:
    rows = [_split_table_row(line) for line in lines if line.strip().startswith("|")]
    if len(rows) < 3:
        return []
    headers = rows[0]
    parsed: list[dict[str, str]] = []
    for row in rows[2:]:
        if len(row) != len(headers):
            continue
        parsed.append({headers[index]: value for index, value in enumerate(row)})
    return parsed


def first_table_after(text: str, marker: str) -> list[dict[str, str]]:
    """Parse the first markdown table appearing at or after ``marker``."""

    index = text.find(marker)
    if index < 0:
        return []
    table_lines: list[str] = []
    in_table = False
    for line in text[index:].splitlines():
        if line.strip().startswith("|"):
            table_lines.append(line)
            in_table = True
        elif in_table:
            break
    return _parse_markdown_table(table_lines)


def pattern_ids(value: str) -> list[str]:
    """Extract sorted unique ``IP-NNN`` pattern identifiers from text."""

    return sorted(set(re.findall(r"\bIP-\d{3}\b", value)))


def split_semicolon(value: str) -> list[str]:
    """Split semicolon-delimited items and omit blank entries."""

    return [item.strip() for item in value.split(";") if item.strip()]


def catalog_pattern_rows(text: str) -> list[dict[str, str]]:
    """Extract interaction-pattern metadata rows from the catalog markdown."""

    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = _split_table_row(line)
        if len(cells) < 3:
            continue
        if cells[0] not in {"P0", "P1", "P2"} or not re.fullmatch(r"IP-\d{3}", cells[1]):
            continue
        rows.append(
            {
                "importance": cells[0],
                "pattern_id": cells[1],
                "name": cells[2],
            }
        )
    return rows


def coverage_exception_rows(text: str) -> dict[str, dict[str, str]]:
    """Parse documented canary coverage exceptions indexed by pattern ID."""

    table = first_table_after(text, "| Pattern ID | Canary Coverage Status |")
    exceptions: dict[str, dict[str, str]] = {}
    for row in table:
        pattern_id = next(iter(pattern_ids(row.get("Pattern ID", ""))), "")
        if not pattern_id:
            continue
        status = slug(row.get("Canary Coverage Status", ""))
        if status not in {"non-applicable", "not-applicable", "deferred"}:
            continue
        exceptions[pattern_id] = {
            "pattern_id": pattern_id,
            "status": "non-applicable" if status == "not-applicable" else status,
            "rationale": row.get("Rationale", ""),
            "owner": row.get("Owner", ""),
        }
    return exceptions


def coverage_exception_is_valid(exception: Mapping[str, str]) -> bool:
    """Return whether a coverage exception supplies required rationale/owner fields."""

    status = exception.get("status")
    rationale = bool(str(exception.get("rationale") or "").strip())
    owner = bool(str(exception.get("owner") or "").strip())
    if status == "non-applicable":
        return rationale
    if status == "deferred":
        return rationale and owner
    return False


def _format_archetypes(profile: Mapping[str, object]) -> str:
    return ", ".join(
        f"`{item.get('name')}`"
        for item in mapping_list(profile, "default_archetypes")
    )


def render_catalog_canary_profiles_markdown(payload: dict[str, object]) -> str:
    """Render catalog family and domain canary profiles as markdown."""

    lines = [
        "# Catalog Canary Profiles",
        "",
        f"- source: `{payload.get('source')}`",
        f"- archetypes: `{payload.get('archetype_count')}`",
        f"- profiles: `{payload.get('profile_count')}`",
        "- dry_run: `true`",
        "- executes_checks: `false`",
        "",
    ]
    for profile in mapping_list(payload, "profiles"):
        lines.extend(
            [
                f"## {profile.get('family')}",
                f"- patterns: `{', '.join(string_list(profile, 'pattern_ids'))}`",
                "- archetypes: " + _format_archetypes(profile),
                f"- trigger surfaces: {profile.get('trigger_surfaces')}",
                f"- minimum fixture: {profile.get('minimum_useful_fixture')}",
                f"- failure meaning: {profile.get('failure_meaning')}",
                "",
            ]
        )
    domain_profiles = mapping_list(payload, "domain_profiles")
    if domain_profiles:
        lines.extend(["## Current Repo Profiles", ""])
        for profile in domain_profiles:
            checks = mapping_list(profile, "checks")
            default_count = sum(1 for check in checks if check.get("tier") != "deep")
            deep_count = sum(1 for check in checks if check.get("tier") == "deep")
            lines.extend(
                [
                    f"### {profile.get('title')}",
                    f"- id: `{profile.get('id')}`",
                    f"- catalog families: `{', '.join(string_list(profile, 'catalog_families'))}`",
                    f"- purpose: {profile.get('purpose')}",
                    f"- default checks: `{default_count}`",
                    f"- deep checks: `{deep_count}`",
                    "",
                ]
            )
    return "\n".join(lines).rstrip() + "\n"


def render_catalog_canary_plan_markdown(payload: dict[str, object]) -> str:
    """Render a selected catalog canary plan as markdown."""

    inputs_value = payload.get("selection_inputs")
    inputs: Mapping[str, object] = (
        inputs_value if isinstance(inputs_value, Mapping) else {}
    )
    lines = [
        "# Catalog Canary Plan",
        "",
        f"- source: `{payload.get('source')}`",
        "- dry_run: `true`",
        "- executes_checks: `false`",
        f"- selected_profiles: `{payload.get('profile_count')}`",
        f"- selected_domain_profiles: `{payload.get('domain_profile_count')}`",
        f"- changed_files: `{', '.join(string_list(inputs, 'changed_files'))}`",
        f"- surfaces: `{', '.join(string_list(inputs, 'surfaces'))}`",
        f"- families: `{', '.join(string_list(inputs, 'families'))}`",
        f"- profiles: `{', '.join(string_list(inputs, 'profiles'))}`",
        f"- include_deep_checks: `{str(inputs.get('include_deep_checks')).lower()}`",
        "",
        str(payload.get("note") or ""),
        "",
    ]
    for profile in mapping_list(payload, "profiles"):
        lines.extend(
            [
                f"## {profile.get('family')}",
                f"- patterns: `{', '.join(string_list(profile, 'pattern_ids'))}`",
                "- selected because: "
                + "; ".join(string_list(profile, "selection_reasons")),
                "- archetypes: " + _format_archetypes(profile),
                f"- minimum fixture: {profile.get('minimum_useful_fixture')}",
                f"- failure meaning: {profile.get('failure_meaning')}",
                "- suggested checks:",
            ]
        )
        for check in mapping_list(profile, "candidate_checks"):
            lines.append(f"  - `{check.get('command')}` - {check.get('reason')}")
        lines.append("")
    for profile in mapping_list(payload, "domain_profiles"):
        lines.extend(
            [
                f"## {profile.get('title')}",
                f"- id: `{profile.get('id')}`",
                f"- catalog families: `{', '.join(string_list(profile, 'catalog_families'))}`",
                "- selected because: "
                + "; ".join(string_list(profile, "selection_reasons")),
                f"- purpose: {profile.get('purpose')}",
                f"- deep checks available: `{str(profile.get('deep_checks_available')).lower()}`",
                f"- deep checks included: `{str(profile.get('deep_checks_included')).lower()}`",
                "- suggested checks:",
            ]
        )
        for check in mapping_list(profile, "checks"):
            lines.append(
                f"  - `{check.get('command')}` [{check.get('tier')}] - {check.get('reason')}"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_catalog_canary_coverage_audit_markdown(payload: dict[str, object]) -> str:
    """Render catalog pattern canary coverage audit results as markdown."""

    lines = [
        "# Catalog Canary Coverage Audit",
        "",
        f"- source: `{payload.get('source')}`",
        "- dry_run: `true`",
        "- executes_checks: `false`",
        f"- priorities: `{', '.join(string_list(payload, 'priorities'))}`",
        f"- required_patterns: `{payload.get('required_pattern_count')}`",
        f"- covered: `{payload.get('covered_count')}`",
        f"- excepted: `{payload.get('excepted_count')}`",
        f"- missing: `{payload.get('missing_count')}`",
        f"- invalid_exceptions: `{payload.get('invalid_exception_count')}`",
        f"- drift: `{payload.get('drift_count')}`",
        "",
        str(payload.get("note") or ""),
        "",
    ]
    missing_patterns = mapping_list(payload, "missing_patterns")
    if missing_patterns:
        lines.extend(["## Missing Coverage", ""])
        for row in missing_patterns:
            lines.append(
                f"- `{row.get('pattern_id')}` {row.get('name')} ({row.get('importance')})"
            )
        lines.append("")
    invalid_exceptions = mapping_list(payload, "invalid_exceptions")
    if invalid_exceptions:
        lines.extend(["## Invalid Exceptions", ""])
        for row in invalid_exceptions:
            lines.append(
                f"- `{row.get('pattern_id')}` {row.get('status')}: "
                f"rationale={row.get('rationale')!r}; owner={row.get('owner')!r}"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
