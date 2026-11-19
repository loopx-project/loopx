"""Migration history belongs to the native thread, not each subsequent prompt."""

from types import SimpleNamespace
from pathlib import Path

import pytest

from loopx import chat_agent, chat_runtime
from loopx.capabilities.native_chat.codex_adapter import CodexAppServerAdapter
from test_chat_manager_model_adoption import conversation as conversation, configure
from test_native_steward_private import steward as steward


def test_source_revocation_during_history_compaction_preserves_original_audience(steward, monkeypatch):  # noqa: F811
    import threading
    from concurrent.futures import ThreadPoolExecutor

    store, runtime, provider, transport, binding, capture, workspace = steward
    transport.admit("steward-app", provider.event("steward-app", "original-question", "A first question"))
    row = transport.core.pending()[0]
    sid = row["session_id"]
    assert runtime.wait_for_turn(session_id=sid, turn_id=row["turn_id"], timeout_sec=10)["status"] == "completed"
    original = store.update_session(sid, manager_context_version=20)
    runtime.adapters.pop(sid).close_session()
    native_calls = capture.read_bytes()
    entered = threading.Event()

    def native_start(**kwargs):
        kwargs["history_check"]()
        entered.set()
        for _ in range(200):
            kwargs["history_check"]()
            threading.Event().wait(.01)
        pytest.fail("restoration ignored the revoked source")

    monkeypatch.setattr(runtime, "_start_adapter", native_start)
    with ThreadPoolExecutor(max_workers=2) as pool:
        preparing = pool.submit(runtime._ensure_adapter, original, work_dir=workspace, objective="manager")
        assert entered.wait(2)
        revoking = pool.submit(transport.bindings.disconnect, binding["binding_id"],
            expected_revision=transport.bindings.read()["revision"])
        with pytest.raises(chat_agent.CodexChatAgentError):
            preparing.result(timeout=3)
        revoking.result(timeout=3)
    current = store.load_session(sid)
    assert current["channel_id"] == original["channel_id"]
    assert current["upstream_thread_id"] == original["upstream_thread_id"]
    assert current["steward_context"] == original["steward_context"]
    assert sid not in runtime.adapters and capture.read_bytes() == native_calls


@pytest.mark.parametrize("action", ["stop", "close"])
def test_native_history_preparation_observes_user_stop_before_its_adapter_lock(conversation, monkeypatch, action):  # noqa: F811
    import threading
    from concurrent.futures import ThreadPoolExecutor

    root, store, runtime, session, _ = conversation
    sid = session["session_id"]
    before = store.messages(sid)
    configure(root, model="gpt-6.1-sol", effort="high")
    entered = threading.Event()
    turn = None
    if action == "stop":
        turn, _ = store.create_turn(sid, client_turn_id="original", message="Original question")
        session = store.update_session(sid, active_turn_id=turn["turn_id"], status="busy")

    def native_start(**kwargs):
        entered.set()
        # The native model may be silent for minutes. Its guard is polled even
        # without notifications; Stop/Close must not wait for model completion.
        for _ in range(200):
            kwargs["history_check"]()
            threading.Event().wait(.01)
        pytest.fail("user action waited behind native history preparation")

    monkeypatch.setattr(runtime, "_start_adapter", native_start)

    def restore():
        with runtime._session_adapter_lock(sid):
            return runtime._ensure_adapter_locked(session, work_dir=root, objective="manager",
                **({"accepted_turn_id": turn["turn_id"]} if turn else {}))

    with ThreadPoolExecutor(max_workers=2) as pool:
        preparing = pool.submit(restore)
        assert entered.wait(1)
        action_result = pool.submit(runtime.interrupt_turn, session_id=sid, turn_id=turn["turn_id"]) if turn else pool.submit(runtime.close_session, sid)
        with pytest.raises(chat_agent.CodexChatAgentError, match="could not be restored"):
            preparing.result(timeout=3)
        result = action_result.result(timeout=3)
    current = store.load_session(sid)
    assert current["upstream_thread_id"] == session["upstream_thread_id"]
    assert sid not in runtime.adapters
    if turn:
        assert result["status"] == "interrupted" and current["active_turn_id"] is None
        assert not store.load_turn(sid, turn["turn_id"]).get("upstream_turn_id")
        events = store.events_after(sid, turn["turn_id"], None)
        assert sum(event["kind"] == "turn.interrupt_requested" for event in events) == 1
    else:
        assert result is True and current["status"] == "closed"
        assert store.messages(sid) == before
    runtime.close()


