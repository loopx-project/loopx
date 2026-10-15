#!/usr/bin/env python3
"""Smoke-test the Codex sub-agent shared-control-plane contract."""

from __future__ import annotations

import json
from pathlib import Path
import re

from loopx.control_plane.turn_driver.subagent_host_adapter import (
    project_child_context_adapter,
    supported_child_context_modes,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DOC = REPO_ROOT / "docs" / "integrations" / "codex-subagent-orchestration.md"
CODEX_HOST_CONTRACT = REPO_ROOT / "examples" / "fixtures" / (
    "codex-spawn-agent-contract.public.json"
)

REQUIRED_PHRASES = (
    "shared control plane",
    "subagent_control_plane_handoff_v0",
    "`parent_goal_id`",
    "`authority_artifact`",
    "`latest_state_ref`",
    "`quota_gate_snapshot`",
    "`evidence_boundary`",
    "`writeback_spend_contract`",
    "`child_guard_policy`",
    "`prevention_first_v0`",
    "temporary task coordinator",
    "child worker reports evidence only; task coordinator writes accepted state and spends",
    "control_plane_handoff_version",
    "It does not own durable goal authority",
    "one pending lease for `(goal_id, todo_id)`",
    "`goal_id` is the shared control-plane lane",
    "`todo_id` is the work item being claimed",
    '"agent_model": "peer_v1"',
    "independent worktrees",
    "Review remains `action_kind=review`",
    "Only currently actionable candidates appear under `eligible_peer_lanes`.",
    "Closed, blocked, or deferred Todos are excluded.",
    "appears under `blocked_peer_lanes`",
)

FORBIDDEN_PHRASES = (
    "PRIVATE_HOME/",
    "lark" + "office.com",
    "~/.codex/sessions",
    "raw_thread",
    "session_history",
    "coordination.primary_agent",
    "primary-agent review todo",
    "side agents",
    "main controller",
    '"role": "controller"',
    '"role": "subagent"',
    "controller owns",
    "parent writes and spends",
)


def main() -> int:
    text = DOC.read_text(encoding="utf-8")
    compact = " ".join(text.split())
    for phrase in REQUIRED_PHRASES:
        assert phrase in compact, phrase
    for phrase in FORBIDDEN_PHRASES:
        assert phrase not in text, phrase
    host_contract = json.loads(CODEX_HOST_CONTRACT.read_text(encoding="utf-8"))
    expected_arguments = host_contract["context_mode_arguments"]
    # The table must describe the shipped adapters, not every schema mode.
    for label, host in (("Codex CLI", "codex-cli"), ("Claude Code", "claude-code")):
        row = next(line for line in text.splitlines() if line.startswith(f"| {label} |"))
        documented_modes = tuple(re.findall(r"`([^`]+)`", row.split("|")[2]))
        assert documented_modes == supported_child_context_modes(host), host
    codex_row = next(line for line in text.splitlines() if line.startswith("| Codex CLI |"))
    for mode, fork_turns in (
        ("fresh", "none"),
        ("forked_snapshot", "all"),
    ):
        adapter = project_child_context_adapter(host="codex-cli", context_mode=mode)
        assert adapter is not None
        assert adapter["native_operation"] == "spawn_agent"
        assert adapter["arguments"] == expected_arguments[mode]
        assert f'`fork_turns="{fork_turns}"`' in codex_row
    for forbidden_argument in host_contract["forbidden_arguments"]:
        assert forbidden_argument not in codex_row
        assert all(
            forbidden_argument not in arguments
            for arguments in expected_arguments.values()
        )
    assert text.count("subagent_control_plane_handoff_v0") >= 2, text
    assert text.count("## ") >= 7, text
    print("codex-subagent-orchestration-contract-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
