"""Source-backed peer context across separate Chat and coordination stores."""

import json
import os
from contextlib import contextmanager
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from types import SimpleNamespace

import pytest

from canonical_authority_fixture import isolate_sqlite_runtime, promoted_create_fixture
from loopx.capabilities.manager_context import POLICY_SCHEMA, _hash, _root, _write, register_ingress
from loopx.chat_coordination import apply_context_handoff
from loopx.chat_store import ChatSessionStore
from loopx.control_plane.collaboration.peers import read_inbox, request
from loopx.capabilities.manager_context.roundtrip import (
    ReturnService, drain, project_chat_session_snapshot, recover_source_chat_provenance,
)


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    isolate_sqlite_runtime(tmp_path, monkeypatch)


def source_request(tmp_path, monkeypatch, *, provider="file", colocated=False):
    if provider == "source_session":
        from loopx.control_plane.projects.registry_codec import source_session_registry_transaction

        registry, root = tmp_path / "registry.json", tmp_path / "runtime"
        with source_session_registry_transaction(registry, operation="create_fixture", create=lambda: {
            "profile_id": "source_session_v1",
            "goals": [{"id": "goal-a", "repo": str(tmp_path), "status": "active",
                       "goal_instance_id": "ginst_" + "a" * 32,
                       "coordination": {"registered_agents": ["agent-a", "reviewer", "third"]}}],
            "session_bindings": [], "session_receipts": [], "lifetime_receipts": [],
        }) as transaction:
            transaction.commit(transaction.payload_copy())
    else:
        registry, root, _ = promoted_create_fixture(tmp_path, provider=provider)
    if provider != "source_session":
        data = json.loads(registry.read_text())
        data["goals"][0]["coordination"]["registered_agents"] += ["reviewer", "third"]
        registry.write_text(json.dumps(data))
    store = ChatSessionStore(root if colocated else tmp_path / "chat-host")
    session = store.create_session(
        goal_id="loopx-manager", agent_id="codex", adapter_kind="codex_app_server",
        upstream_thread_id="synthetic-host", channel_id="manager.external." + "a" * 24,
    )
    turn, _ = store.create_turn(session["session_id"], client_turn_id="source-request",
                               message="Review this correction and return the evidence.", origin="lark")
    _write(_root(root) / "policy.json", {
        "schema_version": POLICY_SCHEMA,
        "sources": {session["channel_id"]: {
            "local_delivery_scope": "selected",
            "sender_ids": ["fixture-owner"],
            "targets": [{"goal_id": "goal-a", "agent_id": agent}
                        for agent in ("agent-a", "reviewer")],
        }},
    })
    register_ingress(root, session_id=session["session_id"], client_turn_id=turn["client_turn_id"],
                     channel=session["channel_id"], sender_id="fixture-owner",
                     message=turn["message"], source_id="lark:fixture-source")
    # The host authorization scope is a separate boundary; recipient/source
    # grants and all collaboration admission below use the real typed owner.
    monkeypatch.setattr("loopx.chat_manager_context.manager_authorization_scope_is_current",
                        lambda *args, **kwargs: True)
    controller = SimpleNamespace(store=store, coordination_runtime_root=root,
                                 registry_path=registry, manager_scope_resolver=None)
    response = apply_context_handoff(
        controller, {"kind": "external_audience"}, session, turn,
        {"context_handoff": {"goal_id": "goal-a", "agent_id": "agent-a"}},
        {"authorization_scope_id": "synthetic-host-scope"}, execution_allowed=lambda: False,
    )
    receipt = response["context_handoff_receipt"]
    store.update_turn(session["session_id"], turn["turn_id"], status="completing", response=response)
    store.finalize_managed_turn_completion(session["session_id"], turn["turn_id"])
    return root, registry, store, session, turn, receipt


