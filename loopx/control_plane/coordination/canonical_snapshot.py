"""Assemble one native canonical snapshot without interpreting Todo semantics.

No partial list escapes this adapter. Continuation, population, identity and
ordering checks protect the transport contract; TS still owns domain validation.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .coordination_state_contract_generated import (
    LOCAL_COORDINATION_TODO_LIST_RESULT_SCHEMA,
    LOCAL_COORDINATION_TODO_SNAPSHOT_PAGE_REQUEST_SCHEMA as REQUEST_SCHEMA,
    LOCAL_COORDINATION_TODO_SNAPSHOT_PAGE_RESULT_SCHEMA as RESULT_SCHEMA,
)
METHOD = "coordination.local_authority.todo_snapshot_page"


def read_canonical_snapshot(
    *,
    rpc: Callable[..., Any],
    runtime_root: str,
    goal_id: str,
    include_leases: bool,
    projection_readback: Mapping[str, Any] | None,
    timeout: float,
) -> dict[str, Any]:
    """Read all pages at one revision, or return a typed failure without rows."""
    after: dict[str, Any] | None = None
    snapshot: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    source: str | None = None
    todos: list[dict[str, Any]] = []
    leases: list[dict[str, Any]] = []
    guards: dict[str, Any] = {}
    last_ids: dict[str, str | None] = {"todos": None, "leases": None}

    def require(condition: bool, message: str) -> None:
        if not condition:
            raise ValueError(message)

    try:
        while True:
            page = rpc(
                METHOD,
                {
                    "schema_version": REQUEST_SCHEMA,
                    "runtime_root": runtime_root,
                    "goal_id": goal_id,
                    "include_leases": include_leases,
                    "projection_readback": dict(projection_readback)
                    if projection_readback is not None
                    else None,
                    "after": after,
                },
                timeout=timeout,
            )
            require(isinstance(page, dict), "snapshot page is not an object")
            if page.get("status") != "page":
                # Do not return accumulated rows or accept an old unpaged success.
                require(
                    page.get("status") != "loaded",
                    "snapshot RPC returned an unpaged result",
                )
                return {
                    "schema_version": LOCAL_COORDINATION_TODO_LIST_RESULT_SCHEMA,
                    **{
                        key: page[key]
                        for key in (
                            "status",
                            "reason_code",
                            "reason",
                            "source_authority",
                            "decision_read_from_provider",
                            "legacy_fallback_used",
                        )
                        if key in page
                    },
                }
            require(
                page.get("schema_version") == RESULT_SCHEMA
                and page.get("decision_read_from_provider") is True
                and page.get("legacy_fallback_used") is False,
                "invalid snapshot page envelope",
            )
            current = page.get("snapshot")
            require(isinstance(current, dict), "snapshot identity is missing")
            require(
                set(current)
                == {
                    "goal_id",
                    "store_identity",
                    "provider_revision",
                    "cursor",
                    "query_sha256",
                    "todo_count",
                    "lease_count",
                },
                "invalid snapshot identity fields",
            )
            require(
                current["goal_id"] == goal_id
                and all(
                    isinstance(current[key], str) and bool(current[key])
                    for key in (
                        "store_identity",
                        "provider_revision",
                        "cursor",
                        "query_sha256",
                    )
                ),
                "foreign or incomplete snapshot identity",
            )
            require(
                all(
                    type(current[key]) is int and current[key] >= 0
                    for key in ("todo_count", "lease_count")
                ),
                "invalid snapshot population",
            )
            require(
                include_leases or current["lease_count"] == 0,
                "unexpected lease population",
            )
            current_metadata = page.get("metadata")
            require(isinstance(current_metadata, dict), "snapshot metadata is missing")
            require(
                set(current_metadata)
                <= {
                    "todo_read_model",
                    "goal_acceptance_contract",
                    "handoff_mode",
                    "projection_readback",
                }
                and "todo_read_model" in current_metadata,
                "invalid snapshot metadata fields",
            )
            if snapshot is None:
                snapshot, metadata, source = (
                    current,
                    current_metadata,
                    page.get("source_authority"),
                )
            require(
                current == snapshot
                and current_metadata == metadata
                and page.get("source_authority") == source,
                "snapshot changed between pages",
            )
            previous_size = len(todos) + len(leases)
            for name, target in (("todos", todos), ("leases", leases)):
                if name == "leases" and not include_leases:
                    require("leases" not in page, "unexpected lease records")
                    continue
                rows = page.get(name)
                require(isinstance(rows, list), "snapshot records are missing")
                for item in rows:
                    require(isinstance(item, dict), "snapshot record is not an object")
                    identity = item.get("todo_id")
                    require(
                        isinstance(identity, str) and bool(identity),
                        "snapshot record identity is missing",
                    )
                    previous = last_ids[name]
                    require(
                        previous is None or identity > previous,
                        "snapshot record is duplicated or out of order",
                    )
                    last_ids[name] = identity
                    target.append(item)
            page_guards = page.get("goal_acceptance_work_guards", {})
            require(
                isinstance(page_guards, dict)
                and set(page_guards) <= {x["todo_id"] for x in page["todos"]},
                "acceptance guard escaped its page",
            )
            require(not (set(page_guards) & set(guards)), "duplicate acceptance guard")
            guards.update(page_guards)
            require(
                len(todos) <= snapshot["todo_count"]
                and len(leases) <= snapshot["lease_count"],
                "snapshot population overflow",
            )
            require(
                not leases or len(todos) == snapshot["todo_count"],
                "lease page precedes remaining Todos",
            )
            require("next" in page, "snapshot continuation is missing")
            after = page["next"]
            if after is None:
                require(
                    len(todos) == snapshot["todo_count"]
                    and len(leases) == snapshot["lease_count"],
                    "snapshot ended before its complete population",
                )
                break
            require(
                isinstance(after, dict)
                and set(after) == {"snapshot", "todo_offset", "lease_offset"},
                "invalid snapshot continuation",
            )
            require(
                after["snapshot"] == snapshot
                and type(after["todo_offset"]) is int
                and type(after["lease_offset"]) is int
                and after["todo_offset"] == len(todos)
                and after["lease_offset"] == len(leases),
                "snapshot continuation skipped records",
            )
            require(
                len(todos) + len(leases) > previous_size
                and len(todos) + len(leases)
                < snapshot["todo_count"] + snapshot["lease_count"],
                "snapshot continuation made no progress or passed its end",
            )
        assert snapshot is not None and metadata is not None
        return {
            "schema_version": LOCAL_COORDINATION_TODO_LIST_RESULT_SCHEMA,
            **metadata,
            "status": "loaded",
            "todos": todos,
            "todo_ids": [item["todo_id"] for item in todos],
            **({"leases": leases} if include_leases else {}),
            **(
                {"goal_acceptance_work_guards": guards}
                if "goal_acceptance_contract" in metadata
                else {}
            ),
            "provider_revision": snapshot["provider_revision"],
            "cursor": snapshot["cursor"],
            "store_identity": snapshot["store_identity"],
            "source_authority": source,
            "decision_read_from_provider": True,
            "legacy_fallback_used": False,
        }
    except ValueError as error:
        return {
            "schema_version": LOCAL_COORDINATION_TODO_LIST_RESULT_SCHEMA,
            "status": "failed",
            "reason_code": "canonical_snapshot_result_invalid",
            "reason": str(error),
            "decision_read_from_provider": True,
            "legacy_fallback_used": False,
        }
