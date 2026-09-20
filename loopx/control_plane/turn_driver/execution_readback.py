"""Public execution readback shared by preview, settlement and recovery paths."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from . import subagent_execution_topology as subagent
from .host_binding import managed_executor_payload_entry, managed_executor_remediation_projection
from .host_failure import project_host_failure
from .lane_fence import turn_lane_in_flight_projection
from .transaction import LOOPX_TURN_EXECUTION_SCHEMA_VERSION


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def execution_payload(
    plan: Mapping[str, Any],
    journal: Mapping[str, Any],
    *,
    execute: bool,
    replayed: bool,
    effects: Mapping[str, bool],
) -> dict[str, Any]:
    transaction = (
        plan.get("transaction") if isinstance(plan.get("transaction"), dict) else {}
    )
    turn_key = str(transaction.get("turn_key") or "")
    planned_host = plan.get("host") if isinstance(plan.get("host"), dict) else {}
    writeback = _mapping(journal.get("writeback"))
    todo_completion = _mapping(writeback.get("completion"))
    quota_spent = effects.get("quota_spent") is True or "quota_spend" in list(
        journal.get("completed_phases") or []
    )
    recovery = journal.get("recovery_audit")
    return {
        "ok": journal.get("status")
        in {
            "preview",
            "committed",
            "stopped",
            "scheduler_action_required",
        },
        "schema_version": LOOPX_TURN_EXECUTION_SCHEMA_VERSION,
        "mode": "run_once",
        "dry_run": not execute,
        "replayed": replayed,
        "resume_turn_key": turn_key,
        "journal_ref": f"turn:{turn_key.removeprefix('sha256:')[:16]}",
        "status": journal.get("status"),
        "execution_mode": planned_host.get("execution_mode"),
        "host": journal.get("host"),
        **managed_executor_payload_entry(plan),
        # Preview admission is an observation; execution receipts stay unchanged.
        **({"route": {
            "kind": plan["route"]["kind"],
            "selected_todo_id": (plan["route"].get("selected_todo") or {}).get("todo_id"),
            "would_invoke_host": plan["route"]["would_invoke_host"],
        }} if not execute else {}),
        "result_kind": journal.get("result_kind"),
        "validation": journal.get("task_validation"),
        "receipt": journal.get("receipt"),
        "scheduler": journal.get("scheduler"),
        **subagent.subagent_execution_payload_projection(journal),
        "effects": dict(effects),
        "quota_slot_spend_count": 1 if quota_spent else 0,
        **(
            {"settlement_result": journal["settlement_result"]}
            if isinstance(journal.get("settlement_result"), Mapping)
            else {}
        ),
        **(
            {"post_settlement": journal["post_settlement"]}
            if isinstance(journal.get("post_settlement"), Mapping)
            else {}
        ),
        **({"todo_completion": todo_completion} if todo_completion else {}),
        **({"reason": journal.get("reason")} if journal.get("reason") else {}),
        **turn_lane_in_flight_projection(journal),
        **managed_executor_remediation_projection(journal),
        **project_host_failure(journal),
        **({"recovery": dict(recovery)} if isinstance(recovery, Mapping) else {}),
    }