@pytest.mark.parametrize("provider", ["file", "sqlite", "source_session"])
@pytest.mark.parametrize("publication", ["transcript", "provider"])
def test_source_revocation_fences_original_return(tmp_path, monkeypatch, provider, publication):
    from loopx.control_plane.collaboration import source_policy_fence
    from loopx.capabilities.manager_context import configure_delivery_target
    from loopx.capabilities.manager_context.roundtrip import report
    from loopx.control_plane.collaboration.inbox import acknowledge

    root, registry, store, session, turn, receipt = source_request(tmp_path, monkeypatch, provider=provider)
    rid, goal_ref = receipt["request_id"], receipt.get("goal_ref")
    acknowledge(root, "goal-a", "agent-a", rid, "adopt", "Read exact source",
                registry=registry, caller_goal_ref=goal_ref)
    report(root, "goal-a", "agent-a", rid, "conclusion", "Checked the exact original source.",
           registry=registry, caller_goal_ref=goal_ref)
    entered, release, attempted, revoked = [threading.Event() for _ in range(4)]
    append = store.append_message
    thread_lock = source_policy_fence._thread_lock
    revoker, first_attempt, sent = [], [], []

    def pause():
        entered.set()
        assert release.wait(10), "test did not release return publication"

    def append_message(*args, **kwargs):
        if publication == "transcript" and kwargs.get("origin") == "manager_followup":
            pause()
        return append(*args, **kwargs)

    def sender(*args):
        if publication == "provider":
            pause()
        sent.append(revoked.is_set())
        return {"reply_verified": True, "verification_performed": True}

    @contextmanager
    def observe_lock(path):
        lock = thread_lock(path)
        if revoker == [threading.get_ident()] and not first_attempt:
            # Source writers first contend on the real in-process fence, before
            # entering the kernel fence. Observe that owner instead of waiting
            # for a syscall which correctly cannot happen until publication ends.
            acquired = lock.acquire(blocking=False)
            first_attempt.append(acquired)
            attempted.set()
            if acquired:
                try:
                    yield
                finally:
                    lock.release()
                return
        with lock:
            yield

    def revoke():
        revoker.append(threading.get_ident())
        result = configure_delivery_target(
            root, registry, channel=session["channel_id"], goal_id="goal-a",
            agent_id="agent-a", grant=False, execute=True,
        )
        revoked.set()
        return result

    monkeypatch.setattr(store, "append_message", append_message)
    monkeypatch.setattr(source_policy_fence, "_thread_lock", observe_lock)
    with ThreadPoolExecutor(max_workers=2) as pool:
        delivery = pool.submit(drain, root, registry, store, sender)
        try:
            assert entered.wait(10)
            revocation = pool.submit(revoke)
            assert attempted.wait(10)
            # Observe a real contended writer, not a wall-clock ordering guess.
            assert first_attempt == [False] and not revoked.is_set()
        finally:
            release.set()
        assert delivery.result(timeout=15) == 1
        assert revocation.result(timeout=15)["readback_verified"]
    assert sent == [False]
    messages = store.messages(session["session_id"])
    assert sum(m.get("origin") == "manager_followup" for m in messages) == 1
    assert store.turn_for_client(session["session_id"], turn["client_turn_id"])["turn_id"] == turn["turn_id"]

    # A later result cannot use the retired grant, even after a pump restart.
    report(root, "goal-a", "agent-a", rid, "conclusion", "A later result.",
           update_id="after-revocation", registry=registry, caller_goal_ref=goal_ref)
    drain(root, registry, ChatSessionStore(store.root.parent), sender)
    assert sent == [False]
    assert store.messages(session["session_id"]) == messages


def forward(root, registry, receipt, *, agent="reviewer", source="agent-a", operation="review"):
    return request(root, registry, "goal-a", source, agent, operation,
                   {"schema_version": "collaboration_brief_v0", "purpose": "Check the exact source",
                    "context": "Preserve the original correction.", "constraints": ["No work launch"],
                    "inputs": [], "acceptance": ["Return evidence and unresolved gaps"],
                    "return_requirement": "Return to the requester"},
                   parent_request_id=receipt["request_id"], caller_goal_ref=receipt.get("goal_ref"))


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_split_store_handoff_can_forward_source_context(tmp_path, monkeypatch, provider):
    root, registry, store, session, turn, receipt = source_request(tmp_path, monkeypatch, provider=provider)
    peer = forward(root, registry, receipt)
    received = read_inbox(root, registry, "goal-a", "reviewer")["items"]
    assert received[0]["parent_request_id"] == receipt["request_id"]
    assert received[0]["request_id"] == peer["request_id"]
    assert not (root / "chat").exists(), "A context read must not create another Chat store"


