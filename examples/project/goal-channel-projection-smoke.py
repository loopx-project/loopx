#!/usr/bin/env python3
"""Smoke-test the goal_channel_projection_v0 frontstage contract."""

from __future__ import annotations

import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.control_plane.goals.goal_channel_projection import (  # noqa: E402
    GOAL_CHANNEL_PROJECTION_SCHEMA_VERSION,
    build_goal_channel_projection,
)


RAW_LOG_PATH = "/" + "private/tmp/raw-run.log"
USER_PRIVATE_PATH = "/" + "Users/example/private-state.md"
RAW_TRANSCRIPT_VALUE = "full transcript" + " body"
CREDENTIAL_VALUE = "credential" + "-value"
SENSITIVE_VALUE = "secret" + "-value"


def assert_no_raw_values(payload: dict[str, object]) -> None:
    text = json.dumps(payload, sort_keys=True)
    forbidden_values = (
        RAW_LOG_PATH,
        USER_PRIVATE_PATH,
        RAW_TRANSCRIPT_VALUE,
        CREDENTIAL_VALUE,
        SENSITIVE_VALUE,
    )
    leaked = [value for value in forbidden_values if value in text]
    assert not leaked, leaked


def sample_status_item() -> dict[str, object]:
    return {
        "goal_id": "demo-goal",
        "domain": "demo",
        "status": "goal_running",
        "waiting_on": "codex",
        "recommended_action": "advance compact frontstage projection",
        "project_asset": {
            "display_name": "Demo Goal",
            "state_updated_at": "2026-06-20T00:00:00Z",
            "next_action": "advance compact frontstage projection",
            "quota": {
                "state": "eligible",
                "reason": "fixture quota",
                "spent_slots": 1,
                "allowed_slots": 10,
            },
            "user_todos": {
                "open_count": 0,
                "first_open_items": [],
            },
            "agent_todos": {
                "open_count": 1,
                "first_open_items": [
                    {
                        "todo_id": "todo_agent_1",
                        "title": "Implement the read-only projection fixture.",
                        "status": "open",
                        "priority": "P1",
                        "claimed_by": "codex-side-bypass",
                        "task_class": "advancement_task",
                    }
                ],
            },
        },
    }


def sample_quota_payload() -> dict[str, object]:
    return {
        "waiting_on": "codex",
        "status": "goal_running",
        "recommended_action": "advance compact frontstage projection",
        "quota": {
            "state": "eligible",
            "reason": "fixture quota",
            "spent_slots": 1,
            "allowed_slots": 10,
        },
        "interaction_contract": {
            "user_channel": {"action_required": False, "notify": "DONT_NOTIFY"},
            "agent_channel": {
                "must_attempt": True,
                "quiet_noop_allowed": False,
            },
        },
    }


def sample_run_history_goal() -> dict[str, object]:
    return {
        "id": "demo-goal",
        "latest_runs": [
            {
                "generated_at": "2026-06-20T00:01:00Z",
                "classification": "validated_progress",
                "health_check": "projection fixture passed",
            },
            {
                "generated_at": "2026-06-19T23:55:00Z",
                "classification": "quota_slot_spent",
                "health_check": "quota slot spend event public-safe",
            },
        ],
    }


def test_basic_projection() -> None:
    payload = build_goal_channel_projection(
        goal_id="demo-goal",
        status_item=sample_status_item(),
        status_payload={"generated_at": "2026-06-20T00:02:00Z"},
        quota_payload=sample_quota_payload(),
        run_history_goal=sample_run_history_goal(),
        review_packet={"generated_at": "2026-06-20T00:02:30Z"},
        artifacts=[
            {
                "kind": "doc",
                "label": "frontstage contract",
                "path": "docs/product/roadmaps/frontstage-channel-lease-roadmap.md",
            }
        ],
        generated_at="2026-06-20T00:03:00Z",
    )
    assert (
        payload["schema_version"] == GOAL_CHANNEL_PROJECTION_SCHEMA_VERSION
    ), payload
    assert payload["mode"] == "read_only", payload
    assert payload["goal_id"] == "demo-goal", payload
    assert payload["display_name"] == "Demo Goal", payload
    assert payload["waiting_on"] == "codex", payload
    assert payload["decision_frame"]["agent_action_required"] is True, payload
    assert payload["decision_frame"]["quiet_noop_allowed"] is False, payload
    assert payload["quota"]["state"] == "eligible", payload
    assert payload["agent_todos"][0]["claimed_by"] == "codex-side-bypass", payload
    # A display-only sample cannot manufacture canonical ownership.
    assert payload["active_leases"] == [], payload
    assert payload["coordination_observation"]["status"] == "unavailable", payload
    assert payload["coordination_authority"] == {"state": "unavailable"}, payload
    assert payload["truth_contract"]["write_authority"] == "none", payload
    assert (
        payload["source_refs"]["latest_run_generated_at"]
        == "2026-06-20T00:01:00Z"
    ), payload
    assert payload["recent_events"][0]["classification"] == "validated_progress", payload
    assert payload["truth_contract"]["event_ledger_is_source_of_truth"] is True, payload
    assert payload["truth_contract"]["projection_is_writable"] is False, payload
    assert_no_raw_values(payload)


