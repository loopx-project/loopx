from __future__ import annotations

from pathlib import Path
from typing import Any

from .bootstrap import (
    DEFAULT_DOMAIN,
    bootstrap_project,
)
from .paths import select_default_runtime_root
from .quota import build_quota_should_run
from .state_refresh import refresh_state_run
from .status import collect_status
from .todos import add_goal_todo


DEFAULT_DEMO_PROJECT = Path("/tmp/loopx-demo")
DEFAULT_DEMO_GOAL_ID = "demo-goal"
DEFAULT_DEMO_OBJECTIVE = "Keep this demo goal organized and verifiable."
DEFAULT_DEMO_USER_TODO = "Decide whether this demo goal is worth wiring into a real project."
DEFAULT_DEMO_AGENT_TODO = "Run one read-only project map before making any code changes."


def _first_queue_item(status_payload: dict[str, Any], goal_id: str) -> dict[str, Any] | None:
    queue = status_payload.get("attention_queue")
    if not isinstance(queue, dict):
        return None
    for item in queue.get("items") or []:
        if isinstance(item, dict) and item.get("goal_id") == goal_id:
            return item
    return None


def run_demo(
    *,
    project: Path,
    runtime_root: Path | None,
    goal_id: str,
    objective: str,
    user_todo: str,
    agent_todo: str,
) -> dict[str, Any]:
    project = project.expanduser().resolve()
    runtime_root = runtime_root.expanduser().resolve() if runtime_root else select_default_runtime_root()
    registry_path = project / ".loopx" / "registry.json"
    goal_doc = project / "GOAL.md"
    project.mkdir(parents=True, exist_ok=True)
    if goal_doc.exists():
        goal_doc_action = "kept-existing"
    else:
        goal_doc.write_text(f"{objective}\n", encoding="utf-8")
        goal_doc_action = "created"

    bootstrap = bootstrap_project(
        project=project,
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=goal_id,
        objective=objective,
        domain=DEFAULT_DOMAIN,
        role="controller",
        parent_goal_id=None,
        state_file=None,
        goal_doc=Path("GOAL.md"),
        adapter_kind="demo_local_queue_v0",
        adapter_status="connected",
        next_probe=None,
        spawn_allowed=False,
        max_children=3,
        allowed_domains=[],
        write_scope=[],
        force=False,
        dry_run=False,
        sync_global=False,
    )
    user_todo_payload = add_goal_todo(
        registry_path=registry_path,
        goal_id=goal_id,
        role="user",
        text=user_todo,
        task_class="user_gate",
        action_kind="demo_goal_decision",
        global_gate=True,
    )
    agent_todo_payload = add_goal_todo(
        registry_path=registry_path,
        goal_id=goal_id,
        role="agent",
        text=agent_todo,
    )
    refresh = refresh_state_run(
        registry_path=registry_path,
        runtime_root_override=str(runtime_root),
        goal_id=goal_id,
        project=project,
        state_file=None,
        classification="state_refreshed",
        recommended_action=None,
        dry_run=False,
        sync_global=False,
    )
    status_payload = collect_status(
        registry_path=registry_path,
        runtime_root_override=str(runtime_root),
        scan_roots=[project],
        limit=5,
        goal_id=goal_id,
    )
    quota = build_quota_should_run(status_payload, goal_id=goal_id)
    queue_item = _first_queue_item(status_payload, goal_id)
    state_file = bootstrap.get("state_file")
    return {
        "ok": bool(bootstrap.get("ok")) and bool(refresh.get("ok")) and bool(status_payload.get("ok")) and bool(quota.get("ok")),
        "project": str(project),
        "goal_id": goal_id,
        "registry": str(registry_path),
        "state_file": state_file,
        "goal_doc": str(goal_doc),
        "goal_doc_action": goal_doc_action,
        "runtime_root": str(runtime_root),
        "bootstrap": {
            "registry_goal_action": bootstrap.get("registry_goal_action"),
            "state_action": bootstrap.get("state_action"),
        },
        "todos": {
            "user_added": user_todo_payload.get("added"),
            "user_already_exists": user_todo_payload.get("already_exists"),
            "agent_added": agent_todo_payload.get("added"),
            "agent_already_exists": agent_todo_payload.get("already_exists"),
        },
        "refresh": {
            "appended": refresh.get("appended"),
            "generated_at": refresh.get("generated_at"),
            "recommended_action": refresh.get("recommended_action"),
        },
        "status": {
            "ok": status_payload.get("ok"),
            "queue_items": (status_payload.get("attention_queue") or {}).get("summary", {}).get("items")
            if isinstance(status_payload.get("attention_queue"), dict)
            else None,
            "demo_waiting_on": queue_item.get("waiting_on") if queue_item else None,
            "demo_next_action": queue_item.get("action") if queue_item else None,
            "demo_user_todos": queue_item.get("user_todos") if queue_item else None,
            "demo_agent_todos": queue_item.get("agent_todos") if queue_item else None,
        },
        "quota": quota,
        "next_commands": [
            f"cd {project}",
            'registry="$PWD/.loopx/registry.json"',
            'loopx --registry "$registry" status --scan-root "$PWD"',
            f'loopx --registry "$registry" --format json quota should-run --goal-id {goal_id} --runtime-profile generic_cli',
        ],
        "dashboard_status_commands": [
            f"cd {project}",
            'registry="$PWD/.loopx/registry.json"',
            'loopx --registry "$registry" serve-status --scan-root "$PWD" --port 8765',
        ],
        "dashboard_status_scope": "project-local-demo",
        "canonical_dashboard_status_commands": [
            "loopx serve-status --global-registry --port 8766 --limit 80",
        ],
        "canonical_dashboard_status_url": "http://127.0.0.1:8766/status.json",
        "dashboard_app_commands": [
            "cd ~/loopx/apps/presentation/dashboard",
            "npm install",
            "npm run dev",
        ],
        "dashboard_status_url": "http://127.0.0.1:8765/status.json",
    }


