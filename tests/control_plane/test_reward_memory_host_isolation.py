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
from loopx.control_plane.quota.turn_envelope import turn_envelope_action_signature_document
from loopx.control_plane.turn_driver.host_candidate import (
    _canonical_hash, build_result, extract_turn_authority, render_prompt,
)
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
    from tests.control_plane.reward_memory_host_fixture import register_plan_memory
    register_plan_memory(plan)
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
    valid_binding = False  # Changed registered sources invalidate the entire optional contribution.
    assert ("reward_memory_recall" in request) is valid_binding
    assert ("reward_memory_reflection_json" in codex_cli_result_schema(request)["properties"]) is False
    assert ("When reward_memory_recall contains guidance" in _prompt(request)) is False


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
    from tests.control_plane.reward_memory_host_fixture import register_plan_memory
    register_plan_memory(plan)
    request = build_loopx_turn_host_request(plan)
    assert "When reward_memory_recall contains guidance" not in _prompt(request)
    # Recall failure does not silently disable independently configured outcome ingest.
    assert "reward_memory_reflection_json" in codex_cli_result_schema(request)["properties"]


@pytest.mark.parametrize("ingest", [False, True])
@pytest.mark.parametrize("from_journal", [False, True])
def test_old_host_reflection_is_ignored_when_ingest_is_off(ingest: bool, from_journal: bool) -> None:
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
    validated = validate_loopx_turn_host_result(plan, result, from_journal=from_journal)
    assert validated["ok"], validated
    assert ("reward_memory_reflection_json" in validated["result"]) is ingest


def test_pre_registration_journal_preserves_validated_reflection_on_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from loopx.control_plane.turn_driver import executor
    from loopx.control_plane.turn_driver.journal_store import (
        load_loopx_turn_plan_from_journal, turn_journal_path,
    )
    from tests.capabilities.test_reward_memory_outcome_lifecycle import _reflection

    plan = _plan(recall=False)
    reflection = _reflection()
    attestation = {
        "schema_version": "reward_memory_reflection_validation_v0",
        "status": "validated",
        "reflection_digest": executor.reward_memory_reflection_digest(reflection),
        "evidence_refs": json.loads(reflection)["evidence_refs"],
    }
    result = {
        "schema_version": "loopx_turn_result_v0",
        "turn_key": plan["transaction"]["turn_key"],
        "result_kind": "validated_progress",
        "completed_phases": ["host_execute", "typed_result"],
        "classification": "fixture_progress", "recommended_action": "Continue",
        "next_action": "Continue", "delivery_batch_scale": "single_surface",
        "delivery_outcome": "outcome_progress",
        "vision_unchanged_reason": "The original objective remains open.",
        "reward_memory_reflection_json": reflection,
    }
    counts = dict(host=0, validation=0, writeback=0, spend=0, post=0)

    def host(_request):
        counts["host"] += 1
        return result

    def validate(_plan, candidate):
        counts["validation"] += 1
        assert candidate["reward_memory_reflection_json"] == reflection
        return {"status": "passed", "validator_kind": "fixture-independent-validator",
                "summary": "Exact reflection and evidence verified.",
                "reward_memory_reflection_validation": attestation}

    def writeback(candidate):
        counts["writeback"] += 1
        assert candidate["reward_memory_reflection_json"] == reflection
        return {"ok": True, "appended": True}

    def spend():
        counts["spend"] += 1
        return {"ok": True, "appended": True, "slots": 1}

    def post(_plan, candidate, evidence):
        counts["post"] += 1
        assert candidate["reward_memory_reflection_json"] == reflection
        assert evidence["task_validation"]["reward_memory_reflection_validation"] == attestation
        return {"ok": True, "status": "fixture_observed", "external_writes_performed": False}

    class InterruptedAfterValidation(BaseException):
        pass

    persist = executor._write_journal

    def interrupt(path, journal, **kwargs):
        # Persist the producer's pre-registration shape from its first write;
        # never rewrite the native journal owner's immutable saved plan.
        legacy = copy.deepcopy(journal)
        legacy["plan"].pop("turn_context_contributions", None)
        persist(path, legacy, **kwargs)
        if journal.get("completed_phases") == ["host_execute", "typed_result", "validation"]:
            raise InterruptedAfterValidation

    options = dict(host_runner=host, task_validator=validate, writeback=writeback,
                   spend=spend, scheduler=lambda _: {"completed": True},
                   post_settlement=post, project=tmp_path, runtime_root=tmp_path / "runtime",
                   goal_id="fixture-goal", timeout_seconds=10, execute=True)
    with monkeypatch.context() as patch:
        patch.setattr(executor, "_write_journal", interrupt)
        with pytest.raises(InterruptedAfterValidation):
            executor.run_loopx_turn_once(plan, **options)

    path = turn_journal_path(options["runtime_root"], goal_id="fixture-goal",
                             turn_key=result["turn_key"])
    journal = json.loads(path.read_text())
    # The pre-registration producer persisted this same result/attestation and
    # verified participation, but no generic contribution annotation. This is a
    # synthetic compatibility input, not a new activation or provider receipt.
    assert "turn_context_contributions" not in journal["plan"]
    original_plan = copy.deepcopy(journal["plan"])
    saved_plan = load_loopx_turn_plan_from_journal(
        options["runtime_root"], goal_id="fixture-goal", turn_key=result["turn_key"],
    )
    recovered = executor.run_loopx_turn_once(saved_plan, **options)
    assert recovered["status"] == "committed", recovered
    final = json.loads(path.read_text())
    assert final["host_result"]["reward_memory_reflection_json"] == reflection
    assert final["task_validation"]["reward_memory_reflection_validation"] == attestation
    assert final["plan"] == original_plan
    before_replay = path.read_bytes()
    replay = executor.run_loopx_turn_once(saved_plan, **options)
    assert replay["replayed"] is True
    assert path.read_bytes() == before_replay
    assert counts == dict(host=1, validation=1, writeback=1, spend=1, post=1)


