from __future__ import annotations

import ast
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from ..content_digest import ENVELOPED_SHA256_PATTERN
from ..runtime.public_safety import public_safe_compact_text
from .actual_default_model_behavior_portfolio import (
    ACTUAL_DEFAULT_MODEL_BEHAVIOR_CONTRAST_COUNT,
    ACTUAL_DEFAULT_MODEL_BEHAVIOR_REPEAT_ATTEMPTS,
    ACTUAL_DEFAULT_MODEL_BEHAVIOR_SCENARIO_COUNT,
)


EXACT_RELEASE_COMMIT_MANIFEST_SCHEMA_VERSION = (
    "exact_release_commit_qualification_manifest_v0"
)
EXACT_RELEASE_COMMIT_RECEIPT_SCHEMA_VERSION = (
    "exact_release_commit_qualification_receipt_v0"
)
EXACT_RELEASE_CHECK_RECEIPT_SCHEMA_VERSION = "exact_release_check_receipt_v0"

REQUIRED_QUALIFICATION_IDS = (
    "pytest",
    "ruff",
    "mypy",
    "risk_canary",
    "full_public",
    "install_upgrade_host",
    "public_boundary",
    "doubao_actual_default",
)
EXPECTED_RESULT_SCHEMA_BY_QUALIFICATION = {
    "pytest": "pytest_summary_v0",
    "ruff": "ruff_summary_v0",
    "mypy": "mypy_summary_v0",
    "risk_canary": "loopx_premerge_validation_gate_v0",
    "full_public": "smoke_fleet_health_v0",
    "install_upgrade_host": "release_install_upgrade_host_summary_v0",
    "public_boundary": "loopx_public_boundary_check_v0",
    "doubao_actual_default": "actual_default_model_behavior_portfolio_v0",
}

_HEX_ID_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[A-Za-z0-9.+-]*)?$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/+\-]{0,159}$")

_MANIFEST_FIELDS = {"schema_version", "candidate", "claims", "qualifications"}
_SOURCE_FIELDS = {
    "git_commit",
    "git_tree",
    "git_dirty",
    "package_version",
    "version_tag",
}
_CLAIM_FIELDS: set[str] = set()
_CHECK_FIELDS = {
    "schema_version",
    "source",
    "status",
    "result_schema_version",
    "result_digest",
    "completed_at",
    "summary",
}


def _exact_fields(value: Mapping[str, Any], allowed: set[str], *, field: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"{field} contains unknown fields: {sorted(unknown)}")


def _token(value: Any, *, field: str) -> str:
    text = public_safe_compact_text(value, limit=160)
    if text is None or not _TOKEN_RE.fullmatch(text):
        raise ValueError(f"{field} must be a compact public-safe token")
    return text


def _hex_id(value: Any, *, field: str) -> str:
    text = str(value or "").strip().lower()
    if not _HEX_ID_RE.fullmatch(text):
        raise ValueError(f"{field} must be a 40- or 64-character lowercase Git id")
    return text


def _digest(value: Any, *, field: str) -> str:
    text = str(value or "").strip().lower()
    if not ENVELOPED_SHA256_PATTERN.fullmatch(text):
        raise ValueError(f"{field} must be a sha256 digest")
    return text