def render_demo_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# LoopX Demo",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- project: `{payload.get('project')}`",
        f"- goal_id: `{payload.get('goal_id')}`",
        f"- registry: `{payload.get('registry')}`",
        f"- state_file: `{payload.get('state_file')}`",
        f"- goal_doc: `{payload.get('goal_doc')}` ({payload.get('goal_doc_action')})",
        f"- runtime_root: `{payload.get('runtime_root')}`",
    ]
    if payload.get("error"):
        lines.append(f"- error: {payload.get('error')}")
        return "\n".join(lines)

    bootstrap = payload.get("bootstrap") if isinstance(payload.get("bootstrap"), dict) else {}
    todos = payload.get("todos") if isinstance(payload.get("todos"), dict) else {}
    refresh = payload.get("refresh") if isinstance(payload.get("refresh"), dict) else {}
    status = payload.get("status") if isinstance(payload.get("status"), dict) else {}
    quota = payload.get("quota") if isinstance(payload.get("quota"), dict) else {}

    lines.extend(
        [
            "",
            "## What Happened",
            "",
            f"- bootstrap: registry `{bootstrap.get('registry_goal_action')}`, state `{bootstrap.get('state_action')}`",
            f"- user todo: added={todos.get('user_added')} already_exists={todos.get('user_already_exists')}",
            f"- agent todo: added={todos.get('agent_added')} already_exists={todos.get('agent_already_exists')}",
            f"- refresh-state: appended={refresh.get('appended')} at={refresh.get('generated_at')}",
            f"- status: ok={status.get('ok')} demo_waiting_on={status.get('demo_waiting_on')}",
            f"- quota: should_run={quota.get('should_run')} state={quota.get('state')} reason={quota.get('reason')}",
        ]
    )
    if status.get("demo_next_action"):
        lines.append(f"- next action: {status.get('demo_next_action')}")

    lines.extend(["", "## Try Next", ""])
    for command in payload.get("next_commands") or []:
        lines.append(f"- `{command}`")
    lines.extend(
        [
            "",
            "## Dashboard Option",
            "",
            "Terminal 1: start a project-local live status server for this demo:",
            "",
        ]
    )
    for command in payload.get("dashboard_status_commands") or []:
        lines.append(f"- `{command}`")
    lines.extend(
        [
            "",
            "Terminal 2: start the dashboard app:",
            "",
        ]
    )
    for command in payload.get("dashboard_app_commands") or []:
        lines.append(f"- `{command}`")
    lines.extend(
        [
            f"- In the dashboard, use the `Live` source or load `{payload.get('dashboard_status_url')}`.",
            "- This demo endpoint is project-local; it does not sync the temporary demo into the shared global registry.",
            "",
            "For the canonical multi-project dashboard, use the shared global registry view instead:",
            "",
        ]
    )
    for command in payload.get("canonical_dashboard_status_commands") or []:
        lines.append(f"- `{command}`")
    if payload.get("canonical_dashboard_status_url"):
        lines.append(f"- load `{payload.get('canonical_dashboard_status_url')}`.")
    return "\n".join(lines)
