from __future__ import annotations

import hashlib
import json
import math
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .control_plane.goals.configure_goal_service import (
    configure_goal_with_global_sync,
)
from .capabilities.multi_subagent import (
    apply_codex_subagent_capacity,
    plan_codex_subagent_capacity,
    public_codex_host_capacity,
)
from .capabilities.periodic_report.workspace import (
    DEFAULT_WORKSPACE_INDEX_LIMIT,
    MAX_WORKSPACE_INDEX_LIMIT,
    MAX_WORKSPACE_INDEX_OFFSET,
    collect_periodic_report_workspace_index,
    read_published_periodic_report_workspace_projection,
)
from .control_plane.status.ssh_host_catalog import (
    SSH_HOST_CATALOG_PATH,
    ssh_host_catalog_payload,
)
from .extensions.presentation import (
    collect_active_extension_presentation_surfaces,
    read_extension_projection,
)
from .extensions.runtime import default_extension_state_file
from .feedback import append_human_reward, compact_reward, validate_goal_id
from .history import load_registry
from .materials import read_review_material
from .paths import resolve_runtime_root
from .release_manifest import release_runtime_identity
from .status import collect_status


DEFAULT_STATUS_HOST = "127.0.0.1"
DEFAULT_STATUS_PORT = 8765
DEFAULT_STATUS_PATH = "/status.json"
DEFAULT_REWARD_DRY_RUN_PATH = "/reward/dry-run"
DEFAULT_REWARD_APPEND_PATH = "/reward/append"
DEFAULT_CONFIGURE_GOAL_DRY_RUN_PATH = "/control-plane/configure-goal/dry-run"
DEFAULT_CONFIGURE_GOAL_APPLY_PATH = "/control-plane/configure-goal/apply"
DEFAULT_REVIEW_MATERIAL_PATH = "/review-material"
DEFAULT_EXTENSION_PRESENTATION_SURFACES_PATH = "/extension-presentation-surfaces"
DEFAULT_EXTENSION_PROJECTION_PATH = "/extension-projection"
DEFAULT_PERIODIC_REPORT_INDEX_PATH = "/periodic-report-workspace"
DEFAULT_PERIODIC_REPORT_PROJECTION_PATH = "/periodic-report-workspace-projection"
DEFAULT_SSH_HOSTS_PATH = SSH_HOST_CATALOG_PATH

REWARD_REQUEST_FIELDS = {
    "goal_id",
    "run_generated_at",
    "recorded_at",
    "decision",
    "reward",
    "reason_summary",
    "follow_up",
    "lesson_kind",
    "lesson_summary",
    "lesson_avoid",
    "lesson_prefer",
}
REWARD_APPEND_FIELDS = REWARD_REQUEST_FIELDS | {
    "preview_id",
    "write_active_state_summary",
}
CONFIGURE_GOAL_REQUEST_FIELDS = {
    "goal_id",
    "quota_compute",
    "quota_window_hours",
    "self_repair_enabled",
    "self_repair_health",
    "self_repair_waiting_projection",
    "multi_subagent_feature",
    "orchestration_mode",
    "spawn_allowed",
    "max_children",
    "align_codex_subagent_capacity",
    "allowed_domains",
    "clear_allowed_domains",
    "registered_agents",
    "clear_registered_agents",
    "peer_task_coordinator",
    "clear_peer_task_coordinator",
    "agent_profiles",
    "clear_agent_profiles",
    "agent_work_modes",
    "clear_agent_work_modes",
    "todo_lifecycle_authority",
    "clear_todo_lifecycle_authority",
    "agent_model",
    "supervisor_agent",
    "supervised_agents",
    "clear_supervisor",
    "write_scope",
    "replace_write_scope",
    "clear_write_scope",
    "boundary_authority_scopes",
    "boundary_authority_source",
    "boundary_authority_decision_id",
    "boundary_authority_recorded_at",
    "boundary_authority_expires_at",
    "clear_boundary_authority",
}
CONFIGURE_GOAL_APPLY_FIELDS = CONFIGURE_GOAL_REQUEST_FIELDS | {"preview_id"}


def _reject_non_standard_json_constant(value: str) -> None:
    raise ValueError(
        f"request body must be strict JSON; non-standard constant {value} is not allowed"
    )


def _require_finite_json_numbers(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("request body must be strict JSON; non-finite numbers are not allowed")
    if isinstance(value, dict):
        for item in value.values():
            _require_finite_json_numbers(item)
    elif isinstance(value, list):
        for item in value:
            _require_finite_json_numbers(item)


def parse_strict_json_object(raw: bytes) -> dict[str, Any]:
    payload = json.loads(
        raw.decode("utf-8"),
        parse_constant=_reject_non_standard_json_constant,
    )
    _require_finite_json_numbers(payload)
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")
    return payload


def parse_goal_activation_filter(query: dict[str, list[str]]) -> str | None:
    """Parse the shared scoped-status query without accepting ambiguous input."""

    values = query.get("goal_activation", [])
    if len(values) > 1 or (values and values[0] not in {"active", "stopped"}):
        raise ValueError("goal_activation must be active or stopped")
    return values[0] if values else None


def _json_boolean(body: dict[str, Any], field: str, *, default: bool = False) -> bool:
    if field not in body:
        return default
    value = body[field]
    if type(value) is not bool:
        raise ValueError(f"{field} must be a JSON boolean")
    return value


def _optional_json_boolean(body: dict[str, Any], field: str) -> bool | None:
    return _json_boolean(body, field) if field in body else None


def is_loopback_host(host: str) -> bool:
    hostname = host.strip().lower()
    return hostname in {"127.0.0.1", "localhost", "::1", "[::1]"}


def is_loopback_origin(origin: str | None) -> bool:
    if not origin:
        return True
    try:
        parsed = urlparse(origin)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and is_loopback_host(parsed.hostname or "")


def cors_response_headers(origin: str | None) -> dict[str, str]:
    """Return CORS headers for an unauthenticated response.

    Only loopback browser origins may read responses cross-origin. A
    non-loopback ``Origin`` gets no ``Access-Control-Allow-Origin`` header so
    browsers block reads; non-browser clients (no ``Origin`` header) need no
    CORS headers at all.
    """

    if not origin or not is_loopback_origin(origin):
        return {}
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
        "Vary": "Origin",
    }


