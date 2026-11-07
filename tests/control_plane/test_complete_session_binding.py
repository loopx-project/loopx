"""Complete-source session lookup uses the existing identity and session owners."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from loopx.control_plane.turn_driver import codex_sessions
from loopx.control_plane.turn_driver.driver import turn_host_lineage
from tests.control_plane.test_turn_host_decision_capture import (
    complete_host_request as complete_host_request,
)


@pytest.mark.parametrize("host", ["codex-cli", "dsh"])
@pytest.mark.parametrize("scope", ["todo", "agent"])
def test_source_only_lookup_reuses_the_original_native_session(
    complete_host_request, tmp_path, host, scope, monkeypatch,
):
    request, contract = complete_host_request
    runtime = tmp_path / "session-runtime"
    lineage = turn_host_lineage(request)
    codex_sessions._store_codex_cli_session(runtime, lineage=lineage, session_scope=scope,
        host=host, session_id="original-session")
    expected = {"schema_version": "loopx_turn_session_binding_v0", **lineage}
    assert codex_sessions.codex_cli_session_binding(runtime, request["turn_envelope"],
        work_contract=contract, session_scope=scope, host=host) == expected
    before = {p: p.read_bytes() for p in runtime.rglob("*.json")}
    from loopx.control_plane.quota import turn_envelope
    invoke = turn_envelope._turn_envelope_result
    observed = []

    def record(operation, **kwargs):
        observed.append((operation, kwargs))
        return invoke(operation, **kwargs)

    monkeypatch.setattr(turn_envelope, "_turn_envelope_result", record)
    for _ in range(2):
        assert codex_sessions.codex_cli_session_binding(runtime, work_contract=contract,
            session_scope=scope, host=host) == expected
    assert len(observed) == 2
    assert all(operation == "complete_host_lineage" and set(options) == {"payload"}
               for operation, options in observed)
    assert codex_sessions.select_codex_cli_session(runtime, lineage=lineage,
        session_scope=scope, host=host)["session_id"] == "original-session"
    assert {p: p.read_bytes() for p in before} == before


@pytest.mark.parametrize("replacement", [None, False, {}, "invalid", "corrupted"])
def test_source_only_lookup_refuses_bad_source_without_a_legacy_fallback(
    complete_host_request, tmp_path, replacement,
):
    request, contract = complete_host_request
    runtime = tmp_path / "session-runtime"
    lineage = turn_host_lineage(request)
    codex_sessions._store_codex_cli_session(runtime, lineage=lineage,
        session_id="original-session")
    damaged = deepcopy(contract)
    damaged["reason"] = "changed after admission"
    value = damaged if replacement == "corrupted" else replacement
    before = {p: p.read_bytes() for p in runtime.rglob("*.json")}
    with pytest.raises((ValueError, RuntimeError, TypeError)):
        codex_sessions.codex_cli_session_binding(runtime, work_contract=value)
    assert {p: p.read_bytes() for p in before} == before
    # The existing historical API and its exact session namespace still work.
    assert codex_sessions.codex_cli_session_binding(runtime,
        request["turn_envelope"])["todo_id"] == lineage["todo_id"]


@pytest.mark.parametrize("adapter", [None, {}, False, "wrong-goal"])
def test_explicit_adapter_still_has_to_match_the_complete_source(complete_host_request, tmp_path, adapter):
    request, contract = complete_host_request
    invalid = deepcopy(request["turn_envelope"])
    invalid["goal_id"] = "other-goal"
    if adapter != "wrong-goal":
        invalid = adapter
    before = json.dumps(request, ensure_ascii=False, sort_keys=True)
    with pytest.raises((ValueError, RuntimeError, TypeError)):
        codex_sessions.codex_cli_session_binding(tmp_path, invalid, work_contract=contract)
    assert json.dumps(request, ensure_ascii=False, sort_keys=True) == before


def test_real_cli_session_lookup_uses_only_current_complete_source(
    complete_host_request, monkeypatch, capsys,
):
    from loopx.cli import main
    from loopx.control_plane import turn_driver
    from loopx.control_plane.turn_driver import codex_cli

    request, contract = complete_host_request
    runtime = Path(contract["runtime_root"])
    lineage = turn_host_lineage(request)
    codex_sessions._store_codex_cli_session(runtime, lineage=lineage,
        session_id="original-session", goal_ref=request.get("goal_ref"))
    before = {}
    for path in runtime.rglob("*.json"):
        value = json.loads(path.read_text())
        if isinstance(value, dict) and value.get("schema_version") == "loopx_codex_cli_session_v1":
            before[path] = path.read_bytes()
    assert len(before) == 1
    registry = json.loads(Path(contract["registry"]).read_text())
    goal = next(row for row in registry["goals"] if row.get("id", row.get("goal_id")) == lineage["goal_id"])
    # Resolve the lazy export before patching its owner, so later recovery
    # consumers cannot retain this test's transport probe on first import.
    original = turn_driver.codex_cli_session_binding
    assert original is codex_cli.codex_cli_session_binding
    observed = []
    bindings = []

    def lookup(*args, **kwargs):
        observed.append((args, kwargs))
        assert len(args) == 1 and "turn_envelope" not in kwargs
        binding = original(*args, **kwargs)
        bindings.append(binding)
        return binding

    monkeypatch.setattr(codex_cli, "codex_cli_session_binding", lookup)
    code = main(["--registry", contract["registry"], "--runtime-root", str(runtime),
        "--format", "json", "turn", "run-once", "--goal-id", lineage["goal_id"],
        "--agent-id", lineage["agent_id"], "--host", "codex-cli",
        "--project", goal["repo"],
        "--session-scope", "todo",
        "--execution-mode", "isolated-headless", "--iteration-context", "resume"])
    result = json.loads(capsys.readouterr().out)
    assert code == 0 and result["ok"], result
    assert turn_driver.codex_cli_session_binding is original
    assert len(observed) == 1
    assert observed[0][1]["work_contract"]["goal_id"] == lineage["goal_id"]
    assert bindings == [{"schema_version": "loopx_turn_session_binding_v0", **lineage}]
    assert result["dry_run"] and result["status"] == "preview"
    assert {p: p.read_bytes() for p in before} == before
