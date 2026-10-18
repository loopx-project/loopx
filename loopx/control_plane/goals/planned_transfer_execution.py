"""Bounded process IO for one reviewed original-Task continuation.

The existing Turn Loop Controller owns every continuation disposition. The
typed Goal transfer plan retains the reviewed budget; native Task/lease/Turn
owners admit and settle each dispatch. There is no timer or second work ledger.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from time import monotonic, time
from pathlib import Path
from typing import Any

from ...file_lock import LockAcquisitionPolicy, exclusive_cross_runtime_file_lock
from ..effect_runtime import effect_runtime_result
from ..turn_driver.journal_store import (
    find_loopx_turn_key_by_settlement_identity, load_turn_journal, turn_journal_path,
)
from ..turn_driver.loop_controller import (
    BoundedTurnBudget,
    LoopDisposition,
    ValidatedTurnReceipt,
    decide_loop_disposition,
)


def _common(plan: dict[str, Any], key: str) -> list[str]:
    arguments = ["--goal-id", plan["goal_id"], "--agent-id", plan["agent_id"],
        "--todo-id", plan["todo_id"], "--turn-instance-id", key, "--host", plan["host"],
        "--execution-mode", "isolated-headless", "--session-scope", "agent"]
    for capability in plan["available_capabilities"]:
        arguments += ["--available-capability", capability]
    return arguments


def _run_original_turn(plan, key, invoke, runtime, codex_bin, timeout, *, host_service=False):
    identity = {name: plan[name] for name in ("goal_id", "agent_id", "todo_id")}
    original = find_loopx_turn_key_by_settlement_identity(runtime,
        **identity, turn_instance_id=key)
    if original is None and timeout < 1:
        return None
    if host_service and original is not None:
        journal = load_turn_journal(turn_journal_path(runtime, goal_id=plan["goal_id"], turn_key=original))
        if journal is None or journal.get("status") != "committed":
            # Reopening a listener does not authorize replay of a possibly live
            # effect or retry of failed work. The native Turn recovery owns it.
            return {"ok": False, "error_code": "original_work_turn_requires_recovery"}
    lease = None
    if original is None:
        args = ["task-lease", "acquire", "--goal-id", plan["goal_id"], "--todo-id", plan["todo_id"],
            "--owner", plan["agent_id"], "--idempotency-key", key,
            "--write-worktree", plan["project"], "--ttl-seconds", "600"]
        for scope in plan["write_scopes"]:
            args += ["--write-scope", scope]
        acquired = invoke(*args)
        if not acquired.get("ok"):
            raise ValueError("Original Task lease requires its owning recovery")
        lease = acquired["lease"]
    args = ["turn", "run-once", *_common(plan, key), "--project", plan["project"],
        "--codex-bin", codex_bin, "--codex-sandbox", plan["sandbox"],
        "--validation-command-json", json.dumps(plan["validation_argv"]),
        "--timeout-seconds", str(timeout), "--no-global-sync", "--execute"]
    if lease is not None:
        args += ["--task-lease-idempotency-key", lease["idempotency_key"],
            "--task-lease-expected-version", str(lease["version"])]
    if plan["validation_timeout_seconds"] is not None:
        args += ["--validation-timeout-seconds", str(plan["validation_timeout_seconds"])]
    result = invoke(*args)
    if result.get("ok") is True:
        # Also recover a crash after commit but before this release. A loaded
        # journal needs no new proof; never release another execution's lease.
        _release_settled_lease(plan, key, invoke)
    return result


def _release_settled_lease(plan, key, invoke):
    common = ["--goal-id", plan["goal_id"], "--todo-id", plan["todo_id"]]
    held = invoke("task-lease", "inspect", *common).get("lease")
    if isinstance(held, dict) and held.get("status") == "active" and held.get("idempotency_key") == key:
        released = invoke("task-lease", "release", *common, "--owner", plan["agent_id"],
            "--idempotency-key", key, "--expected-version", str(held["version"]))
        if not released.get("ok"):
            raise ValueError("Original execution lease requires its owning recovery")


def continue_transferred_work(*, registry_path: Path, goal_id: str, operation_id: str,
    codex_bin: str | None, codex_home: Path | None, host_service: bool = False,
    cancelled=lambda: False) -> dict[str, Any]:
    from .planned_transfer_io import read_goal_transfer_context

    context = read_goal_transfer_context(registry_path=registry_path, goal_id=goal_id)
    runtime = Path(context["source_context"]["runtime_root"])
    request = {"runtime_root": str(runtime), "goal_id": goal_id, "operation_id": operation_id}
    plan = effect_runtime_result("coordination.planned_transfer.execution_plan",
        {**request, **({"host_service": True} if host_service else {})}, retry_safe=True)["plan"]
    if plan is None:
        return {"ok": False, "reason_code": "original_work_not_prepared"}
    env = dict(os.environ)
    if codex_home is not None:
        env["CODEX_HOME"] = str(codex_home)
    prefix = [sys.executable, "-I", "-m", "loopx.cli", "--format", "json",
        "--registry", str(registry_path), "--runtime-root", str(runtime)]

    def invoke(*arguments: str) -> dict[str, Any]:
        # These CLI requests carry only argv. Do not lend the App/terminal's
        # input to a later independent validator; Host protocol input has its
        # own explicit channel inside run-once.
        run = subprocess.run([*prefix, *arguments], cwd=plan["project"], env=env,
            stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", errors="strict", check=False)
        return json.loads(run.stdout)

    identity = {key: plan[key] for key in ("goal_id", "agent_id", "todo_id", "turn_instance_id")}
    budget = plan["continuation_budget"]
    with exclusive_cross_runtime_file_lock(runtime / "goals" / goal_id / "turns" / f"{plan['turn_instance_id']}.dispatch",
            policy=LockAcquisitionPolicy.SINGLE_FLIGHT if host_service else LockAcquisitionPolicy.MUTATION,
            operation="planned_transfer_original_work"):
        remaining = (budget["timeout_seconds"] if "continuation_deadline_at_ms" not in plan else
            min(budget["timeout_seconds"], (plan["continuation_deadline_at_ms"] - time() * 1000) / 1000))
        return _continue_original_turns(plan, invoke, request, runtime, identity, budget,
            monotonic() + remaining, codex_bin or env.get("CODEX_BIN", "codex"),
            host_service=host_service, cancelled=cancelled)


def _continuation_decision(receipt, fresh, completed, max_turns):
    return decide_loop_disposition(turn_receipt=receipt, quota_decision=fresh["turn_envelope"],
        predecessor_turn_key=receipt.turn_key,
        bounded_turn_budget=BoundedTurnBudget(lineage=receipt.lineage,
            max_turns=max_turns, completed_turns=completed))


def _continue_original_turns(plan, invoke, request, runtime, identity, budget, deadline, codex_bin,
    *, host_service=False, cancelled=lambda: False):
    key = identity["turn_instance_id"]
    completed = 0
    previous = None
    while True:
        if cancelled():
            return {"ok": True, **identity, "turn_count": completed, "loop_disposition": LoopDisposition.STOP.value}
        if completed == 0 and find_loopx_turn_key_by_settlement_identity(runtime,
                **{name: plan[name] for name in ("goal_id", "agent_id", "todo_id")}, turn_instance_id=key) is None:
            # Every fresh confirmation checks current admission before acquiring
            # a work lease. Paused/exhausted work must not leave a failed Turn or
            # claim behind. A later Host tick checks again without replaying work.
            fresh = invoke("turn", "plan", *_common(plan, key), "--include-transaction-detail")
            if "turn_envelope" not in fresh:
                # Explicit selection can be refused before a Turn exists. Keep
                # that original native error; do not manufacture a host decision
                # or acquire a lease to discover the same refusal a second time.
                return {"ok": False, **identity, "turn_count": 0,
                    "reason_code": fresh.get("error_code") or "original_work_continuation_unavailable"}
            decision = decide_loop_disposition(turn_receipt=None, quota_decision=fresh["turn_envelope"])
            if decision["disposition"] != LoopDisposition.RUN_NOW.value:
                return {"ok": True, **identity, "turn_count": 0, "loop_disposition": decision["disposition"]}
        result = _run_original_turn(plan, key, invoke, runtime, codex_bin,
            int(deadline - monotonic()), host_service=host_service)
        if result is None:
            if previous is None:
                return {"ok": False, **identity, "reason_code": "original_work_turn_requires_recovery"}
            receipt, fresh, observed = previous
            decision = _continuation_decision(receipt, fresh, completed, completed)
            return {"ok": True, **observed, "reason_code": None,
                "loop_disposition": decision["disposition"]}
        observed = {**identity, "last_turn_instance_id": key, "turn_count": completed + 1}
        if result.get("ok") is not True:
            return {"ok": False, **observed,
                "reason_code": str(result.get("error_code") or "original_work_turn_requires_recovery")}
        receipt = ValidatedTurnReceipt.from_execution(result)
        completed += 1
        if receipt.todo_completion:
            return {"ok": True, **observed, "reason_code": None}
        next_key = "transfer-" + hashlib.sha256(f"{identity['turn_instance_id']}:{receipt.turn_key}".encode()).hexdigest()
        if find_loopx_turn_key_by_settlement_identity(runtime,
                **{name: plan[name] for name in ("goal_id", "agent_id", "todo_id")},
                turn_instance_id=next_key) is not None:
            # Recover the original descendant before requesting fresh work.
            # Its successful terminal receipt may already have closed the Task.
            key = next_key
            continue
        # The original native profile and the normal signed Turn plan are read
        # afresh; neither a previous success nor its receipt authorizes a wake.
        plan = effect_runtime_result("coordination.planned_transfer.execution_plan", request, retry_safe=True)["plan"]
        fresh = invoke("turn", "plan", *_common(plan, next_key), "--include-transaction-detail")
        decision = _continuation_decision(receipt, fresh, completed,
            budget["max_turns"] if monotonic() < deadline else completed)
        if decision["disposition"] != LoopDisposition.RUN_NOW.value:
            return {"ok": True, **observed, "reason_code": None,
                "loop_disposition": decision["disposition"]}
        previous = receipt, fresh, observed
        key = next_key
