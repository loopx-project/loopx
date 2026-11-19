"""Frozen pre-cutover completion observations; synthetic migration evidence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx.control_plane import effect_runtime
from loopx.control_plane.todos import durable_completion as completion

FIXTURE = (
    Path(__file__).parents[1] / "fixtures/control_plane/completion_evidence_read.json"
)
TODO = "todo_fixture0001"


def observe(case):
    kwargs = {
        "todo": case["todo"],
        "expected_todo_id": case.get("expected", TODO),
        "existing_todo_ids": case.get("existing"),
    }
    try:
        if case["operation"] == "terminal":
            result = completion.project_durable_terminal_completion_readback(
                **kwargs,
                expected_completion_turn_key="turn-a",
                projection_source="materialized",
            )
        elif case["operation"] == "intent":
            result = completion.project_durable_completion_intent(**kwargs)
        else:
            result = completion.project_durable_completion_outcome(**kwargs)
        return {"result": result}
    except ValueError as exc:
        return {"error_type": type(exc).__name__, "message": str(exc)}


@pytest.mark.parametrize(
    "case", json.loads(FIXTURE.read_text())["cases"], ids=lambda c: c["name"]
)
def test_complete_observation_bytes(case):
    assert (
        json.dumps(observe(case), ensure_ascii=False, separators=(",", ":"))
        == case["bytes"]
    )


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize(
    "operation", ["ordinary", "terminal", "intent", "post_completion", "legacy"]
)
def test_complete_read_crossings(tmp_path, monkeypatch, provider, operation):
    from loopx.control_plane.coordination.runtime_shadow import (
        build_todo_runtime_shadow_projection,
    )
    from tests.control_plane.canonical_authority_fixture import (
        initialize_canonical_authority,
        isolate_sqlite_runtime,
        read_fixture_todos,
    )

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    todo = {
        "todo_id": TODO,
        "status": "done",
        "role": "agent",
        "text": "Synthetic completed work",
        "completion_continuation": "no_followup"
        if operation == "terminal"
        else "active_goal",
        "completion_turn_key": "turn-a",
        "no_followup": operation == "terminal",
    }
    state = tmp_path / "ACTIVE_GOAL_STATE.md"
    state.write_text(
        "# Fixture\n\n## Agent Todo\n\n- [x] Synthetic completed work\n"
        "  <!-- loopx:todo todo_id=todo_fixture0001 status=done completion_continuation=active_goal -->\n"
    )
    todo = {**read_fixture_todos(state)[0], **todo}
    runtime = tmp_path / "runtime"
    if operation != "legacy":
        initialize_canonical_authority(
            runtime,
            "goal-fixture",
            build_todo_runtime_shadow_projection(
                goal_id="goal-fixture", todos=[todo], handoff_mode="soft_claim"
            ),
            state_path=state,
            provider=provider,
        )
        state.write_text("# Stale display with no Todo\n")
    calls = []
    original = effect_runtime.effect_runtime_request

    def counted(method, *args, **kwargs):
        calls.append(method)
        return original(method, *args, **kwargs)

    monkeypatch.setattr(effect_runtime, "effect_runtime_request", counted)
    result = completion.read_durable_completion_evidence(
        state,
        todo_id=TODO,
        runtime_root=runtime if operation != "legacy" else None,
        goal_id="goal-fixture",
        operation="outcome"
        if operation in {"ordinary", "post_completion", "legacy"}
        else operation,
        completion_turn_key="turn-a"
        if operation in {"ordinary", "terminal"}
        else None,
    )
    assert len(calls) == 1, calls
    expected = {
        "todo_id": TODO,
        "continuation": "no_followup" if operation == "terminal" else "active_goal",
    }
    assert result == (
        {"kind": "committed", "completion": expected}
        if operation == "terminal"
        else expected
    )


@pytest.mark.parametrize("malformed", [None, {}, {"completion": []}])
def test_complete_read_rejects_malformed_result(monkeypatch, malformed):
    monkeypatch.setattr(completion, "project_todo_read", lambda _: malformed)
    with pytest.raises(RuntimeError):
        completion.project_durable_completion_outcome(todo={}, expected_todo_id=TODO)


def test_complete_read_preserves_value_error(monkeypatch):
    def reject(_):
        raise effect_runtime.EffectRuntimeRejected("typed completion rejected")

    monkeypatch.setattr(completion, "project_todo_read", reject)
    with pytest.raises(ValueError, match="^typed completion rejected$"):
        completion.project_durable_completion_outcome(todo={}, expected_todo_id=TODO)


def test_canonical_refusal_never_falls_back_to_completed_markdown(tmp_path):
    from loopx.control_plane.coordination.local_authority import CanonicalAuthorityRequired

    state = tmp_path / "ACTIVE_GOAL_STATE.md"
    state.write_text(
        "# Fixture\n\n## Agent Todo\n\n- [x] Synthetic completed work\n"
        "  <!-- loopx:todo todo_id=todo_fixture0001 status=done completion_continuation=active_goal -->\n"
    )
    with pytest.raises(CanonicalAuthorityRequired):
        completion.read_durable_completion_evidence(
            state, todo_id=TODO, operation="outcome", goal_id="goal-fixture",
            runtime_root=tmp_path / "runtime",
        )


@pytest.mark.parametrize("container", [tuple, set])
def test_python_container_codec_preserves_frozen_output(container):
    case = next(
        case for case in json.loads(FIXTURE.read_text())["cases"]
        if case["name"] == "container-successors-['todo_aaa']"
    )
    case["todo"]["successor_todo_ids"] = container(["todo_aaa"])
    assert json.dumps(observe(case), ensure_ascii=False, separators=(",", ":")) == case["bytes"]
