from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from loopx.control_plane.testing.model_tool_behavior import (
    QUOTA_FIRST_TOOL_INSTRUCTION,
    scripted_exec_tool_response as _tool_response,
)
from loopx.control_plane.testing.selected_todo_tool_behavior import (
    SELECTED_TODO_TOOL_FIXTURE_TODO_ID,
    STALE_DONE_PRIMARY_ACTION_TEXT,
    STALE_DONE_PRIMARY_TODO_ID,
    DoubaoSelectedTodoToolBehaviorActor,
    _build_fixture,
    _classify_tool_command,
    _execute_selected_read,
    bounded_workspace_read_plan,
)


def test_real_tool_loop_executes_action_selected_by_real_quota(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path / "oracle")
    requests: list[dict[str, Any]] = []
    commands = [
        "date -Iseconds",
        fixture.quota_guard_command.replace('"${LOOPX_TURN:?}"', "turn-001"),
        'ls -la fixture/ && echo "---" && cat fixture/selected-lane.json',
    ]

    def transport(**kwargs: Any) -> Mapping[str, Any]:
        requests.append(json.loads(kwargs["body"]))
        return _tool_response(
            f"call-{len(requests)}",
            commands[len(requests) - 1],
        )

    receipt = DoubaoSelectedTodoToolBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="selected-todo-tool-loop-001",
        fixture_root=tmp_path / "actor",
    )

    assert receipt["qualification_passed"] is True
    assert receipt["failure_code"] is None
    assert receipt["observed_tool_sequence"] == [
        "clock",
        "quota_should_run",
        "selected_action",
    ]
    assert receipt["decision"] == "execute"
    assert receipt["selected_todo_id"] == "todo_portfolio001"
    assert receipt["must_attempt_work"] is True
    assert receipt["quiet_noop_allowed"] is False
    assert receipt["selected_action_matched_todo"] is True
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
    assert "selected-lane.json" not in json.dumps(receipt, sort_keys=True)

    first = requests[0]
    assert first["messages"] == [
        {
            "role": "system",
            "content": (
                "You are Codex running one LoopX heartbeat. Follow the heartbeat "
                "task and use the available shell tool when needed. "
                f"{QUOTA_FIRST_TOOL_INSTRUCTION}"
                "The shell working directory is the connected goal project "
                "root; resolve "
                "relative paths from the selected Todo there. Choose each next "
                "action from the latest tool result. If quota sets "
                "selection_required, choose any currently eligible Todo; "
                "selection_command is non-binding. Replace {todo_id}, then run "
                "exactly `<route_prefix> <command_args_template>` for the second "
                "quota guard before delivery. Only when the visible packet lacks "
                "the Todo identity you need, run exactly `<route_prefix> "
                "<candidate_discovery_args>`. Otherwise "
                "execute the exact action in selected_todo.text directly. When it names one file, "
                "read only that file; do not discover, compare, or inspect other "
                "targets. After that selected Todo tool succeeds, call no more "
                "tools."
            ),
        },
        {"role": "user", "content": fixture.task_body},
    ]
    assert first["tools"][0]["function"]["name"] == "exec_command"
    assert first["tool_choice"] == "auto"
    assert "response_format" not in first
    assert "todo_portfolio001" not in first["messages"][1]["content"]
    assert "use selection_command when required" in first["messages"][1]["content"]

    quota_result = json.loads(requests[2]["messages"][-1]["content"])
    assert quota_result["selected_todo"]["todo_id"] == "todo_portfolio001"
    assert "fixture/selected-lane.json" in quota_result["selected_todo"]["text"]


