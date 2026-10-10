"""Actual Chat entrypoint against a fenced FileAuthorityStore and projection IO."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.chat_actions import ChatActionService
from loopx.chat_action_store import ChatActionStore
from loopx.control_plane.work_items.governed_transition_proposal import (
    GovernedTransitionSettlementPhase, settle_governed_transition_proposals,
)
from loopx.control_plane.work_items.team_plan_adapter import team_plan_state_fingerprint
from loopx.control_plane.coordination.coordination_state_contract import (
    TODO_DOMAIN_READ_RECORD_SCHEMA_VERSION, TODO_DOMAIN_RECORD_FIELDS,
)
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.coordination.local_authority_shadow_projection import canonical_bytes
from loopx.control_plane.todos import provider_projection


@pytest.fixture(params=["file", "sqlite"])
def canonical_team(tmp_path, request, monkeypatch):
    if request.param == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime = tmp_path / "runtime"
    state = tmp_path / "state.md"
    state.write_text('# Goal\n\n## Agent Todo\n\n')
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{
        "id": "goal-a", "repo": str(tmp_path), "state_file": "state.md",
        "coordination": {"registered_agents": ["alpha", "beta"], "agent_model": "peer_v1"}}]}))
    initialize_canonical_authority(runtime, "goal-a", {"goal_id": "goal-a", "todos": [], "leases": [],
        "todo_read_model": {"schema_version": TODO_DOMAIN_READ_RECORD_SCHEMA_VERSION,
            "contract_fields": list(TODO_DOMAIN_RECORD_FIELDS), "todo_count": 0,
            "records_sha256": hashlib.sha256(canonical_bytes([])).hexdigest()}}, state_path=state, provider=request.param)
    service = ChatActionService(store=ChatActionStore(runtime / "chat/actions"), registry_path=registry)
    plan = {"schema_version": "steward_team_plan_preview_v0", "kind": "steward_team_plan_preview",
        "goal_id": "goal-a", "objective": "Independent lane outcomes", "quota_envelope": {"slots": 2},
        "stop_condition": "Owner ends request", "lanes": [{"lane_id": f"lane-{agent}", "agent_id": agent,
            "acceptance": "Return independent evidence", "first_todo": {"text": "Same work", "priority": "P1",
            "task_class": "advancement_task", "action_kind": "implement"}} for agent in ("alpha", "beta")]}
    preview = service.preview({"action_kind": "team.plan", "summary": "Assign work", "context": {},
        "normalized_parameters": {"goal_id": "goal-a", "plan": plan}, "idempotency_key": "team-confirm"})
    return runtime, state, service, preview


@pytest.mark.parametrize("source", ["state", "registry"])
def test_reviewed_sources_remain_locked_through_native_commit(canonical_team, monkeypatch, source):
    """A compliant writer cannot replace reviewed facts after fingerprinting."""
    from loopx.file_lock import exclusive_cross_runtime_file_lock, LockAcquireTimeoutError
    from loopx.control_plane.work_items import team_plan_adapter

    runtime, state, service, preview = canonical_team
    target = state if source == "state" else service.registry_path
    original_effect = team_plan_adapter.effect_runtime_result
    checked = []

    def inspect(method, request):
        if method == "work_items.team_plan.commit":
            with pytest.raises(LockAcquireTimeoutError):
                with exclusive_cross_runtime_file_lock(target, timeout_seconds=0):
                    pass
            checked.append(source)
        return original_effect(method, request)

    monkeypatch.setattr(team_plan_adapter, "effect_runtime_result", inspect)
    result = service.apply(preview["proposal_id"])["proposal"]
    assert result["status"] == "applied", result
    assert checked == [source]
    assert len(read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")["todos"]) == 2
    # Every path releases its source guards, allowing the next ordinary writer.
    with exclusive_cross_runtime_file_lock(target, timeout_seconds=0):
        pass


@pytest.mark.parametrize("source", ["state", "registry"])
def test_changed_source_during_handoff_creates_no_todos(canonical_team, monkeypatch, source):
    """The native owner rejects changed bytes even if a writer bypasses locks."""
    from loopx.control_plane.work_items import team_plan_adapter

    runtime, state, service, preview = canonical_team
    target = state if source == "state" else service.registry_path
    original_effect = team_plan_adapter.effect_runtime_result

    def replace(method, request):
        if method == "work_items.team_plan.commit":
            target.write_bytes(target.read_bytes() + b"\n")
        return original_effect(method, request)

    monkeypatch.setattr(team_plan_adapter, "effect_runtime_result", replace)
    result = service.apply(preview["proposal_id"])["proposal"]
    assert result["status"] == "stale", result
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")["todos"] == []


def test_busy_registry_releases_source_locks_without_committing(canonical_team):
    from loopx.file_lock import exclusive_cross_runtime_file_lock

    runtime, state, service, preview = canonical_team
    with exclusive_cross_runtime_file_lock(service.registry_path):
        result = service.apply(preview["proposal_id"])["proposal"]
        assert result["status"] == "failed", result
        with exclusive_cross_runtime_file_lock(state, timeout_seconds=0):
            pass
        assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")["todos"] == []
    assert service.apply(preview["proposal_id"])["proposal"]["status"] == "applied"


@pytest.mark.parametrize("damage", ["missing", "token", "pid", "target"])
def test_native_commit_requires_valid_source_lock_handoff(canonical_team, monkeypatch, damage):
    from loopx.control_plane.work_items import team_plan_adapter

    runtime, state, service, preview = canonical_team
    original_effect = team_plan_adapter.effect_runtime_result

    def tamper(method, request):
        if method == "work_items.team_plan.commit":
            if damage == "missing":
                request.pop("locks")
            elif damage == "token":
                request["locks"][0]["token"] = "expired-token"
            elif damage == "pid":
                request["locks"][0]["pid"] = -1
            else:
                request["locks"][0]["target"] = str(state)
        return original_effect(method, request)

    monkeypatch.setattr(team_plan_adapter, "effect_runtime_result", tamper)
    result = service.apply(preview["proposal_id"])["proposal"]
    assert result["status"] == "failed", result
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")["todos"] == []


def test_native_lost_response_replays_after_handoff_expires(canonical_team, monkeypatch):
    from loopx.control_plane.work_items import team_plan_adapter

    runtime, state, service, preview = canonical_team
    original_effect = team_plan_adapter.effect_runtime_result
    captured = []

    def capture(method, request):
        if method == "work_items.team_plan.commit":
            captured.append(request)
        return original_effect(method, request)

    monkeypatch.setattr(team_plan_adapter, "effect_runtime_result", capture)
    assert service.apply(preview["proposal_id"])["proposal"]["status"] == "applied"
    before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")
    state.write_text(state.read_text() + "\nOwner revised the objective.\n")
    replay = original_effect("work_items.team_plan.commit", captured[0])
    assert replay["status"] == "replayed", replay
    assert replay["result"]["action"] == "reused"
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")["provider_revision"] == before["provider_revision"]


def test_dead_caller_cannot_release_native_source_claims(canonical_team, tmp_path):
    """Kill the Python caller after adoption, before the real provider CAS."""
    from loopx.file_lock import exclusive_mutation_file_lock, LockAcquireTimeoutError

    runtime, state, service, preview = canonical_team
    repo = Path(__file__).resolve().parents[2]
    barrier = tmp_path / "caller-exit"
    barrier.mkdir()
    authority = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")
    provider = "sqlite" if authority["source_authority"] == "sqlite_v0" else "file"
    script = r'''
import json, os, subprocess, sys, time
from pathlib import Path
from loopx.chat_actions import ChatActionService
from loopx.chat_action_store import ChatActionStore
from loopx.control_plane.work_items import team_plan_adapter
registry, runtime, proposal, barrier, provider, probe = sys.argv[1:]
barrier = Path(barrier)
original = team_plan_adapter.effect_runtime_result
def native(method, request):
    if method != "work_items.team_plan.commit":
        return original(method, request)
    (barrier / "request.json").write_text(json.dumps(request))
    with (barrier / "native.log").open("w") as output:
        child = subprocess.Popen(["node", "--no-warnings", "--experimental-strip-types", probe, str(barrier), provider],
                                 stdout=output, stderr=output)
    deadline = time.monotonic() + 15
    while not (barrier / "adopted").exists():
        assert child.poll() is None, (barrier / "native.log").read_text()
        assert time.monotonic() < deadline, "native adoption timed out"
        time.sleep(.01)
    os._exit(0)
team_plan_adapter.effect_runtime_result = native
ChatActionService(store=ChatActionStore(Path(runtime) / "chat/actions"), registry_path=Path(registry)).apply(proposal)
'''
    owner = subprocess.Popen([sys.executable, "-c", script, str(service.registry_path), str(runtime),
        preview["proposal_id"], str(barrier), provider, str(repo / "tests/control_plane_ts/team_plan_commit_probe.ts")],
        cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={**os.environ, "PYTHONPATH": str(repo)})
    try:
        stdout, stderr = owner.communicate(timeout=20)
        assert owner.returncode == 0, stdout + stderr
        request = json.loads((barrier / "request.json").read_text())
        for witness in request["locks"]:
            with pytest.raises(LockAcquireTimeoutError):
                with exclusive_mutation_file_lock(Path(witness["target"]), timeout_seconds=0):
                    pytest.fail("dead caller's native claim was stolen")
        (barrier / "release").touch()
        deadline = time.monotonic() + 15
        while not (barrier / "finished").exists():
            assert time.monotonic() < deadline, (barrier / "native.log").read_text()
            time.sleep(.01)
        assert json.loads((barrier / "result.json").read_text())["status"] == "applied"
        before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")
        assert len(before["todos"]) == 2
        # Retry repairs the display after caller exit; no lane is admitted twice.
        result = service.apply(preview["proposal_id"])["proposal"]
        assert result["status"] == "applied", result
        assert state.read_text().count("Same work") == 2
        assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")["provider_revision"] == before["provider_revision"]
    finally:
        (barrier / "release").touch()
        if owner.poll() is None:
            owner.kill()
            owner.communicate()


@pytest.mark.parametrize("failure_point", ["write", "confirmation"])
def test_canonical_chat_commit_replays_after_projection_failure(canonical_team, monkeypatch, failure_point):
    runtime, state, service, preview = canonical_team
    def fail(*args, **kwargs):
        raise OSError("display unavailable")
    if failure_point == "write":
        monkeypatch.setattr(provider_projection, "atomic_write_state_text", fail)
    else:
        read_authority = provider_projection.read_canonical_todos_if_promoted
        def read_after_write(**kwargs):
            if kwargs.get("projection_readback") is not None:
                raise OSError("confirmation unavailable")
            return read_authority(**kwargs)
        monkeypatch.setattr(provider_projection, "read_canonical_todos_if_promoted", read_after_write)
    failed = service.apply(preview["proposal_id"])["proposal"]
    assert failed["status"] == "failed"
    read = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")
    assert len(read["todos"]) == 2
    assert len({row["todo_id"] for row in read["todos"]}) == 2
    assert ("Same work" not in state.read_text()) is (failure_point == "write")
    monkeypatch.undo()
    applied = service.apply(preview["proposal_id"])["proposal"]
    assert applied["status"] == "applied", applied
    assert state.read_text().count("Same work") == 2
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")["provider_revision"] == read["provider_revision"]


def test_canonical_change_without_markdown_refresh_invalidates_preview(canonical_team):
    runtime, state, service, preview = canonical_team
    from loopx.todos import add_goal_todo
    before = state.read_bytes()
    # The public owner commits and projects new work; restoring only the display
    # simulates delayed projection, not a rollback of canonical authority.
    add_goal_todo(registry_path=service.registry_path, goal_id="goal-a", role="agent", text="Concurrent work",
                  task_class="advancement_task", action_kind="implement", agent_id="alpha")
    state.write_bytes(before)
    result = service.apply(preview["proposal_id"])["proposal"]
    assert result["status"] == "stale", result
    assert len(read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")["todos"]) == 1


def _agent_plan() -> dict:
    """An agent-originated plan may only reserve a lane for its own author."""
    return {"schema_version": "steward_team_plan_preview_v0", "kind": "steward_team_plan_preview",
        "goal_id": "goal-a", "proposal_id": "agent-plan", "objective": "Independent lane outcomes",
        "quota_envelope": {"slots": 2}, "stop_condition": "Owner ends request",
        "lanes": [{"lane_id": "lane-alpha", "agent_id": "alpha", "acceptance": "Return independent evidence",
            "first_todo": {"text": "Own work", "priority": "P1", "task_class": "advancement_task", "action_kind": "implement"}}]}


def _settle_as_agent(service, basis, writes):
    return settle_governed_transition_proposals(
        registry_path=service.registry_path, goal_id="goal-a", agent_id="alpha", effect_id="effect-agent-plan",
        proposals=[_agent_plan()], existing_receipts=[], checkpoint=writes.append,
        phase=GovernedTransitionSettlementPhase.PRE_SETTLEMENT, team_plan_state_basis=basis)


@pytest.mark.parametrize("basis_state", ["moved", "missing"])
def test_canonical_agent_settlement_is_refused_without_its_bound_basis(canonical_team, basis_state):
    """Promoted authority refuses a plan whose journal basis moved or was never bound.

    The provider-revision guard only covers the write race; the basis the agent
    bound before its provider ran is what ties the plan to the Goal it was
    shaped against, so the promoted path compares it too and writes nothing.
    """
    runtime, state, service, _preview = canonical_team
    basis = team_plan_state_fingerprint(registry_path=service.registry_path, goal_id="goal-a", basis_agent_id="alpha")
    if basis_state == "moved":
        state.write_text(state.read_text() + "\nObjective rewritten by the owner.\n")
    writes: list = []

    receipts = _settle_as_agent(service, basis if basis_state == "moved" else None, writes)

    assert receipts[0]["status"] == "failed"
    assert receipts[0]["reason_code"] == ("team_plan_preview_stale" if basis_state == "moved" else "team_plan_basis_missing")
    assert receipts[0]["todo_id"] is None
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")["todos"] == []
    assert "Own work" not in state.read_text()
    assert writes == [receipts]


def test_canonical_agent_settlement_applies_against_its_bound_basis(canonical_team):
    runtime, state, service, _preview = canonical_team
    basis = team_plan_state_fingerprint(registry_path=service.registry_path, goal_id="goal-a", basis_agent_id="alpha")

    receipts = _settle_as_agent(service, basis, [])

    assert receipts[0]["status"] == "committed"
    read = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")
    assert [row["claimed_by"] for row in read["todos"]] == ["alpha"]
    assert receipts[0]["lane_todo_ids"] == [read["todos"][0]["todo_id"]]
