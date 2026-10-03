from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmark.runtime.codex import Execution
from benchmark.runtime.planning import validate_plan_readback
from benchmark.runtime.worker import run_once


@pytest.mark.parametrize(
    "kwargs",
    [
        {"task_entry": "unknown"},
        {"mode": "plain", "task_entry": "loopx-planned"},
        {"mode": "native-goal", "task_entry": "loopx-planned"},
    ],
)
def test_invalid_entry_rejected_before_model_call(kwargs):
    with pytest.raises(ValueError):
        Execution(**kwargs)


@pytest.fixture
def planning_env(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    task = tmp_path / "task.md"
    task.write_text("Repair the failure and preserve a regression test.\n")
    state = tmp_path / "state.md"
    state.write_text("# Active Goal State\n\n## Agent Todos\n\n## User Todos\n")
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "goals": [
                    {
                        "id": "planning-goal",
                        "repo": str(project),
                        "state_file": str(state),
                        "status": "active",
                        "coordination": {"registered_agents": ["planner"]},
                    }
                ],
            }
        )
    )
    cli = tmp_path / "loopx"
    cli.write_text(
        f"#!{sys.executable}\nfrom loopx.cli import main\nraise SystemExit(main())\n"
    )
    cli.chmod(0o755)
    skills = tmp_path / "skills"
    skills.mkdir()
    binary = tmp_path / "codex"
    binary.write_text(
        f"#!{sys.executable}\n"
        + """
import json, os, pathlib, subprocess, sys
print(json.dumps({"type": "thread.started", "thread_id": "planning-fixture-session"}), flush=True)
packet = json.loads(sys.stdin.read().split("Host-supplied planning checkpoint:\\n", 1)[1])
command = [os.environ["LOOPX_CLI"], "--format", "json", "--registry", os.environ["LOOPX_REGISTRY"],
           "--runtime-root", os.environ["LOOPX_RUNTIME_ROOT"], "todo", "add", "--goal-id", "planning-goal",
           "--role", "agent", "--claimed-by", "planner",
           "--text", "[P0] Reproduce and repair the failure, then pass the regression.",
           "--task-class", "advancement_task", "--action-kind", "implement", "--execute"]
if not packet["runnable_todo_ids"]:
    written = json.loads(subprocess.run(command, check=True, capture_output=True, text=True).stdout)
    todo_id = written["todo_id"]
else:
    todo_id = packet["runnable_todo_ids"][0]
result = {"input_digest": packet["input_digest"], "status": "ready", "todo_ids": [todo_id]}
pathlib.Path(sys.argv[sys.argv.index("--output-last-message") + 1]).write_text(json.dumps(result))
pathlib.Path(os.environ["CODEX_HOME"], "seen-argv.json").write_text(json.dumps(sys.argv))
"""
    )
    binary.chmod(0o755)
    return dict(os.environ) | {
        "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
        "LOOPX_EXECUTION_MODE": "heartbeat",
        "LOOPX_TASK_ENTRY": "loopx-planned",
        "LOOPX_TASK_STAGE": "plan",
        "LOOPX_PLANNING_TIMEOUT_SEC": "30",
        "LOOPX_PLANNING_RESULT": str(tmp_path / "planning.json"),
        "LOOPX_GOAL_ID": "planning-goal",
        "LOOPX_AGENT_ID": "planner",
        "LOOPX_REGISTRY": str(registry),
        "LOOPX_RUNTIME_ROOT": str(tmp_path / "runtime"),
        "LOOPX_CLI": str(cli),
        "CODEX_BIN": str(binary),
        "LOOPX_PROJECT": str(project),
        "LOOPX_TASK_DOC": str(task),
        "LOOPX_CODEX_HOME": str(tmp_path / "home"),
        "LOOPX_SHARED_SKILLS": str(skills),
        "LOOPX_WAKE_LOG_DIR": str(tmp_path / "logs" / "wakes"),
        "MODEL_NAME": "fixture",
        "REASONING_EFFORT": "high",
        "OPENAI_BASE_URL": "http://localhost:8123/v1",
        "OPENAI_API_KEY": "fixture-key",
    }


def test_planning_writes_real_todo_then_reuses_it_without_executing_task(planning_env):
    ids = []
    for _ in range(2):
        receipt = run_once(planning_env)
        assert receipt["ok"] and receipt["planning"]["state_readback_verified"]
        ids.append(receipt["planning"]["todo_ids"])
    state = Path(planning_env["LOOPX_REGISTRY"]).with_name("state.md").read_text()
    assert ids[0] == ids[1] and len(ids[0]) == 1
    assert state.count("todo_id=" + ids[0][0]) == 1
    assert not list(Path(planning_env["LOOPX_PROJECT"]).iterdir())
    argv = json.loads(
        (Path(planning_env["LOOPX_CODEX_HOME"]) / "seen-argv.json").read_text()
    )
    assert "features.goals=false" in argv and "resume" not in argv
    assert not list(
        Path(planning_env["LOOPX_RUNTIME_ROOT"]).rglob("benchmark-pending-turn.json")
    )


