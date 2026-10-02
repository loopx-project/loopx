#!/usr/bin/env python3
"""Run the public canary-promotion readiness smoke group.

This command is a preflight for promoting the live checkout into the default
local release snapshot. It validates the public boundary, status projections,
installer wrappers, and dashboard demo-readiness path without mutating the
installed release. By default, a successful run appends one public-safe
promotion-readiness evidence event to the runtime release ledger so status,
doctor, and quota guards can clear stale or missing readiness warnings without
requiring a project Goal.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_DIR = REPO_ROOT / "apps" / "presentation" / "dashboard"

COMMON_NODE_PATHS = [
    "/opt/homebrew/bin",
    "/usr/local/bin",
]
BASE_COMMANDS = [
    (
        "public boundary contract",
        [sys.executable, "-m", "loopx.cli", "check", "--scan-path", str(REPO_ROOT)],
    ),
    ("status markdown projection", [sys.executable, "examples/control_plane/status-markdown-smoke.py"]),
    ("usage/event/decision projections", [sys.executable, "examples/usage-summary-smoke.py"]),
    ("installer release/canary wrappers", [sys.executable, "examples/install-local-smoke.py"]),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dashboard-mode",
        choices=("auto", "require", "skip"),
        default="auto",
        help=(
            "Dashboard readiness policy: auto runs it when apps/presentation/dashboard is present, "
            "require fails if the dashboard app is absent, and skip records the release-boundary omission."
        ),
    )
    parser.add_argument(
        "--include-browser",
        action="store_true",
        help="Also run browser-backed dashboard demo-readiness smokes.",
    )
    parser.add_argument(
        "--no-write-evidence",
        action="store_true",
        help="Run checks only; do not append the promotion-readiness evidence event.",
    )
    return parser.parse_args()


def build_env() -> dict[str, str]:
    path_parts = [os.environ.get("PATH", "")]
    path_parts.extend(path for path in COMMON_NODE_PATHS if Path(path).exists())
    return {
        **os.environ,
        "PATH": ":".join(part for part in path_parts if part),
        "PYTHONPATH": str(REPO_ROOT),
    }


def run_command(label: str, command: list[str], env: dict[str, str]) -> None:
    print(f"[canary-promotion] {label}: {' '.join(command)}", flush=True)
    subprocess.run(command, cwd=REPO_ROOT, env=env, check=True)


def write_readiness_evidence(
    env: dict[str, str],
    *,
    dashboard_skipped: bool,
) -> None:
    command = [
        sys.executable,
        "-m",
        "loopx.cli",
        "promotion-readiness",
        "record",
        "--dashboard-readiness",
        "skipped" if dashboard_skipped else "passed",
        "--execute",
    ]
    run_command(
        "promotion readiness evidence writeback",
        command,
        env,
    )


def dashboard_readiness_plan(
    *,
    dashboard_dir: Path = DASHBOARD_DIR,
    dashboard_mode: str = "auto",
    include_browser: bool = False,
) -> dict[str, object]:
    has_dashboard = (dashboard_dir / "package.json").is_file()
    if dashboard_mode == "skip":
        return {
            "status": "skip",
            "reason": "dashboard readiness explicitly skipped",
            "command": None,
        }
    if has_dashboard:
        command = [
            sys.executable,
            "examples/dashboard-demo-readiness-smoke.py",
            "--require-dependencies",
        ]
        if not include_browser:
            command.append("--skip-browser")
        return {
            "status": "run",
            "reason": None,
            "command": command,
        }
    reason = (
        "apps/presentation/dashboard is not present in this checkout or release snapshot; "
        "run from a source checkout or pass --dashboard-mode=skip to omit it intentionally"
    )
    if dashboard_mode == "require" or include_browser:
        return {
            "status": "fail",
            "reason": reason,
            "command": None,
        }
    return {
        "status": "skip",
        "reason": reason,
        "command": None,
    }


def main() -> int:
    args = parse_args()
    env = build_env()
    commands = list(BASE_COMMANDS)
    dashboard_plan = dashboard_readiness_plan(
        dashboard_mode=args.dashboard_mode,
        include_browser=args.include_browser,
    )
    if dashboard_plan["status"] == "fail":
        raise SystemExit(str(dashboard_plan["reason"]))
    if dashboard_plan["status"] == "run":
        dashboard_command = dashboard_plan["command"]
        assert isinstance(dashboard_command, list)
        commands.append(("dashboard demo readiness", dashboard_command))
    else:
        print(
            f"[canary-promotion] dashboard demo readiness: skipped ({dashboard_plan['reason']})",
            flush=True,
        )

    for label, command in commands:
        run_command(label, command, env)

    evidence_suffix = " without evidence writeback"
    if not args.no_write_evidence:
        write_readiness_evidence(
            env,
            dashboard_skipped=dashboard_plan["status"] == "skip",
        )
        evidence_suffix = " with evidence writeback"

    if dashboard_plan["status"] == "skip":
        suffix = " with dashboard readiness skipped"
    else:
        suffix = " with browser smokes" if args.include_browser else " without browser smokes"
    print(f"canary-promotion-readiness-smoke ok{suffix}{evidence_suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
