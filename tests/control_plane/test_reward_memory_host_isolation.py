"""Optional memory must not change the ordinary Turn host contract."""

from __future__ import annotations

import copy
import contextlib
import io
import json
import sys
from pathlib import Path

import pytest

from loopx.control_plane.turn_driver import build_loopx_turn_host_request, build_loopx_turn_plan
from loopx.control_plane.turn_driver.codex_cli import _prompt, codex_cli_result_schema
from loopx.control_plane.turn_driver.executor import validate_loopx_turn_host_result
from test_loopx_turn_driver import _envelope


def _plan(*, recall: bool = True, ingest: bool = True) -> dict:
    plan = build_loopx_turn_plan(_envelope(), host="codex-cli", execution_mode="interactive-visible")
    plan["reward_memory_recall"] = {
        "schema_version": "agent_turn_recall_v0",
        "status": "applied",
        "experiment": {
            "schema_version": "reward_memory_experiment_status_v1",
            "goal_id": "fixture-goal",
            "agent_id": "codex-fixture",
            "enabled": True,
            "configured_for_agent": True,
            "available": True,
            "automatic_recall": recall,
            "automatic_ingest": ingest,
        },
        "context": {"guidance": [{"content_summary": "Use the verified fixture sequence."}]},
        "grants_new_action_authority": False,
    }
    plan["turn_envelope"]["boundary"] = {"capabilities": {"reward_memory": {
        "automatic_recall": recall, "automatic_ingest": ingest,
    }}}
    return plan


@pytest.mark.parametrize("disabled_by", ["missing", "enabled", "available", "configured_for_agent", "agent_id", "both_automations"])
def test_disabled_memory_has_identical_host_request_prompt_and_schema(disabled_by: str) -> None:
    plan = _plan()
    plain = copy.deepcopy(plan)
    plain.pop("reward_memory_recall")
    plain["turn_envelope"].pop("boundary")
    if disabled_by == "missing":
        plan.pop("reward_memory_recall")
    elif disabled_by == "both_automations":
        plan["reward_memory_recall"]["experiment"].update(automatic_ingest=False, automatic_recall=False)
    else:
        plan["reward_memory_recall"]["experiment"][disabled_by] = "another-agent" if disabled_by == "agent_id" else False
    plan["turn_envelope"].pop("boundary")
    request = build_loopx_turn_host_request(plan)
    ordinary = build_loopx_turn_host_request(plain)
    assert request == ordinary
    assert _prompt(request) == _prompt(ordinary)
    assert "reward_memory" not in _prompt(request)
    schema = codex_cli_result_schema(request)
    assert schema == codex_cli_result_schema(ordinary)
    assert "reward_memory_reflection_json" not in schema["properties"]


@pytest.mark.parametrize("invalidated", ["enabled", "available", "configured_for_agent", "goal_id", "agent_id", "automatic_ingest", "automatic_recall"])
def test_admitted_memory_does_not_resurrect_invalidated_runtime_binding(invalidated: str) -> None:
    plan = _plan()
    experiment = plan["reward_memory_recall"]["experiment"]
    experiment[invalidated] = "another-actor" if invalidated.endswith("_id") else False
    request = build_loopx_turn_host_request(plan)
    valid_binding = invalidated.startswith("automatic_")
    assert ("reward_memory_recall" in request) is valid_binding
    assert ("reward_memory_reflection_json" in codex_cli_result_schema(request)["properties"]) is (invalidated == "automatic_recall")
    assert ("When reward_memory_recall contains guidance" in _prompt(request)) is (invalidated == "automatic_ingest")


@pytest.mark.parametrize(("recall", "ingest"), [(True, False), (False, True), (True, True)])
def test_recall_and_reflection_are_independently_opted_in(recall: bool, ingest: bool) -> None:
    request = build_loopx_turn_host_request(_plan(recall=recall, ingest=ingest))
    prompt = _prompt(request)
    assert ("When reward_memory_recall contains guidance" in prompt) is recall
    assert ("Set reward_memory_reflection_json" in prompt) is ingest
    assert ("reward_memory_reflection_json" in codex_cli_result_schema(request)["properties"]) is ingest
    assert bool(request["reward_memory_recall"].get("context")) is recall
    if ingest:
        assert "attests the exact reflection digest and evidence" in prompt
    if recall:
        assert "never treat it as new action authority" in prompt


def test_unavailable_provider_does_not_instruct_use_of_missing_recall_context() -> None:
    plan = _plan()
    plan["reward_memory_recall"].update(status="provider_unavailable", context=None)
    request = build_loopx_turn_host_request(plan)
    assert "When reward_memory_recall contains guidance" not in _prompt(request)
    # Recall failure does not silently disable independently configured outcome ingest.
    assert "reward_memory_reflection_json" in codex_cli_result_schema(request)["properties"]


