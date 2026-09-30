from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from loopx.chat_action_store import ActionConflictError, ChatActionStore
from loopx.chat_actions import ChatActionService, ProtectedActionGate
from loopx.control_plane.collaboration.operation_handoff import agent_operation_action
from loopx.control_plane.collaboration.inbox import pending
from loopx.capabilities.manager_context import turn_start_hook
from examples.operation_action_fixtures import (
    GOAL_ID,
    agent_result as _agent_result,
    confirmation as _confirmation,
    delivery as _delivery,
    digest as _digest,
    managed_handler as _managed_handler,
    request as _request,
    service as _service,
)


EXECUTION_ACTOR = {
    "goal_id": GOAL_ID,
    "agent_id": "finance-fixture-agent",
    "host_surface": "codex-app",
    "thread_id": "thread-fixture-original",
}


def _agent_request(service: ChatActionService) -> dict[str, object]:
    registry = json.loads(service.registry_path.read_text())
    registry["goals"][0]["coordination"]["thread_agent_bindings"] = [
        {key: value for key, value in EXECUTION_ACTOR.items() if key != "goal_id"}
    ]
    service.registry_path.write_text(json.dumps(registry))
    request = _request()
    parameters = request["normalized_parameters"]
    parameters["executor"] = {
        "kind": "agent_session",
        "revision": "agent-session-handoff-v0",
        "host_surface": EXECUTION_ACTOR["host_surface"],
        "thread_id": EXECUTION_ACTOR["thread_id"],
    }
    parameters["operation_kind"] = "finance.order.execute"
    parameters["projection"]["simulated"] = False
    parameters["projection"]["warning"] = (
        "Synthetic engineering terms; no live account or venue is used."
    )
    parameters["destination_account_ref"] = "account:synthetic-fixture"
    return request


def _claim_agent_operation(
    service: ChatActionService,
    store: ChatActionStore,
    *,
    idempotency_key: str | None = None,
) -> dict:
    request = _agent_request(service)
    if idempotency_key is not None:
        request["idempotency_key"] = idempotency_key
    proposal = service.preview(request)
    delivered = store.record_operation_delivery(
        proposal["proposal_id"], delivery=_delivery(proposal)
    )
    return store.decide_operation(
        proposal["proposal_id"],
        decision="confirm",
        confirmation=_confirmation(delivered),
    )


