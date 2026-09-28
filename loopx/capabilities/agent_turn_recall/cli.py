from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ...history import load_registry
from ...materials import find_registry_goal, goal_repo
from ..reward_memory.experiment import resolve_reward_memory_experiment
from .core import (
    AGENT_TURN_RECALL_SCHEMA_VERSION,
    build_agent_turn_recall_preview,
    build_agent_turn_situation,
    run_agent_turn_recall,
)
from .runtime import (
    AGENT_TURN_RECALL_RECEIPT_SCHEMA_VERSION,
    agent_turn_recall_receipt_path,
    deduplicated_agent_turn_recall_payload,
    load_agent_turn_recall_receipt,
    resolve_reward_memory_turn_session_ref,
    reward_memory_turn_identity_scope,
    reward_memory_turn_read_authority_checkpoints,
    write_agent_turn_recall_receipt,
)


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _render(payload: dict[str, object]) -> str:
    lines = ["# Agent Turn Recall", ""]
    for key in (
        "status",
        "goal_id",
        "agent_id",
        "surface_id",
        "provider_call_count",
    ):
        if payload.get(key) is not None:
            lines.append(f"- {key}: `{payload[key]}`")
    context = payload.get("context")
    guidance = context.get("guidance") if isinstance(context, Mapping) else None
    if isinstance(guidance, list):
        lines.append(f"- guidance_count: `{len(guidance)}`")
    return "\n".join(lines) + "\n"


def register_agent_turn_recall_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    add_subcommand_format: Callable[[argparse.ArgumentParser], None],
) -> None:
    parser = subparsers.add_parser(
        "agent-turn-recall",
        help="Recall scoped memory from the current autonomous Goal/Todo situation.",
    )
    add_subcommand_format(parser)
    parser.add_argument("--goal-id", required=True)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--turn-instance-id", required=True)
    parser.add_argument(
        "--quota-decision-json",
        required=True,
        help="Exact quota should-run JSON path, or - for stdin.",
    )
    parser.add_argument("--session-ref")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Run the configured provider and write one ignored same-turn receipt.",
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Ignore a matching same-turn receipt and query the provider again.",
    )


def _goal_repo(registry_path: Path, goal_id: str) -> Path:
    goal = find_registry_goal(load_registry(registry_path), goal_id)
    resolved = goal_repo(goal) if goal else None
    if resolved is None:
        raise ValueError(f"goal `{goal_id}` repository is unavailable")
    repo = Path(resolved)
    if not repo.is_dir():
        raise ValueError(f"goal `{goal_id}` repository is unavailable")
    return repo


def _receipt_path(repo: Path, goal_id: str, agent_id: str) -> Path:
    return agent_turn_recall_receipt_path(
        repo,
        goal_id=goal_id,
        agent_id=agent_id,
    )


def _quota_decision(path_value: str) -> dict[str, Any]:
    try:
        if path_value == "-":
            payload = json.load(sys.stdin)
        else:
            payload = json.loads(
                Path(path_value).expanduser().read_text(encoding="utf-8")
            )
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("quota decision must be readable JSON") from exc
    if not isinstance(payload, dict) or payload.get("mode") != "should-run":
        raise ValueError("quota decision must be one quota should-run object")
    return payload


def _validate_quota_identity(
    quota_decision: Mapping[str, Any],
    *,
    goal_id: str,
    agent_id: str,
    turn_instance_id: str,
) -> None:
    if quota_decision.get("goal_id") != goal_id:
        raise ValueError("quota decision goal_id does not match --goal-id")
    decision_agent = _mapping(quota_decision.get("agent_identity")).get("agent_id")
    if decision_agent != agent_id:
        raise ValueError("quota decision agent_id does not match --agent-id")
    receipt = _mapping(quota_decision.get("heartbeat_receipt"))
    if receipt.get("turn_instance_id") != turn_instance_id:
        raise ValueError(
            "quota decision turn_instance_id does not match --turn-instance-id"
        )
    if receipt.get("status") not in {"committed", "replayed"}:
        raise ValueError("quota decision heartbeat receipt is not committed")


def _load_receipt(path: Path) -> dict[str, Any] | None:
    return load_agent_turn_recall_receipt(path)


def _write_receipt(path: Path, payload: Mapping[str, Any]) -> None:
    write_agent_turn_recall_receipt(path, payload)


def _identity_scope(config: Mapping[str, Any]) -> dict[str, str | None]:
    return reward_memory_turn_identity_scope(config)


def _resolve_session_ref(
    identity: Mapping[str, str | None], requested: str | None
) -> str | None:
    return resolve_reward_memory_turn_session_ref(identity, requested)


def _read_authority_checkpoints(
    config: Mapping[str, Any], goal_id: str
) -> dict[str, dict[str, Any]]:
    return reward_memory_turn_read_authority_checkpoints(config, goal_id)


