"""Pin a smaller initial selection to previously observed native tool output."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .decision_capture import write_initial_context_read_reference


def observed_first_read_reference(directory: str, target: dict[str, Any], recovery: dict[str, Any],
                                  context: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any] | None:
    """Write the typed delivery plan once. The host renders it from the immutable source."""
    try:
        reference = write_initial_context_read_reference(Path(recovery["path"]), recovery["sha256"],
            context, plan["selections"],
            observed_read={"directory": directory, "target": target, "fields": plan["reused"]})
        return {"path": str(reference), "pointer_bytes": sum(len(key.encode()) + 4 for key in plan["reused"])}
    except (OSError, ValueError):
        return None  # The ordinary first read remains available.


def observation_current(observation: dict[str, Any], path: Path, digest: str, *,
                        hook_session: str | None = None) -> bool:
    from .codex_context import _private_root, _state

    try:
        state = _state(_private_root(observation["directory"]))
        recovery = state["recovery"]
        target = observation["target"]
        return (target == recovery.get("target") and target["context_epoch"] == state["context_epoch"]
                and target["session_id"] == state["session_id"] == (hook_session or os.environ.get("CODEX_THREAD_ID"))
                and recovery["sha256"] == digest and Path(recovery["path"]).resolve() == path.resolve()
                and bool(observation["fields"]) and all(state.get("consumed_pointers", {}).get(pointer) == expected
                    for pointer, expected in observation["fields"].items()))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False
