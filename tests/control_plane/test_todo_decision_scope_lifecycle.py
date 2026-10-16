from __future__ import annotations

import json
from pathlib import Path

import pytest

from canonical_authority_fixture import initialize_canonical_authority
from loopx.control_plane.coordination.coordination_state_contract import (
    TODO_DOMAIN_READ_RECORD_SCHEMA_VERSION,
    TODO_DOMAIN_RECORD_FIELDS,
)
from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)
from loopx.quota import build_quota_should_run
from loopx.status import collect_status, parse_active_state_todos
from loopx.todos import add_goal_todo, complete_goal_todo, supersede_goal_todo

GOAL_ID = "decision-scope-lifecycle"
AGENT_ID = "codex-delivery"
OTHER_AGENT_ID = "codex-review"
PUBLISH_SCOPE = "direction:action:publish_release"
ANNOUNCE_SCOPE = "direction:action:announce_release"
PRIVATE_READ_SCOPE = "private_read:project:restricted_material"
BENCHMARK_MERGE_SCOPE = "write_scope:goal:benchmark_pr_self_merge"


def _write_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    state = repo / "ACTIVE_GOAL_STATE.md"
    state.write_text(
        "\n".join(
            [
                "---",
                f"goal_id: {GOAL_ID}",
                "updated_at: 2026-07-16T00:00:00+00:00",
                "---",
                "",
                "## Agent Todo",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    registry = tmp_path / "registry.global.json"
    registry.write_text(
        json.dumps(
            {
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "harness_self_improvement",
                        "status": "active",
                        "repo": str(repo),
                        "state_file": "ACTIVE_GOAL_STATE.md",
                        "adapter": {"kind": "harness_self_improvement"},
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": [AGENT_ID, OTHER_AGENT_ID],
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return repo, state, registry


def _canonical_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo, state, registry = _write_fixture(tmp_path)
    config = json.loads(registry.read_text(encoding="utf-8"))
    config["common_runtime_root"] = str(tmp_path / "runtime")
    registry.write_text(json.dumps(config), encoding="utf-8")
    projection = build_todo_runtime_shadow_projection(
        goal_id=GOAL_ID, handoff_mode="soft_claim", todos=[],
    )
    projection["todo_read_model"] = {
        **projection["todo_read_model"],
        "schema_version": TODO_DOMAIN_READ_RECORD_SCHEMA_VERSION,
        "contract_fields": list(TODO_DOMAIN_RECORD_FIELDS),
    }
    initialize_canonical_authority(
        tmp_path / "runtime", GOAL_ID, projection, state_path=state,
    )
    return repo, state, registry


def _todo(state: Path, todo_id: str) -> dict:
    todos = parse_active_state_todos(state.read_text(encoding="utf-8"))
    return next(
        item
        for role in ("agent_todos", "user_todos")
        for item in todos[role]["items"]
        if item["todo_id"] == todo_id
    )


def _add_target_and_gate(
    registry: Path,
    *,
    required_scopes: list[str],
    target_status: str = "open",
    decision_scope: str = PUBLISH_SCOPE,
) -> tuple[dict, dict]:
    target = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="Publish the approved release artifact.",
        status=target_status,
        task_class="advancement_task",
        claimed_by=AGENT_ID,
        required_decision_scopes=required_scopes,
    )
    gate = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="user",
        text="Approve publishing the release artifact.",
        task_class="user_gate",
        blocks_agent=AGENT_ID,
        decision_scope=decision_scope,
        unblocks_todo_id=target["todo_id"],
    )
    return target, gate


def test_completed_gate_consumes_scope_for_already_open_publication(
    tmp_path: Path,
) -> None:
    repo, state, registry = _canonical_fixture(tmp_path)
    target, gate = _add_target_and_gate(
        registry,
        required_scopes=[PUBLISH_SCOPE],
    )

    result = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=gate["todo_id"],
        role="user",
        agent_id=AGENT_ID,
        decision_outcome="approve",
        evidence="owner approved the exact release publication",
    )

    assert result["decision_scope_resolution"] == {
        "schema_version": "todo_decision_scope_resolution_v0",
        "state": "resolved",
        "source_todo_id": gate["todo_id"],
        "target_todo_id": target["todo_id"],
        "decision_scope": {
            "schema_version": "decision_scope_v0",
            "kind": "direction",
            "granularity": "action",
            "scope_key": "publish_release",
        },
        "resolved_required_decision_scopes": [
            {
                "schema_version": "decision_scope_v0",
                "kind": "direction",
                "granularity": "action",
                "scope_key": "publish_release",
            }
        ],
        "remaining_required_decision_scopes": [],
        "changed": True,
        "target_status": "open",
    }
    assert result["unblock_resume"]["state"] == "target_not_blocked"
    updated_target = _todo(state, target["todo_id"])
    assert updated_target["status"] == "open"
    assert updated_target.get("required_decision_scopes", []) == []

    status = collect_status(
        registry_path=registry,
        runtime_root_override=str(tmp_path / "runtime"),
        scan_roots=[repo],
        limit=1,
        goal_id=GOAL_ID,
    )
    quota = build_quota_should_run(status, goal_id=GOAL_ID, agent_id=AGENT_ID)
    assert quota["effective_action"] != "todo_decision_scope_projection_repair"
    consistency = quota.get("todo_decision_scope_consistency")
    assert consistency is None or consistency["ok"] is True


def test_completed_broad_gate_projects_standing_authority_into_quota(
    tmp_path: Path,
) -> None:
    repo, state, registry = _canonical_fixture(tmp_path)
    target = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="Validate and self-merge a benchmark PR.",
        task_class="advancement_task",
        claimed_by=AGENT_ID,
        required_decision_scopes=[BENCHMARK_MERGE_SCOPE],
    )
    gate = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="user",
        text="Approve validated benchmark PR self-merge for this goal.",
        task_class="user_gate",
        blocks_agent=AGENT_ID,
        decision_scope=BENCHMARK_MERGE_SCOPE,
    )

    complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=gate["todo_id"],
        role="user",
        agent_id=AGENT_ID,
        decision_outcome="approve",
        evidence="owner approved the standing benchmark PR merge policy",
    )

    parsed = parse_active_state_todos(state.read_text(encoding="utf-8"))
    authority = parsed["standing_decision_authority"]
    assert authority["active_count"] == 1
    assert authority["entries"][0]["source_todo_id"] == gate["todo_id"]

    status = collect_status(
        registry_path=registry,
        runtime_root_override=str(tmp_path / "runtime"),
        scan_roots=[repo],
        limit=1,
        goal_id=GOAL_ID,
    )
    quota = build_quota_should_run(status, goal_id=GOAL_ID, agent_id=AGENT_ID)

    assert quota["standing_decision_authority"]["active_count"] == 1
    assert quota["standing_decision_authority"]["entries"][0]["source_todo_id"] == (
        gate["todo_id"]
    )
    assert quota["selected_todo"]["todo_id"] == target["todo_id"]
    assert quota["effective_action"] != "todo_decision_scope_projection_repair"
    consistency = quota.get("todo_decision_scope_consistency")
    assert consistency is None or consistency["ok"] is True