def test_browser_operation_fixture_needs_no_test_framework(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[1]
    script = (
        repo / "examples/personal-workspace-browser/confirmed-operation-fixtures.py"
    )
    isolated = tmp_path / "effect-server"
    isolated.mkdir()
    # -S excludes site packages (including pytest); LoopX itself has no Python
    # runtime dependencies. The release rehearsal separately installs the wheel.
    result = subprocess.run(
        [sys.executable, "-S", str(script)],
        cwd=tmp_path,
        env={
            **os.environ,
            "PYTHONPATH": str(repo),
            "TMPDIR": str(isolated),
            "TEMP": str(isolated),
            "TMP": str(isolated),
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    fixtures = json.loads(result.stdout)
    assert list(fixtures) == ["confirmed", "waiting", "unknown", "reconciled"]
    for proposal in fixtures.values():
        assert proposal["normalized_parameters"]["goal_id"] == "product-release"
        assert proposal["normalized_parameters"]["executor"]["kind"] == "managed_turn"
    assert (
        fixtures["unknown"]["operation"]["outcome"]["outcome"] == "submission_unknown"
    )
    assert (
        fixtures["reconciled"]["operation"]["outcome"]
        == fixtures["unknown"]["operation"]["outcome"]
    )
    assert (
        fixtures["reconciled"]["operation"]["reconciliation"]["outcome"]
        == "not_executed"
    )


def test_owned_managed_tool_uses_canonical_approval_once_without_desktop_binding(
    tmp_path: Path,
) -> None:
    service, store = _service(tmp_path)
    handler = _managed_handler(service, store)
    native = {"thread_id": "owned-managed-thread", "host_turn_id": "native-turn-1"}
    request = _request()
    request["normalized_parameters"].pop("executor")
    request["normalized_parameters"]["projection"]["simulated"] = False
    prepared = handler(
        "loopx_operation", {"action": "prepare", "request": request}, native
    )
    assert prepared["ok"] is True and prepared["execution_allowed"] is False
    proposal = prepared["proposal"]
    executor = proposal["normalized_parameters"]["executor"]
    assert executor["kind"] == "managed_turn"
    assert executor["session_id"] == native["thread_id"]
    args = {
        "action": "consume",
        "proposal_id": proposal["proposal_id"],
        "consumption_id": "managed-attempt",
    }
    assert handler("loopx_operation", args, native)["ok"] is False  # no human approval
    delivered = store.record_operation_delivery(
        proposal["proposal_id"], delivery=_delivery(proposal)
    )
    claimed = store.decide_operation(
        proposal["proposal_id"],
        decision="confirm",
        confirmation=_confirmation(delivered),
    )
    consumed = handler("loopx_operation", args, native)
    assert consumed["ok"] is True and consumed["execution_allowed"] is True
    assert handler("loopx_operation", args, native)["execution_allowed"] is False
    persisted = store.load(proposal["proposal_id"])
    assert persisted["operation"]["agent_handoff"]["host_turn_id"] == "native-turn-1"
    outcome = _agent_result(claimed, "managed-attempt", result="submission_unknown")
    reported = handler(
        "loopx_operation",
        {
            "action": "report",
            "proposal_id": proposal["proposal_id"],
            "outcome": outcome,
        },
        native,
    )
    assert reported["ok"] is True and reported["needs_reconciliation"] is True
    assert handler("loopx_operation", args, native)["execution_allowed"] is False
    final = {
        **outcome,
        "outcome": "not_executed",
        "external_write_performed": False,
        "reconciles_outcome_digest": reported["outcome_digest"],
    }
    recovered = handler(
        "loopx_operation",
        {"action": "report", "proposal_id": proposal["proposal_id"], "outcome": final},
        native,
    )
    assert recovered["ok"] is True and recovered["needs_reconciliation"] is False


def test_managed_pending_reuses_registered_agent_and_goal_instance_scope(
    tmp_path: Path,
) -> None:
    service, store = _service(tmp_path)
    handler = _managed_handler(service, store)
    native = {"thread_id": "owned-managed-thread", "host_turn_id": "native-turn-1"}
    request = _request()
    request["normalized_parameters"].pop("executor")
    request["normalized_parameters"]["projection"]["simulated"] = False
    proposal = handler(
        "loopx_operation", {"action": "prepare", "request": request}, native
    )["proposal"]
    delivered = store.record_operation_delivery(
        proposal["proposal_id"], delivery=_delivery(proposal)
    )
    store.decide_operation(
        proposal["proposal_id"],
        decision="confirm",
        confirmation=_confirmation(delivered),
    )
    consumed = handler(
        "loopx_operation",
        {
            "action": "consume",
            "proposal_id": proposal["proposal_id"],
            "consumption_id": "managed-attempt",
        },
        native,
    )
    assert consumed["execution_allowed"] is True
    args = {"action": "pending"}
    assert (
        handler("loopx_operation", args, native)["items"][0]["operation_id"]
        == proposal["proposal_id"]
    )

    registry = json.loads(service.registry_path.read_text())
    registry["goals"][0]["activation_state"] = "stopped"
    service.registry_path.write_text(json.dumps(registry))
    # Stopping execution does not erase the original evidence-reconciliation locator.
    historical = handler("loopx_operation", args, native)
    assert historical["ok"] is True and historical["items"][0]["needs_reconciliation"]
    assert historical["items"][0]["execution_allowed"] is False

    registry.update(
        profile_id="source_session_v1",
        session_bindings=[],
        session_receipts=[],
        lifetime_receipts=[],
    )
    registry["goals"][0]["goal_instance_id"] = "ginst_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    service.registry_path.write_text(json.dumps(registry))
    # A current exact-instance Inbox must not expose legacy, unbound locators.
    scoped = handler("loopx_operation", args, native)
    assert scoped["ok"] is True and scoped["items"] == []

    registry["goals"][0]["coordination"]["registered_agents"] = []
    service.registry_path.write_text(json.dumps(registry))
    rejected = handler("loopx_operation", args, native)
    assert rejected == {
        "ok": False,
        "error": "operation_admission_rejected",
        "execution_allowed": False,
    }


def test_managed_replacement_has_evidence_only_access_and_never_inherits_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import contextmanager
    from threading import Event, current_thread
    from loopx.control_plane.collaboration import operation_handoff
    from loopx.control_plane.turn_driver import codex_cli
    from loopx.control_plane.turn_driver.codex_cli import _discard_codex_cli_session

    service, store = _service(tmp_path)
    original = _managed_handler(service, store)
    native = {"thread_id": "owned-managed-thread", "host_turn_id": "native-turn-1"}
    request = _request()
    request["normalized_parameters"].pop("executor")
    request["normalized_parameters"]["projection"]["simulated"] = False
    proposal = original(
        "loopx_operation", {"action": "prepare", "request": request}, native
    )["proposal"]
    delivered = store.record_operation_delivery(
        proposal["proposal_id"], delivery=_delivery(proposal)
    )
    confirmed = store.decide_operation(
        proposal["proposal_id"],
        decision="confirm",
        confirmation=_confirmation(delivered),
    )
    args = {
        "action": "consume",
        "proposal_id": proposal["proposal_id"],
        "consumption_id": "managed-attempt",
    }
    assert original("loopx_operation", args, native)["execution_allowed"] is True
    unknown = _agent_result(confirmed, "managed-attempt", result="submission_unknown")
    reported = original(
        "loopx_operation",
        {
            "action": "report",
            "proposal_id": proposal["proposal_id"],
            "outcome": unknown,
        },
        native,
    )
    replacement = _managed_handler(
        service,
        store,
        session_id="replacement-thread",
        profile_digest="d" * 64,
        todo_id="todo-recovery",
        model="replacement-model",
        reasoning_effort="high",
    )
    replacement_native = {
        "thread_id": "replacement-thread",
        "host_turn_id": "native-recovery-turn",
    }
    inspect = {"action": "inspect", "proposal_id": proposal["proposal_id"]}
    assert replacement("loopx_operation", inspect, replacement_native)["ok"] is False
    _discard_codex_cli_session(
        store.root.parent.parent,
        lineage={
            "goal_id": GOAL_ID,
            "agent_id": "finance-fixture-agent",
            "todo_id": "todo-managed",
        },
    )
    observed = replacement("loopx_operation", inspect, replacement_native)
    assert observed["ok"] is True and observed["execution_allowed"] is False
    assert observed["access"]["permission"] == "historical_evidence_only"
    assert observed["access"]["owner"]["todo_id"] == "todo-recovery"
    assert observed["access"]["authority_source"] == "current_turn_session_binding"
    assert replacement("loopx_operation", args, replacement_native)["ok"] is False
    final = {
        **unknown,
        "outcome": "not_executed",
        "external_write_performed": False,
        "reconciles_outcome_digest": reported["outcome_digest"],
    }
    attempted, acquired = Event(), Event()
    original_lock = codex_cli.exclusive_file_lock
    original_binding = operation_handoff._binding
    original_write = ChatActionStore._write
    commits = []

    @contextmanager
    def observed_lock(path, *args, **kwargs):
        revoking = current_thread().name.startswith("managed-revoker")
        if revoking:
            attempted.set()
        with original_lock(path, *args, **kwargs) as proof:
            if revoking:
                acquired.set()
            yield proof

    def revoke():
        _discard_codex_cli_session(
            store.root.parent.parent,
            lineage={
                "goal_id": GOAL_ID,
                "agent_id": "finance-fixture-agent",
                "todo_id": "todo-recovery",
            },
        )
        commits.append("revocation")

    def record_report(self, payload):
        original_write(self, payload)
        commits.append("report")

    monkeypatch.setattr(codex_cli, "exclusive_file_lock", observed_lock)
    monkeypatch.setattr(ChatActionStore, "_write", record_report)
    with ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="managed-revoker"
    ) as executor:
        futures = []

        def interleaved_binding(registry_path, parameters, runtime_root):
            current = original_binding(registry_path, parameters, runtime_root)
            if parameters["executor"].get("todo_id") == "todo-recovery":
                futures.append(executor.submit(revoke))
                assert attempted.wait(5)
                assert not acquired.wait(0.2), (
                    "replacement revocation must wait for report commit"
                )
            return current

        monkeypatch.setattr(operation_handoff, "_binding", interleaved_binding)
        assert (
            replacement(
                "loopx_operation",
                {
                    "action": "report",
                    "proposal_id": proposal["proposal_id"],
                    "outcome": final,
                },
                replacement_native,
            )["ok"]
            is True
        )
        futures[0].result(timeout=5)
    assert commits == ["report", "revocation"]
    monkeypatch.setattr(operation_handoff, "_binding", original_binding)
    before = store.path.read_bytes()
    assert replacement("loopx_operation", inspect, replacement_native)["ok"] is False
    assert store.path.read_bytes() == before
    stored = store.load(proposal["proposal_id"])
    assert stored["operation"]["outcome"] == unknown
    assert stored["operation"]["reconciliation"] == final
    assert (
        stored["operation"]["reconciliation_report"]["owner"]["thread_id"]
        == "replacement-thread"
    )
    assert (
        stored["normalized_parameters"]["executor"]["session_id"]
        == "owned-managed-thread"
    )


def test_managed_tool_rejects_actor_injection_native_mismatch_and_revoked_profile(
    tmp_path: Path,
) -> None:
    service, store = _service(tmp_path)
    handler = _managed_handler(service, store)
    native = {"thread_id": "owned-managed-thread", "host_turn_id": "native-turn-1"}
    assert (
        handler(
            "loopx_operation", {"action": "context", "actor": EXECUTION_ACTOR}, native
        )["ok"]
        is False
    )
    assert (
        handler(
            "loopx_operation", {"action": "context"}, {**native, "thread_id": "forged"}
        )["ok"]
        is False
    )
    request = _request()
    request["normalized_parameters"].pop("executor")
    request["normalized_parameters"]["projection"]["simulated"] = False
    prepared = handler(
        "loopx_operation", {"action": "prepare", "request": request}, native
    )
    proposal = prepared["proposal"]
    delivered = store.record_operation_delivery(
        proposal["proposal_id"], delivery=_delivery(proposal)
    )
    store.decide_operation(
        proposal["proposal_id"],
        decision="confirm",
        confirmation=_confirmation(delivered),
    )
    from loopx.control_plane.turn_driver.codex_cli import _store_codex_cli_session

    _store_codex_cli_session(
        store.root.parent.parent,
        lineage={
            "goal_id": GOAL_ID,
            "agent_id": "finance-fixture-agent",
            "todo_id": "todo-managed",
        },
        session_id="replacement-managed-thread",
        operation_profile_digest="d" * 64,
        operation_model="test-model",
        operation_reasoning_effort="xhigh",
    )
    assert (
        handler(
            "loopx_operation",
            {
                "action": "consume",
                "proposal_id": proposal["proposal_id"],
                "consumption_id": "managed-attempt",
            },
            native,
        )["ok"]
        is False
    )
    assert store.load(proposal["proposal_id"])["operation"].get("agent_handoff") is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("revision", "future-revision"),
        ("host_surface", "unregistered-host"),
        ("thread_id", "invalid thread"),
        ("extra_authority", True),
    ],
)
def test_agent_executor_invalid_binding_is_a_bounded_validation_error(
    tmp_path: Path, field: str, value: object
) -> None:
    service, store = _service(tmp_path)
    request = _agent_request(service)
    request["normalized_parameters"]["executor"][field] = value
    before = store.path.read_bytes() if store.path.exists() else None
    with pytest.raises(ValueError):
        service.preview(request)
    assert (store.path.read_bytes() if store.path.exists() else None) == before


