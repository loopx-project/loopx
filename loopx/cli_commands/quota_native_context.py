"""Native context projection, lifecycle hook, and saved-capture command handlers."""
from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import tempfile
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from functools import partial
from pathlib import Path
from typing import Any

from ..control_plane.effect_runtime import (
    EffectRuntimeRejected,
    EffectRuntimeRemoteError,
    EffectRuntimeStartupError,
)
from ..control_plane.quota.error_codes import (
    QuotaCommandValidationError,
    QuotaDecisionTransportError,
)
from ..control_plane.turn_driver.codex_execution_preparation import (
    NativeRecoveryLeaseUnavailable,
    guard_native_recovery_publication,
    require_native_recovery_lease,
)
from ..control_plane.turn_driver.decision_capture import (
    CONTEXT_CAPTURE_NAVIGATION_GUIDANCE,
    CONTEXT_CAPTURE_READ_GUIDANCE,
    context_capture_read_command,
    read_context_capture_file as _read_capture,
    read_turn_observation_text,
)
from ..control_plane.turn_driver.native_context_profile import (
    NATIVE_CONTEXT_FIRST_READ_FALLBACK_BYTES,
    NATIVE_CONTEXT_PAGE_OUTPUT_BYTES,
    native_context_page_budget,
)
from .quota_context_reader import (
    _capture_read_reference,
    _capture_selection_transport_template,
    _context_page_output_enabled,
    _saved_context_read_page,
    _validate_context_page_window,
    bind_capture_selection_transport,
    model_context_reference,
)


def _saved_context_transport_failure(
    error: Exception, *, args: argparse.Namespace, receipt: dict[str, Any],
    recovery: dict[str, Any], reader_argv: list[str], source_unverified: bool = False,
    authority_unverified: bool = False,
) -> dict[str, Any]:
    page_bytes, output_bytes = native_context_page_budget()
    limit = getattr(args, "max_page_output_bytes", None)
    if isinstance(limit, int) and limit > 0:
        output_bytes = min(output_bytes, limit)
        page_bytes = min(page_bytes, output_bytes)
    if authority_unverified:
        instruction = ("Read the original receipt with recovery.command. The saved capture remains readable, "
                       "but publishing ready work requires the original current lease.")
    elif source_unverified:
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
            max_page_output_bytes=output_bytes, list_fields=not (source_unverified or authority_unverified),
            select=["/heartbeat_receipt"] if source_unverified or authority_unverified else None,
            reader_argv=reader_argv)},
        "instruction": instruction}


