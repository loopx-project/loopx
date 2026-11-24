"""Non-mocked File and SQLite parity, freshness, and RPC-collapse tests."""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any, cast

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.capabilities.issue_fix import pr_lifecycle, pr_wait_reconcile
from loopx.capabilities.issue_fix.pr_gate_reconcile import (
    reconcile_acknowledged_issue_fix_pr_reviews,
)
from loopx.capabilities.issue_fix.pr_review_ack import record_issue_fix_pr_review_ack
from loopx.cli_commands.status import (
    attach_agent_lane_next_actions,
    handle_status_command,
)
from loopx.control_plane import effect_runtime
from loopx.control_plane.coordination.authority_source_capture import (
    build_todo_runtime_shadow_projection,
)
from loopx.control_plane.coordination.local_authority import (
    LocalCoordinationAuthorityUnavailable,
    claim_canonical_todo_if_promoted,
    read_canonical_todos_if_promoted,
)
from loopx.control_plane.effect_runtime import effect_runtime_request_scope, restart_effect_runtime
from loopx.control_plane.status.agent_lane_projection import (
    compact_agent_lane_status_payload_for_display,
)
from loopx.control_plane.todos.list_readback import list_goal_todos
from loopx.control_plane.todos.quota_summary import (
    compact_agent_lane_todos_for_status_display,
    select_quota_todo_contexts,
)
from loopx.control_plane.todos.todo_index import compact_agent_lane_todo_index_for_status_display
from loopx.control_plane.todos.todo_summary import (
    first_open_todo_text,
    open_todo_items,
    project_asset_todo_summary,
)
from loopx.heartbeat_prequota import (
    render_heartbeat_pre_quota_markdown,
    run_heartbeat_pre_quota,
)
from loopx.presentation.renderers.status_markdown import render_status_markdown
import loopx.rollout_event_log as rollout_event_log_module
from loopx.rollout_event_log import (
    append_rollout_event,
    build_rollout_event,
    rollout_event_log_path,
)
from loopx.status import collect_status

GOAL_ID = "hotpath-efficiency-goal"
AGENT_ID = "agent-hotpath"
REPO_SLUG = "example/hotpath"


@pytest.fixture(autouse=True)
def _isolated_sqlite_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    isolate_sqlite_runtime(tmp_path, monkeypatch)  # type: ignore[no-untyped-call]
    yield
    restart_effect_runtime()


def _normalize_volatile(value: Any) -> Any:
    """Strip wall-clock timestamps, durations, and envelope signatures for parity comparison."""
    if isinstance(value, list):
        return [_normalize_volatile(item) for item in value]
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if key in {
                "checked_at",
                "generated_at",
                "observed_at",
                "last_read_at",
                "recorded_at",
                "duration_ms",
                "projection_envelope",
                "signature",
                "next_check_at",
                "next_due_at",
                "last_checked_at",
                "next_action_basis",
                "recommendation_context",
            }:
                continue
            normalized[key] = _normalize_volatile(item)
        return normalized
    return value