def test_agent_handoff_requires_confirmation_and_original_session(
    tmp_path: Path,
) -> None:
    service, store = _service(tmp_path)
    proposal = service.preview(_agent_request(service))
    runtime = store.root.parent.parent
    args = dict(proposal_id=proposal["proposal_id"], actor=EXECUTION_ACTOR)
    preview = agent_operation_action(
        runtime, service.registry_path, action="inspect", **args
    )
    assert (
        preview["status"] == "awaiting_confirmation"
        and preview["execution_allowed"] is False
    )
    with pytest.raises(ActionConflictError, match="original bound session"):
        agent_operation_action(
            runtime,
            service.registry_path,
            action="inspect",
            **{**args, "actor": {**EXECUTION_ACTOR, "thread_id": "thread-other"}},
        )
    with pytest.raises(ActionConflictError, match="authenticated confirmation"):
        agent_operation_action(
            runtime,
            service.registry_path,
            action="consume",
            consumption_id="attempt-1",
            **args,
        )
    delivered = store.record_operation_delivery(
        proposal["proposal_id"], delivery=_delivery(proposal)
    )
    claimed = store.decide_operation(
        proposal["proposal_id"],
        decision="confirm",
        confirmation=_confirmation(delivered),
    )
    with pytest.raises(ActionConflictError, match="original bound session"):
        agent_operation_action(
            runtime,
            service.registry_path,
            action="consume",
            consumption_id="attempt-1",
            **{**args, "actor": {**EXECUTION_ACTOR, "thread_id": "thread-other"}},
        )
    with pytest.raises(ActionConflictError, match="not been consumed"):
        store.observe_operation_outcome(
            proposal["proposal_id"],
            outcome=_agent_result(claimed, "attempt-1"),
            agent_actor=EXECUTION_ACTOR,
            agent_binding_current=True,
        )
    assert not store.load(proposal["proposal_id"])["operation"].get("agent_handoff")


