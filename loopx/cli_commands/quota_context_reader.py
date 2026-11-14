"""Bounded readers and navigation for an already committed quota capture."""
from __future__ import annotations

import argparse
from collections.abc import Callable
import json
import os
import shlex
import tempfile
from pathlib import Path
from typing import Any

from ..command_invocation import current_loopx_cli_argv, same_context_reader_route, validate_context_reader_route
from ..control_plane.content_digest import BARE_SHA256_PATTERN
from ..control_plane.effect_runtime import EffectRuntimeRemoteError, EffectRuntimeStartupError
from ..control_plane.quota.effective_action import EffectiveAction
from ..control_plane.quota.error_codes import QuotaDecisionTransportError
from ..control_plane.turn_driver.decision_capture import (
    CONTEXT_CAPTURE_NAVIGATION_GUIDANCE,
    CONTEXT_CAPTURE_PAGE_GUIDANCE,
    CONTEXT_CAPTURE_READ_GUIDANCE,
    ContextSelectionDigestChanged,
    context_capture_read_command,
    context_selection_binding,
    context_selection_view as _context_selection_view,
    initial_context_selection_bytes,
    read_context_capture_file as _read_capture,
    read_context_read_reference,
    write_context_read_reference,
    write_initial_context_read_reference,
)
from ..control_plane.turn_driver.native_context_profile import (
    NATIVE_CONTEXT_PAGE_BYTES,
    NATIVE_CONTEXT_PAGE_OUTPUT_BYTES,
    native_context_page_budget,
)


def _capture_selection_transport_template(payload: dict[str, Any], args: argparse.Namespace, *,
                                          capture_root: Path) -> str | None:
    """Derive display argv without editing the captured decision."""
    interaction = payload.get("interaction_contract")
    if not isinstance(interaction, dict):
        return None
    cli = interaction.get("cli_channel")
    if not isinstance(cli, dict):
        return None
    selection = cli.get("selection_command")
    if not isinstance(selection, dict):
        return None
    selected = dict(selection)
    presentation = {**payload, "interaction_contract": {**interaction,
        "cli_channel": {**cli, "selection_command": selected}}}
    bind_capture_selection_transport(presentation, args, capture_root=capture_root)
    command = selected.get("command_args_template")
    return command if isinstance(command, str) else None


def bind_capture_selection_transport(payload: dict[str, Any], args: argparse.Namespace, *,
                                     capture_root: Path | None = None,
                                     capture_directory: Path | None = None,
                                     retain_delivery_options: bool = False) -> None:
    """Retain caller display/capture options when binding or selecting a Turn.

    Only transport argv is composed here; the typed quota owner still selects the
    action and owns admission. Each selection invocation allocates a new capture.
    """
    root = getattr(args, "decision_output_root", None)
    handoff = getattr(args, "decision_handoff_socket", None)
    # A prepared or handed-off capture is immutable. A fixed directory uses
    # its parent for the next selection, matching the Host's capture-root check.
    if root is None and capture_directory is not None and (
        getattr(args, "prepared_capture_reference", False) or handoff
    ):
        capture_root = capture_directory.parent
    complete_turn = getattr(args, "complete_turn", False)
    turn_envelope = getattr(args, "turn_envelope", False)
    if root is None and capture_root is None and not (complete_turn or turn_envelope or retain_delivery_options):
        return
    interaction = payload.get("interaction_contract")
    cli = interaction.get("cli_channel") if isinstance(interaction, dict) else None
    selection = cli.get("selection_command") if isinstance(cli, dict) else None
    if isinstance(selection, dict):
        template = selection.get("command_args_template")
    elif complete_turn and isinstance(cli, dict) and cli.get("turn_binding_required") is True:
        actions = cli.get("next_cli_actions")
        template = actions[0] if isinstance(actions, list) and len(actions) == 1 else None
    else:
        return
    if not isinstance(template, str):
        return
    command = _bind_capture_transport_options(template, args, root=root, capture_root=capture_root)
    if isinstance(selection, dict):
        selection["command_args_template"] = command
    else:
        from ..control_plane.work_items.interaction_contract import bind_cli_turn_selection_action

        bind_cli_turn_selection_action(payload, previous=template, command=command)


