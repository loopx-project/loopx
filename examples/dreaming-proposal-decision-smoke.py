#!/usr/bin/env python3
"""Smoke-test explicit dreaming proposal decisions and promotion."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted  # noqa: E402
from loopx.control_plane.testing.canary_harness import import_fixture_authority, run_json_cli  # noqa: E402

GOAL_ID = "dreaming-decision-fixture"


def run_cli(*args: str, registry_path: Path, runtime: Path) -> dict:
    return run_json_cli(*args, registry_path=registry_path, runtime_root=runtime,
                        cwd=REPO_ROOT, include_returncode=False)


def append_recorded_proposal(
    runs_dir: Path,
    *,
    generated_at: str,
    proposal_id: str,
) -> None:
    runs_dir.mkdir(parents=True, exist_ok=True)
    json_path = runs_dir / f"{proposal_id}.json"
    markdown_path = runs_dir / f"{proposal_id}.md"
    record = {
        "generated_at": generated_at,
        "goal_id": GOAL_ID,
        "classification": "dreaming_refactor_warning",
        "recommended_action": "Review the advisory dreaming proposal before promotion.",
        "operator_question": "Should this proposal become a normal delivery todo?",
        "dreaming": {
            "schema_version": "dreaming_proposal_v0",
            "proposal_id": proposal_id,
            "lane": "exploration",
            "evidence_window": "last_3_non_neutral_runs",
            "proposal_type": "refactor_warning",
            "confidence": "medium",
            "requires_project_controller": True,
            "advisory": True,
            "promoted_to_delivery": False,
            "execution_allowed": False,
            "delivery_spend_allowed": False,
            "server_planning_contract": {
                "schema_version": "server_managed_planning_contract_v0",
                "lane": "dreaming_planning",
                "authority": "proposal_only_until_promoted",
                "may_rank_candidate_todos": True,
                "may_suggest_evidence_probes": True,
                "may_execute_protected_actions": False,
                "may_read_private_material": False,
                "may_mutate_active_state": False,
                "may_append_delivery_history": False,
                "may_spend_delivery_quota": False,
                "promotion_required": True,
            },
        },
        "json_path": str(json_path),
        "markdown_path": str(markdown_path),
    }
    json_path.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text("# Recorded Dreaming Proposal\n", encoding="utf-8")
    with (runs_dir / "index.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


def write_fixture(root: Path, provider: str) -> tuple[Path, Path, Path, Path]:
    project = root / "project"
    runtime = root / "runtime"
    state_file = f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state_path = project / state_file
    registry_path = project / ".loopx" / "registry.json"
    runs_dir = runtime / "goals" / GOAL_ID / "runs"

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        "---\n"
        "status: active\n"
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# Dreaming Decision Fixture\n\n"
        "## Next Action\n\n"
        "- Continue delivery only after explicit proposal decisions.\n\n"
        "## Agent Todo\n\n"
        "- [ ] [P1] Keep existing normal delivery separate from advisory dreaming.\n",
        encoding="utf-8",
    )
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
                        "domain": "dreaming-decision-fixture",
                        "status": "active",
                        "repo": str(project),
                        "state_file": state_file,
                        "adapter": {
                            "kind": "harness_self_improvement",
                            "status": "connected-read-only",
                        },
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
    import_fixture_authority(registry_path, GOAL_ID, provider=provider)
    append_recorded_proposal(
        runs_dir,
        generated_at="2026-01-01T00:00:00+00:00",
        proposal_id="dreaming_fixture_approve",
    )
    append_recorded_proposal(
        runs_dir,
        generated_at="2026-01-01T00:01:00+00:00",
        proposal_id="dreaming_fixture_defer",
    )
    append_recorded_proposal(
        runs_dir,
        generated_at="2026-01-01T00:02:00+00:00",
        proposal_id="dreaming_fixture_reject",
    )
    return registry_path, runtime, state_path, runs_dir / "index.jsonl"


def assert_no_quota_spend(runtime: Path) -> None:
    text = "\n".join(path.read_text(encoding="utf-8") for path in runtime.rglob("*.json*"))
    assert "quota_slot_spent" not in text, text


def assert_decisions(provider: str) -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-dreaming-decision-") as tmp:
        registry_path, runtime, state_path, index_path = write_fixture(Path(tmp), provider)
        original = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID)

        approve = run_cli(
            "dreaming",
            "decide",
            "--goal-id",
            GOAL_ID,
            "--proposal-id",
            "dreaming_fixture_approve",
            "--decision",
            "approve",
            "--reason-summary",
            "The proposal is a bounded follow-up worth normal delivery review.",
            "--todo-text",
            "[P1] Implement the approved dreaming follow-up as normal delivery.",
            "--no-global-sync",
            registry_path=registry_path,
            runtime=runtime,
        )
        assert approve["ok"] is True, approve
        assert approve["appended"] is True, approve
        assert approve["classification"] == "dreaming_proposal_approved", approve
        assert approve["dreaming_decision"]["promoted_to_delivery"] is True, approve
        assert approve["dreaming_decision"]["delivery_spend_allowed"] is False, approve
        assert approve["dreaming_decision"]["quota_spent"] is False, approve
        todo_id = approve["dreaming_decision"]["promoted_todo_id"]
        assert todo_id and todo_id.startswith("todo_"), approve
        assert approve["dreaming_decision"]["created_todo_id"] == todo_id, approve
        assert approve["dreaming_decision"]["todo_added"] is True, approve
        state_after_approve = state_path.read_text(encoding="utf-8")
        assert "[P1] Implement the approved dreaming follow-up" in state_after_approve
        assert "dreaming_fixture_approve" in state_after_approve
        assert approve["side_effects"]["active_state_mutated"] is True, approve
        assert approve["side_effects"]["runtime_history_appended"] is True, approve
        assert approve["side_effects"]["quota_spent"] is False, approve
        assert_no_quota_spend(runtime)
        approved = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID)
        assert approved['source_authority'] == provider + '_v0'
        assert approved['decision_read_from_provider'] is True and approved['legacy_fallback_used'] is False
        assert len(approved['todos']) == len(original['todos']) + 1
        promoted = next(row for row in approved['todos'] if row['todo_id'] == todo_id)
        assert promoted['text'] == '[P1] Implement the approved dreaming follow-up as normal delivery.'
        assert promoted['status'] == 'open' and promoted['task_class'] == 'advancement_task'
        original_ids = {row['todo_id'] for row in original['todos']}
        assert [row for row in approved['todos'] if row['todo_id'] in original_ids] == original['todos']

        before_defer_state = state_path.read_text(encoding="utf-8")
        before_defer_index = index_path.read_text(encoding="utf-8")
        defer = run_cli(
            "dreaming",
            "decide",
            "--goal-id",
            GOAL_ID,
            "--proposal-id",
            "dreaming_fixture_defer",
            "--decision",
            "defer",
            "--reason-summary",
            "The proposal should wait for a stronger delivery boundary.",
            "--no-global-sync",
            registry_path=registry_path,
            runtime=runtime,
        )
        assert defer["ok"] is True, defer
        assert defer["classification"] == "dreaming_proposal_deferred", defer
        assert defer["dreaming_decision"]["promoted_to_delivery"] is False, defer
        assert defer["dreaming_decision"]["promoted_todo_id"] is None, defer
        assert defer["dreaming_decision"]["created_todo_id"] is None, defer
        assert defer["dreaming_decision"]["todo_added"] is False, defer
        assert defer["side_effects"]["active_state_mutated"] is False, defer
        assert defer["side_effects"]["runtime_history_appended"] is True, defer
        assert defer["side_effects"]["quota_spent"] is False, defer
        assert state_path.read_text(encoding="utf-8") == before_defer_state
        assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID) == approved
        assert index_path.read_text(encoding="utf-8") != before_defer_index
        assert_no_quota_spend(runtime)

        before_reject_state = state_path.read_text(encoding="utf-8")
        before_reject_index = index_path.read_text(encoding="utf-8")
        reject = run_cli(
            "dreaming",
            "decide",
            "--goal-id",
            GOAL_ID,
            "--proposal-id",
            "dreaming_fixture_reject",
            "--decision",
            "reject",
            "--reason-summary",
            "The proposal is not useful for this goal boundary.",
            "--dry-run",
            "--no-global-sync",
            registry_path=registry_path,
            runtime=runtime,
        )
        assert reject["ok"] is True, reject
        assert reject["dry_run"] is True, reject
        assert reject["appended"] is False, reject
        assert reject["side_effects"]["active_state_mutated"] is False, reject
        assert reject["side_effects"]["runtime_history_appended"] is False, reject
        assert reject["side_effects"]["quota_spent"] is False, reject
        assert state_path.read_text(encoding="utf-8") == before_reject_state
        assert index_path.read_text(encoding="utf-8") == before_reject_index
        assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID) == approved


def main() -> int:
    for provider in ('file', 'sqlite'):
        assert_decisions(provider)

    print("dreaming-proposal-decision-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
