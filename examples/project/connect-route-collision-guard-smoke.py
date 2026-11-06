#!/usr/bin/env python3
"""Smoke-test global route collision protection for connect/sync paths."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
GOAL_ID = "loopx-meta-fixture"


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def goal_entry(project: Path, registry_path: Path, *, agents: list[str] | None = None) -> dict:
    coordination = {
        "write_scope": [],
        "claim_ttl_minutes": 30,
        "requires_parent_approval": ["write", "publish", "production-action"],
    }
    if agents is not None:
        coordination["registered_agents"] = agents
        coordination["agent_model"] = "peer_v1"
    return {
        "id": GOAL_ID,
        "domain": "collision-smoke",
        "status": "active",
        "repo": str(project),
        "state_file": ".codex/goals/loopx-meta-fixture/ACTIVE_GOAL_STATE.md",
        "adapter": {"kind": "fixture", "status": "connected-read-only"},
        "coordination": coordination,
        "source_registry": str(registry_path),
    }


def write_fixture(root: Path) -> tuple[Path, Path, Path, Path]:
    runtime = root / "runtime"
    source_project = root / "source"
    intruder_project = root / "intruder"
    source_registry = source_project / ".loopx" / "registry.json"
    intruder_registry = intruder_project / ".loopx" / "registry.json"

    for project in (source_project, intruder_project):
        state = project / ".codex/goals/loopx-meta-fixture/ACTIVE_GOAL_STATE.md"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text("# Active Goal State\n\n## Agent Todo\n\n", encoding="utf-8")

    source_payload = {
        "schema_version": "0.1",
        "common_runtime_root": str(runtime),
        "goals": [goal_entry(source_project, source_registry, agents=["codex-main-control"])],
    }
    intruder_payload = {
        "schema_version": "0.1",
        "common_runtime_root": str(runtime),
        "goals": [goal_entry(intruder_project, intruder_registry)],
    }
    write_json(source_registry, source_payload)
    write_json(intruder_registry, intruder_payload)
    global_payload = {
        "schema_version": "0.1",
        "common_runtime_root": str(runtime),
        "registry_role": "global-local",
        "goals": [goal_entry(source_project, source_registry, agents=["codex-main-control"])],
    }
    write_json(runtime / "registry.global.json", global_payload)
    return runtime, source_registry, intruder_registry, runtime / "registry.global.json"


def run_cli(
    registry: Path | None,
    *args: str,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, "-m", "loopx.cli", "--format", "json"]
    if registry is not None:
        command.extend(["--registry", str(registry)])
    command.extend(args)
    process_env = os.environ.copy()
    if env:
        process_env.update(env)
    process_env["LOOPX_USAGE_PING"] = "0"
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        check=check,
        text=True,
        capture_output=True,
        env=process_env,
    )


def payload(result: subprocess.CompletedProcess[str]) -> dict:
    return json.loads(result.stdout)


def only_goal(registry_path: Path) -> dict:
    return json.loads(registry_path.read_text(encoding="utf-8"))["goals"][0]


def assert_sync_collision_guard(root: Path) -> None:
    _, source_registry, intruder_registry, global_registry = write_fixture(root)
    original_global = global_registry.read_text(encoding="utf-8")

    blocked = payload(
        run_cli(intruder_registry, "sync-global", "--goal-id", GOAL_ID, check=False)
    )
    assert blocked["ok"] is False, blocked
    assert "global route collision" in blocked["error"], blocked
    assert global_registry.read_text(encoding="utf-8") == original_global

    dry_replace = payload(
        run_cli(
            intruder_registry,
            "sync-global",
            "--goal-id",
            GOAL_ID,
            "--replace-state",
            "--dry-run",
        )
    )
    assert dry_replace["ok"] is True, dry_replace
    assert dry_replace["route_replacement_allowed"] is True, dry_replace
    assert dry_replace["route_collisions"], dry_replace
    assert global_registry.read_text(encoding="utf-8") == original_global

    replaced = payload(
        run_cli(
            intruder_registry,
            "sync-global",
            "--goal-id",
            GOAL_ID,
            "--replace-state",
        )
    )
    assert replaced["ok"] is True, replaced
    assert replaced["backup_path"], replaced
    assert Path(replaced["backup_path"]).exists(), replaced
    assert Path(only_goal(global_registry)["source_registry"]).resolve() == intruder_registry.resolve()
    assert Path(only_goal(Path(replaced["backup_path"]))["source_registry"]).resolve() == source_registry.resolve()


def assert_register_agent_uses_source_registry(root: Path) -> None:
    runtime, source_registry, _intruder_registry, global_registry = write_fixture(root)

    collision = payload(
        run_cli(
            None,
            "--runtime-root",
            str(runtime),
            "register-agent",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            "codex-main-control",
            "--require-new",
            check=False,
        )
    )
    assert collision["ok"] is False, collision
    assert collision["error_kind"] == "agent_identity_already_registered", collision
    assert collision["changed"] is False, collision
    assert only_goal(source_registry)["coordination"]["registered_agents"] == [
        "codex-main-control"
    ]

    preview = payload(
        run_cli(
            None,
            "--runtime-root",
            str(runtime),
            "register-agent",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            "codex-product-capability",
            "--require-new",
        )
    )
    assert preview["ok"] is True, preview
    assert preview["dry_run"] is True, preview
    assert preview["source_registry"] == str(source_registry), preview
    assert only_goal(source_registry)["coordination"]["registered_agents"] == ["codex-main-control"]

    applied = payload(
        run_cli(
            None,
            "--runtime-root",
            str(runtime),
            "register-agent",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            "codex-product-capability",
            "--require-new",
            "--execute",
        )
    )
    assert applied["ok"] is True, applied
    assert applied["written"] is True, applied
    assert applied["global_sync"]["ok"] is True, applied
    assert applied["registration_readback"]["verified"] is True, applied
    source_agents = only_goal(source_registry)["coordination"]["registered_agents"]
    assert source_agents == ["codex-main-control", "codex-product-capability"], source_agents
    global_agents = only_goal(global_registry)["coordination"]["registered_agents"]
    assert global_agents == source_agents, global_agents
    assert Path(only_goal(global_registry)["source_registry"]).resolve() == source_registry.resolve()

    idempotent = payload(
        run_cli(
            None,
            "--runtime-root",
            str(runtime),
            "register-agent",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            "codex-product-capability",
        )
    )
    assert idempotent["ok"] is True, idempotent
    assert idempotent["changed"] is False, idempotent

    stale_preview = payload(
        run_cli(
            None,
            "--runtime-root",
            str(runtime),
            "register-agent",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            "codex-stale-preview",
            "--require-new",
        )
    )
    assert stale_preview["changed"] is True, stale_preview
    assert stale_preview["written"] is False, stale_preview

    competing_apply = payload(
        run_cli(
            None,
            "--runtime-root",
            str(runtime),
            "register-agent",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            "codex-stale-preview",
            "--require-new",
            "--execute",
        )
    )
    assert competing_apply["ok"] is True, competing_apply

    stale_apply = payload(
        run_cli(
            None,
            "--runtime-root",
            str(runtime),
            "register-agent",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            "codex-stale-preview",
            "--require-new",
            "--execute",
            check=False,
        )
    )
    assert stale_apply["ok"] is False, stale_apply
    assert stale_apply["error_kind"] == "agent_identity_already_registered", stale_apply
    assert stale_apply["written"] is False, stale_apply


def assert_register_agent_preserves_default_global_route(root: Path) -> None:
    home = root / "home"
    shared_runtime = home / ".codex" / "loopx"
    project = root / "source"
    source_registry = project / ".loopx" / "registry.json"
    project_runtime = project / ".loopx" / "runtime"
    state_file = project / ".codex/goals/loopx-meta-fixture/ACTIVE_GOAL_STATE.md"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text("# Active Goal State\n", encoding="utf-8")

    source_payload = {
        "schema_version": "0.1",
        "common_runtime_root": str(project_runtime),
        "goals": [goal_entry(project, source_registry, agents=["codex-main-control"])],
    }
    write_json(source_registry, source_payload)
    global_payload = {
        "schema_version": "0.1",
        "common_runtime_root": str(shared_runtime),
        "registry_role": "global-local",
        "goals": [goal_entry(project, source_registry, agents=["codex-main-control"])],
    }
    shared_registry = shared_runtime / "registry.global.json"
    write_json(shared_registry, global_payload)

    applied = payload(
        run_cli(
            None,
            "register-agent",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            "codex-meta-peer",
            "--execute",
            env={"HOME": str(home)},
        )
    )

    assert applied["ok"] is True, applied
    assert Path(applied["global_registry"]).resolve() == shared_registry.resolve(), applied
    expected_agents = ["codex-main-control", "codex-meta-peer"]
    assert only_goal(source_registry)["coordination"]["registered_agents"] == expected_agents
    assert only_goal(shared_registry)["coordination"]["registered_agents"] == expected_agents
    assert not (project_runtime / "registry.global.json").exists(), applied


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="loopx-connect-route-collision-") as tmp:
        root = Path(tmp)
        assert_sync_collision_guard(root / "collision")
        assert_register_agent_uses_source_registry(root / "register-agent")
        assert_register_agent_preserves_default_global_route(root / "default-global-route")
    print("connect-route-collision-guard-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
