from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote

from ..context_providers import (
    build_context_provider,
    canonical_context_matches,
    context_provider_service_restarted,
    load_context_provider_service_ownership,
)
from ..context_providers.base import (
    ContextProvider,
    ContextProviderRetrieval,
    opaque_provider_ref,
)
from ...repository_identity import resolve_project_identity
from ...control_plane.runtime.public_safety import public_safe_compact_text
from ...public_safe_text import COMPACT_LABEL_PATTERN as _LABEL
from .repository_memory import (
    ISSUE_FIX_REPOSITORY_MEMORY_READ_RESULT_SCHEMA_VERSION,
    MAX_MEMORY_RESULTS,
    SUPPORT_ASPECTS,
)


ISSUE_FIX_REPOSITORY_MEMORY_PROVIDER_CONFIG_SCHEMA_VERSION = (
    "issue_fix_repository_memory_provider_config_v0"
)
PROVIDER_CONFIG_ENV = "LOOPX_ISSUE_FIX_REPOSITORY_MEMORY_PROVIDER_CONFIG"
MAX_PROVIDER_TIMEOUT_SECONDS = 60.0
MAX_PROVIDER_SYNC_TIMEOUT_SECONDS = 600.0
MAX_PROVIDER_SYNC_REFERENCES = 24

_CONFIG_FIELDS = {
    "schema_version",
    "enabled",
    "provider",
    "provider_binary",
    "minimum_provider_version",
    "namespace",
    "visibility",
    "scope_ref",
    "revision_policy",
    "repository_revision",
    "repository_identity",
    "max_results",
    "timeout_seconds",
    "sync_timeout_seconds",
    "resource_references",
    "writeback_enabled",
    "writeback_scope_ref",
    "workspace_scope",
    "peer_scope",
    "service_ownership_receipt_path",
}
_REMOVED_REVISION_LIFECYCLE_FIELDS = {
    "repository_scope_root",
    "active_repository_revision",
    "active_scope_ref",
}
_FORBIDDEN_WRITEBACK_FIELDS = {
    "credential",
    "credentials",
    "expert_answer",
    "private_material",
    "raw_expert_answer",
    "raw_tool_logs",
    "raw_tool_results",
    "raw_transcript",
    "tool_logs",
    "tool_results",
    "transcript",
}


def default_repository_memory_provider_config_path() -> str | None:
    value = os.environ.get(PROVIDER_CONFIG_ENV, "").strip()
    return value or None


def _compact(value: Any, *, field: str, limit: int) -> str:
    text = public_safe_compact_text(value, limit=limit)
    if not text:
        raise ValueError(f"{field} must be compact and public-safe")
    return text


def _label(value: Any, *, field: str) -> str:
    value = _compact(value, field=field, limit=120)
    if not _LABEL.fullmatch(value):
        raise ValueError(f"{field} must be a compact public-safe label")
    return value


def _disabled_memory_input(
    *,
    provider: str,
    namespace: str,
    query_summary: str,
    observed_at: str,
) -> dict[str, Any]:
    return {
        "schema_version": ISSUE_FIX_REPOSITORY_MEMORY_READ_RESULT_SCHEMA_VERSION,
        "provider": provider,
        "namespace": namespace,
        "visibility": "public",
        "status": "disabled",
        "query_summary": query_summary,
        "observed_at": observed_at,
        "search_performed": False,
        "read_performed": False,
        "reason_code": "provider_disabled",
        "writeback_performed": False,
        "automatic_capture_performed": False,
        "results": [],
    }


def _unavailable_memory_input(
    *,
    provider: str,
    namespace: str,
    query_summary: str,
    observed_at: str,
    search_performed: bool,
    read_performed: bool,
    reason_code: str,
) -> dict[str, Any]:
    return {
        "schema_version": ISSUE_FIX_REPOSITORY_MEMORY_READ_RESULT_SCHEMA_VERSION,
        "provider": provider,
        "namespace": namespace,
        "visibility": "public",
        "status": "unavailable",
        "query_summary": query_summary,
        "observed_at": observed_at,
        "search_performed": search_performed,
        "read_performed": read_performed,
        "reason_code": _label(reason_code, field="reason_code"),
        "writeback_performed": False,
        "automatic_capture_performed": False,
        "results": [],
    }