def bind_capture_recovery_transport(payload: dict[str, Any], args: argparse.Namespace, *,
                                    capture_directory: Path | None, registry_path: Path,
                                    runtime_root: Path, preserve_app_delivery: bool = False) -> None:
    """Keep the caller's work contract on the quota owner's recovery guards."""
    if payload.get("effective_action") != EffectiveAction.UNSETTLED_HOST_TURN_RECOVERY.value:
        return
    complete_turn = getattr(args, "complete_turn", False)
    legacy_capture = capture_directory is not None and getattr(args, "turn_envelope", False)
    if not (complete_turn or legacy_capture or preserve_app_delivery):
        return
    native_transport = any(getattr(args, name, None) for name in (
        "decision_handoff_socket", "codex_context_dir", "codex_context_root",
    ))
    if native_transport:
        # A hook journal or rendezvous is bound to its live host. An explicit
        # complete-source caller can resume through the same stateless reader.
        if not complete_turn:
            return
        from copy import copy

        args = copy(args)
        args.decision_handoff_socket = args.codex_context_dir = args.codex_context_root = None
        if args.context_delivery == "on":
            args.context_delivery = "auto"
        args.delta_delivery = "off"
    from ..control_plane.work_items.action_selection_contract import render_cli_command_prefix

    cli = payload["interaction_contract"]["cli_channel"]
    prefix = render_cli_command_prefix(runtime_root=str(runtime_root), registry_path=str(registry_path))
    guard_prefix = prefix + " --format json quota should-run "
    root = getattr(args, "decision_output_root", None)
    capture_root = capture_directory.parent if capture_directory is not None else None
    cli["next_cli_actions"] = [
        _bind_capture_transport_options(action, args, root=root, capture_root=capture_root)
        if action.startswith(guard_prefix) else action
        for action in cli["next_cli_actions"]
    ]


def _bind_capture_transport_options(template: str, args: argparse.Namespace, *,
                                    root: str | None, capture_root: Path | None) -> str:
    words = shlex.split(template)
    options: dict[str, str] = {}
    # A saved guard already carries its capture root. Preserve it unless this
    # caller explicitly selects another; otherwise use the validated cache.
    selected_root = root if root is not None else capture_root
    if selected_root is not None and (root is not None or "--decision-output-root" not in words):
        options["--decision-output-root"] = str(Path(selected_root).expanduser().absolute())
    if handoff := getattr(args, "decision_handoff_socket", None):
        options["--decision-handoff-socket"] = handoff
    # Preserve a caller's explicit opt-out as well as opt-in.
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
    replacements: dict[str, str | None] = dict(options)
    if options.keys() & {"--codex-context-dir", "--codex-context-root"}:
        for option in ("--codex-context-dir", "--codex-context-root"):
            replacements.setdefault(option, None)
    for option, value in replacements.items():
        start = 0
        if value is not None:
            if option not in words:
                words.extend((option, value))
                continue
            # Preserve the saved option's position when rebinding its route.
            index = words.index(option)
            words[index + 1] = value
            start = index + 2
        while option in words[start:]:
            index = words.index(option, start)
            del words[index:index + 2]
    if getattr(args, "turn_envelope", False) and "--turn-envelope" not in words:
        words.append("--turn-envelope")
    if getattr(args, "complete_turn", False) and "--complete-turn" not in words:
        words.append("--complete-turn")
    return shlex.join(words)


def _observed_read_presentation(fields: dict[str, Any], text_append: dict[str, Any] | None = None) -> dict[str, Any]:
    appended = text_append["pointer"] if text_append is not None else None
    instruction = "These unchanged fields still apply. "
    if appended is not None:
        instruction += "For append_to_observed_text, append value to the previously delivered text. "
    return {"previously_delivered_fields": [pointer for pointer in fields if pointer != appended],
        "field_recovery": instruction + "If needed or uncertain, append --select "
            "and the field pointer to recovery.command for its complete current body."}


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
                  reader_argv: list[str] | None = None,
                  compact_reader: bool = False) -> dict[str, Any]:
    total = len(raw)

    def page(text: str) -> dict[str, Any]:
        return _render_capture_page(path, text, digest, offset=offset, total=total,
            size=size, output_limit=output_limit, instruction=instruction,
            presentation=presentation, next_page_bytes=next_page_bytes,
            next_page_output_bytes=next_page_output_bytes, select=select, selection_sha256=selection_sha256,
            list_fields=list_fields, list_depth=list_depth, initial_read=initial_read,
            reference_file=reference_file, reader_argv=reader_argv, compact_reader=compact_reader)

    end = min(offset + size, total)
    while end < total and raw[end] & 0xC0 == 0x80:
        end -= 1
    content = raw[offset:end].decode("utf-8")
    result = page(content)
    if output_limit is None or _json_output_size(result) <= output_limit:
        return result
    # EOF drops next_command: test it above before searching the monotone
    # non-EOF prefix sizes. Never return a zero-progress continuation.
    low, high = 1, len(content) - (end == total)
    best = None
    while low <= high:
        middle = (low + high) // 2
        candidate = page(content[:middle])
        if _json_output_size(candidate) <= output_limit:
            best, low = candidate, middle + 1
        else:
            high = middle - 1
    if best is None:
        raise ValueError("max-page-output-bytes cannot fit page metadata and one UTF-8 character; increase the limit for this same capture")
    return best



