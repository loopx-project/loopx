"""Contract tests binding StewardTurnHost and LarkStewardReports protocols to real runtime hosts."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import importlib.util
import inspect
import os
from pathlib import Path
import re
import subprocess
import sys
import sysconfig

import pytest

from loopx.capabilities.native_chat.conversation_bindings import ChatConversationBindings
from loopx.capabilities.native_chat.external_conversations import ChatExternalConversations
from loopx.capabilities.native_chat.steward_reports import ManagedSessionReports, StewardReports
from loopx.capabilities.native_chat.steward_turn import StewardTurnHost, report_turn
from loopx.capabilities.native_chat.steward_turn_common import (
    StewardBindingsProtocol,
    StewardControllerProtocol,
    StewardStoreProtocol,
)
from loopx.chat_runtime import ChatRuntimeController
from loopx.chat_store import ChatSessionStore
from loopx.extensions.lark.owner_attention import LarkOwnerAttention
from loopx.extensions.lark.private_conversations import LarkPrivateConversations
from loopx.extensions.lark.steward_reports import (
    LarkStewardReports,
    _LarkCoreProtocol,
    _LarkOwnerAttentionProtocol,
    _LarkTransportProtocol,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MYPY_SUCCESS_RE = re.compile(r"^Success: no issues found in (\d+) source files?\s*$", re.MULTILINE)
_MYPY_FOUND_RE = re.compile(
    r"^Found (\d+) errors? in (\d+) files? \(checked (\d+) source files?\)\s*$",
    re.MULTILINE,
)


def _collect_mypy_contract_errors(
    proc: subprocess.CompletedProcess[str],
    *,
    probe: Path,
    callers: Sequence[str],
) -> tuple[list[str], dict[str, list[str]]]:
    expected_files = 1 + len(callers)
    details = f"rc={proc.returncode}\nSTDOUT:\n{proc.stdout.strip()}\nSTDERR:\n{proc.stderr.strip()}"
    if proc.returncode not in (0, 1):
        raise AssertionError(f"mypy exited unexpectedly:\n{details}")
    if proc.stderr.strip():
        raise AssertionError(f"mypy reported errors on stderr:\n{details}")
    if proc.returncode == 0:
        success_match = _MYPY_SUCCESS_RE.search(proc.stdout)
        if success_match is None or int(success_match.group(1)) != expected_files:
            raise AssertionError(f"mypy did not report checking all {expected_files} files:\n{details}")
    else:
        found_match = _MYPY_FOUND_RE.search(proc.stdout)
        if found_match is None or int(found_match.group(3)) != expected_files:
            raise AssertionError(f"mypy aborted before checking all {expected_files} files:\n{details}")
        if int(found_match.group(1)) <= 0:
            raise AssertionError(f"mypy returned 1 without reporting error diagnostics:\n{details}")

    probe_errors: list[str] = []
    caller_errors: dict[str, list[str]] = {path: [] for path in callers}
    probe_prefix = f"{probe}:"
    for line in proc.stdout.splitlines():
        if ": error:" not in line:
            continue
        if line.startswith(probe_prefix):
            probe_errors.append(line)
            continue
        for caller in callers:
            if line.startswith(f"{caller}:"):
                caller_errors[caller].append(line)
                break
    return probe_errors, caller_errors


def _run_mypy_contract_probe(
    tmp_path: Path,
    *,
    probe_name: str,
    probe_lines: Sequence[str],
    callers: Sequence[str],
    follow_imports_silent: bool = False,
    extra_env: Mapping[str, str] | None = None,
) -> tuple[list[str], dict[str, list[str]]]:
    if importlib.util.find_spec("mypy") is None:
        pytest.skip("mypy optional test dependency is unavailable")

    probe = tmp_path / probe_name
    probe.write_text("\n".join([*probe_lines, ""]), encoding="utf-8")
    cmd = [
        sys.executable,
        "-m",
        "mypy",
        "--config-file",
        str(_REPO_ROOT / "pyproject.toml"),
        *(["--follow-imports=silent"] if follow_imports_silent else []),
        "--cache-dir",
        str(tmp_path / ".mypy_cache"),
        "--show-error-codes",
        str(probe),
        *callers,
    ]
    env = {**os.environ, **extra_env} if extra_env is not None else None
    proc = subprocess.run(cmd, cwd=_REPO_ROOT, env=env, capture_output=True, text=True, check=False)
    return _collect_mypy_contract_errors(proc, probe=probe, callers=callers)


def test_real_controller_constructs_host_and_matches_protocol_signatures(tmp_path: Path) -> None:
    store = ChatSessionStore(tmp_path / "chat")
    controller = ChatRuntimeController(store=store, codex_bin="codex")
    controller.project_contexts.conversation_bindings = ChatConversationBindings(
        root=store.root,
        project_contexts=controller.project_contexts,
        observe=lambda _profile: {},
    )

    host = StewardTurnHost(controller)
    reports = StewardReports(controller)
    managed_reports = ManagedSessionReports(controller)
    transport = LarkPrivateConversations(
        controller=controller,
        runtime_root=tmp_path,
        runner=lambda *_args, **_kwargs: {},
        cli_bin="lark-cli",
    )
    lark_reports = LarkStewardReports(transport)

    assert host.controller is controller
    assert host.store is store
    assert reports.controller is controller
    assert managed_reports.controller is controller
    assert lark_reports.controller is controller
    assert lark_reports.transport is transport
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
        (_LarkTransportProtocol, LarkPrivateConversations, "_inbox"),
        (_LarkTransportProtocol, LarkPrivateConversations, "_deliver"),
        (_LarkCoreProtocol, ChatExternalConversations, "read_request"),
        (_LarkOwnerAttentionProtocol, LarkOwnerAttention, "_route"),
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
    callers = [
        "loopx/chat_runtime.py",
        "loopx/capabilities/native_chat/steward_managed_mode.py",
        "loopx/capabilities/native_chat/steward_reports.py",
        "loopx/extensions/lark/steward_reports.py",
    ]
    probe_errors, caller_errors = _run_mypy_contract_probe(
        tmp_path,
        probe_name="controller_contract_probe.py",
        probe_lines=[
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
        ],
        callers=callers,
    )
    assert not probe_errors, f"Real controller/protocol contract errors: {probe_errors}"

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
    assert sum(len(lines) for lines in caller_errors.values()) <= 40


def test_real_lark_transport_protocol_and_callers_mypy_contract(tmp_path: Path) -> None:
    callers = [
        "loopx/extensions/lark/private_conversations.py",
        "loopx/extensions/lark/owner_attention.py",
    ]
    probe_errors, caller_errors = _run_mypy_contract_probe(
        tmp_path,
        probe_name="lark_transport_contract_probe.py",
        probe_lines=[
            "from loopx.capabilities.native_chat.external_conversations import ChatExternalConversations",
            "from loopx.extensions.lark.owner_attention import LarkOwnerAttention",
            "from loopx.extensions.lark.private_conversations import LarkPrivateConversations",
            "from loopx.extensions.lark.steward_reports import (",
            "    LarkStewardReports,",
            "    _LarkCoreProtocol,",
            "    _LarkOwnerAttentionProtocol,",
            "    _LarkTransportProtocol,",
            ")",
            "",
            "def bind_transport(transport: LarkPrivateConversations) -> _LarkTransportProtocol:",
            "    return transport",
            "",
            "def bind_lark_reports(transport: LarkPrivateConversations) -> LarkStewardReports:",
            "    return LarkStewardReports(transport)",
            "",
            "def bind_core(core: ChatExternalConversations) -> _LarkCoreProtocol:",
            "    return core",
            "",
            "def bind_owner_attention(attention: LarkOwnerAttention) -> _LarkOwnerAttentionProtocol:",
            "    return attention",
        ],
        callers=callers,
        follow_imports_silent=True,
    )
    assert not probe_errors, f"Real Lark transport/protocol contract errors: {probe_errors}"

    private_errors = caller_errors["loopx/extensions/lark/private_conversations.py"]
    assert not any(
        'Argument 1 to "LarkStewardReports"' in err
        or "_LarkTransportProtocol" in err
        for err in private_errors
    ), private_errors
    assert len(private_errors) <= 19, private_errors
    assert len(caller_errors["loopx/extensions/lark/owner_attention.py"]) <= 3, (
        caller_errors["loopx/extensions/lark/owner_attention.py"]
    )
    assert sum(len(lines) for lines in caller_errors.values()) <= 22


def test_mypy_contract_probe_rejects_early_exit_or_aborted_run(tmp_path: Path) -> None:
    probe = tmp_path / "dummy_probe.py"
    with pytest.raises(AssertionError, match="MYPYPATH"):
        _collect_mypy_contract_errors(
            subprocess.CompletedProcess(
                args=["mypy"],
                returncode=1,
                stdout="",
                stderr="site-packages is in the MYPYPATH. Please remove it.\n",
            ),
            probe=probe,
            callers=["loopx/chat_runtime.py"],
        )

    with pytest.raises(AssertionError, match="checking all 2 files"):
        _collect_mypy_contract_errors(
            subprocess.CompletedProcess(
                args=["mypy"],
                returncode=0,
                stdout="Success: no issues found in 1 source file\n",
                stderr="",
            ),
            probe=probe,
            callers=["loopx/chat_runtime.py"],
        )

    with pytest.raises(AssertionError, match="exited unexpectedly"):
        _collect_mypy_contract_errors(
            subprocess.CompletedProcess(args=["mypy"], returncode=2, stdout="", stderr="fatal error"),
            probe=probe,
            callers=[],
        )

    purelib = sysconfig.get_path("purelib")
    assert purelib
    with pytest.raises(AssertionError, match="MYPYPATH"):
        _run_mypy_contract_probe(
            tmp_path,
            probe_name="aborted_probe.py",
            probe_lines=["x: int = 1"],
            callers=[],
            follow_imports_silent=True,
            extra_env={"MYPYPATH": purelib},
        )