def test_model_resumes_in_flight_todo_on_the_next_heartbeat(tmp_path: Path) -> None:
    fixture = _build_fixture(
        tmp_path / "oracle",
        prior_in_flight_progress=True,
    )
    commands = [
        fixture.quota_guard_command.replace('"${LOOPX_TURN:?}"', "turn-002"),
        (
            fixture.quota_guard_command.replace(
                '"${LOOPX_TURN:?}"', "turn-002"
            )
            + " --todo-id todo_portfolio001"
        ),
        "cat fixture/selected-lane.json",
    ]
    requests: list[dict[str, Any]] = []

    def transport(**kwargs: Any) -> Mapping[str, Any]:
        requests.append(json.loads(kwargs["body"]))
        return _tool_response(
            f"call-{len(requests)}",
            commands[len(requests) - 1],
        )

    receipt = DoubaoSelectedTodoToolBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="selected-todo-in-flight-continuation-002",
        fixture_root=tmp_path / "actor",
        prior_in_flight_progress=True,
    )

    assert receipt["qualification_passed"] is True, receipt
    assert receipt["observed_tool_sequence"] == [
        "quota_should_run",
        "quota_action_selection",
        "selected_action",
    ]
    first_quota_result = json.loads(requests[1]["messages"][-1]["content"])
    assert first_quota_result["interaction_contract"]["cli_channel"][
        "selection_required"
    ] is True
    quota_result = json.loads(requests[2]["messages"][-1]["content"])
    assert quota_result["selected_todo"]["todo_id"] == "todo_portfolio001"
    assert quota_result["selected_todo"]["selection_binding"] == (
        "heartbeat_receipt"
    )
    assert "action_portfolio" not in quota_result
    assert quota_result["interaction_contract"]["agent_channel"].get(
        "selection_required"
    ) is None
    assert quota_result["interaction_contract"]["agent_channel"][
        "delivery_allowed"
    ] is True
    assert quota_result["heartbeat_receipt"]["status"] == "upgraded"
    assert quota_result["interaction_contract"]["cli_channel"][
        "settlement_plan"
    ]["identity"]["todo_id"] == "todo_portfolio001"


def test_model_can_bind_eligible_todo_outside_non_exhaustive_suggestions(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(
        tmp_path / "oracle",
        unsuggested_selected_todo=True,
    )
    requests: list[dict[str, Any]] = []

    def transport(**kwargs: Any) -> Mapping[str, Any]:
        requests.append(json.loads(kwargs["body"]))
        call_index = len(requests)
        if call_index == 1:
            command = fixture.quota_guard_command.replace(
                '"${LOOPX_TURN:?}"', "turn-004"
            )
        elif call_index == 2:
            first_quota = json.loads(requests[1]["messages"][-1]["content"])
            selection = first_quota["interaction_contract"]["cli_channel"][
                "selection_command"
            ]
            command = " ".join(
                (selection["route_prefix"], selection["candidate_discovery_args"])
            )
        elif call_index == 3:
            command = (
                fixture.quota_guard_command.replace(
                    '"${LOOPX_TURN:?}"', "turn-004"
                )
                + " --todo-id todo_portfolio001"
            )
        else:
            command = "cat fixture/selected-lane.json"
        return _tool_response(
            f"call-{call_index}",
            command,
        )

    receipt = DoubaoSelectedTodoToolBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="selected-todo-unsuggested-004",
        fixture_root=tmp_path / "actor",
        unsuggested_selected_todo=True,
    )

    assert receipt["qualification_passed"] is True, receipt
    assert receipt["observed_tool_sequence"] == [
        "quota_should_run",
        "candidate_discovery",
        "quota_action_selection",
        "selected_action",
    ]
    first_quota = json.loads(requests[1]["messages"][-1]["content"])
    suggested_ids = {
        item["todo_id"]
        for item in first_quota["action_portfolio"]["suggested_actions"]
    }
    assert "todo_portfolio001" not in suggested_ids
    assert first_quota["action_portfolio"]["selection_policy"] == {
        "decision_owner": "agent",
        "mode": "explicit_turn_binding",
        "recommendation_role": "default_not_binding",
        "requires_explicit_turn_binding": True,
        "direct_delivery_before_selection": False,
        "max_alternative_actions": 2,
        "candidate_scope": "current_authoritative_eligible_todos",
        "suggestions_exhaustive": False,
    }
    discovery = json.loads(requests[2]["messages"][-1]["content"])
    assert "todo_portfolio001" in json.dumps(discovery, sort_keys=True)
    selected_quota = json.loads(requests[3]["messages"][-1]["content"])
    assert selected_quota["selected_todo"]["todo_id"] == "todo_portfolio001"
    assert selected_quota["selected_todo"]["selection_binding"] == (
        "heartbeat_receipt"
    )


