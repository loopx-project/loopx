from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from canonical_authority_fixture import initialize_canonical_authority

from loopx.control_plane.coordination.local_authority import (
    read_canonical_todos_if_promoted,
)
from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)
from loopx.control_plane.todos import provider_terminal_lifecycle
from loopx.todos import complete_goal_todo


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize(
    "validation_digest",
    [None, "a" * 64],
    ids=["plain", "validated"],
)
def test_public_completion_repairs_imported_metadata_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    validation_digest: str | None,
) -> None:
    runtime_root = tmp_path / "runtime"
    project = tmp_path / "project"
    project.mkdir()
    state_file = project / "ACTIVE_GOAL_STATE.md"
    state_file.write_text(
        "# Goal\n\n## User Todo\n\n## Agent Todo\n\n## Completed Work Archive\n",
        encoding="utf-8",
    )
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(runtime_root),
                "goals": [
                    {
                        "id": "goal-a",
                        "status": "active",
                        "repo": str(project),
                        "state_file": state_file.name,
                        "coordination": {"registered_agents": ["agent-a"]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    todo = {
        "schema_version": "todo_item_v0",
        "todo_id": "todo-imported",
        "role": "agent",
        "source_section": "Agent Todo",
        "status": "done",
        "done": True,
        "archive_state": "active",
        "text": "Preserve the imported completion",
        "task_class": "advancement_task",
        "claimed_by": "agent-a",
        "note": "Original note",
        "evidence": "Original evidence",
        "reason": "Original reason",
        "completion_turn_key": "original-turn",
        "completion_receipt_id": "tcw_original",
        "completed_at": "2026-09-01T00:00:00Z",
        "updated_at": "2026-09-01T01:00:00Z",
        "last_actor_agent_id": "agent-before",
        **(
            {
                "completion_validation_required": True,
                "completion_validation_sha256": validation_digest,
            }
            if validation_digest is not None
            else {}
        ),
    }
    initialize_canonical_authority(
        runtime_root,
        "goal-a",
        build_todo_runtime_shadow_projection(
            goal_id="goal-a",
            todos=[todo],
            handoff_mode="soft_claim",
        ),
        state_path=state_file,
        provider=provider,
    )

    terminal_requests: list[dict[str, Any]] = []
    execute = provider_terminal_lifecycle.effect_runtime_result

    def capture_request(
        method: str,
        params: dict[str, Any],
        **kwargs: Any,
    ) -> Any:
        if method == "coordination.local_authority.todo_terminal":
            terminal_requests.append(dict(params))
        return execute(method, params, **kwargs)

    monkeypatch.setattr(
        provider_terminal_lifecycle,
        "effect_runtime_result",
        capture_request,
    )
    request = {
        "registry_path": registry,
        "goal_id": "goal-a",
        "todo_id": "todo-imported",
        "agent_id": "agent-a",
        "completion_turn_key": "original-turn",
        "completion_identity_source": "turn_settlement",
    }
    before = read_canonical_todos_if_promoted(
        runtime_root=runtime_root,
        goal_id="goal-a",
    )
    assert before is not None

    preview = complete_goal_todo(**request, dry_run=True)
    assert preview["provider_status"] == "planned"
    assert preview["changed"] is True
    assert read_canonical_todos_if_promoted(
        runtime_root=runtime_root,
        goal_id="goal-a",
    ) == before

    repaired = complete_goal_todo(**request)
    assert repaired["provider_status"] == "applied"
    assert repaired["completion_continuation"] == "active_goal"
    after = read_canonical_todos_if_promoted(
        runtime_root=runtime_root,
        goal_id="goal-a",
    )
    assert after is not None
    repaired_todo = after["todos"][0]
    original_todo = before["todos"][0]
    assert repaired_todo == {
        **original_todo,
        "completion_continuation": "active_goal",
    }

    producer_request = terminal_requests[0]
    assert producer_request["allow_user_gate_auto_acquire"] is True
    assert producer_request["completion_policy_request"] is not None
    assert producer_request["validation_declaration_sha256"] == validation_digest

    replay = complete_goal_todo(**request)
    assert replay["provider_status"] == "replayed"
    assert read_canonical_todos_if_promoted(
        runtime_root=runtime_root,
        goal_id="goal-a",
    ) == after

    if validation_digest is not None:
        return

    closed = complete_goal_todo(**request, no_followup=True)
    assert closed["provider_status"] == "applied"
    assert closed["completion_continuation"] == "no_followup"
    assert closed["completion_recovery"] == "same_turn_terminal_closeout"
