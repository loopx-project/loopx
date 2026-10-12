from __future__ import annotations

import json
import shlex
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

import loopx.control_plane.testing.replan_semantic_action_behavior as replan_behavior
from loopx.control_plane.testing.model_tool_behavior import (
    ScriptedAssistantAction,
    ScriptedDoubaoExecTransport,
    ScriptedExecToolAction,
)
from loopx.control_plane.testing.replan_semantic_action_behavior import (
    DoubaoReplanSemanticActionBehaviorActor,
    _build_fixture,
    _execute_loopx,
)


def _latest_quota_packet(request: Mapping[str, object]) -> dict[str, object]:
    messages = request.get("messages")
    if not isinstance(messages, list) or not messages:
        raise AssertionError("model request must include a real quota result")
    for message in reversed(messages):
        if not isinstance(message, Mapping) or message.get("role") != "tool":
            continue
        try:
            packet = json.loads(str(message.get("content") or ""))
        except json.JSONDecodeError:
            continue
        if isinstance(packet, dict) and packet.get("replan_action_packet"):
            return packet
    raise AssertionError("semantic action must follow a real quota packet")


def _semantic_action(
    request: Mapping[str, object],
    *,
    surface_id: str = "surface-permission-config",
) -> ScriptedExecToolAction:
    packet = _latest_quota_packet(request)
    action = packet["replan_action_packet"]
    assert isinstance(action, Mapping)
    assert "new_surface" in action["uncovered_frontier"]["required_any_of"]
    return ScriptedExecToolAction(
        "loopx --format json --registry ignored --runtime-root ignored "
        "refresh-state --goal-id replan-semantic-action-fixture "
        "--agent-id codex-replan-semantic-action --progress-scope agent_lane "
        "--classification bounded_replan_progress "
        "--recommended-action inspect-the-new-surface "
        "--delivery-batch-scale single_surface "
        "--delivery-outcome outcome_progress "
        "--progress-result-class advanced "
        f"--progress-surface-id {surface_id} "
        "--progress-hypothesis-id hypothesis-permission-default "
        "--progress-probe-kind static-contract-read "
        "--progress-evidence-id evidence-permission-config "
        "--no-global-sync --suppress-external-sinks"
    )


def _exhausted_action(
    request: Mapping[str, object],
) -> ScriptedExecToolAction:
    packet = _latest_quota_packet(request)
    action = packet["replan_action_packet"]
    assert isinstance(action, Mapping)
    assert (
        "coverage_backed_exploration_exhausted"
        in action["uncovered_frontier"]["required_any_of"]
    )
    return ScriptedExecToolAction(
        "loopx --format json --registry ignored --runtime-root ignored "
        "refresh-state --goal-id replan-semantic-action-fixture "
        "--agent-id codex-replan-semantic-action --progress-scope agent_lane "
        "--classification bounded_replan_exhausted "
        "--recommended-action bounded-goal-coverage-exhausted "
        "--delivery-batch-scale single_surface "
        "--delivery-outcome surface_only "
        "--progress-result-class exploration_exhausted "
        "--progress-coverage-scope-id coverage-fixture-all-surfaces "
        "--progress-coverage-complete "
        "--progress-evidence-id evidence-frontier-inventory "
        "--no-global-sync --suppress-external-sinks"
    )


def _inline_explore_context_action(
    request: Mapping[str, object],
) -> ScriptedExecToolAction:
    packet = _latest_quota_packet(request)
    interaction = packet["interaction_contract"]
    assert isinstance(interaction, Mapping)
    channel = interaction["agent_channel"]
    assert isinstance(channel, Mapping)
    work_context = channel["work_context"]
    assert isinstance(work_context, Mapping)
    sources = work_context["sources"]
    assert isinstance(sources, list)
    explore_sources = [
        source for source in sources
        if isinstance(source, Mapping)
        and source.get("kind") == "explore_turn_context"
    ]
    assert len(explore_sources) == 1
    source = explore_sources[0]
    content = source.get("content")
    assert isinstance(content, Mapping)
    assert content.get("goal_id") == "replan-semantic-action-fixture"
    assert content.get("agent_id") == "codex-replan-semantic-action"
    boundary = content.get("boundary")
    assert isinstance(boundary, Mapping)
    assert boundary.get("read_only") is True
    assert isinstance(source.get("command"), str) and source["command"]
    assert not any(
        isinstance(read, Mapping) and read.get("kind") == "explore_turn_context"
        for read in channel.get("required_reads", [])
    )
    return ScriptedExecToolAction(command="cat replan-frontier.json")


