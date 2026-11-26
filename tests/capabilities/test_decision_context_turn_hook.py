"""Tests for opt-in Decision Context turn-start hook binding and isolation."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from loopx.capabilities.decision_context.turn_start_hook import (
    DECISION_CONTEXT_TURN_HOOK_ID,
    extend_turn_start_dispatch,
    read_decision_context_turn_context,
)


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "loopx.cli", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _setup_goal_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch | None = None,
    provider: str = "file",
) -> tuple[Path, Path, Path, Path]:
    from tests.control_plane.canonical_authority_fixture import (
        initialize_canonical_authority,
        isolate_sqlite_runtime,
    )
    from loopx.control_plane.coordination.runtime_shadow import (
        build_todo_runtime_shadow_projection,
    )
    from loopx.control_plane.todos.active_state_todo_parser import (
        parse_active_state_todos,
    )

    if provider == "sqlite" and monkeypatch is not None:
        isolate_sqlite_runtime(tmp_path, monkeypatch)

    repo = tmp_path / "repo"
    state_dir = repo / ".loopx" / "goals" / "ops-goal"
    state_dir.mkdir(parents=True)
    state_file = state_dir / "ACTIVE_GOAL_STATE.md"
    state_file.write_text(
        "# Goal State\n\n"
        "## Objective\n\nVerify Decision Context turn-start hook delivery.\n\n"
        "## Next Action\n\n- Verify bounded source freshness.\n\n"
        "## Agent Todo\n\n"
        "- [ ] [P1] Verify bounded source freshness.\n"
        "  <!-- loopx:todo todo_id=todo_ops_1 status=open claimed_by=operations "
        "task_class=advancement_task action_kind=validate -->\n\n"
        "## User Todo\n",
        encoding="utf-8",
    )
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    registry_path = repo / ".loopx" / "registry.json"
    goal = {
        "id": "ops-goal",
        "display_name": "Ops Goal",
        "domain": "project-goal-control-plane",
        "status": "active-read-only",
        "repo": str(repo),
        "state_file": ".loopx/goals/ops-goal/ACTIVE_GOAL_STATE.md",
        "adapter": {
            "kind": "read_only_project_map_v0",
            "status": "connected-read-only",
        },
        "coordination": {
            "registered_agents": ["operations", "reviewer"],
            "agent_model": "peer_v1",
        },
        "authority_sources": [],
        "quota": {
            "compute": 1.0,
            "window_hours": 24,
            "allowed_slots": 2,
        },
    }
    registry = {
        "schema_version": "0.1",
        "common_runtime_root": str(runtime_root),
        "goals": [goal],
    }
    registry_path.write_text(json.dumps(registry, indent=2), encoding="utf-8")
    fields = parse_active_state_todos(
        state_file.read_text(encoding="utf-8"), goal=goal, item_limit=None
    )
    projection = build_todo_runtime_shadow_projection(
        goal_id="ops-goal",
        todos=fields["agent_todos"]["items"]
        + fields.get("user_todos", {}).get("items", []),
        handoff_mode="soft_claim",
    )
    initialize_canonical_authority(
        runtime_root,
        "ops-goal",
        projection,
        state_path=state_file,
        provider=provider,
    )

    private_dir = tmp_path / "private-secrets"
    private_dir.mkdir(mode=0o700)
    authority_doc = private_dir / "SECRET_CONTEXT.md"
    authority_doc.write_text(
        "TOP_SECRET_BODY_DO_NOT_LEAK_12345\nUpdated priority fact.\n",
        encoding="utf-8",
    )
    profile_path = private_dir / "ops-profile.json"
    profile_payload = {
        "schema_version": "decision_context_profile_v0",
        "goal_id": "ops-goal",
        "enabled": True,
        "enabled_agents": ["operations"],
        "source_provider_bindings": [
            {
                "provider_id": "local-authorities",
                "adapter": "local-file",
                "config": {"max_bytes": 65536},
            }
        ],
        "sources": [
            {
                "source_id": "ops:primary-context",
                "provider_id": "local-authorities",
                "source_kind": "document",
                "priority": "p0",
                "evidence_level": "s1",
                "objectives": ["editorial_alignment"],
                "scan_mode": "incremental",
                "exact_read_policy": "always",
                "freshness_seconds": 86400,
                "private_locator": str(authority_doc),
                "max_changes": 1,
                "enabled": True,
            }
        ],
        "context_provider": None,
        "automation": {"automatic_capture": False, "fail_open": True},
    }
    profile_path.write_text(json.dumps(profile_payload, indent=2), encoding="utf-8")
    return registry_path, runtime_root, profile_path, authority_doc


def test_default_off_parity_when_unbound(tmp_path: Path) -> None:
    registry_path, runtime_root, _profile_path, _doc = _setup_goal_fixture(tmp_path)

    initial_dispatch: dict[str, object] = {"registered_count": 0, "invoked_count": 0}
    unchanged = extend_turn_start_dispatch(
        initial_dispatch,
        runtime_root=runtime_root,
        registry_path=registry_path,
        goal_id="ops-goal",
        agent_id="operations",
    )
    assert unchanged == initial_dispatch
    assert not (runtime_root / "decision-context").exists()

    inspect = _run_cli(
        "--registry",
        str(registry_path),
        "--runtime-root",
        str(runtime_root),
        "decision-context",
        "inspect-profile",
        "--goal-id",
        "ops-goal",
        "--agent-id",
        "operations",
        "--format",
        "json",
    )
    assert inspect.returncode == 0, inspect.stderr
    payload = json.loads(inspect.stdout)
    assert payload["status"] == "disabled"
    assert payload["profile_configured"] is False


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_bind_turn_hook_delivers_redacted_view_and_isolates_other_agents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    registry_path, runtime_root, profile_path, authority_doc = _setup_goal_fixture(
        tmp_path,
        monkeypatch=monkeypatch,
        provider=provider,
    )

    bind = _run_cli(
        "--registry",
        str(registry_path),
        "--runtime-root",
        str(runtime_root),
        "decision-context",
        "hook",
        "bind",
        "--goal-id",
        "ops-goal",
        "--agent-id",
        "operations",
        "--profile",
        str(profile_path),
        "--mode",
        "prepare_evidence",
        "--expected-revision",
        "none",
        "--operation-id",
        "bind-ops-1",
        "--execute",
        "--format",
        "json",
    )
    assert bind.returncode == 0, bind.stderr
    bind_payload = json.loads(bind.stdout)
    assert bind_payload["ok"] is True
    assert bind_payload["status"] == "applied"
    assert str(profile_path) not in bind.stdout
    assert "private-secrets" not in bind.stdout

    # Bound CLI subcommands resolve the private profile without --profile on argv.
    manifest = _run_cli(
        "--registry",
        str(registry_path),
        "--runtime-root",
        str(runtime_root),
        "decision-context",
        "source-manifest",
        "--goal-id",
        "ops-goal",
        "--agent-id",
        "operations",
        "--format",
        "json",
    )
    assert manifest.returncode == 0, manifest.stderr
    manifest_payload = json.loads(manifest.stdout)
    assert manifest_payload["available"] is True
    assert manifest_payload["source_manifest"]["source_count"] == 1
    assert "private-secrets" not in manifest.stdout

    prepare = _run_cli(
        "--registry",
        str(registry_path),
        "--runtime-root",
        str(runtime_root),
        "decision-context",
        "prepare-evidence",
        "--goal-id",
        "ops-goal",
        "--agent-id",
        "operations",
        "--decision-id",
        "check-bound-prepare",
        "--format",
        "json",
    )
    assert prepare.returncode == 0, prepare.stderr
    prepare_payload = json.loads(prepare.stdout)
    assert prepare_payload["available"] is True
    assert prepare_payload["cursor_state_mutated"] is False
    assert "TOP_SECRET_BODY_DO_NOT_LEAK_12345" not in prepare.stdout
    assert str(authority_doc) not in prepare.stdout

    # Turn-start dispatch observes changed source for operations, without leaking paths or bodies.
    dispatched = extend_turn_start_dispatch(
        {},
        runtime_root=runtime_root,
        registry_path=registry_path,
        goal_id="ops-goal",
        agent_id="operations",
    )
    assert dispatched is not None
    assert dispatched["invoked_count"] == 1
    hook_result = dispatched["results"][0]
    assert hook_result["hook_id"] == DECISION_CONTEXT_TURN_HOOK_ID
    assert hook_result["status"] == "observed"
    assert hook_result["local_private_state_mutated"] is False
    assert hook_result["private_content_returned"] is False
    assert len(dispatched["contexts"]) == 1
    context_view = dispatched["contexts"][0]["content"]
    assert context_view["ok"] is True
    obs = context_view["current"]["observation"]
    assert obs["changed_source_ids"] == ["ops:primary-context"]
    assert obs["p0_complete"] is True
    serialized = json.dumps(dispatched)
    assert "TOP_SECRET_BODY_DO_NOT_LEAK_12345" not in serialized
    assert "private-secrets" not in serialized

    # Other registered agents on the same Goal remain completely untouched.
    other_agent_dispatch = extend_turn_start_dispatch(
        {"registered_count": 0, "invoked_count": 0},
        runtime_root=runtime_root,
        registry_path=registry_path,
        goal_id="ops-goal",
        agent_id="reviewer",
    )
    assert other_agent_dispatch == {"registered_count": 0, "invoked_count": 0}

    # Real quota should-run --dry-run delivers decision_context inline in work_context.sources.
    should_run = _run_cli(
        "--format",
        "json",
        "--registry",
        str(registry_path),
        "--runtime-root",
        str(runtime_root),
        "quota",
        "should-run",
        "--goal-id",
        "ops-goal",
        "--agent-id",
        "operations",
        "--dry-run",
        "--context-delivery",
        "off",
    )
    assert should_run.returncode == 0, should_run.stderr
    sr_payload = json.loads(should_run.stdout)
    sources = (
        sr_payload["interaction_contract"]["agent_channel"]["work_context"].get(
            "sources"
        )
        or []
    )
    dc_sources = [s for s in sources if s.get("kind") == "decision_context"]
    assert len(dc_sources) == 1
    assert (
        dc_sources[0]["content"]["current"]["observation"]["changed_source_ids"]
        == ["ops:primary-context"]
    )
    assert "TOP_SECRET_BODY_DO_NOT_LEAK_12345" not in should_run.stdout
    assert "private-secrets" not in should_run.stdout


def test_missing_bound_profile_surfaces_unavailable_without_crashing_turn(
    tmp_path: Path,
) -> None:
    registry_path, runtime_root, profile_path, _doc = _setup_goal_fixture(tmp_path)

    bind = _run_cli(
        "--registry",
        str(registry_path),
        "--runtime-root",
        str(runtime_root),
        "decision-context",
        "hook",
        "bind",
        "--goal-id",
        "ops-goal",
        "--agent-id",
        "operations",
        "--profile",
        str(profile_path),
        "--expected-revision",
        "none",
        "--operation-id",
        "bind-ops-missing-test",
        "--execute",
        "--format",
        "json",
    )
    assert bind.returncode == 0, bind.stderr
    revision = json.loads(bind.stdout)["current"]["revision"]

    # Remove the profile file after binding to simulate unavailable private storage.
    profile_path.unlink()

    turn_view = read_decision_context_turn_context(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id="ops-goal",
        agent_id="operations",
    )
    assert turn_view["ok"] is False
    assert turn_view["status"] == "unavailable"

    dispatched = extend_turn_start_dispatch(
        {},
        runtime_root=runtime_root,
        registry_path=registry_path,
        goal_id="ops-goal",
        agent_id="operations",
    )
    assert dispatched is not None
    assert dispatched["results"][0]["status"] == "unavailable"
    assert dispatched["results"][0]["agent_read_required"] is False
    assert dispatched["required_reads"] == []

    # Unbind recovers cleanly even while the old profile path is gone.
    unbind = _run_cli(
        "--registry",
        str(registry_path),
        "--runtime-root",
        str(runtime_root),
        "decision-context",
        "hook",
        "unbind",
        "--goal-id",
        "ops-goal",
        "--agent-id",
        "operations",
        "--expected-revision",
        revision,
        "--operation-id",
        "unbind-ops-missing-test",
        "--execute",
        "--format",
        "json",
    )
    assert unbind.returncode == 0, unbind.stderr
    assert json.loads(unbind.stdout)["current"]["binding"]["state"] == "retired"


def test_bind_rejects_disabled_or_mismatched_agent_profile(tmp_path: Path) -> None:
    registry_path, runtime_root, profile_path, _doc = _setup_goal_fixture(tmp_path)

    rejected = _run_cli(
        "--registry",
        str(registry_path),
        "--runtime-root",
        str(runtime_root),
        "decision-context",
        "hook",
        "bind",
        "--goal-id",
        "ops-goal",
        "--agent-id",
        "reviewer",
        "--profile",
        str(profile_path),
        "--expected-revision",
        "none",
        "--operation-id",
        "bind-wrong-agent",
        "--execute",
        "--format",
        "json",
    )
    assert rejected.returncode == 2
    payload = json.loads(rejected.stdout)
    assert payload["ok"] is False
    assert "agent_not_enabled" in payload["error"]