@pytest.mark.parametrize("provider", ["file", "sqlite", "source_session"])
@pytest.mark.parametrize("reason,typed", [
    ("original_route_unavailable", True),
    ("return_authorization_unavailable", True),
    ("initial_delivery_receipt_unavailable", True),
    ("original_route_unavailable", False),
])
def test_prewrite_return_retains_typed_cause_and_recovers_original_result(
    tmp_path, monkeypatch, provider, reason, typed,
):
    from datetime import datetime, timedelta, timezone
    from loopx.capabilities.manager_context.roundtrip import ReturnResolutionBlocked, reply_status

    root, registry, store, session, turn, receipt = source_request(tmp_path, monkeypatch, provider=provider)
    rid = receipt["request_id"]
    cli(root, registry, "agent-a", "acknowledge", "--request-id", rid,
        "--decision", "adopt", "--reason", "Read the original source")
    cli(root, registry, "agent-a", "report", "--request-id", rid,
        "--reply-text", "Checked the original source and its result.")
    failed_reads, writes = [], []
    unavailable = True

    def sender(*_):
        if unavailable:
            failed_reads.append(reason)
            # Prose with the same spelling is still an untyped outage. Only
            # the adapter's Core-classified reason may enter public readback.
            if typed:
                raise ReturnResolutionBlocked(reason, "Synthetic source is temporarily unavailable")
            raise ValueError(reason)
        writes.append("original audience")
        return {"reply_verified": True, "idempotency_key": "synthetic-provider-receipt"}

    now = datetime.now(timezone.utc)
    expected = reason if typed else "original_route_or_return_delivery_unavailable"
    for offset in (0, 10):
        drain(root, registry, store, sender, now=now + timedelta(minutes=offset))
        status = reply_status(root, receipt)[0]
        assert status["status"] == "retry_pending" and status["error"] == expected
        public = project_chat_session_snapshot(root, store, session["session_id"], registry=registry)
        returned = [row for row in public["messages"] if row.get("origin") == "manager_followup"]
        assert len(returned) == 1 and returned[0]["turn_id"] == turn["turn_id"]
        assert returned[0]["return_delivery"]["error"] == expected
    assert failed_reads == [reason, reason] and writes == []
    # Source repair resumes the original retry clock and conclusion. Keeping
    # its typed cause must not terminalize a pending initial provider receipt.
    unavailable = False
    recovered = ChatSessionStore(store.root.parent)
    drain(root, registry, recovered, sender, now=now + timedelta(minutes=20))
    drain(root, registry, recovered, sender, now=now + timedelta(minutes=30))
    assert reply_status(root, receipt)[0]["status"] == "delivered"
    assert writes == ["original audience"]
    returned = [row for row in recovered.messages(session["session_id"])
                if row.get("origin") == "manager_followup"]
    assert len(returned) == 1 and returned[0]["turn_id"] == turn["turn_id"]
    assert recovered.turn_for_client(session["session_id"], turn["client_turn_id"])["turn_id"] == turn["turn_id"]


def route_path(root, receipt):
    return _root(root) / "roundtrips" / (receipt["request_id"] + ".json")


