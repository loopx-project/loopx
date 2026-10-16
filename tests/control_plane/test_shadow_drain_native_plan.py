"""The retained Python drain adapter stays inert when no history exists."""
from __future__ import annotations

import json

from loopx.control_plane.coordination import local_authority_shadow_adapter as adapter


def test_disabled_without_history_does_not_start_native_runtime(
    tmp_path,
    monkeypatch,
):
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"goals": [{"id": "goal-e2e"}]}))

    def unexpected(*args, **kwargs):
        raise AssertionError("feature-off drain must not invoke the runtime")

    monkeypatch.setattr(adapter, "effect_runtime_result", unexpected)
    result = adapter.drain_local_authority_shadow_outbox(
        registry_path=registry,
        runtime_root=tmp_path / "runtime",
        goal_id="goal-e2e",
        max_entries=20,
        budget_seconds=10,
    )
    assert result.ok
    assert result.outcome == "nothing_pending"
    assert not (tmp_path / "runtime").exists()
