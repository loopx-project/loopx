#!/usr/bin/env python3
"""Connect a project to loopx for Claude Code (PROJECT-scoped).

Thin wrapper: (1) `loopx connect` the repo, (2) mark `agent_backends += claude`
in the project registry, (3) install the Claude adapter at PROJECT scope (the
loopx MCP + the `/loopx` command into this project's `.claude/`) by delegating to
install.py. No global config, no OS sandbox. The run loop is native `/loop`.

  python connect.py --project DIR --goal-id G [--objective "…"] [--harden]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from loopx.command_invocation import pinned_python_module_command
from loopx.control_plane.projects.registry_codec import (
    ProjectRegistryProtocolError,
    add_project_registry_backend,
    load_project_registry,
    require_runtime_compatible_project_registry,
)

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _gh_prefix():
    return pinned_python_module_command("loopx.cli")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--goal-id", required=True)
    ap.add_argument("--objective", default=None)
    ap.add_argument("--registry", default=None)
    ap.add_argument("--harden", action="store_true",
                    help="also install the optional PreToolUse should_run gate + statusline (project scope)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    proj = Path(a.project).resolve()
    dry = a.dry_run

    # 1. connect the repo to loopx (optional, when an objective is given)
    if a.objective:
        cmd = list(_gh_prefix()) + (["--registry", a.registry] if a.registry else []) + \
              ["connect", "--goal-id", a.goal_id, "--objective", a.objective,
               "--state-file", f".claude/goals/{a.goal_id}/ACTIVE_GOAL_STATE.md"]
        print("[connect]", " ".join(cmd))
        if not dry:
            try:
                rc = subprocess.run(cmd, cwd=str(proj), timeout=120)
            except Exception as e:
                print(f"[connect] FAILED to run loopx connect: {e}")
                sys.exit(1)
            if rc.returncode != 0:
                # Fail closed: a non-zero connect (e.g. a global route collision)
                # must stop the helper rather than print a misleading "next steps".
                print(f"[connect] FAILED (exit {rc.returncode}) — not continuing. "
                      "Resolve the error above and re-run.")
                sys.exit(rc.returncode)

    # 2. mark agent_backends += claude in the project registry (additive)
    if a.registry:
        reg = Path(a.registry)
    else:
        reg = proj / ".loopx" / "registry.json"
        if not reg.exists() and (proj / ".goal-harness" / "registry.json").exists():
            reg = proj / ".goal-harness" / "registry.json"
    if reg.exists():
        try:
            payload = load_project_registry(reg)
            require_runtime_compatible_project_registry(
                payload,
                operation="Claude Goal adapter",
            )
            print(f"[registry] mark agent_backends += claude  ({reg})")
            if not dry:
                add_project_registry_backend(reg, "claude")
        except ProjectRegistryProtocolError as e:
            print(f"[registry] FAILED: {e}")
            sys.exit(1)
        except Exception as e:
            print("  (registry annotate skipped:", e, ")")
    else:
        print(f"[registry] {reg} not found yet (run with --objective to connect first)")

    # 3. install the Claude adapter at PROJECT scope (MCP + /loopx command [+ --harden])
    installer = PLUGIN_ROOT / "scripts" / "install.py"
    inst = [sys.executable, str(installer), "--scope", "project", "--project", str(proj)]
    if a.harden:
        inst.append("--harden")
    if dry:
        inst.append("--dry-run")
    print("[install]", " ".join(inst))
    ri = subprocess.run(inst, cwd=str(proj))
    if ri.returncode != 0:
        # Fail closed: don't claim success / print next steps if the adapter
        # install failed.
        print(f"[install] FAILED (exit {ri.returncode}).")
        sys.exit(ri.returncode)

    print("\nNext: open Claude Code in the project, then:")
    print("    /loopx <task>     # set the goal, write .claude/loop.md, do the first segment")
    print("    /loop             # drive the loop (native; loopx should_run gates each tick)")
    print("    /loopx off        # remove the loop protocol")


if __name__ == "__main__":
    main()
