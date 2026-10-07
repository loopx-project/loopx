"""Actual native Turn events drive mutable, source-bound Bot presentation."""
import json
import threading
import time

import pytest
from test_chat_ordinary_project import ordinary  # noqa: F401
from test_lark_private_conversations import Provider, connect
from test_lark_private_feedback import active
from test_native_steward_private import steward  # noqa: F401

from loopx.chat_codex_goal import CodexGoalDriver
from loopx.chat_store import _atomic_write_json, _read_json
from loopx.extensions.lark.private_conversations import LarkPrivateConversations, PROGRESS_EDIT_BUDGET
from loopx.extensions.lark.private_progress import ANSWER_WINDOW, project_progress
from loopx.extensions.lark.conversation_identity import observe_lark_conversation_identity


class StreamingProvider(Provider):
    def __init__(self):
        super().__init__()
        self.edits = []
        self.hide_next_edit_readback = False
        self.hidden_ref = None
        self.edit_error = None
        self.apply_rejected_edit = False

    def __call__(self, args, cwd=None, timeout=None):
        if "+messages-edit" in args:
            self.calls.append(list(args))
            profile = args[args.index("--profile") + 1]
            ref = args[args.index("--message-id") + 1]
            content = args[args.index("--content") + 1]
            text = json.loads(content)["zh_cn"]["content"][0][0]["text"]
            if "--dry-run" in args:
                return {"returncode": 0, "stdout": json.dumps({"ok": True, "api": [
                    {"body": {"msg_type": "post", "content": content}}]})}
            assert self.messages[ref]["chat_id"] == f"oc_{profile.replace('-', '_')}"
            if self.edit_error is not None and not self.apply_rejected_edit:
                if self.hide_next_edit_readback:
                    self.hide_next_edit_readback = False
                    self.hidden_ref = ref
                return {"returncode": 1, "stderr": json.dumps({"error": self.edit_error})}
            self.messages[ref]["body"] = {"content": content}
            self.edits.append((profile, ref, text))
            if self.hide_next_edit_readback:
                self.hide_next_edit_readback = False
                self.hidden_ref = ref
                # The provider applied the body but lost its acknowledgement.
                return {"returncode": 1, "stderr": "synthetic lost acknowledgement"}
            if self.edit_error is not None:
                return {"returncode": 1, "stderr": json.dumps({"error": self.edit_error})}
            return {"returncode": 0, "stdout": '{"ok":true}'}
        if "+messages-mget" in args and args[args.index("--message-ids") + 1] == self.hidden_ref:
            self.hidden_ref = None
            return {"returncode": 1, "stderr": "synthetic unavailable readback"}
        result = super().__call__(args, cwd=cwd, timeout=timeout)
        if "auth" in args:
            payload = json.loads(result["stdout"])
            payload["appId"] = self.profile_apps[args[args.index("--profile") + 1]]
            result = {**result, "stdout": json.dumps(payload)}
        if "+messages-send" in args and "--dry-run" not in args:
            ref = json.loads(result["stdout"])["data"]["message_id"]
            self.messages[ref].update(chat_id=args[args.index("--chat-id") + 1],
                                     sender={"sender_type": "app", "id": "cli_synthetic_bot"})
        return result


def streaming(fixture, first_text="第一段回答。\n"):
    store, runtime, provider, transport = connect(fixture)
    streamed = StreamingProvider()
    # Existing binding observer and source reads must use the same provider.
    streamed.__dict__.update(provider.__dict__)
    transport.bindings.observe = lambda profile: observe_lark_conversation_identity(
        profile=profile, runner=streamed, cli_bin="lark-cli")
    transport.runner = streamed
    fake = fixture[-2]
    source = fake.read_text().replace('            continue\n        response =', '''            print(json.dumps({"method": "item/started", "params": {"threadId": "durable-thread", "turnId": active_turn, "item": {"id": "read", "type": "commandExecution", "command": "cat source.txt", "commandActions": [{"type": "read", "name": "source.txt"}]}}}), flush=True)
            print(json.dumps({"method": "item/reasoning/textDelta", "params": {"threadId": "durable-thread", "turnId": active_turn, "itemId": "think", "delta": "synthetic private reasoning"}}), flush=True)
            print(json.dumps({"method": "item/agentMessage/delta", "params": {"threadId": "durable-thread", "turnId": active_turn, "delta": "第一段回答。\\n"}}), flush=True)
            continue
        response =''')
    fake.write_text(source.replace('"第一段回答。\\n"', json.dumps(first_text, ensure_ascii=False)))
    return store, runtime, streamed, transport


