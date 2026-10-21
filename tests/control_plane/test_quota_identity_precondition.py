from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx.cli import main
from loopx.control_plane.agents.identity import build_quota_agent_identity
from loopx.control_plane.quota.error_codes import (
    QuotaIdentityPrecondition,
    QuotaIdentityPreconditionError,
)
from loopx.control_plane.testing.canary_harness import (
    project_state_path,
    write_fixture_registry,
)


GOAL_ID = "quota-identity-precondition"
AGENT_ID = "agent-alpha"


def _write_state(project: Path) -> None:
    state_file = project_state_path(project, GOAL_ID)
    state_file.parent.mkdir(parents=True)
    state_file.write_text(
        "---\n"
        "status: active\n"
        "---\n\n"
        "# Quota identity precondition fixture\n\n"
        "## Objective\n\n"
        "Keep selected-registry identity admission explicit.\n\n"
        "## Next Action\n\n"
        "- Repair the selected registry roster.\n",
        encoding="utf-8",
    )


def test_selected_registry_missing_roster_returns_typed_public_recovery(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    global_project = tmp_path / "global-project"
    runtime_root = tmp_path / "runtime"
    selected_registry = project / ".loopx" / "registry.json"
    global_registry = runtime_root / "registry.global.json"
    _write_state(project)
    _write_state(global_project)
    write_fixture_registry(
        project=project,
        runtime_root=runtime_root,
        registry_path=selected_registry,
        goal_id=GOAL_ID,
        domain="quota-identity-precondition",
        adapter_kind="generic_project_goal_v0",
        registered_agents=None,
        extra_goal_fields={"coordination": {"agent_model": "peer_v1"}},
    )
    write_fixture_registry(
        project=global_project,
        runtime_root=runtime_root,
        registry_path=global_registry,
        goal_id=GOAL_ID,
        domain="quota-identity-precondition",
        adapter_kind="generic_project_goal_v0",
        registered_agents=[AGENT_ID],
    )

    exit_code = main(
        [
            "--format",
            "json",
            "--registry",
            str(selected_registry),
            "--runtime-root",
            str(runtime_root),
            "quota",
            "should-run",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["ok"] is False
    assert payload["should_run"] is False
    assert payload["agent_id"] == AGENT_ID
    assert payload["error_code"] == "quota_agent_registry_roster_missing"
    assert payload["status"] == "quota_identity_precondition_failed"
    assert payload["identity_precondition"] == "registered_agent_roster_present"
    assert payload["reason"] == (
        "selected registry goal is missing coordination.registered_agents"
    )
    assert "selected registry" in payload["recommended_action"]
    assert "--agent-id" in payload["recommended_action"]
    assert "verbose_debug" not in payload


@pytest.mark.parametrize(
    ("goal", "agent_id", "precondition", "error_code", "public_agent_id"),
    [
        (
            {},
            "../private-agent",
            QuotaIdentityPrecondition.PUBLIC_SAFE_AGENT_ID,
            "quota_agent_id_invalid",
            None,
        ),
        (
            {"coordination": {"agent_model": "peer_v1"}},
            AGENT_ID,
            QuotaIdentityPrecondition.REGISTERED_AGENT_ROSTER_PRESENT,
            "quota_agent_registry_roster_missing",
            AGENT_ID,
        ),
        (
            {"coordination": {"registered_agents": ["agent-beta"]}},
            AGENT_ID,
            QuotaIdentityPrecondition.REQUESTED_AGENT_REGISTERED,
            "quota_agent_not_registered",
            AGENT_ID,
        ),
    ],
)
def test_quota_identity_admission_uses_typed_public_preconditions(
    goal: dict[str, object],
    agent_id: str,
    precondition: QuotaIdentityPrecondition,
    error_code: str,
    public_agent_id: str | None,
) -> None:
    with pytest.raises(QuotaIdentityPreconditionError) as exc_info:
        build_quota_agent_identity(goal, agent_id=agent_id)

    assert exc_info.value.precondition is precondition
    assert exc_info.value.error_code == error_code
    assert exc_info.value.agent_id == public_agent_id
