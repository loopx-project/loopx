"""Contract, validation, and boundary tests for Zero-Any Goal configuration modules."""

from __future__ import annotations

import ast
from collections.abc import Mapping
import importlib.util
import io
from pathlib import Path
import tokenize
from types import MappingProxyType, SimpleNamespace

import pytest

from loopx.chat import goal_configuration_api, goal_configuration_options, goal_configuration_plans
from loopx.chat.goal_configuration_api import (
    CHAT_GOAL_CONFIGURATION_APPLY_PATH,
    CHAT_GOAL_CONFIGURATION_PATH,
    CHAT_GOAL_CONFIGURATION_PREVIEW_PATH,
    GOAL_CONFIGURATION_INSPECTION_SCHEMA,
    GoalConfigurationRequestMixin,
    _goal_capability_options,
)

_MODULE_PATHS = (
    "loopx/chat/goal_configuration_api.py",
    "loopx/chat/goal_configuration_options.py",
    "loopx/chat/goal_configuration_plans.py",
)


class _ProbeHandler(GoalConfigurationRequestMixin):
    def __init__(
        self,
        *,
        path: str = f"{CHAT_GOAL_CONFIGURATION_PATH}?goal_id=goal-a",
        registry_path: Path,
        runtime_root: Path | None = None,
        reader_result: dict[str, object] | None = None,
        readback_side_effects: list[dict[str, object] | Exception] | None = None,
    ) -> None:
        self.path = path
        self.server = SimpleNamespace(
            registry_path=registry_path,
            runtime_root=runtime_root,
            runtime_controller=SimpleNamespace(codex_home=registry_path.parent / "codex-home"),
            runtime_root_override=registry_path.parent / "runtime-override",
        )
        self.reader_result = reader_result or {}
        self.readback_side_effects = list(readback_side_effects or [])
        self.read_calls = 0
        self.responses: list[tuple[int, dict[str, object]]] = []

    def _read_json(self) -> dict[str, object]:
        return {}

    def _send_json(self, payload: dict[str, object], *, status: int = 200) -> None:
        self.responses.append((status, dict(payload)))

    def _send_error(self, message: str, *, status: int, error_code: str) -> None:
        self.responses.append((status, {"ok": False, "error": message, "error_code": error_code}))

    def _read_public_goal_configuration(
        self,
        goal_id: str,
        machine_namespaces: list[Mapping[str, object]] | None = None,
    ) -> dict[str, object]:
        self.read_calls += 1
        if self.readback_side_effects:
            outcome = self.readback_side_effects.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        return super()._read_public_goal_configuration(goal_id, machine_namespaces)


def test_goal_configuration_modularization_and_layout_pin() -> None:
    root = Path(__file__).resolve().parents[2]
    all_paths = (*_MODULE_PATHS, "tests/control_plane/test_chat_goal_configuration_api_contract.py")
    for rel_path in all_paths:
        text = (root / rel_path).read_text(encoding="utf-8")
        lines = text.splitlines()
        cap = 550 if rel_path == "loopx/chat/goal_configuration_api.py" else 650
        assert len(lines) <= cap, f"{rel_path} exceeded LOC budget ({len(lines)} > {cap})"
        comments = [
            tok.string
            for tok in tokenize.generate_tokens(io.StringIO(text).readline)
            if tok.type == tokenize.COMMENT
        ]
        assert not any("type: ignore" in comment for comment in comments), f"{rel_path} must stay free of type: ignore"
        assert not any("noqa" in comment for comment in comments), f"{rel_path} must stay free of noqa"
        tree = ast.parse(text)
        any_nodes = [node for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id == "Any"]
        assert any_nodes == [], f"{rel_path} must stay Zero-Any"
        if rel_path.startswith("loopx/chat/"):
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    span = (node.end_lineno or node.lineno) - node.lineno + 1
                    assert span <= 85, f"{rel_path}:{node.name} span too large ({span})"

    assert GoalConfigurationRequestMixin.__module__ == "loopx.chat.goal_configuration_api"
    assert importlib.util.find_spec("loopx.chat_goal_configuration_api") is None
    assert CHAT_GOAL_CONFIGURATION_PREVIEW_PATH == f"{CHAT_GOAL_CONFIGURATION_PATH}/preview"
    assert CHAT_GOAL_CONFIGURATION_APPLY_PATH == f"{CHAT_GOAL_CONFIGURATION_PATH}/apply"
    assert isinstance(GOAL_CONFIGURATION_INSPECTION_SCHEMA, str)
    assert callable(goal_configuration_options.goal_capability_options)
    assert callable(goal_configuration_plans.goal_configuration_update_plan)
    assert callable(goal_configuration_api._goal_capability_options)


