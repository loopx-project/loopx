"""Kill real native appends and inspect original identities on both providers."""
from __future__ import annotations

import json

import pytest

from loopx.control_plane.goals import checkpoint_context_io as context_io
from loopx.control_plane.quota.settlement import SettlementIdentity
from tests.control_plane.checkpoint_process import refresh, start_probe
from tests.control_plane.test_checkpoint_provider_fence import fixture
from tests.control_plane.test_quota_settlement_cli import GOAL_ID, AGENT_ID, TODO_ID, _spend_run_count


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("fault", ["before_artifacts", "after_json", "after_markdown", "after_index"])
def test_original_direction_readback_distinguishes_torn_from_committed(tmp_path, monkeypatch, provider, fault):
    _, runtime, registry, _, read, _ = fixture(tmp_path, monkeypatch, provider, first_delivery=True)
    token = read()["read_context_id"]
    barrier = tmp_path / "fault"
    barrier.mkdir()
    (barrier / "release").touch()
    original = context_io.effect_runtime_result

    def native(method, params, **kwargs):
        if method != "goal.checkpoint_read_context.commit":
            return original(method, params, **kwargs)
        child = start_probe({"mode": "checkpoint", "provider": provider, "barrier": str(barrier),
            "params": params, "fault": fault})
        stdout, stderr = child.communicate(timeout=25)
        assert child.returncode == 86, stdout + stderr
        raise OSError("injected native response loss")

    monkeypatch.setattr(context_io, "effect_runtime_result", native)
    with pytest.raises(OSError, match="injected"):
        refresh(registry, runtime, token, first_delivery=True)
    monkeypatch.setattr(context_io, "effect_runtime_result", original)
    if fault == "before_artifacts":
        observed = read()
        assert observed["read_context_id"] != token
        saved = refresh(registry, runtime, observed["read_context_id"], first_delivery=True)
        assert saved["appended"]
    elif fault == "after_index":
        observed = read()
        assert observed["status"] == "committed" and observed["replayed"]
        replay = refresh(registry, runtime, token, first_delivery=True)
        assert replay["appended"] is False
        assert replay["json_path"] == observed["json_path"]
    else:
        for action in (read, lambda: refresh(registry, runtime, token, first_delivery=True)):
            with pytest.raises(context_io.CheckpointReadContextRejected) as rejected:
                action()
            assert rejected.value.code == "checkpoint_commit_unknown"
        # A new Turn identity cannot bypass an unresolved append for the same
        # Agent/Todo. This check precedes any new admission or token issuance.
        identity = SettlementIdentity(goal_id=GOAL_ID, agent_id=AGENT_ID,
            todo_id=TODO_ID, turn_instance_id="different-turn")
        with pytest.raises(context_io.CheckpointReadContextRejected) as rejected:
            context_io._checkpoint_effect("goal.checkpoint_read_context.inspect_attempt", {
                "runtime_root": str(runtime.resolve()), "identity": identity.as_dict(),
                "check_other_attempts": True,
            })
        assert rejected.value.code == "checkpoint_commit_unknown"
    rows = [json.loads(line) for line in (runtime / "goals" / GOAL_ID / "runs/index.jsonl").read_text().splitlines()]
    assert sum(bool(row.get("vision_checkpoint", {}).get("read_context")) for row in rows) == (fault in {"after_index", "before_artifacts"})
    assert _spend_run_count(runtime) == 0
