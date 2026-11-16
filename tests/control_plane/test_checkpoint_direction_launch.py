"""Characterize supported display changes without erasing source behavior."""
from __future__ import annotations

import json

import pytest

from loopx.agent_registry import agent_profile_for_goal
from loopx.goal_mode_context import first_registered_agent
from loopx.control_plane.goals.first_party_host_admission import FirstPartyHostRuntimeRejected
from loopx.control_plane.goals.checkpoint_context_io import _receipt_path
from loopx.control_plane.quota.settlement import SettlementIdentity
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from tests.control_plane import test_checkpoint_semantic_basis as semantic_cases
from tests.control_plane import test_native_lease_renewal as native_cases
from tests.control_plane.test_quota_settlement_cli import AGENT_ID, GOAL_ID, _run_cli

PEER = "independent-peer"


def _seed_source(fixture, *args, profile_list=False, **kwargs):
    project, runtime, registry, state = fixture(*args, **kwargs)
    payload = json.loads(registry.read_text())
    goal = payload["goals"][0]
    goal.setdefault("workspace_guard_policy", {})["peer_independent_worktree_required"] = False
    coordination = goal["coordination"]
    coordination["registered_agents"] = [AGENT_ID, PEER]
    own = {"agent_id": AGENT_ID, "scope_summary": "Verify the selected output."}
    peer = {"agent_id": PEER, "scope_summary": "Verify independent outputs."}
    coordination["agent_profiles"] = (
        [own, {**own, "scope_summary": "A different first-match instruction."}, peer]
        if profile_list else {AGENT_ID: own, PEER: peer})
    registry.write_text(json.dumps(payload), encoding="utf-8")
    return project, runtime, registry, state


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("change", ["goal-display", "profile-map-order", "unrelated-peer-todo"])
def test_display_or_unrelated_work_keeps_original_native_result(provider, change, tmp_path, monkeypatch):
    fixture, build = native_cases._default_fixture, native_cases.build_result
    context = {}

    def setup(*args, **kwargs):
        result = _seed_source(fixture, *args, **kwargs)
        context.update(zip(("project", "runtime", "registry", "state"), result))
        return result

    mutations = []

    def after_generation(request, *args, **kwargs):
        assert not mutations  # Mutate after the original work read, exactly once.
        registry = context["registry"]
        before = json.loads(registry.read_text())
        changed = json.loads(registry.read_text())
        goal = changed["goals"][0]
        if change == "goal-display":
            goal["display_name"] = "Another synthetic label"
        elif change == "profile-map-order":
            profiles = goal["coordination"]["agent_profiles"]
            goal["coordination"]["agent_profiles"] = dict(reversed(list(profiles.items())))
            assert list(profiles) != list(goal["coordination"]["agent_profiles"])
            for agent in (AGENT_ID, PEER):
                assert agent_profile_for_goal(before["goals"][0], agent) == agent_profile_for_goal(goal, agent)
        else:
            rc, added = _run_cli(registry, context["runtime"], "todo", "add", "--goal-id", GOAL_ID,
                "--role", "agent", "--text", "Verify an independent peer output.",
                "--claimed-by", PEER, cwd=context["project"])
            assert rc == 0 and added["added"], added
            # This is a real canonical peer Todo mutation, not a registry edit.
            mutations.append(added["todo_id"])
            return build(request, *args, **kwargs)
        registry.write_text(json.dumps(changed), encoding="utf-8")
        mutations.append(change)
        return build(request, *args, **kwargs)

    monkeypatch.setattr(native_cases, "_default_fixture", setup)
    monkeypatch.setattr(native_cases, "build_result", after_generation)
    # Reuse the unchanged actual driver/Native callback/independent validator
    # scenario. Its oracles require the persisted original work read ID, one
    # generation/validation/direction/debit and a replay with no extra effects.
    native_cases.test_native_completion_uses_current_proof_without_repeating_work(
        provider, "none", tmp_path, monkeypatch)
    assert len(mutations) == 1


@pytest.fixture(params=["file", "sqlite"])
def source_admitted(request, tmp_path, monkeypatch):
    fixture = semantic_cases._default_fixture
    monkeypatch.setattr(semantic_cases, "_default_fixture",
        lambda *args, **kwargs: _seed_source(fixture, *args, profile_list=True, **kwargs))
    # Keep the original admission, read and independent zero-effect snapshot.
    return semantic_cases.admitted.__wrapped__(request, tmp_path, monkeypatch)


