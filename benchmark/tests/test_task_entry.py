from __future__ import annotations

import asyncio
import json
import os
import shlex
import subprocess
import sys
import time
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


@pytest.mark.parametrize("mode", ["heartbeat", "turn", "loopx-goal", "plain", "native-goal"])
def test_task_entry_defaults_to_planning_only_for_loopx_modes(mode):
    kwargs = {"mode": mode}
    if mode == "turn":
        kwargs["validation_command"] = ("python", "validate.py")
    expected = "loopx-planned" if mode in {"heartbeat", "turn", "loopx-goal"} else "seeded-todo"
    assert Execution(**kwargs).task_entry == expected
    assert Execution(**kwargs, task_entry="seeded-todo").task_entry == "seeded-todo"


@pytest.mark.parametrize("entry", ["loopx-planned", "seeded-todo"])
@pytest.mark.parametrize("strategy", [None, "off", "evidence_conditioned_v1"])
def test_execution_observation_keeps_task_entry_and_stride_independent(tmp_path, entry, strategy):
    pytest.importorskip("harbor")
    from benchmark.runtime.harbor import BenchmarkCodex

    agent = BenchmarkCodex(logs_dir=tmp_path, model_name="fixture", task_entry=entry,
        stride_strategy=strategy, execution_observation="local_probe")
    env = agent._worker_env(cwd=str(tmp_path))
    assert agent.execution.task_entry == entry
    assert agent.stride_strategy == strategy
    assert agent.research_loop is False
    assert env["LOOPX_EXECUTION_OBSERVATION"] == "local_probe"
    assert "LOOPX_RESEARCH_LOOP" not in env


@pytest.mark.parametrize("settings", [
    {"execution_observation": True},
    {"execution_observation": False},
    {"execution_observation": ""},
    {"execution_observation": "unavailable-producer"},
    {"execution_observation": "local_probe", "execution_mode": "plain"},
    {"execution_observation": "local_probe", "execution_mode": "native-goal"},
    {"execution_observation": "local_probe", "execution_mode": "loopx-goal"},
    {"execution_observation": "local_probe", "execution_mode": "turn",
     "validation_command": ["python", "validate.py"]},
    {"execution_observation": "local_probe", "research_loop": True},
])
def test_execution_observation_rejects_ambiguous_or_unsupported_treatments(tmp_path, settings):
    pytest.importorskip("harbor")
    from benchmark.runtime.harbor import BenchmarkCodex

    with pytest.raises(ValueError, match="execution_observation"):
        BenchmarkCodex(logs_dir=tmp_path, model_name="fixture", **settings)


def test_research_preset_and_ordinary_default_remain_distinct(tmp_path):
    pytest.importorskip("harbor")
    from benchmark.runtime.harbor import BenchmarkCodex
    from benchmark.runtime.planning import render_planning_prompt
    from benchmark.runtime.research_loop import research_execution_guidance

    ordinary = BenchmarkCodex(logs_dir=tmp_path / "ordinary", model_name="fixture")
    plain_env = ordinary._worker_env(cwd=str(tmp_path))
    assert "LOOPX_RESEARCH_LOOP" not in plain_env
    assert "LOOPX_EXECUTION_OBSERVATION" not in plain_env
    assert ordinary.stride_strategy is None
    preset = BenchmarkCodex(logs_dir=tmp_path / "preset", model_name="fixture", research_loop=True)
    preset_env = preset._worker_env(cwd=str(tmp_path))
    assert preset_env["LOOPX_RESEARCH_LOOP"] == "1"
    assert preset.stride_strategy == "evidence_conditioned_v1"
    packet = {"input_digest": "original", "text": "Test the supplied candidate."}
    ordinary_prompt = render_planning_prompt(packet, plain_env)
    preset_guidance = research_execution_guidance()
    preset_prompt = render_planning_prompt(packet, preset_env)
    assert preset_prompt.count(preset_guidance) == 1
    assert preset_prompt.replace(preset_guidance, "") == ordinary_prompt
    with pytest.raises(ValueError, match="research_loop"):
        BenchmarkCodex(logs_dir=tmp_path / "conflicting", model_name="fixture",
            research_loop=True, stride_strategy="off")


@pytest.mark.parametrize("selection", [
    {"LOOPX_EXECUTION_OBSERVATION": "unknown"},
    {"LOOPX_EXECUTION_OBSERVATION": "local_probe", "LOOPX_EXECUTION_MODE": "plain"},
    {"LOOPX_EXECUTION_OBSERVATION": "local_probe", "LOOPX_RESEARCH_LOOP": "1"},
])
def test_raw_worker_rejects_invalid_observation_before_creating_a_wake(tmp_path, selection):
    wakes = tmp_path / "wakes"
    with pytest.raises(ValueError, match="execution_observation|LOOPX_EXECUTION_OBSERVATION"):
        run_once({"LOOPX_EXECUTION_MODE": "heartbeat", "LOOPX_WAKE_LOG_DIR": str(wakes), **selection})
    assert not wakes.exists()


@pytest.mark.parametrize("settings,research_preset", [({}, False), ({"execution_observation": "local_probe"}, False),
    ({"research_loop": True}, True)])
