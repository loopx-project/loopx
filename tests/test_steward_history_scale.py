from __future__ import annotations

import json
from pathlib import Path
import pytest

from loopx.capabilities.manager_context.chat_history import (
    MAX_KNOWN_HISTORY_MESSAGES,
    page_chat_messages,
)
from loopx.capabilities.manager_context.presentation import project_collaboration
from loopx.capabilities.manager_context.roundtrip import (
    parse_chat_history_selection,
    read_chat_history_selection,
)
from loopx.capabilities.native_chat.queue_api import ChatQueueRequestMixin
from loopx.chat_server import ChatRequestHandler
from loopx.chat_store import ChatSessionStore, utc_now
from loopx.control_plane.collaboration.source_chat_observation import original_turn_reader
from loopx.control_plane.effect_runtime import effect_runtime_request_scope
from loopx.extensions.lark.private_web_projection import PrivateUnsupportedMediaHistory
from loopx.presentation.request_control_api import RequestControlMixin


def _create_steward_session(tmp_path: Path) -> tuple[Path, Path, ChatSessionStore, str]:
    runtime_root = tmp_path / "runtime"
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"goals": []}, ensure_ascii=False), encoding="utf-8")
    store = ChatSessionStore(runtime_root)
    binding_id = "b" * 24
    source_ref = "c" * 24
    saved = {
        "kind": "bound_steward",
        "project_ref": "a" * 24,
        "workspace_path": str(tmp_path),
        "audience": "bound_owner",
        "grant": "portfolio_read",
        "binding_id": binding_id,
        "provider_ref": "d" * 24,
        "operator_ref": "e" * 24,
        "source_ref": source_ref,
        "goal_ids": [],
    }
    session = store.create_session(
        goal_id="loopx-manager",
        agent_id="antigravity",
        adapter_kind="antigravity_ls",
        upstream_thread_id="steward-thread-scale",
        channel_id=f"manager.external.native.{binding_id}.{source_ref}",
        steward_context=saved,
    )
    return runtime_root, registry, store, str(session["session_id"])


def test_history_paging_and_known_history_resume_above_5000_messages(tmp_path: Path) -> None:
    runtime_root, registry, store, sid = _create_steward_session(tmp_path)
    journal_path = store.sessions_root / sid / "messages.jsonl"
    now = utc_now()
    initial_count = 5_205
    raw_lines = [
        json.dumps(
            {
                "schema_version": "loopx_chat_message_v1",
                "message_id": f"msg-{idx:05d}",
                "turn_id": None,
                "role": "user" if idx % 2 == 0 else "agent",
                "text": f"Historical entry {idx}",
                "created_at": now,
            },
            separators=(",", ":"),
        )
        for idx in range(initial_count)
    ]
    journal_path.write_text("\n".join(raw_lines) + "\n", encoding="utf-8")

    assert MAX_KNOWN_HISTORY_MESSAGES >= 50_000
    digests: list[str] = []
    collected: list[dict[str, object]] = []
    cursor: str | None = None
    with effect_runtime_request_scope():
        while True:
            query = {"message_limit": ["200"]}
            if cursor is not None:
                query["message_cursor"] = [cursor]
            selection = parse_chat_history_selection(query)
            payload = read_chat_history_selection(
                runtime_root,
                store,
                sid,
                registry=registry,
                selection=selection,
                project_provider=lambda rows: rows,
            )
            page = payload["message_page"]
            assert isinstance(page.get("visible_page_sha256"), str)
            digests.append(page["visible_page_sha256"])
            collected.extend(payload["messages"])
            cursor = page["next_cursor"]
            if cursor is None:
                break

    assert len(collected) == initial_count
    assert len(digests) == 27
    basis = f"{initial_count}:{''.join(digests)}"

    store.append_message(sid, role="user", message_id="msg-tail-1", text="Tail question")
    store.append_message(sid, role="agent", message_id="msg-tail-2", text="Tail answer")

    with effect_runtime_request_scope():
        resumed = read_chat_history_selection(
            runtime_root,
            store,
            sid,
            registry=registry,
            selection=parse_chat_history_selection({"message_limit": ["200"], "known_history": [basis]}),
            project_provider=lambda rows: rows,
        )
    assert [row["message_id"] for row in resumed["messages"]] == ["msg-tail-1", "msg-tail-2"]
    next_basis = resumed["history_resume"]["next"]
    assert next_basis.startswith(f"{initial_count + 2}:")

    # Mutating an earlier row on disk invalidates both cached journal rows and known_history proof.
    rewritten = journal_path.read_text(encoding="utf-8").replace("Historical entry 0", "Changed entry 0", 1)
    journal_path.write_text(rewritten, encoding="utf-8")
    with effect_runtime_request_scope():
        restarted = read_chat_history_selection(
            runtime_root,
            store,
            sid,
            registry=registry,
            selection=parse_chat_history_selection({"message_limit": ["200"], "known_history": [next_basis]}),
            project_provider=lambda rows: rows,
        )
    assert restarted == {
        "ok": True,
        "schema_version": "loopx_chat_history_resume_v1",
        "status": "restart",
    }