@pytest.mark.parametrize("change", ["roster-order", "profile-guidance", "unknown-profile", "duplicate-profile-order"])
def test_behavioral_source_changes_still_refuse_original_read(source_admitted, change):
    _, _, _, lease, _, _, _, registry, _, snapshot, complete = source_admitted
    payload = json.loads(registry.read_text())
    goal = payload["goals"][0]
    coordination = goal["coordination"]
    if change == "roster-order":
        assert first_registered_agent(goal) == AGENT_ID
        coordination["registered_agents"].reverse()
        assert first_registered_agent(goal) == PEER
    elif change == "profile-guidance":
        coordination["agent_profiles"][0]["scope_summary"] = "Also verify another required output."
    elif change == "unknown-profile":
        coordination["agent_profiles"][0]["future_permission"] = True
    else:
        before = agent_profile_for_goal(goal, AGENT_ID)
        coordination["agent_profiles"].reverse()
        assert agent_profile_for_goal(goal, AGENT_ID) != before
    registry.write_text(json.dumps(payload), encoding="utf-8")
    before = snapshot()
    rc, rejected = complete(lease["version"])
    assert rc == 1 and rejected["error_code"] == "checkpoint_read_context_stale", rejected
    assert rejected["checkpoint_read_context"]["changed_components"] == ["source"], rejected
    assert snapshot() == before  # No Todo acceptance, run append or debit.


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("fault", ["after-capture-display", "goal-stop", "unknown"])
def test_original_turn_recovers_only_a_proved_launch_refusal(provider, fault, tmp_path, monkeypatch):
    fixture, handle = native_cases._default_fixture, native_cases.handle_turn_command
    saved, invocations = {}, []

    def setup(*args, **kwargs):
        result = fixture(*args, **kwargs)
        saved.update(zip(("project", "runtime", "registry", "state"), result))
        return result

    def observed_handle(args, **options):
        host = options["native_host_runner"]
        refused = False

        def observed_host(request, scope):
            nonlocal refused
            direction = request.get("phase") == "checkpoint_direction"
            invocations.append("direction" if direction else "work")
            if direction and not refused:
                refused = True
                if fault == "unknown":
                    with scope():
                        pass
                    # After scope entry the adapter cannot claim that a remote
                    # Host effect was absent, even if this fixture knows more.
                    raise FirstPartyHostRuntimeRejected("stale_goal_authority")
                payload = json.loads(saved["registry"].read_text())
                goal = payload["goals"][0]
                if fault == "goal-stop":
                    goal["quota"]["compute"] = 0
                else:
                    goal["display_name"] = "Changed after direction witness capture"
                saved["registry"].write_text(json.dumps(payload), encoding="utf-8")
            return host(request, scope)

        options["native_host_runner"] = observed_host
        saved.update(args=args, options=options)
        rc = handle(args, **options)
        saved["first_rc"] = rc
        return rc

    monkeypatch.setattr(native_cases, "_default_fixture", setup)
    monkeypatch.setattr(native_cases, "handle_turn_command", observed_handle)
    # The reused success scenario must reach its existing final oracle and
    # fail there on our deliberate launch refusal; inspect the real state next.
    with pytest.raises(AssertionError):
        native_cases.test_native_completion_uses_current_proof_without_repeating_work(
            provider, "none", tmp_path, monkeypatch)
    assert saved["first_rc"] == 1
    goal_root = saved["runtime"] / "goals" / GOAL_ID
    journal_path, = (goal_root / "turns").glob("*.json")
    journal = json.loads(journal_path.read_text())
    identity = SettlementIdentity.from_runtime_payload(journal["result_work_context"]["settlement_identity"])
    receipt_path = _receipt_path(saved["runtime"], identity)
    receipt = json.loads(receipt_path.read_text())
    assert receipt["direction_host_attempt_count"] == 1
    assert journal["completed_phases"] == ["host_execute", "typed_result", "validation"]
    canonical = read_canonical_todos_if_promoted(runtime_root=saved["runtime"], goal_id=GOAL_ID)
    assert canonical["todos"][0]["status"] == "done"
    if fault == "unknown":
        assert "direction_stops" not in receipt
    else:
        assert receipt["direction_stops"][0]["launch_refusal_code"]
        assert ("stop_reason" in receipt["direction_stops"][0]) == (fault == "goal-stop")
    if fault == "goal-stop":
        payload = json.loads(saved["registry"].read_text())
        payload["goals"][0]["quota"]["compute"] = 1
        saved["registry"].write_text(json.dumps(payload), encoding="utf-8")
    args, options, returned = saved["args"], saved["options"], {}
    args.resume_turn_key, args.retry_failed_turn = journal["turn_key"], True
    options["print_payload"] = lambda value, *_a, **_kw: returned.update(value)
    rc = handle(args, **options)
    assert invocations.count("work") == 1
    assert (saved["project"] / "validation-count.txt").read_text().splitlines() == ["validated"]
    rows = [json.loads(line) for line in (goal_root / "runs/index.jsonl").read_text().splitlines()]
    if fault == "after-capture-display":
        assert rc == 0 and returned["status"] == "committed", returned
        assert invocations.count("direction") == 2  # refused launch, then one actual response
        assert sum(r.get("classification") == "quota_slot_spent" for r in rows) == 1
        assert json.loads(journal_path.read_text())["result_work_context"] == journal["result_work_context"]
        before = (goal_root / "runs/index.jsonl").read_bytes()
        assert handle(args, **options) == 0
        assert (goal_root / "runs/index.jsonl").read_bytes() == before
        assert invocations.count("work") == 1 and invocations.count("direction") == 2
    else:
        assert rc == 1, returned
        assert invocations.count("direction") == 1
        assert not any(r.get("classification") == "quota_slot_spent" for r in rows)
        assert ("checkpoint_direction_stopped" if fault == "goal-stop" else
                "checkpoint_direction_launch_outcome_unknown") in json.dumps(returned)
