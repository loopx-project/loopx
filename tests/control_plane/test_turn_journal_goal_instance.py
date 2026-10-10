from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from loopx.control_plane.effect_runtime import EffectRuntimeConflict
from loopx.control_plane.goals.first_party_host_admission import (
    FirstPartyHostGoalAdmission,
)
from loopx.control_plane.goals.source_session_registry_state import guard_path
from loopx.control_plane.projects.registry_codec import (
    source_session_registry_transaction,
)
from loopx.control_plane.turn_driver.journal_store import (
    write_turn_journal_checkpoint,
)
from loopx.file_lock import exclusive_cross_runtime_file_lock


INSTANCE_A = "ginst_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
INSTANCE_B = "ginst_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
TURN_A = f"sha256:{'a' * 64}"
TURN_B = f"sha256:{'b' * 64}"


def _registry_payload(path: Path, instance_id: str) -> dict[str, Any]:
    return {
        "schema_version": "0.2",
        "registry_role": "project-local",
        "profile_id": "source_session_v1",
        "common_runtime_root": str(path.parent),
        "projects": [],
        "goals": [
            {
                "id": "fixture-goal",
                "goal_instance_id": instance_id,
                "status": "active",
                "execution_authority": False,
            }
        ],
        "session_bindings": [],
        "session_receipts": [],
        "lifetime_receipts": [],
        "retired_goal_instances": [],
    }


def _write_registry(path: Path, instance_id: str) -> None:
    expected = _registry_payload(path, instance_id)
    create = None if path.exists() else lambda: expected
    with source_session_registry_transaction(
        path,
        operation="turn_journal_goal_instance_test",
        create=create,
    ) as transaction:
        payload = transaction.payload_copy()
        payload["goals"] = expected["goals"]
        transaction.commit(payload)


def _replace_goal(path: Path, instance_id: str) -> None:
    with exclusive_cross_runtime_file_lock(
        guard_path(path, "fixture-goal"),
        operation="turn_journal_goal_instance_test_recreate",
    ):
        _write_registry(path, instance_id)


def _journal(
    instance_id: str | None,
    turn_key: str,
    *,
    completed_phases: list[str] | None = None,
) -> dict[str, Any]:
    effect_id = (
        f"fixture-goal:fixture-agent:todo_fixture0001:{turn_key}"
    )
    transaction: dict[str, Any] = {
        "turn_key": turn_key,
        "settlement_plan": {
            "schema_version": "quota_settlement_plan_v1",
            "identity": {
                "schema_version": "quota_settlement_identity_v0",
                "effect_id": effect_id,
                "goal_id": "fixture-goal",
                "agent_id": "fixture-agent",
                "todo_id": "todo_fixture0001",
                "turn_instance_id": turn_key,
            },
        },
    }
    plan: dict[str, Any] = {
        "turn_envelope": {
            "goal_id": "fixture-goal",
            "agent_id": "fixture-agent",
            "action": {"selected_todo": {"todo_id": "todo_fixture0001"}},
        },
        "transaction": transaction,
    }
    if instance_id is not None:
        goal_ref = {
            "goal_id": "fixture-goal",
            "goal_instance_id": instance_id,
        }
        plan["goal_ref"] = goal_ref
        transaction["goal_ref"] = goal_ref
    return {
        "schema_version": "loopx_turn_journal_v0",
        "goal_id": "fixture-goal",
        "turn_key": turn_key,
        "status": "in_progress",
        "completed_phases": completed_phases or [],
        "plan": plan,
    }


def _commit_source(
    path: Path,
    journal: dict[str, Any],
    admission: FirstPartyHostGoalAdmission,
) -> None:
    with admission.source_journal_admission(
        runtime_root=path.parents[3],
    ) as source_admission:
        assert source_admission is not None
        write_turn_journal_checkpoint(
            path,
            journal,
            source_admission=source_admission,
        )


def test_source_journal_commit_is_fenced_by_current_exact_goal_ref(
    tmp_path: Path,
) -> None:
    registry = tmp_path / "project" / ".loopx" / "registry.json"
    runtime = tmp_path / "runtime" / "goals" / "fixture-goal" / "turns"
    path_a = runtime / f"{'a' * 64}.json"
    path_b = runtime / f"{'b' * 64}.json"
    _write_registry(registry, INSTANCE_A)
    admission_a = FirstPartyHostGoalAdmission.for_plan(
        registry_path=registry,
        goal_id="fixture-goal",
        planned_goal_ref={
            "goal_id": "fixture-goal",
            "goal_instance_id": INSTANCE_A,
        },
    )
    journal_a = _journal(INSTANCE_A, TURN_A)

    _commit_source(path_a, journal_a, admission_a)
    _commit_source(path_a, journal_a, admission_a)
    before_a = path_a.read_bytes()
    assert "source_admission" not in json.loads(before_a)

    _replace_goal(registry, INSTANCE_B)
    advanced_a = copy.deepcopy(journal_a)
    advanced_a["completed_phases"] = ["host_execute", "typed_result"]
    with pytest.raises(EffectRuntimeConflict) as exc_info:
        _commit_source(path_a, advanced_a, admission_a)
    assert exc_info.value.diagnostic_code == "stale_goal_instance"
    assert path_a.read_bytes() == before_a
    assert not path_b.exists()

    journal_b = _journal(INSTANCE_B, TURN_B)
    admission_b = FirstPartyHostGoalAdmission.for_plan(
        registry_path=registry,
        goal_id="fixture-goal",
        planned_goal_ref={
            "goal_id": "fixture-goal",
            "goal_instance_id": INSTANCE_B,
        },
    )
    _commit_source(path_b, journal_b, admission_b)

    assert json.loads(path_a.read_text(encoding="utf-8")) == journal_a
    assert json.loads(path_b.read_text(encoding="utf-8")) == journal_b
    assert not Path(
        f"{guard_path(registry, 'fixture-goal')}.ts-effect.lock"
    ).exists()


def test_legacy_journal_commit_keeps_the_existing_wire_shape(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime" / "legacy.json"
    journal = _journal(None, TURN_A)

    write_turn_journal_checkpoint(path, journal)

    assert json.loads(path.read_text(encoding="utf-8")) == journal
    assert "source_admission" not in path.read_text(encoding="utf-8")