def test_user_gate_projection() -> None:
    status_item = sample_status_item()
    status_item["project_asset"]["user_todos"] = {
        "open_count": 1,
        "first_open_items": [
            {
                "todo_id": "todo_user_1",
                "title": "Approve the bounded delivery packet.",
                "status": "open",
                "priority": "P0",
                "task_class": "user_gate",
            }
        ],
    }
    quota = sample_quota_payload()
    quota["interaction_contract"]["user_channel"]["action_required"] = True
    payload = build_goal_channel_projection(
        goal_id="demo-goal",
        status_item=status_item,
        quota_payload=quota,
        run_history_goal=sample_run_history_goal(),
    )
    assert payload["decision_frame"]["user_action_required"] is True, payload
    assert payload["user_todos"][0]["todo_id"] == "todo_user_1", payload
    assert payload["open_gates"][0]["kind"] == "user_channel", payload
    assert payload["open_gates"][0]["status"] == "action_required", payload
    assert_no_raw_values(payload)


def test_user_gate_projection_without_interaction_contract() -> None:
    status_item = sample_status_item()
    status_item["project_asset"]["user_todos"] = {
        "open_count": 1,
        "first_open_items": [
            {
                "todo_id": "todo_user_2",
                "title": "Approve the scoped cleanup.",
                "status": "open",
                "task_class": "user_gate",
            }
        ],
    }
    payload = build_goal_channel_projection(
        goal_id="demo-goal",
        status_item=status_item,
        quota_payload={"status": "active_state_user_gate"},
        run_history_goal=sample_run_history_goal(),
    )
    assert payload["decision_frame"]["user_action_required"] is True, payload
    assert payload["open_gates"][0]["kind"] == "user_todo", payload
    assert payload["open_gates"][0]["blocks"] == ["todo_user_2"], payload


def test_raw_private_material_is_warned_not_copied() -> None:
    status_item = sample_status_item()
    status_item["project_asset"]["raw_transcript"] = RAW_TRANSCRIPT_VALUE
    payload = build_goal_channel_projection(
        goal_id="demo-goal",
        status_item=status_item,
        quota_payload={
            **sample_quota_payload(),
            "credential_hint": CREDENTIAL_VALUE,
        },
        run_history_goal={
            "id": "demo-goal",
            "latest_runs": [
                {
                    "generated_at": "2026-06-20T00:01:00Z",
                    "classification": "raw_event_seen",
                    "health_check": "safe compact summary",
                    "raw_log_path": RAW_LOG_PATH,
                }
            ],
        },
        artifacts=[
            {
                "kind": "log",
                "label": "raw local artifact",
                "path": USER_PRIVATE_PATH,
            }
        ],
    )
    warnings = payload["source_warnings"]
    assert warnings, payload
    names = set(warnings[0]["key_names"])
    assert "raw_transcript" in names, payload
    assert "credential_hint" in names, payload
    assert "raw_log_path" in names, payload
    assert "path" in names, payload
    assert payload["artifacts"][0]["kind"] == "log", payload
    assert "path" not in payload["artifacts"][0], payload
    assert_no_raw_values(payload)


def main() -> int:
    test_basic_projection()
    test_user_gate_projection()
    test_user_gate_projection_without_interaction_contract()
    test_raw_private_material_is_warned_not_copied()
    print("goal-channel-projection-smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