def test_stale_done_primary_exposes_successor_without_preselecting_it_for_model(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(
        tmp_path / "oracle",
        stale_done_primary_successor=True,
    )
    requests: list[dict[str, Any]] = []
    commands = [
        fixture.quota_guard_command.replace('"${LOOPX_TURN:?}"', "turn-stale-005"),
        (
            fixture.quota_guard_command.replace(
                '"${LOOPX_TURN:?}"', "turn-stale-005"
            )
            + f" --todo-id {SELECTED_TODO_TOOL_FIXTURE_TODO_ID}"
        ),
        "cat fixture/selected-lane.json",
    ]

    def transport(**kwargs: Any) -> Mapping[str, Any]:
        requests.append(json.loads(kwargs["body"]))
        return _tool_response(
            f"call-{len(requests)}",
            commands[len(requests) - 1],
        )

    receipt = DoubaoSelectedTodoToolBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="stale-primary-visible-successor-005",
        fixture_root=tmp_path / "actor",
        stale_done_primary_successor=True,
    )

    assert receipt["qualification_passed"] is True, receipt
    assert receipt["selected_todo_id"] == SELECTED_TODO_TOOL_FIXTURE_TODO_ID
    initial_prompt = json.dumps(requests[0]["messages"], ensure_ascii=False)
    assert SELECTED_TODO_TOOL_FIXTURE_TODO_ID not in initial_prompt
    first_quota = json.loads(requests[1]["messages"][-1]["content"])
    assert first_quota["action_portfolio"]["primary"]["todo_id"] == (
        STALE_DONE_PRIMARY_TODO_ID
    )
    assert [
        item["todo_id"]
        for item in first_quota["action_portfolio"]["suggested_actions"]
    ] == [STALE_DONE_PRIMARY_TODO_ID, SELECTED_TODO_TOOL_FIXTURE_TODO_ID]
    model_packet = json.dumps(first_quota, ensure_ascii=False, sort_keys=True)
    assert STALE_DONE_PRIMARY_ACTION_TEXT in model_packet
    assert "fixture/selected-lane.json" in model_packet


def test_tool_loop_rejects_delivery_before_required_action_selection(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(
        tmp_path / "oracle",
        prior_in_flight_progress=True,
    )
    commands = [
        fixture.quota_guard_command.replace('"${LOOPX_TURN:?}"', "turn-003"),
        "cat fixture/selected-lane.json",
    ]
    call_count = 0

    def transport(**_: Any) -> Mapping[str, Any]:
        nonlocal call_count
        command = commands[call_count]
        call_count += 1
        return _tool_response(f"call-{call_count}", command)

    receipt = DoubaoSelectedTodoToolBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="selected-todo-selection-required-003",
        fixture_root=tmp_path / "actor",
        prior_in_flight_progress=True,
    )

    assert receipt["qualification_passed"] is False
    assert receipt["failure_code"] == "delivery_before_action_selection"
    assert receipt["observed_tool_sequence"] == [
        "quota_should_run",
        "selected_action",
    ]


def test_tool_loop_rejects_selected_action_before_quota(tmp_path: Path) -> None:
    def transport(**_: Any) -> Mapping[str, Any]:
        return _tool_response("call-1", "cat fixture/selected-lane.json")

    receipt = DoubaoSelectedTodoToolBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="selected-todo-before-quota",
        fixture_root=tmp_path,
    )

    assert receipt["qualification_passed"] is False
    assert receipt["failure_code"] == "selected_action_before_quota"
    assert receipt["observed_tool_sequence"] == ["selected_action_before_quota"]


def test_tool_loop_allows_bounded_file_discovery_before_selected_action(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path / "oracle")
    commands = [
        fixture.quota_guard_command.replace('"${LOOPX_TURN:?}"', "turn-001"),
        "find fixture -maxdepth 2 -type f -print",
        "cat fixture/selected-lane.json",
    ]
    call_count = 0

    def transport(**_: Any) -> Mapping[str, Any]:
        nonlocal call_count
        command = commands[call_count]
        call_count += 1
        return _tool_response(f"call-{call_count}", command)

    receipt = DoubaoSelectedTodoToolBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="selected-todo-bounded-discovery",
        fixture_root=tmp_path / "actor",
    )

    assert receipt["qualification_passed"] is True
    assert receipt["observed_tool_sequence"] == [
        "quota_should_run",
        "workspace_read",
        "selected_action",
    ]


