"""Path-free App transport for the existing typed local-provider migration owner."""
from __future__ import annotations

import re
from typing import Any, TYPE_CHECKING, cast
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from ..control_plane.effect_runtime import effect_runtime_result

CHAT_GOAL_STORAGE_PATH = "/api/chat/goal-storage"


def _token(body: dict[str, Any], name: str, length: int) -> str:
    value = body.get(name)
    if not isinstance(value, str) or not re.fullmatch(rf"[a-f0-9]{{{length}}}", value):
        raise ValueError(f"invalid {name}")
    return value


class GoalStorageRequestMixin:
    server: Any
    path: str

    if TYPE_CHECKING:
        def _registry_and_goal(self, goal_id: str) -> tuple[dict[str, Any], dict[str, Any]]: ...
        def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None: ...
        def _send_error(self, message: str, *, status: int, error_code: str) -> None: ...
        def _read_json(self) -> dict[str, Any]: ...

    def _storage_owner(self, goal_id: str, **fields: Any) -> dict[str, Any]:
        self._registry_and_goal(goal_id)
        return cast(dict[str, Any], effect_runtime_result(
            "coordination.authority_archive.manage",
            {"schema_version": "loopx_authority_archive_admin_request_v0",
             "runtime_root": str(self.server.runtime_root.resolve()),
             "goal_id": goal_id, **fields}, timeout=300.0, retry_safe=False,
        ))

    def _storage_send(self, result: dict[str, Any], goal_id: str, preview_id: str | None = None) -> None:
        # The typed owner decides and verifies. Transport never returns local
        # filenames, exception prose, full plans, projections or raw archives.
        payload = {key: result[key] for key in (
            "ok", "status", "authority_changed", "execution_authority_granted",
            "plan_sha256", "reviewed_source", "target_provider", "selected_provider",
            "current", "recovery", "reason_code",
        ) if key in result}
        payload["goal_id"] = goal_id
        if preview_id is not None:
            payload["preview_id"] = preview_id
        self._send_json(payload, status=200 if result.get("ok") else 409)

    def _storage_inspect(self) -> None:
        try:
            query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
            if set(query) != {"goal_id"} or len(query["goal_id"]) != 1:
                raise ValueError("goal_id is required exactly once")
            goal_id = query["goal_id"][0]
            self._storage_send(self._storage_owner(goal_id, action="migration-readback"), goal_id)
        except (KeyError, TypeError, ValueError):
            self._send_error("Choose a registered Goal.", status=400, error_code="invalid_goal_storage_request")
        except Exception:  # noqa: BLE001 - local provider errors stay private.
            self._send_error("Current storage unavailable.", status=503, error_code="goal_storage_unavailable")

    def _storage_update(self, *, action: str) -> None:
        try:
            body = self._read_json()
            preview = action == "plan-migration"
            allowed = {"goal_id", "provider"} if preview else {"goal_id", "preview_id", "plan_sha256"}
            if set(body) != allowed or not isinstance(body.get("goal_id"), str):
                raise ValueError("invalid storage request")
            goal_id = body["goal_id"]
            self._registry_and_goal(goal_id)
            preview_id = uuid4().hex if preview else _token(body, "preview_id", 32)
            directory = self.server.runtime_root / "chat" / "storage-migrations"
            if preview:
                directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            fields: dict[str, Any] = {"action": action, "plan": str(directory / f"{preview_id}.json")}
            if preview:
                fields["provider"] = body["provider"]
            else:
                fields["plan_sha256"] = _token(body, "plan_sha256", 64)
                if action == "migrate":
                    fields["execute"] = True
            result = self._storage_owner(goal_id, **fields)
            if result.get("ok") and action == "migrate":
                # A committed result survives an independent read failure. The
                # original carrier is retained until the user reads it back.
                readback = self._storage_owner(goal_id, action="migration-readback", plan=fields["plan"], plan_sha256=fields["plan_sha256"])
                result.update({key: readback[key] for key in ("current", "recovery", "reviewed_source", "target_provider") if key in readback})
            self._storage_send(result, goal_id, preview_id)
        except (KeyError, TypeError, ValueError):
            self._send_error("Invalid Goal storage request.", status=400, error_code="invalid_goal_storage_request")
        except Exception:  # noqa: BLE001 - preserve the original carrier on ambiguity.
            self._send_error("Result unavailable. Read or retry the original preview.", status=503, error_code="goal_storage_unavailable")
