from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from benchmark.deepswe import run_native_codex_goal as runnable_goal
from benchmark.native_codex_goal import (
    NativeGoalConfig,
    NativeGoalDeadlineExceeded,
    NativeGoalProtocolError,
    compact_native_goal_receipt,
    observe_native_goal_event,
    probe_native_goal_process,
    run_native_goal_process,
    run_native_goal_process_until_terminal,
    run_native_goal_until_terminal,
    start_native_goal_turn,
)
from loopx.capabilities.benchmark_toolkit.native_codex_isolation import (
    NativeCodexIsolationEnvelope,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


class FakeTransport:
    def __init__(
        self,
        *,
        goal_objective: str = "Finish the task.",
        skill_names: tuple[str, ...] = (),
        skill_errors: tuple[dict[str, str], ...] = (),
        skill_cwd: str | None = None,
    ) -> None:
        self.goal_objective = goal_objective
        self.skill_names = skill_names
        self.skill_errors = skill_errors
        self.skill_cwd = skill_cwd
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def request(self, method: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        self.calls.append((method, dict(params)))
        if method == "initialize":
            return {"serverInfo": {"name": "fake"}}
        if method == "skills/list":
            return {
                "data": [
                    {
                        "cwd": self.skill_cwd or params["cwds"][0],
                        "errors": list(self.skill_errors),
                        "skills": [
                            {"name": name, "enabled": True} for name in self.skill_names
                        ],
                    }
                ]
            }
        if method == "thread/start":
            return {"thread": {"id": "thread-1"}}
        if method == "thread/goal/set":
            return {"goal": {"threadId": "thread-1", **dict(params)}}
        if method == "thread/goal/get":
            return {
                "goal": {
                    "threadId": "thread-1",
                    "objective": self.goal_objective,
                    "status": "active",
                }
            }
        if method == "turn/start":
            return {"turn": {"id": "response-turn", "status": "inProgress"}}
        raise AssertionError(method)

    def notify(self, method: str, params: Mapping[str, Any]) -> None:
        self.calls.append((method, dict(params)))


class ContinuationTransport(FakeTransport):
    def __init__(self, *, terminal_after_second_turn: bool = True) -> None:
        super().__init__()
        self.goal_reads = 0
        self.terminal_after_second_turn = terminal_after_second_turn
        self.events: list[dict[str, Any]] = [
            {
                "method": "turn/started",
                "params": {
                    "threadId": "thread-1",
                    "turn": {"id": "event-turn-1", "status": "inProgress"},
                },
            },
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread-1",
                    "turn": {"id": "event-turn-1", "status": "completed"},
                },
            },
            {
                "method": "turn/started",
                "params": {
                    "threadId": "thread-1",
                    "turn": {"id": "event-turn-2", "status": "inProgress"},
                },
            },
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread-1",
                    "turn": {"id": "event-turn-2", "status": "completed"},
                },
            },
        ]

    def request(self, method: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        if method != "thread/goal/get":
            return super().request(method, params)
        self.calls.append((method, dict(params)))
        self.goal_reads += 1
        status = (
            "complete"
            if self.terminal_after_second_turn and self.goal_reads >= 3
            else "active"
        )
        return {
            "goal": {
                "threadId": "thread-1",
                "objective": self.goal_objective,
                "status": status,
            }
        }

    def next_event(self, *, timeout_sec: float) -> Mapping[str, Any] | None:
        del timeout_sec
        return self.events.pop(0) if self.events else None


def _config(**overrides: Any) -> NativeGoalConfig:
    values = {
        "cwd": "/workspace/case",
        "objective": "Finish the task.",
        "task_instruction": "Implement the requested behavior and validate it.",
        "model": "model-route",
        "effort": "high",
        "token_budget": 120000,
    }
    values.update(overrides)
    return NativeGoalConfig(**values)


def test_goal_transaction_order_and_compact_receipt() -> None:
    transport = FakeTransport()
    turn = start_native_goal_turn(transport, _config())

    assert [method for method, _ in transport.calls] == [
        "initialize",
        "initialized",
        "thread/start",
        "thread/goal/set",
        "thread/goal/get",
        "turn/start",
    ]
    initialize = transport.calls[0][1]
    assert initialize["capabilities"] == {"experimentalApi": True}
    goal_set = transport.calls[3][1]
    assert goal_set["tokenBudget"] == 120000
    assert transport.calls[5][1]["effort"] == "high"

    assert (
        observe_native_goal_event(
            turn,
            {
                "method": "turn/started",
                "params": {
                    "threadId": "thread-1",
                    "turn": {"id": "event-turn", "status": "inProgress"},
                },
            },
        )
        is False
    )
    assert turn.turn_id == "event-turn"
    assert (
        observe_native_goal_event(
            turn,
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread-1",
                    "turn": {"id": "event-turn", "status": "completed"},
                },
            },
        )
        is True
    )

    receipt = compact_native_goal_receipt(turn)
    rendered = json.dumps(receipt, sort_keys=True)
    assert receipt["event_turn_id_observed"] is True
    assert receipt["terminal_event_observed"] is True
    assert receipt["turn_started_count"] == 1
    assert receipt["turn_completed_count"] == 1
    assert receipt["goal_continuation_turn_completed_count"] == 0
    assert receipt["token_budget_present"] is True
    assert "Finish the task." not in rendered
    assert "Implement the requested behavior" not in rendered
    assert "/workspace/case" not in rendered