def _write_goal_fixture(
    tmp_path: Path,
    *,
    provider: str,
    deferred_count: int = 12,
    include_pr_wait: bool = False,
    include_pr_review_gate: bool = False,
    capability_granted: bool = False,
) -> tuple[Path, Path, Path]:
    project = tmp_path / "project"
    runtime = tmp_path / "runtime"
    state = project / "ACTIVE_GOAL_STATE.md"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text("# Hotpath Goal\n\nKeep state concise.\n", encoding="utf-8")

    records: list[dict[str, Any]] = [
        {
            "schema_version": "todo_item_v0",
            "todo_id": "todo_adv_primary",
            "index": 1,
            "role": "agent",
            "status": "open",
            "done": False,
            "text": "[P1] Deliver primary agent hotpath slice",
            "priority": "P1",
            "task_class": "advancement_task",
            "claimed_by": AGENT_ID,
            "archive_state": "active",
            "source_section": "Agent Todo",
            "required_capabilities": ["shell"],
        },
        {
            "schema_version": "todo_item_v0",
            "todo_id": "todo_adv_unclaimed",
            "index": 2,
            "role": "agent",
            "status": "open",
            "done": False,
            "text": "[P2] Follow-up unclaimed agent work",
            "priority": "P2",
            "task_class": "advancement_task",
            "archive_state": "active",
            "source_section": "Agent Todo",
        },
        {
            "schema_version": "todo_item_v0",
            "todo_id": "todo_monitor_quiet",
            "index": 3,
            "role": "agent",
            "status": "open",
            "done": False,
            "text": "[P3] Monitor CI telemetry [repeat: every 6h]",
            "priority": "P3",
            "task_class": "monitor",
            "archive_state": "active",
            "source_section": "Agent Todo",
        },
        {
            "schema_version": "todo_item_v0",
            "todo_id": "todo_user_open",
            "index": 4,
            "role": "user",
            "status": "open",
            "done": False,
            "text": "[P2] Read architecture review note",
            "priority": "P2",
            "task_class": "user_action",
            "archive_state": "active",
            "source_section": "User Todo / Owner Review Reading Queue",
        },
    ]
    next_index = 5
    for offset in range(deferred_count):
        records.append(
            {
                "schema_version": "todo_item_v0",
                "todo_id": f"todo_deferred_{offset:02d}",
                "index": next_index,
                "role": "agent",
                "status": "deferred",
                "done": False,
                "text": f"[P2] Deferred item {offset:02d} after blocker",
                "priority": "P2",
                "task_class": "advancement_task",
                "resume_when": "todo_done:todo_adv_primary",
                "archive_state": "active",
                "source_section": "Agent Todo",
            }
        )
        next_index += 1
    if include_pr_wait:
        records.append(
            {
                "schema_version": "todo_item_v0",
                "todo_id": "todo_wait_pr_41",
                "index": next_index,
                "role": "agent",
                "status": "deferred",
                "done": False,
                "text": "[P1] Resume follow-up after PR 41 merges",
                "priority": "P1",
                "task_class": "advancement_task",
                "task_repository": f"git:github.com/{REPO_SLUG}",
                "resume_when": "pr_merged:#41",
                "archive_state": "active",
                "source_section": "Agent Todo",
            }
        )
        next_index += 1
    if include_pr_review_gate:
        records.append(
            {
                "schema_version": "todo_item_v0",
                "todo_id": "todo_pr_review_41",
                "index": next_index,
                "role": "user",
                "status": "open",
                "done": False,
                "text": f"[P1] Review PR https://github.com/{REPO_SLUG}/pull/41",
                "priority": "P1",
                "task_class": "user_action",
                "bound_agent": AGENT_ID,
                "updated_at": "2026-11-24T09:00:00Z",
                "archive_state": "active",
                "source_section": "User Todo / Owner Review Reading Queue",
            }
        )

    goal_entry: dict[str, Any] = {
        "id": GOAL_ID,
        "status": "active",
        "domain": "software",
        "repo": str(project),
        "state_file": state.name,
        "adapter": {"kind": "read_only_project_map_v0"},
        "coordination": {"registered_agents": [AGENT_ID]},
        "available_capabilities": (
            ["shell", "external_evidence_poll"] if capability_granted else ["shell"]
        ),
    }
    registry = project / ".loopx" / "registry.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(runtime),
                "goals": [goal_entry],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    projection = build_todo_runtime_shadow_projection(goal_id=GOAL_ID, todos=records)
    initialize_canonical_authority(runtime, GOAL_ID, projection, state_path=state, provider=provider)
    return registry, runtime, state


def _append_unrelated_rollout_events(runtime: Path, count: int) -> None:
    log_path = rollout_event_log_path(runtime, GOAL_ID)
    base = datetime(2026, 11, 24, 1, 0, tzinfo=timezone.utc)
    for idx in range(count):
        ts = (base + timedelta(minutes=idx)).isoformat().replace("+00:00", "Z")
        append_rollout_event(
            log_path,
            build_rollout_event(
                goal_id=GOAL_ID,
                event_kind="evidence_log_read",
                agent_id=AGENT_ID,
                status="completed",
                summary=f"Read fixture {idx}.",
                recorded_at=ts,
            ),
        )


