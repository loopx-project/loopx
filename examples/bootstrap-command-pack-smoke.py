#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.control_plane.testing.continuation_verb_guard import (  # noqa: E402
    assert_no_continuation_verb,
)


def run_json(*args: str, env: dict[str, str] | None = None) -> dict[str, object]:
    process_env = os.environ.copy()
    process_env.pop("CODEX_THREAD_ID", None)
    if env:
        process_env.update(env)
    result = subprocess.run(
        [sys.executable, "-m", "loopx.cli", "--format", "json", *args],
        cwd=REPO_ROOT,
        env=process_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict)
    return payload


def assert_packet_summary_refs(
    payload: dict[str, object],
    *,
    packet_kind: str,
    compact_projection_default: bool = False,
) -> dict[str, object]:
    summary = payload["packet_summary"]
    assert isinstance(summary, dict)
    assert summary["schema_version"] == "loopx_start_goal_packet_summary_v0"
    assert summary["packet_kind"] == packet_kind
    compatibility = summary["compatibility"]
    assert isinstance(compatibility, dict)
    assert compatibility == {
        "legacy_fields_retained": not compact_projection_default,
        "compact_projection_default": compact_projection_default,
        "removal_gate": "explicit_host_shadow_parity",
    }
    detail_refs = summary["detail_refs"]
    assert isinstance(detail_refs, dict)
    for detail_ref in detail_refs.values():
        assert isinstance(detail_ref, dict)
        assert detail_ref["schema_version"] == "loopx_packet_json_pointer_ref_v0"
        pointer = str(detail_ref["json_pointer"])
        assert pointer.startswith("#/"), pointer
        current: object = payload
        for escaped_part in pointer[2:].split("/"):
            part = escaped_part.replace("~1", "/").replace("~0", "~")
            if isinstance(current, dict):
                current = current[part]
            elif isinstance(current, list):
                current = current[int(part)]
            else:
                raise AssertionError((pointer, current))
    measurement = summary["duplication_measurement"]
    assert isinstance(measurement, dict)
    assert measurement["schema_version"] == "loopx_packet_duplication_measurement_v0"
    assert (
        measurement["measurement_scope"]
        == "compatibility_projection_without_packet_summary"
    )
    assert int(measurement["serialized_bytes"]) > 0
    current_bytes = len(
        json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
    )
    assert current_bytes > int(measurement["serialized_bytes"])
    return summary


def write_connected_goal_fixture(
    project: Path,
    *,
    goal_id: str,
    agent_id: str,
) -> dict[Path, str]:
    state_file = project / ".codex" / "goals" / goal_id / "ACTIVE_GOAL_STATE.md"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_text = "# Active Goal State\n"
    state_file.write_text(state_text, encoding="utf-8")

    registry = project / ".loopx" / "registry.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry_text = (
        json.dumps(
            {
                "schema_version": "0.1",
                "goals": [
                    {
                        "id": goal_id,
                        "status": "active",
                        "repo": str(project),
                        "state_file": str(state_file.relative_to(project)),
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": [agent_id],
                        },
                    }
                ],
            },
            indent=2,
        )
        + "\n"
    )
    registry.write_text(registry_text, encoding="utf-8")
    return {registry: registry_text, state_file: state_text}


def assert_fixture_unchanged(snapshot: dict[Path, str]) -> None:
    for path, expected in snapshot.items():
        assert path.read_text(encoding="utf-8") == expected


def test_missing_project_stops_before_mutation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp) / "fresh-project"
        project.mkdir()
        payload = run_json(
            "bootstrap-command-pack",
            "--project",
            str(project),
            "--goal-id",
            "fresh-goal",
            "--agent-id",
            "codex-test-agent",
        )
        connection = payload["project_connection"]
        assert isinstance(connection, dict)
        assert payload["schema_version"] == "loopx_bootstrap_command_pack_v0"
        assert payload["slash_command"] == "/loopx"
        assert {"form": "/loopx <goal text>", "mode": "goal_plan_write_and_activate"} in payload["slash_forms"]
        slash_catalog = payload["available_slash_commands"]
        assert isinstance(slash_catalog, dict)
        assert slash_catalog["schema_version"] == "loopx_slash_command_catalog_v0"
        command_names = {item["command"] for item in slash_catalog["commands"]}
        assert "/loopx" in command_names
        assert "/loopx <goal text>" in command_names
        assert "/loopx-global-summary" in command_names
        assert "/loopx-global-gates" in command_names
        onboarding = payload["onboarding_hint"]
        assert onboarding["tell_new_users"] is True
        assert "LoopX command surface is available. Useful commands:" in onboarding["suggested_user_note"]
        assert "loopx slash-commands" in onboarding["suggested_user_note"]
        assert payload["read_only"] is True
        summary = assert_packet_summary_refs(
            payload, packet_kind="bootstrap_command_pack"
        )
        assert summary["next_step_kind"] == "confirm_before_bootstrap_mutation"
        assert connection["connection_state"] == "not_connected"
        assert connection["mutation_confirmation_required"] is True
        assert not (project / ".loopx").exists()
        assert not (project / ".codex").exists()

        safety = payload["safety_contract"]
        assert isinstance(safety, dict)
        assert safety["writes_registry"] is False
        assert safety["writes_state_file"] is False
        assert safety["spends_quota"] is False

        next_step = payload["recommended_next_step"]
        assert isinstance(next_step, dict)
        assert next_step["requires_user_confirmation"] is True
        assert "--dry-run" in str(next_step["dry_run_command"])
        assert "--dry-run" not in str(next_step["after_confirmation_command"])
        assert "/loopx-summary-all" not in json.dumps(payload)


