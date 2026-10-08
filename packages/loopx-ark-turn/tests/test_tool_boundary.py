"""Sandbox policy through the public SDK wire, including actual call-ID shape."""
import asyncio
import copy
from dataclasses import replace
import json

import pytest

from loopx_ark_turn.config import AdapterError
from loopx_ark_turn.host import config_digest
from loopx_ark_turn.receipt import Receipt
from loopx_ark_turn.tool_boundary import NETWORK
from test_host import Provider, config, event, execute, request, tool_event


def builtin_use(**changes):
    return event("call-public", "agent.tool_use", session_thread_id="thread-root",
                 tool_use_id="", name="bash", input={"command": "printf 42"},
                 evaluated_permission="allow") | changes


def builtin_result(**changes):
    return event("result-public", "agent.tool_result", session_thread_id="thread-root",
                 tool_use_id="call-public", content=[{"type": "text", "text": "42"}], is_error=False) | changes


def state(cfg):
    return json.loads(Receipt(cfg.state_dir, request()["turn_key"]).path.read_text())


def test_sandbox_bash_and_local_custom_tool_coexist_with_exact_cleanup(tmp_path):
    cfg = replace(config(tmp_path), sandbox_builtins=True)
    provider = Provider([builtin_use(), builtin_result(), tool_event()])
    original = json.loads(json.dumps(provider.environment_snapshot))
    result = asyncio.run(execute(provider, cfg))
    assert result["result_kind"] == "validated_progress"
    assert provider.environment_snapshot == original
    assert not any(method != "GET" and path.startswith("/environments") for method, path, _ in provider.calls)
    creation = next(body for method, path, body in provider.calls if method == "POST" and path == "/sessions")
    assert "environment_id" not in creation
    assert creation["environment"]["config"]["networking"] == NETWORK
    assert creation["resources"] == creation["vault_ids"] == []
    row = state(cfg)
    assert row["builtin_tools"]["call-public"]["name"] == "bash"
    assert len(row["builtin_tools"]["call-public"]["result_digest"]) == 64
    assert "printf" not in json.dumps(row)
    assert row["cleanup"] == {"session": "absent", "agent": "absent"}
    assert row["provider_usage"]["output_tokens"] == 20
    before = len(provider.calls)
    assert asyncio.run(execute(provider, cfg)) == result
    assert len(provider.calls) == before
    with pytest.raises(AdapterError, match="binding_mismatch"):
        asyncio.run(execute(provider, replace(cfg, sandbox_builtins=False)))
    assert len(provider.calls) == before


@pytest.mark.parametrize("enabled,events,error", [
    (False, [builtin_use()], "builtin_tools_not_enabled"),
    (False, [builtin_result()], "builtin_tools_not_enabled"),
    (True, [builtin_use(session_thread_id="other")], "thread_switch"),
    (True, [builtin_use(session_thread_id="")], "thread_missing"),
    (True, [builtin_use(evaluated_permission=None)], "permission_not_qualified"),
    (True, [builtin_use(evaluated_permission="ask")], "permission_not_qualified"),
    (True, [builtin_result()], "result_without_call"),
    (True, [builtin_use()], "result_missing"),
    (True, [builtin_use(), builtin_result(tool_use_id="other")], "result_without_call"),
    (True, [builtin_use(), builtin_use(id="duplicate", input={"command": "changed"}, tool_use_id="call-public")], "identity_conflict"),
    (True, [builtin_use(), builtin_result(), builtin_result(id="other", content=[])], "result_conflict"),
])
def test_invalid_builtin_histories_withhold_candidate_and_preserve_unknown_cost(tmp_path, enabled, events, error):
    cfg = replace(config(tmp_path, tools=False), sandbox_builtins=enabled)
    provider = Provider(events)
    with pytest.raises(AdapterError, match=error):
        asyncio.run(execute(provider, cfg))
    row = state(cfg)
    assert "candidate" not in row and row.get("provider_usage") is None
    assert provider.deleted == {"/sessions/sesn-fixture", "/agents/agnt-fixture"}
    before = len(provider.calls)
    with pytest.raises(AdapterError, match="reconciliation"):
        asyncio.run(execute(provider, cfg))
    assert len(provider.calls) == before


def test_even_enabled_builtin_before_input_ack_is_unqualified(tmp_path):
    cfg = replace(config(tmp_path, tools=False), sandbox_builtins=True)
    provider = Provider([])
    provider.startup_events = [builtin_use()]
    with pytest.raises(AdapterError, match="builtin_before_input"):
        asyncio.run(execute(provider, cfg))
    assert "candidate" not in state(cfg)
    assert provider.deleted == {"/sessions/sesn-fixture", "/agents/agnt-fixture"}