@pytest.mark.parametrize("change", ["close", "settings", "requested-model"])
def test_history_guard_rechecks_after_native_start_before_publishing(conversation, monkeypatch, change):  # noqa: F811
    from test_chat_manager_model_adoption import Adapter
    root, store, runtime, session, _ = conversation
    sid = session["session_id"]
    configure(root, model="gpt-6.1-sol", effort="high")
    replacement = Adapter(99)

    def native_start(**kwargs):
        kwargs["history_check"]()
        if change == "close":
            store.close_managed_session(sid)
        elif change == "settings":
            configure(root, model="gpt-6-luna", effort="high")
        else:
            store.update_session(sid, requested_agent_model_id="gpt-6-luna")
        return replacement

    monkeypatch.setattr(runtime, "_start_adapter", native_start)
    with pytest.raises((ValueError, chat_agent.CodexChatAgentError)):
        runtime._ensure_adapter(session, work_dir=root, objective="manager")
    current = store.load_session(sid)
    assert current["upstream_thread_id"] == session["upstream_thread_id"]
    assert replacement.closed and sid not in runtime.adapters
    if change == "close":
        assert current["status"] == "closed"
    runtime.close()


@pytest.mark.parametrize("fault", [None, "image-view", "known-later", "settled-command", "later-settled-command",
    "later-image-view", "later-image-moved",
    "command-no-exit", "command-open", "command-invalid-clock", "command-signal", "command-timeout", "command-bool-exit",
    "command-no-output", "command-bool-clock", "command-zero-exit",
    "later-user", "later-failed-effect", "later-moving-items",
    "later-unknown", "missing-original", "duplicate-turn", "live", "failed", "background", "unknown", "live-tool",
    "declined", "duplicate", "foreign", "second-user", "no-final", "legacy-prose", "moving-turn", "moving-items"])
