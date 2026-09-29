#!/usr/bin/env python3
"""Smoke-test bounded persistence overhead for a burst of Chat deltas."""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from loopx import chat_store as chat_store_module  # noqa: E402
from loopx.chat_runtime import ChatRuntimeController  # noqa: E402
from loopx.chat_store import ChatSessionStore  # noqa: E402


class BurstAdapter:
    upstream_thread_id = "burst-thread"

    def capabilities(self) -> dict[str, Any]:
        return {"streaming": True, "resume": True, "interrupt": True}

    def start_turn(
        self,
        message: str,
        event_sink: Callable[[str, dict[str, Any]], None],
    ) -> dict[str, Any]:
        event_sink("turn.started", {"upstream_turn_id": "burst-turn"})
        for index in range(500):
            event_sink("answer.delta", {"text": f"chunk-{index} "})
        return {"message": "Burst complete.", "proposals": [], "gate": None}

    def interrupt_turn(self, turn_id: str | None = None) -> None:
        del turn_id

    def close_session(self) -> None:
        return None

    def healthcheck(self) -> bool:
        return True


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-chat-throughput-") as raw_tmp:
        root = Path(raw_tmp)
        store = ChatSessionStore(root / "runtime")
        session = store.create_session(
            goal_id="throughput-goal",
            agent_id="codex",
            adapter_kind="codex_app_server",
            upstream_thread_id="burst-thread",
        )
        session_id = str(session["session_id"])
        controller = ChatRuntimeController(store=store, codex_bin="codex")
        controller.adapters[session_id] = BurstAdapter()

        fsync_calls = 0
        original_fsync = os.fsync

        def counted_fsync(file_descriptor: int) -> None:
            nonlocal fsync_calls
            fsync_calls += 1
            original_fsync(file_descriptor)

        os.fsync = counted_fsync
        started = time.monotonic()
        try:
            turn, created = controller.submit_turn(
                session_id=session_id,
                client_turn_id="burst-client-turn",
                message="stream a burst",
                work_dir=root,
                objective="Measure streaming persistence.",
            )
            assert created is True
            completed = controller.wait_for_turn(
                session_id=session_id,
                turn_id=str(turn["turn_id"]),
                timeout_sec=10,
            )
            ready_deadline = time.monotonic() + 2
            while time.monotonic() < ready_deadline:
                if (store.load_session(session_id) or {}).get("status") == "ready":
                    break
                time.sleep(0.01)
            assert (store.load_session(session_id) or {}).get("status") == "ready"
        finally:
            os.fsync = original_fsync

        elapsed = time.monotonic() - started
        assert completed["status"] == "completed", completed
        assert completed["delta_count"] == 500, completed
        assert fsync_calls < 100, f"delta persistence used {fsync_calls} fsync calls"
        # The fsync count above is the real budget: it proves no per-delta fsync
        # regardless of runner speed. The wall-clock bound is only a hang guard,
        # so it stays loose enough for a slow shared CI runner.
        assert elapsed < 5.0, f"delta persistence hung for {elapsed:.3f}s"

        replay_store = ChatSessionStore(root / "runtime")
        original_read_jsonl = chat_store_module._read_jsonl
        read_calls = 0

        def counted_read_jsonl(path: Path) -> list[dict[str, Any]]:
            nonlocal read_calls
            read_calls += 1
            return original_read_jsonl(path)

        chat_store_module._read_jsonl = counted_read_jsonl
        try:
            for _ in range(20):
                events = replay_store.events_after(session_id, str(turn["turn_id"]), "0")
                assert len(events) >= 502, len(events)
        finally:
            chat_store_module._read_jsonl = original_read_jsonl
            controller.close()
        assert read_calls <= 1, f"SSE replay reread the event log {read_calls} times"
        assert [int(event["sequence"]) for event in events] == list(range(1, len(events) + 1))
        assert [
            event["payload"]["text"]
            for event in events
            if event["kind"] == "answer.delta"
        ] == [f"chunk-{index} " for index in range(500)]

    print(f"loopx-chat-stream-throughput-smoke: {fsync_calls} fsync, {elapsed:.3f}s")
    print("loopx-chat-stream-throughput-smoke: ok")


if __name__ == "__main__":
    main()
