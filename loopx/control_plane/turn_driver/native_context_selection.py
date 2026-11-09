"""Verify selected tool-output bytes for the private native read journal.

The existing reader/typed selector owns selection semantics. Paths supplied by
tool output cannot introduce a source outside the journal's current captures.
Successful observations are section reads, never full model-retention receipts.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .decision_capture import (read_context_capture_file, read_context_read_reference,
    context_selection_view)


def _has_selected_tail(state: dict[str, Any], digest: str, total: int) -> bool:
    """A nonfinal page can close coverage only after a verified tail."""
    reads = state.get("section_reads")
    previous = reads.get(digest) if isinstance(reads, dict) else None
    ranges = previous.get("ranges") if isinstance(previous, dict) else None
    return isinstance(ranges, list) and any(
        isinstance(row, list) and len(row) == 2 and row[1] == total for row in ranges
    )


def verified_context_selection(state: dict[str, Any], page: dict[str, Any], *,
                               defer_incomplete_sections: bool = False) -> dict[str, Any] | None:
    from ..quota.turn_envelope import _turn_envelope_result

    current = state.get("source_recovery")
    if not isinstance(current, dict) or page.get("ok") is not True:
        return None
    references = [entry for key in ("source_recovery", "recovery")
                  if isinstance(entry := state.get(key), dict)]
    reference_file = page.get("reference_file")
    selected = None
    observation = None
    if isinstance(reference_file, str):
        _, metadata = read_context_read_reference(reference_file)
        selected = metadata.get("selection")
        observation = metadata.get("observed_read")
        if not isinstance(selected, dict) or selected.get("list_fields") is not False:
            return None
        path, source_digest = metadata["path"], metadata["sha256"]
        pointers, view_digest = selected.get("select"), selected.get("sha256")
    else:
        path = next((entry["path"] for entry in references if entry.get("sha256") == page.get("sha256")), None)
        source_digest = page.get("sha256")
        if path is None or page.get("list_fields"):
            return None
        pointers, view_digest = page.get("selected_pointers"), page.get("selection_sha256")
        if pointers is None:
            # A whole original-source read covers sections without pretending
            # it was the separate native required-view delivery.
            pointers, view_digest = [""], source_digest
    physical = Path(path).expanduser().absolute()
    physical = physical.parent.resolve(strict=True) / physical.name
    trusted = next((entry for entry in references if entry.get("sha256") == source_digest
        and Path(entry["path"]).parent.resolve(strict=True) / Path(entry["path"]).name == physical), None)
    initial_read = selected is not None and selected.get("initial_read") is True
    if trusted is None or (initial_read and pointers is not None) or (not initial_read and (
            not isinstance(pointers, list) or not pointers or not all(isinstance(p, str) for p in pointers))):
        return None
    offset, total = page.get("offset"), page.get("total_bytes")
    content = page.get("content")
    if type(offset) is not int or offset < 0 or type(total) is not int or total <= 0 or not isinstance(content, str):
        return None
    actual = content.encode("utf-8")
    if not actual or offset + len(actual) > total:
        return None
    _, raw, source = read_context_capture_file(str(physical), source_digest, private=True)
    actual_digest = source_digest
    presentation_pointers: set[str] = set()
    if selected is not None or "selected_pointers" in page:
        selection, selected_raw, actual_digest = context_selection_view(source, select=pointers,
            initial_read=initial_read, list_fields=False,
            list_depth=selected.get("list_depth") if selected else None,
            expected_sha256=view_digest, preserve_selection_order=selected is not None and not initial_read,
            present_goal_narrative=bool(selected and selected.get("present_goal_narrative") is True))
        if selected_raw is None:
            return None
        raw = selected_raw
        pointers = [item["pointer"] for item in selection["selections"]]
        presentation_pointers = set(selection.get("presentation_pointers", []))
    if actual_digest != view_digest or len(raw) != total or raw[offset:offset + len(actual)] != actual:
        return None
    end = offset + len(actual)
    if ((page.get("eof") is True and (end != total or page.get("next_offset") is not None))
            or (page.get("eof") is False and (end >= total or page.get("next_offset") != end))
            or type(page.get("eof")) is not bool):
        return None
    observed = {**page, "schema_version": "context_capture_page_v1", "sha256": view_digest,
        "selected_pointers": pointers, "source_sha256": current["sha256"], "read_sections": {}}
    # A single verified prefix still reaches the typed range reducer, but
    # cannot yet cause it to publish section digests.
    if defer_incomplete_sections and not page["eof"] and not _has_selected_tail(state, actual_digest, total):
        return observed
    inherited = {}
    if isinstance(observation, dict):
        from .native_first_read import observation_current

        # The hook already matched its event session/workspace to this journal.
        # Native hook processes need not inherit the shell's CODEX_THREAD_ID.
        if observation_current(observation, physical, source_digest, hook_session=state.get("session_id")):
            inherited = observation["fields"]
    # A display edit is not an observation of its literal source text.
    literal_pointers = [pointer for pointer in pointers if pointer not in presentation_pointers]
    if literal_pointers:
        observed["read_sections"] = _turn_envelope_result("context_read_sections", context=source,
            select=literal_pointers, consumed_pointers=inherited)["sections"]
    return observed
