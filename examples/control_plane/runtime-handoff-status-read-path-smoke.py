#!/usr/bin/env python3
"""Keep handoff history intact across fresh and rebuilt current-state routing."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

GOAL_ID = "runtime-handoff-status-read-path-fixture"
AGENT_ID = "codex-product-capability"
TODO_ID = "todo_runtime_handoff_status_read"
TODO_TITLE = "Continue the runtime handoff status read path."
GOAL_NEXT_ACTION = "Keep the goal route stable while runtime handoff is observed."
HANDOFF_READY_CLASSIFICATION = "operator_gate_approved"
POST_HANDOFF_CLASSIFICATION = "runtime_handoff_post_progress"
POST_HANDOFF_ACTION = "Runtime handoff post progress."


def run_cli(
    *args: str,
    registry_path: Path,
    runtime_root: Path,
) -> dict[str, Any]:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--registry",
            str(registry_path),
            "--runtime-root",
            str(runtime_root),
            "--format",
            "json",
            *args,
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(result.stdout + result.stderr)
    return json.loads(result.stdout)


def write_fixture(root: Path, *, stale_run: bool) -> tuple[Path, Path, Path]:
    project = root / "project"
    runtime_root = root / "runtime"
    state_file = f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state_path = project / state_file
    registry_path = project / ".loopx" / "registry.json"
    run_index = runtime_root / "goals" / GOAL_ID / "runs" / "index.jsonl"

    state_path.parent.mkdir(parents=True)
    # Both timestamps use the run's timezone: the original fixture accidentally
    # made its supposedly fresh run older than the state by four hours.
    state_updated_at = (
        "2026-07-05T05:00:00+08:00" if stale_run else "2026-07-05T00:00:00+08:00"
    )
    state_path.write_text(
        "---\n"
        "status: active\n"
        f"updated_at: {state_updated_at}\n"
        "---\n\n"
        "# Runtime Handoff Status Read Path Fixture\n\n"
        "## Next Action\n\n"
        f"- {GOAL_NEXT_ACTION}\n\n"
        "## Agent Todo\n\n"
        f"- [ ] [P1] {TODO_TITLE}\n"
        f"  <!-- loopx:todo todo_id={TODO_ID} status=open "
        "task_class=advancement_task action_kind=runtime_handoff_status_read "
        f"claimed_by={AGENT_ID} -->\n",
        encoding="utf-8",
    )

    run_index.parent.mkdir(parents=True)
    run_rows = [
        {
            "goal_id": GOAL_ID,
            "run_id": "run-handoff-ready",
            "generated_at": "2026-07-05T04:00:00+08:00",
            "classification": HANDOFF_READY_CLASSIFICATION,
            "delivery_batch_scale": "multi_surface",
            "delivery_outcome": "outcome_progress",
            "recommended_action": "Handoff ready.",
        },
        {
            "goal_id": GOAL_ID,
            "run_id": "run-post-handoff",
            "generated_at": "2026-07-05T04:05:00+08:00",
            "classification": POST_HANDOFF_CLASSIFICATION,
            "delivery_batch_scale": "implementation",
            "delivery_outcome": "outcome_progress",
            "recommended_action": POST_HANDOFF_ACTION,
        },
    ]
    artifact_dir = project / "artifacts" / GOAL_ID
    artifact_dir.mkdir(parents=True)
    for row in run_rows:
        run_id = row["run_id"]
        json_path = artifact_dir / f"{run_id}.json"
        markdown_path = artifact_dir / f"{run_id}.md"
        row["json_path"] = str(json_path.relative_to(project))
        row["markdown_path"] = str(markdown_path.relative_to(project))
        json_path.write_text(json.dumps(row, sort_keys=True) + "\n", encoding="utf-8")
        markdown_path.write_text(f"# {row['classification']}\n", encoding="utf-8")
    run_index.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in run_rows),
        encoding="utf-8",
    )

    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "updated_at": "2026-07-05T00:00:00+00:00",
                "common_runtime_root": str(runtime_root),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "control-plane-handoff-read-path",
                        "status": "active",
                        "repo": str(project),
                        "state_file": state_file,
                        "adapter": {
                            "kind": "generic_project_goal_v0",
                            "status": "connected",
                        },
                        "quota": {
                            "compute": 1.0,
                            "window_hours": 24,
                            "slot_minutes": 1,
                            "allowed_slots": 10,
                        },
                        "coordination": {
                            "registered_agents": [AGENT_ID],
                            "agent_model": "peer_v1",
                        },
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
    from tests.control_plane.canonical_authority_fixture import initialize_canonical_source_fixture

    initialize_canonical_source_fixture(runtime_root, GOAL_ID, state_path=state_path)
    return project, runtime_root, registry_path


def status_item(status_payload: dict[str, Any]) -> dict[str, Any]:
    assert status_payload["ok"] is True, status_payload
    assert status_payload["goal_filter"] == GOAL_ID, status_payload
    items = status_payload["attention_queue"]["items"]
    assert len(items) == 1, status_payload
    item = items[0]
    assert item["goal_id"] == GOAL_ID, item
    return item


def assert_handoff_readiness(readiness: dict[str, Any]) -> None:
    assert readiness["ready"] is True, readiness
    assert readiness["codex_ready"] is True, readiness
    assert readiness["source"] == "project_asset", readiness
    assert readiness["quota_state"] == "eligible", readiness
    assert readiness["handoff_status"] == "post_handoff_run_seen", readiness
    assert readiness["post_handoff_run_seen"] is True, readiness
    assert readiness["handoff_ready_classification"] == HANDOFF_READY_CLASSIFICATION, readiness
    assert readiness["handoff_ready_at"] == "2026-07-05T04:00:00+08:00", readiness
    assert readiness["next_probe"] == f"loopx review-packet --goal-id {GOAL_ID} --handoff-only", (
        readiness
    )
    assert readiness["handoff_interface_budget"]["mode"] == "project_agent_handoff", readiness
    assert all(readiness["checks"].values()), readiness

    latest_run = readiness["post_handoff_latest_run"]
    assert latest_run["classification"] == POST_HANDOFF_CLASSIFICATION, readiness
    assert latest_run["delivery_batch_scale"] == "implementation", readiness
    assert latest_run["delivery_outcome"] == "outcome_progress", readiness
    assert latest_run["delivery_turn_kind"] == "compact_evidence", readiness
    assert latest_run["json_exists"] is True, readiness
    assert latest_run["markdown_exists"] is True, readiness
    assert readiness["post_handoff_recent_runs"] == [latest_run], readiness
    assert readiness["post_handoff_small_scale_streak"] == 0, readiness


def assert_status_read_path(
    *,
    project: Path,
    runtime_root: Path,
    registry_path: Path,
    stale_run: bool,
) -> None:
    payload = run_cli(
        "status",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--limit",
        "2",
        registry_path=registry_path,
        runtime_root=runtime_root,
    )
    item = status_item(payload)
    if stale_run:
        # A current Todo read replaces stale progress routing, never its history.
        assert item["source"] == "active_state", item
        assert item["status"] == "active_state_agent_todo", item
        assert item["recommended_action"] == f"[P1] {TODO_TITLE}", item
    else:
        assert item["source"] == "latest_run", item
        assert item["status"] == POST_HANDOFF_CLASSIFICATION, item
        assert item["recommended_action"] == POST_HANDOFF_ACTION, item
    assert item.get("stale_latest_run_warning") is None, item
    assert item["waiting_on"] == "codex", item
    assert item["project_asset"]["active_state_next_action"] == GOAL_NEXT_ACTION, item
    assert item["project_asset"]["latest_validation"]["classification"] == POST_HANDOFF_CLASSIFICATION, item
    assert item["project_asset"]["latest_validation"]["summary"] == POST_HANDOFF_ACTION, item
    assert item["agent_todos"]["items"][0]["todo_id"] == TODO_ID, item
    assert item["project_asset"]["agent_todos"]["payload_reference"]["canonical_path"] == (
        "attention_queue.items[].agent_todos"
    ), item
    assert_handoff_readiness(item["handoff_readiness"])
    assert str(project) not in json.dumps(item, sort_keys=True), item
    assert str(runtime_root) not in json.dumps(item, sort_keys=True), item


def assert_quota_read_path(*, runtime_root: Path, registry_path: Path) -> None:
    payload = run_cli(
        "quota",
        "should-run",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        registry_path=registry_path,
        runtime_root=runtime_root,
    )
    assert payload["ok"] is True, payload
    assert payload["decision"] == "run", payload
    assert payload["effective_action"] == "normal_run", payload
    assert "active_state_next_action" not in payload, payload
    assert payload["goal_route_hint"]["selected_action_differs_from_durable"] is True, payload
    assert payload["goal_route_hint"]["goal_next_action_mutation"] == "none", payload
    next_action = payload["agent_lane_next_action"]
    assert next_action["todo_id"] == TODO_ID, payload
    assert next_action["claimed_by"] == AGENT_ID, payload
    assert next_action["preserves_goal_next_action"] is True, payload
    assert payload["interaction_contract"]["mode"] == "bounded_delivery", payload
    assert payload["work_lane_contract"]["obligation"] == "advance_one_bounded_segment", payload

    readiness = payload["handoff_readiness"]
    assert readiness["handoff_status"] == "post_handoff_run_seen", payload
    assert readiness["post_handoff_run_seen"] is True, payload
    assert readiness["post_handoff_latest_run"]["classification"] == POST_HANDOFF_CLASSIFICATION, (
        payload
    )
    assert readiness["post_handoff_recent_runs"][0]["delivery_batch_scale"] == "implementation", (
        payload
    )


def assert_review_packet_handoff(
    *,
    project: Path,
    runtime_root: Path,
    registry_path: Path,
) -> None:
    payload = run_cli(
        "review-packet",
        "--goal-id",
        GOAL_ID,
        "--handoff-only",
        registry_path=registry_path,
        runtime_root=runtime_root,
    )
    assert payload["ok"] is True, payload
    assert payload["goal_id"] == GOAL_ID, payload
    assert payload["handoff_only"] is True, payload
    assert payload["within_budget"] is True, payload
    handoff = payload["handoff_text"]
    assert f"goal_id=`{GOAL_ID}`" in handoff, handoff
    assert TODO_TITLE in handoff, handoff
    assert f"post_handoff_run={POST_HANDOFF_CLASSIFICATION}" in handoff, handoff
    assert "scale=implementation" in handoff, handoff
    assert "history" in handoff, handoff
    assert str(project) not in handoff, handoff
    assert str(runtime_root) not in handoff, handoff
    assert "packet" not in payload, payload


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="loopx-runtime-handoff-status-read-") as tmp:
        for stale_run in (False, True):
            root = Path(tmp) / ("stale" if stale_run else "fresh")
            project, runtime_root, registry_path = write_fixture(root, stale_run=stale_run)
            files = [path for path in root.rglob("*") if path.is_file()]
            before = {path: path.read_bytes() for path in files}
            assert_status_read_path(
                project=project,
                runtime_root=runtime_root,
                registry_path=registry_path,
                stale_run=stale_run,
            )
            assert_quota_read_path(
                runtime_root=runtime_root,
                registry_path=registry_path,
            )
            assert_review_packet_handoff(
                project=project,
                runtime_root=runtime_root,
                registry_path=registry_path,
            )
            assert {path: path.read_bytes() for path in files} == before
    print("runtime-handoff-status-read-path-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
