import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from loopx.capabilities.manager_context import (
    ENTRY_SCHEMA,
    INSTRUCTION,
    _hash,
    _root,
    _write,
    acknowledge,
    deliver,
    POLICY_SCHEMA,
    register_ingress,
)
from loopx.capabilities.manager_context.roundtrip import (
    EXACT_DELIVERY_ADMISSION_SECONDS,
    ReturnResolutionBlocked,
    drain,
    report,
)
from loopx.capabilities.manager_context.tracking import query
from loopx.chat_store import ChatSessionStore
from loopx.collaboration_mcp import register_collaboration_tools
from loopx.control_plane.collaboration.peers import read_inbox, request
from loopx.control_plane.goals.source_session_registry_state import guard_path
from loopx.control_plane.projects.registry_codec import (
    source_session_registry_transaction,
)
from loopx.file_lock import exclusive_cross_runtime_file_lock


INSTANCE_A = "ginst_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
INSTANCE_B = "ginst_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def _source_payload(root: Path, instance_id: str) -> dict:
    return {
        "profile_id": "source_session_v1",
        "goals": [
            {
                "id": "delivery",
                "repo": str(root),
                "status": "active",
                "goal_instance_id": instance_id,
                "coordination": {
                    "registered_agents": ["builder", "reviewer"],
                },
            }
        ],
        "session_bindings": [],
        "session_receipts": [],
        "lifetime_receipts": [],
    }


def _create_source_registry(root: Path) -> Path:
    registry = root / ".loopx" / "registry.json"
    with source_session_registry_transaction(
        registry,
        operation="test_create_source_registry",
        create=lambda: _source_payload(root, INSTANCE_A),
    ) as transaction:
        transaction.commit(transaction.payload_copy())
    return registry


def _recreate(registry: Path) -> None:
    with exclusive_cross_runtime_file_lock(
        guard_path(registry, "delivery"),
        operation="test_recreate_goal",
    ):
        with source_session_registry_transaction(
            registry,
            operation="test_recreate_goal_registry",
        ) as transaction:
            payload = transaction.payload_copy()
            payload["goals"][0]["goal_instance_id"] = INSTANCE_B
            transaction.commit(payload)


def _brief(purpose: str) -> dict:
    return {
        "schema_version": "collaboration_brief_v0",
        "purpose": purpose,
        "context": "Use the current Goal instance only.",
        "constraints": ["Do not infer continuity from the Goal alias."],
        "inputs": [],
        "acceptance": ["The result remains bound to its originating instance."],
        "return_requirement": "Return one evidence-backed conclusion.",
    }


def _manager_request(root: Path, registry: Path, *, channel="manager"):
    store = ChatSessionStore(root)
    session = store.create_session(
        goal_id="loopx-manager" if channel == "manager" else "delivery",
        agent_id="codex",
        adapter_kind="codex_app_server",
        upstream_thread_id="fixture",
        channel_id=channel,
    )
    turn, _ = store.create_turn(
        session["session_id"],
        client_turn_id="owner-request",
        message="Check the current delivery plan.",
        origin="web",
    )
    receipt = deliver(
        root,
        registry,
        session=session,
        turn=turn,
        request={
            "goal_id": "delivery",
            "agent_id": "builder",
            "brief": _brief("Check the delivery plan"),
        },
    )
    store.update_turn(
        session["session_id"],
        turn["turn_id"],
        status="completing",
        response={
            "message": "Delegated",
            "context_handoff_receipt": receipt,
        },
    )
    store.finalize_managed_turn_completion(
        session["session_id"],
        turn["turn_id"],
    )
    return store, session, receipt