def test_install_records_and_exposes_only_the_selected_observation_producer(tmp_path, monkeypatch, settings, research_preset):
    pytest.importorskip("harbor")
    from benchmark.runtime.harbor import BenchmarkCodex, CodexOffline

    agent = BenchmarkCodex(logs_dir=tmp_path, model_name="fixture", **settings)
    commands = []

    async def no_op(*args, **kwargs):
        pass

    async def stage(*args, **kwargs):
        agent._runner_commit = "fixture"
        return "fixture"

    async def execute(*args, **kwargs):
        commands.append(kwargs["command"])
        return SimpleNamespace(stdout='{"ok":true}', stderr="", return_code=0)

    async def task_exec(**kwargs):
        return SimpleNamespace(stdout="/usr/local/bin:/usr/bin:/bin", return_code=0)

    for key in ("LOOPX_SRC_DIR", "LOOPX_PORTABLE_PYTHON", "LOOPX_NODE_DIR"):
        monkeypatch.setenv(key, str(tmp_path))
    monkeypatch.setattr(CodexOffline, "install", no_op)
    monkeypatch.setattr(agent, "_stage_source", stage)
    monkeypatch.setattr(agent, "exec_as_root", execute)
    monkeypatch.setattr(agent, "exec_as_agent", execute)
    monkeypatch.setattr(agent, "_get_env", lambda key: None)
    asyncio.run(agent.install(SimpleNamespace(default_user="fixture", upload_dir=no_op, exec=task_exec)))
    selected = bool(settings)
    assert any("-m benchmark.runtime.research_probe" in command for command in commands) is selected
    command = next(command for command in commands if "> /logs/agent/loopx-install.json" in command)
    receipt = json.loads(shlex.split(command)[2])
    assert receipt.get("execution_observation") == ("local_probe" if selected else None)
    assert receipt.get("research_loop", False) is research_preset
    assert receipt["task_entry"] == "loopx-planned"
    assert receipt.get("stride_strategy") == ("evidence_conditioned_v1" if research_preset else None)


@pytest.fixture
def planning_env(tmp_path):
    from loopx.control_plane.coordination.runtime_shadow import (
        build_todo_runtime_shadow_projection,
    )
    from tests.control_plane.canonical_authority_fixture import (
        initialize_canonical_authority,
    )

    project = tmp_path / "project"
    project.mkdir()
    task = tmp_path / "task.md"
    task.write_text("Repair the failure and preserve a regression test.\n")
    state = tmp_path / "state.md"
    state.write_text(
        "# Active Goal State\n\n"
        "## User Todo / Owner Review Reading Queue\n\n"
        "## Agent Todo\n\n"
        "## Completed Work Archive\n"
    )
    runtime = tmp_path / "runtime"
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": "planning-goal",
                        "repo": str(project),
                        "domain": "project",
                        "state_file": str(state),
                        "status": "active",
                        "adapter": {"kind": "read_only_project_map_v0", "status": "connected-read-only"},
                        "coordination": {"registered_agents": ["planner"]},
                    }
                ],
            }
        )
    )
    initialize_canonical_authority(
        runtime,
        "planning-goal",
        build_todo_runtime_shadow_projection(
            goal_id="planning-goal", todos=[], handoff_mode="soft_claim"
        ),
        state_path=state,
        provider="file",
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
        "LOOPX_PHASE_DEADLINE_EPOCH": str(time.time() + 600),
        "LOOPX_PLANNING_RESULT": str(tmp_path / "planning.json"),
        "LOOPX_GOAL_ID": "planning-goal",
        "LOOPX_AGENT_ID": "planner",
        "LOOPX_REGISTRY": str(registry),
        "LOOPX_RUNTIME_ROOT": str(runtime),
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


@pytest.mark.parametrize("context", ["fresh", "resume"])
def test_planning_writes_real_todo_then_reuses_it_without_executing_task(
    planning_env, context
):
    planning_env["LOOPX_ITERATION_CONTEXT"] = context
    if context == "fresh":
        planning_env.pop("LOOPX_TASK_ENTRY")  # Omitted worker setting uses the same planner.
    ids = []
    for invocation in range(2):
        receipt = run_once(planning_env)
        assert receipt["ok"] and receipt["planning"]["state_readback_verified"]
        # Planning cannot attest to an execution that has not happened yet.
        assert "planning_session_reused_for_execution" not in receipt["planning"]
        persisted = json.loads(Path(planning_env["LOOPX_PLANNING_RESULT"]).read_text())
        assert persisted == receipt["planning"]
        expected_action = "resume" if context == "resume" and invocation else "start_new"
        assert receipt["session"]["action"] == expected_action
        assert receipt["session"]["session_id"] == "planning-fixture-session"
        ids.append(receipt["planning"]["todo_ids"])
    state = Path(planning_env["LOOPX_REGISTRY"]).with_name("state.md").read_text()
    assert ids[0] == ids[1] and len(ids[0]) == 1
    assert state.count("todo_id=" + ids[0][0]) == 1
    assert not list(Path(planning_env["LOOPX_PROJECT"]).iterdir())
    argv = json.loads(
        (Path(planning_env["LOOPX_CODEX_HOME"]) / "seen-argv.json").read_text()
    )
    assert "features.goals=false" in argv
    assert ("resume" in argv) == (context == "resume")
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


@pytest.mark.parametrize("producer", [None, "local_probe"])
def test_planner_keeps_host_clock_out_of_model_input_and_preserves_task_identity(planning_env, producer):
    from benchmark.runtime.planning import task_plan_packet
    from benchmark.runtime.research_probe import execution_observation_guidance
    from benchmark.runtime.worker import loopx_command

    deadline = str(time.time() + 600)
    env = planning_env | {"LOOPX_PHASE_DEADLINE_EPOCH": deadline}
    if producer is not None:
        env["LOOPX_EXECUTION_OBSERVATION"] = producer
    before = task_plan_packet(env, loopx_command(env))
    receipt = run_once(env)
    assert receipt["ok"] and receipt["planning"]["state_readback_verified"]
    assert receipt.get("execution_observation") == producer
    body = next(Path(env["LOOPX_WAKE_LOG_DIR"]).glob("*/planning-input.json")).read_text()
    assert deadline not in body
    assert "Host execution window" not in body
    assert "Existing execution reserve" not in body
    assert body.count(execution_observation_guidance()) == int(producer is not None)
    assert "Research-loop execution evidence:" not in body
    packet = json.loads(body.split("Host-supplied planning checkpoint:\n", 1)[1])
    assert packet == before
    assert receipt["planning"]["input_digest"] == before["input_digest"]
    assert env["LOOPX_PHASE_DEADLINE_EPOCH"] == deadline
    assert not list(Path(env["LOOPX_PROJECT"]).iterdir())


def test_planning_input_is_clock_independent_but_preserves_task_time_constraints():
    from benchmark.runtime.planning import render_planning_prompt

    packet = {"input_digest": "unchanged", "text": "Task constraint: inference under 30 seconds."}
    body = render_planning_prompt(packet, {})
    for deadline in ["100", "1600", "9999999999"]:
        env = {"LOOPX_PHASE_DEADLINE_EPOCH": deadline}
        assert render_planning_prompt(packet, env) == body
        assert env == {"LOOPX_PHASE_DEADLINE_EPOCH": deadline}
    assert json.loads(body.split("Host-supplied planning checkpoint:\n", 1)[1]) == packet
    assert body.startswith("$loopx\n\nBenchmark research planning guidance:\n")
    assert "declared interface, units, scale" in body
    assert "preserve that reference while testing a replacement" in body
    assert "negative experiment can complete its own Todo" in body
    assert "neither creates a new acceptance document or permission requirement" in body
    assert "Host deadline" not in body and "remaining" not in body
    assert packet == {"input_digest": "unchanged", "text": "Task constraint: inference under 30 seconds."}


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "0", "-1", "not-a-clock"])
def test_planning_window_rejects_invalid_host_clock(value):
    from benchmark.runtime.planning import render_planning_prompt

    with pytest.raises(ValueError):
        render_planning_prompt({"input_digest": "unchanged"},
                               {"LOOPX_PHASE_DEADLINE_EPOCH": value})