def start(fixture, profile="notes-app", name="stream", first_text="第一段回答。\n"):
    store, runtime, provider, transport = streaming(fixture, first_text)
    event = provider.event(profile, name, "wait for interrupt")
    transport.admit(profile, event)
    row = transport.core.pending()[0]
    active(store, row)
    deadline = time.monotonic() + 10
    while not any(e["kind"] == "answer.delta" for e in store.events_after(row["session_id"], row["turn_id"], None)):
        assert time.monotonic() < deadline
        time.sleep(.01)
    transport.reconcile()
    return store, runtime, provider, transport, row


def allow_update(transport, row):
    path = transport.root / f"{row['request_ref']}.json"
    record = _read_json(path)
    record["stream"]["last_attempt_at"] = 0
    _atomic_write_json(path, record)


def test_context_compaction_shows_only_observed_fixed_phase():
    state = {}
    start = {"event_id": "compaction-start", "kind": "agent.phase", "payload": {
        "method": "item/started", "label": "Agent 正在压缩会话上下文"}}
    finish = {"event_id": "compaction-end", "kind": "agent.phase", "payload": {
        "method": "item/completed", "label": "Agent 会话上下文压缩已结束"}}
    assert project_progress(state, [start]) == "⏳ **正在压缩会话上下文**"
    assert project_progress(state, [finish]) == "⏳ **会话上下文压缩已结束，继续处理**"
    for method in ("item/started", "item/completed", "item/reasoning/textDelta"):
        project_progress(state, [{"event_id": method, "kind": "agent.phase", "payload": {
            "method": method, "label": "private unrecognized output", "text": "private reasoning"}}])
    assert "private" not in project_progress(state, [])
    assert "answer" not in state  # Compaction completion is not a terminal answer.


def test_native_compaction_updates_original_post_without_restart(ordinary):  # noqa: F811
    store, runtime, provider, transport = connect(ordinary)
    streamed = StreamingProvider()
    streamed.__dict__.update(provider.__dict__)
    transport.runner = streamed
    fake = ordinary[-2]
    release = fake.parent / "continue-compaction"
    fake.write_text(fake.read_text().replace('            continue\n        response =', f'''            import pathlib, time
            item = {{"id": "compaction", "type": "contextCompaction"}}
            print(json.dumps({{"method": "item/started", "params": {{"threadId": "durable-thread", "turnId": active_turn, "item": item}}}}), flush=True)
            while not pathlib.Path({str(release)!r}).exists():
                time.sleep(.01)
            print(json.dumps({{"method": "item/completed", "params": {{"threadId": "durable-thread", "turnId": active_turn, "item": item}}}}), flush=True)
            continue
        response ='''))
    try:
        transport.admit("notes-app", streamed.event("notes-app", "compaction", "wait for interrupt"))
        row = transport.core.pending()[0]
        active(store, row)
        sid, tid = row["session_id"], row["turn_id"]

        def wait_for_phase(label):
            deadline = time.monotonic() + 10
            while not any(e["kind"] == "agent.phase" and e["payload"].get("label") == label
                          for e in store.events_after(sid, tid, None)):
                assert time.monotonic() < deadline
                time.sleep(.01)

        wait_for_phase("Agent 正在压缩会话上下文")
        transport.reconcile()
        assert len(streamed.writes) == 1 and "正在压缩会话上下文" in streamed.writes[0][1]
        release.touch()
        wait_for_phase("Agent 会话上下文压缩已结束")
        allow_update(transport, row)
        transport.reconcile()
        assert "压缩已结束，继续处理" in streamed.edits[-1][2]
        assert store.load_turn(sid, tid)["status"] == "running"
        runtime.adapters[sid].steer_turn("finish", store.load_turn(sid, tid)["upstream_turn_id"])
        runtime.wait_for_turn(session_id=sid, turn_id=tid, timeout_sec=10)
        transport.reconcile()
        assert streamed.edits[-1][2] == "Steered response."
        assert len(streamed.writes) == 1 and {ref for _, ref, _ in streamed.edits} == {"om_out_0"}
        requests = [json.loads(line) for line in ordinary[4].read_text().splitlines()]
        assert sum(r["method"] == "thread/start" for r in requests) == 1
        assert sum(r["method"] == "turn/start" for r in requests) == 1
    finally:
        release.touch()
        runtime.close()


