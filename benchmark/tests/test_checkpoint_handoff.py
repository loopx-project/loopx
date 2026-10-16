"""Private source rendezvous never substitutes a late or foreign point."""
import hashlib
import json
import os
import sys
import threading
import time

import pytest

from benchmark.runtime.checkpoint_handoff import await_controller_capture


@pytest.mark.parametrize("response", ["captured", "failed", "foreign", "timeout"])
def test_original_request_only_and_cleanup_after_missed_point(tmp_path, response):
    mailbox = tmp_path / "checkpoint-controller"
    ready = mailbox / "ready.json"
    observed = []
    def controller():
        until = time.time() + 2
        while not ready.exists() and time.time() < until:
            time.sleep(.005)
        raw = ready.read_bytes()
        request = json.loads(raw)  # Publication exposes a complete request.
        observed.append(request)
        if response == "timeout":
            return
        result = {"request_sha256": hashlib.sha256(raw).hexdigest(),
                  "ok": response == "captured", "error_kind": "SyntheticFailure",
                  "capture": {"research_point_qualified": False, "sha256": "retained"}}
        if response == "foreign":
            result["request_sha256"] = "another-point"
        pending = mailbox / "reply.tmp"
        pending.write_text(json.dumps(result))
        pending.rename(mailbox / (request["capture_id"] + ".json"))
    thread = threading.Thread(target=controller)
    thread.start()
    try:
        args = (tmp_path / "checkpoints", [{"id": "code", "source_path": "/task/code"}], {},
                {"source_sha256": "prepared", "source_witness": {}}, time.time() + .5)
        if response == "captured":
            assert await_controller_capture(*args)["sha256"] == "retained"
        else:
            with pytest.raises(TimeoutError if response == "timeout" else ValueError):
                await_controller_capture(*args, phase_deadline=args[-1])
    finally:
        thread.join(3)
    assert not thread.is_alive() and not ready.exists() and len(observed) == 1


def test_surviving_request_is_not_overwritten(tmp_path):
    mailbox = tmp_path / "checkpoint-controller"
    mailbox.mkdir()
    ready = mailbox / "ready.json"
    ready.write_bytes(b"original uncertain request")
    with pytest.raises(FileExistsError):
        await_controller_capture(tmp_path / "checkpoints", [], {},
            {"source_sha256": "later", "source_witness": {}}, time.time() + 1)
    assert ready.read_bytes() == b"original uncertain request"


@pytest.mark.parametrize("response", ["captured", "failed", "foreign", "missing", "phase_expired"])
def test_sealed_reply_after_capture_deadline_is_bounded_and_bound(tmp_path, monkeypatch, response):
    from types import SimpleNamespace
    from benchmark.runtime import checkpoint_handoff as handoff

    clock = [100.0]
    mailbox = tmp_path / "checkpoint-controller"
    ready = mailbox / "ready.json"
    published = []
    def advance(seconds):
        clock[0] += seconds
        if clock[0] < 100.25 or published or response in {"missing", "phase_expired"}:
            return
        raw = ready.read_bytes()
        request = json.loads(raw)
        # The extra completion time must not leak into the writer barrier.
        assert request["deadline_epoch"] == 100.1
        result = {"request_sha256": "foreign" if response == "foreign" else hashlib.sha256(raw).hexdigest(),
                  "ok": response == "captured", "error_kind": "SealingFailed",
                  "capture": {"sha256": "sealed-original", "research_point_qualified": False}}
        (mailbox / (request["capture_id"] + ".json")).write_text(json.dumps(result))
        published.append(True)
    monkeypatch.setattr(handoff, "time", SimpleNamespace(time=lambda: clock[0], sleep=advance))
    monkeypatch.setattr(handoff, "CONTROLLER_SEAL_SECONDS", .5)
    args = (tmp_path / "checkpoints", [], {}, {"source_sha256": "prepared", "source_witness": {}}, 100.1)
    phase = 100.2 if response == "phase_expired" else 1000
    if response == "captured":
        assert handoff.await_controller_capture(*args, phase_deadline=phase)["sha256"] == "sealed-original"
    else:
        with pytest.raises(TimeoutError if response in {"missing", "phase_expired"} else ValueError):
            handoff.await_controller_capture(*args, phase_deadline=phase)
    assert 100.1 < clock[0] < (100.3 if response == "phase_expired" else 100.7)
    assert not ready.exists()


