from __future__ import annotations

import json
import shlex
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from loopx.control_plane.testing.model_tool_behavior import (
    QuotaToolContext,
    EXEC_COMMAND_TOOL, ScriptedAssistantAction, ScriptedDoubaoExecTransport, ScriptedExecToolAction,
)
from loopx.control_plane.testing.replan_semantic_action_behavior import (
    DoubaoReplanSemanticActionBehaviorActor, _build_fixture,
)
from loopx.control_plane.testing import replan_semantic_action_behavior, vision_shell_host
from loopx.control_plane.testing.vision_shell_host import VisionShellHost, shell_isolation_available
from loopx.control_plane.turn_driver.decision_capture import read_context_read_reference

pytestmark = pytest.mark.skipif(not shell_isolation_available(), reason="Native shell needs sandbox-exec or bubblewrap")


def _packet(request: Mapping[str, Any]) -> dict[str, Any]:
    packet: dict[str, Any] = {}
    for message in request["messages"]:
        if message["role"] == "tool":
            try:
                value = json.loads(message["content"])
            except json.JSONDecodeError:
                continue
            if not isinstance(value, dict):
                continue
            if value.get("schema_version") in {"context_capture_selection_page_v1", "context_capture_page_v1"}:
                selected = json.loads(value["content"])
                value = {}
                for row in selected["selections"]:
                    if row["pointer"] == "/interaction_contract/cli_channel":
                        value["interaction_contract"] = {"cli_channel": row["value"]}
                    else:
                        value[row["pointer"].removeprefix("/")] = row["value"]
            if "replan_action_packet" in value:
                packet = value
            elif value.get("stage") == "writeback" and packet:
                action = packet["replan_action_packet"]
                assert value["obligation_id"] == action["obligation_id"]
                packet["replan_action_packet"] = {
                    **action, "stride_control": value["stride_control"],
                    "writeback_contract": value["writeback_contract"],
                }
                original_cli = packet["interaction_contract"]["cli_channel"]
                packet["interaction_contract"]["cli_channel"] = {
                    **original_cli, **value["writeback"],
                }
    if packet:
        return packet
    raise AssertionError("quota was not observed")


def vision_patch_action(request: Mapping[str, Any]) -> ScriptedExecToolAction:
    # Authored independently from the implementation under test. The path is
    # still open: evidence confirms reader-by-default, not completion of a Goal.
    packet = _packet(request)
    control = packet.get("stride_control") or packet["replan_action_packet"]["stride_control"]
    assert control["schema_version"] == "stride_decision_v2"
    assert control["strategy_version"] == "evidence_conditioned_v1"
    decision = {
        "schema_version": "goal_vision_replan_contract_v0",
        "state": "vision_patch_proposed",
        "vision_patch": {
            "vision_summary": "Preserve the explicit write permission boundary.",
            "acceptance_summary": "Reader is default; writing requires an explicit grant.",
            "advancement_policy": "as_needed",
        },
        "path_delta": {
            "schema_version": "goal_path_delta_v0", "outcome": "continue",
            "prior_assumption": "The permission boundary needed inspection.",
            "observed_reality": "The configuration defaults to reader and requires a write grant.",
            "retained": ["Explicit write grant"], "changed": ["Evidence-linked vision baseline"],
            "evidence_refs": ["evidence-permission-config"],
        },
        "stride_decision": {
            "schema_version": "stride_decision_v2",
            "strategy_version": "evidence_conditioned_v1",
            "reference_context_id": control["reference_context_id"],
            "reference_obligation_id": control["reference_obligation_id"],
            "basis": "uncertain_evidence",
            "action": "verify",
            "axes": {
                "work_slice": {
                    "plan": "Verify reader defaults and the explicit write grant together.",
                    "reason": "Both facts determine whether the permission boundary is usable.",
                },
                "route_distance": {
                    "plan": "Keep configuration inspection; test the ungranted write assumption.",
                    "reason": "Reading the default alone does not establish write authority.",
                },
                "validation_scope": {
                    "plan": "Check reader default and refusal without an explicit write grant.",
                    "reason": "A contrary permission result would change the next implementation choice.",
                },
            },
            "next_probe": {
                "scope": "Reader and ungranted-write behavior.",
                "prediction": "Reader remains default; writes need a grant.",
                "minimum_evidence": "Configuration source plus an ungranted-write refusal.",
                "adoption_criterion": "Both permission outcomes preserve the explicit boundary.",
                "cost_bound": "One isolated permission check and its refusal case.",
            },
        },
    }
    return ScriptedExecToolAction("cat > decision.json <<'JSON'\n" + json.dumps(decision, indent=2) + "\nJSON")