def test_completed_gate_consumes_only_covered_scope(tmp_path: Path) -> None:
    _repo, state, registry = _canonical_fixture(tmp_path)
    target, gate = _add_target_and_gate(
        registry,
        required_scopes=[PUBLISH_SCOPE, ANNOUNCE_SCOPE],
    )

    result = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=gate["todo_id"],
        role="user",
        agent_id=AGENT_ID,
        decision_outcome="approve",
        evidence="owner approved publication only",
    )

    resolution = result["decision_scope_resolution"]
    assert [
        item["scope_key"]
        for item in resolution["resolved_required_decision_scopes"]
    ] == ["publish_release"]
    assert [
        item["scope_key"]
        for item in resolution["remaining_required_decision_scopes"]
    ] == ["announce_release"]
    updated_target = _todo(state, target["todo_id"])
    assert [
        item["scope_key"] for item in updated_target["required_decision_scopes"]
    ] == ["announce_release"]


def test_superseded_gate_does_not_consume_required_scope(tmp_path: Path) -> None:
    _repo, state, registry = _canonical_fixture(tmp_path)
    target, gate = _add_target_and_gate(
        registry,
        required_scopes=[PUBLISH_SCOPE],
        target_status="blocked",
    )

    result = supersede_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=gate["todo_id"],
        role="user",
        agent_id=AGENT_ID,
        reason="approval request replaced without granting authority",
    )

    assert "decision_scope_resolution" not in result
    updated_target = _todo(state, target["todo_id"])
    assert updated_target["status"] == "blocked"
    assert [
        item["scope_key"] for item in updated_target["required_decision_scopes"]
    ] == ["publish_release"]