def test_completed_work_readback_requires_stable_correlated_settled_inventory(fault):
    from loopx.chat import CHAT_REVIEW_OPEN_TAG, CHAT_REVIEW_CLOSE_TAG
    from loopx.capabilities.native_chat.codex_adapter import completed_work_readback
    import json

    work = {"remaining": "Finish the remaining verified update", "dependency": "A temporary source outage", "effects_settled": True}
    final = "Source unavailable; work remains.\n" + CHAT_REVIEW_OPEN_TAG + json.dumps({
        "message": "", "unfinished_work": work}) + CHAT_REVIEW_CLOSE_TAG
    calls = []
    counts = {"thread/turns/list": 0, "thread/items/list": 0}
    later_reads = 0

    def items(method, params, deadline):
        nonlocal later_reads
        calls.append(method)
        counts[method] += 1
        if method == "thread/turns/list":
            turns = [{"id": "original-turn", "status": "failed" if fault == "failed" else "completed"}]
            if fault in {"known-later", "later-settled-command", "later-image-view", "later-image-moved", "later-user", "later-failed-effect", "later-moving-items", "later-unknown", "live"} or fault == "moving-turn" and counts[method] > 1:
                turns.insert(0, {"id": "later", "status": "inProgress" if fault == "live" else "completed"})
            if fault == "missing-original":
                turns = [{"id": "later", "status": "completed"}]
            if fault == "duplicate-turn":
                turns.append(turns[0])
            return iter(turns)
        if params == {"turnId": "later"}:
            later_reads += 1
            rows = [{"turnId": "later", "item": {"id": "later-user", "type": "userMessage"}},
                {"turnId": "later", "item": {"id": "later-effect", "type": "fileChange",
                    "status": "failed" if fault == "later-failed-effect" else "completed"}}]
            if fault == "later-settled-command":
                rows[1]["item"].update(type="commandExecution", status="failed", exitCode=1,
                    startedAtMs=1000, completedAtMs=1001, aggregatedOutput="Process exited with code 1.")
            if fault in {"later-image-view", "later-image-moved"}:
                rows.append({"turnId": "later", "item": {"id": "later-image", "type": "imageView",
                    "path": "/synthetic/changed.png" if fault == "later-image-moved" and later_reads > 1
                    else "/synthetic/figure.png"}})
            if fault == "later-unknown" or fault == "later-moving-items" and later_reads > 1:
                rows.append({"turnId": "later", "item": {"id": "unknown", "type": "futureEffect"}})
            return iter(rows)
        assert params == {"turnId": "original-turn"}
        effect = {"id": "effect", "type": "fileChange", "status": "completed"}
        if fault == "settled-command" or str(fault).startswith("command-"):
            effect.update(type="commandExecution", status="failed", exitCode=1,
                startedAtMs=1000, completedAtMs=1001, aggregatedOutput="Process exited with code 1.")
            if fault == "command-no-exit":
                effect.pop("exitCode")
            elif fault == "command-open":
                effect["completedAtMs"] = None
            elif fault == "command-invalid-clock":
                effect["completedAtMs"] = 999
            elif fault == "command-signal":
                effect["exitCode"] = 137
            elif fault == "command-timeout":
                effect["exitCode"] = 124
            elif fault == "command-bool-exit":
                effect["exitCode"] = True
            elif fault == "command-no-output":
                effect.pop("aggregatedOutput")
            elif fault == "command-bool-clock":
                effect["startedAtMs"] = True
            elif fault == "command-zero-exit":
                effect["exitCode"] = 0
        if fault == "unknown":
            effect["type"] = "futureEffect"
        if fault == "image-view":
            effect = {"id": "view", "type": "imageView", "path": "/synthetic/notes/figure.png"}
        if fault in {"live-tool", "declined"}:
            effect["status"] = "inProgress" if fault == "live-tool" else "declined"
        rows = [{"turnId": "original-turn", "item": {"id": "user", "type": "userMessage"}},
                {"turnId": "foreign" if fault == "foreign" else "original-turn", "item": effect},
                {"turnId": "original-turn", "item": {"id": "final", "type": "agentMessage",
                    "phase": "commentary" if fault == "no-final" else "final_answer",
                    "text": "Unfinished, retry later" if fault == "legacy-prose" else final}}]
        if fault == "duplicate":
            rows.append(rows[1])
        if fault == "second-user" or fault == "moving-items" and counts[method] > 1:
            rows.append({"turnId": "original-turn", "item": {"id": "correction", "type": "userMessage"}})
        return iter(rows)

    native = SimpleNamespace(thread_id="native", work_dir=Path("/synthetic/notes"), response_timeout_sec=.1, _control_items=items,
        _background_terminals=lambda deadline: [{"processId": "live"}] if fault == "background" else [])
    proof = completed_work_readback(native, "original-turn", known_turn_ids={"later"}
        if fault in {"known-later", "later-settled-command", "later-image-view", "later-image-moved", "later-failed-effect", "later-moving-items", "later-unknown"} else None)
    if fault in {None, "image-view", "known-later", "settled-command", "later-settled-command", "later-image-view"}:
        digest = proof.pop("native_inventory_sha256")
        assert len(digest) == 64 and set(digest) <= set("0123456789abcdef")
        assert proof == {"thread_id": "native", "turn_id": "original-turn", "completed_work": True,
                         "all_effects_settled": True, "unfinished_work": work}
        assert counts == {"thread/turns/list": 3, "thread/items/list": 4 if fault in {"known-later", "later-settled-command", "later-image-view"} else 2}
    else:
        assert proof is None
    assert set(calls) <= {"thread/turns/list", "thread/items/list"}


@pytest.mark.parametrize("fault", [None, "unknown-later", "live", "live-effect", "foreign", "duplicate",
    "moving-items", "moving-turns", "untyped", "unknown-item", "no-final"])
