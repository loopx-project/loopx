"""Actual Chat entrypoint against a fenced FileAuthorityStore and projection IO."""
import hashlib
import json
import os
from pathlib import Path
import shutil
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


@pytest.fixture
def capture_free_chat(canonical_team, tmp_path):
    return _capture_free_chat(canonical_team, tmp_path)


def _capture_free_chat(team, tmp_path):
    """Exercise the real Chat entrypoint from a physically isolated package."""
    import loopx

    runtime, _state, service, original_preview = team
    package_root = tmp_path / "package"
    package = package_root / "loopx"
    shutil.copytree(Path(loopx.__file__).parent, package,
                    ignore=shutil.ignore_patterns("__pycache__"))
    (package / "control_plane/coordination/runtime_shadow_writer_adapter.py").unlink()
    script = r'''
import json, sys
from pathlib import Path
import loopx
from loopx.chat_actions import ChatActionService
from loopx.chat_action_store import ChatActionStore
from loopx.control_plane.work_items import team_plan_adapter
from loopx.control_plane.todos import provider_projection
request = json.loads(sys.argv[1])
assert Path(loopx.__file__).resolve() == Path(request['package']) / '__init__.py'
service = ChatActionService(store=ChatActionStore(Path(request['runtime']) / 'chat/actions'),
                          registry_path=Path(request['registry']))
if request['operation'] == 'preview':
    parameters = service.store.load(request['original'])['normalized_parameters']
    for lane in parameters['plan']['lanes']:
        lane['first_todo']['text'] = request['text']
    result = service.preview({'action_kind': 'team.plan', 'summary': 'Assign work', 'context': {},
        'normalized_parameters': parameters, 'idempotency_key': request['text'].replace(' ', '-')})
elif request['operation'] == 'replay':
    result = team_plan_adapter.effect_runtime_result('work_items.team_plan.commit',
        json.loads(Path(request['capture']).read_text()))
elif request['operation'] == 'historical':
    proposal = service.store.load(request['original'])
    result = team_plan_adapter.settle_team_plan(registry_path=service.registry_path,
        goal_id='goal-a', agent_id=None,
        proposal={**proposal['normalized_parameters']['plan'], 'proposal_id': proposal['proposal_id']},
        expected_state_fingerprint=proposal['expected_state_fingerprint'], basis_agent_id='alpha')
else:
    original = team_plan_adapter.effect_runtime_result
    def capture(method, payload):
        if method == 'work_items.team_plan.commit' and request.get('capture'):
            Path(request['capture']).write_text(json.dumps(payload))
        return original(method, payload)
    team_plan_adapter.effect_runtime_result = capture
    if request.get('fail_display'):
        def fail(*args, **kwargs):
            raise OSError('display unavailable')
        provider_projection.atomic_write_state_text = fail
    result = service.apply(request['proposal'])
print(json.dumps(result))
'''

    def invoke(operation, **kwargs):
        result = subprocess.run([sys.executable, "-c", script, json.dumps({
            "operation": operation, "package": str(package.resolve()), "runtime": str(runtime),
            "registry": str(service.registry_path), "original": original_preview["proposal_id"],
            **{key: str(value) if isinstance(value, Path) else value for key, value in kwargs.items()},
        })], cwd=tmp_path, env={**os.environ, "PYTHONPATH": str(package_root)},
            capture_output=True, text=True, timeout=45)
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)

    return invoke


def test_capture_free_chat_preserves_newer_writes_and_provider_recovery(
        canonical_team, capture_free_chat, tmp_path):
    runtime, state, _service, _preview = canonical_team
    first = capture_free_chat("preview", text="Source isolated work")
    capture = tmp_path / "original-request.json"
    applied = capture_free_chat("apply", proposal=first["proposal_id"], capture=capture)
    assert applied["proposal"]["status"] == "applied", applied
    before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")
    assert len(before["todos"]) == 2
    assert state.read_text().count("Source isolated work") == 2

    later = capture_free_chat("preview", text="Newer work")
    backend = runtime / "authority" / before["source_authority"].replace("_", "-")
    offline = backend.with_name(backend.name + "-offline")
    display = state.read_bytes()
    backend.rename(offline)
    try:
        unavailable = capture_free_chat("apply", proposal=later["proposal_id"])
        assert unavailable["proposal"]["status"] == "failed", unavailable
        assert not backend.exists() and state.read_bytes() == display
    finally:
        offline.rename(backend)
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a") == before
    restored = capture_free_chat("apply", proposal=later["proposal_id"])
    assert restored["proposal"]["status"] == "applied", restored
    newer = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")
    assert len(newer["todos"]) == 4
    replay = capture_free_chat("replay", capture=capture)
    assert replay["status"] == "replayed", replay
    assert replay["result"]["action"] == "reused"
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a") == newer

    stale = capture_free_chat("preview", text="Unreviewed work")
    state.write_text(state.read_text() + "\nOwner changed the objective.\n")
    rejected = capture_free_chat("apply", proposal=stale["proposal_id"])
    assert rejected["proposal"]["status"] == "stale", rejected
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a") == newer


