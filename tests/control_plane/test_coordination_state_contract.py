from __future__ import annotations

import json
from pathlib import Path
import runpy
import subprocess

import pytest

from loopx.control_plane.agents import delivery_workspace
from loopx.control_plane.coordination.coordination_state_contract import (
    CoordinationStateContractError,
    TODO_CANONICAL_READ_RECORD_FIELDS,
    TODO_CANONICAL_REQUIRED_READ_FIELDS,
    canonical_record_fields,
    TODO_DOMAIN_RECORD_FIELDS,
    TODO_DOMAIN_ITEM_SCHEMA_VERSION,
    TODO_PROJECTION_METADATA_FIELDS,
)
from loopx.control_plane.coordination.coordination_state_contract_generated import (
    DELIVERY_BOUNDARY_RESULT_SCHEMA,
    DELIVERY_CONTINUITY_RESULT_SCHEMA,
    DELIVERY_ROUTING_REQUEST_SCHEMA,
    DELIVERY_ROUTING_RESULT_SCHEMA,
    DELIVERY_WORKSPACE_SNAPSHOT_LEGACY_SNAPSHOT_SCHEMA,
    DELIVERY_WORKSPACE_SNAPSHOT_REQUEST_SCHEMA,
    DELIVERY_WORKSPACE_SNAPSHOT_RESULT_SCHEMA,
    DELIVERY_WORKSPACE_SNAPSHOT_SNAPSHOT_SCHEMA,
    LOCAL_AUTHORITY_SHADOW_EVIDENCE_SCHEMA,
    LOCAL_AUTHORITY_SHADOW_OUTBOX_ENTRY_SCHEMA,
    LOCAL_AUTHORITY_SHADOW_REQUEST_SCHEMA,
    LOCAL_COORDINATION_TODO_SNAPSHOT_PAGE_REQUEST_SCHEMA,
    LEGACY_COORDINATION_WRITE_CHECK_REQUEST_SCHEMA,
)
from loopx.control_plane.turn_driver import delivery_continuity
from loopx.control_plane.coordination.coordination_state_contract_generated import LOCAL_AUTHORITY_SHADOW_EVIDENCE_SCHEMA as BRIDGE_SHADOW_EVIDENCE_SCHEMA, LOCAL_AUTHORITY_SHADOW_REQUEST_SCHEMA as BRIDGE_SHADOW_REQUEST_SCHEMA
from loopx.control_plane.coordination.local_authority_shadow_outbox import (
    OUTBOX_ENTRY_SCHEMA,
)
from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)
from loopx.control_plane.coordination.legacy_writer_fence import (
    LEGACY_COORDINATION_WRITE_CHECK_REQUEST_SCHEMA as BRIDGE_WRITE_CHECK_REQUEST_SCHEMA,
)
from loopx.control_plane.coordination.canonical_snapshot import (
    REQUEST_SCHEMA as BRIDGE_LIST_REQUEST_SCHEMA,
)


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("change", ["missing", "unknown", "wrong_type", "duplicate"])
def test_generator_rejects_invalid_protocol_contract(change, tmp_path, monkeypatch) -> None:
    generator = runpy.run_path(str(ROOT / "scripts/generate_coordination_state_contract.py"))
    load = generator["load_contract"]
    contract = load()
    protocol = contract["task_lease_protocol"]
    if change == "missing":
        del protocol["acquire_request_schema"]
    elif change == "unknown":
        protocol["future_schema"] = "loopx_future_v0"
    elif change == "wrong_type":
        protocol["acquire_request_schema"] = True
    else:
        protocol["acquire_request_schema"] = protocol["lifecycle_request_schema"]
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    monkeypatch.setitem(load.__globals__, "CONTRACT_PATH", path)
    with pytest.raises(ValueError, match="task lease protocol"):
        load()


def test_generated_coordination_bindings_are_current() -> None:
    subprocess.run(
        ["python3", "scripts/generate_coordination_state_contract.py", "--check"],
        cwd=ROOT,
        check=True,
    )


@pytest.mark.parametrize(
    ("source_family", "source_key", "message"),
    [
        ("local_authority_protocol", "todo_read_request_schema", "across families"),
        ("local_authority_protocol", "promotion_receipt_schema", "across families"),
        ("runtime_shadow_protocol", "inspect_request_schema", "must be unique"),
        ("local_authority_shadow_protocol", "outbox_entry_schema", "across families"),
        ("task_lease_protocol", "acquire_request_schema", "across families"),
        ("capability_hook_protocol", "intent_schema", "across families"),
        ("replan_settlement_protocol", "result_schema", "across families"),
    ],
)
def test_generator_rejects_protocol_identity_collisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    source_family: str, source_key: str, message: str,
) -> None:
    generator = runpy.run_path(str(ROOT / "scripts/generate_coordination_state_contract.py"))
    contract = json.loads(generator["CONTRACT_PATH"].read_text(encoding="utf-8"))
    contract["runtime_shadow_protocol"]["commit_request_schema"] = contract[source_family][source_key]
    source = tmp_path / "contract.json"
    source.write_text(json.dumps(contract), encoding="utf-8")
    load_contract = generator["load_contract"]
    monkeypatch.setitem(load_contract.__globals__, "CONTRACT_PATH", source)

    with pytest.raises(ValueError, match=message):
        load_contract()


def test_python_binding_equals_language_neutral_contract() -> None:
    raw = json.loads(
        (
            ROOT
            / "loopx/control_plane/coordination/coordination_state_contract_v0.json"
        ).read_text(encoding="utf-8")
    )
    from loopx.control_plane.coordination.coordination_state_contract import (
        COORDINATION_STATE_CONTRACT,
    )

    assert json.loads(json.dumps(COORDINATION_STATE_CONTRACT, default=dict)) == raw


