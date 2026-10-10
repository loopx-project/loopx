from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Callable

from ..capabilities.explore.result_log import (
    DEFAULT_FINDING_LIMIT,
    DEFAULT_MERMAID_NODE_LIMIT,
    EDGE_TYPES,
    FINDING_STATUSES,
    NODE_KINDS,
    NODE_STATUSES,
    append_explore_result_event,
    build_explore_edge_event,
    build_explore_finding_event,
    build_explore_graph_view,
    build_explore_node_event,
    build_explore_result_projection,
    explore_result_log_path,
    load_explore_result_events_strict,
)
from ..capabilities.explore.research_evidence import append_research_observation
from ..capabilities.explore.harness_gate import GATE_STATE_DISABLED
from ..capabilities.explore.resource_portfolio import parse_resource_counts
from ..capabilities.explore.source_history_reconcile import (
    reconcile_explore_source_history,
)
from ..capabilities.explore.todo_branch_plan import (
    build_explore_todo_branch_plan,
    resolve_todo_branch_plan_gate,
)
from ..capabilities.explore.worker_branch_plan import (
    build_explore_worker_branch_plan,
    resolve_explore_harness_gate,
)
from ..control_plane.runtime.runtime_projection_route import (
    compact_goal_source_runtime_route,
    resolve_goal_source_runtime_route,
)
from ..extensions.lark import LARK_EXTENSION_ID, LARK_PROJECTION_SINK_PERMISSION
from ..extensions.lark.presentation.explore_results import (
    lark_explore_schema_payload,
    read_lark_explore_local_config,
)
from ..extensions.lark.presentation.explore_singleflight import (
    default_lark_explore_config_path,
)
from ..presentation.explore_views import build_explore_presentation_bundle
from ..extensions.runtime import (
    default_extension_state_file,
    resolve_extension_activation,
)
from ..history import load_registry
from ..paths import resolve_runtime_root
from ..todos import list_goal_todos
from .explore_feishu_commands import (
    FEISHU_SINK_EXPLORE_COMMANDS,
    handle_explore_feishu_command,
    register_explore_feishu_commands,
)
from .explore_planning_commands import register_explore_planning_commands


PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]
OutputFormat = Callable[[argparse.Namespace], str]