@pytest.mark.parametrize(
    ("decision_outcome", "unblock_state"),
    [
        ("reject", "decision_rejected"),
        ("cancel", "decision_cancelled"),
    ],
)
def test_non_approval_gate_outcome_remains_a_durable_block(
    tmp_path: Path,
    decision_outcome: str,
    unblock_state: str,
) -> None:
    repo, state, registry = _canonical_fixture(tmp_path)
    target, gate = _add_target_and_gate(
        registry,
        required_scopes=[PRIVATE_READ_SCOPE],
        target_status="blocked",
        decision_scope=PRIVATE_READ_SCOPE,
    )

    result = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=gate["todo_id"],
        role="user",
        agent_id=AGENT_ID,
        decision_outcome=decision_outcome,
        evidence=f"owner recorded {decision_outcome} for the exact private read",
    )

    assert result["decision_outcome"] == decision_outcome
    assert "decision_scope_resolution" not in result
    assert result["unblock_resume"]["state"] == unblock_state
    updated_target = _todo(state, target["todo_id"])
    assert updated_target["status"] == "blocked"
    assert updated_target["required_decision_scopes"][0]["scope_key"] == (
        "restricted_material"
    )
    assert updated_target["decision_scope_outcomes"] == [
        {
            "schema_version": "todo_decision_scope_outcome_v0",
            "outcome": decision_outcome,
            "decision_scope": {
                "schema_version": "decision_scope_v0",
                "kind": "private_read",
                "granularity": "project",
                "scope_key": "restricted_material",
            },
            "source_todo_id": gate["todo_id"],
        }
    ]
    assert "authorization satisfied" not in str(updated_target.get("reason") or "")

    status = collect_status(
        registry_path=registry,
        runtime_root_override=str(tmp_path / "runtime"),
        scan_roots=[repo],
        limit=1,
        goal_id=GOAL_ID,
    )
    quota = build_quota_should_run(status, goal_id=GOAL_ID, agent_id=AGENT_ID)
    assert quota["effective_action"] != "todo_decision_scope_projection_repair"
    consistency = quota.get("todo_decision_scope_consistency")
    assert consistency is None or consistency["ok"] is True


def test_user_gate_completion_without_decision_closes_only_the_source(
    tmp_path: Path,
) -> None:
    _repo, state, registry = _canonical_fixture(tmp_path)
    target, gate = _add_target_and_gate(
        registry,
        required_scopes=[PRIVATE_READ_SCOPE],
        target_status="blocked",
        decision_scope=PRIVATE_READ_SCOPE,
    )
    result = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=gate["todo_id"],
        role="user",
        agent_id=AGENT_ID,
        evidence="Close the request without recording an owner decision",
    )

    assert result.get("decision_outcome") is None
    assert "decision_scope_resolution" not in result
    assert _todo(state, gate["todo_id"])["status"] == "done"
    current_target = _todo(state, target["todo_id"])
    assert current_target["status"] == "blocked"
    assert current_target["required_decision_scopes"][0]["scope_key"] == (
        "restricted_material"
    )


def test_custom_gate_consumes_only_its_explicit_custom_requirement(tmp_path: Path) -> None:
    from loopx.cli import main
    from contextlib import redirect_stdout
    from io import StringIO

    _, state, registry = _canonical_fixture(tmp_path)
    custom = "other:action:study_conflict_review"
    unrelated = "other:action:study_method_review"
    named = "public_claim:action:publish_study"
    target = add_goal_todo(registry_path=registry, goal_id=GOAL_ID, role="agent",
        text="Prepare a study with separate owner reviews and publication permission.",
        task_class="advancement_task", claimed_by=AGENT_ID,
        required_decision_scopes=[custom, unrelated, named])
    common = ["--registry", str(registry), "--runtime-root", str(tmp_path / "runtime"),
        "--format", "json", "todo"]
    output = StringIO()
    with redirect_stdout(output):
        code = main([*common, "add", "--goal-id", GOAL_ID, "--role", "user",
            "--text", "Review the study conflict-of-interest declaration.",
            "--task-class", "user_gate", "--blocks-agent", AGENT_ID,
            "--decision-scope", custom, "--unblocks-todo-id", target["todo_id"]])
    assert code == 0
    gate = json.loads(output.getvalue())
    assert _todo(state, gate["todo_id"])["decision_scope"]["kind"] == "other"
    result = complete_goal_todo(registry_path=registry, goal_id=GOAL_ID,
        todo_id=gate["todo_id"], role="user", agent_id=AGENT_ID,
        decision_outcome="approve",
        evidence="Owner approved the conflict-of-interest declaration only.")
    resolution = result["decision_scope_resolution"]
    assert [(scope["kind"], scope["scope_key"]) for scope in resolution["resolved_required_decision_scopes"]] == [
        ("other", "study_conflict_review")]
    expected = [("other", "study_method_review"), ("public_claim", "publish_study")]
    assert [(scope["kind"], scope["scope_key"]) for scope in resolution["remaining_required_decision_scopes"]] == expected
    saved = _todo(state, target["todo_id"])
    assert [(scope["kind"], scope["scope_key"]) for scope in saved["required_decision_scopes"]] == expected
    before = state.read_bytes()
    with pytest.raises(ValueError, match="decision_scope"):
        add_goal_todo(registry_path=registry, goal_id=GOAL_ID, role="user",
            text="Unknown kind must not become custom authority.", task_class="user_gate",
            blocks_agent=AGENT_ID, decision_scope="othre:action:study_conflict_review")
    assert state.read_bytes() == before
