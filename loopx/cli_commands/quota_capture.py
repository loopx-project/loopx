"""Opt-in local transport capture; quota and envelope owners remain unchanged."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import socket
import stat
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..control_plane.content_digest import BARE_SHA256_PATTERN
from ..control_plane.effect_runtime import EffectRuntimeRejected, EffectRuntimeRemoteError, EffectRuntimeStartupError
from ..control_plane.quota.error_codes import QuotaCommandValidationError, QuotaDecisionTransportError
from ..control_plane.turn_driver.decision_capture import (
    context_index_fields as _context_index_fields,
    context_selection_bytes as _context_selection_bytes,
    context_selection_view as _context_selection_view,
    validate_handoff_socket, CONTEXT_CAPTURE_READ_GUIDANCE, CONTEXT_CAPTURE_PAGE_GUIDANCE, context_capture_read_command,
    CONTEXT_CAPTURE_NAVIGATION_GUIDANCE,
    read_context_capture_file as _read_capture,
    read_context_read_reference, write_context_read_reference, private_capture_uid, read_turn_observation_text,
)
from ..control_plane.turn_driver.native_context_profile import (
    NATIVE_CONTEXT_PAGE_BYTES, NATIVE_CONTEXT_PAGE_OUTPUT_BYTES, native_context_page_budget,
)


def quota_context_surface(payload: dict[str, Any], args: argparse.Namespace) -> str:
    """Select the presentation owner from the captured decision, independent of continuity."""
    if payload.get("schema_version") == "loopx_turn_envelope_v1":
        return "complete_turn"
    return "turn_envelope" if getattr(args, "turn_envelope", False) else "quota_packet"


def native_context_activation(args: argparse.Namespace) -> dict[str, Any]:
    from ..control_plane.turn_driver.codex_context import context_delivery_for_arguments

    try:
        return context_delivery_for_arguments(args)
    except ValueError as exc:
        raise QuotaCommandValidationError(str(exc)) from exc


def _capture_page(path: Path, raw: bytes, digest: str, *, offset: int, size: int,
                  output_limit: int | None, instruction: bool,
                  presentation: dict[str, Any] | None = None,
                  next_page_bytes: int | None = None,
                  next_page_output_bytes: int | None = None,
                  select: list[str] | None = None,
                  selection_sha256: str | None = None,
                  list_fields: bool = False,
                  list_depth: int | None = None,
                  initial_read: bool = False,
                  reference_file: Path | None = None,
                  reader_argv: list[str] | None = None) -> dict[str, Any]:
    end = min(offset + size, len(raw))
    while end < len(raw) and raw[end] & 0xC0 == 0x80:
        end -= 1
    content = raw[offset:end].decode("utf-8")

    def page(text: str) -> dict[str, Any]:
        cursor = offset + len(text.encode("utf-8"))
        result: dict[str, Any] = {**(presentation or {}), "ok": True, "schema_version": "context_capture_page_v1", "sha256": digest,
            "offset": offset, "next_offset": cursor if cursor < len(raw) else None,
            "total_bytes": len(raw), "eof": cursor == len(raw), "content": text}
        if select is not None:
            result["selection_sha256"] = selection_sha256
            # The new initial view already names addresses in content; its
            # short continuation pins that view's digest. Existing explicit
            # selectors retain their original metadata and continuation.
            if not initial_read:
                result["selected_pointers"] = select
            if list_fields:
                result["list_fields"] = True
            result["read_instruction"] = ("This is selected detail from the original capture, not a complete context read. "
                "Offsets and EOF describe only this selected view. Follow next_command to finish it; "
                "keep both digests. Detail delivery does not establish a full-context baseline or grant authority.")
        elif instruction or (offset == 0 and output_limit is not None):
            # A digest-bound native reference may be read in a fresh shell
            # without its session lookup. Deliver paging obligations with the
            # first bounded page rather than repeating them in file metadata.
            # Historical unbounded readers retain their exact wire shape.
            result["read_instruction"] = CONTEXT_CAPTURE_PAGE_GUIDANCE
        if reference_file is not None and select is not None:
            # Selected pages have never been full-input receipts. Give this
            # compact transport a distinct schema so old observers cannot
            # mistake an omitted digest for a full capture observation.
            result["schema_version"] = "context_capture_selection_page_v1"
            for key in ("sha256", "selection_sha256", "selected_pointers"):
                result.pop(key, None)
            result["reference_file"] = str(reference_file)
            result["read_instruction"] = ("Selected detail only. Follow next_command for contiguous bytes; "
                "the immutable reference pins both complete digests, which the CLI verifies on every page. "
                "Offsets and EOF describe only this selected view. "
                "EOF is not full-context retention, authority or freshness.")
        if cursor < len(raw):
            result["next_command"] = context_capture_read_command(path, digest, offset=cursor, page_bytes=next_page_bytes or size,
                max_page_output_bytes=next_page_output_bytes or output_limit,
                select=None if initial_read or reference_file is not None else select,
                selection_sha256=selection_sha256 if reference_file is None else None,
                list_fields=list_fields if reference_file is None else False,
                list_depth=list_depth if reference_file is None else None,
                initial_read=initial_read if reference_file is None else False, reference_file=reference_file,
                reader_argv=reader_argv)
        return result

    def fits(result: dict[str, Any]) -> bool:
        # Match cli_runtime.print_payload exactly, including print's newline.
        return output_limit is None or len(json.dumps(result, ensure_ascii=False,
            separators=(",", ":")).encode("utf-8")) + 1 <= output_limit

    result = page(content)
    if fits(result):
        return result
    # EOF drops next_command: test it above before searching the monotone
    # non-EOF prefix sizes. Never return a zero-progress continuation.
    low, high = 1, len(content) - (end == len(raw))
    best = None
    while low <= high:
        middle = (low + high) // 2
        candidate = page(content[:middle])
        if fits(candidate):
            best, low = candidate, middle + 1
        else:
            high = middle - 1
    if best is None:
        raise ValueError("max-page-output-bytes cannot fit page metadata and one UTF-8 character; increase the limit for this same capture")
    return best








def read_context_capture(args: argparse.Namespace, *, presentation: dict[str, Any] | None = None,
                         next_page_bytes: int | None = None,
                         next_page_output_bytes: int | None = None,
                         reader_argv: list[str] | None = None) -> dict[str, Any]:
    """Bounded local IO over immutable JSON, not a new context/authority owner.

    Verify the complete byte digest on every page. The cursor addresses bytes
    in that capture or its separately digest-bound selection, so a replacement
    cannot silently mix two decisions or detail views.
    EOF describes this page, never a model's complete-consumption receipt.
    """
    try:
        digest = getattr(args, "capture_sha256", None)
        value = getattr(args, "capture_file", None)
        reference_file = getattr(args, "reference_file", None)
        pinned_selection = None
        if reference_file:
            if value or digest or getattr(args, "codex_context_dir", None) or getattr(args, "codex_context_root", None):
                raise ValueError("reference-file cannot be combined with capture or native session lookup")
            if args.format != "json":
                raise ValueError("reference-file requires JSON output")
            reference_file, reference = read_context_read_reference(reference_file)
            value, digest = reference["path"], reference["sha256"]
            pinned_selection = reference.get("selection")
        offset, size = args.offset, args.page_bytes
        output_limit = getattr(args, "max_page_output_bytes", None)
        if output_limit is not None and (type(output_limit) is not int or output_limit <= 0
                or getattr(args, "format", None) != "json"):
            raise ValueError("max-page-output-bytes requires JSON output and a positive integer")
        if size is None:
            if args.format == "json":
                if output_limit is None:
                    output_limit = NATIVE_CONTEXT_PAGE_OUTPUT_BYTES
                size = max(8192, min(NATIVE_CONTEXT_PAGE_BYTES, output_limit))
            else:
                size = 4096
        from ..control_plane.turn_driver.codex_context import codex_context_directory

        context_dir = codex_context_directory(directory=getattr(args, "codex_context_dir", None),
            root=getattr(args, "codex_context_root", None))
        if context_dir:
            if value or digest or offset != 0:
                raise ValueError("session lookup starts at offset zero without capture-file or capture-sha256; follow next_command afterwards")
            from ..control_plane.turn_driver.codex_context import read_codex_context_reference

            value, digest = read_codex_context_reference(context_dir,
                goal_id=getattr(args, "goal_id", None), agent_id=getattr(args, "agent_id", None))
        if not isinstance(digest, str) or BARE_SHA256_PATTERN.fullmatch(digest) is None or not value:
            raise ValueError("context-read requires the original capture file and its exact SHA-256")
        from ..control_plane.effect_runtime import MAX_REQUEST_BYTES

        # Default JSON reads share the native large-page profile. Explicit
        # smaller budgets can recover a truncated range of this same capture.
        maximum = max(8192, min(output_limit or 8192, MAX_REQUEST_BYTES))
        if type(offset) is not int or offset < 0 or type(size) is not int or not 256 <= size <= maximum:
            raise ValueError("context-read requires a nonnegative byte offset and page-bytes between 256 and 8192; "
                "larger pages require a sufficient max-page-output-bytes within the capture limit")
        path, raw, payload = _read_capture(value, digest)
        list_fields = getattr(args, "list_fields", False)
        list_depth = getattr(args, "list_depth", None)
        if list_depth is not None and (not list_fields or type(list_depth) is not int or not 1 <= list_depth <= 8):
            raise ValueError("list-depth requires --list and an integer from 1 to 8")
        selectors = getattr(args, "select", None)
        initial_read = getattr(args, "initial_read", False)
        selection_digest = getattr(args, "selection_sha256", None)
        if pinned_selection is not None:
            if selectors is not None or initial_read or list_fields or list_depth is not None or selection_digest is not None:
                raise ValueError("a pinned selection reference cannot change selectors; use the original capture reference")
            if (set(pinned_selection) != {"select", "initial_read", "list_fields", "list_depth", "sha256"}
                    or type(pinned_selection["initial_read"]) is not bool or type(pinned_selection["list_fields"]) is not bool
                    or not isinstance(pinned_selection["sha256"], str)
                    or BARE_SHA256_PATTERN.fullmatch(pinned_selection["sha256"]) is None):
                raise ValueError("reader selection reference is incomplete or unsupported")
            if observation := reference.get("observed_read"):
                from ..control_plane.turn_driver.native_first_read import observation_current

                if not observation_current(observation, path, digest):
                    if offset:
                        return {"ok": False, "reason": "Observed first-read scope changed during paging.",
                            "recovery_command": context_capture_read_command(path, digest, initial_read=True,
                                page_bytes=size, max_page_output_bytes=output_limit, reader_argv=reader_argv)}
                    # Restart in the ordinary complete view of this same capture.
                    # Subsequent pages must pin that view even if the journal changes.
                    pinned_selection, initial_read = None, True
                    try:
                        reference_file = write_context_read_reference(path, digest)
                    except OSError:
                        reference_file = None
                elif offset == 0:
                    presentation = {**(presentation or {}), "previously_delivered_fields": list(observation["fields"]),
                        "field_recovery": "These unchanged fields still apply. If needed or uncertain, append --select "
                            "and the field pointer to recovery.command for its complete current body."}
        if pinned_selection is not None:
            selectors, initial_read = pinned_selection["select"], pinned_selection["initial_read"]
            list_fields, list_depth = pinned_selection["list_fields"], pinned_selection["list_depth"]
            selection_digest = pinned_selection["sha256"]
            if list_depth is not None and (not list_fields or type(list_depth) is not int or not 1 <= list_depth <= 8):
                raise ValueError("reader selection list-depth must be between 1 and 8 and requires list")
        if initial_read and (selectors is not None or list_fields or list_depth is not None):
            raise ValueError("initial-read cannot be combined with select or list")
        if list_fields and selectors is None:
            selectors = [""]
        if initial_read and offset > 0 and selection_digest is None:
            raise ValueError("initial-read continuation requires selection-sha256; follow next_command")
        if selectors is None and not initial_read and selection_digest is not None:
            raise ValueError("selection-sha256 requires --select")
        if selectors is not None or initial_read:
            if getattr(args, "format", None) != "json":
                raise ValueError("selected context-read requires JSON output")
            if offset > 0 and selection_digest is None:
                raise ValueError("selected continuation requires selection-sha256; follow next_command")
            pinned_explicit = pinned_selection is not None and not initial_read
            plan, selected_raw, expected = _context_selection_view(payload, select=selectors,
                initial_read=initial_read, list_fields=list_fields, list_depth=list_depth,
                expected_sha256=selection_digest, preserve_selection_order=pinned_explicit)
            if rejection := plan.get("rejected_selection"):
                # TS alone resolves the nearest container. Reuse the original
                # escaped tokens, without parsing diagnostic prose or traversing
                # the payload again in Python. A failed batch delivers no body.
                pointer = selectors[rejection["argument_index"] - 1]
                parent = "/".join(pointer.split("/")[:rejection["parent_token_count"] + 1])
                command = context_capture_read_command(path, digest, select=[parent], list_fields=True,
                    page_bytes=next_page_bytes or size,
                    max_page_output_bytes=next_page_output_bytes or output_limit,
                    reader_argv=reader_argv)
                return {"ok": False, "schema_version": "context_capture_page_v1",
                    "error_kind": "request_rejected", "diagnostic_code": rejection["code"],
                    "reason": rejection["message"], "recovery_command": command,
                    "recovery": "Use recovery_command to --list the nearest verified container in the same capture, "
                        "then retry the corrected batch. No values were delivered and no guard, admission or spend was performed."}
            if selected_raw is None or expected is None:
                raise ValueError("selected view is unavailable")
            raw = selected_raw
            selection_digest = expected
            selectors = [item["pointer"] for item in plan["selections"]]
            if reference_file is not None and pinned_selection is None:
                try:
                    # Pin the resolved addresses, not the initial navigation
                    # recipe. Existing explicit readers can replay these exact
                    # bytes after a navigation change, with both digests checked.
                    reference_file = write_context_read_reference(path, digest, selection={
                        "select": selectors, "initial_read": False,
                        "list_fields": list_fields, "list_depth": list_depth, "sha256": expected})
                except OSError:
                    # A verified read must not require a writable capture root.
                    # Legacy cursors pin the same capture, selection and digest.
                    reference_file = None
        if offset > len(raw) or (offset < len(raw) and raw[offset] & 0xC0 == 0x80):
            raise ValueError("capture offset is outside the file or not a UTF-8 boundary; use the returned next offset")
        return _capture_page(path, raw, digest, offset=offset, size=size,
            output_limit=output_limit, instruction=bool(context_dir),
            presentation=presentation, next_page_bytes=next_page_bytes,
            next_page_output_bytes=next_page_output_bytes, select=selectors, selection_sha256=selection_digest,
            list_fields=list_fields, list_depth=list_depth, initial_read=initial_read, reference_file=reference_file,
            reader_argv=reader_argv)
    except (OSError, ValueError, RecursionError, EffectRuntimeRemoteError, EffectRuntimeStartupError) as exc:
        # Recovery is presentation-only: keep failures separate from quota's
        # collection error path and retain the existing receipt/capture.
        result = {"ok": False, "schema_version": "context_capture_page_v1",
            "reason": str(exc) if isinstance(exc, (ValueError, EffectRuntimeRemoteError, EffectRuntimeStartupError))
                else "original capture could not be read",
            "recovery": "Restore the original digest-bound capture; no guard, admission or spend was performed."}
        if isinstance(exc, (EffectRuntimeRemoteError, EffectRuntimeStartupError)):
            result["diagnostic_code"] = exc.diagnostic_code
            result["recovery"] = ("Resolve the reported runtime failure, then retry reading the same digest-bound capture; "
                "no guard, admission or spend was performed.")
        if isinstance(exc, EffectRuntimeRemoteError):
            result["error_kind"] = exc.error_kind
            if exc.error_kind == "request_rejected":
                result["recovery"] = ("Correct the rejected read request; for unknown fields use --list on a known parent. "
                    "Keep the original capture and digest; no guard, admission or spend was performed.")
        return result


def _capture_read_reference(value: dict[str, Any], *, page_bytes: int,
                            output_bytes: int, reader_argv: list[str] | None = None) -> dict[str, str]:
    """Keep file identity and the budgeted reader at the transport boundary.

    Navigation lives on the outer view; complete paging obligations accompany
    the actual reader response. Neither belongs in every nested file reference.
    """
    return {"path": value["path"], "sha256": value["sha256"],
        "command": context_capture_read_command(Path(value["path"]), value["sha256"],
            page_bytes=page_bytes, max_page_output_bytes=output_bytes, reader_argv=reader_argv)}


def model_context_reference(view: dict[str, Any], *, reader_argv: list[str] | None = None) -> dict[str, Any]:
    """Present native navigation; machine references and journal bindings stay full.

    The descriptor holds complete digests locally. Legacy explicit readers and
    full-page observation receipts retain their original transport shape.
    """
    page_bytes, output_bytes = native_context_page_budget()
    # Retain an explicitly smaller caller budget from the generated command.
    words = shlex.split(view["command"])
    for option in ("--page-bytes", "--max-page-output-bytes"):
        if option in words:
            if option == "--page-bytes":
                page_bytes = int(words[words.index(option) + 1])
            else:
                output_bytes = int(words[words.index(option) + 1])

    def reference(value: dict[str, Any], *, navigation: bool = False) -> dict[str, str]:
        path = Path(value["path"])
        reader = write_context_read_reference(path, value["sha256"], navigation=value if navigation else None)
        return {"reference_file": str(reader), "command": context_capture_read_command(path, value["sha256"],
            page_bytes=page_bytes, max_page_output_bytes=output_bytes, reference_file=reader,
            reader_argv=reader_argv)}

    result = {"ok": True, "schema_version": "context_capture_navigation_v1", "mode": view["mode"],
        **reference(view, navigation=True), "total_bytes": view["total_bytes"], "recovery": reference(view["recovery"]),
        "instruction": view["instruction"]}
    if "context_delivery" in view:
        result["context_delivery"] = view["context_delivery"]
    if "baseline_reference" in view:
        result["baseline_reference"] = reference(view["baseline_reference"])
        result["continuity_basis"] = view["continuity_basis"]
    if "first_read_command" in view:
        recovery = view["recovery"]
        if view.get("first_read_reference"):
            # The producer already pinned this same initial selection. Keep
            # that existing cursor instead of asking the reader to replan it.
            result["first_read_command"] = context_capture_read_command(Path(recovery["path"]), recovery["sha256"],
                page_bytes=page_bytes, max_page_output_bytes=output_bytes,
                reference_file=Path(view["first_read_reference"]), reader_argv=reader_argv)
        else:
            result["first_read_command"] = context_capture_read_command(Path(recovery["path"]), recovery["sha256"],
                page_bytes=page_bytes, max_page_output_bytes=output_bytes, initial_read=True,
                reference_file=Path(result["recovery"]["reference_file"]), reader_argv=reader_argv)
    result["instruction"] += (" Append --select '<pointer>' or --list to recovery.command for current fields. "
        "Reader references keep complete digests locally; the CLI checks them on every page. "
        "A reference or digest grants no authority or freshness.")
    if len(_native_output_bytes(result)) > min(output_bytes, 7900):
        return view  # Preserve usable legacy recovery under a very small ceiling.
    return result


def _native_context_reference(projected: dict[str, Any], reference: dict[str, Any], *,
                              mode: str, current: dict[str, Any], current_document: dict[str, Any],
                              total_bytes: int, metadata: dict[str, Any] | None,
                              progressive_enabled: bool, field_index: list[dict[str, Any]] | None,
                              initial_read_pointers: list[str] | None,
                              initial_read_reference: Path | None,
                              page_bytes: int, output_bytes: int,
                              page_attached: bool = False,
                              reader_argv: list[str] | None = None) -> dict[str, Any]:
    """Build the bounded native reference without publishing or registering it."""
    target = projected.get("target") if projected.get("schema_version") == "context_delivery_v1" else None
    reference = _capture_read_reference(reference, page_bytes=page_bytes, output_bytes=output_bytes,
        reader_argv=reader_argv)
    result = {"ok": True, **(metadata or {}), "mode": mode, **reference,
        **({"target": target} if target else {})}
    if isinstance(result.get("recovery"), dict):
        result["recovery"] = _capture_read_reference(result["recovery"],
            page_bytes=page_bytes, output_bytes=output_bytes, reader_argv=reader_argv)
    current_path = Path(current["path"])
    result.setdefault("recovery", _capture_read_reference(current,
        page_bytes=page_bytes, output_bytes=output_bytes, reader_argv=reader_argv))
    if mode != "full" and projected.get("continuity_basis") == "captured_file":
        result["continuity_basis"] = "captured_file"
        result["baseline_reference"] = dict(projected["baseline_reference"])
    result["total_bytes"] = total_bytes
    result["index_command"] = context_capture_read_command(current_path, current["sha256"],
        page_bytes=min(page_bytes, 24000), max_page_output_bytes=min(output_bytes, 24000), list_fields=True,
        reader_argv=reader_argv)
    result["select_command"] = context_capture_read_command(current_path, current["sha256"],
        page_bytes=page_bytes, max_page_output_bytes=output_bytes, reader_argv=reader_argv) + " --select '<pointer>'"
    # A complete scalar preview helps orientation without inventing a second
    # decision owner. Oversized or structured values remain discoverable in the
    # index; no prose is truncated or treated as an authorization summary.
    result["overview"] = {key: value for key, value in current_document.items()
        if value is None or isinstance(value, (str, bool, int, float))
        and len(json.dumps(value, ensure_ascii=False).encode("utf-8")) <= 240}
    result["instruction"] = CONTEXT_CAPTURE_NAVIGATION_GUIDANCE if progressive_enabled else CONTEXT_CAPTURE_READ_GUIDANCE
    if mode != "full":
        result["instruction"] += (" command reads the delta; baseline_reference identifies its exact saved base when file-backed. "
            "recovery.command reads current facts directly without that baseline.")
    # The reference must fit even under an explicit small caller ceiling.
    def fits() -> bool:
        return len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()) + 1 <= min(output_bytes, 7900)
    if initial_read_pointers:
        overview = result.pop("overview")
        instruction = result["instruction"]
        if any(pointer.endswith("/vision_authoring/rule") for pointer in initial_read_pointers):
            instruction = ("Before composing a Vision, select the complete vision_authoring object "
                "at the displayed step in this capture. " + instruction)
        first_read = ("Read the top-level content and follow next_command if eof is false. "
            "Use first_read_command only to recover missing initial context from this capture"
            if page_attached else
            "Start with first_read_command for current facts and source navigation from this capture")
        result["instruction"] = (first_read + ", without reading an older baseline. "
            "Read each complete optional attachment from its captured step before using it. " + instruction)
        result["first_read_command"] = context_capture_read_command(current_path, current["sha256"],
            page_bytes=page_bytes, max_page_output_bytes=output_bytes, initial_read=True, reader_argv=reader_argv)
        if not fits():
            result.pop("first_read_command")  # A complete optional selection or the existing generic recovery.
            result["overview"] = overview
            result["instruction"] = instruction
        elif initial_read_reference is not None:
            result["first_read_reference"] = str(initial_read_reference)
            if not fits():
                result.pop("first_read_reference")  # Optional cursor must not displace the original recipe.
    # Prefer the concrete initial read over a broad scalar preview/root index.
    # Otherwise preserve the complete root index, bound to current recovery,
    # never a delta's patch keys. All deeper discovery remains available.
    if field_index is not None and "first_read_command" not in result:
        result["field_index"] = field_index
        if not fits():
            result.pop("field_index")  # Optional metadata must never displace recovery or instructions.
    if not fits():
        result.pop("overview")
    if not fits():
        result.pop("select_command")  # --select also works on the current recovery command.
    if not fits() and "baseline_reference" in result:
        result["baseline_reference"].pop("command", None)  # Exact path/hash remain usable.
    if not fits():
        result.pop("index_command")
        result["instruction"] += " Append --list or --select to recovery.command to discover or read current fields."
    if not fits():
        raise QuotaDecisionTransportError("Native context reference exceeds the output limit; "
            "read the saved original decision and receipt without rerunning admission.")
    return result


def _native_output_bytes(value: dict[str, Any]) -> bytes:
    """Match immutable capture publication and compact CLI JSON, including LF."""
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def _native_context_read_bytes(reference: dict[str, Any], raw: bytes, *,
                               page_bytes: int, output_bytes: int,
                               stop_after: int | None = None,
                               reader_argv: list[str] | None = None) -> int:
    """Count the reference plus its explicit-file reader before publication.

    Stop once this candidate cannot win. This measures one complete wire read,
    not model tokens, retention or the caller's later selective/recovery reads.
    """
    from ..command_invocation import current_loopx_cli_argv
    from ..control_plane.effect_runtime import MAX_REQUEST_BYTES

    if len(raw) > MAX_REQUEST_BYTES:
        raise QuotaDecisionTransportError("Native context capture exceeds the existing reader size limit.")
    # The reader resolves parent aliases before emitting continuation commands.
    # Resolve only the parent: a candidate delta has not been published yet.
    path = Path(reference["path"]).expanduser().absolute()
    path = path.parent.resolve(strict=True) / path.name
    total, offset = len(_native_output_bytes(reference)), 0
    reader_argv = current_loopx_cli_argv() if reader_argv is None else reader_argv
    while True:
        page = _capture_page(path, raw, reference["sha256"],
            offset=offset, size=page_bytes, output_limit=output_bytes, instruction=False,
            reader_argv=reader_argv)
        total += len(_native_output_bytes(page))
        if page["eof"] or (stop_after is not None and total > stop_after):
            return total
        offset = page["next_offset"]


def native_context_capture_view(projected: dict[str, Any], directory: Path, *,
                                context_directory: str | None = None,
                                progressive_enabled: bool = True, hook_session: str | None = None,
                                captured_recovery: dict[str, Any] | None = None,
                                metadata: dict[str, Any] | None = None,
                                max_page_output_bytes: int | None = None,
                                page_attached: bool = False,
                                reader_argv: list[str] | None = None,
                                initial_read_plan: dict[str, Any] | None = None) -> dict[str, Any]:
    """Publish the cheaper readable native transport for an already-decided view.

    TS owns valid deltas and their bindings. This IO adapter can only fall back
    to the already-published current full capture; it never reconstructs content,
    admits work or treats saved bytes as retained model input.
    """
    from ..control_plane.turn_driver.decision_capture import write_decision_capture

    if max_page_output_bytes is not None and max_page_output_bytes <= 0:
        raise QuotaDecisionTransportError("Native context output limit must be positive.")
    page_bytes, output_bytes = native_context_page_budget()
    if max_page_output_bytes is not None:
        output_bytes = min(output_bytes, max_page_output_bytes)
        page_bytes = min(page_bytes, output_bytes)
    typed = projected.get("schema_version") == "context_delivery_v1"
    mode = projected["mode"] if typed else "full"
    ancestry = projected.get("ancestry", 0)
    publish_wire = captured_recovery is None and not (typed and mode == "full")
    wire = directory / "delivery.json"
    raw = _native_output_bytes(projected) if publish_wire else b""
    reference = ({"path": str(wire), "sha256": hashlib.sha256(raw).hexdigest()}
        if publish_wire else captured_recovery or projected["recovery"])
    current = captured_recovery or (projected.get("recovery") if typed else None) or reference
    current_raw = Path(current["path"]).read_bytes() if typed or captured_recovery is not None else raw
    if hashlib.sha256(current_raw).hexdigest() != current["sha256"]:
        raise QuotaDecisionTransportError("Current context capture changed; restore its original bytes without rerunning admission.")
    if initial_read_plan is not None and initial_read_plan.get("capture") != {
            key: current[key] for key in ("path", "sha256")}:
        raise QuotaDecisionTransportError("Native read plan does not match the current immutable capture.")
    current_document = json.loads(current_raw)
    if not publish_wire:
        raw = current_raw

    from ..control_plane.quota.turn_envelope import _turn_envelope_result

    # The same pure TS selector plans initial navigation and the optional index
    # once for current facts, not once per full/delta transport candidate.
    field_index = None
    initial_read_pointers = None
    initial_read_reference = None

    def pin_initial_selection(plan: dict[str, Any]) -> None:
        nonlocal initial_read_pointers, initial_read_reference
        selections = plan["selections"]
        initial_read_pointers = [item["pointer"] for item in selections]
        selected = _context_selection_bytes(current_document, selections, list_fields=False, list_depth=None)
        try:
            initial_read_reference = write_context_read_reference(Path(current["path"]), current["sha256"], selection={
                "select": initial_read_pointers, "initial_read": False, "list_fields": False, "list_depth": None,
                "sha256": hashlib.sha256(selected).hexdigest()})
        except OSError:
            # Read-only capture roots keep the existing initial recipe;
            # metadata is optional and never a read/authority receipt.
            pass

    if progressive_enabled and initial_read_plan is not None:
        try:
            initial, plan = initial_read_plan["initial"], initial_read_plan["plan"]
            if initial:
                pin_initial_selection(plan)
            else:
                field_index = _context_index_fields(current_document, plan["selections"][0]["children"])
        except (EffectRuntimeRemoteError, EffectRuntimeStartupError):
            # Optional discovery cannot invalidate the immutable file reference.
            pass
    elif progressive_enabled:
        try:
            plan = _turn_envelope_result("context_select", context=current_document, select=[""], list_fields=True)
        except (EffectRuntimeRemoteError, EffectRuntimeStartupError):
            pass
        else:
            field_index = _context_index_fields(current_document, plan["selections"][0]["children"])
            initial_read_pointers = plan.get("initial_read_pointers")

    def view(kind: str, ref: dict[str, Any], body: bytes) -> dict[str, Any]:
        return _native_context_reference(projected, ref, mode=kind, current=current,
            current_document=current_document, total_bytes=len(body), metadata=metadata,
            field_index=field_index, initial_read_pointers=initial_read_pointers,
            progressive_enabled=progressive_enabled, initial_read_reference=initial_read_reference,
            page_bytes=page_bytes, output_bytes=output_bytes, page_attached=page_attached,
            reader_argv=reader_argv)

    if typed and mode == "delta":
        result = None
        delta_cost = None
        delta_error = None
        try:
            result = view(mode, reference, raw)
            delta_cost = _native_context_read_bytes(result, raw,
                page_bytes=page_bytes, output_bytes=output_bytes, reader_argv=reader_argv)
        except (QuotaDecisionTransportError, ValueError) as exc:
            result, delta_error = None, exc
        try:
            full = view("full", current, current_raw)
            # Every full page contains the original bytes plus JSON framing.
            # This lower bound avoids paging a large body when delta wins already.
            if delta_cost is None or len(_native_output_bytes(full)) + len(current_raw) <= delta_cost:
                full_cost = _native_context_read_bytes(full, current_raw,
                    page_bytes=page_bytes, output_bytes=output_bytes, stop_after=delta_cost,
                    reader_argv=reader_argv)
                if delta_cost is None or full_cost <= delta_cost:
                    result, publish_wire, ancestry = full, False, 0
        except (QuotaDecisionTransportError, ValueError):
            pass  # An optional full alternative cannot break a readable delta.
        if result is None:
            assert delta_error is not None
            raise delta_error
    else:
        result = view(mode, reference, raw)

    # A guard exposes only a small reference, after its immutable target exists.
    # Do not publish an unused delta or register two competing pending views.
    if publish_wire:
        write_decision_capture(wire, projected)
    from ..control_plane.turn_driver.codex_context import register_native_context_view

    observed = register_native_context_view(context_directory,
        {**result, "ancestry": ancestry}, hook_session=hook_session)
    if (progressive_enabled and observed and "first_read_command" in result
            and observed["pointer_bytes"] <= output_bytes // 4):
        first = Path(observed["path"])
        result["first_read_reference"] = str(first)
        result["first_read_command"] = context_capture_read_command(Path(current["path"]), current["sha256"],
            reference_file=first, page_bytes=page_bytes, max_page_output_bytes=output_bytes, reader_argv=reader_argv)
    return result


def progressive_context_or_current(payload: dict[str, Any], args: argparse.Namespace, *,
                                   runtime_root: Path, directory: Path | None,
                                   reader_argv: list[str] | None,
                                   captured_decision_path: str | None = None) -> dict[str, Any]:
    """Optional stateless IO falls back to this complete decided input/receipt."""
    try:
        return progressive_context_capture_view(payload, args, runtime_root=runtime_root,
            directory=directory,
            reader_argv=reader_argv, captured_decision_path=captured_decision_path)
    except (QuotaDecisionTransportError, OSError, ValueError, EffectRuntimeRemoteError, EffectRuntimeStartupError):
        return payload


def progressive_context_capture_view(payload: dict[str, Any], args: argparse.Namespace, *,
                                     runtime_root: Path, directory: Path | None,
                                     reader_argv: list[str] | None = None,
                                     captured_decision_path: str | None = None) -> dict[str, Any]:
    """Same-capture presentation for every caller, without a native journal.

    A plain read-only should-run remains read-only. Private transport files do
    not admit work, mint a Turn or stand in for observed model consumption.
    """
    from .context_machine import _private_transport_root
    from ..control_plane.turn_driver.decision_capture import capture_progressive_context

    if directory is None:
        root = Path(_private_transport_root(runtime_root))
        directory = Path(tempfile.mkdtemp(prefix="context-", dir=root))
    transport = _capture_selection_transport_template(payload, args, capture_root=directory.parent)
    required, reference = capture_progressive_context(payload, directory=directory, surface=quota_context_surface(payload, args),
        selection_command_args_template=transport, captured_decision_path=captured_decision_path)
    view = native_context_capture_view(required, directory, captured_recovery=reference,
        metadata={"context_delivery": native_context_activation(args),
            **({"capture_sha256": args.capture_sha256} if getattr(args, "capture_sha256", None) else {})},
        max_page_output_bytes=getattr(args, "max_page_output_bytes", None), reader_argv=reader_argv)
    readable = model_context_reference(view, reader_argv=reader_argv)
    # Small results need no extra navigation. The complete original and required
    # captures remain available; never shrink by dropping current obligations.
    return readable if len(_native_output_bytes(readable)) < len(_native_output_bytes(required)) else required


def _saved_context_read_page(args: argparse.Namespace, navigation: dict[str, Any], *,
                             initial_reference: str | None, reader_argv: list[str]) -> dict[str, Any]:
    """Use the ordinary verified reader; the first page and navigation share its output budget."""
    recovery = navigation["recovery"]
    pinned = initial_reference if args.initial_read else None
    reference = pinned or recovery.get("reference_file")
    read_args = argparse.Namespace(**{**vars(args), "quota_command": "context-read",
        "reference_file": reference, "capture_file": None if reference else recovery["path"],
        "capture_sha256": None if reference else recovery["sha256"],
        "codex_context_dir": None, "codex_context_root": None, "initial_read": bool(args.initial_read and not pinned)})
    page = read_context_capture(read_args, presentation={"navigation": navigation}, reader_argv=reader_argv)
    if page["ok"]:
        # Keep the already-budgeted page and cursor ahead of recovery metadata.
        page["navigation"] = page.pop("navigation")
    elif "recovery_command" not in page:
        page["recovery_command"] = recovery["command"]
    return page


def project_saved_context_capture(args: argparse.Namespace, *, hook_session: str | None = None) -> dict[str, Any]:
    """Present an immutable already-decided quota capture, without authority IO.

    Publication is delayed until this native tool runs, so the lifecycle owner
    can select its current continuity basis. Optional codec failure returns the
    committed required-view reference when available, otherwise the original
    full read reference. An unsafe recovery selector fails explicitly
    with that same reference, never a replacement guard.
    Invalid run-mode opt-ins fail before that optional transport path.
    """
    from ..command_invocation import current_loopx_cli_argv

    reader_argv = current_loopx_cli_argv()
    try:
        activation = native_context_activation(args)
        saved_page = bool(getattr(args, "initial_read", False) or getattr(args, "select", None))
        if saved_page and (args.format != "json" or not activation["enabled"] or args.offset != 0
                or args.reference_file or args.selection_sha256 or args.page_bytes is not None
                or (args.initial_read and args.select)):
            raise ValueError("Use an enabled native JSON context-project with either --initial-read or --select "
                "at offset zero. Use context-read for replay or continuation.")
        path, raw, payload = _read_capture(args.capture_file, args.capture_sha256,
                                           private=hook_session is not None)
        identity = payload.get("agent_identity")
        receipt = payload.get("heartbeat_receipt")
        if (payload.get("ok") is not True or not isinstance(identity, dict) or not isinstance(receipt, dict)
                or not args.goal_id or not args.agent_id or not args.turn_instance_id
                or payload.get("goal_id") != args.goal_id or identity.get("agent_id") != args.agent_id
                or receipt.get("turn_instance_id") != args.turn_instance_id):
            raise ValueError("context-project requires the captured quota decision's exact Goal, Agent and Turn")
        if args.turn_envelope or args.decision_output_dir or args.decision_handoff_socket:
            raise ValueError("context-project presents the saved quota view; it does not change view or handoff")
    except QuotaCommandValidationError as exc:
        return {"ok": False, "reason": str(exc)}
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        return {"ok": False, "reason": str(exc) if isinstance(exc, ValueError) else "original capture could not be read",
            "recovery": "Restore the original digest-bound decision; no guard, admission or spend was performed."}

    reference = {"path": str(path), "sha256": args.capture_sha256,
        "command": context_capture_read_command(path, args.capture_sha256, reader_argv=reader_argv)}
    full_reference = dict(reference)
    mode = "full"
    target = None
    native_prepared = False
    native_view = None
    initial_reference = None
    if not activation["enabled"] and activation["progressive_enabled"]:
        # Recovery is presentation of this exact saved decision, including its
        # original Turn. It does not need a supported host or a native session.
        try:
            return progressive_context_capture_view(payload, args,
                runtime_root=Path(getattr(args, "_context_delivery_machine_runtime", path.parent)),
                directory=None, reader_argv=reader_argv, captured_decision_path=str(path))
        except EffectRuntimeRejected as exc:
            # A valid file digest does not validate the decision inside it. The
            # source owner rejected this signed Turn; expose recovery only.
            if quota_context_surface(payload, args) == "complete_turn":
                failure = QuotaDecisionTransportError(str(exc))
                return {"ok": False, "error_code": failure.error_code, "reason": str(failure),
                    "capture_sha256": args.capture_sha256, "recovery": full_reference}
        except (QuotaDecisionTransportError, OSError, ValueError, EffectRuntimeRemoteError, EffectRuntimeStartupError):
            pass  # The complete original reference remains the fallback below.
    try:
        from ..control_plane.turn_driver.codex_context import codex_context_directory, prepare_codex_context

        if not activation["enabled"]:
            raise ValueError("context delivery is off")
        page_bytes, output_bytes = native_context_page_budget()
        output_limit = getattr(args, "max_page_output_bytes", None)
        if output_limit is not None:
            if output_limit <= 0:
                raise QuotaDecisionTransportError("Native context output limit must be positive.")
            output_bytes = min(output_bytes, output_limit)
            page_bytes = min(page_bytes, output_bytes)

        full_reference = _capture_read_reference(full_reference,
            page_bytes=page_bytes, output_bytes=output_bytes, reader_argv=reader_argv)
        reference = dict(full_reference)
        directory = codex_context_directory(directory=args.codex_context_dir,
            root=args.codex_context_root, session=hook_session, create=True)
        if not directory:
            raise ValueError("native context is unavailable")
        # The session already owns a private recovery location. Callers need
        # only supply a separate cache root when they want another location.
        # Create a missing leaf privately; never chmod or follow an existing
        # leaf. Normal platform parent aliases (such as /tmp) remain usable.
        root = Path(args.decision_output_root or directory).expanduser().absolute()
        root = root.parent.resolve(strict=True) / root.name
        root.mkdir(mode=0o700, exist_ok=True)
        metadata = root.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
            raise ValueError("saved projection needs a private physical capture root owned by this user")
        cache = Path(tempfile.mkdtemp(prefix="context-", dir=root))
        # The Host may have captured this guard before native SessionStart.
        # Carry the now-active transport into its selection presentation, not
        # into the immutable source or a second admission. Native lifecycle
        # still owns when the following full/delta view can be published.
        transport = _capture_selection_transport_template(payload, args, capture_root=root)
        prepared = prepare_codex_context(payload, directory=directory, capture_directory=cache,
            goal_id=args.goal_id, agent_id=args.agent_id,
            surface=quota_context_surface(payload, args),
            capture_id=hashlib.sha256(raw).hexdigest(),
            continuity_generation=getattr(args, "_context_delivery_generation", None),
            progressive_enabled=activation["progressive_enabled"], selection_command_args_template=transport,
            captured_decision_path=str(path) if args.initial_read and activation["progressive_enabled"] else None,
            hook_session=hook_session)
        projected = prepared.delivery
        native_prepared = True
        if projected.get("schema_version") == "context_delivery_v1":
            # Native publication owns the complete required view. Keep its
            # committed recovery even if optional wire/inline delivery fails;
            # the input capture digest below remains the original identity.
            full_reference = _capture_read_reference(projected["recovery"],
                page_bytes=page_bytes, output_bytes=output_bytes, reader_argv=reader_argv)
            reference = dict(full_reference)
        native_view = native_context_capture_view(projected, cache, context_directory=directory, hook_session=hook_session,
            progressive_enabled=activation["progressive_enabled"], initial_read_plan=prepared.initial_read_plan,
            metadata={"recovery": full_reference, "capture_sha256": args.capture_sha256,
                "context_delivery": activation},
            max_page_output_bytes=getattr(args, "max_page_output_bytes", None), page_attached=saved_page,
            reader_argv=reader_argv)
        reference = {key: native_view[key] for key in ("path", "sha256", "command")}
        mode, target = native_view["mode"], native_view.get("target")
        initial_reference = native_view.get("first_read_reference")
        native_view = model_context_reference(native_view, reader_argv=reader_argv)
    except (QuotaDecisionTransportError, OSError, ValueError, TypeError, RecursionError) as exc:
        if native_view is not None and not isinstance(exc, QuotaDecisionTransportError):
            # The native view and journal are already valid. Publishing the
            # shorter reader metadata is optional; retain their exact legacy
            # recovery rather than losing the original capture or admitting again.
            pass
        elif isinstance(exc, QuotaDecisionTransportError) or (activation["enabled"] and not native_prepared):
            if not isinstance(exc, QuotaDecisionTransportError):
                exc = QuotaDecisionTransportError(
                    "Current context recovery could not be prepared. Read the saved "
                    "original decision and receipt; do not rerun admission."
                )
            page_bytes, output_bytes = native_context_page_budget()
            output_limit = getattr(args, "max_page_output_bytes", None)
            if isinstance(output_limit, int) and output_limit > 0:
                output_bytes = min(output_bytes, output_limit)
                page_bytes = min(page_bytes, output_bytes)
            recovery_index = {**full_reference, "command": context_capture_read_command(
                Path(full_reference["path"]), full_reference["sha256"],
                page_bytes=page_bytes, max_page_output_bytes=output_bytes, list_fields=True,
                reader_argv=reader_argv)}
            return {"ok": False, "error_code": exc.error_code,
                "reason": " ".join(str(exc).split())[:500],
                "heartbeat_receipt": receipt, "capture_sha256": args.capture_sha256,
                "recovery": recovery_index,
                "instruction": "Context presentation was not published. Discover current fields with "
                    "recovery.command, then select only the facts the current decision needs from the original "
                    "capture; do not drain unrelated optional material or rerun admission. Preserve its Turn "
                    "receipt. If a selected value spans pages, follow next_command only for that selection and "
                    "verify contiguous offsets, total_bytes and sha256. Authority and freshness are unchanged. "
                    + CONTEXT_CAPTURE_NAVIGATION_GUIDANCE}
        # Off is inert; after native preparation an optional wire write can fall
        # back to the committed required view without leaving an older selector live.
    result: dict[str, Any] = {"ok": True, "mode": mode, **reference, **({"target": target} if target else {}),
        "recovery": full_reference,
        "capture_sha256": args.capture_sha256,
        **({"context_delivery": activation} if activation["enabled"] else {}),
        "instruction": "Presentation of the original decision only; authority and freshness are unchanged. "
            + (CONTEXT_CAPTURE_NAVIGATION_GUIDANCE if activation["enabled"] else CONTEXT_CAPTURE_READ_GUIDANCE)}
    navigation = native_view or result
    if saved_page:
        return _saved_context_read_page(args, navigation, initial_reference=initial_reference, reader_argv=reader_argv)
    return navigation


def prepare_quota_capture(args: argparse.Namespace, directory: Path | None) -> Path | None:
    """Validate transport options before allocating a new guard capture."""
    if getattr(args, "prepared_capture_reference", False) and (
        args.quota_command != "should-run" or args.format != "json"
        or not getattr(args, "decision_output_dir", None) or not getattr(args, "turn_instance_id", None)
        or getattr(args, "decision_handoff_socket", None) or getattr(args, "turn_envelope", False)
        or (native_context_activation(args)["enabled"] and not getattr(args, "defer_context_presentation", False))
    ):
        raise QuotaCommandValidationError("prepared capture reference requires an explicit Turn and raw JSON capture")
    if getattr(args, "defer_context_presentation", False) and (
        args.quota_command != "should-run" or args.format != "json"
        or not native_context_activation(args)["enabled"]
        or getattr(args, "decision_handoff_socket", None)
        or not (getattr(args, "decision_output_dir", None) or getattr(args, "decision_output_root", None))
    ):
        raise QuotaCommandValidationError(
            "deferred context presentation requires a native JSON should-run capture without socket handoff")
    if directory is None and (
        getattr(args, "decision_output_dir", None) is not None
        or getattr(args, "decision_output_root", None) is not None
        or getattr(args, "decision_handoff_socket", None) is not None
        or getattr(args, "codex_context_dir", None) is not None
        or getattr(args, "codex_context_root", None) is not None
        or getattr(args, "context_delivery", "off") != "off"
    ):
        from .quota_context import validate_quota_command_context_request

        validate_quota_command_context_request(args)
        return prepare_decision_capture(args)
    return directory


def prepare_decision_capture(args: argparse.Namespace) -> Path | None:
    enabled = native_context_activation(args)["enabled"]
    if enabled and not (getattr(args, "codex_context_dir", None) or getattr(args, "codex_context_root", None)
                        or getattr(args, "decision_handoff_socket", None)):
        raise QuotaCommandValidationError("context-delivery on requires a native context location or explicit host handoff")
    requested = getattr(args, "decision_output_dir", None)
    root = getattr(args, "decision_output_root", None)
    handoff = getattr(args, "decision_handoff_socket", None)
    if enabled and (getattr(args, "codex_context_dir", None) is not None
            or getattr(args, "codex_context_root", None) is not None) and (
            args.format != "json" or (requested is None and root is None)):
        raise QuotaCommandValidationError(
            "native Codex context delivery needs JSON and a capture destination")
    if handoff is not None:
        validate_handoff_socket(handoff)
        if requested is None and root is None:
            raise QuotaCommandValidationError("decision handoff requires an explicit decision capture destination")
    if requested is None and root is None:
        return None
    if requested is not None and root is not None:
        raise QuotaCommandValidationError("choose one decision capture destination")
    if args.quota_command != "should-run" or not (
        getattr(args, "turn_instance_id", None)
        or bool(getattr(args, "begin_turn", False))
    ):
        raise QuotaCommandValidationError(
            "decision capture requires quota should-run and either "
            "--turn-instance-id or --begin-turn"
        )
    try:
        if root is not None:
            parent = Path(root).expanduser().absolute()
            if parent.is_symlink() or not parent.is_dir():
                raise OSError("capture root must be an existing directory, not a symlink")
            return Path(tempfile.mkdtemp(prefix="decision-", dir=parent))
        assert requested is not None  # root is absent; exactly one destination was checked above.
        directory = Path(requested).expanduser().absolute()
        # A new directory per invocation prevents overwriting a prior observation,
        # including a prior invocation in the same Turn. Existing symlinks fail too.
        directory.mkdir(mode=0o700)
    except OSError as exc:
        raise QuotaCommandValidationError(
            "decision capture needs a new directory or an existing writable root; "
            f"no guard was run: {exc}"
        ) from exc
    return directory


def capture_decision(directory: Path | None, payload: dict[str, object]) -> str | None:
    if directory is None:
        return
    from ..control_plane.turn_driver.decision_capture import write_decision_capture

    try:
        return write_decision_capture(directory / "decision.json", payload, publish=os.replace)
    except (OSError, ValueError) as exc:
        raise QuotaDecisionTransportError(
            "Decision capture failed after guard evaluation; the Turn receipt may "
            "already exist. Do not treat this as missing admission or blindly rerun "
            f"the guard. Inspect the existing Turn receipt. Capture directory: {directory}"
        ) from exc


def budgeted_settlement_result(
    payload: dict[str, object], *, runtime_root: Path, output_bytes: int,
    reader_argv: list[str] | None = None,
    supporting_fields: tuple[str, ...] | None = None,
) -> dict[str, object]:
    """Present one evaluated result with its complete immutable diagnostic.

    This is output IO, not native continuity or another quota decision.
    Unbudgeted callers retain their existing full result.
    """
    from ..control_plane.effect_runtime import MAX_REQUEST_BYTES
    from ..control_plane.turn_driver.decision_capture import write_decision_capture

    def fits(value: dict[str, object]) -> bool:
        # Match the JSON CLI serializer, including print's newline.
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()) + 1 <= output_bytes

    if supporting_fields is None and fits(payload):
        return payload
    try:
        uid = private_capture_uid()
        root = runtime_root.expanduser().resolve() / "context-delivery"
        root.mkdir(mode=0o700, exist_ok=True)
        metadata = root.lstat()
        if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != uid
                or metadata.st_mode & 0o077):
            raise ValueError("result capture requires a private physical directory owned by this user")
        directory = Path(tempfile.mkdtemp(prefix="settlement-", dir=root))
        saved = directory / "result.json"
        digest = write_decision_capture(saved, payload)
        if saved.stat().st_size > MAX_REQUEST_BYTES:
            raise ValueError(f"complete result exceeds the existing reader limit: {saved}")
        reference = write_context_read_reference(saved, digest)
        details = {"reference_file": str(reference),
            "command": context_capture_read_command(saved, digest, reference_file=reference,
                page_bytes=min(NATIVE_CONTEXT_PAGE_BYTES, output_bytes),
                max_page_output_bytes=output_bytes, reader_argv=reader_argv)}
        deferred = [key for key in (supporting_fields if supporting_fields is not None else ("before", "after")) if key in payload]
        view = {key: value for key, value in payload.items() if key not in deferred}
        view["result_details"] = {**details, "deferred_fields": deferred}
        view["instruction"] = (
            "The exact Turn is settled. Supporting snapshots are complete in result_details; read only needed details. "
            "Keep every returned warning and unresolved control. No additional spend or admission is required for this result."
        ) if supporting_fields is not None else (
            "The operation was already evaluated. before/after are complete in the immutable result; "
            "read only needed details with context-read --select. Preserve the original Turn and effect "
            "identity during recovery; do not repeat admission or infer authority from this presentation."
        )
        if fits(view) and "result_details" not in payload and "instruction" not in payload:
            return view
        # Large or unknown controls are mandatory selected reads, never cut text.
        pointers = ["/" + key.replace("~", "~0").replace("/", "~1")
            for key in payload if key not in deferred]
        snapshot_note = ""
        if supporting_fields is not None:
            snapshot_note = " Supporting snapshots remain available in the same immutable result."
        elif deferred:
            snapshot_note = " before/after remain available in the same immutable result."
        view = {"ok": payload.get("ok"), "result_details": details,
            "command": context_capture_read_command(saved, digest, reference_file=reference, select=pointers,
                page_bytes=min(NATIVE_CONTEXT_PAGE_BYTES, output_bytes),
                max_page_output_bytes=output_bytes, reader_argv=reader_argv),
            "instruction": "Read the complete selected result controls before acting. The operation was "
                "already evaluated; preserve its original Turn/effect identity. No execution authority "
                "or context baseline is granted." + snapshot_note}
        if fits(view):
            return view
        raise ValueError(f"result reader metadata exceeds the output budget; complete result: {saved}")
    except (OSError, ValueError) as exc:
        raise QuotaDecisionTransportError(
            "The quota operation was already evaluated, but bounded result delivery failed. "
            "Recover the original operation/receipt; do not repeat admission or assume it did not commit. "
            f"{exc}"
        ) from exc


def _capture_selection_transport_template(payload: dict[str, Any], args: argparse.Namespace, *,
                                          capture_root: Path) -> str | None:
    """Derive display argv without editing the captured decision."""
    interaction = payload.get("interaction_contract")
    cli = interaction.get("cli_channel") if isinstance(interaction, dict) else None
    selection = cli.get("selection_command") if isinstance(cli, dict) else None
    if not isinstance(selection, dict):
        return None
    presentation = {**payload, "interaction_contract": {**interaction,
        "cli_channel": {**cli, "selection_command": dict(selection)}}}
    bind_capture_selection_transport(presentation, args, capture_root=capture_root)
    return presentation["interaction_contract"]["cli_channel"]["selection_command"].get("command_args_template")


def bind_capture_selection_transport(payload: dict[str, Any], args: argparse.Namespace, *,
                                     capture_root: Path | None = None,
                                     capture_directory: Path | None = None) -> None:
    """Retain caller display/capture options in the existing selection command.

    Only transport argv is composed here; the typed quota owner still selects the
    action and owns admission. Each selection invocation allocates a new capture.
    """
    root = getattr(args, "decision_output_root", None)
    # A prepared reference binds one immutable directory. Same-Turn
    # selection uses its parent to create a fresh sibling capture.
    if capture_directory is not None and getattr(args, "prepared_capture_reference", False):
        capture_root = capture_directory.parent
    if root is None and capture_root is None:
        return
    interaction = payload.get("interaction_contract")
    cli = interaction.get("cli_channel") if isinstance(interaction, dict) else None
    selection = cli.get("selection_command") if isinstance(cli, dict) else None
    if not isinstance(selection, dict):
        return
    template = selection.get("command_args_template")
    if not isinstance(template, str):
        return
    words = shlex.split(template)
    options: dict[str, str] = {}
    # A saved guard already carries its capture root. Preserve it unless this
    # caller explicitly selects another; otherwise use the validated cache.
    if root is not None or "--decision-output-root" not in words:
        selected_root = root if root is not None else capture_root
        options["--decision-output-root"] = str(Path(selected_root).expanduser().absolute())
    if getattr(args, "decision_handoff_socket", None):
        options["--decision-handoff-socket"] = args.decision_handoff_socket
    # Selection is part of the same Turn, including a caller's explicit opt-out.
    if getattr(args, "context_delivery", None) is not None:
        options["--context-delivery"] = args.context_delivery
    if getattr(args, "delta_delivery", None) is not None:
        options["--delta-delivery"] = args.delta_delivery
    if getattr(args, "codex_context_dir", None):
        options["--codex-context-dir"] = args.codex_context_dir
    if getattr(args, "codex_context_root", None):
        options["--codex-context-root"] = args.codex_context_root
    if getattr(args, "max_page_output_bytes", None) is not None:
        options["--max-page-output-bytes"] = str(args.max_page_output_bytes)
    replaced = set(options)
    if replaced & {"--codex-context-dir", "--codex-context-root"}:
        replaced.update(("--codex-context-dir", "--codex-context-root"))
    for option in replaced:
        while option in words:
            index = words.index(option)
            del words[index:index + 2]
    for option, value in options.items():
        words.extend((option, value))
    if getattr(args, "turn_envelope", False) and "--turn-envelope" not in words:
        words.append("--turn-envelope")
    selection["command_args_template"] = shlex.join(words)


def handoff_decision(directory: Path | None, socket_path: str | None,
                     context_delivery_enabled: bool = False) -> tuple[dict[str, Any], str] | None:
    """Withhold the original output until the Host releases these exact bytes.

    No second guard, admission, settlement or retry is performed here. A lost
    acknowledgement leaves the saved decision available for original-identity
    recovery; it must never be silently replaced by a later observation.
    """
    if socket_path is None:
        return None
    if directory is None:
        raise ValueError("decision handoff requires a captured decision")
    path = directory / "decision.json"
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
            channel.settimeout(120)
            channel.connect(str(validate_handoff_socket(socket_path)))
            channel.sendall(json.dumps({"path": str(path), "sha256": digest}).encode() + b"\n")
            with channel.makefile("rb") as stream:
                line = stream.readline(1025)
            response = json.loads(line)
            ordinary_release = {"released": True, "sha256": digest}
            if response not in (ordinary_release, {**ordinary_release, "context_delivery": True}):
                raise ValueError("Host did not release the original decision digest")
        if path.read_bytes() != raw:
            raise ValueError("captured decision changed during handoff")
    except (OSError, ValueError) as exc:
        raise QuotaDecisionTransportError(
            "Decision handoff failed after guard evaluation; inspect the original "
            f"Turn receipt and {path}. Do not blindly rerun the guard."
        ) from exc
    if response.get("context_delivery") is not True or not context_delivery_enabled:
        return None
    # Both the supported caller opt-in and explicit Host request are required.
    # The release itself does not prove
    # delivery/retention. Read one private snapshot, never an arbitrary pathname
    # supplied by a response or a model. Invalid optimization input selects full.
    request_path = directory / "delivery-request.json"
    try:
        from ..control_plane.effect_runtime import MAX_REQUEST_BYTES
        descriptor = os.open(request_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as source:
            metadata = os.fstat(source.fileno())
            if (not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077 or
                    metadata.st_size > MAX_REQUEST_BYTES or metadata.st_uid != os.getuid()):
                return {}, digest
            raw = source.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            return {}, digest
        request = json.loads(raw)
        return (request if isinstance(request, dict) else {}), digest
    except (OSError, ValueError, RecursionError):
        return {}, digest


def handle_native_context_command(args: argparse.Namespace, *, print_payload: Callable[..., None],
                                  registry_path: Path | None = None, runtime_root_arg: str | None = None) -> int | None:
    """Dispatch native presentation/lifecycle IO before any quota authority read."""
    if getattr(args, "initial_read", False) and args.quota_command not in ("context-read", "context-project"):
        print_payload({"ok": False, "reason": "--initial-read is only supported by context-read or enabled native JSON context-project"}, args.format, str)
        return 1
    list_depth = getattr(args, "list_depth", None)
    if list_depth is not None and (args.quota_command != "context-read" or not getattr(args, "list_fields", False)
            or type(list_depth) is not int or not 1 <= list_depth <= 8):
        print_payload({"ok": False, "reason": "--list-depth requires context-read --list and an integer from 1 to 8"}, args.format, str)
        return 1
    if getattr(args, "selection_sha256", None) is not None and (args.quota_command != "context-read"
            or (getattr(args, "select", None) is None and not getattr(args, "initial_read", False))):
        print_payload({"ok": False, "reason": "selection-sha256 requires context-read with --select or --initial-read"},
            args.format, lambda value: str(value["reason"]))
        return 1
    if getattr(args, "outcome_ref", None) is not None and args.quota_command != "replan-view":
        print_payload({"ok": False, "reason": "outcome-ref is only supported by replan-view"},
            args.format, lambda value: str(value["reason"]))
        return 1
    if getattr(args, "list_fields", False) and args.quota_command != "context-read":
        print_payload({"ok": False, "reason": "--list is only supported by context-read"}, args.format, str)
        return 1
    has_output_limit = getattr(args, "max_page_output_bytes", None) is not None
    settlement_output = (args.quota_command == "spend-slot" and args.format == "json"
        and bool(getattr(args, "turn_instance_id", None)) and bool(getattr(args, "agent_id", None)))
    budget_scope_error = ("max-page-output-bytes is only supported by JSON context-read "
        "or enabled native should-run/context-project, or Turn-bound JSON spend-slot")
    if has_output_limit and args.quota_command != "context-read" and not settlement_output and (
        args.quota_command not in ("should-run", "context-project") or args.format != "json"
        or (getattr(args, "context_delivery", None) == "off" and getattr(args, "delta_delivery", None) != "auto")):
        print_payload({"ok": False, "reason": budget_scope_error},
            args.format, lambda value: str(value["reason"]))
        return 1
    if registry_path is not None:
        from .context_machine import apply_context_machine_options

        try:
            apply_context_machine_options(args, registry_path=registry_path, runtime_root_arg=runtime_root_arg)
        except (OSError, ValueError, TypeError) as exc:
            print_payload({"ok": False, "reason": str(exc)}, args.format,
                lambda value: json.dumps(value, ensure_ascii=False, indent=2))
            return 1
    if has_output_limit and settlement_output:
        try:
            private_capture_uid()
        except ValueError as exc:
            print_payload({"ok": False, "reason": str(exc), "appended": False,
                "registry_mutated": False}, args.format, str)
            return 1
    if has_output_limit and settlement_output and args.max_page_output_bytes <= 0:
        print_payload({"ok": False, "reason": "max-page-output-bytes must be positive"}, args.format, str)
        return 1
    if has_output_limit and args.quota_command != "context-read" and not settlement_output:
        try:
            if not native_context_activation(args)["enabled"]:
                raise QuotaCommandValidationError(budget_scope_error)
            if args.max_page_output_bytes <= 0:
                raise QuotaCommandValidationError("max-page-output-bytes must be positive")
        except QuotaCommandValidationError as exc:
            print_payload({"ok": False, "reason": str(exc)}, args.format,
                lambda value: str(value["reason"]))
            return 1
    if args.quota_command in ("context-config", "context-hook"):
        try:
            activation = native_context_activation(args)
        except QuotaCommandValidationError as exc:
            payload = {"ok": False, "reason": str(exc)}
            print_payload(payload, args.format, lambda value: json.dumps(value, ensure_ascii=False, indent=2))
            return 1
    if args.quota_command == "context-config":
        from ..command_invocation import loopx_cli_argv
        from ..control_plane.turn_driver.codex_context import codex_context_configuration

        try:
            if not activation["enabled"]:
                payload = {"ok": True, "context_delivery": activation}
                print_payload(payload, args.format, lambda value: json.dumps(value, ensure_ascii=False, indent=2))
                return 0
            directory = getattr(args, "codex_context_root", None) or args.codex_context_dir
            if not directory:
                raise ValueError("context-config requires codex-context-root or codex-context-dir")
            machine_runtime = getattr(args, "_context_delivery_machine_runtime", None)
            cli_argv = loopx_cli_argv()
            if registry_path is not None:
                cli_argv += ["--registry", str(registry_path.expanduser().resolve())]
            hook_runtime = machine_runtime or runtime_root_arg
            if hook_runtime:
                cli_argv += ["--runtime-root", str(Path(hook_runtime).expanduser().resolve())]
            config = codex_context_configuration(directory, goal_id=args.goal_id, agent_id=args.agent_id,
                cli_argv=cli_argv, scenario=activation["scenario"], session_root=bool(getattr(args, "codex_context_root", None)),
                inherit_machine=bool(machine_runtime), progressive_enabled=activation["progressive_enabled"])
            explicit_options = "--context-delivery on" if activation["progressive_enabled"] else "--context-delivery off --delta-delivery auto"
            payload = {"ok": True, "context_delivery": activation, "native_config": config,
                "activation": "Apply config_overrides, merge hooks and append developer_instructions to the existing native configuration; "
                    "review and trust only these exact hooks in Codex. "
                    "Existing loaded threads may retain their original hook configuration; enabled/trusted "
                    "discovery is not session initialization or live adoption. Start/resume through the host "
                    "with the resulting configuration after active work reaches its normal safe boundary. "
                    + ("Machine defaults are re-read on each invocation; generated hooks inherit the same machine runtime. "
                       "Omit per-command activation and location flags to retain that inheritance. " if machine_runtime else
                       f"Pass {explicit_options} with the same declared host mode, context location and a decision-output-root to heartbeat-prompt/should-run. ")
                    +
                    "Verified saved files support progressive reads and exact deltas without a full model read; compaction starts a new full baseline on the next work Turn. "
                    "The generated output profile uses 64000-byte continuations and a 20000-token native history limit; "
                    "the same page profile applies without the shell override. Explicit smaller reader budgets remain available for truncated-range recovery. "
                    "Set --context-delivery off and remove the generated context configuration, including its output overrides, to restore ordinary output. "
                    "This command installs nothing and grants no execution authority."}
        except (OSError, ValueError, TypeError) as exc:
            payload = {"ok": False, "reason": str(exc)}
        print_payload(payload, args.format, lambda value: json.dumps(value, ensure_ascii=False, indent=2))
        return 0 if payload["ok"] else 1
    if args.quota_command == "context-hook":
        import sys
        from ..control_plane.effect_runtime import MAX_REQUEST_BYTES
        from ..control_plane.turn_driver.codex_context import (
            CONTEXT_LIFECYCLE_HOOKS, codex_context_directory, codex_context_hook,
            native_context_event_eligible, native_execution_preparation_request,
            record_native_execution_preparation,
        )
        from ..control_plane.turn_driver.codex_execution_preparation import (
            codex_execution_preparation_context,
            execute_codex_execution_preparation,
        )

        # Context transport remains read-only.  An exact typed Host preparation
        # request may additionally execute its ordinary claim and fresh guard.
        try:
            if not activation["enabled"]:
                print("{}")
                return 0
            raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
            event = json.loads(raw) if len(raw) <= MAX_REQUEST_BYTES else None
            if (isinstance(event, dict) and native_context_event_eligible(event)
                    and isinstance(event.get("session_id"), str) and event["session_id"]
                    and event.get("hook_event_name") in CONTEXT_LIFECYCLE_HOOKS):
                directory = codex_context_directory(directory=args.codex_context_dir,
                    root=getattr(args, "codex_context_root", None), session=event.get("session_id"), create=True)
                payload = codex_context_hook(directory, event)
                try:
                    request = native_execution_preparation_request(directory, event)
                    if request is not None:
                        workspace = Path(event.get("cwd") or "")
                        if workspace.is_absolute() and workspace.resolve() == Path.cwd().resolve():
                            capture = (
                                Path(tempfile.mkdtemp(prefix="prepared-", dir=directory)) / "ready"
                                if request.get("turn_instance_id") else None
                            )
                            receipt = execute_codex_execution_preparation(
                                request,
                                cwd=workspace.resolve(),
                                scenario=activation["scenario"],
                                capture_directory=capture,
                                context_directory=directory if capture is not None else None,
                            )
                            record_native_execution_preparation(
                                directory,
                                event,
                                source_sha256=request["source_sha256"],
                                receipt=receipt,
                            )
                            # Publish the decision that the Host just verified;
                            # a model must not need a second guard to obtain its
                            # Goal, task, requirements and settlement contract.
                            if capture is not None:
                                source = capture / "decision.json"
                                # The existing saved-capture owner preserves a
                                # pinned full recovery even if native projection
                                # fails. It never rereads authority or admits work.
                                digest = receipt.pop("capture_sha256")
                                receipt["context"] = project_saved_context_capture(argparse.Namespace(**{
                                    **vars(args), "quota_command": "context-project",
                                    "capture_file": str(source),
                                    "capture_sha256": digest,
                                    "goal_id": request["goal_id"], "agent_id": request["agent_id"],
                                    "turn_instance_id": request["turn_instance_id"],
                                }), hook_session=event["session_id"])
                                if receipt["context"].get("ok") is False:
                                    from ..command_invocation import current_loopx_cli_argv

                                    receipt["context"]["recovery_command"] = context_capture_read_command(
                                        source, digest, reader_argv=current_loopx_cli_argv())
                            existing = payload.get("hookSpecificOutput")
                            existing_context = (
                                str(existing.get("additionalContext") or "")
                                if isinstance(existing, dict)
                                else ""
                            )
                            payload["hookSpecificOutput"] = {
                                "hookEventName": event["hook_event_name"],
                                "additionalContext": " ".join(filter(None, [
                                    existing_context,
                                    codex_execution_preparation_context(receipt),
                                ])),
                            }
                except (OSError, RuntimeError, subprocess.SubprocessError, ValueError, TypeError, RecursionError):
                    # Preserve the original explicit action and any context
                    # recovery output when Host-side preparation cannot prove
                    # its postcondition.
                    pass
            else:
                payload = {}
        except (OSError, RuntimeError, subprocess.SubprocessError, ValueError, TypeError, RecursionError):
            payload = {}
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        return 0
    if args.quota_command == "context-project":
        payload = project_saved_context_capture(args)
        print_payload(payload, args.format, lambda value: str(value.get("command", value.get("reason", ""))))
        return 0 if payload["ok"] else 1
    if args.quota_command == "context-read":
        from ..command_invocation import current_loopx_cli_argv

        # Presentation recovery reads the original capture, never current Goal
        # state. In particular it must not evaluate a guard or append a receipt.
        payload = read_context_capture(args, reader_argv=current_loopx_cli_argv())
        print_payload(payload, args.format, lambda value: str(value.get("content", value.get("reason", ""))))
        return 0 if payload["ok"] else 1
    return None


def read_replan_view(args: argparse.Namespace) -> dict[str, Any]:
    """Read a captured observation only; no registry, provider, guard or receipts."""
    from ..control_plane.quota.turn_envelope import _turn_envelope_result
    from ..command_invocation import current_loopx_cli_argv

    if not args.decision_file or not args.source_hash:
        raise ValueError("replan-view requires --decision-file and --source-hash")
    if args.outcome_ref == "":
        raise ValueError("replan-view outcome-ref requires an exact reference; use context-read --select for original detail")
    path = Path(args.decision_file).expanduser()
    payload = json.loads(read_turn_observation_text(path))
    return _turn_envelope_result("replan_view", payload=payload, source_hash=args.source_hash,
                                 captured_decision_path=str(path), reader_argv=current_loopx_cli_argv(),
                                 stage=args.replan_stage, outcome_ref=args.outcome_ref)


def handle_replan_view(
    args: argparse.Namespace,
    print_payload: Callable[[dict[str, object], str, Callable[[dict[str, object]], str]], None],
) -> int:
    """Render the captured read without entering live quota dispatch or recovery."""
    try:
        view = read_replan_view(args)
    except (OSError, ValueError, EffectRuntimeRejected) as exc:
        view = {"ok": False, "reason": str(exc)}
    print_payload(view, args.format, lambda value: json.dumps(value, ensure_ascii=False, indent=2))
    return 0 if view["ok"] else 1