@pytest.mark.parametrize(("reflection", "kind", "valid"), [
    (None, "validated_progress", True),
    ("x" * 2400, "validated_progress", True),
    ("x" * 2401, "validated_progress", False),
    ({"wrong": "type"}, "validated_progress", False),
    ("bounded reflection", "wait", False),
])
def test_legacy_cached_reflection_is_bounded_and_cannot_activate_a_host(reflection, kind, valid):
    plan = _plan(recall=False)
    plan.pop("turn_context_contributions")
    before = copy.deepcopy(plan)
    request = build_loopx_turn_host_request(plan)
    assert "reward_memory_recall" not in request
    assert "reward_memory_reflection_json" not in codex_cli_result_schema(request)["properties"]
    assert "Set reward_memory_reflection_json" not in _prompt(request)
    assert "When reward_memory_recall contains guidance" not in _prompt(request)
    result = {
        "schema_version": "loopx_turn_result_v0", "turn_key": plan["transaction"]["turn_key"],
        "result_kind": kind, "completed_phases": ["host_execute", "typed_result"],
        "classification": "fixture_progress", "recommended_action": "Continue",
        "next_action": "Continue", "delivery_batch_scale": "single_surface",
        "delivery_outcome": "outcome_progress",
        "vision_unchanged_reason": "The original objective remains open.",
        "reward_memory_reflection_json": reflection,
    }
    fresh = validate_loopx_turn_host_result(plan, result)
    assert fresh["ok"], fresh
    assert "reward_memory_reflection_json" not in fresh["result"]
    cached = validate_loopx_turn_host_result(plan, result, from_journal=True)
    assert cached["ok"] is valid, cached
    if valid:
        assert cached["result"].get("reward_memory_reflection_json") == reflection
    # An invalidated registered contribution must not fall back to legacy input.
    plan["turn_context_contributions"] = []
    current = validate_loopx_turn_host_result(plan, result, from_journal=True)
    assert current["ok"], current
    assert "reward_memory_reflection_json" not in current["result"]
    plan.pop("turn_context_contributions")
    assert plan == before