def test_agent_handoff_one_shot_consumption_survives_concurrent_retry_and_restart(
    tmp_path: Path,
) -> None:
    service, store = _service(tmp_path)
    proposal = _claim_agent_operation(service, store)
    runtime = store.root.parent.parent
    inbox = pending(runtime, GOAL_ID, EXECUTION_ACTOR["agent_id"])
    assert len(inbox["operation_handoffs"]) == 1
    assert inbox["operation_handoffs"][0]["host_delivery"] == "not_attempted"
    assert inbox["operation_handoffs"][0]["execution_allowed"] is False
    assert "operation_handoffs" not in pending(runtime, GOAL_ID, "different-agent")

    def consume(index: int) -> dict:
        return agent_operation_action(
            runtime,
            service.registry_path,
            proposal_id=proposal["proposal_id"],
            actor=EXECUTION_ACTOR,
            action="consume",
            consumption_id=f"attempt-{index}",
        )

    with ThreadPoolExecutor(max_workers=4) as workers:
        receipts = list(workers.map(consume, range(4)))
    first = [r for r in receipts if r["execution_allowed"]]
    assert len(first) == 1
    assert all(
        r["status"] == "already_consumed"
        for r in receipts
        if not r["execution_allowed"]
    )
    restarted = ChatActionStore(store.root)
    handoff = restarted.load(proposal["proposal_id"])["operation"]["agent_handoff"]
    assert consume(99)["execution_allowed"] is False
    assert (
        restarted.load(proposal["proposal_id"])["operation"]["agent_handoff"] == handoff
    )
    assert pending(runtime, GOAL_ID, EXECUTION_ACTOR["agent_id"])["operation_handoffs"][
        0
    ]["needs_reconciliation"]
    result = _agent_result(proposal, first[0]["consumption_id"])
    observed = agent_operation_action(
        runtime,
        service.registry_path,
        proposal_id=proposal["proposal_id"],
        actor=EXECUTION_ACTOR,
        action="report",
        outcome=result,
    )
    assert observed["execution_allowed"] is False and observed["outcome"] == result
    assert "operation_handoffs" not in pending(
        runtime, GOAL_ID, EXECUTION_ACTOR["agent_id"]
    )
    assert consume(99)["execution_allowed"] is False


@pytest.mark.parametrize("change", ["expires", "rebound", "stopped", "payload"])
def test_agent_handoff_fails_closed_on_expiry_binding_activation_or_terms_drift(
    tmp_path: Path, change: str
) -> None:
    service, store = _service(tmp_path)
    proposal = _claim_agent_operation(service, store)
    if change in {"rebound", "stopped"}:
        registry = json.loads(service.registry_path.read_text())
        if change == "rebound":
            registry["goals"][0]["coordination"]["thread_agent_bindings"][0][
                "thread_id"
            ] = "thread-replacement"
        else:
            registry["goals"][0]["activation_state"] = "stopped"
        service.registry_path.write_text(json.dumps(registry))
    else:
        data = json.loads(store.path.read_text())
        stored = data["proposals"][proposal["proposal_id"]]
        if change == "expires":
            stored["operation"]["expires_at"] = "2000-01-01T00:00:00Z"
        else:
            stored["normalized_parameters"]["payload"]["quantity"] = "2.00"
        store.path.write_text(json.dumps(data))
    before = store.path.read_bytes()
    with pytest.raises(ActionConflictError):
        agent_operation_action(
            store.root.parent.parent,
            service.registry_path,
            proposal_id=proposal["proposal_id"],
            actor=EXECUTION_ACTOR,
            action="consume",
            consumption_id="attempt-1",
        )
    assert store.path.read_bytes() == before


