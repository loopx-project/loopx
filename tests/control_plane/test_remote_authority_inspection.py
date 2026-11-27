"""Installed CLI inspection is explicit, read-only, and safe to share."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from examples.control_plane.quota_plan_fixtures import SCOPED_AGENT_ID, write_cli_fixture
from tests.control_plane.remote_authority_inspection_fixture import (
    CREDENTIAL, bind_remote_authority, remote_authority_service,
)


def test_inspect_disabled_binding_does_not_change_registry_or_runtime(tmp_path):
    registry, runtime, _ = write_cli_fixture(tmp_path, scoped_agents=True)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    result = subprocess.run(
        [sys.executable, "-m", "loopx.entrypoint", "--registry", str(registry),
         "--runtime-root", str(runtime), "remote-authority", "inspect",
         "--goal-id", "full-speed", "--agent-id", SCOPED_AGENT_ID, "--format", "json"],
        env={**os.environ, "LOOPX_USAGE_PING": "0", "LOOPX_CONTEXT_DELIVERY": "off"},
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 1, (result.stdout, result.stderr)
    packet = json.loads(result.stdout)
    assert packet["reason_code"] == "remote_authority_disabled"
    assert packet["execution_admitted"] is False
    assert before == {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}


def _inspect(registry, runtime, *extra, goal="full-speed", actor=SCOPED_AGENT_ID):
    return subprocess.run(
        [sys.executable, "-m", "loopx.entrypoint", "--registry", str(registry),
         "--runtime-root", str(runtime), "remote-authority", "inspect",
         "--goal-id", goal, "--agent-id", actor, *extra],
        env={**os.environ, "LOOPX_USAGE_PING": "0", "LOOPX_CONTEXT_DELIVERY": "off"},
        capture_output=True, text=True, timeout=15,
    )


def test_inspect_real_https_preserves_local_state_and_hides_private_fields(tmp_path, monkeypatch):
    registry, runtime, _ = write_cli_fixture(tmp_path, scoped_agents=True)
    monkeypatch.setenv("LOOPX_AUTHORITY_CREDENTIAL", CREDENTIAL)
    with remote_authority_service(tmp_path, SCOPED_AGENT_ID) as (cfg, control):
        bind_remote_authority(registry, "full-speed", cfg)
        before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
        for mode, reason in (("found", "read_verified"), ("denied", "scope_denied"),
                             ("http", "http"), ("invalid", "response_invalid")):
            control["mode"] = mode
            previous = len(control["requests"])
            result = _inspect(registry, runtime, "--format", "json")
            assert result.returncode == (0 if mode == "found" else 1), result.stderr
            packet = json.loads(result.stdout)
            assert packet["reason_code"] == f"remote_authority_{reason}"
            assert packet["execution_admitted"] is False
            assert len(control["requests"]) == previous + 1
            for private in (CREDENTIAL, *[cfg[key] for key in (
                "endpoint", "tenant_id", "store_identity", "probe_todo_id", "ca_file",
            )]):
                assert private not in result.stdout + result.stderr
            assert before == {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
        control["mode"] = "found"
        monkeypatch.delenv("LOOPX_AUTHORITY_CREDENTIAL")
        monkeypatch.setenv("LOOPX_REMOTE_AUTHORITY_CREDENTIAL", CREDENTIAL)
        markdown = _inspect(registry, runtime)
        assert markdown.returncode == 0, markdown.stderr
        assert "Execution admitted: no." in markdown.stdout
        assert CREDENTIAL not in markdown.stdout + markdown.stderr


@pytest.mark.parametrize(("mutation", "reason"), [
    ("unknown-goal", "goal_unregistered"), ("unknown-actor", "actor_unregistered"),
    ("other-goal", "disabled"), ("disabled", "disabled"), ("missing-binding", "binding_incomplete"),
    ("actor-mismatch", "actor_mismatch"), ("no-credential", "credential_required"),
    ("short-credential", "credential_invalid"), ("endpoint", "endpoint_invalid"),
    ("ca", "ca_invalid"), ("registry", "registry_unreadable"),
])
def test_inspect_configuration_errors_never_contact_remote(tmp_path, monkeypatch, mutation, reason):
    registry, runtime, _ = write_cli_fixture(tmp_path, scoped_agents=True)
    monkeypatch.setenv("LOOPX_AUTHORITY_CREDENTIAL", CREDENTIAL)
    monkeypatch.delenv("LOOPX_REMOTE_AUTHORITY_CREDENTIAL", raising=False)
    with remote_authority_service(tmp_path, SCOPED_AGENT_ID) as (cfg, control):
        if mutation == "disabled":
            cfg["enabled"] = False
        if mutation == "missing-binding":
            del cfg["tenant_id"]
        if mutation == "actor-mismatch":
            cfg["actor_id"] = "other-registered-actor"
        if mutation == "no-credential":
            monkeypatch.delenv("LOOPX_AUTHORITY_CREDENTIAL")
        if mutation == "short-credential":
            monkeypatch.setenv("LOOPX_AUTHORITY_CREDENTIAL", "short-secret")
        if mutation == "endpoint":
            cfg["endpoint"] += "/private-path?secret=value"
        if mutation == "ca":
            cfg["ca_file"] = "private-relative-path.pem"
        bind_remote_authority(registry, "half-speed" if mutation == "other-goal" else "full-speed", cfg)
        if mutation == "registry":
            registry.write_text("invalid-registry-private-content", encoding="utf-8")
        result = _inspect(
            registry, runtime, "--format", "json",
            goal="absent-goal" if mutation == "unknown-goal" else "full-speed",
            actor="unregistered-actor" if mutation == "unknown-actor" else SCOPED_AGENT_ID,
        )
        assert result.returncode == 1, result.stderr
        assert json.loads(result.stdout)["reason_code"] == f"remote_authority_{reason}"
        assert control["requests"] == []
        for private in (CREDENTIAL, "short-secret", "private-path", "private-content", str(registry)):
            assert private not in result.stdout + result.stderr


def test_inspect_runtime_error_does_not_emit_exception_details(tmp_path, monkeypatch):
    from loopx.control_plane.coordination.remote_authority_gateway import inspect_remote_authority
    from loopx.control_plane.effect_runtime import EffectRuntimeStartupError

    registry, _, _ = write_cli_fixture(tmp_path, scoped_agents=True)

    def missing_node():
        raise EffectRuntimeStartupError("private-host-detail", diagnostic_code="node_unavailable")

    monkeypatch.setattr("loopx.control_plane.effect_runtime._node_executable", missing_node)
    packet = inspect_remote_authority(registry, "full-speed", SCOPED_AGENT_ID)
    assert packet["reason_code"] == "remote_authority_node_unavailable"
    assert packet["execution_admitted"] is False
    assert "private-host-detail" not in json.dumps(packet)


def test_inspect_full_parser_and_format_positions_are_discoverable():
    from loopx.cli import build_parser
    from loopx.cli_runtime import output_format
    from loopx.help_surface import COMMAND_GROUPS

    for prefix, middle, suffix in ((["--format", "json"], [], []),
                                   ([], ["--format", "json"], []), ([], [], ["--format", "json"])):
        args = build_parser().parse_args([
            *prefix, "remote-authority", *middle, "inspect", "--goal-id", "goal", "--agent-id", "actor", *suffix,
        ])
        assert output_format(args, "inspection_format") == "json"
    assert "remote-authority inspect" in json.dumps(COMMAND_GROUPS)
