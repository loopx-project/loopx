"""Render the bounded Goal acceptance observations owned by status collection."""

from __future__ import annotations

from ...control_plane.goals.acceptance_readback import configured_checks

import re
from typing import Any

from ...control_plane.goals.acceptance_observation import (
    GOAL_ACCEPTANCE_OBSERVATION_SCHEMA_VERSION,
)
from ..markdown import as_dict, as_list, markdown_scalar


def _contract_text(value: Any) -> str:
    """Owner-authored text stays literal in the Markdown readback."""
    text = str(value if value is not None else "unknown")
    return re.sub(r"([\\`*_{}\[\]<>|#])", r"\\\1", text).replace("\n", " ").replace("\r", " ")


def append_goal_acceptance_observation_markdown(
    lines: list[str], goal: dict[str, Any]
) -> None:
    observation = as_dict(goal.get("acceptance_observation"))
    if observation.get("schema_version") == GOAL_ACCEPTANCE_OBSERVATION_SCHEMA_VERSION:
        lines.append(
            "  - acceptance observations (partial; not completion proof): "
            f"historical_progress={len(as_list(observation.get('historical_progress')))} "
            f"gaps={len(as_list(observation.get('acceptance_gaps')))} "
            f"gates={len(as_list(observation.get('guards')))}"
        )
        for gap in as_list(observation.get("acceptance_gaps")):
            if isinstance(gap, dict):
                lines.append(
                    f"    - owner={markdown_scalar(gap.get('owner') or 'unknown')}: "
                    f"{markdown_scalar(gap.get('evidence_required') or gap.get('reason') or 'unknown')}"
                )
        contract = as_dict(observation.get("goal_acceptance_contract"))
        if observation.get("goal_id") != goal.get("id"):
            lines.append("  - Goal acceptance contract: source unavailable; acceptance unknown")
            return
        for outcome in as_list(as_dict(contract.get("outcome_requirements")).get("outcomes")):
            if not isinstance(outcome, dict):
                continue
            lines.append(f"  - outcome: {_contract_text(outcome.get('objective'))}")
            for requirement in as_list(outcome.get("requirements")):
                if not isinstance(requirement, dict):
                    continue
                review = as_dict(requirement.get("agent_review"))
                lines.append(f"    - {_contract_text(requirement.get('description'))}: checks={_contract_text(requirement.get('status'))}; "
                    f"Agent review={_contract_text(review.get('conclusion', 'unreviewed'))}; basis_current={review.get('basis_current', False)}")
                for ref in as_list(review.get("evidence_refs")):
                    lines.append(f"      - evidence: {_contract_text(ref)}")
            for work in as_list(outcome.get("work")):
                if isinstance(work, dict):
                    lines.append(f"    - work: {_contract_text(work.get('todo_id'))} ({_contract_text(work.get('status'))})")
        if configured_checks(contract) is None:
            return
        task_states = {
            "ready": "task association confirmed",
            "unbound": "task association missing",
            "stale": "task association stale",
        }
        verification_states = {
            "unverified": "artifact checks not verified",
            "accepted": "artifact checks passed",
            "failed": "artifact checks failed",
            "stale": "artifact checks stale",
            "partial": "task checks passed; Goal-wide verification unknown",
            "held": "task associations require confirmation",
        }
        lines.extend([
            "  - Goal acceptance contract (read-only; does not automatically approve or complete the Goal):",
            f"    - Goal source: {_contract_text(observation.get('goal_id'))}",
            f"    - contract coverage: {_contract_text(as_dict(contract.get('scope')).get('kind', 'all_advancement'))}",
            f"    - contract revision: {_contract_text(contract.get('revision'))}",
            f"    - contract digest: {_contract_text(contract.get('digest'))}",
            f"    - objective: {_contract_text(contract.get('objective') or 'unknown')}",
        ])
        for non_goal in as_list(contract.get("non_goals")):
            lines.append(f"    - outside scope: {_contract_text(non_goal)}")
        for criterion in as_list(contract.get("criteria")):
            if isinstance(criterion, dict):
                lines.append(
                    f"    - criterion {_contract_text(criterion.get('id'))}: "
                    f"{_contract_text(criterion.get('description'))}"
                )
        tasks = as_list(contract.get("tasks"))
        if not tasks:
            lines.append("    - task associations: unknown; no associations reported")
        for task in tasks:
            if isinstance(task, dict):
                criteria = ", ".join(str(value) for value in as_list(task.get("criterion_ids")))
                lines.append(
                    f"    - {_contract_text(task.get('todo_id'))}: "
                    f"{task_states.get(str(task.get('state')), 'unknown')}; "
                    f"criteria={_contract_text(criteria or 'unknown')}"
                )
                if task.get("reason"):
                    lines.append(f"      {_contract_text(task['reason'])}")
                if task.get("applicable") is False:
                    lines.append("      outside the current task gate")
        verification = as_dict(contract.get("verification"))
        lines.append(
            f"    - artifact verification: {verification_states.get(str(contract.get('status')), 'unknown')}"
        )
        held = as_list(contract.get("held_todo_ids"))
        if held:
            lines.append(f"    - tasks held: {_contract_text(', '.join(str(todo) for todo in held))}")
        if not verification:
            lines.append("    - recorded artifact checks: unknown")
        else:
            lines.extend([
                "    - recorded artifact checks (historical basis; current status accounts for stale checks and task holds):",
                f"      - verification reference: {_contract_text(verification.get('operation_id'))}",
                f"      - contract revision: {_contract_text(verification.get('contract_revision'))}",
                f"      - contract digest: {_contract_text(verification.get('contract_digest'))}",
                f"      - verification scope: {_contract_text(verification.get('todo_id') or 'all contract criteria')}",
            ])
            for result in as_list(verification.get("results")):
                if isinstance(result, dict):
                    passed = result.get("passed")
                    state = "passed" if passed is True else "failed" if passed is False else "unknown"
                    lines.append(
                        f"      - {_contract_text(result.get('criterion_id'))}: {state}; "
                        f"exit code={_contract_text(result.get('exit_code'))}"
                    )