def test_prose_or_fabricated_todo_does_not_qualify_planning(planning_env):
    binary = Path(planning_env["CODEX_BIN"])
    binary.write_text(
        binary.read_text().replace(
            '"todo_ids": [todo_id]', '"todo_ids": ["todo_fabricated"]'
        )
    )
    with pytest.raises(ValueError, match="missing or unrelated"):
        run_once(planning_env)
    assert not Path(planning_env["LOOPX_PLANNING_RESULT"]).exists()
    receipt = json.loads(
        next(
            Path(planning_env["LOOPX_WAKE_LOG_DIR"]).glob("*/receipt.json")
        ).read_text()
    )
    # A zero host exit must not be mistaken for qualified planning.
    assert (
        not receipt["ok"]
        and receipt.get("planning") is None
        and receipt["error_kind"] == "ValueError"
    )


def test_plan_readback_rejects_stale_input_unclaimed_work_and_false_blockers():
    packet = {
        "input_digest": "input-a",
        "goal_id": "goal",
        "agent_id": "agent",
        "existing_todos": [
            {"todo_id": "todo_owned"},
            {"todo_id": "todo_unclaimed"},
            {"todo_id": "todo_gate"},
        ],
        "runnable_todo_ids": ["todo_owned"],
        "blocking_todo_ids": ["todo_gate"],
    }
    result = {"input_digest": "input-a", "status": "ready", "todo_ids": ["todo_owned"]}
    assert validate_plan_readback(result, packet, packet)["status"] == "ready"
    assert (
        validate_plan_readback(
            result | {"status": "blocked", "todo_ids": ["todo_gate"]}, packet, packet
        )["status"]
        == "blocked"
    )
    for invalid in [
        result | {"input_digest": "old"},
        result | {"todo_ids": ["todo_unclaimed"]},
        result | {"status": "blocked"},
        result | {"todo_ids": ["todo_owned", "todo_owned"]},
    ]:
        with pytest.raises(ValueError):
            validate_plan_readback(invalid, packet, packet)


def test_planned_phase_preserves_waits_and_does_not_prewrite_a_todo(
    tmp_path, monkeypatch
):
    pytest.importorskip("harbor")
    from benchmark.runtime.harbor import BenchmarkCodex

    agent = BenchmarkCodex(
        logs_dir=tmp_path, model_name="openai/fixture", task_entry="loopx-planned"
    )
    calls = []

    async def prepared(*args, **kwargs):
        return True

    async def cli(environment, args, **kwargs):
        calls.append(args)
        return {"after": {"execution_profile": {"replan_after_completed_todos": 3}}}

    async def no_pending(**kwargs):
        return SimpleNamespace(return_code=1)

    monkeypatch.setattr(agent, "_write_task_document", prepared)
    monkeypatch.setattr(agent, "_registry_exists", prepared)
    monkeypatch.setattr(agent, "_loopx", cli)
    asyncio.run(
        agent._prepare_phase(
            SimpleNamespace(exec=no_pending), "new feedback", cwd=str(tmp_path)
        )
    )
    assert all(args[:2] != ["todo", "add"] for args in calls)
    assert all(
        "--clear-waiting-on" not in args and "--agent-work-mode" not in args
        for args in calls
    )


