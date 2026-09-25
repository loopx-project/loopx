"""Host CLI adapter; all continuation and ownership decisions stay in TypeScript."""
from pathlib import Path
import json

from ..agent_registry import registered_agent_ids_from_registry
from ..history import load_registry
from ..paths import resolve_runtime_root
from ..control_plane.effect_runtime import effect_runtime_result


def _render_digest(payload: dict) -> str:
    """Render an inspect payload as a readable handoff digest for the target agent."""
    if not payload.get("ok"):
        return json.dumps(payload, indent=2, ensure_ascii=False)
    lines: list[str] = []
    lines.append("# Handoff Context")
    source = payload.get("claimed_by", "unknown")
    note_state = payload.get("note_state", "missing")
    lines.append(f"Source: {source} | Status: {note_state}")
    lines.append(f"Can adopt: {'yes' if payload.get('can_adopt') else 'no'}")
    execution = payload.get("execution_authority")
    if isinstance(execution, dict) and execution.get("allowed") is False:
        lines.append(f"Execution authority: {execution.get('reason_code', 'unavailable')}")
    lines.append("")
    digest = payload.get("digest")
    if digest:
        if digest.get("work_summary"):
            lines.append("## Summary")
            lines.append(str(digest["work_summary"]))
            lines.append("")
        if digest.get("rationale"):
            lines.append("## Rationale")
            lines.append(str(digest["rationale"]))
            lines.append("")
        if digest.get("approaches_tried"):
            lines.append("## What was tried")
            for a in digest["approaches_tried"]:
                approach = a.get("approach", "")
                outcome = a.get("outcome", "")
                reason = a.get("reason", "")
                lines.append(f"- {approach} → {outcome}: {reason}")
            lines.append("")
        if digest.get("next_steps"):
            lines.append("## Next steps")
            for i, step in enumerate(digest["next_steps"], 1):
                lines.append(f"{i}. {step}")
            lines.append("")
        if digest.get("key_decisions"):
            lines.append("## Key decisions")
            for d in digest["key_decisions"]:
                lines.append(f"- {d.get('decision', '')}: {d.get('rationale', '')}")
            lines.append("")
        if digest.get("files_touched"):
            lines.append("## Files touched")
            for f in digest["files_touched"]:
                summary = f" — {f['summary']}" if f.get("summary") else ""
                lines.append(f"- {f.get('path', '')} ({f.get('action', '')}){summary}")
            lines.append("")
        if digest.get("open_questions"):
            lines.append("## Open questions")
            for q in digest["open_questions"]:
                lines.append(f"- {q}")
            lines.append("")
        if digest.get("source_refs"):
            lines.append("## Source references")
            for ref in digest["source_refs"]:
                lines.append(f"- {ref}")
            lines.append("")
    else:
        lines.append("No continuation note present.")
        lines.append("")
    # Availability
    availability = payload.get("availability")
    if availability:
        lines.append("## Environment")
        ws = availability.get("workspace_available")
        lines.append(f"Workspace: {'available' if ws else 'missing'}")
        for a in availability.get("artifacts", []):
            status = "✓" if a.get("available") else "✗"
            lines.append(f"- {status} {a.get('ref', '')}")
        lines.append("")
    # Next step
    next_step = payload.get("next_step")
    if next_step:
        lines.append(f"## Next step\n{next_step}")
    return "\n".join(lines)


def register_todo_continuation(subparsers, add_format):
    parser = subparsers.add_parser(
        "handoff", help="Explicit cross-agent Todo handoff: prepare/inspect/adopt ownership, or restore content without authority changes."
    )
    # Note: we don't use add_format here because we need a custom --format
    # with a 'digest' choice. We add it manually below.
    parser.add_argument("action", choices=["prepare", "inspect", "adopt", "restore"])
    parser.add_argument("--goal-id")
    parser.add_argument("--todo-id")
    parser.add_argument("--agent-id")
    parser.add_argument("--session-id", help="Current host session identifier; provenance, not authorization.")
    parser.add_argument("--input", help="restore only: producer output file, or - for stdin.")
    parser.add_argument("--input-format", choices=["json", "markdown"], default="json",
                        help="restore input representation; JSON is recommended for transport fidelity.")
    parser.add_argument("--operation-id", help="Stable retry identity, required for prepare/adopt.")
    parser.add_argument("--expected-revision", help="Exact revision from inspect, required for prepare/adopt.")
    parser.add_argument("--rationale", help="Decision rationale (legacy, prepare only). Prefer --from-context for rich handoff.")
    parser.add_argument("--source-ref", action="append", default=[], help="Compact source pointer (legacy, prepare only). Prefer --from-context.")
    parser.add_argument("--workspace", default=".", help="Target workspace to check locally; not persisted.")
    parser.add_argument("--artifact", action="append", default=[], help="Required workspace-relative artifact to check locally.")
    parser.add_argument("--target-agent-id", help="For adopt: the registered agent to hand off to (default: current agent).")
    parser.add_argument("--task-lease-idempotency-key", help="Current execution key; never a transfer grant.")
    parser.add_argument("--task-lease-expected-version", type=int, help="Current lease version paired with the execution key.")
    parser.add_argument("--from-context", help="Path to a JSON file containing rich handoff context (work_summary, approaches_tried, next_steps, files_touched, key_decisions, open_questions).")
    parser.add_argument("--format", dest="handoff_format", choices=["markdown", "json", "digest"],
        help="Output format. 'digest' renders inspect as a readable handoff summary (inspect only).")


