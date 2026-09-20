from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from loopx.configure_goal import configure_goal
from loopx.control_plane.quota.goal_boundary import goal_boundary
from loopx.orchestration import (
    compact_orchestration_policy,
    validate_subagent_model_config,
)


@pytest.fixture
def registry(tmp_path: Path) -> Path:
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(tmp_path / "runtime"),
                "goals": [
                    {
                        "id": "example",
                        "repo": str(tmp_path),
                        "status": "active",
                        "spawn_policy": {
                            "mode": "default",
                            "allowed": False,
                            "max_children": 0,
                        },
                    }
                ],
            }
        )
    )
    return path


def test_cli_persists_model_without_enabling_spawn(registry: Path) -> None:
    command = [
        sys.executable,
        "-m",
        "loopx.cli",
        "--registry",
        str(registry),
        "--format",
        "json",
        "configure-goal",
        "--goal-id",
        "example",
        "--subagent-model",
        "gpt-5.6-luna",
        "--subagent-reasoning-effort",
        "max",
    ]
    before = registry.read_bytes()
    preview = subprocess.run(command, capture_output=True, text=True, check=True)
    assert json.loads(preview.stdout)["dry_run"] is True
    assert registry.read_bytes() == before
    applied = json.loads(
        subprocess.run(
            command + ["--execute"], capture_output=True, text=True, check=True
        ).stdout
    )
    config = {"model": "gpt-5.6-luna", "reasoning_effort": "max"}
    assert applied["after"]["orchestration"]["model_config"] == config
    assert applied["after"]["orchestration"]["spawn_allowed"] is False
    goal = json.loads(registry.read_text())["goals"][0]
    assert goal_boundary(goal)["orchestration"]["model_config"] == config


def test_incremental_update_off_and_clear_preserve_boundaries(registry: Path) -> None:
    configure_goal(
        registry_path=registry,
        goal_id="example",
        subagent_model="gpt-5.6-luna",
        subagent_reasoning_effort="max",
        multi_subagent_feature="enabled",
        max_children=2,
        execute=True,
    )
    changed = configure_goal(
        registry_path=registry,
        goal_id="example",
        subagent_reasoning_effort="high",
        execute=True,
    )
    assert (
        changed["after"]["orchestration"]["model_config"]["reasoning_effort"] == "high"
    )
    off = configure_goal(
        registry_path=registry,
        goal_id="example",
        multi_subagent_feature="off",
        execute=True,
    )
    assert off["after"]["orchestration"]["model_config"]["model"] == "gpt-5.6-luna"
    assert off["after"]["orchestration"]["spawn_allowed"] is False
    cleared = configure_goal(
        registry_path=registry,
        goal_id="example",
        clear_subagent_model_config=True,
        execute=True,
    )
    assert "model_config" not in cleared["after"]["orchestration"]
    assert cleared["after"]["orchestration"]["max_children"] == 0


def test_execution_config_is_local_private_pointer_and_survives_disable(
    registry: Path,
) -> None:
    configured = configure_goal(
        registry_path=registry,
        goal_id="example",
        multi_subagent_feature="enabled",
        max_children=2,
        subagent_execution_config=".loopx/config/delegations.json",
        execute=True,
    )
    assert configured["after"]["orchestration"]["execution_config"] == (
        ".loopx/config/delegations.json"
    )
    disabled = configure_goal(
        registry_path=registry,
        goal_id="example",
        multi_subagent_feature="off",
        execute=True,
    )
    assert disabled["after"]["orchestration"]["execution_config"] == (
        ".loopx/config/delegations.json"
    )
    cleared = configure_goal(
        registry_path=registry,
        goal_id="example",
        clear_subagent_execution_config=True,
        execute=True,
    )
    assert "execution_config" not in cleared["after"]["orchestration"]


@pytest.mark.parametrize(
    "value",
    ["delegations.json", "../delegations.json", "/tmp/delegations.json", ".loopx/config/delegations.yml"],
)
def test_execution_config_rejects_non_private_pointer(
    registry: Path, value: str
) -> None:
    before = registry.read_bytes()
    with pytest.raises(ValueError, match="repo-relative JSON path"):
        configure_goal(
            registry_path=registry,
            goal_id="example",
            subagent_execution_config=value,
            execute=True,
        )
    assert registry.read_bytes() == before


@pytest.mark.parametrize(
    "kwargs",
    [
        {"subagent_reasoning_effort": "max"},
        {"subagent_model": ""},
        {"subagent_model": "model\ninjected"},
        {"subagent_model": "example", "subagent_reasoning_effort": "extreme"},
        {"subagent_model": "example", "clear_subagent_model_config": True},
    ],
)
def test_invalid_configuration_does_not_write(registry: Path, kwargs: dict) -> None:
    before = registry.read_bytes()
    with pytest.raises(ValueError):
        configure_goal(
            registry_path=registry, goal_id="example", execute=True, **kwargs
        )
    assert registry.read_bytes() == before


def test_registry_model_overrides_stale_asset_and_clear_does_not_resurrect() -> None:
    stale = {
        "orchestration": {
            "mode": "multi_subagent",
            "spawn_allowed": True,
            "max_children": 2,
            "model_config": {"model": "old-model"},
        }
    }
    goal = {
        "id": "example",
        "spawn_policy": {
            "allowed": True,
            "max_children": 2,
            "model_config": {"model": "gpt-5.6-luna", "reasoning_effort": "max"},
        },
        "project_asset": stale,
    }
    assert (
        goal_boundary(goal)["orchestration"]["model_config"]
        == goal["spawn_policy"]["model_config"]
    )
    del goal["spawn_policy"]["model_config"]
    assert "model_config" not in goal_boundary(goal)["orchestration"]


