#!/usr/bin/env python3
"""Smoke-test the public task_graph_projection_v0 fixture and contract."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.cli_commands.status import review_packet_handoff_only_payload  # noqa: E402
from loopx.control_plane.todos.handoff_note import (  # noqa: E402
    attach_todo_handoff_note,
)

from loopx.review_packet import build_review_packet  # noqa: E402
from loopx.status import build_task_graph_projection  # noqa: E402

FIXTURE_PATH = REPO_ROOT / "examples/fixtures/task-graph-projection-status.public.json"
CONTRACT_PATH = REPO_ROOT / "docs/reference/protocols/task-graph-projection-v0.md"
STATUS_CONTRACT_PATH = REPO_ROOT / "docs/status-data-contract.md"
PROTOCOL_INDEX_PATH = REPO_ROOT / "docs/reference/protocols/README.md"
STATE_MODEL_PATH = REPO_ROOT / "docs/state-interaction-model.md"

ALLOWED_NODE_KINDS = {
    "deliverable",
    "gate",
    "gate_summary",
    "lease",
    "validation",
    "repair",
    "handoff",
    "evidence",
}
ALLOWED_NODE_STATES = {"open", "ready", "blocked", "done", "waiting", "unknown"}
ALLOWED_EDGE_RELATIONS = {
    "depends_on",
    "blocks",
    "validates",
    "repairs",
    "audits",
    "continues",
    "hands_off_to",
    "supersedes",
}
ALLOWED_REF_KEYS = {
    "todo_ids",
    "gate_ids",
    "goal_ids",
    "lease_ids",
    "run_ids",
    "review_packet_ids",
}
SOURCE_OF_TRUTH = {
    "event_ledger",
    "active_goal_state",
    "todos",
    "gates",
    "leases",
    "run_history",
}
PRIVATE_PATTERNS = [
    re.compile(r"/Users/[A-Za-z0-9._-]+/"),
    re.compile(r"/home/[A-Za-z0-9._-]+/"),
    re.compile(r"/private/"),
    re.compile(r"[A-Za-z]:\\\\Users\\\\"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._-]+"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def assert_contains(text: str, needle: str, label: str) -> None:
    if needle not in text:
        raise AssertionError(f"{label} missing {needle!r}")


def assert_public_safe(text: str, label: str) -> None:
    for pattern in PRIVATE_PATTERNS:
        if pattern.search(text):
            raise AssertionError(f"{label} matched private pattern {pattern.pattern!r}")


def assert_refs(refs: object, label: str) -> None:
    assert isinstance(refs, dict) and refs, (label, refs)
    unknown = set(refs) - ALLOWED_REF_KEYS
    assert not unknown, (label, unknown)
    for key, values in refs.items():
        assert isinstance(values, list) and values, (label, key, values)
        for value in values:
            assert isinstance(value, str) and value, (label, key, value)
            assert not value.startswith("/"), (label, key, value)


def assert_projection_shape(
    projection: dict[str, object],
    *,
    goal_id: str,
    label: str,
    min_nodes: int = 4,
    min_edges: int = 3,
) -> None:
    assert projection["schema_version"] == "task_graph_projection_v0", (label, projection)
    assert projection["mode"] == "read_only", (label, projection)
    assert projection["goal_id"] == goal_id, (label, projection)
    assert set(projection["derived_from"]["source_of_truth"]) == SOURCE_OF_TRUTH, (label, projection)
    truth = projection["truth_contract"]
    assert truth["event_ledger_is_source_of_truth"] is True, (label, truth)
    assert truth["projection_is_writable"] is False, (label, truth)
    assert truth["write_api"] is False, (label, truth)
    limits = projection["limits"]
    assert limits["user_gate_node_limit"] == 2, (label, limits)
    assert limits["user_gate_open_count"] >= 0, (label, limits)
    assert limits["user_gate_truncated_count"] >= 0, (label, limits)

    nodes = projection["nodes"]
    edges = projection["edges"]
    assert isinstance(nodes, list) and len(nodes) >= min_nodes, (label, nodes)
    assert isinstance(edges, list) and len(edges) >= min_edges, (label, edges)
    node_ids = [node["node_id"] for node in nodes]
    assert len(node_ids) == len(set(node_ids)), (label, node_ids)
    node_id_set = set(node_ids)

    for node in nodes:
        assert node["kind"] in ALLOWED_NODE_KINDS, (label, node)
        assert node["state"] in ALLOWED_NODE_STATES, (label, node)
        assert isinstance(node["title"], str) and node["title"], (label, node)
        assert_refs(node.get("refs"), f"{label} node {node['node_id']}")

    for edge in edges:
        assert edge["relation"] in ALLOWED_EDGE_RELATIONS, (label, edge)
        assert edge["from_node_id"] in node_id_set, (label, edge)
        assert edge["to_node_id"] in node_id_set, (label, edge)
        assert edge["from_node_id"] != edge["to_node_id"], (label, edge)
        assert isinstance(edge["reason"], str) and edge["reason"], (label, edge)
        if "refs" in edge:
            assert_refs(edge["refs"], f"{label} edge {edge['edge_id']}")


def assert_runtime_projection_builder() -> None:
    goal_id = "runtime-task-graph"
    item = {
        "goal_id": goal_id,
        "status": "operator_gate",
        "waiting_on": "controller",
        "recommended_action": "Implement the task graph projection runtime seam.",
        "user_todos": {
            "open_count": 5,
            "items": [
                {
                    "todo_id": "todo_review_gate",
                    "text": "[P1] Review runtime projection before merge.",
                    "status": "open",
                    "task_class": "user_gate",
                },
                {
                    "todo_id": "todo_policy_gate",
                    "text": "[P1] Confirm task graph cap and truncation policy.",
                    "status": "open",
                    "task_class": "user_gate",
                },
                {
                    "todo_id": "todo_dashboard_gate",
                    "text": "[P2] Decide whether dashboard should expand graph details.",
                    "status": "open",
                    "task_class": "user_gate",
                },
                {
                    "todo_id": "todo_packet_gate",
                    "text": "[P2] Confirm review packet detail path for full gate list.",
                    "status": "open",
                    "task_class": "user_gate",
                },
                {
                    "todo_id": "todo_cold_path_gate",
                    "text": "[P2] Confirm cold path remains the full user todo list.",
                    "status": "open",
                    "task_class": "user_gate",
                },
            ]
        },
        "agent_todos": {
            "items": [
                {
                    "todo_id": "todo_runtime_projection",
                    "text": "[P1] Implement task graph projection.",
                    "title": "Implement task graph projection.",
                    "status": "open",
                    "claimed_by": "codex-main-control",
                }
            ]
        },
        "autonomous_replan_obligation": {
            "schema_version": "autonomous_replan_obligation_v0",
            "recommended_action": "Recover selected work if it stalls.",
        },
    }
    goal = {"id": goal_id}
    latest_runs = [
        {
            "generated_at": "2026-06-21T13:00:00Z",
            "classification": "task_graph_projection_audit_continuation_smoke",
            "recommended_action": "Continue the selected projection lane after audit evidence is recorded.",
        }
    ]
    projection = build_task_graph_projection(item, goal=goal, goal_latest_runs=latest_runs)
    assert isinstance(projection, dict), projection
    assert_projection_shape(projection, goal_id=goal_id, label="runtime projection", min_nodes=5, min_edges=6)
    kinds = {node["kind"] for node in projection["nodes"]}
    assert {"deliverable", "gate", "lease", "validation", "repair"} <= kinds, projection
    assert "gate_summary" in kinds, projection
    gate_nodes = [node for node in projection["nodes"] if node["kind"] == "gate"]
    summary_nodes = [node for node in projection["nodes"] if node["kind"] == "gate_summary"]
    assert len(gate_nodes) == 2, projection
    assert len(summary_nodes) == 1, projection
    assert summary_nodes[0]["title"] == "3 more open user gates not expanded", summary_nodes
    limits = projection["limits"]
    assert limits["user_gate_node_limit"] == 2, limits
    assert limits["user_gate_open_count"] == 5, limits
    assert limits["user_gate_truncated_count"] == 3, limits
    assert "predecessor_node_limit" in limits, limits
    assert "emitted_predecessor_count" in limits, limits
    relations = {edge["relation"] for edge in projection["edges"]}
    assert {"blocks", "depends_on", "validates", "repairs", "audits", "continues"} <= relations, projection
    forbidden_keys = {"write_command", "agent_command", "raw_log", "raw_transcript"}
    projection_keys = set(json.dumps(projection, sort_keys=True).split('"'))
    assert not (projection_keys & forbidden_keys), projection_keys & forbidden_keys

    status_payload = {
        "registry": "./fixtures/registry.json",
        "runtime_root": "./fixtures/runtime",
        "attention_queue": {
            "items": [
                {
                    **item,
                    "task_graph_projection": projection,
                    "project_asset": {
                        "next_action": "Implement the task graph projection runtime seam.",
                        "stop_condition": "stop before write authority or raw evidence export",
                    },
                }
            ]
        },
        "run_history": {"goals": [goal]},
    }
    packet = build_review_packet(status_payload, goal_id=goal_id)
    assert packet["ok"] is True, packet
    assert packet["task_graph_projection"] == projection, packet
    handoff_only = review_packet_handoff_only_payload(packet)
    assert "task_graph_projection" not in handoff_only, handoff_only
    assert_public_safe(json.dumps(packet, sort_keys=True), "runtime review packet")


def _predecessor_todo(
    todo_id: str,
    *,
    status: str,
    successor_todo_ids: list[str] | None = None,
) -> dict[str, object]:
    todo: dict[str, object] = {
        "todo_id": todo_id,
        "title": f"Task {todo_id}",
        "text": f"Task {todo_id}",
        "status": status,
        "done": status == "done",
    }
    if successor_todo_ids:
        todo["successor_todo_ids"] = successor_todo_ids
    return todo


def _predecessor_projection(
    *,
    predecessor_count: int,
    predecessor_status: str = "done",
    reverse_input: bool = False,
    total_count: int | None = None,
) -> dict[str, object]:
    root = _predecessor_todo("todo_graph_root", status="open")
    predecessors = [
        _predecessor_todo(
            f"todo_graph_parent_{index:02d}",
            status=predecessor_status,
            successor_todo_ids=["todo_graph_root"],
        )
        for index in range(predecessor_count)
    ]
    if reverse_input:
        predecessors.reverse()
    items = [root, *predecessors]
    return build_task_graph_projection(
        {
            "goal_id": "task-graph-predecessor-budget",
            "agent_todos": {
                "total_count": total_count if total_count is not None else len(items),
                "open_count": 1,
                "items": items,
            },
            "user_todos": {
                "total_count": 0,
                "open_count": 0,
                "items": [],
            },
        },
        goal={"id": "task-graph-predecessor-budget"},
    )


def _predecessor_todo_ids(projection: dict[str, object]) -> list[str]:
    return [
        node["refs"]["todo_ids"][0]
        for node in projection["nodes"]
        if node["kind"] == "deliverable"
        and node["refs"]["todo_ids"][0] != "todo_graph_root"
    ]


def assert_predecessor_budget_and_actor_contract() -> None:
    expected_predecessors = [
        f"todo_graph_parent_{index:02d}" for index in range(4)
    ]
    for count, expected_truncated in ((0, False), (4, False), (8, True)):
        projection = _predecessor_projection(predecessor_count=count)
        limits = projection["limits"]
        assert limits["emitted_predecessor_count"] == min(count, 4), limits
        assert limits["predecessor_truncated"] is expected_truncated, limits
        assert limits["source_truncated"] is False, limits
        assert _predecessor_todo_ids(projection) == expected_predecessors[:count], projection

    forward = _predecessor_projection(predecessor_count=8)
    reversed_input = _predecessor_projection(
        predecessor_count=8,
        reverse_input=True,
    )
    assert _predecessor_todo_ids(forward) == expected_predecessors, forward
    assert _predecessor_todo_ids(reversed_input) == expected_predecessors, reversed_input

    open_predecessors = _predecessor_projection(
        predecessor_count=8,
        predecessor_status="open",
    )
    open_limits = open_predecessors["limits"]
    assert open_limits["emitted_predecessor_count"] == 4, open_limits
    assert open_limits["predecessor_truncated"] is True, open_limits
    assert _predecessor_todo_ids(open_predecessors) == expected_predecessors, open_predecessors

    source_truncated = _predecessor_projection(
        predecessor_count=0,
        total_count=9,
    )
    source_limits = source_truncated["limits"]
    assert source_limits["predecessor_truncated"] is False, source_limits
    assert source_limits["source_truncated"] is True, source_limits

    actor_todo = {"todo_id": "todo_actor_audit", "role": "agent", "status": "open",
        "title": "Audit actor projection", "text": "Audit actor projection",
        "created_by": "creator-agent", "task_class": "advancement_task"}
    actor_projection = {"agent_todos": {"items": [actor_todo], "total_count": 1, "open_count": 1},
        "user_todos": {"items": [], "total_count": 0, "open_count": 0}}
    actor_graph = build_task_graph_projection(
        {
            "goal_id": "task-graph-actor-clearing",
            "agent_todos": actor_projection["agent_todos"],
            "user_todos": actor_projection["user_todos"],
        },
        goal={"id": "task-graph-actor-clearing"},
    )
    actor_node = next(
        node for node in actor_graph["nodes"] if node["kind"] == "deliverable"
    )
    assert "actor_agent" not in actor_node, actor_node


def assert_diamond_dag_predecessor_edges() -> None:
    """In a diamond DAG root <- {a, b} <- shared, both edges a->shared and b->shared must be present."""
    root = {
        "todo_id": "todo_root",
        "title": "Root",
        "text": "Root",
        "status": "open",
        "done": False,
    }
    a = {
        "todo_id": "todo_a",
        "title": "Task A",
        "text": "Task A",
        "status": "done",
        "done": True,
        "successor_todo_ids": ["todo_root"],
    }
    b = {
        "todo_id": "todo_b",
        "title": "Task B",
        "text": "Task B",
        "status": "done",
        "done": True,
        "successor_todo_ids": ["todo_root"],
    }
    shared = {
        "todo_id": "todo_shared",
        "title": "Shared",
        "text": "Shared",
        "status": "done",
        "done": True,
        "successor_todo_ids": ["todo_a", "todo_b"],
    }
    projection = build_task_graph_projection(
        {
            "goal_id": "task-graph-diamond-dag",
            "agent_todos": {
                "total_count": 4,
                "open_count": 1,
                "items": [root, a, b, shared],
            },
            "user_todos": {
                "total_count": 0,
                "open_count": 0,
                "items": [],
            },
        },
        goal={"id": "task-graph-diamond-dag"},
    )
    assert projection is not None
    edges = projection["edges"]
    nodes = projection["nodes"]
    node_ids = {node["refs"]["todo_ids"][0]: node["node_id"] for node in nodes if node["kind"] == "deliverable"}
    # 4 nodes: root, a, b, shared
    assert len(node_ids) == 4, node_ids
    # Check all expected edges exist
    edge_pairs = {(e["from_node_id"], e["to_node_id"], e["relation"]) for e in edges}
    root_id = node_ids["todo_root"]
    a_id = node_ids["todo_a"]
    b_id = node_ids["todo_b"]
    shared_id = node_ids["todo_shared"]
    assert (root_id, a_id, "continues") in edge_pairs, edge_pairs
    assert (root_id, b_id, "continues") in edge_pairs, edge_pairs
    assert (a_id, shared_id, "continues") in edge_pairs, edge_pairs
    assert (b_id, shared_id, "continues") in edge_pairs, edge_pairs
    assert projection["limits"]["predecessor_truncated"] is False


def assert_evidence_only_for_done_predecessor() -> None:
    """Evidence should only attach when predecessor is done, not when open."""
    root = {
        "todo_id": "todo_root",
        "title": "Root",
        "text": "Root",
        "status": "open",
        "done": False,
        "successor_todo_ids": ["todo_done_pred", "todo_open_pred"],
    }
    done_pred = {
        "todo_id": "todo_done_pred",
        "title": "Done predecessor",
        "text": "Done predecessor",
        "status": "done",
        "done": True,
        "evidence": "Completed work evidence.",
        "successor_todo_ids": ["todo_root"],
    }
    open_pred = {
        "todo_id": "todo_open_pred",
        "title": "Open predecessor",
        "text": "Open predecessor",
        "status": "open",
        "done": False,
        "evidence": "In-progress notes.",
        "successor_todo_ids": ["todo_root"],
    }
    projection = build_task_graph_projection(
        {
            "goal_id": "task-graph-evidence-done-only",
            "agent_todos": {
                "total_count": 3,
                "open_count": 1,
                "items": [root, done_pred, open_pred],
            },
            "user_todos": {
                "total_count": 0,
                "open_count": 0,
                "items": [],
            },
        },
        goal={"id": "task-graph-evidence-done-only"},
    )
    assert projection is not None
    evidence_nodes = [n for n in projection["nodes"] if n["kind"] == "evidence"]
    # Only one evidence node (for done_pred), not for open_pred
    assert len(evidence_nodes) == 1, evidence_nodes
    ev_node = evidence_nodes[0]
    evidence_todo_ids = ev_node["refs"]["todo_ids"]
    assert "todo_done_pred" in evidence_todo_ids, evidence_todo_ids
    assert "todo_open_pred" not in evidence_todo_ids, evidence_todo_ids


def assert_handoff_for_both_open_and_done_predecessor() -> None:
    """Handoff should attach for non-root predecessors regardless of done status."""
    root = {
        "todo_id": "todo_root",
        "title": "Root",
        "text": "Root",
        "status": "open",
        "done": False,
        "successor_todo_ids": ["todo_done_handoff", "todo_open_handoff"],
    }
    done_handoff = {
        "todo_id": "todo_done_handoff",
        "title": "Done predecessor with handoff",
        "text": "Done predecessor with handoff",
        "status": "done",
        "done": True,
        "handoff_note": {
            "from_agent": "agent-a",
            "to_agent": "agent-b",
            "status": "done",
        },
        "successor_todo_ids": ["todo_root"],
    }
    open_handoff = {
        "todo_id": "todo_open_handoff",
        "title": "Open predecessor with handoff",
        "text": "Open predecessor with handoff",
        "status": "open",
        "done": False,
        "claimed_by": "agent-d",
        "agent_id": "agent-c",
        "successor_todo_ids": ["todo_root"],
    }
    open_handoff = attach_todo_handoff_note(open_handoff)
    projection = build_task_graph_projection(
        {
            "goal_id": "task-graph-handoff-both",
            "agent_todos": {
                "total_count": 3,
                "open_count": 1,
                "items": [root, done_handoff, open_handoff],
            },
            "user_todos": {
                "total_count": 0,
                "open_count": 0,
                "items": [],
            },
        },
        goal={"id": "task-graph-handoff-both"},
    )
    assert projection is not None
    handoff_nodes = [n for n in projection["nodes"] if n["kind"] == "handoff"]
    assert len(handoff_nodes) == 2, handoff_nodes
    # Check that the done handoff has state "done"
    done_hn = next(n for n in handoff_nodes if n["from_agent"] == "agent-a")
    assert done_hn["state"] == "done", done_hn
    # Check that the open handoff derives state from the todo's actual status
    # (attach_todo_handoff_note does not inject a status field)
    open_hn = next(n for n in handoff_nodes if n["from_agent"] != "agent-a")
    assert open_hn["state"] == "waiting", open_hn


def assert_handoff_missing_status_not_default_to_done() -> None:
    """Open predecessor with an attach_todo_handoff_note-produced handoff
    (which has no status field) must not default to 'done'."""
    open_handoff = {
        "todo_id": "todo_open_no_status",
        "title": "Open predecessor without explicit handoff status",
        "text": "Open predecessor without explicit handoff status",
        "status": "open",
        "done": False,
        "claimed_by": "agent-x",
        "agent_id": "agent-e",
        "resume_when": "todo_done:todo_target",
        "successor_todo_ids": ["todo_root"],
    }
    open_handoff = attach_todo_handoff_note(open_handoff)
    assert "status" not in open_handoff["handoff_note"], (
        "attach_todo_handoff_note must not inject a status field"
    )
    root = {
        "todo_id": "todo_root",
        "title": "Root",
        "text": "Root",
        "status": "open",
        "done": False,
    }
    projection = build_task_graph_projection(
        {
            "goal_id": "task-graph-handoff-no-status",
            "agent_todos": {
                "total_count": 2,
                "open_count": 1,
                "items": [root, open_handoff],
            },
            "user_todos": {
                "total_count": 0,
                "open_count": 0,
                "items": [],
            },
        },
        goal={"id": "task-graph-handoff-no-status"},
    )
    assert projection is not None
    handoff_nodes = [n for n in projection["nodes"] if n["kind"] == "handoff"]
    assert len(handoff_nodes) == 1, handoff_nodes
    hn = handoff_nodes[0]
    assert hn["state"] != "done", (
        f"handoff on open predecessor must not default to done: {hn}"
    )


def assert_cycle_predecessor_safety() -> None:
    """A cycle A->B->A should not cause infinite loop or crash."""
    root = {
        "todo_id": "todo_cycle_root",
        "title": "Cycle Root",
        "text": "Cycle Root",
        "status": "open",
        "done": False,
    }
    a = {
        "todo_id": "todo_cycle_a",
        "title": "Cycle A",
        "text": "Cycle A",
        "status": "done",
        "done": True,
        "successor_todo_ids": ["todo_cycle_root", "todo_cycle_b"],
    }
    b = {
        "todo_id": "todo_cycle_b",
        "title": "Cycle B",
        "text": "Cycle B",
        "status": "done",
        "done": True,
        "successor_todo_ids": ["todo_cycle_a"],
    }
    projection = build_task_graph_projection(
        {
            "goal_id": "task-graph-cycle",
            "agent_todos": {
                "total_count": 3,
                "open_count": 1,
                "items": [root, a, b],
            },
            "user_todos": {
                "total_count": 0,
                "open_count": 0,
                "items": [],
            },
        },
        goal={"id": "task-graph-cycle"},
    )
    assert projection is not None
    # Should not have exploded; should have exactly 3 deliverable nodes
    deliverable_nodes = [n for n in projection["nodes"] if n["kind"] == "deliverable"]
    assert len(deliverable_nodes) == 3, deliverable_nodes
    # All lineage edges survive; visited nodes, not edges, bound cycle traversal.
    edge_pairs = {(e["from_node_id"], e["to_node_id"], e["relation"]) for e in projection["edges"]}
    node_ids = {n["refs"]["todo_ids"][0]: n["node_id"] for n in deliverable_nodes}
    root_id = node_ids["todo_cycle_root"]
    a_id = node_ids["todo_cycle_a"]
    b_id = node_ids["todo_cycle_b"]
    assert (root_id, a_id, "continues") in edge_pairs
    assert (a_id, b_id, "continues") in edge_pairs
    assert (b_id, a_id, "continues") in edge_pairs


def main() -> int:
    fixture_text = read(FIXTURE_PATH)
    contract = read(CONTRACT_PATH)
    status_contract = read(STATUS_CONTRACT_PATH)
    protocol_index = read(PROTOCOL_INDEX_PATH)
    state_model = read(STATE_MODEL_PATH)

    for label, text in {
        "fixture": fixture_text,
        "contract": contract,
        "status contract": status_contract,
    }.items():
        assert_public_safe(text, label)

    for needle in [
        "attention_queue.items[].task_graph_projection",
        "loopx --format json review-packet --goal-id <goal-id>",
        "event ledger",
        "active goal state",
        "projection_is_writable=false",
        "write_api=false",
        "todo_ids",
        "gate_ids",
        "lease_ids",
        "run_ids",
        "audits",
        "continues",
        "repair, audit, and continuation relations",
    ]:
        assert_contains(contract, needle, "contract")

    assert_contains(status_contract, "task_graph_projection_v0", "status contract")
    assert_contains(protocol_index, "task_graph_projection_v0", "protocol index")
    assert_contains(state_model, "task-graph-projection-v0.md", "state model")

    payload = json.loads(fixture_text)
    item = payload["attention_queue"]["items"][0]
    projection = item["task_graph_projection"]

    assert_projection_shape(projection, goal_id=item["goal_id"], label="fixture projection")

    forbidden_keys = {"write_command", "agent_command", "raw_log", "raw_transcript"}
    fixture_keys = set(json.dumps(payload, sort_keys=True).split('"'))
    assert not (fixture_keys & forbidden_keys), fixture_keys & forbidden_keys
    assert_runtime_projection_builder()
    assert_predecessor_budget_and_actor_contract()
    assert_diamond_dag_predecessor_edges()
    assert_evidence_only_for_done_predecessor()
    assert_handoff_for_both_open_and_done_predecessor()
    assert_handoff_missing_status_not_default_to_done()
    assert_cycle_predecessor_safety()

    print("task-graph-projection-fixture-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
