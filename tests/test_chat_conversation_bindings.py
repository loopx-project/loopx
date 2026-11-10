"""Bound external project Chat uses Core Session, queue, and exact stop."""
import time
import json

import pytest

from extensions.test_lark_private_manager_returns import private_return, return_root  # noqa: F401
from test_chat_ordinary_project import ordinary  # noqa: F401, F811
from test_native_steward_private import steward  # noqa: F401
from test_manager_context_roundtrip import flow  # noqa: F401
from loopx.capabilities.native_chat.conversation_bindings import ChatConversationBindings
from loopx.extensions.lark.conversation_identity import identity_ref


@pytest.mark.parametrize("fault", [None, "owned-adapter", "background", "source-changed", "stop",
    "revoked", "queued", "review-metadata", "reservation-crash"])
def test_original_project_worker_reviews_existing_partial_results_without_business_replay(ordinary, monkeypatch, fault):  # noqa: F811
    from datetime import datetime, timedelta, timezone
    from pathlib import Path
    from loopx.extensions.lark.private_conversations import LarkPrivateConversations
    from loopx.extensions.lark.private_progress import presentation_phase
    from test_lark_private_conversations import connect

    store, runtime, _, _, capture, fake, workspace = ordinary
    work = {"remaining": "Confirm the existing update", "dependency": "Original source outage", "effects_settled": True}
    partial = "Only part was confirmed.\n<loopx-review-json>" + json.dumps({
        "message": "", "unfinished_work": work}) + "</loopx-review-json>"
    inventory = workspace / "native-result-inventory.json"
    code = fake.read_text().replace("turn_count = 0", f"turn_count = 100 if __import__('os').path.exists({str(inventory)!r}) else 0")
    start = code.index("        response = 'Visible runtime response.")
    end = code.index('        print(json.dumps({"method": "item/agentMessage/delta"', start)
    code = code[:start] + f'''        response = {partial!r} if "Core admitted ONE read-only review" not in json.dumps(request["params"]) or {fault == 'review-metadata'!r} else "Existing local result verified; no business operation was repeated."
''' + code[end:]
    marker = '        result = {"thread": {"id": "durable-thread"},'
    code = code.replace(marker, '''        permissions = request.get("params", {}).get("permissions")
        result = {**({"activePermissionProfile": {"id": permissions}, "runtimeWorkspaceRoots": [request["params"]["cwd"]]} if permissions else {}), "thread": {"id": "durable-thread"},''')
    start = code.index('    elif method == "thread/turns/list":')
    end = code.index('    else:\n        result = {}', start)
    code = code[:start] + f'''    elif method in {{"thread/turns/list", "thread/items/list", "thread/backgroundTerminals/list"}}:
        if __import__('os').path.exists({str(inventory)!r}):
            with open({str(inventory)!r}) as observed:
                saved = json.load(observed)
            result = {{"data": saved["turns" if method == "thread/turns/list" else "items" if method == "thread/items/list" else "background"]}}
        else:
            result = {{"data": []}}
''' + code[end:]
    fake.write_text(code)
    store, runtime, provider, transport = connect(ordinary)
    try:
        event = provider.event("notes-app", "old-partial-result", "Confirm the requested update")
        transport.admit("notes-app", event)
        binding = next(row for row in transport.bindings.read()["bindings"] if row["transport_ref"] == "notes-app")
        ref = identity_ref(binding["provider_ref"], event["message_id"])
        original = transport.core.read_request(ref)
        sid = original["session_id"]
        ended = runtime.wait_for_turn(session_id=sid, turn_id=original["turn_id"], timeout_sec=10)
        assert ended["status"] == "completed" and ended["response"]["unfinished_work"] == work
        transport.reconcile()
        path = transport.root / f"{ref}.json"
        old_delivery = json.loads(path.read_text())["deliveries"]["terminal"]
        store.update_turn(sid, original["turn_id"],
            completed_at=(datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat())
        ended = store.load_turn(sid, original["turn_id"])
        old_bytes = store._turn_path(sid, original["turn_id"]).read_bytes()
        native = ended["upstream_turn_id"]
        inventory.write_text(json.dumps({"turns": [{"id": native, "status": "completed"}],
            "items": [{"turnId": native, "item": {"id": "user", "type": "userMessage"}},
                {"turnId": native, "item": {"id": "effect", "type": "commandExecution", "status": "failed", "exitCode": 130}},
                {"turnId": native, "item": {"id": "answer", "type": "agentMessage", "phase": "final_answer", "text": partial}}],
            "background": [{"itemId": "effect", "processId": "owned"}] if fault == "background" else []}))
        artifact = workspace / "existing-result.md"
        artifact.write_text("Already produced by the original execution.\n")
        retained = artifact.read_bytes()
        if fault not in {"owned-adapter", "background"}:
            runtime.adapters.pop(sid).close_session()
        owner = runtime.adapters.get(sid)
        Path(store.load_session(sid)["codex_home"]).mkdir(parents=True, exist_ok=True)
        if fault == "source-changed":
            provider.messages[event["message_id"]]["content"] = "Revised request"
        elif fault == "stop":
            transport.admit("notes-app", provider.event("notes-app", "later-stop", "/stop"))
        elif fault == "revoked":
            transport.bindings.disconnect(original["binding_id"], expected_revision=transport.bindings.read()["revision"])
        elif fault == "queued":
            store.create_queued_turn(sid, client_turn_id="newer-input", message="New input first", origin="web")

        def worker():
            return LarkPrivateConversations(controller=runtime, runtime_root=store.root.parent,
                runner=provider, cli_bin="lark-cli")

        selected = worker()
        if fault == "reservation-crash":
            enqueue = runtime.enqueue_turn
            with monkeypatch.context() as crash:
                crash.setattr(runtime, "enqueue_turn", lambda **_: (_ for _ in ()).throw(OSError("reservation crash")))
                with pytest.raises(OSError, match="reservation crash"):
                    selected.reconcile_request(path)
            assert runtime.enqueue_turn == enqueue
            selected = worker()
        if fault == "revoked":
            selected.reconcile()  # The original service contains a revoked-source read failure.
        else:
            selected.reconcile_request(path)
        recovered = selected.core.read_request(ref)
        if fault in {"owned-adapter", "background", "source-changed", "stop", "revoked", "queued"}:
            assert recovered["turn_id"] == original["turn_id"] and not recovered.get("context_recovery")
            if fault == "owned-adapter":
                assert runtime.adapters[sid] is owner and owner.healthcheck()
                calls = [json.loads(line) for line in capture.read_text().splitlines()]
                assert sum(row.get("method") == "turn/start" for row in calls) == 1
        else:
            assert recovered["turn_id"] != original["turn_id"]
            assert recovered["context_recovery"]["read_only_review"] is True
            assert recovered["context_recovery"]["unfinished_work"] == work
            assert not any(recovered["context_recovery"].get(k) for k in ["work_attempt", "quota_attempt", "routing_attempt", "answer_review"])
            result = runtime.wait_for_turn(session_id=sid, turn_id=recovered["turn_id"], timeout_sec=10)
            assert result["status"] == ("failed" if fault == "review-metadata" else "completed"), result
            selected.reconcile_request(path)
            delivered = json.loads(path.read_text())
            assert delivered["deliveries"]["terminal"] == old_delivery
            assert delivered["deliveries"][presentation_phase(recovered, "terminal")]["verified"]
            assert selected.health()[original["binding_id"]]["pending_count"] == 1
            assert not selected.core.context_recovery_candidate(ref)
            writes = list(provider.writes)
            selected.reconcile_request(path)
            assert provider.writes == writes
            calls = [json.loads(line) for line in capture.read_text().splitlines()]
            starts = [row["params"] for row in calls if row.get("method") == "turn/start"]
            assert len(starts) == 2 and starts[-1]["permissions"] == "loopx_workspace_only_read"
            assert "Core admitted ONE read-only review" in starts[-1]["input"][0]["text"]
            assert not any(row.get("method") in {"thread/fork", "thread/inject_items"} for row in calls)
        assert store._turn_path(sid, original["turn_id"]).read_bytes() == old_bytes
        assert artifact.read_bytes() == retained
    finally:
        runtime.close()


@pytest.mark.parametrize("command", ["stop", "new"])
def test_quota_backlog_observes_a_later_source_stop_before_new_admission(ordinary, monkeypatch, command):  # noqa: F811
    from datetime import datetime, timedelta, timezone
    from loopx.capabilities.native_chat import external_conversations
    from test_lark_private_conversations import connect

    store, runtime, provider, transport = connect(ordinary)
    fake = ordinary[-2]
    fake.write_text(fake.read_text().replace('"codexErrorInfo": "cyberPolicy"',
        '"codexErrorInfo": "usageLimitExceeded"').replace(
        '            print(json.dumps({"method": "item/agentMessage/delta", "params": {"threadId": "durable-thread", "turnId": active_turn, "delta": "Partial answer."}}), flush=True)\n', ''))
    try:
        transport.admit("notes-app", provider.event("notes-app", "quota-stop", "typed terminal failure"))
        row = transport.core.pending()[0]
        failed = runtime.wait_for_turn(session_id=row["session_id"], turn_id=row["turn_id"], timeout_sec=10)
        assert failed["error_code"] == "usage_limit_exceeded"
        transport.reconcile()
        clock = datetime.now(timezone.utc) + timedelta(seconds=61)
        class AfterBackoff(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock
        monkeypatch.setattr(external_conversations, "datetime", AfterBackoff)
        original = transport.core.read_request(row["request_ref"])
        assert transport.core._context_recovery_plan(original, discovery_only=True)["recovery_candidate"]
        transport.admit("steward-app", provider.event("steward-app", "unrelated-stop", "/stop"))
        assert transport.core._context_recovery_plan(original, discovery_only=True)["recovery_candidate"]
        assert transport.health()[row["binding_id"]]["pending_count"] == 1
        transport.admit("notes-app", provider.event("notes-app", "later-stop", f"/{command}"))
        transport.reconcile()
        assert transport.health()[row["binding_id"]]["pending_count"] == 0
        calls = ordinary[4].read_bytes()
        assert not transport.core._context_recovery_plan(original, discovery_only=True)["recovery_candidate"]
        assert ordinary[4].read_bytes() == calls
        assert store.load_turn(row["session_id"], row["turn_id"]) == failed
        transport.admit("notes-app", provider.event("notes-app", "future-request", "A new independent request"))
        newer = next(x for x in transport.core.pending() if x.get("message") == "A new independent request")
        completed = runtime.wait_for_turn(session_id=newer["session_id"], turn_id=newer["turn_id"], timeout_sec=10)
        assert completed["status"] == "completed"
        transport.reconcile()
        from loopx.extensions.lark.private_conversations import LarkPrivateConversations
        restarted = LarkPrivateConversations(controller=runtime, runtime_root=transport.runtime_root,
            runner=provider, cli_bin="lark-cli")
        before = ordinary[4].read_bytes(), list(provider.calls)
        assert restarted.health()[row["binding_id"]]["pending_count"] == 0
        assert (ordinary[4].read_bytes(), provider.calls) == before
        assert store.load_turn(row["session_id"], row["turn_id"]) == failed
    finally:
        runtime.close()


@pytest.fixture
def recorded_return_continuation(private_return, monkeypatch):  # noqa: F811
    """Use the real admission/recovery owners; only model/provider IO is fake."""
    from datetime import datetime, timedelta, timezone
    from pathlib import Path
    from loopx.capabilities import manager_context
    from loopx.capabilities.native_chat import external_conversations
    from loopx.capabilities.native_chat.codex_adapter import CodexAppServerAdapter

    sender, session, _, route, _, provider, transport, _ = private_return
    runtime = transport.core.controller
    store = transport.core.controller.store
    runtime.adapters.pop(session["session_id"]).close_session()
    fake = Path(runtime.codex_bin)
    marker = fake.with_name("quota-failed-once")
    source = fake.read_text().replace('"codexErrorInfo": "cyberPolicy"', '"codexErrorInfo": "usageLimitExceeded"')
    source = source.replace('        if "typed terminal failure" in json.dumps(request.get("params") or {}):',
        f'        if "typed terminal failure" in json.dumps(request.get("params") or {{}}) and not __import__("os").path.exists({str(marker)!r}):\n'
        f'            open({str(marker)!r}, "w").close()')
    source = source.replace('            print(json.dumps({"method": "item/agentMessage/delta", "params": {"threadId": "durable-thread", "turnId": active_turn, "delta": "Partial answer."}}), flush=True)\n', '')
    fake.write_text(source)
    transport.runner = provider
    event = provider.event("steward-app", "quota-original", "typed terminal failure")
    if route["source_id"].startswith("lark:"):
        register = manager_context.register_ingress
        def legacy_ingress(root, **kwargs):
            if kwargs["message"] == event["content"]:
                kwargs["source_id"] = "lark:" + event["message_id"]
            return register(root, **kwargs)
        monkeypatch.setattr(manager_context, "register_ingress", legacy_ingress)
    transport.admit("steward-app", event)
    ref = identity_ref(session["steward_context"]["provider_ref"], event["message_id"])
    row = transport.core.read_request(ref)
    failed = runtime.wait_for_turn(session_id=row["session_id"], turn_id=row["turn_id"], timeout_sec=10)
    assert failed["status"] == "failed" and failed["delta_count"] == 0
    failed_path = store._turn_path(row["session_id"], row["turn_id"])
    failed_bytes = failed_path.read_bytes()
    transport.reconcile()
    clock = datetime.now(timezone.utc) + timedelta(seconds=61)
    class AfterBackoff(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock
    monkeypatch.setattr(external_conversations, "datetime", AfterBackoff)
    monkeypatch.setattr(CodexAppServerAdapter, "failed_request_observation", staticmethod(lambda **_: {
        "thread_id": "durable-thread", "turn_id": failed["upstream_turn_id"],
        "only_original_user_item": True, "codex_error_info": "usageLimitExceeded",
        "quota_available": True, "quota_observed_at": clock.timestamp() - 1}))
    # A restarted reply worker has no previous monotonic discovery hint.
    transport._context_recovery_probe_at.clear()
    transport.reconcile()
    row = transport.core.read_request(ref)
    assert row["turn_id"] != failed["turn_id"]
    turn = runtime.wait_for_turn(session_id=row["session_id"], turn_id=row["turn_id"], timeout_sec=10)
    assert turn["status"] == "completed" and turn["client_turn_id"].endswith("-quota-1")
    transport.reconcile()
    session = store.load_session(row["session_id"])
    delivered = manager_context.deliver(sender.root, runtime.registry_path,
        session=session, turn=turn, request={k: route[k] for k in ("goal_id", "agent_id")},
        source_store=store)
    route = {**route, "source_id": "lark:" + event["message_id"] if route["source_id"].startswith("lark:") else ref,
             "request_id": delivered["request_id"]}
    replies = []
    def runner(args, cwd=None, timeout=None):
        if "+messages-reply" not in args and not ("+messages-send" in args and "--content" in args):
            return provider(args, cwd, timeout)
        provider.calls.append(list(args))
        assert args[args.index("--profile") + 1] == "steward-app"
        if "--message-id" in args:
            assert args[args.index("--message-id") + 1] == event["message_id"]
        else:
            assert args[args.index("--chat-id") + 1] == event["chat_id"]
        content = args[args.index("--content") + 1]
        if "--dry-run" in args:
            data = {"api": [{"body": {"msg_type": "post", "content": content}}]}
        else:
            replies.append(list(args))
            provider.messages["om_out_recovered_worker"] = {"message_id": "om_out_recovered_worker", "msg_type": "post", "body": {"content": content}}
            data = {"data": {"message_id": "om_out_recovered_worker"}}
        return {"returncode": 0, "stdout": json.dumps(data)}
    transport.runner = runner
    yield sender, session, turn, route, row, provider, transport, replies
    assert failed_path.read_bytes() == failed_bytes


def test_recorded_continuation_returns_original_worker_result_once(recorded_return_continuation):
    from datetime import datetime, timedelta, timezone
    from loopx.capabilities.manager_context import acknowledge
    from loopx.capabilities.manager_context.roundtrip import drain, report, reply_status
    from loopx.chat_store import ChatSessionStore

    sender, session, turn, route, _, provider, transport, replies = recorded_return_continuation
    controller = transport.core.controller
    store = controller.store
    capture = store.root.parent.parent / "requests.jsonl"
    before_host_calls = capture.read_bytes()
    before_turns = sorted(store._session_dir(session["session_id"]).glob("turns/*.json"))
    acknowledge(sender.root, route["goal_id"], route["agent_id"], route["request_id"], "adopt", "Use the original audience")
    report(sender.root, route["goal_id"], route["agent_id"], route["request_id"], "conclusion", "Verified worker conclusion")
    provider.verify_replies = False
    drain(sender.root, controller.registry_path, store, sender)
    assert len(replies) == 1
    assert reply_status(sender.root, route)[0]["status"] == "verification_required"
    provider.verify_replies = True
    cold = ChatSessionStore(store.root.parent)
    drain(sender.root, controller.registry_path, cold, sender,
          now=datetime.now(timezone.utc) + timedelta(minutes=10))
    assert reply_status(sender.root, route)[0]["status"] == "delivered"
    assert len(replies) == 1
    returned = [r for r in cold.messages(session["session_id"]) if r.get("origin") == "manager_followup"]
    assert len(returned) == 1 and returned[0]["turn_id"] == turn["turn_id"]
    assert "Verified worker conclusion" in returned[0]["text"]
    assert sorted(store._session_dir(session["session_id"]).glob("turns/*.json")) == before_turns
    assert capture.read_bytes() == before_host_calls


@pytest.mark.parametrize("fault", ["unrecorded", "transport", "native", "unknown-client", "review-policy", "binding", "source"])
def test_continuation_return_rejects_missing_or_changed_source(recorded_return_continuation, fault):
    from loopx.chat_store import _atomic_write_json, _read_json

    sender, session, turn, route, row, provider, transport, replies = recorded_return_continuation
    if fault in {"unrecorded", "transport", "native", "review-policy"}:
        roots = (transport.root, transport.core.root) if fault == "unrecorded" else (
            transport.core.root if fault in {"native", "review-policy"} else transport.root,)
        for root in roots:
            path = root / f"{row['request_ref']}.json"
            value = _read_json(path)
            if fault == "unrecorded":
                value.pop("context_recovery")
            elif fault == "review-policy":
                value["context_recovery"]["read_only_review"] = True
            else:
                value["context_recovery"]["client_turn_id"] += "-changed"
            _atomic_write_json(path, value)
    elif fault == "unknown-client":
        turn = {**turn, "client_turn_id": turn["client_turn_id"] + "-unknown"}
    elif fault == "binding":
        transport.bindings.disconnect(row["binding_id"], expected_revision=transport.bindings.read()["revision"])
    else:
        record = _read_json(transport.root / f"{row['request_ref']}.json")
        provider.messages[record["event"]["message_id"]]["sender"]["id"] = "ou_other"
    try:
        result = sender(route, session, turn, "Must stay with the original authorized source")
        assert not result["reply_verified"]
    except ValueError:
        pass
    assert replies == []


def _history_page_brief():
    return {"schema_version": "collaboration_brief_v0", "purpose": "Inspect original history",
        "context": "Keep the original conversation", "constraints": ["Do not publish"],
        "inputs": [], "acceptance": ["Return the original conclusion"], "return_requirement": "Return here"}


@pytest.mark.parametrize("fault", [None, "stop", "edited-source", "revoked", "unknown-native", "other-stale"])
def test_original_worker_recovers_disconnected_user_only_interruption(steward, monkeypatch, fault):  # noqa: F811
    from datetime import datetime, timedelta, timezone
    from pathlib import Path
    from loopx.capabilities.native_chat import external_conversations

    store, runtime, provider, transport, binding, capture, _ = steward
    Path(runtime.codex_home).mkdir(parents=True, exist_ok=True)
    fake = Path(runtime.codex_bin)
    marker = fake.with_name("interrupted-native-input.json")
    source = fake.read_text()
    source = source.replace('turn_count = 0', f'turn_count = 1 if __import__("os").path.exists({str(marker)!r}) else 0')
    begin = '        if "typed terminal failure" in json.dumps(request.get("params") or {}):'
    assert begin in source
    source = source.replace(begin,
        begin[:-1] + f' and not __import__("os").path.exists({str(marker)!r}):\n'
        f'            open({str(marker)!r}, "w").write(json.dumps({{"turn_id": active_turn}}))\n'
        '            raise SystemExit(0)')
    branch = '    elif method == "thread/turns/list":'
    assert branch in source
    source = source.replace(branch,
        f'    elif method == "thread/turns/list" and __import__("os").path.exists({str(marker)!r}):\n'
        f'        result = {{"data": [{{"id": json.load(open({str(marker)!r}))["turn_id"], "status": "interrupted"}}]}}\n'
        '    elif method == "thread/items/list":\n'
        '        result = {"data": [{"turnId": request["params"]["turnId"], "item": {"id": "user", "type": "userMessage"}}]}\n'
        + branch)
    fake.write_text(source)
    event = provider.event("steward-app", "lost-server", "typed terminal failure")
    transport.admit("steward-app", event)
    ref = identity_ref(binding["provider_ref"], event["message_id"])
    row = transport.core.read_request(ref)
    failed = runtime.wait_for_turn(session_id=row["session_id"], turn_id=row["turn_id"], timeout_sec=10)
    assert failed["error_code"] == "host_gate" and failed["delta_count"] == 0
    deadline = time.monotonic() + 2
    while store.load_session(row["session_id"])["status"] != "stale":
        assert time.monotonic() < deadline
        time.sleep(.01)
    assert store.load_session(row["session_id"])["last_error_code"] == "transport_disconnected"
    failed_bytes = store._turn_path(row["session_id"], row["turn_id"]).read_bytes()
    assert transport.reconcile() == 1  # Keep the original failure notice.
    clock = datetime.now(timezone.utc) + timedelta(seconds=61)
    class AfterBackoff(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock
    monkeypatch.setattr(external_conversations, "datetime", AfterBackoff)
    if fault == "stop":
        transport.admit("steward-app", provider.event("steward-app", "later-stop", "/stop"))
    elif fault == "edited-source":
        provider.messages[event["message_id"]]["content"] = "Changed original source"
    elif fault == "revoked":
        transport.bindings.disconnect(binding["binding_id"], expected_revision=transport.bindings.read()["revision"])
    elif fault == "unknown-native":
        fake.write_text(fake.read_text().replace('"type": "userMessage"', '"type": "unknownItem"'))
    elif fault == "other-stale":
        store.update_session(row["session_id"], last_error_code="unverified")
    before = [json.loads(line) for line in capture.read_text().splitlines()]
    transport._context_recovery_probe_at.clear()
    transport.reconcile()
    current = transport.core.read_request(ref)
    if fault is None:
        assert current["turn_id"] != row["turn_id"] and current["session_id"] == row["session_id"]
        completed = runtime.wait_for_turn(session_id=row["session_id"], turn_id=current["turn_id"], timeout_sec=10)
        assert completed["status"] == "completed"
        assert completed["client_turn_id"].endswith("-routing-1")
        assert current["context_recovery"]["native_observation"]["interrupted_before_execution"] is True
        transport.reconcile()
        assert transport.core.read_request(ref)["delivery_verified"] is True
        after = capture.read_bytes()
        transport.reconcile()
        assert capture.read_bytes() == after  # No second model dispatch after verified return.
    else:
        assert current["turn_id"] == row["turn_id"] and not current.get("context_recovery")
    after = [json.loads(line) for line in capture.read_text().splitlines()]
    def count(calls):
        return sum(p.get("method") == "turn/start" for p in calls)
    assert count(after) == count(before) + (fault is None)
    assert store._turn_path(row["session_id"], row["turn_id"]).read_bytes() == failed_bytes


@pytest.mark.parametrize("change", ["status", "new-input", "source-read-input", "unrelated-source", "revoked", "unknown-native", "unchanged"])
def test_slow_failed_request_probe_does_not_hold_the_source_route(steward, monkeypatch, change):  # noqa: F811
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path
    from loopx.capabilities.native_chat.codex_adapter import CodexAppServerAdapter

    store, runtime, provider, transport, binding, capture, _ = steward
    fake = Path(runtime.codex_bin)
    fake.write_text(fake.read_text().replace('"codexErrorInfo": "cyberPolicy"',
        '"codexErrorInfo": "contextWindowExceeded"').replace(
        '            print(json.dumps({"method": "item/agentMessage/delta", "params": {"threadId": "durable-thread", "turnId": active_turn, "delta": "Partial answer."}}), flush=True)\n', ''))
    event = provider.event("steward-app", "slow-native-read", "typed terminal failure")
    transport.admit("steward-app", event)
    ref = identity_ref(binding["provider_ref"], event["message_id"])
    row = transport.core.read_request(ref)
    failed = runtime.wait_for_turn(session_id=row["session_id"], turn_id=row["turn_id"], timeout_sec=10)
    assert failed["status"] == "failed" and failed["delta_count"] == 0
    store.update_session(row["session_id"], manager_context_version=20)
    assert transport.reconcile() == 1
    original = store._turn_path(row["session_id"], row["turn_id"]).read_bytes()
    before = capture.read_bytes()
    started, release = threading.Event(), threading.Event()

    def slow_probe(**_):
        started.set()
        assert release.wait(10)
        # Even a valid read cannot override input or revocation during IO.
        return None if change == "unknown-native" else {"thread_id": "durable-thread",
            "turn_id": failed["upstream_turn_id"], "only_original_user_item": True,
            "codex_error_info": "contextWindowExceeded"}

    monkeypatch.setattr(CodexAppServerAdapter, "failed_request_observation", staticmethod(slow_probe))

    def source_observation(_):
        if change == "source-read-input":
            store.create_queued_turn(row["session_id"], client_turn_id="input-during-source-read",
                message="A correction while reading the provider", origin="web")
        return {"verified": True}

    with ThreadPoolExecutor(max_workers=1) as pool:
        recovery = pool.submit(transport.core.recover_failed_request, ref,
            source_observation=source_observation)
        try:
            assert started.wait(5)
            if change in {"status", "unknown-native"}:
                begin = time.monotonic()
                admitted = transport.admit("steward-app", provider.event("steward-app", "during-native-read", "/status"))
                assert admitted["status"] == "command_recorded"
                assert time.monotonic() - begin < 1, "Native readback must not block fresh source admission"
            elif change == "new-input":
                store.create_queued_turn(row["session_id"], client_turn_id="later-correction",
                    message="A later original correction", origin="lark")
            elif change == "unrelated-source":
                assert transport.admit("notes-app", provider.event("notes-app", "other-original-source", "/status"))["status"] == "command_recorded"
            elif change == "revoked":
                transport.bindings.disconnect(binding["binding_id"], expected_revision=transport.bindings.read()["revision"])
        finally:
            release.set()
        if change == "revoked":
            with pytest.raises(ValueError):
                recovery.result(timeout=10)
        elif change in {"unchanged", "unrelated-source"}:
            continued = recovery.result(timeout=10)
            assert continued["turn_id"] != row["turn_id"]
            assert continued["session_id"] == row["session_id"]
            runtime.wait_for_turn(session_id=row["session_id"], turn_id=continued["turn_id"], timeout_sec=10)
            assert transport.core.recover_failed_request(ref, source_observation=lambda _: pytest.fail("duplicate recovery"))["turn_id"] == continued["turn_id"]
        else:
            assert recovery.result(timeout=10)["turn_id"] == row["turn_id"]
    assert bool(transport.core.read_request(ref).get("context_recovery")) is (change in {"unchanged", "unrelated-source"})
    assert store._turn_path(row["session_id"], row["turn_id"]).read_bytes() == original
    if change not in {"unchanged", "unrelated-source"}:
        assert capture.read_bytes() == before, "Unknown or changed state cannot create or resume model work"


def test_history_page_keeps_one_original_collaboration_carrier(flow, monkeypatch):  # noqa: F811
    from loopx.capabilities.manager_context.roundtrip import project_chat_session_snapshot

    root, registry, store, create = flow
    session, turn, _ = create(brief=_history_page_brief())
    sid, tid = session["session_id"], turn["turn_id"]
    for i in range(200 - len(store.messages(sid))):
        store.append_message(sid, role="user", text=f"Independent history row {i}")
    answer = store.append_message(sid, role="agent", text="Original answer", turn_id=tid)
    load_turn, reads = store.load_turn, []
    def observed(*args):
        reads.append(args)
        return load_turn(*args)
    monkeypatch.setattr(store, "load_turn", observed)
    first = project_chat_session_snapshot(root, store, sid, registry=registry, message_limit=200)
    assert reads == [], "A page without the original carrier must not project off-page Turns"
    assert not any(row.get("collaboration") for row in first["messages"])
    store.append_message(sid, role="agent", text="Later answer outside frozen prefix", turn_id=tid)
    second = project_chat_session_snapshot(root, store, sid, registry=registry,
        message_limit=200, message_cursor=first["message_page"]["next_cursor"])
    assert len(second["messages"]) == 1 and second["message_page"]["next_cursor"] is None
    assert second["messages"][0]["message_id"] == answer["message_id"]
    assert second["messages"][0]["collaboration"]["agent_id"] == "worker"
    assert reads == [(sid, tid)]


def test_return_delivery_readback_survives_a_separate_history_page(flow):  # noqa: F811
    from loopx.capabilities.manager_context import acknowledge
    from loopx.capabilities.manager_context.roundtrip import drain, report, project_chat_session_snapshot

    root, registry, store, create = flow
    session, turn, receipt = create(brief=_history_page_brief())
    sid = session["session_id"]
    store.append_message(sid, role="agent", text="Original commitment", turn_id=turn["turn_id"])
    acknowledge(root, "research", "worker", receipt["request_id"], "adopt", "Keep original audience")
    report(root, "research", "worker", receipt["request_id"], "conclusion", "Original conclusion")
    drain(root, registry, store, lambda *_: pytest.fail("A private App return must not send to a provider"))
    expected = project_chat_session_snapshot(root, store, sid, registry=registry)["messages"]
    assert len(expected) == 4 and expected[-1].get("return_delivery")
    cursor, rows = None, []
    while True:
        page = project_chat_session_snapshot(root, store, sid, registry=registry,
            message_limit=1, message_cursor=cursor)
        rows.extend(page["messages"])
        cursor = page["message_page"]["next_cursor"]
        if cursor is None:
            break
    assert rows == expected
    assert sum(bool(row.get("collaboration")) for row in rows) == 1


def test_turn_history_matches_full_history_collaboration_and_returns(flow):  # noqa: F811
    from loopx.capabilities.manager_context import acknowledge
    from loopx.capabilities.manager_context.roundtrip import drain, report, project_chat_session_snapshot

    root, registry, store, create = flow
    session, turn, receipt = create(brief=_history_page_brief())
    sid, tid = session["session_id"], turn["turn_id"]
    store.append_message(sid, role="agent", text="Original commitment", turn_id=tid)
    acknowledge(root, "research", "worker", receipt["request_id"], "adopt", "Keep original audience")
    report(root, "research", "worker", receipt["request_id"], "conclusion", "Original conclusion")
    drain(root, registry, store, lambda *_: pytest.fail("A private App return cannot send to a provider"))
    store.append_message(sid, role="user", text="Unrelated history")
    whole = project_chat_session_snapshot(root, store, sid, registry=registry)
    expected = [row for row in whole["messages"] if row.get("turn_id") == tid]
    cursor, rows = None, []
    while True:
        page = project_chat_session_snapshot(root, store, sid, registry=registry,
            message_limit=1, message_cursor=cursor, turn_id=tid)
        rows.extend(page["messages"])
        cursor = page["message_page"]["next_cursor"]
        if cursor is None:
            break
    assert rows == expected
    assert sum(bool(row.get("collaboration")) for row in rows) == 1
    assert rows[-1].get("return_delivery"), "Scoped history retains actual return delivery readback"


def test_original_history_pages_freeze_append_boundary_and_keep_full_rows(ordinary, monkeypatch):  # noqa: F811
    from urllib.parse import urlencode
    from loopx.chat_server import ChatHTTPServer

    store, runtime, contexts, request, capture, _, _ = ordinary
    monkeypatch.setattr(ChatHTTPServer, "runtime_root", store.root.parent, raising=False)
    monkeypatch.setattr(ChatHTTPServer, "registry_path", runtime.registry_path, raising=False)
    status, opened = request("/api/chat/sessions", {
        "context_kind": "project", "project_ref": contexts.available()[0]["project_ref"],
    })
    assert status == 201
    sid = opened["session_id"]
    rows = [store.append_message(sid, role="user" if i % 2 == 0 else "agent",
        text="完整正文" * 3000 if i == 201 else f"Original message {i}",
        turn_id=None, attachments=[{"name": "original.png", "data_url": "data:image/png;base64,AA=="}] if i == 201 else None)
        for i in range(451)]
    native_before = capture.read_bytes()
    path = f"/api/chat/sessions/{sid}"
    code, first = request(path + "?message_limit=200")
    assert code == 200 and len(first["messages"]) == 200
    assert first["message_page"]["total"] == 451
    cursor = first["message_page"]["next_cursor"]
    store.append_message(sid, role="agent", text="Appended during history read")
    code, second = request(path + "?" + urlencode({"message_limit": 200, "message_cursor": cursor}))
    assert code == 200 and len(second["messages"]) == 200
    code, last = request(path + "?" + urlencode({"message_limit": 200,
        "message_cursor": second["message_page"]["next_cursor"]}))
    assert code == 200 and last["message_page"]["next_cursor"] is None
    assert first["messages"] + second["messages"] + last["messages"] == rows
    assert request(path)[1]["messages"][-1]["text"] == "Appended during history read"
    assert request(path + "?message_limit=200")[1]["message_page"]["total"] == 452
    assert capture.read_bytes() == native_before, "History reads must not execute or replay a Turn"
    for query in ["message_limit=0", "message_limit=201", "message_limit=2&message_limit=3",
                  "message_cursor=invalid", "message_limit=2&message_cursor=invalid"]:
        assert request(path + "?" + query)[0] == 400
    def unreadable(*_, **__):
        raise PermissionError("private journal path")
    with monkeypatch.context() as patch:
        patch.setattr(store, "_paged_history_messages", unreadable)
        code, unavailable = request(path + "?message_limit=200")
    assert code == 503 and "private journal path" not in json.dumps(unavailable)


def test_original_turn_history_keeps_complete_rows_and_scope_fences(ordinary, monkeypatch):  # noqa: F811
    from urllib.parse import urlencode
    from loopx.chat_server import ChatHTTPServer

    store, runtime, contexts, request, capture, _, _ = ordinary
    monkeypatch.setattr(ChatHTTPServer, "runtime_root", store.root.parent, raising=False)
    monkeypatch.setattr(ChatHTTPServer, "registry_path", runtime.registry_path, raising=False)
    _, opened = request("/api/chat/sessions", {
        "context_kind": "project", "project_ref": contexts.available()[0]["project_ref"],
    })
    sid = opened["session_id"]
    for i in range(430):
        store.append_message(sid, role="user", text=f"Unrelated history {i}")
    turn, _ = store.create_turn(sid, client_turn_id="selected-original", message="完整原始要求" * 3000, origin="web")
    tid = turn["turn_id"]
    for i in range(202):
        store.append_message(sid, role="agent", text=f"Original response {i}", turn_id=tid,
            attachments=[{"name": "original.png", "data_url": "data:image/png;base64,AA=="}] if i == 201 else None)
    store.update_turn(sid, tid, status="completed", response={"message": "Original response 201"})
    path = f"/api/chat/sessions/{sid}"
    before = capture.read_bytes()
    whole = request(path)[1]
    expected = [row for row in whole["messages"] if row.get("turn_id") == tid]
    recovered, recover = [], runtime.recover_exited_turn
    def observe_recovery(source):
        recovered.append(source)
        return recover(source)
    monkeypatch.setattr(runtime, "recover_exited_turn", observe_recovery)
    query = {"message_limit": 200, "turn_id": tid}
    code, first = request(path + "?" + urlencode(query))
    assert code == 200 and first["message_page"]["turn_id"] == tid
    assert len(first["messages"]) == 200 and first["message_page"]["total"] == len(expected) == 203
    cursor = first["message_page"]["next_cursor"]
    store.append_message(sid, role="agent", text="Outside the frozen original prefix", turn_id=tid)
    code, rest = request(path + "?" + urlencode({**query, "message_cursor": cursor}))
    assert code == 200 and rest["message_page"]["next_cursor"] is None
    assert first["messages"] + rest["messages"] == expected
    assert recovered == [], "A historical Turn read must not recover the Session's active execution"
    assert capture.read_bytes() == before, "A scoped read cannot execute or recover model work"
    assert "turn_id" not in request(path + "?message_limit=200")[1]["message_page"]
    assert recovered == [sid], "An unscoped Chat refresh keeps its ordinary recovery behavior"
    other, _ = store.create_turn(sid, client_turn_id="other-original", message="Other question", origin="web")
    store.update_turn(sid, other["turn_id"], status="completed", response={"message": "Other answer"})
    for selected in [None, other["turn_id"]]:
        wrong = {"message_limit": 200, "message_cursor": cursor, **({"turn_id": selected} if selected else {})}
        assert request(path + "?" + urlencode(wrong))[0] == 400
    for invalid in ["turn_id=missing", "message_limit=200&turn_id=missing", "message_limit=200&turn_id=..",
                    "message_limit=200&turn_id=", "message_limit=200&turn_id=a&turn_id=b"]:
        assert request(path + "?" + invalid)[0] == 400
    journal = store._session_dir(sid) / "messages.jsonl"
    original = journal.read_bytes()
    journal.write_bytes(original + b'{invalid record}\n')
    assert request(path + "?" + urlencode(query))[0] == 400
    journal.write_bytes(original)
    assert request(path + "?" + urlencode(query))[1]["message_page"]["total"] == 204


def test_app_steward_corrupt_legacy_configuration_never_falls_back(tmp_path):
    bindings = ChatConversationBindings(root=tmp_path, project_contexts=None,
        observe=lambda _: pytest.fail("an unselected entry must not probe a provider"))
    assert bindings.app_steward_target() == {"binding_id": None, "session_id": None}
    valid = bindings.read()
    for change in [{"schema_version": "unrecognized"}, {"revision": True},
                   {"bindings": None}, {"app_steward_binding_id": "invalid"}]:
        bindings.path.write_text(json.dumps({**valid, **change}))
        with pytest.raises(ValueError):
            bindings.app_steward_target()
    bindings.path.write_text(json.dumps(valid))
    before = bindings.path.read_bytes()
    assert bindings.app_steward_target() == {"binding_id": None, "session_id": None}
    assert bindings.path.read_bytes() == before
    assert bindings.select_app_steward(None, expected_revision=valid["revision"])["bindings"] == []
    assert bindings.path.read_bytes() == before


def test_app_steward_snapshot_does_not_wait_for_background_mutation(steward):  # noqa: F811
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from loopx.file_lock import exclusive_file_lock

    store, runtime, provider, transport, binding, capture, _ = steward
    event = provider.event("steward-app", "snapshot-original", "Read the workspace")
    transport.admit("steward-app", event)
    request = transport.core.read_request(identity_ref(binding["provider_ref"], event["message_id"]))
    sid = request["session_id"]
    assert runtime.wait_for_turn(session_id=sid, turn_id=request["turn_id"], timeout_sec=10)["status"] == "completed"
    bindings = transport.bindings
    bindings.select_app_steward(binding["binding_id"], expected_revision=bindings.read()["revision"])
    expected = {"binding_id": binding["binding_id"], "session_id": sid}
    assert bindings.app_steward_target() == expected
    before = capture.read_bytes(), store.messages(sid), bindings.path.read_bytes()
    held, release = threading.Event(), threading.Event()

    def background_inspection():
        with runtime.lock, exclusive_file_lock(bindings.path, operation="inspect_native_steward_turn"):
            held.set()
            assert release.wait(10)

    with ThreadPoolExecutor(max_workers=2) as workers:
        inspection = workers.submit(background_inspection)
        try:
            assert held.wait(2)
            target = workers.submit(bindings.app_steward_target)
            assert target.result(timeout=.75) == expected
            assert not release.is_set(), "Snapshot discovery must complete while inspection is blocked"
        finally:
            release.set()
            inspection.result(timeout=2)
    assert (capture.read_bytes(), store.messages(sid), bindings.path.read_bytes()) == before


@pytest.mark.parametrize("change", ["selection", "revocation"])
def test_app_steward_snapshot_rechecks_grant_after_core_discovery(steward, monkeypatch, change):  # noqa: F811
    store, runtime, provider, transport, binding, capture, _ = steward
    event = provider.event("steward-app", "snapshot-race", "Read the workspace")
    transport.admit("steward-app", event)
    request = transport.core.read_request(identity_ref(binding["provider_ref"], event["message_id"]))
    sid = request["session_id"]
    assert runtime.wait_for_turn(session_id=sid, turn_id=request["turn_id"], timeout_sec=10)["status"] == "completed"
    bindings = transport.bindings
    bindings.select_app_steward(binding["binding_id"], expected_revision=bindings.read()["revision"])
    before = capture.read_bytes(), store.messages(sid)
    core = bindings._core

    def change_after_discovery(operation, params, **kwargs):
        result = core(operation, params, **kwargs)
        if operation == "collaboration.conversation.local_sessions":
            if change == "selection":
                bindings.select_app_steward(None, expected_revision=bindings.read()["revision"])
            else:
                bindings.disconnect(binding["binding_id"], expected_revision=bindings.read()["revision"])
        return result

    monkeypatch.setattr(bindings, "_core", change_after_discovery)
    with pytest.raises(ValueError, match="changed"):
        bindings.app_steward_target()
    assert (capture.read_bytes(), store.messages(sid)) == before


def _request_for_event(transport, event):
    binding = transport._binding("notes-app")
    return transport.core.read_request(
        identity_ref(binding["provider_ref"], event["message_id"])
    )


def _wait_for_native_turn(store, session_id, turn_id):
    deadline = time.monotonic() + 10
    while True:
        turn = store.load_turn(session_id, turn_id)
        if turn["status"] == "running" and turn.get("upstream_turn_id"):
            return
        assert time.monotonic() < deadline, turn
        time.sleep(.01)


def _listener_private_admitter(transport, runtime, monkeypatch):
    """Use the shipped listener callback, without opening a provider stream."""
    import threading
    from pathlib import Path
    from loopx.extensions.lark import goal_topic_runtime
    from loopx.extensions.lark.goal_topic_runtime_service import LarkGoalTopicRuntimeService

    stop, callbacks = threading.Event(), []
    monkeypatch.setattr(Path, "home", lambda: runtime.store.root.parent / "listener-home")
    def stream(**kwargs):
        callbacks.append(kwargs["private_admitter"])
        stop.set()
        return {"status": "stopped"}
    monkeypatch.setattr(goal_topic_runtime, "stream_lark_goal_topic_profile", stream)
    service = LarkGoalTopicRuntimeService(
        # Capture one listener callback. Remove its synthetic profile when the
        # stream stops, so the real finally/refresh path cannot start replacement
        # consumers that outlive this helper and race later tests.
        snapshot_provider=lambda: {} if stop.is_set() else {"private_profiles": transport.profiles()},
        runtime_root=transport.runtime_root, runtime_controller=runtime,
        private_conversations=transport)
    service._poll_profile("notes-app", stop)
    assert len(callbacks) == 1
    return callbacks[0]


def _listener_background_stop_journey(fixture, monkeypatch, failure, *, owner=False):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from test_chat_completed_background_stop import BackgroundWire, _completed_with_later_answer
    from test_lark_private_conversations import connect, finish_group_turn
    from loopx.capabilities.native_chat.external_conversations import ChatExternalConversations
    from loopx.extensions.lark.goal_topic_runtime_service import LarkGoalTopicRuntimeService
    import loopx.extensions.lark.private_conversations as private

    if owner:
        store, runtime, provider, transport = connect(fixture)
        root = provider.event("notes-app", "background-root", "Start bounded background work")
        transport.admit("notes-app", root)
        first = finish_group_turn(runtime, transport, message=root["content"])
        sid = first["session_id"]
        wire = BackgroundWire(runtime.adapters[sid].session,
            store.load_turn(sid, first["turn_id"])["upstream_turn_id"], monkeypatch)
        later = provider.event("notes-app", "later-answer", "Keep the corrected filename")
        transport.admit("notes-app", later)
        finish_group_turn(runtime, transport, message=later["content"])
    else:
        store, runtime, provider, transport, root, first, wire = _completed_with_later_answer(fixture, monkeypatch)
    def event(name, text):
        return (provider.event("notes-app", name, text) if owner else
                provider.topic(name, text, root=root["message_id"]))
    sid = first["session_id"]
    completed = {row["turn_id"]: store.load_turn(sid, row["turn_id"])
                 for row in transport.core.pending() if row.get("turn_id")}
    admit = _listener_private_admitter(transport, runtime, monkeypatch)
    stop = event("deferred-background-stop", "/stop")
    service, worker = None, None
    entered, release, duplicate_started = threading.Event(), threading.Event(), threading.Event()
    callers = ThreadPoolExecutor(max_workers=2)
    try:
        wire.calls.clear()
        if failure == "transport-journal":
            write = private._atomic_write_json
            def interrupted(path, value):
                if path.parent == transport.root and value.get("status") == "captured" and value.get("session_id"):
                    raise OSError("transport journal interrupted after Core freeze")
                write(path, value)
            with monkeypatch.context() as patch:
                patch.setattr(private, "_atomic_write_json", interrupted)
                with pytest.raises(OSError, match="after Core freeze"):
                    admit("notes-app", stop)
        else:
            assert admit("notes-app", stop)["status"] == "command_recorded"
        frozen = next(r for r in transport.core.pending() if r["command"] == "stop")
        assert frozen["status"] == "prepared" and frozen["target_recorded"]
        assert json.loads((transport.root / f"{frozen['request_ref']}.json").read_text())["status"] == "captured"
        assert set(frozen["background_turn_ids"]) == set(completed)
        assert not wire.calls and wire.children["owned"].poll() is None
        if failure and failure != "transport-journal":
            if failure == "identity":
                provider.bot_ready = False
            else:
                wire.failure = failure
            # Existing recovery retains the exact pending request on native
            # failure. It must neither report success nor select another Turn.
            transport.reconcile()
            pending = transport.core.read_request(frozen["request_ref"])
            assert pending["status"] == "prepared"
            assert first["turn_id"] in pending["background_turn_ids"]
            assert set(pending["background_turn_ids"]) <= set(frozen["background_turn_ids"])
            assert wire.children["owned"].poll() is None
            assert not any("停止" in text for _, text in provider.writes)
            provider.bot_ready = True
        # Reopen the journal owner as after a service restart. No new queue,
        # reconstructed target or replacement model request is supplied.
        transport.core = ChatExternalConversations(runtime)
        wire.failure = None
        write = wire.session._write
        def delayed(packet):
            if packet["method"] == "thread/backgroundTerminals/list" and not release.is_set():
                # Block inside the native RPC, with its adapter lock held.
                # New admission must persist without waiting for native IO.
                entered.set()
                assert release.wait(10)
            return write(packet)
        monkeypatch.setattr(wire.session, "_write", delayed)
        service = LarkGoalTopicRuntimeService(snapshot_provider=lambda: {},
            runtime_root=transport.runtime_root, runtime_controller=runtime,
            private_conversations=transport)
        worker = threading.Thread(target=service._reconcile_private)
        worker.start()
        assert entered.wait(5)
        def duplicate():
            duplicate_started.set()
            return transport.core.admit(**{key: frozen[key] for key in
                ["binding_id", "source", "request_ref", "message", "command", "attachments", "origin"]})
        repeated = callers.submit(duplicate)
        assert duplicate_started.wait(2)
        newer_event = event("newer-during-background-stop", "wait for interrupt")
        accepted = callers.submit(admit, "notes-app", newer_event)
        assert accepted.result(timeout=5)["status"] == "durably_accepted"
        newer = _request_for_event(transport, newer_event)
        assert newer["session_id"] == sid
        assert store.load_turn(sid, newer["turn_id"])["status"] in {"queued", "starting", "running"}
        assert newer["turn_id"] not in frozen["background_turn_ids"]
        assert not release.is_set() and not repeated.done()
        assert wire.children["owned"].poll() is None
        assert not any("停止" in text for _, text in provider.writes)
        release.set()
        assert repeated.result(timeout=10)["status"] == "command_completed"
        _wait_for_native_turn(store, sid, newer["turn_id"])
        newer_native = store.load_turn(sid, newer["turn_id"])["upstream_turn_id"]
        deadline = time.monotonic() + 10
        while True:
            record = json.loads((transport.root / f"{frozen['request_ref']}.json").read_text())
            if record["status"] == "delivered":
                break
            assert time.monotonic() < deadline, record
            time.sleep(.01)
        service._closed.set()
        worker.join(10)
        assert not worker.is_alive()
        recovered = transport.core.read_request(frozen["request_ref"])
        assert recovered["status"] == "command_completed"
        assert recovered["background_turn_ids"] == [first["turn_id"]]
        assert recovered["session_id"] == sid and recovered["turn_id"] is None
        assert wire.children["owned"].poll() is not None and wire.children["unowned"].poll() is None
        assert store.load_turn(sid, newer["turn_id"])["status"] == "running"
        assert not any(method == "turn/interrupt" and params["turnId"] == newer_native
                       for method, params in wire.calls)
        assert all(store.load_turn(sid, key) == value for key, value in completed.items())
        replies = list(provider.writes)
        assert sum("停止" in text for _, text in replies) == 1
        terminal = dict(record["deliveries"]["terminal"])
        stop_reply = ("notes-app", terminal["text"])
        assert provider.writes.count(stop_reply) == 1
        if not owner:
            assert any(topic == root["message_id"] and "停止" in text for _, topic, text in provider.topic_writes)
        terminations = [call for call in wire.calls if call[0].endswith("/terminate")]
        admit("notes-app", stop)
        transport.reconcile()
        # The newly admitted Turn may emit its own first progress now. Only
        # the original stop's frozen reply/effect must remain unchanged.
        assert provider.writes.count(stop_reply) == 1
        assert json.loads((transport.root / f"{frozen['request_ref']}.json").read_text())["deliveries"]["terminal"] == terminal
        assert [call for call in wire.calls if call[0].endswith("/terminate")] == terminations
    finally:
        release.set()
        callers.shutdown(wait=True)
        if service:
            service._closed.set()
        if worker:
            worker.join(10)
        wire.close()
        runtime.close()


@pytest.mark.parametrize("failure", [None, "thread/items/list", "thread/backgroundTerminals/terminate", "transport-journal", "identity"])
def test_listener_background_stop_freezes_then_accepts_new_native_work_before_recovery(ordinary, monkeypatch, failure):  # noqa: F811
    _listener_background_stop_journey(ordinary, monkeypatch, failure)


def test_private_listener_background_stop_keeps_new_native_work_unblocked(ordinary, monkeypatch):  # noqa: F811
    _listener_background_stop_journey(ordinary, monkeypatch, None, owner=True)


def test_listener_active_stop_keeps_immediate_exact_interruption(ordinary, monkeypatch):  # noqa: F811
    from test_lark_private_conversations import connect_group
    import loopx.capabilities.native_chat.codex_background_control as background

    store, runtime, provider, transport = connect_group(ordinary)
    root = provider.topic("active-listener-stop", "wait for interrupt")
    admit = _listener_private_admitter(transport, runtime, monkeypatch)
    try:
        admit("notes-app", root)
        first = transport.core.pending()[0]
        _wait_for_native_turn(store, first["session_id"], first["turn_id"])
        monkeypatch.setattr(background, "completed_turn_ids", lambda *_: pytest.fail("active stop cannot wait on history"))
        stop = provider.topic("stop-active-listener", "/stop", root=root["message_id"])
        assert admit("notes-app", stop)["status"] == "command_recorded"
        row = next(r for r in transport.core.pending() if r["command"] == "stop")
        assert row["status"] == "command_completed" and row["turn_id"] == first["turn_id"]
        assert runtime.wait_for_turn(session_id=first["session_id"], turn_id=first["turn_id"], timeout_sec=10)["status"] == "interrupted"
    finally:
        runtime.close()


def test_binding_observation_is_opt_in_fresh_and_never_part_of_the_context(ordinary):  # noqa: F811
    store, runtime, contexts, _, _, _, _ = ordinary
    observation = {"transport_ref": "notes-app", "provider_ref": "c" * 24,
                   "operator_ref": "d" * 24, "verified": True, "bot_display_name": "First name"}
    calls = []
    def observe(profile):
        calls.append(profile)
        return observation
    bindings = ChatConversationBindings(root=store.root, project_contexts=contexts, observe=observe)
    binding = bindings.configure(transport_ref="notes-app", project_ref=contexts.available()[0]["project_ref"],
                                 executor_endpoint_id="codex")
    before = bindings.path.read_bytes()
    source = dict(binding_id=binding["binding_id"], source_ref="a" * 24,
                  sender_ref=observation["operator_ref"], private_human_message=True)
    calls.clear()
    original = bindings.resolve(**source)
    assert "observation" not in original and calls == ["notes-app"]
    selected = bindings.resolve(**source, include_observation=True)
    assert calls == ["notes-app", "notes-app"]
    assert selected["context"] == original["context"] and "observation" not in selected["context"]
    assert selected["observation"]["bot_display_name"] == "First name"
    observation["bot_display_name"] = "New name"
    changed = bindings.resolve(**source, include_observation=True)
    assert changed["observation"]["bot_display_name"] == "New name"
    assert selected["observation"]["bot_display_name"] == "First name"
    observation["verified"] = False
    with pytest.raises(ValueError, match="identity"):
        bindings.resolve(**source, include_observation=True)
    observation["verified"] = True
    observation["provider_ref"] = "e" * 24
    with pytest.raises(ValueError, match="identity"):
        bindings.resolve(**source, include_observation=True)
    assert bindings.path.read_bytes() == before and not store.list_sessions()
    runtime.close()


@pytest.mark.parametrize("group", [False, True])
@pytest.mark.parametrize("change", ["revoke", "replace", "unrelated"])
def test_binding_change_while_identity_is_observed_uses_current_grant(ordinary, monkeypatch, group, change):  # noqa: F811
    from test_lark_private_conversations import connect, connect_group
    from loopx.extensions.lark.conversation_identity import lark_private_source

    store, runtime, provider, transport = (connect_group if group else connect)(ordinary)
    bindings = transport.bindings
    binding = transport._binding("notes-app")
    event = provider.topic("grant-observation", "Read project") if group else provider.event("notes-app", "grant-observation", "Read project")
    source = lark_private_source(provider_ref=binding["provider_ref"], event=event)
    observe, armed = bindings.observe, True

    def changed(profile, **kwargs):
        nonlocal armed
        result = observe(profile, **kwargs)
        if armed:
            armed = False
            if change == "revoke":
                bindings.disconnect(binding["binding_id"], expected_revision=bindings.read()["revision"])
            else:
                target = "steward-app" if change == "unrelated" else "notes-app"
                if change == "replace":
                    provider.profile_apps[target] = "cli_replaced_app"
                bindings.configure(transport_ref=target, project_ref=binding["project_ref"],
                    executor_endpoint_id="codex", audience="group" if group else None,
                    group_refs=binding.get("group_refs"), available_group_refs=binding.get("group_refs"))
        return result

    monkeypatch.setattr(bindings, "observe", changed)
    try:
        if change == "unrelated":
            selected = bindings.resolve(binding_id=binding["binding_id"], **source)
            assert selected["binding"] == binding
        else:
            with pytest.raises(ValueError, match="binding|identity|authorized"):
                bindings.resolve(binding_id=binding["binding_id"], **source)
            assert not any(row["enabled"] and row["binding_id"] == binding["binding_id"]
                           for row in bindings.read()["bindings"])
        assert not store.list_sessions() and not provider.writes
    finally:
        runtime.close()


@pytest.mark.parametrize("revoke", [False, True])
def test_prepared_stop_recovery_waits_for_route_fence_before_its_identity_probe(ordinary, monkeypatch, revoke):  # noqa: F811
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from test_chat_completed_background_stop import _completed_with_later_answer
    from loopx.file_lock import exclusive_file_lock

    store, runtime, provider, transport, root, first, wire = _completed_with_later_answer(ordinary, monkeypatch)
    stop = provider.topic("fenced-recovery-stop", "/stop", root=root["message_id"])
    transport.admit("notes-app", stop, defer_background_stop=True)
    frozen = next(row for row in transport.core.pending() if row["command"] == "stop")
    assert frozen["status"] == "prepared" and frozen["target_recorded"]
    fence = transport.core.root / "source-fences" / f"{frozen['binding_id']}.{frozen['source']['source_ref']}.json"
    started, observed = threading.Event(), threading.Event()
    observe, finish = transport.bindings.observe, transport.core._finish_background_stop
    before_native = []

    def observing(*args, **kwargs):
        observed.set()
        return observe(*args, **kwargs)

    def finishing(*args, **kwargs):
        before_native.append(sum("auth" in call for call in provider.calls))
        return finish(*args, **kwargs)

    def recover():
        started.set()
        transport.core.recover(request_ref=frozen["request_ref"])

    monkeypatch.setattr(transport.bindings, "observe", observing)
    monkeypatch.setattr(transport.core, "_finish_background_stop", finishing)
    provider.calls.clear()
    writes_before = list(provider.writes)
    receipts = {turn_id: store.load_turn(first["session_id"], turn_id)
                for turn_id in frozen["background_turn_ids"]}
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with exclusive_file_lock(fence, operation="hold_original_source_route"):
                future = pool.submit(recover)
                assert started.wait(5)
                assert not observed.wait(.1), "A pre-fence probe cannot authorize prepared recovery"
                if revoke:
                    transport.bindings.disconnect(frozen["binding_id"], expected_revision=transport.bindings.read()["revision"])
            future.result(timeout=10)
        current = transport.core.read_request(frozen["request_ref"])
        if revoke:
            assert current["status"] == "prepared" and current["background_turn_ids"] == frozen["background_turn_ids"]
            assert not before_native and wire.children["owned"].poll() is None
            assert provider.writes == writes_before
        else:
            assert before_native == [1], "The fenced admission already owns the fresh identity proof"
            assert current["status"] == "command_completed" and current["background_turn_ids"] == [first["turn_id"]]
            assert wire.children["owned"].poll() is not None
            transport.reconcile()
            assert sum("停止" in text for _, text in provider.writes) == 1
        assert wire.children["unowned"].poll() is None
        assert all(store.load_turn(first["session_id"], turn_id) == receipt
                   for turn_id, receipt in receipts.items())
    finally:
        wire.close()
        runtime.close()


@pytest.mark.parametrize("resumed", [False, True])
def test_group_disconnect_stops_running_and_queued_turns_without_stopping_other_session(ordinary, resumed):  # noqa: F811
    from test_lark_private_conversations import connect_group

    store, runtime, provider, transport = connect_group(ordinary)
    workspace = ordinary[-1]
    root = provider.topic("root", "first request" if resumed else "wait for interrupt")
    assert transport.admit("notes-app", root)["status"] == "durably_accepted"
    first = _request_for_event(transport, root)
    if resumed:
        runtime.wait_for_turn(session_id=first["session_id"], turn_id=first["turn_id"], timeout_sec=10)
        followup = provider.topic("running", "wait for interrupt", root=root["message_id"])
        assert transport.admit("notes-app", followup)["status"] == "durably_accepted"
        active = _request_for_event(transport, followup)
        assert active["session_id"] == first["session_id"]
    else:
        active = first
    sid, tid = active["session_id"], active["turn_id"]
    _wait_for_native_turn(store, sid, tid)
    queued_event = provider.topic("queued", "must never run", root=root["message_id"])
    assert transport.admit("notes-app", queued_event)["status"] == "durably_accepted"
    queued = _request_for_event(transport, queued_event)
    assert store.load_turn(sid, queued["turn_id"])["status"] == "queued"
    peer, _ = runtime.open_session(goal_id=None, agent_id="codex", work_dir=workspace,
        objective="", mode="new", project_ref=ordinary[2].available()[0]["project_ref"])
    peer_turn, _ = runtime.enqueue_turn(session_id=peer["session_id"], client_turn_id="peer",
        message="wait for interrupt", work_dir=workspace, objective="", origin="web")
    _wait_for_native_turn(store, peer["session_id"], peer_turn["turn_id"])
    binding = transport._binding("notes-app")
    transport.bindings.disconnect(binding["binding_id"], expected_revision=transport.bindings.read()["revision"])
    assert runtime.wait_for_turn(session_id=sid, turn_id=tid, timeout_sec=10)["status"] == "interrupted"
    assert store.load_turn(sid, queued["turn_id"])["status"] == "interrupted"
    assert not store.load_turn(sid, queued["turn_id"]).get("upstream_turn_id")
    assert store.load_turn(peer["session_id"], peer_turn["turn_id"])["status"] == "running"
    transport.reconcile()
    assert provider.writes == []
    assert transport.admit("notes-app", queued_event)["status"] == "audience_rejected"
    runtime.interrupt_turn(session_id=peer["session_id"], turn_id=peer_turn["turn_id"])


def _bound_project(fixture):
    from loopx.capabilities.native_chat.external_conversations import ChatExternalConversations
    store, runtime, contexts, _, _, _, workspace = fixture
    observation = {"transport_ref": "notes-app", "provider_ref": "c" * 24,
                   "operator_ref": "d" * 24, "verified": True}
    bindings = ChatConversationBindings(root=store.root, project_contexts=contexts, observe=lambda _: observation)
    contexts.conversation_bindings = bindings
    ChatExternalConversations(runtime)
    args = dict(transport_ref="notes-app", project_ref=contexts.available()[0]["project_ref"],
                executor_endpoint_id="codex", project_grant="workspace_read")
    binding = bindings.configure(**args)
    session, _ = runtime.open_session(goal_id=None, agent_id="codex", work_dir=workspace,
        objective="", mode="resume_latest", conversation_binding_id=binding["binding_id"],
        source_context={"source_ref": "a" * 24, "sender_ref": observation["operator_ref"],
                        "private_human_message": True})
    turn, _ = runtime.enqueue_turn(session_id=session["session_id"], client_turn_id="running",
        message="wait for interrupt", work_dir=workspace, objective="", origin="lark")
    _wait_for_native_turn(store, session["session_id"], turn["turn_id"])
    return bindings, args, binding, session, turn


def test_bound_http_continuation_preserves_identity_after_json_key_reordering(ordinary):  # noqa: F811
    store, runtime, _contexts, request, capture, _fake, _workspace = ordinary
    bindings, _args, binding, session, active = _bound_project(ordinary)
    sid = session["session_id"]
    original_thread = store.load_session(sid)["upstream_thread_id"]
    runtime.interrupt_turn(session_id=sid, turn_id=active["turn_id"])
    # A JSON writer may reorder object members without changing their values.
    # Exercise the real store, HTTP discovery/admission and queued worker path.
    saved = dict(reversed(list(session["project_context"].items())))
    try:
        # Re-serialize only this idle, isolated fixture. Source context remains
        # immutable through the production Session update API.
        from loopx.chat_store import _atomic_write_json
        _atomic_write_json(store._session_path(sid), {**store.load_session(sid), "project_context": saved})
        status, discovered = request(f"/api/chat/sessions?conversation_binding_id={binding['binding_id']}")
        assert status == 200, discovered
        assert [row["session_id"] for row in discovered["sessions"]] == [sid]
        body = {"message": "Continue in the original project", "client_turn_id": "reordered-context", "delivery": "queue"}
        status, accepted = request(f"/api/chat/sessions/{sid}/turns", body)
        assert status == 202, accepted
        assert runtime.wait_for_turn(session_id=sid, turn_id=accepted["turn_id"], timeout_sec=10)["status"] == "completed"
        status, replay = request(f"/api/chat/sessions/{sid}/turns", body)
        assert status == 202 and not replay["created"] and replay["turn_id"] == accepted["turn_id"]
        current = store.load_session(sid)
        assert current["goal_id"] is None and current["upstream_thread_id"] == original_thread
        assert current["project_context"] == saved
        inputs = [row for row in store.messages(sid) if row["turn_id"] == accepted["turn_id"] and row["role"] == "user"]
        assert len(inputs) == 1 and inputs[0]["origin"] == "web"
        calls = [json.loads(line) for line in capture.read_text().splitlines()]
        assert sum(row.get("method") == "turn/start" for row in calls) == 2
        bindings.disconnect(binding["binding_id"], expected_revision=bindings.read()["revision"])
        status, _ = request(f"/api/chat/sessions/{sid}/turns", {**body, "client_turn_id": "revoked-context"})
        assert status == 400 and store.turn_for_client(sid, "revoked-context") is None
    finally:
        runtime.close()


@pytest.mark.parametrize("committed", [False, True])
def test_http_queue_retry_retains_original_managed_acceptance(ordinary, monkeypatch, committed):  # noqa: F811
    store, runtime, contexts, request, capture, _fake, _workspace = ordinary
    status, session = request("/api/chat/sessions", {
        "context_kind": "project", "project_ref": contexts.available()[0]["project_ref"]})
    assert status == 201
    sid = session["session_id"]
    thread = store.load_session(sid)["upstream_thread_id"]
    body = {"message": "Continue the original request 原文", "client_turn_id": "original-managed-input"}
    append = store.append_message

    def interrupted(*args, **kwargs):
        if kwargs.get("role") == "user":
            if committed:
                append(*args, **kwargs)
            raise OSError("managed input publication interrupted")
        return append(*args, **kwargs)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(store, "append_message", interrupted)
            status, failed = request(f"/api/chat/sessions/{sid}/turns", body)
            assert status == 503 and failed["error_code"] == "chat_turn_acceptance_unavailable"
        prepared = store.turn_for_client(sid, body["client_turn_id"])
        message_id = prepared["_acceptance"]["message_id"]
        inputs = store.messages(sid)
        for _ in range(2):
            status, replay = request(f"/api/chat/sessions/{sid}/turns", {**body, "delivery": "queue"})
            assert status == 202 and not replay["created"] and replay["turn_id"] == prepared["turn_id"]
        assert store.messages(sid) == inputs
        assert store.events_after(sid, prepared["turn_id"], None) == []
        assert request(f"/api/chat/sessions/{sid}/turns", {**body, "message": "Changed", "delivery": "queue"})[0] == 400
        status, accepted = request(f"/api/chat/sessions/{sid}/turns", body)
        assert status == 202 and not accepted["created"] and accepted["turn_id"] == prepared["turn_id"]
        result = runtime.wait_for_turn(session_id=sid, turn_id=prepared["turn_id"], timeout_sec=10)
        assert result["status"] == "completed" and "_acceptance" not in result
        assert store.load_session(sid)["upstream_thread_id"] == thread
        assert store.load_session(sid)["goal_id"] is None
        inputs = [row for row in store.messages(sid) if row["role"] == "user"]
        assert len(inputs) == 1 and inputs[0]["message_id"] == message_id and inputs[0]["text"] == body["message"]
        calls = [json.loads(line) for line in capture.read_text().splitlines()]
        assert sum(row.get("method") == "turn/start" for row in calls) == 1
    finally:
        runtime.close()


@pytest.mark.parametrize("committed", [False, True])
def test_bound_http_retry_repairs_interrupted_queue_input_once(ordinary, monkeypatch, committed):  # noqa: F811
    store, runtime, _contexts, request, capture, _fake, workspace = ordinary
    _bindings, _args, _binding, session, active = _bound_project(ordinary)
    sid = session["session_id"]
    original_thread = store.load_session(sid)["upstream_thread_id"]
    message = "Continue with the complete original input: 原文\nsecond line"
    append = store.append_message

    def interrupted(*args, **kwargs):
        if kwargs.get("role") == "user" and kwargs.get("text") == message:
            if committed:
                append(*args, **kwargs)
            raise OSError("queued input publication interrupted")
        return append(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(store, "append_message", interrupted)
        with pytest.raises(OSError, match="queued input publication interrupted"):
            runtime.enqueue_turn(session_id=sid, client_turn_id="original-input", message=message,
                                 work_dir=workspace, objective="", origin="web")
    original = store.turn_for_client(sid, "original-input")
    assert original["status"] == "queued"
    assert store.load_session(sid)["active_turn_id"] == active["turn_id"]
    body = {"message": message, "client_turn_id": "original-input", "delivery": "queue"}
    for _ in range(2):
        status, recovered = request(f"/api/chat/sessions/{sid}/turns", body)
        assert status == 202 and not recovered["created"]
        assert recovered["turn_id"] == original["turn_id"]
    inputs = [row for row in store.messages(sid) if row["turn_id"] == original["turn_id"] and row["role"] == "user"]
    assert len(inputs) == 1 and inputs[0]["text"] == message and inputs[0]["origin"] == "web"
    assert sum(row["kind"] == "turn.queued" for row in store.events_after(sid, original["turn_id"], None)) == 1
    assert store.load_session(sid)["active_turn_id"] == active["turn_id"]
    runtime.interrupt_turn(session_id=sid, turn_id=active["turn_id"])
    assert runtime.wait_for_turn(session_id=sid, turn_id=original["turn_id"], timeout_sec=10)["status"] == "completed"
    assert store.load_session(sid)["upstream_thread_id"] == original_thread
    assert store.load_session(sid)["goal_id"] is None
    calls = [json.loads(line) for line in capture.read_text().splitlines()]
    assert sum(row.get("method") == "turn/start" for row in calls) == 2
    status, replay = request(f"/api/chat/sessions/{sid}/turns", body)
    assert status == 202 and not replay["created"] and replay["turn_id"] == original["turn_id"]
    assert [row for row in store.messages(sid) if row["turn_id"] == original["turn_id"] and row["role"] == "user"] == inputs


def test_bound_queue_recovery_does_not_bypass_revoked_source(ordinary, monkeypatch):  # noqa: F811
    store, runtime, _contexts, request, _capture, _fake, workspace = ordinary
    bindings, _args, binding, session, _active = _bound_project(ordinary)
    sid = session["session_id"]
    append = store.append_message

    def interrupted(*args, **kwargs):
        if kwargs.get("role") == "user" and kwargs.get("text") == "Original queued request":
            raise OSError("queued input publication interrupted")
        return append(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(store, "append_message", interrupted)
        with pytest.raises(OSError):
            runtime.enqueue_turn(session_id=sid, client_turn_id="revoked-input", message="Original queued request",
                                 work_dir=workspace, objective="", origin="web")
    original = store.turn_for_client(sid, "revoked-input")
    bindings.disconnect(binding["binding_id"], expected_revision=bindings.read()["revision"])
    before = store.messages(sid)
    status, _ = request(f"/api/chat/sessions/{sid}/turns", {
        "message": "Original queued request", "client_turn_id": "revoked-input", "delivery": "queue"})
    assert status == 400
    assert store.messages(sid) == before
    assert store.load_turn(sid, original["turn_id"])["status"] == "interrupted"
    assert not store.load_turn(sid, original["turn_id"]).get("upstream_turn_id")


@pytest.mark.parametrize("entrypoint", ["http", "runtime_context"])
def test_bound_session_identity_is_checked_before_input_or_runtime_resolution(ordinary, monkeypatch, entrypoint):  # noqa: F811
    from loopx.capabilities.native_chat.external_conversations import ChatExternalConversations

    store, runtime, contexts, request, capture, _, workspace = ordinary
    observation = {"transport_ref": "notes-app", "provider_ref": "c" * 24,
                   "operator_ref": "d" * 24, "verified": True}
    bindings = ChatConversationBindings(root=store.root, project_contexts=contexts,
                                        observe=lambda _: observation)
    contexts.conversation_bindings = bindings
    ChatExternalConversations(runtime)
    binding = bindings.configure(transport_ref="notes-app",
        project_ref=contexts.available()[0]["project_ref"], executor_endpoint_id="codex")
    session, _ = runtime.open_session(goal_id=None, agent_id="codex", work_dir=workspace,
        objective="", mode="resume_latest", conversation_binding_id=binding["binding_id"],
        source_context={"source_ref": "a" * 24, "sender_ref": observation["operator_ref"],
                        "private_human_message": True})
    # The canonical store accepts an executor endpoint separately from its
    # agent id. A retained Session must still match the current binding.
    changed = store.create_session(goal_id=None, agent_id="codex",
        executor_endpoint_id="kiro-cli", adapter_kind="codex_app_server",
        upstream_thread_id="retained-thread", channel_id=session["channel_id"],
        project_context=session["project_context"])
    sid = changed["session_id"]
    with pytest.raises(ValueError, match="canonical bound Session identity"):
        bindings.local_sessions(binding["binding_id"])
    before_messages = store.messages(sid)
    before_requests = capture.read_text() if capture.exists() else ""
    resume = runtime.resume_session_queue
    dispatches = []
    monkeypatch.setattr(runtime, "resume_session_queue", lambda **args: dispatches.append(args))
    if entrypoint == "http":
        status, _ = request(f"/api/chat/sessions/{sid}/turns",
                            {"message": "Continue here", "client_turn_id": "wrong-recipient"})
        assert status == 400
    else:
        with pytest.raises(ValueError, match="canonical bound Session identity"):
            contexts.session_context(changed)
    assert store.turn_for_client(sid, "wrong-recipient") is None
    assert dispatches == []
    assert store.messages(sid) == before_messages
    assert (capture.read_text() if capture.exists() else "") == before_requests
    # Select the original valid recipient to recover through the same shipped
    # HTTP path. No replacement conversation or Goal registration is needed.
    monkeypatch.setattr(runtime, "resume_session_queue", resume)
    original = session["session_id"]
    body = {"message": "Continue here", "client_turn_id": "original-recipient"}
    status, accepted = request(f"/api/chat/sessions/{original}/turns", body)
    assert status == 202 and accepted["created"] is True
    assert runtime.wait_for_turn(session_id=original,
        turn_id=accepted["turn_id"], timeout_sec=10)["status"] == "completed"
    status, replay = request(f"/api/chat/sessions/{original}/turns", body)
    assert status == 202 and replay["created"] is False and replay["turn_id"] == accepted["turn_id"]
    assert store.load_session(original)["goal_id"] is None


def test_binding_reconfiguration_keeps_same_grant_running_but_stops_replaced_grant(ordinary):  # noqa: F811
    store, runtime, contexts, _, _, _, workspace = ordinary
    bindings, args, binding, session, turn = _bound_project(ordinary)
    assert bindings.configure(**args)["binding_id"] == binding["binding_id"]
    assert store.load_turn(session["session_id"], turn["turn_id"])["status"] == "running"
    with pytest.raises(ValueError, match="revision"):
        bindings.disconnect(binding["binding_id"], expected_revision=0)
    assert store.load_turn(session["session_id"], turn["turn_id"])["status"] == "running"
    contexts.workspace_grant = "workspace_write"
    replacement = bindings.configure(**{**args, "project_grant": "workspace_write"})
    assert replacement["binding_id"] != binding["binding_id"]
    assert runtime.wait_for_turn(session_id=session["session_id"], turn_id=turn["turn_id"], timeout_sec=10)["status"] == "interrupted"
    with pytest.raises(ValueError, match="no longer authorized"):
        runtime.enqueue_turn(session_id=session["session_id"], client_turn_id="stale",
            message="must not run", work_dir=workspace, objective="", origin="lark")


def test_revocation_stop_failure_keeps_grant_revoked_and_retry_confirms_stop(ordinary, monkeypatch):  # noqa: F811
    store, runtime, _, _, _, _, _ = ordinary
    bindings, _, binding, session, turn = _bound_project(ordinary)
    interrupt = runtime.interrupt_turn
    calls = []
    def unavailable(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("private transport detail")
    monkeypatch.setattr(runtime, "interrupt_turn", unavailable)
    with pytest.raises(OSError, match="revoked.*stop.*not confirmed") as error:
        bindings.disconnect(binding["binding_id"], expected_revision=bindings.read()["revision"])
    assert "private transport detail" not in str(error.value)
    assert not bindings.read()["bindings"]
    assert calls == [{"session_id": session["session_id"], "turn_id": turn["turn_id"]}]
    assert store.load_turn(session["session_id"], turn["turn_id"])["status"] == "running"
    monkeypatch.setattr(runtime, "interrupt_turn", interrupt)
    bindings.disconnect(binding["binding_id"], expected_revision=bindings.read()["revision"])
    assert runtime.wait_for_turn(session_id=session["session_id"], turn_id=turn["turn_id"], timeout_sec=10)["status"] == "interrupted"


@pytest.mark.parametrize("replacement", [False, True])
def test_http_revoked_stop_recovery_survives_refresh_and_reopen_without_restoring_grant(ordinary, monkeypatch, replacement):  # noqa: F811
    import http.client
    import threading
    from types import SimpleNamespace
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
    import loopx.chat_lark_api as api

    store, runtime, contexts, _, _, _, workspace = ordinary
    bindings, args, binding, session, turn = _bound_project(ordinary)
    peer, _ = runtime.open_session(goal_id=None, agent_id="codex", work_dir=workspace,
        objective="", mode="new", project_ref=args["project_ref"])
    peer_turn, _ = runtime.enqueue_turn(session_id=peer["session_id"], client_turn_id="peer",
        message="wait for interrupt", work_dir=workspace, objective="", origin="web")
    _wait_for_native_turn(store, peer["session_id"], peer_turn["turn_id"])
    monkeypatch.setattr(api, "build_lark_goal_topic_runtime_snapshot", lambda **_: {"goals": []})
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.chat_store, server.runtime_controller, server.verbose = store, runtime, False
    server.registry_path, server.runtime_root_override = runtime.registry_path, store.root.parent
    server.lark_private_conversations = SimpleNamespace(health=lambda: {}, close=lambda: None)
    server.lark_goal_topic_runtime = SimpleNamespace(refresh=lambda: None, health_snapshot=lambda: {}, close=lambda: None)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def request(method="GET", body=None):
        connection = http.client.HTTPConnection(*server.server_address, timeout=15)
        try:
            connection.request(method, "/api/chat/lark/private-conversations",
                None if body is None else json.dumps(body), {"Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()
    interrupt = runtime.interrupt_turn
    def unavailable(**kwargs):
        raise RuntimeError("private transport detail")
    monkeypatch.setattr(runtime, "interrupt_turn", unavailable)
    revision = bindings.read()["revision"]
    body = {"binding_id": binding["binding_id"], "revision": revision}
    connect = {"app_ref": "notes-app", "project_ref": args["project_ref"],
        "executor_endpoint_id": "codex", "project_grant": "workspace_write"}
    try:
        contexts.workspace_grant = "workspace_write"
        status, error = request("POST" if replacement else "DELETE", connect if replacement else body)
        assert status == 400 and "not confirmed" in error["error"]
        assert "private transport detail" not in json.dumps(error)
        for _ in range(2):
            status, refreshed = request()
            assert status == 200
            assert refreshed["stop_recoveries"] == [{"binding_id": binding["binding_id"],
                "project_ref": args["project_ref"], "project_title": "notes", "pending_count": 1}]
            assert not any(row["binding_id"] == binding["binding_id"] for row in refreshed["connections"])
            assert all(marker not in json.dumps(refreshed) for marker in
                ["operator_ref", "provider_ref", "workspace_path", "private transport detail", "wait for interrupt"])
        reopened = ChatConversationBindings(root=store.root, project_contexts=contexts, observe=bindings._observe_owner)
        reopened.controller = runtime
        assert reopened.stop_recoveries(reopened.read())[0]["binding_id"] == binding["binding_id"]
        assert request("DELETE", body)[0] == 400  # stale revision cannot mutate
        if replacement:
            status, repeated = request("POST", connect)
            assert status == 200 and repeated["stop_recoveries"] == refreshed["stop_recoveries"]
            assert repeated["connections"][0]["binding_id"] != binding["binding_id"]
        with pytest.raises(ValueError, match="no longer authorized"):
            runtime.enqueue_turn(session_id=session["session_id"], client_turn_id="revoked",
                message="must not run", work_dir=workspace, objective="", origin="lark")
        assert store.load_turn(session["session_id"], turn["turn_id"])["status"] == "running"
        frozen_grants = bindings.read()["bindings"]
        monkeypatch.setattr(runtime, "interrupt_turn", interrupt)
        status, recovered = request("DELETE", {**body, "revision": request()[1]["revision"]})
        assert status == 200 and recovered["stop_recoveries"] == []
        assert runtime.wait_for_turn(session_id=session["session_id"], turn_id=turn["turn_id"], timeout_sec=10)["status"] == "interrupted"
        assert store.load_turn(peer["session_id"], peer_turn["turn_id"])["status"] == "running"
        assert bindings.read()["bindings"] == frozen_grants
    finally:
        monkeypatch.setattr(runtime, "interrupt_turn", interrupt)
        runtime.interrupt_turn(session_id=peer["session_id"], turn_id=peer_turn["turn_id"])
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("external_active", [False, True])
def test_revocation_in_attached_host_drops_only_its_work_and_reports_unavailable_active_stop(ordinary, external_active):  # noqa: F811
    store, runtime, _, _, _, _, _ = ordinary
    bindings, _, binding, managed, managed_turn = _bound_project(ordinary)
    attached = store.create_session(goal_id="example-goal", agent_id="codex",
        adapter_kind="attached_host", upstream_thread_id="existing-host", session_mode="attached_host",
        host_surface="codex-app")
    sid = attached["session_id"]
    target = {"context": {"binding_id": binding["binding_id"]}, "target": {"session_id": sid}}
    # Canonical store state represents a Turn already claimed by its host;
    # this tests revocation/control IO, not broker admission or live host stop.
    active, _ = store.create_queued_turn(sid, client_turn_id="claimed", message="host work",
        origin="external" if external_active else "web", external_agent_target=target if external_active else None)
    assert store.claim_next_queued_turn(sid, host_claim_id="host-claim")["turn_id"] == active["turn_id"]
    pending, _ = store.create_queued_turn(sid, client_turn_id="external-pending",
        message="pending external work", origin="external", external_agent_target=target)
    local, _ = store.create_queued_turn(sid, client_turn_id="local-pending", message="local work", origin="web")
    if external_active:
        with pytest.raises(OSError, match="stop.*not confirmed"):
            bindings.disconnect(binding["binding_id"], expected_revision=bindings.read()["revision"])
    else:
        bindings.disconnect(binding["binding_id"], expected_revision=bindings.read()["revision"])
    assert not bindings.read()["bindings"]
    assert store.load_turn(sid, active["turn_id"])["status"] == "running"
    assert store.load_turn(sid, pending["turn_id"])["status"] == "interrupted"
    assert store.load_turn(sid, local["turn_id"])["status"] == "queued"
    recoveries = bindings.stop_recoveries(bindings.read())
    assert [row["binding_id"] for row in recoveries] == ([binding["binding_id"]] if external_active else [])
    assert runtime.wait_for_turn(session_id=managed["session_id"], turn_id=managed_turn["turn_id"], timeout_sec=10)["status"] == "interrupted"


def test_explicit_project_write_reaches_native_host_and_resume_without_goal_and_revokes_old_session(ordinary):  # noqa: F811
    from loopx.chat_runtime import ChatRuntimeController
    from loopx.chat_store import ChatSessionStore

    store, runtime, contexts, _, capture, fake, workspace = ordinary
    observations = {profile: {"transport_ref": profile, "provider_ref": provider * 24,
        "operator_ref": operator * 24, "verified": True}
        for profile, provider, operator in [("notes-app", "c", "d"), ("other-app", "e", "f")]}
    bindings = ChatConversationBindings(root=store.root, project_contexts=contexts, observe=lambda p: observations[p])
    contexts.conversation_bindings = bindings
    ref = contexts.available()[0]["project_ref"]
    def configure(profile, grant="workspace_read"):
        return bindings.configure(transport_ref=profile, project_ref=ref, executor_endpoint_id="codex", project_grant=grant)
    def open_bound(controller, row):
        return controller.open_session(goal_id=None, agent_id="codex", work_dir=workspace, objective="untrusted",
            mode="resume_latest", conversation_binding_id=row["binding_id"], source_context={
                "source_ref": "a" * 24, "sender_ref": row["operator_ref"], "private_human_message": True})[0]
    read = configure("notes-app")
    old = open_bound(runtime, read)
    other = open_bound(runtime, configure("other-app"))
    contexts.workspace_grant = "workspace_write"
    write = configure("notes-app", "workspace_write")
    assert write["binding_id"] != read["binding_id"]
    assert configure("notes-app", "workspace_write")["binding_id"] == write["binding_id"]
    with pytest.raises(ValueError, match="no longer authorized"):
        runtime.enqueue_turn(session_id=old["session_id"], client_turn_id="stale", message="edit",
            work_dir=workspace, objective="ignored", origin="lark")
    one = open_bound(runtime, write)
    assert one["session_id"] != old["session_id"] and one["goal_id"] is None
    assert one["project_context"]["grant"] == "workspace_write"
    adapter = runtime.adapters[one["session_id"]]
    assert not adapter.session.execution_mode and adapter.session.runtime_profile == "restricted"
    assert adapter.session.sandbox == "workspace-write"
    turn, _ = runtime.enqueue_turn(session_id=one["session_id"], client_turn_id="edit", message="整理笔记：更新明确指定的文件",
        work_dir=workspace, objective="ignored", origin="lark")
    assert runtime.wait_for_turn(session_id=one["session_id"], turn_id=turn["turn_id"], timeout_sec=10)["status"] == "completed"
    original = one["upstream_thread_id"]
    runtime.close()
    restarted = ChatRuntimeController(store=ChatSessionStore(store.root.parent), codex_bin=str(fake),
        project_contexts=contexts, registry_path=workspace / "no-registry.json")
    try:
        resumed = open_bound(restarted, write)
        assert resumed["session_id"] == one["session_id"] and resumed["upstream_thread_id"] == original
        requests = [json.loads(line) for line in capture.read_text().splitlines()]
        starts = [r["params"] for r in requests if r.get("method") == "thread/start"]
        assert [r["sandbox"] for r in starts] == ["read-only", "read-only", "workspace-write"]
        resumes = [r["params"] for r in requests if r.get("method") == "thread/resume"]
        assert resumes[-1]["sandbox"] == "workspace-write" and resumes[-1]["threadId"] == original
        prompts = [r["params"]["input"][0]["text"] for r in requests if r.get("method") == "turn/start"]
        assert "project assistant" in prompts[-1] and "Do not edit files" not in prompts[-1]
        assert "AGENTS.md" in prompts[-1] and "hidden Goal" in prompts[-1]
        assert other["project_context"]["grant"] == "workspace_read"
        downgraded = configure("notes-app")
        assert downgraded["binding_id"] != write["binding_id"]
        with pytest.raises(ValueError, match="no longer authorized"):
            open_bound(restarted, write)
        assert open_bound(restarted, downgraded)["session_id"] != one["session_id"]
        assert store.turn_for_client(old["session_id"], "stale") is None
        assert not (workspace / "ACTIVE_GOAL_STATE.md").exists()
    finally:
        restarted.close()


def test_two_bound_audiences_continue_independently_and_use_core_queue_and_stop(ordinary):  # noqa: F811
    store, runtime, contexts, _, _, _, workspace = ordinary
    observations = {
        "notes-app": {"transport_ref": "notes-app", "provider_ref": "c" * 24,
                      "operator_ref": "d" * 24, "verified": True},
        "other-app": {"transport_ref": "other-app", "provider_ref": "e" * 24,
                      "operator_ref": "f" * 24, "verified": True},
    }
    bindings = ChatConversationBindings(root=store.root, project_contexts=contexts,
                                        observe=lambda profile: observations[profile])
    contexts.conversation_bindings = bindings
    ref = contexts.available()[0]["project_ref"]
    first = bindings.configure(transport_ref="notes-app", project_ref=ref, executor_endpoint_id="codex")
    second = bindings.configure(transport_ref="other-app", project_ref=ref, executor_endpoint_id="codex")
    assert bindings.configure(transport_ref="notes-app", project_ref=ref,
                              executor_endpoint_id="codex")["binding_id"] == first["binding_id"]

    def open_bound(row):
        return runtime.open_session(goal_id=None, agent_id="codex", work_dir=workspace, objective="ignored",
            mode="resume_latest", conversation_binding_id=row["binding_id"], source_context={
                "source_ref": "a" * 24, "sender_ref": row["operator_ref"], "private_human_message": True})[0]

    try:
        one, two = open_bound(first), open_bound(second)
        assert one["session_id"] != two["session_id"] and one["channel_id"] != two["channel_id"]
        assert one["goal_id"] is None and two["goal_id"] is None
        assert open_bound(first)["session_id"] == one["session_id"]
        blocked, _ = runtime.enqueue_turn(session_id=one["session_id"], client_turn_id="slow", message="wait for interrupt",
            work_dir=workspace, objective="ignored", origin="lark")
        deadline = time.monotonic() + 10
        while store.load_session(one["session_id"])["active_turn_id"] != blocked["turn_id"]:
            assert time.monotonic() < deadline
            time.sleep(.01)
        started = time.monotonic()
        queued, created = runtime.enqueue_turn(session_id=one["session_id"], client_turn_id="later", message="follow-up",
            work_dir=workspace, objective="ignored", origin="lark")
        assert created and time.monotonic() - started < 3
        assert store.load_turn(one["session_id"], queued["turn_id"])["status"] == "queued"
        assert store.turn_for_client(one["session_id"], "later")["turn_id"] == queued["turn_id"]
        assert runtime.enqueue_turn(session_id=one["session_id"], client_turn_id="later", message="follow-up",
            work_dir=workspace, objective="ignored", origin="lark")[1] is False
        independent, _ = runtime.enqueue_turn(session_id=two["session_id"], client_turn_id="independent", message="other audience",
            work_dir=workspace, objective="ignored", origin="lark")
        assert runtime.wait_for_turn(session_id=two["session_id"], turn_id=independent["turn_id"], timeout_sec=10)["status"] == "completed"
        assert store.load_turn(one["session_id"], blocked["turn_id"])["status"] not in {"completed", "interrupted"}
        runtime.interrupt_turn(session_id=one["session_id"], turn_id=blocked["turn_id"])
        assert runtime.wait_for_turn(session_id=one["session_id"], turn_id=queued["turn_id"], timeout_sec=10)["status"] == "completed"
        assert store.load_turn(one["session_id"], blocked["turn_id"])["status"] == "interrupted"
        assert "other audience" not in str(store.messages(one["session_id"]))
        assert "follow-up" not in str(store.messages(two["session_id"]))
        with pytest.raises(ValueError, match="source"):
            runtime.submit_turn(session_id=one["session_id"], client_turn_id="web", message="wrong source",
                                work_dir=workspace, objective="ignored")
        bindings.disconnect(first["binding_id"], expected_revision=bindings.read()["revision"])
        with pytest.raises(ValueError, match="no longer authorized"):
            runtime.enqueue_turn(session_id=one["session_id"], client_turn_id="revoked", message="old grant",
                                 work_dir=workspace, objective="ignored", origin="lark")
        assert store.turn_for_client(one["session_id"], "revoked") is None
    finally:
        runtime.close()


def test_binding_replacement_or_identity_change_cannot_move_existing_context(tmp_path):
    from loopx.capabilities.native_chat.project_context import ChatProjectContexts
    from loopx.chat_store import ChatSessionStore

    a, b = tmp_path / "notes", tmp_path / "other"
    a.mkdir()
    b.mkdir()
    contexts = ChatProjectContexts([a, b])
    store = ChatSessionStore(tmp_path / "runtime")
    observation = {"transport_ref": "notes-app", "provider_ref": "c" * 24,
                   "operator_ref": "d" * 24, "verified": True}
    bindings = ChatConversationBindings(root=store.root, project_contexts=contexts, observe=lambda _: observation)
    old = bindings.configure(transport_ref="notes-app", project_ref=contexts.available()[0]["project_ref"], executor_endpoint_id="codex")
    saved = bindings.resolve(binding_id=old["binding_id"], source_ref="a" * 24,
                             sender_ref=observation["operator_ref"], private_human_message=True)["context"]
    observation["operator_ref"] = "f" * 24
    with pytest.raises(ValueError, match="independently verified"):
        bindings.session_context(saved)
    observation["operator_ref"] = "d" * 24
    new = bindings.configure(transport_ref="notes-app", project_ref=contexts.available()[1]["project_ref"], executor_endpoint_id="codex")
    assert new["binding_id"] != old["binding_id"]
    with pytest.raises(ValueError, match="no longer authorized"):
        bindings.session_context(saved)
    with pytest.raises(ValueError, match="revision"):
        bindings.disconnect(new["binding_id"], expected_revision=0)
    assert bindings.read()["bindings"][0]["binding_id"] == new["binding_id"]
    assert bindings.path.stat().st_mode & 0o777 == 0o600


def test_read_only_bound_project_rejects_write_scoped_executor_before_session_creation(
    ordinary, monkeypatch  # noqa: F811
):
    store, runtime, contexts, _, _, _, workspace = ordinary
    observations = {
        "notes-app": {
            "transport_ref": "notes-app",
            "provider_ref": "c" * 24,
            "operator_ref": "d" * 24,
            "verified": True,
        }
    }
    bindings = ChatConversationBindings(
        root=store.root, project_contexts=contexts, observe=lambda profile: observations[profile]
    )
    contexts.conversation_bindings = bindings
    project_ref = contexts.available()[0]["project_ref"]
    binding = bindings.configure(
        transport_ref="notes-app",
        project_ref=project_ref,
        executor_endpoint_id="kiro-cli",
        project_grant="workspace_read",
    )
    monkeypatch.setattr(
        "loopx.chat.endpoint_catalog.shutil.which",
        lambda executable: executable if executable == "kiro-cli" else None,
    )
    starts = []

    class FakeWriteScopedAdapter:
        upstream_thread_id = "must-not-be-started"

        def close_session(self):
            pass

    start_adapter = runtime._start_adapter
    runtime._start_adapter = lambda **kwargs: starts.append(kwargs) or FakeWriteScopedAdapter()
    source_context = {
        "source_ref": "a" * 24,
        "sender_ref": binding["operator_ref"],
        "private_human_message": True,
    }

    with pytest.raises(ValueError, match="read-only"):
        runtime.open_session(
            goal_id=None,
            agent_id="kiro-cli",
            work_dir=workspace,
            objective="untrusted",
            mode="resume_latest",
            conversation_binding_id=binding["binding_id"],
            source_context=source_context,
        )

    assert starts == []
    assert store.latest_session(
        goal_id=None, agent_id="kiro-cli", channel_id=f"project.{project_ref}"
    ) is None

    # Selecting an actually read-only executor still lets the bound conversation
    # start, complete a request, and replay its idempotency key.
    read_only_binding = bindings.configure(
        transport_ref="notes-app",
        project_ref=project_ref,
        executor_endpoint_id="codex",
        project_grant="workspace_read",
    )
    monkeypatch.setattr(
        "loopx.chat.endpoint_catalog.shutil.which",
        lambda executable: executable
        if executable in {"kiro-cli", runtime.codex_bin}
        else None,
    )
    runtime._start_adapter = start_adapter
    session, resumed = runtime.open_session(
        goal_id=None,
        agent_id="codex",
        work_dir=workspace,
        objective="untrusted",
        mode="resume_latest",
        conversation_binding_id=read_only_binding["binding_id"],
        source_context=source_context,
    )
    assert not resumed
    turn, created = runtime.enqueue_turn(
        session_id=session["session_id"],
        client_turn_id="read-only-bound-project-turn",
        message="summarize the workspace",
        work_dir=workspace,
        objective="untrusted",
        origin="lark",
    )
    assert created
    assert runtime.wait_for_turn(
        session_id=session["session_id"], turn_id=turn["turn_id"], timeout_sec=10
    )["status"] == "completed"
    replay, replayed = runtime.enqueue_turn(
        session_id=session["session_id"],
        client_turn_id="read-only-bound-project-turn",
        message="summarize the workspace",
        work_dir=workspace,
        objective="untrusted",
        origin="lark",
    )
    assert not replayed and replay["turn_id"] == turn["turn_id"]


def test_external_transport_preserves_native_request_identity_and_session(ordinary, monkeypatch):  # noqa: F811
    from loopx.chat_runtime import ChatRuntimeController
    from loopx.chat_store import ChatSessionStore
    from loopx.capabilities.native_chat.external_conversations import ChatExternalConversations
    store, runtime, contexts, _, capture, fake, workspace = ordinary
    observation = {"transport_ref": "external-owner", "provider_ref": "c" * 24,
                   "operator_ref": "d" * 24, "verified": True}
    bindings = ChatConversationBindings(root=store.root, project_contexts=contexts,
                                        observe=lambda _: observation)
    contexts.conversation_bindings = bindings
    binding = bindings.configure(transport_ref="external-owner", project_ref=contexts.available()[0]["project_ref"],
                                 executor_endpoint_id="codex")
    core = ChatExternalConversations(runtime)
    args = {"binding_id": binding["binding_id"], "source": {"source_ref": "a" * 24,
            "sender_ref": observation["operator_ref"], "private_human_message": True},
            "request_ref": "e" * 24, "message": "Explain the current project", "origin": "external"}
    enqueue = runtime.enqueue_turn
    def crash_after_enqueue(**kwargs):
        enqueue(**kwargs)
        raise OSError("crash before request journal settles")
    monkeypatch.setattr(runtime, "enqueue_turn", crash_after_enqueue)
    try:
        with pytest.raises(OSError, match="crash"):
            core.admit(**args)
        prepared = core.read_request(args["request_ref"])
        assert prepared["status"] == "prepared"
        original = store.turn_for_client(prepared["session_id"], "external-" + args["request_ref"])
        turn = runtime.wait_for_turn(session_id=prepared["session_id"], turn_id=original["turn_id"], timeout_sec=10)
        assert turn["status"] == "completed" and turn["origin"] == "external"
        upstream = store.load_session(prepared["session_id"])["upstream_thread_id"]
        runtime.close()
        runtime = ChatRuntimeController(store=ChatSessionStore(store.root.parent), codex_bin=str(fake),
                                       project_contexts=contexts, registry_path=runtime.registry_path)
        core = ChatExternalConversations(runtime)
        core.recover(request_ref=args["request_ref"])
        admitted = core.read_request(args["request_ref"])
        assert admitted["status"] == "accepted" and admitted["turn_id"] == original["turn_id"]
        assert store.load_session(admitted["session_id"])["upstream_thread_id"] == upstream
        assert core.admit(**args) == admitted
        with pytest.raises(ValueError, match="identity"):
            core.admit(**{**args, "origin": "lark"})
        with pytest.raises(ValueError, match="origin"):
            core.admit(**{**args, "request_ref": "f" * 24, "origin": "web"})
        with pytest.raises(ValueError, match="audience"):
            core.admit(**{**args, "request_ref": "f" * 24,
                "source": {**args["source"], "sender_ref": "b" * 24}})
        assert len(store.list_sessions()) == 1
        assert store.load_session(admitted["session_id"])["project_context"]["binding_id"] == binding["binding_id"]
        requests = [json.loads(line) for line in capture.read_text().splitlines()]
        assert len([row for row in requests if row.get("method") == "thread/start"]) == 1
        assert len([row for row in requests if row.get("method") == "turn/start"]) == 1
        assert not (workspace / "ACTIVE_GOAL_STATE.md").exists()
    finally:
        runtime.close()


def test_external_steward_reuses_machine_grant_and_original_delegation(steward, monkeypatch):  # noqa: F811
    from loopx.capabilities.manager_context import authority, deliver, pending
    from test_native_steward_runtime import apply_profile
    store, runtime, _, transport, binding, _, workspace = steward
    apply_profile(store.root.parent, "trusted_owner")
    runtime.registry_path.write_text(json.dumps({"runtime_root": str(store.root.parent), "goals": [
        {"id": "registered-work", "repo": str(workspace), "coordination": {"registered_agents": ["worker"]}}]}))
    monkeypatch.setattr(runtime, "resume_session_queue", lambda **_: None)
    args = {"binding_id": binding["binding_id"], "source": {"source_ref": "a" * 24,
            "sender_ref": binding["operator_ref"], "private_human_message": True},
            "request_ref": "e" * 24, "message": "Ask the registered worker to inspect the project", "origin": "external"}
    row = transport.core.admit(**args)
    assert row["status"] == "accepted"
    session = store.load_session(row["session_id"])
    turn = store.load_turn(row["session_id"], row["turn_id"])
    assert turn["origin"] == "external" and turn["status"] == "queued"
    assert session["manager_runtime_profile"] == "trusted_owner"
    assert session["manager_runtime_sandbox"] == "danger-full-access"
    target = {"goal_id": "registered-work", "agent_id": "worker"}
    assert authority(store.root.parent, runtime.registry_path, session, turn)["targets"] == [target]
    receipt = deliver(store.root.parent, runtime.registry_path, session=session, turn=turn, request=target)
    assert pending(store.root.parent, **target)["items"][0]["message"] == args["message"]
    assert deliver(store.root.parent, runtime.registry_path, session=session, turn=turn, request=target) == {**receipt, "replayed": True}
    assert transport.core.admit(**args)["turn_id"] == row["turn_id"]
    assert not authority(store.root.parent, runtime.registry_path, session, {**turn, "origin": "web"})["targets"]
    assert not authority(store.root.parent, runtime.registry_path, session, {**turn, "message": "changed"})["targets"]
    unbound = {**session, "steward_context": None}
    assert not authority(store.root.parent, runtime.registry_path, unbound, turn)["targets"]
    transport.bindings.disconnect(binding["binding_id"], expected_revision=transport.bindings.read()["revision"])
    with pytest.raises(ValueError, match="no longer authorized"):
        transport.core.admit(**{**args, "request_ref": "f" * 24})


def test_missing_origin_in_legacy_request_retains_lark_identity(ordinary):  # noqa: F811
    from loopx.capabilities.native_chat.external_conversations import ChatExternalConversations
    store, runtime, contexts, _, _, _, _ = ordinary
    observation = {"transport_ref": "legacy-app", "provider_ref": "c" * 24,
                   "operator_ref": "d" * 24, "verified": True}
    bindings = ChatConversationBindings(root=store.root, project_contexts=contexts, observe=lambda _: observation)
    contexts.conversation_bindings = bindings
    binding = bindings.configure(transport_ref="legacy-app", project_ref=contexts.available()[0]["project_ref"],
                                 executor_endpoint_id="codex")
    core = ChatExternalConversations(runtime)
    args = {"binding_id": binding["binding_id"], "source": {"source_ref": "a" * 24,
            "sender_ref": observation["operator_ref"], "private_human_message": True},
            "request_ref": "e" * 24, "message": "/help", "command": "help"}
    try:
        original = core.admit(**args)
        path = core.root / (args["request_ref"] + ".json")
        original.pop("origin")
        path.write_text(json.dumps(original))
        assert core.admit(**args) == original
        with pytest.raises(ValueError, match="identity"):
            core.admit(**args, origin="external")
        assert store.list_sessions() == []
    finally:
        runtime.close()


def test_external_request_listing_ignores_lock_holder_records(ordinary):  # noqa: F811
    from loopx.capabilities.native_chat.external_conversations import ChatExternalConversations
    store, runtime, contexts, _, _, _, _ = ordinary
    observation = {"transport_ref": "lock-holder-app", "provider_ref": "c" * 24,
                   "operator_ref": "d" * 24, "verified": True}
    bindings = ChatConversationBindings(root=store.root, project_contexts=contexts, observe=lambda _: observation)
    contexts.conversation_bindings = bindings
    binding = bindings.configure(transport_ref="lock-holder-app", project_ref=contexts.available()[0]["project_ref"],
                                 executor_endpoint_id="codex")
    core = ChatExternalConversations(runtime)
    args = {"binding_id": binding["binding_id"], "source": {"source_ref": "a" * 24,
            "sender_ref": observation["operator_ref"], "private_human_message": True},
            "request_ref": "e" * 24, "message": "/help", "command": "help"}
    try:
        first = core.admit(**args)
        # Older Windows releases left persistent lock metadata named like a journal.
        (core.root / (args["request_ref"] + ".json.lock.holder.json")).write_text(
            json.dumps({"schema_version": "file_lock_holder_v0", "pid": 1}))
        second = core.admit(**{**args, "request_ref": "f" * 24})
        assert second["status"] == first["status"]
        assert [row["request_ref"] for row in core.pending()] == ["e" * 24, "f" * 24]
    finally:
        runtime.close()
