from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from loopx.capabilities.explore.research_evidence import append_research_observation
from loopx.capabilities.explore.result_log import (
    append_explore_result_event, append_explore_result_events, build_explore_edge_event, build_explore_node_event,
    build_explore_result_projection, explore_result_log_path, load_explore_result_events_strict,
)
from loopx.extensions.lark.presentation.explore_results import _node_record_values


GOAL = "research-fixture"


def observation(node: str, *, target: str | None = None) -> dict:
    return {
        "schema_version": "typed_research_observation_v0", "explore_node_id": node,
        "progress": {"schema_version": "typed_progress_observation_v0", "work_item_id": f"todo-{node}",
                     "result_class": "exploration_exhausted", "coverage_scope_id": f"scope-{node}",
                     "coverage_complete": True, "evidence_ids": [f"ev-{node}"]},
        "closure_basis": {"schema_version": "research_closure_basis_v0", "disposition": "bounded",
                          "constraints": [{"kind": "invariant", "id": "boundary", "role": "decisive"}],
                          "evidence_ids": [f"ev-{node}"]},
        "composition_candidates": [{"target_node_id": target, "basis": "explicit",
                                    "interaction_kind": "state_interference", "evidence_ids": ["ev-a", "ev-b"]}] if target else [],
    }


def node(path: Path, name: str, *, kind: str = "hypothesis", status: str = "resolved") -> None:
    append_explore_result_event(path, build_explore_node_event(
        goal_id=GOAL, node_id=name, title=f"Research {name}", node_kind=kind,
        status=status, evidence_refs=[f"ev-{name}"],
    ))


def projection(path: Path) -> dict:
    return build_explore_result_projection(load_explore_result_events_strict(path, goal_id=GOAL), goal_id=GOAL)


def test_real_append_replay_projection_and_lark_consumer(tmp_path: Path) -> None:
    path = explore_result_log_path(tmp_path, GOAL)
    node(path, "a")
    node(path, "b")
    legacy = projection(path)
    assert "research_frontier" not in legacy
    assert all("research_summary" not in node for node in legacy["nodes"])
    append_research_observation(path, goal_id=GOAL, observation=observation("b"))
    raw = observation("a", target="b")
    assert append_research_observation(path, goal_id=GOAL, observation=raw)["written"]
    before = path.read_bytes()
    assert append_research_observation(path, goal_id=GOAL, observation=raw)["replayed"]
    assert path.read_bytes() == before
    view = projection(path)
    assert view["research_frontier"]["candidate_count"] == 1
    assert view["research_frontier"]["pending_count"] == 1
    row = _node_record_values(view["nodes"][0], goal_id=GOAL, source_id="fixture")
    assert "composition 1 pending" in row["Summary"]
    assert "closure bounded" in row["Summary"]
    # The existing recovery path imports latest node snapshots as a batch,
    # which may reference a sibling later in that same atomic batch.
    snapshots = {event["result_id"]: event for event in load_explore_result_events_strict(path, goal_id=GOAL)}
    restored = tmp_path / "restored.jsonl"
    append_explore_result_events(restored, list(snapshots.values()), expected_goal_id=GOAL)
    assert projection(restored)["research_frontier"] == view["research_frontier"]


def test_result_requires_current_exact_input_lineage_and_survives_invalidation(tmp_path: Path) -> None:
    path = explore_result_log_path(tmp_path, GOAL)
    for name in ["a", "b"]:
        node(path, name)
    append_research_observation(path, goal_id=GOAL, observation=observation("b"))
    append_research_observation(path, goal_id=GOAL, observation=observation("a", target="b"))
    node(path, "joint", kind="experiment")
    for name in ["a", "b"]:
        append_explore_result_event(path, build_explore_edge_event(
            goal_id=GOAL, from_node="joint", to_node=name, edge_type="depends_on"))
    result = observation("joint")
    with pytest.raises(ValueError, match="exact current input"):
        append_research_observation(path, goal_id=GOAL, observation=result)
    result["input_observations"] = projection(path)["research_frontier"]["gaps"][0]["input_observations"]
    append_research_observation(path, goal_id=GOAL, observation=result)
    assert projection(path)["research_frontier"]["observed_count"] == 1
    node(path, "a", status="open")
    invalidated = projection(path)["research_frontier"]
    assert invalidated["candidate_count"] == 1
    assert invalidated["ineligible_count"] == 1
    before = path.read_bytes()
    assert append_research_observation(path, goal_id=GOAL, observation=result)["replayed"]
    assert path.read_bytes() == before
    revised = observation("a")
    node(path, "a")
    revised["progress"]["evidence_ids"] = ["ev-a-new"]
    revised["closure_basis"]["evidence_ids"] = ["ev-a-new"]
    append_research_observation(path, goal_id=GOAL, observation=revised)
    assert projection(path)["research_frontier"]["observed_count"] == 0


