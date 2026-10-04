"""LoopX Turn facade; preserve identity without loading unselected hosts."""
from __future__ import annotations

from importlib import import_module
from typing import Any

# Compatibility exports resolve to the original owner objects. Importing a
# parser's host-binding submodule must not initialize the whole executor.
_EXPORTS = {
    "CODEX_CLI_SESSION_SCHEMA_VERSION": "codex_cli",
    "codex_cli_result_schema": "codex_cli",
    "codex_cli_session_binding": "codex_cli",
    "codex_cli_session_id_from_jsonl": "codex_cli",
    "load_codex_cli_session": "codex_cli",
    "run_codex_cli_host": "codex_cli",
    "LOOPX_ITERATION_CONTEXT_POLICY_SCHEMA_VERSION": "driver",
    "LOOPX_TURN_SESSION_BINDING_SCHEMA_VERSION": "driver",
    "LoopXTurnRoute": "driver",
    "build_loopx_turn_plan": "driver",
    "selected_turn_todo": "driver",
    "LOOPX_TURN_HOST_REQUEST_SCHEMA_VERSION": "executor",
    "LOOPX_TURN_JOURNAL_INSPECTION_SCHEMA_VERSION": "executor",
    "LOOPX_TURN_TASK_VALIDATION_SCHEMA_VERSION": "executor",
    "build_loopx_turn_command_validator": "executor",
    "build_loopx_turn_host_request": "executor",
    "inspect_loopx_turn_journal": "executor",
    "normalize_host_argv": "executor",
    "reward_memory_reflection_digest": "executor",
    "run_loopx_turn_once": "executor",
    "validate_loopx_turn_host_result": "executor",
    "load_loopx_turn_plan_from_journal": "journal_store",
    "load_turn_journal": "journal_store",
    "turn_journal_path": "journal_store",
    "LOOPX_TURN_MANAGED_STEP_SCHEMA_VERSION": "managed_step",
    "decide_managed_step": "managed_step",
    "BOUNDED_TURN_BUDGET_SCHEMA_VERSION": "loop_controller",
    "LOOP_CONTROLLER_DISPOSITION_SCHEMA_VERSION": "loop_controller",
    "VALIDATED_TURN_RECEIPT_SCHEMA_VERSION": "loop_controller",
    "BoundedTurnBudget": "loop_controller",
    "LoopDisposition": "loop_controller",
    "ValidatedTurnReceipt": "loop_controller",
    "decide_loop_disposition": "loop_controller",
    "LOOPX_TURN_EXECUTION_SCHEMA_VERSION": "transaction",
    "LOOPX_TURN_RESULT_SCHEMA_VERSION": "transaction",
    "LoopXTurnResultKind": "transaction",
    "build_loopx_turn_transaction_plan": "transaction",
    "loopx_turn_execution_committed": "transaction",
    "loopx_turn_execution_has_durable_effects": "transaction",
    "loopx_turn_execution_recovery_required": "transaction",
    "validate_loopx_turn_receipt": "transaction",
    "project_turn_route": "turn_contract_generated",
    "TurnRecoveryBlockedError": "recovery",
}


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f".{module}", __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))


__all__ = [
    "BOUNDED_TURN_BUDGET_SCHEMA_VERSION",
    "CODEX_CLI_SESSION_SCHEMA_VERSION",
    "LOOPX_TURN_EXECUTION_SCHEMA_VERSION",
    "LOOPX_ITERATION_CONTEXT_POLICY_SCHEMA_VERSION",
    "LOOPX_TURN_HOST_REQUEST_SCHEMA_VERSION",
    "LOOPX_TURN_JOURNAL_INSPECTION_SCHEMA_VERSION",
    "LOOPX_TURN_MANAGED_STEP_SCHEMA_VERSION",
    "LOOPX_TURN_RESULT_SCHEMA_VERSION",
    "LOOPX_TURN_SESSION_BINDING_SCHEMA_VERSION",
    "LOOPX_TURN_TASK_VALIDATION_SCHEMA_VERSION",
    "LOOP_CONTROLLER_DISPOSITION_SCHEMA_VERSION",
    "VALIDATED_TURN_RECEIPT_SCHEMA_VERSION",
    "BoundedTurnBudget",
    "LoopDisposition",
    "LoopXTurnResultKind",
    "LoopXTurnRoute",
    "TurnRecoveryBlockedError",
    "ValidatedTurnReceipt",
    "build_loopx_turn_command_validator",
    "build_loopx_turn_host_request",
    "build_loopx_turn_plan",
    "build_loopx_turn_transaction_plan",
    "codex_cli_result_schema",
    "codex_cli_session_binding",
    "codex_cli_session_id_from_jsonl",
    "decide_loop_disposition",
    "project_turn_route",
    "decide_managed_step",
    "load_codex_cli_session",
    "inspect_loopx_turn_journal",
    "load_loopx_turn_plan_from_journal",
    "load_turn_journal",
    "turn_journal_path",
    "loopx_turn_execution_committed",
    "loopx_turn_execution_has_durable_effects",
    "loopx_turn_execution_recovery_required",
    "normalize_host_argv",
    "reward_memory_reflection_digest",
    "run_codex_cli_host",
    "run_loopx_turn_once",
    "selected_turn_todo",
    "validate_loopx_turn_host_result",
    "validate_loopx_turn_receipt",
]
