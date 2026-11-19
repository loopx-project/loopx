"""Optional provider-neutral semantic preference recall."""
from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .contract import application_receipt as application_receipt
    from .contract import maintenance_receipt as maintenance_receipt
    from .contract import provider_doctor as provider_doctor
    from .contract import recall as recall
    from .reward_memory import run_semantic_preference_reward_memory as run_semantic_preference_reward_memory

# The Turn start hook reads local preferences without selecting a recall provider.
_EXPORTS = {
    "application_receipt": "contract",
    "maintenance_receipt": "contract",
    "provider_doctor": "contract",
    "recall": "contract",
    "run_semantic_preference_reward_memory": "reward_memory",
}
__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f".{module}", __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
