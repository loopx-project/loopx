"""Complete replan facts use the private transport, never a display window."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from loopx.control_plane import effect_runtime
from loopx.control_plane.work_items.replan_semantics_codec import project_replan_semantics


@pytest.fixture
def isolated_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    for name in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(name, str(tmp_path))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    yield tmp_path
    effect_runtime.effect_runtime_result("runtime.shutdown", {}, retry_safe=False)


@pytest.mark.parametrize("large", [False, True])
def test_lifecycle_source_preserves_complete_input_and_output(isolated_runtime: Path, large: bool):
    item = {
        "todo_id": "todo_accepted_source", "index": 1,
        "status": "done", "done": True, "task_class": "advancement_task",
        "claimed_by": "fixture-agent", "last_actor_agent_id": "fixture-agent",
        "completed_at": "2026-08-01T00:00:00Z", "updated_at": "2026-08-01T00:00:00Z",
        "completion_turn_key": "original-completion",
        "no_followup": True, "completion_continuation": "no_followup",
        "completion_recovery": "lifecycle_reentry_terminal_closeout",
        "note": "x" * (effect_runtime.MAX_REQUEST_BYTES + 128) if large else "Accepted source.",
    }
    params = {"operation": "lifecycle_source", "agent_id": "fixture-agent",
        "guard_recorded_at": "2026-08-01T00:00:00.500Z", "items": [item]}
    if large:
        # Registration is not a global bypass: ordinary requests still fail.
        with pytest.raises(effect_runtime.EffectRuntimeRejected, match="request is oversized"):
            effect_runtime.effect_runtime_result("work_item.replan_semantics.project", params)
    else:
        assert effect_runtime.effect_runtime_result("work_item.replan_semantics.project", params) == {
            "items": [item], "settled": True,
        }
    assert project_replan_semantics(params) == {"items": [item], "settled": True}
    assert list(isolated_runtime.glob("loopx-effect-*/params.json")) == []
    assert list(isolated_runtime.glob("loopx-effect-*/response.json")) == []


def test_rejected_large_projection_removes_private_files(isolated_runtime: Path):
    with pytest.raises(effect_runtime.EffectRuntimeRejected):
        project_replan_semantics({"operation": "unsupported",
            "items": [{"note": "x" * (effect_runtime.MAX_REQUEST_BYTES + 128)}]})
    assert list(isolated_runtime.glob("loopx-effect-*/params.json")) == []
    assert list(isolated_runtime.glob("loopx-effect-*/response.json")) == []
    assert effect_runtime.effect_runtime_result("runtime.ping", {})["ready"] is True
