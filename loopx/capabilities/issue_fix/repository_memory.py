from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any

from ...control_plane.runtime.public_safety import public_safe_compact_text
from ...public_safe_text import COMPACT_LABEL_PATTERN as _LABEL_PATTERN


ISSUE_FIX_REPOSITORY_MEMORY_READ_RESULT_SCHEMA_VERSION = (
    "issue_fix_repository_memory_read_result_v0"
)
ISSUE_FIX_REPOSITORY_MEMORY_HOOK_SCHEMA_VERSION = "issue_fix_repository_memory_hook_v0"

PROVIDER_STATUSES = {"completed", "unavailable", "disabled"}
VERIFICATION_STATUSES = {"confirmed", "refuted", "unverified"}
SUPPORT_ASPECTS = {
    "architecture",
    "ownership",
    "change_scope",
    "reproduction",
    "validation",
}
DECISION_INFLUENCE_ASPECTS = {
    "reproduction",
    "change_scope",
    "patch",
    "validation",
}
MAX_MEMORY_RESULTS = 6

_INPUT_FIELDS = {
    "schema_version",
    "provider",
    "namespace",
    "visibility",
    "status",
    "query_summary",
    "observed_at",
    "search_performed",
    "read_performed",
    "results",
    "reason_code",
    "provider_version",
    "latency_ms",
    "requested_limit",
    "configured_resource_count",
    "stale_or_unmapped_count",
    "verification_mode",
    "writeback_performed",
    "automatic_capture_performed",
}
_RESULT_FIELDS = {
    "memory_ref",
    "summary",
    "supports",
    "verification_status",
    "verification_reference",
    "verification_revision",
    "decision_influence",
}


def _safe_text(value: Any, *, field: str, limit: int = 220) -> str:
    text = public_safe_compact_text(value, limit=limit)
    if not text:
        raise ValueError(f"{field} must be compact and public-safe")
    return text


def _safe_label(value: Any, *, field: str) -> str:
    label = _safe_text(value, field=field, limit=120)
    if not _LABEL_PATTERN.fullmatch(label):
        raise ValueError(f"{field} must be a compact public-safe label")
    return label


def _safe_repo_reference(value: Any, *, field: str) -> str:
    reference = _safe_text(value, field=field, limit=260)
    if reference.startswith(("/", "~")) or re.match(r"^[A-Za-z]:[\\/]", reference):
        raise ValueError(f"{field} must be repository-relative")
    if "://" in reference:
        raise ValueError(f"{field} must be repository-relative")
    path = PurePosixPath(reference)
    if ".." in path.parts:
        raise ValueError(f"{field} must not traverse outside the repository")
    return reference


def _memory_ref_id(*, provider: str, namespace: str, memory_ref: str) -> str:
    digest = hashlib.sha256(
        f"{provider}\n{namespace}\n{memory_ref}".encode("utf-8")
    ).hexdigest()[:20]
    return f"memory-{digest}"


def _optional_bounded_int(
    value: Any,
    *,
    field: str,
    minimum: int,
    maximum: int,
) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    parsed = int(value)
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    return parsed


def _empty_projection(
    *,
    status: str,
    provider: str | None = None,
    namespace: str | None = None,
    reason_code: str | None = None,
    search_performed: bool = False,
    read_performed: bool = False,
) -> dict[str, Any]:
    projection: dict[str, Any] = {
        "schema_version": ISSUE_FIX_REPOSITORY_MEMORY_HOOK_SCHEMA_VERSION,
        "status": status,
        "provider": provider,
        "namespace": namespace,
        "visibility": "public" if namespace else None,
        "explicit_search_read_only": True,
        "search_performed": search_performed,
        "read_performed": read_performed,
        "result_count": 0,
        "confirmed_count": 0,
        "refuted_count": 0,
        "unverified_count": 0,
        "verified_decision_influence_count": 0,
        "patch_influence_allowed_count": 0,
        "source_refs": [],
        "verification_refs": [],
        "fail_open": True,
        "writeback_performed": False,
        "automatic_capture_performed": False,
        "raw_memory_captured": False,
        "raw_provider_payload_captured": False,
        "credentials_captured": False,
    }
    if reason_code:
        projection["reason_code"] = reason_code
    return projection


