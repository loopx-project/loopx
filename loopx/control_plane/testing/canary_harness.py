from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[3]


def default_state_file(goal_id: str) -> str:
    # Legacy route is intentional: existing characterization fixtures create
    # state there and declare it explicitly in their synthetic registries.
    return f".codex/goals/{goal_id}/ACTIVE_GOAL_STATE.md"


def project_state_path(project: Path, goal_id: str, *, state_file: str | None = None) -> Path:
    return project / (state_file or default_state_file(goal_id))


def run_json_cli(
    *args: str,
    registry_path: Path,
    runtime_root: Path | None = None,
    cwd: Path | None = None,
    include_returncode: bool = True,
) -> dict[str, Any]:
    command = [
        sys.executable,
        "-m",
        "loopx.cli",
        "--registry",
        str(registry_path),
        "--format",
        "json",
        *args,
    ]
    if runtime_root is not None:
        command[5:5] = ["--runtime-root", str(runtime_root)]
    result = subprocess.run(
        command,
        cwd=cwd or REPO_ROOT,
        check=False,
        text=True, encoding="utf-8", errors="replace",
        capture_output=True,
    )
    if result.returncode != 0:
        raise AssertionError(result.stdout + result.stderr)
    if not result.stdout.strip():
        raise AssertionError(f"empty CLI output for {command!r}: {result.stderr}")
    payload = json.loads(result.stdout)
    if not isinstance(payload, dict):
        raise AssertionError(f"expected JSON object for {command!r}: {payload!r}")
    if include_returncode:
        payload["_returncode"] = result.returncode
    return payload


def run_json_cli_result(
    *args: str,
    registry_path: Path,
    runtime_root: Path | None = None,
    cwd: Path | None = None,
) -> tuple[int, dict[str, Any]]:
    command = [
        sys.executable,
        "-m",
        "loopx.cli",
        "--registry",
        str(registry_path),
        "--format",
        "json",
        *args,
    ]
    if runtime_root is not None:
        command[5:5] = ["--runtime-root", str(runtime_root)]
    result = subprocess.run(
        command,
        cwd=cwd or REPO_ROOT,
        check=False,
        text=True, encoding="utf-8", errors="replace",
        capture_output=True,
    )
    if not result.stdout.strip():
        raise AssertionError(f"empty CLI output for {command!r}: {result.stderr}")
    payload = json.loads(result.stdout)
    if not isinstance(payload, dict):
        raise AssertionError(f"expected JSON object for {command!r}: {payload!r}")
    payload["_returncode"] = result.returncode
    return result.returncode, payload


def runtime_root_from_registry(registry_path: Path) -> Path | None:
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    runtime_root = payload.get("common_runtime_root") if isinstance(payload, dict) else None
    return Path(runtime_root) if runtime_root else None


def write_fixture_registry(
    *,
    project: Path,
    runtime_root: Path,
    registry_path: Path,
    goal_id: str,
    domain: str,
    adapter_kind: str,
    adapter_status: str = "connected",
    status: str = "active",
    state_file: str | None = None,
    state_event_log: str | None = None,
    registered_agents: Iterable[str] | None = (),
    quota_allowed_slots: int | None = 10,
    peer_independent_worktree_required: bool | None = None,
    extra_goal_fields: dict[str, Any] | None = None,
) -> Path:
    state_file_value = state_file or default_state_file(goal_id)
    coordination: dict[str, Any] | None = None
    if registered_agents is not None:
        coordination = {
            "registered_agents": list(registered_agents),
            "agent_model": "peer_v1",
        }

    goal: dict[str, Any] = {
        "id": goal_id,
        "domain": domain,
        "status": status,
        "repo": str(project),
        "state_file": state_file_value,
        "adapter": {
            "kind": adapter_kind,
            "status": adapter_status,
        },
        "authority_sources": [],
    }
    if coordination is not None:
        goal["coordination"] = coordination
    if quota_allowed_slots is not None:
        goal["quota"] = {
            "compute": 1.0,
            "window_hours": 24,
            "slot_minutes": 1,
            "allowed_slots": quota_allowed_slots,
        }
    if state_event_log:
        goal["state_event_log"] = state_event_log
    if peer_independent_worktree_required is not None:
        goal["workspace_guard_policy"] = {
            "peer_independent_worktree_required": peer_independent_worktree_required,
        }
    if extra_goal_fields:
        goal.update(extra_goal_fields)

    registry_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_root.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "updated_at": "2026-01-01T00:00:00+00:00",
                "common_runtime_root": str(runtime_root),
                "goals": [goal],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return registry_path


def read_run_index(runtime_root: Path, goal_id: str) -> list[dict[str, Any]]:
    index_path = runtime_root / "goals" / goal_id / "runs" / "index.jsonl"
    if not index_path.exists():
        return []
    return [
        json.loads(line)
        for line in index_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