def test_binding_revocation_and_consumption_share_the_registry_commit_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from contextlib import contextmanager
    from threading import Event
    from loopx.control_plane.projects import registry_codec
    from loopx.control_plane.collaboration import operation_handoff
    from loopx.thread_agent_binding import unbind_thread_agent_in_registry

    service, store = _service(tmp_path)
    proposal = _claim_agent_operation(service, store)
    attempted, acquired = Event(), Event()
    original_transaction = registry_codec._registry_transaction
    original_binding = operation_handoff._binding
    original_write = ChatActionStore._write
    commits = []

    @contextmanager
    def observed_transaction(*args, **kwargs):
        attempted.set()
        with original_transaction(*args, **kwargs) as transaction:
            acquired.set()
            yield transaction

    def revoke():
        result = unbind_thread_agent_in_registry(
            registry_path=service.registry_path,
            goal_id=GOAL_ID,
            host_surface=EXECUTION_ACTOR["host_surface"],
            thread_id=EXECUTION_ACTOR["thread_id"],
            agent_id=EXECUTION_ACTOR["agent_id"],
            execute=True,
        )
        assert result["written"] is True
        commits.append("revocation")

    def record_consumption(self, payload):
        original_write(self, payload)
        commits.append("consumption")

    monkeypatch.setattr(registry_codec, "_registry_transaction", observed_transaction)
    monkeypatch.setattr(ChatActionStore, "_write", record_consumption)
    with ThreadPoolExecutor(max_workers=1) as executor:
        futures = []

        def interleaved_binding(*args):
            current = original_binding(*args)
            futures.append(executor.submit(revoke))
            assert attempted.wait(5)
            assert not acquired.wait(0.2), (
                "revocation cannot commit between binding read and consumption"
            )
            return current

        monkeypatch.setattr(operation_handoff, "_binding", interleaved_binding)
        result = agent_operation_action(
            store.root.parent.parent,
            service.registry_path,
            proposal_id=proposal["proposal_id"],
            actor=EXECUTION_ACTOR,
            action="consume",
            consumption_id="attempt-1",
        )
        assert result["execution_allowed"] is True
        futures[0].result(timeout=5)
    assert commits == ["consumption", "revocation"]
    monkeypatch.setattr(operation_handoff, "_binding", original_binding)
    before = store.path.read_bytes()
    with pytest.raises(ActionConflictError, match="binding is no longer current"):
        agent_operation_action(
            store.root.parent.parent,
            service.registry_path,
            proposal_id=proposal["proposal_id"],
            actor=EXECUTION_ACTOR,
            action="consume",
            consumption_id="attempt-2",
        )
    assert store.path.read_bytes() == before
    report = agent_operation_action(
        store.root.parent.parent,
        service.registry_path,
        proposal_id=proposal["proposal_id"],
        actor=EXECUTION_ACTOR,
        action="report",
        outcome=_agent_result(proposal, "attempt-1", result="not_executed"),
    )
    assert report["execution_allowed"] is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("consumption_id", "different-attempt"),
        ("payload_digest", "0" * 64),
        ("confirmation_digest", "0" * 64),
        ("claim_id", "different-claim"),
        ("executor_revision", "future-revision"),
        ("simulation", True),
        ("evidence_refs", []),
        ("external_write_performed", False),
    ],
)
def test_agent_result_is_bound_to_one_consumed_operation(
    tmp_path: Path, field: str, value: object
) -> None:
    service, store = _service(tmp_path)
    proposal = _claim_agent_operation(service, store)
    runtime = store.root.parent.parent
    args = dict(proposal_id=proposal["proposal_id"], actor=EXECUTION_ACTOR)
    consumed = agent_operation_action(
        runtime,
        service.registry_path,
        action="consume",
        consumption_id="attempt-1",
        **args,
    )
    outcome = {**_agent_result(proposal, consumed["consumption_id"]), field: value}
    before = store.path.read_bytes()
    with pytest.raises(ActionConflictError):
        agent_operation_action(
            runtime, service.registry_path, action="report", outcome=outcome, **args
        )
    assert store.path.read_bytes() == before


def test_unknown_result_never_grants_resubmission_and_late_evidence_does_not_require_active_goal(
    tmp_path: Path,
) -> None:
    service, store = _service(tmp_path)
    proposal = _claim_agent_operation(service, store)
    runtime = store.root.parent.parent
    args = dict(proposal_id=proposal["proposal_id"], actor=EXECUTION_ACTOR)
    agent_operation_action(
        runtime,
        service.registry_path,
        action="consume",
        consumption_id="attempt-1",
        **args,
    )
    registry = json.loads(service.registry_path.read_text())
    registry["goals"][0]["activation_state"] = "stopped"
    registry["goals"][0]["coordination"]["thread_agent_bindings"][0]["thread_id"] = (
        "thread-replacement"
    )
    service.registry_path.write_text(json.dumps(registry))
    outcome = _agent_result(proposal, "attempt-1", result="submission_unknown")
    reported = agent_operation_action(
        runtime, service.registry_path, action="report", outcome=outcome, **args
    )
    assert reported["outcome"] == outcome
    readback = agent_operation_action(
        runtime, service.registry_path, action="inspect", **args
    )
    assert readback["needs_reconciliation"] and not readback["execution_allowed"]
    assert not readback["binding_current"]
    from loopx.control_plane.collaboration.goal_instance_scope import (
        collaboration_goal_scope,
    )

    with collaboration_goal_scope(
        service.registry_path,
        goal_id=GOAL_ID,
        agents=(EXECUTION_ACTOR["agent_id"],),
        require_active=False,
    ) as scope:
        inbox = pending(runtime, GOAL_ID, EXECUTION_ACTOR["agent_id"], scope=scope)
    assert inbox["operation_handoffs"][0]["needs_reconciliation"]
    assert inbox["operation_handoffs"][0]["binding_current"] is False


