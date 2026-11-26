#!/usr/bin/env python3
"""Exercise the public KunlunCode Goal Pro app-server contract."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.hosts.kunluncode.app_server import (  # noqa: E402
    KUNLUN_APP_SERVER_PROTOCOL_VERSION,
    KunlunAppServerClient,
    build_app_server_command,
    compact_goal,
    native_goal_success,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kunluncode-bin", default="kunluncode")
    parser.add_argument(
        "--require",
        action="store_true",
        help="Fail instead of skipping when KunlunCode is unavailable.",
    )
    args = parser.parse_args()
    binary = shutil.which(args.kunluncode_bin)
    if binary is None:
        payload = {
            "ok": not args.require,
            "skipped": not args.require,
            "reason": "kunluncode is not on PATH",
        }
        print(json.dumps(payload, indent=2))
        return 1 if args.require else 0

    with tempfile.TemporaryDirectory(prefix="loopx-kunluncode-goal-pro-") as raw:
        project = Path(raw)
        command = build_app_server_command(
            binary,
            project=project,
            permission_mode="auto",
            max_duration_secs=60,
            scenario="goal-pro",
        )
        environment = dict(os.environ)
        with KunlunAppServerClient(command, cwd=project, env=environment) as client:
            initialized = client.initialize()
            thread_id = client.start_thread(project)
            client.set_goal(
                thread_id,
                objective=(
                    "Complete the deterministic native Goal Pro app-server protocol "
                    "validation without retaining raw execution output."
                ),
                mode="goal-pro",
            )
            client.start_turn(
                thread_id,
                "Complete the active native Goal Pro objective and request verification.",
            )
            goal = client.wait_for_goal_terminal(thread_id, timeout_seconds=30)
        with KunlunAppServerClient(command, cwd=project, env=environment) as client:
            client.initialize()
            client.resume_thread(thread_id)
            resumed_goal = client.get_goal(thread_id)

    success = bool(
        initialized.get("protocolVersion") == KUNLUN_APP_SERVER_PROTOCOL_VERSION
        and native_goal_success(goal, mode="goal-pro")
        and native_goal_success(resumed_goal, mode="goal-pro")
        and compact_goal(goal).get("goal_id")
        == compact_goal(resumed_goal).get("goal_id")
    )
    print(
        json.dumps(
            {
                "ok": success,
                "schema_version": "kunluncode_app_server_goal_pro_smoke_v0",
                "protocol_version": initialized.get("protocolVersion"),
                "terminal_goal": compact_goal(goal),
                "resumed_goal": compact_goal(resumed_goal),
                "boundary": {
                    "raw_output_recorded": False,
                    "raw_trajectory_recorded": False,
                    "credentials_recorded": False,
                    "absolute_paths_recorded": False,
                },
            },
            indent=2,
        )
    )
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