def test_capture_free_chat_recovers_committed_projection(canonical_team, capture_free_chat):
    runtime, state, _service, _preview = canonical_team
    preview = capture_free_chat("preview", text="Recover the confirmed plan")
    display = state.read_bytes()
    pending = capture_free_chat("apply", proposal=preview["proposal_id"], fail_display=True)
    assert pending["proposal"]["status"] == "failed", pending
    assert pending["proposal"]["failure"]["error_code"] == "team_plan_projection_pending"
    committed = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")
    assert len(committed["todos"]) == 2 and state.read_bytes() == display
    recovered = capture_free_chat("apply", proposal=preview["proposal_id"])
    assert recovered["proposal"]["status"] == "applied", recovered
    assert state.read_text().count("Recover the confirmed plan") == 2
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")["provider_revision"] == committed["provider_revision"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_capture_free_chat_preserves_pre_promotion_receipt(tmp_path, monkeypatch, provider):
    """Imported history remains readable without recreating or overwriting work."""
    from loopx.todos import list_goal_todos
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime = tmp_path / "runtime"
    state = tmp_path / "state.md"
    state.write_text("# Goal\n\n## Agent Todo\n\n")
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{
        "id": "goal-a", "repo": str(tmp_path), "state_file": "state.md",
        "coordination": {"registered_agents": ["alpha"], "agent_model": "peer_v1"}}]}))
    service = ChatActionService(store=ChatActionStore(runtime / "chat/actions"), registry_path=registry)
    plan = _agent_plan()
    preview = service.preview({"action_kind": "team.plan", "summary": "Assign work", "context": {},
        "normalized_parameters": {"goal_id": "goal-a", "plan": plan}, "idempotency_key": "legacy-plan"})
    applied = service.apply(preview["proposal_id"])["proposal"]
    assert applied["status"] == "applied", applied
    assert "loopx:team-plan:" in state.read_text()
    rows = list_goal_todos(registry_path=registry, goal_id="goal-a")["todos"]
    projection = build_todo_runtime_shadow_projection(goal_id="goal-a", todos=rows, handoff_mode="soft_claim")
    initialize_canonical_authority(runtime, "goal-a", projection, state_path=state, provider=provider)
    invoke = _capture_free_chat((runtime, state, service, preview), tmp_path)
    later = invoke("preview", text="New work after import")
    assert invoke("apply", proposal=later["proposal_id"])["proposal"]["status"] == "applied"
    current = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")
    display = state.read_bytes()
    replay = invoke("historical")
    assert replay["action"] == "reused"
    assert replay["lane_todo_ids"] == applied["receipt"]["resource_ids"]["lane_todo_ids"]
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a") == current
    assert state.read_bytes() == display

    # Simulate promotion finishing after the first mode observation but before
    # acquiring source locks; its selected backend subsequently goes offline.
    from loopx.control_plane.coordination.local_authority import LocalCoordinationAuthorityUnavailable
    from loopx.control_plane.work_items import team_plan_adapter
    read_authority = team_plan_adapter.read_canonical_todos_if_promoted
    observed = []

    def promotion_race(**kwargs):
        observed.append(True)
        return None if len(observed) == 1 else read_authority(**kwargs)

    backend = runtime / "authority" / current["source_authority"].replace("_", "-")
    offline = backend.with_name(backend.name + "-offline")
    backend.rename(offline)
    try:
        with monkeypatch.context() as race:
            race.setattr(team_plan_adapter, "read_canonical_todos_if_promoted", promotion_race)
            saved = service.store.load(preview["proposal_id"])
            with pytest.raises(LocalCoordinationAuthorityUnavailable):
                team_plan_adapter.settle_team_plan(
                    registry_path=registry, goal_id="goal-a", agent_id=None,
                    proposal={**saved["normalized_parameters"]["plan"], "proposal_id": preview["proposal_id"]},
                    expected_state_fingerprint=saved["expected_state_fingerprint"], basis_agent_id="alpha")
            assert len(observed) == 2
        assert state.read_bytes() == display and not backend.exists()
    finally:
        offline.rename(backend)
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a") == current


