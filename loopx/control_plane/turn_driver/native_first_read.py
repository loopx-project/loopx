"""Pin a smaller initial selection to previously observed native tool output."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from .decision_capture import read_context_read_reference, write_initial_context_read_reference


def observed_first_read_reference(directory: str, target: dict[str, Any], recovery: dict[str, Any],
                                  context: dict[str, Any], plan: dict[str, Any],
                                  initial_read_plan: dict[str, Any] | None = None,
                                  ) -> tuple[dict[str, Any], dict[str, Any] | None] | None:
    """Pin the reduced view and its ordinary fallback from the same capture."""
    initial = initial_read_plan if initial_read_plan and initial_read_plan.get("initial") is True else {}
    pinned = _pin_observed_selection(directory, target, recovery, context, plan, initial.get("plan"))
    if pinned is None:
        return None
    reference, content = pinned
    # Keep this preview in the invocation only. A split remainder is pinned
    # only if the actual output window needs it.
    opening = ({"path": reference["path"], "content": content,
                "reused": plan["reused"], "remaining": plan["linked_remaining"]}
               if "linked_remaining" in plan and "remaining" in initial else None)
    if opening is not None and "text_append" in plan:
        opening["text_append"] = plan["text_append"]
    return reference, opening


def observed_remaining_read_reference(directory: str, target: dict[str, Any], recovery: dict[str, Any],
                                      context: dict[str, Any], opening: dict[str, Any],
                                      ordinary_remaining: dict[str, Any]) -> Path | None:
    """Bind the smaller remainder to the original observation and fallback."""
    remaining, reused = opening["remaining"], opening["reused"]
    if "text_append" in opening and "text_append" not in remaining:
        reused = {pointer: proof for pointer, proof in reused.items()
                  if pointer != opening["text_append"]["pointer"]}
    if not reused:
        return None
    pinned = _pin_observed_selection(directory, target, recovery, context,
        {**remaining, "reused": reused}, ordinary_remaining)
    return Path(pinned[0]["path"]) if pinned is not None else None


def _pin_observed_selection(directory: str, target: dict[str, Any], recovery: dict[str, Any],
                            context: dict[str, Any], plan: dict[str, Any],
                            ordinary_plan: dict[str, Any] | None) -> tuple[dict[str, Any], bytes] | None:
    try:
        path, digest = Path(recovery["path"]), recovery["sha256"]
        observation = {"directory": directory, "target": target, "fields": plan["reused"]}
        if "text_append" in plan:
            append = plan["text_append"]
            if append["pointer"] not in plan["reused"] or ordinary_plan is None:
                return None
            observation["text_append"] = append
        selections = plan.get("selections")
        if ordinary_plan is not None:
            ordinary = ordinary_plan["selections"]
            splices = plan.get("selection_splices")
            if splices is None and (splice := plan.get("selection_splice")) is not None:
                splices = [splice]
            if splices is not None:
                if not isinstance(splices, list) or not splices:
                    return None
                selections, cursor = [], 0
                for splice in splices:
                    start, end, replacement = splice["start"], splice["end"], splice["selections"]
                    if (type(start) is not int or type(end) is not int or not cursor <= start <= end <= len(ordinary)
                            or not isinstance(replacement, list)):
                        return None
                    selections.extend(ordinary[cursor:start])
                    selections.extend(replacement)
                    cursor = end
                selections.extend(ordinary[cursor:])
            fallback, body = write_initial_context_read_reference(path, digest, context, ordinary)
            observation["fallback_reference"] = {"filename": fallback.name,
                "selection_sha256": hashlib.sha256(body).hexdigest()}
        if selections is None:
            return None
        reference, content = write_initial_context_read_reference(path, digest, context, selections,
            observed_read=observation)
        return ({"path": str(reference), "pointer_bytes": sum(len(key.encode()) + 4 for key in plan["reused"])}, content)
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
            return reference, metadata
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
