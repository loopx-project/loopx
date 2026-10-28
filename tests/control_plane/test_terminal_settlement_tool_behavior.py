from __future__ import annotations

import json
import shlex
import subprocess
from collections.abc import Mapping
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from loopx.control_plane.testing.model_tool_behavior import (
    QUOTA_FIRST_TOOL_INSTRUCTION,
    ScriptedAssistantAction,
    ScriptedDoubaoExecTransport,
    ScriptedExecToolAction,
)
from loopx.control_plane.testing.terminal_settlement_tool_behavior import (
    TERMINAL_SETTLEMENT_FIXTURE_TODO_ID,
    DoubaoTerminalSettlementToolBehaviorActor,
    _build_fixture,
)
from loopx.control_plane.testing.vision_shell_host import shell_isolation_available
from loopx.control_plane.todos.markdown import render_todo_markdown


def _latest_quota_packet(request: Mapping[str, Any]) -> dict[str, Any]:
    for message in reversed(request["messages"]):
        if message.get("role") != "tool":
            continue
        try:
            payload = json.loads(message["content"])
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(payload, dict) and "interaction_contract" in payload:
            return payload
    raise AssertionError("scripted model did not receive the real quota packet")


def _writeback_action(request: Mapping[str, Any]) -> ScriptedExecToolAction:
    packet = _latest_quota_packet(request)
    command = packet["interaction_contract"]["cli_channel"]["next_cli_actions"][0]
    for message in reversed(request["messages"]):
        if message["role"] != "tool":
            continue
        try:
            result = json.loads(message["content"])
        except json.JSONDecodeError:
            continue
        if isinstance(result, dict) and result.get("settlement_plan") and result.get("next_cli_actions"):
            command = result["next_cli_actions"][0]
            break
    command = (
        command.replace("<validated_progress>", "terminal_settlement_validated")
        .replace("<scale>", "single_surface")
        .replace("<outcome>", "outcome_progress")
        .replace("<boundary>", "semantic_closeout")
        .replace('"${LOOPX_TURN:?}"', "turn-test-001")
    )
    return ScriptedExecToolAction(command=command)


def _spend_action(request: Mapping[str, Any]) -> ScriptedExecToolAction:
    packet = _latest_quota_packet(request)
    command = packet["interaction_contract"]["cli_channel"]["next_cli_actions"][1]
    return ScriptedExecToolAction(
        command=command.replace('"${LOOPX_TURN:?}"', "turn-test-001")
    )


def _completion_action(
    request: Mapping[str, Any], *, kind: str = "terminal_closeout",
) -> ScriptedExecToolAction:
    packet = _latest_quota_packet(request)
    steps = packet["interaction_contract"]["cli_channel"]["settlement_plan"][
        "ordered_steps"
    ]
    command = next(
        item["command_template"]
        for item in steps
        if item["kind"] == kind
    )
    return ScriptedExecToolAction(
        command=command.replace('"${LOOPX_TURN:?}"', "turn-test-001").replace(
            "'<validated evidence>'",
            "'fixture-settlement-proof'",
        )
    )


def _latest_reentry_transition(request: Mapping[str, Any]) -> dict[str, Any]:
    for message in request["messages"]:
        if message.get("role") != "tool":
            continue
        try:
            payload = json.loads(message["content"])
        except (json.JSONDecodeError, TypeError):
            continue
        transition = (
            payload.get("replan_transition") if isinstance(payload, dict) else None
        )
        if isinstance(transition, dict):
            return transition
    raise AssertionError("scripted model did not receive the real rejection packet")


def _reentry_todo_action(index: int):
    def action(request: Mapping[str, Any]) -> ScriptedExecToolAction:
        transition = _latest_reentry_transition(request)
        commands = [
            item
            for item in transition["next_cli_actions"]
            if "loopx todo complete" in item
        ]
        command = commands[index]
        command = command[command.index("loopx ") :]
        return ScriptedExecToolAction(command=command)

    return action


def _reentry_quota_action(request: Mapping[str, Any]) -> ScriptedExecToolAction:
    transition = _latest_reentry_transition(request)
    command = next(
        item for item in transition["next_cli_actions"] if "quota should-run" in item
    )
    return ScriptedExecToolAction(command=command)