def projected_refresh(request: Mapping[str, Any]) -> ScriptedExecToolAction:
    actions = _packet(request)["interaction_contract"]["cli_channel"]["next_cli_actions"]
    return ScriptedExecToolAction(actions[0].replace(
        "<path-to-evidence-linked-goal-vision-replan-contract-v0.json>", "decision.json",
    ))


def projected_spend(request: Mapping[str, Any]) -> ScriptedExecToolAction:
    cli = _packet(request)["interaction_contract"]["cli_channel"]
    for step in cli.get("settlement_plan", {}).get("ordered_steps", []):
        if step["kind"] == "quota_spend":
            return ScriptedExecToolAction(step["command_template"])
    return ScriptedExecToolAction(cli["next_cli_actions"][1])


def _read_current_vision(request: Mapping[str, Any]) -> ScriptedExecToolAction:
    navigation = json.loads(request["messages"][-1]["content"])
    assert navigation["schema_version"] == "context_capture_navigation_v1"
    return ScriptedExecToolAction(navigation["recovery"]["command"] +
        " --select /autonomous_replan_obligation --select /replan_action_packet"
        " --select /interaction_contract/cli_channel")


def _vision_transport(actions: list[Any], *, exhaust_before_spend: bool | None = None) -> ScriptedDoubaoExecTransport:
    remaining = list(actions)

    def choose(request: Mapping[str, Any]) -> Any:
        latest = request["messages"][-1]
        try:
            value = json.loads(latest["content"]) if latest["role"] == "tool" else None
        except json.JSONDecodeError:
            value = None
        if isinstance(value, dict) and value.get("schema_version") == "context_capture_navigation_v1":
            return _read_current_vision(request)
        if isinstance(value, dict) and value.get("schema_version") == "context_capture_selection_page_v1":
            if not value.get("eof"):
                return ScriptedExecToolAction(value["next_command"])
            action = _packet(request)["replan_action_packet"]
            if isinstance(action.get("review"), str):
                return ScriptedExecToolAction(action["review"])
        if isinstance(value, dict) and value.get("stage") == "review":
            action = _packet(request)["replan_action_packet"]
            if isinstance(action.get("writeback"), str):
                return ScriptedExecToolAction(action["writeback"])
        if exhaust_before_spend is not None and len(remaining) == 3:
            # Leave the last three calls for authoring, writeback and spend.
            # Count delivered reader calls too; staged paths need less padding.
            call = sum(message["role"] == "tool" for message in request["messages"]) + 1
            if call < 38 + int(exhaust_before_spend):
                return ScriptedExecToolAction("printf inspected")
        if not remaining:
            return ScriptedAssistantAction("No further scripted action.")
        action = remaining.pop(0)
        return action(request) if callable(action) else action

    return ScriptedDoubaoExecTransport([choose] * 40)


def _qualify(tmp_path: Path, actions: list[Any]) -> dict[str, Any]:
    transport = _vision_transport(actions)
    return DoubaoReplanSemanticActionBehaviorActor(api_key="test-only-placeholder", transport=transport).qualify(
        qualification_id="native-vision-closeout", fixture_root=tmp_path / "actor", required_vision=True,
    )


