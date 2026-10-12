from pathlib import Path
from types import SimpleNamespace
import json
import os
import subprocess
import sys
import tempfile

import pytest

from benchmark.edgebench.prompts import (
    blind_task_prompt, best_only_task_prompt, native_task_prompt, native_task_instructions,
    native_task_objective,
)


def test_blind_retains_optimization_contract_without_judge_or_background_signal():
    pytest.importorskip("sforge")
    from sforge.harness.evolve_scripts import generate_evolve_prompt

    query = "Read task_instruction.md; satisfy its acceptance criteria.\n### Scoring\nTask-owned text."
    paths = ["solver.py", "results.json"]
    native = generate_evolve_prompt(query, paths, internet=False, submission_cooldown=120)
    blind = blind_task_prompt(query, paths)
    # Independently specified obligations, shared with the native task wrapper.
    for obligation in [
        "**Implement incrementally**", "**Iterate**",
        "You don't lose points for failed attempts", "experimentation is encouraged",
        "**Keep these files in a compilable/runnable state at all times.**",
        "Write changes to disk promptly", "current best solution",
        "**This environment has NO internet access.**", "`solver.py`", "`results.json`",
    ]:
        assert obligation in native and obligation in blind
    assert blind.endswith("---\n\n" + query + "\n")
    assert "local validation" in blind and "available local evidence" in blind
    wrapper = blind.removesuffix(query + "\n")
    for hidden_surface in ["sforge-submit", "judge server", "auto-eval", "background",
                           "300", "120", "Submission Limits", "(0 total)"]:
        assert hidden_surface not in wrapper


@pytest.mark.parametrize("render", [blind_task_prompt, best_only_task_prompt])
@pytest.mark.parametrize("policy,direction", [
    ("score_first", "maximize"), ("score_first", "minimize"),
    ("valid_then_score", "maximize"), ("pass_rate_first", "maximize"),
])
def test_restricted_wrapper_leaves_grading_and_final_selection_to_task(render, policy, direction):
    query = (f"Task final selection: {policy}; score direction: {direction}.\n"
             "All original acceptance criteria must be met.")
    prompt = render(query, ["solver.py"])
    assert prompt.endswith("---\n\n" + query + "\n")
    wrapper = prompt.removesuffix(query + "\n")
    assert "task's grading and final-selection rules determine your result" in wrapper
    assert "**best score**" not in wrapper
    assert "current best solution, judged from available local evidence" in wrapper
    assert "local tests and validation" in wrapper
    assert "Only the AI API is reachable" in wrapper