@pytest.mark.parametrize("cat_options", ["", "-- "])
@pytest.mark.parametrize("batch_read", [False, True])
@pytest.mark.parametrize("ordinary_completion", [False, True])
def test_real_tool_loop_settles_final_todo_after_spend(
    tmp_path: Path, cat_options: str, batch_read: bool, ordinary_completion: bool,
) -> None:
    fixture = _build_fixture(tmp_path / "oracle")
    transport = ScriptedDoubaoExecTransport(
        [
            ScriptedExecToolAction(
                command=fixture.quota_guard_command.replace(
                    '"${LOOPX_TURN:?}"',
                    "turn-test-001",
                )
            ),
            ScriptedExecToolAction(command=(
                f"cat {cat_options}.codex/goals/terminal-settlement-fixture/ACTIVE_GOAL_STATE.md "
                "fixture/settlement-proof.json"
                if batch_read else f"cat {cat_options}fixture/settlement-proof.json"
            )),
            *([partial(_completion_action, kind="validation")] if ordinary_completion else []),
            _writeback_action,
            _spend_action,
            _completion_action,
        ]
    )

    receipt = DoubaoTerminalSettlementToolBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="terminal-settlement-tool-loop-001",
        fixture_root=tmp_path / "actor",
    )

    assert receipt["failure_code"] is None, str(receipt["failure_code"])
    assert receipt["qualification_passed"] is True, json.dumps(receipt, sort_keys=True)
    assert receipt["observed_tool_sequence"] == [
        "quota_should_run",
        "delivery_validation",
        *(["todo_completion"] if ordinary_completion else []),
        "durable_writeback",
        "quota_spend",
        "terminal_closeout",
    ]
    assert receipt["settlement_receipt_steps"] == [
        "validation",
        "durable_writeback",
        "quota_spend",
        "terminal_closeout",
    ]
    assert receipt["terminal_order_observed"] is True
    assert receipt["selected_todo_id"] == TERMINAL_SETTLEMENT_FIXTURE_TODO_ID
    assert receipt["decision"] == "execute"
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
    assert "settlement-proof.json" not in json.dumps(receipt, sort_keys=True)

    first = transport.requests[0]
    system_prompt = first["messages"][0]["content"]
    assert QUOTA_FIRST_TOOL_INSTRUCTION in system_prompt
    assert "the host validates that tool result" in system_prompt
    assert "validation step's command_condition" in system_prompt
    assert "distinct from the final --no-follow-up closeout after spend" in system_prompt
    assert "A receipt without command templates does not replace that plan" in system_prompt
    assert first["messages"][1] == {"role": "user", "content": fixture.task_body}
    assert TERMINAL_SETTLEMENT_FIXTURE_TODO_ID not in fixture.task_body
    quota_result = _latest_quota_packet(transport.requests[2])
    assert quota_result["selected_todo"]["todo_id"] == (
        TERMINAL_SETTLEMENT_FIXTURE_TODO_ID
    )
    if ordinary_completion:
        results = [json.loads(message["content"]) for message in transport.requests[-1]["messages"]
                   if message["role"] == "tool" and message["content"].startswith("{")]
        completed = next(result for result in results if result.get("status") == "done")
        assert completed["settlement_progress"]["next_step"] == "durable_writeback"
        plan = completed["settlement_plan"]
        assert plan["identity"] == completed["settlement_identity"]
        writeback = next(step for step in plan["ordered_steps"] if step["kind"] == "durable_writeback")
        assert writeback["vision_authoring"]["schema_version"] == "goal_vision_replan_contract_v0"
        assert completed["next_cli_actions"] == [writeback["command_template"]]
        assert completed["settlement_result"]["receipts"][0]["step_kind"] == "validation"
        markdown = render_todo_markdown(completed)
        assert "writeback_required" in markdown
        assert completed["next_cli_actions"][0] in markdown


