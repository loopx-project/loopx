from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from loopx.chat_goal_configuration_api import (
    CHAT_GOAL_CONFIGURATION_APPLY_PATH,
    CHAT_GOAL_CONFIGURATION_PATH,
    CHAT_GOAL_CONFIGURATION_PREVIEW_PATH,
    GoalConfigurationRequestMixin,
    _goal_capability_options,
)
from loopx.control_plane.goals import configure_goal_service


def _catalog_payload(*, explore_enabled: bool = False) -> dict[str, Any]:
    return {
        "goal_id": "goal-example",
        "configuration_catalog": {
            "features": [
                {
                    "feature_id": "explore_graph",
                    "display_name": "Explore Graph",
                    "effect": "Durable evidence graph.",
                    "default": {"enabled": False},
                    "current": {"enabled": explore_enabled},
                }
            ],
            "capability_catalog": {
                "schema_version": "capability_configuration_catalog_v0",
                "capabilities": [
                    {
                        "capability_id": "explore_graph",
                        "display_name": "Explore Graph",
                        "description": "Durable evidence graph.",
                        "available_scopes": ["goal"],
                        "goal_feature_id": "explore_graph",
                        "current": {"enabled": explore_enabled},
                        "configuration_editor": {
                            "schema_version": "capability_configuration_editor_v0",
                            "editable": True,
                            "supported_scopes": ["goal"],
                            "fields": [],
                        },
                    }
                ],
            }
        },
        "registry": "/private/path/that/must/not/project",
        "backup_path": "/private/backup/that/must/not/project",
    }


def _periodic_catalog_payload(*, override_present: bool) -> dict[str, Any]:
    feature: dict[str, Any] = {
        "feature_id": "periodic_report",
        "display_name": "Periodic reports",
        "effect": "Complete Goal-specific report route.",
        "default": {"enabled": False, "timezone": "UTC"},
    }
    if override_present:
        feature["current"] = {
            "enabled": True,
            "profile_preset": "goal-weekly",
            "route_ref": "existing-goal-binding",
            "timezone": "UTC",
        }
    return {
        "goal_id": "goal-example",
        "configuration_catalog": {
            "features": [feature],
            "capability_catalog": {
                "schema_version": "capability_configuration_catalog_v0",
                "capabilities": [],
            },
        },
    }


class _Handler(GoalConfigurationRequestMixin):
    def __init__(self, path: str, result: dict[str, Any] | Exception) -> None:
        self.path = path
        self.result = result
        self.server = SimpleNamespace(registry_path=Path("/tmp/registry.json"))
        self.responses: list[dict[str, Any]] = []

    def _goal_configuration_reader(self):
        def read(**kwargs: Any) -> dict[str, Any]:
            assert kwargs == {
                "registry_path": Path("/tmp/registry.json"),
                "goal_id": "goal-example",
                "execute": False,
            }
            if isinstance(self.result, Exception):
                raise self.result
            return self.result

        return read

    def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
        self.responses.append({"status_code": status, **payload})

    def _send_error(
        self,
        message: str,
        *,
        status: int,
        error_code: str,
        **_kwargs: Any,
    ) -> None:
        self.responses.append(
            {
                "ok": False,
                "status_code": status,
                "error": message,
                "error_code": error_code,
            }
        )


class _MachineAwareHandler(_Handler):
    def _goal_configuration_machine_namespaces(self):
        return [
            {
                "namespace": "periodic_report",
                "title": "Periodic reports",
                "description": "Live report defaults.",
                "configuration_template": {"enabled": False, "timezone": "UTC"},
                "current": {
                    "enabled": True,
                    "profile_preset": "weekly-progress",
                    "route_ref": "loopx-manager",
                    "timezone": "Asia/Shanghai",
                },
            }
        ]


