"""Path-free App transport for the existing typed local-provider migration owner."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, TYPE_CHECKING, cast
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from ..control_plane.effect_runtime import effect_runtime_result
from ..control_plane.coordination.local_authority_shadow_projection import source_effect_runtime_result

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
            "operation_id", "source_inventory", "target_handoff_mode",
            "legacy_writer_fenced", "coordination_source_backup_verified", "complete_goal_backup_verified",
            "cold_source",
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
            registry, goal = self._registry_and_goal(goal_id)
        except (KeyError, TypeError, ValueError):
            self._send_error("Choose a registered Goal.", status=400, error_code="invalid_goal_storage_request")
            return
        try:
            result = self._storage_owner(goal_id, action="migration-readback")
            current = result.get("current")
            if result.get("ok") and isinstance(current, dict) and current.get("canonical") is False:
                from ..control_plane.coordination.local_authority_shadow_projection import source_effect_runtime_result
                from ..control_plane.coordination.runtime_shadow import build_runtime_shadow_source_snapshot
                from ..state_refresh import resolve_goal_state

                _, _, state_path = resolve_goal_state(registry=registry, goal_id=goal_id,
                    project_override=None, state_file_override=None)
                projection, snapshot = build_runtime_shadow_source_snapshot(goal=goal,
                    runtime_root=self.server.runtime_root, state_path=state_path,
                    registry_path=self.server.registry_path, include_all_archived_todos=True)
                result = source_effect_runtime_result("coordination.source.inspect_storage", {
                    "schema_version": "loopx_cold_source_inspection_request_v0",
                    "runtime_root": str(self.server.runtime_root.expanduser().absolute()),
                    "goal_id": goal_id, "projection": projection, "source_snapshot": snapshot,
                })
            self._storage_send(result, goal_id)
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

    def _storage_import(self, *, action: str) -> None:
        """Adapt registered source/backup IO; the shared TS owner owns cutover.

        No caller filenames, overrides or client-side source summaries enter
        the owner. Readback is read-only, including after an interrupted apply.
        """
        try:
            body = self._read_json()
            allowed = ({"goal_id", "provider", "handoff_mode"} if action == "prepare" else
                {"goal_id", "operation_id", "plan_sha256", "writers_stopped"} if action == "apply" else
                {"goal_id", "operation_id", "plan_sha256"})
            if set(body) != allowed or not isinstance(body.get("goal_id"), str):
                raise ValueError("invalid import request")
            goal_id = body["goal_id"]
            registry, goal = self._registry_and_goal(goal_id)
            operation = uuid4().hex if action == "prepare" else _token(body, "operation_id", 32)
            root = self.server.runtime_root.resolve()
            request: dict[str, Any] = {"schema_version": "loopx_cold_source_import_request_v0",
                "action": action, "runtime_root": str(root), "goal_id": goal_id, "operation_id": operation}
            if action == "prepare":
                from ..control_plane.goals.state_resolution import resolve_goal_state
                from ..control_plane.coordination.runtime_shadow import build_runtime_shadow_source_snapshot
                from ..control_plane.coordination.cold_source_backup import read_cold_source_backup
                from ..state_backup import build_state_backup_plan, execute_state_backup_plan

                _, project, state = resolve_goal_state(registry=registry, goal_id=goal_id,
                    project_override=None, state_file_override=None)
                if project is None:
                    raise ValueError("registered project is required")
                # Source capture witnesses physical filenames. Give backup IO
                # the same registry route so directory aliases cannot split
                # its saved member map from the source's byte witness.
                registry_path = Path(self.server.registry_path).resolve()
                projection, snapshot = build_runtime_shadow_source_snapshot(goal=goal, runtime_root=root,
                    state_path=state, registry_path=registry_path, include_all_archived_todos=True)
                backup = execute_state_backup_plan(build_state_backup_plan(project=project, runtime_root=root,
                    output_dir=root / "backups" / "cold-import", backup_id=operation,
                    include_automations=False, include_skills=False, include_registry_projects=False,
                    registry_path=registry_path))
                request.update(projection=projection, source_snapshot=snapshot,
                    target_provider=body["provider"], target_handoff_mode=body["handoff_mode"],
                    source_backup=read_cold_source_backup(Path(backup["manifest_path"])))
            else:
                request["expected_plan_sha256"] = _token(body, "plan_sha256", 64)
                if action == "apply":
                    request["writers_stopped"] = body["writers_stopped"]
            result = source_effect_runtime_result("coordination.cold_source.import", request,
                timeout=300.0, retry_safe=False)
            # Read the current store independently. Original intent/receipt
            # success never certifies a later provider or hides a read failure.
            observed = self._storage_owner(goal_id, action="migration-readback")
            result["current"] = observed.get("current") if observed.get("ok") else None
            self._storage_send(result, goal_id)
        except (KeyError, TypeError, ValueError):
            self._send_error("Invalid Goal import request.", status=400, error_code="invalid_goal_storage_request")
        except Exception:  # noqa: BLE001 - preserve the original carrier and private IO errors.
            self._send_error("Import result unavailable. Keep and read the original preview.",
                status=503, error_code="goal_storage_unavailable")