def test_goal_text_invocation_plans_ranked_todos_before_activation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp) / "goal-start-project"
        project.mkdir()
        snapshot = write_connected_goal_fixture(
            project,
            goal_id="goal-start",
            agent_id="codex-test-agent",
        )
        payload = run_json(
            "bootstrap-command-pack",
            "--project",
            str(project),
            "--goal-id",
            "goal-start",
            "--agent-id",
            "codex-test-agent",
            "--goal-text",
            "Ship the lightweight issue triage workflow",
        )

        assert payload["goal_text"] == "Ship the lightweight issue triage workflow"
        summary = assert_packet_summary_refs(
            payload, packet_kind="bootstrap_command_pack"
        )
        assert summary["objective"] == "Ship the lightweight issue triage workflow"
        measurement = summary["duplication_measurement"]
        assert isinstance(measurement, dict)
        objective_measurement = measurement["objective_content"]
        assert isinstance(objective_measurement, dict)
        assert int(objective_measurement["substring_occurrences"]) > 1
        assert int(objective_measurement["duplicate_occurrences"]) > 0
        command_measurement = measurement["command_content"]
        assert isinstance(command_measurement, dict)
        assert int(command_measurement["duplicate_occurrences"]) > 0
        next_step = payload["recommended_next_step"]
        assert isinstance(next_step, dict)
        assert next_step["kind"] == "goal_plan_write_and_activate"
        assert next_step["requires_user_confirmation"] is False
        assert next_step["confirmation_source"] == "/loopx <goal text>"
        assert "--objective 'Ship the lightweight issue triage workflow'" in str(
            next_step["connect_command_if_needed"]
        )
        assert "--no-onboarding-scan" not in str(next_step["connect_command_if_needed"])

        goal_start = payload["goal_start_contract"]
        assert isinstance(goal_start, dict)
        assert goal_start["schema_version"] == "loopx_goal_start_command_v0"
        assert goal_start["planner"]["required_before_todo_write"] is True
        profiles = goal_start["planner"]["profiles"]
        assert profiles["open_ended_product_direction"]["suggested_items_min"] == 2
        assert profiles["open_ended_product_direction"]["suggested_items_max"] == 5
        assert profiles["clear_bounded_problem"]["item_count_policy"] == "planner_sized"
        assert profiles["clear_bounded_problem"][
            "may_reuse_current_todo_when_it_already_represents_the_plan"
        ] is True
        assert "minimum sufficient plan" in goal_start["planner"]["budget_policy"]
        ordering = goal_start["priority_ordering"]
        assert ordering["bucket_order"] == ["P0", "P1", "P2"]
        assert ordering["same_priority_tie_breaker"] == "planner_order_then_todo_write_order"
        assert "Todo index is same-priority rank" in ordering["storage_contract"]

        commands = payload["commands"]
        assert isinstance(commands, dict)
        plan_prompt = str(commands["goal_start_plan_prompt"])
        assert "returned `ordered_steps` + `goal_start_contract` are authoritative" in plan_prompt
        assert "stable unbound host gets a fresh public-safe agent" in plan_prompt
        assert "`loopx agent-onboard --list-agent-types`" in plan_prompt
        assert "only `selected_capability_route`" in plan_prompt
        assert "`capability show`" in plan_prompt
        assert "never infer from text/URLs" in plan_prompt
        assert "broad goal 2-5 public-safe items" in plan_prompt
        assert "minimum sufficient plan" in plan_prompt
        assert "no `--priority`" in plan_prompt
        assert "prefer Agent `advancement_task`" in plan_prompt
        assert "Write one business Todo before work" in plan_prompt
        assert "current Todo evidence + next executable Todo" in plan_prompt
        assert "Chat/model summaries are not durable state" in plan_prompt
        assert "surface the exact pasteable gate" in plan_prompt
        assert "typed `quota_guard`" in plan_prompt
        assert "validation + LoopX writeback or an exact blocker" in plan_prompt
        assert "need, preview, explicit apply" in plan_prompt
        assert "issue_fix_workflow_plan_template" in commands
        issue_fix_template = str(commands["issue_fix_workflow_plan_template"])
        assert "issue-fix workflow-plan" in issue_fix_template
        assert "--url <github-issue-or-pr-url>" in issue_fix_template
        assert "--repo-path <approved-repo>" in issue_fix_template
        assert "--repository-context-json <compact-context.json>" in issue_fix_template
        assert "--validation-label '<validation command>'" in issue_fix_template
        assert "issue_fix_feasibility_template" in commands
        feasibility_template = str(commands["issue_fix_feasibility_template"])
        assert "issue-fix feasibility" in feasibility_template
        assert "--reproduction-status" in feasibility_template
        assert "--scope-class" in feasibility_template
        assert "--repository-context-json <compact-context.json>" in feasibility_template
        assert "--goal-id" in feasibility_template
        assert "issue_fix_pr_lifecycle_template" in commands
        pr_lifecycle_template = str(commands["issue_fix_pr_lifecycle_template"])
        assert "issue-fix pr-lifecycle" in pr_lifecycle_template
        assert "--url <github-pr-url>" in pr_lifecycle_template
        assert "--goal-id" in pr_lifecycle_template
        assert str(payload["goal_id"]) in pr_lifecycle_template
        assert "--claimed-by codex-test-agent" in pr_lifecycle_template
        assert "--execute-transition" in pr_lifecycle_template
        assert "issue_fix_reviewer_request_template" in commands
        reviewer_request_template = str(commands["issue_fix_reviewer_request_template"])
        assert "issue-fix reviewer-request" in reviewer_request_template
        assert "--url <github-pr-url>" in reviewer_request_template
        assert "--repo-path <approved-repo>" in reviewer_request_template
        assert "--execute" in reviewer_request_template
        assert "--agent-id codex-test-agent" in str(commands["goal_start_quota_should_run"])
        refresh_command = str(commands["goal_start_refresh_state"])
        assert "--agent-id codex-test-agent" in refresh_command
        assert "--progress-scope agent_lane" in refresh_command
        assert "--health-check" not in refresh_command
        assert "Same-priority items use that write order as the tie-breaker" in str(payload["message"])
        assert "preview the issue-fix route:" not in str(payload["message"])
        assert "preview the issue-fix route before todo writeback" not in str(
            payload["message"]
        )
        domain_routes = goal_start["domain_route_hints"]
        assert domain_routes["issue_fix_workflow"]["when"] == (
            "selected_capability_route.capability_id is explicitly set to issue-fix"
        )
        assert "workflow-plan" in domain_routes["issue_fix_workflow"]["preview_command"]
        assert "feasibility" in domain_routes["issue_fix_workflow"]["decision_command"]
        assert "reviewer-request" in domain_routes["issue_fix_workflow"][
            "post_pr_reviewer_request_command"
        ]
        assert "pr-lifecycle" in domain_routes["issue_fix_workflow"]["post_pr_monitor_command"]
        assert (
            "keep private/external/publish/destructive/production gates"
            in domain_routes["issue_fix_workflow"]["writeback"]
        )
        assert "verify reviewer-request" in domain_routes["issue_fix_workflow"][
            "writeback"
        ]
        assert "monitor one PR state bucket" in domain_routes["issue_fix_workflow"][
            "writeback"
        ]
        assert_fixture_unchanged(snapshot)


