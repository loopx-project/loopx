"""Production CLI -> durable store -> next-turn disclosure; no provider mocks."""
import json

from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, _run_cli, _write_fixture,
)


def test_explicit_correction_reaches_fresh_turn_without_changing_authority(tmp_path):
    _, runtime, registry = _write_fixture(tmp_path, required_capability="network")
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
    assert "semantic-preference agent read" in json.dumps(plan), plan
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