def register_explore_commands(
    subparsers: argparse._SubParsersAction,
    add_subcommand_format: Callable[[argparse.ArgumentParser], None],
) -> None:
    parser = subparsers.add_parser(
        "explore",
        help="Record the exploration topology and project it into a Feishu/Lark result board.",
    )
    sub = parser.add_subparsers(dest="explore_command", required=True)

    context = sub.add_parser("turn-context", help="Read bounded enabled Explore evidence and planning guidance for an agent turn.")
    add_subcommand_format(context)
    context.add_argument("--goal-id", required=True)
    context.add_argument("--agent-id", required=True)
    context.add_argument("--result-limit", type=int, default=3, help="Evidence details per page (1..20); default 3.")
    context.add_argument("--result-offset", type=int, default=0, help="Continue using the previous page's result_revision.")
    context.add_argument("--result-node", help="Read evidence for one question node.")
    context.add_argument("--result-revision", help="Evidence revision returned by the previous page; rejects changed evidence.")

    schema = sub.add_parser("schema", help="Print the result-board schema and LoopX mapping.")
    add_subcommand_format(schema)

    node = sub.add_parser(
        "node",
        help="Add or update one exploration node (question, area, hypothesis, experiment, artifact).",
    )
    add_subcommand_format(node)
    node.add_argument("--goal-id", required=True)
    node.add_argument("--title", required=True, help="Compact public-safe node statement.")
    node.add_argument("--node-id", help="Stable node id; reuse it to update the same node.")
    node.add_argument("--kind", dest="node_kind", choices=sorted(NODE_KINDS))
    node.add_argument("--status", choices=sorted(NODE_STATUSES))
    node.add_argument("--summary", help="Optional compact public-safe detail.")
    node.add_argument(
        "--blocked-reason",
        help="Required when --status blocked: why the loop is stuck.",
    )
    node.add_argument("--parent", dest="parent_id", help="Parent node id for the topology tree.")
    _add_common_record_args(node)

    edge = sub.add_parser("edge", help="Link two exploration nodes with a typed edge.")
    add_subcommand_format(edge)
    edge.add_argument("--goal-id", required=True)
    edge.add_argument("--from", dest="from_node", required=True, help="Source node id.")
    edge.add_argument("--to", dest="to_node", required=True, help="Target node id.")
    edge.add_argument("--type", dest="edge_type", required=True, choices=sorted(EDGE_TYPES))
    edge.add_argument("--summary", help="Optional compact edge label detail.")
    edge.add_argument("--confidence", type=float, help="0..1 confidence.")
    edge.add_argument("--agent-id")
    edge.add_argument("--run-id")

    finding = sub.add_parser(
        "finding",
        help="Record one finding, optionally attached to a node; reuse --finding-id to update it.",
    )
    add_subcommand_format(finding)
    finding.add_argument("--goal-id", required=True)
    finding.add_argument("--title", required=True, help="Compact public-safe finding statement.")
    finding.add_argument("--finding-id", help="Stable finding id; reuse it to update the same finding.")
    finding.add_argument("--node", dest="node_id", help="Node id this finding belongs to.")
    finding.add_argument("--status", choices=sorted(FINDING_STATUSES))
    finding.add_argument("--summary", help="Optional compact public-safe detail.")
    finding.add_argument("--confidence", type=float, help="0..1 confidence.")
    _add_common_record_args(finding)

    summary = sub.add_parser(
        "summary",
        help="Build the bounded result projection that display sinks render.",
    )
    add_subcommand_format(summary)
    summary.add_argument("--goal-id", required=True)
    _add_projection_limit_args(summary)

    observe = sub.add_parser("observe", help="Record typed research evidence on an existing Explore node; grants no execution or closure authority.")
    add_subcommand_format(observe)
    observe.add_argument("--goal-id", required=True)
    observe.add_argument("--observation-json", required=True, help="File containing a typed_research_observation_v0 envelope.")
    observe.add_argument("--agent-id")

    presentation = sub.add_parser(
        "presentation",
        help="Assess readability and derive same-source canonical/executive views.",
    )
    add_subcommand_format(presentation)
    presentation.add_argument("--goal-id", required=True)
    _add_projection_limit_args(presentation)

    source_reconcile = sub.add_parser(
        "source-history-reconcile",
        help=(
            "Recover registered public-safe results from another runtime log. "
            "Preview only unless --execute; never deletes remote rows."
        ),
    )
    add_subcommand_format(source_reconcile)
    source_reconcile.add_argument("--goal-id", required=True)
    source_reconcile.add_argument(
        "--candidate-runtime-root",
        required=True,
        help="Runtime root containing the candidate goal result log.",
    )
    _add_config_path_arg(source_reconcile)
    source_reconcile.add_argument("--execute", action="store_true")

    graph = sub.add_parser(
        "graph",
        help="Export the exploration topology as Mermaid flowchart source or graph JSON.",
    )
    add_subcommand_format(graph)
    graph.add_argument("--goal-id", required=True)
    _add_projection_limit_args(graph)
    graph.add_argument(
        "--graph-format",
        choices=["mermaid", "json"],
        default="mermaid",
    )
    graph.add_argument(
        "--status",
        dest="graph_statuses",
        action="append",
        choices=sorted(NODE_STATUSES),
        default=[],
        help="Keep nodes with this status. Repeatable; combined with --tag using AND.",
    )
    graph.add_argument(
        "--tag",
        dest="graph_tags",
        action="append",
        default=[],
        help="Keep nodes with any requested exact tag. Repeatable.",
    )
    graph.add_argument(
        "--include-ancestors",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Keep ancestors of matching nodes so the focused topology retains context.",
    )
    graph.add_argument("--out", help="Also write the graph to this local file.")

    register_explore_planning_commands(
        sub,
        add_subcommand_format,
        _add_projection_limit_args,
    )

    register_explore_feishu_commands(
        sub,
        add_subcommand_format,
        _add_config_path_arg,
        _add_projection_limit_args,
    )


def _add_common_record_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--agent-id")
    parser.add_argument("--run-id")
    parser.add_argument(
        "--evidence-ref",
        action="append",
        default=[],
        help="Public relative ref or opaque id, never a local absolute path. Repeatable.",
    )
    parser.add_argument("--tag", action="append", default=[])
    parser.add_argument("--supersedes", help="Result id this event supersedes.")