def _capture_rpc_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    calls: list[tuple[str, dict[str, Any]]] = []
    original = effect_runtime.effect_runtime_request

    def observed(method: str, params: dict[str, Any], **kwargs: Any) -> Any:
        calls.append((method, dict(params) if isinstance(params, dict) else {}))
        return original(method, params, **kwargs)

    monkeypatch.setattr(effect_runtime, "effect_runtime_request", observed)
    return calls


def _status_cli_args(*, agent_id: str | None) -> argparse.Namespace:
    return argparse.Namespace(
        limit=5,
        agent_id=agent_id,
        include_task_graph=False,
        goal_id=None,
        available_capabilities=["shell"],
        use_projection_cache=False,
        write_projection_cache=False,
        projection_cache_ttl_seconds=60,
        format="json",
    )


def _run_status_cli(registry: Path, runtime: Path, *, agent_id: str | None) -> tuple[dict[str, Any], str]:
    captured: list[tuple[dict[str, Any], str]] = []

    def capture(value: dict[str, Any], _fmt: str, renderer: Any) -> None:
        captured.append((value, renderer(value)))

    exit_code = handle_status_command(
        _status_cli_args(agent_id=agent_id),
        registry_path=registry,
        runtime_root_arg=str(runtime),
        output_format=lambda _args: "json",
        print_payload=capture,
    )
    assert exit_code == 0
    assert len(captured) == 1
    return captured[0]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_status_collapses_duplicate_reads_and_n_plus_one_paging_with_exact_parity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    registry_zero, runtime_zero, _ = _write_goal_fixture(
        tmp_path / "zero", provider=provider, deferred_count=0
    )
    registry_many, runtime_many, _ = _write_goal_fixture(
        tmp_path / "many", provider=provider, deferred_count=12
    )

    calls = _capture_rpc_calls(monkeypatch)
    fast_payload, fast_md = _run_status_cli(registry_many, runtime_many, agent_id=AGENT_ID)
    many_methods = [method for method, _ in calls]

    # Zero-deferred vs 12-deferred must keep todo.context.page flat (no N+1 deferred RPCs).
    calls.clear()
    _run_status_cli(registry_zero, runtime_zero, agent_id=AGENT_ID)
    zero_methods = [method for method, _ in calls]

    assert many_methods.count("todo.read") == 1
    assert many_methods.count("coordination.local_authority.todo_snapshot") <= 1
    assert many_methods.count("todo.succession.project") == 0
    assert many_methods.count("todo.context.page") <= 5
    assert many_methods.count("todo.context.page") == zero_methods.count("todo.context.page")
    assert many_methods.count("agent.delivery_workspace.evaluate") == 2
    assert many_methods.count("work_item.interaction_execution.project") == 1
    assert many_methods.count("work_item.interaction_reads.project") == 1

    # Un-reused baseline path: independent collect_status scope + standalone todo.read(quota_projection).
    calls.clear()
    baseline_payload = collect_status(
        registry_path=registry_many,
        runtime_root_override=str(runtime_many),
        limit=5,
        include_task_graph=False,
        goal_id=None,
        available_capabilities=["shell"],
        agent_lane_id=AGENT_ID,
    )
    attach_agent_lane_next_actions(baseline_payload, agent_id=AGENT_ID)
    compact_agent_lane_todos_for_status_display(baseline_payload)
    compact_agent_lane_status_payload_for_display(baseline_payload, agent_id=AGENT_ID)
    compact_agent_lane_todo_index_for_status_display(baseline_payload)
    attention_queue = cast(dict[str, Any], baseline_payload.get("attention_queue", {}))
    for item in cast(list[dict[str, Any]], attention_queue.get("items", [])):
        item.pop("recommendation_context", None)
        item.pop("next_action_basis", None)
    baseline_md = render_status_markdown(baseline_payload)

    def _scrub_iso_timestamps(markdown: str) -> str:
        return re.sub(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z",
            "<TIMESTAMP>",
            markdown,
        )

    assert _normalize_volatile(fast_payload) == _normalize_volatile(baseline_payload)
    assert _scrub_iso_timestamps(fast_md) == _scrub_iso_timestamps(baseline_md)

    # Verify non-agent status drops N+1 deferred/display handoff_sources calls cleanly and preserves output.
    calls.clear()
    plain_payload, plain_md = _run_status_cli(registry_many, runtime_many, agent_id=None)
    plain_methods = [method for method, _ in calls]
    calls.clear()
    _run_status_cli(registry_zero, runtime_zero, agent_id=None)
    plain_zero_methods = [method for method, _ in calls]
    assert plain_methods.count("todo.read") == 1
    assert plain_methods.count("coordination.local_authority.todo_snapshot") <= 1
    assert plain_methods.count("todo.context.page") <= 2
    assert plain_methods.count("todo.context.page") == plain_zero_methods.count("todo.context.page")
    assert "Deliver primary agent hotpath slice" in plain_md
    agent_todos = plain_payload["attention_queue"]["items"][0]["agent_todos"]
    assert open_todo_items(agent_todos, include_handoff_note=False) == [
        {k: v for k, v in item.items() if k != "handoff_note"}
        for item in open_todo_items(agent_todos, include_handoff_note=True)
    ]
    assert first_open_todo_text(agent_todos)
    assert (
        project_asset_todo_summary(agent_todos, role="agent")
        == build_summary_with_notes(agent_todos)
    )


