"""Production CLI -> durable store -> next-turn disclosure; no provider mocks."""
import json
from copy import deepcopy

import pytest
from loopx.control_plane.turn_driver.host_candidate import extract_turn_authority, render_prompt

from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, _run_cli, _write_fixture,
)


def test_explicit_correction_reaches_fresh_turn_without_changing_authority(tmp_path):
    nested = tmp_path / ("workspace  dir" * 12)
    nested.mkdir()
    _, runtime, registry = _write_fixture(nested, required_capability="network")
    before = registry.read_bytes()
    scope = ("--goal-id", GOAL_ID, "--agent-id", AGENT_ID)
    def call(action, *args):
        return _run_cli(registry, runtime, "semantic-preference", "agent", action, *scope, *args)
    rc, empty = call("read")
    assert rc == 0 and empty["current"]["items"] == [], empty
    rc, off = _run_cli(registry, runtime, "quota", "should-run", *scope)
    assert not any(x["hook_id"] == "semantic_preference.agent_context"
                   for x in off["turn_start_capability_hook_dispatch"]["results"])
    args = ("--key", "review.collaboration", "--statement", "Ask designated-reviewer before merging.",
            "--source-ref", "owner-message-1", "--source-quote", "Please use the designated reviewer.",
            "--expected-revision", "none", "--operation-id", "remember-1")
    rc, preview = call("remember", *args)
    assert rc == 0 and preview["status"] == "preview", preview
    assert not (runtime / "agent-preferences").exists()
    rc, written = call("remember", *args, "--execute")
    assert rc == 0 and written["status"] == "applied", written
    rc, fresh = call("read")
    assert rc == 0 and fresh["current"]["items"][0]["statement"] == "Ask designated-reviewer before merging.", fresh
    rc, quota = _run_cli(registry, runtime, "quota", "should-run", *scope)
    assert rc == 0, quota
    reads = quota["turn_start_capability_hook_dispatch"]["required_reads"]
    assert any(x["kind"] == "agent_preferences" for x in reads), quota["turn_start_capability_hook_dispatch"]
    assert "designated-reviewer" not in json.dumps(quota)
    assert quota["capability_gate"]["action"] == off["capability_gate"]["action"]
    rc, plan = _run_cli(registry, runtime, "turn", "plan", *scope)
    assert rc == 0, plan
    preference_read = next(x for x in reads if x["kind"] == "agent_preferences")
    assert len(preference_read["command"]) > 360
    assert "semantic-preference agent read" in json.dumps(plan), plan
    assert any(x["command"] == preference_read["command"]
               for x in plan["turn_envelope"]["required_reads"]), plan
    rc, corrected = call("remember", "--key", "review.collaboration", "--statement", "Do not delegate review.",
        "--source-ref", "owner-message-2", "--source-quote", "Stop asking a reviewer.",
        "--expected-revision", fresh["current"]["revision"], "--operation-id", "correct-2", "--execute")
    assert rc == 0 and corrected["status"] == "applied", corrected
    rc, replay = call("remember", *args, "--execute")
    assert rc == 0 and replay["status"] == "replayed", replay
    assert replay["current"]["items"][0]["statement"] == "Do not delegate review."
    rc, retired = call("retire", "--key", "review.collaboration", "--source-ref", "owner-message-3",
        "--source-quote", "Forget that preference.", "--expected-revision", corrected["current"]["revision"],
        "--operation-id", "retire-3", "--execute")
    assert rc == 0 and retired["current"]["items"][0]["state"] == "retired", retired
    rc, later = call("read")
    assert rc == 0 and later["current"]["items"][0]["statement"] is None, later
    rc, quota = _run_cli(registry, runtime, "quota", "should-run", *scope)
    assert any(x["kind"] == "agent_preferences" for x in quota["turn_start_capability_hook_dispatch"]["required_reads"])
    rc, history = call("history")
    assert rc == 0 and len(history["events"]) == 3, history
    alias = tmp_path / "global-registry.json"
    alias.write_bytes(registry.read_bytes())
    rc, aliased = _run_cli(alias, runtime, "semantic-preference", "agent", "read", *scope)
    assert rc == 0 and aliased["current"] == later["current"], aliased
    assert registry.read_bytes() == before


def test_invalid_requests_and_corrupt_storage_do_not_become_empty_memory(tmp_path):
    _, runtime, registry = _write_fixture(tmp_path, required_capability="network")
    scope = ("--goal-id", GOAL_ID, "--agent-id", AGENT_ID)
    def call(action, *args):
        return _run_cli(registry, runtime, "semantic-preference", "agent", action, *scope, *args)
    rc, bad = call("remember", "--key", "review")
    assert rc != 0 and bad["ok"] is False
    rc, first = call("remember", "--key", "review", "--statement", "Use a reviewer.",
        "--expected-revision", "none", "--operation-id", "one", "--source-ref", "user-1",
        "--source-quote", "Use a reviewer.", "--execute")
    assert rc == 0, first
    rc, healthy_plan = _run_cli(registry, runtime, "turn", "plan", *scope)
    assert rc == 0, healthy_plan
    healthy = extract_turn_authority(healthy_plan)
    assert "unavailable_context" not in healthy
    store = next((runtime / "agent-preferences").glob("*/authority-store-*.json"))
    store.write_text("{broken")
    rc, failed = call("read")
    assert rc != 0 and failed["status"] == "unavailable", failed
    rc, quota = _run_cli(registry, runtime, "quota", "should-run", *scope)
    assert rc == 0, quota
    result = next(x for x in quota["turn_start_capability_hook_dispatch"]["results"]
                  if x["hook_id"] == "semantic_preference.agent_context")
    assert result["status"] == "unavailable"
    assert store.read_text() == "{broken"

    rc, failed_plan = _run_cli(registry, runtime, "turn", "plan", *scope)
    assert rc == 0, failed_plan
    authority = extract_turn_authority(failed_plan)
    unavailable = authority["unavailable_context"]
    assert unavailable["affected_hooks"] == [{
        "hook_id": "semantic_preference.agent_context", "capability_id": "semantic-preference",
        "status": "unavailable", "error_code": "agent_preferences_unreadable",
    }]
    assert unavailable["cache_policy"] == "invalidate_affected_hook_context"
    assert unavailable["dependent_action_policy"] == "hold_until_fresh_context"
    assert unavailable["independent_work_policy"] == "preserve_existing_authority"
    assert authority["primary_action"] == healthy["primary_action"]
    assert authority["write_scope"] == healthy["write_scope"]
    assert authority["required_reads"] == []
    prompt = render_prompt(authority)
    assert "discard cached context" in prompt and "Independent work may" in prompt
    assert "Use a reviewer." not in prompt and "{broken" not in prompt
    rc, quota_envelope = _run_cli(registry, runtime, "quota", "should-run", *scope, "--turn-envelope")
    assert rc == 0, quota_envelope
    assert quota_envelope["contract_capsule"]["unavailable_context"] == unavailable
    changed = deepcopy(failed_plan)
    del changed["turn_envelope"]["contract_capsule"]["unavailable_context"]
    with pytest.raises(ValueError, match="signature"):
        extract_turn_authority(changed)
