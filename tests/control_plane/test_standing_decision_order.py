"""Decision chronology is not Markdown layout or canonical Todo-ID ordering."""

import hashlib
import json
from pathlib import Path

import pytest
from canonical_authority_fixture import initialize_canonical_authority

from loopx.control_plane.coordination.coordination_state_contract import (
    TODO_DOMAIN_ITEM_SCHEMA_VERSION,
    TODO_DOMAIN_READ_RECORD_SCHEMA_VERSION,
    TODO_DOMAIN_RECORD_FIELDS,
)
from loopx.control_plane.coordination.local_authority_shadow_projection import (
    canonical_bytes,
)
from loopx.control_plane.coordination.runtime_shadow import (
    build_runtime_shadow_source_snapshot,
)
from loopx.control_plane.testing.canary_harness import (
    write_fixture_registry,
    run_json_cli_result,
)
from loopx.control_plane.todos.decision_scope import (
    standing_decision_authority_for_agent,
    build_required_decision_scope_consistency,
    build_required_decision_scope_repair_hint,
)
from loopx.control_plane.todos.standing_decision import (
    build_standing_decision_authority,
)


def decision(todo_id: str, outcome: str, completed_at: str) -> dict:
    return {
        "todo_id": todo_id,
        "role": "user",
        "task_class": "user_gate",
        "status": "done",
        "done": True,
        "global_gate": True,
        "decision_scope": "write_scope:goal:release",
        "decision_outcome": outcome,
        "completed_at": completed_at,
    }


def test_later_rejection_wins_even_when_older_approval_sorts_last() -> None:
    items = [
        decision("todo_aaa_reject", "reject", "2026-09-10T02:00:00Z"),
        decision("todo_zzz_approve", "approve", "2026-09-10T01:00:00Z"),
    ]
    for ordered in [items, list(reversed(items))]:
        result = build_standing_decision_authority(ordered)
        assert result["active_count"] == 0
        assert result["entries"][0]["source_todo_id"] == "todo_aaa_reject"


def test_conflicts_survive_lane_filtering_without_granting_cross_agent_authority() -> (
    None
):
    items = [
        decision("todo_first", "approve", ""),
        decision("todo_second", "reject", ""),
    ]
    for item in items:
        item.update(global_gate=False, blocks_agent="agent-a")
    result = build_standing_decision_authority(items, legacy_source_order=False)
    scoped = standing_decision_authority_for_agent(result, agent_id="agent-a")
    assert scoped["active_count"] == 0
    assert scoped["entries"] == []
    assert scoped["conflict_count"] == 1
    assert standing_decision_authority_for_agent(result, agent_id="agent-b") is None
    consistency = build_required_decision_scope_consistency(
        {
            "items": [
                {
                    "todo_id": "todo_delivery",
                    "status": "open",
                    "claimed_by": "agent-a",
                    "required_decision_scopes": ["write_scope:goal:release"],
                }
            ]
        },
        None,
        agent_id="agent-a",
        standing_decision_authority=result,
    )
    assert (
        consistency["errors"][0]["reason_code"] == "standing_decision_order_unresolved"
    )
    assert consistency["errors"][0]["related_user_todo_ids"] == [
        "todo_first",
        "todo_second",
    ]
    hint = build_required_decision_scope_repair_hint(consistency)
    assert hint["trigger"] == "required_decision_scope_projection_drift"
    assert "do not remove required_decision_scopes" in hint["repair_focus"]


def test_canonical_identifiers_are_not_redecoded_as_markdown_tokens() -> None:
    items = [
        decision("receipt-approve", "approve", "2026-09-10T01:00:00Z"),
        decision("receipt-revoke", "reject", "2026-09-10T02:00:00Z"),
    ]
    for item in items:
        item.update(
            schema_version=TODO_DOMAIN_ITEM_SCHEMA_VERSION,
            decision_scope={
                "kind": "write_scope",
                "granularity": "goal",
                "scope_key": "release",
            },
        )
    result = build_standing_decision_authority(items, canonical_records=True)
    assert result["active_count"] == 0
    assert result["entries"][0]["source_todo_id"] == "receipt-revoke"