def test_retired_planning_limit_fails_before_host_invocation(planning_env):
    with pytest.raises(ValueError, match="Remove LOOPX_PLANNING_TIMEOUT_SEC"):
        run_once(planning_env | {"LOOPX_PLANNING_TIMEOUT_SEC": "30"})
    assert not Path(planning_env["LOOPX_PLANNING_RESULT"]).exists()
    assert not Path(planning_env["LOOPX_CODEX_HOME"], "seen-argv.json").exists()


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
        return {"after": {"execution_profile": {"replan_after_effective_turns": 6}}}

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


@pytest.mark.parametrize("producer,objective,research_preset", [
    pytest.param(None, None, False, id="ordinary-default"),
    pytest.param("local_probe", None, False, id="ordinary-observed"),
    pytest.param("local_probe", "Validate the supplied candidate once.", False,
        id="ordinary-objective"),
    pytest.param(None, None, True, id="preset-default"),
    pytest.param(None, "Compare a validated alternative against the supplied candidate.", True,
        id="preset-objective"),
])
def test_goal_bootstrap_preserves_original_objective_and_selected_task_structure(
    tmp_path, monkeypatch, producer, objective, research_preset
):
    pytest.importorskip("harbor")
    from benchmark.runtime import harbor
    from benchmark.runtime.planning import task_plan_packet
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
    from loopx.control_plane.goals.active_state_metadata import split_state_frontmatter

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, control, home = (tmp_path / name for name in ("project", "control", "home"))
    for directory in (project, control, home):
        directory.mkdir()
    candidate = project / "solution.py"
    candidate.write_text("print(5)\n")
    before = candidate.read_bytes()
    cli = tmp_path / "loopx"
    cli.write_text(f"#!{sys.executable}\nfrom loopx.cli import main\nraise SystemExit(main())\n")
    cli.chmod(0o755)
    for key, value in {"_CONTROL": control, "_TASK_DOC": control / "task.md",
        "_REGISTRY": control / "registry.json", "_LOOPX_RUNTIME": tmp_path / "runtime",
        "_CLI": cli, "_SRC": Path(__file__).resolve().parents[2]}.items():
        monkeypatch.setattr(harbor, key, str(value))
    env = dict(os.environ) | {"HOME": str(home), "CODEX_HOME": str(home / "codex"),
        "PYTHONPATH": str(Path(__file__).resolve().parents[2]), "LOOPX_USAGE_PING": "0"}
    interrupt_successor = research_preset and objective is not None

    class Environment:
        async def exec(self, command, *, env=None, cwd=None, **kwargs):
            nonlocal interrupt_successor
            if interrupt_successor and "--successor-todo-id" in shlex.split(command):
                interrupt_successor = False
                # The two Todos are real durable writes; only this transport
                # attempt fails before the edge update reaches the CLI.
                raise RuntimeError("Simulated successor-update transmission failure")
            result = subprocess.run(["/bin/bash", "-c", command], env=env,
                cwd=cwd, text=True, capture_output=True, timeout=90)
            return SimpleNamespace(return_code=result.returncode, stdout=result.stdout, stderr=result.stderr)

        async def upload_file(self, source, target):
            Path(target).write_bytes(Path(source).read_bytes())

    agent = harbor.BenchmarkCodex(logs_dir=tmp_path / "logs", model_name="fixture",
        research_loop=research_preset, execution_observation=producer,
        stride_strategy=None if research_preset else "off", task_objective=objective)
    monkeypatch.setattr(agent, "_profile_env", lambda: env)
    task = ("Start from the supplied solution.py and compare one alternative against it.\n"
        "First preserve correctness, then minimize operations. Retain useful negative results.\n"
        "The final comparison must keep both priorities and this trailing requirement.")

    def call(*args):
        return asyncio.run(agent._loopx(Environment(), list(args), cwd=str(project)))

    try:
        if research_preset and objective is not None:
            with pytest.raises(RuntimeError, match="successor-update transmission failure"):
                asyncio.run(agent._prepare_phase(Environment(), task, cwd=str(project)))
            partial = call("todo", "list", "--goal-id", agent.goal_id, "--role", "agent")
            interrupted_todos = {todo["todo_id"]: todo for todo in partial["todos"]}
            assert len(interrupted_todos) == 2
            assert not interrupted_todos[agent._seeded_todo_id].get("successor_todo_ids")
            [interrupted_research] = [todo for todo in interrupted_todos.values()
                if todo.get("target_key") == "research-continuation"]
            assert interrupted_research["status"] == "deferred"
            interrupted_lease = call("task-lease", "inspect", "--goal-id", agent.goal_id,
                "--todo-id", agent._seeded_todo_id)["lease"]
            assert interrupted_lease["status"] == "released"
        asyncio.run(agent._prepare_phase(Environment(), task, cwd=str(project)))
        registry = json.loads(Path(harbor._REGISTRY).read_text())
        state = Path(registry["goals"][0]["state_file"])
        if not state.is_absolute():
            state = project / state
        assert split_state_frontmatter(state.read_text())[0]["objective"] == (objective or task)
        worker_env = agent._worker_env(cwd=str(project))
        packet = task_plan_packet(worker_env, [str(cli), "--format", "json",
            "--registry", harbor._REGISTRY, "--runtime-root", harbor._LOOPX_RUNTIME])
        assert packet["text"].endswith(task + "\n")
        if research_preset:
            assert call("handoff-mode", "show", "--goal-id", agent.goal_id)["handoff_mode"] == "hard_lease"
            listed = call("todo", "list", "--goal-id", agent.goal_id, "--role", "agent")
            todos = {todo["todo_id"]: todo for todo in listed["todos"]}
            assert len(todos) == 2
            initial = todos[agent._seeded_todo_id]
            [research] = [todo for todo in todos.values()
                if todo.get("target_key") == "research-continuation"]
            assert initial["status"] == "open"
            assert initial["successor_todo_ids"] == [research["todo_id"]]
            assert research["claimed_by"] == agent.agent_id
            assert research["status"] == "deferred"
            assert research["resume_when"] == "todo_done:" + initial["todo_id"]
            assert agent._task_document in research["text"]
            linked_lease = call("task-lease", "inspect", "--goal-id", agent.goal_id,
                "--todo-id", initial["todo_id"])["lease"]
            assert linked_lease["status"] == "released"
            if objective is not None:
                assert todos.keys() == interrupted_todos.keys()
                assert research["todo_id"] == interrupted_research["todo_id"]
                assert linked_lease["version"] > interrupted_lease["version"]
                assert linked_lease["idempotency_key"] != interrupted_lease["idempotency_key"]
                resume_when = "resume_at:2099-01-01T00:00:00Z"
                call("todo", "update", "--goal-id", agent.goal_id, "--agent-id", agent.agent_id,
                    "--todo-id", initial["todo_id"], "--status", "blocked", "--resume-when", resume_when,
                    "--reason", "Wait for the scheduled external observation.", "--execute")
                call("configure-goal", "--goal-id", agent.goal_id,
                    "--waiting-on", "external_evidence", "--execute")
        else:
            assert packet["existing_todos"] == []
        assert packet["execution_handoff"]["spends_quota"] is False
        agent._phase_number += 1
        feedback = "Released feedback is available; retain the original priorities and constraints."
        asyncio.run(agent._prepare_phase(Environment(), feedback, cwd=str(project)))
        next_packet = task_plan_packet(agent._worker_env(cwd=str(project)), [str(cli), "--format", "json",
            "--registry", harbor._REGISTRY, "--runtime-root", harbor._LOOPX_RUNTIME])
        assert next_packet["text"].endswith(feedback + "\n")
        if research_preset:
            next_listed = call("todo", "list", "--goal-id", agent.goal_id, "--role", "agent")
            next_todos = {todo["todo_id"]: todo for todo in next_listed["todos"]}
            assert next_todos.keys() == todos.keys()
            assert next_todos[initial["todo_id"]]["successor_todo_ids"] == [research["todo_id"]]
            next_research = next_todos[research["todo_id"]]
            for field in ("target_key", "claimed_by", "status", "resume_when", "text"):
                assert next_research[field] == research[field]
            next_lease = call("task-lease", "inspect", "--goal-id", agent.goal_id,
                "--todo-id", initial["todo_id"])["lease"]
            assert next_lease["status"] == "released" and next_lease["version"] == linked_lease["version"]
            if objective is not None:
                assert next_todos[initial["todo_id"]]["status"] == "blocked"
                assert next_todos[initial["todo_id"]]["resume_when"] == resume_when
                assert json.loads(Path(harbor._REGISTRY).read_text())["goals"][0]["waiting_on"] == "external_evidence"
        else:
            assert next_packet["existing_todos"] == []
        assert split_state_frontmatter(state.read_text())[0]["objective"] == (objective or task)
        assert candidate.read_bytes() == before
    finally:
        from loopx.control_plane.effect_runtime import restart_effect_runtime

        restart_effect_runtime(wait_for_exit=True)


