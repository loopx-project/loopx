"""Real compatibility sources: complete overlays and their mutation locks."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx.control_plane.todos.handoff_mode import HandoffModeError, set_goal_handoff_mode
from loopx.control_plane.goals.legacy_event_source import RetiredTodoEventSourceError


def workspace(root: Path) -> tuple[Path, Path, Path]:
    state = root / "ACTIVE_GOAL_STATE.md"
    state.write_text("---\ngoal_id: mode-source\nhandoff_mode: legacy\n---\n\n## Agent Todo\n", encoding="utf-8")
    registry = root / "registry.json"
    runtime = root / "runtime"
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{"id": "mode-source",
        "repo": str(root), "state_file": state.name, "adapter": {"kind": "harness_self_improvement"}}]}))
    return registry, state, runtime




def switch(registry: Path, **kwargs):
    return set_goal_handoff_mode(registry_path=registry, goal_id="mode-source", mode="hard_lease", **kwargs)






@pytest.mark.parametrize("contents", ["not json\n", '{"schema_version":"unknown"}\n'])
def test_unreadable_event_source_is_not_quiescence(tmp_path: Path, contents: str) -> None:
    registry, state, _ = workspace(tmp_path)
    path = state.with_name("events.jsonl")
    path.write_text(contents)
    before = state.read_bytes()
    with pytest.raises(RetiredTodoEventSourceError):
        switch(registry)
    assert state.read_bytes() == before




def test_retired_source_refuses_even_a_mode_noop(tmp_path: Path) -> None:
    registry, state, _ = workspace(tmp_path)
    state.write_text(state.read_text().replace("legacy", "hard_lease"))
    state.with_name("events.jsonl").write_text("corrupt unrelated event\n")
    before = state.read_bytes()
    with pytest.raises(RetiredTodoEventSourceError):
        switch(registry)
    assert state.read_bytes() == before


@pytest.mark.parametrize("ending", [b"", b"\r\n"])
def test_public_mode_write_preserves_unrelated_bytes(tmp_path: Path, ending: bytes) -> None:
    registry, state, _ = workspace(tmp_path)
    source = '---\r\ngoal_id: mode-source\r\ntitle: "a\u2028b"\r\nhandoff_mode: legacy\r\n---\r\n\r\n## Agent Todo'.encode() + ending
    state.write_bytes(source)
    assert switch(registry)["changed"] is True
    assert state.read_bytes() == source.replace(b"handoff_mode: legacy", b"handoff_mode: hard_lease")


def test_duplicate_mode_fields_reject_without_partial_repair(tmp_path: Path) -> None:
    registry, state, _ = workspace(tmp_path)
    state.write_text(state.read_text().replace("handoff_mode: legacy", "handoff_mode: legacy\nhandoff_mode: banana"))
    before = state.read_bytes()
    with pytest.raises(HandoffModeError) as caught:
        switch(registry)
    assert caught.value.code == "handoff_mode_duplicate_field"
    assert state.read_bytes() == before


def test_large_prose_never_crosses_the_mode_plan_transport(tmp_path: Path) -> None:
    registry, state, _ = workspace(tmp_path)
    original = state.read_bytes() + b"\n## Evidence\n" + b"Unrelated durable prose.\n" * 150_000
    state.write_bytes(original)
    assert switch(registry)["changed"] is True
    assert state.read_bytes() == original.replace(b"handoff_mode: legacy", b"handoff_mode: hard_lease")
