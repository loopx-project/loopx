"""Read-only filesystem adapter for the TS-owned unfinished delegation agenda."""
from __future__ import annotations

import json
import shlex
from pathlib import Path

from ..content_digest import BARE_SHA256_PATTERN
from ..effect_runtime import effect_runtime_result
from .goal_instance_scope import collaboration_goal_scope, decide_collaboration_lifecycle
from .inbox import (
    _entry, _hash, _now, _pending_entry, _pending_receipt_states, _read,
    _receipt, _root, _target, inspect_result_identity, result_paths, reply_status,
)


def _paths(folder):
    # Same exact-Agent folders as inbox/peer requests; never scan other Agents' inboxes.
    try:
        paths = folder.iterdir()
        return sorted(path for path in paths if path.suffix == ".json" and BARE_SHA256_PATTERN.fullmatch(path.stem))
    except FileNotFoundError:
        return []


def _results(root, row):
    statuses = {r.get("result_key", r["phase"]): r for r in reply_status(root, row)}
    results = []
    for path in result_paths(_root(root) / "replies" / row["request_id"]):
        value = _read(path)
        if value.get("phase") != "conclusion":
            continue
        if inspect_result_identity(value, row, path) is None:
            raise ValueError("delegation result identity unavailable")
        from .peers import _return_consumed

        consumed = row.get("source_kind") == "peer" and _return_consumed(
            path, row, value, agent_id=row["source_agent_id"])
        results.append({"result_key": path.stem, "request_revision": value.get("request_revision", 1),
            "status": statuses[path.stem]["status"], "consumed": consumed})
    return results


def read_delegation_turn_context(root: Path, registry: Path, goal_id: str, agent_id: str,
                                 *, read_command: str | None = None) -> dict:
    read_command = read_command or shlex.join(["loopx", "--registry", str(registry), "--runtime-root",
        str(root), "manager-inbox", "read", "--goal-id", goal_id, "--agent-id", agent_id])
    rows, complete, unavailable = [], True, 0
    with collaboration_goal_scope(registry, goal_id=goal_id, agents=(agent_id,)) as scope:
        base = _root(root)
        folders = (("incoming", base / "entries" / _hash(_target(goal_id, agent_id, scope))),
            ("outgoing", base / "peer-operations" / _hash(_target(goal_id, agent_id, scope))))
        for direction, folder in folders:
            paths = _paths(folder)
            complete = complete and len(paths) <= 2000
            for path in paths[:2000]:
                try:
                    if direction == "incoming":
                        row = _pending_entry(path, goal_id, agent_id, scope)
                    else:
                        operation = _read(path)
                        if (operation.get("goal_id") != goal_id or operation.get("source_agent_id") != agent_id
                            or scope.exact and operation.get("goal_ref") != scope.caller_goal_ref):
                            raise ValueError("delegation operation scope mismatch")
                        row = _entry(root, goal_id, operation["agent_id"], operation["request_id"], scope=scope)
                        if row.get("source_agent_id") != agent_id or row.get("source_id") != operation.get("source_id"):
                            raise ValueError("delegation request scope mismatch")
                    if decide_collaboration_lifecycle(scope, operation="inbox_observe", record=row).get("kind") == "omit":
                        continue
                    rows.append((direction, row))
                except (OSError, ValueError, KeyError, TypeError):
                    unavailable += 1
        batches = []
        # The existing receipt reader batches full controlled requests by RPC wire bytes.
        for start in range(0, len(rows), 128):
            batch = rows[start:start + 128]
            states = _pending_receipt_states(root, [row for _, row in batch])
            observations = []
            for (direction, row), state in zip(batch, states, strict=True):
                links, error = _receipt(root, "links", row)
                results = []
                try:
                    results = _results(root, row)
                except (OSError, ValueError, KeyError, TypeError):
                    error = "delegation_result_unavailable"
                # Only the selected current brief and receipt facts cross this boundary;
                # source messages, transcripts, result text and private rationale do not.
                control = state.get("request_control_view")
                state = {"kind": state["kind"], "recorded_decision": state["recorded_decision"]}
                if control is not None:
                    state["request_control_view"] = {key: control.get(key) for key in (
                        "control_state", "active_reservation", "revision", "revision_digest")}
                    state["request_control_view"]["brief"] = {"purpose": control["brief"]["purpose"]}
                observations.append({"direction": direction,
                    "request": {**{key: row[key] for key in ("request_id", "agent_id", "source_kind",
                        "source_agent_id", "goal_ref") if key in row},
                        "purpose": (row.get("brief") or {}).get("purpose") or row.get("message", "")[:2000]},
                    "receipt": state, "todo_ids": links.get("todo_ids", []),
                    "evidence_unavailable": bool(error), "results": results})
            # Large legal Unicode briefs must not overflow the ordinary local RPC.
            pending = []
            size = 0
            for observation in observations:
                encoded = len(json.dumps(observation, separators=(",", ":")).encode())
                if pending and size + encoded > 1_500_000:
                    batches.append(effect_runtime_result("collaboration.inbox.turn_items", {"observations": pending}))
                    pending, size = [], 0
                pending.append(observation)
                size += encoded
            if pending:
                batches.append(effect_runtime_result("collaboration.inbox.turn_items", {"observations": pending}))
    return effect_runtime_result("collaboration.inbox.turn_context", {"batches": batches,
        "scan_complete": complete, "unavailable_count": unavailable,
        "observed_at": _now(), "read_command": read_command})
