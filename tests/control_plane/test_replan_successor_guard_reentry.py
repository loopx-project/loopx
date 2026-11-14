"""A canonical successor changes the frontier, never the original Turn binding."""
from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
import loopx

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from tests.control_plane.codex_checkpoint_fixture import read_cli_policy_payload, selected_context_values
from test_replan_successor_durable_ack import AGENT, GOAL, history

from loopx.capabilities.machine_configuration.builtins import build_builtin_machine_configuration_registry
from loopx.capabilities.machine_configuration.store import configure_machine_configuration
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.goals.goal_vision import compact_goal_vision_packet, normalize_goal_vision_packet
from loopx.goal_mode_mcp import GoalModeMCPConfig, GoalModeMCPControlPlane


TURN = "turn-periodic-successor-review"
ROOT = Path(loopx.__file__).resolve().parents[1]


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, *, later_vision: bool = False, future_monitor: bool = True, initial_todos: list[dict] | None = None, handoff_mode: str = "soft_claim"):
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime = tmp_path / "project", tmp_path / "runtime"
    project.mkdir()
    state = project / "ACTIVE_GOAL_STATE.md"
    state.write_text("---\nstatus: active\n---\n\n# Synthetic Goal\n\n## Objective\nDeliver the independently accepted source outcome.\n\n## Agent Todo\n")
    index = runtime / "goals" / GOAL / "runs" / "index.jsonl"
    index.parent.mkdir(parents=True)
    evidence = index.parent / "synthetic-artifact.json"
    report = index.parent / "synthetic-artifact.md"
    evidence.write_text(json.dumps({"ok": True, "fixture": "public-safe-replan"}))
    report.write_text("# Synthetic source audit\n")
    runs = history()
    for row in runs:
        row.update(json_path=str(evidence), markdown_path=str(report))
    runs[0]["agent_vision"] = compact_goal_vision_packet(normalize_goal_vision_packet({
        "goal_id": GOAL, "agent_id": AGENT, "state": "vision_drift_detected",
        "vision_patch": {"acceptance_summary": "Independently validate a source artifact.",
                         "advancement_policy": "repeat_until_closed"},
    }, goal_id=GOAL, agent_id=AGENT))
    if later_vision:
        # A settled periodic review is older than the new acceptance gap.
        # The real guard must not turn its vision-patch label into a waiver.
        old = {**runs[1], "generated_at": "2026-08-01T00:29:00Z",
            "autonomous_replan_ack": {
                "schema_version": "autonomous_replan_ack_v0", "recorded": True,
                "delta_contract": {"delta_kinds": ["goal_vision_patch"]},
                "semantic_delta": {"accepted": True, "outcomes": ["fresh_vision_path_outcome"],
                    "satisfying_outcomes": ["fresh_vision_path_outcome"],
                    "trigger_kinds": ["periodic_review_due"], "trigger_checkpoints": [],
                    "obligation_id": "replan-1111111111111111"}}}
        runs = [{**runs[0], "generated_at": "2026-08-01T00:30:00Z"}, old]
    index.write_text("".join(json.dumps(row) + "\n" for row in reversed(runs)))
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{
        "id": GOAL, "status": "active", "repo": str(project), "state_file": state.name,
        "domain": "synthetic-replan",
        "adapter": {"kind": "fixture_connected_delivery_v0", "status": "connected-delivery"},
        "quota": {"compute": 1.0, "window_hours": 24},
        "coordination": {"agent_model": "peer_v1", "registered_agents": [AGENT, "different-agent"]},
    }]}))
    initialize_canonical_authority(runtime, GOAL, build_todo_runtime_shadow_projection(
        goal_id=GOAL, todos=initial_todos or [], handoff_mode=handoff_mode, leases=[],
    ), state_path=state, provider=provider)

    def call(*args: str, expected_code: int = 0) -> dict:
        # This fixture inspects the authority decision; checkpoint tests cover
        # presentation and follow its captured reader to the delivered policy.
        if args[:2] == ("quota", "should-run") and not any(
            arg == "--context-delivery" or arg.startswith("--context-delivery=") for arg in args
        ):
            args = (*args, "--context-delivery", "off")
        result = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(registry),
            "--runtime-root", str(runtime), "--format", "json", *args], cwd=ROOT,
            capture_output=True, text=True, timeout=60)
        payload = json.loads(result.stdout)
        assert result.returncode == expected_code, {k: payload.get(k) for k in ["error", "reason", "autonomous_replan_obligation", "heartbeat_receipt", "goal_frontier_projection"]}
        return payload

    if later_vision and future_monitor:
        call("todo", "add", "--goal-id", GOAL, "--role", "agent", "--claimed-by", AGENT,
            "--text", "Observe the independent external gate", "--task-class", "continuous_monitor",
            "--action-kind", "monitor", "--target-key", "external-gate",
            "--cadence", "30m", "--next-due-at", "2099-01-01T00:00:00Z",
            "--expires-at", "2099-01-02T00:00:00Z")
    return call, runtime, index