def test_capability_option_validation_clears_and_error_paths() -> None:
    expected_clears = {
        "explore_harness": {"clear_explore_configuration": True},
        "goal_capability_organization": {"clear_capability_improvement_configuration": True},
        "periodic_report": {"clear_periodic_report_configuration": True},
        "pull_request_review": {"clear_pull_request_review_configuration": True},
        "change_quality_qualification": {"clear_change_quality_configuration": True},
        "progress_review": {"clear_progress_review_configuration": True},
        "reward_memory": {"clear_reward_memory_config": True},
    }
    for cap_id, expected in expected_clears.items():
        assert _goal_capability_options(cap_id, None) == expected
    assert _goal_capability_options("todo_replan_cadence", None)["clear_execution_replan_after_turns"] is True

    for non_clearable in ("explore_graph", "local_authority_shadow", "unknown"):
        with pytest.raises(ValueError, match="cannot be cleared"):
            _goal_capability_options(non_clearable, None)

    with pytest.raises(ValueError, match="read-only in Dashboard"):
        _goal_capability_options("unknown_capability", {"enabled": True})
    with pytest.raises(ValueError, match="unknown explore_graph configuration field"):
        _goal_capability_options("explore_graph", {"enabled": True, "extra": 1})

    with pytest.raises(TypeError, match="must be a boolean"):
        _goal_capability_options("change_quality_qualification", MappingProxyType({"enabled": 1}))
    assert _goal_capability_options(
        "change_quality_qualification",
        MappingProxyType({"enabled": True, "strict_receipt": True}),
    ) == {
        "change_quality_enabled": True,
        "change_quality_safe_fix": False,
        "change_quality_strict_receipt": True,
    }

    # multi_subagent validation boundaries
    with pytest.raises(ValueError, match="requires a model"):
        _goal_capability_options("multi_subagent", {"enabled": False, "model": "", "reasoning_effort": "high"})
    with pytest.raises(TypeError, match="max_children must be an integer"):
        _goal_capability_options("multi_subagent", {"enabled": True, "max_children": True})
    with pytest.raises(TypeError, match="allowed_domains must be a string list"):
        _goal_capability_options("multi_subagent", {"enabled": True, "allowed_domains": "code"})
    assert _goal_capability_options(
        "multi_subagent",
        {"enabled": False, "execution_config": "  "},
    ) == {"multi_subagent_feature": "off", "clear_subagent_execution_config": True}

    # explore_harness conflicts & types
    with pytest.raises(ValueError, match="not both"):
        _goal_capability_options("explore_harness", {"mode": "focused", "enabled": True})
    with pytest.raises(TypeError, match="composition mode and scope"):
        _goal_capability_options("explore_harness", {"enabled": True, "composition_scope_id": 12})

    # reward_memory validation
    with pytest.raises(ValueError, match="requires agent_id"):
        _goal_capability_options("reward_memory", {"enabled": True})
    with pytest.raises(ValueError, match="only agent_id and enabled=false"):
        _goal_capability_options("reward_memory", {"agent_id": "writer", "enabled": False, "config_path": "/a"})
    assert _goal_capability_options("reward_memory", {"agent_id": "writer", "enabled": False}) == {
        "reward_memory_agent_id": "writer",
        "clear_reward_memory_config": True,
    }

    # todo_replan_cadence validation & sparse override reset
    with pytest.raises(ValueError, match="stride_strategy"):
        _goal_capability_options("todo_replan_cadence", {"stride_strategy": "fixed"})
    with pytest.raises(ValueError, match="planning_horizon_goal must be a boolean"):
        _goal_capability_options("todo_replan_cadence", {"planning_horizon_goal": "yes"})
    assert _goal_capability_options("todo_replan_cadence", {"planning_horizon_goal": False}) == {
        "execution_planning_horizon_goal": False,
        "clear_execution_yardstick_enabled": True,
        "clear_execution_replan_after_turns": True,
        "clear_execution_replan_after_todos": True,
    }