def test_native_stream_visible_before_terminal_coalesces_and_replays_without_new_message(ordinary):  # noqa: F811
    store, runtime, provider, transport, row = start(ordinary)
    try:
        assert len(provider.writes) == 1
        draft = provider.writes[0][1]
        assert "第一段回答" in draft and "内容仍在生成" in draft
        assert "synthetic private reasoning" not in draft and "cat source.txt" not in draft
        assert store.load_turn(row["session_id"], row["turn_id"])["status"] == "running"
        assert any(emoji == "OnIt" for _, _, emoji in provider.reactions.values())
        sid, tid = row["session_id"], row["turn_id"]
        for _ in range(40):
            store.append_event(sid, tid, kind="answer.delta", payload={"text": "后续内容。"}, buffered=True)
        transport.reconcile()
        assert provider.edits == []  # Native chunks accumulate, edits are coalesced.
        allow_update(transport, row)
        replay = LarkPrivateConversations(controller=runtime, runtime_root=transport.runtime_root,
                                         runner=provider, cli_bin="lark-cli")
        replay.reconcile()
        assert len(provider.writes) == 1 and len(provider.edits) == 1
        assert provider.edits[-1][2].count("后续内容。") == 40
        replay.reconcile()
        assert len(provider.edits) == 1
        # Completing the real upstream Turn replaces the same post, immediately
        # bypassing the draft throttle. The durable final answer is authoritative.
        runtime.adapters[sid].steer_turn("finish", store.load_turn(sid, tid)["upstream_turn_id"])
        assert runtime.wait_for_turn(session_id=sid, turn_id=tid, timeout_sec=10)["status"] == "completed"
        assert replay.reconcile() == 1
        assert provider.edits[-1][2] == "Steered response."
        assert len(provider.writes) == 1 and all(ref == "om_out_0" for _, ref, _ in provider.edits)
        assert not any(emoji == "OnIt" for _, _, emoji in provider.reactions.values())
        assert _read_json(replay.root / f"{row['request_ref']}.json")["status"] == "delivered"
        replay.reconcile()
        requests = [json.loads(line) for line in ordinary[4].read_text().splitlines()]
        assert sum(r["method"] == "thread/start" for r in requests) == 1
        assert sum(r["method"] == "turn/start" for r in requests) == 1
    finally:
        runtime.close()


def test_lost_edit_ack_recovered_by_readback_then_exact_stop_closes_draft(ordinary):  # noqa: F811
    store, runtime, provider, transport, row = start(ordinary)
    try:
        store.append_event(row["session_id"], row["turn_id"], kind="answer.delta", payload={"text": "新片段。"})
        allow_update(transport, row)
        provider.hide_next_edit_readback = True
        transport.reconcile()
        assert len(provider.edits) == 1
        allow_update(transport, row)
        replay = LarkPrivateConversations(controller=runtime, runtime_root=transport.runtime_root,
                                         runner=provider, cli_bin="lark-cli")
        replay.reconcile()
        assert len(provider.edits) == 1  # Applied replacement is not repeated.
        replay.admit("notes-app", provider.event("notes-app", "stop-stream", "/stop"))
        assert runtime.wait_for_turn(session_id=row["session_id"], turn_id=row["turn_id"], timeout_sec=10)["status"] == "interrupted"
        provider.fail_reaction_delete = True
        replay.reconcile()
        assert provider.edits[-1][2] == "本次执行已停止。"
        count = len(provider.edits)
        provider.fail_reaction_delete = False
        replay.reconcile()
        assert len(provider.edits) == count
        assert _read_json(replay.root / f"{row['request_ref']}.json")["status"] == "delivered"
        assert "第一段回答" not in json.loads(provider.messages["om_out_0"]["body"]["content"])["zh_cn"]["content"][0][0]["text"]
    finally:
        runtime.close()


