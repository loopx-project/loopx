from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx.capabilities.issue_fix.pr_gate_reconcile import (
    reconcile_issue_fix_pr_gate,
)
from loopx.capabilities.issue_fix.pr_lifecycle import (
    build_issue_fix_pr_lifecycle_monitor_packet,
)
from loopx.capabilities.issue_fix.pr_lifecycle_rollout import (
    append_pr_merge_rollout_event,
)
from loopx.control_plane.todos.resume_condition import (
    evaluate_todo_resume_conditions,
)
from loopx.rollout_event_log import load_rollout_events, rollout_event_log_path


GOAL_ID = "multi-agent-pr-gate-reconcile"
ACTOR_AGENT = "codex-author"


def _write_fixture(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "repo"
    state = project / "ACTIVE_GOAL_STATE.md"
    state.parent.mkdir(parents=True)
    state.write_text(
        "## User Todo / Owner Review Reading Queue\n\n"
        "- [ ] Approve public PR merge.\n"
        "  <!-- loopx:todo todo_id=todo_merge_gate_2298 status=open "
        "task_class=user_gate decision_scope=direction:action:merge_pr_2298 "
        "blocks_agent=codex-author -->\n\n"
        "## Agent Todo\n\n",
        encoding="utf-8",
    )
    registry = tmp_path / "registry.global.json"
    registry.write_text(
        json.dumps(
            {
                "goals": [
                    {
                        "id": GOAL_ID,
                        "status": "active",
                        "repo": str(project),
                        "state_file": state.name,
                        "adapter": {"kind": "harness_self_improvement"},
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": [ACTOR_AGENT, "codex-review"],
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return registry, state


def _reconcile(registry: Path, state: Path, *, agent_id: str | None) -> dict:
    return reconcile_issue_fix_pr_gate(
        registry_path=registry,
        runtime_root_arg=str(registry.parent / "runtime"),
        goal_id=GOAL_ID,
        todo_id="todo_merge_gate_2298",
        agent_id=agent_id,
        project=state.parent,
        url="https://github.com/huangruiteng/loopx/pull/2298",
        provider_payload={
            "state": "MERGED",
            "mergedAt": "2026-07-18T00:00:00Z",
        },
        execute=True,
        generated_at="2026-07-18T00:01:00Z",
    )


def test_multi_agent_legacy_merge_gate_uses_attributed_actor(tmp_path: Path) -> None:
    registry, state = _write_fixture(tmp_path)

    result = _reconcile(registry, state, agent_id=ACTOR_AGENT)

    assert result["write_performed"] is True
    assert result["todo_completion"]["status"] == "done"
    assert result["todo_completion"]["mutation_authority"] == {
        "schema_version": "todo_mutation_authority_v0",
        "command": "complete",
        "mode": "registered_peer_actor",
        "actor_agent_id": ACTOR_AGENT,
        "todo_id": "todo_merge_gate_2298",
        "claim_owner": None,
        "registered_agent_count": 2,
    }
    assert "status=done" in state.read_text(encoding="utf-8")


def test_multi_agent_legacy_merge_gate_without_actor_is_atomic(tmp_path: Path) -> None:
    registry, state = _write_fixture(tmp_path)
    before = state.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="requires --agent-id"):
        _reconcile(registry, state, agent_id=None)

    assert state.read_text(encoding="utf-8") == before


def test_repository_redirect_emits_canonical_merge_with_exact_alias_resume(
    tmp_path: Path,
) -> None:
    registry, _state = _write_fixture(tmp_path)
    runtime_root = tmp_path / "runtime"
    packet = build_issue_fix_pr_lifecycle_monitor_packet(
        url="https://github.com/huangruiteng/loopx/pull/4344",
        provider_payload={
            "state": "MERGED",
            "mergedAt": "2026-09-13T13:45:34Z",
            "url": "https://github.com/loopx-project/loopx/pull/4344",
        },
        generated_at="2026-09-20T10:30:00Z",
    )

    assert packet["observation"]["repo"] == "loopx-project/loopx"
    assert packet["observation"]["requested_repo"] == "huangruiteng/loopx"
    assert packet["observation"]["repository_aliases"] == [
        "huangruiteng/loopx"
    ]

    receipt = append_pr_merge_rollout_event(
        payload=packet,
        goal_id=GOAL_ID,
        registry_path=registry,
        runtime_root_arg=str(runtime_root),
    )
    assert receipt["pr_ref"] == "loopx-project/loopx#4344"
    assert receipt["repository_aliases"] == ["huangruiteng/loopx"]
    events = load_rollout_events(rollout_event_log_path(runtime_root, GOAL_ID))
    assert events[0]["code_refs"]["pr_ref"] == "loopx-project/loopx#4344"
    assert events[0]["source_refs"] == [
        {"kind": "pull_request", "ref": "huangruiteng/loopx#4344"}
    ]

    conditions = evaluate_todo_resume_conditions(
        [
            {
                "todo_id": "todo_legacy_repository_wait",
                "status": "deferred",
                "task_repository": "git:github.com/huangruiteng/loopx",
                "resume_when": "pr_merged:#4344",
            }
        ],
        source_items=[],
        rollout_events=events,
        evaluated_at="2026-09-20T10:31:00Z",
    )
    assert conditions["todo_legacy_repository_wait"]["satisfied"] is True
    assert conditions["todo_legacy_repository_wait"]["matched_pr_ref"] == (
        "huangruiteng/loopx#4344"
    )


def test_repository_redirect_alias_does_not_match_another_pr_number() -> None:
    packet = build_issue_fix_pr_lifecycle_monitor_packet(
        url="https://github.com/huangruiteng/loopx/pull/4344",
        provider_payload={
            "state": "MERGED",
            "mergedAt": "2026-09-13T13:45:34Z",
            "url": "https://github.com/loopx-project/loopx/pull/4345",
        },
    )

    assert packet["observation"]["repo"] == "huangruiteng/loopx"
    assert "repository_aliases" not in packet["observation"]
