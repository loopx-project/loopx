"""Public-safe history codec for the TypeScript-owned replan evidence view."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
import hashlib
import shlex
from typing import Any

from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result
from ..runtime.public_safety import public_safe_compact_text
from ..runtime.time import parse_timestamp
from ..runtime.agent_evidence_history import goal_history_runs


def replan_history_from_status(status: Mapping[str, Any], goal_id: str) -> list[dict[str, Any]]:
    history = status.get("run_history")
    if not isinstance(history, Mapping):
        raise ValueError("replan context requires the run_history source")
    snapshot = goal_history_runs(history, goal_id)
    # A real host route supplies both authorities. Pure snapshot callers stay
    # pure; live replan/handoff reads are independent of the display limit.
    if status.get("registry") and status.get("runtime_root"):
        from ...history import load_index_snapshot, validate_goal_id_path_segment

        goal = validate_goal_id_path_segment(goal_id)
        path = Path(str(status["runtime_root"])) / "goals" / goal / "runs" / "index.jsonl"
        return load_index_snapshot(path, include_artifact_status=False).records
    return snapshot


def replan_evidence_rows(runs: Iterable[Mapping[str, Any]], *, goal_id: str, agent_id: str | None) -> list[dict[str, Any]]:
    from .progress_observation import normalize_progress_observation

    rows = []
    for run in runs:
        if not isinstance(run, Mapping):
            raise ValueError("replan history row must be an object")
        # Legacy compact records may omit the enclosing Goal id. Agent attribution
        # is never guessed: another or unattributed lane cannot supply scoped proof.
        run_goal = run.get("goal_id", goal_id)
        run_agent = run.get("agent_id")
        if run_goal != goal_id or (agent_id and run_agent != agent_id):
            continue
        timestamp = parse_timestamp(run.get("generated_at"))
        if timestamp is None:
            raise ValueError("replan evidence has an invalid generated_at timestamp")
        progress = run.get("progress_observation")
        if "progress_observation" in run:
            if not isinstance(progress, Mapping):
                raise ValueError("replan evidence progress_observation must be an object")
            if progress.get("schema_version") != "typed_progress_observation_v0":
                raise ValueError("unsupported replan evidence progress_observation schema_version")
            progress = normalize_progress_observation(progress)
        row: dict[str, Any] = {
            "goal_id": run_goal,
            "agent_id": run_agent,
            "generated_at": str(run["generated_at"]),
            "observed_at": timestamp.timestamp(),
            "progress_observation": progress,
        }
        for key in ("classification", "delivery_outcome", "health_check", "recommended_action", "todo_id"):
            text = public_safe_compact_text(run.get(key), limit=240)
            if text:
                row[key] = text
        if "settlement_identity" in run or "quota_spend_commit" in run:
            # Transport facts, not a Python eligibility decision. Complete-text
            # digests prevent a shared display prefix from erasing distinct tails.
            # This private wire metadata is excluded from public reference identity.
            source: dict[str, Any] = {key: run.get(key) for key in (
                "goal_id", "agent_id", "todo_id", "turn_instance_id")}
            for key, fields in (("settlement_identity", (
                "schema_version", "goal_id", "agent_id", "todo_id", "turn_instance_id", "effect_id")),
                ("quota_spend_commit", ("schema_version", "effect_id"))):
                value = run.get(key)
                source[key] = {field: value.get(field) for field in fields} if isinstance(value, Mapping) else None
            for key in ("recommended_action", "health_check", "delivery_outcome"):
                value = run.get(key)
                source[key + "_digest"] = hashlib.sha256(value.encode()).hexdigest() if isinstance(value, str) else None
            row["_source_facts"] = source
        rows.append(row)
    return rows


def _goal_facts(status: Mapping[str, Any] | None, goal_id: str, acceptance: Mapping[str, Any] | None) -> dict[str, Any]:
    """Read existing Goal sources; source precedence belongs to TypeScript."""
    def goal_text(value: Any) -> str | None:
        if value is not None and not isinstance(value, str):
            raise ValueError("Goal objective must be text")
        # Preserve the complete core objective, including trailing constraints.
        return public_safe_compact_text(value, limit=max(1, len(value or "")))

    facts: dict[str, Any] = {}
    if status and status.get("registry"):
        from ...agent_registry import load_goal_from_registry
        from ...materials import goal_state_path
        from ..goals.active_state_metadata import active_state_section_text, split_state_frontmatter

        goal = load_goal_from_registry(Path(str(status["registry"])), goal_id)
        if goal:
            facts["registry_objective"] = goal_text(goal.get("objective"))
            path = goal_state_path(goal)
            if path and path.exists():
                state = path.read_text(encoding="utf-8")
                metadata, _ = split_state_frontmatter(state)
                objective = active_state_section_text(state, "Objective") or metadata.get("objective")
                facts["active_state_objective"] = goal_text(objective)
    if acceptance and acceptance.get("enabled") is True:
        # This is the acceptance owner's public projection, including its scope.
        # It must never be mistaken for the whole Goal's original objective.
        facts["acceptance_contract"] = {key: acceptance[key] for key in (
            "objective", "non_goals", "criteria", "scope", "revision", "digest", "status",
        ) if key in acceptance}
    return facts


def project_replan_context(
    *, goal_id: str, agent_id: str | None, runs: Iterable[Mapping[str, Any]],
    obligation: Mapping[str, Any] | None = None, evidence_ref: str | None = None,
    source_status: Mapping[str, Any] | None = None,
    goal_acceptance_contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    from .replan_history_codec import project_replan_request

    prefix = ["loopx"]
    if source_status is not None:
        runs = replan_history_from_status(source_status, goal_id)
        for field, option in (("registry", "--registry"), ("runtime_root", "--runtime-root")):
            if source_status.get(field):
                prefix.extend([option, str(source_status[field])])
    try:
        return project_replan_request({
            "operation": "resolve" if evidence_ref is not None else "project",
            "goal_id": goal_id, "agent_id": agent_id,
            "obligation": dict(obligation) if obligation is not None else None,
            "rows": replan_evidence_rows(runs, goal_id=goal_id, agent_id=agent_id),
            "evidence_ref": evidence_ref,
            "read_prefix": shlex.join(prefix),
            "goal_facts": _goal_facts(source_status, goal_id, goal_acceptance_contract) if evidence_ref is None else {},
            "from_full_index": bool(source_status and source_status.get("registry") and source_status.get("runtime_root")),
        }, method="work_item.replan_context")
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from None


def validate_replan_context(context: Any) -> dict[str, Any]:
    try:
        return effect_runtime_result("work_item.replan_context.project", {
            "operation": "validate", "context": context,
        })
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from None