def _render_capture_page(path: Path, text: str, digest: str, *, offset: int, total: int,
                         size: int, output_limit: int | None, instruction: bool,
                         presentation: dict[str, Any] | None = None,
                         next_page_bytes: int | None = None, next_page_output_bytes: int | None = None,
                         select: list[str] | None = None, selection_sha256: str | None = None,
                         list_fields: bool = False, list_depth: int | None = None, initial_read: bool = False,
                         reference_file: Path | None = None, reader_argv: list[str] | None = None,
                         compact_reader: bool = False) -> dict[str, Any]:
    cursor = offset + len(text.encode("utf-8"))
    result: dict[str, Any] = {**(presentation or {}), "ok": True, "schema_version": "context_capture_page_v1", "sha256": digest,
        "offset": offset, "next_offset": cursor if cursor < total else None,
        "total_bytes": total, "eof": cursor == total, "content": text}
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
    if compact_reader:
        result.pop("reference_file", None)  # The cursor already locates the immutable selection.
    if cursor < total:
        result["next_command"] = context_capture_read_command(path, digest, offset=cursor,
            page_bytes=next_page_bytes if compact_reader else next_page_bytes or size,
            max_page_output_bytes=next_page_output_bytes or output_limit,
            select=None if initial_read or reference_file is not None else select,
            selection_sha256=selection_sha256 if reference_file is None else None,
            list_fields=list_fields if reference_file is None else False,
            list_depth=list_depth if reference_file is None else None,
            initial_read=initial_read if reference_file is None else False, reference_file=reference_file,
            reader_argv=reader_argv, compact=compact_reader)
    if compact_reader:
        # The opening supplies the recovery instruction. Keep it on the last
        # page, where the now-absent continuation leaves room for it.
        if cursor < total:
            result.pop("read_instruction", None)
        else:
            result["read_instruction"] = "Recovery read complete. Do not rerun admission."
    return result


def validate_compact_recovery_budget(reference: Path, limit: int, reader_argv: list[str], *,
                                     page_bytes: int | None = None, pinned_selection: bool = False) -> None:
    """Reserve enough for one escaped character and the longest compact cursor."""
    # Use the largest decimal width of the bounded transport. A selected
    # view may grow beyond the source when it repeats requested values.
    from ..control_plane.effect_runtime import MAX_REQUEST_BYTES

    offset = 10 ** len(str(MAX_REQUEST_BYTES))
    selected = reference if pinned_selection else reference.with_name(reference.name + "." + "0" * 16)
    frames = ((selected, [""]),) if pinned_selection else ((reference, None), (selected, [""]))
    for path, selection in frames:
        frame = _render_capture_page(path, "\x00", "0" * 64, offset=offset,
            total=offset * 9, size=1, output_limit=limit, instruction=False, select=selection,
            list_fields=selection is not None, next_page_bytes=page_bytes,
            reference_file=path, reader_argv=reader_argv, compact_reader=True)
        if _json_output_size(frame) > limit:
            raise ValueError("Output limit cannot fit a recovery page.")


def _inline_context_read_floor(path: Path, content_bytes: int, digest: str, *, page_bytes: int,
                               output_limit: int, reader_argv: list[str] | None,
                               opening_guidance: bool) -> int:
    # Include the maximum cursor and a six-byte JSON escape, plus the
    # opening-page guidance. These together bound the smallest useful page
    # at every offset without reading the body or allocating a second copy.
    offset = content_bytes - 1
    opening = _capture_page(path, b"\0\0", digest,
        offset=0, size=1, output_limit=None, instruction=opening_guidance,
        next_page_bytes=page_bytes, next_page_output_bytes=output_limit,
        reader_argv=reader_argv)
    cursor = context_capture_read_command(path, digest, offset=offset + 1,
        page_bytes=page_bytes, max_page_output_bytes=output_limit, reader_argv=reader_argv)
    return _json_output_size({**opening, "offset": offset, "next_offset": offset + 1,
        "total_bytes": content_bytes, "next_command": cursor})


def inline_context_read_fits(path: Path, raw: bytes, digest: str, *, page_bytes: int,
                             output_limit: int, reader_argv: list[str] | None) -> bool:
    """Check whether the original file can be paged under the supplied limit."""
    path = path.parent.resolve(strict=True) / path.name
    try:
        page = _capture_page(path, raw, digest, offset=0, size=page_bytes,
            output_limit=output_limit, instruction=False, reader_argv=reader_argv)
    except ValueError:
        return False
    if page["eof"]:
        return True
    return _inline_context_read_floor(path, len(raw), digest, page_bytes=page_bytes,
        output_limit=output_limit, reader_argv=reader_argv, opening_guidance=False) <= output_limit


def inline_context_recovery_budget(path: Path, raw: bytes, digest: str, *, page_bytes: int,
                                   output_limit: int, reader_argv: list[str] | None) -> int:
    """Bound an exact-file reader even when no selection reference can be written."""
    path = path.parent.resolve(strict=True) / path.name
    if inline_context_read_fits(path, raw, digest, page_bytes=page_bytes,
                               output_limit=output_limit, reader_argv=reader_argv):
        return output_limit
    while (needed := _inline_context_read_floor(path, len(raw), digest, page_bytes=page_bytes,
            output_limit=output_limit, reader_argv=reader_argv, opening_guidance=True)) > output_limit:
        # Leave space for a page's smallest supported content window beyond
        # the cursor and guidance. Recheck the changed budget's digit count.
        output_limit = needed + 256
    return output_limit


