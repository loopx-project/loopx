"""Host wake of a Chat LoopX lead after a delegated result is accepted.

Expectations follow the wake contract, not the implementation: an accepted
result with a pending intent continues its requester's lead at most once, the
existing Chat LoopX owner decides admission, and every other state leaves a
distinct receipt without starting a Turn.
"""

import json
from pathlib import Path

import pytest

import loopx.collaboration_mcp as collaboration_mcp
from loopx.chat_loopx_mode import pump_delegation_wakes
from loopx.chat_runtime import ChatRuntimeController
from loopx.collaboration_mcp import execution_row_path
from test_chat_loopx_mode import apply, mode  # noqa: F401
from test_chat_project_coordination import project  # noqa: F401

INTENT_ID = "a" * 64
OPERATION_ID = "review-1"


def _runtime_root(service):
    return service.store.root.parent


def _write_record(service, *, status="accepted", agent_id="coordinator",
                  stored_as=None, operation_id=OPERATION_ID, session_id=None):
    root = _runtime_root(service)
    owner_goal, owner_agent = stored_as or ("research", agent_id)
    path = execution_row_path(root, owner_goal, owner_agent, operation_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "operation_id": operation_id,
        "status": status,
        "wake": {
            "schema_version": "loopx_delegation_wake_intent_v0",
            "intent_id": INTENT_ID,
            "requester": {"goal_id": "research", "agent_id": agent_id, "goal_ref": None},
            "operation_id": operation_id,
            "request_id": "request-1",
            # The conversation whose Turn started the operation; the only wake target.
            **({"conversation": {"session_id": session_id, "turn_id": "lead-turn"}}
               if session_id else {}),
            "state": "pending",
        },
    }))
    return path


def _wake(path):
    return json.loads(path.read_text())["wake"]


def _idle_resumable_lead(mode):  # noqa: F811
    """Enable the mode, then leave the lead idle on a resumable native Goal."""
    service, sid, _, settings, calls = mode
    apply(mode, "start", settings=settings)
    service.store.update_session(
        sid, active_turn_id=None, native_goal={"status": "paused", "tokensUsed": 0}
    )
    calls.clear()
    return service, sid, calls


def _pump_with(controller, repo):
    return pump_delegation_wakes(
        controller,
        goal_context=lambda session: {"project": repo, "objective": "Continue"},
    )


def _pump(service, repo):
    return _pump_with(service.controller, repo)


def _start_worker_turn(service, sid):
    """The worker's first durable step, for the stubbed ``submit_turn`` fixture.

    The shared fixture's ``submit_turn`` records acceptance only, exactly as the
    real one returns before the worker writes ``started_at``.  This is *not*
    dispatch evidence: the real worker stamps it before it builds the turn
    context and reaches the provider.
    """
    turn = service.store.turn_for_client(sid, "wake-" + INTENT_ID[:32])
    service.store.update_turn(
        sid, turn["turn_id"], expected_statuses={"queued"},
        status="starting", started_at="2026-09-30T00:00:00Z",
    )
    return turn


def _dispatch_worker_turn(service, sid):
    """The provider-start fact, as ``turn.started`` records it.

    The runtime writes ``status=running`` and ``upstream_turn_id`` when the
    provider reports the Turn actually started, and checkpoints immediately.
    Only this proves a dispatch.
    """
    turn = _start_worker_turn(service, sid)
    service.store.update_turn(
        sid, turn["turn_id"], expected_statuses={"starting"},
        status="running", upstream_turn_id="upstream-1",
    )
    return turn