@pytest.mark.parametrize("capture,phase", [(float("nan"), None), (float("inf"), None),
    (100, float("nan")), (100, float("inf"))])
def test_controller_completion_requires_finite_clocks(capture, phase):
    from benchmark.runtime.checkpoint_handoff import controller_completion_deadline
    with pytest.raises(ValueError, match="finite"):
        controller_completion_deadline(capture, phase)


@pytest.mark.parametrize("hook", ["settled", "interrupted"])
@pytest.mark.parametrize("phase_seconds", [None, 40, 20])
def test_capture_preparation_uses_one_window_without_renewing_phase(tmp_path, monkeypatch, hook, phase_seconds):
    """A mature witness may take >30s; that grants no new phase or retry clock."""
    from contextlib import contextmanager
    from types import SimpleNamespace
    from benchmark.runtime import checkpoint, checkpoint_wake

    clock = [100.0]
    timer = SimpleNamespace(time=lambda: clock[0], monotonic=lambda: clock[0])
    monkeypatch.setattr(checkpoint, "time", timer)
    monkeypatch.setattr(checkpoint_wake, "time", timer)
    code = tmp_path / "code"
    code.write_text("retained source")
    env = {"LOOPX_CHECKPOINT_REQUEST": "explicit declaration", "LOOPX_WAKE_LOG_DIR": str(tmp_path / "wakes"),
        "LOOPX_CLI": "loopx", "LOOPX_PROJECT": str(tmp_path),
        "LOOPX_RUNTIME_ROOT": str(tmp_path / "runtime"), "LOOPX_REGISTRY": str(tmp_path / "registry"),
        "LOOPX_GOAL_ID": "source", "LOOPX_AGENT_ID": "agent"}
    if phase_seconds is not None:
        env["LOOPX_PHASE_DEADLINE_EPOCH"] = str(100 + phase_seconds)
    request = {"destination": str(tmp_path / "checkpoints" / "point"),
        "components": [{"id": "code", "source_path": str(code)}], "point_metadata": {},
        "controller_handoff": True, "loopx_state": True, "native_session": True, "evaluation_opportunity": True}
    receipt = {"ok": True, "stage": "execute", "mode": "heartbeat", "turn_id": "turn",
        "session": {"session_id": "native-session"}}
    binding = SimpleNamespace(session_id="native-session", goal_ref="source", digest="profile")
    monkeypatch.setattr(checkpoint, "research_participants", lambda env, components, *a: (components, {}))
    monkeypatch.setattr(checkpoint, "verify_research_participants", lambda *a: None)
    monkeypatch.setattr(checkpoint, "native_binding_participant", lambda env, components, *a: (components, binding))
    monkeypatch.setattr("benchmark.runtime.sessions.BenchmarkSessionWake", lambda *a, **k: binding)
    monkeypatch.setattr("benchmark.runtime.checkpoint_continuation.restored_capture_history", lambda *a, **k: {})
    @contextmanager
    def reader(*a, **k):
        yield object()
    monkeypatch.setattr("loopx.hosts.codex_checkpoint.native_checkpoint_reader", reader)
    monkeypatch.setattr("loopx.hosts.codex_checkpoint.native_session_checkpoint",
        lambda *a, **k: {"components": [], "metadata": {"store_members": []}})
    deadlines = []
    def artifact(method, params, **kwargs):
        deadline = params["deadline_epoch_ms"] / 1000
        deadlines.append(deadline)
        if params["action"] == "probe":
            clock[0] += 35  # Complete retained witness, rather than trimming history.
        if clock[0] >= deadline:
            raise TimeoutError("original capture window expired")
        return {"source_sha256": "witness", "source_witness": {}, "sha256": "sealed-original"}
    def controller(root, components, metadata, probe, deadline, **kwargs):
        deadlines.append(deadline)
        if clock[0] >= deadline:
            raise TimeoutError("original capture window expired")
        return {"sha256": "sealed-original"}
    monkeypatch.setattr("benchmark.runtime.checkpoint_handoff.await_controller_capture", controller)
    monkeypatch.setattr(checkpoint, "effect_runtime_result", artifact)
    monkeypatch.setattr(checkpoint_wake, "effect_runtime_result", artifact)
    if hook == "settled":
        observed = SimpleNamespace(progress={"state": "settled"},
            identity=SimpleNamespace(value=SimpleNamespace(effect_id="original-effect")))
        monkeypatch.setattr(checkpoint, "read_heartbeat_settlement", lambda *a, **k: observed)
        checkpoint.capture_after_wake(env, receipt, supplied_request=(request, b"declared"))
        assert receipt["checkpoint"]["captured"] is (phase_seconds != 20)
        assert receipt["ok"] is True  # An optional miss cannot replay successful work.
    else:
        wake = checkpoint_wake.CheckpointWake(env, receipt)
        wake.request, wake.raw = request, b"declared"
        held = {"intent": {"obligation_id": "replan-fixture"}, "sha256": "original-guard",
            "path": code, "quota": {"heartbeat_receipt": {"turn_instance_id": "turn"}}}
        phase = float("inf") if phase_seconds is None else 100 + phase_seconds
        if phase_seconds == 20:
            with pytest.raises(TimeoutError, match="original capture window"):
                wake._capture(held, phase)
        else:
            assert wake._capture(held, phase)["sha256"] == "sealed-original"
    expected = 190 if phase_seconds is None else 100 + phase_seconds
    assert deadlines and all(deadline == expected for deadline in deadlines)
    assert code.read_text() == "retained source"


