"""Public Turn hooks follow the original canonical writeback and terminal source."""

from __future__ import annotations

import contextlib
import io
import json
from dataclasses import replace
from pathlib import Path

import pytest

from loopx.capabilities.periodic_report.pending_intent import (
    pending_periodic_report_intents,
)
from loopx.capabilities.periodic_report.post_writeback_hook import (
    evaluate_periodic_report_trigger_evaluation_intent,
)
from loopx.cli import main as cli_main
from loopx.cli_commands import project_lifecycle_refresh_state, turn_post_writeback
from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)
from loopx.control_plane.todos.active_state_todo_parser import parse_active_state_todos
from tests.control_plane.canonical_authority_fixture import (
    initialize_canonical_authority,
    isolate_sqlite_runtime,
)
from tests.test_loopx_turn_driver import (
    _completion_host_and_validation_scripts,
    _turn_run_once_completion_argv,
    _write_live_fixture,
)


GOAL_ID = "loopx-turn-fixture"
AGENT_ID = "codex-fixture"


def _run_cli(args: list[str]) -> dict[str, object]:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exit_code = cli_main(args)
    payload = json.loads(output.getvalue())
    assert exit_code == 0, payload
    return payload


def _promoted_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, provider: str,
) -> tuple[Path, Path, Path]:
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_live_fixture(
        tmp_path, todo_metadata_extra="no_followup=true"
    )
    state = project / ".codex" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
    todos = parse_active_state_todos(state.read_text(encoding="utf-8"), item_limit=None)
    projection = build_todo_runtime_shadow_projection(
        goal_id=GOAL_ID,
        handoff_mode="soft_claim",
        todos=todos["agent_todos"]["items"],
    )
    initialize_canonical_authority(
        runtime, GOAL_ID, projection, state_path=state, provider=provider
    )
    return project, runtime, registry


def _accepted_closed_vision_and_successor(
    project: Path, runtime: Path, registry: Path,
) -> None:
    """Public native writes satisfy the required semantic replan before Turn."""

    base = [
        "--registry", str(registry), "--runtime-root", str(runtime),
        "--format", "json", "refresh-state", "--goal-id", GOAL_ID,
        "--delivery-workspace-path", str(project),
        "--agent-id", AGENT_ID, "--delivery-batch-scale", "implementation",
        "--delivery-outcome", "outcome_progress", "--no-global-sync",
        "--suppress-external-sinks",
    ]
    closed = _run_cli([
        *base, "--classification", "fixture_closed_vision",
        "--vision-state", "vision_closed",
        "--vision-summary", "The bounded fixture stage passed validation.",
        "--vision-acceptance", "The local acceptance check passed.",
    ])
    assert closed["appended"] is True
    assert closed["vision_checkpoint"]["satisfied"] is True
    assert any(
        item["kind"] == "material_delivery_outcome"
        and item["delivery_outcome"] == "outcome_progress"
        for item in closed["vision_checkpoint"]["triggers"]
    )

    successor_vision = {
        "schema_version": "goal_vision_replan_contract_v0",
        "state": "active",
        "vision_patch": {
            "vision_summary": "Finish the remaining local fixture Todo.",
            "acceptance_summary": "Validate the Todo before terminal closeout.",
        },
        "path_delta": {
            "schema_version": "goal_path_delta_v0",
            "outcome": "replan",
            "prior_assumption": "The fixture stage still needed validation.",
            "observed_reality": "That stage passed; the final Todo remains.",
            "evidence_refs": ["fixture:accepted-closed-vision"],
            "changed": ["Finish the remaining fixture Todo."],
        },
    }
    vision_path = project / "successor-vision.json"
    vision_path.write_text(json.dumps(successor_vision), encoding="utf-8")
    successor = _run_cli([
        *base, "--classification", "fixture_successor_vision",
        "--autonomous-replan-recorded", "--agent-vision-json", str(vision_path),
    ])
    assert successor["appended"] is True
    ack = successor["autonomous_replan_ack"]
    assert ack["recorded"] is True
    assert ack["semantic_delta"]["accepted"] is True
    assert "fresh_vision_path_outcome" in ack["semantic_delta"]["outcomes"]


def _enable_weekly_report(registry: Path) -> None:
    value = json.loads(registry.read_text(encoding="utf-8"))
    value["goals"][0]["control_plane"] = {
        "periodic_report": {
            "enabled": True,
            "profile_preset": "weekly",
            "route_ref": "project-room",
        }
    }
    registry.write_text(json.dumps(value), encoding="utf-8")


