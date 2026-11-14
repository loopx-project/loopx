"""Unit tests for remote authority gateway and opt-in canary access."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from loopx.control_plane.coordination.remote_authority_gateway import (
    REMOTE_AUTHORITY_CANARY_GOAL_ID,
    check_remote_authority_gateway_status,
    get_remote_authority_projection,
    is_remote_authority_goal,
)
from loopx.control_plane.heartbeat.builder import build_heartbeat_prompt
from loopx.control_plane.quota.live_decision import build_live_quota_should_run_decision


def test_private_goals_default_disabled():
    # Private goals must never be treated as remote authority goals
    assert not is_remote_authority_goal("personal-loopx-maintenance")
    assert not is_remote_authority_goal("my-private-goal")
    assert not is_remote_authority_goal("loopx-quality-review")  # Disabled by default without explicit opt-in

    assert get_remote_authority_projection("personal-loopx-maintenance") is None
    assert check_remote_authority_gateway_status("personal-loopx-maintenance") is None


def test_canary_goal_opt_in():
    with patch.dict(os.environ, {"LOOPX_REMOTE_AUTHORITY_CANARY_ENABLED": "1", "LOOPX_AUTHORITY_CREDENTIAL": "test-credential-token"}):
        assert is_remote_authority_goal(REMOTE_AUTHORITY_CANARY_GOAL_ID)
        projection = get_remote_authority_projection(REMOTE_AUTHORITY_CANARY_GOAL_ID)
        assert projection is not None
        assert projection["canary"] is True
        assert projection["goal_id"] == REMOTE_AUTHORITY_CANARY_GOAL_ID
        assert projection["tenant_id"] == "tenant-canary"
        assert projection["handoff_mode"] == "hard_lease"
        # Secret credential must NEVER be present in projection
        assert "credential" not in projection
        assert "token" not in projection

        status = check_remote_authority_gateway_status(REMOTE_AUTHORITY_CANARY_GOAL_ID)
        assert status is not None
        assert status["ok"] is True
        assert status["status"] == "connected"


def test_canary_goal_fails_closed_without_credential():
    with patch.dict(os.environ, {"LOOPX_REMOTE_AUTHORITY_CANARY_ENABLED": "1"}, clear=True):
        assert is_remote_authority_goal(REMOTE_AUTHORITY_CANARY_GOAL_ID)
        status = check_remote_authority_gateway_status(REMOTE_AUTHORITY_CANARY_GOAL_ID)
        assert status is not None
        assert status["ok"] is False
        assert status["should_run"] is False
        assert status["status"] == "unavailable"
        assert status["reason_code"] == "remote_authority_credential_required"


def test_explicit_registry_remote_authority_goal():
    registry_goals = {
        "shared-team-goal": {
            "id": "shared-team-goal",
            "remote_authority": {
                "enabled": True,
                "endpoint": "https://coordination.internal.net:8443",
                "tenant_id": "team-infra",
                "store_identity": "postgresql:1234567890abcdef1234567890abcdef",
            },
        },
        "private-local-goal": {
            "id": "private-local-goal",
        },
    }

    assert is_remote_authority_goal("shared-team-goal", registry_goals)
    assert not is_remote_authority_goal("private-local-goal", registry_goals)

    proj = get_remote_authority_projection("shared-team-goal", registry_goals)
    assert proj is not None
    assert proj["endpoint"] == "https://coordination.internal.net:8443"
    assert proj["tenant_id"] == "team-infra"
    assert proj["canary"] is False


def test_heartbeat_prompt_incorporates_remote_gateway_for_canary(tmp_path: Path):
    # For private goals, remote_authority is absent from payload
    private_payload = build_heartbeat_prompt(
        goal_id="personal-loopx-maintenance",
        thin=True,
    )
    assert "remote_authority" not in private_payload

    # When canary is enabled
    with patch.dict(os.environ, {"LOOPX_REMOTE_AUTHORITY_CANARY_ENABLED": "1"}):
        canary_payload = build_heartbeat_prompt(
            goal_id=REMOTE_AUTHORITY_CANARY_GOAL_ID,
            thin=True,
        )
        assert "remote_authority" in canary_payload
        assert canary_payload["remote_authority"]["canary"] is True
        assert canary_payload["remote_authority"]["goal_id"] == REMOTE_AUTHORITY_CANARY_GOAL_ID
