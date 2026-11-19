#!/usr/bin/env python3
"""Synthetic Codex App thread-to-agent identity smoke."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shlex
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from loopx.bootstrap_command_pack import (  # noqa: E402
    build_start_goal_guided_packet,
)
from loopx.cli import main as cli_main  # noqa: E402


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-thread-agent-") as raw:
        root = Path(raw)
        project = root / "project"
        state = project / ".codex" / "goals" / "goal" / "ACTIVE_GOAL_STATE.md"
        state.parent.mkdir(parents=True)
        state.write_text("# Active Goal State\n", encoding="utf-8")
        registry = project / ".loopx" / "registry.json"
        registry.parent.mkdir(parents=True)
        registry.write_text(
            json.dumps(
                {
                    "schema_version": "0.1",
                    "goals": [
                        {
                            "id": "goal",
                            "status": "active",
                            "repo": str(project),
                            "state_file": str(state.relative_to(project)),
                            "coordination": {
                                "registered_agents": ["codex-a", "codex-b"]
                            },
                        }
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        runtime = root / "runtime"
        runtime.mkdir()
        from loopx.control_plane.testing.canary_harness import import_fixture_authority
        config = json.loads(registry.read_text(encoding="utf-8"))
        config["common_runtime_root"] = str(runtime)
        registry.write_text(json.dumps(config), encoding="utf-8")
        import_fixture_authority(registry, "goal", provider="sqlite")
        global_payload = json.loads(registry.read_text(encoding="utf-8"))
        global_payload["registry_role"] = "global-local"
        global_payload["goals"][0]["source_registry"] = str(registry)
        (runtime / "registry.global.json").write_text(
            json.dumps(global_payload, indent=2) + "\n",
            encoding="utf-8",
        )

        first = build_start_goal_guided_packet(
            project=project,
            goal_id="goal",
            agent_id="codex-a",
            thread_id="thread-a",
            cli_bin="loopx",
            host_surface="codex-app",
            goal_text="select the existing lane",
        )
        ordered_steps = first["guided_transaction"]["ordered_steps"]
        assert [step["id"] for step in ordered_steps[:4]] == [
            "inspect_connection",
            "connect_if_needed",
            "bind_thread_identity",
            "plan_ranked_todos",
        ], ordered_steps
        bind_step = ordered_steps[2]
        assert bind_step["must_stop_on_failure"] is True, bind_step

        bind_output = io.StringIO()
        bind_argv = shlex.split(bind_step["command"])
        with contextlib.redirect_stdout(bind_output):
            bind_exit = cli_main(["--runtime-root", str(runtime), *bind_argv[1:]])
        assert bind_exit == 0, bind_output.getvalue()

        previous_thread_id = os.environ.get("CODEX_THREAD_ID")
        os.environ["CODEX_THREAD_ID"] = "thread-a"
        try:
            reuse_output = io.StringIO()
            with contextlib.redirect_stdout(reuse_output):
                reuse_exit = cli_main(
                    [
                        "--runtime-root",
                        str(runtime),
                        "--format",
                        "json",
                        "start-goal",
                        "--guided",
                        "--project",
                        str(project),
                        "--goal-id",
                        "goal",
                        "--host-surface",
                        "codex-app",
                        "--goal-text",
                        "continue the task",
                    ]
                )
            assert reuse_exit == 0, reuse_output.getvalue()
            reused = json.loads(reuse_output.getvalue())
        finally:
            if previous_thread_id is None:
                os.environ.pop("CODEX_THREAD_ID", None)
            else:
                os.environ["CODEX_THREAD_ID"] = previous_thread_id

        assert reused["agent_id"] == "codex-a", reused
        assert reused["thread_agent_binding"]["status"] == "bound", reused
        assert "bind_thread_identity" not in {
            step["id"] for step in reused["guided_transaction"]["ordered_steps"]
        }, reused
        commands = reused["command_pack"]["commands"]
        assert "--agent-id codex-a" in commands["goal_start_quota_should_run"], commands
        assert "--agent-id codex-a" in commands["goal_start_refresh_state"], commands

        deep_link_output = io.StringIO()
        with contextlib.redirect_stdout(deep_link_output):
            deep_link_exit = cli_main(
                [
                    "--registry",
                    str(registry),
                    "--format",
                    "json",
                    "resolve-agent-thread",
                    "--thread-link",
                    "codex://threads/thread-a",
                ]
            )
        assert deep_link_exit == 0, deep_link_output.getvalue()
        deep_link_resolution = json.loads(deep_link_output.getvalue())
        assert deep_link_resolution["goal_id"] == "goal", deep_link_resolution
        assert deep_link_resolution["agent_id"] == "codex-a", deep_link_resolution
        assert deep_link_resolution["host_surface"] is None, deep_link_resolution
        assert deep_link_resolution["host_family"] == "codex", deep_link_resolution
        assert deep_link_resolution["matched_host_surfaces"] == ["codex-app"], (
            deep_link_resolution
        )
        assert deep_link_resolution["session_locator"]["authority"] == "locator_only", (
            deep_link_resolution
        )

        unbound = build_start_goal_guided_packet(
            project=project,
            goal_id="goal",
            agent_id=None,
            thread_id="thread-b",
            cli_bin="loopx",
            host_surface="codex-app",
            goal_text="start another task",
        )
        gate = unbound["guided_transaction"]["identity_selection_gate"]
        assert gate["default_action"] == "register_fresh_agent", gate
        registration = gate["fresh_agent_registration"]
        assert registration["mode"] == "register_fresh_agent"
        assert registration["recommended"] is True
        assert registration["continuation_contract"]["requires_execute_result"] is True
        assert registration["continuation_contract"]["required_result"] == {
            "ok": True, "changed": True, "written": True,
            "global_sync": {"ok": True}, "registration_readback": {"verified": True},
        }
        assert {choice["agent_id"] for choice in gate["choices"]} == {
            "codex-a",
            "codex-b",
        }, gate
        assert all(
            choice["requires_explicit_takeover_intent"] is True
            for choice in gate["choices"]
        ), gate

        register_output = io.StringIO()
        with contextlib.redirect_stdout(register_output):
            register_exit = cli_main(
                [
                    "--runtime-root",
                    str(runtime),
                    "--format",
                    "json",
                    "register-agent",
                    "--goal-id",
                    "goal",
                    "--agent-id",
                    "codex-c",
                    "--require-new",
                    "--execute",
                ]
            )
        assert register_exit == 0, register_output.getvalue()
        registered = json.loads(register_output.getvalue())
        assert registered["ok"] is True, registered
        assert registered["changed"] is True, registered
        assert registered["registration_readback"]["verified"] is True, registered

        selected_fresh = build_start_goal_guided_packet(
            project=project,
            goal_id="goal",
            agent_id="codex-c",
            thread_id="thread-b",
            cli_bin="loopx",
            host_surface="codex-app",
            goal_text="start another task",
        )
        fresh_bind_step = next(
            step
            for step in selected_fresh["guided_transaction"]["ordered_steps"]
            if step["id"] == "bind_thread_identity"
        )
        fresh_bind_output = io.StringIO()
        fresh_bind_argv = shlex.split(fresh_bind_step["command"])
        with contextlib.redirect_stdout(fresh_bind_output):
            fresh_bind_exit = cli_main(
                ["--runtime-root", str(runtime), *fresh_bind_argv[1:]]
            )
        assert fresh_bind_exit == 0, fresh_bind_output.getvalue()

        reused_fresh = build_start_goal_guided_packet(
            project=project,
            goal_id="goal",
            agent_id=None,
            thread_id="thread-b",
            cli_bin="loopx",
            host_surface="codex-app",
            goal_text="continue the new session",
        )
        assert reused_fresh["agent_id"] == "codex-c", reused_fresh
        assert reused_fresh["thread_agent_binding"]["status"] == "bound", reused_fresh

        unbind_output = io.StringIO()
        with contextlib.redirect_stdout(unbind_output):
            unbind_exit = cli_main(
                [
                    "--runtime-root",
                    str(runtime),
                    "--format",
                    "json",
                    "unbind-agent-thread",
                    "--goal-id",
                    "goal",
                    "--thread-id",
                    "thread-b",
                    "--host-surface",
                    "codex-app",
                    "--agent-id",
                    "codex-c",
                    "--execute",
                ]
            )
        assert unbind_exit == 0, unbind_output.getvalue()
        unbound = json.loads(unbind_output.getvalue())
        assert unbound["registration_readback"]["verified"] is True, unbound
        assert unbound["binding"]["status"] == "missing", unbound

        rebind_output = io.StringIO()
        with contextlib.redirect_stdout(rebind_output):
            rebind_exit = cli_main(
                [
                    "--runtime-root",
                    str(runtime),
                    "--format",
                    "json",
                    "bind-agent-thread",
                    "--goal-id",
                    "goal",
                    "--thread-id",
                    "thread-b",
                    "--host-surface",
                    "codex-app",
                    "--agent-id",
                    "codex-b",
                    "--execute",
                ]
            )
        assert rebind_exit == 0, rebind_output.getvalue()
        rebound = json.loads(rebind_output.getvalue())
        assert rebound["binding"]["agent_id"] == "codex-b", rebound

        source_after = json.loads(registry.read_text(encoding="utf-8"))
        bindings_after = source_after["goals"][0]["coordination"][
            "thread_agent_bindings"
        ]
        assert {
            "thread_id": "thread-a",
            "host_surface": "codex-app",
            "agent_id": "codex-a",
        } in bindings_after, bindings_after
        assert {
            "thread_id": "thread-b",
            "host_surface": "codex-app",
            "agent_id": "codex-b",
        } in bindings_after, bindings_after
        assert (
            "codex-c" in source_after["goals"][0]["coordination"]["registered_agents"]
        ), source_after

        fresh = build_start_goal_guided_packet(
            project=project,
            goal_id="goal",
            agent_id=None,
            thread_id=None,
            cli_bin="loopx",
            host_surface="codex-app",
            goal_text="start a genuinely new peer",
            new_peer=True,
        )
        fresh_gate = fresh["guided_transaction"]["identity_selection_gate"]
        assert fresh_gate["default_action"] == "register_fresh_agent", fresh_gate
        assert fresh_gate["fresh_agent_registration"]["recommended"] is True, fresh_gate
    print("codex-app-thread-agent-identity-smoke ok")


if __name__ == "__main__":
    main()