def pinned_context_read_window(reference_file: Path, content: bytes, *, page_bytes: int,
                               output_limit: int, reader_argv: list[str] | None) -> tuple[int, int]:
    """Keep the caller's page budget when it can carry a verified selection."""
    # The reader resolves parent aliases before printing its path and cursor.
    reference_file = reference_file.parent.resolve(strict=True) / reference_file.name
    offset = max(0, len(content) - 1)
    sample = bytes(offset + 2)  # NUL needs six bytes in JSON.
    def continuation_bytes(size: int, budget: int) -> int:
        page = _capture_page(reference_file, sample, "", offset=offset, size=1,
            output_limit=None, instruction=False, next_page_bytes=size,
            next_page_output_bytes=budget, select=[], reference_file=reference_file,
            reader_argv=reader_argv)
        return _json_output_size(page)

    if continuation_bytes(page_bytes, output_limit) <= output_limit:
        return page_bytes, output_limit
    # A small selection can avoid the long continuation altogether. If its
    # complete page needs a separate budget, return enough to read it in one go.
    if len(content) <= NATIVE_CONTEXT_PAGE_BYTES:
        page_bytes = max(page_bytes, len(content))
        whole = _capture_page(reference_file, content, "", offset=0, size=page_bytes,
            output_limit=None, instruction=False, select=[], reference_file=reference_file,
            reader_argv=reader_argv)
        return page_bytes, max(output_limit, _json_output_size(whole))
    while (needed := continuation_bytes(page_bytes, output_limit)) > output_limit:
        output_limit = needed
    return page_bytes, output_limit








def _validate_context_page_window(offset: int, size: int, output_limit: int | None) -> None:
    from ..control_plane.effect_runtime import MAX_REQUEST_BYTES

    maximum = max(8192, min(output_limit or 8192, MAX_REQUEST_BYTES))
    if type(offset) is not int or offset < 0 or type(size) is not int or not 256 <= size <= maximum:
        raise ValueError(f"Page offset must be nonnegative and --page-bytes must be between 256 and {maximum}; "
            "larger pages require a sufficient --max-page-output-bytes within the capture limit.")


def _validate_pinned_selection(selection: dict[str, Any]) -> None:
    if (set(selection) - {"present_goal_narrative"} != {"select", "initial_read", "list_fields", "list_depth", "sha256"}
            or type(selection["initial_read"]) is not bool or type(selection["list_fields"]) is not bool
            or ("present_goal_narrative" in selection and (
                selection["present_goal_narrative"] is not True
                or selection["list_fields"] or selection["initial_read"]))
            or not isinstance(selection["sha256"], str)
            or BARE_SHA256_PATTERN.fullmatch(selection["sha256"]) is None):
        raise ValueError("reader selection reference is incomplete or unsupported")


def _bounded_context_read_failure(payload: dict[str, Any], limit: int | None, *,
                                  restart: Callable[[], str | None] | None = None,
                                  restart_fits: bool = True) -> dict[str, Any]:
    """Keep an intact diagnostic and a usable original-capture locator when they fit."""
    if type(limit) is not int or limit <= 0 or (restart_fits and _json_output_size(payload) <= limit):
        return payload
    diagnostic = {key: payload[key] for key in ("ok", "schema_version", "error_code", "error_kind", "diagnostic_code")
                  if key in payload}
    command = payload.get("recovery_command") if restart_fits else None
    if command is None or _json_output_size({**diagnostic, "recovery_command": command}) > limit:
        command = restart() if restart is not None else None
    if command is not None:
        located = {**diagnostic, "recovery_command": command}
        for candidate in ({**located, "reason": payload["reason"],
                           "recovery": "Read the same capture from offset zero. Do not rerun admission."},
                          {**located, "reason": payload["reason"]}, located):
            if _json_output_size(candidate) <= limit:
                return candidate
    retry = "Retry this read with a larger output budget. Keep the original capture; do not rerun admission."
    for candidate in ({**diagnostic, "reason": payload["reason"], "recovery": retry},
                      {**diagnostic, "reason": payload["reason"]}, diagnostic, {"ok": False}):
        if _json_output_size(candidate) <= limit:
            return candidate
    return {}