def test_accepted_result_wakes_the_lead_exactly_once(mode):  # noqa: F811
    service, sid, calls = _idle_resumable_lead(mode)
    repo = mode[2]
    path = _write_record(service, session_id=sid)

    changed = _pump(service, repo)

    assert len(calls) == 1
    assert calls[0]["client_turn_id"] == "wake-" + INTENT_ID[:32]
    assert calls[0]["loopx_request"]["operation"] == "wake"
    assert calls[0]["loopx_request"]["wake"]["intent_id"] == INTENT_ID
    # Dispatch is accepted; the receipt stays pending until the start fact lands.
    receipt = _wake(path)
    assert receipt["state"] == "pending" and changed == [receipt]
    assert _pump(service, repo) == [] and _wake(path) == receipt

    # The worker's first durable step is not a dispatch: the receipt stays
    # pending, because the provider has not been reached yet.
    _start_worker_turn(service, sid)
    assert _pump(service, repo) == [] and _wake(path) == receipt

    # Once the provider reports the start, the next tick records it without a
    # second Turn.
    _dispatch_worker_turn(service, sid)
    changed = _pump(service, repo)
    woken = _wake(path)
    assert woken["state"] == "woken" and woken["created"] is False
    assert woken["session_id"] == sid and changed == [woken]

    # A second tick finds no pending intent: no second Turn, receipt unchanged.
    assert _pump(service, repo) == []
    assert _wake(path) == woken
    # Every attempt used the one stable client identity, so one Turn resulted.
    assert {call["client_turn_id"] for call in calls} == {"wake-" + INTENT_ID[:32]}
    assert len(_wake_turns(service, sid)) == 1


def test_lost_receipt_after_the_turn_started_records_it_once(mode):  # noqa: F811
    service, sid, calls = _idle_resumable_lead(mode)
    repo = mode[2]
    turn, _ = service.store.create_turn(
        sid, client_turn_id="wake-" + INTENT_ID[:32], message="/goal resume"
    )
    # A dispatched Turn is dispatch evidence; a merely persisted one is not.
    service.store.update_turn(
        sid, turn["turn_id"], loopx_execution=True, status="completed",
        started_at="2026-09-30T00:00:00Z", completed_at="2026-09-30T00:01:00Z",
        upstream_turn_id="upstream-1",
        loopx_request={"operation": "wake", "wake": {"intent_id": INTENT_ID}},
    )
    service.store.update_session(sid, active_turn_id=None)
    path = _write_record(service, session_id=sid)

    _pump(service, repo)

    receipt = _wake(path)
    assert receipt["state"] == "woken" and receipt["created"] is False
    assert receipt["turn_id"] == turn["turn_id"] and calls == []


def test_client_id_owned_by_another_turn_is_not_claimed(mode):  # noqa: F811
    service, sid, calls = _idle_resumable_lead(mode)
    repo = mode[2]
    service.store.create_turn(
        sid, client_turn_id="wake-" + INTENT_ID[:32], message="unrelated"
    )
    service.store.update_session(sid, active_turn_id=None)
    path = _write_record(service, session_id=sid)

    _pump(service, repo)

    assert _wake(path)["state"] == "refused"
    assert _wake(path)["reason"] == "wake_identity_conflict" and calls == []


def test_active_lead_turn_keeps_the_intent_pending_without_churn(mode):  # noqa: F811
    service, sid, _, settings, calls = mode
    repo = mode[2]
    apply(mode, "start", settings=settings)
    calls.clear()
    assert service.store.load_session(sid)["active_turn_id"]
    path = _write_record(service, session_id=sid)

    _pump(service, repo)
    first = _wake(path)
    assert first["state"] == "pending" and first["reason"] == "lead_turn_active"
    before = path.read_bytes()
    _pump(service, repo)
    assert path.read_bytes() == before and calls == []


def test_paused_lead_stays_pending_and_is_not_unpaused(mode):  # noqa: F811
    service, sid, calls = _idle_resumable_lead(mode)
    repo = mode[2]
    session = service.store.load_session(sid)
    service.store.update_session(
        sid, loopx_mode={**session["loopx_mode"], "paused": True}
    )
    path = _write_record(service, session_id=sid)

    _pump(service, repo)

    assert _wake(path)["state"] == "pending" and _wake(path)["reason"] == "lead_paused"
    assert service.store.load_session(sid)["loopx_mode"]["paused"] is True
    assert calls == []


def test_stopped_goal_refuses_the_wake(mode):  # noqa: F811
    service, sid, calls = _idle_resumable_lead(mode)
    repo = mode[2]
    registry = service.controller.registry_path
    payload = json.loads(registry.read_text())
    next(goal for goal in payload["goals"] if goal["id"] == "research")["status"] = "stopped"
    registry.write_text(json.dumps(payload))
    path = _write_record(service, session_id=sid)

    _pump(service, repo)

    assert _wake(path)["state"] == "refused" and _wake(path)["reason"] == "goal_stopped"
    assert calls == []


