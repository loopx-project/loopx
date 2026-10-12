"""Realtime transport isolation through the real routing, Inbox and reply owners."""

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import threading

import pytest

from loopx.extensions.lark import goal_topic_runtime as runtime
from loopx.extensions.lark.event_inbox import inspect_lark_event_inbox
from loopx.extensions.lark.goal_channel_contracts import read_goal_channel_binding
from loopx.extensions.lark.goal_channel_targets import read_goal_channel_targets
from loopx.extensions.lark.goal_topic_runtime_service import LarkGoalTopicRuntimeService
from test_lark_goal_topic_runtime import _reply_runner, _seed_legacy_topic


def _snapshot(tmp_path):
    targets, bindings = tmp_path / "targets.json", tmp_path / "bindings.json"
    _seed_legacy_topic(targets, bindings)
    alpha = read_goal_channel_binding(bindings)
    beta = deepcopy(alpha)
    binding = beta["bindings"].pop("goal-alpha")
    next(iter(binding["connections"].values()))["topic"]["root_message_id"] = (
        "om_topic_beta"
    )
    beta["bindings"]["goal-beta"] = binding
    return {
        "target_payload": read_goal_channel_targets(targets),
        "binding_payloads": {"goal-alpha": alpha, "goal-beta": beta},
    }


def _event(message, topic="alpha"):
    return {
        "event_id": f"evt_{message}",
        "message_id": f"om_{message}",
        "chat_id": "oc_public_fixture",
        "root_id": f"om_topic_{topic}",
        "sender_type": "user",
        "sender_id": "ou_public_owner",
        "mentions": [{"id": "cli_public_fixture"}],
        "create_time": datetime.now(UTC).isoformat(),
        "content": "@linkmacbot public synthetic question",
    }


class _Consumer:
    def __init__(self, lines):
        self.stdout = lines
        self.waited = False

    def poll(self):
        return 0

    def wait(self, timeout=None):
        self.waited = True
        return 0


class _IdleConsumer(_Consumer):
    """A live pipe stays idle until its owning listener terminates it."""

    def __init__(self):
        reader, writer = os.pipe()
        super().__init__(os.fdopen(reader, encoding="utf-8"))
        self._writer = os.fdopen(writer, "w", encoding="utf-8", buffering=1)
        self.terminated = threading.Event()

    def send(self, line):
        self._writer.write(line + "\n")

    def poll(self):
        return 0 if self.terminated.is_set() else None

    def terminate(self):
        self._writer.close()
        self.terminated.set()

    def close(self):
        self.terminate()
        self.stdout.close()


def _thread_reply_runner():
    local = threading.local()

    def run(args):
        if not hasattr(local, "state"):
            local.state = {}
        return _reply_runner(local.state)(args)

    return run


def _start(tmp_path, snapshot, lines, answer, stop=None, health=None):
    consumer = _Consumer(lines)
    options = dict(
        profile="mew",
        snapshot_provider=lambda: snapshot,
        stop=stop or threading.Event(),
        runtime_root=tmp_path / "runtime",
        answer=answer,
        process_factory=lambda _args: consumer,
        provider_runner=None,
        reply_runner=_thread_reply_runner(),
        health_sink=health.append if health is not None else None,
    )
    return consumer, options


def _processed(tmp_path):
    return sum(
        inspect_lark_event_inbox(project=tmp_path / "runtime", config_path=path)[
            "processed_count"
        ]
        for path in (tmp_path / "runtime/.loopx/config/lark-goal-topics").glob("*.json")
    )


@pytest.mark.parametrize("batch", [False, True])
def test_slow_role_does_not_block_another_role_but_its_followup_stays_ordered(
    tmp_path,
    batch,
):
    snapshot = _snapshot(tmp_path)
    active, release, beta_done, followup = [threading.Event() for _ in range(4)]
    health = []
    order = []

    def answer(route, _text):
        message = route["message_id"]
        order.append(message)
        if message == "om_alpha_first":
            active.set()
            assert release.wait(5)
        elif message == "om_alpha_followup":
            followup.set()
        else:
            beta_done.set()
        return "Public synthetic reply"

    def lines():
        yield "[event] ready event_key=im.message.receive_v1\n"
        yield json.dumps(_event("alpha_first"))
        assert active.wait(5)
        events = [_event("alpha_followup"), _event("beta_first", "beta")]
        if batch:
            yield json.dumps(events)
        else:
            yield from map(json.dumps, events)
        yield "[event] exited (reason: timeout)\n"

    consumer, options = _start(tmp_path, snapshot, lines(), answer, health=health)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(runtime.stream_lark_goal_topic_profile, **options)
        try:
            assert beta_done.wait(3), "An unrelated role waited for the slow answer"
            assert not followup.is_set()
            assert not future.done(), (
                "Consumer ownership ended before active work settled"
            )
            assert not consumer.waited
        finally:
            release.set()
        result = future.result(timeout=8)
    assert result["event_count"] == result["replied_count"] == 3
    assert result["status"] == "stream_ended"
    assert order == ["om_alpha_first", "om_beta_first", "om_alpha_followup"]
    assert _processed(tmp_path) == 3
    assert sum(update.get("event_count", 0) for update in health) == 3
    assert "public synthetic question" not in json.dumps(health)


