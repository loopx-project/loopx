"""One-way public-safe Explore bridges for Research companion workflows.

External-evidence projection requires an explicit parent admission and a
registered Goal whose Explore boundary is already enabled; it never auto-executes
external providers or grants new Goal write authority.  Deepresearch export
reads `.loopx/deepresearch/research.json` read-only, exports only sanitized
evidence references from authorized answered claims without unresolved
contradictions, and never mutates the standalone research ledger.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import re
import json
from pathlib import Path
from typing import Any

from ...agent_registry import require_registered_agent_id
from ...control_plane.projects.registry_codec import load_registry
from ...control_plane.runtime.public_safety import validate_public_safe_value
from ...explore_graph import compact_explore_graph_policy
from ...file_lock import exclusive_file_lock
from ...materials import find_registry_goal
from ...public_safe_text import (
    CATEGORY_CREDENTIAL,
    CATEGORY_LOCAL_PATH,
    CATEGORY_ORG_MARKER,
    PUBLIC_SAFE_LOCAL_PATH_PATTERNS,
    classify_private_text,
    find_public_safe_local_path,
)
from ..deep_research.runtime import load_state, state_path as deepresearch_state_path
from ..external_research.projection import _validate_admission, validate_receipt
from .machine_defaults import resolve_explore_goal
from .result_log import (
    FORBIDDEN_TEXT_MARKERS,
    SUMMARY_LIMIT,
    TITLE_LIMIT,
    build_explore_finding_event,
    build_explore_result_projection,
    explore_result_log_path,
    load_explore_result_events_strict,
    validate_explore_public_ref,
    validate_explore_result_event,
    validate_explore_result_event_attribution,
)


BRIDGE_SCHEMA_VERSION = "loopx_explore_research_bridge_v0"
_CREDENTIAL_MARKERS = tuple(
    marker.lower()
    for marker in FORBIDDEN_TEXT_MARKERS
    if any(word in marker.lower() for word in ("auth", "api", "pass", "sec", "tok"))
)
_LOCAL_PATH_TOKEN_PATTERN = re.compile(
    r"(?:file://[^\s,;\"'<>]+|~/[^\s,;\"'<>]+|[A-Za-z]:[\\/][^\s,;\"'<>]+|"
    r"(?:/Users/|/home/|/root/|/private/)[^\s,;\"'<>]+|"
    r"(?:\\Users\\|\\home\\|\\root\\|\\private\\)[^\s,;\"'<>]+|"
    r"\.local[/\\]private[^\s,;\"'<>]*)",
    re.IGNORECASE,
)


def _short_digest(*parts: str) -> str:
    payload = "\0".join(parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:12]


def _digest_token(value: str) -> str:
    clean = str(value or "").strip()
    if clean.startswith("sha256:"):
        clean = clean[len("sha256:") :]
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "", clean)
    return safe[:16] if safe else _short_digest(clean)


_SECRET_CATEGORIES = frozenset({CATEGORY_CREDENTIAL, CATEGORY_ORG_MARKER})
_LOCAL_PATH_CATEGORIES = frozenset({CATEGORY_LOCAL_PATH})


def _require_no_secrets(value: str, *, field: str) -> None:
    match = classify_private_text(
        value,
        categories=_SECRET_CATEGORIES,
        include_compound_field_assignment=True,
    )
    if match is not None:
        raise ValueError(f"{field} rejected: {match.reason}")
    lowered = value.lower()
    if any(marker in lowered for marker in _CREDENTIAL_MARKERS):
        raise ValueError(f"{field} rejected: contains credential-like marker")


def _sanitize_public_text(value: Any, *, field: str, limit: int) -> tuple[str, bool]:
    text = " ".join(str(value or "").split())
    if not text:
        return "", False
    _require_no_secrets(text, field=field)
    scrubbed = _LOCAL_PATH_TOKEN_PATTERN.sub("[redacted-local-path]", text)
    for pattern in PUBLIC_SAFE_LOCAL_PATH_PATTERNS:
        if classify_private_text(
            scrubbed, categories=_LOCAL_PATH_CATEGORIES, include_path_gaps=True,
        ):
            scrubbed = pattern.sub("[redacted-local-path]", scrubbed)
    changed = scrubbed != text
    if find_public_safe_local_path(scrubbed) is not None or classify_private_text(
        scrubbed, categories=_LOCAL_PATH_CATEGORIES, include_path_gaps=True,
    ) is not None:
        scrubbed = "[redacted-local-evidence]"
        changed = True
    validate_public_safe_value(scrubbed, path=field)
    if len(scrubbed) > limit:
        scrubbed = scrubbed[: max(0, limit - 1)].rstrip() + "..."
    return scrubbed, changed


def _is_public_http_ref(value: str) -> bool:
    clean = str(value or "").strip()
    if not clean.lower().startswith(("http://", "https://")):
        return False
    if find_public_safe_local_path(clean) is not None:
        return False
    if classify_private_text(
        clean,
        categories=_SECRET_CATEGORIES,
        include_compound_field_assignment=True,
    ) is not None:
        return False
    try:
        validate_explore_public_ref(clean, field="source_ref")
    except ValueError:
        return False
    return True


def _check_goal_match(
    goal_id: str,
    expected_goal_id: str | None,
    *containers: Mapping[str, Any] | None,
    label: str,
) -> None:
    if expected_goal_id is not None and str(expected_goal_id).strip() != goal_id:
        raise ValueError(f"{label} belongs to a different goal ({expected_goal_id!r} != {goal_id!r})")
    for container in containers:
        if not isinstance(container, Mapping):
            continue
        bound = container.get("goal_id")
        if bound is not None and str(bound).strip() and str(bound).strip() != goal_id:
            raise ValueError(f"{label} belongs to a different goal ({bound!r} != {goal_id!r})")


def _require_enabled_explore_goal(
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    agent_id: str | None,
) -> None:
    goal = find_registry_goal(load_registry(registry_path), goal_id)
    if goal is None:
        raise ValueError(f"Goal {goal_id!r} is not registered")
    effective = resolve_explore_goal(goal, runtime_root)
    policy = compact_explore_graph_policy(
        effective.get("explore_graph"),
        (effective.get("spawn_policy") or {}).get("explore_harness"),
    )
    if not policy["enabled"]:
        raise ValueError(f"Explore must be enabled on Goal {goal_id!r} before writing evidence")
    if agent_id is not None:
        require_registered_agent_id(registry_path=registry_path, goal_id=goal_id, agent_id=agent_id)


def _finding_signature(event: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "goal_id": event.get("goal_id"),
        "event_kind": event.get("event_kind"),
        "result_id": event.get("result_id"),
        "node_id": event.get("node_id"),
        "title": event.get("title"),
        "status": event.get("status"),
        "summary": event.get("summary"),
        "agent_id": event.get("agent_id"),
        "evidence_refs": list(event.get("evidence_refs") or []),
        "tags": list(event.get("tags") or []),
    }


def _append_sanitized_findings(
    *,
    runtime_root: Path,
    goal_id: str,
    node_id: str,
    agent_id: str | None,
    candidates: Sequence[Mapping[str, Any]],
    execute: bool,
    extra: Mapping[str, Any],
) -> dict[str, Any]:
    log_path = explore_result_log_path(runtime_root, goal_id)
    if execute:
        log_path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(log_path, operation="explore-research-bridge"):
        events = load_explore_result_events_strict(log_path, goal_id=goal_id)
        projection = build_explore_result_projection(events, goal_id=goal_id)
        if not any(row["node_id"] == node_id for row in projection["nodes"]):
            raise ValueError(f"node_id {node_id!r} must reference an existing node in Goal {goal_id!r}")
        existing_findings = {
            str(event["result_id"]): event
            for event in events
            if event.get("event_kind") == "finding"
        }
        to_append: list[dict[str, Any]] = []
        replayed_ids: list[str] = []
        all_ids: list[str] = []
        for spec in candidates:
            event = build_explore_finding_event(
                goal_id=goal_id,
                finding_id=str(spec["finding_id"]),
                node_id=node_id,
                title=str(spec["title"]),
                status=str(spec.get("status") or "confirmed"),
                summary=str(spec["summary"]),
                agent_id=agent_id,
                evidence_refs=list(spec["evidence_refs"]),
                tags=list(spec["tags"]),
            )
            validate_public_safe_value(
                {k: v for k, v in event.items() if k != "boundary"},
                path="explore_finding",
            )
            finding_id = str(event["result_id"])
            all_ids.append(finding_id)
            prior = existing_findings.get(finding_id)
            if prior is not None:
                if _finding_signature(prior) != _finding_signature(event):
                    raise ValueError(f"conflicting replay for Explore finding {finding_id!r}")
                replayed_ids.append(finding_id)
            else:
                to_append.append(event)

        if execute and to_append:
            validated = [
                validate_explore_result_event(item, expected_goal_id=goal_id)
                for item in to_append
            ]
            for item in validated:
                validate_explore_result_event_attribution(item)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in validated)
                )
    return {
        "ok": True,
        "schema_version": BRIDGE_SCHEMA_VERSION,
        "goal_id": goal_id,
        "node_id": node_id,
        "dry_run": not execute,
        "written": bool(execute and to_append),
        "replayed": bool(execute and replayed_ids and not to_append),
        "appended_count": len(to_append) if execute else 0,
        "would_append_count": len(to_append) if not execute else 0,
        "replayed_count": len(replayed_ids),
        "finding_ids": all_ids,
        "sanitized": True,
        "log_path": str(log_path),
        **extra,
    }


def project_external_evidence_to_explore(
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    node_id: str,
    plan: Mapping[str, Any],
    receipt: Mapping[str, Any],
    admission: Mapping[str, Any],
    agent_id: str | None = None,
    expected_goal_id: str | None = None,
    execute: bool = False,
) -> dict[str, Any]:
    """Project parent-admitted external evidence into an enabled Explore graph.

    This helper performs no external network or provider execution: callers must
    pass an explicit plan, receipt, and parent admission. Unadmitted dispositions,
    cross-goal payloads, and credentials fail closed; local file paths in raw
    source locators or findings are redacted before reaching the Goal log.
    """
    _check_goal_match(
        goal_id,
        expected_goal_id,
        plan,
        plan.get("request") if isinstance(plan, Mapping) else None,
        admission,
        label="external evidence",
    )
    _require_enabled_explore_goal(
        registry_path=registry_path, runtime_root=runtime_root, goal_id=goal_id, agent_id=agent_id,
    )
    observation = validate_receipt(dict(plan), dict(receipt))
    validated_receipt = observation["receipt"]
    normalized = _validate_admission(dict(plan), dict(validated_receipt), dict(admission))
    if normalized["disposition"] != "admit":
        raise ValueError("Explore projection requires an explicit parent disposition of 'admit'")

    admitted_sources = normalized["downstream_projection"]["sources"]
    if not admitted_sources:
        raise ValueError("external evidence admission has no admitted sources to project")

    admission_token = _digest_token(normalized["admission_id"])
    redacted_source_count = 0
    candidates: list[dict[str, Any]] = []
    for source in admitted_sources:
        raw_ref = str(source.get("source_ref") or "").strip()
        _require_no_secrets(raw_ref, field="external_evidence.source_ref")
        content_token = _digest_token(str(source.get("content_digest") or ""))
        opaque_ref = f"external-evidence:{admission_token}:{content_token}"
        evidence_refs = [opaque_ref]
        if _is_public_http_ref(raw_ref):
            evidence_refs.append(raw_ref)
        else:
            redacted_source_count += 1

        safe_finding, finding_redacted = _sanitize_public_text(
            source.get("finding"), field="external_evidence.finding", limit=TITLE_LIMIT,
        )
        if finding_redacted:
            redacted_source_count += 1
        title = safe_finding or f"Admitted external evidence {opaque_ref}"
        summary_parts = [
            "origin=external-evidence",
            f"admission={normalized['admission_id']}",
            f"provider={validated_receipt['provider_id']}",
            f"digest={source['content_digest']}",
        ]
        if source.get("accessed_at"):
            summary_parts.append(f"accessed_at={source['accessed_at']}")
        summary, _ = _sanitize_public_text(
            "; ".join(summary_parts), field="external_evidence.summary", limit=SUMMARY_LIMIT,
        )
        finding_id = f"ext_{_short_digest(normalized['admission_id'], str(source['content_digest']), node_id)}"
        candidates.append(
            {
                "finding_id": finding_id,
                "title": title,
                "summary": summary,
                "status": "confirmed",
                "evidence_refs": evidence_refs,
                "tags": ["external-evidence", "sanitized-ref"],
            }
        )

    return _append_sanitized_findings(
        runtime_root=runtime_root,
        goal_id=goal_id,
        node_id=node_id,
        agent_id=agent_id,
        candidates=candidates,
        execute=execute,
        extra={
            "operation": "observe-external",
            "plan_id": normalized["plan_id"],
            "admission_id": normalized["admission_id"],
            "provider_id": validated_receipt["provider_id"],
            "redacted_source_count": redacted_source_count,
            "automatic_execution": False,
        },
    )


def export_deepresearch_citations_to_explore(
    *,
    project: Path,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    node_id: str,
    agent_id: str | None = None,
    expected_goal_id: str | None = None,
    claim_ids: Sequence[str] | None = None,
    execute: bool = False,
) -> dict[str, Any]:
    """One-way read-only export of authorized deepresearch citations to Explore.

    Reads `.loopx/deepresearch/research.json` without writing or locking it for
    mutation. Only claims cited by answered questions without open
    contradictions are eligible. Saved local paths, private source locators,
    and report body prose stay in the deepresearch boundary; Explore receives
    only sanitized citation references and compact metadata.
    """
    state_file = deepresearch_state_path(project)
    state = load_state(project)
    if state is None:
        raise ValueError("no deepresearch ledger exists in this project to export")
    before_bytes = state_file.read_bytes()
    _check_goal_match(goal_id, expected_goal_id, state, label="deepresearch ledger")
    _require_enabled_explore_goal(
        registry_path=registry_path, runtime_root=runtime_root, goal_id=goal_id, agent_id=agent_id,
    )

    contradicted_claims = {
        str(claim_id)
        for contradiction in state.get("contradictions") or []
        if contradiction.get("status") != "resolved"
        for claim_id in (contradiction.get("claim_a"), contradiction.get("claim_b"))
        if claim_id
    }
    claim_to_questions: dict[str, list[str]] = {}
    for question in state.get("questions") or []:
        if question.get("status") != "answered":
            continue
        qid = str(question.get("id") or "")
        for cid in question.get("evidence_claims") or []:
            claim_to_questions.setdefault(str(cid), []).append(qid)

    claims_by_id = {
        str(claim["id"]): claim
        for claim in state.get("claims") or []
        if claim.get("id")
    }
    sources_by_id = {
        str(source["id"]): source
        for source in state.get("sources") or []
        if source.get("id")
    }

    if claim_ids:
        selected_ids = [str(cid).strip() for cid in claim_ids if str(cid).strip()]
        for cid in selected_ids:
            if cid not in claims_by_id:
                raise ValueError(f"deepresearch claim {cid!r} does not exist")
            if cid not in claim_to_questions:
                raise ValueError(f"claim {cid!r} is not cited by an answered deepresearch question")
            if cid in contradicted_claims:
                raise ValueError(f"claim {cid!r} has an unresolved contradiction")
    else:
        selected_ids = [
            cid for cid in claims_by_id
            if cid in claim_to_questions and cid not in contradicted_claims
        ]
    if not selected_ids:
        raise ValueError("deepresearch ledger has no authorized resolved citations to export")

    redacted_source_count = 0
    candidates: list[dict[str, Any]] = []
    exported_refs: list[dict[str, Any]] = []
    for cid in selected_ids:
        claim = claims_by_id[cid]
        source_id = str(claim.get("source_id") or "")
        source = sources_by_id.get(source_id) or {}
        raw_locator = str(source.get("url_or_path") or "")
        _require_no_secrets(raw_locator, field=f"deepresearch.source.{source_id}.url_or_path")
        _require_no_secrets(str(claim.get("text") or ""), field=f"deepresearch.claim.{cid}.text")
        if not _is_public_http_ref(raw_locator):
            redacted_source_count += 1
        elif find_public_safe_local_path(raw_locator) is None:
            # Keep even private-ledger source locators redacted on one-way citation export
            # so raw source URLs/paths stay under the original session ledger boundary.
            redacted_source_count += 1

        claim_digest = _short_digest(cid, str(claim.get("text") or ""), source_id)
        locator_digest = _short_digest(source_id, raw_locator)
        claim_ref = f"deepresearch:claim:{cid}:{claim_digest}"
        source_ref = f"deepresearch:source:{source_id}:{locator_digest}"
        question_ids = ",".join(sorted(claim_to_questions[cid]))
        tool_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(source.get("tool") or "unspecified"))[:40]
        stance = re.sub(r"[^A-Za-z0-9_-]+", "", str(claim.get("stance") or "neutral"))[:24]
        title = f"Authorized citation {cid} ({stance}) for {question_ids}"
        summary = (
            f"origin=deepresearch; ledger_readonly_export=true; tool={tool_name}; "
            f"claim_ref={claim_ref}; source_ref={source_ref}"
        )
        finding_id = f"dr_cite_{cid}_{_short_digest(goal_id, node_id, claim_digest, locator_digest)}"
        candidates.append(
            {
                "finding_id": finding_id,
                "title": title,
                "summary": summary,
                "status": "confirmed",
                "evidence_refs": [claim_ref, source_ref],
                "tags": ["deepresearch-citation", "read-only-export"],
            }
        )
        exported_refs.append({"claim_id": cid, "source_id": source_id, "evidence_refs": [claim_ref, source_ref]})

    result = _append_sanitized_findings(
        runtime_root=runtime_root,
        goal_id=goal_id,
        node_id=node_id,
        agent_id=agent_id,
        candidates=candidates,
        execute=execute,
        extra={
            "operation": "import-citations",
            "project": str(project),
            "exported_citations": exported_refs,
            "redacted_source_count": redacted_source_count,
            "deepresearch_ledger_mutated": state_file.read_bytes() != before_bytes,
        },
    )
    return result
