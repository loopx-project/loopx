"""Read-only lifecycle context for hosts whose native tools bypass LoopX Turn."""

from ..agent_registry import load_goal_from_registry, registered_agent_ids_for_goal
from ..control_plane.agent_context import project_goal_agent_context


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


def handle_agent_context(args, registry_path, runtime_root, print_payload, output_format):
    goal = load_goal_from_registry(registry_path, args.goal_id)
    if goal is None or args.agent_id not in registered_agent_ids_for_goal(goal):
        print_payload(
            {"ok": False, "error": "coordinator is not registered for this Goal"},
            output_format(args),
            render_agent_context,
        )
        return 1
    context = project_goal_agent_context(
        phase=args.phase,
        scope={"goal_id": args.goal_id, "agent_id": args.agent_id, "todo_id": None},
        goal=goal,
        registry_path=registry_path,
        runtime_root=runtime_root,
    )
    print_payload(
        {
            "ok": True,
            "agent_context": context,
            "source": "registry.spawn_policy+local_delegation",
            "read_only": True,
            "host_receipts_observed": False,
            "host_receipts_scope": "native_tool_input",
        },
        output_format(args),
        render_agent_context,
    )
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
