"""Verify selected tool-output bytes for the private native read journal.

The existing reader/typed selector owns selection semantics. Paths supplied by
tool output cannot introduce a source outside the journal's current captures.
Successful observations are section reads, never full model-retention receipts.
"""
from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from typing import Any

from .decision_capture import read_context_capture_file, read_context_read_reference


def verified_context_selection(state: dict[str, Any], page: dict[str, Any]) -> dict[str, Any] | None:
    from ...cli_commands.quota_capture import read_context_capture
    from ..effect_runtime import MAX_REQUEST_BYTES
    from ..quota.turn_envelope import _turn_envelope_result

    current = state.get("source_recovery")
    if not isinstance(current, dict) or page.get("ok") is not True:
        return None
    references = [entry for key in ("source_recovery", "recovery")
                  if isinstance(entry := state.get(key), dict)]
    reference_file = page.get("reference_file")
    selected = None
    if isinstance(reference_file, str):
        _, metadata = read_context_read_reference(reference_file)
        selected = metadata.get("selection")
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
    if trusted is None or not isinstance(pointers, list) or not pointers or not all(isinstance(p, str) for p in pointers):
        return None
    offset, total = page.get("offset"), page.get("total_bytes")
    content = page.get("content")
    if type(offset) is not int or offset < 0 or type(total) is not int or total <= 0 or not isinstance(content, str):
        return None
    actual = content.encode("utf-8")
    if not actual or offset + len(actual) > total:
        return None
    expected = read_context_capture(Namespace(format="json", offset=offset,
        page_bytes=max(256, len(actual)), max_page_output_bytes=MAX_REQUEST_BYTES,
        reference_file=reference_file, capture_file=None if reference_file else str(physical),
        capture_sha256=None if reference_file else source_digest,
        select=None if reference_file or pointers == [""] and "selected_pointers" not in page else pointers,
        selection_sha256=None if reference_file else page.get("selection_sha256")))
    if (expected.get("ok") is not True or expected.get("total_bytes") != total
            or expected.get("offset") != offset or not expected.get("content", "").encode().startswith(actual)):
        return None
    end = offset + len(actual)
    if ((page.get("eof") is True and (end != total or page.get("next_offset") is not None))
            or (page.get("eof") is False and (end >= total or page.get("next_offset") != end))
            or type(page.get("eof")) is not bool):
        return None
    _, _, source = read_context_capture_file(str(physical), source_digest, private=True)
    sections = _turn_envelope_result("context_read_sections", context=source, select=pointers)["sections"]
    return {**page, "schema_version": "context_capture_page_v1", "sha256": view_digest,
        "selected_pointers": pointers, "source_sha256": current["sha256"], "read_sections": sections}