@pytest.mark.skipif(sys.platform != "linux", reason="Qualifies the Linux writer process group")
@pytest.mark.parametrize("use_selector,closeout,settlement_only", [
    (False, False, False), (True, False, False), (True, True, False), (True, False, True),
])
def test_non_capture_guard_returns_to_original_writer_without_interrupt(tmp_path, use_selector, closeout, settlement_only):
    """Optional capture preserves quota recovery and pending Turn closeout."""
    from pathlib import Path
    import socket
    import sys
    import tempfile

    from benchmark.runtime.checkpoint import held_quota_decision
    from benchmark.runtime.worker import child_process
    project = tmp_path / "source"
    project.mkdir()
    capture_root = project / "captures"
    capture_root.mkdir(mode=0o700)
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])}
    with tempfile.TemporaryDirectory(prefix="failed-guard-", dir="/tmp") as short:
        socket_path = Path(short).resolve() / "host.sock"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(socket_path))
            listener.listen(1)
            listener.settimeout(30)
            failed = {"ok": False, "mode": "should-run", "goal_id": "failed-source",
                      "decision": "skip", "should_run": False,
                      "error_code": "quota_unexpected_collection_error"}
            if closeout:
                failed = closeout_guard("failed-source", "failed-agent", "failed-guard")
            elif settlement_only:
                failed = replan_settlement_guard("failed-source", "failed-agent", "failed-guard")
            packet = capture_root / "original"
            packet.mkdir()
            decision = packet / "decision.json"
            raw = json.dumps(failed).encode()
            decision.write_bytes(raw)
            # The transport must release this failed packet without invoking
            # capture admission or interrupting the independent original writer.
            code = (
                "import socket,json; from pathlib import Path; "
                "s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); "
                f"s.connect({str(socket_path)!r}); "
                f"s.sendall({(json.dumps({'path': str(decision), 'sha256': hashlib.sha256(raw).hexdigest()}).encode() + bytes([10]))!r}); "
                "d=json.loads(s.makefile('rb').readline()); assert d['released'] is True; "
                f"assert d['sha256']=={hashlib.sha256(raw).hexdigest()!r}; "
                f"Path('observed-failure.json').write_bytes(Path({str(decision)!r}).read_bytes()); "
                "Path('source-work').write_text('original writer recovered')"
            )
            with child_process([sys.executable, "-c", code], env={**env, "LOOPX_PROJECT": str(project)},
                               stdout=None, stderr=None) as process:
                process.stdin.close()
                with held_quota_decision(listener, capture_root, goal_id="failed-source",
                        agent_id="failed-agent", turn_instance_id="failed-guard",
                        native_process=process,
                        interrupt_when=(lambda _: False) if use_selector else None) as held:
                    assert held["quota"]["ok"] is (closeout or settlement_only)
                    assert held["intent"]["capture"] is False
                    assert held["intent"]["execution_authority_granted"] is False
                    assert "native_process_return_code" not in held
                    assert process.poll() is None and not (project / "source-work").exists()
                assert process.wait(timeout=30) == 0
            assert (project / "source-work").read_text() == "original writer recovered"
            assert (project / "observed-failure.json").read_bytes().strip() == raw
            assert held["path"].read_bytes() == raw