def test_start_goal_guided_previews_transaction_without_mutation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp) / "guided-project"
        project.mkdir()
        snapshot = write_connected_goal_fixture(
            project,
            goal_id="guided-goal",
            agent_id="codex-test-agent",
        )
        start_args = (
            "start-goal",
            "--guided",
            "--project",
            str(project),
            "--goal-id",
            "guided-goal",
            "--agent-id",
            "codex-test-agent",
            "--goal-text",
            "Connect this repo and start an auto research lane",
        )
        selection = run_json(*start_args)
        selection_summary = assert_packet_summary_refs(
            selection,
            packet_kind="guided_start_host_surface_selection",
        )
        assert selection_summary["next_step_kind"] == "select_host_surface"
        assert selection["guided_transaction"]["blocked_by"] == "host_surface_selection"
        assert selection["safety_contract"]["writes_registry"] is False

        payload = run_json(*start_args, "--host-surface", "codex-app")

        assert payload["schema_version"] == "loopx_start_goal_guided_v0"
        assert payload["read_only"] is True
        assert payload["guided"] is True
        assert payload["goal_text"] == "Connect this repo and start an auto research lane"
        assert payload["goal_id"] == "guided-goal"
        summary = assert_packet_summary_refs(
            payload,
            packet_kind="guided_start_goal",
            compact_projection_default=True,
        )
        assert summary["objective"] == "Connect this repo and start an auto research lane"
        assert summary["next_step_kind"] == "goal_plan_write_and_activate"
        detail_refs = summary["detail_refs"]
        assert isinstance(detail_refs, dict)
        assert (
            detail_refs["command_pack_summary"]["json_pointer"]
            == "#/command_pack/packet_summary"
        )
        measurement = summary["duplication_measurement"]
        assert isinstance(measurement, dict)
        objective_measurement = measurement["objective_content"]
        assert isinstance(objective_measurement, dict)
        assert int(objective_measurement["substring_occurrences"]) > 1
        command_measurement = measurement["command_content"]
        assert isinstance(command_measurement, dict)
        assert int(command_measurement["duplicate_occurrences"]) > 0

        safety = payload["safety_contract"]
        assert isinstance(safety, dict)
        assert safety["writes_registry"] is False
        assert safety["writes_state_file"] is False
        assert safety["creates_heartbeat"] is False
        assert safety["spends_quota"] is False
        assert safety["force_bootstrap_allowed"] is False

        transaction = payload["guided_transaction"]
        assert isinstance(transaction, dict)
        assert transaction["mode"] == "dry_run_preview"
        step_ids = [step["id"] for step in transaction["ordered_steps"]]
        assert step_ids == [
            "inspect_connection",
            "connect_if_needed",
            "plan_ranked_todos",
            "write_ordered_todos",
            "read_back_authored_todos",
            "refresh_state",
            "activate_host_loop",
            "quota_guard",
            "scheduler_ack_when_needed",
        ]
        assert transaction["idempotency_policy"]["safe_to_rerun_preview"] is True
        assert "do not duplicate" in transaction["idempotency_policy"]["do_not_duplicate_existing_todos"].lower()
        preserve = transaction["preserve_todos_policy"]
        assert preserve["force_bootstrap_default"] == "forbidden_in_guided_flow"
        assert "backup-state" in preserve["before_destructive_reconnect"]
        assert "configure-goal incremental" in preserve["preferred_scope_change"]

        command_pack = payload["command_pack"]
        assert isinstance(command_pack, dict)
        assert command_pack["schema_version"] == "loopx_bootstrap_command_pack_v0"
        assert command_pack["projection_schema_version"] == "loopx_guided_command_pack_projection_v0"
        assert command_pack["projection_mode"] == "guided_start_compatibility"
        assert "--include-command-pack-detail" in str(command_pack["detail_command"])
        assert command_pack["goal_start_contract"]["planner"]["required_before_todo_write"] is True
        assert_fixture_unchanged(snapshot)


