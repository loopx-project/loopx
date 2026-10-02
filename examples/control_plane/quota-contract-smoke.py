#!/usr/bin/env python3
"""Smoke-test the public quota allocation contract wording."""

from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
README = REPO_ROOT / "README.md"
QUOTA_DOC = REPO_ROOT / "docs" / "quota-allocation.md"
STATUS_CONTRACT = REPO_ROOT / "docs" / "status-data-contract.md"
SLOT_SPEND_FIXTURE = REPO_ROOT / "examples" / "quota-slot-spend-event.example.json"


def compact(text: str) -> str:
    return " ".join(text.split())


def assert_contains(text: str, needle: str, *, label: str) -> None:
    assert needle in text, f"{label} missing: {needle!r}"


def main() -> int:
    readme = compact(README.read_text(encoding="utf-8"))
    quota_doc = compact(QUOTA_DOC.read_text(encoding="utf-8"))
    status_contract = compact(STATUS_CONTRACT.read_text(encoding="utf-8"))

    assert_contains(quota_doc, "## Allocation Contract", label="quota doc")
    assert_contains(
        quota_doc,
        "`quota plan` reports an advisory next automatic turn",
        label="quota doc",
    )
    assert_contains(
        quota_doc,
        "It does not grant permission, clear an operator gate, record human reward",
        label="quota doc",
    )
    assert_contains(
        quota_doc,
        "keep `blocked_health`, `operator_gate`, `focus_wait`, `waiting`, `throttled`, and `paused` goals in their own lanes",
        label="quota doc",
    )
    assert_contains(
        quota_doc,
        "only goals with `state=eligible` enter the eligible lane",
        label="quota doc",
    )
    assert_contains(
        quota_doc,
        "sort eligible goals by effective `quota.compute`, highest first",
        label="quota doc",
    )
    assert_contains(
        quota_doc,
        "set `summary.next_automatic_turn` to the first eligible goal, or `none`",
        label="quota doc",
    )
    assert_contains(
        quota_doc,
        "skip the blocked delivery work and follow the reported health, operator",
        label="quota doc",
    )
    assert_contains(
        quota_doc,
        "`safe_bypass_allowed=true`, the target heartbeat may do one bounded read-only steering or analysis step",
        label="quota doc",
    )
    assert_contains(
        quota_doc,
        "`safe_bypass_kind=outcome_floor_recovery`",
        label="quota doc",
    )
    assert_contains(
        quota_doc,
        "produce the required ranker/cross-domain evidence artifact",
        label="quota doc",
    )
    assert_contains(
        quota_doc,
        "`gate_prompt`, `operator_question`, `next_handoff_condition`, `missing_gates`, `user_todo_summary`, or `agent_todo_summary`",
        label="quota doc",
    )
    assert_contains(
        quota_doc,
        "`todo_write_hint`",
        label="quota doc",
    )
    assert_contains(
        quota_doc,
        "Registry entries can carry per-goal `control_plane` policy alongside quota",
        label="quota doc",
    )
    assert_contains(
        quota_doc,
        "`control_plane.self_repair.enabled`: default `false`",
        label="quota doc",
    )
    assert_contains(
        quota_doc,
        "`decision=self_repair`, `self_repair_allowed=true`, `stall_self_repair`",
        label="quota doc",
    )
    assert_contains(
        quota_doc,
        "`control_plane_projection_repair`",
        label="quota doc",
    )
    assert_contains(
        quota_doc,
        "ordinary goals without this registry policy stay in their existing skip, waiting, or health-blocked lanes",
        label="quota doc",
    )
    assert_contains(
        quota_doc,
        "Connected delivery goals also include `goal_boundary`",
        label="quota doc",
    )
    assert_contains(
        quota_doc,
        "automation prompts should say to obey `goal_boundary` instead of repeating long protected file or action lists",
        label="quota doc",
    )
    assert_contains(
        quota_doc,
        "`loopx todo add --role user --task-class user_gate|user_action` instead of",
        label="quota doc",
    )
    assert_contains(
        quota_doc,
        "`automation_prompt_upgrade.required=true`",
        label="quota doc",
    )
    assert_contains(
        quota_doc,
        "does not flip `should_run`; it is a lightweight migration signal for stale installed automations",
        label="quota doc",
    )
    assert_contains(
        quota_doc,
        "ask the user or target controller the concrete gate question instead of silently skipping",
        label="quota doc",
    )
    assert_contains(
        quota_doc,
        "do not report \"no new user action\" while those todos remain open",
        label="quota doc",
    )
    assert_contains(
        quota_doc,
        "This also applies after a bounded safe-bypass step",
        label="quota doc",
    )
    assert_contains(
        quota_doc,
        "`notify_user_on_open_todo=true`",
        label="quota doc",
    )
    assert_contains(
        quota_doc,
        "`promotion_readiness_warning`",
        label="quota doc",
    )
    assert_contains(
        quota_doc,
        "report release readiness blockers from the shared runtime release ledger",
        label="quota doc",
    )
    assert_contains(
        quota_doc,
        "target heartbeat should return a compact `NOTIFY` listing at most three open user todos",
        label="quota doc",
    )
    assert_contains(
        quota_doc,
        "skipping delivery work and quota spend for that blocker-push turn",
        label="quota doc",
    )
    assert_contains(
        quota_doc,
        "## Slot Spend Event Contract",
        label="quota doc",
    )
    assert_contains(
        quota_doc,
        "`classification=quota_slot_spent` with a nested `quota_event` object",
        label="quota doc",
    )
    assert_contains(
        quota_doc,
        "write only after a fresh `quota should-run` returned `should_run=true`, or after it returned `safe_bypass_allowed=true`",
        label="quota doc",
    )
    assert_contains(
        quota_doc,
        "`after.spent_slots` must equal `before.spent_slots + slots`",
        label="quota doc",
    )
    assert_contains(
        quota_doc,
        "do not include human reward, operator-gate approval, write-control, private evidence",
        label="quota doc",
    )
    assert_contains(
        quota_doc,
        "Post-turn accounting protocol",
        label="quota doc",
    )
    assert_contains(
        quota_doc,
        "append exactly one `quota spend-slot --execute` event for that completed turn",
        label="quota doc",
    )
    assert_contains(
        quota_doc,
        "do not append spend for quiet `should_run=false` skips, preflight failures",
        label="quota doc",
    )
    assert_contains(
        quota_doc,
        "if `should_run=false` but `safe_bypass_allowed=true` and the agent actually completes bounded safe-bypass work",
        label="quota doc",
    )
    assert_contains(
        quota_doc,
        "spend only after validated ranker/cross-domain evidence or concrete blocker writeback",
        label="quota doc",
    )
    assert_contains(
        quota_doc,
        "if `should_run=true` with `effective_action=control_plane_health_repair` or `control_plane_projection_repair`",
        label="quota doc",
    )

    normalized_readme = " ".join(readme.split())
    assert_contains(readme, "loopx quota should-run", label="README")
    assert_contains(readme, "loopx quota spend-slot", label="README")
    assert_contains(
        normalized_readme,
        "Automatic turns must check quota first and append spend only after validated writeback.",
        label="README",
    )
    assert_contains(
        normalized_readme,
        "Quiet skips, preflight failures, and dry-run previews do not spend.",
        label="README",
    )
    assert_contains(
        readme,
        "Scheduler cadence follows `quota should-run.scheduler_hint`",
        label="README",
    )
    assert_contains(readme, "[Quota Allocation](docs/quota-allocation.md)", label="README")
    assert_contains(
        status_contract,
        "`loopx quota status` and `loopx quota plan` derive an agent-facing grouping from this same status payload",
        label="status contract",
    )
    assert_contains(
        status_contract,
        'loopx --registry "$HOME/.loopx/registry.global.json" quota should-run --goal-id <goal-id>',
        label="status contract",
    )
    assert_contains(
        status_contract,
        "These are read-only views, not a separate source of truth",
        label="status contract",
    )
    assert_contains(
        status_contract,
        "Registry entries may also declare compact `control_plane` settings",
        label="status contract",
    )
    assert_contains(
        status_contract,
        "By default `control_plane.self_repair.enabled=false`",
        label="status contract",
    )
    assert_contains(
        status_contract,
        "Scripts should treat `summary.next_automatic_turn` in the quota-plan JSON as advisory",
        label="status contract",
    )
    assert_contains(
        status_contract,
        "still respect the displayed health, operator, and evidence gates",
        label="status contract",
    )
    assert_contains(
        status_contract,
        "In lane terms, `next_automatic_turn` may only name the first eligible goal",
        label="status contract",
    )
    assert_contains(
        status_contract,
        "operator-gated, focus-waiting, waiting, throttled, paused, and health-blocked goals must stay out of the eligible lane",
        label="status contract",
    )
    assert_contains(
        status_contract,
        "ask that concrete gate in the visible thread with `NOTIFY`",
        label="status contract",
    )
    assert_contains(
        status_contract,
        "`notify_user_on_open_todo=true`",
        label="status contract",
    )
    assert_contains(
        status_contract,
        "`open_todo_notify_reason`",
        label="status contract",
    )
    assert_contains(
        status_contract,
        "skip quota spend for that blocker-push turn",
        label="status contract",
    )
    assert_contains(
        status_contract,
        "`project_asset`: a compact control-plane projection",
        label="status contract",
    )
    assert_contains(
        status_contract,
        "`owner`, `gate`, `support_mode`, `next_action`, and",
        label="status contract",
    )
    assert_contains(
        status_contract,
        "avoid reconstructing owner, gate, support mode, next action, stop condition, todo counts, compute state, and",
        label="status contract",
    )
    assert_contains(
        status_contract,
        "`support_mode` is a compact product-mode label",
        label="status contract",
    )
    assert_contains(
        status_contract,
        "`control_plane`: optional compact registry policy for this goal",
        label="status contract",
    )
    assert_contains(
        status_contract,
        "`self_repair.allow_waiting_projection_repair`",
        label="status contract",
    )
    assert_contains(
        status_contract,
        "`operator_gate_resume_contract`",
        label="status contract",
    )
    assert_contains(
        status_contract,
        "version=operator_gate_resume_contract_v0",
        label="status contract",
    )
    assert_contains(
        status_contract,
        "re-read current registry, `ACTIVE_GOAL_STATE`, quota, repo dirty/ref snapshot, policy, and run status",
        label="status contract",
    )
    assert_contains(
        status_contract,
        "must not carry the whole repository state back to the old gate",
        label="status contract",
    )
    assert_contains(
        status_contract,
        "`decision=self_repair`, `self_repair_allowed=true`, `stall_self_repair`",
        label="status contract",
    )
    assert_contains(
        status_contract,
        "Goals without that registry policy must not get this lane by default",
        label="status contract",
    )

    fixture = json.loads(SLOT_SPEND_FIXTURE.read_text(encoding="utf-8"))
    quota_event = fixture["quota_event"]
    before = quota_event["before"]
    after = quota_event["after"]
    assert fixture["classification"] == "quota_slot_spent", fixture
    assert quota_event["event_type"] == "quota_slot_spent", fixture
    assert quota_event["source"] in {"heartbeat", "controller", "adapter"}, fixture
    assert quota_event["slots"] > 0, fixture
    assert before["should_run"] is True, fixture
    assert before["state"] == "eligible", fixture
    assert after["spent_slots"] == before["spent_slots"] + quota_event["slots"], fixture
    assert after["allowed_slots"] == before["allowed_slots"], fixture
    assert after["state"] == "throttled", fixture
    assert after["should_run"] is False, fixture
    forbidden = {"human_reward", "operator_gate", "write_control", "private_evidence", "agent_command"}
    assert forbidden.isdisjoint(fixture), fixture
    assert forbidden.isdisjoint(quota_event), fixture

    print("quota-contract-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
