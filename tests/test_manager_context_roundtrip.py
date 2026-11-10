"""One delegation automatically returns a conclusion without a second question."""

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from loopx.capabilities.manager_context import (
    _root,
    _write,
    acknowledge,
    authority,
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
from loopx.control_plane.collaboration import conversation_scope


@pytest.fixture
def frozen_private_reply(tmp_path):
    """Existing provider/Chat receipts, without starting a model or consumer."""
    from loopx.control_plane.collaboration import conversation_reply_context
    from loopx.extensions.lark.conversation_identity import identity_ref, lark_private_source

    store = ChatSessionStore(tmp_path)
    event = {"message_id": "om_current", "parent_id": "om_parent", "root_id": "om_root",
             "thread_id": "omt_topic", "chat_id": "oc_owner", "sender_id": "ou_owner",
             "sender_type": "user", "chat_type": "p2p"}
    provider = identity_ref("cli_fixture")
    source = lark_private_source(provider_ref=provider, event=event)
    saved = {"kind": "bound_steward", "project_ref": "a" * 24, "workspace_path": str(tmp_path),
             "audience": "bound_owner", "grant": "portfolio_read", "binding_id": "b" * 24,
             "provider_ref": provider, "operator_ref": source["sender_ref"],
             "source_ref": source["source_ref"], "goal_ids": []}
    session = store.create_session(goal_id="loopx-manager", agent_id="codex",
        adapter_kind="codex_app_server", upstream_thread_id="original", steward_context=saved,
        channel_id=f"manager.external.native.{saved['binding_id']}.{source['source_ref']}")
    parent = "Original parent with a code sample.\n\n```py\nprint('example')\n```"
    source_text = "只核对这次修改。\n\nCurrent user message:\n用户自己输入的字样也要保留。"
    context = {**event, "source_conversation_id": event["chat_id"], "reply_context": {
        "message_id": event["parent_id"], "conversation_id": event["chat_id"], "content": parent},
        "thread_context": {"conversation_id": event["chat_id"], "root_message_id": event["root_id"],
            "thread_id": event["thread_id"], "messages": [
                {"message_id": event["root_id"], "conversation_id": event["chat_id"], "thread_id": event["thread_id"],
                 "position": -1, "content": "Earlier topic introduction", "sender": {"id": "ou_owner", "kind": "user"}},
                {"message_id": event["message_id"], "conversation_id": event["chat_id"], "thread_id": event["thread_id"],
                 "position": 2, "content": source_text}]}}
    text = conversation_reply_context(context)["context_text"] + "\n\nCurrent user message:\n" + source_text
    request = identity_ref(provider, event["message_id"])
    turn, _ = store.create_turn(session["session_id"], client_turn_id=f"external-{request}", message=text, origin="lark")
    correlation = {"request_ref": request, "binding_id": saved["binding_id"], "source": source,
                   "session_id": session["session_id"], "turn_id": turn["turn_id"]}
    native = {**correlation, "message": text, "command": None}
    record = {**correlation, "event": event, "reply_context_message": text, "reply_context_source_text": source_text}
    for directory, value in (("external-requests", native), ("lark-private-deliveries", record)):
        _write(store.root / directory / f"{request}.json", value)
    return store, session, turn, request, native, record, source_text, parent


@pytest.fixture
def private_snapshot(frozen_private_reply, tmp_path):
    """Exercise provider history at its shipped HTTP composition boundary."""
    import http.client
    from types import SimpleNamespace
    from urllib.parse import urlencode

    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler

    store, session, *_ = frozen_private_reply
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.chat_store, server.verbose = store, False
    server.registry_path, server.runtime_root = tmp_path / "absent.json", tmp_path
    server.runtime_controller = SimpleNamespace(
        recover_exited_turn=lambda _: None, close=lambda: None,
        # Chat keeps its own storage when no registry names another root.
        coordination_runtime_root=store.root.parent,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def read(*, expected_status=200, **query):
        connection = http.client.HTTPConnection(*server.server_address, timeout=15)
        try:
            connection.request("GET", f"/api/chat/sessions/{session['session_id']}?{urlencode(query)}")
            response = connection.getresponse()
            payload = json.loads(response.read())
            assert response.status == expected_status, payload
            return payload
        finally:
            connection.close()

    try:
        yield read
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("scoped", [False, True])
def test_frozen_private_reply_projects_original_text_without_mutating_model_input(frozen_private_reply, tmp_path, private_snapshot, scoped):
    store, session, turn, _, _, _, source_text, parent = frozen_private_reply
    before = {str(p): p.read_bytes() for p in store.root.rglob("*") if p.is_file()}
    # The core projection carries no provider history; the composition root joins it.
    generic = project_chat_session_snapshot(tmp_path, store, session["session_id"], registry=tmp_path / "absent.json",
                                          message_limit=1)
    assert "reply_context" not in generic["messages"][0]
    snapshot = private_snapshot(message_limit=1, **({"turn_id": turn["turn_id"]} if scoped else {}))
    message = snapshot["messages"][0]
    assert message["text"] == turn["message"]
    assert message["reply_context"]["source_text"] == source_text
    assert message["reply_context"]["parent"]["content"] == parent
    assert message["reply_context"]["parent"]["complete"] is True
    assert [row["content"] for row in message["reply_context"]["thread"]["messages"]] == ["Earlier topic introduction"]
    assert {str(p): p.read_bytes() for p in store.root.rglob("*") if p.is_file()} == before


def test_history_pages_reuse_unchanged_journal_but_reread_provider_receipts(
        frozen_private_reply, private_snapshot, monkeypatch):
    store, session, turn, request, *_ = frozen_private_reply
    sid = session["session_id"]
    answer = store.append_message(sid, role="agent", text="Original answer", turn_id=turn["turn_id"])
    journal = store.sessions_root / sid / "messages.jsonl"
    original_row = journal.read_bytes().split(b"\n", 1)[0]
    decoded_original_rows = []
    loads = json.loads

    def observe(value, *args, **kwargs):
        if value == original_row:
            decoded_original_rows.append(value)
        return loads(value, *args, **kwargs)

    monkeypatch.setattr(json, "loads", observe)
    first = private_snapshot(message_limit=1)
    assert "reply_context" in first["messages"][0]
    cursor = first["message_page"]["next_cursor"]
    assert private_snapshot(message_limit=1, message_cursor=cursor)["messages"] == [answer]
    # Cached parsing must not keep a deleted provider receipt visible.
    (store.root / "lark-private-deliveries" / f"{request}.json").unlink()
    fresh = private_snapshot(message_limit=1)
    assert "reply_context" not in fresh["messages"][0]
    assert fresh["message_page"] == first["message_page"]
    assert len(decoded_original_rows) == 1

    open_path = Path.open

    def unavailable(path, *args, **kwargs):
        if path == journal:
            raise PermissionError("journal no longer readable")
        return open_path(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", unavailable)
    rejected = private_snapshot(message_limit=1, message_cursor=cursor, expected_status=503)
    assert "history is temporarily unavailable" in rejected["error"]


@pytest.mark.parametrize("replace", [False, True])
def test_warm_history_keeps_frozen_prefix_and_rejects_same_stamp_rewrite(
        frozen_private_reply, private_snapshot, monkeypatch, replace):
    store, session, turn, *_ = frozen_private_reply
    sid = session["session_id"]
    answer = store.append_message(sid, role="agent", text="Original answer", turn_id=turn["turn_id"])
    first = private_snapshot(message_limit=1)
    cursor = first["message_page"]["next_cursor"]
    extra = store.append_message(sid, role="agent", text="Later result")
    second = private_snapshot(message_limit=1, message_cursor=cursor)
    assert second["messages"] == [answer] and second["message_page"]["next_cursor"] is None
    assert private_snapshot(message_limit=3)["messages"][-1] == extra

    path = store.sessions_root / sid / "messages.jsonl"
    stat = path.stat()
    original = path.read_bytes()
    rewritten = original.replace(b"Original answer", b"Modified answer", 1)
    assert len(rewritten) == len(original) and rewritten != original
    target = path.with_suffix(".replacement") if replace else path
    target.write_bytes(rewritten)
    os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    if replace:
        target.replace(path)
    # A filesystem can expose the same metadata for different journal bytes.
    stat_path = Path.stat
    with monkeypatch.context() as observation:
        observation.setattr(Path, "stat", lambda item, *a, **kw: stat if item == path else stat_path(item, *a, **kw))
        rejected = private_snapshot(message_limit=1, message_cursor=cursor, expected_status=400)
        assert "history cursor source changed" in rejected["error"]
    assert private_snapshot(message_limit=3)["messages"][1]["text"] == "Modified answer"
    path.write_bytes(rewritten + b"{invalid record}\n")
    rejected = private_snapshot(message_limit=3, expected_status=400)
    assert "invalid Chat journal record" in rejected["error"]
    path.write_bytes(rewritten)
    assert private_snapshot(message_limit=3)["messages"][-1] == extra


def test_reply_history_keeps_failed_and_recorded_recovery_context(frozen_private_reply, tmp_path, private_snapshot):
    store, session, original, request, native, record, source_text, _ = frozen_private_reply
    client = f"external-{request}-recovery-21-quota-1"
    store.update_turn(session["session_id"], original["turn_id"], status="failed", error_code="synthetic_upstream_failure")
    store.release_active_turn(session["session_id"], original["turn_id"])
    recovery, _ = store.create_turn(session["session_id"], client_turn_id=client, message=original["message"], origin="lark")
    native.update(turn_id=recovery["turn_id"], context_recovery={"client_turn_id": client, "failed_turn_id": original["turn_id"]})
    record["turn_id"] = recovery["turn_id"]
    for directory, row in (("external-requests", native), ("lark-private-deliveries", record)):
        _write(store.root / directory / f"{request}.json", row)
    snapshot = private_snapshot()
    assert [m["reply_context"]["source_text"] for m in snapshot["messages"]] == [source_text, source_text]
    # A plausible-looking suffix without its original native receipt is not a source.
    store.update_turn(session["session_id"], recovery["turn_id"], status="completed")
    store.release_active_turn(session["session_id"], recovery["turn_id"])
    store.create_turn(session["session_id"], client_turn_id=f"external-{request}-recovery-999", message=original["message"], origin="lark")
    snapshot = private_snapshot()
    assert "reply_context" not in snapshot["messages"][-1]


@pytest.mark.parametrize("drift", ["session", "turn", "binding", "source", "native_message", "frozen_message",
                                  "parent", "thread", "order", "timestamp", "sender", "event_shape", "missing", "user_spoof", "foreign_origin"])
def test_private_reply_view_requires_exact_frozen_source(frozen_private_reply, tmp_path, drift, private_snapshot):
    store, session, turn, request, native, record, _, _ = frozen_private_reply
    path = store.root / "lark-private-deliveries" / f"{request}.json"
    if drift in {"session", "turn", "binding"}:
        record[{"session": "session_id", "turn": "turn_id", "binding": "binding_id"}[drift]] = "foreign"
    elif drift == "source":
        record["source"] = {**record["source"], "source_ref": "d" * 24}
    elif drift == "native_message":
        native["message"] = "different input"
    elif drift == "frozen_message":
        record["reply_context_message"] += "changed"
    elif drift == "event_shape":
        record["event"] = []
    elif drift in {"parent", "thread"}:
        record["event"]["parent_id" if drift == "parent" else "thread_id"] = "foreign"
    elif drift in {"order", "timestamp", "sender"}:
        # Keep all correlation text identical, but challenge the wire decoder's ordering oracle.
        replacement = {"order": '"position":-2', "timestamp": '"position":-1,"created_at":{}',
                       "sender": '"position":-1,"sender":"invalid"'}[drift]
        text = turn["message"].replace('"position":-1', replacement)
        if drift == "sender":
            text = text.replace(',"sender":{"id":"ou_owner","kind":"user"}', '')
        native["message"] = record["reply_context_message"] = text
        _write(store._turn_path(session["session_id"], turn["turn_id"]), {**turn, "message": text})
        store.append_message(session["session_id"], role="user", text=text, turn_id=turn["turn_id"], origin="lark")
    _write(path, record)
    _write(store.root / "external-requests" / f"{request}.json", native)
    if drift in {"missing", "user_spoof"}:
        path.unlink()
    if drift == "foreign_origin":
        store.append_message(session["session_id"], role="user", text=turn["message"], turn_id=turn["turn_id"], origin="peer")
    snapshot = private_snapshot()
    message = snapshot["messages"][-1]
    assert "reply_context" not in message
    assert message["text"] in {turn["message"], record["reply_context_message"]}


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

    def create(external=False, project=False, brief=None, source_store=None, native=False):
        actual_store = source_store or store
        session = actual_store.create_session(
            goal_id="research" if project else "loopx-manager",
            agent_id="codex",
            adapter_kind="codex_app_server",
            upstream_thread_id="test",
            channel_id=("goal.research" if project else "manager.external.native.fixture.owner" if native
                        else "manager.external.test" if external else "manager"),
        )
        turn, _ = actual_store.create_turn(
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
            request={**target, **({"brief": brief} if brief else {})},
            source_store=source_store,
        )
        actual_store.update_turn(
            session["session_id"],
            turn["turn_id"],
            status="completing",
            response={
                "message": "Delivered; a conclusion will return here.",
                "context_handoff_receipt": receipt,
            },
        )
        actual_store.finalize_managed_turn_completion(session["session_id"], turn["turn_id"])
        return session, turn, receipt

    return tmp_path, registry, store, create


@pytest.mark.parametrize("external", [False, True], ids=["owner", "original-audience"])
def test_handoff_reader_retains_partial_scope_after_delivery_and_later_results(flow, external):
    from loopx.capabilities.manager_context.inspection import ManagerInspection, TOOL_NAME

    root, registry, store, create = flow
    session, _, receipt = create(external=external)
    rid = receipt["request_id"]
    acknowledge(root, "research", "worker", rid, "adopt", "Inspect original evidence")
    tool = ManagerInspection(context={"goals": [{"goal_id": "research"}]},
        registry_path=registry, runtime_root=root, owner_scope=not external,
        channel_id=session["channel_id"], scope_valid=lambda: True, record=lambda _: None)

    def read():
        before = {str(p): p.read_bytes() for p in _root(root).rglob("*.json")}
        page = tool.read(TOOL_NAME, {"view": "handoffs", "request_id": rid})
        assert page["ok"] and not page["unknown"] and len(page["rows"]) == 1
        assert {str(p): p.read_bytes() for p in _root(root).rglob("*.json")} == before
        return page["rows"][0]

    remaining = {"summary": "Measure the original revision sequence", "resume_when": "The original evidence is available"}

    def sender(*_):
        return {"ok": True, "reply_verified": True}

    report(root, "research", "worker", rid, "conclusion", "An initial analysis is available.", remaining_work=remaining)
    assert read()["remaining_work"] == remaining
    assert drain(root, registry, store, sender) == 1
    partial = read()
    assert partial["return_replies"][0]["status"] == "delivered"
    assert partial["inbox_state"] == "awaiting_conclusion"
    assert partial["agenda"]["remaining_work"] == remaining
    assert partial["agenda"]["step"] == "await_peer_result"

    report(root, "research", "worker", rid, "conclusion", "Original evidence measured.", update_id="measured")
    queued = read()
    assert "remaining_work" not in queued
    assert queued["agenda"]["step"] == "await_return_delivery"
    assert drain(root, registry, store, sender) == 1
    complete = read()
    assert complete["inbox_state"] == "settled" and complete["agenda"] is None

    # An old delivered conclusion must not erase a later explicit remainder.
    report(root, "research", "worker", rid, "conclusion", "A qualification gap remains.",
           update_id="qualification", remaining_work=remaining)
    assert drain(root, registry, store, sender) == 1
    restarted = ManagerInspection(context=tool.context, registry_path=registry, runtime_root=root,
        owner_scope=not external, channel_id=session["channel_id"], scope_valid=lambda: True, record=lambda _: None)
    row = restarted.read(TOOL_NAME, {"view": "handoffs", "request_id": rid})["rows"][0]
    assert row["remaining_work"] == remaining and row["agenda"]["receipt_state"] == "awaiting_conclusion"
    scope_reads = []

    def revoked_after_read():
        scope_reads.append(True)
        return len(scope_reads) == 1

    restarted.scope_valid = revoked_after_read
    assert restarted.read(TOOL_NAME, {"view": "handoffs", "request_id": rid}) == {
        "ok": False, "error": "authorization_changed"}


def test_handoff_reader_keeps_conflicting_result_scope_unknown_and_audience_bound(flow):
    from loopx.capabilities.manager_context.inspection import ManagerInspection, TOOL_NAME

    root, registry, store, create = flow
    session, _, receipt = create(external=True)
    rid = receipt["request_id"]
    acknowledge(root, "research", "worker", rid, "adopt", "Checked")
    report(root, "research", "worker", rid, "conclusion", "Original partial result.",
           remaining_work={"summary": "Validate", "resume_when": "Evidence available"})
    assert drain(root, registry, store, lambda *_: {"ok": True, "reply_verified": True}) == 1
    path = _root(root) / "replies" / rid / "conclusion.json"
    saved = json.loads(path.read_text())
    saved["remaining_work"] = {"summary": "Missing continuation condition"}
    path.write_text(json.dumps(saved))
    tool = ManagerInspection(context={"goals": [{"goal_id": "research"}]},
        registry_path=registry, runtime_root=root, owner_scope=False,
        channel_id=session["channel_id"], scope_valid=lambda: True, record=lambda _: None)
    page = tool.read(TOOL_NAME, {"view": "handoffs", "request_id": rid})
    assert page["unknown"]
    row = page["rows"][0]
    assert row["inbox_state"] == "receipt_unavailable"
    assert row["agenda"]["step"] == "recover_evidence"
    assert "conclusion_unreadable_or_conflicting" in row["warnings"]
    tool.channel_id = "manager.external.another-owner"
    other = tool.read(TOOL_NAME, {"view": "handoffs", "request_id": rid})
    assert other["ok"] and other["rows"] == []


def test_handoff_scope_is_independent_of_page_size_and_stale_projection_annotations(flow, monkeypatch):
    from loopx.capabilities.manager_context.inspection import ManagerInspection, TOOL_NAME
    from loopx.capabilities.manager_context import tracking

    root, registry, store, create = flow
    session, _, receipt = create()
    rid = receipt["request_id"]
    acknowledge(root, "research", "worker", rid, "adopt", "Checked")
    remaining = {"summary": "Validate", "resume_when": "Evidence available"}
    report(root, "research", "worker", rid, "conclusion", "A partial result.", remaining_work=remaining)
    assert drain(root, registry, store, None) == 1
    for _ in range(12):
        create()
    tool = ManagerInspection(context={"goals": [{"goal_id": "research"}]},
        registry_path=registry, runtime_root=root, owner_scope=True,
        channel_id=session["channel_id"], scope_valid=lambda: True, record=lambda _: None)

    def pages(limit):
        result, offset = {}, 0
        while offset is not None:
            page = tool.read(TOOL_NAME, {"view": "handoffs", "limit": limit, "offset": offset})
            assert page["ok"] and not page["unknown"]
            result.update({row["request_id"]: row for row in page["rows"]})
            offset = page["next_offset"]
        return result

    small, large = pages(3), pages(12)
    assert len(small) == len(large) == 13
    assert {key: (row["inbox_state"], row["agenda"]) for key, row in small.items()} == {
        key: (row["inbox_state"], row["agenda"]) for key, row in large.items()}
    assert small[rid]["remaining_work"] == remaining
    report(root, "research", "worker", rid, "conclusion", "Final result.", update_id="verified")
    assert drain(root, registry, store, None) == 1
    query = tracking.query

    def stale_annotation(*args, **kwargs):
        packet = query(*args, **kwargs)
        for row in packet["rows"]:
            if row["request_id"] == rid:
                row.update({key: small[rid][key] for key in ("inbox_state", "agenda", "remaining_work")})
        return packet

    monkeypatch.setattr(tracking, "query", stale_annotation)
    final = tool.read(TOOL_NAME, {"view": "handoffs", "request_id": rid})["rows"][0]
    assert final["inbox_state"] == "settled" and final["agenda"] is None
    assert "remaining_work" not in final


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


@pytest.mark.parametrize("project", [False, True], ids=["steward", "project"])
def test_owner_return_does_not_create_source_policy(flow, project):
    root, registry, store, create = flow
    session, turn, receipt = create(project=project)
    rid = receipt["request_id"]
    acknowledge(root, "research", "worker", rid, "adopt", "Checked")
    report(root, "research", "worker", rid, "conclusion", "Owner result.")
    assert drain(root, registry, store, None) == 1
    assert reply_status(root, receipt)[0]["status"] == "delivered"
    returned = [m for m in store.messages(session["session_id"])
                if m.get("origin") == "manager_followup"]
    assert len(returned) == 1 and returned[0]["turn_id"] == turn["turn_id"]
    assert not list(_root(root).glob("policy*"))


def test_cli_legacy_explicit_original_revision_returns_once(flow, capsys):
    from loopx.cli import main

    root, registry, store, create = flow
    session, turn, receipt = create()
    rid = receipt["request_id"]
    acknowledge(root, "research", "worker", rid, "adopt", "Checked the original request")
    text = "Checked the original request and completed its existing work."
    argv = ["--registry", str(registry), "--runtime-root", str(root), "--format", "json",
            "manager-inbox", "report", "--goal-id", "research", "--agent-id", "worker",
            "--request-id", rid, "--reply-text", text]
    assert main([*argv, "--request-revision", "1"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "queued_for_original_conversation"
    assert main(argv) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert drain(root, registry, store, None) == 1
    assert drain(root, registry, ChatSessionStore(root), None) == 0
    returned = [row for row in store.messages(session["session_id"])
                if row.get("origin") == "manager_followup"]
    assert len(returned) == 1 and returned[0]["turn_id"] == turn["turn_id"]
    assert returned[0]["text"] == f"{text}\n\n协作回复 · worker · 委托 {rid[:8]}"
    assert reply_status(root, receipt)[0]["status"] == "delivered"
    assert not list(_root(root).glob("policy*"))
    entry = next((_root(root) / "entries").glob("*/*.json"))
    assert "request_control" not in json.loads(entry.read_text())


def test_legacy_original_revision_does_not_repair_mismatched_initial_delivery(flow):
    root, registry, store, create = flow
    session, turn, receipt = create(True)
    rid = receipt["request_id"]
    store.update_turn(session["session_id"], turn["turn_id"], response={
        "context_handoff_receipt": {**receipt, "agent_id": "another-worker"},
    })
    acknowledge(root, "research", "worker", rid, "adopt", "Checked")
    report(root, "research", "worker", rid, "conclusion", "Checked the original request.",
           registry=registry, request_revision=1)
    calls = []
    drain(root, registry, store, lambda *args: calls.append(args))
    assert calls == []
    assert not any(row.get("origin") == "manager_followup"
                   for row in store.messages(session["session_id"]))
    assert reply_status(root, receipt)[0]["status"] != "delivered"
    assert store.load_turn(session["session_id"], turn["turn_id"])["response"][
        "context_handoff_receipt"]["agent_id"] == "another-worker"


@pytest.mark.parametrize("audience", ["local_owner", "bound_owner"])
@pytest.mark.parametrize("drift", ["channel", "goal", "context", "context_missing", "context_null"])
def test_recovered_project_identity_cannot_publish_as_a_manager(flow, audience, drift):
    root, registry, _, _ = flow
    store = ChatSessionStore(root / "separate-chat-host")
    project = {"kind": "project_workspace", "project_ref": "a" * 24,
               "workspace_path": str(root), "audience": audience, "grant": "workspace_read"}
    channel = "project." + project["project_ref"]
    if audience == "bound_owner":
        project.update(binding_id="b" * 24, source_ref="c" * 24,
                       provider_ref="d" * 24, operator_ref="e" * 24)
        channel = "project.external." + project["binding_id"] + "." + project["source_ref"]
    session = store.create_session(goal_id=None, agent_id="codex", adapter_kind="codex_app_server",
                                   upstream_thread_id="original", channel_id=channel, project_context=project)
    turn, _ = store.create_turn(session["session_id"], client_turn_id="question",
                                message="Read-only project question", origin="web")
    # Inject inconsistent recovered metadata only in this disposable store.
    # The registered receiver exists; the project never gained its source grant.
    path = store.root / "sessions" / session["session_id"] / "session.json"
    saved = json.loads(path.read_text())
    saved["channel_id"] = "manager"
    if drift == "goal":
        saved["goal_id"] = "research"
    elif drift == "context":
        saved["project_context"]["grant"] = "invalid"
    elif drift == "context_missing":
        saved.pop("project_context")
    elif drift == "context_null":
        saved["project_context"] = None
    path.write_text(json.dumps(saved))
    observed = store.load_session(session["session_id"])
    before = {p.relative_to(_root(root)): p.read_bytes() for p in _root(root).rglob("*.json")}
    with pytest.raises(ValueError, match="context recipient is not authorized or registered"):
        deliver(root, registry, session=observed, turn=turn,
                request={"goal_id": "research", "agent_id": "worker"}, source_store=store)
    assert {p.relative_to(_root(root)): p.read_bytes() for p in _root(root).rglob("*.json")} == before
    assert not pending(root, "research", "worker")["items"]
    assert json.loads(path.read_text()) == saved
    assert store.load_turn(session["session_id"], turn["turn_id"]) == turn


def test_legacy_manager_source_preserves_missing_goal_instead_of_project_null(flow):
    root, registry, store, _ = flow
    session = store.create_session(goal_id="loopx-manager", agent_id="codex", adapter_kind="codex_app_server",
                                   upstream_thread_id="original", channel_id="manager")
    turn, _ = store.create_turn(session["session_id"], client_turn_id="question", message="Original question", origin="web")
    legacy = {key: value for key, value in session.items() if key != "goal_id"}
    assert conversation_scope(legacy)["kind"] == "owner_portfolio"
    assert conversation_scope({**legacy, "goal_id": None})["kind"] == "unavailable"
    assert authority(root, registry, legacy, turn)["targets"] == [{"goal_id": "research", "agent_id": "worker"}]
    assert authority(root, registry, {**legacy, "goal_id": None}, turn) == {"mode": "unavailable", "targets": []}


@pytest.mark.parametrize("explicit_source", [False, True], ids=["legacy-root", "pinned-root"])
def test_other_chat_host_cannot_delay_original_host_return(flow, explicit_source):
    root, registry, store, create = flow
    if explicit_source:
        store = ChatSessionStore(root / "private-chat-host")
    session, turn, receipt = create(True, source_store=store if explicit_source else None)
    rid = receipt["request_id"]
    acknowledge(root, "research", "worker", rid, "adopt", "Checked")
    report(root, "research", "worker", rid, "conclusion", "The original task is complete.")
    state_path = _root(root) / "replies" / rid / "conclusion.delivery.json"
    before = state_path.read_bytes() if state_path.exists() else None
    foreign = ChatSessionStore(root / "other-chat-host")
    sent = []

    def sender(route, session, turn, text):
        sent.append(text)
        return {"reply_verified": True, "idempotency_key": "sha256:provider-proof"}

    now = datetime.now(timezone.utc)
    assert drain(root, registry, foreign, sender, now=now) == 0
    assert (state_path.read_bytes() if state_path.exists() else None) == before
    assert sent == []
    # The wrong host cannot impose retry_at and starve the source host's
    # immediately following tick. A restart must not duplicate the result.
    assert drain(root, registry, store, sender, now=now) == 1
    drain(root, registry, ChatSessionStore(store.root.parent), sender, now=now)
    returned = [m for m in store.messages(session["session_id"])
                if m.get("origin") == "manager_followup"]
    assert len(sent) == len(returned) == 1
    assert returned[0]["turn_id"] == turn["turn_id"]
    assert reply_status(root, receipt)[0]["status"] == "delivered"


def test_exact_return_skips_other_host_before_admission(flow):
    from loopx.control_plane.projects.registry_codec import SOURCE_SESSION_PROFILE_ID

    root, registry, store, create = flow
    _, _, receipt = create()
    acknowledge(root, "research", "worker", receipt["request_id"], "adopt", "Checked")
    report(root, "research", "worker", receipt["request_id"], "conclusion", "Complete.")
    profile = json.loads(registry.read_text())
    profile["profile_id"] = SOURCE_SESSION_PROFILE_ID
    registry.write_text(json.dumps(profile))
    foreign = ChatSessionStore(root / "other-chat-host")

    def unexpected(*args, **kwargs):
        raise AssertionError("A foreign host must not admit or deliver this return")

    before = {path: path.read_bytes() for path in (_root(root) / "replies").rglob("*.json")}
    assert drain(root, registry, foreign, unexpected) == 0
    assert {path: path.read_bytes() for path in before} == before


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
    assert messages[1]["text"] == f"Review passed; delivery complete.\n\n协作回复 · worker · 委托 {rid[:8]}"
    assert messages[-1]["text"] == f"Receiver adopted the delivery.\n\n协作回复 · worker · 委托 {rid[:8]}"
    assert len({m["message_id"] for m in messages}) == 3
    assert {m["turn_id"] for m in messages} == {turn["turn_id"]}
    assert (root / ".local/manager-context/replies" / rid / "conclusion.json").read_bytes() == before
    assert [r["status"] for r in reply_status(root, receipt)] == ["delivered"] * 3
    if external:
        assert len(sent) == 3
        assert len({m[-1] for m in sent}) == 3


@pytest.mark.parametrize("outcome", ["delivered", "retry_pending", "explicit_unverified"])
def test_return_commit_refreshes_session_hint_without_rewriting_original_messages(flow, monkeypatch, outcome):
    root, registry, store, create = flow
    session, turn, receipt = create(True)
    sid, rid = session["session_id"], receipt["request_id"]
    acknowledge(root, "research", "worker", rid, "adopt", "Accepted")
    report(root, "research", "worker", rid, "conclusion", "The receiver returned the original result.")
    queued = {}

    def sender(*_):
        # The App can read the appended reply before its provider receipt settles.
        queued["session"] = store.public_session(store.load_session(sid))
        queued["messages"] = store.messages(sid)
        queued["journal"] = (store.sessions_root / sid / "messages.jsonl").read_bytes()
        view = project_chat_return_deliveries(root, sid, queued["messages"], source_store=store)
        assert view[-1]["return_delivery"]["status"] == "queued"
        stamp = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
        monkeypatch.setattr("loopx.chat_store.utc_now", lambda: stamp)
        return {"ok": outcome == "delivered", "reply_verified": outcome == "delivered",
                "external_write_performed": outcome == "explicit_unverified"}

    drain(root, registry, store, sender)
    current = store.public_session(store.load_session(sid))
    assert reply_status(root, receipt)[0]["status"] == outcome
    assert current["updated_at"] != queued["session"]["updated_at"], (
        "A committed delivery change must invalidate a cached bound conversation")
    assert current["transcript_revision"] == queued["session"]["transcript_revision"]
    assert store.messages(sid) == queued["messages"]
    assert (store.sessions_root / sid / "messages.jsonl").read_bytes() == queued["journal"]
    assert current["active_turn_id"] is None and current["status"] == "ready"
    assert {path.stem for path in (store.sessions_root / sid / "turns").glob("*.json")} == {turn["turn_id"]}
    assert all(current[key] == queued["session"][key]
               for key in current if key != "updated_at")


def test_return_read_hint_failure_does_not_resend_committed_reply(flow, monkeypatch, caplog):
    root, registry, store, create = flow
    _, _, receipt = create(True)
    rid = receipt["request_id"]
    acknowledge(root, "research", "worker", rid, "adopt", "Accepted")
    report(root, "research", "worker", rid, "conclusion", "The original result is ready.")
    sent = []

    def unavailable(*args, **kwargs):
        raise OSError("Synthetic session metadata failure")

    def sender(*args):
        sent.append(args)
        monkeypatch.setattr(store, "update_session", unavailable)
        return {"ok": True, "reply_verified": True}

    assert drain(root, registry, store, sender) == 1
    assert reply_status(root, receipt)[0]["status"] == "delivered"
    assert "Manager return session read hint unavailable" in caplog.text
    assert drain(root, registry, store, sender) == 0
    assert len(sent) == 1


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
    assert len(messages) == 1
    assert messages[0]["text"] == f"Waiting for review.\n\n协作回复 · worker · 委托 {rid[:8]}"


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
        assert args[0] == calls[0][0] and args[2:] == calls[0][2:]
        assert {key: value for key, value in args[1].items() if key != "updated_at"} == {
            key: value for key, value in calls[0][1].items() if key != "updated_at"
        }
        assert args[1]["updated_at"] != calls[0][1]["updated_at"]
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


def test_return_scan_isolates_invalid_address_from_valid_result(flow):
    root, registry, store, create = flow
    session, turn, receipt = create()
    rid = receipt["request_id"]
    acknowledge(root, "research", "worker", rid, "adopt", "Checked")
    report(root, "research", "worker", rid, "conclusion", "The original request is complete.")
    orphan = _root(root) / "replies" / "!orphan" / "conclusion.json"
    _write(orphan, {"text": "An unrelated damaged address."})
    original = orphan.read_bytes()

    assert drain(root, registry, store, None) == 1
    assert drain(root, registry, ChatSessionStore(root), None) == 0
    assert reply_status(root, receipt)[0]["status"] == "delivered"
    returned = [row for row in store.messages(session["session_id"])
                if row.get("origin") == "manager_followup"]
    assert len(returned) == 1 and returned[0]["turn_id"] == turn["turn_id"]
    assert orphan.read_bytes() == original
    assert not orphan.with_name("conclusion.delivery.json").exists()


@pytest.mark.parametrize("missing", ["session", "turn"])
def test_accepted_return_with_missing_original_route_terminates_without_resend(flow, missing):
    root, registry, store, create = flow
    session, turn, receipt = create(True)
    rid = receipt["request_id"]
    acknowledge(root, "research", "worker", rid, "adopt", "Checked")
    report(root, "research", "worker", rid, "conclusion", "The original request is complete.")
    attempt = {
        "schema_version": "manager_return_delivery_attempt_v0", "provider": "lark",
        "message_ref": "om_recorded_result", "intent_digest": "sha256:" + "a" * 64,
        "provider_receipt": "sha256:" + "b" * 64,
    }

    class Transport:
        sends = 0

        def send_with_attempt(self, route, session, turn, text, record_attempt):
            self.sends += 1
            record_attempt(attempt)
            return {"external_write_performed": True, "reply_verified": False}

        def verify(self, *_):
            pytest.fail("a missing original route cannot authorize verification")

    sender = Transport()
    drain(root, registry, store, sender)
    path = _root(root) / "replies" / rid / "conclusion.delivery.json"
    assert json.loads(path.read_text())["status"] == "verification_required"
    directory = store.root / "sessions" / session["session_id"]
    (directory / "session.json" if missing == "session" else
     directory / "turns" / (turn["turn_id"] + ".json")).unlink()

    assert drain(root, registry, ChatSessionStore(root), sender) == 1
    state = json.loads(path.read_text())
    assert state["status"] == "explicit_unverified"
    assert state["error"] == "original_route_unavailable"
    assert state["attempt"] == attempt
    saved = path.read_bytes()
    assert drain(root, registry, ChatSessionStore(root), sender,
                 now=datetime.now(timezone.utc) + timedelta(days=1)) == 0
    assert sender.sends == 1 and path.read_bytes() == saved


def test_known_provider_locator_is_verified_after_restart_without_resend(flow, monkeypatch):
    from loopx.capabilities.manager_context import roundtrip

    class Clock:
        instant = datetime.now(timezone.utc)

        @classmethod
        def now(cls, tz):
            assert tz is timezone.utc
            return cls.instant

    monkeypatch.setattr(roundtrip, "datetime", Clock)
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
            Clock.instant += timedelta(seconds=7)
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
    assert datetime.fromisoformat(recovered["delivered_at"]) == Clock.instant
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


@pytest.fixture
def returned_history(flow):
    root, registry, store, create = flow
    session, turn, receipt = create()
    acknowledge(root, "research", "worker", receipt["request_id"], "adopt", "Checked")
    report(root, "research", "worker", receipt["request_id"], "conclusion", "Completed the original request.")
    assert drain(root, registry, store, None) == 1
    return root, registry, store, session, turn, receipt


@pytest.fixture
def returned_file_history(flow):
    import os

    if os.open not in os.supports_dir_fd or not hasattr(os, "O_NOFOLLOW"):
        pytest.skip("result snapshots require no-follow directory opens")
    root, registry, store, create = flow
    session, turn, receipt = create(external=True, native=True)
    acknowledge(root, "research", "worker", receipt["request_id"], "adopt", "Return the requested artifacts")
    sources = {"结果.json": b'\xef\xbb\xbf{"result":"verified"}\r\n', "source.bin": b"\x00\xffbinary source\x00",
               "copy.bin": b"\x00\xffbinary source\x00"}
    for name, raw in sources.items():
        (root / name).write_bytes(raw)
    report(root, "research", "worker", receipt["request_id"], "conclusion",
        "The result and original artifacts are saved.\n\n" + "Complete original answer. " * 40,
        registry=registry, attachment_refs=list(sources))

    class Sender:
        supports_result_files = True
        calls = 0

        def __call__(self, *_):
            self.calls += 1
            return {"ok": True, "reply_verified": True}

    sender = Sender()
    assert drain(root, registry, store, sender) == 1
    message = next(row for row in store.messages(session["session_id"]) if row.get("origin") == "manager_followup")
    assert not message.get("attachments"), "Reproduce an existing delivered answer whose transcript lost its files"
    return root, registry, store, session, turn, receipt, message, sources, sender


def test_existing_return_files_project_without_resending_or_rewriting(returned_file_history):
    from loopx.capabilities.manager_context.roundtrip import read_chat_return_file

    root, registry, store, session, _, _, message, sources, sender = returned_file_history
    before = {path: path.read_bytes() for path in root.rglob("*.json")}
    messages = store.messages(session["session_id"])
    snapshot = project_chat_session_snapshot(root, store, session["session_id"], registry=registry)
    visible = next(row for row in snapshot["messages"] if row["message_id"] == message["message_id"])
    assert visible["text"] == message["text"]
    assert [row["name"] for row in visible["attachments"]] == list(sources)
    assert all(row["mime_type"] == "application/octet-stream" and row["data_url"].startswith("/api/chat/sessions/")
               for row in visible["attachments"])
    for file in visible["attachments"]:
        attachment, raw = read_chat_return_file(root, store, session["session_id"], message["message_id"],
            file["id"].removeprefix("result-file."), registry=registry)
        assert attachment["name"] == file["name"] and raw == sources[file["name"]]
    assert sender.calls == 1
    assert store.messages(session["session_id"]) == messages
    assert {path: path.read_bytes() for path in root.rglob("*.json")} == before


@pytest.fixture
def returned_file_http(returned_file_history):
    import http.client
    from types import SimpleNamespace
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler

    root, registry, store, session, *_ = returned_file_history
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.chat_store, server.verbose = store, False
    server.registry_path, server.runtime_root = registry, root
    server.runtime_controller = SimpleNamespace(coordination_runtime_root=root, close=lambda: None)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def get(path, headers=None):
        connection = http.client.HTTPConnection(*server.server_address, timeout=15)
        try:
            connection.request("GET", path, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    try:
        yield get
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_returned_file_http_preserves_bytes_and_filename(returned_file_history, returned_file_http):
    from urllib.parse import unquote

    root, registry, store, session, _, _, message, sources, _ = returned_file_history
    visible = project_chat_return_deliveries(root, session["session_id"], [message], source_store=store, registry=registry)[0]
    for file in visible["attachments"]:
        status, headers, raw = returned_file_http(file["data_url"])
        assert status == 200 and raw == sources[file["name"]]
        assert headers["Content-Type"] == "application/octet-stream"
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert unquote(headers["Content-Disposition"].split("''")[1]) == file["name"]


@pytest.mark.parametrize("drift", ["session", "message", "channel", "turn", "snapshot", "origin"])
def test_returned_file_http_rechecks_original_source_and_snapshot(returned_file_history, returned_file_http, drift):
    root, registry, store, session, _, receipt, message, _, _ = returned_file_history
    visible = project_chat_return_deliveries(root, session["session_id"], [message], source_store=store, registry=registry)[0]
    file = visible["attachments"][0]
    url = file["data_url"]
    if drift == "session":
        url = url.replace(session["session_id"], "another-session")
    elif drift == "message":
        url = url.replace(message["message_id"], "handoff." + "0" * 64)
    elif drift in {"channel", "turn"}:
        path = _root(root) / "roundtrips" / (receipt["request_id"] + ".json")
        route = json.loads(path.read_text())
        route["channel_id" if drift == "channel" else "client_turn_id"] = "another-source"
        _write(path, route)
    elif drift == "snapshot":
        (_root(root) / "result-files" / file["id"].removeprefix("result-file.").split(".")[0]).write_bytes(b"changed snapshot")
    status, _, raw = returned_file_http(url, {"Origin": "https://unrelated.example"} if drift == "origin" else None)
    assert status == (503 if drift == "snapshot" else 403 if drift == "origin" else 404)
    assert json.loads(raw)["ok"] is False


def test_download_attachment_does_not_expand_chat_input_admission(returned_file_history):
    from loopx.chat_attachments import normalize_chat_attachments

    root, registry, store, session, _, _, message, _, _ = returned_file_history
    visible = project_chat_return_deliveries(root, session["session_id"], [message], source_store=store, registry=registry)[0]
    with pytest.raises(ValueError):
        normalize_chat_attachments(visible["attachments"])


@pytest.mark.parametrize("plural", [False, True])
def test_history_delivery_reads_original_route_without_global_scan_and_stays_fresh(returned_history, monkeypatch, plural):
    root, _, store, session, turn, receipt = returned_history
    sid, rid = session["session_id"], receipt["request_id"]
    if plural:
        store.update_turn(sid, turn["turn_id"], response={"context_handoff_receipts": [receipt]})
    messages = store.messages(sid)
    followups = [message for message in messages if message.get("origin") == "manager_followup"]
    routes = _root(root) / "roundtrips"
    for index in range(10):
        _write(routes / f"{index:064x}.json", {"request_id": f"{index:064x}", "session_id": "unrelated"})
    original_glob = Path.glob

    def no_global_routes(path, pattern, **kwargs):
        assert path != routes, "A known original receipt must not enumerate all conversations"
        return original_glob(path, pattern, **kwargs)

    monkeypatch.setattr(Path, "glob", no_global_routes)
    before = {path: path.read_bytes() for path in root.rglob("*.json")}
    projected = project_chat_return_deliveries(root, sid, followups, source_store=store)
    assert len(projected) == len(followups) == 1
    assert projected[0]["return_delivery"]["status"] == "delivered"
    assert {path: path.read_bytes() for path in root.rglob("*.json")} == before
    # Delivery state remains current even when the original transcript is unchanged.
    _write(_root(root) / "replies" / rid / "conclusion.delivery.json", {
        "status": "verification_required", "error": "provider_delivery_unverified",
    })
    latest = project_chat_return_deliveries(root, sid, followups, source_store=store)
    assert latest[0]["return_delivery"]["status"] == "verification_required"
    assert latest[0]["return_delivery"]["error"] == "provider_delivery_unverified"
    assert store.messages(sid) == messages


@pytest.mark.parametrize("receipt_state", ["lost", "malformed", "unreadable_turn", "unmatched"])
def test_history_delivery_keeps_fallback_for_missing_original_locators(returned_history, monkeypatch, receipt_state):
    root, _, store, session, turn, receipt = returned_history
    sid = session["session_id"]
    if receipt_state == "unreadable_turn":
        (store.root / "sessions" / sid / "turns" / (turn["turn_id"] + ".json")).write_text("{")
    else:
        response = {"context_handoff_receipts": "unsupported"} if receipt_state == "malformed" else (
            {"context_handoff_receipts": [None, {"request_id": "../outside"}, {"request_id": "0" * 64}]}
            if receipt_state == "unmatched" else None
        )
        store.update_turn(sid, turn["turn_id"], response=response)
    messages = store.messages(sid)
    routes, scanned = _root(root) / "roundtrips", []
    original_glob = Path.glob

    def observe(path, pattern, **kwargs):
        if path == routes:
            scanned.append(path)
        return original_glob(path, pattern, **kwargs)

    monkeypatch.setattr(Path, "glob", observe)
    projected = project_chat_return_deliveries(root, sid, messages, source_store=store)
    assert len(scanned) == 1
    assert len(projected) == len(messages)
    returned = next(message for message in projected if message.get("origin") == "manager_followup")
    assert returned["return_delivery"] == {"schema_version": "manager_return_delivery_status_v0", **reply_status(root, receipt)[0]}
    assert store.messages(sid) == messages


@pytest.mark.parametrize("drift", ["session", "host", "request"])
def test_history_original_receipt_is_only_a_locator_not_route_authority(returned_history, drift):
    root, _, store, session, _, receipt = returned_history
    sid, rid = session["session_id"], receipt["request_id"]
    path = _root(root) / "roundtrips" / (rid + ".json")
    route = json.loads(path.read_text())
    if drift == "session":
        route["session_id"] = "another-session"
    elif drift == "host":
        route["source_chat_runtime_root"] = str(root / "another-host")
    else:
        route["request_id"] = "0" * 64
    _write(path, route)
    messages = store.messages(sid)
    assert project_chat_return_deliveries(root, sid, messages, source_store=store) == messages


def test_history_fallback_reads_only_visible_result_states(returned_history, monkeypatch):
    from loopx.control_plane.collaboration import inbox

    root, _, store, session, turn, receipt = returned_history
    sid, rid = session["session_id"], receipt["request_id"]
    # Lost locators and an unmatched legacy message must retain the exhaustive
    # fallback, without deserializing all other requests' complete answer bodies.
    store.update_turn(sid, turn["turn_id"], response=None)
    store.append_message(sid, role="agent", origin="manager_followup",
        message_id="handoff." + "0" * 64, text="Legacy result without a known receipt")
    replies = _root(root) / "replies"
    for index in range(30):
        other = f"{index:064x}"
        _write(_root(root) / "roundtrips" / f"{other}.json", {"request_id": other, "session_id": sid})
        for version in range(4):
            _write(replies / other / f"conclusion-{version:08}.json", {
                "phase": "conclusion", "created_at": "2026-09-14T00:00:00+00:00",
                "text": "A complete off-page answer. " * 1000,
            })
    expected = {"schema_version": "manager_return_delivery_status_v0", **reply_status(root, receipt)[0]}
    before = {path: path.read_bytes() for path in root.rglob("*.json")}
    original_read, reads = inbox._read, []

    def read(path):
        if path.parent.parent == replies:
            reads.append(path)
        return original_read(path)

    monkeypatch.setattr(inbox, "_read", read)
    messages = store.messages(sid)
    projected = project_chat_return_deliveries(root, sid, messages, source_store=store)
    returned = [message for message in projected if message.get("return_delivery")]
    assert len(returned) == 1 and returned[0]["return_delivery"] == expected
    assert set(reads) == {replies / rid / "conclusion.json", replies / rid / "conclusion.delivery.json"}
    assert {path: path.read_bytes() for path in root.rglob("*.json")} == before
    assert store.messages(sid) == messages


def test_paged_history_filters_result_io_and_rereads_late_state(frozen_private_reply, private_snapshot, monkeypatch):
    from loopx.control_plane.collaboration import inbox

    store, session, turn, *_ = frozen_private_reply
    root, sid, rid = store.root.parent, session["session_id"], "e" * 64
    replies = _root(root) / "replies" / rid
    _write(_root(root) / "roundtrips" / f"{rid}.json", {"request_id": rid, "session_id": sid})
    body = "Original full body. " * 1000
    keys = ["decision", "conclusion-00000001"]
    for index, key in enumerate(keys):
        _write(replies / f"{key}.json", {"created_at": "2026-09-14T00:00:00+00:00", "text": body})
        _write(replies / f"{key}.delivery.json", {"status": "delivered"})
        store.append_message(sid, role="agent", origin="manager_followup",
            message_id="handoff." + _hash([rid, key]), turn_id=turn["turn_id"], text=body)
        if index == 0:
            for filler in range(205):
                store.append_message(sid, role="agent", message_id=f"filler-{filler}", text=f"Earlier row {filler}")
    original_read, reads = inbox._read, []

    def read(path):
        if path.parent == replies:
            reads.append(path.name)
        return original_read(path)

    monkeypatch.setattr(inbox, "_read", read)
    first = private_snapshot(message_limit=200)
    assert set(reads) == {"decision.json", "decision.delivery.json"}
    assert next(message for message in first["messages"] if message.get("return_delivery"))["text"] == body
    _write(replies / "conclusion-00000001.delivery.json", {
        "status": "verification_required", "error": "provider_delivery_unverified",
    })
    reads.clear()
    tail = private_snapshot(message_limit=200, message_cursor=first["message_page"]["next_cursor"])
    returned = next(message for message in tail["messages"] if message.get("return_delivery"))
    assert returned["text"] == body
    assert returned["return_delivery"]["result_key"] == "conclusion-00000001"
    assert returned["return_delivery"]["status"] == "verification_required"
    assert set(reads) == {"conclusion-00000001.json", "conclusion-00000001.delivery.json"}
    (replies / "conclusion-00000001.delivery.json").write_text("{")
    fresh = private_snapshot(message_limit=200, message_cursor=first["message_page"]["next_cursor"])
    failed = next(message for message in fresh["messages"] if message.get("return_delivery"))
    assert failed["return_delivery"]["status"] == "explicit_unverified"
    assert failed["return_delivery"]["error"] == "delivery_state_unreadable"


def _read_history_with_visible_proof(read):
    rows, digests, cursor = [], [], None
    while True:
        payload = read(message_limit=200, **({"message_cursor": cursor} if cursor else {}))
        page = payload["message_page"]
        rows.extend(payload["messages"])
        digests.append(page["visible_page_sha256"])
        cursor = page["next_cursor"]
        if cursor is None:
            return rows, f"{len(rows)}:{''.join(digests)}"


def test_verified_history_suffix_reuses_short_last_page_and_stays_in_order(frozen_private_reply, private_snapshot):
    store, session, _, _, _, _, source_text, parent = frozen_private_reply
    sid = session["session_id"]
    for index in range(204):
        store.append_message(sid, role="agent", message_id=f"original-{index}", text="A full original body. " * 20)
    earlier, basis = _read_history_with_visible_proof(private_snapshot)
    assert earlier[0]["reply_context"]["source_text"] == source_text
    assert earlier[0]["reply_context"]["parent"]["content"] == parent

    for batch in (7, 3):
        for index in range(batch):
            store.append_message(sid, role="agent", text=f"Natural reply {index}. " * 180)
        suffix = private_snapshot(message_limit=200, known_history=basis)
        full, expected_basis = _read_history_with_visible_proof(private_snapshot)
        assert suffix["session"]["session_id"] == sid
        assert len(suffix["messages"]) == batch
        assert earlier + suffix["messages"] == full
        assert suffix["history_resume"]["base"] == basis
        assert suffix["history_resume"]["next"] == expected_basis
        earlier, basis = full, expected_basis
    empty = private_snapshot(message_limit=200, known_history=basis)
    assert empty["messages"] == []
    assert empty["history_resume"]["next"] == basis


def test_late_delivery_state_invalidates_visible_history_proof(frozen_private_reply, private_snapshot):
    store, session, turn, *_ = frozen_private_reply
    sid, rid = session["session_id"], "3" * 64
    root = _root(store.root.parent)
    reply = root / "replies" / rid / "conclusion.json"
    state = reply.with_name("conclusion.delivery.json")
    _write(root / "roundtrips" / f"{rid}.json", {"request_id": rid, "session_id": sid})
    _write(reply, {"created_at": "2026-09-14T00:00:00+00:00", "text": "Actual receiver result"})
    _write(state, {"status": "delivered"})
    store.append_message(sid, role="agent", origin="manager_followup",
        message_id="handoff." + _hash([rid, "conclusion"]), turn_id=turn["turn_id"], text="Actual receiver result")
    rows, basis = _read_history_with_visible_proof(private_snapshot)
    assert rows[-1]["return_delivery"]["status"] == "delivered"
    journal = store.sessions_root / sid / "messages.jsonl"
    before = journal.read_bytes()
    _write(state, {"status": "verification_required", "error": "provider_delivery_unverified"})
    assert private_snapshot(message_limit=200, known_history=basis) == {
        "ok": True, "schema_version": "loopx_chat_history_resume_v1", "status": "restart",
    }
    fresh, new_basis = _read_history_with_visible_proof(private_snapshot)
    assert new_basis != basis and fresh[-1]["return_delivery"]["status"] == "verification_required"
    assert journal.read_bytes() == before


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


def test_return_service_can_close_before_start(flow):
    root, registry, store, _ = flow
    service = ReturnService(root, registry, store, lambda *_: {"reply_verified": True})

    service.close()

    assert service.stop.is_set()
    assert not service.thread.is_alive()


def test_host_recovery_returns_the_original_result_once(flow, monkeypatch):
    from loopx.capabilities.manager_context import roundtrip
    root, registry, store, create = flow
    _, _, receipt = create(True)
    rid = receipt["request_id"]
    acknowledge(root, "research", "worker", rid, "no_change", "Reviewed")
    report(root, "research", "worker", rid, "conclusion", "Original result after host recovery.")
    original, attempts, sends = roundtrip.drain, [], []
    sent = threading.Event()
    provenance = roundtrip.recover_source_chat_provenance
    starts = []

    def discover(*args, **kwargs):
        starts.append(True)
        return provenance(*args, **kwargs)

    def failed_once(*args, **kwargs):
        attempts.append(True)
        if len(attempts) == 1:
            raise AssertionError("synthetic host failure")
        return original(*args, **kwargs)

    def transport(*_):
        sends.append(True)
        sent.set()
        return {"reply_verified": True, "idempotency_key": "sha256:synthetic-proof"}

    monkeypatch.setattr(roundtrip, "drain", failed_once)
    monkeypatch.setattr(roundtrip, "recover_source_chat_provenance", discover)
    service = ReturnService(root, registry, store, transport)
    service.start()
    service.start()
    try:
        assert sent.wait(10)
    finally:
        service.close()
    drain(root, registry, store, transport)
    assert sends == [True]
    assert starts == [True], "Step recovery must retain its scope instead of replaying startup discovery"
    state = json.loads((_root(root) / "replies" / rid / "conclusion.delivery.json").read_text())
    assert state["status"] == "delivered"


def test_host_recovery_retains_the_queued_source_probe_backoff(flow, monkeypatch):
    from loopx.capabilities.manager_context import execution
    root, registry, store, _ = flow
    original = execution.QueuedSourcePump
    created, probes, deadlines = [], [], []
    recovered = threading.Event()

    def make_pump(*args):
        pump = original(*args)
        tick = pump.tick
        created.append(pump)

        def fail_after_first_probe(**kwargs):
            tick(**kwargs)
            probes.append(True)
            deadlines.append(pump.next_probe)
            if len(probes) == 1:
                raise AssertionError("synthetic host failure after a real bounded probe")
            recovered.set()

        pump.tick = fail_after_first_probe
        return pump

    monkeypatch.setattr(execution, "QueuedSourcePump", make_pump)
    service = ReturnService(root, registry, store, lambda *_: pytest.fail("No send is authorized"))
    service.start()
    try:
        assert recovered.wait(10)
    finally:
        service.close()
    assert len(created) == 1 and deadlines[0] == deadlines[1]
    assert store.list_sessions() == [], "Host recovery cannot enroll a new Turn"


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
    assert reply["text"].startswith("The assessment is available.")
    assert reply["text"].endswith(f"协作回复 · worker · 委托 {rid[:8]}")
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
    assert sent[0].startswith("This request is deferred.")
    assert sent[0].endswith(f"协作回复 · worker · 委托 {receipt['request_id'][:8]}")
    assert "Private receiver rationale" not in json.dumps(sent)
    assert all(not row.get("collaboration") for row in snapshot["messages"])


@pytest.mark.parametrize("external", [False, True], ids=["app", "provider"])
@pytest.mark.parametrize("body,remaining", [
    ("修复已通过原请求的验收。\n\n- 结果保存在原项目。\n- 后续无需用户操作。", None),
    ("新方案未通过验收，已保留原方案。\n\n本地耗时降低不代表实际结果更好；费用未知。", None),
    ("源码修复已提交，安装后的表现还未核验。", {"summary": "仍需在隔离安装中验证原失败场景。", "resume_when": "取得相应环境后继续；不操作生产会话。"}),
    ("当前结果未知。缺少的测量不能算作零，也不能据此认定成功。", None),
    ("本次无法执行：请求超出当前授权。\n\n已有成果保留，未扩大受众。", None),
    ("正在等待原始验收证据，当前尚无可确认的结论。", {"summary": "还欠原始证据核验。", "resume_when": "原证据可读取后继续，无需用户重复提出请求。"}),
    ("目前只能确认部分结果；反例与未知项仍保留。\n\n" + "- 完整证据：测试通过，实际采用尚未验证；不能混为一谈。\n" * 180, None),
], ids=["success", "negative", "partial", "unknown", "refusal", "waiting", "long"])
def test_reader_return_keeps_complete_authored_answer_and_original_route(flow, external, body, remaining):
    root, registry, store, create = flow
    session, _, receipt = create(external=external)
    rid = receipt["request_id"]
    acknowledge(root, "research", "worker", rid, "adopt", "Read original requirements")
    report(root, "research", "worker", rid, "conclusion", body, remaining_work=remaining)
    path = _root(root) / "replies" / rid / "conclusion.json"
    original = path.read_bytes()
    saved = json.loads(original)
    assert saved["return_format_version"] == 2
    assert saved["text"] == body.strip()
    expected = body.strip()
    if remaining:
        assert saved["remaining_work"] == remaining
        expected += f"\n\n{remaining['summary']}\n\n{remaining['resume_when']}"
    expected += f"\n\n协作回复 · worker · 委托 {rid[:8]}"
    sent = []

    def sender(route, actual_session, _, payload):
        assert route["session_id"] == actual_session["session_id"] == session["session_id"]
        assert actual_session["channel_id"] == session["channel_id"]
        sent.append(payload)
        return {"ok": True, "reply_verified": True, "verification_performed": True}

    assert drain(root, registry, store, sender) == 1
    snapshot = project_chat_session_snapshot(root, store, session["session_id"], registry=registry)
    returned = [row for row in snapshot["messages"] if row.get("origin") == "manager_followup"]
    assert len(returned) == 1 and returned[0]["text"] == expected
    assert sent == ([expected] if external else [])
    assert path.read_bytes() == original
    assert reply_status(root, receipt)[0]["status"] == "delivered"
    assert drain(root, registry, store, sender) == 0
    report(root, "research", "worker", rid, "conclusion", body, remaining_work=remaining)
    assert path.read_bytes() == original
    assert len(list((store.root / "sessions" / session["session_id"] / "turns").glob("*.json"))) == 1


def test_legacy_return_replay_keeps_published_text_and_rejects_unknown_format(flow):
    root, registry, store, create = flow
    session, _, receipt = create()
    rid = receipt["request_id"]
    acknowledge(root, "research", "worker", rid, "adopt", "Read original request")
    body = "此前发表的完整结论，费用仍未知。"
    remaining = {"summary": "原验收仍未完成。", "resume_when": "有原始证据时继续。"}
    report(root, "research", "worker", rid, "conclusion", body, remaining_work=remaining)
    path = _root(root) / "replies" / rid / "conclusion.json"
    legacy = json.loads(path.read_text())
    legacy.pop("return_format_version")
    _write(path, legacy)
    original = path.read_bytes()
    report(root, "research", "worker", rid, "conclusion", body, remaining_work=remaining)
    assert path.read_bytes() == original
    assert drain(root, registry, store, lambda *_: pytest.fail("private message sent externally")) == 1
    returned = next(row for row in project_chat_session_snapshot(root, store, session["session_id"], registry=registry)["messages"]
                    if row.get("origin") == "manager_followup")
    assert returned["text"] == (f"协作回复 · worker · 委托 {rid[:8]}\n\n{body}\n\n"
                               f"仍需完成 / Remaining work: {remaining['summary']}\n继续条件 / Continue when: {remaining['resume_when']}")
    report(root, "research", "worker", rid, "conclusion", "后续新增事实。", update_id="later-fact")
    update = path.with_name("conclusion-00000001.json")
    corrupt = json.loads(update.read_text())
    corrupt["return_format_version"] = 999
    _write(update, corrupt)
    drain(root, registry, store, lambda *_: pytest.fail("unknown format sent"))
    assert len([row for row in store.messages(session["session_id"]) if row.get("origin") == "manager_followup"]) == 1


@pytest.mark.parametrize("external", [False, True], ids=["app", "provider"])
@pytest.mark.parametrize("symbol", ["证", "😀"], ids=["bmp", "non-bmp"])
def test_cli_return_budget_uses_the_published_format_without_losing_content(flow, capsys, external, symbol):
    from loopx.cli import main
    from loopx.capabilities.manager_context.inspection import ManagerInspection, TOOL_NAME

    root, registry, store, create = flow
    session, turn, receipt = create(external=external)
    rid = receipt["request_id"]
    acknowledge(root, "research", "worker", rid, "adopt", "Read the original request")
    remaining = {"summary": "实际采用尚未核验。", "resume_when": "取得原始证据后继续。"}
    suffix = f"\n\n{remaining['summary']}\n\n{remaining['resume_when']}"
    body = symbol * (20000 - len(suffix))
    argv = ["--registry", str(registry), "--runtime-root", str(root), "--format", "json",
            "manager-inbox", "report", "--goal-id", "research", "--agent-id", "worker",
            "--request-id", rid, "--phase", "conclusion", "--remaining-work", remaining["summary"],
            "--resume-when", remaining["resume_when"]]
    path = _root(root) / "replies" / rid / "conclusion.json"
    assert main([*argv, "--reply-text", body + symbol]) == 1
    assert "result budget" in json.loads(capsys.readouterr().out)["error"]
    assert not path.exists(), "An oversized result must not be committed or queued"
    assert main([*argv, "--reply-text", body]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "queued_for_original_conversation"
    original = path.read_bytes()
    saved = json.loads(original)
    assert saved["return_format_version"] == 2 and saved["text"] == body
    assert saved["remaining_work"] == remaining and len(body + suffix) == 20000
    expected = body + suffix + f"\n\n协作回复 · worker · 委托 {rid[:8]}"
    sent = []

    def sender(route, actual_session, actual_turn, payload):
        assert route["session_id"] == actual_session["session_id"] == session["session_id"]
        assert actual_turn["turn_id"] == turn["turn_id"]
        sent.append(payload)
        return {"ok": True, "reply_verified": True, "verification_performed": True}

    assert drain(root, registry, store, sender) == 1
    returned = [row for row in store.messages(session["session_id"]) if row.get("origin") == "manager_followup"]
    assert len(returned) == 1 and returned[0]["text"] == expected
    assert sent == ([expected] if external else [])
    assert reply_status(root, receipt)[0]["status"] == "delivered"
    tool = ManagerInspection(context={"goals": [{"goal_id": "research"}]}, registry_path=registry,
        runtime_root=root, owner_scope=not external, channel_id=session["channel_id"],
        scope_valid=lambda: True, record=lambda _: None)
    inspected = tool.read(TOOL_NAME, {"view": "handoffs", "request_id": rid})
    assert inspected["ok"] and not inspected["unknown"]
    assert inspected["rows"][0]["remaining_work"] == remaining
    assert inspected["rows"][0]["inbox_state"] == "awaiting_conclusion"
    assert main([*argv, "--reply-text", body]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert path.read_bytes() == original
    assert drain(root, registry, ChatSessionStore(root), sender) == 0
    assert len(list((store.root / "sessions" / session["session_id"] / "turns").glob("*.json"))) == 1
    # Both compact observation codecs must retain the persisted version. An
    # unknown renderer is not an unversioned result and cannot be replayed.
    _write(path, {**saved, "return_format_version": 37})
    corrupt = path.read_bytes()
    assert main([*argv, "--reply-text", body]) == 1
    assert "return format" in json.loads(capsys.readouterr().out)["error"]
    assert path.read_bytes() == corrupt
    assert tool.read(TOOL_NAME, {"view": "handoffs", "request_id": rid})["unknown"] is True


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


def test_exact_loop_payload_conflict_sets_terminal_state_without_retry(tmp_path):
    from loopx.control_plane.projects.registry_codec import source_session_registry_transaction

    registry = tmp_path / ".loopx" / "registry.json"
    with source_session_registry_transaction(registry, operation="test_exact_return", create=lambda: {
        "profile_id": "source_session_v1",
        "goals": [{"id": "research", "repo": str(tmp_path), "status": "active",
                   "goal_instance_id": "ginst_" + "a" * 32,
                   "coordination": {"registered_agents": ["worker"]}}],
        "session_bindings": [], "session_receipts": [], "lifetime_receipts": [],
    }) as transaction:
        transaction.commit(transaction.payload_copy())
    store = ChatSessionStore(tmp_path)
    session = store.create_session(goal_id="loopx-manager", agent_id="codex", adapter_kind="codex_app_server",
                                   upstream_thread_id="fixture", channel_id="manager")
    turn, _ = store.create_turn(session["session_id"], client_turn_id="original",
                                message="Review the evidence.", origin="web")
    receipt = deliver(tmp_path, registry, session=session, turn=turn,
                      request={"goal_id": "research", "agent_id": "worker"})
    store.update_turn(session["session_id"], turn["turn_id"], status="completing",
                       response={"context_handoff_receipt": receipt})
    store.finalize_managed_turn_completion(session["session_id"], turn["turn_id"])
    rid = receipt["request_id"]
    acknowledge(tmp_path, "research", "worker", rid, "adopt", "Checked", registry=registry)
    report(tmp_path, "research", "worker", rid, "conclusion", "Checked the evidence.", registry=registry)
    mid = "handoff." + _hash([rid, "conclusion"])
    store.append_message(session["session_id"], role="agent", text="An earlier, different conclusion.",
                         turn_id=turn["turn_id"], origin="manager_followup", message_id=mid)
    deliveries = []
    assert drain(tmp_path, registry, store, lambda *args: deliveries.append(args)) == 1
    assert deliveries == []
    state_path = _root(tmp_path) / "replies" / rid / "conclusion.delivery.json"
    state = json.loads(state_path.read_text())
    assert state["status"] == "explicit_unverified"
    assert state["error"] == "manager_return_payload_conflict"
    assert state["goal_ref"] == receipt["goal_ref"]
    assert drain(tmp_path, registry, ChatSessionStore(tmp_path), None) == 0
    assert json.loads(state_path.read_text()) == state


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