def reward_preview_id(payload: dict[str, Any]) -> str:
    stable = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()[:24]


def configure_goal_preview_id(payload: dict[str, Any]) -> str:
    codex_host_capacity = payload.get("codex_host_capacity")
    if isinstance(codex_host_capacity, dict) and isinstance(
        codex_host_capacity.get("plan_before_apply"), dict
    ):
        codex_host_capacity = codex_host_capacity["plan_before_apply"]
    stable_payload = {
        "goal_id": payload.get("goal_id"),
        "changed": payload.get("changed"),
        "changed_fields": payload.get("changed_fields"),
        "before": payload.get("before"),
        "after": payload.get("after"),
        "codex_host_capacity": codex_host_capacity,
    }
    stable = json.dumps(stable_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()[:24]


class StatusHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True

    registry_path: Path
    runtime_root_override: str | None
    scan_roots: list[Path]
    limit: int
    status_path: str
    reward_dry_run_path: str
    reward_append_path: str
    reward_write_enabled: bool
    configure_goal_dry_run_path: str
    configure_goal_apply_path: str
    control_plane_write_enabled: bool
    goal_subagent_configuration_enabled: bool
    ssh_config_path: Path | None
    verbose: bool


class StatusRequestHandler(BaseHTTPRequestHandler):
    server: StatusHTTPServer

    def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in cors_response_headers(self.headers.get("Origin")).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        for key, value in cors_response_headers(self.headers.get("Origin")).items():
            self.send_header(key, value)
        self.end_headers()

    def _read_json_body(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length") or "0")
        if content_length <= 0:
            raise ValueError("request body is empty")
        if content_length > 64_000:
            raise ValueError("request body is too large")
        return parse_strict_json_object(self.rfile.read(content_length))

    def _parse_reward_body(self, body: dict[str, Any], *, append: bool) -> tuple[str, str | None, dict[str, Any]]:
        allowed = REWARD_APPEND_FIELDS if append else REWARD_REQUEST_FIELDS
        unknown = sorted(set(body) - allowed)
        if unknown:
            raise ValueError(f"unknown reward field(s): {', '.join(unknown)}")

        goal_id = str(body.get("goal_id") or "").strip()
        validate_goal_id(goal_id)
        decision = str(body.get("decision") or "").strip()
        reward_value = str(body.get("reward") or "").strip()
        reason_summary = str(body.get("reason_summary") or "").strip()
        run_generated_at = body.get("run_generated_at")
        follow_up_value = body.get("follow_up")
        follow_up = str(follow_up_value).strip() if follow_up_value else None
        lesson_kind = str(body.get("lesson_kind") or "").strip()
        lesson_summary = str(body.get("lesson_summary") or "").strip()
        lesson = None
        if lesson_kind or lesson_summary or body.get("lesson_avoid") or body.get("lesson_prefer"):
            lesson = {
                "kind": lesson_kind,
                "summary": lesson_summary,
                "avoid": body.get("lesson_avoid") or [],
                "prefer": body.get("lesson_prefer") or [],
            }
        if not goal_id:
            raise ValueError("goal_id is required")
        if append and not run_generated_at:
            raise ValueError("run_generated_at is required for reward append")
        if not decision:
            raise ValueError("decision is required")
        if not reason_summary:
            raise ValueError("reason_summary is required")

        reward = compact_reward(
            recorded_at=str(body.get("recorded_at")).strip() if body.get("recorded_at") else None,
            decision=decision,
            reward=reward_value,
            reason_summary=reason_summary,
            follow_up=follow_up,
            lesson=lesson,
        )
        return goal_id, str(run_generated_at).strip() if run_generated_at else None, reward

    def _compact_reward_response(
        self,
        payload: dict[str, Any],
        *,
        dry_run: bool,
        appended: bool,
        request_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        human_reward = payload.get("human_reward")
        reward_for_preview = dict(human_reward) if isinstance(human_reward, dict) else human_reward
        if isinstance(reward_for_preview, dict) and not (request_body or {}).get("recorded_at"):
            reward_for_preview.pop("recorded_at", None)
        preview_payload = {
            "goal_id": payload.get("goal_id"),
            "raw_index_records_before": payload.get("raw_index_records_before"),
            "selected_run": payload.get("selected_run"),
            "human_reward": reward_for_preview,
        }
        return {
            "ok": True,
            "dry_run": dry_run,
            "appended": appended,
            "goal_id": payload.get("goal_id"),
            "raw_index_records_before": payload.get("raw_index_records_before"),
            "preview_id": reward_preview_id(preview_payload),
            "selected_run": payload.get("selected_run"),
            "human_reward": human_reward,
            "active_state_summary": payload.get("active_state_summary"),
            "project_agent_visibility": payload.get("project_agent_visibility"),
        }

    def _reward_dry_run_payload(self, body: dict[str, Any], *, append: bool = False) -> dict[str, Any]:
        goal_id, run_generated_at, reward = self._parse_reward_body(body, append=append)
        return append_human_reward(
            registry_path=self.server.registry_path,
            runtime_root_override=self.server.runtime_root_override,
            goal_id=goal_id,
            run_generated_at=run_generated_at,
            reward=reward,
            dry_run=True,
            write_active_state_summary=(
                _json_boolean(body, "write_active_state_summary", default=True)
                if append
                else False
            ),
        )

    def _handle_reward_dry_run(self) -> None:
        try:
            body = self._read_json_body()
            payload = self._reward_dry_run_payload(body)
        except Exception as exc:  # noqa: BLE001 - preserve validation diagnostics for the local UI.
            self._send_json(
                {
                    "ok": False,
                    "dry_run": True,
                    "appended": False,
                    "error": str(exc),
                },
                status=400,
            )
            return

        self._send_json(self._compact_reward_response(payload, dry_run=True, appended=False, request_body=body))

    def _handle_reward_append(self) -> None:
        if not self.server.reward_write_enabled:
            self._send_json(
                {
                    "ok": False,
                    "dry_run": False,
                    "appended": False,
                    "error": "reward write API is not enabled; restart serve-status with --enable-reward-write-api",
                },
                status=403,
            )
            return
        if not is_loopback_origin(self.headers.get("Origin")):
            self._send_json(
                {
                    "ok": False,
                    "dry_run": False,
                    "appended": False,
                    "error": "reward append only accepts loopback browser origins",
                },
                status=403,
            )
            return

        try:
            body = self._read_json_body()
            preview_id = str(body.get("preview_id") or "").strip()
            if not preview_id:
                raise ValueError("preview_id is required")
            dry_run_payload = self._reward_dry_run_payload(body, append=True)
            expected_preview = self._compact_reward_response(
                dry_run_payload,
                dry_run=True,
                appended=False,
                request_body=body,
            ).get("preview_id")
            if preview_id != expected_preview:
                self._send_json(
                    {
                        "ok": False,
                        "dry_run": False,
                        "appended": False,
                        "error": "stale reward preview; run Dry-run Check again before appending",
                    },
                    status=409,
                )
                return
            goal_id, run_generated_at, reward = self._parse_reward_body(body, append=True)
            payload = append_human_reward(
                registry_path=self.server.registry_path,
                runtime_root_override=self.server.runtime_root_override,
                goal_id=goal_id,
                run_generated_at=run_generated_at,
                reward=reward,
                dry_run=False,
                write_active_state_summary=_json_boolean(
                    body, "write_active_state_summary", default=True
                ),
            )
        except Exception as exc:  # noqa: BLE001 - preserve validation diagnostics for the local UI.
            self._send_json(
                {
                    "ok": False,
                    "dry_run": False,
                    "appended": False,
                    "error": str(exc),
                    **({"error_code": exc.code, **getattr(exc, "payload", {})} if isinstance(getattr(exc, "code", None), str) else {}),
                },
                status=400,
            )
            return

        self._send_json(self._compact_reward_response(payload, dry_run=False, appended=True, request_body=body))

    def _parse_configure_goal_body(self, body: dict[str, Any], *, apply: bool) -> dict[str, Any]:
        allowed = CONFIGURE_GOAL_APPLY_FIELDS if apply else CONFIGURE_GOAL_REQUEST_FIELDS
        unknown = sorted(set(body) - allowed)
        if unknown:
            raise ValueError(f"unknown configure-goal field(s): {', '.join(unknown)}")
        goal_id = str(body.get("goal_id") or "").strip()
        validate_goal_id(goal_id)
        if not goal_id:
            raise ValueError("goal_id is required")
        allowed_domains = body.get("allowed_domains")
        if allowed_domains is not None and not isinstance(allowed_domains, list):
            raise ValueError("allowed_domains must be a list of strings")
        registered_agents = body.get("registered_agents")
        if registered_agents is not None and not isinstance(registered_agents, list):
            raise ValueError("registered_agents must be a list of strings")
        agent_profiles = body.get("agent_profiles")
        if agent_profiles is not None and not isinstance(agent_profiles, list):
            raise ValueError("agent_profiles must be a list of objects")
        clear_agent_profiles = body.get("clear_agent_profiles")
        if clear_agent_profiles is not None and not isinstance(clear_agent_profiles, list):
            raise ValueError("clear_agent_profiles must be a list of strings")
        agent_work_modes = body.get("agent_work_modes")
        if agent_work_modes is not None and not isinstance(agent_work_modes, dict):
            raise ValueError("agent_work_modes must be an object")
        clear_agent_work_modes = body.get("clear_agent_work_modes")
        if clear_agent_work_modes is not None and not isinstance(
            clear_agent_work_modes, list
        ):
            raise ValueError("clear_agent_work_modes must be a list of strings")
        todo_lifecycle_authority = body.get("todo_lifecycle_authority")
        if (
            todo_lifecycle_authority is not None
            and not isinstance(todo_lifecycle_authority, list)
        ):
            raise ValueError("todo_lifecycle_authority must be a list of objects")
        clear_todo_lifecycle_authority = body.get(
            "clear_todo_lifecycle_authority"
        )
        if (
            clear_todo_lifecycle_authority is not None
            and not isinstance(clear_todo_lifecycle_authority, list)
        ):
            raise ValueError(
                "clear_todo_lifecycle_authority must be a list of strings"
            )
        supervised_agents = body.get("supervised_agents")
        if supervised_agents is not None and not isinstance(supervised_agents, list):
            raise ValueError("supervised_agents must be a list of strings")
        boundary_authority_scopes = body.get("boundary_authority_scopes")
        if boundary_authority_scopes is not None and not isinstance(boundary_authority_scopes, list):
            raise ValueError("boundary_authority_scopes must be a list of strings")
        write_scope = body.get("write_scope")
        if write_scope is not None and not isinstance(write_scope, list):
            raise ValueError("write_scope must be a list of strings")
        return {
            "goal_id": goal_id,
            "quota_compute": body.get("quota_compute"),
            "quota_window_hours": body.get("quota_window_hours"),
            "self_repair_enabled": _optional_json_boolean(body, "self_repair_enabled"),
            "self_repair_health": _optional_json_boolean(body, "self_repair_health"),
            "self_repair_waiting_projection": _optional_json_boolean(
                body, "self_repair_waiting_projection"
            ),
            "multi_subagent_feature": body.get("multi_subagent_feature"),
            "orchestration_mode": body.get("orchestration_mode"),
            "spawn_allowed": _optional_json_boolean(body, "spawn_allowed"),
            "max_children": body.get("max_children"),
            "align_codex_subagent_capacity": _json_boolean(
                body, "align_codex_subagent_capacity"
            ),
            "allowed_domains": [str(item) for item in allowed_domains] if allowed_domains is not None else None,
            "clear_allowed_domains": _json_boolean(body, "clear_allowed_domains"),
            "registered_agents": [str(item) for item in registered_agents] if registered_agents is not None else None,
            "clear_registered_agents": _json_boolean(body, "clear_registered_agents"),
            "peer_task_coordinator": body.get("peer_task_coordinator"),
            "clear_peer_task_coordinator": _json_boolean(
                body, "clear_peer_task_coordinator"
            ),
            "agent_profiles": agent_profiles,
            "clear_agent_profiles": (
                [str(item) for item in clear_agent_profiles]
                if clear_agent_profiles is not None
                else None
            ),
            "agent_work_modes": agent_work_modes,
            "clear_agent_work_modes": (
                [str(item) for item in clear_agent_work_modes]
                if clear_agent_work_modes is not None
                else None
            ),
            "todo_lifecycle_authority": todo_lifecycle_authority,
            "clear_todo_lifecycle_authority": (
                [str(item) for item in clear_todo_lifecycle_authority]
                if clear_todo_lifecycle_authority is not None
                else None
            ),
            "agent_model": body.get("agent_model"),
            "supervisor_agent": body.get("supervisor_agent"),
            "supervised_agents": (
                [str(item) for item in supervised_agents]
                if supervised_agents is not None
                else None
            ),
            "clear_supervisor": _json_boolean(body, "clear_supervisor"),
            "write_scope": [str(item) for item in write_scope] if write_scope is not None else None,
            "replace_write_scope": _json_boolean(body, "replace_write_scope"),
            "clear_write_scope": _json_boolean(body, "clear_write_scope"),
            "boundary_authority_scopes": (
                [str(item) for item in boundary_authority_scopes]
                if boundary_authority_scopes is not None
                else None
            ),
            "boundary_authority_source": body.get("boundary_authority_source"),
            "boundary_authority_decision_id": body.get("boundary_authority_decision_id"),
            "boundary_authority_recorded_at": body.get("boundary_authority_recorded_at"),
            "boundary_authority_expires_at": body.get("boundary_authority_expires_at"),
            "clear_boundary_authority": _json_boolean(body, "clear_boundary_authority"),
        }

    def _configure_goal_payload(self, body: dict[str, Any], *, apply: bool, execute: bool) -> dict[str, Any]:
        values = self._parse_configure_goal_body(body, apply=apply)
        return configure_goal_with_global_sync(
            registry_path=self.server.registry_path,
            goal_id=values["goal_id"],
            runtime_root_override=self.server.runtime_root_override,
            quota_compute=values["quota_compute"],
            quota_window_hours=values["quota_window_hours"],
            self_repair_enabled=values["self_repair_enabled"],
            self_repair_health=values["self_repair_health"],
            self_repair_waiting_projection=values["self_repair_waiting_projection"],
            multi_subagent_feature=values["multi_subagent_feature"],
            orchestration_mode=values["orchestration_mode"],
            spawn_allowed=values["spawn_allowed"],
            max_children=values["max_children"],
            align_codex_subagent_capacity=values[
                "align_codex_subagent_capacity"
            ],
            codex_host_capacity_planner=plan_codex_subagent_capacity,
            codex_host_capacity_applier=apply_codex_subagent_capacity,
            allowed_domains=values["allowed_domains"],
            clear_allowed_domains=values["clear_allowed_domains"],
            registered_agents=values["registered_agents"],
            clear_registered_agents=values["clear_registered_agents"],
            peer_task_coordinator=values["peer_task_coordinator"],
            clear_peer_task_coordinator=values[
                "clear_peer_task_coordinator"
            ],
            agent_profiles=values["agent_profiles"],
            clear_agent_profiles=values["clear_agent_profiles"],
            agent_work_modes=values["agent_work_modes"],
            clear_agent_work_modes=values["clear_agent_work_modes"],
            todo_lifecycle_authority=values["todo_lifecycle_authority"],
            clear_todo_lifecycle_authority=values[
                "clear_todo_lifecycle_authority"
            ],
            agent_model=values["agent_model"],
            supervisor_agent=values["supervisor_agent"],
            supervised_agents=values["supervised_agents"],
            clear_supervisor=values["clear_supervisor"],
            write_scope=values["write_scope"],
            replace_write_scope=values["replace_write_scope"],
            clear_write_scope=values["clear_write_scope"],
            boundary_authority_scopes=values["boundary_authority_scopes"],
            boundary_authority_source=values["boundary_authority_source"],
            boundary_authority_decision_id=values["boundary_authority_decision_id"],
            boundary_authority_recorded_at=values["boundary_authority_recorded_at"],
            boundary_authority_expires_at=values["boundary_authority_expires_at"],
            clear_boundary_authority=values["clear_boundary_authority"],
            execute=execute,
        )

    def _compact_configure_goal_response(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": True,
            "dry_run": payload.get("dry_run"),
            "execute": payload.get("execute"),
            "written": payload.get("written"),
            "changed": payload.get("changed"),
            "goal_id": payload.get("goal_id"),
            "changed_fields": payload.get("changed_fields"),
            "before": payload.get("before"),
            "after": payload.get("after"),
            "preview_id": configure_goal_preview_id(payload),
            "control_plane_summary": payload.get("control_plane_summary"),
            "orchestration_summary": payload.get("orchestration_summary"),
            "feature_summary": payload.get("feature_summary"),
            "goal_configuration_changed": payload.get(
                "goal_configuration_changed"
            ),
            "codex_host_capacity": public_codex_host_capacity(payload),
            "heartbeat_prompt_migration": payload.get("heartbeat_prompt_migration"),
            "supervisor_prompt": payload.get("supervisor_prompt"),
            "global_sync": payload.get("global_sync"),
        }

    def _handle_configure_goal_dry_run(self) -> None:
        try:
            payload = self._configure_goal_payload(self._read_json_body(), apply=False, execute=False)
        except Exception as exc:  # noqa: BLE001 - preserve validation diagnostics for the local UI.
            self._send_json(
                {
                    "ok": False,
                    "dry_run": True,
                    "execute": False,
                    "written": False,
                    "error": str(exc),
                },
                status=400,
            )
            return
        self._send_json(self._compact_configure_goal_response(payload))

    def _handle_configure_goal_apply(self) -> None:
        if not self.server.control_plane_write_enabled:
            self._send_json(
                {
                    "ok": False,
                    "dry_run": False,
                    "execute": True,
                    "written": False,
                    "error": "control-plane write API is not enabled; restart serve-status with --enable-control-plane-write-api",
                },
                status=403,
            )
            return
        if not is_loopback_origin(self.headers.get("Origin")):
            self._send_json(
                {
                    "ok": False,
                    "dry_run": False,
                    "execute": True,
                    "written": False,
                    "error": "control-plane apply only accepts loopback browser origins",
                },
                status=403,
            )
            return
        try:
            body = self._read_json_body()
            preview_id = str(body.get("preview_id") or "").strip()
            if not preview_id:
                raise ValueError("preview_id is required")
            dry_run_payload = self._configure_goal_payload(body, apply=True, execute=False)
            expected_preview = self._compact_configure_goal_response(dry_run_payload).get("preview_id")
            if preview_id != expected_preview:
                self._send_json(
                    {
                        "ok": False,
                        "dry_run": False,
                        "execute": True,
                        "written": False,
                        "error": "stale control-plane preview; run Dry-run Check again before applying",
                    },
                    status=409,
                )
                return
            payload = self._configure_goal_payload(body, apply=True, execute=True)
        except Exception as exc:  # noqa: BLE001 - preserve validation diagnostics for the local UI.
            self._send_json(
                {
                    "ok": False,
                    "dry_run": False,
                    "execute": True,
                    "written": False,
                    "error": str(exc),
                },
                status=400,
            )
            return
        self._send_json(self._compact_configure_goal_response(payload))

    def _handle_review_material(self, query: dict[str, list[str]]) -> None:
        if not is_loopback_host(str(self.server.server_address[0])):
            self._send_json(
                {
                    "ok": False,
                    "error": "review material reads require a loopback status server",
                },
                status=403,
            )
            return
        goal_id = (query.get("goal_id") or [""])[0].strip()
        raw_path = (query.get("path") or [""])[0].strip()
        if not goal_id or not raw_path:
            self._send_json(
                {
                    "ok": False,
                    "error": "goal_id and path are required",
                },
                status=400,
            )
            return
        try:
            registry = load_registry(self.server.registry_path)
            payload = read_review_material(
                registry=registry,
                runtime_root=resolve_runtime_root(registry, self.server.runtime_root_override),
                goal_id=goal_id,
                raw_path=raw_path,
            )
        except Exception as exc:  # noqa: BLE001 - local UI should see the exact read failure.
            self._send_json(
                {
                    "ok": False,
                    "goal_id": goal_id,
                    "path": raw_path,
                    "error": str(exc),
                },
                status=400,
            )
            return
        self._send_json(payload)

    def _handle_extension_projection(self, query: dict[str, list[str]]) -> None:
        if not is_loopback_host(str(self.server.server_address[0])):
            self._send_json(
                {
                    "ok": False,
                    "error": "extension projection reads require a loopback status server",
                },
                status=403,
            )
            return
        if not is_loopback_origin(self.headers.get("Origin")):
            self._send_json(
                {
                    "ok": False,
                    "error": "extension projection reads only accept loopback browser origins",
                },
                status=403,
            )
            return
        extension_id = (query.get("extension_id") or [""])[0].strip()
        surface_id = (query.get("surface_id") or [""])[0].strip()
        extension_revision = (query.get("extension_revision") or [""])[0].strip()
        payload_sha256 = (query.get("payload_sha256") or [""])[0].strip()
        if not (extension_id and surface_id and extension_revision and payload_sha256):
            self._send_json(
                {
                    "ok": False,
                    "error": (
                        "extension_id, surface_id, extension_revision, and "
                        "payload_sha256 are required"
                    ),
                },
                status=400,
            )
            return
        try:
            registry = load_registry(self.server.registry_path)
            runtime_root = resolve_runtime_root(
                registry,
                self.server.runtime_root_override,
                registry_path=self.server.registry_path,
            )
            envelope = read_extension_projection(
                state_file=default_extension_state_file(runtime_root),
                extension_id=extension_id,
                surface_id=surface_id,
                extension_revision=extension_revision,
                payload_sha256=payload_sha256,
            )
        except Exception as exc:  # noqa: BLE001 - local UI needs the read failure.
            self._send_json(
                {
                    "ok": False,
                    "extension_id": extension_id,
                    "surface_id": surface_id,
                    "error": str(exc),
                },
                status=400,
            )
            return
        self._send_json({"ok": True, "projection": envelope})

    def _handle_extension_presentation_surfaces(self) -> None:
        if not is_loopback_host(str(self.server.server_address[0])):
            self._send_json(
                {
                    "ok": False,
                    "error": (
                        "extension presentation surfaces require a loopback "
                        "status server"
                    ),
                },
                status=403,
            )
            return
        if not is_loopback_origin(self.headers.get("Origin")):
            self._send_json(
                {
                    "ok": False,
                    "error": (
                        "extension presentation surfaces only accept loopback "
                        "browser origins"
                    ),
                },
                status=403,
            )
            return
        try:
            registry = load_registry(self.server.registry_path)
            runtime_root = resolve_runtime_root(
                registry,
                self.server.runtime_root_override,
                registry_path=self.server.registry_path,
            )
            surfaces = collect_active_extension_presentation_surfaces(
                state_file=default_extension_state_file(runtime_root),
            )
        except Exception as exc:  # noqa: BLE001 - local UI needs the read failure.
            self._send_json(
                {
                    "ok": False,
                    "error": str(exc),
                },
                status=400,
            )
            return
        self._send_json({"ok": True, "presentation_surfaces": surfaces})

    def _handle_periodic_report_index(self, query: dict[str, list[str]]) -> None:
        if not is_loopback_host(
            str(self.server.server_address[0])
        ) or not is_loopback_origin(self.headers.get("Origin")):
            self._send_json(
                {"ok": False, "error": "periodic report reads require loopback access"},
                status=403,
            )
            return
        try:
            registry = load_registry(self.server.registry_path)
            runtime_root = resolve_runtime_root(
                registry,
                self.server.runtime_root_override,
                registry_path=self.server.registry_path,
            )
            goal_id = (query.get("goal_id") or [""])[0].strip() or None
            limit_text = (query.get("limit") or [""])[0].strip()
            offset_text = (query.get("offset") or [""])[0].strip()
            window_requested = "limit" in query or "offset" in query
            limit = DEFAULT_WORKSPACE_INDEX_LIMIT if not limit_text else int(limit_text)
            offset = 0 if not offset_text else int(offset_text)
            if limit < 0 or limit > MAX_WORKSPACE_INDEX_LIMIT:
                raise ValueError(
                    "periodic report index limit must be between 0 and "
                    f"{MAX_WORKSPACE_INDEX_LIMIT}"
                )
            if offset < 0 or offset > MAX_WORKSPACE_INDEX_OFFSET:
                raise ValueError(
                    "periodic report index offset must be between 0 and "
                    f"{MAX_WORKSPACE_INDEX_OFFSET}"
                )
            index = collect_periodic_report_workspace_index(
                runtime_root=runtime_root,
                goal_id=goal_id,
                limit=limit,
                offset=offset,
            )
            if not window_requested:
                index = {
                    "schema_version": index["schema_version"],
                    "count": index["count"],
                    "items": index["items"],
                }
        except Exception as exc:  # noqa: BLE001 - local UI needs the read failure.
            self._send_json({"ok": False, "error": str(exc)}, status=400)
            return
        self._send_json({"ok": True, "periodic_reports": index})

    def _handle_periodic_report_projection(self, query: dict[str, list[str]]) -> None:
        if not is_loopback_host(
            str(self.server.server_address[0])
        ) or not is_loopback_origin(self.headers.get("Origin")):
            self._send_json(
                {"ok": False, "error": "periodic report reads require loopback access"},
                status=403,
            )
            return
        values = {
            key: (query.get(key) or [""])[0].strip()
            for key in (
                "goal_id",
                "agent_id",
                "generation_id",
                "content_sha256",
            )
        }
        if not all(values.values()):
            self._send_json(
                {"ok": False, "error": "exact periodic report ref is required"},
                status=400,
            )
            return
        try:
            registry = load_registry(self.server.registry_path)
            projection = read_published_periodic_report_workspace_projection(
                runtime_root=resolve_runtime_root(
                    registry,
                    self.server.runtime_root_override,
                    registry_path=self.server.registry_path,
                ),
                **values,
            )
        except Exception as exc:  # noqa: BLE001 - local UI needs the read failure.
            self._send_json({"ok": False, "error": str(exc)}, status=400)
            return
        self._send_json({"ok": True, "projection": projection})

    def _handle_ssh_hosts(self) -> None:
        if not is_loopback_host(str(self.server.server_address[0])):
            self._send_json(
                {
                    "ok": False,
                    "error": "SSH Host discovery requires a loopback status server",
                },
                status=403,
            )
            return
        if not is_loopback_origin(self.headers.get("Origin")):
            self._send_json(
                {
                    "ok": False,
                    "error": "SSH Host discovery only accepts loopback browser origins",
                },
                status=403,
            )
            return
        self._send_json(
            ssh_host_catalog_payload(getattr(self.server, "ssh_config_path", None))
        )

    def _local_dashboard_api_payload(self) -> dict[str, Any]:
        return {
            "source": "serve-status",
            "runtime_identity": release_runtime_identity(),
            "status_url": self.server.status_path,
            "health_url": "/healthz",
            "readiness_url": "/?readiness=1",
            "review_material_url": DEFAULT_REVIEW_MATERIAL_PATH,
            "presentation_surfaces_url": (
                DEFAULT_EXTENSION_PRESENTATION_SURFACES_PATH
            ),
            "presentation_detail_url": DEFAULT_EXTENSION_PROJECTION_PATH,
            "periodic_report_index_url": DEFAULT_PERIODIC_REPORT_INDEX_PATH,
            "periodic_report_detail_url": DEFAULT_PERIODIC_REPORT_PROJECTION_PATH,
            "ssh_hosts_url": DEFAULT_SSH_HOSTS_PATH,
            "reward_dry_run_url": self.server.reward_dry_run_path,
            "reward_append_url": self.server.reward_append_path if self.server.reward_write_enabled else None,
            "reward_write_enabled": self.server.reward_write_enabled,
            "configure_goal_dry_run_url": self.server.configure_goal_dry_run_path,
            "configure_goal_apply_url": self.server.configure_goal_apply_path
            if self.server.control_plane_write_enabled
            else None,
            "control_plane_write_enabled": self.server.control_plane_write_enabled,
        }

    def _status_readiness(self) -> dict[str, str]:
        # Read only the existing configuration boundary, not Goal projections,
        # provider health, or repository scans. An absent registry is a valid
        # empty installation under load_registry's existing contract.
        reason = "registry_readable"
        try:
            registry = load_registry(self.server.registry_path)
            if not isinstance(registry, dict):
                reason = "registry_invalid"
        except (ValueError, UnicodeError):
            reason = "registry_invalid"
        except OSError:
            reason = "registry_unavailable"
        return {
            "schema_version": "loopx_status_readiness_v0",
            "state": "ready" if reason == "registry_readable" else "failed",
            "reason": reason,
        }

    def do_GET(self) -> None:
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        # Keep blank values so `?goal_activation=` and repeated values like
        # `?goal_activation=active&goal_activation=` fail closed with HTTP 400
        # instead of being silently dropped by the default parse_qs behavior.
        query = parse_qs(parsed_url.query, keep_blank_values=True)
        if path == "/healthz":
            self._send_json({"ok": True})
            return
        if path == DEFAULT_REVIEW_MATERIAL_PATH:
            self._handle_review_material(query)
            return
        if path == DEFAULT_EXTENSION_PRESENTATION_SURFACES_PATH:
            self._handle_extension_presentation_surfaces()
            return
        if path == DEFAULT_EXTENSION_PROJECTION_PATH:
            self._handle_extension_projection(query)
            return
        if path == DEFAULT_PERIODIC_REPORT_INDEX_PATH:
            self._handle_periodic_report_index(query)
            return
        if path == DEFAULT_PERIODIC_REPORT_PROJECTION_PATH:
            self._handle_periodic_report_projection(query)
            return
        if path == DEFAULT_SSH_HOSTS_PATH:
            self._handle_ssh_hosts()
            return
        if path in {"", "/"}:
            readiness = query.get("readiness")
            if readiness is not None and readiness != ["1"]:
                self._send_json(
                    {"ok": False, "error": "readiness must be specified once as 1"},
                    status=400,
                )
                return
            if readiness is not None and (
                not is_loopback_host(str(self.server.server_address[0]))
                or not is_loopback_origin(self.headers.get("Origin"))
            ):
                self._send_json(
                    {"ok": False, "error": "readiness requires loopback access"},
                    status=403,
                )
                return
            self._send_json(
                {
                    "ok": True,
                    **self._local_dashboard_api_payload(),
                    **({"readiness": self._status_readiness()} if readiness is not None else {}),
                }
            )
            return
        if path != self.server.status_path:
            self._send_json(
                {
                    "ok": False,
                    "error": f"unknown path: {path}",
                    "status_url": self.server.status_path,
                },
                status=404,
            )
            return

        try:
            activation_state_filter = parse_goal_activation_filter(query)
        except ValueError as exc:
            self._send_json(
                {
                    "ok": False,
                    "error": str(exc),
                },
                status=400,
            )
            return

        try:
            payload = collect_status(
                registry_path=self.server.registry_path,
                runtime_root_override=self.server.runtime_root_override,
                scan_roots=self.server.scan_roots,
                limit=self.server.limit,
                include_public_boundary_scan=False,
                include_goal_subagent_configuration=(
                    getattr(
                        self.server,
                        "goal_subagent_configuration_enabled",
                        False,
                    )
                ),
                activation_state_filter=activation_state_filter,
            )
            payload["local_dashboard_api"] = self._local_dashboard_api_payload()
        except Exception as exc:  # noqa: BLE001 - the HTTP layer should preserve diagnostics.
            self._send_json(
                {
                    "ok": False,
                    "registry": str(self.server.registry_path),
                    "runtime_root": self.server.runtime_root_override,
                    "error": str(exc),
                },
                status=500,
            )
            return

        self._send_json(payload)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == self.server.reward_dry_run_path:
            self._handle_reward_dry_run()
            return
        if path == self.server.reward_append_path:
            self._handle_reward_append()
            return
        if path == self.server.configure_goal_dry_run_path:
            self._handle_configure_goal_dry_run()
            return
        if path == self.server.configure_goal_apply_path:
            self._handle_configure_goal_apply()
            return
        self._send_json(
            {
                "ok": False,
                "error": f"unknown path: {path}",
                "reward_dry_run_url": self.server.reward_dry_run_path,
                "reward_append_url": self.server.reward_append_path if self.server.reward_write_enabled else None,
                "configure_goal_dry_run_url": self.server.configure_goal_dry_run_path,
                "configure_goal_apply_url": self.server.configure_goal_apply_path
                if self.server.control_plane_write_enabled
                else None,
            },
            status=404,
        )

    def log_message(self, format: str, *args: object) -> None:
        if self.server.verbose:
            super().log_message(format, *args)


def normalize_status_path(path: str) -> str:
    trimmed = path.strip() or DEFAULT_STATUS_PATH
    if not trimmed.startswith("/"):
        trimmed = f"/{trimmed}"
    return trimmed


def serve_status(
    *,
    registry_path: Path,
    runtime_root_override: str | None,
    scan_roots: list[Path],
    limit: int,
    host: str,
    port: int,
    status_path: str,
    enable_reward_write_api: bool,
    enable_control_plane_write_api: bool,
    verbose: bool,
    enable_goal_subagent_configuration: bool = False,
) -> None:
    normalized_path = normalize_status_path(status_path)
    if enable_reward_write_api and not is_loopback_host(host):
        raise ValueError("--enable-reward-write-api requires a loopback --host such as 127.0.0.1")
    if enable_control_plane_write_api and not is_loopback_host(host):
        raise ValueError("--enable-control-plane-write-api requires a loopback --host such as 127.0.0.1")
    server = StatusHTTPServer((host, port), StatusRequestHandler)
    server.registry_path = registry_path
    server.runtime_root_override = runtime_root_override
    server.scan_roots = scan_roots
    server.limit = limit
    server.status_path = normalized_path
    server.reward_dry_run_path = DEFAULT_REWARD_DRY_RUN_PATH
    server.reward_append_path = DEFAULT_REWARD_APPEND_PATH
    server.reward_write_enabled = enable_reward_write_api
    server.configure_goal_dry_run_path = DEFAULT_CONFIGURE_GOAL_DRY_RUN_PATH
    server.configure_goal_apply_path = DEFAULT_CONFIGURE_GOAL_APPLY_PATH
    server.control_plane_write_enabled = enable_control_plane_write_api
    server.goal_subagent_configuration_enabled = (
        enable_goal_subagent_configuration
    )
    server.ssh_config_path = None
    server.verbose = verbose
    print(f"Serving LoopX status at http://{host}:{port}{normalized_path}", flush=True)
    print(f"Reward dry-run: http://{host}:{port}{server.reward_dry_run_path}", flush=True)
    if enable_reward_write_api:
        print(f"Reward append: http://{host}:{port}{server.reward_append_path}", flush=True)
    print(f"Control-plane settings dry-run: http://{host}:{port}{server.configure_goal_dry_run_path}", flush=True)
    if enable_control_plane_write_api:
        print(f"Control-plane settings apply: http://{host}:{port}{server.configure_goal_apply_path}", flush=True)
    print(f"Health check: http://{host}:{port}/healthz", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping LoopX status server")
    finally:
        server.server_close()
