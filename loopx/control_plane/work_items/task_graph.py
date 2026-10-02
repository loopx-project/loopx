from __future__ import annotations

import hashlib
import re
from typing import Any, Callable

from ..runtime.time import now_utc_iso
from ..effect_runtime import effect_runtime_result
from ..todos.contract import TODO_STATUS_DEFERRED
from ..todos.summary_item import todo_planning_source_items

TASK_GRAPH_PROJECTION_SCHEMA_VERSION = "task_graph_projection_v0"
TASK_GRAPH_SOURCE_OF_TRUTH = [
    "event_ledger",
    "active_goal_state",
    "todos",
    "gates",
    "leases",
    "run_history",
]
TASK_GRAPH_MAX_USER_GATE_NODES = 2
TASK_GRAPH_MAX_PREDECESSOR_NODES = 4
TASK_GRAPH_AUDIT_MARKERS = ("audit", "audited")
TASK_GRAPH_CONTINUATION_MARKERS = ("continuation", "continue", "continuing", "continued")


def _task_graph_node_id(
    prefix: str,
    value: Any,
    *,
    public_safe_compact_text: Callable[..., str | None],
    durable_id: str | None = None,
) -> str:
    raw = public_safe_compact_text(value, limit=120) or prefix
    normalized = re.sub(r"[^A-Za-z0-9_]+", "_", raw).strip("_").lower()
    if not normalized:
        normalized = hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:10]
    if durable_id:
        id_hash = hashlib.sha256(durable_id.encode("utf-8")).hexdigest()[:6]
    else:
        id_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:6]
    if len(normalized) > 50:
        normalized = f"{normalized[:44].rstrip('_')}_{id_hash}"
    return f"{prefix}_{normalized}_{id_hash}"


def _task_graph_ref_values(
    *values: Any,
    limit: int = 4,
    public_safe_compact_text: Callable[..., str | None],
) -> list[str]:
    refs: list[str] = []
    for value in values:
        if isinstance(value, list):
            for nested in _task_graph_ref_values(
                *value,
                limit=limit,
                public_safe_compact_text=public_safe_compact_text,
            ):
                if nested not in refs:
                    refs.append(nested)
                    if len(refs) >= limit:
                        return refs
            continue
        text = public_safe_compact_text(value, limit=120)
        if not text or text in refs:
            continue
        refs.append(text)
        if len(refs) >= limit:
            break
    return refs


def _task_graph_refs(
    key: str,
    *values: Any,
    public_safe_compact_text: Callable[..., str | None],
) -> dict[str, list[str]] | None:
    refs = _task_graph_ref_values(*values, public_safe_compact_text=public_safe_compact_text)
    if not refs:
        return None
    return {key: refs}


def _task_graph_todo_state(
    todo: dict[str, Any],
    *,
    normalize_todo_status: Callable[[Any], str | None],
    todo_done_for_status: Callable[[str], bool],
    todo_status_open: str,
    waiting_default: bool = False,
) -> str:
    status = normalize_todo_status(todo.get("status")) or todo_status_open
    # Deferred is terminal for scheduling, but the work has not been delivered.
    if status == TODO_STATUS_DEFERRED:
        return "waiting"
    if todo.get("done") or todo_done_for_status(status):
        return "done"
    if status == "blocked":
        return "blocked"
    if status in {"waiting", "deferred"} or waiting_default:
        return "waiting"
    return "open"


def _task_graph_generated_at(
    *,
    goal: dict[str, Any],
    goal_latest_runs: list[dict[str, Any]],
    public_safe_compact_text: Callable[..., str | None],
    latest_run: Callable[[dict[str, Any]], dict[str, Any] | None],
) -> str:
    candidates: list[dict[str, Any]] = [*goal_latest_runs]
    current_run = latest_run(goal)
    if isinstance(current_run, dict):
        candidates.append(current_run)
    for run in candidates:
        generated_at = public_safe_compact_text(run.get("generated_at"), limit=80)
        if generated_at:
            return generated_at
    return now_utc_iso()


def _task_graph_active_state_updated_at(
    item: dict[str, Any],
    goal: dict[str, Any],
    *,
    public_safe_compact_text: Callable[..., str | None],
    latest_run: Callable[[dict[str, Any]], dict[str, Any] | None],
) -> str | None:
    warning = (
        item.get("stale_latest_run_warning")
        if isinstance(item.get("stale_latest_run_warning"), dict)
        else {}
    )
    updated_at = public_safe_compact_text(warning.get("active_state_updated_at"), limit=80)
    if updated_at:
        return updated_at
    current_run = latest_run(goal)
    state = (
        current_run.get("state")
        if isinstance(current_run, dict) and isinstance(current_run.get("state"), dict)
        else {}
    )
    frontmatter = state.get("frontmatter") if isinstance(state.get("frontmatter"), dict) else {}
    return public_safe_compact_text(frontmatter.get("updated_at"), limit=80)


