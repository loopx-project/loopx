"""Seed real, isolated LoopX state and serve the shipped Workspace over it."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any

from loopx.bootstrap import bootstrap_project
from loopx.configure_goal import configure_goal
from loopx.control_plane.todos.handoff_mode import set_goal_handoff_mode
from loopx.state_refresh import refresh_state_run
from loopx.todos import add_goal_todo, complete_goal_todo, update_goal_todo

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
MARKER = ".workspace-story-demo.json"
REGISTRY_NAME = "registry.json"


def checked(result: dict[str, Any]) -> dict[str, Any]:
    if not result.get("ok"):
        raise RuntimeError(json.dumps(result, ensure_ascii=False))
    return result


def write_story_artifacts(project: Path, story: dict[str, Any], notice: str) -> None:
    (project / "BRIEF.md").write_text(f"# {story['title']}\n\n{story['brief']}\n")
    with (project / "working-table.csv").open("w", newline="") as table:
        writer = csv.writer(table)
        writer.writerow(["item", "amount"])
        writer.writerows(story["rows"])
    calculation = {
        "provenance": notice,
        "inputs": story["rows"],
        "sum": sum(row[1] for row in story["rows"]),
        "row_count": len(story["rows"]),
    }
    if "budget_limit" in story:
        calculation["contingency"] = story["budget_limit"] - calculation["sum"]
    if story["id"] == "research-brief":
        calculation["operating_cost_sensitivity"] = [
            {
                "heat_kwh": heat,
                "tariff": tariff,
                "efficiency": efficiency,
                "annual_cost": round(heat / efficiency * tariff, 2),
            }
            for heat in [6000, 12000, 18000]
            for tariff in [0.2, 0.3, 0.4]
            for efficiency in [2.5, 3.0, 3.5]
        ]
    (project / "calculations.json").write_text(json.dumps(calculation, indent=2))

    (project / "DELIVERY-PLAN.md").write_text(
        "# Delivery plan\n\n"
        + "\n".join(
            f"- **{t['phase']} / {t['agent']} / {t['status']}** — {t['title']}"
            + (f" (after {t['after']})" if t.get("after") else "")
            for t in story["tasks"]
        )
        + "\n"
    )


def seed_delivery_tasks(story: dict[str, Any], registry: Path, runtime: Path) -> list[dict[str, Any]]:
    """Record each story dependency as the typed relation its state allows."""
    todos: list[dict[str, Any]] = []
    ids: dict[str, str] = {}
    by_key = {task["key"]: task for task in story["tasks"]}
    dependents: dict[str, list[str]] = {}
    for task in story["tasks"]:
        if task.get("after"):
            dependents.setdefault(task["after"], []).append(task["key"])
    for task in story["tasks"]:
        owner, status, title = task["agent"], task["status"], task["title"]
        after = task.get("after")
        result = checked(
            add_goal_todo(
                registry_path=registry,
                runtime_root_arg=str(runtime),
                goal_id=story["id"],
                role="agent",
                text="[P1] " + title,
                task_class="advancement_task",
                action_kind="prepare",
                task_domain=task["phase"].lower().replace(" ", "-"),
                claimed_by=owner,
                status="open" if status == "done" else status,
                resume_when="todo_done:" + ids[after] if status == "deferred" else None,
                note=f"Phase: {task['phase']}. See BRIEF.md and calculations.json.",
            )
        )
        ids[task["key"]] = result["todo_id"]
        todos.append({**task, "todo_id": result["todo_id"]})
    for task in story["tasks"]:
        # Deferred work already waits on its condition; the rest needs a link.
        linked = [
            key for key in dependents.get(task["key"], [])
            if by_key[key]["status"] != "deferred"
        ]
        if task["status"] == "done":
            checked(
                complete_goal_todo(
                    registry_path=registry,
                    runtime_root_arg=str(runtime),
                    goal_id=story["id"],
                    todo_id=ids[task["key"]],
                    agent_id=task["agent"],
                    evidence="Scenario replay checkpoint; BRIEF.md, working-table.csv and calculations.json retain the planning inputs. No live execution receipt claimed.",
                    successor_todo_ids=[ids[key] for key in linked] or None,
                    no_followup=not linked,
                )
            )
        elif linked:
            if len(linked) > 1:
                raise ValueError(f"{task['key']} can unblock only one task")
            checked(
                update_goal_todo(
                    registry_path=registry,
                    runtime_root_arg=str(runtime),
                    goal_id=story["id"],
                    todo_id=ids[task["key"]],
                    agent_id=task["agent"],
                    unblocks_todo_id=ids[linked[0]],
                )
            )
    return todos


def seed_story(root: Path, story: dict[str, Any], notice: str) -> dict[str, Any]:
    runtime = root / "runtime"
    registry = root / REGISTRY_NAME
    project = root / "projects" / story["id"]
    project.mkdir(parents=True)
    (project / "GOAL.md").write_text(
        f"# {story['title']}\n\n{story['objective']}\n\n{notice}\n"
    )
    checked(
        bootstrap_project(
            project=project,
            registry_path=registry,
            runtime_root=runtime,
            goal_id=story["id"],
            display_name=story["title"],
            objective=story["objective"],
            domain="workspace-demo",
            role="controller",
            parent_goal_id=None,
            state_file=None,
            goal_doc=Path("GOAL.md"),
            adapter_kind="workspace_story_demo_v2",
            adapter_status="connected-read-only",
            next_probe=None,
            spawn_allowed=False,
            max_children=0,
            allowed_domains=[],
            write_scope=[],
            force=False,
            dry_run=False,
            sync_global=False,
        )
    )
    checked(
        configure_goal(
            registry_path=registry,
            goal_id=story["id"],
            registered_agents=story["agents"],
            execute=True,
        )
    )
    checked(
        set_goal_handoff_mode(
            registry_path=registry,
            goal_id=story["id"],
            mode="soft_claim",
            runtime_root_arg=str(runtime),
        )
    )
    write_story_artifacts(project, story, notice)
    todos = seed_delivery_tasks(story, registry, runtime)
    gated = {t["after"]: t["todo_id"] for t in todos if (t.get("after") or "").startswith("gate:")}
    gates = {}
    for decision in story["gates"]:
        gate = checked(
            add_goal_todo(
                registry_path=registry,
                runtime_root_arg=str(runtime),
                goal_id=story["id"],
                role="user",
                task_class="user_gate",
                action_kind="approve",
                blocks_agent=decision["agent"],
                unblocks_todo_id=gated["gate:" + decision["key"]],
                text="[P0] " + decision["title"],
            )
        )
        gates[decision["key"]] = {
            "todo_id": gate["todo_id"],
            "agent": decision["agent"],
        }
    # The App and CLI replay share the canonical User completion relationship.
    # Do not teach the demo a second writer that opens the dependent afterward.
    for key, gate in gates.items():
        targets = [todo for todo in todos if todo.get("after") == "gate:" + key]
        if len(targets) != 1:
            raise ValueError("Each demo decision must have one direct dependent")
        target = targets[0]
        scope = {"schema_version": "decision_scope_v0", "kind": "direction",
                 "granularity": "action", "scope_key": f"{story['id']}:{key}"}
        checked(update_goal_todo(
            registry_path=registry, runtime_root_arg=str(runtime),
            goal_id=story["id"], todo_id=gate["todo_id"],
            unblocks_todo_id=target["todo_id"], decision_scope=scope,
            agent_id=gate["agent"], reason="Bind the demo decision to its dependent.",
        ))
        checked(update_goal_todo(
            registry_path=registry, runtime_root_arg=str(runtime),
            goal_id=story["id"], todo_id=target["todo_id"],
            required_decision_scopes=[scope], agent_id=target["agent"],
            reason="Wait for the demo owner decision.",
        ))
    monitors = []
    for owner, title, cadence, target in story["monitors"]:
        monitor = checked(
            add_goal_todo(
                registry_path=registry,
                runtime_root_arg=str(runtime),
                goal_id=story["id"],
                role="agent",
                text="[P2] " + title,
                task_class="continuous_monitor",
                action_kind="watch",
                claimed_by=owner,
                monitor_metadata={
                    "target_key": target,
                    "cadence": cadence,
                    "watch_only": True,
                    "next_due_at": (
                        datetime.now(timezone.utc) + timedelta(days=1)
                    ).isoformat(),
                },
            )
        )
        monitors.append(monitor["todo_id"])
    checked(
        refresh_state_run(
            registry_path=registry,
            runtime_root_override=str(runtime),
            goal_id=story["id"],
            project=project,
            state_file=None,
            classification="workspace_demo_prepared",
            recommended_action="Inspect the example work, review the owner decision, and compare independent Agent lanes.",
            agent_id=story["agents"][0],
            progress_scope="agent_lane",
            dry_run=False,
            sync_global=False,
        )
    )
    return {
        "id": story["id"],
        "title": story["title"],
        "todos": todos,
        "gates": gates,
        "monitors": monitors,
    }


def prepare(root: Path) -> dict[str, Any]:
    root = root.expanduser()
    if root.is_symlink():
        raise ValueError("Demo root must not be a symlink")
    root = root.resolve()
    marker = root / MARKER
    if marker.exists():
        manifest: dict[str, Any] = json.loads(marker.read_text())
        if manifest.get("schema_version") != "workspace_story_demo_v3":
            raise ValueError("Unrecognized demo manifest")
        if manifest.get("root") != str(root) or manifest.get("registry") != str(
            root / REGISTRY_NAME
        ):
            raise ValueError("Demo manifest does not belong to this directory")
        if not (root / REGISTRY_NAME).is_file():
            raise ValueError("Demo registry is missing; use a new empty directory")
        return manifest
    if root.exists() and any(root.iterdir()):
        raise ValueError(
            "Choose a new or empty directory; existing work is never overwritten"
        )
    root.mkdir(parents=True, exist_ok=True)
    registry = root / REGISTRY_NAME
    catalog = json.loads((HERE / "stories.json").read_text())
    manifest = {
        "schema_version": "workspace_story_demo_v3",
        "notice": catalog["notice"],
        "root": str(root),
        "registry": str(registry),
        "goals": [],
    }
    manifest["goals"] = [
        seed_story(root, story, catalog["notice"]) for story in catalog["stories"]
    ]
    marker.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


def advance(
    root: Path, manifest: dict[str, Any], story_id: str, decision_key: str | None
) -> None:
    story = next(s for s in manifest["goals"] if s["id"] == story_id)
    registry = root / REGISTRY_NAME
    decision_key = decision_key or next(iter(story["gates"]))
    if decision_key not in story["gates"]:
        raise ValueError(f"Unknown decision; choose one of {list(story['gates'])}")
    decision = story["gates"][decision_key]
    checked(
        complete_goal_todo(
            registry_path=registry,
            runtime_root_arg=str(root / "runtime"),
            goal_id=story_id,
            todo_id=decision["todo_id"],
            role="user",
            decision_outcome="approve",
            agent_id=decision["agent"],
            evidence="Illustrative demo decision replay; no external action authorized.",
            no_followup=True,
        )
    )
    print(
        json.dumps(
            {
                "advanced": story_id,
                "decision": decision_key,
                "external_effects": False,
            }
        )
    )


def run_isolated(args: argparse.Namespace, root: Path) -> None:
    # Prepare and replay must be isolated too: even read-only default discovery
    # can consult personal registries before the server starts.
    home = root / "home"
    env = {
        k: v
        for k, v in os.environ.items()
        if k in {"PATH", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT"}
    }
    env.update(HOME=str(home), CODEX_HOME=str(home / ".codex"), PYTHONPATH=str(REPO))
    # Paths and ports are data, never arguments to an interpreter invocation.
    result = subprocess.run(
        [sys.executable, "-m", "demo.workspace", args.command, "--_isolated"],
        input=json.dumps({"root": str(root), "port": args.port,
                          "story": args.story, "decision": args.decision}),
        text=True,
        cwd=REPO,
        env=env,
        check=False,
    )
    raise SystemExit(result.returncode)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "serve", "advance"])
    parser.add_argument(
        "--story", choices=["community-day", "research-brief", "neighborhood-site"]
    )
    parser.add_argument(
        "--decision", help="Decision key; defaults to the first decision in the story"
    )
    parser.add_argument(
        "--root",
        type=Path,
        help="A new empty directory; omitted uses a temporary directory",
    )
    parser.add_argument("--port", type=int, default=8791)
    parser.add_argument("--_isolated", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args._isolated:
        config = json.load(sys.stdin)
        args.root = Path(config["root"])
        args.port = int(config["port"])
        args.story = config.get("story")
        args.decision = config.get("decision")
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    root = (
        args.root or Path(tempfile.mkdtemp(prefix="loopx-workspace-demo-"))
    ).expanduser()
    if root.is_symlink():
        parser.error("Demo root must not be a symlink")
    root = root.resolve()
    if not args._isolated:
        run_isolated(args, root)
    manifest = prepare(root)
    (root / "home").mkdir(exist_ok=True)
    if args.command == "advance":
        if not args.story:
            parser.error("advance requires --story")
        advance(root, manifest, args.story, args.decision)
        return
    if args.command == "prepare":
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return
    print(json.dumps({"url": f"http://127.0.0.1:{args.port}/chat/",
                      "notice": manifest["notice"]}), flush=True)
    from loopx.chat_server import serve_chat

    serve_chat(
        registry_path=root / REGISTRY_NAME,
        runtime_root_override=root / "runtime",
        scan_roots=[root],
        port=args.port,
        assets_dir=REPO / "loopx/web/chat",
        codex_bin="loopx-demo-no-agent",
        claude_bin="loopx-demo-no-agent",
        lark_cli_bin="loopx-demo-no-lark",
    )


if __name__ == "__main__":
    main()
