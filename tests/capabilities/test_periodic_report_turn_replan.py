"""Periodic report intents follow committed Turn replan writeback and replay."""

from __future__ import annotations

import contextlib
import importlib
import io
import json
from pathlib import Path
import sys

import pytest

from loopx.capabilities.periodic_report.pending_intent import pending_periodic_report_intents
from loopx.capabilities.periodic_report.post_writeback_hook import (
    evaluate_periodic_report_trigger_evaluation_intent,
)
from loopx.cli import main as cli_main
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.todos.active_state_todo_parser import parse_active_state_todos
from tests.control_plane.canonical_authority_fixture import (
    initialize_canonical_authority,
    isolate_sqlite_runtime,
)
from tests.test_loopx_turn_driver import _write_live_fixture


GOAL_ID = "loopx-turn-fixture"
AGENT_ID = "codex-fixture"


def _cli(args: list[str]) -> dict:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        status = cli_main(args)
    result = json.loads(output.getvalue())
    assert status == 0, result
    return result


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str):
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_live_fixture(
        tmp_path, todo_metadata_extra="no_followup=true"
    )
    state = project / ".codex/goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
    todos = parse_active_state_todos(state.read_text(encoding="utf-8"), item_limit=None)
    projection = build_todo_runtime_shadow_projection(
        goal_id=GOAL_ID, handoff_mode="soft_claim",
        todos=todos["agent_todos"]["items"],
    )
    initialize_canonical_authority(
        runtime, GOAL_ID, projection, state_path=state, provider=provider
    )
    config = json.loads(registry.read_text(encoding="utf-8"))
    config["goals"][0]["control_plane"] = {
        "periodic_report": {
            "enabled": True, "profile_preset": "weekly", "route_ref": "project-room",
        }
    }
    registry.write_text(json.dumps(config), encoding="utf-8")
    return project, runtime, registry