def test_unknown_result_stays_in_original_inbox_past_expiry_until_bound_reconciliation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, store = _service(tmp_path)
    proposal = _claim_agent_operation(service, store)
    runtime = store.root.parent.parent
    args = dict(proposal_id=proposal["proposal_id"], actor=EXECUTION_ACTOR)
    agent_operation_action(
        runtime,
        service.registry_path,
        action="consume",
        consumption_id="attempt-1",
        **args,
    )
    unknown = _agent_result(proposal, "attempt-1", result="submission_unknown")
    report = agent_operation_action(
        runtime, service.registry_path, action="report", outcome=unknown, **args
    )
    assert report["status"] == "submission_unknown" and report["needs_reconciliation"]
    assert agent_operation_action(
        runtime,
        service.registry_path,
        action="consume",
        consumption_id="attempt-1",
        **args,
    )["needs_reconciliation"]
    for _ in range(2):
        assert (
            pending(runtime, GOAL_ID, EXECUTION_ACTOR["agent_id"])[
                "operation_handoffs"
            ][0]["status"]
            == "submission_unknown"
        )
    after_expiry = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    monkeypatch.setattr("loopx.chat_action_store._utc_now", lambda: after_expiry)
    projected = pending(runtime, GOAL_ID, EXECUTION_ACTOR["agent_id"])[
        "operation_handoffs"
    ][0]
    assert projected["needs_reconciliation"] and not projected["execution_allowed"]
    hook = turn_start_hook(
        runtime, service.registry_path, GOAL_ID, EXECUTION_ACTOR["agent_id"]
    ).producer()
    assert hook["agent_read_required"] and hook["observation_count"] == 1
    final = _agent_result(proposal, "attempt-1", result="not_executed")
    with pytest.raises(ActionConflictError, match="exact original unknown result"):
        agent_operation_action(
            runtime, service.registry_path, action="report", outcome=final, **args
        )
    final["reconciles_outcome_digest"] = _digest(unknown)
    settled = agent_operation_action(
        runtime, service.registry_path, action="report", outcome=final, **args
    )
    assert settled["outcome"] == final and not settled["needs_reconciliation"]
    assert settled["execution_allowed"] is False
    updated = store.load(proposal["proposal_id"])
    assert updated["operation"]["outcome"] == unknown
    assert updated["operation"]["reconciliation"] == final
    assert "operation_handoffs" not in pending(
        runtime, GOAL_ID, EXECUTION_ACTOR["agent_id"]
    )
    assert not agent_operation_action(
        runtime,
        service.registry_path,
        action="consume",
        consumption_id="attempt-2",
        **args,
    )["execution_allowed"]


def test_lifecycle_only_source_profile_cannot_acquire_new_operation_authority(
    tmp_path: Path,
) -> None:
    service, store = _service(tmp_path)
    registry = json.loads(service.registry_path.read_text())
    registry.update(
        profile_id="source_session_v1",
        session_bindings=[],
        session_receipts=[],
        lifetime_receipts=[],
    )
    first_instance = "ginst_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    registry["goals"][0]["goal_instance_id"] = first_instance
    service.registry_path.write_text(json.dumps(registry))
    request = _agent_request(service)
    before = store.path.read_bytes() if store.path.exists() else None
    with pytest.raises(ValueError, match="lifecycle-only profile"):
        service.preview(request)
    assert (store.path.read_bytes() if store.path.exists() else None) == before


@pytest.mark.parametrize("historical", [False, True])
def test_replacement_session_reconciles_under_its_current_binding_without_reconsumption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    historical: bool,
) -> None:
    from loopx.thread_agent_binding import (
        bind_thread_agent_in_registry,
        unbind_thread_agent_in_registry,
    )

    service, store = _service(tmp_path)
    proposal = _claim_agent_operation(service, store)
    runtime = store.root.parent.parent
    args = dict(proposal_id=proposal["proposal_id"], actor=EXECUTION_ACTOR)
    agent_operation_action(
        runtime,
        service.registry_path,
        action="consume",
        consumption_id="attempt-1",
        **args,
    )
    unknown = _agent_result(proposal, "attempt-1", result="submission_unknown")
    agent_operation_action(
        runtime, service.registry_path, action="report", outcome=unknown, **args
    )
    replacement = {**EXECUTION_ACTOR, "thread_id": "thread-fixture-replacement"}
    final = _agent_result(proposal, "attempt-1", result="not_executed")
    final["reconciles_outcome_digest"] = _digest(unknown)
    recovery_args = {**args, "actor": replacement}
    before = store.path.read_bytes()
    with pytest.raises(ActionConflictError):
        agent_operation_action(
            runtime, service.registry_path, action="inspect", **recovery_args
        )
    bind_thread_agent_in_registry(
        registry_path=service.registry_path, **replacement, execute=True
    )
    with pytest.raises(ActionConflictError):
        agent_operation_action(
            runtime,
            service.registry_path,
            action="report",
            outcome=final,
            **recovery_args,
        )
    assert store.path.read_bytes() == before
    unbind_thread_agent_in_registry(
        registry_path=service.registry_path, **EXECUTION_ACTOR, execute=True
    )
    if historical:
        registry = json.loads(service.registry_path.read_text())
        registry["goals"][0]["activation_state"] = "stopped"
        service.registry_path.write_text(json.dumps(registry))
        after_expiry = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        monkeypatch.setattr("loopx.chat_action_store._utc_now", lambda: after_expiry)
    inspected = agent_operation_action(
        runtime, service.registry_path, action="inspect", **recovery_args
    )
    assert inspected["route"] == EXECUTION_ACTOR
    assert inspected["access"]["owner"] == replacement
    assert inspected["access"]["permission"] == "historical_evidence_only"
    assert inspected["outcome"] == unknown and not inspected["binding_current"]
    for attempt in ("attempt-1", "attempt-2"):
        reason = "Goal is stopped" if historical else "original bound session"
        with pytest.raises(ActionConflictError, match=reason):
            agent_operation_action(
                runtime,
                service.registry_path,
                action="consume",
                consumption_id=attempt,
                **recovery_args,
            )
    settled = agent_operation_action(
        runtime, service.registry_path, action="report", outcome=final, **recovery_args
    )
    assert not settled["execution_allowed"] and not settled["needs_reconciliation"]
    updated = store.load(proposal["proposal_id"])
    assert updated["operation"]["outcome"] == unknown
    assert updated["operation"]["reconciliation"] == final
    provenance = updated["operation"]["reconciliation_report"]
    assert provenance["owner"] == replacement
    assert provenance["original_route"] == EXECUTION_ACTOR
    assert provenance["authority_source"] == "current_registry_binding"
    assert provenance["permission"] == "historical_evidence_only"
    stable = store.path.read_bytes()
    agent_operation_action(
        runtime, service.registry_path, action="report", outcome=final, **recovery_args
    )
    assert store.path.read_bytes() == stable

    with pytest.raises(ActionConflictError, match="already immutable"):
        agent_operation_action(
            runtime,
            service.registry_path,
            action="report",
            outcome={
                **final,
                "summary": "A conflicting historical result is not a retry.",
            },
            **recovery_args,
        )
    assert store.path.read_bytes() == stable

    unbind_thread_agent_in_registry(
        registry_path=service.registry_path, **replacement, execute=True
    )
    with pytest.raises(ActionConflictError):
        agent_operation_action(
            runtime,
            service.registry_path,
            action="report",
            outcome=final,
            **recovery_args,
        )
    assert store.path.read_bytes() == stable