def test_tool_loop_allows_bounded_discovery_with_safe_path_exclusion(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path / "oracle")
    commands = [
        fixture.quota_guard_command.replace('"${LOOPX_TURN:?}"', "turn-001"),
        (
            'ls -la && find . -name "selected-lane.json" '
            '-not -path "*/node_modules/*" 2>/dev/null'
        ),
        "cat fixture/selected-lane.json",
    ]
    call_count = 0

    def transport(**_: Any) -> Mapping[str, Any]:
        nonlocal call_count
        command = commands[call_count]
        call_count += 1
        return _tool_response(f"call-{call_count}", command)

    receipt = DoubaoSelectedTodoToolBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="selected-todo-safe-path-exclusion",
        fixture_root=tmp_path / "actor",
    )

    assert receipt["qualification_passed"] is True
    assert receipt["observed_tool_sequence"] == [
        "quota_should_run",
        "workspace_read",
        "selected_action",
    ]


def test_tool_loop_allows_bounded_discovery_with_positive_path_filter(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path / "oracle")
    commands = [
        fixture.quota_guard_command.replace('"${LOOPX_TURN:?}"', "turn-001"),
        (
            'ls -la && find . -name "selected-lane.json" '
            '-path "*/fixture/*" 2>/dev/null'
        ),
        "cat fixture/selected-lane.json",
    ]
    call_count = 0

    def transport(**_: Any) -> Mapping[str, Any]:
        nonlocal call_count
        command = commands[call_count]
        call_count += 1
        return _tool_response(f"call-{call_count}", command)

    receipt = DoubaoSelectedTodoToolBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="selected-todo-positive-path-filter",
        fixture_root=tmp_path / "actor",
    )

    assert receipt["qualification_passed"] is True
    assert receipt["observed_tool_sequence"] == [
        "quota_should_run",
        "workspace_read",
        "selected_action",
    ]


def test_tool_loop_rejects_absolute_positive_path_filter(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path / "oracle")
    commands = [
        fixture.quota_guard_command.replace('"${LOOPX_TURN:?}"', "turn-001"),
        'find . -path "/etc/*"',
    ]
    call_count = 0

    def transport(**_: Any) -> Mapping[str, Any]:
        nonlocal call_count
        command = commands[call_count]
        call_count += 1
        return _tool_response(f"call-{call_count}", command)

    receipt = DoubaoSelectedTodoToolBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="selected-todo-reject-absolute-path-filter",
        fixture_root=tmp_path / "actor",
    )

    assert receipt["qualification_passed"] is False
    assert receipt["failure_code"] == "unexpected_command"


def test_tool_loop_allows_bounded_discovery_pipeline_before_action(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path / "oracle")
    commands = [
        fixture.quota_guard_command.replace('"${LOOPX_TURN:?}"', "turn-001"),
        (
            'find . -name "selected-lane.json" -not -path "*/.git/*" '
            '2>/dev/null | head -20'
        ),
        "cat fixture/selected-lane.json",
    ]
    call_count = 0

    def transport(**_: Any) -> Mapping[str, Any]:
        nonlocal call_count
        command = commands[call_count]
        call_count += 1
        return _tool_response(f"call-{call_count}", command)

    receipt = DoubaoSelectedTodoToolBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="selected-todo-bounded-discovery-pipeline",
        fixture_root=tmp_path / "actor",
    )

    assert receipt["qualification_passed"] is True
    assert receipt["observed_tool_sequence"] == [
        "quota_should_run",
        "workspace_read",
        "selected_action",
    ]


def test_tool_loop_allows_bounded_selected_content_pipeline(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path / "oracle")
    commands = [
        fixture.quota_guard_command.replace('"${LOOPX_TURN:?}"', "turn-001"),
        "cat fixture/selected-lane.json 2>/dev/null | head -200",
    ]
    call_count = 0

    def transport(**_: Any) -> Mapping[str, Any]:
        nonlocal call_count
        command = commands[call_count]
        call_count += 1
        return _tool_response(f"call-{call_count}", command)

    receipt = DoubaoSelectedTodoToolBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="selected-todo-bounded-content-pipeline",
        fixture_root=tmp_path / "actor",
    )

    assert receipt["qualification_passed"] is True
    assert receipt["observed_tool_sequence"] == [
        "quota_should_run",
        "selected_action",
    ]