@pytest.mark.parametrize("allow_untyped", [False, True])
def test_completed_result_review_reads_evidence_without_proving_replay_safety(fault, allow_untyped):
    from loopx.chat import CHAT_REVIEW_OPEN_TAG, CHAT_REVIEW_CLOSE_TAG
    from loopx.capabilities.native_chat.codex_adapter import completed_result_review_readback
    import json

    work = {"remaining": "Verify the retained update", "dependency": "The source was unavailable", "effects_settled": True}
    final = "Only part of the update was confirmed.\n" + CHAT_REVIEW_OPEN_TAG + json.dumps({
        "message": "", "unfinished_work": work}) + CHAT_REVIEW_CLOSE_TAG
    calls = []
    counts = {"thread/turns/list": 0, "thread/items/list": 0}

    def items(method, params, deadline):
        calls.append(method)
        counts[method] += 1
        if method == "thread/turns/list":
            rows = [{"id": "later", "status": "inProgress" if fault == "live" else "interrupted"},
                {"id": "original", "status": "completed"}]
            if fault == "moving-turns" and counts[method] > 1:
                rows.insert(0, {"id": "foreign", "status": "completed"})
            return iter(rows)
        assert params == {"turnId": "original"}, "A result-only read need not rescan later business effects"
        rows = [{"turnId": "original", "item": {"id": "user", "type": "userMessage"}},
            {"turnId": "original", "item": {"id": "effect", "type": "commandExecution",
                "status": "inProgress" if fault == "live-effect" else "failed", "exitCode": 130}},
            {"turnId": "original", "item": {"id": "answer", "type": "agentMessage",
                "phase": "commentary" if fault == "no-final" else "final_answer",
                "text": "No structured declaration" if fault == "untyped" else final}}]
        if fault == "foreign":
            rows[1]["turnId"] = "other"
        if fault == "unknown-item":
            rows[1]["item"]["type"] = "futureEffect"
        if fault == "duplicate":
            rows.append(rows[1])
        if fault == "moving-items" and counts[method] > 1:
            rows[2]["item"]["text"] += " changed"
        return iter(rows)

    native = SimpleNamespace(thread_id="native", work_dir=Path("/synthetic/workspace"),
        response_timeout_sec=1, _control_items=items)
    proof = completed_result_review_readback(native, "original",
        known_turn_ids=set() if fault == "unknown-later" else {"later"}, allow_untyped=allow_untyped)
    if fault is None or fault == "untyped" and allow_untyped:
        assert proof["read_only_review"] is True and proof["completed_result_review"] is True
        if fault == "untyped":
            assert proof["unfinished_work"] is None and proof["untyped_result_review"] is True
            assert proof["response"]["message"] == proof["visible_response"] == "No structured declaration"
            assert not proof["response"].get("unfinished_work")
        else:
            assert proof["unfinished_work"] == work and proof["visible_response"] == final
        assert proof["thread_id"] == "native" and proof["turn_id"] == "original"
        assert len(proof["native_inventory_sha256"]) == 64
        assert "all_effects_settled" not in proof and "completed_work" not in proof
        assert counts == {"thread/turns/list": 2, "thread/items/list": 2}
    else:
        assert proof is None
    assert set(calls) <= {"thread/turns/list", "thread/items/list"}


@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("count", [2, 400])
def test_history_is_persisted_once_before_publishing_a_new_adapter(monkeypatch, tmp_path, resume, count):
    history = [
        {"role": "user", "content": "Keep this early constraint.\n  Preserve spacing."},
        {"role": "assistant", "content": "Acknowledged."},
        *[{"role": "user", "content": f"Evidence {i}: " + "x" * 1000} for i in range(count)],
        {"role": "user", "content": ""},
    ]
    calls = []
    native = SimpleNamespace(thread_id="native-thread")
    active_bytes = 0

    def request(method, params):
        nonlocal active_bytes
        if method == "thread/inject_items":
            active_bytes += sum(len(item["content"][0]["text"].encode()) for item in params["items"])
            # Model the finite next-request window independently of the batching
            # constant. An unbounded injection fails before a user Turn can run.
            assert active_bytes < 256 * 1024
        calls.append((method, params))
        return {}

    def compact():
        nonlocal active_bytes
        calls.append(("thread/compact/start", {}))
        active_bytes = 128  # The native compactor's opaque retained state.

    native._request, native.compact_history = request, compact
    monkeypatch.setattr(chat_agent.CodexChatAgentSession, "start", lambda **kwargs: native)
    adapter = CodexAppServerAdapter.start(
        codex_bin="fixture", work_dir=tmp_path, goal_id="fixture", objective="Stable objective",
        history=history, resume_thread_id="native-thread" if resume else None,
        history_check=(lambda: pytest.fail("small/resumed history must keep its existing path")) if resume or count == 2 else None,
    )
    assert adapter.session is native
    if resume:
        assert calls == []  # The existing thread already owns its history.
        return
    injected = [params for method, params in calls if method == "thread/inject_items"]
    assert all(params["threadId"] == "native-thread" for params in injected)
    assert [item for params in injected for item in params["items"]] == [{
            "type": "message", "role": item["role"],
            "content": [{"type": "output_text" if item["role"] == "assistant" else "input_text",
                         "text": item["content"]}],
        } for item in history if item["content"]]
    methods = [method for method, _ in calls]
    assert methods == ["thread/name/set", "thread/inject_items", *[
        method for _ in range(len(injected) - 1) for method in ("thread/compact/start", "thread/inject_items")]]
    assert len(injected) == 1 if count == 2 else len(injected) > 1
    assert all(method != "turn/start" for method, _ in calls)