def _compact_context_restart(path: Path, digest: str, *, limit: int, page_bytes: int,
                             reader_argv: list[str] | None, select: list[str] | None = None,
                             list_fields: bool = False, list_depth: int | None = None,
                             initial_read: bool = False, pinned: dict[str, Any] | None = None) -> str | None:
    """Reuse the private reader protocol; never expose a new or unverified capture."""
    if limit < 128:
        return None  # A command and the minimum successful page cannot fit.
    # The existing private recovery transport uses a short same-user POSIX
    # directory. The source directory or CLI path can consume most of the
    # caller's budget; the private descriptor keeps the exact source digest.
    from ..control_plane.turn_driver.decision_capture import private_capture_uid

    directory = None
    try:
        route = validate_context_reader_route(reader_argv or current_loopx_cli_argv())
        private_capture_uid()
        directory = Path(tempfile.mkdtemp(prefix="lx-", dir="/tmp"))
        locator = directory / "r"
        expected = locator.with_name("r." + "0" * 16) if pinned else locator
        validate_compact_recovery_budget(expected, limit, route, page_bytes=page_bytes,
            pinned_selection=pinned is not None)
        command = context_capture_read_command(path, digest, reference_file=expected, reader_argv=route,
            compact=True, page_bytes=page_bytes, max_page_output_bytes=limit, select=select,
            list_fields=list_fields, list_depth=list_depth, initial_read=initial_read)
        if _json_output_size({"ok": False, "recovery_command": command}) > limit:
            return None
        reference = write_context_read_reference(path, digest, reference_file=locator, reader_argv=route,
            selection=pinned["selection"] if pinned else None,
            selection_paths=pinned.get("selection_paths") if pinned else None)
        return context_capture_read_command(path, digest, reference_file=reference, reader_argv=route,
            compact=True, page_bytes=page_bytes, max_page_output_bytes=limit, select=select,
            list_fields=list_fields, list_depth=list_depth, initial_read=initial_read)
    except (OSError, ValueError):
        return None
    finally:
        if directory is not None:
            try:
                directory.rmdir()
            except OSError:
                pass  # Keep the published recovery descriptor.


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
    output_limit = getattr(args, "max_page_output_bytes", None)
    try:
        digest = getattr(args, "capture_sha256", None)
        value = getattr(args, "capture_file", None)
        reference_file = getattr(args, "reference_file", None)
        pinned_selection = None
        bound_route = None
        if reference_file:
            requested_reference = Path(reference_file).expanduser().absolute()
            if value or digest or getattr(args, "codex_context_dir", None) or getattr(args, "codex_context_root", None):
                raise ValueError("reference-file cannot be combined with capture or native session lookup")
            if args.format != "json":
                raise ValueError("reference-file requires JSON output")
            reference_file, reference = read_context_read_reference(reference_file)
            value, digest = reference["path"], reference["sha256"]
            pinned_selection = reference.get("selection")
            if bound_route := reference.get("reader_argv"):
                if not same_context_reader_route(reader_argv or current_loopx_cli_argv(), bound_route):
                    raise ValueError("run the recovery command with its original LoopX executable")
                reader_argv = bound_route
                reference_file = requested_reference
        offset, size = args.offset, args.page_bytes
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
        _validate_context_page_window(offset, size, output_limit)
        path, raw, payload = _read_capture(value, digest, private=bool(reference_file) and os.name == "nt")

        def restart(failure: dict[str, Any], *, select: list[str] | None = None, list_fields: bool = False,
                    list_depth: int | None = None, initial_read: bool = False,
                    fallback: tuple[Path, dict[str, Any]] | None = None) -> dict[str, Any]:
            page_bytes = next_page_bytes or size
            restart_limit = next_page_output_bytes or output_limit
            reference = fallback[0] if fallback else None
            failure["recovery_command"] = context_capture_read_command(path, digest, select=select,
                list_fields=list_fields, list_depth=list_depth, initial_read=initial_read,
                reference_file=reference, page_bytes=page_bytes, max_page_output_bytes=restart_limit, reader_argv=reader_argv)
            if output_limit is None:
                return failure
            # A short refusal alone is insufficient. Its returned command must
            # also have room to advance, including the longest legacy cursor.
            from ..control_plane.effect_runtime import MAX_REQUEST_BYTES

            width = 10 ** len(str(MAX_REQUEST_BYTES))
            selectors = fallback[1]["selection"]["select"] if fallback else select
            if selectors is None and list_fields:
                selectors = [""]
            if selectors is None and initial_read:
                selectors = []
            frame = _render_capture_page(path, "\x00", digest, offset=width, total=width * 9,
                size=page_bytes, output_limit=restart_limit, instruction=False,
                select=selectors, selection_sha256=digest,
                list_fields=list_fields, list_depth=list_depth, initial_read=initial_read,
                reference_file=reference, reader_argv=reader_argv)
            return _bounded_context_read_failure(failure, output_limit,
                restart_fits=_json_output_size(frame) <= output_limit,
                restart=lambda: _compact_context_restart(path, digest, limit=output_limit,
                    page_bytes=page_bytes, reader_argv=reader_argv, select=select, list_fields=list_fields,
                    list_depth=list_depth, initial_read=initial_read, pinned=fallback[1] if fallback else None))

        list_fields = getattr(args, "list_fields", False)
        list_depth = getattr(args, "list_depth", None)
        if list_depth is not None and (not list_fields or type(list_depth) is not int or not 1 <= list_depth <= 8):
            raise ValueError("list-depth requires --list and an integer from 1 to 8")
        selectors = getattr(args, "select", None)
        initial_read = getattr(args, "initial_read", False)
        selection_digest = getattr(args, "selection_sha256", None)
        text_append = None
        if pinned_selection is not None:
            if selectors is not None or initial_read or list_fields or list_depth is not None or selection_digest is not None:
                raise ValueError("a pinned selection reference cannot change selectors; use the original capture reference")
            _validate_pinned_selection(pinned_selection)
            if observation := reference.get("observed_read"):
                from ..control_plane.turn_driver.native_first_read import observation_current, observed_first_read_fallback

                if not observation_current(observation, path, digest):
                    fallback = observed_first_read_fallback(observation, path, digest)
                    if fallback is not None:
                        try:
                            _validate_pinned_selection(fallback[1]["selection"])
                        except ValueError:
                            fallback = None
                    if offset:
                        return restart({"ok": False, "reason": "Observed first-read scope changed during paging."},
                            initial_read=fallback is None, fallback=fallback)
                    # Restart in the ordinary complete view of this same capture.
                    if fallback is not None:
                        reference_file, reference = fallback
                        pinned_selection = reference["selection"]
                    else:
                        pinned_selection, initial_read = None, True
                        try:
                            reference_file = write_context_read_reference(path, digest)
                        except OSError:
                            reference_file = None
                else:
                    text_append = observation.get("text_append")
                    if offset == 0:
                        presentation = {**(presentation or {}),
                            **_observed_read_presentation(observation["fields"], text_append)}
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
            try:
                plan, selected_raw, expected = _context_selection_view(payload, select=selectors,
                    initial_read=initial_read, list_fields=list_fields, list_depth=list_depth,
                    expected_sha256=selection_digest, preserve_selection_order=pinned_explicit,
                    present_goal_narrative=(pinned_selection.get("present_goal_narrative") is True
                        if pinned_selection is not None else None),
                    selection_paths=reference.get("selection_paths") if pinned_selection is not None else None,
                    text_append=text_append)
            except ContextSelectionDigestChanged as exc:
                # A verified file digest only binds bytes. Preserve an explicit
                # selection or listing, including a receipt-only diagnostic.
                # Historical initial-read has no explicit scope to preserve.
                return restart({"ok": False, "schema_version": "context_capture_page_v1", "reason": str(exc),
                    "recovery": ("Use recovery_command to read the original capture from offset zero; "
                        if initial_read else "Use recovery_command to restart the original selected read from offset zero; ")
                    + "this read performs no guard, admission or spend."},
                    select=selectors, list_fields=list_fields, list_depth=list_depth)
            if rejection := plan.get("rejected_selection"):
                # TS alone resolves the nearest container. Reuse the original
                # escaped tokens, without parsing diagnostic prose or traversing
                # the payload again in Python. A failed batch delivers no body.
                if selectors is None:
                    raise ValueError("context reader rejected an initial view without explicit selectors")
                pointer = selectors[rejection["argument_index"] - 1]
                parent = "/".join(pointer.split("/")[:rejection["parent_token_count"] + 1])
                return restart({"ok": False, "schema_version": "context_capture_page_v1",
                    "error_kind": "request_rejected", "diagnostic_code": rejection["code"],
                    "reason": rejection["message"],
                    "recovery": "Use recovery_command to --list the nearest verified container in the same capture, "
                        "then retry the corrected batch. No values were delivered and no guard, admission or spend was performed."},
                    select=[parent], list_fields=True)
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
                    reference_file = write_context_read_reference(path, digest, selection=context_selection_binding(
                        selectors, list_fields=list_fields, list_depth=list_depth, sha256=expected,
                        presented=bool(plan.get("presentation_pointers"))),
                        selection_paths=[item["path"] for item in plan["selections"]] if not list_fields else None,
                        reference_file=reference_file if bound_route is not None else None,
                        reader_argv=bound_route)
                except OSError:
                    if bound_route is not None:
                        raise ValueError("Repair the private recovery reference and retry this same read.") from None
                    # Legacy cursors pin the same capture, selection and digest.
                    reference_file = None
        if offset > len(raw) or (offset < len(raw) and raw[offset] & 0xC0 == 0x80):
            raise ValueError("capture offset is outside the file or not a UTF-8 boundary; use the returned next offset")
        return _capture_page(path, raw, digest, offset=offset, size=size,
            output_limit=output_limit, instruction=bool(context_dir),
            presentation=presentation, next_page_bytes=next_page_bytes or (args.page_bytes if bound_route else None),
            next_page_output_bytes=next_page_output_bytes, select=selectors, selection_sha256=selection_digest,
            list_fields=list_fields, list_depth=list_depth, initial_read=initial_read, reference_file=reference_file,
            reader_argv=reader_argv, compact_reader=bound_route is not None)
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
        return _bounded_context_read_failure(result, output_limit)


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
    def command_budget(command: str) -> tuple[int, int]:
        words = shlex.split(command)
        page_bytes, output_bytes = native_context_page_budget()
        if "--page-bytes" in words:
            page_bytes = int(words[words.index("--page-bytes") + 1])
        if "--max-page-output-bytes" in words:
            output_bytes = int(words[words.index("--max-page-output-bytes") + 1])
        return page_bytes, output_bytes

    page_bytes, output_bytes = command_budget(view["command"])

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
    if "replan_review_command" in view:
        result["replan_review_command"] = view["replan_review_command"]
    if "first_read_command" in view:
        recovery = view["recovery"]
        first_page_bytes, first_output_bytes = command_budget(view["first_read_command"])
        if view.get("first_read_reference"):
            # The producer already pinned this same initial selection. Keep
            # that existing cursor instead of asking the reader to replan it.
            result["first_read_command"] = context_capture_read_command(Path(recovery["path"]), recovery["sha256"],
                page_bytes=first_page_bytes, max_page_output_bytes=first_output_bytes,
                reference_file=Path(view["first_read_reference"]), reader_argv=reader_argv)
        else:
            result["first_read_command"] = context_capture_read_command(Path(recovery["path"]), recovery["sha256"],
                page_bytes=first_page_bytes, max_page_output_bytes=first_output_bytes, initial_read=True,
                reference_file=Path(result["recovery"]["reference_file"]), reader_argv=reader_argv)
        if "remaining_read_command" in view:
            result["remaining_read_command"] = view["remaining_read_command"]
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
                              first_read_budget: tuple[int, int],
                              page_attached: bool = False,
                              first_read_requested: bool = False,
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
    if first_read_requested or initial_read_pointers:
        overview = result.pop("overview")
        instruction = result["instruction"]
        if first_read_requested:
            instruction = ("If the initial selection names a Vision authoring step, select the complete vision_authoring object "
                "at that step before composing a Vision. " + instruction)
        elif any(pointer.endswith("/vision_authoring/rule") for pointer in (initial_read_pointers or ())):
            instruction = ("Before composing a Vision, read this step's full vision_authoring from this capture, "
                "or reuse it if the exact field is in previously_delivered_fields and its full text is available. "
                + instruction)
        first_read = ("Read the top-level content and follow next_command if eof is false. "
            "Use first_read_command only to recover missing initial context from this capture"
            if page_attached else
            "Start with first_read_command for current facts and source navigation from this capture")
        result["instruction"] = (first_read + ", without reading an older baseline. "
            "Read each complete optional attachment from its captured step before using it. " + instruction)
        first_page_bytes, first_output_bytes = first_read_budget
        result["first_read_command"] = context_capture_read_command(current_path, current["sha256"],
            page_bytes=first_page_bytes, max_page_output_bytes=first_output_bytes,
            initial_read=True, reader_argv=reader_argv)
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
    """Serialize a compact transport document with the capture file's LF."""
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def _json_output_size(value: dict[str, Any] | bytes) -> int:
    """Count compact JSON with the host's line ending, reusing serialized capture bytes."""
    if isinstance(value, bytes):
        return len(value) + len(os.linesep) - 1
    return len(json.dumps(value, ensure_ascii=False,
        separators=(",", ":")).encode("utf-8")) + len(os.linesep)


