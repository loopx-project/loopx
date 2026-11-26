"""Contract, parity, efficiency, and failure-mode tests for subagent topology."""

from __future__ import annotations

import ast
from collections.abc import Mapping
from pathlib import Path
import re
from types import MappingProxyType

import pytest

from loopx.control_plane.turn_driver import (
    subagent_execution_contracts,
    subagent_execution_reconciliation,
    subagent_execution_topology,
)
from loopx.control_plane.turn_driver.subagent_execution_topology import (
    CHILD_CONTEXT_INHERITANCE,
    CHILD_CONTEXT_MODES,
    CHILD_EXECUTION_GUARD_SCHEMA_VERSION,
    CHILD_EXECUTION_REJECTION_SCHEMA_VERSION,
    CHILD_EXECUTION_TASK_PACKET_SCHEMA_VERSION,
    CHILD_FALLBACK_ACTIONS,
    EXECUTION_KINDS,
    HOST_RESULT_FIELDS,
    KNOWN_EFFECT_CLASSES,
    MAX_CHILD_EXECUTION_RECEIPTS,
    MAX_EFFECT_CLASSES,
    MAX_EVIDENCE_REFS,
    OPAQUE_REF_PATTERN,
    RECEIPT_STATUSES,
    SUBAGENT_CONTROL_PLANE_RECONCILIATION_SCHEMA_VERSION,
    SUBAGENT_EXECUTION_TOPOLOGY_SCHEMA_VERSION,
    SUBAGENT_HOST_EXECUTION_RECEIPT_SCHEMA_VERSION,
    ChildExecutionTopologyError,
    _child_execution_lane,
    _child_task_packet,
    _opaque_ref,
    _opaque_refs,
    bind_child_operations_to_topology,
    build_subagent_execution_topology,
    child_execution_receipts_json_schema,
    normalize_subagent_host_execution_receipts,
    observe_subagent_host_result,
    reconcile_subagent_execution,
    subagent_bundle_id,
    subagent_execution_payload_projection,
    subagent_host_request_projection,
    subagent_host_result_fields,
    subagent_lane_id,
)


def _sample_brief(
    *,
    write_scopes: list[str] | None = None,
    validation_declared: bool = True,
    execution_policy: Mapping[str, object] | None = None,
    extra: Mapping[str, object] | None = None,
) -> dict[str, object]:
    brief: dict[str, object] = {
        "child_guard_policy": "prevention_first_v0",
        "objective": "Verify scoped module contract cleanly",
        "action_kind": "verify",
        "target_key": "scope:lane-1",
        "task_domain": "control_plane",
        "task_repository": "loopx",
        "authority_artifact": "artifact:goal-contract",
        "latest_state_ref": "state:head-1",
        "required_capabilities": ["filesystem", "network"],
        "required_write_scopes": [] if write_scopes is None else list(write_scopes),
        "acceptance": ["All unit checks pass", "Zero private paths emitted"],
        "validation_declared": validation_declared,
        "validation_policy": "parent validates output contract",
        "expected_output": "compact_evidence_bundle",
        "execution_policy": (
            {"max_turns": 4, "max_minutes": 5}
            if execution_policy is None
            else dict(execution_policy)
        ),
    }
    if extra:
        brief.update(extra)
    return brief


def _sample_operation(
    todo_id: str = "todo_child001",
    *,
    recommended_context: str = "fresh",
    brief: Mapping[str, object] | None = None,
) -> dict[str, object]:
    return {
        "todo_id": todo_id,
        "recommended_context": recommended_context,
        "available_contexts": ["fresh", "forked_snapshot"],
        "brief": dict(brief) if brief is not None else _sample_brief(),
    }