@pytest.mark.parametrize("status,resume_when", [
    pytest.param("open", None, id="research-already-open"),
    pytest.param("done", None, id="research-completed"),
    pytest.param("deferred", "resume_at:2099-01-01T00:00:00Z", id="research-waiting"),
    pytest.param("deferred", "todo_done:prior-initial", id="different-initial-stage"),
])
def test_research_preset_reentry_preserves_existing_research_without_relinking(status, resume_when):
    from benchmark.runtime.research_loop import prepare_research_continuation

    research = {"todo_id": "retained-research", "target_key": "research-continuation",
        "claimed_by": "fixture-agent", "status": status, "resume_when": resume_when}
    calls = []

    async def call(environment, args, *, cwd):
        calls.append(args)
        assert args[:2] == ["todo", "list"], "Existing research cannot trigger preparation effects"
        return {"todos": [{"todo_id": "current-initial", "status": "open",
            "claimed_by": "fixture-agent", "successor_todo_ids": []}, research]}

    owner = SimpleNamespace(_seeded_todo_id="current-initial", _loopx=call,
        goal_id="fixture-goal", agent_id="fixture-agent", _task_document="task-phase-001.md")
    retained = asyncio.run(prepare_research_continuation(owner, None, cwd="."))
    assert retained == research
    assert calls == [["todo", "list", "--goal-id", owner.goal_id, "--role", "agent"]]