def test_intent_without_an_originating_conversation_wakes_nobody(mode):  # noqa: F811
    """An operation started outside a Chat conversation has no wake target;
    a same-identity conversation is never selected in its place."""
    service, sid, calls = _idle_resumable_lead(mode)
    repo = mode[2]
    path = _write_record(service)

    _pump(service, repo)

    assert _wake(path)["state"] == "refused" and _wake(path)["reason"] == "no_wake_owner"
    assert calls == []


def test_pinned_conversation_rebound_to_another_requester_is_refused(mode):  # noqa: F811
    service, sid, calls = _idle_resumable_lead(mode)
    repo = mode[2]
    path = _write_record(service, agent_id="someone-else", session_id=sid)

    _pump(service, repo)

    assert _wake(path)["state"] == "refused"
    assert _wake(path)["reason"] == "wake_identity_conflict" and calls == []


def test_intent_stored_under_another_requester_is_not_decided(mode):  # noqa: F811
    service, sid, calls = _idle_resumable_lead(mode)
    repo = mode[2]
    path = _write_record(service, stored_as=("research", "reviewer"), session_id=sid)

    assert _pump(service, repo) == []
    assert _wake(path)["state"] == "pending" and calls == []


@pytest.mark.parametrize("status", ["rejected", "running", "stopped"])
def test_only_an_accepted_result_can_wake(mode, status):  # noqa: F811
    service, sid, calls = _idle_resumable_lead(mode)
    repo = mode[2]
    path = _write_record(service, status=status, session_id=sid)

    assert _pump(service, repo) == []
    assert _wake(path)["state"] == "pending" and calls == []


# Native dispatch recovery and conversation pinning.
#
# These run the real ChatRuntimeController.submit_turn, TS turn acceptance and
# the durable Chat store.  Only the adapter and worker transport are replaced,
# so no model runs; "dispatch" below is a request to start the worker.

class Transport:
    """Adapter/worker boundary with injectable faults.

    ``start_worker`` drives the real ``_run_turn`` body when the wake Turn can
    start, so the worker's durable start fact, its single-flight release and
    the native acceptance recovery path are the shipped ones; only the adapter
    and the thread are replaced.  No model runs.
    """

    def __init__(self, service, *, adapter_failures=0, start_write_failures=0,
                 real_worker=False, dispatch_provider=True):
        self.service = service
        self.adapter_failures = adapter_failures
        self.start_write_failures = start_write_failures
        self.real_worker = real_worker
        # Whether the stubbed provider reports ``turn.started``. ``False`` models
        # a Turn whose worker started but never reached a provider.
        self.dispatch_provider = dispatch_provider
        self.adapter_attempts = []
        self.dispatches = []

    def ensure_adapter(self, session, **kwargs):
        self.adapter_attempts.append(session["session_id"])
        if len(self.adapter_attempts) <= self.adapter_failures:
            raise RuntimeError("adapter initialization failed")
        return StubAdapter(self.service, session)

    def arm(self, monkeypatch, controller):
        """Bind this transport to ``controller``, including the start-fact fault."""
        store = self.service.store
        real_update_turn = store.update_turn

        def update_turn(*args, **changes):
            # Exactly the worker's first durable step, on its first attempt.
            if self.start_write_failures and changes.get("status") == "starting" \
                    and changes.get("started_at"):
                self.start_write_failures -= 1
                raise OSError("chat turn store unavailable")
            return real_update_turn(*args, **changes)

        monkeypatch.setattr(store, "update_turn", update_turn)
        monkeypatch.setattr(controller, "_ensure_adapter_locked", self.ensure_adapter)
        monkeypatch.setattr(controller, "_start_accepted_turn_worker", self.start_worker)
        return self

    def start_worker(self, *, session_id, turn_id, message="", attachments=None,
                     adapter=None, loopx_execution=False, **kwargs):
        self.dispatches.append((session_id, turn_id))
        if not self.real_worker:  # the worker's start, then the provider's
            self.service.store.update_turn(
                session_id, turn_id, expected_statuses={"queued"},
                status="starting", started_at="2026-09-30T00:00:00Z",
            )
            if self.dispatch_provider:
                # ``turn.started``: the runtime's provider-start fact.
                self.service.store.update_turn(
                    session_id, turn_id, expected_statuses={"starting"},
                    status="running", upstream_turn_id="upstream-1",
                )
            return True
        if (self.service.store.load_turn(session_id, turn_id) or {}).get("status") != "queued":
            return False
        # The shipped worker body, over a stub adapter: the failure is after the
        # durable start write, which is the fact under test.
        self.service.controller._run_turn(
            session_id=session_id, turn_id=turn_id, message=message,
            attachments=attachments or [], adapter=adapter, loopx_execution=loopx_execution,
        )
        return True


