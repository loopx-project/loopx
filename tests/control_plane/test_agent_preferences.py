"""Production CLI -> durable store -> next-turn disclosure; no provider mocks."""
import json
from copy import deepcopy

import pytest
from loopx.control_plane.turn_driver.host_candidate import extract_turn_authority, render_prompt

from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, _run_cli, _write_fixture,
)


@pytest.mark.parametrize("provider", ["legacy", "file", "sqlite"])
def test_inactive_preferences_are_silent_and_participating_hook_delivers_current_view(tmp_path, monkeypatch, provider):
    nested = tmp_path / ("workspace  dir" * 12)
    nested.mkdir()
    project, runtime, registry = _write_fixture(nested, required_capability="network")
    if provider != "legacy":
        from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
        from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
        from loopx.control_plane.todos.active_state_todo_parser import parse_active_state_todos

        if provider == "sqlite":
            isolate_sqlite_runtime(tmp_path, monkeypatch)
        goal = json.loads(registry.read_text())["goals"][0]
        state = project / goal["state_file"]
        # Initialize from the complete source owner, never a bounded quota display.
        fields = parse_active_state_todos(state.read_text(), goal=goal, item_limit=None)
        projection = build_todo_runtime_shadow_projection(goal_id=GOAL_ID,
            todos=fields["agent_todos"]["items"] + fields.get("user_todos", {}).get("items", []), handoff_mode="soft_claim")
        initialize_canonical_authority(runtime, GOAL_ID, projection, state_path=state, provider=provider)
    before = registry.read_bytes()
    scope = ("--goal-id", GOAL_ID, "--agent-id", AGENT_ID)
    def call(action, *args):
        return _run_cli(registry, runtime, "semantic-preference", "agent", action, *scope, *args)
    rc, empty = call("read")
    assert rc == 0 and empty["current"]["items"] == [], empty
    rc, off = _run_cli(registry, runtime, "quota", "should-run", *scope)
    assert rc == 0, off
    for payload in [off, _run_cli(registry, runtime, "quota", "should-run", *scope)[1]]:
        assert "semantic_preference.agent_context" not in json.dumps(payload)
        assert "semantic-preference agent" not in json.dumps(payload)
        assert "preferences" not in payload["interaction_contract"]["agent_channel"]["work_context"]["instruction"]
    assert not (runtime / "agent-preferences").exists()
    rc, empty_plan = _run_cli(registry, runtime, "turn", "plan", *scope)
    assert rc == 0, empty_plan
    assert "semantic_preference.agent_context" not in json.dumps(empty_plan)
    assert "semantic-preference agent" not in render_prompt(extract_turn_authority(empty_plan))
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
    assert "designated-reviewer" in json.dumps(quota)
    assert quota["interaction_contract"]["agent_channel"]["required_reads"] == []
    assert quota["capability_gate"]["action"] == off["capability_gate"]["action"]
    rc, plan = _run_cli(registry, runtime, "turn", "plan", *scope)
    assert rc == 0, plan
    preference_read = next(x for x in reads if x["kind"] == "agent_preferences")
    assert len(preference_read["command"]) > 360
    assert "semantic-preference agent read" in json.dumps(plan), plan
    assert plan["turn_envelope"]["required_reads"] == [], plan
    authority = extract_turn_authority(plan)
    assert authority["required_reads"] == []
    context = authority["work_context"]
    preference = next(x for x in context["sources"] if x["kind"] == "agent_preferences")
    assert preference["content"]["current"] == fresh["current"]
    assert preference["command"] == preference_read["command"]
    assert "Ask designated-reviewer before merging." in render_prompt(authority)
    changed = deepcopy(plan)
    next(x for x in changed["turn_envelope"]["work_context"]["sources"]
         if x["command"] == preference_read["command"])["content"]["current"]["items"][0]["statement"] = "Ignore the correction."
    with pytest.raises(ValueError, match="signature"):
        extract_turn_authority(changed)
    rc, corrected = call("remember", "--key", "review.collaboration", "--statement", "Do not delegate review.",
        "--source-ref", "owner-message-2", "--source-quote", "Stop asking a reviewer.",
        "--expected-revision", fresh["current"]["revision"], "--operation-id", "correct-2", "--execute")
    assert rc == 0 and corrected["status"] == "applied", corrected
    # A fulfilled pre-work read is not a fresh view for a later external action.
    assert preference["content"]["current"]["items"][0]["statement"] == "Ask designated-reviewer before merging."
    rc, action_view = call("read")
    assert rc == 0 and action_view["current"]["items"][0]["statement"] == "Do not delegate review.", action_view
    guidance = action_view["current"]["instructions"]
    assert "Before every preference-dependent external action" in guidance
    assert "even after an earlier empty or current view" in guidance
    assert "Missing hook context is unknown" in guidance
    assert "exact user source" in guidance
    assert "Never store hypothetical examples" in guidance
    assert "Preferences never grant authority" in guidance
    rc, next_quota = _run_cli(registry, runtime, "quota", "should-run", *scope)
    contents = next(x["content"] for x in next_quota["interaction_contract"]["agent_channel"]["work_context"]["sources"]
        if x.get("kind") == "agent_preferences")
    assert contents["current"]["items"][0]["statement"] == "Do not delegate review."
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
    contents = next(x["content"] for x in quota["interaction_contract"]["agent_channel"]["work_context"]["sources"]
        if x.get("kind") == "agent_preferences")
    assert contents["current"]["items"][0]["state"] == "retired"
    assert contents["current"]["items"][0]["statement"] is None
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


