"""Inspect and correct host-local runtime observations without editing Goal policy."""
from ..agent_registry import load_goal_from_registry
from ..control_plane.agents.capability_memory import agent_capability_memory, resolve_agent_capabilities
from ..control_plane.effect_runtime import EffectRuntimeRemoteError


def register_agent_capabilities(subparsers, add_format):
    parser = subparsers.add_parser(
        "agent-capabilities", help="Inspect or correct a registered Agent's local runtime capabilities."
    )
    add_format(parser)
    parser.add_argument("--goal-id", required=True)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--available-capability", action="append", dest="available")
    parser.add_argument("--unavailable-capability", action="append", dest="unavailable")
    parser.add_argument("--forget-capability", action="append", dest="forget",
                        help="Remove a local observation and restore Goal inheritance.")
    parser.add_argument("--execute", action="store_true", help="Apply the observation changes; otherwise preview.")


def handle_agent_capabilities(args, registry_path, runtime_root, print_payload, output_format):
    try:
        state = agent_capability_memory(
            registry_path=registry_path, runtime_root=runtime_root,
            goal_id=args.goal_id, agent_id=args.agent_id,
            available=args.available, unavailable=args.unavailable, forget=args.forget,
            execute=args.execute,
        )
        availability = resolve_agent_capabilities(
            {}, goal_id=args.goal_id, agent_identity=None, available=[], item=load_goal_from_registry(registry_path, args.goal_id) or {},
            project_asset={}, state=state,
        )
        payload = {"ok": True, "agent_capabilities": state, "availability": availability}
    except (ValueError, TypeError, OSError, EffectRuntimeRemoteError) as exc:
        payload = {"ok": False, "error": str(exc)}
    print_payload(payload, output_format(args), render_agent_capabilities)
    return 0 if payload["ok"] else 1


def render_agent_capabilities(payload):
    if not payload["ok"]:
        return payload["error"]
    state = payload["agent_capabilities"]
    lines = [f"Runtime observations: {state['goal_id']} / {state['agent_id']} (this host)"]
    for key in ("goal", "probed", "agent", "unavailable", "effective"):
        if key in payload["availability"]:
            lines.append(f"{key}: {', '.join(payload['availability'][key]) or '(none)'}")
    if "proposed" in state:
        lines.append(f"Preview: {state['proposed']}; use --execute to apply.")
    if state["invocation_only"]:
        lines.append("Not remembered: " + ", ".join(state["invocation_only"]))
    lines.append("Runtime observations do not grant credentials, production access or enable optional capabilities.")
    return "\n".join(lines)
