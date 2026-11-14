"""Opt-in local transport capture; quota and envelope owners remain unchanged."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import stat
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..control_plane.effect_runtime import EffectRuntimeRejected, EffectRuntimeRemoteError, EffectRuntimeStartupError
from ..control_plane.quota.effective_action import EffectiveAction
from ..control_plane.quota.error_codes import QuotaCommandValidationError, QuotaDecisionTransportError
from ..control_plane.turn_driver.decision_capture import (
    context_index_fields as _context_index_fields,
    write_initial_context_read_reference,
    validate_handoff_socket, CONTEXT_CAPTURE_READ_GUIDANCE, context_capture_read_command,
    CONTEXT_CAPTURE_NAVIGATION_GUIDANCE,
    read_context_capture_file as _read_capture,
    write_context_read_reference, private_capture_uid, open_regular_capture, read_turn_observation_text,
)
from ..control_plane.turn_driver.native_context_profile import (
    NATIVE_CONTEXT_FIRST_READ_FALLBACK_BYTES, NATIVE_CONTEXT_PAGE_BYTES, NATIVE_CONTEXT_PAGE_OUTPUT_BYTES,
    native_context_page_budget,
)
from .quota_context_reader import (
    budgeted_ready_opening,
    _capture_selection_transport_template,
    bind_capture_selection_transport as bind_capture_selection_transport,
    _saved_context_read_page,
    _context_page_output_enabled,
    _capture_read_reference,
    _native_context_read_bytes,
    _native_context_reference,
    _native_output_bytes,
    _validate_context_page_window,
    model_context_reference,
    read_context_capture,
)


def native_context_activation(args: argparse.Namespace) -> dict[str, Any]:
    from ..control_plane.turn_driver.codex_context import context_delivery_for_arguments

    try:
        return context_delivery_for_arguments(args)
    except ValueError as exc:
        raise QuotaCommandValidationError(str(exc)) from exc


def native_context_capture_view(projected: dict[str, Any], directory: Path, *,
                                context_directory: str | None = None,
                                progressive_enabled: bool = True, hook_session: str | None = None,
                                captured_recovery: dict[str, Any] | None = None,
                                metadata: dict[str, Any] | None = None,
                                max_page_output_bytes: int | None = None,
                                first_read_page_bytes: int | None = None,
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
    # The generated opening crosses a tool boundary even when returned as a
    # command. Keep its inferred budget out of bulk and selection recovery.
    first_output_bytes = (output_bytes if max_page_output_bytes is not None
        else min(output_bytes, NATIVE_CONTEXT_FIRST_READ_FALLBACK_BYTES))
    first_page_bytes = (min(page_bytes, first_output_bytes) if first_read_page_bytes is None
        else first_read_page_bytes)
    first_read_budget = first_page_bytes, first_output_bytes
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
    initial_read_content = b""
    # A stateless attached page uses the ordinary verified selector below.
    # Its recovery recipe does not require an extra root index to be planned.
    stateless_first_read = progressive_enabled and page_attached and captured_recovery is not None

    def pin_initial_selection(plan: dict[str, Any]) -> None:
        nonlocal initial_read_pointers, initial_read_reference, initial_read_content
        selections = plan["selections"]
        initial_read_pointers = [item["pointer"] for item in selections]
        try:
            initial_read_reference, initial_read_content = write_initial_context_read_reference(Path(current["path"]), current["sha256"],
                current_document, selections)
        except OSError:
            # Read-only capture roots keep the existing initial recipe;
            # metadata is optional and never a read/authority receipt.
            pass

    interaction = current_document.get("interaction_contract")
    channel = interaction.get("agent_channel") if isinstance(interaction, dict) else None
    stateless_ready = (
        progressive_enabled and initial_read_plan is None and context_directory is None and not page_attached
        and current_document.get("effective_action") == EffectiveAction.NORMAL_RUN.value
        and current_document.get("normal_delivery_allowed") is True
        and isinstance(channel, dict) and channel.get("delivery_allowed") is True
    )
    if stateless_ready:
        try:
            navigation_plan = _turn_envelope_result("context_select", context=current_document,
                select=[""], list_fields=True, include_initial_read_plan=True)
        except (EffectRuntimeRemoteError, EffectRuntimeStartupError):
            pass
        else:
            selection = navigation_plan.get("initial_read_plan")
            initial_read_plan = {"initial": selection is not None,
                                 "plan": navigation_plan if selection is None else selection}

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
    elif progressive_enabled and not stateless_first_read and not stateless_ready:
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
            progressive_enabled=progressive_enabled,
            initial_read_reference=initial_read_reference,
            page_bytes=page_bytes, output_bytes=output_bytes, first_read_budget=first_read_budget,
            page_attached=page_attached, first_read_requested=stateless_first_read, reader_argv=reader_argv)

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
    opening_observation = None
    if (progressive_enabled and observed and "first_read_command" in result
            and observed["pointer_bytes"] <= first_output_bytes // 4):
        first = Path(observed["path"])
        result["first_read_reference"] = str(first)
        result["first_read_command"] = context_capture_read_command(Path(current["path"]), current["sha256"],
            reference_file=first, page_bytes=first_page_bytes,
            max_page_output_bytes=first_output_bytes, reader_argv=reader_argv)
        opening_observation = (initial_read_plan or {}).get("observed_opening")
        if opening_observation is None or opening_observation["path"] != str(first):
            return result
    if not page_attached and initial_read_plan and initial_read_plan.get("linked_opening_detail"):
        return budgeted_ready_opening(result, initial_read_plan, current_document, initial_read_content,
            page_bytes=first_page_bytes, output_bytes=first_output_bytes, reader_argv=reader_argv,
            context_directory=context_directory, observation=opening_observation)
    return result


def project_native_quota_context(payload: dict[str, Any], args: argparse.Namespace, *,
                                 capture_directory: Path, capture_id: str, decision_identity: tuple[str, str],
                                 progressive_enabled: bool, reader_argv: list[str] | None) -> dict[str, Any]:
    """Prepare one already-decided native view; callers retain receipt failures."""
    from ..control_plane.turn_driver.codex_context import (
        NativeContextSessionUnavailable, codex_context_directory, prepare_codex_context,
    )
    try:
        directory = codex_context_directory(directory=args.codex_context_dir,
            root=getattr(args, "codex_context_root", None), create=True)
    except NativeContextSessionUnavailable:
        directory = None  # Current full input, no fabricated native journal.
    prepared = prepare_codex_context(payload, directory=directory,
        capture_directory=capture_directory, goal_id=decision_identity[0], agent_id=decision_identity[1],
        surface=("complete_turn" if getattr(args, "complete_turn", False)
            else "turn_envelope" if getattr(args, "turn_envelope", False) else "quota_packet"),
        capture_id=capture_id, continuity_generation=getattr(args, "_context_delivery_generation", None),
        progressive_enabled=progressive_enabled)
    recovery = prepared.delivery.get("recovery")
    view = native_context_capture_view(prepared.delivery, capture_directory, context_directory=directory,
        progressive_enabled=progressive_enabled, initial_read_plan=prepared.initial_read_plan,
        metadata={"capture_sha256": capture_id, **({"recovery": recovery} if recovery is not None else {})},
        max_page_output_bytes=getattr(args, "max_page_output_bytes", None), reader_argv=reader_argv)
    return model_context_reference(view, reader_argv=reader_argv)


def progressive_context_or_current(payload: dict[str, Any], args: argparse.Namespace, *,
                                   runtime_root: Path, directory: Path | None,
                                   reader_argv: list[str] | None) -> dict[str, Any]:
    """Optional stateless IO falls back to this complete decided input/receipt."""
    try:
        return progressive_context_capture_view(payload, args, runtime_root=runtime_root,
            directory=directory, surface="turn_envelope" if getattr(args, "turn_envelope", False) else "quota_packet",
            reader_argv=reader_argv)
    except (QuotaDecisionTransportError, OSError, ValueError, EffectRuntimeRemoteError, EffectRuntimeStartupError):
        return payload


def progressive_context_capture_view(payload: dict[str, Any], args: argparse.Namespace, *,
                                     runtime_root: Path, directory: Path | None,
                                     surface: str = "quota_packet",
                                     reader_argv: list[str] | None = None,
                                     captured_decision_path: str | None = None,
                                     page_attached: bool = False) -> dict[str, Any]:
    """Same-capture presentation for every caller, without a native journal.

    A plain read-only should-run remains read-only. Private transport files do
    not admit work, mint a Turn or stand in for observed model consumption.
    """
    from .context_machine import _private_transport_root
    from .quota_request import quota_detail_sections_from_args
    from ..control_plane.turn_driver.decision_capture import capture_progressive_context

    # Only a fresh stateless source can supply this route. Saved and explicitly
    # captured decisions keep the reader chosen by their existing callers.
    stage_from_projection = (directory is None and captured_decision_path is None
        and "vision" not in quota_detail_sections_from_args(args))
    if directory is None:
        root = Path(_private_transport_root(runtime_root))
        directory = Path(tempfile.mkdtemp(prefix="context-", dir=root))
    if payload.get("schema_version") == "loopx_turn_envelope_v1":
        surface = "complete_turn"
    transport = _capture_selection_transport_template(payload, args, capture_root=directory.parent)
    required, reference = capture_progressive_context(payload, directory=directory, surface=surface,
        selection_command_args_template=transport, captured_decision_path=captured_decision_path,
        replan_reader_argv=reader_argv if stage_from_projection else None)
    view = native_context_capture_view(required, directory, captured_recovery=reference,
        metadata={"context_delivery": native_context_activation(args),
            **({"capture_sha256": args.capture_sha256} if getattr(args, "capture_sha256", None) else {})},
        max_page_output_bytes=getattr(args, "max_page_output_bytes", None), page_attached=page_attached, reader_argv=reader_argv)
    readable = model_context_reference(view, reader_argv=reader_argv)
    # Small results need no extra navigation. The complete original and required
    # captures remain available; never shrink by dropping current obligations.
    return readable if page_attached or len(_native_output_bytes(readable)) < len(_native_output_bytes(required)) else required


def _saved_context_transport_failure(
    error: Exception, *, args: argparse.Namespace, receipt: dict[str, Any],
    recovery: dict[str, Any], reader_argv: list[str], source_unverified: bool = False,
) -> dict[str, Any]:
    page_bytes, output_bytes = native_context_page_budget()
    limit = getattr(args, "max_page_output_bytes", None)
    if isinstance(limit, int) and limit > 0:
        output_bytes = min(output_bytes, limit)
        page_bytes = min(page_bytes, output_bytes)
    if source_unverified:
        instruction = (
            "The saved source could not be verified. Use recovery.command for diagnostic receipt readback. "
            "Restore an intact, supported capture and retry context-project with its original Goal, Agent, Turn "
            "and digest before using it as work context. Preserve the original receipt and do not rerun admission."
        )
    else:
        instruction = (
            "Context presentation was not published. Discover current fields with recovery.command, then select "
            "only the facts the current decision needs from the original capture; do not drain unrelated optional "
            "material or rerun admission. Preserve its Turn receipt. If a selected value spans pages, follow "
            "next_command only for that selection and verify contiguous offsets, total_bytes and sha256. "
            "Authority and freshness are unchanged. " + CONTEXT_CAPTURE_NAVIGATION_GUIDANCE
        )
    return {"ok": False, "error_code": QuotaDecisionTransportError.error_code,
        "reason": " ".join(str(error).split())[:500],
        "heartbeat_receipt": receipt, "capture_sha256": args.capture_sha256,
        "recovery": {**recovery, "command": context_capture_read_command(
            Path(recovery["path"]), recovery["sha256"], page_bytes=page_bytes,
            max_page_output_bytes=output_bytes, list_fields=not source_unverified,
            select=["/heartbeat_receipt"] if source_unverified else None, reader_argv=reader_argv)},
        "instruction": instruction}


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
    from ..control_plane.quota.turn_envelope import (
        COMPLETE_TURN_DECISION_VERSION, assert_complete_turn_decision, is_legacy_saved_quota_decision,
    )

    reader_argv = current_loopx_cli_argv()
    try:
        activation = native_context_activation(args)
        saved_page = bool(getattr(args, "initial_read", False) or getattr(args, "select", None))
        if saved_page and (args.format != "json" or not _context_page_output_enabled(args, activation) or args.offset != 0
                or args.reference_file or args.selection_sha256 or (args.initial_read and args.select)):
            raise ValueError("Use JSON context-project at offset zero with native delivery or progressive --initial-read. "
                "Choose either --initial-read or --select. Use context-read for replay or continuation.")
        caller_output_limit = getattr(args, "max_page_output_bytes", None)
        output_limit = caller_output_limit
        if args.initial_read:
            # Keep the inferred limit on the attached read. Bulk recovery and
            # same-Turn selection use only the caller's explicit options.
            output_limit = (NATIVE_CONTEXT_FIRST_READ_FALLBACK_BYTES if output_limit is None
                else min(output_limit, NATIVE_CONTEXT_PAGE_OUTPUT_BYTES))
        if saved_page and args.page_bytes is not None:
            _validate_context_page_window(args.offset, args.page_bytes, output_limit)
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
    version = payload.get("schema_version")
    complete = version == COMPLETE_TURN_DECISION_VERSION
    try:
        if not complete and not is_legacy_saved_quota_decision(payload):
            if activation["enabled"]:
                from ..control_plane.turn_driver.codex_context import retire_rejected_saved_context

                retire_rejected_saved_context(directory=args.codex_context_dir, context_root=args.codex_context_root,
                    goal_id=args.goal_id, agent_id=args.agent_id, capture_sha256=args.capture_sha256,
                    hook_session=hook_session)
            raise QuotaDecisionTransportError("Unsupported saved quota decision version or mixed Turn binding.")
        if complete and not activation["enabled"] and not activation["progressive_enabled"]:
            assert_complete_turn_decision(payload)
    except (QuotaDecisionTransportError, EffectRuntimeRemoteError, EffectRuntimeStartupError) as exc:
        return _saved_context_transport_failure(exc, args=args, receipt=receipt,
            recovery=full_reference, reader_argv=reader_argv, source_unverified=True)

    mode = "full"
    target = None
    native_prepared = False
    native_view = None
    initial_reference = None
    initial_read_plan = None
    if not activation["enabled"] and activation["progressive_enabled"]:
        # Recovery is presentation of this exact saved decision, including its
        # original Turn. It does not need a supported host or a native session.
        try:
            navigation = progressive_context_capture_view(payload, args,
                runtime_root=Path(getattr(args, "_context_delivery_machine_runtime", path.parent)),
                directory=None, reader_argv=reader_argv, captured_decision_path=str(path), page_attached=saved_page)
            if saved_page:
                return _saved_context_read_page(args, navigation, initial_reference=None,
                    initial_read_plan=None, output_limit=output_limit, reader_argv=reader_argv)
            return navigation
        except (QuotaDecisionTransportError, OSError, ValueError, EffectRuntimeRemoteError, EffectRuntimeStartupError) as exc:
            if complete:
                return _saved_context_transport_failure(exc, args=args, receipt=receipt,
                    recovery=full_reference, reader_argv=reader_argv, source_unverified=True)
            # Historical readers keep their original fallback below.
    try:
        from ..control_plane.turn_driver.codex_context import codex_context_directory, prepare_codex_context

        if not activation["enabled"]:
            raise ValueError("context delivery is off")
        page_bytes, output_bytes = native_context_page_budget()
        if caller_output_limit is not None:
            if caller_output_limit <= 0:
                raise QuotaDecisionTransportError("Native context output limit must be positive.")
            output_bytes = min(output_bytes, caller_output_limit)
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
        presentation = payload
        if args.initial_read and activation["progressive_enabled"] and not complete:
            from .quota_presentation import _project_quota_cli_payload
            from ..control_plane.quota.turn_envelope import project_captured_replan_cli_view

            # Keep the saved bytes intact. Stage unstaged readers against the
            # exact path just verified here, including a digest-bound copy.
            presentation = _project_quota_cli_payload(
                project_captured_replan_cli_view(payload, captured_decision_path=str(path)),
                argparse.Namespace(quota_command="should-run"), frozenset(), None,
            )
            if "host_poll_receipt" in payload:
                presentation["host_poll_receipt"] = payload["host_poll_receipt"]
        prepared = prepare_codex_context(presentation, directory=directory, capture_directory=cache,
            goal_id=args.goal_id, agent_id=args.agent_id, surface="complete_turn" if complete else "quota_packet",
            capture_id=hashlib.sha256(raw).hexdigest(),
            continuity_generation=getattr(args, "_context_delivery_generation", None),
            progressive_enabled=activation["progressive_enabled"], selection_command_args_template=transport,
            captured_decision_path=str(path) if args.initial_read and activation["progressive_enabled"] else None,
            hook_session=hook_session, saved_capture=True)
        projected = prepared.delivery
        initial_read_plan = prepared.initial_read_plan
        native_prepared = True
        if projected.get("schema_version") == "context_delivery_v1":
            # Native publication owns the complete required view. Keep its
            # committed recovery even if optional wire/inline delivery fails;
            # the input capture digest below remains the original identity.
            full_reference = _capture_read_reference(projected["recovery"],
                page_bytes=page_bytes, output_bytes=output_bytes, reader_argv=reader_argv)
            reference = dict(full_reference)
        else:
            bind_capture_selection_transport(projected, args, capture_root=root)
        native_view = native_context_capture_view(projected, cache, context_directory=directory, hook_session=hook_session,
            progressive_enabled=activation["progressive_enabled"], initial_read_plan=prepared.initial_read_plan,
            metadata={"recovery": full_reference, "capture_sha256": args.capture_sha256,
                "context_delivery": activation},
            max_page_output_bytes=caller_output_limit,
            first_read_page_bytes=args.page_bytes if args.initial_read else None, page_attached=saved_page,
            reader_argv=reader_argv)
        reference = {key: native_view[key] for key in ("path", "sha256", "command")}
        mode, target = native_view["mode"], native_view.get("target")
        initial_reference = native_view.get("first_read_reference")
        native_view = model_context_reference(native_view, reader_argv=reader_argv)
    except (EffectRuntimeRemoteError, EffectRuntimeStartupError) as exc:
        if not complete:
            raise
        return _saved_context_transport_failure(exc, args=args, receipt=receipt,
            recovery=full_reference, reader_argv=reader_argv, source_unverified=not native_prepared)
    except (QuotaDecisionTransportError, OSError, ValueError, TypeError, RecursionError) as exc:
        if native_view is not None and not isinstance(exc, QuotaDecisionTransportError):
            # The native view and journal are already valid. Publishing the
            # shorter reader metadata is optional; retain their exact legacy
            # recovery rather than losing the original capture or admitting again.
            pass
        elif isinstance(exc, QuotaDecisionTransportError) or (activation["enabled"] and not native_prepared):
            if not isinstance(exc, QuotaDecisionTransportError):
                exc = QuotaDecisionTransportError("Current context recovery could not be prepared.")
            return _saved_context_transport_failure(exc, args=args, receipt=receipt,
                recovery=full_reference, reader_argv=reader_argv, source_unverified=complete and not native_prepared)
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
        return _saved_context_read_page(args, navigation, initial_reference=initial_reference,
            initial_read_plan=initial_read_plan, output_limit=output_limit, reader_argv=reader_argv)
    return navigation


def prepare_quota_capture(args: argparse.Namespace, directory: Path | None) -> Path | None:
    """Validate transport options before allocating a new guard capture."""
    if getattr(args, "scheduler_observation", False):
        from .quota_request import validate_scheduler_observation_request

        validate_scheduler_observation_request(args)
        if directory is not None:
            raise QuotaCommandValidationError("--scheduler-observation does not prepare a capture")
        return None
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
        handoff_root = None
        if root is not None:
            handoff_root = Path(root).expanduser().absolute()
        elif requested is not None:
            handoff_root = Path(requested).expanduser().absolute().parent
        validate_handoff_socket(handoff, capture_root=handoff_root)
        if handoff_root is None:
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
        if getattr(args, "prepared_capture_reference", False):
            # Bind the physical parent before admission: the saved selection
            # uses it as an output root, which must not be a symlink.
            directory = Path(os.path.realpath(directory.parent, strict=True)) / directory.name
        # A new directory per invocation prevents overwriting a prior observation,
        # including a prior invocation in the same Turn. Existing symlinks fail too.
        directory.mkdir(mode=0o700)
    except OSError as exc:
        raise QuotaCommandValidationError(
            "decision capture needs a new directory or an existing writable root; "
            f"no guard was run: {exc}"
        ) from exc
    return directory


def capture_decision(directory: Path | None, payload: dict[str, object], *,
                     args: argparse.Namespace | None = None, native_context: bool = False,
                     record_capture: Callable[[dict[str, object], str], None] | None = None) -> str | None:
    if directory is None:
        return
    from ..control_plane.turn_driver.decision_capture import write_decision_capture

    try:
        digest = write_decision_capture(directory / "decision.json", payload, publish=os.replace)
    except (OSError, ValueError) as exc:
        raise QuotaDecisionTransportError(
            "Decision capture failed after guard evaluation; the Turn receipt may "
            "already exist. Do not treat this as missing admission or blindly rerun "
            f"the guard. Inspect the existing Turn receipt. Capture directory: {directory}"
        ) from exc
    if record_capture is not None:
        record_capture(payload, digest)
    if native_context and args is not None and args.quota_command == "should-run" and payload.get("ok") is True:
        from ..control_plane.turn_driver.codex_context import record_native_claim_source

        try:
            record_native_claim_source(payload, digest, directory=args.codex_context_dir,
                context_root=getattr(args, "codex_context_root", None))
        except (OSError, ValueError) as exc:
            raise QuotaDecisionTransportError(
                "The decision was saved, but the native Host could not register it. "
                "Read the original decision and receipt before recovering this Turn."
            ) from exc
    return digest


def settlement_capture_failure_view(
    payload: dict[str, object], *, output_bytes: int, error: QuotaDecisionTransportError,
) -> dict[str, object]:
    """Keep the evaluated result controls available when capture fails."""
    omitted = [key for key in ("before", "after") if key in payload]
    view = {key: value for key, value in payload.items() if key not in omitted}
    view["result_delivery_error"] = {
        "error_code": error.error_code,
        "reason": str(error),
        "recommended_action": "Preserve the original result controls and Turn identity. "
            "Repair capture privacy or remove --max-page-output-bytes and read back the "
            "same operation; do not repeat admission or assume it did not commit.",
        "omitted_diagnostics": omitted,
    }
    if (view.get("ok") is True and (view.get("appended") or view.get("idempotent_replay"))
            and view.get("json_path") and "quota_event" in view
            and len(_native_output_bytes(view)) + len(os.linesep) - 1 > output_bytes):
        # The durable spend record already contains the event and its snapshots.
        view.pop("quota_event")
        omitted.append("quota_event")
    return view


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


def handoff_decision(directory: Path | None, socket_path: str | None,
                     context_delivery_enabled: bool = False, *,
                     capture_sha256: str | None) -> tuple[dict[str, Any], str] | None:
    """Withhold the original output until the Host releases these exact bytes.

    No second guard, admission, settlement or retry is performed here. A lost
    acknowledgement leaves the saved decision available for original-identity
    recovery; it must never be silently replaced by a later observation.
    """
    if socket_path is None:
        return None
    if directory is None or capture_sha256 is None:
        raise ValueError("decision handoff requires a captured decision and its published digest")
    path = directory / "decision.json"
    try:
        with os.fdopen(open_regular_capture(path), "rb") as source:
            raw = source.read()
        if hashlib.sha256(raw).hexdigest() != capture_sha256:
            raise ValueError("captured decision changed before handoff")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
            channel.settimeout(120)
            channel.connect(str(validate_handoff_socket(socket_path)))
            channel.sendall(json.dumps({"path": str(path), "sha256": capture_sha256}).encode() + b"\n")
            with channel.makefile("rb") as stream:
                line = stream.readline(1025)
            response = json.loads(line)
            ordinary_release = {"released": True, "sha256": capture_sha256}
            if response not in (ordinary_release, {**ordinary_release, "context_delivery": True}):
                raise ValueError("Host did not release the original decision digest")
        with os.fdopen(open_regular_capture(path), "rb") as source:
            if source.read(len(raw) + 1) != raw:
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
                return {}, capture_sha256
            raw = source.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            return {}, capture_sha256
        request = json.loads(raw)
        return (request if isinstance(request, dict) else {}), capture_sha256
    except (OSError, ValueError, RecursionError):
        return {}, capture_sha256


def handle_native_context_command(args: argparse.Namespace, *, print_payload: Callable[..., None],
                                  registry_path: Path | None = None, runtime_root_arg: str | None = None) -> int | None:
    """Dispatch captured reads and native lifecycle IO before quota authority access."""
    if getattr(args, "complete_turn", False) and (args.quota_command != "should-run" or args.format != "json"):
        print_payload({"ok": False, "reason": "--complete-turn requires JSON quota should-run"}, args.format, str)
        return 1
    if getattr(args, "scheduler_observation", False):
        from .quota_request import validate_scheduler_observation_request

        try:
            # Validate the compact observation as a closed request shape before
            # context-profile defaults or any quota authority can mutate/add
            # reader, Turn, receipt or capture options.
            validate_scheduler_observation_request(args)
        except QuotaCommandValidationError as exc:
            print_payload({"ok": False, "reason": str(exc)}, args.format, str)
            return 1
        return None
    if getattr(args, "initial_read", False) and args.quota_command not in ("context-read", "context-project"):
        print_payload({"ok": False, "reason": "--initial-read is only supported by context-read or JSON context-project"},
                      args.format, str)
        return 1
    list_depth = getattr(args, "list_depth", None)
    if list_depth is not None and (args.quota_command != "context-read" or not getattr(args, "list_fields", False)
            or type(list_depth) is not int or not 1 <= list_depth <= 8):
        print_payload({"ok": False, "reason": "--list-depth requires context-read --list and an integer from 1 to 8"}, args.format, str)
        return 1
    if getattr(args, "list_fields", False) and args.quota_command != "context-read":
        print_payload({"ok": False, "reason": "--list is only supported by context-read"}, args.format, str)
        return 1
    has_output_limit = getattr(args, "max_page_output_bytes", None) is not None
    settlement_output = (args.quota_command == "spend-slot" and args.format == "json"
        and bool(getattr(args, "turn_instance_id", None)) and bool(getattr(args, "agent_id", None)))
    budget_scope_error = ("max-page-output-bytes is only supported by JSON context-read "
        "or enabled native should-run/context-project, progressive context-project --initial-read, "
        "or Turn-bound JSON spend-slot")
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
            if not _context_page_output_enabled(args, native_context_activation(args)):
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
            LegacyNativePreparationCapture,
            codex_execution_preparation_context,
            codex_execution_preparation_readback_context,
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
                request = None
                preparation_context = ""
                try:
                    workspace = Path(event.get("cwd") or "")
                    if workspace.is_absolute() and workspace.resolve() == Path.cwd().resolve():
                        request = native_execution_preparation_request(directory, event)
                    if request is not None:
                        if "claim_source_sha256" not in request or not request.get("turn_instance_id"):
                            raise LegacyNativePreparationCapture()
                        if request.get("readback_only") is True:
                            preparation_context = codex_execution_preparation_readback_context(request) or ""
                        else:
                            capture = Path(tempfile.mkdtemp(prefix="prepared-", dir=directory)) / "ready"
                            receipt = execute_codex_execution_preparation(
                                request,
                                cwd=workspace.resolve(),
                                scenario=activation["scenario"],
                                capture_directory=capture,
                                context_directory=Path(directory),
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
                            source = capture / "decision.json"
                            # The saved-capture reader never rereads authority or admits work.
                            digest = receipt.pop("capture_sha256")
                            receipt.pop("capture_path")
                            receipt["context"] = project_saved_context_capture(argparse.Namespace(**{
                                **vars(args), "quota_command": "context-project",
                                "capture_file": str(source),
                                "capture_sha256": digest,
                                "goal_id": request["goal_id"], "agent_id": request["agent_id"],
                                "turn_instance_id": request["turn_instance_id"],
                            }), hook_session=event["session_id"])
                            context = receipt["context"]
                            if context.get("ok") is False:
                                recovery = context.get("recovery")
                                if isinstance(recovery, dict) and isinstance(recovery.get("command"), str):
                                    context["recovery_command"] = recovery["command"]
                                else:
                                    from ..command_invocation import current_loopx_cli_argv

                                    context["recovery_command"] = context_capture_read_command(
                                        source, digest, reader_argv=current_loopx_cli_argv())
                            preparation_context = codex_execution_preparation_context(receipt)
                except LegacyNativePreparationCapture as error:
                    preparation_context = str(error)
                except (OSError, RuntimeError, subprocess.SubprocessError, ValueError, TypeError, RecursionError):
                    # Reconcile only the attempted source. A concurrent guard
                    # can replace recovery, so it must never be reserved here.
                    if request is not None and request.get("turn_instance_id"):
                        readback = native_execution_preparation_request(
                            directory, event, readback_for=request["source_sha256"],
                        )
                        if readback is not None:
                            preparation_context = codex_execution_preparation_readback_context(readback) or ""
                if preparation_context:
                    existing = payload.get("hookSpecificOutput")
                    existing_context = str(existing.get("additionalContext") or "") if isinstance(existing, dict) else ""
                    payload["hookSpecificOutput"] = {
                        "hookEventName": event["hook_event_name"],
                        "additionalContext": " ".join(filter(None, [existing_context, preparation_context])),
                    }
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
    if args.quota_command == "replan-view":
        return handle_replan_view(args, print_payload)
    return None


def read_replan_view(args: argparse.Namespace) -> dict[str, Any]:
    """Read a captured observation only; no registry, provider, guard or receipts."""
    from ..control_plane.quota.turn_envelope import _turn_envelope_result
    from ..command_invocation import current_loopx_cli_argv

    if not args.decision_file or not args.source_hash:
        raise ValueError("replan-view requires --decision-file and --source-hash")
    capture_sha256 = getattr(args, "capture_sha256", None)
    if capture_sha256 is None:
        path = Path(args.decision_file).expanduser()
        payload = json.loads(read_turn_observation_text(path))
    else:
        path, _, payload = _read_capture(args.decision_file, capture_sha256, private=True)
    return _turn_envelope_result("replan_view", payload=payload, source_hash=args.source_hash,
                                 captured_decision_path=str(path), reader_argv=current_loopx_cli_argv(),
                                 stage=args.replan_stage, outcome_ref=args.outcome_ref,
                                 **({"capture_sha256": capture_sha256} if capture_sha256 is not None else {}))


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