def test_start_goal_guided_blocks_orphaned_goal_state() -> None:
    """A reset that deleted the registry entry must not reopen the same goal."""

    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp) / "reset-project"
        project.mkdir()
        goal_id = "reset-goal"
        snapshot = write_connected_goal_fixture(
            project, goal_id=goal_id, agent_id="codex-retired"
        )
        state_file = project / ".codex" / "goals" / goal_id / "ACTIVE_GOAL_STATE.md"
        registry = project / ".loopx" / "registry.json"
        registry.write_text(
            json.dumps({"schema_version": "0.1", "goals": []}, indent=2) + "\n",
            encoding="utf-8",
        )

        payload = run_json(
            "start-goal",
            "--guided",
            "--project",
            str(project),
            "--goal-id",
            goal_id,
            "--host-surface",
            "codex-app",
            "--goal-text",
            "Continue the interrupted refactor",
        )

        connection = payload["project_connection"]
        assert connection["connection_state"] == "orphaned_goal_state", connection
        assert connection["goal_found"] is False, connection
        assert connection["bootstrap_continuation_allowed"] is False, connection
        assert connection["orphaned_goal_state"]["state_file_routes"] == [
            f".codex/goals/{goal_id}/ACTIVE_GOAL_STATE.md"
        ], connection

        transaction = payload["guided_transaction"]
        assert transaction["blocked_by"] == "orphaned_goal_state", transaction
        assert [step["id"] for step in transaction["ordered_steps"]] == [
            "inspect_connection",
            "resolve_orphaned_goal_state",
        ], transaction

        gate = transaction["orphaned_goal_state_gate"]
        assert gate["schema_version"] == "loopx_orphaned_goal_state_gate_v0", gate
        assert gate["forbidden_until_resolved"] == [
            "bootstrap",
            "agent_registration",
            "todo_write",
            "quota_spend",
            "host_loop_activation",
        ], gate
        for route in gate["resolution_routes"]:
            assert route["mutates"] is False, route
            assert "--execute" not in route["command"], route
            assert route["command"].splitlines()[-1].startswith("loopx "), route

        commands = payload["command_pack"]["commands"]
        for key in (
            "goal_start_connect_if_needed",
            "bootstrap_after_user_confirmation",
            "goal_start_plan_prompt",
        ):
            assert commands[key] is None, key

        safety = payload["safety_contract"]
        assert safety["force_bootstrap_allowed"] is False, safety
        assert safety["writes_state_file"] is False, safety
        assert safety["orphaned_goal_state_blocks_continuation"] is True, safety
        assert_packet_summary_refs(
            payload,
            packet_kind="guided_start_goal",
            compact_projection_default=True,
        )
        assert_fixture_unchanged({registry: registry.read_text(), state_file: snapshot[state_file]})


