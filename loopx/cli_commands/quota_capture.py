"""Opt-in local transport capture; quota and envelope owners remain unchanged."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from ..control_plane.quota.error_codes import QuotaCommandValidationError


def prepare_decision_capture(args: argparse.Namespace) -> Path | None:
    requested = getattr(args, "decision_output_dir", None)
    if requested is None:
        return None
    if args.quota_command != "should-run" or not getattr(args, "turn_instance_id", None):
        raise QuotaCommandValidationError(
            "--decision-output-dir requires quota should-run and --turn-instance-id"
        )
    directory = Path(requested).expanduser().absolute()
    try:
        # A new directory per invocation prevents overwriting a prior observation,
        # including a prior invocation in the same Turn. Existing symlinks fail too.
        directory.mkdir(mode=0o700)
    except OSError as exc:
        raise QuotaCommandValidationError(
            "--decision-output-dir must be a new writable directory with an existing "
            f"parent; no guard was run: {exc}"
        ) from exc
    return directory


def capture_decision(directory: Path | None, payload: dict[str, object]) -> None:
    if directory is None:
        return
    temporary = directory / ".decision.json.tmp"
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, directory / "decision.json")
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            "Decision capture failed after guard evaluation; the Turn receipt may "
            "already exist. Do not treat this as missing admission or blindly rerun "
            f"the guard. Inspect the existing Turn receipt. Capture directory: {directory}"
        ) from exc
    finally:
        temporary.unlink(missing_ok=True)
