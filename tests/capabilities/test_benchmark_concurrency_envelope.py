from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from loopx.capabilities.benchmark_toolkit import (
    BENCHMARK_RESOURCE_HEADROOM_RECEIPT_SCHEMA_VERSION,
    admit_benchmark_case,
    build_benchmark_concurrency_config,
    build_benchmark_concurrency_status,
    configure_benchmark_concurrency_envelope,
    default_benchmark_concurrency_envelope_path,
    normalize_benchmark_resource_headroom_receipt,
    read_benchmark_concurrency_envelope,
    release_benchmark_case,
)
from loopx.cli import main
from tests.source_cli import run_source_cli


def _connected_goal_registries(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "canonical-project"
    source_registry = project / ".loopx" / "registry.json"
    source_registry.parent.mkdir(parents=True)
    goal = {
        "id": "fixture-goal",
        "objective": "Run a public benchmark study.",
        "repo": str(project),
    }
    source_registry.write_text(
        json.dumps(
            {
                "common_runtime_root": str(tmp_path / "source-runtime"),
                "goals": [goal],
            }
        ),
        encoding="utf-8",
    )
    global_registry = tmp_path / "shared-runtime" / "registry.global.json"
    global_registry.parent.mkdir(parents=True)
    global_registry.write_text(
        json.dumps(
            {
                "registry_role": "global-local",
                "common_runtime_root": str(global_registry.parent),
                "goals": [{**goal, "source_registry": str(source_registry)}],
            }
        ),
        encoding="utf-8",
    )
    return project, global_registry


def _configure(
    path: Path,
    *,
    total: int = 8,
    target: int | None = None,
    baseline: int = 7,
    require_resource_headroom_receipt: bool = False,
) -> None:
    result = configure_benchmark_concurrency_envelope(
        path,
        build_benchmark_concurrency_config(
            max_active_cases=total,
            target_active_cases=target,
            max_baseline_cases=baseline,
            max_test_cases=total,
            reserved_test_cases=1,
            require_resource_headroom_receipt=require_resource_headroom_receipt,
        ),
        execute=True,
        observed_at="2026-08-18T07:00:00Z",
    )
    assert result["ok"] is True
    assert result["write_performed"] is True


def _resource_headroom_receipt(
    *,
    state: str = "sufficient",
    observed_at: str = "2026-08-18T07:00:00Z",
    expires_at: str = "2026-08-18T07:10:00Z",
) -> dict[str, object]:
    return {
        "schema_version": BENCHMARK_RESOURCE_HEADROOM_RECEIPT_SCHEMA_VERSION,
        "observed_at": observed_at,
        "expires_at": expires_at,
        "checks": [
            {"kind": "temporary_storage", "state": state},
            {"kind": "process_capacity", "state": "sufficient"},
        ],
    }


def test_config_rejects_role_caps_outside_total() -> None:
    with pytest.raises(ValueError, match="max_baseline_cases"):
        build_benchmark_concurrency_config(
            max_active_cases=4,
            max_baseline_cases=5,
        )
    with pytest.raises(ValueError, match="reserved_test_cases"):
        build_benchmark_concurrency_config(
            max_active_cases=4,
            max_test_cases=1,
            reserved_test_cases=2,
        )
    with pytest.raises(ValueError, match="target_active_cases"):
        build_benchmark_concurrency_config(
            max_active_cases=4,
            target_active_cases=5,
        )
    with pytest.raises(ValueError, match="combined baseline and test capacity"):
        build_benchmark_concurrency_config(
            max_active_cases=8,
            target_active_cases=7,
            max_baseline_cases=3,
            max_test_cases=3,
        )


def test_status_hints_backfill_to_target_without_weakening_hard_cap(
    tmp_path: Path,
) -> None:
    path = tmp_path / "concurrency-envelope.json"
    _configure(path, total=8, target=4, baseline=7)

    initial = build_benchmark_concurrency_status(
        read_benchmark_concurrency_envelope(path)
    )
    assert initial["config"]["max_active_cases"] == 8
    assert initial["target_occupancy"] == {
        "active_cases": 4,
        "current_cases": 0,
        "missing_cases": 4,
        "excess_cases": 0,
        "underfilled": True,
        "above_target": False,
        "utilization": 0.0,
    }
    assert initial["backfill_hint"]["preferred_arm_group"] == "test"
    assert initial["runtime_reconciliation_hint"] == {
        "required_on_transition_or_cadence": True,
        "reason_code": "admission_ledger_is_not_runtime_liveness",
        "active_count_source": "admission_ledger",
        "authoritative_observation": "benchmark_runtime_observation_v0",
        "observation_command": "loopx benchmark runtime-observation --help",
        "triggers": [
            "launch_receipt",
            "terminal_transition",
            "runner_invalid_transition",
            "periodic_liveness_check",
        ],
        "instruction": (
            "Classify exact-job receipts and runner-owner liveness through "
            "runtime-observation. Apply its typed terminal or runner-invalid transition "
            "before releasing that run, then read concurrency-status and backfill any "
            "reported gap."
        ),
    }
    assert initial["next_action"] == "backfill_to_target"

    for index, role in enumerate(("treatment", "baseline", "baseline", "baseline")):
        result = admit_benchmark_case(
            path,
            run_id=f"run-{index}",
            case_id=f"case-{index}",
            arm_role=role,
            execute=True,
            admitted_at=f"2026-08-18T07:0{index + 1}:00Z",
        )
        assert result["admitted"] is True

    at_target = build_benchmark_concurrency_status(
        read_benchmark_concurrency_envelope(path)
    )
    assert at_target["target_occupancy"]["underfilled"] is False
    assert at_target["remaining_capacity"]["total"] == 0
    assert (
        at_target["runtime_reconciliation_hint"]["active_count_source"]
        == "admission_ledger"
    )
    assert at_target["next_action"] == "hold_at_target"

    blocked = admit_benchmark_case(
        path,
        run_id="run-above-target",
        case_id="case-above-target",
        arm_role="treatment",
        execute=True,
        admitted_at="2026-08-18T07:05:00Z",
    )
    assert blocked["admitted"] is False
    assert blocked["reason_codes"] == ["target_capacity_exhausted"]


def test_reserved_test_slot_prevents_baseline_starvation(tmp_path: Path) -> None:
    path = tmp_path / "concurrency-envelope.json"
    _configure(path, total=4, baseline=4)

    for index in range(3):
        result = admit_benchmark_case(
            path,
            run_id=f"baseline-{index}",
            case_id=f"case-{index}",
            arm_role="baseline",
            execute=True,
            admitted_at=f"2026-08-18T07:0{index + 1}:00Z",
        )
        assert result["admitted"] is True

    blocked = admit_benchmark_case(
        path,
        run_id="baseline-3",
        case_id="case-3",
        arm_role="baseline",
        execute=True,
        admitted_at="2026-08-18T07:04:00Z",
    )
    assert blocked["ok"] is False
    assert blocked["reason_codes"] == ["reserved_test_capacity"]

    test_arm = admit_benchmark_case(
        path,
        run_id="control-4",
        case_id="case-4",
        arm_role="control",
        execute=True,
        admitted_at="2026-08-18T07:05:00Z",
    )
    assert test_arm["admitted"] is True
    assert test_arm["status"]["active_counts"] == {
        "total": 4,
        "baseline": 3,
        "test": 1,
    }


def test_required_resource_headroom_receipt_fails_closed_before_reservation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "concurrency-envelope.json"
    _configure(path, total=2, baseline=1, require_resource_headroom_receipt=True)

    missing = admit_benchmark_case(
        path,
        run_id="baseline-missing",
        case_id="case-missing",
        arm_role="baseline",
        execute=True,
        admitted_at="2026-08-18T07:05:00Z",
    )
    insufficient = admit_benchmark_case(
        path,
        run_id="baseline-low-storage",
        case_id="case-low-storage",
        arm_role="baseline",
        execute=True,
        admitted_at="2026-08-18T07:05:00Z",
        resource_headroom_receipt=_resource_headroom_receipt(state="insufficient"),
    )
    expired = admit_benchmark_case(
        path,
        run_id="baseline-expired",
        case_id="case-expired",
        arm_role="baseline",
        execute=True,
        admitted_at="2026-08-18T08:00:00Z",
        resource_headroom_receipt=_resource_headroom_receipt(),
    )
    unresolved = admit_benchmark_case(
        path,
        run_id="baseline-unresolved",
        case_id="case-unresolved",
        arm_role="baseline",
        execute=True,
        admitted_at="2026-08-18T07:05:00Z",
        resource_headroom_receipt=_resource_headroom_receipt(state="unresolved"),
    )
    future = admit_benchmark_case(
        path,
        run_id="baseline-future",
        case_id="case-future",
        arm_role="baseline",
        execute=True,
        admitted_at="2026-08-18T07:05:00Z",
        resource_headroom_receipt=_resource_headroom_receipt(
            observed_at="2026-08-18T07:06:00Z",
        ),
    )

    assert missing["reason_codes"] == ["resource_headroom_receipt_required"]
    assert insufficient["reason_codes"] == ["temporary_storage_insufficient"]
    assert expired["reason_codes"] == ["resource_headroom_receipt_expired"]
    assert unresolved["reason_codes"] == ["temporary_storage_unresolved"]
    assert future["reason_codes"] == ["resource_headroom_receipt_from_future"]
    assert missing["write_performed"] is False
    assert insufficient["write_performed"] is False
    assert expired["write_performed"] is False
    assert unresolved["write_performed"] is False
    assert future["write_performed"] is False
    assert read_benchmark_concurrency_envelope(path)["active_runs"] == []


def test_qualified_resource_headroom_receipt_is_not_persisted(tmp_path: Path) -> None:
    path = tmp_path / "concurrency-envelope.json"
    _configure(path, total=2, baseline=1, require_resource_headroom_receipt=True)

    admitted = admit_benchmark_case(
        path,
        run_id="baseline-qualified",
        case_id="case-qualified",
        arm_role="baseline",
        execute=True,
        admitted_at="2026-08-18T07:05:00Z",
        resource_headroom_receipt=_resource_headroom_receipt(),
    )

    assert admitted["admitted"] is True
    assert admitted["resource_headroom"] == {
        "qualified": True,
        "required": True,
        "reason_codes": [],
        "check_count": 2,
        "check_kinds": ["process_capacity", "temporary_storage"],
        "receipt_persisted": False,
    }
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert "resource_headroom" not in stored
    assert stored["config"]["require_resource_headroom_receipt"] is True


def test_resource_headroom_receipt_rejects_unknown_or_duplicate_checks() -> None:
    invalid_kind = _resource_headroom_receipt()
    invalid_kind["checks"] = [{"kind": "private_mount", "state": "sufficient"}]
    with pytest.raises(ValueError, match="kind is unsupported"):
        normalize_benchmark_resource_headroom_receipt(invalid_kind)

    duplicate = _resource_headroom_receipt()
    duplicate["checks"] = [
        {"kind": "memory", "state": "sufficient"},
        {"kind": "memory", "state": "sufficient"},
    ]
    with pytest.raises(ValueError, match="cannot repeat"):
        normalize_benchmark_resource_headroom_receipt(duplicate)

    overlong = _resource_headroom_receipt(
        observed_at="2026-08-18T07:00:00Z",
        expires_at="2026-08-18T07:15:01Z",
    )
    with pytest.raises(ValueError, match="must not exceed 15 minutes"):
        normalize_benchmark_resource_headroom_receipt(overlong)


def test_admission_is_idempotent_and_release_reopens_capacity(tmp_path: Path) -> None:
    path = tmp_path / "concurrency-envelope.json"
    _configure(path, total=2, baseline=1)
    first = admit_benchmark_case(
        path,
        run_id="control-1",
        case_id="case-1",
        arm_role="treatment",
        execute=True,
        admitted_at="2026-08-18T07:01:00Z",
    )
    repeated = admit_benchmark_case(
        path,
        run_id="control-1",
        case_id="case-1",
        arm_role="treatment",
        execute=True,
        admitted_at="2026-08-18T07:02:00Z",
    )
    assert first["write_performed"] is True
    assert repeated["idempotent"] is True
    assert repeated["write_performed"] is False

    released = release_benchmark_case(
        path,
        run_id="control-1",
        execute=True,
        released_at="2026-08-18T07:03:00Z",
    )
    repeated_release = release_benchmark_case(
        path,
        run_id="control-1",
        execute=True,
        released_at="2026-08-18T07:04:00Z",
    )
    assert released["released"] is True
    assert released["write_performed"] is True
    assert repeated_release["idempotent"] is True
    assert repeated_release["write_performed"] is False
    assert (
        build_benchmark_concurrency_status(read_benchmark_concurrency_envelope(path))[
            "active_counts"
        ]["total"]
        == 0
    )


def test_atomic_admission_never_exceeds_total_capacity(tmp_path: Path) -> None:
    path = tmp_path / "concurrency-envelope.json"
    _configure(path, total=3, baseline=2)

    def admit(index: int) -> dict[str, object]:
        return admit_benchmark_case(
            path,
            run_id=f"run-{index}",
            case_id=f"case-{index}",
            arm_role="control" if index == 0 else "baseline",
            execute=True,
            admitted_at=f"2026-08-18T07:{index + 1:02d}:00Z",
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(admit, range(12)))

    assert sum(result["admitted"] is True for result in results) == 3
    status = build_benchmark_concurrency_status(
        read_benchmark_concurrency_envelope(path)
    )
    assert status["active_counts"]["total"] == 3
    assert status["overcommitted"] is False


def test_cli_previews_config_and_requires_admission_before_launch(
    tmp_path: Path,
) -> None:
    base = [
        "benchmark",
        "concurrency-configure",
        "--goal-id",
        "fixture-goal",
        "--project",
        str(tmp_path),
        "--max-active-cases",
        "8",
        "--target-active-cases",
        "6",
        "--max-baseline-cases",
        "7",
        "--max-test-cases",
        "4",
        "--reserved-test-cases",
        "1",
        "--format",
        "json",
    ]
    path = default_benchmark_concurrency_envelope_path(
        project=tmp_path, goal_id="fixture-goal"
    )
    preview = run_source_cli(base, check=True)
    assert json.loads(preview.stdout)["dry_run"] is True
    assert not path.exists()

    configured = run_source_cli([*base, "--execute"], check=True)
    configured_payload = json.loads(configured.stdout)
    assert configured_payload["write_performed"] is True
    assert str(tmp_path) not in configured.stdout

    admitted = run_source_cli(
        [
            "benchmark",
            "concurrency-admit",
            "--goal-id",
            "fixture-goal",
            "--project",
            str(tmp_path),
            "--run-id",
            "baseline-1",
            "--case-id",
            "case-1",
            "--arm-role",
            "baseline",
            "--execute",
            "--format",
            "json",
        ],
        check=True,
    )
    assert json.loads(admitted.stdout)["admitted"] is True

    shown = run_source_cli(
        [
            "benchmark",
            "concurrency-status",
            "--goal-id",
            "fixture-goal",
            "--project",
            str(tmp_path),
            "--format",
            "json",
        ],
        check=True,
    )
    payload = json.loads(shown.stdout)
    assert payload["active_counts"] == {"total": 1, "baseline": 1, "test": 0}
    assert payload["target_occupancy"]["missing_cases"] == 5
    assert payload["backfill_hint"]["required"] is True
    assert payload["next_action"] == "backfill_to_target"


def test_cli_resource_headroom_gate_requires_fresh_typed_receipt(
    tmp_path: Path,
) -> None:
    configure = run_source_cli(
        [
            "benchmark",
            "concurrency-configure",
            "--goal-id",
            "fixture-goal",
            "--project",
            str(tmp_path),
            "--max-active-cases",
            "2",
            "--max-baseline-cases",
            "1",
            "--max-test-cases",
            "1",
            "--require-resource-headroom-receipt",
            "--execute",
            "--format",
            "json",
        ],
        check=True,
    )
    assert (
        json.loads(configure.stdout)["status"]["config"][
            "require_resource_headroom_receipt"
        ]
        is True
    )

    missing = run_source_cli(
        [
            "benchmark",
            "concurrency-admit",
            "--goal-id",
            "fixture-goal",
            "--project",
            str(tmp_path),
            "--run-id",
            "baseline-missing",
            "--case-id",
            "case-missing",
            "--arm-role",
            "baseline",
            "--execute",
            "--format",
            "json",
        ],
        check=False,
    )
    assert missing.returncode == 2
    assert json.loads(missing.stdout)["reason_codes"] == [
        "resource_headroom_receipt_required"
    ]

    receipt_path = tmp_path / "resource-headroom.json"
    observed_at = datetime.now(UTC)
    receipt_path.write_text(
        json.dumps(
            _resource_headroom_receipt(
                observed_at=observed_at.isoformat(),
                expires_at=(observed_at + timedelta(minutes=5)).isoformat(),
            )
        ),
        encoding="utf-8",
    )
    admitted = run_source_cli(
        [
            "benchmark",
            "concurrency-admit",
            "--goal-id",
            "fixture-goal",
            "--project",
            str(tmp_path),
            "--run-id",
            "baseline-qualified",
            "--case-id",
            "case-qualified",
            "--arm-role",
            "baseline",
            "--resource-headroom-json",
            str(receipt_path),
            "--execute",
            "--format",
            "json",
        ],
        check=True,
    )
    payload = json.loads(admitted.stdout)
    assert payload["admitted"] is True
    assert payload["resource_headroom"]["qualified"] is True
    assert str(receipt_path) not in admitted.stdout


def test_cli_omitted_project_routes_envelope_to_registered_goal_repo(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, global_registry = _connected_goal_registries(tmp_path)
    unrelated = tmp_path / "linked-worktree"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)

    assert (
        main(
            [
                "--registry",
                str(global_registry),
                "benchmark",
                "concurrency-configure",
                "--goal-id",
                "fixture-goal",
                "--max-active-cases",
                "4",
                "--max-baseline-cases",
                "3",
                "--reserved-test-cases",
                "1",
                "--execute",
                "--format",
                "json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    envelope = default_benchmark_concurrency_envelope_path(
        project=project,
        goal_id="fixture-goal",
    )

    assert payload["status"]["config"]["max_active_cases"] == 4
    assert envelope.is_file()
    assert not (unrelated / ".loopx").exists()
