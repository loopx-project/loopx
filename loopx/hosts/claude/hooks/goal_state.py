#!/usr/bin/env python3
"""Goal-mode context resolution — registry-driven, mirroring the Codex model.

LoopX's registry (``.loopx/registry.json``) is the single source of truth for the
project's goal (goal_id / agent / scope), exactly as on Codex. We resolve it by
walking up from cwd to the nearest ``.loopx/`` (or legacy ``.goal-harness/``)
registry — so two sessions in different projects are independent, and two
sessions in the SAME project share the one goal.

"Goal-mode ON / armed" for a project = a ``.claude/loop.md`` exists (written by
``/loopx <task>``). That file is the per-iteration protocol that Claude Code's
native ``/loop`` runs; its presence means loopx is driving this project. The
OPTIONAL PreToolUse hook + statusline gate on this, so they only act where loopx
is actually active. ``LOOPX_GOAL_FORCE=1`` forces armed=true for tests.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.goal_mode_context import (  # noqa: E402
    find_registry as _find_registry,
    first_registered_agent,
    project_root_for as _project_root_for,
    resolve_goal_context,
)

_ARMED_RE = re.compile(r"<!--\s*loopx:armed\s*(\{.*?\})\s*-->")


def find_registry(cwd) -> Path | None:
    """Nearest ancestor of cwd (inclusive) holding a registry.json, else None."""
    return _find_registry(cwd)


def project_root_for(cwd) -> Path | None:
    return _project_root_for(cwd)


def _agent_of(goal: dict):
    return first_registered_agent(goal)


def loop_md_path(project_root) -> Path:
    return Path(project_root) / ".claude" / "loop.md"


def read_armed_goal(project_root) -> dict | None:
    """The goal_id/agent_id that `/loopx` armed for THIS project, parsed from the
    `loopx:armed` marker it writes at the top of `.claude/loop.md`. None if the
    project isn't armed or the marker is absent/unparseable.

    This is what lets goal_context resolve the *armed* goal rather than guessing
    by registry order, so a multi-goal repo gates/claims/completes the goal the
    user actually set up."""
    if not project_root:
        return None
    try:
        text = loop_md_path(project_root).read_text(encoding="utf-8")
    except Exception:
        return None
    m = _ARMED_RE.search(text)
    if not m:
        return None
    try:
        d = json.loads(m.group(1))
    except Exception:
        return None
    return d if isinstance(d, dict) and d.get("goal_id") else None


def goal_context(cwd) -> dict | None:
    """The current project's goal, read live from the registry. None if no goal.

    Returns goal_id, registry path, agent_id, write_scope (the goal's repo), and
    project_root — everything the hooks / MCP / statusline need. When the project
    is armed (`.claude/loop.md` carries a `loopx:armed` marker), the marker's goal
    is authoritative; otherwise we fall back to the first registry goal."""
    root = project_root_for(cwd)
    armed = read_armed_goal(root) if root else None
    return resolve_goal_context(
        cwd,
        preferred_goal_id=str((armed or {}).get("goal_id") or "") or None,
        preferred_agent_id=str((armed or {}).get("agent_id") or "") or None,
    )


def is_armed(project_root) -> bool:
    """Armed = the project has a `.claude/loop.md` (loopx is driving), or forced."""
    if os.environ.get("LOOPX_GOAL_FORCE") == "1":
        return True
    return bool(project_root) and loop_md_path(project_root).exists()


def active_context(cwd) -> dict | None:
    """goal_context for cwd, but only when goal-mode is armed; else None."""
    ctx = goal_context(cwd)
    if not ctx or not is_armed(ctx.get("project_root")):
        return None
    return ctx
