from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from canonical_authority_fixture import initialize_canonical_authority

from loopx.control_plane.coordination import local_authority as local_authority_module
from loopx.control_plane.coordination.coordination_state_contract import (
    TODO_DOMAIN_READ_RECORD_SCHEMA_VERSION,
    TODO_DOMAIN_RECORD_FIELDS,
)
from loopx.control_plane.coordination.legacy_writer_fence import (
    legacy_coordination_writer_fence_path,
)
from loopx.control_plane.coordination.local_authority import (
    LocalCoordinationAuthorityRejection,
    LocalCoordinationAuthorityUnavailable,
    claim_canonical_todo_if_promoted,
    read_canonical_todos_if_promoted,
)
from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)
from loopx.control_plane.todos import (
    provider_create,
    provider_projection,
    provider_terminal_lifecycle,
)
from loopx.control_plane.todos.active_state_editing import (
    TODO_SECTION_HEADINGS,
    section_bounds,
    todo_blocks,
)
from loopx.control_plane.todos.completion_validation_projection import (
    completion_validation_declaration_sha256,
    project_completion_validation_authority,
)
from loopx.control_plane.todos.completion_validation_store import (
    completion_validation_declaration_path,
    read_completion_validation_declaration,
)
from loopx.control_plane.todos.contract import format_todo_metadata_line
from loopx.todos import (
    add_goal_todo,
    archive_completed_todos,
    complete_goal_todo,
    list_goal_todos,
    supersede_goal_todo,
)


def _engage_fence(runtime_root: Path, goal_id: str = "goal-a") -> None:
    path = legacy_coordination_writer_fence_path(
        runtime_root=runtime_root,
        goal_id=goal_id,
    )
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"state": "engaged"}), encoding="utf-8")


def _todo_read_model(todo_count: int) -> dict[str, object]:
    return {
        "schema_version": "loopx_todo_canonical_read_record_v0",
        "todo_count": todo_count,
    }


