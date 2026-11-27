"""Tests for quota should-run canonical snapshot reuse, stale recovery, and RPC coalescing."""

from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.cli import main
from loopx.control_plane import effect_runtime
from loopx.control_plane.coordination.authority_source_capture import build_todo_runtime_shadow_projection
from loopx.control_plane.coordination.local_authority import claim_canonical_todo_if_promoted
from loopx.control_plane.effect_runtime import effect_runtime_request_scope, restart_effect_runtime
from loopx.control_plane.quota import live_decision
from loopx.control_plane.todos.list_readback import list_goal_todos
from loopx.control_plane.work_items import planning_inventory


GOAL_ID = "waterfall-goal"
AGENT_ID = "antigravity"


def _write_waterfall_goal(tmp_path: Path, *, provider: str) -> tuple[Path, Path]:
    project = tmp_path / f"project-{provider}"
    runtime = tmp_path / f"runtime-{provider}"
    project.mkdir(parents=True)
    (project / "README.md").write_text("# Waterfall Goal\n", encoding="utf-8")
    state_rel = f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state_path = project / state_rel
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text("# Waterfall Goal\n", encoding="utf-8")
    records = [
        {
            "schema_version": "todo_item_v0",
            "todo_id": "todo_user_gate",
            "index": 1,
            "role": "user",
            "status": "done",
            "done": True,
            "completed_at": "2026-11-27T01:00:00Z",
            "no_followup": True,
            "task_class": "user_gate",
            "text": "[P0] Done cross-role user gate.",
            "priority": "P0",
            "archive_state": "active",
            "source_section": "User Todo / Owner Review Reading Queue",
        },
        {
            "schema_version": "todo_item_v0",
            "todo_id": "todo_user_open",
            "index": 2,
            "role": "user",
            "status": "open",
            "done": False,
            "global_gate": False,
            "bound_agent": "other-agent",
            "task_class": "user_action",
            "text": "[P1] Unrelated other agent user notice.",
            "priority": "P1",
            "archive_state": "active",
            "source_section": "User Todo / Owner Review Reading Queue",
        },
        {
            "schema_version": "todo_item_v0",
            "todo_id": "todo_agent_primary",
            "index": 1,
            "role": "agent",
            "status": "open",
            "done": False,
            "claimed_by": AGENT_ID,
            "resume_when": "todo_done:todo_user_gate",
            "task_class": "advancement_task",
            "text": "[P0] Primary claimed agent deliverable.",
            "priority": "P0",
            "archive_state": "active",
            "source_section": "Agent Todo",
        },
        {
            "schema_version": "todo_item_v0",
            "todo_id": "todo_agent_unclaimed",
            "index": 2,
            "role": "agent",
            "status": "open",
            "done": False,
            "task_class": "advancement_task",
            "text": "[P1] Secondary unclaimed deliverable for stale-claim test.",
            "priority": "P1",
            "archive_state": "active",
            "source_section": "Agent Todo",
        },
    ]
    registry = project / ".loopx" / "registry.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "waterfall-test",
                        "status": "active",
                        "repo": str(project),
                        "state_file": state_rel,
                        "adapter": {"kind": "fixture_connected_delivery_v0", "status": "connected-delivery"},
                        "quota": {"compute": 1.0, "window_hours": 24},
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": [AGENT_ID, "other-agent"],
                            "agent_profiles": {
                                AGENT_ID: {
                                    "schema_version": "agent_profile_v1",
                                    "profile_role": "quality",
                                    "scope": "quality optimization",
                                },
                                "other-agent": {
                                    "schema_version": "agent_profile_v1",
                                    "profile_role": "observer",
                                    "scope": "read-only observation",
                                },
                            },
                            "write_scope": ["loopx/**"],
                        },
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    projection = build_todo_runtime_shadow_projection(goal_id=GOAL_ID, todos=records)
    initialize_canonical_authority(
        runtime,
        GOAL_ID,
        projection,
        state_path=state_path,
        provider=provider,
    )
    return registry, runtime