class StubAdapter:
    """Enough adapter for the real worker to reach a bounded, non-model failure."""

    upstream_thread_id = "stub-upstream"

    def __init__(self, service, session):
        self.service = service
        self.session = session
        self.goal_driver = None
        self.team_plan_context = None

    def capabilities(self):
        return {}

    def start_turn(self, message, event_sink):  # pragma: no cover - never reached
        raise AssertionError("the stub adapter must not start a native turn")

    def interrupt_turn(self, turn_id=None):
        return None

    def close_session(self):
        return None

    def healthcheck(self):
        return True


def _native(mode, monkeypatch, **faults):  # noqa: F811
    service, sid, repo, settings, _ = mode
    apply(mode, "start", settings=settings)
    store = service.store
    start_turn = store.load_session(sid)["active_turn_id"]
    store.update_turn(sid, start_turn, status="completed", started_at="2026-09-30T00:00:00Z",
                      completed_at="2026-09-30T00:00:01Z")
    store.update_session(sid, active_turn_id=None, status="ready", loopx_tools=True,
                         native_goal={"status": "paused", "tokensUsed": 0})
    controller = service.controller
    transport = Transport(service, **faults).arm(monkeypatch, controller)
    monkeypatch.setattr(controller, "submit_turn",
                        ChatRuntimeController.submit_turn.__get__(controller))
    return service, sid, repo, transport


def _wake_turns(service, sid):
    turns = service.store.root / "sessions" / sid / "turns"
    return [
        row for row in (json.loads(p.read_text()) for p in turns.glob("*.json")
                        if not p.name.endswith(".events.json"))
        if str(row.get("client_turn_id", "")).startswith("wake-")
    ]


def _second_lead(service, sid, settings):
    """Another enabled conversation for the same Goal and coordinator identity."""
    store = service.store
    other = store.create_session(
        goal_id="research", agent_id="codex", channel_id="goal.research",
        upstream_thread_id="fixture-2", upstream_mode="chat", adapter_kind="codex_app_server",
    )["session_id"]
    mode_state = store.load_session(sid)["loopx_mode"]
    store.update_session(other, loopx_mode={**mode_state, "enabled": True, "paused": False},
                         loopx_tools=True, status="ready",
                         native_goal={"status": "paused", "tokensUsed": 0})
    return other


def _fail_next_wake_receipt(monkeypatch):
    """Lose the next wake receipt write, whatever state it settles to."""
    real = collaboration_mcp._write
    failed = []

    def write(path, row):
        if not failed and (row.get("wake") or {}).get("state") in {"woken", "pending"}:
            failed.append(path)
            raise OSError("receipt write lost")
        return real(path, row)

    monkeypatch.setattr(collaboration_mcp, "_write", write)
    return failed


def test_adapter_failure_after_acceptance_is_redispatched_not_recorded(mode, monkeypatch):  # noqa: F811
    service, sid, repo, transport = _native(mode, monkeypatch, adapter_failures=1)
    path = _write_record(service, session_id=sid)

    assert _pump(service, repo) == []
    [queued] = _wake_turns(service, sid)
    assert queued["status"] == "queued" and queued["started_at"] is None
    assert _wake(path)["state"] == "pending" and transport.dispatches == []

    _pump(service, repo)

    # The native acceptance owner re-dispatches the same Turn; no second Turn.
    assert len(transport.adapter_attempts) == 2
    assert transport.dispatches == [(sid, queued["turn_id"])]
    assert [row["turn_id"] for row in _wake_turns(service, sid)] == [queued["turn_id"]]
    assert _wake(path)["state"] == "pending"

    # Only after the start fact is durable does the next tick record the wake.
    _pump(service, repo)

    receipt = _wake(path)
    assert receipt["state"] == "woken" and receipt["turn_id"] == queued["turn_id"]
    assert receipt["created"] is False and transport.dispatches == [(sid, queued["turn_id"])]


