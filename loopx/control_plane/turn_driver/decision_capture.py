"""Private original-capture and presentation IO; TS owns binding validation."""
from __future__ import annotations

import hashlib
import json
import shlex
import os
import socket
import stat
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..quota.turn_envelope import bind_turn_decision_capture
from ..quota.error_codes import QuotaCommandValidationError


def write_decision_capture(
    path: Path, payload: Mapping[str, Any], *, publish=os.link,
) -> None:
    """Publish complete private JSON once; never replace an earlier observation."""
    descriptor, temporary = tempfile.mkstemp(prefix=".decision-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(dict(payload), stream, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        publish(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def prepare_turn_decision_capture(
    plan: Mapping[str, Any], *, journal_path: Path,
    decision: Mapping[str, Any] | None, existing_journal: bool,
) -> dict[str, Any]:
    """Called under the Turn journal lock before Host/admission effects.

    Existing journals must retain their original reference. A missing legacy
    observation cannot be reconstructed from today's guard. Already committed
    journals remain readable through ordinary no-effect replay.
    """
    envelope = plan["turn_envelope"]
    transaction = plan["transaction"]
    path = journal_path.parent / "decisions" / journal_path.name
    capture = envelope.get("detail_ref", {}).get("captured_decision")
    if existing_journal and not capture:
        raise ValueError("This Turn journal has no original decision capture; preserve it and "
                         "recover its pending effects before starting a fresh Turn")
    if path.is_symlink():
        raise ValueError("Turn decision capture must not be a symlink")
    if path.exists():
        observation = json.loads(path.read_text(encoding="utf-8"))
    elif existing_journal or capture:
        raise ValueError("Original Turn decision capture is missing; restore the original "
                         "observation, do not rerun the guard to replace it")
    else:
        if decision is None:
            raise ValueError("New Turn requires its full original decision")
        observation = dict(decision)
    from ...command_invocation import current_loopx_cli_argv

    bound = bind_turn_decision_capture(
        observation, envelope, captured_decision_path=str(path),
        turn_instance_id=str(transaction.get("turn_instance_id") or transaction["turn_key"]),
        turn_key=transaction["turn_key"],
        # New journals have a real immutable source and executable reader.
        # Legacy journals retain their recorded delivery/recovery route.
        reader_argv=current_loopx_cli_argv() if not existing_journal else None,
    )
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        write_decision_capture(path, observation)
    return {**plan, "turn_envelope": bound}


def validate_handoff_socket(value: str) -> Path:
    """A caller-owned local transport, not an authority or admission endpoint."""
    path = Path(value)
    try:
        if (os.name != "posix" or not hasattr(socket, "AF_UNIX") or not path.is_absolute() or
                path.resolve(strict=True) != path or
                not stat.S_ISSOCK(path.stat().st_mode) or
                path.parent.stat().st_mode & 0o077 or
                path.stat().st_uid != os.getuid()):
            raise OSError("expected a same-user socket in a private physical directory")
    except FileNotFoundError as exc:
        # A resumed session can remember an earlier wake's private endpoint.
        # Reject before any guard runs and name the current-wake remedy.
        raise QuotaCommandValidationError(
            "invalid decision handoff socket: the endpoint no longer exists; it belonged "
            "to a Host wake that has ended. No guard was run. Rerun with the "
            "--decision-output-root and --decision-handoff-socket named by the current "
            "wake, or without both when the current wake names none"
        ) from exc
    except OSError as exc:
        raise QuotaCommandValidationError(f"invalid decision handoff socket: {exc}") from exc
    return path