@pytest.mark.parametrize("patch", [
    {"explore_node_id": "missing"}, {"explore_node_id": "/Users/example/private"},
    {"closure_basis": None}, {"raw_prompt": "should never persist"},
])
def test_invalid_observation_never_appends(tmp_path: Path, patch: dict) -> None:
    path = explore_result_log_path(tmp_path, GOAL)
    node(path, "a")
    before = path.read_bytes()
    with pytest.raises(ValueError):
        append_research_observation(path, goal_id=GOAL, observation={**observation("a"), **patch})
    assert path.read_bytes() == before


def test_unknown_and_unattributed_candidate_rejected(tmp_path: Path) -> None:
    path = explore_result_log_path(tmp_path, GOAL)
    node(path, "a")
    with pytest.raises(ValueError, match="existing same-goal"):
        append_research_observation(path, goal_id=GOAL, observation=observation("a", target="b"))
    node(path, "b")
    raw = observation("a", target="b")
    raw["composition_candidates"][0]["evidence_ids"] = ["ev-a", "unattributed"]
    with pytest.raises(ValueError, match="attributable to both"):
        append_research_observation(path, goal_id=GOAL, observation=raw)


@pytest.mark.parametrize("patch", [{"schema_version": None}, {"evidence_ids": {"ev-a": "wrong shape"}}])
def test_research_transport_does_not_coerce_malformed_progress(tmp_path: Path, patch: dict) -> None:
    path = explore_result_log_path(tmp_path, GOAL)
    node(path, "a")
    raw = observation("a")
    raw["progress"].update(patch)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        append_research_observation(path, goal_id=GOAL, observation=raw)
    assert path.read_bytes() == before


@pytest.mark.parametrize("batch", [False, True])
def test_generic_writers_cannot_bypass_attribution(tmp_path: Path, batch: bool) -> None:
    path = explore_result_log_path(tmp_path, GOAL)
    node(path, "a")
    forged = build_explore_node_event(goal_id=GOAL, node_id="a", title="Research a",
        status="resolved", research_observation=observation("a", target="missing"))
    before = path.read_bytes()
    with pytest.raises(ValueError, match="existing same-goal"):
        if batch:
            append_explore_result_events(path, [forged], expected_goal_id=GOAL)
        else:
            append_explore_result_event(path, forged)
    assert path.read_bytes() == before


def test_real_cli_uses_synthetic_source_runtime_and_readback(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    registry = tmp_path / "registry.json"
    runtime = tmp_path / "runtime"
    registry.write_text(json.dumps({"schema_version": "loopx_registry_v1", "runtime_root": str(runtime), "goals": []}))
    path = explore_result_log_path(runtime, GOAL)
    node(path, "a")
    packet = tmp_path / "observation.json"
    packet.write_text(json.dumps(observation("a")))

    def command(*args: str) -> dict:
        completed = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(registry),
            "--runtime-root", str(runtime), "--format", "json", "explore", *args], cwd=root, text=True,
            capture_output=True, check=False)
        assert completed.returncode == 0, completed.stdout + completed.stderr
        return json.loads(completed.stdout)

    result = command("observe", "--goal-id", GOAL, "--observation-json", str(packet))
    assert result["ok"] and result["written"]
    assert command("observe", "--goal-id", GOAL, "--observation-json", str(packet))["replayed"]
    view = command("summary", "--goal-id", GOAL)
    assert view["nodes"][0]["research_observation"] == result["observation"]
    assert view["research_frontier"]["mode"] == "read_only_shadow"
