from __future__ import annotations

import json

import pytest

from loopx.control_plane.scheduler.scheduler_hint import (
    build_app_automation_scheduler_ack_hint,
)
from loopx.control_plane.scheduler.state import (
    CODEX_APP_STATEFUL_BACKOFF_STATE_KEY,
    CODEX_APP_SURFACE,
    SCHEDULER_STATE_SCHEMA_VERSION,
    load_scheduler_state,
    write_scheduler_state,
)

AGENT_ID = "codex-scheduler-ack-table"
EXPECTED_RRULE = "FREQ=MINUTELY;INTERVAL=30"
RESET_TOKEN = "reset-token"
IDENTITY_SIGNATURE = "identity-signature"
STATE_SCOPE_MUTATIONS = [
    ("schema_version", "scheduler_state_v999"),
    ("goal_id", "wrong-goal"),
    ("agent_id", "wrong-agent"),
    ("surface", "wrong-surface"),
    ("state_key", "scheduler.wrong.state"),
]


@pytest.mark.parametrize(
    ("field", "mutated_value"),
    STATE_SCOPE_MUTATIONS,
    ids=[field for field, _ in STATE_SCOPE_MUTATIONS],
)
def test_scheduler_state_scope_mutations_fail_closed(
    tmp_path,
    field: str,
    mutated_value: str,
) -> None:
    valid_state = {
        "schema_version": SCHEDULER_STATE_SCHEMA_VERSION,
        "goal_id": "goal-ack-decision-table",
        "agent_id": AGENT_ID,
        "surface": CODEX_APP_SURFACE,
        "state_key": CODEX_APP_STATEFUL_BACKOFF_STATE_KEY,
        "reset_token": RESET_TOKEN,
        "identity_signature": IDENTITY_SIGNATURE,
        "progression_index": 0,
        "progression_minutes": [30],
        "last_applied_rrule": EXPECTED_RRULE,
        "updated_at": "2026-01-01T12:00:00+00:00",
    }
    state_path = write_scheduler_state(
        tmp_path,
        valid_state,
        goal_id=valid_state["goal_id"],
        agent_id=valid_state["agent_id"],
    )
    mutated_state = {**valid_state, field: mutated_value}
    state_path.write_text(
        json.dumps(mutated_state, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    assert (
        load_scheduler_state(
            tmp_path,
            goal_id=valid_state["goal_id"],
            agent_id=valid_state["agent_id"],
        )
        is None
    )
    with pytest.raises(ValueError, match="target scope or schema"):
        write_scheduler_state(
            tmp_path,
            mutated_state,
            goal_id=valid_state["goal_id"],
            agent_id=valid_state["agent_id"],
        )


def test_scheduler_ack_hint_preserves_public_contract_and_runtime_capabilities() -> None:
    hint = build_app_automation_scheduler_ack_hint(
        goal_id="goal-ack-contract",
        agent_id=AGENT_ID,
        state_key=CODEX_APP_STATEFUL_BACKOFF_STATE_KEY,
        applied_rrule=EXPECTED_RRULE,
        reset_token=RESET_TOKEN,
        identity_signature=IDENTITY_SIGNATURE,
        available_capabilities=["shell", "network", "benchmark_runner"],
        host_match_observed=True,
    )

    assert {
        "schema_version": hint["schema_version"],
        "command": hint["command"],
        "execute": hint["execute"],
        "uses_current_hint": hint["uses_current_hint"],
        "no_spend": hint["no_spend"],
    } == {
        "schema_version": "app_automation_scheduler_ack_hint_v0",
        "command": "quota scheduler-ack-current",
        "execute": True,
        "uses_current_hint": True,
        "no_spend": True,
    }
    assert hint["args"]["available_capabilities"] == [
        "network",
        "benchmark_runner",
    ]
    assert hint["args"]["host_match_observed"] is True
    assert hint["cli_args"] == [
        "quota",
        "scheduler-ack-current",
        "--goal-id",
        "goal-ack-contract",
        "--agent-id",
        AGENT_ID,
        "-A",
        "--available-capability",
        "network",
        "--available-capability",
        "benchmark_runner",
        "--applied-rrule",
        EXPECTED_RRULE,
        "--host-match-observed",
        "--reset-token",
        RESET_TOKEN,
        "--identity-signature",
        IDENTITY_SIGNATURE,
        "--execute",
    ]