def test_tool_loop_allows_bounded_state_and_fallback_discovery(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path / "oracle")
    commands = [
        fixture.quota_guard_command.replace('"${LOOPX_TURN:?}"', "turn-001"),
        (
            'ls -la ~/.codex/loopx/ 2>/dev/null; echo "---"; '
            'find ~/.codex/loopx -maxdepth 3 -name "fixture" -type d '
            '2>/dev/null; echo "---"; find ~/.codex/loopx '
            '-name "selected-lane.json" 2>/dev/null'
        ),
        (
            'ls -la && echo "---" && ls -la fixture/ 2>/dev/null '
            '|| echo "no fixture dir in cwd" && echo "---" && pwd'
        ),
        "cat fixture/selected-lane.json",
    ]
    call_count = 0

    def transport(**_: Any) -> Mapping[str, Any]:
        nonlocal call_count
        command = commands[call_count]
        call_count += 1
        return _tool_response(f"call-{call_count}", command)

    receipt = DoubaoSelectedTodoToolBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="selected-todo-natural-discovery",
        fixture_root=tmp_path / "actor",
    )

    assert receipt["qualification_passed"] is True
    assert receipt["observed_tool_sequence"] == [
        "quota_should_run",
        "workspace_read",
        "workspace_read",
        "selected_action",
    ]


@pytest.mark.parametrize("cat_options", ["", "-- "])
@pytest.mark.parametrize("batch_read", [False, True])
def test_tool_loop_treats_fixture_state_read_as_metadata(
    tmp_path: Path,
    cat_options: str,
    batch_read: bool,
) -> None:
    fixture = _build_fixture(tmp_path / "oracle")
    state_path = ".codex/goals/portfolio-goal/ACTIVE_GOAL_STATE.md"
    commands = [
        fixture.quota_guard_command.replace('"${LOOPX_TURN:?}"', "turn-001"),
        f"cat {cat_options}{state_path}" if cat_options else f"sed -n 1,120p {state_path}",
        (
            f"cat {cat_options}{state_path} fixture/selected-lane.json"
            if batch_read
            else f"cat {cat_options}fixture/selected-lane.json && head -n 40 {state_path}"
        ),
    ]
    call_count = 0

    def transport(**_: Any) -> Mapping[str, Any]:
        nonlocal call_count
        command = commands[call_count]
        call_count += 1
        return _tool_response(f"call-{call_count}", command)

    receipt = DoubaoSelectedTodoToolBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="selected-todo-fixture-state-metadata",
        fixture_root=tmp_path / "actor",
    )

    assert receipt["qualification_passed"] is True
    assert receipt["observed_tool_sequence"] == [
        "quota_should_run",
        "workspace_read",
        "selected_action",
    ]


@pytest.mark.parametrize("command", [
    "cat --",
    "cat -- /etc/passwd",
    "cat -- fixture/selected-lane.json /etc/passwd",
    "cat -- ../outside.json",
    "cat -- fixture/selected-lane.json ../outside.json",
    "cat -- fixture/selected-lane.json > output.json",
    "cat -n -- fixture/selected-lane.json",
    "cat -- " + " ".join(["fixture/selected-lane.json"] * 9),
    "false || cat -- fixture/selected-lane.json .loopx/registry.json",
])
def test_end_of_options_does_not_expand_bounded_read_authority(
    tmp_path: Path, command: str,
) -> None:
    fixture = _build_fixture(tmp_path)
    assert bounded_workspace_read_plan(command, fixture=fixture) is None


def test_batch_read_does_not_grant_selected_sibling_authority(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path)
    command = "cat -- fixture/selected-lane.json fixture/deferred-lane.json"
    assert _classify_tool_command(command, fixture=fixture, quota_observed=True) == (
        "wrong_selected_todo_target", None,
    )