@pytest.mark.parametrize(
    ("read_proof", "drift_option", "expected_failure"),
    [(False, None, "completion_before_validation"),
     (True, "--agent-id", "completion_identity_mismatch"),
     (True, "--turn-instance-id", "completion_identity_mismatch")],
)
def test_ordinary_completion_cannot_bypass_validation_or_identity(
    tmp_path: Path, read_proof: bool, drift_option: str | None, expected_failure: str,
) -> None:
    fixture = _build_fixture(tmp_path / "oracle")

    def completion(request: Mapping[str, Any]) -> ScriptedExecToolAction:
        tokens = shlex.split(_completion_action(request, kind="validation").command)
        if drift_option:
            tokens[tokens.index(drift_option) + 1] = "foreign-identity"
        return ScriptedExecToolAction(command=shlex.join(tokens))

    transport = ScriptedDoubaoExecTransport([
        ScriptedExecToolAction(command=fixture.quota_guard_command.replace(
            '"${LOOPX_TURN:?}"', "turn-test-001",
        )),
        *([ScriptedExecToolAction(command="cat fixture/settlement-proof.json")] if read_proof else []),
        completion,
    ])
    receipt = DoubaoTerminalSettlementToolBehaviorActor(
        api_key="test-only-placeholder", transport=transport,
    ).qualify(qualification_id="ordinary-completion-refusal", fixture_root=tmp_path / "actor")
    assert receipt["qualification_passed"] is False
    assert receipt["failure_code"] == expected_failure
    assert receipt["settlement_receipt_steps"] == []
    assert receipt["terminal_order_observed"] is False


def test_ordinary_completion_does_not_authorize_an_unscoped_writeback(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path / "oracle")
    transport = ScriptedDoubaoExecTransport([
        ScriptedExecToolAction(command=fixture.quota_guard_command.replace(
            '"${LOOPX_TURN:?}"', "turn-test-001",
        )),
        ScriptedExecToolAction(command="cat fixture/settlement-proof.json"),
        partial(_completion_action, kind="validation"),
        ScriptedExecToolAction(command="loopx refresh-state"),
    ])
    receipt = DoubaoTerminalSettlementToolBehaviorActor(
        api_key="test-only-placeholder", transport=transport,
    ).qualify(qualification_id="ordinary-completion-unscoped-writeback", fixture_root=tmp_path / "actor")
    assert receipt["qualification_passed"] is False
    assert receipt["failure_code"] == "writeback_identity_mismatch"
    assert receipt["settlement_receipt_steps"] == ["validation"]
    assert receipt["terminal_order_observed"] is False


def test_terminal_fixture_requires_quota_before_reading_proof(tmp_path: Path) -> None:
    receipt = DoubaoTerminalSettlementToolBehaviorActor(
        api_key="test-only-placeholder",
        transport=ScriptedDoubaoExecTransport([
            ScriptedExecToolAction(command="cat fixture/settlement-proof.json"),
        ]),
    ).qualify(qualification_id="read-before-quota", fixture_root=tmp_path / "actor")
    assert receipt["failure_code"] == "workspace_read_before_quota"
    assert receipt["qualification_passed"] is False
    assert receipt["settlement_receipt_steps"] == []


@pytest.mark.parametrize("option", ["--registry", "--runtime-root", "--project", "--state-file"])
@pytest.mark.parametrize("spelling", ["separate", "inline", "duplicate"])
def test_terminal_fixture_rejects_foreign_routes(tmp_path: Path, option: str, spelling: str) -> None:
    fixture = _build_fixture(tmp_path / "oracle")
    outside = tmp_path / "owner-state.json"
    outside.write_text("Owner state must remain unchanged.\n")

    def completion(request):
        tokens = shlex.split(_completion_action(request, kind="validation").command)
        if spelling == "duplicate":
            if option not in tokens:
                tokens += [option, str(tmp_path / "actor/project")]
            tokens += [option, str(outside)]
        elif spelling == "inline":
            if option in tokens:
                index = tokens.index(option)
                del tokens[index:index + 2]
            tokens += [option + "=" + str(outside)]
        elif option in tokens:
            tokens[tokens.index(option) + 1] = str(outside)
        else:
            tokens += [option, str(outside)]
        return ScriptedExecToolAction(command=shlex.join(tokens))

    receipt = DoubaoTerminalSettlementToolBehaviorActor(
        api_key="test-only-placeholder", transport=ScriptedDoubaoExecTransport([
            ScriptedExecToolAction(command=fixture.quota_guard_command),
            ScriptedExecToolAction(command="cat fixture/settlement-proof.json"), completion,
        ]),
    ).qualify(qualification_id="foreign-route", fixture_root=tmp_path / "actor")
    assert receipt["failure_code"] == {"separate": "fixture_route_mismatch",
                                       "inline": "nonliteral_fixture_argument",
                                       "duplicate": "duplicate_fixture_argument"}[spelling]
    assert receipt["qualification_passed"] is False
    assert receipt["settlement_receipt_steps"] == []
    assert outside.read_text() == "Owner state must remain unchanged.\n"


