from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from loopx.capabilities.benchmark_toolkit import (
    BENCHMARK_TREATMENT_CONTINUATION_RECEIPT_SCHEMA_VERSION,
    build_benchmark_treatment_continuation_receipt,
)
from tests.source_cli import run_source_cli

def _observation() -> dict[str, object]:
    return {
        "schema_version": "benchmark_treatment_continuation_observation_v0",
        "treatment_applicable": True,
        "startup_state": "qualified",
        "observation_complete": True,
        "post_start_control_events": {
            "todo_transition_count": 0,
            "technical_replan_count": 0,
            "control_closeout_count": 0,
        },
        "terminal_control_state": "unsettled",
        "precommit_validation_state": "observed",
    }


def test_receipt_distinguishes_startup_only_from_score_qualification() -> None:
    receipt = build_benchmark_treatment_continuation_receipt(_observation())

    assert receipt["schema_version"] == (
        BENCHMARK_TREATMENT_CONTINUATION_RECEIPT_SCHEMA_VERSION
    )
    assert receipt["classification"] == "startup_only"
    assert receipt["post_start_control_observed"] is False
    assert receipt["terminal_control_state"] == "unsettled"
    assert receipt["score_semantics"] == {
        "score_countability_unchanged": True,
        "integrity_qualification_unchanged": True,
        "treatment_fidelity_unchanged": True,
        "claim_scope": "post_run_mechanism_analysis_only",
    }
    assert receipt["public_boundary"] == {
        "raw_content_recorded": False,
        "path_recorded": False,
        "run_identity_recorded": False,
    }


def test_receipt_reports_post_start_semantic_control_without_claiming_closeout() -> None:
    observation = _observation()
    observation["post_start_control_events"] = {
        "todo_transition_count": 2,
        "technical_replan_count": 1,
        "control_closeout_count": 0,
    }

    receipt = build_benchmark_treatment_continuation_receipt(observation)

    assert receipt["classification"] == "sustained"
    assert receipt["post_start_control_event_count"] == 3
    assert receipt["terminal_control_state"] == "unsettled"
    assert "terminal_control_unsettled" in receipt["reason_codes"]


def test_terminal_only_closeout_does_not_establish_sustained_control() -> None:
    observation = _observation()
    observation["post_start_control_events"] = {
        "todo_transition_count": 0,
        "technical_replan_count": 0,
        "control_closeout_count": 3,
    }
    observation["terminal_control_state"] = "settled"

    receipt = build_benchmark_treatment_continuation_receipt(observation)

    assert receipt["classification"] == "startup_only"
    assert receipt["post_start_control_observed"] is True
    assert receipt["post_start_control_event_count"] == 3
    assert receipt["post_start_control_event_counts"] == {
        "control_closeout_count": 3,
        "technical_replan_count": 0,
        "todo_transition_count": 0,
    }
    assert (
        "terminal_only_control_does_not_establish_persistence"
        in receipt["reason_codes"]
    )


def test_incomplete_terminal_only_observation_remains_unknown() -> None:
    observation = _observation()
    observation["observation_complete"] = False
    observation["post_start_control_events"] = {
        "todo_transition_count": 0,
        "technical_replan_count": 0,
        "control_closeout_count": 1,
    }

    receipt = build_benchmark_treatment_continuation_receipt(observation)

    assert receipt["classification"] == "unknown"
    assert receipt["post_start_control_event_count"] == 1
    assert "post_run_observation_incomplete" in receipt["reason_codes"]


def test_absence_requires_complete_post_run_observation() -> None:
    observation = _observation()
    observation["observation_complete"] = False

    receipt = build_benchmark_treatment_continuation_receipt(observation)

    assert receipt["classification"] == "unknown"
    assert "post_run_observation_incomplete" in receipt["reason_codes"]


def test_non_treatment_receipt_is_explicitly_not_applicable() -> None:
    observation = _observation()
    observation.update(
        treatment_applicable=False,
        startup_state="not_applicable",
        terminal_control_state="not_applicable",
        precommit_validation_state="not_applicable",
    )

    receipt = build_benchmark_treatment_continuation_receipt(observation)

    assert receipt["classification"] == "not_applicable"
    assert receipt["post_start_control_event_count"] == 0


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.update(extra="private-ref"), "observation fields"),
        (
            lambda value: value.update(observation_complete=1),
            "observation_complete must be a boolean",
        ),
        (
            lambda value: value["post_start_control_events"].update(
                todo_transition_count=-1
            ),
            "must be a non-negative integer",
        ),
        (
            lambda value: value.update(
                treatment_applicable=False,
                startup_state="not_applicable",
            ),
            "not_applicable terminal control",
        ),
    ],
)
def test_receipt_rejects_ambiguous_or_wide_observations(mutation, message: str) -> None:
    observation = copy.deepcopy(_observation())
    mutation(observation)

    with pytest.raises((TypeError, ValueError), match=message):
        build_benchmark_treatment_continuation_receipt(observation)


def test_cli_emits_public_safe_receipt(tmp_path: Path) -> None:
    observation_path = tmp_path / "observation.json"
    observation_path.write_text(json.dumps(_observation()), encoding="utf-8")

    completed = run_source_cli(
        [
            "benchmark",
            "treatment-continuation-receipt",
            "--observation-json",
            str(observation_path),
            "--format",
            "json",
        ],
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["classification"] == "startup_only"
    assert str(observation_path) not in completed.stdout


def test_cli_fails_closed_on_invalid_observation(tmp_path: Path) -> None:
    observation = _observation()
    observation["private_trajectory"] = "/private/run.jsonl"
    observation_path = tmp_path / "invalid.json"
    observation_path.write_text(json.dumps(observation), encoding="utf-8")

    completed = run_source_cli(
        [
            "benchmark",
            "treatment-continuation-receipt",
            "--observation-json",
            str(observation_path),
            "--format",
            "json",
        ],
        check=False,
    )

    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    assert payload["classification"] == "input_invalid"
    assert "/private/run.jsonl" not in completed.stdout


def test_catalog_teaches_score_neutral_treatment_continuation_readback() -> None:
    from loopx.capabilities.catalog import build_capability_detail_packet

    capability = build_capability_detail_packet("benchmark-toolkit")["capability"]
    analysis = capability["post_run_case_analysis"]
    continuation = analysis["treatment_continuation_receipt"]

    assert "treatment-continuation-receipt" in continuation["command"]
    assert continuation["classifications"] == [
        "sustained",
        "startup_only",
        "unknown",
        "not_applicable",
    ]
    assert continuation["score_semantics"] == "unchanged"
    assert "does not change" in continuation["analysis_boundary"]
    assert "terminal-only" in continuation["persistence_rule"].lower()
    assert continuation["observation_template"]["schema_version"] == (
        "benchmark_treatment_continuation_observation_v0"
    )