def _external_manager_request(root: Path, registry: Path):
    store = ChatSessionStore(root)
    session = store.create_session(
        goal_id="loopx-manager",
        agent_id="codex",
        adapter_kind="codex_app_server",
        upstream_thread_id="fixture",
        channel_id="manager.external.fixture",
    )
    turn, _ = store.create_turn(
        session["session_id"],
        client_turn_id="owner-request",
        message="Check the current delivery plan.",
        origin="lark",
    )
    target = {"goal_id": "delivery", "agent_id": "builder"}
    _write(
        _root(root) / "policy.json",
        {
            "schema_version": POLICY_SCHEMA,
            "sources": {
                session["channel_id"]: {
                    "sender_ids": ["owner"],
                    "targets": [target],
                }
            },
        },
    )
    register_ingress(
        root,
        session_id=session["session_id"],
        client_turn_id=turn["client_turn_id"],
        channel=session["channel_id"],
        sender_id="owner",
        message=turn["message"],
        source_id="lark:source",
    )
    receipt = deliver(
        root,
        registry,
        session=session,
        turn=turn,
        request=target,
    )
    store.update_turn(
        session["session_id"],
        turn["turn_id"],
        status="completing",
        response={
            "message": "Delegated",
            "context_handoff_receipt": receipt,
        },
    )
    store.finalize_managed_turn_completion(
        session["session_id"],
        turn["turn_id"],
    )
    acknowledge(
        root,
        "delivery",
        "builder",
        receipt["request_id"],
        "adopt",
        "Work in A.",
        registry=registry,
        caller_goal_ref=receipt["goal_ref"],
    )
    report(
        root,
        "delivery",
        "builder",
        receipt["request_id"],
        "conclusion",
        "A completed its bounded review.",
        registry=registry,
        caller_goal_ref=receipt["goal_ref"],
    )
    return store, session, receipt


def test_recreated_goal_cannot_observe_or_mutate_prior_instance_requests(
    tmp_path: Path,
) -> None:
    registry = _create_source_registry(tmp_path)
    _, _, receipt = _manager_request(tmp_path, registry)
    goal_ref_a = receipt["goal_ref"]

    first = read_inbox(
        tmp_path,
        registry,
        "delivery",
        "builder",
        caller_goal_ref=goal_ref_a,
    )
    assert [item["request_id"] for item in first["items"]] == [
        receipt["request_id"]
    ]
    acknowledge(
        tmp_path,
        "delivery",
        "builder",
        receipt["request_id"],
        "adopt",
        "Work in A.",
        registry=registry,
        caller_goal_ref=goal_ref_a,
    )

    _recreate(registry)

    assert read_inbox(
        tmp_path,
        registry,
        "delivery",
        "builder",
    )["items"] == []
    with pytest.raises((FileNotFoundError, ValueError)):
        acknowledge(
            tmp_path,
            "delivery",
            "builder",
            receipt["request_id"],
            "reject",
            "B must not mutate A.",
            registry=registry,
        )


def test_late_prior_instance_result_returns_only_to_its_saved_conversation(
    tmp_path: Path,
) -> None:
    registry = _create_source_registry(tmp_path)
    store, session, receipt = _manager_request(tmp_path, registry)
    goal_ref_a = receipt["goal_ref"]
    acknowledge(
        tmp_path,
        "delivery",
        "builder",
        receipt["request_id"],
        "adopt",
        "Work in A.",
        registry=registry,
        caller_goal_ref=goal_ref_a,
    )
    _recreate(registry)

    report(
        tmp_path,
        "delivery",
        "builder",
        receipt["request_id"],
        "conclusion",
        "A completed its bounded review.",
        registry=registry,
        caller_goal_ref=goal_ref_a,
    )
    assert drain(tmp_path, registry, store, None) == 1
    returned = [
        item
        for item in store.messages(session["session_id"])
        if item.get("origin") == "manager_followup"
    ]
    assert len(returned) == 1
    assert "A completed its bounded review." in returned[0]["text"]
    state = json.loads(
        (
            _root(tmp_path)
            / "replies"
            / receipt["request_id"]
            / "conclusion.delivery.json"
        ).read_text(encoding="utf-8")
    )
    assert state["status"] == "delivered"
    assert state["goal_ref"] == goal_ref_a

    rows = query(
        tmp_path,
        registry,
        goal_ids=["delivery"],
        owner_scope=True,
        request_id=receipt["request_id"],
    )["rows"]
    assert rows[0]["goal_ref"] == goal_ref_a
    with pytest.raises((FileNotFoundError, ValueError)):
        report(
            tmp_path,
            "delivery",
            "builder",
            receipt["request_id"],
            "conclusion",
            "B cannot replace A's result.",
            registry=registry,
        )


