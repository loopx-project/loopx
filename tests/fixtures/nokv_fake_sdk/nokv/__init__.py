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


class RoutingConfig:
    # Union of the two real wheels the helper is qualified against: the 0.11.x
    # releases provide etcd/static, the metadata-runtimes line provides seeds.
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
        self._bytes: bytes | None = None
        self._generation: int | None = None

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
        if self._bytes is None or self._generation is None:
            raise FileNotFoundError("missing")
        return {
            "bytes": self._bytes,
            "metadata": {
                "workbench": workbench,
                "path": path,
                "workspace_incarnation_id": _CURRENT_INCARNATION,
                "generation": self._generation,
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
        expected = values["expected_generation"]
        if expected is None and self._generation is not None:
            raise FileExistsError("already exists")
        if expected is not None and expected != self._generation:
            raise RuntimeError("generation conflict")
        self._generation = (self._generation or 0) + 1
        self._bytes = payload
        return {
            "operation_id": values["operation_id"],
            "artifact_revision_id": values["artifact_revision_id"],
            "workbench": workbench,
            "path": path,
            "generation": self._generation,
        }


if not _FENCED:

    def _publish_bytes_without_fence(
        self: Client, workbench: str, path: str, payload: bytes, **values: Any
    ) -> dict[str, Any]:
        return self._publish(workbench, path, payload, values)

    Client.publish_bytes = _publish_bytes_without_fence  # type: ignore[method-assign]
