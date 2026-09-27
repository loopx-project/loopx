"""Read-only Goal selection must constrain collection, not just displayed rows."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest
from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime

from loopx.cli import build_parser
from loopx.cli_commands.quota_context import prepare_quota_command_context
from loopx.control_plane.effect_runtime import restart_effect_runtime
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.projection_envelope_facts import seal_projection_envelope, source_fact
from loopx.control_plane.runtime.time import now_utc_iso
from loopx.control_plane.testing.canary_harness import run_json_cli_result, write_fixture_registry
from loopx.presentation.renderers.quota_markdown import render_quota_markdown
from loopx.quota import build_quota_plan


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    yield
    assert restart_effect_runtime()["status"] in {"stopped", "not_running"}


@pytest.fixture(params=["legacy", "file", "sqlite"])
def goals(tmp_path, request):
    project = tmp_path / "project"
    project.mkdir()
    state = project / "state.md"
    state.write_text("---\nstatus: active\nwaiting_on: codex\n---\n# Example\n\n## Agent Todo\n")
    registry, runtime = tmp_path / "registry.json", tmp_path / "runtime"
    write_fixture_registry(project=project, runtime_root=runtime, registry_path=registry,
                           goal_id="selected", domain="engineering", adapter_kind="generic_project_goal_v0",
                           state_file=str(state), registered_agents=["worker"])
    doc = json.loads(registry.read_text())
    other = deepcopy(doc["goals"][0])
    other.update(id="unrelated", state_file=str(project / "missing.md"))
    doc["goals"].append(other)
    registry.write_text(json.dumps(doc))
    if request.param != "legacy":
        projection = build_todo_runtime_shadow_projection(goal_id="selected", todos=[], handoff_mode="soft_claim")
        initialize_canonical_authority(runtime, "selected", projection, state_path=state, provider=request.param)
    return registry, runtime, project


@pytest.mark.parametrize("mode", ["status", "plan"])
def test_selected_goal_excludes_unrelated_broken_goal(goals, mode):
    registry, runtime, project = goals
    code, payload = run_json_cli_result("quota", mode, "--goal-id", "selected", "--scan-root", str(project),
                                        registry_path=registry, runtime_root=runtime)
    assert code == 0, payload
    assert payload["goal_filter"] == "selected"
    assert {row["goal_id"] for group in payload["groups"].values() for row in group} == {"selected"}
    assert payload["summary"]["registered_goals"] == 1
    assert not any(row.get("goal_id") == "unrelated" for row in payload["health_items"])
    assert not list((runtime / "goals" / "selected" / "runs").glob("*.json*"))
    assert payload["status_projection_envelope"]["coverage"]["scope"] == "goal"
    assert "Within Selected Goal" in render_quota_markdown(payload)

    _, global_payload = run_json_cli_result("quota", mode, "--scan-root", str(project),
                                            registry_path=registry, runtime_root=runtime)
    assert "goal_filter" not in global_payload
    assert {row["goal_id"] for group in global_payload["groups"].values() for row in group} == {"selected", "unrelated"}


def test_unknown_goal_does_not_become_a_successful_empty_or_global_plan(goals):
    registry, runtime, project = goals
    code, payload = run_json_cli_result("quota", "status", "--goal-id", "missing", "--scan-root", str(project),
                                        registry_path=registry, runtime_root=runtime)
    assert code == 1
    assert payload["status"] == "goal_not_found"
    assert payload["goal_filter"] == "missing"
    assert not any(payload["groups"].values())
    assert "connect or sync" in render_quota_markdown(payload)


@pytest.mark.parametrize("mode", ["status", "plan"])
@pytest.mark.parametrize("goal_id", [None, "selected"])
def test_scope_reaches_collector_and_cache_identity(tmp_path, mode, goal_id):
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"common_runtime_root": str(tmp_path / "runtime"), "goals": []}))
    argv = ["quota", mode, "--write-projection-cache"]
    if goal_id:
        argv.extend(["--goal-id", goal_id])
    args = build_parser().parse_args(argv)
    calls = []

    def collect(**kwargs):
        calls.append(kwargs["goal_id"])
        now = now_utc_iso()
        envelope = seal_projection_envelope(projection="status", observed_at=now,
            sources=[source_fact("registry", last_read_at=now)],
            coverage={"scope": "goal" if kwargs["goal_id"] else "registry", "expected_count": 1,
                      "included_count": 1, "omitted": [], "shown_count": 1, "available_count": 1})
        return {"ok": True, "goal_filter": kwargs["goal_id"], "projection_envelope": envelope}

    context = prepare_quota_command_context(args, registry_path=registry, runtime_root_arg=None,
        status_collector=collect, operator_inbox_urgency_projector_factory=lambda **kwargs: lambda **kw: {})
    assert calls == [goal_id]
    assert context.status_goal_id == goal_id
    args.write_projection_cache = False
    args.use_projection_cache = True
    cached = prepare_quota_command_context(args, registry_path=registry, runtime_root_arg=None,
        status_collector=collect, operator_inbox_urgency_projector_factory=lambda **kwargs: lambda **kw: {})
    assert calls == [goal_id]
    assert cached.status_payload["goal_filter"] == goal_id
    cached_plan = build_quota_plan(cached.status_payload)
    assert cached_plan["status_projection_envelope"]["served_from_cache"] is True
    assert cached_plan["status_projection_envelope"]["observed_at"] == context.status_payload["projection_envelope"]["observed_at"]
    # Switching scope must miss; the previous global/Goal cache cannot answer it.
    args.goal_id = None if goal_id else "selected"
    other = prepare_quota_command_context(args, registry_path=registry, runtime_root_arg=None,
        status_collector=collect, operator_inbox_urgency_projector_factory=lambda **kwargs: lambda **kw: {})
    assert calls == [goal_id, args.goal_id]
    assert other.cache_metadata["hit"] is False


def test_scoped_plan_keeps_global_health_and_upstream_observation_proof():
    now = now_utc_iso()
    envelope = seal_projection_envelope(projection="status", observed_at=now,
        sources=[source_fact("registry", last_read_at=now)],
        coverage={"scope": "goal", "expected_count": 1, "included_count": 1,
                  "omitted": [], "shown_count": 1, "available_count": 1})
    health = {"goal_id": "global", "source": "contract", "severity": "error",
              "recommended_action": "Repair the shared registry boundary"}
    status = {"ok": False, "goal_filter": "selected", "projection_envelope": envelope,
              "run_history": {"goals": [{"id": "selected", "registry_member": True,
                                          "quota": {"state": "eligible", "compute": 1}}]},
              "attention_queue": {"items": [health]}}
    result = build_quota_plan(status)
    assert result["ok"] is False
    assert result["health_items"] == [health]
    assert result["status_projection_envelope"] == envelope
    assert "Repair the shared registry boundary" in render_quota_markdown(result)


def test_observation_metadata_does_not_change_internal_execution_plan():
    result = build_quota_plan({"ok": True, "goal_filter": "missing",
                               "projection_envelope": {"served_from_cache": True}}, mode="should-run")
    assert result["ok"] is True
    assert "goal_filter" not in result
    assert "status" not in result
    assert "status_projection_envelope" not in result
