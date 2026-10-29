"""Candidate complete source through actual producers; no model is invoked."""
import copy
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

import pytest

from loopx.control_plane.quota.turn_envelope import (
    _turn_envelope_result, compose_turn_decision,
    prepare_context_delivery, restore_context_delivery,
)
from loopx.control_plane.turn_driver.codex_cli import _prompt
from loopx.control_plane.turn_driver.executor import build_loopx_turn_host_request
from tests.control_plane.test_quota_settlement_cli import (
    _write_fixture, _configure_read_only_todo, _run_cli, _spend_run_count,
    GOAL_ID, AGENT_ID, TODO_ID,
)
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime


def _valid_interaction():
    from tests.control_plane.test_replan_host_context_projection import _quota_payload

    return _quota_payload()["interaction_contract"]


def _canonical_digest(value):
    # Negative fixtures need the existing JS hash codec, independently of the
    # production validator. Source-preserving IO now legitimately retains 1.0;
    # hashing Python's spelling would reject the fixture before its semantic
    # fault is reached. Native JSON.stringify supplies the number codec only.
    canonical = subprocess.check_output(["node", "-e",
        "let s=''; process.stdin.setEncoding('utf8'); process.stdin.on('data',x=>s+=x); "
        "process.stdin.on('end',()=>process.stdout.write(JSON.stringify(JSON.parse(s))));"],
        input=json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode())
    return "sha256:" + sha256(canonical).hexdigest()


def _resign_contract(contract):
    source = {key: value for key, value in contract.items()
              if key not in {"schema_version", "contract_binding"}}
    contract["contract_binding"].pop("content_hash")
    contract["contract_binding"]["source_decision_hash"] = _canonical_digest(source)
    contract["contract_binding"]["content_hash"] = _canonical_digest(contract)
    return contract, source


def _future_producer_contract(contract):
    contract = copy.deepcopy(contract)
    contract["effective_action"] = "future_execution"
    return _resign_contract(contract)


@pytest.mark.parametrize("mutation, error", [
    ("version", "schema_version"),
    ("boolean", "must_attempt"),
    ("notification", "notify"),
    ("contradiction", "quiet"),
])
def test_complete_readers_reject_invalid_interaction_with_valid_integrity(mutation, error):
    from loopx.control_plane.quota.turn_envelope import build_turn_envelope
    from tests.control_plane.test_replan_host_context_projection import _quota_payload

    source = _quota_payload()
    interaction = source["interaction_contract"]
    if mutation == "version":
        interaction["schema_version"] = "future_interaction_version"
    elif mutation == "boolean":
        interaction["agent_channel"]["must_attempt"] = "true"
    elif mutation == "notification":
        interaction["user_channel"]["notify"] = "FUTURE_NOTIFY"
    else:
        interaction["agent_channel"]["quiet_noop_allowed"] = True
    # Obtain a valid current contract, then emulate a producer with different
    # required semantics and correct integrity hashes. Every reader must refuse.
    contract = compose_turn_decision(_quota_payload())
    contract["interaction_contract"] = interaction
    contract, _ = _resign_contract(contract)
    before = copy.deepcopy(source)
    for read in (
        lambda: compose_turn_decision(source),
        lambda: compose_turn_decision(contract),
        lambda: build_turn_envelope(contract),
        lambda: _turn_envelope_result("replan_view", payload=contract,
            source_hash=_canonical_digest(contract), stage="review"),
    ):
        with pytest.raises(RuntimeError, match=error):
            read()
    assert source == before


def test_complete_source_rejects_tampering_and_binding_substitution():
    source = {"ok": True, "goal_id": "fixture", "agent_identity": {"agent_id": "worker"},
              "effective_action": "normal_run",
              "interaction_contract": {**_valid_interaction(), "future_optional": {"value": None}}, "future_evidence": {"original": "unchanged"}}
    before = copy.deepcopy(source)
    result = compose_turn_decision(source)
    assert {k: v for k, v in result.items() if k not in {"schema_version", "contract_binding"}} == before
    assert source == before
    assert compose_turn_decision(result) == result
    tampered = copy.deepcopy(result)
    tampered["future_evidence"]["original"] = "changed"
    with pytest.raises(RuntimeError, match="integrity mismatch"):
        compose_turn_decision(tampered)
    with pytest.raises(RuntimeError, match="host binding"):
        _turn_envelope_result("compose_complete", payload=result, scheduler_execution_args="different")
    with pytest.raises(RuntimeError, match="reserved"):
        compose_turn_decision({**source, "schema_version": "unknown"})


@pytest.mark.parametrize("reference", [None, False, [], "$.user_todo_summary.first_executable_items",
    "$.agent_todo_summary.missing", "$.agent_todo_summary.backlog_items"])
def test_complete_readers_refuse_invalid_diagnostic_references_with_valid_hashes(reference):
    from loopx.control_plane.quota.turn_envelope import build_turn_envelope
    from tests.control_plane.test_replan_host_context_projection import _quota_payload

    source = _quota_payload()
    lane = [{"todo_id": "task", "text": "Preserve the complete requirement. " * 80}]
    source["agent_todo_summary"] = {"schema_version": "todo_summary_v0",
        "first_executable_items": lane, "backlog_items": copy.deepcopy(lane)}
    contract = compose_turn_decision(source)
    assert contract["agent_todo_summary"]["backlog_items_ref"] == "$.agent_todo_summary.first_executable_items"
    contract["agent_todo_summary"]["backlog_items_ref"] = reference
    contract, _ = _resign_contract(contract)
    for read in (lambda: compose_turn_decision(contract), lambda: build_turn_envelope(contract),
        lambda: _turn_envelope_result("replan_view", payload=contract,
            source_hash=_canonical_digest(contract), stage="review")):
        with pytest.raises(RuntimeError, match="summary lane reference"):
            read()