def _composition_successor_action(
    request: Mapping[str, object],
) -> ScriptedExecToolAction:
    packet = _latest_quota_packet(request)
    frontier = packet.get("bounded_research_frontier")
    assert isinstance(frontier, Mapping)
    selected_gap = frontier.get("selected_gap")
    assert isinstance(selected_gap, Mapping)
    assert selected_gap["required_outcome"] == "joint_experiment_result"
    action = packet["replan_action_packet"]
    assert isinstance(action, Mapping)
    bounded = action.get("bounded_frontier")
    assert isinstance(bounded, Mapping)
    assert bounded["gap_id"] == selected_gap["gap_id"]
    writeback = action.get("writeback_contract")
    assert isinstance(writeback, Mapping)
    command = str(writeback.get("successor_command") or "")
    assert command
    command_tokens = shlex.split(command)
    assert command_tokens[0] == "loopx"
    command_tokens[1:1] = ["--format", "json", "--registry", "ignored"]
    return ScriptedExecToolAction(command=shlex.join(command_tokens))


def test_real_tool_loop_chooses_and_persists_semantic_replan_action(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path / "oracle")
    transport = ScriptedDoubaoExecTransport(
        [
            ScriptedExecToolAction(command="date -Iseconds"),
            ScriptedExecToolAction(command=fixture.quota_guard_command),
            ScriptedExecToolAction(command="cat replan-frontier.json"),
            ScriptedExecToolAction(command="cat fixture/permission-config.json"),
            _semantic_action,
        ]
    )
    receipt = DoubaoReplanSemanticActionBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="replan-semantic-action-001",
        fixture_root=tmp_path / "actor",
    )

    assert receipt["qualification_passed"] is True, receipt
    assert receipt["observed_tool_sequence"] == [
        "clock",
        "quota_should_run",
        "workspace_read",
        "workspace_read",
        "semantic_replan_writeback",
    ]
    assert receipt["context_delivery"] == "host_projected"
    assert receipt["semantic_action_accepted"] is True
    assert "new_surface" in receipt["selected_semantic_outcomes"]
    assert "new_surface" in receipt["required_semantic_outcomes"]
    assert receipt["replan_obligation_id"].startswith("replan-")
    assert receipt["boundary"] == {
        "raw_prompt_persisted": False,
        "raw_provider_response_persisted": False,
        "raw_command_persisted": False,
        "filesystem_writes_executed": True,
        "writes_limited_to_temporary_fixture": True,
        "external_writes_executed": False,
        "shell_commands_executed": False,
        "read_only_host_commands_executed": True,
    }

    system_prompt = transport.requests[0]["messages"][0]["content"]
    assert "`cat` only the exact path named in active_state_next_action" in system_prompt
    assert "`cat` only the source_ref returned by that target" in system_prompt
    assert "interaction_contract.cli_channel.next_cli_actions[0]" in system_prompt
    assert "Do not use ls, find, rg, or paged help" in system_prompt
    assert "exactly `loopx refresh-state --help` once" in system_prompt
    assert "execute the projected typed writeback" in system_prompt
    assert "execute exactly one loopx refresh-state command" in system_prompt
    assert "do not create a Todo, chain commands, or combine actions" in system_prompt

    quota_packet = json.loads(transport.requests[2]["messages"][-1]["content"])
    assert quota_packet.get("required_reads") in (None, [])
    assert "successor Todo" not in quota_packet["active_state_next_action"]
    assert quota_packet["autonomous_replan_obligation"]["replan_context"][
        "delivery_receipt"
    ]["status"] == "delivered"
    assert quota_packet["replan_action_packet"]["obligation_id"] == receipt[
        "replan_obligation_id"
    ]
    assert quota_packet["replan_action_packet"]["writeback_contract"] == {}
    assert quota_packet["interaction_contract"]["agent_channel"][
        "primary_action"
    ] == "produce one typed outcome from the host-projected replan action packet"
    next_cli_actions = quota_packet["interaction_contract"]["cli_channel"][
        "next_cli_actions"
    ]
    assert any("refresh-state" in action for action in next_cli_actions)
    assert not any("successor_command" in action for action in next_cli_actions)

    index_path = (
        tmp_path
        / "actor"
        / "runtime"
        / "goals"
        / "replan-semantic-action-fixture"
        / "runs"
        / "index.jsonl"
    )
    latest = json.loads(index_path.read_text(encoding="utf-8").splitlines()[-1])
    assert latest["progress_observation"]["surface_id"] == (
        "surface-permission-config"
    )
    assert latest["autonomous_replan_ack"]["semantic_delta"]["accepted"] is True


