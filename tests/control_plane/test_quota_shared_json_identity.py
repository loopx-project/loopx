"""Same-packet sharing must retain distinct JSON facts and complete instructions."""
from __future__ import annotations

import json
import math
from copy import deepcopy

import pytest

from loopx.control_plane.quota.cli_projection import compact_quota_should_run_cli_payload


INSTRUCTIONS = "Preserve every original requirement.\nKeep failure evidence and stop before publication. 完整来源。"
FAMILIES = ("frontier", "agent", "cli", "authoring")


def _packet(family, left, right, *, reverse=False):
    first = {"instructions": INSTRUCTIONS, "extension": {"observation": left}}
    second = {"instructions": INSTRUCTIONS, "extension": {"observation": right}}
    if reverse:
        second = dict(reversed(second.items()))
    if family == "authoring":
        return {"replan_action_packet": {"writeback_contract": {"vision_authoring": first}},
            "interaction_contract": {"cli_channel": {"settlement_plan": {
                "ordered_steps": [{"kind": "durable_writeback", "vision_authoring": second}]}}}}
    if family == "frontier":
        mirror = {"goal_frontier_projection": {"vision_continuation_audit": second}}
    else:
        mirror = {"interaction_contract": {family + "_channel": {"vision_continuation_audit": second}}}
    return {"vision_continuation_audit": first, **mirror}


def _mirror_parent(packet, family):
    if family == "authoring":
        return packet["replan_action_packet"]["writeback_contract"], "vision_authoring"
    if family == "frontier":
        return packet["goal_frontier_projection"], "vision_continuation_audit"
    return packet["interaction_contract"][family + "_channel"], "vision_continuation_audit"


def _assert_observation(body, expected):
    assert body["instructions"] == INSTRUCTIONS
    actual = body["extension"]["observation"]
    _assert_scalar(actual, expected)


def _assert_scalar(actual, expected):
    assert type(actual) is type(expected)
    assert actual == expected
    if isinstance(expected, float) and expected == 0:
        assert math.copysign(1, actual) == math.copysign(1, expected)


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("left,right", [(True, 1), (False, 0), (1, 1.0), (-0.0, 0.0)],
    ids=["boolean-one", "boolean-zero", "integer-float", "signed-zero"])
def test_sharing_keeps_distinct_json_values(family, left, right):
    source = _packet(family, left, right)
    original = json.dumps(source, ensure_ascii=False)
    result = compact_quota_should_run_cli_payload(source)
    parent, field = _mirror_parent(result, family)
    assert field in parent, "Distinct source facts must remain inline instead of aliasing another value"
    assert field + "_ref" not in parent
    assert "projection_ref" not in parent[field]
    _assert_observation(parent[field], left if family == "authoring" else right)
    if family == "authoring":
        owner = result["interaction_contract"]["cli_channel"]["settlement_plan"]["ordered_steps"][0]["vision_authoring"]
        _assert_observation(owner, right)
    else:
        _assert_observation(result["vision_continuation_audit"], left)
    assert json.dumps(source, ensure_ascii=False) == original
    assert compact_quota_should_run_cli_payload(result) == result
    detailed = compact_quota_should_run_cli_payload(source, include_vision_detail=True)
    parent, field = _mirror_parent(detailed, family)
    _assert_observation(parent[field], left if family == "authoring" else right)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("native", [False, True], ids=["ordinary", "native"])
