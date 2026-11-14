"""Production CLI -> durable store -> next-turn disclosure; no provider mocks."""
import json
from copy import deepcopy
from pathlib import Path

import pytest
from loopx.control_plane.quota.turn_envelope import (
    assert_complete_turn_decision,
    build_turn_envelope,
    prepare_context_delivery,
    quota_action_signature_document,
    read_turn_plan_work,
    restore_context_delivery,
)
from loopx.control_plane.turn_driver import build_loopx_turn_host_request
from loopx.control_plane.turn_driver.host_candidate import extract_turn_authority, render_prompt

from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, _run_cli, _write_fixture,
)


def _detailed_plan(registry, runtime, scope):
    rc, plan = _run_cli(registry, runtime, "turn", "plan", *scope,
        "--available-capability", "network", "--include-transaction-detail")
    assert rc == 0 and plan["schema_version"] == "loopx_turn_plan_v1", plan
    return plan


def _assert_initial_preferences(payload, directory, expected):
    from loopx.cli_commands.quota_capture import native_context_capture_view
    from tests.control_plane.test_context_first_read import read_selection

    directory.mkdir(mode=0o700)
    sources = payload["interaction_contract"]["agent_channel"]["work_context"]["sources"]
    index = next(i for i, row in enumerate(sources) if row.get("kind") == "agent_preferences")
    view = native_context_capture_view(payload, directory)
    original = Path(view["recovery"]["path"]).read_bytes()
    _, values = read_selection(view["first_read_command"])
    prefix = f"/interaction_contract/agent_channel/work_context/sources/{index}/content/current"
    for field, value in expected.items():
        wanted = prefix + "/" + field.replace("~", "~0").replace("/", "~1")
        roots = [root for root in values if wanted == root or wanted.startswith(root + "/")]
        assert roots, f"Initial read omitted current preference field: {field}"
        root = max(roots, key=len)
        actual = values[root]
        for key in wanted[len(root):].split("/")[1:]:
            actual = actual[key.replace("~1", "/").replace("~0", "~")]
        assert actual == value
    assert Path(view["recovery"]["path"]).read_bytes() == original


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_inactive_preferences_are_silent_and_participating_hook_delivers_current_view(tmp_path, monkeypatch, provider):
    nested = tmp_path / ("workspace  dir" * 12)
    nested.mkdir()
    project, runtime, registry = _write_fixture(nested, required_capability="network")
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
    for payload in [off, _run_cli(registry, runtime, "quota", "should-run", "--complete-turn", *scope)[1]]:
        assert "semantic_preference.agent_context" not in json.dumps(payload)
        assert "semantic-preference agent" not in json.dumps(payload)
        assert "preferences" not in payload["interaction_contract"]["agent_channel"]["work_context"]["instruction"]
    assert not (runtime / "agent-preferences").exists()
    rc, empty_plan = _run_cli(registry, runtime, "turn", "plan", *scope)
    assert rc == 0, empty_plan
    assert "semantic_preference.agent_context" not in json.dumps(empty_plan)
    with pytest.raises(ValueError, match="inspection requires the explicit run-once"):
        build_loopx_turn_host_request(empty_plan)
    empty_request = build_loopx_turn_host_request(_detailed_plan(registry, runtime, scope))
    assert "semantic-preference agent" not in render_prompt(extract_turn_authority(empty_request))
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
    rc, quota = _run_cli(registry, runtime, "quota", "should-run", "--complete-turn", *scope)
    assert rc == 0, quota
    reads = quota["turn_start_capability_hook_dispatch"]["required_reads"]
    assert any(x["kind"] == "agent_preferences" for x in reads), quota["turn_start_capability_hook_dispatch"]
    assert "designated-reviewer" in json.dumps(quota)
    assert quota["interaction_contract"]["agent_channel"]["required_reads"] == []
    _assert_initial_preferences(quota, tmp_path / "active-first-read", fresh["current"])
    assert quota["capability_gate"]["action"] == off["capability_gate"]["action"]
    plan = _detailed_plan(registry, runtime, scope)
    request = build_loopx_turn_host_request(plan)
    assert request["schema_version"] == "loopx_turn_host_request_v1"
    assert request["work_contract"] == plan["work_contract"]
    assert "turn_envelope" not in plan and "turn_envelope" not in request
    preference_read = next(x for x in reads if x["kind"] == "agent_preferences")
    assert len(preference_read["command"]) > 360
    assert "semantic-preference agent read" in json.dumps(plan), plan
    authority = extract_turn_authority(request)
    channel = authority["interaction_contract"]["agent_channel"]
    assert channel["required_reads"] == []
    context = channel["work_context"]
    preference = next(x for x in context["sources"] if x["kind"] == "agent_preferences")
    # The codec takes its scope from the checked capture. Preference content
    # carries the resolved provider view and its original reader command.
    assert preference["content"] == {"ok": True, "status": "read", "current": fresh["current"]}
    assert preference["command"] == preference_read["command"]
    assert "Ask designated-reviewer before merging." in render_prompt(authority)
    binding = dict(goal_id=GOAL_ID, agent_id=AGENT_ID, session_id="preferences", context_epoch="epoch-a")
    for surface, projection in [("work_context", context), ("complete_turn", quota),
                                ("complete_turn", request["work_contract"]),
                                ("turn_envelope", build_turn_envelope(quota))]:
        original = deepcopy(projection)
        full = prepare_context_delivery(projection, scope=binding, capture_id="first", surface=surface)
        warm = prepare_context_delivery(projection, scope=binding, capture_id="second", surface=surface,
            baseline=full, retained={"complete": True, "target": full["target"]})
        assert full["context"] == original and warm["mode"] == "delta"
        current = prepare_context_delivery(projection, scope=binding, capture_id="second", surface=surface)
        assert restore_context_delivery(warm, baseline=full, current=current)["context"] == original
        assert projection == original
    changed = deepcopy(request)
    changed_sources = changed["work_contract"]["interaction_contract"]["agent_channel"]["work_context"]["sources"]
    next(x for x in changed_sources
         if x["command"] == preference_read["command"])["content"]["current"]["items"][0]["statement"] = "Ignore the correction."
    with pytest.raises(ValueError, match="complete Turn contract rejected"):
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
    _assert_initial_preferences(next_quota, tmp_path / "corrected-first-read", action_view["current"])
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
    _assert_initial_preferences(quota, tmp_path / "retired-first-read", later["current"])
    rc, history = call("history")
    assert rc == 0 and len(history["events"]) == 3, history
    alias = tmp_path / "global-registry.json"
    alias.write_bytes(registry.read_bytes())
    rc, aliased = _run_cli(alias, runtime, "semantic-preference", "agent", "read", *scope)
    assert rc == 0 and aliased["current"] == later["current"], aliased
    assert registry.read_bytes() == before