def _task_graph_latest_run_node(
    *,
    goal_latest_runs: list[dict[str, Any]],
    selected_todo_id: str | None,
    public_safe_compact_text: Callable[..., str | None],
) -> dict[str, Any] | None:
    for run in goal_latest_runs:
        if not isinstance(run, dict):
            continue
        run_ref = public_safe_compact_text(
            run.get("run_id") or run.get("generated_at") or run.get("classification"),
            limit=120,
        )
        classification = public_safe_compact_text(run.get("classification"), limit=120)
        if not (run_ref or classification):
            continue
        refs = _task_graph_refs(
            "run_ids",
            run_ref or classification,
            public_safe_compact_text=public_safe_compact_text,
        )
        if refs and selected_todo_id:
            todo_refs = _task_graph_ref_values(
                selected_todo_id,
                public_safe_compact_text=public_safe_compact_text,
            )
            if todo_refs:
                refs["todo_ids"] = todo_refs
        if not refs:
            continue
        title = classification or "Latest compact run-history evidence"
        return {
            "node_id": _task_graph_node_id(
                "node_run",
                run_ref or title,
                public_safe_compact_text=public_safe_compact_text,
            ),
            "kind": "validation",
            "title": f"Latest compact run: {title}",
            "state": "ready",
            "refs": refs,
        }
    return None


def _task_graph_latest_run_lineage_relations(
    run_node_id: str | None,
    selected_node_id: str | None,
    *,
    goal_latest_runs: list[dict[str, Any]],
    public_safe_compact_text: Callable[..., str | None],
) -> list[tuple[str, str]]:
    if not run_node_id or not selected_node_id:
        return []
    for run in goal_latest_runs:
        if not isinstance(run, dict):
            continue
        values = [
            run.get("classification"),
            run.get("recommended_action"),
            run.get("delivery_outcome"),
            run.get("delivery_batch_scale"),
        ]
        text = " ".join(
            public_safe_compact_text(value, limit=160) or ""
            for value in values
        ).lower()
        if not text.strip():
            continue
        relations: list[tuple[str, str]] = []
        if any(marker in text for marker in TASK_GRAPH_AUDIT_MARKERS):
            relations.append(
                (
                    "audits",
                    "Compact run-history evidence audits the selected work lane without replacing todo or gate state.",
                )
            )
        if any(marker in text for marker in TASK_GRAPH_CONTINUATION_MARKERS):
            relations.append(
                (
                    "continues",
                    "Compact run-history evidence records a continuation of the selected work lane.",
                )
            )
        return relations
    return []


def _task_graph_visible_user_gate_items(
    user_todos: dict[str, Any] | None,
    *,
    limit: int,
    open_todo_items: Callable[..., list[dict[str, Any]]],
    max_status_todos_per_role: int,
    todo_item_task_class: Callable[[dict[str, Any]], str],
    user_gate_task_class: str,
    todo_summary_open_count: Callable[[dict[str, Any] | None], int],
) -> tuple[list[dict[str, Any]], int]:
    visible_items = open_todo_items(
        user_todos,
        limit=max_status_todos_per_role,
        text_limit=180,
        source_keys=("gate_open_items", "first_open_items", "items"),
    )
    visible_gate_items = [
        item for item in visible_items if todo_item_task_class(item) == user_gate_task_class
    ]
    if visible_gate_items and len(visible_gate_items) == len(visible_items):
        gate_open_count = max(len(visible_gate_items), todo_summary_open_count(user_todos))
    else:
        gate_open_count = len(visible_gate_items)
    return visible_gate_items[:limit], gate_open_count


