#!/usr/bin/env python3
"""Statusline segment: a glanceable loopx goal state for the Claude Code CLI.

Claude Code feeds session JSON on stdin; we resolve THIS project's goal (only
while goal-mode is armed) and run one quick `loopx quota should-run` probe, then
render a single compact line. We surface the signals a watching user actually
needs, in priority order:

  1. ⚠ needs you   — an operator gate / open user todo is blocking (highest signal)
  2. ▶ working      — should_run=true, plus the next concrete action loopx picked
  3. ⏸ <state>      — paused/holding, plus the short reason (quota, gate, health…)

…always tailed with todo progress (done✓/open▸). Prints nothing when there is no
goal here or goal-mode is off.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# Share the registry-driven resolver with the hooks so the statusline shows THIS
# session's project goal, and only while goal-mode is armed (heartbeat installed).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))
from goal_state import active_context
from stdio_utf8 import pin_utf8_stdio


import shutil as _shutil
def _gh_prefix():
    _exe = _shutil.which("loopx")
    return [_exe] if _exe else [__import__("sys").executable, "-m", "loopx.cli"]


def _clip(s, n: int) -> str:
    s = " ".join(str(s or "").split())  # collapse whitespace/newlines
    return s if len(s) <= n else s[: n - 1] + "…"


def _render(gid: str, d: dict) -> str:
    """Turn a `quota should-run` payload into one compact statusline string."""
    agent = d.get("agent_todo_summary") or {}
    done, open_ = agent.get("done_count"), agent.get("open_count")
    prog = f"{done}✓/{open_}▸" if (done is not None or open_ is not None) else ""

    ic = d.get("interaction_contract") or {}
    user_ch = ic.get("user_channel") or {}
    users = d.get("user_todo_summary") or {}
    gate = d.get("gate_prompt")
    needs_user = bool(gate) or bool(user_ch.get("action_required")) or bool(users.get("open_count"))

    if needs_user:
        msg = gate or f"{users.get('open_count') or 'a'} user todo(s) to answer"
        return f"[loopx {gid} · ⚠ needs you: {_clip(msg, 46)}]"

    if d.get("should_run") is True:
        nxt = d.get("recommended_action")
        head = f"▶ {prog}".rstrip() if prog else "▶ working"
        return f"[loopx {gid} · {head}" + (f" · next: {_clip(nxt, 40)}]" if nxt else "]")

    # paused / holding: lead with WHY (state + short reason), then progress
    state = (d.get("state") or "paused").strip()
    reason = d.get("reason")
    tail = f" · {prog}" if prog else ""
    return f"[loopx {gid} · ⏸ {state}" + (f": {_clip(reason, 38)}" if reason else "") + f"{tail}]"


def main():
    raw = sys.stdin.read()  # Claude's session json carries this session's cwd
    cwd = None
    try:
        sess = json.loads(raw or "{}")
        cwd = sess.get("cwd") or (sess.get("workspace") or {}).get("current_dir")
    except Exception:
        pass
    st = active_context(cwd or Path.cwd())
    if not st:
        print("")  # no goal here, or goal-mode off -> no segment
        return
    gid = st.get("goal_id", "?")
    try:
        cmd = list(_gh_prefix())
        if st.get("registry"):
            cmd += ["--registry", st["registry"]]
        cmd += ["--format", "json", "quota", "should-run", "--goal-id", gid]
        if st.get("agent_id"):
            cmd += ["--agent-id", st["agent_id"]]
        out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=8)
        d = json.loads(out.stdout or "{}")
        print(_render(gid, d))
    except Exception:
        # probe failed: still show that goal-mode is armed, without counts/state
        print(f"[loopx {gid}]")


if __name__ == "__main__":
    pin_utf8_stdio()
    main()
