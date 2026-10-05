from __future__ import annotations

import json
import runpy
from pathlib import Path

import pytest

from loopx.cli import main
from loopx.configure_goal import configure_goal
from loopx.chat_goal_configuration_api import _goal_capability_options
from loopx.capabilities.pr_review_queue.goal_configuration import (
    apply_change,
    configuration_summary,
    resolve_configuration,
)
from loopx.capabilities.pr_review_queue.machine_defaults import (
    normalize_pull_request_review_machine_defaults,
)


def test_ci_configuration_defaults_overrides_and_clear() -> None:
    machine = {
        "namespaces": {
            "pull_request_review": {
                "schema_version": "pull_request_review_machine_defaults_v0",
                "wait_for_ci": False,
                "review_priority": "owner-first",
            }
        }
    }
    assert resolve_configuration()["wait_for_ci"] is True
    assert resolve_configuration(machine_configuration=machine)["wait_for_ci"] is False
    goal = {}
    apply_change(goal, {"wait_for_ci": True}, clear=False)
    assert resolve_configuration(goal, machine)["wait_for_ci"] is True
    # An override is atomic, matching the generic capability editor contract.
    assert (
        resolve_configuration(goal, machine)["review_order"] == "forward"
    )
    apply_change(goal, None, clear=True)
    assert resolve_configuration(goal, machine)["wait_for_ci"] is False


@pytest.mark.parametrize("invalid", ["false", 0, None])
def test_ci_configuration_rejects_non_boolean_input(invalid) -> None:
    with pytest.raises(TypeError, match="boolean"):
        normalize_pull_request_review_machine_defaults(
            {
                "schema_version": "pull_request_review_machine_defaults_v0",
                "wait_for_ci": invalid,
            }
        )
    with pytest.raises(TypeError, match="boolean"):
        apply_change({}, {"wait_for_ci": invalid}, clear=False)


def test_goal_editor_save_clear_and_cli_observe_the_same_policy(
    tmp_path, capsys
) -> None:
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "common_runtime_root": str(tmp_path / "runtime"),
                "goals": [
                    {"id": "review-goal", "repo": str(tmp_path), "status": "active"},
                    {"id": "other-goal", "repo": str(tmp_path), "status": "active"},
                ],
            }
        )
    )
    options = _goal_capability_options("pull_request_review", {"wait_for_ci": False})
    saved = configure_goal(
        registry_path=registry, goal_id="review-goal", execute=True, **options
    )
    assert saved["written"] is True
    goals = json.loads(registry.read_text())["goals"]
    assert configuration_summary(goals[0])["wait_for_ci"] is False
    assert configuration_summary(goals[1]) is None
    fixtures = runpy.run_path(
        str(Path(__file__).parents[1] / "test_pr_review_github_scan.py")
    )
    pr = fixtures["_merge_ready_pr"]()
    pr["statusCheckRollup"] = [{"name": "test", "status": "IN_PROGRESS"}]
    pr["review_thread_summary"] = {
        "complete": True,
        "total_count": 0,
        "unresolved_count": 0,
    }
    fixture = tmp_path / "prs.json"
    fixture.write_text(json.dumps({"repository": "owner/repo", "pull_requests": [pr]}))
    common = [
        "--registry",
        str(registry),
        "--format",
        "json",
        "pr-review",
        "--fixture",
        str(fixture),
    ]
    for goal_id, expected_status, expected_wait in [
        ("review-goal", 0, False),
        ("other-goal", 1, True),
    ]:
        code = main(
            [
                *common,
                "--goal-id",
                goal_id,
                "--check-merge-readiness",
                "4110@" + "a" * 40,
            ]
        )
        packet = json.loads(capsys.readouterr().out)
        assert code == expected_status, packet
        assert packet["wait_for_ci"] is expected_wait
    assert main([*common, "--goal-id", "review-goal"]) == 0
    packet = json.loads(capsys.readouterr().out)
    assert packet["request"]["review_configuration"]["wait_for_ci"] is False
    assert "statusCheckRollup" not in str(
        packet["pull_requests"][0]["evidence_commands"]
    )
    contract = packet["agent_response_contract"]["review_execution_contract"]
    assert "not_consulted" in json.dumps(contract)
    cleared = configure_goal(
        registry_path=registry,
        goal_id="review-goal",
        execute=True,
        **_goal_capability_options("pull_request_review", None),
    )
    assert cleared["written"] is True
    assert configuration_summary(json.loads(registry.read_text())["goals"][0]) is None
