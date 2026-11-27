"""Owner-local configuration download and isolated recovery; never activation."""
from typing import TYPE_CHECKING, Any, Protocol, cast

from ..capabilities.configuration_backup import (
    capture_configuration_backup,
    compare_configuration_backups,
    restore_configuration_backup,
    verify_configuration_backup,
)
from ..control_plane.effect_runtime import MAX_LOCAL_SNAPSHOT_BYTES

CONFIGURATION_BACKUP_PATH = "/api/chat/configuration-backup"


class _ComparisonHTTP(Protocol):
    """Operations supplied by the composed HTTP handler, not mixin overrides."""

    def _read_json(self, *, max_bytes: int) -> dict[str, Any]: ...

    def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None: ...

    def _send_error(self, message: str, *, status: int, error_code: str) -> None: ...


if TYPE_CHECKING:
    from ..chat_server import ChatRequestHandler

    _comparison_http_contract: type[_ComparisonHTTP] = ChatRequestHandler


class ConfigurationBackupRequestMixin:
    def _configuration_backup_export(self):
        try:
            body = self._read_json()
            if set(body) - {"goal_ids"}:
                raise ValueError("configuration backup export contains unknown fields")
            ids = body.get("goal_ids")
            if ids is not None and (not isinstance(ids, list) or any(not isinstance(value, str) or not value.strip() for value in ids)):
                raise ValueError("goal_ids must be a list of nonempty ids")
            backup = capture_configuration_backup(registry_path=self.server.registry_path,
                runtime_root=self.server.runtime_root, goal_ids=ids)
            self._send_json({"ok": True, "status": "exported", "backup": backup, **verify_configuration_backup(backup)})
        except Exception:
            self._send_error("Configuration backup could not be captured; inspect the source configuration.",
                status=400, error_code="configuration_backup_capture_failed")

    def _configuration_backup_compare(self) -> None:
        http = cast(_ComparisonHTTP, self)
        try:
            body = http._read_json(max_bytes=MAX_LOCAL_SNAPSHOT_BYTES)
            if set(body) != {"before", "after"}:
                raise ValueError("configuration comparison requires exactly two snapshots")
            http._send_json(compare_configuration_backups(body["before"], body["after"]))
        except Exception:
            http._send_error("Configuration snapshots could not be compared. Check both files, their digests and the comparison size limit. No settings were changed.",
                status=400, error_code="configuration_backup_compare_failed")

    def _configuration_backup_restore(self):
        try:
            # A configuration checkpoint uses the existing local-snapshot byte
            # budget. Ordinary chat/configuration request limits stay unchanged.
            body = self._read_json(max_bytes=MAX_LOCAL_SNAPSHOT_BYTES)
            if set(body) != {"backup", "expected_sha256", "execute"} or not isinstance(body["execute"], bool):
                raise ValueError("configuration backup restore request is invalid")
            backup = body["backup"]
            verified = verify_configuration_backup(backup)
            if body["expected_sha256"] != verified["sha256"]:
                raise ValueError("reviewed configuration digest changed")
            # The browser cannot choose an arbitrary filesystem destination.
            parent = self.server.runtime_root / "backups" / "configuration"
            if parent.resolve() != parent.absolute():
                raise ValueError("configuration checkpoint parent cannot follow a symlink")
            if body["execute"]:
                parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            # A stable id makes a duplicate click an occupied-target conflict.
            destination = parent / str(verified["sha256"])
            if not body["execute"]:
                self._send_json({**verified, "status": "preview", "written": False})
                return
            receipt = restore_configuration_backup(backup, destination=destination,
                expected_sha256=body["expected_sha256"], execute=True)
            self._send_json({**receipt, "checkpoint_ref": f"backups/configuration/{verified['sha256']}"})
        except Exception:
            self._send_error("Configuration checkpoint could not be restored. Check its digest and whether this checkpoint already exists. Live settings were not changed.",
                status=400, error_code="configuration_backup_restore_failed")