def test_unconfigured_shape_and_unrecognized_model_availability() -> None:
    assert compact_orchestration_policy(None) == {
        "mode": "default",
        "spawn_allowed": False,
        "max_children": 0,
    }
    # Host catalog owns availability; LoopX does not silently replace unknown models.
    assert validate_subagent_model_config({"model": "provider/future-model"}) == {
        "model": "provider/future-model"
    }
    with pytest.raises(ValueError):
        validate_subagent_model_config({"model": "example", "fallback": "parent"})


def test_capability_editor_model_roundtrip_and_clear(registry: Path) -> None:
    from loopx.chat_goal_configuration_api import _goal_capability_options

    for config, expected in [
        (
            {"enabled": False, "model": "gpt-5.6-luna", "reasoning_effort": "max"},
            {"model": "gpt-5.6-luna", "reasoning_effort": "max"},
        ),
        (
            {"enabled": False, "model": "gpt-5.6-luna", "reasoning_effort": ""},
            {"model": "gpt-5.6-luna"},
        ),
        ({"enabled": False, "model": "", "reasoning_effort": ""}, None),
    ]:
        result = configure_goal(
            registry_path=registry,
            goal_id="example",
            execute=True,
            **_goal_capability_options("multi_subagent", config),
        )
        assert result["after"]["orchestration"].get("model_config") == expected
        assert result["after"]["orchestration"]["spawn_allowed"] is False
    with pytest.raises(ValueError):
        _goal_capability_options(
            "multi_subagent", {"enabled": False, "model": "", "reasoning_effort": "max"}
        )


def test_capability_editor_requests_one_click_codex_capacity_alignment() -> None:
    from loopx.chat_goal_configuration_api import _goal_capability_options

    options = _goal_capability_options(
        "multi_subagent",
        {"enabled": True, "max_children": 6, "allowed_domains": []},
    )

    assert options["align_codex_subagent_capacity"] is True
    assert options["max_children"] == 6


@pytest.mark.parametrize("config", [
    {"model": 42},
    {"model": "example", "reasoning_effort": True},
])
def test_capability_model_rejects_non_string_inputs(config: dict) -> None:
    from loopx.chat_goal_configuration_api import _goal_capability_options

    with pytest.raises(ValueError, match="must be strings"):
        _goal_capability_options("multi_subagent", {"enabled": False, **config})


def test_goal_drawer_preview_binding_and_model_roundtrip(registry: Path) -> None:
    from types import SimpleNamespace

    from loopx.chat_goal_subagent_api import GoalSubagentConfigurationRequestMixin

    class Handler(GoalSubagentConfigurationRequestMixin):
        def __init__(self) -> None:
            self.server = SimpleNamespace(
                registry_path=registry,
                runtime_root_override=str(registry.parent / "runtime"),
            )
            self.body = {}
            self.response = {}

        def _registry_and_goal(self, goal_id):
            payload = json.loads(registry.read_text())
            return payload, next(g for g in payload["goals"] if g["id"] == goal_id)

        def _read_json(self):
            return self.body

        def _send_json(self, payload, *, status=200):
            self.response = {"status": status, **payload}

        def _send_error(self, message, **kwargs):
            self.response = {"error": message, **kwargs}

    handler = Handler()
    base = {"goal_id": "example", "enabled": False}
    preference = {"model": "gpt-5.6-luna", "reasoning_effort": "max"}
    # A preview cannot be reused for a different model; no partial write occurs.
    handler.body = {**base, "model_config": preference}
    before = registry.read_bytes()
    handler._goal_subagent_configuration(apply=False)
    assert handler.response["status"] == 200
    assert registry.read_bytes() == before
    preview_id = handler.response["preview_id"]
    handler.body = {**base, "model_config": None, "preview_id": preview_id}
    handler._goal_subagent_configuration(apply=True)
    assert handler.response["status"] == 409
    assert registry.read_bytes() == before

    # Exercise the actual API owner and file-backed writer for set, preserve,
    # effort removal and clear. Execution stays off throughout.
    for fields, expected in [
        ({"model_config": preference}, preference),
        ({}, preference),
        ({"model_config": {"model": "gpt-5.6-luna"}}, {"model": "gpt-5.6-luna"}),
        ({"model_config": None}, None),
    ]:
        handler.body = {**base, **fields}
        handler._goal_subagent_configuration(apply=False)
        assert handler.response["status"] == 200
        handler.body["preview_id"] = handler.response["preview_id"]
        handler._goal_subagent_configuration(apply=True)
        assert handler.response["status"] == 200, handler.response
        actual = json.loads(registry.read_text())["goals"][0]["spawn_policy"]
        assert actual.get("model_config") == expected
        assert actual["allowed"] is False

    handler.body = {
        **base,
        "execution_config": ".loopx/config/delegations.json",
    }
    handler._goal_subagent_configuration(apply=False)
    assert handler.response["after"]["orchestration"]["execution_config"] == (
        ".loopx/config/delegations.json"
    )
    handler.body["preview_id"] = handler.response["preview_id"]
    handler._goal_subagent_configuration(apply=True)
    assert handler.response["status"] == 200
    assert json.loads(registry.read_text())["goals"][0]["spawn_policy"][
        "execution_config"
    ] == ".loopx/config/delegations.json"
