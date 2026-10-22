from dataclasses import asdict, replace
import json
from pathlib import Path
import subprocess
import sys

import pytest

from loopx_memt_turn.config import AdapterError, load_config

from memt_fixtures import turn


def invoke(config, tmp_path, *arguments):
    path = tmp_path / "operator.json"
    path.write_text(json.dumps(asdict(config)))
    return subprocess.run([sys.executable, "-m", "loopx_memt_turn.cli", "--config", str(path), *arguments],
                          input=json.dumps(turn()), text=True, capture_output=True, timeout=30, cwd=config.workspace)


def test_cli_returns_a_turn_candidate_through_real_sdk_and_http(config, provider, tmp_path):
    first = invoke(config, tmp_path)
    assert first.returncode == 0, first.stderr
    candidate = json.loads(first.stdout)
    assert candidate["turn_key"] == turn()["turn_key"]
    assert candidate["result_kind"] == "validated_progress"
    assert invoke(config, tmp_path).stdout == first.stdout


def test_disabled_config_and_doctor_never_create_memory_or_call_server(config, provider, tmp_path):
    disabled = replace(config, enabled=False)
    doctor = invoke(disabled, tmp_path, "--doctor")
    assert doctor.returncode == 0
    assert json.loads(doctor.stdout)["network_checked"] is False
    response = invoke(disabled, tmp_path)
    assert response.returncode == 1
    assert json.loads(response.stdout)["error"] == "memt_disabled"
    assert provider.requests == []
    assert not Path(config.state_dir).exists()


def test_provider_error_is_not_exposed_and_failed_request_is_not_replayed(config, provider, tmp_path):
    provider.fail_action = "create"
    failure = invoke(config, tmp_path)
    assert failure.returncode == 1
    assert "synthetic-private-provider-error" not in failure.stdout + failure.stderr
    assert json.loads(failure.stdout)["ok"] is False
    retry = invoke(config, tmp_path)
    assert retry.returncode == 1
    assert json.loads(retry.stdout)["error"] == "interrupted_turn_requires_operator_reconciliation"


@pytest.mark.parametrize("field,invalid", [
    ("enabled", "false"), ("base_url", "http://remote.invalid"),
    ("base_url", "https://user:secret@remote.invalid"), ("timeout_seconds", float("nan")),
    ("max_tool_calls", True), ("mcp_command", "shell command"),
])
def test_invalid_operator_configuration_is_rejected_before_execution(config, field, invalid):
    with pytest.raises((AdapterError, ValueError)):
        replace(config, **{field: invalid})


def test_duplicate_json_configuration_cannot_hide_enablement(config, tmp_path):
    path = tmp_path / "operator.json"
    path.write_text(json.dumps(asdict(config))[:-1] + ',"enabled":false}')
    with pytest.raises(AdapterError, match="duplicate_json_field"):
        load_config(path)


def test_task_workspace_cannot_own_operator_configuration(config):
    path = Path(config.workspace) / "operator.json"
    path.write_text(json.dumps(asdict(config)))
    with pytest.raises(AdapterError, match="config_must_be_outside_workspace"):
        load_config(path)


def test_cli_rejects_a_different_execution_workspace_before_provider_calls(config, provider, tmp_path):
    path = tmp_path / "operator.json"
    path.write_text(json.dumps(asdict(config)))
    response = subprocess.run([sys.executable, "-m", "loopx_memt_turn.cli", "--config", str(path)],
                              input=json.dumps(turn()), text=True, capture_output=True, timeout=30, cwd=tmp_path)
    assert response.returncode == 1
    assert json.loads(response.stdout)["error"] == "workspace_binding_mismatch"
    assert provider.requests == []