def _normalise_config(
    config: Mapping[str, Any],
    *,
    repository_revision: str,
) -> dict[str, Any]:
    if (
        config.get("schema_version")
        != ISSUE_FIX_REPOSITORY_MEMORY_PROVIDER_CONFIG_SCHEMA_VERSION
    ):
        raise ValueError(
            "repository memory provider config schema_version must be "
            "issue_fix_repository_memory_provider_config_v0"
        )
    removed_fields = sorted(set(config) & _REMOVED_REVISION_LIFECYCLE_FIELDS)
    if removed_fields:
        raise ValueError(
            "per-checkout repository activation fields were removed; configure one "
            "stable scope_ref for a provider-managed rolling default-branch index: "
            f"{removed_fields}"
        )
    unknown = sorted(set(config) - _CONFIG_FIELDS)
    if unknown:
        raise ValueError(
            f"repository memory provider config has unsupported fields: {unknown}"
        )
    provider = _label(config.get("provider"), field="provider")
    namespace = _label(config.get("namespace"), field="namespace")
    if config.get("visibility") != "public":
        raise ValueError("repository memory provider visibility must be public")
    if not re.fullmatch(r"[0-9a-fA-F]{12,64}", repository_revision):
        raise ValueError("current repository revision must be a git object id")
    configured_revision = str(config.get("repository_revision") or "").strip()
    revision_policy = str(
        config.get("revision_policy")
        or ("pinned" if configured_revision else "rolling_default_branch")
    ).strip()
    if revision_policy == "checkout_head":
        raise ValueError(
            "checkout_head revision scopes were removed; use "
            "revision_policy=rolling_default_branch with one stable scope_ref"
        )
    if revision_policy not in {"pinned", "rolling_default_branch"}:
        raise ValueError(
            "revision_policy must be pinned or rolling_default_branch"
        )
    if revision_policy == "pinned":
        configured_revision = _compact(
            configured_revision,
            field="repository_revision",
            limit=120,
        )
        if not re.fullmatch(r"[0-9a-fA-F]{12,64}", configured_revision):
            raise ValueError(
                "repository memory provider revision must be a git object id"
            )
        availability_reason_code = (
            None
            if configured_revision == repository_revision
            else "provider_revision_mismatch"
        )
        scope_ref = _compact(
            config.get("scope_ref"), field="scope_ref", limit=500
        ).rstrip("/")
    else:
        if configured_revision:
            raise ValueError(
                "rolling_default_branch does not accept repository_revision; "
                "the current checkout revision is supplied by the issue-fix caller"
            )
        configured_revision = repository_revision
        availability_reason_code = None
        scope_ref = _compact(
            config.get("scope_ref"), field="scope_ref", limit=500
        ).rstrip("/")
    if not scope_ref.startswith("viking://"):
        raise ValueError("repository memory provider scope_ref must use viking://")
    if revision_policy == "pinned" and configured_revision[:12] not in scope_ref:
        raise ValueError("repository memory provider scope_ref must be revision-scoped")
    max_results = min(
        max(1, int(config.get("max_results") or 3)),
        MAX_MEMORY_RESULTS,
    )
    timeout_seconds = min(
        max(1.0, float(config.get("timeout_seconds") or 15.0)),
        MAX_PROVIDER_TIMEOUT_SECONDS,
    )
    sync_timeout_seconds = min(
        max(1.0, float(config.get("sync_timeout_seconds") or 180.0)),
        MAX_PROVIDER_SYNC_TIMEOUT_SECONDS,
    )
    raw_resource_references = config.get("resource_references") or []
    if not isinstance(raw_resource_references, Sequence) or isinstance(
        raw_resource_references, (str, bytes)
    ):
        raise ValueError("resource_references must be a list")
    if len(raw_resource_references) > MAX_PROVIDER_SYNC_REFERENCES:
        raise ValueError(
            f"resource_references supports at most {MAX_PROVIDER_SYNC_REFERENCES} files"
        )
    resource_references: list[str] = []
    for index, raw_reference in enumerate(raw_resource_references):
        reference = PurePosixPath(
            _compact(raw_reference, field=f"resource_references[{index}]", limit=260)
        )
        if reference.is_absolute() or ".." in reference.parts:
            raise ValueError("resource_references must be repository-relative")
        normalised_reference = reference.as_posix()
        if normalised_reference not in resource_references:
            resource_references.append(normalised_reference)
    writeback_enabled = config.get("writeback_enabled") is True
    writeback_scope_ref = str(config.get("writeback_scope_ref") or "").strip()
    workspace_scope = str(config.get("workspace_scope") or "").strip()
    peer_scope = str(config.get("peer_scope") or "").strip()
    service_ownership_receipt_path = str(
        config.get("service_ownership_receipt_path") or ""
    ).strip()
    repository_identity = str(config.get("repository_identity") or "").strip()
    if repository_identity:
        repository_identity = resolve_project_identity(
            ".", remote_url=repository_identity
        )
    if writeback_enabled:
        writeback_scope_ref = _compact(
            writeback_scope_ref,
            field="writeback_scope_ref",
            limit=500,
        ).rstrip("/")
        if not writeback_scope_ref.startswith("viking://resources/"):
            raise ValueError(
                "repository memory writeback_scope_ref must use viking://resources/"
            )
        if repository_revision[:12] not in writeback_scope_ref:
            raise ValueError(
                "repository memory writeback_scope_ref must be revision-scoped"
            )
        workspace_scope = _label(workspace_scope, field="workspace_scope")
        peer_scope = _label(peer_scope, field="peer_scope")
    return {
        **dict(config),
        "provider": provider,
        "namespace": namespace,
        "scope_ref": scope_ref,
        "revision_policy": revision_policy,
        "repository_revision": configured_revision,
        "max_results": max_results,
        "timeout_seconds": timeout_seconds,
        "sync_timeout_seconds": sync_timeout_seconds,
        "resource_references": resource_references,
        "enabled": config.get("enabled") is not False,
        "writeback_enabled": writeback_enabled,
        "writeback_scope_ref": writeback_scope_ref,
        "workspace_scope": workspace_scope,
        "peer_scope": peer_scope,
        "service_ownership_receipt_path": service_ownership_receipt_path,
        "repository_identity": repository_identity,
        "availability_reason_code": availability_reason_code,
    }


def _repository_scope_check(
    normalised: Mapping[str, Any], *, repo_path: str | Path
) -> dict[str, Any]:
    expected = str(normalised.get("repository_identity") or "").strip()
    if not expected:
        return {
            "required": False,
            "matched": None,
            "reason_code": "repository_identity_not_configured",
        }
    try:
        observed = resolve_project_identity(repo_path)
    except ValueError:
        observed = ""
    return {
        "required": True,
        "matched": observed == expected,
        "reason_code": (
            "repository_identity_matched"
            if observed == expected
            else (
                "repository_identity_mismatch"
                if observed
                else "repository_identity_unavailable"
            )
        ),
        "expected_identity_digest": hashlib.sha256(
            expected.encode("utf-8")
        ).hexdigest()[:16],
        "observed_identity_digest": (
            hashlib.sha256(observed.encode("utf-8")).hexdigest()[:16]
            if observed
            else None
        ),
    }


