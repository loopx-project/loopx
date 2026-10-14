from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import threading
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from loopx.chat_runtime import ChatRuntimeController
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.chat_store import CHAT_TURN_SCHEMA_VERSION, ChatSessionStore


class _HealthyAdapter:
    upstream_thread_id = "fixture-upstream"

    def capabilities(self) -> dict[str, Any]:
        return {}

    def start_turn(
        self,
        message: str,
        event_sink: Any,
    ) -> dict[str, Any]:
        del message, event_sink
        raise AssertionError("the fixture replaces runtime dispatch")

    def interrupt_turn(self, turn_id: str | None = None) -> None:
        del turn_id

    def healthcheck(self) -> bool:
        return True

    def close_session(self) -> None:
        return None


def _turn_count(store: ChatSessionStore, session_id: str) -> int:
    count = 0
    for path in (store.sessions_root / session_id / "turns").glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise TypeError("persisted turn-directory JSON must be an object")
        if payload.get("schema_version") == CHAT_TURN_SCHEMA_VERSION:
            count += 1
    return count


def main() -> int:
    scenario = sys.argv[1] if len(sys.argv) > 1 else ""
    if scenario not in {"before_transcript", "after_queued"}:
        raise ValueError("unknown acceptance fault scenario")

    root = Path(tempfile.mkdtemp(prefix="loopx-chat-acceptance-http-"))
    project = root / "project"
    project.mkdir()
    registry_path = root / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "common_runtime_root": str(root),
                "goals": [
                    {
                        "id": "goal-one",
                        "repo": str(project),
                        "status": "active",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    store = ChatSessionStore(root / "runtime")
    runtime = ChatRuntimeController(
        store=store,
        codex_bin="missing-codex",
        registry_path=registry_path,
    )
    session_id = str(
        store.create_session(
            goal_id="goal-one",
            agent_id="codex",
            executor_endpoint_id="codex",
            adapter_kind="codex_app_server",
            upstream_thread_id="fixture-upstream",
            upstream_mode="chat",
            codex_home=str(runtime.codex_home),
        )["session_id"]
    )
    mismatched_session_id = str(
        store.create_session(
            goal_id="goal-one",
            agent_id="codex",
            executor_endpoint_id="codex",
            adapter_kind="codex_app_server",
            upstream_thread_id="other-upstream",
            upstream_mode="chat",
            codex_home=str(root / "other-codex-home"),
        )["session_id"]
    )
    runtime.adapters[session_id] = _HealthyAdapter()

    dispatch_started = threading.Event()
    dispatch_release = threading.Event()
    dispatch_count = 0

    def blocked_run_turn(**kwargs: object) -> None:
        nonlocal dispatch_count
        dispatch_count += 1
        dispatch_started.set()
        dispatch_release.wait(timeout=10)
        done_event = kwargs["done_event"]
        if not isinstance(done_event, threading.Event):
            raise TypeError("turn dispatch must carry a completion event")
        with runtime.lock:
            runtime.turn_done_events.pop(
                (session_id, str(kwargs["turn_id"])),
                None,
            )
        done_event.set()

    runtime._run_turn = blocked_run_turn

    failed = False
    if scenario == "before_transcript":
        original_append_message = store.append_message

        def fail_before_transcript(*args: Any, **kwargs: Any) -> dict[str, Any]:
            nonlocal failed
            if not failed and kwargs.get("role") == "user":
                failed = True
                raise OSError("private transcript fault detail")
            result = original_append_message(*args, **kwargs)
            if not isinstance(result, dict):
                raise TypeError("append_message returned an invalid result")
            return result

        store.append_message = fail_before_transcript
    else:
        original_append_event = store.append_event

        def fail_after_queued(*args: Any, **kwargs: Any) -> dict[str, Any]:
            nonlocal failed
            result = original_append_event(*args, **kwargs)
            if not isinstance(result, dict):
                raise TypeError("append_event returned an invalid result")
            if not failed and kwargs.get("kind") == "turn.queued":
                failed = True
                raise OSError("private queued event fault detail")
            return result

        store.append_event = fail_after_queued

    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.verbose = False
    server.registry_path = registry_path
    server.chat_store = store
    server.runtime_controller = runtime
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    print(
        json.dumps(
            {
                "origin": f"http://127.0.0.1:{server.server_port}",
                "session_id": session_id,
                "mismatched_session_id": mismatched_session_id,
            }
        ),
        flush=True,
    )

    try:
        if sys.stdin.readline().strip() != "inspect":
            raise ValueError("fixture did not receive an inspect request")
        if not dispatch_started.wait(timeout=5):
            raise TimeoutError("accepted turn was not dispatched")
        turn = store.turn_for_client(session_id, "recoverable-request")
        if turn is None:
            raise AssertionError("accepted turn is missing")
        queued_events = [
            event
            for event in store.events_after(
                session_id,
                str(turn["turn_id"]),
                None,
            )
            if event.get("kind") == "turn.queued"
        ]
        user_messages = [
            message
            for message in store.messages(session_id)
            if message.get("role") == "user"
        ]
        mismatch_turns = _turn_count(store, mismatched_session_id)
        mismatch_messages = store.messages(mismatched_session_id)
        print(
            json.dumps(
                {
                    "fault_injected": failed,
                    "turn_count": _turn_count(store, session_id),
                    "user_message_count": len(user_messages),
                    "queued_event_count": len(queued_events),
                    "dispatch_count": dispatch_count,
                    "acceptance_capsule_present": "_acceptance" in turn,
                    "mismatched_turn_count": mismatch_turns,
                    "mismatched_message_count": len(mismatch_messages),
                    "mismatched_session_status": (
                        store.load_session(mismatched_session_id) or {}
                    ).get("status"),
                }
            ),
            flush=True,
        )
    finally:
        dispatch_release.set()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)
        runtime.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
