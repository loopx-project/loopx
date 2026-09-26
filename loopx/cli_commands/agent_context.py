"""Read-only lifecycle context for hosts whose native tools bypass LoopX Turn."""

from ..agent_registry import load_goal_from_registry, registered_agent_ids_for_goal
from ..capabilities.multi_subagent.native_child_receipts import load_native_child_activity
from ..control_plane.agent_context import project_goal_agent_context
from ..orchestration import compact_orchestration_policy


def register_agent_context(subparsers, add_format):
    parser = subparsers.add_parser(
        "agent-context", help="Read enabled capability guidance for the coordinator."
    )
    add_format(parser)
    parser.add_argument("--goal-id", required=True)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument(
        "--phase",
        required=True,
        choices=("before_plan", "before_delegate", "after_delegate_result"),
    )
    parser.add_argument(
        "--native-child-operation",
        choices=("spawn", "followup"),
        help="Typed native child operation observed by the current host.",
    )
    parser.add_argument(
        "--native-child-outcome",
        choices=("succeeded", "agent_thread_limit_reached"),
        help="Typed native child outcome; raw host errors are never accepted.",
    )
    parser.add_argument(
        "--native-child-count",
        type=int,
        help="Optional non-negative native child count observed by the host.",
    )
    parser.add_argument(
        "--turn-instance-id",
        help="Read durable native child activity for this exact admitted Turn.",
    )


def handle_agent_context(args, registry_path, runtime_root, print_payload, output_format):
    goal = load_goal_from_registry(registry_path, args.goal_id)
    if goal is None or args.agent_id not in registered_agent_ids_for_goal(goal):
        print_payload(
            {"ok": False, "error": "coordinator is not registered for this Goal"},
            output_format(args),
            render_agent_context,
        )
        return 1
    operation = args.native_child_operation
    outcome = args.native_child_outcome
    child_count = args.native_child_count
    if bool(operation) != bool(outcome):
        print_payload(
            {
                "ok": False,
                "error": (
                    "--native-child-operation and --native-child-outcome "
                    "must be provided together"
                ),
            },
            output_format(args),
            render_agent_context,
        )
        return 1
    if operation and args.phase != "after_delegate_result":
        print_payload(
            {
                "ok": False,
                "error": "native child outcomes require --phase after_delegate_result",
            },
            output_format(args),
            render_agent_context,
        )
        return 1
    if child_count is not None and (child_count < 0 or not operation):
        print_payload(
            {
                "ok": False,
                "error": (
                    "--native-child-count must be non-negative and accompany "
                    "a native child operation/outcome"
                ),
            },
            output_format(args),
            render_agent_context,
        )
        return 1
    if args.turn_instance_id and args.phase != "after_delegate_result":
        print_payload(
            {"ok": False, "error": "Turn child receipts require --phase after_delegate_result"},
            output_format(args), render_agent_context,
        )
        return 1
    observations = {}
    if operation:
        native_capacity = {
            "schema_version": "native_subagent_capacity_observation_v0",
            "operation": operation,
            "outcome": outcome,
        }
        if child_count is not None:
            native_capacity["child_count"] = child_count
        observations["native_host_capacity"] = native_capacity
    native_activity = None
    if args.turn_instance_id:
        orchestration = compact_orchestration_policy(goal.get("spawn_policy"))
        if (orchestration.get("mode") != "multi_subagent"
                or orchestration.get("spawn_allowed") is not True
                or int(orchestration.get("max_children") or 0) < 1):
            print_payload(
                {"ok": False, "error": "Turn child receipts require enabled multi_subagent policy"},
                output_format(args), render_agent_context,
            )
            return 1
        native_activity = load_native_child_activity(
            runtime_root, goal_id=args.goal_id, agent_id=args.agent_id,
            turn_instance_id=args.turn_instance_id,
            configured_limit=int(orchestration["max_children"]),
        )
        observations["native_child_activity"] = native_activity
    context = project_goal_agent_context(
        phase=args.phase,
        scope={"goal_id": args.goal_id, "agent_id": args.agent_id, "todo_id": None},
        goal=goal,
        registry_path=registry_path,
        runtime_root=runtime_root,
        observations=observations,
    )
    if operation and context is None:
        print_payload(
            {
                "ok": False,
                "error": "native child outcomes require enabled multi_subagent policy",
            },
            output_format(args),
            render_agent_context,
        )
        return 1
    payload = {
        "ok": True,
        "agent_context": context,
        "source": "registry.spawn_policy+local_delegation",
        "read_only": True,
        "host_receipts_observed": False,
        "host_receipts_scope": "native_tool_input",
    }
    if operation:
        payload.update(
            {
                "host_capacity_observed": True,
                "host_capacity_scope": "native_tool_input",
            }
        )
    if native_activity is not None:
        payload["native_child_activity"] = native_activity
        payload["host_receipts_scope"] = "turn_bound_coordinator_report"
    print_payload(payload, output_format(args), render_agent_context)
    return 0


def render_agent_context(payload):
    if not payload["ok"]:
        return str(payload["error"])
    context = payload.get("agent_context")
    if not context:
        return "No enabled capability contributes coordinator context at this phase."
    lines = [
        f"Coordinator context: {context['phase']} (projected guidance; not delivery or adoption)"
    ]
    for contribution in context["contributions"]:
        lines.append(f"{contribution['capability_id']} / {contribution['revision']}")
        lines.extend(f"- {text}" for text in contribution["guidance"])
        for key, value in contribution["facts"].items():
            lines.append(f"- {key}: {value}")
    if context["failures"]:
        lines.append("Some context providers failed; inspect JSON diagnostics.")
    return "\n".join(lines)
