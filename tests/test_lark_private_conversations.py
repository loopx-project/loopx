"""Public synthetic provider/native Core journeys, never private credentials."""
import json
import re
import sys
import time
import http.client
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from io import BytesIO
from types import SimpleNamespace

import pytest
from test_chat_ordinary_project import ordinary  # noqa: F401
from test_antigravity_steward_adapter import language_server  # noqa: F401

from loopx.capabilities.native_chat.conversation_bindings import ChatConversationBindings
from loopx.capabilities.native_chat.external_conversations import ChatExternalConversations
from loopx.extensions.lark.conversation_identity import identity_ref, lark_private_source, observe_lark_conversation_identity
from loopx.extensions.lark.private_conversations import LarkPrivateConversations
from loopx.extensions.lark.private_delivery_discovery import PrivateDeliveryDiscovery
from loopx.extensions.lark.goal_topic_runtime import poll_lark_goal_topic_profile_once


def test_app_steward_discovery_only_projects_the_bound_session(tmp_path, monkeypatch):
    from collections import Counter
    from loopx import chat_store
    from test_native_steward_private import finish, steward

    fixture = steward.__wrapped__(tmp_path)
    store, runtime, provider, transport, binding, capture, _ = next(fixture)
    try:
        event = provider.event("steward-app", "discovery-source", "Read the fixture workspace")
        assert transport.admit("steward-app", event)["status"] == "durably_accepted"
        request = transport.core.read_request(identity_ref(binding["provider_ref"], event["message_id"]))
        assert finish(runtime, request)["status"] == "completed"
        original_id = request["session_id"]
        other = store.create_session(goal_id="different-goal", agent_id="codex", adapter_kind="codex_app_server",
                                     upstream_thread_id="other-thread", upstream_mode="chat")
        bindings = transport.bindings
        bindings.select_app_steward(binding["binding_id"], expected_revision=bindings.read()["revision"])
        before = capture.read_bytes(), bindings.path.read_bytes(), store.messages(original_id)
        metadata, transcripts = Counter(), []
        read, revision = chat_store._read_json, store._event_revision

        def read_session(path):
            if path.name == "session.json":
                metadata[path.parent.name] += 1
            return read(path)

        def read_revision(path):
            if path.name == "messages.jsonl":
                transcripts.append(path.parent.name)
            return revision(path)

        with monkeypatch.context() as observations:
            observations.setattr(chat_store, "_read_json", read_session)
            observations.setattr(store, "_event_revision", read_revision)
            start = len(provider.calls)
            assert bindings.app_steward_target() == {"binding_id": binding["binding_id"], "session_id": original_id}
            assert sum("auth" in args and "--verify" in args for args in provider.calls[start:]) == 1
        assert metadata == {original_id: 3, other["session_id"]: 1}
        assert transcripts == []
        assert before == (capture.read_bytes(), bindings.path.read_bytes(), store.messages(original_id))

        # The alias's own context is unrelated, but its declared ID still
        # resolves to the same canonical bound record. Core must see both.
        alias_path = store._session_path(other["session_id"])
        alias_path.write_text(json.dumps({**other, "session_id": original_id}))
        with pytest.raises(ValueError, match="canonical bound Session identity is ambiguous"):
            bindings.app_steward_target()
    finally:
        runtime.close()
        fixture.close()


@pytest.mark.parametrize("outcome", ["adopted", "closed", "interrupted"])
def test_private_steward_holds_original_request_during_executor_adoption(tmp_path, monkeypatch, outcome):
    from test_native_steward_private import finish, steward

    fixture = steward.__wrapped__(tmp_path)
    store, runtime, provider, transport, binding, capture, _ = next(fixture)
    try:
        event = provider.event("steward-app", "steward-original", "Read the original conversation")
        transport.admit("steward-app", event)
        original_ref = identity_ref(binding["provider_ref"], event["message_id"])
        original = transport.core.read_request(original_ref)
        sid = original["session_id"]
        assert finish(runtime, original)["status"] == "completed"
        original_path = transport.root / f"{original_ref}.json"
        assert transport.reconcile_request(original_path) == 1
        original_delivery = original_path.read_bytes()

        session = store.load_session(sid)
        session["manager_executor_allocation"] = {
            "schema_version": 1, "selection_policy": "pinned" if outcome == "interrupted" else "preferred"}
        store._session_path(sid).write_text(json.dumps(session))
        bindings = transport.bindings
        adopted = bindings.read()
        stale = {**adopted, "bindings": [
            {**row, "executor_endpoint_id": "antigravity"} if row["binding_id"] == binding["binding_id"] else row
            for row in adopted["bindings"]]}
        bindings.path.write_text(json.dumps(stale))
        pending_event = provider.event("steward-app", "steward-awaiting-executor", "Continue in the same conversation")
        pending_ref = identity_ref(binding["provider_ref"], pending_event["message_id"])
        pending_path = transport.root / f"{pending_ref}.json"
        calls_before = capture.read_bytes()
        writes_before = list(provider.writes)

        def open_obsolete_executor(**kwargs):
            raise AssertionError(f"admission tried to open a replacement Session: {kwargs['agent_id']}")

        monkeypatch.setattr(runtime, "open_session", open_obsolete_executor)
        assert transport.admit("steward-app", pending_event)["status"] == "admission_deferred"
        held = transport.core.read_request(pending_ref)
        assert held["status"] == "prepared" and held["session_id"] == sid and held["turn_id"] is None
        assert held["response_code"] == "steward_executor_binding_pending"
        provider_checks = len(provider.calls)
        assert transport.reconcile_request(pending_path) == 0
        assert len(provider.calls) == provider_checks
        assert transport.core.read_request(pending_ref) == held
        assert capture.read_bytes() == calls_before and provider.writes == writes_before
        assert not store.queued_turns(sid)

        if outcome == "closed":
            assert runtime.close_session(sid)
        bindings.path.write_text(json.dumps({**adopted, "revision": adopted["revision"] + 1}))
        if outcome == "interrupted":
            def interrupt_admission_projection(*args, **kwargs):
                raise OSError("interrupted after Core admission")

            with monkeypatch.context() as interruption:
                interruption.setattr(transport, "_record_admission", interrupt_admission_projection)
                with pytest.raises(OSError, match="after Core admission"):
                    transport.reconcile_request(pending_path)
            assert transport.core.read_request(pending_ref)["status"] == "accepted"
            assert json.loads(pending_path.read_text())["status"] == "captured"
        transport.reconcile_request(pending_path)
        settled = transport.core.read_request(pending_ref)
        assert settled["session_id"] == sid and "response_code" not in settled
        if outcome == "closed":
            assert settled["status"] == "rejected" and settled["turn_id"] is None
            assert not store.turn_for_client(sid, f"external-{pending_ref}")
            assert [row["session_id"] for row in store.list_sessions()] == [sid]
        else:
            assert settled["status"] == "accepted"
            assert finish(runtime, settled)["status"] == "completed"
            transport.reconcile_request(pending_path)
            delivered = json.loads(pending_path.read_text())
            assert delivered["status"] == "delivered" and delivered["deliveries"]["terminal"]["verified"]
            assert store.turn_for_client(sid, f"external-{pending_ref}")["turn_id"] == settled["turn_id"]
            writes = list(provider.writes)
            transport.reconcile_request(pending_path)
            assert provider.writes == writes
            assert sum(json.loads(line)["method"] == "turn/start" for line in capture.read_text().splitlines()) == 2
        assert original_path.read_bytes() == original_delivery
    finally:
        fixture.close()


@pytest.mark.parametrize("change", ["executor", "scope", "revoked", "second_publication", "closed",
                                    "source_unavailable", "source_changed"])
def test_private_steward_admission_rechecks_same_owner_executor_publication(tmp_path, monkeypatch, change):
    from test_native_steward_private import finish, steward
    from loopx import chat_ingress
    from loopx.capabilities.native_chat import conversation_bindings
    from loopx.file_lock import exclusive_file_lock

    fixture = steward.__wrapped__(tmp_path)
    store, runtime, provider, transport, binding, capture, _ = next(fixture)
    try:
        def source_reads(message_id):
            return sum("+messages-mget" in args and message_id in args for args in provider.calls)

        original_event = provider.event("steward-app", "original-before-adoption", "Read the original conversation")
        assert transport.admit("steward-app", original_event)["status"] == "durably_accepted"
        assert source_reads(original_event["message_id"]) == 1
        original_ref = identity_ref(binding["provider_ref"], original_event["message_id"])
        original = transport.core.read_request(original_ref)
        assert finish(runtime, original)["status"] == "completed"
        original_path = transport.root / f"{original_ref}.json"
        assert transport.reconcile_request(original_path) == 1
        original_delivery = original_path.read_bytes()
        sid = original["session_id"]

        bindings = transport.bindings
        bindings.select_app_steward(binding["binding_id"], expected_revision=bindings.read()["revision"])
        adopted = bindings.read()
        stale = {**adopted, "bindings": [
            {**row, "executor_endpoint_id": "antigravity"} if row["binding_id"] == binding["binding_id"] else row
            for row in adopted["bindings"]]}
        bindings.path.write_text(json.dumps(stale))
        published = {**adopted, "revision": adopted["revision"] + 1}
        if change == "scope":
            published["bindings"] = [
                {**row, "goal_scope": "selected"} if row["binding_id"] == binding["binding_id"] else row
                for row in adopted["bindings"]]
        if change == "revoked":
            published = {**published, "app_steward_binding_id": None, "bindings": [
                row for row in adopted["bindings"] if row["binding_id"] != binding["binding_id"]]}
        observed_endpoints = []
        pending_event = provider.event("steward-app", "original-during-adoption", "Continue in this conversation")
        pending_id = pending_event["message_id"]
        pending_source = dict(provider.messages[pending_id])

        def publish_at_scope_fence(path, **kwargs):
            if path == bindings.path and kwargs.get("operation") == "configure_bound_delivery_scope":
                previous = next(row for row in bindings.read()["bindings"] if row["binding_id"] == binding["binding_id"])
                observed_endpoints.append(previous["executor_endpoint_id"])
                if len(observed_endpoints) == 1:
                    bindings.path.write_text(json.dumps(published))
                    if change == "source_unavailable":
                        provider.messages.pop(pending_id)
                    elif change == "source_changed":
                        provider.messages[pending_id]["content"] = "Edited after the first provider read"
                elif change == "second_publication":
                    bindings.path.write_text(json.dumps({**stale, "revision": adopted["revision"] + 2}))
                elif change == "closed":
                    assert runtime.close_session(sid)
            return exclusive_file_lock(path, **kwargs)

        monkeypatch.setattr(chat_ingress, "exclusive_file_lock", publish_at_scope_fence)
        monkeypatch.setattr(conversation_bindings, "exclusive_file_lock", publish_at_scope_fence)
        pending_ref = identity_ref(binding["provider_ref"], pending_id)
        pending_path = transport.root / f"{pending_ref}.json"
        calls_before = capture.read_bytes()
        writes_before = list(provider.writes)
        admission = transport.admit("steward-app", pending_event)
        request = transport.core.read_request(pending_ref)
        if change == "source_unavailable":
            assert admission["status"] == "source_verification_failed"
            assert request["status"] == "prepared" and request["session_id"] == sid and request["turn_id"] is None
            assert json.loads(pending_path.read_text())["status"] == "captured"
            assert source_reads(pending_id) == 2 and observed_endpoints == ["antigravity"]
            assert not store.turn_for_client(sid, f"external-{pending_ref}")
            assert capture.read_bytes() == calls_before and provider.writes == writes_before
            provider.messages[pending_id] = pending_source
            transport.reconcile_request(pending_path)
            request = transport.core.read_request(pending_ref)
        if change not in {"executor", "source_unavailable"}:
            assert admission["status"] == "command_rejected"
            assert request["status"] == "rejected" and request["turn_id"] is None
            assert not store.turn_for_client(sid, f"external-{pending_ref}")
            assert capture.read_bytes() == calls_before and provider.writes == writes_before
            if change == "source_changed":
                assert source_reads(pending_id) == 2
            assert observed_endpoints == (["antigravity", "codex"] if change in {"second_publication", "closed"} else ["antigravity"])
            if change == "closed":
                assert request["session_id"] == sid and "response_code" not in request
        else:
            if change == "executor":
                assert admission["status"] == "durably_accepted"
                assert source_reads(pending_id) == 2
            assert observed_endpoints == ["antigravity", "codex"]
            assert request["status"] == "accepted" and request["session_id"] == sid
            assert request["binding_id"] == binding["binding_id"] and request["source"] == original["source"]
            assert finish(runtime, request)["status"] == "completed"
            if json.loads(pending_path.read_text())["status"] != "delivered":
                assert transport.reconcile_request(pending_path) == 1
            delivered = json.loads(pending_path.read_text())
            assert delivered["status"] == "delivered" and delivered["deliveries"]["terminal"]["verified"]
            assert delivered["event"]["message_id"] == pending_event["message_id"]
            assert store.turn_for_client(sid, f"external-{pending_ref}")["turn_id"] == request["turn_id"]
            writes_after = list(provider.writes)
            assert transport.reconcile_request(pending_path) == 0 and provider.writes == writes_after
            assert sum(json.loads(line)["method"] == "turn/start" for line in capture.read_text().splitlines()) == 2
        assert [row["session_id"] for row in store.list_sessions()] == [sid]
        assert original_path.read_bytes() == original_delivery
    finally:
        fixture.close()


def test_historical_context_is_explicitly_bounded_and_never_decodes_unread_attachments():
    rows = [{"message_id": f"m-{i}", "created_at": f"2026-01-01T00:00:{i:02}+00:00",
        "role": "agent", "text": f"Complete original answer {i}"} for i in range(14)]
    rows[-1]["attachments"] = [{"data_url": "unread-image-bytes"}]
    def messages(sid):
        assert sid == "original-session"
        return rows
    reader = SimpleNamespace(controller=SimpleNamespace(store=SimpleNamespace(messages=messages)),
        read_request=lambda _: {"source_created_at": "2026-01-01T00:00:30+00:00"})
    view = ChatExternalConversations.history_answer_context(reader, {"session_id": "original-session"}, {"request_ref": "original"})
    assert view["earlier_messages_omitted"] and view["available_core_messages"] == 14
    assert len(view["messages"]) == 12 and view["messages"][-1]["text"] == rows[-1]["text"]
    assert view["messages"][-1]["attachments_unread"] and "unread-image-bytes" not in json.dumps(view)
    rows[-1]["text"] = "界" * 25_000
    view = ChatExternalConversations.history_answer_context(reader, {"session_id": "original-session"}, {"request_ref": "original"})
    assert view["messages"] == [] and "unavailable_reason" in view
    rows[-1]["created_at"] = "2026-01-01T00:00:13"
    with pytest.raises(ValueError, match="history time"):
        ChatExternalConversations.history_answer_context(reader, {"session_id": "original-session"}, {"request_ref": "original"})


@pytest.mark.parametrize("crash", [None, "before-queue", "after-queue", "stop", "revoked"])
def test_history_answer_uses_original_context_and_original_worker_corrects_legacy_review(tmp_path, monkeypatch, crash):
    """A later answer must not redefine an older 'those four' request."""
    from datetime import timedelta, timezone
    from pathlib import Path
    from test_native_steward_private import steward, finish
    from loopx import chat_store
    from loopx.capabilities.native_chat import external_conversations

    fixture = steward.__wrapped__(tmp_path)
    store, runtime, provider, transport, binding, capture, _ = next(fixture)
    peer = Path(runtime.codex_bin)
    peer.write_text(peer.read_text().replace('result = {"thread": {"id": "durable-thread"}}',
        'result = {"thread": {"id": "durable-thread"}, '
        '"activePermissionProfile": {"id": request["params"].get("permissions")}, '
        '"runtimeWorkspaceRoots": [request["params"]["cwd"]]}').replace(
        '    elif method == "turn/start":',
        '    elif method == "thread/turns/list":\n'
        '        result = {"data": [{"id": "original-native", "status": "completed"}], "nextCursor": None}\n'
        '    elif method == "thread/backgroundTerminals/list":\n        result = {"data": [], "nextCursor": None}\n'
        '    elif method == "turn/start":'))
    try:
        transport.admit("steward-app", provider.event("steward-app", "context-seed", "Explain the current work"))
        seed = transport.core.pending()[0]
        assert finish(runtime, seed)["status"] == "completed"
        transport.reconcile()
        stamp = datetime.now(timezone.utc)
        with monkeypatch.context() as clock:
            clock.setattr(chat_store, "utc_now", lambda: (stamp - timedelta(seconds=1)).isoformat())
            original = store.append_message(seed["session_id"], role="agent",
                text="The four pending mechanisms are lifecycle, receiver returns, delivery reconciliation, and native context.")
            clock.setattr(chat_store, "utc_now", lambda: (stamp + timedelta(seconds=1)).isoformat())
            store.append_message(seed["session_id"], role="agent", text="UNRELATED LATER FOUR: colors, fonts, spacing, icons")
        event = provider.event("steward-app", "original-context-followup", "Please implement those four.")
        event["create_time"] = str(int(stamp.timestamp() * 1000))
        provider.messages[event["message_id"]]["create_time"] = event["create_time"]
        class ReviewClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return stamp + timedelta(seconds=61)
        monkeypatch.setattr(external_conversations, "datetime", ReviewClock)
        before = len(capture.read_text().splitlines())
        assert transport.admit("steward-app", event, history_review=True)["status"] == "durably_accepted"
        ref = identity_ref(binding["provider_ref"], event["message_id"])
        first = transport.core.read_request(ref)
        result = finish(runtime, first)
        assert result["status"] == "completed", result
        wire = [json.loads(line) for line in capture.read_text().splitlines()[before:]]
        prompt = json.dumps([x for x in wire if x["method"] == "turn/start"])
        assert original["text"] in prompt, "The answer needs the original antecedent, not a later native review"
        assert "UNRELATED LATER FOUR" not in prompt
        assert transport.reconcile() == 1
        if crash is None:
            # A newer, fully answered legacy review must not starve the older
            # partial request. Neither delivery nor age makes it a candidate.
            complete_event = provider.event("steward-app", "complete-later-review", "A self-contained question")
            complete_event["create_time"] = str(int((stamp + timedelta(seconds=2)).timestamp() * 1000))
            provider.messages[complete_event["message_id"]]["create_time"] = complete_event["create_time"]
            assert transport.admit("steward-app", complete_event, history_review=True)["status"] == "durably_accepted"
            complete_ref = identity_ref(binding["provider_ref"], complete_event["message_id"])
            complete = transport.core.read_request(complete_ref)
            assert finish(runtime, complete)["status"] == "completed"
            assert transport.reconcile() == 1
            for path in [transport.core.root / f"{complete_ref}.json", transport.root / f"{complete_ref}.json"]:
                row = json.loads(path.read_text())
                row["context_recovery"].pop("context_version", None)
                path.write_text(json.dumps(row))
        # Seed the actual released legacy shape: a terminal, delivered,
        # action-free read-only review without original-context qualification.
        result["response"]["gate"] = {"kind": "evidence_gap", "summary": "Implementation unverified", "next_action": ""}
        store.update_turn(first["session_id"], first["turn_id"], response=result["response"])
        for path in [transport.core.root / f"{ref}.json", transport.root / f"{ref}.json"]:
            row = json.loads(path.read_text())
            row["context_recovery"].pop("context_version", None)
            path.write_text(json.dumps(row))
        old_turn = store._turn_path(first["session_id"], first["turn_id"]).read_bytes()
        before = len(capture.read_text().splitlines())
        if crash in {"stop", "revoked"}:
            if crash == "stop":
                transport.admit("steward-app", provider.event("steward-app", "stop-before-context-correction", "/stop"))
            else:
                transport.bindings.disconnect(binding["binding_id"], expected_revision=transport.bindings.read()["revision"])
            transport.observe_ingress()
            assert transport.core.read_request(ref)["turn_id"] == first["turn_id"]
            assert store._turn_path(first["session_id"], first["turn_id"]).read_bytes() == old_turn
            assert not any(json.loads(line)["method"] == "turn/start" for line in capture.read_text().splitlines()[before:])
            return
        if crash:
            enqueue = runtime.enqueue_turn
            def interrupted(**kwargs):
                if crash == "after-queue":
                    enqueue(**kwargs)
                monkeypatch.setattr(runtime, "enqueue_turn", enqueue)
                raise OSError("synthetic correction acknowledgement lost")
            monkeypatch.setattr(runtime, "enqueue_turn", interrupted)
            transport.observe_ingress()
            if crash == "after-queue":
                # Completion may race a lost queue acknowledgement. The
                # reserved child remains the same read-only work, even before
                # the source journal adopts its new Turn id.
                reserved = transport.core.read_request(ref)["context_recovery"]
                queued = store.turn_for_client(first["session_id"], reserved["client_turn_id"])
                assert finish(runtime, {"session_id": first["session_id"], "turn_id": queued["turn_id"]})["status"] == "completed"
        transport.observe_ingress()
        corrected = transport.core.read_request(ref)
        assert corrected["turn_id"] != first["turn_id"]
        assert corrected["context_recovery"]["previous_answer_turn_id"] == first["turn_id"]
        assert corrected["context_recovery"]["failed_turn_id"] is None
        assert corrected["context_recovery"]["version"] == first["context_recovery"]["version"] + 1
        assert re.fullmatch(r"external-[a-f0-9]{24}-recovery-[1-9][0-9]*-review-1", corrected["context_recovery"]["client_turn_id"])
        corrected_result = finish(runtime, corrected)
        assert corrected_result["status"] == "completed"
        assert corrected_result["response"]["gate"] is not None
        assert transport.reconcile() == 1
        record = json.loads((transport.root / f"{ref}.json").read_text())
        assert record["deliveries"]["terminal.recovery.history.answer.1"]["verified"]
        returned = record["deliveries"]["terminal.recovery.history.answer.2"]
        assert returned["verified"] and returned["text"].startswith("更正之前的补答：")
        wire = [json.loads(line) for line in capture.read_text().splitlines()[before:]]
        assert sum(x["method"] == "thread/start" for x in wire) == 1
        prompt = json.dumps([x for x in wire if x["method"] == "turn/start"])
        assert original["text"] in prompt and "UNRELATED LATER FOUR" not in prompt
        assert store._turn_path(first["session_id"], first["turn_id"]).read_bytes() == old_turn
        count = len(capture.read_text().splitlines())
        transport.observe_ingress()
        transport.reconcile()
        assert len(capture.read_text().splitlines()) == count
    finally:
        fixture.close()


@pytest.mark.parametrize("fault", [None, "original-worker", "formatted-time", "uncaptured-stop", "project-source", "busy", "stop", "before-session", "source-change", "queue-crash", "reservation-crash", "ordinary-capture", "background", "background-unavailable", "native-active", "revoked"])
def test_uncaptured_history_has_one_answer_only_review_not_a_business_replay(tmp_path, monkeypatch, fault):
    from datetime import timedelta, timezone
    from test_native_steward_private import steward, finish
    from loopx.capabilities.native_chat import external_conversations

    fixture = steward.__wrapped__(tmp_path)
    store, runtime, provider, transport, binding, capture, workspace = next(fixture)
    from pathlib import Path
    peer = Path(runtime.codex_bin)
    peer.write_text(peer.read_text().replace('result = {"thread": {"id": "durable-thread"}}',
        'result = {"thread": {"id": "durable-thread"}, '
        '"activePermissionProfile": {"id": request["params"].get("permissions")}, '
        '"runtimeWorkspaceRoots": [request["params"]["cwd"]]}').replace(
        '    elif method == "turn/start":',
        '    elif method == "thread/turns/list":\n'
        f'        result = {{"data": [{{"id": "original-native", "status": {("inProgress" if fault == "native-active" else "completed")!r}}}], "nextCursor": None}}\n'
        '    elif method == "thread/backgroundTerminals/list":\n        result = {"data": [], "nextCursor": None}\n'
        '    elif method == "turn/start":'))
    try:
        seed = provider.event("steward-app", "history-seed", "Explain this workspace")
        assert transport.admit("steward-app", seed)["status"] == "durably_accepted"
        first = transport.core.pending()[0]
        assert finish(runtime, first)["status"] == "completed"
        transport.reconcile()
        session = store.load_session(first["session_id"])
        stamp = datetime.now(timezone.utc)
        source_time = (datetime.fromisoformat(session["created_at"]) - timedelta(seconds=1)
                       if fault == "before-session" else stamp)
        event = provider.event("steward-app", "missing-original-question", "Explain the existing result; do not execute it again")
        event["create_time"] = str(int(source_time.timestamp() * 1000))
        provider.messages[event["message_id"]]["create_time"] = event["create_time"]
        if fault == "formatted-time":
            def formatted(args, cwd=None, timeout=None):
                result = provider(args, cwd, timeout)
                payload = json.loads(result["stdout"])
                if "+messages-mget" in args or "+chat-messages-list" in args:
                    for item in payload.get("data", {}).get("items", payload.get("data", {}).get("messages", [])):
                        item["create_time"] = datetime.fromtimestamp(int(item["create_time"]) / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M")
                elif "api" in args and "GET" in args:
                    payload.pop("code", None)
                    payload["ok"] = True
                return {**result, "stdout": json.dumps(payload)}
            monkeypatch.setattr(transport, "runner", formatted)
        clock = stamp + timedelta(seconds=61)
        class ReviewClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock
        monkeypatch.setattr(external_conversations, "datetime", ReviewClock)
        before = [json.loads(line) for line in capture.read_text().splitlines()]
        if fault == "project-source":
            project = provider.event("notes-app", "not-a-historical-steward", "Explain this workspace")
            assert transport.admit("notes-app", project, history_review=True)["status"] == "history_answer_held"
            assert len(transport.core.pending()) == 1
            return
        if fault == "busy":
            live = provider.event("steward-app", "live-before-history", "wait for interrupt")
            transport.admit("steward-app", live)
            active = next(item for item in transport.core.pending() if item["message"] == live["content"])
            deadline = time.monotonic() + 5
            while store.load_turn(active["session_id"], active["turn_id"])["status"] != "running":
                assert time.monotonic() < deadline
                time.sleep(.01)
            size = len(capture.read_text().splitlines())
            assert transport.admit("steward-app", event, history_review=True)["status"] == "history_answer_held"
            later = [json.loads(line) for line in capture.read_text().splitlines()[size:]]
            assert not any(item["method"] in {"thread/turns/list", "thread/backgroundTerminals/list"} for item in later)
            assert store.load_turn(active["session_id"], active["turn_id"])["status"] == "running"
            return
        if fault == "stop":
            transport.admit("steward-app", provider.event("steward-app", "history-stop", "/stop"))
        elif fault == "revoked":
            transport.bindings.disconnect(binding["binding_id"], expected_revision=transport.bindings.read()["revision"])
        elif fault in {"background", "background-unavailable"}:
            original_adapter = runtime.adapters[first["session_id"]]
            def background(_deadline):
                if fault == "background-unavailable":
                    raise RuntimeError("synthetic unavailable original inventory")
                return [{"itemId": "original-command", "processId": "original-process"}]
            monkeypatch.setattr(original_adapter.session, "_background_terminals", background)
        elif fault == "ordinary-capture":
            transport.admit("steward-app", event)
            row = next(row for row in transport.core.pending() if row["message"] == event["content"])
            assert finish(runtime, row)["status"] == "completed"
            before = [json.loads(line) for line in capture.read_text().splitlines()]
        elif fault == "source-change":
            read = transport._source_message
            def changed(*args, **kwargs):
                value = read(*args, **kwargs)
                provider.messages[event["message_id"]]["content"] = "Edited after the first verification"
                return value
            monkeypatch.setattr(transport, "_source_message", changed)
        elif fault in {"queue-crash", "reservation-crash"}:
            enqueue = runtime.enqueue_turn
            def committed_then_crashed(**kwargs):
                if fault == "queue-crash":
                    enqueue(**kwargs)
                monkeypatch.setattr(runtime, "enqueue_turn", enqueue)
                raise OSError("synthetic lost queue acknowledgement")
            monkeypatch.setattr(runtime, "enqueue_turn", committed_then_crashed)
        if fault in {"queue-crash", "reservation-crash"}:
            with pytest.raises(OSError, match="lost queue acknowledgement"):
                transport.admit("steward-app", event, history_review=True)
            pending = next(row for row in transport.core.pending() if row.get("capture_origin") == "provider_history")
            transport.core.recover(request_ref=pending["request_ref"])
            with pytest.raises(ValueError, match="original provider preflight"):
                transport.core.admit(binding_id=binding["binding_id"], source=pending["source"],
                    request_ref=pending["request_ref"], message=pending["message"])
            if fault == "reservation-crash":
                import loopx.chat_manager as manager
                monkeypatch.setattr(manager, "MANAGER_CONTEXT_VERSION", 23)
            transport.admit("steward-app", event, history_review=True)
        elif fault in {"original-worker", "formatted-time", "uncaptured-stop"}:
            from loopx.extensions.lark import private_ingress
            monkeypatch.setattr(private_ingress, "time", SimpleNamespace(time=lambda: clock.timestamp()))
            if fault == "uncaptured-stop":
                stop = provider.event("steward-app", "uncaptured-history-stop", "/stop")
                provider.messages[stop["message_id"]]["create_time"] = str(int((stamp + timedelta(seconds=1)).timestamp() * 1000))
            getattr(transport, "observe_ingress", transport.ingress.observe)()
            if fault == "uncaptured-stop":
                assert len(transport.core.pending()) == 1
                assert not any(row.get("capture_origin") == "provider_history" for row in transport.core.pending())
                return
            assert len(transport.core.pending()) == 2
        else:
            admitted = transport.admit("steward-app", event, history_review=True)
            if fault in {"stop", "before-session", "source-change", "ordinary-capture", "background", "background-unavailable", "native-active", "revoked"}:
                assert admitted["status"] != "durably_accepted"
                assert [json.loads(line) for line in capture.read_text().splitlines()
                        if json.loads(line)["method"] == "turn/start"] == [row for row in before if row["method"] == "turn/start"]
                if fault in {"background", "background-unavailable"}:
                    assert original_adapter.healthcheck() and runtime.adapters[first["session_id"]] is original_adapter
                return
            assert admitted["status"] == "durably_accepted"
        row = next(row for row in transport.core.pending() if row.get("capture_origin") == "provider_history")
        result = finish(runtime, row)
        assert result["status"] == "completed", result
        assert row["context_recovery"]["failed_turn_id"] is None
        assert row["context_recovery"]["native_observation"] is None
        assert row["session_id"] == first["session_id"]
        assert transport.reconcile() == 1
        saved = json.loads((transport.root / f"{row['request_ref']}.json").read_text())
        terminal = saved["deliveries"]["terminal.recovery.history.answer.1"]
        assert terminal["verified"] and terminal["text"] == result["response"]["message"]
        wire = [json.loads(line) for line in capture.read_text().splitlines()][len(before):]
        starts = [row for row in wire if row["method"] == "turn/start"]
        assert len(starts) == 1
        threads = [row["params"] for row in wire if row["method"] == "thread/start"]
        assert len(threads) == 1 and threads[0]["runtimeWorkspaceRoots"] == [str(workspace)]
        assert threads[0]["permissions"] == "loopx_steward_host_read"
        assert "past effects are unknown" in json.dumps(starts[0])
        assert "Do not repeat or execute business operations" in json.dumps(starts[0])
        assert not result["response"].get("proposals") and not result["response"].get("context_handoff")
        assert transport.reconcile() == 0
        assert not transport.core.context_recovery_candidate(row["request_ref"])
        if fault == "original-worker":
            getattr(transport, "observe_ingress", transport.ingress.observe)()
            assert len(transport.core.pending()) == 2
            assert sum(json.loads(line)["method"] == "turn/start" for line in capture.read_text().splitlines()) == 2
            followup = provider.event("steward-app", "normal-after-history", "A new independent question")
            assert transport.admit("steward-app", followup)["status"] == "durably_accepted"
            normal = next(item for item in transport.core.pending() if item["message"] == followup["content"])
            assert normal["session_id"] == first["session_id"] and not normal.get("context_recovery")
            assert finish(runtime, normal)["status"] == "completed"
            assert store.load_session(normal["session_id"])["answer_review"] is False
    finally:
        fixture.close()


@pytest.mark.parametrize("fault", [None, "admission-crash", "source-image", "source-caption", "saved-image", "extra-native-event", "revoked", "stop"])
def test_original_post_image_preflight_recovery_keeps_images_source_and_failed_turn(tmp_path, monkeypatch, fault):
    from test_native_steward_private import steward, finish
    from test_lark_private_images import image_runner
    from test_chat_image_attachments import PNG_DATA_URL
    from test_manager_preparation_recovery import advance_recovery_clock
    from loopx.chat_store import _read_json, _atomic_write_json
    from loopx.chat_manager import MANAGER_CONTEXT_VERSION
    import loopx.chat_runtime as runtime_module

    fixture = steward.__wrapped__(tmp_path)
    store, runtime, provider, transport, binding, capture, _ = next(fixture)
    transport.runner = image_runner(provider)
    original_start = runtime_module.start_attachment_turn

    def old_preflight(*_args, **_kwargs):
        raise ValueError("attachments currently require the Codex Agent endpoint")

    monkeypatch.setattr(runtime_module, "start_attachment_turn", old_preflight)
    try:
        event = provider.event("steward-app", "original-image-preflight", "Explain this image\n![Image](img_example)", kind="post")
        assert transport.admit("steward-app", event)["status"] == "durably_accepted"
        row = transport.core.pending()[0]
        failed = finish(runtime, row)
        assert failed["status"] == "failed" and failed["upstream_turn_id"] is None and failed["delta_count"] == 0
        assert not any(json.loads(line)["method"] == "turn/start" for line in capture.read_text().splitlines())
        transport.reconcile()
        path = transport.root / f"{row['request_ref']}.json"
        record = _read_json(path)
        assert record["deliveries"]["terminal"]["verified"]
        original_failure_bytes = store._turn_path(row["session_id"], row["turn_id"]).read_bytes()
        monkeypatch.setattr(runtime_module, "start_attachment_turn", original_start)
        advance_recovery_clock(monkeypatch)
        if fault in {"source-image", "source-caption"}:
            provider.messages[event["message_id"]]["content"] = ("Explain this image\n![Image](img_replaced)"
                if fault == "source-image" else "Different intent\n![Image](img_example)")
        elif fault == "saved-image":
            original = _read_json(transport.core.root / f"{row['request_ref']}.json")
            original["attachments"][0]["name"] = "a different frozen image"
            _atomic_write_json(transport.core.root / f"{row['request_ref']}.json", original)
        elif fault == "extra-native-event":
            events_after = store.events_after
            monkeypatch.setattr(store, "events_after", lambda sid, tid, cursor, **kw: events_after(sid, tid, cursor, **kw)
                + ([{"event_id": "unknown-effect", "kind": "turn.started", "payload": {}}] if tid == row["turn_id"] else []))
        elif fault == "revoked":
            transport.bindings.disconnect(binding["binding_id"], expected_revision=transport.bindings.read()["revision"])
        elif fault == "stop":
            transport.admit("steward-app", provider.event("steward-app", "stop-original-image", "/stop"))
        if fault in {None, "admission-crash"}:
            assert transport.core.context_recovery_candidate(row["request_ref"])
        if fault == "admission-crash":
            enqueue = runtime.enqueue_turn

            def committed_then_disconnected(**kwargs):
                enqueue(**kwargs)
                monkeypatch.setattr(runtime, "enqueue_turn", enqueue)
                raise OSError("synthetic crash after original queue admission")

            monkeypatch.setattr(runtime, "enqueue_turn", committed_then_disconnected)
            with pytest.raises(OSError, match="after original queue admission"):
                transport.reconcile_request(path)
        # Exercise the existing reply worker after its next scheduled visit;
        # discovery's monotonic backoff has separate real-pool coverage below.
        if fault == "revoked":
            with pytest.raises(ValueError, match="no longer authorized"):
                transport.reconcile_request(path)
        else:
            transport.reconcile_request(path)
        current = transport.core.read_request(row["request_ref"])
        if fault not in {None, "admission-crash"}:
            assert current["turn_id"] == row["turn_id"] and not current.get("context_recovery")
            assert not any(json.loads(line)["method"] == "turn/start" for line in capture.read_text().splitlines())
            return
        assert current["session_id"] == row["session_id"] and current["turn_id"] != row["turn_id"]
        assert current["context_recovery"]["preparation_attempt"] == 1
        continued = runtime.wait_for_turn(session_id=row["session_id"], turn_id=current["turn_id"], timeout_sec=15)
        assert continued["status"] == "completed" and continued["attachments"] == failed["attachments"]
        transport.reconcile()
        delivered = _read_json(path)
        terminal = delivered["deliveries"][f"terminal.recovery.{MANAGER_CONTEXT_VERSION}.preparation.1"]
        assert terminal["verified"] and terminal["text"] == continued["response"]["message"]
        actual = provider.messages[terminal["attempt"]["message_ref"]]
        body = json.loads(actual["body"]["content"])
        assert body["zh_cn"]["content"][0][0]["text"] == terminal["text"]
        assert actual["chat_id"] == event["chat_id"] and actual["sender"]["id"] == provider.profile_apps["steward-app"]
        assert delivered["deliveries"]["terminal"] == record["deliveries"]["terminal"]
        assert store._turn_path(row["session_id"], row["turn_id"]).read_bytes() == original_failure_bytes
        wire = [json.loads(line) for line in capture.read_text().splitlines()]
        model_calls = [call for call in wire if call["method"] == "turn/start"]
        assert len(model_calls) == 1
        assert any(item.get("type") == "image" and item["url"] == PNG_DATA_URL for item in model_calls[0]["params"]["input"])
        transport.reconcile()
        assert sum(json.loads(line)["method"] == "turn/start" for line in capture.read_text().splitlines()) == 1
        assert len([call for call in provider.calls if "+messages-resources-download" in call]) == 1
    finally:
        fixture.close()


@pytest.mark.parametrize("fault", [None, "stop", "source-changed", "later-source", "no-capacity", "native-tool",
    "stop-after-discovery", "source-changed-after-discovery", "later-source-after-discovery"])
def test_original_worker_continues_proven_unstarted_argon_quota_on_codex(tmp_path, language_server, monkeypatch, fault):  # noqa: F811
    from copy import deepcopy
    from datetime import timedelta
    from pathlib import Path
    from test_native_steward_private import steward
    from test_chat_manager_model_adoption import configure
    from loopx.chat_manager import manager_executor_allocation
    import loopx.chat_manager as manager_module
    import loopx.chat_runtime as runtime_module
    from loopx.capabilities.native_chat import external_conversations, antigravity_adapter

    monkeypatch.setenv("LOOPX_MANAGER_MODEL", "gemini-4-argon-high")
    monkeypatch.setenv("LOOPX_MANAGER_REASONING_EFFORT", "high")
    fixture = steward.__wrapped__(tmp_path)
    store, runtime, provider, transport, binding, capture, _ = next(fixture)
    try:
        binding = transport.bindings.configure(transport_ref="steward-app", project_ref=binding["project_ref"],
            executor_endpoint_id="antigravity", context_kind="steward")
        language_server.update(quota_error_indices=[0], quota_fraction=0)
        event = provider.event("steward-app", "quota-before-execution", "Explain this change")
        transport.admit("steward-app", event)
        ref = identity_ref(binding["provider_ref"], event["message_id"])
        original = transport.core.read_request(ref)
        failed = runtime.wait_for_turn(session_id=original["session_id"], turn_id=original["turn_id"], timeout_sec=15)
        assert failed["status"] == "failed" and failed["error_code"] == "antigravity_step_error"
        original_bytes = store._turn_path(original["session_id"], original["turn_id"]).read_bytes()
        original_events = store.events_after(original["session_id"], original["turn_id"], None)
        path = transport.root / f"{ref}.json"
        transport.reconcile_request(path)  # Preserve original failure delivery.
        configure(runtime.coordination_runtime_root, model="gemini-4-argon-high", effort="high",
            endpoint="antigravity", policy="flexible")
        capabilities = [{"agent_id": name, "available": True, "adapter_kind": kind} for name, kind in
            [("antigravity", "antigravity_ls"), ("codex", "codex_app_server")]]
        monkeypatch.setattr(runtime, "capabilities", lambda: capabilities)
        allocation = manager_executor_allocation(runtime, environ={}, endpoint_capabilities=[
            {**item, "available": item["agent_id"] == "codex"} for item in capabilities])
        fake = Path(runtime.codex_bin)
        fake.write_text(fake.read_text().replace('result = {"thread": {"id": "durable-thread"}}',
            'result = {"thread": {"id": "durable-thread"}, "model": "gpt-6.1-sol"}'))
        def route(*_):
            return None if fault == "no-capacity" else {"allocation": allocation,
                "blocked_primary": None, "observed_at": time.time()}
        monkeypatch.setattr(manager_module, "manager_session_quota_route", route)
        monkeypatch.setattr(runtime_module, "manager_session_quota_route", route)
        class Ahead(datetime):
            @classmethod
            def now(cls, tz=None):
                return super().now(tz) + timedelta(seconds=65)
        monkeypatch.setattr(external_conversations, "datetime", Ahead)
        update_session = store.update_session
        transitions = []
        def checked_update(session_id, **changes):
            if changes.get("quota_recovery"):
                before = store.load_session(session_id)
                without_proof = {key: value for key, value in changes.items() if key != "quota_recovery"}
                with pytest.raises(ValueError, match="executor transition"):
                    update_session(session_id, **without_proof)
                assert store.load_session(session_id) == before
                transitions.append(session_id)
            return update_session(session_id, **changes)
        monkeypatch.setattr(store, "update_session", checked_update)
        if fault in {None, "later-source-after-discovery"}:
            source_controls = transport.core.source_controls
            scans = []
            def counted_controls():
                scans.append(True)
                return source_controls()
            monkeypatch.setattr(transport.core, "source_controls", counted_controls)
            discovery = transport.core.context_recovery_observer()
            assert discovery(ref) and discovery(ref)
            assert len(scans) == 1  # One complete source-control view per discovery pass.
        if fault in {"stop-after-discovery", "source-changed-after-discovery"}:
            assert path in transport.pending_delivery_paths()
        if fault in {"stop", "stop-after-discovery"}:
            store.append_event(original["session_id"], original["turn_id"], kind="turn.interrupt_requested", payload={})
            original_events = store.events_after(original["session_id"], original["turn_id"], None)
        elif fault in {"source-changed", "source-changed-after-discovery"}:
            provider.messages[event["message_id"]]["content"] = "Changed original input"
        elif fault in {"later-source", "later-source-after-discovery"}:
            transport.admit("steward-app", provider.event("steward-app", "source-correction", "/status"))
            # A later ordinary source is observed without creating model work.
            later = deepcopy(original)
            later.update(request_ref="e" * 24, created_at=(datetime.now().astimezone() + timedelta(seconds=1)).isoformat())
            from loopx.chat_store import _atomic_write_json
            _atomic_write_json(transport.core.root / f"{later['request_ref']}.json", later)
            if fault == "later-source-after-discovery":
                before_discovery = len(scans)
                assert discovery(ref)  # Retained hint, never permission to dispatch.
                assert len(scans) == before_discovery
                transport.core.context_recovery_observer()(ref)
                assert len(scans) == before_discovery + 1  # A new pass observes the new source input.
        elif fault == "native-tool":
            post = antigravity_adapter._rpc_post
            def with_tool(*args, **kwargs):
                result = post(*args, **kwargs)
                if args[2] == "GetCascadeTrajectorySteps":
                    result["steps"][0]["runCommand"] = {"commandLine": "unknown effects"}
                return result
            monkeypatch.setattr(antigravity_adapter, "_rpc_post", with_tool)
        transport.reconcile_request(path)
        recovered = transport.core.read_request(ref)
        assert store._turn_path(original["session_id"], original["turn_id"]).read_bytes() == original_bytes
        assert store.events_after(original["session_id"], original["turn_id"], None) == original_events
        if fault:
            assert recovered["turn_id"] == original["turn_id"]
            assert not recovered.get("context_recovery")
        else:
            assert recovered["context_recovery"]["quota_executor"] == "codex"
            done = runtime.wait_for_turn(session_id=recovered["session_id"], turn_id=recovered["turn_id"], timeout_sec=15)
            assert done["status"] == "completed", done
            transport.reconcile_request(path)
            assert transport.core.read_request(ref)["delivery_verified"]
            session = store.load_session(recovered["session_id"])
            assert session["agent_id"] == "codex" and session["agent_model_id"] == "gpt-6.1-sol"
            assert transitions == [session["session_id"]]
            after = capture.read_bytes()
            transport.reconcile_request(path)
            assert capture.read_bytes() == after  # Original worker reentry cannot dispatch twice.
        assert language_server["sent_count"] == 1  # The quota-failed Argon business is never sent again.
    finally:
        runtime.close()
        fixture.close()


class Provider:
    def __init__(self):
        self.messages = {}
        self.calls = []
        self.writes = []
        self.topic_writes = []
        self.delivery_ids = {}
        self.lose_next_reply = False
        self.verify_replies = True
        self.reactions = {}
        self.reaction_creates = []
        self.fail_reaction_create = False
        self.fail_reaction_delete = False
        self.owner_ready = True
        self.bot_ready = True
        self.profile_apps = {profile: f"cli_{profile.replace('-', '_')}"
                             for profile in ["notes-app", "steward-app"]}

    def event(self, profile, name, text, kind="text"):
        event = {"schema_version": "lark_event_inbox_event_v0", "event_id": f"event_{name}",
            "message_id": f"om_{name}", "chat_id": f"oc_{profile.replace('-', '_')}", "chat_type": "p2p",
            "sender_type": "user", "sender_id": f"ou_{profile.replace('-', '_')}", "message_type": kind,
            "content": text}
        self.messages[event["message_id"]] = {"message_id": event["message_id"], "chat_id": event["chat_id"],
            "sender": {"id": event["sender_id"], "sender_type": "user"}, "msg_type": kind, "content": text,
            "create_time": str(int(time.time() * 1000))}
        return event

    def topic(self, name, text, *, root=None, chat="oc_community", sender="ou_member", addressed=True):
        event = self.event("notes-app", name, text)
        event.update(chat_id=chat, chat_type="group", sender_id=sender)
        if root:
            event.update(root_id=root, parent_id=root, thread_id=f"omt_{root}")
        self.messages[event["message_id"]].update(chat_id=chat,
            sender={"id": sender, "sender_type": "user"},
            **{key: event[key] for key in ["root_id", "parent_id", "thread_id"] if key in event})
        if not root and addressed:
            self.messages[event["message_id"]]["mentions"] = [{"name": "notes-app", "id": {"open_id": "ou_notes_app_bot"}}]
        return event

    def __call__(self, args, cwd=None, timeout=None):
        self.calls.append(list(args))
        if args[1:] == ["profile", "list"]:
            return {"returncode": 0, "stdout": json.dumps([
                {"name": profile, "appId": app_id}
                for profile, app_id in self.profile_apps.items()]), "stderr": ""}
        profile = args[args.index("--profile") + 1]
        if "auth" in args:
            data = {"ok": True, "appId": self.profile_apps[profile], "identities": {
                "bot": {"available": self.bot_ready, "verified": self.bot_ready, "appName": profile,
                        "openId": f"ou_{profile.replace('-', '_')}_bot"},
                "user": {"available": self.owner_ready, "verified": self.owner_ready, "openId": f"ou_{profile.replace('-', '_')}"}}}
        elif "+chat-list" in args:
            data = {"ok": True, "data": {"chats": [{"chat_id": chat, "name": title}
                for chat, title in [("oc_community", "Community trial"), ("oc_second", "Second trial")]]}}
        elif "+chat-messages-list" in args:
            # Exercise the existing ingress owner against actual synthetic
            # history, including its time windows and continuation tokens.
            chat = args[args.index("--chat-id") + 1]
            if chat != f"oc_{profile.replace('-', '_')}":
                raise ValueError("synthetic private history belongs to another profile")
            start = args[args.index("--start") + 1] if "--start" in args else None
            end = args[args.index("--end") + 1]
            order = args[args.index("--order") + 1]
            if order not in {"asc", "desc"}:
                raise ValueError("unsupported synthetic history order")
            lower = datetime.fromisoformat(start).timestamp() * 1000 if start else float("-inf")
            upper = datetime.fromisoformat(end).timestamp() * 1000
            query = [profile, chat, start, end, order]
            offset = 0
            if "--page-token" in args:
                cursor = json.loads(args[args.index("--page-token") + 1])
                if (not isinstance(cursor, list) or len(cursor) != 6 or cursor[:5] != query
                        or type(cursor[5]) is not int or cursor[5] < 0):
                    raise ValueError("synthetic history cursor does not match its source window")
                offset = cursor[5]
            size = int(args[args.index("--page-size") + 1])
            if not 1 <= size <= 50:
                raise ValueError("invalid synthetic history page size")
            rows = [dict(row) for row in tuple(self.messages.values())
                    if row["chat_id"] == chat and lower <= int(row["create_time"]) <= upper]
            rows.sort(key=lambda row: (int(row["create_time"]), row["message_id"]), reverse=order == "desc")
            page = rows[offset:offset + size]
            more = offset + size < len(rows)
            data = {"ok": True, "identity": "bot", "data": {"messages": page, "has_more": more,
                "page_token": json.dumps([*query, offset + size]) if more else None}}
        elif "api" in args and "GET" in args:
            path = args[args.index("GET") + 1]
            assert path.startswith("/open-apis/im/v1/messages/")
            ids = (json.loads(args[args.index("--params") + 1])["message_ids"]
                   if path.endswith("/mget") else [path.rsplit("/", 1)[-1]])
            messages = [dict(self.messages[ref]) for ref in ids]
            for message in messages:
                if message.get("msg_type") == "text" and "content" in message and "body" not in message:
                    message["body"] = {"content": json.dumps({"text": message.pop("content")})}
            data = {"code": 0, "data": {"items": messages}}
        elif "+messages-mget" in args:
            refs = args[args.index("--message-ids") + 1].split(",")
            messages = [self.messages[ref] if self.verify_replies or not ref.startswith("om_out_") else
                {**self.messages[ref], "body": {"content": json.dumps({"text": "not yet visible"})}} for ref in refs]
            data = {"ok": True, "data": {"items": messages}}
        elif "+messages-edit" in args:
            ref = args[args.index("--message-id") + 1]
            content = args[args.index("--content") + 1]
            assert self.messages[ref]["sender"]["id"] == self.profile_apps[profile]
            if "--dry-run" in args:
                data = {"ok": True, "api": [{"body": {"content": content, "msg_type": "post"}}]}
            else:
                self.messages[ref]["body"] = {"content": content}
                data = {"ok": True}
        elif "+messages-send" in args or "+messages-reply" in args:
            kind = "post" if "--content" in args else "text"
            content = args[args.index("--content") + 1] if kind == "post" else json.dumps({"text": args[args.index("--text") + 1]})
            text = json.loads(content)["zh_cn"]["content"][0][0]["text"] if kind == "post" else json.loads(content)["text"]
            if "--dry-run" in args:
                data = {"ok": True, "api": [{"body": {"content": content, "msg_type": kind}}]}
            else:
                delivery_id = (profile, args[args.index("--idempotency-key") + 1])
                if delivery_id in self.delivery_ids:
                    return {"returncode": 0, "stdout": json.dumps({"ok": True,
                        "data": {"message_id": self.delivery_ids[delivery_id]}}), "stderr": ""}
                ref = f"om_out_{len(self.writes)}"
                self.delivery_ids[delivery_id] = ref
                self.writes.append((profile, text))
                target = self.messages[args[args.index("--message-id") + 1]] if "+messages-reply" in args else None
                self.messages[ref] = {"message_id": ref, "msg_type": kind, "body": {"content": content},
                    "chat_id": target["chat_id"] if target else args[args.index("--chat-id") + 1],
                    "sender": {"id": self.profile_apps[profile], "sender_type": "app"},
                    "create_time": str(int(time.time() * 1000))}
                if target:
                    assert "--reply-in-thread" in args
                    root = target.get("root_id") or target["message_id"]
                    self.messages[ref].update(root_id=root, parent_id=target["message_id"], thread_id=f"omt_{root}")
                    self.topic_writes.append((target["chat_id"], root, text))
                if self.lose_next_reply:
                    self.lose_next_reply = False
                    raise OSError("synthetic lost provider response after write")
                data = {"ok": True, "data": {"message_id": ref}}
        elif "reactions" in args:
            ref = args[args.index("--message-id") + 1]
            if "create" in args:
                if self.fail_reaction_create:
                    return {"returncode": 1, "stdout": '{"ok":false}'}
                emoji = json.loads(args[args.index("--data") + 1])["reaction_type"]["emoji_type"]
                reaction = f"reaction_{len(self.reaction_creates)}"
                self.reaction_creates.append((profile, ref, emoji))
                self.reactions[reaction] = (profile, ref, emoji)
                data = {"ok": True, "data": {"reaction_id": reaction}}
            elif "delete" in args:
                if self.fail_reaction_delete:
                    return {"returncode": 1, "stdout": '{"ok":false}'}
                reaction = args[args.index("--reaction-id") + 1]
                assert self.reactions[reaction][:2] == (profile, ref)
                self.reactions.pop(reaction)
                data = {"ok": True}
            else:
                data = {"ok": True, "data": {"items": [{"reaction_id": key} for key, row in self.reactions.items()
                                                        if row[:2] == (profile, ref)], "has_more": False}}
        else:
            pytest.fail(f"unnecessary provider operation: {args[2:5]}")
        return {"returncode": 0, "stdout": json.dumps(data), "stderr": ""}


def test_synthetic_private_history_preserves_window_pages_and_audience():
    provider = Provider()
    for profile in ("notes-app", "steward-app"):
        for index in range(5):
            event = provider.event(profile, f"{profile}-{index}", f"source {index}")
            provider.messages[event["message_id"]]["create_time"] = str((100 + index) * 1000)
    args = ["lark-cli", "--profile", "steward-app", "im", "+chat-messages-list", "--as", "bot",
        "--chat-id", "oc_steward_app", "--start", "1970-01-01T00:01:41+00:00",
        "--end", "1970-01-01T00:01:43+00:00", "--order", "asc", "--page-size", "2"]
    first = json.loads(provider(args)["stdout"])["data"]
    assert [row["message_id"] for row in first["messages"]] == ["om_steward-app-1", "om_steward-app-2"]
    assert first["has_more"]
    second = json.loads(provider([*args, "--page-token", first["page_token"]])["stdout"])["data"]
    assert [row["message_id"] for row in second["messages"]] == ["om_steward-app-3"]
    assert not second["has_more"] and second["page_token"] is None
    wrong_window = list(args)
    wrong_window[wrong_window.index("--end") + 1] = "1970-01-01T00:01:44+00:00"
    with pytest.raises(ValueError, match="cursor does not match"):
        provider([*wrong_window, "--page-token", first["page_token"]])
    foreign = list(args)
    foreign[foreign.index("--chat-id") + 1] = "oc_notes_app"
    with pytest.raises(ValueError, match="another profile"):
        provider(foreign)


def connect(fixture):
    store, runtime, contexts, _, _, _, _ = fixture
    provider = Provider()
    bindings = ChatConversationBindings(root=store.root, project_contexts=contexts,
        observe=lambda profile: observe_lark_conversation_identity(profile=profile, runner=provider, cli_bin="lark-cli"),
        observe_group=lambda profile: observe_lark_conversation_identity(profile=profile, runner=provider, cli_bin="lark-cli", audience="group"))
    contexts.conversation_bindings = bindings
    for profile in ["notes-app", "steward-app"]:
        bindings.configure(transport_ref=profile, project_ref=contexts.available()[0]["project_ref"], executor_endpoint_id="codex")
    return store, runtime, provider, LarkPrivateConversations(controller=runtime, runtime_root=store.root.parent,
                                                             runner=provider, cli_bin="lark-cli")


@pytest.fixture
def source_https(monkeypatch):
    from loopx.extensions import public_source_reader

    requests, resolutions = [], []

    def resolve(host):
        resolutions.append(host)
        return ["8.8.8.8"]

    def serve(response_for):
        class Socket:
            def settimeout(self, value):
                pass

            def makefile(self, *args):
                return BytesIO(self.wire_response)

        class Connection:
            def __init__(self, host, address, timeout):
                self.host = host
                self.sock = Socket()

            def request(self, method, path, *, headers):
                assert method == "GET"
                assert set(headers) == {"Accept-Encoding", "User-Agent"}
                url = f"https://{self.host}{path}"
                requests.append(url)
                redirect, mime, body = response_for(url)
                if redirect:
                    self.sock.wire_response = f"HTTP/1.1 302 Found\r\nLocation: {redirect}\r\nContent-Length: 0\r\n\r\n".encode()
                else:
                    headers = f"HTTP/1.1 200 OK\r\nContent-Type: {mime}\r\nContent-Length: {len(body)}\r\n\r\n"
                    self.sock.wire_response = headers.encode() + body

            def getresponse(self):
                response = http.client.HTTPResponse(self.sock)
                response.begin()
                return response

            def close(self):
                pass

        monkeypatch.setattr(public_source_reader, "public_addresses", resolve)
        monkeypatch.setattr(public_source_reader, "_PinnedHTTPS", Connection)
        return requests, resolutions

    return serve


@pytest.mark.parametrize("fault", ["none", "foreign_source", "unlisted_image", "changed_request",
    "corrupt_readback", "legacy_cached_readback", "upload_denied", "listed_redirect", "same_host_redirect",
    "foreign_redirect", "foreign_round_trip", "query_redirect", "article_redirect"])
def test_private_completed_answer_returns_image_bytes_instead_of_a_remote_placeholder(ordinary, source_https, fault):  # noqa: F811
    from test_chat_image_attachments import PNG_BYTES
    from test_lark_app_image_projection import ImageIO

    store, runtime, provider, transport = connect(ordinary)
    capture, fake = ordinary[4:6]
    url = ("https://unrelated.example" if fault == "foreign_source" else "https://source.example") + "/chart.png"
    authored = f'Original explanation.\n\n![Source chart](<{url}> "Figure 6")'
    script = fake.read_text()
    default = '"message": "Runtime response.",'
    assert default in script
    fake.write_text(script.replace(default,
        f'"message": {authored!r} if turn_count == 1 else "Next original reply.",', 1))
    article_url = "https://source.example/article"
    redirect_targets = {
        "listed_redirect": "/canonical.png",
        "same_host_redirect": "/unlisted.png",
        "foreign_redirect": "https://unrelated.example/foreign.png",
        "foreign_round_trip": "https://unrelated.example/foreign.png",
        "query_redirect": "/chart.png?private=fixture",
    }

    def respond(target):
        if target == article_url and fault == "article_redirect":
            return "/other-article", None, b""
        if target in (article_url, "https://source.example/other-article"):
            basename = "different-chart.png" if fault == "unlisted_image" else "chart.png"
            listings = f'<img src="/{basename}">'
            if fault == "listed_redirect":
                listings += '<img src="/canonical.png">'
            return None, "text/html", listings.encode()
        if target == url and fault in redirect_targets:
            return redirect_targets[fault], None, b""
        if target == "https://unrelated.example/foreign.png" and fault == "foreign_round_trip":
            return "https://source.example/unlisted.png", None, b""
        return None, "image/png", PNG_BYTES

    fetched, resolutions = source_https(respond)
    images_delivered = fault in ("none", "listed_redirect")
    io = ImageIO(provider)
    io.corrupt_download = fault in ("corrupt_readback", "legacy_cached_readback")
    def media(args, cwd=None, timeout=None):
        if fault == "upload_denied" and "images" in args and "create" in args and "--dry-run" not in args:
            return {"returncode": 3, "stderr": '{"ok":false,"error":{"code":99991672}}'}
        return io(args, cwd, timeout)

    transport.runner = media
    try:
        source = provider.event("notes-app", "answer-with-original-image", "Read https://source.example/article")
        transport.admit("notes-app", source)
        original = transport.core.pending()[0]
        sid, tid, ref = (original[key] for key in ("session_id", "turn_id", "request_ref"))
        completed = runtime.wait_for_turn(session_id=sid, turn_id=tid, timeout_sec=10)
        assert completed["status"] == "completed"
        if fault == "changed_request":
            provider.messages[source["message_id"]]["content"] = "Source URL removed."
        path = transport.root / f"{ref}.json"
        assert path in transport.pending_body_paths()
        assert transport.reconcile_request(path) == 0
        text_only = json.loads(path.read_text())
        assert text_only["deliveries"]["terminal"]["verified"] is True
        assert transport.core.read_request(ref).get("delivery_verified", False) is False
        assert fetched == [] and io.uploads == 0
        assert path not in transport.pending_body_paths()
        assert path in transport.pending_delivery_paths() and transport.is_recovery_path(path)
        assert transport.reconcile_request(path) == int(images_delivered)
        saved = json.loads(path.read_text())
        assert saved["answer_images"]["status"] == ("delivered" if images_delivered else "unverified")
        out = [(mid, row, json.loads(row["body"]["content"])["zh_cn"]["content"])
               for mid, row in provider.messages.items() if mid.startswith("om_out_") and row["msg_type"] == "post"]
        assert all(row["chat_id"] == source["chat_id"] for _, row, _ in out)
        assert any("[Source chart](" in nodes[0][0]["text"] for _, _, nodes in out)
        assert all(not any("![Source chart]" in node.get("text", "") for line in nodes for node in line) for _, _, nodes in out)
        assert transport.core.read_request(ref).get("delivery_verified", False) is images_delivered
        native_images = [node["image_key"] for _, _, nodes in out for line in nodes for node in line if node["tag"] == "img"]
        assert native_images == list(io.resources)
        assert all(io.resources[k] == PNG_BYTES for k in native_images)
        expected_requests = {
            "foreign_source": [], "changed_request": [], "unlisted_image": [article_url],
            "article_redirect": [article_url], "listed_redirect": [article_url, url, "https://source.example/canonical.png"],
        }.get(fault, [article_url, url])
        assert fetched == expected_requests
        assert resolutions == ["source.example"] * len(expected_requests)
        if fault in {"same_host_redirect", "foreign_redirect", "foreign_round_trip", "query_redirect", "article_redirect"}:
            assert io.uploads == 0 and native_images == []
        assert ("图片尚未核验送达" in str(out)) is not images_delivered
        if fault in ("corrupt_readback", "legacy_cached_readback"):
            io.corrupt_download = False
            from loopx.chat_store import _atomic_write_json
            restarted = LarkPrivateConversations(controller=runtime, runtime_root=transport.runtime_root,
                                                  runner=media, cli_bin="lark-cli")
            writes = list(provider.writes)
            assert path not in restarted.pending_body_paths()
            assert path not in restarted.pending_delivery_paths()
            assert restarted.reconcile_request(path) == 0
            assert provider.writes == writes and io.uploads == 1 and fetched == [article_url, url]
            saved["answer_images"]["next_check_at"] = 0
            if fault == "legacy_cached_readback":
                saved["answer_images"].pop("source_urls_verified")
            _atomic_write_json(path, saved)
            assert path in restarted.pending_delivery_paths()
            assert restarted.reconcile_request(path) == 1
            assert transport.core.read_request(ref)["delivery_verified"] is True
            assert provider.writes == writes and io.uploads == 1
            assert fetched == [article_url, url] * (2 if fault == "legacy_cached_readback" else 1)
        expected_turns = 1
        if fault == "upload_denied":
            followup = provider.event("notes-app", "text-after-image-denial", "Answer the next question")
            retry = transport.admit("notes-app", followup)
            assert retry["status"] == "durably_accepted"
            later_ref = identity_ref(transport._binding("notes-app")["provider_ref"], followup["message_id"])
            later = transport.core.read_request(later_ref)
            assert later["session_id"] == sid
            assert runtime.wait_for_turn(session_id=sid, turn_id=later["turn_id"], timeout_sec=10)["status"] == "completed"
            later_path = transport.root / f"{later_ref}.json"
            assert path not in transport.pending_body_paths() and path not in transport.pending_delivery_paths()
            assert later_path in transport.pending_body_paths()
            assert transport.reconcile_request(later_path) == 1
            assert transport.core.read_request(later_ref)["delivery_verified"] is True
            assert transport.core.read_request(ref).get("delivery_verified", False) is False
            expected_turns = 2
        packets = [json.loads(line) for line in capture.read_text().splitlines()]
        assert sum(packet.get("method") == "turn/start" for packet in packets) == expected_turns
        assert any(row["text"].endswith(authored) for row in store.messages(sid) if row["role"] == "agent")
    finally:
        runtime.close()


@pytest.mark.parametrize("fault", ["readback", "edited_source", "edited_original_reply", "edit_on_image_get",
    "edit_on_send_preview", "foreign_sender", "changed_bytes", "stop", "unknown_send", "upload_denied",
    "unlisted_redirect", "unlisted_cached_redirect"])
def test_original_worker_queues_only_media_for_a_verified_legacy_answer(ordinary, monkeypatch, source_https, fault):  # noqa: F811
    from hashlib import sha256
    from loopx.chat_store import _atomic_write_json
    from test_chat_image_attachments import PNG_BYTES
    from test_lark_app_image_projection import ImageIO

    store, runtime, provider, transport = connect(ordinary)
    fake = ordinary[5]
    origin = "https://source.example/article"
    urls = [f"https://source.example/figure-{i}.png" for i in range(1, 5)]
    figures = {url: PNG_BYTES + bytes([i]) for i, url in enumerate(urls)}
    answer = f"Original explanation.\n\n![One]({urls[0]})\n\n![Two]({urls[1]})"
    script = fake.read_text()
    marker = '"message": "Runtime response.",'
    assert marker in script
    fake.write_text(script.replace(marker, f'"message": {answer!r},', 1))
    io = ImageIO(provider)
    edited_body = None

    def edit_original_reply():
        nonlocal edited_body
        edited_body = {"content": json.dumps({"zh_cn": {"content": [[{"tag": "text", "text": "The original answer was edited."}]]}})}
        provider.messages[prior_id]["body"] = edited_body

    def provider_io(args, cwd=None, timeout=None):
        if fault == "upload_denied" and "images" in args and "create" in args and "--dry-run" not in args:
            provider.calls.append(list(args))
            return {"returncode": 3, "stderr": '{"ok":false,"error":{"type":"authorization","code":99991672}}'}
        result = io(args, cwd, timeout)
        if fault == "edit_on_send_preview" and "+messages-send" in args and "--dry-run" in args and "--content" in args:
            if "原图（按原文顺序）" in args[args.index("--content") + 1]:
                edit_original_reply()
        return result

    transport.runner = provider_io

    unlisted = "https://source.example/unlisted.png"

    def respond(url):
        if url == origin:
            return None, "text/html", "".join(f'<img src="{url}">' for url in urls).encode()
        if fault == "edit_on_image_get":
            edit_original_reply()
        if fault in ("unlisted_redirect", "unlisted_cached_redirect") and url == urls[-1]:
            return "/unlisted.png", None, b""
        return None, "image/png", figures[urls[-1] if url == unlisted else url]

    fetched, resolutions = source_https(respond)

    def restart():
        return LarkPrivateConversations(controller=runtime, runtime_root=transport.runtime_root,
                                       runner=provider_io, cli_bin="lark-cli")

    try:
        event = provider.event("notes-app", "historical-image-answer", f"Read {origin}")
        assert transport.admit("notes-app", event)["status"] == "durably_accepted"
        original = transport.core.pending()[0]
        sid, tid, ref = (original[key] for key in ("session_id", "turn_id", "request_ref"))
        assert runtime.wait_for_turn(session_id=sid, turn_id=tid, timeout_sec=10)["status"] == "completed"
        path = transport.root / f"{ref}.json"
        # Produce an actual legacy reply and provider receipt before installing
        # the media-only repair. The stored answer and Core are real.
        with monkeypatch.context() as legacy:
            legacy.setattr(transport, "_deliver_completed_answer", lambda p, row, text, *, inbox:
                           transport._deliver(p, row, "terminal", text, inbox=inbox))
            assert transport.reconcile_request(path) == 1
        saved = json.loads(path.read_text())
        terminal = saved["deliveries"]["terminal"]
        prior_id = terminal["attempt"]["message_ref"]
        prior_body = json.dumps(provider.messages[prior_id]["body"])
        core = (transport.core.read_request(ref), store.load_turn(sid, tid), store.messages(sid))
        writes = len(provider.writes)
        worker = restart()
        assert path not in worker.pending_delivery_paths()
        assert worker.reconcile_request(path) == 0 and io.uploads == 0

        plan = {"binding_id": saved["binding_id"], "turn_id": tid, "message_id": prior_id,
                "answer_sha256": sha256(answer.encode()).hexdigest(),
                "images": [{"url": url, "sha256": sha256(figures[url]).hexdigest()} for url in urls]}
        manifest = worker.root / "historical-answer-images" / path.name
        _atomic_write_json(manifest, plan)
        if fault == "unlisted_cached_redirect":
            import base64
            from loopx.chat_attachments import normalize_chat_image_attachments

            legacy_attachments = normalize_chat_image_attachments([
                {"id": f"reply-image-{index}", "name": f"original-{index}.png", "mime_type": "image/png",
                 "data_url": "data:image/png;base64," + base64.b64encode(data).decode(), "size": len(data)}
                for index, data in enumerate(figures.values(), 1)])
            saved["historical_answer_images"] = {"plan": plan, "status": "pending", "attachments": legacy_attachments}
            _atomic_write_json(path, saved)
        assert path not in worker.pending_body_paths()
        assert path in worker.pending_delivery_paths() and worker.is_recovery_path(path)
        if fault == "edited_source":
            provider.messages[event["message_id"]]["content"] = "Changed source."
        elif fault == "edited_original_reply":
            edit_original_reply()
        elif fault == "foreign_sender":
            provider.messages[prior_id]["sender"]["id"] = provider.profile_apps["steward-app"]
        elif fault == "changed_bytes":
            figures[urls[-1]] = PNG_BYTES
        elif fault == "stop":
            # The authoritative Stop predates any possible file recovery.
            control = {**core[0], "command": "stop"}
            _atomic_write_json(transport.core.root / ("d" * 24 + ".json"), control)
        elif fault == "readback":
            io.corrupt_download = True
        elif fault == "unknown_send":
            provider.lose_next_reply = True

        assert worker.reconcile_request(path) == 0
        if fault in {"readback", "unknown_send"}:
            pending = json.loads(path.read_text())
            assert pending["historical_answer_images"]["status"] == "pending"
            assert io.uploads == 4 and len(provider.writes) == writes + 1
            worker = restart()
            assert path not in worker.pending_delivery_paths()
            pending["historical_answer_images"]["next_check_at"] = 0
            _atomic_write_json(path, pending)
            io.corrupt_download = False
            assert path in worker.pending_delivery_paths()
            assert worker.reconcile_request(path) == int(fault == "readback")
            assert io.uploads == 4 and len(provider.writes) == writes + 1
            row = json.loads(path.read_text())
            assert row["historical_answer_images"]["status"] == ("delivered" if fault == "readback" else "pending")
            outputs = [m for m in provider.messages.values() if m["msg_type"] == "post"
                       and "原图（按原文顺序）" in m["body"]["content"]]
            assert len(outputs) == 1
            post = outputs[0]
            assert post["chat_id"] == event["chat_id"] and post["sender"]["id"] == provider.profile_apps["notes-app"]
            lines = json.loads(post["body"]["content"])["zh_cn"]["content"]
            keys = [node["image_key"] for line in lines for node in line if node["tag"] == "img"]
            assert len(keys) == 4 and [io.resources[key] for key in keys] == list(figures.values())
            assert worker.reconcile_request(path) == 0
        else:
            assert io.uploads == (4 if fault == "edit_on_send_preview" else 0) and len(provider.writes) == writes
            if fault in ("unlisted_redirect", "unlisted_cached_redirect"):
                assert fetched == [origin, *urls] and resolutions == ["source.example"] * len(fetched)
                assert unlisted not in fetched
            if fault == "upload_denied":
                pending = json.loads(path.read_text())
                assert pending["historical_answer_images"]["status"] == "pending"
                assert "answer_images.historical" not in pending["deliveries"]
                assert any("images" in cmd and "create" in cmd and "--dry-run" not in cmd for cmd in provider.calls)
        assert provider.messages[prior_id]["body"] == (edited_body if edited_body is not None else json.loads(prior_body))
        assert json.loads(path.read_text())["deliveries"]["terminal"] == terminal
        assert core == (transport.core.read_request(ref), store.load_turn(sid, tid), store.messages(sid))
        assert sum(json.loads(line).get("method") == "turn/start" for line in ordinary[4].read_text().splitlines()) == 1
    finally:
        runtime.close()


def test_private_delivery_keeps_retired_steward_cards_out_of_image_recovery(
    ordinary,  # noqa: F811
):
    from loopx.chat_store import _atomic_write_json

    store, runtime, provider, transport = connect(ordinary)
    bindings = transport.bindings
    workspace = runtime.project_contexts.available()[0]["project_ref"]
    binding = bindings.configure(transport_ref="steward-app", project_ref=workspace,
                                 executor_endpoint_id="codex", context_kind="steward")
    try:
        event = provider.event("steward-app", "retired-card-before-image-repair", "/triage")
        assert transport.admit("steward-app", event)["status"] == "triage_queued"
        ref = identity_ref(binding["provider_ref"], event["message_id"])
        card = transport.root / f"{ref}.json"
        assert card in transport.pending_body_paths()
        assert card in transport.pending_delivery_paths()
        assert transport.health()[binding["binding_id"]]["pending_count"] == 1

        provider.messages[event["message_id"]]["content"] = "/status"
        assert transport.reconcile_request(card) == 0
        retired = json.loads(card.read_text())
        assert retired["triage"]["retired"] == "original_source_changed"

        # Even an orphaned descriptor with this filename cannot turn an
        # upstream transport-only record into image or model recovery work.
        _atomic_write_json(transport.root / "historical-answer-images" / card.name, {})
        worker = LarkPrivateConversations(controller=runtime, runtime_root=transport.runtime_root,
                                          runner=provider, cli_bin="lark-cli")
        assert card not in worker.pending_body_paths()
        assert card not in worker.pending_delivery_paths()
        assert worker.health()[binding["binding_id"]]["pending_count"] == 0
        assert not (worker.core.root / card.name).exists()

        assert worker.reconcile_request(card) == 0
        assert json.loads(card.read_text()) == retired
        assert not store.list_sessions()
        assert not provider.writes
    finally:
        runtime.close()


@pytest.mark.parametrize("case", ["normal", "invalid-metadata", "metadata-only", "unfinished", "source-changed", "closed", "lost-ack", "message-crash",
    "historical", "historical-source-changed", "historical-stop"])
def test_original_worker_recovers_native_result_without_replay(tmp_path, language_server, monkeypatch, case):  # noqa: F811
    from test_native_steward_private import steward
    from loopx.capabilities.native_chat.antigravity_adapter import AntigravityAdapter
    from loopx.chat_agent import CodexChatAgentError
    from loopx.chat_store import _read_json

    monkeypatch.setenv("LOOPX_MANAGER_MODEL", "gemini-4-argon-high")
    monkeypatch.setenv("LOOPX_MANAGER_REASONING_EFFORT", "high")
    fixture = steward.__wrapped__(tmp_path)
    store, runtime, provider, transport, binding, _, workspace = next(fixture)
    historical = case.startswith("historical")
    fault = case.removeprefix("historical-") if case != "historical" else "normal"
    try:
        binding = transport.bindings.configure(transport_ref="steward-app", project_ref=binding["project_ref"],
            executor_endpoint_id="antigravity", context_kind="steward")
        original = AntigravityAdapter.start_turn
        language_server["invalid_handoff"] = case in {"invalid-metadata", "metadata-only"}
        language_server["unfinished"] = case == "unfinished"
        def lost_core_terminal(adapter, message, event_sink):
            result = original(adapter, message, event_sink)
            if case == "metadata-only":
                return result
            raise CodexChatAgentError("Synthetic lost Core terminal commit", gate=None, error_code="antigravity_rpc_error")
        monkeypatch.setattr(AntigravityAdapter, "start_turn", lost_core_terminal)
        event = provider.event("steward-app", "native-original", "Complete the original request")
        transport.admit("steward-app", event)
        ref = identity_ref(binding["provider_ref"], event["message_id"])
        row = transport.core.read_request(ref)
        failed = runtime.wait_for_turn(session_id=row["session_id"], turn_id=row["turn_id"], timeout_sec=15)
        if case == "metadata-only":
            assert failed["status"] == "completed" and failed["response"]["gate"]["kind"] == "response_metadata_invalid"
            assert transport.reconcile() == 1
            assert transport.health()[row["binding_id"]]["pending_count"] == 1
            assert "Argon answer 1" in provider.writes[-1][1]
            assert "相关操作和委派尚未提交" in provider.writes[-1][1]
            assert transport.core.context_recovery_candidate(ref)
            plan = transport.core._context_recovery_plan(transport.core.read_request(ref), discovery_only=True)
            assert plan["metadata_review"] and not plan.get("recover_native_result")  # Format only, no business replay.
            assert language_server["sent_count"] == 1
            return
        assert failed["status"] == "failed" and failed["error_code"] == "antigravity_rpc_error"
        assert transport.reconcile_request(transport.root / f"{ref}.json") == 1
        path = transport.root / f"{ref}.json"
        old = _read_json(path)
        if case not in {"source-changed", "closed"}:
            assert transport.core.context_recovery_candidate(ref)
        if historical:
            monkeypatch.setattr(AntigravityAdapter, "start_turn", original)
            later = provider.event("steward-app", "later-separate-question", "An unrelated later question")
            transport.admit("steward-app", later)
            later_row = transport.core.read_request(identity_ref(binding["provider_ref"], later["message_id"]))
            assert runtime.wait_for_turn(session_id=later_row["session_id"], turn_id=later_row["turn_id"], timeout_sec=15)["status"] == "completed"
            transport.reconcile_request(transport.root / f"{later_row['request_ref']}.json")
        failed_bytes = store._turn_path(row["session_id"], row["turn_id"]).read_bytes()
        writes = len(provider.writes)
        monkeypatch.setattr(runtime, "enqueue_turn", lambda **_: pytest.fail("Result recovery enqueued work"))
        monkeypatch.setattr(runtime, "resume_session_queue", lambda **_: pytest.fail("Result recovery resumed work"))
        if fault == "source-changed":
            provider.messages[event["message_id"]]["content"] = "Edited source"
        elif fault == "stop":
            store.append_event(row["session_id"], row["turn_id"], kind="turn.interrupt_requested", payload={})
        elif case == "closed":
            store.update_session(row["session_id"], status="closed")
        elif case == "lost-ack":
            provider.lose_next_reply = True
        elif case == "message-crash":
            append = store.append_message
            def crash(*args, **kwargs):
                if str(kwargs.get("message_id", "")).startswith("native."):
                    raise OSError("Synthetic journal/message crash")
                return append(*args, **kwargs)
            monkeypatch.setattr(store, "append_message", crash)
            with pytest.raises(OSError, match="journal/message crash"):
                transport.reconcile()
            monkeypatch.setattr(store, "append_message", append)
            monkeypatch.setattr(ChatExternalConversations, "_native_result_text",
                                staticmethod(lambda _: pytest.fail("Frozen message intent was regenerated")))
        if case == "lost-ack":
            # The original intent owner retains/reconciles an uncertain write.
            try:
                transport.reconcile()
            except OSError:
                pass
        restarted = LarkPrivateConversations(controller=runtime, runtime_root=transport.runtime_root,
            runner=provider, cli_bin="lark-cli")
        restarted.reconcile()
        native = restarted.core.read_request(ref)
        recovered = native.get("native_result_recovery")
        if fault in {"source-changed", "closed", "stop"}:
            assert recovered is None and len(provider.writes) == writes
        elif case == "lost-ack":
            assert recovered["delivery_verified"] is False
            receipt = _read_json(path)["deliveries"][f"native_result.{row['turn_id']}"]
            assert receipt["started"] and not receipt.get("attempt")
            assert receipt["blocker"] == "provider_delivery_receipt_unknown"
            assert len(provider.writes) == writes + 1
            restarted.reconcile()
            assert len(provider.writes) == writes + 1  # Unknown delivery remains owed.
            assert restarted.health()[row["binding_id"]]["pending_count"] == 1
        else:
            assert recovered["delivery_verified"] is True
            message = next(item for item in store.messages(row["session_id"]) if item["message_id"] == recovered["message_id"])
            receipt = _read_json(path)["deliveries"][f"native_result.{row['turn_id']}"]
            body = json.loads(provider.messages[receipt["attempt"]["message_ref"]]["body"]["content"])
            assert body["zh_cn"]["content"][0][0]["text"] == message["text"]
            assert "Argon answer 1" in message["text"]
            if historical:
                assert "历史答复" in message["text"] and "没有重新执行业务" in message["text"]
                assert "Argon answer 2" not in message["text"]
            assert len(provider.writes) == writes + 1
            assert restarted.health()[row["binding_id"]]["pending_count"] == (case in {"invalid-metadata", "unfinished"})
            if case == "unfinished":
                assert "Original work still owed" in message["text"]
            assert len([item for item in store.messages(row["session_id"]) if item["message_id"] == recovered["message_id"]]) == 1
        assert _read_json(path)["deliveries"]["terminal"] == old["deliveries"]["terminal"]
        assert store._turn_path(row["session_id"], row["turn_id"]).read_bytes() == failed_bytes
        assert len(list(store._turn_path(row["session_id"], row["turn_id"]).parent.glob("*.json"))) == (2 if historical else 1)
        assert len([1 for method, _ in language_server["calls"] if method == "SendUserCascadeMessage"]) == (2 if historical else 1)
    finally:
        fixture.close()


@pytest.mark.parametrize("case", ["normal", "source-changed", "closed", "lost-ack", "message-crash", "non-prefix", "action-change"])
def test_original_queue_recovers_historical_truncated_answer_without_execution(tmp_path, language_server, monkeypatch, case):  # noqa: F811
    from test_native_steward_private import steward
    from loopx.capabilities.native_chat.antigravity_adapter import AntigravityAdapter
    from loopx.chat_store import _read_json

    monkeypatch.setenv("LOOPX_MANAGER_MODEL", "gemini-4-argon-high")
    monkeypatch.setenv("LOOPX_MANAGER_REASONING_EFFORT", "high")
    fixture = steward.__wrapped__(tmp_path)
    store, runtime, provider, transport, binding, *_ = next(fixture)
    try:
        binding = transport.bindings.configure(transport_ref="steward-app", project_ref=binding["project_ref"],
            executor_endpoint_id="antigravity", context_kind="steward")
        complete = "Original `<loopx-review-json>` is a literal marker. The complete original answer."
        native_response = {"message": "Different original answer" if case == "non-prefix" else complete,
            "proposals": [], "gate": None}
        if case == "action-change":
            native_response["unfinished_work"] = {"remaining": "More work", "dependency": "Worker", "effects_settled": True}
        language_server.update(plain_text=True, answers={0: '<loopx-review-json>' + json.dumps(native_response) + '</loopx-review-json>'})
        start = AntigravityAdapter.start_turn

        def old_parser(adapter, message, event_sink):
            # This legacy service had neither the fixed parser nor durable
            # native result capture. Keep exercising its live-history path.
            response = start(adapter, message, lambda kind, payload:
                event_sink(kind, payload) if kind != "turn.native_result" else None)
            return {**response, "message": "Original `"}

        monkeypatch.setattr(AntigravityAdapter, "start_turn", old_parser)
        event = provider.event("steward-app", "historical-original", "Complete original request")
        transport.admit("steward-app", event)
        ref = identity_ref(binding["provider_ref"], event["message_id"])
        row = transport.core.read_request(ref)
        assert runtime.wait_for_turn(session_id=row["session_id"], turn_id=row["turn_id"], timeout_sec=15)["status"] == "completed"
        assert transport.reconcile_request(transport.root / f"{ref}.json") == 1
        monkeypatch.setattr(AntigravityAdapter, "start_turn", start)
        later = provider.event("steward-app", "historical-later", "A separate later request")
        transport.admit("steward-app", later)
        later_ref = identity_ref(binding["provider_ref"], later["message_id"])
        later_row = transport.core.read_request(later_ref)
        assert runtime.wait_for_turn(session_id=later_row["session_id"], turn_id=later_row["turn_id"], timeout_sec=15)["status"] == "completed"
        assert later_row["session_id"] == row["session_id"]
        assert transport.reconcile_request(transport.root / f"{later_ref}.json") == 1
        path = transport.root / f"{ref}.json"
        old = _read_json(path)
        turn_bytes = store._turn_path(row["session_id"], row["turn_id"]).read_bytes()
        event_bytes = store._event_path(row["session_id"], row["turn_id"]).read_bytes()
        writes = len(provider.writes)
        calls = [item for item in language_server["calls"] if item[0] in {"StartCascade", "SendUserCascadeMessage", "CancelCascadeInvocation"}]
        monkeypatch.setattr(runtime, "enqueue_turn", lambda **_: pytest.fail("Result recovery enqueued work"))
        monkeypatch.setattr(runtime, "resume_session_queue", lambda **_: pytest.fail("Result recovery resumed work"))
        monkeypatch.setattr(AntigravityAdapter, "start_turn", lambda *_, **__: pytest.fail("Result recovery started model work"))
        if case == "source-changed":
            provider.messages[event["message_id"]]["content"] = "Edited source"
        elif case == "closed":
            store.update_session(row["session_id"], status="closed")
        elif case == "lost-ack":
            provider.lose_next_reply = True
        elif case == "message-crash":
            append = store.append_message

            def crash(*args, **kwargs):
                if str(kwargs.get("message_id", "")).startswith("native."):
                    raise OSError("Synthetic recovery message crash")
                return append(*args, **kwargs)

            monkeypatch.setattr(store, "append_message", crash)
            with pytest.raises(OSError, match="recovery message crash"):
                transport.reconcile()
            monkeypatch.setattr(store, "append_message", append)
            monkeypatch.setattr(ChatExternalConversations, "_native_result_text",
                                staticmethod(lambda _: pytest.fail("Frozen message was regenerated")))
        if case == "lost-ack":
            try:
                transport.reconcile()
            except OSError:
                pass
        restarted = LarkPrivateConversations(controller=runtime, runtime_root=transport.runtime_root,
            runner=provider, cli_bin="lark-cli")
        restarted.reconcile()  # The ordinary original queue discovers the historical result.
        recovered = restarted.core.read_request(ref).get("native_result_recovery")
        if case in {"source-changed", "closed", "non-prefix", "action-change"}:
            assert recovered is None and len(provider.writes) == writes
        else:
            assert recovered["native_observation"]["latest_input_verified"] is False
            assert recovered["native_observation"]["end_step_index"] == 2
            assert recovered["native_observation"]["total_steps"] == 4
            assert recovered["native_observation"]["response"]["message"] == complete
            assert recovered["delivery_verified"] is (case != "lost-ack")
            assert len(provider.writes) == writes + 1
            receipt = _read_json(path)["deliveries"][f"native_result.{row['turn_id']}"]
            if case == "lost-ack":
                assert receipt["blocker"] == "provider_delivery_receipt_unknown"
                assert restarted.health()[row["binding_id"]]["pending_count"] == 1
            else:
                message = next(item for item in store.messages(row["session_id"]) if item["message_id"] == recovered["message_id"])
                body = json.loads(provider.messages[receipt["attempt"]["message_ref"]]["body"]["content"])
                assert body["zh_cn"]["content"][0][0]["text"] == message["text"]
                assert complete in message["text"] and "Argon answer 2" not in message["text"]
            restarted.reconcile()
            assert len(provider.writes) == writes + 1
        assert _read_json(path)["deliveries"]["terminal"] == old["deliveries"]["terminal"]
        assert store._turn_path(row["session_id"], row["turn_id"]).read_bytes() == turn_bytes
        assert store._event_path(row["session_id"], row["turn_id"]).read_bytes() == event_bytes
        assert len(list(store._turn_path(row["session_id"], row["turn_id"]).parent.glob("*.json"))) == 2
        assert [item for item in language_server["calls"] if item[0] in {"StartCascade", "SendUserCascadeMessage", "CancelCascadeInvocation"}] == calls
    finally:
        fixture.close()


@pytest.mark.parametrize("case", ["normal", "provider-unavailable", "closed", "source-changed", "wrong-native-turn", "wrong-anchor",
                                  "mixed-execution", "failed-core", "lost-ack"])
def test_original_queue_retains_completed_answer_across_native_cleanup(tmp_path, language_server, monkeypatch, case):  # noqa: F811 - imported pytest fixture
    from test_native_steward_private import steward
    from loopx.capabilities.native_chat.antigravity_adapter import AntigravityAdapter
    from loopx.chat_agent import CodexChatAgentError
    from loopx.chat_store import _read_json

    monkeypatch.setenv("LOOPX_MANAGER_MODEL", "gemini-4-argon-high")
    monkeypatch.setenv("LOOPX_MANAGER_REASONING_EFFORT", "high")
    fixture = steward.__wrapped__(tmp_path)
    store, runtime, provider, transport, binding, *_ = next(fixture)
    try:
        binding = transport.bindings.configure(transport_ref="steward-app", project_ref=binding["project_ref"],
            executor_endpoint_id="antigravity", context_kind="steward")
        complete = "Original `<loopx-review-json>` is literal.\n\n" + "完整原答复包含中文和🙂。\n" * 200 + "Original final paragraph."
        language_server.update(plain_text=True, answers={0: '<loopx-review-json>' + json.dumps({
            "message": complete, "proposals": [], "gate": None}) + '</loopx-review-json>'})
        start = AntigravityAdapter.start_turn

        def damaged_response(adapter, message, event_sink):
            response = start(adapter, message, event_sink)
            if case == "failed-core":
                raise CodexChatAgentError("Synthetic post-processing failure", gate=None, error_code="upstream_transport_failure")
            return {**response, "message": "Original `"}

        monkeypatch.setattr(AntigravityAdapter, "start_turn", damaged_response)
        event = provider.event("steward-app", "retained-original", "Complete original request")
        transport.admit("steward-app", event)
        ref = identity_ref(binding["provider_ref"], event["message_id"])
        row = transport.core.read_request(ref)
        assert runtime.wait_for_turn(session_id=row["session_id"], turn_id=row["turn_id"], timeout_sec=15)["status"] == (
            "failed" if case == "failed-core" else "completed")
        assert transport.reconcile_request(transport.root / f"{ref}.json") == 1
        retained = store.native_result(row["session_id"], row["turn_id"])
        result_path = store._session_dir(row["session_id"]) / "native-results" / f"{row['turn_id']}.json"
        assert result_path.stat().st_mode & 0o777 == 0o600
        assert retained["steps"][-1]["plannerResponse"]["response"].endswith('</loopx-review-json>')
        assert all("thinking" not in step.get("plannerResponse", {}) and "runCommand" not in step for step in retained["steps"])
        assert not any(item["kind"] == "turn.native_result" for item in store.events_after(row["session_id"], row["turn_id"], None))
        monkeypatch.setattr(AntigravityAdapter, "start_turn", start)
        later = provider.event("steward-app", "retained-later", "A separate later request")
        transport.admit("steward-app", later)
        later_row = transport.core.read_request(identity_ref(binding["provider_ref"], later["message_id"]))
        assert runtime.wait_for_turn(session_id=later_row["session_id"], turn_id=later_row["turn_id"], timeout_sec=15)["status"] == "completed"
        transport.reconcile_request(transport.root / f"{later_row['request_ref']}.json")
        store.compact_completed_events(older_than_hours=0)
        # The provider can no longer prove even a normal stop, and the original
        # input is historical. Recovery must use the privately retained facts.
        from copy import deepcopy
        cleared = deepcopy(retained["steps"] + store.native_result(later_row["session_id"], later_row["turn_id"])["steps"])
        for step in cleared:
            step["status"] = "CORTEX_STEP_STATUS_CLEARED"
            step.get("plannerResponse", {}).pop("stopReason", None)
        language_server.update(frames=[], frame={"at": 0, "steps": cleared, "status": "CASCADE_RUN_STATUS_IDLE"})
        if case in {"wrong-native-turn", "wrong-anchor", "mixed-execution"}:
            if case == "wrong-native-turn":
                retained["upstream_turn_id"] = "another-invocation"
            elif case == "wrong-anchor":
                retained["steps"][0]["userInput"]["items"][0]["text"] += "foreign"
            else:
                retained["steps"][-1]["metadata"]["executionId"] = "another-execution"
            # Corrupt only the isolated fixture's private artifact.
            result_path.write_text(json.dumps(retained))
        elif case == "provider-unavailable":
            monkeypatch.setattr("loopx.capabilities.native_chat.antigravity_adapter.discover_antigravity_language_server", lambda: None)
        elif case == "closed":
            store.update_session(row["session_id"], status="closed")
        elif case == "source-changed":
            provider.messages[event["message_id"]]["content"] = "Edited source"
        elif case == "lost-ack":
            provider.lose_next_reply = True
        turn_bytes = store._turn_path(row["session_id"], row["turn_id"]).read_bytes()
        events_bytes = store._event_path(row["session_id"], row["turn_id"]).read_bytes()
        retained_bytes = result_path.read_bytes()
        writes = len(provider.writes)
        calls = [item for item in language_server["calls"] if item[0] in {"StartCascade", "SendUserCascadeMessage", "CancelCascadeInvocation"}]
        monkeypatch.setattr(runtime, "enqueue_turn", lambda **_: pytest.fail("Result recovery enqueued work"))
        monkeypatch.setattr(runtime, "resume_session_queue", lambda **_: pytest.fail("Result recovery resumed work"))
        restarted = LarkPrivateConversations(controller=runtime, runtime_root=transport.runtime_root, runner=provider, cli_bin="lark-cli")
        restarted.reconcile()
        recovered = restarted.core.read_request(ref).get("native_result_recovery")
        if case in {"normal", "provider-unavailable", "lost-ack"}:
            assert recovered["native_observation"]["observation_source"] == "retained_turn_result"
            assert recovered["native_observation"]["response"]["message"] == complete
            assert recovered["delivery_verified"] is (case != "lost-ack")
            assert len(provider.writes) == writes + 1
            if case != "lost-ack":
                receipt = _read_json(transport.root / f"{ref}.json")["deliveries"][f"native_result.{row['turn_id']}"]
                body = json.loads(provider.messages[receipt["attempt"]["message_ref"]]["body"]["content"])
                assert complete in "".join(item.get("text", "") for line in body["zh_cn"]["content"] for item in line)
            restarted.reconcile()
            assert len(provider.writes) == writes + 1
        else:
            assert recovered is None and len(provider.writes) == writes
        assert store._turn_path(row["session_id"], row["turn_id"]).read_bytes() == turn_bytes
        assert store._event_path(row["session_id"], row["turn_id"]).read_bytes() == events_bytes
        assert result_path.read_bytes() == retained_bytes
        assert [item for item in language_server["calls"] if item[0] in {"StartCascade", "SendUserCascadeMessage", "CancelCascadeInvocation"}] == calls
    finally:
        fixture.close()


def test_native_capture_io_failure_preserves_the_primary_answer(tmp_path, language_server, monkeypatch, caplog):  # noqa: F811 - imported pytest fixture
    from test_native_steward_private import steward

    monkeypatch.setenv("LOOPX_MANAGER_MODEL", "gemini-4-argon-high")
    monkeypatch.setenv("LOOPX_MANAGER_REASONING_EFFORT", "high")
    fixture = steward.__wrapped__(tmp_path)
    store, runtime, provider, transport, binding, *_ = next(fixture)
    try:
        binding = transport.bindings.configure(transport_ref="steward-app", project_ref=binding["project_ref"],
            executor_endpoint_id="antigravity", context_kind="steward")
        retain = store.retain_native_result

        def blocked_capture(session_id, turn_id, result):
            (store._session_dir(session_id) / "native-results").write_text("Synthetic occupied artifact directory")
            return retain(session_id, turn_id, result)

        monkeypatch.setattr(store, "retain_native_result", blocked_capture)
        language_server.update(plain_text=True, answers={0: "Verified original complete answer."})
        event = provider.event("steward-app", "original-primary", "Complete original request")
        transport.admit("steward-app", event)
        ref = identity_ref(binding["provider_ref"], event["message_id"])
        row = transport.core.read_request(ref)
        turn = runtime.wait_for_turn(session_id=row["session_id"], turn_id=row["turn_id"], timeout_sec=15)
        assert turn["status"] == "completed"
        assert store.native_result(row["session_id"], row["turn_id"]) == {}
        transport.reconcile_request(transport.root / f"{ref}.json")
        receipt = json.loads((transport.root / f"{ref}.json").read_text())["deliveries"]["terminal"]
        body = json.loads(provider.messages[receipt["attempt"]["message_ref"]]["body"]["content"])
        assert "Verified original complete answer." in "".join(item.get("text", "") for line in body["zh_cn"]["content"] for item in line)
        assert sum(method == "SendUserCascadeMessage" for method, _ in language_server["calls"]) == 1
        assert not any(method == "CancelCascadeInvocation" for method, _ in language_server["calls"])
        warning = [record.getMessage() for record in caplog.records if record.name == "loopx.chat_runtime"]
        assert any("Native completion capture unavailable: FileExistsError" in message for message in warning)
        assert all(str(tmp_path) not in message for message in warning)
    finally:
        fixture.close()


def test_active_original_reply_does_not_wait_for_older_native_result_probes(tmp_path, language_server, monkeypatch):  # noqa: F811
    from concurrent.futures import ThreadPoolExecutor
    from test_native_steward_private import steward
    from loopx.capabilities.native_chat.antigravity_adapter import AntigravityAdapter
    from loopx.chat_agent import CodexChatAgentError
    import time

    monkeypatch.setenv("LOOPX_MANAGER_MODEL", "gemini-4-argon-high")
    monkeypatch.setenv("LOOPX_MANAGER_REASONING_EFFORT", "high")
    fixture = steward.__wrapped__(tmp_path)
    store, runtime, provider, transport, binding, *_ = next(fixture)
    try:
        binding = transport.bindings.configure(transport_ref="steward-app", project_ref=binding["project_ref"],
            executor_endpoint_id="antigravity", context_kind="steward")
        start = AntigravityAdapter.start_turn

        def lose_commit(adapter, message, event_sink):
            start(adapter, message, event_sink)
            raise CodexChatAgentError("Synthetic lost Core commit", gate=None, error_code="antigravity_rpc_error")

        monkeypatch.setattr(AntigravityAdapter, "start_turn", lose_commit)
        first = provider.event("steward-app", "older-result", "Older original request")
        transport.admit("steward-app", first)
        ref = identity_ref(binding["provider_ref"], first["message_id"])
        row = transport.core.read_request(ref)
        assert runtime.wait_for_turn(session_id=row["session_id"], turn_id=row["turn_id"], timeout_sec=15)["status"] == "failed"
        assert transport.reconcile_request(transport.root / f"{ref}.json") == 1
        assert transport.core.context_recovery_candidate(ref)  # Idle discovery is preserved.
        monkeypatch.setattr(AntigravityAdapter, "start_turn", start)
        from loopx.capabilities.native_chat import antigravity_adapter
        post = antigravity_adapter._rpc_post

        def keep_new_request_running(url, token, method, payload, **kwargs):
            result = post(url, token, method, payload, **kwargs)
            if method == "SendUserCascadeMessage" and language_server["sent_count"] == 2:
                language_server["mode"] = "running"
            return result

        monkeypatch.setattr(antigravity_adapter, "_rpc_post", keep_new_request_running)
        later = provider.event("steward-app", "current-work", "Current original work")
        transport.admit("steward-app", later)
        current = transport.core.read_request(identity_ref(binding["provider_ref"], later["message_id"]))
        deadline = time.monotonic() + 5
        while not store.load_turn(current["session_id"], current["turn_id"]).get("upstream_turn_id"):
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert not transport.core.context_recovery_candidate(ref)
        monkeypatch.setattr(AntigravityAdapter, "completed_result_observation", staticmethod(
            lambda **_: pytest.fail("Historical native IO while current original work is active")))
        assert transport.reconcile_request(transport.root / f"{ref}.json") == 0
        with ThreadPoolExecutor(max_workers=1) as replies:
            try:
                replies.submit(transport.reconcile_request, transport.root / f"{current['request_ref']}.json").result(timeout=2)
                record = json.loads((transport.root / f"{current['request_ref']}.json").read_text())
                assert record["deliveries"]["progress"]["verified"]
                assert store.load_turn(current["session_id"], current["turn_id"])["status"] in {"starting", "running"}
            finally:
                language_server["mode"] = "complete"
        assert language_server["sent_count"] == 2
    finally:
        language_server["mode"] = "complete"
        fixture.close()


@pytest.mark.parametrize("fault", [None, "wrong-target", "changed-brief", "other-action", "still-invalid",
    "revoked", "new-input", "stop", "unknown-send", "post-enqueue-crash", "reservation-crash", "revoke-during-review",
    "tool-call", "tool-step", "mixed-execution", "missing-stop", "conflicting-input", "response-only", "extra-input"])
def test_original_queue_reviews_only_unsubmitted_native_handoffs(tmp_path, language_server, monkeypatch, fault):  # noqa: F811
    from copy import deepcopy
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from test_native_steward_private import steward
    from loopx.capabilities.native_chat.antigravity_adapter import AntigravityAdapter
    from loopx.capabilities.manager_context import POLICY_SCHEMA, _root, _write, pending
    from loopx.chat_agent import CodexChatAgentError
    from loopx.chat_store import _read_json

    monkeypatch.setenv("LOOPX_MANAGER_MODEL", "gemini-4-argon-high")
    monkeypatch.setenv("LOOPX_MANAGER_REASONING_EFFORT", "high")
    fixture = steward.__wrapped__(tmp_path)
    store, runtime, provider, transport, binding, _, workspace = next(fixture)
    revoker = ThreadPoolExecutor(max_workers=1)
    try:
        registry = json.loads(runtime.registry_path.read_text())
        registry["goals"] = [{"id": "work", "repo": str(workspace), "coordination": {"registered_agents": ["worker", "other-worker"]}}]
        runtime.registry_path.write_text(json.dumps(registry))
        binding = transport.bindings.configure(transport_ref="steward-app", project_ref=binding["project_ref"],
            executor_endpoint_id="antigravity", context_kind="steward")
        language_server["invalid_handoff"] = True
        language_server["original_handoffs"] = [
            {"goal_id": "work", "agent_id": "worker", "brief": "invalid"},
            {"goal_id": "work", "agent_id": "other-worker", "brief": "Second original instruction"}]
        start = AntigravityAdapter.start_turn
        def lose_first_commit(adapter, message, event_sink, **kwargs):
            result = start(adapter, message, event_sink, **kwargs)
            if not kwargs.get("read_only_review"):
                raise CodexChatAgentError("Lost original Core commit", gate=None, error_code="antigravity_rpc_error")
            if fault == "revoke-during-review":
                revoker.submit(transport.bindings.disconnect, binding["binding_id"],
                    expected_revision=transport.bindings.read()["revision"])
                deadline = time.monotonic() + 5
                while binding in transport.bindings.read()["bindings"]:
                    assert time.monotonic() < deadline
                    threading.Event().wait(0.01)
            return result
        monkeypatch.setattr(AntigravityAdapter, "start_turn", lose_first_commit)
        event = provider.event("steward-app", "format-original", "Ask the original worker to assess the constraint")
        transport.admit("steward-app", event)
        ref = identity_ref(binding["provider_ref"], event["message_id"])
        original = transport.core.read_request(ref)
        failed = runtime.wait_for_turn(session_id=original["session_id"], turn_id=original["turn_id"], timeout_sec=15)
        assert failed["status"] == "failed"
        failed_bytes = store._turn_path(original["session_id"], original["turn_id"]).read_bytes()
        failed_events = store.events_after(original["session_id"], original["turn_id"], None)
        path = transport.root / f"{ref}.json"
        assert transport.reconcile_request(path) == 1  # Original failure notice.
        assert transport.reconcile_request(path) == 1  # Original native prose, no model Send.
        frozen = deepcopy(transport.core.read_request(ref)["native_result_recovery"])
        notice = deepcopy(_read_json(path)["deliveries"]["terminal"])
        assert frozen["delivery_verified"] and language_server["sent_count"] == 1
        session = store.load_session(original["session_id"])
        _write(_root(runtime.coordination_runtime_root) / "policy.json", {"schema_version": POLICY_SCHEMA, "sources": {
            session["channel_id"]: {"local_delivery_scope": "selected", "sender_ids": [binding["operator_ref"]],
                "targets": [{"goal_id": "work", "agent_id": agent} for agent in ("worker", "other-worker")]}}})
        brief = {"schema_version": "collaboration_brief_v0", "purpose": "Assess the original constraint", "context": "invalid",
            "constraints": ["Keep the original source"], "inputs": [], "acceptance": ["Return the assessment"],
            "return_requirement": "Return to the original conversation"}
        response = {"message": "Untrusted model delivery claim", "proposals": [], "gate": None,
            "context_handoff": [{"goal_id": "work", "agent_id": "worker", "brief": brief},
                {"goal_id": "work", "agent_id": "other-worker", "brief": {**brief, "context": "Second original instruction"}}]}
        if fault == "wrong-target":
            response["context_handoff"][-1]["agent_id"] = "another"
        elif fault == "changed-brief":
            brief["context"] = "Changed original instruction"
        elif fault == "other-action":
            response["unfinished_work"] = {"remaining": "New work", "dependency": "Someone", "effects_settled": True}
            response["goal_draft"] = {"objective": "Other business"}
        elif fault == "still-invalid":
            response["context_handoff"][0]["brief"] = "invalid again"
        language_server["review_response"] = response
        language_server["review_native_fault"] = fault
        if fault == "revoked":
            transport.bindings.disconnect(binding["binding_id"], expected_revision=transport.bindings.read()["revision"])
        elif fault == "new-input":
            store.create_queued_turn(session["session_id"], client_turn_id="newer", message="New correction", origin="lark")
        elif fault == "stop":
            store.append_event(session["session_id"], failed["turn_id"], kind="turn.interrupt_requested", payload={})
        elif fault == "unknown-send":
            language_server["send_failure"] = True
        elif fault in {"post-enqueue-crash", "reservation-crash"}:
            enqueue = runtime.enqueue_turn
            def crash(**kwargs):
                if fault == "post-enqueue-crash":
                    enqueue(**kwargs)
                raise OSError("Synthetic post-enqueue crash")
            monkeypatch.setattr(runtime, "enqueue_turn", crash)
            with pytest.raises(OSError, match="post-enqueue crash"):
                transport.reconcile_request(path)
            monkeypatch.setattr(runtime, "enqueue_turn", enqueue)
        restarted = LarkPrivateConversations(controller=runtime, runtime_root=transport.runtime_root,
            runner=provider, cli_bin="lark-cli")
        try:
            restarted.reconcile_request(path)
        except ValueError:
            assert fault == "revoked"
        row = restarted.core.read_request(ref)
        if fault in {"revoked", "new-input", "stop"}:
            assert row["turn_id"] == original["turn_id"]
            assert language_server["sent_count"] == 1
            return
        assert row["turn_id"] != original["turn_id"]
        reviewed = runtime.wait_for_turn(session_id=row["session_id"], turn_id=row["turn_id"], timeout_sec=15)
        assert reviewed["client_turn_id"].endswith("-metadata-1")
        if fault != "revoke-during-review":
            restarted.reconcile_request(path)
        recovered = restarted.core.read_request(ref)
        assert recovered["native_result_recovery"] == frozen
        assert _read_json(path)["deliveries"]["terminal"] == notice
        assert store._turn_path(original["session_id"], original["turn_id"]).read_bytes() == failed_bytes
        assert store.events_after(original["session_id"], original["turn_id"], None) == failed_events
        assert not any("Untrusted model delivery claim" in write[1] for write in provider.writes)
        entries = pending(runtime.coordination_runtime_root, "work", "worker")["items"]
        others = pending(runtime.coordination_runtime_root, "work", "other-worker")["items"]
        if fault in {None, "post-enqueue-crash", "reservation-crash"}:
            assert reviewed["status"] == "completed", reviewed
            assert len(entries) == 1 and entries[0]["source_id"] == ref
            assert len(others) == 1 and others[0]["source_id"] == ref
            assert reviewed["response"]["context_handoff_receipts"][0]["request_id"] == entries[0]["request_id"]
            assert not reviewed["response"]["context_handoff_failures"]
            assert "实际提交回执" in json.dumps(provider.messages, ensure_ascii=False)
        else:
            assert reviewed["status"] in ({"failed", "interrupted"} if fault == "revoke-during-review" else {"failed"}), reviewed
            assert entries == others == []
            if fault != "revoke-during-review":
                assert restarted.core.quota_backlog_pending(recovered)
        count, calls = language_server["sent_count"], len(provider.writes)
        if fault != "revoke-during-review":
            restarted.reconcile_request(path)
            restarted.reconcile_request(path)
        assert (language_server["sent_count"], len(provider.writes)) == (count, calls)
        assert len(list(store._turn_path(row["session_id"], row["turn_id"]).parent.glob("*.json"))) == 2
        sends = [body for method, body in language_server["calls"] if method == "SendUserCascadeMessage"]
        config = sends[-1]["cascadeConfig"]["plannerConfig"]
        assert config["toolConfig"]["forceDisableToolCalling"] is True
        assert config["conversational"]["agenticMode"] is False
        assert config["customizationDiscoveryConfig"]["hooks"]["inheritUser"] is False
        assert runtime.adapters[row["session_id"]].runtime_profile == "trusted_owner"
        if fault is None:
            from loopx.capabilities.manager_context import acknowledge
            from loopx.capabilities.manager_context.roundtrip import drain, report, reply_status
            from loopx.extensions.lark.manager_returns import LarkManagerReturnTransport
            sender = LarkManagerReturnTransport(SimpleNamespace(registry_path=runtime.registry_path,
                lark_private_conversations=restarted, lark_goal_topic_runtime=SimpleNamespace(
                    snapshot_provider=lambda: pytest.fail("Private return must keep its native source"))),
                runtime.coordination_runtime_root)
            route = {"goal_id": "work", "agent_id": "worker", "request_id": entries[0]["request_id"],
                "source_id": ref, "session_id": row["session_id"]}
            acknowledge(sender.root, "work", "worker", route["request_id"], "adopt", "Use original source")
            report(sender.root, "work", "worker", route["request_id"], "conclusion", "Verified receiver conclusion")
            drain(sender.root, runtime.registry_path, store, sender)
            assert reply_status(sender.root, route)[0]["status"] == "delivered"
            assert "Verified receiver conclusion" in json.dumps(provider.messages, ensure_ascii=False)
            returned = [item for item in store.messages(row["session_id"]) if item.get("origin") == "manager_followup"]
            assert len(returned) == 1 and returned[0]["turn_id"] == reviewed["turn_id"]
            assert language_server["sent_count"] == count
    finally:
        revoker.shutdown(wait=True)
        fixture.close()


@pytest.mark.parametrize("fault", [None, "codex-fallback", "quota-restore", "quota-unchanged", "quota-unknown",
    "stop", "new-input", "no-anchor", "changed-proof", "unknown-outer", "wrong-target", "changed-brief", "tool-step",
    "cleared-before-proof", "retention-io", "link-inputs", "link-inputs-new-input", "link-inputs-bad-reference",
    "link-inputs-ungranted", "link-inputs-codex-fallback", "link-inputs-oversized-description", "settled-tool-error"])
def test_completed_invalid_metadata_uses_one_original_format_review(tmp_path, language_server, monkeypatch, fault):  # noqa: F811 - imported pytest fixture
    """An already delivered, historical answer must not be sent/executed twice."""
    from copy import deepcopy
    from datetime import timezone
    from test_native_steward_private import steward
    from loopx.capabilities.native_chat.antigravity_adapter import AntigravityAdapter
    from loopx.capabilities.manager_context import POLICY_SCHEMA, _root, _write, pending

    monkeypatch.setenv("LOOPX_MANAGER_MODEL", "gemini-4-argon-high")
    monkeypatch.setenv("LOOPX_MANAGER_REASONING_EFFORT", "high")
    fixture = steward.__wrapped__(tmp_path)
    store, runtime, provider, transport, binding, _, workspace = next(fixture)
    try:
        registry = json.loads(runtime.registry_path.read_text())
        registry["goals"] = [{"id": "work", "repo": str(workspace), "coordination": {
            "registered_agents": ["worker", "other-worker"] if fault == "link-inputs-ungranted" else ["worker"]}}]
        runtime.registry_path.write_text(json.dumps(registry))
        binding = transport.bindings.configure(transport_ref="steward-app", project_ref=binding["project_ref"],
            executor_endpoint_id="antigravity", context_kind="steward")
        original_brief = {"schema_version": "collaboration_brief_v0", "Hypotheses": ["Assess this hypothesis"],
            "Evidence": ["Original authorized evidence"], "Constraints": ["Return here; do not change frozen work"]}
        link_inputs = str(fault).startswith("link-inputs")
        codex_fallback = fault in {"codex-fallback", "link-inputs-codex-fallback"}
        if link_inputs:
            ref = "https://example.test/project/changes/123"
            if fault == "link-inputs-bad-reference":
                ref += "?opaque=synthetic"
            description = ("x" * 1001 if fault == "link-inputs-oversized-description"
                else "Review the identified change at its recorded revision.")
            original_brief = {"schema_version": "collaboration_brief_v0", "purpose": "Assess the original hypothesis",
                "context": "Review the recorded change and return its verified disposition.",
                "constraints": ["Do not change frozen work"],
                "inputs": [{"ref": ref, "description": description}],
                "acceptance": ["Return the assessment"], "return_requirement": "Return to the original conversation"}
        handoff = {"schema_version": "context_handoff_v0", "goal_id": "work", "agent_id": "worker", "brief": original_brief}
        if fault == "unknown-outer":
            handoff["execution_binding_id"] = "unapproved-binding"
        language_server.update(invalid_handoff=True, original_handoffs=handoff,
            answers={0: "Original complete visible answer."})
        # Emulate an original completion from before durable result capture;
        # keep its real Core input anchor and same-invocation native history.
        start_turn = AntigravityAdapter.start_turn
        def legacy_completion(adapter, message, event_sink, **kwargs):
            return start_turn(adapter, message, lambda kind, payload: event_sink(kind, payload)
                if kind != "turn.native_result" else None, **kwargs)
        if not link_inputs:
            monkeypatch.setattr(AntigravityAdapter, "start_turn", legacy_completion)
        event = provider.event("steward-app", "completed-metadata", "Ask the original worker to assess this hypothesis")
        transport.admit("steward-app", event)
        ref = identity_ref(binding["provider_ref"], event["message_id"])
        original = transport.core.read_request(ref)
        completed = runtime.wait_for_turn(session_id=original["session_id"], turn_id=original["turn_id"], timeout_sec=15)
        assert completed["status"] == "completed"
        assert completed["response"]["gate"]["kind"] == "response_metadata_invalid"
        if link_inputs:
            original_send = next(body for method, body in language_server["calls"] if method == "SendUserCascadeMessage")
            prompt = original_send["items"][0]["text"]
            assert "Put PR, issue and other URLs and their exact revisions in context" in prompt
            assert "claim delivery only after a host receipt" in prompt
        monkeypatch.setattr(AntigravityAdapter, "start_turn", start_turn)
        assert transport.reconcile_request(transport.root / f"{ref}.json") == 1
        original_bytes = store._turn_path(original["session_id"], original["turn_id"]).read_bytes()
        original_events = store.events_after(original["session_id"], original["turn_id"], None)
        retained_native = store.native_result(original["session_id"], original["turn_id"])
        assert bool(retained_native) is link_inputs
        # Another native input is historical context, not authority to replay it.
        language_server["inputs"].append({"text": "An unrelated later native input", "created_at": datetime.now(timezone.utc).isoformat()})
        language_server["sent_count"] += 1
        if fault == "settled-tool-error":
            from loopx.capabilities.native_chat import antigravity_adapter as native
            rpc = native._rpc_post
            language_server["ignore_step_range"] = True
            def later_handled_tool_error(url, token, method, body, **kwargs):
                result = rpc(url, token, method, body, **kwargs)
                if method == "GetCascadeTrajectory":
                    result["numTotalSteps"] += 1
                elif method == "GetCascadeTrajectorySteps":
                    result = deepcopy(result)
                    for step in result["steps"]:
                        index = step["metadata"]["sourceTrajectoryStepInfo"]["stepIndex"]
                        if index >= 3:
                            step["metadata"]["sourceTrajectoryStepInfo"]["stepIndex"] += 1
                    later = next(s for s in result["steps"] if s["metadata"]["sourceTrajectoryStepInfo"]["stepIndex"] == 2)
                    result["steps"].insert(3, {"type": "CORTEX_STEP_TYPE_VIEW_FILE", "status": "CORTEX_STEP_STATUS_ERROR",
                        "metadata": {**later["metadata"], "sourceTrajectoryStepInfo": {"stepIndex": 3}}})
                return result
            monkeypatch.setattr(native, "_rpc_post", later_handled_tool_error)
        if fault in {"quota-restore", "quota-unchanged"} or codex_fallback:
            language_server.update(quota_error_indices=[1], quota_fraction=0.006)
        elif fault == "quota-unknown":
            language_server["quota_unknown"] = True
        elif fault == "cleared-before-proof":
            language_server["cleared_input_indices"] = [0]
        elif fault == "retention-io":
            def cannot_retain(*args, **kwargs):
                raise OSError("Synthetic supplemental storage unavailable")
            monkeypatch.setattr(store, "retain_native_result", cannot_retain)
        context = json.dumps(original_brief, ensure_ascii=False, separators=(",", ":"))
        brief = {"schema_version": "collaboration_brief_v0", "purpose": "Assess the original hypothesis", "context": context,
            "constraints": ["Do not change frozen work"], "inputs": [], "acceptance": ["Return the assessment"],
            "return_requirement": "Return to the original conversation"}
        response = {"message": "Unverified model delivery claim", "proposals": [], "gate": None,
            "context_handoff": [{"goal_id": "work", "agent_id": "worker", "brief": brief}]}
        if fault == "wrong-target":
            response["context_handoff"][0]["agent_id"] = "other-worker"
        elif fault == "changed-brief":
            brief["context"] = "Changed original evidence"
        elif fault == "tool-step":
            language_server["review_native_fault"] = "tool-step"
            language_server["review_fault_start_index"] = 2
        language_server["review_response"] = response
        session = store.load_session(original["session_id"])
        granted = "other-worker" if fault == "link-inputs-ungranted" else "worker"
        _write(_root(runtime.coordination_runtime_root) / "policy.json", {"schema_version": POLICY_SCHEMA, "sources": {
            session["channel_id"]: {"local_delivery_scope": "selected", "sender_ids": [binding["operator_ref"]],
                "targets": [{"goal_id": "work", "agent_id": granted}]}}})
        if fault == "stop":
            store.append_event(session["session_id"], original["turn_id"], kind="turn.interrupt_requested", payload={})
            original_events = store.events_after(original["session_id"], original["turn_id"], None)
        elif fault in {"new-input", "link-inputs-new-input"}:
            store.create_queued_turn(session["session_id"], client_turn_id="new-correction", message="A correction", origin="lark")
        elif fault == "no-anchor":
            events_after = store.events_after
            def without_anchor(*args):
                rows = deepcopy(events_after(*args))
                for item in rows:
                    item.get("payload", {}).pop("native_input", None)
                return rows
            monkeypatch.setattr(store, "events_after", without_anchor)
        elif fault == "changed-proof":
            observe = AntigravityAdapter.completed_result_observation
            def changed(**kwargs):
                result = observe(**kwargs)
                if result:
                    result["response"]["message"] += "Changed original answer"
                return result
            monkeypatch.setattr(AntigravityAdapter, "completed_result_observation", staticmethod(changed))
        codex_sends = []
        if codex_fallback:
            from types import SimpleNamespace
            from test_chat_manager_model_adoption import configure
            from loopx.chat_manager import manager_executor_allocation
            from loopx.capabilities.native_chat.codex_adapter import CodexAppServerAdapter
            import loopx.chat_manager as manager_module
            import loopx.chat_runtime as runtime_module
            configure(runtime.coordination_runtime_root, model="gemini-4-argon-high", effort="high",
                endpoint="antigravity", policy="flexible")
            capabilities = [{"agent_id": name, "available": True, "adapter_kind": kind} for name, kind in
                [("antigravity", "antigravity_ls"), ("codex", "codex_app_server")]]
            monkeypatch.setattr(runtime, "capabilities", lambda: capabilities)
            allocation = manager_executor_allocation(runtime, environ={}, endpoint_capabilities=[
                {**item, "available": item["agent_id"] == "codex"} for item in capabilities])
            def route(*args):
                return {"allocation": allocation, "blocked_primary": None,
                    "observed_at": time.time()}
            monkeypatch.setattr(manager_module, "manager_session_quota_route", route)
            monkeypatch.setattr(runtime_module, "manager_session_quota_route", route)
            class Formatter(CodexAppServerAdapter):
                def __init__(self):
                    import queue
                    from loopx.chat_agent import CodexChatAgentSession
                    super().__init__(CodexChatAgentSession(process=SimpleNamespace(poll=lambda: None),
                        messages=queue.Queue(), thread_id="formatter-native", work_dir=workspace,
                        metadata_review=True, read_only_review=True, resolved_model="gpt-6.1-sol",
                        model="gpt-6.1-sol", reasoning_effort="high", sandbox="read-only"))
                def start_turn(self, message, event_sink):
                    codex_sends.append(message)
                    event_sink("turn.started", {"upstream_turn_id": "format-only"})
                    return deepcopy(response)
                def close_session(self):
                    pass
                def healthcheck(self):
                    return True
            def start(**kwargs):
                assert kwargs["agent_id"] == "codex" and kwargs["read_only_review"] is True
                assert kwargs["metadata_review_context"] == session["steward_context"]
                assert kwargs["resume_thread_id"] is None
                return Formatter()
            monkeypatch.setattr(runtime, "_start_adapter", start)
        path = transport.root / f"{ref}.json"
        if fault == "settled-tool-error":
            from loopx.extensions.lark.goal_topic_runtime_service import LarkGoalTopicRuntimeService
            service = LarkGoalTopicRuntimeService(snapshot_provider=lambda: {}, runtime_root=transport.runtime_root,
                runtime_controller=runtime, private_conversations=transport)
            def drive(step, **_):
                deadline = time.monotonic() + 15
                try:
                    while time.monotonic() < deadline:
                        step()
                        current = transport.core.read_request(ref)
                        if (current["turn_id"] != original["turn_id"]
                                and current.get("context_recovery", {}).get("metadata_review") and current.get("delivery_verified")):
                            return
                        time.sleep(.01)
                    pytest.fail("The existing discovery/reply worker did not complete the original metadata review")
                finally:
                    service._closed.set()
            monkeypatch.setattr(service._delivery_worker, "repeat", drive)
            service._reconcile_private()
        else:
            transport.reconcile_request(path)
        if fault in {"quota-restore", "quota-unchanged"}:
            held = transport.core.read_request(ref)
            assert held["metadata_quota_wait"]["remaining_fraction"] == 0.006
            assert not held.get("context_recovery")
            retained = store.native_result(original["session_id"], original["turn_id"])
            assert retained["upstream_turn_id"] == completed["upstream_turn_id"]
            retained_bytes = (store._session_dir(original["session_id"]) / "native-results" / (original["turn_id"] + ".json")).read_bytes()
            # The original service retained its own verified facts before quota
            # recovery. Later native compaction cannot erase this obligation.
            language_server["cleared_input_indices"] = [0]
            transport.reconcile_request(path)
            assert len([1 for method, _ in language_server["calls"] if method == "SendUserCascadeMessage"]) == 1
            if fault == "quota-restore":
                language_server["quota_fraction"] = 1
                transport.reconcile_request(path)
                assert len([1 for method, _ in language_server["calls"] if method == "SendUserCascadeMessage"]) == 1
                from datetime import timedelta
                language_server["quota_reset"] = (datetime.now(timezone.utc) + timedelta(days=14)).isoformat()
                transport.reconcile_request(path)
            assert (store._session_dir(original["session_id"]) / "native-results" / (original["turn_id"] + ".json")).read_bytes() == retained_bytes
        row = transport.core.read_request(ref)
        assert store._turn_path(original["session_id"], original["turn_id"]).read_bytes() == original_bytes
        if fault in {"quota-unchanged", "quota-unknown", "stop", "new-input", "link-inputs-new-input",
                     "no-anchor", "changed-proof", "unknown-outer",
                     "cleared-before-proof", "retention-io"}:
            assert row["turn_id"] == original["turn_id"]
            assert not pending(runtime.coordination_runtime_root, "work", "worker")["items"]
            assert len([1 for method, _ in language_server["calls"] if method == "SendUserCascadeMessage"]) == 1
            return
        assert row["turn_id"] != original["turn_id"], "Completed invalid metadata was not returned to its original worker"
        reviewed = runtime.wait_for_turn(session_id=row["session_id"], turn_id=row["turn_id"], timeout_sec=15)
        transport.reconcile_request(path)
        entries = pending(runtime.coordination_runtime_root, "work", "worker")["items"]
        if codex_fallback or fault in {None, "quota-restore", "link-inputs", "link-inputs-bad-reference", "settled-tool-error",
                                     "link-inputs-oversized-description"}:
            assert reviewed["status"] == "completed", reviewed
            assert len(entries) == 1 and entries[0]["source_id"] == ref
            assert entries[0]["brief"]["context"] == context
            if link_inputs:
                assert entries[0]["brief"]["inputs"] == []
                assert reviewed["response"]["context_handoff_receipts"][0]["request_id"] == entries[0]["request_id"]
                assert store.native_result(original["session_id"], original["turn_id"]) == retained_native
        elif fault == "link-inputs-ungranted":
            assert reviewed["status"] == "completed" and not entries
            assert not pending(runtime.coordination_runtime_root, "work", "other-worker")["items"]
            assert not reviewed["response"]["context_handoff_receipts"]
            assert reviewed["response"]["context_handoff_failures"]
        else:
            assert reviewed["status"] == "failed" and not entries
        assert store._turn_path(original["session_id"], original["turn_id"]).read_bytes() == original_bytes
        assert store.events_after(original["session_id"], original["turn_id"], None) == original_events
        assert "native_result_recovery" not in row  # Already delivered prose is not sent a second time.
        assert sum("Original complete visible answer." in str(item.get("body", {}).get("content", item.get("content")))
            for item in provider.messages.values()) == 1
        assert not any("Unverified model delivery claim" in write[1] for write in provider.writes)
        sends = [body for method, body in language_server["calls"] if method == "SendUserCascadeMessage"]
        if codex_fallback:
            assert len(sends) == 1 and len(codex_sends) == 1
            assert "Put PR, issue and other URLs and their exact revisions in context" in codex_sends[0]
            actual = store.load_session(session["session_id"])
            assert actual["agent_id"] == "codex" and actual["agent_model_id"] == "gpt-6.1-sol"
            assert actual["steward_context"] == session["steward_context"]
            assert actual["upstream_context_history"][-1]["thread_id"] == session["upstream_thread_id"]
        elif fault in {"link-inputs", "link-inputs-ungranted"}:
            assert len(sends) == 1, "Link-only metadata repair must not start a second model Turn"
            assert json.loads(path.read_text())["status"] == "delivered"
            assert transport.core.read_request(ref)["delivery_verified"] is True
            if fault == "link-inputs":
                assert "实际提交回执" in json.dumps(provider.messages, ensure_ascii=False)
        else:
            assert len(sends) == 2 and sends[-1]["cascadeConfig"]["plannerConfig"]["toolConfig"]["forceDisableToolCalling"] is True
            if link_inputs:
                assert "Put PR, issue and other URLs and their exact revisions in context" in sends[-1]["items"][0]["text"]
        before = (len(sends), len(provider.writes))
        transport.reconcile_request(path)
        assert (len([1 for method, _ in language_server["calls"] if method == "SendUserCascadeMessage"]), len(provider.writes)) == before
        if codex_fallback:
            # The next original-source input/status must reuse this canonical
            # Session even though the binding's preferred endpoint is Argon.
            monkeypatch.setattr(runtime, "enqueue_turn", lambda **kw: store.create_queued_turn(
                kw["session_id"], client_turn_id=kw["client_turn_id"], message=kw["message"], origin=kw["origin"]))
            followup = provider.event("steward-app", "after-fallback", "Continue in this original conversation")
            transport.admit("steward-app", followup)
            admitted = transport.core.read_request(identity_ref(binding["provider_ref"], followup["message_id"]))
            assert admitted["status"] == "accepted" and admitted["session_id"] == session["session_id"]
            assert store.load_turn(admitted["session_id"], admitted["turn_id"])["status"] == "queued"
            status = provider.event("steward-app", "status-after-fallback", "/status")
            transport.admit("steward-app", status)
            observed = transport.core.read_request(identity_ref(binding["provider_ref"], status["message_id"]))
            assert observed["session_id"] == session["session_id"]
            assert len(store.list_sessions(channel_id=session["channel_id"])) == 1
            assert len(codex_sends) == 1
    finally:
        fixture.close()


def connect_group(fixture):
    store, runtime, provider, transport = connect(fixture)
    runtime.codex_home = fixture[-1].parent / "isolated-native-account"
    runtime.codex_home.mkdir()
    fake = fixture[-2]
    fake.write_text(fake.read_text().replace('"thread": {"id": "durable-thread"},',
        '"thread": {"id": "durable-thread"}, '
        '"activePermissionProfile": {"id": request["params"].get("permissions")}, '
        '"runtimeWorkspaceRoots": [request["params"]["cwd"]],').replace(
            '        if "wait for interrupt" in json.dumps(request.get("params") or {}) or "wait for steer" in json.dumps(request.get("params") or {}):',
            # The synthetic host's wait trigger belongs to the current input,
            # not a preceding message quoted as context.
            '        current = json.dumps(request.get("params") or {}).rsplit("Current user message:", 1)[-1]\n'
            '        if "wait for interrupt" in current or "wait for steer" in current:'))
    binding = transport.bindings.read()["bindings"][0]
    refs = [identity_ref(binding["provider_ref"], chat) for chat in ["oc_community", "oc_second"]]
    transport.bindings.configure(transport_ref="notes-app", project_ref=binding["project_ref"],
        executor_endpoint_id="codex", audience="group", group_refs=refs, available_group_refs=refs)
    return store, runtime, provider, transport


@pytest.mark.parametrize("info, pending", [("usageLimitExceeded", 1), ("cyberPolicy", 0), (None, 0)])
def test_delivered_quota_failure_stays_pending_across_restart_without_replaying(ordinary, monkeypatch, info, pending):  # noqa: F811
    store, runtime, provider, transport = connect(ordinary)
    fake = ordinary[-2]
    fake.write_text(fake.read_text().replace('"codexErrorInfo": "cyberPolicy"',
        f'"codexErrorInfo": "{info}"').replace(
        '            print(json.dumps({"method": "item/agentMessage/delta", "params": {"threadId": "durable-thread", "turnId": active_turn, "delta": "Partial answer."}}), flush=True)\n', ''))
    try:
        event = provider.event("notes-app", "quota-pending",
            "typed terminal failure" if info else "A normal successful request")
        transport.admit("notes-app", event)
        row = transport.core.pending()[0]
        failed = runtime.wait_for_turn(session_id=row["session_id"], turn_id=row["turn_id"], timeout_sec=10)
        assert failed["status"] == ("failed" if info else "completed")
        assert (failed["error_code"] == "usage_limit_exceeded") == bool(pending)
        transport.reconcile()
        saved = json.loads((transport.root / f"{row['request_ref']}.json").read_text())
        assert saved["status"] == "delivered" and saved["deliveries"]["terminal"]["verified"]
        if not pending:
            # The typed owner can exclude completed/non-quota work without
            # scanning unrelated events or source-control journals.
            monkeypatch.setattr(store, "events_after", lambda *_: pytest.fail("not quota backlog"))
            monkeypatch.setattr(transport.core, "source_controls", lambda: pytest.fail("not quota backlog"))
        assert transport.health()[row["binding_id"]]["pending_count"] == pending
        before = ordinary[4].read_bytes(), list(provider.writes)
        restarted = LarkPrivateConversations(controller=runtime, runtime_root=transport.runtime_root,
            runner=provider, cli_bin="lark-cli")
        assert restarted.health()[row["binding_id"]] == {"pending_count": pending, "recovery_count": 0}
        assert (ordinary[4].read_bytes(), provider.writes) == before
        assert store.load_turn(row["session_id"], row["turn_id"]) == failed
    finally:
        runtime.close()


@pytest.mark.parametrize("command", ["stop", "new"])
@pytest.mark.parametrize("error_code", ["response_timeout", "upstream_transport_failure", "context_window_exceeded"])
def test_delivered_timeout_stays_pending_without_replaying_and_honors_stop(ordinary, monkeypatch, command, error_code):  # noqa: F811
    from loopx.chat_agent import CodexChatAgentError
    from loopx.capabilities.native_chat.codex_adapter import CodexAppServerAdapter

    starts = []

    def timeout_after_start(_adapter, message, event_sink):
        starts.append(message)
        event_sink("turn.started", {"upstream_turn_id": "synthetic-timeout"})
        raise CodexChatAgentError("Synthetic upstream failure", gate=None, error_code=error_code)

    monkeypatch.setattr(CodexAppServerAdapter, "start_turn", timeout_after_start)
    store, runtime, provider, transport = connect(ordinary)
    try:
        transport.admit("notes-app", provider.event("notes-app", "timeout-pending", "Complete this request."))
        row = transport.core.pending()[0]
        failed = runtime.wait_for_turn(session_id=row["session_id"], turn_id=row["turn_id"], timeout_sec=10)
        assert failed["status"] == "failed" and failed["error_code"] == error_code
        transport.reconcile()
        saved = json.loads((transport.root / f"{row['request_ref']}.json").read_text())
        assert saved["status"] == "delivered" and saved["deliveries"]["terminal"]["verified"]
        restarted = LarkPrivateConversations(controller=runtime, runtime_root=transport.runtime_root,
            runner=provider, cli_bin="lark-cli")
        before = ordinary[4].read_bytes(), list(provider.writes), store.messages(row["session_id"])
        with monkeypatch.context() as observation_only:
            observation_only.setattr(runtime, "enqueue_turn", lambda **_: pytest.fail("health admitted work"))
            observation_only.setattr(transport.core, "recover_failed_request", lambda *_a, **_kw: pytest.fail("health replayed work"))
            assert transport.health()[row["binding_id"]] == {"pending_count": 1, "recovery_count": 0}
            assert restarted.health()[row["binding_id"]] == {"pending_count": 1, "recovery_count": 0}
        assert (ordinary[4].read_bytes(), provider.writes, store.messages(row["session_id"])) == before
        assert store.load_turn(row["session_id"], row["turn_id"]) == failed
        assert len(starts) == 1 and "Complete this request." in starts[0]
        transport.admit("notes-app", provider.event("notes-app", "retire-timeout", f"/{command}"))
        transport.reconcile()
        assert transport.health()[row["binding_id"]]["pending_count"] == 0
        assert len(starts) == 1
    finally:
        runtime.close()


@pytest.mark.parametrize("command", ["stop", "new"])
def test_delivered_unfinished_work_remains_visible_without_admitting_recovery(ordinary, monkeypatch, command):  # noqa: F811
    store, runtime, _, _, _, fake, _ = ordinary
    work = {"remaining": "Finish the verified remaining result", "dependency": "Temporary source outage", "effects_settled": True}
    partial = "Partial result; authorized work remains.\n<loopx-review-json>" + json.dumps({
        "message": "", "unfinished_work": work}) + "</loopx-review-json>"
    source = fake.read_text()
    start = source.index("        response = 'Visible runtime response.")
    end = source.index('        print(json.dumps({"method": "item/agentMessage/delta"', start)
    fake.write_text(source[:start] + f"        response = {partial!r}\n" + source[end:])
    store, runtime, provider, transport = connect(ordinary)
    try:
        event = provider.event("notes-app", "unfinished-visible", "Finish this synthetic request.")
        transport.admit("notes-app", event)
        row = transport.core.pending()[0]
        turn = runtime.wait_for_turn(session_id=row["session_id"], turn_id=row["turn_id"], timeout_sec=10)
        assert turn["response"]["unfinished_work"] == work
        transport.reconcile()
        assert transport.core.read_request(row["request_ref"])["delivery_verified"]
        restarted = LarkPrivateConversations(controller=runtime, runtime_root=store.root.parent,
            runner=provider, cli_bin="lark-cli")
        starts = ordinary[4].read_bytes(), list(provider.writes)
        with monkeypatch.context() as observation_only:
            observation_only.setattr(runtime, "enqueue_turn", lambda **_: pytest.fail("health admitted work"))
            observation_only.setattr(transport.core, "recover_failed_request", lambda *_args, **_: pytest.fail("health replayed work"))
            assert transport.health()[row["binding_id"]] == {"pending_count": 1, "recovery_count": 0}
            assert restarted.health()[row["binding_id"]] == {"pending_count": 1, "recovery_count": 0}
        assert (ordinary[4].read_bytes(), provider.writes) == starts
        assert store.load_turn(row["session_id"], row["turn_id"]) == turn
        transport.admit("notes-app", provider.event("notes-app", "retire-unfinished", f"/{command}"))
        transport.reconcile()
        assert transport.health()[row["binding_id"]]["pending_count"] == 0
    finally:
        runtime.close()


@pytest.mark.parametrize("command", ["stop", "new"])
def test_original_quota_recovery_with_gate_remains_pending_after_verified_reply(ordinary, monkeypatch, command):  # noqa: F811
    from datetime import timedelta, timezone
    from loopx.capabilities.native_chat import external_conversations
    from loopx.capabilities.native_chat.codex_adapter import CodexAppServerAdapter

    store, runtime, _, _, _, fake, _ = ordinary
    source = fake.read_text().replace('"codexErrorInfo": "cyberPolicy"', '"codexErrorInfo": "usageLimitExceeded"')
    source = source.replace('            print(json.dumps({"method": "item/agentMessage/delta", "params": {"threadId": "durable-thread", "turnId": active_turn, "delta": "Partial answer."}}), flush=True)\n', '')
    fake.write_text(source)
    store, runtime, provider, transport = connect(ordinary)
    try:
        event = provider.event("notes-app", "quota-gate-visible", "typed terminal failure")
        transport.admit("notes-app", event)
        row = transport.core.pending()[0]
        failed = runtime.wait_for_turn(session_id=row["session_id"], turn_id=row["turn_id"], timeout_sec=10)
        assert failed["error_code"] == "usage_limit_exceeded"
        transport.reconcile()
        failed_path = store._turn_path(row["session_id"], row["turn_id"])
        failed_bytes = failed_path.read_bytes()
        runtime.adapters.pop(row["session_id"]).close_session()
        gate = {"kind": "authorization", "summary": "Original workspace authorization needs verification", "next_action": ""}
        answer = "Recovery reached the original authorization boundary.\n<loopx-review-json>" + json.dumps({
            "message": "", "gate": gate}) + "</loopx-review-json>"
        source = source.replace('if "typed terminal failure" in json.dumps(request.get("params") or {}):', 'if False:')
        start = source.index("        response = 'Visible runtime response.")
        end = source.index('        print(json.dumps({"method": "item/agentMessage/delta"', start)
        fake.write_text(source[:start] + f"        response = {answer!r}\n" + source[end:])
        clock = datetime.now(timezone.utc) + timedelta(seconds=61)
        class AfterBackoff(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock
        monkeypatch.setattr(external_conversations, "datetime", AfterBackoff)
        monkeypatch.setattr(CodexAppServerAdapter, "failed_request_observation", staticmethod(lambda **kw: {
            "thread_id": "durable-thread", "turn_id": kw["turn_id"], "codex_error_info": "usageLimitExceeded",
            "only_original_user_item": True, "quota_available": True,
            "quota_observed_at": int(clock.timestamp() * 1000) / 1000}))
        # The original scanner owns recovery; observing health never admits it.
        transport = LarkPrivateConversations(controller=runtime, runtime_root=store.root.parent,
            runner=provider, cli_bin="lark-cli")
        transport.reconcile()
        continued = transport.core.read_request(row["request_ref"])
        assert continued["turn_id"] != row["turn_id"] and continued["context_recovery"]["quota_attempt"] == 1
        completed = runtime.wait_for_turn(session_id=row["session_id"], turn_id=continued["turn_id"], timeout_sec=10)
        assert completed["status"] == "completed" and completed["response"]["gate"] == gate
        transport.reconcile_request(transport.root / f"{row['request_ref']}.json")
        assert transport.core.read_request(row["request_ref"])["delivery_verified"]
        restarted = LarkPrivateConversations(controller=runtime, runtime_root=store.root.parent,
            runner=provider, cli_bin="lark-cli")
        before = ordinary[4].read_bytes(), list(provider.writes)
        assert transport.health()[row["binding_id"]] == {"pending_count": 1, "recovery_count": 0}
        assert restarted.health()[row["binding_id"]] == {"pending_count": 1, "recovery_count": 0}
        restarted.reconcile()
        assert (ordinary[4].read_bytes(), provider.writes) == before
        assert failed_path.read_bytes() == failed_bytes
        assert store.load_turn(row["session_id"], continued["turn_id"]) == completed
        transport.admit("notes-app", provider.event("notes-app", "retire-quota-gate", f"/{command}"))
        transport.reconcile()
        assert transport.health()[row["binding_id"]]["pending_count"] == 0
    finally:
        runtime.close()


@pytest.mark.parametrize("fault", [None, "pool-exhausted", "native-effect", "unknown-native", "source-changed", "stop", "stale-capacity"])
def test_original_steward_quota_recovery_uses_its_api_pool_not_subscription(tmp_path, monkeypatch, fault):
    from datetime import timedelta, timezone
    from pathlib import Path
    from test_native_steward_private import steward, finish
    from test_chat_manager_model_adoption import configure
    from loopx.capabilities.native_chat import codex_auth, external_conversations
    from loopx.capabilities.native_chat.codex_adapter import CodexAppServerAdapter
    from loopx import chat_manager

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "packages/loopx-codex-provider-routing/src"))
    from loopx_codex_provider_routing.native import NativeAccounts

    fixture = steward.__wrapped__(tmp_path)
    store, runtime, provider, transport, binding, capture, _ = next(fixture)
    fake = Path(runtime.codex_bin)
    marker = tmp_path / "failed-once"
    source = fake.read_text().replace('"codexErrorInfo": "cyberPolicy"', '"codexErrorInfo": "usageLimitExceeded"')
    source = source.replace('        if "typed terminal failure" in json.dumps(request.get("params") or {}):',
        f'        if "typed terminal failure" in json.dumps(request.get("params") or {{}}) and not __import__("os").path.exists({str(marker)!r}):\n'
        f'            open({str(marker)!r}, "w").close()')
    source = source.replace('            print(json.dumps({"method": "item/agentMessage/delta", "params": {"threadId": "durable-thread", "turnId": active_turn, "delta": "Partial answer."}}), flush=True)\n', '')
    fake.write_text(source)
    try:
        event = provider.event("steward-app", "api-quota-original", "typed terminal failure")
        transport.admit("steward-app", event)
        row = transport.core.read_request(identity_ref(binding["provider_ref"], event["message_id"]))
        failed = finish(runtime, row)
        assert failed["error_code"] == "usage_limit_exceeded" and failed["delta_count"] == 0
        original = store._turn_path(row["session_id"], row["turn_id"]).read_bytes()
        transport.reconcile_request(transport.root / f"{row['request_ref']}.json")
        configure(runtime.coordination_runtime_root, model="gemini-4-argon-high", effort="high",
            endpoint="antigravity", policy="flexible")
        monkeypatch.setattr(runtime, "capabilities", lambda: [
            {"agent_id": name, "available": True, "adapter_kind": kind} for name, kind in
            [("codex", "codex_app_server"), ("antigravity", "antigravity_ls")]])
        owner = NativeAccounts(runtime.coordination_runtime_root)
        keys = owner.api_keys()
        for index in range(2):
            keys.add(expected_revision=index, label=f"Synthetic {index}", api_key="sk-" + "synthetic-only-" * 3 + str(index))
        owner.configure(expected_revision=0, enabled=False, slots=[], steward_api_role="primary")
        rows = keys.status()["keys"]
        for key in rows if fault == "pool-exhausted" else rows[:1]:
            keys.record(key["id"], status=429, code="insufficient_quota", model="gpt-6.1-sol", headers={})
        # Only paid upstream IO is substituted; the account owner, selection,
        # canonical queue, original source, delivery and provider readback are real.
        monkeypatch.setattr(codex_auth, "prepare_api_transport", lambda *_a, **_kw: None)
        clock = datetime.now(timezone.utc) + timedelta(seconds=65)
        class Ahead(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock
        monkeypatch.setattr(external_conversations, "datetime", Ahead)
        monkeypatch.setattr(CodexAppServerAdapter, "failed_request_observation", staticmethod(lambda **kw: None
            if fault == "unknown-native" else {"thread_id": "durable-thread", "turn_id": kw["turn_id"],
                "codex_error_info": "usageLimitExceeded", "only_original_user_item": fault != "native-effect",
                "quota_available": False, "quota_observed_at": clock.timestamp() - 1}))
        if fault == "stale-capacity":
            route = chat_manager.manager_session_quota_route
            monkeypatch.setattr(chat_manager, "manager_session_quota_route", lambda *a: {
                **route(*a), "observed_at": clock.timestamp() - 121})
        if fault == "source-changed":
            provider.messages[event["message_id"]]["content"] = "Changed original intent"
        elif fault == "stop":
            transport.admit("steward-app", provider.event("steward-app", "stop-api-quota", "/stop"))
        restarted = LarkPrivateConversations(controller=runtime, runtime_root=store.root.parent,
            runner=provider, cli_bin="lark-cli")
        restarted.reconcile()
        current = transport.core.read_request(row["request_ref"])
        assert store._turn_path(row["session_id"], row["turn_id"]).read_bytes() == original
        if fault is not None:
            assert current["turn_id"] == row["turn_id"] and not current.get("context_recovery")
            assert sum(json.loads(line)["method"] == "turn/start" for line in capture.read_text().splitlines()) == 1
            return
        assert current["turn_id"] != row["turn_id"] and current["context_recovery"]["quota_attempt"] == 1
        completed = finish(runtime, current)
        assert completed["status"] == "completed"
        restarted.reconcile_request(restarted.root / f"{row['request_ref']}.json")
        record = json.loads((restarted.root / f"{row['request_ref']}.json").read_text())
        delivery = record["deliveries"][f"terminal.recovery.{chat_manager.MANAGER_CONTEXT_VERSION}.quota.1"]
        assert delivery["verified"] and delivery["text"] == completed["response"]["message"]
        actual = provider.messages[delivery["attempt"]["message_ref"]]
        assert actual["chat_id"] == event["chat_id"] and actual["sender"]["id"] == provider.profile_apps["steward-app"]
        restarted.reconcile()
        assert sum(json.loads(line)["method"] == "turn/start" for line in capture.read_text().splitlines()) == 2
    finally:
        fixture.close()


def test_large_original_images_do_not_block_pending_work_or_connection_health(ordinary, monkeypatch):  # noqa: F811
    from test_lark_private_images import image_runner
    from test_chat_image_attachments import PNG_BYTES
    from loopx.chat_agent import CodexChatAgentError
    import loopx.chat_runtime as runtime_module
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler

    store, runtime, provider, transport = connect(ordinary)
    # Three accepted 3 MiB images fit Chat's 12 MiB input allowance, but their
    # base64 encoding exceeds the ordinary 8 MiB Core observation transport.
    image = PNG_BYTES + bytes(3 * 1024 * 1024 - len(PNG_BYTES))
    transport.runner = image_runner(provider, content=image)

    def quota_exhausted(*_args, **_kwargs):
        raise CodexChatAgentError("Synthetic exhausted model", error_code="usage_limit_exceeded", gate=None)

    monkeypatch.setattr(runtime_module, "start_attachment_turn", quota_exhausted)
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.chat_store, server.runtime_controller, server.verbose = store, runtime, False
    server.lark_private_conversations = transport
    server.lark_goal_topic_runtime = SimpleNamespace(health_snapshot=lambda: {}, close=lambda: None)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        event = provider.event("notes-app", "large-original-images",
            "Explain all three diagrams\n" + "\n".join(f"![Image](img_{i})" for i in range(3)), kind="post")
        assert transport.admit("notes-app", event)["status"] == "durably_accepted"
        row = transport.core.pending()[0]
        turn = runtime.wait_for_turn(session_id=row["session_id"], turn_id=row["turn_id"], timeout_sec=10)
        assert turn["status"] == "failed" and turn["error_code"] == "usage_limit_exceeded"
        assert len(turn["attachments"]) == 3 and sum(item["size"] for item in turn["attachments"]) == len(image) * 3
        # The ordinary reply worker still delivers its failure notice; delivery
        # does not retire the retained original work or authorize a replay.
        transport.reconcile()
        assert json.loads((transport.root / f"{row['request_ref']}.json").read_text())["status"] == "delivered"
        # An old Turn can retain a large diagnostic without changing its
        # structured failure. The read-only health route must still show the
        # pending original, even when that diagnostic cannot fit a Core RPC.
        store.update_turn(row["session_id"], row["turn_id"], error="synthetic diagnostic " * 450_000)

        def durable_state():
            return {path: path.read_bytes() for path in store.root.rglob("*")
                    if path.suffix in {".json", ".jsonl"}}

        before = durable_state()
        writes = list(provider.writes)
        connection = http.client.HTTPConnection(*server.server_address, timeout=15)
        try:
            connection.request("GET", "/api/chat/lark/private-conversations")
            response = connection.getresponse()
            assert response.status == 200
            result = json.loads(response.read())
        finally:
            connection.close()
        binding = next(item for item in result["connections"] if item["binding_id"] == row["binding_id"])
        assert binding["pending_count"] == 1 and binding["recovery_count"] == 0
        assert transport.core.quota_backlog_pending(row)
        assert transport.core.quota_backlog_observer()([row]) == [True]
        assert durable_state() == before and provider.writes == writes
        assert not ordinary[4].exists() or '"method": "turn/start"' not in ordinary[4].read_text()
        store.update_session(row["session_id"], status="closed")
        assert not transport.core.quota_backlog_pending(row)
        assert transport.core.quota_backlog_observer()([row]) == [False]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        runtime.close()


@pytest.mark.parametrize("command", ["stop", "new"])
def test_health_reads_source_controls_once_and_keeps_fresh_recovery(ordinary, monkeypatch, command):  # noqa: F811
    from loopx.capabilities.native_chat import external_conversations
    from loopx.control_plane import effect_runtime

    store, runtime, provider, transport = connect(ordinary)
    fake = ordinary[-2]
    fake.write_text(fake.read_text().replace('"codexErrorInfo": "cyberPolicy"',
        '"codexErrorInfo": "usageLimitExceeded"').replace(
        '            print(json.dumps({"method": "item/agentMessage/delta", "params": {"threadId": "durable-thread", "turnId": active_turn, "delta": "Partial answer."}}), flush=True)\n', ''))
    try:
        binding = transport._binding("notes-app")
        rows = []
        for index in range(8):
            event = provider.event("notes-app", f"health-quota-{index}", "typed terminal failure")
            transport.admit("notes-app", event)
            row = transport.core.read_request(identity_ref(binding["provider_ref"], event["message_id"]))
            turn = runtime.wait_for_turn(session_id=row["session_id"], turn_id=row["turn_id"], timeout_sec=10)
            assert turn["status"] == "failed" and turn["error_code"] == "usage_limit_exceeded"
            transport.reconcile()
            rows.append(row)

        def durable_state():
            paths = {path for root in (transport.root, transport.core.root) for path in root.glob("*.json")}
            paths.update(path for path in store.sessions_root.rglob("*") if path.suffix in {".json", ".jsonl"})
            return {path: path.read_bytes() for path in paths}

        before = durable_state()
        effects = ordinary[4].read_bytes(), list(provider.writes)
        reads = []
        revisions = []
        read_json = external_conversations._read_json
        fingerprint = effect_runtime._runtime_fingerprint

        def counted_read(path):
            if path.parent == transport.core.root and re.fullmatch(r"[a-f0-9]{24}", path.stem):
                reads.append(path)
            return read_json(path)

        monkeypatch.setattr(external_conversations, "_read_json", counted_read)
        def counted_revision():
            revisions.append(True)
            return fingerprint()

        monkeypatch.setattr(effect_runtime, "_runtime_fingerprint", counted_revision)
        expected = {"pending_count": len(rows), "recovery_count": 0}
        assert transport.health()[binding["binding_id"]] == expected
        # Correlation reads plus one complete control observation, rather than
        # a complete journal scan for each historical terminal request.
        assert len(reads) <= 3 * len(rows)
        assert len(revisions) <= 1  # One managed runtime revision for the health request.
        assert durable_state() == before
        assert (ordinary[4].read_bytes(), provider.writes) == effects

        # Durable control observations must keep all three existing fences.
        # These synthetic journal records are inputs, not new admissions.
        for index, change in enumerate([
            {"binding_id": "f" * 24},
            {"source": dict(rows[0]["source"], source_ref="e" * 24)},
            {"created_at": "2000-01-01T00:00:00+00:00"},
        ]):
            control_row = dict(rows[0], command=command, **change)
            (transport.core.root / f"{index + 1:024x}.json").write_text(json.dumps(control_row))
        # The journal reader already tolerates damaged JSON and ignores
        # non-request metadata; observation reuse must retain that behavior.
        (transport.core.root / ("d" * 24 + ".json")).write_text("{")
        (transport.core.root / "metadata.json").write_text(json.dumps(dict(rows[0], command=command)))
        assert transport.health()[binding["binding_id"]] == expected
        assert all(transport.core.quota_backlog_pending(row) for row in rows)

        # An actual later control must reach both a new health observation and
        # the default recovery check, independently of the previous snapshot.
        control = provider.event("notes-app", "health-control", f"/{command}")
        transport.admit("notes-app", control)
        assert transport.health()[binding["binding_id"]]["pending_count"] == 1  # undelivered control
        assert all(not transport.core.quota_backlog_pending(row) for row in rows)
        assert all(not transport.core.context_recovery_candidate(row["request_ref"]) for row in rows)
    finally:
        runtime.close()


def test_settings_health_reuses_core_correlation_but_observes_current_work(tmp_path, monkeypatch):
    from loopx.capabilities.native_chat import external_conversations
    from loopx.chat_store import _atomic_write_json
    from test_private_chat_backlog_projection import fixture_history

    store, core, transport, rows = fixture_history(tmp_path, count=2)
    partial, completed = rows
    binding = partial["binding_id"]
    sid = partial["session_id"]
    partial_path = transport.root / f"{partial['request_ref']}.json"
    completed_path = transport.root / f"{completed['request_ref']}.json"
    partial_request, completed_request = core.root / partial_path.name, core.root / completed_path.name
    response = {"schema_version": "loopx_chat_agent_response_v0",
                "message": "Original result", "proposals": []}
    store.update_turn(sid, partial["turn_id"], status="completed", response={
        **response, "unfinished_work": {"remaining": "Return the report", "dependency": "Worker",
                                        "effects_settled": True}})
    store.update_turn(sid, completed["turn_id"], status="completed", response=response)
    initial_partial = json.loads(partial_path.read_text())
    _atomic_write_json(partial_path, {**initial_partial, "deliveries": {
        "terminal": {"started": True, "verified": False}}})
    read_request = external_conversations._read_json
    request_reads = []

    def observe(path):
        request_reads.append(path)
        return read_request(path)

    monkeypatch.setattr(external_conversations, "_read_json", observe)
    assert transport.health()[binding] == {"pending_count": 1, "recovery_count": 1}
    assert set(request_reads) == {partial_request, completed_request}
    request_reads.clear()
    assert transport.health()[binding] == {"pending_count": 1, "recovery_count": 1}
    # Windows has no reliable ctime mutation stamp; its reader stays fresh.
    assert set(request_reads) == ({partial_request, completed_request} if sys.platform == "win32" else set())
    request_reads.clear()

    # Independent native updates must not wait for the Lark receipt to change.
    store.update_turn(sid, partial["turn_id"], status="completed", response=response)
    store.update_turn(sid, completed["turn_id"], status="failed", error_code="usage_limit_exceeded", response=None)
    assert transport.health()[binding] == {"pending_count": 1, "recovery_count": 0}
    assert set(request_reads) == ({partial_request, completed_request} if sys.platform == "win32" else set())
    request_reads.clear()

    # Atomically replace the journal with one from a different source. Core
    # must not pair the original delivery with this changed request.
    _atomic_write_json(completed_request, {**completed, "source": {"source_ref": "e" * 24}})
    assert transport.health()[binding] == {"pending_count": 0, "recovery_count": 0}
    assert completed_request in request_reads
    _atomic_write_json(completed_request, completed)
    assert transport.health()[binding] == {"pending_count": 1, "recovery_count": 0}

    _atomic_write_json(completed_path, {**json.loads(completed_path.read_text()), "status": "accepted",
                                       "deliveries": {"terminal": {"started": True, "verified": False}}})
    assert transport.health()[binding] == {"pending_count": 1, "recovery_count": 1}
    completed_path.unlink()
    assert transport.health()[binding] == {"pending_count": 0, "recovery_count": 0}


@pytest.mark.parametrize("scenario", ["concurrent-close", "transient-session-read"])
def test_settings_health_uses_current_session_for_each_batch(ordinary, monkeypatch, scenario):  # noqa: F811
    from pathlib import Path

    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
    from loopx.chat_store import CHAT_TURN_SCHEMA_VERSION, _atomic_write_json
    from test_private_chat_backlog_projection import durable_state

    store, runtime, provider, transport = connect(ordinary)
    binding_id = transport._binding("notes-app")["binding_id"]
    session = store.create_session(goal_id="demo", agent_id="codex", adapter_kind="codex_app_server",
                                   upstream_thread_id="native")
    sid = session["session_id"]
    session_path = store._session_path(sid)
    for index in range(66):
        ref, tid = f"{index:024x}", f"{index:032x}"
        row = {"request_ref": ref, "binding_id": binding_id, "source": {"source_ref": "c" * 24},
               "session_id": sid, "turn_id": tid, "command": None, "status": "accepted",
               "delivery_verified": True, "message": "Original user request",
               "created_at": "2026-01-01T00:00:00+00:00"}
        _atomic_write_json(transport.core.root / f"{ref}.json", row)
        _atomic_write_json(transport.root / f"{ref}.json", dict(row, status="delivered", deliveries={}))
        _atomic_write_json(store._turn_path(sid, tid), {
            "schema_version": CHAT_TURN_SCHEMA_VERSION, "session_id": sid, "turn_id": tid,
            "client_turn_id": f"external-{ref}", "status": "failed",
            "error_code": "usage_limit_exceeded", "response": None,
        })

    # Use the receipt scan's order, which varies across filesystems.
    paths = list(transport.root.glob("*.json"))
    answered = paths[:64] if scenario == "concurrent-close" else paths[2:]
    for path in answered:
        row = json.loads(path.read_bytes())
        store.update_turn(sid, row["turn_id"], status="completed", response={
            "schema_version": "loopx_chat_agent_response_v0", "message": "Already answered", "proposals": [],
        })

    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.chat_store, server.runtime_controller, server.verbose = store, runtime, False
    server.lark_private_conversations = transport
    server.lark_goal_topic_runtime = SimpleNamespace(health_snapshot=lambda: {}, close=lambda: None)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(method, path):
        connection = http.client.HTTPConnection(*server.server_address, timeout=15)
        try:
            connection.request(method, path)
            response = connection.getresponse()
            assert response.status == 200
            return json.loads(response.read())
        finally:
            connection.close()

    def close():
        assert request("DELETE", f"/api/chat/sessions/{sid}") == {
            "ok": True, "session_id": sid, "closed": True,
        }

    observed_fault = False
    try:
        if scenario == "concurrent-close":
            last_turn = json.loads(paths[-1].read_bytes())["turn_id"]
            load_turn = store.load_turn

            def close_during_batch(session_id, turn_id):
                nonlocal observed_fault
                if session_id == sid and turn_id == last_turn and not observed_fault:
                    observed_fault = True
                    close()
                return load_turn(session_id, turn_id)

            monkeypatch.setattr(store, "load_turn", close_during_batch)
        else:
            close()
            read_text = Path.read_text

            def fail_first_session_read(path, *args, **kwargs):
                nonlocal observed_fault
                if path == session_path and not observed_fault:
                    observed_fault = True
                    raise OSError("temporary Session read failure")
                return read_text(path, *args, **kwargs)

            monkeypatch.setattr(Path, "read_text", fail_first_session_read)

        before, provider_calls = durable_state(store), list(provider.calls)
        settings_path = "/api/chat/lark/private-conversations"
        authorization_path = f"{settings_path}?view=authorization"
        authorization = request("GET", authorization_path)
        assert not observed_fault
        result = request("GET", settings_path)
        assert observed_fault
        assert store.load_session(sid)["status"] == "closed"
        assert request("GET", authorization_path) == authorization
        before[str(session_path.relative_to(store.root))] = session_path.read_bytes()
        assert durable_state(store) == before and provider.calls == provider_calls
        binding = next(row for row in result["connections"] if row["binding_id"] == binding_id)
        assert (binding["pending_count"], binding["recovery_count"]) == (0, 0)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("fault", [None, "changed-source", "revoked", "stop", "unknown-native",
    "background", "later-user", "completed-later", "equivalent-later", "later-failed-effect",
    "settled-command", "later-settled-command", "unsettled-command",
    "reservation-crash", "post-enqueue-crash", "queued-newer", "queued-items", "queued-input", "private-path", "still-incomplete", "cold-native", "dead-native", "foreign-native", "failed-effect",
    "review-live", "review-live-source", "review-live-stop", "review-live-revoked", "review-live-equivalent", "review-live-unknown",
    "review-live-reservation-crash", "review-live-enqueue-crash", "transient-observation"])
def test_original_worker_continues_explicit_unfinished_project_work_once_after_restart(ordinary, monkeypatch, fault):  # noqa: F811
    """Reply-worker restart retains native ownership; native loss stays fenced."""
    from datetime import datetime, timedelta, timezone
    from pathlib import Path
    from loopx.capabilities.native_chat import external_conversations
    from loopx.extensions.lark.private_progress import presentation_phase

    store, runtime, contexts, _, capture, fake, workspace = ordinary
    contexts.workspace_grant = "workspace_write"
    store, runtime, provider, transport = connect(ordinary)
    binding = next(row for row in transport.bindings.read()["bindings"] if row["transport_ref"] == "notes-app")
    inventory = workspace / "native-inventory.json"
    artifact = workspace / "verified-result.txt"
    work = {"remaining": "Append the missing verified result", "dependency": "Temporary synthetic source outage", "effects_settled": True}
    if fault == "private-path":
        work["remaining"] = "Append to " + str(artifact)
    partial = "Partial result retained; remaining work is blocked.\n<loopx-review-json>" + json.dumps({
        "message": "", "unfinished_work": work}) + "</loopx-review-json>"
    source = fake.read_text()
    # Persisted history is readable from a cold client. Background terminals
    # belong to the process that loaded the thread, as in native app-server.
    source = source.replace("turn_count = 0", "loaded = False\nreview_only = False\nturn_count = 0")
    source = source.replace('    elif method in {"thread/start", "thread/resume"}:',
        '    elif method in {"thread/start", "thread/resume"}:\n        loaded = True\n'
        '        review_only = request["params"].get("permissions") == "loopx_workspace_only_read"')
    source = source.replace('        result = {"thread": {"id": "durable-thread"},',
        '        result = {"thread": {"id": "durable-thread"},\n'
        '                  "activePermissionProfile": {"id": request["params"].get("permissions")},\n'
        '                  "runtimeWorkspaceRoots": request["params"].get("runtimeWorkspaceRoots"),')
    source = source.replace("turn_count = 0", f"turn_count = len(json.load(open({str(inventory)!r}))[\"turns\"]) if __import__(\"os\").path.exists({str(inventory)!r}) else 0")
    start = source.index("        response = 'Visible runtime response.")
    end = source.index('        print(json.dumps({"method": "item/agentMessage/delta"', start)
    source = source[:start] + f'''        from pathlib import Path
        path = Path({str(inventory)!r})
        first = not path.exists()
        previous = json.loads(path.read_text()) if path.exists() else {{"turns": []}}
        response = {partial!r} if first or {fault == "still-incomplete"!r} else "Verified remaining work completed."
        artifact = Path({str(artifact)!r})
        if review_only:
            response = "Verified partial result. The remaining work is still pending."
        elif "Newer follow-up" in json.dumps(request["params"]):
            response = "Newer input processed."
        else:
            artifact.write_text((artifact.read_text() if artifact.exists() else "") + ("first\\n" if first else "remaining\\n"))
        path.write_text(json.dumps({{"turns": [{{"id": active_turn, "status": "completed"}}, *previous["turns"]], "items": [
            {{"turnId": active_turn, "item": {{"id": "user", "type": "userMessage"}}}},
            *([] if review_only else [
                {{"turnId": active_turn, "item": {{"id": "effect", "type": "fileChange", "status": "completed"}}}},
                {{"turnId": active_turn, "item": {{"id": "view", "type": "imageView", "path": str(artifact)}}}},
            ]),
            {{"turnId": active_turn, "item": {{"id": "answer", "type": "agentMessage", "phase": "final_answer", "text": response}}}},
            *previous.get("items", [])]}}))
''' + source[end:]
    start = source.index('    elif method == "thread/turns/list":')
    end = source.index('    else:\n        result = {}', start)
    source = source[:start] + f'''    elif method in {{"thread/turns/list", "thread/items/list"}}:
        snapshot = json.load(open({str(inventory)!r}))
        result = {{"data": snapshot["turns"] if method == "thread/turns/list" else
            [row for row in snapshot["items"] if row["turnId"] == request["params"]["turnId"]]}}
    elif method == "thread/backgroundTerminals/list":
        if not loaded:
            print(json.dumps({{"id": request["id"], "error": {{"code": -32600, "message": "thread not found"}}}}), flush=True)
            continue
        result = {{"data": [{{"processId": "still-live"}}] if {fault == "background"!r} else []}}
''' + source[end:]
    fake.write_text(source)
    event = provider.event("notes-app", "unfinished-source", "Complete this synthetic original workspace request.")
    transport.admit("notes-app", event)
    ref = identity_ref(binding["provider_ref"], event["message_id"])
    original = transport.core.read_request(ref)
    turn = runtime.wait_for_turn(session_id=original["session_id"], turn_id=original["turn_id"], timeout_sec=10)
    assert turn["status"] == "completed"
    if fault == "private-path":
        assert str(workspace) not in turn["response"]["unfinished_work"]["remaining"]
    else:
        assert turn["response"]["unfinished_work"] == work
    assert transport.reconcile() == 1
    path = transport.root / (ref + ".json")
    original_delivery = json.loads(path.read_text())["deliveries"]["terminal"]
    original_turn_bytes = store._turn_path(original["session_id"], original["turn_id"]).read_bytes()
    session_before = store.load_session(original["session_id"])
    Path(session_before["codex_home"]).mkdir(parents=True, exist_ok=True)
    if fault in {"completed-later", "equivalent-later", "later-failed-effect", "later-settled-command"}:
        message = event["content"] if fault == "equivalent-later" else "Newer follow-up: a different completed request."
        later_event = provider.event("notes-app", "later-correlated", message)
        transport.admit("notes-app", later_event)
        later_ref = identity_ref(binding["provider_ref"], later_event["message_id"])
        later = transport.core.read_request(later_ref)
        later_turn = runtime.wait_for_turn(session_id=later["session_id"], turn_id=later["turn_id"], timeout_sec=10)
        assert later_turn["status"] == "completed"
        transport.reconcile_request(transport.root / (later_ref + ".json"))
        if fault in {"later-failed-effect", "later-settled-command"}:
            snapshot = json.loads(inventory.read_text())
            effect = next(row["item"] for row in snapshot["items"] if row["turnId"] == later_turn["upstream_turn_id"]
                and row["item"]["type"] == "fileChange")
            effect["status"] = "failed"
            if fault == "later-settled-command":
                effect.update(type="commandExecution", exitCode=1, startedAtMs=1000,
                    completedAtMs=1001, aggregatedOutput="Process exited with code 1.")
            inventory.write_text(json.dumps(snapshot))
    if fault in {"settled-command", "unsettled-command"}:
        snapshot = json.loads(inventory.read_text())
        snapshot["items"][1]["item"].update(type="commandExecution", status="failed", exitCode=1,
            startedAtMs=1000, completedAtMs=1001, aggregatedOutput="Process exited with code 1.")
        if fault == "unsettled-command":
            snapshot["items"][1]["item"]["exitCode"] = None
        inventory.write_text(json.dumps(snapshot))
    if fault == "cold-native" or str(fault).startswith("review-live"):
        runtime.adapters.pop(original["session_id"]).close_session()
    elif fault == "dead-native":
        runtime.adapters[original["session_id"]].close_session()
    elif fault == "foreign-native":
        runtime.adapters[original["session_id"]].session.thread_id = "foreign-thread"
    before_recovery_calls = [json.loads(line) for line in capture.read_text().splitlines()]
    clock = datetime.now(timezone.utc) + timedelta(seconds=61)
    class AfterBackoff(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock
    monkeypatch.setattr(external_conversations, "datetime", AfterBackoff)
    if fault == "changed-source":
        provider.messages[event["message_id"]]["content"] = "Edited source"
    if fault == "revoked":
        transport.bindings.disconnect(binding["binding_id"], expected_revision=transport.bindings.read()["revision"])
    if fault == "stop":
        transport.admit("notes-app", provider.event("notes-app", "stop-later", "/stop"))
    if fault in {"unknown-native", "later-user", "failed-effect"}:
        snapshot = json.loads(inventory.read_text())
        if fault == "unknown-native":
            snapshot["items"].append({"turnId": turn["upstream_turn_id"], "item": {"id": "unknown", "type": "futureEffect"}})
        elif fault == "failed-effect":
            snapshot["items"][1]["item"]["status"] = "failed"
        else:
            snapshot["turns"].insert(0, {"id": "newer-user", "status": "completed"})
        inventory.write_text(json.dumps(snapshot))

    def worker():
        return LarkPrivateConversations(controller=runtime, runtime_root=store.root.parent, runner=provider, cli_bin="lark-cli")
    selected = worker()
    if fault in {"reservation-crash", "post-enqueue-crash", "queued-newer", "queued-items", "queued-input"}:
        enqueue = runtime.enqueue_turn
        resume = runtime.resume_session_queue
        if fault in {"queued-newer", "queued-items", "queued-input"}:
            # Hold dispatch before enqueue, rather than relying on a slow
            # adapter startup to let the newer-input fixture win a race.
            monkeypatch.setattr(runtime, "resume_session_queue", lambda **kwargs: None)
        def crash(**kwargs):
            if fault in {"post-enqueue-crash", "queued-newer", "queued-items", "queued-input"}:
                enqueue(**kwargs)
            raise OSError("synthetic continuation admission crash")
        monkeypatch.setattr(runtime, "enqueue_turn", crash)
        with pytest.raises(OSError, match="admission crash"):
            selected.reconcile()
        monkeypatch.setattr(runtime, "enqueue_turn", enqueue)
        if fault == "queued-newer":
            snapshot = json.loads(inventory.read_text())
            snapshot["turns"].insert(0, {"id": "newer-after-reservation", "status": "completed"})
            inventory.write_text(json.dumps(snapshot))
        if fault == "queued-items":
            snapshot = json.loads(inventory.read_text())
            snapshot["items"][1]["item"]["output"] = "Changed after admission"
            inventory.write_text(json.dumps(snapshot))
        if fault == "queued-input":
            transport.admit("notes-app", provider.event("notes-app", "newer-input", "Newer follow-up: inspect this correction first."))
        if fault in {"queued-newer", "queued-items", "queued-input"}:
            monkeypatch.setattr(runtime, "resume_session_queue", resume)
        selected = worker()
    artifact_before_recovery = artifact.read_bytes()
    if fault == "transient-observation":
        from loopx.extensions.lark import private_conversations

        owner = runtime.adapters[original["session_id"]]
        observe = owner.completed_work_observation
        observations = 0
        monotonic = private_conversations.time.monotonic
        elapsed = 0
        monkeypatch.setattr(private_conversations, "time", SimpleNamespace(
            monotonic=lambda: monotonic() + elapsed, time=private_conversations.time.time))

        def unavailable_once(**kwargs):
            nonlocal observations
            observations += 1
            return None if observations == 1 else observe(**kwargs)

        monkeypatch.setattr(owner, "completed_work_observation", unavailable_once)
        selected.reconcile()
        # An inconclusive inventory is not a reason to replace the original
        # writable owner with a model review of its already delivered answer.
        assert selected.core.read_request(ref)["turn_id"] == original["turn_id"]
        assert runtime.adapters[original["session_id"]] is owner and owner.healthcheck()
        assert artifact.read_bytes() == artifact_before_recovery
        assert observations == 1
        assert selected.core.quota_backlog_pending(selected.core.read_request(ref))
        selected.reconcile()
        assert observations == 1, "The original reply worker still respects its probe backoff"
        elapsed = 61
        clock += timedelta(seconds=61)
    selected.reconcile()
    continued = selected.core.read_request(ref)
    reviewed = fault == "cold-native" or str(fault).startswith("review-live")
    if fault in {"changed-source", "revoked", "stop", "unknown-native", "background", "later-user", "dead-native", "foreign-native",
                 "failed-effect", "equivalent-later", "later-failed-effect", "unsettled-command"}:
        assert continued["turn_id"] == original["turn_id"] and not continued.get("context_recovery")
        assert artifact.read_bytes() == artifact_before_recovery
        if fault in {"failed-effect", "equivalent-later", "later-failed-effect", "unsettled-command"}:
            assert runtime.adapters[original["session_id"]].healthcheck()
    elif reviewed:
        assert continued["turn_id"] != original["turn_id"]
        assert continued["session_id"] == original["session_id"]
        recovery = continued["context_recovery"]
        assert recovery["read_only_review"] is True and "work_attempt" not in recovery
        assert recovery["native_observation"]["completed_result_review"] is True
        assert recovery["native_observation"].get("all_effects_settled") is not True
        review = runtime.wait_for_turn(session_id=continued["session_id"], turn_id=continued["turn_id"], timeout_sec=10)
        assert review["status"] == "completed", review
        assert not review["response"].get("unfinished_work")
        assert artifact.read_bytes() == artifact_before_recovery
        selected.reconcile_request(path)
        deliveries = json.loads(path.read_text())["deliveries"]
        assert deliveries["terminal"] == original_delivery
        returned = deliveries[presentation_phase(continued, "terminal")]
        assert returned["verified"] and "The remaining work is still pending." in returned["text"]
        writes = list(provider.writes)
        assert not worker().core.context_recovery_candidate(ref)
        worker().reconcile()
        assert provider.writes == writes

        recovery_calls = [json.loads(line) for line in capture.read_text().splitlines()][len(before_recovery_calls):]
        resume, = [row["params"] for row in recovery_calls if row.get("method") == "thread/resume"]
        assert resume["threadId"] == session_before["upstream_thread_id"]
        profile = "loopx_workspace_only_read"
        assert resume["permissions"] == profile and resume["runtimeWorkspaceRoots"] == [str(workspace.resolve())]
        permissions = resume["config"]["permissions"][profile]
        assert permissions["filesystem"][":workspace_roots"] == {".": "read"}
        assert permissions["network"]["enabled"] is False
        assert sum(row.get("method") == "turn/start" for row in recovery_calls) == 1
        if str(fault).startswith("review-live"):
            # An ordinary source request naturally restores the writable owner.
            # Merely opening a cold reader must never manufacture effects proof.
            later_message = event["content"] if fault == "review-live-equivalent" else "Newer follow-up: inspect another result."
            later_event = provider.event("notes-app", "after-result-review", later_message)
            transport.admit("notes-app", later_event)
            later_ref = identity_ref(binding["provider_ref"], later_event["message_id"])
            later = transport.core.read_request(later_ref)
            assert runtime.wait_for_turn(session_id=later["session_id"], turn_id=later["turn_id"], timeout_sec=10)["status"] == "completed"
            transport.reconcile_request(transport.root / f"{later_ref}.json")
            before = artifact.read_bytes()
            if fault == "review-live-source":
                provider.messages[event["message_id"]]["content"] = "Changed original source"
            elif fault == "review-live-stop":
                transport.admit("notes-app", provider.event("notes-app", "after-review-stop", "/stop"))
            elif fault == "review-live-revoked":
                transport.bindings.disconnect(binding["binding_id"], expected_revision=transport.bindings.read()["revision"])
            elif fault == "review-live-unknown":
                snapshot = json.loads(inventory.read_text())
                snapshot["items"].append({"turnId": turn["upstream_turn_id"], "item": {"id": "unknown", "type": "futureEffect"}})
                inventory.write_text(json.dumps(snapshot))
            selected = worker()
            if fault in {"review-live-reservation-crash", "review-live-enqueue-crash"}:
                enqueue = runtime.enqueue_turn
                def crash_after_review(**kwargs):
                    if fault == "review-live-enqueue-crash":
                        enqueue(**kwargs)
                    raise OSError("synthetic post-review admission crash")
                monkeypatch.setattr(runtime, "enqueue_turn", crash_after_review)
                with pytest.raises(OSError, match="post-review admission crash"):
                    selected.reconcile()
                monkeypatch.setattr(runtime, "enqueue_turn", enqueue)
                selected = worker()
            selected.reconcile()
            resumed = selected.core.read_request(ref)
            if fault in {"review-live", "review-live-reservation-crash", "review-live-enqueue-crash"}:
                assert resumed["turn_id"] != continued["turn_id"]
                assert resumed["context_recovery"]["work_attempt"] == 1
                assert resumed["context_recovery"]["failed_turn_id"] == original["turn_id"]
                assert runtime.wait_for_turn(session_id=resumed["session_id"], turn_id=resumed["turn_id"], timeout_sec=10)["status"] == "completed"
                selected.reconcile_request(path)
                assert artifact.read_text() == "first\nremaining\n"
                assert json.loads(path.read_text())["deliveries"][presentation_phase(resumed, "terminal")]["verified"]
                after_writes = list(provider.writes)
                worker().reconcile()
                assert provider.writes == after_writes
            else:
                assert resumed["turn_id"] == continued["turn_id"]
                assert artifact.read_bytes() == before
            assert store._turn_path(original["session_id"], original["turn_id"]).read_bytes() == original_turn_bytes
    else:
        assert continued["turn_id"] != original["turn_id"]
        assert continued["session_id"] == original["session_id"]
        assert continued["context_recovery"]["work_attempt"] == 1
        interrupted = fault in {"queued-newer", "queued-items", "queued-input"}
        assert runtime.wait_for_turn(session_id=continued["session_id"], turn_id=continued["turn_id"], timeout_sec=10)["status"] == ("failed" if interrupted else "completed")
        if fault == "queued-input":
            queued = [item for item in selected.core.pending() if item["request_ref"] != ref and item.get("command") is None]
            assert len(queued) == 1
            assert runtime.wait_for_turn(session_id=queued[0]["session_id"], turn_id=queued[0]["turn_id"], timeout_sec=10)["status"] == "completed"
            worker().reconcile()
        selected.reconcile_request(path)
        deliveries = json.loads(path.read_text())["deliveries"]
        assert deliveries["terminal"] == original_delivery
        assert deliveries[presentation_phase(continued, "terminal")]["verified"]
        assert artifact.read_text() == ("first\n" if interrupted else "first\nremaining\n")
        assert not worker().core.context_recovery_candidate(ref)
        writes = list(provider.writes)
        worker().reconcile()
        assert provider.writes == writes
        after = store.load_session(original["session_id"])
        assert after["project_context"] == session_before["project_context"]
        assert after["upstream_thread_id"] == session_before["upstream_thread_id"]
        starts = [json.loads(line)["params"] for line in capture.read_text().splitlines() if json.loads(line).get("method") == "turn/start"]
        assert len(starts) == (1 if fault in {"queued-newer", "queued-items"} else 3 if fault in {"completed-later", "later-settled-command"} else 2)
        if not interrupted:
            assert "Do only the remaining authorized work" in starts[-1]["input"][0]["text"]
        if fault == "queued-input":
            assert "Newer follow-up" in starts[-1]["input"][0]["text"]
            assert "Core admitted one continuation" not in starts[-1]["input"][0]["text"]
        snapshot = ordinary[3]("/api/chat/sessions/" + original["session_id"])[1]
        bodies = [message["text"] for message in snapshot["messages"] if message["role"] == "agent"]
        assert turn["response"]["message"] in bodies
        if fault is None:
            assert "Verified remaining work completed." in bodies
    assert store._turn_path(original["session_id"], original["turn_id"]).read_bytes() == original_turn_bytes
    after_recovery_calls = [json.loads(line) for line in capture.read_text().splitlines()]
    restored_owner = int(str(fault).startswith("review-live"))
    expected_new = {"initialize": int(reviewed) + int(fault == "cold-native") + 2 * restored_owner,
        "thread/start": 0, "thread/resume": int(reviewed) + restored_owner}
    for method, count in expected_new.items():
        assert sum(row.get("method") == method for row in after_recovery_calls[len(before_recovery_calls):]) == count


def _app_projection_journey(fixture):
    store, runtime, provider, transport = connect(fixture)
    event = provider.event("notes-app", "original-app-chat", "Original source conversation")
    transport.admit("notes-app", event)
    original = next(row for row in transport.core.pending() if row["message"] == event["content"])
    runtime.wait_for_turn(session_id=original["session_id"], turn_id=original["turn_id"], timeout_sec=10)
    transport.reconcile()
    before = len(provider.writes)
    turns = []
    for client in ["app-input-one", "app-input-two"]:
        status, result = fixture[3](f"/api/chat/sessions/{original['session_id']}/turns",
            {"message": "Same App input", "client_turn_id": client})
        assert status == 202
        assert runtime.wait_for_turn(session_id=original["session_id"],
            turn_id=result["turn_id"], timeout_sec=10)["status"] == "completed"
        turns.append(result["turn_id"])
    return store, runtime, provider, transport, original, turns, before


def _drain_app_projection(transport, predicate):
    deadline = time.monotonic() + 10
    while not predicate():
        assert time.monotonic() < deadline, transport.health()
        transport.reconcile()
        time.sleep(.02)


@pytest.mark.parametrize("fault", ["truncated_json", "invalid_utf8", "vanished_during_scan"])
def test_unreadable_private_delivery_does_not_hold_other_original_replies(ordinary, monkeypatch, fault):  # noqa: F811
    from loopx.extensions.lark import private_delivery_discovery

    _store, runtime, provider, transport = connect(ordinary)
    originals = {}
    try:
        for profile in ("notes-app", "steward-app"):
            event = provider.event(profile, f"independent-{profile}", f"Complete the original {profile} request")
            transport.admit(profile, event)
            request = identity_ref(transport._binding(profile)["provider_ref"], event["message_id"])
            native = transport.core.read_request(request)
            result = runtime.wait_for_turn(session_id=native["session_id"], turn_id=native["turn_id"], timeout_sec=10)
            assert result["status"] == "completed"
            originals[profile] = (transport.root / f"{request}.json", event)

        held, held_event = originals["notes-app"]
        healthy, healthy_event = originals["steward-app"]
        saved = held.read_bytes()
        with monkeypatch.context() as damage:
            if fault == "vanished_during_scan":
                fingerprint = private_delivery_discovery.file_change_fingerprint

                def vanished(path):
                    if path == held:
                        raise FileNotFoundError(path)
                    return fingerprint(path)

                damage.setattr(private_delivery_discovery, "file_change_fingerprint", vanished)
            else:
                held.write_bytes(b'{"status":' if fault == "truncated_json" else b"\xff")
            unchanged = held.read_bytes()
            for discover in (transport.pending_body_paths, transport.pending_delivery_paths):
                assert discover() == [healthy]

            requests = ordinary[4].read_bytes()
            writes_before = len(provider.writes)
            transport.reconcile()
            final = json.loads(healthy.read_text())["deliveries"]["terminal"]
            assert final["verified"]
            assert provider.messages[final["attempt"]["message_ref"]]["chat_id"] == healthy_event["chat_id"]
            assert held.read_bytes() == unchanged
            assert all(profile != "notes-app" for profile, _ in provider.writes[writes_before:])

        if fault != "vanished_during_scan":
            held.write_bytes(saved)
        transport.reconcile()
        final = json.loads(held.read_text())["deliveries"]["terminal"]
        assert final["verified"]
        assert provider.messages[final["attempt"]["message_ref"]]["chat_id"] == held_event["chat_id"]
        sent = list(provider.writes)
        transport.reconcile()
        assert provider.writes == sent
        assert ordinary[4].read_bytes() == requests
    finally:
        runtime.close()


def test_app_messages_and_answers_reach_the_original_chat_once_without_new_execution(ordinary):  # noqa: F811
    store, runtime, provider, transport, original, turns, before = _app_projection_journey(ordinary)
    sid = original["session_id"]
    canonical = [message for message in store.messages(sid) if message.get("turn_id") in turns]
    requests_before = ordinary[4].read_text()
    try:
        _drain_app_projection(transport, lambda: len(provider.writes) >= before + len(canonical))
        projected = provider.writes[before:]
        assert len(projected) == len(canonical)
        # Repeated content remains two distinct messages, in canonical order.
        for (profile, text), message in zip(projected, canonical):
            assert profile == "notes-app" and message["text"] in text
            assert ("你（App）" if message["role"] == "user" else "LoopX（App）") in text
        restarted = LarkPrivateConversations(controller=runtime, runtime_root=transport.runtime_root,
            runner=provider, cli_bin="lark-cli")
        for _ in range(3):
            restarted.reconcile()
        assert provider.writes[before:] == projected
        assert ordinary[4].read_text() == requests_before
        assert len(store.list_sessions()) == 1
        # Provider echoes are Bot-authored, never a second human input.
        assert len([m for m in store.messages(sid) if m.get("origin") == "web"]) == 2
        user_mirrors = {ref for ref, message in provider.messages.items()
            if "你（App）" in json.dumps(message.get("body", {}), ensure_ascii=False)}
        assert len(user_mirrors) == 2
        assert {(profile, ref, emoji) for profile, ref, emoji in provider.reaction_creates
                if ref in user_mirrors} == {("notes-app", ref, "Get") for ref in user_mirrors}
        # Completed App inputs retain receipt feedback, never a processing
        # reaction on the assistant mirror or a different original request.
        assert {ref for _profile, ref, _emoji in provider.reaction_creates if ref.startswith("om_out_")} == user_mirrors
    finally:
        runtime.close()


def test_app_projection_discovers_a_restored_transcript_with_the_same_size_and_mtime(ordinary):  # noqa: F811
    import os

    store, runtime, provider, transport, original, turns, before = _app_projection_journey(ordinary)
    sid = original["session_id"]
    transcript = store.sessions_root / sid / "messages.jsonl"
    canonical = [message for message in store.messages(sid) if message.get("turn_id") in turns]
    starts = ordinary[4].read_bytes()
    try:
        full = transcript.read_bytes()
        old = b"".join(line for line in full.splitlines(keepends=True)
                       if json.loads(line).get("turn_id") not in turns)
        assert old.endswith(b"\n") and len(old) < len(full)
        # Simulate the earlier view before a same-size atomic restore.
        transcript.write_bytes(old[:-1] + b" " * (len(full) - len(old)) + b"\n")
        previous = transcript.stat()
        transport._web_projection_scan_at = 0
        assert transport._web_projection_paths() == []
        assert len(provider.writes) == before

        restored = transcript.with_name("restored-messages.jsonl")
        restored.write_bytes(full)
        os.utime(restored, ns=(previous.st_atime_ns, previous.st_mtime_ns))
        restored.replace(transcript)
        observed = transcript.stat()
        assert (observed.st_mtime_ns, observed.st_size) == (previous.st_mtime_ns, previous.st_size)
        transport._web_projection_scan_at = 0
        path, = transport._web_projection_paths()
        _drain_app_projection(transport, lambda: len(provider.writes) == before + len(canonical))

        deliveries = json.loads(path.read_text())["deliveries"]
        assert {key for key in deliveries if key.startswith("web.")} == {
            "web." + message["message_id"] for message in canonical}
        for message, (profile, text) in zip(canonical, provider.writes[before:], strict=True):
            receipt = deliveries["web." + message["message_id"]]
            assert profile == "notes-app" and message["text"] in text and receipt["verified"]
            delivered = provider.messages[receipt["attempt"]["message_ref"]]
            assert delivered["chat_id"] == "oc_notes_app"
        transport._web_projection_scan_at = 0
        transport.reconcile()
        assert len(provider.writes) == before + len(canonical)
        assert ordinary[4].read_bytes() == starts and transcript.read_bytes() == full
    finally:
        runtime.close()


@pytest.mark.parametrize("recovery", ["readback", "revoked"])
def test_app_failed_turn_mirrors_only_its_terminal_notice_to_the_original_chat(ordinary, monkeypatch, recovery):  # noqa: F811
    store, runtime, provider, transport = connect(ordinary)
    profile = "steward-app"
    event = provider.event(profile, "original-app-failure-source", "Original steward chat")
    transport.admit(profile, event)
    original = transport.core.pending()[0]
    sid = original["session_id"]
    try:
        runtime.wait_for_turn(session_id=sid, turn_id=original["turn_id"], timeout_sec=10)
        transport.reconcile()
        with monkeypatch.context() as queued:
            queued.setattr(runtime, "resume_session_queue", lambda **_: None)
            status, turn = ordinary[3](f"/api/chat/sessions/{sid}/turns", {
                "message": "App request with typed terminal failure", "client_turn_id": "app-failed-once"})
        assert status == 202 and turn["status"] == "queued"
        turn_id = turn["turn_id"]
        stale = store.append_message(sid, role="error", text="Unconfirmed synthetic stop detail", turn_id=turn_id)
        _drain_app_projection(transport, lambda: any("你（App）" in text for _, text in provider.writes))
        transport.reconcile()
        path, = (transport.root / "web-projections").glob("*.json")
        original_outbox = json.loads(path.read_text())
        assert original_outbox["status"] == "delivered"
        runtime.resume_session_queue(session_id=sid, work_dir=ordinary[-1], objective="")
        failed = runtime.wait_for_turn(session_id=sid, turn_id=turn_id, timeout_sec=10)
        assert failed["status"] == "failed" and failed["error_code"] == "cyber_policy", failed
        errors = [item for item in store.messages(sid) if item["turn_id"] == turn_id
                  and item["role"] == "error" and item["text"] == failed["error"]]
        canonical, = errors
        duplicate = store.append_message(sid, role="error", text=failed["error"], turn_id=turn_id)
        phase = "web." + canonical["message_id"]
        turns_before = ordinary[4].read_bytes()
        transcript = (store.sessions_root / sid / "messages.jsonl").read_bytes()
        before = len(provider.writes)

        def worker():
            return LarkPrivateConversations(controller=runtime, runtime_root=transport.runtime_root,
                runner=provider, cli_bin="lark-cli")

        if recovery == "revoked":
            binding = transport._binding(profile)
            transport.bindings.disconnect(binding["binding_id"], expected_revision=transport.bindings.read()["revision"])
            restarted = worker()
            for _ in range(3):
                restarted.reconcile()
            assert len(provider.writes) == before
            assert phase not in json.loads(path.read_text())["deliveries"]
        else:
            provider.verify_replies = False
            _drain_app_projection(worker(), lambda: len(provider.writes) == before + 1)
            attempted = json.loads(path.read_text())["deliveries"][phase]
            assert attempted["attempt"] and not attempted.get("verified")
            provider.verify_replies = True
            restarted = worker()
            _drain_app_projection(restarted, lambda: json.loads(path.read_text())["deliveries"][phase].get("verified"))
            restarted.reconcile()
            outbox = json.loads(path.read_text())
            assert outbox["status"] == "delivered"
            assert outbox["deliveries"][phase]["attempt"] == attempted["attempt"]
            for key, old in original_outbox["deliveries"].items():
                assert {k: v for k, v in outbox["deliveries"][key].items() if k != "feedback"} == {
                    k: v for k, v in old.items() if k != "feedback"}
            assert "web." + stale["message_id"] not in outbox["deliveries"]
            assert "web." + duplicate["message_id"] not in outbox["deliveries"]
            assert len(provider.writes) == before + 1
            sender, body = provider.writes[-1]
            assert sender == profile and "上游安全策略拦截" in body and failed["completed_at"] in body
            assert failed["error"] not in body and stale["text"] not in body
            mirror = provider.messages[attempted["attempt"]["message_ref"]]
            assert mirror["chat_id"] == event["chat_id"]
        assert ordinary[4].read_bytes() == turns_before
        assert (store.sessions_root / sid / "messages.jsonl").read_bytes() == transcript
        assert len(store.list_sessions()) == 1
    finally:
        runtime.close()


@pytest.mark.parametrize("terminal", ["completed", "failed", "interrupted"])
def test_app_mirror_feedback_tracks_turn_only_changes_and_separate_sessions(ordinary, monkeypatch, tmp_path, terminal):  # noqa: F811
    gate = tmp_path / "finish-app-input"
    fake = ordinary[-2]
    marker = '        if "typed terminal failure" in json.dumps(request.get("params") or {}):'
    source = fake.read_text()
    assert marker in source
    fake.write_text(source.replace(marker,
        '        from pathlib import Path\n'
        '        if "App lifecycle" in json.dumps(request.get("params") or {}):\n'
        f'            while {terminal != "interrupted"!r} and not Path({str(gate)!r}).exists():\n'
        '                time.sleep(.01)\n' + marker))
    store, runtime, provider, transport = connect(ordinary)
    originals, turns = {}, {}
    try:
        for profile in ("notes-app", "steward-app"):
            event = provider.event(profile, f"lifecycle-{profile}", "Original source")
            transport.admit(profile, event)
            original = next(row for row in transport.core.pending()
                            if row["binding_id"] == transport._binding(profile)["binding_id"])
            runtime.wait_for_turn(session_id=original["session_id"], turn_id=original["turn_id"], timeout_sec=10)
            transport.reconcile()
            originals[profile] = original
        original_bytes = {row["request_ref"]: (transport.root / f"{row['request_ref']}.json").read_bytes()
                          for row in originals.values()}
        captured = ordinary[4].read_bytes()
        with monkeypatch.context() as queued:
            queued.setattr(runtime, "resume_session_queue", lambda **_: None)
            for profile, original in originals.items():
                text = f"App lifecycle {profile}" + (
                    " typed terminal failure" if terminal == "failed" else
                    " wait for interrupt" if terminal == "interrupted" else "")
                status, turn = ordinary[3](f"/api/chat/sessions/{original['session_id']}/turns",
                    {"message": text, "client_turn_id": f"app-lifecycle-{profile}"})
                assert status == 202 and turn["status"] == "queued"
                turns[profile] = turn
        transport._web_projection_scan_at = 0
        _drain_app_projection(transport, lambda: sum("你（App）" in text for _, text in provider.writes) == 2)
        transport.reconcile()  # Finish body delivery while both inputs remain queued.
        targets = {profile: next(ref for ref, row in provider.messages.items()
            if f"App lifecycle {profile}" in json.dumps(row.get("body", {}), ensure_ascii=False)
            and "你（App）" in json.dumps(row.get("body", {}), ensure_ascii=False)) for profile in originals}
        assert {(profile, ref, emoji) for profile, ref, emoji in provider.reactions.values() if ref in targets.values()} == {
            (profile, ref, "Get") for profile, ref in targets.items()}
        assert ordinary[4].read_bytes() == captured  # Feedback did not start either queued input.
        transcripts = {row["session_id"]: (store.sessions_root / row["session_id"] / "messages.jsonl").read_bytes()
                       for row in originals.values()}
        for profile, original in originals.items():
            runtime.resume_session_queue(session_id=original["session_id"], work_dir=ordinary[-1], objective="")
        deadline = time.monotonic() + 10
        while any(store.load_turn(row["session_id"], turns[profile]["turn_id"])["status"] != "running"
                  for profile, row in originals.items()):
            assert time.monotonic() < deadline
            time.sleep(.01)
        assert all((store.sessions_root / sid / "messages.jsonl").read_bytes() == data
                   for sid, data in transcripts.items())
        transport._web_projection_scan_at = 0
        _drain_app_projection(transport, lambda: all((profile, ref, "OnIt") in provider.reactions.values()
                                                   for profile, ref in targets.items()))
        transport.reconcile()
        if terminal == "interrupted":
            for profile, row in originals.items():
                runtime.interrupt_turn(session_id=row["session_id"], turn_id=turns[profile]["turn_id"])
        else:
            gate.touch()
        for profile, row in originals.items():
            ended = runtime.wait_for_turn(session_id=row["session_id"], turn_id=turns[profile]["turn_id"], timeout_sec=10)
            assert ended["status"] == terminal
        transport._web_projection_scan_at = 0
        _drain_app_projection(transport, lambda: not any(ref in targets.values() and emoji == "OnIt"
                                                       for _, ref, emoji in provider.reactions.values()))
        if terminal == "failed":
            _drain_app_projection(transport, lambda: sum("App 执行记录" in body for _, body in provider.writes) == 2)
        assert {(profile, ref, emoji) for profile, ref, emoji in provider.reactions.values() if ref in targets.values()} == {
            (profile, ref, "Get") for profile, ref in targets.items()}
        starts = ordinary[4].read_bytes()
        sent, reactions = list(provider.writes), list(provider.reaction_creates)
        restarted = LarkPrivateConversations(controller=runtime, runtime_root=transport.runtime_root,
            runner=provider, cli_bin="lark-cli")
        for _ in range(3):
            restarted.reconcile()
        assert provider.writes == sent and provider.reaction_creates == reactions
        assert ordinary[4].read_bytes() == starts
        assert all((transport.root / f"{ref}.json").read_bytes() == data for ref, data in original_bytes.items())
    finally:
        gate.touch()
        runtime.close()


def test_app_projection_recovers_original_receipt_and_revocation_blocks_remaining_messages(ordinary):  # noqa: F811
    store, runtime, provider, transport, original, turns, before = _app_projection_journey(ordinary)
    provider.verify_replies = False
    try:
        _drain_app_projection(transport, lambda: len(provider.writes) > before)
        attempted = list(provider.writes)
        transport.reconcile()
        assert provider.writes == attempted
        provider.verify_replies = True
        transport.reconcile()
        # A verified old receipt may advance; the remaining canonical messages
        # retain the same grant and are denied after revocation.
        binding = transport._binding("notes-app")
        transport.bindings.disconnect(binding["binding_id"], expected_revision=transport.bindings.read()["revision"])
        revoked_writes = list(provider.writes)
        for _ in range(3):
            transport.reconcile()
        assert provider.writes == revoked_writes
        assert len(store.list_sessions()) == 1
    finally:
        runtime.close()


@pytest.mark.parametrize("unreadable", [b'{"status":', b"\xff"])
def test_unreadable_app_mirror_does_not_hold_other_private_replies(ordinary, unreadable):  # noqa: F811
    store, runtime, provider, transport, original, turns, _ = _app_projection_journey(ordinary)
    try:
        transport._web_projection_scan_at = 0
        [mirror] = transport._web_projection_paths()
        complete_receipt = mirror.read_bytes()
        mirror.write_bytes(unreadable)

        event = provider.event("steward-app", "independent-private-reply", "An independent original request")
        transport.admit("steward-app", event)
        ref = identity_ref(transport._binding("steward-app")["provider_ref"], event["message_id"])
        native = transport.core.read_request(ref)
        assert runtime.wait_for_turn(session_id=native["session_id"], turn_id=native["turn_id"], timeout_sec=10)["status"] == "completed"
        healthy = transport.root / f"{ref}.json"
        for discover in (transport.pending_body_paths, transport.pending_delivery_paths):
            paths = discover()
            assert healthy in paths and mirror not in paths

        # A previously scheduled worker can still see damage after discovery.
        before = list(provider.writes)
        assert transport.reconcile_request(mirror) == 0
        assert mirror.read_bytes() == unreadable
        assert provider.writes == before

        requests = ordinary[4].read_bytes()
        transport.reconcile()
        terminal = json.loads(healthy.read_text())["deliveries"]["terminal"]
        assert terminal["verified"]
        assert provider.messages[terminal["attempt"]["message_ref"]]["chat_id"] == event["chat_id"]
        assert mirror.read_bytes() == unreadable
        assert not any("（App）" in text for _, text in provider.writes)

        # Restoring the exact receipt in the disposable fixture makes the
        # original App work available to a newly constructed worker again.
        mirror.write_bytes(complete_receipt)
        restarted = LarkPrivateConversations(controller=runtime, runtime_root=transport.runtime_root,
            runner=provider, cli_bin="lark-cli")
        expected = [row for row in store.messages(original["session_id"]) if row.get("turn_id") in turns]
        _drain_app_projection(restarted, lambda: sum("（App）" in text for _, text in provider.writes) == len(expected))
        sent = list(provider.writes)
        restarted.reconcile()
        assert provider.writes == sent
        assert ordinary[4].read_bytes() == requests
    finally:
        runtime.close()


def test_app_long_return_keeps_all_parts_and_does_not_repeat_identical_parts(ordinary):  # noqa: F811
    store, runtime, provider, transport, original, turns, before = _app_projection_journey(ordinary)
    text = ("A repeated output paragraph with its complete evidence.\n" * 4000) + "UNIQUE FINAL END"
    store.append_message(original["session_id"], role="agent", text=text,
        turn_id=turns[-1], message_id="synthetic-long-result")
    # Discover the committed result immediately. This case exercises delivery,
    # including recovery of repeated parts, independently of scan cadence.
    transport._web_projection_scan_at = 0
    try:
        _drain_app_projection(transport, lambda: any("UNIQUE FINAL END" in t for _, t in provider.writes[before:]))
        # Each canonical character survives transport splitting; an identical
        # paragraph in different parts cannot reuse the same provider UUID.
        parts = [t for _, t in provider.writes[before:]
                 if "A repeated output paragraph" in t or "UNIQUE FINAL END" in t]
        assert len(parts) > 1
        combined = "".join(re.sub(r"^（\d+/\d+）\n\n", "", part) for part in parts)
        assert combined == "**LoopX（App）**\n\n" + text
        sent = list(provider.writes)
        transport.reconcile()
        assert provider.writes == sent
    finally:
        runtime.close()


def test_app_projection_does_not_retry_a_write_without_a_provider_locator(ordinary):  # noqa: F811
    store, runtime, provider, transport, original, turns, before = _app_projection_journey(ordinary)
    provider.lose_next_reply = True
    try:
        _drain_app_projection(transport, lambda: len(provider.writes) > before)
        attempted = list(provider.writes)
        requests = ordinary[4].read_text()
        restarted = LarkPrivateConversations(controller=runtime, runtime_root=transport.runtime_root,
            runner=provider, cli_bin="lark-cli")
        for _ in range(3):
            restarted.reconcile()
        assert provider.writes == attempted
        assert ordinary[4].read_text() == requests
        assert restarted.health()[original["binding_id"]]["recovery_count"] == 1
    finally:
        runtime.close()


def test_app_projection_refuses_a_replaced_provider_identity(ordinary):  # noqa: F811
    store, runtime, provider, transport, original, turns, before = _app_projection_journey(ordinary)
    provider.profile_apps["notes-app"] = "cli_replaced_app"
    try:
        restarted = LarkPrivateConversations(controller=runtime, runtime_root=transport.runtime_root,
            runner=provider, cli_bin="lark-cli")
        for _ in range(3):
            restarted.reconcile()
        assert len(provider.writes) == before
        assert len(store.list_sessions()) == 1
    finally:
        runtime.close()


@pytest.mark.parametrize("fault", ["revoked", "app", "source", "session", "turn", "context_recovery"])
def test_app_feedback_rechecks_exact_source_and_turn_after_mirror_delivery(ordinary, monkeypatch, fault):  # noqa: F811
    store, runtime, provider, transport, original, turns, before = _app_projection_journey(ordinary)
    deliver = transport._deliver
    baseline = list(provider.reaction_creates)
    starts = ordinary[4].read_bytes()

    def changed(path, row, phase, text, **kwargs):
        result = deliver(path, row, phase, text, **kwargs)
        if result and phase.startswith("web.") and "你（App）" in text:
            if fault == "revoked":
                transport.bindings.disconnect(row["binding_id"], expected_revision=transport.bindings.read()["revision"])
            elif fault == "app":
                provider.profile_apps["notes-app"] = "cli_replaced_app"
            elif fault == "source":
                row["source"] = {**row["source"], "source_ref": "0" * 24}
            elif fault == "session":
                row["session_id"] = "missing-session"
            elif fault == "context_recovery":
                source_path = transport.root / f"{row['request_ref']}.json"
                source = json.loads(source_path.read_text())
                source["context_recovery"] = {"client_turn_id": "unsupported-recovery"}
                source_path.write_text(json.dumps(source))
        return result

    if fault == "turn":
        feedback = transport._web_projection_feedback

        def wrong_turn(row, message):
            # Canonical storage keeps its correct Turn; a stale delivery
            # argument cannot redirect feedback to the original Lark input.
            return feedback(row, {**message, "turn_id": original["turn_id"]})

        monkeypatch.setattr(transport, "_web_projection_feedback", wrong_turn)
    monkeypatch.setattr(transport, "_deliver", changed)
    try:
        transport._web_projection_scan_at = 0
        transport.reconcile()
        assert len(provider.writes) == before + 1
        assert provider.reaction_creates == baseline
        assert ordinary[4].read_bytes() == starts
    finally:
        runtime.close()


@pytest.mark.parametrize("disabled", [False, True])
def test_app_feedback_failure_or_opt_out_does_not_block_frozen_messages(ordinary, disabled):  # noqa: F811
    store, runtime, provider, transport, original, turns, before = _app_projection_journey(ordinary)
    baseline = list(provider.reaction_creates)
    transport.reaction_feedback = not disabled
    provider.fail_reaction_create = not disabled
    starts = ordinary[4].read_bytes()
    try:
        _drain_app_projection(transport, lambda: len(provider.writes) == before + 4)
        transport.reconcile()
        sent = list(provider.writes)
        restarted = LarkPrivateConversations(controller=runtime, runtime_root=transport.runtime_root,
            runner=provider, cli_bin="lark-cli", reaction_feedback=not disabled)
        for _ in range(3):
            restarted.reconcile()
        assert provider.writes == sent and provider.reaction_creates == baseline
        assert ordinary[4].read_bytes() == starts
        assert len(store.messages(original["session_id"])) == 6
    finally:
        runtime.close()


def test_app_long_input_feedback_uses_its_last_verified_post(ordinary):  # noqa: F811
    store, runtime, provider, transport, original, turns, before = _app_projection_journey(ordinary)
    text = "Long App input with complete source.\n" * 900 + "END_APP_INPUT"
    sid = original["session_id"]
    try:
        status, turn = ordinary[3](f"/api/chat/sessions/{sid}/turns",
            {"message": text, "client_turn_id": "long-app-feedback"})
        assert status == 202
        assert runtime.wait_for_turn(session_id=sid, turn_id=turn["turn_id"], timeout_sec=10)["status"] == "completed"
        _drain_app_projection(transport, lambda: any("END_APP_INPUT" in body for _, body in provider.writes[before:]))
        parts = [body for _, body in provider.writes[before:] if "Long App input" in body or "END_APP_INPUT" in body]
        assert len(parts) > 1
        assert "".join(re.sub(r"^（\d+/\d+）\n\n", "", part) for part in parts) == "**你（App）**\n\n" + text
        refs = {ref for ref, message in provider.messages.items()
                if "Long App input" in json.dumps(message.get("body", {})) or "END_APP_INPUT" in json.dumps(message.get("body", {}))}
        last = next(ref for ref in refs if "END_APP_INPUT" in json.dumps(provider.messages[ref]))
        assert [entry for entry in provider.reaction_creates if entry[1] in refs] == [("notes-app", last, "Get")]
    finally:
        runtime.close()


def test_enabling_app_feedback_does_not_backfill_old_finished_mirrors(ordinary, monkeypatch):  # noqa: F811
    store, runtime, provider, transport, original, turns, before = _app_projection_journey(ordinary)
    transport.reaction_feedback = False
    baseline = list(provider.reaction_creates)
    try:
        _drain_app_projection(transport, lambda: len(provider.writes) == before + 4)
        transport.reconcile()
        sent = list(provider.writes)
        restarted = LarkPrivateConversations(controller=runtime, runtime_root=transport.runtime_root,
            runner=provider, cli_bin="lark-cli", reaction_feedback=True)
        for _ in range(3):
            restarted.reconcile()
        assert provider.writes == sent and provider.reaction_creates == baseline
        # An idle worker must not decode or authorize the full App history on
        # every feedback tick just to find the same finished inputs again.
        transcript = store.sessions_root / original["session_id"] / "messages.jsonl"
        path_type = type(transcript)
        original_read = path_type.read_bytes

        def read(path):
            assert path != transcript, "idle feedback reread the complete Chat transcript"
            return original_read(path)

        with monkeypatch.context() as idle:
            idle.setattr(path_type, "read_bytes", read)
            for _ in range(3):
                restarted.reconcile_app_feedback()
        assert provider.writes == sent and provider.reaction_creates == baseline
    finally:
        runtime.close()


def test_app_feedback_refuses_a_changed_input_with_an_old_verified_mirror(ordinary, monkeypatch):  # noqa: F811
    store, runtime, provider, transport, original, turns, before = _app_projection_journey(ordinary)
    deliver = transport._deliver
    changed_target = []
    captured = ordinary[4].read_bytes()

    def amend_after_send(path, row, phase, text, **kwargs):
        result = deliver(path, row, phase, text, **kwargs)
        if result and "你（App）" in text and not changed_target:
            changed_target.append(row["deliveries"][phase]["attempt"]["message_ref"])
            transcript = store.sessions_root / original["session_id"] / "messages.jsonl"
            messages = [json.loads(line) for line in transcript.read_text().splitlines()]
            for message in messages:
                if phase == "web." + message["message_id"]:
                    message["text"] = "Changed canonical input; frozen provider body is now stale."
            # Deliberate corruption of disposable canonical source data,
            # not an edit API or a mutation of an active user conversation.
            transcript.write_text("".join(json.dumps(message) + "\n" for message in messages))
        return result

    monkeypatch.setattr(transport, "_deliver", amend_after_send)
    try:
        _drain_app_projection(transport, lambda: len(provider.writes) == before + 4)
        transport.reconcile()
        assert changed_target
        assert not any(ref == changed_target[0] for _, ref, _ in provider.reaction_creates)
        assert ordinary[4].read_bytes() == captured
    finally:
        runtime.close()


def finish_group_turn(runtime, transport, *, message):
    row = next(row for row in transport.core.pending() if row["message"] == message
               or row["message"].endswith(f"\n\nCurrent user message:\n{message}"))
    assert runtime.wait_for_turn(session_id=row["session_id"], turn_id=row["turn_id"], timeout_sec=10)["status"] == "completed"
    transport.reconcile()
    return row


@pytest.mark.parametrize("group", [False, True])
def test_recovery_waits_for_inflight_capture_without_readmitting(ordinary, monkeypatch, group):  # noqa: F811
    import loopx.extensions.lark.private_conversations as private

    store, runtime, provider, transport = (connect_group if group else connect)(ordinary)
    event = provider.topic("inflight", "Explain this project") if group else provider.event(
        "notes-app", "inflight", "Explain this project")
    reached_core, release_core, captured_seen = threading.Event(), threading.Event(), threading.Event()
    core_admit, read = transport.core.admit, private._read_json
    admissions = []

    def admit(**kwargs):
        admissions.append(kwargs["request_ref"])
        reached_core.set()
        assert release_core.wait(5)
        return core_admit(**kwargs)

    def observed(path):
        row = read(path)
        if (threading.current_thread().name.startswith("capture-recovery")
                and path.parent == transport.root and row.get("status") == "captured"):
            captured_seen.set()
        return row

    monkeypatch.setattr(transport.core, "admit", admit)
    monkeypatch.setattr(private, "_read_json", observed)
    try:
        with ThreadPoolExecutor(max_workers=1) as intake, ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="capture-recovery") as recovery:
            original = intake.submit(transport.admit, "notes-app", event)
            try:
                assert reached_core.wait(5)
                path = next(transport.root.glob("*.json"))
                assert read(path)["status"] == "captured"
                returned = recovery.submit(transport.reconcile_request, path)
                assert captured_seen.wait(5)  # The reader really saw the in-flight row.
            finally:
                release_core.set()
            assert original.result(timeout=10)["status"] == "durably_accepted"
            returned.result(timeout=10)
        assert len(admissions) == 1
        row = transport.core.pending()[0]
        result = runtime.wait_for_turn(session_id=row["session_id"], turn_id=row["turn_id"], timeout_sec=10)
        assert result["status"] == "completed"
        transport.reconcile()
        assert len(store.list_sessions()) == 1
        assert [m["text"] for m in store.messages(row["session_id"]) if m["role"] == "user"] == [event["content"]]
        delivered = read(path)
        terminal = delivered["deliveries"]["terminal"]
        assert delivered["status"] == "delivered" and terminal["verified"] is True
        # A running Turn can create a draft before recovery returns. Terminal
        # delivery then edits it; writes records creations, not current bodies.
        final_ref = terminal["attempt"]["message_ref"]
        final_message = provider.messages[final_ref]
        content = json.loads(final_message["body"]["content"])
        final_text = result["response"]["message"]
        assert content["zh_cn"]["content"][0][0]["text"] == final_text
        assert sum(message.get("body") == final_message["body"]
                   for message in provider.messages.values()) == 1
        assert all(profile == "notes-app" for profile, _ in provider.writes)
        packets = [json.loads(line) for line in ordinary[4].read_text().splitlines()]
        assert sum(packet["method"] == "turn/start" for packet in packets) == 1
        if group:
            assert provider.topic_writes[0][:2] == (event["chat_id"], event["message_id"])
        visible_before = json.dumps(provider.messages, sort_keys=True)
        writes_before = list(provider.writes)
        assert transport.reconcile() == 0
        assert json.dumps(provider.messages, sort_keys=True) == visible_before
        assert provider.writes == writes_before
    finally:
        release_core.set()
        runtime.close()


@pytest.mark.parametrize("group", [False, True])
@pytest.mark.parametrize("revoked", [False, True])
def test_abandoned_capture_recovers_only_with_current_binding(ordinary, monkeypatch, group, revoked):  # noqa: F811
    store, runtime, provider, transport = (connect_group if group else connect)(ordinary)
    event = provider.topic("abandoned", "Explain this project") if group else provider.event(
        "notes-app", "abandoned", "Explain this project")
    core_admit = transport.core.admit

    def unavailable(**kwargs):
        raise OSError("owned failure before any Core admission")

    try:
        monkeypatch.setattr(transport.core, "admit", unavailable)
        with pytest.raises(OSError, match="before any Core admission"):
            transport.admit("notes-app", event)
        path = next(transport.root.glob("*.json"))
        captured = json.loads(path.read_text())
        assert captured["status"] == "captured" and store.list_sessions() == []
        monkeypatch.setattr(transport.core, "admit", core_admit)
        if revoked:
            transport.bindings.disconnect(captured["binding_id"], expected_revision=transport.bindings.read()["revision"])
            assert transport.reconcile_request(path) == 0
            assert store.list_sessions() == [] and transport.core.pending() == []
            assert provider.writes == [] and provider.reaction_creates == []
            assert json.loads(path.read_text())["status"] == "captured"
        else:
            transport.reconcile_request(path)
            row = transport.core.pending()[0]
            result = runtime.wait_for_turn(session_id=row["session_id"], turn_id=row["turn_id"], timeout_sec=10)
            assert result["status"] == "completed"
            transport.reconcile()
            assert len(store.list_sessions()) == 1
            delivered = json.loads(path.read_text())
            terminal = delivered["deliveries"]["terminal"]
            assert delivered["status"] == "delivered" and terminal["verified"] is True
            # Recovery can finish a draft by editing its existing message.
            # Provider-visible content, not creation history, proves the final.
            final = provider.messages[terminal["attempt"]["message_ref"]]
            assert final["chat_id"] == event["chat_id"]
            assert final["sender"] == {"id": provider.profile_apps["notes-app"], "sender_type": "app"}
            content = json.loads(final["body"]["content"])
            assert content["zh_cn"]["content"][0][0]["text"] == result["response"]["message"]
            assert sum(message.get("body") == final["body"] for message in provider.messages.values()) == 1
            assert all(profile == "notes-app" for profile, _ in provider.writes)
            assert [m["text"] for m in store.messages(row["session_id"]) if m["role"] == "user"] == [event["content"]]
            packets = [json.loads(line) for line in ordinary[4].read_text().splitlines()]
            assert sum(packet["method"] == "turn/start" for packet in packets) == 1
            assert transport.reconcile() == 0
    finally:
        runtime.close()


@pytest.mark.parametrize("unavailable", ["bot_ready", "owner_ready"])
def test_owner_event_survives_identity_outage_and_recovers_once(ordinary, unavailable):  # noqa: F811
    store, runtime, provider, transport = connect(ordinary)
    event = provider.event("notes-app", "identity_outage", "Explain this project")
    try:
        setattr(provider, unavailable, False)
        assert transport.admit("notes-app", event)["status"] == "audience_rejected"
        path = next(transport.root.glob("*.json"))
        captured = json.loads(path.read_text())
        assert captured["status"] == "captured" and captured["event"] == event
        assert transport.pending_delivery_paths() == [path]
        assert store.list_sessions() == [] and transport.core.pending() == []
        assert provider.writes == [] and provider.reaction_creates == []
        assert transport.reconcile_request(path) == 0
        assert json.loads(path.read_text()) == captured

        # The normal scanner can recover the same receipt after a restart;
        # no source-history replay or second event consumer is involved.
        restored = LarkPrivateConversations(controller=runtime, runtime_root=store.root.parent,
            runner=provider, cli_bin="lark-cli")
        setattr(provider, unavailable, True)
        restored.reconcile_request(path)
        row = restored.core.pending()[0]
        result = runtime.wait_for_turn(session_id=row["session_id"], turn_id=row["turn_id"], timeout_sec=10)
        assert result["status"] == "completed"
        restored.reconcile()
        delivered = json.loads(path.read_text())
        assert delivered["status"] == "delivered" and delivered["event"] == event
        assert delivered["deliveries"]["terminal"]["verified"] is True
        assert len(store.list_sessions()) == 1
        assert [m["text"] for m in store.messages(row["session_id"]) if m["role"] == "user"] == [event["content"]]
        provider_messages = json.loads(json.dumps(provider.messages))
        provider_writes = list(provider.writes)
        restored.admit("notes-app", event)
        restored.reconcile()
        assert provider.messages == provider_messages and provider.writes == provider_writes
        packets = [json.loads(line) for line in ordinary[4].read_text().splitlines()]
        assert sum(packet["method"] == "turn/start" for packet in packets) == 1
    finally:
        runtime.close()


@pytest.mark.parametrize("change", ["revoked", "source_sender_changed", "owner_changed"])
def test_unverified_owner_receipt_does_not_grant_later_execution(ordinary, monkeypatch, change):  # noqa: F811
    store, runtime, provider, transport = connect(ordinary)
    event = provider.event("notes-app", "unverified_boundary", "Explain this project")
    try:
        provider.owner_ready = False
        assert transport.admit("notes-app", event)["status"] == "audience_rejected"
        path = next(transport.root.glob("*.json"))
        captured = json.loads(path.read_text())
        provider.owner_ready = True
        if change == "revoked":
            transport.bindings.disconnect(captured["binding_id"],
                expected_revision=transport.bindings.read()["revision"])
        elif change == "source_sender_changed":
            provider.messages[event["message_id"]]["sender"]["id"] = "ou_other"
        else:
            observe = transport.bindings.observe
            monkeypatch.setattr(transport.bindings, "observe", lambda profile: {
                **observe(profile), "operator_ref": "f" * 24})
        assert transport.reconcile_request(path) == 0
        assert store.list_sessions() == [] and transport.core.pending() == []
        assert provider.writes == [] and provider.reaction_creates == []
        assert json.loads(path.read_text())["status"] == "captured"
    finally:
        runtime.close()


@pytest.mark.parametrize("group", [False, True])
def test_identity_outage_does_not_precapture_groups_or_other_owners(ordinary, group):  # noqa: F811
    store, runtime, provider, transport = (connect_group if group else connect)(ordinary)
    event = provider.topic("unverified_group", "Explain this project") if group else provider.event(
        "notes-app", "unverified_other_owner", "Explain this project")
    if not group:
        event["sender_id"] = "ou_other"
    try:
        provider.bot_ready = False
        assert transport.admit("notes-app", event)["status"] == "audience_rejected"
        assert list(transport.root.glob("*.json")) == []
        assert store.list_sessions() == [] and transport.core.pending() == []
        assert provider.writes == [] and provider.reaction_creates == []
    finally:
        runtime.close()


def test_rejected_source_returns_notice_without_creating_core_execution(ordinary, monkeypatch):  # noqa: F811
    store, runtime, provider, transport = connect(ordinary)
    event = provider.event("notes-app", "rejected_before_core", "Explain this project")
    try:
        admit = transport.core.admit
        def reject(**_kwargs):
            raise ValueError("identity changed before Core admission")

        monkeypatch.setattr(transport.core, "admit", reject)
        assert transport.admit("notes-app", event)["status"] == "command_rejected"
        path = next(transport.root.glob("*.json"))
        captured = json.loads(path.read_text())
        native_path = transport.core.root / f"{captured['request_ref']}.json"
        assert not native_path.exists()
        assert transport.reconcile_request(path) == 1
        delivered = json.loads(path.read_text())
        assert delivered["status"] == "delivered"
        assert delivered["deliveries"]["terminal"]["verified"] is True
        assert len(provider.writes) == 1 and not native_path.exists()
        assert store.list_sessions() == []
        with pytest.raises(ValueError, match="request journal unavailable"):
            transport.core.record_delivery(captured["request_ref"], session_id=None, turn_id=None)
        assert not native_path.exists()
        monkeypatch.setattr(transport.core, "admit", admit)
        assert transport.admit("notes-app", event)["status"] == "command_rejected"
        assert not native_path.exists() and store.list_sessions() == []
        assert len(provider.writes) == 1
    finally:
        runtime.close()


@pytest.mark.parametrize("group", [False, True])
def test_completion_during_processing_receipt_returns_final_without_stale_draft(ordinary, monkeypatch, tmp_path, group):  # noqa: F811
    store, runtime, provider, transport = (connect_group if group else connect)(ordinary)
    gate, ready = tmp_path / "complete", tmp_path / "native-waiting"
    fake = ordinary[-2]
    marker = '        if "typed terminal failure" in json.dumps(request.get("params") or {}):'
    source = fake.read_text()
    assert marker in source
    fake.write_text(source.replace(marker,
        '        from pathlib import Path\n'
        f'        Path({str(ready)!r}).touch()\n'
        f'        while not Path({str(gate)!r}).exists():\n'
        '            time.sleep(.01)\n' + marker))
    event = provider.topic("receipt-completion", "Explain this project") if group else provider.event(
        "notes-app", "receipt-completion", "Explain this project")
    provider_call = provider.__call__
    completed = []

    def receipt(args, cwd=None, timeout=None):
        if "reactions" in args and "create" in args:
            emoji = json.loads(args[args.index("--data") + 1])["reaction_type"]["emoji_type"]
            if emoji == "OnIt":
                gate.touch()
                completed.append(runtime.wait_for_turn(session_id=row["session_id"], turn_id=row["turn_id"], timeout_sec=5))
                assert completed[-1]["status"] == "completed"
        return provider_call(args, cwd, timeout)

    monkeypatch.setattr(transport, "runner", receipt)
    try:
        assert transport.admit("notes-app", event)["status"] == "durably_accepted"
        row = transport.core.pending()[0]
        deadline = time.monotonic() + 5
        # Native stdout and the persisted Turn are separate observations.
        while time.monotonic() < deadline and (not ready.exists()
                or store.load_turn(row["session_id"], row["turn_id"])["status"] != "running"):
            time.sleep(.01)
        assert ready.exists() and store.load_turn(row["session_id"], row["turn_id"])["status"] == "running"
        path = transport.root / f"{row['request_ref']}.json"
        assert transport.reconcile_request(path) == 1
        assert completed and provider.writes == [("notes-app", completed[-1]["response"]["message"])]
        assert "progress" not in json.loads(path.read_text())["deliveries"]
        assert transport.reconcile() == 0
    finally:
        gate.touch()
        runtime.close()


def test_completed_group_queue_keeps_a_stopped_host_stopped(ordinary):  # noqa: F811
    store, runtime, provider, transport = connect_group(ordinary)
    try:
        event = provider.topic("idle-host", "Explain this project")
        assert transport.admit("notes-app", event)["status"] == "durably_accepted"
        row = transport.core.pending()[0]
        result = runtime.wait_for_turn(session_id=row["session_id"], turn_id=row["turn_id"], timeout_sec=10)
        assert result["status"] == "completed"
        with runtime.lock:
            worker = runtime.session_queue_threads.get(row["session_id"])
        if worker:
            worker.join(3)
            assert not worker.is_alive()
        with runtime.lock:
            adapter = runtime.adapters.pop(row["session_id"])
        adapter.close_session()
        assert not adapter.healthcheck()
        capture = ordinary[4]
        native_before = capture.read_bytes()
        # The real recovery pump is also used while a terminal reply is owed.
        # It must not prepare an idle host or change the completed answer.
        before = store.load_turn(row["session_id"], row["turn_id"])
        assert transport.reconcile() == 1
        with runtime.lock:
            worker = runtime.session_queue_threads.get(row["session_id"])
        if worker:
            worker.join(3)
            assert not worker.is_alive()
        assert capture.read_bytes() == native_before
        assert row["session_id"] not in runtime.adapters
        assert store.load_turn(row["session_id"], row["turn_id"]) == before
        assert len(provider.writes) == 1 and provider.writes[0][1] == result["response"]["message"]
        assert transport.reconcile() == 0
    finally:
        runtime.close()


def test_group_receipt_reuses_only_the_fresh_bindings_display_observation(ordinary, monkeypatch):  # noqa: F811
    store, runtime, provider, transport = connect_group(ordinary)
    root = provider.topic("receipt-source", "Explain this project")
    observe = transport.bindings.observe
    source_verified = transport._source_verified
    checking_source, source_identity_observed = threading.Event(), threading.Event()
    calls = []
    def observed(profile, **kwargs):
        import sys
        calls.append(sys._getframe(1).f_code.co_name)
        if checking_source.is_set():
            source_identity_observed.set()
        return observe(profile, **kwargs)
    def verified(record, **kwargs):
        checking_source.set()
        try:
            return source_verified(record, **kwargs)
        finally:
            checking_source.clear()
    monkeypatch.setattr(transport.bindings, "observe", observed)
    monkeypatch.setattr(transport, "_source_verified", verified)
    try:
        assert transport.admit("notes-app", root)["status"] == "durably_accepted"
        row = next(row for row in transport.core.pending() if row["message"] == root["content"])
        runtime.wait_for_turn(session_id=row["session_id"], turn_id=row["turn_id"], timeout_sec=10)
        calls.clear()
        transport.reconcile()
        assert any(emoji == "Get" for _, _, emoji in provider.reaction_creates)
        assert "resolve" in calls and source_identity_observed.is_set()
        # Naming the Inbox adds no authentication request after the fresh
        # binding resolution. Actual outbound source validation still observes.
        assert "_inbox" not in calls
        assert all(topic == root["message_id"] for _, topic, _ in provider.topic_writes)
        assert len(store.list_sessions()) == 1
    finally:
        runtime.close()


@pytest.mark.parametrize("command", ["/status", "/stop", "/help"])
def test_completed_command_recovery_leaves_provenance_to_delivery(ordinary, command):  # noqa: F811
    store, runtime, provider, transport = connect_group(ordinary)
    root = provider.topic("command-root", "/help")
    event = provider.topic("completed-command", command, root=root["message_id"])
    try:
        assert transport.admit("notes-app", event)["status"] == "command_recorded"
        path = next(path for path in transport.root.glob("*.json")
                    if json.loads(path.read_text())["event"]["message_id"] == event["message_id"])
        record = json.loads(path.read_text())
        native_path = transport.core.root / f"{record['request_ref']}.json"
        before = native_path.read_bytes()
        assert json.loads(before)["status"] == "command_completed"

        # There is no command execution to recover. Provider availability must
        # not add an identity round-trip to this already committed Core state.
        provider.calls.clear()
        transport.core.recover(request_ref=record["request_ref"])
        assert provider.calls == []
        assert native_path.read_bytes() == before

        pending = json.loads(before) | {"commission_adoption_pending": True}
        native_path.write_text(json.dumps(pending))
        transport.core.recover(request_ref=record["request_ref"])
        assert any("auth" in call for call in provider.calls)
        assert json.loads(native_path.read_text()) == pending
        native_path.write_bytes(before)

        # Skipping idle recovery does not skip the delivery owner's fresh check.
        provider.bot_ready = False
        assert transport.reconcile_request(path) == 0
        assert provider.writes == []
        assert json.loads(path.read_text())["status"] != "delivered"

        provider.bot_ready = True
        assert transport.reconcile_request(path) == 1
        assert json.loads(path.read_text())["status"] == "delivered"
        assert json.loads(native_path.read_text())["delivery_verified"] is True
        assert len(provider.writes) == 1
        assert provider.topic_writes[0][:2] == (event["chat_id"], root["message_id"])
    finally:
        runtime.close()


@pytest.mark.parametrize("fault", [None, "changed-source", "provider-unavailable", "revoked"])
def test_completed_answer_delivery_does_not_prepare_another_execution(ordinary, monkeypatch, fault):  # noqa: F811
    store, runtime, provider, transport = connect(ordinary)
    event = provider.event("notes-app", "completed-original", "Complete this original request.")
    try:
        transport.admit("notes-app", event)
        native = next(row for row in transport.core.pending() if row["message"] == event["content"])
        sid, tid = native["session_id"], native["turn_id"]
        completed = runtime.wait_for_turn(session_id=sid, turn_id=tid, timeout_sec=10)
        assert completed["status"] == "completed"
        worker = runtime.session_queue_threads.get(sid)
        if worker is not None:
            worker.join(5)
            assert not worker.is_alive()
        followup, _ = store.create_queued_turn(sid, client_turn_id="saved-next-input", message="Saved next input.")
        queued_before = store.load_turn(sid, followup["turn_id"])
        path = transport.root / f"{native['request_ref']}.json"
        native_path = transport.core.root / path.name
        original = native_path.read_bytes()
        messages = store.messages(sid)
        # Preparing another execution may be slow or unavailable while a
        # committed answer is ready. Delivery must not depend on either call.
        monkeypatch.setattr(runtime.project_contexts, "session_context",
                            lambda *_: pytest.fail("workspace preparation before committed-answer delivery"))
        monkeypatch.setattr(runtime, "resume_session_queue",
                            lambda **_: pytest.fail("queue preparation before committed-answer delivery"))
        provider.calls.clear()
        transport.core.recover(request_ref=native["request_ref"])
        assert provider.calls == []
        assert native_path.read_bytes() == original
        assert store.messages(sid) == messages
        if fault == "changed-source":
            provider.messages[event["message_id"]]["sender"]["id"] = "ou_another_human"
        elif fault == "provider-unavailable":
            provider.bot_ready = False
        elif fault == "revoked":
            transport.bindings.disconnect(native["binding_id"], expected_revision=transport.bindings.read()["revision"])
        # Disconnect legitimately stops the queued follow-up. Compare delivery
        # against that new authority state, retaining the original answer.
        delivery_messages = store.messages(sid)
        delivery_followup = store.load_turn(sid, followup["turn_id"])
        if fault == "revoked":
            assert delivery_followup["status"] in {"failed", "interrupted"}
        delivered = transport.reconcile_request(path)
        if fault != "revoked":
            assert any("auth" in call for call in provider.calls)  # Fresh delivery provenance remains mandatory.
        if fault:
            assert delivered == 0 and provider.writes == []
            assert json.loads(path.read_text())["status"] != "delivered"
        else:
            assert delivered == 1
            assert provider.writes == [("notes-app", "Runtime response.")]
            assert json.loads(native_path.read_text())["delivery_verified"] is True
        assert store.messages(sid) == delivery_messages
        assert [m for m in delivery_messages if m.get("turn_id") == tid] == [m for m in messages if m.get("turn_id") == tid]
        assert store.load_turn(sid, followup["turn_id"]) == delivery_followup
        if fault != "revoked":
            assert delivery_followup == queued_before
        packets = [json.loads(line) for line in ordinary[4].read_text().splitlines()]
        assert len([row for row in packets if row["method"] == "turn/start"]) == 1
    finally:
        runtime.close()


def test_unexecuted_request_still_uses_original_queue_recovery(ordinary, monkeypatch):  # noqa: F811
    store, runtime, provider, transport = connect(ordinary)
    resumes = []
    monkeypatch.setattr(runtime, "resume_session_queue", lambda **kw: resumes.append(kw))
    event = provider.event("notes-app", "not-completed", "A completed-looking title is still new work.")
    try:
        transport.admit("notes-app", event)
        native = next(row for row in transport.core.pending() if row["message"] == event["content"])
        assert store.load_turn(native["session_id"], native["turn_id"])["status"] == "queued"
        resumes.clear()
        provider.calls.clear()
        transport.core.recover(request_ref=native["request_ref"])
        assert len(resumes) == 1 and resumes[0]["session_id"] == native["session_id"]
        assert any("auth" in call for call in provider.calls)
        assert provider.writes == []
        assert store.load_turn(native["session_id"], native["turn_id"])["status"] == "queued"
    finally:
        runtime.close()


def test_lark_transport_recovery_returns_on_original_admission_without_model_replay(ordinary, monkeypatch):  # noqa: F811
    from test_chat_turn_steering import install_transport_failure

    install_transport_failure(ordinary[-2])
    store, runtime, provider, transport = connect(ordinary)
    monkeypatch.setattr(runtime.closed, "wait", lambda _: False)
    event = provider.event("notes-app", "recover-original", "transport recovery")
    try:
        assert transport.admit("notes-app", event)["status"] == "durably_accepted"
        record = next(row for row in transport.core.pending() if row["message"] == event["content"])
        sid, tid = record["session_id"], record["turn_id"]
        assert runtime.wait_for_turn(session_id=sid, turn_id=tid, timeout_sec=10)["status"] == "completed"
        provider.verify_replies = False
        transport.reconcile()
        sent = list(provider.writes)
        assert ("notes-app", "Runtime response.") in sent
        provider.verify_replies = True
        assert transport.admit("notes-app", {**event, "event_id": "redelivery"})["status"] == "durably_accepted"
        # Lost/unconfirmed provider readback must retry delivery, never model IO.
        transport.reconcile()
        assert provider.writes == sent
        packets = [json.loads(line) for line in ordinary[4].read_text().splitlines()]
        starts = [row["params"] for row in packets if row["method"] == "turn/start"]
        assert len(starts) == 2 and starts[0] == starts[1]
        assert len(store.list_sessions()) == 1
        assert [row["text"] for row in store.messages(sid) if row["role"] == "user"] == [event["content"]]
        assert all(profile == "notes-app" for profile, _ in provider.writes)
        reply = next(row for row in provider.messages.values()
                     if "Runtime response." in row.get("body", {}).get("content", ""))
        assert reply["chat_id"] == event["chat_id"]
    finally:
        runtime.close()


def test_group_topics_share_members_but_isolate_topics_apps_and_native_host(ordinary):  # noqa: F811
    store, runtime, provider, transport = connect_group(ordinary)
    try:
        root = provider.topic("community-root", "Explain this public project")
        assert transport.admit("notes-app", root)["status"] == "durably_accepted"
        first = finish_group_turn(runtime, transport, message=root["content"])
        continuation = provider.topic("community-follow", "Compare its configuration", root=root["message_id"], sender="ou_other_member")
        assert transport.admit("notes-app", continuation)["status"] == "durably_accepted"
        second = finish_group_turn(runtime, transport, message=continuation["content"])
        assert second["session_id"] == first["session_id"]
        assert transport.admit("notes-app", {**continuation, "event_id": "redelivered"})["status"] == "durably_accepted"
        for name, chat in [("different-topic", "oc_community"), ("different-group", "oc_second")]:
            event = provider.topic(name, name, chat=chat)
            assert transport.admit("notes-app", event)["status"] == "durably_accepted"
            row = finish_group_turn(runtime, transport, message=name)
            assert row["session_id"] != first["session_id"]
        assert len(store.list_sessions()) == 3
        context = store.load_session(first["session_id"])["project_context"]
        assert context["audience"] == "bound_group" and context["filesystem_scope"] == "workspace_only"
        assert all(row["goal_id"] is None for row in store.list_sessions())
        assert provider.topic_writes and all(chat in {"oc_community", "oc_second"} for chat, _, _ in provider.topic_writes)
        assert any(root_id == root["message_id"] and text == "Runtime response." for _, root_id, text in provider.topic_writes)
        requests = [json.loads(line) for line in ordinary[4].read_text().splitlines()]
        starts = [r["params"] for r in requests if r["method"] == "thread/start"]
        assert len(starts) == 3 and all(r["permissions"] == "loopx_workspace_only_read" for r in starts)
        assert all(not r.get("dynamicTools") for r in starts)
        assert "Fresh Core evidence" not in ordinary[4].read_text()
        # Original private project still uses the default host and grant.
        assert runtime.project_contexts.available()[0].get("filesystem_scope") is None
        assert transport._binding("steward-app").get("audience") is None
        runtime.close()
        from loopx.chat_runtime import ChatRuntimeController
        restarted = ChatRuntimeController(store=store, codex_bin=str(ordinary[-2]), project_contexts=runtime.project_contexts)
        restarted.codex_home = runtime.codex_home
        try:
            reopened, resumed = restarted.open_session(goal_id=None, agent_id="codex", work_dir=ordinary[-1],
                objective="continue", conversation_binding_id=context["binding_id"],
                source_context=lark_private_source(provider_ref=context["provider_ref"], event=root),
                mode="resume_latest")
            assert resumed and reopened["session_id"] == first["session_id"]
        finally:
            restarted.close()
    finally:
        runtime.close()


def test_group_model_handoff_cannot_read_or_dispatch_to_a_private_goal(ordinary, monkeypatch):  # noqa: F811
    from loopx.chat_runtime import CodexAppServerAdapter
    store, runtime, provider, transport = connect_group(ordinary)
    original = CodexAppServerAdapter.start_turn

    def untrusted_response(adapter, *args, **kwargs):
        return {**original(adapter, *args, **kwargs), "context_handoff": {
            "goal_id": "private-work", "agent_id": "private-agent", "brief": "read private state"}}

    monkeypatch.setattr(CodexAppServerAdapter, "start_turn", untrusted_response)
    monkeypatch.setattr("loopx.chat_runtime.apply_context_handoff",
        lambda *_, **__: pytest.fail("workspace Chat must reject before any private Goal read or dispatch"))
    try:
        root = provider.topic("untrusted-handoff", "Explain this public project")
        assert transport.admit("notes-app", root)["status"] == "durably_accepted"
        row = transport.core.pending()[0]
        turn = runtime.wait_for_turn(session_id=row["session_id"], turn_id=row["turn_id"], timeout_sec=10)
        assert turn["status"] == "failed"
        assert "cannot hand off" in json.dumps(turn)
        assert not (store.root.parent / "manager-context").exists()
    finally:
        runtime.close()


@pytest.mark.parametrize("mentioned_stop", [False, True])
def test_group_progress_queue_and_stop_remain_in_the_original_topic(ordinary, mentioned_stop):  # noqa: F811
    store, runtime, provider, transport = connect_group(ordinary)
    try:
        roots = [provider.topic(f"running-{i}", "wait for interrupt") for i in range(2)]
        for root in roots:
            assert transport.admit("notes-app", root)["status"] == "durably_accepted"
        rows = [next(row for row in transport.core.pending() if row["source"] == lark_private_source(
            provider_ref=transport._binding("notes-app")["provider_ref"], event=root)) for root in roots]
        for row in rows:
            deadline = time.monotonic() + 10
            while store.load_turn(row["session_id"], row["turn_id"])["status"] != "running":
                assert time.monotonic() < deadline
                time.sleep(.01)
        queued = provider.topic("queued-in-first-topic", "Continue this public check", root=roots[0]["message_id"])
        assert transport.admit("notes-app", queued)["status"] == "durably_accepted"
        assert len(store.queued_turns(rows[0]["session_id"])) == 1
        store.append_event(rows[0]["session_id"], rows[0]["turn_id"], kind="answer.delta", payload={"text": "Public progress"})
        store.append_event(rows[0]["session_id"], rows[0]["turn_id"], kind="agent.phase", payload={"method": "item/reasoning/textDelta", "label": "private reasoning"})
        transport.reconcile()
        assert any(root == roots[0]["message_id"] and "Public progress" in text for _, root, text in provider.topic_writes)
        assert all("private reasoning" not in text for _, _, text in provider.topic_writes)
        stop = provider.topic("stop-first-topic", "/stop", root=roots[0]["message_id"])
        if mentioned_stop:
            stop["content"] = "@Renamed assistant  /stop"
            provider.messages[stop["message_id"]].update(content=stop["content"],
                body={"content": json.dumps({"text": "@_user_1  /stop"})},
                mentions=[{"key": "@_user_1", "name": "Renamed assistant", "id": "ou_notes_app_bot"}])
        assert transport.admit("notes-app", stop)["status"] == "command_recorded"
        assert runtime.wait_for_turn(session_id=rows[0]["session_id"], turn_id=rows[0]["turn_id"], timeout_sec=10)["status"] == "interrupted"
        assert store.load_turn(rows[1]["session_id"], rows[1]["turn_id"])["status"] == "running"
        finish_group_turn(runtime, transport, message=queued["content"])
        assert any("+messages-edit" in call for call in provider.calls)
        assert all(root in {row["message_id"] for row in roots} for _, root, _ in provider.topic_writes)
    finally:
        runtime.close()


@pytest.mark.parametrize("command", ["/status", "/help", "/new"])
def test_group_mentioned_controls_bypass_model_turns(ordinary, command):  # noqa: F811
    store, runtime, provider, transport = connect_group(ordinary)
    try:
        root = provider.topic("control-topic", "Explain this public project")
        assert transport.admit("notes-app", root)["status"] == "durably_accepted"
        row = finish_group_turn(runtime, transport, message=root["content"])
        turns = store.root / "sessions" / row["session_id"] / "turns"
        before = sorted(turns.glob("*.json"))
        event = provider.topic("mentioned-control", "@Renamed assistant " + command, root=root["message_id"])
        provider.messages[event["message_id"]].update(
            body={"content": json.dumps({"text": "@_user_1 " + command})},
            mentions=[{"key": "@_user_1", "name": "Renamed assistant", "id": {"app_id": "cli_notes_app"}}])
        assert transport.admit("notes-app", event)["status"] == "command_recorded"
        assert sorted(turns.glob("*.json")) == before
    finally:
        runtime.close()


@pytest.mark.parametrize("command", ["/stop", "/status", "/help", "/new"])
def test_admitted_topic_mentioned_control_uses_verified_pair(ordinary, command):  # noqa: F811
    store, runtime, provider, transport = connect_group(ordinary)
    try:
        root = provider.topic("mentioned-fast-root",
                              "Explain the project" if command == "/new" else "wait for interrupt")
        assert transport.admit("notes-app", root)["status"] == "durably_accepted"
        original = transport.core.pending()[0]
        if command == "/new":
            finish_group_turn(runtime, transport, message=root["content"])
        else:
            deadline = time.monotonic() + 5
            while store.load_turn(original["session_id"], original["turn_id"])["status"] != "running":
                assert time.monotonic() < deadline
                time.sleep(.01)
        event = provider.topic("mentioned-fast-control", "@Renamed assistant " + command,
                               root=root["message_id"])
        event["mentions"] = [{"name": "Renamed assistant", "key": "@_user_1", "id": "ou_notes_app_bot"}]
        provider.messages[event["message_id"]].update(
            body={"content": json.dumps({"text": "@_user_1 " + command})},
            mentions=event["mentions"])
        provider.calls.clear()
        assert transport.admit("notes-app", event)["status"] == "command_recorded"
        calls = list(provider.calls)
        assert not any("+messages-mget" in args for args in calls)
        reads = [args for args in calls if "GET" in args]
        assert len(reads) == 1 and reads[0][reads[0].index("GET") + 1].endswith("/mget")
        assert json.loads(reads[0][reads[0].index("--params") + 1]) == {
            "message_ids": [event["message_id"], root["message_id"]]}
        request = next(row for row in transport.core.pending() if row["command"] == command[1:])
        assert request["session_id"] == original["session_id"]
        assert request["message"] == event["content"]
        # Rendered names and wire keys are not persisted as a replacement
        # message; replay still reads current authority and the same raw pair.
        assert transport.admit("notes-app", event)["status"] == "command_recorded"
        assert transport.core.read_request(request["request_ref"]) == request
    finally:
        runtime.close()


@pytest.mark.parametrize("canonical_text,identity,expected", [
    ("Explain this public project", "ou_notes_app_bot", None),
    ("@_user_1 /help", "ou_notes_app_bot", "help"),
    ("@_user_1 /stop", "ou_other_bot", None),
])
def test_admitted_topic_mention_hint_cannot_supply_control_authority(
    ordinary, canonical_text, identity, expected,  # noqa: F811
):
    _, runtime, provider, transport = connect_group(ordinary)
    try:
        root = provider.topic("untrusted-fast-root", "Explain this public project")
        assert transport.admit("notes-app", root)["status"] == "durably_accepted"
        event = provider.topic("untrusted-fast-control", "@Renamed assistant /stop", root=root["message_id"])
        event["mentions"] = [{"name": "Renamed assistant", "key": "@_user_1", "id": "ou_notes_app_bot"}]
        provider.messages[event["message_id"]].update(
            body={"content": json.dumps({"text": canonical_text})},
            mentions=[{"name": "Renamed assistant", "key": "@_user_1", "id": identity}])
        result = transport.admit("notes-app", event)
        assert result["status"] == ("command_recorded" if expected else "durably_accepted")
        request = transport.core.read_request(identity_ref(
            transport._binding("notes-app")["provider_ref"], event["message_id"]))
        assert request["command"] == expected
        assert request["command"] != "stop"
    finally:
        runtime.close()


@pytest.mark.parametrize("raw_text,identity,key", [
    ("@_user_1 /stop", "ou_steward_app_bot", "@_user_1"),
    ("@notes-app /stop", "ou_notes_app_bot", "@_user_1"),
    ("Explain @_user_1 /stop", "ou_notes_app_bot", "@_user_1"),
    ("`@_user_1 /stop`", "ou_notes_app_bot", "@_user_1"),
    ("@_user_1 /stop after this", "ou_notes_app_bot", "@_user_1"),
    ("@_user_1/stop", "ou_notes_app_bot", "@_user_1"),
    ("@_user_1 /stop", "ou_notes_app_bot", ""),
])
def test_group_mention_text_does_not_invent_a_control(ordinary, raw_text, identity, key):  # noqa: F811
    _, runtime, provider, transport = connect_group(ordinary)
    try:
        root = provider.topic("ordinary-topic", "Explain this public project")
        assert transport.admit("notes-app", root)["status"] == "durably_accepted"
        finish_group_turn(runtime, transport, message=root["content"])
        rendered = raw_text.replace("@_user_1", "@notes-app")
        event = provider.topic("ordinary-mention", rendered, root=root["message_id"])
        provider.messages[event["message_id"]].update(
            body={"content": json.dumps({"text": raw_text})},
            mentions=[{"key": key, "name": "notes-app", "id": identity}])
        assert transport.admit("notes-app", event)["status"] == "durably_accepted"
        row = finish_group_turn(runtime, transport, message=rendered)
        assert row["message"].endswith(f"\n\nCurrent user message:\n{rendered}")
    finally:
        runtime.close()


@pytest.mark.parametrize("text,command", [("/stop", "stop"), ("/status", "status"),
                                         ("/help", "help"), ("/new", "new")])
@pytest.mark.parametrize("followup", [False, True])
def test_group_control_does_not_require_display_service(ordinary, text, command, followup):  # noqa: F811
    store, runtime, provider, transport = connect_group(ordinary)
    try:
        root = provider.topic("canonical-control-root", "/help") if followup else None
        event = provider.topic("canonical-control", text, root=root["message_id"] if root else None)

        def unavailable_display(args, cwd=None, timeout=None):
            if "+messages-mget" in args:
                return {"returncode": 1, "stdout": '{"ok":false}'}
            return provider(args, cwd, timeout)

        transport.runner = unavailable_display
        assert transport.admit("notes-app", event)["status"] == "command_recorded"
        request = transport.core.pending()[0]
        assert request["command"] == command and request["turn_id"] is None
        assert store.list_sessions() == []
        assert transport.admit("notes-app", event)["status"] == "command_recorded"
        assert transport.core.pending()[0] == request
        # Event hints cannot replace a fresh canonical read on the next attempt.
        provider.messages[(root or event)["message_id"]]["mentions"] = []
        assert transport.admit("notes-app", event)["status"] == "source_verification_failed"
        assert not provider.writes
    finally:
        runtime.close()


def test_group_old_rendered_control_keeps_exact_native_replay(ordinary):  # noqa: F811
    _, runtime, provider, transport = connect_group(ordinary)
    try:
        event = provider.topic("old-rendered-control", "/stop")
        message = provider.messages[event["message_id"]]
        message.update(content="Earlier thread context\n/stop", body={"content": '{"text":"/stop"}'})
        binding = transport._binding("notes-app")
        source = lark_private_source(provider_ref=binding["provider_ref"], event=event)
        request_ref = identity_ref(binding["provider_ref"], event["message_id"])
        original = transport.core.admit(binding_id=binding["binding_id"], source=source,
            request_ref=request_ref, message=message["content"], command="stop")
        assert transport.admit("notes-app", event)["status"] == "command_recorded"
        assert transport.core.read_request(request_ref) == original
        assert any("+messages-mget" in args for args in provider.calls)
    finally:
        runtime.close()


def test_group_control_hint_cannot_override_canonical_ordinary_text(ordinary):  # noqa: F811
    _, runtime, provider, transport = connect_group(ordinary)
    try:
        event = provider.topic("forged-control-hint", "/stop")
        provider.messages[event["message_id"]]["content"] = "Explain the public project"
        assert transport.admit("notes-app", event)["status"] == "durably_accepted"
        request = transport.core.pending()[0]
        assert request["command"] is None and request["message"] == "Explain the public project"
        assert any("+messages-mget" in args for args in provider.calls)
    finally:
        runtime.close()


@pytest.mark.parametrize("body", ["invalid json", "{}", '{"text":null}', '{"text":"  "}'])
def test_group_display_cannot_supply_a_control_missing_from_canonical_text(ordinary, body):  # noqa: F811
    store, runtime, provider, transport = connect_group(ordinary)
    try:
        event = provider.topic("invalid-control-source", "/stop")
        provider.messages[event["message_id"]]["body"] = {"content": body}
        assert transport.admit("notes-app", event)["status"] == "source_verification_failed"
        assert not transport.core.pending() and not store.list_sessions()
        assert not list(transport.root.glob("*.json")) and not provider.writes
    finally:
        runtime.close()


@pytest.mark.parametrize("group", [False, True])
@pytest.mark.parametrize("command", ["/stop", "/status", "/help", "/new"])
def test_completed_control_reply_precedes_optional_received_reaction(ordinary, monkeypatch, group, command):  # noqa: F811
    store, runtime, provider, transport = (connect_group if group else connect)(ordinary)
    original = Provider.__call__
    try:
        def reaction_after_reply(self, args, cwd=None, timeout=None):
            if "reactions" in args and "create" in args:
                assert self.writes, "optional receipt feedback must not delay the control result"
            return original(self, args, cwd, timeout)

        monkeypatch.setattr(Provider, "__call__", reaction_after_reply)
        event = provider.topic("control-order", command) if group else provider.event("notes-app", "control-order", command)
        assert transport.admit("notes-app", event)["status"] == "command_recorded"
        transport.reconcile()
        record = json.loads(next(transport.root.glob("*.json")).read_text())
        assert record["status"] == "delivered" and record["deliveries"]["terminal"]["verified"]
        assert record["feedback"]["received"]["ok"]
        assert len(provider.writes) == 1 and len(provider.reaction_creates) == 1
        transport.reconcile()
        assert len(provider.writes) == 1 and len(provider.reaction_creates) == 1
        assert store.list_sessions() == []
    finally:
        runtime.close()


def test_group_provenance_and_revocation_prevent_private_or_wrong_topic_delivery(ordinary):  # noqa: F811
    store, runtime, provider, transport = connect_group(ordinary)
    try:
        for event in [provider.event("notes-app", "private", "private query"),
                      provider.topic("foreign", "foreign", chat="oc_not_selected"),
                      provider.topic("unaddressed", "ordinary community conversation", addressed=False)]:
            assert transport.admit("notes-app", event)["status"] in {"audience_rejected", "source_verification_failed"}
        root = provider.topic("root", "topic one")
        forged = provider.topic("forged", "wrong root", root=root["message_id"])
        provider.messages[forged["message_id"]]["root_id"] = "om_other_root"
        assert transport.admit("notes-app", forged)["status"] == "source_verification_failed"
        assert not store.list_sessions() and not list(transport.root.glob("*.json"))
        assert transport.admit("notes-app", root)["status"] == "durably_accepted"
        row = next(row for row in transport.core.pending() if row["message"] == root["content"])
        assert runtime.wait_for_turn(session_id=row["session_id"], turn_id=row["turn_id"], timeout_sec=10)["status"] == "completed"
        binding = transport._binding("notes-app")
        transport.bindings.disconnect(binding["binding_id"], expected_revision=transport.bindings.read()["revision"])
        transport.reconcile()
        assert provider.writes == []  # Even a completed result needs current source authority.
        assert transport.admit("notes-app", root)["status"] == "audience_rejected"
    finally:
        runtime.close()


@pytest.mark.parametrize("mentions", [
    [{"name": "notes-app", "id": {"open_id": "ou_steward_app_bot"}}],
    [{"name": "notes-app", "id": {"app_id": "cli_steward_app"}}],
    [{"name": "notes-app"}],
    None,
])
def test_group_root_requires_this_apps_provider_mention_before_capture(ordinary, mentions):  # noqa: F811
    store, runtime, provider, transport = connect_group(ordinary)
    try:
        root = provider.topic("wrong-recipient", "Explain this project")
        message = provider.messages[root["message_id"]]
        message["mentioned"] = True
        if mentions is None:
            message.pop("mentions")
        else:
            message["mentions"] = mentions
        assert transport.admit("notes-app", root)["status"] == "source_verification_failed"
        assert not store.list_sessions() and not list(transport.root.glob("*.json"))
        assert provider.writes == []
    finally:
        runtime.close()


@pytest.mark.parametrize("identity", [{"open_id": "ou_notes_app_bot"}, {"app_id": "cli_notes_app"}])
def test_group_provider_mention_survives_a_bot_rename_and_topic_followup(ordinary, identity):  # noqa: F811
    _, runtime, provider, transport = connect_group(ordinary)
    try:
        root = provider.topic("renamed-bot", "Explain this project")
        provider.messages[root["message_id"]]["mentions"] = [{"name": "Renamed project assistant", "id": identity}]
        assert transport.admit("notes-app", root)["status"] == "durably_accepted"
        first = finish_group_turn(runtime, transport, message=root["content"])
        followup = provider.topic("renamed-followup", "Compare its configuration", root=root["message_id"])
        assert transport.admit("notes-app", followup)["status"] == "durably_accepted"
        second = finish_group_turn(runtime, transport, message=followup["content"])
        assert second["session_id"] == first["session_id"]
        assert all(topic == root["message_id"] for _, topic, _ in provider.topic_writes)
    finally:
        runtime.close()


@pytest.mark.parametrize("is_reply", [False, True])
def test_group_readback_accepts_null_event_fields_and_provider_enriched_thread(ordinary, is_reply):  # noqa: F811
    _, runtime, provider, transport = connect_group(ordinary)
    try:
        root = provider.topic("compact-topic", "Explain this project")
        # The CLI event projection emits null for absent fields; mget can
        # supply the topic's thread id even when the event did not carry it.
        root.update(root_id=None, parent_id=None, thread_id=None)
        provider.messages[root["message_id"]]["thread_id"] = "omt_compact_topic"
        provider.messages[root["message_id"]]["mentions"] = [{"id": "cli_notes_app", "name": "notes-app"}]
        event = root
        if is_reply:
            event = provider.topic("compact-reply", "Compare its configuration", root=root["message_id"])
            event["thread_id"] = None
            provider.messages[event["message_id"]]["thread_id"] = "omt_compact_topic"
        assert transport.admit("notes-app", event)["status"] == "durably_accepted"
        finish_group_turn(runtime, transport, message=event["content"])
        assert all(topic == root["message_id"] for _, topic, _ in provider.topic_writes)
    finally:
        runtime.close()


@pytest.mark.parametrize("field,value", [("parent_id", "om_wrong_parent"), ("thread_id", "omt_wrong_thread")])
def test_group_readback_rejects_supplied_conflicting_ancestry(ordinary, field, value):  # noqa: F811
    store, runtime, provider, transport = connect_group(ordinary)
    try:
        root = provider.topic("conflicting-topic", "Explain this project")
        event = provider.topic("conflicting-reply", "Continue", root=root["message_id"])
        event[field] = value
        assert transport.admit("notes-app", event)["status"] == "source_verification_failed"
        assert not store.list_sessions() and not list(transport.root.glob("*.json"))
        assert provider.writes == []
    finally:
        runtime.close()


def test_group_reply_uses_raw_ancestry_when_display_readback_omits_it(ordinary):  # noqa: F811
    _, runtime, provider, transport = connect_group(ordinary)
    root = provider.topic("display-root", "Explain this project")
    reply = provider.topic("display-reply", "Compare its configuration", root=root["message_id"])

    def rendered_provider(args, cwd=None, timeout=None):
        result = provider(args, cwd, timeout)
        if "+messages-mget" in args:
            payload = json.loads(result["stdout"])
            for message in payload["data"]["items"]:
                message = dict(message)
                message.pop("root_id", None)
                message.pop("parent_id", None)
                payload["data"]["items"] = [message]
            return {**result, "stdout": json.dumps(payload)}
        return result

    transport.runner = rendered_provider
    try:
        assert transport.admit("notes-app", root)["status"] == "durably_accepted"
        first = finish_group_turn(runtime, transport, message=root["content"])
        assert transport.admit("notes-app", reply)["status"] == "durably_accepted"
        second = finish_group_turn(runtime, transport, message=reply["content"])
        assert second["session_id"] == first["session_id"]
        assert all(topic == root["message_id"] for _, topic, _ in provider.topic_writes)
        assert len([text for _, _, text in provider.topic_writes if text == "Runtime response."]) == 2
    finally:
        runtime.close()


@pytest.mark.parametrize("failure", ["unavailable", "code_error", "wrong_sender", "wrong_chat", "wrong_type", "wrong_root"])
@pytest.mark.parametrize("control", [False, True])
def test_group_raw_readback_failure_cannot_fall_back_to_display_authority(ordinary, failure, control):  # noqa: F811
    store, runtime, provider, transport = connect_group(ordinary)
    root = provider.topic("raw-root", "Explain this project")
    reply = provider.topic("raw-reply", "/stop" if control else "Compare its configuration", root=root["message_id"])

    def conflicting_provider(args, cwd=None, timeout=None):
        result = provider(args, cwd, timeout)
        if "api" in args and "GET" in args:
            if failure == "unavailable":
                return {"returncode": 1, "stdout": '{"code":999}', "stderr": ""}
            if failure == "code_error":
                return {**result, "stdout": json.dumps({**json.loads(result["stdout"]), "code": 999})}
            payload = json.loads(result["stdout"])
            message = dict(payload["data"]["items"][0])
            message.update({"wrong_sender": {"sender": {"id": "ou_other", "sender_type": "user"}},
                            "wrong_chat": {"chat_id": "oc_other"}, "wrong_type": {"msg_type": "image"},
                            "wrong_root": {"root_id": "om_other_root"}}[failure])
            payload["data"]["items"] = [message]
            return {**result, "stdout": json.dumps(payload)}
        return result

    transport.runner = conflicting_provider
    try:
        assert transport.admit("notes-app", reply)["status"] == "source_verification_failed"
        assert not store.list_sessions() and not list(transport.root.glob("*.json"))
        assert not provider.writes and not provider.reaction_creates
    finally:
        runtime.close()


@pytest.mark.parametrize("kind", ["image", "post"])
def test_group_raw_provenance_preserves_rendered_media_and_original_topic(ordinary, kind):  # noqa: F811
    from test_lark_private_images import image_runner
    from test_chat_image_attachments import PNG_DATA_URL

    _, runtime, provider, transport = connect_group(ordinary)
    root = provider.topic("media-root", "Explain this project")
    media = provider.topic("media-reply", "Inspect this diagram\n![Image](img_example)", root=root["message_id"])
    media["message_type"] = kind
    provider.messages[media["message_id"]]["msg_type"] = kind
    download = image_runner(provider)

    def raw_media_provider(args, cwd=None, timeout=None):
        result = download(args, cwd, timeout)
        if "api" in args and "GET" in args:
            payload = json.loads(result["stdout"])
            message = payload["data"]["items"][0]
            if message.get("msg_type") == kind:
                message.pop("content")
                body = {"image_key": "img_example"} if kind == "image" else {
                    "zh_cn": {"title": "", "content": [[{"tag": "text", "text": "Inspect this diagram"},
                                                       {"tag": "img", "image_key": "img_example"}]]}}
                message["body"] = {"content": json.dumps(body)}
            return {**result, "stdout": json.dumps(payload)}
        return result

    transport.runner = raw_media_provider
    try:
        assert transport.admit("notes-app", root)["status"] == "durably_accepted"
        first = finish_group_turn(runtime, transport, message=root["content"])
        assert transport.admit("notes-app", media)["status"] == "durably_accepted"
        second = finish_group_turn(runtime, transport, message="Inspect this diagram\n[图片 1]")
        assert second["session_id"] == first["session_id"]
        assert second["attachments"][0]["data_url"] == PNG_DATA_URL
        assert all(topic == root["message_id"] for _, topic, _ in provider.topic_writes)
    finally:
        runtime.close()


def test_group_status_and_recipient_commands_cannot_expose_private_sessions(ordinary):  # noqa: F811
    store, runtime, provider, transport = connect_group(ordinary)
    try:
        root = provider.topic("status-root", "/status")
        assert transport.admit("notes-app", root)["status"] == "command_recorded"
        help_event = provider.topic("help", "/help", root=root["message_id"])
        assert transport.admit("notes-app", help_event)["status"] == "command_recorded"
        for name, command in [("agents", "/agents"), ("agent", "/agent secret"), ("project", "/project")]:
            assert transport.admit("notes-app", provider.topic(name, command, root=root["message_id"]))["status"] == "command_recorded"
        transport.reconcile()
        assert not store.list_sessions()
        replies = "\n".join(text for _, text in provider.writes)
        assert str(ordinary[-1]) not in replies and "此 Bot" in replies
        assert "不能选择个人 Agent" in replies
        assert all(root_id == root["message_id"] for _, root_id, _ in provider.topic_writes)
    finally:
        runtime.close()


@pytest.mark.parametrize("owner_ready", [True, False])
def test_http_group_setup_observes_membership_reads_back_selection_and_rejects_widening(ordinary, monkeypatch, owner_ready):  # noqa: F811
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
    from loopx.extensions.lark.cli_resolution import LarkCliResolution
    import loopx.chat_lark_api as api

    store, runtime, provider, transport = connect(ordinary)
    monkeypatch.setattr(api, "build_lark_goal_topic_runtime_snapshot", lambda **_: {"goals": []})
    monkeypatch.setattr(ChatRequestHandler, "_lark_runner", lambda _: provider)
    refreshes = []
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.chat_store, server.runtime_controller = store, runtime
    server.verbose = False
    server.registry_path, server.runtime_root_override = runtime.registry_path, store.root.parent
    server.lark_cli_resolution = LarkCliResolution(command="lark-cli", available=True, source="explicit", version="fixture", error_code=None)
    server.lark_private_conversations = transport
    server.lark_goal_topic_runtime = SimpleNamespace(refresh=lambda: refreshes.append(True), health_snapshot=lambda: {}, close=lambda: None)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(path, body=None):
        connection = http.client.HTTPConnection(*server.server_address, timeout=15)
        try:
            connection.request("GET" if body is None else "POST", path,
                None if body is None else json.dumps(body), headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    path = "/api/chat/lark/private-conversations"
    body = {"app_ref": "notes-app", "project_ref": runtime.project_contexts.available()[0]["project_ref"],
        "executor_endpoint_id": "codex", "audience": "group", "group_ids": ["oc_community"]}
    provider.owner_ready = owner_ready
    try:
        original = transport.bindings.read()
        for bad in [{"group_ids": ["oc_not_selected"]}, {"group_ids": []},
                    {"group_ids": ["oc_community", "oc_community"]}, {"context_kind": "steward"},
                    {"executor_endpoint_id": "claude-code"}]:
            assert request(path, {**body, **bad})[0] == 400
            assert transport.bindings.read() == original and not refreshes
        status, response = request(path, body)
        assert status == 200, response
        saved = next(row for row in response["connections"] if row["app_ref"] == "notes-app")
        assert saved["audience"] == "group" and saved["group_count"] == 1
        assert saved["agent_candidates"] == saved["agent_targets"] == []
        assert str(ordinary[-1]) not in json.dumps(response)
        selected = request("/api/chat/lark/chats?app_ref=notes-app")[1]["chats"]
        assert [(row["chat_id"], row["selected"]) for row in selected] == [("oc_community", True), ("oc_second", False)]
        assert request(path, body)[1]["revision"] == response["revision"]
        assert refreshes == [True, True]
        assert not store.list_sessions() and not provider.writes
        if not owner_ready:
            frozen = transport.bindings.read()
            assert request(path, {key: value for key, value in body.items() if key not in {"audience", "group_ids"}})[0] == 400
            assert transport.bindings.read() == frozen
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        runtime.close()


def test_http_stop_recovery_reuses_source_revision_without_reusing_turn_state(ordinary, monkeypatch):  # noqa: F811
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
    from loopx.control_plane import effect_runtime

    store, runtime, provider, transport = connect(ordinary)
    binding = transport._binding("notes-app")
    sessions = [store.create_session(goal_id="example-goal", agent_id="codex",
        adapter_kind="codex_app_server", upstream_thread_id=f"idle-{index}") for index in range(8)]
    sid = sessions[0]["session_id"]
    revoked = "a" * 24
    def queue(name, binding_id):
        return store.create_queued_turn(sid, client_turn_id=name, message=name, origin="external",
            external_agent_target={"context": {"binding_id": binding_id,
                "project_ref": binding["project_ref"]}, "target": {"session_id": sid}})[0]
    owed = queue("revoked-queued-work", revoked)
    authorized = queue("authorized-queued-work", binding["binding_id"])
    original_binding = transport.bindings.path.read_bytes()
    original_turns = [store._turn_path(sid, turn["turn_id"]).read_bytes() for turn in [owed, authorized]]
    native_before = ordinary[4].read_bytes() if ordinary[4].exists() else None
    revision_reads = []
    observed = threading.local()
    fingerprint = effect_runtime._runtime_fingerprint
    stop_recoveries = ChatConversationBindings.stop_recoveries

    def counted_fingerprint():
        if getattr(observed, "stop_recovery", False):
            revision_reads.append(True)
        return fingerprint()

    def observed_stop_recoveries(self, current):
        observed.stop_recovery = True
        try:
            return stop_recoveries(self, current)
        finally:
            observed.stop_recovery = False

    monkeypatch.setattr(effect_runtime, "_runtime_fingerprint", counted_fingerprint)
    monkeypatch.setattr(ChatConversationBindings, "stop_recoveries", observed_stop_recoveries)
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.chat_store, server.runtime_controller, server.verbose = store, runtime, False
    server.lark_private_conversations = transport
    server.lark_goal_topic_runtime = SimpleNamespace(health_snapshot=lambda: {}, close=lambda: None)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def snapshot():
        revision_reads.clear()
        connection = http.client.HTTPConnection(*server.server_address, timeout=15)
        try:
            connection.request("GET", "/api/chat/lark/private-conversations")
            response = connection.getresponse()
            assert response.status == 200
            result = json.loads(response.read())
            assert len(revision_reads) <= 1  # At most one source inventory, rather than one per Session.
            return result
        finally:
            connection.close()

    try:
        assert snapshot()["stop_recoveries"] == [{"binding_id": revoked,
            "project_ref": binding["project_ref"], "project_title": ordinary[-1].name, "pending_count": 1}]
        assert [store._turn_path(sid, turn["turn_id"]).read_bytes() for turn in [owed, authorized]] == original_turns
        store.update_turn(sid, owed["turn_id"], status="interrupted")
        assert snapshot()["stop_recoveries"] == []  # A later read observes settlement immediately.
        assert store.load_turn(sid, authorized["turn_id"])["status"] == "queued"
        assert transport.bindings.path.read_bytes() == original_binding
        assert (ordinary[4].read_bytes() if ordinary[4].exists() else None) == native_before
        assert not provider.writes
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        runtime.close()


def test_http_listener_health_belongs_to_each_bound_transport(ordinary):  # noqa: F811
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
    from loopx.capabilities.native_chat.transports import ChatConversationTransports
    from test_chat_transport_composition import Transport

    store, runtime, provider, lark = connect(ordinary)
    external = Transport("external-owner")
    composed = ChatConversationTransports(observe_default=lark.bindings.observe, transports=[external])
    bindings = ChatConversationBindings(root=store.root, project_contexts=runtime.project_contexts,
                                       observe=composed.observe)
    runtime.project_contexts.conversation_bindings = bindings
    bindings.configure(transport_ref=external.transport_ref,
        project_ref=runtime.project_contexts.available()[0]["project_ref"], executor_endpoint_id="codex")
    original = bindings.read()
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.chat_store, server.runtime_controller, server.verbose = store, runtime, False
    server.lark_private_conversations, server.conversation_transports = lark, composed
    # A stale Lark row must not mask the current locally installed provider.
    server.lark_goal_topic_runtime = SimpleNamespace(health_snapshot=lambda: {
        "notes-app": {"status": "listening"}, "steward-app": {"status": "starting"},
        "external-owner": {"status": "stopped"}}, close=lambda: None)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def snapshot():
        connection = http.client.HTTPConnection(*server.server_address, timeout=15)
        try:
            connection.request("GET", "/api/chat/lark/private-conversations")
            response = connection.getresponse()
            assert response.status == 200
            return {row["app_ref"]: row for row in json.loads(response.read())["connections"]}
        finally:
            connection.close()

    def unavailable():
        raise OSError("provider credential must not be exposed")

    try:
        for state in ["starting", "listening", "retrying", "stopped", "standby", "inactive"]:
            external.health_snapshot = lambda: {"status": state, "credential": "private-provider-data"}
            rows = snapshot()
            assert rows["external-owner"]["listener_status"] == state
            assert rows["notes-app"]["listener_status"] == "listening"
            assert rows["steward-app"]["listener_status"] == "starting"
            assert "private-provider-data" not in json.dumps(rows)
        for malformed in [None, [], {"status": "private-provider-data"}, {"status": []}]:
            external.health_snapshot = lambda: malformed
            assert snapshot()["external-owner"]["listener_status"] == "unknown"
        external.health_snapshot = unavailable
        assert snapshot()["external-owner"]["listener_status"] == "unknown"
        del external.health_snapshot
        assert snapshot()["external-owner"]["listener_status"] == "unknown"
        del server.conversation_transports
        server.lark_goal_topic_runtime = SimpleNamespace(health_snapshot=lambda: {}, close=lambda: None)
        assert all(row["listener_status"] == "unknown" for row in snapshot().values())
        assert bindings.read() == original and not store.list_sessions() and not provider.writes
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        runtime.close()


def test_bot_only_group_continues_after_user_logout_but_rejects_private_and_app_drift(ordinary):  # noqa: F811
    store, runtime, provider, transport = connect_group(ordinary)
    try:
        provider.owner_ready = False
        root = provider.topic("bot-only-root", "Explain the project")
        assert transport.admit("notes-app", root)["status"] == "durably_accepted"
        first = finish_group_turn(runtime, transport, message=root["content"])
        home = store.load_session(first["session_id"])["codex_home"]
        # Signing in a user later must not change this App-owned principal/home.
        provider.owner_ready = True
        follow = provider.topic("bot-only-follow", "Compare the config", root=root["message_id"])
        assert transport.admit("notes-app", follow)["status"] == "durably_accepted"
        second = finish_group_turn(runtime, transport, message=follow["content"])
        assert second["session_id"] == first["session_id"]
        assert store.load_session(second["session_id"])["codex_home"] == home
        private = provider.event("notes-app", "private-for-group", "List personal tasks")
        assert transport.admit("notes-app", private)["status"] == "audience_rejected"
        provider.profile_apps["notes-app"] = "cli_rotated_app"
        changed = provider.topic("rotated-app", "Explain config", root=root["message_id"])
        assert transport.admit("notes-app", changed)["status"] == "audience_rejected"
    finally:
        runtime.close()


def test_group_identity_requires_verified_bot_and_private_identity_requires_verified_user():
    provider = Provider()
    provider.owner_ready = False
    group = observe_lark_conversation_identity(profile="notes-app", runner=provider, cli_bin="lark-cli", audience="group")
    assert group["operator_ref"] != identity_ref(group["provider_ref"], "ou_notes_app")
    from loopx.extensions.lark.conversation_identity import LarkOwnerIdentityUnavailable
    with pytest.raises(LarkOwnerIdentityUnavailable):
        observe_lark_conversation_identity(profile="notes-app", runner=provider, cli_bin="lark-cli")
    provider.bot_ready = False
    with pytest.raises(ValueError, match="App identity"):
        observe_lark_conversation_identity(profile="notes-app", runner=provider, cli_bin="lark-cli", audience="group")


def test_group_outbound_source_verification_reads_fresh_envelopes_without_display_projection(ordinary):  # noqa: F811
    _, runtime, provider, transport = connect_group(ordinary)
    try:
        root = provider.topic("outbound-root", "/status")
        event = provider.topic("outbound-follow", "/status", root=root["message_id"])
        assert transport.admit("notes-app", event)["status"] == "command_recorded"
        record = json.loads(next(transport.root.glob("*.json")).read_text())
        provider.calls.clear()
        assert transport._source_verified(record)
        assert not any("+messages-mget" in args for args in provider.calls)
        assert {args[args.index("GET") + 1] for args in provider.calls if "GET" in args} == {
            f"/open-apis/im/v1/messages/{root['message_id']}",
            f"/open-apis/im/v1/messages/{event['message_id']}",
        }
        # Every outbound attempt must re-read the root: no cross-attempt cache.
        provider.messages[root["message_id"]]["mentions"] = []
        assert not transport._source_verified(record)
        assert not provider.writes
    finally:
        runtime.close()


@pytest.fixture
def completed_group_stop(ordinary):  # noqa: F811
    _, runtime, provider, transport = connect_group(ordinary)
    try:
        root = provider.topic("batch-root", "wait for interrupt")
        assert transport.admit("notes-app", root)["status"] == "durably_accepted"
        request = transport.core.pending()[0]
        deadline = time.monotonic() + 5
        while runtime.store.load_turn(request["session_id"], request["turn_id"])["status"] != "running":
            assert time.monotonic() < deadline
            time.sleep(.01)
        event = provider.topic("batch-stop", "/stop", root=root["message_id"])
        assert transport.admit("notes-app", event)["status"] == "command_recorded"
        assert runtime.wait_for_turn(session_id=request["session_id"],
            turn_id=request["turn_id"], timeout_sec=5)["status"] == "interrupted"
        record = next(json.loads(p.read_text()) for p in transport.root.glob("*.json")
                      if json.loads(p.read_text())["event"]["message_id"] == event["message_id"])
        assert transport.core.read_request(record["request_ref"])["target_recorded"]
        provider.calls.clear()
        yield runtime, provider, transport, root, event, record
    finally:
        runtime.close()


def test_completed_stop_batches_its_core_bound_source_and_root_with_one_current_identity(completed_group_stop):
    _, provider, transport, root, event, record = completed_group_stop
    assert transport._source_verified(record)
    calls = [a for a in provider.calls if "GET" in a]
    assert len(calls) == 1 and calls[0][calls[0].index("GET") + 1].endswith("/mget")
    assert json.loads(calls[0][calls[0].index("--params") + 1]) == {
        "message_ids": [event["message_id"], root["message_id"]]}
    assert sum("auth" in a for a in provider.calls) == 1
    provider.messages[root["message_id"]]["mentions"] = []
    assert not transport._source_verified(record)  # Re-read, never cache the old root.
    assert not provider.writes and not provider.reaction_creates


def test_completed_stop_inbox_name_cannot_replace_fresh_source_checks(completed_group_stop, monkeypatch):
    _, provider, transport, root, event, record = completed_group_stop
    transport._inbox(record)  # The preceding ordinary topic already has an Inbox.
    path = transport.root / f"{record['request_ref']}.json"
    entered, release = threading.Event(), threading.Event()
    original = transport._source_verified

    def verify(row, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(row, **kwargs)

    monkeypatch.setattr(transport, "_source_verified", verify)
    with ThreadPoolExecutor(max_workers=1) as workers:
        pending = workers.submit(transport.reconcile_request, path)
        try:
            assert entered.wait(2)
            assert not provider.writes and not provider.reaction_creates
            # The hint cannot retain authority when the root loses its @.
            provider.messages[root["message_id"]]["mentions"] = []
        finally:
            release.set()
        assert pending.result(timeout=3) == 0
    assert not provider.writes and not provider.reaction_creates
    provider.messages[root["message_id"]]["mentions"] = [{"id": {"open_id": "ou_notes_app_bot"}}]
    assert transport.reconcile_request(path) == 1
    assert provider.topic_writes == [(event["chat_id"], root["message_id"], "已请求停止这条消息对应的执行。")]
    assert transport.reconcile_request(path) == 0 and len(provider.writes) == 1


@pytest.mark.parametrize("change", ["app", "bot", "revoke", "sender", "chat", "root", "parent", "thread"])
def test_completed_stop_cached_name_does_not_authorize_a_changed_source(completed_group_stop, change):
    _, provider, transport, _, event, record = completed_group_stop
    transport._inbox(record)
    if change == "app":
        provider.profile_apps["notes-app"] = "cli_other"
    elif change == "bot":
        provider.bot_ready = False
    elif change == "revoke":
        transport.bindings.disconnect(record["binding_id"], expected_revision=transport.bindings.read()["revision"])
    else:
        field, value = {"sender": ("sender", {"id": "ou_other", "sender_type": "user"}),
            "chat": ("chat_id", "oc_other"), "root": ("root_id", "om_other"),
            "parent": ("parent_id", "om_other"), "thread": ("thread_id", "omt_other")}[change]
        provider.messages[event["message_id"]][field] = value
    assert transport.reconcile_request(transport.root / f"{record['request_ref']}.json") == 0
    assert not provider.writes and not provider.reaction_creates


def test_completed_stop_verified_reply_rechecks_grant_before_feedback(completed_group_stop):
    _, provider, transport, _, _, record = completed_group_stop
    config = transport._inbox(record)
    path = transport.root / f"{record['request_ref']}.json"
    # Model a crash after verified reply, before the original source's cleanup.
    assert transport._deliver(path, record, "terminal", "已请求停止这条消息对应的执行。", inbox=lambda: config)
    writes = list(provider.writes)
    assert not provider.reaction_creates
    transport.bindings.disconnect(record["binding_id"], expected_revision=transport.bindings.read()["revision"])
    assert transport.reconcile_request(path) == 0
    assert provider.writes == writes and not provider.reaction_creates
    assert json.loads(path.read_text())["status"] == "command_completed"


def test_completed_stop_refreshes_a_renamed_bot_without_resending_an_attempt(completed_group_stop, monkeypatch):
    _, provider, transport, root, event, record = completed_group_stop
    config = transport._inbox(record)
    original = Provider.__call__
    def renamed(self, args, cwd=None, timeout=None):
        result = original(self, args, cwd, timeout)
        if "auth" in args:
            value = json.loads(result["stdout"])
            value["identities"]["bot"]["appName"] = "Renamed Bot"
            result["stdout"] = json.dumps(value)
        return result
    monkeypatch.setattr(Provider, "__call__", renamed)
    path = transport.root / f"{record['request_ref']}.json"
    assert transport.reconcile_request(path) == 0 and not provider.writes
    assert json.loads(config.read_text())["reply"]["bot_display_name"] == "Renamed Bot"
    assert not json.loads(path.read_text())["deliveries"]["terminal"].get("started")
    assert transport.reconcile_request(path) == 1
    assert provider.topic_writes == [(event["chat_id"], root["message_id"], "已请求停止这条消息对应的执行。")]
    assert transport.reconcile_request(path) == 0 and len(provider.writes) == 1


@pytest.mark.parametrize("invalid", ["missing", "malformed", "foreign_profile", "foreign_chat"])
def test_completed_stop_rebuilds_unusable_inbox_metadata(completed_group_stop, invalid):
    _, provider, transport, root, event, record = completed_group_stop
    config = transport._inbox(record)
    value = json.loads(config.read_text())
    if invalid == "missing":
        config.unlink()
    elif invalid == "malformed":
        config.write_text('{')
    else:
        value["reply"]["sender_profile" if invalid == "foreign_profile" else "chat_id"] = "foreign"
        value["reply"]["bot_display_name"] = "Foreign Bot"
        config.write_text(json.dumps(value))
    assert transport.reconcile_request(transport.root / f"{record['request_ref']}.json") == 1
    assert provider.topic_writes == [(event["chat_id"], root["message_id"], "已请求停止这条消息对应的执行。")]


@pytest.mark.parametrize("field,value", [("target_recorded", False), ("command", "status"),
    ("status", "prepared"), ("binding_id", "other"), ("source", {}), ("request_ref", "other")])
def test_completed_stop_batch_requires_the_persisted_core_proof(completed_group_stop, field, value):
    _, provider, transport, _, _, record = completed_group_stop
    path = transport.core.root / f"{record['request_ref']}.json"
    native = json.loads(path.read_text())
    path.write_text(json.dumps({**native, field: value}))
    transport._source_verified(record)
    assert not any("GET" in a and a[a.index("GET") + 1].endswith("/mget") for a in provider.calls)
    assert not provider.writes and not provider.reaction_creates


@pytest.mark.parametrize("change", ["sender", "chat", "type", "root", "parent", "thread",
    "mention", "root_sender", "root_parent", "root_chat", "app", "bot", "revoke"])
def test_completed_stop_batch_retains_source_and_grant_fences(completed_group_stop, change):
    _, provider, transport, root, event, record = completed_group_stop
    message, original = provider.messages[event["message_id"]], provider.messages[root["message_id"]]
    if change in {"sender", "chat", "type", "root", "parent", "thread"}:
        field, value = {"sender": ("sender", {"id": "ou_other", "sender_type": "user"}),
            "chat": ("chat_id", "oc_other"), "type": ("msg_type", "image"),
            "root": ("root_id", "om_other"), "parent": ("parent_id", "om_other"),
            "thread": ("thread_id", "omt_other")}[change]
        message[field] = value
    elif change == "mention":
        original["mentions"] = []
    elif change == "root_sender":
        original["sender"] = {"id": "ou_other", "sender_type": "app"}
    elif change == "root_parent":
        original["parent_id"] = "om_other"
    elif change == "root_chat":
        original["chat_id"] = "oc_other"
    elif change == "app":
        provider.profile_apps["notes-app"] = "cli_other"
    elif change == "bot":
        provider.bot_ready = False
    else:
        transport.bindings.disconnect(record["binding_id"], expected_revision=transport.bindings.read()["revision"])
    assert not transport._source_verified(record)
    assert not provider.writes and not provider.reaction_creates


@pytest.mark.parametrize("failure", ["missing", "duplicate", "extra", "error", "unavailable", "bad_id"])
def test_completed_stop_batch_rejects_partial_or_ambiguous_results(completed_group_stop, failure):
    _, provider, transport, _, _, record = completed_group_stop
    def runner(args, cwd=None, timeout=None):
        result = provider(args, cwd, timeout)
        if "GET" in args and args[args.index("GET") + 1].endswith("/mget"):
            data = json.loads(result["stdout"])
            items = data["data"]["items"]
            if failure == "missing":
                items.pop()
            elif failure == "duplicate":
                items[1] = items[0]
            elif failure == "extra":
                items.append({"message_id": "om_unrequested"})
            elif failure == "error":
                data["code"] = 999
            elif failure == "bad_id":
                items[0]["message_id"] = []
            else:
                result["returncode"] = 1
            result["stdout"] = json.dumps(data)
        return result
    transport.runner = runner
    assert not transport._source_verified(record)
    assert not provider.writes and not provider.reaction_creates


@pytest.mark.parametrize("change", ["unverified_app", "other_app", "root_mention", "source_parent"])
def test_completed_stop_source_verification_reuses_one_current_observation_only_within_the_call(
    completed_group_stop, change,
):
    _, provider, transport, _, _, record = completed_group_stop
    provider.calls.clear()
    assert transport._source_verified(record)
    # The binding check already verified this App. Its observation also checks
    # the root mention; a second network identity probe adds no new authority.
    assert sum("auth" in args for args in provider.calls) == 1
    if change == "unverified_app":
        provider.bot_ready = False
    elif change == "other_app":
        provider.profile_apps[record["profile"]] = "cli_other_app"
    elif change == "root_mention":
        provider.messages[record["event"]["root_id"]]["mentions"] = []
    else:
        provider.messages[record["event"]["message_id"]]["parent_id"] = "om_other_parent"
    provider.calls.clear()
    assert not transport._source_verified(record)
    assert sum("auth" in args for args in provider.calls) == 1
    assert not provider.writes and not provider.reaction_creates


@pytest.mark.parametrize("identity_fails", [False, True])
def test_completed_stop_preselected_without_observation_joins_a_fresh_identity(completed_group_stop, monkeypatch, identity_fails):
    _, provider, transport, _, _, record = completed_group_stop
    started, observed, release = threading.Event(), threading.Event(), threading.Event()
    original_sources, original_observe = transport._group_source_pair, transport.bindings.observe
    observations = []

    def sources(*args):
        started.set()
        assert release.wait(3)
        return original_sources(*args)

    def observe(*args, **kwargs):
        observations.append(True)
        if len(observations) == 2:
            assert started.wait(2)
            observed.set()
            if identity_fails:
                raise ValueError("fresh App observation unavailable")
        return original_observe(*args, **kwargs)

    monkeypatch.setattr(transport, "_group_source_pair", sources)
    monkeypatch.setattr(transport.bindings, "observe", observe)
    selected = transport.bindings.resolve(binding_id=record["binding_id"], **record["source"])
    with ThreadPoolExecutor(max_workers=1) as caller:
        pending = caller.submit(transport._source_message, record, selected=selected, render_content=False)
        try:
            assert started.wait(2) and observed.wait(2)
            assert not pending.done()
        finally:
            release.set()
        assert bool(pending.result(timeout=3)) is not identity_fails
    assert len(observations) == 2
    assert not provider.writes and not provider.reaction_creates


@pytest.mark.parametrize("failure", [None, "identity", "source", "revoked"])
def test_completed_stop_default_overlaps_readonly_observations_but_joins_and_rechecks_authority(
    completed_group_stop, monkeypatch, failure,
):
    _, provider, transport, _, _, record = completed_group_stop
    source_started, identity_started, release = threading.Event(), threading.Event(), threading.Event()
    original_sources, original_observe = transport._group_source_pair, transport.bindings.observe

    def sources(*args):
        source_started.set()
        assert release.wait(3)
        return (None, None) if failure == "source" else original_sources(*args)

    def observe(*args, **kwargs):
        observation = original_observe(*args, **kwargs)
        identity_started.set()
        assert release.wait(3)
        if failure == "identity":
            raise ValueError("current App observation unavailable")
        return observation

    monkeypatch.setattr(transport, "_group_source_pair", sources)
    monkeypatch.setattr(transport.bindings, "observe", observe)
    with ThreadPoolExecutor(max_workers=1) as caller:
        pending = caller.submit(transport._source_verified, record)
        try:
            assert source_started.wait(2) and identity_started.wait(2)
            assert not pending.done() and not provider.writes
            if failure == "revoked":
                state = transport.bindings.read()
                transport.bindings.disconnect(binding_id=record["binding_id"], expected_revision=state["revision"])
        finally:
            release.set()
        assert pending.result(timeout=3) is (failure is None)
    assert not provider.writes and not provider.reaction_creates


@pytest.mark.parametrize("revoke", [False, True])
def test_completed_stop_reply_worker_waits_for_both_reads_and_does_not_send_after_revocation(
    completed_group_stop, monkeypatch, revoke,
):
    _, provider, transport, _, event, record = completed_group_stop
    checking_source, source_started, identity_started, release = (
        threading.Event() for _ in range(4)
    )
    original_verify = transport._source_verified
    original_sources, original_observe = transport._group_source_pair, transport.bindings.observe

    def verify(value, **kwargs):
        checking_source.set()
        return original_verify(value, **kwargs)

    def sources(*args):
        source_started.set()
        assert release.wait(3)
        return original_sources(*args)

    def observe(*args, **kwargs):
        observation = original_observe(*args, **kwargs)
        if checking_source.is_set():
            identity_started.set()
            assert release.wait(3)
        return observation

    monkeypatch.setattr(transport, "_source_verified", verify)
    monkeypatch.setattr(transport, "_group_source_pair", sources)
    monkeypatch.setattr(transport.bindings, "observe", observe)
    path = transport.root / f"{record['request_ref']}.json"
    with ThreadPoolExecutor(max_workers=1) as caller:
        pending = caller.submit(transport.reconcile_request, path)
        try:
            assert source_started.wait(2) and identity_started.wait(2)
            assert not pending.done() and not provider.writes
            if revoke:
                state = transport.bindings.read()
                transport.bindings.disconnect(binding_id=record["binding_id"], expected_revision=state["revision"])
        finally:
            release.set()
        assert pending.result(timeout=3) == (0 if revoke else 1)
    delivered = json.loads(path.read_text())
    if revoke:
        assert not provider.writes and not delivered["deliveries"].get("terminal", {}).get("verified")
    else:
        assert delivered["deliveries"]["terminal"]["verified"]
        assert len(provider.writes) == 1
        assert provider.messages[event["message_id"]]["root_id"] == event["root_id"]


@pytest.mark.parametrize("followup", [False, True])
@pytest.mark.parametrize("identity_fails", [False, True])
def test_group_outbound_joins_current_source_and_identity_before_root_read(ordinary, monkeypatch, followup, identity_fails):  # noqa: F811
    _, runtime, provider, transport = connect_group(ordinary)
    release_source = threading.Event()
    try:
        root = provider.topic("outbound-overlap-root", "/status")
        event = provider.topic("outbound-overlap-follow", "/status", root=root["message_id"]) if followup else root
        assert transport.admit("notes-app", event)["status"] == "command_recorded"
        record = json.loads(next(transport.root.glob("*.json")).read_text())
        provider.calls.clear()
        source_started, identity_started = threading.Event(), threading.Event()
        original_source = transport._group_source_message
        original_observe = transport.bindings.observe

        def source(profile, message_id):
            if message_id == event["message_id"]:
                source_started.set()
                assert release_source.wait(timeout=3)
            else:
                assert release_source.is_set()  # Never read event-supplied ancestry early.
            return original_source(profile, message_id)

        def observe(*args, **kwargs):
            if source_started.is_set():
                identity_started.set()
                if identity_fails:
                    raise ValueError("current App identity unavailable")
            return original_observe(*args, **kwargs)

        monkeypatch.setattr(transport, "_group_source_message", source)
        monkeypatch.setattr(transport.bindings, "observe", observe)
        with ThreadPoolExecutor(max_workers=1) as caller:
            pending = caller.submit(transport._source_verified, record)
            try:
                assert source_started.wait(timeout=2)
                assert identity_started.wait(timeout=1)
                assert not pending.done()  # Including identity failure, join the owned source read.
                assert not any("GET" in args for args in provider.calls)
            finally:
                release_source.set()
            assert pending.result(timeout=3) is not identity_fails
        assert not any("+messages-mget" in args for args in provider.calls)
        assert not provider.writes and not provider.reaction_creates
        if identity_fails:
            assert not any("GET" in args and args[args.index("GET") + 1].endswith(root["message_id"])
                           for args in provider.calls if followup)
    finally:
        release_source.set()
        runtime.close()


@pytest.mark.parametrize("captured_thread", [False, True])
def test_private_reply_keeps_the_exact_quote_when_thread_id_is_optional(ordinary, captured_thread):  # noqa: F811
    store, runtime, provider, transport = connect(ordinary)
    profile = "steward-app"
    parent = provider.event(profile, "private-quote-parent", "Original quote\n\n**Second paragraph**")
    reply = provider.event(profile, "private-quote-reply", "My question\n\n**Keep its formatting**")
    parent_id, reply_id = parent["message_id"], reply["message_id"]
    thread_id = "omt_private_quote"
    reply.update(parent_id=parent_id, root_id=parent_id, thread_id=thread_id if captured_thread else None)
    child = provider.messages[reply_id]
    child.update(parent_id=parent_id, root_id=parent_id, thread_id=thread_id, thread_message_position=0)
    provider.messages[parent_id].update(thread_id=thread_id, thread_message_position=-1, thread_replies=[child])
    try:
        assert transport.admit(profile, reply)["status"] == "durably_accepted"
        request, = transport.core.pending()
        sid, tid = request["session_id"], request["turn_id"]
        transport = LarkPrivateConversations(controller=runtime, runtime_root=transport.runtime_root,
            runner=provider, cli_bin="lark-cli")
        redelivery = {**reply, "event_id": "redelivered-private-quote", "thread_id": None}
        assert transport.admit(profile, redelivery)["status"] == "durably_accepted"
        assert transport.admit(profile, {**redelivery, "thread_id": "omt_other"})["status"] == "source_conflict"
        runtime.wait_for_turn(session_id=sid, turn_id=tid, timeout_sec=10)
        original, = [row for row in store.messages(sid) if row["role"] == "user"]
        status, snapshot = ordinary[3](f"/api/chat/sessions/{sid}?message_limit=5&turn_id={tid}")
        assert status == 200
        visible = next(row for row in snapshot["messages"] if row["message_id"] == original["message_id"])
        context = visible["reply_context"]
        assert context["source_text"] == reply["content"]
        assert context["parent"] == {"message_id": parent_id, "content": parent["content"], "complete": True}
        assert context["thread"]["thread_id"] == thread_id
        assert context["thread"]["before_message_id"] == reply_id
        assert visible["text"] == original["text"] == request["message"]
        transport.reconcile()
        assert provider.topic_writes == [(reply["chat_id"], parent_id, "Runtime response.")]
    finally:
        runtime.close()


@pytest.mark.parametrize("event_shape", ["compact", "full", "provider_without_thread"])
def test_nested_private_reply_deduplicates_when_thread_history_is_unavailable(ordinary, event_shape):  # noqa: F811
    store, runtime, provider, transport = connect(ordinary)
    profile = "steward-app"
    root = provider.event(profile, "nested-root", "Earlier topic")
    parent = provider.event(profile, "nested-parent", "Exact quoted answer\n\n**Second paragraph**")
    reply = provider.event(profile, "nested-reply", "My question\n\n**Keep its formatting**")
    root_id, parent_id, reply_id = (row["message_id"] for row in (root, parent, reply))
    thread_id = "omt_nested_child"
    reply.update(parent_id=parent_id, root_id=root_id)
    provider.messages[parent_id].update(parent_id=root_id, root_id=root_id, thread_id="omt_parent",
        sender={"id": provider.profile_apps[profile], "sender_type": "app"})
    child = provider.messages[reply_id]
    child.update(parent_id=parent_id, root_id=root_id)
    if event_shape != "provider_without_thread":
        child.update(thread_id=thread_id, thread_message_position=-1)
    if event_shape == "full":
        reply["thread_id"] = thread_id
    try:
        assert transport.admit(profile, reply)["status"] == "durably_accepted"
        request, = transport.core.pending()
        sid, tid = request["session_id"], request["turn_id"]
        transport = LarkPrivateConversations(controller=runtime, runtime_root=transport.runtime_root,
            runner=provider, cli_bin="lark-cli")
        full = {**reply, "event_id": "nested-full-redelivery", "thread_id": thread_id}
        expected = "source_conflict" if event_shape == "provider_without_thread" else "durably_accepted"
        assert transport.admit(profile, full)["status"] == expected
        compact = {**reply, "event_id": "nested-compact-redelivery"}
        compact.pop("thread_id", None)
        assert transport.admit(profile, compact)["status"] == "durably_accepted"
        assert transport.admit(profile, {**full, "thread_id": "omt_parent"})["status"] == "source_conflict"
        assert transport.admit(profile, {**full, "parent_id": root_id})["status"] == "source_conflict"
        assert runtime.wait_for_turn(session_id=sid, turn_id=tid, timeout_sec=10)["status"] == "completed"
        original, = [row for row in store.messages(sid) if row["role"] == "user"]
        status, snapshot = ordinary[3](f"/api/chat/sessions/{sid}?message_limit=5&turn_id={tid}")
        assert status == 200
        visible = next(row for row in snapshot["messages"] if row["message_id"] == original["message_id"])
        context = visible["reply_context"]
        assert context["source_text"] == reply["content"]
        assert context["parent"] == {"message_id": parent_id, "content": parent["content"], "complete": True}
        assert "thread" not in context
        assert visible["text"] == original["text"] == request["message"]
        transport.reconcile()
        replies = [args for args in provider.calls if "+messages-reply" in args and "--dry-run" not in args]
        assert sum(args[args.index("--message-id") + 1] == reply_id for args in replies) == 1
    finally:
        runtime.close()


def test_group_admission_source_reads_overlap_but_keep_rendered_model_input(ordinary, monkeypatch):  # noqa: F811
    _, runtime, provider, transport = connect_group(ordinary)
    try:
        root = provider.topic("overlap-root", "Explain the project")
        event = provider.topic("overlap-follow", "@notes-app Explain config", root=root["message_id"])
        provider.messages[event["message_id"]]["body"] = {
            "content": json.dumps({"text": "@_user_1 Explain config"})}
        started = threading.Barrier(2)
        source_reading = threading.Event()
        original_source = transport._source_message
        original_observe = transport.bindings.observe

        def source(*args, **kwargs):
            source_reading.set()
            try:
                return original_source(*args, **kwargs)
            finally:
                source_reading.clear()

        def observe(*args, **kwargs):
            assert not source_reading.is_set(), "source rendering must reuse its admission identity"
            return original_observe(*args, **kwargs)

        def overlap(args, cwd=None, timeout=None):
            if (("+messages-mget" in args and args[args.index("--message-ids") + 1] == event["message_id"]) or
                    ("GET" in args and args[args.index("GET") + 1].endswith(event["message_id"]))):
                started.wait(timeout=2)
            return provider(args, cwd, timeout)

        transport.runner = overlap
        monkeypatch.setattr(transport, "_source_message", source)
        monkeypatch.setattr(transport.bindings, "observe", observe)
        assert transport.admit("notes-app", event)["status"] == "durably_accepted"
        row = transport.core.pending()[0]
        assert row["message"].endswith(f"\n\nCurrent user message:\n{event['content']}")
        assert row["source"]["topic_ref"] == lark_private_source(
            provider_ref=identity_ref("cli_notes_app"), event=event)["topic_ref"]
        assert not provider.writes
    finally:
        runtime.close()


def test_group_admission_identity_is_not_cached_between_requests(ordinary, monkeypatch):  # noqa: F811
    _, runtime, provider, transport = connect_group(ordinary)
    try:
        observed = []
        admission_thread = threading.get_ident()
        original_observe = transport.bindings.observe

        def observe(*args, **kwargs):
            if threading.get_ident() == admission_thread:
                observed.append(args[0])
            return original_observe(*args, **kwargs)

        monkeypatch.setattr(transport.bindings, "observe", observe)
        event = provider.topic("fresh-preflight", "Explain the project")
        assert transport.admit("notes-app", event)["status"] == "durably_accepted"
        # The admission preflight does not replace the Core owner's checks.
        assert len(observed) > 1 and set(observed) == {"notes-app"}
        first_request_observations = len(observed)
        record = json.loads(next(transport.root.glob("*.json")).read_text())
        assert "observation" not in record
        assert "observation" not in transport.core.read_request(record["request_ref"])

        provider.bot_ready = False
        next_event = provider.topic("unavailable-next-preflight", "Explain config")
        assert transport.admit("notes-app", next_event)["status"] == "audience_rejected"
        assert observed == ["notes-app"] * (first_request_observations + 1)
        assert len(list(transport.root.glob("*.json"))) == 1
        assert not provider.writes and not provider.reaction_creates
    finally:
        runtime.close()


@pytest.mark.parametrize("change", ["bot_unavailable", "app_replaced", "binding_revoked"])
def test_group_admission_joins_fresh_identity_and_core_rechecks_source_changes(ordinary, change):  # noqa: F811
    store, runtime, provider, transport = connect_group(ordinary)
    try:
        event = provider.topic("admission-change", "Explain the project")
        binding = next(row for row in transport.bindings.read()["bindings"]
                       if row["transport_ref"] == "notes-app")
        original_source = transport._group_source_message

        def changed(profile, message_id):
            message = original_source(profile, message_id)
            if change == "bot_unavailable":
                provider.bot_ready = False
            elif change == "app_replaced":
                provider.profile_apps[profile] = "cli_replacement"
            else:
                transport.bindings.disconnect(binding["binding_id"],
                    expected_revision=transport.bindings.read()["revision"])
            return message

        transport._group_source_message = changed
        assert transport.admit("notes-app", event)["status"] in {
            "source_verification_failed", "command_rejected"}
        assert transport.core.pending() == []
        assert store.list_sessions() == []
        assert not provider.writes and not provider.reaction_creates
    finally:
        runtime.close()


@pytest.mark.parametrize("drift", [
    "sender", "chat", "type", "root", "parent", "thread", "mention",
    "root_sender", "root_parent", "root_chat", "app", "bot", "revoke",
])
def test_group_outbound_envelopes_fail_closed_on_source_or_grant_drift(ordinary, drift):  # noqa: F811
    _, runtime, provider, transport = connect_group(ordinary)
    try:
        root = provider.topic("fence-root", "/status")
        event = provider.topic("fence-follow", "/status", root=root["message_id"])
        assert transport.admit("notes-app", event)["status"] == "command_recorded"
        record = json.loads(next(transport.root.glob("*.json")).read_text())
        provider.calls.clear()
        message, original = provider.messages[event["message_id"]], provider.messages[root["message_id"]]
        if drift == "sender":
            message["sender"] = {"id": "ou_other", "sender_type": "user"}
        elif drift == "chat":
            message["chat_id"] = "oc_other"
        elif drift == "type":
            message["msg_type"] = "image"
        elif drift == "root":
            message["root_id"] = "om_other"
        elif drift == "parent":
            message["parent_id"] = "om_other"
        elif drift == "thread":
            message["thread_id"] = "omt_other"
        elif drift == "mention":
            original["mentions"] = []
        elif drift == "root_sender":
            original["sender"]["sender_type"] = "app"
        elif drift == "root_parent":
            original["parent_id"] = "om_other"
        elif drift == "root_chat":
            original["chat_id"] = "oc_other"
        elif drift == "app":
            provider.profile_apps["notes-app"] = "cli_other"
        elif drift == "bot":
            provider.bot_ready = False
        else:
            transport.bindings.disconnect(record["binding_id"], expected_revision=transport.bindings.read()["revision"])
        transport.reconcile()
        assert not provider.writes
        assert not transport.core.read_request(record["request_ref"]).get("delivery_verified")
        if drift in {"sender", "chat", "type", "root", "parent", "thread"}:
            assert not any("GET" in args and args[args.index("GET") + 1].endswith(root["message_id"])
                           for args in provider.calls)
    finally:
        runtime.close()


def test_legacy_group_principal_and_native_home_are_not_migrated_during_observation(ordinary):  # noqa: F811
    store, runtime, provider, transport = connect_group(ordinary)
    try:
        group_observer = transport.bindings._observe_group
        transport.bindings._observe_group = None
        binding = next(row for row in transport.bindings.read()["bindings"] if row["transport_ref"] == "notes-app")
        transport.bindings.configure(transport_ref="notes-app", project_ref=binding["project_ref"],
            executor_endpoint_id="codex", audience="group", group_refs=binding["group_refs"],
            available_group_refs=binding["group_refs"])
        transport.bindings._observe_group = group_observer
        frozen = transport.bindings.read()
        root = provider.topic("legacy-group-root", "Explain the project")
        assert transport.admit("notes-app", root)["status"] == "durably_accepted"
        first = finish_group_turn(runtime, transport, message=root["content"])
        home = store.load_session(first["session_id"])["codex_home"]
        follow = provider.topic("legacy-group-follow", "Compare config", root=root["message_id"])
        assert transport.admit("notes-app", follow)["status"] == "durably_accepted"
        second = finish_group_turn(runtime, transport, message=follow["content"])
        assert first["session_id"] == second["session_id"]
        assert store.load_session(second["session_id"])["codex_home"] == home
        assert transport.bindings.read() == frozen
        provider.owner_ready = False
        assert transport.admit("notes-app", provider.topic("legacy-logout", "Compare", root=root["message_id"]))["status"] == "audience_rejected"
        assert transport.bindings.read() == frozen
    finally:
        runtime.close()


def test_native_private_admission_queue_other_app_stop_and_verified_delivery(ordinary):  # noqa: F811
    store, runtime, provider, transport = connect(ordinary)
    try:
        first = provider.event("notes-app", "slow", "wait for interrupt")
        assert transport.admit("notes-app", first)["status"] == "durably_accepted"
        canonical = ChatExternalConversations(runtime).pending()[0]
        sid = canonical["session_id"]
        deadline = time.monotonic() + 10
        while store.load_session(sid).get("active_turn_id") != canonical["turn_id"]:
            assert time.monotonic() < deadline
            time.sleep(.01)
        queued = provider.event("notes-app", "queued", "follow-up")
        started = time.monotonic()
        assert transport.admit("notes-app", queued)["status"] == "durably_accepted"
        assert time.monotonic() - started < 3
        assert len(store.queued_turns(sid)) == 1
        # Status uses the canonical running Turn/queue, without another model call.
        status_event = provider.event("notes-app", "current-status", "/status")
        assert transport.admit("notes-app", status_event)["status"] == "command_recorded"
        status_snapshot = next(row["status_snapshot"] for row in transport.core.pending()
                               if row["command"] == "status")
        assert status_snapshot["active_turn_status"] in {"starting", "running"}
        transport.reconcile()
        status_reply = next(text for profile, text in provider.writes
                            if profile == "notes-app" and "排队消息：1 条" in text)
        phase = "正在启动" if status_snapshot["active_turn_status"] == "starting" else "正在执行"
        assert phase in status_reply and "个人助手 · notes" in status_reply
        assert str(ordinary[-1]) not in status_reply and "/help" in status_reply
        assert len(store.queued_turns(sid)) == 1
        assert transport.admit("notes-app", {**queued, "event_id": "redelivery"})["status"] == "durably_accepted"
        assert len(store.queued_turns(sid)) == 1
        other = provider.event("steward-app", "other", "different audience")
        assert transport.admit("steward-app", other)["status"] == "durably_accepted"
        other_row = next(row for row in transport.core.pending() if row["message"] == "different audience")
        assert runtime.wait_for_turn(session_id=other_row["session_id"], turn_id=other_row["turn_id"], timeout_sec=10)["status"] == "completed"
        transport.reconcile()
        assert any(profile == "steward-app" and text == "Runtime response." for profile, text in provider.writes)
        assert any(profile == "notes-app" and "这条已排队" in text for profile, text in provider.writes)
        stop = provider.event("notes-app", "stop", "/stop")
        assert transport.admit("notes-app", stop)["status"] == "command_recorded"
        queued_row = next(row for row in transport.core.pending() if row["message"] == "follow-up")
        assert runtime.wait_for_turn(session_id=sid, turn_id=queued_row["turn_id"], timeout_sec=10)["status"] == "completed"
        transport.reconcile()
        before = len(provider.writes)
        transport.reconcile()
        assert len(provider.writes) == before
        assert "different audience" not in str(store.messages(sid))
        assert not any("chats" in call for call in provider.calls)
        assert not any("schema_version" in text for _, text in provider.writes)
        assert all(row["goal_id"] is None for row in store.list_sessions())
    finally:
        runtime.close()


@pytest.mark.parametrize("profile", ["notes-app", "steward-app"])
@pytest.mark.parametrize("info", ["serverOverloaded", "other"])
def test_model_capacity_failure_keeps_session_and_original_delivery(ordinary, profile, info):  # noqa: F811
    store, runtime, provider, transport = connect(ordinary)
    _, _, _, _, capture, fake, _ = ordinary
    fake.write_text(fake.read_text().replace('"codexErrorInfo": "cyberPolicy"',
        f'"codexErrorInfo": {json.dumps(info)}').replace('private-fixture-upstream-detail',
        'private-fixture Selected model is at capacity serverOverloaded'))
    try:
        event = provider.event(profile, "busy-model", "typed terminal failure")
        assert transport.admit(profile, event)["status"] == "durably_accepted"
        row = transport.core.pending()[0]
        sid, tid = row["session_id"], row["turn_id"]
        turn = runtime.wait_for_turn(session_id=sid, turn_id=tid, timeout_sec=10)
        assert turn["status"] == "failed" and turn["response"] is None
        assert turn["error_code"] == ("server_overloaded" if info == "serverOverloaded" else "host_gate")
        thread = store.load_session(sid)["upstream_thread_id"]
        transport.reconcile()
        terminal = provider.writes[-1][1]
        assert ("当前模型繁忙" in terminal) == (info == "serverOverloaded")
        assert "原会话已保留" in terminal
        assert "private-fixture" not in terminal and "Partial answer" not in terminal
        count = len(provider.writes)
        LarkPrivateConversations(controller=runtime, runtime_root=store.root.parent,
            runner=provider, cli_bin="lark-cli").reconcile()
        assert len(provider.writes) == count
        # Only the human's next input starts work; no retry or model/thread fallback.
        before = [json.loads(line) for line in capture.read_text().splitlines()]
        assert sum(r["method"] == "turn/start" for r in before) == 1
        followup = provider.event(profile, "after-busy", "continue here")
        transport.admit(profile, followup)
        following = transport.core.pending()[0]
        assert following["session_id"] == sid
        assert runtime.wait_for_turn(session_id=sid, turn_id=following["turn_id"], timeout_sec=10)["status"] == "completed"
        assert store.load_session(sid)["upstream_thread_id"] == thread
        assert len(store.list_sessions()) == 1 and store.load_session(sid)["goal_id"] is None
    finally:
        runtime.close()


@pytest.mark.parametrize("profile", ["notes-app", "steward-app"])
@pytest.mark.parametrize("info,code", [
    ("other", "host_gate"),
    ({"responseStreamDisconnected": {"httpStatusCode": None}}, "upstream_transport_failure"),
    ("cyberPolicy", "cyber_policy"),
])
def test_disconnect_failure_returns_original_retry_evidence_without_manual_status_or_replay(
    ordinary, profile, info, code,  # noqa: F811
):
    store, runtime, provider, transport = connect(ordinary)
    capture, fake = ordinary[-3:-1]
    source = fake.read_text().replace('"codexErrorInfo": "cyberPolicy"',
                                     f'"codexErrorInfo": {info!r}')
    marker = '            turn_status[active_turn] = "failed"'
    source = source.replace(marker, marker + '\n'
        '            for _ in range(5):\n'
        '                print(json.dumps({"method": "error", "params": {"threadId": "durable-thread", '
        '"turnId": active_turn, "willRetry": True, "error": {"codexErrorInfo": '
        '{"responseStreamDisconnected": {"httpStatusCode": None}}, "message": "private retry detail"}}}), flush=True)')
    fake.write_text(source)
    try:
        event = provider.event(profile, "disconnect", "typed terminal failure")
        assert transport.admit(profile, event)["status"] == "durably_accepted"
        row = transport.core.pending()[0]
        sid, tid = row["session_id"], row["turn_id"]
        turn = runtime.wait_for_turn(session_id=sid, turn_id=tid, timeout_sec=10)
        assert turn["status"] == "failed" and turn["response"] is None
        assert turn["error_code"] == code
        events = store.events_after(sid, tid, None)
        assert sum(bool(r.get("payload", {}).get("retry")) for r in events) == 5
        thread = store.load_session(sid)["upstream_thread_id"]
        provider.verify_replies = False
        assert transport.reconcile() == 0
        terminal = provider.writes[-1][1]
        label = {"host_gate": "响应流曾中断", "upstream_transport_failure": "响应连接失败",
                 "cyber_policy": "安全策略拦截"}[code]
        assert label in terminal and "原会话已保留" in terminal
        assert "/status" not in terminal and "已过期" not in terminal
        assert "private" not in terminal and "Partial answer" not in terminal
        if code != "host_gate":
            assert "响应流曾中断" not in terminal
        # The failed delivery resumes its frozen intent after a transport
        # restart. It cannot turn a retry observation into a new model call.
        before = list(provider.writes)
        provider.verify_replies = True
        restarted = LarkPrivateConversations(controller=runtime, runtime_root=store.root.parent,
                                             runner=provider, cli_bin="lark-cli")
        assert restarted.reconcile() == 1 and restarted.reconcile() == 0
        assert provider.writes == before
        assert store.load_turn(sid, tid)["error_code"] == code
        requests = [json.loads(line) for line in capture.read_text().splitlines()]
        assert sum(r["method"] == "turn/start" for r in requests) == 1
        assert store.load_session(sid)["upstream_thread_id"] == thread
        # Retry history belongs to a Turn, not the entire Session. A later
        # unclassified failure cannot inherit this disconnect explanation.
        fake.write_text(source.replace('            for _ in range(5):', '            for _ in range(0):')
                        .replace(f'"codexErrorInfo": {info!r}', '"codexErrorInfo": "other"'))
        runtime.close()
        from loopx.chat_runtime import ChatRuntimeController
        resumed = ChatRuntimeController(store=store, codex_bin=str(fake), project_contexts=ordinary[2])
        runtime = resumed
        following = LarkPrivateConversations(controller=resumed, runtime_root=store.root.parent,
                                             runner=provider, cli_bin="lark-cli")
        following.admit(profile, provider.event(profile, "new-failure", "typed terminal failure"))
        next_row = next(r for r in following.core.pending() if r.get("turn_id") != tid)
        assert next_row["session_id"] == sid
        assert resumed.wait_for_turn(session_id=sid, turn_id=next_row["turn_id"], timeout_sec=10)["error_code"] == "host_gate"
        following.reconcile()
        assert "响应流曾中断" not in provider.writes[-1][1]
        assert store.load_session(sid)["upstream_thread_id"] == thread
    finally:
        runtime.close()


def test_bound_app_history_keeps_original_unsupported_media_without_a_turn(ordinary):  # noqa: F811
    store, runtime, provider, transport = connect(ordinary)
    profile = "steward-app"
    try:
        assert transport.admit(profile, provider.event(profile, "original-text", "Original text."))["status"] == "durably_accepted"
        request, = transport.core.pending()
        sid, tid = request["session_id"], request["turn_id"]
        runtime.wait_for_turn(session_id=sid, turn_id=tid, timeout_sec=10)
        transport.reconcile()
        def snapshot(query=""):
            return ordinary[3](f"/api/chat/sessions/{sid}{query}")

        status, first = snapshot("?message_limit=1")
        assert status == 200
        old_cursor = first["message_page"]["next_cursor"]
        assert old_cursor
        original_messages = store.messages(sid)
        model_requests = ordinary[-3].read_bytes()
        complete = snapshot("?message_limit=200")[1]
        original_basis = f"{complete['message_page']['total']}:{complete['message_page']['visible_page_sha256']}"

        event = provider.event(profile, "late-unsupported-audio", "private-resource-token", kind="audio")
        event["create_time"] = "1700000000123"
        provider.messages[event["message_id"]]["create_time"] = event["create_time"]
        assert transport.admit(profile, event)["status"] == "command_recorded"
        assert transport.reconcile() == 1
        sent = list(provider.writes)
        assert "未提交执行" in sent[-1][1]
        assert transport.admit(profile, {**event, "event_id": "duplicate-audio"})["status"] == "command_recorded"
        transport.reconcile()
        assert provider.writes == sent
        original_notice = next(row for row in transport.core.pending() if row.get("command") == "unsupported")
        assert original_notice["session_id"] == sid and original_notice["turn_id"] is None

        binding_id = original_notice["binding_id"]
        listed_status, listed = ordinary[3](f"/api/chat/sessions?conversation_binding_id={binding_id}")
        assert listed_status == 200
        listed_revision = listed["sessions"][0]["transcript_revision"]
        assert listed_revision != first["session"]["transcript_revision"]
        provider_calls = list(provider.calls)
        status, page = snapshot("?message_limit=1")
        assert status == 200 and page["session"]["transcript_revision"] == listed_revision
        notice, = page["messages"]
        assert notice["role"] == "error" and notice["origin"] == "lark" and notice["turn_id"] is None
        assert notice["created_at"] == "2023-11-14T22:13:20.123000+00:00"
        assert "飞书音频" in notice["text"] and "未提交执行" in notice["text"]
        assert "private-resource-token" not in json.dumps(page)
        assert snapshot("?message_limit=1&message_cursor=" + old_cursor)[0] == 400
        status, tail = snapshot("?message_limit=10&message_cursor=" + page["message_page"]["next_cursor"])
        assert status == 200 and [r["message_id"] for r in tail["messages"]] == [r["message_id"] for r in original_messages]
        status, turn = snapshot(f"?message_limit=10&turn_id={tid}")
        assert status == 200 and all(row["message_id"] != notice["message_id"] for row in turn["messages"])
        assert provider.calls == provider_calls and store.messages(sid) == original_messages
        assert ordinary[-3].read_bytes() == model_requests

        # Provider-only notices belong to the same visible prefix proof as
        # canonical rows. Inserting one invalidates an older complete prefix;
        # once read, it can be retained during quiet and append-only refreshes.
        status, stale = snapshot("?message_limit=200&known_history=" + original_basis)
        assert status == 200 and stale["status"] == "restart"
        status, complete = snapshot("?message_limit=200")
        assert status == 200
        basis = f"{complete['message_page']['total']}:{complete['message_page']['visible_page_sha256']}"
        status, quiet = snapshot("?message_limit=200&known_history=" + basis)
        assert status == 200 and quiet["messages"] == [] and quiet["history_resume"]["next"] == basis
        added = store.append_message(sid, role="agent", text="A later original result after the media notice.")
        status, suffix = snapshot("?message_limit=200&known_history=" + basis)
        assert status == 200 and suffix["messages"] == [added]
        assert provider.calls == provider_calls and ordinary[-3].read_bytes() == model_requests

        # The same transport source cannot be surfaced under another native
        # correlation, nor after the original grant is disconnected.
        native_path = transport.core.root / f"{original_notice['request_ref']}.json"
        native_path.write_text(json.dumps({**original_notice, "session_id": "other-session"}))
        assert notice["message_id"] not in {row["message_id"] for row in snapshot()[1]["messages"]}
        native_path.write_text(json.dumps(original_notice))
        assert notice["message_id"] in {row["message_id"] for row in snapshot()[1]["messages"]}
        current = transport.bindings.read()
        binding = next(row for row in current["bindings"] if row["transport_ref"] == profile)
        transport.bindings.disconnect(binding["binding_id"], expected_revision=current["revision"])
        assert notice["message_id"] not in {row["message_id"] for row in snapshot()[1]["messages"]}
    finally:
        runtime.close()


def test_bound_app_history_isolates_unreadable_media_receipts_and_recovers(ordinary):  # noqa: F811
    store, runtime, provider, transport = connect(ordinary)
    request = ordinary[3]
    histories = {}
    try:
        for profile, kind in (("steward-app", "audio"), ("notes-app", "file")):
            event = provider.event(profile, f"{profile}-message", f"Original {profile} text")
            assert transport.admit(profile, event)["status"] == "durably_accepted"
            original = next(row for row in transport.core.pending() if row.get("message") == event["content"])
            sid = original["session_id"]
            runtime.wait_for_turn(session_id=sid, turn_id=original["turn_id"], timeout_sec=10)
            media = provider.event(profile, f"{profile}-unsupported", "", kind=kind)
            assert transport.admit(profile, media)["status"] == "command_recorded"
            record = next(row for row in transport.core.pending()
                          if row.get("session_id") == sid and row.get("command") == "unsupported")
            histories[profile] = (sid, record["binding_id"], record["request_ref"])

        def view(profile):
            sid, binding, _ = histories[profile]
            snapshot_status, snapshot = request(f"/api/chat/sessions/{sid}?message_limit=10")
            index_status, index = request(f"/api/chat/sessions?conversation_binding_id={binding}")
            assert (snapshot_status, index_status) == (200, 200)
            assert snapshot["session"]["transcript_revision"] == index["sessions"][0]["transcript_revision"]
            return snapshot["messages"], snapshot["session"]["transcript_revision"]

        good = view("steward-app")
        foreign = view("notes-app")
        assert sum("飞书音频" in row["text"] for row in good[0]) == 1
        assert sum("飞书文件" in row["text"] for row in foreign[0]) == 1
        assert not any("飞书文件" in row["text"] for row in good[0])
        bad_path = transport.root / f"{histories['notes-app'][2]}.json"
        original_bytes = bad_path.read_bytes()
        temporary = bad_path.with_suffix(".corrupt")
        temporary.write_bytes(bytes([0xff, 0xfe]))
        temporary.replace(bad_path)

        # A broken optional row must neither poison another binding nor erase
        # the affected binding's canonical text. Its own notice stays absent.
        assert view("steward-app") == good
        damaged, damaged_revision = view("notes-app")
        assert damaged == store.messages(histories["notes-app"][0])
        assert damaged_revision != foreign[1]
        assert bad_path.read_bytes() == bytes([0xff, 0xfe])

        # Recover even without a directory generation change after the bad
        # read. An incomplete scan cannot be retained as complete.
        bad_path.write_bytes(original_bytes)
        assert view("notes-app") == foreign
        assert view("steward-app") == good
        assert not provider.writes
        assert sum(json.loads(line)["method"] == "turn/start" for line in ordinary[-3].read_text().splitlines()) == 2
    finally:
        runtime.close()


def test_source_rejection_unsupported_file_notice_and_ambiguous_reply_readback(ordinary):  # noqa: F811
    _, runtime, provider, transport = connect(ordinary)
    try:
        image = provider.event("notes-app", "file", "", kind="file")
        assert transport.admit("notes-app", {**image, "sender_type": "app"})["status"] == "audience_rejected"
        assert transport.admit("steward-app", image)["status"] == "audience_rejected"
        assert transport.core.pending() == []
        assert transport.admit("notes-app", image)["status"] == "command_recorded"
        provider.verify_replies = False
        assert transport.reconcile() == 0
        assert len(provider.writes) == 1 and "未提交执行" in provider.writes[0][1]
        assert transport.reconcile() == 0 and len(provider.writes) == 1
        provider.verify_replies = True
        assert transport.reconcile() == 1 and len(provider.writes) == 1
        assert runtime.store.list_sessions() == []
        with pytest.raises(ValueError, match="non-default"):
            observe_lark_conversation_identity(profile="default", runner=provider, cli_bin="lark-cli")
    finally:
        runtime.close()


def test_existing_consumer_dispatches_private_admission_without_waiting_for_model():
    event = {"chat_type": "p2p", "message_type": "text", "message_id": "om_synthetic"}
    admitted = []
    result = poll_lark_goal_topic_profile_once(profile="notes-app", snapshot={"private_profiles": {
        "notes-app": {"cli_bin": "lark-cli", "provider_ref": "a" * 24}}}, runtime_root=".",
        answer=lambda *_: pytest.fail("ordinary private Chat must not call a Goal answer"),
        consume_runner=lambda args: {"returncode": 0, "stdout": json.dumps(event)},
        private_admitter=lambda profile, event: admitted.append((profile, event)) or {"status": "durably_accepted"})
    assert result["event_statuses"] == ["durably_accepted"]
    assert admitted == [("notes-app", event)]


@pytest.mark.parametrize("answer,display", [
    ("Author @source_account; primary post unverified.", "Author ＠source_account; primary post unverified."),
    (r"Result\nNext step", "Result\nNext step"),
    ('Untrusted <at id="unknown">label</at>', 'Untrusted ‹at id="unknown">label‹/at>'),
])
def test_private_answer_repairs_presentation_and_recovers_exact_receipt(ordinary, answer, display):  # noqa: F811
    store, runtime, provider, transport = connect(ordinary)
    fake, capture = ordinary[-2], ordinary[-3]
    fake.write_text(fake.read_text().replace('"Runtime response."', json.dumps(answer)))
    try:
        event = provider.event("notes-app", "quoted-source", "Read the source.")
        assert transport.admit("notes-app", event)["status"] == "durably_accepted"
        native = transport.core.pending()[0]
        turn = runtime.wait_for_turn(session_id=native["session_id"], turn_id=native["turn_id"], timeout_sec=10)
        assert turn["response"]["message"] == answer
        provider.verify_replies = False
        assert transport.reconcile() == 0
        assert provider.writes[-1] == ("notes-app", display)
        before = list(provider.writes)
        saved = json.loads(next(transport.root.glob("*.json")).read_text())
        assert saved["deliveries"]["terminal"]["text"] == display
        assert saved["deliveries"]["terminal"]["attempt"]
        # Restart only the transport facade: read back the original attempt,
        # without another provider send, model Turn or rewritten Core response.
        recovered = LarkPrivateConversations(controller=runtime, runtime_root=transport.runtime_root,
                                             runner=provider, cli_bin="lark-cli")
        provider.verify_replies = True
        assert recovered.reconcile() == 1
        assert recovered.reconcile() == 0
        assert provider.writes == before
        assert store.load_turn(native["session_id"], native["turn_id"])["response"]["message"] == answer
        calls = [json.loads(line) for line in capture.read_text().splitlines()]
        assert sum(row.get("method") == "turn/start" for row in calls) == 1
    finally:
        runtime.close()


def test_private_answer_keeps_preexisting_attempt_text_on_recovery(ordinary, monkeypatch):  # noqa: F811
    from loopx.extensions.lark import private_conversations
    store, runtime, provider, transport = connect(ordinary)
    fake, capture = ordinary[-2], ordinary[-3]
    answer = "Existing\r\nanswer."
    fake.write_text(fake.read_text().replace('"Runtime response."', json.dumps(answer)))
    try:
        event = provider.event("notes-app", "old-reply-intent", "Continue the answer.")
        assert transport.admit("notes-app", event)["status"] == "durably_accepted"
        native = transport.core.pending()[0]
        runtime.wait_for_turn(session_id=native["session_id"], turn_id=native["turn_id"], timeout_sec=10)
        provider.verify_replies = False
        # Reproduce the pre-change facade: it journals Core text unchanged;
        # the existing Inbox owner independently normalizes the provider wire.
        with monkeypatch.context() as legacy:
            legacy.setattr(private_conversations, "_presentation_text", lambda text: text)
            assert transport.reconcile() == 0
        saved = json.loads(next(transport.root.glob("*.json")).read_text())
        assert saved["deliveries"]["terminal"]["text"] == answer
        assert provider.writes[-1] == ("notes-app", "Existing\nanswer.")
        before = list(provider.writes)
        provider.verify_replies = True
        recovered = LarkPrivateConversations(controller=runtime, runtime_root=transport.runtime_root,
                                             runner=provider, cli_bin="lark-cli")
        assert recovered.reconcile() == 1
        assert recovered.reconcile() == 0 and provider.writes == before
        assert store.load_turn(native["session_id"], native["turn_id"])["response"]["message"] == answer
        assert sum(json.loads(line).get("method") == "turn/start" for line in capture.read_text().splitlines()) == 1
    finally:
        runtime.close()


def test_new_session_replay_cannot_close_a_later_session(ordinary):  # noqa: F811
    store, runtime, provider, transport = connect(ordinary)
    try:
        transport.admit("notes-app", provider.event("notes-app", "first", "first"))
        first = next(row for row in transport.core.pending() if row["message"] == "first")
        runtime.wait_for_turn(session_id=first["session_id"], turn_id=first["turn_id"], timeout_sec=10)
        new = provider.event("notes-app", "new", "/new")
        transport.admit("notes-app", new)
        transport.admit("notes-app", provider.event("notes-app", "next", "next"))
        second = next(row for row in transport.core.pending() if row["message"] == "next")
        assert first["session_id"] != second["session_id"]
        transport.admit("notes-app", new)
        assert store.load_session(second["session_id"])["status"] != "closed"
        # Simulate a lost correlation response after original Core admission.
        # Its stable Turn must remain in the closed original Session.
        original_path = transport.core.root / f"{first['request_ref']}.json"
        original = json.loads(original_path.read_text())
        original["status"] = "prepared"
        original_path.write_text(json.dumps(original))
        transport.core.recover()
        recovered = json.loads(original_path.read_text())
        assert recovered["session_id"] == first["session_id"] and recovered["turn_id"] == first["turn_id"]
        assert store.turn_for_client(second["session_id"], f"external-{first['request_ref']}") is None
        runtime.wait_for_turn(session_id=second["session_id"], turn_id=second["turn_id"], timeout_sec=10)
    finally:
        runtime.close()


def test_private_setup_companion_verifies_each_app_and_redacts_subjects(ordinary, tmp_path):  # noqa: F811
    from types import SimpleNamespace
    from loopx.extensions.lark.private_conversation_api import PrivateConversationRequestMixin
    _, runtime, _, transport = connect(ordinary)
    registry = tmp_path / "fresh-registry.json"
    registry.write_text(json.dumps({"schema_version": "0.1", "goals": []}))
    updates = []
    server = SimpleNamespace(runtime_controller=runtime, registry_path=registry, runtime_root_override=tmp_path,
        lark_private_conversations=transport, lark_goal_topic_runtime=SimpleNamespace(
            refresh=lambda: updates.append(True), health_snapshot=lambda: {}))

    class Handler(PrivateConversationRequestMixin):
        def __init__(self):
            self.server = server
            self.body = {}
            self.result = None
        def _read_json(self):
            return self.body
        def _send_json(self, value):
            self.result = value
        def _send_error(self, value, **kwargs):
            self.result = {"error": value, **kwargs}

    handler = Handler()
    try:
        handler._private_conversations()
        assert len(handler.result["connections"]) == 2
        assert all(row["context_available"] for row in handler.result["connections"])
        encoded = json.dumps(handler.result)
        assert all(marker not in encoded for marker in ["operator_ref", "provider_ref", "workspace_path", "ou_notes", "cli_notes"])
        handler.body = {"app_ref": "notes-app", "project_ref": "f" * 24, "executor_endpoint_id": "codex"}
        handler._private_conversation_connect()
        assert handler.result["status"] == 400 and not updates
        handler.body = {"binding_id": transport.bindings.read()["bindings"][0]["binding_id"], "revision": 0}
        handler._private_conversation_disconnect()
        assert handler.result["status"] == 400 and not updates
        handler.body["revision"] = transport.bindings.read()["revision"]
        handler._private_conversation_disconnect()
        assert handler.result["ok"] and len(handler.result["connections"]) == 1 and updates == [True]
    finally:
        runtime.close()


def test_http_authorization_read_does_not_wait_for_bot_health_or_delivery_history(ordinary, monkeypatch):  # noqa: F811
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler

    store, runtime, _, transport = connect(ordinary)
    bindings = transport.bindings
    original = bindings.read()
    sessions = store.list_sessions()

    def blocked(*_args, **_kwargs):
        raise AssertionError("Authorization reads must not observe unrelated bot work")

    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.chat_store, server.runtime_controller = store, runtime
    server.verbose = False
    server.lark_private_conversations = transport
    server.lark_goal_topic_runtime = SimpleNamespace(health_snapshot=blocked, close=lambda: None)
    monkeypatch.setattr(transport, "health", blocked)
    monkeypatch.setattr(bindings, "agent_candidates", blocked)
    monkeypatch.setattr(bindings, "goal_scope_ids", blocked)
    monkeypatch.setattr(bindings, "stop_recoveries", blocked)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def read(query="?view=authorization"):
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        try:
            connection.request("GET", "/api/chat/lark/private-conversations" + query)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    try:
        status, snapshot = read()
        assert status == 200 and snapshot["ok"]
        assert snapshot["revision"] == original["revision"]
        assert {row["binding_id"] for row in snapshot["connections"]} == {row["binding_id"] for row in original["bindings"]}
        for actual, saved in zip(snapshot["connections"], original["bindings"], strict=True):
            assert actual["app_ref"] == saved["transport_ref"]
            assert all(actual[key] == saved[key] for key in ("binding_id", "context_kind", "project_ref", "grant", "executor_endpoint_id"))
            assert actual["context_available"] is True
            assert not {"pending_count", "listener_status", "operator_ref", "provider_ref", "workspace_path"} & actual.keys()
        for invalid in ("?view=", "?view=unknown", "?view=authorization&view=authorization"):
            assert read(invalid)[0] == 400
        with monkeypatch.context() as unavailable:
            unavailable.setattr(runtime.project_contexts, "available", lambda: [])
            assert all(not row["context_available"] for row in read()[1]["connections"])

        # A real revocation disappears immediately, without caching old grants.
        removed = original["bindings"][0]["binding_id"]
        bindings.disconnect(removed, expected_revision=original["revision"])
        assert removed not in {row["binding_id"] for row in read()[1]["connections"]}
        current_read = bindings.read
        count = 0

        def changed_during_read():
            nonlocal count
            count += 1
            result = current_read()
            if count > 1:
                result = {**result, "revision": result["revision"] + 1}
            return result

        monkeypatch.setattr(bindings, "read", changed_during_read)
        assert read()[0] == 409
        assert store.list_sessions() == sessions
    finally:
        server.shutdown()
        server.server_close()
        runtime.close()


def test_rendered_cli_text_is_preserved_and_source_type_must_agree(ordinary):  # noqa: F811
    store, runtime, provider, transport = connect(ordinary)
    try:
        literal = '{"text":"this is literal user text"}'
        event = provider.event("notes-app", "rendered", literal)
        assert transport.admit("notes-app", event)["status"] == "durably_accepted"
        assert transport.core.pending()[0]["message"] == literal
        mismatched = provider.event("notes-app", "wrong_type", "not a text message")
        provider.messages[mismatched["message_id"]]["msg_type"] = "image"
        assert transport.admit("notes-app", mismatched)["status"] == "source_conflict"
        assert len(transport.core.pending()) == 1
    finally:
        runtime.close()


def test_private_app_alias_guard_rejects_unknown_or_unverified_identity():
    from loopx.chat_lark_api import _app_identity_for_private_guard
    provider = Provider()
    assert _app_identity_for_private_guard("notes-app", provider, "lark-cli") == "cli_notes_app"
    for data in [{}, {"appId": "cli_notes_app"}, {"appId": "malformed", "identities": {"bot": {
        "available": True, "verified": True}}}, {"appId": "cli_notes_app", "identities": {"bot": {
        "available": True, "verified": False}}}]:
        def unverified(*_args, **_kwargs):
            return {"returncode": 0, "stdout": json.dumps(data), "stderr": ""}
        with pytest.raises(ValueError, match="could not be verified"):
            _app_identity_for_private_guard("notes-app", unverified, "lark-cli")



def test_revocation_during_source_read_prevents_native_admission(ordinary):  # noqa: F811
    store, runtime, provider, transport = connect(ordinary)
    try:
        event = provider.event("notes-app", "revoked", "must not execute")
        binding = transport.bindings.read()["bindings"][0]

        def revoke_after_read(args, cwd=None, timeout=None):
            result = provider(args, cwd, timeout)
            if "+messages-mget" in args:
                transport.bindings.disconnect(binding["binding_id"],
                    expected_revision=transport.bindings.read()["revision"])
            return result

        transport.runner = revoke_after_read
        assert transport.admit("notes-app", event)["status"] == "command_rejected"
        assert transport.core.pending() == []
        assert store.list_sessions() == []
        assert provider.writes == []
    finally:
        runtime.close()


def test_private_admission_reuses_the_effect_runtime_revision_for_one_event(ordinary, monkeypatch):  # noqa: F811
    from loopx.control_plane import effect_runtime

    store, runtime, provider, transport = connect(ordinary)
    binding = transport.bindings.read()["bindings"][0]
    event = provider.event("notes-app", "runtime-revision", "Explain this workspace")
    ref = identity_ref(binding["provider_ref"], event["message_id"])
    admission_thread = threading.get_ident()
    fingerprints = []
    original = effect_runtime._runtime_fingerprint

    def fingerprint():
        value = original()
        if threading.get_ident() == admission_thread:
            fingerprints.append(value)
        return value

    monkeypatch.setattr(effect_runtime, "_runtime_fingerprint", fingerprint)
    try:
        assert transport.admit("notes-app", event)["status"] == "durably_accepted"
        assert len(fingerprints) == 1
        admitted = transport.core.read_request(ref)
        session_id, turn_id = admitted["session_id"], admitted["turn_id"]
        assert runtime.wait_for_turn(session_id=session_id, turn_id=turn_id, timeout_sec=15)["status"] == "completed"

        transport.admit("notes-app", event)
        assert len(fingerprints) == 2
        assert transport.core.read_request(ref)["turn_id"] == turn_id
        assert len([row for row in store.messages(session_id) if row["role"] == "user"]) == 1
    finally:
        runtime.close()


def test_core_admission_verifies_once_inside_both_fences_including_replay(ordinary, monkeypatch):  # noqa: F811
    from contextlib import contextmanager
    from loopx.capabilities.native_chat import external_conversations

    store, runtime, _, transport = connect(ordinary)
    binding = transport.bindings.read()["bindings"][0]
    source = {"source_ref": "a" * 24, "sender_ref": binding["operator_ref"], "private_human_message": True}
    original_lock = external_conversations.exclusive_file_lock
    active = set()

    @contextmanager
    def track_lock(path, *, operation):
        with original_lock(path, operation=operation):
            active.add(operation)
            try:
                yield
            finally:
                active.remove(operation)

    original_observe = transport.bindings.observe
    observed = []

    def observe(profile):
        assert active == {"route_external_chat_request", "admit_external_chat_request"}
        observed.append(profile)
        return original_observe(profile)

    monkeypatch.setattr(external_conversations, "exclusive_file_lock", track_lock)
    transport.bindings.observe = observe
    try:
        args = {"binding_id": binding["binding_id"], "source": source,
                "request_ref": "b" * 24, "message": "/help", "command": "help"}
        first = transport.core.admit(**args)
        assert first["status"] == "command_completed"
        assert observed == ["notes-app"]
        assert transport.core.admit(**args) == first
        assert observed == ["notes-app", "notes-app"]
        transport.bindings.disconnect(binding["binding_id"], expected_revision=transport.bindings.read()["revision"])
        with pytest.raises(ValueError):
            transport.core.admit(**args)
        assert store.list_sessions() == []
    finally:
        runtime.close()


@pytest.mark.parametrize("field", ["binding_id", "source_ref"])
@pytest.mark.parametrize("value", ["../../outside", "", "not-an-opaque-reference", None])
def test_invalid_source_lock_path_is_rejected_before_file_access(ordinary, monkeypatch, field, value):  # noqa: F811
    from loopx.capabilities.native_chat import external_conversations

    _, runtime, _, transport = connect(ordinary)
    binding = transport.bindings.read()["bindings"][0]
    source = {"source_ref": "a" * 24, "sender_ref": binding["operator_ref"], "private_human_message": True}
    binding_id = binding["binding_id"]
    if field == "binding_id":
        binding_id = value
    else:
        source[field] = value

    def unexpected_lock(*args, **kwargs):
        pytest.fail("invalid references must not reach filesystem locks")

    monkeypatch.setattr(external_conversations, "exclusive_file_lock", unexpected_lock)
    try:
        with pytest.raises(ValueError, match="invalid external conversation source reference"):
            transport.core.admit(binding_id=binding_id, source=source,
                                 request_ref="b" * 24, message="must not execute")
        assert not transport.core.root.exists()
    finally:
        runtime.close()


@pytest.mark.parametrize("change", ["disconnect", "owner_change"])
def test_authority_changed_while_waiting_for_source_fence_blocks_admission(ordinary, monkeypatch, change):  # noqa: F811
    from contextlib import contextmanager
    from loopx.capabilities.native_chat import external_conversations

    store, runtime, _, transport = connect(ordinary)
    binding = transport.bindings.read()["bindings"][0]
    source = {"source_ref": "a" * 24, "sender_ref": binding["operator_ref"], "private_human_message": True}
    original_lock = external_conversations.exclusive_file_lock
    original_observe = transport.bindings.observe

    @contextmanager
    def change_before_acquiring_source_fence(path, *, operation):
        if operation == "route_external_chat_request":
            if change == "disconnect":
                transport.bindings.disconnect(binding["binding_id"], expected_revision=transport.bindings.read()["revision"])
            else:
                transport.bindings.observe = lambda profile: {**original_observe(profile), "operator_ref": "f" * 24}
        with original_lock(path, operation=operation):
            yield

    monkeypatch.setattr(external_conversations, "exclusive_file_lock", change_before_acquiring_source_fence)
    try:
        with pytest.raises(ValueError):
            transport.core.admit(binding_id=binding["binding_id"], source=source,
                                 request_ref="b" * 24, message="must not execute")
        assert transport.core.pending() == []
        assert store.list_sessions() == []
    finally:
        runtime.close()



def test_listener_discovery_is_separate_from_current_owner_authorization(ordinary):  # noqa: F811
    _, runtime, _, transport = connect(ordinary)
    original = transport.bindings.observe
    def observe(profile):
        if profile == "steward-app":
            raise ValueError("this App owner is no longer verified")
        return original(profile)
    transport.bindings.observe = observe
    try:
        provider = transport.runner
        provider.calls.clear()
        assert set(transport.profiles()) == {"notes-app", "steward-app"}
        assert provider.calls == [["lark-cli", "profile", "list"]]
        transport.bindings.observe = lambda profile: {**original(profile), "operator_ref": "f" * 24}
        assert set(transport.profiles()) == {"notes-app", "steward-app"}
        transport.bindings.observe = observe
        rejected = {"message_id": "om_unknown", "chat_id": "oc_steward_app", "sender_id": "ou_steward_app",
                    "chat_type": "p2p", "sender_type": "user", "message_type": "text", "content": "unverified"}
        assert transport.admit("steward-app", rejected)["status"] == "audience_rejected"
        assert transport.admit("notes-app", provider.event("notes-app", "healthy", "/status"))["status"] == "command_recorded"
    finally:
        runtime.close()



def test_listener_lease_uses_current_app_and_detects_removal_or_retarget(ordinary):  # noqa: F811
    import hashlib

    _, runtime, provider, transport = connect(ordinary)
    try:
        profiles = transport.profiles()
        assert profiles["notes-app"]["consumer_ref"] == hashlib.sha256(b"cli_notes_app").hexdigest()[:32]
        # A new App under the old profile must not run under the old App's lease.
        provider.profile_apps["notes-app"] = "cli_replacement"
        assert set(transport.profiles()) == {"steward-app"}
        del provider.profile_apps["notes-app"]
        assert set(transport.profiles()) == {"steward-app"}
        provider.profile_apps["notes-app"] = "cli_notes_app"
        binding = transport._binding("notes-app")
        transport.bindings.disconnect(binding["binding_id"], expected_revision=transport.bindings.read()["revision"])
        assert set(transport.profiles()) == {"steward-app"}
    finally:
        runtime.close()



@pytest.mark.parametrize("result", [
    {"returncode": 1, "stdout": "", "timed_out": True},
    {"returncode": 0, "stdout": "not JSON"},
    {"returncode": 0, "stdout": "{}"},
])
def test_unreadable_local_profile_inventory_is_unknown_not_removed(ordinary, result):  # noqa: F811
    _, runtime, _, transport = connect(ordinary)
    try:
        transport.runner = lambda *_args: result
        with pytest.raises(OSError, match="configuration could not be read"):
            transport.profiles()
    finally:
        runtime.close()



def test_actual_stream_survives_probe_and_inventory_fault_then_stops_on_disconnect(ordinary):  # noqa: F811
    import threading
    from loopx.extensions.lark.goal_topic_runtime import stream_lark_goal_topic_profile

    _, runtime, provider, transport = connect(ordinary)
    released, listening, fault_seen, recovered = (threading.Event() for _ in range(4))
    result = {}
    fail_inventory = False

    def snapshot():
        if fail_inventory and not fault_seen.is_set():
            saved = transport.runner
            transport.runner = lambda *_args: {"returncode": 1, "stdout": ""}
            try:
                return {"private_profiles": transport.profiles()}
            finally:
                transport.runner = saved
                fault_seen.set()
        profiles = transport.profiles()
        if fault_seen.is_set():
            recovered.set()
        return {"private_profiles": profiles}

    class Lines:
        def __iter__(self):
            yield "[event] ready event_key=im.message.receive_v1\n"
            assert released.wait(10)

    class Consumer:
        stdout = Lines()
        def poll(self):
            return 0 if released.is_set() else None
        def wait(self, timeout=None):
            assert released.wait(timeout or 10)
            return 0
        def terminate(self):
            released.set()
        kill = terminate

    stop = threading.Event()
    def run():
        result.update(stream_lark_goal_topic_profile(profile="notes-app", snapshot_provider=snapshot,
            stop=stop, runtime_root=transport.runtime_root, answer=lambda *_args: "unused",
            private_admitter=transport.admit, process_factory=lambda *_args: Consumer(),
            health_sink=lambda update: listening.set() if update.get("status") == "listening" else None))

    worker = threading.Thread(target=run)
    try:
        worker.start()
        assert listening.wait(3)
        transport.bindings.observe = lambda _profile: (_ for _ in ()).throw(ValueError("verification unavailable"))
        fail_inventory = True
        assert fault_seen.wait(3) and recovered.wait(3)
        assert worker.is_alive() and not released.is_set() and not stop.is_set()
        assert transport.admit("notes-app", provider.event("notes-app", "unverified", "/status"))["status"] == "audience_rejected"
        binding = transport._binding("notes-app")
        transport.bindings.disconnect(binding["binding_id"], expected_revision=transport.bindings.read()["revision"])
        worker.join(4)
        assert not worker.is_alive() and stop.is_set()
        assert result["status"] == "configuration_removed"
    finally:
        stop.set()
        released.set()
        worker.join(4)
        runtime.close()



def test_slow_reply_readback_does_not_hold_independent_stop_or_other_app(ordinary, monkeypatch):  # noqa: F811
    import threading
    from loopx.extensions.lark.goal_topic_runtime_service import LarkGoalTopicRuntimeService

    store, runtime, provider, transport = connect(ordinary)
    blocked, release = threading.Event(), threading.Event()
    original = Provider.__call__

    def delayed(self, args, cwd=None, timeout=None):
        if "+messages-mget" in args and args[args.index("--message-ids") + 1] == "om_out_0":
            blocked.set()
            assert release.wait(10)
        return original(self, args, cwd, timeout)

    monkeypatch.setattr(Provider, "__call__", delayed)
    transport.admit("notes-app", provider.event("notes-app", "old-status", "/status"))
    service = LarkGoalTopicRuntimeService(snapshot_provider=lambda: {}, runtime_root=store.root.parent,
        runtime_controller=runtime, private_conversations=transport)
    service.start()
    service.start()
    worker = service._delivery_thread
    assert worker is not None
    try:
        assert blocked.wait(10)
        transport.admit("notes-app", provider.event("notes-app", "urgent-stop", "/stop"))
        transport.admit("steward-app", provider.event("steward-app", "other-status", "/status"))
        deadline = time.monotonic() + 5
        while len(provider.writes) < 3:
            assert time.monotonic() < deadline
            time.sleep(.01)
        # The old provider read is still blocked, yet both exact audiences have
        # received feedback. No Session/model was needed for these controls.
        assert not release.is_set()
        assert ("notes-app", "当前没有正在执行的消息。") in provider.writes
        assert any(profile == "steward-app" and "个人助手 · notes" in text
                   for profile, text in provider.writes)
        assert store.list_sessions() == []
    finally:
        release.set()
        service.close()
        worker.join(10)
        runtime.close()
    assert not worker.is_alive()
    writes = list(provider.writes)
    transport.reconcile()
    assert provider.writes == writes  # Recovery does not resend an attempted reply.



def test_slow_app_reaction_does_not_block_current_or_later_answers(ordinary, monkeypatch):  # noqa: F811
    from loopx.extensions.lark.goal_topic_runtime_service import LarkGoalTopicRuntimeService

    store, runtime, provider, transport, original, turns, before = _app_projection_journey(ordinary)
    blocked, release = threading.Event(), threading.Event()
    provider_call = Provider.__call__

    def held_reaction(self, args, cwd=None, timeout=None):
        if "reactions" in args and "create" in args:
            ref = args[args.index("--message-id") + 1]
            if "你（App）" in json.dumps(self.messages[ref], ensure_ascii=False):
                blocked.set()
                assert release.wait(60)
        return provider_call(self, args, cwd, timeout)

    monkeypatch.setattr(Provider, "__call__", held_reaction)
    starts = ordinary[4].read_bytes()
    service = LarkGoalTopicRuntimeService(snapshot_provider=lambda: {}, runtime_root=store.root.parent,
        runtime_controller=runtime, private_conversations=transport)
    service.start()
    try:
        # Scanner startup and source verification are setup, not the held-IO
        # invariant. Start the original ten-second delivery bound only once
        # this exact provider operation is demonstrably pending.
        assert blocked.wait(20)
        deadline = time.monotonic() + 10
        while len(provider.writes) < before + 4:
            assert time.monotonic() < deadline, "A held input reaction blocked a completed answer"
            time.sleep(.01)
        assert ordinary[4].read_bytes() == starts  # No model replay to recover delivery.
        status, later = ordinary[3](f"/api/chat/sessions/{original['session_id']}/turns", {
            "message": "Later App input", "client_turn_id": "later-app-input"})
        assert status == 202
        assert runtime.wait_for_turn(session_id=original["session_id"], turn_id=later["turn_id"],
            timeout_sec=10)["status"] == "completed"
        transport._web_projection_scan_at = 0
        service._delivery_wakeup.set()
        deadline = time.monotonic() + 10
        while len(provider.writes) < before + 6:
            assert time.monotonic() < deadline, "A held reaction blocked later canonical messages"
            time.sleep(.01)
        assert not release.is_set()
        assert any("Later App input" in text for _, text in provider.writes[before:])
        assert len(store.list_sessions()) == 1
    finally:
        release.set()
        service.close()
        service._delivery_thread.join(10)
        runtime.close()
    projection = json.loads(next((transport.root / "web-projections").glob("*.json")).read_text())
    assert len(projection["deliveries"]) == 6
    assert all(phase.get("verified") for phase in projection["deliveries"].values())


def test_private_discovery_skips_unchanged_bodies_but_not_new_work_or_core_checks(tmp_path, monkeypatch):
    from loopx.chat_store import _atomic_write_json
    from loopx.extensions.lark import private_conversations as private

    clock, checks, reads = [100.0], [], []
    eligible = {"old": False}
    transport = LarkPrivateConversations.__new__(LarkPrivateConversations)
    transport.root = tmp_path
    transport._delivery_discovery = PrivateDeliveryDiscovery(tmp_path)
    transport._context_recovery_probe_at = {}
    transport._web_projection_paths = lambda: []
    transport.core = SimpleNamespace(context_recovery_observer=lambda: lambda ref:
        checks.append(ref) or eligible[ref])
    monkeypatch.setattr(private, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    read_json = private._read_json

    def observed(path):
        reads.append(path)
        return read_json(path)

    monkeypatch.setattr("loopx.extensions.lark.private_delivery_discovery._read_json", observed)
    old, pending, new = (tmp_path / f"{name}.json" for name in ("old", "pending", "new"))
    _atomic_write_json(old, {"status": "delivered", "request_ref": "old", "session_id": "session",
        "turn_id": "failed", "event": {"chat_type": "p2p", "text": "private-body-marker" * 10000},
        "deliveries": {"terminal": {"text": "private-body-marker" * 10000}}})
    _atomic_write_json(pending, {"status": "pending"})
    assert transport.pending_delivery_paths() == [pending]
    reads.clear()
    assert transport.pending_delivery_paths() == [pending] and reads == [pending]
    assert "private-body-marker" not in repr(transport._delivery_discovery._completed)
    _atomic_write_json(new, {"status": "pending"})
    assert transport.pending_delivery_paths() == [new, pending]
    clock[0] += 61
    eligible["old"] = True
    assert transport.pending_delivery_paths() == [new, old, pending]
    clock[0] += 61
    eligible["old"] = False  # A fresh Core decision wins over unchanged provider hints.
    assert transport.pending_delivery_paths() == [new, pending]
    assert checks == ["old", "old", "old"]
    old.unlink()
    assert transport.pending_delivery_paths() == [new, pending]
    assert old not in transport._delivery_discovery._completed


@pytest.mark.parametrize("replace", [False, True])
def test_private_discovery_detects_same_size_updates_with_preserved_mtime(tmp_path, monkeypatch, replace):
    import os
    from loopx.chat_store import _atomic_write_json

    transport = LarkPrivateConversations.__new__(LarkPrivateConversations)
    transport.root = tmp_path
    transport._delivery_discovery = PrivateDeliveryDiscovery(tmp_path)
    transport._context_recovery_probe_at = {}
    transport._web_projection_paths = lambda: []
    transport.core = SimpleNamespace(context_recovery_observer=lambda: lambda _: False)
    path = tmp_path / "original.json"
    _atomic_write_json(path, {"status": "delivered"})
    assert transport.pending_delivery_paths() == []
    before = path.stat()
    body = path.read_bytes().replace(b'delivered', b'uncertain')
    if replace:
        other = tmp_path / "replacement.tmp"
        other.write_bytes(body)
        os.utime(other, ns=(before.st_atime_ns, before.st_mtime_ns))
        os.replace(other, path)
    else:
        path.write_bytes(body)
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert path.stat().st_size == before.st_size and path.stat().st_mtime_ns == before.st_mtime_ns
    assert transport.pending_delivery_paths() == [path]
    # An unreadable record is held for a later scan. It keeps its bytes and
    # becomes discoverable again as soon as the original reader recovers.
    reads = []

    def unavailable(candidate):
        reads.append(candidate)
        raise OSError("read unavailable")

    with monkeypatch.context() as faults:
        faults.setattr("loopx.extensions.lark.private_delivery_discovery._read_json", unavailable)
        assert transport.pending_delivery_paths() == []
        assert reads == [path] and path.read_bytes() == body
    assert transport.pending_delivery_paths() == [path]


def test_private_discovery_does_not_cache_a_file_replaced_during_read(tmp_path, monkeypatch):
    from loopx.chat_store import _atomic_write_json
    from loopx.extensions.lark import private_conversations as private

    transport = LarkPrivateConversations.__new__(LarkPrivateConversations)
    transport.root = tmp_path
    transport._delivery_discovery = PrivateDeliveryDiscovery(tmp_path)
    transport._context_recovery_probe_at = {}
    transport._web_projection_paths = lambda: []
    transport.core = SimpleNamespace(context_recovery_observer=lambda: lambda _: False)
    path = tmp_path / "original.json"
    _atomic_write_json(path, {"status": "delivered"})
    read_json = private._read_json

    def replaced(path):
        row = read_json(path)
        _atomic_write_json(path, {"status": "pending"})
        return row

    monkeypatch.setattr("loopx.extensions.lark.private_delivery_discovery._read_json", replaced)
    assert transport.pending_delivery_paths() == []
    monkeypatch.setattr("loopx.extensions.lark.private_delivery_discovery._read_json", read_json)
    assert transport.pending_delivery_paths() == [path]


@pytest.mark.parametrize("background_slots", [0, 2])
def test_private_recovery_backlog_uses_released_capacity_before_retry_delay(tmp_path, monkeypatch, background_slots):
    from loopx.extensions.lark import goal_topic_runtime_service as worker
    from loopx.extensions.lark import private_conversations as private

    # Exercise the real discovery, bounded executor, fairness counters and
    # reconciliation entrypoint. Only native/provider work and the clock are
    # synthetic: discovering work while full must not count as attempting it.
    clock = [100.0]
    release, full = threading.Event(), threading.Event()
    lock = threading.Lock()
    attempts, completed = {}, set()
    slots = 4 - background_slots
    native_root = tmp_path / "native"
    native_root.mkdir()
    transport = LarkPrivateConversations.__new__(LarkPrivateConversations)
    transport.root = tmp_path
    transport._delivery_discovery = PrivateDeliveryDiscovery(tmp_path)
    transport._body_discovery = PrivateDeliveryDiscovery(tmp_path)
    transport._context_recovery_probe_at = {}
    transport._web_projection_paths = lambda: []
    transport.reconcile_app_feedback = lambda: 0

    def recover(ref, **_):
        with lock:
            attempts.setdefault(ref, clock[0])
            if len(attempts) == slots:
                full.set()
        assert release.wait(5)
        completed.add(ref)
        return {"request_ref": ref, "turn_id": "original-failed-turn"}

    transport.core = SimpleNamespace(root=native_root,
        context_recovery_candidate=lambda ref: ref != "ineligible",
        context_recovery_observer=lambda: transport.core.context_recovery_candidate,
        recover_failed_request=recover)
    if background_slots:
        transport.owner_attention = SimpleNamespace(has_enabled=lambda: True,
            reconcile=lambda: release.wait(5) and 0)
        transport.steward_reports = SimpleNamespace(pending=lambda: True,
            reconcile=lambda: release.wait(5) and 0)

    def write(ref):
        path = tmp_path / f"{ref}.json"
        path.write_text(json.dumps({"status": "delivered", "request_ref": ref,
            "session_id": "original-session", "turn_id": "original-failed-turn",
            "profile": "fixture", "event": {"message_id": ref}}))
        return path

    paths = [write(f"old-{i:02}") for i in range(9)]
    write("ineligible")
    original_bytes = {path: path.read_bytes() for path in tmp_path.glob("*.json")}
    service = worker.LarkGoalTopicRuntimeService(snapshot_provider=lambda: {}, runtime_root=tmp_path,
        runtime_controller=None, private_conversations=transport)
    monkeypatch.setattr(private, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(worker, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def drive(step, **_):
        # Historical discovery now hands its result back to the same scanner.
        # Drive that real handoff before asserting the unchanged pool bound.
        deadline = time.monotonic() + 2
        while not full.is_set() and time.monotonic() < deadline:
            step()
            full.wait(.01)
        assert full.is_set()
        clock[0] = 101
        step()  # No free capacity: do not put the remaining old requests off.
        paths.append(write("late"))
        clock[0] = 102
        step()  # The later arrival is also discovered while all slots are held.
        release.set()
        for tick in range(103, 115):
            clock[0] = tick
            step()
            deadline = time.monotonic() + .1
            while len(completed) < min(10, (tick - 102) * slots) and time.monotonic() < deadline:
                time.sleep(.001)
            if len(completed) == 10:
                break
        service._closed.set()

    monkeypatch.setattr(service._delivery_worker, "repeat", drive)
    try:
        service._reconcile_private()
    finally:
        release.set()
    assert set(attempts) == {path.stem for path in paths}
    assert attempts["late"] < 115
    assert all(path.read_bytes() == body for path, body in original_bytes.items())
    assert all(transport._context_recovery_probe_at[path] > clock[0] for path in paths)


@pytest.mark.parametrize("background_slots", [0, 2])
@pytest.mark.parametrize("origin", ["lark", "web"])
def test_current_body_uses_next_slot_before_unattempted_recovery_backlog(tmp_path, monkeypatch, background_slots, origin):
    from loopx.extensions.lark import goal_topic_runtime_service as worker

    release_old, release_rest = threading.Event(), threading.Event()
    changed = threading.Condition()
    started, finished = [], []
    slots = 4 - background_slots
    transport = LarkPrivateConversations.__new__(LarkPrivateConversations)
    transport.root = tmp_path
    transport._delivery_discovery = PrivateDeliveryDiscovery(tmp_path)
    transport._body_discovery = PrivateDeliveryDiscovery(tmp_path)
    transport._context_recovery_probe_at = {}
    transport.core = SimpleNamespace(context_recovery_observer=lambda: lambda ref: True)
    transport.reconcile_app_feedback = lambda: 0
    web = tmp_path / "web-projections" / "current.json"
    transport._web_projection_paths = lambda: [web] if web.exists() else []
    fresh = web if origin == "web" else tmp_path / "z-current.json"
    for index in range(slots * 3):
        (tmp_path / f"old-{index:02}.json").write_text(json.dumps({"status": "delivered",
            "request_ref": str(index), "session_id": "original", "turn_id": "failed"}))

    def reconcile(path):
        with changed:
            started.append(path)
            first_wave = len(started) <= slots
            changed.notify_all()
        assert (release_old if first_wave else release_rest).wait(5)
        with changed:
            finished.append(path)
            changed.notify_all()
        return 0

    transport.reconcile_request = reconcile
    if background_slots:
        transport.owner_attention = SimpleNamespace(has_enabled=lambda: True,
            reconcile=lambda: release_rest.wait(5) and 0)
        transport.steward_reports = SimpleNamespace(pending=lambda: True,
            reconcile=lambda: release_rest.wait(5) and 0)
    service = worker.LarkGoalTopicRuntimeService(snapshot_provider=lambda: {}, runtime_root=tmp_path,
        runtime_controller=None, private_conversations=transport)

    def drive(step, **_):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            step()
            with changed:
                if changed.wait_for(lambda: len(started) == slots, .01):
                    break
        with changed:
            assert len(started) == slots
        fresh.parent.mkdir(exist_ok=True)
        fresh.write_text(json.dumps({"status": "pending" if origin == "web" else "accepted"}))
        step()  # Retain the new discovery while every existing slot is held.
        release_old.set()
        with changed:
            assert changed.wait_for(lambda: len(finished) == slots, 2)
        # The provider returns just after its last observable checkpoint.
        # Let the actual Future settle instead of racing its done flag.
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            step()
            with changed:
                if changed.wait_for(lambda: len(started) == slots * 2, .01):
                    break
        with changed:
            assert len(started) == slots * 2
            assert fresh in started[slots:], "new body remained behind untouched historical recovery probes"
        service._closed.set()
        release_rest.set()

    monkeypatch.setattr(service._delivery_worker, "repeat", drive)
    try:
        service._reconcile_private()
    finally:
        service._closed.set()
        release_old.set()
        release_rest.set()
    assert len(started) == slots * 2 and len(set(started)) == len(started)
    assert sum(path.name.startswith("old-") for path in started) == slots * 2 - 1


@pytest.mark.parametrize(("initial", "body_scan_fails"), [
    ("accepted", False), ("accepted", True), ("delivered", False),
])
def test_already_attempted_private_path_is_rediscovered(tmp_path, monkeypatch, initial, body_scan_fails):
    from concurrent.futures import Future
    from loopx.chat_store import _atomic_write_json, _read_json
    from loopx.extensions.lark import goal_topic_runtime_service as worker
    from loopx.extensions.lark import private_conversations as private

    class ImmediateExecutor:
        """The worker still sees completed futures at the next scan boundary."""
        def __init__(self, **_):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def submit(self, action, *args, **kwargs):
            future = Future()
            try:
                future.set_result(action(*args, **kwargs))
            except Exception as error:
                future.set_exception(error)
            return future

    # Same durable path: ordinary body sends first or an earlier native probe
    # left no effect. A later full discovery finds eligibility, while the fast
    # body scan continues to exclude the now-delivered receipt.
    path = tmp_path / "original.json"
    record = {"status": initial, "request_ref": "original", "session_id": "session",
              "turn_id": "turn", "event": {"message_id": "source"}, "profile": "fixture"}
    _atomic_write_json(path, record)
    clock = [100.0]
    now = SimpleNamespace(monotonic=lambda: clock[0])
    monkeypatch.setattr(worker, "time", now)
    monkeypatch.setattr(private, "time", now)
    monkeypatch.setattr(worker, "ThreadPoolExecutor", ImmediateExecutor)
    attempts, discoveries, recovery_times = [], [], []
    required_recoveries = 1 if initial == "accepted" else 2

    def eligible(request_ref):
        assert request_ref == record["request_ref"]
        discoveries.append(clock[0])
        return attempts.count("recovery") < required_recoveries

    def reconcile(receipt):
        assert receipt == path
        current = _read_json(receipt)
        if current["status"] == "accepted":
            attempts.append("body")
            _atomic_write_json(receipt, {**current, "status": "delivered"})
        else:
            attempts.append("recovery")
            recovery_times.append(clock[0])
            transport._context_recovery_probe_at[path] = clock[0] + 60
        return 0

    transport = private.LarkPrivateConversations.__new__(private.LarkPrivateConversations)
    transport.root = tmp_path
    transport._delivery_discovery = PrivateDeliveryDiscovery(tmp_path)
    transport._body_discovery = PrivateDeliveryDiscovery(tmp_path)
    transport._context_recovery_probe_at = {}
    transport._web_projection_paths = lambda: []
    transport.reconcile_app_feedback = lambda: 0
    transport.core = SimpleNamespace(context_recovery_observer=lambda: eligible)
    transport.reconcile_request = reconcile
    pending_body_paths = transport.pending_body_paths

    def scan_bodies():
        if body_scan_fails and clock[0] == 101:
            raise OSError("temporary body scan failure")
        return pending_body_paths()

    transport.pending_body_paths = scan_bodies
    service = worker.LarkGoalTopicRuntimeService(snapshot_provider=lambda: {}, runtime_root=tmp_path,
        runtime_controller=None, private_conversations=transport)

    def drive(step, **_):
        for tick in (100, 101, 102, 161, 162, 222, 223):
            clock[0] = float(tick)
            step()

    monkeypatch.setattr(service._delivery_worker, "repeat", drive)
    service._reconcile_private()
    expected = ["body", "recovery"] if initial == "accepted" else ["recovery", "recovery"]
    assert len(discoveries) >= 2
    assert attempts == expected, (attempts, discoveries)
    if body_scan_fails:
        assert recovery_times == [102.0]
    assert _read_json(path) == {**record, "status": "delivered"}


def test_private_recovery_discovery_keeps_negative_and_attempt_backoff(tmp_path, monkeypatch):
    from loopx.extensions.lark import private_conversations as private

    clock, checks, attempts = [100.0], [], []
    eligible = {"candidate": True, "ineligible": False}
    transport = LarkPrivateConversations.__new__(LarkPrivateConversations)
    transport.root = tmp_path
    transport._delivery_discovery = PrivateDeliveryDiscovery(tmp_path)
    transport._context_recovery_probe_at = {}
    transport._web_projection_paths = lambda: []
    monkeypatch.setattr(private, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def candidate(ref):
        checks.append(ref)
        return eligible[ref]

    def unavailable(ref, **_):
        attempts.append(ref)
        raise OSError("synthetic native observation unavailable")

    transport.core = SimpleNamespace(context_recovery_candidate=candidate,
        context_recovery_observer=lambda: transport.core.context_recovery_candidate,
        recover_failed_request=unavailable)
    paths = {}
    for ref in eligible:
        path = tmp_path / f"{ref}.json"
        path.write_text(json.dumps({"status": "delivered", "request_ref": ref,
            "session_id": "original", "turn_id": "failed", "profile": "fixture",
            "event": {"message_id": ref}}))
        paths[ref] = path
    assert transport.pending_delivery_paths() == [paths["candidate"]]
    assert transport.pending_delivery_paths() == []
    assert checks.count("ineligible") == 1 and attempts == []
    clock[0] += 30  # The worker finally gets a slot after discovery.
    with pytest.raises(OSError, match="observation unavailable"):
        transport.reconcile_request(paths["candidate"])
    assert attempts == ["candidate"]
    clock[0] += 31  # Discovery's old hint elapsed; the actual probe's has not.
    assert transport.pending_delivery_paths() == []
    clock[0] += 30
    # Fresh eligibility wins over the old discovery hint; no stale cached
    # candidate is an execution grant after a source/permission/stop change.
    eligible["candidate"] = False
    assert transport.pending_delivery_paths() == []
    assert attempts == ["candidate"]


def test_private_reply_workers_have_no_executor_backlog_and_stop_scheduling(tmp_path):
    import threading
    from loopx.extensions.lark.goal_topic_runtime_service import LarkGoalTopicRuntimeService

    release, full = threading.Event(), threading.Event()
    lock = threading.Lock()
    calls = []

    class Pending:
        def pending_delivery_paths(self):
            return [tmp_path / str(index) for index in range(9)]

        def reconcile_request(self, path):
            with lock:
                calls.append(path)
                if len(calls) == 4:
                    full.set()
            assert release.wait(10)
            return 0

    service = LarkGoalTopicRuntimeService(snapshot_provider=lambda: {}, runtime_root=tmp_path,
        runtime_controller=None, private_conversations=Pending())
    service.start()
    worker = service._delivery_thread
    assert worker is not None
    try:
        assert full.wait(5)
        assert len(calls) == 4 and len(set(calls)) == 4
        closer = threading.Thread(target=service.close)
        closer.start()
        assert service._closed.wait(1)
        release.set()
        closer.join(5)
        assert not closer.is_alive()
        worker.join(5)
        assert not worker.is_alive()
        assert len(calls) == 4  # Five durable requests remain; none were queued in memory.
    finally:
        service._closed.set()
        release.set()
        worker.join(10)



def test_private_scanner_recovers_inside_its_live_pool_without_restarting_requests(tmp_path, monkeypatch, caplog):
    from loopx.extensions.lark import goal_topic_runtime_service as module

    release, full, recovered = threading.Event(), threading.Event(), threading.Event()
    calls, pools, scans = [], [], []
    pool_factory = module.ThreadPoolExecutor

    def make_pool(*args, **kwargs):
        pool = pool_factory(*args, **kwargs)
        pools.append(pool)
        return pool

    class Pending:
        def pending_delivery_paths(self):
            return [tmp_path / str(index) for index in range(9)]

        def reconcile_request(self, path):
            calls.append(path)
            if len(calls) == 4:
                full.set()
            assert release.wait(10)
            return 0

    monkeypatch.setattr(module, "ThreadPoolExecutor", make_pool)
    service = module.LarkGoalTopicRuntimeService(snapshot_provider=lambda: {}, runtime_root=tmp_path,
        runtime_controller=None, private_conversations=Pending())
    clear = service._delivery_wakeup.clear

    def fail_once():
        scans.append(True)
        if len(scans) == 2:
            raise AssertionError("private synthetic scanner detail")
        clear()
        if len(scans) > 2:
            recovered.set()

    monkeypatch.setattr(service._delivery_wakeup, "clear", fail_once)
    service.start()
    service._delivery_worker.retry_seconds = .01  # Only this disposable scanner's failure wait.
    try:
        assert full.wait(3)
        assert recovered.wait(4), "Scanner recovery must not wait for unrelated in-flight writes to drain"
        assert len(pools) == 1 and len(calls) == 4 and len(set(calls)) == 4
        assert "private synthetic scanner detail" not in caplog.text
    finally:
        release.set()
        service.close()
        service._delivery_thread.join(5)
    assert not service._delivery_thread.is_alive() and len(calls) == 4


def test_completed_command_recovery_defers_app_probe_and_revocation_blocks_reply(ordinary):  # noqa: F811
    store, runtime, provider, transport = connect(ordinary)
    try:
        for profile in ["notes-app", "steward-app"]:
            transport.admit(profile, provider.event(profile, profile, "/status"))
        first = next(row for row in transport.core.pending()
                     if row["binding_id"] == transport.bindings.read()["bindings"][0]["binding_id"])
        observed = []
        original = transport.bindings.observe
        transport.bindings.observe = lambda profile: (observed.append(profile), original(profile))[1]
        transport.core.recover(request_ref=first["request_ref"])
        assert observed == []  # A completed command has no Core work to recover.
        binding = transport.bindings.read()
        transport.bindings.disconnect(first["binding_id"], expected_revision=binding["revision"])
        assert transport.reconcile_request(transport.root / f"{first['request_ref']}.json") == 0
        assert provider.writes == [] and store.list_sessions() == []
    finally:
        runtime.close()


def test_original_reply_is_delivered_while_historical_recovery_discovery_waits(ordinary, monkeypatch):  # noqa: F811
    from loopx.chat_store import _atomic_write_json, _read_json
    from loopx.extensions.lark.goal_topic_runtime_service import LarkGoalTopicRuntimeService

    store, runtime, provider, transport = connect(ordinary)
    scanning, release, delivered = threading.Event(), threading.Event(), threading.Event()
    old_ref = "0" * 24
    old_path = transport.root / f"{old_ref}.json"
    # This is only a synthetic historical discovery candidate, with no grant
    # or Core request. Its blocked read must not delay a real original answer.
    _atomic_write_json(old_path, {"status": "delivered", "request_ref": old_ref,
        "session_id": "historical", "turn_id": "historical", "event": {}, "deliveries": {}})
    observer = transport.core.context_recovery_observer

    def recovery_observer():
        original = observer()

        def candidate(ref):
            if ref == old_ref:
                scanning.set()
                assert release.wait(15)
                return False
            return original(ref)
        return candidate

    monkeypatch.setattr(transport.core, "context_recovery_observer", recovery_observer)
    original_reconcile = transport.reconcile_request
    target = [None]

    def reconcile(path):
        result = original_reconcile(path)
        if path == target[0] and _read_json(path).get("status") == "delivered":
            delivered.set()
        return result

    monkeypatch.setattr(transport, "reconcile_request", reconcile)
    service = LarkGoalTopicRuntimeService(snapshot_provider=lambda: {}, runtime_root=transport.runtime_root,
        runtime_controller=runtime, private_conversations=transport,
        profile_poller=lambda _profile, stop: stop.wait(15))
    service.start()
    try:
        assert scanning.wait(5)
        event = provider.event("notes-app", "fresh-during-history-read", "Explain this original question")
        target[0] = transport.root / f"{identity_ref(transport._binding('notes-app')['provider_ref'], event['message_id'])}.json"
        assert service._admit_private("notes-app", event)["status"] == "durably_accepted"
        request = next(row for row in transport.core.pending() if row.get("message") == event["content"])
        assert target[0].stem == request["request_ref"]
        turn = runtime.wait_for_turn(session_id=request["session_id"], turn_id=request["turn_id"], timeout_sec=5)
        assert turn["status"] == "completed"
        assert delivered.wait(4), "A historical read blocked the original reply worker"
        assert not release.is_set()
        record = _read_json(target[0])
        terminal = record["deliveries"]["terminal"]
        assert terminal["verified"] and terminal["text"] == turn["response"]["message"]
        final = provider.messages[terminal["attempt"]["message_ref"]]
        assert json.loads(final["body"]["content"])["zh_cn"]["content"][0][0]["text"] == turn["response"]["message"]
        assert len(store.list_sessions()) == 1
        packets = [json.loads(line) for line in ordinary[4].read_text().splitlines()]
        assert sum(packet["method"] == "turn/start" for packet in packets) == 1
        assert _read_json(old_path)["status"] == "delivered"
    finally:
        release.set()
        service.close()
        if service._delivery_thread is not None:
            service._delivery_thread.join(5)
        runtime.close()


def test_original_reply_gets_free_worker_when_background_checks_are_due(ordinary, monkeypatch):  # noqa: F811
    from types import SimpleNamespace

    from loopx.chat_store import _read_json
    from loopx.extensions.lark import goal_topic_runtime_service as module

    store, runtime, provider, transport = connect(ordinary)
    discovery_started, background_due, release, delivered, ingress_started = (
        threading.Event() for _ in range(5)
    )
    # Advance only the scheduler's due checks. The four worker threads, Core
    # admission and provider readback still use their normal entry points.
    monkeypatch.setattr(module, "time", SimpleNamespace(
        monotonic=lambda: 61.0 if background_due.is_set() else 0.0,
    ))
    pending_bodies = transport.pending_body_paths
    monkeypatch.setattr(transport, "pending_body_paths",
        lambda: pending_bodies() if background_due.is_set() else [])
    monkeypatch.setattr(transport, "reconcile_app_feedback", None)

    def discover_history():
        discovery_started.set()
        assert release.wait(12)
        return []

    def reconcile_background():
        assert release.wait(12)
        return 0

    def observe_ingress(*, cancelled):
        ingress_started.set()
        assert release.wait(12)
        return []

    monkeypatch.setattr(transport, "pending_delivery_paths", discover_history)
    monkeypatch.setattr(transport.steward_reports, "pending", background_due.is_set)
    monkeypatch.setattr(transport.steward_reports, "reconcile", reconcile_background)
    monkeypatch.setattr(transport.owner_attention, "has_enabled", background_due.is_set)
    monkeypatch.setattr(transport.owner_attention, "reconcile", reconcile_background)
    monkeypatch.setattr(transport, "observe_ingress", observe_ingress)
    event = provider.event("notes-app", "reply-during-due-checks", "Explain the original request")
    ref = identity_ref(transport._binding("notes-app")["provider_ref"], event["message_id"])
    target = transport.root / f"{ref}.json"
    reconcile_original = transport.reconcile_request

    def reconcile(path):
        result = reconcile_original(path)
        if path == target and result == 1:
            delivered.set()
        return result

    monkeypatch.setattr(transport, "reconcile_request", reconcile)
    service = module.LarkGoalTopicRuntimeService(snapshot_provider=lambda: {}, runtime_root=transport.runtime_root,
        runtime_controller=runtime, private_conversations=transport,
        profile_poller=lambda _profile, stop: stop.wait(12))
    service.start()
    try:
        assert discovery_started.wait(5)
        assert transport.admit("notes-app", event)["status"] == "durably_accepted"
        request = transport.core.read_request(ref)
        turn = runtime.wait_for_turn(session_id=request["session_id"], turn_id=request["turn_id"], timeout_sec=5)
        assert turn["status"] == "completed"
        background_due.set()
        service._delivery_wakeup.set()
        assert delivered.wait(4), "Due background checks occupied every worker ahead of an original reply"
        assert ingress_started.wait(4)
        terminal = _read_json(target)["deliveries"]["terminal"]
        assert terminal["verified"] and terminal["text"] == turn["response"]["message"]
        reply = provider.messages[terminal["attempt"]["message_ref"]]
        assert reply["chat_id"] == event["chat_id"]
        assert json.loads(reply["body"]["content"])["zh_cn"]["content"][0][0]["text"] == terminal["text"]
        assert len(store.list_sessions()) == 1
        packets = [json.loads(line) for line in ordinary[4].read_text().splitlines()]
        assert sum(packet["method"] == "turn/start" for packet in packets) == 1
    finally:
        release.set()
        service.close()
        if service._delivery_thread is not None:
            service._delivery_thread.join(5)
        runtime.close()


def test_historical_discovery_uses_one_existing_slot_without_an_executor_backlog(tmp_path):
    from loopx.extensions.lark.goal_topic_runtime_service import LarkGoalTopicRuntimeService

    scanning, release_scan, full, release_bodies = (threading.Event() for _ in range(4))
    calls, bodies = [], []
    lock = threading.Lock()

    class Pending:
        def pending_body_paths(self):
            return list(bodies)

        def pending_delivery_paths(self):
            scanning.set()
            assert release_scan.wait(15)
            return []

        def reconcile_request(self, path):
            with lock:
                calls.append(path)
                if len(calls) == 3:
                    full.set()
            assert release_bodies.wait(15)
            return 0

    service = LarkGoalTopicRuntimeService(snapshot_provider=lambda: {}, runtime_root=tmp_path,
        runtime_controller=None, private_conversations=Pending())
    service.start()
    closer = None
    try:
        assert scanning.wait(5)
        for index in range(9):
            path = tmp_path / str(index)
            path.write_text("durable original request")
            bodies.append(path)
        service._delivery_wakeup.set()
        assert full.wait(4)
        assert len(calls) == 3 and len(set(calls)) == 3  # One scan plus three replies, total four.
        closer = threading.Thread(target=service.close)
        closer.start()
        assert service._closed.wait(1)
    finally:
        release_scan.set()
        release_bodies.set()
        service.close()
        if closer is not None:
            closer.join(5)
        service._delivery_thread.join(5)
    assert not service._delivery_thread.is_alive()
    assert len(calls) == 3  # The other six durable requests were never submitted to the executor.