def _run_history(tmp_path: Path) -> list[dict[str, Any]]:
    index = tmp_path / "actor/runtime/goals/replan-semantic-action-fixture/runs/index.jsonl"
    return [json.loads(line) for line in index.read_text(encoding="utf-8").splitlines()]


def test_current_capture_reader_completes_native_shell_closeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY", "on")
    fixture = _build_fixture(tmp_path / "oracle", required_vision=True)
    transport = _vision_transport([
        ScriptedExecToolAction(fixture.quota_guard_command),
        ScriptedExecToolAction("cat fixture/permission-config.json"),
        vision_patch_action, projected_refresh, projected_spend,
    ])
    result = DoubaoReplanSemanticActionBehaviorActor(
        api_key="test-only-placeholder", transport=transport,
    ).qualify(qualification_id="native-module-reader-closeout",
              fixture_root=tmp_path / "actor", required_vision=True)
    outputs = [json.loads(message["content"])
               for message in transport.requests[-1]["messages"]
               if message["role"] == "tool" and message["content"].startswith("{")]
    navigation = outputs[0]
    reader = shlex.split(navigation["recovery"]["command"])
    assert reader[1:3] == ["-m", "loopx.entrypoint"]
    page = outputs[1]
    assert page["schema_version"] == "context_capture_selection_page_v1" and page["eof"] is True
    packet = _packet(transport.requests[-1])
    action = packet["replan_action_packet"]
    assert "agent_vision.stride_decision" in action["writeback_contract"]["required_fields"]
    assert "fields" in action["writeback_contract"]["vision_authoring"]
    plan = packet["interaction_contract"]["cli_channel"]["settlement_plan"]
    assert plan["identity"]["replan_obligation_id"] == packet["autonomous_replan_obligation"]["obligation_id"]
    writer = next(value for value in outputs if value.get("appended") is True)
    assert writer["settlement_identity"] == plan["identity"]
    assert writer["settlement_progress"]["state"] == "spend_required"
    assert result["qualification_passed"] is True, result
    assert result["execution_host"] == "os_isolated_shell"
    assert result["vision_closeout"]["settled"] is True
    assert result["vision_closeout"]["spend_count"] == 1
    assert result["tool_call_limit"] == 40
    decisions = [row["agent_vision"]["stride_decision"]
                 for row in _run_history(tmp_path) if isinstance(row.get("agent_vision"), dict)]
    assert len(decisions) == 1
    control = action["stride_control"]
    assert decisions[0]["reference_context_id"] == control["reference_context_id"]
    assert decisions[0]["reference_obligation_id"] == control["reference_obligation_id"]
    assert decisions[0]["schema_version"] == "stride_decision_v2"


