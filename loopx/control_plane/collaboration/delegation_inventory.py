"""Bounded readback of an existing requester's durable delegation journal."""
from __future__ import annotations

import heapq
from typing import TYPE_CHECKING

from ..effect_runtime import EffectRuntimeRemoteError, effect_runtime_result
from .inbox import _read
from .peers import _goal, require_operation_id
from ..content_digest import BARE_SHA256_PATTERN

if TYPE_CHECKING:
    from ...collaboration_mcp import Delegations


def read_delegation_inventory(service: Delegations, *, limit: int = 20,
                              cursor: str | None = None) -> dict:
    query = effect_runtime_result("collaboration.delegation.inventory_query",
                                  {"limit": limit, "cursor": cursor})
    _goal(service.registry, service.goal_id, service.agent_id)
    directory = service.path("inventory").parent

    def addresses():
        try:
            entries = directory.iterdir()
            for path in entries:
                if path.suffix != ".json":
                    continue
                if not BARE_SHA256_PATTERN.fullmatch(path.stem):
                    raise ValueError("unexpected delegation record address; reconcile inventory storage")
                if query["cursor"] is None or path.stem > query["cursor"]:
                    yield path.stem
        except FileNotFoundError:
            # No journal yet is a valid empty inventory; other IO errors propagate.
            if directory.exists():
                raise

    keys = heapq.nsmallest(query["limit"] + 1, addresses())
    items = []
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
            item = effect_runtime_result("collaboration.delegation.inventory_item",
                                          {"record": record, "observation": observed})
        except (OSError, ValueError, KeyError, TypeError, EffectRuntimeRemoteError):
            # A corrupt/stale branch must not hide healthy siblings or be called accepted.
            item = effect_runtime_result("collaboration.delegation.inventory_item",
                                          {"record": record, "observation": None})
        items.append(item)
    more = len(keys) > query["limit"]
    return {
        "schema_version": "loopx_delegation_inventory_v0",
        "items": items, "has_more": more,
        "next_cursor": keys[query["limit"] - 1] if more else None,
        "page_readback_complete": all(item["status"] != "unavailable" for item in items),
        "note": "Live requester-scoped page, not a snapshot or proof that the whole Goal is complete. "
                "Read original operations for full results; resume only when recovery is required. "
                "Restart paging to discover work added before the cursor. Listing starts no work.",
    }
