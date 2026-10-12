"""CLI/host adaptation for the TS-owned explicit preference lifecycle."""
from pathlib import Path
import shlex
from typing import Any

from ...agent_registry import load_goal_from_registry, registered_agent_ids_for_goal
from ...control_plane.effect_runtime import effect_runtime_result


def agent_preferences(*, registry_path: Path, runtime_root: Path, goal_id: str,
                      agent_id: str, action: str = "read", **fields):
    goal = load_goal_from_registry(registry_path, goal_id)
    if not goal or not goal.get("repo") or not goal.get("state_file"):
        raise ValueError("Agent preferences require a registered Goal with a project and state file")
    from ...paths import registry_project_root
    project = Path(str(goal["repo"])).expanduser()
    if not project.is_absolute():
        project = registry_project_root(registry_path) / project
    owner = (project / str(goal["state_file"])).resolve()
    result = effect_runtime_result("agent.preferences", {
        "schema_version": "agent_preferences_request_v1",
        "goal_state_ref": str(owner),
        "goal_instance_id": goal.get("goal_instance_id"),
        "runtime_root": str(runtime_root.expanduser().resolve()),
        "goal_id": goal_id, "agent_id": agent_id,
        "registered_agents": registered_agent_ids_for_goal(goal),
        "action": action, **fields,
    })
    if not isinstance(result, dict):
        raise TypeError("invalid agent preferences response")
    return result


def extend_turn_start_dispatch(
    dispatch: dict[str, Any] | None,
    *,
    runtime_root: Path,
    registry_path: Path,
    goal_id: str,
    agent_id: str | None,
) -> Any:
    # Explicit local use creates this namespace. Untouched runtimes do not
    # invoke the preference provider or add capability instructions to a guard.
    if not agent_id:
        return dispatch
    try:
        (runtime_root / "agent-preferences").stat()
    except FileNotFoundError:
        return dispatch
    except OSError:
        # Let the typed provider disclose denial/failure rather than treating
        # an inaccessible existing namespace as disabled or empty context.
        pass
    from ...control_plane.capability_hooks import (
        TurnStartHookRegistration, TURN_START_HOOK_RESULT_SCHEMA_VERSION, dispatch_turn_start_hooks,
    )

    snapshot = None

    def produce():
        nonlocal snapshot
        state = agent_preferences(registry_path=registry_path, runtime_root=runtime_root,
                                  goal_id=goal_id, agent_id=agent_id, action="turn_context")
        snapshot = state
        if not state.get("ok"):
            # The dispatcher exposes this failure; never substitute cached prose.
            return {
                "schema_version": TURN_START_HOOK_RESULT_SCHEMA_VERSION,
                "hook_id": "semantic_preference.agent_context", "capability_id": "semantic-preference",
                "phase": "turn_start", "status": "unavailable", "observation_count": 0,
                "agent_read_required": False, "external_reads_performed": False,
                "external_writes_performed": False, "local_private_state_mutated": False,
                "private_content_returned": False, "provider_payload_returned": False,
                "error_code": ("agent_preferences_permission_denied"
                    if state.get("status") == "permission_denied" else "agent_preferences_unreadable"),
            }
        count = state["observation_count"]
        return {
            "schema_version": TURN_START_HOOK_RESULT_SCHEMA_VERSION,
            "hook_id": "semantic_preference.agent_context", "capability_id": "semantic-preference",
            "phase": "turn_start", "status": "observed" if count else "empty",
            "observation_count": count, "agent_read_required": count > 0,
            "external_reads_performed": False, "external_writes_performed": False,
            "local_private_state_mutated": False, "private_content_returned": False,
            "provider_payload_returned": False, "error_code": None,
        }

    command = shlex.join(["loopx", "--registry", str(registry_path), "--runtime-root",
        str(runtime_root), "semantic-preference", "agent", "read", "--goal-id", goal_id,
        "--agent-id", agent_id, "--format", "json"])
    hook = TurnStartHookRegistration(
        hook_id="semantic_preference.agent_context", capability_id="semantic-preference",
        requested_read_scope=("owner_private_agent_preferences",), requested_write_scope=(),
        producer=produce,
        context_reader=lambda: {"ok": True, "status": "read", "current": snapshot["current"]},
        required_read={"kind": "agent_preferences", "command": command,
            "reason": "Read current preferences and retirements; apply explicit user corrections before acting. Memory is not permission.",
            "ordering": "before_work"},
    )

    extra = dispatch_turn_start_hooks((hook,))
    # Another Goal/Agent's journal does not opt this scope into preference
    # context. Retirements and expiry remain present and must invalidate cache.
    if snapshot is not None and snapshot.get("ok") and snapshot.get("status") == "absent":
        return dispatch
    result = dict(dispatch or {})
    for key in ("results", "required_reads", "failures", "contexts"):
        if key == "contexts" and not extra.get(key):
            continue
        result[key] = list(result.get(key) or []) + list(extra.get(key) or [])
    for key in ("registered_count", "invoked_count"):
        result[key] = int(result.get(key) or 0) + int(extra.get(key) or 0)
    return result


def register_agent_preference_commands(commands, add_format):
    parser = commands.add_parser("agent", help="Read, correct or retire explicit local Agent preferences.")
    parser.add_argument("agent_preference_action", choices=["read", "remember", "retire", "history"])
    add_format(parser)
    parser.add_argument("--goal-id", required=True)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--key", help="Stable preference subject; corrections reuse this key.")
    parser.add_argument("--statement")
    parser.add_argument("--source-ref", help="Private reference to the explicit user instruction.")
    parser.add_argument("--source-quote", help="Bounded exact user wording; do not include secrets.")
    parser.add_argument("--expected-revision", help="Current read revision; use 'none' only for an empty store.")
    parser.add_argument("--operation-id", help="Keep unchanged when retrying an uncertain write.")
    parser.add_argument("--expires-at", help="Optional UTC ISO expiry of a remembered preference.")
    parser.add_argument("--after-cursor", help="History page continuation.")
    parser.add_argument("--execute", action="store_true", help="Commit a correction; otherwise preview only.")


def handle_agent_preferences(args, *, registry_path, runtime_root_arg):
    from ...history import load_registry
    from ...paths import resolve_runtime_root
    root = resolve_runtime_root(load_registry(registry_path), runtime_root_arg, registry_path=registry_path)
    action = args.agent_preference_action
    fields = {"execute": args.execute}
    if action in ("remember", "retire"):
        if args.expected_revision is None:
            raise ValueError("read preferences first, then pass --expected-revision (or 'none' for an empty store)")
        fields["expected_revision"] = None if args.expected_revision == "none" else args.expected_revision
    if action in ("remember", "retire"):
        fields.update(key=args.key, statement=args.statement, source_kind="user_instruction",
                      source_ref=args.source_ref, source_quote=args.source_quote,
                      operation_id=args.operation_id, expires_at=args.expires_at)
    if action == "history":
        fields["after_cursor"] = args.after_cursor
    return agent_preferences(registry_path=registry_path, runtime_root=root,
                             goal_id=args.goal_id, agent_id=args.agent_id, action=action, **fields)


def render_agent_preferences(payload):
    import json
    # The same owner-local read is used by the scoped turn context hook.
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
