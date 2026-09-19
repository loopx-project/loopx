"""Loopback Chat API boundary for preview-locked per-Goal sub-agent settings."""

from __future__ import annotations

from typing import Any

from .control_plane.goals.configure_goal_service import configure_goal_with_global_sync
from .capabilities.multi_subagent import (
    apply_codex_subagent_capacity,
    plan_codex_subagent_capacity,
    public_codex_host_capacity,
)
from .control_plane.todos.contract import normalize_todo_task_domain
from .status_server import configure_goal_preview_id
from .orchestration import subagent_model_configuration_options


CHAT_GOAL_SUBAGENT_DRY_RUN_PATH = "/api/chat/goal-subagents/dry-run"
CHAT_GOAL_SUBAGENT_APPLY_PATH = "/api/chat/goal-subagents/apply"
CHAT_GOAL_SUBAGENT_MAX_CHILDREN = 32


def goal_subagent_configuration_enabled(server: Any) -> bool:
    return bool(getattr(server, "goal_subagent_configuration_enabled", False))


def add_goal_subagent_capability(
    capabilities: dict[str, Any],
    *,
    server: Any,
) -> None:
    if goal_subagent_configuration_enabled(server):
        capabilities["goal_subagent_configuration"] = "preview_locked"


def add_goal_subagent_routes(
    routes: dict[str, Any],
    *,
    handler: Any,
) -> None:
    if goal_subagent_configuration_enabled(handler.server):
        routes[CHAT_GOAL_SUBAGENT_DRY_RUN_PATH] = lambda: (
            handler._goal_subagent_configuration(apply=False)
        )
        routes[CHAT_GOAL_SUBAGENT_APPLY_PATH] = lambda: (
            handler._goal_subagent_configuration(apply=True)
        )


