"""Loopback operator transport for the existing acceptance owner.

TypeScript retains configuration, evidence and CAS decisions. This adapter only
parses the local HTTP request and invokes the same host IO as the CLI.
"""

from __future__ import annotations
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, urlparse


class _AcceptanceServer(Protocol):
    registry_path: Path
    runtime_root: Path


class GoalAcceptanceRequestMixin:
    server: _AcceptanceServer
    path: str

    def _read_json(self) -> dict[str, Any]:
        raise NotImplementedError

    def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
        raise NotImplementedError

    def _send_error(self, message: str, *, status: int, error_code: str) -> None:
        raise NotImplementedError

    def _goal_acceptance_inspect(self) -> None:
        from .acceptance import inspect_goal_acceptance, public_goal_acceptance

        try:
            query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
            if (
                set(query) != {"goal_id"}
                or len(query["goal_id"]) != 1
                or not query["goal_id"][0].strip()
            ):
                raise ValueError("one goal_id is required")
            result = public_goal_acceptance(
                inspect_goal_acceptance(
                    registry_path=self.server.registry_path,
                    runtime_root=str(self.server.runtime_root),
                    goal_id=query["goal_id"][0],
                )
            )
            self._send_json({**result, "goal_id": query["goal_id"][0]})
        except (OSError, TypeError, ValueError, RuntimeError):
            self._send_error(
                "Acceptance is unavailable for this Goal; reload after its local authority is available.",
                status=409,
                error_code="goal_acceptance_unavailable",
            )

    def _goal_acceptance_update(self) -> None:
        from .acceptance import (
            configure_goal_acceptance,
            public_goal_acceptance,
            verify_goal_acceptance,
        )

        try:
            body = self._read_json()
            if set(body) - {
                "goal_id",
                "action",
                "document",
                "expected_provider_revision",
                "operation_id",
            }:
                raise ValueError("unknown acceptance request fields")
            goal_id, action = body.get("goal_id"), body.get("action")
            revision = body.get("expected_provider_revision")
            if (
                not isinstance(goal_id, str)
                or not goal_id.strip()
                or not isinstance(revision, str)
                or not revision
            ):
                raise ValueError("Goal and current provider revision are required")
            common: dict[str, Any] = {
                "registry_path": self.server.registry_path,
                "runtime_root": str(self.server.runtime_root),
                "goal_id": goal_id,
                "execute": True,
            }
            if action == "verify":
                if set(body) - {"goal_id", "action", "expected_provider_revision"}:
                    raise ValueError("check does not accept declarations or results")
                result = verify_goal_acceptance(
                    **common, expected_provider_revision=revision
                )
            elif action in {"configure", "disable"}:
                document = body.get("document")
                if (
                    action == "configure"
                    and not isinstance(document, dict)
                    or action == "disable"
                    and document is not None
                ):
                    raise ValueError(
                        "configuration requires a document; disable requires no document"
                    )
                operation_id = body.get("operation_id")
                if not isinstance(operation_id, str) or not operation_id:
                    raise ValueError(
                        "configuration requires a stable operation identity"
                    )
                result = public_goal_acceptance(
                    configure_goal_acceptance(
                        **common,
                        document=document,
                        disable=action == "disable",
                        expected_provider_revision=revision,
                        operation_id=operation_id,
                    )
                )
            else:
                raise ValueError("unknown acceptance operation")
            self._send_json({**result, "goal_id": goal_id})
        except (OSError, TypeError, ValueError, RuntimeError):
            # An uncertain check is never automatically replayed. The caller
            # can inspect the current receipt before explicitly running again.
            self._send_error(
                "Acceptance could not be applied or checked. Read back the current contract and receipt before retrying.",
                status=409,
                error_code="goal_acceptance_operation_failed",
            )