def test_real_guard_and_recovery_keep_distinct_vision_observations(tmp_path, monkeypatch, capsys, provider, native):
    """Keep different optional source facts through the real CLI and capture.

    The sole producer injection adds a boolean/integer extension after real
    admission. No quota, capture, projection, reader or receipt is mocked.
    """
    import loopx.cli_commands.quota as quota
    from loopx.cli import main
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
    from tests.control_plane import test_quota_settlement_cli as fixture

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = fixture._write_fixture(tmp_path, provider=provider)
    monkeypatch.chdir(project)
    monkeypatch.setenv("CODEX_THREAD_ID", "json-sharing-fixture")
    monkeypatch.delenv("CODEX_ROLLOUT_TRACE_ROOT", raising=False)
    prefix = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json"]

    def call(*args):
        code = main([*prefix, *args])
        return code, json.loads(capsys.readouterr().out)

    code, vision = call("refresh-state", "--goal-id", fixture.GOAL_ID,
        "--agent-id", fixture.AGENT_ID, "--vision-state", "vision_patch_proposed",
        "--vision-summary", "Preserve distinct source observations.",
        "--vision-acceptance", "Complete original requirements and retain evidence types.",
        "--no-global-sync")
    assert code == 0 and vision["ok"], vision
    state = project / f".codex/goals/{fixture.GOAL_ID}/ACTIVE_GOAL_STATE.md"
    before_state = state.read_bytes()
    original_emit, observations = quota._emit_quota_result, []

    def emit_with_distinct_sources(payload, args, **kwargs):
        audit = deepcopy(payload["vision_continuation_audit"])
        audit["sharing_fixture"] = {"observation": True}
        payload["vision_continuation_audit"] = audit
        other = deepcopy(audit)
        other["sharing_fixture"]["observation"] = 1
        payload["goal_frontier_projection"]["vision_continuation_audit"] = deepcopy(other)
        for channel in ("agent_channel", "cli_channel"):
            payload["interaction_contract"][channel]["vision_continuation_audit"] = deepcopy(other)
        observations.append(deepcopy(payload))
        return original_emit(payload, args, **kwargs)

    monkeypatch.setattr(quota, "_emit_quota_result", emit_with_distinct_sources)
    capture = tmp_path / "capture"
    args = ["quota", "should-run", "--goal-id", fixture.GOAL_ID,
        "--agent-id", fixture.AGENT_ID, "--todo-id", fixture.TODO_ID,
        "--turn-instance-id", "source-sharing-turn", "--codex-app",
        "--decision-output-dir", str(capture)]
    if native:
        context = tmp_path / "native-context"
        context.mkdir(mode=0o700)
        args += ["--context-delivery", "on", "--codex-context-dir", str(context)]
    code, emitted = call(*args)
    assert code == 0 and emitted["ok"] and len(observations) == 1, emitted
    original_bytes = (capture / "decision.json").read_bytes()
    original = json.loads(original_bytes)
    before_receipts = fixture._heartbeat_receipt_count(runtime, "source-sharing-turn")
    assert before_receipts == 1 and fixture._spend_run_count(runtime) == 0
    if native:
        from pathlib import Path

        recovery = emitted["recovery"]
        view = json.loads(Path(recovery["path"]).read_bytes())
        pointers = ["/vision_continuation_audit/sharing_fixture",
            "/goal_frontier_projection/vision_continuation_audit/sharing_fixture",
            "/interaction_contract/agent_channel/vision_continuation_audit/sharing_fixture",
            "/interaction_contract/cli_channel/vision_continuation_audit/sharing_fixture"]
        selects = [arg for pointer in pointers for arg in ("--select", pointer)]
        code, page = call("quota", "context-read", "--capture-file", recovery["path"],
            "--capture-sha256", recovery["sha256"], *selects)
        assert code == 0 and page["ok"] and page["eof"], page
        values = {row["pointer"]: row["value"] for row in json.loads(page["content"])["selections"]}
        for pointer in pointers:
            _assert_scalar(values[pointer]["observation"], True if pointer == pointers[0] else 1)
        code, refused = call("quota", "context-read", "--capture-file", recovery["path"],
            "--capture-sha256", "0" * 64, *selects)
        assert code == 1 and refused["ok"] is False
    else:
        view = emitted
    _assert_scalar(view["vision_continuation_audit"]["sharing_fixture"]["observation"], True)
    for family in ("frontier", "agent", "cli"):
        parent, field = _mirror_parent(view, family)
        assert "projection_ref" not in parent[field]
        _assert_scalar(parent[field]["sharing_fixture"]["observation"], 1)
        expected = original["vision_continuation_audit"] | {"sharing_fixture": {"observation": 1}}
        assert parent[field] == expected
    assert original["heartbeat_receipt"] == observations[0]["heartbeat_receipt"]
    assert (capture / "decision.json").read_bytes() == original_bytes
    assert fixture._heartbeat_receipt_count(runtime, "source-sharing-turn") == before_receipts
    assert fixture._spend_run_count(runtime) == 0
    assert state.read_bytes() == before_state


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("value", [None, True, 1, 1.0, -0.0])
def test_sharing_still_reuses_equal_json_with_different_key_order(family, value):
    source = _packet(family, value, deepcopy(value), reverse=True)
    original = json.dumps(source, ensure_ascii=False)
    result = compact_quota_should_run_cli_payload(source)
    parent, field = _mirror_parent(result, family)
    if family == "authoring":
        assert field not in parent
        assert parent[field + "_ref"] == (
            "$.interaction_contract.cli_channel.settlement_plan.ordered_steps[0].vision_authoring")
        owner = result["interaction_contract"]["cli_channel"]["settlement_plan"]["ordered_steps"][0][field]
    else:
        assert parent[field]["projection_ref"] == "$.vision_continuation_audit"
        owner = result["vision_continuation_audit"]
    _assert_observation(owner, value)
    assert json.dumps(source, ensure_ascii=False) == original
    assert compact_quota_should_run_cli_payload(result) == result
