#!/usr/bin/env python3
"""Smoke-test local LoopX state backup planning and execution."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# Standalone smoke entrypoints resolve this checkout before importing LoopX.
from loopx.state_backup import (  # noqa: E402
    build_state_backup_plan,
    execute_state_backup_plan,
    render_state_backup_markdown,
)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload: dict[str, object]) -> None:
    write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def seed_fixture(root: Path) -> tuple[Path, Path, Path]:
    home = root / "home"
    codex_home = home / ".codex"
    runtime = codex_home / "loopx"
    project = root / "project"

    write_json(project / ".loopx" / "registry.json", {"schema_version": "0.1", "common_runtime_root": str(runtime),
        "goals": [{"id": "fixture", "repo": str(project), "state_file": ".codex/goals/fixture/ACTIVE_GOAL_STATE.md"}]})
    write_text(project / ".codex" / "goals" / "fixture" / "ACTIVE_GOAL_STATE.md", "# fixture\n")
    write_text(project / ".claude" / "goals" / "fixture" / "ACTIVE_GOAL_STATE.md", "# claude fixture\n")
    write_text(project / ".local" / "goals" / "fixture" / "ACTIVE_GOAL_STATE.md", "# local fixture\n")

    remote_project = root / "remote-project"
    remote_registry = remote_project / ".loopx" / "registry.json"
    remote_state = remote_project / ".codex" / "goals" / "remote" / "ACTIVE_GOAL_STATE.md"
    write_json(remote_registry, {"schema_version": "0.1", "common_runtime_root": str(runtime),
        "goals": [{"id": "remote", "repo": str(remote_project), "state_file": ".codex/goals/remote/ACTIVE_GOAL_STATE.md"}]})
    write_text(remote_state, "# remote fixture\n")
    write_json(
        runtime / "registry.global.json",
        {
            "schema_version": "0.1",
            "common_runtime_root": str(runtime),
            "goals": [
                {
                    "id": "fixture",
                    "repo": str(project),
                    "state_file": ".codex/goals/fixture/ACTIVE_GOAL_STATE.md",
                    "source_registry": str(project / ".loopx" / "registry.json"),
                },
                {
                    "id": "remote",
                    "repo": str(remote_project),
                    "state_file": ".codex/goals/remote/ACTIVE_GOAL_STATE.md",
                    "source_registry": str(remote_registry),
                },
                {
                    "id": "missing-project",
                    "repo": str(root / "missing-project"),
                    "state_file": ".local/goals/missing/ACTIVE_GOAL_STATE.md",
                },
            ],
        },
    )
    write_json(codex_home / "automations" / "fixture.json", {"automation_id": "fixture"})
    write_text(codex_home / "skills" / "loopx-fixture" / "SKILL.md", "# fixture skill\n")

    # This path lives under the default output dir and must not be captured.
    write_text(runtime / "backups" / "old" / "stale.txt", "stale backup\n")
    return project, runtime, codex_home


def run_cli(project: Path, runtime: Path, backup_id: str, env: dict[str, str]) -> dict[str, object]:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--format",
            "json",
            "--runtime-root",
            str(runtime),
            "backup-state",
            "--project",
            str(project),
            "--backup-id",
            backup_id,
            "--execute",
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def assert_archive(payload: dict[str, object]) -> None:
    archive_path = Path(str(payload["archive_path"]))
    manifest_path = Path(str(payload["manifest_path"]))
    assert archive_path.exists(), payload
    assert manifest_path.exists(), payload
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "loopx_state_backup_v0", manifest
    assert manifest["dry_run"] is False, manifest
    execution = manifest["execution"]
    assert execution["archive_sha256"], execution
    assert execution["archive_size_bytes"] > 0, execution
    assert execution["archive_to_logical_ratio"] > 0, execution
    rendered = render_state_backup_markdown(manifest)
    assert "Logical source bytes (before compression)" in rendered, rendered
    assert "Archive/logical ratio" in rendered, rendered
    with tarfile.open(archive_path, "r:gz") as archive:
        names = set(archive.getnames())
    included = {
        str(item["key"]): item
        for item in payload.get("included", [])
        if isinstance(item, dict) and item.get("key")
    }
    assert "runtime-root/registry.global.json" in names, names
    assert "project/.loopx/registry.json" in names, names
    assert "project/.codex/goals/fixture/ACTIVE_GOAL_STATE.md" in names, names
    assert "project/.claude/goals/fixture/ACTIVE_GOAL_STATE.md" in names, names
    assert "project/.local/goals/fixture/ACTIVE_GOAL_STATE.md" in names, names
    assert "codex/automations/fixture.json" in names, names
    assert "codex/skills/loopx-fixture/SKILL.md" in names, names
    remote_goals = included["registry_project_codex_goals:remote"]
    remote_state = included["registry_active_state:remote"]
    remote_registry = included["registry_source_registry:remote"]
    assert f"{remote_goals['archive_path']}/remote/ACTIVE_GOAL_STATE.md" in names, names
    assert str(remote_state["archive_path"]) in names, names
    assert str(remote_registry["archive_path"]) in names, names
    assert "manifest.json" in names, names
    assert "runtime-root/backups/old/stale.txt" not in names, names


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="loopx-state-backup-smoke-") as tmp:
        root = Path(tmp)
        project, runtime, codex_home = seed_fixture(root)
        env = {
            **os.environ,
            "HOME": str(codex_home.parent),
            "CODEX_HOME": str(codex_home),
            "PYTHONPATH": str(REPO_ROOT),
        }
        old_home = os.environ.get("HOME")
        old_codex_home = os.environ.get("CODEX_HOME")
        os.environ["HOME"] = str(codex_home.parent)
        os.environ["CODEX_HOME"] = str(codex_home)
        try:
            plan = build_state_backup_plan(project=project, runtime_root=runtime, backup_id="module-fixture")
            assert plan["ok"] is True, plan
            assert plan["dry_run"] is True, plan
            assert plan["summary"]["included_target_count"] >= 5, plan
            summary = plan["summary"]
            assert summary["logical_source_bytes"] == summary["total_stats"]["bytes"], summary
            category_stats = summary["category_stats"]
            assert category_stats["runtime"]["bytes"] > 0, category_stats
            assert category_stats["project_state"]["bytes"] > 0, category_stats
            assert category_stats["active_state_routes"]["target_count"] == 2, category_stats
            assert category_stats["source_registries"]["target_count"] == 2, category_stats
            assert category_stats["automations"]["target_count"] == 1, category_stats
            assert category_stats["skills"]["target_count"] == 1, category_stats
            overlap = summary["contained_overlap_stats"]
            assert overlap["contained_target_count"] >= 4, overlap
            assert overlap["logical_bytes"] > 0, overlap
            assert overlap["unique_source_bytes_estimate"] < summary["logical_source_bytes"], overlap
            assert plan["registry_discovery"] == {
                "enabled": True,
                "global_registry": str((runtime / "registry.global.json").resolve()),
                "goal_count": 3,
                "project_count": 3,
                "reachable_project_count": 2,
                "missing_project_count": 1,
                "active_state_route_count": 3,
                "active_state_included_count": 2,
                "active_state_missing_count": 1,
                "source_registry_route_count": 2,
                "source_registry_included_count": 2,
                "source_registry_missing_count": 0,
                "goal_runtime_roots": {goal_id: "runtime-root" for goal_id in ("fixture", "remote", "missing-project")},
            }, plan
            assert any(
                item.get("key") == "registry_project:missing-project"
                for item in plan["missing"]
            ), plan
            applied = execute_state_backup_plan(plan)
            assert applied["ok"] is True, applied
            assert applied["dry_run"] is False, applied
            assert_archive(applied)
            current_only = build_state_backup_plan(
                project=project,
                runtime_root=runtime,
                backup_id="current-only-fixture",
                include_registry_projects=False,
            )
            assert current_only["registry_discovery"]["enabled"] is False, current_only
            assert current_only["summary"]["logical_source_bytes"] > 0, current_only
            assert current_only["registry_discovery"]["goal_runtime_roots"] == {"fixture": "runtime-root"}, current_only
            assert not any(
                Path(item["source_path"]).resolve().is_relative_to((root / "remote-project").resolve())
                for item in current_only["included"]
            ), current_only
        finally:
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home
            if old_codex_home is None:
                os.environ.pop("CODEX_HOME", None)
            else:
                os.environ["CODEX_HOME"] = old_codex_home

        cli_payload = run_cli(project, runtime, "cli-fixture", env)
        assert cli_payload["ok"] is True, cli_payload
        assert cli_payload["summary"]["included_target_count"] >= 5, cli_payload
        assert_archive(cli_payload)

    print("state-backup-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