def test_literal_mentions_use_exact_wire_proof_through_update_replay_and_final(ordinary):  # noqa: F811
    fake = ordinary[-2]
    final = "完整结果：@Fixture 与 @Later 都是普通文字。"
    fake.write_text(fake.read_text().replace('"message": "Steered response.",', f'"message": {final!r},'))
    store, runtime, provider, transport, row = start(ordinary, first_text="阅读 @Fixture。\n")
    try:
        sid, tid = row["session_id"], row["turn_id"]
        path = transport.root / f"{row['request_ref']}.json"
        record = _read_json(path)
        assert "＠Fixture" in record["stream"]["confirmed_text"]
        assert record["stream"]["confirmed_text"] == record["deliveries"]["progress"]["text"]
        assert "@Fixture" in record["stream"]["answer"]  # Core/view content stays intact.
        # The first implementation persisted raw text after the sender made
        # literal mentions inert. An upgrade must recover that old proof too.
        record["stream"]["confirmed_text"] = record["stream"]["confirmed_text"].replace("＠Fixture", "@Fixture")
        _atomic_write_json(path, record)
        store.append_event(sid, tid, kind="answer.delta", payload={"text": "再读 @Later。"})
        allow_update(transport, row)
        replay = LarkPrivateConversations(controller=runtime, runtime_root=transport.runtime_root,
                                         runner=provider, cli_bin="lark-cli")
        replay.reconcile()
        assert "＠Later" in provider.edits[-1][2]
        runtime.adapters[sid].steer_turn("finish", store.load_turn(sid, tid)["upstream_turn_id"])
        runtime.wait_for_turn(session_id=sid, turn_id=tid, timeout_sec=10)
        assert replay.reconcile() == 1
        assert provider.edits[-1][2] == final.replace("@", "＠")
        assert len(provider.writes) == 1
        assert _read_json(path)["status"] == "delivered"
        replay.reconcile()
        assert len(provider.writes) == 1
    finally:
        runtime.close()


def test_full_canonical_final_replaces_bounded_draft_without_truncation(ordinary):  # noqa: F811
    final = "**完整结果**\n\n" + "保留每一段证据。\n" * 850
    fake = ordinary[-2]
    fake.write_text(fake.read_text().replace('"message": "Steered response.",', f'"message": {final!r},'))
    store, runtime, provider, transport, row = start(ordinary)
    try:
        sid, tid = row["session_id"], row["turn_id"]
        runtime.adapters[sid].steer_turn("finish", store.load_turn(sid, tid)["upstream_turn_id"])
        runtime.wait_for_turn(session_id=sid, turn_id=tid, timeout_sec=10)
        assert transport.reconcile() == 1
        assert provider.edits[-1][2] == final.strip()
        assert len(provider.edits[-1][2]) > ANSWER_WINDOW
        assert len(provider.writes) == 1
    finally:
        runtime.close()


def test_two_app_streams_and_stop_are_isolated(ordinary):  # noqa: F811
    store, runtime, provider, transport, first = start(ordinary)
    try:
        event = provider.event("steward-app", "other-stream", "wait for interrupt")
        transport.admit("steward-app", event)
        other = next(row for row in transport.core.pending() if row["request_ref"] != first["request_ref"])
        active(store, other)
        deadline = time.monotonic() + 10
        while not any(e["kind"] == "answer.delta" for e in store.events_after(other["session_id"], other["turn_id"], None)):
            assert time.monotonic() < deadline
            time.sleep(.01)
        transport.reconcile()
        assert [profile for profile, _ in provider.writes] == ["notes-app", "steward-app"]
        transport.admit("notes-app", provider.event("notes-app", "isolation-stop", "/stop"))
        runtime.wait_for_turn(session_id=first["session_id"], turn_id=first["turn_id"], timeout_sec=10)
        transport.reconcile()
        assert provider.edits[-1][:2] == ("notes-app", "om_out_0")
        assert store.load_turn(other["session_id"], other["turn_id"])["status"] == "running"
        assert any(profile == "steward-app" and emoji == "OnIt" for profile, _, emoji in provider.reactions.values())
        assert first["session_id"] != other["session_id"]
    finally:
        runtime.close()