@pytest.mark.parametrize("status", ["ready", "blocked"])
def test_planning_budget_and_blocked_handoff_use_the_real_adapter_run(
    tmp_path, monkeypatch, status
):
    pytest.importorskip("harbor")
    from benchmark.runtime import harbor

    agent = harbor.BenchmarkCodex(
        logs_dir=tmp_path,
        model_name="openai/fixture",
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


@pytest.mark.parametrize("mode", ["heartbeat", "turn", "plain", "native-goal", "loopx-goal"])
def test_real_scheduler_stops_after_one_budget_exhausted_wake(planning_env, mode):
    from benchmark.runtime.scheduler import worker_command

    env = planning_env | {
        "LOOPX_EXECUTION_MODE": mode, "LOOPX_TASK_STAGE": "execute",
        "LOOPX_TASK_ENTRY": "seeded-todo",
        "LOOPX_PHASE_DEADLINE_EPOCH": str(time.time() + 100),
    }
    if mode == "turn":
        env["LOOPX_VALIDATION_COMMAND_JSON"] = '["python", "check.py"]'
    registry = Path(env["LOOPX_REGISTRY"])
    registered = json.loads(registry.read_text())
    registered["goals"][0]["domain"] = "project"
    registered["goals"][0]["adapter"] = {
        "kind": "read_only_project_map_v0", "status": "connected-read-only",
    }
    registry.write_text(json.dumps(registered))
    # An isolated File authority remains runnable: only the benchmark owner
    # knows its separate phase deadline. Exercise the real quota CLI, not a
    # synthetic scheduler decision that happens to terminate on the next read.
    cli = [env["LOOPX_CLI"], "--format", "json", "--registry", env["LOOPX_REGISTRY"],
           "--runtime-root", env["LOOPX_RUNTIME_ROOT"]]
    subprocess.run([*cli, "todo", "add", "--goal-id", env["LOOPX_GOAL_ID"],
                    "--role", "agent", "--claimed-by", env["LOOPX_AGENT_ID"],
                    "--text", "[P0] Complete synthetic task", "--execute"],
                   env=env, check=True, capture_output=True, text=True)
    def quota():
        result = subprocess.run([
            *cli, "quota", "should-run", "--goal-id", env["LOOPX_GOAL_ID"],
            "--agent-id", env["LOOPX_AGENT_ID"], "--runtime-profile", "generic_cli",
            "--include-detail", "scheduler",
        ], env=env, text=True, capture_output=True)
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)
    before = quota()
    assert before["should_run"] is True
    command = worker_command(env, python=sys.executable,
        source=str(Path(__file__).resolve().parents[2]),
        state_file=str(Path(env["LOOPX_RUNTIME_ROOT"]) / "scheduler.json"),
        host_timeout=500)
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    if mode in {"heartbeat", "turn"}:
        assert "status=wake_requested_stop" in result.stdout
    receipts = list(Path(env["LOOPX_WAKE_LOG_DIR"]).glob("*/receipt.json"))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text())
    assert receipt["budget_exhausted"] and receipt["host_invoked"] is False
    assert not Path(env["LOOPX_CODEX_HOME"]).exists()
    assert not (Path(env["LOOPX_RUNTIME_ROOT"]) / "benchmark-pending-turn.json").exists()
    after = quota()
    assert after["should_run"] is True
    assert after["quota"]["spent_slots"] == before["quota"]["spent_slots"]


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
def test_seeded_task_acceptance_survives_phase_update_without_reviving_terminal_work(
    planning_env, tmp_path, monkeypatch, status
):
    import contextlib
    import io
    pytest.importorskip("harbor")
    from benchmark.runtime import harbor
    from loopx.cli import main

    agent = harbor.BenchmarkCodex(logs_dir=tmp_path, model_name="openai/fixture",
                                 goal_id="planning-goal", agent_id="planner")

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
        first = await cli(None, ["todo", "list", "--goal-id", "planning-goal",
                                "--role", "agent", "--todo-id", original])
        initial_text = first["todo"]["text"]
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
        # Both the first task and a later phase keep the native task's full
        # acceptance in scope, without preplanning a successor or reviving work.
        assert todos[agent._seeded_todo_id]["text"] == (
            "[P0] Complete the task in /opt/loopx-benchmark/control/task-phase-002.md. "
            "Inspect the workspace, implement and validate against the task's full "
            "requirements and acceptance criteria. Keep unmet requirements explicit "
            "when judging task completion."
        )
        assert initial_text == todos[agent._seeded_todo_id]["text"].replace(
            "task-phase-002.md", "task-phase-001.md"
        )
        if status in {"open", "blocked"}:
            assert agent._seeded_todo_id == original and len(todos) == 1
            assert todos[original]["status"] == status
            assert "task-phase-002.md" in todos[original]["text"]
        else:
            assert agent._seeded_todo_id != original and len(todos) == 2
            assert todos[original]["status"] == status
            assert "task-phase-001.md" in todos[original]["text"]

    asyncio.run(scenario())


