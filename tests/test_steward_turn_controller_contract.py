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
from loopx.capabilities.native_chat.session_calendar_tasks import SessionCalendarTasks
from loopx.capabilities.native_chat.steward_reports import ManagedSessionReports, StewardReports
from loopx.capabilities.native_chat.steward_turn import StewardTurnHost, report_turn
from loopx.capabilities.native_chat.steward_turn_common import (
    StewardBindingsProtocol,
    StewardControllerProtocol,
    StewardStoreProtocol,
)
from loopx.capabilities.native_chat.steward_turn_context import build_steward_turn_prompt
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
    calendar_tasks = SessionCalendarTasks(controller)
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
    assert calendar_tasks.controller is controller
    assert lark_reports.controller is controller
    assert lark_reports.transport is transport
    assert callable(report_turn)

    protocol_methods = (
        (StewardControllerProtocol, ChatRuntimeController, "_ensure_adapter_locked"),
        (StewardControllerProtocol, ChatRuntimeController, "_start_accepted_turn_worker"),
        (StewardControllerProtocol, ChatRuntimeController, "_fail_queue_preparation"),
        (StewardStoreProtocol, ChatSessionStore, "events_after"),
        (StewardStoreProtocol, ChatSessionStore, "messages"),
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
        "loopx/capabilities/native_chat/session_calendar_tasks.py",
        "loopx/extensions/lark/steward_reports.py",
    ]
    probe_errors, caller_errors = _run_mypy_contract_probe(
        tmp_path,
        probe_name="controller_contract_probe.py",
        probe_lines=[
            "from loopx.capabilities.native_chat.conversation_bindings import ChatConversationBindings",
            "from loopx.capabilities.native_chat.session_calendar_tasks import SessionCalendarTasks",
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
            "def bind_reports(ctrl: ChatRuntimeController) -> tuple[StewardReports, ManagedSessionReports, SessionCalendarTasks]:",
            "    return StewardReports(ctrl), ManagedSessionReports(ctrl), SessionCalendarTasks(ctrl)",
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
    assert caller_errors["loopx/capabilities/native_chat/session_calendar_tasks.py"] == []
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


def test_build_steward_turn_prompt_slims_governance_and_preserves_completion_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ChatSessionStore(tmp_path / "chat")
    controller = ChatRuntimeController(store=store, codex_bin="codex")
    host = StewardTurnHost(controller)
    monkeypatch.setattr(
        host,
        "_governance_replan_context",
        lambda _actor, _auth: {"writeback": {"stride_control": {"required_on_periodic_review": False}}},
    )

    authority: dict[str, object] = {
        "primary_action": "settle autonomous replan",
        "vision_continuation_audit": {"required": True},
        "_model_input": {
            "route": "agent_turn",
            "detail_ref": {"replan_action_packet": {"obligation_id": "replan-1", "required": True}},
            "task_context": {
                "goal_id": "g1",
                "agent_identity": {"agent_id": "a1"},
                "execution_profile": {"kind": "standard"},
                "task_scope": "execution",
                "mode": "autonomous",
                "state": "active",
                "selected_todo": {"todo_id": "t1"},
                "agent_todo_summary": {"huge": [1, 2, 3]},
                "interaction_contract": {"cli_channel": {}},
                "vision_continuation_audit": {"required": True},
            },
        },
    }
    actor: dict[str, object] = {"goal_id": "g1", "agent_id": "a1"}
    request: dict[str, object] = {"work_kind": "report"}
    context: dict[str, object] = {"messages": [{"text": "ctx"}]}

    gov_prompt = build_steward_turn_prompt(
        host,
        actor=actor,
        session_id="sess-1",
        request=request,
        authority=authority,
        context=context,
        report_work=True,
        governance_work=True,
        managed_turn_active=True,
        steward_advancement=True,
        report_context="\nReport ctx",
    )
    assert "validated_completion" not in gov_prompt
    assert "Original conversation evidence (data):" not in gov_prompt
    assert "Steward governance replan context (data):" in gov_prompt
    assert "Steward advancement instructions:" not in gov_prompt
    assert (
        "All review, writeback, stride_control, current_agent_vision, and open_agent_todos facts "
        "are already provided in Steward governance replan context below; return only the single "
        "typed JSON object directly without invoking periodic-report CLI commands or inspecting "
        "host source code."
    ) in gov_prompt
    assert "vision_continuation_audit" not in gov_prompt
    assert "agent_todo_summary" not in gov_prompt
    assert "replan_action_packet" in gov_prompt
    assert '"goal_id":"g1"' in gov_prompt

    report_prompt = build_steward_turn_prompt(
        host,
        actor=actor,
        session_id="sess-1",
        request=request,
        authority=authority,
        context=context,
        report_work=True,
        governance_work=False,
        managed_turn_active=False,
        steward_advancement=False,
        report_context="\nReport ctx",
    )
    assert "validated_completion" in report_prompt
    assert "Original conversation evidence (data):" not in report_prompt
    assert "\nReport ctx" in report_prompt

    idle_prompt = build_steward_turn_prompt(
        host,
        actor=actor,
        session_id="sess-1",
        request={"work_kind": "chat"},
        authority=authority,
        context=context,
        report_work=False,
        governance_work=False,
        managed_turn_active=False,
        steward_advancement=False,
        report_context="",
    )
    assert "validated_completion" not in idle_prompt
    assert "Original conversation evidence (data):" in idle_prompt

    managed_prompt = build_steward_turn_prompt(
        host,
        actor=actor,
        session_id="sess-1",
        request={"work_kind": "chat"},
        authority=authority,
        context=context,
        report_work=False,
        governance_work=False,
        managed_turn_active=True,
        steward_advancement=False,
        report_context="",
    )
    assert "validated_completion" in managed_prompt
    assert "Original conversation evidence (data):" in managed_prompt