def test_batch_read_failure_cannot_be_masked_by_selected_file(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path)
    (fixture.project_root / ".loopx" / "registry.json").unlink()
    with pytest.raises(RuntimeError, match="bounded read plan failed"):
        _execute_selected_read(
            "cat -- .loopx/registry.json fixture/selected-lane.json", fixture=fixture,
        )


def test_tool_loop_allows_bounded_hermetic_registry_preview(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path / "oracle")
    commands = [
        fixture.quota_guard_command.replace('"${LOOPX_TURN:?}"', "turn-001"),
        (
            'ls -la ~/.codex/loopx/ 2>/dev/null; echo "---"; '
            'ls -la ~/.codex/loopx/projects/ 2>/dev/null; echo "---"; '
            "cat ~/.codex/loopx/registry.global.json 2>/dev/null | head -200"
        ),
        "cat fixture/selected-lane.json",
    ]
    call_count = 0

    def transport(**_: Any) -> Mapping[str, Any]:
        nonlocal call_count
        command = commands[call_count]
        call_count += 1
        return _tool_response(f"call-{call_count}", command)

    receipt = DoubaoSelectedTodoToolBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="selected-todo-runtime-registry-preview",
        fixture_root=tmp_path / "actor",
    )

    assert receipt["qualification_passed"] is True
    assert receipt["observed_tool_sequence"] == [
        "quota_should_run",
        "workspace_read",
        "selected_action",
    ]


def test_tool_loop_rejects_unbounded_host_metadata_read(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path / "oracle")
    commands = [
        fixture.quota_guard_command.replace('"${LOOPX_TURN:?}"', "turn-001"),
        "ls -la /etc",
    ]
    call_count = 0

    def transport(**_: Any) -> Mapping[str, Any]:
        nonlocal call_count
        command = commands[call_count]
        call_count += 1
        return _tool_response(f"call-{call_count}", command)

    receipt = DoubaoSelectedTodoToolBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="selected-todo-reject-host-read",
        fixture_root=tmp_path / "actor",
    )

    assert receipt["qualification_passed"] is False
    assert receipt["failure_code"] == "unexpected_command"


def test_tool_loop_rejects_action_for_deferred_decoy(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path / "oracle")
    commands = [
        fixture.quota_guard_command.replace('"${LOOPX_TURN:?}"', "turn-001"),
        "cat fixture/deferred-lane.json",
    ]
    call_count = 0

    def transport(**_: Any) -> Mapping[str, Any]:
        nonlocal call_count
        command = commands[call_count]
        call_count += 1
        return _tool_response(f"call-{call_count}", command)

    receipt = DoubaoSelectedTodoToolBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="selected-todo-wrong-target",
        fixture_root=tmp_path / "actor",
    )

    assert receipt["qualification_passed"] is False
    assert receipt["failure_code"] == "wrong_selected_todo_target"
    assert receipt["selected_action_matched_todo"] is False


def test_tool_loop_never_executes_a_model_supplied_program_path(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path / "oracle")
    commands = [
        fixture.quota_guard_command.replace('"${LOOPX_TURN:?}"', "turn-001"),
        "/untrusted/bin/cat fixture/selected-lane.json",
    ]
    call_count = 0

    def transport(**_: Any) -> Mapping[str, Any]:
        nonlocal call_count
        command = commands[call_count]
        call_count += 1
        return _tool_response(f"call-{call_count}", command)

    receipt = DoubaoSelectedTodoToolBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="selected-todo-normalized-executable",
        fixture_root=tmp_path / "actor",
    )

    assert receipt["qualification_passed"] is True
    assert receipt["observed_tool_sequence"] == [
        "quota_should_run",
        "selected_action",
    ]


def test_tool_loop_fails_when_model_only_describes_the_action(
    tmp_path: Path,
) -> None:
    def transport(**_: Any) -> Mapping[str, Any]:
        return {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": "I would inspect the selected lane next.",
                    },
                }
            ]
        }

    receipt = DoubaoSelectedTodoToolBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="selected-todo-prose-only",
        fixture_root=tmp_path,
    )

    assert receipt["qualification_passed"] is False
    assert receipt["failure_code"] == "model_returned_without_tool_call"
    assert receipt["tool_call_count"] == 0
