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
    native_research_guidance,
)


def test_blind_retains_optimization_and_capture_contract_without_judge_or_feedback_signal():
    pytest.importorskip("sforge")
    from sforge.harness.evolve_scripts import generate_evolve_prompt

    query = "Read task_instruction.md; satisfy its acceptance criteria.\n### Scoring\nTask-owned text."
    paths = ["solver.py", "results.json"]
    native = generate_evolve_prompt(query, paths, internet=False, submission_cooldown=120)
    blind = blind_task_prompt(query, paths, eval_interval=600)
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
    assert "configured interval: 600 seconds" in wrapper
    assert "You cannot submit evaluations or access official evaluation feedback" in wrapper
    for hidden_surface in ["sforge-submit", "judge server", "auto-eval",
                           "Submission Limits", "(0 total)"]:
        assert hidden_surface not in wrapper


@pytest.mark.parametrize("render", [blind_task_prompt, best_only_task_prompt])
@pytest.mark.parametrize("interval", [0, 600])
@pytest.mark.parametrize("policy,direction", [
    ("score_first", "maximize"), ("score_first", "minimize"),
    ("valid_then_score", "maximize"), ("pass_rate_first", "maximize"),
])
def test_restricted_wrapper_preserves_task_and_describes_host_sampling(render, interval, policy, direction):
    query = (f"Task final selection: {policy}; score direction: {direction}.\n"
             "All original acceptance criteria must be met.")
    prompt = render(query, ["solver.py"], eval_interval=interval)
    assert prompt.endswith("---\n\n" + query + "\n")
    wrapper = prompt.removesuffix(query + "\n")
    assert "task's grading and final-selection rules determine your result" in wrapper
    assert "**best score**" not in wrapper
    assert "current best solution, judged from available local evidence" in wrapper
    assert "local tests and validation" in wrapper
    assert "Only the AI API is reachable" in wrapper
    assert ("configured interval: 600 seconds" in wrapper) is (interval > 0)
    assert ("independently of manual submissions" in wrapper) is (interval > 0)
    assert ("Periodic automatic evaluation is disabled" in wrapper) is (interval == 0)
    assert "sforge-submit" not in wrapper


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
        objective = "Complete the supplied task."
    else:
        render = best_only_task_prompt if feedback == "best-only" else blind_task_prompt
        prompt = render("Complete the supplied task.", ["solver.py"], eval_interval=600)
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
        goal_id="fixture-goal",
        execution=SimpleNamespace(uses_loopx=profile.startswith("heartbeat-")),
        _prepare_phase=prepare, _write_task_document=prepare, _loopx=configure,
        _worker_env=lambda **kwargs: {},
    )
    monkeypatch.setattr("benchmark.runtime.sforge.worker_command", lambda *a, **kw: ["worker"])
    worker.format_run_cmd(str(remote), internet=False)
    assert remote.read_text() == prompt == (tmp_path / "agent_prompt.md").read_text()
    assert received == ([] if profile in {"official", "single"} else [prompt])
    if feedback == "native":
        assert agents.read_text().startswith("Task-owned instructions: preserve the interface.\n")
        assert agents.read_text().endswith(instructions)
        assert prompt.startswith(instructions)
        assert objective == worker.task_objective and objective in prompt
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


@pytest.mark.parametrize("query", [
    "Original task: maximize A subject to B. Keep native acceptance and ranking.",
    "Use an early diagnostic submission before implementing the remaining targets.",
    "Run the task's required local validation before submitting.\n### Strategy\nTask-owned text.",
    "Stop once the specified acceptance checks pass; no further optimization is requested. "
    "Respect explicit stop instructions, budget exhaustion, and genuine blockers.",
])
def test_native_context_preserves_objective_and_stops_without_imposing_work_order(query):
    options = dict(submission_cooldown=3600, eval_interval=0, internet=False,
                   selection="score_first", score_direction="maximize")
    policy = native_task_instructions(["solution.py"], **options)
    assert native_task_prompt(query, ["solution.py"], **options) == policy + "---\n\n" + query + "\n"
    assert "adds no local-test pass requirement or prior-improvement requirement" in policy
    assert "The task defines candidate validity and acceptance" in policy
    assert policy.count(native_research_guidance()) == 1
    assert "milestones; they do not by themselves establish completion" in policy
    assert "continue seeking useful improvements" in policy
    assert "while budget remains and further in-scope progress is feasible" in policy
    assert "Respect explicit stop instructions, budget exhaustion, and genuine blockers" not in policy
    assert "### Work Between Submissions" not in policy
    assert "Implement incrementally" not in policy
    assert "best validated candidate" not in policy
    assert "total trial budget" not in policy
    assert "remaining time" not in policy