def test_sforge_planning_entry_runs_inside_native_process_and_reuses_receipt(planning_env):
    from benchmark.runtime.sforge_entry import prepare_entry
    env = planning_env | {"LOOPX_PHASE_DEADLINE_EPOCH": str(time.time() + 600)}
    assert prepare_entry(env)
    initial = list(Path(env["LOOPX_WAKE_LOG_DIR"]).glob("*/receipt.json"))
    assert len(initial) == 1
    assert prepare_entry(env)
    assert list(Path(env["LOOPX_WAKE_LOG_DIR"]).glob("*/receipt.json")) == initial
    # Real process boundary retains the native transport environment and deadline.
    marker = Path(env["LOOPX_PROJECT"]) / "handoff.json"
    target = "import os,json,pathlib; pathlib.Path(%r).write_text(json.dumps({k:os.environ[k] for k in ['HTTPS_PROXY','LOOPX_PHASE_DEADLINE_EPOCH','LOOPX_TASK_STAGE']}))" % str(marker)
    proxy = "http://api-only.invalid:9090"
    result = subprocess.run([sys.executable, "-m", "benchmark.runtime.sforge_entry",
                             sys.executable, "-c", target], env=env | {"HTTPS_PROXY": proxy},
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert Path(env["LOOPX_PLANNING_RESULT"]).with_name("execution-started").exists()
    assert json.loads(marker.read_text()) == {"HTTPS_PROXY": proxy,
        "LOOPX_PHASE_DEADLINE_EPOCH": env["LOOPX_PHASE_DEADLINE_EPOCH"],
        "LOOPX_TASK_STAGE": "execute"}
    saved = Path(env["LOOPX_PLANNING_RESULT"])
    valid = json.loads(saved.read_text())
    for mutation in ({"state_readback_verified": False}, {"todo_ids": ["missing"]},
                     {"status": "blocked"}, {"todo_ids": valid["todo_ids"] * 2}):
        saved.write_text(json.dumps(valid | mutation))
        with pytest.raises(ValueError):
            prepare_entry(env)
    changed = valid | {"input_digest": "stale"}
    saved.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="current task"):
        prepare_entry(env)


def test_sforge_planner_uses_trial_deadline_instead_of_a_stage_cap(planning_env, monkeypatch):
    from benchmark.runtime.sforge_entry import prepare_entry

    communicate = subprocess.Popen.communicate
    observed = []

    def capture(process, *args, **kwargs):
        if process.args[0] == planning_env["CODEX_BIN"]:
            observed.append(kwargs["timeout"])
        return communicate(process, *args, **kwargs)

    monkeypatch.setattr(subprocess.Popen, "communicate", capture)
    # Even an inherited shared-worker cap cannot shorten native SForge planning.
    env = planning_env | {"LOOPX_PHASE_DEADLINE_EPOCH": str(time.time() + 1200),
                          "LOOPX_PLANNING_TIMEOUT_SEC": "30"}
    assert prepare_entry(env)
    assert len(observed) == 1 and 1100 < observed[0] <= 1200
    assert Path(env["LOOPX_PLANNING_RESULT"]).exists()


@pytest.mark.parametrize("legacy_limit", [0, 30, 300, float("inf")])
def test_shared_harbor_rejects_retired_planning_limit(tmp_path, legacy_limit):
    pytest.importorskip("harbor")
    from benchmark.runtime.harbor import BenchmarkCodex

    with pytest.raises(ValueError, match="Remove planning_timeout_sec"):
        BenchmarkCodex(logs_dir=tmp_path, model_name="openai/fixture",
                       planning_timeout_sec=legacy_limit)
    BenchmarkCodex(logs_dir=tmp_path, model_name="openai/fixture", planning_timeout_sec=None)


