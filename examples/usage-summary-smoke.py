#!/usr/bin/env python3
"""Smoke-test public-safe usage summary proxy fields."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx import status as status_module  # noqa: E402
from loopx.control_plane.quota.usage_summary import build_usage_summary as build_usage_summary_read_model  # noqa: E402
from loopx.history import collect_history  # noqa: E402


FORBIDDEN_USAGE_KEYS = {
    "token_count",
    "tokens",
    "thread_count",
    "threads",
    "raw_thread_logs",
    "raw_session_path",
    "session_transcript",
}


def write_registry(root: Path) -> Path:
    project = root / "project"
    runtime = root / "runtime"
    registry_path = project / ".loopx" / "registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    goals = []
    for goal_id in ("project-a", "project-b"):
        state_file = f".codex/goals/{goal_id}/ACTIVE_GOAL_STATE.md"
        (project / Path(state_file).parent).mkdir(parents=True, exist_ok=True)
        (project / state_file).write_text(
            "---\nstatus: active\nupdated_at: 2026-01-01T00:00:00+00:00\n---\n"
            f"\n# {goal_id}\n\n## Next Action\n\n- Continue one public-safe step.\n",
            encoding="utf-8",
        )
        goals.append(
            {
                "id": goal_id,
                "domain": "usage-summary-fixture",
                "status": "active",
                "repo": str(project),
                "state_file": state_file,
                "adapter": {"kind": "fixture_adapter_v0", "status": "connected-read-only"},
            }
        )
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "updated_at": "2026-01-01T00:00:00+00:00",
                "common_runtime_root": str(runtime),
                "goals": goals,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return registry_path


def append_run(
    runtime: Path,
    *,
    goal_id: str,
    generated_at: datetime,
    classification: str,
    quota_event: dict[str, Any] | None = None,
    human_reward: dict[str, Any] | None = None,
    operator_gate: dict[str, Any] | None = None,
    operator_gate_resume_contract: dict[str, Any] | None = None,
    delivery_batch_scale: str | None = None,
    delivery_outcome: str | None = None,
) -> None:
    run_dir = runtime / "goals" / goal_id / "runs"
    run_dir.mkdir(parents=True, exist_ok=True)
    stamp = generated_at.isoformat().replace("+00:00", "Z").replace(":", "-")
    json_path = run_dir / f"{stamp}-{classification}.json"
    markdown_path = run_dir / f"{stamp}-{classification}.md"
    record: dict[str, Any] = {
        "generated_at": generated_at.isoformat(),
        "goal_id": goal_id,
        "classification": classification,
        "recommended_action": "fixture action",
        "health_check": "fixture health",
    }
    if quota_event:
        record["quota_event"] = quota_event
    if human_reward:
        record["human_reward"] = human_reward
    if operator_gate:
        record["operator_gate"] = operator_gate
    if operator_gate_resume_contract:
        record["operator_gate_resume_contract"] = operator_gate_resume_contract
    if delivery_batch_scale:
        record["delivery_batch_scale"] = delivery_batch_scale
    if delivery_outcome:
        record["delivery_outcome"] = delivery_outcome
    json_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text("# Fixture run\n", encoding="utf-8")
    with (run_dir / "index.jsonl").open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    **record,
                    "json_path": str(json_path),
                    "markdown_path": str(markdown_path),
                },
                sort_keys=True,
            )
            + "\n"
        )


def assert_no_forbidden_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            assert key not in FORBIDDEN_USAGE_KEYS, key
            assert_no_forbidden_keys(child)
    elif isinstance(value, list):
        for child in value:
            assert_no_forbidden_keys(child)


def main() -> int:
    now = datetime.now(timezone.utc)
    with tempfile.TemporaryDirectory(prefix="loopx-usage-summary-") as tmp:
        root = Path(tmp)
        registry_path = write_registry(root)
        runtime = root / "runtime"
        append_run(runtime, goal_id="project-a", generated_at=now - timedelta(hours=1), classification="state_refreshed")
        # No quota_event: the spend ledger records no slot for a spend whose
        # event it cannot read, and the usage summary reports the same number.
        append_run(runtime, goal_id="project-a", generated_at=now - timedelta(minutes=30), classification="quota_slot_spent")
        append_run(
            runtime,
            goal_id="project-a",
            generated_at=now - timedelta(minutes=45),
            classification="operator_gate_approved",
            operator_gate={
                "recorded_at": (now - timedelta(minutes=45)).isoformat(),
                "gate": "demo-gate",
                "decision": "approved",
                "reason_summary": "fixture operator approval",
            },
        )
        append_run(runtime, goal_id="project-b", generated_at=now - timedelta(hours=2), classification="read_only_project_map")
        append_run(runtime, goal_id="project-b", generated_at=now - timedelta(minutes=90), classification="delivery_ranker_implementation")
        append_run(
            runtime,
            goal_id="project-b",
            generated_at=now - timedelta(hours=3),
            classification="quota_slot_spent",
            quota_event={"event_type": "quota_slot_spent", "source": "heartbeat", "slots": 2},
        )
        append_run(runtime, goal_id="project-b", generated_at=now - timedelta(days=8), classification="state_refreshed")
        append_run(
            runtime,
            goal_id="project-b",
            generated_at=now - timedelta(days=8, minutes=5),
            classification="human_reward_recorded",
            human_reward={
                "recorded_at": (now - timedelta(days=8, minutes=5)).isoformat(),
                "decision": "continue",
                "reward": "positive",
                "reason_summary": "fixture stale reward",
            },
        )
        append_run(
            runtime,
            goal_id="project-a",
            generated_at=now - timedelta(hours=25),
            classification="canary_promotion_readiness_smoke_group",
            delivery_batch_scale="multi_surface",
            delivery_outcome="primary_goal_outcome",
        )

        payload = status_module.collect_status(
            registry_path=registry_path,
            runtime_root_override=str(runtime),
            limit=20,
        )
        history = collect_history(
            registry_path=registry_path,
            runtime_root=runtime,
            goal_id=None,
            limit=20,
        )
        status_contract = payload["status_contract"]
        assert status_contract["schema_version"] == 2, status_contract
        assert status_contract["minimum_dashboard_schema_version"] == 2, status_contract
        assert status_contract["producer"] == "loopx status", status_contract
        assert status_contract["reload_hint"] == "scripts/macos-dashboard-launchagent.sh restart", status_contract

        usage = payload["usage_summary"]
        wrapper_usage = status_module.build_status_runtime_summaries(
            history=history,
            queue=payload["attention_queue"],
            runtime_root=runtime,
            goal_id_filter=None,
            display_limit=20,
            todo_index_limit=240,
        )["usage_summary"]
        direct_usage = build_usage_summary_read_model(
            history,
            parse_timestamp=status_module.parse_timestamp,
        )
        wrapper_usage["generated_at"] = usage["generated_at"]
        direct_usage["generated_at"] = usage["generated_at"]
        assert wrapper_usage == usage, (wrapper_usage, usage)
        assert direct_usage == usage, (direct_usage, usage)
        totals = usage["totals"]
        assert usage["available"] is True, usage
        assert usage["source"] == "run_history", usage
        assert usage["sample_run_count"] == 9, usage
        assert totals["runs_24h"] == 6, totals
        assert totals["runs_7d"] == 7, totals
        assert totals["quota_spend_slots_24h"] == 2, totals
        assert totals["quota_spend_slots_7d"] == 2, totals
        assert totals["automation_run_count_24h"] == 2, totals
        assert totals["automation_run_count_7d"] == 2, totals
        assert totals["progress_signal_run_count_24h"] == 3, totals
        assert totals["progress_signal_run_count_7d"] == 4, totals

        goals = {goal["goal_id"]: goal for goal in usage["goals"]}
        assert goals["project-a"]["runs_24h"] == 3, goals
        assert goals["project-a"]["runs_7d"] == 4, goals
        assert goals["project-b"]["runs_24h"] == 3, goals
        # project-a's spend run has no readable quota_event, so it contributes
        # no slot; project-b's carries a 2-slot event.
        assert goals["project-a"]["quota_spend_slots_24h"] == 0, goals
        assert goals["project-a"]["quota_spend_slots_7d"] == 0, goals
        assert goals["project-b"]["quota_spend_slots_24h"] == 2, goals
        assert goals["project-b"]["quota_spend_slots_7d"] == 2, goals
        assert goals["project-a"]["progress_signal_run_count_24h"] == 1, goals
        assert goals["project-a"]["progress_signal_run_count_7d"] == 2, goals
        assert goals["project-b"]["progress_signal_run_count_24h"] == 2, goals
        assert goals["project-a"]["project_share_24h"] == 0.5, goals
        assert goals["project-b"]["project_share_24h"] == 0.5, goals
        assert_no_forbidden_keys(usage)

        event_ledger = payload["event_ledger_summary"]
        event_totals = event_ledger["totals"]
        assert event_ledger["available"] is True, event_ledger
        assert event_ledger["source"] == "run_history", event_ledger
        assert event_ledger["sample_run_count"] == 9, event_ledger
        assert event_totals["events_24h"] == 6, event_totals
        assert event_totals["events_7d"] == 7, event_totals
        assert event_totals["by_class_24h"] == {
            "accounting": 2,
            "decision": 1,
            "evidence": 1,
            "state": 1,
            "work": 1,
        }, event_totals
        assert event_totals["by_class_7d"] == {
            "accounting": 2,
            "decision": 1,
            "evidence": 1,
            "state": 1,
            "work": 2,
        }, event_totals
        event_goals = {goal["goal_id"]: goal for goal in event_ledger["goals"]}
        assert event_goals["project-a"]["by_class_24h"]["state"] == 1, event_goals
        assert event_goals["project-a"]["by_class_24h"]["decision"] == 1, event_goals
        assert event_goals["project-a"]["by_class_24h"]["accounting"] == 1, event_goals
        assert event_goals["project-b"]["by_class_24h"]["evidence"] == 1, event_goals
        assert event_goals["project-b"]["by_class_24h"]["work"] == 1, event_goals
        assert event_goals["project-b"]["by_class_24h"]["accounting"] == 1, event_goals
        assert_no_forbidden_keys(event_ledger)

        promotion_readiness = payload["promotion_readiness_summary"]
        assert promotion_readiness["available"] is True, promotion_readiness
        assert promotion_readiness["source"] == "run_history", promotion_readiness
        assert promotion_readiness["sample_run_count"] == 1, promotion_readiness
        assert promotion_readiness["goal_id"] == "project-a", promotion_readiness
        assert promotion_readiness["classification"] == "canary_promotion_readiness_smoke_group", promotion_readiness
        assert promotion_readiness["delivery_outcome"] == "primary_goal_outcome", promotion_readiness
        assert promotion_readiness["freshness_status"] == "stale", promotion_readiness
        assert promotion_readiness["is_fresh"] is False, promotion_readiness
        assert promotion_readiness["requires_readiness_run"] is True, promotion_readiness
        assert promotion_readiness["freshness_window_hours"] == 24, promotion_readiness
        assert promotion_readiness["json_exists"] is True, promotion_readiness
        assert promotion_readiness["markdown_exists"] is True, promotion_readiness
        assert_no_forbidden_keys(promotion_readiness)

        decision_freshness = payload["decision_freshness_summary"]
        decision_summary = decision_freshness["summary"]
        assert decision_freshness["available"] is True, decision_freshness
        assert decision_freshness["source"] == "run_history", decision_freshness
        assert decision_freshness["sample_run_count"] == 9, decision_freshness
        assert decision_freshness["window_days"] == 7, decision_freshness
        assert decision_summary == {
            "decision_count": 2,
            "stale_count": 1,
            "rebase_required_count": 2,
            "fresh_count": 0,
        }, decision_summary
        decision_items = {
            (item["goal_id"], item["decision_kind"]): item
            for item in decision_freshness["items"]
        }
        assert decision_items[("project-a", "operator_gate")]["freshness_state"] == "rebase_required", decision_items
        assert decision_items[("project-a", "operator_gate")]["newer_event_count_7d"] == 1, decision_items
        assert decision_items[("project-b", "human_reward")]["freshness_state"] == "stale_rebase_required", decision_items
        assert decision_items[("project-b", "human_reward")]["stale_by_age"] is True, decision_items
        assert decision_items[("project-b", "human_reward")]["newer_event_count_7d"] == 3, decision_items
        assert_no_forbidden_keys(decision_freshness)

    print("usage-summary smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