def build_summary_with_notes(agent_todos: dict[str, Any]) -> dict[str, Any] | None:
    """Reference comparison where handoff_note was computed and immediately discarded."""
    summary = project_asset_todo_summary(agent_todos, role="agent")
    assert summary is not None
    # Confirm open_todo_items with notes matches the stripped project_asset rows.
    for row in cast(list[dict[str, Any]], summary.get("items", [])):
        assert "handoff_note" not in row
    for row in cast(list[dict[str, Any]], summary.get("deferred_items", [])):
        assert "handoff_note" not in row
    return summary


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_status_reused_quota_projection_rejects_stale_and_unavailable_authority(
    tmp_path: Path, provider: str
) -> None:
    registry, runtime, _ = _write_goal_fixture(
        tmp_path / "stale", provider=provider, deferred_count=2
    )
    with effect_runtime_request_scope():
        before = read_canonical_todos_if_promoted(
            runtime_root=runtime, goal_id=GOAL_ID, include_summary=True
        )
        fields = before["_todo_fields"]
        claim_canonical_todo_if_promoted(
            registry_path=registry,
            runtime_root=runtime,
            goal_id=GOAL_ID,
            todo_id="todo_adv_unclaimed",
            role="agent",
            claimed_by=AGENT_ID,
            actor_agent_id=AGENT_ID,
            dry_run=False,
            task_lease_idempotency_key="hotpath-lease-1",
            task_lease_expected_version=0,
        )
        with pytest.raises(ValueError, match="quota canonical source changed"):
            select_quota_todo_contexts(
                fields["user_todos"],
                None,
                fields["agent_todos"],
                None,
                agent_identity={"agent_id": AGENT_ID},
                available_capabilities=["shell"],
                reuse_fresh_canonical_snapshot=True,
            )

    registry2, runtime2, _ = _write_goal_fixture(
        tmp_path / "rollout", provider=provider, deferred_count=1
    )
    with effect_runtime_request_scope():
        before2 = read_canonical_todos_if_promoted(
            runtime_root=runtime2, goal_id=GOAL_ID, include_summary=True
        )
        fields2 = before2["_todo_fields"]
        append_rollout_event(
            rollout_event_log_path(runtime2, GOAL_ID),
            build_rollout_event(
                goal_id=GOAL_ID,
                event_kind="evidence_log_read",
                agent_id=AGENT_ID,
                status="completed",
                summary="Concurrent log append.",
                recorded_at="2026-11-24T10:00:00Z",
            ),
        )
        with pytest.raises(ValueError, match="quota canonical source changed"):
            select_quota_todo_contexts(
                fields2["user_todos"],
                None,
                fields2["agent_todos"],
                None,
                agent_identity={"agent_id": AGENT_ID},
                available_capabilities=["shell"],
                reuse_fresh_canonical_snapshot=True,
            )

        authority_dir = runtime2 / "authority" / f"{provider}-v0"
        hidden = authority_dir.with_name(authority_dir.name + "-hidden")
        authority_dir.rename(hidden)
        try:
            with pytest.raises(LocalCoordinationAuthorityUnavailable):
                select_quota_todo_contexts(
                    fields2["user_todos"],
                    None,
                    fields2["agent_todos"],
                    None,
                    agent_identity={"agent_id": AGENT_ID},
                    available_capabilities=["shell"],
                    reuse_fresh_canonical_snapshot=True,
                )
        finally:
            if authority_dir.exists():
                shutil.rmtree(authority_dir)
            hidden.rename(authority_dir)