@pytest.mark.parametrize("context_mode", ["on", "off"])
@pytest.mark.parametrize("invalid", [
    "missing", "malformed_context", "other_obligation", "missing_axis_reason", "missing_minimum_evidence",
])
def test_native_stride_rejection_cannot_write_or_spend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, context_mode: str, invalid: str,
) -> None:
    monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY", context_mode)
    fixture = _build_fixture(tmp_path / "oracle", required_vision=True)

    def author(request: Mapping[str, Any]) -> ScriptedExecToolAction:
        lines = vision_patch_action(request).command.splitlines()
        draft = json.loads("\n".join(lines[1:-1]))
        stride = draft["stride_decision"]
        if invalid == "missing":
            del draft["stride_decision"]
        elif invalid == "malformed_context":
            stride["reference_context_id"] = "foreign-context"
        elif invalid == "other_obligation":
            stride["reference_obligation_id"] = "replan-" + "0" * 16
        elif invalid == "missing_axis_reason":
            del stride["axes"]["route_distance"]["reason"]
        else:
            del stride["next_probe"]["minimum_evidence"]
        return ScriptedExecToolAction(lines[0] + "\n" + json.dumps(draft) + "\nJSON")

    def rejected(request: Mapping[str, Any]) -> ScriptedExecToolAction:
        feedback = json.loads(request["messages"][-1]["content"])
        assert feedback["exit_code"] != 0
        assert "stride" in feedback["output"]
        assert "vision_budget_exceeded" not in feedback["output"]
        if invalid == "missing":
            refusal = json.loads(feedback["output"])
            assert refusal["ok"] is False and refusal["appended"] is False
            transition = refusal["replan_transition"]
            assert transition["reason_code"] == "stride_decision_required"
            assert transition["obligation_id"] == _packet(request)["autonomous_replan_obligation"]["obligation_id"]
        return projected_spend(request)

    def unspent(request: Mapping[str, Any]) -> ScriptedAssistantAction:
        feedback = json.loads(request["messages"][-1]["content"])
        assert "vision_closeout_spend_before_writeback" in feedback["output"]
        return ScriptedAssistantAction("Correct the decision before settlement.")

    result = _qualify(tmp_path, [
        ScriptedExecToolAction(fixture.quota_guard_command),
        ScriptedExecToolAction("cat fixture/permission-config.json"),
        author, projected_refresh, rejected, unspent,
    ])
    assert result["qualification_passed"] is False
    assert not result.get("vision_closeout")
    history = tmp_path / "actor/runtime/goals/replan-semantic-action-fixture/runs/index.jsonl"
    assert history.read_text() == ""


@pytest.mark.parametrize("change", ["source_hash", "decision_file", "other_module"])
def test_native_shell_reader_bridge_does_not_expand_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY", "on")
    fixture = _build_fixture(tmp_path / "oracle", required_vision=True)
    reader: ScriptedExecToolAction | None = None

    def read_allowed(request: Mapping[str, Any]) -> ScriptedExecToolAction:
        nonlocal reader
        reader = _read_current_vision(request)
        return reader

    def invalid_reader(request: Mapping[str, Any]) -> ScriptedExecToolAction:
        assert reader is not None
        allowed = json.loads(request["messages"][-1]["content"])
        assert allowed["ok"] is True and allowed["eof"] is True
        tokens = shlex.split(reader.command)
        if change == "other_module":
            assert tokens[1:3] == ["-m", "loopx.entrypoint"]
            tokens[2] = "loopx"
        else:
            index = tokens.index("--reference-file")
            _, reference = read_context_read_reference(tokens[index + 1])
            capture = Path(reference["path"])
            digest = reference["sha256"]
            if change == "source_hash":
                digest = "0" * 64
            else:
                # The copy contains the same valid bytes but has no current capture binding.
                other = capture.with_name("foreign.json")
                other.write_bytes(capture.read_bytes())
                capture = other
            tokens[index:index + 2] = ["--capture-file", str(capture), "--capture-sha256", digest]
        return ScriptedExecToolAction(shlex.join(tokens))

    def rejected(request: Mapping[str, Any]) -> ScriptedAssistantAction:
        error = json.loads(request["messages"][-1]["content"])
        assert error["exit_code"] != 0
        assert error["error_code"] == "shell_nonzero"
        if change != "other_module":
            assert "context reader does not match the current quota capture" in error["output"]
        return ScriptedAssistantAction("No accepted reader or state mutation.")

    result = DoubaoReplanSemanticActionBehaviorActor(
        api_key="test-only-placeholder", transport=ScriptedDoubaoExecTransport([
            ScriptedExecToolAction(fixture.quota_guard_command), read_allowed, invalid_reader, rejected,
        ]),
    ).qualify(qualification_id="native-reader-boundary",
              fixture_root=tmp_path / "actor", required_vision=True)
    assert result["qualification_passed"] is False
    assert result["semantic_action_accepted"] is False
    assert not result.get("vision_closeout")
    assert not _run_history(tmp_path)