def _native_context_read_bytes(reference: dict[str, Any], raw: bytes, *,
                               page_bytes: int, output_bytes: int,
                               stop_after: int | None = None,
                               reader_argv: list[str] | None = None) -> int:
    """Count the reference plus its explicit-file reader before publication.

    Stop once this candidate cannot win. This measures one complete wire read,
    not model tokens, retention or the caller's later selective/recovery reads.
    """
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


def _contains_ordinary_opening(content: str, document: dict[str, Any], plan: dict[str, Any]) -> bool:
    rows = plan.get("ordinary_opening_rows")
    if not rows:
        return False
    prefix, _ = initial_context_selection_bytes(document, plan["plan"]["selections"][:rows])
    # The ordinary page can end inside a later row. Compare the complete
    # serialized control rows before the selection document's closing bytes.
    return content.encode("utf-8").startswith(prefix.removesuffix(b"]}\n"))


def budgeted_ready_opening(navigation: dict[str, Any], plan: dict[str, Any],
                           document: dict[str, Any], content: bytes, *,
                           page_bytes: int, output_bytes: int, reader_argv: list[str] | None,
                           context_directory: str | None,
                           observation: dict[str, Any] | None) -> dict[str, Any]:
    """Keep the ordinary reader when its actual first page carries the controls."""
    capture = plan["capture"]
    path, digest = Path(capture["path"]), capture["sha256"]
    if observation is not None:
        content = observation["content"]
    reference = navigation.get("first_read_reference")
    if reference and content:
        try:
            page = _capture_page(path, content, digest, offset=0, size=page_bytes,
                output_limit=output_bytes, instruction=False, select=[], reference_file=Path(reference),
                presentation=_observed_read_presentation(observation["reused"], observation.get("text_append"))
                    if observation else None,
                reader_argv=reader_argv)
        except ValueError:
            pass  # The ordinary reader has no room for its page framing.
        else:
            if page["eof"] or _contains_ordinary_opening(page["content"], document, plan):
                return navigation
    remaining = None
    if observation is not None and context_directory is not None:
        from ..control_plane.turn_driver.native_first_read import observed_remaining_read_reference

        remaining = observed_remaining_read_reference(context_directory, navigation["target"],
            capture, document, observation, plan["remaining"])
    return _bounded_initial_read(navigation, plan, page_bytes=page_bytes, output_bytes=output_bytes,
        reader_argv=reader_argv, attach_page=False, remaining_reference=remaining) or navigation