def test_semantic_cli_execution_failure_retains_command_kind(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _build_fixture(tmp_path / "oracle")
    transport = ScriptedDoubaoExecTransport(
        [
            ScriptedExecToolAction(command=fixture.quota_guard_command),
            ScriptedExecToolAction(command="cat replan-frontier.json"),
            ScriptedExecToolAction(command="cat fixture/permission-config.json"),
            _semantic_action,
        ]
    )
    original_execute = replan_behavior._execute_loopx

    def fail_semantic_writeback(command: str, **kwargs: Any) -> str:
        if "refresh-state" in command:
            raise RuntimeError("synthetic CLI execution failure")
        return original_execute(command, **kwargs)

    monkeypatch.setattr(replan_behavior, "_execute_loopx", fail_semantic_writeback)
    receipt = DoubaoReplanSemanticActionBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="replan-semantic-action-cli-failure",
        fixture_root=tmp_path / "actor",
    )

    assert receipt["qualification_passed"] is False
    assert receipt["failure_code"] == "semantic_replan_writeback_execution_failed"
    assert receipt["observed_tool_sequence"][-1] == "semantic_replan_writeback"


def test_real_tool_loop_allows_bounded_refresh_state_help(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path / "oracle")
    transport = ScriptedDoubaoExecTransport(
        [
            ScriptedExecToolAction(command=fixture.quota_guard_command),
            ScriptedExecToolAction(command="cat replan-frontier.json"),
            ScriptedExecToolAction(command="cat fixture/permission-config.json"),
            ScriptedExecToolAction(
                command="loopx refresh-state --help 2>&1 | head -60"
            ),
            _semantic_action,
        ]
    )
    receipt = DoubaoReplanSemanticActionBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="replan-bounded-refresh-state-help",
        fixture_root=tmp_path / "actor",
    )

    assert receipt["qualification_passed"] is True, receipt
    assert receipt["observed_tool_sequence"] == [
        "quota_should_run",
        "workspace_read",
        "workspace_read",
        "refresh_state_help",
        "semantic_replan_writeback",
    ]
    assert receipt["semantic_action_accepted"] is True


def test_real_tool_loop_preserves_projected_targets_through_content_pipelines(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path / "oracle")
    transport = ScriptedDoubaoExecTransport(
        [
            ScriptedExecToolAction(command=fixture.quota_guard_command),
            ScriptedExecToolAction(
                command="cat replan-frontier.json 2>/dev/null | head -200"
            ),
            ScriptedExecToolAction(
                command="cat fixture/permission-config.json | head -200"
            ),
            _semantic_action,
        ]
    )
    receipt = DoubaoReplanSemanticActionBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="replan-bounded-content-pipelines",
        fixture_root=tmp_path / "actor",
    )

    assert receipt["qualification_passed"] is True, receipt
    assert receipt["observed_tool_sequence"] == [
        "quota_should_run",
        "workspace_read",
        "workspace_read",
        "semantic_replan_writeback",
    ]
    assert receipt["semantic_action_accepted"] is True


