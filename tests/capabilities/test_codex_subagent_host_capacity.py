from __future__ import annotations

from pathlib import Path

import pytest

from loopx.capabilities.multi_subagent.codex_host_capacity import (
    apply_codex_subagent_capacity,
    plan_codex_subagent_capacity,
)
from loopx.control_plane.goals.configure_goal_service import (
    configure_goal_with_global_sync,
)
from loopx.chat_goal_subagent_api import GoalSubagentConfigurationRequestMixin


def _apply(home: Path, required: int) -> dict:
    plan = plan_codex_subagent_capacity(required, home=home)
    return apply_codex_subagent_capacity(
        required,
        home=home,
        expected_source_sha256=plan["source_sha256"],
    )


def test_absent_config_is_created_with_canonical_child_limit(tmp_path: Path) -> None:
    preview = plan_codex_subagent_capacity(6, home=tmp_path)
    assert preview["status"] == "implicit_default_unknown"
    assert preview["counts_main_thread"] is False
    assert preview["write_required"] is True

    receipt = _apply(tmp_path, 6)

    assert receipt["status"] == "updated"
    assert receipt["configured_children"] == 6
    assert receipt["readback_verified"] is True
    assert receipt["new_session_required"] is True
    assert (tmp_path / "config.toml").read_text(encoding="utf-8") == (
        "[agents]\nmax_concurrent_threads_per_session = 6\n"
    )


def test_legacy_alias_is_migrated_without_changing_unrelated_fields(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        "model = \"gpt-5.6-sol\"\n\n"
        "[agents]\n"
        "enabled = true\n"
        "max_threads = 3 # legacy capacity\n"
        "default_subagent_model = \"gpt-5.6-luna\"\n",
        encoding="utf-8",
    )

    receipt = _apply(tmp_path, 6)
    source = config.read_text(encoding="utf-8")

    assert receipt["previous_configured_children"] == 3
    assert "max_concurrent_threads_per_session = 6 # legacy capacity" in source
    assert "max_threads" not in source
    assert 'model = "gpt-5.6-sol"' in source
    assert 'default_subagent_model = "gpt-5.6-luna"' in source
    assert receipt["backup_path"]


def test_canonical_key_precedes_a_higher_legacy_alias_and_apply_preserves_both(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        "[agents]\n"
        "max_concurrent_threads_per_session = 4\n"
        "max_threads = 8\n",
        encoding="utf-8",
    )

    preview = plan_codex_subagent_capacity(6, home=tmp_path)

    assert preview["configured_children"] == 4
    assert preview["configured_source_key"] == "max_concurrent_threads_per_session"
    assert preview["legacy_alias_present"] is True
    assert preview["status"] == "explicit_shortfall"

    receipt = apply_codex_subagent_capacity(
        6,
        home=tmp_path,
        expected_source_sha256=preview["source_sha256"],
    )

    assert receipt["configured_children"] == 8
    assert receipt["legacy_alias_present"] is False
    assert config.read_text(encoding="utf-8") == (
        "[agents]\nmax_concurrent_threads_per_session = 8\n"
    )


