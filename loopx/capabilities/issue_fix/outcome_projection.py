"""Public issue-fix delivery evidence validation and outcome projection."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path

from ...control_plane.content_digest import ENVELOPED_SHA256_PATTERN
from .outcome_case import (
    DELIVERY_OUTCOME_STATUSES as DELIVERY_OUTCOME_STATUSES,
    DELIVERY_VALIDATION_STATUSES as DELIVERY_VALIDATION_STATUSES,
    ISSUE_FIX_DELIVERY_EVIDENCE_INPUT_SCHEMA_VERSION as ISSUE_FIX_DELIVERY_EVIDENCE_INPUT_SCHEMA_VERSION,
    ISSUE_FIX_OUTCOME_CASE_SCHEMA_VERSION as ISSUE_FIX_OUTCOME_CASE_SCHEMA_VERSION,
    ISSUE_FIX_OUTCOME_COLLECTION_PROJECTION_SCHEMA_VERSION as ISSUE_FIX_OUTCOME_COLLECTION_PROJECTION_SCHEMA_VERSION,
    ISSUE_FIX_OUTCOME_PROJECTION_SCHEMA_VERSION as ISSUE_FIX_OUTCOME_PROJECTION_SCHEMA_VERSION,
    ISSUE_FIX_REPOSITORY_LEARNING_CARD_INPUT_SCHEMA_VERSION as ISSUE_FIX_REPOSITORY_LEARNING_CARD_INPUT_SCHEMA_VERSION,
    ISSUE_FIX_REUSABLE_KNOWLEDGE_INPUT_SCHEMA_VERSION as ISSUE_FIX_REUSABLE_KNOWLEDGE_INPUT_SCHEMA_VERSION,
    _coerce_mapping_tree,
    _context_tags as _context_tags,
    _evidence_summary as _evidence_summary,
    _mapping as _mapping,
    _object_list,
    _repo_relative_files as _repo_relative_files,
    _result as _result,
    _safe_text as _safe_text,
    _safe_text_list as _safe_text_list,
    _stage_and_card_status as _stage_and_card_status,
    build_outcome_case_record,
    render_issue_fix_outcome_projection_markdown as render_issue_fix_outcome_projection_markdown,
    validate_issue_fix_outcome_collection_projection as validate_issue_fix_outcome_collection_projection,
    validate_issue_fix_outcome_projection as validate_issue_fix_outcome_projection,
)
from .outcome_collection import (
    IssueFixOutcomeCollectionProjection as IssueFixOutcomeCollectionProjection,
    _build_lifecycle_only_outcome as _build_lifecycle_only_outcome,
    _lifecycle_recency as _lifecycle_recency,
    _load_domain_packets as _load_domain_packets,
    collect_issue_fix_outcomes_from_domain_state,
)
from .repository_commit_evidence import (
    ISSUE_FIX_REPOSITORY_COMMIT_EVIDENCE_SCHEMA_VERSION as ISSUE_FIX_REPOSITORY_COMMIT_EVIDENCE_SCHEMA_VERSION,
)

_REPOSITORY_FINGERPRINT_PATTERN = ENVELOPED_SHA256_PATTERN
_COMMIT_OID_PATTERN = re.compile(r"[0-9a-fA-F]{40,64}")
_RECOVERY_REF_PATTERN = re.compile(
    r"refs/(?:heads|remotes|tags)/[A-Za-z0-9][A-Za-z0-9._/-]{0,180}"
)


def _repository_commit_evidence(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("repository_commit_evidence must be an object")
    if (
        value.get("schema_version")
        != ISSUE_FIX_REPOSITORY_COMMIT_EVIDENCE_SCHEMA_VERSION
    ):
        raise ValueError(
            "repository_commit_evidence schema_version must be "
            "issue_fix_repository_commit_evidence_v0"
        )
    evidence: dict[str, object] = {
        "schema_version": ISSUE_FIX_REPOSITORY_COMMIT_EVIDENCE_SCHEMA_VERSION,
        "status": _safe_text(
            value.get("status"), field="commit evidence status", limit=20
        ),
        "repo": _safe_text(value.get("repo"), field="commit evidence repo", limit=180),
        "repository_fingerprint": _safe_text(
            value.get("repository_fingerprint"),
            field="repository fingerprint",
            limit=80,
        ),
        "declared_repository_revision": _safe_text(
            value.get("declared_repository_revision")
            or value.get("repository_revision"),
            field="declared repository revision",
            limit=80,
        ),
        "repository_revision": _safe_text(
            value.get("repository_revision"),
            field="commit evidence repository revision",
            limit=80,
        ),
        "declared_commit_ref": _safe_text(
            value.get("declared_commit_ref"),
            field="declared commit ref",
            limit=80,
        ),
        "commit_oid": _safe_text(value.get("commit_oid"), field="commit oid", limit=80),
        "recovery_ref": _safe_text(
            value.get("recovery_ref"), field="recovery ref", limit=200
        ),
        "recovery_ref_oid": _safe_text(
            value.get("recovery_ref_oid"), field="recovery ref oid", limit=80
        ),
        "commit_is_ancestor": value.get("commit_is_ancestor") is True,
        "verified_at": _safe_text(
            value.get("verified_at"), field="commit evidence verified_at", limit=80
        ),
        "repo_path_captured": value.get("repo_path_captured") is True,
        "remote_urls_captured": value.get("remote_urls_captured") is True,
        "raw_git_output_captured": value.get("raw_git_output_captured") is True,
    }
    if evidence["status"] != "verified":
        raise ValueError("repository_commit_evidence status must be verified")
    if not _REPOSITORY_FINGERPRINT_PATTERN.fullmatch(
        str(evidence["repository_fingerprint"])
    ):
        raise ValueError("repository_commit_evidence fingerprint must use sha256")
    for field in ("repository_revision", "commit_oid", "recovery_ref_oid"):
        if not _COMMIT_OID_PATTERN.fullmatch(str(evidence[field])):
            raise ValueError(f"repository_commit_evidence {field} must be a commit oid")
    if not _RECOVERY_REF_PATTERN.fullmatch(str(evidence["recovery_ref"])):
        raise ValueError(
            "repository_commit_evidence recovery_ref must be a full git ref"
        )
    if evidence["recovery_ref_oid"] != evidence["repository_revision"]:
        raise ValueError(
            "repository_commit_evidence recovery ref must pin the revision"
        )
    if not evidence["commit_is_ancestor"]:
        raise ValueError("repository_commit_evidence must prove commit ancestry")
    if any(
        evidence[field]
        for field in (
            "repo_path_captured",
            "remote_urls_captured",
            "raw_git_output_captured",
        )
    ):
        raise ValueError(
            "repository_commit_evidence must not capture local or raw git data"
        )
    return evidence


def _commit_evidence_status(
    delivery: Mapping[str, object], *, repo: str, repository_revision: str
) -> tuple[str, str | None]:
    commit_ref = str(delivery.get("commit_ref") or "").strip()
    verification_required = bool(
        commit_ref
        and (
            delivery.get("validation_status") == "passed"
            or delivery.get("outcome_status") == "completed"
        )
    )
    if not verification_required:
        return "not_required", None
    evidence = delivery.get("repository_commit_evidence")
    if not isinstance(evidence, Mapping):
        return "unverified", "repository_commit_evidence_missing"
    expected = {
        "repo": repo,
        "declared_repository_revision": repository_revision,
        "declared_commit_ref": commit_ref,
    }
    if any(str(evidence.get(key) or "") != value for key, value in expected.items()):
        return "unverified", "repository_commit_evidence_mismatch"
    return "verified", None


def _reusable_knowledge(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("reusable_knowledge must be an object")
    schema_version = str(value.get("schema_version") or "").strip()
    if schema_version not in {
        ISSUE_FIX_REUSABLE_KNOWLEDGE_INPUT_SCHEMA_VERSION,
        ISSUE_FIX_REPOSITORY_LEARNING_CARD_INPUT_SCHEMA_VERSION,
    }:
        raise ValueError(
            "reusable_knowledge schema_version must be a supported reusable "
            "knowledge or repository learning-card input"
        )
    compact: dict[str, object] = {
        "schema_version": schema_version,
        "symptom_signature": _safe_text(
            value.get("symptom_signature"),
            field="reusable_knowledge.symptom_signature",
            limit=320,
        ),
        "reproduction_contract": _safe_text(
            value.get("reproduction_contract"),
            field="reusable_knowledge.reproduction_contract",
            limit=420,
        ),
        "root_cause": _safe_text(
            value.get("root_cause"),
            field="reusable_knowledge.root_cause",
            limit=420,
        ),
        "violated_invariant": _safe_text(
            value.get("violated_invariant"),
            field="reusable_knowledge.violated_invariant",
            limit=320,
        ),
        "repair_pattern": _safe_text(
            value.get("repair_pattern"),
            field="reusable_knowledge.repair_pattern",
            limit=420,
        ),
        "validation_contract": _safe_text(
            value.get("validation_contract"),
            field="reusable_knowledge.validation_contract",
            limit=420,
        ),
        "applicability": _safe_text(
            value.get("applicability"),
            field="reusable_knowledge.applicability",
            limit=320,
        ),
        "non_applicability": _safe_text(
            value.get("non_applicability"),
            field="reusable_knowledge.non_applicability",
            limit=320,
        ),
        "verification_references": _repo_relative_files(
            value.get("verification_references")
        ),
    }
    if schema_version == ISSUE_FIX_REPOSITORY_LEARNING_CARD_INPUT_SCHEMA_VERSION:
        confidence = _safe_text(
            value.get("confidence"),
            field="reusable_knowledge.confidence",
            limit=20,
        ).lower()
        if confidence not in {"high", "medium", "low"}:
            raise ValueError(
                "reusable_knowledge.confidence must be high, medium, or low"
            )
        if value.get("current_checkout_verification_required") is not True:
            raise ValueError(
                "repository learning cards require current checkout verification"
            )
        compact.update(
            {
                "confidence": confidence,
                "affected_modules": _repo_relative_files(
                    value.get("affected_modules")
                ),
                "invalidation_conditions": _safe_text_list(
                    value.get("invalidation_conditions"),
                    field="reusable_knowledge.invalidation_conditions",
                    limit=320,
                    count_limit=8,
                ),
                "revalidation_contract": _safe_text(
                    value.get("revalidation_contract"),
                    field="reusable_knowledge.revalidation_contract",
                    limit=420,
                ),
                "current_checkout_verification_required": True,
            }
        )
    missing = [
        key for key, item in compact.items() if key != "schema_version" and not item
    ]
    if missing:
        raise ValueError(
            "reusable_knowledge requires non-empty reusable fields: "
            + ", ".join(missing)
        )
    return compact


def _optional_legacy_sequence(value: object) -> object:
    # Preserve v0 compatibility where empty-string fields stood in for absent lists.
    if value is None or (isinstance(value, str) and value == ""):
        return []
    return value


def _delivery_evidence(value: Mapping[str, object] | None) -> dict[str, object]:
    if value is None:
        return {
            "provided": False,
            "outcome_status": "in_progress",
            "validation_status": None,
            "changed_files": [],
            "commit_ref": None,
            "repository_commit_evidence": None,
            "outputs": [],
            "risks": [],
        }
    if not isinstance(value, Mapping):
        raise ValueError("delivery evidence must be an object")
    schema_version = str(value.get("schema_version") or "").strip()
    if schema_version != ISSUE_FIX_DELIVERY_EVIDENCE_INPUT_SCHEMA_VERSION:
        raise ValueError(
            "delivery evidence schema_version must be "
            "issue_fix_delivery_evidence_input_v0"
        )
    validation_status = str(value.get("validation_status") or "not_run").strip()
    if validation_status not in DELIVERY_VALIDATION_STATUSES:
        raise ValueError(
            "delivery evidence validation_status must be one of "
            f"{sorted(DELIVERY_VALIDATION_STATUSES)}"
        )
    outcome_status = str(value.get("outcome_status") or "in_progress").strip()
    if outcome_status not in DELIVERY_OUTCOME_STATUSES:
        raise ValueError(
            "delivery evidence outcome_status must be one of "
            f"{sorted(DELIVERY_OUTCOME_STATUSES)}"
        )
    commit_ref = _safe_text(value.get("commit_ref"), field="commit_ref", limit=80)
    if commit_ref and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{3,79}", commit_ref):
        raise ValueError("commit_ref must be a compact public-safe git reference")
    outputs: list[dict[str, str]] = []
    raw_outputs = _optional_legacy_sequence(value.get("outputs"))
    if not isinstance(raw_outputs, Sequence) or isinstance(raw_outputs, (str, bytes)):
        raise ValueError("delivery evidence outputs must be a list")
    for index, item in enumerate(raw_outputs[:10]):
        if not isinstance(item, Mapping):
            raise ValueError(f"delivery evidence outputs[{index}] must be an object")
        kind = _safe_text(item.get("kind"), field=f"outputs[{index}].kind", limit=60)
        url = _safe_text(item.get("url"), field=f"outputs[{index}].url", limit=320)
        if url and not url.startswith("https://"):
            raise ValueError("delivery evidence output URLs must use https")
        if kind or url:
            outputs.append({"kind": kind or "artifact", "url": url})
    return {
        "provided": True,
        "outcome_status": outcome_status,
        "validation_status": validation_status,
        "validation_label": _safe_text(
            value.get("validation_label"), field="validation_label", limit=260
        ),
        "changed_files": _repo_relative_files(
            _optional_legacy_sequence(value.get("changed_files"))
        ),
        "commit_ref": commit_ref or None,
        "repository_commit_evidence": _repository_commit_evidence(
            value.get("repository_commit_evidence")
        ),
        "outputs": outputs,
        "risks": _safe_text_list(
            _optional_legacy_sequence(value.get("risks")),
            field="risks",
            limit=260,
            count_limit=10,
        ),
        "recorded_at": _safe_text(
            value.get("recorded_at"), field="recorded_at", limit=80
        )
        or None,
        "reusable_knowledge": _reusable_knowledge(value.get("reusable_knowledge")),
    }


def compact_issue_fix_delivery_evidence(
    value: Mapping[str, object],
    *,
    recorded_at: str | None = None,
) -> dict[str, object]:
    """Validate and compact delivery evidence before domain-state writeback."""

    delivery = _delivery_evidence(value)
    compact: dict[str, object] = {
        "schema_version": ISSUE_FIX_DELIVERY_EVIDENCE_INPUT_SCHEMA_VERSION,
        "outcome_status": delivery["outcome_status"],
        "validation_status": delivery["validation_status"],
        "validation_label": delivery.get("validation_label") or "",
        "changed_files": _object_list(delivery.get("changed_files")),
        "commit_ref": delivery.get("commit_ref"),
        "outputs": _object_list(delivery.get("outputs")),
        "risks": _object_list(delivery.get("risks")),
    }
    commit_evidence = delivery.get("repository_commit_evidence")
    if isinstance(commit_evidence, Mapping):
        compact["repository_commit_evidence"] = _coerce_mapping_tree(commit_evidence)
    reusable_knowledge = delivery.get("reusable_knowledge")
    if isinstance(reusable_knowledge, Mapping):
        compact["reusable_knowledge"] = _coerce_mapping_tree(reusable_knowledge)
    effective_recorded_at = recorded_at or delivery.get("recorded_at")
    if effective_recorded_at:
        compact["recorded_at"] = _safe_text(
            effective_recorded_at,
            field="delivery evidence recorded_at",
            limit=80,
        )
    return compact


def build_issue_fix_outcome_projection(
    *,
    goal_id: str,
    feasibility_packet: Mapping[str, object],
    pr_lifecycle_packet: Mapping[str, object] | None = None,
    delivery_evidence_input: Mapping[str, object] | None = None,
    agent_id: str | None = None,
    generated_at: str | None = None,
) -> dict[str, object]:
    """Compose one public-safe issue-fix case view without writing source state."""

    if not isinstance(feasibility_packet, Mapping):
        raise ValueError("feasibility packet must be an object")
    feasibility_observation = _mapping(feasibility_packet.get("observation"))
    feasibility_decision = _mapping(feasibility_packet.get("decision"))
    if feasibility_packet.get("schema_version") != "issue_fix_feasibility_v0":
        raise ValueError("feasibility packet must use issue_fix_feasibility_v0")
    repo = _safe_text(feasibility_observation.get("repo"), field="repo", limit=180)
    issue_ref = _safe_text(
        feasibility_observation.get("issue_ref"), field="issue_ref", limit=180
    )
    if not repo or not issue_ref:
        raise ValueError("feasibility packet must identify repo and issue_ref")
    route = str(feasibility_decision.get("route") or "").strip()
    if route not in {"fix_pr", "comment_only", "triage_only"}:
        raise ValueError("feasibility packet must select a supported route")

    lifecycle_observation: dict[str, object] = {}
    lifecycle_transition: dict[str, object] = {}
    if pr_lifecycle_packet is not None:
        if not isinstance(pr_lifecycle_packet, Mapping):
            raise ValueError("PR lifecycle packet must be an object")
        if (
            pr_lifecycle_packet.get("schema_version")
            != "issue_fix_pr_lifecycle_monitor_v0"
        ):
            raise ValueError(
                "PR lifecycle packet must use issue_fix_pr_lifecycle_monitor_v0"
            )
        lifecycle_observation = _mapping(pr_lifecycle_packet.get("observation"))
        lifecycle_transition = _mapping(pr_lifecycle_packet.get("transition"))
        lifecycle_repo = str(lifecycle_observation.get("repo") or "").strip()
        if lifecycle_repo != repo:
            raise ValueError(
                "feasibility and PR lifecycle packets must use the same repo"
            )

    repository_context = _mapping(feasibility_observation.get("repository_context"))
    context_effect = _mapping(feasibility_packet.get("repository_context_effect"))
    delivery = _delivery_evidence(delivery_evidence_input)
    repository_revision = _safe_text(
        repository_context.get("repository_revision"),
        field="repository revision",
        limit=80,
    )
    commit_evidence_status, commit_evidence_reason = _commit_evidence_status(
        delivery,
        repo=repo,
        repository_revision=repository_revision,
    )
    effective_validation_status = (
        "unverified"
        if commit_evidence_status == "unverified"
        else delivery.get("validation_status")
    )
    effective_delivery_outcome_status = (
        "blocked"
        if commit_evidence_status == "unverified"
        else str(delivery.get("outcome_status") or "in_progress")
    )
    reproduction_status = str(
        feasibility_observation.get("reproduction_status") or "missing"
    ).strip()
    stage, card_status, priority = _stage_and_card_status(
        route=route,
        reproduction_status=reproduction_status,
        delivery_outcome_status=effective_delivery_outcome_status,
        commit_evidence_status=commit_evidence_status,
        lifecycle_observation=lifecycle_observation,
    )
    issue_url = _safe_text(
        feasibility_observation.get("permalink"), field="issue permalink", limit=320
    )
    pr_url = _safe_text(
        lifecycle_observation.get("permalink"), field="PR permalink", limit=320
    )
    issue_number = feasibility_observation.get("number")
    pr_number = lifecycle_observation.get("number")
    title = f"[{route}] {repo} #{issue_number or issue_ref}"
    if pr_number:
        title += f" -> PR #{pr_number}"
    validation_label = str(
        delivery.get("validation_label")
        or _safe_text(
            feasibility_observation.get("validation_label"),
            field="validation label",
            limit=260,
        )
    )
    validation_status = str(
        effective_validation_status or ("declared" if validation_label else "unknown")
    )
    repository_context_status = (
        _safe_text(
            repository_context.get("context_status")
            or context_effect.get("context_status"),
            field="context status",
            limit=80,
        )
        or "unknown"
    )
    raw_changed = _object_list(delivery.get("changed_files"))
    changed_files = [str(item) for item in raw_changed]
    feasibility_transition = _mapping(feasibility_packet.get("transition"))
    projected_todo = _mapping(feasibility_transition.get("projected_todo"))
    next_action = _safe_text(
        lifecycle_transition.get("reason")
        or projected_todo.get("text")
        or feasibility_transition.get("decision"),
        field="next action",
        limit=320,
    )
    if commit_evidence_status == "unverified":
        next_action = (
            "Re-resolve commit_ref in the declared repository and record a "
            "recoverable branch, tag, or remote ref before publication."
        )
    case = build_outcome_case_record(
        goal_id=goal_id,
        repo=repo,
        issue_ref=issue_ref,
        route=route,
        stage=stage,
        card_status=card_status,
        priority=priority,
        agent_id=agent_id,
        title=title,
        next_action=next_action,
        issue_number=issue_number,
        issue_url=issue_url,
        repository_revision=repository_revision,
        repository_context=repository_context,
        context_effect=context_effect,
        reproduction_status=reproduction_status,
        feasibility_observation=feasibility_observation,
        validation_status=validation_status,
        validation_label=validation_label,
        delivery=delivery,
        effective_delivery_outcome_status=effective_delivery_outcome_status,
        commit_evidence_status=commit_evidence_status,
        commit_evidence_reason=commit_evidence_reason,
        changed_files=changed_files,
        lifecycle_observation=lifecycle_observation,
        pr_number=pr_number,
        pr_url=pr_url,
        repository_context_status=repository_context_status,
    )
    packet: dict[str, object] = {
        "ok": True,
        "schema_version": ISSUE_FIX_OUTCOME_PROJECTION_SCHEMA_VERSION,
        "mode": "issue-fix-outcome",
        "source_id": "issue-fix-outcome",
        "goal_id": goal_id,
        "generated_at": generated_at,
        "agent_identity": {"agent_id": agent_id} if agent_id else {},
        "issue_fix_outcomes": [case],
        "source_contract": {
            "feasibility": "issue_fix_feasibility_v0",
            "pr_lifecycle": (
                "issue_fix_pr_lifecycle_monitor_v0"
                if pr_lifecycle_packet is not None
                else None
            ),
            "delivery_evidence": (
                ISSUE_FIX_DELIVERY_EVIDENCE_INPUT_SCHEMA_VERSION
                if delivery_evidence_input is not None
                else None
            ),
            "repository_commit_evidence": (
                ISSUE_FIX_REPOSITORY_COMMIT_EVIDENCE_SCHEMA_VERSION
                if delivery.get("repository_commit_evidence") is not None
                else None
            ),
            "writes_source_state": False,
            "creates_parallel_state_machine": False,
        },
        "external_reads_performed": False,
        "external_writes_performed": False,
        "raw_logs_captured": False,
        "local_paths_captured": False,
        "credentials_captured": False,
    }
    validation = validate_issue_fix_outcome_projection(packet)
    packet["validation"] = validation
    packet["ok"] = bool(validation["ok"])
    return packet


def build_issue_fix_outcome_collection_from_domain_state(
    *,
    goal_id: str,
    project: str | Path = ".",
    agent_id: str | None = None,
    generated_at: str | None = None,
) -> IssueFixOutcomeCollectionProjection:
    """Derive all goal issue outcomes from existing compact domain state.

    Feasibility rows are the issue inventory. A PR lifecycle row enriches an
    issue only when its observation carries the same explicit ``issue_ref``.
    Lifecycle rows without a matching feasibility row remain visible as
    PR-only outcomes; branch names, titles, and issue numbers are never
    guessed. No source state or parallel outcome ledger is written.
    """

    return collect_issue_fix_outcomes_from_domain_state(
        goal_id=goal_id,
        project=project,
        agent_id=agent_id,
        generated_at=generated_at,
        project_outcome=build_issue_fix_outcome_projection,
    )
