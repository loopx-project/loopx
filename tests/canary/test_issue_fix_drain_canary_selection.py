from __future__ import annotations

from loopx.canary.planner import build_catalog_canary_plan
from loopx.canary.premerge import build_premerge_validation_gate
from loopx.canary.runner import build_canary_smoke_suite_run


def test_reviewer_notification_drain_change_selects_postwrite_smoke_under_premerge_and_smoke_suite() -> None:
    changed = ["loopx/capabilities/issue_fix/reviewer_notification_drain.py"]

    quick_gate = build_premerge_validation_gate(
        changed_files=changed,
        tier="quick",
        execute=False,
    )
    quick_commands = [
        check["command"]
        for check in quick_gate["catalog_run"]["selected_checks"]
    ]
    assert (
        "python3 examples/issue-fix-reviewer-notification-drain-postwrite-smoke.py"
        in quick_commands
    ), quick_commands

    standard_gate = build_premerge_validation_gate(
        changed_files=changed,
        tier="standard",
        execute=False,
    )
    standard_commands = [
        check["command"]
        for check in standard_gate["catalog_run"]["selected_checks"]
    ]
    assert (
        "python3 examples/issue-fix-reviewer-notification-drain-postwrite-smoke.py"
        in standard_commands
    ), standard_commands
    assert (
        "python3 examples/issue-fix-reviewer-notification-sink-smoke.py"
        in standard_commands
    ), standard_commands

    limited_suite = build_canary_smoke_suite_run(
        suite="catalog-plan",
        changed_files=changed,
        limit=3,
        execute=False,
    )
    limited_commands = [
        check["command"] for check in limited_suite["selected_checks"]
    ]
    assert (
        "python3 examples/issue-fix-reviewer-notification-drain-postwrite-smoke.py"
        in limited_commands
    ), limited_commands

    profile_suite = build_canary_smoke_suite_run(
        suite="catalog-plan",
        profiles=["issue-fix-reviewer-notification-drain"],
        execute=False,
    )
    profile_commands = [
        check["command"] for check in profile_suite["selected_checks"]
    ]
    assert profile_commands == [
        "python3 examples/issue-fix-reviewer-notification-drain-postwrite-smoke.py",
        "python3 examples/issue-fix-reviewer-notification-sink-smoke.py",
    ]


def test_reviewer_routing_profile_stays_within_three_checks_and_avoids_drain_drift() -> None:
    routing_suite = build_canary_smoke_suite_run(
        suite="catalog-plan",
        profiles=["issue-fix-reviewer-routing"],
        execute=False,
    )
    routing_commands = [
        check["command"] for check in routing_suite["selected_checks"]
    ]
    assert routing_commands == [
        "python3 examples/issue-fix-capability-guide-smoke.py",
        "python3 examples/issue-fix-reviewer-recommendation-smoke.py",
        "python3 examples/issue-fix-reviewer-request-smoke.py",
    ]

    unrelated_plan = build_catalog_canary_plan(
        changed_files=["loopx/capabilities/issue_fix/workflow_plan.py"]
    )
    unrelated_ids = {
        profile["id"] for profile in unrelated_plan["domain_profiles"]
    }
    assert "issue-fix-reviewer-notification-drain" not in unrelated_ids
    assert "issue-fix-reviewer-routing" not in unrelated_ids
    assert "issue-fix-json-input-boundary" not in unrelated_ids
