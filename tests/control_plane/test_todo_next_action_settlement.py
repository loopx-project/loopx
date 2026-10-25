from __future__ import annotations

import json
from pathlib import Path

from canonical_authority_fixture import initialize_canonical_authority
from loopx.bootstrap import render_state_markdown
from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)
from loopx.state_projection import active_state_next_action_entries
from loopx.state_refresh import build_state_refresh_record
from loopx.todos import add_goal_todo, complete_goal_todo

GOAL_ID = "todo-next-action-settlement"
AGENT_ID = "codex-next-action"


def _initialize_canonical(tmp_path: Path, state: Path) -> None:
    initialize_canonical_authority(
        tmp_path / "runtime",
        GOAL_ID,
        build_todo_runtime_shadow_projection(
            goal_id=GOAL_ID,
            todos=[],
            handoff_mode="soft_claim",
        ),
        state_path=state,
    )


def _write_fixture(tmp_path: Path, *, next_action: str) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    state = repo / "ACTIVE_GOAL_STATE.md"
    state.write_text(
        "\n".join(
            [
                "---",
                f"goal_id: {GOAL_ID}",
                "updated_at: 2026-08-21T00:00:00+08:00",
                "---",
                "",
                "## Agent Todo",
                "",
                "## Next Action",
                "",
                f"- {next_action}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    registry = tmp_path / "registry.global.json"
    registry.write_text(
        json.dumps(
            {
                "common_runtime_root": str(tmp_path / "runtime"),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "harness_self_improvement",
                        "status": "active",
                        "repo": str(repo),
                        "state_file": state.name,
                        "adapter": {"kind": "harness_self_improvement"},
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": [AGENT_ID],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    _initialize_canonical(tmp_path, state)
    return registry, state


def test_bootstrap_renders_no_first_connect_todo_or_next_action_binding(
    tmp_path: Path,
) -> None:
    state_text = render_state_markdown(
        project=tmp_path,
        goal_id=GOAL_ID,
        adapter_kind="read_only_project_map_v0",
        objective="Implement and validate the task.",
        updated_at="2026-08-21T00:00:00+08:00",
        goal_doc=None,
        execution_profile=None,
    )

    assert "loopx:next-action" not in state_text
    assert "loopx:todo todo_id=" not in state_text
    assert active_state_next_action_entries(state_text) == [
        "Initial routing is owned by the connected domain adapter."
    ]
    record = build_state_refresh_record(
        goal_id=GOAL_ID,
        state_file=tmp_path / "ACTIVE_GOAL_STATE.md",
        state_text=state_text,
        classification="state_refreshed",
        recommended_action="Continue the selected task.",
        recommended_action_source="test",
        generated_at="2026-08-21T00:01:00+08:00",
        registry_goal=None,
    )
    assert record["state"]["next_action"] == active_state_next_action_entries(
        state_text
    )
    assert "loopx:next-action" not in json.dumps(record)


def test_agent_todo_add_after_bootstrap_keeps_default_next_action(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    state = repo / "ACTIVE_GOAL_STATE.md"
    state.write_text(
        render_state_markdown(
            project=repo,
            goal_id=GOAL_ID,
            adapter_kind="read_only_project_map_v0",
            objective="Implement and validate the task.",
            updated_at="2026-08-21T00:00:00+08:00",
            goal_doc=None,
            execution_profile=None,
        ),
        encoding="utf-8",
    )
    registry = tmp_path / "registry.global.json"
    registry.write_text(
        json.dumps(
            {
                "common_runtime_root": str(tmp_path / "runtime"),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "harness_self_improvement",
                        "status": "active",
                        "repo": str(repo),
                        "state_file": state.name,
                        "adapter": {"kind": "read_only_project_map_v0"},
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": [AGENT_ID],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    _initialize_canonical(tmp_path, state)

    added = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="[P0] Implement and validate the requested behavior.",
        task_class="advancement_task",
        action_kind="implementation",
        claimed_by=AGENT_ID,
    )

    state_text = state.read_text(encoding="utf-8")
    assert added["todo_id"]
    assert active_state_next_action_entries(state_text) == [
        "Initial routing is owned by the connected domain adapter."
    ]
    assert "loopx:next-action" not in state_text
    assert f"todo_id={added['todo_id']}" in state_text


def test_agent_todo_add_preserves_same_priority_and_manual_next_actions(
    tmp_path: Path,
) -> None:
    registry, state = _write_fixture(
        tmp_path,
        next_action="Keep the owner-approved route unchanged.",
    )
    manual = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="[P0] Implement the requested behavior.",
        task_class="advancement_task",
        action_kind="implementation",
        claimed_by=AGENT_ID,
    )
    assert manual["todo_id"]
    assert active_state_next_action_entries(state.read_text(encoding="utf-8")) == [
        "Keep the owner-approved route unchanged."
    ]

    state.write_text(
        state.read_text(encoding="utf-8").replace(
            "- Keep the owner-approved route unchanged.\n",
            "- [P0] Implement the requested behavior.\n"
            "<!-- loopx:next-action schema=loopx_next_action_binding_v0 "
            f"todo_id={manual['todo_id']} -->\n",
            1,
        ),
        encoding="utf-8",
    )
    same_priority = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="[P0] Add focused validation.",
        task_class="advancement_task",
        action_kind="validation_review",
        claimed_by=AGENT_ID,
    )
    assert same_priority["todo_id"]
    assert active_state_next_action_entries(state.read_text(encoding="utf-8")) == [
        "[P0] Implement the requested behavior."
    ]


def test_higher_priority_todo_does_not_take_over_another_claimed_lane(
    tmp_path: Path,
) -> None:
    bound_text = "[P1] Continue the first peer's selected route."
    registry, state = _write_fixture(tmp_path, next_action=bound_text)
    bound = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text=bound_text,
        task_class="advancement_task",
        action_kind="implementation",
        claimed_by=AGENT_ID,
    )
    state.write_text(
        state.read_text(encoding="utf-8").replace(
            f"- {bound_text}\n",
            f"- {bound_text}\n"
            "<!-- loopx:next-action schema=loopx_next_action_binding_v0 "
            f"todo_id={bound['todo_id']} -->\n",
            1,
        ),
        encoding="utf-8",
    )
    registry_payload = json.loads(registry.read_text(encoding="utf-8"))
    registry_payload["goals"][0]["coordination"]["registered_agents"].append(
        "codex-other"
    )
    registry.write_text(json.dumps(registry_payload), encoding="utf-8")

    added = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="[P0] Execute the other peer's independent route.",
        task_class="advancement_task",
        action_kind="implementation",
        claimed_by="codex-other",
    )

    assert added["todo_id"]
    assert active_state_next_action_entries(state.read_text(encoding="utf-8")) == [
        bound_text
    ]


def test_refresh_record_preserves_a_long_wrapped_next_action_losslessly(
    tmp_path: Path,
) -> None:
    prefix = "Run one bounded settlement check and preserve its durable context"
    tail = "through the final public-safe boundary sentence without truncation."
    state_text = "\n".join(
        [
            "---",
            f"goal_id: {GOAL_ID}",
            "---",
            "",
            "## Next Action",
            "",
            f"- {prefix} " + ("across related control-plane state " * 8),
            f"  {tail}",
            "",
        ]
    )

    assert active_state_next_action_entries(state_text)[0].endswith("…")
    record = build_state_refresh_record(
        goal_id=GOAL_ID,
        state_file=tmp_path / "ACTIVE_GOAL_STATE.md",
        state_text=state_text,
        classification="state_refreshed",
        recommended_action=prefix,
        recommended_action_source="test",
        generated_at="2026-08-21T00:01:00+08:00",
        registry_goal=None,
    )

    assert record["state"]["next_action"][0].endswith(tail)
    assert "…" not in record["state"]["next_action"][0]


def test_complete_preserves_unrelated_owner_next_action(tmp_path: Path) -> None:
    registry, state = _write_fixture(
        tmp_path,
        next_action="Keep the owner-approved release route unchanged.",
    )
    todo = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="[P1] Validate the project connection.",
        task_class="advancement_task",
        action_kind="project_connection_review",
        claimed_by=AGENT_ID,
    )

    result = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=str(todo["todo_id"]),
        no_followup=True,
        agent_id=AGENT_ID,
        evidence="connection preflight passed",
    )

    assert result["changed"] is True
    assert active_state_next_action_entries(state.read_text(encoding="utf-8")) == [
        "Keep the owner-approved release route unchanged."
    ]
