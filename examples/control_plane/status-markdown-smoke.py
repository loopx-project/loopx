#!/usr/bin/env python3
"""Smoke-test agent-facing status and quota hints.

This stays dependency-free and uses the public status collector against a
temporary planned read-only-map goal.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.status import project_post_handoff_history  # noqa: E402
from loopx.status import (  # noqa: E402
    build_status_runtime_summaries,
    build_contract_health_projection,
    collect_status,
    project_asset_summary_is_public_safe,
)
from loopx.presentation.renderers.status_markdown import render_status_markdown  # noqa: E402
from loopx.quota import build_quota_should_run, render_quota_should_run_markdown  # noqa: E402
from loopx.review_packet import build_review_packet  # noqa: E402
from loopx.handoff_budget import PROJECT_AGENT_HANDOFF_BUDGET  # noqa: E402
from status_markdown_agent_lane_fixtures import (  # noqa: E402
    assert_status_agent_lane_frontier_hint_projection,
    assert_status_agent_lane_next_action_projection,
    assert_status_agent_lane_todo_summary_display_compaction,
    assert_status_agent_lane_vision_lookback_survives_display_trim,
    assert_status_agent_member_selected_lane_claim_survives_truncated_claim_list,
)
from status_markdown_fixtures import (  # noqa: E402
    AGENT_TODO_TEXT,
    APPROVED_ACTION,
    APPROVED_COMMAND,
    CONNECTED_READONLY_ACTION,
    CONNECTED_READONLY_CLASSIFICATION,
    CONNECTED_READONLY_GOAL_ID,
    DEFERRED_ACTION,
    DELIVERY_ACTION,
    DELIVERY_AGENT_TODO,
    DELIVERY_GOAL_ID,
    DEPENDENCY_AGENT_TODO,
    DEPENDENCY_BLOCKER_GOAL_ID,
    DEPENDENCY_CURRENT_GOAL_ID,
    DEPENDENCY_MONITOR_TODO,
    DEPENDENCY_USER_TODO,
    EXPLICIT_REFRESH_CLASSIFICATION,
    OLD_PLANNED_ACTION,
    POST_HANDOFF_ACTION,
    POST_HANDOFF_CLASSIFICATION,
    REGISTRY_OVERRIDE_ACTION,
    REGISTRY_OVERRIDE_HANDOFF,
    REGISTRY_OVERRIDE_QUESTION,
    REGISTRY_OVERRIDE_STATUS,
    REJECTED_ACTION,
    USER_TODO_TEXT,
    assert_decision_freshness_summary_markdown,
    assert_event_ledger_summary_markdown,
    assert_missing_project_asset_markdown_fallback,
    assert_usage_summary_markdown,
    mark_planned_todos_done,
    write_connected_delivery_registry,
    write_connected_readonly_registry,
    write_dependency_blocker_registry,
    write_global_source_registry_shadow,
    write_planned_registry,
)
from status_markdown_run_fixtures import (  # noqa: E402
    append_connected_delivery_fixture,
    append_connected_readonly_progress_fixture,
    append_operator_gate_fixture,
    append_orphan_runtime_fixture,
    append_post_handoff_run_fixture,
    append_quota_slot_spend_fixture,
    append_stale_state_projection_fixture,
    append_state_refreshed_fixture,
    set_registry_attention_override,
)

def assert_handoff_budget_contract(readiness: dict, label: str) -> None:
    budget = readiness.get("handoff_interface_budget")
    assert isinstance(budget, dict), (label, readiness)
    assert budget["mode"] == PROJECT_AGENT_HANDOFF_BUDGET["mode"], (label, budget)
    assert budget["max_lines"] == PROJECT_AGENT_HANDOFF_BUDGET["max_lines"], (label, budget)
    assert budget["max_chars"] == PROJECT_AGENT_HANDOFF_BUDGET["max_chars"], (label, budget)


def collect_fixture_status(root: Path, registry_path: Path) -> tuple[dict, str]:
    payload = collect_status(
        registry_path=registry_path,
        runtime_root_override=str(root / "runtime"),
        scan_roots=[root / "project"],
        limit=5,
    )
    return payload, render_status_markdown(payload)


def append_explicit_delivery_refresh(root: Path, registry_path: Path) -> dict:
    command = [
        sys.executable,
        "-m",
        "loopx.cli",
        "--registry",
        str(registry_path),
        "--runtime-root",
        str(root / "runtime"),
        "--format",
        "json",
        "refresh-state",
        "--goal-id",
        DELIVERY_GOAL_ID,
        "--classification",
        EXPLICIT_REFRESH_CLASSIFICATION,
        "--recommended-action",
        DELIVERY_ACTION,
        "--delivery-batch-scale",
        "multi_surface",
        "--delivery-outcome",
        "outcome_progress",
    ]
    dry_run = subprocess.run(
        [*command, "--dry-run", "--no-global-sync"],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    dry_payload = json.loads(dry_run.stdout)
    assert dry_payload["appended"] is False, dry_payload
    assert dry_payload["delivery_batch_scale"] == "multi_surface", dry_payload
    assert dry_payload["delivery_outcome"] == "outcome_progress", dry_payload

    result = subprocess.run(
        [*command, "--no-global-sync"],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    payload = json.loads(result.stdout)
    assert payload["appended"] is True, payload
    assert payload["delivery_batch_scale"] == "multi_surface", payload
    assert payload["delivery_outcome"] == "outcome_progress", payload
    index_lines = (root / "runtime" / "goals" / DELIVERY_GOAL_ID / "runs" / "index.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    latest_index = json.loads(index_lines[-1])
    assert latest_index["delivery_batch_scale"] == "multi_surface", latest_index
    assert latest_index["delivery_outcome"] == "outcome_progress", latest_index
    return payload


def assert_operator_gate_waits_for_user(payload: dict, markdown: str, *, status: str, action: str) -> None:
    items = payload["attention_queue"]["items"]
    assert len(items) == 1, items
    item = items[0]
    assert item["goal_id"] == "planned-main-control", item
    assert item["status"] == status, item
    assert item["waiting_on"] == "user_or_controller", item
    assert item["recommended_action"] == action, item
    assert "agent_command" not in item, item
    assert "operator_question" in item, item
    assert "agent_command:" not in markdown, markdown
    assert "operator_gate_dry_run" not in markdown, markdown


def assert_promotion_readiness_summary_markdown() -> None:
    markdown = render_status_markdown({
        "ok": True,
        "registry": "./fixtures/registry.json",
        "runtime_root": "./fixtures/runtime",
        "goal_count": 2,
        "run_count": 5,
        "contract": {"ok": True, "summary": {"errors": 0, "warnings": 0, "checks": 0}},
        "global_registry": {"available": False, "summary": {}},
        "promotion_readiness_summary": {
            "available": True,
            "source": "run_history",
            "goal_id": "loopx-meta",
            "generated_at": "2026-06-01T00:00:00+00:00",
            "classification": "canary_promotion_readiness_smoke_group",
            "delivery_outcome": "primary_goal_outcome",
            "json_exists": True,
            "markdown_exists": True,
            "freshness_status": "stale",
            "age_hours": 25.0,
            "requires_readiness_run": True,
            "freshness_window_hours": 24,
            "sample_run_count": 1,
        },
        "attention_queue": {
            "item_count": 0,
            "needs_user_or_controller": 0,
            "needs_controller": 0,
            "needs_codex": 0,
            "watching_external_evidence": 0,
            "items": [],
        },
        "run_history": {"goals": []},
    })

    assert "## Promotion Readiness Summary" in markdown, markdown
    assert "source=run_history" in markdown, markdown
    assert "available=True" in markdown, markdown
    assert "freshness=stale" in markdown, markdown
    assert "age_hours=25.0" in markdown, markdown
    assert "requires_readiness_run=True" in markdown, markdown
    assert "window_hours=24" in markdown, markdown
    assert "goal=loopx-meta" in markdown, markdown
    assert "classification=canary_promotion_readiness_smoke_group" in markdown, markdown
    assert "artifacts=True/True" in markdown, markdown


def assert_promotion_gate_summary_markdown() -> None:
    markdown = render_status_markdown({
        "ok": True,
        "registry": "./fixtures/registry.json",
        "runtime_root": "./fixtures/runtime",
        "goal_count": 2,
        "run_count": 5,
        "contract": {"ok": True, "summary": {"errors": 0, "warnings": 0, "checks": 0}},
        "global_registry": {"available": False, "summary": {}},
        "promotion_gate": {
            "ok": True,
            "gate": "promotion_readiness",
            "gate_state": "ready",
            "can_promote": True,
            "should_warn": False,
            "non_blocking": True,
            "recommended_action": "promotion readiness is fresh",
            "readiness": {
                "freshness_status": "fresh",
                "requires_readiness_run": False,
                "generated_at": "2026-06-01T00:00:00+00:00",
                "age_hours": 0.25,
            },
        },
        "attention_queue": {
            "item_count": 0,
            "needs_user_or_controller": 0,
            "needs_controller": 0,
            "needs_codex": 0,
            "watching_external_evidence": 0,
            "items": [],
        },
        "run_history": {"goals": []},
    })

    assert "## Promotion Gate" in markdown, markdown
    assert "state=ready" in markdown, markdown
    assert "can_promote=True" in markdown, markdown
    assert "should_warn=False" in markdown, markdown
    assert "non_blocking=True" in markdown, markdown
    assert "freshness=fresh" in markdown, markdown
    assert "requires_readiness_run=False" in markdown, markdown
    assert "generated_at=2026-06-01T00:00:00+00:00" in markdown, markdown
    assert "age_hours=0.25" in markdown, markdown
    assert "action=promotion readiness is fresh" in markdown, markdown

    warning_markdown = render_status_markdown({
        "ok": True,
        "registry": "./fixtures/registry.json",
        "runtime_root": "./fixtures/runtime",
        "goal_count": 2,
        "run_count": 5,
        "contract": {"ok": True, "summary": {"errors": 0, "warnings": 0, "checks": 0}},
        "global_registry": {"available": False, "summary": {}},
        "promotion_gate": {
            "ok": True,
            "gate": "promotion_readiness",
            "gate_state": "warning",
            "can_promote": False,
            "should_warn": True,
            "non_blocking": True,
            "recommended_action": "python3 examples/canary/canary-promotion-readiness-smoke.py",
            "warning_message": "promotion-readiness evidence is missing; generated_at=none",
            "readiness": {
                "freshness_status": "missing",
                "requires_readiness_run": True,
                "generated_at": None,
                "age_hours": None,
            },
        },
        "attention_queue": {
            "item_count": 0,
            "needs_user_or_controller": 0,
            "needs_controller": 0,
            "needs_codex": 0,
            "watching_external_evidence": 0,
            "items": [],
        },
        "run_history": {"goals": []},
    })

    assert "## Promotion Gate" in warning_markdown, warning_markdown
    assert "state=warning" in warning_markdown, warning_markdown
    assert "can_promote=False" in warning_markdown, warning_markdown
    assert "should_warn=True" in warning_markdown, warning_markdown
    assert "freshness=missing" in warning_markdown, warning_markdown
    assert "requires_readiness_run=True" in warning_markdown, warning_markdown
    assert "action=python3 examples/canary/canary-promotion-readiness-smoke.py" in warning_markdown, warning_markdown
    assert "warning: promotion-readiness evidence is missing; generated_at=none" in warning_markdown, warning_markdown


def assert_promotion_readiness_full_scan_fallback() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-promotion-readiness-full-scan-") as raw_tmp:
        root = Path(raw_tmp)
        runtime = root / "runtime"
        runs_dir = runtime / "goals" / "loopx-meta" / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        readiness_json = runs_dir / "2026-01-01T00-00-00-readiness.json"
        readiness_markdown = runs_dir / "2026-01-01T00-00-00-readiness.md"
        readiness_json.write_text("{}", encoding="utf-8")
        readiness_markdown.write_text("# readiness\n", encoding="utf-8")
        (runs_dir / "index.jsonl").write_text(
            json.dumps(
                {
                    "generated_at": "2026-01-01T00:00:00+00:00",
                    "goal_id": "loopx-meta",
                    "classification": "canary_promotion_readiness_smoke_group",
                    "delivery_batch_scale": "multi_surface",
                    "delivery_outcome": "primary_goal_outcome",
                    "recommended_action": "fixture readiness evidence",
                    "json_path": str(readiness_json),
                    "markdown_path": str(readiness_markdown),
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        summary = build_status_runtime_summaries(
            history={"runs": [{"classification": "quota_slot_spent", "generated_at": "2026-01-02T00:00:00+00:00"}]},
            queue={"items": []}, runtime_root=runtime, goal_id_filter=None, display_limit=1, todo_index_limit=1,
        )["promotion_readiness_summary"]
        assert summary["available"] is True, summary
        assert summary["source"] == "goal_run_history_legacy", summary
        assert summary["evidence_scope"] == "goal_run_history", summary
        assert summary["sample_run_count"] == 0, summary
        assert summary["classification"] == "canary_promotion_readiness_smoke_group", summary
        assert summary["delivery_outcome"] == "primary_goal_outcome", summary
        assert summary["json_exists"] is True, summary
        assert summary["markdown_exists"] is True, summary


def assert_promotion_readiness_warning_in_quota_guard() -> None:
    goal_id = "loopx-meta"

    def status_payload(readiness_summary: dict) -> dict:
        return {
            "ok": True,
            "registry": "./fixtures/registry.json",
            "runtime_root": "./fixtures/runtime",
            "goal_count": 1,
            "run_count": 1,
            "attention_queue": {
                "items": [
                    {
                        "goal_id": goal_id,
                        "status": "promotion_readiness_guard_fixture",
                        "waiting_on": "codex",
                        "severity": "action",
                        "recommended_action": "continue release-readiness guard fixture",
                        "source": "fixture",
                        "quota": {
                            "compute": 1,
                            "window_hours": 24,
                            "slot_minutes": 1,
                            "allowed_slots": 1440,
                            "spent_slots": 0,
                            "state": "eligible",
                            "reason": "fixture eligible quota",
                        },
                    }
                ],
            },
            "run_history": {
                "goals": [
                    {
                        "id": goal_id,
                        "status": "promotion_readiness_guard_fixture",
                        "registry_member": True,
                        "latest_runs": [],
                    }
                ]
            },
            "promotion_readiness_summary": readiness_summary,
        }

    stale_payload = build_quota_should_run(
        status_payload(
            {
                "available": True,
                "source": "run_history",
                "goal_id": goal_id,
                "generated_at": "2026-06-01T00:00:00+00:00",
                "classification": "canary_promotion_readiness_smoke_group",
                "freshness_status": "stale",
                "requires_readiness_run": True,
                "freshness_window_hours": 24,
                "age_hours": 25.0,
                "sample_run_count": 1,
                "json_exists": True,
                "markdown_exists": True,
            }
        ),
        goal_id=goal_id,
    )
    assert stale_payload["should_run"] is True, stale_payload
    stale_warning = stale_payload["promotion_readiness_warning"]
    assert stale_warning["freshness_status"] == "stale", stale_warning
    assert stale_warning["requires_readiness_run"] is True, stale_warning
    assert stale_warning["age_hours"] == 25.0, stale_warning
    stale_markdown = render_quota_should_run_markdown(stale_payload)
    assert "promotion_readiness_warning: status=stale requires_readiness_run=True" in stale_markdown, stale_markdown
    assert (
        "promotion_readiness_action: python3 examples/canary/canary-promotion-readiness-smoke.py"
        in stale_markdown
    ), stale_markdown
    assert "promotion-readiness-smoke.py --no-write-evidence" not in stale_markdown, stale_markdown
    assert "promotion_readiness_evidence: goal=loopx-meta" in stale_markdown, stale_markdown
    assert "age_hours=25.0" in stale_markdown, stale_markdown
    assert "artifacts=True/True" in stale_markdown, stale_markdown

    missing_payload = build_quota_should_run(
        status_payload(
            {
                "available": False,
                "source": "run_history",
                "reason": "no canary promotion readiness run found in sampled history",
                "freshness_status": "missing",
                "requires_readiness_run": True,
                "freshness_window_hours": 24,
                "age_hours": None,
                "sample_run_count": 0,
            }
        ),
        goal_id=goal_id,
    )
    assert missing_payload["should_run"] is True, missing_payload
    missing_warning = missing_payload["promotion_readiness_warning"]
    assert missing_warning["freshness_status"] == "missing", missing_warning
    assert missing_warning["available"] is False, missing_warning
    missing_markdown = render_quota_should_run_markdown(missing_payload)
    assert "promotion_readiness_warning: status=missing requires_readiness_run=True" in missing_markdown, missing_markdown
    assert (
        "promotion_readiness_action: python3 examples/canary/canary-promotion-readiness-smoke.py"
        in missing_markdown
    ), missing_markdown
    assert "promotion-readiness-smoke.py --no-write-evidence" not in missing_markdown, missing_markdown
    assert "promotion_readiness_reason: no canary promotion readiness run found in sampled history" in missing_markdown, missing_markdown

    fresh_payload = build_quota_should_run(
        status_payload(
            {
                "available": True,
                "source": "run_history",
                "goal_id": goal_id,
                "generated_at": "2026-06-01T00:00:00+00:00",
                "classification": "canary_promotion_readiness_smoke_group",
                "freshness_status": "fresh",
                "requires_readiness_run": False,
                "freshness_window_hours": 24,
                "age_hours": 1.0,
                "sample_run_count": 1,
                "json_exists": True,
                "markdown_exists": True,
            }
        ),
        goal_id=goal_id,
    )
    assert fresh_payload["should_run"] is True, fresh_payload
    assert "promotion_readiness_warning" not in fresh_payload, fresh_payload


def assert_status_contract_health_projection() -> None:
    projection = build_contract_health_projection(
        {
            "summary": {"errors": 0, "warnings": 4, "checks": 8},
            "errors": [],
            "warnings": [
                "fixture-goal: duplicate index rows raw=2 unique=1 unexpected=1",
                "fixture-goal: stale projection warning A",
                "fixture-goal: stale projection warning B",
                "fixture-goal: stale projection warning C",
            ],
        }
    )
    assert projection["contract_summary"] == {"errors": 0, "warnings": 4, "checks": 8}, projection
    assert projection["contract_warnings_total_count"] == 4, projection
    assert projection["contract_warnings_truncated"] is True, projection
    assert len(projection["contract_warnings"]) == 3, projection
    payload = {
        "ok": True,
        "registry": "./fixtures/registry.json",
        "runtime_root": "./fixtures/runtime",
        "goal_count": 1,
        "run_count": 1,
        "status_contract": {
            "schema_version": 2,
            "minimum_dashboard_schema_version": 2,
            "producer": "loopx status",
        },
        "contract": {"ok": True, "summary": {"errors": 0, "warnings": 4, "checks": 8}},
        **projection,
        "global_registry": {"available": False, "summary": {}},
    }
    markdown = render_status_markdown(payload)
    assert "Status Contract Signals" in markdown, markdown
    assert "duplicate index rows raw=2 unique=1 unexpected=1" in markdown, markdown
    assert "contract_warnings_truncated: total=4" in markdown, markdown


def assert_quota_should_run(
    payload: dict,
    *,
    expected: bool,
    state: str,
    waiting_on: str,
    expect_operator_question: bool = True,
) -> dict:
    quota_payload = build_quota_should_run(payload, goal_id="planned-main-control")
    quota_markdown = render_quota_should_run_markdown(quota_payload)
    assert quota_payload["should_run"] is expected, quota_payload
    assert quota_payload["state"] == state, quota_payload
    assert quota_payload["waiting_on"] == waiting_on, quota_payload
    if expected:
        assert quota_payload["decision"] == "run", quota_payload
        assert quota_payload["agent_command"] == APPROVED_COMMAND, quota_payload
        assert f"agent_command: `{APPROVED_COMMAND}`" in quota_markdown, quota_markdown
    else:
        assert quota_payload["decision"] == "skip", quota_payload
        if state == "operator_gate":
            assert quota_payload["reason"] == "operator gate blocks gated delivery; safe non-gated steering may continue", quota_payload
            assert quota_payload["safe_bypass_allowed"] is True, quota_payload
            assert quota_payload["blocked_action_scope"] == "gated_delivery", quota_payload
            if expect_operator_question:
                assert quota_payload["operator_question"], quota_payload
                assert "建议回复格式" in quota_markdown, quota_markdown
            else:
                assert "operator_question" not in quota_payload, quota_payload
            assert quota_payload["gate_prompt"], quota_payload
            assert quota_payload["notify_user_on_gate"] is True, quota_payload
            assert "Gate Prompt" in quota_markdown, quota_markdown
            assert "safe_bypass_allowed: `True`" in quota_markdown, quota_markdown
        assert "agent_command" not in quota_payload, quota_payload
        assert "agent_command:" not in quota_markdown, quota_markdown
    return quota_payload


def assert_planned_preview_is_not_runnable(payload: dict, markdown: str) -> None:
    items = payload["attention_queue"]["items"]
    assert len(items) == 1, items
    item = items[0]
    assert item["goal_id"] == "planned-main-control", item
    assert item["waiting_on"] == "controller", item
    assert item["recommended_action"] == USER_TODO_TEXT, item
    assert item["project_asset"]["owner"] == "controller", item
    assert item["project_asset"]["gate"] == "active_state_user_gate", item
    assert item["project_asset"]["next_action"] == USER_TODO_TEXT, item
    assert "stop until the controller or owner resolves this gate" in item["project_asset"]["stop_condition"], item
    assert item["project_asset"]["user_todos"]["open"] == 1, item
    assert item["project_asset"]["agent_todos"]["open"] == 1, item
    assert item["project_asset"]["quota"]["compute"] == 1.0, item
    assert item["project_asset"]["quota"]["state"] == "operator_gate", item
    assert "agent_command" not in item, item
    assert "operator_gate_dry_run" not in item, item
    assert OLD_PLANNED_ACTION not in json.dumps(payload, ensure_ascii=False), payload
    assert OLD_PLANNED_ACTION not in markdown, markdown
    assert USER_TODO_TEXT in markdown, markdown
    assert "project_asset: owner=controller gate=active_state_user_gate" in markdown, markdown
    assert f"asset_next_action: {USER_TODO_TEXT}" in markdown, markdown
    assert "asset_todos: user_open=1 agent_open=1" in markdown, markdown
    assert f"asset_user_todo: {USER_TODO_TEXT}" in markdown, markdown
    assert f"asset_agent_todo: {AGENT_TODO_TEXT}" in markdown, markdown
    assert "asset_quota: compute=1.0 state=operator_gate" in markdown, markdown

    assert "operator_gate_dry_run" not in markdown, markdown

    quota_payload = assert_quota_should_run(
        payload,
        expected=False,
        state="operator_gate",
        waiting_on="controller",
        expect_operator_question=False,
    )
    assert quota_payload["status"] == "active_state_user_gate", quota_payload


def assert_project_local_status_excludes_runtime_orphans(registry_path: Path) -> None:
    root = registry_path.parents[2]
    orphan_goal_id = "unregistered-orphan-goal"
    append_orphan_runtime_fixture(root, goal_id=orphan_goal_id, generated_at="2026-01-01T00:05:00+00:00")
    payload, markdown = collect_fixture_status(root, registry_path)
    goal_ids = {item["id"] for item in payload["run_history"]["goals"]}
    queue_goal_ids = {item["goal_id"] for item in payload["attention_queue"]["items"]}
    assert goal_ids == {"planned-main-control"}, payload
    assert queue_goal_ids == {"planned-main-control"}, payload
    assert orphan_goal_id not in markdown, markdown
    quota_payload = build_quota_should_run(payload, goal_id="planned-main-control")
    assert quota_payload["plan_summary"]["health_blockers"] == 0, quota_payload


def assert_registry_attention_override(payload: dict, markdown: str) -> None:
    items = payload["attention_queue"]["items"]
    assert len(items) == 1, items
    item = items[0]
    assert item["goal_id"] == "planned-main-control", item
    assert item["status"] == REGISTRY_OVERRIDE_STATUS, item
    assert item["waiting_on"] == "user_or_controller", item
    assert item["source"] == "registry", item
    assert item["recommended_action"] == REGISTRY_OVERRIDE_ACTION, item
    assert item["project_asset"]["owner"] == "user_or_controller", item
    assert item["project_asset"]["gate"] == "operator_question", item
    assert item["project_asset"]["next_action"] == REGISTRY_OVERRIDE_ACTION, item
    assert item["project_asset"]["stop_condition"] == REGISTRY_OVERRIDE_HANDOFF, item
    assert item["project_asset"]["user_todos"]["open"] == 0, item
    assert item["project_asset"]["agent_todos"]["open"] == 0, item
    assert item["project_asset"]["quota"]["state"] == "operator_gate", item
    assert item["project_asset"]["latest_validation"]["classification"] == "state_refreshed", item
    assert item["operator_question"] == REGISTRY_OVERRIDE_QUESTION, item
    assert item["next_handoff_condition"] == REGISTRY_OVERRIDE_HANDOFF, item
    assert item["user_todos"]["open_count"] == 0, item
    assert item["user_todos"]["done_count"] == 2, item
    assert item["agent_todos"]["open_count"] == 0, item
    assert item["agent_todos"]["done_count"] == 1, item
    assert "agent_command" not in item, item
    assert REGISTRY_OVERRIDE_STATUS in markdown, markdown
    assert REGISTRY_OVERRIDE_ACTION in markdown, markdown
    assert "project_asset: owner=user_or_controller gate=operator_question" in markdown, markdown
    assert f"asset_next_action: {REGISTRY_OVERRIDE_ACTION}" in markdown, markdown
    assert "asset_todos: user_open=0 agent_open=0" in markdown, markdown
    assert f"asset_user_todo: {USER_TODO_TEXT}" not in markdown, markdown
    assert f"asset_agent_todo: {AGENT_TODO_TEXT}" not in markdown, markdown
    assert "latest_validation: classification=state_refreshed" in markdown, markdown
    assert f"next_user_todo: {USER_TODO_TEXT}" not in markdown, markdown
    assert f"next_agent_todo: {AGENT_TODO_TEXT}" not in markdown, markdown
    assert "state_refreshed" in json.dumps(payload["run_history"], ensure_ascii=False), payload
    assert_quota_should_run(
        payload,
        expected=False,
        state="operator_gate",
        waiting_on="user_or_controller",
    )


def assert_connected_delivery_custom_run_stays_runnable(payload: dict, markdown: str) -> None:
    items = payload["attention_queue"]["items"]
    assert len(items) == 1, items
    item = items[0]
    assert item["goal_id"] == DELIVERY_GOAL_ID, item
    assert item["status"] == "delivery_ranker_readiness_batch", item
    assert item["waiting_on"] == "codex", item
    assert item["source"] == "latest_run", item
    assert item["recommended_action"] == DELIVERY_ACTION, item
    assert item["project_asset"]["owner"] == "codex", item
    assert item["project_asset"]["next_action"] == DELIVERY_ACTION, item
    assert item["project_asset"]["execution_profile"]["minimum_scale"] == "implementation", item
    assert item["project_asset"]["execution_profile"]["degradation_policy"]["small_scale_streak_threshold"] == 3, item
    assert item["project_asset"]["orchestration"]["mode"] == "multi_subagent", item
    assert item["project_asset"]["orchestration"]["max_children"] == 2, item
    assert item["project_asset"]["agent_todos"]["open"] == 1, item
    assert item["agent_todos"]["open_count"] == 1, item
    assert item["agent_todos"]["items"][0]["text"] == DELIVERY_AGENT_TODO, item
    readiness = item["handoff_readiness"]
    assert readiness["handoff_status"] == "post_handoff_run_seen", readiness
    assert readiness["post_handoff_run_seen"] is True, readiness
    assert "handoff_ready_at" not in readiness, readiness
    assert readiness["post_handoff_latest_run"]["classification"] == "delivery_ranker_readiness_batch", readiness
    assert readiness["post_handoff_latest_run"]["delivery_batch_scale"] == "multi_surface", readiness
    assert readiness["post_handoff_latest_run"]["delivery_outcome"] == "outcome_progress", readiness
    assert readiness["post_handoff_recent_runs"][0]["delivery_batch_scale"] == "multi_surface", readiness
    assert readiness["post_handoff_recent_runs"][0]["delivery_outcome"] == "outcome_progress", readiness
    assert readiness["post_handoff_small_scale_streak"] == 0, readiness
    assert readiness["post_handoff_outcome_gap_streak"] == 0, readiness
    assert_handoff_budget_contract(readiness, "connected delivery status readiness")
    assert "delivery_ranker_readiness_batch" in markdown, markdown
    assert "handoff_interface_budget: mode=project_agent_handoff max_lines=16 max_chars=1800" in markdown, markdown
    assert "handoff_state: status=post_handoff_run_seen post_handoff_run_seen=True ready_at=" in markdown, markdown
    assert "post_handoff_run: classification=delivery_ranker_readiness_batch" in markdown, markdown
    assert "scale=multi_surface" in markdown, markdown
    assert "outcome=outcome_progress" in markdown, markdown
    assert (
        "post_handoff_recent_scales: multi_surface small_streak=0 "
        "outcome=outcome_progress turn_kind=compact_evidence outcome_gap_streak=0"
    ) in markdown, markdown
    assert "execution_profile: cadence=bounded_progress_segment minimum=implementation" in markdown, markdown
    assert "orchestration: mode=multi_subagent spawn_allowed=True max_children=2" in markdown, markdown
    assert f"asset_agent_todo: {DELIVERY_AGENT_TODO}" in markdown, markdown

    quota_payload = build_quota_should_run(payload, goal_id=DELIVERY_GOAL_ID)
    assert quota_payload["should_run"] is True, quota_payload
    assert quota_payload["state"] == "eligible", quota_payload
    assert quota_payload["waiting_on"] == "codex", quota_payload
    assert quota_payload["agent_todo_summary"]["open_count"] == 1, quota_payload
    assert quota_payload["goal_boundary"]["adapter"]["status"] == "connected-delivery", quota_payload
    assert quota_payload["goal_boundary"]["write_scope"] == ["src/**", "tests/**"], quota_payload
    assert quota_payload["execution_profile"]["minimum_scale"] == "implementation", quota_payload
    assert quota_payload["goal_boundary"]["execution_profile"]["minimum_scale"] == "implementation", quota_payload
    assert quota_payload["goal_boundary"]["orchestration"]["mode"] == "multi_subagent", quota_payload
    assert quota_payload["goal_boundary"]["orchestration"]["max_children"] == 2, quota_payload
    assert (
        quota_payload["handoff_readiness"]["post_handoff_latest_run"]["delivery_batch_scale"]
        == "multi_surface"
    ), quota_payload
    assert (
        quota_payload["handoff_readiness"]["post_handoff_latest_run"]["delivery_outcome"]
        == "outcome_progress"
    ), quota_payload
    assert (
        quota_payload["handoff_readiness"]["post_handoff_recent_runs"][0]["delivery_batch_scale"]
        == "multi_surface"
    ), quota_payload
    assert quota_payload["handoff_readiness"]["post_handoff_small_scale_streak"] == 0, quota_payload
    assert quota_payload["handoff_readiness"]["post_handoff_outcome_gap_streak"] == 0, quota_payload
    assert_handoff_budget_contract(quota_payload["handoff_readiness"], "connected delivery quota readiness")
    assert quota_payload["heartbeat_recommendation"]["recommended_mode"] == "steering_audit_then_one_step", quota_payload
    quota_markdown = render_quota_should_run_markdown(quota_payload)
    assert "- handoff_interface_budget: mode=project_agent_handoff max_lines=16 max_chars=1800" in quota_markdown, quota_markdown
    assert "execution_profile: cadence=bounded_progress_segment minimum=implementation" in quota_markdown, quota_markdown
    assert "goal_boundary_orchestration: mode=multi_subagent spawn_allowed=True max_children=2" in quota_markdown, quota_markdown


def assert_source_registry_shadow_collapses_into_live_queue_item(payload: dict) -> None:
    registry_summary = payload["global_registry"]["summary"]
    assert registry_summary["action"] == 1, payload["global_registry"]
    assert "source_registry_missing" in json.dumps(payload["global_registry"], ensure_ascii=False), payload

    items = payload["attention_queue"]["items"]
    goal_items = [item for item in items if item["goal_id"] == DELIVERY_GOAL_ID]
    assert len(goal_items) == 1, items
    item = goal_items[0]
    assert item["status"] == "delivery_ranker_readiness_batch", item
    assert item["source"] == "latest_run", item
    assert not any(queue_item["status"] == "source_registry_missing" for queue_item in items), items
    shadows = item["global_registry_shadow_findings"]
    assert shadows[0]["kind"] == "source_registry_missing", shadows
    assert item["project_asset"]["global_registry_shadow_findings"]["open"] == 1, item

    quota_payload = build_quota_should_run(payload, goal_id=DELIVERY_GOAL_ID)
    assert quota_payload["should_run"] is True, quota_payload
    assert quota_payload["state"] == "eligible", quota_payload
    assert quota_payload["plan_summary"]["health_blockers"] == 0, quota_payload


def assert_connected_readonly_progress_run_stays_runnable(payload: dict, markdown: str) -> None:
    items = payload["attention_queue"]["items"]
    assert len(items) == 1, items
    item = items[0]
    assert item["goal_id"] == CONNECTED_READONLY_GOAL_ID, item
    assert item["status"] == CONNECTED_READONLY_CLASSIFICATION, item
    assert item["waiting_on"] == "codex", item
    assert item["source"] == "latest_run", item
    assert item["recommended_action"] == CONNECTED_READONLY_ACTION, item
    assert item["project_asset"]["owner"] == "codex", item
    assert item["project_asset"]["next_action"] == CONNECTED_READONLY_ACTION, item
    assert CONNECTED_READONLY_CLASSIFICATION in markdown, markdown
    assert "waiting_on=codex" in markdown, markdown

    quota_payload = build_quota_should_run(payload, goal_id=CONNECTED_READONLY_GOAL_ID)
    assert quota_payload["should_run"] is True, quota_payload
    assert quota_payload["state"] == "eligible", quota_payload
    assert quota_payload["waiting_on"] == "codex", quota_payload
    assert quota_payload["status"] == CONNECTED_READONLY_CLASSIFICATION, quota_payload
    assert quota_payload["recommended_action"] == CONNECTED_READONLY_ACTION, quota_payload
    quota_markdown = render_quota_should_run_markdown(quota_payload)
    assert "should_run: `True`" in quota_markdown, quota_markdown
    assert f"status: `{CONNECTED_READONLY_CLASSIFICATION}`" in quota_markdown, quota_markdown


def assert_dependency_blockers_stay_separate(payload: dict, markdown: str) -> None:
    items_by_goal = {
        item["goal_id"]: item
        for item in payload["attention_queue"]["items"]
    }
    current_item = items_by_goal[DEPENDENCY_CURRENT_GOAL_ID]
    blocker_item = items_by_goal[DEPENDENCY_BLOCKER_GOAL_ID]
    assert current_item["waiting_on"] == "codex", current_item
    assert current_item["agent_todos"]["open_count"] == 2, current_item
    assert "user_todos" not in current_item, current_item
    blockers = current_item["dependency_blockers"]
    assert blockers["open_count"] == 1, blockers
    assert blockers["source"] == "attention_queue.user_todos", blockers
    assert blockers["items"][0]["goal_id"] == DEPENDENCY_BLOCKER_GOAL_ID, blockers
    assert blockers["items"][0]["text"] == DEPENDENCY_USER_TODO, blockers
    assert blocker_item["user_todos"]["open_count"] == 1, blocker_item
    assert "dependency_blockers" not in blocker_item, blocker_item
    assert "dependency_blockers: open=1 source=attention_queue.user_todos" in markdown, markdown
    assert f"dependency_user_todo: goal={DEPENDENCY_BLOCKER_GOAL_ID}" in markdown, markdown
    backlog = payload["attention_queue"]["autonomous_backlog_candidates"]
    assert backlog["open_count"] == 1, backlog
    assert backlog["source"] == "attention_queue.agent_todos", backlog
    assert backlog["task_class"] == "advancement_task", backlog
    assert backlog["items"][0]["goal_id"] == DEPENDENCY_CURRENT_GOAL_ID, backlog
    assert backlog["items"][0]["text"] == DEPENDENCY_AGENT_TODO, backlog
    assert DEPENDENCY_MONITOR_TODO not in [item["text"] for item in backlog["items"]], backlog
    assert (
        "autonomous_backlog_candidates: open=1 task_class=advancement_task source=attention_queue.agent_todos"
        in markdown
    ), markdown
    assert f"autonomous_candidate: goal={DEPENDENCY_CURRENT_GOAL_ID}" in markdown, markdown
    monitors = payload["attention_queue"]["autonomous_monitor_candidates"]
    assert monitors["open_count"] == 1, monitors
    assert monitors["task_class"] == "continuous_monitor", monitors
    assert monitors["items"][0]["goal_id"] == DEPENDENCY_CURRENT_GOAL_ID, monitors
    assert monitors["items"][0]["text"] == DEPENDENCY_MONITOR_TODO, monitors
    assert (
        "autonomous_monitor_candidates: open=1 task_class=continuous_monitor source=attention_queue.agent_todos"
        in markdown
    ), markdown
    assert f"autonomous_monitor_candidate: goal={DEPENDENCY_CURRENT_GOAL_ID}" in markdown, markdown
    quota_payload = build_quota_should_run(payload, goal_id=DEPENDENCY_CURRENT_GOAL_ID)
    assert quota_payload["should_run"] is True, quota_payload
    assert quota_payload["state"] == "eligible", quota_payload
    assert quota_payload["waiting_on"] == "codex", quota_payload
    assert quota_payload["autonomous_backlog_candidates"]["items"][0]["text"] == DEPENDENCY_AGENT_TODO
    assert quota_payload["autonomous_monitor_candidates"]["items"][0]["text"] == DEPENDENCY_MONITOR_TODO


def assert_connected_delivery_no_baseline_small_streak(payload: dict, markdown: str) -> None:
    items = payload["attention_queue"]["items"]
    assert len(items) == 1, items
    item = items[0]
    assert item["goal_id"] == DELIVERY_GOAL_ID, item
    assert item["project_asset"]["execution_profile"]["minimum_scale"] == "implementation", item
    assert item["project_asset"]["execution_profile"]["degradation_policy"]["small_scale_streak_threshold"] == 3, item
    readiness = item["handoff_readiness"]
    assert readiness["handoff_status"] == "post_handoff_run_seen", readiness
    assert readiness["post_handoff_run_seen"] is True, readiness
    assert "handoff_ready_at" not in readiness, readiness
    assert [run["delivery_batch_scale"] for run in readiness["post_handoff_recent_runs"]] == [
        "test_only",
        "test_only",
    ], readiness
    assert readiness["post_handoff_small_scale_streak"] == 2, readiness
    assert "post_handoff_recent_scales: test_only,test_only small_streak=2" in markdown, markdown

    quota_payload = build_quota_should_run(payload, goal_id=DELIVERY_GOAL_ID)
    quota_readiness = quota_payload["handoff_readiness"]
    assert [run["delivery_batch_scale"] for run in quota_readiness["post_handoff_recent_runs"]] == [
        "test_only",
        "test_only",
    ], quota_payload
    assert quota_readiness["post_handoff_small_scale_streak"] == 2, quota_payload
    quota_markdown = render_quota_should_run_markdown(quota_payload)
    assert "post_handoff_recent_scales: test_only,test_only small_streak=2" in quota_markdown, quota_markdown
    assert "execution_profile: cadence=bounded_progress_segment minimum=implementation" in quota_markdown, quota_markdown


def assert_connected_delivery_surface_loop(payload: dict, markdown: str) -> None:
    items = payload["attention_queue"]["items"]
    assert len(items) == 1, items
    item = items[0]
    assert item["quota"]["state"] == "focus_wait", item
    assert item["quota"]["blocked_action_scope"] == "delivery_outcome_floor", item
    assert item["quota"]["safe_bypass_allowed"] is True, item
    assert item["quota"]["safe_bypass_kind"] == "outcome_floor_recovery", item
    assert item["project_asset"]["quota"]["state"] == "focus_wait", item
    readiness = item["handoff_readiness"]
    assert readiness["ready"] is False, readiness
    assert readiness["codex_ready"] is False, readiness
    assert readiness["quota_state"] == "focus_wait", readiness
    assert [run["delivery_batch_scale"] for run in readiness["post_handoff_recent_runs"]] == [
        "implementation",
        "implementation",
        "implementation",
    ], readiness
    assert [run["delivery_outcome"] for run in readiness["post_handoff_recent_runs"]] == [
        "surface_only",
        "surface_only",
        "surface_only",
    ], readiness
    assert readiness["post_handoff_small_scale_streak"] == 0, readiness
    assert readiness["post_handoff_outcome_gap_streak"] == 3, readiness
    assert readiness["handoff_status"] == "post_handoff_run_seen", readiness
    assert "scale=implementation outcome=surface_only" in markdown, markdown
    assert "quota_state=focus_wait" in markdown, markdown
    assert "handoff_checks: pass=project_asset_backed,same_source_should_run,handoff_has_next_action,handoff_has_stop_condition,handoff_sanitized_surface fail=codex_ready" in markdown, markdown
    assert (
        "post_handoff_recent_scales: implementation,implementation,implementation "
        "small_streak=0 outcome=surface_only,surface_only,surface_only "
        "turn_kind=contract_only_preparation,contract_only_preparation,contract_only_preparation "
        "outcome_gap_streak=3"
    ) in markdown, markdown

    quota_payload = build_quota_should_run(payload, goal_id=DELIVERY_GOAL_ID)
    assert quota_payload["should_run"] is True, quota_payload
    assert quota_payload["decision"] == "safe_bypass_recovery", quota_payload
    assert quota_payload["recovery_delivery_allowed"] is True, quota_payload
    assert quota_payload["normal_delivery_allowed"] is False, quota_payload
    assert quota_payload["state"] == "focus_wait", quota_payload
    assert quota_payload["quota"]["blocked_action_scope"] == "delivery_outcome_floor", quota_payload
    assert quota_payload["quota"]["handoff_outcome_floor_block"] is True, quota_payload
    assert quota_payload["safe_bypass_allowed"] is True, quota_payload
    assert quota_payload["safe_bypass_kind"] == "outcome_floor_recovery", quota_payload
    assert quota_payload["quota"]["post_handoff_outcome_gap_streak"] == 3, quota_payload
    assert quota_payload["heartbeat_recommendation"]["recommended_mode"] == "outcome_floor_recovery", quota_payload
    assert quota_payload["heartbeat_recommendation"]["notify"] == "DONT_NOTIFY", quota_payload
    quota_readiness = quota_payload["handoff_readiness"]
    assert quota_readiness["post_handoff_outcome_gap_streak"] == 3, quota_payload
    quota_markdown = render_quota_should_run_markdown(quota_payload)
    assert "- decision: `safe_bypass_recovery`" in quota_markdown, quota_markdown
    assert "- should_run: `True`" in quota_markdown, quota_markdown
    assert "- recovery_delivery_allowed: `True`" in quota_markdown, quota_markdown
    assert "- state: `focus_wait`" in quota_markdown, quota_markdown
    assert "- safe_bypass_allowed: `True`" in quota_markdown, quota_markdown
    assert "- safe_bypass_kind: outcome_floor_recovery" in quota_markdown, quota_markdown
    assert "handoff outcome floor not met" in quota_markdown, quota_markdown
    assert "mode=outcome_floor_recovery notify=DONT_NOTIFY" in quota_markdown, quota_markdown
    assert "outcome_gap_streak=3" in quota_markdown, quota_markdown


def assert_delivery_semantics_require_structured_fields() -> None:
    def delivery_scale_signal(run: dict) -> str:
        return project_post_handoff_history([run])["post_handoff_latest_run"]["delivery_batch_scale"]

    assert (
        delivery_scale_signal(
            {
                "classification": "dashboard_home_browser_smoke_regression",
                "delivery_batch_scale": "multi_surface",
            }
        )
        == "multi_surface"
    )
    assert (
        delivery_scale_signal(
            {"classification": "side_bypass_validation_plan_source_shape_consumer_test"}
        )
        == "unknown"
    )
    assert (
        delivery_scale_signal({"classification": "owner_handoff_consumer_test"})
        == "unknown"
    )
    assert (
        delivery_scale_signal({"classification": "delivery_ranker_readiness_batch"})
        == "unknown"
    )
    assert (
        delivery_scale_signal({"classification": "feedback_reranker_adapter_slice"})
        == "unknown"
    )
    profile = {
        "outcome_floor": {
            "surface_streak_threshold": 2,
            "outcome_markers": ["macro_evidence", "evidence_segment", "ranker_fit", "eval_metric"],
            "surface_only_hints": ["forecast", "runbook", "queue", "fields"],
        }
    }
    assert (
        project_post_handoff_history([{"classification": "side_bypass_owner_drop_landing_forecast_implementation"}], profile)["post_handoff_latest_run"]["delivery_outcome"]
        == "unknown"
    )
    assert (
        project_post_handoff_history([{"classification": "side_bypass_ranker_fit_metric_implementation"}], profile)["post_handoff_latest_run"]["delivery_outcome"]
        == "unknown"
    )
    assert (
        project_post_handoff_history([{"classification": "side_bypass_macro_evidence_segment_implementation"}], profile)["post_handoff_latest_run"]["delivery_outcome"]
        == "unknown"
    )
    assert (
        project_post_handoff_history([{"classification": "status_refresh_without_marker", "delivery_outcome": "primary_goal_outcome"}], profile)["post_handoff_latest_run"]["delivery_outcome"]
        == "primary_goal_outcome"
    )


def assert_project_asset_secret_scanner_boundaries() -> None:
    assert project_asset_summary_is_public_safe(
        {
            "latest_validation": {
                "classification": "skill_delivery_hint_contract",
                "summary": "mask_delivery_hint_contract is also ordinary public text",
            }
        }
    )
    for value in (
        "s" + "k-" + "1234567890abcdef",
        "s" + "k_" + "1234567890abcdef",
        "a" + "k_" + "1234567890abcdef",
        "Bear" + "er " + "abcdefghijklmnop",
        "tok" + "en=" + "abcdefghijklmnop",
    ):
        assert not project_asset_summary_is_public_safe({"latest_validation": {"summary": value}}), value


def assert_explicit_delivery_refresh(payload: dict, markdown: str) -> None:
    item = payload["attention_queue"]["items"][0]
    assert item["goal_id"] == DELIVERY_GOAL_ID, item
    assert item["status"] == EXPLICIT_REFRESH_CLASSIFICATION, item
    readiness = item["handoff_readiness"]
    assert readiness["post_handoff_latest_run"]["classification"] == EXPLICIT_REFRESH_CLASSIFICATION, readiness
    assert readiness["post_handoff_latest_run"]["delivery_batch_scale"] == "multi_surface", readiness
    assert readiness["post_handoff_latest_run"]["delivery_outcome"] == "outcome_progress", readiness
    assert readiness["post_handoff_recent_runs"][0]["delivery_batch_scale"] == "multi_surface", readiness
    assert readiness["post_handoff_recent_runs"][0]["delivery_outcome"] == "outcome_progress", readiness
    assert readiness["post_handoff_small_scale_streak"] == 0, readiness
    assert readiness["post_handoff_outcome_gap_streak"] == 0, readiness
    assert "post_handoff_run: classification=dashboard_home_browser_smoke_regression" in markdown, markdown
    assert "scale=multi_surface" in markdown, markdown
    assert "outcome=outcome_progress" in markdown, markdown

    quota_payload = build_quota_should_run(payload, goal_id=DELIVERY_GOAL_ID)
    assert quota_payload["should_run"] is True, quota_payload
    assert quota_payload["handoff_readiness"]["post_handoff_latest_run"]["delivery_batch_scale"] == "multi_surface", quota_payload
    assert quota_payload["handoff_readiness"]["post_handoff_latest_run"]["delivery_outcome"] == "outcome_progress", quota_payload


def assert_stale_latest_run_automatically_reconciled(payload: dict, markdown: str) -> None:
    item = payload["attention_queue"]["items"][0]
    assert item["source"] == "active_state", item
    assert item["status"] == "active_state_user_gate", item
    assert item["waiting_on"] == "controller", item
    assert item["recommended_action"] == USER_TODO_TEXT, item
    assert "stale_latest_run_warning" not in item, item
    assert "stale_latest_run_warning" not in item["project_asset"], item
    assert "stale_latest_run_warning:" not in markdown, markdown
    historical = payload["run_history"]["goals"][0]["latest_status_run"]
    assert historical["generated_at"] == "2026-01-01T00:02:00+00:00", historical
    assert historical["classification"] == "state_refreshed", historical

    quota_payload = build_quota_should_run(payload, goal_id="planned-main-control")
    assert not quota_payload.get("stale_latest_run_warning"), quota_payload
    quota_markdown = render_quota_should_run_markdown(quota_payload)
    assert "stale_latest_run_action:" not in quota_markdown, quota_markdown
    packet = build_review_packet(payload, goal_id="planned-main-control", action_kind="codex")
    assert not packet.get("stale_latest_run_warning"), packet
    assert "【状态投影警告】" not in packet["packet"], packet


def assert_handoff_waiting_for_post_run(item: dict, markdown: str) -> None:
    readiness = item["handoff_readiness"]
    assert readiness["ready"] is True, readiness
    assert readiness["handoff_status"] == "ready_waiting_for_run", readiness
    assert readiness["post_handoff_run_seen"] is False, readiness
    assert readiness["handoff_ready_at"] == "2026-01-01T00:01:00+00:00", readiness
    assert readiness["handoff_ready_classification"] == "operator_gate_approved", readiness
    assert "post_handoff_latest_run" not in readiness, readiness
    assert (
        "handoff_state: status=ready_waiting_for_run "
        "post_handoff_run_seen=False ready_at=2026-01-01T00:01:00+00:00"
    ) in markdown, markdown
    assert "post_handoff_run:" not in markdown.split("## Run History")[0], markdown


def assert_post_handoff_run_seen(payload: dict, markdown: str) -> None:
    items = payload["attention_queue"]["items"]
    assert len(items) == 1, items
    item = items[0]
    assert item["goal_id"] == "planned-main-control", item
    assert item["status"] == POST_HANDOFF_CLASSIFICATION, item
    assert item["waiting_on"] == "codex", item
    assert item["recommended_action"] == POST_HANDOFF_ACTION, item
    readiness = item["handoff_readiness"]
    assert readiness["ready"] is True, readiness
    assert readiness["handoff_status"] == "post_handoff_run_seen", readiness
    assert readiness["post_handoff_run_seen"] is True, readiness
    assert readiness["handoff_ready_at"] == "2026-01-01T00:01:00+00:00", readiness
    assert readiness["handoff_ready_classification"] == "operator_gate_approved", readiness
    assert readiness["post_handoff_latest_run"]["classification"] == POST_HANDOFF_CLASSIFICATION, readiness
    assert readiness["post_handoff_latest_run"]["generated_at"] == "2026-01-01T00:01:45+00:00", readiness
    assert readiness["post_handoff_latest_run"]["delivery_batch_scale"] == "single_surface", readiness
    assert [run["delivery_batch_scale"] for run in readiness["post_handoff_recent_runs"]] == [
        "single_surface",
        "single_surface",
    ], readiness
    assert readiness["post_handoff_small_scale_streak"] == 2, readiness
    assert (
        "handoff_state: status=post_handoff_run_seen "
        "post_handoff_run_seen=True ready_at=2026-01-01T00:01:00+00:00"
    ) in markdown, markdown
    assert (
        "post_handoff_run: classification=read_only_project_map "
        "at=2026-01-01T00:01:45+00:00 scale=single_surface"
    ) in markdown, markdown
    assert "post_handoff_recent_scales: single_surface,single_surface small_streak=2" in markdown, markdown
    quota_payload = build_quota_should_run(payload, goal_id="planned-main-control")
    quota_markdown = render_quota_should_run_markdown(quota_payload)
    quota_readiness = quota_payload["handoff_readiness"]
    assert quota_readiness["handoff_status"] == "post_handoff_run_seen", quota_payload
    assert quota_readiness["post_handoff_run_seen"] is True, quota_payload
    assert quota_readiness["post_handoff_latest_run"]["classification"] == POST_HANDOFF_CLASSIFICATION, quota_payload
    assert quota_readiness["post_handoff_latest_run"]["delivery_batch_scale"] == "single_surface", quota_payload
    assert [run["delivery_batch_scale"] for run in quota_readiness["post_handoff_recent_runs"]] == [
        "single_surface",
        "single_surface",
    ], quota_payload
    assert quota_readiness["post_handoff_small_scale_streak"] == 2, quota_payload
    assert "handoff_state: status=post_handoff_run_seen post_handoff_run_seen=True" in quota_markdown, quota_markdown
    assert (
        "post_handoff_run: classification=read_only_project_map "
        "at=2026-01-01T00:01:45+00:00 scale=single_surface"
    ) in quota_markdown, quota_markdown
    assert "post_handoff_recent_scales: single_surface,single_surface small_streak=2" in quota_markdown, quota_markdown


def assert_static_dashboard_post_handoff_scale(payload: dict, root: Path) -> None:
    status_path = root / "status.json"
    html_path = root / "status.html"
    status_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    subprocess.run(
        [
            sys.executable,
            "examples/render-status-dashboard.py",
            str(status_path),
            str(html_path),
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    html = html_path.read_text(encoding="utf-8")
    assert "Post-handoff run" in html, html
    assert "scale=single_surface" in html, html
    assert "Recent scales" in html, html
    assert "single_surface, single_surface; small_streak 2" in html, html


def main() -> int:
    assert_missing_project_asset_markdown_fallback()
    assert_usage_summary_markdown()
    assert_event_ledger_summary_markdown()
    assert_promotion_readiness_summary_markdown()
    assert_promotion_gate_summary_markdown()
    assert_decision_freshness_summary_markdown()
    assert_status_contract_health_projection()
    with tempfile.TemporaryDirectory(prefix="loopx-status-smoke-") as tmp:
        root = Path(tmp)
        registry_path = write_planned_registry(root)
        assert_project_local_status_excludes_runtime_orphans(registry_path)
        payload, markdown = collect_fixture_status(root, registry_path)
        mark_planned_todos_done(root)
        append_operator_gate_fixture(
            root,
            decision="approve",
            generated_at="2026-01-01T00:01:00+00:00",
            recommended_action=APPROVED_ACTION,
        )
        approved_payload, approved_markdown = collect_fixture_status(root, registry_path)
        append_quota_slot_spend_fixture(root, generated_at="2026-01-01T00:01:30+00:00")
        post_spend_payload, post_spend_markdown = collect_fixture_status(root, registry_path)
        append_post_handoff_run_fixture(root, generated_at="2026-01-01T00:01:40+00:00")
        append_post_handoff_run_fixture(root, generated_at="2026-01-01T00:01:45+00:00")
        post_handoff_payload, post_handoff_markdown = collect_fixture_status(root, registry_path)
        assert_static_dashboard_post_handoff_scale(post_handoff_payload, root)
        append_operator_gate_fixture(
            root,
            decision="reject",
            generated_at="2026-01-01T00:02:00+00:00",
            recommended_action=REJECTED_ACTION,
        )
        rejected_payload, rejected_markdown = collect_fixture_status(root, registry_path)
        append_operator_gate_fixture(
            root,
            decision="defer",
            generated_at="2026-01-01T00:03:00+00:00",
            recommended_action=DEFERRED_ACTION,
        )
        deferred_payload, deferred_markdown = collect_fixture_status(root, registry_path)
    with tempfile.TemporaryDirectory(prefix="loopx-status-registry-override-smoke-") as tmp:
        root = Path(tmp)
        registry_path = write_planned_registry(root)
        mark_planned_todos_done(root)
        append_state_refreshed_fixture(root, generated_at="2026-01-01T00:04:00+00:00")
        set_registry_attention_override(registry_path)
        override_payload, override_markdown = collect_fixture_status(root, registry_path)
    with tempfile.TemporaryDirectory(prefix="loopx-status-connected-delivery-smoke-") as tmp:
        root = Path(tmp)
        delivery_registry_path = write_connected_delivery_registry(root)
        append_connected_delivery_fixture(root, generated_at="2026-01-01T00:05:00+00:00")
        delivery_payload, delivery_markdown = collect_fixture_status(root, delivery_registry_path)
    with tempfile.TemporaryDirectory(prefix="loopx-status-source-registry-shadow-") as tmp:
        root = Path(tmp)
        shadow_registry_path = write_connected_delivery_registry(root)
        write_global_source_registry_shadow(root, shadow_registry_path, goal_id=DELIVERY_GOAL_ID)
        append_connected_delivery_fixture(root, generated_at="2026-01-01T00:05:00+00:00")
        shadow_payload, _shadow_markdown = collect_fixture_status(root, shadow_registry_path)
    with tempfile.TemporaryDirectory(prefix="loopx-status-explicit-refresh-scale-") as tmp:
        root = Path(tmp)
        explicit_registry_path = write_connected_delivery_registry(root)
        append_explicit_delivery_refresh(root, explicit_registry_path)
        explicit_payload, explicit_markdown = collect_fixture_status(root, explicit_registry_path)
    with tempfile.TemporaryDirectory(prefix="loopx-status-stale-latest-run-") as tmp:
        root = Path(tmp)
        stale_projection_registry_path = write_planned_registry(root)
        append_stale_state_projection_fixture(root)
        stale_projection_payload, stale_projection_markdown = collect_fixture_status(root, stale_projection_registry_path)
    with tempfile.TemporaryDirectory(prefix="loopx-status-connected-readonly-progress-") as tmp:
        root = Path(tmp)
        readonly_registry_path = write_connected_readonly_registry(root)
        append_connected_readonly_progress_fixture(root, generated_at="2026-01-01T00:05:00+00:00")
        readonly_payload, readonly_markdown = collect_fixture_status(root, readonly_registry_path)
    with tempfile.TemporaryDirectory(prefix="loopx-status-dependency-blockers-") as tmp:
        root = Path(tmp)
        dependency_registry_path = write_dependency_blocker_registry(root)
        dependency_payload, dependency_markdown = collect_fixture_status(root, dependency_registry_path)
    with tempfile.TemporaryDirectory(prefix="loopx-status-connected-delivery-small-streak-") as tmp:
        root = Path(tmp)
        delivery_registry_path = write_connected_delivery_registry(root)
        append_connected_delivery_fixture(
            root,
            generated_at="2026-01-01T00:05:00+00:00",
            classification="delivery_owner_drop_shape_test",
            delivery_batch_scale="test_only",
        )
        append_connected_delivery_fixture(
            root,
            generated_at="2026-01-01T00:06:00+00:00",
            classification="delivery_active_blocker_snapshot_test",
            delivery_batch_scale="test_only",
        )
        small_streak_payload, small_streak_markdown = collect_fixture_status(root, delivery_registry_path)
    with tempfile.TemporaryDirectory(prefix="loopx-status-connected-delivery-surface-loop-") as tmp:
        root = Path(tmp)
        delivery_registry_path = write_connected_delivery_registry(root)
        append_connected_delivery_fixture(
            root,
            generated_at="2026-01-01T00:05:00+00:00",
            classification="delivery_owner_drop_landing_forecast_implementation",
            delivery_batch_scale="implementation",
            delivery_outcome="surface_only",
        )
        append_connected_delivery_fixture(
            root,
            generated_at="2026-01-01T00:06:00+00:00",
            classification="delivery_owner_drop_scenario_runbook_implementation",
            delivery_batch_scale="implementation",
            delivery_outcome="surface_only",
        )
        append_connected_delivery_fixture(
            root,
            generated_at="2026-01-01T00:07:00+00:00",
            classification="delivery_next_action_queue_owner_drop_fields_implementation",
            delivery_batch_scale="implementation",
            delivery_outcome="surface_only",
        )
        surface_loop_payload, surface_loop_markdown = collect_fixture_status(root, delivery_registry_path)

    assert_planned_preview_is_not_runnable(payload, markdown)
    approved_items = approved_payload["attention_queue"]["items"]
    assert len(approved_items) == 1, approved_items
    approved_item = approved_items[0]
    assert approved_item["goal_id"] == "planned-main-control", approved_item
    assert approved_item["status"] == "operator_gate_approved", approved_item
    assert approved_item["waiting_on"] == "codex", approved_item
    assert approved_item["recommended_action"] == APPROVED_ACTION, approved_item
    assert approved_item["project_asset"]["owner"] == "codex", approved_item
    assert approved_item["project_asset"]["gate"] == "none", approved_item
    assert approved_item["project_asset"]["next_action"] == APPROVED_ACTION, approved_item
    assert "command fails" in approved_item["project_asset"]["stop_condition"], approved_item
    assert approved_item["project_asset"]["latest_validation"]["classification"] == "operator_gate_approved", approved_item
    assert approved_item["agent_command"] == APPROVED_COMMAND, approved_item
    assert "operator_question" not in approved_item, approved_item
    assert "operator_gate_dry_run" not in approved_markdown, approved_markdown
    assert "latest_validation: classification=operator_gate_approved" in approved_markdown, approved_markdown
    assert f"agent_command: `{APPROVED_COMMAND}`" in approved_markdown, approved_markdown
    approved_run = approved_payload["run_history"]["goals"][0]["latest_runs"][0]
    resume_contract = approved_run["operator_gate_resume_contract"]
    assert resume_contract["version"] == "operator_gate_resume_contract_v0", resume_contract
    assert resume_contract["gate_id"] == "read_only_map_opt_in", resume_contract
    assert resume_contract["operator_decision"] == "approve", resume_contract
    assert "decision_point_rebase_only" in resume_contract["migration_or_rebase_result"], resume_contract
    assert "whole repo/worktree" in resume_contract["migration_or_rebase_result"], resume_contract
    assert "operator_gate_resume_contract: version=operator_gate_resume_contract_v0" in approved_markdown, approved_markdown
    assert_handoff_waiting_for_post_run(approved_item, approved_markdown)
    approved_quota_payload = assert_quota_should_run(
        approved_payload,
        expected=True,
        state="eligible",
        waiting_on="codex",
    )
    approved_quota_readiness = approved_quota_payload["handoff_readiness"]
    assert approved_quota_readiness["handoff_status"] == "ready_waiting_for_run", approved_quota_payload
    assert approved_quota_readiness["post_handoff_run_seen"] is False, approved_quota_payload
    approved_quota_markdown = render_quota_should_run_markdown(approved_quota_payload)
    assert "handoff_state: status=ready_waiting_for_run post_handoff_run_seen=False" in approved_quota_markdown, approved_quota_markdown
    post_spend_items = post_spend_payload["attention_queue"]["items"]
    assert len(post_spend_items) == 1, post_spend_items
    post_spend_item = post_spend_items[0]
    assert post_spend_item["status"] == "operator_gate_approved", post_spend_item
    assert post_spend_item["recommended_action"] == APPROVED_ACTION, post_spend_item
    assert "quota_slot_spent" in json.dumps(post_spend_payload["run_history"], ensure_ascii=False), post_spend_payload
    assert "quota_slot_spent" not in post_spend_markdown.split("## Run History")[0], post_spend_markdown
    assert_handoff_waiting_for_post_run(post_spend_item, post_spend_markdown)
    assert_post_handoff_run_seen(post_handoff_payload, post_handoff_markdown)
    assert_operator_gate_waits_for_user(
        rejected_payload,
        rejected_markdown,
        status="operator_gate_rejected",
        action=REJECTED_ACTION,
    )
    assert_quota_should_run(
        rejected_payload,
        expected=False,
        state="operator_gate",
        waiting_on="user_or_controller",
    )
    assert_operator_gate_waits_for_user(
        deferred_payload,
        deferred_markdown,
        status="operator_gate_deferred",
        action=DEFERRED_ACTION,
    )
    assert_quota_should_run(
        deferred_payload,
        expected=False,
        state="operator_gate",
        waiting_on="user_or_controller",
    )
    assert_registry_attention_override(override_payload, override_markdown)
    assert_connected_delivery_custom_run_stays_runnable(delivery_payload, delivery_markdown)
    assert_source_registry_shadow_collapses_into_live_queue_item(shadow_payload)
    assert_explicit_delivery_refresh(explicit_payload, explicit_markdown)
    assert_stale_latest_run_automatically_reconciled(stale_projection_payload, stale_projection_markdown)
    assert_connected_readonly_progress_run_stays_runnable(readonly_payload, readonly_markdown)
    assert_dependency_blockers_stay_separate(dependency_payload, dependency_markdown)
    assert_connected_delivery_no_baseline_small_streak(small_streak_payload, small_streak_markdown)
    assert_connected_delivery_surface_loop(surface_loop_payload, surface_loop_markdown)
    assert_delivery_semantics_require_structured_fields()
    assert_project_asset_secret_scanner_boundaries()
    assert_promotion_readiness_full_scan_fallback()
    assert_promotion_readiness_warning_in_quota_guard()
    assert_status_agent_lane_next_action_projection()
    assert_status_agent_lane_todo_summary_display_compaction()
    assert_status_agent_member_selected_lane_claim_survives_truncated_claim_list()
    assert_status_agent_lane_vision_lookback_survives_display_trim()
    assert_status_agent_lane_frontier_hint_projection()
    print("status-markdown-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