@pytest.mark.skipif(os.name != "posix", reason="Requires the Host Unix rendezvous")
@pytest.mark.parametrize("ok,goal", [(False, "foreign"), ("false", "source"), (None, "source")])
def test_failure_dispatch_does_not_relax_foreign_or_malformed_readback(tmp_path, ok, goal):
    import socket
    import tempfile
    from pathlib import Path

    from benchmark.runtime.checkpoint import held_quota_decision
    from loopx.control_plane.effect_runtime import EffectRuntimeRemoteError

    root = tmp_path / "decisions"
    packet = root / "original"
    packet.mkdir(parents=True)
    decision = packet / "decision.json"
    raw = json.dumps({"ok": ok, "goal_id": goal}).encode()
    decision.write_bytes(raw)
    responses = []
    with tempfile.TemporaryDirectory(prefix="invalid-guard-", dir="/tmp") as short:
        address = str(Path(short).resolve() / "host.sock")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(address)
            listener.listen(1)

            def caller():
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
                    channel.connect(address)
                    channel.sendall(json.dumps({"path": str(decision),
                        "sha256": hashlib.sha256(raw).hexdigest()}).encode() + b"\n")
                    responses.append(channel.recv(4096))

            thread = threading.Thread(target=caller)
            thread.start()
            try:
                with pytest.raises(EffectRuntimeRemoteError):
                    with held_quota_decision(listener, root, goal_id="source",
                            agent_id="agent", turn_instance_id="turn"):
                        pytest.fail("invalid readback cannot enter a capture boundary")
            finally:
                thread.join(5)
            assert not thread.is_alive() and responses == [b""]
    assert decision.read_bytes() == raw


def closeout_guard(goal="source", agent="agent", turn="turn"):
    """A completed Todo still owns this Turn while the next replan is visible."""
    todo = "todo_0123456789ab"
    return {"ok": True, "goal_id": goal, "effective_action": "autonomous_replan_required",
        "replan_action_packet": {"schema_version": "replan_action_packet_v0",
            "decision": "replan_required", "obligation_id": "replan-0123456789abcdef"},
        "heartbeat_receipt": {"status": "replayed", "turn_instance_id": turn,
            "closeout_required": True, "settlement_identity": {
                "goal_id": goal, "agent_id": agent, "turn_instance_id": turn, "todo_id": todo,
                "effect_id": f"{goal}:{agent}:{todo}:{turn}"}}}


def replan_settlement_guard(goal="source", agent="agent", turn="turn"):
    """The semantic work is committed; its original replan Turn still owes settlement."""
    replan = "replan-0123456789abcdef"
    return {"ok": True, "goal_id": goal, "effective_action": "autonomous_replan_required",
        "replan_action_packet": {"schema_version": "replan_action_packet_v0",
            "decision": "settlement_pending", "obligation_id": replan, "settlement_only": True},
        "heartbeat_receipt": {"status": "replayed", "turn_instance_id": turn,
            "semantic_replan_obligation_id": replan, "settlement_identity": {
                "goal_id": goal, "agent_id": agent, "turn_instance_id": turn,
                "replan_obligation_id": replan, "binding_kind": "autonomous_replan", "binding_id": replan,
                "effect_id": f"{goal}:{agent}:autonomous_replan:{replan}:{turn}"}}}


def test_closeout_is_not_a_fresh_capture_but_next_bound_replan_is():
    from loopx.control_plane.effect_runtime import effect_runtime_result
    quota = closeout_guard()
    request = {"action": "select_replan", "quota": quota,
               "goal_id": "source", "agent_id": "agent", "turn_instance_id": "turn"}
    result = effect_runtime_result("runtime.checkpoint_artifacts", request)
    assert result == {"capture": False, "reason": "turn_closeout_pending", "execution_authority_granted": False}
    receipt = quota["heartbeat_receipt"]
    receipt.pop("closeout_required")
    receipt["semantic_replan_obligation_id"] = "replan-0123456789abcdef"
    result = effect_runtime_result("runtime.checkpoint_artifacts", request)
    assert result["capture"] is True and result["execution_authority_granted"] is False


@pytest.mark.parametrize("field,value", [("goal_id", "foreign"), ("agent_id", "foreign"),
    ("turn_instance_id", "foreign"), ("effect_id", "foreign")])