def cli(root, registry, agent, action, *args, ok=True):
    process = subprocess.run(
        [os.environ.get("LOOPX_TEST_CLI_PYTHON", sys.executable), "-I", "-m", "loopx.cli",
         "--format", "json", "--runtime-root", str(root), "--registry", str(registry),
         "manager-inbox", action, "--goal-id", "goal-a", "--agent-id", agent, *args],
        cwd=registry.parent, capture_output=True, text=True, timeout=30,
    )
    assert process.returncode == (0 if ok else 1), (process.stdout, process.stderr)
    return json.loads(process.stdout)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_host_restart_recovers_legacy_source_and_full_return_once(tmp_path, monkeypatch, provider):
    import threading

    root, registry, store, session, turn, receipt = source_request(tmp_path, monkeypatch, provider=provider)
    path = route_path(root, receipt)
    legacy = json.loads(path.read_text())
    legacy.pop("source_chat_runtime_root")
    _write(path, legacy)
    before_chat = {p: p.read_bytes() for p in store.root.rglob("*") if p.is_file()}
    brief_path = tmp_path / "brief.json"
    brief_path.write_text(json.dumps({"schema_version": "collaboration_brief_v0", "purpose": "Review source",
        "context": "Preserve the correction", "constraints": ["No work launch"], "inputs": [],
        "acceptance": ["Return exact evidence"], "return_requirement": "Return to the requester"}))
    args = ("--peer-agent-id", "reviewer", "--operation-id", "review", "--brief-file", str(brief_path),
            "--parent-request-id", receipt["request_id"])
    rejected = cli(root, registry, "agent-a", "request", *args, ok=False)
    assert not rejected["ok"] and "original Chat host" in json.dumps(rejected)
    with pytest.raises(ValueError, match="original Chat host"):
        forward(root, registry, receipt)
    assert not (root / "chat").exists() and json.loads(path.read_text()) == legacy
    # The existing real host pump receives its actual Chat store at startup.
    recovered = threading.Event()
    original = recover_source_chat_provenance

    def recovery(*args, **kwargs):
        count = original(*args, **kwargs)
        recovered.set()
        return count

    monkeypatch.setattr("loopx.capabilities.manager_context.roundtrip.recover_source_chat_provenance", recovery)
    service = ReturnService(root, registry, store, lambda *args: pytest.fail("No result is owed yet"))
    service.start()
    try:
        assert recovered.wait(5)
    finally:
        service.close()
    saved = json.loads(path.read_text())
    assert saved == legacy | {"source_chat_runtime_root": str(store.root.parent.resolve())}
    assert {p: p.read_bytes() for p in before_chat} == before_chat
    assert recover_source_chat_provenance(root, registry, store) == 0
    peer = cli(root, registry, "agent-a", "request", *args)
    assert cli(root, registry, "agent-a", "request", *args)["replayed"]
    received = cli(root, registry, "reviewer", "read")["items"][0]
    assert received["inherited_context"]["message"] == turn["message"]
    cli(root, registry, "reviewer", "acknowledge", "--request-id", peer["request_id"],
        "--decision", "adopt", "--reason", "Read exact source")
    cli(root, registry, "reviewer", "report", "--request-id", peer["request_id"],
        "--reply-text", "Review found no gap.")
    returned = cli(root, registry, "agent-a", "read")
    assert returned["peer_returns"]["items"][0]["request_id"] == peer["request_id"]
    cli(root, registry, "agent-a", "acknowledge-return", "--request-id", peer["request_id"])
    cli(root, registry, "agent-a", "acknowledge", "--request-id", receipt["request_id"],
        "--decision", "adopt", "--reason", "Adopt peer result")
    cli(root, registry, "agent-a", "report", "--request-id", receipt["request_id"],
        "--reply-text", "Accepted exact review.")
    sent = []

    def sender(route, original_session, original_turn, text):
        sent.append((route, original_session, original_turn, text))
        return {"reply_verified": True, "verification_performed": True}

    drain(root, registry, store, sender)
    assert len(sent) == 1
    assert sent[0][0]["request_id"] == receipt["request_id"]
    assert sent[0][1]["session_id"] == session["session_id"]
    assert sent[0][2]["client_turn_id"] == turn["client_turn_id"]
    assert drain(root, registry, store, sender) == 0
    snapshot = project_chat_session_snapshot(root, store, session["session_id"], registry=registry)
    assert any("Accepted exact review." in row.get("text", "") for row in snapshot["messages"])
    store.update_session(session["session_id"], status="closed")
    assert not cli(root, registry, "agent-a", "request", *args, ok=False)["ok"]


@pytest.mark.parametrize("change", ["closed", "missing_turn", "revoked", "source_changed", "wrong_store",
                                        "channel_changed", "route_changed", "receipt_conflict", "unsettled"])