@pytest.mark.parametrize("objective", ["", "  ", 64800, "Unrelated objective"])
def test_native_goal_transport_rejects_objective_outside_task_prompt(objective, tmp_path, monkeypatch):
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
                        "_LOOPX_RUNTIME": tmp_path / "runtime",
                        "_SRC": Path(__file__).resolve().parents[2]}.items():
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
    query = ("Optimize metric A first, then metric C, while preserving hard constraint B.\n"
             "Do not replace the original priorities with a local proxy.")
    policy = native_task_instructions(["solution.py"], **options)
    prompt = native_task_prompt(query, ["solution.py"], **options)
    worker = SForgeWorker(SForgeConfig(agent_model="fixture", agent_effort="xhigh"),
        profile=profile, cwd=str(project), feedback_prompt=prompt, workspace_instructions=policy,
        task_objective=query)
    worker.install_stop_hook(None, None, tmp_path, None)
    worker.environment = Environment()
    remote = control / "entry.md"
    try:
        command = worker.format_run_cmd(str(remote), internet=False)
        registry = json.loads(Path(harbor._REGISTRY).read_text())
        state = Path(registry["goals"][0]["state_file"])
        if not state.is_absolute():
            state = project / state
        from loopx.control_plane.goals.active_state_metadata import split_state_frontmatter
        assert split_state_frontmatter(state.read_text())[0]["objective"] == query
        assert policy not in state.read_text()
        assert remote.read_text() == prompt
        assert policy in (project / "AGENTS.md").read_text()
        packet = task_plan_packet(env | worker.runtime._worker_env(cwd=str(project)),
            [str(cli), "--registry", harbor._REGISTRY, "--runtime-root", harbor._LOOPX_RUNTIME,
             "--format", "json"])
        assert packet["text"].endswith(prompt)
        assert worker.task_objective in packet["text"] and query in packet["text"]
        assert packet["existing_todos"] == [] and packet["execution_handoff"]["spends_quota"] is False
        # Real typed replan owner reads the bootstrapped registry/state, not a
        # hand-built objective fixture. It must retain both ordered priorities
        # and the trailing constraint through the scoped source projection.
        readback = subprocess.run([sys.executable, "-c",
            "import json,sys; from loopx.control_plane.work_items.replan_context_codec "
            "import project_replan_context; print(json.dumps(project_replan_context("
            "goal_id='benchmark-goal',agent_id='benchmark-agent',runs=(),"
            "source_status={'registry':sys.argv[1],'runtime_root':sys.argv[2],'run_history':{'runs':[]}})))",
            harbor._REGISTRY, harbor._LOOPX_RUNTIME], env=env, cwd=project,
            capture_output=True, text=True, timeout=90)
        assert readback.returncode == 0, readback.stderr
        core = json.loads(readback.stdout)["core_goal"]
        assert core["objective"] == " ".join(query.split())
        assert core["objective_source"] == "active_state" and core["acceptance_contract"] is None
        assert worker.format_run_cmd(str(remote), internet=False, resume=True) == command
        assert (project / "AGENTS.md").read_text().count(policy) == 1
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
    text = best_only_task_prompt(query, ["solver.py"], eval_interval=600)
    assert text.endswith("---\n\n" + query + "\n")
    for clause in ["first valid result", "baseline", "Ties, regressions", "not necessarily",
                   "automatically adds", "acceptance criteria", "source_archive", "do not need to poll"]:
        assert clause in text
    assert "sforge-submit" not in text


