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

from .native_hooks import prepare_native_hooks


def preflight_context_instruction(env: dict[str, str], path: Path, digest: str, turn_id: str, *,
                                  guide_in_host: bool = False) -> str:
    """Delay presentation of the original preflight until its native tool call.

    Keep the original source/hash. The product's lifecycle owner chooses the
    full/delta view when the native tool actually presents it.
    """
    from loopx.command_invocation import loopx_cli_argv
    from loopx.control_plane.turn_driver.decision_capture import CONTEXT_CAPTURE_NAVIGATION_GUIDANCE

    prefix = [*loopx_cli_argv(env["LOOPX_CLI"]), "--format", "json", "quota"]
    source = ["--capture-file", str(path), "--capture-sha256", digest]
    project = [*prefix, "context-project", *source,
        "--goal-id", env["LOOPX_GOAL_ID"], "--agent-id", env["LOOPX_AGENT_ID"],
        "--turn-instance-id", turn_id, "--initial-read", "--context-delivery", "on", "--runtime-profile", "generic_cli", "--codex-context-dir", env["LOOPX_CODEX_CONTEXT_DIR"]]
    receipt_reader = [*prefix, "context-read", *source, "--select", "/heartbeat_receipt"]
    return ("Run this command directly and deliver its complete bounded stdout to the model. "
        "Keep top-level content and next_command when present; navigation alone omits the returned page. "
        "Preserve any failure response.\n```sh\n" + shlex.join(project)
        + "\n```\n" + ("" if guide_in_host else "After projection succeeds: " + CONTEXT_CAPTURE_NAVIGATION_GUIDANCE)
        + "For known fields, replace --initial-read with repeated --select pointers in the first command. "
        "After a successful projection, continue the returned page or use navigation.recovery.command to reread. "
        "If projection fails, follow its recovery instructions and preserve the original receipt. "
        "If it provides no verified context or usable instructions, restore the intact capture and retry the same "
        "context-project with its original digest and Turn. Use this command for diagnostic receipt readback:\n```sh\n"
        + shlex.join(receipt_reader) + "\n```\n"
        "Use verified context before work. Do not rerun admission to recover presentation. "
        "Neither presentation nor recovery renews this decision's authority or freshness. ")


def prepare_context_delivery(env: dict[str, str], home: Path) -> None:
    from loopx.control_plane.turn_driver.codex_context import codex_context_configuration

    root = home / "loopx-context"
    root.mkdir(mode=0o700, exist_ok=True)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("context delivery requires a private, physical trial directory")
    env["LOOPX_CODEX_CONTEXT_DIR"] = str(root)
    configuration = codex_context_configuration(str(root), goal_id=env["LOOPX_GOAL_ID"],
        agent_id=env["LOOPX_AGENT_ID"], scenario="codex_benchmark_heartbeat", cli_argv=[env["LOOPX_CLI"], "--cli-bin", env["LOOPX_CLI"]])
    native_args = prepare_native_hooks(env, home, configuration["hooks"], purpose="context")
    # This isolated trial's native developer instructions survive compaction;
    # a failed SessionStart handler cannot erase the read-only recovery route.
    native_args += ["-c", "developer_instructions=" + json.dumps(configuration["developer_instructions"])]
    native_args += [argument for key, value in configuration["config_overrides"].items()
        for argument in ("-c", key + "=" + json.dumps(value))]
    env["LOOPX_CODEX_CONTEXT_TRUST_ARGS"] = json.dumps(native_args)