def _bounded_initial_read(navigation: dict[str, Any], plan: dict[str, Any], *,
                          page_bytes: int, output_bytes: int, reader_argv: list[str] | None,
                          attach_page: bool = True, remaining_reference: Path | None = None,
                          ordinary_page: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Try the typed opening and keep the remainder pinned to the same capture."""
    if "opening" not in plan or "first_read_command" not in navigation:
        return None
    try:
        path, _, document = _read_capture(plan["capture"]["path"], plan["capture"]["sha256"])
        digest = plan["capture"]["sha256"]
        if ordinary_page and _contains_ordinary_opening(ordinary_page["content"], document, plan):
            return None
        opening, content = write_initial_context_read_reference(path, digest, document, plan["opening"]["selections"])
        if len(content) > page_bytes or len(content) >= output_bytes:
            return None
        remaining = remaining_reference
        if remaining is None:
            remaining, _ = write_initial_context_read_reference(path, digest, document, plan["remaining"]["selections"])

        def command(reference: Path) -> str:
            return context_capture_read_command(path, digest, page_bytes=page_bytes,
                max_page_output_bytes=output_bytes, reference_file=reference, reader_argv=reader_argv)

        guidance = ("This opening covers the decision and read obligations. Before work, read its content, "
            "then follow remaining_read_command and its cursor through the rest of the initial selection. "
            "Both views pin the same capture.")
        navigation = {**navigation, "first_read_command": command(opening), "remaining_read_command": command(remaining),
            "instruction": (guidance + " " + navigation["instruction"] if attach_page
                else navigation["instruction"] + " " + guidance)}
        page = _capture_page(path, content, digest, offset=0, size=len(content), output_limit=None,
            instruction=False, select=[], reference_file=opening, reader_argv=reader_argv)
        # An opening is useful only as a complete, independently readable
        # page. Keep the ordinary cursor when even the opening cannot fit.
        if not attach_page:
            navigation["first_read_reference"] = str(opening)
            return (navigation if len(_native_output_bytes(page)) <= output_bytes
                and len(_native_output_bytes(navigation)) <= min(output_bytes, 7900) else None)
        page["read_instruction"] = (
            "EOF ends the opening. Before work, read its content and follow navigation.remaining_read_command "
            "and its next_command cursor through the remaining initial context. Both views pin the same capture. "
            "EOF does not establish retention, authority or freshness.")
        page["navigation"] = navigation
        return page if len(_native_output_bytes(page)) <= output_bytes else None
    except (OSError, ValueError):
        return None  # The same-capture ordinary reader is already available.


def _saved_context_read_page(args: argparse.Namespace, navigation: dict[str, Any], *,
                             initial_reference: str | None, initial_read_plan: dict[str, Any] | None,
                             output_limit: int | None, reader_argv: list[str]) -> dict[str, Any]:
    """Use the ordinary verified reader; the first page and navigation share its output budget."""
    recovery = navigation["recovery"]
    pinned = initial_reference if args.initial_read else None
    reference = pinned or recovery.get("reference_file")
    read_args = argparse.Namespace(**{**vars(args), "quota_command": "context-read",
        "reference_file": reference, "capture_file": None if reference else recovery["path"],
        "capture_sha256": None if reference else recovery["sha256"],
        "max_page_output_bytes": output_limit,
        "codex_context_dir": None, "codex_context_root": None, "initial_read": bool(args.initial_read and not pinned)})
    page = read_context_capture(read_args, presentation={"navigation": navigation}, reader_argv=reader_argv)
    if page.get("ok") is True:
        if (pinned and initial_read_plan and output_limit is not None and not page["eof"]
                and "previously_delivered_fields" not in page
                and (opening := _bounded_initial_read(navigation, initial_read_plan,
                    page_bytes=args.page_bytes if args.page_bytes is not None else output_limit,
                    output_bytes=output_limit, reader_argv=reader_argv, ordinary_page=page)) is not None):
            return opening
        # Keep the already-budgeted page and cursor ahead of recovery metadata.
        page["navigation"] = page.pop("navigation")
    elif "recovery_command" not in page:
        # Preserve the verified navigation when it fits. The attached read
        # may have been refused precisely because its opening was too large.
        fallback = {"ok": False, **page, "recovery_command": recovery["command"]}
        if output_limit is None or _json_output_size(fallback) <= output_limit:
            return fallback
    return page


def _context_page_output_enabled(args: argparse.Namespace, activation: dict[str, Any]) -> bool:
    return activation["enabled"] or (
        args.quota_command == "context-project" and getattr(args, "initial_read", False)
        and activation["progressive_enabled"])