def _write_pr_wait_status_fixture(
    tmp_path: Path, *, provider: str, seed_rollout_log: bool
) -> tuple[Path, Path, Path]:
    project = tmp_path / "project"
    runtime = tmp_path / "runtime"
    state = project / "ACTIVE_GOAL_STATE.md"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text("# Hotpath Goal\n\nKeep state concise.\n", encoding="utf-8")
    records: list[dict[str, Any]] = [
        {
            "schema_version": "todo_item_v0",
            "todo_id": "todo_adv_primary",
            "index": 1,
            "role": "agent",
            "status": "done",
            "done": True,
            "no_followup": True,
            "text": "[P1] Completed primary work",
            "priority": "P1",
            "task_class": "advancement_task",
            "claimed_by": AGENT_ID,
            "completed_at": "2026-11-24T09:00:00Z",
            "archive_state": "active",
            "source_section": "Agent Todo",
        },
        {
            "schema_version": "todo_item_v0",
            "todo_id": "todo_wait_pr_41",
            "index": 2,
            "role": "agent",
            "status": "deferred",
            "done": False,
            "text": "[P1] Resume follow-up after PR 41 merges",
            "priority": "P1",
            "task_class": "advancement_task",
            "claimed_by": AGENT_ID,
            "task_repository": f"git:github.com/{REPO_SLUG}",
            "resume_when": "pr_merged:#41",
            "archive_state": "active",
            "source_section": "Agent Todo",
        },
    ]
    goal_entry: dict[str, Any] = {
        "id": GOAL_ID,
        "status": "active",
        "domain": "software",
        "repo": str(project),
        "state_file": state.name,
        "adapter": {"kind": "read_only_project_map_v0", "status": "connected"},
        "coordination": {"registered_agents": [AGENT_ID]},
        "available_capabilities": ["shell"],
    }
    registry = project / ".loopx" / "registry.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(runtime),
                "goals": [goal_entry],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    projection = build_todo_runtime_shadow_projection(goal_id=GOAL_ID, todos=records)
    initialize_canonical_authority(runtime, GOAL_ID, projection, state_path=state, provider=provider)
    log_path = rollout_event_log_path(runtime, GOAL_ID)
    if seed_rollout_log:
        append_rollout_event(
            log_path,
            build_rollout_event(
                goal_id=GOAL_ID,
                event_kind="evidence_log_read",
                agent_id=AGENT_ID,
                status="completed",
                summary="Baseline log event prior to PR merge.",
                recorded_at="2026-11-24T09:30:00Z",
            ),
        )
    return registry, runtime, log_path