def test_closeout_does_not_relax_settlement_identity(field, value):
    from loopx.control_plane.effect_runtime import effect_runtime_result, EffectRuntimeRemoteError
    quota = closeout_guard()
    quota["heartbeat_receipt"]["settlement_identity"][field] = value
    with pytest.raises(EffectRuntimeRemoteError):
        effect_runtime_result("runtime.checkpoint_artifacts", {"action": "select_replan", "quota": quota,
            "goal_id": "source", "agent_id": "agent", "turn_instance_id": "turn"})


@pytest.mark.parametrize("fault", [None, "first_read", "probe", "cursor_moved", "source_moved", "capture"])
@pytest.mark.parametrize("restored", [False, True])
def test_interrupted_capture_keeps_reader_through_publication_and_cleans_up(tmp_path, monkeypatch, fault, restored):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from benchmark.runtime.checkpoint_wake import CheckpointWake

    env = {"LOOPX_WAKE_LOG_DIR": str(tmp_path / "wakes"), "LOOPX_CLI": "loopx",
        "LOOPX_PROJECT": str(tmp_path), "LOOPX_REGISTRY": str(tmp_path / "registry"),
        "LOOPX_RUNTIME_ROOT": str(tmp_path / "runtime"), "LOOPX_GOAL_ID": "goal",
        "LOOPX_AGENT_ID": "agent"}
    hook = CheckpointWake(env, {"turn_id": "turn", "session": {"session_id": "session"}})
    hook.raw = b"synthetic request"
    code = tmp_path / "candidate"
    code.write_text("retained")
    hook.request = {"components": [{"id": "code", "source_path": str(code)}],
                    "point_metadata": {}, "native_session": True}
    held = {"path": tmp_path / "held", "sha256": "guard-digest",
            "intent": {"obligation_id": "replan-fixture"},
            "quota": {"heartbeat_receipt": {"turn_instance_id": "turn"}}}
    held["path"].write_text("original guard")
    binding = SimpleNamespace(session_id="session", goal_ref="goal", digest="profile")
    monkeypatch.setattr("benchmark.runtime.checkpoint.native_binding_participant",
                        lambda env, components, metadata: (components, binding))
    monkeypatch.setattr("benchmark.runtime.sessions.BenchmarkSessionWake", lambda *a, **k: binding)
    events = []
    token = object()
    native_options = {"retained_history": {"turn_ids": ["source"], "visible_history_sha256": "source-digest"},
                      "required_completed_turn_id": "continued"} if restored else {}
    if restored or fault == "source_moved":
        history_reads = []
        def history(*args, **kwargs):
            history_reads.append(True)
            if fault == "source_moved" and len(history_reads) == 2:
                raise ValueError("restored capture source changed after preflight")
            return native_options
        monkeypatch.setattr("benchmark.runtime.checkpoint_continuation.restored_capture_history",
                            history)
    @contextmanager
    def reader(*args, **kwargs):
        events.append("open")
        try:
            yield token
        finally:
            events.append("closed")
    def read(*args, transport, interrupted_guard, **kwargs):
        assert transport is token and interrupted_guard["decision_sha256"] == held["sha256"]
        assert {key: kwargs[key] for key in native_options} == native_options
        events.append("read")
        if fault == "first_read":
            raise ValueError("synthetic read failure")
        return {"components": [], "metadata": {"store_members": [],
                "visible_history_sha256": "later" if fault == "cursor_moved" and events.count("read") == 2 else "original"}}
    monkeypatch.setattr("loopx.hosts.codex_checkpoint.native_checkpoint_reader", reader)
    monkeypatch.setattr("loopx.hosts.codex_checkpoint.native_session_checkpoint", read)
    def artifact(method, params, **kwargs):
        assert params["deadline_epoch_ms"] <= phase_deadline * 1000 + 1
        events.append(params["action"])
        if params["action"] == "probe":
            if fault == "probe":
                raise ValueError("synthetic probe failure")
            return {"source_sha256": "witness"}
        assert "closed" not in events
        if fault == "capture":
            raise ValueError("synthetic capture failure")
        assert params["expected_source_sha256"] == "witness"
        return {"sha256": "sealed"}
    monkeypatch.setattr("benchmark.runtime.checkpoint_wake.effect_runtime_result", artifact)
    phase_deadline = time.time() + 5
    if fault:
        with pytest.raises(ValueError, match="failure|cursor moved|source changed"):
            hook._capture(held, time.monotonic() + 5)
        assert events[-1] == "closed"
        assert ("capture" in events) is (fault == "capture")
    else:
        assert hook._capture(held, time.monotonic() + 5)["sha256"] == "sealed"
        assert events == ["open", "read", "probe", "read", "capture", "closed"]