def test_refresh_state_help_requires_projected_frontier_reads(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path / "oracle")
    transport = ScriptedDoubaoExecTransport(
        [
            ScriptedExecToolAction(command=fixture.quota_guard_command),
            ScriptedExecToolAction(command="loopx refresh-state --help"),
        ]
    )
    receipt = DoubaoReplanSemanticActionBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="replan-help-before-frontier",
        fixture_root=tmp_path / "actor",
    )

    assert receipt["qualification_passed"] is False
    assert receipt["failure_code"] == "refresh_state_help_before_frontier_read"


def test_model_exits_after_replan_confirms_goal_coverage_is_exhausted(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path / "oracle", exhausted_frontier=True)
    transport = ScriptedDoubaoExecTransport(
        [
            ScriptedExecToolAction(command=fixture.quota_guard_command),
            ScriptedExecToolAction(command="cat replan-frontier.json"),
            _exhausted_action,
        ]
    )

    receipt = DoubaoReplanSemanticActionBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="replan-exhausted-exit-001",
        fixture_root=tmp_path / "actor",
        exhausted_frontier=True,
    )

    assert receipt["qualification_passed"] is True, receipt
    assert receipt["observed_tool_sequence"] == [
        "quota_should_run",
        "workspace_read",
        "semantic_replan_writeback",
    ]
    assert receipt["selected_semantic_outcomes"] == [
        "coverage_backed_exploration_exhausted"
    ]
    assert receipt["semantic_reentry"] == {
        "decision": "skip",
        "effective_action": "monitor_quiet_skip",
        "replan_rule": "future_monitor_wait",
        "replan_closed": True,
        "exited": True,
    }


@pytest.mark.parametrize(
    ("terminal_args", "expected_error"),
    [
        (
            "--progress-result-class no_followup ",
            "no_followup requires --progress-coverage-scope-id",
        ),
        (
            "--progress-result-class exploration_exhausted ",
            "exploration_exhausted requires --progress-coverage-scope-id",
        ),
        (
            "--progress-result-class exploration_exhausted "
            "--progress-coverage-scope-id coverage-fixture-all-surfaces ",
            "exploration_exhausted requires --progress-coverage-complete",
        ),
    ],
)
def test_terminal_progress_cli_reports_missing_coverage_contract(
    tmp_path: Path,
    terminal_args: str,
    expected_error: str,
) -> None:
    fixture = _build_fixture(tmp_path / "fixture", exhausted_frontier=True)
    index_path = (
        fixture.runtime_root
        / "goals"
        / "replan-semantic-action-fixture"
        / "runs"
        / "index.jsonl"
    )
    before = index_path.read_text(encoding="utf-8")

    with pytest.raises(RuntimeError, match=expected_error):
        _execute_loopx(
            "loopx --format json --registry ignored --runtime-root ignored "
            "refresh-state --goal-id replan-semantic-action-fixture "
            "--agent-id codex-replan-semantic-action "
            "--progress-scope agent_lane "
            "--classification bounded_replan_terminal "
            f"{terminal_args}"
            "--progress-evidence-id evidence-frontier-inventory "
            "--no-global-sync --suppress-external-sinks",
            fixture=fixture,
            turn_instance_id="invalid-terminal-progress-turn",
        )

    assert index_path.read_text(encoding="utf-8") == before


def test_real_tool_loop_selects_composition_gap_and_creates_bound_successor(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(
        tmp_path / "oracle",
        composition_frontier=True,
    )
    transport = ScriptedDoubaoExecTransport(
        [
            ScriptedExecToolAction(command=fixture.quota_guard_command),
            _inline_explore_context_action,
            ScriptedExecToolAction(command="cat fixture/permission-config.json"),
            _composition_successor_action,
        ]
    )
    receipt = DoubaoReplanSemanticActionBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="replan-composition-successor-001",
        fixture_root=tmp_path / "actor",
        composition_frontier=True,
    )

    assert receipt["qualification_passed"] is True, receipt
    assert receipt["observed_tool_sequence"] == [
        "quota_should_run",
        "workspace_read",
        "workspace_read",
        "replan_successor_create",
    ]
    assert receipt["selected_semantic_outcomes"] == ["new_runnable_successor"]
    successor_reentry = receipt["successor_reentry"]
    assert successor_reentry["selected_todo_id"].startswith("todo_")
    assert successor_reentry["decision"] == "run"
    assert successor_reentry["effective_action"] == "normal_run"
    assert successor_reentry["replan_closed"] is True
    assert successor_reentry["composition_experiment_ref"] == (
        "experiment-permission-composition"
    )
    assert successor_reentry["composition_status"] == "scheduled"

    quota_packet = json.loads(transport.requests[1]["messages"][-1]["content"])
    selected_gap = quota_packet["bounded_research_frontier"]["selected_gap"]
    assert selected_gap["experiment_node_ref"] == (
        "experiment-permission-composition"
    )
    assert quota_packet["replan_action_packet"]["bounded_frontier"][
        "experiment_node_ref"
    ] == selected_gap["experiment_node_ref"]


