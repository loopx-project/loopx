"""Admission validates its source without transporting the unused Todo body."""
from pathlib import Path

import pytest

from loopx.control_plane import effect_runtime
from loopx.control_plane.coordination.local_authority import CanonicalAuthorityRequired
from loopx.control_plane.goals.checkpoint_context_io import default_result_freshness_policy
from tests.control_plane.test_canonical_snapshot_integration import native_read, wide_goal as wide_goal


def test_admission_policy_keeps_wide_source_on_typed_owner(wide_goal, monkeypatch):
    runtime, _state, _registry, projection = wide_goal
    before = native_read(runtime)
    calls = []
    actual = effect_runtime._request_with_info

    def capture(info, **kwargs):
        calls.append((kwargs["method"], kwargs.get("large_local_snapshot", False)))
        return actual(info, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(effect_runtime, "_request_with_info", capture)
        policy = default_result_freshness_policy(runtime, "goal-a", "todo_000")
    assert policy == "turn_result_freshness_required_v1"
    assert calls == [("goal.checkpoint_read_context.work_binding", False)]
    after = native_read(runtime)
    assert after["todos"] == before["todos"] == projection["todos"]
    assert after["provider_revision"] == before["provider_revision"]
    # A supplied authority hint cannot replace validation of the real source.
    assert effect_runtime.effect_runtime_result("goal.checkpoint_read_context.work_binding", {
        "phase": "admission", "runtime_root": str(runtime), "goal_id": "goal-a",
        "todo_id": "todo_000", "goal_ref": None, "source_authority": "untrusted_hint",
    }) == {"policy": "turn_result_freshness_required_v1"}


def test_failed_admission_keeps_canonical_authority_error(wide_goal):
    runtime, _state, _registry, _projection = wide_goal
    with pytest.raises(CanonicalAuthorityRequired) as failure:
        default_result_freshness_policy(Path(runtime), "unmigrated-goal", "todo_000")
    assert failure.value.code == "canonical_authority_required"
    assert failure.value.payload["operation"] == "todo_read"
    assert failure.value.payload["write_admitted"] is False
    assert failure.value.payload["legacy_fallback_used"] is False