@pytest.mark.parametrize("stage", ["initial", "reconciled"])
def test_recovery_binding_revocation_cannot_split_report_validation_from_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    from contextlib import contextmanager
    from threading import Event
    from loopx.control_plane.projects import registry_codec
    from loopx.control_plane.collaboration import operation_handoff
    from loopx.thread_agent_binding import (
        bind_thread_agent_in_registry,
        unbind_thread_agent_in_registry,
    )

    service, store = _service(tmp_path)
    proposal = _claim_agent_operation(service, store)
    runtime = store.root.parent.parent
    args = dict(proposal_id=proposal["proposal_id"], actor=EXECUTION_ACTOR)
    agent_operation_action(
        runtime,
        service.registry_path,
        action="consume",
        consumption_id="attempt-1",
        **args,
    )
    unknown = _agent_result(proposal, "attempt-1", result="submission_unknown")
    if stage == "reconciled":
        agent_operation_action(
            runtime, service.registry_path, action="report", outcome=unknown, **args
        )
    replacement = {**EXECUTION_ACTOR, "thread_id": "thread-fixture-replacement"}
    unbind_thread_agent_in_registry(
        registry_path=service.registry_path, **EXECUTION_ACTOR, execute=True
    )
    bind_thread_agent_in_registry(
        registry_path=service.registry_path, **replacement, execute=True
    )
    final = _agent_result(proposal, "attempt-1", result="not_executed")
    if stage == "reconciled":
        final["reconciles_outcome_digest"] = _digest(unknown)
    attempted, acquired = Event(), Event()
    original_transaction = registry_codec._registry_transaction
    original_binding = operation_handoff._binding
    original_write = ChatActionStore._write
    commits = []

    @contextmanager
    def observed_transaction(*args, **kwargs):
        attempted.set()
        with original_transaction(*args, **kwargs) as transaction:
            acquired.set()
            yield transaction

    def revoke():
        result = unbind_thread_agent_in_registry(
            registry_path=service.registry_path, **replacement, execute=True
        )
        assert result["written"]
        commits.append("revocation")

    def record_report(self, payload):
        original_write(self, payload)
        commits.append("report")

    monkeypatch.setattr(registry_codec, "_registry_transaction", observed_transaction)
    monkeypatch.setattr(ChatActionStore, "_write", record_report)
    with ThreadPoolExecutor(max_workers=1) as executor:
        futures = []

        def interleaved_binding(registry_path, parameters, runtime_root):
            current = original_binding(registry_path, parameters, runtime_root)
            if parameters["executor"]["thread_id"] == replacement["thread_id"]:
                futures.append(executor.submit(revoke))
                assert attempted.wait(5)
                assert not acquired.wait(0.2), (
                    "revocation must wait for historical report commit"
                )
            return current

        monkeypatch.setattr(operation_handoff, "_binding", interleaved_binding)
        result = agent_operation_action(
            runtime,
            service.registry_path,
            action="report",
            outcome=final,
            **{**args, "actor": replacement},
        )
        assert not result["execution_allowed"]
        futures[0].result(timeout=5)
    assert commits == ["report", "revocation"]
    monkeypatch.setattr(operation_handoff, "_binding", original_binding)
    before = store.path.read_bytes()
    with pytest.raises(ActionConflictError):
        agent_operation_action(
            runtime,
            service.registry_path,
            action="report",
            outcome=final,
            **{**args, "actor": replacement},
        )
    assert store.path.read_bytes() == before