def _index(runtime: Path) -> list[dict]:
    path = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize(
    ("provider", "host_kind"),
    [("file", "generic-cli"), ("sqlite", "generic-cli"), ("file", "dsh")],
)
@pytest.mark.parametrize("mode", ["standard", "dispatch_failure", "response_loss", "off"])
def test_turn_milestone_intent_is_replay_stable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, host_kind: str, mode: str,
) -> None:
    project, runtime, registry = _fixture(tmp_path, monkeypatch, provider)
    base = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json"]
    closed = _cli([
        *base, "refresh-state", "--goal-id", GOAL_ID,
        "--delivery-workspace-path", str(project), "--agent-id", AGENT_ID,
        "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
        "--no-global-sync", "--suppress-external-sinks",
        "--classification", "fixture_material_closed_vision", "--vision-state", "vision_closed",
        "--vision-summary", "The bounded fixture stage passed validation.",
        "--vision-acceptance", "The local acceptance check passed.",
    ])
    assert closed["appended"] is True and closed["vision_checkpoint"]["satisfied"] is True

    optional_reads = 0
    if mode == "off":
        config = json.loads(registry.read_text(encoding="utf-8"))
        config["goals"][0]["control_plane"].pop("periodic_report")
        registry.write_text(json.dumps(config), encoding="utf-8")

    workspace = project / "isolated-host-workspace"
    workspace.mkdir()
    vision = {
        "schema_version": "goal_vision_replan_contract_v0", "state": "active",
        "vision_patch": {
            "vision_summary": "Continue the next bounded fixture check.",
            "acceptance_summary": "Validate the next fixture outcome independently.",
        },
        "path_delta": {
            "schema_version": "goal_path_delta_v0", "outcome": "replan",
            "prior_assumption": "The prior fixture stage remained open.",
            "observed_reality": "The prior stage passed its local validation.",
            "evidence_refs": ["fixture:validated-closed-stage"],
            "changed": ["Advance the next bounded fixture check."],
        },
    }
    candidate = {
        "result_kind": "replan_required",
        "classification": "fixture_replan_successor",
        "recommended_action": "Continue the validated successor stage.",
        "next_action": "Validate the next bounded fixture check.",
        "delivery_batch_scale": "implementation",
        "delivery_outcome": "outcome_progress",
        "path_delta_mode": "material_replan",
        "agent_vision_json": json.dumps(vision),
        "summary": "The prior stage closed and an active successor was authored.",
    }
    host_runner = tmp_path / ("turn_replan_dsh_runner.py" if host_kind == "dsh" else "turn_replan_host.py")
    if host_kind == "dsh":
        host_runner.write_text(
            "import json\nfrom pathlib import Path\n"
            f"counter = Path({str(workspace / 'host-count.txt')!r})\n"
            "def run_dsh_turn(*, prompt, session_id, workspace, session_root, provider, model, "
            "reasoning_effort, max_tokens, cordis, runtime_bin, request_timeout_seconds):\n"
            "    counter.write_text(str(int(counter.read_text()) + 1 if counter.exists() else 1))\n"
            "    Path(workspace, 'validated-artifact.txt').write_text('validated')\n"
            f"    return json.dumps({candidate!r})\n",
            encoding="utf-8",
        )
    else:
        host_runner.write_text(
            "import json, pathlib, sys\n"
            "request = json.load(sys.stdin)\n"
            "counter = pathlib.Path('host-count.txt')\n"
            "counter.write_text(str(int(counter.read_text()) + 1 if counter.exists() else 1))\n"
            "pathlib.Path('validated-artifact.txt').write_text('validated')\n"
            "json.dump({'schema_version':'loopx_turn_result_v0','turn_key':request['turn_key'],"
            "'completed_phases':['host_execute','typed_result'],**" + repr(candidate) + "},sys.stdout)\n",
            encoding="utf-8",
        )
    validator = 'import pathlib; raise SystemExit(0 if pathlib.Path("validated-artifact.txt").read_text() == "validated" else 7)'
    host_args = (
        ["--host", "dsh", "--dsh-runner", str(host_runner)]
        if host_kind == "dsh"
        else ["--host", "generic-cli", "--host-adapter-command-json", json.dumps([sys.executable, str(host_runner)])]
    )
    args = [
        *base, "turn", "run-once", *host_args, "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID, "--project", str(workspace),
        "--validation-command-json", json.dumps([sys.executable, "-c", validator]),
        "--execution-mode", "isolated-headless", "--scan-root", str(project),
        "--no-global-sync", "--execute",
    ]
    turn_module = None
    if mode in {"dispatch_failure", "response_loss", "off"}:
        turn_module = importlib.import_module("loopx.cli_commands.turn_post_writeback")
    if mode == "off":
        assert turn_module is not None
        original_read = turn_module.read_heartbeat_settlement

        def count_optional_read(*args, **kwargs):
            nonlocal optional_reads
            optional_reads += 1
            return original_read(*args, **kwargs)

        monkeypatch.setattr(turn_module, "read_heartbeat_settlement", count_optional_read)
    original_dispatch = None
    if mode in {"dispatch_failure", "response_loss"}:
        assert turn_module is not None
        original_dispatch = turn_module.dispatch_committed_cli_post_writeback_hooks

        def inject_optional_failure(**kwargs):
            if mode == "dispatch_failure":
                raise OSError("synthetic failure before optional dispatch")
            result = original_dispatch(**kwargs)
            if mode == "response_loss" and result["intent_count"]:
                raise OSError("synthetic optional response loss after sidecar commit")
            return result

        monkeypatch.setattr(
            turn_module, "dispatch_committed_cli_post_writeback_hooks", inject_optional_failure
        )

    first = _cli(args)
    assert first["status"] == "committed" and first["result_kind"] == "replan_required"
    assert first["effects"]["host_invoked"] and first["effects"]["state_written"]
    assert first["effects"]["quota_spent"]
    assert (workspace / "host-count.txt").read_text(encoding="utf-8") == "1"
    if mode == "standard":
        assert "post_writeback_hooks" in first
    rows = _index(runtime)
    original = next(row for row in rows if row.get("classification") == "fixture_replan_successor")
    durable_refresh = json.loads(Path(original["json_path"]).read_text(encoding="utf-8"))
    ack = durable_refresh["autonomous_replan_ack"]
    original_revision = durable_refresh["todo_source"]["provider_revision"]
    assert durable_refresh["agent_vision"]["state"] == "active"
    assert durable_refresh["agent_vision"]["agent_id"] == AGENT_ID
    assert ack["recorded"] is True and ack["semantic_delta"]["accepted"] is True
    assert "fresh_vision_path_outcome" in ack["semantic_delta"]["satisfying_outcomes"]
    assert "vision_successor_required" in ack["semantic_delta"]["trigger_kinds"]
    assert ack["semantic_delta"]["obligation_id"]

    sidecars = runtime / "goals" / GOAL_ID / "post_writeback_hooks"
    if mode == "off":
        assert "post_writeback_hooks" not in first
        assert not list(sidecars.glob("*.json")) and optional_reads == 0
    elif mode == "dispatch_failure":
        assert first["post_writeback_hooks"]["failures"]
        assert not list(sidecars.glob("*.json"))
    elif mode == "response_loss":
        assert first["post_writeback_hooks"]["failures"]
        assert len(list(sidecars.glob("*.json"))) == 1

    replay_args = [*args[:-1], "--resume-turn-key", first["resume_turn_key"], "--execute"]
    if mode in {"standard", "off"}:
        if mode == "off":
            assert turn_module is not None and original_dispatch is None
        replay = _cli(replay_args)
        assert replay["replayed"] is True
        assert (workspace / "host-count.txt").read_text(encoding="utf-8") == "1"
        retained_intent = (
            replay["post_writeback_hooks"]["intents"][0] if mode != "off" else None
        )

    later = _cli([
        *base, "todo", "add", "--goal-id", GOAL_ID, "--role", "agent",
        "--claimed-by", AGENT_ID, "--text", "A later bounded fixture task remains open.",
    ])
    assert later["changed"] is True
    current = _cli([*base, "todo", "list", "--goal-id", GOAL_ID, "--todo-id", later["todo_id"]])
    assert current["todo"]["status"] == "open"
    later_revision = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID)[
        "provider_revision"
    ]
    assert later_revision != original_revision

    if mode != "off":
        vision = project / "unaccepted-active-vision.json"
        vision.write_text(json.dumps({
            "schema_version": "goal_vision_replan_contract_v0", "state": "active",
            "vision_patch": {
                "vision_summary": "Continue a different bounded fixture check.",
                "acceptance_summary": "This edit has no accepted successor ACK.",
            },
        }), encoding="utf-8")
        edit = _cli([
            *base, "refresh-state", "--goal-id", GOAL_ID,
            "--delivery-workspace-path", str(project), "--agent-id", AGENT_ID,
            "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
            "--no-global-sync", "--suppress-external-sinks",
            "--classification", "fixture_unaccepted_later_vision_edit",
            "--agent-vision-json", str(vision),
        ])
        assert edit["appended"] is True and edit["post_writeback_hooks"]["intent_count"] == 0
        edited_refresh = json.loads(Path(edit["json_path"]).read_text(encoding="utf-8"))
        assert edited_refresh["agent_vision"]["state"] == "active"
        assert edited_refresh["agent_vision"]["agent_id"] == AGENT_ID
        assert "autonomous_replan_ack" not in edited_refresh

    if mode in {"dispatch_failure", "response_loss"}:
        assert turn_module is not None and original_dispatch is not None
        monkeypatch.setattr(
            turn_module, "dispatch_committed_cli_post_writeback_hooks", original_dispatch
        )
        if mode == "dispatch_failure":
            assert not list(sidecars.glob("*.json"))
        else:
            assert len(list(sidecars.glob("*.json"))) == 1
        replay = _cli(replay_args)
        assert replay["replayed"] is True
        assert (workspace / "host-count.txt").read_text(encoding="utf-8") == "1"
        retained_intent = replay["post_writeback_hooks"]["intents"][0]

    replay = _cli(replay_args)
    assert replay["replayed"] is True
    rows = _index(runtime)
    current_original = next(
        row for row in rows if row.get("classification") == "fixture_replan_successor"
    )
    assert current_original["todo_source"]["provider_revision"] == original_revision
    assert current_original["todo_source"]["provider_revision"] != later_revision
    assert sum(row.get("classification") == "fixture_replan_successor" for row in rows) == 1
    assert sum(row.get("classification") == "quota_slot_spent" for row in rows) == 1
    assert (workspace / "host-count.txt").read_text(encoding="utf-8") == "1"

    if mode == "off":
        assert "post_writeback_hooks" not in replay
        assert not list(sidecars.glob("*.json")) and optional_reads == 0
        assert pending_periodic_report_intents(
            registry_path=registry, runtime_root=runtime, goal_id=GOAL_ID, agent_id=AGENT_ID,
        ) == []
        return

    dispatch = replay["post_writeback_hooks"]
    assert dispatch["intent_count"] == 1 and dispatch["failures"] == []
    intent = dispatch["intents"][0]
    assert intent == retained_intent
    assert intent["requested_write_scope"] == []
    assert intent["payload"]["generation_authorized"] is False
    assert intent["payload"]["external_delivery_authorized"] is False
    stage = intent["payload"]["stage_completion"]
    assert stage["transition"] == "successor_frontier_settled"
    assert stage["acceptance"] == "validated"
    assert stage["frontier_identity"] == ack["semantic_delta"]["obligation_id"]
    decision = evaluate_periodic_report_trigger_evaluation_intent(intent)
    assert decision["eligible"] is True
    assert decision["selected_trigger_kind"] == "bounded_segment_milestone"
    pending = pending_periodic_report_intents(
        registry_path=registry, runtime_root=runtime, goal_id=GOAL_ID, agent_id=AGENT_ID,
    )
    assert len(pending) == 1 and pending[0]["idempotency_key"] == intent["idempotency_key"]