def test_failure_after_the_prepared_capsule_is_repaired_and_dispatched(mode, monkeypatch):  # noqa: F811
    service, sid, repo, transport = _native(mode, monkeypatch)
    path = _write_record(service, session_id=sid)
    real = service.store.append_message
    failed = []

    def append_message(*args, **kwargs):
        if not failed:
            failed.append(True)
            raise OSError("transcript unavailable")
        return real(*args, **kwargs)

    monkeypatch.setattr(service.store, "append_message", append_message)
    assert _pump(service, repo) == []
    [prepared] = _wake_turns(service, sid)
    assert "_acceptance" in prepared and transport.dispatches == []
    assert _wake(path)["state"] == "pending"

    _pump(service, repo)

    [settled] = _wake_turns(service, sid)
    assert settled["turn_id"] == prepared["turn_id"] and "_acceptance" not in settled
    assert transport.dispatches == [(sid, prepared["turn_id"])]
    assert _wake(path)["state"] == "pending"

    _pump(service, repo)

    assert _wake(path)["state"] == "woken"
    assert transport.dispatches == [(sid, prepared["turn_id"])]


@pytest.mark.parametrize("started", [False, True])
def test_lost_receipt_replays_only_the_original_turn(mode, monkeypatch, started):  # noqa: F811
    # ``started=False`` fails the start write itself, before any receipt exists;
    # the native owner replays that same Turn.  ``started=True`` loses only the
    # receipt, after the start fact landed.  Both must start it exactly once.
    service, sid, repo, transport = _native(
        mode, monkeypatch, start_write_failures=0 if started else 1)
    path = _write_record(service, session_id=sid)
    lost = _fail_next_wake_receipt(monkeypatch)

    assert _pump(service, repo) == []
    assert _wake(path)["state"] == "pending" and len(transport.dispatches) == 1
    # The receipt is the write that was lost, when the start fact survived.
    assert bool(lost) is started
    [queued] = _wake_turns(service, sid)
    assert (queued["started_at"] is not None) is started

    # Replay through native dispatch: the still-queued case dispatches once
    # more for that same Turn; the started case does not dispatch again.
    _pump(service, repo)
    # A started Turn is recorded without another dispatch.
    _pump(service, repo)

    [turn] = _wake_turns(service, sid)
    assert turn["turn_id"] == queued["turn_id"] and turn["started_at"]
    receipt = _wake(path)
    assert receipt["state"] == "woken" and receipt["turn_id"] == turn["turn_id"]
    expected = 1 if started else 2
    assert transport.dispatches == [(sid, turn["turn_id"])] * expected
    # Both paths start the Turn exactly once and never mint a second one.
    assert _pump(service, repo) == [] and _wake(path) == receipt
    assert len(_wake_turns(service, sid)) == 1


def test_a_worker_start_without_a_provider_dispatch_is_not_a_wake(mode, monkeypatch):  # noqa: F811
    """The pre-dispatch window: `started_at` lands, the provider is never reached.

    The worker stamps `started_at` before it builds the turn context, prepares
    LoopX mode and hands the message to the adapter.  Reading that as dispatch
    evidence would record `woken` for a Turn the provider never accepted — and
    because a terminal receipt is never rescanned, the intent would be lost.
    """
    service, sid, repo, transport = _native(
        mode, monkeypatch, dispatch_provider=False)
    path = _write_record(service, session_id=sid)

    # Admission is pending; the worker stamps its pre-dispatch start fact and
    # the provider is never reached, so no upstream identity is recorded.
    _pump(service, repo)
    [queued] = _wake_turns(service, sid)
    assert queued["status"] == "starting" and queued["started_at"]
    assert queued["upstream_turn_id"] is None
    # No provider accepted this Turn, so the intent stays open.
    receipt = _wake(path)
    assert receipt["state"] == "pending" and receipt["reason"] == "wake_dispatch_pending"
    assert "woken_at" not in receipt

    # A restart rediscovers it and still does not claim a wake.
    controller, rebuilt = _rebuilt(mode, monkeypatch, dispatch_provider=False)
    assert _pump_with(controller, repo) == []
    assert _wake(path)["state"] == "pending"
    assert rebuilt.dispatches == []

    # Only the provider's own start fact turns it into a wake, exactly once.
    service.store.update_turn(
        sid, queued["turn_id"], status="running", upstream_turn_id="upstream-1",
    )
    changed = _pump_with(controller, repo)
    receipt = _wake(path)
    assert receipt["state"] == "woken" and receipt["turn_id"] == queued["turn_id"]
    assert receipt["created"] is False and changed == [receipt]
    assert len(_wake_turns(service, sid)) == 1
    assert _pump_with(controller, repo) == [] and _wake(path) == receipt