class _MutationHandler(GoalConfigurationRequestMixin):
    def __init__(self, body: dict[str, Any]) -> None:
        self.path = CHAT_GOAL_CONFIGURATION_PREVIEW_PATH
        self.body = body
        self.applied = False
        self.server = SimpleNamespace(
            registry_path=Path("/tmp/registry.json"),
            runtime_root_override=None,
        )
        self.responses: list[dict[str, Any]] = []

    def _read_json(self) -> dict[str, Any]:
        return self.body

    def _goal_configuration_reader(self):
        def read(**_kwargs: Any) -> dict[str, Any]:
            return _catalog_payload(explore_enabled=self.applied)

        return read

    def _goal_configuration_writer(self):
        def write(*, execute: bool, **kwargs: Any) -> dict[str, Any]:
            assert kwargs["goal_id"] == "goal-example"
            assert kwargs["explore_graph_enabled"] is True
            if execute:
                self.applied = True
            return {
                **_catalog_payload(explore_enabled=True),
                "ok": True,
                "changed": not self.applied,
                "changed_fields": ["explore_graph"],
            }

        return write

    def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
        self.responses.append({"status_code": status, **payload})

    def _send_error(
        self,
        message: str,
        *,
        status: int,
        error_code: str,
        **_kwargs: Any,
    ) -> None:
        self.responses.append(
            {
                "ok": False,
                "status_code": status,
                "error": message,
                "error_code": error_code,
            }
        )


class _PeriodicClearHandler(_MutationHandler):
    def __init__(self, body: dict[str, Any]) -> None:
        super().__init__(body)
        self.override_present = True

    def _goal_configuration_machine_namespaces(self):
        return _MachineAwareHandler._goal_configuration_machine_namespaces(self)

    def _goal_configuration_reader(self):
        def read(**_kwargs: Any) -> dict[str, Any]:
            return _periodic_catalog_payload(
                override_present=self.override_present,
            )

        return read

    def _goal_configuration_writer(self):
        def write(*, execute: bool, **kwargs: Any) -> dict[str, Any]:
            assert kwargs["goal_id"] == "goal-example"
            assert kwargs["clear_periodic_report_configuration"] is True
            if execute:
                self.override_present = False
            return {
                **_periodic_catalog_payload(override_present=False),
                "ok": True,
                "changed": True,
                "changed_fields": ["periodic_report"],
            }

        return write


class _PartialWriteHandler(_MutationHandler):
    def _goal_configuration_writer(self):
        def write(*, execute: bool, **kwargs: Any) -> dict[str, Any]:
            assert kwargs["goal_id"] == "goal-example"
            assert kwargs["explore_graph_enabled"] is True
            if not execute:
                return {
                    **_catalog_payload(explore_enabled=True),
                    "ok": True,
                    "changed": True,
                    "changed_fields": ["explore_graph"],
                }
            self.applied = True
            return {
                **_catalog_payload(explore_enabled=True),
                "ok": False,
                "written": True,
                "partial_write": True,
                "changed": True,
                "changed_fields": ["explore_graph"],
                "error": "configure-goal shared registry readback did not verify",
                "recommended_action": (
                    "rerun loopx sync-global --goal-id goal-example after repairing "
                    "the shared runtime route"
                ),
            }

        return write


class _PartialWriteReadbackFailureHandler(_PartialWriteHandler):
    def _goal_configuration_reader(self):
        def read(**_kwargs: Any) -> dict[str, Any]:
            if self.applied:
                raise OSError("source readback unavailable")
            return _catalog_payload(explore_enabled=False)

        return read


def test_goal_configuration_inspection_is_path_free_and_revisioned() -> None:
    handler = _Handler(
        f"{CHAT_GOAL_CONFIGURATION_PATH}?goal_id=goal-example",
        _catalog_payload(),
    )

    handler._goal_configuration_inspect()

    response = handler.responses[0]
    assert response["status_code"] == 200
    assert response["schema_version"] == "goal_configuration_inspection_v0"
    assert response["goal_id"] == "goal-example"
    assert response["available_capabilities"] == ["explore_graph"]
    assert str(response["revision"]).startswith("sha256:")
    assert "registry" not in response
    assert "backup_path" not in response