def test_same_peer_operation_id_is_distinct_after_goal_recreation(
    tmp_path: Path,
) -> None:
    registry = _create_source_registry(tmp_path)
    first = request(
        tmp_path,
        registry,
        "delivery",
        "builder",
        "reviewer",
        "review-one",
        _brief("Review A"),
    )
    _recreate(registry)
    second = request(
        tmp_path,
        registry,
        "delivery",
        "builder",
        "reviewer",
        "review-one",
        _brief("Review B"),
    )

    assert first["request_id"] != second["request_id"]
    current = read_inbox(tmp_path, registry, "delivery", "reviewer")
    assert [item["request_id"] for item in current["items"]] == [
        second["request_id"]
    ]


def test_exact_inbox_cursor_cannot_cross_goal_recreation(tmp_path: Path) -> None:
    registry = _create_source_registry(tmp_path)
    for index in range(21):
        request(
            tmp_path,
            registry,
            "delivery",
            "builder",
            "reviewer",
            f"review-a-{index}",
            _brief(f"Review A item {index}"),
        )
    first = read_inbox(tmp_path, registry, "delivery", "reviewer")
    assert first["has_more"]

    _recreate(registry)

    with pytest.raises(ValueError, match="cursor scope mismatch"):
        read_inbox(
            tmp_path,
            registry,
            "delivery",
            "reviewer",
            cursor=first["next_cursor"],
        )


def test_exact_return_single_flight_outlives_admission_without_holding_lifetime(
    tmp_path: Path,
) -> None:
    registry = _create_source_registry(tmp_path)
    store, _, receipt = _external_manager_request(tmp_path, registry)
    admitted_at = datetime(2026, 9, 28, tzinfo=timezone.utc)

    entered = threading.Event()
    release = threading.Event()

    class Transport:
        calls = 0

        def __call__(self, *_args):
            self.calls += 1
            if self.calls == 1:
                entered.set()
                assert release.wait(timeout=5)
            return {
                "reply_verified": True,
                "idempotency_key": "sha256:provider-proof",
            }

    transport = Transport()
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            drain,
            tmp_path,
            registry,
            store,
            transport,
            now=admitted_at,
        )
        assert entered.wait(timeout=5)
        _recreate(registry)
        second = executor.submit(
            drain,
            tmp_path,
            registry,
            store,
            transport,
            now=admitted_at
            + timedelta(seconds=EXACT_DELIVERY_ADMISSION_SECONDS + 1),
        )
        assert second.result(timeout=5) == 0
        release.set()
        assert first.result(timeout=5) == 1

    assert transport.calls == 1
    state = json.loads(
        (
            _root(tmp_path)
            / "replies"
            / receipt["request_id"]
            / "conclusion.delivery.json"
        ).read_text(encoding="utf-8")
    )
    assert state["status"] == "delivered"
    assert state["goal_ref"] == receipt["goal_ref"]
    assert "admission" not in state


def test_exact_external_return_does_not_resend_after_expired_unknown_admission(
    tmp_path: Path,
) -> None:
    registry = _create_source_registry(tmp_path)
    store, _, receipt = _external_manager_request(tmp_path, registry)
    admitted_at = datetime(2026, 9, 28, tzinfo=timezone.utc)
    state_path = (
        _root(tmp_path)
        / "replies"
        / receipt["request_id"]
        / "conclusion.delivery.json"
    )
    _write(
        state_path,
        {
            "status": "admitted",
            "goal_ref": receipt["goal_ref"],
            "admission": {
                "token": "interrupted-writer",
                "prior_status": "queued",
                "admitted_at": admitted_at.isoformat(),
                "expires_at": (
                    admitted_at
                    + timedelta(seconds=EXACT_DELIVERY_ADMISSION_SECONDS)
                ).isoformat(),
            },
        },
    )

    class Transport:
        calls = 0

        def __call__(self, *_args):
            self.calls += 1
            return {
                "reply_verified": True,
                "idempotency_key": "sha256:unexpected-resend",
            }

    transport = Transport()
    assert (
        drain(
            tmp_path,
            registry,
            store,
            transport,
            now=admitted_at
            + timedelta(seconds=EXACT_DELIVERY_ADMISSION_SECONDS + 1),
        )
        == 0
    )
    assert transport.calls == 0
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state == {
        "status": "explicit_unverified",
        "error": "provider_delivery_unverified",
        "goal_ref": receipt["goal_ref"],
    }


