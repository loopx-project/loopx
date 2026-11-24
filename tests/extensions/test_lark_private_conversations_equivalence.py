"""Paired immutable baseline vs HEAD behavioral equivalence tests for private Lark conversations."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime as real_datetime, timezone as real_timezone
from functools import cache
import json
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace
from typing import Any
import zlib

import pytest

from loopx.capabilities.native_chat.conversation_bindings import ChatConversationBindings
from loopx.capabilities.native_chat.external_conversations import STEWARD_BINDING_ADOPTION_PENDING
from loopx.capabilities.native_chat.project_context import ChatProjectContexts
from loopx.chat_store import CHAT_TURN_SCHEMA_VERSION, ChatSessionStore, _atomic_write_json
from loopx.extensions.lark import identity_shapes
from loopx.extensions.lark import private_conversations as head_mod
from loopx.extensions.lark.conversation_identity import identity_ref, lark_private_source
from loopx.extensions.lark.private_delivery_discovery import PrivateDeliveryDiscovery


BASELINE_COMMIT = "f0ca9cef0e394b62d4a0f28c54beba16cb7aa29c"


@cache
def _load_baseline_module(ref: str = BASELINE_COMMIT) -> ModuleType:
    repo_root = Path(__file__).resolve().parents[2]
    proc = subprocess.run(
        ["git", "show", f"{ref}:loopx/extensions/lark/private_conversations.py"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        detail = proc.stderr.strip() or f"exit={proc.returncode}"
        raise RuntimeError(f"paired equivalence baseline {ref} is unavailable: {detail}")
    module_name = f"loopx.extensions.lark._baseline_{ref[:12]}_private_conversations"
    mod = ModuleType(module_name)
    mod.__file__ = str(repo_root / "loopx" / "extensions" / "lark" / "private_conversations.py")
    mod.__package__ = "loopx.extensions.lark"
    sys.modules[module_name] = mod
    try:
        exec(compile(proc.stdout, f"{ref}:loopx/extensions/lark/private_conversations.py", "exec"), mod.__dict__)
    except Exception as exc:
        sys.modules.pop(module_name, None)
        raise RuntimeError(f"paired equivalence baseline {ref} failed to load: {exc}") from exc
    return mod


def _variants() -> list[tuple[str, ModuleType]]:
    base = _load_baseline_module(BASELINE_COMMIT)
    if base is head_mod or base.LarkPrivateConversations is head_mod.LarkPrivateConversations:
        raise RuntimeError("paired equivalence baseline resolved to HEAD module")
    variants: list[tuple[str, ModuleType]] = [(f"baseline_{BASELINE_COMMIT[:10]}", base), ("HEAD", head_mod)]
    assert len(variants) == 2
    return variants


def test_missing_or_unreadable_baseline_fails_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    variants = _variants()
    assert [name for name, _ in variants] == [f"baseline_{BASELINE_COMMIT[:10]}", "HEAD"]
    assert variants[0][1] is not head_mod
    assert variants[1][1] is head_mod
    assert variants[0][1].LarkPrivateConversations is not head_mod.LarkPrivateConversations

    with pytest.raises(RuntimeError, match="unavailable"):
        _load_baseline_module("0" * 40)

    def _corrupt_git_show(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=["git", "show"], returncode=0, stdout="def broken(:\n", stderr="")

    with monkeypatch.context() as ctx:
        ctx.setattr(subprocess, "run", _corrupt_git_show)
        with pytest.raises(RuntimeError, match="failed to load"):
            _load_baseline_module("syntax-error-baseline-ref")
        _load_baseline_module.cache_clear()
        try:
            with pytest.raises(RuntimeError, match="failed to load"):
                _variants()
        finally:
            _load_baseline_module.cache_clear()


def _assert_paired_equal(outputs: list[dict[str, Any]]) -> dict[str, Any]:
    assert len(outputs) == 2, f"expected paired [baseline, HEAD] execution, got {len(outputs)} result(s)"
    assert outputs[0] == outputs[1]
    return outputs[0]


@contextmanager
def _patch_clock(monkeypatch: pytest.MonkeyPatch, mod: ModuleType, clock: list[float]) -> Iterator[None]:
    fake_time = SimpleNamespace(monotonic=lambda: clock[0], time=lambda: clock[0])

    class _FakeDateTime:
        @staticmethod
        def now(_tz: object = None) -> real_datetime:
            return real_datetime.fromtimestamp(clock[0], tz=real_timezone.utc)

    with monkeypatch.context() as ctx:
        ctx.setattr(mod, "time", fake_time)
        ctx.setattr(mod, "datetime", _FakeDateTime)
        if mod is not head_mod:
            ctx.setattr(head_mod, "time", fake_time)
            ctx.setattr(head_mod, "datetime", _FakeDateTime)
        yield


class _FakeProvider:
    def __init__(self, *, app_id: str = "cli_alpha123", bot_open_id: str = "ou_bot_1",
                 owner_open_id: str = "ou_owner_1", chat_id: str = "oc_chat_1",
                 profile: str = "notes-app") -> None:
        self.app_id = app_id
        self.bot_open_id = bot_open_id
        self.owner_open_id = owner_open_id
        self.chat_id = chat_id
        self.profile = profile
        self.messages: dict[str, dict[str, Any]] = {}
        self.writes: list[tuple[str, str, str]] = []
        self.edits: list[tuple[str, str]] = []
        self.calls: list[list[str]] = []
        self.update_blocker: str | None = None
        self.verify_replies = True
        self.out_seq = 0

    def add_message(
        self, message_id: str, content: str, *, chat_type: str = "p2p",
        sender_id: str | None = None, msg_type: str = "text",
        root_id: str | None = None, parent_id: str | None = None, thread_id: str | None = None,
        mentions: list[dict[str, Any]] | None = None, deleted: bool = False,
        raw_text: str | None = None, create_time: str = "1700000000000",
    ) -> dict[str, Any]:
        sid = sender_id or self.owner_open_id
        raw_content = json.dumps({"text": content if raw_text is None else raw_text}, ensure_ascii=False)
        msg = {
            "message_id": message_id,
            "chat_id": self.chat_id,
            "chat_type": chat_type,
            "msg_type": msg_type,
            "message_type": msg_type,
            "content": content,
            "body": {"content": raw_content},
            "sender": {"id": sid, "sender_type": "user"},
            "create_time": create_time,
            "deleted": deleted,
            **({"root_id": root_id} if root_id else {}),
            **({"parent_id": parent_id} if parent_id else {}),
            **({"thread_id": thread_id} if thread_id else {}),
            **({"mentions": mentions} if mentions is not None else {}),
        }
        self.messages[message_id] = msg
        return msg

    def event(
        self, message_id: str, content: str, *, chat_type: str = "p2p",
        msg_type: str = "text", root_id: str | None = None, parent_id: str | None = None,
        thread_id: str | None = None, mentions: list[dict[str, Any]] | None = None,
        create_time: str = "1700000000000",
    ) -> dict[str, Any]:
        return {
            "schema_version": "lark_event_inbox_event_v0",
            "event_id": f"ev_{message_id}",
            "message_id": message_id,
            "chat_id": self.chat_id,
            "chat_type": chat_type,
            "sender_id": self.owner_open_id,
            "sender_type": "user",
            "message_type": msg_type,
            "content": content,
            "create_time": create_time,
            **({"root_id": root_id} if root_id else {}),
            **({"parent_id": parent_id} if parent_id else {}),
            **({"thread_id": thread_id} if thread_id else {}),
            **({"mentions": mentions} if mentions is not None else {}),
        }

    def __call__(self, args: list[str], _cwd: Path | None = None, _timeout: float | None = None) -> dict[str, Any]:
        self.calls.append(list(args))
        if args[1:3] == ["profile", "list"]:
            return {
                "returncode": 0,
                "stdout": json.dumps([{"name": self.profile, "appId": self.app_id}]),
                "stderr": "",
            }
        tail = args[3:] if len(args) > 2 and args[1] == "--profile" else args[1:]
        if tail[:3] == ["auth", "status", "--verify"]:
            return {
                "returncode": 0,
                "stdout": json.dumps({
                    "appId": self.app_id,
                    "identities": {
                        "bot": {"available": True, "verified": True, "openId": self.bot_open_id, "appName": "LoopX Bot"},
                        "user": {"available": True, "verified": True, "openId": self.owner_open_id},
                    },
                }),
                "stderr": "",
            }
        if tail[:2] == ["api", "GET"] and "/open-apis/im/v1/messages/mget" in tail[2]:
            params = json.loads(tail[tail.index("--params") + 1])
            items = [self.messages[mid] for mid in params["message_ids"] if mid in self.messages]
            return {"returncode": 0, "stdout": json.dumps({"code": 0, "data": {"items": items}}), "stderr": ""}
        if tail[:2] == ["api", "GET"] and tail[2].startswith("/open-apis/im/v1/messages/"):
            mid = tail[2].rsplit("/", 1)[-1]
            item = self.messages.get(mid)
            if item is None:
                return {"returncode": 1, "stdout": json.dumps({"code": 1, "ok": False}), "stderr": ""}
            return {"returncode": 0, "stdout": json.dumps({"code": 0, "data": {"items": [item]}}), "stderr": ""}
        if tail[:2] == ["im", "+messages-mget"]:
            mids = tail[tail.index("--message-ids") + 1].split(",")
            items = [self.messages[mid] for mid in mids if mid in self.messages]
            if not self.verify_replies and any(mid.startswith("om_out_") for mid in mids):
                return {"returncode": 1, "stdout": "", "stderr": "unverified"}
            return {"returncode": 0, "stdout": json.dumps({"ok": True, "identity": "bot", "data": {"items": items}}), "stderr": ""}
        if tail[:2] == ["im", "+messages-reply"] or tail[:2] == ["im", "+messages-send"]:
            kind, raw_content, text = self._parse_outbound_args(tail)
            if "--dry-run" in tail:
                return {
                    "returncode": 0,
                    "stdout": json.dumps({"ok": True, "api": [{"body": {"content": raw_content, "msg_type": kind}}]}),
                    "stderr": "",
                }
            mid = f"om_out_{self.out_seq}"
            self.out_seq += 1
            self.writes.append(("reply", mid, text))
            self.messages[mid] = {
                "message_id": mid,
                "chat_id": self.chat_id,
                "msg_type": kind,
                "message_type": kind,
                "content": text,
                "body": {"content": raw_content},
                "sender": {"id": self.app_id, "sender_type": "app"},
                "deleted": False,
            }
            return {
                "returncode": 0,
                "stdout": json.dumps({"ok": True, "identity": "bot", "data": {"message_id": mid}}),
                "stderr": "",
            }
        if (tail[:2] == ["im", "messages"] and "patch" in tail) or tail[:2] == ["im", "+messages-edit"]:
            kind, raw_content, text = self._parse_outbound_args(tail)
            if "--dry-run" in tail:
                return {
                    "returncode": 0,
                    "stdout": json.dumps({"ok": True, "api": [{"body": {"content": raw_content, "msg_type": kind}}]}),
                    "stderr": "",
                }
            if self.update_blocker == "provider_update_edit_limit":
                return {
                    "returncode": 1,
                    "stdout": json.dumps({"ok": False, "error": {"type": "api", "code": 230072, "message": "edit limit"}}),
                    "stderr": "",
                }
            if self.update_blocker == "provider_update_too_large":
                return {
                    "returncode": 1,
                    "stdout": json.dumps({"ok": False, "error": {"type": "api", "code": 230025, "message": "body too large"}}),
                    "stderr": "",
                }
            mid = tail[tail.index("--message-id") + 1]
            self.edits.append((mid, text))
            self.messages[mid]["msg_type"] = kind
            self.messages[mid]["message_type"] = kind
            self.messages[mid]["content"] = text
            self.messages[mid]["body"] = {"content": raw_content}
            return {"returncode": 0, "stdout": json.dumps({"ok": True, "identity": "bot", "data": {"message_id": mid}}), "stderr": ""}
        if tail[:2] == ["im", "+messages-reactions-create"] or tail[:2] == ["im", "+messages-reactions-delete"]:
            return {"returncode": 0, "stdout": json.dumps({"ok": True, "data": {"reaction_id": "rxn_1"}}), "stderr": ""}
        return {"returncode": 0, "stdout": json.dumps({"ok": True}), "stderr": ""}

    @classmethod
    def _parse_outbound_args(cls, tail: list[str]) -> tuple[str, str, str]:
        if "--content" in tail:
            raw = tail[tail.index("--content") + 1]
            parsed = json.loads(raw)
            if isinstance(parsed, dict) and "zh_cn" in parsed:
                rows = parsed["zh_cn"].get("content") or []
                text = "".join(
                    str(node.get("text") or "")
                    for row in rows if isinstance(row, list)
                    for node in row if isinstance(node, dict)
                )
                return "post", raw, text
            text = str(parsed.get("text") if isinstance(parsed, dict) else raw)
            return "text", raw, text
        if "--data" in tail:
            data = json.loads(tail[tail.index("--data") + 1])
            raw = str(data.get("content") or "{}")
            parsed = json.loads(raw)
            text = str(parsed.get("text") if isinstance(parsed, dict) else raw)
            return "text", raw, text
        if "--post" in tail or "--markdown" in tail:
            text = cls._extract_reply_text(tail)
            raw = json.dumps({"zh_cn": {"content": [[{"tag": "md", "text": text}]]}}, ensure_ascii=False)
            return "post", raw, text
        text = cls._extract_reply_text(tail)
        raw = json.dumps({"text": text}, ensure_ascii=False)
        return "text", raw, text

    @staticmethod
    def _extract_reply_text(tail: list[str]) -> str:
        if "--text" in tail:
            return tail[tail.index("--text") + 1]
        if "--post" in tail:
            return tail[tail.index("--post") + 1]
        if "--markdown" in tail:
            return tail[tail.index("--markdown") + 1]
        if "--content" in tail:
            raw = tail[tail.index("--content") + 1]
            try:
                parsed = json.loads(raw)
            except ValueError:
                return raw
            if isinstance(parsed, dict) and "zh_cn" in parsed:
                return str(parsed["zh_cn"].get("content") or "")
            return str(parsed.get("text") if isinstance(parsed, dict) else raw)
        return ""


def _build_env(
    mod: ModuleType, root: Path, *, audience: str = "owner", context_kind: str = "project",
    reaction_feedback: bool = False,
) -> tuple[ChatSessionStore, ChatConversationBindings, _FakeProvider, Any, dict[str, Any]]:
    workspace = root / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    runtime_root = root / "runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    provider = _FakeProvider()
    store = ChatSessionStore(runtime_root / ".loopx" / "chat")
    contexts = ChatProjectContexts([workspace])

    def observe(profile: str) -> dict[str, Any]:
        pref = identity_ref(provider.app_id)
        op = identity_ref(pref, "group" if audience == "group" else provider.owner_open_id)
        return {
            "transport_ref": profile,
            "provider_ref": pref,
            "operator_ref": op,
            "verified": True,
            "consumer_ref": "c" * 32,
            "bot_display_name": "LoopX Bot",
            "bot_app_id": provider.app_id,
            "bot_open_id": provider.bot_open_id,
        }

    registry_path = runtime_root / "registry.json"
    registry_path.write_text(
        json.dumps({"schema_version": "0.1", "goals": [{"id": "g1", "repo": str(workspace)}]}),
        encoding="utf-8",
    )
    bindings = ChatConversationBindings(
        root=store.root,
        project_contexts=contexts,
        observe=observe,
        observe_group=observe if audience == "group" else None,
    )
    contexts.conversation_bindings = bindings
    controller = SimpleNamespace(
        store=store,
        project_contexts=contexts,
        registry_path=registry_path,
        coordination_runtime_root=runtime_root,
        steward_report_provider=None,
        adapters={},
        codex_bin="codex",
        lock=SimpleNamespace(__enter__=lambda s: s, __exit__=lambda s, *a: False),
        recover_exited_turn=lambda _sid, _tid: None,
        enqueue_turn=lambda session_id, *, client_turn_id, message, origin="lark", **_kw: store.create_turn(
            session_id, client_turn_id=client_turn_id, message=message, origin=origin,
        ),
        submit_external_turn=lambda **_kw: {"session_id": "s" * 32, "turn_id": "t" * 32},
    )
    bindings.controller = controller
    project_ref = contexts.available()[0]["project_ref"]
    binding = bindings.configure(
        transport_ref=provider.profile,
        project_ref=project_ref,
        executor_endpoint_id="codex",
        context_kind=context_kind,
        audience=audience if audience == "group" else None,
        group_refs=[identity_ref(identity_ref(provider.app_id), provider.chat_id)] if audience == "group" else None,
        available_group_refs=[identity_ref(identity_ref(provider.app_id), provider.chat_id)] if audience == "group" else None,
    )
    transport = mod.LarkPrivateConversations(
        controller=controller,
        runtime_root=runtime_root,
        runner=provider,
        cli_bin="lark-cli",
        reaction_feedback=reaction_feedback,
    )
    return store, bindings, provider, transport, binding


def test_exported_surface_and_identity_equivalence() -> None:
    expected_module_attrs = (
        "LarkPrivateConversations",
        "PrivateUnsupportedMediaHistory",
        "project_private_reply_history",
        "PROGRESS_EDIT_BUDGET",
        "APP_ID_PATTERN",
        "CHAT_CONTROL_COMMANDS",
        "ANSWER_IMAGE_RETRY_SECONDS",
        "ANSWER_IMAGE_SOURCE_TIMEOUT",
        "_status_text",
        "_command_text",
        "_same_fields",
        "_presentation_text",
        "_read_json",
        "_atomic_write_json",
        "time",
        "datetime",
        "record_event_timing",
        "resumable_answer_image_session",
        "reply_lark_event_inbox",
        "presentation_phase",
        "presentation_stream",
        "_msg_type",
        "_valid_user_sender",
        "_lark_payload",
    )
    expected_class_attrs = (
        "profiles",
        "configured_profile_apps",
        "health",
        "admit",
        "return_inbox",
        "pending_body_paths",
        "pending_delivery_paths",
        "is_recovery_path",
        "observe_ingress",
        "reconcile",
        "reconcile_request",
        "_deliver",
        "_deliver_completed_answer",
        "_inbox",
        "_feedback",
        "_source_message",
        "_source_verified",
        "_source_authority",
        "_group_source_message",
        "_group_source_pair",
        "_record_admission",
        "_reject_command",
        "_group_control_hint",
        "_group_text_command_input",
        "_binding",
        "_reply_runner",
        "_recovery_source_observation",
        "_planned_historical_repairs",
    )
    assert head_mod.APP_ID_PATTERN is identity_shapes.LARK_APP_ID_PATTERN
    seen: list[str] = []
    for name, mod in _variants():
        seen.append(name)
        for attr in expected_module_attrs:
            assert hasattr(mod, attr), f"missing module attr {attr}"
        for attr in expected_class_attrs:
            assert hasattr(mod.LarkPrivateConversations, attr), f"missing class attr {attr}"
        assert mod.APP_ID_PATTERN is identity_shapes.LARK_APP_ID_PATTERN
        assert mod.CHAT_CONTROL_COMMANDS == {"/status": "status", "/help": "help", "/new": "new", "/stop": "stop"}
    assert seen == [f"baseline_{BASELINE_COMMIT[:10]}", "HEAD"]


def test_message_normalization_and_status_equivalence() -> None:
    obs = {"bot_app_id": "cli_bot1", "bot_open_id": "ou_bot1"}
    samples_msg_type = [
        {"msg_type": "text", "message_type": "post"},
        {"message_type": "image"},
        {},
    ]
    samples_sender = [
        ({"chat_id": "c1", "sender": {"sender_type": "user", "id": "u1"}, "msg_type": "text"}, "c1", "u1", "text", False),
        ({"chat_id": "c1", "sender": {"sender_type": "app", "id": "u1"}, "msg_type": "text"}, "c1", "u1", "text", False),
        ({"chat_id": "c1", "sender": {"sender_type": "user", "id": "u1"}, "msg_type": "text", "deleted": True}, "c1", "u1", "text", True),
        ({"chat_id": "c2", "sender": {"sender_type": "user", "id": "u1"}, "msg_type": "text"}, "c1", "u1", "text", False),
    ]
    samples_hint = [
        {"content": "  /status  "},
        {"content": "@_user_1 /stop", "mentions": [{"key": "@_user_1", "name": "LoopX"}]},
        {"content": "@LoopX /new", "mentions": [{"key": "@_user_1", "name": "LoopX"}]},
        {"content": "@_user_1 /stop", "mentions": [{"key": "@_user_1", "name": "A"}, {"key": "@_user_1", "name": "B"}]},
        {"content": "hello /status"},
        {"content": 123},
    ]
    samples_cmd_input = [
        {"body": {"content": json.dumps({"text": "@_user_1 /stop"})},
         "mentions": [{"key": "@_user_1", "open_id": "ou_bot1"}]},
        {"body": {"content": json.dumps({"text": "@_user_1 /stop"})},
         "mentions": [{"key": "@_user_1", "open_id": "ou_other"}]},
        {"body": {"content": json.dumps({"text": "quote @_user_1 /stop"})},
         "mentions": [{"key": "@_user_1", "open_id": "ou_bot1"}]},
        {"body": {"content": "{not json"}},
    ]
    status_snapshots = [
        {"context_kind": "project", "audience": "owner", "grant": "workspace_write",
         "session_status": "ready", "active_turn_observation_available": True,
         "active_turn_status": None, "queued_count": 0,
         "workspace_path": "/ws", "executor_endpoint_id": "codex", "observed_at": "2026-01-01T00:00:00Z"},
        {"context_kind": "steward", "audience": "owner", "grant": "portfolio_read",
         "session_status": None, "active_turn_observation_available": False,
         "active_turn_status": None, "queued_count": 0, "authorized_commission_count": 2,
         "workspace_path": "/ws", "executor_endpoint_id": "codex", "observed_at": "2026-01-01T00:00:00Z"},
        {"context_kind": "project", "audience": "group", "grant": "workspace_read",
         "session_status": "ready", "active_turn_observation_available": True,
         "active_turn_status": "running", "queued_count": 1,
         "workspace_path": "/ws", "executor_endpoint_id": "codex", "observed_at": "2026-01-01T00:00:00Z"},
    ]

    results = []
    for _label, mod in _variants():
        cls = mod.LarkPrivateConversations
        results.append({
            "msg_types": [mod._msg_type(m) for m in samples_msg_type],
            "senders": [
                mod._valid_user_sender(m, c, s, msg_type=t, not_deleted=nd)
                for m, c, s, t, nd in samples_sender
            ],
            "hints": [cls._group_control_hint(e) for e in samples_hint],
            "cmd_inputs": [cls._group_text_command_input(m, obs) for m in samples_cmd_input],
            "status_texts": [
                (mod._status_text(s, help_requested=False), mod._status_text(s, help_requested=True))
                for s in status_snapshots
            ],
            "command_texts": [mod._command_text(c) for c in ("no_session", "stop_requested", "unknown")],
        })

    baseline = _assert_paired_equal(results)
    assert baseline["msg_types"] == ["text", "image", None]
    assert baseline["senders"] == [True, False, False, False]
    assert baseline["hints"] == ["/status", "/stop", "/new", "/stop", None, None]
    assert baseline["cmd_inputs"] == ["/stop", "@_user_1 /stop", "quote @_user_1 /stop", None]


def test_debounce_backoff_and_queue_discovery_equivalence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    outputs = []
    for label, mod in _variants():
        base_dir = tmp_path / label.replace("/", "_")
        deliv_dir = base_dir / "lark-private-deliveries"
        ext_dir = base_dir / "external-requests"
        deliv_dir.mkdir(parents=True)
        ext_dir.mkdir(parents=True)
        store = ChatSessionStore(base_dir)
        sid, tid = "s" * 32, "t" * 32
        store.create_session(
            goal_id="goal-eq", agent_id="codex", adapter_kind="codex_app_server",
            upstream_thread_id="th1", session_id=sid,
        )
        turn_path = store._turn_path(sid, tid)
        turn_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(turn_path, {"turn_id": tid, "status": "failed"})
        store._event_path(sid, tid).write_text('{"kind":"turn.failed"}\n', encoding="utf-8")

        ref_done = "a" * 24
        ref_triage = "b" * 24
        ref_unknown = "c" * 24
        _atomic_write_json(deliv_dir / f"{ref_done}.json", {
            "status": "delivered", "request_ref": ref_done, "session_id": sid, "turn_id": tid,
            "profile": "notes-app", "event": {"message_id": "om_done"},
        })
        _atomic_write_json(ext_dir / f"{ref_done}.json", {"request_ref": ref_done, "status": "accepted"})
        _atomic_write_json(deliv_dir / f"{ref_triage}.json", {
            "status": "captured", "request_ref": ref_triage, "triage": {"action": {"operation": "menu"}},
        })
        _atomic_write_json(deliv_dir / f"{ref_unknown}.json", {
            "status": "accepted", "request_ref": ref_unknown,
            "deliveries": {"terminal": {"started": True, "verified": False, "blocker": "provider_delivery_receipt_unknown"}},
        })

        repairs_dir = deliv_dir / "historical-answer-images"
        repairs_dir.mkdir()
        _atomic_write_json(repairs_dir / ("d" * 24 + ".json"), {"turn_id": "x"})

        transport = mod.LarkPrivateConversations.__new__(mod.LarkPrivateConversations)
        transport.root = deliv_dir
        transport._context_recovery_probe_at = {}
        transport._historical_image_probe_at = {}
        transport._delivery_discovery = PrivateDeliveryDiscovery(deliv_dir)
        transport._body_discovery = PrivateDeliveryDiscovery(deliv_dir)
        transport._web_projection_paths = lambda: []
        transport.core = SimpleNamespace(
            root=ext_dir,
            controller=SimpleNamespace(store=store),
            context_recovery_observer=lambda: (lambda _ref, **_kw: False),
        )

        clock = [1000.0]
        with _patch_clock(monkeypatch, mod, clock):
            repairs_1 = sorted(transport._planned_historical_repairs())
            body_paths = [p.name for p in transport.pending_body_paths()]
            delivery_1 = [p.name for p in transport.pending_delivery_paths()]
            done_path = deliv_dir / f"{ref_done}.json"
            expected_stagger = zlib.crc32(done_path.name.encode("utf-8")) % 60
            probe_1 = transport._context_recovery_probe_at[done_path]

            # Jump past initial stagger -> second noop increases exponential backoff (120 + stagger).
            clock[0] = probe_1 + 1.0
            transport.pending_delivery_paths()
            probe_2 = transport._context_recovery_probe_at[done_path]

            # Ready -> busy pulse retains backoff; closing session invalidates backoff.
            sess_path = store._session_path(sid)
            sess_data = json.loads(sess_path.read_text(encoding="utf-8"))
            sess_path.write_text(json.dumps({**sess_data, "status": "busy", "active_turn_id": tid}), encoding="utf-8")
            clock[0] = probe_2 - 5.0
            delivery_during_busy = [p.name for p in transport.pending_delivery_paths()]

            sess_path.write_text(json.dumps({**sess_data, "status": "closed"}), encoding="utf-8")
            transport.pending_delivery_paths()
            probe_after_close = transport._context_recovery_probe_at[done_path]

        outputs.append({
            "repairs": repairs_1,
            "body_paths": body_paths,
            "delivery_1": delivery_1,
            "probe_1_delta": probe_1 - 1000.0,
            "expected_stagger": expected_stagger,
            "probe_2_delta": probe_2 - (1000.0 + 60 + expected_stagger + 1.0),
            "delivery_during_busy": delivery_during_busy,
            "probe_after_close_reset": probe_after_close > clock[0],
        })

    baseline = _assert_paired_equal(outputs)
    assert baseline["body_paths"] == [f"{'b' * 24}.json"]
    assert baseline["delivery_1"] == [f"{'b' * 24}.json"]
    assert baseline["probe_1_delta"] == 60.0 + baseline["expected_stagger"]
    assert baseline["probe_2_delta"] == 120.0 + baseline["expected_stagger"]


def test_target_and_mention_resolution_equivalence(tmp_path: Path) -> None:
    outputs = []
    for label, mod in _variants():
        base_dir = tmp_path / label.replace("/", "_")
        _, _, provider, transport, binding = _build_env(mod, base_dir, audience="owner")
        profile_map = transport.profiles()
        configured = transport.configured_profile_apps()

        provider.add_message(
            "om_p2p_mention", "@Bot check", chat_type="p2p",
            mentions=[{"key": "@_user_1", "id": {"app_id": provider.app_id, "open_id": provider.bot_open_id}}],
        )
        provider.add_message("om_p2p_recalled", "old text", chat_type="p2p", deleted=True)
        record_mention = {
            "request_ref": identity_ref(binding["provider_ref"], "om_p2p_mention"),
            "profile": provider.profile,
            "binding_id": binding["binding_id"],
            "source": lark_private_source(provider_ref=binding["provider_ref"], event=provider.event("om_p2p_mention", "@Bot check")),
            "event": provider.event("om_p2p_mention", "@Bot check"),
        }
        record_recall = {
            "request_ref": identity_ref(binding["provider_ref"], "om_p2p_recalled"),
            "profile": provider.profile,
            "binding_id": binding["binding_id"],
            "source": lark_private_source(provider_ref=binding["provider_ref"], event=provider.event("om_p2p_recalled", "old text")),
            "event": provider.event("om_p2p_recalled", "old text"),
        }
        src_mention = transport._source_message(record_mention)
        src_recall_hidden = transport._source_message(record_recall, report_private_recall=False)
        src_recall_shown = transport._source_message(record_recall, report_private_recall=True)

        # Group topic root mention verification.
        grp_dir = tmp_path / f"{label.replace('/', '_')}_grp"
        _, _, grp_provider, grp_transport, grp_binding = _build_env(mod, grp_dir, audience="group")
        grp_provider.add_message(
            "om_root", "@Bot topic start", chat_type="group",
            mentions=[{"key": "@_user_1", "open_id": grp_provider.bot_open_id}],
        )
        grp_provider.add_message(
            "om_reply", "@_user_1 /status", chat_type="group", root_id="om_root", parent_id="om_root",
            mentions=[{"key": "@_user_1", "open_id": grp_provider.bot_open_id}],
        )
        grp_ev = grp_provider.event("om_reply", "@LoopX Bot /status", chat_type="group", root_id="om_root", parent_id="om_root")
        grp_record = {
            "request_ref": identity_ref(grp_binding["provider_ref"], "om_reply"),
            "profile": grp_provider.profile,
            "binding_id": grp_binding["binding_id"],
            "source": lark_private_source(provider_ref=grp_binding["provider_ref"], event=grp_ev),
            "event": grp_ev,
        }
        grp_msg = grp_transport._source_message(grp_record)
        auth_ok = grp_transport._source_authority(grp_record, grp_provider.profile, grp_provider.chat_id, "om_reply")

        outputs.append({
            "profiles_keys": sorted(profile_map.keys()),
            "configured": configured,
            "p2p_mention": src_mention.get("_native_mention") if src_mention else None,
            "recall_hidden": src_recall_hidden,
            "recall_shown": src_recall_shown,
            "grp_cmd_input": grp_msg.get("_command_input") if grp_msg else None,
            "auth_ok": auth_ok,
        })

    baseline = _assert_paired_equal(outputs)
    assert baseline["p2p_mention"] is True
    assert baseline["recall_hidden"] is None
    assert baseline["recall_shown"] == {"deleted": True}
    assert baseline["grp_cmd_input"] == "/status"
    assert baseline["auth_ok"] == ("LoopX Bot", True)


def test_admit_control_history_and_conflict_equivalence(tmp_path: Path) -> None:
    outputs = []
    for label, mod in _variants():
        base_dir = tmp_path / label.replace("/", "_")
        _, _, provider, transport, binding = _build_env(mod, base_dir, audience="owner", context_kind="steward")

        provider.add_message("om_status", "/status")
        status_res = transport.admit(provider.profile, provider.event("om_status", "/status"))

        # Redelivery with mutated content -> source_conflict.
        conflict_res = transport.admit(provider.profile, provider.event("om_status", "mutated content"))

        # Steward prefix command /delegate without valid tokens -> command_rejected.
        provider.add_message("om_delegate", "/delegate bad")
        delegate_res = transport.admit(provider.profile, provider.event("om_delegate", "/delegate bad"))

        # Unsupported message type -> command_recorded ("unsupported").
        provider.add_message("om_audio", "", msg_type="audio")
        audio_res = transport.admit(provider.profile, provider.event("om_audio", "", msg_type="audio"))

        # Group admitted topic control path + fallback.
        grp_dir = tmp_path / f"{label.replace('/', '_')}_grp"
        store, _, grp_provider, grp_transport, grp_binding = _build_env(mod, grp_dir, audience="group")
        grp_provider.add_message(
            "om_root", "@Bot start", chat_type="group",
            mentions=[{"key": "@_user_1", "open_id": grp_provider.bot_open_id}],
        )
        root_ref = identity_ref(grp_binding["provider_ref"], "om_root")
        root_ev = grp_provider.event("om_root", "@LoopX Bot start", chat_type="group")
        root_src = lark_private_source(provider_ref=grp_binding["provider_ref"], event=root_ev)
        _atomic_write_json(grp_transport.core.root / f"{root_ref}.json", {
            "request_ref": root_ref,
            "binding_id": grp_binding["binding_id"],
            "status": "accepted",
            "command": None,
            "session_id": "sess_grp_1",
            "source": root_src,
        })
        grp_provider.add_message(
            "om_stop", "@_user_1 /status", chat_type="group", root_id="om_root", parent_id="om_root",
            mentions=[{"key": "@_user_1", "open_id": grp_provider.bot_open_id}],
        )
        stop_ev = grp_provider.event(
            "om_stop", "/status", chat_type="group", root_id="om_root", parent_id="om_root",
        )
        grp_ctrl_res = grp_transport.admit(grp_provider.profile, stop_ev)

        outputs.append({
            "status_res": status_res,
            "conflict_res": conflict_res,
            "delegate_res": delegate_res,
            "audio_res": audio_res,
            "grp_ctrl_res": grp_ctrl_res,
        })

    baseline = _assert_paired_equal(outputs)
    assert baseline["status_res"] == {"status": "command_recorded"}
    assert baseline["conflict_res"] == {"status": "source_conflict"}
    assert baseline["delegate_res"] == {"status": "command_rejected"}
    assert baseline["audio_res"] == {"status": "command_recorded"}
    assert baseline["grp_ctrl_res"] == {"status": "command_recorded"}


def test_deliver_progress_fallback_and_ambiguous_recovery_equivalence(tmp_path: Path) -> None:
    outputs = []
    for label, mod in _variants():
        base_dir = tmp_path / label.replace("/", "_")
        _, _, provider, transport, binding = _build_env(mod, base_dir, audience="owner")
        provider.add_message("om_1", "Run check")
        ev = provider.event("om_1", "Run check")
        req_ref = identity_ref(binding["provider_ref"], "om_1")
        path = transport.root / f"{req_ref}.json"
        record = {
            "schema_version": "lark_private_chat_delivery_v0",
            "request_ref": req_ref,
            "profile": provider.profile,
            "binding_id": binding["binding_id"],
            "source": lark_private_source(provider_ref=binding["provider_ref"], event=ev),
            "event": ev,
            "deliveries": {},
            "status": "accepted",
        }
        _atomic_write_json(path, record)
        inbox_fn = lambda r=record: transport._inbox(r)  # noqa: E731

        # 1. Progress reply -> edit_limit on terminal -> fallback to fresh final send.
        assert transport._deliver(path, record, "progress", "⏳ **正在处理**", inbox=inbox_fn) is True
        provider.update_blocker = "provider_update_edit_limit"
        assert transport._deliver(path, record, "terminal", "Final answer!", inbox=inbox_fn) is True
        provider.update_blocker = None
        after_edit_limit = json.loads(path.read_text(encoding="utf-8"))

        # 2. Ambiguous started=True without attempt -> provider_delivery_receipt_unknown without resend.
        req_ref_2 = "e" * 24
        provider.add_message("om_2", "Check crash")
        ev_2 = provider.event("om_2", "Check crash")
        path_2 = transport.root / f"{req_ref_2}.json"
        record_2 = {
            "schema_version": "lark_private_chat_delivery_v0",
            "request_ref": req_ref_2,
            "profile": provider.profile,
            "binding_id": binding["binding_id"],
            "source": lark_private_source(provider_ref=binding["provider_ref"], event=ev_2),
            "event": ev_2,
            "deliveries": {"terminal": {"started": True, "text": "Crash body"}},
            "status": "accepted",
        }
        _atomic_write_json(path_2, record_2)
        writes_before = len(provider.writes)
        ambig_ok = transport._deliver(path_2, record_2, "terminal", "Crash body", inbox=lambda: transport._inbox(record_2))
        after_ambig = json.loads(path_2.read_text(encoding="utf-8"))

        outputs.append({
            "edit_limit_reached": after_edit_limit["stream"]["edit_limit_reached"],
            "terminal_verified": after_edit_limit["deliveries"]["terminal"]["verified"],
            "rejected_blocker": after_edit_limit["deliveries"]["terminal"]["rejected_update"]["blocker"],
            "writes_count": len(provider.writes),
            "ambig_ok": ambig_ok,
            "ambig_no_resend": len(provider.writes) == writes_before,
            "ambig_blocker": after_ambig["deliveries"]["terminal"]["blocker"],
        })

    baseline = _assert_paired_equal(outputs)
    assert baseline["edit_limit_reached"] is True
    assert baseline["terminal_verified"] is True
    assert baseline["rejected_blocker"] == "provider_update_edit_limit"
    assert baseline["ambig_ok"] is False
    assert baseline["ambig_no_resend"] is True
    assert baseline["ambig_blocker"] == "provider_delivery_receipt_unknown"


def test_deliver_completed_answer_image_backoff_and_return_inbox_equivalence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    outputs = []
    for label, mod in _variants():
        base_dir = tmp_path / label.replace("/", "_")
        store, bindings, provider, transport, binding = _build_env(mod, base_dir, audience="owner", context_kind="steward")
        provider.add_message("om_img", "Publish the HTML summary")
        ev = provider.event("om_img", "Publish the HTML summary")
        req_ref = identity_ref(binding["provider_ref"], "om_img")
        sid, tid = "1" * 32, "2" * 32
        src = lark_private_source(provider_ref=binding["provider_ref"], event=ev)
        selected = bindings.resolve(binding_id=binding["binding_id"], **src)
        session = store.create_session(
            goal_id="loopx-manager", agent_id="codex", adapter_kind="codex_app_server",
            upstream_thread_id="th_ret", session_id=sid,
            channel_id=selected["channel_id"],
            steward_context=selected["context"],
        )
        turn = {
            "schema_version": CHAT_TURN_SCHEMA_VERSION,
            "session_id": sid,
            "turn_id": tid,
            "client_turn_id": f"external-{req_ref}",
            "status": "completed",
            "response": {"message": "Done"},
        }
        _atomic_write_json(store._turn_path(sid, tid), turn)
        _atomic_write_json(transport.core.root / f"{req_ref}.json", {
            "request_ref": req_ref, "binding_id": binding["binding_id"], "source": src,
            "session_id": sid, "turn_id": tid, "status": "accepted", "command": None,
        })
        path = transport.root / f"{req_ref}.json"
        record = {
            "schema_version": "lark_private_chat_delivery_v0",
            "request_ref": req_ref, "binding_id": binding["binding_id"], "profile": provider.profile,
            "source": src, "session_id": sid, "turn_id": tid, "event": ev, "deliveries": {}, "status": "accepted",
        }
        _atomic_write_json(path, record)

        inbox_path, returned_rec = transport.return_inbox(
            route={"source_id": req_ref, "goal_id": "g1", "session_id": sid},
            session=session,
            turn=turn,
        )
        with pytest.raises(ValueError, match="original private request unavailable"):
            transport.return_inbox(
                route={"source_id": req_ref, "goal_id": "g1", "session_id": sid},
                session=session,
                turn={**turn, "client_turn_id": "invalid"},
            )

        clock = [5000.0]
        answer_text = "Guide\n\n![Diagram](local_diagram.png)"
        with _patch_clock(monkeypatch, mod, clock):
            first = transport._deliver_completed_answer(
                path, json.loads(path.read_text(encoding="utf-8")), answer_text,
                inbox=lambda: transport._inbox(record),
            )
            second = transport._deliver_completed_answer(
                path, json.loads(path.read_text(encoding="utf-8")), answer_text,
                inbox=lambda: transport._inbox(record),
            )
            clock[0] += 100.0  # Before 300s backoff expires -> skipped without incrementing retry_count.
            third = transport._deliver_completed_answer(
                path, json.loads(path.read_text(encoding="utf-8")), answer_text,
                inbox=lambda: transport._inbox(record),
            )
        final_row = json.loads(path.read_text(encoding="utf-8"))
        outputs.append({
            "inbox_name": inbox_path.name,
            "returned_ref": returned_rec["request_ref"],
            "steps": (first, second, third),
            "img_status": final_row["answer_images"]["status"],
            "retry_count": final_row["answer_images"]["retry_count"],
            "next_check_delta": final_row["answer_images"]["next_check_at"] - 5000.0,
        })

    baseline = _assert_paired_equal(outputs)
    assert baseline["steps"] == (False, False, False)
    assert baseline["img_status"] == "unverified"
    assert baseline["retry_count"] == 1
    assert baseline["next_check_delta"] == 300.0


def test_reconcile_request_recovery_and_terminal_delivery_equivalence(tmp_path: Path) -> None:
    outputs = []
    for label, mod in _variants():
        base_dir = tmp_path / label.replace("/", "_")
        store, bindings, provider, transport, binding = _build_env(mod, base_dir, audience="owner")

        # Case 1: captured request waiting on STEWARD_BINDING_ADOPTION_PENDING returns 0 without provider IO.
        req_wait = "a" * 24
        sid = "a" * 32
        ev_wait = provider.event("om_wait", "hello")
        src_wait = lark_private_source(provider_ref=binding["provider_ref"], event=ev_wait)
        selected_wait = bindings.resolve(binding_id=binding["binding_id"], **src_wait)
        sess_wait = store.create_session(
            goal_id=None, agent_id="codex", adapter_kind="codex_app_server",
            upstream_thread_id="th_wait", session_id=sid,
            channel_id=selected_wait["channel_id"],
            project_context=selected_wait["context"],
            executor_endpoint_id="antigravity",
        )
        sess_wait["manager_executor_allocation"] = {"selection_policy": "pinned"}
        _atomic_write_json(store._session_path(sid), sess_wait)
        wait_path = transport.root / f"{req_wait}.json"
        _atomic_write_json(wait_path, {
            "schema_version": "lark_private_chat_delivery_v0",
            "request_ref": req_wait, "profile": provider.profile, "binding_id": binding["binding_id"],
            "source": src_wait, "session_id": sid, "turn_id": None, "event": ev_wait,
            "deliveries": {}, "status": "captured", "response_code": STEWARD_BINDING_ADOPTION_PENDING,
        })
        _atomic_write_json(transport.core.root / f"{req_wait}.json", {
            "request_ref": req_wait, "binding_id": binding["binding_id"], "source": src_wait,
            "session_id": sid, "turn_id": None, "status": "prepared",
            "response_code": STEWARD_BINDING_ADOPTION_PENDING,
        })
        calls_before = len(provider.calls)
        wait_rc = transport.reconcile_request(wait_path)
        wait_no_io = len(provider.calls) == calls_before

        # Case 2: accepted -> completed turn reconciliation delivers terminal reply and marks delivered.
        provider.add_message("om_done", "/status")
        assert transport.admit(provider.profile, provider.event("om_done", "/status"))["status"] == "command_recorded"
        done_ref = identity_ref(binding["provider_ref"], "om_done")
        done_path = transport.root / f"{done_ref}.json"
        done_rc = transport.reconcile_request(done_path)
        done_row = json.loads(done_path.read_text(encoding="utf-8"))

        # Case 3: accepted -> failed turn delivers manager_failure_reply + "原会话已保留。".
        provider.add_message("om_fail", "Do work")
        assert transport.admit(provider.profile, provider.event("om_fail", "Do work"))["status"] == "durably_accepted"
        fail_ref = identity_ref(binding["provider_ref"], "om_fail")
        fail_path = transport.root / f"{fail_ref}.json"
        fail_rec = json.loads(fail_path.read_text(encoding="utf-8"))
        store.update_turn(fail_rec["session_id"], fail_rec["turn_id"], status="failed", error_code="usage_limit_exceeded")
        fail_rc = transport.reconcile_request(fail_path)
        fail_row = json.loads(fail_path.read_text(encoding="utf-8"))

        outputs.append({
            "wait_rc": wait_rc,
            "wait_no_io": wait_no_io,
            "done_rc": done_rc,
            "done_status": done_row["status"],
            "fail_rc": fail_rc,
            "fail_status": fail_row["status"],
            "fail_text_kept": "原会话已保留。" in fail_row["deliveries"]["terminal"]["text"],
        })

    baseline = _assert_paired_equal(outputs)
    assert baseline["wait_rc"] == 0 and baseline["wait_no_io"] is True
    assert baseline["done_rc"] == 1 and baseline["done_status"] == "delivered"
    assert baseline["fail_rc"] == 1 and baseline["fail_status"] == "delivered" and baseline["fail_text_kept"] is True


@pytest.mark.parametrize("timing_enabled", [False, True], ids=["timing_disabled", "timing_enabled"])
def test_held_history_answer_secondary_read_timing_equivalence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    timing_enabled: bool,
) -> None:
    import hashlib

    if timing_enabled:
        monkeypatch.setenv("LOOPX_LARK_EVENT_TIMING", "1")
    else:
        monkeypatch.delenv("LOOPX_LARK_EVENT_TIMING", raising=False)

    outputs = []
    for label, mod in _variants():
        base_dir = tmp_path / label.replace("/", "_")
        _, _, provider, transport, _ = _build_env(
            mod, base_dir, audience="owner", context_kind="steward"
        )
        provider.add_message("om_hist_sec_fail", "Explain previous result")
        provider.add_message("om_hist_prepared", "Explain prepared hold")
        original_runner = transport.runner
        api_get_calls = 0

        def failing_secondary_runner(
            args: list[str], cwd: Path | None = None, timeout: float | None = None
        ) -> dict[str, Any]:
            nonlocal api_get_calls
            if (
                "api" in args
                and "GET" in args
                and any("om_hist_sec_fail" in str(item) for item in args)
            ):
                api_get_calls += 1
                if api_get_calls >= 2:
                    return {"returncode": 1, "stdout": "", "stderr": "secondary envelope read failed"}
            return dict(original_runner(args, cwd, timeout))

        monkeypatch.setattr(transport, "runner", failing_secondary_runner)
        admission_calls = 0
        original_admit_history = transport.core.admit_history_answer

        def counting_admit_history(*args: Any, **kwargs: Any) -> dict[str, Any]:
            nonlocal admission_calls
            admission_calls += 1
            return dict(original_admit_history(*args, **kwargs))

        monkeypatch.setattr(transport.core, "admit_history_answer", counting_admit_history)
        capsys.readouterr()

        held_sec = transport.admit(
            provider.profile,
            provider.event("om_hist_sec_fail", "Explain previous result"),
            history_review=True,
        )
        sec_calls = admission_calls
        sec_out = capsys.readouterr().out
        sec_ref = hashlib.sha256(
            json.dumps([provider.profile, "om_hist_sec_fail"]).encode()
        ).hexdigest()
        sec_stages = [
            row["stage"]
            for line in sec_out.splitlines()
            if (row := json.loads(line)).get("schema_version") == "loopx_lark_event_timing_v0"
            and row.get("event_ref") == sec_ref
        ]

        admission_calls = 0
        held_prep = transport.admit(
            provider.profile,
            provider.event("om_hist_prepared", "Explain prepared hold"),
            history_review=True,
        )
        prep_calls = admission_calls
        prep_out = capsys.readouterr().out
        prep_ref = hashlib.sha256(
            json.dumps([provider.profile, "om_hist_prepared"]).encode()
        ).hexdigest()
        prep_stages = [
            row["stage"]
            for line in prep_out.splitlines()
            if (row := json.loads(line)).get("schema_version") == "loopx_lark_event_timing_v0"
            and row.get("event_ref") == prep_ref
        ]

        outputs.append(
            {
                "held_sec": held_sec,
                "sec_calls": sec_calls,
                "sec_stages": sec_stages,
                "held_prep": held_prep,
                "prep_calls": prep_calls,
                "prep_stages": prep_stages,
            }
        )

    baseline = _assert_paired_equal(outputs)
    assert baseline["held_sec"] == {"status": "history_answer_held"}
    assert baseline["sec_calls"] == 0
    assert baseline["held_prep"] == {"status": "history_answer_held"}
    assert baseline["prep_calls"] == 1
    if timing_enabled:
        expected_stages = [
            "admission_started",
            "binding_hint_ready",
            "capture_lock_acquired",
            "core_admission_started",
        ]
        assert baseline["sec_stages"] == expected_stages
        assert baseline["prep_stages"] == expected_stages
        assert "core_admission_returned" not in baseline["sec_stages"]
        assert "core_admission_returned" not in baseline["prep_stages"]
    else:
        assert baseline["sec_stages"] == []
        assert baseline["prep_stages"] == []