def test_a_pre_provider_failure_does_not_claim_a_wake(mode, monkeypatch):  # noqa: F811
    """A Turn that fails after its start write but before dispatch is refused.

    The real error path marks the Turn `failed` with no `turn.started` event, so
    no provider accepted it.  Its client id cannot admit another Turn, and the
    intent must end in an actionable refusal rather than claiming a wake.
    """
    service, sid, repo, transport = _native(mode, monkeypatch, dispatch_provider=False)
    path = _write_record(service, session_id=sid)

    _pump(service, repo)
    [turn] = _wake_turns(service, sid)
    # The worker wrote its start fact; the provider call then failed.
    service.store.update_turn(
        sid, turn["turn_id"], status="starting", started_at="2026-09-30T00:00:00Z",
    )
    service.store.update_turn(
        sid, turn["turn_id"], status="failed", error_code="adapter_unavailable",
        expected_statuses={"starting"},
    )
    [failed] = _wake_turns(service, sid)
    assert failed["started_at"] and failed["upstream_turn_id"] is None

    # The next tick re-reads the Turn and refuses: no provider accepted it, so
    # its client id cannot admit another one.  This is the receipt the previous
    # head wrongly wrote as `woken`.
    changed = _pump(service, repo)
    receipt = _wake(path)
    assert receipt["state"] == "refused", receipt
    assert receipt["reason"] == "wake_turn_ended_unstarted"
    assert "woken_at" not in receipt and changed == [receipt]
    # Refused is terminal for this intent and no second Turn was minted.
    assert _pump(service, repo) == [] and _wake(path) == receipt
    assert len(_wake_turns(service, sid)) == 1


def test_wake_turn_cancelled_before_it_started_is_not_woken(mode, monkeypatch):  # noqa: F811
    service, sid, repo, transport = _native(mode, monkeypatch, adapter_failures=1)
    path = _write_record(service, session_id=sid)
    _pump(service, repo)
    [queued] = _wake_turns(service, sid)
    service.store.update_turn(sid, queued["turn_id"], status="interrupted",
                              completed_at="2026-09-30T00:00:02Z")
    service.store.update_session(sid, active_turn_id=None, status="ready")

    _pump(service, repo)

    receipt = _wake(path)
    assert receipt["state"] == "refused" and receipt["reason"] == "wake_turn_ended_unstarted"
    assert transport.dispatches == []


def test_queued_wake_turn_keeps_the_pause_boundary(mode, monkeypatch):  # noqa: F811
    service, sid, repo, transport = _native(mode, monkeypatch, adapter_failures=1)
    path = _write_record(service, session_id=sid)
    _pump(service, repo)
    session = service.store.load_session(sid)
    service.store.update_session(sid, loopx_mode={**session["loopx_mode"], "paused": True})

    _pump(service, repo)

    assert _wake(path)["state"] == "pending" and _wake(path)["reason"] == "lead_paused"
    assert transport.dispatches == [] and len(transport.adapter_attempts) == 1


def test_wake_never_moves_to_another_conversation_after_exit(mode, monkeypatch):  # noqa: F811
    service, sid, repo, transport = _native(mode, monkeypatch)
    other = _second_lead(service, sid, mode[3])
    session = service.store.load_session(sid)
    service.store.update_session(sid, loopx_mode={**session["loopx_mode"], "enabled": False})
    path = _write_record(service, session_id=sid)

    _pump(service, repo)

    assert _wake(path)["state"] == "refused" and _wake(path)["reason"] == "no_wake_owner"
    assert transport.dispatches == [] and _wake_turns(service, other) == []


