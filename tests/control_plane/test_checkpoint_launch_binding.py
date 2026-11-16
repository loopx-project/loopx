"""Launch witness lifetime through the shared adapter and Codex binding."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from loopx.cli_commands.turn_host_request import bind_turn_host_admission, build_codex_cli_host_runner
from loopx.control_plane.goals.first_party_host_admission import FirstPartyHostGoalAdmission, FirstPartyHostRuntimeRejected


def _fixture(tmp_path):
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"schema_version": "0.2", "goals": [{"id": "launch-goal", "status": "active"}]}))
    original = FirstPartyHostGoalAdmission.for_plan(registry_path=registry,
        goal_id="launch-goal", planned_goal_ref=None)
    payload = json.loads(registry.read_text())
    payload["goals"][0]["display_name"] = "A new synthetic label"
    registry.write_text(json.dumps(payload))
    return registry, original


def _decision(_registry, **_context):
    # Adapter isolation only: real quota/source validation is exercised by the
    # Native integration matrix; this is its already-checked launch result.
    return {"current": {"should_run": True, "status_health_ok": True, "effective_action": "normal_run"},
        "planned_effective_action": "normal_run", "refresh_launch_witness": True}


@pytest.mark.parametrize("phase", ["work", "historical-direction", "new-direction"])
def test_only_verified_new_direction_replaces_a_business_witness(tmp_path, phase):
    registry, original = _fixture(tmp_path)
    context = {"read_context_id": "original-direction", "purpose": "first_delivery"}
    request = {} if phase == "work" else {"phase": "checkpoint_direction", "checkpoint_context": context}
    def checked(path, **kw):
        return {**_decision(path, **kw), "refresh_launch_witness": phase == "new-direction"}
    admission = bind_turn_host_admission(request, goal_admission=original, registry_path=registry,
        goal_id="launch-goal", goal_ref=None, current_launch_decision=checked)
    if phase == "new-direction":
        with admission.launch_lifetime(operation="test_direction"):
            pass
    else:
        with pytest.raises(FirstPartyHostRuntimeRejected, match="stale_goal_authority"):
            with admission.launch_lifetime(operation="test_original"):
                pytest.fail("unchanged historical/work witness must reject")


def test_codex_binding_checks_after_capture_and_does_not_label_late_errors_absent(tmp_path):
    registry, original = _fixture(tmp_path)
    contexts = []
    def checked(path, **kw):
        contexts.append(kw)
        return _decision(path, **kw)
    def host(request, **options):
        admission = options["goal_admission"]
        with admission.launch_lifetime(operation="test_codex_direction"):
            pass
        late = FirstPartyHostRuntimeRejected("stale_goal_authority")
        with pytest.raises(FirstPartyHostRuntimeRejected):
            with admission.launch_lifetime(operation="test_late_error"):
                raise late
        assert late.launch_not_started is False
        payload = json.loads(registry.read_text())
        payload["goals"][0]["display_name"] = "Changed after capture"
        registry.write_text(json.dumps(payload))
        with pytest.raises(FirstPartyHostRuntimeRejected) as refused:
            with admission.launch_lifetime(operation="test_stale_direction"):
                pytest.fail("after-capture changes cannot launch")
        assert refused.value.launch_not_started is True
        return {"ok": True}
    args = SimpleNamespace(goal_id="launch-goal", codex_bin="codex", codex_sandbox=None,
        codex_model=None, codex_reasoning_effort=None, codex_mcp_server_json=None, timeout_seconds=None)
    runner = build_codex_cli_host_runner(args, runtime_root=tmp_path / "runtime", project=tmp_path,
        registry_path=registry, goal_ref=None, goal_admission=original,
        current_launch_decision=checked, run_codex_cli_host=host)
    context = {"read_context_id": "original-direction", "purpose": "first_delivery"}
    assert runner({"phase": "checkpoint_direction", "checkpoint_context": context})["ok"]
    assert all(c["checkpoint_direction"] is True and c["checkpoint_context"] == context for c in contexts)
