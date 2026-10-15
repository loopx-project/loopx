from __future__ import annotations

import json

import pytest

from loopx.capabilities.benchmark_toolkit import (
    BENCHMARK_RUNTIME_CONTINUITY_SCHEMA_VERSION,
    build_benchmark_runtime_continuity,
)
from tests.source_cli import run_source_cli

RUNTIME_DIGEST = "a" * 64
GENERATION_DIGEST = "b" * 64


def _continuity(**overrides: str) -> dict[str, object]:
    facts = {
        "launch_runtime_digest": RUNTIME_DIGEST,
        "closeout_runtime_digest": RUNTIME_DIGEST,
        "launch_generation_digest": GENERATION_DIGEST,
        "closeout_generation_digest": GENERATION_DIGEST,
        "event_window_state": "qualified",
    }
    facts.update(overrides)
    return build_benchmark_runtime_continuity(**facts)


def test_runtime_continuity_qualifies_one_launch_generation() -> None:
    receipt = _continuity()

    assert receipt["schema_version"] == BENCHMARK_RUNTIME_CONTINUITY_SCHEMA_VERSION
    assert receipt["classification"] == "qualified"
    assert receipt["qualified"] is True
    assert receipt["closeout_write_allowed"] is True
    assert receipt["runtime_artifact_matches"] is True
    assert receipt["generation_matches"] is True
    assert receipt["event_window_qualified"] is True
    assert receipt["recommended_transition"] == "accept_closeout"


@pytest.mark.parametrize(
    ("overrides", "classification", "transition"),
    [
        (
            {"closeout_generation_digest": "c" * 64},
            "generation_mismatch",
            "route_to_launch_generation",
        ),
        (
            {"closeout_runtime_digest": "c" * 64},
            "runtime_artifact_mismatch",
            "reject_runtime_artifact",
        ),
        (
            {"event_window_state": "missing"},
            "event_window_missing",
            "repair_event_window_evidence",
        ),
        (
            {"event_window_state": "ambiguous"},
            "event_window_ambiguous",
            "repair_event_window_evidence",
        ),
        (
            {"event_window_state": "outside_launch_window"},
            "event_window_outside_launch",
            "repair_event_window_evidence",
        ),
    ],
)
def test_runtime_continuity_mutations_fail_closed(
    overrides: dict[str, str],
    classification: str,
    transition: str,
) -> None:
    receipt = _continuity(**overrides)

    assert receipt["classification"] == classification
    assert receipt["qualified"] is False
    assert receipt["closeout_write_allowed"] is False
    assert receipt["recommended_transition"] == transition


def test_runtime_continuity_prioritizes_generation_routing() -> None:
    receipt = _continuity(
        closeout_generation_digest="c" * 64,
        closeout_runtime_digest="d" * 64,
        event_window_state="missing",
    )

    assert receipt["classification"] == "generation_mismatch"
    assert receipt["recommended_transition"] == "route_to_launch_generation"


def test_runtime_continuity_receipt_does_not_emit_private_evidence() -> None:
    receipt = _continuity()
    rendered = json.dumps(receipt, sort_keys=True)

    assert receipt["public_boundary"] == {
        "runtime_artifact_digest_recorded": False,
        "generation_digest_recorded": False,
        "event_payload_recorded": False,
        "run_identity_recorded": False,
        "path_recorded": False,
    }
    assert RUNTIME_DIGEST not in rendered
    assert GENERATION_DIGEST not in rendered


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("launch_runtime_digest", "a" * 63),
        ("closeout_runtime_digest", "A" * 64),
        ("launch_generation_digest", "generation-1"),
        ("closeout_generation_digest", ""),
        ("event_window_state", "guessed"),
    ],
)
def test_runtime_continuity_rejects_untyped_evidence(
    field: str,
    value: str,
) -> None:
    with pytest.raises(ValueError):
        _continuity(**{field: value})


def test_runtime_continuity_cli_fails_closed_without_echoing_digests() -> None:
    command = [
        "benchmark",
        "runtime-continuity",
        "--launch-runtime-digest",
        RUNTIME_DIGEST,
        "--closeout-runtime-digest",
        RUNTIME_DIGEST,
        "--launch-generation-digest",
        GENERATION_DIGEST,
        "--closeout-generation-digest",
        "c" * 64,
        "--event-window-state",
        "qualified",
        "--require-qualified",
        "--format",
        "json",
    ]
    completed = run_source_cli(command, check=False)

    assert completed.returncode == 1
    receipt = json.loads(completed.stdout)
    assert receipt["classification"] == "generation_mismatch"
    assert receipt["recommended_transition"] == "route_to_launch_generation"
    assert RUNTIME_DIGEST not in completed.stdout
    assert GENERATION_DIGEST not in completed.stdout


def test_runtime_continuity_cli_reduces_invalid_input_without_echo() -> None:
    command = [
        "benchmark",
        "runtime-continuity",
        "--launch-runtime-digest",
        "private-invalid-digest",
        "--closeout-runtime-digest",
        RUNTIME_DIGEST,
        "--launch-generation-digest",
        GENERATION_DIGEST,
        "--closeout-generation-digest",
        GENERATION_DIGEST,
        "--event-window-state",
        "qualified",
        "--require-qualified",
        "--format",
        "json",
    ]
    completed = run_source_cli(command, check=False)

    assert completed.returncode == 1
    receipt = json.loads(completed.stdout)
    assert receipt["classification"] == "continuity_input_invalid"
    assert receipt["recommended_transition"] == "repair_continuity_evidence"
    assert "private-invalid-digest" not in completed.stdout