@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_invalid_legacy_sources_are_not_recovered(tmp_path, monkeypatch, change, provider):
    root, registry, store, session, turn, receipt = source_request(tmp_path, monkeypatch, provider=provider)
    path = route_path(root, receipt)
    legacy = json.loads(path.read_text())
    legacy.pop("source_chat_runtime_root")
    if change == "closed":
        store.update_session(session["session_id"], status="closed")
    elif change == "missing_turn":
        store._turn_path(session["session_id"], turn["turn_id"]).unlink()
    elif change == "revoked":
        policy = json.loads((_root(root) / "policy.json").read_text())
        policy["sources"][session["channel_id"]]["sender_ids"] = []
        _write(_root(root) / "policy.json", policy)
    elif change == "source_changed":
        ingress_path = _root(root) / "ingress" / (_hash([session["session_id"], turn["client_turn_id"]]) + ".json")
        ingress = json.loads(ingress_path.read_text())
        _write(ingress_path, ingress | {"source_id": "lark:another-source"})
    elif change == "wrong_store":
        store = ChatSessionStore(tmp_path / "unrelated-host")
    elif change == "channel_changed":
        legacy["channel_id"] = "manager.external.other"
    elif change == "route_changed":
        legacy["source_id"] = "lark:other-source"
    elif change == "receipt_conflict":
        store.update_turn(session["session_id"], turn["turn_id"],
                          response={"context_handoff_receipt": {**receipt, "request_id": "f" * 64}})
    elif change == "unsettled":
        store.update_turn(session["session_id"], turn["turn_id"], status="queued")
    _write(path, legacy)
    collaboration_root = _root(root)
    before_requests = {p: p.read_bytes() for folder in ("entries", "peer-operations", "roundtrips")
                       for p in (collaboration_root / folder).rglob("*.json")}
    assert recover_source_chat_provenance(root, registry, store) == 0
    assert json.loads(path.read_text()) == legacy
    with pytest.raises(ValueError):
        forward(root, registry, receipt)
    assert {p: p.read_bytes() for folder in ("entries", "peer-operations", "roundtrips")
            for p in (collaboration_root / folder).rglob("*.json")} == before_requests
    assert not (root / "chat").exists()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_pinned_source_is_read_only_not_retargetable_and_keeps_current_grants(tmp_path, monkeypatch, provider):
    from loopx.capabilities.manager_context.roundtrip import register
    from loopx.control_plane.collaboration.inbox import _entry

    root, registry, store, session, turn, receipt = source_request(tmp_path, monkeypatch, provider=provider)
    path = route_path(root, receipt)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        register(root, _entry(root, "goal-a", "agent-a", receipt["request_id"]), session, turn,
                 source_store=ChatSessionStore(tmp_path / "other-host"))
    assert path.read_bytes() == before
    peer = forward(root, registry, receipt)
    # A third hop is not implicitly granted by the first successful forwarding.
    with pytest.raises(ValueError):
        forward(root, registry, peer, source="reviewer", agent="third", operation="third-hop")
    policy = json.loads((_root(root) / "policy.json").read_text())
    policy["sources"][session["channel_id"]]["targets"] = [{"goal_id": "goal-a", "agent_id": "agent-a"}]
    _write(_root(root) / "policy.json", policy)
    with pytest.raises(ValueError):
        forward(root, registry, receipt, operation="revoked-review")
    assert path.read_bytes() == before


@pytest.mark.parametrize("locator", [None, {}, "relative-root", "/unrelated-synthetic-host"])
@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_host_cannot_replace_existing_provenance(tmp_path, monkeypatch, locator, provider):
    from loopx.capabilities.manager_context.roundtrip import register
    from loopx.control_plane.collaboration.inbox import _entry

    root, registry, store, session, turn, receipt = source_request(tmp_path, monkeypatch, provider=provider)
    path = route_path(root, receipt)
    _write(path, json.loads(path.read_text()) | {"source_chat_runtime_root": locator})
    before = path.read_bytes()
    with pytest.raises(ValueError, match="route"):
        register(root, _entry(root, "goal-a", "agent-a", receipt["request_id"]), session, turn, source_store=store)
    assert path.read_bytes() == before


def test_colocated_host_preserves_legacy_route_shape(tmp_path, monkeypatch):
    root, registry, store, session, turn, receipt = source_request(tmp_path, monkeypatch, colocated=True)
    path = route_path(root, receipt)
    before = path.read_bytes()
    assert "source_chat_runtime_root" not in json.loads(before)
    assert recover_source_chat_provenance(root, registry, store) == 0
    forward(root, registry, receipt)
    assert path.read_bytes() == before


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_committed_request_survives_a_lost_chat_answer(tmp_path, monkeypatch, provider):
    root, registry, store, session, turn, receipt = source_request(tmp_path, monkeypatch, provider=provider)
    path = route_path(root, receipt)
    route = json.loads(path.read_text())
    route.pop("source_chat_runtime_root")
    _write(path, route)
    store.update_turn(session["session_id"], turn["turn_id"], status="failed", response=None)
    assert recover_source_chat_provenance(root, registry, store) == 1
    assert forward(root, registry, receipt)["status"] == "delivered"