@pytest.mark.parametrize("token_budget", [0, -1, True, 1.5])
def test_non_positive_or_boolean_token_budget_fails_before_transport(
    token_budget: object,
) -> None:
    transport = FakeTransport()
    with pytest.raises(ValueError, match="positive integer"):
        start_native_goal_turn(transport, _config(token_budget=token_budget))
    assert transport.calls == []


def test_goal_identity_mismatch_fails_closed() -> None:
    with pytest.raises(NativeGoalProtocolError, match="goal_objective_mismatch"):
        start_native_goal_turn(FakeTransport(goal_objective="Different"), _config())


def test_required_skills_are_proven_before_thread_start() -> None:
    transport = FakeTransport(skill_names=("loopx", "loopx-project", "other"))

    turn = start_native_goal_turn(
        transport,
        _config(required_skill_ids=("loopx", "loopx-project")),
    )

    methods = [method for method, _ in transport.calls]
    assert methods[:4] == ["initialize", "initialized", "skills/list", "thread/start"]
    assert turn.required_skill_ids == ("loopx", "loopx-project")
    assert turn.discovered_required_skill_ids == ("loopx", "loopx-project")
    assert turn.skill_catalog_count == 3
    receipt = compact_native_goal_receipt(turn)
    assert receipt["required_skills_discovered"] is True
    assert receipt["skill_error_count"] == 0


def test_missing_or_invalid_required_skills_fail_before_thread_start() -> None:
    missing = FakeTransport(skill_names=("loopx",))
    with pytest.raises(NativeGoalProtocolError, match="required_skills_missing"):
        start_native_goal_turn(
            missing,
            _config(required_skill_ids=("loopx", "loopx-project")),
        )
    assert [method for method, _ in missing.calls][-1] == "skills/list"

    invalid = FakeTransport(
        skill_names=("loopx",),
        skill_errors=({"message": "invalid", "path": "/redacted"},),
    )
    with pytest.raises(NativeGoalProtocolError, match="skills_list_errors:1"):
        start_native_goal_turn(
            invalid,
            _config(required_skill_ids=("loopx",)),
        )
    assert [method for method, _ in invalid.calls][-1] == "skills/list"

    wrong_cwd = FakeTransport(skill_names=("loopx",), skill_cwd="/wrong-cwd")
    with pytest.raises(NativeGoalProtocolError, match="skills_list_cwd_mismatch"):
        start_native_goal_turn(
            wrong_cwd,
            _config(required_skill_ids=("loopx",)),
        )
    assert [method for method, _ in wrong_cwd.calls][-1] == "skills/list"


def test_terminal_event_preserves_failed_turn_status() -> None:
    turn = start_native_goal_turn(FakeTransport(), _config())
    assert (
        observe_native_goal_event(
            turn,
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread-1",
                    "turn": {"id": "response-turn", "status": "failed"},
                },
            },
        )
        is True
    )
    assert turn.turn_status == "failed"


