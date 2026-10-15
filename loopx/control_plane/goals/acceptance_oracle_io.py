"""Host filesystem/process IO for the TypeScript acceptance oracle owner.

No configuration, evidence classification or acceptance decision is owned here.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


def oracle_file_versions(
    workspace: Path | None, refs: list[str]
) -> list[dict[str, str]] | None:
    if workspace is None:
        return None
    versions = []
    try:
        for ref in sorted(refs):
            path = workspace
            for part in Path(ref).parts:
                path = path / part
                if path.is_symlink():
                    return None
            if not path.is_file():
                return None
            digest = hashlib.sha256()
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            versions.append({"ref": ref, "sha256": digest.hexdigest()})
    except OSError:
        return None
    return versions


def oracle_versions(
    workspace: Path | None, oracle: dict[str, Any]
) -> dict[str, Any] | None:
    inputs = oracle_file_versions(workspace, oracle["input_refs"])
    outputs = oracle_file_versions(workspace, oracle["output_refs"])
    if inputs is None or outputs is None:
        return None
    return {"input_versions": inputs, "output_versions": outputs}


def run_snapshot_oracle(
    *, workspace: Path, criterion: dict[str, Any], before: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    """Run the approved checker against copies of precisely the declared files.

    CWD isolation freezes the bytes seen through the declared relative refs; it
    is not an OS sandbox or an attestation of imports/environment/network access.
    """
    import shutil
    import subprocess
    from tempfile import TemporaryDirectory
    from ..runtime.validation_command import run_caller_validation

    oracle = criterion["oracle"]
    pins = criterion["validation_files"]
    with TemporaryDirectory(prefix="loopx-acceptance-oracle-") as directory:
        snapshot = Path(directory)
        for ref in {
            *(pin["path"] for pin in pins),
            *oracle["input_refs"],
            *oracle["output_refs"],
        }:
            target = snapshot / ref
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(workspace / ref, target)

        def pins_current() -> bool:
            versions = oracle_file_versions(snapshot, [pin["path"] for pin in pins])
            return versions is not None and {
                row["ref"]: row["sha256"] for row in versions
            } == {pin["path"]: pin["sha256"] for pin in pins}

        stable = pins_current() and oracle_versions(snapshot, oracle) == before
        if not stable:
            return {}, False
        try:
            receipt = run_caller_validation(
                snapshot,
                validation_argv=criterion["validation_argv"],
                validation_label=criterion["validation_label"],
                timeout_seconds=criterion["validation_timeout_seconds"],
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError) as exc:
            receipt = {
                "schema_version": "issue_fix_validation_command_v0",
                "command_label": criterion["validation_label"],
                "passed": False,
                "exit_code": None,
                "status": "timeout"
                if isinstance(exc, subprocess.TimeoutExpired)
                else "command_not_run",
                "stdout_captured": False,
                "stderr_captured": False,
                "local_path_captured": False,
            }
        return dict(receipt), pins_current() and oracle_versions(
            snapshot, oracle
        ) == before