def test_required_explore_read_uses_nested_interaction_contract() -> None:
    command = (
        "loopx --format json explore turn-context --goal-id "
        "replan-semantic-action-fixture --agent-id codex-replan-semantic-action"
    )
    packet = {
        "interaction_contract": {
            "agent_channel": {
                "required_reads": [
                    {"kind": "goal_state", "command": "cat goal-state.md"},
                    {
                        "kind": "explore_turn_context",
                        "source": "turn_start_capability_hook",
                        "ordering": "before_work",
                        "command": command,
                    },
                ],
            },
        },
    }

    assert replan_behavior._required_explore_read_command(packet) == command


def test_action_outside_observed_frontier_is_rejected(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path / "oracle")
    transport = ScriptedDoubaoExecTransport(
        [
            ScriptedExecToolAction(command=fixture.quota_guard_command),
            ScriptedExecToolAction(command="cat replan-frontier.json"),
            ScriptedExecToolAction(command="cat fixture/permission-config.json"),
            lambda request: _semantic_action(request, surface_id="surface-existing"),
        ]
    )

    receipt = DoubaoReplanSemanticActionBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="replan-repeat-rejected",
        fixture_root=tmp_path / "actor",
    )

    assert receipt["qualification_passed"] is False
    assert receipt["failure_code"] == "semantic_action_misses_uncovered_frontier"


def test_fully_repeated_observation_cannot_close_replan(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path / "oracle")

    def repeated_action(request: Mapping[str, object]) -> ScriptedExecToolAction:
        _latest_quota_packet(request)
        return ScriptedExecToolAction(
            "loopx --format json --registry ignored --runtime-root ignored "
            "refresh-state --goal-id replan-semantic-action-fixture "
            "--agent-id codex-replan-semantic-action --progress-scope agent_lane "
            "--classification differently-worded-progress "
            "--progress-result-class advanced "
            "--progress-surface-id surface-existing "
            "--progress-hypothesis-id hypothesis-existing "
            "--progress-probe-kind probe-existing "
            "--progress-evidence-id evidence-existing "
            "--no-global-sync --suppress-external-sinks"
        )

    transport = ScriptedDoubaoExecTransport(
        [
            ScriptedExecToolAction(command=fixture.quota_guard_command),
            ScriptedExecToolAction(command="cat replan-frontier.json"),
            ScriptedExecToolAction(command="cat fixture/permission-config.json"),
            repeated_action,
        ]
    )
    receipt = DoubaoReplanSemanticActionBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="replan-full-repeat-rejected",
        fixture_root=tmp_path / "actor",
    )

    assert receipt["qualification_passed"] is False
    assert receipt["failure_code"] == "semantic_action_misses_uncovered_frontier"
    assert receipt["semantic_action_accepted"] is False


def test_manual_evidence_read_is_context_not_replan_completion(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path / "oracle")
    transport = ScriptedDoubaoExecTransport(
        [
            ScriptedExecToolAction(command=fixture.quota_guard_command),
            ScriptedExecToolAction(
                command=(
                    "loopx --format json history "
                    "--goal-id replan-semantic-action-fixture "
                    "--agent-id codex-replan-semantic-action"
                )
            ),
        ]
    )
    receipt = DoubaoReplanSemanticActionBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="replan-read-only-rejected",
        fixture_root=tmp_path / "actor",
    )

    assert receipt["qualification_passed"] is False
    assert receipt["failure_code"] == "manual_evidence_read_is_not_replan"