def test_goal_configuration_merges_live_machine_defaults_without_goal_override() -> None:
    handler = _MachineAwareHandler(
        f"{CHAT_GOAL_CONFIGURATION_PATH}?goal_id=goal-example",
        _catalog_payload(),
    )

    handler._goal_configuration_inspect()

    response = handler.responses[0]
    capabilities = {
        item["capability_id"]: item
        for item in response["capability_catalog"]["capabilities"]
    }
    periodic = capabilities["periodic_report"]
    assert periodic["available_scopes"] == ["machine", "goal"]
    assert "current" not in periodic
    assert periodic["machine_current"]["route_ref"] == "loopx-manager"
    assert periodic["effective_configuration"]["source"] == "machine_default"
    assert periodic["effective_configuration"]["inherited"] is True


def test_machine_inheritable_goal_capabilities_can_clear_their_overrides() -> None:
    assert _goal_capability_options("todo_replan_cadence", None) == {
        "clear_execution_replan_after_todos": True
    }
    assert _goal_capability_options("change_quality_qualification", None) == {
        "clear_change_quality_configuration": True
    }
    assert _goal_capability_options("reward_memory", None) == {
        "clear_reward_memory_config": True
    }


def test_reward_memory_goal_editor_reuses_the_capability_owned_configurator() -> None:
    assert _goal_capability_options(
        "reward_memory",
        {
            "config_path": ".loopx/config/reward-memory/private.json",
            "enabled_agents": ["researcher"],
        },
    ) == {
        "reward_memory_config": ".loopx/config/reward-memory/private.json",
        "reward_memory_agents": ["researcher"],
    }
    assert _goal_capability_options(
        "reward_memory", {"enabled_agents": ["researcher"]}
    ) == {
        "reward_memory_config": None,
        "reward_memory_agents": ["researcher"],
    }


def test_coordination_runtime_shadow_editor_uses_the_transaction_bound_owner() -> None:
    assert _goal_capability_options(
        "coordination_runtime_shadow", {"enabled": True}
    ) == {"coordination_runtime_shadow_file": True}
    assert _goal_capability_options(
        "coordination_runtime_shadow", {"enabled": False}
    ) == {"clear_coordination_runtime_shadow": True}


def test_goal_configuration_inspection_requires_one_goal_id() -> None:
    handler = _Handler(CHAT_GOAL_CONFIGURATION_PATH, _catalog_payload())
    handler._goal_configuration_inspect()

    assert handler.responses == [
        {
            "ok": False,
            "status_code": 400,
            "error": "goal_id is required exactly once",
            "error_code": "invalid_goal_configuration_request",
        }
    ]


def test_goal_configuration_inspection_sanitizes_unexpected_failures() -> None:
    handler = _Handler(
        f"{CHAT_GOAL_CONFIGURATION_PATH}?goal_id=goal-example",
        RuntimeError("private failure"),
    )
    handler._goal_configuration_inspect()

    assert handler.responses == [
        {
            "ok": False,
            "status_code": 500,
            "error": "Goal configuration could not be inspected.",
            "error_code": "goal_configuration_inspection_failed",
        }
    ]


def test_goal_configuration_preview_and_apply_are_revision_locked() -> None:
    body = {
        "goal_id": "goal-example",
        "capability_id": "explore_graph",
        "configuration": {"enabled": True},
    }
    preview_handler = _MutationHandler(body)
    preview_handler._goal_configuration_update(execute=False)
    preview = preview_handler.responses[0]

    assert preview["status_code"] == 201
    assert preview["status"] == "preview"
    assert preview["action"] == "update"
    assert preview["goal_configuration"] == {"enabled": True}

    apply_handler = _MutationHandler(
        {**body, "expected_plan_revision": preview["plan_revision"]}
    )
    apply_handler.path = CHAT_GOAL_CONFIGURATION_APPLY_PATH
    apply_handler._goal_configuration_update(execute=True)
    receipt = apply_handler.responses[0]

    assert receipt["status_code"] == 200
    assert receipt["status"] == "applied"
    assert receipt["readback_verified"] is True
    assert receipt["goal_configuration"] == {"enabled": True}


def test_goal_configuration_apply_rejects_stale_preview() -> None:
    handler = _MutationHandler(
        {
            "goal_id": "goal-example",
            "capability_id": "explore_graph",
            "configuration": {"enabled": True},
            "expected_plan_revision": "sha256:" + "0" * 64,
        }
    )

    handler._goal_configuration_update(execute=True)

    assert handler.responses[0]["status_code"] == 409
    assert handler.responses[0]["error_code"] == "goal_configuration_preview_stale"