@pytest.mark.parametrize("timeout", [None, 1])
def test_goal_runtime_waits_for_automatic_continuation_until_terminal(timeout) -> None:
    transport = ContinuationTransport()

    turn = run_native_goal_until_terminal(transport, _config(), timeout_sec=timeout)

    methods = [method for method, _ in transport.calls]
    assert methods.count("turn/start") == 1
    assert methods.count("thread/goal/get") == 3
    assert turn.post_goal_status == "complete"
    assert turn.turn_started_count == 2
    assert turn.turn_completed_count == 2
    assert turn.goal_status_poll_count == 2
    assert turn.turn_id == "event-turn-2"
    assert (
        compact_native_goal_receipt(turn)["goal_continuation_turn_completed_count"] == 1
    )


def test_goal_runtime_exposes_typed_deadline_when_active_goal_never_continues() -> None:
    transport = ContinuationTransport(terminal_after_second_turn=False)
    transport.events = transport.events[:2]
    observed = []

    with pytest.raises(
        NativeGoalDeadlineExceeded,
        match="goal_timeout_before_terminal",
    ):
        run_native_goal_until_terminal(
            transport,
            _config(),
            timeout_sec=0.01,
            on_turn_started=observed.append,
        )
    assert len(observed) == 1
    assert observed[0].turn_completed_count == 1
    assert observed[0].goal_status == "active"


def _write_fake_app_server(path: Path) -> None:
    path.write_text(
        textwrap.dedent(
            r"""
            #!/usr/bin/env python3
            import json
            import os
            import sys

            expected_process_cwd = os.environ.get("EXPECTED_PROCESS_CWD")
            if expected_process_cwd:
                assert os.getcwd() == expected_process_cwd
            objective = ""
            goal_reads = 0
            continuation_enabled = os.environ.get("FAKE_GOAL_CONTINUATION") == "1"
            for line in sys.stdin:
                request = json.loads(line)
                method = request.get("method")
                request_id = request.get("id")
                params = request.get("params") or {}
                if method == "initialized":
                    continue
                if method == "initialize":
                    result = {"serverInfo": {"name": "fixture"}}
                elif method == "thread/start":
                    assert params["model"] == "model-route"
                    expected_goal_cwd = os.environ.get("EXPECTED_GOAL_CWD")
                    if expected_goal_cwd:
                        assert params["cwd"] == expected_goal_cwd
                    result = {"thread": {"id": "thread-1"}}
                elif method == "thread/goal/set":
                    objective = params["objective"]
                    result = {"goal": {"threadId": "thread-1"}}
                elif method == "thread/goal/get":
                    goal_reads += 1
                    result = {
                        "goal": {
                            "threadId": "thread-1",
                            "objective": objective,
                            "status": (
                                "complete"
                                if continuation_enabled and goal_reads >= 3
                                else "active"
                            ),
                        }
                    }
                elif method == "turn/start":
                    assert params["sandboxPolicy"]["networkAccess"] is False
                    result = {"turn": {"id": "response-turn", "status": "inProgress"}}
                else:
                    raise AssertionError(method)
                print(json.dumps({"id": request_id, "result": result}), flush=True)
                if method == "turn/start":
                    print(json.dumps({
                        "method": "turn/started",
                        "params": {
                            "threadId": "thread-1",
                            "turn": {"id": "event-turn", "status": "inProgress"},
                        },
                    }), flush=True)
                    print(json.dumps({
                        "method": "item/agentMessage/delta",
                        "params": {"threadId": "thread-1", "turnId": "event-turn"},
                    }), flush=True)
                    print(json.dumps({
                        "method": "turn/completed",
                        "params": {
                            "threadId": "thread-1",
                            "turn": {"id": "event-turn", "status": "completed"},
                        },
                    }), flush=True)
                elif (
                    method == "thread/goal/get"
                    and continuation_enabled
                    and goal_reads == 2
                ):
                    for event_method, turn_id in (
                        ("turn/started", "event-turn-2"),
                        ("turn/completed", "event-turn-2"),
                    ):
                        print(json.dumps({
                            "method": event_method,
                            "params": {
                                "threadId": "thread-1",
                                "turn": {"id": turn_id, "status": "completed"},
                            },
                        }), flush=True)
            """
        ).lstrip(),
        encoding="utf-8",
    )
    path.chmod(0o755)


