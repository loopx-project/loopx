#!/usr/bin/env python3
"""Smart entry for the `/loopx` slash command — the loopx setup helper.

LoopX does NOT own the run loop on Claude Code. Claude Code's native `/loop` is
the scheduler/executor; loopx provides the control-plane protocol (the MCP tools
+ a per-iteration `.claude/loop.md`). `/loopx` just sets a goal up and hands off
to `/loop`.

Routing by first token:
  <free text task>  -> ONE-SHOT: ensure a goal exists for this project (bootstrap
                       if needed), register a default agent, add the task as a
                       todo, write `.claude/loop.md` (the protocol), and do one
                       bounded first segment. Then drive it with native `/loop`.
  (no args) / on    -> (re)write `.claude/loop.md` for this project's existing
                       goal and show how to drive it with `/loop`.
  status            -> the project's goal detail (objective, state, next, todos).
  off               -> remove `.claude/loop.md` (native `/loop` then has nothing
                       to run). Registry/goal are left intact.

The LoopX registry (`.loopx/registry.json`) is the single source of truth for
goal_id/agent/scope. The default agent `cc` is registered so the loopx identity
contract is active and `quota should-run --agent-id cc` is satisfied.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_AGENT = "cc"

# registry-driven context, shared with the hooks/MCP
sys.path.insert(0, str(HERE.parent / "hooks"))
from goal_state import goal_context, find_registry, loop_md_path  # noqa: E402
from stdio_utf8 import pin_utf8_stdio  # noqa: E402
from loopx.control_plane.heartbeat.rules import (  # noqa: E402
    HOST_LOOP_SAFETY_RULE,
    RUNTIME_REPAIR_ROUTING_RULE,
    SCOPE_BOUNDED_WORK_RULE,
)
from loopx.history import load_registry  # noqa: E402


def gh_prefix():
    """Cross-platform loopx invocation: the CLI shim if on PATH, else the module."""
    exe = shutil.which("loopx")
    return [exe] if exe else [sys.executable, "-m", "loopx.cli"]


def gh(args, cwd=None):
    return subprocess.run(gh_prefix() + args, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)


def slug(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower() or "project"
    return f"cc-{s}"[:48]


def loop_execution_content(goal_id, agent_id) -> str:
    """The per-iteration protocol that native `/loop` runs (written to
    .claude/loop.md). loopx's should_run is the deterministic per-tick gate; the
    agent uses the wired loopx MCP tools, never raw CLI guessing.

    The leading `loopx:armed` marker persists WHICH goal /loopx armed for this
    project, so goal_state.goal_context() resolves the right goal in multi-goal
    registries instead of guessing from registry order."""
    armed = json.dumps({"goal_id": goal_id, "agent_id": agent_id})
    return (
        f"<!-- loopx:armed {armed} -->\n"
        f"loopx tick — advance goal `{goal_id}` (agent `{agent_id}`). Use the wired loopx MCP\n"
        f"tools; do NOT run `loopx --help` or guess ids.\n\n"
        f"{HOST_LOOP_SAFETY_RULE}\n{RUNTIME_REPAIR_ROUTING_RULE}\n"
        "Read complete successful `should_run()` JSON each work iteration. Follow its\n"
        "current `interaction_contract`: selection/re-entry before admitted work,\n"
        "then validation and settlement. Never infer completion from an empty Todo list.\n"
        f"{SCOPE_BOUNDED_WORK_RULE}\n"
        "Honor claim/lease and user/repository authority; claim only when required.\n"
        "Run real acceptance checks before `complete_task`; supply truthful evidence\n"
        f"and the bound agent_id=\"{agent_id}\". Complete only finished Todos, not partial work.\n"
        "That MCP operation owns writeback/spend: use it INSTEAD OF the raw CLI sequence.\n"
        "Use interaction_contract.mcp_channel for tool ownership and vision input limits.\n"
        "At material delivery, compare the Goal's vision/acceptance with actual evidence.\n"
        "Pass the resulting agent_vision or a justified vision_unchanged_reason to\n"
        "complete_task. If omitted, call review_task_vision with only that Todo and\n"
        "Agent to read the basis; rejudge, then submit read_context_id with the decision.\n"
        "Stale/replaced receipts require rereading; lost replies retry the same receipt\n"
        "and decision. Recovery never spends again. This is not a Goal-stop\n"
        "shortcut: open acceptance needs replan; vision_closed closes a stage and needs\n"
        "a successor vision; no_followup requires evidence of no remaining scoped work.\n"
        "For new replan work not covered by these tools, use the exact live\n"
        "interaction_contract CLI actions, preserving its binding and settlement order.\n"
        "Link already planned follow-up via successor_todo_ids; next_agent_todo creates\n"
        "new work, not a reference to an existing id. Do not duplicate the current plan.\n"
        "After a lost response, read back or retry the same completion intent; do not\n"
        "invent a new successor or settlement identity. Recheck `should_run()` afterward.\n"
        "Continue authorized work while the live contract requires it; notification\n"
        "silence is not execution silence. Waiting is not completion: follow current\n"
        "host scheduling guidance without repeated unchanged polling. Terminal\n"
        "no-follow-up ends this Goal's work; cancel only its own recurring wakeup.\n"
        "Repair entrypoint errors within authority; an unavailable/incomplete contract\n"
        "permits neither work nor spending and must not be reported as completion.\n"
    )


def loop_md_content(goal_id, agent_id) -> str:
    from loopx.control_plane.heartbeat.bootstrap_prompt import BOOTSTRAP_INSTRUCTION
    armed = json.dumps({"goal_id": goal_id, "agent_id": agent_id})
    return (f"<!-- loopx:armed {armed} -->\nLoopX managed MCP bootstrap v1\n"
            "Each entry/resume: call the bound LoopX `host_prompt` MCP tool, "
            "verify its goal_id and agent_id match the armed binding above, "
            "then read its complete task_body. Do not create another Goal or scheduler.\n"
            f"{BOOTSTRAP_INSTRUCTION}\n")


def write_loop_md(proj: Path, goal_id, agent_id) -> Path:
    """Write the protocol to <project>/.claude/loop.md (bare `/loop` runs it)."""
    path = loop_md_path(proj)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(loop_md_content(goal_id, agent_id), encoding="utf-8")
    return path


def goal_detail(ctx):
    """Objective (from the registry goal entry's active-state file) + live state
    (from quota should-run) for the `/loopx status` detail view."""
    gid, reg, agent = ctx.get("goal_id"), ctx.get("registry"), ctx.get("agent_id")
    objective = ""
    if reg:
        try:
            regp = Path(reg)
            data = load_registry(regp)
            entry = next((g for g in data.get("goals", []) if g.get("id") == gid), None)
            sf = (entry or {}).get("state_file")
            if sf:
                text = (regp.parent.parent / sf).read_text(encoding="utf-8")
                m = re.search(r"^objective:\s*(.+)$", text, re.MULTILINE)
                if m:
                    objective = m.group(1).strip().strip('"').strip("'")
        except Exception:
            pass
    payload = {}
    try:
        cmd = gh_prefix() + (["--registry", reg] if reg else []) + \
            ["--format", "json", "quota", "should-run", "--goal-id", gid]
        if agent:
            cmd += ["--agent-id", agent]
        out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
        payload = json.loads(out.stdout or "{}")
    except Exception:
        pass
    return objective, payload


def print_status(ctx):
    gid = ctx.get("goal_id")
    objective, d = goal_detail(ctx)
    a = d.get("agent_todo_summary") or {}
    done, open_ = a.get("done_count"), a.get("open_count")
    gate = d.get("gate_prompt")
    if gate:
        state = f"⚠ needs you: {gate}"
    elif d.get("should_run") is True:
        state = "▶ running"
    else:
        state = f"⏸ {d.get('state') or 'paused'}" + (f" — {d['reason']}" if d.get("reason") else "")
    print(f"goal      : {gid}")
    print(f"agent     : {ctx.get('agent_id') or DEFAULT_AGENT}")
    if objective:
        print(f"objective : {objective}")
    print(f"state     : {state}")
    if d.get("recommended_action"):
        print(f"next      : {d['recommended_action']}")
    if done is not None or open_ is not None:
        print(f"todos     : {done} done / {open_} open")
    for it in (a.get("first_open_items") or [])[:8]:
        t = (it.get("text") or "").strip()
        if t:
            print(f"   ▸ {t}")
    armed = "armed (.claude/loop.md present)" if loop_md_path(Path.cwd()).exists() else "not armed (run /loopx to arm)"
    print(f"loop      : {armed} — drive with native /loop")


def main():
    args = sys.argv[1:]
    first = args[0] if args else None
    proj = Path.cwd()

    # off: remove the loop protocol; native /loop then has nothing to run.
    if first == "off":
        lp = loop_md_path(proj)
        if not lp.exists():
            print("goal-mode OFF (no .claude/loop.md here).")
            return
        try:
            lp.unlink()
            print("goal-mode OFF — removed .claude/loop.md. (Press Esc to stop a running /loop.)")
        except OSError as e:
            print(f"goal-mode OFF — could not remove {lp} ({e}); remove it manually.")
        return

    # status: full goal detail.
    if first == "status":
        ctx = goal_context(proj)
        if not ctx:
            print("loopx goal-mode: no goal in this project yet — run `/loopx <task>` to create one.")
            return
        print_status(ctx)
        return

    # bare (=on) / on: (re)arm THIS project's existing goal and show how to drive it.
    if not args or first == "on":
        ctx = goal_context(proj)
        if not ctx or not ctx.get("goal_id"):
            print("loopx goal-mode: ready — no goal set in this project yet.")
            print("Tell me what to work on and I'll set it up:")
            print("    /loopx <your goal>   e.g.  /loopx 写一个 RTL 模块并跑通仿真")
            return
        write_loop_md(proj, ctx["goal_id"], ctx.get("agent_id") or DEFAULT_AGENT)
        print(f"goal-mode armed  goal={ctx['goal_id']}  agent={ctx.get('agent_id') or DEFAULT_AGENT}")
        print("Drive it with native /loop:  `/loop` (Claude self-paces)  |  `/loop 10m` (fixed).")
        print("Stop with Esc; `/loopx off` removes the loop protocol.")
        return

    # free-text task -> one-shot setup + write loop.md + first bounded segment.
    task = " ".join(args).strip().strip('"').strip("'")
    reg = find_registry(proj)
    if reg is not None:
        ctx = goal_context(proj) or {}
        goal_id = ctx.get("goal_id")
        registry = str(reg)
    else:
        goal_id = slug(proj.name)
        registry = str(proj / ".loopx" / "registry.json")
        # Claude projects keep goal state under .claude/ (not the Codex-default .codex/)
        state_file = f".claude/goals/{goal_id}/ACTIVE_GOAL_STATE.md"
        r = gh(["bootstrap", "--project", str(proj), "--goal-id", goal_id,
                "--objective", task, "--state-file", state_file])
        if "ok: `True`" not in r.stdout and "ok=True" not in r.stdout and r.returncode != 0:
            print("[loopx] bootstrap failed:\n" + (r.stdout + r.stderr)[:600])
            sys.exit(1)

    if not goal_id:
        print("[loopx] could not determine goal id")
        sys.exit(1)

    # register the default agent (identity contract) and add the task as a todo
    gh(["--registry", registry, "configure-goal", "--goal-id", goal_id,
        "--agent-model", "peer_v1", "--registered-agent", DEFAULT_AGENT, "--execute"])
    add = gh(["--registry", registry, "--format", "json", "todo", "add",
              "--goal-id", goal_id, "--role", "agent", "--text", task])
    todo_id = ""
    try:
        todo_id = json.loads(add.stdout).get("todo_id", "")
    except Exception:
        pass

    # write the per-iteration protocol; the RUN happens when the user types /loop
    # (this command only SETS UP the goal — it must not do the work itself).
    write_loop_md(proj, goal_id, DEFAULT_AGENT)

    tid = todo_id or "(see should_run output)"
    print("goal set up. loopx is the control plane; Claude Code's native /loop is the runtime.")
    print(f"  goal_id : {goal_id}")
    print(f"  agent   : {DEFAULT_AGENT}")
    print(f"  todo_id : {tid}")
    print(f"  scope   : {proj}")
    print(f"  task    : {task}")
    print("  wrote   : .claude/loop.md  (the per-tick protocol)")
    print()
    print("START WORKING — run native `/loop`  (Claude self-paces)  or  `/loop 10m`  (fixed cadence).")
    print("Each /loop tick follows should_run's current contract; complete_task settles only verified, finished work.")
    print("Stop with Esc or `/loopx off`.")


if __name__ == "__main__":
    pin_utf8_stdio()
    main()
