"""Path-free Settings transport for original Goal work and source recovery."""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ..control_plane.goals.planned_transfer_io import (
    cancel_registered_goal_transfer,
    inspect_registered_goal_transfer,
    confirm_registered_goal_transfer,
)

GOAL_TRANSFER_PATH = "/api/chat/goal-transfer"


class GoalTransferRequestMixin:
    server: Any

    if TYPE_CHECKING:
        def _read_json(self) -> dict[str, Any]: ...
        def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None: ...
        def _send_error(self, message: str, *, status: int, error_code: str) -> None: ...

    def _goal_transfer(self) -> None:
        try:
            body = self._read_json()
            action = body.get("action")
            fields = {"action", "goal_id", "operation_id", "preview_sha256"} if action == "confirm" else (
                {"action", "goal_id", "operation_id"} if action == "cancel" else {"action", "goal_id"})
            if set(body) != fields or action not in ("inspect", "cancel", "confirm"):
                raise ValueError("Unsupported transfer request")
            if any(not isinstance(value, str) or not value.strip() for value in body.values()):
                raise ValueError("Transfer request requires original identifiers")
            # Both surfaces resolve the project source through the existing
            # registry codec. Browser data cannot redirect filesystem owners.
            if action == "inspect":
                result = inspect_registered_goal_transfer(registry_path=self.server.registry_path, goal_id=body["goal_id"])
            elif action == "confirm":
                result = confirm_registered_goal_transfer(registry_path=self.server.registry_path, goal_id=body["goal_id"],
                    operation_id=body["operation_id"], expected_preview_sha256=body["preview_sha256"])
            else:
                result = {"ok": True, **cancel_registered_goal_transfer(registry_path=self.server.registry_path,
                    goal_id=body["goal_id"], operation_id=body["operation_id"])}
            self._send_json(result)
        except Exception:
            self._send_error("The original transfer state is unavailable. Retry the same operation; recorded decisions and destination work are preserved.",
                status=400, error_code="goal_transfer_unavailable")
