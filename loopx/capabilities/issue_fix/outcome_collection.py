"""Domain-state ledger readers and multi-case collection assembly."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Literal, overload

from ...control_plane.runtime.public_safety import public_safe_compact_text
from .metadata_preview import normalise_github_issue_link_reference
from .outcome_case import (
    ISSUE_FIX_OUTCOME_COLLECTION_PROJECTION_SCHEMA_VERSION,
    _evidence_summary,
    _mapping,
    _object_list,
    _safe_text,
    validate_issue_fix_outcome_collection_projection,
)


OutcomeProjector = Callable[..., dict[str, object]]


class IssueFixOutcomeCollectionProjection(dict[str, object]):
    """Typed dict container exposing list-typed outcome/warning getters."""

    @overload
    def get(
        self,
        key: Literal["issue_fix_outcomes"],
        default: list[dict[str, object]] | None = None,
        /,
    ) -> list[dict[str, object]] | None: ...

    @overload
    def get(
        self,
        key: Literal["warnings"],
        default: list[str] | None = None,
        /,
    ) -> list[str] | None: ...

    @overload
    def get(self, key: str, default: object = None, /) -> object: ...

    def get(self, key: str, default: object = None, /) -> object:
        return super().get(key, default)


def _load_domain_packets(path: Path) -> tuple[list[dict[str, object]], list[str]]:
    if not path.is_file():
        return [], []
    packets: list[dict[str, object]] = []
    warnings: list[str] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").split("\n"), start=1
    ):
        if not line.strip():
            continue
        try:
            packet: object = json.loads(line)
        except json.JSONDecodeError:
            warnings.append(f"{path.name}:{line_number} is not valid JSON")
            continue
        if not isinstance(packet, dict):
            warnings.append(f"{path.name}:{line_number} is not an object")
            continue
        packets.append({str(k): v for k, v in packet.items()})
    return packets, warnings


def _lifecycle_recency(packet: Mapping[str, object]) -> tuple[str, str]:
    observation = _mapping(packet.get("observation"))
    return (
        str(
            observation.get("updated_at")
            or observation.get("merged_at")
            or observation.get("closed_at")
            or packet.get("generated_at")
            or ""
        ),
        str(observation.get("pr_ref") or ""),
    )


def _build_lifecycle_only_outcome(
    *,
    goal_id: str,
    pr_lifecycle_packet: Mapping[str, object],
    agent_id: str | None = None,
    project_outcome: OutcomeProjector | None = None,
) -> dict[str, object]:
    """Project a PR lifecycle row that has no feasibility inventory row.

    The PR lifecycle ledger is already compact source state. Dropping one of
    its rows from the collection makes terminal delivery counts incorrect, so
    this adapter keeps the lifecycle fact visible without inventing
    reproduction, validation, or issue association evidence.
    """

    if not isinstance(pr_lifecycle_packet, Mapping):
        raise ValueError("PR lifecycle packet must be an object")
    if (
        pr_lifecycle_packet.get("schema_version")
        != "issue_fix_pr_lifecycle_monitor_v0"
    ):
        raise ValueError(
            "PR lifecycle packet must use issue_fix_pr_lifecycle_monitor_v0"
        )
    if project_outcome is None:
        from .outcome_projection import build_issue_fix_outcome_projection

        projector: OutcomeProjector = build_issue_fix_outcome_projection
    else:
        projector = project_outcome
    observation = _mapping(pr_lifecycle_packet.get("observation"))
    repo = _safe_text(observation.get("repo"), field="repo", limit=180)
    pr_ref = _safe_text(observation.get("pr_ref"), field="pr_ref", limit=180)
    if not repo or not pr_ref:
        raise ValueError("PR lifecycle packet must identify repo and pr_ref")
    raw_issue_ref = str(observation.get("issue_ref") or "").strip()
    issue_ref = (
        normalise_github_issue_link_reference(raw_issue_ref)
        if raw_issue_ref
        else ""
    )
    issue_number: int | None = None
    if issue_ref:
        match = re.fullmatch(r"issues_(\d+)", issue_ref)
        issue_number = int(match.group(1)) if match else None
    issue_url = (
        f"https://github.com/{repo}/issues/{issue_number}"
        if issue_number is not None
        else ""
    )
    synthetic_issue_ref = issue_ref or f"pr_lifecycle_{pr_ref}"
    synthetic_feasibility: dict[str, object] = {
        "schema_version": "issue_fix_feasibility_v0",
        "observation": {
            "repo": repo,
            "issue_ref": synthetic_issue_ref,
            "number": issue_number,
            "permalink": issue_url,
            "reproduction_status": "missing",
            "repository_context": {},
        },
        "decision": {"route": "fix_pr"},
        "transition": {"decision": "pr_lifecycle_only"},
        "repository_context_effect": {},
    }
    projection = projector(
        goal_id=goal_id,
        feasibility_packet=synthetic_feasibility,
        pr_lifecycle_packet=pr_lifecycle_packet,
        agent_id=agent_id,
    )
    projected_outcomes = _object_list(projection.get("issue_fix_outcomes"))
    case = _mapping(projected_outcomes[0] if projected_outcomes else {})
    case["outcome_id"] = f"{repo}:{issue_ref or pr_ref}"
    case["title"] = f"[fix_pr] {repo} PR #{observation.get('number') or pr_ref}"
    case["issue_ref"] = issue_ref or None
    case["issue"] = {"number": issue_number, "url": issue_url}
    case["evidence"] = _evidence_summary(case)
    return case


def _index_lifecycle_by_issue(
    lifecycle_packets: list[dict[str, object]],
) -> dict[tuple[str, str], list[dict[str, object]]]:
    lifecycle_by_issue: dict[tuple[str, str], list[dict[str, object]]] = {}
    for packet in lifecycle_packets:
        observation = _mapping(packet.get("observation"))
        repo = str(observation.get("repo") or "").strip()
        raw_issue_ref = str(observation.get("issue_ref") or "").strip()
        issue_ref = (
            normalise_github_issue_link_reference(raw_issue_ref)
            if raw_issue_ref
            else ""
        )
        if repo and issue_ref:
            lifecycle_by_issue.setdefault((repo, issue_ref), []).append(packet)
    return lifecycle_by_issue


def _collect_feasibility_outcomes(
    *,
    goal_id: str,
    feasibility_packets: list[dict[str, object]],
    lifecycle_by_issue: Mapping[tuple[str, str], list[dict[str, object]]],
    agent_id: str | None,
    generated_at: str | None,
    warnings: list[str],
    project_outcome: OutcomeProjector,
) -> tuple[list[dict[str, object]], set[int]]:
    outcomes: list[dict[str, object]] = []
    linked_lifecycle_ids: set[int] = set()
    for feasibility_packet in feasibility_packets:
        observation = _mapping(feasibility_packet.get("observation"))
        repo = str(observation.get("repo") or "").strip()
        raw_issue_ref = str(observation.get("issue_ref") or "").strip()
        issue_ref = (
            normalise_github_issue_link_reference(raw_issue_ref)
            if raw_issue_ref
            else ""
        )
        lifecycle_packet: dict[str, object] | None = None
        candidates = lifecycle_by_issue.get((repo, issue_ref), [])
        if candidates:
            lifecycle_packet = max(candidates, key=_lifecycle_recency)
            linked_lifecycle_ids.update(id(item) for item in candidates)
        try:
            projection = project_outcome(
                goal_id=goal_id,
                feasibility_packet=feasibility_packet,
                pr_lifecycle_packet=lifecycle_packet,
                delivery_evidence_input=(
                    feasibility_packet.get("delivery_evidence")
                    if isinstance(feasibility_packet.get("delivery_evidence"), Mapping)
                    else None
                ),
                agent_id=agent_id,
                generated_at=generated_at,
            )
        except ValueError as exc:
            safe_identity = (
                public_safe_compact_text(f"{repo}:{issue_ref}", limit=220)
                or "unknown issue"
            )
            safe_error = public_safe_compact_text(exc, limit=260) or "invalid row"
            warnings.append(f"skipped {safe_identity}: {safe_error}")
            continue
        if projection.get("ok") is True:
            for item in _object_list(projection.get("issue_fix_outcomes")):
                if isinstance(item, dict):
                    outcomes.append(item)
    return outcomes, linked_lifecycle_ids


def collect_issue_fix_outcomes_from_domain_state(
    *,
    goal_id: str,
    project: str | Path = ".",
    agent_id: str | None = None,
    generated_at: str | None = None,
    project_outcome: OutcomeProjector,
) -> IssueFixOutcomeCollectionProjection:
    from ...domain_packs.issue_fix import (
        default_issue_fix_domain_state_ledger_path,
        default_issue_fix_feasibility_ledger_path,
    )

    project_path = Path(project).expanduser()
    feasibility_packets, feasibility_warnings = _load_domain_packets(
        default_issue_fix_feasibility_ledger_path(
            project=project_path,
            goal_id=goal_id,
        )
    )
    lifecycle_packets, lifecycle_warnings = _load_domain_packets(
        default_issue_fix_domain_state_ledger_path(
            project=project_path,
            goal_id=goal_id,
        )
    )
    warnings = [*feasibility_warnings, *lifecycle_warnings]
    lifecycle_by_issue = _index_lifecycle_by_issue(lifecycle_packets)
    outcomes, linked_lifecycle_ids = _collect_feasibility_outcomes(
        goal_id=goal_id,
        feasibility_packets=feasibility_packets,
        lifecycle_by_issue=lifecycle_by_issue,
        agent_id=agent_id,
        generated_at=generated_at,
        warnings=warnings,
        project_outcome=project_outcome,
    )

    unlinked_lifecycle_packets = [
        packet for packet in lifecycle_packets if id(packet) not in linked_lifecycle_ids
    ]
    lifecycle_only_by_key: dict[tuple[str, str], dict[str, object]] = {}
    lifecycle_only_without_key: list[dict[str, object]] = []
    for lifecycle_packet in unlinked_lifecycle_packets:
        observation = _mapping(lifecycle_packet.get("observation"))
        repo = str(observation.get("repo") or "").strip()
        raw_issue_ref = str(observation.get("issue_ref") or "").strip()
        issue_ref = (
            normalise_github_issue_link_reference(raw_issue_ref)
            if raw_issue_ref
            else ""
        )
        pr_ref = str(observation.get("pr_ref") or "").strip()
        identity = issue_ref or pr_ref
        if not repo or not identity:
            lifecycle_only_without_key.append(lifecycle_packet)
            continue
        key = (repo, identity)
        current = lifecycle_only_by_key.get(key)
        if current is None or _lifecycle_recency(lifecycle_packet) > _lifecycle_recency(
            current
        ):
            lifecycle_only_by_key[key] = lifecycle_packet

    lifecycle_only_outcome_count = 0
    unprojected_lifecycle_count = len(lifecycle_only_without_key)
    for lifecycle_packet in lifecycle_only_by_key.values():
        try:
            outcomes.append(
                _build_lifecycle_only_outcome(
                    goal_id=goal_id,
                    pr_lifecycle_packet=lifecycle_packet,
                    agent_id=agent_id,
                    project_outcome=project_outcome,
                )
            )
            lifecycle_only_outcome_count += 1
        except ValueError as exc:
            observation = _mapping(lifecycle_packet.get("observation"))
            safe_identity = public_safe_compact_text(
                f"{observation.get('repo')}:{observation.get('pr_ref')}",
                limit=220,
            ) or "unknown PR"
            safe_error = public_safe_compact_text(exc, limit=260) or "invalid row"
            warnings.append(f"skipped {safe_identity}: {safe_error}")
            unprojected_lifecycle_count += 1

    unlinked_lifecycle_count = len(unlinked_lifecycle_packets)
    collection = IssueFixOutcomeCollectionProjection(
        {
            "ok": True,
            "schema_version": ISSUE_FIX_OUTCOME_COLLECTION_PROJECTION_SCHEMA_VERSION,
            "mode": "issue-fix-outcome-collection",
            "source_id": "issue-fix-outcome",
            "goal_id": goal_id,
            "generated_at": generated_at,
            "agent_identity": {"agent_id": agent_id} if agent_id else {},
            "issue_fix_outcomes": outcomes,
            "source_contract": {
                "feasibility": "issue_fix_feasibility_v0",
                "pr_lifecycle": "issue_fix_pr_lifecycle_monitor_v0",
                "delivery_evidence": "embedded_in_feasibility_row",
                "repository_commit_evidence": "embedded_in_delivery_evidence",
                "association": "explicit_repo_and_issue_ref_or_pr_lifecycle_only",
                "writes_source_state": False,
                "creates_parallel_state_machine": False,
            },
            "source_counts": {
                "feasibility": len(feasibility_packets),
                "pr_lifecycle": len(lifecycle_packets),
                "outcomes": len(outcomes),
                "unlinked_pr_lifecycle": unlinked_lifecycle_count,
                "lifecycle_only_outcomes": lifecycle_only_outcome_count,
                "coalesced_pr_lifecycle": (
                    unlinked_lifecycle_count
                    - len(lifecycle_only_by_key)
                    - len(lifecycle_only_without_key)
                ),
                "unprojected_pr_lifecycle": unprojected_lifecycle_count,
            },
            "warnings": warnings,
            "external_reads_performed": False,
            "external_writes_performed": False,
            "raw_logs_captured": False,
            "local_paths_captured": False,
            "credentials_captured": False,
        }
    )
    validation = validate_issue_fix_outcome_collection_projection(collection)
    collection["validation"] = validation
    collection["ok"] = bool(validation["ok"])
    return collection