def test_invalid_requests_and_corrupt_storage_do_not_become_empty_memory(tmp_path):
    _, runtime, registry = _write_fixture(tmp_path, required_capability="network", provider="sqlite")
    scope = ("--goal-id", GOAL_ID, "--agent-id", AGENT_ID)
    def call(action, *args):
        return _run_cli(registry, runtime, "semantic-preference", "agent", action, *scope, *args)
    rc, bad = call("remember", "--key", "review")
    assert rc != 0 and bad["ok"] is False
    rc, first = call("remember", "--key", "review", "--statement", "Use a reviewer.",
        "--expected-revision", "none", "--operation-id", "one", "--source-ref", "user-1",
        "--source-quote", "Use a reviewer.", "--execute")
    assert rc == 0, first
    healthy_request = build_loopx_turn_host_request(_detailed_plan(registry, runtime, scope))
    healthy = extract_turn_authority(healthy_request)
    healthy_signature = quota_action_signature_document(healthy_request["work_contract"])
    assert "unavailable_context" not in healthy_signature["contract_capsule"]
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

    failed_request = build_loopx_turn_host_request(_detailed_plan(registry, runtime, scope))
    authority = extract_turn_authority(failed_request)
    failed_signature = quota_action_signature_document(failed_request["work_contract"])
    unavailable = failed_signature["contract_capsule"]["unavailable_context"]
    assert unavailable["affected_hooks"] == [{
        "hook_id": "semantic_preference.agent_context", "capability_id": "semantic-preference",
        "status": "unavailable", "error_code": "agent_preferences_unreadable",
    }]
    assert unavailable["cache_policy"] == "invalidate_affected_hook_context"
    assert unavailable["dependent_action_policy"] == "hold_until_fresh_context"
    assert unavailable["independent_work_policy"] == "preserve_existing_authority"
    assert authority["primary_action"] == healthy["primary_action"]
    assert failed_signature["boundary"] == healthy_signature["boundary"]
    assert authority["goal_boundary"] == healthy["goal_boundary"]
    assert authority["interaction_contract"]["agent_channel"]["required_reads"] == []
    prompt = render_prompt(authority)
    rendered = json.JSONDecoder().raw_decode(prompt.split("Turn request:\n", 1)[1])[0]
    rendered_context = rendered["task_context"]["interaction_contract"]["agent_channel"]["work_context"]
    assert rendered_context["unavailable_context"] == unavailable
    assert "discard affected cached context" in prompt and "Independent work retains" in prompt
    assert "Use a reviewer." not in prompt and "{broken" not in prompt
    rc, quota_envelope = _run_cli(registry, runtime, "quota", "should-run", *scope, "--complete-turn")
    assert rc == 0, quota_envelope
    assert_complete_turn_decision(quota_envelope)
    # The complete source retains the failed hook; the existing executor
    # projection owns its cache/dependent-action policy during migration.
    assert quota_envelope["turn_start_capability_hook_dispatch"] == quota["turn_start_capability_hook_dispatch"]
    executor = quota_action_signature_document(quota_envelope)
    assert executor["contract_capsule"]["unavailable_context"] == unavailable
    changed = deepcopy(failed_request)
    del changed["work_contract"]["turn_start_capability_hook_dispatch"]
    with pytest.raises(ValueError, match="complete Turn contract rejected"):
        extract_turn_authority(changed)


