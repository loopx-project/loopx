"""A binding inspection must use the actual Turn without launching or spending."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from loopx.control_plane.turn_driver import build_loopx_turn_plan
from loopx.control_plane.turn_driver.executor import (
    LOOPX_TURN_JOURNAL_SCHEMA_VERSION,
)
from loopx.control_plane.turn_driver.journal_store import turn_journal_path
from test_delegation_cli import cli
from test_local_delegation import service as delegation_service

service = delegation_service


def test_real_cli_preflight_preserves_unknown_runtime_and_state(service):
    root, runner = service
    before = runner.registry.read_bytes()
    status, result = cli(runner, "inspect", "--binding-id", "analysis")
    assert status == 0, result
    assert result["state"] == "runtime_unverified"
    assert result["turn_eligible"] and result["acceptance_ready"]
    assert result["executor"]["host"] == "generic-cli"
    assert result["executor"]["available"] is None
    assert result["executor"]["runtime_probe"] is None
    assert result["executor"]["unavailable_remediation"] == []
    assert not any(result["effects"].values())
    assert runner.registry.read_bytes() == before
    assert not (root / "host-started").exists()
    assert not list(runner.path("inventory").parent.glob("*.json"))
    assert not list((root / "runtime" / "goals").glob("*/turns/*.json"))


def test_real_cli_dsh_preflight_preserves_interpreter_probe_without_launch(service):
    root, runner = service
    config = json.loads(runner.config.read_text())
    config["bindings"][0]["host_args"] = ["--host", "dsh"]
    runner.config.write_text(json.dumps(config))
    before = runner.registry.read_bytes()
    status, result = cli(runner, "inspect", "--binding-id", "analysis")
    assert status == 0, result
    executor = result["executor"]
    probe = executor["runtime_probe"]
    assert probe["schema_version"] == "managed_runtime_probe_v0"
    assert probe["scope"] == "probing_interpreter"
    assert probe["module"] == "deepseek_harness"
    assert isinstance(probe["available"], bool)
    if not probe["available"]:
        assert executor["available"] is False
        assert executor["reason"] == "dsh_runtime_unavailable"
        assert executor["unavailable_remediation"] == ["configure_dsh_runtime", "select_individual_host"]
    assert "credential_env" not in executor and "endpoint_env" not in executor
    assert not any(result["effects"].values())
    assert runner.registry.read_bytes() == before
    assert not (root / "host-started").exists()
    assert not list(runner.path("inventory").parent.glob("*.json"))
    assert not list((root / "runtime" / "goals").glob("*/turns/*.json"))


def test_preflight_rejects_execution_and_retargeting_before_subprocess(
    service, monkeypatch
):
    _, runner = service
    config = json.loads(runner.config.read_text())
    original = list(config["bindings"][0]["host_args"])
    calls = []
    monkeypatch.setattr(runner, "_cli", lambda *args: calls.append(args))
    for flags in (
        ["--execute"],
        ["--exec"],
        ["--todo-id", "other"],
        ["--agent-id", "other"],
        ["--project", "other"],
        ["--scan-root", "other"],
        ["--resume-turn-key", "other"],
    ):
        config["bindings"][0]["host_args"] = [*original, *flags]
        runner.config.write_text(json.dumps(config))
        with pytest.raises(ValueError):
            runner.inspect("analysis")
    assert calls == []


def test_preflight_scope_and_incompatible_cli_arguments(service):
    _, runner = service
    for arguments in [
        (),
        ("--binding-id", "analysis", "--execute"),
        ("--binding-id", "analysis", "--operation-id", "wrong"),
        ("--binding-id", "analysis", "--limit", "2"),
    ]:
        status, result = cli(runner, "inspect", *arguments)
        assert status == 1 and not result["ok"]
    status, result = cli(
        runner, "inspect", "--binding-id", "analysis", actor="reviewer"
    )
    assert status == 1 and not result["ok"]


def test_preflight_surfaces_actual_turn_rejection_without_launch(service):
    root, runner = service
    config = json.loads(runner.config.read_text())
    config["bindings"][0]["host_args"] = [
        "--host", "generic-cli", "--host-command-json", "[]",
    ]
    runner.config.write_text(json.dumps(config))
    status, result = cli(runner, "inspect", "--binding-id", "analysis")
    assert status == 1
    assert "delegation Turn preflight unavailable" in result["error"]
    assert not (root / "host-started").exists()


def test_preflight_projects_unavailable_authority_without_turn_or_provider(
    service, monkeypatch
):
    from loopx import collaboration_mcp as delegation

    root, runner = service
    calls = []

    def unavailable(**_kwargs):
        raise ValueError(
            "Goal acceptance requires an existing canonical authority; "
            "activation never promotes a provider"
        )

    monkeypatch.setattr(delegation, "inspect_goal_acceptance", unavailable)
    monkeypatch.setattr(runner, "_cli", lambda *args, **kwargs: calls.append(args))
    result = runner.inspect("analysis")
    assert result["state"] == "authority_unavailable"
    assert result["authority_ready"] is False
    assert result["authority_state"] == "unavailable"
    assert result["authority_next_action"] == "repair_canonical_authority"
    assert result["promotion_from_surface_allowed"] is False
    assert "canonical authority" in result["authority_reason"]
    assert not any(result["effects"].values())
    assert calls == []
    assert not (root / "host-started").exists()


def test_dispatch_preserves_run_once_rejection_before_host_launch(
    service, monkeypatch
):
    root, runner = service
    monkeypatch.setattr(runner, "_spawn", lambda _: None)
    runner.start("analysis", "rejected-plan", {
        "schema_version": "collaboration_brief_v0",
        "purpose": "Exercise a rejected Turn plan",
        "context": "The provider must remain unstarted.",
        "constraints": ["No external actions"],
        "inputs": [],
        "acceptance": ["Preserve the canonical rejection"],
        "return_requirement": "Return no model result",
    })
    calls = []

    def rejected_turn(_binding, *args, **_kwargs):
        calls.append(args)
        return {
            "ok": False,
            "schema_version": "loopx_turn_execution_v0",
            "mode": "run_once",
            "error": "Requested Turn Todo is not accepted by canonical authority",
            "effects": {"host_invoked": False, "state_written": False,
                        "scheduler_acknowledged": False, "quota_spent": False},
        }

    monkeypatch.setattr(runner, "_cli", rejected_turn)
    runner.execute("rejected-plan")
    result = runner.read("rejected-plan")
    assert result["status"] == "rejected"
    assert result["error"] == (
        "Requested Turn Todo is not accepted by canonical authority"
    )
    assert len(calls) == 1 and calls[0][:2] == ("turn", "run-once")
    assert "--execute" in calls[0]
    assert "turn_key" not in result
    assert not (root / "host-started").exists()


def test_hard_lease_delegation_claims_before_host_launch(service, monkeypatch):
    from loopx import collaboration_mcp as delegation

    _, runner = service
    monkeypatch.setattr(runner, "_spawn", lambda _: None)
    monkeypatch.setattr(
        delegation,
        "show_goal_handoff_mode",
        lambda **_kwargs: {"handoff_mode": "hard_lease"},
    )
    runner.start("analysis", "leased-dispatch", brief={
        "schema_version": "collaboration_brief_v0",
        "purpose": "Exercise an atomic hard-lease dispatch",
        "context": "The lease must precede the managed host.",
        "constraints": ["No external actions"],
        "inputs": [],
        "acceptance": ["Preserve canonical lease identity"],
        "return_requirement": "Return no model result",
    })
    calls = []

    def canonical(_binding, *args, **_kwargs):
        calls.append(args)
        if args[:2] == ("todo", "claim"):
            key = args[args.index("--task-lease-idempotency-key") + 1]
            return {
                "ok": True,
                "lease": {
                    "owner": "analyst",
                    "idempotency_key": key,
                    "status": "active",
                    "version": 7,
                },
            }
        return {
            "ok": False,
            "schema_version": "loopx_turn_execution_v0",
            "mode": "run_once",
            "error": "stop after lease evidence",
            "effects": {
                "host_invoked": False,
                "state_written": False,
                "scheduler_acknowledged": False,
                "quota_spent": False,
            },
        }

    monkeypatch.setattr(runner, "_cli", canonical)
    runner.execute("leased-dispatch")
    result = runner.read("leased-dispatch")
    row = json.loads(runner.path("leased-dispatch").read_text())

    assert [call[:2] for call in calls] == [("todo", "claim"), ("turn", "run-once")]
    assert row["task_lease"] == {
        "required": True,
        "handoff_mode": "hard_lease",
        "idempotency_key": row["turn_instance_id"],
        "version": 7,
    }
    assert result["status"] == "rejected"
    assert result["error"] == "stop after lease evidence"


def test_exact_validated_turn_can_reopen_a_false_terminal_observation(
    service, monkeypatch
):
    _, runner = service
    monkeypatch.setattr(runner, "_spawn", lambda _: None)
    runner.start("analysis", "recover-settlement", {
        "schema_version": "collaboration_brief_v0",
        "purpose": "Recover one validated settlement",
        "context": "The model result and independent validation already exist.",
        "constraints": ["Never rerun model work"],
        "inputs": [],
        "acceptance": ["Resume only the exact Turn"],
        "return_requirement": "Return the validated artifact",
    })
    path = runner.path("recover-settlement")
    row = json.loads(path.read_text())
    turn_instance_id = "delegation-" + row["identity"]["request_id"][:32]
    row.update(
        status="rejected",
        turn_instance_id=turn_instance_id,
        error="legacy false terminal observation",
    )
    path.write_text(json.dumps(row))
    plan = build_loopx_turn_plan(
        {
            "ok": True,
            "schema_version": "loopx_turn_envelope_v0",
            "goal_id": runner.goal_id,
            "agent_id": "analyst",
            "should_run": True,
            "effective_action": "normal_run",
            "action": {
                "must_attempt": True,
                "delivery_allowed": True,
                "quiet_noop_allowed": False,
                "selected_todo": {"todo_id": "todo_analyst-initial"},
            },
            "user": {"action_required": False, "open_count": 0},
            "writeback": {"spend_after_validation": True},
            "scheduler": {"action": "run_now"},
            "action_signature": {
                "matches": True,
                "source_hash": "sha256:fixture",
                "envelope_hash": "sha256:fixture",
            },
            "compaction": {"within_budget": True},
        },
        host="generic-cli",
        execution_mode="isolated-headless",
        turn_instance_id=turn_instance_id,
        iteration_context_policy="fresh",
    )
    transaction = plan["transaction"]
    turn_key = transaction["turn_key"]
    journal_path = turn_journal_path(
        runner.root, goal_id=runner.goal_id, turn_key=turn_key
    )
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    host_result = {
        "schema_version": "loopx_turn_result_v0",
        "turn_key": turn_key,
        "result_kind": "validated_progress",
        "completed_phases": ["host_execute", "typed_result"],
    }
    journal_path.write_text(json.dumps({
        "schema_version": LOOPX_TURN_JOURNAL_SCHEMA_VERSION,
        "goal_id": runner.goal_id,
        "turn_key": turn_key,
        "status": "in_progress",
        "result_kind": "validated_progress",
        "completed_phases": ["host_execute", "typed_result", "validation"],
        "plan": plan,
        "host_result": host_result,
        "task_validation": {"ok": True, "status": "passed"},
    }))

    binding = runner.binding("analysis", require_active=True)
    assert runner._recover_validated_settlement(path, row, binding) is True
    recovered = json.loads(path.read_text())
    assert recovered["status"] == "turn_returned"
    assert recovered["turn_key"] == turn_key
    assert recovered["turn_result"]["resume_turn_key"] == turn_key


def test_selected_dsh_profile_is_not_replaced_by_the_default(service):
    root, runner = service
    config = json.loads(runner.config.read_text())
    config["bindings"][0]["host_args"] = [
        "--host",
        "dsh",
        "--dsh-provider",
        "openai",
        "--dsh-model",
        "synthetic-model",
        "--dsh-reasoning-effort",
        "high",
    ]
    runner.config.write_text(json.dumps(config))
    status, result = cli(runner, "inspect", "--binding-id", "analysis")
    assert status == 0, result
    assert result["executor"]["host"] == "dsh"
    assert "synthetic-model" in result["executor"]["profile"]
    assert "high" in result["executor"]["profile"]
    assert result["state"] in {"launchable", "runtime_unavailable"}
    assert not any(result["effects"].values())
    assert not (root / "host-started").exists()


def test_selected_codex_managed_agent_profile_is_projected_exactly(service):
    root, runner = service
    config = json.loads(runner.config.read_text())
    config["bindings"][0]["host_args"] = [
        "--host",
        "codex-cli",
        "--codex-model",
        "gpt-5.6-sol",
        "--codex-reasoning-effort",
        "xhigh",
    ]
    runner.config.write_text(json.dumps(config))

    status, result = cli(runner, "inspect", "--binding-id", "analysis")

    assert status == 0, result
    assert result["executor"] == {
        "host": "codex-cli",
        "available": None,
        "reason": None,
        "profile": "gpt-5.6-sol@xhigh",
        "runtime_probe": None,
        "unavailable_remediation": [],
    }
    assert result["state"] == "runtime_unverified"
    assert not any(result["effects"].values())
    assert not (root / "host-started").exists()

    binding = runner.binding("analysis", require_active=True)
    execution = runner._execution_arguments(binding, "native-tool-inspection")
    encoded = execution[execution.index("--codex-mcp-server-json") + 1]
    native = json.loads(encoded)
    assert native["schema_version"] == "codex_stdio_mcp_server_v0"
    assert native["name"] == "loopx_delegation"
    command = native["command"]
    assert command[:3] == [sys.executable, "-P", "-c"]
    assert "loopx.collaboration_mcp" in command
    assert str(Path(__file__).resolve().parents[1]) in command
    assert command[command.index("--agent-id") + 1] == "analyst"
    assert command[command.index("--workspace") + 1] == binding["workspace"]
    assert command[command.index("--execution-config") + 1] == str(runner.config)
    assert "lead" not in command

    validator = json.loads(execution[execution.index("--validation-command-json") + 1])
    assert validator[:3] == [sys.executable, "-P", "-c"]
    assert "loopx.collaboration_mcp" in validator
    assert str(Path(__file__).resolve().parents[1]) in validator

    shadow = Path(binding["workspace"]) / "loopx"
    shadow.mkdir()
    (shadow / "__init__.py").write_text("", encoding="utf-8")
    (shadow / "collaboration_mcp.py").write_text(
        "raise RuntimeError('stale workspace MCP must not be imported')\n",
        encoding="utf-8",
    )
    clean_environment = os.environ.copy()
    clean_environment.pop("PYTHONPATH", None)
    completed = subprocess.run(
        command,
        cwd=binding["workspace"],
        input="",
        text=True,
        capture_output=True,
        env=clean_environment,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr
    assert "stale workspace MCP" not in completed.stderr


def test_preflight_ignores_stale_loopx_checkout_in_worker_workspace(service):
    root, runner = service
    workspace = Path(runner.binding("analysis", require_active=True)["workspace"])
    shadow = workspace / "loopx"
    shadow.mkdir()
    (shadow / "__init__.py").write_text("", encoding="utf-8")
    (shadow / "cli.py").write_text(
        "raise RuntimeError('stale workspace LoopX must not be imported')\n",
        encoding="utf-8",
    )

    status, result = cli(runner, "inspect", "--binding-id", "analysis")

    assert status == 0, result
    assert result["turn_eligible"] is True
    assert not any(result["effects"].values())


def test_preflight_does_not_call_an_invalidated_acceptance_ready(service):
    root, runner = service
    from loopx.agent_registry import load_goal_from_registry
    from pathlib import Path

    workspace = Path(load_goal_from_registry(runner.registry, runner.goal_id)["repo"])
    pin = workspace / "validation" / "acceptance.py"
    pin.write_text(pin.read_text() + "\n# revised validator\n")
    status, result = cli(runner, "inspect", "--binding-id", "analysis")
    assert status == 0, result
    assert not result["acceptance_ready"]
    assert result["state"] in {"turn_blocked", "acceptance_unavailable"}


def test_http_team_readback_uses_original_scope_without_a_new_turn(service):
    import http.client
    import threading
    from loopx.chat_runtime import ChatRuntimeController
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
    from loopx.chat_store import ChatSessionStore

    root, runner = service
    from loopx.agent_registry import load_goal_from_registry
    from pathlib import Path

    workspace = Path(load_goal_from_registry(runner.registry, runner.goal_id)["repo"])
    config = workspace / ".loopx" / "config" / "delegations.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_bytes(runner.config.read_bytes())
    registry_payload = json.loads(runner.registry.read_text())
    goal = next(
        item for item in registry_payload["goals"] if item["id"] == runner.goal_id
    )
    goal.setdefault("spawn_policy", {})["execution_config"] = (
        ".loopx/config/delegations.json"
    )
    runner.registry.write_text(json.dumps(registry_payload))
    store = ChatSessionStore(root / "runtime")
    controller = ChatRuntimeController(
        store=store, codex_bin="codex", registry_path=runner.registry
    )
    session = store.create_session(
        goal_id=runner.goal_id,
        agent_id="codex",
        channel_id="goal." + runner.goal_id,
        upstream_thread_id="fixture",
        upstream_mode="chat",
        adapter_kind="codex_app_server",
    )
    # Existing settings owner, with no native Goal or new executor.
    controller.loopx_mode.apply(
        session["session_id"],
        {
            "operation": "configure",
            "settings": {
                "agent_id": "lead",
                "token_budget": 1000,
            },
        },
        work_dir=root,
        objective="Inspect existing work",
    )
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.verbose = False
    server.registry_path, server.chat_store, server.runtime_controller = (
        runner.registry,
        store,
        controller,
    )
    server.runtime_root_override, server.scan_roots, server.limit = (
        str(runner.root),
        [],
        20,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for body, expected in [
            ({"operation": "inspect", "binding_id": "analysis"}, 200),
            ({"operation": "operations"}, 200),
            ({"operation": "operations", "agent_id": "other"}, 409),
        ]:
            conn = http.client.HTTPConnection(
                "127.0.0.1", server.server_port, timeout=45
            )
            conn.request(
                "POST",
                f"/api/chat/sessions/{session['session_id']}/loopx",
                json.dumps(body),
                {
                    "Content-Type": "application/json",
                    "Origin": f"http://127.0.0.1:{server.server_port}",
                },
            )
            response = conn.getresponse()
            result = json.loads(response.read())
            conn.close()
            assert response.status == expected, result
            if body["operation"] == "inspect":
                assert result["state"] == "runtime_unverified"
        assert store.load_session(session["session_id"]).get("active_turn_id") is None
        assert not (root / "host-started").exists()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        controller.close()