def _sample_envelope(
    *,
    max_children: object = 2,
    mode: str = "adaptive",
    include_agent_context: bool = False,
) -> dict[str, object]:
    envelope: dict[str, object] = {
        "goal_id": "goal-fixture",
        "agent_id": "agent-coord",
        "task_orchestration_contract": {
            "schema_version": "task_orchestration_contract_v2",
            "mode": mode,
            "coordinator_agent_id": "coord-1",
            "max_children": max_children,
        },
    }
    if include_agent_context:
        envelope["agent_context"] = {
            "schema_version": "agent_context_v0",
            "scope": {
                "goal_id": "goal-fixture",
                "agent_id": "agent-coord",
                "todo_id": "todo_1",
            },
        }
    return envelope


def _sample_receipt(
    topology: Mapping[str, object],
    *,
    lane_index: int = 0,
    status: str = "completed",
    workspace_ref: str | None = None,
    evidence_refs: list[str] | None = None,
    effect_classes: list[str] | None = None,
    context_mode: str = "fresh",
) -> dict[str, object]:
    raw_lanes = topology["lanes"]
    assert isinstance(raw_lanes, list)
    lane = raw_lanes[lane_index]
    assert isinstance(lane, Mapping)
    return {
        "schema_version": SUBAGENT_HOST_EXECUTION_RECEIPT_SCHEMA_VERSION,
        "bundle_id": topology["bundle_id"],
        "lane_id": lane["lane_id"],
        "goal_id": topology["goal_id"],
        "todo_id": lane["todo_id"],
        "execution_kind": lane["execution_kind"],
        "runtime_id": "codex-cli",
        "worker_ref": "worker:child-1",
        "source_state_ref": topology["source_state_ref"],
        "task_packet_digest": lane["task_packet_digest"],
        "context_mode": context_mode,
        "workspace_ref": workspace_ref,
        "status": status,
        "effect_classes": ["local_read"] if effect_classes is None else list(effect_classes),
        "evidence_refs": ["artifact:result-1"] if evidence_refs is None else list(evidence_refs),
        "raw_transcript_copied": False,
    }


