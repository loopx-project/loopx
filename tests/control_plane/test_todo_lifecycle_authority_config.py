from __future__ import annotations

import json
from pathlib import Path

import pytest

from canonical_authority_fixture import initialize_canonical_authority
from loopx.cli import main
from loopx.configure_goal import configure_goal
from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)
from loopx.todos import add_goal_todo


GOAL_ID = "todo-lifecycle-authority-config"
OWNER_AGENT = "codex-owner"
ORCHESTRATION_AGENT = "codex-orchestrator"


def _registry(tmp_path: Path) -> Path:
    (tmp_path / "ACTIVE_GOAL_STATE.md").write_text(
        "---\n"
        f"goal_id: {GOAL_ID}\n"
        "---\n\n"
        "## Agent Todo\n\n",
        encoding="utf-8",
    )
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "common_runtime_root": str(tmp_path / "runtime"),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "repo": str(tmp_path),
                        "state_file": "ACTIVE_GOAL_STATE.md",
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": [
                                OWNER_AGENT,
                                ORCHESTRATION_AGENT,
                            ],
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    initialize_canonical_authority(
        tmp_path / "runtime",
        GOAL_ID,
        build_todo_runtime_shadow_projection(
            goal_id=GOAL_ID,
            todos=[],
            handoff_mode="soft_claim",
        ),
        state_path=tmp_path / "ACTIVE_GOAL_STATE.md",
    )
    return registry


def test_configure_goal_writes_typed_todo_lifecycle_authority(
    tmp_path: Path,
) -> None:
    registry = _registry(tmp_path)
    grant = {
        "agent_id": ORCHESTRATION_AGENT,
        "actions": ["complete", "reassign", "supersede"],
        "requires_reason": True,
    }

    preview = configure_goal(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_lifecycle_authority=[grant],
        execute=False,
    )
    assert preview["changed_fields"] == ["todo_lifecycle_authority"]
    assert preview["after"]["todo_lifecycle_authority"] == [grant]

    applied = configure_goal(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_lifecycle_authority=[grant],
        execute=True,
    )
    assert applied["written"] is True
    stored = json.loads(registry.read_text(encoding="utf-8"))
    assert stored["goals"][0]["coordination"]["todo_lifecycle_authority"] == [
        grant
    ]


@pytest.mark.parametrize(
    ("grant", "message"),
    [
        (
            {
                "agent_id": "codex-unknown",
                "actions": ["complete"],
                "requires_reason": True,
            },
            "must already be registered",
        ),
        (
            {
                "agent_id": ORCHESTRATION_AGENT,
                "actions": ["approve_review"],
                "requires_reason": True,
            },
            "unsupported todo lifecycle authority action",
        ),
    ],
)
def test_configure_goal_rejects_invalid_authority_grants(
    tmp_path: Path,
    grant: dict,
    message: str,
) -> None:
    registry = _registry(tmp_path)

    with pytest.raises(ValueError, match=message):
        configure_goal(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_lifecycle_authority=[grant],
            execute=False,
        )


def test_configure_goal_can_clear_one_authority_grant(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    grant = {
        "agent_id": ORCHESTRATION_AGENT,
        "actions": ["complete"],
        "requires_reason": True,
    }
    configure_goal(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_lifecycle_authority=[grant],
        execute=True,
    )

    cleared = configure_goal(
        registry_path=registry,
        goal_id=GOAL_ID,
        clear_todo_lifecycle_authority=[ORCHESTRATION_AGENT],
        execute=True,
    )

    assert cleared["after"]["todo_lifecycle_authority"] == []
    stored = json.loads(registry.read_text(encoding="utf-8"))
    assert "todo_lifecycle_authority" not in stored["goals"][0]["coordination"]


def test_configure_goal_cli_previews_authority_grant(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    registry = _registry(tmp_path)
    grant = {
        "agent_id": ORCHESTRATION_AGENT,
        "actions": ["complete", "reassign"],
        "requires_reason": True,
    }

    exit_code = main(
        [
            "--registry",
            str(registry),
            "--runtime-root",
            str(tmp_path / "runtime"),
            "--format",
            "json",
            "configure-goal",
            "--goal-id",
            GOAL_ID,
            "--todo-lifecycle-authority-json",
            json.dumps(grant),
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["after"]["todo_lifecycle_authority"] == [grant]


def test_todo_cli_emits_delegated_override_receipt(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    registry = _registry(tmp_path)
    configure_goal(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_lifecycle_authority=[
            {
                "agent_id": ORCHESTRATION_AGENT,
                "actions": ["complete"],
                "requires_reason": True,
            }
        ],
        execute=True,
    )
    todo = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="Complete one worker-owned task.",
        task_class="advancement_task",
        claimed_by=OWNER_AGENT,
    )

    exit_code = main(
        [
            "--registry",
            str(registry),
            "--format",
            "json",
            "todo",
            "complete",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            todo["todo_id"],
            "--agent-id",
            ORCHESTRATION_AGENT,
            "--authority-reason",
            "Verified the result before orchestration closeout.",
            "--evidence",
            "validation://cli-override",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mutation_authority"]["authority_mode"] == (
        "delegated_orchestration_override"
    )
    assert payload["mutation_authority"]["code"] == "terminal_transition"
    assert payload["source_authority"] == "file_v0"
