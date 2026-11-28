"""Fail-closed typing, export, and recovery seam contracts for external conversations."""

from __future__ import annotations

import ast
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import TYPE_CHECKING, cast

import pytest

from loopx.capabilities.native_chat import external_conversations
from loopx.capabilities.native_chat.external_conversations import (
    ChatExternalConversations,
    ExternalBacklogRequestDict,
    ExternalRequestDict,
    NativeResultRecoveryDict,
    QUOTA_BACKLOG_BATCH_SIZE,
    RejectedMentionReplayDict,
    as_external_backlog_request,
    as_external_request,
)
from loopx.capabilities.native_chat.external_conversations_contracts import Payload
from loopx.chat_store import ChatSessionStore, _atomic_write_json

if TYPE_CHECKING:
    from loopx.chat_runtime import ChatRuntimeController

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MODULE_FILES: tuple[Path, ...] = (
    _REPO_ROOT / "loopx/capabilities/native_chat/external_conversations.py",
    _REPO_ROOT / "loopx/capabilities/native_chat/external_conversations_recovery.py",
    _REPO_ROOT / "loopx/capabilities/native_chat/external_conversations_contracts.py",
    _REPO_ROOT / "loopx/capabilities/native_chat/external_mention_recovery.py",
    _REPO_ROOT / "tests/test_external_conversations_contract.py",
)

_CALLER_ERROR_CAPS: dict[str, int] = {
    "loopx/capabilities/native_chat/external_conversations.py": 0,
    "loopx/capabilities/native_chat/external_conversations_recovery.py": 0,
    "loopx/capabilities/native_chat/external_conversations_contracts.py": 0,
    "loopx/capabilities/native_chat/external_mention_recovery.py": 0,
    "loopx/capabilities/native_chat/result_review_evidence.py": 0,
    "loopx/extensions/lark/private_conversations.py": 2,
    "loopx/extensions/lark/private_delivery_discovery.py": 7,
    "loopx/extensions/lark/owner_attention.py": 4,
    "loopx/extensions/lark/steward_reports.py": 6,
    "loopx/extensions/lark/private_progress.py": 8,
    "loopx/extensions/lark/private_steward_triage.py": 2,
    "loopx/extensions/lark/private_web_projection.py": 23,
    "loopx/chat_runtime.py": 25,
    "loopx/chat_server.py": 22,
    "loopx/chat/coordination.py": 1,
    "loopx/chat/session_open.py": 28,
    "loopx/capabilities/native_chat/steward_commands.py": 1,
    "loopx/capabilities/native_chat/codex_adapter.py": 23,
}

_FORBIDDEN_PRIVATE_CONVERSATIONS_REGRESSIONS: tuple[str, ...] = (
    "read_request",
    "recover_failed_request",
    "ExternalBacklogRequestDict",
    "ExternalRequestDict",
    "ExternalSourceDict",
    "NativeResultRecoveryDict",
    "native_result_recovery",
    "Payload",
)

_SUMMARY_SUCCESS_RE = re.compile(r"^Success: no issues found in (\d+) source files?$")
_SUMMARY_FOUND_RE = re.compile(
    r"^Found \d+ errors? in \d+ files? \(checked (\d+) source files?\)$"
)


def _parse_mypy_contract_output(
    proc: subprocess.CompletedProcess[str], *, paths: Sequence[str]
) -> dict[str, list[str]]:
    """Require complete mypy execution and parse per-file diagnostics."""
    if proc.returncode not in (0, 1):
        raise AssertionError(
            f"mypy probe aborted with rc={proc.returncode}: "
            f"stderr={proc.stderr!r}, stdout={proc.stdout!r}"
        )
    if proc.stderr.strip():
        raise AssertionError(f"mypy probe produced non-empty stderr: {proc.stderr!r}")

    lines = [line.rstrip() for line in proc.stdout.splitlines() if line.strip()]
    if not lines:
        raise AssertionError("mypy probe returned empty stdout without summary line")

    summary = lines[-1]
    checked: int | None = None
    for pattern in (_SUMMARY_SUCCESS_RE, _SUMMARY_FOUND_RE):
        match = pattern.match(summary)
        if match is not None:
            checked = int(match.group(1))
            break
    if checked is None:
        raise AssertionError(f"mypy probe exited before summary line: {summary!r}")
    if checked != len(paths):
        raise AssertionError(
            f"mypy probe checked {checked} files, expected {len(paths)}: {summary!r}"
        )

    errors_by_file: dict[str, list[str]] = {path: [] for path in paths}
    for line in lines[:-1]:
        if ": error:" not in line:
            continue
        file_part = line.split(":", 1)[0]
        if file_part not in errors_by_file:
            raise AssertionError(f"unexpected diagnostic file {file_part!r}: {line!r}")
        errors_by_file[file_part].append(line)
    return errors_by_file