def test_steward_commission_progress_uses_exact_first_turn_and_original_app(steward, monkeypatch):  # noqa: F811
    store, runtime, original, transport, _, _, _ = steward
    provider = StreamingProvider()
    provider.__dict__.update(original.__dict__)
    transport.runner = provider
    started, release = threading.Event(), threading.Event()

    def observe(self, emit):
        emit("turn.started", {"upstream_turn_id": "native-first"})
        emit("answer.delta", {"text": "委托首轮正在执行。"})
        started.set()
        assert release.wait(10)
        raise self.session._timeout_error("hard_timeout", "Synthetic host timeout")

    monkeypatch.setattr(CodexGoalDriver, "_observe", observe)
    event = provider.event("steward-app", "delegate-stream", "/delegate --tokens 12000 wait for interrupt")
    transport.admit("steward-app", event)
    transport.reconcile()
    proposal = transport.core.actions.store.list()[0]
    confirm = provider.event("steward-app", "confirm-stream", "/confirm " + proposal["proposal_id"])
    transport.admit("steward-app", confirm)
    transport.reconcile()
    assert started.wait(10)
    transport.reconcile()
    draft = next((ref, message) for ref, message in provider.messages.items()
                 if "委托首轮正在执行" in str(message.get("body") or {}))
    assert draft[1]["chat_id"] == "oc_steward_app"
    resources = transport.core.actions.load(proposal["proposal_id"])["receipt"]["resource_ids"]
    assert store.load_turn(resources["session_id"], resources["turn_id"])["status"] == "running"
    release.set()
    runtime.wait_for_turn(session_id=resources["session_id"], turn_id=resources["turn_id"], timeout_sec=10)
    transport.reconcile()
    assert provider.edits[-1][0:2] == ("steward-app", draft[0])
    assert "委托首轮执行超时" in provider.edits[-1][2]
    assert not any(profile == "notes-app" for profile, _ in provider.writes)


@pytest.mark.parametrize("change", ["app", "audience"])
def test_stream_rechecks_current_app_and_target_audience_before_edit(ordinary, change):  # noqa: F811
    store, runtime, provider, transport, row = start(ordinary)
    try:
        store.append_event(row["session_id"], row["turn_id"], kind="answer.delta", payload={"text": "private next chunk"})
        allow_update(transport, row)
        if change == "app":
            provider.profile_apps["notes-app"] = "cli_replaced_app"
        else:
            provider.messages["om_out_0"]["chat_id"] = "oc_other_audience"
        transport.reconcile()
        assert provider.edits == []
        assert len(provider.writes) == 1
        assert store.load_turn(row["session_id"], row["turn_id"])["status"] == "running"
    finally:
        runtime.close()


def test_progress_is_bounded_and_does_not_invent_activity_or_show_reasoning():
    state = {}
    assert project_progress(state, [{"event_id": "1", "kind": "turn.started", "payload": {}}]) is None
    assert project_progress(state, [{"event_id": "2", "kind": "agent.phase", "payload": {
        "method": "item/reasoning/textDelta", "step": {"kind": "reasoning", "detail": "private chain"}}}]) is None
    text = project_progress(state, [{"event_id": "3", "kind": "agent.phase", "payload": {
        "method": "item/started", "step": {"kind": "command", "verb": "read", "state": "running", "detail": "secret command args"}}}])
    assert "正在读取内容" in text and "secret" not in text
    text = project_progress(state, [{"event_id": "4", "kind": "answer.delta", "payload": {"text": "a" * (ANSWER_WINDOW + 50)}}])
    assert len(state["answer"]) == ANSWER_WINDOW and "最近的回答片段" in text
    assert state["cursor"] == "4"


def test_progress_edit_budget_survives_restart_and_reserves_final(ordinary):  # noqa: F811
    store, runtime, provider, transport, row = start(ordinary)
    try:
        sid, tid = row["session_id"], row["turn_id"]
        for index in range(PROGRESS_EDIT_BUDGET + 3):
            store.append_event(sid, tid, kind="answer.delta", payload={"text": f"片段 {index}。"})
            allow_update(transport, row)
            # Each update uses a new transport, as a service restart would.
            transport = LarkPrivateConversations(controller=runtime, runtime_root=transport.runtime_root,
                                                 runner=provider, cli_bin="lark-cli")
            transport.reconcile()
        assert len(provider.edits) == PROGRESS_EDIT_BUDGET
        record = _read_json(transport.root / f"{row['request_ref']}.json")
        assert record["stream"]["edit_attempts"] == PROGRESS_EDIT_BUDGET
        assert f"片段 {PROGRESS_EDIT_BUDGET + 2}。" in record["stream"]["answer"]
        runtime.adapters[sid].steer_turn("finish", store.load_turn(sid, tid)["upstream_turn_id"])
        runtime.wait_for_turn(session_id=sid, turn_id=tid, timeout_sec=10)
        assert transport.reconcile() == 1
        assert provider.edits[-1][2] == "Steered response."
        assert len(provider.writes) == 1
    finally:
        runtime.close()