def test_pending_goal_capture_remains_readable_but_cannot_dispatch_business_work():
    from loopx.control_plane.quota.turn_envelope import build_turn_controller_input
    from loopx.control_plane.turn_driver.codex_cli import prepare_codex_cli_input
    from loopx.control_plane.turn_driver.driver import build_loopx_turn_plan
    from loopx.control_plane.turn_driver.host_candidate import extract_turn_authority, execution_profile_present
    from tests.test_turn_envelope import _full_decision

    source = _full_decision()
    source["interaction_contract"]["agent_channel"]["required_reads"] = [
        {"source": "goal_state", "command": "cat -- original-goal.md", "ordering": "before_work"}]
    contract = compose_turn_decision(source)
    before = copy.deepcopy(contract)
    plan = build_loopx_turn_plan(build_turn_controller_input(contract).envelope(), work_contract=contract,
        host="dsh", execution_mode="isolated-headless", turn_instance_id="original-turn")
    request = build_loopx_turn_host_request(plan)
    assert isinstance(execution_profile_present(request), bool)
    with pytest.raises(RuntimeError, match="Goal source"):
        prepare_codex_cli_input(request)
    with pytest.raises(ValueError, match="complete Turn contract rejected") as rejected:
        extract_turn_authority(request)
    assert "Goal source" in str(rejected.value.__cause__)
    assert contract == before


@pytest.mark.parametrize("action", ["future_execution", "", None, 0])
def test_complete_source_refuses_unknown_required_action_before_capture(action):
    from tests.control_plane.test_replan_host_context_projection import _quota_payload

    source = _quota_payload()
    source["effective_action"] = action
    original = copy.deepcopy(source)
    with pytest.raises(RuntimeError, match="effective_action"):
        compose_turn_decision(source)
    assert source == original


def test_complete_readers_refuse_unknown_action_even_with_valid_integrity():
    from loopx.control_plane.quota.turn_envelope import build_turn_envelope
    from tests.control_plane.test_replan_host_context_projection import _quota_payload

    contract, _ = _future_producer_contract(compose_turn_decision(_quota_payload()))
    # A future producer can supply perfectly valid hashes for a semantic value
    # this consumer cannot execute. Integrity alone must not admit that action.
    with pytest.raises(RuntimeError, match="effective_action"):
        compose_turn_decision(contract)
    with pytest.raises(RuntimeError, match="effective_action"):
        build_turn_envelope(contract)
    with pytest.raises(RuntimeError, match="effective_action"):
        _turn_envelope_result("replan_view", payload=contract,
            source_hash=_canonical_digest(contract), stage="review")


def test_complete_action_validation_preserves_registered_union_and_optional_facts():
    from loopx.control_plane.quota.effective_action import EffectiveAction
    from loopx.control_plane.agents.agent_scope_frontier import AgentScopeFrontierAction
    from tests.control_plane.test_replan_host_context_projection import _quota_payload

    for action in {*EffectiveAction, *AgentScopeFrontierAction}:
        source = _quota_payload()
        source["effective_action"] = action.value
        source["future_evidence"] = {"schema_version": "future_optional_evidence",
                                     "effective_action": "uninterpreted evidence", "result": None}
        contract = compose_turn_decision(source)
        assert contract["effective_action"] == action.value
        assert contract["future_evidence"] == source["future_evidence"]
        assert compose_turn_decision(contract) == contract


@pytest.mark.parametrize("mutation", ["version", "content", "binding"])
def test_replan_reader_refuses_invalid_complete_contract_even_with_matching_file_hash(mutation):
    from tests.control_plane.test_replan_host_context_projection import _quota_payload

    contract = compose_turn_decision(_quota_payload())
    if mutation == "version":
        contract["schema_version"] = "unsupported-future-version"
    elif mutation == "content":
        contract["replan_action_packet"]["planning_guidance"] = ["Altered obligation"]
    else:
        contract.pop("contract_binding")
    # A caller can truthfully hash the bytes it read without proving that their
    # version or inner source binding is valid. Both checks are required.
    digest = _canonical_digest(contract)
    with pytest.raises(RuntimeError, match="version|integrity|binding"):
        _turn_envelope_result("replan_view", payload=contract, source_hash=digest, stage="review")


@pytest.mark.parametrize("successor", [False, True])
def test_complete_replan_carries_original_turn_successor_binding_before_capture(successor):
    from tests.control_plane.test_replan_host_context_projection import _quota_payload

    source = _quota_payload()
    plan = {"identity": {"turn_instance_id": "original-turn"},
            "goal_ref": {"goal_instance_id": "original-goal-instance"}}
    source["interaction_contract"]["cli_channel"]["settlement_plan"] = plan
    if successor:
        source["replan_action_packet"]["writeback_contract"] = {
            "successor_command": "loopx todo add --goal-id fixture",
            "successor_host_action": "end_current_heartbeat"}
    before = copy.deepcopy(source)
    contract = compose_turn_decision(source)
    writer = contract["replan_action_packet"]["writeback_contract"]
    assert writer["successor_binding_args"] == (
        "--turn-instance-id original-turn --goal-instance-id original-goal-instance")
    assert "original Turn settlement plan before ending" in writer["successor_settlement"]
    assert "do not run the successor" in writer["successor_settlement"]
    assert "successor_host_action" not in writer
    assert contract["interaction_contract"]["cli_channel"]["settlement_plan"] == plan
    if successor:
        assert writer["successor_command"].endswith(writer["successor_binding_args"])
        assert contract["interaction_contract"]["cli_channel"]["next_cli_actions"] == [writer["successor_command"]]
    assert source == before
    assert compose_turn_decision(contract) == contract