@pytest.mark.parametrize("planning_elapsed", [400, 850])
def test_harbor_planning_uses_the_phase_deadline_without_a_stage_cap(tmp_path, monkeypatch, planning_elapsed):
    pytest.importorskip("harbor")
    from benchmark.runtime import harbor

    agent = harbor.BenchmarkCodex(logs_dir=tmp_path, model_name="fixture",
                                 turn_timeout_sec=500, scheduler_timeout_sec=1000)
    clock = [0.0]
    planning = []
    execution = []

    async def prepare(*args, **kwargs):
        pass

    async def execute(environment, *, command, env=None, **kwargs):
        if command == "pwd":
            return SimpleNamespace(stdout="/workspace", return_code=0)
        if env.get("LOOPX_TASK_STAGE") == "plan":
            planning.append((command, env, kwargs))
            # A valid plan may exceed the removed 300-second stage cap.
            clock[0] = float(planning_elapsed)
        else:
            execution.append((command, env, kwargs))
        return SimpleNamespace(stdout="", return_code=0)

    async def result(**kwargs):
        return SimpleNamespace(stdout=json.dumps({"status": "ready", "state_readback_verified": True}))

    monkeypatch.setattr(harbor.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(agent, "_prepare_phase", prepare)
    monkeypatch.setattr(agent, "exec_as_agent", execute)
    monkeypatch.setattr(agent, "_populate_context", lambda *args: None)
    invocation = agent.run("Synthetic task", SimpleNamespace(exec=result, is_mounted=True), SimpleNamespace())
    if planning_elapsed == 850:
        with pytest.raises(TimeoutError, match="before execution handoff"):
            asyncio.run(invocation)
        assert len(planning) == 1 and not execution
        return
    asyncio.run(invocation)

    assert len(planning) == len(execution) == 1
    command, env, options = planning[0]
    assert "LOOPX_PLANNING_TIMEOUT_SEC" not in env
    assert "LOOPX_PHASE_DEADLINE_EPOCH=$(( $(date +%s) + 1000 ))" in command
    assert options["timeout_sec"] >= 1000
    assert "--kill-after=30 600s" in execution[0][0]
    assert float(execution[0][1]["LOOPX_CODEX_TURN_TIMEOUT_SEC"]) == 440


def test_sforge_post_plan_deadline_never_marks_execution_started(tmp_path, monkeypatch):
    from benchmark.runtime import sforge_entry

    monkeypatch.setattr(sforge_entry, "prepare_entry", lambda env: True)
    monkeypatch.setenv("LOOPX_PHASE_DEADLINE_EPOCH", str(time.time() + 150))
    monkeypatch.setenv("LOOPX_PLANNING_RESULT", str(tmp_path / "planning.json"))
    monkeypatch.setattr(sys, "argv", ["sforge-entry", "execution-must-not-start"])
    monkeypatch.setattr(os, "execvpe", lambda *args: pytest.fail("Execution after exhausted planning"))
    assert sforge_entry.main() == 1
    assert not (tmp_path / "execution-started").exists()


def test_sforge_planning_exhausted_budget_never_invokes_host(planning_env):
    from benchmark.runtime.sforge_entry import prepare_entry
    env = planning_env | {"LOOPX_PHASE_DEADLINE_EPOCH": str(time.time() + 150)}
    assert not prepare_entry(env)
    assert not Path(env["LOOPX_PLANNING_RESULT"]).exists()
    assert not Path(env["LOOPX_WAKE_LOG_DIR"]).exists()
    result = subprocess.run([sys.executable, "-m", "benchmark.runtime.sforge_entry",
        sys.executable, "-c", "raise SystemExit('execution must not start')"],
        env=env, capture_output=True, text=True)
    assert result.returncode == 1
    assert "execution must not start" not in result.stderr
    assert not Path(env["LOOPX_PLANNING_RESULT"]).with_name("execution-started").exists()


def test_sforge_planning_failed_host_has_no_execution_handoff(planning_env):
    from benchmark.runtime.sforge_entry import prepare_entry
    Path(planning_env["CODEX_BIN"]).write_text(f"#!{sys.executable}\nraise SystemExit(1)\n")
    with pytest.raises(RuntimeError):
        prepare_entry(planning_env | {"LOOPX_PHASE_DEADLINE_EPOCH": str(time.time() + 600)})
    assert not Path(planning_env["LOOPX_PLANNING_RESULT"]).exists()


@pytest.mark.parametrize("settings,field,value", [
    ({}, "replan_after_effective_turns", 6),
    ({"planning_horizon_goal": True}, "replan_after_effective_turns", 6),
    ({"planning_horizon_goal": False}, "replan_after_effective_turns", 6),
    ({"replan_after_turns": 2}, "replan_after_effective_turns", 2),
    ({"replan_after_todos": 3}, "replan_after_completed_todos", 3),
    ({"stride_strategy": "off"}, "replan_after_effective_turns", 6),
    ({"stride_strategy": "evidence_conditioned_v1"}, "replan_after_effective_turns", 6),
    ({"stride_strategy": "evidence_conditioned_v1", "replan_after_todos": 2}, "replan_after_completed_todos", 2),
])
def test_runner_cadence_persists_through_real_configure_goal(
    planning_env, tmp_path, monkeypatch, settings, field, value
):
    import contextlib
    import io
    pytest.importorskip("harbor")
    from benchmark.runtime import harbor
    from loopx.cli import main

    agent = harbor.BenchmarkCodex(logs_dir=tmp_path, model_name="fixture",
                                 goal_id="planning-goal", agent_id="planner", **settings)

    async def cli(environment, args, **kwargs):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main([
                "--format", "json", "--registry", planning_env["LOOPX_REGISTRY"],
                "--runtime-root", planning_env["LOOPX_RUNTIME_ROOT"], *args,
            ])
        assert code == 0, output.getvalue()
        return json.loads(output.getvalue())

    async def prepared(*args, **kwargs):
        return True

    async def no_pending(**kwargs):
        return SimpleNamespace(return_code=1)

    monkeypatch.setattr(agent, "_loopx", cli)
    monkeypatch.setattr(agent, "_registry_exists", prepared)
    monkeypatch.setattr(agent, "_write_task_document", prepared)
    asyncio.run(agent._prepare_phase(SimpleNamespace(exec=no_pending), "Synthetic task",
                                     cwd=planning_env["LOOPX_PROJECT"]))
    readback = asyncio.run(cli(None, ["configure-goal", "--goal-id", "planning-goal"]))
    profile = readback["after"]["execution_profile"]
    assert profile[field] == value
    other = ("replan_after_completed_todos" if field == "replan_after_effective_turns"
             else "replan_after_effective_turns")
    assert other not in profile
    strategy = settings.get("stride_strategy")
    assert profile.get("stride_strategy", "off") == (strategy or "off")
    horizon = settings.get("planning_horizon_goal")
    assert profile.get("planning_horizon_goal", False) == (horizon or False)
    assert agent._replan_receipt() == {field: value,
        **({"planning_horizon_goal": horizon} if horizon is not None else {}), **({"stride_strategy": strategy}
                                                    if strategy is not None else {})}


def test_sforge_planning_entry_retries_a_dropped_planning_wake_within_budget(planning_env, monkeypatch):
    import benchmark.runtime.sforge_entry as entry

    real = entry.run_once
    seen = []

    def flaky(env):
        seen.append(env["LOOPX_TASK_STAGE"])
        if len(seen) == 1:
            # The native process died without a plan or a harness error.
            return {"ok": False, "timed_out": False, "return_code": 1}
        return real(env)

    monkeypatch.setattr(entry, "run_once", flaky)
    env = planning_env | {"LOOPX_PHASE_DEADLINE_EPOCH": str(time.time() + 600)}
    assert entry.prepare_entry(env)
    assert seen == ["plan", "plan"]
    attempts = json.loads(Path(env["LOOPX_PLANNING_RESULT"]).with_name("planning-attempts.json").read_text())
    assert [a["ok"] for a in attempts] == [False, True]
    assert attempts[0]["return_code"] == 1
    assert len(list(Path(env["LOOPX_WAKE_LOG_DIR"]).glob("*/receipt.json"))) == 1


@pytest.mark.parametrize("receipt", [
    {"ok": False, "timed_out": True, "return_code": 124},
    {"ok": False, "timed_out": False, "return_code": 1, "error_kind": "ValueError"},
    {"ok": False, "timed_out": False, "return_code": 0},
])
def test_sforge_planning_entry_does_not_retry_timeouts_or_harness_failures(planning_env, monkeypatch, receipt):
    import benchmark.runtime.sforge_entry as entry

    calls = []
    monkeypatch.setattr(entry, "run_once", lambda env: calls.append(env["LOOPX_TASK_STAGE"]) or dict(receipt))
    env = planning_env | {"LOOPX_PHASE_DEADLINE_EPOCH": str(time.time() + 600)}
    with pytest.raises(RuntimeError, match="Planning failed"):
        entry.prepare_entry(env)
    assert calls == ["plan"]
    attempts = json.loads(Path(env["LOOPX_PLANNING_RESULT"]).with_name("planning-attempts.json").read_text())
    assert len(attempts) == 1 and attempts[0]["ok"] is False


def test_sforge_planning_entry_stops_retrying_after_the_attempt_limit(planning_env, monkeypatch):
    import benchmark.runtime.sforge_entry as entry

    calls = []
    monkeypatch.setattr(entry, "run_once", lambda env: calls.append(1) or {"ok": False, "timed_out": False, "return_code": 1})
    env = planning_env | {"LOOPX_PHASE_DEADLINE_EPOCH": str(time.time() + 600)}
    with pytest.raises(RuntimeError, match="Planning failed"):
        entry.prepare_entry(env)
    assert len(calls) == entry.PLANNING_WAKE_ATTEMPTS


@pytest.mark.parametrize("context", ["fresh", "resume"])
@pytest.mark.parametrize("producer", [None, "local_probe"])
def test_execute_wake_keeps_host_clocks_out_of_fresh_and_resumed_input(planning_env, context, producer):
    """Exercise the real worker/heartbeat CLI and capture its model transport input."""
    from benchmark.runtime.research_probe import execution_observation_guidance

    env = planning_env | {"LOOPX_ITERATION_CONTEXT": context}
    if producer is not None:
        env["LOOPX_EXECUTION_OBSERVATION"] = producer
    assert run_once(env)["ok"]
    binary = Path(env["CODEX_BIN"])
    binary.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        "pathlib.Path(os.environ['CODEX_HOME'], 'execute-input.txt').write_text(sys.stdin.read())\n"
        "print(json.dumps({'type': 'thread.started', 'thread_id': 'planning-fixture-session'}))\n"
    )
    before = Path(env["LOOPX_REGISTRY"]).with_name("state.md").read_bytes()
    old_deadline = env["LOOPX_PHASE_DEADLINE_EPOCH"]
    for offset in [900, 500]:
        deadline = str(time.time() + offset)
        current = env | {"LOOPX_TASK_STAGE": "execute",
                         "LOOPX_PHASE_DEADLINE_EPOCH": deadline,
                         "LOOPX_CODEX_TURN_TIMEOUT_SEC": "120"}
        receipt = run_once(current)
        assert receipt["ok"]
        assert receipt.get("execution_observation") == producer
        body = (Path(env["LOOPX_CODEX_HOME"]) / "execute-input.txt").read_text()
        assert deadline not in body
        assert "Absolute phase deadline" not in body
        assert old_deadline not in body
        assert "Current wake execution limit" not in body
        assert "Existing execution reserve" not in body
        assert "The Host enforces runtime limits externally" not in body
        assert "heartbeat-prompt" in body or "quota" in body
        assert body.count(execution_observation_guidance()) == int(producer is not None)
        assert "Research-loop execution evidence:" not in body
        assert receipt["session"]["action"] == ("resume" if context == "resume" else "start_new")
        assert "turn_envelope" not in receipt
        assert current["LOOPX_PHASE_DEADLINE_EPOCH"] == deadline
    # Runtime clocks remain internal and never change task/completion authority.
    assert Path(env["LOOPX_REGISTRY"]).with_name("state.md").read_bytes() == before