def _deduplicated_payload(
    receipt: Mapping[str, Any],
    *,
    goal_id: str,
    agent_id: str,
) -> dict[str, Any] | None:
    return deduplicated_agent_turn_recall_payload(
        receipt,
        goal_id=goal_id,
        agent_id=agent_id,
    )


def handle_agent_turn_recall_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    output_format: Callable[..., str],
    print_payload: Callable[..., None],
) -> int | None:
    if args.command != "agent-turn-recall":
        return None
    try:
        goal_repo = _goal_repo(registry_path, args.goal_id)
        experiment_status, config = resolve_reward_memory_experiment(
            registry_path=registry_path,
            goal_id=args.goal_id,
            agent_id=args.agent_id,
        )
        if config is None:
            payload: dict[str, Any] = {
                "ok": True,
                "schema_version": AGENT_TURN_RECALL_SCHEMA_VERSION,
                "status": str(experiment_status.get("status") or "not_available"),
                "reason_code": experiment_status.get("reason_code"),
                "goal_id": args.goal_id,
                "agent_id": args.agent_id,
                "experiment": experiment_status,
                "provider_call_count": 0,
                "grants_new_action_authority": False,
                "quota_spend_performed": False,
                "external_writes_performed": False,
                "suppress_external_sinks": True,
            }
        else:
            identity = _identity_scope(config)
            quota_decision = _quota_decision(args.quota_decision_json)
            _validate_quota_identity(
                quota_decision,
                goal_id=args.goal_id,
                agent_id=args.agent_id,
                turn_instance_id=args.turn_instance_id,
            )
            situation = build_agent_turn_situation(
                quota_decision,
                goal_id=args.goal_id,
                agent_id=args.agent_id,
                turn_instance_id=args.turn_instance_id,
                workspace_ref=str(identity["workspace_ref"] or ""),
                project_ref=str(identity["project_ref"] or ""),
                user_ref=identity["user_ref"],
                session_ref=_resolve_session_ref(identity, args.session_ref),
            )
            receipt_path = _receipt_path(goal_repo, args.goal_id, args.agent_id)
            previous = None if args.force_refresh else _load_receipt(receipt_path)
            deduplicated = (
                _deduplicated_payload(
                    previous,
                    goal_id=args.goal_id,
                    agent_id=args.agent_id,
                )
                if previous
                and previous.get("turn_recall_id") == situation["turn_recall_id"]
                else None
            )
            if deduplicated is not None:
                payload = deduplicated
            elif not args.execute:
                payload = build_agent_turn_recall_preview(situation) | {
                    "goal_id": args.goal_id,
                    "agent_id": args.agent_id,
                    "experiment": experiment_status,
                }
            else:
                try:
                    read_checkpoints = _read_authority_checkpoints(config, args.goal_id)
                except RuntimeError:
                    # This failure precedes the provider; do not fabricate a
                    # zero-call receipt for errors after recall has begun.
                    payload = {
                        "ok": False,
                        "schema_version": AGENT_TURN_RECALL_SCHEMA_VERSION,
                        "status": "runtime_unavailable",
                        "reason_code": "automatic_recall_runtime_failed",
                        "goal_id": args.goal_id,
                        "agent_id": args.agent_id,
                        "provider_call_count": 0,
                        "grants_new_action_authority": False,
                        "quota_spend_performed": False,
                        "external_writes_performed": False,
                        "suppress_external_sinks": True,
                    }
                    print_payload(payload, output_format(args), _render)
                    return 2
                payload = run_agent_turn_recall(
                    config,
                    situation,
                    observed_at=datetime.now(timezone.utc).isoformat(),
                    read_authority_checkpoints=read_checkpoints,
                ) | {
                    "goal_id": args.goal_id,
                    "agent_id": args.agent_id,
                    "experiment": experiment_status,
                }
                if payload.get("status") in {"applied", "not_available"}:
                    _write_receipt(
                        receipt_path,
                        {
                            "schema_version": AGENT_TURN_RECALL_RECEIPT_SCHEMA_VERSION,
                            "goal_id": args.goal_id,
                            "agent_id": args.agent_id,
                            "turn_recall_id": payload.get("turn_recall_id"),
                            "situation_fingerprint": payload.get(
                                "situation_fingerprint"
                            ),
                            "source_status": payload.get("status"),
                            "context": payload.get("context"),
                        },
                    )
                    payload["same_turn_receipt_written"] = True
    except (OSError, TypeError, ValueError) as exc:
        payload = {
            "ok": False,
            "schema_version": AGENT_TURN_RECALL_SCHEMA_VERSION,
            "status": "invalid_request",
            "goal_id": args.goal_id,
            "agent_id": args.agent_id,
            "error": str(exc),
            "provider_call_count": 0,
            "grants_new_action_authority": False,
            "quota_spend_performed": False,
            "external_writes_performed": False,
            "suppress_external_sinks": True,
        }
    print_payload(payload, output_format(args), _render)
    return 0 if payload.get("ok") else 2