@pytest.mark.parametrize("inactive", ["settlement_only", "capability_intent", "different_context", "unbound"])
def test_complete_composition_preserves_inactive_replan_authoring(inactive):
    from tests.control_plane.test_replan_host_context_projection import _quota_payload

    source = _quota_payload()
    source["interaction_contract"]["cli_channel"]["settlement_plan"] = {
        "identity": {"turn_instance_id": "original-turn"}}
    source["replan_action_packet"]["writeback_contract"] = {
        "successor_command": "loopx todo add --goal-id fixture",
        "successor_host_action": "end_current_heartbeat"}
    if inactive == "settlement_only":
        source["replan_action_packet"]["settlement_only"] = True
    elif inactive == "capability_intent":
        source["effective_action"] = "governed_capability_intent"
    elif inactive == "different_context":
        source["autonomous_replan_obligation"]["replan_context"]["obligation_id"] = "different"
    else:
        source["interaction_contract"]["cli_channel"].pop("settlement_plan")
    contract = compose_turn_decision(source)
    assert {k: v for k, v in contract.items() if k not in {"schema_version", "contract_binding"}} == source


def test_complete_markdown_keeps_unknown_facts_types_and_instruction_text():
    from loopx.presentation.renderers.quota_markdown import render_quota_should_run_markdown
    from loopx.presentation.renderers.turn_envelope_markdown import render_turn_envelope_markdown

    source = {"ok": True, "goal_id": "fixture", "agent_identity": {"agent_id": "worker"},
        "effective_action": "normal_run",
        "interaction_contract": _valid_interaction(),
        "future_evidence": {"missing": None, "denied": False, "attempts": 0, "text": "0"}}
    source["interaction_contract"]["agent_channel"]["required_reads"] = [
        {"instruction": "Read the original constraints.\n```\nStop before publication."}]
    contract = compose_turn_decision(source)
    before = copy.deepcopy(contract)
    for render in (render_quota_should_run_markdown, render_turn_envelope_markdown):
        rendered = render(contract)
        # The complete JSON remains one source even for a human-readable CLI.
        actual = json.loads(rendered.split("```json\n", 1)[1].rsplit("\n```", 1)[0])
        assert actual == before
        assert contract == before


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("host_args", [("--codex-app",), ("--runtime-profile", "generic_cli")])
def test_complete_quota_successor_settles_original_turn_without_staged_reader(tmp_path, monkeypatch, provider, host_args):
    import shlex
    from tests.control_plane.test_replan_successor_guard_reentry import _fixture, GOAL, AGENT, TURN

    call, runtime, index = _fixture(tmp_path, monkeypatch, provider, future_monitor=False)
    capture = tmp_path / "capture"
    guard = call("quota", "should-run", *host_args, "--goal-id", GOAL,
        "--agent-id", AGENT, "--turn-instance-id", TURN, "--decision-output-dir", str(capture))
    original = (capture / "decision.json").read_bytes()
    assert json.loads(original) == guard
    identity = guard["heartbeat_receipt"]["settlement_identity"]
    writer = guard["replan_action_packet"]["writeback_contract"]
    binding = shlex.split(writer["successor_binding_args"])
    assert binding[binding.index("--turn-instance-id") + 1] == TURN
    added = call("todo", "add", "--goal-id", GOAL, "--role", "agent", "--claimed-by", AGENT,
        "--text", "Independently verify the source artifact", "--task-class", "advancement_task",
        "--action-kind", "validate", "--target-key", "independent-source-artifact",
        "--operation-id", "complete-source-successor", "--replan-obligation-id", identity["replan_obligation_id"],
        *binding)
    assert added["settlement_identity"] == identity
    assert added["replan_transition"]["turn_boundary"] == "settle_original_turn_before_end"
    steps = added["settlement_plan"]["ordered_steps"]
    refresh = next(row["command_template"] for row in steps if row["kind"] == "durable_writeback")
    spend = next(row["command_template"] for row in steps if row["kind"] == "quota_spend")
    refused = call(*shlex.split(spend)[1:], expected_code=1)
    assert refused.get("appended") is not True
    for placeholder, value in {"<validated_progress>": "successor_created",
        "<scale>": "single_surface", "<outcome>": "outcome_progress",
        "<boundary>": "in_flight_continuation"}.items():
        refresh = refresh.replace(placeholder, value)
    refreshed = call(*shlex.split(refresh)[1:], "--no-global-sync", "--suppress-external-sinks",
        "--vision-unchanged-reason", "The accepted source outcome is unchanged; the successor supplies its next validation.")
    assert refreshed["settlement_progress"]["state"] == "spend_required"
    spent = call(*shlex.split(spend)[1:])
    assert spent["settlement_progress"]["state"] == "settled"
    assert call(*shlex.split(spend)[1:])["appended"] is False
    assert sum(json.loads(line).get("classification") == "quota_slot_spent" for line in index.read_text().splitlines()) == 1
    todo = call("todo", "list", "--goal-id", GOAL, "--todo-id", added["todo_id"])["todo"]
    assert todo["status"] == "open"  # Creating/settling is not executing the successor.
    assert (capture / "decision.json").read_bytes() == original

    # After inline execution/settlement, check historical material-reader parity.
    digest = _canonical_digest(guard)
    staged = _turn_envelope_result("replan_view", payload=guard, source_hash=digest, stage="writeback")
    assert staged["writeback_contract"]["successor_binding_args"] == writer["successor_binding_args"]
    assert staged["writeback_contract"]["successor_settlement"] == writer["successor_settlement"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("format_name,envelope_flag", [("json", True), ("markdown", True), ("markdown", False)])
def test_real_quota_complete_capture_and_differential_recovery(tmp_path, monkeypatch, provider, format_name, envelope_flag):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    state = _configure_read_only_todo(project)
    from tests.control_plane.canonical_authority_fixture import initialize_canonical_authority, read_fixture_todos
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    initialize_canonical_authority(runtime, GOAL_ID,
        build_todo_runtime_shadow_projection(goal_id=GOAL_ID, handoff_mode="soft_claim", todos=read_fixture_todos(state)),
        state_path=state, provider=provider)
    capture = tmp_path / "capture"
    command = [sys.executable, "-m", "loopx.entrypoint", "--registry", str(registry),
        "--runtime-root", str(runtime), "--format", format_name, "quota", "should-run", "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID, "--todo-id", TODO_ID, "--turn-instance-id", "complete-source",
        "--codex-app", "--available-capability", "shell", "--available-capability", "filesystem_read",
        "--decision-output-dir", str(capture)]
    if envelope_flag:
        command.append("--turn-envelope")
    process = subprocess.run(command, cwd=Path(__file__).resolve().parents[2],
        capture_output=True, text=True, check=False)
    assert process.returncode == 0, process.stdout + process.stderr
    text = process.stdout if format_name == "json" else process.stdout.split("```json\n", 1)[1].rsplit("\n```", 1)[0]
    output = json.loads(text)
    original = (capture / "decision.json").read_bytes()
    contract = json.loads(original)
    assert contract["schema_version"] == "loopx_turn_envelope_v1"
    summary = contract["agent_todo_summary"]
    assert summary["first_open_items_ref"] == "$.agent_todo_summary.first_executable_items"
    assert summary["first_executable_items"][0]["todo_id"] == TODO_ID
    assert summary["first_executable_items"][0]["status"] == "open"
    assert output["interaction_contract"] == contract["interaction_contract"]
    assert output["contract_binding"] == contract["contract_binding"]
    assert output == contract  # Every original fact and instruction survives rendering.
    assert "action" not in output and "contract_capsule" not in output
    scope = dict(goal_id=GOAL_ID, agent_id=AGENT_ID, session_id="fixture", context_epoch="one")
    first = prepare_context_delivery(contract, scope=scope, capture_id="one", surface="complete_turn")
    current = prepare_context_delivery(contract, scope=scope, capture_id="two", surface="complete_turn")
    warm = prepare_context_delivery(contract, scope=scope, capture_id="two", surface="complete_turn",
        baseline=first, retained={"complete": True, "target": first["target"]})
    assert restore_context_delivery(warm, baseline=first, current=current)["context"] == contract
    assert restore_context_delivery(warm, baseline=None, current=current)["context"] == contract
    old = prepare_context_delivery(contract, scope=scope, capture_id="one", surface="quota_packet")
    changed_surface = prepare_context_delivery(contract, scope=scope, capture_id="two", surface="complete_turn",
        baseline=old, retained={"complete": True, "target": old["target"]})
    assert changed_surface["mode"] == "full"
    assert (capture / "decision.json").read_bytes() == original
    assert _spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("first_attempt_fails", [False, True])
def test_real_turn_preview_reaches_complete_codex_input(tmp_path, monkeypatch, provider, first_attempt_fails):
    from tests.control_plane.test_turn_host_decision_capture import _fixture_module
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    from loopx.control_plane.todos.active_state_todo_parser import parse_active_state_todos
    from tests.control_plane.canonical_authority_fixture import initialize_canonical_authority
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    from loopx.cli_commands import turn_run_once

    prepare_request = turn_run_once.prepare_turn_host_request

    class SignatureOnlyEnvelope(dict):
        def get(self, key, default=None):
            if key in {"action", "task_orchestration_contract", "writeback",
                       "effective_action", "replan_action_packet"}:
                raise AssertionError("complete execution read the historical adapter: " + key)
            return super().get(key, default)

    def require_complete_execution(*args, **kwargs):
        project, argv, validator, envelope = prepare_request(*args, **kwargs)
        # Keep the real adapter for source/signature verification, but forbid
        # CLI execution policy from reading its obsolete field layout. The
        # persisted plan, native host, provider and settlement remain real.
        if "work_contract" in args[1]:
            envelope = SignatureOnlyEnvelope(envelope)
        return project, argv, validator, envelope

    monkeypatch.setattr(turn_run_once, "prepare_turn_host_request", require_complete_execution)
    f = _fixture_module()
    project, runtime, workspace, registry = f._write_fixture(tmp_path, turn_count=2)
    state = project / ".codex/goals" / f.GOAL_ID / "ACTIVE_GOAL_STATE.md"
    summary = parse_active_state_todos(state.read_text(), state_path=state, item_limit=None)
    initialize_canonical_authority(runtime, f.GOAL_ID,
        build_todo_runtime_shadow_projection(goal_id=f.GOAL_ID, handoff_mode="soft_claim",
            todos=summary["agent_todos"]["items"]), state_path=state, provider=provider)
    # Add acceptance through the real authority API, so native execution and
    # failed-turn recovery exercise sharing rather than a hand-built contract.
    prefix = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json"]
    document = tmp_path / "acceptance.json"
    document.write_text(json.dumps({"outcome_id": "complete-input", "owner_agent_id": f.AGENT_ID,
        "objective": "Deliver the complete requested artifact", "todo_ids": [f.TODO_ID],
        "requirements": [{"id": "complete", "description": "Preserve the original content and tail constraint."}],
        "source": {"ref": "message:owner", "summary": "Original artifact requirement"}}))
    code, inspected = f._run_cli([*prefix, "goal-acceptance", "inspect", "--goal-id", f.GOAL_ID])
    assert code == 0, inspected
    code, refined = f._run_cli([*prefix, "goal-acceptance", "refine", "--goal-id", f.GOAL_ID,
        "--agent-id", f.AGENT_ID, "--document", str(document), "--expected-provider-revision",
        inspected["provider_revision"], "--operation-id", "original-acceptance", "--execute"])
    assert code == 0, refined
    code, inspected = f._run_cli([*prefix, "goal-acceptance", "inspect", "--goal-id", f.GOAL_ID])
    assert code == 0, inspected
    expected_acceptance = inspected["goal_acceptance_contract"]
    args = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
        "turn", "plan", "--goal-id", f.GOAL_ID, "--agent-id", f.AGENT_ID,
        "--turn-instance-id", "complete-source", "--host", "codex-cli",
        "--execution-mode", "isolated-headless", "--include-transaction-detail"]
    code, plan = f._run_cli(args)
    assert code == 0, plan
    assert "work_contract" in plan, json.dumps(plan)
    contract = plan["work_contract"]
    assert contract["schema_version"] == "loopx_turn_envelope_v1"
    request = build_loopx_turn_host_request(plan)
    prompt = _prompt(request)
    actual = json.loads(prompt.split("Turn request:\n", 1)[1])
    assert actual["work_contract"] == contract
    assert "turn_envelope" not in actual and "turn_authority" not in actual
    from loopx.control_plane.turn_driver.host_candidate import extract_turn_authority, render_prompt
    structured = render_prompt(extract_turn_authority(request))
    structured_input, _ = json.JSONDecoder().raw_decode(structured.split("Turn request:\n", 1)[1])
    assert structured_input == actual
    assert "_model_input" not in structured_input
    tampered = copy.deepcopy(request)
    tampered["work_contract"]["reason"] = "different facts"
    with pytest.raises(RuntimeError, match="integrity mismatch"):
        _prompt(tampered)
    with pytest.raises(ValueError, match="complete Turn contract rejected"):
        extract_turn_authority(tampered)
    from loopx.dsh_goal_mode.turn_host_adapter import DshHostConfig, run_dsh_host
    from loopx.control_plane.turn_driver.host_failure import BuiltInHostError
    with pytest.raises(BuiltInHostError, match="dsh_turn_authority_rejected"):
        run_dsh_host(tampered, config=DshHostConfig(workspace=workspace,
            dsh_runner=tmp_path / "must-not-load.py"))

    assert not (workspace / ".dsh").exists()
    assert not list(runtime.rglob("decisions/*.json"))
    assert f._quota_spend_count(runtime) == 0

    # An old journal must not inherit a new admission's complete source.
    from loopx.control_plane.turn_driver.decision_capture import prepare_turn_decision_capture
    historical = {key: value for key, value in contract.items()
                  if key not in {"schema_version", "contract_binding"}}
    historical_envelope = _turn_envelope_result("build", payload=historical,
        scheduler_execution_args=contract["contract_binding"]["scheduler_execution_args"])
    historical_plan = {**plan, "turn_envelope": historical_envelope}
    historical_journal = tmp_path / "historical" / "journal.json"
    prepared = prepare_turn_decision_capture(historical_plan, journal_path=historical_journal,
        decision=historical, existing_journal=False)
    restored = prepare_turn_decision_capture({**prepared, "work_contract": contract},
        journal_path=historical_journal, decision=None, existing_journal=True)
    assert "work_contract" not in restored
    assert "work_contract" not in build_loopx_turn_host_request(restored)

    executable = f._write_fake_codex(tmp_path)
    script = executable.read_text().replace("#!/usr/bin/env python3", "#!" + sys.executable)
    script = script.replace("prompt = sys.stdin.read()", 'prompt = sys.stdin.read()\npathlib.Path("received.txt").write_text(prompt)')
    if first_attempt_fails:
        script = script.replace("marker = pathlib.Path(",
            'failed = pathlib.Path("failed-once")\n'
            'if not failed.exists():\n'
            '    failed.touch()\n'
            '    print(json.dumps({"type":"thread.started","thread_id":"session-fixture-0001"}), flush=True)\n'
            '    print("Selected model is at capacity. Please try a different model.", file=sys.stderr)\n'
            '    raise SystemExit(9)\n'
            'marker = pathlib.Path(')
    executable.write_text(script)
    run_args = f._base_argv(registry=registry, runtime=runtime, workspace=workspace,
        codex_bin=executable, model=None, timeout_seconds=20,
        expected_marker=f._marker_value(1), turn_instance_id="complete-execution")
    from loopx.cli_commands import turn as native_turn
    real_compose = native_turn.compose_turn_decision
    def future_action(payload, **kwargs):
        return real_compose({**payload, "effective_action": "future_execution"}, **kwargs)
    with monkeypatch.context() as patch:
        # Fault only the producer's semantic value. Admission, provider,
        # composition, CLI refusal and capture/host boundaries remain real.
        patch.setattr(native_turn, "compose_turn_decision", future_action)
        code, refused = f._run_cli([*run_args, "--execute"])
    assert code == 1 and "effective_action" in json.dumps(refused), refused
    assert not (workspace / "received.txt").exists()
    assert not list(runtime.rglob("decisions/*.json"))
    assert f._quota_spend_count(runtime) == 0
    # Preserve the historical decoder's existing route, but refuse the same
    # unknown execution semantics in a validly hashed complete contract before
    # either native host can load a runner or produce a captured execution.
    from loopx.control_plane.turn_driver.driver import build_loopx_turn_plan
    future_contract, future_source = _future_producer_contract(contract)
    legacy_envelope = _turn_envelope_result("build", payload=future_source,
        scheduler_execution_args=contract["contract_binding"]["scheduler_execution_args"])
    legacy_plan = build_loopx_turn_plan(legacy_envelope, host="codex-cli",
        execution_mode="isolated-headless", turn_instance_id="future-source")
    assert legacy_plan["route"]["would_invoke_host"] is True
    future_request = build_loopx_turn_host_request({**legacy_plan, "work_contract": future_contract})
    with pytest.raises(RuntimeError, match="effective_action"):
        _prompt(future_request)
    with pytest.raises(ValueError, match="complete Turn contract rejected"):
        extract_turn_authority(future_request)
    with pytest.raises(BuiltInHostError, match="dsh_turn_authority_rejected"):
        run_dsh_host(future_request, config=DshHostConfig(workspace=workspace,
            dsh_runner=tmp_path / "must-not-load.py"))

    invalid_interaction = copy.deepcopy(request)
    invalid_interaction["work_contract"]["interaction_contract"]["schema_version"] = "future_interaction_version"
    invalid_interaction["work_contract"], _ = _resign_contract(invalid_interaction["work_contract"])
    with pytest.raises(RuntimeError, match="interaction_contract.schema_version"):
        _prompt(invalid_interaction)
    with pytest.raises(ValueError, match="complete Turn contract rejected"):
        extract_turn_authority(invalid_interaction)
    with pytest.raises(BuiltInHostError, match="dsh_turn_authority_rejected"):
        run_dsh_host(invalid_interaction, config=DshHostConfig(workspace=workspace,
            dsh_runner=tmp_path / "must-not-load.py"))
    assert not (workspace / "received.txt").exists()
    assert not (workspace / ".dsh").exists()
    assert f._quota_spend_count(runtime) == 0

    code, executed = f._run_cli([*run_args, "--execute"])
    if first_attempt_fails:
        assert code == 1 and executed["status"] == "failed", executed
        assert f._quota_spend_count(runtime) == 0
    else:
        assert code == 0 and executed["status"] == "committed", executed
    observed = json.loads((workspace / "received.txt").read_text().split("Turn request:\n", 1)[1])
    assert observed["work_contract"]["schema_version"] == "loopx_turn_envelope_v1"
    assert "turn_envelope" not in observed
    acceptance_sources = observed["work_contract"]["interaction_contract"]["agent_channel"]["work_context"]["sources"]
    acceptance_index = next(i for i, row in enumerate(acceptance_sources) if row["source"] == "goal_acceptance")
    assert acceptance_sources[acceptance_index]["content"]["goal_acceptance_contract"] == expected_acceptance
    assert observed["work_contract"]["agent_todo_summary"]["goal_acceptance_contract_ref"] == (
        f"$.interaction_contract.agent_channel.work_context.sources[{acceptance_index}].content.goal_acceptance_contract")
    saved = list(runtime.rglob("decisions/*.json"))
    assert len(saved) == 1
    saved_bytes = saved[0].read_bytes()
    assert json.loads(saved_bytes) == observed["work_contract"]
    journal = json.loads((saved[0].parent.parent / saved[0].name).read_text())
    # Native journal persistence must preserve the captured source's number
    # types and precision, not merely Python's permissive 1 == 1.0 equality.
    assert json.dumps(journal["plan"]["work_contract"], sort_keys=True) == json.dumps(
        observed["work_contract"], sort_keys=True)
    replay_args = list(run_args)
    index = replay_args.index("--turn-instance-id")
    del replay_args[index:index + 2]
    if first_attempt_fails:
        retry_args = [*replay_args, "--resume-turn-key", observed["turn_key"], "--retry-failed-turn", "--execute"]
        received = (workspace / "received.txt").read_bytes()
        for corrupt in (True, False):
            if corrupt:
                saved[0].write_text(json.dumps({**observed["work_contract"], "reason": "tampered source"}))
            else:
                saved[0].unlink()
            code, rejected = f._run_cli(retry_args)
            assert code == 1, rejected
            assert (workspace / "received.txt").read_bytes() == received
            assert f._quota_spend_count(runtime) == 0
            saved[0].write_bytes(saved_bytes)
        code, recovered = f._run_cli(retry_args)
        assert code == 0 and recovered["status"] == "committed", recovered
        restored = json.loads((workspace / "received.txt").read_text().split("Turn request:\n", 1)[1])
        assert restored["work_contract"] == observed["work_contract"]
    code, replay = f._run_cli([*replay_args, "--resume-turn-key", observed["turn_key"], "--execute"])
    assert code == 0 and replay["replayed"] is True, replay
    assert saved[0].read_bytes() == saved_bytes
    assert f._quota_spend_count(runtime) == 1


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("prior_todo_binding", [False, True])
def test_native_replan_complete_input_retains_verified_material_readers(tmp_path, monkeypatch, provider, prior_todo_binding):
    from tests.control_plane.test_turn_replan_writeback import _fixture
    from tests.control_plane.test_quota_settlement_cli import _classification_count, SELECTED_REPLAN_TODO_ID
    project, runtime, registry, _state, _prior_turn = _fixture(tmp_path, monkeypatch, provider)
    configuration = json.loads(registry.read_text())
    configuration["goals"][0]["explore_graph"] = {"enabled": True}
    registry.write_text(json.dumps(configuration))
    turn = _prior_turn if prior_todo_binding else "complete-replan-readers"
    executable = tmp_path / "replan-host"
    executable.write_text("#!" + sys.executable + '''
import json, pathlib, shlex, subprocess, sys
print(json.dumps({"type":"thread.started", "thread_id":"session-fixture-replan"}), flush=True)
prompt = sys.stdin.read()
root = pathlib.Path.cwd()
(root / "received.txt").write_text(prompt)
request = json.loads(prompt.split("Turn request:\\n", 1)[1])
packet = request["detail_ref"]["replan_action_packet"]
for stage in ("review", "writeback"):
    read = subprocess.run(shlex.split(packet[stage]), text=True, capture_output=True, check=True)
    (root / (stage + ".json")).write_text(read.stdout)
output = pathlib.Path(sys.argv[sys.argv.index("--output-last-message") + 1])
output.write_text(json.dumps({"schema_version":"loopx_turn_result_v0", "turn_key":request["turn_key"],
    "result_kind":"wait", "completed_phases":["host_execute","typed_result"],
    "summary":"Original material readers verified; no task outcome claimed."}))
''')
    executable.chmod(0o700)
    code, result = _run_cli(registry, runtime, "turn", "run-once", "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID, "--turn-instance-id", turn, "--host", "codex-cli",
        "--execution-mode", "isolated-headless", "--project", str(project),
        "--codex-bin", str(executable), "--timeout-seconds", "20", "--no-global-sync", "--execute", cwd=project)
    assert code == 0, json.dumps(result, indent=2)
    prompt = (project / "received.txt").read_text()
    request = json.loads(prompt.split("Turn request:\n", 1)[1])
    source = request["work_contract"]
    assert source["schema_version"] == "loopx_turn_envelope_v1"
    assert "turn_envelope" not in request and "turn_authority" not in request
    identity = result["receipt"]["lineage"]
    if prior_todo_binding:
        assert identity["todo_id"] == SELECTED_REPLAN_TODO_ID
        assert not identity.get("replan_obligation_id")
    else:
        assert not identity["todo_id"]
        assert identity["replan_obligation_id"] == source["autonomous_replan_obligation"]["obligation_id"]
    graph = next(row["content"]["graph"] for row in
        source["interaction_contract"]["agent_channel"]["work_context"]["sources"]
        if row.get("hook_id") == "explore.turn_context")
    assert ("result_attachment_template" in graph) is prior_todo_binding
    assert ("unavailable for this pure replan" in graph["result_writeback_guidance"]) is not prior_todo_binding
    assert "Read measurement_feedback before repeating a measured route" in graph["result_writeback_guidance"]
    assert graph["record_finding_template"] and graph["record_node_template"]
    context = source["autonomous_replan_obligation"]["replan_context"]
    review = json.loads((project / "review.json").read_text())
    writeback = json.loads((project / "writeback.json").read_text())
    assert review["context_id"] == writeback["context_id"] == context["context_id"]
    # The native outer controller owns settlement; its model input has no
    # inline settlement owner to reference. Preserve all authoring here.
    contract = source["replan_action_packet"]["writeback_contract"]
    steps = source["interaction_contract"]["cli_channel"].get("settlement_plan", {}).get("ordered_steps", [])
    assert not any("vision_authoring" in step for step in steps)
    assert "vision_authoring_ref" not in contract
    if "vision_authoring" in contract:
        assert writeback["writeback_contract"]["vision_authoring"] == {
            key: value for key, value in contract["vision_authoring"].items() if key != "minimal_example"}
    assert review["source_hash"] == writeback["source_hash"] == request["source_decision_hash"]
    assert "Read detail_ref.replan_action_packet.review" in prompt
    assert "before reading detail_ref.replan_action_packet.writeback" in prompt
    assert "Never re-enter quota admission" in prompt and "Do not execute writeback commands" in prompt
    from pathlib import Path
    import shlex
    import subprocess
    captured = Path(request["detail_ref"]["captured_decision"]["path"])
    original = captured.read_bytes()
    assert json.loads(original) == source
    captured.write_text(json.dumps({**source, "reason": "Changed original facts"}))
    rejected = subprocess.run(shlex.split(request["detail_ref"]["replan_action_packet"]["review"]),
        capture_output=True, text=True)
    assert rejected.returncode != 0
    captured.write_bytes(original)
    assert _classification_count(runtime, "quota_slot_spent") == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("prior_todo_binding", [False, True])