def test_terminal_failure_receipt_does_not_echo_cli_or_model_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from loopx.control_plane.testing import terminal_settlement_tool_behavior as terminal

    fixture = _build_fixture(tmp_path / "oracle")
    execute = terminal._execute_cli

    def failed_writeback(command, state):
        if "refresh-state" in command:
            raise RuntimeError("private-model-payload-and-private-file-name")
        return execute(command, state)

    monkeypatch.setattr(terminal, "_execute_cli", failed_writeback)
    receipt = DoubaoTerminalSettlementToolBehaviorActor(
        api_key="test-only-placeholder", transport=ScriptedDoubaoExecTransport([
            ScriptedExecToolAction(command=fixture.quota_guard_command),
            ScriptedExecToolAction(command="cat fixture/settlement-proof.json"), _writeback_action,
        ]),
    ).qualify(qualification_id="redacted-cli-failure", fixture_root=tmp_path / "actor")
    assert receipt["qualification_passed"] is False
    assert receipt["failure_code"] == "tool_execution_failed"
    assert "private-model-payload" not in json.dumps(receipt)


@pytest.mark.skipif(not shell_isolation_available(), reason="Draft authoring requires native OS isolation")
@pytest.mark.parametrize(("draft_target", "coverage_observation", "correct_input"), [
    (".scratch/vision.json", False, False), (".scratch/vision.json", True, False),
    (".scratch/vision.json", True, True),
    ("fixture/settlement-proof.json", False, False), ("outside", False, False),
])
def test_terminal_native_draft_preserves_proof_and_authority(
    tmp_path: Path, draft_target: str, coverage_observation: bool, correct_input: bool,
) -> None:
    fixture = _build_fixture(tmp_path / "oracle")
    outside = tmp_path / "owner-private.json"
    outside.write_text("Owner artifact.\n")
    target = str(outside) if draft_target == "outside" else draft_target
    vision = {
        "schema_version": "goal_vision_replan_contract_v0", "state": "vision_closed",
        "vision_patch": {"acceptance_summary": "Original proof and Todo completion independently validated."},
        "path_delta": {"outcome": "no_change", "prior_assumption": "Final delivery required validation.",
                       "observed_reality": "The proof was read and original Todo validation committed.",
                       "retained": ["Original validated delivery"], "evidence_refs": ["fixture-settlement-proof"]},
    }
    if coverage_observation:
        vision["state"] = "no_followup"
        vision["path_delta"]["outcome"] = "stop"
        vision["path_delta"]["stopped"] = vision["path_delta"].pop("retained")

    def authority_files():
        root = tmp_path / "actor"
        return {str(path.relative_to(root)): path.read_bytes()
                for tree in (root / "runtime", root / "project/.codex", root / "project/.loopx", root / "home")
                for path in tree.rglob("*") if path.is_file()}

    before_rejection = {}

    def writeback(request, *, omit_scope=False):
        if correct_input and not omit_scope:
            rejected = json.loads(request["messages"][-1]["content"])
            assert rejected["ok"] is False and rejected["appended"] is False
            assert rejected["error_code"] == "progress_observation_invalid_arguments"
            assert "--progress-coverage-scope-id" in rejected["error"]
            assert authority_files() == before_rejection
        command = _writeback_action(request).command + " --agent-vision-json .scratch/vision.json"
        if coverage_observation:
            packet = _latest_quota_packet(request)
            step = packet["interaction_contract"]["cli_channel"]["settlement_plan"]["ordered_steps"][1]
            assert "--progress-coverage-scope-id" in step["precondition"]
            assert "--progress-coverage-complete" in step["precondition"]
            command += " --progress-result-class no_followup --progress-evidence-id fixture-settlement-proof"
            if not omit_scope:
                command += " --progress-coverage-scope-id terminal-settlement-fixture"
        if omit_scope:
            before_rejection.update(authority_files())
        return ScriptedExecToolAction(command=command)

    def completion(request):
        tokens = shlex.split(_completion_action(request, kind="validation").command)
        tokens[tokens.index("--turn-instance-id") + 1] = "${LOOPX_TURN:?}"
        return ScriptedExecToolAction(command=shlex.join(tokens))

    receipt = DoubaoTerminalSettlementToolBehaviorActor(
        api_key="test-only-placeholder", transport=ScriptedDoubaoExecTransport([
            ScriptedExecToolAction(command=fixture.quota_guard_command),
            ScriptedExecToolAction(command="cat fixture/settlement-proof.json"),
            completion,
            ScriptedExecToolAction(command="cat > " + shlex.quote(target) + " <<'JSON'\n" +
                                  json.dumps(vision) + "\nJSON"),
            *([partial(writeback, omit_scope=True)] if correct_input else []),
            writeback, _spend_action, _completion_action,
        ]),
    ).qualify(qualification_id="native-terminal-draft", fixture_root=tmp_path / "actor")
    passed = draft_target == ".scratch/vision.json"
    assert receipt["qualification_passed"] is passed, receipt
    assert receipt["boundary"]["shell_commands_executed"] is True
    assert receipt["settlement_receipt_steps"] == (
        ["validation", "durable_writeback", "quota_spend", "terminal_closeout"] if passed else ["validation"]
    )
    assert (tmp_path / "actor/project/fixture/settlement-proof.json").read_bytes() == fixture.proof_target.read_bytes()
    assert outside.read_text() == "Owner artifact.\n"
    if correct_input:
        assert receipt["tool_call_count"] == 8
        assert receipt["observed_tool_sequence"].count("progress_observation_invalid_arguments") == 1