def _add_config_path_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config-path",
        help="Local board config path. Defaults beside the LoopX registry.",
    )


def _add_projection_limit_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--finding-limit", type=int, default=DEFAULT_FINDING_LIMIT)
    parser.add_argument("--mermaid-node-limit", type=int, default=DEFAULT_MERMAID_NODE_LIMIT)


def _projection_for(
    args: argparse.Namespace,
    *,
    runtime_root: Path,
    finding_limit_override: int | None = None,
) -> dict[str, object]:
    log_path = explore_result_log_path(runtime_root, args.goal_id)
    events = load_explore_result_events_strict(log_path, goal_id=args.goal_id)
    finding_limit = (
        int(args.finding_limit)
        if finding_limit_override is None
        else int(finding_limit_override)
    )
    if finding_limit < 0:
        finding_limit = len(events)
    projection = build_explore_result_projection(
        events,
        goal_id=args.goal_id,
        finding_limit=max(0, finding_limit),
        mermaid_node_limit=max(1, int(args.mermaid_node_limit)),
    )
    projection["log_path"] = str(log_path)
    return projection


def _append_event_payload(event: dict[str, object], *, runtime_root: Path, goal_id: str) -> dict[str, object]:
    payload = append_explore_result_event(explore_result_log_path(runtime_root, goal_id), event)
    payload["goal_id"] = goal_id
    payload["event"] = event
    return payload


def _goal_orchestration_boundary(registry: dict[str, object], *, goal_id: str) -> dict[str, object] | None:
    """Per-goal orchestration policy from the registered goal's spawn_policy.

    spawn_policy is the single writable source that the live quota/status
    pipeline projects into goal_boundary.orchestration (enrich_project_asset
    derives project_asset.orchestration from it too), so the planner gate must
    read exactly it -- honoring any other registry key would create a second
    authorization surface invisible to `quota should-run`. An unregistered
    goal has no boundary, which the planners treat as opt-out.
    """

    goals = registry.get("goals")
    if not isinstance(goals, list):
        return None
    for goal in goals:
        if not isinstance(goal, dict) or str(goal.get("id") or "") != goal_id:
            continue
        spawn_policy = goal.get("spawn_policy")
        return spawn_policy if isinstance(spawn_policy, dict) else None
    return None


def _tree_lines(tree: object, *, indent: int = 0) -> list[str]:
    lines: list[str] = []
    if not isinstance(tree, list):
        return lines
    for branch in tree:
        if not isinstance(branch, dict):
            continue
        lines.append(f"{'  ' * indent}- [{branch.get('status')}] {branch.get('title')}")
        lines.extend(_tree_lines(branch.get("children"), indent=indent + 1))
    return lines


