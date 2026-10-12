from pathlib import Path

import pytest

from loopx.capabilities.native_chat.codex_context import codex_home, disable_mcp_servers, process_environment, public_source_reader, public_source_read_context, shared_chatgpt_transport


def test_default_host_preserves_environment_and_native_store(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNTHETIC_PROVIDER_KEY", "fixture")
    home = codex_home(tmp_path, None)
    env = process_environment(home, isolated=False)
    assert home == tmp_path
    assert env["SYNTHETIC_PROVIDER_KEY"] == "fixture"
    assert env["CODEX_HOME"] == str(tmp_path)


def test_private_context_rejects_symlink_to_another_store(tmp_path):
    root = tmp_path / "loopx-projects"
    root.mkdir()
    other = tmp_path / "account"
    other.mkdir()
    (root / "fixture").symlink_to(other, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        codex_home(tmp_path, {"host_store_key": "fixture"})


def test_private_host_environment_does_not_inherit_account_variables(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNTHETIC_PROVIDER_KEY", "fixture")
    monkeypatch.setenv("HTTPS_PROXY", "fixture-private-proxy")
    monkeypatch.setenv("BASH_ENV", "fixture-private-profile")
    home = tmp_path / "private" / ".codex"
    env = process_environment(home, isolated=True)
    assert home.is_dir()
    assert Path(env["HOME"]) == home.parent
    assert env["CODEX_HOME"] == str(home)
    assert all(k not in env for k in ("SYNTHETIC_PROVIDER_KEY", "HTTPS_PROXY", "BASH_ENV"))


def test_mcp_disable_overrides_all_config_layers_without_forwarding_secrets():
    private = {"mcp_servers": {"fixture": {"command": "private", "env": {"TOKEN": "secret"}}}}
    requested = {"mcp_servers": {"caller": {"enabled": True}}, "model": "fixture"}
    result = disable_mcp_servers(private, requested)
    assert result == {"model": "fixture", "features": {"apps": False}, "mcp_servers": {
        "fixture": {"enabled": False}, "caller": {"enabled": False}}}
    assert private["mcp_servers"]["fixture"]["env"]["TOKEN"] == "secret"
    for config, override in [({"mcp_servers": "bad"}, {}), ({}, {"mcp_servers": ["bad"]})]:
        with pytest.raises(ValueError, match="MCP"):
            disable_mcp_servers(config, override)


def test_private_native_tools_disable_apps_with_public_reader_off(monkeypatch):
    monkeypatch.setenv("LOOPX_CHAT_PUBLIC_SOURCE_READ", "off")
    private = {"features": {"apps": True}, "mcp_servers": {"private": {"enabled": True}}}
    requested = {"features": {"apps": True, "shell_tool": True}}
    result = public_source_reader(private, disable_mcp_servers(private, requested))
    assert result["features"] == {"apps": False, "shell_tool": True}
    assert result["mcp_servers"] == {"private": {"enabled": False}}
    assert private["features"]["apps"] is True
    assert requested["features"]["apps"] is True
    with pytest.raises(ValueError, match="feature"):
        disable_mcp_servers({}, {"features": ["apps"]})


def test_public_reader_off_does_not_change_ordinary_host_config(monkeypatch):
    monkeypatch.setenv("LOOPX_CHAT_PUBLIC_SOURCE_READ", "off")
    config = {"features": {"apps": True}, "mcp_servers": {"private": {"enabled": True}}}
    assert public_source_reader(config, config) == config


def test_public_source_context_requires_actual_admission_not_environment(monkeypatch):
    monkeypatch.setenv("LOOPX_CHAT_PUBLIC_SOURCE_READ", "on")
    assert public_source_read_context({}) == ""
    assert public_source_read_context({"mcp_servers": {
        "loopx_public_source_read": {"enabled": False}}}) == ""
    assert public_source_read_context({"mcp_servers": {
        "loopx_public_source_read": {"enabled": True, "enabled_tools": ["other_tool"]}}}) == ""
    admitted = public_source_reader({}, {})
    monkeypatch.setenv("LOOPX_CHAT_PUBLIC_SOURCE_READ", "off")
    assert "read_public_url" in public_source_read_context(admitted)
    disabled = public_source_reader(admitted, disable_mcp_servers(admitted, {}))
    assert public_source_read_context(disabled) == ""


def test_shared_chatgpt_transport_preserves_explicit_provider_and_core_policy():
    explicit = {"model_provider": "operator-provider", "model_providers": {
        "operator-provider": {"name": "operator-owned"}}}
    assert shared_chatgpt_transport(explicit, {}) == explicit
    policy = {"default_permissions": "fixture", "permissions": {"fixture": {
        "network": {"enabled": False}, "filesystem": {"/fixture": "write"}}}}
    result = shared_chatgpt_transport(policy, {})
    assert result["permissions"] == policy["permissions"]
    assert result["default_permissions"] == "fixture"
    assert "model_provider" not in policy
    assert set(result["model_providers"][result["model_provider"]]) == {
        "name", "wire_api", "requires_openai_auth", "supports_websockets"}
    with pytest.raises(ValueError, match="conflicts with project configuration"):
        shared_chatgpt_transport(policy, {"model_providers": {
            result["model_provider"]: {"base_url": "https://example.invalid", "env_key": "PRIVATE_KEY"}}})


def test_compatibility_catalog_probe_keeps_private_process_environment(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    from loopx import chat_agent
    monkeypatch.setenv("SYNTHETIC_PROVIDER_KEY", "account-secret")
    env = process_environment(tmp_path / "private" / ".codex", isolated=True)
    launched = []
    monkeypatch.setattr(chat_agent.subprocess, "run", lambda *args, **kwargs:
        launched.append(kwargs["env"]) or SimpleNamespace(returncode=0,
            stdout=json.dumps({"models": [{"base_instructions": "synthetic built-in model"}]})))
    with chat_agent._current_builtin_model_catalog("fixture-native-codex", environment=env):
        assert "SYNTHETIC_PROVIDER_KEY" not in launched[0]
        assert launched[0]["HOME"] == env["HOME"]
        assert launched[0]["CODEX_HOME"] != env["CODEX_HOME"]
    assert env["CODEX_HOME"] == str(tmp_path / "private" / ".codex")