def _guard(call, turn: str = TURN, *, expected_code: int = 0):
    return call("quota", "should-run", "--codex-app", "--goal-id", GOAL,
                "--agent-id", AGENT, "--turn-instance-id", turn,
                "--codex-app-current-rrule", "FREQ=MINUTELY;INTERVAL=3", expected_code=expected_code)


def _add(call, obligation_id: str):
    return call("todo", "add", "--goal-id", GOAL, "--role", "agent", "--claimed-by", AGENT,
        "--text", "Independently verify the source artifact", "--task-class", "advancement_task",
        "--action-kind", "validate", "--target-key", "independent-source-artifact",
        "--operation-id", "periodic-source-successor", "--replan-obligation-id", obligation_id)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("native_session,saved_projection,native_context", [
    (False, False, False), (False, False, True), (True, False, True),
    (False, True, True), (True, True, True),
], ids=["stateless-guard", "cold-native-guard", "active-native-guard", "cold-saved", "active-saved"])
def test_bound_successor_review_reads_current_evidence_and_uses_creation_receipt(
    tmp_path, monkeypatch, provider, native_session, saved_projection, native_context,
):
    call, runtime, index = _fixture(tmp_path, monkeypatch, provider, future_monitor=False)
    monkeypatch.delenv("LOOPX_CONTEXT_DELIVERY", raising=False)
    native, captures = tmp_path / "native", tmp_path / "captures"
    for directory in (native, captures):
        directory.mkdir(mode=0o700)
    project = tmp_path / "project"
    session = "bound-successor-review"

    def hook(name, **fields):
        result = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(tmp_path / "registry.json"),
            "--runtime-root", str(runtime), "--format", "json", "quota", "context-hook",
            "--codex-app", "--context-delivery", "on", "--codex-context-dir", str(native)],
            input=json.dumps({"hook_event_name": name, "session_id": session, "cwd": str(project), **fields}),
            cwd=project, text=True, capture_output=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
        state = json.loads((native / "session.json").read_bytes())
        assert state["session_id"] == session
        return state["context_epoch"]

    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    if native_session:
        monkeypatch.setenv("CODEX_THREAD_ID", session)
        epoch = hook("SessionStart", source="startup")
    flags = ("quota", "should-run", "--codex-app", "--goal-id", GOAL, "--agent-id", AGENT,
             "--turn-instance-id", TURN, "--complete-turn", "--decision-output-root", str(captures),
             *(("--context-delivery", "on", "--codex-context-dir", str(native)) if native_context
               else ("--context-delivery", "auto")))
    guard = call(*flags)
    source = next(captures.rglob("decision.json")).resolve()
    original = source.read_bytes()
    digest = hashlib.sha256(original).hexdigest()
    if saved_projection:
        first = call("quota", "context-project", "--codex-app", "--context-delivery", "on",
            "--goal-id", GOAL, "--agent-id", AGENT, "--turn-instance-id", TURN,
            "--capture-file", str(source), "--capture-sha256", digest,
            "--codex-context-dir", str(native), "--initial-read")
        navigation = first["navigation"]
    else:
        navigation = guard
    assert navigation["schema_version"] == "context_capture_navigation_v1"

    def read(command=None, first_page=None):
        content, offset = [], 0
        page = first_page
        while command or page is not None:
            if page is None:
                words = shlex.split(command)
                page = call(*words[words.index("quota"):])
            assert page["offset"] == offset
            content.append(page["content"])
            offset += len(content[-1].encode())
            if page["eof"]:
                assert offset == page["total_bytes"]
                break
            command = page.get("next_command")
            assert command
            page = None
        return selected_context_values("".join(content))

    review_command = navigation["replan_review_command"]
    review_words = shlex.split(review_command)
    review_reference = json.loads(Path(review_words[review_words.index("--reference-file") + 1]).read_bytes())
    assert Path(review_reference["path"]).resolve() == source
    assert review_reference["sha256"] == digest
    review = read(review_command)
    if not saved_projection:
        first_words = shlex.split(navigation["first_read_command"])
        first = call(*first_words[first_words.index("quota"):])
    assert first["offset"] == 0
    if not saved_projection:
        assert first["eof"]
    decision = read(first_page=first)
    assert review["/effective_action"] == "autonomous_replan_required"
    identity = review["/heartbeat_receipt"]["settlement_identity"]
    assert identity == decision["/heartbeat_receipt"]["settlement_identity"]
    assert review["/goal_boundary"] == decision["/goal_boundary"]
    channel = "/interaction_contract/agent_channel"
    assert review[channel + "/required_reads"] == decision[channel + "/required_reads"]
    obligation = review["/autonomous_replan_obligation"]
    assert obligation["replan_context"]["core_goal"]["objective"] == "Deliver the independently accepted source outcome."
    assert obligation["replan_context"]["evidence"]
    assert obligation["obligation_id"] == identity["replan_obligation_id"]
    if native_session:
        assert hook("PostCompact") != epoch
        assert read(review_command) == review
    authoring = "/replan_action_packet/writeback_contract"
    cli = "/interaction_contract/cli_channel"
    assert cli + "/next_cli_actions" not in review
    assert cli + "/settlement_plan" not in decision
    ordinary_review = shlex.split(decision["/replan_action_packet/review"])
    staged = call(*ordinary_review[ordinary_review.index("quota"):])
    assert staged["stage"] == "review" and staged["obligation_id"] == identity["replan_obligation_id"]
    assert staged["core_goal"] == obligation["replan_context"]["core_goal"]
    writeback_words = shlex.split(staged["next"]["writeback"])
    writeback = call(*writeback_words[writeback_words.index("quota"):])
    assert writeback["stage"] == "writeback" and writeback["obligation_id"] == identity["replan_obligation_id"]
    assert writeback["writeback_contract"]["vision_authoring"]["fields"]
    assert writeback["writeback"]["settlement_plan"]["identity"] == identity
    binding = shlex.split(review[authoring + "/successor_binding_args"])
    assert binding == shlex.split(writeback["writeback_contract"]["successor_binding_args"])
    # The original file remains the sole source even when its staged view exists.
    try:
        source.write_bytes(original + b" ")
        rejected = call(*review_words[review_words.index("quota"):], expected_code=1)
        assert "digest mismatch" in rejected["reason"]
    finally:
        source.write_bytes(original)
    added = call("todo", "add", "--goal-id", GOAL, "--role", "agent", "--claimed-by", AGENT,
        "--text", "Independently verify the source artifact", "--task-class", "advancement_task",
        "--action-kind", "validate", "--target-key", "independent-source-artifact",
        "--replan-obligation-id", identity["replan_obligation_id"], *binding)
    assert added["settlement_identity"] == identity
    assert added["settlement_plan"]["identity"] == identity
    assert added["replan_transition"]["outcome"] == "new_runnable_successor"
    assert "replan_review_command" not in call(*flags)  # Original settlement is no longer review.
    assert "replan_review_command" not in _guard(call)  # Explicit inline delivery remains complete.
    assert not any(json.loads(row).get("classification") == "quota_slot_spent" for row in index.read_text().splitlines())


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("context_delivery", ["off", "on"])
def test_successor_cli_reads_policy_with_context_delivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
    context_delivery: str,
) -> None:
    tmp_path = tmp_path.resolve()
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    call, _, _ = _fixture(tmp_path, monkeypatch, provider)
    native, captures = tmp_path / "native", tmp_path / "captures"
    native.mkdir(mode=0o700)
    captures.mkdir(mode=0o700)
    try:
        presented = call(
            "quota", "should-run", "--codex-app", "--goal-id", GOAL,
            "--agent-id", AGENT, "--turn-instance-id", TURN,
            "--codex-app-current-rrule", "FREQ=MINUTELY;INTERVAL=3",
            "--context-delivery", context_delivery,
            "--codex-context-dir", str(native), "--decision-output-root", str(captures),
        )
        if context_delivery == "on":
            assert presented["schema_version"] == "context_capture_navigation_v1"
        # Observe the complete current policy after checking native transport.
        policy = read_cli_policy_payload(json.dumps(presented), returncode=0)
        assert policy["decision"] == "autonomous_replan_required"
        assert policy["heartbeat_receipt"]["turn_instance_id"] == TURN
        identity = policy["heartbeat_receipt"]["settlement_identity"]
        assert (identity["goal_id"], identity["agent_id"]) == (GOAL, AGENT)
        assert identity["binding_kind"] == "autonomous_replan"
        assert policy["execution_obligation"]["must_attempt_work"] is True
    finally:
        from loopx.control_plane.effect_runtime import restart_effect_runtime

        restart_effect_runtime(wait_for_exit=True)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_machine_default_stride_due_allows_evidence_linked_terminal_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    call, runtime, index = _fixture(tmp_path, monkeypatch, provider, future_monitor=False)
    assert "stride_strategy" not in json.loads((tmp_path / "registry.json").read_text())["goals"][0].get("execution_profile", {})
    owner = build_builtin_machine_configuration_registry()
    configuration = {"schema_version": "loopx_machine_configuration_v0", "namespaces": {
        "todo_replan_cadence": {"schema_version": "todo_replan_cadence_machine_defaults_v2",
            "count_unit": "completed_todos", "count": 1,
            "stride_strategy": "evidence_conditioned_v1"}}}
    preview = configure_machine_configuration(
        runtime_root=runtime, registry=owner, configuration=configuration)
    configure_machine_configuration(
        runtime_root=runtime, registry=owner, configuration=configuration,
        execute=True, expected_plan_revision=preview["plan_revision"])

    turn = "machine-default-terminal-review"
    guard = _guard(call, turn)
    obligation = guard["autonomous_replan_obligation"]
    assert "periodic_review_due" in {trigger["kind"] for trigger in obligation["triggers"]}
    control = guard["replan_action_packet"]["stride_control"]
    assert control["strategy_version"] == "evidence_conditioned_v1"
    captures = tmp_path / "stride-captures"
    captures.mkdir(mode=0o700)
    navigation = call("quota", "should-run", "--codex-app", "--goal-id", GOAL, "--agent-id", AGENT,
        "--turn-instance-id", turn, "--context-delivery", "on", "--complete-turn", "--decision-output-root", str(captures))
    assert navigation["schema_version"] == "context_capture_navigation_v1"
    assert "replan_review_command" not in navigation
    common = ("refresh-state", "--goal-id", GOAL, "--agent-id", AGENT,
        "--replan-obligation-id", obligation["obligation_id"], "--turn-instance-id", turn,
        "--classification", "source_review", "--delivery-batch-scale", "single_surface",
        "--delivery-outcome", "outcome_gap", "--progress-result-class", "blocked",
        "--progress-blocker-id", "fixture:unverified-source",
        "--progress-evidence-id", "fixture:provenance-refusal",
        "--no-global-sync", "--suppress-external-sinks")
    before = index.read_bytes()
    rejected = call(*common, expected_code=1)
    assert "stride_decision" in rejected["error"]
    assert index.read_bytes() == before

    decision = {
        "schema_version": "stride_decision_v2", "strategy_version": "evidence_conditioned_v1",
        "reference_context_id": control["reference_context_id"],
        "reference_obligation_id": obligation["obligation_id"],
        "basis": "uncertain_evidence", "action": "verify",
        "axes": {
            "work_slice": {"plan": "Complete the source provenance check.",
                "reason": "The pending decision needs one independent result."},
            "route_distance": {"plan": "Retain the current method while checking provenance.",
                "reason": "No result yet supports replacing the route."},
            "validation_scope": {"plan": "Check the source and a contrary case.",
                "reason": "Both outcomes distinguish a valid source from a false lead."}},
        "next_probe": {"scope": "One complete provenance verification.",
            "prediction": "Only checked source evidence supports continuation.",
            "adoption_criterion": "Adopt only after the checked source meets acceptance.",
            "cost_bound": "One source and its contrary case.",
            "minimum_evidence": "A checked source result and contrary-case result."}}
    packet = {"schema_version": "goal_vision_replan_contract_v0",
        "state": "vision_patch_proposed",
        "vision_patch": {"acceptance_summary": "The source outcome remains unproven."},
        "path_delta": {"schema_version": "goal_path_delta_v0", "outcome": "stop",
            "prior_assumption": "The source might support the next decision.",
            "observed_reality": "The source still lacks independent validation.",
            "retained": ["Original source acceptance"],
            "changed": ["Stop this route until the source is checked"],
            "evidence_refs": ["evidence:source-provenance-review"]},
        "stride_decision": decision}
    packet_path = tmp_path / "terminal-review.json"
    packet_path.write_text(json.dumps(packet))
    accepted = call(*common, "--agent-vision-json", str(packet_path))
    assert accepted["autonomous_replan_ack"]["semantic_delta"]["accepted"] is True
    assert json.loads(Path(accepted["json_path"]).read_text())["agent_vision"]["stride_decision"] == decision


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("objective", [
    "Deliver the independently accepted source outcome.",
    "Read /workspace/fixture/task.md and preserve the accepted quality target.",
])
def test_real_mcp_replan_delivers_objective_and_evidence_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, objective: str,
) -> None:
    _fixture(tmp_path, monkeypatch, provider, later_vision=True)
    state = tmp_path / "project/ACTIVE_GOAL_STATE.md"
    state.write_text(state.read_text().replace(
        "Deliver the independently accepted source outcome.", objective))
    control = GoalModeMCPControlPlane(
        GoalModeMCPConfig(server_name="synthetic-replan", runtime_profile="claude_code",
                          legacy_host_surface="claude_code"),
        lambda: {"goal_id": GOAL, "agent_id": AGENT, "registry": str(tmp_path / "registry.json")},
    )
    # Select the test checkout's real CLI, rather than an unrelated global install.
    control.command_prefix = lambda: [sys.executable, "-m", "loopx.cli"]
    packet = json.loads(control.should_run())
    assert packet["ok"] is True
    assert packet["decision"] == "autonomous_replan_required"
    guidance = " ".join(packet["replan_action_packet"]["planning_guidance"])
    assert "provisional agent-created candidate rules" in guidance
    assert "relax frozen acceptance" in guidance
    assert packet["autonomous_replan_obligation"]["required"] is True
    core = packet["autonomous_replan_obligation"]["replan_context"]["core_goal"]
    assert core["objective"] == objective
    assert core["objective_source"] == "active_state"
    assert packet["interaction_contract"]["cli_channel"]["spend_allowed_now"] is False


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("ack_at,gap_at,replan", [
    ("2026-08-01T00:30:00.000100Z", "2026-08-01T00:30:00.000900Z", True),
    ("2026-08-01T00:30:00.000100Z", "2026-08-01T00:30:00.000100Z", False),
    ("2026-08-01T08:30:00.000100+08:00", "2026-08-01T00:30:00.000100Z", False),
    ("2026-02-30T00:00:00Z", "2026-02-28T00:00:00Z", True),
])
def test_real_guard_uses_strict_microsecond_vision_ack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
    ack_at: str, gap_at: str, replan: bool,
) -> None:
    call, _, index = _fixture(tmp_path, monkeypatch, provider, later_vision=True)
    # Author only this disposable fixture's history, before its first guard.
    rows = [json.loads(line) for line in index.read_text().splitlines()]
    for row in rows:
        row["generated_at"] = ack_at if row.get("autonomous_replan_ack") else gap_at
    index.write_text("".join(json.dumps(row) + "\n" for row in rows))
    invalid_history = ack_at == "2026-02-30T00:00:00Z"
    guarded = _guard(call, expected_code=1 if invalid_history else 0)
    if invalid_history:
        # Rejecting the ACK exposes the invalid durable source to context
        # validation. Fail closed before committing any settlement receipt.
        assert guarded["should_run"] is False
        assert guarded["heartbeat_receipt"]["status"] == "write_failed"
        return
    assert guarded["goal_frontier_projection"]["acceptance_gaps"]
    assert bool(guarded.get("autonomous_replan_obligation")) is replan
    assert guarded["should_run"] is replan
    assert guarded["decision"] == ("autonomous_replan_required" if replan else "skip")


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("later_vision", [False, True])
def test_successor_guard_returns_original_settlement_not_repeated_planning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, later_vision: bool,
) -> None:
    call, runtime, index = _fixture(tmp_path, monkeypatch, provider, later_vision=later_vision)
    original = _guard(call)
    if later_vision:
        assert original["goal_frontier_projection"]["normalized_progress"]["agent_monitor_open_count"] == 1
        assert original["decision"] == "autonomous_replan_required"
        assert original["execution_obligation"]["must_attempt_work"] is True
        assert original["autonomous_replan_obligation"]["triggers"][0]["kind"] == "vision_acceptance_gap"
    core_goal = original["autonomous_replan_obligation"]["replan_context"]["core_goal"]
    assert core_goal["objective"] == "Deliver the independently accepted source outcome."
    assert core_goal["objective_source"] == "active_state"
    identity = original["heartbeat_receipt"]["settlement_identity"]
    assert identity["binding_kind"] == "autonomous_replan"
    obligation_id = identity["replan_obligation_id"]
    added = _add(call, obligation_id)
    assert added["replan_transition"]["recorded"] is True
    assert added["replan_transition"]["outcome"] == "new_runnable_successor"

    # This same-Turn read is a closeout query, not permission for the new work.
    reread = _guard(call)
    assert reread["heartbeat_receipt"]["settlement_identity"] == identity
    assert reread.get("selected_todo") is None
    assert reread["autonomous_replan_obligation"]["resolution_mode"] == "receipt_bound_replan_settlement"
    packet = reread["replan_action_packet"]
    assert packet["obligation_id"] == obligation_id
    assert packet["successor_todo_id"] == added["todo_id"]
    assert packet["writeback_contract"]["preferred_input"] == "canonical_successor_transition"
    cli = reread["interaction_contract"]["cli_channel"]
    assert [step["kind"] for step in cli["settlement_plan"]["ordered_steps"]] == [
        "validation", "durable_writeback", "quota_spend",
    ]
    actions = cli["next_cli_actions"]
    assert len(actions) == 2
    assert all("todo add" not in command for command in actions)
    assert all("--replan-obligation-id " + obligation_id in command for command in actions)
    assert all("--turn-instance-id " + TURN in command for command in actions)
    from loopx.control_plane.quota.turn_envelope import assert_complete_turn_decision

    def check_off_capture(name, expected_commands, expected_plan):
        directory = tmp_path / name
        envelope = call("quota", "should-run", "--codex-app", "--goal-id", GOAL,
            "--agent-id", AGENT, "--turn-instance-id", TURN, "--turn-envelope",
            "--context-delivery", "off", "--decision-output-dir", str(directory))
        complete = json.loads((directory / "decision.json").read_bytes())
        assert envelope["schema_version"] == "loopx_turn_envelope_v0"
        assert envelope["replan_action_packet"]["settlement_only"] is True
        assert envelope["writeback"]["next_cli_actions"] == expected_commands
        assert envelope["writeback"]["settlement_plan"] == expected_plan
        assert envelope["action_signature"]["matches"] is True
        assert envelope["action_signature"]["source_decision_hash"] == complete["contract_binding"]["content_hash"]
        assert complete["schema_version"] == "loopx_turn_envelope_v1"
        assert complete["heartbeat_receipt"]["settlement_identity"] == identity
        assert complete["interaction_contract"]["cli_channel"]["next_cli_actions"] == expected_commands
        assert complete["interaction_contract"]["cli_channel"]["settlement_plan"] == expected_plan
        assert_complete_turn_decision(complete)

    check_off_capture("replan-before-writeback", actions, cli["settlement_plan"])
    premature_spend = call(*shlex.split(actions[1])[1:], expected_code=1)
    assert premature_spend.get("appended") is not True
    assert not any(json.loads(line).get("classification") == "quota_slot_spent"
                   for line in index.read_text().splitlines())

    # Execute the returned commands unchanged; no invented progress fields,
    # successor completion or unverified spend admission are supplied by us.
    refreshed = call(*shlex.split(actions[0])[1:], "--no-global-sync", "--suppress-external-sinks")
    assert refreshed["settlement_progress"]["state"] == "spend_required"
    persisted = json.loads(Path(refreshed["json_path"]).read_text())
    assert persisted["autonomous_replan_ack"]["semantic_delta"]["obligation_id"] == obligation_id
    pending = _guard(call)
    assert pending["heartbeat_receipt"]["settlement_identity"] == identity
    assert pending["settlement_progress"]["state"] == "spend_required"
    assert pending["settlement_owed"]["effect_id"] == identity["effect_id"]
    pending_cli = pending["interaction_contract"]["cli_channel"]
    spend_actions = pending_cli["next_cli_actions"]
    assert spend_actions == [actions[1]]
    assert pending["settlement_owed"]["command"] == actions[1]
    pending_plan = pending_cli["settlement_plan"]
    assert pending_plan["identity"] == identity
    assert pending_plan["ordered_steps"] == [cli["settlement_plan"]["ordered_steps"][-1]]
    check_off_capture("replan-after-writeback", spend_actions, pending_plan)
    spent = call(*shlex.split(spend_actions[0])[1:])
    assert spent["settlement_progress"]["state"] == "settled"
    assert spent["appended"] is True
    assert call(*shlex.split(actions[1])[1:])["appended"] is False
    settled = _guard(call)
    assert settled["effective_action"] == "heartbeat_settled_skip"
    assert settled.get("selected_todo") is None
    hint = settled["scheduler_hint"]
    assert hint["action"] == "preserve_current_schedule"
    assert hint["app_automation"]["host_action"] == "none"
    assert "recommended_rrule" not in hint["app_automation"]
    assert "ack_hint" not in hint["app_automation"]
    rows = [json.loads(line) for line in index.read_text().splitlines()]
    assert sum(row.get("classification") == "quota_slot_spent" for row in rows) == 1
    todo = call("todo", "list", "--goal-id", GOAL, "--todo-id", added["todo_id"])["todo"]
    assert todo["status"] == "open"
    # A later display/priority head must not displace the accepted new route.
    competing = call("todo", "add", "--goal-id", GOAL, "--role", "agent",
        "--claimed-by", AGENT, "--text", "[P0] Validate the older independent route",
        "--task-class", "advancement_task", "--action-kind", "validate",
        "--operation-id", "competing-prior-route")
    fresh = _guard(call, "turn-independent-vision-review")
    assert fresh["effective_action"] != "heartbeat_settled_skip"
    assert fresh["scheduler_hint"]["action"] == "run_now"
    assert fresh["scheduler_hint"]["app_automation"]["recommended_interval_minutes"] == 3
    assert fresh["goal_frontier_projection"]["acceptance_gaps"]
    assert fresh["goal_frontier_projection"]["vision_continuation_audit"]["decision"] == "acceptance_gap_open"
    assert fresh["selected_todo"]["todo_id"] == added["todo_id"]
    assert fresh["agent_lane_next_action"]["todo_id"] == added["todo_id"]
    portfolio = fresh["action_portfolio"]
    assert portfolio["primary"]["todo_id"] == added["todo_id"]
    assert any(row["todo_id"] == competing["todo_id"] and row["selection_role"] == "alternative"
               for row in portfolio["suggested_actions"])


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("invalidation", ["wrong_owner", "deferred", "unclaimed"])
@pytest.mark.parametrize("later_vision", [False, True])
def test_invalidated_successor_cannot_rebind_or_close_original_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, invalidation: str, later_vision: bool,
) -> None:
    call, runtime, index = _fixture(tmp_path, monkeypatch, provider, later_vision=later_vision)
    original = _guard(call)
    identity = original["heartbeat_receipt"]["settlement_identity"]
    added = _add(call, identity["replan_obligation_id"])
    prior_guard = _guard(call)
    prior_actions = prior_guard["interaction_contract"]["cli_channel"]["next_cli_actions"]
    edit = {"wrong_owner": ["--claimed-by", "different-agent"],
            "deferred": ["--status", "deferred", "--resume-when", "resume_at:2099-01-01T00:00:00Z"],
            "unclaimed": ["--clear-claim"]}[invalidation]
    call("todo", "update", "--goal-id", GOAL, "--agent-id", AGENT,
         "--todo-id", added["todo_id"], *edit)
    # Losing the successor invalidates its settlement proof. The same Turn
    # can still review the original obligation from current canonical facts.
    guarded = _guard(call)
    assert guarded["heartbeat_receipt"]["settlement_identity"] == identity
    assert guarded["heartbeat_receipt"]["event_id"] == prior_guard["heartbeat_receipt"]["event_id"]
    obligation = guarded["autonomous_replan_obligation"]
    assert guarded["effective_action"] == "autonomous_replan_required"
    assert obligation["selection_binding"] == "heartbeat_receipt"
    assert obligation["obligation_id"] == identity["replan_obligation_id"]
    assert obligation["replan_context"]["obligation_id"] == identity["replan_obligation_id"]
    assert guarded.get("selected_todo") is None
    assert (guarded.get("autonomous_replan_obligation") or {}).get("resolution_mode") != "receipt_bound_replan_settlement"
    assert (guarded.get("replan_action_packet") or {}).get("settlement_only") is not True
    denied = call(*shlex.split(prior_actions[0])[1:], "--no-global-sync",
                  "--suppress-external-sinks", expected_code=1)
    assert denied.get("appended") is not True
    denied_spend = call(*shlex.split(prior_actions[1])[1:], expected_code=1)
    assert denied_spend.get("appended") is not True
    rows = [json.loads(line) for line in index.read_text().splitlines()]
    assert not any(row.get("classification") == "quota_slot_spent" for row in rows)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_later_vision_replan_can_close_covered_frontier_and_settle_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    call, _, index = _fixture(tmp_path, monkeypatch, provider, later_vision=True, future_monitor=False)
    state = tmp_path / "project" / "ACTIVE_GOAL_STATE.md"
    # Terminal convergence requires explicit complete sources, never absence.
    state.write_text(state.read_text() + "\n## User Todo / Owner Review Reading Queue\n")
    covered = call("todo", "add", "--goal-id", GOAL, "--role", "agent", "--claimed-by", AGENT,
        "--text", "Observe the now covered source", "--task-class", "continuous_monitor",
        "--action-kind", "monitor", "--target-key", "covered-source", "--cadence", "30m",
        "--next-due-at", "2099-01-01T00:00:00Z", "--expires-at", "2099-01-02T00:00:00Z")
    call("todo", "complete", "--goal-id", GOAL, "--todo-id", covered["todo_id"],
        "--agent-id", AGENT, "--no-follow-up", "--evidence", "Declared source coverage is complete.")
    guard = _guard(call)
    identity = guard["heartbeat_receipt"]["settlement_identity"]
    assert identity["binding_kind"] == "autonomous_replan"
    assert guard["autonomous_replan_obligation"]["triggers"][0]["kind"] == "vision_acceptance_gap"
    # A real empty-frontier CLI wake still receives the scoped next-step review.
    assert any("An empty Todo queue is not a scope limit" in item
               for item in guard["replan_action_packet"]["planning_guidance"])
    # The real provider/CLI entry delivers objective and evidence advice while
    # retaining the same independently covered frontier and single-debit path.
    guidance = " ".join(guard["replan_action_packet"]["planning_guidance"])
    assert "provisional agent-created candidate rules" in guidance
    assert "relax frozen acceptance" in guidance
    binding = ("--goal-id", GOAL, "--agent-id", AGENT,
               "--turn-instance-id", TURN, "--replan-obligation-id", identity["replan_obligation_id"])
    vision = tmp_path / "covered-vision.json"
    vision.write_text(json.dumps({
        "schema_version": "goal_vision_replan_contract_v0", "state": "no_followup",
        "vision_patch": {"acceptance_summary": "The independently accepted source outcome is covered."},
        "path_delta": {"schema_version": "goal_path_delta_v0", "outcome": "stop",
            "prior_assumption": "The source frontier still needs another bounded probe.",
            "observed_reality": "The declared coverage is complete and no successor remains.",
            "retained": ["the independently accepted source contract"],
            "evidence_refs": ["evidence:source-coverage"]},
    }))
    refreshed = call("refresh-state", *binding,
        "--classification", "covered_frontier_closeout", "--delivery-batch-scale", "implementation",
        "--delivery-outcome", "outcome_progress", "--agent-vision-json", str(vision),
        "--progress-result-class", "no_followup", "--progress-coverage-scope-id", "source-coverage",
        "--progress-coverage-complete", "--progress-evidence-id", "evidence:source-coverage",
        "--no-global-sync", "--suppress-external-sinks")
    assert refreshed["settlement_progress"]["state"] == "spend_required"
    pending = _guard(call)
    assert pending["heartbeat_receipt"]["settlement_identity"] == identity
    assert pending["settlement_progress"]["state"] == "spend_required"
    assert pending.get("selected_todo") is None
    assert pending["should_run"] is True
    assert pending["normal_delivery_allowed"] is False
    cli = pending["interaction_contract"]["cli_channel"]
    plan = cli["settlement_plan"]
    assert plan["identity"] == identity
    assert [step["kind"] for step in plan["ordered_steps"]] == ["quota_spend"]
    command = pending["settlement_owed"]["command"]
    assert cli["next_cli_actions"] == [command]
    spent = call(*shlex.split(command)[1:])
    assert spent["appended"] is True
    assert spent["settlement_progress"]["state"] == "settled"
    assert call("quota", "spend-slot", *binding, "--slots", "1", "--source", "heartbeat", "--execute")["appended"] is False
    terminal = _guard(call, "turn-after-covered-vision-closeout")
    assert terminal["ok"] is True
    assert terminal["effective_action"] == "terminal_no_followup"
    assert terminal["should_run"] is False
    assert terminal.get("autonomous_replan_obligation") is None
    # A terminal execution frontier does not silently declare the Goal achieved.
    assert json.loads((tmp_path / "registry.json").read_text())["goals"][0]["status"] == "active"
    rows = [json.loads(line) for line in index.read_text().splitlines()]
    assert sum(row.get("classification") == "quota_slot_spent" for row in rows) == 1
