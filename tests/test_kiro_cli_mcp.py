"""Kiro CLI reaches LoopX's typed control plane through the shared MCP server.

Three contracts matter and each is exercised for real rather than mocked:

* identity: the server acts only for the agent the running session's
  ``KIRO_SESSION_ID`` is bound to, and fails closed otherwise;
* settlement: a completion through the Kiro server runs the same typed
  guard -> lifecycle -> writeback -> spend path Claude uses, under the Kiro
  runtime profile;
* install: the kiro-cli surface registers exactly one ``loopx`` entry in
  ``KIRO_HOME/settings/mcp.json`` and never takes over a user's own server.
"""

from __future__ import annotations

from typing import cast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from loopx import slash_command_install
from loopx.control_plane.coordination.goal_storage_initialization import (
    initialize_goal_storage_target,
)
from loopx.goal_mode_mcp import GoalModeMCPControlPlane
from loopx.hosts.kiro_cli import (
    KIRO_CLI_MCP_CONFIG_SUBPATH,
    KIRO_CLI_SESSION_ID_ENV,
    mcp_server_script,
)
from loopx.hosts.kiro_cli.mcp_server import CONFIG, goal_context
from loopx.slash_command_install import install_slash_commands
from loopx.status import parse_active_state_todos
from loopx.todos import add_goal_todo

GOAL_ID = "kiro-mcp-fixture"
AGENT_ID = "kiro-worker"
OTHER_AGENT_ID = "codex-main"
SESSION_ID = "0f5d0a52-5b8e-4c55-9a8e-4a3f2b1c9d10"


