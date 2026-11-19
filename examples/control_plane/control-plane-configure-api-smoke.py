#!/usr/bin/env python3
"""Smoke-test local dashboard control-plane settings dry-run/apply APIs."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


GOAL_ID = "configure-api-smoke-goal"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def request_json(method: str, url: str, payload: dict[str, Any] | None = None, *, origin: str | None = None) -> tuple[int, dict[str, Any]]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    if origin:
        headers["Origin"] = origin
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return int(response.status), json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return int(error.code), json.loads(error.read().decode("utf-8"))


def wait_for_health(base_url: str) -> None:
    deadline = time.time() + 10
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            status, payload = request_json("GET", f"{base_url}/healthz")
            if status == 200 and payload.get("ok") is True:
                return
        except Exception as exc:  # noqa: BLE001 - preserve startup diagnostics.
            last_error = exc
        time.sleep(0.1)
    raise RuntimeError(f"server did not become healthy: {last_error}")


def start_server(repo_root: Path, registry: Path, runtime_root: Path, port: int, *, write_enabled: bool) -> subprocess.Popen[str]:
    command = [
        sys.executable,
        "-m",
        "loopx.cli",
        "--registry",
        str(registry),
        "--runtime-root",
        str(runtime_root),
        "serve-status",
        "--port",
        str(port),
        "--scan-root",
        str(registry.parent),
    ]
    if write_enabled:
        command.append("--enable-control-plane-write-api")
    server = subprocess.Popen(
        command,
        cwd=repo_root,
        env={**os.environ, "PYTHONPATH": str(repo_root)},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    wait_for_health(f"http://127.0.0.1:{port}")
    return server


def stop_server(server: subprocess.Popen[str]) -> None:
    server.terminate()
    try:
        server.wait(timeout=5)
    except subprocess.TimeoutExpired:
        server.kill()
        server.wait(timeout=5)


def write_fixture(root: Path) -> tuple[Path, Path]:
    runtime_root = root / "runtime"
    project = root / "project"
    project.mkdir(parents=True)
    (project / "STATE.md").write_text("---\nupdated_at: 2026-01-01T00:00:00+00:00\n---\n", encoding="utf-8")
    registry = root / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "common_runtime_root": str(runtime_root),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "repo": str(project),
                        "state_file": "STATE.md",
                        "domain": "configure-api-smoke",
                        "status": "connected-read-only",
                        "adapter": {"kind": "smoke", "status": "connected-read-only"},
                        "quota": {"compute": 1, "window_hours": 24},
                        "control_plane": {
                            "self_repair": {
                                "enabled": False,
                                "allow_health_blocker_repair": False,
                                "allow_waiting_projection_repair": False,
                            }
                        },
                        "spawn_policy": {
                            "mode": "default",
                            "allowed": False,
                            "max_children": 0,
                            "allowed_domains": [],
                        },
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    from loopx.control_plane.testing.canary_harness import import_fixture_authority

    import_fixture_authority(registry, GOAL_ID, provider="sqlite")
    return registry, runtime_root


def goal_from_registry(registry: Path) -> dict[str, Any]:
    return json.loads(registry.read_text(encoding="utf-8"))["goals"][0]


def queue_item(status_payload: dict[str, Any]) -> dict[str, Any]:
    items = status_payload.get("attention_queue", {}).get("items") or []
    item = next((entry for entry in items if entry.get("goal_id") == GOAL_ID), None)
    assert isinstance(item, dict), status_payload
    return item


def assert_status_settings(
    status_payload: dict[str, Any],
    *,
    quota_compute: float,
    quota_window_hours: float,
    self_repair_enabled: bool,
    orchestration_mode: str,
    spawn_allowed: bool,
    max_children: int,
    allowed_domains: list[str] | None = None,
) -> None:
    item = queue_item(status_payload)
    asset = item.get("project_asset")
    assert isinstance(asset, dict), item
    quota = item.get("quota")
    assert isinstance(quota, dict), item
    assert quota["compute"] == quota_compute, item
    assert quota["window_hours"] == quota_window_hours, item
    asset_quota = asset.get("quota")
    assert isinstance(asset_quota, dict), item
    assert asset_quota["compute"] == quota_compute, item
    control_plane = item.get("control_plane")
    assert isinstance(control_plane, dict), item
    self_repair = control_plane.get("self_repair")
    assert isinstance(self_repair, dict), item
    assert self_repair["enabled"] is self_repair_enabled, item
    asset_control_plane = asset.get("control_plane")
    assert asset_control_plane == control_plane, item
    orchestration = asset.get("orchestration")
    assert isinstance(orchestration, dict), item
    assert orchestration["mode"] == orchestration_mode, item
    assert orchestration["spawn_allowed"] is spawn_allowed, item
    assert orchestration["max_children"] == max_children, item
    if allowed_domains is None:
        assert "allowed_domains" not in orchestration, item
    else:
        assert orchestration["allowed_domains"] == allowed_domains, item


def configure_payload() -> dict[str, Any]:
    return {
        "goal_id": GOAL_ID,
        "quota_compute": 0.5,
        "quota_window_hours": 12,
        "self_repair_enabled": True,
        "self_repair_health": True,
        "self_repair_waiting_projection": True,
        "multi_subagent_feature": "enabled",
        "max_children": 2,
        "allowed_domains": ["docs", "validation"],
        "clear_allowed_domains": False,
        "registered_agents": ["codex-main-control", "codex-side-bypass"],
        "todo_lifecycle_authority": [
            {
                "agent_id": "codex-main-control",
                "actions": ["complete", "reassign", "supersede"],
                "requires_reason": True,
            }
        ],
        "agent_model": "peer_v1",
        "clear_registered_agents": False,
        "write_scope": ["docs/**", "tests/**"],
        "replace_write_scope": False,
        "clear_write_scope": False,
    }


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix="loopx-configure-api-smoke-") as raw_tmp:
        registry, runtime_root = write_fixture(Path(raw_tmp))
        disabled_port = free_port()
        disabled_server = start_server(repo_root, registry, runtime_root, disabled_port, write_enabled=False)
        try:
            base_url = f"http://127.0.0.1:{disabled_port}"
            status, status_payload = request_json("GET", f"{base_url}/status.json")
            assert status == 200, status_payload
            disabled_api = status_payload["local_dashboard_api"]
            assert disabled_api["control_plane_write_enabled"] is False, disabled_api
            assert disabled_api["configure_goal_dry_run_url"] == "/control-plane/configure-goal/dry-run", disabled_api
            assert disabled_api["configure_goal_apply_url"] is None, disabled_api
            assert_status_settings(
                status_payload,
                quota_compute=1.0,
                quota_window_hours=24,
                self_repair_enabled=False,
                orchestration_mode="default",
                spawn_allowed=False,
                max_children=0,
            )
            status, dry = request_json("POST", f"{base_url}/control-plane/configure-goal/dry-run", configure_payload())
            assert status == 200, dry
            assert dry["changed"] is True, dry
            assert dry["written"] is False, dry
            assert dry["after"]["registered_agents"] == ["codex-main-control", "codex-side-bypass"], dry
            assert dry["after"]["agent_model"] == "peer_v1", dry
            assert dry["after"]["todo_lifecycle_authority"] == configure_payload()[
                "todo_lifecycle_authority"
            ], dry
            assert dry["after"]["write_scope"] == ["docs/**", "tests/**"], dry
            assert dry["feature_summary"]["multi_subagent"] == "enabled", dry
            assert goal_from_registry(registry)["quota"]["compute"] == 1
            status, blocked = request_json(
                "POST",
                f"{base_url}/control-plane/configure-goal/apply",
                {**configure_payload(), "preview_id": dry["preview_id"]},
                origin="http://127.0.0.1:5173",
            )
            assert status == 403, blocked
            assert blocked["written"] is False, blocked
            status, status_payload = request_json("GET", f"{base_url}/status.json")
            assert status == 200, status_payload
            assert status_payload["local_dashboard_api"]["configure_goal_apply_url"] is None, status_payload
            assert_status_settings(
                status_payload,
                quota_compute=1.0,
                quota_window_hours=24,
                self_repair_enabled=False,
                orchestration_mode="default",
                spawn_allowed=False,
                max_children=0,
            )
        finally:
            stop_server(disabled_server)

        non_loopback = subprocess.run(
            [
                sys.executable,
                "-m",
                "loopx.cli",
                "--format",
                "json",
                "--registry",
                str(registry),
                "--runtime-root",
                str(runtime_root),
                "serve-status",
                "--host",
                "0.0.0.0",
                "--port",
                str(free_port()),
                "--enable-control-plane-write-api",
            ],
            cwd=repo_root,
            env={**os.environ, "PYTHONPATH": str(repo_root)},
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert non_loopback.returncode == 1, non_loopback.stdout + non_loopback.stderr
        assert "requires a loopback" in json.loads(non_loopback.stdout)["error"], non_loopback.stdout

        port = free_port()
        server = start_server(repo_root, registry, runtime_root, port, write_enabled=True)
        try:
            base_url = f"http://127.0.0.1:{port}"
            status, status_payload = request_json("GET", f"{base_url}/status.json")
            assert status == 200, status_payload
            enabled_api = status_payload["local_dashboard_api"]
            assert enabled_api["control_plane_write_enabled"] is True, enabled_api
            assert enabled_api["configure_goal_apply_url"] == "/control-plane/configure-goal/apply", enabled_api
            status, dry = request_json("POST", f"{base_url}/control-plane/configure-goal/dry-run", configure_payload())
            assert status == 200, dry
            assert dry["preview_id"], dry
            assert dry["after"]["registered_agents"] == ["codex-main-control", "codex-side-bypass"], dry
            assert dry["after"]["agent_model"] == "peer_v1", dry
            assert dry["after"]["todo_lifecycle_authority"] == configure_payload()[
                "todo_lifecycle_authority"
            ], dry
            assert dry["after"]["write_scope"] == ["docs/**", "tests/**"], dry
            assert dry["feature_summary"]["multi_subagent"] == "enabled", dry
            status, stale = request_json(
                "POST",
                f"{base_url}/control-plane/configure-goal/apply",
                {**configure_payload(), "preview_id": "stale-preview"},
                origin="http://127.0.0.1:5173",
            )
            assert status == 409, stale
            assert stale["written"] is False, stale
            status, forbidden = request_json(
                "POST",
                f"{base_url}/control-plane/configure-goal/apply",
                {**configure_payload(), "preview_id": dry["preview_id"]},
                origin="https://example.com",
            )
            assert status == 403, forbidden
            assert forbidden["written"] is False, forbidden
            status, applied = request_json(
                "POST",
                f"{base_url}/control-plane/configure-goal/apply",
                {**configure_payload(), "preview_id": dry["preview_id"]},
                origin="http://127.0.0.1:5173",
            )
            assert status == 200, applied
            assert applied["written"] is True, applied
            assert applied["feature_summary"]["multi_subagent"] == "enabled", applied
            goal = goal_from_registry(registry)
            assert goal["quota"]["compute"] == 0.5, goal
            assert goal["quota"]["window_hours"] == 12, goal
            assert goal["control_plane"]["self_repair"]["enabled"] is True, goal
            assert goal["spawn_policy"]["mode"] == "multi_subagent", goal
            assert goal["spawn_policy"]["allowed"] is True, goal
            assert goal["spawn_policy"]["max_children"] == 2, goal
            assert goal["spawn_policy"]["allowed_domains"] == ["docs", "validation"], goal
            assert goal["coordination"]["registered_agents"] == ["codex-main-control", "codex-side-bypass"], goal
            assert goal["coordination"]["agent_model"] == "peer_v1", goal
            assert goal["coordination"]["todo_lifecycle_authority"] == (
                configure_payload()["todo_lifecycle_authority"]
            ), goal
            assert goal["coordination"]["write_scope"] == ["docs/**", "tests/**"], goal
            status, refreshed = request_json("GET", f"{base_url}/status.json")
            assert status == 200, refreshed
            assert refreshed["local_dashboard_api"]["control_plane_write_enabled"] is True, refreshed
            assert refreshed["local_dashboard_api"]["configure_goal_apply_url"] == "/control-plane/configure-goal/apply", refreshed
            assert_status_settings(
                refreshed,
                quota_compute=0.5,
                quota_window_hours=12,
                self_repair_enabled=True,
                orchestration_mode="multi_subagent",
                spawn_allowed=True,
                max_children=2,
                allowed_domains=["docs", "validation"],
            )
        finally:
            stop_server(server)

    print("control-plane-configure-api-smoke ok")


if __name__ == "__main__":
    main()
