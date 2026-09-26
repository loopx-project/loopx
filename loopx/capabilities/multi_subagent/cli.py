"""Provider-neutral, Turn-scoped native child receipt commands."""

from __future__ import annotations

from ...agent_registry import load_goal_from_registry, registered_agent_ids_for_goal
from ...orchestration import compact_orchestration_policy
from .native_child_receipts import load_native_child_activity, record_native_child


def register_native_child_commands(subparsers, add_format):
    parser = subparsers.add_parser(
        "native-child", help="Record or read typed host-native child activity for an admitted Turn."
    )
    add_format(parser)
    parser.add_argument("native_child_action", choices=("record", "read"))
    parser.add_argument("--goal-id", required=True)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--turn-instance-id", required=True)
    parser.add_argument("--operation-id", help="Stable identity for one host-native child operation.")
    parser.add_argument("--stage", choices=("decision", "result", "review"))
    parser.add_argument("--operation", choices=("spawn", "followup", "skip"))
    parser.add_argument("--outcome")
    parser.add_argument("--entrypoint-id", help="Opaque host entrypoint identity, not a host-specific enum.")
    parser.add_argument("--reason-code")
    parser.add_argument("--evidence-ref", help="Public-safe opaque reference for accepted evidence.")
    parser.add_argument("--validation-ref", help="Public-safe opaque reference for parent validation.")
    parser.add_argument("--execute", action="store_true", help="Append the receipt; otherwise preview only.")


def handle_native_child_command(args, registry_path, runtime_root, print_payload, output_format):
    if args.command != "native-child":
        return None
    try:
        goal = load_goal_from_registry(registry_path, args.goal_id)
        if goal is None or args.agent_id not in registered_agent_ids_for_goal(goal):
            raise ValueError("coordinator is not registered for this Goal")
        orchestration = compact_orchestration_policy(goal.get("spawn_policy"))
        if not (
            orchestration.get("mode") == "multi_subagent"
            and orchestration.get("spawn_allowed") is True
            and int(orchestration.get("max_children") or 0) > 0
        ):
            raise ValueError("native child receipts require enabled multi_subagent policy")
        configured_limit = int(orchestration["max_children"])
        if args.native_child_action == "read":
            if any((args.operation_id, args.stage, args.operation, args.outcome,
                    args.entrypoint_id, args.reason_code, args.evidence_ref,
                    args.validation_ref, args.execute)):
                raise ValueError("read accepts only goal, agent and Turn identity")
            payload = {"ok": True, "native_child_activity": load_native_child_activity(
                runtime_root, goal_id=args.goal_id, agent_id=args.agent_id,
                turn_instance_id=args.turn_instance_id, configured_limit=configured_limit,
            )}
        else:
            if not args.operation_id or not args.stage or not args.outcome:
                raise ValueError("record requires operation-id, stage and outcome")
            payload = record_native_child(
                runtime_root=runtime_root, goal_id=args.goal_id, agent_id=args.agent_id,
                turn_instance_id=args.turn_instance_id, operation_id=args.operation_id,
                configured_limit=configured_limit, stage=args.stage,
                operation=args.operation, outcome=args.outcome,
                entrypoint_id=args.entrypoint_id, reason_code=args.reason_code,
                evidence_ref=args.evidence_ref, validation_ref=args.validation_ref,
                execute=args.execute,
            )
    except (OSError, ValueError, KeyError) as exc:
        payload = {"ok": False, "error": str(exc)}
    print_payload(payload, output_format(args), render_native_child)
    return 0 if payload["ok"] else 1


def render_native_child(payload):
    if not payload["ok"]:
        return str(payload["error"])
    activity = payload["native_child_activity"]
    return (
        f"Native child activity for {activity['turn_instance_id']}: "
        f"{activity['observation']}; {activity['launched_count']} reported starts, "
        f"{activity['skipped_count']} skips, "
        f"{activity['capacity_rejected_count']} capacity rejections, "
        f"{activity['host_failed_count']} host failures."
    )
