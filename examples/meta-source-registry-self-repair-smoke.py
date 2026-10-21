#!/usr/bin/env python3
"""Smoke-test meta source-registry shadow handling without leaking policy."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.paths import global_registry_path  # noqa: E402
from loopx.quota import build_quota_should_run  # noqa: E402
from loopx.status import collect_status  # noqa: E402


META_GOAL_ID = "loopx-meta"
OTHER_GOAL_ID = "ordinary-control"


def write_state(project: Path, goal_id: str) -> str:
    state_file = f".codex/goals/{goal_id}/ACTIVE_GOAL_STATE.md"
    state_path = project / state_file
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        "---\n"
        "status: active\n"
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        f"# {goal_id}\n\n"
        "## Agent Todo\n\n"
        "- [ ] Repair or verify the source-registry control-plane projection.\n",
        encoding="utf-8",
    )
    return state_file


def goal_entry(
    *,
    goal_id: str,
    project: Path,
    runtime: Path,
    source_registry: Path,
    synced_at: str,
    meta_policy: bool,
) -> dict:
    goal = {
        "id": goal_id,
        "domain": "meta-source-registry-fixture",
        "status": "active",
        "repo": str(project),
        "state_file": write_state(project, goal_id),
        "adapter": {
            "kind": "harness_self_improvement" if meta_policy else "fixture_connected_readonly_v0",
            "status": "connected-read-only",
        },
        "source_registry": str(source_registry),
        "synced_at": synced_at,
        "quota": {
            "compute": 1.0,
            "window_hours": 24,
        },
        "authority_sources": [],
    }
    if meta_policy:
        goal["control_plane"] = {
            "self_repair": {
                "enabled": True,
                "allow_health_blocker_repair": True,
                "allow_waiting_projection_repair": True,
            }
        }
    return goal


def write_global_registry(
    root: Path,
    *,
    finding_kind: str,
) -> tuple[Path, Path, Path]:
    runtime = root / "runtime"
    meta_project = root / "meta-project"
    other_project = root / "ordinary-project"
    meta_project.mkdir()
    other_project.mkdir()
    missing_source = meta_project / ".loopx" / "registry.json"
    stale_source = meta_project / ".loopx" / "stale-registry.json"
    source_registry = missing_source
    synced_at = "2026-01-01T00:00:00+00:00"
    if finding_kind == "stale_source_registry":
        stale_source.parent.mkdir(parents=True, exist_ok=True)
        stale_source.write_text('{"schema_version": 1, "goals": []}\n', encoding="utf-8")
        now = time.time()
        os.utime(stale_source, (now, now))
        source_registry = stale_source

    global_registry = global_registry_path(runtime)
    global_registry.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "updated_at": "2026-01-01T00:00:00+00:00",
        "common_runtime_root": str(runtime),
        "goals": [
            goal_entry(
                goal_id=META_GOAL_ID,
                project=meta_project,
                runtime=runtime,
                source_registry=source_registry,
                synced_at=synced_at,
                meta_policy=True,
            ),
            goal_entry(
                goal_id=OTHER_GOAL_ID,
                project=other_project,
                runtime=runtime,
                source_registry=other_project / ".loopx" / "registry.json",
                synced_at=synced_at,
                meta_policy=False,
            ),
        ],
    }
    global_registry.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return global_registry, runtime, meta_project


def queue_item(status_payload: dict, goal_id: str) -> dict:
    items = status_payload.get("attention_queue", {}).get("items") or []
    matches = [item for item in items if isinstance(item, dict) and item.get("goal_id") == goal_id]
    assert len(matches) == 1, items
    return matches[0]


def assert_meta_source_shadow(finding_kind: str) -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-meta-source-shadow-") as tmp:
        root = Path(tmp)
        registry_path, runtime, project = write_global_registry(root, finding_kind=finding_kind)
        status_payload = collect_status(
            registry_path=registry_path,
            runtime_root_override=str(runtime),
            limit=5,
        )
        findings_text = json.dumps(status_payload["global_registry"], ensure_ascii=False)
        assert finding_kind in findings_text, status_payload["global_registry"]

        meta_item = queue_item(status_payload, META_GOAL_ID)
        assert meta_item["status"] == "connected_without_run", meta_item
        assert not any(
            item.get("goal_id") == META_GOAL_ID and item.get("status") == finding_kind
            for item in status_payload["attention_queue"]["items"]
            if isinstance(item, dict)
        ), status_payload["attention_queue"]["items"]
        shadows = meta_item["global_registry_shadow_findings"]
        assert shadows[0]["kind"] == finding_kind, shadows
        asset_shadow = meta_item["project_asset"]["global_registry_shadow_findings"]
        assert asset_shadow["open"] == 1, asset_shadow
        assert finding_kind in asset_shadow["kinds"], asset_shadow

        meta_decision = build_quota_should_run(status_payload, goal_id=META_GOAL_ID)
        assert meta_decision["should_run"] is True, meta_decision
        assert meta_decision["effective_action"] == "normal_run", meta_decision
        assert meta_decision["plan_summary"]["health_blockers"] == 0, meta_decision
        assert meta_decision["control_plane"]["self_repair"]["enabled"] is True, meta_decision

        other_decision = build_quota_should_run(status_payload, goal_id=OTHER_GOAL_ID)
        assert other_decision["should_run"] is True, other_decision
        assert "control_plane" not in other_decision, other_decision
        assert other_decision["self_repair_allowed"] is False, other_decision


def main() -> int:
    assert_meta_source_shadow("source_registry_missing")
    assert_meta_source_shadow("stale_source_registry")
    print("meta-source-registry-self-repair-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
