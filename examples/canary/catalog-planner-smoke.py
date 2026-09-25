#!/usr/bin/env python3
"""Smoke-test catalog-informed canary profile planning."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
CATALOG = REPO_ROOT / "docs" / "concepts" / "interaction-pattern-catalog.md"

from loopx.canary.planner import (  # noqa: E402
    build_catalog_canary_coverage_audit,
    build_catalog_canary_plan,
    build_catalog_canary_profiles,
)
from loopx.cli_commands.canary import collect_git_diff_changed_files  # noqa: E402


def assert_profiles_come_from_catalog_matrix() -> None:
    payload = build_catalog_canary_profiles()
    assert payload["ok"] is True, payload
    assert payload["dry_run"] is True, payload
    assert payload["executes_checks"] is False, payload
    families = {profile["family"] for profile in payload["profiles"]}
    assert {
        "Work Routing",
        "Human Decision",
        "State And Boundary",
        "Evidence Lifecycle",
        "Planning Governance",
    } <= families, payload
    work_routing = next(profile for profile in payload["profiles"] if profile["family"] == "Work Routing")
    assert "IP-001" in work_routing["pattern_ids"], work_routing
    assert work_routing["candidate_checks"], work_routing
    assert all("command" in check and "reason" in check for check in work_routing["candidate_checks"])
    domain_profile_ids = {profile["id"] for profile in payload["domain_profiles"]}
    assert {
        "pr-review-and-merge",
        "release-promotion",
        "install-update",
        "control-plane-refactor",
        "repo-architecture-budget",
        "control-plane-state-machine",
        "scheduler-ack-route",
        "status-read-path",
        "status-projection-cache",
        "review-packet-read-path",
        "cli-command-contract",
        "todo-lifecycle",
        "monitor-scheduler",
        "state-write-correctness",
        "product-entry-workflows",
        "cross-runtime-impl-review-demo",
        "host-command-entry",
        "first-connect-contract",
        "runtime-connector-catalog",
        "frontstage-rollout",
        "auto-research-demo",
        "catalog-canary-contract",
        "benchmark-toolkit-boundary",
    } <= domain_profile_ids, payload
    domain_profiles = {profile["id"]: profile for profile in payload["domain_profiles"]}
    state_write_commands = [
        check["command"] for check in domain_profiles["state-write-correctness"]["checks"]
    ]
    assert "python3 examples/control_plane/task-lease-runtime-smoke.py" in state_write_commands
    assert "python3 examples/control_plane/todo-write-correctness-smoke.py" in state_write_commands


def assert_plan_selects_minimal_profiles_from_changed_surfaces() -> None:
    payload = build_catalog_canary_plan(
        changed_files=["loopx/quota.py", "loopx/status.py"],
        surfaces=["scheduler hint", "quota should-run"],
        max_checks_per_family=2,
    )
    families = [profile["family"] for profile in payload["profiles"]]
    assert "Work Routing" in families, payload
    assert "State And Boundary" in families, payload
    assert "Evidence Lifecycle" not in families, payload
    for profile in payload["profiles"]:
        assert len(profile["candidate_checks"]) <= 2, profile
        assert profile["selection_reasons"], profile
    domain_profiles = {profile["id"]: profile for profile in payload["domain_profiles"]}
    assert "control-plane-refactor" in domain_profiles, payload
    assert "monitor-scheduler" in domain_profiles, payload
    for profile in domain_profiles.values():
        assert all(check["tier"] == "default" for check in profile["checks"]), profile
        if profile["id"] == "repo-architecture-budget":
            assert profile["deep_checks_available"] is False, profile
        else:
            assert profile["deep_checks_available"] is True, profile
        assert profile["deep_checks_included"] is False, profile
    assert payload["suggested_check_count"] == len(payload["suggested_checks"]), payload
    assert payload["commands"] == [
        check["command"] for check in payload["suggested_checks"]
    ], payload
    assert payload["suggested_checks"][0]["source"] == "domain_profile", payload
    assert payload["executes_checks"] is False, payload


def assert_scheduler_ack_route_profile_keeps_independent_checks() -> None:
    payload = build_catalog_canary_plan(
        changed_files=["loopx/control_plane/scheduler/ack.py"],
        surfaces=["scheduler ACK route binding"],
    )
    domain_profiles = {profile["id"]: profile for profile in payload["domain_profiles"]}
    profile = domain_profiles["scheduler-ack-route"]
    assert [check["command"] for check in profile["checks"]] == [
        "python3 examples/control_plane/quota-scheduler-state-ack-smoke.py",
        "python3 examples/control_plane/quota-scheduler-registry-route-smoke.py",
        "python3 examples/control_plane/monitor-scheduler-contract-smoke.py",
    ], profile


def assert_catalog_documents_selection_rules() -> None:
    catalog = CATALOG.read_text(encoding="utf-8")
    for snippet in [
        "Use this selection order for ordinary PR, release, and refactor review:",
        "Start from changed files and touched surfaces, not from the PR title.",
        "PR review or self-merge workflow",
        "Release or install promotion",
        "Control-plane refactor",
        "Keep default profiles on fixture-level or dry-run checks.",
        "When hot-path and cold-path surfaces both changed",
        "Existing-contract-first rule: canary planning should consume current public\nruntime/status surfaces",
        "`quota should-run`, `status`, `review-packet`, `loopx check`, current smoke\nfixtures, `loopx canary plan` output, and fixture-level `loopx canary run`\nchecks as the first evidence layer",
        "`loopx canary run` must stay no-write by\ndefault",
        "not write promotion evidence, create runtime contracts, poll external targets,\nor run deep/browser checks",
        "stop at\na review packet first",
        "owner review before implementation",
    ]:
        assert snippet in catalog, snippet


def assert_pr_release_and_refactor_profiles_select() -> None:
    pr_payload = build_catalog_canary_plan(
        changed_files=["loopx/pr_review.py", "skills/loopx-pr-review/SKILL.md"],
        surfaces=["pr-review public PR metadata"],
    )
    pr_profile_ids = {profile["id"] for profile in pr_payload["domain_profiles"]}
    assert "pr-review-and-merge" in pr_profile_ids, pr_payload

    release_payload = build_catalog_canary_plan(
        changed_files=["docs/product/release-readiness.md"],
        surfaces=["release promotion install update"],
    )
    release_profiles = {profile["id"]: profile for profile in release_payload["domain_profiles"]}
    release_profile_ids = set(release_profiles)
    assert "release-promotion" in release_profile_ids, release_payload
    assert "install-update" in release_profile_ids, release_payload
    release_commands = [
        check["command"] for check in release_profiles["release-promotion"]["checks"]
    ]
    assert "python3 examples/control_plane/promotion-readiness-readmodel-smoke.py" in release_commands
    assert "python3 examples/canary/canary-promotion-readiness-boundary-smoke.py" in release_commands
    assert all("canary-promotion-readiness-smoke.py" not in command for command in release_commands)
    assert release_profiles["release-promotion"]["deep_checks_available"] is True

    deep_release_payload = build_catalog_canary_plan(
        profiles=["release-promotion"],
        include_deep_checks=True,
        max_checks_per_profile=6,
    )
    assert "python3 examples/canary/canary-promotion-readiness-smoke.py" in deep_release_payload["commands"]

    install_payload = build_catalog_canary_plan(
        changed_files=["scripts/install-local.sh", "loopx/self_update.py"],
        surfaces=["install update rollback"],
    )
    install_profiles = {profile["id"]: profile for profile in install_payload["domain_profiles"]}
    assert "install-update" in install_profiles, install_payload
    install_profile = install_profiles["install-update"]
    install_commands = [check["command"] for check in install_profile["checks"]]
    assert "python3 examples/install-local-smoke.py" in install_commands, install_profile
    assert "python3 examples/loopx-update-smoke.py" in install_commands, install_profile
    assert all(check["tier"] == "default" for check in install_profile["checks"]), install_profile
    assert install_profile["deep_checks_available"] is True, install_profile

    deep_install_payload = build_catalog_canary_plan(
        changed_files=["scripts/install-local.sh"],
        surfaces=["install promotion boundary"],
        include_deep_checks=True,
    )
    deep_install_profile = next(
        profile
        for profile in deep_install_payload["domain_profiles"]
        if profile["id"] == "install-update"
    )
    deep_install_commands = [check["command"] for check in deep_install_profile["checks"]]
    assert "python3 examples/release/local-install-promotion-boundary-smoke.py" in deep_install_commands, (
        deep_install_profile
    )

    refactor_payload = build_catalog_canary_plan(
        changed_files=["loopx/quota.py", "loopx/status.py"],
        surfaces=["control-plane refactor scheduler hint"],
    )
    refactor_profile_ids = {profile["id"] for profile in refactor_payload["domain_profiles"]}
    assert "control-plane-refactor" in refactor_profile_ids, refactor_payload
    refactor_profile = next(
        profile
        for profile in refactor_payload["domain_profiles"]
        if profile["id"] == "control-plane-refactor"
    )
    refactor_commands = [check["command"] for check in refactor_profile["checks"]]
    assert "python3 examples/control_plane/bounded-context-namespace-smoke.py" in refactor_commands, (
        refactor_profile
    )
    assert "repo-architecture-budget" in refactor_profile_ids, refactor_payload
    architecture_profile = next(
        profile
        for profile in refactor_payload["domain_profiles"]
        if profile["id"] == "repo-architecture-budget"
    )
    architecture_commands = [check["command"] for check in architecture_profile["checks"]]
    assert "python3 examples/control_plane/control-plane-maintainability-ratchet-smoke.py" in architecture_commands, (
        architecture_profile
    )

    state_machine_payload = build_catalog_canary_plan(
        changed_files=["examples/control_plane/control-plane-integrated-canary-smoke.py"],
        surfaces=[
            "complex control-plane state-machine interaction_contract "
            "scheduler_hint work_lane_contract goal_frontier"
        ],
        max_checks_per_profile=5,
    )
    state_machine_profiles = {
        profile["id"]: profile for profile in state_machine_payload["domain_profiles"]
    }
    assert "control-plane-state-machine" in state_machine_profiles, state_machine_payload
    state_machine_profile = state_machine_profiles["control-plane-state-machine"]
    state_machine_commands = [check["command"] for check in state_machine_profile["checks"]]
    assert "python3 examples/control_plane/control-plane-integrated-canary-smoke.py" not in state_machine_commands, (
        state_machine_profile
    )
    assert (
        "python3 examples/control_plane/peer-agent-continuation-state-machine-smoke.py"
        in state_machine_commands
    ), state_machine_profile
    assert "python3 examples/control_plane/interaction-contract-state-machine-smoke.py" in state_machine_commands, (
        state_machine_profile
    )
    assert "python3 examples/control_plane/heartbeat-quota-flow-smoke.py" in state_machine_commands, (
        state_machine_profile
    )
    assert "python3 examples/control_plane/quota-scheduler-state-ack-smoke.py" in state_machine_commands, (
        state_machine_profile
    )
    assert all(check["tier"] == "default" for check in state_machine_profile["checks"]), (
        state_machine_profile
    )
    assert state_machine_profile["deep_checks_available"] is True, state_machine_profile
    assert state_machine_profile["deep_checks_included"] is False, state_machine_profile

    frontier_rule_payload = build_catalog_canary_plan(
        changed_files=[
            "loopx/control_plane/goals/goal_frontier/replan_rules.py",
        ],
        surfaces=["ordered goal frontier replan policy"],
        max_checks_per_profile=5,
    )
    frontier_rule_profiles = {
        profile["id"]: profile for profile in frontier_rule_payload["domain_profiles"]
    }
    assert "goal-frontier-replan-rules" in frontier_rule_profiles, frontier_rule_payload
    frontier_rule_commands = [
        check["command"]
        for check in frontier_rule_profiles["goal-frontier-replan-rules"]["checks"]
    ]
    assert (
        "python3 examples/control_plane/goal-frontier-replan-rules-smoke.py"
        in frontier_rule_commands
    ), frontier_rule_profiles["goal-frontier-replan-rules"]

    state_machine_deep_payload = build_catalog_canary_plan(
        changed_files=["examples/control_plane/control-plane-integrated-canary-smoke.py"],
        surfaces=[
            "complex control-plane state-machine interaction_contract "
            "scheduler_hint work_lane_contract goal_frontier"
        ],
        include_deep_checks=True,
        max_checks_per_profile=5,
    )
    state_machine_deep_profiles = {
        profile["id"]: profile for profile in state_machine_deep_payload["domain_profiles"]
    }
    state_machine_deep_commands = [
        check["command"]
        for check in state_machine_deep_profiles["control-plane-state-machine"]["checks"]
    ]
    assert "python3 examples/control_plane/control-plane-integrated-canary-smoke.py" in (
        state_machine_deep_commands
    ), state_machine_deep_profiles["control-plane-state-machine"]

    interaction_contract_payload = build_catalog_canary_plan(
        changed_files=["loopx/control_plane/work_items/interaction_contract.py"],
        surfaces=["interaction_contract protocol_action_packet state-machine"],
        max_checks_per_profile=4,
    )
    interaction_contract_profiles = {
        profile["id"]: profile for profile in interaction_contract_payload["domain_profiles"]
    }
    assert "control-plane-refactor" in interaction_contract_profiles, interaction_contract_payload
    assert "control-plane-state-machine" in interaction_contract_profiles, interaction_contract_payload
    interaction_contract_commands = [
        check["command"]
        for check in interaction_contract_profiles["control-plane-state-machine"]["checks"]
    ]
    assert "python3 examples/control_plane/interaction-contract-state-machine-smoke.py" in (
        interaction_contract_commands
    ), interaction_contract_profiles["control-plane-state-machine"]

    interaction_smoke_payload = build_catalog_canary_plan(
        changed_files=["examples/control_plane/interaction-contract-state-machine-smoke.py"],
        surfaces=[],
        max_checks_per_profile=4,
    )
    interaction_smoke_profiles = {
        profile["id"]: profile for profile in interaction_smoke_payload["domain_profiles"]
    }
    assert "control-plane-state-machine" in interaction_smoke_profiles, (
        interaction_smoke_payload
    )
    interaction_smoke_commands = [
        check["command"]
        for check in interaction_smoke_profiles["control-plane-state-machine"]["checks"]
    ]
    assert "python3 examples/control_plane/interaction-contract-state-machine-smoke.py" in (
        interaction_smoke_commands
    ), interaction_smoke_profiles["control-plane-state-machine"]

    bounded_context_payload = build_catalog_canary_plan(
        changed_files=["loopx/control_plane/work_items/work_lane.py"],
        surfaces=[
            "bounded-context work_lane_contract state-machine interaction_contract"
        ],
        max_checks_per_profile=3,
    )
    bounded_context_profiles = {
        profile["id"]: profile for profile in bounded_context_payload["domain_profiles"]
    }
    assert "control-plane-refactor" in bounded_context_profiles, bounded_context_payload
    assert "control-plane-state-machine" in bounded_context_profiles, bounded_context_payload
    bounded_context_state_machine_commands = [
        check["command"]
        for check in bounded_context_profiles["control-plane-state-machine"]["checks"]
    ]
    assert "python3 examples/control_plane/control-plane-integrated-canary-smoke.py" not in (
        bounded_context_state_machine_commands
    ), bounded_context_profiles["control-plane-state-machine"]
    assert "python3 examples/control_plane/interaction-contract-state-machine-smoke.py" in (
        bounded_context_state_machine_commands
    ), bounded_context_profiles["control-plane-state-machine"]

    work_lane_policy_payload = build_catalog_canary_plan(
        changed_files=["loopx/control_plane/scheduler/monitor_todo.py"],
        surfaces=["resume_when resume_ready work-lane policy seam"],
        max_checks_per_profile=5,
    )
    work_lane_profiles = {
        profile["id"]: profile for profile in work_lane_policy_payload["domain_profiles"]
    }
    assert "control-plane-refactor" in work_lane_profiles, work_lane_policy_payload
    work_lane_commands = [
        check["command"] for check in work_lane_profiles["control-plane-refactor"]["checks"]
    ]
    assert "python3 examples/control_plane/quota-resume-gated-open-todo-smoke.py" in work_lane_commands, (
        work_lane_profiles["control-plane-refactor"]
    )
    assert "python3 examples/control_plane/quota-cleared-blocker-successor-gate-smoke.py" in work_lane_commands, (
        work_lane_profiles["control-plane-refactor"]
    )
    assert all(
        check["tier"] == "default"
        for check in work_lane_profiles["control-plane-refactor"]["checks"]
    ), work_lane_profiles["control-plane-refactor"]

    monitor_target_payload = build_catalog_canary_plan(
        changed_files=["loopx/control_plane/quota/monitor_poll_commit.ts"],
        surfaces=["monitor_target monitor-poll scheduler_hint state-machine"],
        max_checks_per_profile=5,
    )
    monitor_target_profiles = {
        profile["id"]: profile for profile in monitor_target_payload["domain_profiles"]
    }
    assert "control-plane-refactor" in monitor_target_profiles, monitor_target_payload
    assert "control-plane-state-machine" in monitor_target_profiles, monitor_target_payload
    monitor_target_state_machine_commands = [
        check["command"]
        for check in monitor_target_profiles["control-plane-state-machine"]["checks"]
    ]
    assert "python3 examples/control_plane/control-plane-integrated-canary-smoke.py" not in (
        monitor_target_state_machine_commands
    ), monitor_target_profiles["control-plane-state-machine"]
    assert "python3 examples/control_plane/interaction-contract-state-machine-smoke.py" in (
        monitor_target_state_machine_commands
    ), monitor_target_profiles["control-plane-state-machine"]

    monitor_writeback_payload = build_catalog_canary_plan(
        changed_files=["loopx/control_plane/scheduler/monitor_poll_writeback.py"],
        surfaces=["monitor_poll_writeback scheduler_hint state-machine"],
        max_checks_per_profile=5,
    )
    monitor_writeback_profiles = {
        profile["id"]: profile for profile in monitor_writeback_payload["domain_profiles"]
    }
    assert "control-plane-refactor" in monitor_writeback_profiles, monitor_writeback_payload
    assert "control-plane-state-machine" in monitor_writeback_profiles, monitor_writeback_payload
    monitor_writeback_state_machine_commands = [
        check["command"]
        for check in monitor_writeback_profiles["control-plane-state-machine"]["checks"]
    ]
    assert "python3 examples/control_plane/control-plane-integrated-canary-smoke.py" not in (
        monitor_writeback_state_machine_commands
    ), monitor_writeback_profiles["control-plane-state-machine"]
    assert "python3 examples/control_plane/interaction-contract-state-machine-smoke.py" in (
        monitor_writeback_state_machine_commands
    ), monitor_writeback_profiles["control-plane-state-machine"]

    status_payload = build_catalog_canary_plan(
        changed_files=["loopx/status.py"],
        surfaces=["status --goal-id read-path"],
    )
    status_profiles = {profile["id"]: profile for profile in status_payload["domain_profiles"]}
    assert "status-read-path" in status_profiles, status_payload
    status_commands = [check["command"] for check in status_profiles["status-read-path"]["checks"]]
    assert "python3 examples/control_plane/status-goal-filter-smoke.py" in status_commands, status_payload
    assert "python3 examples/control_plane/status-quota-review-packet-parity-smoke.py" in status_commands, status_payload
    assert "python3 examples/control_plane/runtime-handoff-status-read-path-smoke.py" in status_commands, (
        status_payload
    )
    assert all(check["tier"] == "default" for check in status_profiles["status-read-path"]["checks"]), status_payload
    assert status_profiles["status-read-path"]["deep_checks_available"] is True, status_payload
    assert status_profiles["status-read-path"]["deep_checks_included"] is False, status_payload

    status_wide_payload = build_catalog_canary_plan(
        changed_files=["loopx/status.py"],
        surfaces=["status --goal-id read-path"],
        max_checks_per_profile=5,
    )
    status_wide_profiles = {
        profile["id"]: profile for profile in status_wide_payload["domain_profiles"]
    }
    status_wide_commands = [
        check["command"] for check in status_wide_profiles["status-read-path"]["checks"]
    ]
    assert "python3 examples/control_plane/run-compaction-readmodel-smoke.py" in status_wide_commands, (
        status_wide_payload
    )
    assert "python3 examples/control_plane/goal-channel-readmodel-smoke.py" in status_wide_commands, (
        status_wide_payload
    )

    status_cache_payload = build_catalog_canary_plan(
        changed_files=["loopx/control_plane/runtime/status_projection_cache.py"],
        surfaces=["status_projection_cache projection-cache read-path"],
        max_checks_per_profile=4,
    )
    status_cache_profiles = {
        profile["id"]: profile for profile in status_cache_payload["domain_profiles"]
    }
    assert "status-projection-cache" in status_cache_profiles, status_cache_payload
    status_cache_commands = [
        check["command"] for check in status_cache_profiles["status-projection-cache"]["checks"]
    ]
    assert "python3 examples/control_plane/status-projection-cache-smoke.py" in status_cache_commands, (
        status_cache_payload
    )

    runtime_handoff_payload = build_catalog_canary_plan(
        changed_files=["loopx/control_plane/handoff/project_handoff.py"],
        surfaces=["runtime handoff post_handoff_run status read-path"],
    )
    runtime_handoff_profiles = {
        profile["id"]: profile for profile in runtime_handoff_payload["domain_profiles"]
    }
    assert "status-read-path" in runtime_handoff_profiles, runtime_handoff_payload
    runtime_handoff_commands = [
        check["command"] for check in runtime_handoff_profiles["status-read-path"]["checks"]
    ]
    assert "python3 examples/control_plane/runtime-handoff-status-read-path-smoke.py" in (
        runtime_handoff_commands
    ), runtime_handoff_payload

    review_packet_payload = build_catalog_canary_plan(
        changed_files=["loopx/review_packet.py", "loopx/cli_commands/status.py"],
        surfaces=["review-packet handoff-only operator packet read-path"],
    )
    review_packet_profiles = {
        profile["id"]: profile for profile in review_packet_payload["domain_profiles"]
    }
    assert "review-packet-read-path" in review_packet_profiles, review_packet_payload
    review_packet_profile = review_packet_profiles["review-packet-read-path"]
    review_packet_commands = [check["command"] for check in review_packet_profile["checks"]]
    assert "python3 examples/control_plane/review-packet-cli-smoke.py" in review_packet_commands, review_packet_profile
    assert "python3 examples/control_plane/review-packet-smoke.py" in review_packet_commands, review_packet_profile
    assert all(check["tier"] == "default" for check in review_packet_profile["checks"]), review_packet_profile
    assert review_packet_profile["deep_checks_available"] is True, review_packet_profile
    assert review_packet_profile["deep_checks_included"] is False, review_packet_profile

    cli_payload = build_catalog_canary_plan(
        changed_files=["loopx/cli.py", "loopx/cli_commands/version.py"],
        surfaces=["cli command modularization"],
    )
    cli_profile_ids = {profile["id"] for profile in cli_payload["domain_profiles"]}
    assert "cli-command-contract" in cli_profile_ids, cli_payload
    cli_profile = next(profile for profile in cli_payload["domain_profiles"] if profile["id"] == "cli-command-contract")
    commands = [check["command"] for check in cli_profile["checks"]]
    assert "python3 examples/cli-version-command-modularization-smoke.py" in commands, cli_profile

    output_budget_payload = build_catalog_canary_plan(
        changed_files=["loopx/cli_commands/status.py", "loopx/help_surface.py"],
        surfaces=["agent-facing CLI output qualification"],
    )
    output_budget_profiles = {
        profile["id"]: profile
        for profile in output_budget_payload["domain_profiles"]
    }
    assert "agent-facing-cli-output-budget" in output_budget_profiles, output_budget_payload
    output_budget_checks = [
        check["command"]
        for check in output_budget_profiles["agent-facing-cli-output-budget"]["checks"]
    ]
    assert output_budget_checks == [
        "python3 examples/control_plane/cli-output-budget-regression-smoke.py"
    ], output_budget_profiles["agent-facing-cli-output-budget"]

    todo_payload = build_catalog_canary_plan(
        changed_files=["loopx/todos.py", "loopx/control_plane/todos/contract.py"],
        surfaces=["todo lifecycle todo claim todo list"],
    )
    todo_profiles = {profile["id"]: profile for profile in todo_payload["domain_profiles"]}
    assert "todo-lifecycle" in todo_profiles, todo_payload
    todo_profile = todo_profiles["todo-lifecycle"]
    todo_commands = [check["command"] for check in todo_profile["checks"]]
    assert "python3 examples/control_plane/todo-lifecycle-cli-smoke.py" in todo_commands, todo_profile
    assert all(check["tier"] == "default" for check in todo_profile["checks"]), todo_profile
    assert todo_profile["deep_checks_available"] is True, todo_profile

    product_entry_payload = build_catalog_canary_plan(
        changed_files=[
            "README.md",
            "loopx/capabilities/issue_fix/README.md",
            "docs/update-notes/README.md",
            "loopx/capabilities/content_ops/surface.py",
            "scripts/update_notes_release_job.py",
        ],
        surfaces=["product-entry issue-fix content-ops update-note cross-runtime demo"],
        max_checks_per_profile=7,
    )
    product_entry_profiles = {
        profile["id"]: profile for profile in product_entry_payload["domain_profiles"]
    }
    assert "product-entry-workflows" in product_entry_profiles, product_entry_payload
    assert "issue-fix-reviewer-routing" in product_entry_profiles, product_entry_payload
    assert "install-update" not in product_entry_profiles, product_entry_payload
    product_entry_profile = product_entry_profiles["product-entry-workflows"]
    product_entry_commands = [check["command"] for check in product_entry_profile["checks"]]
    assert "python3 examples/issue-fix-workflow-contract-smoke.py" in product_entry_commands, product_entry_profile
    assert "python3 examples/issue-fix-repository-context-smoke.py" in product_entry_commands, product_entry_profile
    assert "python3 examples/issue-fix-feasibility-smoke.py" in product_entry_commands, product_entry_profile
    assert "python3 examples/issue-fix-pr-lifecycle-smoke.py" in product_entry_commands, product_entry_profile
    assert "python3 examples/content-ops-issue-fix-intake-smoke.py" in product_entry_commands, product_entry_profile
    assert "python3 examples/public_entry/readme-demo-surface-smoke.py" in product_entry_commands, product_entry_profile
    assert "python3 examples/update-notes-archive-smoke.py" in product_entry_commands, product_entry_profile
    assert all(check["tier"] == "default" for check in product_entry_profile["checks"]), product_entry_profile
    assert product_entry_profile["deep_checks_available"] is True, product_entry_profile
    assert product_entry_profile["deep_checks_included"] is False, product_entry_profile
    reviewer_profile = product_entry_profiles["issue-fix-reviewer-routing"]
    reviewer_commands = [check["command"] for check in reviewer_profile["checks"]]
    assert reviewer_commands == [
        "python3 examples/issue-fix-json-input-boundary-smoke.py",
        "python3 examples/issue-fix-capability-guide-smoke.py",
        "python3 examples/issue-fix-reviewer-recommendation-smoke.py",
        "python3 examples/issue-fix-reviewer-request-smoke.py",
        "python3 examples/issue-fix-reviewer-notification-sink-smoke.py",
    ], reviewer_profile
    assert all(check["tier"] == "default" for check in reviewer_profile["checks"]), reviewer_profile
    assert reviewer_profile["deep_checks_available"] is False, reviewer_profile

    outcome_payload = build_catalog_canary_plan(
        changed_files=[
            "loopx/capabilities/issue_fix/repository_memory_provider.py",
            "examples/issue-fix-validated-memory-writeback-smoke.py",
        ],
        surfaces=["issue-fix outcome validated memory writeback"],
    )
    outcome_profiles = {
        profile["id"]: profile for profile in outcome_payload["domain_profiles"]
    }
    outcome_profile = outcome_profiles["issue-fix-outcome-visibility"]
    outcome_commands = [check["command"] for check in outcome_profile["checks"]]
    assert "python3 examples/issue-fix-outcome-projection-smoke.py" in outcome_commands
    assert (
        "python3 examples/issue-fix-validated-memory-writeback-smoke.py"
        in outcome_commands
    )

    cross_runtime_payload = build_catalog_canary_plan(
        changed_files=[
            "loopx/control_plane/handoff/cross_runtime_impl_review.py",
            "loopx/cli_commands/starter.py",
            "docs/product/use-cases/cross-runtime/cross-runtime-impl-review-demo.md",
        ],
        surfaces=[
            "loopx demo impl-review claude implements codex reviews "
            "cross_runtime_impl_review_demo_packet_v0"
        ],
        max_checks_per_profile=3,
    )
    cross_runtime_profiles = {
        profile["id"]: profile for profile in cross_runtime_payload["domain_profiles"]
    }
    assert "cross-runtime-impl-review-demo" in cross_runtime_profiles, cross_runtime_payload
    cross_runtime_profile = cross_runtime_profiles["cross-runtime-impl-review-demo"]
    cross_runtime_commands = [check["command"] for check in cross_runtime_profile["checks"]]
    assert (
        "python3 examples/cross-runtime-impl-review-demo-smoke.py" in cross_runtime_commands
    ), cross_runtime_profile
    assert "python3 examples/public_entry/readme-demo-surface-smoke.py" in cross_runtime_commands
    assert all(check["tier"] == "default" for check in cross_runtime_profile["checks"])
    assert cross_runtime_profile["deep_checks_available"] is False, cross_runtime_profile
    assert cross_runtime_profile["deep_checks_included"] is False, cross_runtime_profile

    host_command_payload = build_catalog_canary_plan(
        changed_files=[
            "loopx/cli_commands/slash_commands.py",
            "docs/reference/protocols/codex-app-host-command-registry-v0.md",
            "docs/reference/protocols/global-manager-command-v0.md",
        ],
        surfaces=["slash-commands /loopx-global-summary host command registry"],
        max_checks_per_profile=3,
    )
    host_command_profiles = {
        profile["id"]: profile for profile in host_command_payload["domain_profiles"]
    }
    assert "host-command-entry" in host_command_profiles, host_command_payload
    host_command_profile = host_command_profiles["host-command-entry"]
    host_command_commands = [check["command"] for check in host_command_profile["checks"]]
    assert "python3 examples/slash-command-catalog-smoke.py" in host_command_commands
    assert "python3 examples/codex-app-host-command-registry-smoke.py" in host_command_commands
    assert "python3 examples/project/global-manager-command-protocol-smoke.py" in host_command_commands
    assert all(check["tier"] == "default" for check in host_command_profile["checks"])
    assert host_command_profile["deep_checks_available"] is False, host_command_profile
    assert host_command_profile["deep_checks_included"] is False, host_command_profile

    onboarding_payload = build_catalog_canary_plan(
        changed_files=[
            "loopx/bootstrap.py",
            "loopx/bootstrap_command_pack.py",
            "loopx/contract.py",
        ],
        surfaces=[
            "new user onboarding first connect contract state projection gap start-goal"
        ],
    )
    onboarding_profiles = {
        profile["id"]: profile for profile in onboarding_payload["domain_profiles"]
    }
    assert "first-connect-contract" in onboarding_profiles, onboarding_payload
    onboarding_profile = onboarding_profiles["first-connect-contract"]
    assert [check["command"] for check in onboarding_profile["checks"]] == [
        "python3 examples/project/first-connect-contract-smoke.py"
    ], onboarding_profile
    assert all(check["tier"] == "default" for check in onboarding_profile["checks"])
    assert onboarding_profile["deep_checks_available"] is False, onboarding_profile
    assert onboarding_profile["deep_checks_included"] is False, onboarding_profile

    runtime_connector_payload = build_catalog_canary_plan(
        changed_files=["docs/integrations/runtime-connector-catalog.md"],
        surfaces=[
            "runtime connector catalog codex app heartbeat codex cli tui "
            "claude code loop worker bridge scheduler_hint scoped identity"
        ],
        max_checks_per_profile=4,
    )
    runtime_connector_profiles = {
        profile["id"]: profile for profile in runtime_connector_payload["domain_profiles"]
    }
    assert "runtime-connector-catalog" in runtime_connector_profiles, runtime_connector_payload
    runtime_connector_profile = runtime_connector_profiles["runtime-connector-catalog"]
    runtime_connector_commands = [
        check["command"] for check in runtime_connector_profile["checks"]
    ]
    assert "python3 examples/control_plane/heartbeat-prompt-smoke.py" in runtime_connector_commands
    assert (
        "python3 examples/codex-cli-tui-bootstrap-smoke-bundle-smoke.py"
        in runtime_connector_commands
    )
    assert "python3 examples/claude-goalmode-lifecycle-smoke.py" in runtime_connector_commands
    assert "python3 examples/worker-bridge-install-contract-smoke.py" in runtime_connector_commands
    assert all(check["tier"] == "default" for check in runtime_connector_profile["checks"])
    assert runtime_connector_profile["deep_checks_available"] is True, runtime_connector_profile
    assert runtime_connector_profile["deep_checks_included"] is False, runtime_connector_profile

    auto_research_payload = build_catalog_canary_plan(
        changed_files=["demo/auto_research/core.py"],
        surfaces=["auto-research demo frontier visible launcher"],
    )
    auto_research_profiles = {
        profile["id"]: profile for profile in auto_research_payload["domain_profiles"]
    }
    assert "auto-research-demo" in auto_research_profiles, auto_research_payload
    auto_research_profile = auto_research_profiles["auto-research-demo"]
    auto_research_commands = [check["command"] for check in auto_research_profile["checks"]]
    assert (
        "python3 examples/auto-research-minimal-kernel-smoke.py"
        in auto_research_commands
    ), auto_research_profile
    assert (
        "python3 examples/decentralized-auto-research-frontier-smoke.py" in auto_research_commands
    ), auto_research_profile
    assert all(check["tier"] == "default" for check in auto_research_profile["checks"]), auto_research_profile
    assert auto_research_profile["deep_checks_available"] is True, auto_research_profile

    explore_payload = build_catalog_canary_plan(
        changed_files=[
            "loopx/capabilities/explore/harness_runtime.py",
            "loopx/configure_goal.py",
        ],
        surfaces=["explore harness resume configure-goal"],
    )
    explore_profiles = {
        profile["id"]: profile for profile in explore_payload["domain_profiles"]
    }
    assert "explore-harness" in explore_profiles, explore_payload
    explore_profile = explore_profiles["explore-harness"]
    explore_commands = [check["command"] for check in explore_profile["checks"]]
    assert "python3 examples/explore-configure-goal-smoke.py" in explore_commands
    assert "python3 examples/explore-harness-runtime-resume-smoke.py" in explore_commands
    assert "python3 examples/explore-worker-plan-gate-smoke.py" in explore_commands
    assert explore_profile["deep_checks_available"] is True, explore_profile

    configure_sync_payload = build_catalog_canary_plan(
        changed_files=["loopx/control_plane/goals/configure_goal_service.py"],
        surfaces=["configure-goal authoritative shared runtime sync readback"],
    )
    configure_sync_profiles = {
        profile["id"]: profile
        for profile in configure_sync_payload["domain_profiles"]
    }
    configure_sync_profile = configure_sync_profiles["peer-agent-runtime"]
    configure_sync_commands = [
        check["command"] for check in configure_sync_profile["checks"]
    ]
    assert (
        "python3 examples/project/configure-goal-global-sync-smoke.py"
        in configure_sync_commands
    ), configure_sync_profile


def assert_explicit_profile_can_include_deep_checks() -> None:
    payload = build_catalog_canary_plan(
        profiles=["benchmark-toolkit-boundary"],
        include_deep_checks=True,
        max_checks_per_profile=4,
    )
    assert payload["profile_count"] == 0, payload
    assert payload["domain_profile_count"] == 1, payload
    profile = payload["domain_profiles"][0]
    assert profile["id"] == "benchmark-toolkit-boundary", profile
    assert profile["deep_checks_included"] is True, profile
    commands = payload["commands"]
    assert (
        "python3 examples/benchmark-run-permission-policy-smoke.py"
        in commands
    ), payload
    assert any(check["tier"] == "deep" for check in profile["checks"]), profile
    assert "existing public runtime/status contracts first" in payload["note"], payload
    assert "owner-review necessity/risk packet" in payload["note"], payload


def assert_benchmark_toolkit_changes_select_boundary_smoke() -> None:
    payload = build_catalog_canary_plan(
        changed_files=["loopx/capabilities/benchmark_toolkit/integrity.py"],
        surfaces=["benchmark toolkit integrity no-submit boundary"],
        max_checks_per_profile=3,
    )
    profiles = {profile["id"]: profile for profile in payload["domain_profiles"]}
    assert "benchmark-toolkit-boundary" in profiles, payload
    profile = profiles["benchmark-toolkit-boundary"]
    commands = [check["command"] for check in profile["checks"]]
    assert (
        "python3 examples/benchmark-run-permission-policy-smoke.py"
        in commands
    ), profile
    assert all(check["tier"] == "default" for check in profile["checks"]), profile
    assert profile["deep_checks_available"] is True, profile
    assert profile["deep_checks_included"] is False, profile


def assert_explicit_catalog_profile_id_selects_family_profile() -> None:
    payload = build_catalog_canary_plan(
        profiles=["state-and-boundary"],
        max_checks_per_family=4,
    )
    assert payload["profile_count"] == 1, payload
    assert payload["domain_profile_count"] == 0, payload
    profile = payload["profiles"][0]
    assert profile["id"] == "state-and-boundary", profile
    assert profile["family"] == "State And Boundary", profile
    assert profile["selection_reasons"] == [
        "selected because this catalog profile was explicitly requested",
    ], profile
    assert "python3 examples/control_plane/todo-contract-smoke.py" in payload["commands"], payload
    assert all(
        check["source"] == "catalog_family"
        for check in payload["suggested_checks"]
    ), payload


def assert_catalog_canary_selects_own_profile_not_benchmark() -> None:
    payload = build_catalog_canary_plan(
        changed_files=["loopx/canary/planner.py", "loopx/canary/runner.py"],
        surfaces=["catalog canary runner"],
    )
    domain_profiles = {profile["id"]: profile for profile in payload["domain_profiles"]}
    assert "catalog-canary-contract" in domain_profiles, payload
    assert "benchmark-toolkit-boundary" not in domain_profiles, payload
    commands = payload["commands"]
    assert "python3 examples/canary/catalog-planner-smoke.py" in commands, payload
    assert "python3 examples/canary/catalog-run-e2e-smoke.py" in commands, payload
    assert "python3 examples/canary/smoke-suite-runner-smoke.py" in commands, payload
    assert "python3 examples/canary/pytest-smoke-suite-facade-smoke.py" not in commands, payload


def assert_catalog_canary_deep_profile_includes_pytest_facade() -> None:
    payload = build_catalog_canary_plan(
        profiles=["catalog-canary-contract"],
        include_deep_checks=True,
        max_checks_per_profile=4,
    )
    assert payload["domain_profile_count"] == 1, payload
    profile = payload["domain_profiles"][0]
    assert profile["id"] == "catalog-canary-contract", profile
    assert profile["deep_checks_included"] is True, profile
    commands = payload["commands"]
    assert "python3 examples/canary/pytest-smoke-suite-facade-smoke.py" in commands, payload


def assert_install_update_does_not_select_release_promotion() -> None:
    payload = build_catalog_canary_plan(
        changed_files=["loopx/doctor.py", "examples/install-local-smoke.py"],
        max_checks_per_profile=3,
    )
    domain_profiles = {profile["id"]: profile for profile in payload["domain_profiles"]}
    assert "install-update" in domain_profiles, payload
    assert "release-promotion" not in domain_profiles, payload
    commands = payload["commands"]
    assert "python3 examples/install-local-smoke.py" in commands, payload
    assert "python3 examples/loopx-update-smoke.py" in commands, payload
    assert all("canary-promotion-readiness-smoke.py" not in command for command in commands), payload


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _make_git_diff_selector_repo(tmp_dir: Path) -> tuple[Path, str]:
    repo = tmp_dir / "selector-repo"
    repo.mkdir()
    _run_git(repo, "init")
    _run_git(repo, "config", "user.email", "loopx-smoke@example.invalid")
    _run_git(repo, "config", "user.name", "LoopX Smoke")

    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _run_git(repo, "add", "README.md")
    _run_git(repo, "commit", "-m", "base")
    base_ref = _run_git(repo, "rev-parse", "HEAD").stdout.strip()

    committed_path = repo / "loopx" / "canary" / "planner.py"
    committed_path.parent.mkdir(parents=True)
    committed_path.write_text("# committed canary planner change\n", encoding="utf-8")
    _run_git(repo, "add", "loopx/canary/planner.py")
    _run_git(repo, "commit", "-m", "committed canary planner")

    unstaged_path = repo / "examples" / "canary" / "catalog-planner-smoke.py"
    unstaged_path.parent.mkdir(parents=True)
    unstaged_path.write_text("# tracked catalog canary smoke change\n", encoding="utf-8")
    _run_git(repo, "add", "examples/canary/catalog-planner-smoke.py")
    _run_git(repo, "commit", "-m", "tracked catalog canary smoke")

    staged_path = repo / "loopx" / "cli_commands" / "canary.py"
    staged_path.parent.mkdir(parents=True)
    staged_path.write_text("# staged cli canary change\n", encoding="utf-8")
    _run_git(repo, "add", "loopx/cli_commands/canary.py")

    unstaged_path.write_text("# unstaged catalog canary smoke change\n", encoding="utf-8")

    untracked_path = repo / "docs" / "new-catalog-canary-note.md"
    untracked_path.parent.mkdir(parents=True)
    untracked_path.write_text("# untracked catalog canary note\n", encoding="utf-8")
    return repo, base_ref


def assert_git_diff_selector_covers_pr_and_worktree_changes(tmp_dir: Path) -> None:
    repo, base_ref = _make_git_diff_selector_repo(tmp_dir)
    assert _run_git(repo, "diff", "--name-only", "--cached").stdout.splitlines() == [
        "loopx/cli_commands/canary.py",
    ]
    assert _run_git(repo, "diff", "--name-only").stdout.splitlines() == [
        "examples/canary/catalog-planner-smoke.py",
    ]
    assert _run_git(repo, "ls-files", "--others", "--exclude-standard").stdout.splitlines() == [
        "docs/new-catalog-canary-note.md",
    ]
    selector = collect_git_diff_changed_files(repo_root=repo, base_ref=base_ref)
    assert selector["ok"] is True, selector
    assert selector["successful_sources"] == ["base", "staged", "unstaged", "untracked"], selector
    assert selector["changed_files"] == [
        "examples/canary/catalog-planner-smoke.py",
        "loopx/canary/planner.py",
        "loopx/cli_commands/canary.py",
        "docs/new-catalog-canary-note.md",
    ], selector

    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--format",
            "json",
            "canary",
            "plan",
            "--from-git-diff",
            "--git-diff-base",
            base_ref,
        ],
        cwd=repo,
        env=env,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    payload = json.loads(completed.stdout)
    assert payload["selector_sources"]["git_diff"]["changed_file_count"] == 4, payload
    assert payload["selection_inputs"]["changed_files"] == selector["changed_files"], payload
    domain_profile_ids = {profile["id"] for profile in payload["domain_profiles"]}
    assert "catalog-canary-contract" in domain_profile_ids, payload
    assert "benchmark-toolkit-boundary" not in domain_profile_ids, payload
    assert "python3 examples/canary/catalog-planner-smoke.py" in payload["commands"], payload


def assert_coverage_audit_tracks_p0_p1_patterns() -> None:
    payload = build_catalog_canary_coverage_audit()
    assert payload["ok"] is True, payload
    assert payload["dry_run"] is True, payload
    assert payload["executes_checks"] is False, payload
    assert payload["priorities"] == ["P0", "P1"], payload
    assert payload["required_pattern_count"] >= 20, payload
    assert payload["missing_count"] == 0, payload
    assert payload["invalid_exception_count"] == 0, payload
    covered_ids = {row["pattern_id"] for row in payload["covered_patterns"]}
    assert {"IP-001", "IP-004", "IP-024", "IP-029"} <= covered_ids, payload


def assert_coverage_audit_reports_matrix_drift(tmp_dir: Path) -> None:
    catalog_text = CATALOG.read_text(encoding="utf-8")
    # Mutate one coverage token, independently of unrelated catalog additions.
    rows = catalog_text.splitlines(keepends=True)
    matching = [
        index for index, row in enumerate(rows)
        if row.startswith("| Planning Governance |")
        and "IP-024" in [pattern.strip() for pattern in row.split("|")[2].split(",")]
    ]
    assert len(matching) == 1, "expected one Planning Governance coverage row"
    index = matching[0]
    cells = rows[index].split("|")
    patterns = [pattern.strip() for pattern in cells[2].split(",")]
    assert patterns.count("IP-024") == 1, "drift probe requires exactly one IP-024 token"
    cells[2] = " " + ", ".join(pattern for pattern in patterns if pattern != "IP-024") + " "
    rows[index] = "|".join(cells)
    drift_text = "".join(rows)
    assert drift_text != catalog_text, "drift probe must change its input"
    drift_catalog = tmp_dir / "catalog-drift.md"
    drift_catalog.write_text(drift_text, encoding="utf-8")
    payload = build_catalog_canary_coverage_audit(catalog_path=drift_catalog)
    assert payload["ok"] is False, payload
    assert payload["missing_count"] == 1, payload
    assert payload["missing_patterns"][0]["pattern_id"] == "IP-024", payload

    excepted_catalog = tmp_dir / "catalog-deferred.md"
    excepted_catalog.write_text(
        drift_text
        + "\n\n"
        "## Canary Coverage Exceptions\n\n"
        "| Pattern ID | Canary Coverage Status | Rationale | Owner |\n"
        "| --- | --- | --- | --- |\n"
        "| IP-024 | deferred | waits for a repair-delta profile owner before default canary coverage | codex-main-control |\n",
        encoding="utf-8",
    )
    excepted_payload = build_catalog_canary_coverage_audit(catalog_path=excepted_catalog)
    assert excepted_payload["ok"] is True, excepted_payload
    assert excepted_payload["missing_count"] == 0, excepted_payload
    assert excepted_payload["excepted_count"] == 1, excepted_payload
    assert excepted_payload["excepted_patterns"][0]["pattern_id"] == "IP-024", excepted_payload


def assert_cli_json_plan_is_dry_run() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--format",
            "json",
            "canary",
            "plan",
            "--changed-file",
            "loopx/quota.py",
            "--surface",
            "scheduler hint",
            "--max-checks-per-family",
            "1",
        ],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    payload = json.loads(completed.stdout)
    assert payload["dry_run"] is True, payload
    assert payload["executes_checks"] is False, payload
    assert payload["profile_count"] >= 1, payload
    assert payload["suggested_check_count"] == len(payload["commands"]), payload
    assert payload["commands"], payload
    assert all(check["command"] in payload["commands"] for check in payload["suggested_checks"]), payload
    work_routing = next(profile for profile in payload["profiles"] if profile["family"] == "Work Routing")
    assert len(work_routing["candidate_checks"]) == 1, work_routing
    assert any(profile["id"] == "monitor-scheduler" for profile in payload["domain_profiles"]), payload


def assert_cli_profile_accepts_catalog_profile_id() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--format",
            "json",
            "canary",
            "plan",
            "--profile",
            "state-and-boundary",
            "--max-checks-per-family",
            "1",
        ],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    payload = json.loads(completed.stdout)
    assert payload["profile_count"] == 1, payload
    assert payload["domain_profile_count"] == 0, payload
    assert payload["profiles"][0]["id"] == "state-and-boundary", payload
    assert payload["commands"] == ["python3 examples/control_plane/todo-contract-smoke.py"], payload


def assert_cli_json_coverage_audit_is_dry_run() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--format",
            "json",
            "canary",
            "coverage-audit",
        ],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    payload = json.loads(completed.stdout)
    assert payload["dry_run"] is True, payload
    assert payload["executes_checks"] is False, payload
    assert payload["drift_count"] == 0, payload


def main() -> int:
    assert_profiles_come_from_catalog_matrix()
    assert_catalog_documents_selection_rules()
    assert_plan_selects_minimal_profiles_from_changed_surfaces()
    assert_scheduler_ack_route_profile_keeps_independent_checks()
    assert_pr_release_and_refactor_profiles_select()
    assert_explicit_profile_can_include_deep_checks()
    assert_benchmark_toolkit_changes_select_boundary_smoke()
    assert_explicit_catalog_profile_id_selects_family_profile()
    assert_catalog_canary_selects_own_profile_not_benchmark()
    assert_catalog_canary_deep_profile_includes_pytest_facade()
    assert_install_update_does_not_select_release_promotion()
    assert_coverage_audit_tracks_p0_p1_patterns()
    tmp = tempfile.mkdtemp(prefix="loopx-catalog-canary-smoke-")
    try:
        tmp_dir = Path(tmp)
        assert_coverage_audit_reports_matrix_drift(tmp_dir)
        assert_git_diff_selector_covers_pr_and_worktree_changes(tmp_dir)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    assert_cli_json_plan_is_dry_run()
    assert_cli_profile_accepts_catalog_profile_id()
    assert_cli_json_coverage_audit_is_dry_run()
    print("catalog-canary-planner-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
