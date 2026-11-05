"""Pin a smaller initial selection to previously observed native tool output."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from ..effect_runtime import EffectRuntimeRemoteError, EffectRuntimeStartupError
from ..quota.turn_envelope import _turn_envelope_result
from .decision_capture import context_selection_bytes, write_context_read_reference


def observed_first_read_reference(directory: str, target: dict, recovery: dict, context: dict,
                                  pointers: list[str], seen: dict[str, str], output_budget: int) -> Path | None:
    try:
        plan = _turn_envelope_result("observed_first_read", context=context, select=pointers, consumed_pointers=seen)
        if not plan["reused"] or not plan["selections"]:
            return None
        # Keep each bounded page usable even when many outcomes match.
        if sum(len(key.encode()) + 4 for key in plan["reused"]) > output_budget // 4:
            return None
        selected = plan["selections"]
        raw = context_selection_bytes(context, selected, list_fields=False, list_depth=None)
        return write_context_read_reference(Path(recovery["path"]), recovery["sha256"], selection={
            "select": [item["pointer"] for item in selected], "initial_read": False,
            "list_fields": False, "list_depth": None, "sha256": hashlib.sha256(raw).hexdigest()},
            observed_read={"directory": directory, "target": target, "fields": plan["reused"]})
    except (OSError, ValueError, EffectRuntimeRemoteError, EffectRuntimeStartupError):
        return None  # The ordinary first read remains available.


def observation_current(observation: dict[str, Any], path: Path, digest: str) -> bool:
    from .codex_context import _private_root, _state

    try:
        state = _state(_private_root(observation["directory"]))
        recovery = state["recovery"]
        target = observation["target"]
        return (target == recovery.get("target") and target["context_epoch"] == state["context_epoch"]
                and target["session_id"] == state["session_id"] == os.environ.get("CODEX_THREAD_ID")
                and recovery["sha256"] == digest and Path(recovery["path"]).resolve() == path.resolve()
                and bool(observation["fields"]) and all(state.get("consumed_pointers", {}).get(pointer) == expected
                    for pointer, expected in observation["fields"].items()))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False