@pytest.mark.skipif(os.name != "posix", reason="real bootstrap crash barrier uses select on a pipe")
def test_legacy_chat_replay_survives_interrupted_shadow_bootstrap(tmp_path, monkeypatch):
    """A stopped writer must not strand an already-published plan receipt."""
    import select
    from loopx.control_plane.work_items import team_plan_adapter
    from loopx.control_plane.coordination.runtime_shadow import build_runtime_shadow_source_snapshot
    from loopx.control_plane.coordination.shadow_management import read_shadow_management_state
    from loopx.control_plane.coordination.coordination_state_contract_generated import (
        COORDINATION_RUNTIME_SHADOW_BOOTSTRAP_REQUEST_SCHEMA,
    )

    runtime = tmp_path / "runtime"
    state = tmp_path / "state.md"
    state.write_text("# Goal\n\n## Agent Todo\n\n")
    goal = {"id": "goal-a", "repo": str(tmp_path), "state_file": "state.md",
        "coordination": {"registered_agents": ["alpha"], "agent_model": "peer_v1"}}
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [goal]}))
    service = ChatActionService(store=ChatActionStore(runtime / "chat/actions"), registry_path=registry)
    preview = service.preview({"action_kind": "team.plan", "summary": "Assign work", "context": {},
        "normalized_parameters": {"goal_id": "goal-a", "plan": _agent_plan()},
        "idempotency_key": "lost-original-response"})
    write = team_plan_adapter.write_captured_todo_state

    def lose_response(*args, **kwargs):
        write(*args, **kwargs)
        raise OSError("response lost after publication")

    with monkeypatch.context() as failed_response:
        failed_response.setattr(team_plan_adapter, "write_captured_todo_state", lose_response)
        assert service.apply(preview["proposal_id"])["proposal"]["status"] == "failed"
    assert state.read_text().count("loopx:team-plan:") == 1
    state.write_text(state.read_text() + "\nLater owner data must survive.\n")
    original = state.read_bytes()
    projection, snapshot = build_runtime_shadow_source_snapshot(
        goal=goal, runtime_root=runtime, state_path=state, registry_path=registry)
    request = {"schema_version": COORDINATION_RUNTIME_SHADOW_BOOTSTRAP_REQUEST_SCHEMA,
        "runtime_root": str(runtime), "goal_id": "goal-a", "operation_id": "bootstrap:plan-recovery",
        "source_version": "source:before-crash", "projection": projection, "source_snapshot": snapshot}
    repo = Path(__file__).resolve().parents[2]
    child = subprocess.Popen(["node", "--no-warnings", "--experimental-strip-types",
        str(repo / "tests/control_plane_ts/fixtures/shadow_management_crash_worker.ts"),
        "bootstrap-public", json.dumps(request), "bootstrap_prepared"],
        cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        ready, _, _ = select.select([child.stdout], [], [], 15)
        assert ready, "bootstrap did not reach its real filesystem barrier"
        assert child.stdout.readline().strip() == "ready:bootstrap_prepared"
    finally:
        child.kill()
        child.communicate(timeout=10)
    pending = read_shadow_management_state(runtime, "goal-a")
    assert pending["status"] == "bootstrapping"
    # Restart from an isolated package with the old capture producer absent.
    invoke = _capture_free_chat((runtime, state, service, preview), tmp_path)
    replay = invoke("historical")
    assert replay["action"] == "reused" and replay["created_todo_ids"] == []
    recovered = service.apply(preview["proposal_id"])["proposal"]
    assert recovered["status"] == "applied", recovered
    assert replay["lane_todo_ids"] == recovered["receipt"]["resource_ids"]["lane_todo_ids"]
    assert state.read_bytes() == original
    assert read_shadow_management_state(runtime, "goal-a") == pending

    # Recovery is tied to the exact intent, actor and original reviewed basis.
    saved = service.store.load(preview["proposal_id"])
    original_plan = {**saved["normalized_parameters"]["plan"], "proposal_id": preview["proposal_id"]}
    for changed in (
        {"proposal": {**original_plan, "objective": "Changed intent"}},
        {"agent_id": "alpha"},
        {"expected_state_fingerprint": "different-basis"},
    ):
        with pytest.raises(team_plan_adapter.EffectRuntimeRejected, match="identity mismatch"):
            team_plan_adapter.settle_team_plan(**{
                "registry_path": registry, "goal_id": "goal-a", "agent_id": None,
                "proposal": original_plan, "expected_state_fingerprint": saved["expected_state_fingerprint"],
                "basis_agent_id": "alpha", **changed,
            })
        assert state.read_bytes() == original
    # A different plan still needs normal admission; recovery cannot restart work.
    fresh = service.preview({"action_kind": "team.plan", "summary": "New work", "context": {},
        "normalized_parameters": {"goal_id": "goal-a", "plan": _agent_plan()},
        "idempotency_key": "new-request-during-hold"})
    refused = service.apply(fresh["proposal_id"])["proposal"]
    assert refused["status"] == "failed", refused
    assert state.read_bytes() == original
    assert read_shadow_management_state(runtime, "goal-a") == pending
