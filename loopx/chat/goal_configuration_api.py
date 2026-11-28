"""HTTP inspection, preview, and apply mixin for Goal capability configuration."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import nullcontext
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..capabilities.configuration_inspection import (
    GOAL_CONFIGURATION_INSPECTION_SCHEMA,
    inspect_machine_namespaces,
    project_goal_configuration as _public_goal_configuration,
)
from ..capabilities.multi_subagent import (
    apply_codex_subagent_capacity,
    plan_codex_subagent_capacity,
)
from ..configuration_transaction import require_expected_configuration_plan_revision
from ..control_plane.goals.configure_goal_service import (
    configure_goal_with_global_sync,
    read_goal_configuration_with_source_route,
)
from .goal_configuration_options import goal_capability_options
from .goal_configuration_plans import (
    GoalConfigurationReader as GoalConfigurationReader,
    GoalConfigurationServerProtocol,
    GoalConfigurationWriter as GoalConfigurationWriter,
    build_applied_goal_configuration_payload,
    build_partial_write_receipt_payload,
    capability_entry as _capability_entry,
    codex_home_override,
    goal_configuration_update_plan as _goal_configuration_update_plan,
    parse_goal_configuration_update as _parse_goal_configuration_update,
    runtime_root_override_arg,
)

_goal_capability_options = goal_capability_options

CHAT_GOAL_CONFIGURATION_PATH = "/api/chat/goal-configuration"
CHAT_GOAL_CONFIGURATION_PREVIEW_PATH = f"{CHAT_GOAL_CONFIGURATION_PATH}/preview"
CHAT_GOAL_CONFIGURATION_APPLY_PATH = f"{CHAT_GOAL_CONFIGURATION_PATH}/apply"


class GoalConfigurationRequestMixin:
    """Serve Goal configuration inspection, dry-run preview, and apply endpoints."""

    server: GoalConfigurationServerProtocol
    path: str

    def _read_json(self) -> dict[str, object]:
        raise NotImplementedError

    def _send_json(self, payload: dict[str, object], *, status: int = 200) -> None:
        raise NotImplementedError

    def _send_error(
        self,
        message: str,
        *,
        status: int,
        error_code: str,
    ) -> None:
        raise NotImplementedError

    def _goal_configuration_reader(self) -> GoalConfigurationReader:
        return read_goal_configuration_with_source_route

    def _goal_configuration_writer(self) -> GoalConfigurationWriter:
        return configure_goal_with_global_sync

    @staticmethod
    def _goal_configuration_readback_matches(
        capability_id: str,
        actual: object,
        desired: object,
        applied: Mapping[str, object],
    ) -> bool:
        if capability_id == "reward_memory" and isinstance(actual, Mapping) and isinstance(desired, Mapping):
            from ..capabilities.reward_memory.configuration import reward_memory_configuration_readback_matches

            return bool(reward_memory_configuration_readback_matches(actual, desired, dict(applied)))
        return actual == desired

    def _goal_configuration_machine_namespaces(self) -> list[Mapping[str, object]] | None:
        runtime_root = getattr(self.server, "runtime_root", None)
        if not isinstance(runtime_root, Path):
            return None
        return list(inspect_machine_namespaces(runtime_root))

    def _goal_configuration_inspect(self) -> None:
        try:
            query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
            if set(query) - {"goal_id"}:
                raise ValueError("goal configuration request contains unknown fields")
            goal_ids = query.get("goal_id") or []
            if len(goal_ids) != 1 or not goal_ids[0].strip():
                raise ValueError("goal_id is required exactly once")
            goal_id = goal_ids[0].strip()
            result = self._goal_configuration_reader()(
                registry_path=self.server.registry_path,
                goal_id=goal_id,
                execute=False,
            )
            response = dict(
                _public_goal_configuration(
                    result,
                    machine_namespaces=self._goal_configuration_machine_namespaces(),
                )
            )
            from ..agent_registry import load_goal_from_registry
            from ..capabilities.progress_review.context import external_progress_review_context

            goal = load_goal_from_registry(self.server.registry_path, goal_id)
            runtime_root = getattr(self.server, "runtime_root", None)
            observation = external_progress_review_context(
                goal or {},
                runtime_root if isinstance(runtime_root, Path) else None,
            )
            if observation is not None:
                catalog = response.get("capability_catalog")
                capabilities = catalog.get("capabilities") if isinstance(catalog, Mapping) else None
                if isinstance(capabilities, list):
                    for capability in capabilities:
                        if isinstance(capability, dict) and capability.get("capability_id") == "progress_review":
                            capability["observation"] = observation["summary"]
        except (FileNotFoundError, KeyError, TypeError, ValueError) as exc:
            self._send_error(str(exc), status=400, error_code="invalid_goal_configuration_request")
            return
        except Exception:
            self._send_error(
                "Goal configuration could not be inspected.",
                status=500,
                error_code="goal_configuration_inspection_failed",
            )
            return
        self._send_json(response)

    def _read_public_goal_configuration(
        self,
        goal_id: str,
        machine_namespaces: list[Mapping[str, object]] | None = None,
    ) -> dict[str, object]:
        result = self._goal_configuration_reader()(
            registry_path=self.server.registry_path,
            goal_id=goal_id,
            execute=False,
        )
        return dict(
            _public_goal_configuration(
                result,
                machine_namespaces=(
                    self._goal_configuration_machine_namespaces()
                    if machine_namespaces is None
                    else machine_namespaces
                ),
            )
        )

    def _prepare_goal_configuration_plan(
        self,
        *,
        goal_id: str,
        capability_id: str,
        machine_namespaces: list[Mapping[str, object]] | None,
        options: Mapping[str, object],
    ) -> tuple[dict[str, object], dict[str, object], dict[str, object], object]:
        current_public = self._read_public_goal_configuration(goal_id, machine_namespaces)
        desired_result = self._goal_configuration_writer()(
            registry_path=self.server.registry_path,
            goal_id=goal_id,
            runtime_root_override=runtime_root_override_arg(self.server),
            execute=False,
            codex_home_override=codex_home_override(self.server),
            codex_host_capacity_planner=plan_codex_subagent_capacity,
            codex_host_capacity_applier=apply_codex_subagent_capacity,
            **options,
        )
        desired_public = dict(_public_goal_configuration(desired_result, machine_namespaces=machine_namespaces))
        plan, desired_configuration = _goal_configuration_update_plan(
            goal_id=goal_id,
            capability_id=capability_id,
            current_public=current_public,
            desired_public=desired_public,
            desired_result=desired_result,
        )
        return current_public, desired_public, plan, desired_configuration

    def _send_partial_write_receipt(
        self,
        *,
        applied: Mapping[str, object],
        capability_id: str,
        desired_configuration: object,
        desired_public: Mapping[str, object],
        goal_id: str,
        plan: Mapping[str, object],
        prefetched_readback: tuple[dict[str, object], object] | None = None,
    ) -> None:
        if prefetched_readback is not None:
            readback_public, readback_configuration = prefetched_readback
            readback_verified = False
        else:
            try:
                readback_public = self._read_public_goal_configuration(goal_id)
                readback_configuration = _capability_entry(readback_public, capability_id).get("current")
                readback_verified = self._goal_configuration_readback_matches(
                    capability_id, readback_configuration, desired_configuration, applied
                )
            except Exception:
                readback_public = dict(desired_public)
                readback_configuration = None
                readback_verified = False
        payload = build_partial_write_receipt_payload(
            applied=applied,
            capability_id=capability_id,
            goal_id=goal_id,
            plan=plan,
            readback_public=readback_public,
            readback_configuration=readback_configuration,
            readback_verified=readback_verified,
        )
        self._send_json(payload, status=207)

    def _send_applied_goal_configuration(
        self,
        *,
        applied: Mapping[str, object],
        capability_id: str,
        desired_configuration: object,
        desired_public: Mapping[str, object],
        goal_id: str,
        plan: Mapping[str, object],
        after_verified_apply: Callable[[], None] | None = None,
    ) -> None:
        try:
            readback_public = self._read_public_goal_configuration(goal_id)
            readback_configuration = _capability_entry(readback_public, capability_id).get("current")
            readback_verified = self._goal_configuration_readback_matches(
                capability_id, readback_configuration, desired_configuration, applied
            )
        except Exception:
            if not applied.get("written"):
                raise
            self._send_partial_write_receipt(
                applied={
                    **applied,
                    "error": "Configuration was written, but current readback is unavailable.",
                    "recommended_action": "Reload the current Goal configuration before previewing another change.",
                },
                capability_id=capability_id,
                desired_configuration=desired_configuration,
                desired_public=desired_public,
                goal_id=goal_id,
                plan=plan,
            )
            return
        if not readback_verified:
            if applied.get("written"):
                self._send_partial_write_receipt(
                    applied={
                        **applied,
                        "error": "Configuration was written, but current readback did not verify.",
                        "recommended_action": "Reload the current Goal configuration before previewing another change.",
                    },
                    capability_id=capability_id,
                    desired_configuration=desired_configuration,
                    desired_public=desired_public,
                    goal_id=goal_id,
                    plan=plan,
                    prefetched_readback=(readback_public, readback_configuration),
                )
                return
            raise RuntimeError("Goal configuration readback did not verify")
        if after_verified_apply is not None:
            after_verified_apply()
        payload = build_applied_goal_configuration_payload(
            applied=applied,
            capability_id=capability_id,
            goal_id=goal_id,
            plan=plan,
            readback_public=readback_public,
            readback_configuration=readback_configuration,
        )
        self._send_json(payload)

    def _goal_configuration_update(self, *, execute: bool) -> None:
        try:
            body = self._read_json()
            goal_id, capability_id, configuration, expected_revision = _parse_goal_configuration_update(
                body, execute=execute
            )
            options = _goal_capability_options(capability_id, configuration)
            machine_namespaces = self._goal_configuration_machine_namespaces()
            from ..capabilities.native_chat.steward_reports import session_report_configuration_guard

            controller = getattr(self.server, "runtime_controller", None)
            proposed = dict(configuration) if configuration is not None else None
            guard = (
                session_report_configuration_guard(controller, goal_id, proposed)
                if capability_id == "periodic_report" and controller is not None
                else nullcontext()
            )
            with guard as after_verified_apply:
                current_public, desired_public, plan, desired_configuration = self._prepare_goal_configuration_plan(
                    goal_id=goal_id,
                    capability_id=capability_id,
                    machine_namespaces=machine_namespaces,
                    options=options,
                )
                if not execute:
                    plan["capability_catalog"] = desired_public["capability_catalog"]
                    self._send_json(plan, status=201)
                    return
                require_expected_configuration_plan_revision(
                    expected_plan_revision=expected_revision,
                    actual_plan_revision=str(plan["plan_revision"]),
                    subject="Goal configuration",
                )
                applied = self._goal_configuration_writer()(
                    registry_path=self.server.registry_path,
                    goal_id=goal_id,
                    runtime_root_override=runtime_root_override_arg(self.server),
                    execute=True,
                    expected_goal_configuration_revision=str(current_public["revision"]),
                    codex_home_override=codex_home_override(self.server),
                    codex_host_capacity_planner=plan_codex_subagent_capacity,
                    codex_host_capacity_applier=apply_codex_subagent_capacity,
                    **options,
                )
                if not applied.get("ok"):
                    if not (applied.get("written") and applied.get("partial_write")):
                        raise RuntimeError("Goal configuration apply did not verify")
                    self._send_partial_write_receipt(
                        applied=applied,
                        capability_id=capability_id,
                        desired_configuration=desired_configuration,
                        desired_public=desired_public,
                        goal_id=goal_id,
                        plan=plan,
                    )
                    return
                self._send_applied_goal_configuration(
                    applied=applied,
                    capability_id=capability_id,
                    desired_configuration=desired_configuration,
                    desired_public=desired_public,
                    goal_id=goal_id,
                    plan=plan,
                    after_verified_apply=after_verified_apply,
                )
        except (TypeError, ValueError) as exc:
            stale = "preview again" in str(exc)
            self._send_error(
                str(exc),
                status=409 if stale else 400,
                error_code="goal_configuration_preview_stale" if stale else "invalid_goal_configuration",
            )
        except Exception:
            self._send_error(
                "Goal configuration could not be applied; the prior state was preserved.",
                status=500,
                error_code="goal_configuration_apply_failed",
            )


__all__ = [
    "CHAT_GOAL_CONFIGURATION_PATH",
    "CHAT_GOAL_CONFIGURATION_PREVIEW_PATH",
    "CHAT_GOAL_CONFIGURATION_APPLY_PATH",
    "GOAL_CONFIGURATION_INSPECTION_SCHEMA",
    "GoalConfigurationRequestMixin",
]
