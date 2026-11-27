"""Focused regressions for CLI, goal-mode, Lark, presentation, and capability fixes."""

from __future__ import annotations

import argparse
import base64
import importlib
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_cli_build_parser_rejects_removed_postgres_subcommand() -> None:
    from loopx.cli import build_parser

    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["postgres", "--format", "json", "setup", "--execute"])


def test_opencode2_goal_worker_routes_stderr_and_preserves_failure_exit(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from loopx.cli_commands import opencode2_goal_worker as mod

    fake_worker = tmp_path / "worker.mjs"
    fake_worker.write_text("", encoding="utf-8")
    monkeypatch.setattr(mod, "_materialized_worker_path", lambda: fake_worker)

    class _FakeProc:
        def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
            self.returncode = returncode
            self._stdout = stdout
            self.stderr = io.StringIO(stderr)

        def communicate(self) -> tuple[str, None]:
            return self._stdout, None

        def kill(self) -> None:
            pass

        def wait(self) -> int:
            return self.returncode

    payloads: list[tuple[dict[str, object], str]] = []

    def capture_payload(payload: dict[str, object], fmt: str, _renderer) -> None:
        payloads.append((payload, fmt))

    args = argparse.Namespace(
        goal_id="g1",
        directory=str(tmp_path),
        agent_id=None,
        registry=None,
        capability=[],
        session_id=None,
        task_body=None,
        max_turns=None,
        max_duration_minutes=None,
        force_resume=False,
        state_dir=None,
        format="json",
    )

    monkeypatch.setattr(
        mod.subprocess,
        "Popen",
        lambda *a, **k: _FakeProc(
            0,
            json.dumps({"operation": "opencode2_goal_worker", "ok": False, "result": {"kind": "paused_state"}}),
            "worker diagnostic line\n",
        ),
    )
    rc = mod.handle_opencode2_goal_worker_command(args, capture_payload)
    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert "worker diagnostic line" in captured.err
    assert payloads[-1][0]["ok"] is False
    assert payloads[-1][0]["exit_code"] == 0

    monkeypatch.setattr(
        mod.subprocess,
        "Popen",
        lambda *a, **k: _FakeProc(
            2,
            json.dumps({"operation": "opencode2_goal_worker", "ok": True, "result": {"kind": "done"}}),
            "",
        ),
    )
    rc2 = mod.handle_opencode2_goal_worker_command(args, capture_payload)
    assert rc2 == 2
    assert payloads[-1][0]["ok"] is False
    assert payloads[-1][0]["exit_code"] == 2


def test_explore_turn_context_with_empty_goal_id_uses_default_registry(
    tmp_path: Path, monkeypatch
) -> None:
    from loopx.cli_commands import explore as mod

    registry = tmp_path / ".loopx" / "registry.json"
    registry.parent.mkdir(parents=True)
    registry.write_text('{"schema_version": "0.1", "goals": []}', encoding="utf-8")

    turn_ctx_mod = importlib.import_module("loopx.capabilities.explore.turn_context")
    seen: list[dict[str, object]] = []

    def fake_turn_context(**kwargs):
        seen.append(kwargs)
        return {"ok": True, "goal_id": kwargs.get("goal_id")}

    monkeypatch.setattr(turn_ctx_mod, "explore_turn_context", fake_turn_context)

    printed: list[dict[str, object]] = []
    args = argparse.Namespace(
        command="explore",
        explore_command="turn-context",
        goal_id="",
        agent_id="a1",
        todo_id=None,
        result_limit=10,
        result_offset=0,
        result_node=None,
        result_revision=None,
        config_path=None,
        format="json",
    )
    rc = mod.handle_explore_command(
        args,
        registry_path=registry,
        runtime_root_arg=str(tmp_path),
        output_format=lambda a: "json",
        print_payload=lambda payload, fmt, _renderer: printed.append(payload),
    )
    assert rc == 0
    assert seen and seen[0]["registry_path"] == registry
    assert printed[-1]["ok"] is True


@pytest.mark.parametrize(
    "malformed_reply",
    [
        None,
        (0, {"status": []}),
        (0, {"trajectory": "wrong shape"}),
        (1, {"steps": None}),
        (1, {"steps": [{"metadata": "wrong shape"}]}),
        (1, {"steps": [{"metadata": {"sourceTrajectoryStepInfo": []}}]}),
        (2, {"trajectory": "wrong shape"}),
    ],
)
def test_agy_native_input_accepts_ls_url_and_rejects_malformed_replies(
    monkeypatch, malformed_reply,
) -> None:
    from loopx.agy_goal_mode import host_runtime as mod
    from loopx.capabilities.native_chat import antigravity_adapter as provider

    monkeypatch.delenv("ANTIGRAVITY_LS_ADDRESS", raising=False)
    monkeypatch.setenv("ANTIGRAVITY_LS_URL", "http://127.0.0.1:4321")
    monkeypatch.setenv("ANTIGRAVITY_CSRF_TOKEN", "tok")

    args = argparse.Namespace(conversation_id="conv-1")

    snapshot = {
        "numTotalSteps": 1,
        "status": "CASCADE_RUN_STATUS_IDLE",
        "trajectory": {"cascadeId": "conv-1"},
    }
    steps = [{
        "type": "CORTEX_STEP_TYPE_USER_INPUT",
        "status": "CORTEX_STEP_STATUS_DONE",
        "metadata": {"createdAt": "2026-01-01T00:00:00Z"},
        "userInput": {"userResponse": "hello"},
    }]
    replies = [snapshot.copy(), {"steps": steps}, snapshot.copy()]
    if malformed_reply is not None:
        index, override = malformed_reply
        replies[index].update(override)
    responses = iter(replies)
    monkeypatch.setattr(provider, "_rpc_post", lambda *_a, **_k: next(responses))
    res = mod._native_input(args, "hello")
    if malformed_reply is None:
        assert res is not None and res[1:] == ("CASCADE_RUN_STATUS_IDLE", 1)
    else:
        assert res is None


def test_agy_native_input_returns_no_anchor_when_rpc_fails(monkeypatch) -> None:
    from loopx.agy_goal_mode import host_runtime as mod
    from loopx.capabilities.native_chat import antigravity_adapter as provider
    from loopx.chat_agent import CodexChatAgentError

    monkeypatch.setenv("ANTIGRAVITY_LS_ADDRESS", "127.0.0.1:4321")
    monkeypatch.setenv("ANTIGRAVITY_CSRF_TOKEN", "tok")

    def raise_rpc(*_a, **_k):
        raise CodexChatAgentError("ls unavailable", gate=None)

    monkeypatch.setattr(provider, "_rpc_post", raise_rpc)
    assert mod._native_input(argparse.Namespace(conversation_id="conv-1"), "hello") is None


def test_claude_goal_policy_fail_closed_empty_path_and_resolves_relative_cwd(
    tmp_path: Path, monkeypatch
) -> None:
    hooks_dir = str(REPO_ROOT / "loopx" / "claude_goal_mode" / "hooks")
    if hooks_dir not in sys.path:
        sys.path.insert(0, hooks_dir)
    import goal_policy

    project = tmp_path / "proj"
    project.mkdir()
    other_dir = tmp_path / "elsewhere"
    other_dir.mkdir()
    monkeypatch.chdir(other_dir)
    monkeypatch.setattr(
        goal_policy,
        "active_context",
        lambda cwd: {
            "goal_id": "g1",
            "registry": str(project / ".loopx" / "registry.json"),
            "agent_id": "cc",
            "write_scope": [str(project)],
            "project_root": str(project),
        },
    )
    monkeypatch.setattr(goal_policy, "should_run", lambda *a, **k: True)

    missing = goal_policy.decide(
        {"cwd": str(project), "tool_name": "Write", "tool_input": {"file_path": ""}}
    )["hookSpecificOutput"]
    assert missing["permissionDecision"] == "deny"
    assert "missing file_path" in missing["permissionDecisionReason"]

    rel_ok = goal_policy.decide(
        {"cwd": str(project), "tool_name": "Write", "tool_input": {"file_path": "src/app.py"}}
    )["hookSpecificOutput"]
    assert rel_ok["permissionDecision"] == "allow"


def test_lark_private_progress_handles_missing_version_null_summaries_and_list_label() -> None:
    from loopx.extensions.lark.private_progress import (
        presentation_phase,
        presentation_stream,
        project_progress,
    )

    rec: dict[str, object] = {"context_recovery": {"presentation_key": "k1"}}
    assert presentation_phase(rec, "progress") == "progress.recovery.k1"
    stream = presentation_stream(rec)
    assert isinstance(stream, dict)

    state: dict[str, object] = {"thinking_summaries": None}
    events = [
        {
            "event_id": "e1",
            "kind": "agent.phase",
            "payload": {
                "method": "item/reasoning/summaryTextDelta",
                "step": {
                    "kind": "reasoning",
                    "reasoning_summary": True,
                    "detail": "Checking state",
                    "id": "r1",
                },
            },
        },
        {
            "event_id": "e2",
            "kind": "agent.phase",
            "payload": {"method": "item/started", "label": ["unexpected-list"]},
        },
    ]
    rendered = project_progress(state, events, owner_private=True)
    assert rendered is not None and "Checking state" in rendered


def test_explore_results_api_skips_double_validation_matches_goal_and_returns_404(
    monkeypatch,
) -> None:
    from loopx.presentation import explore_results_api as mod
    from loopx.control_plane.status.goal_source import SourceScopeError

    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        mod,
        "validate_explore_result_event",
        lambda event, expected_goal_id=None: calls.append(event) or event,
    )
    monkeypatch.setattr(
        mod,
        "build_explore_result_projection",
        lambda events, goal_id, finding_limit: {"nodes": [], "findings": []},
    )
    import loopx.control_plane.work_items.yardstick_decision as ydec

    monkeypatch.setattr(ydec, "project_work_yardstick_presentations", lambda rows, goal_id: [])

    mod._finding_rows([{"x": 1}], None, goal_id="g1", validated=True)
    assert calls == []
    mod._finding_rows([{"x": 1}], None, goal_id="g1", validated=False)
    assert len(calls) == 1

    class _FakeReader:
        def __init__(self, raise_scope: bool = False) -> None:
            self.raise_scope = raise_scope
            self.source = SimpleNamespace(
                namespace="ns-1",
                goal_id="g-second",
                read_events=lambda gid: [],
            )

        def registered_status(self, gid: str, fresh: bool = False):
            if self.raise_scope:
                raise SourceScopeError("missing")
            return {
                "run_history": {
                    "goals": [
                        {"id": "g-first", "semantic_history": {}},
                        {
                            "id": "g-second",
                            "semantic_history": {
                                "agents": [
                                    {
                                        "latest_agent_vision_run": {
                                            "generated_at": "2026-07-01T12:00:00Z",
                                            "agent_vision": {
                                                "vision_patch": {"vision_summary": "v"}
                                            },
                                        }
                                    }
                                ]
                            },
                        },
                    ]
                }
            }

    import loopx.presentation.goal_source_routing as gsr

    for raise_scope, expected_status in ((False, 200), (True, 404)):
        reader = _FakeReader(raise_scope=raise_scope)
        monkeypatch.setattr(gsr, "linked_goal_source", lambda srv, gid: ("row", reader))
        sent: list[tuple[int, dict[str, object]]] = []

        class _Dummy(mod.ExploreResultsRequestMixin):
            path = "/api/chat/explore-results?goal_id=g-second&source_namespace=ns-1&view=history-timing"
            server = SimpleNamespace()

            def _require_loopback_origin(self) -> bool:
                return True

            def _send_json(self, payload: dict[str, object], status: int = 200) -> None:
                sent.append((status, payload))

            def _send_error(self, message: str, status: int = 400) -> None:
                sent.append((status, {"ok": False, "error": message}))

        _Dummy()._explore_results()
        assert sent[-1][0] == expected_status
        if expected_status == 200:
            assert sent[-1][1]["strategy_recorded_at"] == "2026-07-01T12:00:00Z"