def project_saved_context_capture(
    args: argparse.Namespace, *, hook_session: str | None = None,
    registry_path: Path | None = None, runtime_root_arg: str | None = None,
) -> dict[str, Any]:
    """Present an immutable already-decided quota capture.

    Resumed Host preparation rechecks the original claim before publication.
    Historical capture projection remains independent of live authority.

    Publication is delayed until this native tool runs, so the lifecycle owner
    can select its current continuity basis. Optional codec failure returns the
    committed required-view reference when available, otherwise the original
    full read reference. An unsafe recovery selector fails explicitly
    with that same reference, never a replacement guard.
    Invalid run-mode opt-ins fail before that optional transport path.
    """
    from . import quota_capture
    from ..command_invocation import current_loopx_cli_argv
    from ..control_plane.quota.turn_envelope import (
        COMPLETE_TURN_DECISION_VERSION, assert_complete_turn_decision, is_legacy_saved_quota_decision,
    )

    reader_argv = current_loopx_cli_argv()
    prepared_claim = getattr(args, "prepared_claim_operation_id", None)
    try:
        activation = quota_capture.native_context_activation(args)
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
        path, _, payload = _read_capture(args.capture_file, args.capture_sha256,
                                         private=hook_session is not None or prepared_claim is not None)
        if payload.get("ok") is False:
            raise ValueError("The saved capture is diagnostic. Read it with quota context-read.")
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
    publication_guard: Callable[[], AbstractContextManager[None]] = nullcontext

    def authority_refusal(error: Exception) -> dict[str, Any]:
        return _saved_context_transport_failure(error, args=args, receipt=receipt,
            recovery=full_reference, reader_argv=reader_argv, authority_unverified=True)

    if prepared_claim is not None:
        from ..history import load_registry
        from ..paths import resolve_runtime_root

        try:
            if not complete or registry_path is None:
                raise ValueError("Native readiness recovery requires a bound complete Turn.")
            assert_complete_turn_decision(payload)
            registry_path = registry_path.resolve()
            runtime_root = resolve_runtime_root(load_registry(registry_path), runtime_root_arg).resolve()
            require_native_recovery_lease(payload, claim_operation_id=prepared_claim,
                                          registry_path=registry_path, runtime_root=runtime_root)
        except (OSError, RuntimeError, ValueError, TypeError) as exc:
            return authority_refusal(exc)
        publication_guard = partial(guard_native_recovery_publication, payload,
            claim_operation_id=prepared_claim, registry_path=registry_path, runtime_root=runtime_root)
    try:
        if not complete and not is_legacy_saved_quota_decision(payload):
            if activation["enabled"]:
                from ..control_plane.turn_driver.codex_context import retire_rejected_saved_context

                retire_rejected_saved_context(directory=args.codex_context_dir, context_root=args.codex_context_root,
                    goal_id=args.goal_id, agent_id=args.agent_id, capture_sha256=args.capture_sha256,
                    hook_session=hook_session)
            raise QuotaDecisionTransportError("Unsupported saved quota decision version or mixed Turn binding.")
        if complete and prepared_claim is None and not activation["enabled"] and not activation["progressive_enabled"]:
            assert_complete_turn_decision(payload)
    except (QuotaDecisionTransportError, EffectRuntimeRemoteError, EffectRuntimeStartupError) as exc:
        return _saved_context_transport_failure(exc, args=args, receipt=receipt,
            recovery=full_reference, reader_argv=reader_argv, source_unverified=True)

    captured_decision = {"path": str(path)}
    if complete and activation["progressive_enabled"]:
        from ..control_plane.runtime.private_permissions import require_private_path

        try:
            require_private_path(path)
        except OSError:
            # A manually restored capture can still use the checked historical reader.
            pass
        else:
            captured_decision["sha256"] = args.capture_sha256

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
            navigation = quota_capture.progressive_context_capture_view(payload, args,
                runtime_root=Path(getattr(args, "_context_delivery_machine_runtime", path.parent)),
                directory=None, reader_argv=reader_argv, captured_decision=captured_decision, page_attached=saved_page)
            with publication_guard():
                if saved_page:
                    return _saved_context_read_page(args, navigation, initial_reference=None,
                        initial_read_plan=None, output_limit=output_limit, reader_argv=reader_argv)
                return navigation
        except NativeRecoveryLeaseUnavailable as exc:
            return authority_refusal(exc)
        except (QuotaDecisionTransportError, OSError, ValueError, EffectRuntimeRemoteError, EffectRuntimeStartupError) as exc:
            if complete:
                return _saved_context_transport_failure(exc, args=args, receipt=receipt,
                    recovery=full_reference, reader_argv=reader_argv, source_unverified=True)
            # Historical readers keep their original fallback below.
    try:
        from ..control_plane.turn_driver.codex_context import codex_context_directory, prepare_codex_context

        if not activation["enabled"]:
            raise ValueError("context delivery is off")
        page_bytes, output_bytes = quota_capture.native_context_page_budget()
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
            capture_id=args.capture_sha256,
            continuity_generation=getattr(args, "_context_delivery_generation", None),
            progressive_enabled=activation["progressive_enabled"], max_page_output_bytes=caller_output_limit,
            selection_command_args_template=transport,
            captured_decision=captured_decision if activation["progressive_enabled"]
                and not args.select and not getattr(args, "list_fields", False)
                and (args.initial_read or "sha256" in captured_decision) else None,
            hook_session=hook_session, saved_capture=True, publication_guard=publication_guard)
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
        native_view = quota_capture.native_context_capture_view(
            projected, cache, context_directory=directory, hook_session=hook_session,
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
    except NativeRecoveryLeaseUnavailable as exc:
        return authority_refusal(exc)
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
    try:
        with nullcontext() if native_prepared else publication_guard():
            if saved_page:
                return _saved_context_read_page(args, navigation, initial_reference=initial_reference,
                    initial_read_plan=initial_read_plan, output_limit=output_limit, reader_argv=reader_argv)
            return navigation
    except NativeRecoveryLeaseUnavailable as exc:
        return authority_refusal(exc)


def _handle_context_config(
    args: argparse.Namespace,
    *,
    activation: dict[str, Any],
    print_payload: Callable[..., None],
    registry_path: Path | None,
    runtime_root_arg: str | None,
) -> int:
    from ..command_invocation import loopx_cli_argv
    from ..control_plane.turn_driver.codex_context import codex_context_configuration

    try:
        if not activation["enabled"]:
            payload: dict[str, Any] = {"ok": True, "context_delivery": activation}
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


def _handle_context_hook(args: argparse.Namespace, *, activation: dict[str, Any]) -> int:
    import sys
    from ..control_plane.effect_runtime import MAX_REQUEST_BYTES
    from ..control_plane.turn_driver.codex_context import (
        CONTEXT_LIFECYCLE_HOOKS, codex_context_directory, codex_context_hook,
        native_context_event_eligible, native_execution_preparation_request,
        record_native_execution_preparation,
    )
    from ..control_plane.turn_driver.codex_execution_preparation import (
        LegacyNativePreparationCapture,
        NativeClaimRejected,
        NativePreparationTurnUnavailable,
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
            if directory is None:
                raise ValueError("context directory unavailable")
            scenario = activation["scenario"]
            progressive_enabled = activation["progressive_enabled"]
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
                        preparation_context = codex_execution_preparation_readback_context(
                            request, scenario=scenario, context_directory=directory,
                            progressive_enabled=progressive_enabled,
                        ) or ""
                    else:
                        capture = Path(tempfile.mkdtemp(prefix="prepared-", dir=directory)) / "ready"
                        receipt = execute_codex_execution_preparation(
                            request,
                            cwd=workspace.resolve(),
                            scenario=scenario,
                            capture_directory=capture,
                            context_directory=Path(directory),
                            progressive_enabled=progressive_enabled,
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
            except (NativeClaimRejected, NativePreparationTurnUnavailable) as error:
                preparation_context = str(error)
                if request is not None:
                    record_native_execution_preparation(
                        directory, event, source_sha256=request["source_sha256"], receipt=None,
                        claim_rejected=isinstance(error, NativeClaimRejected),
                    )
            except (OSError, RuntimeError, subprocess.SubprocessError, ValueError, TypeError, RecursionError):
                # Reconcile only the attempted source. A concurrent guard
                # can replace recovery, so it must never be reserved here.
                if request is not None and request.get("turn_instance_id"):
                    readback = native_execution_preparation_request(
                        directory, event, readback_for=request["source_sha256"],
                    )
                    if readback is not None:
                        preparation_context = codex_execution_preparation_readback_context(
                            readback, scenario=scenario, context_directory=directory,
                            progressive_enabled=progressive_enabled,
                        ) or ""
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


def handle_native_context_command(args: argparse.Namespace, *, print_payload: Callable[..., None],
                                  registry_path: Path | None = None, runtime_root_arg: str | None = None) -> int | None:
    """Dispatch captured reads and native lifecycle IO before quota authority access."""
    from . import quota_capture

    if getattr(args, "prepared_claim_operation_id", None) is not None and args.quota_command != "context-project":
        print_payload({"ok": False, "reason": "--prepared-claim-operation-id requires quota context-project"},
                      args.format, str)
        return 1
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
            quota_capture.private_capture_uid()
        except ValueError as exc:
            print_payload({"ok": False, "reason": str(exc), "appended": False,
                "registry_mutated": False}, args.format, str)
            return 1
    if has_output_limit and settlement_output and args.max_page_output_bytes <= 0:
        print_payload({"ok": False, "reason": "max-page-output-bytes must be positive"}, args.format, str)
        return 1
    if has_output_limit and args.quota_command != "context-read" and not settlement_output:
        try:
            if not _context_page_output_enabled(args, quota_capture.native_context_activation(args)):
                raise QuotaCommandValidationError(budget_scope_error)
            if args.max_page_output_bytes <= 0:
                raise QuotaCommandValidationError("max-page-output-bytes must be positive")
        except QuotaCommandValidationError as exc:
            print_payload({"ok": False, "reason": str(exc)}, args.format,
                lambda value: str(value["reason"]))
            return 1
    if args.quota_command in ("context-config", "context-hook"):
        try:
            activation = quota_capture.native_context_activation(args)
        except QuotaCommandValidationError as exc:
            payload = {"ok": False, "reason": str(exc)}
            print_payload(payload, args.format, lambda value: json.dumps(value, ensure_ascii=False, indent=2))
            return 1
        if args.quota_command == "context-config":
            return _handle_context_config(
                args,
                activation=activation,
                print_payload=print_payload,
                registry_path=registry_path,
                runtime_root_arg=runtime_root_arg,
            )
        return _handle_context_hook(args, activation=activation)
    if args.quota_command == "context-project":
        payload = project_saved_context_capture(args, registry_path=registry_path, runtime_root_arg=runtime_root_arg)
        print_payload(payload, args.format, lambda value: str(value.get("command", value.get("reason", ""))))
        return 0 if payload["ok"] else 1
    if args.quota_command == "context-read":
        from ..command_invocation import current_loopx_cli_argv

        # Presentation recovery reads the original capture, never current Goal
        # state. In particular it must not evaluate a guard or append a receipt.
        payload = quota_capture.read_context_capture(args, reader_argv=current_loopx_cli_argv())
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