def test_terminal_input_rejections_do_not_reset_the_call_budget(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path / "oracle")

    def rejected_writeback(request):
        return ScriptedExecToolAction(command=_writeback_action(request).command +
                                      " --progress-result-class no_followup")

    transport = ScriptedDoubaoExecTransport([
        ScriptedExecToolAction(command=fixture.quota_guard_command),
        ScriptedExecToolAction(command="cat fixture/settlement-proof.json"),
        partial(_completion_action, kind="validation"),
        *([rejected_writeback] * 7),
    ])
    receipt = DoubaoTerminalSettlementToolBehaviorActor(
        api_key="test-only-placeholder", transport=transport,
    ).qualify(qualification_id="bounded-input-correction", fixture_root=tmp_path / "actor")
    assert receipt["qualification_passed"] is False
    assert receipt["failure_code"] == "tool_call_budget_exhausted"
    assert receipt["tool_call_count"] == 10
    assert receipt["observed_tool_sequence"].count("progress_observation_invalid_arguments") == 7
    assert receipt["settlement_receipt_steps"] == ["validation"]


@pytest.mark.parametrize("payload_ok", [False, True])
def test_nonzero_after_actual_writeback_cannot_advance_to_spend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload_ok: bool,
) -> None:
    from loopx.control_plane.quota.settlement import read_heartbeat_settlement
    fixture = _build_fixture(tmp_path / "oracle")
    execute = subprocess.run
    committed = {}

    def fail_after_writeback(argv, *args, **kwargs):
        completed = execute(argv, *args, **kwargs)
        if isinstance(argv, list) and "loopx.cli" in argv and "refresh-state" in argv:
            assert completed.returncode == 0
            payload = json.loads(completed.stdout)
            assert payload["ok"] is True
            committed.update(payload["settlement_identity"])
            # Inject only a post-effect process failure; the actual CLI above
            # produced the independent writeback and committed receipt.
            payload["ok"] = payload_ok
            return subprocess.CompletedProcess(
                completed.args, 1, json.dumps(payload), "synthetic post-effect failure",
            )
        return completed

    monkeypatch.setattr(subprocess, "run", fail_after_writeback)
    transport = ScriptedDoubaoExecTransport([
        ScriptedExecToolAction(command=fixture.quota_guard_command),
        ScriptedExecToolAction(command="cat fixture/settlement-proof.json"),
        partial(_completion_action, kind="validation"), _writeback_action,
        _spend_action, _completion_action,
    ])
    receipt = DoubaoTerminalSettlementToolBehaviorActor(
        api_key="test-only-placeholder", transport=transport,
    ).qualify(qualification_id="nonzero-after-writeback", fixture_root=tmp_path / "actor")
    assert receipt["qualification_passed"] is False
    assert receipt["failure_code"] == "tool_execution_failed"
    assert receipt["tool_call_count"] == len(transport.requests) == 4
    assert receipt["settlement_receipt_steps"] == ["validation"]
    assert "synthetic post-effect" not in json.dumps(receipt)
    # Independent canonical readback proves that the failure did not roll back
    # the real write; neither a debit nor terminal effect followed it.
    readback = read_heartbeat_settlement(
        tmp_path / "actor/runtime", goal_id=committed["goal_id"],
        agent_id=committed["agent_id"], todo_id=committed["todo_id"],
        turn_instance_id=committed["turn_instance_id"],
    )
    assert readback is not None
    assert readback.progress["next_step"] == "quota_spend"


