"""Private observation IO for the real Turn host; TS owns binding validation."""
from __future__ import annotations

import json
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
    bound = bind_turn_decision_capture(
        observation, envelope, captured_decision_path=str(path),
        turn_instance_id=str(transaction.get("turn_instance_id") or transaction["turn_key"]),
        turn_key=transaction["turn_key"],
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
