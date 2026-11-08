"""Pin a smaller initial selection to previously observed native tool output."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from .decision_capture import read_context_read_reference, write_initial_context_read_reference


def observed_first_read_reference(directory: str, target: dict[str, Any], recovery: dict[str, Any],
                                  context: dict[str, Any], plan: dict[str, Any],
                                  initial_read_plan: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Pin the reduced view and its ordinary fallback from the same capture."""
    try:
        path, digest = Path(recovery["path"]), recovery["sha256"]
        observation = {"directory": directory, "target": target, "fields": plan["reused"]}
        selections = plan.get("selections")
        if initial_read_plan is not None and initial_read_plan.get("initial") is True:
            ordinary = initial_read_plan["plan"]["selections"]
            if splice := plan.get("selection_splice"):
                start, end = splice["start"], splice["end"]
                if type(start) is not int or type(end) is not int or not 0 <= start <= end <= len(ordinary):
                    return None
                selections = [*ordinary[:start], *splice["selections"], *ordinary[end:]]
            fallback, body = write_initial_context_read_reference(path, digest, context, ordinary)
            observation["fallback_reference"] = {"filename": fallback.name,
                "selection_sha256": hashlib.sha256(body).hexdigest()}
        if selections is None:
            return None
        reference, _ = write_initial_context_read_reference(path, digest, context, selections,
            observed_read=observation)
        return {"path": str(reference), "pointer_bytes": sum(len(key.encode()) + 4 for key in plan["reused"])}
    except (OSError, ValueError, KeyError, TypeError):
        return None  # The ordinary first read remains available.


def observed_first_read_fallback(observation: dict[str, Any], path: Path, digest: str) -> tuple[Path, dict[str, Any]] | None:
    """Locate the ordinary pinned selection of this exact capture."""
    fallback = observation.get("fallback_reference") if isinstance(observation, dict) else None
    if not isinstance(fallback, dict):
        return None
    filename = fallback.get("filename")
    if not isinstance(filename, str) or not filename.startswith(path.stem + ".") or not filename.endswith(".reader.json"):
        return None
    try:
        reference, metadata = read_context_read_reference(str(path.with_name(filename)))
        selection = metadata.get("selection")
        source = Path(metadata["path"]).expanduser().absolute()
        if (metadata["sha256"] == digest and source.parent.resolve(strict=True) / source.name == path
                and "observed_read" not in metadata and isinstance(selection, dict)
                and isinstance(fallback.get("selection_sha256"), str)
                and selection.get("sha256") == fallback["selection_sha256"]):
            return reference, selection
    except (OSError, ValueError):
        pass
    return None


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
