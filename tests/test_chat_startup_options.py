from __future__ import annotations

import inspect

import pytest

from loopx.chat_server import serve_chat
from loopx.cli import build_parser
from loopx.cli_commands import support_control
from loopx.cli_commands.support_control_chat import chat_server_options


@pytest.mark.parametrize("configured, expected", [(None, None), ("0", 0.1), ("12.5", 12.5), ("45", 45.0)])
def test_shared_startup_options_preserve_adapter_defaults_and_explicit_limits(tmp_path, configured, expected):
    tail = [] if configured is None else ["--startup-timeout-seconds", configured]
    args = build_parser().parse_args(["chat", "--no-open", *tail])
    options = chat_server_options(args, registry_path=tmp_path / "registry.json")
    assert options["startup_timeout_sec"] == expected
    assert options["idle_timeout_sec"] == 180.0
    assert options["hard_timeout_sec"] == 900.0
    inspect.signature(serve_chat).bind(**options)


def test_shared_options_keep_workspace_permissions_and_explicit_timeout_floors(tmp_path):
    args = build_parser().parse_args([
        "chat", "--project-workspace-grant", "workspace_read",
        "--project-filesystem-scope", "workspace_only", "--no-private-reactions",
        "--idle-timeout-seconds", "0", "--hard-timeout-seconds", "0", "--limit", "-1",
    ])
    options = chat_server_options(args, registry_path=tmp_path / "registry.json")
    assert options["project_workspace_grant"] == "workspace_read"
    assert options["project_filesystem_scope"] == "workspace_only"
    assert options["private_reactions"] is False
    assert options["idle_timeout_sec"] == options["hard_timeout_sec"] == 0.1
    assert options["limit"] == 0


def test_shared_options_resolve_global_registry_and_expand_selected_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    runtime = tmp_path / "runtime"
    args = build_parser().parse_args([
        "--runtime-root", str(runtime), "chat", "--global-registry",
        "--scan-path", "~/first", "--scan-path", "~/second", "--assets-dir", "~/bundle",
    ])
    options = chat_server_options(args, registry_path=tmp_path / "absent.json")
    assert options["registry_path"] == runtime / "registry.global.json"
    assert options["scan_roots"] == [tmp_path / "first", tmp_path / "second"]
    assert options["assets_dir"] == tmp_path / "bundle"
    assert not runtime.exists()


def test_shared_options_keep_scan_root_fallback_and_reject_another_command(tmp_path):
    args = build_parser().parse_args(["chat", "--scan-root", str(tmp_path)])
    assert chat_server_options(args, registry_path=tmp_path / "registry.json")["scan_roots"] == [tmp_path]
    args = build_parser().parse_args(["dashboard"])
    with pytest.raises(ValueError, match="require the chat command"):
        chat_server_options(args, registry_path=tmp_path / "registry.json")


def test_cli_uses_shared_options_before_replacing_or_starting_a_server(tmp_path, monkeypatch):
    args = build_parser().parse_args(["chat", "--no-open", "--replace-existing-loopx-chat"])
    registry = tmp_path / "registry.json"
    calls = []

    def compose(received, *, registry_path):
        assert received is args and registry_path == registry
        calls.append("compose")
        return chat_server_options(received, registry_path=registry_path)

    monkeypatch.setattr(support_control, "chat_server_options", compose)
    monkeypatch.setattr(support_control, "replace_existing_loopx_chat", lambda *args: calls.append("replace"))
    monkeypatch.setattr(support_control, "serve_chat", lambda **kwargs: calls.append(kwargs))
    result = support_control.handle_support_control_command(args, registry_path=registry,
        registry_was_supplied=True, print_payload=lambda *args: None, output_format=lambda *args: "json")
    assert result == 0
    assert calls[:2] == ["compose", "replace"]
    assert calls[2]["startup_timeout_sec"] is None
    assert calls[2]["registry_path"] == registry
    assert calls[2]["open_browser"] is False


def test_failed_option_composition_does_not_replace_a_running_server(tmp_path, monkeypatch):
    args = build_parser().parse_args(["chat", "--replace-existing-loopx-chat"])
    calls = []
    payloads = []

    def reject(*args, **kwargs):
        raise ValueError("invalid fixture options")

    monkeypatch.setattr(support_control, "chat_server_options", reject)
    monkeypatch.setattr(support_control, "replace_existing_loopx_chat", lambda *args: calls.append("replace"))
    monkeypatch.setattr(support_control, "serve_chat", lambda **kwargs: calls.append("serve"))
    result = support_control.handle_support_control_command(args, registry_path=tmp_path / "registry.json",
        registry_was_supplied=True, print_payload=lambda payload, *args: payloads.append(payload),
        output_format=lambda *args: "json")
    assert result == 1 and calls == []
    assert payloads[0]["ok"] is False
    assert payloads[0]["schema_version"] == "loopx_chat_start_v0"
