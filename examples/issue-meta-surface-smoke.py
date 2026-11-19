#!/usr/bin/env python3
"""Smoke-test public-safe issue meta surface projection."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.status import parse_issue_meta_surface, project_asset_summary_is_public_safe  # noqa: E402


GOAL_ID = "issue-meta-surface-fixture"
ISSUE_META_LINE = (
    "- anchor_id=issue_anchor_parser_bug repo=sample-org/sample-repo "
    "issue=#128 labels=bug,good-first-issue owner_route=repo_maintainer_review "
    "related_code=src/parser.py validation=unit_smoke "
    "promotion_target=agent_todo:todo_issue_fix status=selected_anchor freshness=fresh"
)


def state_text() -> str:
    return (
        "---\n"
        "status: active\n"
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# Issue Meta Surface Fixture\n\n"
        "## Next Action\n\n"
        "- Promote the public issue anchor into a bounded solver todo.\n\n"
        "## Issue Meta Surface\n\n"
        f"{ISSUE_META_LINE}\n\n"
        "## Agent Todo\n\n"
        "- [ ] [P1] Promote issue_anchor_parser_bug into an approved solver handoff.\n"
    )


def run_cli(*args: str, registry_path: Path, runtime: Path) -> dict:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--registry",
            str(registry_path),
            "--runtime-root",
            str(runtime),
            "--format",
            "json",
            *args,
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def write_fixture(root: Path) -> tuple[Path, Path]:
    project = root / "project"
    runtime = root / "runtime"
    state_file = f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state_path = project / state_file
    registry_path = project / ".loopx" / "registry.json"

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(state_text(), encoding="utf-8")
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "issue-meta-surface-fixture",
                        "status": "active",
                        "repo": str(project),
                        "state_file": state_file,
                        "adapter": {
                            "kind": "harness_self_improvement",
                            "status": "connected-read-only",
                        },
                        "authority_sources": [],
                        "quota": {
                            "compute": 1.0,
                            "window_hours": 24,
                            "allowed_slots": 5,
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
    from loopx.control_plane.testing.canary_harness import import_fixture_authority

    for record in json.loads(registry_path.read_text())["goals"]:
        import_fixture_authority(registry_path, record["id"], provider="sqlite")
    return registry_path, runtime


def assert_issue_meta_surface(surface: dict) -> None:
    assert surface["schema_version"] == "issue_meta_surface_v0", surface
    assert surface["source_section"] == "Issue Meta Surface", surface
    assert surface["item_count"] == 1, surface
    item = surface["items"][0]
    assert item["schema_version"] == "issue_meta_surface_item_v0", item
    assert item["anchor_id"] == "issue_anchor_parser_bug", item
    assert item["repo_handle"] == "sample-org/sample-repo", item
    assert item["issue_handle"] == "#128", item
    assert item["labels"] == ["bug", "good-first-issue"], item
    assert item["owner_route"] == "repo_maintainer_review", item
    assert item["related_code_hint"] == "src/parser.py", item
    assert item["validation_surface"] == "unit_smoke", item
    assert item["promotion_target"] == "agent_todo:todo_issue_fix", item
    assert item["status"] == "selected_anchor", item
    assert item["freshness"] == "fresh", item
    assert project_asset_summary_is_public_safe({"issue_meta_surface": surface}), surface


def main() -> int:
    parsed = parse_issue_meta_surface(state_text())
    assert parsed is not None
    assert_issue_meta_surface(parsed)

    with tempfile.TemporaryDirectory(prefix="loopx-issue-meta-surface-") as tmp:
        registry_path, runtime = write_fixture(Path(tmp))
        status_payload = run_cli("status", registry_path=registry_path, runtime=runtime)
        items = status_payload.get("attention_queue", {}).get("items") or []
        assert len(items) == 1, status_payload
        item = items[0]
        assert_issue_meta_surface(item["issue_meta_surface"])
        assert_issue_meta_surface(item["project_asset"]["issue_meta_surface"])

    print("issue-meta-surface-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