def test_sufficient_legacy_alias_remains_a_compatible_no_op(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    source = "[agents]\nmax_threads = 8\n"
    config.write_text(source, encoding="utf-8")

    preview = plan_codex_subagent_capacity(6, home=tmp_path)
    receipt = apply_codex_subagent_capacity(
        6,
        home=tmp_path,
        expected_source_sha256=preview["source_sha256"],
    )

    assert preview["configured_source_key"] == "max_threads"
    assert receipt["status"] == "already_sufficient"
    assert receipt["written"] is False
    assert config.read_text(encoding="utf-8") == source


def test_existing_higher_limit_is_never_lowered(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        "[agents]\nmax_concurrent_threads_per_session = 12\n",
        encoding="utf-8",
    )
    before = config.read_bytes()
    preview = plan_codex_subagent_capacity(6, home=tmp_path)

    receipt = apply_codex_subagent_capacity(
        6,
        home=tmp_path,
        expected_source_sha256=preview["source_sha256"],
    )

    assert receipt["status"] == "already_sufficient"
    assert receipt["written"] is False
    assert receipt["configured_children"] == 12
    assert config.read_bytes() == before


def test_stale_preview_refuses_to_overwrite(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text("[agents]\nenabled = true\n", encoding="utf-8")
    preview = plan_codex_subagent_capacity(6, home=tmp_path)
    config.write_text("[agents]\nenabled = false\n", encoding="utf-8")

    with pytest.raises(ValueError, match="changed after preview"):
        apply_codex_subagent_capacity(
            6,
            home=tmp_path,
            expected_source_sha256=preview["source_sha256"],
        )
    assert config.read_text(encoding="utf-8") == "[agents]\nenabled = false\n"


def test_disabled_goal_does_not_read_or_create_codex_config(tmp_path: Path) -> None:
    preview = plan_codex_subagent_capacity(0, home=tmp_path)

    assert preview["status"] == "not_required"
    assert preview["write_required"] is False
    assert not (tmp_path / "config.toml").exists()


def test_browser_projection_omits_host_paths_hashes_and_backups() -> None:
    projection = GoalSubagentConfigurationRequestMixin._public_codex_host_capacity(
        {
            "codex_host_capacity": {
                "status": "updated",
                "required_children": 6,
                "configured_children": 6,
                "written": True,
                "config_path": "/private/codex/config.toml",
                "source_sha256": "local-hash-placeholder",
                "backup_path": "/private/codex/config.toml.bak",
                "plan_before_apply": {"config_path": "/private/codex/config.toml"},
            }
        }
    )

    assert projection == {
        "status": "updated",
        "required_children": 6,
        "configured_children": 6,
        "written": True,
    }


def test_managed_chat_home_precedes_generic_codex_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    managed = tmp_path / "managed"
    generic = tmp_path / "generic"
    managed.mkdir()
    generic.mkdir()
    (managed / "config.toml").write_text(
        "[agents]\nmax_concurrent_threads_per_session = 8\n",
        encoding="utf-8",
    )
    (generic / "config.toml").write_text(
        "[agents]\nmax_concurrent_threads_per_session = 2\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LOOPX_CHAT_CODEX_HOME", str(managed))
    monkeypatch.setenv("CODEX_HOME", str(generic))

    preview = plan_codex_subagent_capacity(6)

    assert preview["status"] == "explicit_sufficient"
    assert preview["configured_children"] == 8
    assert preview["config_path"] == str(managed / "config.toml")


def test_goal_apply_and_host_alignment_share_one_preview_locked_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    registry = tmp_path / "project" / ".loopx" / "registry.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(tmp_path / "source-runtime"),
                "goals": [
                    {
                        "id": "capacity-goal",
                        "repo": str(tmp_path / "project"),
                        "status": "active",
                        "spawn_policy": {
                            "allowed": False,
                            "max_children": 0,
                            "mode": "default",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    codex_home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    options = {
        "registry_path": registry,
        "goal_id": "capacity-goal",
        "runtime_root_override": str(tmp_path / "shared-runtime"),
        "multi_subagent_feature": "enabled",
        "max_children": 6,
        "align_codex_subagent_capacity": True,
    }

    preview = configure_goal_with_global_sync(
        **options,
        execute=False,
        codex_host_capacity_planner=plan_codex_subagent_capacity,
        codex_host_capacity_applier=apply_codex_subagent_capacity,
    )
    assert preview["changed"] is True
    assert preview["goal_configuration_changed"] is True
    assert preview["codex_host_capacity"]["write_required"] is True
    assert not (codex_home / "config.toml").exists()

    applied = configure_goal_with_global_sync(
        **options,
        execute=True,
        codex_host_capacity_planner=plan_codex_subagent_capacity,
        codex_host_capacity_applier=apply_codex_subagent_capacity,
    )
    assert applied["ok"] is True
    assert applied["written"] is True
    assert applied["global_sync"]["readback"]["verified"] is True
    assert applied["codex_host_capacity"]["written"] is True
    assert applied["codex_host_capacity"]["readback_verified"] is True
    assert applied["codex_host_capacity"]["new_session_required"] is True
    assert "max_concurrent_threads_per_session = 6" in (
        codex_home / "config.toml"
    ).read_text(encoding="utf-8")

    settled = configure_goal_with_global_sync(
        **options,
        execute=False,
        codex_host_capacity_planner=plan_codex_subagent_capacity,
        codex_host_capacity_applier=apply_codex_subagent_capacity,
    )
    assert settled["changed"] is False
    assert settled["goal_configuration_changed"] is False
    assert settled["codex_host_capacity"]["status"] == "explicit_sufficient"


def test_host_failure_after_goal_write_returns_truthful_partial_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json
    registry = tmp_path / "project" / ".loopx" / "registry.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(tmp_path / "source-runtime"),
                "goals": [
                    {
                        "id": "capacity-goal",
                        "repo": str(tmp_path / "project"),
                        "status": "active",
                        "spawn_policy": {
                            "allowed": False,
                            "max_children": 0,
                            "mode": "default",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    codex_home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    def reject_host_write(*_args, **_kwargs):
        raise ValueError("simulated concurrent Codex config edit")

    result = configure_goal_with_global_sync(
        registry_path=registry,
        goal_id="capacity-goal",
        runtime_root_override=str(tmp_path / "shared-runtime"),
        multi_subagent_feature="enabled",
        max_children=6,
        align_codex_subagent_capacity=True,
        codex_host_capacity_planner=plan_codex_subagent_capacity,
        codex_host_capacity_applier=reject_host_write,
        execute=True,
    )

    assert result["ok"] is False
    assert result["partial_write"] is True
    assert result["goal_configuration_changed"] is True
    assert result["codex_host_capacity"]["status"] == "apply_failed"
    assert result["codex_host_capacity"]["readback_verified"] is False
    assert not (codex_home / "config.toml").exists()
    goal = json.loads(registry.read_text(encoding="utf-8"))["goals"][0]
    assert goal["spawn_policy"]["max_children"] == 6
