"""Real child-process and typed-bridge contract tests; no paid model calls."""

from __future__ import annotations

import json
import copy
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest
from jsonschema import Draft202012Validator, ValidationError

from loopx.capabilities.benchmark_toolkit.trajectory_assessment import (
    execute,
    prepare,
    read_job,
    request_cancel,
)
from loopx.control_plane.effect_runtime import effect_runtime_result


@pytest.fixture
def prepared(tmp_path):
    path = tmp_path / "input.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "trajectory_assessment_input_v1",
                "scope": dict(
                    goal_id="goal",
                    case_id="case",
                    run_id="run",
                    revision="r1",
                    window="one episode · 观察窗口",
                ),
                "objective": "Improve native utility · 原始目标",
                "selection": dict(
                    rule="synthetic lifecycle fixture",
                    population_episodes=1,
                    exhaustive=True,
                ),
                "sources": [
                    dict(
                        id="source",
                        artifact="result.txt",
                        artifact_sha256="a" * 64,
                        first_line=1,
                        text="No utility measurement is available · 未测量。",
                    )
                ],
                "episodes": [
                    dict(
                        id="episode",
                        question="Does it help?",
                        source_ids=["source"],
                        eligible_metrics=["benefit_test"],
                    )
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return prepare(path, model="fixture", effort="max", service_tier="fast")


@pytest.mark.parametrize("language", [None, "zh", "en"])
@pytest.mark.parametrize("synthesis", [False, True])
def test_generation_schema_enforces_episode_evidence_before_provider_output(prepared, language, synthesis):
    evidence = copy.deepcopy(prepared["input"])
    evidence["sources"].append({**evidence["sources"][0], "id": "sibling"})
    evidence["sources"].append({**evidence["sources"][0], "id": "display-only"})
    evidence["episodes"].append(dict(id="second", question="A separate intervention?",
                                      source_ids=["sibling"], eligible_metrics=["information_gain"]))
    evidence["selection"].update(population_episodes=2)
    config = dict(model="fixture", effort="max", service_tier="fast", research_synthesis=synthesis)
    if language:
        config["output_language"] = language
    legacy = effect_runtime_result("benchmark.trajectory.prepare", dict(input=evidence, config=config))
    scoped = effect_runtime_result("benchmark.trajectory.prepare", dict(
        input=evidence, config={**config, "output_schema_revision": 2}))
    Draft202012Validator.check_schema(scoped["output_schema"])
    schema = Draft202012Validator(scoped["output_schema"])
    original_schema = Draft202012Validator(legacy["output_schema"])
    result = dict(assessments=[dict(episode_id="episode", metric="benefit_test", verdict="yes",
        reason="Observed utility test", citations=[dict(source_id="source", first_line=1, last_line=1)])], limitations=[])
    if language:
        result["episode_questions"] = []
    if synthesis:
        result["synthesis"] = dict(summary=dict(text="Declared sample only", episode_ids=["episode"],
            citations=[dict(source_id="source", first_line=1, last_line=1)]), findings=[], next_experiments=[])
    schema.validate(result)
    for foreign in ["sibling", "display-only", "invented"]:
        invalid = copy.deepcopy(result)
        invalid["assessments"][0]["citations"][0]["source_id"] = foreign
        original_schema.validate(invalid)
        with pytest.raises(ValidationError):
            schema.validate(invalid)
    for key, value in [("episode_id", "second"), ("episode_id", "invented"), ("metric", "information_gain")]:
        invalid = copy.deepcopy(result)
        invalid["assessments"][0][key] = value
        with pytest.raises(ValidationError):
            schema.validate(invalid)
    result["assessments"][0].update(verdict="unknown", citations=[])
    schema.validate(result)
    assert scoped["analysis_key"] != legacy["analysis_key"]
    assert scoped["prompt"] == legacy["prompt"]
    assert effect_runtime_result("benchmark.trajectory.prepare", dict(input=evidence, config=config)) == legacy


def provider(
    tmp_path: Path, *, delay=0, code=0, invalid=False, invalid_utf8=False,
    expected_home=None,
) -> str:
    script = tmp_path / "codex-fixture"
    output = {
        "assessments": [
            dict(
                episode_id="episode",
                metric="benefit_test",
                verdict="unknown",
                reason="Measurement is unavailable · 未测量",
                citations=[],
            )
        ],
        "limitations": ["Synthetic provider; no model qualification"],
        "synthesis": {"summary": {"text": "The measurement is unavailable; utility remains unknown.",
                                   "episode_ids": ["episode"], "citations": [dict(source_id="source", first_line=1, last_line=1)]},
                      "findings": [], "next_experiments": []},
    }
    if invalid:
        output["assessments"] = []
    payload = (
        b"\xff"
        if invalid_utf8
        else json.dumps(output, ensure_ascii=False).encode("utf-8")
    )
    script.write_text(
        f"""#!{sys.executable}
import json,os,sys,time
from pathlib import Path
assert "\\u539f\\u59cb\\u76ee\\u6807" in sys.stdin.buffer.read().decode("utf-8")
options = dict(arg.split('=', 1) for index, arg in enumerate(sys.argv) if index > 0 and sys.argv[index-1] == '-c')
selected = json.loads(options['model_provider'])
assert json.loads(options['model_providers.' + selected + '.requires_openai_auth']) is True
assert json.loads(options['model_providers.' + selected + '.supports_websockets']) is False
assert not any(key.endswith(('.base_url', '.env_key', '.experimental_bearer_token')) for key in options)
assert '--ignore-user-config' in sys.argv and '--ignore-rules' in sys.argv
assert sys.argv[sys.argv.index('--sandbox')+1] == 'read-only'
assert json.loads(options['web_search']) == 'disabled'
assert 'shell_tool' in sys.argv and 'unified_exec' in sys.argv
if {expected_home!r} is not None:
    assert os.environ.get('CODEX_HOME') == {expected_home!r}
time.sleep({delay})
if {code}: sys.exit({code})
Path(sys.argv[sys.argv.index('-o')+1]).write_bytes({payload!r})
sys.stderr.buffer.write("\\u5408\\u6210\\u8bca\\u65ad".encode("utf-8"))
sys.stdout.buffer.write((json.dumps({{'type':'turn.completed','message':'\\u5206\\u6790\\u5b8c\\u6210','usage':{{'input_tokens':12,'output_tokens':3}}}},ensure_ascii=False)+'\\n').encode("utf-8"))
""",
        encoding="utf-8",
    )
    script.chmod(0o700)
    return str(script)


@pytest.mark.parametrize("chat_home", [None, "", "selected-chat-home"])
def test_actual_child_uses_explicit_chat_account_not_unrelated_terminal(
    prepared, tmp_path, monkeypatch, chat_home
):
    monkeypatch.setenv("CODEX_HOME", "unrelated-terminal-home")
    if chat_home is None:
        monkeypatch.delenv("LOOPX_CHAT_CODEX_HOME", raising=False)
    else:
        monkeypatch.setenv("LOOPX_CHAT_CODEX_HOME", chat_home)
    expected = "selected-chat-home" if chat_home else "unrelated-terminal-home"
    result = execute(
        prepared, tmp_path / "jobs",
        codex=provider(tmp_path, expected_home=expected),
    )
    assert result["status"] == "succeeded"
    # Selection belongs to this child, not the server's process or saved input.
    assert os.environ["CODEX_HOME"] == "unrelated-terminal-home"
    assert "CODEX_HOME" not in result["config"]


def test_actual_child_reuse_and_repair_of_derived_output(prepared, tmp_path):
    first = execute(prepared, tmp_path / "jobs", codex=provider(tmp_path))
    assert first["status"] == "succeeded" and first["usage"] == {
        "input_tokens": 12,
        "output_tokens": 3,
    }
    assert first["served_service_tier"] is None
    directory = Path(first["directory"])
    (directory / "assessment.json").write_text("{}", encoding="utf-8")
    second = execute(prepared, tmp_path / "jobs", codex="must-not-run")
    assert second["reused"] is True
    assert (
        json.loads((directory / "assessment.json").read_text(encoding="utf-8"))[
            "metrics"
        ][1]["unknown"]
        == 1
    )
    assert second["analysis_wall_seconds"] == first["analysis_wall_seconds"]


def test_failed_model_output_needs_explicit_retry_and_preserves_failure(
    prepared, tmp_path
):
    with pytest.raises(Exception, match="missing eligible"):
        execute(prepared, tmp_path / "jobs", codex=provider(tmp_path, invalid=True))
    directory = tmp_path / "jobs" / prepared["analysis_key"]
    failure = read_job(directory)
    assert failure["status"] == "failed"
    assert failure["usage"] == {"input_tokens": 12, "output_tokens": 3}
    assert failure["analysis_wall_seconds"] is not None
    assert not (directory / "assessment.json").exists()
    assert execute(prepared, tmp_path / "jobs", codex="must-not-run")["ok"] is False
    success = execute(prepared, tmp_path / "jobs", retry=True, codex=provider(tmp_path))
    assert success["status"] == "succeeded" and success["attempt"] == 2
    assert (
        json.loads(
            (directory / "attempt-1" / "receipt.json").read_text(encoding="utf-8")
        )["status"]
        == "failed"
    )
    assert read_job(directory / "attempt-1")["usage"] == failure["usage"]


@pytest.mark.parametrize("damage", ["invalid_json", "invalid_result", "missing_output"])
def test_rejected_reuse_preserves_the_completed_attempt(prepared, tmp_path, damage):
    first = execute(prepared, tmp_path / "jobs", codex=provider(tmp_path))
    directory = Path(first["directory"])
    receipt = (directory / "receipt.json").read_bytes()
    result = (directory / "assessment.json").read_bytes()
    output = (directory / "model-output.json").read_bytes()
    if damage == "missing_output":
        (directory / "model-output.json").unlink()
    else:
        (directory / "model-output.json").write_text(
            "{" if damage == "invalid_json" else "{}", encoding="utf-8"
        )
    with pytest.raises(Exception):
        execute(prepared, tmp_path / "jobs", codex="must-not-run")
    # A failed integrity check is not a new provider attempt. Preserve the
    # completed attempt's status, observed cost, clock and result proof.
    assert (directory / "receipt.json").read_bytes() == receipt
    assert (directory / "assessment.json").read_bytes() == result
    assert not (directory / "execution.lock").exists()
    (directory / "model-output.json").write_bytes(output)
    recovered = execute(prepared, tmp_path / "jobs", codex="must-not-run")
    assert recovered["status"] == "succeeded" and recovered["reused"] is True
    assert recovered["attempt"] == first["attempt"]
    assert recovered["analysis_wall_seconds"] == first["analysis_wall_seconds"]
    assert recovered["usage"] == first["usage"]


def test_retry_preparation_failure_does_not_rewrite_the_previous_failure(prepared, tmp_path):
    with pytest.raises(Exception, match="missing eligible"):
        execute(prepared, tmp_path / "jobs", codex=provider(tmp_path, invalid=True))
    directory = tmp_path / "jobs" / prepared["analysis_key"]
    receipt = (directory / "receipt.json").read_bytes()
    # A conflicting archive must be diagnosed before a new attempt exists.
    archive = directory / "attempt-1"
    archive.mkdir()
    with pytest.raises(FileExistsError):
        execute(prepared, tmp_path / "jobs", retry=True, codex="must-not-run")
    assert (directory / "receipt.json").read_bytes() == receipt
    assert not (directory / "execution.lock").exists()
    archive.rmdir()
    result = execute(prepared, tmp_path / "jobs", retry=True, codex=provider(tmp_path))
    assert result["status"] == "succeeded" and result["attempt"] == 2
    assert (archive / "receipt.json").read_bytes() == receipt


def test_failure_after_starting_a_new_attempt_is_still_recorded(prepared, tmp_path):
    with pytest.raises(FileNotFoundError):
        execute(prepared, tmp_path / "jobs", codex=str(tmp_path / "missing-provider"))
    directory = tmp_path / "jobs" / prepared["analysis_key"]
    receipt = read_job(directory)
    assert receipt["status"] == "failed" and receipt["attempt"] == 1
    assert receipt["usage"] is None and receipt["analysis_wall_seconds"] is not None
    assert not (directory / "execution.lock").exists()


def test_cli_retry_preparation_preserves_status_and_attempt_sequence(prepared, tmp_path, monkeypatch):
    with pytest.raises(Exception, match="missing eligible"):
        execute(prepared, tmp_path / "jobs", codex=provider(tmp_path, invalid=True))
    directory = tmp_path / "jobs" / prepared["analysis_key"]
    original = {name: path.read_bytes() for name in ("receipt.json", "events.jsonl", "model-output.json")
                if (path := directory / name).exists()}
    binary = tmp_path / "bin"
    binary.mkdir()
    (binary / "codex").symlink_to(provider(tmp_path))
    monkeypatch.setenv("PATH", str(binary) + os.pathsep + os.environ["PATH"])
    registry = tmp_path / "registry.json"
    registry.write_text('{"projects":{}}')
    prefix = [sys.executable, "-m", "loopx.entrypoint", "--registry", str(registry),
              "--runtime-root", str(tmp_path / "runtime"), "--format", "json", "benchmark"]
    command = [*prefix, "trajectory-assess", "--input-json", str(tmp_path / "input.json"),
               "--output-dir", str(tmp_path / "jobs"), "--model", "fixture", "--execute"]
    obstruction = directory / "input.tmp"
    obstruction.mkdir()
    rejected = subprocess.run([*command, "--retry"], capture_output=True, text=True)
    assert rejected.returncode != 0
    status = subprocess.run([*prefix, "trajectory-assessment-status", "--directory", str(directory)],
                            capture_output=True, text=True)
    assert json.loads(status.stdout) == json.loads(original["receipt.json"])
    assert all((directory / name).read_bytes() == raw for name, raw in original.items())
    obstruction.rmdir()
    ordinary = subprocess.run(command, capture_output=True, text=True)
    assert json.loads(ordinary.stdout)["attempt"] == 1
    assert "Explicit retry" in json.loads(ordinary.stdout)["reason"]
    assert (directory / "receipt.json").read_bytes() == original["receipt.json"]
    retry = subprocess.run([*command, "--retry"], capture_output=True, text=True, check=True)
    assert json.loads(retry.stdout)["attempt"] == 2
    assert json.loads(retry.stdout)["status"] == "succeeded"
    assert all((directory / "attempt-1" / name).read_bytes() == raw for name, raw in original.items())


def test_cli_rejected_reuse_and_source_recovery_preserve_original_receipt(prepared, tmp_path, monkeypatch):
    first = execute(prepared, tmp_path / "jobs", codex=provider(tmp_path))
    directory = Path(first["directory"])
    receipt = (directory / "receipt.json").read_bytes()
    output = (directory / "model-output.json").read_bytes()
    (directory / "model-output.json").write_text("{}")
    # Even an unexpected dispatch in this negative case stays synthetic.
    binary = tmp_path / "bin"
    binary.mkdir()
    called = tmp_path / "unexpected-dispatch"
    child = binary / "codex"
    child.write_text(f"#!{sys.executable}\nfrom pathlib import Path\nPath({str(called)!r}).touch()\nraise SystemExit(99)\n")
    child.chmod(0o700)
    monkeypatch.setenv("PATH", str(binary) + os.pathsep + os.environ["PATH"])
    registry = tmp_path / "registry.json"
    registry.write_text('{"projects":{}}')
    command = [sys.executable, "-m", "loopx.entrypoint", "--registry", str(registry),
               "--runtime-root", str(tmp_path / "runtime"), "--format", "json",
               "benchmark", "trajectory-assess", "--input-json", str(tmp_path / "input.json"),
               "--output-dir", str(tmp_path / "jobs"), "--model", "fixture", "--execute"]
    rejected = subprocess.run(command, capture_output=True, text=True)
    assert rejected.returncode != 0
    assert (directory / "receipt.json").read_bytes() == receipt
    assert not called.exists() and not (directory / "execution.lock").exists()
    (directory / "model-output.json").write_bytes(output)
    recovered = subprocess.run(command, capture_output=True, text=True, check=True)
    value = json.loads(recovered.stdout)
    assert value["status"] == "succeeded" and value["reused"] is True
    assert value["analysis_wall_seconds"] == first["analysis_wall_seconds"]
    assert value["usage"] == first["usage"] and not called.exists()


def test_invalid_utf8_output_fails_strictly_and_retry_preserves_bytes(
    prepared, tmp_path
):
    with pytest.raises(UnicodeDecodeError):
        execute(
            prepared, tmp_path / "jobs", codex=provider(tmp_path, invalid_utf8=True)
        )
    directory = tmp_path / "jobs" / prepared["analysis_key"]
    assert read_job(directory)["status"] == "failed"
    assert not (directory / "execution.lock").exists()
    assert not (directory / "assessment.json").exists()
    assert execute(prepared, tmp_path / "jobs", codex="must-not-run")["ok"] is False
    assert (
        execute(prepared, tmp_path / "jobs", retry=True, codex=provider(tmp_path))[
            "status"
        ]
        == "succeeded"
    )
    assert (directory / "attempt-1" / "model-output.json").read_bytes() == b"\xff"
    result = json.loads((directory / "assessment.json").read_text(encoding="utf-8"))
    assert result["assessments"][0]["reason"] == "Measurement is unavailable · 未测量"


def test_concurrent_execution_and_cancel_do_not_signal_stored_pid(prepared, tmp_path):
    results = []
    worker = threading.Thread(
        target=lambda: results.append(
            execute(prepared, tmp_path / "jobs", codex=provider(tmp_path, delay=30))
        )
    )
    worker.start()
    directory = tmp_path / "jobs" / prepared["analysis_key"]
    deadline = time.monotonic() + 15
    while not (directory / "receipt.json").exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    try:
        assert (
            execute(prepared, tmp_path / "jobs", codex="must-not-run")["status"]
            == "busy"
        )
        assert request_cancel(directory)["cancel_requested"] is True
    finally:
        (directory / "cancel.request").touch()
        worker.join(15)
    assert not worker.is_alive()
    assert results[0]["status"] == "cancelled"
    assert not (directory / "assessment.json").exists()


def test_preview_cli_is_read_only_and_renders_json(prepared, tmp_path):
    import subprocess

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.entrypoint",
            "--format",
            "json",
            "benchmark",
            "trajectory-assess",
            "--input-json",
            str(tmp_path / "input.json"),
            "--output-dir",
            str(tmp_path / "never-created"),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "preview"
    assert not (tmp_path / "never-created").exists()


def test_execute_rejects_forged_key_before_creating_files(prepared, tmp_path):
    prepared["analysis_key"] = "../escape"
    with pytest.raises(ValueError, match="key mismatch"):
        execute(prepared, tmp_path / "must-not-exist", codex="must-not-run")
    assert not (tmp_path / "must-not-exist").exists()