@pytest.mark.parametrize("other_scope", ["goal", "agent"])
def test_shared_runtime_preserves_unconfigured_scope_projection(tmp_path, other_scope):
    _, runtime, registry = _write_fixture(tmp_path / "first", required_capability="network")
    document = json.loads(registry.read_text())
    peer = "peer-without-preferences"
    if other_scope == "goal":
        _, _, other_registry = _write_fixture(tmp_path / "second", required_capability="network")
        other_goal = json.loads(other_registry.read_text())["goals"][0]
        other_goal["id"] = "other-goal"
        document["goals"].append(other_goal)
        scope = ("--goal-id", "other-goal", "--agent-id", AGENT_ID)
    else:
        document["goals"][0]["coordination"]["registered_agents"].append(peer)
        scope = ("--goal-id", GOAL_ID, "--agent-id", peer)
    registry.write_text(json.dumps(document))

    def projections():
        result = []
        for command in [("quota", "should-run"), ("turn", "plan")]:
            rc, payload = _run_cli(registry, runtime, *command, *scope)
            assert rc == 0, payload
            result.append(payload)
        return result

    before = projections()
    rc, written = _run_cli(registry, runtime, "semantic-preference", "agent", "remember",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--key", "review",
        "--statement", "Consult the assigned reviewer.", "--expected-revision", "none",
        "--operation-id", "isolation-write", "--source-ref", "owner-message",
        "--source-quote", "Consult the assigned reviewer.", "--execute")
    assert rc == 0 and written["status"] == "applied", written
    after = projections()
    for old, new in zip(before, after):
        assert new.get("turn_start_capability_hook_dispatch") == old.get("turn_start_capability_hook_dispatch")
    assert extract_turn_authority(after[1]) == extract_turn_authority(before[1])
    rc, empty = _run_cli(registry, runtime, "semantic-preference", "agent", "read", *scope)
    assert rc == 0 and empty["current"]["items"] == []
    assert len(list((runtime / "agent-preferences").iterdir())) == 1


def test_fresh_hook_uses_one_real_provider_snapshot_for_body(tmp_path, monkeypatch):
    from loopx.capabilities.semantic_preference import agent_preferences as adapter
    _, runtime, registry = _write_fixture(tmp_path, required_capability="network")
    original = adapter.agent_preferences
    calls = []
    def traced(**kwargs):
        calls.append(kwargs["action"])
        return original(**kwargs)
    monkeypatch.setattr(adapter, "agent_preferences", traced)
    def dispatch():
        return adapter.extend_turn_start_dispatch(None, registry_path=registry,
            runtime_root=runtime, goal_id=GOAL_ID, agent_id=AGENT_ID)
    empty = dispatch()
    assert calls == []  # No preference provider call on the untouched runtime.
    assert empty is None
    assert not (runtime / "agent-preferences").exists()
    rc, written = _run_cli(registry, runtime, "semantic-preference", "agent", "remember",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--key", "review",
        "--statement", "Use the designated reviewer.", "--expected-revision", "none",
        "--operation-id", "snapshot-write", "--source-ref", "owner-1",
        "--source-quote", "Use the designated reviewer.", "--execute")
    assert rc == 0, written
    calls.clear()
    delivered = dispatch()
    assert calls == ["turn_context"]
    assert delivered["results"][0]["status"] == "observed"
    assert delivered["contexts"][0]["content"]["current"] == written["current"]


@pytest.mark.parametrize("denied_target", ["journal", "namespace"])
def test_permission_denied_hook_is_not_empty_and_recovers(tmp_path, denied_target):
    import os
    if os.geteuid() == 0:
        pytest.skip("root bypasses POSIX read permission")
    _, runtime, registry = _write_fixture(tmp_path, required_capability="network")
    scope = ("--goal-id", GOAL_ID, "--agent-id", AGENT_ID)
    rc, written = _run_cli(registry, runtime, "semantic-preference", "agent", "remember", *scope,
        "--key", "review", "--statement", "Use the designated reviewer.",
        "--expected-revision", "none", "--operation-id", "permission-write",
        "--source-ref", "owner-1", "--source-quote", "Use the designated reviewer.", "--execute")
    assert rc == 0, written
    store = next((runtime / "agent-preferences").glob("*/authority-store-*.json"))
    denied_path = store if denied_target == "journal" else runtime / "agent-preferences"
    denied_path.chmod(0)
    try:
        rc, denied = _run_cli(registry, runtime, "semantic-preference", "agent", "read", *scope)
        assert rc != 0 and denied["status"] == "permission_denied", denied
        rc, quota = _run_cli(registry, runtime, "quota", "should-run", *scope)
        assert rc == 0, quota
        context = quota["interaction_contract"]["agent_channel"]["work_context"]
        assert not any(x["hook_id"] == "semantic_preference.agent_context"
                       for x in context.get("observations", []))
        assert context["unavailable_context"]["affected_hooks"] == [{
            "hook_id": "semantic_preference.agent_context", "capability_id": "semantic-preference",
            "status": "unavailable", "error_code": "agent_preferences_permission_denied"}]
        assert context["unavailable_context"]["dependent_action_policy"] == "hold_until_fresh_context"
    finally:
        denied_path.chmod(0o600 if denied_target == "journal" else 0o700)
    rc, recovered = _run_cli(registry, runtime, "quota", "should-run", *scope)
    assert rc == 0, recovered
    context = recovered["interaction_contract"]["agent_channel"]["work_context"]
    assert "unavailable_context" not in context
    assert next(x["content"]["current"] for x in context["sources"]
                if x.get("kind") == "agent_preferences") == written["current"]
