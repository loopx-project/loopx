"""The original delegation CLI distinguishes transport from model readiness."""

import json

import pytest

from test_delegation_cli import cli
from test_local_delegation import service as delegation_service

service = delegation_service


@pytest.mark.parametrize("configured", [False, None, "true", 1, [True]])
def test_dsh_transport_requires_explicit_native_module_observation(configured):
    from loopx.control_plane.effect_runtime import effect_runtime_result

    result = effect_runtime_result("operation.managed_transport.project", {
        "host": "dsh", "dsh_operation_module_configured": configured,
        "model": "fixture-model", "reasoning_effort": "high",
    })
    assert result["reason"] == "operation_transport_host_unsupported"
    assert result["transport"]["configuration_valid"] is False
    assert result["transport"]["runtime_qualified"] is False


@pytest.mark.parametrize("host", [["codex-cli"], {"host": "codex-cli"}, None, True, 1])
def test_supported_host_readback_never_coerces_an_invalid_host(host):
    from loopx.control_plane.effect_runtime import effect_runtime_result

    result = effect_runtime_result("operation.managed_transport.project", {
        "host": host, "sandbox": "read-only", "model": "test-model",
        "reasoning_effort": "high",
    })
    assert result["reason"] == "operation_transport_host_unsupported"
    assert result["remediation"] == ["select_supported_operation_host"]
    assert result["transport"]["configuration_valid"] is False


@pytest.mark.parametrize("sandbox", [["read-only"], ["workspace-write"], {"sandbox": "read-only"}, None, True, 1])
def test_supported_sandbox_readback_never_coerces_an_invalid_sandbox(sandbox):
    from loopx.control_plane.effect_runtime import effect_runtime_result

    result = effect_runtime_result("operation.managed_transport.project", {
        "host": "codex-cli", "sandbox": sandbox, "model": "test-model",
        "reasoning_effort": "high",
    })
    assert result["reason"] == "operation_transport_sandbox_unsupported"
    assert result["remediation"] == ["select_supported_operation_sandbox"]
    assert result["transport"]["configuration_valid"] is False


@pytest.mark.parametrize(
    ("options", "reason", "remedy"),
    [
        (
            ["--host", "codex-cli", "--codex-model", "test-model",
             "--codex-reasoning-effort", "high", "--codex-sandbox", "danger-full-access"],
            "operation_transport_sandbox_unsupported", "select_supported_operation_sandbox",
        ),
        (
            ["--host", "codex-cli"],
            "operation_transport_profile_required", "correct_execution_profile",
        ),
    ],
)
def test_original_cli_inspection_returns_typed_transport_recovery_without_effects(
    service, options, reason, remedy,
):
    root, runner = service
    config = json.loads(runner.config.read_text())
    config["bindings"][0]["host_args"] = [*options, "--codex-operation-tools"]
    runner.config.write_text(json.dumps(config))
    before = runner.config.read_bytes(), runner.registry.read_bytes()

    status, result = cli(runner, "inspect", "--binding-id", "analysis")

    assert status == 0, result
    assert result["executor"]["host"] == options[1]
    assert result["executor"]["available"] is False
    assert result["executor"]["reason"] == reason
    assert result["executor"]["unavailable_remediation"] == [remedy]
    transport = result["executor"]["operation_transport"]
    assert transport["supported_hosts"] == ["codex-cli"]
    assert transport["supported_sandboxes"] == ["read-only", "workspace-write"]
    assert transport["configuration_valid"] is False
    assert transport["runtime_qualified"] is False
    assert transport["human_confirmation_required"] is True
    assert transport["first_consumption_required"] is True
    assert not any(result["effects"].values())
    assert not (root / "host-started").exists()
    assert before == (runner.config.read_bytes(), runner.registry.read_bytes())


def test_cli_does_not_repurpose_codex_operation_flag_for_a_dsh_executor(service):
    root, runner = service
    config = json.loads(runner.config.read_text())
    config["bindings"][0]["host_args"] = [
        "--host", "dsh", "--dsh-runner", "fixture-runner.py",
        "--dsh-provider", "deepseek-official", "--dsh-model", "deepseek-flash",
        "--dsh-reasoning-effort", "high", "--codex-operation-tools",
    ]
    runner.config.write_text(json.dumps(config))
    before = runner.config.read_bytes(), runner.registry.read_bytes()

    status, result = cli(runner, "inspect", "--binding-id", "analysis")

    # The original argv owner rejects this before executor projection. Keep
    # that boundary; direct binding readback does not qualify a DSH CLI route.
    assert status == 1
    assert result["ok"] is False
    assert "--codex-operation-tools requires the codex-cli host" in result["error"]
    assert not (root / "host-started").exists()
    assert before == (runner.config.read_bytes(), runner.registry.read_bytes())