def test_semantic_action_must_follow_real_frontier_read(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path / "oracle")
    transport = ScriptedDoubaoExecTransport(
        [
            ScriptedExecToolAction(command=fixture.quota_guard_command),
            _semantic_action,
        ]
    )
    receipt = DoubaoReplanSemanticActionBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="replan-action-before-frontier-read",
        fixture_root=tmp_path / "actor",
    )

    assert receipt["qualification_passed"] is False
    assert receipt["failure_code"] == "semantic_action_before_frontier_read"


def test_model_can_create_and_bind_a_real_runnable_successor(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path / "oracle")

    def create_bound_successor(
        request: Mapping[str, object],
    ) -> ScriptedExecToolAction:
        packet = _latest_quota_packet(request)
        obligation = packet["autonomous_replan_obligation"]
        assert isinstance(obligation, Mapping)
        return ScriptedExecToolAction(
            "loopx --format json --registry ignored todo add "
            "--goal-id replan-semantic-action-fixture --role agent "
            "--task-class advancement_task "
            "--action-kind validate "
            "--task-domain research "
            "--target-key surface:permission-contract "
            "--text '[P0] Inspect the uncovered permission contract' "
            "--claimed-by codex-replan-semantic-action "
            f"--replan-obligation-id {obligation['obligation_id']}"
        )

    transport = ScriptedDoubaoExecTransport(
        [
            ScriptedExecToolAction(command=fixture.quota_guard_command),
            ScriptedExecToolAction(command="cat replan-frontier.json"),
            ScriptedExecToolAction(command="cat fixture/permission-config.json"),
            create_bound_successor,
        ]
    )
    receipt = DoubaoReplanSemanticActionBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="replan-real-successor",
        fixture_root=tmp_path / "actor",
    )

    assert receipt["qualification_passed"] is True, receipt
    assert receipt["observed_tool_sequence"] == [
        "quota_should_run",
        "workspace_read",
        "workspace_read",
        "replan_successor_create",
    ]
    assert receipt["selected_semantic_outcomes"] == ["new_runnable_successor"]
    successor_reentry = receipt["successor_reentry"]
    assert successor_reentry["decision"] == "run"
    assert successor_reentry["effective_action"] == "normal_run"
    assert successor_reentry["replan_closed"] is True