@pytest.mark.parametrize("code,unavailable,applied", [(230072, False, False), (230020, False, False),
                                                    (230072, True, False), (230072, False, True)])
def test_terminal_quota_fallback_requires_definite_rejection_and_fresh_readback(ordinary, code, unavailable, applied):  # noqa: F811
    store, runtime, provider, transport, row = start(ordinary)
    try:
        sid, tid = row["session_id"], row["turn_id"]
        runtime.adapters[sid].steer_turn("finish", store.load_turn(sid, tid)["upstream_turn_id"])
        runtime.wait_for_turn(session_id=sid, turn_id=tid, timeout_sec=10)
        provider.edit_error = {"type": "api", "code": code}
        provider.hide_next_edit_readback = unavailable
        provider.apply_rejected_edit = applied
        result = transport.reconcile()
        fallback = code == 230072 and not unavailable and not applied
        assert len(provider.writes) == (2 if fallback else 1)
        assert result == int(fallback or applied)
        record = _read_json(transport.root / f"{row['request_ref']}.json")
        if fallback:
            assert record["deliveries"]["terminal"]["rejected_update"]["blocker"] == "provider_update_edit_limit"
            assert not record["stream"].get("closed")  # The exhausted draft was not edited closed.
            assert provider.writes[-1] == ("notes-app", "Steered response.")
        replay = LarkPrivateConversations(controller=runtime, runtime_root=transport.runtime_root,
                                         runner=provider, cli_bin="lark-cli")
        replay.reconcile()
        assert len(provider.writes) == (2 if fallback or unavailable else 1)
        requests = [json.loads(line) for line in ordinary[4].read_text().splitlines()]
        assert sum(r["method"] == "turn/start" for r in requests) == 1
    finally:
        runtime.close()


def test_upgrade_recovers_old_failed_terminal_edit_and_final_cleanup_without_resend(ordinary):  # noqa: F811
    store, runtime, provider, transport, row = start(ordinary)
    try:
        sid, tid = row["session_id"], row["turn_id"]
        runtime.adapters[sid].steer_turn("finish", store.load_turn(sid, tid)["upstream_turn_id"])
        runtime.wait_for_turn(session_id=sid, turn_id=tid, timeout_sec=10)
        # The old transport froze and recorded a final update, but the provider
        # rejected it. That journal must recover without rerunning the model.
        provider.edit_error = {"type": "api", "code": 230020}
        assert transport.reconcile() == 0
        path = transport.root / f"{row['request_ref']}.json"
        assert _read_json(path)["deliveries"]["terminal"]["started"]
        provider.edit_error = {"type": "api", "code": 230072}
        provider.fail_reaction_delete = True
        replay = LarkPrivateConversations(controller=runtime, runtime_root=transport.runtime_root,
                                         runner=provider, cli_bin="lark-cli")
        assert replay.reconcile() == 0
        assert len(provider.writes) == 2
        assert _read_json(path)["deliveries"]["terminal"]["attempt"]["message_ref"] == "om_out_1"
        provider.fail_reaction_delete = False
        assert replay.reconcile() == 1
        assert len(provider.writes) == 2
        assert _read_json(path)["status"] == "delivered"
    finally:
        runtime.close()


def test_ambiguous_progress_edit_counts_against_budget_before_provider_write(ordinary):  # noqa: F811
    store, runtime, provider, transport, row = start(ordinary)
    try:
        store.append_event(row["session_id"], row["turn_id"], kind="answer.delta", payload={"text": "下一段。"})
        allow_update(transport, row)
        provider.hide_next_edit_readback = True
        transport.reconcile()
        record = _read_json(transport.root / f"{row['request_ref']}.json")
        assert record["stream"]["edit_attempts"] == 1
        assert len(provider.edits) == 1
        allow_update(transport, row)
        transport.reconcile()
        record = _read_json(transport.root / f"{row['request_ref']}.json")
        assert record["stream"]["edit_attempts"] == 1  # Readback recovery spends no edit.
        assert len(provider.edits) == 1
    finally:
        runtime.close()
