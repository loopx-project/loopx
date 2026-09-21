from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx.cli import main
from loopx.configure_goal import configure_goal


GOAL_ID = "local-authority-shadow-config"
REPO_ROOT = Path(__file__).resolve().parents[2]


def _registry(tmp_path: Path) -> Path:
    state = tmp_path / "ACTIVE_GOAL_STATE.md"
    state.write_text(
        "---\n"
        f"goal_id: {GOAL_ID}\n"
        "handoff_mode: hard_lease\n"
        "---\n\n"
        "## Agent Todo\n\n",
        encoding="utf-8",
    )
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "goals": [
                    {
                        "id": GOAL_ID,
                        "repo": str(tmp_path),
                        "state_file": state.name,
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": ["agent-a", "agent-b"],
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return registry


def test_configure_goal_enables_and_clears_closed_file_shadow_config(
    tmp_path: Path,
) -> None:
    registry = _registry(tmp_path)

    preview = configure_goal(
        registry_path=registry,
        goal_id=GOAL_ID,
        local_authority_shadow_file=True,
        execute=False,
    )

    assert preview["changed_fields"] == ["local_authority_shadow"]
    assert preview["before"]["local_authority_shadow"] == {
        "enabled": False,
        "mode": None,
        "status": "disabled",
    }
    assert preview["after"]["local_authority_shadow"] == {
        "enabled": True,
        "mode": "file_one_way",
        "status": "enabled",
    }

    applied = configure_goal(
        registry_path=registry,
        goal_id=GOAL_ID,
        local_authority_shadow_file=True,
        execute=True,
    )
    assert applied["written"] is True
    goal = json.loads(registry.read_text(encoding="utf-8"))["goals"][0]
    assert goal["coordination"]["authority_shadow"] == {
        "schema_version": "loopx_local_authority_shadow_config_v0",
        "mode": "file_one_way",
    }
    assert goal["coordination"]["agent_model"] == "peer_v1"

    repeated = configure_goal(
        registry_path=registry,
        goal_id=GOAL_ID,
        local_authority_shadow_file=True,
        execute=True,
    )
    assert repeated["written"] is False

    cleared = configure_goal(
        registry_path=registry,
        goal_id=GOAL_ID,
        clear_local_authority_shadow=True,
        execute=True,
    )
    assert cleared["changed_fields"] == ["local_authority_shadow"]
    goal = json.loads(registry.read_text(encoding="utf-8"))["goals"][0]
    assert "authority_shadow" not in goal["coordination"]
    assert goal["coordination"]["registered_agents"] == ["agent-a", "agent-b"]


def test_configure_goal_rejects_enable_and_clear_in_one_operation(
    tmp_path: Path,
) -> None:
    registry = _registry(tmp_path)
    with pytest.raises(ValueError, match="cannot be combined"):
        configure_goal(
            registry_path=registry,
            goal_id=GOAL_ID,
            local_authority_shadow_file=True,
            clear_local_authority_shadow=True,
        )


def test_configure_goal_exposes_transaction_bound_runtime_shadow_separately(
    tmp_path: Path,
) -> None:
    registry = _registry(tmp_path)

    preview = configure_goal(
        registry_path=registry,
        goal_id=GOAL_ID,
        coordination_runtime_shadow_file=True,
        execute=False,
    )
    assert preview["changed_fields"] == ["coordination_runtime_shadow"]
    assert preview["before"]["coordination_runtime_shadow"] == {
        "enabled": False,
        "provider": None,
        "status": "configuration_absent",
    }
    assert preview["after"]["coordination_runtime_shadow"] == {
        "enabled": True,
        "provider": "file_v0",
        "status": "enabled",
    }
    assert preview["after"]["local_authority_shadow"]["enabled"] is False

    applied = configure_goal(
        registry_path=registry,
        goal_id=GOAL_ID,
        coordination_runtime_shadow_file=True,
        execute=True,
    )
    assert applied["written"] is True
    goal = json.loads(registry.read_text(encoding="utf-8"))["goals"][0]
    assert goal["coordination"]["runtime_shadow"] == {
        "enabled": True,
        "schema_version": "loopx_coordination_runtime_shadow_config_v0",
        "provider": "file_v0",
    }
    assert "authority_shadow" not in goal["coordination"]

    feature = next(
        item
        for item in applied["configuration_catalog"]["features"]
        if item["feature_id"] == "coordination_runtime_shadow"
    )
    assert feature["current"]["enabled"] is True
    assert feature["commands"]["apply_enable"].endswith(
        "--coordination-runtime-shadow-file --execute"
    )

    cleared = configure_goal(
        registry_path=registry,
        goal_id=GOAL_ID,
        clear_coordination_runtime_shadow=True,
        execute=True,
    )
    assert cleared["changed_fields"] == ["coordination_runtime_shadow"]
    goal = json.loads(registry.read_text(encoding="utf-8"))["goals"][0]
    assert "runtime_shadow" not in goal["coordination"]


def test_configure_goal_cli_exposes_default_off_shadow_boundary(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    registry = _registry(tmp_path)

    exit_code = main(
        [
            "--registry",
            str(registry),
            "--runtime-root",
            str(tmp_path / "runtime"),
            "--format",
            "json",
            "configure-goal",
            "--goal-id",
            GOAL_ID,
            "--local-authority-shadow-file",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["written"] is False
    assert payload["after"]["local_authority_shadow"]["enabled"] is True
    feature = next(
        item
        for item in payload["configuration_catalog"]["features"]
        if item["feature_id"] == "local_authority_shadow"
    )
    assert feature["display_name"] == "Local post-commit authority observation"
    assert feature["availability"] == "experimental_opt_in"
    assert "parity" not in feature["consider_when"].lower()
    assert "post-commit snapshot" in feature["effect"]
    assert feature["does_not"] == [
        "read the candidate for lifecycle decisions",
        "write candidate state back into Markdown or task-lease files",
        "promote shared authority or fence legacy writers",
        "bind the snapshot to the exact primary transaction",
        "guarantee delivery through a durable outbox",
        "compare source and candidate or issue a parity verdict",
    ]
    assert feature["commands"]["apply_disable"].endswith(
        "--clear-local-authority-shadow --execute"
    )
    assert "authority_shadow" not in json.loads(
        registry.read_text(encoding="utf-8")
    )["goals"][0]["coordination"]


def test_rfc_disambiguates_historical_and_current_stage_numbering() -> None:
    english = (
        REPO_ROOT
        / "docs/architecture/rfcs/shared-goal-authority-state-provider-v0.md"
    ).read_text(encoding="utf-8")
    chinese = (
        REPO_ROOT
        / "docs/architecture/rfcs/shared-goal-authority-state-provider-v0.zh-CN.md"
    ).read_text(encoding="utf-8")

    assert "historical #3669 implementation sequence" in english
    assert "part of the Stage 0 reference foundation" in english
    assert "not the Stage 3 remote-shadow phase in Section 11" in english
    assert "#3669 历史实施序列" in chinese
    assert "属于 Stage 0 reference foundation" in chinese
    assert "不是第 11 节的 Stage 3 远端 shadow 阶段" in chinese