def _install_rollout_toctou_preload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    log_path: Path,
    mode: str,
) -> Path:
    flag_path = tmp_path / "injected.flag"
    merge_event = build_rollout_event(
        goal_id=GOAL_ID,
        event_kind="pr_merge",
        agent_id=AGENT_ID,
        pr_ref=f"{REPO_SLUG}#41",
        status="merged",
        summary="PR 41 merged.",
        recorded_at="2026-11-24T10:00:00Z",
    )
    merge_line = json.dumps(merge_event, sort_keys=True, ensure_ascii=False) + "\n"
    resolved_log = str(log_path.expanduser().resolve(strict=False))
    hook_path = tmp_path / f"toctou-hook-{mode}.mjs"
    hook_path.write_text(
        'import { appendFileSync, writeFileSync } from "node:fs";\n'
        'import fsPromises from "node:fs/promises";\n'
        'import { syncBuiltinESMExports } from "node:module";\n'
        'import { resolve } from "node:path";\n'
        f"const targetLog = {json.dumps(resolved_log)};\n"
        f"const flagPath = {json.dumps(str(flag_path))};\n"
        f"const mergeLine = {json.dumps(merge_line)};\n"
        f"const mode = {json.dumps(mode)};\n"
        "let sawRead = false;\n"
        "let injected = false;\n"
        "const isTarget = (arg) => typeof arg === 'string' && resolve(arg) === targetLog;\n"
        "const origReadFile = fsPromises.readFile.bind(fsPromises);\n"
        "const origStat = fsPromises.stat.bind(fsPromises);\n"
        "fsPromises.readFile = async function (pathArg, ...rest) {\n"
        "  try {\n"
        "    const data = await origReadFile(pathArg, ...rest);\n"
        "    if (isTarget(pathArg)) {\n"
        "      sawRead = true;\n"
        "      if (mode === 'after_read_before_post_stat' && !injected) {\n"
        "        injected = true;\n"
        "        appendFileSync(targetLog, mergeLine, 'utf8');\n"
        "        writeFileSync(flagPath, '1', 'utf8');\n"
        "      }\n"
        "    }\n"
        "    return data;\n"
        "  } catch (error) {\n"
        "    if (mode === 'created_after_enoent' && !injected && isTarget(pathArg) && error?.code === 'ENOENT') {\n"
        "      injected = true;\n"
        "      writeFileSync(targetLog, mergeLine, 'utf8');\n"
        "      writeFileSync(flagPath, '1', 'utf8');\n"
        "    }\n"
        "    throw error;\n"
        "  }\n"
        "};\n"
        "fsPromises.stat = async function (pathArg, ...rest) {\n"
        "  const info = await origStat(pathArg, ...rest);\n"
        "  if (mode === 'after_snapshot_before_line86' && sawRead && !injected && isTarget(pathArg)) {\n"
        "    injected = true;\n"
        "    appendFileSync(targetLog, mergeLine, 'utf8');\n"
        "    writeFileSync(flagPath, '1', 'utf8');\n"
        "  }\n"
        "  return info;\n"
        "};\n"
        "syncBuiltinESMExports();\n",
        encoding="utf-8",
    )
    existing_node_options = os.environ.get("NODE_OPTIONS", "").strip()
    monkeypatch.setenv(
        "NODE_OPTIONS",
        f"{existing_node_options} --import={hook_path.as_uri()}".strip(),
    )
    restart_effect_runtime()
    return flag_path


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize(
    ("mode", "seed_rollout_log"),
    [
        ("after_read_before_post_stat", True),
        ("after_snapshot_before_line86", True),
        ("created_after_enoent", False),
    ],
)
def test_status_agent_lane_never_emits_stale_resume_ready_on_mid_read_pr_merge_toctou(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    mode: str,
    seed_rollout_log: bool,
) -> None:
    registry, runtime, log_path = _write_pr_wait_status_fixture(
        tmp_path, provider=provider, seed_rollout_log=seed_rollout_log
    )
    flag_path = _install_rollout_toctou_preload(
        tmp_path, monkeypatch, log_path=log_path, mode=mode
    )
    try:
        payload, rendered_md = _run_status_cli(registry, runtime, agent_id=AGENT_ID)
    finally:
        restart_effect_runtime()

    assert flag_path.is_file(), f"expected {mode} TOCTOU window hook to fire"
    assert payload["ok"] is True
    item = payload["attention_queue"]["items"][0]
    agent_todos = item["agent_todos"]
    assert agent_todos["items"][0]["todo_id"] == "todo_wait_pr_41"
    assert agent_todos["items"][0]["resume_ready"] is True
    assert agent_todos["deferred_work_counts"]["ready"] == 1
    assert agent_todos["deferred_work_counts"]["blocked"] == 0
    frontier = item["project_asset"]["agent_scope_frontier"]
    assert frontier["action"] == "successor_replan_required"
    assert frontier["deferred_resume_candidates"][0]["todo_id"] == "todo_wait_pr_41"
    assert frontier["deferred_resume_candidates"][0]["resume_ready"] is True
    assert "todo_wait_pr_41" in rendered_md


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_status_agent_lane_recovers_when_pr_merge_lands_between_collect_status_and_batch_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    import loopx.cli_commands.status as status_cli_module

    registry, runtime, log_path = _write_pr_wait_status_fixture(
        tmp_path, provider=provider, seed_rollout_log=True
    )
    original_attach = status_cli_module.attach_agent_lane_next_actions
    appended = False

    def append_before_first_attach(payload: dict[str, object], **kwargs: Any) -> dict[str, object]:
        nonlocal appended
        if not appended:
            appended = True
            append_rollout_event(
                log_path,
                build_rollout_event(
                    goal_id=GOAL_ID,
                    event_kind="pr_merge",
                    agent_id=AGENT_ID,
                    pr_ref=f"{REPO_SLUG}#41",
                    status="merged",
                    summary="PR 41 merged before quota batch.",
                    recorded_at="2026-11-24T10:05:00Z",
                ),
            )
        return original_attach(payload, **kwargs)

    monkeypatch.setattr(status_cli_module, "attach_agent_lane_next_actions", append_before_first_attach)
    payload, _ = _run_status_cli(registry, runtime, agent_id=AGENT_ID)

    assert appended is True
    assert payload["ok"] is True
    item = payload["attention_queue"]["items"][0]
    assert item["agent_todos"]["items"][0]["todo_id"] == "todo_wait_pr_41"
    assert item["agent_todos"]["items"][0]["resume_ready"] is True
    assert item["agent_todos"]["deferred_work_counts"]["ready"] == 1
    assert item["project_asset"]["agent_scope_frontier"]["action"] == "successor_replan_required"
    assert item["project_asset"]["agent_scope_frontier"]["deferred_resume_candidates"][0]["resume_ready"] is True


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_heartbeat_prequota_empty_fastpath_skips_rollout_scans_with_parity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    registry, runtime, _ = _write_goal_fixture(
        tmp_path,
        provider=provider,
        deferred_count=4,
        include_pr_wait=False,
        include_pr_review_gate=False,
        capability_granted=True,
    )
    _append_unrelated_rollout_events(runtime, 50)

    calls = _capture_rpc_calls(monkeypatch)
    rollout_scans: list[Path] = []
    original_load = rollout_event_log_module.load_rollout_events

    def tracked_load(path: Path, **kwargs: Any) -> list[dict[str, Any]]:
        rollout_scans.append(path)
        return original_load(path, **kwargs)

    monkeypatch.setattr(rollout_event_log_module, "load_rollout_events", tracked_load)

    result = run_heartbeat_pre_quota(
        registry_path=registry,
        runtime_root_arg=str(runtime),
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
    )
    methods = [method for method, _ in calls]

    assert result["ok"] is True
    assert result["degraded"] is False
    assert result["checks"]["acknowledged_pr_reviews"]["reconciled_count"] == 0
    assert result["checks"]["acknowledged_pr_reviews"]["ack_receipt_count"] == 0
    assert result["checks"]["pr_dependencies"]["external_read_count"] == 0
    assert len(rollout_scans) == 0
    assert methods.count("todo.read") == 1
    assert methods.count("todo.pr_wait_observation.plan") == 1
    assert "- reconciled_count: `0`" in render_heartbeat_pre_quota_markdown(result)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_heartbeat_prequota_shares_todo_read_and_preserves_active_wait_and_ack_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    registry, runtime, state = _write_goal_fixture(
        tmp_path,
        provider=provider,
        deferred_count=2,
        include_pr_wait=True,
        include_pr_review_gate=True,
        capability_granted=True,
    )
    _append_unrelated_rollout_events(runtime, 30)
    pr_url = f"https://github.com/{REPO_SLUG}/pull/41"
    record_issue_fix_pr_review_ack(
        registry_path=registry,
        runtime_root_arg=str(runtime),
        goal_id=GOAL_ID,
        todo_id="todo_pr_review_41",
        agent_id=AGENT_ID,
        project=state.parent,
        url=pr_url,
        owner_acknowledged=True,
        generated_at="2026-11-24T09:10:00Z",
    )

    # Record a recent validation event for #41 so pr_wait_reconcile uses TS rollout log readback.
    append_rollout_event(
        rollout_event_log_path(runtime, GOAL_ID),
        build_rollout_event(
            goal_id=GOAL_ID,
            event_kind="validation",
            agent_id=AGENT_ID,
            pr_ref=f"{REPO_SLUG}#41",
            classification="pr_wait_observation",
            status="OPEN",
            summary="Recent check within cooldown.",
            recorded_at=datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
        ),
    )

    calls = _capture_rpc_calls(monkeypatch)
    # 1) Historical/unmutated parity when external PR state is still OPEN (fetch_metadata=False):
    #    reconcile_acknowledged_issue_fix_pr_reviews + pr_wait_reconcile share ONE todo.read.
    baseline_ack = reconcile_acknowledged_issue_fix_pr_reviews(
        registry_path=registry,
        runtime_root_arg=str(runtime),
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        project=state.parent,
        fetch_metadata=False,
        execute=False,
    )
    baseline_wait = pr_wait_reconcile.reconcile_pr_wait_dependencies(
        registry_path=registry,
        runtime_root_arg=str(runtime),
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
    )
    baseline_read_count = sum(m == "todo.read" for m, _ in calls)
    assert baseline_read_count == 3  # 2 in pr_gate/pr_review_ack + 1 in pr_wait_reconcile

    calls.clear()
    all_todos_box: dict[str, Any] = {}

    def single_query_loader() -> list[dict[str, Any]]:
        if "todos" not in all_todos_box:
            all_todos_box["todos"] = list_goal_todos(
                registry_path=registry,
                runtime_root_arg=str(runtime),
                goal_id=GOAL_ID,
                role=None,
            )["todos"]
        return list(all_todos_box["todos"])

    def single_agent_loader() -> list[dict[str, Any]] | None:
        if "todos" not in all_todos_box:
            return None
        return [t for t in all_todos_box["todos"] if t.get("role") == "agent"]

    shared_ack = reconcile_acknowledged_issue_fix_pr_reviews(
        registry_path=registry,
        runtime_root_arg=str(runtime),
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        project=state.parent,
        fetch_metadata=False,
        execute=False,
        todo_loader=single_query_loader,
    )
    shared_wait = pr_wait_reconcile.reconcile_pr_wait_dependencies(
        registry_path=registry,
        runtime_root_arg=str(runtime),
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        shared_agent_todos_loader=single_agent_loader,
    )
    assert _normalize_volatile(shared_ack) == _normalize_volatile(baseline_ack)
    assert _normalize_volatile(shared_wait) == _normalize_volatile(baseline_wait)
    assert shared_wait["plan"]["waiting_targets"][0]["pr_ref"] == f"{REPO_SLUG}#41"
    assert sum(m == "todo.read" for m, _ in calls) == 1

    # 2) Active end-to-end prequota reconciliation when external PR reaches MERGED:
    def fake_fetch(reference: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "url": pr_url,
            "number": 41,
            "state": "MERGED",
            "mergedAt": "2026-11-24T09:15:00Z",
            "baseRepository": {"nameWithOwner": REPO_SLUG},
            "headRepository": {"nameWithOwner": REPO_SLUG},
        }

    monkeypatch.setattr(pr_lifecycle, "fetch_github_pr_lifecycle_payload", fake_fetch)
    calls.clear()
    active_result = run_heartbeat_pre_quota(
        registry_path=registry,
        runtime_root_arg=str(runtime),
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
    )
    assert active_result["checks"]["acknowledged_pr_reviews"]["reconciled_count"] == 1
    assert active_result["checks"]["acknowledged_pr_reviews"]["terminal_count"] == 1
    assert active_result["checks"]["pr_dependencies"]["external_read_count"] == 0
    # After MERGED reconciliation appended pr_merge, the deferred pr_merged wait is now satisfied!
    assert active_result["checks"]["pr_dependencies"]["plan"]["waiting_targets"] == []
    user_todo = list_goal_todos(
        registry_path=registry,
        runtime_root_arg=str(runtime),
        goal_id=GOAL_ID,
        role="user",
        todo_id="todo_pr_review_41",
    )["todo"]
    assert user_todo["status"] == "done"

    # 3) Next clean heartbeat-prequota turn hits single-read already_reconciled path (1 todo.read total!).
    calls.clear()
    second_result = run_heartbeat_pre_quota(
        registry_path=registry,
        runtime_root_arg=str(runtime),
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
    )
    assert second_result["checks"]["acknowledged_pr_reviews"]["already_reconciled_count"] == 1
    assert second_result["checks"]["acknowledged_pr_reviews"]["reconciled_count"] == 0
    assert sum(m == "todo.read" for m, _ in calls) == 1