def render_explore_markdown(payload: dict[str, object]) -> str:
    if "graph_enabled" in payload and "harness_enabled" in payload:
        return "# Explore Harness turn context\n\n```json\n" + json.dumps(payload, ensure_ascii=False, indent=2) + "\n```\n"
    lines = ["# LoopX Explore", ""]
    if not payload.get("ok"):
        lines.extend([f"- ok: `{payload.get('ok')}`", f"- error: `{payload.get('error')}`", ""])
        return "\n".join(lines)
    for key in (
        "schema_version",
        "goal_id",
        "execute",
        "event_kind",
        "result_id",
        "event_id",
        "sink_visibility",
        "message_id",
        "card_file",
        "out",
        "presentation_mode",
        "source_revision",
    ):
        if payload.get(key) not in (None, ""):
            lines.append(f"- {key}: `{payload.get(key)}`")
    counts = payload.get("counts")
    research = payload.get("research_frontier")
    if isinstance(research, dict):
        lines.append(f"- research composition (read-only): {research['pending_count']} pending, "
                     f"{research['observed_count']} observed, {research['ineligible_count']} ineligible")
        for gap in research["gaps"]:
            lines.append(f"  - {gap['gap_id']}: {gap['state']} ({gap['reason']})")
        if research["omitted_count"]:
            lines.append(f"  - {research['omitted_count']} additional candidates omitted from this bounded view")
    observation = payload.get("observation")
    if isinstance(observation, dict):
        lines.append(f"- research observation: `{observation['explore_node_id']}`; "
                     f"replayed={payload.get('replayed')}; written={payload.get('written')}")
    if isinstance(counts, dict):
        lines.append(
            f"- map: `{counts.get('node_count')} nodes, {counts.get('edge_count')} edges, "
            f"{counts.get('finding_count')} findings`"
        )
    row_counts = payload.get("row_counts")
    if isinstance(row_counts, dict):
        joined = ", ".join(f"{key}={value}" for key, value in row_counts.items())
        lines.append(f"- rows: `{joined}`")
    reason_codes = payload.get("reason_codes")
    if isinstance(reason_codes, list) and reason_codes:
        lines.append(f"- reason_codes: `{', '.join(str(item) for item in reason_codes)}`")
    if payload.get("strategy"):
        lines.append(f"- strategy: `{payload.get('strategy')}`")
    if payload.get("harness_profile"):
        lines.append(f"- harness_profile: `{payload.get('harness_profile')}`")
    gate = payload.get("orchestration_gate")
    if isinstance(gate, dict) and gate:
        lines.append(
            "- orchestration_gate: "
            f"`state={gate.get('state')} reason={gate.get('reason')} "
            f"spawn_allowed={gate.get('spawn_allowed')} max_children={gate.get('max_children')} "
            f"width={gate.get('effective_width')}/{gate.get('requested_width')}`"
        )
    if payload.get("issue_width") not in (None, ""):
        lines.append(f"- issue_width: `{payload.get('issue_width')}`")
    if payload.get("worker_width") not in (None, ""):
        lines.append(f"- worker_width_ceiling: `{payload.get('worker_width')}`")
    if payload.get("branch_fill_policy") not in (None, ""):
        lines.append(f"- branch_fill_policy: `{payload.get('branch_fill_policy')}`")
    if payload.get("verification_budget") not in (None, ""):
        lines.append(f"- verification_budget: `{payload.get('verification_budget')}`")
    ab_result = payload.get("ab_result")
    if isinstance(ab_result, dict):
        if ab_result.get("baseline_serial_theta") is not None:
            lines.append(
                "- ab_estimate: "
                f"`baseline={ab_result.get('baseline_serial_theta')}, "
                f"dspark={ab_result.get('dspark_selected_theta')}, "
                f"speedup={ab_result.get('estimated_speedup_vs_baseline')}x`"
            )
        else:
            lines.append(
                "- ab_estimate: "
                f"`baseline_evidence={ab_result.get('baseline_expected_evidence_units')}, "
                f"dspark_evidence={ab_result.get('dspark_expected_evidence_units')}, "
                f"speedup={ab_result.get('estimated_speedup_vs_baseline')}x`"
            )
    selected_branches = payload.get("selected_branches")
    if isinstance(selected_branches, list) and selected_branches:
        lines.extend(["", "## Predicted Branches", ""])
        for branch in selected_branches:
            if not isinstance(branch, dict):
                continue
            lines.append(
                f"- {branch.get('branch_role')} `{branch.get('todo_id')}` "
                f"score=`{branch.get('score')}` confidence=`{branch.get('confidence')}`: "
                f"{branch.get('text')}"
            )
            commands = branch.get("suggested_commands")
            if isinstance(commands, list) and commands:
                for command in commands:
                    lines.append(f"  - `{command}`")
    selected_worker_branches = payload.get("selected_worker_branches")
    if isinstance(selected_worker_branches, list) and selected_worker_branches:
        lines.extend(["", "## Worker Branches", ""])
        for branch in selected_worker_branches:
            if not isinstance(branch, dict):
                continue
            todo_ids = ", ".join(str(todo_id) for todo_id in branch.get("todo_ids") or [])
            lines.append(
                f"- {branch.get('branch_role')} `{branch.get('branch_id')}` "
                f"worker=`{branch.get('worker_hint')}` confidence=`{branch.get('confidence')}` "
                f"evidence=`{branch.get('expected_evidence_units')}`"
            )
            if todo_ids:
                lines.append(f"  - todos: `{todo_ids}`")
            commands = branch.get("suggested_commands")
            if isinstance(commands, list) and commands:
                for command in commands[:4]:
                    lines.append(f"  - `{command}`")
    tree_lines = _tree_lines(payload.get("tree"))
    if tree_lines:
        lines.extend(["", "## Topology", "", *tree_lines])
    stuck = payload.get("stuck")
    if isinstance(stuck, list) and stuck:
        lines.extend(["", "## Blocked", ""])
        for item in stuck:
            if isinstance(item, dict):
                reason = str(item.get("blocked_reason") or "").strip()
                lines.append(f"- {item.get('title')}" + (f" - {reason}" if reason else ""))
    findings = payload.get("findings")
    if isinstance(findings, list) and findings:
        lines.extend(["", "## Findings", ""])
        for item in findings:
            if isinstance(item, dict):
                lines.append(f"- [{item.get('status')}] {item.get('finding')}")
    mermaid = payload.get("mermaid")
    if isinstance(mermaid, str) and mermaid and payload.get("graph_format") != "json":
        lines.extend(["", "## Mermaid", "", "```mermaid", mermaid, "```"])
    card_markdown = payload.get("card_markdown")
    if isinstance(card_markdown, str) and card_markdown:
        lines.extend(["", "## Card", "", card_markdown])
    warnings = payload.get("warnings")
    if isinstance(warnings, list) and warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in warnings)
    lines.append("")
    return "\n".join(lines)


