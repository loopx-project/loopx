"""Optional OAR dependencies qualify only the hosts that actually use them."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from loopx.chat_agent import agent_endpoint_error
from loopx.chat_endpoint_catalog import builtin_chat_endpoints
from loopx.control_plane.runtime import node_probe
from loopx.control_plane.runtime.node_probe import probe_node
from loopx.control_plane.turn_driver.host_binding import (
    OAR_RUNTIME_UNAVAILABLE,
    REMEDY_CONFIGURE_OAR_RUNTIME,
    managed_executor_binding,
    managed_executor_binding_from_host_args,
    managed_executor_unavailable_payload,
)
from loopx.hosts.oar import installation


def _missing_oar() -> dict:
    return {
        "ready": False,
        "oar_version": "fixture-version",
        "node": "/private/fixture/node",
        "reason": "private diagnostic fixture-credential",
    }


@pytest.mark.parametrize("host", ["codex-cli", "dsh"])
def test_bounded_hosts_fail_closed_on_the_shared_dependency(host: str) -> None:
    binding = managed_executor_binding(
        host,
        environ={"DEEPSEEK_API_KEY": "fixture-credential"},
        module_probe=lambda _name: True,
        oar_probe=_missing_oar,
    )
    assert binding["available"] is False
    assert binding["unavailable_reason"] == OAR_RUNTIME_UNAVAILABLE
    assert binding["unavailable_remediation"] == [REMEDY_CONFIGURE_OAR_RUNTIME]
    assert binding["oar_runtime"] == {
        "ready": False,
        "oar_version": "fixture-version",
        "unavailable_reason": OAR_RUNTIME_UNAVAILABLE,
    }
    refusal = managed_executor_unavailable_payload(
        {"managed_executor": binding}, execute=True, host_projection={"kind": host}
    )
    assert refusal is not None
    assert refusal["remediation"] == [REMEDY_CONFIGURE_OAR_RUNTIME]
    assert refusal["remediation_env_vars"] == []
    assert refusal["remediation_host"] is None
    serialized = json.dumps([binding, refusal])
    assert "fixture-credential" not in serialized
    assert "/private/" not in serialized


@pytest.mark.parametrize(
    ("host", "options"),
    [
        ("generic-cli", {}),
        ("claude-code", {}),
        ("dsh", {"dsh_runner_configured": True}),
        ("codex-cli", {"codex_operation_tools": True}),
        ("dsh", {"dsh_operation_module_configured": True}),
    ],
)
def test_other_transports_do_not_probe_the_optional_dependency(
    monkeypatch: pytest.MonkeyPatch, host: str, options: dict,
) -> None:
    def forbidden() -> dict:
        raise AssertionError("this transport does not use OAR")

    monkeypatch.setattr(
        "loopx.control_plane.effect_runtime.effect_runtime_result",
        lambda _operation, _params: {"transport": {}, "reason": None},
    )
    binding = managed_executor_binding(
        host, environ={"DEEPSEEK_API_KEY": "fixture-credential"},
        module_probe=lambda _name: True, oar_probe=forbidden,
        model="fixture-model", reasoning_effort="high", **options,
    )
    assert "oar_runtime" not in binding
    assert binding["available"] is not False


def test_explicit_runner_argv_preserves_its_own_launch_contract() -> None:
    def forbidden() -> dict:
        raise AssertionError("an explicit DSH runner has no OAR dependency")

    binding = managed_executor_binding_from_host_args(
        ["--host", "dsh", "--dsh-runner", "fixture:run"],
        environ={}, module_probe=lambda _name: False, oar_probe=forbidden,
    )
    assert binding["available"] is True
    assert binding["runtime_probe"]["scope"] == "configured_runner"
    assert "oar_runtime" not in binding


def test_node22_remains_a_core_runtime_but_cannot_launch_oar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(node_probe.shutil, "which", lambda _name: "fixture-node")
    monkeypatch.setattr(
        node_probe.subprocess, "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, "v22.22.3\n", ""),
    )
    assert probe_node().ready is True
    observed = installation.inspect_installation()
    assert observed["ready"] is False
    assert "24" in observed["reason"]
    bounded = managed_executor_binding("codex-cli", environ={})
    assert bounded["unavailable_reason"] == OAR_RUNTIME_UNAVAILABLE
    assert managed_executor_binding("generic-cli", environ={})["available"] is None


def test_unrelated_imports_and_native_codex_chat_never_load_oar() -> None:
    checked = subprocess.run(
        [sys.executable, "-c", """
import sys
from loopx.control_plane.turn_driver.host_binding import managed_executor_binding
from loopx.chat_manager import manager_channel_binding
assert managed_executor_binding('generic-cli')['available'] is None
assert managed_executor_binding('claude-code')['available'] is None
assert manager_channel_binding({'LOOPX_MANAGER_ENDPOINT': 'codex'})['available'] is None
assert not any(name.startswith('loopx.hosts.oar') for name in sys.modules)
"""],
        capture_output=True, text=True, check=False,
    )
    assert checked.returncode == 0, checked.stderr


def test_endpoint_catalog_keeps_native_codex_available_and_repairs_managed_oar(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(installation, "inspect_installation", _missing_oar)
    monkeypatch.setattr(
        "loopx.control_plane.turn_driver.host_binding.dsh_runtime_importable",
        lambda _probe=None: True,
    )
    monkeypatch.setattr(
        "loopx.chat_endpoint_catalog.operator_provider_environ",
        lambda _root: {"DEEPSEEK_API_KEY": "fixture-credential"},
    )
    rows = {row["agent_id"]: row for row in builtin_chat_endpoints(
        codex_bin=sys.executable, claude_bin="missing-fixture", kiro_cli_bin="missing-fixture",
        runtime_root=tmp_path,
    )}
    assert rows["codex"]["available"] is True
    assert "oar_runtime" not in rows["codex"]
    assert rows["dsh"]["available"] is False
    assert rows["dsh"]["unavailable_reason"] == OAR_RUNTIME_UNAVAILABLE
    assert rows["dsh"]["oar_runtime"]["ready"] is False
    assert rows["dsh"]["interrupt"] is True
    error = agent_endpoint_error("dsh", reason=OAR_RUNTIME_UNAVAILABLE)
    assert error.error_code == "agent_endpoint_unavailable"
    assert "python -m loopx.hosts.oar install" in error.gate["next_action"]
    assert "python -m loopx.hosts.oar status" in error.gate["next_action"]


def test_doctor_reports_oar_as_optional_without_blocking_core(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from loopx import doctor

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(installation, "inspect_installation", lambda: {
        "ready": False, "oar_version": "fixture-version",
        "reason": "OAR requires Node.js 24+",
        "next_action": "Run `python -m loopx.hosts.oar install`.",
    })
    monkeypatch.setattr(
        "loopx.control_plane.effect_runtime.collect_effect_runtime_readiness",
        lambda *, deep: {"ready": True, "status": "ready", "detected_node_version": "22.22.3"},
    )
    result = doctor.collect_doctor(
        registry_path=tmp_path / "registry.json",
        runtime_root_override=str(tmp_path / "runtime"),
    )
    check = next(row for row in result["checks"] if row["id"] == "oar_runtime_ready")
    assert check["required"] is False
    assert check["ok"] is False
    assert result["typescript_control_plane"]["ready"] is True
    markdown = doctor.render_doctor_markdown(result)
    assert "## Optional OAR Host Runtime" in markdown
    assert "python -m loopx.hosts.oar install" in markdown
    assert "Core commands and native Codex Chat do not require this dependency." in markdown