@pytest.mark.parametrize("change", ["closed", "missing_turn", "bad_locator", "wrong_locator", "ambiguous_turn"])
@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_pinned_source_still_requires_the_exact_available_conversation(tmp_path, monkeypatch, change, provider):
    root, registry, store, session, turn, receipt = source_request(tmp_path, monkeypatch, provider=provider)
    path = route_path(root, receipt)
    if change == "closed":
        store.update_session(session["session_id"], status="closed")
    elif change == "missing_turn":
        store._turn_path(session["session_id"], turn["turn_id"]).unlink()
    elif change in {"bad_locator", "wrong_locator"}:
        route = json.loads(path.read_text())
        route["source_chat_runtime_root"] = "relative-root" if change == "bad_locator" else str(tmp_path / "absent-host")
        _write(path, route)
    else:
        # Two synthetic stored turns claiming one client identity are ambiguous.
        duplicate = store.load_turn(session["session_id"], turn["turn_id"])
        duplicate["turn_id"] = "duplicate"
        _write(store._turn_path(session["session_id"], "duplicate"), duplicate)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        forward(root, registry, receipt)
    assert recover_source_chat_provenance(root, registry, store) == 0
    assert path.read_bytes() == before and not (root / "chat").exists()


def test_source_session_recovery_preserves_goal_ref_and_rejects_new_instance(tmp_path, monkeypatch):
    from loopx.control_plane.projects.registry_codec import source_session_registry_transaction

    root, registry, store, session, turn, receipt = source_request(tmp_path, monkeypatch, provider="source_session")
    path = route_path(root, receipt)
    route = json.loads(path.read_text())
    route.pop("source_chat_runtime_root")
    _write(path, route)
    assert recover_source_chat_provenance(root, registry, store) == 1
    assert json.loads(path.read_text())["goal_ref"] == receipt["goal_ref"]
    peer = forward(root, registry, receipt)
    peer_route = json.loads(route_path(root, peer).read_text())
    assert peer_route["goal_ref"] == receipt["goal_ref"]
    with source_session_registry_transaction(registry, operation="replace_fixture") as transaction:
        payload = transaction.payload_copy()
        payload["goals"][0]["goal_instance_id"] = "ginst_" + "b" * 32
        transaction.commit(payload)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="instance"):
        forward(root, registry, receipt, operation="new-instance")
    assert path.read_bytes() == before


@pytest.mark.parametrize("provider", ["file", "sqlite", "source_session"])
@pytest.mark.parametrize("entrypoint", ["handoff", "peer"])
@pytest.mark.parametrize("order", ["revoke_first", "publish_first"])
def test_context_revocation_orders_inbox_publication(tmp_path, monkeypatch, provider, entrypoint, order):
    from loopx.capabilities import manager_context
    from loopx.control_plane.collaboration import peers

    root, registry, store, session, turn, receipt = source_request(tmp_path, monkeypatch, provider=provider)
    adapter = manager_context if entrypoint == "handoff" else peers
    publication_entered, release_publication = threading.Event(), threading.Event()
    original_plan = adapter.plan_request_publication

    def publication_plan(*args, **kwargs):
        plan = original_plan(*args, **kwargs)
        publication_entered.set()
        assert release_publication.wait(5), "test did not release inbox publication"
        return plan

    def publish():
        if entrypoint == "peer":
            return forward(root, registry, receipt)
        return manager_context.deliver(
            root, registry, session=session, turn=turn,
            request={"goal_id": "goal-a", "agent_id": "reviewer"}, source_store=store,
        )

    def revoke():
        return manager_context.configure_delivery_target(
            root, registry, channel=session["channel_id"], goal_id="goal-a",
            agent_id="reviewer", grant=False, execute=True,
        )

    def records():
        return {p.relative_to(_root(root)): p.read_bytes()
                for folder in ("entries", "peer-operations", "roundtrips")
                for p in (_root(root) / folder).rglob("*.json")}

    before = records()
    if order == "revoke_first":
        assert revoke()["readback_verified"]
        with pytest.raises(ValueError, match="not authorized"):
            publish()
        assert records() == before
        assert not read_inbox(root, registry, "goal-a", "reviewer")["items"]
        return

    monkeypatch.setattr(adapter, "plan_request_publication", publication_plan)
    revoke_entered = threading.Event()

    def concurrent_revoke():
        revoke_entered.set()
        return revoke()

    with ThreadPoolExecutor(max_workers=2) as executor:
        publishing = executor.submit(publish)
        try:
            assert publication_entered.wait(5)
            revoking = executor.submit(concurrent_revoke)
            assert revoke_entered.wait(5)
            # Revocation may not report completion across an admitted commit.
            # The timeout only bounds the blocked-call observation; events
            # determine when the real publication can continue.
            with pytest.raises(FutureTimeout):
                revoking.result(timeout=0.2)
        finally:
            release_publication.set()
        published = publishing.result(timeout=5)
        revoked = revoking.result(timeout=5)
    assert published["status"] == "delivered" and revoked["readback_verified"]
    received = read_inbox(root, registry, "goal-a", "reviewer")["items"]
    assert len(received) == 1 and received[0]["request_id"] == published["request_id"]
    if entrypoint == "peer":
        assert received[0]["parent_request_id"] == receipt["request_id"]
        assert received[0]["inherited_context"]["message"] == turn["message"]
    else:
        assert received[0]["message"] == turn["message"]
    # A committed receipt remains historical evidence, never a replay grant.
    committed = records()
    with pytest.raises(ValueError, match="not authorized"):
        publish()
    assert records() == committed
    assert not (root / "chat").exists()


