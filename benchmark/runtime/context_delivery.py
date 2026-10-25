"""Explicit trial-local consumer of the product's native context transport.

No packet semantics live here. The existing worker stages exact product hooks
and trusts only their native hashes; quota owns projection and full recovery.
"""
from __future__ import annotations

import json
import os
import stat
import shlex
from pathlib import Path


def preflight_context_instruction(env: dict[str, str], path: Path, digest: str, turn_id: str) -> str:
    """Delay presentation of the original preflight until its native tool call.

    Keep the original source/hash. The product's lifecycle owner chooses the
    full/delta view when the native tool actually presents it.
    """
    from loopx.command_invocation import loopx_cli_argv

    prefix = [*loopx_cli_argv(env["LOOPX_CLI"]), "--format", "json", "quota"]
    source = ["--capture-file", str(path), "--capture-sha256", digest]
    project = [*prefix, "context-project", *source,
        "--goal-id", env["LOOPX_GOAL_ID"], "--agent-id", env["LOOPX_AGENT_ID"],
        "--turn-instance-id", turn_id, "--context-delivery", "on", "--runtime-profile", "generic_cli", "--codex-context-dir", env["LOOPX_CODEX_CONTEXT_DIR"]]
    full = [*prefix, "context-read", *source]
    return ("Present that same immutable packet with:\n```sh\n" + shlex.join(project)
        + "\n```\nConsume any returned page content, then follow next_command through eof. "
        "If no page content is returned, run command and follow all next_command pages. Reconstruct any delta against "
        "its exact retained baseline before following the original interaction_contract. "
        "Normal completed reads use the explicit native-hook continuity assumption. "
        "If projection is unavailable, the baseline was invalidated, or a response is incomplete, "
        "follow its full recovery reference. If the projection command itself fails, read the original "
        "complete packet with:\n```sh\n" + shlex.join(full) + "\n```\nFollow every next_command. "
        "Neither presentation nor recovery renews this decision's authority or freshness. ")


def prepare_context_delivery(env: dict[str, str], home: Path) -> None:
    from loopx.control_plane.turn_driver.codex_context import (
        CONTEXT_LIFECYCLE_HOOKS, codex_context_configuration,
    )
    from loopx.control_plane.turn_driver.codex_stdio import StdioNativeGoalTransport

    root = home / "loopx-context"
    root.mkdir(mode=0o700, exist_ok=True)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("context delivery requires a private, physical trial directory")
    env["LOOPX_CODEX_CONTEXT_DIR"] = str(root)
    configuration = codex_context_configuration(str(root), goal_id=env["LOOPX_GOAL_ID"],
        agent_id=env["LOOPX_AGENT_ID"], scenario="codex_benchmark_heartbeat", cli_argv=[env["LOOPX_CLI"], "--cli-bin", env["LOOPX_CLI"]])
    content = json.dumps({"hooks": configuration["hooks"]})
    path = home / "hooks.json"
    if path.is_symlink() or (path.exists() and path.read_text() != content):
        raise ValueError("native context hooks changed within a trial; use a new trial")
    if not path.exists():
        path.write_text(content)
        path.chmod(0o600)
    # The native host is the hash/key owner. Do not duplicate its hashing or
    # bypass trust for unrelated workspace hooks. Discovery launches no model.
    with (home / "context-hooks-stderr.log").open("a") as stderr:
        with StdioNativeGoalTransport.spawn(
            # This process only reads native hook identities. Catalog/plugin
            # startup can clone repositories into an otherwise private home;
            # it is unrelated to discovery and must not affect solver config.
            [env["CODEX_BIN"], "-c", "features.plugins=false",
             "-c", "features.remote_plugin=false", "-c", "features.plugin_sharing=false",
             "app-server", "--listen", "stdio://"],
            cwd=env["LOOPX_PROJECT"], env=env, stderr=stderr, response_timeout_sec=10,
        ) as native:
            native.request("initialize", {"clientInfo": {"name": "loopx_context_transport", "version": "0.1.0"},
                                          "capabilities": {"experimentalApi": True}})
            native.notify("initialized", {})
            listed = native.request("hooks/list", {"cwds": [env["LOOPX_PROJECT"]]})
    hooks = [hook for entry in listed["data"] for hook in entry["hooks"]
             if hook.get("sourcePath") == str(path.resolve())]
    if len(hooks) != len(CONTEXT_LIFECYCLE_HOOKS) or path.read_text() != content:
        raise ValueError("native host did not discover the exact trial context hooks")
    entries = []
    for hook in hooks:
        key, digest = hook.get("key"), hook.get("currentHash")
        if not isinstance(key, str) or not key or not isinstance(digest, str) or not digest:
            raise ValueError("native host returned no context-hook trust identity")
        entries.append(f"{json.dumps(key)} = {{ trusted_hash = {json.dumps(digest)} }}")
    # CLI override keys are dot-separated; native keys contain paths/dots.
    # Carry the entire table as TOML so native keys remain literal strings.
    # This isolated trial's native developer instructions survive compaction;
    # a failed SessionStart handler cannot erase the read-only recovery route.
    native_args = ["-c", "hooks.state={" + ",".join(entries) + "}",
        "-c", "developer_instructions=" + json.dumps(configuration["developer_instructions"])]
    env["LOOPX_CODEX_CONTEXT_TRUST_ARGS"] = json.dumps(native_args)
