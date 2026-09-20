"""A binding inspection must use the actual Turn without launching or spending."""

import json

import pytest

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
    assert "canonical authority" in result["authority_reason"]
    assert not any(result["effects"].values())
    assert calls == []
    assert not (root / "host-started").exists()


def test_dispatch_preserves_turn_plan_rejection_before_transaction_read(
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

    def rejected_plan(_binding, *args, **_kwargs):
        calls.append(args)
        return {
            "ok": False,
            "schema_version": "loopx_turn_plan_v0",
            "mode": "plan",
            "error": "Requested Turn Todo is not accepted by canonical authority",
            "effects": {"host_invoked": False, "state_written": False,
                        "scheduler_acknowledged": False, "quota_spent": False},
        }

    monkeypatch.setattr(runner, "_cli", rejected_plan)
    runner.execute("rejected-plan")
    result = runner.read("rejected-plan")
    assert result["status"] == "rejected"
    assert result["error"] == (
        "delegation Turn plan rejected: "
        "Requested Turn Todo is not accepted by canonical authority"
    )
    assert len(calls) == 1 and calls[0][:2] == ("turn", "plan")
    assert "turn_key" not in result
    assert not (root / "host-started").exists()


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
    }
    assert result["state"] == "runtime_unverified"
    assert not any(result["effects"].values())
    assert not (root / "host-started").exists()


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