def test_successor_dry_run_shares_quota_agent_scope_for_user_gates(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path / "fixture")
    other_agent_id = "codex-replan-other-agent"
    registry = json.loads(fixture.global_registry_path.read_text(encoding="utf-8"))
    registry["goals"][0]["coordination"]["registered_agents"].append(
        other_agent_id
    )
    registry["goals"][0]["coordination"]["agent_profiles"][other_agent_id] = {
        "schema_version": "agent_profile_v1",
        "agent_id": other_agent_id,
        "profile_role": "independent-review",
        "scope_summary": "Review an independent bounded delivery.",
        "default_task_classes": ["user_action"],
        "vision_requirement": "optional",
    }
    registry_text = json.dumps(
        registry,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    for registry_path in (
        fixture.global_registry_path,
        fixture.project_root / ".loopx" / "registry.json",
    ):
        registry_path.write_text(registry_text, encoding="utf-8")
    state_path = (
        fixture.project_root
        / ".codex"
        / "goals"
        / "replan-semantic-action-fixture"
        / "ACTIVE_GOAL_STATE.md"
    )
    state_text = state_path.read_text(encoding="utf-8").replace(
        "## Agent Todo\n",
        (
            "## User Todo / Owner Review Reading Queue\n\n"
            "- [ ] [P0] Review the other agent's independent delivery.\n"
            "  <!-- loopx:todo todo_id=todo_other_agent_review status=open "
            "task_class=user_action "
            "bound_agent=codex-replan-other-agent -->\n\n"
            "## Agent Todo\n"
        ),
    )
    state_path.write_text(state_text, encoding="utf-8")

    quota = json.loads(
        _execute_loopx(
            fixture.quota_guard_command,
            fixture=fixture,
            turn_instance_id="agent-scoped-quota-turn",
        )
    )
    obligation_id = quota["autonomous_replan_obligation"]["obligation_id"]
    before = state_path.read_text(encoding="utf-8")
    dry_run = json.loads(
        _execute_loopx(
            "loopx --format json --registry ignored --runtime-root ignored "
            "todo add --goal-id replan-semantic-action-fixture "
            "--role agent --task-class advancement_task "
            "--action-kind inspect --target-key surface:next-bounded-slice "
            "--text '[P0] Inspect the next bounded surface' "
            "--claimed-by codex-replan-semantic-action "
            f"--replan-obligation-id {obligation_id} --dry-run",
            fixture=fixture,
            turn_instance_id="agent-scoped-successor-turn",
        )
    )

    assert dry_run["ok"] is True
    assert dry_run["dry_run"] is True
    assert dry_run["added"] is True
    assert state_path.read_text(encoding="utf-8") == before


def test_stale_successor_obligation_is_rejected_before_todo_mutation(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path / "fixture")
    state_path = (
        fixture.project_root
        / ".codex"
        / "goals"
        / "replan-semantic-action-fixture"
        / "ACTIVE_GOAL_STATE.md"
    )
    before = state_path.read_text(encoding="utf-8")

    with pytest.raises(RuntimeError, match="does not match the current open obligation"):
        _execute_loopx(
            "loopx --format json --registry ignored todo add "
            "--goal-id replan-semantic-action-fixture --role agent "
            "--task-class advancement_task "
            "--action-kind validate --target-key surface:stale-target "
            "--text '[P0] Stale successor must not be written' "
            "--claimed-by codex-replan-semantic-action "
            "--replan-obligation-id replan-0000000000000000",
            fixture=fixture,
            turn_instance_id="stale-successor-turn",
        )

    assert state_path.read_text(encoding="utf-8") == before


def test_untyped_successor_is_rejected_before_todo_mutation(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path / "fixture")
    state_path = (
        fixture.project_root
        / ".codex"
        / "goals"
        / "replan-semantic-action-fixture"
        / "ACTIVE_GOAL_STATE.md"
    )
    quota = json.loads(
        _execute_loopx(
            fixture.quota_guard_command,
            fixture=fixture,
            turn_instance_id="untyped-successor-turn",
        )
    )
    obligation_id = quota["autonomous_replan_obligation"]["obligation_id"]
    before = state_path.read_text(encoding="utf-8")

    with pytest.raises(RuntimeError, match="requires --action-kind"):
        _execute_loopx(
            "loopx --format json --registry ignored todo add "
            "--goal-id replan-semantic-action-fixture --role agent "
            "--task-class advancement_task "
            "--text '[P0] Replan again without an executable target' "
            "--claimed-by codex-replan-semantic-action "
            f"--replan-obligation-id {obligation_id}",
            fixture=fixture,
            turn_instance_id="untyped-successor-turn",
        )

    assert state_path.read_text(encoding="utf-8") == before


def test_semantic_writeback_before_quota_is_rejected(tmp_path: Path) -> None:
    transport = ScriptedDoubaoExecTransport(
        [
            ScriptedExecToolAction(
                command=(
                    "loopx --format json --registry ignored --runtime-root ignored "
                    "refresh-state --goal-id replan-semantic-action-fixture "
                    "--agent-id codex-replan-semantic-action "
                    "--progress-scope agent_lane "
                    "--progress-result-class advanced "
                    "--progress-surface-id surface-new "
                    "--no-global-sync --suppress-external-sinks"
                )
            )
        ]
    )
    receipt = DoubaoReplanSemanticActionBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="replan-action-before-quota",
        fixture_root=tmp_path,
    )

    assert receipt["qualification_passed"] is False
    assert receipt["failure_code"] == "semantic_action_before_quota"


def test_prose_intent_without_typed_action_is_rejected(tmp_path: Path) -> None:
    transport = ScriptedDoubaoExecTransport(
        [ScriptedAssistantAction(content="I would explore a new surface next.")]
    )
    receipt = DoubaoReplanSemanticActionBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="replan-prose-only",
        fixture_root=tmp_path,
    )

    assert receipt["qualification_passed"] is False
    assert receipt["failure_code"] == "model_returned_without_semantic_action"
    assert receipt["tool_call_count"] == 0