@pytest.mark.parametrize("settings", [
    {"type": "self_hosted"}, {"env": {"PUBLIC_FIXTURE_SECRET": "synthetic"}},
    {"setup_script": "touch synthetic"}, {"packages": {"pip": ["synthetic"]}},
    {"tos": {"bucket": "synthetic"}},
])
@pytest.mark.parametrize("frozen", [False, True])
def test_unsafe_environment_rejected_before_input_or_startup(tmp_path, settings, frozen):
    cfg = replace(config(tmp_path, tools=False), sandbox_builtins=True)
    provider = Provider([])
    environment = {"id": "env-fixture", "config": {"type": "cloud", "networking": NETWORK} | settings}
    if frozen:
        provider.frozen_environment = environment
    else:
        provider.environment_snapshot = environment
    with pytest.raises(AdapterError, match="environment_not_qualified"):
        asyncio.run(execute(provider, cfg))
    assert not any(path.endswith("/events") for _, path, _ in provider.calls)
    if not frozen:
        assert [method for method, _, _ in provider.calls] == ["GET"]


@pytest.mark.parametrize("mutation", ["network", "missing", "resource", "vault"])
def test_weaker_or_missing_frozen_isolation_is_rejected(tmp_path, mutation):
    cfg = replace(config(tmp_path, tools=False), sandbox_builtins=True)
    provider = Provider([])
    provider.frozen_environment = {"id": "env-fixture", "config": {"type": "cloud", "networking": NETWORK}}
    if mutation == "network":
        provider.frozen_environment["config"]["networking"] = NETWORK | {"allowed_hosts": ["example.invalid"]}
    elif mutation == "missing":
        provider.frozen_environment = {}
    else:
        provider.session_extras["resources" if mutation == "resource" else "vault_ids"] = (
            [{"type": "file", "file_id": "file-public", "mount_path": "/fixture"}] if mutation == "resource" else ["synthetic"])
    with pytest.raises(AdapterError, match="not_qualified"):
        asyncio.run(execute(provider, cfg))
    assert not any(path.endswith("/events") for _, path, _ in provider.calls)


def test_builtin_observation_limit_includes_custom_calls(tmp_path):
    cfg = replace(config(tmp_path), sandbox_builtins=True, max_tool_calls=1)
    provider = Provider([builtin_use(), builtin_result(), tool_event()])
    with pytest.raises(AdapterError, match="tool_call_budget"):
        asyncio.run(execute(provider, cfg))
    assert not (cfg.workspace / "observation.json").exists()


def test_legacy_candidate_is_inspectable_but_cannot_gain_new_qualification(tmp_path):
    cfg = config(tmp_path, tools=False)
    provider = Provider([])
    asyncio.run(execute(provider, cfg))
    row = state(cfg)
    row.pop("tool_boundary_revision")
    path = Receipt(cfg.state_dir, request()["turn_key"]).path
    path.write_text(json.dumps(row))
    before = len(provider.calls)
    with pytest.raises(AdapterError, match="legacy_attempt"):
        asyncio.run(execute(provider, cfg))
    assert len(provider.calls) == before
    receipt = Receipt(cfg.state_dir, request()["turn_key"])
    receipt.read()
    assert receipt.projection()["has_candidate"] is True
    assert config_digest(replace(cfg, sandbox_builtins=False)) == row["provider_config_digest"]


def test_enabled_checkpoint_replays_observation_without_repeating_effects(tmp_path, monkeypatch):
    cfg = replace(config(tmp_path), sandbox_builtins=True)
    provider = Provider([builtin_use(), builtin_result(), tool_event()])
    checkpoints = []
    save = Receipt.save

    def capture(receipt):
        save(receipt)
        if receipt.data["stage"] == "terminal" and not receipt.data.get("candidate"):
            checkpoints.append(copy.deepcopy(receipt.data))

    monkeypatch.setattr(Receipt, "save", capture)
    asyncio.run(execute(provider, cfg))
    Receipt(cfg.state_dir, request()["turn_key"]).path.write_text(json.dumps(checkpoints[0]))
    provider.deleted.clear()
    before = len(provider.calls)
    assert asyncio.run(execute(provider, cfg))["result_kind"] == "validated_progress"
    assert not any(method == "POST" for method, _, _ in provider.calls[before:])
    assert json.loads((cfg.workspace / "observation.json").read_text())["calls"] == 1


def test_runtime_environment_change_rejects_candidate(tmp_path):
    cfg = replace(config(tmp_path, tools=False), sandbox_builtins=True)
    provider = Provider([builtin_use(), builtin_result()])

    def transport(req):
        response = provider(req)
        if req.method == "GET" and req.url.path.endswith("/events"):
            provider.frozen_environment["config"]["networking"] = NETWORK | {"allow_package_managers": True}
        return response

    with pytest.raises(AdapterError, match="networking_not_qualified"):
        asyncio.run(execute(transport, cfg))
    assert "candidate" not in state(cfg)
    assert provider.deleted == {"/sessions/sesn-fixture", "/agents/agnt-fixture"}
