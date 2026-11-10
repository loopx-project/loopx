"""File/SQLite -> real CLI quota -> external worker, with only Host work faked."""
from contextlib import contextmanager
from copy import deepcopy
from http.client import HTTPConnection
import json
from pathlib import Path
import shlex
import sys
from threading import Thread

import pytest

from test_replan_successor_guard_reentry import AGENT, GOAL, TURN, _add, _fixture, _guard
from test_external_scheduler_worker import _args, _guarded_host, worker
from loopx.cli_commands.quota_presentation import project_scheduler_observation
from loopx.extensions.process_runtime import CappedProcessResult
from loopx.history import collect_history


def _admitted_replan_for_worker(quota, command, turn, **kwargs):
    # These recovery cases supply a receipt from an explicitly admitted Host
    # Turn. A normal scheduler observation must not commit that admission.
    assert command[-1] == "--scheduler-observation"
    bound = quota([*command[:-1], "--context-delivery", "off", "--include-detail", "scheduler",
                   "--turn-instance-id", turn], **kwargs)
    return project_scheduler_observation(bound)


def _worker_readback(args):
    history = collect_history(registry_path=Path(args.registry), runtime_root=Path(args.runtime_root),
                              goal_id=GOAL, limit=1)
    return next(goal for goal in history["goals"] if goal["id"] == GOAL)["host_worker_observations"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("damage", [b"{broken", b"\xff", b'{"schema_version":"other"}'])
def test_ordinary_cli_poll_preserves_unreadable_unknown_operation(tmp_path, monkeypatch, provider, damage):
    call, runtime, _ = _fixture(tmp_path, monkeypatch, provider, future_monitor=False)
    args = _args(fake_cli=Path(sys.executable).parent / "loopx",
        state_file=tmp_path / "worker-state.json", wake_cmd="controlled-failure", quota_timeout_seconds=90)
    args.registry, args.runtime_root = str(tmp_path / "registry.json"), str(runtime)
    args.goal_id, args.agent_id = GOAL, AGENT
    dispatches = []
    monkeypatch.setattr(worker, "_run_wake", lambda *a, **kw: dispatches.append(1) or
        CappedProcessResult(returncode=1, stdout=b""))
    assert worker.run_worker(args) != 0
    assert _worker_readback(args)["rows"][0]["status"] == "replan_outcome_unknown"
    from loopx.control_plane.quota.host_poll_receipts import receipt_path_for_goal
    path = receipt_path_for_goal({"id": GOAL}, tmp_path / "project/ACTIVE_GOAL_STATE.md")
    path.write_bytes(damage)
    for _ in range(2):
        packet = call("quota", "should-run", "--goal-id", GOAL, "--agent-id", AGENT, "--record-host-poll")
        assert packet["host_poll_receipt"]["recorded"] is False
        assert "unreadable" in packet["host_poll_receipt"]["error"]
        assert path.read_bytes() == damage
        assert _worker_readback(args)["read_status"] == "unreadable"
    assert json.loads(Path(args.state_file).read_text())["replan_attempts"][0]["status"] == "unknown"
    assert dispatches == [1]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("uppercase", [False, True])
def test_real_unresolved_replan_dispatches_once_across_return_and_restart(tmp_path, monkeypatch, provider, uppercase):
    _, runtime, index = _fixture(tmp_path, monkeypatch, provider, future_monitor=False)
    args = _args(fake_cli=Path(sys.executable).parent / "loopx",
                 state_file=tmp_path / "worker-state.json", wake_cmd="controlled-host-boundary",
                 quota_timeout_seconds=90)
    args.registry, args.runtime_root = str(tmp_path / "registry.json"), str(runtime)
    args.goal_id, args.agent_id, args.once = GOAL, AGENT, False
    if uppercase:
        args.agent_id = AGENT.upper()
    decisions, requests = [], []
    real_quota = worker.run_quota_should_run

    def observe_quota(*command, **kwargs):
        packet = real_quota(*command, **kwargs)
        decisions.append(deepcopy(packet))
        return packet

    def inconclusive_host(*command, **kwargs):
        requests.append({"dispatch": len(requests) + 1, "provider_requests": 1})
        return CappedProcessResult(returncode=0, stdout=b"No new conclusion.\n")

    monkeypatch.setattr(worker, "run_quota_should_run", observe_quota)
    monkeypatch.setattr(worker, "_run_wake", inconclusive_host)
    before = index.read_bytes()
    assert worker.run_worker(args, sleep=lambda _: pytest.fail("active recheck must be immediate")) == 0
    args.agent_id = AGENT
    args.once = True
    for _ in range(2):
        assert worker.run_worker(args) == 0
    assert len(decisions) == 4
    assert len(requests) == 1  # D=1, P=1 in this deterministic Host fixture.
    original = decisions[0]["autonomous_replan_obligation"]["obligation_id"]
    for packet in decisions:
        assert worker.parse_tick(packet).should_run
        assert packet["effective_action"] == "autonomous_replan_required"
        assert packet["autonomous_replan_obligation"]["obligation_id"] == original
        assert not packet.get("goal_ref"), "the executable legacy entry must not acquire a forged lifetime"
    state = json.loads(Path(args.state_file).read_text())
    assert state["replan_attempts"][0]["obligation_id"] == original
    assert state["replan_attempts"][0]["status"] == "returned"
    assert index.read_bytes() == before  # E=0 and Q=0: rc=0 never became an ACK or spend.
    readback = _worker_readback(args)
    assert readback["read_status"] == "read"
    assert readback["rows"][0]["status"] == "replan_paused"
    assert readback["rows"][0]["obligation_id"] == original
    assert readback["rows"][0]["turn_instance_id"] is None


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_real_original_receipt_recovery_unblocks_next_work_without_repeating_replan(tmp_path, monkeypatch, provider):
    call, runtime, index = _fixture(tmp_path, monkeypatch, provider, future_monitor=False)
    args = _args(fake_cli=Path(sys.executable).parent / "loopx", state_file=tmp_path / "worker-state.json",
                 wake_cmd="controlled-original-turn", quota_timeout_seconds=90)
    args.registry, args.runtime_root = str(tmp_path / "registry.json"), str(runtime)
    args.goal_id, args.agent_id = GOAL, AGENT
    real_quota = worker.run_quota_should_run
    current_turn = [TURN]
    decisions, requests, recovery = [], [], {}

    def scoped_quota(command, **kwargs):
        # This Host fixture explicitly preserves its original admitted Turn.
        # An ordinary opaque unbound shell command has no such guarantee.
        packet = _admitted_replan_for_worker(real_quota, command, current_turn[0], **kwargs)
        decisions.append(deepcopy(packet))
        return packet

    def original_result_lost(*command, **kwargs):
        identity = decisions[-1]["heartbeat_receipt"]["settlement_identity"]
        requests.append(identity["replan_obligation_id"])
        successor = _add(call, identity["replan_obligation_id"])
        original_pending = _guard(call)
        actions = original_pending["interaction_contract"]["cli_channel"]["next_cli_actions"]
        refreshed = call(*shlex.split(actions[0])[1:], "--no-global-sync", "--suppress-external-sinks")
        assert refreshed["settlement_progress"]["state"] == "spend_required"
        recovery.update(spend=actions[1], successor=successor["todo_id"], identity=identity)
        return CappedProcessResult(returncode=1, stdout=b"")  # Effect happened; outer ACK was lost.

    monkeypatch.setattr(worker, "run_quota_should_run", scoped_quota)
    monkeypatch.setattr(worker, "_run_wake", original_result_lost)
    assert worker.run_worker(args) != 0
    assert len(requests) == 1
    readback = _worker_readback(args)
    assert readback["read_status"] == "read"
    assert readback["rows"][0]["status"] == "replan_outcome_unknown"
    assert readback["rows"][0]["turn_instance_id"] == TURN
    current_turn[0] = "current-work-after-lost-ack"
    # A changed current Turn / frontier cannot resolve the original debt.
    assert worker.run_worker(args) != 0
    assert len(requests) == 1
    # Existing non-model recovery acts on the exact old receipt, never the new Turn.
    spent = call(*shlex.split(recovery["spend"])[1:])
    assert spent["settlement_progress"]["state"] == "settled"
    assert spent["appended"] is True
    assert call(*shlex.split(recovery["spend"])[1:])["appended"] is False
    monkeypatch.setattr(worker, "_run_wake", lambda *a, **kw: requests.append("next-work") or
                        CappedProcessResult(returncode=0, stdout=b""))
    assert worker.run_worker(args) == 0
    assert requests == [recovery["identity"]["replan_obligation_id"], "next-work"]
    assert decisions[-1]["selected_todo"]["todo_id"] == recovery["successor"]
    state = json.loads(Path(args.state_file).read_text())
    assert state["replan_attempts"][0]["recovery"] == "original_settlement_verified"
    assert state["replan_attempts"][0]["settlement_identity"] == recovery["identity"]
    rows = [json.loads(line) for line in index.read_text().splitlines()]
    assert sum(row.get("classification") == "quota_slot_spent" for row in rows) == 1
    assert _worker_readback(args) is None


@contextmanager
def _worker_http_readback(args, *, registry_path=None):
    """Read the shipped Chat status route with its real registry/status owner."""
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler

    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.registry_path = registry_path or Path(args.registry)
    server.runtime_root = Path(args.runtime_root)
    server.runtime_root_override = args.runtime_root
    server.scan_roots = [Path(args.state_file).parent / "project"]
    server.limit, server.selected_goal_id, server.verbose = 1, GOAL, False
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def read():
        connection = HTTPConnection(*server.server_address, timeout=90)
        try:
            connection.request("GET", f"/status.json?goal_id={GOAL}")
            response = connection.getresponse()
            payload = json.loads(response.read())
            assert response.status == 200, payload
            return next(row for row in payload["run_history"]["goals"] if row["id"] == GOAL)["host_worker_observations"]
        finally:
            connection.close()

    try:
        yield read
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_real_worker_main_uses_the_core_canonical_agent_across_case_changes(tmp_path, monkeypatch, provider):
    call, runtime, index = _fixture(tmp_path, monkeypatch, provider, future_monitor=False)
    packet = call("quota", "should-run", "--goal-id", GOAL, "--agent-id", AGENT.upper(),
                  "--runtime-profile", "generic_cli", "--include-detail", "scheduler")
    assert packet["should_run"] is True
    assert packet["agent_identity"]["agent_id"] == AGENT
    requests = []
    monkeypatch.setattr(worker, "_run_wake", lambda *a, **kw: requests.append(1) or
                        CappedProcessResult(returncode=1, stdout=b""))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "worker-state"))
    argv = ["--cli-bin", str(Path(sys.executable).parent / "loopx"),
            "--registry", str(tmp_path / "registry.json"), "--runtime-root", str(runtime),
            "--goal-id", GOAL, "--wake-cmd", "controlled-host-boundary", "--once",
            "--quota-timeout-seconds", "90"]
    before = index.read_bytes()
    assert worker.main([*argv, "--agent-id", AGENT.upper()]) == 1
    # Case is spelling, not permission to dispatch another unresolved effect.
    assert worker.main([*argv, "--agent-id", AGENT]) == 2
    assert requests == [1]
    states = list((tmp_path / "worker-state" / "loopx" / "external-worker").glob("*.json"))
    assert len(states) == 1
    attempt = json.loads(states[0].read_text())["replan_attempts"][0]
    assert attempt["agent_id"] == AGENT
    assert attempt["status"] == "unknown"
    assert index.read_bytes() == before
    args = _args(fake_cli=Path(sys.executable).parent / "loopx", state_file=states[0])
    args.registry, args.runtime_root = str(tmp_path / "registry.json"), str(runtime)
    readback = _worker_readback(args)
    assert readback["read_status"] == "read"
    assert [row["agent_id"] for row in readback["rows"]] == [AGENT]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_real_ordinary_poll_cannot_replace_an_unreadable_worker_receipt(tmp_path, monkeypatch, provider):
    from loopx.control_plane.quota.host_poll_receipts import receipt_path_for_goal

    call, runtime, index = _fixture(tmp_path, monkeypatch, provider, future_monitor=False)
    args = _args(fake_cli=Path(sys.executable).parent / "loopx", state_file=tmp_path / "worker.json",
                 wake_cmd="controlled-host-boundary", quota_timeout_seconds=90)
    args.registry, args.runtime_root = str(tmp_path / "registry.json"), str(runtime)
    args.goal_id, args.agent_id = GOAL, AGENT
    requests = []
    monkeypatch.setattr(worker, "_run_wake", lambda *a, **kw: requests.append(1) or
                        CappedProcessResult(returncode=1, stdout=b""))
    before = index.read_bytes()
    assert worker.run_worker(args) == 1
    path = receipt_path_for_goal({"id": GOAL}, tmp_path / "project" / "ACTIVE_GOAL_STATE.md")
    original_receipt, original_attempt = path.read_bytes(), Path(args.state_file).read_bytes()
    assert _worker_readback(args)["rows"][0]["status"] == "replan_outcome_unknown"
    schema_damage = {**json.loads(original_receipt), "schema_version": "invalid"}
    with _worker_http_readback(args) as read_http:
        initial = read_http()
        assert initial == _worker_readback(args)
        for corrupt in (b"{broken", b"\xff", json.dumps(schema_damage).encode()):
            path.write_bytes(corrupt)
            assert read_http()["read_status"] == "unreadable"
            poll = call("quota", "should-run", "--goal-id", GOAL, "--agent-id", AGENT,
                        "--runtime-profile", "generic_cli", "--record-host-poll")
            assert poll["ok"] is True
            assert poll["host_poll_receipt"]["recorded"] is False
            assert path.read_bytes() == corrupt
            assert read_http() == {"goal_id": GOAL, "read_status": "unreadable", "rows": []}
            assert Path(args.state_file).read_bytes() == original_attempt
        # Restoring the original observation repairs readback without inventing
        # a successful Host outcome, altering the attempt, or spending a slot.
        path.write_bytes(original_receipt)
        assert read_http() == initial
    assert requests == [1]
    assert index.read_bytes() == before


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("worker_route", ["source", "global"])
def test_real_worker_observation_survives_source_global_materialization(tmp_path, monkeypatch, provider, worker_route):
    from loopx.global_registry import sync_project_registry_to_global

    _, runtime, index = _fixture(tmp_path, monkeypatch, provider, future_monitor=False)
    source = tmp_path / "registry.json"

    def sync():
        result = sync_project_registry_to_global(registry_path=source, runtime_root_override=str(runtime), goal_id=GOAL)
        assert result["ok"] is True
        return Path(result["global_registry"])

    mirror = sync()
    args = _args(fake_cli=Path(sys.executable).parent / "loopx", state_file=tmp_path / "worker.json",
                 wake_cmd="controlled-host-boundary", quota_timeout_seconds=90)
    args.registry, args.runtime_root = str(source if worker_route == "source" else mirror), str(runtime)
    args.goal_id, args.agent_id = GOAL, AGENT
    requests = []
    monkeypatch.setattr(worker, "_run_wake", lambda *a, **kw: requests.append(1) or
                        CappedProcessResult(returncode=1, stdout=b""))
    before = index.read_bytes()
    assert worker.run_worker(args) == 1
    original_attempt = Path(args.state_file).read_bytes()

    def read(registry):
        return collect_history(registry_path=registry, runtime_root=runtime, goal_id=GOAL,
                               limit=1)["goals"][0]["host_worker_observations"]

    source_read = read(source)
    assert source_read["read_status"] == "read"
    assert source_read["rows"][0]["status"] == "replan_outcome_unknown"
    assert read(mirror) == source_read
    # A real no-op sync may change its materialization timestamp, not the
    # original worker's source, runtime, obligation or effect outcome.
    with _worker_http_readback(args, registry_path=mirror) as read_http:
        assert read_http() == source_read
        sync()
        assert read_http() == source_read
        assert read(mirror) == source_read
    assert read(source) == source_read
    assert Path(args.state_file).read_bytes() == original_attempt
    assert requests == [1]
    assert index.read_bytes() == before


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_real_quota_retry_preserves_original_obligation_without_settlement(tmp_path, monkeypatch, provider):
    _, runtime, history = _fixture(tmp_path, monkeypatch, provider, future_monitor=False)
    args, count = _guarded_host(tmp_path, kind="provider_capacity", recover_at=2)
    args.cli_bin = str(Path(sys.executable).parent / "loopx")
    args.registry, args.runtime_root = str(tmp_path / "registry.json"), str(runtime)
    args.goal_id, args.agent_id, args.quota_timeout_seconds = GOAL, AGENT, 90
    now, delays = [1000.0], []
    def sleep(seconds):
        delays.append(seconds)
        now[0] += seconds
    before = history.read_bytes()
    # Real CLI admission and authority stores; only the model process is synthetic.
    # Its second response succeeds without semantically settling the replan.
    assert worker.run_worker(args, sleep=sleep, clock=lambda: now[0]) == 0
    assert count.read_text() == "2"
    assert delays == [30.0]
    attempts = json.loads(Path(args.state_file).read_text())["replan_attempts"]
    assert len(attempts) == 1 and attempts[0]["status"] == "returned"
    assert history.read_bytes() == before
    args.once = True
    assert worker.run_worker(args, clock=lambda: now[0]) == 0
    assert count.read_text() == "2"
    assert history.read_bytes() == before


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("recovery", ["fresh", "restart", "missing_proof", "unreadable_proof", "wrong_identity"])
def test_settled_replan_host_failure_allows_only_verified_next_work(tmp_path, monkeypatch, provider, recovery):
    call, runtime, history = _fixture(tmp_path, monkeypatch, provider, future_monitor=False)
    args, _ = _guarded_host(tmp_path, kind="provider_capacity", recover_at=2)
    args.cli_bin = str(Path(sys.executable).parent / "loopx")
    args.registry, args.runtime_root = str(tmp_path / "registry.json"), str(runtime)
    args.goal_id, args.agent_id, args.quota_timeout_seconds, args.once = GOAL, AGENT, 90, True
    real_quota = worker.run_quota_should_run
    turn, decisions, requests, original = [TURN], [], [], {}

    def quota(command, **kwargs):
        packet = _admitted_replan_for_worker(real_quota, command, turn[0], **kwargs)
        decisions.append(packet)
        return packet

    session = {"binding_scope": "agent", "session_id": "session-a"}

    def host(*command, **kwargs):
        requests.append(kwargs.get("environment", {}).get("LOOPX_HOST_RETRY_SESSION_ID"))
        if len(requests) == 1:
            identity = decisions[-1]["heartbeat_receipt"]["settlement_identity"]
            successor = _add(call, identity["replan_obligation_id"])
            actions = _guard(call)["interaction_contract"]["cli_channel"]["next_cli_actions"]
            refreshed = call(*shlex.split(actions[0])[1:], "--no-global-sync", "--suppress-external-sinks")
            assert refreshed["settlement_progress"]["state"] == "spend_required"
            spent = call(*shlex.split(actions[1])[1:])
            assert spent["appended"] is True and spent["settlement_progress"]["state"] == "settled"
            original.update(identity=identity, successor=successor["todo_id"], spend=actions[1])
            receipt = {"ok": False, "session": session, "host_failure": {
                "schema_version": "loopx_turn_host_failure_v0", "kind": "provider_capacity",
                "attempt": 1, "retryable": True, "retry": {
                    "strategy": "same_configuration", "max_attempts": 3, "backoff_seconds": 30}}}
            return CappedProcessResult(returncode=1, stdout=json.dumps(receipt).encode())
        assert decisions[-1]["selected_todo"]["todo_id"] == original["successor"]
        return CappedProcessResult(returncode=0, stdout=json.dumps({"ok": True, "session": session}).encode())

    monkeypatch.setattr(worker, "run_quota_should_run", quota)
    monkeypatch.setattr(worker, "_run_wake", host)
    assert worker.run_worker(args, clock=lambda: 1000.0) == 1
    turn[0] = "next-work-after-settled-replan"
    # The transport retry keeps its original backoff even though Core settled.
    assert worker.run_worker(args, clock=lambda: 1001.0) == 2
    assert requests == [""]

    if recovery != "fresh":
        real_save = worker._save_state

        def interrupt_after_proof(path, state, **kwargs):
            real_save(path, state, **kwargs)
            if any(a.get("recovery") == "original_settlement_verified" for a in state.get("replan_attempts", [])):
                raise OSError("synthetic interruption after durable recovery proof")

        with monkeypatch.context() as interrupted:
            interrupted.setattr(worker, "_save_state", interrupt_after_proof)
            assert worker.run_worker(args, clock=lambda: 1031.0) == 2
        assert requests == [""]
        if recovery == "missing_proof":
            monkeypatch.setattr(worker, "read_heartbeat_settlement", lambda *a, **kw: None)
        elif recovery == "unreadable_proof":
            def unavailable(*a, **kw):
                raise OSError("synthetic original authority unavailable")
            monkeypatch.setattr(worker, "read_heartbeat_settlement", unavailable)
        elif recovery == "wrong_identity":
            from dataclasses import replace
            real_read = worker.read_heartbeat_settlement

            def conflicting(*a, **kw):
                proof = real_read(*a, **kw)
                return replace(proof, identity=replace(proof.identity,
                    value=replace(proof.identity.value, turn_instance_id="different-original-turn")))

            monkeypatch.setattr(worker, "read_heartbeat_settlement", conflicting)

    allowed = recovery in {"fresh", "restart"}
    assert worker.run_worker(args, clock=lambda: 1031.0) == (0 if allowed else 2)
    assert requests == (["", "session-a"] if allowed else [""])
    state = json.loads(Path(args.state_file).read_text())
    assert len(state["replan_attempts"]) == 1
    attempt = state["replan_attempts"][0]
    assert attempt["status"] == "returned" and attempt["recovery"] == "original_settlement_verified"
    assert attempt["settlement_identity"] == original["identity"]
    if allowed:
        assert "host_attempt" not in state
    else:
        assert state["host_attempt"]["attempt"] == 1
    rows = [json.loads(line) for line in history.read_text().splitlines()]
    assert sum(row.get("classification") == "quota_slot_spent" for row in rows) == 1
    assert call(*shlex.split(original["spend"])[1:])["appended"] is False
