from __future__ import annotations

import json
import shlex
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from loopx.control_plane.testing.model_tool_behavior import (
    EXEC_COMMAND_TOOL, ScriptedAssistantAction, ScriptedDoubaoExecTransport, ScriptedExecToolAction,
)
from loopx.control_plane.testing.replan_semantic_action_behavior import (
    DoubaoReplanSemanticActionBehaviorActor, _build_fixture,
)
from loopx.control_plane.testing.vision_shell_host import VisionShellHost, shell_isolation_available

pytestmark = pytest.mark.skipif(not shell_isolation_available(), reason="Native shell needs sandbox-exec or bubblewrap")


def _packet(request: Mapping[str, Any]) -> dict[str, Any]:
    for message in request["messages"]:
        if message["role"] == "tool":
            try:
                value = json.loads(message["content"])
            except json.JSONDecodeError:
                continue
            if "replan_action_packet" in value:
                return value
    raise AssertionError("quota was not observed")


def vision_patch_action(_: Mapping[str, Any]) -> ScriptedExecToolAction:
    # Authored independently from the implementation under test. The path is
    # still open: evidence confirms reader-by-default, not completion of a Goal.
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
    }
    return ScriptedExecToolAction("cat > decision.json <<'JSON'\n" + json.dumps(decision, indent=2) + "\nJSON")


def projected_refresh(request: Mapping[str, Any]) -> ScriptedExecToolAction:
    actions = _packet(request)["interaction_contract"]["cli_channel"]["next_cli_actions"]
    return ScriptedExecToolAction(actions[0].replace(
        "<path-to-evidence-linked-goal-vision-replan-contract-v0.json>", "decision.json",
    ))


def projected_spend(request: Mapping[str, Any]) -> ScriptedExecToolAction:
    return ScriptedExecToolAction(_packet(request)["interaction_contract"]["cli_channel"]["next_cli_actions"][1])


def _qualify(tmp_path: Path, actions: list[Any]) -> dict[str, Any]:
    transport = ScriptedDoubaoExecTransport(actions)
    return DoubaoReplanSemanticActionBehaviorActor(api_key="test-only-placeholder", transport=transport).qualify(
        qualification_id="native-vision-closeout", fixture_root=tmp_path / "actor", required_vision=True,
    )


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


@pytest.mark.parametrize("evidence", ["evidence-permission-config", "fixture/permission-config.json"])
def test_cli_validation_errors_are_correctable_in_the_same_draft(tmp_path: Path, evidence: str) -> None:
    fixture = _build_fixture(tmp_path / "oracle", required_vision=True)
    def oversized(request: Mapping[str, Any]) -> ScriptedExecToolAction:
        return ScriptedExecToolAction(vision_patch_action(request).command.replace(
            "Reader is default; writing requires an explicit grant.", "x" * 700))
    def correct(request: Mapping[str, Any]) -> ScriptedExecToolAction:
        response = json.loads(request["messages"][-1]["content"])
        assert response["exit_code"] != 0
        assert "vision_budget_exceeded" in response["output"]
        return ScriptedExecToolAction(vision_patch_action(request).command.replace("evidence-permission-config", evidence))
    result = _qualify(tmp_path, [
        ScriptedExecToolAction(fixture.quota_guard_command),
        ScriptedExecToolAction("cat fixture/permission-config.json"),
        oversized, projected_refresh, correct, projected_refresh, projected_spend,
    ])
    assert result["qualification_passed"] is True, result
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


@pytest.mark.parametrize("extra_reads,passed", [(27, True), (28, False)])
def test_budget_boundary_still_requires_the_final_spend(tmp_path: Path, extra_reads: int, passed: bool) -> None:
    fixture = _build_fixture(tmp_path / "oracle", required_vision=True)
    result = _qualify(tmp_path, [
        ScriptedExecToolAction(fixture.quota_guard_command),
        ScriptedExecToolAction("cat fixture/permission-config.json"),
        *[ScriptedExecToolAction("printf inspected") for _ in range(extra_reads)],
        vision_patch_action, projected_refresh, projected_spend,
    ])
    assert result["qualification_passed"] is passed
    assert result["tool_call_count"] == result["tool_call_limit"] == 32
    assert result["vision_closeout"]["settled"] is passed


def test_narrow_actor_retains_its_existing_budget_and_tool(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path / "oracle")
    transport = ScriptedDoubaoExecTransport([
        ScriptedExecToolAction(fixture.quota_guard_command),
        *[ScriptedExecToolAction("pwd") for _ in range(7)],
    ])
    result = DoubaoReplanSemanticActionBehaviorActor(api_key="test-only-placeholder", transport=transport).qualify(
        qualification_id="narrow-unchanged", fixture_root=tmp_path / "actor",
    )
    assert result["tool_call_limit"] == result["tool_call_count"] == 7
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


def test_actor_cannot_shadow_the_trusted_cli_in_its_writable_project(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path / "oracle", required_vision=True)
    def check_real_cli(request: Mapping[str, Any]) -> ScriptedAssistantAction:
        output = request["messages"][-1]["content"]
        assert "loopx <command> --help" in output and "SHADOWED_CLI" not in output
        return ScriptedAssistantAction("Only checking executor source provenance.")
    result = _qualify(tmp_path, [
        ScriptedExecToolAction(fixture.quota_guard_command),
        ScriptedExecToolAction("mkdir loopx; touch loopx/__init__.py; printf 'print(\"SHADOWED_CLI\")' > loopx/cli.py; loopx --help"),
        check_real_cli,
    ])
    assert result["qualification_passed"] is False