def _run_should_run_cli(
    registry: Path,
    runtime: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[int, dict[str, object], list[tuple[str, object]]]:
    calls: list[tuple[str, object]] = []
    orig = effect_runtime.effect_runtime_request

    def trace(
        method: str,
        params: Mapping[str, object],
        *,
        timeout: float = effect_runtime.DEFAULT_REQUEST_TIMEOUT_SECONDS,
        retry_safe: bool = True,
        large_local_snapshot: bool = False,
    ) -> dict[str, object]:
        kind = params.get("kind") or params.get("schema_version")
        calls.append((method, kind))
        return orig(
            method,
            params,
            timeout=timeout,
            retry_safe=retry_safe,
            large_local_snapshot=large_local_snapshot,
        )

    monkeypatch.setattr(effect_runtime, "effect_runtime_request", trace)
    capsys.readouterr()
    code = main(
        [
            "--registry",
            str(registry),
            "--runtime-root",
            str(runtime),
            "--format",
            "json",
            "quota",
            "should-run",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
            "--dry-run",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    return code, payload, calls


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_quota_should_run_uses_head_checked_canonical_reuse_and_single_role_parity(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    if provider == "sqlite":
        env_dir = tmp_path / "sqlite-env"
        env_dir.mkdir(parents=True, exist_ok=True)
        isolate_sqlite_runtime(env_dir, monkeypatch)
    try:
        registry, runtime = _write_waterfall_goal(tmp_path, provider=provider)
        code, payload, calls = _run_should_run_cli(registry, runtime, capsys, monkeypatch)
        assert code == 0
        assert payload["ok"] is True

        todo_read_kinds = [kind for method, kind in calls if method == "todo.read"]
        assert "canonical_fields" in todo_read_kinds
        assert "quota_projection" not in todo_read_kinds
        assert ("todo.quota_planning.project", "todo_quota_planning_batch_v0") in calls

        # Verify single-role cross-role resume parity via list_goal_todos.
        with effect_runtime_request_scope():
            exact = list_goal_todos(
                registry_path=registry,
                runtime_root_arg=str(runtime),
                goal_id=GOAL_ID,
                role="agent",
                todo_id="todo_agent_primary",
            )
            all_todos = list_goal_todos(
                registry_path=registry,
                runtime_root_arg=str(runtime),
                goal_id=GOAL_ID,
            )
        assert exact["todo"]["todo_id"] == "todo_agent_primary"
        assert exact["todo"]["resume_ready"] is True
        assert any(t["todo_id"] == "todo_agent_primary" for t in all_todos["agent_todos"]["items"])
    finally:
        if provider == "sqlite":
            restart_effect_runtime()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_quota_should_run_recovers_from_stale_canonical_source_mutation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    if provider == "sqlite":
        env_dir = tmp_path / "sqlite-env"
        env_dir.mkdir(parents=True, exist_ok=True)
        isolate_sqlite_runtime(env_dir, monkeypatch)
    try:
        registry, runtime = _write_waterfall_goal(tmp_path, provider=provider)

        orig_builder = live_decision.build_live_quota_should_run_decision
        observed_flags: list[bool] = []
        mutated = False

        def wrap_builder(status_payload: dict[str, object], **kwargs: object) -> dict[str, object]:
            nonlocal mutated
            observed_flags.append(bool(kwargs.get("reuse_fresh_canonical_snapshot", False)))
            if not mutated:
                mutated = True
                claim_canonical_todo_if_promoted(
                    registry_path=registry,
                    runtime_root=runtime,
                    goal_id=GOAL_ID,
                    todo_id="todo_agent_unclaimed",
                    role="agent",
                    claimed_by="other-agent",
                    actor_agent_id="other-agent",
                    dry_run=False,
                    task_lease_idempotency_key="stale-retry-lease-1",
                    task_lease_expected_version=0,
                )
                return orig_builder(status_payload, **kwargs)
            return orig_builder(status_payload, **kwargs)

        from loopx.cli_commands import quota as quota_cmd

        monkeypatch.setattr(quota_cmd, "build_live_quota_should_run_decision", wrap_builder)
        capsys.readouterr()
        code = main(
            [
                "--registry",
                str(registry),
                "--runtime-root",
                str(runtime),
                "--format",
                "json",
                "quota",
                "should-run",
                "--goal-id",
                GOAL_ID,
                "--agent-id",
                AGENT_ID,
                "--dry-run",
            ]
        )
        assert code == 0
        assert observed_flags == [True, False]
    finally:
        if provider == "sqlite":
            restart_effect_runtime()


def test_planning_inventory_coalesces_compaction_into_single_rpc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item_sel = {"todo_id": "todo_sel", "text": "[P0] Selected work", "status": "open", "task_class": "advancement_task", "source": "selected"}
    item_src = {"todo_id": "todo_src", "text": "[P1] Source item", "status": "open", "task_class": "advancement_task", "selected_by": "fallback"}
    item_run = {"todo_id": "todo_run", "text": "[P1] Runnable item", "status": "open", "task_class": "advancement_task"}
    item_blk = {"todo_id": "todo_blk", "text": "[P0] Blocked higher priority", "status": "blocked", "task_class": "advancement_task"}

    def fake_compact(items: list[dict[str, object]], *, texts: list[str | None] | None = None, strip_text: bool = False) -> list[dict[str, object]]:
        return [{"todo_id": it["todo_id"], "text": (texts or [None] * len(items))[i] or it["text"]} for i, it in enumerate(items)]

    calls: list[int] = []
    orig = fake_compact

    def counted(items: list[dict[str, object]], *, texts: list[str | None] | None = None, strip_text: bool = False) -> list[dict[str, object]]:
        calls.append(len(items))
        return orig(items, texts=texts, strip_text=strip_text)

    monkeypatch.setattr(planning_inventory, "compact_todo_summary_items", counted)
    batched = planning_inventory.build_quota_planning_inventory_request(
        goal_id=GOAL_ID,
        selected=item_sel,
        agent_id=AGENT_ID,
        agent_todo_summary={"open_count": 3, "deferred_count": 1, "first_executable_items": [item_run]},
        agent_todo_source_items=[item_src],
        capability_gate=None,
        blocked_priority_fallback={"blocked_items": [item_blk]},
    )
    assert calls == [4]
    assert batched is not None
    assert batched["selected_todo"]["source"] == "selected"
    assert batched["source_items"][0]["selected_by"] == "fallback"
    assert batched["runnable_candidates"][0]["todo_id"] == "todo_run"
    assert batched["unavailable_higher_priority"][0]["availability_reason"] == "status_blocked"
