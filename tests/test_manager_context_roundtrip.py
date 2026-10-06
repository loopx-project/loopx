"""One delegation automatically returns a conclusion without a second question."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from loopx.capabilities.manager_context import (
    _root,
    _write,
    acknowledge,
    deliver,
    pending,
    register_ingress,
    POLICY_SCHEMA,
)
from loopx.capabilities.manager_context.roundtrip import (
    ReturnService,
    ReturnResolutionBlocked,
    _hash,
    drain,
    project_chat_return_deliveries,
    project_chat_session_snapshot,
    report,
    reply_status,
)
from loopx.chat_store import ChatSessionStore


@pytest.fixture
def flow(tmp_path):
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "goals": [
                    {
                        "id": "research",
                        "repo": str(tmp_path),
                        "coordination": {"registered_agents": ["worker"]},
                    }
                ]
            }
        )
    )
    store = ChatSessionStore(tmp_path)

    def create(external=False, project=False, brief=None):
        session = store.create_session(
            goal_id="research" if project else "loopx-manager",
            agent_id="codex",
            adapter_kind="codex_app_server",
            upstream_thread_id="test",
            channel_id="goal.research" if project else "manager.external.test" if external else "manager",
        )
        turn, _ = store.create_turn(
            session["session_id"],
            client_turn_id="question",
            message="Investigate this new constraint",
            origin="lark" if external else "web",
        )
        target = {"goal_id": "research", "agent_id": "worker"}
        if external:
            _write(
                _root(tmp_path) / "policy.json",
                {
                    "schema_version": POLICY_SCHEMA,
                    "sources": {
                        session["channel_id"]: {
                            "local_delivery_scope": "selected", "sender_ids": ["owner"],
                            "targets": [target],
                        }
                    },
                },
            )
            register_ingress(
                tmp_path,
                session_id=session["session_id"],
                client_turn_id="question",
                channel=session["channel_id"],
                sender_id="owner",
                message=turn["message"],
                source_id="lark:om_fixture_source",
            )
        receipt = deliver(
            tmp_path, registry, session=session, turn=turn,
            request={**target, **({"brief": brief} if brief else {})}
        )
        store.update_turn(
            session["session_id"],
            turn["turn_id"],
            status="completing",
            response={
                "message": "Delivered; a conclusion will return here.",
                "context_handoff_receipt": receipt,
            },
        )
        store.finalize_managed_turn_completion(session["session_id"], turn["turn_id"])
        return session, turn, receipt

    return tmp_path, registry, store, create


@pytest.mark.parametrize("project", [False, True], ids=["steward", "project"])
def test_single_request_returns_to_original_transcript_without_second_model_turn(flow, project):
    root, registry, store, create = flow
    session, turn, receipt = create(project=project)
    rid = receipt["request_id"]
    acknowledge(root, "research", "worker", rid, "adopt", "Private deliberation")
    assert pending(root, "research", "worker")["items"]
    report(
        root,
        "research",
        "worker",
        rid,
        "conclusion",
        "Checked the constraint, updated the existing plan, and recorded the validation result.",
    )
    assert not pending(root, "research", "worker")["items"]
    before_turns = list(
        (store.root / "sessions" / session["session_id"] / "turns").glob("*.json")
    )

    def unexpected(*_):
        raise AssertionError("owner conversation must not send external messages")

    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda _: drain(root, registry, store, unexpected), range(2)))
    # A new store models restart: no in-memory dedupe or new user prompt needed.
    drain(root, registry, ChatSessionStore(root), unexpected)
    rows = store.messages(session["session_id"])
    returned = [x for x in rows if x.get("origin") == "manager_followup"]
    assert len(returned) == 1 and returned[0]["turn_id"] == turn["turn_id"]
    assert "updated the existing plan" in returned[0]["text"]
    assert "Private deliberation" not in returned[0]["text"]
    assert len(
        list((store.root / "sessions" / session["session_id"] / "turns").glob("*.json"))
    ) == len(before_turns)
    assert reply_status(root, receipt)[0]["status"] == "delivered"


def test_conclusion_coalesces_unsent_intermediate_decision_and_is_immutable(flow):
    root, registry, store, create = flow
    session, _, r = create()
    rid = r["request_id"]
    with pytest.raises(ValueError, match="receiver decision"):
        report(root, "research", "worker", rid, "conclusion", "No change required.")
    acknowledge(root, "research", "worker", rid, "no_change", "Evidence unchanged")
    report(
        root,
        "research",
        "worker",
        rid,
        "decision",
        "Reviewing the original constraint.",
    )
    report(
        root,
        "research",
        "worker",
        rid,
        "conclusion",
        "The existing plan already covers this constraint. No priority changed.",
    )
    report(
        root,
        "research",
        "worker",
        rid,
        "conclusion",
        "The existing plan already covers this constraint. No priority changed.",
    )
    with pytest.raises(ValueError, match="conflicting"):
        report(root, "research", "worker", rid, "conclusion", "Different text")
    drain(root, registry, store, lambda *_: None)
    assert (
        len(
            [
                x
                for x in store.messages(session["session_id"])
                if x.get("origin") == "manager_followup"
            ]
        )
        == 1
    )
    assert {x["status"] for x in reply_status(root, r)} == {"superseded", "delivered"}


@pytest.mark.parametrize("external", [False, True])
def test_later_completion_returns_to_original_conversation_once(flow, external):
    root, registry, store, create = flow
    session, turn, receipt = create(external)
    rid = receipt["request_id"]
    acknowledge(root, "research", "worker", rid, "adopt", "Work accepted")
    report(root, "research", "worker", rid, "conclusion", "Draft ready; awaiting review.")
    sent = []

    def sender(route, source_session, source_turn, text):
        sent.append((route["session_id"], source_turn["turn_id"], text))
        return {"ok": True, "reply_verified": True}

    drain(root, registry, store, sender)
    before = (root / ".local/manager-context/replies" / rid / "conclusion.json").read_bytes()
    update = report(root, "research", "worker", rid, "conclusion", "Review passed; delivery complete.", update_id="review-complete")
    assert update["result_key"] != "conclusion"
    assert report(root, "research", "worker", rid, "conclusion", "Review passed; delivery complete.", update_id="review-complete")["result_key"] == update["result_key"]
    with pytest.raises(ValueError, match="conflicting"):
        report(root, "research", "worker", rid, "conclusion", "Another result", update_id="review-complete")
    report(root, "research", "worker", rid, "conclusion", "Receiver adopted the delivery.", update_id="receiver-adopted")
    store = ChatSessionStore(root)  # The ordinary production reader after restart.
    drain(root, registry, store, sender)
    drain(root, registry, store, sender)
    messages = [m for m in store.messages(session["session_id"]) if m.get("origin") == "manager_followup"]
    assert len(messages) == 3
    assert messages[1]["text"].endswith("Review passed; delivery complete.")
    assert messages[-1]["text"].endswith("Receiver adopted the delivery.")
    assert len({m["message_id"] for m in messages}) == 3
    assert {m["turn_id"] for m in messages} == {turn["turn_id"]}
    assert (root / ".local/manager-context/replies" / rid / "conclusion.json").read_bytes() == before
    assert [r["status"] for r in reply_status(root, receipt)] == ["delivered"] * 3
    if external:
        assert len(sent) == 3
        assert len({m[-1] for m in sent}) == 3


def test_result_update_does_not_overtake_unknown_external_delivery(flow):
    root, registry, store, create = flow
    session, _, receipt = create(True)
    rid = receipt["request_id"]
    acknowledge(root, "research", "worker", rid, "adopt", "Accepted")
    report(root, "research", "worker", rid, "conclusion", "Waiting for review.")
    report(root, "research", "worker", rid, "conclusion", "Review complete.", update_id="reviewed")
    calls = []

    def unknown(route, source_session, source_turn, text):
        calls.append(text)
        return {"ok": False, "external_write_performed": True}  # No readback locator.

    drain(root, registry, store, unknown)
    drain(root, registry, ChatSessionStore(root), unknown)
    assert len(calls) == 1
    assert [r["status"] for r in reply_status(root, receipt)] == ["explicit_unverified", "queued"]
    messages = [m for m in store.messages(session["session_id"]) if m.get("origin") == "manager_followup"]
    assert len(messages) == 1 and messages[0]["text"].endswith("Waiting for review.")


def test_external_failure_retries_same_reply_after_restart_without_duplicate_transcript(
    flow,
):
    root, registry, store, create = flow
    session, turn, r = create(True)
    rid = r["request_id"]
    acknowledge(root, "research", "worker", rid, "adopt", "Private rationale")
    report(
        root,
        "research",
        "worker",
        rid,
        "conclusion",
        "Validation completed; the revised plan preserves the existing priorities.",
    )
    calls = []

    def offline(route, s, t, text):
        calls.append((route, s, t, text))
        return {"ok": False, "external_write_performed": False}

    now = datetime.now(timezone.utc)
    drain(root, registry, store, offline, now=now)
    assert reply_status(root, r)[0]["status"] == "retry_pending"
    drain(root, registry, store, offline, now=now)
    assert len(calls) == 1

    def online(*args):
        assert args == calls[0]
        return {"ok": True, "reply_verified": True}

    drain(
        root, registry, ChatSessionStore(root), online, now=now + timedelta(minutes=10)
    )
    assert reply_status(root, r)[0]["status"] == "delivered"
    assert (
        len(
            [
                x
                for x in store.messages(session["session_id"])
                if x.get("origin") == "manager_followup"
            ]
        )
        == 1
    )


def test_revocation_and_closed_conversation_never_retarget(flow):
    root, registry, store, create = flow
    session, _, r = create(True)
    rid = r["request_id"]
    acknowledge(root, "research", "worker", rid, "reject", "Unsupported")
    report(
        root,
        "research",
        "worker",
        rid,
        "conclusion",
        "The request was assessed and declined because the required evidence is absent.",
    )
    _write(
        _root(root) / "policy.json", {"schema_version": POLICY_SCHEMA, "sources": {}}
    )

    def unexpected(*_):
        raise AssertionError("revoked request must not be sent")

    drain(root, registry, store, unexpected)
    assert not [
        x
        for x in store.messages(session["session_id"])
        if x.get("origin") == "manager_followup"
    ]
    store.update_session(session["session_id"], status="closed")
    drain(
        root,
        registry,
        store,
        unexpected,
        now=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    assert reply_status(root, r)[0]["status"] == "retry_pending"


def test_legacy_route_is_recovered_only_from_exact_persisted_chat_receipt(flow):
    root, registry, store, create = flow
    session, _, r = create()
    rid = r["request_id"]
    (_root(root) / "roundtrips" / (rid + ".json")).unlink()
    acknowledge(root, "research", "worker", rid, "defer", "Dependent data unavailable")
    report(
        root,
        "research",
        "worker",
        rid,
        "conclusion",
        "Deferred until the named dependency is available; no work result is claimed.",
    )
    drain(root, registry, store, lambda *_: None)
    assert (
        len(
            [
                x
                for x in store.messages(session["session_id"])
                if x.get("origin") == "manager_followup"
            ]
        )
        == 1
    )


def test_ambiguous_provider_write_is_not_blindly_resent(flow):
    root, registry, store, create = flow
    _, _, r = create(True)
    rid = r["request_id"]
    acknowledge(root, "research", "worker", rid, "adopt", "Checked")
    report(
        root,
        "research",
        "worker",
        rid,
        "conclusion",
        "Processed with a recorded validation result.",
    )
    calls = []

    def ambiguous(*args):
        calls.append(args)
        return {"ok": False, "external_write_performed": True, "reply_verified": False}

    drain(root, registry, store, ambiguous)
    drain(
        root,
        registry,
        store,
        ambiguous,
        now=datetime.now(timezone.utc) + timedelta(days=2),
    )
    assert len(calls) == 1
    assert reply_status(root, r)[0] == {
        "phase": "conclusion",
        "status": "explicit_unverified",
        "created_at": reply_status(root, r)[0]["created_at"],
        "delivered_at": None,
        "error": "provider_locator_unavailable",
    }


def test_known_provider_locator_is_verified_after_restart_without_resend(flow):
    root, registry, store, create = flow
    session, _, receipt = create(True)
    rid = receipt["request_id"]
    acknowledge(root, "research", "worker", rid, "adopt", "Checked")
    report(
        root,
        "research",
        "worker",
        rid,
        "conclusion",
        "Processed with a recorded validation result.",
    )

    class Transport:
        def __init__(self):
            self.send_calls = 0
            self.verify_calls = 0

        def send_with_attempt(self, route, session, turn, text, record_attempt):
            self.send_calls += 1
            record_attempt(
                {
                    "schema_version": "manager_return_delivery_attempt_v0",
                    "provider": "lark",
                    "message_ref": "om_provider_reply",
                    "intent_digest": "sha256:" + "a" * 64,
                    "provider_receipt": "sha256:" + "b" * 64,
                }
            )
            raise RuntimeError("synthetic process interruption after provider send")

        def verify(self, route, session, turn, text, attempt):
            self.verify_calls += 1
            assert attempt["message_ref"] == "om_provider_reply"
            return {
                "ok": True,
                "verification_performed": True,
                "reply_verified": True,
            }

    transport = Transport()
    drain(root, registry, store, transport)
    first = reply_status(root, receipt)[0]
    assert first["status"] == "verification_required"
    assert "message_ref" not in first and "intent_digest" not in first
    first_projection = project_chat_return_deliveries(
        root, session["session_id"], store.messages(session["session_id"])
    )
    assert next(
        row for row in first_projection if row.get("origin") == "manager_followup"
    )["return_delivery"]["status"] == "verification_required"
    assert "om_provider_reply" not in json.dumps(first_projection)

    drain(root, registry, ChatSessionStore(root), transport)
    recovered = reply_status(root, receipt)[0]
    assert recovered["status"] == "delivered"
    assert recovered["verification"] == "reconciled_after_restart"
    assert transport.send_calls == 1
    assert transport.verify_calls == 1

    projected = project_chat_return_deliveries(
        root, session["session_id"], store.messages(session["session_id"])
    )
    returned = [row for row in projected if row.get("origin") == "manager_followup"]
    assert returned[0]["return_delivery"] == {
        "schema_version": "manager_return_delivery_status_v0",
        **recovered,
    }


def test_chat_snapshot_keeps_current_session_delivery_after_unrelated_route_limit(flow):
    root, registry, store, _ = flow
    session = store.create_session(
        goal_id="loopx-manager",
        agent_id="codex",
        adapter_kind="codex_app_server",
        upstream_thread_id="projection-limit",
        channel_id="manager",
    )
    turn, _ = store.create_turn(
        session["session_id"],
        client_turn_id="projection-limit",
        message="Check delivery projection completeness",
        origin="web",
    )

    expected = {}
    for request_id, status in (
        ("e" * 64, "delivered"),
        ("f" * 64, "verification_required"),
    ):
        _write(
            _root(root) / "roundtrips" / f"{request_id}.json",
            {"request_id": request_id, "session_id": session["session_id"]},
        )
        _write(
            _root(root) / "replies" / request_id / "conclusion.json",
            {
                "request_id": request_id,
                "phase": "conclusion",
                "created_at": "2026-09-14T00:00:00+00:00",
            },
        )
        delivery = {"status": status}
        if status == "verification_required":
            delivery["error"] = "provider_delivery_unverified"
        _write(
            _root(root) / "replies" / request_id / "conclusion.delivery.json",
            delivery,
        )
        message_id = "handoff." + _hash([request_id, "conclusion"])
        store.append_message(
            session["session_id"],
            role="agent",
            text=f"Projected {status}",
            turn_id=turn["turn_id"],
            origin="manager_followup",
            message_id=message_id,
        )
        expected[message_id] = status

    for index in range(2000):
        request_id = f"{index:064x}"
        _write(
            _root(root) / "roundtrips" / f"{request_id}.json",
            {"request_id": request_id, "session_id": f"unrelated-{index}"},
        )

    snapshot = project_chat_session_snapshot(root, store, session["session_id"], registry=registry)
    returned = {
        row["message_id"]: row["return_delivery"]["status"]
        for row in snapshot["messages"]
        if row.get("message_id") in expected
    }
    assert returned == expected


def test_provider_verification_outage_retries_read_only_without_resend(flow):
    root, registry, store, create = flow
    _, _, receipt = create(True)
    rid = receipt["request_id"]
    acknowledge(root, "research", "worker", rid, "adopt", "Checked")
    report(root, "research", "worker", rid, "conclusion", "Bounded result.")

    class Transport:
        send_calls = 0
        verify_calls = 0

        def send_with_attempt(self, route, session, turn, text, record_attempt):
            self.send_calls += 1
            record_attempt(
                {
                    "schema_version": "manager_return_delivery_attempt_v0",
                    "provider": "lark",
                    "message_ref": "om_provider_reply",
                    "intent_digest": "sha256:" + "a" * 64,
                    "provider_receipt": "sha256:" + "b" * 64,
                }
            )
            return {"external_write_performed": True, "reply_verified": False}

        def verify(self, *_args):
            self.verify_calls += 1
            return {
                "ok": False,
                "verification_performed": False,
                "reply_verified": False,
                "blocker": "provider_verification_unavailable",
            }

    transport = Transport()
    now = datetime.now(timezone.utc)
    drain(root, registry, store, transport, now=now)
    drain(root, registry, ChatSessionStore(root), transport, now=now)
    state = reply_status(root, receipt)[0]
    assert state["status"] == "verification_required"
    assert state["error"] == "provider_verification_unavailable"
    assert transport.send_calls == 1 and transport.verify_calls == 1


def test_provider_verification_mismatch_is_terminal_and_public_safe(flow):
    root, registry, store, create = flow
    _, _, receipt = create(True)
    rid = receipt["request_id"]
    acknowledge(root, "research", "worker", rid, "adopt", "Checked")
    report(root, "research", "worker", rid, "conclusion", "Bounded result.")

    class Transport:
        def send_with_attempt(self, route, session, turn, text, record_attempt):
            record_attempt(
                {
                    "schema_version": "manager_return_delivery_attempt_v0",
                    "provider": "lark",
                    "message_ref": "om_private_provider_reply",
                    "intent_digest": "sha256:" + "a" * 64,
                    "provider_receipt": "sha256:" + "b" * 64,
                }
            )
            return {"external_write_performed": True, "reply_verified": False}

        def verify(self, *_args):
            return {
                "ok": False,
                "verification_performed": True,
                "reply_verified": False,
                "blocker": "private provider mismatch detail",
            }

    transport = Transport()
    drain(root, registry, store, transport)
    drain(root, registry, ChatSessionStore(root), transport)
    state = reply_status(root, receipt)[0]
    assert state["status"] == "explicit_unverified"
    assert state["error"] == "provider_delivery_mismatch"
    assert "private" not in str(state)


def test_provider_verification_stops_after_return_authority_revocation(flow):
    root, registry, store, create = flow
    _, _, receipt = create(True)
    rid = receipt["request_id"]
    acknowledge(root, "research", "worker", rid, "adopt", "Checked")
    report(root, "research", "worker", rid, "conclusion", "Bounded result.")

    class Transport:
        verify_calls = 0

        def send_with_attempt(self, route, session, turn, text, record_attempt):
            record_attempt(
                {
                    "schema_version": "manager_return_delivery_attempt_v0",
                    "provider": "lark",
                    "message_ref": "om_provider_reply",
                    "intent_digest": "sha256:" + "a" * 64,
                    "provider_receipt": "sha256:" + "b" * 64,
                }
            )
            return {"external_write_performed": True, "reply_verified": False}

        def verify(self, *_args):
            self.verify_calls += 1
            return {"verification_performed": True, "reply_verified": True}

    transport = Transport()
    drain(root, registry, store, transport)
    _write(
        _root(root) / "policy.json", {"schema_version": POLICY_SCHEMA, "sources": {}}
    )
    drain(root, registry, ChatSessionStore(root), transport)

    state = reply_status(root, receipt)[0]
    assert state["status"] == "explicit_unverified"
    assert state["error"] == "return_authorization_unavailable"
    assert transport.verify_calls == 0


def test_legacy_verification_required_without_locator_is_never_resent(flow):
    """A record from before locators were persisted must not be guessed or resent."""
    root, registry, store, create = flow
    _, _, receipt = create(True)
    rid = receipt["request_id"]
    acknowledge(root, "research", "worker", rid, "adopt", "Checked")
    report(root, "research", "worker", rid, "conclusion", "Legacy ambiguous result.")
    _write(
        _root(root) / "replies" / rid / "conclusion.delivery.json",
        {"status": "verification_required", "error": "provider_delivery_unverified"},
    )

    class Transport:
        send_calls = 0

        def send_with_attempt(self, route, session, turn, text, record_attempt):
            self.send_calls += 1
            return {"reply_verified": True}

        def verify(self, *_args):
            raise AssertionError("a locatorless return has nothing to verify")

    transport = Transport()
    drain(root, registry, store, transport)
    drain(root, registry, ChatSessionStore(root), transport)
    state = reply_status(root, receipt)[0]
    assert state["status"] == "explicit_unverified"
    assert state["error"] == "provider_locator_unavailable"
    # Terminal: no later pump may resend the already published conclusion.
    drain(
        root,
        registry,
        ChatSessionStore(root),
        transport,
        now=datetime.now(timezone.utc) + timedelta(days=2),
    )
    assert transport.send_calls == 0


def accepted_attempt():
    return {
        "schema_version": "manager_return_delivery_attempt_v0",
        "provider": "lark",
        "message_ref": "om_provider_reply",
        "intent_digest": "sha256:" + "a" * 64,
        "provider_receipt": "sha256:" + "b" * 64,
    }


@pytest.mark.parametrize(
    "reason",
    [
        "return_authorization_unavailable",
        "original_route_unavailable",
        "initial_delivery_receipt_unavailable",
    ],
)
def test_typed_return_resolution_terminalizes_without_repeating_readback(
    flow, reason
):
    root, registry, store, create = flow
    _, _, receipt = create(True)
    rid = receipt["request_id"]
    acknowledge(root, "research", "worker", rid, "adopt", "Checked")
    report(root, "research", "worker", rid, "conclusion", "Bounded result.")

    class Transport:
        verify_calls = 0

        def send_with_attempt(self, route, session, turn, text, record_attempt):
            record_attempt(accepted_attempt())
            return {"external_write_performed": True, "reply_verified": False}

        def verify(self, *_args):
            self.verify_calls += 1
            raise ReturnResolutionBlocked(reason, "Adapter resolution blocked")

    transport = Transport()
    drain(root, registry, store, transport)
    drain(root, registry, ChatSessionStore(root), transport)
    state = reply_status(root, receipt)[0]
    assert state["status"] == "explicit_unverified"
    assert state["error"] == reason
    assert transport.verify_calls == 1
    # A terminal reason is never re-read, not even after the backoff window.
    drain(
        root,
        registry,
        ChatSessionStore(root),
        transport,
        now=datetime.now(timezone.utc) + timedelta(days=2),
    )
    assert transport.verify_calls == 1


@pytest.mark.parametrize("message", [
    "provider readback transport failed",
    "route lookup temporarily unavailable",
    "authorization service read timed out",
    "initial reply read interrupted",
])
def test_unclassified_verification_failure_recovers_without_resending(flow, message):
    root, registry, store, create = flow
    session, _, receipt = create(True)
    rid = receipt["request_id"]
    acknowledge(root, "research", "worker", rid, "adopt", "Checked")
    report(root, "research", "worker", rid, "conclusion", "Bounded result.")

    class Transport:
        verify_calls = 0
        send_calls = 0

        def send_with_attempt(self, route, session, turn, text, record_attempt):
            self.send_calls += 1
            record_attempt(accepted_attempt())
            # A recorded provider write resumes readback regardless of wording.
            raise RuntimeError(message)

        def verify(self, *_args):
            self.verify_calls += 1
            if self.verify_calls == 1:
                raise RuntimeError(message)
            return {"verification_performed": True, "reply_verified": True}

    transport = Transport()
    drain(root, registry, store, transport)
    drain(root, registry, ChatSessionStore(root), transport)
    assert reply_status(root, receipt)[0]["status"] == "verification_required"
    assert transport.verify_calls == 1
    # The kept locator stays retryable, but the next pump must respect the
    # backoff instead of re-running the provider readback every few seconds.
    drain(root, registry, ChatSessionStore(root), transport)
    assert transport.verify_calls == 1
    drain(
        root,
        registry,
        ChatSessionStore(root),
        transport,
        now=datetime.now(timezone.utc) + timedelta(days=2),
    )
    assert transport.verify_calls == 2
    assert transport.send_calls == 1
    assert reply_status(root, receipt)[0]["status"] == "delivered"
    snapshot = project_chat_session_snapshot(root, ChatSessionStore(root), session["session_id"], registry=registry)
    replies = [m for m in snapshot["messages"] if m.get("origin") == "manager_followup"]
    assert len(replies) == 1
    assert replies[0]["return_delivery"]["status"] == "delivered"
    assert message not in json.dumps(snapshot)


def test_public_delivery_projection_normalizes_unknown_private_state(flow):
    root, _, _, create = flow
    _, _, receipt = create(True)
    rid = receipt["request_id"]
    acknowledge(root, "research", "worker", rid, "adopt", "Checked")
    report(root, "research", "worker", rid, "conclusion", "Bounded result.")
    _write(
        _root(root) / "replies" / rid / "conclusion.delivery.json",
        {"status": "verification_required", "error": "private provider detail"},
    )

    state = reply_status(root, receipt)[0]
    assert state["status"] == "explicit_unverified"
    assert state["error"] == "delivery_state_unreadable"
    assert "private" not in str(state)


def test_background_service_delivers_without_another_agent_or_query(flow, monkeypatch):
    observations = []
    monkeypatch.setattr('loopx.usage_ping.observe_verified_return', lambda: observations.append('verified'))
    root, registry, store, create = flow
    _, _, receipt = create(True)
    rid = receipt["request_id"]
    acknowledge(root, "research", "worker", rid, "no_change", "Reviewed")
    report(
        root,
        "research",
        "worker",
        rid,
        "conclusion",
        "No change is needed after checking the existing plan.",
    )
    sent = threading.Event()

    def transport(*_):
        sent.set()
        return {"reply_verified": True, "idempotency_key": "sha256:provider-proof"}

    service = ReturnService(root, registry, store, transport)
    service.start()
    try:
        assert sent.wait(timeout=5)
    finally:
        service.close()
    state = json.loads(
        (_root(root) / "replies" / rid / "conclusion.delivery.json").read_text()
    )
    assert state["status"] == "delivered"
    assert state["provider_receipt"] == "sha256:provider-proof"
    assert not service.thread.is_alive()
    drain(root, registry, store, transport)
    assert observations == []  # Legacy delivery is outside the exact-source telemetry contract.


@pytest.mark.parametrize("project", [False, True], ids=["steward", "project"])
def test_registration_revocation_blocks_return_without_retargeting(flow, project):
    root, registry, store, create = flow
    session, _, receipt = create(project=project)
    rid = receipt["request_id"]
    acknowledge(root, "research", "worker", rid, "adopt", "Assess correction")
    report(root, "research", "worker", rid, "conclusion", "Independent finding is ready.")
    payload = json.loads(registry.read_text())
    payload["goals"][0]["coordination"]["registered_agents"] = []
    registry.write_text(json.dumps(payload))

    def forbidden(*_):
        pytest.fail("a revoked registration must not cause an external send")

    drain(root, registry, store, forbidden)
    assert not [m for m in store.messages(session["session_id"])
                if m.get("origin") == "manager_followup"]
    assert reply_status(root, receipt)[0]["status"] == "retry_pending"


def test_provider_write_without_a_locator_is_terminal_and_not_repeated(flow):
    """A write the provider took without a message id is never sent again.

    The canonical attempt contract holds "accepted, no locator" as a typed
    state. The pump has to converge there: a return whose only record names no
    readback target cannot stay retryable, because the retry would post text the
    reader may already have.
    """

    root, registry, store, create = flow
    session, _, receipt = create(True)
    rid = receipt["request_id"]
    acknowledge(root, "research", "worker", rid, "adopt", "Checked")
    report(
        root,
        "research",
        "worker",
        rid,
        "conclusion",
        "Processed with a recorded validation result.",
    )

    class Transport:
        def __init__(self):
            self.send_calls = 0
            self.verify_calls = 0

        def send_with_attempt(self, route, session, turn, text, record_attempt):
            self.send_calls += 1
            record_attempt(
                {
                    "schema_version": "manager_return_delivery_attempt_v0",
                    "provider": "lark",
                    "message_ref": None,
                    "intent_digest": "sha256:" + "a" * 64,
                    "provider_receipt": "sha256:" + "b" * 64,
                }
            )
            raise RuntimeError("synthetic process interruption after provider send")

        def verify(self, route, session, turn, text, attempt):
            self.verify_calls += 1
            raise AssertionError("a write with no locator has nothing to verify")

    transport = Transport()
    drain(root, registry, store, transport)
    first = reply_status(root, receipt)[0]

    # The write is recorded and the return settles as unverifiable in the same
    # pass: nothing can read it back, so it must not stay retryable.
    assert first["status"] == "explicit_unverified"
    assert first["error"] == "provider_locator_unavailable"
    assert transport.send_calls == 1

    state_path = next(
        (root / ".local" / "manager-context" / "replies").glob(
            f"{receipt['request_id']}/conclusion.delivery.json"
        )
    )
    state = json.loads(state_path.read_text())
    assert state["attempt"]["message_ref"] is None
    assert state["attempt"]["intent_digest"] == "sha256:" + "a" * 64

    drain(root, registry, ChatSessionStore(root), transport)
    again = reply_status(root, receipt)[0]
    assert again["status"] == "explicit_unverified"
    assert transport.send_calls == 1
    assert transport.verify_calls == 0


@pytest.mark.parametrize("project", [False, True], ids=["steward", "goal-chat"])
@pytest.mark.parametrize("decision", ["defer", "reject", "adopt", "no_change"])
def test_reply_delivery_does_not_replace_receiver_disposition(flow, project, decision):
    root, registry, store, create = flow
    brief = dict(
        schema_version="collaboration_brief_v0", purpose="Draft a community survey",
        context="Prepare a reviewable draft before publication.",
        constraints=["Do not publish"], inputs=[],
        acceptance=["A readable survey with concrete questions"],
        return_requirement="Return the draft or explain why it is deferred",
    )
    session, turn, receipt = create(project=project, brief=brief)
    rid = receipt["request_id"]
    reason = "Recipient assessment; private evidence at /Users/example/private/notes.md"
    acknowledge(root, "research", "worker", rid, decision, reason)
    report(root, "research", "worker", rid, "conclusion", "The assessment is available.")
    drain(root, registry, store, lambda *_: pytest.fail("private return sent externally"))
    snapshot = project_chat_session_snapshot(root, ChatSessionStore(root), session["session_id"], registry=registry)
    collaboration = next(row["collaboration"] for row in snapshot["messages"] if row.get("collaboration"))
    assert collaboration["decision"] == decision
    assert collaboration["goal_id"] == "research"
    assert "Recipient assessment" in collaboration["decision_reason"]
    assert "/Users/example" not in collaboration["decision_reason"]
    assert collaboration["returns"][0]["status"] == "delivered"
    reply = next(row for row in snapshot["messages"] if row.get("origin") == "manager_followup")
    assert reply["text"].startswith("协作回复 · worker")
    assert "处理结论" not in reply["text"]
    assert "Recipient assessment" not in reply["text"]
    assert len(list((store.root / "sessions" / session["session_id"] / "turns").glob("*.json"))) == 1


def test_external_reply_never_exposes_private_receiver_reason(flow):
    root, registry, store, create = flow
    session, _, receipt = create(external=True)
    acknowledge(root, "research", "worker", receipt["request_id"], "defer", "Private receiver rationale")
    report(root, "research", "worker", receipt["request_id"], "conclusion", "This request is deferred.")
    sent = []
    def sender(_route, _session, _turn, payload):
        sent.append(payload)
        return {"ok": True, "reply_verified": True, "verification_performed": True}
    drain(root, registry, store, sender)
    snapshot = project_chat_session_snapshot(root, store, session["session_id"], registry=registry)
    assert "Private receiver rationale" not in json.dumps(snapshot)
    assert len(sent) == 1
    assert sent[0].startswith("协作回复 · worker")
    assert "Private receiver rationale" not in json.dumps(sent)
    assert all(not row.get("collaboration") for row in snapshot["messages"])


def test_unresolvable_return_transport_records_retry_without_fake_receipt(flow):
    root, registry, store, create = flow
    session, _, receipt = create(True)
    rid = receipt["request_id"]
    acknowledge(root, "research", "worker", rid, "adopt", "Checked")
    report(
        root,
        "research",
        "worker",
        rid,
        "conclusion",
        "Checked with the current evidence and found no gap.",
    )

    class InertTransport:
        """Neither attempt-aware nor callable: cannot deliver anything."""

    drain(root, registry, store, InertTransport())
    state = json.loads(
        (_root(root) / "replies" / rid / "conclusion.delivery.json").read_text()
    )
    assert state["status"] == "retry_pending"
    assert state["error"] == "return_transport_unavailable"
    assert "delivered_at" not in state and "message_id" not in state
    projected = reply_status(root, receipt)[0]
    assert projected["status"] == "retry_pending"
    assert projected["error"] == "return_transport_unavailable"


def test_attempt_only_transport_still_delivers_without_call_protocol(flow):
    root, registry, store, create = flow
    session, _, receipt = create(True)
    rid = receipt["request_id"]
    acknowledge(root, "research", "worker", rid, "adopt", "Checked")
    report(
        root,
        "research",
        "worker",
        rid,
        "conclusion",
        "Recorded the validation result for the existing plan.",
    )

    class Transport:
        """Attempt-aware only: the instance itself is not callable."""

        def __init__(self):
            self.calls = 0

        def send_with_attempt(self, route, session, turn, text, record_attempt):
            self.calls += 1
            return {"reply_verified": True, "idempotency_key": "sha256:provider-proof"}

    transport = Transport()
    drain(root, registry, store, transport)
    assert transport.calls == 1
    state = json.loads(
        (_root(root) / "replies" / rid / "conclusion.delivery.json").read_text()
    )
    assert state["status"] == "delivered"
    assert state["provider_receipt"] == "sha256:provider-proof"
    assert reply_status(root, receipt)[0]["status"] == "delivered"


def test_private_channel_completes_transcript_only_without_sender(flow):
    root, registry, store, create = flow
    session, turn, receipt = create()
    rid = receipt["request_id"]
    acknowledge(root, "research", "worker", rid, "adopt", "Private deliberation")
    report(
        root,
        "research",
        "worker",
        rid,
        "conclusion",
        "Completed the private check against the existing plan.",
    )

    def unexpected(*_):
        raise AssertionError("private channel must deliver without any transport")

    drain(root, registry, store, unexpected)
    state = json.loads(
        (_root(root) / "replies" / rid / "conclusion.delivery.json").read_text()
    )
    assert state["status"] == "delivered"
    returned = [
        row
        for row in store.messages(session["session_id"])
        if row.get("origin") == "manager_followup"
    ]
    assert len(returned) == 1 and returned[0]["turn_id"] == turn["turn_id"]
    assert reply_status(root, receipt)[0]["status"] == "delivered"


def test_payload_conflict_records_terminal_state_without_retry(flow):
    root, registry, store, create = flow
    session, turn, receipt = create(True)
    rid = receipt["request_id"]
    acknowledge(root, "research", "worker", rid, "adopt", "Checked")
    report(
        root,
        "research",
        "worker",
        rid,
        "conclusion",
        "Checked with the current evidence and found no gap.",
    )
    # Pre-seed the transcript with the same handoff message_id carrying a
    # different payload. A payload conflict is a data fault, not a transport
    # fault: drain must stop in the existing explicit_unverified status with
    # the typed error code — no new status word, so every existing reader
    # keeps its semantics — instead of folding the conflict into the
    # transport retry path.
    conflicting_mid = "handoff." + _hash([rid, "conclusion"])
    store.append_message(
        session["session_id"],
        role="agent",
        text="An earlier, different version of this conclusion.",
        turn_id=turn["turn_id"],
        origin="manager_followup",
        message_id=conflicting_mid,
    )
    deliveries = []

    def recording_transport(route, session, turn, text):
        deliveries.append(text)
        return {"reply_verified": True, "idempotency_key": "sha256:provider-proof"}

    drain(root, registry, store, recording_transport)
    state_path = _root(root) / "replies" / rid / "conclusion.delivery.json"
    state = json.loads(state_path.read_text())
    assert state["status"] == "explicit_unverified"
    assert state["error"] == "manager_return_payload_conflict"
    assert "delivered_at" not in state and "message_id" not in state
    assert "attempts" not in state and "retry_at" not in state
    assert deliveries == []
    # The recorded state is terminal: a second pass neither retries the
    # transport nor rewrites the record.
    drain(root, registry, store, recording_transport)
    assert json.loads(state_path.read_text()) == state
    assert deliveries == []
    projected = reply_status(root, receipt)[0]
    assert projected["status"] == "explicit_unverified"
    assert projected["error"] == "manager_return_payload_conflict"


def test_stored_empty_attachments_list_replays_without_delivery_conflict(flow):
    root, registry, store, create = flow
    session, turn, receipt = create()
    rid = receipt["request_id"]
    acknowledge(root, "research", "worker", rid, "adopt", "Private deliberation")
    report(
        root,
        "research",
        "worker",
        rid,
        "conclusion",
        "Completed the private check against the existing plan.",
    )

    def unexpected(*_):
        raise AssertionError("private channel must deliver without any transport")

    # First pass writes the transcript row (no ``attachments`` key) and the
    # delivered receipt.
    drain(root, registry, store, unexpected)
    messages_path = (
        store.root / "sessions" / session["session_id"] / "messages.jsonl"
    )
    rows = [
        json.loads(line)
        for line in messages_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    handoff_rows = [row for row in rows if row.get("message_id", "").startswith("handoff.")]
    assert len(handoff_rows) == 1 and "attachments" not in handoff_rows[0]
    # An externally written or migrated row can carry a literal empty
    # ``attachments`` list where the delivery payload has no key at all:
    # field normalization folds both onto the canonical empty list, so the
    # same-id replay still delivers instead of reporting a phantom payload
    # conflict (a bare ``.get()`` comparison would raise one here).
    for row in rows:
        if row.get("message_id", "").startswith("handoff."):
            row["attachments"] = []
    messages_path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    state_path = _root(root) / "replies" / rid / "conclusion.delivery.json"
    state_path.unlink()
    drain(root, registry, store, unexpected)
    state = json.loads(state_path.read_text())
    assert state["status"] == "delivered"
    stored = [
        row
        for row in store.messages(session["session_id"])
        if row.get("message_id", "").startswith("handoff.")
    ]
    assert len(stored) == 1 and stored[0]["attachments"] == []


def test_exact_loop_payload_conflict_sets_terminal_state_without_retry(
    flow,
    monkeypatch,
):
    root, registry, store, create = flow
    session, turn, receipt = create(True)
    rid = receipt["request_id"]
    acknowledge(root, "research", "worker", rid, "adopt", "Checked")
    report(
        root,
        "research",
        "worker",
        rid,
        "conclusion",
        "Checked with the current evidence and found no gap.",
    )
    conflicting_mid = "handoff." + _hash([rid, "conclusion"])
    store.append_message(
        session["session_id"],
        role="agent",
        text="An earlier, different version of this conclusion.",
        turn_id=turn["turn_id"],
        origin="manager_followup",
        message_id=conflicting_mid,
    )
    # The exact admission machinery needs a source-session registry fixture
    # this module does not build; stub the context construction and the
    # settlement writer so the exact loop's own delivery-side conflict
    # check is what runs against the real divergent row above.
    import loopx.capabilities.manager_context.roundtrip as return_roundtrip
    from loopx.control_plane.projects.registry_codec import (
        SOURCE_SESSION_PROFILE_ID,
    )

    settlements = []

    def fake_settlement(root, registry, context, value, **kwargs):
        settlements.append(dict(value))

    monkeypatch.setattr(
        return_roundtrip, "_write_exact_return_state", fake_settlement
    )

    def fake_context(root, registry, store, path, state_path, now):
        reply = return_roundtrip._read(path)
        return {
            "reply": {**reply, "phase": path.stem},
            "row": {"request_id": rid, "agent_id": "worker"},
            "route": {"session_id": session["session_id"]},
            "session": session,
            "turn": turn,
            "store": store,
            "state_path": state_path,
            "state": {},
            "token": "fixture",
            "source_id": "lark:om_fixture_source",
        }

    monkeypatch.setattr(return_roundtrip, "_exact_return_context", fake_context)
    profile = json.loads(registry.read_text())
    profile["profile_id"] = SOURCE_SESSION_PROFILE_ID
    registry.write_text(json.dumps(profile))
    deliveries = []

    def recording_transport(route, session, turn, text):
        deliveries.append(text)
        return {"reply_verified": True, "idempotency_key": "sha256:provider-proof"}

    drain(root, registry, store, recording_transport)
    assert deliveries == []
    assert len(settlements) == 1
    assert settlements[0] == {
        "status": "explicit_unverified",
        "error": "manager_return_payload_conflict",
    }


def test_explicit_channel_return_completes_transcript_only_without_sender(flow):
    root, registry, store, create = flow
    session, turn, receipt = create()
    assert session["channel_id"] == "manager"
    rid = receipt["request_id"]
    acknowledge(root, "research", "worker", rid, "adopt", "Checked")
    report(
        root,
        "research",
        "worker",
        rid,
        "conclusion",
        "Completed the check against the existing plan.",
    )

    def unexpected(*_):
        raise AssertionError(
            "explicit private channel must complete without any transport"
        )

    drain(root, registry, store, unexpected)
    state = json.loads(
        (_root(root) / "replies" / rid / "conclusion.delivery.json").read_text()
    )
    assert state["status"] == "delivered"
    returned = [
        row
        for row in store.messages(session["session_id"])
        if row.get("origin") == "manager_followup"
    ]
    assert len(returned) == 1 and returned[0]["turn_id"] == turn["turn_id"]
    assert reply_status(root, receipt)[0]["status"] == "delivered"
