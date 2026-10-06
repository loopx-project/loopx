"""Opt-in local transport capture; quota and envelope owners remain unchanged."""
from __future__ import annotations

import argparse
import json
import os
import shlex
import tempfile
from pathlib import Path

from ..control_plane.quota.error_codes import QuotaCommandValidationError


def prepare_decision_capture(args: argparse.Namespace) -> Path | None:
    requested = getattr(args, "decision_output_dir", None)
    root = getattr(args, "decision_output_root", None)
    if requested is None and root is None:
        return None
    if requested is not None and root is not None:
        raise QuotaCommandValidationError("choose one decision capture destination")
    if args.quota_command != "should-run" or not getattr(args, "turn_instance_id", None):
        raise QuotaCommandValidationError(
            "decision capture requires quota should-run and --turn-instance-id"
        )
    try:
        if root is not None:
            parent = Path(root).expanduser().absolute()
            if parent.is_symlink() or not parent.is_dir():
                raise OSError("capture root must be an existing directory, not a symlink")
            return Path(tempfile.mkdtemp(prefix="decision-", dir=parent))
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


def bind_capture_selection_transport(payload: dict, args: argparse.Namespace) -> None:
    """Retain caller display/capture options in the existing selection command.

    Only transport argv is added here; the typed quota owner still selects the
    action and owns admission. Each selection invocation allocates a new capture.
    """
    root = getattr(args, "decision_output_root", None)
    if root is None:
        return
    interaction = payload.get("interaction_contract")
    cli = interaction.get("cli_channel") if isinstance(interaction, dict) else None
    selection = cli.get("selection_command") if isinstance(cli, dict) else None
    if not isinstance(selection, dict):
        return
    template = selection.get("command_args_template")
    if not isinstance(template, str):
        return
    suffix = " --decision-output-root " + shlex.quote(str(Path(root).expanduser().absolute()))
    if getattr(args, "turn_envelope", False):
        suffix += " --turn-envelope"
    selection["command_args_template"] = template + suffix
