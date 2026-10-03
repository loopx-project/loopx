#!/usr/bin/env python3
"""Smoke-test that auto-research worker-loop is not a fake metric generator."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from demo.auto_research.demo_e2e import _seed_visible_demo_control_plane  # noqa: E402
from demo.auto_research.demo_supervisor import (  # noqa: E402
    build_auto_research_demo_supervisor_plan,
)
from loopx.state_projection import state_projection_gap_warning  # noqa: E402


GOAL_ID = "loopx-auto-research-demo"
AGENT_IDS = [
    "research-curator",
    "hypothesis-proposer",
    "research-executor",
    "evaluator-promoter",
]
LANES = [
    "research-curator:research-curator:research_curator",
    "hypothesis-proposer:hypothesis-proposer:hypothesis_proposer",
    "research-executor:research-executor:research_executor",
    "evaluator-promoter:evaluator-promoter:evaluator_promoter",
]


def assert_public_safe(payload: Any) -> None:
    text = json.dumps(payload, sort_keys=True) if not isinstance(payload, str) else payload
    forbidden = [
        "/" + "Users/",
        "/" + "private/",
        "/" + "tmp/",
        "http" + "://",
        "https" + "://",
        "api" + "_key",
        "pass" + "word",
        "sec" + "ret",
    ]
    leaked = [needle for needle in forbidden if needle.lower() in text.lower()]
    assert not leaked, leaked


def main() -> int:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        supervisor = build_auto_research_demo_supervisor_plan(
            goal_id=GOAL_ID,
            agent_specs=LANES,
            session_name="loopx-auto-research-worker-loop-smoke",
            cli_bin="loopx",
            codex_bin="codex",
            tmux_bin="tmux",
            reasoning_effort="high",
        )
        _visible_control, registry, runtime_root = _seed_visible_demo_control_plane(
            demo_root=temp,
            goal_id=GOAL_ID,
            objective="Verify worker-loop cannot manufacture auto-research evidence.",
            supervisor=supervisor,
        )
        state_file = (
            temp
            / "visible-control-plane"
            / ".loopx"
            / "goals"
            / GOAL_ID
            / "ACTIVE_GOAL_STATE.md"
        )
        state_text = state_file.read_text(encoding="utf-8")
        assert "Run `loopx check` against the project registry" not in state_text
        assert (
            "Goal-level route delegates to role frontier; panes own execution."
            in state_text
        )
        route_only_state = (
            "## Agent Todo\n\n"
            "- [x] Complete role-frontier auto-research work.\n\n"
            "## Next Action\n\n"
            "- Goal-level route delegates to role frontier; panes own execution.\n"
        )
        assert state_projection_gap_warning(route_only_state) is None

        workspace = temp / "shared-research-workspace"
        workspace.mkdir()
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONPATH"] = f"{REPO_ROOT}{os.pathsep}{env.get('PYTHONPATH', '')}"
        args = [
            sys.executable,
            "-m",
            "loopx.cli",
            "--registry",
            str(registry),
            "--runtime-root",
            str(runtime_root),
            "--format",
            "json",
            "auto-research",
            "worker-loop",
            "--goal-id",
            GOAL_ID,
            "--lane-count",
            str(len(AGENT_IDS)),
            "--max-rounds",
            "1",
            "--visible-lanes-accepted",
            "--complete-selected-todo",
            "--execute",
        ]
        for agent_id in AGENT_IDS:
            args.extend(["--agent-id", agent_id])
        result = subprocess.run(
            args,
            cwd=workspace,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise AssertionError(
                f"worker-loop failed rc={result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}"
            )
        payload = json.loads(result.stdout)
        assert payload["ok"] is True, payload
        assert payload["schema_version"] == "auto_research_worker_loop_v0", payload
        assert payload["mode"] == "execute", payload
        assert payload["max_rounds"] == 1, payload
        assert payload["turn_count"] == 4, payload
        assert payload["stop_reason"] == "no_executed_turns", payload
        assert payload["selected_actions"] == ["write_research_contract"], payload

        manual_turns = [
            turn for turn in payload["turns"] if turn.get("mode") == "manual_research_required"
        ]
        assert [turn["agent_id"] for turn in manual_turns] == ["research-curator"], payload
        assert [turn["selected_action"] for turn in manual_turns] == [
            "write_research_contract"
        ], payload
        no_action_turns = [turn for turn in payload["turns"] if turn.get("mode") == "no_action"]
        assert [turn["agent_id"] for turn in no_action_turns] == [
            "hypothesis-proposer",
            "research-executor",
            "evaluator-promoter",
        ], payload
        assert no_action_turns[0]["selected_action"] is None, payload
        assert all(turn["executed"] is False for turn in manual_turns), payload
        assert all(turn.get("completion_status") is None for turn in manual_turns), payload
        assert all(turn.get("dev_metric") is None for turn in payload["turns"]), payload
        assert all(turn.get("holdout_metric") is None for turn in payload["turns"]), payload
        assert all("demo_iteration" not in turn for turn in payload["turns"]), payload
        assert payload["executed_turn_count"] == 0, payload
        assert payload["completed_turn_count"] == 0, payload
        assert_public_safe(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