def native_source_request(tmp_path, monkeypatch, provider):
    from loopx.capabilities.native_chat.conversation_bindings import ChatConversationBindings
    from loopx.capabilities.native_chat.project_context import ChatProjectContexts
    from loopx.capabilities.manager_context import deliver

    root, registry, store, _, _, legacy = source_request(tmp_path, monkeypatch, provider=provider)
    workspace = tmp_path / "native-workspace"
    workspace.mkdir()
    other = tmp_path / "other-workspace"
    other.mkdir()
    contexts = ChatProjectContexts([workspace, other])
    observation = {"verified": True, "transport_ref": "fixture-app",
                   "provider_ref": "b" * 24, "operator_ref": "c" * 24}
    bindings = ChatConversationBindings(root=store.root, project_contexts=contexts, observe=lambda _: observation)
    bindings.controller = SimpleNamespace(store=store, registry_path=registry, coordination_runtime_root=root)
    binding = bindings.configure(transport_ref="fixture-app", project_ref=contexts.available()[0]["project_ref"],
                                 executor_endpoint_id="codex", context_kind="steward")
    selected = bindings.resolve(binding_id=binding["binding_id"], source_ref="d" * 24,
                                sender_ref=binding["operator_ref"], private_human_message=True)
    session = store.create_session(goal_id="loopx-manager", agent_id="codex", adapter_kind="codex_app_server",
        upstream_thread_id="synthetic-host", channel_id=selected["channel_id"], steward_context=selected["context"])
    turn, _ = store.create_turn(session["session_id"], client_turn_id="native-source",
                               message="Review the original evidence.", origin="lark")
    bindings.ensure_delivery_scope(selected)
    register_ingress(root, session_id=session["session_id"], client_turn_id=turn["client_turn_id"],
        channel=session["channel_id"], sender_id=binding["operator_ref"], message=turn["message"],
        source_id="lark:native-source")
    receipt = deliver(root, registry, session=session, turn=turn,
                      request={"goal_id": "goal-a", "agent_id": "agent-a"}, source_store=store)
    store.update_turn(session["session_id"], turn["turn_id"], status="completing",
                      response={"context_handoff_receipt": receipt})
    store.finalize_managed_turn_completion(session["session_id"], turn["turn_id"])
    return SimpleNamespace(root=root, registry=registry, store=store, bindings=bindings,
                           selected=selected, session=session, turn=turn, receipt=receipt, legacy=legacy)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("change", ["disconnect", "replace"])