@pytest.mark.parametrize("mode", ["markdown", "canonical_v0", "native"])
@pytest.mark.parametrize("archived", [False, True])
@pytest.mark.parametrize("ambiguous", [False, True])
def test_public_quota_reads_revocation_from_full_history_without_mutating_display(
    tmp_path: Path, mode: str, archived: bool, ambiguous: bool
) -> None:
    state, runtime, registry = (
        tmp_path / "STATE.md",
        tmp_path / "runtime",
        tmp_path / "registry.json",
    )
    # Intentionally put the newer rejection before the older approval in both
    # source and ID order. The displayed done list is not the authority source.
    scope = "write_scope:goal:release"
    rejection_time = "2026-09-10T01:00:00Z" if ambiguous else "2026-09-10T02:00:00Z"
    source = "# Goal\n\n## Agent Todo\n\n- [ ] Deliver after authorization\n" + (
        f"  <!-- loopx:todo todo_id=todo_delivery role=agent status=open task_class=advancement_task claimed_by=agent-a required_decision_scopes={scope} -->\n\n"
        "## User Todo / Owner Review Reading Queue\n\n"
    )
    rejection = (
        "- [x] Revoke standing approval\n"
        f"  <!-- loopx:todo todo_id=todo_aaa_reject role=user status=done task_class=user_gate global_gate=true decision_scope={scope} decision_outcome=reject completed_at={rejection_time} -->\n"
    )
    approval = (
        "- [x] Earlier approval\n"
        f"  <!-- loopx:todo todo_id=todo_zzz_approve role=user status=done task_class=user_gate global_gate=true decision_scope={scope} decision_outcome=approve completed_at=2026-09-10T01:00:00Z -->\n"
    )
    # More done items than the ordinary hot-path display budget; authority
    # must not be derived from the few retained display rows.
    source += "".join(
        f"- [x] Ordinary completed reading {i}\n  <!-- loopx:todo todo_id=todo_read_{i:03d} role=user status=done task_class=user_action -->\n"
        for i in range(12)
    )
    source += (
        approval + "\n## Todo Archive\n\n" + rejection
        if archived
        else rejection + approval
    )
    state.write_text(source, encoding="utf-8")
    write_fixture_registry(
        project=tmp_path,
        runtime_root=runtime,
        registry_path=registry,
        goal_id="goal-a",
        domain="decision-read",
        adapter_kind="generic_project_goal_v0",
        state_file=str(state),
        registered_agents=["agent-a"],
        quota_allowed_slots=None,
    )
    if mode != "markdown":
        goal = json.loads(registry.read_text(encoding="utf-8"))["goals"][0]
        projection, _ = build_runtime_shadow_source_snapshot(
            goal=goal,
            runtime_root=runtime,
            state_path=state,
            registry_path=registry,
        )
        if archived:
            captured = [
                item
                for item in projection["todos"]
                if item["todo_id"] == "todo_aaa_reject"
            ]
            assert len(captured) == 1
            assert captured[0]["archive_state"] == "archive"
        if mode == "native":
            for todo in projection["todos"]:
                todo.update(schema_version=TODO_DOMAIN_ITEM_SCHEMA_VERSION)
                todo.pop("index", None)
                todo.pop("source_section", None)
            projection["todo_read_model"] = {
                "schema_version": TODO_DOMAIN_READ_RECORD_SCHEMA_VERSION,
                "contract_fields": list(TODO_DOMAIN_RECORD_FIELDS),
                "todo_count": len(projection["todos"]),
                "records_sha256": hashlib.sha256(
                    canonical_bytes(projection["todos"])
                ).hexdigest(),
            }
        initialize_canonical_authority(runtime, "goal-a", projection, state_path=state)
        state.unlink()
    before = state.read_bytes() if state.exists() else None
    code, packet = run_json_cli_result(
        "quota",
        "should-run",
        "--goal-id",
        "goal-a",
        "--agent-id",
        "agent-a",
        registry_path=registry,
        runtime_root=runtime,
    )
    assert code == 0, packet
    authority = packet["standing_decision_authority"]
    assert authority["active_count"] == 0
    if ambiguous:
        assert (
            packet["stall_self_repair"]["consistency"]["errors"][0]["reason_code"]
            == "standing_decision_order_unresolved"
        )
        assert authority["entries"] == []
        assert authority["conflict_count"] == 1
        assert (
            authority["conflicts"][0]["reason_code"]
            == "standing_decision_order_unresolved"
        )
    else:
        assert authority["entries"][0]["source_todo_id"] == "todo_aaa_reject"
    assert packet["effective_action"] == "todo_decision_scope_projection_repair"
    assert (state.read_bytes() if state.exists() else None) == before
