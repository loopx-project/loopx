"""CLI surface for governed goal amendment proposal submission and readback.

``loopx goal-amendment-proposal`` (alias: ``loopx amendment-proposal``) is
the Stage 2 production consumer of RFC shared-goal-alignment-and-governed-
amendment-v0: it submits one ``goal_amendment_proposal_v0`` payload through
the admission adapter and reads the retained append-only journal back.
Submission is proposal-only — admission has zero canonical effect; the
journal lives under ``runtime/goals/<goal_id>/amendment-proposals/``.
The causal replan obligation inventory is never a CLI input: admission
derives it on the spot from the authoritative quota run-history projection
under ``runtime/goals/<goal_id>/runs/``.

Submit and readback share one selector precedence (see
``_resolve_goal_routing``): the CLI-resolved registry (``--registry`` /
``LOOPX_REGISTRY`` / default) is authoritative for both modes and
``--project`` never replaces it with a project-local registry — two
registries can register the same Goal over different runtime roots, and a
readback that swapped registries would report another runtime's journal
even though the submit succeeded. The runtime root follows the same
registry: ``--runtime-root`` wins, else the registry's
``common_runtime_root``.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path

from ..control_plane.goals.goal_amendment_proposal import (
    admit_goal_amendment_proposal,
    read_goal_amendment_proposal_journal,
)
from ..control_plane.goals.shared_goal_alignment import (
    _registered_goal,
)
from ..history import load_registry
from ..runtime import validate_goal_id_path_segment
from ..presentation.markdown import markdown_scalar
from ..control_plane.todos.path_resolution import resolve_todo_state_path

PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]


def _read_json_object(path_text: str, label: str) -> dict[str, object]:
    path = Path(path_text).expanduser()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"{label} must contain a JSON object")
    return payload


def render_goal_amendment_proposal_markdown(
    payload: dict[str, object],
) -> str:
    lines = [
        "# LoopX Goal Amendment Proposal",
        "",
        f"- ok: `{payload.get('ok')}`",
    ]
    if payload.get("goal_id"):
        lines.append(f"- goal_id: `{payload.get('goal_id')}`")
    if payload.get("error"):
        lines.append(f"- error: {payload.get('error')}")
        return "\n".join(lines)
    if payload.get("schema_version") == "goal_amendment_result_v0":
        lines.extend([f"- status: `{payload.get('status')}`",
            f"- execution authority granted: `{payload.get('execution_authority_granted')}`"])
        for field in ("reason", "canonical_effect", "goal_revision", "todo_id", "replan_settlement", "peer_adoption"):
            if field in payload:
                lines.append(f"- {field}: `{payload[field]}`")
        intent = payload.get("intent")
        if isinstance(intent, dict):
            root = intent.get("root_intent") or {}
            lines.extend([f"- Goal revision: `{intent.get('goal_revision')}`",
                f"- Objective: {markdown_scalar(root.get('objective'))}"])
            for field in ("non_goals", "acceptance", "stop_conditions", "terminal_conditions"):
                lines.append(f"- {field}: {markdown_scalar(root.get(field))}")
            policy = intent.get("policy") or {}
            lines.append("- Templates: " + ", ".join(markdown_scalar(row.get("id")) for row in policy.get("templates", [])))
        readiness = payload.get("mutation_readiness")
        if isinstance(readiness, dict):
            lines.append(f"- Mutation readiness: {markdown_scalar(readiness.get('code') or readiness.get('kind'))}")
        return "\n".join(lines)
    if "rows" in payload:
        rows = payload.get("rows")
        if isinstance(rows, list):
            lines.append(f"- count: {payload.get('count', len(rows))}")
            for row in rows:
                if isinstance(row, dict):
                    lines.append(
                        f"- `{row.get('proposal_id')}` admission=`{row.get('admission')}`"
                        f" sequence={row.get('journal_append_sequence')}"
                    )
        return "\n".join(lines)
    if payload.get("proposer_agent_id"):
        lines.append(f"- proposer_agent_id: `{payload.get('proposer_agent_id')}`")
    if payload.get("amendment_class"):
        lines.append(f"- amendment_class: `{payload.get('amendment_class')}`")
    lines.append(f"- admission: `{payload.get('admission')}`")
    lines.append(f"- canonical_effect: `{payload.get('canonical_effect')}`")
    admission_facts = payload.get("admission_facts")
    if isinstance(admission_facts, list) and admission_facts:
        lines.append("- admission_facts:")
        for fact in admission_facts:
            lines.append(f"  - `{fact}`")
    if payload.get("proposal_digest"):
        lines.append(f"- proposal_digest: `{payload.get('proposal_digest')}`")
    if payload.get("replan_obligation_id"):
        lines.append(f"- replan_obligation_id: `{payload.get('replan_obligation_id')}`")
    if payload.get("journal_append_sequence") is not None:
        lines.append(
            f"- journal_append_sequence: {payload.get('journal_append_sequence')}"
        )
    return "\n".join(lines)


def register_goal_amendment_proposal_command(
    subparsers: argparse._SubParsersAction,
    add_subcommand_format: Callable[[argparse.ArgumentParser], None],
) -> None:
    parser = subparsers.add_parser(
        "goal-amendment-proposal",
        aliases=["amendment-proposal"],
        help=(
            "Submit one governed goal amendment proposal for admission "
            "(zero canonical effect) or read the retained proposal journal."
        ),
    )
    add_subcommand_format(parser)
    parser.add_argument(
        "--proposal-json",
        help=(
            "Path to a goal_amendment_proposal_v0 JSON object to submit "
            "(submit mode; mutually exclusive with --list). The causal "
            "replan obligation inventory is derived from the quota "
            "run-history projection at submit time; it cannot be injected."
        ),
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help=(
            "Read back every retained proposal admission row of one goal "
            "(readback mode; requires --goal-id)."
        ),
    )
    parser.add_argument(
        "--goal-id",
        help=(
            "Registered Goal id. Required with --list; in submit mode it "
            "must match proposal.goal_id when given."
        ),
    )
    parser.add_argument("--governed", metavar="OPERATION",
        help="Operate the exact-lifetime canonical amendment owner; legacy submission remains unchanged.")
    parser.add_argument("--goal-instance-id", help="Exact source-owned Goal lifetime for --governed.")
    parser.add_argument("--agent-id", help="Registered peer actor; root configuration is a trusted-owner operation.")
    parser.add_argument("--request-json", help="Bounded operation parameters; cannot replace source-owned facts.")
    parser.add_argument("--operation-id", help="Stable mutation id; reuse it after a lost response.")
    parser.add_argument("--execute", action="store_true", help="Commit an explicit governed mutation; default is a read-only preview.")
    parser.add_argument(
        "--project",
        help=(
            "Project directory containing the goal or active state. "
            "Defaults to the registry goal repository. Never selects the "
            "registry: the explicit --registry (or its resolved default) "
            "stays authoritative for both submit and --list, so the same "
            "selectors always read back what they submitted."
        ),
    )


def handle_goal_amendment_proposal_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    output_format: Callable[..., str],
    print_payload: PrintPayload,
) -> int | None:
    if args.command not in {"goal-amendment-proposal", "amendment-proposal"}:
        return None
    goal_id: str | None = getattr(args, "goal_id", None)
    try:
        runtime_root = Path(runtime_root_arg).expanduser() if runtime_root_arg else None
        if getattr(args, "governed", None):
            from ..control_plane.goals.goal_amendment_runtime import execute_goal_amendment
            if args.list or args.proposal_json or args.project:
                raise ValueError("--governed cannot replace legacy submit/list/project routing")
            if not goal_id or not args.goal_instance_id:
                raise ValueError("--governed requires --goal-id and --goal-instance-id")
            parameters = {}
            if args.request_json:
                with Path(args.request_json).expanduser().open("rb") as stream:
                    raw = stream.read(256 * 1024 + 1)
                if len(raw) > 256 * 1024:
                    raise ValueError("amendment request exceeds the complete 256 KiB limit")
                parameters = json.loads(raw)
                if not isinstance(parameters, dict):
                    raise ValueError("amendment request must be an object")
            if args.operation_id:
                if "operation_id" in parameters:
                    raise ValueError("supply operation_id once")
                parameters["operation_id"] = args.operation_id
            payload = execute_goal_amendment(registry_path=registry_path, goal_id=goal_id,
                goal_instance_id=args.goal_instance_id, operation=args.governed,
                actor_agent_id=args.agent_id, parameters=parameters,
                runtime_root=runtime_root, execute=args.execute)
            accepted = payload.get("ok") is True
            print_payload(payload, output_format(args), render_goal_amendment_proposal_markdown)
            return 0 if accepted else 1
        if any(getattr(args, name, None) for name in ("goal_instance_id", "agent_id", "request_json", "operation_id", "execute")):
            raise ValueError("governed-operation flags require --governed")
        if args.list:
            payload = _handle_list(
                goal_id=goal_id,
                registry_path=registry_path,
                runtime_root=runtime_root,
            )
        else:
            payload = _handle_submit(
                args,
                goal_id=goal_id,
                registry_path=registry_path,
                runtime_root=runtime_root,
            )
        payload = {"ok": True, **payload}
        exit_code = 0
    except Exception as exc:
        payload = {
            "ok": False,
            "goal_id": goal_id,
            "error": str(exc),
        }
        exit_code = 1

    print_payload(payload, output_format(args), render_goal_amendment_proposal_markdown)
    return exit_code


def _resolve_project(
    args: argparse.Namespace,
    *,
    goal_id: str | None,
    registry_path: Path,
) -> Path:
    if getattr(args, "project", None):
        return Path(args.project).expanduser()
    if goal_id:
        resolved_project, _ = resolve_todo_state_path(
            registry_path=registry_path,
            goal_id=goal_id,
            require_existing=False,
        )
        if resolved_project is None:
            raise ValueError(
                "goal project is not configured in the registry; pass --project"
            )
        return resolved_project
    return Path.cwd()


def _runtime_root_from_registry(
    registry_payload: dict[str, object],
) -> Path | None:
    text = str(registry_payload.get("common_runtime_root") or "").strip()
    return Path(text).expanduser() if text else None


def _resolve_goal_routing(
    *,
    goal_id: str,
    registry_path: Path,
    runtime_root: Path | None,
) -> tuple[Path, dict[str, object], Path]:
    """Resolve the registry/runtime pair both submit and readback route through.

    One documented selector precedence, shared by ``--proposal-json`` submit
    and ``--list`` readback so the journal a submission lands in is always
    the journal the same selectors read back:

    1. Registry: the CLI-resolved registry path (``--registry``,
       ``LOOPX_REGISTRY``, or the resolved default) is authoritative and is
       never silently replaced by a project-local ``<project>/.loopx/
       registry.json``. Two registries can register the same Goal over
       different runtime roots, so letting ``--project`` swap the registry
       on one path only makes a successful submission unreadable — the
       readback reports another runtime's journal (usually an empty list)
       even though nothing failed. ``--project`` selects the Goal state
       directory for submit, not the registry.
    2. Runtime root: an explicit ``--runtime-root`` wins; otherwise the
       selected registry's ``common_runtime_root``.

    Raises the same unreadable/invalid-registry and missing-runtime-root
    errors for both modes, before any journal read or write.
    """

    try:
        registry_payload = load_registry(registry_path)
    except (OSError, ValueError):
        raise ValueError(f"goal registry is unreadable: {registry_path}") from None
    _registered_goal(registry_payload, goal_id=goal_id)

    effective_runtime_root = runtime_root
    if effective_runtime_root is None:
        effective_runtime_root = _runtime_root_from_registry(registry_payload)
    if effective_runtime_root is None:
        raise ValueError(
            "proposal journal routing requires a runtime root "
            "(--runtime-root or the registry common_runtime_root)"
        )
    return registry_path, registry_payload, effective_runtime_root


def _handle_list(
    *,
    goal_id: str | None,
    registry_path: Path,
    runtime_root: Path | None,
) -> dict[str, object]:
    if not goal_id:
        raise ValueError("--list requires --goal-id")
    safe_goal_id = validate_goal_id_path_segment(goal_id)

    # Readback routes through the same selector precedence as submit: the
    # explicit registry is authoritative and --project never replaces it.
    _, _, effective_runtime_root = _resolve_goal_routing(
        goal_id=safe_goal_id,
        registry_path=registry_path,
        runtime_root=runtime_root,
    )
    rows = read_goal_amendment_proposal_journal(
        runtime_root=effective_runtime_root,
        goal_id=safe_goal_id,
    )
    return {"goal_id": safe_goal_id, "count": len(rows), "rows": rows}


def _handle_submit(
    args: argparse.Namespace,
    *,
    goal_id: str | None,
    registry_path: Path,
    runtime_root: Path | None,
) -> dict[str, object]:
    if not getattr(args, "proposal_json", None):
        raise ValueError(
            "submit mode requires --proposal-json (or use --list for readback)"
        )
    proposal = _read_json_object(args.proposal_json, "--proposal-json")
    proposal_goal_id_raw = str(proposal.get("goal_id") or "").strip()
    if not proposal_goal_id_raw:
        raise ValueError("proposal.goal_id must be a non-empty registered goal id")
    proposal_goal_id = validate_goal_id_path_segment(proposal_goal_id_raw)

    if goal_id:
        safe_goal_id = validate_goal_id_path_segment(goal_id)
        if safe_goal_id != proposal_goal_id:
            raise ValueError(
                f"--goal-id ({goal_id}) must match proposal.goal_id "
                f"({proposal_goal_id}) when both are given"
            )
    project = _resolve_project(
        args,
        goal_id=proposal_goal_id,
        registry_path=registry_path,
    )
    # Submit routes through the same selector precedence as readback (see
    # _resolve_goal_routing): the explicit registry wins for both, so the
    # journal this append lands in is exactly the one --list reads back.
    effective_registry_path, _, _ = _resolve_goal_routing(
        goal_id=proposal_goal_id,
        registry_path=registry_path,
        runtime_root=runtime_root,
    )
    record = admit_goal_amendment_proposal(
        proposal=proposal,
        project=project,
        registry_path=effective_registry_path,
        runtime_root=runtime_root,
    )
    return dict(record)
