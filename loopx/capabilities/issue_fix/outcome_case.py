"""Schema constants, validation, markdown rendering, and case record builders."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath

from ...control_plane.runtime.public_safety import public_safe_compact_text
from .pr_lifecycle import BRANCH_REPLAN_MERGE_STATES

ISSUE_FIX_OUTCOME_PROJECTION_SCHEMA_VERSION = "issue_fix_outcome_projection_v0"
ISSUE_FIX_OUTCOME_CASE_SCHEMA_VERSION = "issue_fix_outcome_case_v0"
ISSUE_FIX_OUTCOME_COLLECTION_PROJECTION_SCHEMA_VERSION = (
    "issue_fix_outcome_collection_projection_v0"
)
ISSUE_FIX_DELIVERY_EVIDENCE_INPUT_SCHEMA_VERSION = (
    "issue_fix_delivery_evidence_input_v0"
)
ISSUE_FIX_REUSABLE_KNOWLEDGE_INPUT_SCHEMA_VERSION = (
    "issue_fix_reusable_knowledge_input_v0"
)
ISSUE_FIX_REPOSITORY_LEARNING_CARD_INPUT_SCHEMA_VERSION = (
    "issue_fix_repository_learning_card_input_v0"
)

DELIVERY_VALIDATION_STATUSES = {"passed", "failed", "partial", "not_run"}
DELIVERY_OUTCOME_STATUSES = {"in_progress", "completed", "blocked"}


def _coerce_mapping_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _coerce_mapping_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_coerce_mapping_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_coerce_mapping_value(item) for item in value)
    return value


def _coerce_mapping_tree(mapping: Mapping[str, object]) -> dict[str, object]:
    return {str(key): _coerce_mapping_value(item) for key, item in mapping.items()}


def _mapping(value: object) -> dict[str, object]:
    return _coerce_mapping_tree(value) if isinstance(value, Mapping) else {}


def _object_list(value: object) -> list[object]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return list(value)
    return []


def _safe_text(value: object, *, field: str, limit: int = 260) -> str:
    if value is None:
        return ""
    text = public_safe_compact_text(value, limit=limit)
    if str(value).strip() and not text:
        raise ValueError(f"{field} must be a compact public-safe value")
    return text or ""


def _safe_text_list(
    value: object,
    *,
    field: str,
    limit: int = 220,
    count_limit: int = 20,
) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{field} must be a list")
    return [
        text
        for index, item in enumerate(value)
        if (text := _safe_text(item, field=f"{field}[{index}]", limit=limit))
    ][:count_limit]


def _repo_relative_files(value: object) -> list[str]:
    files = _safe_text_list(value, field="changed_files", limit=260)
    for path in files:
        pure = PurePosixPath(path)
        if (
            path.startswith(("/", "~"))
            or "\\" in path
            or pure.is_absolute()
            or ".." in pure.parts
        ):
            raise ValueError("changed_files must contain only repo-relative paths")
    return files


def _stage_and_card_status(
    *,
    route: str,
    reproduction_status: str,
    delivery_outcome_status: str,
    commit_evidence_status: str,
    lifecycle_observation: Mapping[str, object],
) -> tuple[str, str, str]:
    if lifecycle_observation:
        state = str(lifecycle_observation.get("state") or "OPEN").upper()
        checks = _mapping(lifecycle_observation.get("checks"))
        checks_aggregate = str(checks.get("aggregate") or "UNKNOWN").upper()
        review_decision = str(
            lifecycle_observation.get("review_decision") or "UNKNOWN"
        ).upper()
        merge_state = str(
            lifecycle_observation.get("merge_state_status") or "UNKNOWN"
        ).upper()
        if state == "MERGED":
            return "merged", "done", "P1"
        if state == "CLOSED":
            return "closed_without_merge", "done", "P1"
        if commit_evidence_status == "unverified":
            return "delivery_evidence_unverified", "blocked", "P1"
        if delivery_outcome_status == "blocked":
            return "delivery_blocked", "blocked", "P0"
        if checks_aggregate == "FAILING":
            return "ci_failed", "blocked", "P0"
        if review_decision == "CHANGES_REQUESTED":
            return "changes_requested", "blocked", "P0"
        if merge_state in BRANCH_REPLAN_MERGE_STATES:
            return "branch_stale_or_conflicted", "blocked", "P1"
        if lifecycle_observation.get("is_draft") is True:
            return "draft_pr", "open", "P1"
        if checks_aggregate == "PENDING":
            return "ci_pending", "open", "P2"
        if review_decision == "APPROVED" and checks_aggregate == "PASSING":
            return "merge_ready", "open", "P1"
        if review_decision == "REVIEW_REQUIRED":
            return "review_wait", "open", "P2"
        return "pr_open", "open", "P2"
    if route == "triage_only":
        return "triage_complete", "done", "P1"
    if commit_evidence_status == "unverified":
        return "delivery_evidence_unverified", "blocked", "P1"
    if route == "comment_only":
        if delivery_outcome_status == "completed":
            return "comment_published", "done", "P1"
        if delivery_outcome_status == "blocked":
            return "comment_blocked", "blocked", "P1"
        return "comment_packet", "open", "P1"
    if delivery_outcome_status == "blocked":
        return "fix_blocked", "blocked", "P0"
    if delivery_outcome_status == "completed":
        return "fix_review_ready", "open", "P1"
    if reproduction_status == "planned":
        return "reproduction_planned", "open", "P0"
    if reproduction_status in {"missing", "blocked"}:
        return "reproduction_blocked", "blocked", "P0"
    return "fix_in_progress", "open", "P0"


def _context_tags(
    *,
    route: str,
    stage: str,
    reproduction_status: str,
    validation_status: str,
    repository_context_status: str,
    changed_files: Sequence[str],
    commit_evidence_status: str,
) -> list[str]:
    """Build bounded, filterable reviewer context without free-form prose."""

    tags = [
        route,
        stage,
        f"reproduction_{reproduction_status}",
        f"validation_{validation_status}",
    ]
    if any(
        PurePosixPath(path).parts and PurePosixPath(path).parts[0] in {"test", "tests"}
        for path in changed_files
    ):
        tags.append("tests_changed")
    if len(changed_files) > 1:
        tags.append("multi_file")
    if repository_context_status == "grounded":
        tags.append("repository_context_grounded")
    if commit_evidence_status != "not_required":
        tags.append(f"commit_{commit_evidence_status}")
    return list(dict.fromkeys(tags))


def _result(
    *,
    route: str,
    stage: str,
    issue_url: str,
    pr_url: str,
    delivery: Mapping[str, object],
) -> dict[str, object]:
    terminal = stage in {
        "merged",
        "closed_without_merge",
        "triage_complete",
        "comment_published",
    }
    if stage == "merged":
        kind = "fix_pr_merged"
    elif stage == "closed_without_merge":
        kind = "pr_closed_without_merge"
    elif stage == "triage_complete":
        kind = "triage_complete"
    elif stage == "comment_published":
        kind = "useful_comment_published"
    elif route == "comment_only":
        kind = "comment_packet_pending_publication"
    elif pr_url:
        kind = "fix_pr_open"
    else:
        kind = "fix_in_progress"
    outputs = _object_list(delivery.get("outputs"))
    known_urls = {
        str(item.get("url") or "") for item in outputs if isinstance(item, Mapping)
    }
    for output_kind, url in (("issue", issue_url), ("pull_request", pr_url)):
        if url and url not in known_urls:
            outputs.append({"kind": output_kind, "url": url})
    return {
        "kind": kind,
        "terminal": terminal,
        "public_outputs": outputs,
    }


def _evidence_summary(case: Mapping[str, object]) -> str:
    context = _mapping(case.get("repository_context"))
    reproduction = _mapping(case.get("reproduction"))
    validation = _mapping(case.get("validation"))
    pull_request = _mapping(case.get("pull_request"))
    issue = _mapping(case.get("issue"))
    delivery = _mapping(case.get("delivery"))
    result = _mapping(case.get("result"))
    checks = _mapping(pull_request.get("checks"))
    parts = [
        f"stage={case.get('stage')}",
        f"route={case.get('route')}",
        f"revision={context.get('revision') or 'unknown'}",
        f"context={context.get('fingerprint') or 'unknown'}",
        f"reproduction={reproduction.get('status') or 'unknown'}",
        f"validation={validation.get('status') or 'unknown'}",
    ]
    if issue.get("url"):
        parts.append(f"issue={issue.get('url')}")
    if delivery.get("commit_ref"):
        parts.append(f"commit={delivery.get('commit_ref')}")
        parts.append(
            f"commit_integrity={delivery.get('commit_evidence_status') or 'unverified'}"
        )
    changed_files = _object_list(delivery.get("changed_files"))
    if changed_files:
        parts.append(f"changed_files={','.join(str(item) for item in changed_files)}")
    if pull_request:
        parts.extend(
            [
                f"pr_state={pull_request.get('state') or 'unknown'}",
                f"checks={checks.get('aggregate') or 'unknown'}",
                f"review={pull_request.get('review_decision') or 'unknown'}",
                f"merge_state={pull_request.get('merge_state_status') or 'unknown'}",
                f"pr={pull_request.get('url') or 'unknown'}",
            ]
        )
    output_urls = [
        str(item.get("url") or "")
        for item in _object_list(result.get("public_outputs"))
        if isinstance(item, Mapping) and item.get("url")
    ]
    if output_urls:
        parts.append(f"outputs={','.join(output_urls)}")
    risks = _object_list(case.get("risks"))
    if risks:
        parts.append(f"risks={' | '.join(str(item) for item in risks)}")
    return "; ".join(parts)


def build_outcome_case_record(
    *,
    goal_id: str,
    repo: str,
    issue_ref: str,
    route: str,
    stage: str,
    card_status: str,
    priority: str,
    agent_id: str | None,
    title: str,
    next_action: str,
    issue_number: object,
    issue_url: str,
    repository_revision: str,
    repository_context: Mapping[str, object],
    context_effect: Mapping[str, object],
    reproduction_status: str,
    feasibility_observation: Mapping[str, object],
    validation_status: str,
    validation_label: str,
    delivery: Mapping[str, object],
    effective_delivery_outcome_status: str,
    commit_evidence_status: str,
    commit_evidence_reason: str | None,
    changed_files: list[str],
    lifecycle_observation: Mapping[str, object],
    pr_number: object,
    pr_url: str,
    repository_context_status: str,
) -> dict[str, object]:
    case: dict[str, object] = {
        "schema_version": ISSUE_FIX_OUTCOME_CASE_SCHEMA_VERSION,
        "outcome_id": f"{repo}:{issue_ref}",
        "title": title,
        "summary": f"{stage}: {next_action}" if next_action else stage,
        "goal_id": goal_id,
        "repo": repo,
        "issue_ref": issue_ref,
        "route": route,
        "stage": stage,
        "status": card_status,
        "priority": priority,
        "task_class": "continuous_monitor" if not card_status == "done" else "advancement_task",
        "action_kind": "issue_fix_outcome",
        "claimed_by": _safe_text(agent_id, field="agent_id", limit=120) or None,
        "issue": {
            "number": issue_number,
            "url": issue_url,
        },
        "repository_context": {
            "revision": repository_revision or None,
            "fingerprint": _safe_text(
                repository_context.get("context_fingerprint")
                or context_effect.get("context_fingerprint"),
                field="context fingerprint",
                limit=80,
            )
            or None,
            "status": _safe_text(
                repository_context.get("context_status")
                or context_effect.get("context_status"),
                field="context status",
                limit=80,
            )
            or "unknown",
        },
        "reproduction": {
            "status": reproduction_status,
            "label": _safe_text(
                feasibility_observation.get("reproduction_label"),
                field="reproduction label",
                limit=260,
            )
            or None,
            "evidence_refs": _object_list(
                context_effect.get("reproduction_evidence_refs")
            ),
        },
        "validation": {
            "status": validation_status,
            "label": validation_label or None,
            "evidence_refs": _object_list(
                context_effect.get("validation_evidence_refs")
            ),
        },
        "delivery": {
            "evidence_provided": delivery.get("provided") is True,
            "outcome_status": effective_delivery_outcome_status,
            "reported_outcome_status": delivery.get("outcome_status"),
            "reported_validation_status": delivery.get("validation_status"),
            "commit_ref": delivery.get("commit_ref"),
            "commit_evidence_status": commit_evidence_status,
            "commit_evidence_reason": commit_evidence_reason,
            "repository_commit_evidence": delivery.get("repository_commit_evidence"),
            "changed_files": changed_files,
            "recorded_at": delivery.get("recorded_at"),
        },
        "reusable_knowledge": delivery.get("reusable_knowledge"),
        "pull_request": (
            {
                "number": pr_number,
                "ref": lifecycle_observation.get("pr_ref"),
                "url": pr_url,
                "state": lifecycle_observation.get("state"),
                "is_draft": lifecycle_observation.get("is_draft") is True,
                "checks": _mapping(lifecycle_observation.get("checks")),
                "review_decision": lifecycle_observation.get("review_decision"),
                "merge_state_status": lifecycle_observation.get("merge_state_status"),
                "merged_at": lifecycle_observation.get("merged_at"),
                "closed_at": lifecycle_observation.get("closed_at"),
            }
            if lifecycle_observation
            else None
        ),
        "result": _result(
            route=route,
            stage=stage,
            issue_url=issue_url,
            pr_url=pr_url,
            delivery=delivery,
        ),
        "risks": _object_list(delivery.get("risks")),
        "context_tags": _context_tags(
            route=route,
            stage=stage,
            reproduction_status=reproduction_status,
            validation_status=validation_status,
            repository_context_status=repository_context_status,
            changed_files=changed_files,
            commit_evidence_status=commit_evidence_status,
        ),
        "next_action": next_action or None,
        "handoff": next_action or "No further action projected.",
        "scope": "issue_fix_outcome",
        "_include_done_by_default": True,
    }
    case["evidence"] = _evidence_summary(case)
    return case


def validate_issue_fix_outcome_collection_projection(
    packet: Mapping[str, object],
) -> dict[str, object]:
    errors: list[str] = []
    if not isinstance(packet, Mapping):
        raise ValueError("outcome collection projection must be an object")
    if (
        packet.get("schema_version")
        != ISSUE_FIX_OUTCOME_COLLECTION_PROJECTION_SCHEMA_VERSION
    ):
        errors.append(
            "schema_version must be issue_fix_outcome_collection_projection_v0"
        )
    raw_outcomes = packet.get("issue_fix_outcomes")
    if not isinstance(raw_outcomes, list):
        errors.append("issue_fix_outcomes must be a list")
        outcomes: list[object] = []
    else:
        outcomes = raw_outcomes
    outcome_ids = [
        str(item.get("outcome_id") or "")
        for item in outcomes
        if isinstance(item, Mapping)
    ]
    if any(not value for value in outcome_ids):
        errors.append("every issue-fix outcome must have an outcome_id")
    if len(set(outcome_ids)) != len(outcome_ids):
        errors.append("issue-fix outcome ids must be unique")
    for key in (
        "external_reads_performed",
        "external_writes_performed",
        "raw_logs_captured",
        "local_paths_captured",
        "credentials_captured",
    ):
        if packet.get(key) is not False:
            errors.append(f"{key} must be false")
    return {
        "schema_version": "issue_fix_outcome_collection_projection_validation_v0",
        "ok": not errors,
        "errors": errors,
    }


def validate_issue_fix_outcome_projection(
    packet: Mapping[str, object],
) -> dict[str, object]:
    errors: list[str] = []
    if not isinstance(packet, Mapping):
        raise ValueError("outcome projection must be an object")
    if packet.get("schema_version") != ISSUE_FIX_OUTCOME_PROJECTION_SCHEMA_VERSION:
        errors.append("schema_version must be issue_fix_outcome_projection_v0")
    outcomes = packet.get("issue_fix_outcomes")
    if not isinstance(outcomes, list) or len(outcomes) != 1:
        errors.append("issue_fix_outcomes must contain exactly one case")
    for key in (
        "external_reads_performed",
        "external_writes_performed",
        "raw_logs_captured",
        "local_paths_captured",
        "credentials_captured",
    ):
        if packet.get(key) is not False:
            errors.append(f"{key} must be false")
    return {
        "schema_version": "issue_fix_outcome_projection_validation_v0",
        "ok": not errors,
        "errors": errors,
    }


def render_issue_fix_outcome_projection_markdown(
    payload: Mapping[str, object],
) -> str:
    if not isinstance(payload, Mapping):
        raise ValueError("outcome projection payload must be an object")
    if payload.get("ok") is not True:
        return f"# LoopX issue-fix outcome\n\n- ok: `false`\n- error: `{payload.get('error')}`"
    outcomes = _object_list(payload.get("issue_fix_outcomes"))
    outcome = _mapping(outcomes[0] if outcomes else {})
    result = _mapping(outcome.get("result"))
    lines = [
        "# LoopX issue-fix outcome",
        "",
        f"- case: `{outcome.get('outcome_id')}`",
        f"- stage: `{outcome.get('stage')}`",
        f"- route: `{outcome.get('route')}`",
        f"- result: `{result.get('kind')}`",
        f"- evidence: {outcome.get('evidence')}",
        f"- next action: {outcome.get('next_action') or 'none'}",
    ]
    writeback = _mapping(payload.get("repository_memory_writeback"))
    if writeback:
        lines.extend(
            [
                f"- repository memory writeback: `{writeback.get('status')}`",
                f"- provider writes: `{writeback.get('write_count', 0)}`",
            ]
        )
    return "\n".join(lines)