def test_real_stdio_process_runs_complete_native_goal_transaction(
    tmp_path: Path,
) -> None:
    fake_server = tmp_path / "fake-codex"
    _write_fake_app_server(fake_server)
    config = _config(
        cwd=str(tmp_path),
        sandbox_policy={
            "type": "workspaceWrite",
            "writableRoots": [str(tmp_path)],
            "networkAccess": False,
        },
    )

    turn = run_native_goal_process(
        config,
        process_command=[sys.executable, str(fake_server)],
        response_timeout_sec=2,
        goal_timeout_sec=2,
    )

    assert turn.response_turn_id == "response-turn"
    assert turn.turn_id == "event-turn"
    assert turn.event_turn_id_observed is True
    assert turn.terminal_event_observed is True
    assert turn.post_goal_status == "active"
    assert turn.notification_counts == {
        "item/agentMessage/delta": 1,
        "turn/completed": 1,
        "turn/started": 1,
    }


def test_process_cwd_can_differ_from_goal_thread_cwd(tmp_path: Path) -> None:
    fake_server = tmp_path / "fake-codex"
    _write_fake_app_server(fake_server)
    process_cwd = tmp_path / "process"
    goal_cwd = tmp_path / "goal"
    process_cwd.mkdir()
    goal_cwd.mkdir()
    process_env = dict(os.environ)
    process_env.update(
        {
            "EXPECTED_PROCESS_CWD": str(process_cwd),
            "EXPECTED_GOAL_CWD": str(goal_cwd),
        }
    )

    turn = run_native_goal_process(
        _config(
            cwd=str(goal_cwd),
            sandbox_policy={
                "type": "workspaceWrite",
                "writableRoots": [str(goal_cwd)],
                "networkAccess": False,
            },
        ),
        process_command=[sys.executable, str(fake_server)],
        process_env=process_env,
        process_cwd=str(process_cwd),
        response_timeout_sec=2,
        goal_timeout_sec=2,
    )

    assert turn.terminal_event_observed is True


@pytest.mark.parametrize("timeout", [None, 2])
def test_real_stdio_process_waits_until_native_goal_is_terminal(
    tmp_path: Path, timeout,
) -> None:
    fake_server = tmp_path / "fake-continuing-codex"
    _write_fake_app_server(fake_server)
    process_env = dict(os.environ)
    process_env["FAKE_GOAL_CONTINUATION"] = "1"

    turn = run_native_goal_process_until_terminal(
        _config(
            cwd=str(tmp_path),
            sandbox_policy={
                "type": "workspaceWrite",
                "writableRoots": [str(tmp_path)],
                "networkAccess": False,
            },
        ),
        process_command=[sys.executable, str(fake_server)],
        process_env=process_env,
        response_timeout_sec=2,
        goal_timeout_sec=timeout,
    )

    assert turn.post_goal_status == "complete"
    assert turn.turn_started_count == 2
    assert turn.turn_completed_count == 2
    assert turn.goal_status_poll_count == 2


def test_real_stdio_preflight_attaches_goal_without_starting_turn(
    tmp_path: Path,
) -> None:
    fake_server = tmp_path / "fake-codex"
    _write_fake_app_server(fake_server)

    turn = probe_native_goal_process(
        _config(cwd=str(tmp_path)),
        process_command=[sys.executable, str(fake_server)],
        response_timeout_sec=2,
    )

    assert turn.goal_status == "active"
    assert turn.turn_id == ""
    assert turn.methods[-1] == "thread/goal/get"


def test_runnable_example_connects_to_stdio_app_server(tmp_path: Path) -> None:
    fake_server = tmp_path / "fake-codex"
    _write_fake_app_server(fake_server)
    objective = tmp_path / "objective.txt"
    task = tmp_path / "task.txt"
    objective.write_text("\nFinish the task.\n", encoding="utf-8")
    task.write_text("\nImplement the requested behavior.\n", encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "benchmark" / "deepswe" / "run_native_codex_goal.py"),
            "--cwd",
            str(tmp_path),
            "--objective-file",
            str(objective),
            "--task-file",
            str(task),
            "--codex-bin",
            str(fake_server),
            "--model",
            "model-route",
            "--preflight-only",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    receipt = json.loads(completed.stdout)
    assert receipt["execution_mode"] == "goal_attachment_preflight"
    assert receipt["goal_status"] == "active"
    assert receipt["turn_id_present"] is False
    summary = receipt["public_trajectory_summary"]
    assert summary["schema_version"] == "public_trajectory_summary_v0"
    assert summary["adapter_id"] == "deepswe-native-codex-goal"
    assert summary["lifecycle_state"] == "attached"
    assert summary["complete"] is False
    assert str(tmp_path) not in completed.stdout