@pytest.mark.parametrize("status", ["ready", "blocked"])
def test_planning_budget_and_blocked_handoff_use_the_real_adapter_run(
    tmp_path, monkeypatch, status
):
    pytest.importorskip("harbor")
    from benchmark.runtime import harbor

    agent = harbor.BenchmarkCodex(
        logs_dir=tmp_path,
        model_name="openai/fixture",
        task_entry="loopx-planned",
        turn_timeout_sec=250,
        scheduler_timeout_sec=500,
    )
    clock = [0.0]
    executions = []

    async def prepare(*args, **kwargs):
        pass

    async def execute(environment, *, command, env=None, **kwargs):
        if command == "pwd":
            return SimpleNamespace(stdout="/workspace", return_code=0)
        if env.get("LOOPX_TASK_STAGE") == "plan":
            clock[0] = 200
        else:
            executions.append((command, env))
        return SimpleNamespace(stdout="", return_code=0)

    async def read_result(**kwargs):
        return SimpleNamespace(
            stdout=json.dumps({"status": status, "state_readback_verified": True})
        )

    monkeypatch.setattr(harbor.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(agent, "_prepare_phase", prepare)
    monkeypatch.setattr(agent, "exec_as_agent", execute)
    monkeypatch.setattr(agent, "_populate_context", lambda *args: None)
    environment = SimpleNamespace(exec=read_result, is_mounted=True)
    asyncio.run(agent.run("Synthetic task", environment, SimpleNamespace()))
    assert len(executions) == (1 if status == "ready" else 0)
    if executions:
        command, env = executions[0]
        assert "--kill-after=30 300s" in command
        assert float(env["LOOPX_CODEX_TURN_TIMEOUT_SEC"]) == 140
        assert "LOOPX_PHASE_DEADLINE_EPOCH=$(( $(date +%s) + 300 ))" in command


def test_pending_turn_prevents_phase_input_replacement(tmp_path, monkeypatch):
    pytest.importorskip("harbor")
    from benchmark.runtime.harbor import BenchmarkCodex

    agent = BenchmarkCodex(logs_dir=tmp_path, model_name="openai/fixture")

    async def pending(**kwargs):
        return SimpleNamespace(return_code=0)

    async def unexpected_write(*args, **kwargs):
        pytest.fail("pending transaction input must not be replaced")

    monkeypatch.setattr(agent, "_write_task_document", unexpected_write)
    with pytest.raises(RuntimeError, match="pending Turn"):
        asyncio.run(
            agent._prepare_phase(
                SimpleNamespace(exec=pending), "next task", cwd=str(tmp_path)
            )
        )


def test_late_scheduler_wake_does_not_open_an_unfinishable_turn(planning_env, monkeypatch):
    from benchmark.runtime import worker

    env = planning_env | {
        "LOOPX_EXECUTION_MODE": "turn",
        "LOOPX_TASK_STAGE": "execute",
        "LOOPX_VALIDATION_COMMAND_JSON": '["python", "check.py"]',
        "LOOPX_CODEX_TURN_TIMEOUT_SEC": "60",
        "LOOPX_PHASE_DEADLINE_EPOCH": "260",
    }
    monkeypatch.setattr(worker.time, "time", lambda: 100)
    monkeypatch.setattr(worker, "prepare_codex_home", lambda *a, **kw: pytest.fail("late wake must not launch a host"))
    for entry in ("seeded-todo", "loopx-planned"):
        receipt = run_once(env | {"LOOPX_TASK_ENTRY": entry})
        assert receipt["budget_exhausted"] and receipt["host_invoked"] is False
        assert receipt.get("turn_execution") is None
    assert not (Path(env["LOOPX_RUNTIME_ROOT"]) / "benchmark-pending-turn.json").exists()


def test_remaining_phase_time_caps_later_host_windows(planning_env, monkeypatch):
    from benchmark.runtime import worker

    class CapturedWindow(Exception):
        pass

    def capture(home, *, execution, **kwargs):
        assert execution.timeout_seconds == 40
        raise CapturedWindow

    monkeypatch.setattr(worker.time, "time", lambda: 100)
    monkeypatch.setattr(worker, "prepare_codex_home", capture)
    with pytest.raises(CapturedWindow):
        run_once(planning_env | {
            "LOOPX_TASK_STAGE": "execute",
            "LOOPX_CODEX_TURN_TIMEOUT_SEC": "60",
            "LOOPX_PHASE_DEADLINE_EPOCH": "300",
        })


@pytest.mark.parametrize("status", ["open", "blocked", "done", "deferred"])
def test_seeded_followup_uses_real_todo_delta_without_reviving_terminal_work(
    planning_env, tmp_path, monkeypatch, status
):
    import contextlib
    import io
    pytest.importorskip("harbor")
    from benchmark.runtime import harbor
    from loopx.cli import main

    monkeypatch.setattr(harbor, "_GOAL_ID", "planning-goal")
    monkeypatch.setattr(harbor, "_AGENT_ID", "planner")
    agent = harbor.BenchmarkCodex(logs_dir=tmp_path, model_name="openai/fixture")

    async def cli(environment, args, **kwargs):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main([
                "--format", "json", "--registry", planning_env["LOOPX_REGISTRY"],
                "--runtime-root", planning_env["LOOPX_RUNTIME_ROOT"], *args,
            ])
        assert code == 0, output.getvalue()
        return json.loads(output.getvalue())

    monkeypatch.setattr(agent, "_loopx", cli)

    async def scenario():
        agent._phase_number = 1
        await agent._seed_phase(None, cwd=planning_env["LOOPX_PROJECT"])
        original = agent._seeded_todo_id
        transition = (["complete", "--no-follow-up", "--note", "Synthetic task independently validated; no remaining work"]
                      if status == "done" else ["update", "--status", status])
        if status == "deferred":
            transition += ["--resume-when", "capacity_available:fixture_pool"]
        await cli(None, ["todo", *transition, "--goal-id", "planning-goal",
                        "--todo-id", original, "--agent-id", "planner", "--execute"])
        agent._phase_number = 2
        await agent._seed_phase(None, cwd=planning_env["LOOPX_PROJECT"])
        listed = await cli(None, ["todo", "list", "--goal-id", "planning-goal", "--role", "agent"])
        todos = {t["todo_id"]: t for t in listed["todos"]}
        if status in {"open", "blocked"}:
            assert agent._seeded_todo_id == original and len(todos) == 1
            assert todos[original]["status"] == status
            assert "task-phase-002.md" in todos[original]["text"]
        else:
            assert agent._seeded_todo_id != original and len(todos) == 2
            assert todos[original]["status"] == status
            assert "task-phase-001.md" in todos[original]["text"]

    asyncio.run(scenario())