def _run_mypy_contract_probe(
    paths: Sequence[str], *, extra_env: Mapping[str, str] | None = None, strict: bool = False
) -> dict[str, list[str]]:
    """Run fail-closed mypy check over callers and fail on partial/aborted runs."""
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    args = [sys.executable, "-m", "mypy"]
    if strict:
        args.append("--strict")
    args.extend(["--no-incremental", "--follow-imports=silent", *paths])
    proc = subprocess.run(
        args,
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return _parse_mypy_contract_output(proc, paths=paths)


def _collect_mypy_contract_errors(
    paths: Sequence[str], *, extra_env: Mapping[str, str] | None = None, strict: bool = False
) -> dict[str, list[str]]:
    return _run_mypy_contract_probe(paths, extra_env=extra_env, strict=strict)


def test_mypy_contract_probe_rejects_early_exit_or_aborted_run(tmp_path: Path) -> None:
    sample_paths = ("a.py", "b.py")
    with pytest.raises(AssertionError, match="aborted with rc=2"):
        _parse_mypy_contract_output(
            subprocess.CompletedProcess(["mypy"], 2, stdout="", stderr=""),
            paths=sample_paths,
        )
    with pytest.raises(AssertionError, match="non-empty stderr"):
        _parse_mypy_contract_output(
            subprocess.CompletedProcess(
                ["mypy"], 1, stdout="Success: no issues found in 2 source files\n", stderr="MYPYPATH error"
            ),
            paths=sample_paths,
        )
    with pytest.raises(AssertionError, match="before summary line"):
        _parse_mypy_contract_output(
            subprocess.CompletedProcess(
                ["mypy"], 1, stdout="Found 1 error in 1 file (errors prevented further checking)\n", stderr=""
            ),
            paths=sample_paths,
        )
    with pytest.raises(AssertionError, match="checked 1 files, expected 2"):
        _parse_mypy_contract_output(
            subprocess.CompletedProcess(
                ["mypy"], 0, stdout="Success: no issues found in 1 source file\n", stderr=""
            ),
            paths=sample_paths,
        )

    broken_stubs = tmp_path / "stubs"
    (broken_stubs / "loopx").mkdir(parents=True)
    (broken_stubs / "loopx" / "__init__.pyi").write_text("def broken(:\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="aborted with rc=2"):
        _run_mypy_contract_probe(
            ["loopx/capabilities/native_chat/external_conversations.py"],
            extra_env={"MYPYPATH": str(broken_stubs)},
        )


def test_external_conversations_preserves_caller_typing_contracts() -> None:
    paths = tuple(_CALLER_ERROR_CAPS)
    errors_by_file = _collect_mypy_contract_errors(paths)
    for path, max_errors in _CALLER_ERROR_CAPS.items():
        observed = errors_by_file[path]
        assert len(observed) <= max_errors, (
            f"{path} exceeded caller mypy budget {max_errors} with {len(observed)}: {observed}"
        )

    private_list = errors_by_file["loopx/extensions/lark/private_conversations.py"]
    private_errors = "\n".join(private_list)
    for forbidden in _FORBIDDEN_PRIVATE_CONVERSATIONS_REGRESSIONS:
        assert forbidden not in private_errors, (
            f"private_conversations.py regressed on {forbidden!r}:\n{private_errors}"
        )
    for line in private_list:
        if (
            ("[arg-type]" in line and ("str | None" in line or 'type "object"' in line))
            or ("[union-attr]" in line and 'Item "None" of "dict[str, Any] | None"' in line)
            or ("[index]" in line and 'of type "object"' in line)
        ):
            raise AssertionError(f"unexpected nullability diagnostic in private_conversations.py: {line}")


def test_external_conversations_surface_parity_zero_any_and_size() -> None:
    for path in _MODULE_FILES:
        src = path.read_text(encoding="utf-8")
        lines = src.splitlines()
        assert len(lines) < 1000, f"{path} exceeds 1000 LOC ({len(lines)})"
        assert not any(re.search(r"#\s*" + "type:" + r"\s*ignore", line) for line in lines), (
            f"{path} contains type ignore comment"
        )
        tree = ast.parse(src)
        nofollow_nodes = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr == "O_" + "NOFOLLOW"
        ]
        assert not nofollow_nodes, f"{path} contains bare os.O_NOFOLLOW attribute access"
        any_nodes = [
            node
            for node in ast.walk(tree)
            if (isinstance(node, ast.Name) and node.id == "Any")
            or (isinstance(node, ast.Attribute) and node.attr == "Any")
        ]
        assert not any_nodes, f"{path} still references typing.Any ({len(any_nodes)} nodes)"

    required_compat_exports = (
        "ChatExternalConversations",
        "ExternalBacklogRequestDict",
        "ExternalRequestDict",
        "ExternalSourceDict",
        "NativeResultRecoveryDict",
        "RejectedMentionReplayDict",
        "as_external_backlog_request",
        "as_external_request",
        "QUOTA_BACKLOG_BATCH_SIZE",
        "STEWARD_BINDING_ADOPTION_PENDING",
        "datetime",
        "timezone",
        "_atomic_write_json",
        "_read_json",
        "exclusive_file_lock",
        "try_exclusive_file_lock",
        "result_review_evidence",
        "checked_session_home",
        "effect_runtime",
        "observed_turn_event_summary",
        "_RecoveryDiscoveryReads",
    )
    for export in required_compat_exports:
        assert hasattr(external_conversations, export), f"missing compatibility export: {export}"

    required_methods = (
        "quota_backlog_observer",
        "quota_backlog_pending",
        "context_recovery_observer",
        "context_recovery_candidate",
        "recover_failed_request",
        "native_result_reply",
        "record_native_result_delivery",
        "history_answer_correction_candidate",
        "admit_history_answer",
        "recovery_context",
        "prepare_metadata_review_input",
        "history_answer_context",
        "execute_turn",
        "validate_metadata_review_response",
        "prepare_continuation_input",
        "queued_recovery_context",
        "capture_native_mention",
        "admit",
        "pending",
        "source_controls",
        "read_request",
        "read_backlog_requests",
        "record_delivery",
        "recover",
    )
    for method in required_methods:
        assert callable(getattr(ChatExternalConversations, method, None)), f"missing method: {method}"

    req = ExternalRequestDict({
        "request_ref": "a" * 24,
        "binding_id": "b" * 24,
        "source": {"source_ref": "s" * 24},
        "native_result_recovery": {"turn_id": "t-1", "message_text": "hello"},
        "rejected_mention_replay": {"reason": "none"},
    })
    assert isinstance(req, ExternalRequestDict) and isinstance(req, dict)
    req.update(status="accepted", session_id="sess-1", turn_id="t-1", response="ok")
    assert req["status"] == "accepted"
    assert req["source"]["source_ref"] == "s" * 24
    raw_rec = req.get("native_result_recovery")
    assert isinstance(raw_rec, dict) and raw_rec["turn_id"] == "t-1"
    typed_rec = NativeResultRecoveryDict({"turn_id": "t-1", "message_text": "hello"})
    typed_replay = RejectedMentionReplayDict({"target_turn_id": "t-1", "original_response": None})
    assert typed_rec["turn_id"] == "t-1" and typed_replay["target_turn_id"] == "t-1"
    copied = deepcopy(req)
    assert isinstance(copied, ExternalRequestDict) and copied["session_id"] == "sess-1"
    narrowed = as_external_request(dict(req))
    assert isinstance(narrowed, dict) and narrowed["request_ref"] == "a" * 24
    backlog = ExternalBacklogRequestDict({
        "request_ref": "a" * 24,
        "binding_id": "b" * 24,
        "source": {"source_ref": "s" * 24},
        "session_id": None,
        "turn_id": None,
        "status": "command_completed",
        "command": "help",
        "created_at": "2026-01-01T00:00:00+00:00",
    })
    assert isinstance(backlog, ExternalBacklogRequestDict) and isinstance(backlog, dict)
    assert isinstance(as_external_backlog_request(dict(backlog)), dict)


class _StubBindings:
    controller: object

    def __init__(self) -> None:
        self.controller = None

    def resolve(self, *, binding_id: str, **kwargs: object) -> Payload:
        provider_ref = "d" * 24
        operator_ref = "e" * 24
        project_ref = "c" * 24
        return {
            "binding": {
                "schema_version": "loopx_chat_conversation_binding_v0",
                "binding_id": binding_id,
                "context_kind": "project",
                "project_ref": project_ref,
                "executor_endpoint_id": "codex",
                "grant": "workspace_write",
                "enabled": True,
                "transport_ref": "demo",
                "provider_ref": provider_ref,
                "operator_ref": operator_ref,
            },
            "context": {
                "kind": "project_workspace",
                "project_ref": project_ref,
                "workspace_path": str(_REPO_ROOT),
                "audience": "bound_owner",
                "grant": "workspace_write",
                "binding_id": binding_id,
                "source_ref": "b" * 24,
                "provider_ref": provider_ref,
                "operator_ref": operator_ref,
            },
            "channel_id": "chan-1",
        }

    def ensure_delivery_scope(self, selected: Payload) -> None:
        return None


class _StubProjectContexts:
    def __init__(self) -> None:
        self.conversation_bindings = _StubBindings()


class _StubController:
    def __init__(self, store: ChatSessionStore) -> None:
        self.store = store
        self.project_contexts = _StubProjectContexts()
        self.capabilities: dict[str, object] = {}

    def open_session(
        self,
        *,
        goal_id: str | None,
        agent_id: str,
        work_dir: Path,
        objective: str,
        mode: str,
        conversation_binding_id: str,
        source_context: Payload,
    ) -> tuple[Payload, bool]:
        session = self.store.create_session(
            goal_id=goal_id or "g-1",
            agent_id=agent_id,
            executor_endpoint_id=agent_id,
            adapter_kind="codex_app_server",
            upstream_thread_id="thread-1",
            upstream_mode="chat",
            channel_id="chan-1",
        )
        return cast(Payload, session), True

    def enqueue_turn(
        self,
        *,
        session_id: str,
        client_turn_id: str,
        message: str,
        attachments: list[Payload] | None = None,
        work_dir: Path | None = None,
        objective: str = "",
        origin: str = "lark",
        external_agent_target: Payload | None = None,
    ) -> tuple[Payload, bool]:
        turn, created = self.store.create_turn(
            session_id, client_turn_id=client_turn_id, message=message
        )
        self.store.update_turn(session_id, str(turn["turn_id"]), status="queued")
        loaded = self.store.load_turn(session_id, str(turn["turn_id"])) or turn
        return cast(Payload, loaded), created


def test_external_conversations_recovery_and_monkeypatch_seams(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ChatSessionStore(tmp_path, startup_maintenance=False)
    core = ChatExternalConversations(cast("ChatRuntimeController", _StubController(store)))
    core.root.mkdir(parents=True, exist_ok=True)

    ref_a = "a" * 24
    ref_b = "b" * 24
    created_at = "2026-01-01T00:00:00+00:00"
    stop_created_at = "2026-01-01T00:01:00+00:00"
    _atomic_write_json(core.root / f"{ref_a}.json", {
        "request_ref": ref_a,
        "binding_id": "bind-1",
        "source": {"source_ref": "src-1"},
        "status": "prepared",
        "capture_origin": "provider_history",
        "command": None,
        "created_at": created_at,
        "message": "m1",
    })
    _atomic_write_json(core.root / f"{ref_b}.json", {
        "request_ref": ref_b,
        "binding_id": "bind-1",
        "source": {"source_ref": "src-1"},
        "status": "command_completed",
        "command": "stop",
        "created_at": stop_created_at,
        "message": "/stop",
    })

    loaded = core.read_request(ref_a)
    assert isinstance(loaded, dict)
    assert loaded["source"]["source_ref"] == "src-1"
    assert [row["request_ref"] for row in core.pending()] == [ref_a, ref_b]

    backlog_rows = list(core.read_backlog_requests([ref_a, ref_b], defer_recovery=False))
    assert len(backlog_rows) == 2
    assert all(isinstance(row, dict) for row in backlog_rows)

    fromisoformat_calls: list[str] = []

    class _CountingDatetime(datetime):
        @classmethod
        def fromisoformat(cls, date_string: str) -> _CountingDatetime:
            fromisoformat_calls.append(date_string)
            parsed = super().fromisoformat(date_string)
            return cls(
                parsed.year,
                parsed.month,
                parsed.day,
                parsed.hour,
                parsed.minute,
                parsed.second,
                parsed.microsecond,
                tzinfo=parsed.tzinfo,
            )

    monkeypatch.setattr(external_conversations, "datetime", _CountingDatetime)
    core.recover()
    observer = core.quota_backlog_observer(use_cache=True)
    assert observer([]) == []
    with pytest.raises(ValueError, match="at most 64"):
        observer([{} for _ in range(QUOTA_BACKLOG_BATCH_SIZE + 1)])

    session = store.create_session(
        goal_id="g-1",
        agent_id="codex",
        executor_endpoint_id="codex",
        adapter_kind="codex_app_server",
        upstream_thread_id="t-1",
        upstream_mode="chat",
    )
    turn, _ = store.create_turn(session["session_id"], client_turn_id="c-1", message="hello")
    store.update_turn(
        session["session_id"],
        turn["turn_id"],
        status="failed",
        error_code="usage_limit_exceeded",
        response=None,
    )
    store.update_session(session["session_id"], active_turn_id=None)
    row_for_quota = {
        "request_ref": "c" * 24,
        "binding_id": "bind-1",
        "source": {"source_ref": "src-1"},
        "session_id": session["session_id"],
        "turn_id": turn["turn_id"],
        "command": None,
        "created_at": created_at,
        "delivery_verified": False,
        "status": "accepted",
    }
    assert core.quota_backlog_observer(source_controls=list)([row_for_quota]) == [True]
    assert observer([row_for_quota]) == [False]
    assert fromisoformat_calls == [stop_created_at]


def test_read_request_and_backlog_reject_unsafe_str_narrowing(tmp_path: Path) -> None:
    contracts_rel = "loopx/capabilities/native_chat/external_conversations_contracts.py"
    external_rel = "loopx/capabilities/native_chat/external_conversations.py"

    turn_id_strip = tmp_path / "unsafe_turn_id_strip.py"
    turn_id_strip.write_text(
        "from loopx.capabilities.native_chat.external_conversations import ChatExternalConversations\n"
        "\n"
        "def consume(core: ChatExternalConversations, ref: str) -> str:\n"
        '    return core.read_request(ref)["turn_id"].strip()\n',
        encoding="utf-8",
    )
    turn_errors = _run_mypy_contract_probe(
        [contracts_rel, external_rel, str(turn_id_strip)], strict=True
    )[str(turn_id_strip)]
    assert any("union-attr" in line and '"None"' in line for line in turn_errors), turn_errors

    backlog_msg_strip = tmp_path / "unsafe_backlog_message_strip.py"
    backlog_msg_strip.write_text(
        "from loopx.capabilities.native_chat.external_conversations import ChatExternalConversations\n"
        "\n"
        "def consume_backlog(core: ChatExternalConversations, ref: str) -> str:\n"
        "    item = next(core.read_backlog_requests([ref]))\n"
        '    return item["message"].strip()\n',
        encoding="utf-8",
    )
    backlog_errors = _run_mypy_contract_probe(
        [contracts_rel, external_rel, str(backlog_msg_strip)], strict=True
    )[str(backlog_msg_strip)]
    assert any("[call-overload]" in line for line in backlog_errors), backlog_errors

    context_rec = tmp_path / "unsafe_context_recovery_access.py"
    context_rec.write_text(
        "from loopx.capabilities.native_chat.external_conversations import ChatExternalConversations\n"
        "\n"
        "def consume_recovery(core: ChatExternalConversations, ref: str) -> object:\n"
        '    return core.read_request(ref)["context_recovery"].get("x")\n',
        encoding="utf-8",
    )
    rec_errors = _run_mypy_contract_probe(
        [contracts_rel, external_rel, str(context_rec)], strict=True
    )[str(context_rec)]
    assert any("[call-overload]" in line for line in rec_errors), rec_errors

    absent_return = tmp_path / "unsafe_response_return.py"
    absent_return.write_text(
        "from loopx.capabilities.native_chat.external_conversations import ChatExternalConversations\n"
        "\n"
        "def read_response(core: ChatExternalConversations, ref: str) -> str | None:\n"
        '    resp = core.read_request(ref)["response"]\n'
        "    return None if resp is None else resp\n"
        "\n"
        "def read_provider(core: ChatExternalConversations, ref: str) -> str:\n"
        '    return core.read_request(ref)["source"]["provider_ref"]\n'
        "\n"
        "def read_topic(core: ChatExternalConversations, ref: str) -> str | None:\n"
        '    return core.read_request(ref)["source"]["topic_ref"]\n',
        encoding="utf-8",
    )
    absent_return_errors = _run_mypy_contract_probe(
        [contracts_rel, external_rel, str(absent_return)], strict=True
    )[str(absent_return)]
    assert sum("[call-overload]" in line or "[index]" in line for line in absent_return_errors) == 3, absent_return_errors

    absent_attr = tmp_path / "unsafe_absent_key_methods.py"
    absent_attr.write_text(
        "from loopx.capabilities.native_chat.external_conversations import ChatExternalConversations\n"
        "\n"
        "def touch_absent(core: ChatExternalConversations, ref: str) -> tuple[str, ...]:\n"
        "    req = core.read_request(ref)\n"
        "    return (\n"
        '        req["origin"].strip(),\n'
        '        core.admit(binding_id="", source={"source_ref": ""}, request_ref="", message="")["origin"].strip(),\n'
        '        req["client_turn_id"].strip(),\n'
        '        req["history_answer_hold"].strip(),\n'
        '        req["profile"].strip(),\n'
        '        req["response"].strip(),\n'
        '        req["response_code"].strip(),\n'
        '        req["source"]["provider_ref"].strip(),\n'
        '        req["source"]["topic_ref"].strip(),\n'
        "    )\n",
        encoding="utf-8",
    )
    absent_attr_errors = _run_mypy_contract_probe(
        [contracts_rel, external_rel, str(absent_attr)], strict=True
    )[str(absent_attr)]
    assert sum("[call-overload]" in line or "[index]" in line for line in absent_attr_errors) == 9, absent_attr_errors

    absent_bool = tmp_path / "unsafe_boolean_absent_check.py"
    absent_bool.write_text(
        "from loopx.capabilities.native_chat.external_conversations import ChatExternalConversations\n"
        "\n"
        "def has_response(core: ChatExternalConversations, ref: str) -> bool:\n"
        '    return core.read_request(ref)["response"] is not None\n'
        "\n"
        "def check_absent_booleans(core: ChatExternalConversations, ref: str) -> tuple[bool, ...]:\n"
        "    req = core.read_request(ref)\n"
        "    item = next(core.read_backlog_requests([ref]))\n"
        "    return (\n"
        '        req["origin"] is not None,\n'
        '        bool(req["origin"]),\n'
        '        bool(req["response"]),\n'
        '        req["response_code"] is not None,\n'
        '        req["client_turn_id"] is not None,\n'
        '        req["history_answer_hold"] is not None,\n'
        '        req["profile"] is not None,\n'
        '        req["context_recovery"] is not None,\n'
        '        req["source"]["provider_ref"] is not None,\n'
        '        req["source"]["topic_ref"] is not None,\n'
        '        item["message"] is not None,\n'
        '        item["response"] is not None,\n'
        "    )\n",
        encoding="utf-8",
    )
    absent_bool_errors = _run_mypy_contract_probe(
        [contracts_rel, external_rel, str(absent_bool)], strict=True
    )[str(absent_bool)]
    assert sum(
        ("[call-overload]" in line or "[index]" in line) for line in absent_bool_errors
    ) == 13, absent_bool_errors

    unguarded_get = tmp_path / "unguarded_typed_get_strip.py"
    unguarded_get.write_text(
        "from loopx.capabilities.native_chat.external_conversations import (\n"
        "    ChatExternalConversations,\n"
        "    RejectedMentionReplayDict,\n"
        ")\n"
        "\n"
        "def read_unstripped(\n"
        "    core: ChatExternalConversations, ref: str, replay: RejectedMentionReplayDict\n"
        ") -> tuple[object, ...]:\n"
        "    req = core.read_request(ref)\n"
        "    item = next(core.read_backlog_requests([ref]))\n"
        "    return (\n"
        '        req.get("response").strip(),\n'
        '        req.get("turn_id", "").strip(),\n'
        '        req.get("session_id", "").strip(),\n'
        '        req.get("command", "").strip(),\n'
        '        req.get("response", "").strip(),\n'
        '        req.get("origin", "").strip(),\n'
        '        req.get("context_recovery", {}).get("x"),\n'
        '        req.get("agent_target", {}).get("x"),\n'
        '        req["source"].get("topic_ref", "").strip(),\n'
        '        item.get("turn_id", "").strip(),\n'
        '        item.get("response", "").strip(),\n'
        '        replay.get("original_response", "").strip(),\n'
        "    )\n",
        encoding="utf-8",
    )
    unguarded_get_errors = _run_mypy_contract_probe(
        [contracts_rel, external_rel, str(unguarded_get)], strict=True
    )[str(unguarded_get)]
    assert sum("union-attr" in line and '"None"' in line for line in unguarded_get_errors) == 12, unguarded_get_errors

    safe_narrowing = tmp_path / "safe_request_and_backlog.py"
    safe_narrowing.write_text(
        "from loopx.capabilities.native_chat.external_conversations import ChatExternalConversations\n"
        "\n"
        "def has_response_safely(core: ChatExternalConversations, ref: str) -> bool:\n"
        '    return core.read_request(ref).get("response") is not None\n'
        "\n"
        "def consume_safely(core: ChatExternalConversations, ref: str) -> str:\n"
        "    req = core.read_request(ref)\n"
        '    tid = req["turn_id"]\n'
        '    tid_or_empty = (req.get("turn_id") or "").strip()\n'
        '    tid_default = req.get("turn_id", "")\n'
        "    tid_default_hint = tid_default.strip() if tid_default is not None else ''\n"
        '    status_default = req.get("status", "unknown").strip()\n'
        '    ctx_rec = req.get("context_recovery")\n'
        "    rec_hint = str(ctx_rec.get('x')) if ctx_rec is not None else ''\n"
        '    target_hint = str((req.get("agent_target") or {}).get("x"))\n'
        '    resp = req.get("response")\n'
        "    resp_hint = resp.strip() if resp is not None else ''\n"
        '    code = req.get("response_code")\n'
        "    code_hint = code.strip() if code is not None else ''\n"
        '    provider = req["source"].get("provider_ref")\n'
        "    provider_hint = provider.strip() if provider is not None else ''\n"
        '    topic = req["source"].get("topic_ref")\n'
        "    topic_hint = topic.strip() if topic is not None else ''\n"
        '    verified = req.get("delivery_verified") is True\n'
        '    origin_hint = (req.get("origin") or "lark").strip()\n'
        '    replayed_origin = (core.admit(binding_id="", source={"source_ref": ""}, request_ref="", message="").get("origin") or "lark").strip()\n'
        "    item = next(core.read_backlog_requests([ref]))\n"
        '    item_tid = (item.get("turn_id") or "").strip()\n'
        '    item_resp = item.get("response")\n'
        '    item_resp_hint = item_resp.strip() if item_resp is not None else ""\n'
        '    return f"{req[\'message\'].strip()}:{tid.strip() if tid is not None else \'\'}:{tid_or_empty}:{tid_default_hint}:{status_default}:{origin_hint}:{replayed_origin}:{rec_hint}:{target_hint}:{resp_hint}:{code_hint}:{req[\'source\'][\'source_ref\'].strip()}:{provider_hint}:{topic_hint}:{verified}:{item[\'status\'].strip()}:{item_tid}:{item_resp_hint}"\n',
        encoding="utf-8",
    )
    safe_errors = _run_mypy_contract_probe(
        [contracts_rel, external_rel, str(safe_narrowing)], strict=True
    )
    assert safe_errors[str(safe_narrowing)] == []


def test_core_help_accepted_and_backlog_real_field_shapes(tmp_path: Path) -> None:
    store = ChatSessionStore(tmp_path, startup_maintenance=False)
    core = ChatExternalConversations(cast("ChatRuntimeController", _StubController(store)))
    core.root.mkdir(parents=True, exist_ok=True)

    binding_id = "a" * 24
    source: Payload = {"source_ref": "b" * 24}
    help_ref = "1" * 24
    msg_ref = "2" * 24
    agents_ref = "3" * 24
    absent_optional_keys = (
        "command_argument",
        "commission_resources",
        "context_recovery",
        "mention_capture",
        "mention_delivery",
        "native_result_recovery",
        "rejected_mention_replay",
    )
    omitted_on_admit_keys = (
        "client_turn_id",
        "history_answer_hold",
        "profile",
        "response",
        "response_code",
    )
    omitted_on_minimal_source = ("provider_ref", "topic_ref")

    help_admitted = core.admit(
        binding_id=binding_id,
        source=source,
        request_ref=help_ref,
        message="/help",
        command="help",
    )
    help_row = core.read_request(help_ref)
    assert help_admitted["status"] == "command_completed"
    assert help_row["status"] == "command_completed"
    assert "session_id" in help_row and help_row["session_id"] is None
    assert "turn_id" in help_row and help_row["turn_id"] is None
    assert help_row.get("session_id", "") is None
    assert help_row.get("turn_id", "") is None
    assert (help_row.get("turn_id") or "").strip() == ""
    assert help_row.get("agent_target") is None
    assert RejectedMentionReplayDict({"target_turn_id": "t-1", "original_response": None}).get("original_response", "") is None
    for key in (*absent_optional_keys, "client_turn_id", "history_answer_hold", "profile", "response"):
        assert key not in help_row
        assert help_row.get(key) is None
    with pytest.raises(KeyError):
        _ = cast(Payload, help_row)["response"]
    for src_key in omitted_on_minimal_source:
        assert src_key not in help_row["source"]
        assert help_row["source"].get(src_key) is None
        with pytest.raises(KeyError):
            _ = cast(Payload, help_row["source"])[src_key]
    with pytest.raises(AttributeError):
        cast(str, help_row["turn_id"]).strip()
    with pytest.raises(AttributeError):
        cast(str, help_row.get("turn_id", "")).strip()

    legacy_help_ref = "4" * 24
    legacy_initial = core.admit(
        binding_id=binding_id,
        source=source,
        request_ref=legacy_help_ref,
        message="/help",
        command="help",
    )
    legacy_path = core.root / f"{legacy_help_ref}.json"
    legacy_payload = cast(Payload, dict(legacy_initial))
    legacy_payload.pop("origin", None)
    legacy_path.write_text(json.dumps(legacy_payload), encoding="utf-8")
    legacy_replayed = core.admit(
        binding_id=binding_id,
        source=source,
        request_ref=legacy_help_ref,
        message="/help",
        command="help",
    )
    legacy_read = core.read_request(legacy_help_ref)
    assert legacy_replayed["status"] == "command_completed"
    assert legacy_read["status"] == "command_completed"
    assert "origin" not in legacy_replayed and "origin" not in legacy_read
    assert legacy_replayed.get("origin") is None and legacy_read.get("origin") is None
    assert (legacy_replayed.get("origin") or "lark").strip() == "lark"
    assert (legacy_read.get("origin") or "lark").strip() == "lark"
    with pytest.raises(KeyError):
        _ = cast(Payload, legacy_replayed)["origin"]
    with pytest.raises(KeyError):
        _ = cast(Payload, legacy_read)["origin"]
    with pytest.raises(ValueError, match="identity"):
        core.admit(
            binding_id=binding_id,
            source=source,
            request_ref=legacy_help_ref,
            message="/help",
            command="help",
            origin="external",
        )

    msg_admitted = core.admit(
        binding_id=binding_id,
        source=source,
        request_ref=msg_ref,
        message="hello world",
        command=None,
    )
    msg_row = core.read_request(msg_ref)
    assert msg_admitted["status"] == "accepted"
    assert msg_row["status"] == "accepted"
    assert isinstance(msg_row["session_id"], str) and msg_row["session_id"]
    assert isinstance(msg_row["turn_id"], str) and msg_row["turn_id"]
    assert msg_row["message"] == "hello world"
    assert msg_row.get("agent_target") is None
    for key in (*absent_optional_keys, "status_snapshot"):
        assert key not in msg_row
    for key in omitted_on_admit_keys:
        assert key not in msg_row
        assert msg_row.get(key) is None
        with pytest.raises(KeyError):
            _ = cast(Payload, msg_row)[key]
    assert (msg_row.get("response") is not None) is False
    with pytest.raises(KeyError):
        _ = cast(Payload, msg_row)["response"] is not None
    for src_key in omitted_on_minimal_source:
        assert src_key not in msg_row["source"]
        assert msg_row["source"].get(src_key) is None
        with pytest.raises(KeyError):
            _ = cast(Payload, msg_row["source"])[src_key]

    source_with_refs: Payload = {"source_ref": "b" * 24, "provider_ref": "d" * 24, "topic_ref": "f" * 24}
    core.admit(
        binding_id=binding_id,
        source=source_with_refs,
        request_ref=agents_ref,
        message="/agents",
        command="agents",
    )
    replied_row = core.read_request(agents_ref)
    assert replied_row["status"] == "command_completed"
    replied_text = replied_row.get("response")
    assert (replied_text is not None) is True
    assert isinstance(replied_text, str) and "Agent" in replied_text
    assert replied_row["source"].get("provider_ref") == "d" * 24
    assert replied_row["source"].get("topic_ref") == "f" * 24

    for defer in (True, False):
        backlog = list(core.read_backlog_requests([msg_ref], defer_recovery=defer))
        assert len(backlog) == 1
        item = backlog[0]
        assert item["request_ref"] == msg_ref
        assert item["status"] == "accepted"
        assert item["turn_id"] == msg_row["turn_id"]
        assert item.get("message") is None
        assert "message" not in item
        with pytest.raises(KeyError):
            _ = cast(Payload, item)["message"]