@pytest.mark.parametrize("internet", [False, True])
@pytest.mark.parametrize("interval", [0, 600])
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
                   "`candidate.py`", "`output.json`", "submission/round identity",
                   "Native submissions and terminal capture read on-disk files",
                   "unsaved changes are not included", "recoverable copy of the best-supported candidate",
                   "Match a result to its source version",
                   "The task defines candidate validity and acceptance", "not necessarily your current files",
                   "not zero scores", f"`{selection}`", f"`{direction}`"]:
        assert clause in wrapper
    assert "current best solution" not in wrapper
    assert ("NO internet access" in wrapper) is (not internet)
    for clause in ["The host periodically samples", "configured interval", "independently of manual submissions",
                   "does not wait for editing or local validation to finish", "Keep a runnable candidate",
                   "write completed changes to disk promptly", "partially updated files can be sampled",
                   "These automatic results are hidden from you", "Periodic automatic evaluation is disabled"]:
        assert clause not in wrapper
    assert text == native_task_prompt(query, ["candidate.py", "output.json"],
        submission_cooldown=3600, eval_interval=600 if interval == 0 else 0, internet=internet,
        selection=selection, score_direction=direction)
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


def test_native_prompt_upload_is_readable_regardless_of_launcher_umask(tmp_path, monkeypatch):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from sforge.harness.config import SForgeConfig
    from benchmark.runtime.sforge import SForgeWorker

    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", "/private-credential")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    prompt = native_task_prompt("Complete the supplied task.", ["solver.py"],
        submission_cooldown=3600, eval_interval=0, internet=False,
        selection="pass_rate_first", score_direction="maximize")
    worker = SForgeWorker(SForgeConfig(agent_model="fixture", agent_effort="xhigh"),
                         profile="heartbeat-resume", cwd=str(workspace), feedback_prompt=prompt,
                         feedback="native", workspace_instructions=None, task_objective=None)
    worker.backend = SimpleNamespace(start_feedback=lambda handle: None)
    worker.handle = "worker-handle"
    worker.log_dir = tmp_path
    remote = tmp_path / "remote-prompt.md"
    modes = []

    class Environment:
        async def upload_file(self, source, target):
            # The native copy keeps the host mode; record what the agent would see.
            modes.append(Path(source).stat().st_mode & 0o777)
            Path(target).write_text(Path(source).read_text())

        async def exec(self, command):
            result = subprocess.run(["/bin/bash", "-c", command], capture_output=True, text=True)
            return SimpleNamespace(return_code=result.returncode, stdout=result.stdout)

    async def prepare(environment, text, **kwargs):
        return None

    worker.environment = Environment()
    worker.runtime = SimpleNamespace(
        goal_id="fixture-goal", execution=SimpleNamespace(uses_loopx=True),
        _prepare_phase=prepare, _write_task_document=prepare, _worker_env=lambda **kwargs: {},
    )
    monkeypatch.setattr("benchmark.runtime.sforge.worker_command", lambda *a, **kw: ["worker"])
    previous = os.umask(0o077)
    try:
        worker.format_run_cmd(str(remote), internet=False)
    finally:
        os.umask(previous)
    assert modes == [0o644]
    assert remote.read_text() == prompt


@pytest.mark.parametrize("source,covered", [
    ("/task", True), ("/home/workspace", True),
    ("/home/workspace/task/solver.py", False), ("/home/workspace/task-other", False),
])
def test_checkpoint_guidance_distinguishes_directory_from_file_coverage(source, covered):
    from benchmark.edgebench.prompts import checkpoint_research_instructions
    workspace = "/task" if source == "/task" else "/home/workspace/task"
    text = checkpoint_research_instructions(
        {"components": [{"id": "code", "source_path": source}]}, workspace)
    assert ("includes this task workspace" in text) is covered
    assert ("does not cover the whole task workspace" in text) is not covered
    assert "negative results" in text and "workspace-relative references" in text
    assert "external file does not preserve its bytes" in text
    assert "does not change submitted files" in text
    assert source not in text  # Do not echo arbitrary private capture roots.
