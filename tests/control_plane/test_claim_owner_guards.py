"""Owner-level regressions for the canonical Todo claim guards.

These cases drive the TypeScript claim owner directly through the effect
runtime on disposable real File/SQLite stores. They hold with or without any
Lark room surface: the guards protect every caller of the canonical claim.
"""
import json
from pathlib import Path

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.effect_runtime import effect_runtime_result

GOAL = "claim-guard-fixture-goal"
TODO = "todo_claim_guard"


PROVIDER_AUTHORITY = {"file": "file_v0", "sqlite": "sqlite_v0"}


@pytest.fixture(params=["file", "sqlite"])
def authority(tmp_path, monkeypatch, request):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    provider = request.param
    runtime, registry, state = tmp_path / "runtime", tmp_path / ".loopx/registry.json", tmp_path / "state.md"
    registry.parent.mkdir()
    state.write_text("## Agent Todo\n")
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{
        "id": GOAL, "repo": str(tmp_path), "state_file": state.name,
        "coordination": {"registered_agents": ["agent-a", "agent-b"]}}]}))
    yield runtime, registry, state, provider
    effect_runtime_result("runtime.shutdown", {}, retry_safe=False)


def _seed(runtime: Path, todos: list[dict], state: Path, provider: str) -> None:
    projection = build_todo_runtime_shadow_projection(goal_id=GOAL, todos=todos, handoff_mode="soft_claim")
    initialize_canonical_authority(runtime, GOAL, projection, state_path=state, provider=provider)


def _claim(runtime: Path, registry: Path, actor: str, key: str, **extra):
    import hashlib
    with open(registry, "rb") as handle:
        source = {"path": str(registry.resolve()),
            "sha256": hashlib.sha256(handle.read()).hexdigest()}
    return effect_runtime_result("coordination.local_authority.todo_claim", {
        "schema_version": "loopx_local_coordination_todo_claim_request_v1",
        "runtime_root": str(runtime), "goal_id": GOAL, "todo_id": TODO,
        "role": "agent", "claimed_by": actor, "actor_agent_id": actor,
        "registered_agents": ["agent-a", "agent-b"], "registry_source": source,
        "operation_id": key, "lease_request": None, "observed_at": "2026-11-23T00:00:00Z",
        "dry_run": False, **extra})


def _todo(runtime: Path) -> dict:
    snapshot = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL)
    return next(todo for todo in snapshot["todos"] if todo["todo_id"] == TODO)


def _open_todo(**fields) -> dict:
    return {"schema_version": "todo_item_v0", "todo_id": TODO, "role": "agent", "status": "open",
        "done": False, "text": "PRIVATE_ORGANIZATIONAL_CONTEXT", "archive_state": "active",
        "source_section": "Agent Todo", "index": 1, "task_class": "advancement_task", **fields}


def test_claim_on_todo_bound_to_another_agent_is_rejected_without_side_effects(authority):
    runtime, registry, state, provider = authority
    _seed(runtime, [_open_todo(bound_agent="agent-b")], state, provider)
    rejected = _claim(runtime, registry, "agent-a", "guard-bound-mismatch")
    assert rejected["status"] == "failed" and rejected["reason_code"] == "bound_agent_mismatch", rejected
    assert rejected["decision_read_from_provider"] is True
    assert rejected["source_authority"] == PROVIDER_AUTHORITY[provider], rejected
    todo = _todo(runtime)
    assert not todo.get("claimed_by")
    receipt = effect_runtime_result("coordination.local_authority.operation_receipt", {
        "schema_version": "loopx_local_coordination_operation_receipt_request_v0",
        "runtime_root": str(runtime), "goal_id": GOAL, "operation_id": "guard-bound-mismatch"})
    assert receipt["status"] == "missing", receipt
    # The bound agent itself still claims normally.
    accepted = _claim(runtime, registry, "agent-b", "guard-bound-owner")
    assert accepted["status"] == "applied", accepted
    assert accepted["source_authority"] == PROVIDER_AUTHORITY[provider], accepted
    assert _todo(runtime)["claimed_by"] == "agent-b"


def test_claim_wires_reject_an_unqualified_goal_ref_before_any_provider_effect(authority):
    runtime, registry, state, provider = authority
    _seed(runtime, [_open_todo()], state, provider)
    for goal_ref in (None, {"goal_id": GOAL, "goal_instance_id": "ginst_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}):
        rejected = _claim(runtime, registry, "agent-a", "guard-goal-ref", goal_ref=goal_ref)
        assert rejected["status"] == "failed", rejected
        assert rejected["reason_code"] == "goal_ref_claim_contract_unqualified", rejected
        assert rejected["decision_read_from_provider"] is False
        assert rejected["legacy_fallback_used"] is False
    assert not _todo(runtime).get("claimed_by")
    # Omitting the field keeps the ordinary claim path.
    accepted = _claim(runtime, registry, "agent-a", "guard-goal-ref-clean")
    assert accepted["status"] == "applied", accepted
    assert accepted["source_authority"] == PROVIDER_AUTHORITY[provider], accepted
    assert _todo(runtime)["claimed_by"] == "agent-a"


def test_ordinary_unclaimed_self_claim_and_same_key_replay_keep_room_off_parity(authority):
    runtime, registry, state, provider = authority
    _seed(runtime, [_open_todo()], state, provider)
    first = _claim(runtime, registry, "agent-a", "guard-parity-key")
    assert first["status"] == "applied" and first.get("claimed_by") == "agent-a", first
    assert first["source_authority"] == PROVIDER_AUTHORITY[provider], first
    snapshot = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL)
    assert snapshot["source_authority"] == PROVIDER_AUTHORITY[provider], snapshot
    revision = snapshot["provider_revision"]
    replay = _claim(runtime, registry, "agent-a", "guard-parity-key")
    assert replay["status"] == "replayed", replay
    assert replay["source_authority"] == PROVIDER_AUTHORITY[provider], replay
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL)["provider_revision"] == revision