def test_real_structured_host_reads_same_complete_replan_source(tmp_path, monkeypatch, provider, prior_todo_binding):
    from tests.control_plane.test_turn_replan_writeback import _fixture, _runner, _run_turn
    project, runtime, registry, _state, prior_turn = _fixture(tmp_path, monkeypatch, provider)
    runner = _runner(tmp_path, {"result_kind": "wait", "summary": "Original evidence read; no outcome claimed."})
    script = runner.read_text().replace("import json\n", "import json, shlex, subprocess\n")
    script = script.replace("    return json.dumps(candidate)",
        "    candidate.pop('work_evidence', None)\n    candidate.pop('validation_evidence', None)\n    return json.dumps(candidate)")
    script = script.replace("    counter = root / 'host-count.txt'", """    prompt = kwargs["prompt"]
    (root / "received.txt").write_text(prompt)
    request, _ = json.JSONDecoder().raw_decode(prompt.split("Turn request:\n", 1)[1])
    for stage in ("review", "writeback"):
        result = subprocess.run(shlex.split(request["detail_ref"]["replan_action_packet"][stage]),
            text=True, capture_output=True, check=True)
        (root / (stage + ".json")).write_text(result.stdout)
    counter = root / 'host-count.txt'""")
    # The runner is a separate Python module; preserve its literal newline escape.
    script = script.replace('split("Turn request:\n",', 'split("Turn request:\\n",')
    runner.write_text(script)
    turn = prior_turn if prior_todo_binding else "structured-complete-replan"
    rc, result = _run_turn(project, runtime, registry, runner, turn=turn)
    if not prior_todo_binding:
        # Structured DSH still has Todo-scoped sessions: an obligation-only
        # replan remains blocked rather than inventing a Todo or widening scope.
        assert rc == 1 and result["error"] == "LoopX Turn route is not host executable"
        assert not (project / "received.txt").exists()
        assert _spend_run_count(runtime) == 0
        return
    assert rc == 0, json.dumps(result, indent=2)
    prompt = (project / "received.txt").read_text()
    request, _ = json.JSONDecoder().raw_decode(prompt.split("Turn request:\n", 1)[1])
    source = request["work_contract"]
    assert source["schema_version"] == "loopx_turn_envelope_v1"
    assert "turn_envelope" not in request and "turn_authority" not in request
    saved = list(runtime.rglob("decisions/*.json"))
    assert len(saved) == 1 and json.loads(saved[0].read_text()) == source
    for stage in ("review", "writeback"):
        material = json.loads((project / (stage + ".json")).read_text())
        assert material["ok"] is True
        assert material["source_hash"] == request["source_decision_hash"]
    assert "before reading detail_ref.replan_action_packet.writeback" in prompt
    assert "outer Turn executor owns those effects" in prompt
    assert _spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_external_worker_validates_complete_source_before_wake(tmp_path, monkeypatch, provider):
    import shlex
    from tests.test_external_scheduler_worker import _args, _write_executable, worker
    from tests.control_plane.canonical_authority_fixture import initialize_canonical_authority, read_fixture_todos
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    state = _configure_read_only_todo(project)
    initialize_canonical_authority(runtime, GOAL_ID,
        build_todo_runtime_shadow_projection(goal_id=GOAL_ID, handoff_mode="soft_claim", todos=read_fixture_todos(state)),
        state_path=state, provider=provider)
    rc, contract = _run_cli(registry, runtime, "quota", "should-run", "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID, "--todo-id", TODO_ID, "--turn-instance-id", "external-source",
        "--runtime-profile", "generic_cli", "--include-detail", "scheduler",
        "--available-capability", "shell", "--available-capability", "filesystem_read", cwd=project)
    assert rc == 0, contract
    assert contract["schema_version"] == "loopx_turn_envelope_v1"
    assert contract["interaction_contract"]["agent_channel"]["must_attempt"] is True
    original = copy.deepcopy(contract)
    before_state = state.read_bytes()
    for mutation in ("valid", "version", "missing_version", "missing_binding", "content",
                     "terminal", "action", "interaction", "reference"):
        candidate = copy.deepcopy(contract)
        if mutation == "version":
            candidate["schema_version"] = "unsupported-future-version"
        elif mutation == "missing_version":
            candidate.pop("schema_version")
        elif mutation == "missing_binding":
            candidate.pop("contract_binding")
        elif mutation == "content":
            candidate["scheduler_hint"]["reason"] = "Changed after capture"
        elif mutation == "terminal":
            candidate["scheduler_hint"]["unchanged_poll"] = {"local_scheduler": "stop"}
        elif mutation == "action":
            candidate["effective_action"] = "future_execution"
            candidate, _ = _resign_contract(candidate)
        elif mutation == "interaction":
            candidate["interaction_contract"]["schema_version"] = "future_interaction_version"
            candidate, _ = _resign_contract(candidate)
        elif mutation == "reference":
            candidate.setdefault("agent_lane_next_action", {}).pop("title", None)
            candidate["agent_lane_next_action"]["title_ref"] = "$.missing_original_requirement"
            candidate, _ = _resign_contract(candidate)
        case = tmp_path / mutation
        cli, marker = case / "quota", case / "wake-called"
        _write_executable(cli, f"#!{sys.executable}\nprint({json.dumps(candidate)!r})\n")
        args = _args(fake_cli=cli, state_file=case / "state.json",
            wake_cmd=shlex.join([sys.executable, "-c",
                f"from pathlib import Path; Path({str(marker)!r}).touch()"]), wake_timeout_seconds=5)
        args.goal_id, args.agent_id = GOAL_ID, AGENT_ID
        result = worker.run_worker(args)
        assert result == (0 if mutation == "valid" else 2), mutation
        assert marker.exists() is (mutation == "valid"), mutation
    assert contract == original
    assert state.read_bytes() == before_state
    assert _spend_run_count(runtime) == 0