@pytest.mark.parametrize(("recall", "ingest"), [(False, False), (True, False), (False, True), (True, True)])
@pytest.mark.parametrize("invalidated", [None, "enabled", "available", "configured_for_agent", "goal_id", "agent_id"])
def test_shared_host_memory_keeps_binding_context_and_validation(recall, ingest, invalidated):
    plan = _plan(recall=recall, ingest=ingest)
    envelope = plan["turn_envelope"]
    envelope["action"]["primary_action"] = "Advance one public fixture"
    digest = _canonical_hash(turn_envelope_action_signature_document(envelope))
    envelope["action_signature"].update(source_hash=digest, envelope_hash=digest)
    from tests.control_plane.reward_memory_host_fixture import register_plan_memory
    register_plan_memory(plan)
    if invalidated:
        plan["reward_memory_recall"]["experiment"][invalidated] = "other" if invalidated.endswith("_id") else False
    request = build_loopx_turn_host_request(plan)
    before = copy.deepcopy(request)
    authority = extract_turn_authority(request)
    prompt = render_prompt(authority)
    active = invalidated is None
    assert ("When reward_memory_recall contains guidance" in prompt) is (active and recall)
    assert ("Set reward_memory_reflection_json" in prompt) is (active and ingest)
    if active and recall:
        assert authority["reward_memory_recall"]["context"] == plan["turn_context_contributions"][0]["context_fields"]["reward_memory_recall"]["context"]
    elif "reward_memory_recall" in authority:
        assert authority["reward_memory_recall"]["context"] is None
    if not active or not (recall or ingest):
        ordinary = {k: v for k, v in request.items() if k != "reward_memory_recall"}
        assert authority == extract_turn_authority(ordinary)
        assert prompt == render_prompt(extract_turn_authority(ordinary))
    candidate = {"result_kind": "validated_progress", "next_action": "Continue the fixture",
        "reward_memory_reflection_json": '{"status":"no_evidence"}'}
    result = build_result(request, candidate)
    assert ("reward_memory_reflection_json" in result) is (active and ingest)
    assert validate_loopx_turn_host_result(plan, result)["ok"]
    if active and ingest:
        # Preserve the input for the owning validators, without coercion or
        # truncation in this adapter. Oversize and stop-result rules still hold.
        candidate["reward_memory_reflection_json"] = {"wrong": "type"}
        assert build_result(request, candidate)["reward_memory_reflection_json"] == {"wrong": "type"}
        candidate["reward_memory_reflection_json"] = "x" * 2401
        assert not validate_loopx_turn_host_result(plan, build_result(request, candidate))["ok"]
        candidate.update(result_kind="wait", reward_memory_reflection_json='{"status":"no_evidence"}')
        assert not validate_loopx_turn_host_result(plan, build_result(request, candidate))["ok"]
    invalid = copy.deepcopy(request)
    invalid["turn_envelope"]["boundary"]["capabilities"]["reward_memory"]["automatic_ingest"] = not ingest
    with pytest.raises(ValueError, match="signature"):
        extract_turn_authority(invalid)
    assert request == before


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("host_kind", ["codex-cli", "dsh", "generic-cli"])
@pytest.mark.parametrize("mode", ["unconfigured", "disabled", "stale", "both_off", "recall_only", "recall_context", "oversized_recall", "ingest_only", "both_on"])
def test_real_turn_cli_memory_projection_and_settlement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, host_kind: str, mode: str) -> None:
    from loopx.cli import main
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
    from tests.control_plane.reward_memory_host_fixture import enable_live_memory
    from test_loopx_turn_driver import _write_live_fixture

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_live_fixture(tmp_path, provider=provider)
    ingest = mode in {"ingest_only", "both_on", "oversized_recall"}
    if mode != "unconfigured":
        config = enable_live_memory(registry, recall=mode in {"recall_only", "recall_context", "oversized_recall", "both_on"}, ingest=ingest)
        if mode == "disabled":
            data = json.loads(registry.read_text())
            data["goals"][0]["control_plane"]["reward_memory"]["enabled"] = False
            registry.write_text(json.dumps(data))
        elif mode == "stale":
            config.write_text(config.read_text() + "\n")
    if mode in {"recall_context", "oversized_recall"}:
        from loopx.capabilities.agent_turn_recall.runtime import run_configured_agent_turn_recall_fail_open
        from loopx.capabilities.reward_memory.experiment import resolve_reward_memory_experiment
        from tests.capabilities.test_agent_turn_recall import RecallProvider, active_record
        experiment_status, binding = resolve_reward_memory_experiment(
            registry_path=registry, goal_id="loopx-turn-fixture", agent_id="codex-fixture")
        if mode == "recall_context":
            provider_fixture = RecallProvider(active_record(binding, expires_at="2099-01-01T00:00:00+00:00"))
            # Only provider IO is hermetic; native config, binding, recall receipt,
            # admission, host, validator and canonical settlement still execute.
            monkeypatch.setattr("loopx.capabilities.agent_turn_recall.run_configured_agent_turn_recall_fail_open",
                lambda **kwargs: run_configured_agent_turn_recall_fail_open(**kwargs, provider=provider_fixture))
        else:
            monkeypatch.setattr(
                "loopx.capabilities.agent_turn_recall.run_configured_agent_turn_recall_fail_open",
                lambda **kwargs: {
                    "schema_version": "agent_turn_recall_v0",
                    "status": "applied",
                    "goal_id": "loopx-turn-fixture",
                    "agent_id": "codex-fixture",
                    "experiment": experiment_status,
                    "context": {"guidance": [
                        {"content_summary": "x" * 20_000} for _ in range(4)
                    ]},
                    "grants_new_action_authority": False,
                },
            )
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
    host_args = ["--host", "codex-cli", "--codex-bin", str(host)]
    if host_kind == "dsh":
        runner = tmp_path / "fixture_dsh_runner.py"
        runner.write_text('''import json, os, pathlib
def run_dsh_turn(*, prompt, **kwargs):
    authority = json.loads(prompt.split('Turn authority JSON:\\n', 1)[1].split('\\n\\nWhen finished', 1)[0])
    pathlib.Path(os.environ['FIXTURE_HOST_CAPTURE']).write_text(json.dumps({'prompt': prompt, 'authority': authority}))
    return json.dumps({'result_kind': 'validated_progress', 'classification': 'fixture_progress',
        'next_action': 'Run the next bounded fixture', 'path_delta_mode': 'unchanged',
        'vision_unchanged_reason': 'The original fixture remains open.',
        'work_evidence': ['artifact:fixture-host-capture'],
        'validation_evidence': ['check:fixture-independent-validator'],
        'reward_memory_reflection_json': '{"status":"no_evidence"}'})
''', encoding="utf-8")
        host_args = ["--host", "dsh", "--dsh-runner", str(runner), "--dsh-home", str(tmp_path / "dsh-home")]
    elif host_kind == "generic-cli":
        runner = tmp_path / "generic_host.py"
        runner.write_text('''import json, os, pathlib, sys
request = json.load(sys.stdin)
pathlib.Path(os.environ['FIXTURE_HOST_CAPTURE']).write_text(json.dumps({'request': request}))
json.dump({'schema_version': 'loopx_turn_result_v0', 'turn_key': request['turn_key'],
    'result_kind': 'validated_progress', 'completed_phases': ['host_execute', 'typed_result'],
    'classification': 'fixture_progress', 'next_action': 'Run the next bounded fixture',
    'recommended_action': 'Continue the original fixture', 'summary': 'Independent fixture validation passed.',
    'delivery_batch_scale': 'single_surface', 'delivery_outcome': 'outcome_progress',
    'vision_unchanged_reason': 'The original fixture remains open.',
    'reward_memory_reflection_json': '{"status":"no_evidence"}'}, sys.stdout)
''', encoding="utf-8")
        host_args = ["--host", host_kind, "--host-adapter-command-json", json.dumps([sys.executable, str(runner)])]
    monkeypatch.setenv("FIXTURE_HOST_CAPTURE", str(capture))
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = main(["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
            "turn", "run-once", "--goal-id", "loopx-turn-fixture", "--agent-id", "codex-fixture",
            *host_args, "--project", str(project),
            "--iteration-context", "fresh", "--validation-command-json", json.dumps([sys.executable, "-c", "pass"]),
            "--scan-root", str(project), "--no-global-sync", "--execute"])
    result = json.loads(output.getvalue())
    assert code == 0, result
    observed = json.loads(capture.read_text())
    if host_kind == "codex-cli":
        assert ("reward_memory_reflection_json" in observed["schema"]["properties"]) is ingest
    if host_kind != "generic-cli":
        assert ("Set reward_memory_reflection_json" in observed["prompt"]) is ingest
        assert ("When reward_memory_recall contains guidance" in observed["prompt"]) is (mode == "recall_context")
    if mode in {"recall_context", "oversized_recall"}:
        memory = observed["authority" if host_kind == "dsh" else "request"]["reward_memory_recall"]
        assert memory["status"] == "applied"
        if mode == "recall_context":
            assert "integration branch" in memory["context"]["guidance"][0]["content_summary"]
        else:
            assert memory["context"] is None
    if mode in {"unconfigured", "disabled", "stale", "both_off"}:
        if host_kind == "generic-cli":
            assert "reward_memory_recall" not in observed["request"]
        else:
            assert "reward_memory" not in observed["prompt"]
    assert result["effects"]["quota_spent"] is True
    assert result.get("post_settlement", {}).get("external_writes_performed", False) is False
    if ingest:
        assert "post_settlement" in result
    journals = list((runtime / "goals/loopx-turn-fixture/turns").glob("*.json"))
    assert len(journals) == 1
    assert ("reward_memory_reflection_json" in json.loads(journals[0].read_text())["host_result"]) is ingest

    # Read the real consumers after the native host and settlement. A connected
    # lifecycle is source wiring, not an activation or live-provider receipt.
    from loopx.capabilities.reward_memory.configuration import reward_memory_goal_configuration_summary
    from loopx.capabilities.reward_memory.experiment import resolve_reward_memory_experiment
    from loopx.presentation.renderers.reward_memory_markdown import append_agent_reward_memory_markdown

    experiment, _ = resolve_reward_memory_experiment(
        registry_path=registry, goal_id="loopx-turn-fixture", agent_id="codex-fixture")
    configuration = reward_memory_goal_configuration_summary(json.loads(registry.read_text())["goals"][0])
    assert configuration["effective_available"] is experiment["available"]
    assert experiment["automatic_ingest"] is ingest
    status_output = io.StringIO()
    with contextlib.redirect_stdout(status_output):
        status_code = main(["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
            "status", "--goal-id", "loopx-turn-fixture", "--agent-id", "codex-fixture", "--scan-root", str(project)])
    status = json.loads(status_output.getvalue())
    assert status_code == 0, status
    item = next(item for item in status["attention_queue"]["items"] if item.get("goal_id") == "loopx-turn-fixture")
    projection = item.get("project_asset", {}).get("agent_reward_memory") or item.get("agent_reward_memory")
    if experiment["available"]:
        assert projection is not None
    consumers = [experiment, configuration]
    if projection is not None:
        assert projection["automatic_ingest"] is ingest
        consumers.append(projection)
        lines: list[str] = []
        append_agent_reward_memory_markdown(lines, {"agent_reward_memory": projection}, {})
        markdown = "\n".join(lines)
        for host_id in ["codex_cli_turn", "dsh_turn", "generic_cli_turn"]:
            assert f"{host_id}:recall=connected,ingest=connected_post_settlement" in markdown
        assert "lark:recall=status_projection_only,ingest=uncovered" in markdown
    for consumer in consumers:
        coverage = {row["host_id"]: row for row in consumer["host_coverage"]}
        for host_id in ["codex_cli_turn", "dsh_turn", "generic_cli_turn"]:
            assert coverage[host_id]["automatic_recall"] == "connected"
            assert coverage[host_id]["automatic_ingest"] == "connected_post_settlement"
        assert coverage["lark"]["automatic_ingest"] == "uncovered"