def test_native_binding_revokes_inherited_context_without_resurrection(tmp_path, monkeypatch, provider, change):
    value = native_source_request(tmp_path, monkeypatch, provider)
    bindings, selected = value.bindings, value.selected
    before = forward(value.root, value.registry, value.receipt, operation="before-revocation")
    policy_path = _root(value.root) / "policy.json"
    policy = json.loads(policy_path.read_text())
    # Exact sibling sources and legacy connections must survive. These are
    # policy fixtures, not source identities that authorize this request.
    unrelated = "manager.external.native." + "e" * 24 + "." + "d" * 24
    policy["sources"][unrelated] = dict(policy["sources"][selected["channel_id"]])
    _write(policy_path, policy)
    historical = route_path(value.root, value.receipt).read_bytes()
    if change == "disconnect":
        bindings.disconnect(selected["binding"]["binding_id"], expected_revision=bindings.read()["revision"])
    else:
        ref = next(row["project_ref"] for row in bindings.projects.available()
                   if row["project_ref"] != selected["binding"]["project_ref"])
        bindings.configure(transport_ref="fixture-app", project_ref=ref,
                           executor_endpoint_id="codex", context_kind="steward")
    assert value.store.load_session(value.session["session_id"])["status"] != "closed"
    for operation in ("after-revocation", "before-revocation"):
        with pytest.raises(ValueError, match="source authorization unavailable"):
            forward(value.root, value.registry, value.receipt, operation=operation)
    with pytest.raises(ValueError, match="no longer authorized"):
        bindings.ensure_delivery_scope(selected)
    retained = json.loads(policy_path.read_text())
    retired = {**policy["sources"][selected["channel_id"]], "sender_ids": [],
               "evidence_goal_scope": "selected", "evidence_goal_ids": []}
    assert retained == {**policy, "sources": {**policy["sources"], selected["channel_id"]: retired}}
    from loopx.capabilities.manager_context import evidence_scope_readback
    assert evidence_scope_readback(value.root, selected["channel_id"], registry_path=value.registry)["goal_ids"] == []
    assert route_path(value.root, value.receipt).read_bytes() == historical
    items = read_inbox(value.root, value.registry, "goal-a", "reviewer")["items"]
    assert [row["request_id"] for row in items] == [before["request_id"]]
    assert forward(value.root, value.registry, value.legacy, operation="unrelated")["status"] == "delivered"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("stage", ["policy", "binding"])
def test_native_binding_source_revocation_retries_policy_failure(tmp_path, monkeypatch, provider, stage):
    from loopx.capabilities import manager_context
    from loopx.capabilities.native_chat import conversation_bindings

    value = native_source_request(tmp_path, monkeypatch, provider)
    bindings = value.bindings
    current = bindings.read()
    owner, method = (manager_context, "_write") if stage == "policy" else (conversation_bindings, "_atomic_write_json")
    write = getattr(owner, method)

    def failed_write(path, row):
        raise OSError("synthetic revocation IO failure")

    monkeypatch.setattr(owner, method, failed_write)
    with pytest.raises(OSError, match="synthetic revocation"):
        bindings.disconnect(value.selected["binding"]["binding_id"], expected_revision=current["revision"])
    assert bindings.read() == current, "failed retirement must not advertise a completed disconnect"
    if stage == "binding":
        with pytest.raises(ValueError, match="source authorization unavailable"):
            forward(value.root, value.registry, value.receipt, operation="partial-retirement")
    monkeypatch.setattr(owner, method, write)
    result = bindings.disconnect(value.selected["binding"]["binding_id"], expected_revision=current["revision"])
    assert not result["bindings"]
    assert bindings.disconnect(value.selected["binding"]["binding_id"], expected_revision=result["revision"]) == result
    with pytest.raises(ValueError, match="source authorization unavailable"):
        forward(value.root, value.registry, value.receipt)


@pytest.mark.parametrize("with_transport", [False, True])
def test_composed_binding_reuses_host_and_retires_source(tmp_path, monkeypatch, with_transport):
    from loopx.capabilities.native_chat.transports import install_conversation_transports

    value = native_source_request(tmp_path, monkeypatch, "file")
    controller = value.bindings.controller
    controller.project_contexts = value.bindings.projects
    server = SimpleNamespace(runtime_controller=controller, chat_store=value.store)
    observe = value.bindings._observe_owner
    transport = SimpleNamespace(transport_ref="fixture-app", observe=lambda: observe("fixture-app"))
    install_conversation_transports(server, observe_default=observe,
                                   factories=[lambda _: transport] if with_transport else [])
    bindings = controller.project_contexts.conversation_bindings
    assert bindings.controller is controller
    assert bindings.session_context(value.session["steward_context"])["channel_id"] == value.session["channel_id"]
    bindings.disconnect(value.selected["binding"]["binding_id"], expected_revision=bindings.read()["revision"])
    with pytest.raises(ValueError, match="source authorization unavailable"):
        forward(value.root, value.registry, value.receipt)


def test_source_retirement_without_policy_does_not_initialize_storage(tmp_path):
    from loopx.capabilities.manager_context import revoke_conversation_sources

    root = tmp_path / "unused-runtime"
    revoke_conversation_sources(root, "a" * 24)
    assert not root.exists()