def test_goal_configuration_clear_override_is_revision_locked() -> None:
    body = {
        "goal_id": "goal-example",
        "capability_id": "periodic_report",
        "configuration": None,
    }
    preview_handler = _PeriodicClearHandler(body)
    preview_handler._goal_configuration_update(execute=False)
    preview = preview_handler.responses[0]

    assert preview["status_code"] == 201
    assert preview["action"] == "delete"
    assert preview["goal_configuration"] is None

    apply_handler = _PeriodicClearHandler(
        {**body, "expected_plan_revision": preview["plan_revision"]}
    )
    apply_handler.path = CHAT_GOAL_CONFIGURATION_APPLY_PATH
    apply_handler._goal_configuration_update(execute=True)
    receipt = apply_handler.responses[0]

    assert receipt["status_code"] == 200
    assert receipt["readback_verified"] is True
    assert receipt["goal_configuration"] is None
    periodic = next(
        item
        for item in receipt["capability_catalog"]["capabilities"]
        if item["capability_id"] == "periodic_report"
    )
    assert periodic["effective_configuration"]["source"] == "machine_default"


def test_goal_configuration_apply_preserves_partial_write_receipt() -> None:
    body = {
        "goal_id": "goal-example",
        "capability_id": "explore_graph",
        "configuration": {"enabled": True},
    }
    preview_handler = _PartialWriteHandler(body)
    preview_handler._goal_configuration_update(execute=False)
    preview = preview_handler.responses[0]

    apply_handler = _PartialWriteHandler(
        {**body, "expected_plan_revision": preview["plan_revision"]}
    )
    apply_handler.path = CHAT_GOAL_CONFIGURATION_APPLY_PATH
    apply_handler._goal_configuration_update(execute=True)

    receipt = apply_handler.responses[0]
    assert receipt["status_code"] == 207
    assert receipt["ok"] is False
    assert receipt["status"] == "partial_write"
    assert receipt["source_written"] is True
    assert receipt["shared_sync_pending"] is True
    assert receipt["readback_verified"] is True
    assert receipt["goal_configuration"] == {"enabled": True}
    assert receipt["recommended_action"].startswith("rerun loopx sync-global")
    assert "prior state was preserved" not in receipt.get("error", "")


def test_goal_configuration_partial_write_survives_source_readback_failure() -> None:
    body = {
        "goal_id": "goal-example",
        "capability_id": "explore_graph",
        "configuration": {"enabled": True},
    }
    preview_handler = _PartialWriteReadbackFailureHandler(body)
    preview_handler._goal_configuration_update(execute=False)
    preview = preview_handler.responses[0]
    apply_handler = _PartialWriteReadbackFailureHandler(
        {**body, "expected_plan_revision": preview["plan_revision"]}
    )
    apply_handler._goal_configuration_update(execute=True)

    receipt = apply_handler.responses[0]
    assert receipt["status_code"] == 207
    assert receipt["status"] == "partial_write"
    assert receipt["source_written"] is True
    assert receipt["readback_verified"] is False
    assert receipt["applied_revision"] is None
    assert receipt["goal_configuration"] is None


def test_goal_configuration_service_rechecks_revision_before_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_path = tmp_path / "registry.json"
    registry_path.write_text("{}\n", encoding="utf-8")
    calls: list[dict[str, Any]] = []

    def configure_goal_stub(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        assert kwargs["execute"] is False
        return _catalog_payload(explore_enabled=True)

    monkeypatch.setattr(
        configure_goal_service,
        "configure_goal",
        configure_goal_stub,
    )

    with pytest.raises(ValueError, match="preview again"):
        configure_goal_service.configure_goal_with_global_sync(
            registry_path=registry_path,
            goal_id="goal-example",
            runtime_root_override=None,
            execute=True,
            expected_goal_configuration_revision="sha256:" + "0" * 64,
            explore_graph_enabled=True,
        )

    assert len(calls) == 1
    assert registry_path.read_text(encoding="utf-8") == "{}\n"