@pytest.mark.parametrize("disable", [False, True])
def test_waiting_message_uses_fresh_binding_and_stop_never_acknowledges_it(
    tmp_path,
    disable,
):
    snapshot = _snapshot(tmp_path)
    active, release, queued = [threading.Event() for _ in range(3)]
    stop = threading.Event()
    answers = []
    health = []

    def answer(route, _text):
        answers.append(route["message_id"])
        active.set()
        assert release.wait(5)
        return "Public synthetic reply"

    def lines():
        yield "[event] ready event_key=im.message.receive_v1\n"
        yield json.dumps(_event("alpha_first"))
        assert active.wait(5)
        yield json.dumps(_event("alpha_waiting"))
        queued.set()
        assert release.wait(5)
        yield "[event] exited (reason: timeout)\n"

    _consumer, options = _start(tmp_path, snapshot, lines(), answer, stop, health)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(runtime.stream_lark_goal_topic_profile, **options)
        try:
            assert queued.wait(5)
            if disable:
                next(
                    iter(
                        snapshot["binding_payloads"]["goal-alpha"]["bindings"][
                            "goal-alpha"
                        ]["connections"].values()
                    )
                )["enabled"] = False
            else:
                stop.set()
        finally:
            release.set()
        result = future.result(timeout=8)
    assert answers == ["om_alpha_first"]
    assert result["replied_count"] == 1
    assert _processed(tmp_path) == 1
    assert not list((tmp_path / "runtime").rglob("om_alpha_waiting.json"))
    if disable:
        assert any(
            update.get("last_event_status") == "ignored"
            and update.get("last_event_reason") == "topic_mismatch"
            for update in health
        )
    else:
        assert result["status"] == "stopped"


def test_explicit_session_groups_topics_but_different_sessions_are_independent(
    tmp_path, monkeypatch
):
    snapshot = _snapshot(tmp_path)
    monkeypatch.setattr(
        runtime,
        "decide_lark_topic_event",
        lambda **kwargs: {
            "route": {
                "session_id": "session-public",
                "app_ref": "mew",
                "target_ref": "fixture",
                "topic_root_message_id": kwargs["event"]["root_id"],
            }
        },
    )
    keys = [
        runtime._profile_event_lane(
            profile="mew",
            snapshot=snapshot,
            event=_event("message", topic),
            runtime_root=tmp_path,
        )
        for topic in ("alpha", "beta")
    ]
    assert keys[0][0] != keys[1][0]
    assert keys[0][1] == keys[1][1] == "session.session-public"
    monkeypatch.setattr(
        runtime,
        "decide_lark_topic_event",
        lambda **kwargs: {
            "route": {
                "session_id": "session-new",
                "app_ref": "mew",
                "target_ref": "fixture",
                "topic_root_message_id": kwargs["event"]["root_id"],
            }
        },
    )
    updated = runtime._profile_event_lane(
        profile="mew",
        snapshot=snapshot,
        event=_event("message", "beta"),
        runtime_root=tmp_path,
    )
    assert updated[0] == keys[1][0]
    assert updated[1] != keys[1][1]


def test_inactive_profile_keeps_the_native_listener_and_dispatch_off(
    tmp_path, monkeypatch
):
    def unexpected(*_args, **_kwargs):
        raise AssertionError(
            "An inactive profile must allocate no consumer or dispatcher"
        )

    monkeypatch.setattr(runtime, "ProfileEventDispatch", unexpected)
    result = runtime.stream_lark_goal_topic_profile(
        profile="mew",
        snapshot_provider=lambda: {},
        stop=threading.Event(),
        runtime_root=tmp_path / "runtime",
        answer=unexpected,
        process_factory=unexpected,
    )
    assert result["status"] == "inactive"
    assert result["event_count"] == result["replied_count"] == 0
    assert not (tmp_path / "runtime").exists()


def test_reader_failure_drains_active_work_and_cleans_up_without_accepting_buffered_input(
    tmp_path,
):
    snapshot = _snapshot(tmp_path)
    active, release = threading.Event(), threading.Event()
    answers = []

    def answer(route, _text):
        answers.append(route["message_id"])
        active.set()
        assert release.wait(5)
        return "Public synthetic reply"

    def lines():
        yield "[event] ready event_key=im.message.receive_v1\n"
        yield json.dumps(_event("alpha_first"))
        assert active.wait(5)
        yield json.dumps(_event("alpha_waiting"))
        raise OSError("synthetic reader failure")

    consumer, options = _start(tmp_path, snapshot, lines(), answer)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(runtime.stream_lark_goal_topic_profile, **options)
        try:
            with pytest.raises(TimeoutError):
                future.result(timeout=0.1)
            assert not consumer.waited
        finally:
            release.set()
        with pytest.raises(OSError, match="synthetic reader failure"):
            future.result(timeout=8)
    assert consumer.waited
    assert answers == ["om_alpha_first"]
    assert _processed(tmp_path) == 1
    assert not list((tmp_path / "runtime").rglob("om_alpha_waiting.json"))


