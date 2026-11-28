"""Contract, O(limit) bounding, malformed-input, and origin/main parity tests for project_asset."""
from __future__ import annotations

import ast
from pathlib import Path
import re
import subprocess
import types

import pytest

from loopx.control_plane.work_items import (
    project_asset as pa,
    project_asset_todo_summary as pa_todos,
)


def _load_baseline_project_asset() -> types.ModuleType | None:
    repo_root = Path(__file__).resolve().parents[2]
    proc = subprocess.run(
        ["git", "show", "origin/main:loopx/control_plane/work_items/project_asset.py"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout:
        return None
    mod = types.ModuleType("_baseline_project_asset")
    mod.__package__ = "loopx.control_plane.work_items"
    mod.__file__ = str(repo_root / "loopx/control_plane/work_items/project_asset.py")
    exec(compile(proc.stdout, mod.__file__, "exec"), mod.__dict__)
    return mod


def test_project_asset_modules_zero_any_and_size_bounds() -> None:
    root = Path(__file__).resolve().parents[2]
    paths = [
        (root / "loopx/control_plane/work_items/project_asset.py", 550),
        (root / "loopx/control_plane/work_items/project_asset_todo_summary.py", 749),
        (Path(__file__).resolve(), 550),
    ]
    for path, max_loc in paths:
        source = path.read_text(encoding="utf-8")
        assert len(source.splitlines()) <= max_loc, f"{path} exceeded {max_loc} LOC"
        assert re.search(r"#\s*type:\s*ignore\b", source) is None, f"{path} contains type ignore"
        tree = ast.parse(source)
        any_refs = sum(
            1
            for node in ast.walk(tree)
            if (isinstance(node, ast.Name) and node.id == "Any")
            or (isinstance(node, ast.Attribute) and node.attr == "Any")
            or (isinstance(node, ast.alias) and node.name == "Any")
        )
        assert any_refs == 0, f"{path} has {any_refs} Any references"


def test_project_asset_preserves_exports() -> None:
    expected = (
        "TODO_PROJECTION_VIEW_SCHEMA_VERSION",
        "TODO_PROJECTION_DETAIL_POINTER_SCHEMA_VERSION",
        "PROJECT_ASSET_TODO_PROJECTION_GAP_SCHEMA_VERSION",
        "PROJECT_ASSET_HANDOFF_STATE_TRACE_CHECK_KEYS",
        "PROJECT_ASSET_TODO_DISPLAY_FIELDS",
        "project_asset_public_safe_compact_text",
        "project_asset_owner",
        "project_asset_gate",
        "project_asset_stop_condition",
        "project_asset_support_mode",
        "project_asset_quota_summary",
        "_optional_int",
        "project_asset_quota_state",
        "project_asset_user_todo_open_count",
        "project_asset_latest_validation",
        "enrich_project_asset",
        "attach_active_state_project_asset_fields",
        "project_asset_summary_is_public_safe",
        "project_asset_handoff_check_projection",
        "project_asset_next_safe_command",
        "build_project_asset",
        "project_asset_todo_projection_metadata",
        "project_asset_todo_projection_gap",
        "_project_asset_display_todo_item",
        "_project_asset_display_todo_items",
        "build_project_asset_todo_summary",
    )
    for name in expected:
        assert hasattr(pa, name), f"missing export {name}"
    assert pa.build_project_asset_todo_summary is pa_todos.build_project_asset_todo_summary


def test_bounded_deferred_slicing_and_malformed_containers() -> None:
    compacts: list[str] = []

    def _compact(item: dict[str, object]) -> dict[str, object]:
        compacts.append(str(item.get("todo_id") or ""))
        return dict(item)

    deferred_rows: list[object] = [
        "non-dict-ignored",
        *[
            {"todo_id": f"def-{i}", "text": f"Deferred item {i}"}
            for i in range(50)
        ],
    ]
    summary = pa.build_project_asset_todo_summary(
        {
            "open_count": 3,
            "done_count": 1,
            "total_count": 4,
            "claimed_open_count": 1,
            "unclaimed_open_count": None,
            "deferred_count": 50,
            "deferred_items": deferred_rows,
            "deferred_resume_candidates": None,
            "recent_completed_advancement_items": "malformed",
        },
        role="agent",
        item_limit=2,
        deferred_item_limit=2,
        advancement_task_class="advancement",
        open_todo_items=lambda _t, **_k: [{"todo_id": "open-1", "text": "Run check", "index": 0}],
        compact_todo_item=_compact,
        todo_lane_items=lambda _t, _l, **_k: [],
        todo_item_is_actionable_open=lambda _i: True,
        todo_item_task_class=lambda _i: "advancement",
    )
    assert summary is not None
    # Key-presence: explicit None for unclaimed_open_count must not synthesize numeric fallback.
    assert summary["unclaimed_open_count"] is None
    assert summary["next_index"] == 0
    # Deferred slicing bounds compact_todo_item calls to deferred_item_limit, not all 50 inputs.
    assert compacts == ["def-0", "def-1"]
    deferred_out = summary["deferred_items"]
    assert isinstance(deferred_out, list) and len(deferred_out) == 2
    assert "deferred_resume_candidates" not in summary
    assert "recent_completed_advancement_items" not in summary


def test_helpers_edge_cases_and_malformed_inputs() -> None:
    assert pa._optional_int(None) is None
    assert pa._optional_int("not-int") is None
    assert pa._optional_int({"bad": 1}) is None
    assert pa._optional_int("12") == 12
    assert pa.project_asset_quota_summary(None) is None
    assert pa.project_asset_latest_validation({}) is None
    assert pa.attach_active_state_project_asset_fields({"project_asset": "not-dict"}) == {}
    assert pa.project_asset_handoff_check_projection({"project_asset": None}) is None

    unsafe_user_path = "/" + "Users" + "/demo/worktree"
    assert not pa.project_asset_summary_is_public_safe({"note": unsafe_user_path})
    assert pa.project_asset_summary_is_public_safe({"note": "public-safe status"})

    handoff = pa.project_asset_handoff_check_projection(
        {
            "waiting_on": "codex",
            "recommended_action": "Ship slice",
            "quota": {"state": "eligible"},
            "project_asset": {
                "next_action": "Ship slice",
                "stop_condition": "stop if tests fail",
            },
        }
    )
    assert handoff is not None
    assert handoff["codex_ready"] is True
    assert handoff["state_trace_ready"] is True


def test_paired_origin_main_behavioral_parity() -> None:
    baseline = _load_baseline_project_asset()
    if baseline is None:
        pytest.skip("git origin/main unavailable in this checkout")

    head_asset = pa.build_project_asset(
        status="blocked",
        waiting_on="user_or_controller",
        recommended_action="Confirm release window",
        operator_question=None,
        agent_command="loopx status --format json",
        missing_gates=[],
        next_handoff_condition=None,
        monitor_signal_waiting_on="monitor_signal",
        monitor_display_stop_condition="stop on unchanged monitor",
    )
    base_asset = baseline.build_project_asset(
        status="blocked",
        waiting_on="user_or_controller",
        recommended_action="Confirm release window",
        operator_question=None,
        agent_command="loopx status --format json",
        missing_gates=[],
        next_handoff_condition=None,
        monitor_signal_waiting_on="monitor_signal",
        monitor_display_stop_condition="stop on unchanged monitor",
    )
    assert head_asset == base_asset

    # Gap & active-state attachment parity
    assert pa.project_asset_todo_projection_gap(user_todos=None, agent_todos={}) == baseline.project_asset_todo_projection_gap(
        user_todos=None, agent_todos={}
    )
    assert pa.project_asset_todo_projection_gap(user_todos={}, agent_todos={}) is None

    def _build_enriched(mod: object) -> dict[str, object]:
        m = mod  # runtime module
        assert isinstance(m, (types.ModuleType, object))
        item: dict[str, object] = {
            "project_asset": {
                "next_action": "Verify checks",
                "stop_condition": "stop if checks fail",
                "todo_projection_gap": {"stale": True},
            },
            "todo_projection_gap": {"stale": True},
            "active_state_next_action": "Verify checks",
            "backlog_hygiene_warning": {"kind": "hygiene"},
            "agent_todos": {"monitor_open_items": [{"todo_id": "m1"}]},
        }
        runs: list[dict[str, object]] = [{"agent_id": "codex", "generated_at": "2026-11-26T00:00:00Z", "classification": "ok"}]
        getattr(mod, "enrich_project_asset")(
            item,
            user_todos={"open_count": 2, "schema_version": "u_v0"},
            agent_todos={"open_count": 3, "schema_version": "a_v0"},
            quota={"compute": "codex", "state": "eligible", "spent_slots": 1, "allowed_slots": 4, "reason": "ok"},
            latest_validation=getattr(mod, "project_asset_latest_validation")(runs[0]),
            latest_runs=runs,
            execution_profile={"mode": "default"},
            orchestration={"mode": "single"},
            subagent_activity={"active": 0},
            interface_budget_cadence={"interval": 60},
            project_asset_todo_summary=lambda t, role=None: (
                getattr(mod, "build_project_asset_todo_summary")(
                    t,
                    role=role,
                    item_limit=2,
                    deferred_item_limit=2,
                    advancement_task_class="advancement",
                    open_todo_items=lambda _td, limit=2, source_keys=(): [{"todo_id": "t1", "text": "Do work", "claimed_by": "codex"}],
                    compact_todo_item=lambda x: dict(x),
                    todo_lane_items=lambda _td, _lane, limit=2: [],
                    todo_item_is_actionable_open=lambda _x: True,
                    todo_item_task_class=lambda _x: "advancement",
                )
            ),
            project_asset_todo_projection_gap=getattr(mod, "project_asset_todo_projection_gap"),
            project_asset_quota_summary=getattr(mod, "project_asset_quota_summary"),
            compact_execution_profile=lambda p: dict(p or {}),
            compact_orchestration_policy=lambda o: dict(o),
            project_asset_handoff_readiness=lambda _i, latest_runs=None: {"ready": True, "runs": len(latest_runs or [])},
            project_asset_quota_state=getattr(mod, "project_asset_quota_state"),
            project_asset_user_todo_open_count=getattr(mod, "project_asset_user_todo_open_count"),
            build_long_task_cadence_hint=lambda **kw: {"quota_state": kw["quota_state"], "open": kw["user_todo_open_count"]},
        )
        attached = getattr(mod, "attach_active_state_project_asset_fields")(
            item,
            latest_runs=runs,
            next_action_projection_warning=lambda **kw: {"warning": kw["active_state_next_action"]},
            autonomous_replan_obligation_from_runs=lambda r, **_kw: {"count": len(r)},
            external_progress_review={"summary": {"ok": True}},
            effective_turn_cadence={"seconds": 30},
        )
        handoff_proj = getattr(mod, "project_asset_handoff_check_projection")(item)
        return {"item": item, "attached": attached, "handoff": handoff_proj}

    assert _build_enriched(pa) == _build_enriched(baseline)