@pytest.mark.parametrize("other_scope", ["goal", "agent"])
def test_shared_runtime_preserves_unconfigured_scope_projection(tmp_path, other_scope):
    _, runtime, registry = _write_fixture(tmp_path / "first", required_capability="network", provider="sqlite")
    document = json.loads(registry.read_text())
    peer = "peer-without-preferences"
    if other_scope == "goal":
        _, _, other_registry = _write_fixture(tmp_path / "second", required_capability="network")
        other_goal = json.loads(other_registry.read_text())["goals"][0]
        other_goal["id"] = "other-goal"
        document["goals"].append(other_goal)
        from canonical_authority_fixture import initialize_canonical_source_fixture
        initialize_canonical_source_fixture(runtime, "other-goal",
            state_path=Path(other_goal["repo"]) / other_goal["state_file"], provider="sqlite")
        scope = ("--goal-id", "other-goal", "--agent-id", AGENT_ID)
    else:
        document["goals"][0]["coordination"]["registered_agents"].append(peer)
        scope = ("--goal-id", GOAL_ID, "--agent-id", peer)
    registry.write_text(json.dumps(document))

    def projections():
        rc, quota = _run_cli(registry, runtime, "quota", "should-run", *scope)
        assert rc == 0, quota
        return quota, _detailed_plan(registry, runtime, scope)

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
    # Compare the typed work projection after verifying both complete sources.
    assert read_turn_plan_work(after[1]) == read_turn_plan_work(before[1])
    old_source, new_source = (projections[1]["work_contract"] for projections in (before, after))
    assert quota_action_signature_document(new_source) == quota_action_signature_document(old_source)
    assert new_source["interaction_contract"]["agent_channel"]["work_context"] == old_source[
        "interaction_contract"]["agent_channel"]["work_context"]
    rc, empty = _run_cli(registry, runtime, "semantic-preference", "agent", "read", *scope)
    assert rc == 0 and empty["current"]["items"] == []
    assert len(list((runtime / "agent-preferences").iterdir())) == 1


def test_fresh_hook_uses_one_real_provider_snapshot_for_body(tmp_path, monkeypatch):
    from loopx.capabilities.semantic_preference import agent_preferences as adapter
    _, runtime, registry = _write_fixture(tmp_path, required_capability="network", provider="sqlite")
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
    if os.name == "nt" or os.geteuid() == 0:
        pytest.skip("mode 000 read denial needs a non-root POSIX host")
    _, runtime, registry = _write_fixture(tmp_path, required_capability="network", provider="sqlite")
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