def test_large_history_uses_observed_room_across_multiple_bounded_imports(monkeypatch, tmp_path):
    history = [{"role": "user" if index % 2 else "assistant",
                "content": f"{index:03d} " + "语🧭" * 24000} for index in range(7)]
    calls = []
    received = []
    available = 260000

    def request(method, params):
        nonlocal available
        calls.append(method)
        if method == "thread/inject_items":
            import json
            items = params["items"]
            wire_size = len(json.dumps(items, ensure_ascii=False).encode("utf-8"))
            assert wire_size < 140000
            available -= wire_size + len(items) * 8
            assert available >= 0
            received.extend(items)

    def compact():
        nonlocal available
        calls.append("compact")
        available = 350000
        return available

    native = SimpleNamespace(thread_id="native-thread", _request=request, compact_history=compact)
    monkeypatch.setattr(chat_agent.CodexChatAgentSession, "start", lambda **kwargs: native)
    CodexAppServerAdapter.start(codex_bin="fixture", work_dir=tmp_path, goal_id="fixture",
        objective="Restore original context", history=history)

    expected = "".join(row["content"] for row in history)
    assert "".join(item["content"][0]["text"] for item in received) == expected
    # The first prefix has no native usage. Once compaction reports headroom,
    # subsequent compactions are driven by remaining capacity.
    assert calls[:4] == ["thread/name/set", "thread/inject_items", "compact", "thread/inject_items"]
    assert calls.count("compact") < calls.count("thread/inject_items") - 1
    assert "turn/start" not in calls


def test_one_large_utf8_message_survives_bounded_native_restore(monkeypatch, tmp_path):
    text = ('原始约束😀\\\"\n' * 40000) + "Keep the final constraint."
    injected, calls = [], []
    native = SimpleNamespace(thread_id="native-thread", compact_history=lambda: calls.append("compact"))
    def request(method, params):
        if method == "thread/inject_items":
            import json
            assert len(json.dumps(params["items"], ensure_ascii=False).encode()) < 256 * 1024
            injected.extend(params["items"])
        calls.append(method)
    native._request = request
    monkeypatch.setattr(chat_agent.CodexChatAgentSession, "start", lambda **kwargs: native)
    CodexAppServerAdapter.start(codex_bin="fixture", work_dir=tmp_path, goal_id="fixture",
        objective="Stable objective", history=[{"role": "user", "content": text}])
    assert "".join(item["content"][0]["text"] for item in injected) == text
    assert all(item["role"] == "user" for item in injected)
    assert "compact" in calls and "turn/start" not in calls


def test_unfinished_history_compaction_never_injects_the_next_prefix(monkeypatch, tmp_path):
    calls = []
    native = SimpleNamespace(thread_id="native-thread", close=lambda: calls.append("closed"))
    native._request = lambda method, params: calls.append(method)
    def compact():
        calls.append("compact")
        raise chat_agent.CodexChatTimeoutError("unknown compaction outcome", error_code="response_timeout", gate=None)
    native.compact_history = compact
    monkeypatch.setattr(chat_agent.CodexChatAgentSession, "start", lambda **kwargs: native)
    with pytest.raises(chat_agent.CodexChatTimeoutError, match="unknown compaction outcome"):
        CodexAppServerAdapter.start(codex_bin="fixture", work_dir=tmp_path, goal_id="fixture",
            objective="Stable objective", history=[{"role": "user", "content": "x" * 500000}])
    assert calls == ["thread/name/set", "thread/inject_items", "compact", "closed"]


