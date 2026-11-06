"""Tuple-returning CLI transport preserves producer JSON, including unknown fields."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
from types import SimpleNamespace

import pytest

from loopx.control_plane.testing import canary_harness


@pytest.mark.parametrize("parent_setting", ["on", "off"])
def test_machine_cli_child_is_inline_without_changing_parent_or_raw_helper(monkeypatch, tmp_path, parent_setting):
    monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY", parent_setting)
    observed = []
    def run(*args, **kwargs):
        observed.append(kwargs.get("env"))
        return SimpleNamespace(stdout='{"ok":true,"unknown":null}', stderr="", returncode=0)
    monkeypatch.setattr(canary_harness.subprocess, "run", run)
    assert canary_harness.run_json_cli("probe", registry_path=tmp_path / "registry.json")["ok"] is True
    assert observed[0]["LOOPX_CONTEXT_DELIVERY"] == "off"
    canary_harness.run_json_cli_result("probe", registry_path=tmp_path / "registry.json")
    assert observed[1] is None  # Raw-wire callers retain their own transport.
    assert os.environ["LOOPX_CONTEXT_DELIVERY"] == parent_setting


@pytest.mark.parametrize("parent_setting", ["on", "off"])
def test_real_peer_guard_and_successor_remain_machine_decisions(monkeypatch, parent_setting):
    monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY", parent_setting)
    path = Path(__file__).resolve().parents[2] / "examples/control_plane/peer-agent-continuation-state-machine-smoke.py"
    fixture = runpy.run_path(str(path))
    fixture["assert_same_peer_continuation"]()
    fixture["assert_review_is_action_semantics_over_generic_handoff"]()
    assert os.environ["LOOPX_CONTEXT_DELIVERY"] == parent_setting


@pytest.mark.parametrize("parent_setting", ["on", "off"])
@pytest.mark.parametrize("gate", ["user", "quota"])
def test_inline_machine_transport_preserves_real_core_gates(monkeypatch, tmp_path, parent_setting, gate):
    monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY", parent_setting)
    original = canary_harness.write_fixture_registry
    def write_registry(**kwargs):
        if gate == "quota":
            kwargs["quota_allowed_slots"] = 0
        registry = original(**kwargs)
        if gate == "user":
            state = canary_harness.project_state_path(kwargs["project"], kwargs["goal_id"])
            state.write_text(state.read_text() + "\n## User Todo\n\n- [ ] [P0] Confirm this fixture's boundary.\n"
                "  <!-- loopx:todo todo_id=todo_owner_gate status=open task_class=user_gate "
                "blocks_agent=codex-alpha -->\n")
        return registry
    monkeypatch.setattr(canary_harness, "write_fixture_registry", write_registry)
    path = Path(__file__).resolve().parents[2] / "examples/control_plane/peer-agent-continuation-state-machine-smoke.py"
    fixture = runpy.run_path(str(path))
    project, runtime, registry = fixture["write_fixture"](tmp_path)
    result = fixture["should_run"](project, runtime, registry, fixture["PEER_ALPHA"])
    assert result["should_run"] is False
    if gate == "quota":
        assert result["quota"]["allowed_slots"] == 0
    else:
        assert result["requires_user_action"] is True
    assert os.environ["LOOPX_CONTEXT_DELIVERY"] == parent_setting


@pytest.mark.parametrize("exit_code", [0, 7])
@pytest.mark.parametrize("producer_field", [False, True])
def test_result_transport_does_not_inject_or_overwrite_source_fields(tmp_path, monkeypatch, exit_code, producer_field):
    source = {"number": 1.0, "unknown": {"allowed": False, "evidence": None}}
    if producer_field:
        source["_returncode"] = {"producer_fact": "Preserve this exact value."}
    raw = json.dumps(source, ensure_ascii=False)
    monkeypatch.setattr(canary_harness.subprocess, "run", lambda *args, **kwargs:
        SimpleNamespace(stdout=raw, stderr="", returncode=exit_code))
    status, observed = canary_harness.run_json_cli_result("probe", registry_path=tmp_path / "absent.json")
    assert status == exit_code
    assert json.dumps(observed, ensure_ascii=False) == raw


@pytest.mark.parametrize("valid_digest", [False, True])
def test_real_capture_cli_returns_the_same_wire_through_the_helper(tmp_path, monkeypatch, valid_digest):
    monkeypatch.setenv("LOOPX_USAGE_PING", "0")
    capture = tmp_path / "source.json"
    raw = b'{"goal":"Keep the original source and stop before publication.","value":1.0}'
    capture.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest() if valid_digest else "0" * 64
    registry, runtime = tmp_path / "absent-registry.json", tmp_path / "absent-runtime"
    arguments = ["quota", "context-read", "--capture-file", str(capture), "--capture-sha256", digest]
    direct = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(registry),
        "--runtime-root", str(runtime), "--format", "json", *arguments],
        cwd=tmp_path, capture_output=True, text=True, check=False)
    status, observed = canary_harness.run_json_cli_result(*arguments,
        registry_path=registry, runtime_root=runtime, cwd=tmp_path)
    assert status == direct.returncode == (0 if valid_digest else 1)
    assert observed == json.loads(direct.stdout)
    assert capture.read_bytes() == raw
    assert not registry.exists() and not runtime.exists()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("gate", ["ready", "user_gate", "zero_quota"])
@pytest.mark.parametrize("parent_mode", ["on", "off"])
@pytest.mark.parametrize("consumer", ["autonomous", "autonomous_subprocess", "shared_runtime"])
def test_machine_smoke_calls_keep_core_gates_and_parent_context_mode(
    tmp_path, monkeypatch, provider, gate, parent_mode, consumer,
):
    from tests.control_plane import test_quota_settlement_cli as fixture

    project, runtime, registry = fixture._write_fixture(tmp_path)
    if gate == "user_gate":
        fixture._append_blocking_user_gate(project)
    elif gate == "zero_quota":
        source = json.loads(registry.read_text())
        source["goals"][0]["quota"]["compute"] = 0
        source["goals"][0]["quota"].pop("allowed_slots")
        registry.write_text(json.dumps(source))
    canary_harness.import_fixture_authority(registry, fixture.GOAL_ID, provider=provider)
    monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY", parent_mode)
    root = Path(__file__).resolve().parents[2]
    arguments = ("quota", "should-run", "--goal-id", fixture.GOAL_ID,
                 "--agent-id", fixture.AGENT_ID)
    if consumer == "shared_runtime":
        smoke = runpy.run_path(str(root / "examples/control_plane/refresh-state-shared-runtime-projection-smoke.py"))
        observed = smoke["run_cli"]("--registry", str(registry), "--runtime-root", str(runtime),
                                    *arguments, cwd=project, shared_runtime=runtime)
    else:
        smoke = runpy.run_path(str(root / "examples/autonomous-replan-obligation-smoke.py"))
        smoke["run_cli"].__globals__["USE_SUBPROCESS_CLI"] = consumer == "autonomous_subprocess"
        observed = smoke["run_cli"](*arguments, registry_path=registry, runtime=runtime)
    assert observed["normal_delivery_allowed"] is (gate == "ready"), observed
    assert os.environ["LOOPX_CONTEXT_DELIVERY"] == parent_mode
