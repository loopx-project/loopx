from __future__ import annotations

import os
from typing import Any

# The fixture presents the 0.11.1 release surface by default. Two other shapes
# exist so tests can prove the helper refuses them at admission instead of
# downgrading silently: "0.11.0" (the previous release: no publication fence,
# no typed refusal) and "0.11.1-unfenced" (a wheel labelled 0.11.1 that lacks
# the fence surface).
_SHAPE = os.environ.get("LOOPX_FAKE_NOKV_SDK_SHAPE", "0.11.1")
if _SHAPE not in {"0.11.1", "0.11.0", "0.11.1-unfenced"}:
    raise RuntimeError(f"unknown fake NoKV SDK shape {_SHAPE!r}")
_FENCED = _SHAPE == "0.11.1"
__version__ = "0.11.0" if _SHAPE == "0.11.0" else "0.11.1"
API_VERSION = 1
_CURRENT_INCARNATION = "a" * 32

# Keep a missing schema distinct from an SDK-declared schema in the handshake.
# This fixture metadata does not qualify an actual distribution or backend.
_schema = os.environ.get("LOOPX_FAKE_NOKV_PROTOCOL_SCHEMA")
if _schema:
    WORKSPACE_PROTOCOL_SCHEMA = _schema


class RoutingConfig:
    # Independently omit the selected constructor to exercise a routing
    # capability mismatch without removing the current publication fence.
    if os.environ.get("LOOPX_FAKE_NOKV_RELEASE_SHAPE") != "1":

        @staticmethod
        def seeds(endpoints: list[str]) -> object:
            return ("seeds", endpoints)

    @staticmethod
    def etcd(endpoints: list[str], key_prefix: str, lease_ttl_seconds: int) -> object:
        return ("etcd", endpoints, key_prefix, lease_ttl_seconds)

    @staticmethod
    def static(*values: Any) -> object:
        return ("static", values)


class ObjectStoreConfig:
    @staticmethod
    def memory() -> object:
        return ("memory",)

    @staticmethod
    def s3(**values: Any) -> object:
        return ("s3", values)


if _FENCED:

    class WorkspaceIncarnationMismatch(RuntimeError):
        """Mirror of the 0.11.1 SDK refusal: the fence did not match, nothing was written."""

        def __init__(self, message: str, expected: str) -> None:
            super().__init__(message)
            self.expected = expected


class Client:
    def __init__(self, **_values: Any) -> None:
        self._objects: dict[tuple[str, str], tuple[bytes, int]] = {}

    def find_workspaces(self, **_values: Any) -> dict[str, Any]:
        return {
            "workspaces": [
                {
                    "workspace": {
                        "workbench": "authority-workbench",
                        "workspace_incarnation_id": _CURRENT_INCARNATION,
                    }
                }
            ],
            "next_cursor": None,
        }

    def read(self, workbench: str, path: str) -> dict[str, Any]:
        entry = self._objects.get((workbench, path))
        if entry is None:
            raise FileNotFoundError("missing")
        raw, generation = entry
        return {
            "bytes": raw,
            "metadata": {
                "workbench": workbench,
                "path": path,
                "workspace_incarnation_id": _CURRENT_INCARNATION,
                "generation": generation,
            },
        }

    def publish_bytes(
        self,
        workbench: str,
        path: str,
        payload: bytes,
        *,
        expected_workspace_incarnation_id: str | None = None,
        **values: Any,
    ) -> dict[str, Any]:
        # The real owner evaluates the fence before claiming the path, so a
        # stale fence leaves the stored generation untouched.
        if (
            expected_workspace_incarnation_id is not None
            and expected_workspace_incarnation_id != _CURRENT_INCARNATION
        ):
            raise WorkspaceIncarnationMismatch(
                "workbench incarnation mismatch", expected_workspace_incarnation_id
            )
        return self._publish(workbench, path, payload, values)

    def _publish(
        self, workbench: str, path: str, payload: bytes, values: dict[str, Any]
    ) -> dict[str, Any]:
        key = (workbench, path)
        existing = self._objects.get(key)
        current_generation = existing[1] if existing is not None else None
        expected = values["expected_generation"]
        if expected is None and current_generation is not None:
            raise FileExistsError("already exists")
        if expected is not None and expected != current_generation:
            raise RuntimeError("generation conflict")
        next_generation = (current_generation or 0) + 1
        self._objects[key] = (payload, next_generation)
        return {
            "operation_id": values["operation_id"],
            "artifact_revision_id": values["artifact_revision_id"],
            "workbench": workbench,
            "path": path,
            "generation": next_generation,
        }


if not _FENCED:

    def _publish_bytes_without_fence(
        self: Client, workbench: str, path: str, payload: bytes, **values: Any
    ) -> dict[str, Any]:
        return self._publish(workbench, path, payload, values)

    Client.publish_bytes = _publish_bytes_without_fence  # type: ignore[method-assign]