@pytest.mark.parametrize("verification_failure, terminal", [
    (None, False),
    ("route lookup temporarily unavailable", False),
    ("authorization service read timed out", False),
    ("initial reply read interrupted", False),
    ("original_route_unavailable", True),
    ("return_authorization_unavailable", True),
    ("initial_delivery_receipt_unavailable", True),
])
def test_exact_external_return_verifies_after_recreation_without_resend(
    tmp_path: Path, verification_failure: str | None, terminal: bool, monkeypatch,
) -> None:
    observations = []
    monkeypatch.setattr('loopx.usage_ping.observe_verified_return', lambda: observations.append('verified'))
    registry = _create_source_registry(tmp_path)
    store, _, receipt = _external_manager_request(tmp_path, registry)
    admitted_at = datetime(2026, 9, 28, tzinfo=timezone.utc)

    class Transport:
        send_calls = 0
        verify_calls = 0

        def send_with_attempt(
            self,
            _route,
            _session,
            _turn,
            _text,
            record_attempt,
        ):
            self.send_calls += 1
            record_attempt(
                {
                    "schema_version": "manager_return_delivery_attempt_v0",
                    "provider": "lark",
                    "message_ref": "om_exact_reply",
                    "intent_digest": "sha256:" + "a" * 64,
                    "provider_receipt": "sha256:" + "b" * 64,
                }
            )
            raise SystemExit("simulated sender crash after attempt persistence")

        def verify(self, *_args):
            self.verify_calls += 1
            if self.verify_calls == 1 and verification_failure is not None:
                if terminal:
                    raise ReturnResolutionBlocked(verification_failure, "Resolution blocked")
                raise RuntimeError(verification_failure)
            return {
                "ok": True,
                "verification_performed": True,
                "reply_verified": True,
            }

    transport = Transport()
    with pytest.raises(
        SystemExit,
        match="simulated sender crash after attempt persistence",
    ):
        drain(
            tmp_path,
            registry,
            store,
            transport,
            now=admitted_at,
        )
    first = json.loads(
        (
            _root(tmp_path)
            / "replies"
            / receipt["request_id"]
            / "conclusion.delivery.json"
        ).read_text(encoding="utf-8")
    )
    assert first["status"] == "admitted"
    assert first["attempt"]["message_ref"] == "om_exact_reply"
    assert first["goal_ref"] == receipt["goal_ref"]
    assert observations == []  # An unverified provider attempt is not a return.

    _recreate(registry)
    assert (
        drain(
            tmp_path,
            registry,
            store,
            transport,
            now=admitted_at
            + timedelta(seconds=EXACT_DELIVERY_ADMISSION_SECONDS + 1),
        )
        == 1
    )
    assert transport.send_calls == 1
    assert transport.verify_calls == 1
    state_path = (
        _root(tmp_path) / "replies" / receipt["request_id"] / "conclusion.delivery.json"
    )
    if verification_failure is not None:
        failed = json.loads(state_path.read_text(encoding="utf-8"))
        assert failed["goal_ref"] == receipt["goal_ref"]
        assert failed["status"] == ("explicit_unverified" if terminal else "retry_pending")
        if terminal:
            assert failed["error"] == verification_failure
        else:
            assert failed["attempt"] == first["attempt"]
            # Another immediate pump must honor backoff, even after recreation.
            drain(tmp_path, registry, ChatSessionStore(tmp_path), transport,
                  now=admitted_at + timedelta(seconds=EXACT_DELIVERY_ADMISSION_SECONDS + 1))
            assert transport.verify_calls == 1
        drain(tmp_path, registry, ChatSessionStore(tmp_path), transport,
              now=admitted_at + timedelta(days=1))
        assert transport.send_calls == 1
        assert transport.verify_calls == (1 if terminal else 2)
        if terminal:
            assert json.loads(state_path.read_text(encoding="utf-8")) == failed
            assert observations == []
            return
    state = json.loads(
        (
            _root(tmp_path)
            / "replies"
            / receipt["request_id"]
            / "conclusion.delivery.json"
        ).read_text(encoding="utf-8")
    )
    assert state["status"] == "delivered"
    assert state["goal_ref"] == receipt["goal_ref"]
    assert state["verification"] == "reconciled_after_restart"
    drain(tmp_path, registry, ChatSessionStore(tmp_path), transport, now=admitted_at + timedelta(days=2))
    assert observations == ['verified']


