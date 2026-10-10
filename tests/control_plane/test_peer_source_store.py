"""Source-backed peer context across separate Chat and coordination stores."""

import json
import os
import subprocess
import sys
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
        upstream_thread_id="synthetic-host", channel_id="manager.external.fixture",
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
