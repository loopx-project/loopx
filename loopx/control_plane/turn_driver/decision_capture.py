"""Private observation IO for the real Turn host; TS owns binding validation."""
from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..quota.turn_envelope import bind_turn_decision_capture


def write_decision_capture(
    path: Path, payload: Mapping[str, Any], *, publish=os.link,
) -> None:
    """Publish complete private JSON once; never replace an earlier observation."""
    descriptor, temporary = tempfile.mkstemp(prefix=".decision-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(dict(payload), stream, ensure_ascii=False, indent=2, allow_nan=False)
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
