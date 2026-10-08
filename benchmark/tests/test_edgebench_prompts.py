from pathlib import Path
from types import SimpleNamespace
import shlex

import pytest

from benchmark.edgebench.prompts import blind_task_prompt, best_only_task_prompt


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
@pytest.mark.parametrize("feedback", ["blind", "best-only"])
def test_each_worker_receives_and_records_restricted_prompt(tmp_path, monkeypatch, profile, feedback):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from sforge.harness.config import SForgeConfig
    from benchmark.runtime.sforge import SForgeWorker

    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", "/private-credential")
    render = best_only_task_prompt if feedback == "best-only" else blind_task_prompt
    prompt = render("Complete the supplied task.", ["solver.py"])
    worker = SForgeWorker(SForgeConfig(agent_model="fixture", agent_effort="xhigh"),
                         profile=profile, cwd="/task", feedback_prompt=prompt, feedback=feedback)
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
            assert shlex.split(command) == ["cat", str(remote)]
            return SimpleNamespace(return_code=0, stdout=remote.read_text())

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
    worker.format_run_cmd(str(remote), internet=False, resume=True)
    assert len(started) == (2 if feedback == "best-only" else 0)
    assert len(uploads) == 1  # Resume does not replace a live task context.


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
