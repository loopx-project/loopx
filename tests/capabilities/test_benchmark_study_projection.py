from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

import pytest

from loopx.capabilities.benchmark_toolkit import (
    BENCHMARK_CASE_INSIGHT_PROJECTION_SCHEMA_VERSION,
    BENCHMARK_EXPERIMENT_BOARD_ROW_SCHEMA_VERSION,
    BENCHMARK_STUDY_MANIFEST_SCHEMA_VERSION,
    build_benchmark_four_arm_contract,
    build_benchmark_study_dashboard,
    build_benchmark_runtime_observation,
    build_benchmark_upload_envelope,
    compact_benchmark_four_arm_contract,
    normalize_benchmark_case_insight_projection,
    normalize_benchmark_study_manifest,
    normalize_benchmark_upload_envelope,
    read_benchmark_local_upload_records,
    read_benchmark_upload_receipt,
    simulate_benchmark_upload,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _loopx_json(*args: str) -> dict[str, object]:
    result = subprocess.run(
        [str(REPO_ROOT / "scripts/loopx"), *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def _manifest(*, four_arm: bool = False) -> dict[str, object]:
    factors = [{"factor_id": "orchestrator", "levels": ["goal", "loopx"]}]
    arms: list[dict[str, object]] = [
        {
            "arm_id": "goal_plain",
            "arm_role": "baseline",
            "factor_assignments": {"orchestrator": "goal"},
        },
        {
            "arm_id": "loopx_plain",
            "arm_role": "treatment",
            "factor_assignments": {"orchestrator": "loopx"},
        },
    ]
    if four_arm:
        factors.append({"factor_id": "domain_hint", "levels": ["none", "swe"]})
        arms = [
            {
                "arm_id": "goal_plain",
                "arm_role": "baseline",
                "factor_assignments": {
                    "orchestrator": "goal",
                    "domain_hint": "none",
                },
            },
            {
                "arm_id": "loopx_plain",
                "arm_role": "treatment",
                "factor_assignments": {
                    "orchestrator": "loopx",
                    "domain_hint": "none",
                },
            },
            {
                "arm_id": "goal_swe",
                "arm_role": "control",
                "factor_assignments": {
                    "orchestrator": "goal",
                    "domain_hint": "swe",
                },
            },
            {
                "arm_id": "loopx_swe",
                "arm_role": "treatment",
                "factor_assignments": {
                    "orchestrator": "loopx",
                    "domain_hint": "swe",
                },
            },
        ]
    return {
        "schema_version": BENCHMARK_STUDY_MANIFEST_SCHEMA_VERSION,
        "benchmark_id": "fixture-swe@1",
        "study_id": "factorial-v1" if four_arm else "paired-v1",
        "protocol_id": "solver-v1",
        "comparison_protocol_id": "comparison-v1",
        "case_set": {
            "case_set_id": "public-cases-v1",
            "case_ids": ["case-1", "case-2"],
        },
        "factors": factors,
        "arms": arms,
        "baseline_arm_id": "goal_plain",
        "metrics": [
            {
                "metric_name": "requirements_passed",
                "role": "primary",
                "unit": "tests",
                "higher_is_better": True,
                "binary": False,
            },
            {
                "metric_name": "regressions_passed",
                "role": "guardrail",
                "unit": "tests",
                "higher_is_better": True,
                "binary": False,
            },
            {
                "metric_name": "reward",
                "role": "guardrail",
                "higher_is_better": True,
                "binary": True,
            },
        ],
        "source_revisions": [
            {"component": "runner", "revision": "0123456789abcdef"},
            {"component": "task-set", "revision": "fedcba9876543210"},
        ],
        "labels": {"title": "Fictional software benchmark"},
        "extension_metadata": {"native_primary_name": "requirements_passed"},
        "privacy_classification": "public_safe",
    }


def _five_mode_manifest() -> dict[str, object]:
    manifest = _manifest()
    manifest["study_id"] = "five-mode-v1"
    levels = ["plain", "goal", "managed_a", "managed_b", "scheduled"]
    manifest["factors"] = [{"factor_id": "execution_mode", "levels": levels}]
    manifest["arms"] = [
        {
            "arm_id": level,
            "arm_role": (
                "baseline"
                if level == "plain"
                else "control"
                if level == "goal"
                else "treatment"
            ),
            "factor_assignments": {"execution_mode": level},
        }
        for level in levels
    ]
    manifest["baseline_arm_id"] = "plain"
    manifest["labels"] = {"title": "Fictional five-mode software benchmark"}
    return manifest


def _row(
    *,
    arm_id: str,
    arm_role: str,
    feature: int,
    reward: int,
    study_id: str = "paired-v1",
    anchor: str | None = None,
) -> dict[str, object]:
    run_id = f"{arm_id}-case-1"
    row: dict[str, object] = {
        "schema_version": BENCHMARK_EXPERIMENT_BOARD_ROW_SCHEMA_VERSION,
        "benchmark_id": "fixture-swe@1",
        "study_id": study_id,
        "case_id": "case-1",
        "run_id": run_id,
        "arm_id": arm_id,
        "arm_role": arm_role,
        "attempt": 1,
        "status": "completed",
        "observed_at": "2026-09-02T12:00:00+00:00",
        "model_id": "fixture-model-v1",
        "protocol_id": f"{arm_id}-protocol-v1",
        "comparison_protocol_id": "comparison-v1",
        "runner_revision": "0123456789abcdef",
        "claim_scope": "matched_study",
        "primary_metric": "requirements_passed",
        "guardrail_metrics": ["regressions_passed", "reward"],
        "metrics": {
            "requirements_passed": {
                "value": feature,
                "total": 10,
                "unit": "tests",
                "higher_is_better": True,
            },
            "regressions_passed": {
                "value": 20,
                "total": 20,
                "unit": "tests",
                "higher_is_better": True,
            },
            "reward": {"value": reward, "higher_is_better": True},
        },
        "countability": {
            "integrity_qualified": True,
            "official_result_present": True,
            "score_countable": True,
        },
        "treatment_fidelity": "not_applicable"
        if arm_role == "baseline"
        else "qualified",
        "effort": {
            "duration_ms": 3600000,
            "agent_steps": 30,
            "goal_turns": 4,
            "token_count": 40000,
            "estimated_cost_usd": 0.5,
        },
        "insight": {"status": "complete", "classification": "contract_fit"},
    }
    if anchor is not None:
        row["comparison_anchor_run_id"] = anchor
    if arm_id.startswith("loopx"):
        row["orchestrator_runtime"] = {
            "provider_id": "loopx",
            "version": "0.6.0",
            "revision": "89abcdef01234567",
        }
    return row


def _insight(
    *,
    study_id: str = "paired-v1",
    run_id: str = "loopx_plain-case-1",
    outcome_status: str = "completed",
) -> dict[str, object]:
    return {
        "schema_version": BENCHMARK_CASE_INSIGHT_PROJECTION_SCHEMA_VERSION,
        "benchmark_id": "fixture-swe@1",
        "study_id": study_id,
        "case_id": "case-1",
        "run_id": run_id,
        "outcome_status": outcome_status,
        "failure_class": "none",
        "causal_summary": "The implementation preserved the declared API contract.",
        "expectedness": "expected",
        "implication": "Retain the independent contract check.",
        "next_probe": "Repeat on another public case family.",
        "confidence": "high",
        "evidence_refs": ["public-receipt:abc123"],
        "privacy_classification": "public_safe",
        "producer_redaction_attested": True,
    }


def _envelope(
    payload: dict[str, object],
    *,
    record_kind: str,
    key: str,
    study_id: str = "paired-v1",
    supersedes: str | None = None,
    observed_at: str = "2026-09-02T12:00:00+00:00",
) -> dict[str, object]:
    return build_benchmark_upload_envelope(
        payload,
        record_kind=record_kind,
        producer_id="fixture-adapter",
        producer_version="1.0.0",
        benchmark_id="fixture-swe@1",
        study_id=study_id,
        idempotency_key=key,
        observed_at=observed_at,
        source_revision="0123456789abcdef",
        supersedes_record_id=supersedes,
    )


def _upload_default_insight_run(store: Path) -> dict[str, object]:
    run = _envelope(
        _row(
            arm_id="loopx_plain",
            arm_role="treatment",
            feature=9,
            reward=1,
            anchor="goal_plain-case-1",
        ),
        record_kind="experiment_board_row",
        key="insight-run",
    )
    simulate_benchmark_upload(store, run, execute=True)
    return run


def _four_arm_contract() -> dict[str, object]:
    return compact_benchmark_four_arm_contract(
        build_benchmark_four_arm_contract(
            base_goal_text="Implement the public task.",
            domain_hint="Validate every public requirement independently.",
            hint_id="swe",
            domain_hint_independent_of_loopx=True,
        )
    )


def test_two_arm_and_factorized_four_arm_manifests_validate() -> None:
    assert len(normalize_benchmark_study_manifest(_manifest())["arms"]) == 2
    assert (
        len(normalize_benchmark_study_manifest(_manifest(four_arm=True))["arms"]) == 4
    )
    assert len(normalize_benchmark_study_manifest(_five_mode_manifest())["arms"]) == 5


@pytest.mark.parametrize("mutation", ["assignment", "baseline", "metrics"])
def test_invalid_manifest_design_fails_closed(mutation: str) -> None:
    manifest = _manifest()
    if mutation == "assignment":
        manifest["arms"][1]["factor_assignments"] = {"orchestrator": "unknown"}
    elif mutation == "baseline":
        manifest["baseline_arm_id"] = "loopx_plain"
    else:
        manifest["metrics"][1]["metric_name"] = "requirements_passed"
    with pytest.raises(ValueError):
        normalize_benchmark_study_manifest(manifest)


def test_board_row_round_trips_through_upload_envelope() -> None:
    row = _row(
        arm_id="loopx_plain",
        arm_role="treatment",
        feature=9,
        reward=1,
        anchor="goal_plain-case-1",
    )
    envelope = _envelope(row, record_kind="experiment_board_row", key="run-1")
    assert normalize_benchmark_upload_envelope(envelope)["payload"] == row


def test_envelope_rejects_identity_mismatch_unknown_kind_and_bad_digest() -> None:
    with pytest.raises(ValueError, match="benchmark_id"):
        build_benchmark_upload_envelope(
            _manifest(),
            record_kind="study_manifest",
            producer_id="fixture-adapter",
            producer_version="1.0.0",
            benchmark_id="another-benchmark",
            study_id="paired-v1",
            idempotency_key="manifest-v1",
            observed_at="2026-09-02T12:00:00+00:00",
            source_revision="0123456789abcdef",
        )
    with pytest.raises(ValueError, match="unsupported"):
        _envelope(_manifest(), record_kind="raw_trajectory", key="bad-kind")
    envelope = _envelope(_manifest(), record_kind="study_manifest", key="manifest-v1")
    envelope["payload_digest"] = "0" * 64
    with pytest.raises(ValueError, match="digest"):
        normalize_benchmark_upload_envelope(envelope)


def test_local_upload_preview_execute_replay_and_readback(tmp_path: Path) -> None:
    store = tmp_path / "simulated-upload.jsonl"
    envelope = _envelope(_manifest(), record_kind="study_manifest", key="manifest-v1")
    preview = simulate_benchmark_upload(store, envelope)
    assert preview["disposition"] == "preview_accepted"
    assert preview["write_performed"] is False
    assert not store.exists()

    accepted = simulate_benchmark_upload(store, envelope, execute=True)
    assert accepted["disposition"] == "accepted"
    assert accepted["write_performed"] is True
    assert accepted["external_write_performed"] is False
    assert accepted["network_access_performed"] is False

    retry = copy.deepcopy(envelope)
    retry["observed_at"] = "2026-09-02T13:00:00+00:00"
    replay = simulate_benchmark_upload(store, retry, execute=True)
    assert replay["disposition"] == "replayed"
    assert replay["provider_revision"] == 1
    readback = read_benchmark_upload_receipt(store, record_id=envelope["record_id"])
    assert readback["payload_digest"] == envelope["payload_digest"]
    assert readback["provider_revision"] == 1
    assert read_benchmark_local_upload_records(store)[0]["envelope"] == envelope


def test_idempotency_key_reuse_with_different_payload_fails(tmp_path: Path) -> None:
    store = tmp_path / "simulated-upload.jsonl"
    first = _envelope(_manifest(), record_kind="study_manifest", key="manifest-v1")
    simulate_benchmark_upload(store, first, execute=True)
    changed = _manifest()
    changed["labels"] = {"title": "Changed public title"}
    second = _envelope(changed, record_kind="study_manifest", key="manifest-v1")
    with pytest.raises(ValueError, match="different content"):
        simulate_benchmark_upload(store, second, execute=True)


def test_changed_board_row_requires_legal_explicit_supersession(tmp_path: Path) -> None:
    store = tmp_path / "simulated-upload.jsonl"
    running = _row(arm_id="goal_plain", arm_role="baseline", feature=8, reward=0)
    running.update(
        status="running",
        metrics={},
        countability={
            "integrity_qualified": False,
            "official_result_present": False,
            "score_countable": False,
        },
        insight={"status": "pending"},
    )
    first = _envelope(running, record_kind="experiment_board_row", key="run-start")
    simulate_benchmark_upload(store, first, execute=True)
    terminal = _row(arm_id="goal_plain", arm_role="baseline", feature=8, reward=0)
    silent = _envelope(terminal, record_kind="experiment_board_row", key="run-terminal")
    with pytest.raises(ValueError, match="supersession"):
        simulate_benchmark_upload(store, silent, execute=True)
    corrected = _envelope(
        terminal,
        record_kind="experiment_board_row",
        key="run-terminal",
        supersedes=first["record_id"],
    )
    simulate_benchmark_upload(store, corrected, execute=True)
    rewrite = copy.deepcopy(terminal)
    rewrite["status"] = "cancelled"
    rewrite["metrics"] = {}
    rewrite["countability"] = {
        "integrity_qualified": False,
        "official_result_present": False,
        "score_countable": False,
    }
    rewrite["insight"] = {"status": "not_required"}
    illegal = _envelope(
        rewrite,
        record_kind="experiment_board_row",
        key="run-rewrite",
        supersedes=corrected["record_id"],
    )
    with pytest.raises(ValueError, match="cannot move"):
        simulate_benchmark_upload(store, illegal, execute=True)


def test_case_insight_supersession_preserves_run_identity(tmp_path: Path) -> None:
    store = tmp_path / "simulated-upload.jsonl"
    _upload_default_insight_run(store)
    first = _envelope(
        _insight(), record_kind="case_insight_projection", key="insight-1"
    )
    simulate_benchmark_upload(store, first, execute=True)
    changed = _insight()
    changed["run_id"] = "another-run"
    replacement = _envelope(
        changed,
        record_kind="case_insight_projection",
        key="insight-2",
        supersedes=first["record_id"],
    )

    with pytest.raises(ValueError, match="preserve case and run identity"):
        simulate_benchmark_upload(store, replacement, execute=True)


def test_supersession_preserves_producer_and_immutable_study_identity(
    tmp_path: Path,
) -> None:
    store = tmp_path / "simulated-upload.jsonl"
    first = _envelope(_manifest(), record_kind="study_manifest", key="manifest-v1")
    simulate_benchmark_upload(store, first, execute=True)

    changed = _manifest()
    changed["labels"] = {"title": "Changed public title"}
    replacement = _envelope(
        changed,
        record_kind="study_manifest",
        key="manifest-v2",
        supersedes=first["record_id"],
    )
    with pytest.raises(ValueError, match="immutable"):
        simulate_benchmark_upload(store, replacement, execute=True)
    duplicate_identity = _envelope(
        _manifest(), record_kind="study_manifest", key="manifest-v3"
    )
    with pytest.raises(ValueError, match="immutable"):
        simulate_benchmark_upload(store, duplicate_identity, execute=True)

    insight = _envelope(
        _insight(), record_kind="case_insight_projection", key="insight-v1"
    )
    _upload_default_insight_run(store)
    simulate_benchmark_upload(store, insight, execute=True)
    other_producer = build_benchmark_upload_envelope(
        _insight(),
        record_kind="case_insight_projection",
        producer_id="another-adapter",
        producer_version="1.0.0",
        benchmark_id="fixture-swe@1",
        study_id="paired-v1",
        idempotency_key="insight-v2",
        observed_at="2026-09-02T12:00:00+00:00",
        source_revision="0123456789abcdef",
        supersedes_record_id=insight["record_id"],
    )
    with pytest.raises(ValueError, match="identity"):
        simulate_benchmark_upload(store, other_producer, execute=True)


def test_redacted_insight_rejects_raw_fields_and_path_references() -> None:
    insight = _insight()
    insight["raw_trajectory"] = "private solver text"
    with pytest.raises(ValueError, match="unsupported fields"):
        normalize_benchmark_case_insight_projection(insight)
    insight = _insight()
    insight["evidence_refs"] = ["/tmp/private/trajectory.json"]
    with pytest.raises(ValueError, match="public-safe token"):
        normalize_benchmark_case_insight_projection(insight)
    insight = _insight(outcome_status="running")
    with pytest.raises(ValueError, match="must be terminal"):
        normalize_benchmark_case_insight_projection(insight)


def test_case_insight_upload_requires_exact_terminal_run(tmp_path: Path) -> None:
    store = tmp_path / "simulated-upload.jsonl"
    insight = _envelope(
        _insight(), record_kind="case_insight_projection", key="insight-v1"
    )
    with pytest.raises(ValueError, match="exact-run board record"):
        simulate_benchmark_upload(store, insight)

    running = _row(
        arm_id="loopx_plain",
        arm_role="treatment",
        feature=0,
        reward=0,
        anchor="goal_plain-case-1",
    )
    running.update(
        {
            "status": "running",
            "metrics": {},
            "countability": {
                "integrity_qualified": False,
                "official_result_present": False,
                "score_countable": False,
            },
            "treatment_fidelity": "pending",
            "effort": {},
            "insight": {"status": "pending"},
        }
    )
    running_envelope = _envelope(
        running, record_kind="experiment_board_row", key="run-start"
    )
    simulate_benchmark_upload(store, running_envelope, execute=True)
    with pytest.raises(ValueError, match="requires a terminal run"):
        simulate_benchmark_upload(store, insight)

    terminal_envelope = _envelope(
        _row(
            arm_id="loopx_plain",
            arm_role="treatment",
            feature=9,
            reward=1,
            anchor="goal_plain-case-1",
        ),
        record_kind="experiment_board_row",
        key="run-terminal",
        supersedes=running_envelope["record_id"],
    )
    simulate_benchmark_upload(store, terminal_envelope, execute=True)
    accepted = simulate_benchmark_upload(store, insight, execute=True)
    assert accepted["disposition"] == "accepted"

    invalidated_run = copy.deepcopy(terminal_envelope["payload"])
    invalidated_run["insight"] = {"status": "not_required"}
    invalidation = _envelope(
        invalidated_run,
        record_kind="experiment_board_row",
        key="run-without-insight",
        supersedes=terminal_envelope["record_id"],
    )
    with pytest.raises(ValueError, match="complete post-run analysis"):
        simulate_benchmark_upload(store, invalidation)

    mismatch = _envelope(
        _insight(outcome_status="cancelled"),
        record_kind="case_insight_projection",
        key="insight-mismatch",
    )
    with pytest.raises(ValueError, match="outcome does not match"):
        simulate_benchmark_upload(store, mismatch)


@pytest.mark.parametrize("status", ["runner_invalid", "cancelled"])
def test_case_insight_upload_accepts_non_score_terminal_outcomes(
    tmp_path: Path, status: str
) -> None:
    store = tmp_path / f"{status}.jsonl"
    run = _row(
        arm_id="goal_plain", arm_role="baseline", feature=0, reward=0
    )
    run.update(
        {
            "status": status,
            "metrics": {},
            "countability": {
                "integrity_qualified": False,
                "official_result_present": False,
                "score_countable": False,
            },
        }
    )
    run_envelope = _envelope(
        run, record_kind="experiment_board_row", key=f"{status}-run"
    )
    insight_envelope = _envelope(
        _insight(run_id="goal_plain-case-1", outcome_status=status),
        record_kind="case_insight_projection",
        key=f"{status}-insight",
    )
    simulate_benchmark_upload(store, run_envelope, execute=True)
    assert simulate_benchmark_upload(store, insight_envelope, execute=True)[
        "disposition"
    ] == "accepted"


def test_dashboard_exposes_denominators_native_metrics_and_existing_comparisons() -> (
    None
):
    baseline = _row(arm_id="goal_plain", arm_role="baseline", feature=6, reward=0)
    treatment = _row(
        arm_id="loopx_plain",
        arm_role="treatment",
        feature=9,
        reward=1,
        anchor="goal_plain-case-1",
    )
    records = [
        _envelope(baseline, record_kind="experiment_board_row", key="baseline"),
        _envelope(treatment, record_kind="experiment_board_row", key="treatment"),
        _envelope(_insight(), record_kind="case_insight_projection", key="insight"),
    ]
    dashboard = build_benchmark_study_dashboard(_manifest(), records)
    assert dashboard["status"] == "provisional"
    assert dashboard["campaign"]["intended_cell_denominator"] == 4
    assert dashboard["campaign"]["selected_score_countable_cell_count"] == 2
    assert dashboard["campaign"]["matched_pair_countable_count"] == 1
    assert dashboard["contrasts"]["loopx_plain"] == {
        "matched_pair_denominator": 1,
        "primary_metric_directions": {"improved": 1, "flat": 0, "regressed": 0},
        "binary_metric_transitions": {"reward": {"0_to_1": 1, "1_to_0": 0, "same": 0}},
    }
    loopx_arm = next(arm for arm in dashboard["arms"] if arm["arm_id"] == "loopx_plain")
    assert loopx_arm["metrics"]["requirements_passed"]["suite_micro_rate"] == 0.9
    assert loopx_arm["binary_outcomes"]["reward"]["success_rate"] == 1
    assert loopx_arm["effort"]["duration_ms"]["median"] == 3600000
    assert dashboard["cases"][0]["largest_eligible_primary_contrast"] is not None
    assert dashboard["runs"][1]["redacted_insight"]["confidence"] == "high"
    assert dashboard["runs"][1]["upload_provenance"]["producer_id"] == "fixture-adapter"
    assert dashboard["authority"]["manifest_changes_scores"] is False


def test_dashboard_rejects_a_store_manifest_from_another_design() -> None:
    records = [_envelope(_manifest(), record_kind="study_manifest", key="manifest-v1")]
    changed = _manifest()
    changed["labels"] = {"title": "A different declared study"}

    with pytest.raises(ValueError, match="manifest does not match"):
        build_benchmark_study_dashboard(changed, records)


def test_four_arm_dashboard_reuses_factorial_authority() -> None:
    rows = [
        _row(
            arm_id="goal_plain",
            arm_role="baseline",
            feature=5,
            reward=0,
            study_id="factorial-v1",
        ),
        _row(
            arm_id="loopx_plain",
            arm_role="treatment",
            feature=7,
            reward=0,
            study_id="factorial-v1",
            anchor="goal_plain-case-1",
        ),
        _row(
            arm_id="goal_swe",
            arm_role="control",
            feature=6,
            reward=0,
            study_id="factorial-v1",
            anchor="goal_plain-case-1",
        ),
        _row(
            arm_id="loopx_swe",
            arm_role="treatment",
            feature=10,
            reward=1,
            study_id="factorial-v1",
            anchor="goal_swe-case-1",
        ),
    ]
    records = [
        _envelope(
            row,
            record_kind="experiment_board_row",
            key=f"row-{index}",
            study_id="factorial-v1",
        )
        for index, row in enumerate(rows)
    ]
    records.append(
        _envelope(
            _insight(study_id="factorial-v1"),
            record_kind="case_insight_projection",
            key="factorial-insight",
            study_id="factorial-v1",
        )
    )
    dashboard = build_benchmark_study_dashboard(
        _manifest(four_arm=True),
        records,
        four_arm_contract=_four_arm_contract(),
    )
    assert dashboard["campaign"]["factorial_contrast_count"] == 1
    assert dashboard["campaign"]["factorial_contrast_countable_count"] == 1
    assert (
        dashboard["factorial_contrasts"][0]["interaction_contrast"]["metric_contrasts"][
            "requirements_passed"
        ]["difference_in_differences"]
        == 2
    )
    assert next(
        run
        for run in dashboard["runs"]
        if run["run_id"] == "loopx_plain-case-1"
    )["redacted_insight"]["confidence"] == "high"


def test_cli_local_simulation_flow(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    payload_path = tmp_path / "payload.json"
    envelope_path = tmp_path / "envelope.json"
    store = tmp_path / "store.jsonl"
    manifest_path.write_text(json.dumps(_five_mode_manifest()), encoding="utf-8")
    baseline = _row(
        arm_id="plain",
        arm_role="baseline",
        feature=6,
        reward=0,
        study_id="five-mode-v1",
    )
    payload_path.write_text(json.dumps(baseline), encoding="utf-8")

    validate = _loopx_json(
        "benchmark",
        "study-validate",
        "--manifest-json",
        str(manifest_path),
        "--format",
        "json",
    )
    assert validate["schema_version"] == BENCHMARK_STUDY_MANIFEST_SCHEMA_VERSION
    envelope = _loopx_json(
        "benchmark",
        "upload-envelope",
        "--payload-json",
        str(payload_path),
        "--record-kind",
        "experiment_board_row",
        "--producer-id",
        "fixture-adapter",
        "--producer-version",
        "1.0.0",
        "--benchmark-id",
        "fixture-swe@1",
        "--study-id",
        "five-mode-v1",
        "--idempotency-key",
        "baseline-case-1",
        "--observed-at",
        "2026-09-02T12:00:00+00:00",
        "--source-revision",
        "0123456789abcdef",
        "--format",
        "json",
    )
    envelope_path.write_text(json.dumps(envelope), encoding="utf-8")
    upload_args = [
        "benchmark",
        "upload-local",
        "--envelope-json",
        str(envelope_path),
        "--store",
        str(store),
        "--format",
        "json",
    ]
    assert _loopx_json(*upload_args)["write_performed"] is False
    _loopx_json(*upload_args, "--execute")
    readback = _loopx_json(
        "benchmark",
        "upload-readback",
        "--store",
        str(store),
        "--record-id",
        str(envelope["record_id"]),
        "--format",
        "json",
    )
    assert readback["disposition"] == "readback_verified"

    payload_path.write_text(
        json.dumps(_insight(study_id="five-mode-v1", run_id="plain-case-1")),
        encoding="utf-8",
    )
    insight_envelope = _loopx_json(
        "benchmark",
        "upload-envelope",
        "--payload-json",
        str(payload_path),
        "--record-kind",
        "case_insight_projection",
        "--producer-id",
        "fixture-adapter",
        "--producer-version",
        "1.0.0",
        "--benchmark-id",
        "fixture-swe@1",
        "--study-id",
        "five-mode-v1",
        "--idempotency-key",
        "insight-case-1",
        "--observed-at",
        "2026-09-02T12:01:00+00:00",
        "--source-revision",
        "0123456789abcdef",
        "--format",
        "json",
    )
    envelope_path.write_text(json.dumps(insight_envelope), encoding="utf-8")
    assert _loopx_json(*upload_args)["disposition"] == "preview_accepted"
    _loopx_json(*upload_args, "--execute")
    insight_readback = _loopx_json(
        "benchmark",
        "upload-readback",
        "--store",
        str(store),
        "--record-id",
        str(insight_envelope["record_id"]),
        "--format",
        "json",
    )
    assert insight_readback["payload_digest"] == insight_envelope["payload_digest"]
    packet = _loopx_json(
        "benchmark",
        "study-dashboard",
        "--manifest-json",
        str(manifest_path),
        "--store",
        str(store),
        "--format",
        "json",
    )
    assert packet["campaign"]["intended_arm_count"] == 5
    assert packet["campaign"]["selected_score_countable_cell_count"] == 1
    assert packet["runs"][0]["redacted_insight"]["confidence"] == "high"
    assert packet["network_access_performed"] is False


def test_behavior_findings_do_not_change_study_score_projection():
    manifest = _manifest()
    run = _envelope(
        _row(arm_id="goal_plain", arm_role="baseline", feature=5, reward=0),
        record_kind="experiment_board_row", key="baseline",
    )
    observation = json.loads(
        (REPO_ROOT / "examples/benchmark-behavior-finding.json").read_text()
    )
    observation.update(benchmark_id=manifest["benchmark_id"], study_id=manifest["study_id"])
    finding = _envelope(observation, record_kind="behavior_finding", key="finding")
    before = build_benchmark_study_dashboard(manifest, [run])
    after = build_benchmark_study_dashboard(manifest, [run, finding])
    assert before == after


def test_dashboard_projects_runtime_observations_into_the_campaign_block() -> None:
    """RFC §7: the runtime-observation record kind must reach the campaign block.

    The projection reads these counts straight off observations, so a payload
    naming a classification outside ``BenchmarkRuntimeClassification``, or a
    factorial count that disagrees with the list beside it, cannot come from this
    reducer. Both invariants are pinned here rather than asserted in prose.
    """

    from loopx.capabilities.benchmark_toolkit.runtime_observation import (
        BenchmarkRuntimeClassification,
    )

    baseline = _row(arm_id="goal_plain", arm_role="baseline", feature=6, reward=0)
    treatment = _row(
        arm_id="loopx_plain",
        arm_role="treatment",
        feature=9,
        reward=1,
        anchor="goal_plain-case-1",
    )
    observations = [
        build_benchmark_runtime_observation(
            admission_active=True,
            job_receipt_state="resolved",
            runner_owner_state="alive",
        ),
        build_benchmark_runtime_observation(
            admission_active=True,
            job_receipt_state="resolved",
            runner_owner_state="absent_after_grace",
            terminal_result_present=True,
        ),
        build_benchmark_runtime_observation(
            admission_active=False,
            job_receipt_state="ambiguous",
            runner_owner_state="unknown",
        ),
    ]
    dashboard = build_benchmark_study_dashboard(
        _manifest(),
        [
            _envelope(baseline, record_kind="experiment_board_row", key="baseline"),
            _envelope(treatment, record_kind="experiment_board_row", key="treatment"),
            _envelope(_insight(), record_kind="case_insight_projection", key="insight"),
        ]
        + [
            _envelope(
                observation,
                record_kind="runtime_observation",
                key=f"observation-{index}",
            )
            for index, observation in enumerate(observations)
        ],
    )
    campaign = dashboard["campaign"]

    assert campaign["runtime_observation_count"] == 3
    counts = campaign["runtime_classification_counts"]
    assert counts == {
        "not_admitted": 1,
        "running_qualified": 1,
        "terminal_pending_reconcile": 1,
    }
    assert set(counts) <= {item.value for item in BenchmarkRuntimeClassification}
    assert sum(counts.values()) == campaign["runtime_observation_count"]
    assert campaign["factorial_contrast_count"] == len(dashboard["factorial_contrasts"])
    assert (
        campaign["factorial_contrast_countable_count"]
        <= campaign["factorial_contrast_count"]
    )
    assert dashboard["authority"]["factorial_comparison_source"] == (
        dashboard["factorial_contrasts"][0]["schema_version"]
        if dashboard["factorial_contrasts"]
        else None
    )