def test_cached_journal_rows_invalidate_on_marker_insertion_and_bytes_change(tmp_path: Path) -> None:
    _, _, store, sid = _create_steward_session(tmp_path)
    store.append_message(sid, role="user", message_id="m1", text="one")
    store.append_message(sid, role="agent", message_id="m2", text="two")
    store.append_message(sid, role="user", message_id="m3", text="three")

    cached = store.session_snapshot(sid, strict_messages=True, copy_messages=False)["messages"]
    first_page = page_chat_messages(sid, cached, limit=2)
    second_page = page_chat_messages(sid, cached, limit=2, cursor=first_page["message_page"]["next_cursor"])
    assert [row["message_id"] for row in second_page["messages"]] == ["m3"]

    markers = [
        {
            "schema_version": "loopx_chat_message_v1",
            "message_id": "lark.unsupported.000000000000000000000001",
            "turn_id": None,
            "role": "error",
            "origin": "lark",
            "text": "Unsupported media",
            "created_at": "2026-01-01T00:00:00.000000+00:00",
        }
    ]
    with_markers = PrivateUnsupportedMediaHistory.insert(cached, markers)
    with pytest.raises(ValueError, match="cursor"):
        page_chat_messages(sid, with_markers, limit=2, cursor=first_page["message_page"]["next_cursor"])


def test_plain_terminal_turn_cache_and_original_turn_reader_reuse_stat_fingerprints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root, registry, store, _ = _create_steward_session(tmp_path)
    manager_session = store.create_session(
        goal_id="loopx-manager",
        agent_id="codex",
        adapter_kind="codex_app_server",
        upstream_thread_id="manager-thread-scale",
        channel_id="manager",
    )
    sid = str(manager_session["session_id"])
    turns_dir = store.sessions_root / sid / "turns"
    turns_dir.mkdir(parents=True, exist_ok=True)
    now = utc_now()
    for idx in range(6):
        tid = f"turn-{idx}"
        (turns_dir / f"{tid}.json").write_text(
            json.dumps(
                {
                    "schema_version": "loopx_chat_turn_state_v1",
                    "session_id": sid,
                    "turn_id": tid,
                    "client_turn_id": f"client-{idx}",
                    "status": "completed",
                    "message": f"question {idx}",
                    "response": {"message": f"answer {idx}", "proposals": []},
                    "created_at": now,
                    "completed_at": now,
                }
            ),
            encoding="utf-8",
        )
        store.append_message(sid, role="agent", turn_id=tid, message_id=f"ans-{idx}", text=f"answer {idx}")

    messages = store.messages(sid, strict=True)
    load_calls = 0
    original_load_turn = store.load_turn

    def counting_load_turn(session_id: str, turn_id: str) -> dict[str, object] | None:
        nonlocal load_calls
        load_calls += 1
        return original_load_turn(session_id, turn_id)

    monkeypatch.setattr(store, "load_turn", counting_load_turn)
    with effect_runtime_request_scope():
        project_collaboration(store, runtime_root, sid, messages, registry=registry)
        assert load_calls == 6
        load_calls = 0
        project_collaboration(store, runtime_root, sid, messages, registry=registry)
        assert load_calls == 0

    # Warm terminal turn discovery avoids re-parsing unchanged turn JSON files while re-reading matched turns fresh.
    reader_cold = original_turn_reader(store, sid)
    assert reader_cold("client-3") is not None
    reader_warm = original_turn_reader(store, sid)
    assert reader_warm("client-3") is not None


def test_socket_disconnect_during_response_does_not_emit_secondary_503() -> None:
    errors: list[tuple[str, int]] = []

    class DummySnapshotHandler:
        path = "/api/chat/sessions/s1?message_limit=200"

        class server:
            registry_path = Path("/tmp/registry.json")

            class chat_store:
                @staticmethod
                def load_session(_sid: str) -> dict[str, object]:
                    return {"session_id": "s1"}

                @staticmethod
                def session_snapshot(_sid: str, **_kwargs: object) -> dict[str, object]:
                    return {
                        "ok": True,
                        "schema_version": "loopx_chat_store_v1",
                        "session": {"session_id": "s1"},
                        "messages": [],
                        "active_turn": None,
                    }

            class private_media_history:
                @staticmethod
                def read(*_args: object) -> list[dict[str, object]]:
                    return []

            class runtime_controller:
                coordination_runtime_root = Path("/tmp")
                project_contexts = None

                @staticmethod
                def recover_exited_turn(_sid: str, _tid: str | None = None) -> None:
                    return None

        @staticmethod
        def _link_history_images(_sid: str, _session: object, _sel: object, rows: list[dict[str, object]]) -> list[dict[str, object]]:
            return rows

        @staticmethod
        def _send_json(_payload: object, status: int = 200) -> None:
            raise BrokenPipeError(f"client disconnected on {status}")

        @staticmethod
        def _send_error(message: str, status: int = 400, **_kwargs: object) -> None:
            errors.append((message, status))

    ChatRequestHandler._session_snapshot(DummySnapshotHandler(), "s1")  # type: ignore[arg-type]
    assert errors == []

    class DummyQueueHandler(ChatQueueRequestMixin):
        class server:
            class chat_store:
                @staticmethod
                def load_session(_sid: str) -> dict[str, object]:
                    return {"session_id": "s1", "active_turn_id": None}

                @staticmethod
                def queued_turns(_sid: str, **_kwargs: object) -> list[dict[str, object]]:
                    return []

            class runtime_controller:
                project_contexts = None

        @staticmethod
        def _session_context(_session: object) -> None:
            return None

        @staticmethod
        def _send_json(_payload: object, status: int = 200) -> None:
            raise ConnectionResetError(f"reset on {status}")

        @staticmethod
        def _send_error(message: str, status: int = 400, **_kwargs: object) -> None:
            errors.append((message, status))

    DummyQueueHandler()._session_queue("s1")
    assert errors == []
    assert hasattr(RequestControlMixin, "_request_inventory_read")