def test_runnable_example_isolation_recovers_and_restores_loopx_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    private_root = tmp_path / "controller-private"
    workspace = private_root / "task-workspace"
    workspace.mkdir(parents=True)
    work_dir = tmp_path / "run-work"
    work_dir.mkdir()
    profile_root = tmp_path / "installed-profile"
    profile_root.mkdir()
    alias = work_dir / "host-visible"
    alias.mkdir()
    objective = tmp_path / "objective.txt"
    task = tmp_path / "task.txt"
    objective.write_text("Finish the task.\n", encoding="utf-8")
    task.write_text("Implement the requested behavior.\n", encoding="utf-8")

    project_registry = workspace / ".loopx/registry.json"
    global_registry = workspace / ".loopx/runtime/registry.global.json"
    global_registry.parent.mkdir(parents=True)
    for registry in (project_registry, global_registry):
        registry.write_text(
            json.dumps({"workspace": str(alias)}, indent=2) + "\n",
            encoding="utf-8",
        )

    def fake_envelope(**kwargs: Any) -> NativeCodexIsolationEnvelope:
        assert kwargs["workspace_source"] == workspace
        assert kwargs["private_root"] == private_root
        assert kwargs["profile_root"] == profile_root
        return NativeCodexIsolationEnvelope(
            process_command=("isolated-codex", "app-server"),
            work_dir=work_dir,
            workspace_alias=alias,
            profile_root=profile_root,
        )

    observed: dict[str, Any] = {}

    def fake_probe(config: NativeGoalConfig, **kwargs: Any) -> object:
        observed["config"] = config
        observed["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(
        runnable_goal,
        "build_native_codex_isolation_envelope",
        fake_envelope,
    )
    monkeypatch.setattr(runnable_goal, "probe_native_goal_process", fake_probe)
    monkeypatch.setattr(
        runnable_goal,
        "compact_native_goal_receipt",
        lambda _: {
            "schema_version": "native_codex_goal_turn_receipt_v0",
            "goal_status": "active",
            "post_goal_status": None,
            "turn_status": "not_started",
            "terminal_event_observed": False,
            "item_event_count": 0,
            "error_event_count": 0,
            "turn_started_count": 0,
            "turn_completed_count": 0,
            "goal_continuation_turn_completed_count": 0,
            "goal_status_poll_count": 0,
            "notification_counts": {},
            "turn_id_present": False,
        },
    )

    assert (
        runnable_goal.main(
            [
                "--cwd",
                str(workspace),
                "--objective-file",
                str(objective),
                "--task-file",
                str(task),
                "--codex-bin",
                "codex",
                "--isolate",
                "--isolation-work-dir",
                str(work_dir),
                "--private-root",
                str(private_root),
                "--profile-root",
                str(profile_root),
                "--preflight-only",
            ]
        )
        == 0
    )

    config = observed["config"]
    assert isinstance(config, NativeGoalConfig)
    assert config.cwd == str(alias)
    assert observed["kwargs"] == {
        "codex_bin": "codex",
        "process_command": ("isolated-codex", "app-server"),
        "process_cwd": str(work_dir),
        "response_timeout_sec": 30,
    }
    for registry in (project_registry, global_registry):
        assert json.loads(registry.read_text(encoding="utf-8")) == {
            "workspace": str(workspace)
        }

    output = capsys.readouterr().out
    receipt = json.loads(output)
    assert receipt["native_isolation"] == {
        "control_state_found": True,
        "enabled": True,
        "prelaunch_replacement_count": 2,
        "profile_bound": True,
        "recovered_replacement_count": 2,
        "restored_replacement_count": 2,
        "workspace_alias_used": True,
    }
    assert str(workspace) not in output
    assert str(alias) not in output
