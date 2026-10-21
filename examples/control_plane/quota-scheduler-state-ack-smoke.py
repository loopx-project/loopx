#!/usr/bin/env python3
"""Smoke-test one public CLI scheduler RRULE ACK round trip."""

from __future__ import annotations

from pathlib import Path
import os
import sys
import tempfile
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from examples.control_plane.quota_plan_fixtures import (  # noqa: E402
    SCOPED_AGENT_ID,
    write_cli_fixture,
    write_codex_heartbeat_fixture,
)
from loopx.control_plane.testing.canary_harness import run_json_cli  # noqa: E402


GOAL_ID = "needs-operator"


def run_should_run(
    *,
    registry_path: Path,
    runtime_root: Path,
    project: Path,
) -> dict:
    return run_json_cli(
        "quota",
        "should-run",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        SCOPED_AGENT_ID,
        "--codex-app",
        registry_path=registry_path,
        runtime_root=runtime_root,
        cwd=REPO_ROOT,
    )


def assert_cli_scheduler_ack_public_round_trip() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-quota-scheduler-ack-") as tmp, patch.dict(os.environ):
        codex_home = Path(tmp) / "codex-home"
        os.environ.update(CODEX_HOME=str(codex_home), CODEX_THREAD_ID="scheduler-fixture")
        write_codex_heartbeat_fixture(codex_home, goal_id=GOAL_ID, agent_id=SCOPED_AGENT_ID, rrule="FREQ=MINUTELY;INTERVAL=3")
        registry_path, runtime_root, project = write_cli_fixture(
            Path(tmp),
            scoped_agents=True,
        )
        before = run_should_run(
            registry_path=registry_path,
            runtime_root=runtime_root,
            project=project,
        )
        before_app = before["scheduler_hint"]["codex_app"]
        target_rrule = before_app["recommended_rrule"]
        ack_hint = before_app["ack_hint"]
        assert before_app["stateful_backoff"]["apply_needed"] is True, before
        assert ack_hint["command"] == "quota scheduler-ack-current", before
        assert ack_hint["args"]["applied_rrule"] == target_rrule, before
        assert ack_hint["args"]["host_match_observed"] is True, before

        write_codex_heartbeat_fixture(codex_home, goal_id=GOAL_ID, agent_id=SCOPED_AGENT_ID, rrule=target_rrule)
        ack = run_json_cli(
            *ack_hint["cli_args"],
            registry_path=registry_path,
            runtime_root=runtime_root,
            cwd=REPO_ROOT,
        )
        scheduler_state = ack["scheduler_ack_event"]["scheduler_state"]
        assert ack["ok"] is True, ack
        assert ack["scheduler_state_mutated"] is True, ack
        assert scheduler_state["last_applied_rrule"] == target_rrule, ack
        assert ack["post_ack_contract"][
            "do_not_apply_successor_rrule_from_ack_response"
        ] is True, ack

        settled = run_should_run(
            registry_path=registry_path,
            runtime_root=runtime_root,
            project=project,
        )
        settled_app = settled["scheduler_hint"]["codex_app"]
        assert settled_app["stateful_backoff"]["state_status"] == "same_identity"
        assert settled_app["stateful_backoff"]["current_rrule"] == target_rrule
        assert settled_app["stateful_backoff"]["apply_needed"] is False
        assert settled_app["stateful_backoff"]["ack_needed"] is False
        assert settled_app["host_action"] == "none"
        assert "recommended_rrule" not in settled_app


def main() -> int:
    assert_cli_scheduler_ack_public_round_trip()
    print("quota-scheduler-state-ack-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
