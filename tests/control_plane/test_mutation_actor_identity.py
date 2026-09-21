from pathlib import Path

import pytest

from loopx.control_plane.actor_identity import normalize_owner_controller_actor
from loopx.control_plane.goals.activation_service import set_goal_activation_state
from loopx.control_plane.runtime.run_compaction import compact_human_reward
from loopx.feedback import append_human_reward
from scripts.codex_app_apply_rrule import _parse_args


def test_mutation_entrypoints_fail_before_io_without_an_explicit_actor(
    tmp_path: Path,
) -> None:
    missing_registry = tmp_path / "missing-registry.json"

    with pytest.raises(ValueError, match="actor kind is required"):
        set_goal_activation_state(
            registry_path=missing_registry,
            goal_id="fixture-goal",
            state="stopped",
            execute=True,
        )

    with pytest.raises(ValueError, match="actor kind is required"):
        append_human_reward(
            registry_path=missing_registry,
            runtime_root_override=None,
            goal_id="fixture-goal",
            run_generated_at=None,
            reward={},
        )

    with pytest.raises(SystemExit) as scheduler_error:
        _parse_args([])
    assert scheduler_error.value.code == 2


def test_owner_controller_actor_is_typed_and_optional_for_read_only_preview() -> None:
    assert normalize_owner_controller_actor(None, required=False) is None
    assert normalize_owner_controller_actor("owner", required=True).value == "owner"
    assert (
        normalize_owner_controller_actor("controller", required=True).value
        == "controller"
    )
    with pytest.raises(ValueError, match="must be owner or controller"):
        normalize_owner_controller_actor("agent", required=True)

    assert compact_human_reward(
        {
            "recorded_at": "2026-09-20T00:00:00Z",
            "actor_kind": "owner",
            "decision": "continue",
            "reward": "positive",
            "reason_summary": "Validated result.",
        }
    )["actor_kind"] == "owner"