def _fail_open_provider_result(
    *,
    config: Mapping[str, Any],
    query_summary: str,
    observed_at: str,
    repository_revision: str,
    reason_code: str,
    repository_scope_check: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    provider = _label(config.get("provider"), field="provider")
    namespace = _label(config.get("namespace"), field="namespace")
    memory_input = _unavailable_memory_input(
        provider=provider,
        namespace=namespace,
        query_summary=query_summary,
        observed_at=observed_at,
        search_performed=False,
        read_performed=False,
        reason_code=reason_code,
    )
    memory_input.update(
        {
            "requested_limit": min(
                max(1, int(config.get("max_results") or 3)), MAX_MEMORY_RESULTS
            ),
            "configured_resource_count": len(config.get("resource_references") or []),
            "stale_or_unmapped_count": 0,
            "verification_mode": "exact_project_current_checkout_required",
        }
    )
    projection = ContextProviderRetrieval(
        provider=provider,
        namespace=namespace,
        status="unavailable",
        query_summary=query_summary,
        observed_at=observed_at,
        search_performed=False,
        read_performed=False,
        reason_code=reason_code,
        requested_limit=int(memory_input["requested_limit"]),
    ).public_packet()
    projection["checkout_revision_digest"] = hashlib.sha256(
        repository_revision.encode("utf-8")
    ).hexdigest()[:16]
    if repository_scope_check is not None:
        projection["repository_scope_check"] = dict(repository_scope_check)
    return {"memory_input": memory_input, "provider_projection": projection}


def _repository_context_advisory_packet(
    normalised: Mapping[str, Any],
    *,
    repository_revision: str,
    repository_scope_check: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project a stable provider scope without turning it into patch authority."""

    retrieval_allowed = bool(
        normalised.get("enabled")
        and not normalised.get("availability_reason_code")
        and (repository_scope_check or {}).get("matched") is not False
    )
    return {
        "schema_version": "repository_context_advisory_v0",
        "ok": True,
        "provider": str(normalised["provider"]),
        "namespace": str(normalised["namespace"]),
        "visibility": "public",
        "source_policy": str(normalised["revision_policy"]),
        "scope_ref": opaque_provider_ref(
            provider=str(normalised["provider"]),
            namespace=str(normalised["namespace"]),
            resource_ref=str(normalised["scope_ref"]),
        ),
        "current_checkout_revision": repository_revision,
        "retrieval_allowed": retrieval_allowed,
        "verification_required": True,
        "patch_authority": False,
        "provider_refresh_ownership": "external",
        "repository_scope_check": dict(repository_scope_check or {}),
        "raw_provider_refs_captured": False,
    }


def _validated_outcome_fact(
    outcome_packet: Mapping[str, Any],
    *,
    repository_revision: str,
    workspace_scope: str,
    peer_scope: str,
    verification_reference_digests: Mapping[str, str] | None = None,
) -> tuple[str, str, str]:
    def reject_unsafe_fields(value: Any) -> None:
        if isinstance(value, Mapping):
            for raw_key, nested in value.items():
                key = str(raw_key).strip().lower().replace("-", "_")
                if key in _FORBIDDEN_WRITEBACK_FIELDS and nested:
                    raise ValueError(
                        f"repository memory writeback rejects unsafe field: {key}"
                    )
                if key in {
                    "credentials_captured",
                    "local_paths_captured",
                    "raw_content_captured",
                    "raw_logs_captured",
                } and nested is True:
                    raise ValueError(
                        f"repository memory writeback rejects unsafe capture flag: {key}"
                    )
                reject_unsafe_fields(nested)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            for nested in value:
                reject_unsafe_fields(nested)

    reject_unsafe_fields(outcome_packet)
    if outcome_packet.get("schema_version") != "issue_fix_outcome_projection_v0":
        raise ValueError(
            "repository memory writeback requires issue_fix_outcome_projection_v0"
        )
    outcomes = outcome_packet.get("issue_fix_outcomes")
    if not isinstance(outcomes, Sequence) or isinstance(outcomes, (str, bytes)):
        raise ValueError("repository memory writeback requires one outcome case")
    cases = [item for item in outcomes if isinstance(item, Mapping)]
    if len(cases) != 1:
        raise ValueError("repository memory writeback requires exactly one outcome case")
    case = cases[0]
    repository_context = case.get("repository_context")
    delivery = case.get("delivery")
    validation = case.get("validation")
    result = case.get("result")
    issue = case.get("issue")
    if not all(
        isinstance(value, Mapping)
        for value in (repository_context, delivery, validation, result, issue)
    ):
        raise ValueError("repository memory writeback outcome case is incomplete")
    if str(repository_context.get("revision") or "") != repository_revision:
        raise ValueError(
            "repository memory writeback revision must match the outcome checkout"
        )
    if delivery.get("evidence_provided") is not True:
        raise ValueError("repository memory writeback requires explicit delivery evidence")
    if delivery.get("outcome_status") != "completed":
        raise ValueError("repository memory writeback requires completed delivery")
    if validation.get("status") != "passed":
        raise ValueError("repository memory writeback requires passed validation")
    recorded_at = _compact(
        delivery.get("recorded_at"), field="delivery recorded_at", limit=80
    )
    if not recorded_at:
        raise ValueError(
            "repository memory writeback requires stable delivery recorded_at"
        )

    public_outputs: list[dict[str, str]] = []
    for index, item in enumerate(result.get("public_outputs") or []):
        if not isinstance(item, Mapping):
            raise ValueError(f"public_outputs[{index}] must be an object")
        kind = _compact(item.get("kind") or "artifact", field="output kind", limit=60)
        url = _compact(item.get("url"), field="output url", limit=320)
        if url and not url.startswith("https://"):
            raise ValueError("repository memory output URLs must use https")
        if url:
            public_outputs.append({"kind": kind, "url": url})

    changed_files: list[str] = []
    for index, raw_path in enumerate(delivery.get("changed_files") or []):
        path = PurePosixPath(
            _compact(raw_path, field=f"changed_files[{index}]", limit=260)
        )
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("repository memory changed files must be repo-relative")
        changed_files.append(path.as_posix())

    repo = _compact(case.get("repo"), field="repo", limit=180)
    issue_ref = _compact(case.get("issue_ref"), field="issue_ref", limit=180)
    reusable_knowledge = case.get("reusable_knowledge")
    if reusable_knowledge is not None and not isinstance(reusable_knowledge, Mapping):
        raise ValueError("repository memory reusable_knowledge must be an object")
    learning_card_eligible = bool(
        isinstance(reusable_knowledge, Mapping)
        and reusable_knowledge.get("schema_version")
        == "issue_fix_repository_learning_card_input_v0"
    )
    if learning_card_eligible and case.get("stage") not in {
        "merged",
        "comment_published",
        "triage_complete",
    }:
        raise ValueError("repository learning card requires a terminal outcome")
    supersession_key = "sha256:" + hashlib.sha256(
        f"{workspace_scope}\n{peer_scope}\n{repo}\n{issue_ref}".encode("utf-8")
    ).hexdigest()
    knowledge_eligible = bool(reusable_knowledge)
    fact = {
        "schema_version": (
            "issue_fix_repository_learning_card_memory_v0"
            if learning_card_eligible
            else (
                "issue_fix_reusable_knowledge_memory_v0"
                if knowledge_eligible
                else "issue_fix_validated_outcome_memory_v0"
            )
        ),
        "fact_type": (
            "repository_learning_card"
            if learning_card_eligible
            else (
                "reusable_issue_fix_knowledge"
                if knowledge_eligible
                else "validated_issue_fix_outcome"
            )
        ),
        "workspace_scope": workspace_scope,
        "peer_scope": peer_scope,
        "repository": repo,
        "issue_ref": issue_ref,
        "issue_url": _compact(issue.get("url"), field="issue URL", limit=320),
        "route": _label(case.get("route"), field="route"),
        "stage": _label(case.get("stage"), field="stage"),
        "repository_revision": repository_revision,
        "context_fingerprint": _compact(
            repository_context.get("fingerprint"),
            field="context fingerprint",
            limit=80,
        ),
        "validation_status": "passed",
        "validation_label": _compact(
            validation.get("label"), field="validation label", limit=260
        ),
        "commit_ref": _compact(
            delivery.get("commit_ref"), field="commit ref", limit=80
        ),
        "changed_files": changed_files,
        "public_outputs": public_outputs,
        "risks": [
            _compact(item, field=f"risks[{index}]", limit=260)
            for index, item in enumerate(case.get("risks") or [])
        ],
        "freshness": "revision_pinned",
        "observed_at": recorded_at,
        "provenance": "issue_fix_outcome_projection_v0",
        "supersession_key": supersession_key,
    }
    if knowledge_eligible:
        fact["knowledge"] = dict(reusable_knowledge)
    if learning_card_eligible:
        fact.update(
            {
                "confidence": reusable_knowledge["confidence"],
                "affected_modules": list(
                    reusable_knowledge.get("affected_modules") or []
                ),
                "invalidation_conditions": list(
                    reusable_knowledge.get("invalidation_conditions") or []
                ),
                "revalidation_contract": reusable_knowledge[
                    "revalidation_contract"
                ],
                "current_checkout_verification_required": True,
                "evidence_refs": [
                    output["url"] for output in public_outputs if output.get("url")
                ],
                "verification_reference_digests": dict(
                    verification_reference_digests or {}
                ),
            }
        )
    canonical = json.dumps(
        fact,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    idempotency_key = "sha256:" + hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()
    body = (
        (
            "# Reusable issue-fix knowledge\n\n```json\n"
            if knowledge_eligible
            else "# Validated issue-fix outcome\n\n```json\n"
        )
        + json.dumps(
            {**fact, "idempotency_key": idempotency_key},
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n```\n"
    )
    return idempotency_key, body, str(fact["fact_type"])


def write_issue_fix_validated_outcome_memory(
    *,
    config: Mapping[str, Any],
    outcome_packet: Mapping[str, Any],
    repository_revision: str,
    repo_path: str | Path | None,
    observed_at: str,
    execute: bool,
    provider: ContextProvider | None = None,
) -> dict[str, Any]:
    """Write one distilled, revision-pinned outcome through an explicit provider gate."""

    normalised = _normalise_config(config, repository_revision=repository_revision)
    base = {
        "schema_version": "issue_fix_validated_outcome_memory_writeback_v0",
        "provider": normalised["provider"],
        "namespace": normalised["namespace"],
        "visibility": "public",
        "repository_revision": repository_revision,
        "writeback_enabled": normalised["writeback_enabled"],
        "automatic_capture_performed": False,
        "raw_content_captured": False,
        "credentials_captured": False,
    }
    if not normalised["enabled"]:
        return {
            **base,
            "ok": False,
            "status": "disabled",
            "reason_code": "provider_disabled",
            "external_writes_performed": False,
        }
    if normalised.get("availability_reason_code"):
        return {
            **base,
            "ok": False,
            "status": "blocked",
            "reason_code": normalised["availability_reason_code"],
            "external_writes_performed": False,
        }
    if not normalised["writeback_enabled"]:
        return {
            **base,
            "ok": False,
            "status": "disabled",
            "reason_code": "writeback_not_owner_enabled",
            "external_writes_performed": False,
        }
    outcomes = outcome_packet.get("issue_fix_outcomes")
    cases = [item for item in outcomes or [] if isinstance(item, Mapping)]
    case = cases[0] if len(cases) == 1 else {}
    delivery = case.get("delivery") if isinstance(case, Mapping) else None
    commit_ref = (
        _compact(delivery.get("commit_ref"), field="commit ref", limit=80)
        if isinstance(delivery, Mapping) and delivery.get("commit_ref")
        else ""
    )
    verification = {
        "schema_version": "issue_fix_validated_outcome_checkout_verification_v0",
        "repository_revision": repository_revision,
        "commit_ref": commit_ref or None,
        "repository_revision_resolved": False,
        "commit_ref_resolved": False,
        "commit_is_ancestor": False,
        "repo_path_recorded": False,
        "raw_git_output_captured": False,
    }
    if repo_path is None:
        return {
            **base,
            "ok": False,
            "status": "blocked",
            "reason_code": "repo_checkout_required",
            "checkout_verification": verification,
            "external_writes_performed": False,
        }
    repository_scope_check = _repository_scope_check(normalised, repo_path=repo_path)
    if repository_scope_check.get("matched") is False:
        return {
            **base,
            "ok": False,
            "status": "blocked",
            "reason_code": repository_scope_check["reason_code"],
            "repository_scope_check": repository_scope_check,
            "external_writes_performed": False,
        }
    if not commit_ref:
        return {
            **base,
            "ok": False,
            "status": "blocked",
            "reason_code": "delivery_commit_ref_required",
            "checkout_verification": verification,
            "external_writes_performed": False,
        }
    checkout = Path(repo_path).expanduser().resolve()

    def git_ok(*args: str) -> bool:
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=checkout,
                text=True, encoding="utf-8", errors="replace",
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0

    def git_blob_digest(reference: str) -> str | None:
        try:
            result = subprocess.run(
                ["git", "show", f"{repository_revision}:{reference}"],
                cwd=checkout,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0:
            return None
        return "sha256:" + hashlib.sha256(result.stdout).hexdigest()

    verification["repository_revision_resolved"] = git_ok(
        "cat-file", "-e", f"{repository_revision}^{{commit}}"
    )
    verification["commit_ref_resolved"] = git_ok(
        "cat-file", "-e", f"{commit_ref}^{{commit}}"
    )
    if not verification["repository_revision_resolved"]:
        reason_code = "repository_revision_not_in_checkout"
    elif not verification["commit_ref_resolved"]:
        reason_code = "delivery_commit_not_in_checkout"
    else:
        verification["commit_is_ancestor"] = git_ok(
            "merge-base", "--is-ancestor", commit_ref, repository_revision
        )
        reason_code = (
            None
            if verification["commit_is_ancestor"]
            else "delivery_commit_not_in_repository_revision"
        )
    if reason_code:
        return {
            **base,
            "ok": False,
            "status": "blocked",
            "reason_code": reason_code,
            "checkout_verification": verification,
            "external_writes_performed": False,
        }
    reusable_knowledge = (
        case.get("reusable_knowledge")
        if isinstance(case, Mapping)
        else None
    )
    verification_reference_digests: dict[str, str] = {}
    if isinstance(reusable_knowledge, Mapping):
        verification_references = list(
            reusable_knowledge.get("verification_references") or []
        )
        if (
            reusable_knowledge.get("schema_version")
            == "issue_fix_repository_learning_card_input_v0"
        ):
            verification_references.extend(
                reusable_knowledge.get("affected_modules") or []
            )
        missing_references = [
            str(reference)
            for reference in verification_references
            if not git_ok(
                "cat-file",
                "-e",
                f"{repository_revision}:{reference}",
            )
        ]
        if missing_references:
            return {
                **base,
                "ok": False,
                "status": "blocked",
                "reason_code": "knowledge_verification_reference_not_in_revision",
                "missing_reference_count": len(missing_references),
                "checkout_verification": verification,
                "external_writes_performed": False,
            }
        if (
            reusable_knowledge.get("schema_version")
            == "issue_fix_repository_learning_card_input_v0"
        ):
            verification_reference_digests = {
                str(reference): digest
                for reference in reusable_knowledge.get("verification_references")
                or []
                if (digest := git_blob_digest(str(reference))) is not None
            }
            if len(verification_reference_digests) != len(
                reusable_knowledge.get("verification_references") or []
            ):
                return {
                    **base,
                    "ok": False,
                    "status": "blocked",
                    "reason_code": "knowledge_reference_digest_unavailable",
                    "checkout_verification": verification,
                    "external_writes_performed": False,
                }
    idempotency_key, body, fact_type = _validated_outcome_fact(
        outcome_packet,
        repository_revision=repository_revision,
        workspace_scope=str(normalised["workspace_scope"]),
        peer_scope=str(normalised["peer_scope"]),
        verification_reference_digests=verification_reference_digests,
    )
    digest = idempotency_key.removeprefix("sha256:")
    collection = (
        "repository-learning-cards"
        if fact_type == "repository_learning_card"
        else (
            "reusable-knowledge"
            if fact_type == "reusable_issue_fix_knowledge"
            else "validated-outcomes"
        )
    )
    target = (
        f"{normalised['writeback_scope_ref']}/{collection}/"
        f"{normalised['workspace_scope']}/{normalised['peer_scope']}/{digest}.md"
    )
    configured_provider = provider or build_context_provider(normalised)
    with tempfile.TemporaryDirectory(prefix="loopx-validated-outcome-") as tmpdir:
        source = Path(tmpdir) / f"{digest}.md"
        source.write_text(body, encoding="utf-8")
        sync = configured_provider.sync(
            namespace=str(normalised["namespace"]),
            resources=[(str(source), target)],
            timeout_seconds=float(normalised["sync_timeout_seconds"]),
            observed_at=observed_at,
            execute=execute,
        )
    packet = sync.public_packet()
    packet.update(
        {
            **base,
            "ok": sync.status in {"completed", "planned"},
            "status": sync.status,
            "reason_code": sync.reason_code,
            "idempotency_key": idempotency_key,
            "fact_type": fact_type,
            "knowledge_eligible": fact_type
            in {"reusable_issue_fix_knowledge", "repository_learning_card"},
            "supersession_key_recorded": True,
            "revision_scoped": True,
            "checkout_verification": verification,
        }
    )
    return packet


def _repo_relative_ref(
    *,
    scope_ref: str,
    resource_ref: str,
    configured_references: Sequence[str],
) -> str | None:
    prefix = scope_ref.rstrip("/") + "/"
    if not resource_ref.startswith(prefix):
        return None
    relative = unquote(resource_ref.removeprefix(prefix)).strip("/")
    if not relative:
        return None
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts:
        return None
    normalised = path.as_posix()
    for reference in sorted(configured_references, key=len, reverse=True):
        if normalised == reference or normalised.startswith(
            reference.rstrip("/") + "/"
        ):
            return reference
    return normalised if not configured_references else None


def _checkout_content(repo_path: Path, reference: str) -> str | None:
    root = repo_path.expanduser().resolve()
    candidate = (root / reference).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    try:
        return candidate.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _checkout_digest(repo_path: Path, reference: str) -> str | None:
    root = repo_path.expanduser().resolve()
    candidate = (root / reference).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    try:
        return "sha256:" + hashlib.sha256(candidate.read_bytes()).hexdigest()
    except OSError:
        return None


def _repository_learning_card(content: str) -> Mapping[str, Any] | None:
    marker = "```json"
    start = content.find(marker)
    if start < 0:
        return None
    end = content.find("```", start + len(marker))
    if end < 0:
        return None
    try:
        payload = json.loads(content[start + len(marker) : end].strip())
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, Mapping):
        return None
    if (
        payload.get("schema_version")
        != "issue_fix_repository_learning_card_memory_v0"
        or payload.get("fact_type") != "repository_learning_card"
        or payload.get("current_checkout_verification_required") is not True
    ):
        return None
    knowledge = payload.get("knowledge")
    if not isinstance(knowledge, Mapping):
        return None
    if (
        knowledge.get("schema_version")
        != "issue_fix_repository_learning_card_input_v0"
    ):
        return None
    return payload


def retrieve_issue_fix_repository_memory(
    *,
    config: Mapping[str, Any],
    repo_path: str | Path,
    repository_revision: str,
    query: str,
    query_summary: str,
    supports: Sequence[str],
    observed_at: str,
    provider: ContextProvider | None = None,
) -> dict[str, Any]:
    """Run a configured provider and convert it into issue-fix advisory evidence."""

    query = _compact(query, field="query", limit=500)
    query_summary = _compact(query_summary, field="query_summary", limit=220)
    normalised = _normalise_config(config, repository_revision=repository_revision)
    support_values = sorted(
        {str(value).strip() for value in supports if str(value).strip()}
    )
    if not support_values or any(
        value not in SUPPORT_ASPECTS for value in support_values
    ):
        raise ValueError(f"supports must use {sorted(SUPPORT_ASPECTS)}")
    provider_id = str(normalised["provider"])
    namespace = str(normalised["namespace"])
    repository_scope_check = _repository_scope_check(normalised, repo_path=repo_path)
    repository_context = _repository_context_advisory_packet(
        normalised,
        repository_revision=repository_revision,
        repository_scope_check=repository_scope_check,
    )
    if not normalised["enabled"]:
        memory_input = _disabled_memory_input(
            provider=provider_id,
            namespace=namespace,
            query_summary=query_summary,
            observed_at=observed_at,
        )
        return {
            "memory_input": memory_input,
            "provider_projection": {
                "status": "disabled",
                "fail_open": True,
                "result_count": 0,
            },
        }
    availability_reason_code = normalised.get("availability_reason_code")
    if availability_reason_code or repository_scope_check.get("matched") is False:
        result = _fail_open_provider_result(
            config=normalised,
            query_summary=query_summary,
            observed_at=observed_at,
            repository_revision=repository_revision,
            reason_code=str(
                availability_reason_code or repository_scope_check["reason_code"]
            ),
            repository_scope_check=(
                repository_scope_check
                if repository_scope_check.get("required")
                else None
            ),
        )
        result["provider_projection"]["repository_context"] = repository_context
        return result

    provider = provider or build_context_provider(normalised)
    retrieval = provider.retrieve(
        namespace=namespace,
        scope_ref=str(normalised["scope_ref"]),
        query=query,
        query_summary=query_summary,
        max_results=int(normalised["max_results"]),
        timeout_seconds=float(normalised["timeout_seconds"]),
        observed_at=observed_at,
    )
    provider_projection = retrieval.public_packet()
    provider_projection["repository_context"] = repository_context
    provider_projection["repository_scope_check"] = repository_scope_check
    if retrieval.status != "completed":
        memory_input = _unavailable_memory_input(
            provider=provider_id,
            namespace=namespace,
            query_summary=query_summary,
            observed_at=observed_at,
            search_performed=retrieval.search_performed,
            read_performed=retrieval.read_performed,
            reason_code=retrieval.reason_code or "provider_unavailable",
        )
        memory_input.update(
            {
                key: value
                for key, value in {
                    "provider_version": retrieval.provider_version,
                    "latency_ms": retrieval.latency_ms,
                    "requested_limit": retrieval.requested_limit,
                    "configured_resource_count": len(normalised["resource_references"]),
                    "stale_or_unmapped_count": 0,
                    "verification_mode": "canonical_text_or_parser_chunk",
                }.items()
                if value is not None
            }
        )
        return {
            "memory_input": memory_input,
            "provider_projection": provider_projection,
        }

    root = Path(repo_path)
    results: list[dict[str, Any]] = []
    confirmed_count = 0
    stale_or_unmapped_count = 0
    learning_card_count = 0
    learning_card_confirmed_count = 0
    learning_cards: list[dict[str, Any]] = []
    for item in retrieval.items:
        learning_card = _repository_learning_card(item.content)
        if learning_card is not None:
            learning_card_count += 1
            knowledge = learning_card["knowledge"]
            references = [
                str(reference)
                for reference in knowledge.get("verification_references") or []
            ]
            digests = learning_card.get("verification_reference_digests")
            digest_map = digests if isinstance(digests, Mapping) else {}
            confirmed = bool(references) and all(
                _checkout_digest(root, reference) == digest_map.get(reference)
                for reference in references
            )
            symptom = _compact(
                knowledge.get("symptom_signature"),
                field="learning_card.symptom_signature",
                limit=140,
            )
            repair = _compact(
                knowledge.get("repair_pattern"),
                field="learning_card.repair_pattern",
                limit=140,
            )
            row = {
                "memory_ref": opaque_provider_ref(
                    provider=provider_id,
                    namespace=namespace,
                    resource_ref=item.resource_ref,
                ),
                "summary": _compact(
                    f"{symptom} Repair pattern: {repair}",
                    field="learning_card.summary",
                    limit=220,
                ),
                "supports": support_values,
                "verification_status": "confirmed" if confirmed else "unverified",
            }
            if confirmed:
                row["verification_reference"] = references[0]
                row["verification_revision"] = repository_revision
                confirmed_count += 1
                learning_card_confirmed_count += 1
            else:
                stale_or_unmapped_count += 1
            results.append(row)
            learning_cards.append(
                {
                    "memory_ref": row["memory_ref"],
                    "confidence": _label(
                        knowledge.get("confidence"),
                        field="learning_card.confidence",
                    ),
                    "source_revision": _compact(
                        learning_card.get("repository_revision"),
                        field="learning_card.repository_revision",
                        limit=120,
                    ),
                    "affected_modules": [
                        _compact(value, field="learning_card.affected_module", limit=260)
                        for value in knowledge.get("affected_modules") or []
                    ],
                    "verification_references": references,
                    "invalidation_conditions": [
                        _compact(value, field="learning_card.invalidation", limit=320)
                        for value in knowledge.get("invalidation_conditions") or []
                    ],
                    "revalidation_contract": _compact(
                        knowledge.get("revalidation_contract"),
                        field="learning_card.revalidation_contract",
                        limit=420,
                    ),
                    "current_checkout_verification_required": True,
                    "reference_digest_match": confirmed,
                }
            )
            continue
        reference = _repo_relative_ref(
            scope_ref=str(normalised["scope_ref"]),
            resource_ref=item.resource_ref,
            configured_references=normalised["resource_references"],
        )
        checkout_content = _checkout_content(root, reference) if reference else None
        confirmed = checkout_content is not None and canonical_context_matches(
            item.content, checkout_content
        )
        row: dict[str, Any] = {
            "memory_ref": opaque_provider_ref(
                provider=provider_id,
                namespace=namespace,
                resource_ref=item.resource_ref,
            ),
            "summary": _compact(item.summary, field="result.summary", limit=220),
            "supports": support_values,
            "verification_status": "confirmed" if confirmed else "unverified",
        }
        if confirmed and reference:
            row["verification_reference"] = reference
            row["verification_revision"] = repository_revision
            confirmed_count += 1
        else:
            stale_or_unmapped_count += 1
        results.append(row)

    memory_input = {
        "schema_version": ISSUE_FIX_REPOSITORY_MEMORY_READ_RESULT_SCHEMA_VERSION,
        "provider": provider_id,
        "namespace": namespace,
        "visibility": "public",
        "status": "completed",
        "query_summary": query_summary,
        "observed_at": observed_at,
        "search_performed": retrieval.search_performed,
        "read_performed": retrieval.read_performed,
        "writeback_performed": False,
        "automatic_capture_performed": False,
        "provider_version": retrieval.provider_version,
        "latency_ms": retrieval.latency_ms,
        "requested_limit": retrieval.requested_limit,
        "configured_resource_count": len(normalised["resource_references"]),
        "stale_or_unmapped_count": stale_or_unmapped_count,
        "verification_mode": "canonical_text_parser_chunk_or_learning_card_digest",
        "results": results,
    }
    if memory_input["provider_version"] is None:
        del memory_input["provider_version"]
    provider_projection["checkout_verification"] = {
        "revision": repository_revision,
        "confirmed_count": confirmed_count,
        "stale_or_unmapped_count": stale_or_unmapped_count,
        "verified_decision_influence_count": 0,
        "patch_influence_allowed_count": 0,
        "configured_resource_count": len(normalised["resource_references"]),
        "verification_mode": "canonical_text_parser_chunk_or_learning_card_digest",
        "learning_card_count": learning_card_count,
        "learning_card_confirmed_count": learning_card_confirmed_count,
    }
    provider_projection["learning_cards"] = learning_cards
    return {
        "memory_input": memory_input,
        "provider_projection": provider_projection,
    }


def sync_issue_fix_repository_memory(
    *,
    config: Mapping[str, Any],
    repo_path: str | Path,
    repository_revision: str,
    references: Sequence[str],
    observed_at: str,
    execute: bool,
    provider: ContextProvider | None = None,
) -> dict[str, Any]:
    """Bound an explicit provider resource refresh to approved checkout files."""

    normalised = _normalise_config(config, repository_revision=repository_revision)
    if normalised.get("availability_reason_code"):
        return {
            "schema_version": "issue_fix_repository_memory_sync_v0",
            "ok": False,
            "status": "blocked",
            "reason_code": normalised["availability_reason_code"],
            "provider": normalised["provider"],
            "namespace": normalised["namespace"],
            "repository_revision": repository_revision,
            "requested_reference_count": 0,
            "external_writes_performed": False,
            "fail_open": True,
        }
    repository_scope_check = _repository_scope_check(normalised, repo_path=repo_path)
    repository_context = _repository_context_advisory_packet(
        normalised,
        repository_revision=repository_revision,
        repository_scope_check=repository_scope_check,
    )
    if not normalised["enabled"]:
        return {
            "schema_version": "issue_fix_repository_memory_sync_v0",
            "ok": False,
            "status": "disabled",
            "provider": normalised["provider"],
            "namespace": normalised["namespace"],
            "repository_revision": repository_revision,
            "requested_reference_count": 0,
            "external_writes_performed": False,
            "fail_open": True,
            "repository_context": repository_context,
        }
    if repository_scope_check.get("matched") is False:
        return {
            "schema_version": "issue_fix_repository_memory_sync_v0",
            "ok": False,
            "status": "blocked",
            "reason_code": repository_scope_check["reason_code"],
            "provider": normalised["provider"],
            "namespace": normalised["namespace"],
            "repository_revision": repository_revision,
            "requested_reference_count": 0,
            "external_writes_performed": False,
            "fail_open": True,
            "repository_context": repository_context,
            "repository_scope_check": repository_scope_check,
        }
    scope_ref = str(normalised["scope_ref"])
    if not scope_ref.startswith("viking://resources/"):
        raise ValueError("issue-fix repository sync requires a resources scope")
    if len(references) > MAX_PROVIDER_SYNC_REFERENCES:
        raise ValueError(
            f"sync supports at most {MAX_PROVIDER_SYNC_REFERENCES} references"
        )
    bounded = list(dict.fromkeys(str(reference) for reference in references))
    if not bounded:
        raise ValueError("at least one repository reference is required")
    root = Path(repo_path).expanduser().resolve()
    configured_references = set(normalised["resource_references"])
    if configured_references and any(
        str(PurePosixPath(reference)) not in configured_references
        for reference in bounded
    ):
        raise ValueError("sync references must be declared in resource_references")
    resources: list[tuple[str, str]] = []
    for index, raw_reference in enumerate(bounded):
        reference = PurePosixPath(
            _compact(raw_reference, field=f"references[{index}]", limit=260)
        )
        if reference.is_absolute() or ".." in reference.parts:
            raise ValueError("sync references must be repository-relative")
        source = (root / reference.as_posix()).resolve()
        try:
            source.relative_to(root)
        except ValueError as exc:
            raise ValueError("sync reference escapes the repository") from exc
        if not source.is_file():
            raise ValueError(f"sync reference is not a file: {reference.as_posix()}")
        resources.append((str(source), f"{scope_ref}/{reference.as_posix()}"))

    provider = provider or build_context_provider(normalised)
    ownership_required = normalised["revision_policy"] == "rolling_default_branch"
    ownership_before = (
        load_context_provider_service_ownership(
            normalised["service_ownership_receipt_path"],
            expected_provider=str(normalised["provider"]),
        )
        if ownership_required
        else None
    )
    if execute and ownership_before is not None and not ownership_before.verified:
        return {
            "schema_version": "issue_fix_repository_memory_sync_v0",
            "ok": False,
            "status": "blocked",
            "reason_code": ownership_before.reason_code,
            "provider": normalised["provider"],
            "namespace": normalised["namespace"],
            "repository_revision": repository_revision,
            "requested_reference_count": len(resources),
            "completed_count": 0,
            "write_count": 0,
            "pending_count": 0,
            "external_writes_performed": False,
            "fail_open": True,
            "revision_scoped": False,
            "automatic_capture_performed": False,
            "memory_writeback_performed": False,
            "repository_context": repository_context,
            "provider_service_ownership": ownership_before.public_packet(
                required=True
            ),
        }
    sync = provider.sync(
        namespace=str(normalised["namespace"]),
        resources=resources,
        timeout_seconds=float(normalised["sync_timeout_seconds"]),
        observed_at=observed_at,
        execute=execute,
    )
    packet = sync.public_packet()
    ownership_after = (
        load_context_provider_service_ownership(
            normalised["service_ownership_receipt_path"],
            expected_provider=str(normalised["provider"]),
        )
        if ownership_required
        else None
    )
    restart_detected = bool(
        ownership_before is not None
        and ownership_after is not None
        and context_provider_service_restarted(ownership_before, ownership_after)
    )
    if execute and restart_detected:
        packet.update(
            {
                "ok": False,
                "status": "partial",
                "reason_code": "provider_service_restarted",
                "retry_disposition": "restart_detected_no_resume",
            }
        )
    elif execute and ownership_after is not None and not ownership_after.verified:
        packet.update(
            {
                "ok": False,
                "status": "partial",
                "reason_code": "provider_service_ownership_lost",
                "retry_disposition": "manual_reconcile",
            }
        )
    packet.update(
        {
            "schema_version": "issue_fix_repository_memory_sync_v0",
            "repository_revision": repository_revision,
            "requested_reference_count": len(resources),
            "revision_scoped": normalised["revision_policy"] == "pinned",
            "automatic_capture_performed": False,
            "memory_writeback_performed": False,
            "repository_context": repository_context,
        }
    )
    if ownership_after is not None:
        packet["provider_service_ownership"] = ownership_after.public_packet(
            required=True,
            restart_detected=restart_detected,
            attempt_latency_ms=sync.latency_ms,
        )
    return packet


def render_issue_fix_repository_memory_sync_markdown(
    payload: Mapping[str, Any],
) -> str:
    return "\n".join(
        [
            "# Issue Fix Repository Memory Sync",
            "",
            f"- ok: `{bool(payload.get('ok'))}`",
            f"- status: `{payload.get('status')}`",
            f"- provider: `{payload.get('provider')}`",
            f"- namespace: `{payload.get('namespace')}`",
            f"- repository revision: `{payload.get('repository_revision')}`",
            f"- requested references: `{payload.get('requested_reference_count', 0)}`",
            f"- completed resources: `{payload.get('completed_count', 0)}`",
            f"- provider writes: `{payload.get('write_count', 0)}`",
            f"- external writes performed: `{bool(payload.get('external_writes_performed'))}`",
        ]
    )