@pytest.mark.parametrize("profile", [
    "official", "single", "native-goal", "heartbeat-resume", "heartbeat-explore",
])
@pytest.mark.parametrize("feedback", ["native", "blind", "best-only"])
def test_each_worker_receives_and_records_selected_prompt(tmp_path, monkeypatch, profile, feedback):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from sforge.harness.config import SForgeConfig
    from benchmark.runtime.sforge import SForgeWorker

    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", "/private-credential")
    workspace = tmp_path / "worker's workspace"
    workspace.mkdir()
    agents = workspace / "AGENTS.md"
    agents.write_text("Task-owned instructions: preserve the interface.\n")
    instructions = None
    objective = None
    if feedback == "native":
        prompt = native_task_prompt("Complete the supplied task.", ["solver.py"],
            submission_cooldown=3600, eval_interval=0, internet=False,
            selection="pass_rate_first", score_direction="maximize")
        instructions = native_task_instructions(["solver.py"],
            submission_cooldown=3600, eval_interval=0, internet=False,
            selection="pass_rate_first", score_direction="maximize")
        objective = native_task_objective()
    else:
        render = best_only_task_prompt if feedback == "best-only" else blind_task_prompt
        prompt = render("Complete the supplied task.", ["solver.py"])
    worker = SForgeWorker(SForgeConfig(agent_model="fixture", agent_effort="xhigh"),
                         profile=profile, cwd=str(workspace), feedback_prompt=prompt,
                         feedback=feedback, workspace_instructions=instructions,
                         task_objective=objective)
    started = []
    worker.backend = SimpleNamespace(start_feedback=lambda handle: started.append(handle))
    worker.handle = "worker-handle"
    worker.log_dir = tmp_path
    remote = tmp_path / "remote-prompt.md"
    remote.write_text("old native wrapper")
    (tmp_path / "agent_prompt.md").write_text("old native wrapper")
    received, uploads = [], []

    class Environment:
        async def upload_file(self, source, target):
            Path(target).write_text(Path(source).read_text())
            uploads.append(target)

        async def exec(self, command):
            result = subprocess.run(["/bin/bash", "-c", command], capture_output=True, text=True)
            return SimpleNamespace(return_code=result.returncode, stdout=result.stdout)

    async def prepare(environment, text, **kwargs):
        received.append(text)

    async def configure(*args, **kwargs):
        return {"ok": True}

    worker.environment = Environment()
    worker.runtime = SimpleNamespace(
        execution=SimpleNamespace(uses_loopx=profile.startswith("heartbeat-")),
        _prepare_phase=prepare, _write_task_document=prepare, _loopx=configure,
        _worker_env=lambda **kwargs: {}, planning_timeout=90,
    )
    monkeypatch.setattr("benchmark.runtime.sforge.worker_command", lambda *a, **kw: ["worker"])
    worker.format_run_cmd(str(remote), internet=False)
    assert remote.read_text() == prompt == (tmp_path / "agent_prompt.md").read_text()
    assert received == ([] if profile in {"official", "single"} else [prompt])
    if feedback == "native":
        assert agents.read_text().startswith("Task-owned instructions: preserve the interface.\n")
        assert agents.read_text().endswith(instructions)
        assert prompt.startswith(instructions)
        assert objective == worker.task_objective and objective in agents.read_text()
        assert "`sforge-submit --result ID --details`" in agents.read_text()
        assert "`sforge-submit --help`" in prompt
        assert (tmp_path / "workspace-AGENTS.md").read_text() == agents.read_text()
        worker.instructions_installed = False  # Safe readback retry does not duplicate the block.
        worker._install_workspace_instructions()
        assert agents.read_text().count("<!-- EdgeBench native feedback policy -->") == 1
    else:
        assert agents.read_text() == "Task-owned instructions: preserve the interface.\n"
        assert not (tmp_path / "workspace-AGENTS.md").exists()
    agents.write_text(agents.read_text() + "\nLater task work.\n")
    worker.format_run_cmd(str(remote), internet=False, resume=True)
    assert agents.read_text().endswith("Later task work.\n")
    assert len(started) == (2 if feedback == "best-only" else 0)
    assert len(uploads) == 1  # Resume does not replace a live task context.


def test_native_research_guidance_preserves_original_task_without_budget_injection():
    query = "Original task: maximize A subject to B. Keep native acceptance and ranking."
    options = dict(submission_cooldown=3600, eval_interval=0, internet=False,
                   selection="score_first", score_direction="maximize")
    policy = native_task_instructions(["solution.py"], **options)
    assert native_task_prompt(query, ["solution.py"], **options) == policy + "---\n\n" + query + "\n"
    assert native_task_objective() in policy
    assert "total trial budget" not in policy
    assert "remaining time" not in policy


@pytest.mark.parametrize("objective", ["", "  ", 64800, "Unrelated objective"])
def test_native_goal_transport_rejects_objective_outside_task_policy(objective, tmp_path, monkeypatch):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from sforge.harness.config import SForgeConfig
    from benchmark.runtime.sforge import SForgeWorker

    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", "/synthetic-credential")
    with pytest.raises(ValueError, match="Task objective"):
        SForgeWorker(SForgeConfig(agent_model="fixture", agent_effort="xhigh"),
            profile="heartbeat-resume", cwd=str(tmp_path),
            feedback_prompt="Resolved native policy.\nTask",
            workspace_instructions="Resolved native policy.\n", task_objective=objective)