def handle_todo_continuation(args, *, registry_path, runtime_root_arg, output_format, print_payload):
    if args.command != "handoff":
        return None
    if args.action == "restore":
        from .handoff_restore import handle_handoff_restore
        return handle_handoff_restore(args, output_format=output_format, print_payload=print_payload)
    try:
        if args.input or args.input_format != "json":
            raise ValueError("--input/--input-format are only valid for restore")
        if not all((args.goal_id, args.todo_id, args.agent_id, args.session_id)):
            raise ValueError("prepare/inspect/adopt require --goal-id, --todo-id, --agent-id and --session-id")
        if args.action != "inspect" and (not args.operation_id or not args.expected_revision):
            raise ValueError("prepare/adopt require --operation-id and --expected-revision; reuse both on retry")
        if args.action != "prepare" and (args.rationale or args.source_ref):
            raise ValueError("decision rationale and source references belong to prepare")
        if args.action != "adopt" and args.target_agent_id:
            raise ValueError("--target-agent-id is only valid for adopt action")
        if args.action != "inspect" and getattr(args, "handoff_format", None) == "digest":
            raise ValueError("--format digest is only valid for inspect action")
        registry = load_registry(registry_path)
        root = resolve_runtime_root(registry, runtime_root_arg)
        # Nest context separately; TypeScript owns its closed semantic schema.
        request = {
            "runtime_root": str(root),
            "goal_id": args.goal_id,
            "todo_id": args.todo_id,
            "agent_id": args.agent_id,
            "registered_agents": registered_agent_ids_from_registry(registry_path, args.goal_id),
            "action": args.action,
            "session_id": args.session_id,
            "operation_id": args.operation_id,
            "expected_provider_revision": args.expected_revision,
            "workspace": str(Path(args.workspace).expanduser().resolve()),
            "artifacts": args.artifact,
            "target_agent_id": args.target_agent_id,
        }
        if args.action == "prepare":
            if getattr(args, "from_context", None):
                context_path = Path(args.from_context).expanduser().resolve()
                with open(context_path, "r", encoding="utf-8") as f:
                    context = json.load(f)
                request["context"] = context
            else:
                # Legacy: pass through rationale and source_refs.
                if args.rationale:
                    request["rationale"] = args.rationale
                if args.source_ref:
                    request["source_refs"] = args.source_ref
        key = args.task_lease_idempotency_key
        version = args.task_lease_expected_version
        if key is not None or version is not None:
            request["lease_proof"] = {"idempotency_key": key, "expected_version": version}
        payload = effect_runtime_result("coordination.local_authority.todo_continuation", request)
    except (ValueError, RuntimeError, OSError, json.JSONDecodeError) as exc:
        payload = {"ok": False, "status": "failed",
            "reason_code": "invalid_continuation_request", "reason": str(exc)}
    # Delivery retries project the current provider head without replaying a write.
    committed = payload.get("result") or payload.get("claim") or payload.get("adoption") or {}
    if args.action != "inspect" and committed.get("status") in {"applied", "replayed", "recovered", "no_change"}:
        from ..control_plane.todos.provider_projection import settle_canonical_todo_projection
        payload = settle_canonical_todo_projection(payload, registry_path=registry_path,
            runtime_root=root, goal_id=args.goal_id)
    # Determine output format.
    handoff_format = getattr(args, "handoff_format", None)
    if handoff_format:
        fmt = handoff_format
    else:
        fmt = output_format(args)
    if fmt == "digest":
        print(_render_digest(payload))
    else:
        print_payload(payload, fmt, lambda value: json.dumps(value, indent=2, ensure_ascii=False))
    return 0 if payload.get("ok") else 1