def test_closed_origin_conversation_is_refused_without_a_substitute(mode, monkeypatch):  # noqa: F811
    service, sid, repo, transport = _native(mode, monkeypatch)
    other = _second_lead(service, sid, mode[3])
    service.store.update_session(sid, status="closed")
    path = _write_record(service, session_id=sid)

    _pump(service, repo)

    assert _wake(path)["state"] == "refused" and _wake(path)["reason"] == "no_wake_owner"
    assert transport.dispatches == [] and _wake_turns(service, other) == []


def _rebuilt(mode, monkeypatch, **faults):  # noqa: F811
    """A fresh real controller over the same persisted store, as after a restart."""
    service = mode[0]
    controller = ChatRuntimeController(
        store=service.store,
        codex_bin="codex",
        registry_path=service.controller.registry_path,
    )
    transport = Transport(service, **faults).arm(monkeypatch, controller)
    monkeypatch.setattr(controller, "submit_turn",
                        ChatRuntimeController.submit_turn.__get__(controller))
    return controller, transport


def test_start_fact_write_failure_is_not_a_wake(mode, monkeypatch):  # noqa: F811
    """An accepted Turn whose start never persisted is pending, never woken.

    ``submit_turn`` returning proves admission and a queued Turn; only the
    worker's durable ``started_at`` proves dispatch.  The first write of that
    fact fails here, so the intent must stay recoverable.
    """
    service, sid, repo, transport = _native(
        mode, monkeypatch, start_write_failures=1, dispatch_provider=False)
    path = _write_record(service, session_id=sid)

    # The worker's start write fails: no dispatch fact, so no terminal receipt.
    assert _pump(service, repo) == []
    [queued] = _wake_turns(service, sid)
    assert queued["status"] == "queued" and queued["started_at"] is None
    assert _wake(path)["state"] == "pending" and "woken_at" not in _wake(path)
    # The failed start released the worker single-flight guard for this Turn.
    assert (sid, queued["turn_id"]) not in service.controller.turn_done_events

    # The next tick replays that same Turn through native dispatch; the worker
    # starts it, but the provider's own start fact has not landed yet.
    _pump(service, repo)
    [started] = _wake_turns(service, sid)
    assert started["turn_id"] == queued["turn_id"] and started["started_at"]
    assert started["upstream_turn_id"] is None
    assert _wake(path)["state"] == "pending"

    # Only the provider's start fact makes it a wake: one receipt, no new Turn.
    service.store.update_turn(
        sid, started["turn_id"], status="running", upstream_turn_id="upstream-1",
    )
    changed = _pump(service, repo)
    receipt = _wake(path)
    assert receipt["state"] == "woken" and receipt["turn_id"] == started["turn_id"]
    assert receipt["created"] is False and changed == [receipt]
    assert [row["turn_id"] for row in _wake_turns(service, sid)] == [started["turn_id"]]
    # Settled: another tick is a no-op.
    assert _pump(service, repo) == [] and _wake(path) == receipt


def test_start_fact_write_failure_recovers_after_a_rebuilt_controller(mode, monkeypatch):  # noqa: F811
    """A restart rediscovers the same intent and starts the same Turn once."""
    service, sid, repo, transport = _native(
        mode, monkeypatch, start_write_failures=1, dispatch_provider=False)
    path = _write_record(service, session_id=sid)

    assert _pump(service, repo) == []
    [queued] = _wake_turns(service, sid)
    assert queued["status"] == "queued" and _wake(path)["state"] == "pending"

    # A fresh controller over the same persisted store, as after a restart.
    controller, rebuilt = _rebuilt(mode, monkeypatch, dispatch_provider=False)
    _pump_with(controller, repo)

    [started] = _wake_turns(service, sid)
    assert started["turn_id"] == queued["turn_id"] and started["started_at"]
    assert rebuilt.dispatches == [(sid, queued["turn_id"])]

    # The provider's own start fact is what turns it into a wake, exactly once.
    service.store.update_turn(
        sid, started["turn_id"], status="running", upstream_turn_id="upstream-1",
    )
    changed = _pump_with(controller, repo)
    receipt = _wake(path)
    assert receipt["state"] == "woken" and receipt["session_id"] == sid
    assert receipt["turn_id"] == started["turn_id"] and changed == [receipt]
    assert _pump_with(controller, repo) == []