def _task_graph_collect_todo_items(
    todos_summary: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    if not isinstance(todos_summary, dict):
        return {}
    by_id: dict[str, dict[str, Any]] = {}
    for raw in todo_planning_source_items(todos_summary, include_terminal=True):
        if not isinstance(raw, dict):
            continue
        tid = raw.get("todo_id")
        if isinstance(tid, str) and tid:
            by_id[tid] = raw
    return by_id


def _task_graph_source_items_truncated(
    todos_summary: dict[str, Any] | None,
) -> bool:
    if not isinstance(todos_summary, dict):
        return False
    items = todo_planning_source_items(todos_summary, include_terminal=True)
    try:
        total_count = int(todos_summary.get("total_count") or 0)
    except (TypeError, ValueError):
        return False
    return total_count > len(items)


def _task_graph_deliverable_node(
    *,
    todo: dict[str, Any],
    public_safe_compact_text: Callable[..., str | None],
    normalize_todo_status: Callable[[Any], str | None],
    todo_done_for_status: Callable[[str], bool],
    todo_status_open: str,
    waiting_default: bool = False,
) -> dict[str, Any] | None:
    todo_id = public_safe_compact_text(todo.get("todo_id"), limit=120)
    title = public_safe_compact_text(todo.get("title") or todo.get("text"), limit=160)
    if not todo_id or not title:
        return None
    node: dict[str, Any] = {
        "node_id": _task_graph_node_id(
            "node_todo",
            todo_id,
            public_safe_compact_text=public_safe_compact_text,
            durable_id=todo_id,
        ),
        "kind": "deliverable",
        "title": title,
        "state": _task_graph_todo_state(
            todo,
            normalize_todo_status=normalize_todo_status,
            todo_done_for_status=todo_done_for_status,
            todo_status_open=todo_status_open,
            waiting_default=waiting_default,
        ),
        "refs": _task_graph_refs(
            "todo_ids",
            todo_id,
            public_safe_compact_text=public_safe_compact_text,
        ),
    }
    owner = public_safe_compact_text(todo.get("claimed_by"), limit=80)
    if owner:
        node["owner_agent"] = owner
    actor = public_safe_compact_text(todo.get("last_actor_agent_id"), limit=80)
    if actor:
        node["actor_agent"] = actor
    return node


class _TaskGraphProjectionBuilder:
    def __init__(
        self,
        *,
        public_safe_compact_text: Callable[..., str | None],
    ) -> None:
        self._public_safe_compact_text = public_safe_compact_text
        self.nodes: list[dict[str, Any]] = []
        self.edges: list[dict[str, Any]] = []
        self.node_ids: set[str] = set()
        self.edge_ids: set[str] = set()
        self.refs_by_node_id: dict[str, dict[str, list[str]]] = {}

    def add_node(self, node: dict[str, Any] | None) -> str | None:
        if not isinstance(node, dict):
            return None
        node_id = str(node.get("node_id") or "")
        if not node_id:
            return None
        if node_id in self.node_ids:
            return node_id
        refs = node.get("refs") if isinstance(node.get("refs"), dict) else None
        if not refs:
            return None
        title = self._public_safe_compact_text(node.get("title"), limit=160)
        if not title:
            return None
        node["title"] = title
        self.node_ids.add(node_id)
        self.refs_by_node_id[node_id] = refs
        self.nodes.append(node)
        return node_id

    def add_edge(
        self,
        *,
        edge_id: str,
        from_node_id: str | None,
        to_node_id: str | None,
        relation: str,
        reason: str,
        refs: dict[str, list[str]] | None = None,
    ) -> None:
        if not from_node_id or not to_node_id or from_node_id == to_node_id:
            return
        if (
            from_node_id not in self.node_ids
            or to_node_id not in self.node_ids
            or edge_id in self.edge_ids
        ):
            return
        compact_reason = self._public_safe_compact_text(reason, limit=180)
        if not compact_reason:
            return
        edge: dict[str, Any] = {
            "edge_id": edge_id,
            "from_node_id": from_node_id,
            "to_node_id": to_node_id,
            "relation": relation,
            "reason": compact_reason,
        }
        if refs:
            edge["refs"] = refs
        self.edge_ids.add(edge_id)
        self.edges.append(edge)




def _task_graph_attach_handoff(
    *,
    current_todo: dict[str, Any],
    current_tid: str,
    current_nid: str,
    successor_nid: str | None,
    builder: _TaskGraphProjectionBuilder,
    public_safe_compact_text: Callable[..., str | None],
) -> None:
    handoff_note = current_todo.get("handoff_note") if isinstance(current_todo.get("handoff_note"), dict) else None
    if not handoff_note:
        return
    handoff_from = public_safe_compact_text(handoff_note.get("from_agent"), limit=80)
    handoff_to = public_safe_compact_text(handoff_note.get("to_agent"), limit=80)
    if not handoff_from or not handoff_to or handoff_from == handoff_to:
        return
    handoff_status = public_safe_compact_text(handoff_note.get("status"), limit=80)
    if handoff_status in ("done", "waiting", "unknown"):
        handoff_state = handoff_status
    elif current_todo.get("done") is True or str(current_todo.get("status") or "").strip().lower() == "done":
        handoff_state = "done"
    elif str(current_todo.get("status") or "").strip().lower() == "open":
        handoff_state = "waiting"
    else:
        handoff_state = "unknown"
    handoff_id = f"handoff:{current_tid}:{handoff_from}:{handoff_to}"
    handoff_node_id = builder.add_node(
        {
            "node_id": _task_graph_node_id(
                "node_handoff",
                handoff_id,
                public_safe_compact_text=public_safe_compact_text,
            ),
            "kind": "handoff",
            "title": f"Handoff from {handoff_from} to {handoff_to}",
            "state": handoff_state,
            "refs": _task_graph_refs(
                "todo_ids",
                current_tid,
                public_safe_compact_text=public_safe_compact_text,
            ),
            "from_agent": handoff_from,
            "to_agent": handoff_to,
        }
    )
    if not handoff_node_id:
        return
    builder.add_edge(
        edge_id=_task_graph_node_id(
            "edge_hands_off_to",
            f"{current_nid}:{handoff_node_id}",
            public_safe_compact_text=public_safe_compact_text,
        ),
        from_node_id=current_nid,
        to_node_id=handoff_node_id,
        relation="hands_off_to",
        reason=f"Agent {handoff_from} handed off to {handoff_to}.",
        refs=_task_graph_refs(
            "todo_ids",
            current_tid,
            public_safe_compact_text=public_safe_compact_text,
        ),
    )
    if successor_nid:
        builder.add_edge(
            edge_id=_task_graph_node_id(
                "edge_continues_handoff",
                f"{handoff_node_id}:{successor_nid}",
                public_safe_compact_text=public_safe_compact_text,
            ),
            from_node_id=handoff_node_id,
            to_node_id=successor_nid,
            relation="continues",
            reason=f"Successor continues after handoff from {handoff_from} to {handoff_to}.",
            refs=_task_graph_refs(
                "todo_ids",
                current_tid,
                public_safe_compact_text=public_safe_compact_text,
            ),
        )


def _task_graph_attach_evidence(
    *,
    current_todo: dict[str, Any],
    current_tid: str,
    current_nid: str,
    builder: _TaskGraphProjectionBuilder,
    public_safe_compact_text: Callable[..., str | None],
) -> None:
    evidence_text = public_safe_compact_text(current_todo.get("evidence"), limit=180)
    note_text = public_safe_compact_text(current_todo.get("note"), limit=180)
    if not evidence_text and not note_text:
        return
    ev_id = f"evidence:{current_tid}"
    ev_title = evidence_text or note_text or f"Evidence for {current_tid}"
    ev_node_id = builder.add_node(
        {
            "node_id": _task_graph_node_id(
                "node_evidence",
                ev_id,
                public_safe_compact_text=public_safe_compact_text,
            ),
            "kind": "evidence",
            "title": ev_title[:120],
            "state": "done",
            "refs": _task_graph_refs(
                "todo_ids",
                current_tid,
                public_safe_compact_text=public_safe_compact_text,
            ),
        }
    )
    if ev_node_id:
        builder.add_edge(
            edge_id=_task_graph_node_id(
                "edge_evidences",
                f"{ev_node_id}:{current_nid}",
                public_safe_compact_text=public_safe_compact_text,
            ),
            from_node_id=ev_node_id,
            to_node_id=current_nid,
            relation="validates",
            reason="Completion evidence supports the delivered predecessor.",
            refs=_task_graph_refs(
                "todo_ids",
                current_tid,
                public_safe_compact_text=public_safe_compact_text,
            ),
        )


def _task_graph_topology_row(
    todo_id: str,
    value: dict[str, Any],
    *,
    done: bool,
    public_safe_compact_text: Callable[..., str | None],
) -> dict[str, Any]:
    row: dict[str, Any] = {"todo_id": todo_id, "done": done, "successor_todo_ids": []}
    for field in ("unblocks_todo_id", "superseded_by", "resume_when"):
        text = public_safe_compact_text(value.get(field), limit=240)
        if text:
            row[field] = text
    successors = value.get("successor_todo_ids")
    if isinstance(successors, list):
        row["successor_todo_ids"] = [
            text for raw in successors
            if (text := public_safe_compact_text(raw, limit=120))
        ]
    return row


def _task_graph_build_predecessor_chain(
    *,
    selected_todo_id: str,
    selected_node_id: str,
    all_todos_by_id: dict[str, dict[str, Any]],
    builder: _TaskGraphProjectionBuilder,
    public_safe_compact_text: Callable[..., str | None],
    normalize_todo_status: Callable[[Any], str | None],
    todo_done_for_status: Callable[[str], bool],
    todo_status_open: str,
    max_predecessor_nodes: int,
    source_truncated: bool,
) -> dict[str, Any]:
    # Python owns public-safe rendering; TypeScript owns relation discovery and
    # bounded traversal. Never serialize evidence, notes, or private source text.
    renderable_todos: dict[str, dict[str, Any]] = {}
    rows = []
    for tid, value in all_todos_by_id.items():
        if not public_safe_compact_text(value.get("title") or value.get("text"), limit=160):
            continue
        renderable_todos[tid] = value
        state = _task_graph_todo_state(
            value,
            normalize_todo_status=normalize_todo_status,
            todo_done_for_status=todo_done_for_status,
            todo_status_open=todo_status_open, waiting_default=tid != selected_todo_id,
        )
        rows.append(_task_graph_topology_row(
            tid, value, done=state == "done", public_safe_compact_text=public_safe_compact_text,
        ))
    result = effect_runtime_result("work_item.task_graph.topology", {
        "schema_version": "task_graph_topology_request_v0",
        "selected_todo_id": selected_todo_id, "items": rows,
        "predecessor_limit": max_predecessor_nodes,
        "source_truncated": source_truncated,
    })
    if not isinstance(result, dict) or result.get("schema_version") != "task_graph_topology_result_v0":
        raise RuntimeError("TypeScript task graph topology shape mismatch")
    node_ids = {selected_todo_id: selected_node_id}
    for tid in result["predecessor_todo_ids"]:
        node_ids[tid] = builder.add_node(_task_graph_deliverable_node(
            todo=renderable_todos[tid], public_safe_compact_text=public_safe_compact_text,
            normalize_todo_status=normalize_todo_status,
            todo_done_for_status=todo_done_for_status,
            todo_status_open=todo_status_open, waiting_default=True,
        ))
    for edge in result["edges"]:
        source, target = edge["from_todo_id"], edge["to_todo_id"]
        builder.add_edge(
            edge_id=_task_graph_node_id(
                f"edge_{edge['source_relation']}",
                f"{node_ids[source]}:{node_ids[target]}",
                public_safe_compact_text=public_safe_compact_text,
            ),
            from_node_id=node_ids[source], to_node_id=node_ids[target],
            relation=edge["relation"], reason=edge["reason"],
            refs=_task_graph_refs("todo_ids", target,
                                  public_safe_compact_text=public_safe_compact_text),
        )
    for tid in [selected_todo_id, *result["predecessor_todo_ids"]]:
        value, nid = all_todos_by_id[tid], node_ids[tid]
        if _task_graph_todo_state(value, normalize_todo_status=normalize_todo_status,
                todo_done_for_status=todo_done_for_status, todo_status_open=todo_status_open) == "done":
            _task_graph_attach_evidence(
                current_todo=value, current_tid=tid, current_nid=nid, builder=builder,
                public_safe_compact_text=public_safe_compact_text,
            )
        if tid != selected_todo_id:
            # A shared ancestor may have several edges. Handoff presentation
            # keeps its historical single attachment to the first discovered one.
            successor = next(e["from_todo_id"] for e in result["edges"]
                             if e["to_todo_id"] == tid)
            _task_graph_attach_handoff(
                current_todo=value, current_tid=tid, current_nid=nid,
                successor_nid=node_ids[successor], builder=builder,
                public_safe_compact_text=public_safe_compact_text,
            )
    return result["completeness"]


def build_task_graph_projection(
    item: dict[str, Any],
    *,
    goal: dict[str, Any],
    goal_latest_runs: list[dict[str, Any]] | None = None,
    public_safe_compact_text: Callable[..., str | None],
    normalize_todo_status: Callable[[Any], str | None],
    todo_done_for_status: Callable[[str], bool],
    todo_status_open: str,
    open_todo_items: Callable[..., list[dict[str, Any]]],
    max_status_todos_per_role: int,
    todo_item_task_class: Callable[[dict[str, Any]], str],
    user_gate_task_class: str,
    todo_summary_open_count: Callable[[dict[str, Any] | None], int],
    latest_run: Callable[[dict[str, Any]], dict[str, Any] | None],
) -> dict[str, Any] | None:
    """Build a compact read-only graph from already-projected status fields."""

    goal_id = public_safe_compact_text(item.get("goal_id") or goal.get("id"), limit=120)
    if not goal_id:
        return None
    latest_runs = [run for run in goal_latest_runs or [] if isinstance(run, dict)]
    builder = _TaskGraphProjectionBuilder(public_safe_compact_text=public_safe_compact_text)

    agent_todo_summary = (
        item.get("agent_todos") if isinstance(item.get("agent_todos"), dict) else None
    )
    user_todo_summary = (
        item.get("user_todos") if isinstance(item.get("user_todos"), dict) else None
    )
    agent_todos_by_id = _task_graph_collect_todo_items(agent_todo_summary)
    user_todos_by_id = _task_graph_collect_todo_items(user_todo_summary)
    source_truncated = (
        _task_graph_source_items_truncated(agent_todo_summary)
        or _task_graph_source_items_truncated(user_todo_summary)
    )

    agent_items = open_todo_items(
        agent_todo_summary,
        limit=1,
        text_limit=180,
    )
    selected_todo = agent_items[0] if agent_items else None
    selected_todo_id = public_safe_compact_text(
        selected_todo.get("todo_id") if isinstance(selected_todo, dict) else None,
        limit=120,
    )
    selected_node_id: str | None = None
    if isinstance(selected_todo, dict):
        selected_node_id = builder.add_node(
            _task_graph_deliverable_node(
                todo=selected_todo,
                public_safe_compact_text=public_safe_compact_text,
                normalize_todo_status=normalize_todo_status,
                todo_done_for_status=todo_done_for_status,
                todo_status_open=todo_status_open,
            )
        )
        claimed_by = public_safe_compact_text(selected_todo.get("claimed_by"), limit=80)
        if claimed_by and selected_node_id:
            lease_id = f"claim:{goal_id}:{selected_todo_id or 'selected'}:{claimed_by}"
            lease_node_id = builder.add_node(
                {
                    "node_id": _task_graph_node_id(
                        "node_lease",
                        lease_id,
                        public_safe_compact_text=public_safe_compact_text,
                    ),
                    "kind": "lease",
                    "title": f"Claimed by {claimed_by}",
                    "state": "ready",
                    "refs": _task_graph_refs(
                        "lease_ids",
                        lease_id,
                        public_safe_compact_text=public_safe_compact_text,
                    ),
                    "owner_agent": claimed_by,
                }
            )
            builder.add_edge(
                edge_id=_task_graph_node_id(
                    "edge_depends",
                    f"{selected_node_id}:{lease_node_id}",
                    public_safe_compact_text=public_safe_compact_text,
                ),
                from_node_id=selected_node_id,
                to_node_id=lease_node_id,
                relation="depends_on",
                reason="Selected work depends on its active claim lease.",
                refs=_task_graph_refs(
                    "lease_ids",
                    lease_id,
                    public_safe_compact_text=public_safe_compact_text,
                ),
            )

    predecessor_metrics: dict[str, Any] = {}
    if selected_todo_id and selected_node_id and isinstance(selected_todo, dict):
        all_todos_by_id = {**agent_todos_by_id, **user_todos_by_id}
        all_todos_by_id.setdefault(selected_todo_id, selected_todo)
        predecessor_metrics = _task_graph_build_predecessor_chain(
            selected_todo_id=selected_todo_id,
            selected_node_id=selected_node_id,
            all_todos_by_id=all_todos_by_id,
            builder=builder,
            public_safe_compact_text=public_safe_compact_text,
            normalize_todo_status=normalize_todo_status,
            todo_done_for_status=todo_done_for_status,
            todo_status_open=todo_status_open,
            max_predecessor_nodes=TASK_GRAPH_MAX_PREDECESSOR_NODES,
            source_truncated=source_truncated,
        )

    user_items, user_gate_open_count = _task_graph_visible_user_gate_items(
        user_todo_summary,
        limit=TASK_GRAPH_MAX_USER_GATE_NODES,
        open_todo_items=open_todo_items,
        max_status_todos_per_role=max_status_todos_per_role,
        todo_item_task_class=todo_item_task_class,
        user_gate_task_class=user_gate_task_class,
        todo_summary_open_count=todo_summary_open_count,
    )
    for ordinal, todo in enumerate(user_items):
        todo_id = public_safe_compact_text(todo.get("todo_id"), limit=120)
        gate_id = todo_id or f"gate:{goal_id}:user:{ordinal + 1}"
        gate_node_id = builder.add_node(
            {
                "node_id": _task_graph_node_id(
                    "node_gate",
                    gate_id,
                    public_safe_compact_text=public_safe_compact_text,
                ),
                "kind": "gate",
                "title": todo.get("title") or todo.get("text") or "Open user gate",
                "state": _task_graph_todo_state(
                    todo,
                    normalize_todo_status=normalize_todo_status,
                    todo_done_for_status=todo_done_for_status,
                    todo_status_open=todo_status_open,
                    waiting_default=True,
                ),
                "refs": _task_graph_refs(
                    "gate_ids",
                    gate_id,
                    public_safe_compact_text=public_safe_compact_text,
                ),
            }
        )
        builder.add_edge(
            edge_id=_task_graph_node_id(
                "edge_blocks",
                f"{gate_node_id}:{selected_node_id}:{ordinal}",
                public_safe_compact_text=public_safe_compact_text,
            ),
            from_node_id=gate_node_id,
            to_node_id=selected_node_id,
            relation="blocks",
            reason="Open user gate blocks the gated delivery path.",
            refs=_task_graph_refs(
                "gate_ids",
                gate_id,
                public_safe_compact_text=public_safe_compact_text,
            ),
        )
    user_gate_truncated_count = max(0, user_gate_open_count - len(user_items))
    if user_gate_truncated_count:
        summary_node_id = builder.add_node(
            {
                "node_id": _task_graph_node_id(
                    "node_gate_summary",
                    f"{goal_id}:{user_gate_truncated_count}:more_user_gates",
                    public_safe_compact_text=public_safe_compact_text,
                ),
                "kind": "gate_summary",
                "title": f"{user_gate_truncated_count} more open user gates not expanded",
                "state": "waiting",
                "refs": _task_graph_refs(
                    "goal_ids",
                    goal_id,
                    public_safe_compact_text=public_safe_compact_text,
                ),
            }
        )
        builder.add_edge(
            edge_id=_task_graph_node_id(
                "edge_blocks",
                f"{summary_node_id}:{selected_node_id}:user_gate_summary",
                public_safe_compact_text=public_safe_compact_text,
            ),
            from_node_id=summary_node_id,
            to_node_id=selected_node_id,
            relation="blocks",
            reason="Additional open user gates stay on the cold path instead of expanding the task graph hot path.",
            refs=_task_graph_refs(
                "goal_ids",
                goal_id,
                public_safe_compact_text=public_safe_compact_text,
            ),
        )

    run_node_id = builder.add_node(
        _task_graph_latest_run_node(
            goal_latest_runs=latest_runs,
            selected_todo_id=selected_todo_id,
            public_safe_compact_text=public_safe_compact_text,
        )
    )
    builder.add_edge(
        edge_id=_task_graph_node_id(
            "edge_validates",
            f"{run_node_id}:{selected_node_id}",
            public_safe_compact_text=public_safe_compact_text,
        ),
        from_node_id=run_node_id,
        to_node_id=selected_node_id,
        relation="validates",
        reason="Latest compact run-history evidence validates or contextualizes the selected work.",
        refs=builder.refs_by_node_id.get(run_node_id or ""),
    )
    for relation, reason in _task_graph_latest_run_lineage_relations(
        run_node_id,
        selected_node_id,
        goal_latest_runs=latest_runs,
        public_safe_compact_text=public_safe_compact_text,
    ):
        builder.add_edge(
            edge_id=_task_graph_node_id(
                f"edge_{relation}",
                f"{run_node_id}:{selected_node_id}",
                public_safe_compact_text=public_safe_compact_text,
            ),
            from_node_id=run_node_id,
            to_node_id=selected_node_id,
            relation=relation,
            reason=reason,
            refs=builder.refs_by_node_id.get(run_node_id or ""),
        )

    replan = (
        item.get("autonomous_replan_obligation")
        if isinstance(item.get("autonomous_replan_obligation"), dict)
        else None
    )
    if replan and selected_node_id:
        replan_id = public_safe_compact_text(
            replan.get("schema_version") or replan.get("kind") or "autonomous_replan_obligation",
            limit=120,
        )
        repair_node_id = builder.add_node(
            {
                "node_id": _task_graph_node_id(
                    "node_repair",
                    replan_id,
                    public_safe_compact_text=public_safe_compact_text,
                ),
                "kind": "repair",
                "title": replan.get("recommended_action") or replan_id,
                "state": "ready",
                "refs": _task_graph_refs(
                    "todo_ids",
                    selected_todo_id,
                    public_safe_compact_text=public_safe_compact_text,
                ),
            }
        )
        builder.add_edge(
            edge_id=_task_graph_node_id(
                "edge_repairs",
                f"{repair_node_id}:{selected_node_id}",
                public_safe_compact_text=public_safe_compact_text,
            ),
            from_node_id=repair_node_id,
            to_node_id=selected_node_id,
            relation="repairs",
            reason="Autonomous repair/replan obligation should recover the selected work lane.",
            refs=_task_graph_refs(
                "todo_ids",
                selected_todo_id,
                public_safe_compact_text=public_safe_compact_text,
            ),
        )

    if not builder.nodes:
        return None
    derived_from: dict[str, Any] = {
        "source_of_truth": TASK_GRAPH_SOURCE_OF_TRUTH,
        "status_item_goal_id": goal_id,
        "run_history_window": "compact_latest_runs",
    }
    active_state_updated_at = _task_graph_active_state_updated_at(
        item,
        goal,
        public_safe_compact_text=public_safe_compact_text,
        latest_run=latest_run,
    )
    if active_state_updated_at:
        derived_from["active_state_updated_at"] = active_state_updated_at
    limits: dict[str, int | bool] = {
        "user_gate_node_limit": TASK_GRAPH_MAX_USER_GATE_NODES,
        "user_gate_open_count": user_gate_open_count,
        "user_gate_truncated_count": user_gate_truncated_count,
    }
    if predecessor_metrics:
        limits["predecessor_node_limit"] = predecessor_metrics["predecessor_limit"]
        limits["emitted_predecessor_count"] = predecessor_metrics["emitted_predecessor_count"]
        limits["predecessor_truncated"] = predecessor_metrics["predecessor_truncated"]
        limits["source_truncated"] = predecessor_metrics["source_truncated"]
        limits["missing_predecessor_count"] = predecessor_metrics["missing_predecessor_count"]
        limits["topology_complete"] = predecessor_metrics["topology_complete"]
    return {
        "schema_version": TASK_GRAPH_PROJECTION_SCHEMA_VERSION,
        "mode": "read_only",
        "goal_id": goal_id,
        "generated_at": _task_graph_generated_at(
            goal=goal,
            goal_latest_runs=latest_runs,
            public_safe_compact_text=public_safe_compact_text,
            latest_run=latest_run,
        ),
        "derived_from": derived_from,
        "truth_contract": {
            "event_ledger_is_source_of_truth": True,
            "projection_is_writable": False,
            "write_api": False,
            "recompute_rule": "Recompute from status, active state, gates, leases, and run history after each lifecycle event.",
        },
        "limits": limits,
        "nodes": builder.nodes,
        "edges": builder.edges,
    }


GOAL_TASK_MAP_SCHEMA_VERSION = "goal_task_map_v0"
GOAL_TASK_MAP_MAX_NODES = 120


def build_goal_task_map(
    *,
    goal_id: str,
    todos: list[dict[str, Any]],
    source_truncated: bool,
    public_safe_compact_text: Callable[..., str | None],
    normalize_todo_status: Callable[[Any], str | None],
    todo_done_for_status: Callable[[str], bool],
    todo_status_open: str,
    monitor_task_class: str,
) -> dict[str, Any]:
    """Read-only map of one Goal's active Todos and their recorded relations."""

    renderable: dict[str, tuple[dict[str, Any], str, str]] = {}
    rows = []
    for value in todos:
        tid = public_safe_compact_text(value.get("todo_id"), limit=120)
        title = public_safe_compact_text(value.get("title") or value.get("text"), limit=160)
        if not tid or not title or tid in renderable:
            continue
        state = _task_graph_todo_state(
            value,
            normalize_todo_status=normalize_todo_status,
            todo_done_for_status=todo_done_for_status,
            todo_status_open=todo_status_open,
        )
        renderable[tid] = (value, title, state)
        rows.append(_task_graph_topology_row(
            tid, value, done=state == "done", public_safe_compact_text=public_safe_compact_text,
        ))
    result = effect_runtime_result("work_item.task_graph.goal_topology", {
        "schema_version": "task_graph_goal_topology_request_v0",
        "items": rows,
        "node_limit": GOAL_TASK_MAP_MAX_NODES,
        "source_truncated": source_truncated,
    })
    if not isinstance(result, dict) or result.get("schema_version") != "task_graph_goal_topology_result_v0":
        raise RuntimeError("TypeScript goal task graph topology shape mismatch")
    nodes: list[dict[str, Any]] = []
    node_ids: dict[str, str] = {}
    for entry in result["nodes"]:
        tid = entry["todo_id"]
        value, title, state = renderable[tid]
        gate = value.get("role") == "user"
        node_ids[tid] = _task_graph_node_id(
            "node_gate" if gate else "node_todo", tid,
            public_safe_compact_text=public_safe_compact_text, durable_id=tid,
        )
        node: dict[str, Any] = {
            "node_id": node_ids[tid],
            "kind": "gate" if gate else (
                "monitor" if value.get("task_class") == monitor_task_class else "deliverable"
            ),
            "title": title,
            "state": state,
            "depth": entry["depth"],
            "refs": {"todo_ids": [tid]},
        }
        for key, field in (("owner_agent", "claimed_by"), ("task_domain", "task_domain")):
            text = public_safe_compact_text(value.get(field), limit=80)
            if text and not gate:
                node[key] = text
        nodes.append(node)
    edges = [
        {
            "edge_id": _task_graph_node_id(
                f"edge_{edge['source_relation']}",
                f"{node_ids[edge['from_todo_id']]}:{node_ids[edge['to_todo_id']]}",
                public_safe_compact_text=public_safe_compact_text,
            ),
            "from_node_id": node_ids[edge["from_todo_id"]],
            "to_node_id": node_ids[edge["to_todo_id"]],
            "relation": edge["relation"],
            "enforcement": edge["enforcement"],
            "reason": edge["reason"],
        }
        for edge in result["edges"]
    ]
    return {
        "schema_version": GOAL_TASK_MAP_SCHEMA_VERSION,
        "mode": "read_only",
        "goal_id": goal_id,
        "limits": result["completeness"],
        "nodes": nodes,
        "edges": edges,
    }
