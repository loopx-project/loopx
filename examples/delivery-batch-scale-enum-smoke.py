#!/usr/bin/env python3
"""Smoke-test structured delivery_batch_scale enum enforcement."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.control_plane.work_items.delivery_batch_scale import (  # noqa: E402
    DELIVERY_BATCH_SCALE_ALIASES,
    DELIVERY_BATCH_SCALE_CHOICES,
    DELIVERY_BATCH_SCALE_INPUT_CHOICES,
    SMALL_DELIVERY_BATCH_SCALES,
    UNKNOWN_DELIVERY_BATCH_SCALE,
    DeliveryBatchScale,
    delivery_batch_scale_value,
    normalize_delivery_batch_scale,
    require_delivery_batch_scale,
)
from loopx.control_plane.work_items.delivery_history import (  # noqa: E402
    project_delivery_history,
)
from loopx.state_refresh import refresh_state_run  # noqa: E402


GOAL_ID = "delivery-batch-scale-enum-fixture"


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_fixture(root: Path) -> tuple[Path, Path]:
    project = root / "project"
    runtime = root / "runtime"
    state_file = Path(".codex/goals") / GOAL_ID / "ACTIVE_GOAL_STATE.md"
    (project / state_file).parent.mkdir(parents=True, exist_ok=True)
    (project / state_file).write_text(
        "---\n"
        "status: active\n"
        "---\n\n"
        "# Delivery Batch Scale Fixture\n\n"
        "## Agent Todo\n\n"
        "- [ ] Advance one bounded implementation batch.\n\n"
        "## Next Action\n\n"
        "- Advance one bounded implementation batch.\n",
        encoding="utf-8",
    )
    registry_path = project / ".loopx" / "registry.json"
    write_json(
        registry_path,
        {
            "schema_version": 1,
            "common_runtime_root": str(runtime),
            "goals": [
                {
                    "id": GOAL_ID,
                    "domain": "enum-fixture",
                    "status": "active",
                    "repo": str(project),
                    "state_file": str(state_file),
                    "adapter": {"kind": "harness_self_improvement", "status": "connected"},
                    "quota": {"compute": 1.0, "window_hours": 24},
                }
            ],
        },
    )
    from loopx.control_plane.testing.canary_harness import import_fixture_authority
    import_fixture_authority(registry_path, GOAL_ID, provider="sqlite")
    return registry_path, runtime


def assert_enum_sets() -> None:
    assert DELIVERY_BATCH_SCALE_CHOICES == (
        "test_only",
        "single_surface",
        "multi_surface",
        "implementation",
    )
    assert DELIVERY_BATCH_SCALE_ALIASES == {
        "single_segment": DeliveryBatchScale.SINGLE_SURFACE,
        "bounded_segment": DeliveryBatchScale.SINGLE_SURFACE,
    }
    assert DELIVERY_BATCH_SCALE_INPUT_CHOICES == DELIVERY_BATCH_SCALE_CHOICES
    assert require_delivery_batch_scale("multi_surface") == DeliveryBatchScale.MULTI_SURFACE
    for alias in DELIVERY_BATCH_SCALE_ALIASES:
        try:
            require_delivery_batch_scale(alias)
        except ValueError as exc:
            assert "read-only and ambiguous for new writes" in str(exc)
        else:
            raise AssertionError(f"legacy delivery scale alias accepted for a new write: {alias}")
    assert normalize_delivery_batch_scale("single_segment") == DeliveryBatchScale.SINGLE_SURFACE
    assert normalize_delivery_batch_scale("bounded_segment") == DeliveryBatchScale.SINGLE_SURFACE
    assert delivery_batch_scale_value("single_segment") == DeliveryBatchScale.SINGLE_SURFACE.value
    assert normalize_delivery_batch_scale("unknown") is None
    assert SMALL_DELIVERY_BATCH_SCALES == {
        DeliveryBatchScale.TEST_ONLY,
        DeliveryBatchScale.SINGLE_SURFACE,
    }
    try:
        require_delivery_batch_scale("implementation_plus_validation")
    except ValueError as exc:
        assert "delivery_batch_scale must be one of:" in str(exc)
    else:
        raise AssertionError("invalid delivery batch scale accepted")


def assert_refresh_state_enforces_enum(registry_path: Path) -> None:
    payload = refresh_state_run(
        registry_path=registry_path,
        runtime_root_override=None,
        goal_id=GOAL_ID,
        project=None,
        state_file=None,
        classification="enum_fixture_progress",
        recommended_action="Advance one bounded implementation batch.",
        delivery_batch_scale=DeliveryBatchScale.IMPLEMENTATION.value,
        dry_run=True,
        sync_global=False,
    )
    assert payload["delivery_batch_scale"] == DeliveryBatchScale.IMPLEMENTATION.value, payload

    try:
        refresh_state_run(
            registry_path=registry_path,
            runtime_root_override=None,
            goal_id=GOAL_ID,
            project=None,
            state_file=None,
            classification="enum_fixture_alias",
            recommended_action="Advance one bounded implementation batch.",
            delivery_batch_scale="bounded_segment",
            dry_run=True,
            sync_global=False,
        )
    except ValueError as exc:
        assert "read-only and ambiguous for new writes" in str(exc)
    else:
        raise AssertionError("refresh-state accepted an ambiguous legacy delivery scale alias")

    try:
        refresh_state_run(
            registry_path=registry_path,
            runtime_root_override=None,
            goal_id=GOAL_ID,
            project=None,
            state_file=None,
            classification="enum_fixture_invalid",
            recommended_action="Advance one bounded implementation batch.",
            delivery_batch_scale="implementation_plus_validation",
            dry_run=True,
            sync_global=False,
        )
    except ValueError as exc:
        assert "delivery_batch_scale must be one of:" in str(exc)
    else:
        raise AssertionError("refresh-state accepted invalid delivery batch scale")

    cli_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--registry",
            str(registry_path),
            "--format",
            "json",
            "refresh-state",
            "--goal-id",
            GOAL_ID,
            "--delivery-batch-scale",
            "implementation_plus_validation",
            "--dry-run",
            "--no-global-sync",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert cli_result.returncode != 0, cli_result.stdout
    assert "invalid choice" in cli_result.stderr, cli_result.stderr

    alias_cli_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--registry",
            str(registry_path),
            "--format",
            "json",
            "refresh-state",
            "--goal-id",
            GOAL_ID,
            "--delivery-batch-scale",
            "single_segment",
            "--dry-run",
            "--no-global-sync",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert alias_cli_result.returncode != 0, alias_cli_result.stdout
    assert "invalid choice" in alias_cli_result.stderr, alias_cli_result.stderr


def projected_delivery_batch_scale(run: dict[str, object]) -> str:
    projection = project_delivery_history([run])
    return str(projection["runs"][0]["delivery_batch_scale"])


def assert_delivery_history_uses_enum_not_raw_value() -> None:
    assert (
        projected_delivery_batch_scale(
            {
                "classification": "runner_batch_fixture",
                "delivery_batch_scale": DeliveryBatchScale.MULTI_SURFACE.value,
            }
        )
        == DeliveryBatchScale.MULTI_SURFACE.value
    )
    assert (
        projected_delivery_batch_scale(
            {
                "classification": "runner_batch_fixture",
                "delivery_batch_scale": "batch_plus_raw_logs",
            }
        )
        == UNKNOWN_DELIVERY_BATCH_SCALE
    )
    assert (
        projected_delivery_batch_scale({"classification": "owner_handoff_consumer_test"})
        == UNKNOWN_DELIVERY_BATCH_SCALE
    )
    assert (
        projected_delivery_batch_scale(
            {
                "classification": "owner_handoff_consumer_test",
                "delivery_batch_scale": DeliveryBatchScale.TEST_ONLY.value,
            }
        )
        == DeliveryBatchScale.TEST_ONLY.value
    )


def main() -> int:
    assert_enum_sets()
    with tempfile.TemporaryDirectory(prefix="delivery-batch-scale-enum-") as tmp:
        registry_path, _runtime = write_fixture(Path(tmp))
        assert_refresh_state_enforces_enum(registry_path)
    assert_delivery_history_uses_enum_not_raw_value()
    print("delivery batch scale enum smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