@pytest.mark.parametrize("policy", ["as_needed", "repeat_until_closed"])
@pytest.mark.parametrize("compound", [False, True])
def test_native_shell_closes_real_cli_turn_without_command_rituals(tmp_path: Path, policy: str, compound: bool) -> None:
    fixture = _build_fixture(tmp_path / "oracle", required_vision=True)
    def author(request: Mapping[str, Any]) -> ScriptedExecToolAction:
        command = vision_patch_action(request).command.replace("as_needed", policy)
        command += "\necho authored\npython3 -m json.tool decision.json >/dev/null"
        if compound:
            refresh = projected_refresh(request).command.replace("'decision.json'", '"$VISION"')
            command += "\nVISION=decision.json\n" + refresh + " && " + projected_spend(request).command
        return ScriptedExecToolAction(command)
    actions = [
        ScriptedExecToolAction("pwd && ls -la && loopx --help | head -100"),
        ScriptedExecToolAction(fixture.quota_guard_command),
        ScriptedExecToolAction("python3 -c \"import json; print(json.dumps(json.load(open('fixture/permission-config.json'))))\""),
        author,
    ]
    if not compound:
        actions += [projected_refresh, projected_spend]
    result = _qualify(tmp_path, actions)
    assert result["qualification_passed"] is True, result
    assert result["execution_host"] == "os_isolated_shell"
    assert result["boundary"]["shell_commands_executed"] is True
    assert result["selected_semantic_outcomes"] == ["fresh_vision_path_outcome"]
    assert result["vision_closeout"] == {
        "checkpoint_satisfied": True, "bound_writeback": True, "settled": True,
        "spend_count": 1, "original_obligation_closed": True,
    }
    assert "required_agent_vision_missing" not in result["semantic_reentry"]["trigger_kinds"]


@pytest.mark.parametrize(("evidence", "failure"), [
    ("evidence-permission-config", "budget"),
    ("fixture/permission-config.json", "budget"),
    ("evidence-permission-config", "missing_stride"),
])
def test_cli_validation_errors_are_correctable_in_the_same_draft(tmp_path: Path, evidence: str, failure: str) -> None:
    fixture = _build_fixture(tmp_path / "oracle", required_vision=True)

    def invalid_draft(request: Mapping[str, Any]) -> ScriptedExecToolAction:
        action = vision_patch_action(request)
        if failure == "budget":
            return ScriptedExecToolAction(action.command.replace(
                "Reader is default; writing requires an explicit grant.", "x" * 700))
        lines = action.command.splitlines()
        draft = json.loads("\n".join(lines[1:-1]))
        del draft["stride_decision"]
        return ScriptedExecToolAction(lines[0] + "\n" + json.dumps(draft) + "\nJSON")

    def correct(request: Mapping[str, Any]) -> ScriptedExecToolAction:
        response = QuotaToolContext.from_output(request["messages"][-1]["content"]).packet
        assert response["exit_code"] != 0
        if failure == "missing_stride":
            refusal = json.loads(response["output"])
            assert refusal["appended"] is False
            assert refusal["replan_transition"]["reason_code"] == "stride_decision_required"
        else:
            assert "vision_budget_exceeded" in response["output"]
        return ScriptedExecToolAction(vision_patch_action(request).command.replace("evidence-permission-config", evidence))

    result = _qualify(tmp_path, [
        ScriptedExecToolAction(fixture.quota_guard_command),
        ScriptedExecToolAction("cat fixture/permission-config.json"),
        invalid_draft, projected_refresh, correct, projected_refresh, projected_spend,
    ])
    assert result["qualification_passed"] is True, result
    assert result["vision_closeout"]["spend_count"] == 1


