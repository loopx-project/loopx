"""Real-provider source semantics; ordered behavior must not become display."""
from __future__ import annotations

import json

import pytest

from loopx.agent_registry import agent_profile_for_goal
from loopx.goal_mode_context import first_registered_agent
from tests.control_plane import test_checkpoint_semantic_basis as cases
from tests.control_plane.test_quota_settlement_cli import AGENT_ID, GOAL_ID, _spend_run_count

PEER = "independent-peer"


@pytest.fixture(params=["file", "sqlite"])
def source_work(request, tmp_path, monkeypatch):
    fixture = cases._default_fixture

    def setup(*args, **kwargs):
        project, runtime, registry, state = fixture(*args, **kwargs)
        payload = json.loads(registry.read_text())
        coordination = payload["goals"][0]["coordination"]
        coordination["registered_agents"] = [AGENT_ID, PEER]
        own = {"agent_id": AGENT_ID, "scope_summary": "Verify the selected output."}
        peer = {"agent_id": PEER, "scope_summary": "Verify independent outputs."}
        coordination["agent_profiles"] = (
            [own, {**own, "scope_summary": "Another first-match instruction."}, peer]
            if request.node.callspec.params["change"] == "duplicate-profile-order"
            else {AGENT_ID: own, PEER: peer})
        registry.write_text(json.dumps(payload), encoding="utf-8")
        return project, runtime, registry, state

    monkeypatch.setattr(cases, "_default_fixture", setup)
    # Reuse the unchanged canonical admission, original read and zero-effect
    # oracle. Only the synthetic source setup differs from the original case.
    return cases.admitted.__wrapped__(request, tmp_path, monkeypatch)


@pytest.mark.parametrize("change", ["goal-display", "profile-map-order", "unrelated-peer-todo"])
def test_supported_display_or_unrelated_todo_keeps_original_read(source_work, change):
    cli, _, _, lease, context, receipt, _, registry, runtime, _, complete = source_work
    payload = json.loads(registry.read_text())
    goal = payload["goals"][0]
    if change == "unrelated-peer-todo":
        rc, added = cli("todo", "add", "--goal-id", GOAL_ID, "--role", "agent",
            "--text", "Verify an independent peer output.", "--claimed-by", PEER)
        assert rc == 0 and added["added"], added
    else:
        if change == "goal-display":
            goal["display_name"] = "Another synthetic label"
        else:
            before = {agent: agent_profile_for_goal(goal, agent) for agent in (AGENT_ID, PEER)}
            profiles = goal["coordination"]["agent_profiles"]
            goal["coordination"]["agent_profiles"] = dict(reversed(list(profiles.items())))
            assert list(profiles) != list(goal["coordination"]["agent_profiles"])
            assert {agent: agent_profile_for_goal(goal, agent) for agent in before} == before
        registry.write_text(json.dumps(payload), encoding="utf-8")
    rc, saved = complete(lease["version"])
    assert rc == 0 and saved["completed"], saved
    persisted = json.loads(receipt.read_text())
    assert persisted["schema_version"] == "checkpoint_read_context_v3"
    assert persisted["read_context_id"] == context["read_context_id"]
    index = runtime / "goals" / GOAL_ID / "runs/index.jsonl"
    before_replay = index.read_bytes() if index.exists() else None
    rc, replay = complete(lease["version"])
    assert rc == 0 and replay["idempotent_replay"], replay
    assert (index.read_bytes() if index.exists() else None) == before_replay
    assert _spend_run_count(runtime) == 0


@pytest.mark.parametrize("change", ["roster-order", "profile-guidance", "unknown-profile", "duplicate-profile-order"])
def test_behavioral_or_unknown_source_change_has_no_acceptance_effect(source_work, change):
    _, _, _, lease, _, _, _, registry, _, snapshot, complete = source_work
    payload = json.loads(registry.read_text())
    goal = payload["goals"][0]
    coordination = goal["coordination"]
    if change == "roster-order":
        assert first_registered_agent(goal) == AGENT_ID
        coordination["registered_agents"].reverse()
        assert first_registered_agent(goal) == PEER
    elif change == "duplicate-profile-order":
        before = agent_profile_for_goal(goal, AGENT_ID)
        coordination["agent_profiles"].reverse()
        assert agent_profile_for_goal(goal, AGENT_ID) != before
    else:
        profile = coordination["agent_profiles"][AGENT_ID]
        profile["scope_summary" if change == "profile-guidance" else "future_permission"] = "changed"
    registry.write_text(json.dumps(payload), encoding="utf-8")
    before = snapshot()
    rc, rejected = complete(lease["version"])
    assert rc == 1 and rejected["error_code"] == "checkpoint_read_context_stale", rejected
    assert rejected["checkpoint_read_context"]["changed_components"] == ["source"], rejected
    assert snapshot() == before