def test_history_prefixes_share_the_original_hard_time_budget(monkeypatch, tmp_path):
    import loopx.capabilities.native_chat.codex_adapter as provider
    clock, calls = [0.0], []
    native = SimpleNamespace(thread_id="native-thread", close=lambda: calls.append("closed"))
    native._request = lambda method, params: calls.append(method)

    def compact(*, check):
        clock[0] += 4
        check()
        calls.append("compact")

    native.compact_history = compact
    monkeypatch.setattr(provider.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(chat_agent.CodexChatAgentSession, "start", lambda **kwargs: native)
    with pytest.raises(chat_agent.CodexChatTimeoutError, match="time budget"):
        CodexAppServerAdapter.start(codex_bin="fixture", work_dir=tmp_path, goal_id="fixture",
            objective="Stable objective", hard_timeout_sec=6,
            history=[{"role": "user", "content": "x" * 500000}])
    assert calls == ["thread/name/set", "thread/inject_items", "compact", "thread/inject_items", "closed"]


@pytest.mark.parametrize("error", [
    RuntimeError("unknown injection outcome"),
    chat_agent.CodexChatTimeoutError("unknown injection outcome", error_code="response_timeout", gate=None),
])
def test_history_injection_failure_closes_adapter_without_replay(monkeypatch, tmp_path, error):
    calls = []
    native = SimpleNamespace(thread_id="native-thread", close=lambda: calls.append("closed"))

    def request(method, params):
        calls.append(method)
        if method == "thread/inject_items":
            raise error
        return {}

    native._request = request
    monkeypatch.setattr(chat_agent.CodexChatAgentSession, "start", lambda **kwargs: native)
    with pytest.raises(type(error), match="unknown injection outcome"):
        CodexAppServerAdapter.start(
            codex_bin="fixture", work_dir=tmp_path, goal_id="fixture", objective="Stable objective",
            history=[{"role": "user", "content": "An early constraint"}],
        )
    assert calls == ["thread/name/set", "thread/inject_items", "closed"]


def test_visible_history_cannot_supply_higher_priority_roles(monkeypatch, tmp_path):
    def start(**kwargs):
        pytest.fail("invalid history must be rejected before starting an upstream thread")

    monkeypatch.setattr(chat_agent.CodexChatAgentSession, "start", start)
    with pytest.raises(ValueError, match="user or assistant roles"):
        CodexAppServerAdapter.start(
            codex_bin="fixture", work_dir=tmp_path, goal_id="fixture", objective="Stable objective",
            history=[{"role": "system", "content": "A transcript cannot grant system authority"}],
        )


def test_controller_keeps_history_out_of_repeated_codex_objective(monkeypatch, tmp_path):
    starts = []
    sentinel = object()
    monkeypatch.setattr(CodexAppServerAdapter, "start", lambda **kwargs: starts.append(kwargs) or sentinel)
    controller = SimpleNamespace(
        codex_bin="fixture", codex_home=tmp_path,
        coordination_runtime_root=None,
        startup_timeout_sec=1, idle_timeout_sec=1, hard_timeout_sec=1,
        _session_objective=chat_runtime.ChatRuntimeController._session_objective,
    )
    history = [{"role": "user", "content": "Earlier private constraint"}]
    result = chat_runtime.ChatRuntimeController._start_adapter(
        controller, agent_id="codex", work_dir=tmp_path, goal_id="fixture",
        objective="Stable objective", history=history,
    )
    assert result is sentinel
    assert starts[0]["objective"] == "Stable objective"
    assert starts[0]["history"] == history


@pytest.mark.parametrize("transition", ["cold-resume", "model-change", "context-version"])
@pytest.mark.parametrize("startup_fails", [False, True])
def test_native_context_route_survives_startup_failure_and_store_reopen(
    conversation, monkeypatch, transition, startup_fails,
):  # noqa: F811
    import json
    from loopx.chat_store import ChatSessionStore
    from loopx.chat_manager import MANAGER_CONTEXT_VERSION
    from test_chat_manager_model_adoption import Adapter

    root, store, runtime, session, starts = conversation
    sid = session["session_id"]
    original_native = session["upstream_thread_id"]
    store.append_message(sid, role="user", text="Private earlier requirement")
    store.append_message(sid, role="agent", text="Private earlier answer")
    if transition == "model-change":
        configure(root, model="gpt-6.1-sol", effort="high")
    elif transition == "context-version":
        session = store.update_session(sid, manager_context_version=MANAGER_CONTEXT_VERSION - 1)
    runtime.adapters.pop(sid).close_session()
    turn, _ = store.create_turn(sid, client_turn_id="next-input", message="Current private question")
    session = store.update_session(sid, active_turn_id=turn["turn_id"], status="busy")
    before = store.messages(sid)
    observations = []

    def start(**kwargs):
        # Inspect a separately opened production store before upstream IO.
        stored = ChatSessionStore(root).load_turn(sid, turn["turn_id"])
        if stored.get("native_context_preparation"):
            observations.append(stored["native_context_preparation"])
        if startup_fails:
            raise chat_agent.CodexChatAgentError("Native startup failed", error_code="resume_failed", gate=None)
        return Adapter(len(starts))

    monkeypatch.setattr(runtime, "_start_adapter", start)
    if startup_fails:
        with pytest.raises(chat_agent.CodexChatAgentError, match="could not be restored"):
            runtime._ensure_adapter(session, work_dir=root, objective="manager")
    else:
        runtime._ensure_adapter(session, work_dir=root, objective="manager")
    assert len(observations) == 1
    route = observations[0]
    assert route["source_thread_id"] == original_native
    assert route["planned_method"] == ("thread/resume" if transition == "cold-resume" else "thread/start")
    assert route["history_message_count"] == (0 if transition == "cold-resume" else 2)
    assert route["manager_context_migration"] is (transition != "cold-resume")
    assert route["provider_context_migration"] is False
    assert route["project_context_migration"] is False
    assert route["native_checkpoint_requested"] is (transition == "cold-resume")
    assert "private" not in json.dumps(route).lower()
    assert not store.load_turn(sid, turn["turn_id"]).get("upstream_turn_id")
    assert store.messages(sid) == before
    runtime.close()


def test_healthy_adapter_reuse_does_not_record_a_native_restart(conversation):  # noqa: F811
    root, store, runtime, session, starts = conversation
    sid = session["session_id"]
    turn, _ = store.create_turn(sid, client_turn_id="next-input", message="Keep working")
    session = store.update_session(sid, active_turn_id=turn["turn_id"], status="busy")
    adapter = runtime._ensure_adapter(session, work_dir=root, objective="manager")
    assert adapter is starts[0][1] and len(starts) == 1
    assert "native_context_preparation" not in store.load_turn(sid, turn["turn_id"])
    runtime.close()


@pytest.mark.parametrize("fault", ["replace", "extra-body", "bad-method", "bad-flag", "negative-count", "started", "terminal"])
def test_native_context_observation_cannot_rewrite_prior_facts(conversation, fault):  # noqa: F811
    root, store, runtime, session, _ = conversation
    sid = session["session_id"]
    runtime.adapters.pop(sid).close_session()
    turn, _ = store.create_turn(sid, client_turn_id="original-input", message="Keep original work")
    session = store.update_session(sid, active_turn_id=turn["turn_id"], status="busy")
    runtime._ensure_adapter(session, work_dir=root, objective="manager")
    first = store.load_turn(sid, turn["turn_id"])["native_context_preparation"]
    changed = dict(first)
    if fault == "replace":
        changed["source_thread_id"] = "different-thread"
    elif fault == "extra-body":
        changed["transcript"] = "must not be captured"
    elif fault == "bad-method":
        changed["planned_method"] = "turn/start"
    elif fault == "bad-flag":
        changed["native_checkpoint_requested"] = "true"
    elif fault == "negative-count":
        changed["history_message_count"] = -1
    elif fault == "started":
        store.update_turn(sid, turn["turn_id"], upstream_turn_id="native-started")
    else:
        store.update_turn(sid, turn["turn_id"], status="failed")
    before = store.load_turn(sid, turn["turn_id"])
    with pytest.raises(ValueError, match="immutable queued-Turn observation"):
        store.update_turn(sid, turn["turn_id"], native_context_preparation=changed)
    assert store.load_turn(sid, turn["turn_id"]) == before
    assert [e["kind"] for e in store.events_after(sid, turn["turn_id"], None)] == ["turn.queued"]
    runtime.close()