def test_ordinary_completion_requires_its_actual_validation_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from loopx.control_plane.testing import terminal_settlement_tool_behavior as terminal

    fixture = _build_fixture(tmp_path / "oracle")
    execute = terminal._execute_cli

    def missing_validation_receipt(command, state):
        payload = execute(command, state)
        if "todo complete" in command:
            assert payload["status"] == "done"
            assert payload["settlement_result"]["receipts"][0]["step_kind"] == "validation"
            payload["settlement_result"]["receipts"] = []
        return payload

    monkeypatch.setattr(terminal, "_execute_cli", missing_validation_receipt)
    transport = ScriptedDoubaoExecTransport([
        ScriptedExecToolAction(command=fixture.quota_guard_command.replace(
            '"${LOOPX_TURN:?}"', "turn-test-001",
        )),
        ScriptedExecToolAction(command="cat fixture/settlement-proof.json"),
        partial(_completion_action, kind="validation"),
    ])
    receipt = DoubaoTerminalSettlementToolBehaviorActor(
        api_key="test-only-placeholder", transport=transport,
    ).qualify(qualification_id="ordinary-completion-missing-receipt", fixture_root=tmp_path / "actor")
    assert receipt["qualification_passed"] is False
    assert receipt["failure_code"] == "completion_receipt_mismatch"
    assert receipt["settlement_receipt_steps"] == []
    assert receipt["terminal_order_observed"] is False


@pytest.mark.parametrize("batch_read", [False, True])
def test_tool_loop_rejects_terminal_closeout_before_spend(
    tmp_path: Path, batch_read: bool,
) -> None:
    fixture = _build_fixture(tmp_path / "oracle")
    transport = ScriptedDoubaoExecTransport(
        [
            ScriptedExecToolAction(
                command=fixture.quota_guard_command.replace(
                    '"${LOOPX_TURN:?}"',
                    "turn-test-001",
                )
            ),
            ScriptedExecToolAction(command=(
                "cat -- .codex/goals/terminal-settlement-fixture/ACTIVE_GOAL_STATE.md "
                "fixture/settlement-proof.json"
                if batch_read else "cat fixture/settlement-proof.json"
            )),
            _completion_action,
        ]
    )

    receipt = DoubaoTerminalSettlementToolBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="terminal-closeout-before-spend",
        fixture_root=tmp_path / "actor",
    )

    assert receipt["qualification_passed"] is False
    assert receipt["failure_code"] == "terminal_closeout_before_spend"
    assert receipt["settlement_receipt_steps"] == []
    assert receipt["tool_call_receipts"][-1]["redacted_command_shape"] == {
        "parseable": True,
        "token_count_bucket": 12,
        "executable_family": "control_plane",
        "workspace_operation": "other",
        "git_operation": None,
        "python_module": None,
        "contains_loopx": True,
        "loopx_command_path": "todo",
        "loopx_command_action": "complete",
        "operator_before_loopx": False,
        "operator_after_loopx": False,
        "has_environment_assignment": False,
        "mentions_proof_target": False,
        "multiline": False,
    }


@pytest.mark.parametrize("batch_read", [False, True])
def test_tool_loop_rejects_spend_before_writeback(
    tmp_path: Path, batch_read: bool,
) -> None:
    fixture = _build_fixture(tmp_path / "oracle")
    transport = ScriptedDoubaoExecTransport(
        [
            ScriptedExecToolAction(
                command=fixture.quota_guard_command.replace(
                    '"${LOOPX_TURN:?}"',
                    "turn-test-001",
                )
            ),
            ScriptedExecToolAction(command=(
                "cat -- .codex/goals/terminal-settlement-fixture/ACTIVE_GOAL_STATE.md "
                "fixture/settlement-proof.json"
                if batch_read else "cat fixture/settlement-proof.json"
            )),
            _spend_action,
        ]
    )

    receipt = DoubaoTerminalSettlementToolBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="terminal-spend-before-writeback",
        fixture_root=tmp_path / "actor",
    )

    assert receipt["qualification_passed"] is False
    assert receipt["failure_code"] == "quota_spend_before_writeback"
    assert receipt["settlement_receipt_steps"] == []