def test_subagent_topology_module_budgets_and_reexports() -> None:
    root = Path(subagent_execution_topology.__file__).resolve().parents[3]
    module_paths = [
        root / "loopx/control_plane/turn_driver/subagent_execution_topology.py",
        root / "loopx/control_plane/turn_driver/subagent_execution_contracts.py",
        root / "loopx/control_plane/turn_driver/subagent_execution_reconciliation.py",
    ]
    internal_edges: dict[str, set[str]] = {}
    for path in module_paths:
        src = path.read_text(encoding="utf-8")
        lines = src.splitlines()
        limit = 650 if path.name == "subagent_execution_topology.py" else 750
        assert len(lines) <= limit, f"{path.name} exceeded {limit} LOC ({len(lines)})"
        assert not re.search(r"#\s*type:\s*ignore", src), f"type ignore in {path.name}"
        tree = ast.parse(src, filename=str(path))
        assert not any(isinstance(n, ast.Name) and n.id == "Any" for n in ast.walk(tree))
        internal_edges[path.stem] = {
            n.module
            for n in ast.walk(tree)
            if isinstance(n, ast.ImportFrom) and n.module and n.module.startswith("subagent_execution")
        }

    assert not internal_edges["subagent_execution_contracts"]
    assert "subagent_execution_topology" not in internal_edges["subagent_execution_reconciliation"]

    pairs = [
        (SUBAGENT_EXECUTION_TOPOLOGY_SCHEMA_VERSION, subagent_execution_contracts.SUBAGENT_EXECUTION_TOPOLOGY_SCHEMA_VERSION),
        (SUBAGENT_HOST_EXECUTION_RECEIPT_SCHEMA_VERSION, subagent_execution_contracts.SUBAGENT_HOST_EXECUTION_RECEIPT_SCHEMA_VERSION),
        (SUBAGENT_CONTROL_PLANE_RECONCILIATION_SCHEMA_VERSION, subagent_execution_contracts.SUBAGENT_CONTROL_PLANE_RECONCILIATION_SCHEMA_VERSION),
        (CHILD_EXECUTION_TASK_PACKET_SCHEMA_VERSION, subagent_execution_contracts.CHILD_EXECUTION_TASK_PACKET_SCHEMA_VERSION),
        (CHILD_EXECUTION_GUARD_SCHEMA_VERSION, subagent_execution_contracts.CHILD_EXECUTION_GUARD_SCHEMA_VERSION),
        (CHILD_EXECUTION_REJECTION_SCHEMA_VERSION, subagent_execution_contracts.CHILD_EXECUTION_REJECTION_SCHEMA_VERSION),
        (MAX_CHILD_EXECUTION_RECEIPTS, subagent_execution_contracts.MAX_CHILD_EXECUTION_RECEIPTS),
        (MAX_EFFECT_CLASSES, subagent_execution_contracts.MAX_EFFECT_CLASSES),
        (MAX_EVIDENCE_REFS, subagent_execution_contracts.MAX_EVIDENCE_REFS),
        (EXECUTION_KINDS, subagent_execution_contracts.EXECUTION_KINDS),
        (RECEIPT_STATUSES, subagent_execution_contracts.RECEIPT_STATUSES),
        (KNOWN_EFFECT_CLASSES, subagent_execution_contracts.KNOWN_EFFECT_CLASSES),
        (CHILD_CONTEXT_MODES, subagent_execution_contracts.CHILD_CONTEXT_MODES),
        (CHILD_CONTEXT_INHERITANCE, subagent_execution_contracts.CHILD_CONTEXT_INHERITANCE),
        (CHILD_FALLBACK_ACTIONS, subagent_execution_contracts.CHILD_FALLBACK_ACTIONS),
        (OPAQUE_REF_PATTERN, subagent_execution_contracts.OPAQUE_REF_PATTERN),
        (HOST_RESULT_FIELDS, subagent_execution_contracts.HOST_RESULT_FIELDS),
        (ChildExecutionTopologyError, subagent_execution_contracts.ChildExecutionTopologyError),
        (subagent_bundle_id, subagent_execution_contracts.subagent_bundle_id),
        (subagent_lane_id, subagent_execution_contracts.subagent_lane_id),
        (child_execution_receipts_json_schema, subagent_execution_contracts.child_execution_receipts_json_schema),
        (normalize_subagent_host_execution_receipts, subagent_execution_reconciliation.normalize_subagent_host_execution_receipts),
        (reconcile_subagent_execution, subagent_execution_reconciliation.reconcile_subagent_execution),
    ]
    for exported, canonical in pairs:
        assert exported is canonical


def test_immutable_mappingproxy_and_empty_container_parity() -> None:
    assert build_subagent_execution_topology(
        turn_envelope=_sample_envelope(), child_operations=[], turn_key="t1", source_state_ref="state:1"
    ) is None
    assert build_subagent_execution_topology(
        turn_envelope=MappingProxyType({"task_orchestration_contract": None}),
        child_operations=[_sample_operation()],
        turn_key="t1",
        source_state_ref="state:1",
    ) is None
    assert build_subagent_execution_topology(
        turn_envelope=_sample_envelope(mode="serial"),
        child_operations=[_sample_operation()],
        turn_key="t1",
        source_state_ref="state:1",
    ) is None

    # Boolean/zero max_children falls back to len(child_operations).
    env_bool = MappingProxyType(_sample_envelope(max_children=True))
    op_ok = MappingProxyType(_sample_operation("todo_1", brief=MappingProxyType(_sample_brief(write_scopes=["loopx/a.py"]))))
    op_bad = MappingProxyType(_sample_operation("todo_2", brief=MappingProxyType(_sample_brief(execution_policy={}))))
    topo = build_subagent_execution_topology(
        turn_envelope=env_bool,
        child_operations=[op_ok, op_bad],
        turn_key="turn-k1",
        source_state_ref="state:abc",
    )
    assert topo is not None
    assert topo["topology"] == "ephemeral_children"
    env_block = topo["execution_envelope"]
    assert isinstance(env_block, dict) and env_block["max_children"] == 2
    assert topo["rationale_codes"] == ["admitted_ephemeral_child_lanes", "child_launch_rejected"]
    bound = bind_child_operations_to_topology([op_ok, op_bad], MappingProxyType(topo))
    assert len(bound) == 1 and bound[0]["todo_id"] == "todo_1"
    assert bound[0]["workspace_ref"] == f"workspace:{bound[0]['lane_id']}"
    assert bind_child_operations_to_topology([op_ok], {}) == [dict(op_ok)]
    assert bind_child_operations_to_topology([op_ok], None) == [dict(op_ok)]

    # Empty child_operations list is omitted, while empty {} mappings stay present.
    req = subagent_host_request_projection(
        MappingProxyType({"delegation_context": {}, "child_operations": [], "subagent_execution_topology": {}})
    )
    assert req == {"delegation_context": {}, "subagent_execution_topology": {}}
    assert subagent_host_result_fields({}) == frozenset()
    assert subagent_host_result_fields({"subagent_execution_topology": {}}) == HOST_RESULT_FIELDS


