from __future__ import annotations

from importlib import import_module
from typing import Any

# Importing the Turn registration or contract is not a request to run the
# inspector. Explicit compatibility exports still resolve to their owners.
_EXPORTS = {
    "anti_entropy_inspect": "agent_tool",
    "ANTI_ENTROPY_CATALOG_ENTRY": "catalog_entry",
    "run_anti_entropy_verify": "cli",
    "ANTI_ENTROPY_CAPABILITY_ID": "contract",
    "ANTI_ENTROPY_RECEIPT_SCHEMA_VERSION": "contract",
    "ANTI_ENTROPY_RETIREMENT_BASIS_SCHEMA_VERSION": "contract",
    "AntiEntropyFinding": "contract",
    "AntiEntropyReceipt": "contract",
    "RetirementBasis": "contract",
    "ANTI_ENTROPY_INSPECT_TOOL_DESCRIPTOR": "feedback",
    "build_anti_entropy_turn_context": "feedback",
    "run_anti_entropy_feedback_cycle": "feedback",
    "audit_ghost_code": "oracles",
    "evaluate_diff_hygiene": "oracles",
    "verify_delete_first": "oracles",
    "anti_entropy_context_registration": "turn_context",
    "anti_entropy_context_result": "turn_context",
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
    "ANTI_ENTROPY_CAPABILITY_ID",
    "ANTI_ENTROPY_CATALOG_ENTRY",
    "ANTI_ENTROPY_INSPECT_TOOL_DESCRIPTOR",
    "ANTI_ENTROPY_RECEIPT_SCHEMA_VERSION",
    "ANTI_ENTROPY_RETIREMENT_BASIS_SCHEMA_VERSION",
    "AntiEntropyFinding",
    "AntiEntropyReceipt",
    "RetirementBasis",
    "anti_entropy_inspect",
    "audit_ghost_code",
    "build_anti_entropy_turn_context",
    "evaluate_diff_hygiene",
    "run_anti_entropy_feedback_cycle",
    "run_anti_entropy_verify",
    "verify_delete_first",
    "anti_entropy_context_registration",
    "anti_entropy_context_result",
]
