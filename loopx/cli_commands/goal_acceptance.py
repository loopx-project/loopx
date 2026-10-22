"""Local owner configuration and real validation for a Goal acceptance basis."""

from __future__ import annotations

from ..control_plane.goals.acceptance_readback import configured_checks

import argparse
import json
import shlex
from pathlib import Path
from typing import Any

from ..control_plane.goals.acceptance import (
    GoalAcceptanceRejected,
    configure_goal_acceptance,
    refine_goal_outcome,
    update_goal_objective,
    inspect_goal_acceptance,
    public_goal_acceptance,
    verify_goal_acceptance,
)


def register_goal_acceptance_command(subparsers: Any, add_format: Any) -> None:
    parser = subparsers.add_parser(
        "goal-acceptance",
        help="Read Goal intent, prepare revisions, adopt current intent, refine outcomes or run configured checks.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''Objective CLI workflow (no App required):
  1. inspect --agent-id AGENT
  2. prepare-objective --agent-id AGENT --document objective.json --operation-id ID --execute
     objective.json: {"objective":"Current direction", "reason":"Evidence of changed user direction", "source_ref":"message:owner"}
     Saves/reuses a proposal; does not change the Goal or block existing work.
  3. inspect-proposal --proposal-id PROPOSAL
  4. apply-proposal --proposal-id PROPOSAL --execute
     Local owner operation: use existing explicit authorization, or surface once
     for owner review and continue independently authorized work. Do not ask
     again for an already authorized unchanged proposal. Omit --execute to preview.
     Retry the same proposal ID after a lost response; no new operation ID.
  5. inspect --agent-id AGENT
     Review your own work, then adopt-objective --agent-id AGENT --document adoption.json
       --operation-id ID --expected-provider-revision REV --execute
     adoption.json: {"revision":1, "digest":"<goal_objective.digest>", "work_digest":"<objective_work_digest>", "review_summary":"How my current work fits"}
     Use exact values from inspect; each peer adopts only for itself.
All steps require --goal-id GOAL. Proposal preparation alone requires neither
owner input nor adoption; only an applied objective revision needs adoption.
Authority, budgets, stops and existing gated actions remain binding.''',
    )
    add_format(parser)
    parser.add_argument("action", type=lambda value: "clear-checks" if value == "disable" else value, choices=("inspect", "prepare", "configure", "verify", "clear-checks", "refine", "prepare-objective", "inspect-proposal", "apply-proposal", "revise-objective", "adopt-objective"))
    parser.add_argument("--goal-id", required=True)
    parser.add_argument(
        "--proposal-id", help="Existing objective proposal ID for inspect-proposal/apply-proposal; reuses the original reviewed request and recovery identity.",
    )
    parser.add_argument(
        "--agent-id",
        help="Registered caller; Agents may prepare objective revisions, adopt only for themselves, refine outcomes or inspect/verify checks. Shared objective revision requires the owner.",
    )
    parser.add_argument(
        "--document",
        type=Path,
        help="Objective revision/adoption, outcome refinement or check plan; see the selected action contract.",
    )
    parser.add_argument(
        "--expected-provider-revision",
        help="Exact revision from inspect; required by configure/clear-checks/refine/revise-objective/adopt-objective.",
    )
    parser.add_argument(
        "--operation-id", help="Stable identity for an unchanged operation retry."
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="With prepare or prepare-objective, save a reviewable proposal only. For other mutations, apply the named operation. Otherwise preview only.",
    )


def render_goal_acceptance(payload: dict[str, Any]) -> str:
    lines = ["# Goal acceptance", "", f"- ok: {payload.get('ok')}"]
    if payload.get("error"):
        lines.append(f"- error: {payload['error']}")
        if payload.get("error_detail"):
            lines.append(f"- detail: {payload['error_detail']}")
        return "\n".join(lines)
    if payload.get("provider_revision"):
        lines.append(f"- provider revision: {payload['provider_revision']}")
    proposal = payload.get("proposal")
    if proposal and proposal["action_kind"] == "goal.update":
        parameters = proposal["normalized_parameters"]
        lines.extend([f"- proposal: {proposal['proposal_id']} ({proposal['status']})",
            f"- proposed objective: {parameters['objective']}",
            f"- reason: {parameters['reason']}", f"- source: {parameters['source_ref']}"])
        lines.extend(f"- CLI: {command}" for command in payload.get("next_cli_actions", []))
        lines.append("- A pending proposal does not change intent or block independently authorized work. Owner apply needs existing explicit authorization; do not repeat an answered request.")
        if payload.get("current"):
            lines.append(render_goal_acceptance(payload["current"]))
        return "\n".join(lines)
    contract = payload.get("goal_acceptance_contract") or {}
    objective = contract.get("goal_objective")
    if objective:
        lines.extend([f"- current Goal objective (revision {objective['revision']}): {objective['objective']}",
            f"- revision reason: {objective['reason']}"])
        for peer in objective["peer_adoption"]:
            lines.append(f"- {peer['agent_id']}: " + ("current revision adopted" if peer["current"] else "own work review and adopt-objective required before new execution"))
    if payload.get("objective_work_digest"):
        lines.append(f"- own work digest: {payload['objective_work_digest']}")
    lines.append("- requirements and evidence review: available by default")
    if not configured_checks(contract):
        lines.append("- automated checks: not configured")
    if configured_checks(contract):
        lines.extend(
            [
                f"- coverage: {(contract.get('scope') or {}).get('kind', 'all_advancement')}",
                f"- acceptance revision: {contract.get('revision')}",
                f"- objective: {contract.get('objective')}",
            ]
        )
        for task in contract.get("tasks", []):
            lines.append(
                f"- {task.get('todo_id')}: {task.get('state')} ({', '.join(task.get('criterion_ids', []))})"
            )
        lines.append(
            f"- configured artifact checks: {contract.get('status', 'unverified')}"
        )
    for outcome in (contract.get("outcome_requirements") or {}).get("outcomes", []):
        lines.append(f"- outcome {outcome['id']}: {outcome['objective']} ({outcome['owner_agent_id']})")
        for requirement in outcome["requirements"]:
            lines.append(f"  - {requirement['id']}: {requirement['description']} [{requirement['status']}]")
            review = requirement.get("agent_review")
            if review:
                current = "current basis" if review["basis_current"] else "basis changed; review again"
                lines.append(f"    Agent review: {review['conclusion']} ({current}; not independent verification). {review['note']}")
                lines.extend(f"    evidence: {ref}" for ref in review["evidence_refs"])
        lines.append("  work: " + (", ".join(f"{work['todo_id']} ({work['status']})" for work in outcome["work"]) or "not linked"))
    for gap in (contract.get("outcome_requirements") or {}).get("acceptance_gaps", []):
        lines.append(f"- next work for {gap['outcome_id']}: {', '.join(gap['requirement_ids'])} still needs evidence or linked work; use existing replan")
    return "\n".join(lines)


def handle_goal_acceptance_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    output_format: Any,
    print_payload: Any,
) -> int | None:
    if args.command != "goal-acceptance":
        return None
    try:
        common = {
            "registry_path": registry_path,
            "goal_id": args.goal_id,
            "runtime_root": runtime_root_arg,
            "agent_id": args.agent_id,
        }
        if args.proposal_id and args.action not in {"inspect-proposal", "apply-proposal"}:
            raise ValueError("--proposal-id requires inspect-proposal or apply-proposal")
        if args.action in {"inspect-proposal", "apply-proposal"}:
            from ..control_plane.goals.goal_objective_actions import objective_revision_proposal
            if not args.proposal_id or args.document or args.operation_id or args.expected_provider_revision:
                raise ValueError("inspect-proposal/apply-proposal requires only --proposal-id, not a new document, operation or revision")
            if args.action == "inspect-proposal" and args.execute:
                raise ValueError("inspect-proposal is read-only")
            payload = objective_revision_proposal(**common, proposal_id=args.proposal_id,
                execute=args.action == "apply-proposal" and args.execute)
        elif args.action in {"revise-objective", "adopt-objective"}:
            if args.document is None or not args.operation_id or not args.expected_provider_revision:
                raise ValueError("objective updates require --document, --operation-id and --expected-provider-revision from inspect")
            document = json.loads(args.document.read_text(encoding="utf-8"))
            payload = public_goal_acceptance(update_goal_objective(**common, operation=args.action,
                document=document, operation_id=args.operation_id, expected_provider_revision=args.expected_provider_revision, execute=args.execute))
        elif args.action == "prepare-objective":
            from ..control_plane.goals.goal_objective_actions import prepare_objective_revision
            if args.document is None or not args.operation_id or args.expected_provider_revision:
                raise ValueError("prepare-objective requires --document and --operation-id; it reads the current basis")
            document = json.loads(args.document.read_text(encoding="utf-8"))
            payload = prepare_objective_revision(**common, document=document, operation_id=args.operation_id, execute=args.execute)
        elif args.action == "refine":
            if args.document is None or not args.operation_id or not args.expected_provider_revision:
                raise ValueError("refine requires --document, --operation-id and --expected-provider-revision from inspect")
            refinement = json.loads(args.document.read_text(encoding="utf-8"))
            if not isinstance(refinement, dict):
                raise ValueError("outcome refinement must be a JSON object")
            payload = public_goal_acceptance(refine_goal_outcome(**common, refinement=refinement,
                operation_id=args.operation_id, expected_provider_revision=args.expected_provider_revision, execute=args.execute))
        elif args.action == "prepare":
            from ..control_plane.goals.acceptance_actions import prepare_acceptance_check
            if args.document is None or not args.operation_id:
                raise ValueError("prepare requires --document and a stable --operation-id")
            if args.expected_provider_revision:
                raise ValueError("prepare reads the current basis itself")
            document = json.loads(args.document.read_text(encoding="utf-8"))
            if not isinstance(document, dict):
                raise ValueError("acceptance document must be a JSON object")
            payload = prepare_acceptance_check(**common, document=document,
                operation_id=args.operation_id, execute=args.execute)
        elif args.action in {"configure", "clear-checks"}:
            if not args.expected_provider_revision:
                raise ValueError(
                    "configure/clear-checks requires --expected-provider-revision from inspect"
                )
            if args.action == "configure" and args.document is None:
                raise ValueError("configure requires --document")
            if args.action == "clear-checks" and args.document is not None:
                raise ValueError("disable does not accept --document")
            document = (
                json.loads(args.document.read_text(encoding="utf-8"))
                if args.document
                else None
            )
            if document is not None and not isinstance(document, dict):
                raise ValueError("acceptance document must be a JSON object")
            payload = public_goal_acceptance(
                configure_goal_acceptance(
                    **common,
                    document=document,
                    expected_provider_revision=args.expected_provider_revision,
                    operation_id=args.operation_id,
                    execute=args.execute,
                )
            )
        else:
            if args.document or args.expected_provider_revision or args.operation_id:
                raise ValueError("configuration arguments require configure or disable")
            if args.action == "inspect":
                if args.execute:
                    raise ValueError("inspect is read-only")
                payload = public_goal_acceptance(inspect_goal_acceptance(**common))
            else:
                payload = verify_goal_acceptance(**common, execute=args.execute)
        if (payload.get("proposal") or {}).get("action_kind") == "goal.update":
            prefix = ["loopx", "--registry", str(registry_path), "--format", "json"]
            if runtime_root_arg:
                prefix.extend(["--runtime-root", runtime_root_arg])
            proposal_args = ["--goal-id", args.goal_id, "--proposal-id", payload["proposal"]["proposal_id"]]
            payload["next_cli_actions"] = [shlex.join(prefix + ["goal-acceptance", "inspect-proposal"] + proposal_args)]
            if payload["proposal"]["status"] in {"preview_ready", "deferred", "applying"}:
                payload["next_cli_actions"].append(shlex.join(prefix + ["goal-acceptance", "apply-proposal"] + proposal_args + ["--execute"]))
            payload["next_action"] = "Surface this proposal once and continue independently authorized work under the current Goal. Apply only under existing explicit local-owner authorization; do not ask again for an already authorized unchanged proposal. After apply, each Agent reviews and adopts for itself."
        code = (
            1
            if payload.get("ok") is False
            or payload.get("checks_passed") is False
            or payload.get("acceptance_ready") is False
            else 0
        )
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        payload, code = {
            "ok": False, "error": str(exc),
            **({"error_code": exc.code, **exc.payload} if isinstance(exc, GoalAcceptanceRejected) else {}),
        }, 1
    print_payload(payload, output_format(args), render_goal_acceptance)
    return code
