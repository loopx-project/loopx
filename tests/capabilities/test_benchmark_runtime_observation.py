from __future__ import annotations

import json

import pytest

from loopx.capabilities.benchmark_toolkit import (
    BENCHMARK_RUNTIME_OBSERVATION_SCHEMA_VERSION,
    build_benchmark_runtime_observation,
)
from tests.source_cli import run_source_cli

@pytest.mark.parametrize(
    (
        "facts",
        "classification",
        "healthy",
        "reconciliation_required",
        "transition",
    ),
    [
        (
            {
                "admission_active": False,
                "job_receipt_state": "resolved",
                "runner_owner_state": "alive",
                "terminal_result_present": True,
            },
            "not_admitted",
            False,
            False,
            "none",
        ),
        (
            {
                "admission_active": True,
                "job_receipt_state": "resolved",
                "runner_owner_state": "alive",
            },
            "running_qualified",
            True,
            False,
            "none",
        ),
        (
            {
                "admission_active": True,
                "job_receipt_state": "resolved",
                "runner_owner_state": "alive",
                "terminal_result_present": True,
            },
            "terminal_pending_reconcile",
            False,
            True,
            "write_terminal_then_release",
        ),
        (
            {
                "admission_active": True,
                "job_receipt_state": "resolved",
                "runner_owner_state": "alive",
                "typed_fatal_runner_error": True,
            },
            "runner_invalid_pending_reconcile",
            False,
            True,
            "write_runner_invalid_then_release",
        ),
        (
            {
                "admission_active": True,
                "job_receipt_state": "missing",
                "runner_owner_state": "alive",
                "terminal_result_present": True,
            },
            "runtime_authority_unresolved",
            False,
            False,
            "repair_runtime_authority",
        ),
        (
            {
                "admission_active": True,
                "job_receipt_state": "ambiguous",
                "runner_owner_state": "alive",
                "typed_fatal_runner_error": True,
            },
            "runtime_authority_unresolved",
            False,
            False,
            "repair_runtime_authority",
        ),
        (
            {
                "admission_active": True,
                "job_receipt_state": "resolved",
                "runner_owner_state": "absent_after_grace",
            },
            "runner_lost_pending_reconcile",
            False,
            True,
            "write_runner_invalid_then_release",
        ),
        (
            {
                "admission_active": True,
                "job_receipt_state": "resolved",
                "runner_owner_state": "unknown",
            },
            "startup_or_liveness_unresolved",
            False,
            False,
            "reobserve_after_startup_grace",
        ),
    ],
)
def test_runtime_observation_requires_exact_authority_and_owner_liveness(
    facts: dict[str, object],
    classification: str,
    healthy: bool,
    reconciliation_required: bool,
    transition: str,
) -> None:
    receipt = build_benchmark_runtime_observation(**facts)

    assert receipt["schema_version"] == BENCHMARK_RUNTIME_OBSERVATION_SCHEMA_VERSION
    assert receipt["classification"] == classification
    assert receipt["healthy_active"] is healthy
    assert receipt["reconciliation_required"] is reconciliation_required
    assert receipt["recommended_transition"] == transition
    assert receipt["ledger_occupancy_sufficient"] is False
    assert receipt["exact_runtime_authority_required"] is True
    assert receipt["write_performed"] is False


def test_runtime_observation_receipt_is_public_safe() -> None:
    private_values = [
        "private-run-id",
        "/private/worktree",
        "--job-name private-run-id",
        "private runner failure",
    ]
    receipt = build_benchmark_runtime_observation(
        admission_active=True,
        job_receipt_state="ambiguous",
        runner_owner_state="unknown",
    )

    assert receipt["public_boundary"] == {
        "run_identity_recorded": False,
        "process_arguments_recorded": False,
        "raw_error_recorded": False,
        "path_recorded": False,
    }
    rendered = json.dumps(receipt, sort_keys=True)
    assert all(private_value not in rendered for private_value in private_values)


def test_runtime_observation_rejects_unknown_typed_states() -> None:
    with pytest.raises(ValueError, match="^invalid_job_receipt_state$"):
        build_benchmark_runtime_observation(
            admission_active=True,
            job_receipt_state="guessed",
            runner_owner_state="alive",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("admission_active", "false"),
        ("terminal_result_present", 1),
        ("typed_fatal_runner_error", "true"),
    ],
)
def test_runtime_observation_rejects_non_boolean_facts(
    field: str,
    value: object,
) -> None:
    facts: dict[str, object] = {
        "admission_active": True,
        "job_receipt_state": "resolved",
        "runner_owner_state": "alive",
    }
    facts[field] = value

    with pytest.raises(TypeError, match=rf"^{field}_must_be_boolean$"):
        build_benchmark_runtime_observation(**facts)


def test_runtime_observation_cli_can_fail_closed() -> None:
    command = [
        "benchmark",
        "runtime-observation",
        "--admission-active",
        "--job-receipt-state",
        "resolved",
        "--runner-owner-state",
        "absent_after_grace",
        "--require-healthy",
        "--format",
        "json",
    ]
    completed = run_source_cli(command, check=False)

    assert completed.returncode == 1
    receipt = json.loads(completed.stdout)
    assert receipt["classification"] == "runner_lost_pending_reconcile"
    assert receipt["healthy_active"] is False
    assert receipt["recommended_transition"] == "write_runner_invalid_then_release"
