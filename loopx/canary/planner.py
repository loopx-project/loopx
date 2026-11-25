"""Catalog-driven canary profile planning and coverage auditing."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import re

from .catalog_markdown import (
    catalog_pattern_rows,
    catalog_profile_id,
    coverage_exception_is_valid,
    coverage_exception_rows,
    first_table_after,
    mapping_list,
    pattern_ids,
    render_catalog_canary_coverage_audit_markdown,
    render_catalog_canary_plan_markdown,
    render_catalog_canary_profiles_markdown,
    slug,
    split_semicolon,
    string_list,
)
from .core_domain_profiles import CORE_DOMAIN_PROFILES
from .family_profiles import FAMILY_CHECKS, FAMILY_SELECTOR_HINTS
from .quality_surface_catalog import (
    build_quality_surface_catalog_audit as _build_quality_surface_catalog_audit,
)
from .workflow_domain_profiles import WORKFLOW_DOMAIN_PROFILES


CANARY_PROFILE_SCHEMA_VERSION = "catalog_canary_profile_v0"
CANARY_DOMAIN_PROFILE_SCHEMA_VERSION = "catalog_canary_domain_profile_v0"
CANARY_PROFILES_SCHEMA_VERSION = "catalog_canary_profiles_v0"
CANARY_PLAN_SCHEMA_VERSION = "catalog_canary_plan_v0"
CANARY_COVERAGE_AUDIT_SCHEMA_VERSION = "catalog_canary_coverage_audit_v0"

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG_PATH = REPO_ROOT / "docs" / "concepts" / "interaction-pattern-catalog.md"

CURRENT_REPO_PROFILES: tuple[Mapping[str, object], ...] = (
    *CORE_DOMAIN_PROFILES,
    *WORKFLOW_DOMAIN_PROFILES,
)

__all__ = [
    "CANARY_PROFILE_SCHEMA_VERSION",
    "CANARY_DOMAIN_PROFILE_SCHEMA_VERSION",
    "CANARY_PROFILES_SCHEMA_VERSION",
    "CANARY_PLAN_SCHEMA_VERSION",
    "CANARY_COVERAGE_AUDIT_SCHEMA_VERSION",
    "REPO_ROOT",
    "DEFAULT_CATALOG_PATH",
    "FAMILY_CHECKS",
    "FAMILY_SELECTOR_HINTS",
    "CURRENT_REPO_PROFILES",
    "build_quality_surface_catalog_audit",
    "resolve_canary_catalog_path",
    "build_catalog_canary_profiles",
    "build_catalog_canary_coverage_audit",
    "flatten_catalog_canary_checks",
    "build_catalog_canary_plan",
    "render_catalog_canary_profiles_markdown",
    "render_catalog_canary_plan_markdown",
    "render_catalog_canary_coverage_audit_markdown",
]


def _quality_audit_source_root(candidate: Path | None) -> Path | None:
    source_root = (candidate or REPO_ROOT).resolve()
    required_paths = (
        source_root / "pyproject.toml",
        source_root / "loopx" / "canary" / "quality_surface_catalog.py",
        source_root / "tests" / "canary" / "test_quality_surface_catalog.py",
    )
    return source_root if all(path.is_file() for path in required_paths) else None


def build_quality_surface_catalog_audit(
    *,
    repo_root: Path | None = None,
) -> dict[str, object]:
    """Audit quality-layer ownership for every high-risk canary profile."""

    raw_audit = _build_quality_surface_catalog_audit(
        CURRENT_REPO_PROFILES,
        repo_root=_quality_audit_source_root(repo_root),
    )
    audit: dict[str, object] = {}
    for key, value in raw_audit.items():
        audit[str(key)] = value
    return audit


def resolve_canary_catalog_path(
    catalog_path: Path | None = None,
    *,
    repo_root: Path | None = None,
) -> Path:
    """Resolve development inputs without treating an installed wheel as a checkout."""

    path = (
        catalog_path
        or (
            repo_root / "docs/concepts/interaction-pattern-catalog.md"
            if repo_root is not None
            else DEFAULT_CATALOG_PATH
        )
    ).expanduser()
    if not path.is_file():
        raise ValueError(
            f"canary catalog is missing: {path}; run from a complete LoopX source "
            "checkout or supply --catalog PATH. The wheel does not bundle development inputs."
        )
    return path


def _read_catalog(catalog_path: Path | None = None) -> tuple[Path, str]:
    path = resolve_canary_catalog_path(catalog_path)
    return path, path.read_text(encoding="utf-8")


def _display_path(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def build_catalog_canary_profiles(catalog_path: Path | None = None) -> dict[str, object]:
    """Load canary family and domain profiles from the interaction pattern catalog."""

    path, text = _read_catalog(catalog_path)
    archetype_rows = first_table_after(text, "| Canary Archetype |")
    family_rows = first_table_after(text, "| Family |")

    archetypes: list[dict[str, str]] = []
    for row in archetype_rows:
        name = row.get("Canary Archetype", "")
        archetypes.append(
            {
                "id": slug(name),
                "name": name,
                "primary_question": row.get("Primary Question", ""),
                "trigger_surface": row.get("Typical Trigger Surface", ""),
                "fixture_depth": row.get("Fixture Depth", ""),
                "cost_tier": row.get("Cost Tier", ""),
                "failure_meaning": row.get("Failure Usually Means", ""),
            }
        )

    profiles: list[dict[str, object]] = []
    for row in family_rows:
        family = row.get("Family", "")
        archetype_names = split_semicolon(row.get("Default Canary Archetypes", ""))
        profiles.append(
            {
                "schema_version": CANARY_PROFILE_SCHEMA_VERSION,
                "id": slug(family),
                "family": family,
                "pattern_ids": pattern_ids(row.get("P0/P1 Pattern Coverage", "")),
                "default_archetypes": [
                    {
                        "id": slug(name),
                        "name": name,
                    }
                    for name in archetype_names
                ],
                "trigger_surfaces": row.get("Trigger Surfaces", ""),
                "minimum_useful_fixture": row.get("Minimum Useful Fixture", ""),
                "failure_meaning": row.get("Failure Meaning", ""),
                "candidate_checks": FAMILY_CHECKS.get(family, []),
            }
        )

    domain_profiles: list[dict[str, object]] = []
    for profile in CURRENT_REPO_PROFILES:
        domain_profiles.append(
            {
                "schema_version": CANARY_DOMAIN_PROFILE_SCHEMA_VERSION,
                **profile,
                "checks": [dict(check) for check in mapping_list(profile, "checks")],
            }
        )

    return {
        "ok": bool(archetypes and profiles),
        "schema_version": CANARY_PROFILES_SCHEMA_VERSION,
        "source": _display_path(path),
        "dry_run": True,
        "executes_checks": False,
        "archetype_count": len(archetypes),
        "profile_count": len(profiles),
        "domain_profile_count": len(domain_profiles),
        "archetypes": archetypes,
        "profiles": profiles,
        "domain_profiles": domain_profiles,
    }


def build_catalog_canary_coverage_audit(
    *,
    catalog_path: Path | None = None,
    priorities: list[str] | None = None,
) -> dict[str, object]:
    """Audit catalog interaction-pattern coverage against canary profiles and exceptions."""

    path, text = _read_catalog(catalog_path)
    profile_packet = build_catalog_canary_profiles(catalog_path)
    required_priorities = tuple(priorities or ["P0", "P1"])
    pattern_rows = [
        row
        for row in catalog_pattern_rows(text)
        if row.get("importance") in required_priorities
    ]
    profile_coverage: dict[str, list[str]] = {}
    for profile in mapping_list(profile_packet, "profiles"):
        profile_id = str(profile.get("id") or "")
        for pattern_id in string_list(profile, "pattern_ids"):
            profile_coverage.setdefault(pattern_id, []).append(profile_id)
    exceptions = coverage_exception_rows(text)

    covered: list[dict[str, object]] = []
    excepted: list[dict[str, object]] = []
    missing: list[dict[str, object]] = []
    invalid_exceptions: list[dict[str, object]] = []
    for row in pattern_rows:
        pattern_id = row["pattern_id"]
        profile_ids = profile_coverage.get(pattern_id, [])
        if profile_ids:
            covered.append({**row, "profile_ids": profile_ids})
            continue
        exception = exceptions.get(pattern_id)
        if exception and coverage_exception_is_valid(exception):
            excepted.append({**row, **exception})
            continue
        if exception:
            invalid_exceptions.append({**row, **exception})
        else:
            missing.append(dict(row))

    drift_count = len(missing) + len(invalid_exceptions)
    return {
        "ok": bool(profile_packet.get("ok")) and drift_count == 0,
        "schema_version": CANARY_COVERAGE_AUDIT_SCHEMA_VERSION,
        "source": _display_path(path),
        "dry_run": True,
        "executes_checks": False,
        "priorities": list(required_priorities),
        "required_pattern_count": len(pattern_rows),
        "covered_count": len(covered),
        "excepted_count": len(excepted),
        "missing_count": len(missing),
        "invalid_exception_count": len(invalid_exceptions),
        "drift_count": drift_count,
        "covered_patterns": covered,
        "excepted_patterns": excepted,
        "missing_patterns": missing,
        "invalid_exceptions": invalid_exceptions,
        "note": (
            "Coverage means each selected catalog pattern is listed in a canary "
            "family profile, or has an explicit non-applicable rationale or "
            "deferred owner in a catalog coverage exception table. This audit "
            "reports drift only; it does not force one giant canary or execute checks."
        ),
    }


def _selector_blob(changed_files: list[str], surfaces: list[str]) -> str:
    return "\n".join([*changed_files, *surfaces]).lower()


def _selection_reasons(profile: Mapping[str, object], selector_blob: str) -> list[str]:
    family = str(profile.get("family") or "")
    family_lc = family.lower()
    reasons: list[str] = []
    if family_lc and family_lc in selector_blob:
        reasons.append(f"selector names catalog family `{family}`")
    for hint in FAMILY_SELECTOR_HINTS.get(family, ()):
        if hint.lower() in selector_blob:
            reasons.append(f"selector matches `{hint}`")
    trigger_surfaces = str(profile.get("trigger_surfaces") or "").lower()
    for token in re.findall(r"[a-z][a-z0-9_-]{2,}", selector_blob):
        if token in trigger_surfaces and f"trigger surface mentions `{token}`" not in reasons:
            reasons.append(f"trigger surface mentions `{token}`")
    return reasons


def _domain_selection_reasons(
    profile: Mapping[str, object], selector_blob: str,
) -> list[str]:
    reasons: list[str] = []
    profile_id = str(profile.get("id") or "")
    title = str(profile.get("title") or "")
    if profile_id and profile_id in selector_blob:
        reasons.append(f"selector names profile `{profile_id}`")
    if title and title.lower() in selector_blob:
        reasons.append(f"selector names profile `{title}`")
    for hint in string_list(profile, "trigger_hints"):
        hint_text = hint.lower()
        if hint_text and hint_text in selector_blob:
            reasons.append(f"selector matches `{hint}`")
    for family in string_list(profile, "catalog_families"):
        family_text = family.lower()
        if family_text and family_text in selector_blob:
            reasons.append(f"selector matches family `{family}`")
    return reasons


def _domain_profile_with_checks(
    profile: Mapping[str, object],
    *,
    include_deep_checks: bool,
    max_checks: int,
) -> dict[str, object]:
    raw_checks = mapping_list(profile, "checks")
    checks = [
        dict(check)
        for check in raw_checks
        if include_deep_checks or check.get("tier") != "deep"
    ]
    copied = dict(profile)
    copied["checks"] = checks[: max(1, max_checks)]
    copied["deep_checks_available"] = any(
        check.get("tier") == "deep" for check in raw_checks
    )
    copied["deep_checks_included"] = bool(include_deep_checks)
    return copied


def flatten_catalog_canary_checks(
    plan: Mapping[str, object],
) -> list[dict[str, object]]:
    """Return selected canary checks in execution order."""

    checks: list[dict[str, object]] = []
    seen: set[str] = set()
    sources = (
        ("domain_profile", "domain_profiles", "checks", "title"),
        ("catalog_family", "profiles", "candidate_checks", "family"),
    )
    for source_name, profiles_key, checks_key, title_key in sources:
        for profile in mapping_list(plan, profiles_key):
            for check in mapping_list(profile, checks_key):
                command = str(check.get("command") or "")
                if not command or command in seen:
                    continue
                seen.add(command)
                checks.append(
                    {
                        "source": source_name,
                        "profile_id": profile.get("id"),
                        "profile_title": profile.get(title_key),
                        "tier": check.get("tier") or "default",
                        **check,
                    }
                )
    return checks


def _select_catalog_profiles(
    profiles: list[Mapping[str, object]],
    *,
    selector_blob: str,
    requested_families: set[str],
    requested_catalog_profiles: set[str],
    requested_domain_profiles: set[str],
    max_checks: int,
) -> list[dict[str, object]]:
    selected: list[dict[str, object]] = []
    for profile in profiles:
        profile_id = catalog_profile_id(profile)
        if (
            requested_domain_profiles
            and not requested_catalog_profiles
            and not requested_families
            and not selector_blob
        ):
            continue
        reasons = _selection_reasons(profile, selector_blob)
        if requested_catalog_profiles and profile_id not in requested_catalog_profiles:
            continue
        if requested_families and slug(str(profile.get("family") or "")) not in requested_families:
            continue
        if not requested_catalog_profiles and not requested_families and selector_blob and not reasons:
            continue
        profile_copy = dict(profile)
        profile_copy["candidate_checks"] = [
            dict(check) for check in mapping_list(profile_copy, "candidate_checks")
        ][:max_checks]
        if requested_catalog_profiles and profile_id in requested_catalog_profiles:
            profile_copy["selection_reasons"] = reasons or [
                "selected because this catalog profile was explicitly requested",
            ]
        else:
            profile_copy["selection_reasons"] = reasons or [
                "selected because no narrower selector was provided",
            ]
        selected.append(profile_copy)
    return selected


def _select_domain_profiles(
    profiles: list[Mapping[str, object]],
    *,
    selector_blob: str,
    requested_catalog_profiles: set[str],
    requested_domain_profiles: set[str],
    include_deep_checks: bool,
    max_profile_checks: int,
) -> list[dict[str, object]]:
    selected: list[dict[str, object]] = []
    for profile in profiles:
        reasons = _domain_selection_reasons(profile, selector_blob)
        if (
            requested_domain_profiles
            and slug(str(profile.get("id") or "")) not in requested_domain_profiles
        ):
            continue
        if requested_catalog_profiles and not requested_domain_profiles:
            continue
        if not requested_domain_profiles and selector_blob and not reasons:
            continue
        if not requested_domain_profiles and not selector_blob:
            continue
        profile_copy = _domain_profile_with_checks(
            profile,
            include_deep_checks=include_deep_checks,
            max_checks=max_profile_checks,
        )
        profile_copy["selection_reasons"] = reasons or [
            "selected because this profile was explicitly requested",
        ]
        selected.append(profile_copy)
    return selected


def build_catalog_canary_plan(
    *,
    catalog_path: Path | None = None,
    changed_files: list[str] | None = None,
    surfaces: list[str] | None = None,
    families: list[str] | None = None,
    profiles: list[str] | None = None,
    include_deep_checks: bool = False,
    max_checks_per_family: int = 3,
    max_checks_per_profile: int = 3,
) -> dict[str, object]:
    """Select catalog family and repo domain canary checks for a scope or diff."""

    packet = build_catalog_canary_profiles(catalog_path)
    changed_files = changed_files or []
    surfaces = surfaces or []
    requested_families = {slug(family) for family in (families or []) if family.strip()}
    requested_profiles = {slug(profile) for profile in (profiles or []) if profile.strip()}
    selector_blob = _selector_blob(changed_files, surfaces)
    max_checks = max(1, max_checks_per_family)
    max_profile_checks = max(1, max_checks_per_profile)
    catalog_profiles = mapping_list(packet, "profiles")
    catalog_profile_ids = {catalog_profile_id(profile) for profile in catalog_profiles}
    requested_catalog_profiles = requested_profiles & catalog_profile_ids
    requested_domain_profiles = requested_profiles - requested_catalog_profiles

    selected_profiles = _select_catalog_profiles(
        catalog_profiles,
        selector_blob=selector_blob,
        requested_families=requested_families,
        requested_catalog_profiles=requested_catalog_profiles,
        requested_domain_profiles=requested_domain_profiles,
        max_checks=max_checks,
    )
    selected_domain_profiles = _select_domain_profiles(
        mapping_list(packet, "domain_profiles"),
        selector_blob=selector_blob,
        requested_catalog_profiles=requested_catalog_profiles,
        requested_domain_profiles=requested_domain_profiles,
        include_deep_checks=include_deep_checks,
        max_profile_checks=max_profile_checks,
    )

    payload: dict[str, object] = {
        "ok": True,
        "schema_version": CANARY_PLAN_SCHEMA_VERSION,
        "source": packet["source"],
        "dry_run": True,
        "executes_checks": False,
        "selection_inputs": {
            "changed_files": changed_files,
            "surfaces": surfaces,
            "families": families or [],
            "profiles": profiles or [],
            "include_deep_checks": include_deep_checks,
            "max_checks_per_family": max_checks,
            "max_checks_per_profile": max_profile_checks,
        },
        "profile_count": len(selected_profiles),
        "domain_profile_count": len(selected_domain_profiles),
        "profiles": selected_profiles,
        "domain_profiles": selected_domain_profiles,
        "note": (
            "This planner only selects and explains candidate canary checks. "
            "It does not execute smoke tests or create new runtime contracts. "
            "Plan from existing public runtime/status contracts first; when a "
            "new runtime contract seems necessary, stop at an owner-review "
            "necessity/risk packet before implementation."
        ),
    }
    suggested_checks = flatten_catalog_canary_checks(payload)
    payload["suggested_check_count"] = len(suggested_checks)
    payload["suggested_checks"] = suggested_checks
    payload["commands"] = [str(check.get("command") or "") for check in suggested_checks]
    return payload
