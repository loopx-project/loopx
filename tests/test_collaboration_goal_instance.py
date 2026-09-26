import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
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
from loopx.capabilities.manager_context.roundtrip import drain, report
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


def _manager_request(root: Path, registry: Path):
    store = ChatSessionStore(root)
    session = store.create_session(
        goal_id="loopx-manager",
        agent_id="codex",
        adapter_kind="codex_app_server",
        upstream_thread_id="fixture",
        channel_id="manager",
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


def test_exact_return_releases_lifetime_lock_around_provider_io(
    tmp_path: Path,
) -> None:
    registry = _create_source_registry(tmp_path)
    store, _, receipt = _external_manager_request(tmp_path, registry)

    entered = threading.Event()
    release = threading.Event()

    class Transport:
        calls = 0

        def __call__(self, *_args):
            self.calls += 1
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
        )
        assert entered.wait(timeout=5)
        _recreate(registry)
        second = executor.submit(
            drain,
            tmp_path,
            registry,
            store,
            transport,
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


def test_exact_external_return_verifies_after_recreation_without_resend(
    tmp_path: Path,
) -> None:
    registry = _create_source_registry(tmp_path)
    store, _, receipt = _external_manager_request(tmp_path, registry)

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
            return {
                "external_write_performed": True,
                "reply_verified": False,
            }

        def verify(self, *_args):
            self.verify_calls += 1
            return {
                "ok": True,
                "verification_performed": True,
                "reply_verified": True,
            }

    transport = Transport()
    assert drain(tmp_path, registry, store, transport) == 1
    first = json.loads(
        (
            _root(tmp_path)
            / "replies"
            / receipt["request_id"]
            / "conclusion.delivery.json"
        ).read_text(encoding="utf-8")
    )
    assert first["status"] == "verification_required"
    assert first["goal_ref"] == receipt["goal_ref"]

    _recreate(registry)
    assert drain(tmp_path, registry, store, transport) == 1
    assert transport.send_calls == 1
    assert transport.verify_calls == 1
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
