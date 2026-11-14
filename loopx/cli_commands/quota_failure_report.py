"""Owner-local failure reporting for the quota CLI command.

Keeping the honest failure and validation payloads beside the command handler
pushed that module over its size budget. They form one cohesive unit: what gets
logged, what the operator sees, and how a rejected request is reported without
inventing success.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections.abc import Callable, Mapping
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path

from ..command_invocation import (
    current_loopx_cli_argv,
    loopx_cli_argv,
    loopx_cli_route,
    resolve_command_path,
    same_context_reader_route,
)
from ..control_plane.coordination.legacy_writer_fence import (
    LegacyCoordinationWriterFenced,
)
from ..control_plane.coordination.local_authority import (
    LocalCoordinationAuthorityUnavailable,
)
from ..control_plane.effect_runtime import (
    EffectRuntimeHostPermissionError,
    EffectRuntimeRejected,
    EffectRuntimeRemoteError,
    EffectRuntimeStartupError,
)
from ..control_plane.quota.effective_action import EffectiveAction
from ..control_plane.quota.error_codes import (
    CloseoutQueryUnavailableError,
    HeartbeatReceiptIdentityConflictError,
    QuotaActionSelectionConflictError,
    QuotaCommandValidationError,
    QuotaDecisionTransportError,
    QuotaIdentityPreconditionError,
    quota_error_code,
)
from ..file_lock import lock_timeout_error_fields

QUOTA_EVENT_KINDS = {
    "should-run": "quota_should_run",
    "monitor-poll": "quota_monitor_poll",
    "scheduler-ack": "quota_scheduler_ack",
    "scheduler-ack-current": "quota_scheduler_ack",
    "scheduler-fail-current": "quota_scheduler_failure",
    "spend-slot": "quota_spend",
    "void-slot": "quota_void",
}


def should_log_quota(command: str, payload: Mapping[str, object]) -> bool:
    return command in QUOTA_EVENT_KINDS and (
        command == "should-run"
        or (
            bool(payload.get("ok"))
            and (
                bool(payload.get("appended"))
                or bool(payload.get("receipt_repair_required"))
            )
        )
    )


def verbose_debug_fields(error: Exception, *, verbose: bool) -> dict[str, object]:
    if not verbose:
        return {}
    return {
        "verbose_debug": {
            "error_type": type(error).__name__,
            "error": str(error),
        }
    }


def quota_failure_payload(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    error: Exception,
) -> dict[str, object]:
    command = args.quota_command
    lock_timeout_fields = lock_timeout_error_fields(error)
    verbose_debug = verbose_debug_fields(
        error, verbose=bool(getattr(args, "verbose", False))
    )
    recommended_action = (
        error.recommended_action
        if isinstance(error, EffectRuntimeHostPermissionError)
        else "fix quota/status collection before spending automatic compute"
    )
    # Observation and execution callers consume the same authority rejection.
    # Preserve its typed recovery instead of losing it at the early read return.
    authority_failure = (
        {"error_code": error.code, "reason": str(error), **error.payload}
        if isinstance(error, (LegacyCoordinationWriterFenced, LocalCoordinationAuthorityUnavailable))
        else {}
    )
    if command not in QUOTA_EVENT_KINDS:
        return {
            "ok": False,
            "mode": command,
            "registry": str(registry_path),
            "runtime_root": runtime_root_arg,
            "error_code": quota_error_code(error),
            "error": "quota collection failed",
            "summary": {
                "registered_goals": 0,
                "health_blockers": 1,
                "next_automatic_turn": None,
                "states": {},
            },
            "groups": {},
            "health_items": [
                {
                    "goal_id": "loopx-quota",
                    "status": "quota_collection_failed",
                    "waiting_on": "codex",
                    "severity": "high",
                    "recommended_action": recommended_action,
                    "source": "quota",
                }
            ],
            **verbose_debug,
            **lock_timeout_fields,
            **authority_failure,
        }

    # A managed-runtime failure already carries a bounded, public-safe message
    # (which method could not be reached and how long it was given, or the typed
    # TS owner's own refusal), so publish it instead of a generic line the caller
    # cannot act on. Other exceptions may carry private paths: class name only.
    public_reason = (
        str(error)
        if isinstance(
            error,
            (CloseoutQueryUnavailableError, HeartbeatReceiptIdentityConflictError,
             EffectRuntimeStartupError, EffectRuntimeRemoteError),
        )
        else "quota collection failed"
    )
    payload: dict[str, object] = {
        "ok": False,
        "mode": command,
        "goal_id": args.goal_id,
        "decision": "skip",
        "should_run": False,
        "error_code": quota_error_code(error),
        "reason": public_reason,
        "state": "blocked_health",
        "waiting_on": "codex",
        "status": "quota_collection_failed",
        "source": "quota",
        "recommended_action": recommended_action,
        **verbose_debug,
        **lock_timeout_fields,
    }
    if payload["error_code"] == "quota_unexpected_collection_error":
        payload["error_class"] = type(error).__name__
        diagnostic_code = getattr(error, "diagnostic_code", None)
        if isinstance(error, EffectRuntimeRemoteError) and isinstance(diagnostic_code, str):
            payload["diagnostic_code"] = diagnostic_code
    if isinstance(error, HeartbeatReceiptIdentityConflictError):
        payload.update({
            "status": "heartbeat_receipt_identity_conflict",
            "effective_action": EffectiveAction.QUOTA_SKIP.value,
            "recommended_action": (
                "Rerun quota should-run with the same --turn-instance-id and without "
                "--todo-id or --replan-obligation-id to read the bound Turn's recovery "
                "contract. Settle verified work or record its verified blocked closeout; "
                "do not replace the committed binding, repair receipt storage, or invent "
                "a new Turn id. Independent work belongs to the next real host Turn."
            ),
        })
    if isinstance(error, CloseoutQueryUnavailableError):
        payload.update({
            "status": error.diagnostic_code,
            "effective_action": EffectiveAction.CONTROL_PLANE_HEALTH_REPAIR.value,
            "recommended_action": (
                "check runtime health, then retry quota should-run with the same "
                "Turn identity to read the closeout state; do not infer settlement "
                "or replay work from a missing query response"
            ),
        })
    if isinstance(error, QuotaActionSelectionConflictError):
        # The requested Todo could not be reconciled with the projection. Report
        # the real conflict and the next read to make, rather than the generic
        # "quota collection failed" and a pointer at receipt writeback.
        selection_conflict: dict[str, object] = {
            "kind": error.kind.value,
            "requested_todo_id": error.requested_todo_id,
            "selected_todo_id": error.selected_todo_id,
            "qualification_state": error.qualification_state,
        }
        if error.unsettled_prior_turn_instance_id:
            selection_conflict["unsettled_prior_turn_instance_id"] = (
                error.unsettled_prior_turn_instance_id
            )
        if error.unsettled_repair:
            selection_conflict["unsettled_repair"] = error.unsettled_repair
        if (
            error.admission_must_attempt is not None
            or error.admission_delivery_allowed is not None
        ):
            selection_conflict["admission"] = {
                "agent_must_attempt": error.admission_must_attempt,
                "delivery_allowed": error.admission_delivery_allowed,
            }
        if error.retained_selection:
            selection_conflict["retained_selection"] = True
            selection_conflict["retained_selection_todo_id"] = error.selected_todo_id
        if error.receipt_replan_obligation_id:
            selection_conflict["receipt_replan_obligation_id"] = (
                error.receipt_replan_obligation_id
            )
        payload.update(
            {
                "reason": str(error),
                "status": "quota_action_selection_conflict",
                "recommended_action": error.recommended_action,
                "action_selection_conflict": selection_conflict,
            }
        )
    if isinstance(error, QuotaIdentityPreconditionError):
        payload.update(
            {
                "reason": str(error),
                "status": "quota_identity_precondition_failed",
                "identity_precondition": error.precondition.value,
                "recommended_action": error.recommended_action,
            }
        )
        if error.agent_id is not None:
            payload["agent_id"] = error.agent_id
    elif isinstance(error, (LegacyCoordinationWriterFenced, LocalCoordinationAuthorityUnavailable)):
        payload.update(authority_failure)
    if lock_timeout_fields:
        payload["recommended_action"] = "inspect the lock holder before retrying"
    if command == "monitor-poll":
        payload.update(
            {
                "source": args.source,
                "agent_id": args.agent_id,
                "todo_id": args.todo_id,
                "target_key": args.target_key,
                "result_hash": args.result_hash,
                "material_change": bool(args.material_change),
            }
        )
    elif command in {"scheduler-ack", "scheduler-ack-current"}:
        payload.update(
            {
                "agent_id": args.agent_id,
                "surface": args.surface,
                "state_key": args.state_key,
                "applied_rrule": args.applied_rrule,
            }
        )
    elif command == "scheduler-fail-current":
        payload.update(
            {
                "agent_id": args.agent_id,
                "surface": args.surface,
                "state_key": args.state_key,
                "failed_rrule": args.failed_rrule,
                "failure_kind": args.failure_kind,
            }
        )
    return payload


def _fits_failure_output(payload: dict[str, object], limit: int) -> bool:
    # Count the compact CLI JSON and its trailing newline.
    return _failure_output_size(payload) <= limit


def captured_quota_failure_view(
    payload: Mapping[str, object], *, capture_directory: Path,
    max_page_output_bytes: int | None = None,
    capture_sha256: str | None = None,
    reader_argv: list[str] | None = None,
    runtime_root: Path | None = None,
    captured_payload: Mapping[str, object] | None = None,
    capture_bytes: bytes | None = None,
) -> dict[str, object]:
    """Bound a failure using the saved decision and its original receipt.

    A composition rejection saves its diagnostic. A later presentation failure
    retains the admitted decision and points to its receipt for recovery.
    """
    from ..control_plane.turn_driver.decision_capture import (
        context_capture_read_command, native_context_page_budget, write_initial_context_read_reference,
    )
    from .quota_context_reader import (
        inline_context_read_fits, inline_context_recovery_budget, pinned_context_read_window,
    )

    page_bytes, limit = native_context_page_budget()
    if max_page_output_bytes is not None:
        limit = min(limit, max_page_output_bytes)
    saved = capture_directory / "decision.json"
    original = saved.read_bytes() if capture_bytes is None else capture_bytes
    source = payload if captured_payload is None else captured_payload
    digest = capture_sha256 or hashlib.sha256(original).hexdigest()
    original_decision = source.get("ok") is True
    page_size = max(256, min(page_bytes, limit))
    fields = ("ok", "goal_id", "decision", "should_run", "error_code", "diagnostic_code", "error", "reason",
              "recommended_action", "heartbeat_receipt", "rollout_event", "host_poll_receipt", "action_selection_conflict")
    result = {key: payload[key] for key in fields if key in payload}
    interaction = payload.get("interaction_contract")
    cli = interaction.get("cli_channel") if isinstance(interaction, Mapping) else None
    if isinstance(cli, Mapping) and "next_cli_actions" in cli:
        result["next_cli_actions"] = cli["next_cli_actions"]
    reference = {"path": str(saved), "sha256": digest}
    result["captured_decision"] = {**reference,
        "command": context_capture_read_command(saved, digest,
            page_bytes=page_size, max_page_output_bytes=limit, reader_argv=reader_argv)}
    compact_instruction = "Read the original failure and Turn receipt before recovery. Do not rerun admission."
    if original_decision:
        compact_instruction = "Read the saved decision for the original Turn. Do not rerun admission."
        result["instruction"] = compact_instruction
    else:
        result["instruction"] = (
            "The guard refused this request. Follow its recovery using the original Turn identity. "
            "The saved decision is optional diagnostic detail; read only facts needed for recovery. "
            "This response does not establish a context baseline or grant execution authority."
        )

    if not _fits_failure_output(result, limit) or not inline_context_read_fits(
        saved, original, digest, page_bytes=page_size, output_limit=limit, reader_argv=reader_argv,
    ):
        # Pin the small diagnostic selection so long launcher/capture paths and
        # repeated --select arguments do not crowd out the recovery command.
        recovery_fields = (
            ("heartbeat_receipt", "rollout_event", "host_poll_receipt")
            if original_decision else (*fields, "composition_error")
        )
        paths = [[key] for key in recovery_fields if key in source]
        if not original_decision and isinstance(cli, Mapping) and "next_cli_actions" in cli:
            paths.append(["interaction_contract", "cli_channel", "next_cli_actions"])
        if not paths:
            paths = [["ok"]]
        pointers = ["/" + "/".join(path) for path in paths]
        selections = [{"path": path, "pointer": pointer} for path, pointer in zip(paths, pointers, strict=True)]
        try:
            reader, selected = write_initial_context_read_reference(saved, digest, dict(source), selections)
        except (OSError, ValueError):
            # Existing captures can remain readable when publishing reader metadata fails.
            reader, selected = None, None
        result = {"ok": False, "captured_decision": reference,
            **{key: payload[key] for key in ("error_code", "diagnostic_code") if key in payload},
            "instruction": compact_instruction,
            "command": context_capture_read_command(saved, digest, reference_file=reader,
                page_bytes=page_size, max_page_output_bytes=limit, reader_argv=reader_argv)}
        read_size, read_limit = page_size, limit
        if reader is not None and selected is not None:
            read_size, read_limit = pinned_context_read_window(reader, selected,
                page_bytes=page_size, output_limit=limit, reader_argv=reader_argv)
        if runtime_root is not None and (reader is None or not _fits_failure_output(result, limit) or read_limit > limit):
            from .context_machine import _private_transport_root

            try:
                transport = Path(_private_transport_root(runtime_root))
                if reader is None or len(str(transport)) < len(str(reader.parent)):
                    shorter, body = write_initial_context_read_reference(saved, digest, dict(source), selections,
                        reference_directory=transport)
                    read_size, read_limit = pinned_context_read_window(shorter, body,
                        page_bytes=page_size, output_limit=limit, reader_argv=reader_argv)
                    reader = shorter
            except (OSError, ValueError):
                pass  # Keep the original capture and any usable reference.
        if reader is None:
            # Explicit selectors require the same runtime that may have just
            # failed. The original file's digest is sufficient for this reader.
            read_limit = inline_context_recovery_budget(saved, original, digest,
                page_bytes=page_size, output_limit=limit, reader_argv=reader_argv)
        result["command"] = context_capture_read_command(saved, digest,
            reference_file=reader, page_bytes=read_size, max_page_output_bytes=read_limit,
            reader_argv=reader_argv)
        if read_limit > limit:
            result["recovery_page_output_bytes"] = read_limit
            result["instruction"] = "Do not rerun admission."
        if not _fits_failure_output(result, limit):
            # The command or pinned descriptor already carries the capture path.
            result["captured_decision"] = {"sha256": digest}
        if original_decision and "reason" in payload and _fits_failure_output({**result, "reason": payload["reason"]}, limit):
            result["reason"] = payload["reason"]
        if not _fits_failure_output(result, limit):
            # Even an impossibly small envelope must keep the original failure
            # and locator. Report why the requested output size cannot be met.
            result["instruction"] = "Output limit is too small for recovery metadata. Read this Turn. Do not rerun admission."
    return result


def quota_composition_failure_view(
    args: argparse.Namespace, committed: Mapping[str, object],
    error: EffectRuntimeRemoteError | EffectRuntimeStartupError, *,
    capture_directory: Path | None, present_capture_failure: bool,
    reader_argv: list[str], runtime_root: Path | None,
    record_capture: Callable[[dict[str, object], str], None],
) -> dict[str, object]:
    """Publish diagnostics after admission, independently of execution source."""
    failure = quota_decision_transport_failure_payload(args, committed, error, capture_directory=None)
    if capture_directory is None:
        return failure
    from .quota_capture import capture_decision

    diagnostic = {**failure, "source_diagnostic": committed,
        "composition_error": {"type": type(error).__name__, "message": str(error),
            "diagnostic_code": error.diagnostic_code}}
    digest = capture_decision(capture_directory, diagnostic, record_capture=record_capture)
    failure = quota_decision_transport_failure_payload(args, committed, error,
        capture_directory=capture_directory, capture_sha256=digest, reader_argv=reader_argv)
    if not present_capture_failure:
        return failure
    return captured_quota_failure_view(failure, capture_directory=capture_directory,
        capture_sha256=digest, max_page_output_bytes=getattr(args, "max_page_output_bytes", None),
        reader_argv=reader_argv, captured_payload=diagnostic, runtime_root=runtime_root)


def _compact_quota_failure(
    command: str | None = None, *, receipt: Mapping[str, object] | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {"ok": False, "error_code": QuotaDecisionTransportError.error_code,
        "instruction": "Do not rerun."}
    if command is not None:
        result["command"] = command
    if receipt is not None:
        result["heartbeat_receipt"] = receipt
    return result


def _failure_output_size(payload: dict[str, object]) -> int:
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()) + len(os.linesep)


@dataclass(frozen=True)
class _QuotaFailureReader:
    """One private, compact route reserved before a size-limited guard."""

    reference: Path
    command: str
    reader_argv: list[str]


def _quota_failure_reader_route(cli_bin: str | None) -> list[str]:
    if cli_bin is None:
        route = current_loopx_cli_argv()
        if os.path.isabs(route[0]):
            return route
        cli_bin = route[0]
    executable = resolve_command_path(cli_bin)
    if executable is None:
        raise QuotaCommandValidationError("Recovery CLI could not be resolved; no guard was run.")
    # A wrapper or symlink can select behavior using its invocation path.
    return loopx_cli_argv(str(executable.absolute()))


def _prepare_quota_failure_reader(limit: int, *, cli_bin: str | None) -> _QuotaFailureReader:
    from tempfile import mkdtemp

    from .quota_context_reader import validate_compact_recovery_budget
    from ..control_plane.turn_driver.decision_capture import (
        context_capture_read_command, private_capture_uid,
    )

    # Use a short same-user POSIX directory because caller-selected capture and
    # launcher paths can otherwise consume the entire reply.
    private_capture_uid()
    route = _quota_failure_reader_route(cli_bin)
    directory = Path(mkdtemp(prefix="lx-", dir="/tmp"))
    reference = directory / "r"
    # Keep the caller's explicit route for wrappers. When the running
    # release or console script already identifies itself, spell it once.
    if route[1:2] == ["--cli-bin"]:
        # A released scripts/loopx can be chosen explicitly by a different
        # caller. Its own release discovery follows symlinks to this entry.
        release_launcher = Path(__file__).resolve().parents[2] / "scripts" / "loopx"
        if (same_context_reader_route(current_loopx_cli_argv(preserve_override=False), route)
                or same_context_reader_route([str(release_launcher)], route)):
            route = route[:1]
    try:
        validate_compact_recovery_budget(reference, limit, route)
        command = context_capture_read_command(reference, "", reference_file=reference,
            reader_argv=route, compact=True, max_page_output_bytes=limit)
        if _failure_output_size(_compact_quota_failure(command)) > limit:
            raise ValueError("Output limit cannot fit a recovery command.")
    except ValueError as exc:
        directory.rmdir()
        raise QuotaCommandValidationError("Output limit cannot fit recovery metadata; no guard was run.") from exc
    return _QuotaFailureReader(reference, command, route)


def _quota_budget_failure_payload(reason: str, limit: int) -> dict[str, object]:
    payload: dict[str, object] = {"ok": False, "error_code": "QUOTA_VALIDATION_FAILED", "reason": reason}
    if _failure_output_size(payload) <= limit:
        return payload
    minimal: dict[str, object] = {"ok": False}
    if _failure_output_size(minimal) <= limit:
        return minimal
    return {}


def run_with_quota_failure_reader(
    args: argparse.Namespace, limit: int, *,
    print_payload: Callable[..., None], run: Callable[[], int],
) -> int:
    """Keep one recovery locator for the guard and any inline selection reentry."""
    cli_bin = getattr(args, "control_cli_bin", None)
    try:
        reader = _prepare_quota_failure_reader(limit, cli_bin=cli_bin)
    except (OSError, ValueError) as error:
        reason = (str(error) if isinstance(error, QuotaCommandValidationError)
            else "Cannot prepare a private recovery reference; no guard was run.")
        minimum_json_bytes = 2 + len(os.linesep)
        if limit >= minimum_json_bytes:
            print_payload(_quota_budget_failure_payload(reason, limit), "json", str)
        return 1
    args._quota_failure_reader = reader
    executable = reader.reader_argv[0]
    # Bind the captured Inbox, review and settlement commands before admission.
    # Recovering the receipt may happen after the caller's PATH changes.
    route = loopx_cli_route(executable) if cli_bin is not None and cli_bin != executable else nullcontext()
    try:
        with route:
            return run()
    finally:
        try:
            reader.reference.parent.rmdir()
        except OSError:
            pass  # Keep published diagnostics available for same-Turn reads.


def _bounded_quota_transport_failure(
    failure: dict[str, object], limit: int, reader: _QuotaFailureReader | None,
    path: Path | None, digest: str | None,
) -> dict[str, object]:
    from ..control_plane.turn_driver.decision_capture import write_context_read_reference, write_decision_capture

    if digest is None and _failure_output_size(failure) <= limit:
        return failure
    original_locator = {"path": str(path), "sha256": digest} if path is not None and digest is not None else None
    receipt = failure.get("heartbeat_receipt")
    command = None
    if reader is not None:
        try:
            if path is None or digest is None:
                # Preserve the committed receipt when decision.json could
                # not be published. Keep these bytes if the reference fails.
                path = reader.reference.with_name("failure.json")
                digest = write_decision_capture(path, failure)
            write_context_read_reference(
                path, digest, reference_file=reader.reference, reader_argv=reader.reader_argv,
            )
        except (OSError, ValueError):
            pass
        else:
            command = reader.command
    locator = None
    if path is not None and digest is not None:
        capture: dict[str, object] = {"path": str(path), "sha256": digest}
        if command is not None:
            capture["command"] = command
        else:
            # The direct reader can need more space than the opening reply.
            # Expose its saved identity without advertising unqualified argv.
            locator = {
                **_compact_quota_failure(),
                "captured_decision": capture,
                "instruction": "Use a larger output channel to read the saved capture by its path and SHA-256. "
                "Do not rerun admission.",
            }
            failure = {**failure, "instruction": locator["instruction"]}
        failure = {**failure, "captured_decision": capture}
    if _failure_output_size(failure) <= limit:
        return failure
    if locator is not None and _failure_output_size(locator) <= limit:
        return locator
    if command is not None or isinstance(receipt, Mapping):
        compact = _compact_quota_failure(command, receipt=receipt if command is None else None)
        guided = {**compact, "instruction": "Read the original Turn receipt before recovering. Do not rerun admission."}
        if command is not None and original_locator is not None:
            # Keep the independently checkable identity when the receipt and
            # full failure do not fit. A fallback diagnostic is not the original.
            for reference in (original_locator, {"sha256": original_locator["sha256"]}):
                located = {**guided, "captured_decision": reference}
                if _failure_output_size(located) <= limit:
                    return located
        if _failure_output_size(guided) <= limit:
            return guided
        if _failure_output_size(compact) <= limit:
            return compact
    result = _compact_quota_failure()
    if isinstance(receipt, Mapping):
        result["heartbeat_receipt"] = {key: receipt[key] for key in ("status", "event_id", "turn_instance_id") if key in receipt}
    result["reason"] = "Recovery reference could not be published. Inspect the original receipt. Do not rerun admission."
    if _failure_output_size(result) <= limit:
        return result
    result.pop("reason")
    return result if _failure_output_size(result) <= limit else _compact_quota_failure()


def quota_decision_transport_failure_payload(
    args: argparse.Namespace,
    committed: Mapping[str, object],
    error: Exception,
    *,
    capture_directory: Path | None,
    capture_sha256: str | None = None,
    reader_argv: list[str] | None = None,
) -> dict[str, object]:
    """Report a post-guard failure using the receipt and any actual capture."""
    saved = capture_directory / "decision.json" if capture_directory is not None else None
    composition_error = (
        error if isinstance(error, (EffectRuntimeRemoteError, EffectRuntimeStartupError)) else None
    )
    typed = composition_error is not None or isinstance(error, QuotaDecisionTransportError)
    if composition_error is not None and not isinstance(composition_error, EffectRuntimeRejected):
        message = "The local runtime could not compose the complete Turn context."
    elif typed:
        message = str(error)
    else:
        message = "The guard committed, but its result could not be presented."
    reason = " ".join(message.split())[:500]
    payload: dict[str, object] = {
        "ok": False,
        "mode": args.quota_command,
        "goal_id": args.goal_id,
        "decision": "skip",
        "should_run": False,
        "error_code": QuotaDecisionTransportError.error_code,
        "reason": reason,
        "state": "blocked_health",
        "waiting_on": "codex",
        "status": "decision_transport_failed",
        "source": "quota",
        "recommended_action": (
            "The guard already ran. Inspect this Turn's receipt and resolve the reported "
            "failure before recovery. Do not rerun admission."
        ),
    }
    if composition_error is not None:
        payload.update(error=reason, diagnostic_code=composition_error.diagnostic_code)
    if not typed:
        payload["error_class"] = type(error).__name__
        payload.update(verbose_debug_fields(error, verbose=bool(getattr(args, "verbose", False))))
    limit = getattr(args, "max_page_output_bytes", None) if args.format == "json" else None
    if saved is not None and capture_sha256 is not None:
        from ..control_plane.turn_driver.decision_capture import context_capture_read_command

        payload["captured_decision"] = {"path": str(saved), "sha256": capture_sha256,
            "command": context_capture_read_command(saved, capture_sha256, reader_argv=reader_argv,
                compact=limit is not None, max_page_output_bytes=limit)}
        payload["recommended_action"] = (
            "The guard already ran. Use quota context-read with the saved composition "
            "diagnostic path and SHA-256. Inspect this Turn's receipt before resolving "
            "the failure. Do not rerun admission."
            if composition_error is not None
            else "The guard already ran. Read the saved decision and this Turn's receipt "
            "instead of rerunning the guard. If the handoff endpoint belongs to an "
            "ended Host wake, use the transport named by the current wake, or none."
        )
    for key in ("heartbeat_receipt", "rollout_event", "host_poll_receipt"):
        if key in committed:
            payload[key] = committed[key]
    return payload


def quota_captured_transport_failure_view(
    args: argparse.Namespace, committed: dict[str, object], error: Exception, *,
    capture_directory: Path | None, capture_sha256: str | None,
    reader_argv: list[str], runtime_root: Path | None,
) -> dict[str, object]:
    """Report a delivery failure without replacing the previously committed capture."""
    reader = getattr(args, "_quota_failure_reader", None)
    if reader is not None:
        reader_argv = reader.reader_argv
    failure = quota_decision_transport_failure_payload(
        args, committed, error, capture_directory=capture_directory,
        capture_sha256=capture_sha256, reader_argv=reader_argv,
    )
    output_limit = getattr(args, "max_page_output_bytes", None)
    if output_limit is None or args.format != "json":
        return failure
    if capture_directory is None or capture_sha256 is None:
        return _bounded_quota_transport_failure(failure, output_limit, reader, None, None)

    from ..control_plane.turn_driver.decision_capture import read_context_capture_file

    saved = capture_directory / "decision.json"
    try:
        _, original, source = read_context_capture_file(str(saved), capture_sha256, private=True)
    except (OSError, ValueError):
        pass
    else:
        projected = captured_quota_failure_view(
            failure, capture_directory=capture_directory, max_page_output_bytes=output_limit,
            capture_sha256=capture_sha256, reader_argv=reader_argv,
            runtime_root=runtime_root, captured_payload=source, capture_bytes=original,
        )
        if _fits_failure_output(projected, output_limit) and "recovery_page_output_bytes" not in projected:
            return projected
        return _bounded_quota_transport_failure(failure, output_limit, reader, saved, capture_sha256)
    # The Host can change the file after guard publication. The expected digest
    # remains that of the original and there is no reader for the changed bytes.
    result = {"ok": False, "error_code": QuotaDecisionTransportError.error_code, "captured_decision": {
        "path": str(saved), "sha256": capture_sha256,
    }, "reason": "The saved decision cannot be verified against its original SHA-256.",
        "instruction": "Restore the original capture and inspect its Turn receipt. Do not rerun admission."}
    if not _fits_failure_output(result, output_limit):
        result.pop("reason")
        result["instruction"] = "Restore file. Read receipt. Do not rerun admission."
        if not _fits_failure_output(result, output_limit):
            # The reserved reader can carry the original locator and receipt in
            # private diagnostics. It must never read the unverified capture.
            diagnostic = {**result, **{key: failure[key] for key in
                ("heartbeat_receipt", "rollout_event", "host_poll_receipt") if key in failure}}
            return _bounded_quota_transport_failure(diagnostic, output_limit, reader, None, None)
    for key in ("heartbeat_receipt", "rollout_event", "host_poll_receipt"):
        if key in failure:
            candidate = {**result, key: failure[key]}
            if _fits_failure_output(candidate, output_limit):
                result = candidate
    return result


def quota_validation_failure_payload(
    args: argparse.Namespace,
    exc: QuotaCommandValidationError,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
) -> dict[str, object]:
    command = args.quota_command
    if command not in QUOTA_EVENT_KINDS:
        return {
            "ok": False,
            "mode": command,
            "registry": str(registry_path),
            "runtime_root": runtime_root_arg,
            "error_code": "QUOTA_VALIDATION_FAILED",
            "error": str(exc),
            "summary": {
                "registered_goals": 0,
                "health_blockers": 0,
                "next_automatic_turn": None,
                "states": {},
            },
            "groups": {},
            "health_items": [],
        }
    return {
        "ok": False,
        "mode": command,
        "goal_id": args.goal_id,
        "decision": "skip",
        "should_run": False,
        "error_code": "QUOTA_VALIDATION_FAILED",
        "reason": str(exc),
        "state": "blocked_validation",
        "waiting_on": "codex",
        "status": "quota_validation_failed",
        "source": "quota",
        "recommended_action": "fix the command arguments before retrying",
    }




__all__ = [
    "QUOTA_EVENT_KINDS",
    "quota_decision_transport_failure_payload",
    "quota_failure_payload",
    "quota_validation_failure_payload",
    "should_log_quota",
    "verbose_debug_fields",
]
