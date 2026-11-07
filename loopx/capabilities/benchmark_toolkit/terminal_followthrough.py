"""Local ledger IO for the typed, managed benchmark terminal transition.

The provider owns terminal/grading evidence. This adapter never discovers a
process, stops a solver, deletes a container, or invents scoring qualification.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ...control_plane.effect_runtime import (
    EffectRuntimeConflict, EffectRuntimeRejected, EffectRuntimeRemoteError,
    EffectRuntimeStartupError, effect_runtime_result,
)
from ...domain_state import _replace_domain_state_jsonl
from ...file_lock import exclusive_file_lock
from ...registry import atomic_write_json
from .concurrency_envelope import (
    _release_preview,
    default_benchmark_concurrency_envelope_path,
    read_benchmark_concurrency_envelope,
)
from .experiment_board import (
    benchmark_experiment_board_row_key,
    default_benchmark_experiment_board_path,
    normalize_benchmark_experiment_board_row,
    read_benchmark_experiment_board_rows,
)
from .runtime_observation import build_benchmark_runtime_observation


def reconcile_benchmark_terminal(
    *, project: str | Path, goal_id: str,
    admission_receipt: Mapping[str, Any], identity: Mapping[str, Any],
    terminal: Mapping[str, Any], execute: bool = False,
    agent_id: str | None = None,
) -> dict[str, Any]:
    """Write/read terminal, then release/read the original admission generation.

    The typed decision is prepared before locking; its exact inputs are checked
    under both existing Python locks, concurrency first, through readback.
    A crash between the two atomic replacements leaves a terminal board row and
    the original reservation; replay completes release without rewriting scores.
    The standalone board and concurrency commands retain their existing policy.
    """
    if not all(isinstance(item, Mapping) for item in (admission_receipt, identity, terminal)):
        raise TypeError("benchmark terminal inputs must be objects")
    envelope_path = default_benchmark_concurrency_envelope_path(
        project=project, goal_id=goal_id,
    )
    board_path = default_benchmark_experiment_board_path(
        project=project, goal_id=goal_id,
    )
    envelope = read_benchmark_concurrency_envelope(envelope_path)
    if envelope is None:
        raise ValueError("benchmark terminal admission ledger unavailable")
    active_runs = envelope["active_runs"]
    rows = read_benchmark_experiment_board_rows(board_path)
    admitted = any(row["run_id"] == identity.get("run_id") for row in active_runs)
    # This is classification, not authority: the typed owner below verifies
    # both identities and the exact admission against the locked ledgers.
    observation = build_benchmark_runtime_observation(
        admission_active=admitted, job_receipt_state="resolved",
        runner_owner_state="unknown",
        terminal_result_present=(terminal.get("solver_terminal") is True
                                 and terminal.get("grading_drained") is True),
    )
    try:
        decision = effect_runtime_result("benchmark.terminal-followthrough.prepare", {
            "goal_id": goal_id, "admission_receipt": dict(admission_receipt),
            "identity": dict(identity), "terminal": dict(terminal),
            "reconciled_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            # All rows with this run id remain visible for cross-study
            # ambiguity checks; unrelated history does not cross the RPC cap.
            "rows": [row for row in rows if row["run_id"] == identity.get("run_id")],
            "active_runs": [slot for slot in active_runs if slot["run_id"] == identity.get("run_id")],
            "runtime_observation": observation,
        })
    except (EffectRuntimeRejected, EffectRuntimeConflict) as exc:
        raise ValueError("benchmark terminal identity or evidence rejected") from exc
    except (EffectRuntimeRemoteError, EffectRuntimeStartupError) as exc:
        raise ValueError("benchmark terminal runtime unavailable; retry original evidence") from exc
    row = normalize_benchmark_experiment_board_row(decision["row"])
    key = benchmark_experiment_board_row_key(row)
    before = next(item for item in rows if benchmark_experiment_board_row_key(item) == key)
    result = {
        "ok": True, "goal_id": goal_id, "row": row,
        "dry_run": not execute, "write_performed": False,
        "released": False, "already_released": decision["already_released"],
        "board_readback_verified": False, "slot_readback_verified": False,
        "runtime_observation": observation, "path_recorded": False,
    }
    if not execute:
        return result
    with ExitStack() as stack:
        for path in (envelope_path, board_path):
            stack.enter_context(exclusive_file_lock(
                path, agent_id=agent_id, operation="benchmark_terminal_reconcile",
            ))
        # The policy subprocess never holds ledger locks. Reject any state
        # change since its exact input snapshot, then commit under both locks.
        if (read_benchmark_concurrency_envelope(envelope_path) != envelope
                or read_benchmark_experiment_board_rows(board_path) != rows):
            raise ValueError("benchmark terminal inputs changed; retry original evidence")
        if row != before:
            # Preserve other rows and the established domain-state key format.
            next_rows = [row if benchmark_experiment_board_row_key(item) == key else item
                         for item in rows]
            _replace_domain_state_jsonl(board_path, [
                {**item, "domain_state_key": benchmark_experiment_board_row_key(item)}
                for item in next_rows
            ])
            result["write_performed"] = True
        persisted = [item for item in read_benchmark_experiment_board_rows(board_path)
                     if benchmark_experiment_board_row_key(item) == key]
        if persisted != [row]:
            raise ValueError("benchmark terminal board readback mismatch; reservation retained")
        result["board_readback_verified"] = True
        if not decision["already_released"]:
            # The CAS input was rechecked under this same envelope lock.
            # Reuse the existing release transformation, not a second ledger rule.
            release, replacement = _release_preview(
                envelope, compact_run_id=row["run_id"],
                timestamp=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            )
            if replacement is None or release["released"] is not True:
                raise ValueError("benchmark terminal admission release unresolved")
            atomic_write_json(envelope_path, replacement)
            result.update(released=True, write_performed=True)
        readback = read_benchmark_concurrency_envelope(envelope_path)
        if readback is None or any(item["run_id"] == row["run_id"] for item in readback["active_runs"]):
            raise ValueError("benchmark terminal slot readback unresolved")
        result["slot_readback_verified"] = True
        return result
