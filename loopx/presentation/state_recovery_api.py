"""Loopback-only backup upload and immutable inert-audit readback."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
from uuid import uuid4

from ..control_plane.state_recovery_io import (
    MAX_ARCHIVE_BYTES, MAX_MANIFEST_BYTES, StateRecoveryError,
    read_state_recovery_audit, verify_state_backup,
)
from ..status_server import is_loopback_host

STATE_RECOVERY_PATH = "/api/chat/state-recovery"
_UPLOAD_TTL_SECONDS = 3600
_REF = re.compile(r"^(archive|manifest):([a-f0-9]{32})$")


class StateRecoveryRequestMixin:
    def _recovery_root(self, *, create: bool = False) -> Path:
        if os.name != "posix":
            raise StateRecoveryError("recovery_platform_unqualified")
        if not is_loopback_host(str(self.server.server_address[0])):
            raise StateRecoveryError("recovery_requires_loopback_server")
        root = self.server.runtime_root / "backups" / "state-recovery"
        if root.absolute() != root.resolve():
            raise StateRecoveryError("recovery_storage_not_physical")
        if create:
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
            (root / "inputs").mkdir(exist_ok=True, mode=0o700)
            (root / "checkpoints").mkdir(exist_ok=True, mode=0o700)
        for directory in (root, root / "inputs", root / "checkpoints"):
            if directory.exists() or directory.is_symlink():
                info = directory.lstat()
                if directory.is_symlink() or not directory.is_dir() or info.st_uid != os.getuid() or info.st_mode & 0o077:
                    raise StateRecoveryError("recovery_storage_not_private")
        return root

    def _state_recovery_profile(self) -> None:
        supported = os.name == "posix" and is_loopback_host(str(self.server.server_address[0]))
        self._send_json({"ok": True, "supported": supported, "activation_available": False,
                         "maximum_archive_bytes": MAX_ARCHIVE_BYTES, "maximum_manifest_bytes": MAX_MANIFEST_BYTES})

    def _recovery_failure(self, error: Exception) -> None:
        code = error.code if isinstance(error, StateRecoveryError) else "recovery_unavailable"
        self._send_error("Backup inspection did not finish. Check the archive, manifest and available disk space.",
                         status=400 if isinstance(error, ValueError) else 503, error_code=code)

    def _state_recovery_upload(self, kind: str) -> None:
        temporary: Path | None = None
        previous_timeout = self.connection.gettimeout()
        try:
            if os.name != "posix":
                raise StateRecoveryError("recovery_platform_unqualified")
            maximum = MAX_ARCHIVE_BYTES if kind == "archive" else MAX_MANIFEST_BYTES
            lengths = self.headers.get_all("Content-Length") or []
            if len(lengths) != 1 or not lengths[0].isdigit() or self.headers.get("Transfer-Encoding"):
                raise StateRecoveryError("recovery_upload_length_invalid")
            length = int(lengths[0])
            if not 0 < length <= maximum:
                raise StateRecoveryError("recovery_capacity_exceeded")
            root = self._recovery_root(create=True)
            if shutil.disk_usage(root).free < length + MAX_MANIFEST_BYTES:
                raise StateRecoveryError("recovery_disk_capacity")
            inputs = root / "inputs"
            # These are disposable upload inputs, not backups or audit receipts.
            for old in inputs.iterdir():
                if re.fullmatch(r"(?:archive|manifest)-[a-f0-9]{32}", old.name) and not old.is_symlink():
                    if old.stat().st_mtime < time.time() - _UPLOAD_TTL_SECONDS:
                        old.unlink()
            fd, name = tempfile.mkstemp(prefix=".upload-", dir=inputs)
            temporary = Path(name)
            digest, remaining = hashlib.sha256(), length
            self.connection.settimeout(30)
            with os.fdopen(fd, "wb") as output:
                while remaining:
                    chunk = self.rfile.read(min(256 * 1024, remaining))
                    if not chunk:
                        raise StateRecoveryError("recovery_upload_incomplete")
                    output.write(chunk)
                    digest.update(chunk)
                    remaining -= len(chunk)
                output.flush()
                os.fsync(output.fileno())
            identifier = uuid4().hex
            destination = inputs / f"{kind}-{identifier}"
            os.link(temporary, destination)
            self._send_json({"ok": True, "input_ref": f"{kind}:{identifier}",
                             "sha256": digest.hexdigest(), "size_bytes": length,
                             **({"checkpoint_ref": identifier} if kind == "archive" else {})})
        except (ValueError, OSError, RuntimeError) as error:
            self.close_connection = True
            self._recovery_failure(error)
        finally:
            self.connection.settimeout(previous_timeout)
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _recovery_input(self, ref: object, kind: str) -> tuple[Path, str]:
        match = _REF.fullmatch(ref) if isinstance(ref, str) else None
        if match is None or match.group(1) != kind:
            raise StateRecoveryError("recovery_input_ref_invalid")
        root = self._recovery_root()
        return root / "inputs" / f"{kind}-{match.group(2)}", match.group(2)

    def _state_recovery_verify(self) -> None:
        try:
            body = self._read_json()
            if set(body) != {"archive_ref", "manifest_ref", "execute"} or type(body["execute"]) is not bool:
                raise StateRecoveryError("recovery_request_invalid")
            archive, identifier = self._recovery_input(body["archive_ref"], "archive")
            manifest, _ = self._recovery_input(body["manifest_ref"], "manifest")
            destination = self._recovery_root() / "checkpoints" / identifier
            audit = verify_state_backup(archive=archive, manifest=manifest, destination=destination,
                                        execute=body["execute"])
            self._send_json({**audit, "checkpoint_ref": identifier})
        except (ValueError, OSError, RuntimeError) as error:
            self._recovery_failure(error)

    def _state_recovery_read(self) -> None:
        try:
            body = self._read_json()
            ref = body.get("checkpoint_ref")
            if set(body) != {"checkpoint_ref"} or not isinstance(ref, str) or not re.fullmatch(r"[a-f0-9]{32}", ref):
                raise StateRecoveryError("recovery_checkpoint_ref_invalid")
            audit = read_state_recovery_audit(self._recovery_root() / "checkpoints" / ref)
            self._send_json({**audit, "checkpoint_ref": ref, "historical_readback": True})
        except (ValueError, OSError, RuntimeError) as error:
            self._recovery_failure(error)

    def _state_recovery_list(self) -> None:
        try:
            parent = self._recovery_root() / "checkpoints"
            candidates = [] if not parent.exists() else [path for path in parent.iterdir()
                if re.fullmatch(r"[a-f0-9]{32}", path.name) and path.is_dir() and not path.is_symlink()]
            candidates.sort(key=lambda path: path.stat().st_mtime_ns, reverse=True)
            items = []
            for path in candidates[:10]:
                try:
                    audit = read_state_recovery_audit(path)
                    items.append({"checkpoint_ref": path.name, "available": True,
                                  "observed_at": audit["observed_at"], "summary": audit["summary"]})
                except (ValueError, OSError, RuntimeError):
                    items.append({"checkpoint_ref": path.name, "available": False})
            self._send_json({"ok": True, "items": items, "has_more": len(candidates) > 10})
        except (ValueError, OSError, RuntimeError) as error:
            self._recovery_failure(error)