@pytest.mark.parametrize("active_answer", [False, True])
def test_idle_handler_failure_unblocks_reader_and_retains_active_answer_ownership(
    tmp_path, monkeypatch, active_answer
):
    snapshot = _snapshot(tmp_path)
    active, release, failed = [threading.Event() for _ in range(3)]
    health, answers = [], []
    poll = runtime.poll_lark_goal_topic_profile_once

    def failing_poll(**kwargs):
        event = json.loads(kwargs["consume_runner"]([])["stdout"])
        if event["message_id"] == "om_beta_failure":
            failed.set()
            raise OSError("synthetic handler failure")
        return poll(**kwargs)

    def answer(route, _text):
        answers.append(route["message_id"])
        active.set()
        assert release.wait(8)
        return "Public synthetic reply"

    monkeypatch.setattr(runtime, "poll_lark_goal_topic_profile_once", failing_poll)
    consumer = _IdleConsumer()
    stop = threading.Event()
    _unused, options = _start(tmp_path, snapshot, [], answer, stop, health)
    options["process_factory"] = lambda _args: consumer
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(runtime.stream_lark_goal_topic_profile, **options)
        try:
            consumer.send("[event] ready event_key=im.message.receive_v1")
            if active_answer:
                consumer.send(json.dumps(_event("alpha_first")))
                assert active.wait(5)
                consumer.send(json.dumps(_event("alpha_waiting")))
            consumer.send(json.dumps(_event("beta_failure", "beta")))
            assert failed.wait(5)
            # No subsequent line or provider disconnect may be needed to
            # expose the failure and enter the existing service retry path.
            assert consumer.terminated.wait(3), "Idle reader hid a handler failure"
            assert health[-1]["status"] == "failed"
            assert health[-1]["error_code"] == "lark_event_listener_failed"
            assert not stop.is_set(), "Failure was mistaken for a requested stop"
            if active_answer:
                assert not future.done()
                assert not consumer.waited
        finally:
            release.set()
            consumer.terminate()
        try:
            with pytest.raises(OSError, match="synthetic handler failure"):
                future.result(timeout=8)
        finally:
            consumer.close()
    assert consumer.waited
    assert answers == (["om_alpha_first"] if active_answer else [])
    assert _processed(tmp_path) == int(active_answer)
    assert not list((tmp_path / "runtime").rglob("om_alpha_waiting.json"))
    assert not list((tmp_path / "runtime").rglob("om_beta_failure.json"))
    assert health[-1]["status"] == "failed", "Late answer hid the listener failure"
    assert "synthetic handler failure" not in json.dumps(health)


def test_idle_handler_failure_reconnects_through_the_existing_service_owner(
    tmp_path, monkeypatch
):
    snapshot = _snapshot(tmp_path)
    consumer = _IdleConsumer()
    stop = threading.Event()
    attempts, health = [], []
    stream = runtime.stream_lark_goal_topic_profile
    poll = runtime.poll_lark_goal_topic_profile_once
    service = LarkGoalTopicRuntimeService(
        snapshot_provider=lambda: snapshot,
        runtime_root=tmp_path / "runtime",
        runtime_controller=object(),
    )
    # Keep the real machine/App lease in the fixture, never in the user's home.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    update_health = service._update_health

    def record_health(profile, **updates):
        update_health(profile, **updates)
        health.append(service.health_snapshot()[profile])

    def failing_poll(**kwargs):
        if not consumer.waited:
            raise OSError("synthetic handler failure")
        return poll(**kwargs)

    def owned_stream(**kwargs):
        attempts.append(len(attempts) + 1)
        if len(attempts) == 1:
            consumer.send("[event] ready event_key=im.message.receive_v1")
            consumer.send(json.dumps(_event("beta_failure", "beta")))
            return stream(**kwargs, process_factory=lambda _args: consumer)
        assert consumer.waited, "New consumer started before the old owner settled"
        replacement = _Consumer(
            [
                "[event] ready event_key=im.message.receive_v1\n",
                "[event] exited (reason: timeout)\n",
            ]
        )
        result = stream(**kwargs, process_factory=lambda _args: replacement)
        assert result["ok"] and replacement.waited
        stop.set()
        service._closed.set()
        return result

    monkeypatch.setattr(service, "_update_health", record_health)
    monkeypatch.setattr(runtime, "poll_lark_goal_topic_profile_once", failing_poll)
    monkeypatch.setattr(runtime, "stream_lark_goal_topic_profile", owned_stream)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(service._poll_profile, "mew", stop)
            try:
                future.result(timeout=6)
            finally:
                stop.set()
                service._closed.set()
                consumer.terminate()
    finally:
        consumer.close()
    assert attempts == [1, 2]
    assert any(row["status"] == "failed" for row in health)
    assert any(
        row["status"] == "retrying"
        and row["restart_count"] == 1
        and row["error_code"] == "lark_event_listener_failed"
        for row in health
    )
    assert health[-1]["status"] == "listening"
    assert health[-1]["restart_count"] == 1
    assert _processed(tmp_path) == 0
