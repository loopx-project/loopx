"""The selected dependency's proof reaches the actual consumer and validated work."""
from __future__ import annotations

import copy
import json
import shlex
import sys
from pathlib import Path

import pytest

from loopx.capabilities.issue_fix import pr_lifecycle
from loopx.cli_commands import turn
from loopx.control_plane.quota.turn_envelope import build_turn_envelope
from loopx.control_plane.turn_driver.host_candidate import extract_turn_authority
from loopx.rollout_event_log import load_rollout_events, rollout_event_log_path
from loopx.todos import add_goal_todo, list_goal_todos
from tests.capabilities.test_pr_wait_observation import _invoke_turn, _observation
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
from tests.test_loopx_turn_driver import _write_live_fixture


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("observer_entry", ["native", "heartbeat"])
def test_selected_merge_proof_survives_capture_and_actual_host_adoption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, observer_entry: str,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_live_fixture(
        tmp_path, provider=provider, todo_metadata_extra="resume_when=pr_merged:other/repo#42",
    )
    config = json.loads(registry.read_text())
    config["goals"][0]["available_capabilities"] = ["external_evidence_poll"]
    registry.write_text(json.dumps(config))
    requirement = "Preserve the accepted requirement. " * 22 + "Require the terminal independent artifact marker."
    todo_id = add_goal_todo(
        registry_path=registry, goal_id="loopx-turn-fixture", role="agent", claimed_by="codex-fixture",
        text=requirement, priority="P0", action_kind="fixture", resume_when="pr_merged:owner/repo#42",
    )["todo_id"]
    before = list_goal_todos(registry_path=registry, goal_id="loopx-turn-fixture", todo_id=todo_id)["todo"]
    assert before["resume_ready"] is False
    calls = []

    def fetch(reference, **kwargs):
        calls.append(reference)
        return {"url": f"https://github.com/{reference['repo']}/pull/{reference['number']}", "state": "MERGED",
                "mergedAt": "2026-10-01T00:00:00Z" if reference["repo"] == "owner/repo"
                else "2026-10-02T00:00:00Z"}

    monkeypatch.setattr(pr_lifecycle, "fetch_github_pr_lifecycle_payload", fetch)
    prefix = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json"]
    if observer_entry == "heartbeat":
        prompts = []
        for mode in ("full", "thin"):
            code, prompt = _invoke_turn([
                *prefix, "heartbeat-prompt", "--goal-id", "loopx-turn-fixture", "--agent-id", "codex-fixture",
                "--codex-app", "--available-capability", "external_evidence_poll", f"--{mode}",
            ])
            assert code == 0, prompt
            prompts.append(prompt)
        command = prompts[0]["pr_review_pre_quota_command"]
        for number, prompt in enumerate(prompts):
            # Execute the command actually carried by each generated bootstrap.
            # Only output format is selected by the harness; its route is intact.
            generated = next(line for line in prompt["task_body"].splitlines() if line.strip() == command)
            code, observation = _invoke_turn(["--format", "json", *shlex.split(generated)[1:]])
            assert code == 0, observation
            assert observation["checks"]["pr_dependencies"]["external_read_count"] == (2 if number == 0 else 0)

    captured = []
    project_envelope = turn.build_turn_envelope

    def capture(decision, **kwargs):
        captured.append(copy.deepcopy(decision))
        return project_envelope(decision, **kwargs)

    monkeypatch.setattr(turn, "build_turn_envelope", capture)
    runner = tmp_path / "consumer.py"
    runner.write_text(
        "import json\nfrom pathlib import Path\n"
        "def run_dsh_turn(**kwargs):\n"
        "    root = Path(kwargs['workspace'])\n"
        "    prompt = kwargs['prompt']\n"
        "    (root / 'consumer-input.txt').write_text(prompt)\n"
        "    authority = json.JSONDecoder().raw_decode(prompt.split('Turn authority JSON:\\n', 1)[1])[0]\n"
        "    selected = authority['selected_todo']\n"
        f"    assert selected['todo_id'] == {todo_id!r}\n"
        "    assert selected['resume_when'] == 'pr_merged:owner/repo#42'\n"
        "    assert selected['resume_ready'] is True\n"
        "    condition = selected['resume_condition']\n"
        "    assert condition['satisfied'] is True\n"
        "    assert condition['matched_pr_ref'] == 'owner/repo#42'\n"
        "    assert condition['matched_event_at'] == '2026-10-01T00:00:00Z'\n"
        "    task = next(s['content']['todo'] for s in authority['work_context']['sources']\n"
        "                if s.get('source') == 'selected_todo')\n"
        f"    assert task['text'] == {before['text']!r}\n"
        "    counter = root / 'consumer-count'\n"
        "    counter.write_text(str(int(counter.read_text()) + 1 if counter.exists() else 1))\n"
        "    (root / 'validated-artifact').write_text('terminal independent artifact marker')\n"
        "    return json.dumps({'result_kind':'validated_progress','classification':'dependency_followthrough',"
        "'work_evidence':['validated-artifact'],"
        "'validation_evidence':['Independent command verifies the terminal artifact marker'],"
        "'summary':'Validated the original dependency-bound fixture work.','delivery_batch_scale':'implementation',"
        "'delivery_outcome':'outcome_progress','delivery_boundary':'in_flight_continuation',"
        "'recommended_action':'Continue the original accepted work.'})\n"
    )
    argv = [*prefix, "turn", "run-once", "--goal-id", "loopx-turn-fixture", "--agent-id", "codex-fixture",
            "--host", "dsh", "--dsh-runner", str(runner), "--project", str(project),
            "--todo-id", todo_id, "--turn-instance-id", "consumer-adoption", "--available-capability", "external_evidence_poll",
            "--validation-command-json", json.dumps([sys.executable, "-c", "from pathlib import Path; "
                "assert Path('validated-artifact').read_text() == 'terminal independent artifact marker'"]),
            "--no-global-sync", "--execute"]
    code, result = _invoke_turn(argv)
    assert code == 0 and result["status"] == "committed", json.dumps(result, indent=2)
    assert _observation(result)["external_read_count"] == (2 if observer_entry == "native" else 0)
    assert len(captured) == 1
    full = captured[0]
    envelope = build_turn_envelope(full)
    expected = list_goal_todos(registry_path=registry, goal_id="loopx-turn-fixture", todo_id=todo_id)["todo"]
    assert expected["resume_ready"] is True
    for field in ("resume_when", "resume_ready", "resume_condition"):
        assert full["selected_todo"][field] == expected[field]
        assert envelope["action"]["selected_todo"][field] == expected[field]
    assert expected["resume_condition"]["matched_pr_ref"] == "owner/repo#42"
    assert expected["resume_condition"]["matched_event_at"] == "2026-10-01T00:00:00Z"
    assert envelope["action_signature"]["matches"] is True
    prompt = (project / "consumer-input.txt").read_text()
    consumed = json.JSONDecoder().raw_decode(prompt.split("Turn authority JSON:\n", 1)[1])[0]
    assert consumed["selected_todo"]["resume_condition"] == expected["resume_condition"]
    assert "Require the terminal independent artifact marker." in prompt

    # A matching signature must cover readiness and the exact event provenance.
    for field, value in (("resume_ready", False), ("resume_condition", {"satisfied": True, "matched_pr_ref": "other/repo#42"})):
        changed = copy.deepcopy(envelope)
        changed["action"]["selected_todo"][field] = value
        with pytest.raises(ValueError, match="action signature"):
            extract_turn_authority({"turn_envelope": changed})

    for field in ("text", "status", "claimed_by", "resume_when"):
        assert expected[field] == before[field]
    # A new pending dependency makes accidental polling by either read/replay visible.
    pending = add_goal_todo(
        registry_path=registry, goal_id="loopx-turn-fixture", role="agent", claimed_by="codex-fixture",
        text="Continue a separately observed dependency", resume_when="pr_merged:owner/repo#43",
    )["todo_id"]
    events = load_rollout_events(rollout_event_log_path(runtime, "loopx-turn-fixture"))
    assert len(calls) == 2 and {call["repo"] for call in calls} == {"owner/repo", "other/repo"}
    assert sum(event["event_kind"] == "quota_spend" for event in events) == 1
    assert sum(event["event_kind"] == "pr_merge" for event in events) == 2
    assert not any(event["event_kind"] == "todo_complete" for event in events)

    # Managed-step remains a read-only controller even with polling declared.
    revision = list_goal_todos(registry_path=registry, goal_id="loopx-turn-fixture")["authority_read"]["provider_revision"]
    code, managed = _invoke_turn([*prefix, "turn", "managed-step", "--goal-id", "loopx-turn-fixture",
        "--agent-id", "codex-fixture", "--host", "dsh", "--turn-key", result["resume_turn_key"],
         "--available-capability", "external_evidence_poll"])
    assert code == 1 and managed["error"] == "managed step requires a failed Turn journal", managed
    assert list_goal_todos(registry_path=registry, goal_id="loopx-turn-fixture")["authority_read"]["provider_revision"] == revision
    replay_argv = list(argv)
    for fresh_option in ("--todo-id", "--turn-instance-id"):
        selection_index = replay_argv.index(fresh_option)
        del replay_argv[selection_index:selection_index + 2]
    code, replay = _invoke_turn([*replay_argv, "--resume-turn-key", result["resume_turn_key"]])
    assert code == 0 and replay["status"] == "committed", replay
    assert len(calls) == 2 and (project / "consumer-count").read_text() == "1"
    assert list_goal_todos(registry_path=registry, goal_id="loopx-turn-fixture", todo_id=pending)["todo"]["resume_ready"] is False
    assert load_rollout_events(rollout_event_log_path(runtime, "loopx-turn-fixture")) == events