def test_start_goal_guided_fences_orphaned_state_for_every_absence_route() -> None:
    """No registry authority plus surviving state must fence, however that came about.

    One real CLI run per absence shape: a deleted registry file, a registry that
    declares no goal, and a registry that no longer parses each reach the fence
    through a different return in the inspection.
    """

    for shape, registry_text, absence in (
        ("missing", None, "not_connected"),
        ("empty", '{"schema_version": "0.1", "goals": []}\n', "registry_without_goal"),
        ("invalid", '{"schema_version": "0.1", "goals": [', "registry_invalid"),
    ):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "reset-project"
            state_file = project / ".codex" / "goals" / "reset-goal" / "ACTIVE_GOAL_STATE.md"
            state_file.parent.mkdir(parents=True)
            state_text = "# Orphaned goal state written by a retired lane\n"
            state_file.write_text(state_text, encoding="utf-8")
            if registry_text is not None:
                registry = project / ".loopx" / "registry.json"
                registry.parent.mkdir(parents=True)
                registry.write_text(registry_text, encoding="utf-8")

            payload = run_json(
                "start-goal",
                "--guided",
                "--project",
                str(project),
                "--goal-id",
                "reset-goal",
                "--host-surface",
                "codex-app",
                "--goal-text",
                "Continue the interrupted refactor",
            )

            connection = payload["project_connection"]
            assert connection["connection_state"] == "orphaned_goal_state", (shape, connection)
            assert connection["absent_connection_state"] == absence, (shape, connection)
            transaction = payload["guided_transaction"]
            assert transaction["blocked_by"] == "orphaned_goal_state", (shape, transaction)
            assert [step["id"] for step in transaction["ordered_steps"]] == [
                "inspect_connection",
                "resolve_orphaned_goal_state",
            ], (shape, transaction)
            commands = payload["command_pack"]["commands"]
            for key in (
                "goal_start_connect_if_needed",
                "goal_start_refresh_state",
                "goal_start_host_loop_activation",
                "goal_start_quota_should_run",
                "goal_start_plan_prompt",
            ):
                assert commands[key] is None, (shape, key)
            assert state_file.read_text(encoding="utf-8") == state_text, shape


def test_unparseable_registry_without_orphaned_state_keeps_onboarding() -> None:
    """A broken registry is its own repair action; the fence must not stand in for it."""

    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp) / "reset-project"
        registry = project / ".loopx" / "registry.json"
        registry.parent.mkdir(parents=True)
        registry.write_text('{"schema_version": "0.1", "goals": [', encoding="utf-8")

        payload = run_json(
            "start-goal",
            "--guided",
            "--project",
            str(project),
            "--goal-id",
            "reset-goal",
            "--host-surface",
            "codex-app",
            "--goal-text",
            "Continue the interrupted refactor",
        )

        connection = payload["project_connection"]
        assert connection["connection_state"] == "registry_invalid", connection
        assert "orphaned_goal_state" not in connection, connection
        assert connection["reason"], connection
        assert payload["command_pack"]["commands"]["goal_start_connect_if_needed"], payload


def test_fenced_project_surfaces_offer_no_continuation() -> None:
    """No real CLI surface over orphaned state may spell out a runnable mutation.

    The guided packet with its full command pack, and the standalone command pack
    with its rendered message, are both executed by hosts. Each carried
    ``register-agent --execute`` in nested fields the top-level fence never read.
    """

    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp) / "reset-project"
        state_file = project / ".codex" / "goals" / "reset-goal" / "ACTIVE_GOAL_STATE.md"
        state_file.parent.mkdir(parents=True)
        state_file.write_text(
            "# Orphaned goal state written by a retired lane\n", encoding="utf-8"
        )
        guided = run_json(
            "start-goal",
            "--guided",
            "--include-command-pack-detail",
            "--project",
            str(project),
            "--goal-id",
            "reset-goal",
            "--host-surface",
            "codex-app",
            "--goal-text",
            "Continue the interrupted refactor",
        )
        standalone = run_json(
            "bootstrap-command-pack",
            "--project",
            str(project),
            "--goal-id",
            "reset-goal",
            "--host-surface",
            "codex-app",
        )

        assert guided["project_connection"]["connection_state"] == "orphaned_goal_state"
        assert standalone["project_connection"]["connection_state"] == "orphaned_goal_state"
        assert_no_continuation_verb(guided, source="guided with command pack detail")
        assert_no_continuation_verb(standalone, source="standalone command pack")
        # Without a surviving read-only route the guard above could pass on a
        # packet that tells the operator nothing at all.
        assert guided["command_pack"]["commands"]["status"]
        assert state_file.is_file()