def build_issue_fix_repository_memory_hook(
    *,
    memory_input: Mapping[str, Any] | None,
    repository_revision: str | None,
) -> dict[str, Any]:
    """Normalise a host-provided search/read result without retaining raw memory."""

    if memory_input is None:
        return {
            "sources": [],
            "projection": _empty_projection(status="not_requested"),
        }
    if not isinstance(memory_input, Mapping):
        raise ValueError("repository memory result must be an object")
    unknown = sorted(set(memory_input) - _INPUT_FIELDS)
    if unknown:
        raise ValueError(f"repository memory result has unsupported fields: {unknown}")
    if (
        memory_input.get("schema_version")
        != ISSUE_FIX_REPOSITORY_MEMORY_READ_RESULT_SCHEMA_VERSION
    ):
        raise ValueError(
            "repository memory result schema_version must be "
            "issue_fix_repository_memory_read_result_v0"
        )
    if memory_input.get("writeback_performed") is not False:
        raise ValueError("repository memory hook forbids memory writeback")
    if memory_input.get("automatic_capture_performed") is not False:
        raise ValueError("repository memory hook forbids automatic capture")

    provider = _safe_label(memory_input.get("provider"), field="provider")
    namespace = _safe_label(memory_input.get("namespace"), field="namespace")
    if memory_input.get("visibility") != "public":
        raise ValueError("repository memory namespace visibility must be public")
    status = str(memory_input.get("status") or "").strip()
    if status not in PROVIDER_STATUSES:
        raise ValueError(
            f"repository memory status must be one of {sorted(PROVIDER_STATUSES)}"
        )
    query_summary = _safe_text(
        memory_input.get("query_summary"), field="query_summary", limit=220
    )
    observed_at = _safe_text(
        memory_input.get("observed_at"), field="observed_at", limit=80
    )
    provider_version = (
        _safe_text(
            memory_input.get("provider_version"), field="provider_version", limit=80
        )
        if memory_input.get("provider_version") is not None
        else None
    )
    latency_ms = _optional_bounded_int(
        memory_input.get("latency_ms"),
        field="latency_ms",
        minimum=0,
        maximum=600_000,
    )
    requested_limit = _optional_bounded_int(
        memory_input.get("requested_limit"),
        field="requested_limit",
        minimum=1,
        maximum=MAX_MEMORY_RESULTS,
    )
    configured_resource_count = _optional_bounded_int(
        memory_input.get("configured_resource_count"),
        field="configured_resource_count",
        minimum=0,
        maximum=24,
    )
    stale_or_unmapped_count = _optional_bounded_int(
        memory_input.get("stale_or_unmapped_count"),
        field="stale_or_unmapped_count",
        minimum=0,
        maximum=MAX_MEMORY_RESULTS,
    )
    verification_mode = (
        _safe_label(memory_input.get("verification_mode"), field="verification_mode")
        if memory_input.get("verification_mode") is not None
        else None
    )
    compact_telemetry = {
        key: value
        for key, value in {
            "provider_version": provider_version,
            "latency_ms": latency_ms,
            "requested_limit": requested_limit,
            "configured_resource_count": configured_resource_count,
            "stale_or_unmapped_count": stale_or_unmapped_count,
            "verification_mode": verification_mode,
        }.items()
        if value is not None
    }
    search_performed = memory_input.get("search_performed") is True
    read_performed = memory_input.get("read_performed") is True
    raw_results = memory_input.get("results") or []
    if not isinstance(raw_results, Sequence) or isinstance(raw_results, (str, bytes)):
        raise ValueError("repository memory results must be a list")
    if len(raw_results) > MAX_MEMORY_RESULTS:
        raise ValueError(
            f"repository memory result supports at most {MAX_MEMORY_RESULTS} hits"
        )

    if status in {"unavailable", "disabled"}:
        if raw_results:
            raise ValueError(f"repository memory status {status} must not include hits")
        reason_code = _safe_label(memory_input.get("reason_code"), field="reason_code")
        return {
            "sources": [],
            "projection": _empty_projection(
                status=status,
                provider=provider,
                namespace=namespace,
                reason_code=reason_code,
                search_performed=search_performed,
                read_performed=read_performed,
            )
            | {
                "query_summary": query_summary,
                "observed_at": observed_at,
                **compact_telemetry,
            },
        }

    if not search_performed:
        raise ValueError("completed repository memory result requires explicit search")
    if raw_results and not read_performed:
        raise ValueError("repository memory hits require explicit read")
    if raw_results and not repository_revision:
        return {
            "sources": [],
            "projection": _empty_projection(
                status="skipped_missing_revision",
                provider=provider,
                namespace=namespace,
                reason_code="repository_revision_required",
                search_performed=search_performed,
                read_performed=read_performed,
            )
            | {
                "query_summary": query_summary,
                "observed_at": observed_at,
                "discarded_result_count": len(raw_results),
                **compact_telemetry,
            },
        }

    sources: list[dict[str, Any]] = []
    verification_refs: list[str] = []
    counts = {key: 0 for key in VERIFICATION_STATUSES}
    verified_decision_influence_count = 0
    patch_influence_allowed_count = 0
    for index, raw in enumerate(raw_results):
        if not isinstance(raw, Mapping):
            raise ValueError(f"repository memory results[{index}] must be an object")
        unknown_result = sorted(set(raw) - _RESULT_FIELDS)
        if unknown_result:
            raise ValueError(
                f"repository memory results[{index}] has unsupported fields: "
                f"{unknown_result}"
            )
        memory_ref = _safe_text(
            raw.get("memory_ref"), field=f"results[{index}].memory_ref", limit=500
        )
        summary = _safe_text(
            raw.get("summary"), field=f"results[{index}].summary", limit=220
        )
        raw_supports = raw.get("supports")
        if not isinstance(raw_supports, Sequence) or isinstance(
            raw_supports, (str, bytes)
        ):
            raise ValueError(
                f"repository memory results[{index}].supports must be a list"
            )
        supports = sorted(
            {str(value).strip() for value in raw_supports if str(value).strip()}
        )
        if not supports or any(value not in SUPPORT_ASPECTS for value in supports):
            raise ValueError(
                f"repository memory results[{index}].supports must use "
                f"{sorted(SUPPORT_ASPECTS)}"
            )
        verification_status = str(
            raw.get("verification_status") or "unverified"
        ).strip()
        if verification_status not in VERIFICATION_STATUSES:
            raise ValueError(
                f"repository memory results[{index}].verification_status must be one "
                f"of {sorted(VERIFICATION_STATUSES)}"
            )
        counts[verification_status] += 1
        raw_decision_influence = raw.get("decision_influence") or []
        if not isinstance(raw_decision_influence, Sequence) or isinstance(
            raw_decision_influence, (str, bytes)
        ):
            raise ValueError(
                f"repository memory results[{index}].decision_influence must be a list"
            )
        decision_influence = sorted(
            {
                str(value).strip()
                for value in raw_decision_influence
                if str(value).strip()
            }
        )
        if any(
            value not in DECISION_INFLUENCE_ASPECTS
            for value in decision_influence
        ):
            raise ValueError(
                f"repository memory results[{index}].decision_influence must use "
                f"{sorted(DECISION_INFLUENCE_ASPECTS)}"
            )
        if decision_influence and verification_status != "confirmed":
            raise ValueError(
                f"repository memory results[{index}] must be confirmed before "
                "recording decision influence"
            )

        verification_reference: str | None = None
        if verification_status in {"confirmed", "refuted"}:
            verification_reference = _safe_repo_reference(
                raw.get("verification_reference"),
                field=f"results[{index}].verification_reference",
            )
            verification_revision = _safe_text(
                raw.get("verification_revision"),
                field=f"results[{index}].verification_revision",
                limit=120,
            )
            if verification_revision != repository_revision:
                raise ValueError(
                    f"repository memory results[{index}] verification_revision must "
                    "match repository_revision"
                )
            verification_refs.append(verification_reference)
        elif raw.get("verification_reference") or raw.get("verification_revision"):
            raise ValueError(
                f"repository memory results[{index}] must not claim verification "
                "metadata while unverified"
            )

        if verification_status == "confirmed":
            if decision_influence:
                verified_decision_influence_count += 1
            if "patch" in decision_influence:
                patch_influence_allowed_count += 1
            source_id = _memory_ref_id(
                provider=provider,
                namespace=namespace,
                memory_ref=memory_ref,
            )
            source_summary = (
                f"{summary} Checkout verification: confirmed. "
                f"Evidence: {verification_reference}."
            )
            if decision_influence:
                source_summary += (
                    " Verified decision influence: "
                    + ", ".join(decision_influence)
                    + "."
                )
            sources.append(
                {
                    "source_id": source_id,
                    "source_kind": "memory_retrieval",
                    "reference": f"memory:{source_id.removeprefix('memory-')}",
                    "trust": "advisory",
                    "freshness": "current",
                    "supports": supports,
                    "summary": source_summary,
                }
            )

    projection = _empty_projection(
        status=(
            "used"
            if sources
            else "verification_required"
            if counts["unverified"]
            else "no_usable_results"
            if counts["refuted"]
            else "empty"
        ),
        provider=provider,
        namespace=namespace,
        search_performed=search_performed,
        read_performed=read_performed,
    )
    projection.update(
        {
            "query_summary": query_summary,
            "observed_at": observed_at,
            "result_count": len(raw_results),
            "confirmed_count": counts["confirmed"],
            "refuted_count": counts["refuted"],
            "unverified_count": counts["unverified"],
            "verified_decision_influence_count": verified_decision_influence_count,
            "patch_influence_allowed_count": patch_influence_allowed_count,
            "source_refs": [source["source_id"] for source in sources],
            "verification_refs": sorted(set(verification_refs)),
            **compact_telemetry,
        }
    )
    return {"sources": sources, "projection": projection}