def test_long_lived_mcp_keeps_its_captured_instance_after_recreation(
    tmp_path: Path,
) -> None:
    registry = _create_source_registry(tmp_path)
    _, _, receipt = _manager_request(tmp_path, registry)

    class Server:
        def __init__(self) -> None:
            self.tools = {}

        def tool(self):
            def register(function):
                self.tools[function.__name__] = function
                return function

            return register

    server = Server()
    register_collaboration_tools(
        server,
        tmp_path,
        registry,
        "delivery",
        "builder",
        tmp_path,
    )
    server.tools["assess_request"](
        receipt["request_id"],
        "adopt",
        "Work in A.",
    )
    _recreate(registry)

    assert server.tools["read_context"]()["items"] == []
    with pytest.raises(ValueError, match="stale_goal_instance"):
        server.tools["request_peer"](
            "reviewer",
            "stale-review",
            _brief("Do not create work in B"),
        )
    result = server.tools["return_result"](
        receipt["request_id"],
        "A completed its bounded review.",
    )
    assert result["status"] == "queued_for_original_conversation"


def test_manager_inbox_cli_reads_and_decides_current_exact_request(
    tmp_path: Path,
) -> None:
    registry = _create_source_registry(tmp_path)
    receipt = request(
        tmp_path,
        registry,
        "delivery",
        "builder",
        "reviewer",
        "cli-review",
        _brief("Review through the CLI"),
    )
    base = [
        sys.executable,
        "-m",
        "loopx.cli",
        "--runtime-root",
        str(tmp_path),
        "--registry",
        str(registry),
        "manager-inbox",
    ]

    read = subprocess.run(
        [
            *base,
            "read",
            "--goal-id",
            "delivery",
            "--agent-id",
            "reviewer",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert read.returncode == 0, (read.stdout, read.stderr)
    assert json.loads(read.stdout)["items"][0]["goal_ref"] == {
        "goal_id": "delivery",
        "goal_instance_id": INSTANCE_A,
    }

    decision = subprocess.run(
        [
            *base,
            "acknowledge",
            "--goal-id",
            "delivery",
            "--agent-id",
            "reviewer",
            "--request-id",
            receipt["request_id"],
            "--decision",
            "adopt",
            "--reason",
            "Review the exact request.",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert decision.returncode == 0, (decision.stdout, decision.stderr)
    assert json.loads(decision.stdout)["goal_ref"]["goal_instance_id"] == INSTANCE_A


def test_legacy_delivery_keeps_v1_paths_ids_and_json_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import loopx.capabilities.manager_context.roundtrip as roundtrip
    import loopx.capabilities.manager_context.tracking as tracking

    fixed_time = "2026-09-26T00:00:00+00:00"
    monkeypatch.setattr(tracking, "_now", lambda: fixed_time)
    monkeypatch.setattr(roundtrip, "_now", lambda: fixed_time)
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "goals": [
                    {
                        "id": "delivery",
                        "repo": str(tmp_path),
                        "coordination": {"registered_agents": ["builder"]},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    session = {"session_id": "manager-session", "channel_id": "manager"}
    turn = {
        "client_turn_id": "request-one",
        "origin": "web",
        "message": "Check the plan.",
    }
    target = {"goal_id": "delivery", "agent_id": "builder"}
    source_id = "web:" + _hash(
        [session["session_id"], turn["client_turn_id"]]
    )
    request_id = _hash([source_id, target])

    receipt = deliver(
        tmp_path,
        registry,
        session=session,
        turn=turn,
        request=target,
    )

    assert receipt["request_id"] == request_id
    assert "goal_ref" not in receipt
    entry = {
        "schema_version": ENTRY_SCHEMA,
        "request_id": request_id,
        **target,
        "source_id": source_id,
        "message": turn["message"],
        "instruction": INSTRUCTION,
        "delivered_at": fixed_time,
        "source_channel": "manager",
    }
    route = {
        "request_id": request_id,
        **target,
        "source_id": source_id,
        "session_id": session["session_id"],
        "client_turn_id": turn["client_turn_id"],
        "channel_id": session["channel_id"],
        "registered_at": fixed_time,
    }
    entry_path = (
        _root(tmp_path)
        / "entries"
        / _hash(target)
        / f"{request_id}.json"
    )
    route_path = _root(tmp_path) / "roundtrips" / f"{request_id}.json"
    assert entry_path.read_bytes() == json.dumps(
        entry,
        ensure_ascii=False,
    ).encode()
    assert route_path.read_bytes() == json.dumps(
        route,
        ensure_ascii=False,
    ).encode()


@pytest.mark.parametrize("channel", ["manager", "goal.delivery"])
@pytest.mark.parametrize("recreated", [False, True])
def test_app_snapshot_retains_exact_receiver_disposition(tmp_path, recreated, channel):
    import http.client
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler

    registry = _create_source_registry(tmp_path)
    store, session, receipt = _manager_request(tmp_path, registry, channel=channel)
    read_inbox(tmp_path, registry, "delivery", "builder")
    acknowledge(
        tmp_path, "delivery", "builder", receipt["request_id"], "defer",
        "Waiting for the requested public source.",
        registry=registry, caller_goal_ref=receipt["goal_ref"],
    )
    if recreated:
        _recreate(registry)
    # Exercise the production HTTP snapshot route, reloading the real file store.
    # No model, external message or active Goal is involved.
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.verbose = False
    server.registry_path = registry
    server.runtime_root = tmp_path
    server.chat_store = ChatSessionStore(tmp_path)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    connection = http.client.HTTPConnection(*server.server_address, timeout=10)
    try:
        connection.request("GET", f"/api/chat/sessions/{session['session_id']}")
        response = connection.getresponse()
        snapshot = json.loads(response.read())
        assert response.status == 200
    finally:
        connection.close()
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()
    cards = [m["collaboration"] for m in snapshot["messages"] if m.get("collaboration")]
    assert len(cards) == 1
    assert cards[0]["request_id"] == receipt["request_id"]
    assert cards[0]["read_status"] == "supplied"
    assert cards[0]["decision"] == "defer"
    assert cards[0]["decision_reason"] == "Waiting for the requested public source."


@pytest.mark.parametrize("damage", ["route_instance", "receipt_instance", "registry_missing"])
def test_app_readback_never_substitutes_a_different_instance(tmp_path, damage):
    from loopx.capabilities.manager_context.roundtrip import project_chat_session_snapshot

    registry = _create_source_registry(tmp_path)
    store, session, receipt = _manager_request(tmp_path, registry)
    if damage == "route_instance":
        route_path = _root(tmp_path) / "roundtrips" / (receipt["request_id"] + ".json")
        route = json.loads(route_path.read_text())
        route["goal_ref"]["goal_instance_id"] = INSTANCE_B
        _write(route_path, route)
    elif damage == "receipt_instance":
        turn = store.turn_for_client(session["session_id"], "owner-request")
        response = turn["response"]
        response["context_handoff_receipt"]["goal_ref"]["goal_instance_id"] = INSTANCE_B
        store.update_turn(session["session_id"], turn["turn_id"], response=response)
    else:
        registry = tmp_path / "missing-registry.json"
    before = store.messages(session["session_id"])
    snapshot = project_chat_session_snapshot(
        tmp_path, store, session["session_id"], registry=registry,
    )
    assert snapshot["messages"] == before
    assert not any(row.get("collaboration") for row in snapshot["messages"])
