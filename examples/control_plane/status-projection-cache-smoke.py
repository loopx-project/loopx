#!/usr/bin/env python3
"""Smoke-test opt-in status projection cache behavior."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from loopx.control_plane.runtime.status_projection_cache import write_status_projection_cache  # noqa: E402
from loopx.control_plane.testing.canary_harness import import_fixture_authority  # noqa: E402

GOAL_ID = "status-projection-cache-fixture"
AGENT_ID = "codex-main-control"
PRIVATE_DOC_MARKER = "https://" + "la" + "rk" + "office.example/doc"


def write_fixture(root: Path) -> tuple[Path, Path, Path]:
    project = root / "project"
    runtime = root / "runtime"
    state_rel = f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state_file = project / state_rel
    registry_path = project / ".loopx" / "registry.json"
    public_doc = project / "README.md"

    state_file.parent.mkdir(parents=True)
    state_file.write_text(
        "---\n"
        "status: active\n"
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# Status Projection Cache Fixture\n\n"
        "## Objective\n\n"
        "Validate opt-in status projection cache behavior.\n\n"
        "## Next Action\n\n"
        "- Exercise the status/quota projection cache.\n\n"
        "## Agent Todo\n\n",
        encoding="utf-8",
    )
    public_doc.write_text("Public smoke fixture for status projection cache.\n", encoding="utf-8")

    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "updated_at": "2026-01-01T00:00:00+00:00",
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "status-projection-cache",
                        "status": "active",
                        "repo": str(project),
                        "state_file": state_rel,
                        "adapter": {
                            "kind": "generic_project_goal_v0",
                            "status": "connected",
                        },
                        "coordination": {
                            "registered_agents": [AGENT_ID],
                            "agent_model": "peer_v1",
                        },
                        "quota": {
                            "compute": 1.0,
                            "window_hours": 24,
                        },
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    import_fixture_authority(registry_path, GOAL_ID)
    return registry_path, runtime, public_doc


def run_cli_result(
    registry_path: Path,
    *args: str,
    check: bool = True,
) -> tuple[dict[str, Any], int, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--registry",
            str(registry_path),
            "--format",
            "json",
            *args,
        ],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        text=True,
        capture_output=True,
    )
    if check and result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    payload = json.loads(result.stdout) if result.stdout.strip().startswith("{") else {}
    return payload, result.returncode, result.stderr


def run_cli(registry_path: Path, *args: str) -> dict[str, Any]:
    payload, _returncode, _stderr = run_cli_result(registry_path, *args)
    return payload


def assert_cache_hit(payload: dict[str, Any]) -> None:
    cache = cache_metadata(payload)
    assert isinstance(cache, dict), payload
    assert cache.get("schema_version") == "status_projection_cache_v0", cache
    assert cache.get("hit") is True, cache


def assert_cache_written(payload: dict[str, Any]) -> None:
    cache = cache_metadata(payload)
    assert isinstance(cache, dict), payload
    assert cache.get("schema_version") == "status_projection_cache_v0", cache
    assert cache.get("written") is True, cache
    assert cache.get("hit") is False, cache
    assert Path(str(cache.get("path"))).exists(), cache


def cache_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    cache = payload.get("projection_cache")
    if not isinstance(cache, dict):
        cache = payload.get("status_projection_cache")
    assert isinstance(cache, dict), payload
    return cache


def expire_cache_record(payload: dict[str, Any]) -> None:
    path = Path(str(cache_metadata(payload).get("path")))
    record = json.loads(path.read_text(encoding="utf-8"))
    record["generated_at"] = "2000-01-01T00:00:00Z"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def assert_cache_expired(payload: dict[str, Any]) -> None:
    cache = cache_metadata(payload)
    assert cache.get("schema_version") == "status_projection_cache_v0", cache
    assert cache.get("hit") is False, cache
    assert cache.get("miss_reason") == "expired", cache


def assert_next_action(payload: dict[str, Any], expected_todo_id: str) -> None:
    next_action = payload.get("agent_lane_next_action")
    assert isinstance(next_action, dict), payload
    assert next_action.get("todo_id") == expected_todo_id, payload


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="loopx-status-projection-cache-") as tmp:
        registry_path, runtime, public_doc = write_fixture(Path(tmp))

        todo_payload = run_cli(
            registry_path,
            "todo",
            "add",
            "--goal-id",
            GOAL_ID,
            "--role",
            "agent",
            "--text",
            "Validate status projection cache.",
            "--claimed-by",
            AGENT_ID,
        )
        assert todo_payload.get("ok") is True, todo_payload
        initial_todo_id = str(todo_payload["todo_id"])

        status_write = run_cli(
            registry_path,
            "status",
            "--limit",
            "5",
            "--write-projection-cache",
        )
        assert status_write.get("ok") is True, status_write
        assert_cache_written(status_write)

        status_cached = run_cli(
            registry_path,
            "status",
            "--limit",
            "5",
            "--use-projection-cache",
        )
        assert status_cached.get("ok") is True, status_cached
        assert_cache_hit(status_cached)

        quota_write = run_cli(
            registry_path,
            "quota",
            "should-run",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
            "--available-capability",
            "shell",
            "--limit",
            "5",
            "--write-projection-cache",
        )
        assert quota_write.get("ok") is True, quota_write
        assert quota_write.get("should_run") is True, quota_write
        assert_cache_written(quota_write)

        quota_cached = run_cli(
            registry_path,
            "quota",
            "should-run",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
            "--available-capability",
            "shell",
            "--limit",
            "5",
            "--use-projection-cache",
        )
        assert quota_cached.get("ok") is True, quota_cached
        assert quota_cached.get("should_run") is True, quota_cached
        assert_cache_hit(quota_cached)
        assert_next_action(quota_cached, initial_todo_id)

        completed = run_cli(
            registry_path,
            "todo",
            "complete",
            "--goal-id",
            GOAL_ID,
            "--role",
            "agent",
            "--todo-id",
            initial_todo_id,
            "--agent-id",
            AGENT_ID,
            "--claimed-by",
            AGENT_ID,
            "--evidence",
            "fixture cache state transition",
            "--next-agent-todo",
            "Continue after status projection cache invalidation.",
            "--next-claimed-by",
            AGENT_ID,
            "--next-task-class",
            "advancement_task",
            "--next-action-kind",
            "status_projection_cache_successor",
        )
        assert completed.get("ok") is True, completed
        successor_id = completed["next_todos"][0]["todo_id"]

        live_status = run_cli(
            registry_path,
            "status",
            "--limit",
            "5",
        )
        live_status_text = json.dumps(live_status, sort_keys=True)
        assert successor_id in live_status_text, live_status
        assert "projection_cache" not in live_status, live_status

        live_quota = run_cli(
            registry_path,
            "quota",
            "should-run",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
            "--available-capability",
            "shell",
            "--limit",
            "5",
        )
        assert live_quota.get("ok") is True, live_quota
        assert_next_action(live_quota, successor_id)
        assert "status_projection_cache" not in live_quota, live_quota

        stale_status = run_cli(
            registry_path,
            "status",
            "--limit",
            "5",
            "--use-projection-cache",
        )
        assert_cache_hit(stale_status)
        stale_status_text = json.dumps(stale_status, sort_keys=True)
        assert initial_todo_id in stale_status_text, stale_status
        assert successor_id not in stale_status_text, stale_status

        stale_quota = run_cli(
            registry_path,
            "quota",
            "should-run",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
            "--available-capability",
            "shell",
            "--limit",
            "5",
            "--use-projection-cache",
        )
        assert stale_quota.get("ok") is True, stale_quota
        # Read-only status may display an opted-in stale snapshot; scheduling
        # must invalidate changed authoritative Todos before selecting work.
        quota_cache = cache_metadata(stale_quota)
        assert quota_cache["hit"] is False, quota_cache
        assert quota_cache["miss_reason"] == "goal_todo_projection_changed", quota_cache
        assert_next_action(stale_quota, successor_id)

        expire_cache_record(stale_status)
        expire_cache_record(stale_quota)

        expired_status = run_cli(
            registry_path,
            "status",
            "--limit",
            "5",
            "--use-projection-cache",
        )
        assert_cache_expired(expired_status)
        assert successor_id in json.dumps(expired_status, sort_keys=True), expired_status

        expired_quota = run_cli(
            registry_path,
            "quota",
            "should-run",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
            "--available-capability",
            "shell",
            "--limit",
            "5",
            "--use-projection-cache",
        )
        assert expired_quota.get("ok") is True, expired_quota
        assert_cache_expired(expired_quota)
        assert_next_action(expired_quota, successor_id)

        # A damaged optional cache cannot hide the current canonical successor.
        for previous, command in (
            (expired_status, ("status",)),
            (expired_quota, (
                "quota", "should-run", "--goal-id", GOAL_ID,
                "--agent-id", AGENT_ID, "--available-capability", "shell",
            )),
        ):
            path = Path(str(cache_metadata(previous)["path"]))
            damaged = b'{"payload":"\xe2\x82'
            path.write_bytes(damaged)
            args = (*command,  "--limit", "5")
            recovered = run_cli(registry_path, *args, "--use-projection-cache")
            assert recovered["ok"] is True, recovered
            assert cache_metadata(recovered)["hit"] is False, recovered
            assert cache_metadata(recovered)["miss_reason"] == "unreadable", recovered
            assert successor_id in json.dumps(recovered), recovered
            assert path.read_bytes() == damaged
            if command[0] == "quota":
                assert_next_action(recovered, successor_id)

            rewritten = run_cli(
                registry_path, *args, "--use-projection-cache", "--write-projection-cache",
            )
            assert_cache_written(rewritten)
            reused = run_cli(registry_path, *args, "--use-projection-cache")
            assert_cache_hit(reused)
            assert successor_id in json.dumps(reused), reused

        public_doc.write_text(
            f"Synthetic outgoing content for explicit checking: {PRIVATE_DOC_MARKER}\n",
            encoding="utf-8",
        )
        execution = run_cli(
            registry_path, "quota", "should-run", "--goal-id", GOAL_ID,
            "--agent-id", AGENT_ID,
        )
        assert execution["status_health_ok"] is True, execution
        leak_payload, leak_returncode, _ = run_cli_result(
            registry_path, "check", "--scan-path", str(public_doc), check=False,
        )
        assert leak_returncode != 0 and not leak_payload["ok"], leak_payload
        assert any("private_doc_url" in str(error) for error in leak_payload["errors"]), leak_payload

        cache_dir = runtime / "status-projection-cache"
        assert cache_dir.exists(), cache_dir
        assert list(cache_dir.glob("*.json")), cache_dir
        with ThreadPoolExecutor(max_workers=8) as executor:
            writes = list(
                executor.map(
                    lambda index: write_status_projection_cache(
                        registry_path=registry_path,
                        runtime_root=runtime,
                        limit=5,
                        include_task_graph=False,
                        goal_id=GOAL_ID,
                        payload={"ok": True, "concurrent_write": index},
                        max_age_seconds=120,
                    ),
                    range(16),
                )
            )
        assert all(item.get("written") is True for item in writes), writes
        concurrent_cache_path = Path(str(writes[-1]["path"]))
        assert json.loads(concurrent_cache_path.read_text(encoding="utf-8"))["payload"]["ok"] is True

    print("status-projection-cache-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