def handle_explore_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    print_payload: PrintPayload,
    output_format: OutputFormat,
) -> int | None:
    if args.command != "explore":
        return None
    fmt = output_format(args)
    activation: dict[str, object] | None = None
    try:
        registry = load_registry(registry_path)
        runtime_root = resolve_runtime_root(registry, runtime_root_arg)
        if args.explore_command in (
            {"schema", "source-history-reconcile"} | FEISHU_SINK_EXPLORE_COMMANDS
        ):
            activation = resolve_extension_activation(
                LARK_EXTENSION_ID,
                state_file=default_extension_state_file(runtime_root),
                required_permissions=(LARK_PROJECTION_SINK_PERMISSION,),
            )
        source_runtime_route = None
        goal_id = str(getattr(args, "goal_id", "") or "")
        if goal_id:
            source_runtime_route = resolve_goal_source_runtime_route(
                registry_path=registry_path,
                goal_id=goal_id,
                runtime_root_override=runtime_root_arg,
                registry=registry,
            )
            runtime_root = Path(str(source_runtime_route["source_runtime_root"]))
        config_path = (
            Path(args.config_path).expanduser()
            if getattr(args, "config_path", None)
            else default_lark_explore_config_path(
                Path(str(source_runtime_route["source_registry"]))
                if source_runtime_route is not None
                else registry_path
            )
        )
        if args.explore_command == "schema":
            payload = lark_explore_schema_payload()
        elif args.explore_command == "turn-context":
            from ..capabilities.explore.turn_context import explore_turn_context
            payload = explore_turn_context(registry_path=Path(str(source_runtime_route["source_registry"])), runtime_root=runtime_root,
                                           goal_id=args.goal_id, agent_id=args.agent_id,
                                           result_limit=args.result_limit, result_offset=args.result_offset,
                                           result_node=args.result_node, result_revision=args.result_revision)
        elif args.explore_command == "node":
            event = build_explore_node_event(
                goal_id=args.goal_id,
                title=args.title,
                node_id=args.node_id,
                node_kind=args.node_kind,
                status=args.status,
                summary=args.summary,
                blocked_reason=args.blocked_reason,
                parent_id=args.parent_id,
                agent_id=args.agent_id,
                run_id=args.run_id,
                evidence_refs=args.evidence_ref,
                tags=args.tag,
                supersedes=args.supersedes,
            )
            payload = _append_event_payload(event, runtime_root=runtime_root, goal_id=args.goal_id)
        elif args.explore_command == "edge":
            event = build_explore_edge_event(
                goal_id=args.goal_id,
                from_node=args.from_node,
                to_node=args.to_node,
                edge_type=args.edge_type,
                summary=args.summary,
                confidence=args.confidence,
                agent_id=args.agent_id,
                run_id=args.run_id,
            )
            payload = _append_event_payload(event, runtime_root=runtime_root, goal_id=args.goal_id)
        elif args.explore_command == "finding":
            event = build_explore_finding_event(
                goal_id=args.goal_id,
                title=args.title,
                finding_id=args.finding_id,
                node_id=args.node_id,
                status=args.status,
                summary=args.summary,
                confidence=args.confidence,
                agent_id=args.agent_id,
                run_id=args.run_id,
                evidence_refs=args.evidence_ref,
                tags=args.tag,
                supersedes=args.supersedes,
            )
            payload = _append_event_payload(event, runtime_root=runtime_root, goal_id=args.goal_id)
        elif args.explore_command == "observe":
            observation = json.loads(Path(args.observation_json).read_text(encoding="utf-8"))
            if not isinstance(observation, dict):
                raise ValueError("research observation must be an object")
            payload = append_research_observation(
                explore_result_log_path(runtime_root, args.goal_id), goal_id=args.goal_id,
                observation=observation, agent_id=args.agent_id,
            )
        elif args.explore_command == "summary":
            payload = _projection_for(args, runtime_root=runtime_root)
        elif args.explore_command == "presentation":
            projection = _projection_for(
                args,
                runtime_root=runtime_root,
                finding_limit_override=-1,
            )
            payload = build_explore_presentation_bundle(projection)
        elif args.explore_command == "source-history-reconcile":
            local = read_lark_explore_local_config(config_path)
            result_records = (
                local.get("result_records")
                if isinstance(local.get("result_records"), dict)
                else {}
            )
            payload = reconcile_explore_source_history(
                canonical_log_path=explore_result_log_path(runtime_root, args.goal_id),
                candidate_log_path=explore_result_log_path(
                    Path(args.candidate_runtime_root), args.goal_id
                ),
                goal_id=args.goal_id,
                registered_result_keys=set(result_records),
                execute=bool(args.execute),
            )
        elif args.explore_command == "graph":
            projection = _projection_for(args, runtime_root=runtime_root)
            graph_view = build_explore_graph_view(
                projection.get("nodes") or [],
                projection.get("edges") or [],
                statuses=args.graph_statuses,
                tags=args.graph_tags,
                include_ancestors=bool(args.include_ancestors),
                node_limit=max(1, int(args.mermaid_node_limit)),
            )
            payload = {
                "ok": True,
                "schema_version": "loopx_explore_graph_v0",
                "goal_id": args.goal_id,
                "graph_format": args.graph_format,
                "counts": projection.get("counts"),
                "graph_counts": graph_view.get("graph_counts"),
                "filter": graph_view.get("filter"),
                "nodes": graph_view.get("nodes"),
                "edges": graph_view.get("edges"),
                "mermaid": graph_view.get("mermaid"),
            }
            if args.out:
                out_path = Path(args.out).expanduser()
                out_path.parent.mkdir(parents=True, exist_ok=True)
                if args.graph_format == "mermaid":
                    out_path.write_text(str(graph_view.get("mermaid") or "") + "\n", encoding="utf-8")
                else:
                    graph_json = {
                        "goal_id": args.goal_id,
                        "graph_counts": graph_view.get("graph_counts"),
                        "filter": graph_view.get("filter"),
                        "nodes": graph_view.get("nodes"),
                        "edges": graph_view.get("edges"),
                    }
                    out_path.write_text(
                        json.dumps(graph_json, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                payload["out"] = str(out_path)
        elif args.explore_command == "todo-branch-plan":
            resource_capacities = parse_resource_counts(
                args.resource_capacity,
                flag_name="--resource-capacity",
            )
            resource_usage = parse_resource_counts(
                args.resource_usage,
                flag_name="--resource-usage",
            )
            orchestration = _goal_orchestration_boundary(registry, goal_id=args.goal_id)
            gate = resolve_todo_branch_plan_gate(orchestration, requested_width=args.width)
            if gate["state"] == GATE_STATE_DISABLED:
                # Short-circuit before goal-state projection so disabled and
                # unregistered goals both get the explicit opt-in packet
                # instead of a state-file resolution error.
                payload = build_explore_todo_branch_plan(
                    goal_id=args.goal_id,
                    todos=[],
                    agent_id=args.agent_id,
                    orchestration=orchestration,
                    width=args.width,
                    resource_capacities=resource_capacities,
                    resource_usage=resource_usage,
                )
            else:
                projection = _projection_for(args, runtime_root=runtime_root)
                todo_payload = list_goal_todos(
                    registry_path=registry_path,
                    goal_id=args.goal_id,
                    role="agent",
                    status="open",
                    agent_id=args.agent_id,
                )
                payload = build_explore_todo_branch_plan(
                    goal_id=args.goal_id,
                    todos=[item for item in todo_payload.get("todos") or [] if isinstance(item, dict)],
                    projection=projection,
                    agent_id=args.agent_id,
                    orchestration=orchestration,
                    width=args.width,
                    allow_unscoped_parallel=bool(args.allow_unscoped_parallel),
                    scheduler_strategy=args.scheduler_strategy,
                    scheduler_load=args.scheduler_load,
                    resource_capacities=resource_capacities,
                    resource_usage=resource_usage,
                )
                payload["todo_source"] = {
                    "source": todo_payload.get("source"),
                    "state_file": todo_payload.get("state_file"),
                    "todo_count": todo_payload.get("todo_count"),
                }
        elif args.explore_command == "worker-branch-plan":
            resource_capacities = parse_resource_counts(
                args.resource_capacity,
                flag_name="--resource-capacity",
            )
            resource_usage = parse_resource_counts(
                args.resource_usage,
                flag_name="--resource-usage",
            )
            orchestration = _goal_orchestration_boundary(registry, goal_id=args.goal_id)
            gate = resolve_explore_harness_gate(orchestration, requested_width=args.worker_width)
            if gate["state"] == GATE_STATE_DISABLED:
                # Short-circuit before goal-state projection so disabled and
                # unregistered goals both get the explicit opt-in packet
                # instead of a state-file resolution error.
                payload = build_explore_worker_branch_plan(
                    goal_id=args.goal_id,
                    todos=[],
                    agent_id=args.agent_id,
                    orchestration=orchestration,
                    worker_width=args.worker_width,
                    resource_capacities=resource_capacities,
                    resource_usage=resource_usage,
                )
            else:
                projection = _projection_for(args, runtime_root=runtime_root)
                todo_payload = list_goal_todos(
                    registry_path=registry_path,
                    goal_id=args.goal_id,
                    role="agent",
                    status="open",
                    agent_id=args.agent_id,
                )
                router_state = None
                if getattr(args, "router_state", None):
                    router_state = json.loads(Path(args.router_state).read_text(encoding="utf-8-sig"))
                load_profile = None
                if getattr(args, "load_profile", None):
                    load_profile = json.loads(Path(args.load_profile).read_text(encoding="utf-8-sig"))
                payload = build_explore_worker_branch_plan(
                    goal_id=args.goal_id,
                    todos=[item for item in todo_payload.get("todos") or [] if isinstance(item, dict)],
                    projection=projection,
                    agent_id=args.agent_id,
                    orchestration=orchestration,
                    worker_width=args.worker_width,
                    max_todos_per_branch=args.max_todos_per_branch,
                    scheduler_load=args.scheduler_load,
                    allow_unscoped_parallel=bool(args.allow_unscoped_parallel),
                    harness_profile=args.harness_profile,
                    branch_fill_policy=args.branch_fill_policy,
                    marginal_score_floor=args.marginal_score_floor,
                    router_state=router_state,
                    load_profile=load_profile,
                    resource_capacities=resource_capacities,
                    resource_usage=resource_usage,
                )
                payload["todo_source"] = {
                    "source": todo_payload.get("source"),
                    "state_file": todo_payload.get("state_file"),
                    "todo_count": todo_payload.get("todo_count"),
                }
        elif args.explore_command in FEISHU_SINK_EXPLORE_COMMANDS:
            payload = handle_explore_feishu_command(
                args,
                config_path=config_path,
                runtime_root=runtime_root,
                projection_for=_projection_for,
            )
        else:
            raise ValueError(f"unknown explore command: {args.explore_command}")
        if source_runtime_route is not None:
            payload["source_runtime_route"] = compact_goal_source_runtime_route(
                source_runtime_route
            )
        if activation is not None and payload.get("ok"):
            payload["extension_activation"] = activation
    except Exception as exc:
        payload = {
            "ok": False,
            "schema_version": "loopx_explore_error_v0",
            "error": str(exc),
        }
    print_payload(payload, fmt, render_explore_markdown)
    return 0 if payload.get("ok") else 1