def test_start_goal_guided_requires_explicit_goal_for_multi_goal_project() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp) / "multi-goal-project"
        project.mkdir()
        goals = []
        for goal_id, status, agent_id in (
            ("completed-goal", "complete", "codex-completed"),
            ("active-goal", "active", "codex-active"),
        ):
            state_file = project / ".codex" / "goals" / goal_id / "ACTIVE_GOAL_STATE.md"
            state_file.parent.mkdir(parents=True, exist_ok=True)
            state_file.write_text("# Active Goal State\n", encoding="utf-8")
            goals.append(
                {
                    "id": goal_id,
                    "status": status,
                    "repo": str(project),
                    "state_file": f".codex/goals/{goal_id}/ACTIVE_GOAL_STATE.md",
                    "coordination": {
                        "agent_model": "peer_v1",
                        "registered_agents": [agent_id],
                    },
                }
            )
        registry = project / ".loopx" / "registry.json"
        registry.parent.mkdir(parents=True)
        registry.write_text(
            json.dumps({"schema_version": "0.1", "goals": goals}, indent=2) + "\n",
            encoding="utf-8",
        )

        payload = run_json(
            "start-goal",
            "--guided",
            "--project",
            str(project),
            "--host-surface",
            "codex-app",
            "--goal-text",
            "Add a new meta agent without reusing an old lane",
        )

        assert payload["goal_id"] is None, payload
        assert payload["agent_id"] is None, payload
        assert payload["project_connection"]["connection_state"] == "goal_selection_required", payload
        assert payload["recommended_next_step"]["kind"] == "select_goal", payload
        transaction = payload["guided_transaction"]
        assert transaction["blocked_by"] == "goal_selection", transaction
        assert [step["id"] for step in transaction["ordered_steps"]] == [
            "inspect_connection",
            "select_goal",
        ], transaction
        gate = payload["goal_selection_gate"]
        assert gate["schema_version"] == "loopx_goal_selection_gate_v0", gate
        assert [choice["goal_id"] for choice in gate["choices"]] == [
            "completed-goal",
            "active-goal",
        ], gate
        for choice in gate["choices"]:
            assert f"--goal-id {choice['goal_id']}" in choice["rerun_command"], choice
            assert "--goal-text 'Add a new meta agent without reusing an old lane'" in choice[
                "rerun_command"
            ], choice
        commands = payload["command_pack"]["commands"]
        assert set(commands) == {"doctor", "status", "goal_selection_choices"}, commands
        assert payload["command_pack"]["host_loop_activation"] == {
            "activation_state": "goal_selection_required",
            "activation_allowed": False,
            "activation_required_after_todo_write": False,
        }, payload
        assert payload["safety_contract"]["writes_registry"] is False, payload
        assert payload["safety_contract"]["creates_heartbeat"] is False, payload
        assert_packet_summary_refs(
            payload,
            packet_kind="guided_start_goal",
            compact_projection_default=True,
        )


