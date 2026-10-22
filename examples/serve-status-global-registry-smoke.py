#!/usr/bin/env python3
"""Smoke-test serve-status global registry selection from a project directory."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def write_state(project: Path, goal_id: str, title: str) -> str:
    state_file = f".codex/goals/{goal_id}/ACTIVE_GOAL_STATE.md"
    path = project / state_file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        "status: active\n"
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        f"# {title}\n\n"
        "## Next Action\n\n"
        "- Continue the next bounded segment.\n\n## Agent Todo\n",
        encoding="utf-8",
    )
    return state_file


def write_project_registry(project: Path, runtime: Path, goal_id: str, title: str) -> Path:
    state_file = write_state(project, goal_id, title)
    registry_path = project / ".loopx" / "registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "updated_at": "2026-01-01T00:00:00+00:00",
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": goal_id,
                        "domain": "serve-status-global-smoke",
                        "status": "active",
                        "repo": str(project),
                        "state_file": state_file,
                        "adapter": {"kind": "smoke_v0", "status": "connected"},
                        "authority_sources": [],
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return registry_path


def wait_for_status(url: str) -> dict:
    deadline = time.time() + 8
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # pragma: no cover - smoke retry path
            last_error = exc
            time.sleep(0.1)
    raise RuntimeError(f"timed out waiting for {url}: {last_error}")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-serve-status-global-") as raw_tmp:
        root = Path(raw_tmp)
        runtime = root / "runtime"
        local_project = root / "local-project"
        global_project = root / "global-project"
        local_registry = write_project_registry(local_project, runtime, "local-only-goal", "Local Only Goal")
        global_source_registry = write_project_registry(global_project, runtime, "global-goal", "Global Goal")

        global_registry = runtime / "registry.global.json"
        runtime.mkdir(parents=True, exist_ok=True)
        global_payload = json.loads(global_source_registry.read_text(encoding="utf-8"))
        global_payload["goals"][0]["source_registry"] = str(global_source_registry)
        global_registry.write_text(json.dumps(global_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        port = free_port()
        command = [
            sys.executable,
            "-m",
            "loopx.cli",
            "--runtime-root",
            str(runtime),
            "serve-status",
            "--global-registry",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--scan-path",
            str(REPO_ROOT / "README.md"),
            "--limit",
            "10",
        ]
        server = subprocess.Popen(
            command,
            cwd=local_project,
            env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            payload = wait_for_status(f"http://127.0.0.1:{port}/status.json")
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive cleanup
                server.kill()
                server.wait(timeout=5)

        assert payload["ok"] is True, payload
        assert Path(payload["registry"]) == global_registry, payload["registry"]
        assert payload["goal_count"] == 1, payload
        assert payload["global_registry"]["current_registry_is_global"] is True, payload["global_registry"]
        assert payload["global_registry"]["summary"]["findings"] == 0, payload["global_registry"]
        assert local_registry.exists(), local_registry
        queue_goal_ids = {item.get("goal_id") for item in payload["attention_queue"]["items"]}
        assert "global-goal" in queue_goal_ids, queue_goal_ids
        assert "local-only-goal" not in queue_goal_ids, queue_goal_ids
    print("serve-status-global-registry-smoke ok")


if __name__ == "__main__":
    main()
