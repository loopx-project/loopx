"""Tier-0 (<15s deterministic Base vs Head differential) microbenchmark runner."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import runpy
import subprocess
import tempfile
import time
from typing import Any

from loopx.control_plane.testing.cli_output_differential import (
    compare_cli_output_receipts,
    select_cli_output_base_ref,
)
from loopx.control_plane.testing.cli_output_differential_allowances import (
    CLI_OUTPUT_FIXTURE_CONTRACT_VERSION,
    CLI_OUTPUT_PROBE_SCHEMA_VERSION,
)

from .metrics import (
    MICROBENCH_DIFF_SCHEMA_VERSION,
    BehaviorConstraintResult,
    EfficiencyMetrics,
    assert_public_safe_payload,
    build_scenario_source_attribution,
    compare_base_vs_head_efficiency,
    summarize_artifact_boundary,
    summarize_claim_scope,
)
from .scenarios import (
    MICROBENCH_SCENARIO_IDS,
    prepare_m1_fixture,
    prepare_m2_fixture,
    prepare_m3_fixture,
    run_m1_deterministic_arm,
    run_m2_deterministic_arm,
    run_m3_deterministic_arm,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DIFFERENTIAL_SMOKE_PATH = (
    REPO_ROOT / "examples" / "control_plane" / "cli-output-base-head-differential-smoke.py"
)


def _differential_smoke_helpers() -> dict[str, Any]:
    return runpy.run_path(str(DIFFERENTIAL_SMOKE_PATH))


def _resolve_pinned_base_ref(base_ref: str, *, main_ref: str = "origin/main") -> str:
    helpers = _differential_smoke_helpers()
    selected = select_cli_output_base_ref(
        base_ref,
        main_ref=main_ref,
        is_ancestor=helpers["_is_ancestor"],
    )
    return str(helpers["_pin_probe_base_ref"](selected, main_ref=main_ref))


def _head_commit() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _synthetic_probe_row(
    *,
    row_id: str,
    scenario: str,
    eff: EfficiencyMetrics,
    behavior: BehaviorConstraintResult,
) -> dict[str, Any]:
    wire_chars = int(eff.wire_bytes or 0)
    return {
        "row_id": row_id,
        "surface_id": "quota_should_run",
        "scenario": scenario,
        "format": "json",
        "variant_id": None,
        "qualification_policy": "absolute_hot_path",
        "chars": wire_chars,
        "utf8_bytes": wire_chars,
        "lines": 1,
        "compact_payload_chars": wire_chars,
        "output_contract_version": "loopx_turn_envelope_v1",
        "controls_sha256": "microbench-behavior-pass" if behavior.passed else "microbench-behavior-fail",
        "microbench_behavior_passed": behavior.passed,
    }


def run_tier0_differential(
    *,
    scenarios: Sequence[str] | None = None,
    base_ref: str | None = None,
    main_ref: str = "origin/main",
    include_cli_output_differential: bool = False,
) -> dict[str, Any]:
    """Run deterministic Base vs Head Tier-0 M1/M2/M3 differential checks (<15s target)."""
    started = time.monotonic()
    selected = tuple(scenarios) if scenarios else MICROBENCH_SCENARIO_IDS
    unknown = sorted(set(selected) - set(MICROBENCH_SCENARIO_IDS))
    if unknown:
        raise ValueError(f"unknown microbench scenarios: {', '.join(unknown)}")

    head_sha = _head_commit()
    resolved_base_sha: str | None = None
    use_external_base = False
    if base_ref:
        resolved_base_sha = _resolve_pinned_base_ref(base_ref, main_ref=main_ref)
        use_external_base = resolved_base_sha != head_sha

    results: list[dict[str, Any]] = []
    probe_base_rows: list[dict[str, Any]] = []
    probe_head_rows: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="loopx-microbench-t0-") as temp:
        temp_root = Path(temp).resolve()
        base_source_root: Path | None = None
        if use_external_base and resolved_base_sha is not None:
            helpers = _differential_smoke_helpers()
            base_source_root = temp_root / "base-src"
            helpers["_materialize_base_source"](resolved_base_sha, base_source_root)

        def _run_m1_arm_pair(arm_name: str, src_root: Path | None) -> dict[str, Any]:
            fix = prepare_m1_fixture(temp_root / f"m1_{arm_name}", source_root=src_root)
            return run_m1_deterministic_arm(fix, arm=arm_name, source_root=src_root)

        def _run_m2_pair() -> dict[str, Any]:
            m2_root = temp_root / "m2"
            m2_fix = prepare_m2_fixture(m2_root)
            m2_base = run_m2_deterministic_arm(m2_fix, arm="base")
            m2_head = run_m2_deterministic_arm(m2_fix, arm="head")
            m2_cmp = compare_base_vs_head_efficiency(
                m2_base["efficiency"],
                m2_head["efficiency"],
                base_behavior=m2_base["behavior_constraint"],
                head_behavior=m2_head["behavior_constraint"],
            )
            m2_deltas = m2_cmp["deltas"]
            tok_delta = m2_deltas.get("prompt_tokens") if isinstance(m2_deltas, Mapping) else None
            head_ok = bool(m2_cmp["behavior_preserved"]) and isinstance(tok_delta, int) and tok_delta < 0
            hook_available = bool(m2_head.get("semantic_unit_settlement_hook_available"))
            return {
                "scenario_id": "m2_epoch_prefetch_semantic_fold",
                "passed": head_ok,
                "semantic_unit_settlement_hook_available": hook_available,
                "source_attribution": build_scenario_source_attribution(
                    base_source="candidate_head",
                    head_source="candidate_head",
                    comparison_kind="same_commit_mechanism_verification",
                    production_hot_path_covered=hook_available,
                    note=(
                        "Both M2 arms run on the candidate evaluator checkout; excluded from "
                        "cross-commit claims and production settlement hot-path claims unless "
                        "the production settlement hook is present."
                    ),
                ),
                "base": {
                    "behavior_constraint": m2_base["behavior_constraint"].to_dict(),
                    "efficiency": m2_base["efficiency"].to_dict(),
                },
                "head": {
                    "behavior_constraint": m2_head["behavior_constraint"].to_dict(),
                    "efficiency": m2_head["efficiency"].to_dict(),
                },
                "comparison": m2_cmp,
            }

        def _run_m3_pair() -> dict[str, Any]:
            m3_root_head = temp_root / "m3_head"
            m3_head_fix = prepare_m3_fixture(m3_root_head)
            m3_head = run_m3_deterministic_arm(
                m3_head_fix,
                arm="head",
                source_root=None,
            )
            if base_source_root is not None:
                m3_root_base = temp_root / "m3_base"
                m3_base_fix = prepare_m3_fixture(m3_root_base)
                m3_base = run_m3_deterministic_arm(
                    m3_base_fix,
                    arm="base",
                    source_root=base_source_root,
                )
            else:
                m3_base = m3_head
            m3_cmp = compare_base_vs_head_efficiency(
                m3_base["efficiency"],
                m3_head["efficiency"],
                base_behavior=m3_base["behavior_constraint"],
                head_behavior=m3_head["behavior_constraint"],
            )
            m3_cross = base_source_root is not None
            return {
                "scenario_id": "m3_claim_block_settle_invariants",
                "passed": bool(m3_cmp["behavior_preserved"]) and m3_head["behavior_constraint"].passed,
                "source_attribution": build_scenario_source_attribution(
                    base_source="external_base_checkout" if m3_cross else "candidate_head",
                    head_source="candidate_head",
                    comparison_kind=(
                        "cross_commit_differential" if m3_cross else "same_commit_arm_contrast"
                    ),
                    production_hot_path_covered=True,
                ),
                "base": {
                    "behavior_constraint": m3_base["behavior_constraint"].to_dict(),
                    "efficiency": m3_base["efficiency"].to_dict(),
                },
                "head": {
                    "behavior_constraint": m3_head["behavior_constraint"].to_dict(),
                    "efficiency": m3_head["efficiency"].to_dict(),
                },
                "comparison": m3_cmp,
            }

        with ThreadPoolExecutor(max_workers=4) as pool:
            f_m1_base = (
                pool.submit(_run_m1_arm_pair, "base", base_source_root)
                if "m1_envelope_scoped_recovery" in selected
                else None
            )
            f_m1_head = (
                pool.submit(_run_m1_arm_pair, "head", None)
                if "m1_envelope_scoped_recovery" in selected
                else None
            )
            f_m2 = (
                pool.submit(_run_m2_pair)
                if "m2_epoch_prefetch_semantic_fold" in selected
                else None
            )
            f_m3 = (
                pool.submit(_run_m3_pair)
                if "m3_claim_block_settle_invariants" in selected
                else None
            )
            m1_base = f_m1_base.result() if f_m1_base is not None else None
            m1_head = f_m1_head.result() if f_m1_head is not None else None
            m2_res = f_m2.result() if f_m2 is not None else None
            m3_res = f_m3.result() if f_m3 is not None else None

        if m1_base is not None and m1_head is not None:
            m1_cmp = compare_base_vs_head_efficiency(
                m1_base["efficiency"],
                m1_head["efficiency"],
                base_behavior=m1_base["behavior_constraint"],
                head_behavior=m1_head["behavior_constraint"],
            )
            # Head slim delivery (`on`) must shrink delivered wire/tokens vs `off` while keeping invariants.
            m1_deltas = m1_cmp["deltas"]
            wire_delta = m1_deltas.get("wire_bytes") if isinstance(m1_deltas, Mapping) else None
            head_ok = bool(m1_cmp["behavior_preserved"]) and isinstance(wire_delta, int) and wire_delta < 0
            m1_cross = base_source_root is not None
            results.append(
                {
                    "scenario_id": "m1_envelope_scoped_recovery",
                    "passed": head_ok,
                    "source_attribution": build_scenario_source_attribution(
                        base_source="external_base_checkout" if m1_cross else "candidate_head",
                        head_source="candidate_head",
                        comparison_kind=(
                            "cross_commit_differential" if m1_cross else "same_commit_arm_contrast"
                        ),
                        production_hot_path_covered=True,
                    ),
                    "base": {
                        "behavior_constraint": m1_base["behavior_constraint"].to_dict(),
                        "efficiency": m1_base["efficiency"].to_dict(),
                    },
                    "head": {
                        "behavior_constraint": m1_head["behavior_constraint"].to_dict(),
                        "efficiency": m1_head["efficiency"].to_dict(),
                    },
                    "comparison": m1_cmp,
                }
            )
            probe_base_rows.append(
                _synthetic_probe_row(
                    row_id="surface/quota_should_run/m1_scoped_recovery/json",
                    scenario="small",
                    eff=m1_base["efficiency"],
                    behavior=m1_base["behavior_constraint"],
                )
            )
            probe_head_rows.append(
                _synthetic_probe_row(
                    row_id="surface/quota_should_run/m1_scoped_recovery/json",
                    scenario="small",
                    eff=m1_head["efficiency"],
                    behavior=m1_head["behavior_constraint"],
                )
            )
        if m2_res is not None:
            results.append(m2_res)
        if m3_res is not None:
            results.append(m3_res)

    cli_diff_summary: dict[str, Any] | None = None
    if probe_base_rows and probe_head_rows:
        cli_diff = compare_cli_output_receipts(
            {
                "schema_version": CLI_OUTPUT_PROBE_SCHEMA_VERSION,
                "fixture_contract_version": CLI_OUTPUT_FIXTURE_CONTRACT_VERSION,
                "rows": probe_base_rows,
            },
            {
                "schema_version": CLI_OUTPUT_PROBE_SCHEMA_VERSION,
                "fixture_contract_version": CLI_OUTPUT_FIXTURE_CONTRACT_VERSION,
                "rows": probe_head_rows,
            },
        )
        cli_diff_summary = {
            "mode": "m1_probe_receipt_differential",
            "ok": cli_diff["ok"],
            "compared_row_count": cli_diff["compared_row_count"],
            "failed_row_count": cli_diff["failed_row_count"],
            "review_row_count": cli_diff["review_row_count"],
        }

    full_smoke_result: dict[str, Any] | None = None
    if include_cli_output_differential:
        completed = subprocess.run(
            [
                "python3",
                str(DIFFERENTIAL_SMOKE_PATH),
                *(["--base-ref", base_ref] if base_ref else []),
            ],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
        full_smoke_result = {
            "returncode": completed.returncode,
            "ok": completed.returncode == 0,
            "summary_line": (completed.stdout.strip().splitlines() or [""])[-1],
        }

    wall_seconds = round(time.monotonic() - started, 3)
    overall_passed = all(row["passed"] for row in results) and (
        full_smoke_result["ok"] if full_smoke_result is not None else True
    )
    claim_scope = summarize_claim_scope(results)
    payload: dict[str, Any] = {
        "schema_version": MICROBENCH_DIFF_SCHEMA_VERSION,
        "tier": "tier0_deterministic",
        "passed": overall_passed,
        "wall_seconds": wall_seconds,
        "head_commit": head_sha,
        "base_ref": resolved_base_sha or "local_arm_contrast",
        "external_base_checkout_used": bool(claim_scope["cross_commit_scenarios"]),
        "claim_scope": claim_scope,
        "scenarios": results,
        "cli_output_differential": cli_diff_summary,
        **(
            {"full_cli_output_differential_smoke": full_smoke_result}
            if full_smoke_result is not None
            else {}
        ),
        "boundary": summarize_artifact_boundary(),
    }
    assert_public_safe_payload(payload, label="tier0_microbench_report")
    return payload