def test_generated_contract_is_deeply_immutable() -> None:
    from loopx.control_plane.coordination.coordination_state_contract import (
        COORDINATION_STATE_CONTRACT,
    )

    with pytest.raises(TypeError):
        COORDINATION_STATE_CONTRACT["schema_version"] = "mutated"
    with pytest.raises(TypeError):
        COORDINATION_STATE_CONTRACT["todo_read_record"]["fields"] = ("mutated",)
    with pytest.raises(TypeError):
        COORDINATION_STATE_CONTRACT["todo_read_record"]["fields"][0] = "mutated"
    assert "archive_state" in TODO_DOMAIN_RECORD_FIELDS


def test_required_fields_are_declared_by_the_record_contract() -> None:
    assert set(TODO_CANONICAL_REQUIRED_READ_FIELDS) <= set(
        TODO_CANONICAL_READ_RECORD_FIELDS
    )


def test_domain_projection_split_keeps_archival_as_a_task_fact() -> None:
    from loopx.control_plane.coordination.local_authority import (
        canonical_todo_summary_fields,
    )
    from loopx.control_plane.todos.succession_warning import project_succession

    assert "archive_state" in TODO_DOMAIN_RECORD_FIELDS
    assert set(TODO_PROJECTION_METADATA_FIELDS) == {"source_section", "index"}
    assert not set(TODO_PROJECTION_METADATA_FIELDS) & set(TODO_DOMAIN_RECORD_FIELDS)
    todo = {
        "schema_version": TODO_DOMAIN_ITEM_SCHEMA_VERSION,
        "todo_id": "todo_native", "role": "agent", "status": "done",
        "done": True, "text": "Keep durable lifecycle semantics",
        "archive_state": "active", "task_class": "advancement_task",
        "claimed_by": "agent-a",
    }
    assert project_succession([todo])[0]["tracked_completion"] is True
    assert project_succession([{**todo, "archive_state": "archive"}])[0]["tracked_completion"] is False
    summary = canonical_todo_summary_fields([todo])
    assert summary["agent_todos"]["source_section"] == "Agent Todo"
    assert "source_section" not in todo and "index" not in todo
    archived = {**todo, "todo_id": "todo_archived", "archive_state": "archive"}
    with_archive = canonical_todo_summary_fields([todo, archived])
    assert with_archive["agent_todos"]["done_count"] == summary["agent_todos"]["done_count"]
    assert with_archive["agent_todos"]["archived_advancement_done_count"] == 1


def test_python_bridge_uses_generated_local_authority_protocol_schemas() -> None:
    assert BRIDGE_LIST_REQUEST_SCHEMA == LOCAL_COORDINATION_TODO_SNAPSHOT_PAGE_REQUEST_SCHEMA


def test_python_shadow_bridges_use_generated_protocol_schemas() -> None:
    assert BRIDGE_SHADOW_REQUEST_SCHEMA == LOCAL_AUTHORITY_SHADOW_REQUEST_SCHEMA
    assert BRIDGE_SHADOW_EVIDENCE_SCHEMA == LOCAL_AUTHORITY_SHADOW_EVIDENCE_SCHEMA
    assert OUTBOX_ENTRY_SCHEMA == LOCAL_AUTHORITY_SHADOW_OUTBOX_ENTRY_SCHEMA
    assert (
        build_todo_runtime_shadow_projection(goal_id="goal_contract", todos=[])[
            "schema_version"
        ]
        == "loopx_coordination_runtime_shadow_projection_v0"
    )
    assert BRIDGE_WRITE_CHECK_REQUEST_SCHEMA == LEGACY_COORDINATION_WRITE_CHECK_REQUEST_SCHEMA


def test_python_delivery_continuity_uses_generated_protocol_schemas() -> None:
    assert delivery_continuity.DELIVERY_CONTINUITY_RESULT_SCHEMA == DELIVERY_CONTINUITY_RESULT_SCHEMA
    assert delivery_continuity.DELIVERY_BOUNDARY_RESULT_SCHEMA == DELIVERY_BOUNDARY_RESULT_SCHEMA
    assert delivery_continuity.DELIVERY_ROUTING_REQUEST_SCHEMA == DELIVERY_ROUTING_REQUEST_SCHEMA
    assert delivery_continuity.DELIVERY_ROUTING_RESULT_SCHEMA == DELIVERY_ROUTING_RESULT_SCHEMA


def test_python_delivery_workspace_uses_generated_protocol_schemas() -> None:
    assert (
        delivery_workspace.DELIVERY_WORKSPACE_SCHEMA_VERSION
        == DELIVERY_WORKSPACE_SNAPSHOT_SNAPSHOT_SCHEMA
    )
    assert (
        delivery_workspace.LEGACY_DELIVERY_WORKSPACE_SCHEMA_VERSION
        == DELIVERY_WORKSPACE_SNAPSHOT_LEGACY_SNAPSHOT_SCHEMA
    )
    assert (
        delivery_workspace.DELIVERY_WORKSPACE_REQUEST_SCHEMA
        == DELIVERY_WORKSPACE_SNAPSHOT_REQUEST_SCHEMA
    )
    assert (
        delivery_workspace.DELIVERY_WORKSPACE_RESULT_SCHEMA
        == DELIVERY_WORKSPACE_SNAPSHOT_RESULT_SCHEMA
    )


def test_record_validation_rejects_required_fields_outside_declared_fields() -> None:
    with pytest.raises(
        CoordinationStateContractError,
        match="required fields are absent from fields: role",
    ):
        canonical_record_fields(
            {"todo_id": "todo_contract"},
            fields=("todo_id",),
            required_fields=("todo_id", "role"),
            label="test record",
            reject_unknown=True,
        )
