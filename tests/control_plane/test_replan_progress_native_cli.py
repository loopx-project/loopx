"""Typed progress must settle only the admitted replan through the real CLI."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.control_plane.coordination.canonical_snapshot import read_canonical_snapshot
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.effect_runtime import effect_runtime_result, restart_effect_runtime
from loopx.control_plane.todos.active_state_todo_parser import parse_active_state_todos
from loopx.control_plane.work_items.progress_observation import normalize_progress_observation

GOAL = "typed-progress-cli-fixture"
AGENT = "typed-progress-agent"
TODO = "todo_original_validation"
TURN = "turn-typed-progress-review"


def make_progress_cli_fixture(root: Path, provider: str, scenario: str) -> dict:
    """Create synthetic source/history for source and installed-package replay."""
    project, runtime = root / "project", root / "runtime"
    project.mkdir()
    state = project / "ACTIVE_GOAL_STATE.md"
    state.write_text(
        "---\nstatus: active\n---\n\n# Goal\n\n## Agent Todo\n\n"
        "- [ ] [P1] Validate the original artifact.\n"
        f"  <!-- loopx:todo todo_id={TODO} status=open task_class=advancement_task "
        f"claimed_by={AGENT} action_kind=validate validation_command=pytest -->\n"
    )
    baseline = {
        "schema_version": "typed_progress_observation_v0", "work_item_id": TODO,
        "surface_id": "surface-existing", "hypothesis_id": "hypothesis-existing",
        "probe_kind": "probe-existing", "result_class": "unchanged",
        "evidence_ids": ["evidence-existing"],
    }
    if scenario.startswith("terminal_"):
        baseline["coverage_scope_id"] = "scope-existing"
    index = runtime / "goals" / GOAL / "runs" / "index.jsonl"
    index.parent.mkdir(parents=True)
    source_json, source_md = index.parent / "source.json", index.parent / "source.md"
    source_json.write_text('{"fixture":"synthetic-typed-progress"}\n')
    source_md.write_text("# Synthetic prior source audit\n")
    rows = [{
        "classification": "state_refreshed", "agent_id": AGENT,
        "generated_at": f"2026-08-13T00:0{i}:00Z", "turn_instance_id": f"turn-prior-{i}",
        "progress_observation": normalize_progress_observation(baseline),
        "json_path": str(source_json), "markdown_path": str(source_md),
    } for i in range(2)]
    if scenario == "known_blocker":
        rows.insert(0, {
            **rows[0], "generated_at": "2026-08-12T23:59:00Z", "turn_instance_id": "turn-old-blocker",
            "progress_observation": normalize_progress_observation({
                **baseline, "result_class": "blocked", "blocker_id": "blocker-permission",
            }),
        })
    index.write_text("".join(json.dumps(row) + "\n" for row in rows))
    registry = root / "registry.json"
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{
        "id": GOAL, "status": "active", "repo": str(project), "state_file": state.name,
        "domain": "synthetic-typed-progress",
        "adapter": {"kind": "fixture_connected_delivery_v0", "status": "connected-delivery"},
        "quota": {"compute": 1.0, "window_hours": 24},
        "coordination": {"agent_model": "peer_v1", "registered_agents": [AGENT]},
    }]}))
    todos = parse_active_state_todos(state.read_text(), item_limit=None)["agent_todos"]["items"]
    initialize_canonical_authority(runtime, GOAL, build_todo_runtime_shadow_projection(
        goal_id=GOAL, todos=todos, handoff_mode="soft_claim", leases=[],
    ), state_path=state, provider=provider)
    observation = {**baseline, "evidence_ids": ["evidence-current"]}
    if scenario in {"advanced", "advanced_no_evidence"}:
        observation.update(result_class="advanced", probe_kind="probe-independent")
        if scenario == "advanced_no_evidence":
            observation.pop("evidence_ids")
    elif scenario == "evidence_only":
        observation["result_class"] = "advanced"
    elif scenario == "known_blocker":
        observation.update(work_item_id="todo_different_work", result_class="blocked", blocker_id="blocker-permission")
    elif scenario.startswith("terminal_"):
        observation.update(result_class="no_followup", coverage_scope_id="scope-independent")
    refresh = [
        "refresh-state", "--goal-id", GOAL, "--agent-id", AGENT, "--todo-id", TODO,
        "--turn-instance-id", TURN, "--classification", "bounded_replan_progress",
        "--delivery-batch-scale", "single_surface", "--delivery-outcome", "outcome_progress",
        "--no-global-sync", "--suppress-external-sinks",
    ]
    for field in ("result_class", "work_item_id", "surface_id", "hypothesis_id", "probe_kind", "blocker_id", "coverage_scope_id"):
        if field in observation:
            refresh.extend(["--progress-" + field.replace("_", "-"), observation[field]])
    if observation.get("evidence_ids"):
        refresh.extend(["--progress-evidence-id", "evidence-current"])
    if scenario == "terminal_stop":
        vision = root / "vision.json"
        vision.write_text(json.dumps({
            "goal_id": GOAL, "agent_id": AGENT, "state": "no_followup",
            "vision_patch": {"acceptance_summary": "The bounded source scope has no remaining follow-up."},
            "path_delta": {
                "outcome": "stop", "prior_assumption": "The source scope might contain follow-up work.",
                "observed_reality": "Independent coverage found no follow-up in this bounded scope.",
                "stopped": ["Repeat exploration of the covered source scope."],
                "evidence_refs": ["evidence-current"],
            },
        }))
        refresh.extend(["--agent-vision-json", str(vision)])
    else:
        refresh.extend(["--vision-unchanged-reason", "The original validation remains open."])
    return {"project": project, "runtime": runtime, "registry": registry, "state": state,
            "index": index, "observation": observation, "refresh": refresh}


def call_progress_cli(fixture: dict, *args: str, expected_code: int = 0) -> dict:
    result = subprocess.run([
        sys.executable, "-m", "loopx.cli", "--registry", str(fixture["registry"]),
        "--runtime-root", str(fixture["runtime"]), "--format", "json", *args,
    ], cwd=fixture["project"], env={**os.environ, "LOOPX_USAGE_PING": "0"},
        capture_output=True, text=True, timeout=60)
    assert result.returncode == expected_code, (result.stdout, result.stderr)
    return json.loads(result.stdout)


def _authority(fixture: dict) -> dict:
    result = read_canonical_snapshot(rpc=effect_runtime_result, runtime_root=str(fixture["runtime"]),
        goal_id=GOAL, include_leases=True, projection_readback=None, timeout=20)
    assert result["status"] == "loaded", result
    return result


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("scenario,expected_outcomes,reason_code", [
    ("advanced", ["new_probe_family"], None),
    ("evidence_only", [], None),
    ("known_blocker", [], "progress_observation_replayed"),
    ("advanced_no_evidence", [], None),
    ("terminal_inconsistent", [], "no_followup_vision_path_inconsistent"),
    ("terminal_stop", ["coverage_backed_no_followup"], None),
])
def test_typed_progress_cli_preserves_replan_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
    scenario: str, expected_outcomes: list[str], reason_code: str | None,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    fixture = make_progress_cli_fixture(tmp_path, provider, scenario)
    try:
        guard_args = ("quota", "should-run", "--codex-app", "--goal-id", GOAL,
                      "--agent-id", AGENT, "--turn-instance-id", TURN,
                      "--scan-path", str(fixture["project"]))
        initial = call_progress_cli(fixture, *guard_args)
        assert initial["decision"] == "autonomous_replan_required", initial
        bound = call_progress_cli(fixture, *guard_args, "--todo-id", TODO)
        obligation = bound["autonomous_replan_obligation"]
        assert [trigger["kind"] for trigger in obligation["triggers"]] == ["typed_progress_repeat"]
        assert bound["heartbeat_receipt"]["settlement_identity"]["todo_id"] == TODO
        original = {key: fixture[key].read_bytes() for key in ("registry", "state", "index")}
        authority = _authority(fixture)
        result = call_progress_cli(fixture, *fixture["refresh"],
            expected_code=0 if expected_outcomes else 1)
        if expected_outcomes:
            assert result["ok"] is True and result["appended"] is True
            ack = result["autonomous_replan_ack"]
            assert ack["recorded"] is True
            delta = ack["semantic_delta"]
            fingerprint = hashlib.sha256(json.dumps(
                {key: value for key, value in fixture["observation"].items() if key != "schema_version"},
                sort_keys=True, ensure_ascii=True, separators=(",", ":"),
            ).encode()).hexdigest()[:16]
            assert delta == {
                "schema_version": "replan_semantic_delta_v0", "accepted": True,
                "outcomes": expected_outcomes, "satisfying_outcomes": expected_outcomes,
                "required_any_of": ["new_surface", "new_hypothesis", "new_probe_family",
                    "new_runnable_successor", "coverage_backed_exploration_exhausted",
                    "new_concrete_blocker", "coverage_backed_no_followup"],
                "observation_fingerprint": fingerprint,
                "reason": "writeback changes an outcome accepted by this obligation source",
                "trigger_kinds": ["typed_progress_repeat"], "trigger_checkpoints": [],
                "obligation_id": obligation["obligation_id"],
            }
            persisted = json.loads(Path(result["json_path"]).read_text())
            assert persisted["autonomous_replan_ack"] == ack
            assert persisted["progress_observation"]["fingerprint"] == fingerprint
            compact = json.loads(fixture["index"].read_text().splitlines()[-1])
            assert compact["autonomous_replan_ack"]["semantic_delta"] == delta
            restart_effect_runtime(wait_for_exit=True)
            replay = call_progress_cli(fixture, *fixture["refresh"])
            assert replay["appended"] is False and replay["idempotent_replay"] is True
        else:
            assert result["ok"] is False and result["appended"] is False
            rejection = result["replan_transition"]
            assert rejection["obligation_id"] == obligation["obligation_id"]
            assert rejection["host_action"] == "write_typed_semantic_delta"
            assert rejection["reason_code"] == reason_code
            assert rejection["next_cli_actions"]
            restart_effect_runtime(wait_for_exit=True)
            assert call_progress_cli(fixture, *fixture["refresh"], expected_code=1) == result
            assert {key: fixture[key].read_bytes() for key in original} == original
        assert fixture["registry"].read_bytes() == original["registry"]
        displayed = parse_active_state_todos(fixture["state"].read_text(), item_limit=None)["agent_todos"]["items"]
        assert [(todo["todo_id"], todo["status"]) for todo in displayed] == [(TODO, "open")]
        assert _authority(fixture) == authority
        assert authority["todos"][0]["status"] == "open"
    finally:
        restart_effect_runtime(wait_for_exit=True)