CONTEXT_CAPTURE_READ_GUIDANCE = (
    "Follow next_command through eof and combine content in byte-offset order. "
    "In code-mode, forward successful exec stdout directly with text(result.output); "
    "retain errors. The reader verifies the original capture SHA-256; keep its returned digest and byte cursors. "
    "Do not hash or retype the packet. Native input observation independently verifies complete model-visible "
    "bytes before delta reuse; neither a retyped hash nor a disk hash proves that input. "
    "Small batches can save model round trips; start with at most two default pages "
    "and inspect every output. A host may truncate inside valid JSON or omit a whole output. "
    "Consume all model-visible content in UTF-8 byte-offset order with complete coverage through total_bytes. "
    "If a range or output is missing, truncated or uncertain, reread the affected range "
    "of this same capture with fewer pages or a smaller --page-bytes; do not trust a preview or EOF alone. "
    "Do not rerun admission or ask the owner. File existence and page EOF are not retention proof."
)


def context_capture_read_command(path: Path, digest: str, *, offset: int = 0, page_bytes: int = 4096) -> str:
    from ...command_invocation import loopx_cli_argv

    return shlex.join([*loopx_cli_argv(), "--format", "json", "quota", "context-read",
        "--capture-file", str(path), "--capture-sha256", digest,
        "--offset", str(offset), "--page-bytes", str(page_bytes)])


def capture_context_projection(payload: dict[str, Any], *, directory: Path) -> dict[str, Any]:
    """Save the full projection independently of the optional presentation codec."""
    projection = directory / "projection.json"
    write_decision_capture(projection, payload)
    digest = hashlib.sha256(projection.read_bytes()).hexdigest()
    return {
        "path": str(projection), "sha256": digest,
        "instruction": "Complete JSON for this original capture. If the baseline is missing, compacted, "
            "truncated or uncertain, use this full projection before dependent work. "
            + CONTEXT_CAPTURE_READ_GUIDANCE,
        "command": context_capture_read_command(projection, digest),
    }


def project_captured_context(
    payload: dict[str, Any], *, directory: Path | None, request: dict[str, Any] | None,
    goal_id: str, agent_id: str, surface: str, capture_id: str,
    captured_recovery: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Adapt an admitted capture to the shared TS presentation owner.

    The caller-owned rendezvous may supply a continuity observation. It must
    still recheck the candidate at final model-input assembly and ACK only intact
    delivery. Unsupported ordinary CLI clients never supply this request. Every
    opt-in attempt saves the complete projected JSON for same-capture recovery;
    neither malformed requests nor optimization failures rerun the guard.
    A native caller may pass its already-published recovery reference, never a
    reference supplied in the external request.
    """
    if request is None or directory is None:
        return payload
    from ..effect_runtime import EffectRuntimeRemoteError, EffectRuntimeStartupError
    from ..quota.turn_envelope import prepare_context_delivery

    try:
        recovery = captured_recovery if captured_recovery is not None else capture_context_projection(
            payload, directory=directory)
        scope = request.get("scope")
        if not isinstance(scope, dict) or scope.get("goal_id") != goal_id or scope.get("agent_id") != agent_id:
            return payload
        retained = request.get("retained")
        if "native_input" in request:
            from .codex_input_trace import codex_context_in_active_input

            # An optional native observation can only narrow the caller's
            # retention claim. It never mints an ACK or enables tracing, and a
            # malformed/stale observation must not fall back to that claim.
            observation, baseline = request["native_input"], request.get("baseline")
            native_session = scope.get("session_id")
            if (not isinstance(observation, dict) or not isinstance(baseline, dict)
                    or not isinstance(native_session, str)
                    or not codex_context_in_active_input(observation, baseline=baseline,
                        session_id=native_session)):
                retained = None
        delivery = prepare_context_delivery(payload, scope=scope,
            capture_id=capture_id, surface=surface,
            baseline=request.get("baseline"), retained=retained)
        delivery["recovery"] = recovery
        return delivery
    except (OSError, ValueError, TypeError, EffectRuntimeRemoteError, EffectRuntimeStartupError):
        # The original full payload is already available. An unavailable optional
        # codec/capture must not convert safe full delivery into an execution gate.
        return payload