@pytest.mark.parametrize("profile", ["heartbeat-resume", "heartbeat-explore"])
def test_native_research_objective_and_full_planning_input_survive_real_goal_bootstrap(
        tmp_path, monkeypatch, profile):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from sforge.harness.config import SForgeConfig
    from benchmark.runtime import harbor
    from benchmark.runtime.planning import task_plan_packet
    from benchmark.runtime.sforge import SForgeWorker, CodexOffline

    project, control = tmp_path / "project", tmp_path / "control"
    project.mkdir()
    control.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    cli = tmp_path / "loopx"
    cli.write_text(f"#!{sys.executable}\nfrom loopx.cli import main\nraise SystemExit(main())\n")
    cli.chmod(0o755)
    for name, value in {"_CONTROL": control, "_TASK_DOC": control / "task.md",
                        "_REGISTRY": control / "registry.json", "_CLI": cli,
                        "_LOOPX_RUNTIME": tmp_path / "runtime"}.items():
        monkeypatch.setattr(harbor, name, str(value))
    # Exercise real shell, task uploads, bootstrap/configure and planner without
    # staging binaries, contacting a model or creating a task/grader container.
    env = dict(os.environ) | {"HOME": str(home), "CODEX_HOME": str(home / "codex"),
        "PYTHONPATH": str(Path(__file__).resolve().parents[2]), "LOOPX_USAGE_PING": "0",
        "TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)}
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(harbor.BenchmarkCodex, "_profile_env", lambda self: env)
    async def installed(self, environment):
        pass
    monkeypatch.setattr(CodexOffline, "install", installed)
    monkeypatch.setattr(harbor.BenchmarkCodex, "install", installed)
    class Environment:
        async def exec(self, command, *, env=None, cwd=None, **kwargs):
            result = subprocess.run(["/bin/bash", "-c", command], env=env,
                cwd=cwd, text=True, capture_output=True, timeout=90)
            return SimpleNamespace(return_code=result.returncode, stdout=result.stdout, stderr=result.stderr)
        async def upload_file(self, source, target):
            Path(target).write_bytes(Path(source).read_bytes())
    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", "/synthetic-credential")
    options = dict(submission_cooldown=3600, eval_interval=0, internet=False,
                   selection="score_first", score_direction="maximize")
    query = "Optimize original metric A while preserving hard constraint B."
    policy = native_task_instructions(["solution.py"], **options)
    prompt = native_task_prompt(query, ["solution.py"], **options)
    worker = SForgeWorker(SForgeConfig(agent_model="fixture", agent_effort="xhigh"),
        profile=profile, cwd=str(project), feedback_prompt=prompt, workspace_instructions=policy,
        task_objective=native_task_objective())
    worker.install_stop_hook(None, None, tmp_path, None)
    worker.environment = Environment()
    remote = control / "entry.md"
    try:
        command = worker.format_run_cmd(str(remote), internet=False)
        registry = json.loads(Path(harbor._REGISTRY).read_text())
        state = Path(registry["goals"][0]["state_file"])
        if not state.is_absolute():
            state = project / state
        assert worker.task_objective in state.read_text()
        assert remote.read_text() == prompt
        assert policy in (project / "AGENTS.md").read_text()
        packet = task_plan_packet(env | worker.runtime._worker_env(cwd=str(project)),
            [str(cli), "--registry", harbor._REGISTRY, "--runtime-root", harbor._LOOPX_RUNTIME,
             "--format", "json"])
        assert packet["text"].endswith(prompt)
        assert worker.task_objective in packet["text"] and query in packet["text"]
        assert packet["existing_todos"] == [] and packet["execution_handoff"]["spends_quota"] is False
        assert worker.format_run_cmd(str(remote), internet=False, resume=True) == command
        assert (project / "AGENTS.md").read_text().count(native_task_objective()) == 1
    finally:
        subprocess.run([sys.executable, "-c", "from loopx.control_plane.effect_runtime import "
            "effect_runtime_result; effect_runtime_result('runtime.shutdown',{},retry_safe=False)"],
            env=env, check=True, capture_output=True, text=True, timeout=30)


def test_native_formatting_does_not_rewrite_prompt(tmp_path, monkeypatch):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from sforge.harness.config import SForgeConfig
    from sforge.harness.agent.codex import CodexAgent
    from benchmark.runtime.sforge import SForgeWorker

    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", "/private-credential")
    config = SForgeConfig(agent_model="fixture", agent_effort="xhigh")
    prompt = tmp_path / "agent_prompt.md"
    prompt.write_text("native task wrapper")
    worker = SForgeWorker(config, profile="official", cwd="/task")
    worker.log_dir = tmp_path
    assert worker.format_run_cmd(str(prompt), internet=False) == CodexAgent(config).format_run_cmd(
        str(prompt), internet=False)
    assert prompt.read_text() == "native task wrapper"


def test_best_only_wrapper_preserves_task_and_limits_signal_meaning():
    query = "External evaluation and evaluation feedback are unavailable. TASK TEXT"
    text = best_only_task_prompt(query, ["solver.py"])
    assert text.endswith("---\n\n" + query + "\n")
    for clause in ["first valid result", "baseline", "Ties, regressions", "not necessarily",
                   "automatically adds", "acceptance criteria", "source_archive", "do not need to poll"]:
        assert clause in text
    assert "sforge-submit" not in text


@pytest.mark.parametrize("internet,interval", [(False, 0), (True, 600)])
@pytest.mark.parametrize("selection,direction", [
    ("score_first", "maximize"), ("pass_rate_first", "minimize"),
])
def test_native_candidate_prompt_describes_actual_feedback_and_preserves_objective(
        internet, interval, selection, direction):
    query = "TASK: prioritize metric A; B is a hard constraint.\nDo not change task scoring."
    text = native_task_prompt(query, ["candidate.py", "output.json"],
        submission_cooldown=3600, eval_interval=interval, internet=internet,
        selection=selection, score_direction=direction)
    assert text.endswith("---\n\n" + query + "\n")
    wrapper = text.removesuffix(query + "\n")
    for clause in ["**3600 seconds**", "first accepted submission", "not from result arrival",
                   "ties and regressions", "make a new submission", "without creating a new evaluation",
                   "current promising candidate", "separate checkpoint", "every local proxy metric",
                   "original objective", "hard constraints", "not necessarily your current files",
                   "Do not idle", "not zero scores", f"`{selection}`", f"`{direction}`"]:
        assert clause in wrapper
    assert "current best solution" not in wrapper
    assert ("NO internet access" in wrapper) is (not internet)
    assert ("every 600 seconds" in wrapper) is (interval > 0)
    assert ("Periodic automatic evaluation is disabled" in wrapper) is (interval == 0)
    assert "not a score-only filter" in wrapper


def test_native_recovery_distinguishes_existing_result_from_new_submission():
    policy = native_task_instructions(["candidate.py"],
        submission_cooldown=3600, eval_interval=0, internet=False,
        selection="score_first", score_direction="maximize")
    for clause in ["`sforge-submit --help`", "clients that advertise `--result ID`",
                   "`sforge-submit --result ID --details`", "read or poll",
                   "does not create a new evaluation or consume another submission",
                   "does not restart the cooldown", "If the ID was not received",
                   "inspect `--list` first", "do not guess an ID or blindly resubmit",
                   "token-visible history", "missing session or result",
                   "unresolved feedback"]:
        assert clause in policy


@pytest.mark.parametrize("existing", ["absent", "partial", "symlink", "directory"])
def test_workspace_feedback_instructions_preserve_files_and_fail_closed(tmp_path, monkeypatch, existing):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from sforge.harness.config import SForgeConfig
    from benchmark.runtime.sforge import SForgeWorker
    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", "/synthetic-credential")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    agents = workspace / "AGENTS.md"
    outside = tmp_path / "outside.md"
    outside.write_text("Do not overwrite.\n")
    if existing == "partial":
        agents.write_text("Original task.\n<!-- EdgeBench native feedback policy -->\npartial")
    elif existing == "symlink":
        agents.symlink_to(outside)
    elif existing == "directory":
        agents.mkdir()
    class Environment:
        async def exec(self, command):
            result = subprocess.run(["/bin/bash", "-c", command], capture_output=True, text=True)
            return SimpleNamespace(return_code=result.returncode, stdout=result.stdout)
    worker = SForgeWorker(SForgeConfig(agent_model="fixture", agent_effort="xhigh"),
        profile="official", cwd=str(workspace), feedback_prompt="Resolved native policy.\nNative task",
        workspace_instructions="Resolved native policy.\n")
    worker.environment, worker.log_dir = Environment(), tmp_path
    if existing == "absent":
        worker._install_workspace_instructions()
        assert agents.read_text().endswith("Resolved native policy.\n")
        assert (tmp_path / "workspace-AGENTS.md").read_text() == agents.read_text()
    else:
        with pytest.raises(RuntimeError, match="AGENTS.md"):
            worker._install_workspace_instructions()
        assert not worker.instructions_installed
        assert not (tmp_path / "workspace-AGENTS.md").exists()
        if existing == "partial":
            assert agents.read_text().endswith("partial")
    assert outside.read_text() == "Do not overwrite.\n"


@pytest.mark.parametrize("feedback,prompt,instructions", [
    ("blind", None, None), ("best-only", None, None),
    ("native", "Native task", "Different policy"), ("native", None, "Policy"),
    ("native", "Native task", ""), ("blind", "Policy task", "Policy"),
])
def test_workspace_policy_cannot_drift_from_feedback_mode_or_startup(
        monkeypatch, feedback, prompt, instructions):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from sforge.harness.config import SForgeConfig
    from benchmark.runtime.sforge import SForgeWorker
    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", "/synthetic-credential")
    with pytest.raises(ValueError):
        SForgeWorker(SForgeConfig(agent_model="fixture", agent_effort="xhigh"),
            profile="official", cwd="/task", feedback=feedback,
            feedback_prompt=prompt, workspace_instructions=instructions)