def test_inbox_uses_shared_recovery_priority_and_explicit_overflow(
    tmp_path: Path,
) -> None:
    import subprocess
    import sys

    service, store = _service(tmp_path)
    operation_ids = set()
    for index in range(22):
        proposal = _claim_agent_operation(
            service, store, idempotency_key=f"overflow-{index}"
        )
        operation_ids.add(proposal["proposal_id"])
        args = dict(proposal_id=proposal["proposal_id"], actor=EXECUTION_ACTOR)
        agent_operation_action(
            store.root.parent.parent,
            service.registry_path,
            action="consume",
            consumption_id="attempt-1",
            **args,
        )
        agent_operation_action(
            store.root.parent.parent,
            service.registry_path,
            action="report",
            outcome=_agent_result(proposal, "attempt-1", result="submission_unknown"),
            **args,
        )
    inbox = pending(store.root.parent.parent, GOAL_ID, EXECUTION_ACTOR["agent_id"])
    assert len(inbox["operation_handoffs"]) == 20
    assert inbox["operation_handoff_pending_count"] == 22
    assert inbox["operation_handoff_overflow"]["reason"] == "attention_page_capacity"
    assert inbox["operation_handoff_overflow"]["count"] == 2
    cursor = inbox["operation_handoff_next_cursor"]
    assert cursor == inbox["operation_handoff_overflow"]["next_cursor"]
    before = store.path.read_bytes()
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--format",
            "json",
            "--registry",
            str(service.registry_path),
            "--runtime-root",
            str(store.root.parent.parent),
            "manager-inbox",
            "read",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            EXECUTION_ACTOR["agent_id"],
            "--operation-cursor",
            cursor,
        ],
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    rest = json.loads(process.stdout)
    assert len(rest["operation_handoffs"]) == 2
    assert rest["operation_handoff_next_cursor"] is None
    assert rest["operation_handoff_pending_count"] == 22
    assert {
        item["operation_id"]
        for item in [*inbox["operation_handoffs"], *rest["operation_handoffs"]]
    } == operation_ids
    assert all(
        item["needs_reconciliation"]
        for item in [*inbox["operation_handoffs"], *rest["operation_handoffs"]]
    )
    assert store.path.read_bytes() == before
    assert (
        len(
            pending(store.root.parent.parent, GOAL_ID, EXECUTION_ACTOR["agent_id"])[
                "operation_handoffs"
            ]
        )
        == 20
    )
    with pytest.raises(ValueError, match="cursor scope mismatch"):
        pending(
            store.root.parent.parent, GOAL_ID, "other-agent", operation_cursor=cursor
        )


def test_operation_preview_arms_one_canonical_gate_and_local_apply_cannot_claim(
    tmp_path: Path,
) -> None:
    service, store = _service(tmp_path)

    proposal = service.preview(_request())

    assert proposal["status"] == "gated"
    assert proposal["available_transitions"] == ["cancel"]
    assert proposal["operation"]["lifecycle_state"] == "awaiting_confirmation"
    assert proposal["gate"]["kind"] == "human_operation_confirmation"
    with pytest.raises(ProtectedActionGate, match="local apply"):
        service.apply(str(proposal["proposal_id"]))
    assert store.load(str(proposal["proposal_id"]))["status"] == "gated"


def test_operation_digest_change_cannot_reuse_idempotency_key(tmp_path: Path) -> None:
    service, _store = _service(tmp_path)
    service.preview(_request())
    changed = _request(
        payload={
            "schema_version": "finance_order_intent_v0",
            "side": "buy",
            "asset": "SYNTH",
            "quantity": "2.00",
        }
    )

    with pytest.raises(ActionConflictError, match="idempotency key"):
        service.preview(changed)


def test_operation_rejects_non_finite_payload_numbers(tmp_path: Path) -> None:
    service, _store = _service(tmp_path)
    request = _request(payload={"schema_version": "fixture", "price": float("nan")})

    with pytest.raises(ValueError, match="JSON"):
        service.preview(request)


def test_lark_decision_claims_once_and_restart_preserves_outcome(
    tmp_path: Path,
) -> None:
    service, store = _service(tmp_path)
    proposal = service.preview(_request())
    proposal_id = str(proposal["proposal_id"])
    delivered = store.record_operation_delivery(
        proposal_id, delivery=_delivery(proposal)
    )
    confirmation = _confirmation(delivered)

    forged = {**confirmation, "principal": "lark:ou_untrusted_fixture"}
    with pytest.raises(ActionConflictError, match="not authorized"):
        store.decide_operation(proposal_id, decision="confirm", confirmation=forged)

    claimed = store.decide_operation(
        proposal_id, decision="confirm", confirmation=confirmation
    )
    replay = store.decide_operation(
        proposal_id, decision="confirm", confirmation=confirmation
    )
    assert claimed["operation"]["lifecycle_state"] == "claimed"
    assert replay["operation"]["claim"] == claimed["operation"]["claim"]
    with pytest.raises(ActionConflictError, match="already consumed"):
        store.decide_operation(
            proposal_id,
            decision="confirm",
            confirmation={**confirmation, "event_id": "evt-operation-2"},
        )

    outcome = {
        "schema_version": "loopx_operation_outcome_v0",
        "outcome": "simulated_filled",
        "projection_verified": True,
        "operation_id": proposal_id,
        "payload_digest": claimed["operation"]["payload_digest"],
        "summary": "Simulation completed without an external write.",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "external_write_performed": False,
    }
    observed = store.observe_operation_outcome(proposal_id, outcome=outcome)
    restarted = ChatActionStore(store.root)

    assert observed["status"] == "applied"
    assert restarted.load(proposal_id)["operation"]["outcome"] == outcome
    assert restarted.observe_operation_outcome(proposal_id, outcome=outcome) == observed


def test_reject_is_terminal_without_executor_claim(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    proposal = service.preview(_request())
    proposal_id = str(proposal["proposal_id"])
    delivered = store.record_operation_delivery(
        proposal_id, delivery=_delivery(proposal)
    )

    rejected = store.decide_operation(
        proposal_id,
        decision="reject",
        confirmation=_confirmation(delivered),
    )

    assert rejected["status"] == "rejected"
    assert rejected["operation"]["claim"] is None
    assert rejected["operation"]["outcome"]["outcome"] == "rejected_by_operator"
