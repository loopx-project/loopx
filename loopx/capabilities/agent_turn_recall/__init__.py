"""Public recall exports, loaded when selected by a caller."""

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .catalog_entry import AGENT_TURN_RECALL_SCHEMA_VERSION as AGENT_TURN_RECALL_SCHEMA_VERSION
    from .core import (
        AGENT_TURN_RECALL_CONTEXT_SCHEMA_VERSION as AGENT_TURN_RECALL_CONTEXT_SCHEMA_VERSION,
        AGENT_TURN_SITUATION_SCHEMA_VERSION as AGENT_TURN_SITUATION_SCHEMA_VERSION,
        build_agent_turn_recall_preview as build_agent_turn_recall_preview,
        build_agent_turn_situation as build_agent_turn_situation,
        run_agent_turn_recall as run_agent_turn_recall,
    )
    from .runtime import (
        agent_turn_recall_receipt_path as agent_turn_recall_receipt_path,
        run_configured_agent_turn_recall as run_configured_agent_turn_recall,
        run_configured_agent_turn_recall_fail_open as run_configured_agent_turn_recall_fail_open,
    )

_EXPORTS = {
    "AGENT_TURN_RECALL_CONTEXT_SCHEMA_VERSION": ".core",
    "AGENT_TURN_RECALL_SCHEMA_VERSION": ".catalog_entry",
    "AGENT_TURN_SITUATION_SCHEMA_VERSION": ".core",
    "build_agent_turn_recall_preview": ".core",
    "build_agent_turn_situation": ".core",
    "agent_turn_recall_receipt_path": ".runtime",
    "run_configured_agent_turn_recall": ".runtime",
    "run_configured_agent_turn_recall_fail_open": ".runtime",
    "run_agent_turn_recall": ".core",
}
__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