@pytest.mark.parametrize("read_command", [
    "cat -- fixture/missing.json fixture/settlement-proof.json",
    "cat -- fixture/settlement-proof.json fixture/missing.json",
])
def test_batch_read_failure_cannot_admit_terminal_writeback(
    tmp_path: Path, read_command: str,
) -> None:
    fixture = _build_fixture(tmp_path / "oracle")
    transport = ScriptedDoubaoExecTransport([
        ScriptedExecToolAction(command=fixture.quota_guard_command.replace(
            '"${LOOPX_TURN:?}"', "turn-test-001",
        )),
        ScriptedExecToolAction(command=read_command),
        _writeback_action,
    ])
    receipt = DoubaoTerminalSettlementToolBehaviorActor(
        api_key="test-only-placeholder", transport=transport,
    ).qualify(
        qualification_id="terminal-batch-read-failure", fixture_root=tmp_path / "actor",
    )
    assert receipt["qualification_passed"] is False
    assert receipt["settlement_receipt_steps"] == []
    assert len(transport.requests) == 2


def test_real_rejection_loop_settles_projected_todos_then_stops(
    tmp_path: Path,
) -> None:
    transport = ScriptedDoubaoExecTransport(
        [
            _reentry_todo_action(0),
            _reentry_todo_action(1),
            _reentry_quota_action,
            ScriptedAssistantAction(content="Terminal lifecycle is settled."),
        ]
    )

    receipt = DoubaoTerminalSettlementToolBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify_reentry(
        qualification_id="terminal-reentry-tool-loop-001",
        fixture_root=tmp_path / "actor",
    )

    assert receipt["qualification_passed"] is True, json.dumps(receipt, sort_keys=True)
    assert receipt["failure_code"] is None
    assert receipt["provider_call_count"] == 4
    assert receipt["tool_call_count"] == 3
    assert receipt["projected_todo_count"] == 2
    assert receipt["settled_todo_count"] == 2
    assert receipt["completion_identity_preserved"] is True
    assert receipt["terminal_quota_observed"] is True
    assert receipt["model_stopped_after_terminal_quota"] is True
    assert receipt["observed_tool_sequence"] == [
        "todo_lifecycle_settlement",
        "todo_lifecycle_settlement",
        "terminal_quota_reentry",
    ]
    transition = _latest_reentry_transition(transport.requests[0])
    assert len(transition["triggers"]) == 2
    assert all(
        trigger["completion_identity_source"] == "unscoped_completion"
        for trigger in transition["triggers"]
    )
    assert all(
        str(trigger["completion_turn_key"]).startswith("local_completion_")
        for trigger in transition["triggers"]
    )
    receipt_text = json.dumps(receipt, sort_keys=True)
    assert all(
        trigger["todo_id"] not in receipt_text for trigger in transition["triggers"]
    )


def test_rejection_loop_rejects_repeated_refresh(tmp_path: Path) -> None:
    transport = ScriptedDoubaoExecTransport(
        [
            ScriptedExecToolAction(
                command=(
                    "loopx refresh-state --goal-id terminal-reentry-fixture "
                    "--agent-id codex-terminal-reentry"
                )
            )
        ]
    )

    receipt = DoubaoTerminalSettlementToolBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify_reentry(
        qualification_id="terminal-reentry-repeat-refresh",
        fixture_root=tmp_path / "actor",
    )

    assert receipt["qualification_passed"] is False
    assert receipt["failure_code"] == "repeated_refresh_state"
    assert receipt["settled_todo_count"] == 0


def test_rejection_loop_rejects_completion_identity_drift(tmp_path: Path) -> None:
    def drifted_action(request: Mapping[str, Any]) -> ScriptedExecToolAction:
        action = _reentry_todo_action(0)(request)
        command = action.command.replace(
            "local_completion_",
            "local_completion_drift_",
            1,
        )
        return ScriptedExecToolAction(command=command)

    transport = ScriptedDoubaoExecTransport([drifted_action])
    receipt = DoubaoTerminalSettlementToolBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify_reentry(
        qualification_id="terminal-reentry-identity-drift",
        fixture_root=tmp_path / "actor",
    )

    assert receipt["qualification_passed"] is False
    assert receipt["failure_code"] == "lifecycle_settlement_identity_mismatch"
    assert receipt["settled_todo_count"] == 0
