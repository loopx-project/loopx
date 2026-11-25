"""Contract tests binding StewardTurnHost protocols to real ChatRuntimeController."""
from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path
import subprocess
import sys

import pytest

from loopx.capabilities.native_chat.conversation_bindings import ChatConversationBindings
from loopx.capabilities.native_chat.steward_reports import ManagedSessionReports, StewardReports
from loopx.capabilities.native_chat.steward_turn import StewardTurnHost, report_turn
from loopx.capabilities.native_chat.steward_turn_common import (
    StewardBindingsProtocol,
    StewardControllerProtocol,
    StewardStoreProtocol,
)
from loopx.chat_runtime import ChatRuntimeController
from loopx.chat_store import ChatSessionStore

_REPO_ROOT = Path(__file__).resolve().parents[1]


def test_real_controller_constructs_host_and_matches_protocol_signatures(tmp_path: Path) -> None:
    store = ChatSessionStore(tmp_path / "chat")
    controller = ChatRuntimeController(store=store, codex_bin="codex")

    host = StewardTurnHost(controller)
    reports = StewardReports(controller)
    managed_reports = ManagedSessionReports(controller)

    assert host.controller is controller
    assert host.store is store
    assert reports.controller is controller
    assert managed_reports.controller is controller
    assert callable(report_turn)

    protocol_methods = (
        (StewardControllerProtocol, ChatRuntimeController, "_ensure_adapter_locked"),
        (StewardControllerProtocol, ChatRuntimeController, "_start_accepted_turn_worker"),
        (StewardControllerProtocol, ChatRuntimeController, "_fail_queue_preparation"),
        (StewardStoreProtocol, ChatSessionStore, "events_after"),
        (StewardStoreProtocol, ChatSessionStore, "append_event"),
        (StewardStoreProtocol, ChatSessionStore, "update_session"),
        (StewardStoreProtocol, ChatSessionStore, "accept_managed_turn"),
        (StewardBindingsProtocol, ChatConversationBindings, "resolve"),
        (StewardBindingsProtocol, ChatConversationBindings, "_resolve_observed"),
    )
    for proto_cls, real_cls, method_name in protocol_methods:
        proto_params = list(inspect.signature(getattr(proto_cls, method_name)).parameters.items())[1:]
        real_params = inspect.signature(getattr(real_cls, method_name)).parameters
        for name, proto_param in proto_params:
            if proto_param.kind is inspect.Parameter.VAR_KEYWORD:
                assert any(p.kind is inspect.Parameter.VAR_KEYWORD for p in real_params.values()), (
                    f"{real_cls.__name__}.{method_name} missing **kwargs"
                )
                continue
            assert name in real_params, f"{real_cls.__name__}.{method_name} missing parameter {name!r}"


def test_real_controller_protocol_and_four_callers_mypy_contract(tmp_path: Path) -> None:
    if importlib.util.find_spec("mypy") is None:
        pytest.skip("mypy optional test dependency is unavailable")

    probe = tmp_path / "controller_contract_probe.py"
    probe.write_text(
        "\n".join(
            [
                "from loopx.capabilities.native_chat.conversation_bindings import ChatConversationBindings",
                "from loopx.capabilities.native_chat.steward_reports import ManagedSessionReports, StewardReports",
                "from loopx.capabilities.native_chat.steward_turn import StewardTurnHost",
                "from loopx.capabilities.native_chat.steward_turn_common import (",
                "    StewardBindingsProtocol,",
                "    StewardControllerProtocol,",
                "    StewardStoreProtocol,",
                ")",
                "from loopx.chat_runtime import ChatRuntimeController",
                "from loopx.chat_store import ChatSessionStore",
                "",
                "def bind_host(ctrl: ChatRuntimeController) -> StewardTurnHost:",
                "    return StewardTurnHost(ctrl)",
                "",
                "def bind_ctrl(ctrl: ChatRuntimeController) -> StewardControllerProtocol:",
                "    return ctrl",
                "",
                "def bind_store(store: ChatSessionStore) -> StewardStoreProtocol:",
                "    return store",
                "",
                "def bind_bindings(bindings: ChatConversationBindings) -> StewardBindingsProtocol:",
                "    return bindings",
                "",
                "def bind_reports(ctrl: ChatRuntimeController) -> tuple[StewardReports, ManagedSessionReports]:",
                "    return StewardReports(ctrl), ManagedSessionReports(ctrl)",
                "",
            ]
        ),
        encoding="utf-8",
    )
    callers = [
        "loopx/chat_runtime.py",
        "loopx/capabilities/native_chat/steward_managed_mode.py",
        "loopx/capabilities/native_chat/steward_reports.py",
        "loopx/extensions/lark/steward_reports.py",
    ]
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--config-file",
            str(_REPO_ROOT / "pyproject.toml"),
            "--cache-dir",
            str(tmp_path / ".mypy_cache"),
            "--show-error-codes",
            str(probe),
            *callers,
        ],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    probe_errors = [line for line in proc.stdout.splitlines() if str(probe) in line and ": error:" in line]
    assert not probe_errors, f"Real controller/protocol contract errors: {probe_errors}"

    caller_errors: dict[str, list[str]] = {path: [] for path in callers}
    for line in proc.stdout.splitlines():
        if ": error:" not in line:
            continue
        file_part = line.split(":", 1)[0]
        if file_part in caller_errors:
            caller_errors[file_part].append(line)

    assert not any(
        'Argument 1 to "StewardTurnHost"' in err or "StewardControllerProtocol" in err
        for err in caller_errors["loopx/chat_runtime.py"]
    ), caller_errors["loopx/chat_runtime.py"]
    assert len(caller_errors["loopx/chat_runtime.py"]) <= 27, caller_errors["loopx/chat_runtime.py"]
    assert len(caller_errors["loopx/capabilities/native_chat/steward_managed_mode.py"]) <= 13, (
        caller_errors["loopx/capabilities/native_chat/steward_managed_mode.py"]
    )
    assert caller_errors["loopx/capabilities/native_chat/steward_reports.py"] == []
    assert caller_errors["loopx/extensions/lark/steward_reports.py"] == []
    assert sum(len(lines) for lines in caller_errors.values()) <= 73