def test_missing_durable_receipt_reaches_shell_and_can_be_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _build_fixture(tmp_path / "oracle", required_vision=True)
    execute = replan_semantic_action_behavior._execute_loopx
    intercepted = False

    def first_refresh_without_receipt(command: str, **kwargs: Any) -> str:
        nonlocal intercepted
        if "refresh-state" in command and not intercepted:
            intercepted = True
            return '{"ok": true}'
        return execute(command, **kwargs)

    def retry_after_error(request: Mapping[str, Any]) -> ScriptedExecToolAction:
        feedback = QuotaToolContext.from_output(request["messages"][-1]["content"]).packet
        assert feedback["exit_code"] != 0
        assert "vision_closeout_durable_writeback_missing" in feedback["output"]
        return projected_refresh(request)

    monkeypatch.setattr(replan_semantic_action_behavior, "_execute_loopx", first_refresh_without_receipt)
    result = _qualify(tmp_path, [
        ScriptedExecToolAction(fixture.quota_guard_command),
        ScriptedExecToolAction("cat fixture/permission-config.json"),
        vision_patch_action,
        projected_refresh,
        retry_after_error,
        projected_spend,
    ])
    assert intercepted is True
    assert result["qualification_passed"] is True
    assert result["vision_closeout"]["spend_count"] == 1


@pytest.mark.parametrize("invalid", ["no_source", "wrong_turn", "unread_reference", "no_spend"])
def test_native_host_does_not_qualify_unproven_closeout(tmp_path: Path, invalid: str) -> None:
    fixture = _build_fixture(tmp_path / "oracle", required_vision=True)
    def author(request: Mapping[str, Any]) -> ScriptedExecToolAction:
        command = vision_patch_action(request).command
        if invalid == "unread_reference":
            command = command.replace("evidence-permission-config", "unread-source")
        return ScriptedExecToolAction(command)
    def refresh(request: Mapping[str, Any]) -> ScriptedExecToolAction:
        tokens = shlex.split(projected_refresh(request).command)
        if invalid == "wrong_turn":
            tokens[tokens.index("--turn-instance-id") + 1] = "wrong-turn"
        return ScriptedExecToolAction(shlex.join(tokens))
    actions = [ScriptedExecToolAction(fixture.quota_guard_command)]
    if invalid != "no_source":
        actions.append(ScriptedExecToolAction("cat fixture/permission-config.json"))
    actions += [author, refresh, ScriptedAssistantAction("Stopped before a verified settlement.")]
    result = _qualify(tmp_path, actions)
    assert result["qualification_passed"] is False
    assert not (result.get("vision_closeout") or {}).get("settled")


@pytest.mark.parametrize("context_mode", ["on", "off"])
@pytest.mark.parametrize("passed", [True, False])
def test_budget_boundary_still_requires_the_final_spend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, context_mode: str, passed: bool) -> None:
    monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY", context_mode)
    fixture = _build_fixture(tmp_path / "oracle", required_vision=True)
    # The only difference is whether spend would be call 40 or call 41.
    transport = _vision_transport([
        ScriptedExecToolAction(fixture.quota_guard_command),
        ScriptedExecToolAction("cat fixture/permission-config.json"),
        vision_patch_action, projected_refresh, projected_spend,
    ], exhaust_before_spend=not passed)
    result = DoubaoReplanSemanticActionBehaviorActor(
        api_key="test-only-placeholder", transport=transport,
    ).qualify(qualification_id="native-vision-closeout", fixture_root=tmp_path / "actor", required_vision=True)
    assert result["qualification_passed"] is passed
    assert result["tool_call_count"] == result["tool_call_limit"] == 40
    assert result["failure_code"] == (None if passed else "tool_call_budget_exhausted")
    assert result["vision_closeout"]["bound_writeback"] is True
    assert result["vision_closeout"]["settled"] is passed
    spends = [row for row in _run_history(tmp_path) if row.get("classification") == "quota_slot_spent"]
    assert len(spends) == int(passed)