def test_chat_attachments_rejects_bool_and_list_sizes() -> None:
    from loopx.chat.attachments import normalize_chat_image_attachments

    data = base64.b64encode(b"img").decode("ascii")
    for bad_size in (True, False, [3], "3"):
        with pytest.raises(ValueError):
            normalize_chat_image_attachments(
                [{"data_url": f"data:image/png;base64,{data}", "size": bad_size}]
            )


def test_presets_quotes_cli_bin_and_tolerates_none_lists() -> None:
    from loopx.presets import build_beginner_preset_packet, render_beginner_preset_markdown

    pkt = build_beginner_preset_packet(preset_id="daily-triage", cli_bin="/opt/my loopx/bin/loopx")
    assert pkt["common_setup"][0] == "'/opt/my loopx/bin/loopx' doctor"
    assert pkt["presets"][0]["commands"]["cli_start"].startswith("'/opt/my loopx/bin/loopx' start-goal")
    md = render_beginner_preset_markdown(
        {
            "summary": "s",
            "mutation_policy": "m",
            "common_setup": None,
            "presets": [{"id": "x", "useful_when": None, "safety_defaults": None, "commands": None}],
        }
    )
    assert "LoopX beginner preset picker" in md


def test_content_ops_rejects_duplicate_stdin_and_uses_generic_error_header(
    tmp_path: Path, monkeypatch
) -> None:
    from loopx.capabilities.content_ops.cli import handle_content_ops_command

    monkeypatch.setattr(sys, "stdin", io.StringIO('{"a": 1}'))
    printed: list[tuple[dict[str, object], str]] = []
    args = argparse.Namespace(
        content_ops_command="item-transition",
        item_json="-",
        event_json="-",
        max_input_bytes=1024,
        format="markdown",
    )
    rc = handle_content_ops_command(
        args,
        output_format=lambda a: "markdown",
        print_payload=lambda payload, fmt, renderer: printed.append((payload, renderer(payload))),
    )
    assert rc == 1
    payload, md = printed[-1]
    assert payload["ok"] is False
    assert "stdin ('-')" in str(payload["error"])
    assert md.startswith("# LoopX Content-Ops\n")
    assert "Private Connector Gate" not in md


def test_effect_runtime_stop_recognizes_reparented_all_zombie_thread_group(
    tmp_path: Path, monkeypatch
) -> None:
    from loopx.control_plane import effect_runtime

    locator = tmp_path / "runtime.json"
    info = {"pid": 424242, "token": "serving"}
    monkeypatch.setattr(effect_runtime, "_request_with_info", lambda *a, **kw: {"stopped": True})
    monkeypatch.setattr(effect_runtime, "_pid_is_alive", lambda pid: True)
    monkeypatch.setattr(effect_runtime, "_reap_exited_runtime_child", lambda _info: True)

    zombie = False
    monkeypatch.setattr(effect_runtime, "_linux_thread_group_is_zombie", lambda pid: zombie)
    pending = effect_runtime._stop_effect_runtime(
        locator, info, timeout=0.05, wait_for_process_exit=True
    )
    assert pending["stopped"] is False
    assert pending["status"] == "shutdown_pending"

    zombie = True
    stopped = effect_runtime._stop_effect_runtime(
        locator, info, timeout=0.5, wait_for_process_exit=True
    )
    assert stopped["stopped"] is True
    assert stopped["status"] == "stopped"
