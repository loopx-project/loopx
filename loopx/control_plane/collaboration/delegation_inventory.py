"""Bounded readback of an existing requester's durable delegation journal."""
from __future__ import annotations

import heapq
from typing import TYPE_CHECKING

from ..effect_runtime import EffectRuntimeRemoteError, effect_runtime_result
from .inbox import _read
from .peers import _goal, require_operation_id
from ..content_digest import BARE_SHA256_PATTERN
from ..runtime.time import now_utc_iso

if TYPE_CHECKING:
    from ...collaboration_mcp import Delegations

# Files kept beside an execution record and never merged into it: the stop
# receipt, and the record naming the native Host its Turn launched.
DELEGATION_STOP_RECEIPT_SUFFIX = ".stop.json"
DELEGATION_HOST_PROCESS_SUFFIX = ".host.json"
DELEGATION_RECORD_SIDECAR_SUFFIXES = (DELEGATION_STOP_RECEIPT_SUFFIX, DELEGATION_HOST_PROCESS_SUFFIX)


def read_delegation_inventory(service: Delegations, *, limit: int = 20,
                              cursor: str | None = None) -> dict:
    query = effect_runtime_result("collaboration.delegation.inventory_query",
                                  {"limit": limit, "cursor": cursor})
    _goal(service.registry, service.goal_id, service.agent_id)
    directory = service.path("inventory").parent
    available_count = 0

    def addresses():
        nonlocal available_count
        try:
            entries = directory.iterdir()
            for path in entries:
                if path.suffix != ".json" or path.name.endswith(DELEGATION_RECORD_SIDECAR_SUFFIXES):
                    continue
                if not BARE_SHA256_PATTERN.fullmatch(path.stem):
                    raise ValueError("unexpected delegation record address; reconcile inventory storage")
                if query["cursor"] is None or path.stem > query["cursor"]:
                    available_count += 1
                    yield path.stem
        except FileNotFoundError:
            # No journal yet is a valid empty inventory; other IO errors propagate.
            if directory.exists():
                raise

    keys = heapq.nsmallest(query["limit"] + 1, addresses())
    directory_read_at = now_utc_iso()
    # Bound the aggregate by its earliest read, never by final page assembly.
    operations_read_at = now_utc_iso() if keys else directory_read_at
    observations = []
    for key in keys[:query["limit"]]:
        record = {"record_id": key, "operation_id": None}
        try:
            path = directory / (key + ".json")
            if path.is_symlink() or not path.is_file():
                raise ValueError("delegation record unavailable")
            operation_id = require_operation_id(_read(path)["identity"]["operation_id"])
            if service.path(operation_id) != path:
                raise ValueError("delegation record identity mismatch")
            record["operation_id"] = operation_id
            observed = service.read(operation_id)
        except (OSError, ValueError, KeyError, TypeError, EffectRuntimeRemoteError):
            observed = None
        observations.append({"record": record, "observation": observed})
    return effect_runtime_result("collaboration.delegation.inventory_page", {
        "query": query, "record_ids": keys, "observations": observations,
        "read_facts": {"observed_at": now_utc_iso(), "directory_read_at": directory_read_at,
                       "operations_read_at": operations_read_at,
                       "available_count": available_count},
    }, large_local_snapshot=True)
