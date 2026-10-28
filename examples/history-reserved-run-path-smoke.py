#!/usr/bin/env python3
"""Smoke-test reserved run paths for same-second append writers."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from loopx import history, state_refresh  # noqa: E402


GOAL_ID = "reserved-run-path-fixture"
FIXED_TIME = "2026-01-01T00:00:00+00:00"


def write_registry(root: Path) -> Path:
    project = root / "project"
    runtime = root / "runtime"
    state_file = f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state_path = project / state_file
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        "---\n"
        "status: reserved-run-path-fixture\n"
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# Reserved Run Path Fixture\n\n"
        "## Next Action\n\n"
        "- continue reserved run path validation\n\n"
        "## Agent Todo\n\n",
        encoding="utf-8",
    )
    registry_path = project / ".loopx" / "registry.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "reserved-run-path-fixture",
                        "status": "active-read-only",
                        "repo": str(project),
                        "state_file": state_file,
                        "adapter": {"kind": "fixture", "status": "connected-read-only"},
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
    import_fixture_authority(registry_path, GOAL_ID, provider="sqlite")
    return registry_path


def artifact_identity(payload: dict[str, object]) -> tuple[str, str, str]:
    return (
        str(payload.get("generated_at") or ""),
        str(payload.get("json_path") or ""),
        str(payload.get("markdown_path") or ""),
    )


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-reserved-run-path-") as raw_tmp:
        root = Path(raw_tmp)
        registry_path = write_registry(root)
        original_refresh_now = state_refresh.now_local
        state_refresh.now_local = lambda: FIXED_TIME
        try:
            payloads = [
                state_refresh.refresh_state_run(
                    registry_path=registry_path,
                    runtime_root_override=None,
                    goal_id=GOAL_ID,
                    project=None,
                    state_file=None,
                    classification=f"reserved_run_path_refresh_{suffix}",
                    recommended_action="continue reserved run path validation",
                    dry_run=False,
                    sync_global=False,
                )
                for suffix in ("a", "b", "c", "d")
            ]
        finally:
            state_refresh.now_local = original_refresh_now

        identities = [artifact_identity(payload) for payload in payloads]
        assert len(identities) == len(set(identities)), identities
        assert [Path(payload["json_path"]).name for payload in payloads] == [
            "2026-01-01T00-00-00-00-00.json",
            "2026-01-01T00-00-00-00-00-2.json",
            "2026-01-01T00-00-00-00-00-3.json",
            "2026-01-01T00-00-00-00-00-4.json",
        ], payloads

        duplicate_report = history.inspect_index_duplicates(
            registry_path=registry_path,
            runtime_root_override=None,
            goal_id=GOAL_ID,
            limit=10,
        )
        assert duplicate_report["duplicate_group_count"] == 0, duplicate_report
        print("history-reserved-run-path-smoke ok")


if __name__ == "__main__":
    main()
