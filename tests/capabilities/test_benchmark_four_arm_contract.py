from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx.capabilities.benchmark_toolkit import (
    BENCHMARK_FOUR_ARM_CONTRACT_SCHEMA_VERSION,
    BENCHMARK_FOUR_ARM_SPEC_SCHEMA_VERSION,
    build_benchmark_four_arm_contract,
    build_benchmark_four_arm_contract_from_spec,
    compact_benchmark_four_arm_contract,
)
from tests.source_cli import run_source_cli

BASE_GOAL = "Complete the requested benchmark task."
DOMAIN_HINT = "Implement the public requirements, validate them, and review the diff."


def _contract() -> dict[str, object]:
    return build_benchmark_four_arm_contract(
        base_goal_text=BASE_GOAL,
        domain_hint=DOMAIN_HINT,
        hint_id="swe_hint",
        domain_hint_independent_of_loopx=True,
    )


def test_four_arm_contract_changes_only_the_two_declared_factors() -> None:
    contract = _contract()
    arms = {arm["arm_id"]: arm for arm in contract["arms"]}

    assert contract["schema_version"] == BENCHMARK_FOUR_ARM_CONTRACT_SCHEMA_VERSION
    assert contract["qualified"] is True
    assert contract["qualification_scope"] == "factor_design_and_prompt_parity_only"
    assert contract["execution_qualified"] is False
    assert set(arms) == {
        "goal_plain",
        "loopx_plain",
        "goal_swe_hint",
        "loopx_swe_hint",
    }
    assert arms["goal_plain"]["task_goal_text"] == BASE_GOAL
    assert (
        arms["goal_plain"]["task_goal_sha256"]
        == arms["loopx_plain"]["task_goal_sha256"]
    )
    assert arms["goal_swe_hint"]["task_goal_text"] == (f"{BASE_GOAL}\n\n{DOMAIN_HINT}")
    assert (
        arms["goal_swe_hint"]["task_goal_sha256"]
        == arms["loopx_swe_hint"]["task_goal_sha256"]
    )
    assert (
        arms["goal_plain"]["task_goal_sha256"]
        != arms["goal_swe_hint"]["task_goal_sha256"]
    )
    assert arms["loopx_plain"]["comparison_anchor_arm_id"] == "goal_plain"
    assert arms["goal_swe_hint"]["comparison_anchor_arm_id"] == "goal_plain"
    assert arms["loopx_swe_hint"]["comparison_anchor_arm_id"] == "goal_swe_hint"
    assert contract["attestations"] == {
        "domain_hint_independent_of_loopx": True,
    }
    assert contract["runner_obligations"] == [
        "keep_loopx_startup_out_of_band",
        "match_runtime_task_goal_hash_to_selected_arm",
        "pin_all_non_factor_inputs",
        "register_each_run_on_experiment_board",
    ]
    assert contract["interaction_contrast"] == {
        "candidate_effect": {
            "candidate_arm_id": "loopx_swe_hint",
            "anchor_arm_id": "goal_swe_hint",
        },
        "anchor_effect": {
            "candidate_arm_id": "loopx_plain",
            "anchor_arm_id": "goal_plain",
        },
    }


def test_compact_contract_retains_hashes_but_omits_prompt_text() -> None:
    compact = compact_benchmark_four_arm_contract(_contract())

    assert compact["prompt_text_recorded"] is False
    assert all("task_goal_text" not in arm for arm in compact["arms"])
    assert all("task_goal_sha256" in arm for arm in compact["arms"])
    assert BASE_GOAL not in json.dumps(compact)
    assert DOMAIN_HINT not in json.dumps(compact)


@pytest.mark.parametrize(
    ("override", "match"),
    [
        ({"domain_hint_independent_of_loopx": False}, "attested independent"),
        ({"hint_id": "SWE hint"}, "lowercase token"),
        ({"domain_hint": "  "}, "must not be empty"),
    ],
)
def test_four_arm_contract_fails_closed_on_invalid_factor_spec(
    override: dict[str, object], match: str
) -> None:
    kwargs: dict[str, object] = {
        "base_goal_text": BASE_GOAL,
        "domain_hint": DOMAIN_HINT,
        "hint_id": "swe_hint",
        "domain_hint_independent_of_loopx": True,
    }
    kwargs.update(override)

    with pytest.raises((TypeError, ValueError), match=match):
        build_benchmark_four_arm_contract(**kwargs)


def test_spec_rejects_unknown_fields() -> None:
    with pytest.raises(ValueError, match="unsupported fields"):
        build_benchmark_four_arm_contract_from_spec(
            {
                "schema_version": BENCHMARK_FOUR_ARM_SPEC_SCHEMA_VERSION,
                "base_goal_text": BASE_GOAL,
                "domain_hint": DOMAIN_HINT,
                "domain_hint_independent_of_loopx": True,
                "provider_prompt": "must not be mixed into task semantics",
            }
        )


@pytest.mark.parametrize("invalid_hint_id", [None, 0, False, ""])
def test_spec_rejects_explicit_invalid_hint_id(invalid_hint_id: object) -> None:
    with pytest.raises((TypeError, ValueError), match="hint_id"):
        build_benchmark_four_arm_contract_from_spec(
            {
                "schema_version": BENCHMARK_FOUR_ARM_SPEC_SCHEMA_VERSION,
                "base_goal_text": BASE_GOAL,
                "domain_hint": DOMAIN_HINT,
                "hint_id": invalid_hint_id,
                "domain_hint_independent_of_loopx": True,
            }
        )


def test_cli_defaults_to_a_prompt_redacted_qualification_receipt(
    tmp_path: Path,
) -> None:
    spec_path = tmp_path / "four-arm.json"
    spec_path.write_text(
        json.dumps(
            {
                "schema_version": BENCHMARK_FOUR_ARM_SPEC_SCHEMA_VERSION,
                "base_goal_text": BASE_GOAL,
                "domain_hint": DOMAIN_HINT,
                "hint_id": "swe_hint",
                "domain_hint_independent_of_loopx": True,
            }
        ),
        encoding="utf-8",
    )

    completed = run_source_cli(
        [
            "benchmark",
            "four-arm-contract",
            "--spec-json",
            str(spec_path),
            "--require-qualified",
            "--format",
            "json",
        ],
        check=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["qualified"] is True
    assert payload["execution_qualified"] is False
    assert payload["prompt_text_recorded"] is False
    assert BASE_GOAL not in completed.stdout
    assert DOMAIN_HINT not in completed.stdout


def test_cli_can_return_prompts_only_under_explicit_runner_opt_in(
    tmp_path: Path,
) -> None:
    spec_path = tmp_path / "four-arm.json"
    spec_path.write_text(
        json.dumps(
            {
                "schema_version": BENCHMARK_FOUR_ARM_SPEC_SCHEMA_VERSION,
                "base_goal_text": BASE_GOAL,
                "domain_hint": DOMAIN_HINT,
                "domain_hint_independent_of_loopx": True,
            }
        ),
        encoding="utf-8",
    )

    completed = run_source_cli(
        [
            "benchmark",
            "four-arm-contract",
            "--spec-json",
            str(spec_path),
            "--include-prompt-text",
            "--format",
            "json",
        ],
        check=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["arms"][0]["task_goal_text"] == BASE_GOAL
    assert payload["prompt_text_recorded"] is True