def _non_negative_int(value: Any, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _positive_int(value: Any, *, field: str) -> int:
    normalized = _non_negative_int(value, field=field)
    if normalized < 1:
        raise ValueError(f"{field} must be positive")
    return normalized


def _boolean(value: Any, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be boolean")
    return value


def _completed_at(value: Any) -> str:
    text = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("qualification.completed_at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("qualification.completed_at must include a timezone")
    return text


def _source_identity(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    _exact_fields(value, _SOURCE_FIELDS, field=field)
    version = str(value.get("package_version") or "").strip()
    if not _VERSION_RE.fullmatch(version):
        raise ValueError(f"{field}.package_version must be a package version")
    version_tag = str(value.get("version_tag") or "").strip()
    if version_tag != f"v{version}":
        raise ValueError(f"{field}.version_tag must match package_version")
    dirty = _boolean(value.get("git_dirty"), field=f"{field}.git_dirty")
    return {
        "git_commit": _hex_id(value.get("git_commit"), field=f"{field}.git_commit"),
        "git_tree": _hex_id(value.get("git_tree"), field=f"{field}.git_tree"),
        "git_dirty": dirty,
        "package_version": version,
        "version_tag": version_tag,
    }


def _summary(
    value: Any,
    *,
    qualification_id: str,
    allowed: set[str],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"qualifications.{qualification_id}.summary must be an object")
    _exact_fields(value, allowed, field=f"qualifications.{qualification_id}.summary")
    return dict(value)


def _validate_pytest(value: Any) -> tuple[dict[str, Any], list[str]]:
    summary = _summary(
        value,
        qualification_id="pytest",
        allowed={"passed_count", "failed_count", "skipped_count"},
    )
    passed = _positive_int(summary.get("passed_count"), field="pytest.passed_count")
    failed = _non_negative_int(summary.get("failed_count"), field="pytest.failed_count")
    skipped = _non_negative_int(summary.get("skipped_count"), field="pytest.skipped_count")
    return (
        {"passed_count": passed, "failed_count": failed, "skipped_count": skipped},
        ["pytest_failed"] if failed else [],
    )


def _zero_count_summary(
    value: Any,
    *,
    qualification_id: str,
    count_field: str,
) -> tuple[dict[str, Any], list[str]]:
    summary = _summary(
        value,
        qualification_id=qualification_id,
        allowed={count_field},
    )
    count = _non_negative_int(summary.get(count_field), field=f"{qualification_id}.{count_field}")
    return {count_field: count}, [f"{qualification_id}_failed"] if count else []


def _validate_risk_canary(value: Any) -> tuple[dict[str, Any], list[str]]:
    summary = _summary(
        value,
        qualification_id="risk_canary",
        allowed={"selected_check_count", "failure_count", "manual_hold_count"},
    )
    selected = _positive_int(
        summary.get("selected_check_count"), field="risk_canary.selected_check_count"
    )
    failures = _non_negative_int(
        summary.get("failure_count"), field="risk_canary.failure_count"
    )
    holds = _non_negative_int(
        summary.get("manual_hold_count"), field="risk_canary.manual_hold_count"
    )
    issues = []
    if failures:
        issues.append("risk_canary_failed")
    if holds:
        issues.append("risk_canary_manual_hold")
    return {
        "selected_check_count": selected,
        "failure_count": failures,
        "manual_hold_count": holds,
    }, issues


def _validate_full_public(value: Any) -> tuple[dict[str, Any], list[str]]:
    summary = _summary(
        value,
        qualification_id="full_public",
        allowed={"ready", "shard_count", "failure_count", "timeout_count"},
    )
    ready = _boolean(summary.get("ready"), field="full_public.ready")
    shards = _positive_int(summary.get("shard_count"), field="full_public.shard_count")
    failures = _non_negative_int(
        summary.get("failure_count"), field="full_public.failure_count"
    )
    timeouts = _non_negative_int(
        summary.get("timeout_count"), field="full_public.timeout_count"
    )
    issues = [] if ready and failures == 0 and timeouts == 0 else ["full_public_failed"]
    return {
        "ready": ready,
        "shard_count": shards,
        "failure_count": failures,
        "timeout_count": timeouts,
    }, issues


def _validate_install_upgrade_host(value: Any) -> tuple[dict[str, Any], list[str]]:
    summary = _summary(
        value,
        qualification_id="install_upgrade_host",
        allowed={"install_passed", "upgrade_passed", "host_passed"},
    )
    normalized = {
        field: _boolean(summary.get(field), field=f"install_upgrade_host.{field}")
        for field in ("install_passed", "upgrade_passed", "host_passed")
    }
    issues = [] if all(normalized.values()) else ["install_upgrade_host_failed"]
    return normalized, issues


def _validate_public_boundary(value: Any) -> tuple[dict[str, Any], list[str]]:
    summary = _summary(
        value,
        qualification_id="public_boundary",
        allowed={"scanned_path_count", "violation_count"},
    )
    scanned = _positive_int(
        summary.get("scanned_path_count"), field="public_boundary.scanned_path_count"
    )
    violations = _non_negative_int(
        summary.get("violation_count"), field="public_boundary.violation_count"
    )
    return {
        "scanned_path_count": scanned,
        "violation_count": violations,
    }, ["public_boundary_failed"] if violations else []


def _validate_doubao(value: Any) -> tuple[dict[str, Any], list[str]]:
    summary = _summary(
        value,
        qualification_id="doubao_actual_default",
        allowed={
            "model_id",
            "topology",
            "scenario_count",
            "contrast_count",
            "contrast_failure_count",
            "repeats_per_scenario",
            "actor_call_count",
            "failure_count",
            "skip_count",
            "qualification_passed",
        },
    )
    model_id = _token(summary.get("model_id"), field="doubao_actual_default.model_id")
    topology = _token(summary.get("topology"), field="doubao_actual_default.topology")
    scenario_count = _positive_int(
        summary.get("scenario_count"), field="doubao_actual_default.scenario_count"
    )
    contrast_count = _positive_int(
        summary.get("contrast_count"), field="doubao_actual_default.contrast_count"
    )
    contrast_failures = _non_negative_int(
        summary.get("contrast_failure_count"),
        field="doubao_actual_default.contrast_failure_count",
    )
    repeats = _positive_int(
        summary.get("repeats_per_scenario"),
        field="doubao_actual_default.repeats_per_scenario",
    )
    calls = _non_negative_int(
        summary.get("actor_call_count"), field="doubao_actual_default.actor_call_count"
    )
    failures = _non_negative_int(
        summary.get("failure_count"), field="doubao_actual_default.failure_count"
    )
    skips = _non_negative_int(
        summary.get("skip_count"), field="doubao_actual_default.skip_count"
    )
    passed = _boolean(
        summary.get("qualification_passed"),
        field="doubao_actual_default.qualification_passed",
    )
    valid = bool(
        topology == "actual_default_one_arm"
        and scenario_count == ACTUAL_DEFAULT_MODEL_BEHAVIOR_SCENARIO_COUNT
        and contrast_count == ACTUAL_DEFAULT_MODEL_BEHAVIOR_CONTRAST_COUNT
        and contrast_failures == 0
        and repeats == ACTUAL_DEFAULT_MODEL_BEHAVIOR_REPEAT_ATTEMPTS
        and calls == scenario_count * repeats
        and failures == 0
        and skips == 0
        and passed
    )
    normalized = {
        "model_id": model_id,
        "topology": topology,
        "scenario_count": scenario_count,
        "contrast_count": contrast_count,
        "contrast_failure_count": contrast_failures,
        "repeats_per_scenario": repeats,
        "actor_call_count": calls,
        "failure_count": failures,
        "skip_count": skips,
        "qualification_passed": passed,
    }
    return normalized, [] if valid else ["doubao_actual_default_failed"]


_SummaryValidator = Callable[[Any], tuple[dict[str, Any], list[str]]]


def _validator(qualification_id: str) -> _SummaryValidator:
    validators: dict[str, _SummaryValidator] = {
        "pytest": _validate_pytest,
        "ruff": lambda value: _zero_count_summary(
            value, qualification_id="ruff", count_field="violation_count"
        ),
        "mypy": lambda value: _zero_count_summary(
            value, qualification_id="mypy", count_field="error_count"
        ),
        "risk_canary": _validate_risk_canary,
        "full_public": _validate_full_public,
        "install_upgrade_host": _validate_install_upgrade_host,
        "public_boundary": _validate_public_boundary,
        "doubao_actual_default": _validate_doubao,
    }
    return validators[qualification_id]


def _normalize_check(
    qualification_id: str,
    value: Any,
    *,
    candidate: dict[str, Any],
) -> tuple[dict[str, Any], list[str], list[str]]:
    if not isinstance(value, Mapping):
        raise ValueError(f"qualifications.{qualification_id} must be an object")
    _exact_fields(value, _CHECK_FIELDS, field=f"qualifications.{qualification_id}")
    if value.get("schema_version") != EXACT_RELEASE_CHECK_RECEIPT_SCHEMA_VERSION:
        raise ValueError(
            f"qualifications.{qualification_id} must use {EXACT_RELEASE_CHECK_RECEIPT_SCHEMA_VERSION}"
        )
    source = _source_identity(value.get("source"), field=f"qualifications.{qualification_id}.source")
    status = str(value.get("status") or "").strip()
    if status not in {"passed", "failed", "skipped"}:
        raise ValueError(f"qualifications.{qualification_id}.status is invalid")
    result_schema = _token(
        value.get("result_schema_version"),
        field=f"qualifications.{qualification_id}.result_schema_version",
    )
    expected_result_schema = EXPECTED_RESULT_SCHEMA_BY_QUALIFICATION[qualification_id]
    if result_schema != expected_result_schema:
        raise ValueError(
            f"qualifications.{qualification_id}.result_schema_version must be "
            f"{expected_result_schema}"
        )
    summary, summary_issues = _validator(qualification_id)(value.get("summary"))
    source_mismatches = []
    for field in _SOURCE_FIELDS:
        if source[field] != candidate[field]:
            source_mismatches.append(f"{qualification_id}:{field}")
    failures = list(summary_issues)
    if status != "passed":
        failures.append(f"{qualification_id}_{status}")
    return {
        "schema_version": EXACT_RELEASE_CHECK_RECEIPT_SCHEMA_VERSION,
        "source": source,
        "status": status,
        "result_schema_version": result_schema,
        "result_digest": _digest(
            value.get("result_digest"),
            field=f"qualifications.{qualification_id}.result_digest",
        ),
        "completed_at": _completed_at(value.get("completed_at")),
        "summary": summary,
    }, sorted(set(failures)), sorted(set(source_mismatches))


def build_exact_release_commit_qualification(
    manifest: Mapping[str, Any],
    *,
    observed_source: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate exact-source release evidence without executing checks or publishing."""

    if not isinstance(manifest, Mapping):
        raise ValueError("manifest must be an object")
    _exact_fields(manifest, _MANIFEST_FIELDS, field="manifest")
    if manifest.get("schema_version") != EXACT_RELEASE_COMMIT_MANIFEST_SCHEMA_VERSION:
        raise ValueError(
            f"manifest must use {EXACT_RELEASE_COMMIT_MANIFEST_SCHEMA_VERSION}"
        )
    candidate = _source_identity(manifest.get("candidate"), field="manifest.candidate")
    claims = manifest.get("claims")
    if not isinstance(claims, Mapping):
        raise ValueError("manifest.claims must be an object")
    _exact_fields(claims, _CLAIM_FIELDS, field="manifest.claims")
    raw_qualifications = manifest.get("qualifications")
    if not isinstance(raw_qualifications, Mapping):
        raise ValueError("manifest.qualifications must be an object")
    allowed_ids = set(REQUIRED_QUALIFICATION_IDS)
    unknown_ids = sorted(set(raw_qualifications) - allowed_ids)
    if unknown_ids:
        raise ValueError(f"manifest.qualifications contains unknown ids: {unknown_ids}")

    required_ids = list(REQUIRED_QUALIFICATION_IDS)
    missing = sorted(set(required_ids) - set(raw_qualifications))
    normalized_checks: dict[str, Any] = {}
    failures: list[str] = []
    source_mismatches: list[str] = []
    for qualification_id in sorted(raw_qualifications):
        normalized, check_failures, check_mismatches = _normalize_check(
            qualification_id,
            raw_qualifications[qualification_id],
            candidate=candidate,
        )
        normalized_checks[qualification_id] = normalized
        failures.extend(check_failures)
        source_mismatches.extend(check_mismatches)

    observed = None
    if observed_source is not None:
        observed = _source_identity(observed_source, field="observed_source")
        for field in _SOURCE_FIELDS:
            if observed[field] != candidate[field]:
                source_mismatches.append(f"observed_source:{field}")
    if candidate["git_dirty"]:
        source_mismatches.append("candidate:git_dirty")

    ready = not missing and not failures and not source_mismatches
    if source_mismatches:
        decision = "hold_source_mismatch"
    elif missing:
        decision = "hold_missing_qualification"
    elif failures:
        decision = "hold_failed_qualification"
    else:
        decision = "ready_for_owner_release"
    return {
        "schema_version": EXACT_RELEASE_COMMIT_RECEIPT_SCHEMA_VERSION,
        "ready_for_release": ready,
        "decision": decision,
        "automatic_release_promotion_allowed": False,
        "candidate": candidate,
        "observed_source": observed,
        "claims": {},
        "required_qualification_ids": required_ids,
        "completed_qualification_ids": sorted(normalized_checks),
        "missing_qualification_ids": missing,
        "qualification_failures": sorted(set(failures)),
        "source_mismatches": sorted(set(source_mismatches)),
        "qualifications": normalized_checks,
        "read_boundary": {
            "manifest_only": True,
            "checks_executed": False,
            "model_api_invoked": False,
            "release_mutation_invoked": False,
            "raw_logs_retained": False,
            "raw_model_material_retained": False,
            "local_paths_recorded": False,
        },
    }


def _git(repo_root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        check=False,
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )
    if completed.returncode != 0:
        raise ValueError("release source identity is unavailable from Git")
    return completed.stdout.strip()


def _checkout_package_version(repo_root: Path) -> str:
    try:
        init_module = ast.parse(
            (repo_root / "loopx" / "__init__.py").read_text(encoding="utf-8")
        )
    except (OSError, SyntaxError) as exc:
        raise ValueError("release package version is unavailable from the checkout") from exc

    module_version = None
    for statement in init_module.body:
        if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
            continue
        target = statement.targets[0]
        if (
            isinstance(target, ast.Name)
            and target.id == "__version__"
            and isinstance(statement.value, ast.Constant)
            and isinstance(statement.value.value, str)
        ):
            module_version = statement.value.value
            break
    if not isinstance(module_version, str) or not _VERSION_RE.fullmatch(module_version):
        raise ValueError("release package version is invalid in the checkout")
    return module_version


def collect_release_source_identity(repo_root: Path) -> dict[str, Any]:
    root = repo_root.expanduser().resolve()
    status = _git(root, "status", "--porcelain")
    package_version = _checkout_package_version(root)
    return {
        "git_commit": _hex_id(_git(root, "rev-parse", "HEAD"), field="git_commit"),
        "git_tree": _hex_id(_git(root, "rev-parse", "HEAD^{tree}"), field="git_tree"),
        "git_dirty": bool(status),
        "package_version": package_version,
        "version_tag": f"v{package_version}",
    }


def render_exact_release_commit_qualification_markdown(payload: dict[str, Any]) -> str:
    candidate = payload.get("candidate") if isinstance(payload.get("candidate"), dict) else {}
    lines = [
        "# Exact Release Commit Qualification",
        "",
        f"- ready_for_release: `{payload.get('ready_for_release')}`",
        f"- decision: `{payload.get('decision')}`",
        f"- git_commit: `{candidate.get('git_commit')}`",
        f"- git_tree: `{candidate.get('git_tree')}`",
        f"- package: `{candidate.get('version_tag')}`",
        f"- automatic_release_promotion_allowed: `{payload.get('automatic_release_promotion_allowed')}`",
        "",
        "## Qualifications",
    ]
    for qualification_id in payload.get("required_qualification_ids") or []:
        qualifications = payload.get("qualifications")
        check = qualifications.get(qualification_id) if isinstance(qualifications, dict) else None
        if isinstance(check, dict):
            lines.append(
                f"- `{qualification_id}`: `{check.get('status')}` "
                f"({check.get('result_schema_version')})"
            )
        else:
            lines.append(f"- `{qualification_id}`: `missing`")
    for field, title in (
        ("source_mismatches", "Source Mismatches"),
        ("missing_qualification_ids", "Missing Qualifications"),
        ("qualification_failures", "Qualification Failures"),
    ):
        values = payload.get(field) or []
        if values:
            lines.extend(["", f"## {title}"])
            lines.extend(f"- `{value}`" for value in values)
    lines.extend(
        [
            "",
            "This receipt is read-only. It qualifies evidence for owner release review; "
            "it does not run checks, call a model, move refs, tag, or publish.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"