def test_lost_receipt_then_new_conversation_cannot_dispatch_twice(mode, monkeypatch):  # noqa: F811
    service, sid, repo, transport = _native(mode, monkeypatch)
    path = _write_record(service, session_id=sid)
    _fail_next_wake_receipt(monkeypatch)
    _pump(service, repo)
    assert len(transport.dispatches) == 1 and _wake(path)["state"] == "pending"
    [turn] = _wake_turns(service, sid)
    # The original conversation leaves the mode; a new one takes the same identity.
    service.store.update_turn(sid, turn["turn_id"], status="completed",
                              completed_at="2026-09-30T00:00:03Z")
    session = service.store.load_session(sid)
    service.store.update_session(sid, active_turn_id=None, status="ready",
                                 loopx_mode={**session["loopx_mode"], "enabled": False})
    other = _second_lead(service, sid, mode[3])

    _pump(service, repo)

    receipt = _wake(path)
    assert receipt["state"] == "woken" and receipt["session_id"] == sid
    assert receipt["turn_id"] == turn["turn_id"] and receipt["created"] is False
    assert len(transport.dispatches) == 1 and _wake_turns(service, other) == []

def test_the_in_turn_tool_pins_the_conversation_it_runs_in(mode, monkeypatch):  # noqa: F811
    """The model supplies the brief, never the wake routing: the host fixes it."""
    from loopx.collaboration_mcp import Delegations

    pinned = []

    def start(self, binding_id, operation_id, brief, parent_request_id=None, *, conversation=None):
        pinned.append(conversation)
        return {"operation_id": operation_id, "status": "prepared"}

    monkeypatch.setattr(Delegations, "start", start)
    service, sid, _, settings, _ = mode
    apply(mode, "start", settings=settings)
    adapter = type("A", (), {})()
    adapter.session = type("S", (), {})()
    adapter.goal_driver = type("D", (), {
        "turn_start_handler": None, "stopped": __import__("threading").Event()})()
    turn_id = service.store.load_session(sid)["active_turn_id"]
    service.prepare(sid, turn_id, adapter, lambda name, arguments: {"ok": True}, lambda *a, **k: None)

    adapter.session.read_tool_handler("loopx_collaboration", {
        "action": "start", "binding_id": "review", "operation_id": "op-1",
        "brief": {"schema_version": "collaboration_brief_v0"}})

    assert pinned == [{"session_id": sid, "turn_id": turn_id}]


# The public reference enumerates the wake receipt vocabulary, and operators read
# those names out of the operation record. A rename in the planner that the docs
# do not follow leaves a machine state nobody can look up, so the two are pinned
# together here instead of by review attention.
WAKE_REASONS = {
    "pending": {"lead_turn_active", "lead_paused", "allowance_exhausted", "wake_dispatch_pending"},
    "refused": {
        "goal_stopped", "lead_unbound", "binding_revoked", "native_goal_complete",
        "native_goal_absent", "wake_identity_conflict", "wake_turn_ended_unstarted",
        "no_wake_owner",
    },
}


def test_the_documented_wake_vocabulary_matches_the_planner():
    """Every documented reason is produced by the typed owner, and none is stale."""
    source = (Path(__file__).resolve().parents[1] / "loopx/control_plane/collaboration/chat_mode.ts").read_text()
    for state, reasons in WAKE_REASONS.items():
        for reason in sorted(reasons):
            assert f'outcome("{state}", "{reason}")' in source, f"{reason} is documented but never produced"


def test_the_public_reference_lists_every_wake_reason(markdown_path=None):
    """The reference names every reason the planner can write, in both languages."""
    reference = (Path(__file__).resolve().parents[1] / "docs/reference/goal-chat-continuation.md").read_text()
    for state, reasons in WAKE_REASONS.items():
        for reason in sorted(reasons):
            assert reference.count(reason) >= 2, (
                f"{reason} must be listed in the English and Chinese wake-receipt lists"
            )
    assert "wake_turn_not_started" not in reference, "the retired reason name is still documented"