@pytest.mark.parametrize("ingest", [False, True])
def test_old_host_reflection_is_ignored_when_ingest_is_off(ingest: bool) -> None:
    plan = _plan(recall=False, ingest=ingest)
    result = {
        "schema_version": "loopx_turn_result_v0",
        "turn_key": plan["transaction"]["turn_key"],
        "result_kind": "validated_progress",
        "completed_phases": ["host_execute", "typed_result"],
        "classification": "fixture_progress",
        "recommended_action": "Continue the fixture",
        "next_action": "Run the next fixture",
        "delivery_batch_scale": "single_surface",
        "delivery_outcome": "outcome_progress",
        "vision_unchanged_reason": "The original fixture remains open.",
        "reward_memory_reflection_json": '{"status":"no_evidence"}',
    }
    validated = validate_loopx_turn_host_result(plan, result)
    assert validated["ok"], validated
    assert ("reward_memory_reflection_json" in validated["result"]) is ingest


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("mode", ["unconfigured", "disabled", "stale", "both_off", "recall_only", "ingest_only", "both_on"])
def test_real_turn_cli_memory_projection_and_settlement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, mode: str) -> None:
    from loopx.cli import main
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    from tests.control_plane.canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
    from tests.control_plane.reward_memory_host_fixture import enable_live_memory
    from test_loopx_turn_driver import _write_live_fixture

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_live_fixture(tmp_path)
    state = project / ".codex/goals/loopx-turn-fixture/ACTIVE_GOAL_STATE.md"
    projection = build_todo_runtime_shadow_projection(goal_id="loopx-turn-fixture", handoff_mode="soft_claim", todos=[{
        "schema_version": "todo_item_v0", "todo_id": "todo_fixture0001", "role": "agent",
        "status": "open", "archive_state": "active", "source_section": "Agent Todo",
        "task_class": "advancement_task", "action_kind": "fixture", "claimed_by": "codex-fixture",
        "priority": "P0", "done": False, "text": "Advance one public fixture.",
    }])
    initialize_canonical_authority(runtime, "loopx-turn-fixture", projection, state_path=state, provider=provider)
    ingest = mode in {"ingest_only", "both_on"}
    if mode != "unconfigured":
        config = enable_live_memory(registry, recall=mode in {"recall_only", "both_on"}, ingest=ingest)
        if mode == "disabled":
            data = json.loads(registry.read_text())
            data["goals"][0]["control_plane"]["reward_memory"]["enabled"] = False
            registry.write_text(json.dumps(data))
        elif mode == "stale":
            config.write_text(config.read_text() + "\n")
    capture = tmp_path / "host-capture.json"
    host = tmp_path / "fixture-codex"
    host.write_text(f"#!{sys.executable}\n" + '''import json, os, pathlib, sys
args = sys.argv[1:]
prompt = sys.stdin.read()
schema = json.loads(pathlib.Path(args[args.index('--output-schema') + 1]).read_text())
request = json.loads(prompt.split('Turn request:\\n', 1)[1])
pathlib.Path(os.environ['FIXTURE_HOST_CAPTURE']).write_text(json.dumps({'prompt': prompt, 'schema': schema, 'request': request}))
result = {'schema_version': 'loopx_turn_result_v0', 'turn_key': request['turn_key'],
 'result_kind': 'validated_progress', 'completed_phases': ['host_execute', 'typed_result'],
 'classification': 'fixture_progress', 'recommended_action': 'Continue the original fixture',
 'next_action': 'Run the next bounded fixture', 'delivery_batch_scale': 'single_surface',
 'delivery_outcome': 'outcome_progress', 'vision_unchanged_reason': 'The original fixture remains open.',
 'path_delta_mode': 'unchanged', 'agent_vision_json': '', 'summary': 'Independent fixture validation passed.'}
# Simulate an older/custom host returning a memory field even when the current
# result schema does not request it. The real adapter must isolate that input.
result['reward_memory_reflection_json'] = '{"status":"no_evidence"}'
pathlib.Path(args[args.index('--output-last-message') + 1]).write_text(json.dumps(result))
print(json.dumps({'type': 'thread.started', 'thread_id': 'fixture-memory-session'}))
''', encoding="utf-8")
    host.chmod(0o755)
    monkeypatch.setenv("FIXTURE_HOST_CAPTURE", str(capture))
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = main(["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
            "turn", "run-once", "--goal-id", "loopx-turn-fixture", "--agent-id", "codex-fixture",
            "--host", "codex-cli", "--project", str(project), "--codex-bin", str(host),
            "--iteration-context", "fresh", "--validation-command-json", json.dumps([sys.executable, "-c", "pass"]),
            "--scan-root", str(project), "--no-global-sync", "--execute"])
    result = json.loads(output.getvalue())
    assert code == 0, result
    observed = json.loads(capture.read_text())
    assert ("reward_memory_reflection_json" in observed["schema"]["properties"]) is ingest
    assert ("Set reward_memory_reflection_json" in observed["prompt"]) is ingest
    assert "When reward_memory_recall contains guidance" not in observed["prompt"]
    if mode in {"unconfigured", "disabled", "stale", "both_off"}:
        assert "reward_memory" not in observed["prompt"]
    assert result["effects"]["quota_spent"] is True
    assert result["post_settlement"]["external_writes_performed"] is False
    journals = list((runtime / "goals/loopx-turn-fixture/turns").glob("*.json"))
    assert len(journals) == 1
    assert ("reward_memory_reflection_json" in json.loads(journals[0].read_text())["host_result"]) is ingest