@pytest.mark.parametrize("context_mode", ["on", "off"])
def test_narrow_actor_retains_its_existing_budget_and_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, context_mode: str) -> None:
    monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY", context_mode)
    fixture = _build_fixture(tmp_path / "oracle")
    transport = ScriptedDoubaoExecTransport([
        ScriptedExecToolAction(fixture.quota_guard_command),
        *[ScriptedExecToolAction("pwd") for _ in range(7)],
    ])
    result = DoubaoReplanSemanticActionBehaviorActor(api_key="test-only-placeholder", transport=transport).qualify(
        qualification_id="narrow-unchanged", fixture_root=tmp_path / "actor",
    )
    assert result["ordinary_tool_call_limit"] == result["tool_call_count"] == 7
    assert result["tool_call_limit"] == (10 if context_mode == "on" else 7)
    assert len(transport.requests) == 7
    assert transport.requests[0]["tools"] == [EXEC_COMMAND_TOOL]


def test_os_boundary_protects_inputs_authority_private_data_and_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = _build_fixture(tmp_path / "fixture", required_vision=True)
    private = tmp_path / "private.txt"
    private.write_text("synthetic-private-marker")
    monkeypatch.setenv("ARK_API_KEY", "synthetic-do-not-inherit")
    host = VisionShellHost(fixture.project_root, lambda *args: "ok", turn_instance_id="shell-isolation-test")
    original = fixture.work_source_target.read_bytes()
    try:
        for command in [
            "echo forged > fixture/permission-config.json",
            "mv fixture renamed-inputs",
            f"echo forged > {shlex.quote(str(fixture.runtime_root / 'forged.json'))}",
            f"cat {shlex.quote(str(private))}",
            "python3 -c \"import socket; socket.create_connection(('127.0.0.1',9),timeout=1)\"",
        ]:
            output, code = host.execute(command)
            assert code != 0
            assert "synthetic-private-marker" not in output
        output, code = host.execute("printf '%s' \"$ARK_API_KEY\"; echo draft > draft.txt; python3 -c \"import json; print(json.dumps({'valid':True}))\"")
        assert code == 0 and "synthetic-do-not-inherit" not in output
        assert fixture.work_source_target.read_bytes() == original
        assert (fixture.project_root / "draft.txt").read_text().strip() == "draft"
        assert not (fixture.runtime_root / "forged.json").exists()
    finally:
        host.close()


def test_timed_out_shell_kills_process_group_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = _build_fixture(tmp_path / "fixture", required_vision=True)
    host = VisionShellHost(fixture.project_root, lambda *args: "ok", turn_instance_id="shell-timeout-test")
    original_killpg = vision_shell_host.os.killpg
    calls: list[int] = []

    def kill_once(pid: int, sig: int) -> None:
        calls.append(pid)
        if len(calls) > 1:
            raise AssertionError("A timed-out process group must not be killed twice")
        original_killpg(pid, sig)

    monkeypatch.setattr(vision_shell_host, "_COMMAND_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(vision_shell_host.os, "killpg", kill_once)
    try:
        _, code = host.execute("sleep 5")
        assert code == 124
        assert len(calls) == 1
    finally:
        host.close()


def test_actor_cannot_shadow_the_trusted_cli_in_its_writable_project(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path / "oracle", required_vision=True)
    def check_real_cli(request: Mapping[str, Any]) -> ScriptedAssistantAction:
        outputs = [message["content"] for message in request["messages"] if message["role"] == "tool"]
        assert any("loopx <command> --help" in output for output in outputs)
        assert all("SHADOWED_CLI" not in output for output in outputs)
        return ScriptedAssistantAction("Only checking executor source provenance.")
    result = _qualify(tmp_path, [
        ScriptedExecToolAction(fixture.quota_guard_command),
        ScriptedExecToolAction("mkdir loopx; touch loopx/__init__.py; printf 'print(\"SHADOWED_CLI\")' > loopx/cli.py; loopx --help"),
        check_real_cli,
    ])
    assert result["qualification_passed"] is False