def _write_project(
    tmp_path: Path,
    *,
    bindings: list[dict[str, str]] | None = None,
) -> tuple[Path, Path, Path]:
    project = tmp_path / "project"
    registry = project / ".loopx" / "registry.json"
    registry.parent.mkdir(parents=True)
    state_file = project / "ACTIVE_GOAL_STATE.md"
    state_file.write_text(
        "---\n"
        f"goal_id: {GOAL_ID}\n"
        "status: active\n"
        "updated_at: 2026-08-21T00:00:00+00:00\n"
        "---\n\n## Agent Todo\n\n",
        encoding="utf-8",
    )
    registry.write_text(
        json.dumps(
            {
                "common_runtime_root": str(tmp_path / "runtime"),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "creation_operation_id": f"goal-create:{GOAL_ID}",
                        "domain": "harness_self_improvement",
                        "status": "active",
                        "repo": str(project),
                        "state_file": state_file.name,
                        "adapter": {
                            "kind": "harness_self_improvement",
                            "status": "connected-read-only",
                        },
                        "quota": {
                            "compute": 1.0,
                            "window_hours": 24,
                            "slot_minutes": 1,
                        },
                        "coordination": {
                            "agent_model": "peer_v1",
                            # A different agent first: resolution must never
                            # fall back to registry order.
                            "registered_agents": [OTHER_AGENT_ID, AGENT_ID],
                            "storage_target": {
                                "schema_version": "loopx_new_goal_storage_target_v1",
                                "provider": "file",
                                "handoff_mode": "soft_claim",
                            },
                            "thread_agent_bindings": (
                                bindings
                                if bindings is not None
                                else [
                                    {
                                        "thread_id": SESSION_ID,
                                        "host_surface": "kiro-cli",
                                        "agent_id": AGENT_ID,
                                    }
                                ]
                            ),
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    goal = json.loads(registry.read_text(encoding="utf-8"))["goals"][0]
    initialized = initialize_goal_storage_target(
        tmp_path / "runtime",
        goal,
        registry_path=registry,
    )
    assert initialized["authority_initialized"] is True
    assert initialized["legacy_fallback_used"] is False
    return project, registry, state_file


def test_session_binding_selects_the_bound_agent_not_registry_order(
    tmp_path: Path,
) -> None:
    project, registry, _ = _write_project(tmp_path)
    context = goal_context(project, {KIRO_CLI_SESSION_ID_ENV: SESSION_ID})
    assert context is not None
    assert context["goal_id"] == GOAL_ID
    assert context["agent_id"] == AGENT_ID
    assert context["registry"] == str(registry)


@pytest.mark.parametrize(
    ("environ", "bindings"),
    (
        # The host exported no session id.
        ({}, None),
        # A session nobody bound.
        ({KIRO_CLI_SESSION_ID_ENV: "another-session"}, None),
        # The same id bound under another host surface is not a Kiro binding.
        (
            {KIRO_CLI_SESSION_ID_ENV: SESSION_ID},
            [{"thread_id": SESSION_ID, "host_surface": "codex-app", "agent_id": AGENT_ID}],
        ),
        # One session bound to two lanes is ambiguous, never a pick.
        (
            {KIRO_CLI_SESSION_ID_ENV: SESSION_ID},
            [
                {"thread_id": SESSION_ID, "host_surface": "kiro-cli", "agent_id": AGENT_ID},
                {"thread_id": SESSION_ID, "host_surface": "kiro-cli", "agent_id": OTHER_AGENT_ID},
            ],
        ),
        # A binding to an agent the Goal does not register.
        (
            {KIRO_CLI_SESSION_ID_ENV: SESSION_ID},
            [{"thread_id": SESSION_ID, "host_surface": "kiro-cli", "agent_id": "ghost"}],
        ),
    ),
)
def test_unbound_or_ambiguous_sessions_fail_closed(
    tmp_path: Path,
    environ: dict[str, str],
    bindings: list[dict[str, str]] | None,
) -> None:
    project, _, _ = _write_project(tmp_path, bindings=bindings)
    assert goal_context(project, environ) is None
    control = GoalModeMCPControlPlane(CONFIG, lambda: goal_context(project, environ))
    refused = json.loads(control.should_run())
    assert refused["ok"] is False
    assert "Kiro CLI session" in refused["next_action"]


def _run_cli(registry: Path, *args: str) -> tuple[int, dict[str, object]]:
    result = subprocess.run(
        [sys.executable, "-m", "loopx.cli", "--registry", str(registry), "--format", "json", *args],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode, json.loads(result.stdout)


def test_completion_through_the_kiro_server_settles_once_under_its_profile(
    tmp_path: Path,
) -> None:
    project, registry, state_file = _write_project(tmp_path)
    added = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="Deliver the bounded Kiro fixture change.",
        task_class="advancement_task",
        claimed_by=AGENT_ID,
        continuation_policy="same_agent_non_delivery",
    )
    todo_id = str(added["todo_id"])
    environ = {KIRO_CLI_SESSION_ID_ENV: SESSION_ID}
    control = GoalModeMCPControlPlane(CONFIG, lambda: goal_context(project, environ))
    control.command_prefix = lambda: [sys.executable, "-m", "loopx.cli"]

    # The gate runs under the Kiro profile for the bound agent.
    gate = json.loads(control.should_run())
    assert gate["goal_id"] == GOAL_ID
    assert gate["agent_identity"]["agent_id"] == AGENT_ID
    assert gate["should_run"] is True
    assert gate["scheduler_hint"]["execution_context"]["host_surface"] == "kiro_cli"

    # The server refuses to act for an agent the session is not bound to.
    foreign = json.loads(control.claim_task(todo_id, OTHER_AGENT_ID))
    assert foreign["ok"] is False
    assert foreign["bound_agent_id"] == AGENT_ID

    result = json.loads(
        control.complete_task(
            todo_id,
            AGENT_ID,
            "focused Kiro fixture validation passed",
            next_agent_todo="Run the Kiro successor fixture check.",
        )
    )
    assert result["ok"] is True, json.dumps(result, indent=2, sort_keys=True)
    settlement = result["settlement"]
    assert settlement["durable_writeback"]["ok"] is True
    assert settlement["quota_spend"]["appended"] is True

    status_rc, status = _run_cli(
        registry,
        "quota",
        "should-run",
        "--context-delivery",
        "off",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--runtime-profile",
        CONFIG.runtime_profile,
    )
    assert status_rc == 0, status
    assert status["quota"]["spent_slots"] == 1  # type: ignore[index]
    todos = parse_active_state_todos(state_file.read_text(encoding="utf-8"))
    by_id = {str(item["todo_id"]): item for item in todos["agent_todos"]["items"]}
    assert by_id[todo_id]["status"] == "done"


def test_completion_requires_a_connected_delivery_frontier(tmp_path: Path) -> None:
    project, registry, state_file = _write_project(tmp_path)
    document = json.loads(registry.read_text(encoding="utf-8"))
    document["goals"][0]["adapter"].pop("status")
    registry.write_text(json.dumps(document), encoding="utf-8")
    added = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="Retain the unadmitted Kiro task.",
        task_class="advancement_task",
        claimed_by=AGENT_ID,
    )
    before = state_file.read_bytes()
    control = GoalModeMCPControlPlane(
        CONFIG, lambda: goal_context(project, {KIRO_CLI_SESSION_ID_ENV: SESSION_ID}),
    )
    control.command_prefix = lambda: [sys.executable, "-m", "loopx.cli"]
    result = json.loads(control.complete_task(str(added["todo_id"]), AGENT_ID, "not admitted"))
    assert result["ok"] is False and result["settlement_blocked_completion"] is True
    assert result["settlement"]["failed_stage"] == "guard"
    assert state_file.read_bytes() == before
    code, status = _run_cli(
        registry, "quota", "should-run", "--context-delivery", "off", "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID, "--runtime-profile", CONFIG.runtime_profile,
    )
    assert code == 0 and status["quota"]["spent_slots"] == 0


def test_server_entrypoint_starts_as_a_standalone_script() -> None:
    """Kiro launches the registered script with the provisioned interpreter,
    not as an installed module, so it must import cleanly by path."""
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import runpy, sys; sys.argv=['x']; "
            f"ns = runpy.run_path({str(mcp_server_script())!r}, run_name='probe'); "
            "print(ns['CONFIG'].runtime_profile)",
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd="/",
    )
    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.strip() == "kiro_cli"


def _mcp_row(payload: dict[str, object]) -> dict[str, object]:
    rows = [
        row
        for row in payload["installed"]  # type: ignore[union-attr]
        if row["mechanism"] == "kiro_cli_mcp_server"  # type: ignore[index]
    ]
    assert len(rows) == 1
    return rows[0]  # type: ignore[return-value]


def test_install_registers_one_loopx_entry_and_uninstall_retires_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = {"command": "/opt/loopx/bin/python", "args": [str(mcp_server_script())]}
    monkeypatch.setattr(
        slash_command_install,
        "_kiro_cli_mcp_command",
        lambda: (entry["command"], entry["args"][0]),
    )
    kiro_home = tmp_path / "kiro-home"
    config = kiro_home / KIRO_CLI_MCP_CONFIG_SUBPATH
    config.parent.mkdir(parents=True)
    other = {"command": "codegraph", "args": ["serve", "--mcp"]}
    config.write_text(json.dumps({"mcpServers": {"codegraph": other}}), encoding="utf-8")

    preview = install_slash_commands(
        execute=False, surfaces=["kiro-cli"], kiro_home=str(kiro_home)
    )
    assert _mcp_row(preview)["status"] == "would_write"
    assert json.loads(config.read_text(encoding="utf-8")) == {"mcpServers": {"codegraph": other}}

    payload = install_slash_commands(
        execute=True, surfaces=["kiro-cli"], kiro_home=str(kiro_home)
    )
    row = _mcp_row(payload)
    assert row["status"] == "written"
    assert row["path"] == str(config)
    assert payload["summary"]["kiro_cli_mcp_path"] == str(config)  # type: ignore[index]
    assert json.loads(config.read_text(encoding="utf-8")) == {
        "mcpServers": {"codegraph": other, "loopx": entry}
    }

    again = install_slash_commands(execute=True, surfaces=["kiro-cli"], kiro_home=str(kiro_home))
    assert _mcp_row(again)["status"] == "unchanged"

    removed = install_slash_commands(
        execute=True, uninstall=True, surfaces=["kiro-cli"], kiro_home=str(kiro_home)
    )
    assert _mcp_row(removed)["status"] == "retired"
    assert json.loads(config.read_text(encoding="utf-8")) == {"mcpServers": {"codegraph": other}}


def test_install_never_takes_over_a_user_owned_loopx_server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        slash_command_install,
        "_kiro_cli_mcp_command",
        lambda: ("/opt/loopx/bin/python", str(mcp_server_script())),
    )
    kiro_home = tmp_path / "kiro-home"
    config = kiro_home / KIRO_CLI_MCP_CONFIG_SUBPATH
    config.parent.mkdir(parents=True)
    mine = {"mcpServers": {"loopx": {"command": "my-own-loopx", "args": []}}}
    config.write_text(json.dumps(mine), encoding="utf-8")

    for uninstall in (False, True):
        payload = install_slash_commands(
            execute=True,
            uninstall=uninstall,
            surfaces=["kiro-cli"],
            kiro_home=str(kiro_home),
        )
        assert _mcp_row(payload)["status"] == "skipped_user_owned_mcp_entry"
        assert json.loads(config.read_text(encoding="utf-8")) == mine


def test_install_reports_a_malformed_config_instead_of_rewriting_it(
    tmp_path: Path,
) -> None:
    kiro_home = tmp_path / "kiro-home"
    config = kiro_home / KIRO_CLI_MCP_CONFIG_SUBPATH
    config.parent.mkdir(parents=True)
    config.write_text('{"mcpServers": ["not", "a", "map"]}', encoding="utf-8")
    payload = install_slash_commands(
        execute=True, surfaces=["kiro-cli"], kiro_home=str(kiro_home)
    )
    assert _mcp_row(payload)["status"] == "blocked_invalid_kiro_cli_mcp_json"
    assert config.read_text(encoding="utf-8") == '{"mcpServers": ["not", "a", "map"]}'


def test_default_install_migrates_stale_managed_kiro_mcp_and_preserves_other_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default `slash-commands --install` (`surfaces=all`, what `self-update` runs)
    migrates a sidecar-verified Kiro MCP registration whose server path moved."""
    from loopx.hosts.claude.scripts import install as claude_install

    provision_calls = 0

    def fake_provision(*, dry: bool = False) -> Path:
        nonlocal provision_calls
        if not dry:
            provision_calls += 1
        return Path(sys.executable)

    monkeypatch.setattr(claude_install, "provision_mcp_python", fake_provision)
    monkeypatch.setattr(claude_install, "_has_mcp", lambda _interp: True)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    kiro_home = tmp_path / "kiro-home"
    config = kiro_home / KIRO_CLI_MCP_CONFIG_SUBPATH
    marker = config.parent / slash_command_install.CURSOR_MCP_MARKER_NAME
    config.parent.mkdir(parents=True)
    stale_script = tmp_path / "removed-wheel" / "loopx" / "kiro_cli_goal_mode" / "mcp_server.py"
    stale_entry = {"command": "/stale/mcp-venv/bin/python", "args": [str(stale_script)]}
    other = {"command": "codegraph", "args": ["serve", "--mcp"]}
    config.write_text(json.dumps({"mcpServers": {"codegraph": other, "loopx": stale_entry}}), encoding="utf-8")
    marker.write_text(
        json.dumps(
            {
                "schema_version": slash_command_install.KIRO_CLI_MCP_MARKER_SCHEMA_VERSION,
                "servers": {"loopx": stale_entry},
            }
        ),
        encoding="utf-8",
    )

    codex_home = str(tmp_path / "codex")
    claude_home = str(tmp_path / "claude")
    opencode_home = str(tmp_path / "opencode")
    kiro_home_arg = str(kiro_home)

    preview = install_slash_commands(
        execute=False,
        codex_home=codex_home,
        claude_home=claude_home,
        opencode_home=opencode_home,
        kiro_home=kiro_home_arg,
    )
    assert preview["effective_surfaces"] == ["codex", "claude-code", "opencode"]
    assert provision_calls == 0
    assert _mcp_row(preview)["status"] == "would_write"
    assert json.loads(config.read_text(encoding="utf-8"))["mcpServers"]["loopx"] == stale_entry
    assert not (kiro_home / "skills").exists()

    # The stale removed script fails before migration.
    stale_run = subprocess.run([sys.executable, str(stale_script)], capture_output=True, text=True, check=False)
    assert stale_run.returncode == 2

    migrated = install_slash_commands(
        execute=True,
        codex_home=codex_home,
        claude_home=claude_home,
        opencode_home=opencode_home,
        kiro_home=kiro_home_arg,
    )
    assert provision_calls == 1
    assert _mcp_row(migrated)["status"] == "written"
    migrated_summary = cast(dict[str, object], migrated["summary"])
    assert migrated_summary["kiro_cli_mcp_path"] == str(config)
    assert migrated_summary["kiro_cli_skill_dir"] is None
    assert not (kiro_home / "skills").exists()

    expected_entry = {"command": sys.executable, "args": [str(mcp_server_script())]}
    assert json.loads(config.read_text(encoding="utf-8")) == {
        "mcpServers": {"codegraph": other, "loopx": expected_entry}
    }
    assert json.loads(marker.read_text(encoding="utf-8")) == {
        "schema_version": slash_command_install.KIRO_CLI_MCP_MARKER_SCHEMA_VERSION,
        "servers": {"loopx": expected_entry},
    }

    # Verify the migrated entry imports cleanly as a standalone script.
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import runpy; ns = runpy.run_path(__import__('sys').argv[1], run_name='probe'); print(ns['CONFIG'].runtime_profile)",
            str(mcp_server_script()),
        ],
        capture_output=True,
        text=True,
        cwd="/",
        check=False,
    )
    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.strip() == "kiro_cli"

    # Re-running default install is a clean no-op with zero extra provisioning.
    second = install_slash_commands(
        execute=True,
        codex_home=codex_home,
        claude_home=claude_home,
        opencode_home=opencode_home,
        kiro_home=kiro_home_arg,
    )
    assert provision_calls == 1
    second_summary = cast(dict[str, object], second["summary"])
    second_installed = cast(list[dict[str, object]], second["installed"])
    assert second_summary["kiro_cli_mcp_path"] is None
    assert all(row["surface"] != "kiro-cli" for row in second_installed)

    # Explicit kiro-cli uninstall still retires only the migrated LoopX entry.
    default_uninstall = install_slash_commands(
        execute=True,
        uninstall=True,
        codex_home=codex_home,
        claude_home=claude_home,
        opencode_home=opencode_home,
        kiro_home=kiro_home_arg,
    )
    default_uninstall_rows = cast(list[dict[str, object]], default_uninstall["installed"])
    assert all(row["surface"] != "kiro-cli" for row in default_uninstall_rows)
    assert "loopx" in json.loads(config.read_text(encoding="utf-8"))["mcpServers"]

    kiro_uninstall = install_slash_commands(
        execute=True, uninstall=True, surfaces=["kiro-cli"], kiro_home=str(kiro_home)
    )
    assert _mcp_row(kiro_uninstall)["status"] == "retired"
    assert json.loads(config.read_text(encoding="utf-8")) == {"mcpServers": {"codegraph": other}}


@pytest.mark.parametrize("case", ("no_sidecar", "user_edited_entry", "wrong_schema", "malformed_config"))
def test_default_install_leaves_unverified_or_user_owned_kiro_mcp_untouched(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    monkeypatch.setattr(
        slash_command_install,
        "_kiro_cli_mcp_command",
        lambda: pytest.fail("must not provision or rewrite unverified Kiro MCP"),
    )
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    kiro_home = tmp_path / "kiro-home"
    config = kiro_home / KIRO_CLI_MCP_CONFIG_SUBPATH
    marker = config.parent / slash_command_install.CURSOR_MCP_MARKER_NAME
    config.parent.mkdir(parents=True)

    mine = {"command": "my-custom-loopx", "args": ["--custom"]}
    other = {"command": "codegraph", "args": ["serve"]}
    if case == "malformed_config":
        raw_config = "{not-valid-json"
        config.write_text(raw_config, encoding="utf-8")
        marker.write_text(
            json.dumps(
                {
                    "schema_version": slash_command_install.KIRO_CLI_MCP_MARKER_SCHEMA_VERSION,
                    "servers": {"loopx": {"command": "/old/python", "args": ["/old/mcp_server.py"]}},
                }
            ),
            encoding="utf-8",
        )
    else:
        raw_config = json.dumps({"mcpServers": {"other": other, "loopx": mine}})
        config.write_text(raw_config, encoding="utf-8")
        if case == "user_edited_entry":
            marker.write_text(
                json.dumps(
                    {
                        "schema_version": slash_command_install.KIRO_CLI_MCP_MARKER_SCHEMA_VERSION,
                        "servers": {"loopx": {"command": "/old/python", "args": ["/old/mcp_server.py"]}},
                    }
                ),
                encoding="utf-8",
            )
        elif case == "wrong_schema":
            marker.write_text(
                json.dumps({"schema_version": "other_schema_v0", "servers": {"loopx": mine}}),
                encoding="utf-8",
            )

    payload = install_slash_commands(
        execute=True,
        codex_home=str(tmp_path / "codex"),
        claude_home=str(tmp_path / "claude"),
        opencode_home=str(tmp_path / "opencode"),
        kiro_home=str(kiro_home),
    )
    assert payload["ok"] is True
    payload_summary = cast(dict[str, object], payload["summary"])
    payload_installed = cast(list[dict[str, object]], payload["installed"])
    assert payload_summary["kiro_cli_mcp_path"] is None
    assert all(row["surface"] != "kiro-cli" for row in payload_installed)
    assert config.read_text(encoding="utf-8") == raw_config


@pytest.mark.parametrize("sidecar_kind", ("stale_managed", "non_utf8_sidecar"))
def test_explicit_codex_surface_skips_kiro_mcp_probe_and_migration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sidecar_kind: str,
) -> None:
    monkeypatch.setattr(
        slash_command_install,
        "_managed_kiro_mcp_needs_migration",
        lambda _path: pytest.fail("explicit --surface codex must not probe Kiro sidecar"),
    )
    monkeypatch.setattr(
        slash_command_install,
        "_kiro_cli_mcp_command",
        lambda: pytest.fail("explicit --surface codex must not provision Kiro MCP"),
    )
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    kiro_home = tmp_path / "kiro-home"
    config = kiro_home / KIRO_CLI_MCP_CONFIG_SUBPATH
    marker = config.parent / slash_command_install.CURSOR_MCP_MARKER_NAME
    config.parent.mkdir(parents=True)
    stale_entry = {"command": "/stale/python", "args": ["/stale/mcp_server.py"]}
    config_bytes = json.dumps({"mcpServers": {"loopx": stale_entry}}).encode("utf-8")
    config.write_bytes(config_bytes)
    if sidecar_kind == "stale_managed":
        marker_bytes = json.dumps(
            {
                "schema_version": slash_command_install.KIRO_CLI_MCP_MARKER_SCHEMA_VERSION,
                "servers": {"loopx": stale_entry},
            }
        ).encode("utf-8")
    else:
        marker_bytes = b"\xff\xfe\x00not-utf8-sidecar"
    marker.write_bytes(marker_bytes)

    codex_home = str(tmp_path / "codex")
    for execute in (False, True):
        payload = install_slash_commands(
            execute=execute,
            surfaces=["codex"],
            codex_home=codex_home,
            kiro_home=str(kiro_home),
        )
        assert payload["ok"] is True
        assert payload["effective_surfaces"] == ["codex"]
        summary = cast(dict[str, object], payload["summary"])
        installed = cast(list[dict[str, object]], payload["installed"])
        assert summary["kiro_cli_mcp_path"] is None
        assert summary["kiro_cli_skill_dir"] is None
        assert all(row["surface"] != "kiro-cli" for row in installed)
        assert config.read_bytes() == config_bytes
        assert marker.read_bytes() == marker_bytes
        assert not (kiro_home / "skills").exists()


def test_real_cli_handles_non_utf8_kiro_sidecar_and_selective_codex_cleanly(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    kiro_home = tmp_path / "kiro-home"
    config = kiro_home / KIRO_CLI_MCP_CONFIG_SUBPATH
    marker = config.parent / slash_command_install.CURSOR_MCP_MARKER_NAME
    config.parent.mkdir(parents=True)
    preserved_entry = {"command": "/existing/python", "args": ["/existing/mcp_server.py"]}
    config_bytes = json.dumps({"mcpServers": {"loopx": preserved_entry}}).encode("utf-8")
    corrupt_sidecar_bytes = b"\xff\xfe\x00\x80corrupt-kiro-sidecar"
    config.write_bytes(config_bytes)
    marker.write_bytes(corrupt_sidecar_bytes)

    env = {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "KIRO_HOME": str(kiro_home),
        "CODEX_HOME": str(tmp_path / "codex"),
        "CLAUDE_HOME": str(tmp_path / "claude"),
        "OPENCODE_CONFIG_DIR": str(tmp_path / "opencode"),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
        "PYTHONPATH": str(repo_root),
    }

    def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "loopx.cli_runtime", "slash-commands", *args, "--format", "json"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(tmp_path),
            check=False,
        )

    codex_only = run_cli("--install", "--surface", "codex")
    assert codex_only.returncode == 0, codex_only.stderr
    codex_payload = json.loads(codex_only.stdout)
    assert codex_payload["ok"] is True
    assert codex_payload["effective_surfaces"] == ["codex"]
    assert codex_payload["summary"]["kiro_cli_mcp_path"] is None
    assert config.read_bytes() == config_bytes
    assert marker.read_bytes() == corrupt_sidecar_bytes

    default_all = run_cli("--install")
    assert default_all.returncode == 0, default_all.stderr
    default_payload = json.loads(default_all.stdout)
    assert default_payload["ok"] is True
    assert default_payload["effective_surfaces"] == ["codex", "claude-code", "opencode"]
    assert default_payload["summary"]["kiro_cli_mcp_path"] is None
    assert all(row["surface"] != "kiro-cli" for row in default_payload["installed"])
    assert config.read_bytes() == config_bytes
    assert marker.read_bytes() == corrupt_sidecar_bytes

    kiro_selected = run_cli("--install", "--surface", "kiro-cli")
    assert kiro_selected.returncode == 0, kiro_selected.stderr
    kiro_payload = json.loads(kiro_selected.stdout)
    assert kiro_payload["ok"] is True
    assert _mcp_row(kiro_payload)["status"] == "skipped_user_owned_mcp_entry"
    assert config.read_bytes() == config_bytes
    assert marker.read_bytes() == corrupt_sidecar_bytes

    # Non-UTF-8 mcp.json itself also fails closed structurally rather than raising UnicodeDecodeError.
    config.write_bytes(corrupt_sidecar_bytes)
    bad_config = run_cli("--install", "--surface", "kiro-cli")
    assert bad_config.returncode == 1, bad_config.stderr
    bad_payload = json.loads(bad_config.stdout)
    assert bad_payload["ok"] is False
    assert _mcp_row(bad_payload)["status"] == "blocked_invalid_kiro_cli_mcp_json"
    assert config.read_bytes() == corrupt_sidecar_bytes
