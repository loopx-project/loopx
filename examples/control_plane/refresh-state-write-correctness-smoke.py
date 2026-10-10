#!/usr/bin/env python3
"""Smoke-test refresh-state dry-run write correctness packets."""

from __future__ import annotations

import hashlib
import json
import runpy
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import loopx.state_refresh as state_refresh


GOAL_ID = "refresh-state-write-correctness-goal"
GENERATED_AT = "2026-01-02T00:00:00+00:00"


def write_fixture(root: Path) -> tuple[Path, Path, Path]:
    project = root / "project"
    runtime = root / "runtime"
    state_file = f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state_path = project / state_file
    registry_path = project / ".loopx" / "registry.json"

    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        "---\n"
        "status: active\n"
        "owner_mode: goal\n"
        'objective: "Preview refresh-state write correctness."\n'
        "updated_at: 2026-01-02T00:00:00+00:00\n"
        "---\n\n"
        "# Refresh State Write Correctness Fixture\n\n"
        "## Next Action\n\n"
        "- Keep the current route stable.\n",
        encoding="utf-8",
    )
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "updated_at": GENERATED_AT,
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "refresh-state-write-correctness-fixture",
                        "status": "active",
                        "repo": str(project),
                        "state_file": state_file,
                        "adapter": {"kind": "fixture", "status": "connected-read-only"},
                        "authority_sources": [],
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return registry_path, runtime, project


def dry_run_payload(registry_path: Path, runtime: Path, project: Path) -> dict:
    return state_refresh.refresh_state_run(
        registry_path=registry_path,
        runtime_root_override=str(runtime),
        goal_id=GOAL_ID,
        project=project,
        state_file=None,
        classification="state_refreshed",
        recommended_action="preview refresh-state correctness packet",
        next_action=None,
        delivery_batch_scale="single_surface",
        delivery_outcome="surface_only",
        dry_run=True,
        sync_global=False,
    )


def main() -> None:
    original_now_local = state_refresh.now_local
    try:
        state_refresh.now_local = lambda: GENERATED_AT
        with tempfile.TemporaryDirectory(prefix="loopx-refresh-write-correctness-") as raw_tmp:
            registry_path, runtime, project = write_fixture(Path(raw_tmp))
            state_path = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
            state_before, registry_before = state_path.read_bytes(), registry_path.read_bytes()
            first = dry_run_payload(registry_path, runtime, project)
            second = dry_run_payload(registry_path, runtime, project)
            assert state_path.read_bytes() == state_before
            assert registry_path.read_bytes() == registry_before
            assert not runtime.exists()

            assert first["dry_run"] is True, first
            assert first["appended"] is False, first
            packet = first.get("local_state_write_correctness")
            assert isinstance(packet, dict), first
            assert packet["schema_version"] == "local_state_write_correctness_v0", packet

            intent = packet["write_intent"]
            assert intent["goal_id"] == GOAL_ID, packet
            assert intent["writer_id"] == "loopx.refresh-state", packet
            assert intent["write_class"] == "refresh_state", packet
            assert intent["target_refs"]["state_file_ref"] == "registry.goal.state_file", packet
            assert intent["target_refs"]["run_history_ref"] == "runtime.goal.runs", packet
            assert intent["target_refs"]["global_registry_ref"] is None, packet
            expected_revision = "sha256:" + hashlib.sha256(
                (project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md").read_text(
                    encoding="utf-8"
                ).encode("utf-8")
            ).hexdigest()
            assert intent["expected_revision"]["value"] == expected_revision, packet
            assert (
                intent["idempotency_key"]
                == second["local_state_write_correctness"]["write_intent"]["idempotency_key"]
            )

            preview = packet["preview"]
            assert preview["mode"] == "dry_run", packet
            assert preview["non_destructive"] is True, packet
            # A history-only refresh does not rewrite the shared Next Action.
            assert preview["expected_write_scopes"] == ["runtime_history"], packet
            assert "append refresh-state run" in preview["patch_summary"], packet

            assert packet["lock_boundary"]["kind"] == "per_goal", packet
            assert packet["apply_result"]["status"] == "preview_only", packet
            boundary = packet["projection"]["public_boundary"]
            assert boundary == {
                "raw_logs_copied": False,
                "private_paths_copied": False,
                "credentials_copied": False,
                "production_action_authorized": False,
            }, packet
            assert str(project) not in json.dumps(packet, ensure_ascii=False), packet

            markdown = state_refresh.render_state_refresh_markdown(first)
            assert "local_state_write_correctness" in markdown, markdown
            assert "status=preview_only" in markdown, markdown
    finally:
        state_refresh.now_local = original_now_local

    runpy.run_path(
        str(Path(__file__).with_name("refresh-state-shared-runtime-projection-smoke.py")),
        run_name="__main__",
    )
    print("refresh-state-write-correctness-smoke ok")


if __name__ == "__main__":
    main()