def test_plan_parsing_server_overrides_and_inspect_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="unknown fields: expected_plan_revision"):
        goal_configuration_plans.parse_goal_configuration_update(
            {"goal_id": "g1", "capability_id": "explore_graph", "configuration": {}, "expected_plan_revision": "rev"},
            execute=False,
        )
    with pytest.raises(ValueError, match="configuration is required"):
        goal_configuration_plans.parse_goal_configuration_update(
            {"goal_id": "g1", "capability_id": "explore_graph"},
            execute=False,
        )
    with pytest.raises(TypeError, match="must be an object or null"):
        goal_configuration_plans.parse_goal_configuration_update(
            {"goal_id": "g1", "capability_id": "explore_graph", "configuration": "invalid"},
            execute=True,
        )
    assert goal_configuration_plans.parse_goal_configuration_update(
        MappingProxyType(
            {
                "goal_id": " g1 ",
                "capability_id": " explore_graph ",
                "configuration": None,
                "expected_plan_revision": "rev-1",
            }
        ),
        execute=True,
    ) == ("g1", "explore_graph", None, "rev-1")

    server_path = SimpleNamespace(
        runtime_controller=SimpleNamespace(codex_home=tmp_path / "home"),
        runtime_root_override=tmp_path / "rt",
    )
    server_str = SimpleNamespace(
        runtime_controller=SimpleNamespace(codex_home=str(tmp_path / "home")),
        runtime_root_override=str(tmp_path / "rt"),
    )
    assert goal_configuration_plans.codex_home_override(server_path) == tmp_path / "home"
    assert goal_configuration_plans.codex_home_override(server_str) == tmp_path / "home"
    assert goal_configuration_plans.codex_home_override(SimpleNamespace()) is None
    assert goal_configuration_plans.runtime_root_override_arg(server_path) == str(tmp_path / "rt")
    assert goal_configuration_plans.runtime_root_override_arg(server_str) == str(tmp_path / "rt")

    # Inspection query validation + observation enrichment + 500 error sanitization.
    bad_handler = _ProbeHandler(
        path=f"{CHAT_GOAL_CONFIGURATION_PATH}?goal_id=a&extra=1",
        registry_path=tmp_path / "registry.json",
    )
    bad_handler._goal_configuration_inspect()
    assert bad_handler.responses[0] == (
        400,
        {"ok": False, "error": "goal configuration request contains unknown fields", "error_code": "invalid_goal_configuration_request"},
    )

    inspect_handler = _ProbeHandler(
        path=f"{CHAT_GOAL_CONFIGURATION_PATH}?goal_id=goal-a",
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "rt",
    )
    monkeypatch.setattr(
        inspect_handler,
        "_goal_configuration_reader",
        lambda: lambda **_kw: {"revision": "rev-0", "capability_catalog": {"capabilities": [{"capability_id": "progress_review"}]}},
    )
    monkeypatch.setattr(
        goal_configuration_api,
        "_public_goal_configuration",
        lambda result, **_kw: dict(result),
    )
    monkeypatch.setattr("loopx.agent_registry.load_goal_from_registry", lambda *_a: {"id": "goal-a"})
    monkeypatch.setattr(
        "loopx.capabilities.progress_review.context.external_progress_review_context",
        lambda *_a: {"summary": "steady progress"},
    )
    inspect_handler._goal_configuration_inspect()
    status, payload = inspect_handler.responses[0]
    assert status == 200
    catalog = payload.get("capability_catalog")
    assert isinstance(catalog, Mapping)
    capabilities = catalog.get("capabilities")
    assert isinstance(capabilities, list) and len(capabilities) == 1
    first_cap = capabilities[0]
    assert isinstance(first_cap, Mapping)
    assert first_cap["observation"] == "steady progress"


def test_partial_write_readback_avoids_duplicate_read_and_sanitizes_failures(tmp_path: Path) -> None:
    stale_public: dict[str, object] = {
        "revision": "rev-after",
        "capability_catalog": {"capabilities": [{"capability_id": "explore_graph", "current": {"enabled": False}}]},
    }
    handler = _ProbeHandler(
        registry_path=tmp_path / "registry.json",
        readback_side_effects=[stale_public],
    )
    handler._send_applied_goal_configuration(
        applied={
            "written": True,
            "changed_fields": "malformed",
            "global_sync": {"readback": "not-a-mapping"},
        },
        capability_id="explore_graph",
        desired_configuration={"enabled": True},
        desired_public={"revision": "rev-desired", "capability_catalog": {"capabilities": []}},
        goal_id="goal-a",
        plan={"plan_revision": "plan-1", "writes_required": True},
    )
    # Only one _read_public_goal_configuration call when readback succeeded and mismatched.
    assert handler.read_calls == 1
    status, receipt = handler.responses[0]
    assert status == 207
    assert receipt["status"] == "partial_write"
    assert receipt["readback_verified"] is False
    assert receipt["changed_fields"] == []
    assert receipt["shared_sync_pending"] is True

    # Non-written readback mismatch raises RuntimeError rather than fabricating partial_write.
    unwritten_handler = _ProbeHandler(
        registry_path=tmp_path / "registry.json",
        readback_side_effects=[stale_public],
    )
    with pytest.raises(RuntimeError, match="readback did not verify"):
        unwritten_handler._send_applied_goal_configuration(
            applied={"written": False},
            capability_id="explore_graph",
            desired_configuration={"enabled": True},
            desired_public={"revision": "rev-desired", "capability_catalog": {"capabilities": []}},
            goal_id="goal-a",
            plan={"plan_revision": "plan-1", "writes_required": True},
        )