def test_efficiency_dedup_and_single_pass_receipt_safety(monkeypatch: pytest.MonkeyPatch) -> None:
    deduped = _opaque_refs(
        ["ref:b", "ref:a", "ref:b", "ref:c", "ref:a"],
        field="evidence_refs",
        limit=MAX_EVIDENCE_REFS,
    )
    assert deduped == ["ref:a", "ref:b", "ref:c"]

    topo = build_subagent_execution_topology(
        turn_envelope=_sample_envelope(max_children=2),
        child_operations=[_sample_operation("todo_1"), _sample_operation("todo_2")],
        turn_key="turn-eff",
        source_state_ref="state:1",
    )
    assert topo is not None
    receipt = _sample_receipt(topo, lane_index=0, effect_classes=["local_read", "local_read"])

    safety_calls: list[str] = []
    orig_validate = subagent_execution_contracts.validate_public_safe_value

    def counting_validate(value: object, *, path: str = "public_payload") -> None:
        safety_calls.append(path)
        orig_validate(value, path=path)

    monkeypatch.setattr(subagent_execution_contracts, "validate_public_safe_value", counting_validate)
    normalized = normalize_subagent_host_execution_receipts([MappingProxyType(receipt)])
    assert len(normalized) == 1 and normalized[0]["effect_classes"] == ["local_read"]
    # Validates 8 required string refs + 2 effect_classes items + 1 evidence_refs item without full-dict re-walk.
    assert len(safety_calls) == 11
    assert not any(call == "child_execution_receipts[0]" for call in safety_calls)

    # Reconciliation works cleanly over immutable MappingProxyType lanes and receipts.
    raw_lanes = topo["lanes"]
    assert isinstance(raw_lanes, list)
    frozen_lanes = [
        MappingProxyType(lane) for lane in raw_lanes if isinstance(lane, Mapping)
    ]
    frozen_topo = MappingProxyType({**topo, "lanes": frozen_lanes})
    rec = reconcile_subagent_execution(
        frozen_topo, [MappingProxyType(normalized[0]), MappingProxyType(normalized[0])]
    )
    assert rec is not None and rec["status"] == "drifted"
    counts = rec["counts"]
    assert isinstance(counts, dict) and counts["orphaned"] == 1 and counts["incomplete"] == 1


