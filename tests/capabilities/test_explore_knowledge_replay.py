"""Historical knowledge is reusable; execution lineage is not transferable."""
import json

import pytest

from loopx.capabilities.explore.result_log import (
    append_explore_result_event, append_explore_result_events,
    build_explore_node_event, build_explore_edge_event, build_explore_finding_event,
    build_explore_result_projection, load_explore_result_events_strict,
    rebase_explore_knowledge_events, validate_explore_result_event,
)


def source_events():
    common = dict(goal_id="source", agent_id="source-author", run_id="original-run",
                  recorded_at="2026-01-01T00:00:00Z")
    return [build_explore_node_event(**common, node_id="route", title="Tested route", status="dead_end"),
            build_explore_node_event(**common, node_id="alternative", title="Unresolved alternative",
                                    parent_id="route", status="open"),
            build_explore_finding_event(**common, finding_id="negative", node_id="route",
                title="Transfer failed", status="refuted", confidence=.8,
                summary="Only the tested inputs; the alternative is still untested.",
                evidence_refs=[f"validation:sample-{i}" for i in range(16)], tags=[f"tag-{i}" for i in range(8)]),
            build_explore_edge_event(**common, from_node="alternative", to_node="route", edge_type="refutes")]


def rebased(events=None):
    return rebase_explore_knowledge_events(source_events() if events is None else events,
        source_goal_id="source", destination_goal_id="receiver", source_ref="checkpoint:" + "a" * 64)


def test_complete_knowledge_preserves_topology_scope_and_source_identity():
    source = source_events()
    # Persisted compatibility text is longer than a new writer's text budget.
    from loopx.capabilities.explore.result_log import _event_id
    source[2]["summary"] = "x" * 2002
    source[2]["event_id"] = _event_id(source[2])
    result = rebased(source)
    assert result == rebased(source) and source[2]["goal_id"] == "source"
    for old, new in zip(source, result):
        assert new == validate_explore_result_event(new, expected_goal_id="receiver")
        assert new["run_id"] == "checkpoint:" + "a" * 64 + ":" + old["event_id"]
        for key in old.keys() - {"goal_id", "run_id", "event_id", "result_id"}:
            assert new[key] == old[key]
        if old["event_kind"] != "edge":
            assert new["result_id"] == old["result_id"]
    expected_edge = build_explore_edge_event(goal_id="receiver", from_node="alternative", to_node="route", edge_type="refutes")
    assert result[-1]["result_id"] == expected_edge["result_id"] != source[-1]["result_id"]
    projection = build_explore_result_projection(result, goal_id="receiver")
    assert projection["findings"][0]["status"] == "refuted"
    assert projection["nodes"][0]["status"] == "dead_end"


def test_replay_strips_operational_research_binding_but_keeps_original_archive():
    from tests.capabilities.test_explore_research_evidence import observation
    source = build_explore_node_event(goal_id="source", node_id="a", title="Completed source experiment",
        status="resolved", research_observation=observation("a"))
    imported = rebased([source])
    assert "research_observation" not in imported[0]
    assert "research_observation" in source
    assert "research_frontier" not in build_explore_result_projection(imported, goal_id="receiver")


def test_invalid_source_is_rejected_before_any_replay():
    source = source_events()
    with pytest.raises(ValueError, match="different goal"):
        rebased([*source, build_explore_node_event(goal_id="foreign", title="Foreign")])
    with pytest.raises(ValueError, match="distinct"):
        rebase_explore_knowledge_events(source, source_goal_id="source", destination_goal_id="source", source_ref="checkpoint:one")
    assert rebased([*source, source[0]]) == rebased(source)


@pytest.mark.parametrize("existing", ["empty", "partial", "complete", "newer"])
def test_prefix_recovery_is_idempotent_and_never_reverts_newer_evidence(tmp_path, existing):
    path = tmp_path / "events.jsonl"
    imported = rebased()
    initial = [] if existing == "empty" else imported[:2] if existing == "partial" else imported
    newer = build_explore_node_event(goal_id="receiver", node_id="route", title="Retested route", status="open")
    if existing == "newer":
        initial = [*initial, newer]
    path.write_text("".join(json.dumps(row) + "\n" for row in initial))
    result = append_explore_result_events(path, imported, expected_goal_id="receiver", require_ordered_prefix=True)
    assert result["appended_event_count"] == len(imported) - min(len(initial), len(imported))
    expected = [*imported, newer] if existing == "newer" else imported
    assert load_explore_result_events_strict(path, goal_id="receiver") == expected
    before = path.read_bytes()
    assert append_explore_result_events(path, imported, expected_goal_id="receiver",
                                       require_ordered_prefix=True)["appended_event_count"] == 0
    assert path.read_bytes() == before


@pytest.mark.parametrize("damage", ["unrelated", "interleaved", "reordered", "truncated", "foreign", "invalid"])
def test_prefix_conflicts_fail_without_rewriting_any_destination_bytes(tmp_path, damage):
    path = tmp_path / "events.jsonl"
    imported = rebased()
    current = build_explore_node_event(goal_id="receiver", title="Current work")
    rows = {"unrelated": [current], "interleaved": [imported[0], current],
            "reordered": list(reversed(imported)), "truncated": imported,
            "foreign": source_events(), "invalid": [{"unexpected": True}]}[damage]
    raw = "".join(json.dumps(row) + "\n" for row in rows).encode()
    if damage == "truncated":
        raw = raw.rstrip(b"\n")
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        append_explore_result_events(path, imported, expected_goal_id="receiver", require_ordered_prefix=True)
    assert path.read_bytes() == raw


def test_ordinary_batch_append_does_not_acquire_prefix_semantics(tmp_path):
    path = tmp_path / "events.jsonl"
    current = build_explore_node_event(goal_id="receiver", title="Existing work")
    append_explore_result_event(path, current)
    append_explore_result_events(path, rebased(), expected_goal_id="receiver")
    assert load_explore_result_events_strict(path, goal_id="receiver") == [current, *rebased()]


@pytest.mark.parametrize("options", [{"require_ordered_prefix": 1},
    {"require_ordered_prefix": True, "create_only_node_ids": ["route"]}])
def test_prefix_options_cannot_silently_weaken_replay(tmp_path, options):
    path = tmp_path / "events.jsonl"
    with pytest.raises(ValueError):
        append_explore_result_events(path, rebased(), expected_goal_id="receiver", **options)
    assert not path.exists()