@pytest.mark.parametrize("fault", [None, "binding_lost", "source_moved", "session_moved", "no_cursor", "pending_review"])
@pytest.mark.parametrize("reset", [False, True])
def test_restored_capture_cannot_drop_its_preflight_basis(tmp_path, monkeypatch, fault, reset):
    from benchmark.runtime import checkpoint_continuation as continuation
    native = {"turn_ids": ["source"], "visible_history_sha256": "retained-digest"}
    binding = {"session_id": "destination", "checkpoint_artifact_sha256": "a" * 64,
               "checkpoint_artifact_root": str(tmp_path / "point")}
    gate = {"capture_allowed": True, "checkpoint_sha256": "a" * 64,
            "native_continuation_cursor": "continued"}
    if reset:
        binding["checkpoint_context_refresh"] = {"checkpoint_sha256": "a" * 64,
            "session_id": "destination", "review_basis": {"decision_sha256": "accepted"}}
    if fault == "binding_lost":
        binding = None
    elif fault == "source_moved":
        binding["checkpoint_artifact_sha256"] = "b" * 64
    elif fault == "session_moved":
        binding["session_id"] = "another"
    elif fault == "no_cursor":
        gate.pop("native_continuation_cursor")
    elif fault == "pending_review":
        gate["capture_allowed"] = False
    reads = []
    monkeypatch.setattr(continuation, "load_codex_cli_session", lambda *a, **k: binding)
    def basis(method, request):
        assert request == {"action": "read_basis", "root": str(tmp_path / "point"), "expected_sha256": "a" * 64}
        reads.append(True)
        return {"point_metadata": {"native_session": native}}
    monkeypatch.setattr(continuation, "effect_runtime_result", basis)
    env = {"LOOPX_RUNTIME_ROOT": str(tmp_path), "LOOPX_GOAL_ID": "goal", "LOOPX_AGENT_ID": "agent"}
    if fault:
        with pytest.raises(ValueError, match="preflight|admitted session"):
            continuation.restored_capture_history(env, gate, session_id="destination")
        assert not reads
    else:
        assert continuation.restored_capture_history(env, gate, session_id="destination") == {
            "retained_history": ({"turn_ids": [], "visible_history_sha256": hashlib.sha256(b"[]").hexdigest()}
                                 if reset else native), "required_completed_turn_id": "continued"}
        assert reads == [True]


@pytest.mark.parametrize("native_participant", [False, True])
def test_preflight_defers_history_only_when_capture_will_verify_it(tmp_path, monkeypatch, native_participant):
    from types import SimpleNamespace
    from benchmark.runtime import checkpoint
    wake = tmp_path / "wakes" / "turn"
    wake.mkdir(parents=True)
    request = tmp_path / "request"
    request.write_text(json.dumps({"destination": str(tmp_path / "checkpoints"),
        "trigger": "fresh_replan", "native_session": native_participant,
        "components": [{"id": "code", "source_path": str(tmp_path / "code")}], "point_metadata": {}}))
    monkeypatch.setattr(checkpoint.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=json.dumps({"ok": True})))
    monkeypatch.setattr(checkpoint, "effect_runtime_result", lambda *a, **k: {"capture": False, "reason": "no_fresh_replan"})
    observed = []
    def gate(*args, full_history):
        observed.append(full_history)
        return {"capture_allowed": True}
    monkeypatch.setattr("benchmark.runtime.checkpoint_continuation.restored_capture_gate", gate)
    env = {"LOOPX_CHECKPOINT_REQUEST": str(request), "LOOPX_WAKE_LOG_DIR": str(wake.parent),
        "LOOPX_CLI": "loopx", "LOOPX_PROJECT": str(tmp_path), "LOOPX_RUNTIME_ROOT": str(tmp_path),
        "LOOPX_REGISTRY": str(tmp_path / "registry"), "LOOPX_GOAL_ID": "goal", "LOOPX_AGENT_ID": "agent"}
    receipt = {"turn_id": "turn", "mode": "heartbeat", "stage": "execute"}
    assert checkpoint.capture_before_replan(env, receipt, "current work").endswith("current work")
    assert observed == [not native_participant]
    assert receipt["checkpoint"]["reason"] == "no_fresh_replan"