def _completion_args(project: Path, runtime: Path, registry: Path) -> tuple[list[str], Path]:
    workspace = project / "isolated-host-workspace"
    workspace.mkdir()
    host_script, validator_script = _completion_host_and_validation_scripts()
    host_script = host_script.replace(
        'pathlib.Path("completion-artifact.txt").write_text("completed", encoding="utf-8")',
        'pathlib.Path("completion-artifact.txt").write_text("completed", encoding="utf-8")\n'
        'counter = pathlib.Path("host-count.txt")\n'
        'counter.write_text(str(int(counter.read_text()) + 1 if counter.exists() else 1))',
    )
    return _turn_run_once_completion_argv(
        workspace, runtime, registry, host_script, validator_script,
    ), workspace


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("enabled", [False, True], ids=["off", "enabled"])
def test_canonical_turn_does_not_reclose_superseded_stage_on_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, enabled: bool,
) -> None:
    project, runtime, registry = _promoted_fixture(
        tmp_path, monkeypatch, provider=provider
    )
    _accepted_closed_vision_and_successor(project, runtime, registry)
    if enabled:
        _enable_weekly_report(registry)
    else:
        source_reads = 0
        real_readback = turn_post_writeback.read_heartbeat_settlement

        def count_optional_source_read(*args: object, **kwargs: object) -> object:
            nonlocal source_reads
            source_reads += 1
            return real_readback(*args, **kwargs)

        monkeypatch.setattr(
            turn_post_writeback, "read_heartbeat_settlement",
            count_optional_source_read,
        )
    args, workspace = _completion_args(project, runtime, registry)
    first = _run_cli(args)
    assert first["status"] == "committed"
    assert first["todo_completion"]["continuation"] == "no_followup"
    assert first["quota_slot_spend_count"] == 1
    sidecar_dir = runtime / "goals" / GOAL_ID / "post_writeback_hooks"

    if enabled:
        refresh = first["post_writeback_hooks"]
        assert refresh["intent_count"] == 0
        terminal = first["terminal_post_writeback_hooks"]
        assert terminal["intent_count"] == 0
        assert terminal["failures"] == []
        # The accepted closed Vision has been superseded by an ACTIVE successor.
        # Finishing this Todo cannot close that older stage a second time.
        assert terminal["intents"] == []
        assert pending_periodic_report_intents(
            registry_path=registry, runtime_root=runtime,
            goal_id=GOAL_ID, agent_id=AGENT_ID,
        ) == []
        original_sidecars = {p.name: p.read_bytes() for p in sidecar_dir.glob("*.json")}
        assert len(original_sidecars) == 2
    else:
        assert "post_writeback_hooks" not in first
        assert "terminal_post_writeback_hooks" not in first
        assert not list(sidecar_dir.glob("*.json"))
        assert source_reads == 0

    # Treat the first CLI response as lost; the original Turn key is authoritative.
    replay = _run_cli([
        *args[:-1], "--resume-turn-key", first["resume_turn_key"], "--execute",
    ])
    assert replay["replayed"] is True
    assert (workspace / "host-count.txt").read_text() == "1"
    rows = [
        json.loads(line)
        for line in (
            runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    assert sum(row.get("classification") == "fixture_completion" for row in rows) == 1
    assert sum(row.get("classification") == "quota_slot_spent" for row in rows) == 1
    if enabled:
        assert replay["terminal_post_writeback_hooks"]["intents"] == []
        assert {
            p.name: p.read_bytes() for p in sidecar_dir.glob("*.json")
        } == original_sidecars
    else:
        assert not list(sidecar_dir.glob("*.json"))


def test_terminal_hook_retries_original_source_after_later_canonical_todo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, runtime, registry = _promoted_fixture(
        tmp_path, monkeypatch, provider="file"
    )
    _accepted_closed_vision_and_successor(project, runtime, registry)
    _enable_weekly_report(registry)
    args, workspace = _completion_args(project, runtime, registry)
    real_dispatch = turn_post_writeback.dispatch_committed_cli_post_writeback_hooks
    interrupted = False

    def fail_one_terminal_response(**kwargs: object) -> dict[str, object]:
        nonlocal interrupted
        if kwargs["event_kind"] == "todo_complete" and not interrupted:
            interrupted = True
            raise OSError("synthetic optional response loss")
        return real_dispatch(**kwargs)

    monkeypatch.setattr(
        turn_post_writeback,
        "dispatch_committed_cli_post_writeback_hooks",
        fail_one_terminal_response,
    )
    first = _run_cli(args)
    assert first["status"] == "committed"
    assert first["todo_completion"]["continuation"] == "no_followup"
    assert interrupted is True
    assert first["terminal_post_writeback_hooks"]["failures"]
    assert pending_periodic_report_intents(
        registry_path=registry, runtime_root=runtime,
        goal_id=GOAL_ID, agent_id=AGENT_ID,
    ) == []
    monkeypatch.setattr(
        turn_post_writeback,
        "dispatch_committed_cli_post_writeback_hooks",
        real_dispatch,
    )

    added = _run_cli([
        "--registry", str(registry), "--runtime-root", str(runtime),
        "--format", "json", "todo", "add", "--goal-id", GOAL_ID,
        "--role", "agent", "--claimed-by", AGENT_ID,
        "--text", "A later fixture Todo has a different authority revision.",
    ])
    assert added["changed"] is True

    replay = _run_cli([
        *args[:-1], "--resume-turn-key", first["resume_turn_key"], "--execute",
    ])
    assert replay["replayed"] is True
    terminal = replay["terminal_post_writeback_hooks"]
    assert terminal["intent_count"] == 0
    assert terminal["failures"] == []
    assert terminal["intents"] == []
    assert pending_periodic_report_intents(
        registry_path=registry, runtime_root=runtime,
        goal_id=GOAL_ID, agent_id=AGENT_ID,
    ) == []
    assert (workspace / "host-count.txt").read_text() == "1"
    rows = [
        json.loads(line)
        for line in (
            runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    assert sum(row.get("classification") == "fixture_completion" for row in rows) == 1
    assert sum(row.get("classification") == "quota_slot_spent" for row in rows) == 1


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_ordinary_canonical_turn_without_stage_is_not_reportable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    project, runtime, registry = _promoted_fixture(
        tmp_path, monkeypatch, provider=provider
    )
    _enable_weekly_report(registry)
    workspace = project / "isolated-host-workspace"
    workspace.mkdir()
    host_script = """
import json, pathlib, sys
request = json.load(sys.stdin)
pathlib.Path("progress-artifact.txt").write_text("validated")
json.dump({
    "schema_version": "loopx_turn_result_v0",
    "turn_key": request["turn_key"],
    "result_kind": "validated_progress",
    "completed_phases": ["host_execute", "typed_result"],
    "classification": "fixture_ordinary_progress",
    "recommended_action": "Continue the bounded fixture.",
    "next_action": "Complete the fixture Todo after validation.",
    "delivery_batch_scale": "implementation",
    "delivery_outcome": "outcome_progress",
    "vision_unchanged_reason": "The fixture route is unchanged.",
    "summary": "The fixture advanced without a stage boundary."
}, sys.stdout)
"""
    validator_script = """
import pathlib, sys
raise SystemExit(0 if pathlib.Path("progress-artifact.txt").read_text() == "validated" else 7)
"""
    args = _turn_run_once_completion_argv(
        workspace, runtime, registry, host_script, validator_script,
    )
    first = _run_cli(args)
    assert first["status"] == "committed"
    assert first["post_writeback_hooks"]["intent_count"] == 0
    assert first["post_writeback_hooks"]["failures"] == []
    assert "terminal_post_writeback_hooks" not in first
    sidecars = list((runtime / "goals" / GOAL_ID / "post_writeback_hooks").glob("*.json"))
    assert len(sidecars) == 1
    receipt = json.loads(sidecars[0].read_text(encoding="utf-8"))
    assert receipt["status"] == "not_applicable"
    assert receipt["intent"] is None
    assert pending_periodic_report_intents(
        registry_path=registry, runtime_root=runtime,
        goal_id=GOAL_ID, agent_id=AGENT_ID,
    ) == []

    replay = _run_cli([
        *args[:-1], "--resume-turn-key", first["resume_turn_key"], "--execute",
    ])
    assert replay["replayed"] is True
    assert replay["post_writeback_hooks"]["intent_count"] == 0
    assert len(list(sidecars[0].parent.glob("*.json"))) == 1


@pytest.mark.parametrize("malformed_source", [{}, "bad"], ids=["empty", "scalar"])
def test_malformed_optional_todo_source_recovers_on_exact_turn_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, malformed_source: object,
) -> None:
    project, runtime, registry = _promoted_fixture(
        tmp_path, monkeypatch, provider="file"
    )
    _accepted_closed_vision_and_successor(project, runtime, registry)
    _enable_weekly_report(registry)
    args, workspace = _completion_args(project, runtime, registry)
    real_readback = turn_post_writeback.read_heartbeat_settlement

    def malformed_optional_readback(*args: object, **kwargs: object) -> object:
        readback = real_readback(*args, **kwargs)
        assert readback is not None and readback.writeback_run is not None
        return replace(
            readback,
            writeback_run={
                **readback.writeback_run,
                "todo_source": malformed_source,
            },
        )

    monkeypatch.setattr(
        turn_post_writeback, "read_heartbeat_settlement",
        malformed_optional_readback,
    )
    first = _run_cli(args)
    assert first["status"] == "committed"
    assert first["todo_completion"]["continuation"] == "no_followup"
    assert first["quota_slot_spend_count"] == 1
    assert first["post_writeback_hooks"]["failures"][0]["error_code"] == (
        "source_projection_failed"
    )
    assert not first.get("terminal_post_writeback_hooks", {}).get("intents")
    assert pending_periodic_report_intents(
        registry_path=registry, runtime_root=runtime,
        goal_id=GOAL_ID, agent_id=AGENT_ID,
    ) == []

    monkeypatch.setattr(
        turn_post_writeback, "read_heartbeat_settlement", real_readback,
    )
    replay = _run_cli([
        *args[:-1], "--resume-turn-key", first["resume_turn_key"], "--execute",
    ])
    assert replay["replayed"] is True
    assert replay["terminal_post_writeback_hooks"]["intent_count"] == 0
    assert replay["terminal_post_writeback_hooks"]["failures"] == []
    assert (workspace / "host-count.txt").read_text() == "1"
    rows = [
        json.loads(line)
        for line in (
            runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    assert sum(row.get("classification") == "fixture_completion" for row in rows) == 1
    assert sum(row.get("classification") == "quota_slot_spent" for row in rows) == 1


def _public_closed_current_vision_args(
    project: Path, runtime: Path, registry: Path,
) -> tuple[list[str], list[str], list[str], list[str]]:
    turn_id = "public-terminal-stage-1"
    base = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json"]
    binding = [
        "--agent-id", AGENT_ID, "--todo-id", "todo_fixture0001",
        "--turn-instance-id", turn_id,
    ]
    guard = _run_cli([
        *base, "quota", "should-run", "--codex-app", "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID, "--turn-instance-id", turn_id,
        "--scan-path", str(project),
    ])
    assert guard["decision"] == "run"
    effect_id = guard["heartbeat_receipt"]["settlement_identity"]["effect_id"]

    completion_args = [
        *base, "todo", "complete", "--goal-id", GOAL_ID, *binding,
        "--claimed-by", AGENT_ID, "--evidence", "Synthetic validated completion",
    ]
    completed = _run_cli(completion_args)
    assert completed["changed"] is True
    assert completed["post_writeback_hooks"]["intent_count"] == 0

    refresh_args = [
        *base, "refresh-state", "--goal-id", GOAL_ID,
        "--delivery-workspace-path", str(project),
        "--classification", "fixture_current_vision_closed",
        "--delivery-batch-scale", "implementation",
        "--delivery-outcome", "outcome_progress", *binding,
        "--completion-todo-id", "todo_fixture0001",
        "--completion-turn-key", effect_id,
        "--vision-state", "vision_closed",
        "--vision-summary", "The final fixture stage passed validation.",
        "--vision-acceptance", "The final fixture acceptance is verified.",
        "--no-global-sync", "--suppress-external-sinks",
    ]
    return base, binding, completion_args, refresh_args


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_public_same_turn_closed_current_vision_records_one_pending_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    project, runtime, registry = _promoted_fixture(
        tmp_path, monkeypatch, provider=provider
    )
    _enable_weekly_report(registry)
    base, binding, completion_args, refresh_args = _public_closed_current_vision_args(
        project, runtime, registry
    )
    refreshed = _run_cli(refresh_args)
    assert refreshed["appended"] is True
    assert refreshed["vision_checkpoint"]["satisfied"] is True
    assert refreshed["post_writeback_hooks"]["intent_count"] == 1
    intent = refreshed["post_writeback_hooks"]["intents"][0]
    assert intent["intent_kind"] == "periodic_report.trigger_evaluation"
    assert intent["requested_write_scope"] == []
    assert intent["payload"]["generation_authorized"] is False
    assert intent["payload"]["external_delivery_authorized"] is False
    stage = intent["payload"]["stage_completion"]
    assert stage["transition"] == "goal_terminal"
    assert stage["acceptance"] == "validated"
    decision = evaluate_periodic_report_trigger_evaluation_intent(intent)
    assert decision["eligible"] is True
    assert decision["selected_trigger_kind"] == "bounded_segment_milestone"

    spent = _run_cli([
        *base, "quota", "spend-slot", "--goal-id", GOAL_ID,
        "--slots", "1", "--source", "heartbeat", "--execute", *binding,
        "--scan-path", str(project),
    ])
    assert spent["appended"] is True
    terminal = _run_cli([*completion_args, "--no-follow-up"])
    assert terminal["post_writeback_hooks"]["intent_count"] == 1
    terminal_intent = terminal["post_writeback_hooks"]["intents"][0]
    assert terminal_intent["idempotency_key"] == intent["idempotency_key"]
    assert terminal_intent["payload"]["stage_completion"] == stage
    pending = pending_periodic_report_intents(
        registry_path=registry, runtime_root=runtime,
        goal_id=GOAL_ID, agent_id=AGENT_ID,
    )
    assert len(pending) == 1
    assert pending[0]["idempotency_key"] == intent["idempotency_key"]

    refresh_replay = _run_cli(refresh_args)
    assert refresh_replay["idempotent_replay"] is True
    assert refresh_replay["post_writeback_hooks"]["intents"] == [intent]
    terminal_replay = _run_cli([*completion_args, "--no-follow-up"])
    assert terminal_replay["idempotent_replay"] is True
    assert terminal_replay["post_writeback_hooks"]["intents"] == [terminal_intent]
    rows = [
        json.loads(line)
        for line in (
            runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    assert sum(row.get("classification") == "fixture_current_vision_closed" for row in rows) == 1
    assert sum(row.get("classification") == "quota_slot_spent" for row in rows) == 1


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_closed_current_vision_hook_recovers_original_source_after_later_todo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    project, runtime, registry = _promoted_fixture(
        tmp_path, monkeypatch, provider=provider
    )
    _enable_weekly_report(registry)
    base, binding, _, refresh_args = _public_closed_current_vision_args(
        project, runtime, registry
    )
    real_dispatch = (
        project_lifecycle_refresh_state.dispatch_committed_cli_post_writeback_hooks
    )
    sidecar_dir = runtime / "goals" / GOAL_ID / "post_writeback_hooks"
    prior_sidecars = {p.name: p.read_bytes() for p in sidecar_dir.glob("*.json")}

    def lose_optional_response(**kwargs: object) -> dict[str, object]:
        raise OSError("synthetic optional hook response loss")

    monkeypatch.setattr(
        project_lifecycle_refresh_state,
        "dispatch_committed_cli_post_writeback_hooks",
        lose_optional_response,
    )
    with pytest.raises(OSError, match="synthetic optional hook response loss"):
        _run_cli(refresh_args)
    assert {p.name: p.read_bytes() for p in sidecar_dir.glob("*.json")} == prior_sidecars

    spent = _run_cli([
        *base, "quota", "spend-slot", "--goal-id", GOAL_ID,
        "--slots", "1", "--source", "heartbeat", "--execute", *binding,
        "--scan-path", str(project),
    ])
    assert spent["appended"] is True
    added = _run_cli([
        *base, "todo", "add", "--goal-id", GOAL_ID,
        "--role", "agent", "--claimed-by", AGENT_ID,
        "--text", "A later fixture Todo remains open after the original stage.",
    ])
    assert added["changed"] is True
    current = _run_cli([
        *base, "todo", "list", "--goal-id", GOAL_ID,
        "--todo-id", added["todo_id"],
    ])
    assert current["todo"]["status"] == "open"
    monkeypatch.setattr(
        project_lifecycle_refresh_state,
        "dispatch_committed_cli_post_writeback_hooks",
        real_dispatch,
    )

    replay = _run_cli(refresh_args)
    assert replay["idempotent_replay"] is True
    assert replay["post_writeback_hooks"]["intent_count"] == 1
    assert replay["post_writeback_hooks"]["failures"] == []
    intent = replay["post_writeback_hooks"]["intents"][0]
    assert intent["payload"]["generation_authorized"] is False
    assert intent["payload"]["external_delivery_authorized"] is False
    assert intent["payload"]["stage_completion"]["transition"] == "goal_terminal"
    assert intent["payload"]["stage_completion"]["acceptance"] == "validated"
    decision = evaluate_periodic_report_trigger_evaluation_intent(intent)
    assert decision["eligible"] is True
    assert decision["selected_trigger_kind"] == "bounded_segment_milestone"
    pending = pending_periodic_report_intents(
        registry_path=registry, runtime_root=runtime,
        goal_id=GOAL_ID, agent_id=AGENT_ID,
    )
    assert len(pending) == 1
    assert pending[0]["idempotency_key"] == intent["idempotency_key"]
    rows = [
        json.loads(line)
        for line in (
            runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    assert sum(row.get("classification") == "fixture_current_vision_closed" for row in rows) == 1
    assert sum(row.get("classification") == "quota_slot_spent" for row in rows) == 1