class GoalSubagentConfigurationRequestMixin:
    """Preview and apply the existing canonical Goal orchestration policy."""

    server: Any

    def _read_json(self) -> dict[str, Any]:
        raise NotImplementedError

    def _registry_and_goal(self, goal_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        raise NotImplementedError

    def _send_error(self, message: str, **kwargs: Any) -> None:
        raise NotImplementedError

    def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
        raise NotImplementedError

    def _parse_goal_subagent_configuration(
        self,
        body: dict[str, Any],
        *,
        apply: bool,
    ) -> dict[str, Any]:
        allowed = {
            "goal_id",
            "enabled",
            "max_children",
            "allowed_domains",
            "model_config",
            "execution_config",
            "align_codex_host_capacity",
        }
        if apply:
            allowed.add("preview_id")
        unknown = sorted(set(body) - allowed)
        if unknown:
            raise ValueError(
                "unknown Goal sub-agent configuration field(s): " + ", ".join(unknown)
            )

        goal_id = " ".join(str(body.get("goal_id") or "").split())[:160].strip()
        if not goal_id:
            raise ValueError("goal_id is required")
        self._registry_and_goal(goal_id)
        if not isinstance(body.get("enabled"), bool):
            raise ValueError("enabled must be a boolean")
        enabled = body["enabled"]
        if "execution_config" in body and not isinstance(
            body.get("execution_config"), str
        ):
            raise ValueError("execution_config must be a string")
        if "align_codex_host_capacity" in body and not isinstance(
            body["align_codex_host_capacity"], bool
        ):
            raise ValueError("align_codex_host_capacity must be a boolean")

        if enabled:
            max_children = body.get("max_children")
            if isinstance(max_children, bool) or not isinstance(max_children, int):
                raise ValueError("max_children must be an integer")
            if not 1 <= max_children <= CHAT_GOAL_SUBAGENT_MAX_CHILDREN:
                raise ValueError(
                    "max_children must be between 1 and "
                    f"{CHAT_GOAL_SUBAGENT_MAX_CHILDREN}"
                )
            raw_domains = body.get("allowed_domains", [])
            if not isinstance(raw_domains, list):
                raise ValueError(
                    "allowed_domains must be a list of public-safe task domains"
                )
            allowed_domains: list[str] = []
            for raw_domain in raw_domains:
                domain = normalize_todo_task_domain(raw_domain)
                if not domain:
                    raise ValueError(
                        "allowed_domains must contain public-safe lowercase task-domain tokens"
                    )
                if domain not in allowed_domains:
                    allowed_domains.append(domain)
        else:
            if body.get("max_children") not in (None, 0):
                raise ValueError(
                    "disabled Goal sub-agents require max_children 0 or omitted"
                )
            if body.get("allowed_domains") not in (None, []):
                raise ValueError(
                    "disabled Goal sub-agents require allowed_domains [] or omitted"
                )
            max_children = 0
            allowed_domains = []

        return {
            "goal_id": goal_id,
            "multi_subagent_feature": "enabled" if enabled else "off",
            "max_children": max_children,
            "allowed_domains": allowed_domains,
            "align_codex_subagent_capacity": bool(
                body.get("align_codex_host_capacity", False)
            ),
            **(
                {
                    "subagent_execution_config": str(
                        body.get("execution_config") or ""
                    ).strip()
                }
                if "execution_config" in body
                and str(body.get("execution_config") or "").strip()
                else {"clear_subagent_execution_config": True}
                if "execution_config" in body
                else {}
            ),
            **(
                subagent_model_configuration_options(body["model_config"])
                if "model_config" in body
                else {}
            ),
        }

    def _goal_subagent_configuration_payload(
        self,
        body: dict[str, Any],
        *,
        apply: bool,
        execute: bool,
    ) -> dict[str, Any]:
        values = self._parse_goal_subagent_configuration(body, apply=apply)
        return configure_goal_with_global_sync(
            registry_path=self.server.registry_path,
            goal_id=values["goal_id"],
            runtime_root_override=self.server.runtime_root_override,
            multi_subagent_feature=values["multi_subagent_feature"],
            max_children=values["max_children"],
            allowed_domains=values["allowed_domains"],
            align_codex_subagent_capacity=values[
                "align_codex_subagent_capacity"
            ],
            codex_home_override=getattr(
                getattr(self.server, "runtime_controller", None),
                "codex_home",
                None,
            ),
            codex_host_capacity_planner=plan_codex_subagent_capacity,
            codex_host_capacity_applier=apply_codex_subagent_capacity,
            **{
                key: values[key]
                for key in (
                    "subagent_model",
                    "subagent_reasoning_effort",
                    "clear_subagent_model_config",
                    "subagent_execution_config",
                    "clear_subagent_execution_config",
                )
                if key in values
            },
            execute=execute,
        )

    @staticmethod
    def _public_codex_host_capacity(payload: dict[str, Any]) -> dict[str, Any]:
        return public_codex_host_capacity(payload)

    @staticmethod
    def _compact_goal_subagent_configuration(
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "ok": bool(payload.get("ok")),
            "dry_run": payload.get("dry_run"),
            "execute": payload.get("execute"),
            "written": payload.get("written"),
            "changed": payload.get("changed"),
            "goal_id": payload.get("goal_id"),
            "changed_fields": payload.get("changed_fields"),
            "before": payload.get("before"),
            "after": payload.get("after"),
            "preview_id": configure_goal_preview_id(payload),
            "orchestration_summary": payload.get("orchestration_summary"),
            "feature_summary": payload.get("feature_summary"),
            "goal_configuration_changed": payload.get(
                "goal_configuration_changed"
            ),
            "codex_host_capacity": (
                GoalSubagentConfigurationRequestMixin._public_codex_host_capacity(
                    payload
                )
            ),
            "global_sync": payload.get("global_sync"),
            "partial_write": bool(payload.get("partial_write")),
            "error": payload.get("error"),
            "recommended_action": payload.get("recommended_action"),
        }

    def _goal_subagent_configuration(self, *, apply: bool) -> None:
        try:
            body = self._read_json()
            if apply:
                preview_id = str(body.get("preview_id") or "").strip()
                if not preview_id:
                    raise ValueError("preview_id is required")
                dry_run = self._goal_subagent_configuration_payload(
                    body,
                    apply=True,
                    execute=False,
                )
                expected_preview = self._compact_goal_subagent_configuration(dry_run)[
                    "preview_id"
                ]
                if preview_id != expected_preview:
                    self._send_error(
                        "stale Goal sub-agent preview; generate a new preview before applying",
                        status=409,
                        error_code="stale_goal_subagent_preview",
                    )
                    return
                payload = self._goal_subagent_configuration_payload(
                    body,
                    apply=True,
                    execute=True,
                )
            else:
                payload = self._goal_subagent_configuration_payload(
                    body,
                    apply=False,
                    execute=False,
                )
        except ValueError as exc:
            self._send_error(
                str(exc),
                status=400,
                error_code="invalid_goal_subagent_configuration",
            )
            return
        except Exception:
            self._send_error(
                "Goal sub-agent configuration could not be completed.",
                status=400,
                error_code="goal_subagent_configuration_failed",
            )
            return
        response = self._compact_goal_subagent_configuration(payload)
        self._send_json(response, status=200 if response["ok"] else 409)