def test_connected_project_reuses_existing_state() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp) / "connected-project"
        state_file = project / ".codex" / "goals" / "connected-goal" / "ACTIVE_GOAL_STATE.md"
        state_file.parent.mkdir(parents=True)
        state_file.write_text("# Active Goal State\n", encoding="utf-8")
        registry = project / ".loopx" / "registry.json"
        registry.parent.mkdir(parents=True)
        registry.write_text(
            json.dumps(
                {
                    "schema_version": "0.1",
                    "goals": [
                        {
                            "id": "connected-goal",
                            "status": "active",
                            "repo": str(project),
                            "state_file": ".codex/goals/connected-goal/ACTIVE_GOAL_STATE.md",
                        }
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        payload = run_json(
            "bootstrap-command-pack",
            "--project",
            str(project),
            "--goal-id",
            "connected-goal",
        )
        connection = payload["project_connection"]
        assert isinstance(connection, dict)
        assert connection["connection_state"] == "connected"
        assert connection["mutation_confirmation_required"] is False

        next_step = payload["recommended_next_step"]
        assert isinstance(next_step, dict)
        assert next_step["kind"] == "status_and_loop_activation"
        assert next_step["requires_user_confirmation"] is False
        assert "dry_run_command" not in next_step
        assert "loopx status" in str(payload["commands"])


def test_linked_git_worktree_reuses_canonical_source_registry() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        primary = root / "primary"
        primary.mkdir()
        subprocess.run(["git", "init"], cwd=primary, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        subprocess.run(
            ["git", "config", "user.email", "loopx@example.invalid"],
            cwd=primary,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        subprocess.run(
            ["git", "config", "user.name", "LoopX Smoke"],
            cwd=primary,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        (primary / "README.md").write_text("# primary\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=primary, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=primary,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        worktree = root / "linked-worktree"
        subprocess.run(
            ["git", "worktree", "add", "-b", "linked-smoke", str(worktree)],
            cwd=primary,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        canonical_goal = "canonical-goal"
        state_file = primary / ".codex" / "goals" / canonical_goal / "ACTIVE_GOAL_STATE.md"
        state_file.parent.mkdir(parents=True)
        state_file.write_text("# Active Goal State\n", encoding="utf-8")
        primary_registry = primary / ".loopx" / "registry.json"
        primary_registry.parent.mkdir(parents=True)
        primary_payload = {
            "schema_version": "0.1",
            "goals": [
                {
                    "id": canonical_goal,
                    "status": "active",
                    "repo": str(primary),
                    "state_file": ".codex/goals/canonical-goal/ACTIVE_GOAL_STATE.md",
                }
            ],
        }
        primary_registry.write_text(json.dumps(primary_payload, indent=2) + "\n", encoding="utf-8")

        shadow_goal = "linked-worktree-goal"
        shadow_state = worktree / ".codex" / "goals" / shadow_goal / "ACTIVE_GOAL_STATE.md"
        shadow_state.parent.mkdir(parents=True)
        shadow_state.write_text("# Shadow State\n", encoding="utf-8")
        shadow_registry = worktree / ".loopx" / "registry.json"
        shadow_registry.parent.mkdir(parents=True)
        shadow_registry.write_text(
            json.dumps(
                {
                    "schema_version": "0.1",
                    "goals": [
                        {
                            "id": shadow_goal,
                            "status": "active",
                            "repo": str(worktree),
                            "state_file": ".codex/goals/linked-worktree-goal/ACTIVE_GOAL_STATE.md",
                        }
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        runtime = root / "runtime"
        global_registry = runtime / "registry.global.json"
        global_registry.parent.mkdir(parents=True)
        global_payload = {
            "schema_version": "0.1",
            "registry_role": "global-local",
            "goals": [
                {
                    **primary_payload["goals"][0],
                    "source_registry": str(primary_registry),
                    "synced_at": "2026-06-28T00:00:00+00:00",
                },
                {
                    "id": shadow_goal,
                    "status": "active",
                    "repo": str(worktree),
                    "state_file": ".codex/goals/linked-worktree-goal/ACTIVE_GOAL_STATE.md",
                    "source_registry": str(shadow_registry),
                    "synced_at": "2026-06-28T00:00:00+00:00",
                },
            ],
        }
        global_registry.write_text(json.dumps(global_payload, indent=2) + "\n", encoding="utf-8")

        payload = run_json(
            "bootstrap-command-pack",
            "--project",
            str(worktree),
            env={"LOOPX_RUNTIME_ROOT": str(runtime)},
        )

        connection = payload["project_connection"]
        assert isinstance(connection, dict)
        alias = connection["canonical_project_alias"]
        assert isinstance(alias, dict)
        assert alias["applied"] is True, alias
        assert alias["kind"] == "git_worktree_canonical_source_registry", alias
        assert Path(str(connection["input_project"])).resolve() == worktree.resolve(), connection
        assert Path(str(payload["project"])).resolve() == primary.resolve(), payload
        assert payload["goal_id"] == canonical_goal, payload
        assert Path(str(connection["registry"])).resolve() == primary_registry.resolve(), connection
        assert Path(str(connection["state_file"])).resolve() == state_file.resolve(), connection
        assert connection["connection_state"] == "connected", connection
        assert payload["recommended_next_step"]["requires_user_confirmation"] is False, payload
        assert shadow_goal not in str(payload["message"])


def test_skill_slash_fallback_contract() -> None:
    skill_text = (REPO_ROOT / "skills" / "loopx-project" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    pr_review_skill_text = (REPO_ROOT / "skills" / "loopx-pr-review" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    normalized = " ".join(skill_text.split())
    pr_review_normalized = " ".join(pr_review_skill_text.split())

    assert "## Slash Command Fallback" in skill_text
    assert "`/loopx`" in skill_text
    assert "`/loopx <goal text>`" in skill_text
    assert "loopx bootstrap-command-pack --project ." in skill_text
    assert "loopx start-goal --guided --project ." in skill_text
    assert "canonical_project_alias" in skill_text
    assert (
        "`start-goal --project` keeps the requested project route" in skill_text
    )
    assert "Lower-level diagnostic command packs" in normalized
    assert '--goal-text "<GOAL_TEXT>"' in skill_text
    assert "bare `/loopx` read-only command" in skill_text
    assert "explicit goal-start intent" in normalized
    assert "planner order plus `todo add` write order" in normalized
    assert "do not silently downgrade `/loopx <goal text>`" in normalized
    assert "`/loopx-global-summary`" in skill_text
    assert "Legacy `/loop-global-*` forms" in normalized
    assert "loopx slash-commands" in skill_text
    assert "not project bootstrap commands" in normalized
    assert "`/loopx-pr-review`" in skill_text
    assert "load the narrower `loopx-pr-review` skill" in normalized
    assert "Do not handle `/loopx-pr-review` from this broader project skill" in normalized
    assert "do not route it to `loopx-pr-merge` unless" in normalized
    assert "loopx --format json pr-review --state all" not in skill_text
    assert "keeps ordinary queue discovery open-only" in pr_review_skill_text
    assert "explicit `--state merged|all`" in pr_review_skill_text
    assert "Save the full first JSON packet before printing a compact projection" in pr_review_normalized
    assert "agent_response_contract" in pr_review_skill_text
    assert "pull_requests[review_action_kind!=null].review_template" in pr_review_skill_text
    assert "pull_requests[review_action_kind!=null].evidence_commands" in pr_review_skill_text
    assert "Do not pipe the only copy through `jq`" in pr_review_skill_text
    assert "`review_groups`" in pr_review_skill_text
    assert "ranked actionable `review_sequence`" in pr_review_skill_text
    assert "must not appear in `review_sequence`" in pr_review_skill_text
    assert "complete Chinese five-block review" in pr_review_normalized
    assert "capability owns review depth, evidence requirements" in pr_review_normalized


def test_start_goal_guided_derives_display_name_from_goal_text() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp) / "display-name-project"
        project.mkdir()
        write_connected_goal_fixture(project, goal_id="display-goal", agent_id="codex-test-agent")

        derived = run_json(
            "start-goal",
            "--guided",
            "--project",
            str(project),
            "--goal-id",
            "display-goal",
            "--agent-id",
            "codex-test-agent",
            "--host-surface",
            "codex-app",
            "--goal-text",
            "修复 scheduler state path 覆盖问题",
            "--include-command-pack-detail",
        )
        connect_command = str(
            derived["command_pack"]["commands"]["goal_start_connect_if_needed"]
        )
        assert "--display-name" not in connect_command, connect_command
        assert derived["command_pack"].get("display_name") is None

        run_json(
            "bootstrap",
            "--project",
            str(project),
            "--goal-id",
            "derived-display-goal",
            "--objective",
            "修复 scheduler state path 覆盖问题",
            "--no-global-sync",
        )
        registry = json.loads(
            (project / ".loopx" / "registry.json").read_text(encoding="utf-8")
        )
        registry_goal = next(
            goal for goal in registry["goals"] if goal["id"] == "derived-display-goal"
        )
        assert registry_goal.get("display_name") == "修复 scheduler state path 覆盖问题"

        explicit = run_json(
            "start-goal",
            "--guided",
            "--project",
            str(project),
            "--goal-id",
            "display-goal",
            "--agent-id",
            "codex-test-agent",
            "--host-surface",
            "codex-app",
            "--goal-text",
            "some objective",
            "--display-name",
            "Custom Title",
            "--include-command-pack-detail",
        )
        explicit_command = str(
            explicit["command_pack"]["commands"]["goal_start_connect_if_needed"]
        )
        assert "--display-name 'Custom Title'" in explicit_command, explicit_command
        assert explicit["command_pack"].get("display_name") == "Custom Title"

        rejected = run_json(
            "start-goal",
            "--guided",
            "--project",
            str(project),
            "--goal-id",
            "display-goal",
            "--agent-id",
            "codex-test-agent",
            "--host-surface",
            "codex-app",
            "--goal-text",
            "/private/secret/path should not leak",
            "--include-command-pack-detail",
        )
        rejected_command = str(
            rejected["command_pack"]["commands"]["goal_start_connect_if_needed"]
        )
        assert "--display-name" not in rejected_command, rejected_command
        assert rejected["command_pack"].get("display_name") is None


def main() -> int:
    test_missing_project_stops_before_mutation()
    test_goal_text_invocation_plans_ranked_todos_before_activation()
    test_start_goal_guided_previews_transaction_without_mutation()
    test_start_goal_guided_blocks_orphaned_goal_state()
    test_start_goal_guided_fences_orphaned_state_for_every_absence_route()
    test_unparseable_registry_without_orphaned_state_keeps_onboarding()
    test_fenced_project_surfaces_offer_no_continuation()
    test_start_goal_guided_requires_explicit_goal_for_multi_goal_project()
    test_connected_project_reuses_existing_state()
    test_linked_git_worktree_reuses_canonical_source_registry()
    test_skill_slash_fallback_contract()
    test_start_goal_guided_derives_display_name_from_goal_text()
    print("bootstrap command pack smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
