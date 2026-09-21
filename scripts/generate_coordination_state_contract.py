#!/usr/bin/env python3
"""Generate Python and TypeScript bindings for the coordination contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from pprint import pformat
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    ROOT
    / "loopx"
    / "control_plane"
    / "coordination"
    / "coordination_state_contract_v0.json"
)
PYTHON_PATH = CONTRACT_PATH.with_name("coordination_state_contract_generated.py")
TYPESCRIPT_PATH = CONTRACT_PATH.with_name("coordination_state_contract.generated.ts")

EXPECTED_TODO_KEYS = {
    "schema_version",
    "item_schema_version",
    "fields",
    "required_fields",
}
EXPECTED_COMPATIBILITY = {
    "unknown_field_policy": "reject",
    "field_removal_policy": "maintainer_approval_required",
    "markdown_role": "human_workbench_and_compatibility_projection",
}
LOCAL_AUTHORITY_PROTOCOL_KEYS = (
    "todo_read_request_schema",
    "todo_read_result_schema",
    "todo_list_request_schema",
    "todo_list_result_schema",
    "promotion_request_schema",
    "promotion_result_schema",
    "promotion_receipt_schema",
    "promotion_review_request_schema",
    "promotion_review_result_schema",
)
RUNTIME_SHADOW_PROTOCOL_KEYS = (
    "commit_request_schema",
    "commit_result_schema",
    "receipt_schema",
    "inspect_request_schema",
    "inspect_result_schema",
    "bootstrap_request_schema",
    "bootstrap_result_schema",
    "rollback_request_schema",
    "rollback_result_schema",
    "qualify_request_schema",
    "qualify_result_schema",
    "todo_read_request_schema",
    "todo_read_result_schema",
)
LOCAL_AUTHORITY_SHADOW_PROTOCOL_KEYS = (
    "binding_schema",
    "config_schema",
    "request_schema",
    "projection_schema",
    "evidence_schema",
    "observation_receipt_schema",
    "outbox_entry_schema",
    "outbox_commit_schema",
    "drain_cursor_schema",
    "transaction_projection_schema",
    "commit_entry_request_schema",
    "commit_entry_result_schema",
    "read_request_schema",
    "read_result_schema",
    "event_schema",
    "transaction_receipt_schema",
    "transaction_evidence_schema",
)
LEGACY_WRITER_FENCE_PROTOCOL_KEYS = (
    "fence_schema",
    "engage_request_schema",
    "result_schema",
    "write_check_request_schema",
    "write_check_result_schema",
)
DELIVERY_CONTINUITY_PROTOCOL_KEYS = (
    "continuity_result_schema",
    "boundary_result_schema",
    "routing_request_schema",
    "routing_result_schema",
)
DELIVERY_WORKSPACE_PROTOCOL_KEYS = (
    "causality_schema",
    "causality_request_schema",
    "causality_result_schema",
    "resolution_schema",
    "settlement_requirement_schema",
    "legacy_receipt_evidence_schema",
)
DELIVERY_WORKSPACE_SNAPSHOT_PROTOCOL_KEYS = (
    "snapshot_schema",
    "legacy_snapshot_schema",
    "request_schema",
    "result_schema",
)
TASK_LEASE_PROTOCOL_KEYS = (
    "acquire_request_schema",
    "canonical_acquire_request_schema",
    "lifecycle_request_schema",
    "canonical_renew_request_schema",
    "canonical_lifecycle_request_schema",
    "canonical_claim_transfer_request_schema",
)
CAPABILITY_HOOK_PROTOCOL_KEYS = (
    "registration_schema",
    "interaction_result_schema",
    "turn_start_registration_schema",
    "turn_start_result_schema",
    "post_writeback_registration_schema",
    "post_writeback_input_schema",
    "post_writeback_result_schema",
    "post_writeback_receipt_schema",
    "intent_schema",
)
ACTION_PORTFOLIO_PROTOCOL_KEYS = (
    "selection_request_schema",
    "selection_result_schema",
    "planning_packet_request_schema",
    "planning_packet_result_schema",
)
TODO_RESUME_PROTOCOL_KEYS = (
    "normalize_request_schema",
    "evaluation_request_schema",
    "evaluation_result_schema",
    "external_wait_request_schema",
    "external_wait_result_schema",
)
REPLAN_SETTLEMENT_PROTOCOL_KEYS = (
    "request_schema",
    "result_schema",
    "lifecycle_reentry_request_schema",
    "lifecycle_reentry_result_schema",
)
LEGACY_WRITER_FENCE_CONSTANT_NAMES = {
    "fence_schema": "LEGACY_COORDINATION_WRITER_FENCE_SCHEMA",
    "engage_request_schema": "LEGACY_COORDINATION_WRITER_FENCE_ENGAGE_REQUEST_SCHEMA",
    "result_schema": "LEGACY_COORDINATION_WRITER_FENCE_RESULT_SCHEMA",
    "write_check_request_schema": "LEGACY_COORDINATION_WRITE_CHECK_REQUEST_SCHEMA",
    "write_check_result_schema": "LEGACY_COORDINATION_WRITE_CHECK_RESULT_SCHEMA",
}


# One ordered binding map owns validation keys and both language exports.
PROTOCOL_BINDINGS = {
    "local_authority_protocol": {key: f"LOCAL_COORDINATION_{key.upper()}" for key in LOCAL_AUTHORITY_PROTOCOL_KEYS},
    "runtime_shadow_protocol": {key: f"COORDINATION_RUNTIME_SHADOW_{key.upper()}" for key in RUNTIME_SHADOW_PROTOCOL_KEYS},
    "local_authority_shadow_protocol": {key: f"LOCAL_AUTHORITY_SHADOW_{key.upper()}" for key in LOCAL_AUTHORITY_SHADOW_PROTOCOL_KEYS},
    "shadow_management_protocol": {
        "state_schema": "SHADOW_MANAGEMENT_STATE_SCHEMA",
        "manifest_schema": "SHADOW_MANAGEMENT_MANIFEST_SCHEMA",
        "outbox_manifest_schema": "SHADOW_OUTBOX_MANIFEST_SCHEMA",
    },
    "legacy_writer_fence_protocol": LEGACY_WRITER_FENCE_CONSTANT_NAMES,
    "delivery_continuity_protocol": {key: f"DELIVERY_{key.upper()}" for key in DELIVERY_CONTINUITY_PROTOCOL_KEYS},
    "delivery_workspace_protocol": {key: f"DELIVERY_WORKSPACE_{key.upper()}" for key in DELIVERY_WORKSPACE_PROTOCOL_KEYS},
    "delivery_workspace_snapshot_protocol": {key: f"DELIVERY_WORKSPACE_SNAPSHOT_{key.upper()}" for key in DELIVERY_WORKSPACE_SNAPSHOT_PROTOCOL_KEYS},
    "task_lease_protocol": {key: f"TASK_LEASE_{key.upper()}" for key in TASK_LEASE_PROTOCOL_KEYS},
    "capability_hook_protocol": {key: f"CAPABILITY_HOOK_{key.upper()}" for key in CAPABILITY_HOOK_PROTOCOL_KEYS},
    "action_portfolio_protocol": {key: f"ACTION_PORTFOLIO_{key.upper()}" for key in ACTION_PORTFOLIO_PROTOCOL_KEYS},
    "todo_resume_protocol": {key: f"TODO_RESUME_{key.upper()}" for key in TODO_RESUME_PROTOCOL_KEYS},
    "replan_settlement_protocol": {key: f"REPLAN_SETTLEMENT_{key.upper()}" for key in REPLAN_SETTLEMENT_PROTOCOL_KEYS},
}
EXPECTED_TOP_LEVEL_KEYS = {
    "schema_version", "todo_read_record", "todo_domain_record",
    "todo_projection_metadata", "todo_priority", "compatibility", *PROTOCOL_BINDINGS,
}


def _string_list(value: object, *, label: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item for item in value)
    ):
        raise ValueError(f"{label} must contain non-empty strings")
    if len(set(value)) != len(value):
        raise ValueError(f"{label} must not contain duplicates")
    return value


def load_contract() -> dict[str, Any]:
    raw: object = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or set(raw) != EXPECTED_TOP_LEVEL_KEYS:
        raise ValueError("coordination contract has unexpected top-level fields")
    if raw.get("schema_version") != "loopx_coordination_state_contract_v0":
        raise ValueError("coordination contract schema mismatch")
    priority = raw.get("todo_priority")
    if not isinstance(priority, dict) or set(priority) != {"values", "legacy_prefix_pattern", "legacy_label_pattern", "missing_rank"}:
        raise ValueError("Todo priority contract has unexpected fields")
    _string_list(priority["values"], label="todo_priority.values")
    for pattern in ("legacy_prefix_pattern", "legacy_label_pattern"):
        if not isinstance(priority[pattern], str) or not priority[pattern]:
            raise ValueError("Todo priority pattern must be a non-empty string")
    if not isinstance(priority["missing_rank"], int) or isinstance(priority["missing_rank"], bool):
        raise ValueError("Todo missing priority rank must be an integer")
    todo = raw.get("todo_read_record")
    if not isinstance(todo, dict) or set(todo) != EXPECTED_TODO_KEYS:
        raise ValueError("Todo record contract has unexpected fields")
    for field in ("schema_version", "item_schema_version"):
        if not isinstance(todo.get(field), str) or not todo[field]:
            raise ValueError(f"todo_read_record.{field} must be a non-empty string")
    fields = _string_list(todo.get("fields"), label="todo_read_record.fields")
    required = _string_list(
        todo.get("required_fields"), label="todo_read_record.required_fields"
    )
    missing = sorted(set(required).difference(fields))
    if missing:
        raise ValueError(
            "todo_read_record.required_fields are absent from fields: "
            + ", ".join(missing)
        )
    seen_schemas: set[str] = set()
    for section, bindings in PROTOCOL_BINDINGS.items():
        protocol = raw.get(section)
        label = section.replace("_", " ")
        if not isinstance(protocol, dict) or tuple(protocol) != tuple(bindings):
            raise ValueError(f"{label} has unexpected fields or order")
        if any(not isinstance(value, str) or not value for value in protocol.values()):
            raise ValueError(f"{label} schemas must be non-empty strings")
        if len(set(protocol.values())) != len(protocol):
            raise ValueError(f"{label} schemas must be unique")
        if seen_schemas.intersection(protocol.values()):
            raise ValueError("protocol schemas must be unique across families")
        seen_schemas.update(protocol.values())
    if raw.get("compatibility") != EXPECTED_COMPATIBILITY:
        raise ValueError("coordination contract compatibility policy mismatch")
    projection = raw["todo_projection_metadata"]
    if not isinstance(projection, dict) or set(projection) != {"fields", "required_fields"}:
        raise ValueError("Todo projection contract has unexpected fields")
    projection_fields = _string_list(projection["fields"], label="projection.fields")
    projection_required = _string_list(projection["required_fields"], label="projection.required_fields")
    if not set(projection_required) <= set(projection_fields) <= set(fields):
        raise ValueError("Todo projection fields are not declared")
    domain = raw["todo_domain_record"]
    if not isinstance(domain, dict) or set(domain) != {
        "schema_version", "item_schema_version", "fields_from", "exclude_fields_from", "required_fields",
    }:
        raise ValueError("Todo domain contract has unexpected fields")
    if domain["fields_from"] != "todo_read_record" or domain["exclude_fields_from"] != "todo_projection_metadata":
        raise ValueError("Todo domain field sources are invalid")
    for field in ("schema_version", "item_schema_version"):
        if not isinstance(domain[field], str) or not domain[field]:
            raise ValueError("Todo domain schema versions must be non-empty strings")
    domain_required = _string_list(domain["required_fields"], label="domain.required_fields")
    if not set(domain_required) <= set(fields) - set(projection_fields):
        raise ValueError("Todo domain required fields are not declared")
    return raw


def render_python(contract: dict[str, Any]) -> str:
    literal = pformat(contract, width=88, sort_dicts=False)
    constants = "\n\n".join(
        "\n".join(f"{name}: Final[str] = {contract[section][key]!r}"
                  for key, name in bindings.items())
        for section, bindings in PROTOCOL_BINDINGS.items()
    )
    return (
        '"""Generated from coordination_state_contract_v0.json; do not edit."""\n\n'
        "from __future__ import annotations\n\n"
        "from types import MappingProxyType\n"
        "from typing import Any, Final\n\n"
        "def _freeze(value: Any) -> Any:\n"
        "    if isinstance(value, dict):\n"
        "        return MappingProxyType({key: _freeze(item) for key, item in value.items()})\n"
        "    if isinstance(value, list):\n"
        "        return tuple(_freeze(item) for item in value)\n"
        "    return value\n\n"
        f"COORDINATION_STATE_CONTRACT: Final = _freeze({literal})\n"
        f"{constants}\n"
    )


def render_typescript(contract: dict[str, Any]) -> str:
    # Declare each wire identity once, then compose the public contract from
    # those literal-typed bindings. JSON remains the sole editable source.
    constants = "\n\n".join(
        "\n".join(f"export const {name} = {json.dumps(contract[section][key])};"
                  for key, name in bindings.items())
        for section, bindings in PROTOCOL_BINDINGS.items()
    )
    sections = []
    for section, value in contract.items():
        if section in PROTOCOL_BINDINGS:
            fields = ",\n".join(
                f"    {json.dumps(key)}: {name}"
                for key, name in PROTOCOL_BINDINGS[section].items()
            )
            rendered = "{\n" + fields + "\n  }"
        else:
            rendered = json.dumps(value, indent=2, ensure_ascii=False).replace("\n", "\n  ")
        sections.append(f"  {json.dumps(section)}: {rendered}")
    literal = "{\n" + ",\n".join(sections) + "\n}"
    return (
        "// Generated from coordination_state_contract_v0.json; do not edit.\n\n"
        "function deepFreeze<T>(value: T): T {\n"
        "  if (value !== null && typeof value === 'object') {\n"
        "    for (const child of Object.values(value)) deepFreeze(child);\n"
        "    Object.freeze(value);\n"
        "  }\n"
        "  return value;\n"
        "}\n\n"
        f"{constants}\n\n"
        f"export const COORDINATION_STATE_CONTRACT = deepFreeze({literal} as const);\n"
    )


def update(path: Path, content: str, *, check: bool) -> bool:
    current = path.read_text(encoding="utf-8") if path.exists() else None
    if current == content:
        return True
    if check:
        print(f"stale generated coordination contract: {path.relative_to(ROOT)}", file=sys.stderr)
        return False
    path.write_text(content, encoding="utf-8")
    print(f"generated {path.relative_to(ROOT)}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    contract = load_contract()
    current = [
        update(PYTHON_PATH, render_python(contract), check=args.check),
        update(TYPESCRIPT_PATH, render_typescript(contract), check=args.check),
    ]
    return 0 if all(current) else 1


if __name__ == "__main__":
    raise SystemExit(main())
