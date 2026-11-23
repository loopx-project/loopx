from __future__ import annotations

import threading
import time
from typing import Any

from loopx.extensions.lark.conversation_identity import identity_ref
from test_native_steward_private import finish, steward  # noqa: F401


def _wait_turn_running(store: Any, session_id: str, turn_id: str, timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        turn = store.load_turn(session_id, turn_id)
        if turn and turn.get("status") in {"starting", "running"} and turn.get("upstream_turn_id"):
            return
        time.sleep(0.02)
    raise AssertionError(f"Turn {turn_id} in session {session_id} did not reach running state")


def test_steward_explicit_topic_runs_concurrently_without_waiting_on_busy_lobby(steward):  # noqa: F811
    store, runtime, provider, transport, binding, _, _ = steward
    try:
        lobby_event = provider.event("steward-app", "lobby-slow", "wait for interrupt")
        assert transport.admit("steward-app", lobby_event)["status"] == "durably_accepted"
        lobby_ref = identity_ref(binding["provider_ref"], lobby_event["message_id"])
        lobby_req = transport.core.read_request(lobby_ref)
        _wait_turn_running(store, lobby_req["session_id"], lobby_req["turn_id"])

        # Explicit Feishu topic message arrives while Lobby is blocked on a long turn.
        root = provider.event("steward-app", "topic-anchor", "Earlier topic root")
        topic_event = provider.event("steward-app", "topic-question", "Quick read-only check in topic")
        topic_event.update(
            root_id=root["message_id"],
            parent_id=root["message_id"],
            thread_id=f"omt_{root['message_id']}",
        )
        provider.messages[topic_event["message_id"]].update(
            root_id=root["message_id"],
            parent_id=root["message_id"],
            thread_id=f"omt_{root['message_id']}",
        )
        assert transport.admit("steward-app", topic_event)["status"] == "durably_accepted"
        topic_ref_id = identity_ref(binding["provider_ref"], topic_event["message_id"])
        topic_req = transport.core.read_request(topic_ref_id)

        assert topic_req["session_id"] != lobby_req["session_id"]
        expected_topic_ref = identity_ref(binding["provider_ref"], topic_event["chat_id"], root["message_id"])
        assert topic_req["source"]["topic_ref"] == expected_topic_ref

        # Topic lane finishes immediately even while Lobby turn is still running!
        completed_topic = finish(runtime, topic_req)
        assert completed_topic["status"] == "completed"
        assert store.load_turn(lobby_req["session_id"], lobby_req["turn_id"])["status"] == "running"

        transport.reconcile()
        assert any(root_id == root["message_id"] for _, root_id, _ in provider.topic_writes)

        # Cleanly stop the busy Lobby turn.
        stop_lobby = provider.event("steward-app", "stop-lobby", "/stop")
        assert transport.admit("steward-app", stop_lobby)["status"] == "command_recorded"
        assert finish(runtime, lobby_req)["status"] == "interrupted"
    finally:
        runtime.close()


def test_busy_lobby_auto_forks_new_topic_lane_and_preserves_followup_continuity(steward):  # noqa: F811
    store, runtime, provider, transport, binding, _, _ = steward
    try:
        # Select App steward so we can verify Desktop App stays pinned to Lobby.
        transport.bindings.select_app_steward(
            binding["binding_id"], expected_revision=transport.bindings.read()["revision"]
        )

        # 1. Start a long-running turn in Lobby.
        lobby_event = provider.event("steward-app", "lobby-busy", "wait for interrupt")
        assert transport.admit("steward-app", lobby_event)["status"] == "durably_accepted"
        lobby_req = transport.core.read_request(identity_ref(binding["provider_ref"], lobby_event["message_id"]))
        _wait_turn_running(store, lobby_req["session_id"], lobby_req["turn_id"])

        # 2. Send a top-level independent message while Lobby is busy -> auto-forks new Topic Lane!
        top_followup = provider.event("steward-app", "auto-fork-msg", "Independent question while lobby busy")
        assert transport.admit("steward-app", top_followup)["status"] == "durably_accepted"
        fork_ref = identity_ref(binding["provider_ref"], top_followup["message_id"])
        fork_req = transport.core.read_request(fork_ref)
        fork_delivery = transport.root / f"{fork_ref}.json"

        assert fork_req["session_id"] != lobby_req["session_id"]
        expected_lane = identity_ref(binding["provider_ref"], top_followup["chat_id"], top_followup["message_id"])
        assert fork_req["source"]["topic_ref"] == expected_lane
        assert finish(runtime, fork_req)["status"] == "completed"

        # Reconcile delivers the reply in a new Feishu thread rooted at top_followup["message_id"]!
        assert transport.reconcile_request(fork_delivery) == 1
        assert any(
            root_id == top_followup["message_id"]
            for _, root_id, _ in provider.topic_writes
        )

        # 3. User replies inside that newly created Feishu thread -> hits the EXACT SAME child session!
        in_thread = provider.event("steward-app", "thread-continuation", "Follow-up inside the forked thread")
        in_thread.update(
            root_id=top_followup["message_id"],
            parent_id=top_followup["message_id"],
            thread_id=f"omt_{top_followup['message_id']}",
        )
        provider.messages[in_thread["message_id"]].update(
            root_id=top_followup["message_id"],
            parent_id=top_followup["message_id"],
            thread_id=f"omt_{top_followup['message_id']}",
        )
        assert transport.admit("steward-app", in_thread)["status"] == "durably_accepted"
        cont_req = transport.core.read_request(identity_ref(binding["provider_ref"], in_thread["message_id"]))
        assert cont_req["session_id"] == fork_req["session_id"]
        assert finish(runtime, cont_req)["status"] == "completed"

        # 4. Desktop App steward target still points to primary Lobby session, never hijacked by child lane!
        assert transport.bindings.app_steward_session()["session_id"] == lobby_req["session_id"]

        # Stop Lobby cleanly.
        transport.admit("steward-app", provider.event("steward-app", "stop-lobby-2", "/stop"))
        assert finish(runtime, lobby_req)["status"] == "interrupted"
    finally:
        runtime.close()


def test_steward_topic_lanes_enforce_bounded_concurrency_limit(steward, monkeypatch):  # noqa: F811
    store, runtime, provider, transport, binding, _, _ = steward
    runtime.max_steward_concurrent_lanes = 2

    active_lock = threading.Lock()
    active_count = 0
    peak_active = 0
    two_running = threading.Event()
    release_turns = threading.Event()
    original_run_turn = runtime._run_turn

    def tracked_run_turn(**kwargs: Any) -> Any:
        nonlocal active_count, peak_active
        with active_lock:
            active_count += 1
            peak_active = max(peak_active, active_count)
            if active_count >= 2:
                two_running.set()
        try:
            release_turns.wait(timeout=8.0)
            return original_run_turn(**kwargs)
        finally:
            with active_lock:
                active_count -= 1

    monkeypatch.setattr(runtime, "_run_turn", tracked_run_turn)
    try:
        requests = []
        for idx in range(4):
            root = provider.event("steward-app", f"root-{idx}", f"Topic root {idx}")
            ev = provider.event("steward-app", f"msg-{idx}", f"Parallel topic message {idx}")
            ev.update(
                root_id=root["message_id"],
                parent_id=root["message_id"],
                thread_id=f"omt_{root['message_id']}",
            )
            provider.messages[ev["message_id"]].update(
                root_id=root["message_id"],
                parent_id=root["message_id"],
                thread_id=f"omt_{root['message_id']}",
            )
            assert transport.admit("steward-app", ev)["status"] == "durably_accepted"
            requests.append(transport.core.read_request(identity_ref(binding["provider_ref"], ev["message_id"])))

        assert len({r["session_id"] for r in requests}) == 4
        assert two_running.wait(timeout=5.0)
        # Give the 3rd and 4th queue workers a window to contend on the semaphore.
        time.sleep(0.15)
        assert peak_active == 2

        release_turns.set()
        for req in requests:
            assert finish(runtime, req)["status"] == "completed"
        assert peak_active == 2
    finally:
        release_turns.set()
        runtime.close()


def test_topic_lane_stop_and_status_are_lane_scoped_without_stopping_lobby(steward):  # noqa: F811
    store, runtime, provider, transport, binding, _, _ = steward
    try:
        # Start a slow turn in Lobby.
        lobby_ev = provider.event("steward-app", "lobby-keep-alive", "wait for interrupt")
        assert transport.admit("steward-app", lobby_ev)["status"] == "durably_accepted"
        lobby_req = transport.core.read_request(identity_ref(binding["provider_ref"], lobby_ev["message_id"]))
        _wait_turn_running(store, lobby_req["session_id"], lobby_req["turn_id"])

        # Start a slow turn in a distinct Topic Lane.
        root = provider.event("steward-app", "topic-stop-root", "Topic root")
        topic_ev = provider.event("steward-app", "topic-slow", "wait for interrupt")
        topic_ev.update(
            root_id=root["message_id"],
            parent_id=root["message_id"],
            thread_id=f"omt_{root['message_id']}",
        )
        provider.messages[topic_ev["message_id"]].update(
            root_id=root["message_id"],
            parent_id=root["message_id"],
            thread_id=f"omt_{root['message_id']}",
        )
        assert transport.admit("steward-app", topic_ev)["status"] == "durably_accepted"
        topic_req = transport.core.read_request(identity_ref(binding["provider_ref"], topic_ev["message_id"]))
        _wait_turn_running(store, topic_req["session_id"], topic_req["turn_id"])

        # Send /stop inside the Topic Lane -> interrupts ONLY the Topic Lane, NOT Lobby!
        stop_topic = provider.event("steward-app", "stop-topic-only", "/stop")
        stop_topic.update(
            root_id=root["message_id"],
            parent_id=topic_ev["message_id"],
            thread_id=f"omt_{root['message_id']}",
        )
        provider.messages[stop_topic["message_id"]].update(
            root_id=root["message_id"],
            parent_id=topic_ev["message_id"],
            thread_id=f"omt_{root['message_id']}",
        )
        assert transport.admit("steward-app", stop_topic)["status"] == "command_recorded"
        assert finish(runtime, topic_req)["status"] == "interrupted"
        assert store.load_turn(lobby_req["session_id"], lobby_req["turn_id"])["status"] == "running"

        # Now stop Lobby cleanly.
        assert transport.admit("steward-app", provider.event("steward-app", "stop-lobby-final", "/stop"))["status"] == "command_recorded"
        assert finish(runtime, lobby_req)["status"] == "interrupted"
    finally:
        runtime.close()