@pytest.mark.parametrize("invalid", [True, "1", 1.5])
def test_python_terminal_adapter_rejects_coercible_numeric_values(
    tmp_path: Path,
    invalid: object,
) -> None:
    state_file = tmp_path / "ACTIVE_GOAL_STATE.md"
    state_file.write_text("# Goal\n\n## Agent Todo\n", encoding="utf-8")
    runtime_root = tmp_path / "runtime"
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "common_runtime_root": str(runtime_root),
                "goals": [
                    {
                        "id": "goal-a",
                        "repo": str(tmp_path),
                        "state_file": state_file.name,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="max_active_done must be a non-negative integer"):
        archive_completed_todos(
            registry_path=registry,
            goal_id="goal-a",
            max_active_done=invalid,  # type: ignore[arg-type]
            dry_run=False,
        )
    with pytest.raises(
        ValueError,
        match="task_lease_expected_version must be a non-negative integer or None",
    ):
        complete_goal_todo(
            registry_path=registry,
            goal_id="goal-a",
            todo_id="todo-a",
            evidence="strict adapter validation",
            task_lease_idempotency_key="lease-a",
            task_lease_expected_version=invalid,  # type: ignore[arg-type]
            next_agent_todo="Continue after strict validation.",
            next_task_class="advancement_task",
        )
    assert not runtime_root.exists()


def test_absent_fence_preserves_legacy_path_without_starting_typescript(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "loopx.control_plane.coordination.local_authority.effect_runtime_result",
        lambda *_args, **_kwargs: pytest.fail("pre-cutover read must stay legacy"),
    )
    assert (
        read_canonical_todos_if_promoted(
            runtime_root=tmp_path,
            goal_id="goal-a",
        )
        is None
    )


def test_engaged_fence_reads_typescript_provider_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _engage_fence(tmp_path)
    monkeypatch.setattr(
        "loopx.control_plane.coordination.local_authority.effect_runtime_result",
        lambda method, params: {
            "status": "loaded",
            "todos": [{"todo_id": "todo_a", "role": "agent", "status": "open"}],
            "todo_read_model": _todo_read_model(1),
            "provider_revision": "file:1",
            "cursor": "1",
            "source_authority": "file_v0",
            "decision_read_from_provider": True,
            "legacy_fallback_used": False,
        },
    )
    result = read_canonical_todos_if_promoted(
        runtime_root=tmp_path,
        goal_id="goal-a",
    )
    assert result is not None
    assert result["todos"][0]["todo_id"] == "todo_a"


def test_promoted_claim_adapter_invokes_typescript_without_markdown_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "goals": [
                    {
                        "id": "goal-a",
                        "coordination": {"registered_agents": ["agent-a", "agent-b"]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    _engage_fence(tmp_path)
    calls: list[tuple[str, dict[str, object]]] = []

    def _claim(method: str, params: dict[str, object]) -> dict[str, object]:
        calls.append((method, params))
        return {
            "status": "applied",
            "changed": True,
            "todo_id": "todo_a",
            "claimed_by": "agent-a",
            "source_authority": "file_v0",
            "decision_read_from_provider": True,
            "legacy_fallback_used": False,
        }

    monkeypatch.setattr(
        "loopx.control_plane.coordination.local_authority.effect_runtime_result",
        _claim,
    )
    result = claim_canonical_todo_if_promoted(
        registry_path=registry,
        runtime_root=tmp_path,
        goal_id="goal-a",
        todo_id="todo_a",
        role="agent",
        claimed_by="agent-a",
        actor_agent_id="agent-a",
        dry_run=False,
        task_lease_idempotency_key="turn:claim-and-acquire",
        task_lease_expected_version=0,
    )

    assert result is not None and result["changed"] is True
    assert calls[0][0] == "coordination.local_authority.todo_claim"
    assert calls[0][1]["registered_agents"] == ["agent-a", "agent-b"]
    assert str(calls[0][1]["operation_id"]).startswith("todo-claim:goal-a:todo_a:")
    assert isinstance(calls[0][1]["observed_at"], str)
    assert calls[0][1]["lease_request"] == {
        "idempotency_key": "turn:claim-and-acquire",
        "expected_version": 0,
        "ttl_seconds": None,
    }


def test_promoted_add_invokes_native_create_without_markdown_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(tmp_path / "runtime"),
                "goals": [
                    {
                        "id": "goal-a",
                        "coordination": {"registered_agents": ["agent-a", "agent-b"]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        "loopx.control_plane.todos.provider_create.read_canonical_todos_if_promoted",
        lambda **_kwargs: {"todos": []},
    )

    def _create(method: str, params: dict[str, object]) -> dict[str, object]:
        calls.append((method, params))
        todo = params["todo"]
        assert isinstance(todo, dict)
        return {
            "status": "applied",
            "changed": True,
            "todo_id": todo["todo_id"],
            "todo": todo,
            "source_authority": "file_v0",
            "decision_read_from_provider": True,
            "legacy_fallback_used": False,
        }

    monkeypatch.setattr(
        "loopx.control_plane.todos.provider_create.effect_runtime_result", _create
    )
    result = add_goal_todo(
        registry_path=registry,
        goal_id="goal-a",
        role="agent",
        text="Create natively",
        claimed_by="agent-a",
        agent_id="agent-a",
        task_class="advancement_task",
        action_kind="implement",
        validation_command_json='["python", "-c", "pass"]',
    )

    assert result["added"] is True
    assert calls[0][0] == "coordination.local_authority.todo_create"
    assert calls[0][1]["todo"]["schema_version"] == "todo_domain_record_v0"
    assert calls[0][1]["todo"]["claimed_by"] == "agent-a"
    assert calls[0][1]["todo"]["completion_validation_required"] is True
    assert len(calls[0][1]["todo"]["completion_validation_sha256"]) == 64
    assert "validation_command_argv" not in calls[0][1]["todo"]
    private_declaration = read_completion_validation_declaration(
        runtime_root=tmp_path / "runtime",
        goal_id="goal-a",
        todo_id=str(result["todo_id"]),
    )
    assert private_declaration is not None
    assert private_declaration["validation_command_argv"] == ["python", "-c", "pass"]
    assert calls[0][1]["registered_agents"] == ["agent-a", "agent-b"]


def test_promoted_add_delegates_semantic_duplicate_to_typescript(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "loopx.control_plane.todos.provider_create.read_canonical_todos_if_promoted",
        lambda **_kwargs: {
            "todos": [
                {
                    "todo_id": "todo_existing",
                    "role": "agent",
                    "status": "open",
                    "archive_state": "active",
                    "text": "Already native",
                }
            ]
        },
    )
    calls: list[tuple[str, dict[str, object]]] = []

    def _create(method: str, params: dict[str, object]) -> dict[str, object]:
        calls.append((method, params))
        return {
            "status": "no_change",
            "changed": False,
            "todo_id": "todo_existing",
            "source_authority": "file_v0",
            "decision_read_from_provider": True,
            "legacy_fallback_used": False,
        }

    monkeypatch.setattr(
        "loopx.control_plane.todos.provider_create.effect_runtime_result",
        _create,
    )
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "goals": [
                    {"id": "goal-a", "coordination": {"registered_agents": ["agent-a"]}}
                ],
            }
        ),
        encoding="utf-8",
    )

    result = add_goal_todo(
        registry_path=registry,
        goal_id="goal-a",
        role="agent",
        text="Already native",
        claimed_by="agent-a",
        agent_id="agent-a",
    )

    assert result["already_exists"] is True
    assert result["todo_id"] == "todo_existing"
    assert calls[0][0] == "coordination.local_authority.todo_create"


def _promoted_create_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    runtime_root = tmp_path / "runtime"
    project = tmp_path / "project"
    state_file = project / ".codex/goals/goal-a/ACTIVE_GOAL_STATE.md"
    state_file.parent.mkdir(parents=True)
    state_file.write_text(
        "# Goal\n\n## User Todo / Owner Review Reading Queue\n\n"
        "## Agent Todo\n\n## Completed Work Archive\n",
        encoding="utf-8",
    )
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(runtime_root),
                "goals": [
                    {
                        "id": "goal-a",
                        "repo": str(project),
                        "state_file": ".codex/goals/goal-a/ACTIVE_GOAL_STATE.md",
                        "coordination": {"registered_agents": ["agent-a"]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    projection = build_todo_runtime_shadow_projection(
        goal_id="goal-a", todos=[], handoff_mode="soft_claim"
    )
    projection["todo_read_model"] = {
        **projection["todo_read_model"],
        "schema_version": TODO_DOMAIN_READ_RECORD_SCHEMA_VERSION,
        "contract_fields": list(TODO_DOMAIN_RECORD_FIELDS),
    }
    initialize_canonical_authority(
        runtime_root, "goal-a", projection, state_path=state_file
    )
    return registry_path, runtime_root, state_file


def test_rejected_validated_create_publishes_no_private_sidecar(
    tmp_path: Path,
) -> None:
    registry_path, runtime_root, _state_file = _promoted_create_fixture(tmp_path)
    first = add_goal_todo(
        registry_path=registry_path,
        goal_id="goal-a",
        role="agent",
        text="Keep one accepted validation declaration",
        claimed_by="agent-a",
        agent_id="agent-a",
        validation_command_json=json.dumps(["python3", "-c", "raise SystemExit(0)"]),
        validation_label="accepted declaration",
    )
    first_id = str(first["todo_id"])
    accepted = read_completion_validation_declaration(
        runtime_root=runtime_root,
        goal_id="goal-a",
        todo_id=first_id,
    )
    assert accepted is not None

    with pytest.raises(LocalCoordinationAuthorityUnavailable) as exc_info:
        add_goal_todo(
            registry_path=registry_path,
            goal_id="goal-a",
            role="agent",
            text="Keep one accepted validation declaration",
            claimed_by="agent-a",
            agent_id="agent-a",
            validation_command_json=json.dumps(
                ["python3", "-c", "raise SystemExit(7)"]
            ),
            validation_label="rejected declaration",
        )
    assert exc_info.value.code == "todo_semantic_duplicate_conflict"
    declaration_dir = completion_validation_declaration_path(
        runtime_root=runtime_root,
        goal_id="goal-a",
        todo_id=first_id,
    ).parent
    assert [path.name for path in declaration_dir.glob("*.json")] == [
        f"{first_id}.json"
    ]
    assert (
        read_completion_validation_declaration(
            runtime_root=runtime_root,
            goal_id="goal-a",
            todo_id=first_id,
        )
        == accepted
    )


def test_concurrent_validated_create_publishes_only_the_canonical_winner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    registry_path, runtime_root, _state_file = _promoted_create_fixture(tmp_path)
    real_read = provider_create.read_canonical_todos_if_promoted
    barrier = Barrier(2)

    def synchronized_read(**kwargs: object) -> dict[str, object] | None:
        result = real_read(**kwargs)  # type: ignore[arg-type]
        barrier.wait(timeout=10)
        return result

    monkeypatch.setattr(
        provider_create,
        "read_canonical_todos_if_promoted",
        synchronized_read,
    )

    def create(label: str, exit_code: int) -> tuple[str, object]:
        try:
            return (
                "accepted",
                add_goal_todo(
                    registry_path=registry_path,
                    goal_id="goal-a",
                    role="agent",
                    text="Resolve concurrent validation ownership",
                    claimed_by="agent-a",
                    agent_id="agent-a",
                    validation_command_json=json.dumps(
                        ["python3", "-c", f"raise SystemExit({exit_code})"]
                    ),
                    validation_label=label,
                ),
            )
        except LocalCoordinationAuthorityUnavailable as exc:
            return ("rejected", exc.code)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda values: create(*values),
                [("candidate-a", 0), ("candidate-b", 7)],
            )
        )
    assert [kind for kind, _value in results].count("accepted") == 1
    assert [kind for kind, _value in results].count("rejected") == 1
    assert next(value for kind, value in results if kind == "rejected") == (
        "todo_semantic_duplicate_conflict"
    )

    canonical = read_canonical_todos_if_promoted(
        runtime_root=runtime_root, goal_id="goal-a"
    )
    assert canonical is not None and len(canonical["todos"]) == 1
    canonical_todo = canonical["todos"][0]
    canonical_todo_id = str(canonical_todo["todo_id"])
    stored = read_completion_validation_declaration(
        runtime_root=runtime_root,
        goal_id="goal-a",
        todo_id=canonical_todo_id,
    )
    assert stored is not None
    assert completion_validation_declaration_sha256(stored) == (
        canonical_todo["completion_validation_sha256"]
    )
    declaration_dir = completion_validation_declaration_path(
        runtime_root=runtime_root,
        goal_id="goal-a",
        todo_id=canonical_todo_id,
    ).parent
    assert [path.name for path in declaration_dir.glob("*.json")] == [
        f"{canonical_todo_id}.json"
    ]


def test_validated_create_recovers_sidecar_after_commit_before_publish_crash(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    registry_path, runtime_root, _state_file = _promoted_create_fixture(tmp_path)
    real_persist = provider_create.persist_completion_validation_declaration
    persist_calls = 0

    def crash_once(**kwargs: object) -> str:
        nonlocal persist_calls
        persist_calls += 1
        if persist_calls == 1:
            raise OSError("injected validation sidecar publication crash")
        return real_persist(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        provider_create,
        "persist_completion_validation_declaration",
        crash_once,
    )
    create_kwargs = {
        "registry_path": registry_path,
        "goal_id": "goal-a",
        "role": "agent",
        "text": "Recover the private declaration publication",
        "claimed_by": "agent-a",
        "agent_id": "agent-a",
        "validation_command_json": json.dumps(
            ["python3", "-c", "raise SystemExit(0)"]
        ),
        "validation_label": "recover publication",
    }
    with pytest.raises(OSError, match="publication crash"):
        add_goal_todo(**create_kwargs)

    canonical = read_canonical_todos_if_promoted(
        runtime_root=runtime_root,
        goal_id="goal-a",
    )
    assert canonical is not None and len(canonical["todos"]) == 1
    todo_id = str(canonical["todos"][0]["todo_id"])
    assert read_completion_validation_declaration(
        runtime_root=runtime_root,
        goal_id="goal-a",
        todo_id=todo_id,
    ) is None

    recovered = add_goal_todo(**create_kwargs)
    assert recovered["status"] == "no_change"
    assert recovered["todo_id"] == todo_id
    assert recovered["added"] is False
    assert recovered["already_exists"] is True
    stored = read_completion_validation_declaration(
        runtime_root=runtime_root,
        goal_id="goal-a",
        todo_id=todo_id,
    )
    assert stored is not None
    assert completion_validation_declaration_sha256(stored) == (
        canonical["todos"][0]["completion_validation_sha256"]
    )
    canonical_after = read_canonical_todos_if_promoted(
        runtime_root=runtime_root,
        goal_id="goal-a",
    )
    assert canonical_after is not None and len(canonical_after["todos"]) == 1


def test_promoted_native_create_recovers_markdown_after_delivery_crash(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    project = tmp_path / "project"
    state_file = project / ".codex/goals/goal-a/ACTIVE_GOAL_STATE.md"
    state_file.parent.mkdir(parents=True)
    source = """# Goal

Human context.

## Agent Todo

## Next Action

Continue.
"""
    state_file.write_text(source, encoding="utf-8")
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(runtime_root),
                "goals": [
                    {
                        "id": "goal-a",
                        "status": "active",
                        "repo": str(project),
                        "state_file": ".codex/goals/goal-a/ACTIVE_GOAL_STATE.md",
                        "coordination": {
                            "registered_agents": ["agent-a", "agent-b"]
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    projection = build_todo_runtime_shadow_projection(
        goal_id="goal-a", todos=[], handoff_mode="soft_claim"
    )
    projection["todo_read_model"] = {
        **projection["todo_read_model"],
        "schema_version": TODO_DOMAIN_READ_RECORD_SCHEMA_VERSION,
        "contract_fields": list(TODO_DOMAIN_RECORD_FIELDS),
    }
    initialize_canonical_authority(runtime_root, "goal-a", projection, state_path=state_file)

    real_write = provider_projection.atomic_write_state_text

    def crash(*_args: object, **_kwargs: object) -> None:
        raise OSError("injected projection crash")

    monkeypatch.setattr(provider_projection, "atomic_write_state_text", crash)
    applied = add_goal_todo(
        registry_path=registry_path,
        goal_id="goal-a",
        role="agent",
        text="Recover the native compatibility projection",
        task_class="advancement_task",
        action_kind="implement",
        claimed_by="agent-a",
        agent_id="agent-a",
        validation_command_json=json.dumps(["python3", "-c", "raise SystemExit(0)"]),
        validation_label="recoverable provider validation",
    )

    assert applied["status"] == "applied"
    assert applied["projection_delivery"] == "pending"
    canonical = read_canonical_todos_if_promoted(
        runtime_root=runtime_root,
        goal_id="goal-a",
    )
    assert canonical is not None
    assert canonical["todos"][0]["schema_version"] == "todo_domain_record_v0"
    assert canonical["todos"][0]["completion_validation_required"] is True
    assert len(canonical["todos"][0]["completion_validation_sha256"]) == 64
    assert "validation_command_argv" not in canonical["todos"][0]
    assert state_file.read_text(encoding="utf-8") == source

    monkeypatch.setattr(provider_projection, "atomic_write_state_text", real_write)
    replay = add_goal_todo(
        registry_path=registry_path,
        goal_id="goal-a",
        role="agent",
        text="Recover the native compatibility projection",
        task_class="advancement_task",
        action_kind="implement",
        claimed_by="agent-a",
        agent_id="agent-a",
        validation_command_json=json.dumps(["python3", "-c", "raise SystemExit(0)"]),
        validation_label="recoverable provider validation",
    )
    assert replay["status"] == "no_change"
    assert replay["projection_delivery"] == "delivered"
    rendered = state_file.read_text(encoding="utf-8")
    assert "Recover the native compatibility projection" in rendered
    assert "## User Todo / Owner Review Reading Queue" in rendered
    assert "Human context." in rendered
    assert "Continue." in rendered
    completed = complete_goal_todo(
        registry_path=registry_path,
        runtime_root_arg=str(runtime_root),
        goal_id="goal-a",
        todo_id=str(applied["todo_id"]),
        role="agent",
        claimed_by="agent-a",
        agent_id="agent-a",
        no_followup=True,
    )
    assert completed["status"] == "done"
    assert completed["provider_status"] == "applied"
    assert completed["validation_receipt"]["passed"] is True


def test_engaged_fence_never_falls_back_when_provider_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _engage_fence(tmp_path)
    monkeypatch.setattr(
        "loopx.control_plane.coordination.local_authority.effect_runtime_result",
        lambda method, params: {
            "status": "missing",
            "source_authority": "file_v0",
            "decision_read_from_provider": True,
            "legacy_fallback_used": False,
        },
    )
    with pytest.raises(LocalCoordinationAuthorityUnavailable) as exc_info:
        read_canonical_todos_if_promoted(runtime_root=tmp_path, goal_id="goal-a")
    assert exc_info.value.code == "local_authority_todo_list_unavailable"


def _claim_registry(tmp_path: Path) -> Path:
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "goals": [
                    {
                        "id": "goal-a",
                        "coordination": {"registered_agents": ["agent-a"]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return registry


def _seed_promoted_store(
    runtime_root: Path, *, handoff_mode: str | None = None
) -> None:
    """Initialize one open agent Todo in the canonical provider and engage its fence.

    This branch's bootstrap binds a registered source state path and takes the
    management locks, so the promotion is seeded through the shared fixture that
    runs the real FileAuthorityStore and the real fence engagement instead of the
    effect-runtime bootstrap/commit/promote sequence.
    """

    projection = build_todo_runtime_shadow_projection(
        goal_id="goal-a",
        todos=[
            {
                "schema_version": "todo_item_v0",
                "index": 1,
                "done": False,
                "text": "Claim through the promoted provider head",
                "todo_id": "todo_a",
                "role": "agent",
                "status": "open",
                "archive_state": "active",
                "source_section": TODO_SECTION_HEADINGS["agent"],
            }
        ],
    )
    # main's builder carries no handoff mode and the claim owner reads a missing
    # mode as legacy; this branch's builder defaults to hard_lease, so seed the
    # legacy mode explicitly unless a test asks for another one.
    projection["handoff_mode"] = str(handoff_mode) if handoff_mode is not None else "legacy"
    state_path = runtime_root / "ACTIVE_GOAL_STATE.md"
    if not state_path.exists():
        state_path.write_text(
            "---\ngoal_id: goal-a\n---\n\n## Agent Todo\n\n", encoding="utf-8"
        )
    initialize_canonical_authority(runtime_root, "goal-a", projection, state_path=state_path)


def test_promoted_claim_rejection_preserves_legacy_valueerror_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Promoted claim rejections must stay catchable via ``except ValueError``.

    The legacy kernel raised ValueError for decision rejections such as
    todo_not_open; external Python API callers rely on that contract.
    """
    _engage_fence(tmp_path)
    monkeypatch.setattr(
        "loopx.control_plane.coordination.local_authority.effect_runtime_result",
        lambda method, params: {
            "status": "failed",
            "failure_kind": "decision_rejection",
            "reason_code": "todo_not_open",
            "reason": "todo claim requires status=open",
            "source_authority": "file_v0",
            "decision_read_from_provider": True,
            "legacy_fallback_used": False,
        },
    )
    with pytest.raises(ValueError) as exc_info:
        claim_canonical_todo_if_promoted(
            registry_path=_claim_registry(tmp_path),
            runtime_root=tmp_path,
            goal_id="goal-a",
            todo_id="todo_a",
            role="agent",
            claimed_by="agent-a",
            actor_agent_id="agent-a",
            dry_run=False,
        )
    rejection = exc_info.value
    assert isinstance(rejection, LocalCoordinationAuthorityRejection)
    # Callers that already migrated to the authority-unavailable handling of
    # promoted claims keep working: the rejection is still its subclass.
    assert isinstance(rejection, LocalCoordinationAuthorityUnavailable)
    assert rejection.code == "todo_not_open"
    assert str(rejection) == "todo claim requires status=open"


def test_promoted_claim_protocol_failure_stays_infrastructure_outage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Protocol-level request failures must not masquerade as ValueErrors."""
    _engage_fence(tmp_path)
    monkeypatch.setattr(
        "loopx.control_plane.coordination.local_authority.effect_runtime_result",
        lambda method, params: {
            "status": "failed",
            "reason_code": "invalid_local_coordination_todo_claim_request",
            "reason": "registered_agents must be a JSON array",
            "source_authority": "file_v0",
            "decision_read_from_provider": True,
            "legacy_fallback_used": False,
        },
    )
    with pytest.raises(LocalCoordinationAuthorityUnavailable) as exc_info:
        claim_canonical_todo_if_promoted(
            registry_path=_claim_registry(tmp_path),
            runtime_root=tmp_path,
            goal_id="goal-a",
            todo_id="todo_a",
            role="agent",
            claimed_by="agent-a",
            actor_agent_id="agent-a",
            dry_run=False,
        )
    assert exc_info.value.code == "invalid_local_coordination_todo_claim_request"
    assert not isinstance(exc_info.value, ValueError)
    assert not isinstance(exc_info.value, LocalCoordinationAuthorityRejection)


def test_promoted_claim_folds_agent_id_whitespace_like_legacy(tmp_path: Path) -> None:
    """Every Python whitespace character (including U+0085 NEL, U+001C..U+001F,
    tabs, NBSP) in claimed_by must fold to '-' identically before and after promotion.
    """
    from loopx.control_plane.todos.contract import normalize_todo_claimed_by

    variants = [
        "Agent A",
        "Agent\tA",
        "Agent\u0085A",
        "Agent\u001cA",
        "Agent\u001dA",
        "Agent\u001eA",
        "Agent\u001fA",
        "Agent\u00a0A",
        "\u0085 Agent \t A \u001c ",
    ]
    for variant in variants:
        assert normalize_todo_claimed_by(variant) == "agent-a"

    _seed_promoted_store(tmp_path)
    result = claim_canonical_todo_if_promoted(
        registry_path=_claim_registry(tmp_path),
        runtime_root=tmp_path,
        goal_id="goal-a",
        todo_id="todo_a",
        role="agent",
        claimed_by="Agent\u0085A",
        actor_agent_id="\u0085 Agent \t A \u001c ",
        dry_run=False,
    )
    assert result is not None and result["ok"] is True
    assert result["status"] == "applied"
    assert result["claimed_by"] == "agent-a"


def test_promoted_claim_missing_todo_rejection_is_valueerror(tmp_path: Path) -> None:
    """End-to-end: a real TypeScript decision rejection raises ValueError."""
    _seed_promoted_store(tmp_path)
    with pytest.raises(ValueError) as exc_info:
        claim_canonical_todo_if_promoted(
            registry_path=_claim_registry(tmp_path),
            runtime_root=tmp_path,
            goal_id="goal-a",
            todo_id="todo_missing",
            role="agent",
            claimed_by="agent-a",
            actor_agent_id="agent-a",
            dry_run=False,
        )
    assert isinstance(exc_info.value, LocalCoordinationAuthorityRejection)
    assert exc_info.value.code == "todo_not_found"


def test_todo_list_uses_provider_after_cutover_even_when_markdown_disagrees(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    state_file = project / ".codex/goals/goal-a/ACTIVE_GOAL_STATE.md"
    state_file.parent.mkdir(parents=True)
    state_file.write_text(
        "# Goal\n\n## Agent Todos\n\n- [ ] stale Markdown Todo <!-- loopx:todo todo_id=todo_stale status=open -->\n",
        encoding="utf-8",
    )
    runtime_root = tmp_path / "runtime"
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(runtime_root),
                "goals": [
                    {
                        "id": "goal-a",
                        "status": "active",
                        "repo": str(project),
                        "state_file": ".codex/goals/goal-a/ACTIVE_GOAL_STATE.md",
                        "coordination": {"registered_agents": ["agent-a", "agent-b"]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    _engage_fence(runtime_root)
    state_file.unlink()
    monkeypatch.setattr(
        "loopx.control_plane.coordination.local_authority.effect_runtime_result",
        lambda method, params: {
            "status": "loaded",
            "todos": [
                {
                    "todo_id": "todo_provider",
                    "role": "agent",
                    "status": "open",
                    "text": "provider Todo",
                }
            ],
            "todo_read_model": _todo_read_model(1),
            "provider_revision": "file:2",
            "cursor": "2",
            "source_authority": "file_v0",
            "decision_read_from_provider": True,
            "legacy_fallback_used": False,
        },
    )

    result = list_goal_todos(registry_path=registry_path, goal_id="goal-a")

    assert result["source"] == "file_authority"
    assert [item["todo_id"] for item in result["todos"]] == ["todo_provider"]
    assert result["authority_read"]["legacy_fallback_used"] is False


def test_canonical_hard_lease_claim_cli_atomically_acquires_ownership(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    project = tmp_path / "project"
    state_file = project / ".codex/goals/goal-a/ACTIVE_GOAL_STATE.md"
    state_file.parent.mkdir(parents=True)
    state_file.write_text("# Goal\n", encoding="utf-8")
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(runtime_root),
                "goals": [
                    {
                        "id": "goal-a",
                        "status": "active",
                        "repo": str(project),
                        "state_file": ".codex/goals/goal-a/ACTIVE_GOAL_STATE.md",
                        "coordination": {
                            "registered_agents": ["agent-a", "agent-b"]
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    todo = {
        "schema_version": "todo_item_v0",
        "index": 1,
        "done": False,
        "text": "Claim and acquire one canonical ownership transaction",
        "todo_id": "todo_atomic_claim",
        "role": "agent",
        "status": "open",
        "archive_state": "active",
        "source_section": TODO_SECTION_HEADINGS["agent"],
        "required_write_scopes": ["loopx/control_plane/**"],
    }
    projection = build_todo_runtime_shadow_projection(
        goal_id="goal-a",
        todos=[todo],
    )
    projection["handoff_mode"] = "hard_lease"
    initialize_canonical_authority(runtime_root, "goal-a", projection, state_path=state_file)
    state_file.unlink()

    base_command = [
        sys.executable,
        "-m",
        "loopx.cli",
        "--format",
        "json",
        "--registry",
        str(registry_path),
        "todo",
        "claim",
        "--goal-id",
        "goal-a",
        "--todo-id",
        "todo_atomic_claim",
        "--claimed-by",
        "agent-a",
        "--agent-id",
        "agent-a",
        "--claim-operation-id",
        "atomic-cli-claim",
    ]
    initial_failure = subprocess.run(
        base_command,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert initial_failure.returncode == 1
    failure_payload = json.loads(initial_failure.stdout)
    assert failure_payload["ok"] is False
    assert failure_payload["error_code"] == "handoff_mode_requires_lease"
    assert failure_payload["handoff_mode"] == "hard_lease"
    assert "loopx todo claim --task-lease-idempotency-key" in failure_payload["error"]
    assert "--task-lease-expected-version" in failure_payload["error"]
    recovery = failure_payload.get("recovery") or {}
    assert recovery.get("command") == "loopx todo claim"
    assert recovery.get("requires_flags") == ["--task-lease-idempotency-key"]
    assert "--task-lease-expected-version" in (recovery.get("optional_flags") or [])

    command = [
        *base_command,
        "--task-lease-idempotency-key",
        "turn:atomic-cli-claim",
        "--task-lease-expected-version",
        "0",
    ]
    before = list_goal_todos(registry_path=registry_path, goal_id="goal-a")
    preview = json.loads(
        subprocess.run(
            [*command, "--dry-run"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout
    )
    assert preview["status"] == "planned"
    assert preview["todo_changed"] is True
    assert preview["lease_changed"] is True
    assert list_goal_todos(registry_path=registry_path, goal_id="goal-a") == before

    applied = json.loads(
        subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout
    )
    assert applied["status"] == "applied"
    assert applied["todo_changed"] is True
    assert applied["lease_changed"] is True
    assert applied["lease"]["owner"] == "agent-a"
    assert applied["lease"]["idempotency_key"] == "turn:atomic-cli-claim"
    assert applied["lease"]["write_scopes"] == ["loopx/control_plane/**"]
    replay = json.loads(
        subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout
    )
    assert replay["status"] == "replayed"
    assert replay["original_receipt"] == applied["original_receipt"]
    after = list_goal_todos(registry_path=registry_path, goal_id="goal-a")
    assert after["todos"][0]["claimed_by"] == "agent-a"
    assert not state_file.exists()

    edit = [sys.executable, "-m", "loopx.cli", "--format", "json", "--registry",
        str(registry_path), "todo", "update", "--goal-id", "goal-a", "--todo-id",
        "todo_atomic_claim", "--agent-id", "agent-a", "--text", "Correct leased task",
        "--note", "Updated note", "--update-operation-id", "cli-leased-edit",
        "--task-lease-idempotency-key", "turn:atomic-cli-claim",
        "--task-lease-expected-version", str(applied["lease"]["version"])]
    def invoke_edit(argv):
        return subprocess.run(argv, capture_output=True, text=True, timeout=30)
    preview_edit = invoke_edit([*edit, "--dry-run"])
    assert preview_edit.returncode == 0, preview_edit.stdout + preview_edit.stderr
    assert json.loads(preview_edit.stdout)["status"] == "planned"
    assert list_goal_todos(registry_path=registry_path, goal_id="goal-a") == after
    first_edit = invoke_edit(edit)
    assert first_edit.returncode == 0, first_edit.stdout + first_edit.stderr
    assert json.loads(first_edit.stdout)["status"] == "applied"
    retry_edit = invoke_edit(edit)
    assert retry_edit.returncode == 0, retry_edit.stdout + retry_edit.stderr
    assert json.loads(retry_edit.stdout)["status"] == "replayed"
    changed_edit = invoke_edit([*edit, "--note", "Different intent"])
    assert changed_edit.returncode != 0
    final = list_goal_todos(registry_path=registry_path, goal_id="goal-a")
    assert final["todos"][0]["text"] == "Correct leased task"
    assert final["todos"][0]["note"] == "Updated note"
    assert final["todos"][0]["claimed_by"] == "agent-a"
    assert not state_file.exists()


def test_promoted_terminal_lifecycle_commits_successors_and_archive_natively(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    project = tmp_path / "project"
    state_file = project / ".codex/goals/goal-a/ACTIVE_GOAL_STATE.md"
    state_file.parent.mkdir(parents=True)
    validation_argv = ["python3", "-c", "raise SystemExit(0)"]
    complete_metadata = format_todo_metadata_line(
        todo_id="todo_complete_native",
        status="open",
        task_class="advancement_task",
        claimed_by="agent-a",
        validation_command_argv=json.dumps(validation_argv),
        validation_label="provider terminal integration",
        validation_timeout_seconds=5,
    )
    supersede_metadata = format_todo_metadata_line(
        todo_id="todo_supersede_native",
        status="open",
        task_class="advancement_task",
        claimed_by="agent-b",
    )
    state_file.write_text(
        f"""# Goal

Human narrative remains outside canonical Todo authority.

## User Todo / Owner Review Reading Queue

## Agent Todo

- [ ] Complete through TypeScript authority
{complete_metadata}
- [ ] Supersede through TypeScript authority
{supersede_metadata}

## Completed Work Archive

## Next Action

Continue provider-first delivery.
""",
        encoding="utf-8",
    )
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(runtime_root),
                "goals": [
                    {
                        "id": "goal-a",
                        "status": "active",
                        "repo": str(project),
                        "state_file": ".codex/goals/goal-a/ACTIVE_GOAL_STATE.md",
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": ["agent-a", "agent-b"],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    todos = [
        {
            "schema_version": "todo_item_v0",
            "index": 1,
            "done": False,
            "text": "Complete through TypeScript authority",
            "todo_id": "todo_complete_native",
            "role": "agent",
            "status": "open",
            "archive_state": "active",
            "source_section": TODO_SECTION_HEADINGS["agent"],
            "task_class": "advancement_task",
            "claimed_by": "agent-a",
            "validation_command_argv": validation_argv,
            "validation_label": "provider terminal integration",
            "validation_timeout_seconds": 5,
        },
        {
            "schema_version": "todo_item_v0",
            "index": 2,
            "done": False,
            "text": "Supersede through TypeScript authority",
            "todo_id": "todo_supersede_native",
            "role": "agent",
            "status": "open",
            "archive_state": "active",
            "source_section": TODO_SECTION_HEADINGS["agent"],
            "task_class": "advancement_task",
            "claimed_by": "agent-b",
        },
    ]
    projection = build_todo_runtime_shadow_projection(
        goal_id="goal-a",
        todos=[project_completion_validation_authority(todo) for todo in todos],
        handoff_mode="soft_claim",
    )
    initialize_canonical_authority(
        runtime_root,
        "goal-a",
        projection,
        state_path=state_file,
    )
    runtime_calls: list[str] = []
    archive_operation_ids: list[str] = []
    original_effect_runtime_result = provider_terminal_lifecycle.effect_runtime_result
    original_authority_runtime_result = local_authority_module.effect_runtime_result

    def count_runtime_call(method: str, params: dict[str, object]) -> object:
        runtime_calls.append(method)
        if method == "coordination.local_authority.todo_archive":
            archive_operation_ids.append(str(params["operation_id"]))
        return original_effect_runtime_result(method, params)

    def count_authority_runtime_call(
        method: str, params: dict[str, object]
    ) -> object:
        runtime_calls.append(method)
        return original_authority_runtime_result(method, params)

    monkeypatch.setattr(
        provider_terminal_lifecycle,
        "effect_runtime_result",
        count_runtime_call,
    )
    monkeypatch.setattr(
        local_authority_module,
        "effect_runtime_result",
        count_authority_runtime_call,
    )

    completed = complete_goal_todo(
        registry_path=registry_path,
        runtime_root_arg=str(runtime_root),
        goal_id="goal-a",
        todo_id="todo_complete_native",
        role="agent",
        claimed_by="agent-a",
        agent_id="agent-a",
        next_agent_todo="Continue after the native terminal commit",
        next_claimed_by="agent-b",
        next_task_class="advancement_task",
        next_action_kind="implement",
        evidence="provider integration passed",
    )
    assert completed["status"] == "done"
    assert completed["provider_status"] == "applied"
    assert completed["completed"] is True
    assert completed["projection_delivery"] == "delivered", completed
    assert completed["validation_receipt"]["passed"] is True
    assert completed["validation_receipt"]["command_label"] == (
        "provider terminal integration"
    )
    assert runtime_calls == [
        "coordination.local_authority.todo_list",
        "coordination.local_authority.todo_terminal",
        "coordination.local_authority.todo_terminal",
        "coordination.local_authority.todo_list",
    ]
    successor_id = completed["generated_successor_todo_ids"][0]

    runtime_calls.clear()
    superseded = supersede_goal_todo(
        registry_path=registry_path,
        runtime_root_arg=str(runtime_root),
        goal_id="goal-a",
        todo_id="todo_supersede_native",
        role="agent",
        agent_id="agent-b",
        reason="replace with a smaller continuation",
        next_agent_todo="Replacement after native supersede",
        next_claimed_by="agent-b",
    )
    assert superseded["status"] == "done"
    assert superseded["provider_status"] == "applied"
    assert superseded["superseded"] is True
    assert superseded["projection_delivery"] == "delivered"
    assert runtime_calls == [
        "coordination.local_authority.todo_list",
        "coordination.local_authority.todo_terminal",
        "coordination.local_authority.todo_list",
    ]

    canonical = read_canonical_todos_if_promoted(
        runtime_root=runtime_root,
        goal_id="goal-a",
    )
    assert canonical is not None
    by_id = {todo["todo_id"]: todo for todo in canonical["todos"]}
    assert by_id["todo_complete_native"]["status"] == "done"
    assert by_id["todo_complete_native"]["successor_todo_ids"] == [successor_id]
    assert by_id[successor_id]["claimed_by"] == "agent-b"
    assert by_id["todo_supersede_native"]["status"] == "done"
    assert by_id["todo_supersede_native"]["superseded_by"] in by_id

    runtime_calls.clear()
    archived = archive_completed_todos(
        registry_path=registry_path,
        runtime_root_arg=str(runtime_root),
        goal_id="goal-a",
        role="agent",
        max_active_done=0,
        dry_run=False,
    )
    assert archived["status"] == "applied"
    assert archived["moved_count"] == 2
    assert archived["projection_delivery"] == "delivered"
    assert runtime_calls == [
        "coordination.local_authority.todo_list",
        "coordination.local_authority.todo_archive",
        "coordination.local_authority.todo_list",
        "coordination.local_authority.todo_archive_ack",
    ]
    canonical_after_archive = read_canonical_todos_if_promoted(
        runtime_root=runtime_root,
        goal_id="goal-a",
    )
    assert canonical_after_archive is not None
    archived_ids = {
        todo["todo_id"]
        for todo in canonical_after_archive["todos"]
        if todo["archive_state"] == "archive"
    }
    assert archived_ids == {"todo_complete_native", "todo_supersede_native"}
    archive_revision = canonical_after_archive["provider_revision"]
    for _ in range(2):
        runtime_calls.clear()
        no_change = archive_completed_todos(
            registry_path=registry_path,
            runtime_root_arg=str(runtime_root),
            goal_id="goal-a",
            role="agent",
            max_active_done=0,
            dry_run=False,
        )
        assert no_change["status"] == "no_change"
        assert no_change["changed"] is False
        assert no_change["moved_count"] == 0
        assert no_change["provider_revision"] == archive_revision
        assert runtime_calls == [
            "coordination.local_authority.todo_list",
            "coordination.local_authority.todo_archive",
            "coordination.local_authority.todo_list",
        ]
        unchanged = read_canonical_todos_if_promoted(
            runtime_root=runtime_root,
            goal_id="goal-a",
        )
        assert unchanged is not None
        assert unchanged["provider_revision"] == archive_revision
    assert archive_operation_ids[-2] == archive_operation_ids[-1]
    assert archive_operation_ids[0] != archive_operation_ids[-1]
    rendered = state_file.read_text(encoding="utf-8")
    assert "Human narrative remains outside canonical Todo authority." in rendered
    assert "Continue provider-first delivery." in rendered


def test_public_terminal_optional_prose_matches_before_and_after_promotion(
    tmp_path: Path,
) -> None:
    cases = [
        ("note", None, None),
        ("note", "", None),
        ("note", " \u0085 ", None),
        ("note", "ordinary note", "ordinary note"),
        ("note", " first\u0085  second ", "first second"),
        ("evidence", None, None),
        ("evidence", "", None),
        ("evidence", " \u0085 ", None),
        ("evidence", "ordinary evidence", "ordinary evidence"),
        ("evidence", " first\u0085  second ", "first second"),
        ("reason", None, None),
        ("reason", "", None),
        ("reason", " \u0085 ", None),
        ("reason", "ordinary reason", "ordinary reason"),
        ("reason", " first\u0085  second ", "first second"),
    ]

    def exercise(root: Path, *, promoted: bool) -> dict[str, object]:
        runtime_root = root / "runtime"
        project = root / "project"
        state_file = project / "ACTIVE_GOAL_STATE.md"
        project.mkdir(parents=True)
        records: list[dict[str, object]] = []
        todo_lines: list[str] = []
        for index, (field, _value, _expected) in enumerate(cases, start=1):
            todo_id = f"todo_prose_{index:02d}"
            text = f"Exercise optional {field} case {index}"
            metadata = format_todo_metadata_line(
                todo_id=todo_id,
                status="open",
                task_class="advancement_task",
                claimed_by="agent-a",
            )
            todo_lines.extend([f"- [ ] {text}", str(metadata)])
            records.append(
                {
                    "schema_version": "todo_item_v0",
                    "index": index,
                    "done": False,
                    "text": text,
                    "todo_id": todo_id,
                    "role": "agent",
                    "status": "open",
                    "archive_state": "active",
                    "source_section": TODO_SECTION_HEADINGS["agent"],
                    "task_class": "advancement_task",
                    "claimed_by": "agent-a",
                }
            )
        state_file.write_text(
            "# Goal\n\n## User Todo / Owner Review Reading Queue\n\n"
            "## Agent Todo\n\n" + "\n".join(todo_lines) +
            "\n\n## Completed Work Archive\n",
            encoding="utf-8",
        )
        registry_path = root / "registry.json"
        registry_path.write_text(
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
                            "coordination": {
                                "agent_model": "peer_v1",
                                "registered_agents": ["agent-a"],
                            },
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        if promoted:
            initialize_canonical_authority(
                runtime_root,
                "goal-a",
                build_todo_runtime_shadow_projection(
                    goal_id="goal-a",
                    todos=records,
                    handoff_mode="soft_claim",
                ),
                state_path=state_file,
            )

        for index, (field, value, _expected) in enumerate(cases, start=1):
            common = {
                "registry_path": registry_path,
                "runtime_root_arg": str(runtime_root),
                "goal_id": "goal-a",
                "todo_id": f"todo_prose_{index:02d}",
                "role": "agent",
                "agent_id": "agent-a",
            }
            if field == "reason":
                result = supersede_goal_todo(**common, reason=value)
                assert result["superseded"] is True
            else:
                result = complete_goal_todo(
                    **common,
                    claimed_by="agent-a",
                    no_followup=True,
                    **{field: value},
                )
                assert result["completed"] is True

        if promoted:
            projection = read_canonical_todos_if_promoted(
                runtime_root=runtime_root,
                goal_id="goal-a",
            )
            assert projection is not None
            todos = projection["todos"]
        else:
            lines = state_file.read_text(encoding="utf-8").splitlines()
            bounds = section_bounds(lines, "agent")
            assert bounds is not None
            todos = todo_blocks(
                lines,
                bounds[0],
                bounds[1],
                role="agent",
                source_section=bounds[2],
            )
        by_id = {todo["todo_id"]: todo for todo in todos}
        return {
            f"{field}:{index}": by_id[f"todo_prose_{index:02d}"].get(field)
            for index, (field, _value, _expected) in enumerate(cases, start=1)
        }

    legacy = exercise(tmp_path / "legacy", promoted=False)
    canonical = exercise(tmp_path / "canonical", promoted=True)
    expected = {
        f"{field}:{index}": value
        for index, (field, _input, value) in enumerate(cases, start=1)
    }
    assert legacy == expected
    assert canonical == expected


def test_illegal_terminal_actor_is_a_domain_valueerror_before_and_after_promotion(
    tmp_path: Path,
) -> None:
    def rejected(root: Path, *, promoted: bool) -> ValueError:
        runtime_root = root / "runtime"
        project = root / "project"
        state_file = project / "ACTIVE_GOAL_STATE.md"
        project.mkdir(parents=True)
        metadata = format_todo_metadata_line(
            todo_id="todo_terminal_actor",
            status="open",
            task_class="advancement_task",
            claimed_by="agent-a",
        )
        state_file.write_text(
            "# Goal\n\n## User Todo / Owner Review Reading Queue\n\n"
            "## Agent Todo\n\n- [ ] Reject an illegal terminal actor\n"
            f"{metadata}\n\n## Completed Work Archive\n",
            encoding="utf-8",
        )
        registry_path = root / "registry.json"
        registry_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "common_runtime_root": str(runtime_root),
                    "goals": [
                        {
                            "id": "goal-a",
                            "repo": str(project),
                            "state_file": state_file.name,
                            "coordination": {
                                "agent_model": "peer_v1",
                                "registered_agents": ["agent-a"],
                            },
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        if promoted:
            initialize_canonical_authority(
                runtime_root,
                "goal-a",
                build_todo_runtime_shadow_projection(
                    goal_id="goal-a",
                    handoff_mode="soft_claim",
                    todos=[
                        {
                            "schema_version": "todo_item_v0",
                            "index": 1,
                            "done": False,
                            "text": "Reject an illegal terminal actor",
                            "todo_id": "todo_terminal_actor",
                            "role": "agent",
                            "status": "open",
                            "archive_state": "active",
                            "source_section": TODO_SECTION_HEADINGS["agent"],
                            "task_class": "advancement_task",
                            "claimed_by": "agent-a",
                        }
                    ],
                ),
                state_path=state_file,
            )
        with pytest.raises(ValueError) as exc_info:
            complete_goal_todo(
                registry_path=registry_path,
                runtime_root_arg=str(runtime_root),
                goal_id="goal-a",
                todo_id="todo_terminal_actor",
                role="agent",
                claimed_by="agent-a",
                agent_id="agent-b",
                no_followup=True,
            )
        return exc_info.value

    legacy = rejected(tmp_path / "legacy", promoted=False)
    canonical = rejected(tmp_path / "canonical", promoted=True)
    assert not isinstance(legacy, LocalCoordinationAuthorityUnavailable)
    assert isinstance(canonical, LocalCoordinationAuthorityRejection)
    assert canonical.code == "actor_not_registered"


@pytest.mark.parametrize(
    "reason_code",
    [
        "version_mismatch",
        "lease_cas_mismatch",
        "actor_not_registered",
        "handoff_mode_requires_lease",
    ],
)
def test_promoted_terminal_rejection_code_survives_public_python_facade(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    reason_code: str,
) -> None:
    runtime_root = tmp_path / "runtime"
    project = tmp_path / "project"
    state_file = project / "ACTIVE_GOAL_STATE.md"
    project.mkdir()
    state_file.write_text("# Goal\n\n## Agent Todo\n", encoding="utf-8")
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(runtime_root),
                "goals": [
                    {
                        "id": "goal-a",
                        "repo": str(project),
                        "state_file": state_file.name,
                        "coordination": {"registered_agents": ["agent-a"]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    projection = build_todo_runtime_shadow_projection(
        goal_id="goal-a",
        todos=[
            {
                "schema_version": "todo_item_v0",
                "index": 1,
                "done": False,
                "text": "Complete through the promoted provider",
                "todo_id": "todo_terminal",
                "role": "agent",
                "status": "open",
                "archive_state": "active",
                "source_section": TODO_SECTION_HEADINGS["agent"],
                "task_class": "advancement_task",
                "claimed_by": "agent-a",
            }
        ],
        handoff_mode="soft_claim",
    )
    initialize_canonical_authority(
        runtime_root, "goal-a", projection, state_path=state_file
    )

    monkeypatch.setattr(
        "loopx.control_plane.todos.provider_terminal_lifecycle.effect_runtime_result",
        lambda *_args, **_kwargs: {
            "schema_version": "loopx_coordination_todo_terminal_lifecycle_result_v0",
            "status": "failed",
            "changed": False,
            "failure_kind": "decision_rejection",
            "reason_code": reason_code,
            "reason": f"terminal request rejected: {reason_code}",
            "source_authority": "file_v0",
            "decision_read_from_provider": True,
            "legacy_fallback_used": False,
        },
    )

    with pytest.raises(LocalCoordinationAuthorityRejection) as exc_info:
        complete_goal_todo(
            registry_path=registry_path,
            goal_id="goal-a",
            todo_id="todo_terminal",
            claimed_by="agent-a",
            agent_id="agent-a",
            no_followup=True,
        )
    assert exc_info.value.code == reason_code
    assert exc_info.value.payload["reason_code"] == reason_code


def test_promoted_terminal_retry_reuses_receipt_after_projection_crash(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    project = tmp_path / "project"
    state_file = project / "ACTIVE_GOAL_STATE.md"
    project.mkdir()
    state_file.write_text("# Goal\n\n## Agent Todo\n", encoding="utf-8")
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(runtime_root),
                "goals": [
                    {
                        "id": "goal-a",
                        "repo": str(project),
                        "state_file": state_file.name,
                        "coordination": {"registered_agents": ["agent-a"]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    projection = build_todo_runtime_shadow_projection(
        goal_id="goal-a",
        todos=[
            {
                "schema_version": "todo_item_v0",
                "index": 1,
                "done": False,
                "text": "Complete once despite a projection crash",
                "todo_id": "todo_terminal",
                "role": "agent",
                "status": "open",
                "archive_state": "active",
                "source_section": TODO_SECTION_HEADINGS["agent"],
                "task_class": "advancement_task",
                "claimed_by": "agent-a",
            }
        ],
        handoff_mode="soft_claim",
    )
    initialize_canonical_authority(
        runtime_root, "goal-a", projection, state_path=state_file
    )
    runtime_calls: list[str] = []
    original_effect_runtime_result = provider_terminal_lifecycle.effect_runtime_result
    original_authority_runtime_result = local_authority_module.effect_runtime_result

    def count_runtime_call(method: str, params: dict[str, object]) -> object:
        runtime_calls.append(method)
        return original_effect_runtime_result(method, params)

    def count_authority_runtime_call(
        method: str, params: dict[str, object]
    ) -> object:
        runtime_calls.append(method)
        return original_authority_runtime_result(method, params)

    monkeypatch.setattr(
        provider_terminal_lifecycle,
        "effect_runtime_result",
        count_runtime_call,
    )
    monkeypatch.setattr(
        local_authority_module,
        "effect_runtime_result",
        count_authority_runtime_call,
    )
    original_settle = provider_projection.settle_canonical_todo_projection

    def _crash_projection(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise OSError("injected projection delivery crash")

    # The adapter imports this symbol directly, so fail only the compatibility
    # delivery after the canonical transaction has committed.
    monkeypatch.setattr(
        "loopx.control_plane.todos.provider_terminal_lifecycle.settle_canonical_todo_projection",
        _crash_projection,
    )
    request = {
        "registry_path": registry_path,
        "goal_id": "goal-a",
        "todo_id": "todo_terminal",
        "claimed_by": "agent-a",
        "agent_id": "agent-a",
        "next_agent_todo": "Continue after the recovered projection.",
        "next_claimed_by": "agent-a",
        "next_task_class": "advancement_task",
    }
    with pytest.raises(OSError, match="projection delivery crash"):
        complete_goal_todo(**request)
    assert runtime_calls == [
        "coordination.local_authority.todo_list",
        "coordination.local_authority.todo_terminal",
    ]

    monkeypatch.setattr(
        "loopx.control_plane.todos.provider_terminal_lifecycle.settle_canonical_todo_projection",
        original_settle,
    )
    replay = complete_goal_todo(**request)
    assert replay["status"] == "done"
    assert replay["provider_status"] == "replayed"
    assert replay["idempotent_replay"] is True
    assert runtime_calls == [
        "coordination.local_authority.todo_list",
        "coordination.local_authority.todo_terminal",
        "coordination.local_authority.todo_list",
        "coordination.local_authority.todo_terminal",
        "coordination.local_authority.todo_list",
    ]
    canonical = read_canonical_todos_if_promoted(
        runtime_root=runtime_root, goal_id="goal-a"
    )
    assert canonical is not None
    successors = [
        todo
        for todo in canonical["todos"]
        if todo["todo_id"] != "todo_terminal"
    ]
    assert len(successors) == 1
    assert successors[0]["text"] == "Continue after the recovered projection."

    prose_replay = complete_goal_todo(
        **request,
        note="Retry with a clearer explanation.",
        evidence="Public retry evidence may be enriched.",
    )
    assert prose_replay["provider_status"] == "replayed"
    assert prose_replay["idempotent_replay"] is True

    with pytest.raises(LocalCoordinationAuthorityRejection) as exc_info:
        complete_goal_todo(
            **{
                **request,
                "next_agent_todo": "Start a genuinely different continuation.",
            }
        )
    assert exc_info.value.code == "coordination_operation_identity_mismatch"


def test_real_canonical_provider_preserves_complete_complex_todo_semantics(
    tmp_path: Path,
) -> None:
    """Preserve the full record through a real already canonical provider."""

    runtime_root = tmp_path / "runtime"
    project = tmp_path / "project"
    state_file = project / ".codex/goals/goal-a/ACTIVE_GOAL_STATE.md"
    state_file.parent.mkdir(parents=True)
    state_file.write_text("# Goal\n", encoding="utf-8")
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(runtime_root),
                "goals": [
                    {
                        "id": "goal-a",
                        "status": "active",
                        "repo": str(project),
                        "state_file": ".codex/goals/goal-a/ACTIVE_GOAL_STATE.md",
                        "coordination": {"registered_agents": ["agent-a", "agent-b"]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    complex_todo = {
        "schema_version": "todo_item_v0",
        "index": 7,
        "done": False,
        "text": "Qualify provider cutover on a complex Goal",
        "title": "Provider semantic parity",
        "todo_id": "todo_complex",
        "role": "agent",
        "status": "deferred",
        "priority": "P0",
        "archive_state": "active",
        "source_section": TODO_SECTION_HEADINGS["agent"],
        "task_class": "continuous_monitor",
        "action_kind": "monitor",
        "task_domain": "control_plane",
        "task_repository": "loopx",
        "continuation_policy": "continue_goal",
        "claimed_by": "agent-a",
        "excluded_agents": ["agent-b"],
        "resume_when": "material_change",
        "resume_ready": False,
        "cadence": "weekly",
        "next_due_at": "2026-09-07T09:00:00+08:00",
        "expires_at": "2026-10-01T00:00:00+08:00",
        "watch_only": True,
        "material_change": False,
        "material_change_generation": 4,
        "consecutive_no_change": 2,
        "max_no_change_before_replan": 3,
        "successor_todo_ids": ["todo_successor"],
        "note": "keep operator context",
        "evidence": "semantic fixture evidence",
        "updated_at": "2026-09-04T10:00:00+08:00",
    }
    successor = {
        "schema_version": "todo_item_v0",
        "index": 8,
        "done": True,
        "text": "Preserve completion semantics",
        "title": "Completion evidence",
        "todo_id": "todo_successor",
        "role": "agent",
        "status": "done",
        "priority": "P1",
        "archive_state": "active",
        "source_section": TODO_SECTION_HEADINGS["agent"],
        "completion_continuation": "no_followup",
        "completed_at": "2026-09-03T18:00:00+08:00",
        "completion_turn_key": "turn-complete",
    }
    claimable = {
        "schema_version": "todo_item_v0",
        "index": 9,
        "done": False,
        "text": "Claim directly against the promoted provider head",
        "todo_id": "todo_claimable",
        "role": "agent",
        "status": "open",
        "archive_state": "active",
        "source_section": TODO_SECTION_HEADINGS["agent"],
        "priority": "P0",
        "action_kind": "implement",
        "note": "this complete record must survive the claim",
    }
    projection = build_todo_runtime_shadow_projection(
        goal_id="goal-a",
        todos=[complex_todo, successor, claimable],
        handoff_mode="soft_claim",
    )
    initialize_canonical_authority(runtime_root, "goal-a", projection, state_path=state_file)

    state_file.unlink()
    result = list_goal_todos(registry_path=registry_path, goal_id="goal-a")
    by_id = {item["todo_id"]: item for item in result["todos"]}
    for field in (
        "text",
        "title",
        "priority",
        "source_section",
        "archive_state",
        "continuation_policy",
        "resume_when",
        "cadence",
        "next_due_at",
        "expires_at",
        "watch_only",
        "material_change_generation",
        "successor_todo_ids",
        "note",
        "evidence",
    ):
        assert by_id["todo_complex"][field] == complex_todo[field]
    assert by_id["todo_successor"]["completed_at"] == successor["completed_at"]
    assert by_id["todo_successor"]["completion_continuation"] == "no_followup"
    assert result["authority_read"]["todo_read_model"]["todo_count"] == 3

    # The public compatibility CLI must retain a claim-neutral text correction
    # and the existing structured priority after promotion; it must not
    # reconstruct or write the Markdown source.
    correction_command = [
        sys.executable,
        "-m",
        "loopx.cli",
        "--format",
        "json",
        "--registry",
        str(registry_path),
        "todo",
        "update",
        "--goal-id",
        "goal-a",
        "--todo-id",
        "todo_claimable",
        "--agent-id",
        "agent-b",
        "--text",
        "Corrected before claiming",
    ]
    correction = subprocess.run(
        correction_command, capture_output=True, text=True, check=True, timeout=30
    )
    assert json.loads(correction.stdout)["ok"] is True
    corrected = list_goal_todos(registry_path=registry_path, goal_id="goal-a")
    corrected_item = next(
        item for item in corrected["todos"] if item["todo_id"] == "todo_claimable"
    )
    assert corrected_item["text"] == "[P0] Corrected before claiming"
    assert not corrected_item.get("claimed_by")
    assert corrected_item["last_actor_agent_id"] == "agent-b"
    assert not state_file.exists()

    # A note-only CLI edit must survive an independent provider readback too;
    # accepting the option or exercising the text branch cannot prove this.
    note_command = [*correction_command[:-2], "--note", "Correction context"]
    note_result = subprocess.run(
        note_command, capture_output=True, text=True, check=True, timeout=30
    )
    assert json.loads(note_result.stdout)["ok"] is True
    noted = list_goal_todos(registry_path=registry_path, goal_id="goal-a")
    noted_item = next(
        item for item in noted["todos"] if item["todo_id"] == "todo_claimable"
    )
    assert noted_item["note"] == "Correction context"
    assert noted_item["text"] == "[P0] Corrected before claiming"
    assert not noted_item.get("claimed_by")
    assert not state_file.exists()

    claim_command = [
        sys.executable,
        "-m",
        "loopx.cli",
        "--format",
        "json",
        "--registry",
        str(registry_path),
        "todo",
        "claim",
        "--goal-id",
        "goal-a",
        "--todo-id",
        "todo_claimable",
        "--claimed-by",
        "agent-a",
        "--agent-id",
        "agent-a",
        "--claim-operation-id",
        "initial-cli-claim",
    ]
    # Duplicate callers race from separate processes, but one operation must
    # produce exactly one accepted claim and the same durable receipt.
    with ThreadPoolExecutor(max_workers=2) as pool:
        attempts = [
            pool.submit(
                subprocess.run,
                claim_command,
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
            for _ in range(2)
        ]
        claims = [json.loads(attempt.result().stdout) for attempt in attempts]
    assert sum(item["status"] == "applied" for item in claims) == 1
    assert all(
        item["status"] in {"applied", "recovered", "replayed"} for item in claims
    )
    assert claims[0]["original_receipt"] == claims[1]["original_receipt"]
    assert claims[0]["provider_revision"] == claims[1]["provider_revision"]
    claimed = next(item for item in claims if item["status"] == "applied")
    assert claimed["ok"] is True
    assert claimed["source_authority"] == "file_v0"
    assert claimed["legacy_fallback_used"] is False
    assert claimed["mutation_authority"]["mode"] == "registered_peer_actor"

    after_claim = list_goal_todos(registry_path=registry_path, goal_id="goal-a")
    claimed_item = next(
        item for item in after_claim["todos"] if item["todo_id"] == "todo_claimable"
    )
    assert claimed_item["claimed_by"] == "agent-a"

    # Separate CLI processes must replay one durable operation, not mint a
    # fresh receipt for every retry. Preview does not consume that identity.
    claim_command = [*claim_command[:-1], "retryable-cli-claim"]
    preview = json.loads(
        subprocess.run(
            [*claim_command, "--dry-run"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    assert preview["dry_run"] is True
    original = json.loads(
        subprocess.run(
            claim_command,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    replay = json.loads(
        subprocess.run(
            claim_command,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    assert original["status"] == "no_change"
    assert replay["status"] == "replayed"
    assert replay["original_receipt"] == original["original_receipt"]
    assert replay["provider_revision"] == original["provider_revision"]
    changed_intent = [
        "agent-b" if part == "agent-a" else part for part in claim_command
    ]
    rejected = subprocess.run(
        changed_intent, capture_output=True, text=True, check=False
    )
    assert rejected.returncode != 0
    assert (
        json.loads(rejected.stdout)["error"]
        == "operation id already names a different coordination request"
    )
    for invalid_key in ("", " padded-operation "):
        invalid = subprocess.run(
            [*claim_command[:-1], invalid_key],
            capture_output=True,
            text=True,
            check=False,
        )
        assert invalid.returncode != 0
    assert not state_file.exists()

    create_command = [
        sys.executable,
        "-m",
        "loopx.cli",
        "--format",
        "json",
        "--registry",
        str(registry_path),
        "todo",
        "add",
        "--goal-id",
        "goal-a",
        "--role",
        "agent",
        "--text",
        "Create directly against promoted provider",
        "--claimed-by",
        "agent-a",
        "--task-class",
        "advancement_task",
        "--action-kind",
        "implement",
    ]
    create_preview = subprocess.run(
        [*create_command, "--dry-run"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(create_preview.stdout)["status"] == "planned"
    assert not state_file.exists()
    created = json.loads(
        subprocess.run(
            create_command,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    assert created["ok"] is True
    assert created["source_authority"] == "file_v0"
    assert created["legacy_fallback_used"] is False
    assert not state_file.exists()

    after_create = list_goal_todos(registry_path=registry_path, goal_id="goal-a")
    created_item = next(
        item for item in after_create["todos"] if item["todo_id"] == created["todo_id"]
    )
    assert created_item["text"] == "Create directly against promoted provider"
    assert created_item["claimed_by"] == "agent-a"
    assert after_create["authority_read"]["todo_read_model"]["todo_count"] == 4
    assert claimed_item["note"] == "Correction context"

    # Real CLI, no Markdown file: provider data feeds an in-memory editor and
    # only requested fields return through TS CAS. Complex sibling fields do
    # not round-trip through the lossy Markdown representation.
    command = [
        sys.executable,
        "-m",
        "loopx.cli",
        "--format",
        "json",
        "--registry",
        str(registry_path),
        "todo",
        "update",
        "--goal-id",
        "goal-a",
        "--todo-id",
        "todo_claimable",
        "--agent-id",
        "agent-a",
        "--text",
        "Edit provider-owned work",
        "--note",
        "compatibility edit",
    ]
    preview = subprocess.run(
        [*command, "--dry-run"], capture_output=True, text=True, check=True
    )
    assert json.loads(preview.stdout)["status"] == "planned"
    assert (
        list_goal_todos(registry_path=registry_path, goal_id="goal-a")["todos"]
        == after_create["todos"]
    )
    edited = subprocess.run(command, capture_output=True, text=True, check=True)
    edit_result = json.loads(edited.stdout)
    assert edit_result["status"] == "applied"
    assert edit_result["projection_delivery"] == "pending"
    assert not state_file.exists()
    after_edit = list_goal_todos(registry_path=registry_path, goal_id="goal-a")
    edited_by_id = {item["todo_id"]: item for item in after_edit["todos"]}
    assert edited_by_id["todo_claimable"] == {
        **claimed_item,
        "text": "[P0] Edit provider-owned work",
        "title": "Edit provider-owned work",
        "note": "compatibility edit",
        "last_actor_agent_id": "agent-a",
        "updated_at": edited_by_id["todo_claimable"]["updated_at"],
    }
    assert edited_by_id["todo_claimable"]["updated_at"] != claimed_item["updated_at"]
    assert edited_by_id["todo_complex"] == by_id["todo_complex"]
    assert edited_by_id["todo_successor"] == by_id["todo_successor"]


@pytest.mark.parametrize(
    ("reason_code", "reason"),
    [
        (
            "coordination_operation_identity_mismatch",
            "operation id names a different coordination request",
        ),
        (
            "invalid_coordination_projection",
            "canonical projection could not be decoded",
        ),
        (
            "invalid_coordination_todo_claim_receipt",
            "stored claim receipt could not be verified",
        ),
    ],
)
def test_promoted_claim_protocol_failures_stay_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    reason_code: str,
    reason: str,
) -> None:
    """Protocol and storage-integrity failures are outages, not decisions."""

    _engage_fence(tmp_path)

    def _protocol_failure(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {
            "schema_version": "loopx_coordination_todo_claim_result_v0",
            "status": "failed",
            "failure_kind": "protocol_failure",
            "reason_code": reason_code,
            "reason": reason,
        }

    monkeypatch.setattr(
        "loopx.control_plane.coordination.local_authority.effect_runtime_result",
        _protocol_failure,
    )
    with pytest.raises(LocalCoordinationAuthorityUnavailable) as exc_info:
        claim_canonical_todo_if_promoted(
            registry_path=_claim_registry(tmp_path),
            runtime_root=tmp_path,
            goal_id="goal-a",
            todo_id="todo_x1",
            role="agent",
            claimed_by="agent-a",
            actor_agent_id="agent-a",
            dry_run=False,
        )
    assert not isinstance(exc_info.value, ValueError)
    assert exc_info.value.code == reason_code


def test_protocol_failure_kind_absent_means_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A legacy failure result without the kind field is an outage, not a decision."""

    _engage_fence(tmp_path)

    def _legacy_failure(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {
            "schema_version": "loopx_coordination_todo_claim_result_v0",
            "status": "failed",
            "reason_code": "todo_not_open",
            "reason": "todo claim requires status=open",
        }

    monkeypatch.setattr(
        "loopx.control_plane.coordination.local_authority.effect_runtime_result",
        _legacy_failure,
    )
    with pytest.raises(LocalCoordinationAuthorityUnavailable) as exc_info:
        claim_canonical_todo_if_promoted(
            registry_path=_claim_registry(tmp_path),
            runtime_root=tmp_path,
            goal_id="goal-a",
            todo_id="todo_a",
            role="agent",
            claimed_by="agent-a",
            actor_agent_id="agent-a",
            dry_run=False,
        )
    assert not isinstance(exc_info.value, ValueError)


def test_hard_lease_eligibility_rejection_is_valueerror(
    tmp_path: Path,
) -> None:
    """A real hard-lease Todo without a lease rejects with a usable repair path.

    The unpromoted path raises TaskLeaseError (a ValueError) when a hard-lease
    Todo has no matching active lease; the promoted path must keep that caller
    contract and leave the canonical state untouched. The canonical recovery
    operation is to supply a task-lease idempotency key to acquire the lease
    atomically with the claim.
    """

    _seed_promoted_store(tmp_path, handoff_mode="hard_lease")
    registry_path = _claim_registry(tmp_path)
    store_head = json.loads(
        (
            tmp_path / "authority" / "file-v0" / "authority-store-bf21e67b01a351a1.json"
        ).read_text(encoding="utf-8")
    )
    head_before = json.dumps(store_head.get("head", {}), sort_keys=True)
    with pytest.raises(ValueError) as exc_info:
        claim_canonical_todo_if_promoted(
            registry_path=registry_path,
            runtime_root=tmp_path,
            goal_id="goal-a",
            todo_id="todo_a",
            role="agent",
            claimed_by="agent-a",
            actor_agent_id="agent-a",
            dry_run=False,
        )
    assert isinstance(exc_info.value, LocalCoordinationAuthorityRejection)
    assert exc_info.value.code == "handoff_mode_requires_lease"
    assert (
        "hard_lease Todo claim requires an active canonical lease held by the claiming agent"
        in str(exc_info.value)
    )
    assert "loopx todo claim --task-lease-idempotency-key" in str(exc_info.value)
    assert "--task-lease-expected-version" in str(exc_info.value)
    assert (
        exc_info.value.payload.get("recovery", {}).get("requires_flags")
        == ["--task-lease-idempotency-key"]
    )
    store_after = json.loads(
        (
            tmp_path / "authority" / "file-v0" / "authority-store-bf21e67b01a351a1.json"
        ).read_text(encoding="utf-8")
    )
    assert json.dumps(store_after.get("head", {}), sort_keys=True) == head_before

    # Canonical recovery path: supply task_lease_idempotency_key to acquire
    # the lease atomically during claim.
    recovered = claim_canonical_todo_if_promoted(
        registry_path=registry_path,
        runtime_root=tmp_path,
        goal_id="goal-a",
        todo_id="todo_a",
        role="agent",
        claimed_by="agent-a",
        actor_agent_id="agent-a",
        dry_run=False,
        task_lease_idempotency_key="turn:claim-and-acquire",
        task_lease_expected_version=0,
    )
    assert recovered is not None and recovered["status"] == "applied"
    assert recovered["changed"] is True
    assert recovered["lease"]["owner"] == "agent-a"
    assert recovered["lease"]["status"] == "active"
    assert recovered["lease"]["idempotency_key"] == "turn:claim-and-acquire"

    # With the active lease now persisted, subsequent claims succeed without
    # requiring a new lease request.
    subsequent = claim_canonical_todo_if_promoted(
        registry_path=registry_path,
        runtime_root=tmp_path,
        goal_id="goal-a",
        todo_id="todo_a",
        role="agent",
        claimed_by="agent-a",
        actor_agent_id="agent-a",
        dry_run=False,
    )
    assert subsequent is not None
    assert subsequent["status"] in {"applied", "no_change", "replayed"}
