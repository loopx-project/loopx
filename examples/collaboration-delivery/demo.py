"""Bounded real-agent demo controller; private state stays in a fresh ignored root."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from loopx.capabilities.manager_context import deliver, pending
from loopx.control_plane.collaboration.peers import returns
from loopx.capabilities.manager_context.roundtrip import drain
from loopx.chat_store import ChatSessionStore
from loopx.control_plane.coordination.goal_storage_initialization import (
    initialize_goal_storage_target,
)

HERE = Path(__file__).resolve().parent
GOAL = "allocation-demo"
ACTORS = ("builder", "analyst", "reviewer")
TASKS = json.loads((HERE / "tasks.json").read_text())
ARTIFACTS = {
    "analyst-1": ("analyst", "builder", ["outputs/model.json"]),
    "builder-2": (
        "builder",
        "reviewer",
        ["solver.py", "REQUIREMENTS.md", "outputs/model.json", "outputs/plan.json"],
    ),
    "reviewer-1": ("reviewer", "builder", ["outputs/review-r1.md"]),
    "builder-3": (
        "builder",
        "reviewer",
        ["solver.py", "REQUIREMENTS.md", "inputs/scenario.json", "outputs/plan.json"],
    ),
    "reviewer-2": ("reviewer", "builder", ["outputs/review-r2.md"]),
}


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def git(workspace, *args):
    return subprocess.check_output(
        [
            "git",
            "-C",
            str(workspace),
            "-c",
            "user.name=Demo Controller",
            "-c",
            "user.email=demo@example.invalid",
            *args,
        ],
        text=True,
    ).strip()


def worker(root, actor):
    return root / "agents" / actor


def cli(root, *args, cwd=None):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--registry",
            str(root / "registry.json"),
            "--runtime-root",
            str(root / "runtime"),
            "--format",
            "json",
            *args,
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd,
    )
    if result.returncode:
        (root / "last-cli-failure.log").write_text(result.stdout + "\n" + result.stderr)
        raise SystemExit(
            "CLI failed; inspect last-cli-failure.log in the private demo root"
        )
    return json.loads(result.stdout)


def owner_request(root, correction=False):
    store = ChatSessionStore(root / "runtime")
    meta = json.loads((root / "demo.json").read_text())
    session = store.load_session(meta["session_id"])
    source = root / "project/inputs/scenario.json"
    message = (
        "Correction: reserve one unit in each stock group and allocate at least four "
        "units to East. Keep all prior constraints and obtain a second independent review."
        if correction
        else "Build the allocation planner. Ask analyst for a model and "
        "reviewer for independent verification; return the verified result here."
    )
    turn, _ = store.create_turn(
        session["session_id"],
        client_turn_id="correction" if correction else "initial",
        message=message,
        origin="web",
    )
    brief = {
        "schema_version": "collaboration_brief_v0",
        "purpose": message,
        "context": "The owner rejected proportional rounding. Use exact integer optimization "
        "and sorted-id lexicographic tie-breaking. Preserve zero-demand behavior.",
        "constraints": ["Synthetic local files only; no orders or external services"],
        "inputs": [
            {
                "ref": "inputs/scenario.json",
                "description": "Current allocation input",
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            },
            {"ref": "REQUIREMENTS.md", "description": "Acceptance contract"},
        ],
        "acceptance": [
            "Runnable solver with independently verified optimum and negative cases"
        ],
        "return_requirement": "Return actual artifacts, independent findings and remaining gaps",
    }
    receipt = deliver(
        root / "runtime",
        root / "registry.json",
        session=session,
        turn=turn,
        request={"goal_id": GOAL, "agent_id": "builder", "brief": brief},
    )
    store.update_turn(
        session["session_id"],
        turn["turn_id"],
        status="completing",
        response={"message": "Delegation saved.", "context_handoff_receipt": receipt},
    )
    store.finalize_managed_turn_completion(session["session_id"], turn["turn_id"])
    meta["requests"].append(receipt["request_id"])
    write(root / "demo.json", meta)


def prepare(root):
    # Never point the fixture controller at an existing Goal or runtime.
    root.mkdir(parents=True, exist_ok=False)
    (root / ".gitignore").write_text("*\n")
    project = root / "project"
    (project / "inputs").mkdir(parents=True)
    shutil.copy(HERE / "scenario.json", project / "inputs/scenario.json")
    shutil.copy(HERE / "REQUIREMENTS.md", project / "REQUIREMENTS.md")
    (project / ".gitignore").write_text(".local/\nACTIVE_GOAL_STATE.md\n__pycache__/\n")
    (project / "ACTIVE_GOAL_STATE.md").write_text(
        "---\nstatus: active\n---\n# Allocation demo\n\n## User Todo\n\n## Agent Todo\n\n## Next Action\n\n- Run the assigned bounded phase.\n"
    )
    git(project, "init", "-b", "main")
    git(project, "add", ".gitignore", "inputs/scenario.json", "REQUIREMENTS.md")
    git(project, "commit", "-s", "-m", "Initialize synthetic allocation scenario")
    # Identity only: this URL is never contacted by the controller.
    git(
        project,
        "remote",
        "add",
        "origin",
        "https://example.invalid/loopx/allocation-demo.git",
    )
    registry = {
        "schema_version": 1,
        "common_runtime_root": str(root / "runtime"),
        "goals": [
            {
                "id": GOAL,
                "creation_operation_id": f"goal-create:{GOAL}",
                "domain": "synthetic-allocation",
                "status": "active",
                "repo": str(project),
                "state_file": "ACTIVE_GOAL_STATE.md",
                "adapter": {"kind": "fixture_v0", "status": "connected-delivery"},
                "quota": {"compute": 20.0, "window_hours": 24},
                "coordination": {
                    "agent_model": "peer_v1",
                    "registered_agents": list(ACTORS),
                    "write_scope": ["**"],
                    "storage_target": {
                        "schema_version": "loopx_new_goal_storage_target_v1",
                        "provider": "file",
                        "handoff_mode": "soft_claim",
                    },
                },
            }
        ],
    }
    registry_path = root / "registry.json"
    write(registry_path, registry)
    initialized = initialize_goal_storage_target(
        root / "runtime",
        registry["goals"][0],
        registry_path=registry_path,
    )
    if not isinstance(initialized, dict) or (
        initialized.get("authority_initialized") is not True
        or initialized.get("legacy_fallback_used") is not False
    ):
        raise RuntimeError("demo canonical authority initialization failed")
    for actor in ACTORS:
        workspace = worker(root, actor)
        workspace.parent.mkdir(exist_ok=True)
        git(project, "worktree", "add", "-b", actor, str(workspace))
        (workspace / "outputs").mkdir()
        (workspace / "tasks").mkdir()
        args = [
            "-m",
            "loopx.collaboration_mcp",
            "--registry",
            str(root / "registry.json"),
            "--runtime-root",
            str(root / "runtime"),
            "--goal-id",
            GOAL,
            "--agent-id",
            actor,
            "--workspace",
            str(workspace),
        ]
        # JSON is valid YAML and avoids shell/YAML interpolation of host paths.
        write(
            root / f"{actor}-cordis.yml",
            [
                {
                    "insert": [
                        {
                            "id": "loopx-collaboration",
                            "name": "@deepseek-ai/dsh-mcp-client",
                            "config": {
                                "transport": "stdio",
                                "serverName": "loopx_collaboration",
                                "command": sys.executable,
                                "args": args,
                                "cwd": str(workspace),
                                "failOnStartupError": True,
                            },
                        }
                    ]
                }
            ],
        )
    store = ChatSessionStore(root / "runtime")
    session = store.create_session(
        goal_id="loopx-manager",
        agent_id="codex",
        adapter_kind="codex_app_server",
        upstream_thread_id="demo-owner",
        channel_id="manager",
    )
    write(
        root / "demo.json",
        {
            "schema": "allocation_demo_v1",
            "session_id": session["session_id"],
            "requests": [],
        },
    )
    owner_request(root)
    print("Prepared isolated fixture; no model call has run.")


def validate(root, phase):
    actor = phase.split("-")[0]
    workspace = worker(root, actor)
    runtime = root / "runtime"
    if phase == "builder-1":
        assert (workspace / "outputs/build-plan.md").stat().st_size > 0
        assert pending(runtime, GOAL, "analyst")["items"]
    elif phase in {"analyst-1", "reviewer-1", "reviewer-2"}:
        for ref in ARTIFACTS[phase][2]:
            assert (workspace / ref).stat().st_size > 0
        assert any(
            row["agent_id"] == actor
            for row in returns(runtime, GOAL, "builder")["items"]
        )
    elif phase in {"builder-2", "builder-3"}:
        rows = pending(runtime, GOAL, "reviewer")["items"]
        expected = {
            ref: hashlib.sha256((workspace / ref).read_bytes()).hexdigest()
            for ref in (
                "solver.py",
                "REQUIREMENTS.md",
                "inputs/scenario.json",
                "outputs/plan.json",
            )
        }
        # Interrupted attempts retain older immutable requests. Only the request
        # matching current artifacts qualifies; the receiver must assess stale ones.
        matching = []
        for row in rows:
            refs = {item["ref"]: item.get("sha256") for item in row["brief"]["inputs"]}
            if all(refs.get(ref) == digest for ref, digest in expected.items()):
                matching.append(row)
        assert len(matching) == 1, (
            "Expected one review request bound to current artifacts"
        )
    elif phase == "builder-final":
        assert (workspace / "outputs/final.md").stat().st_size > 0
        meta = json.loads((root / "demo.json").read_text())
        assert len(meta["requests"]) == 2
        for rid in meta["requests"]:
            assert (
                runtime / ".local/manager-context/replies" / rid / "conclusion.json"
            ).exists()
    print(
        "Artifact/route validation passed; this is not independent solver acceptance."
    )


def run(root, phase, model, execute, attempt):
    if not execute:
        raise SystemExit(
            "Model execution requires --execute and an externally configured DEEPSEEK_API_KEY"
        )
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise SystemExit("DEEPSEEK_API_KEY is not configured")
    actor = phase.split("-")[0]
    instance = phase if attempt == 1 else f"{phase}-attempt-{attempt}"
    workspace = worker(root, actor)
    meta = json.loads((root / "demo.json").read_text())
    (workspace / "OPERATING.md").write_text(
        f"Your identity is {actor}. Use the scoped loopx_collaboration MCP tools read_context, "
        "assess_request, request_peer, return_result, consume_peer_result. They bind identity "
        "outside the file sandbox. Do not use CLI writes or request sandbox escalation. "
        "Open actual files and verify versions. Requests/replies do not grant Todo/lease authority. "
        "Never edit registry/state, commit Git, use network or invent another Agent's result. "
        f"Owner request ids: {', '.join(meta['requests'])}.\n"
    )
    task = TASKS[phase]
    if attempt > 1:
        task += (
            f"\nExplicit repair attempt {attempt}. Read outputs/repair-feedback.md and current "
            "REQUIREMENTS.md. Preserve failed evidence; use a NEW peer operation id for a "
            "new review, consume earlier peer results after assessing them, and never "
            "reuse an old approval for changed artifacts."
        )
    (workspace / "tasks" / f"{phase}.md").write_text(task + "\n")
    todos = cli(root, "todo", "list", "--goal-id", GOAL)["todos"]
    owned = next(
        (
            t
            for t in todos
            if t.get("claimed_by") == actor and t.get("status") == "open"
        ),
        None,
    )
    text = f"Read OPERATING.md and tasks/{phase}.md and perform that bounded collaboration phase."
    cli(
        root,
        "todo",
        "update",
        "--goal-id",
        GOAL,
        "--todo-id",
        owned["todo_id"],
        "--agent-id",
        actor,
        "--text",
        text,
    ) if owned else cli(
        root,
        "todo",
        "add",
        "--goal-id",
        GOAL,
        "--role",
        "agent",
        "--claimed-by",
        actor,
        "--text",
        text,
        "--action-kind",
        "implement",
    )
    validator = [
        sys.executable,
        str(HERE / "demo.py"),
        "validate",
        "--root",
        str(root),
        "--phase",
        phase,
    ]
    result = cli(
        root,
        "turn",
        "run-once",
        "--goal-id",
        GOAL,
        "--agent-id",
        actor,
        "--turn-instance-id",
        instance,
        "--host",
        "dsh",
        "--execution-mode",
        "isolated-headless",
        "--project",
        str(workspace),
        "--dsh-home",
        str(root / f"home-{instance}"),
        "--dsh-cordis",
        str(root / f"{actor}-cordis.yml"),
        "--dsh-model",
        model,
        "--dsh-reasoning-effort",
        "high",
        "--validation-command-json",
        json.dumps(validator),
        "--validation-failure-kind",
        "repair_required",
        "--no-global-sync",
        "--timeout-seconds",
        "600",
        "--execute",
        cwd=workspace,
    )
    write(root / f"{instance}.json", result)
    print(
        json.dumps(
            {k: result.get(k) for k in ("ok", "status", "result_kind", "validation")}
        )
    )
    if result.get("status") != "committed":
        raise SystemExit(
            "Phase did not commit; inspect its private receipt before retrying"
        )


def transfer(root, phase):
    source, target, refs = ARTIFACTS[phase]
    src, dst = worker(root, source), worker(root, target)
    git(src, "add", "--", *refs)
    if git(src, "diff", "--cached", "--name-only"):
        git(src, "commit", "-s", "-m", f"Deliver {phase} artifacts")
    commit = git(src, "rev-parse", "HEAD")
    for ref in refs:
        (dst / ref).parent.mkdir(parents=True, exist_ok=True)
        (dst / ref).write_bytes(
            subprocess.check_output(["git", "-C", str(src), "show", f"{commit}:{ref}"])
        )
    write(
        root / f"{phase}-transfer.json",
        {
            "source": source,
            "target": target,
            "commit": commit,
            "artifacts": {
                ref: hashlib.sha256((dst / ref).read_bytes()).hexdigest()
                for ref in refs
            },
        },
    )
    print(f"Transferred {len(refs)} versioned artifacts: {source} -> {target}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=["prepare", "run", "validate", "transfer", "correct", "readback"],
    )
    parser.add_argument("--root", type=Path, default=Path(".local/allocation-demo"))
    parser.add_argument("--phase", choices=list(TASKS))
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument(
        "--attempt",
        type=int,
        default=1,
        help="Explicit new Turn after failed or invalidated evidence; retains earlier receipts.",
    )
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.attempt < 1:
        parser.error("--attempt must be positive")
    root = args.root.resolve()
    if args.action == "prepare":
        prepare(root)
        return
    if (
        json.loads((root / "demo.json").read_text()).get("schema")
        != "allocation_demo_v1"
    ):
        raise SystemExit("Not an isolated demo root")
    if args.action in {"run", "validate", "transfer"} and not args.phase:
        parser.error("--phase is required")
    if args.action == "run":
        run(root, args.phase, args.model, args.execute, args.attempt)
    elif args.action == "validate":
        validate(root, args.phase)
    elif args.action == "transfer":
        if args.phase not in ARTIFACTS:
            parser.error("This phase has no artifact transfer")
        transfer(root, args.phase)
    elif args.action == "correct":
        meta = json.loads((root / "demo.json").read_text())
        if len(meta["requests"]) != 1:
            raise SystemExit("Correction already created")
        source = root / "project/inputs/scenario.json"
        write(
            source,
            {
                **json.loads(source.read_text()),
                "reserve_per_group": 1,
                "minimum_east": 4,
            },
        )
        shutil.copy(source, worker(root, "builder") / "inputs/scenario.json")
        owner_request(root, correction=True)
        print(
            "New immutable owner request created; the first request's input version remains unchanged."
        )
    elif args.action == "readback":
        store = ChatSessionStore(root / "runtime")
        delivered = drain(root / "runtime", root / "registry.json", store, None)
        assert (
            drain(
                root / "runtime",
                root / "registry.json",
                ChatSessionStore(root / "runtime"),
                None,
            )
            == 0
        )
        from loopx.capabilities.manager_context.roundtrip import (
            project_chat_session_snapshot,
        )

        snapshot = project_chat_session_snapshot(
            root / "runtime",
            store,
            json.loads((root / "demo.json").read_text())["session_id"],
        )
        write(root / "conversation.json", snapshot)
        print(
            json.dumps(
                {
                    "new_deliveries": delivered,
                    "repeat_deliveries": 0,
                    "conclusions": [
                        m.get("text", m.get("content"))
                        for m in snapshot["messages"]
                        if m.get("origin") == "manager_followup"
                    ],
                }
            )
        )


if __name__ == "__main__":
    main()