def test_reconciliation_dispositions_and_host_observation_wiring() -> None:
    assert reconcile_subagent_execution(None, []) is None
    assert reconcile_subagent_execution({}, []) is None

    topo = build_subagent_execution_topology(
        turn_envelope=_sample_envelope(max_children=1, include_agent_context=True),
        child_operations=[_sample_operation("todo_1"), _sample_operation("todo_2")],
        turn_key="turn-rec",
        source_state_ref="state:1",
    )
    assert topo is not None
    receipt = _sample_receipt(topo, status="completed")
    norm: dict[str, object] = {}
    errors: list[str] = []
    observe_subagent_host_result(
        {"subagent_execution_topology": topo, "turn_envelope": _sample_envelope(include_agent_context=True)},
        {"child_execution_receipts": [receipt]},
        norm,
        errors,
    )
    assert errors == []
    assert "child_execution_receipts" in norm
    rec = norm["subagent_reconciliation"]
    assert isinstance(rec, Mapping) and rec["status"] == "guarded"
    assert "pre_spawn_rejections" in rec
    projected = subagent_execution_payload_projection({"host_result": norm})
    assert "subagent_reconciliation" in projected
    assert subagent_execution_payload_projection({"host_result": {}}) == {}


def test_negative_validation_paths_and_fail_closed_guards() -> None:
    assert normalize_subagent_host_execution_receipts(None) == []
    bad_cases: tuple[object, ...] = (
        "bad",
        {},
        [object()] * (MAX_CHILD_EXECUTION_RECEIPTS + 1),
        ["not-dict"],
    )
    for bad_receipts in bad_cases:
        with pytest.raises(ValueError):
            normalize_subagent_host_execution_receipts(bad_receipts)

    topo = build_subagent_execution_topology(
        turn_envelope=_sample_envelope(max_children=1),
        child_operations=[_sample_operation("todo_1")],
        turn_key="turn-neg",
        source_state_ref="state:1",
    )
    assert topo is not None
    base = _sample_receipt(topo)

    bad_variants: list[dict[str, object]] = [
        {**base, "extra": 1},
        {k: v for k, v in base.items() if k != "evidence_refs"},
        {**base, "schema_version": "wrong_v1"},
        {**base, "execution_kind": "persistent"},
        {**base, "context_mode": "unknown"},
        {**base, "status": "paused"},
        {**base, "raw_transcript_copied": True},
        {**base, "evidence_refs": "not-list"},
        {**base, "evidence_refs": ["../escape"]},
        {**base, "effect_classes": ["unknown_effect"]},
        {**base, "effect_classes": ["local_read" for _ in range(MAX_EFFECT_CLASSES + 1)]},
        {**base, "evidence_refs": [f"artifact:r{i}" for i in range(MAX_EVIDENCE_REFS + 1)]},
        {**base, "worker_ref": "/etc/private/path"},
        {**base, "workspace_ref": "".join(["sk", "-", "fixture0000000000000000"])},
    ]
    for bad in bad_variants:
        with pytest.raises(ValueError):
            normalize_subagent_host_execution_receipts([bad])

    norm: dict[str, object] = {}
    errors: list[str] = []
    observe_subagent_host_result(
        {"subagent_execution_topology": topo},
        {"child_execution_receipts": "bad-type"},
        norm,
        errors,
    )
    assert errors == ["child_execution_receipts must be a list"]
    assert "child_execution_receipts" not in norm
    assert isinstance(norm["subagent_reconciliation"], Mapping)

    with pytest.raises(ValueError, match="must be a string"):
        _opaque_ref(123, field="x")
    with pytest.raises(ChildExecutionTopologyError, match="child_task_packet_incomplete"):
        _child_task_packet(
            operation=_sample_operation(recommended_context="resume"),
            brief=_sample_brief(),
            todo_id="todo_1",
            source_state_ref="state:1",
            allowed_effect_classes=["local_read"],
            workspace_requirement="not_required",
            workspace_ref=None,
        )
    with pytest.raises(ChildExecutionTopologyError, match="acceptance"):
        _child_execution_lane(
            _sample_operation(brief=_sample_brief(extra={"acceptance": ["  "]})),
            bundle_id="bundle_1",
            source_state_ref="state:1",
        )
    schema = child_execution_receipts_json_schema()
    assert schema["maxItems"] == MAX_CHILD_EXECUTION_RECEIPTS
    assert "task_packet_digest" in schema["items"]["properties"]
