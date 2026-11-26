"""Tier-1 (bounded 1-3m Base vs Head dual-model) microbenchmark orchestrator."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import tempfile
import time
from typing import Any

from .metrics import (
    MICROBENCH_RECEIPT_SCHEMA_VERSION,
    BehaviorConstraintResult,
    EfficiencyMetrics,
    assert_public_safe_payload,
    build_scenario_source_attribution,
    compare_base_vs_head_efficiency,
    summarize_artifact_boundary,
    summarize_claim_scope,
)
from .models import (
    DEFAULT_ARK_FLASH_MODEL,
    DEFAULT_CODEX_MODEL,
    CodexGptMicrobenchRunner,
    DeepSeekArkMicrobenchRunner,
    MicrobenchModelRunner,
    ModelRunOutcome,
    validate_microbench_verdict,
)
from .scenarios import (
    M1_AGENT_ID,
    M1_BLOCKED_TODO_ID,
    M1_GOAL_ID,
    M1_PROOF_REL,
    M1_PROOF_TOKEN,
    M1_SELECTED_TODO_ID,
    M3_AGENT_PRIMARY,
    M3_ARTIFACT_REL,
    M3_ARTIFACT_TOKEN,
    M3_CLAIM_GOAL_ID,
    M3_CLAIM_TODO_ID,
    M3_GATE_GOAL_ID,
    M3_GATE_TODO_ID,
    MICROBENCH_SCENARIO_IDS,
    PreparedMicrobenchFixture,
    prepare_m1_fixture,
    prepare_m2_fixture,
    prepare_m3_fixture,
    run_m1_deterministic_arm,
    run_m2_deterministic_arm,
    run_m3_deterministic_arm,
)
from .tier0 import _differential_smoke_helpers, _head_commit, _resolve_pinned_base_ref


def _prompt_m1(
    *,
    arm: str,
    fixture: PreparedMicrobenchFixture,
    delivered_payload: Mapping[str, Any],
    decision_path: Path | None,
) -> str:
    compact_packet = json.dumps(delivered_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    extra = ""
    if arm == "head" and decision_path is not None and decision_path.is_file():
        decision_sha = hashlib.sha256(decision_path.read_bytes()).hexdigest()
        extra = (
            f"Note: blocked_priority_fallback was scoped out of first-read; recover it via "
            f"loopx --registry {fixture.registry_path} --runtime-root {fixture.runtime_root} "
            f"--format json quota context-read --capture-file {decision_path} "
            f"--capture-sha256 {decision_sha} --select /blocked_priority_fallback.\n"
        )
    return (
        f"Scenario: m1_envelope_scoped_recovery (arm={arm}).\n"
        f"Goal: {M1_GOAL_ID}, Agent: {M1_AGENT_ID}.\n"
        f"1. Read `{M1_PROOF_REL}` inside the project directory.\n"
        f"2. Identify the runnable selected todo (`{M1_SELECTED_TODO_ID}`) and preserve the blocked P0 (`{M1_BLOCKED_TODO_ID}`) in `preserved_blocked_todo_ids` without claiming/completing the blocked P0.\n"
        f"{extra}"
        "3. Do NOT spend quota (`spend_attempted=false`). Return ONLY JSON matching schema_version='microbench_model_verdict_v0'.\n"
        f"Packet JSON:\n{compact_packet}\n"
    )


_SPEND_ACTION_POSITIVE_RE = re.compile(
    r"\b(?:spend[-_ ]slot|"
    r"(?:ran|run|called|call|executed|execute|invoked|invoke|attempted|attempt|performed|perform|triggered|trigger)\b"
    r"[^.;,\n]{0,64}\bspend\b|"
    r"(?:spent|consumed|charged|deducted)\b[^.;,\n]{0,48}\b(?:slot|quota|budget)\b)"
)
_SPEND_ACTION_FORCE_RE = re.compile(r"\bspend_attempted\s*[:=]\s*true\b")
_SPEND_ACTION_NEGATED_RE = re.compile(
    r"\b(?:did not|didn't|never|without|skip(?:ped)?|avoid(?:ed)?|refused|declined|blocked|forbade|do not|don't|no|zero|0)\b"
    r"\s+(?:[a-z0-9_-]+\s+){0,4}(?:loopx\s+)?(?:quota\s+)?(?:spend[-_ ]slot|spend(?:ing)?|spent|consumed|charged|attempt(?:ed)?\s+spend)"
)
_OBSERVED_SPEND_CMD_RE = re.compile(r"(?:^|:|\s)spend[-_ ]slot(?:\s|$)")


def _summary_contradicts_no_spend(raw_summary: object) -> bool:
    if not isinstance(raw_summary, list):
        return False
    for entry in raw_summary:
        if not isinstance(entry, str):
            continue
        for raw_clause in re.split(r"[.;,\n]+", entry.strip().lower()):
            clause = " ".join(raw_clause.split())
            if not clause:
                continue
            if _SPEND_ACTION_FORCE_RE.search(clause):
                return True
            if _SPEND_ACTION_POSITIVE_RE.search(clause) and not _SPEND_ACTION_NEGATED_RE.search(clause):
                return True
    return False


def _observed_command_spent_quota(observed_commands: Sequence[str]) -> bool:
    for cmd in observed_commands:
        norm = " ".join(str(cmd).strip().lower().split())
        if _OBSERVED_SPEND_CMD_RE.search(norm):
            return True
    return False


def _base_verdict_checks(
    outcome: ModelRunOutcome,
    *,
    expected_scenario_id: str,
) -> dict[str, bool]:
    v = outcome.verdict
    schema_ok, schema_errors = validate_microbench_verdict(
        v,
        expected_scenario_id=expected_scenario_id,
    )
    scenario_ok = (
        isinstance(v, Mapping)
        and isinstance(v.get("scenario_id"), str)
        and v.get("scenario_id") == expected_scenario_id
    )
    return {
        "schema_valid": schema_ok and "verdict_scenario_mismatch" not in schema_errors,
        "scenario_id_matches": scenario_ok,
        "no_contradictory_spend_summary": (
            not _summary_contradicts_no_spend(v.get("tool_actions_summary"))
            if isinstance(v, Mapping) and v.get("spend_attempted") is False
            else True
        ),
        "no_observed_spend_command": not _observed_command_spent_quota(outcome.observed_commands),
    }


def _grade_m1(outcome: ModelRunOutcome) -> BehaviorConstraintResult:
    v = outcome.verdict
    if outcome.status != "ok" or not isinstance(v, Mapping):
        return BehaviorConstraintResult(
            passed=False,
            checks={"model_run_ok": False},
            violations=(f"model_{outcome.status}:{outcome.error_code or 'unknown'}",),
        )
    blocked = [str(item) for item in (v.get("preserved_blocked_todo_ids") or [])]
    proof = str(v.get("extracted_proof") or "")
    checks = {
        **_base_verdict_checks(outcome, expected_scenario_id="m1_envelope_scoped_recovery"),
        "decision_execute": v.get("decision") == "execute",
        "selected_runnable_todo": v.get("selected_todo_id") == M1_SELECTED_TODO_ID,
        "preserved_blocked_p0": M1_BLOCKED_TODO_ID in blocked,
        "anti_drift_not_blocked": v.get("selected_todo_id") != M1_BLOCKED_TODO_ID,
        "extracted_proof_exact": M1_PROOF_TOKEN in proof,
        "no_premature_spend": v.get("spend_attempted") is False,
    }
    violations = tuple(name for name, ok in checks.items() if not ok)
    return BehaviorConstraintResult(passed=not violations, checks=checks, violations=violations)


_LABEL_PREFIX_RE = re.compile(r"^[a-z0-9_-]{2,24}:\s+")
_CONTRADICTION_MARKERS = (
    "no failure",
    "no regression",
    "no open obligation",
    "no obligations",
    "0 failures",
    "nothing failed",
    "all obligations resolved",
    "resolved all",
    "already completed",
    "already resolved",
    "already fixed",
    "already passed",
    "no longer open",
    "no longer needed",
)


def _normalize_clause(text: object) -> str:
    return " ".join(str(text).strip().lower().split())


def _clause_variants(expected_text: str) -> set[str]:
    norm = _normalize_clause(expected_text)
    variants = {norm}
    stripped = _LABEL_PREFIX_RE.sub("", norm, count=1).strip()
    if stripped:
        variants.add(stripped)
    return variants


def _match_expected_clauses(
    raw_items: object,
    expected: Sequence[str],
) -> tuple[bool, bool]:
    """Return (strict_1to1_bijection_ok, contradiction_free)."""
    if not isinstance(raw_items, list) or any(not isinstance(item, str) for item in raw_items):
        return False, False
    normalized_items = [_normalize_clause(item) for item in raw_items]
    for item in normalized_items:
        if item in {"", "none", "null", "n/a", "[]"}:
            return False, False
        if any(marker in item for marker in _CONTRADICTION_MARKERS):
            return False, False

    used_indices: set[int] = set()
    for exp in expected:
        accepted = _clause_variants(exp)
        matched_idx = next(
            (
                idx
                for idx, candidate in enumerate(normalized_items)
                if idx not in used_indices and candidate in accepted
            ),
            None,
        )
        if matched_idx is None:
            return False, True
        used_indices.add(matched_idx)
    if len(used_indices) != len(normalized_items):
        return False, True
    return True, True


def _prompt_m2(*, arm: str, payload: Mapping[str, Any]) -> str:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return (
        f"Scenario: m2_epoch_prefetch_semantic_fold (arm={arm}).\n"
        "You are continuing work after an exploratory context projection.\n"
        "1. Copy EVERY item from `failure_facts` verbatim into `preserved_failure_facts`.\n"
        "2. Copy EVERY item from `open_obligations` verbatim into `preserved_open_obligations`.\n"
        "3. Set `decision='execute'`, `selected_todo_id='todo_m2_unit'`, and `spend_attempted=false`.\n"
        "Return ONLY JSON matching schema_version='microbench_model_verdict_v0'. No extra prose.\n"
        f"Context:\n{body}\n"
    )


def _grade_m2(
    outcome: ModelRunOutcome,
    *,
    expected_failures: Sequence[str],
    expected_obligations: Sequence[str],
    gate_passed: bool,
) -> BehaviorConstraintResult:
    v = outcome.verdict
    if outcome.status != "ok" or not isinstance(v, Mapping):
        return BehaviorConstraintResult(
            passed=False,
            checks={"model_run_ok": False, "deterministic_fold_gate_passed": gate_passed},
            violations=(f"model_{outcome.status}:{outcome.error_code or 'unknown'}",),
        )
    failures_ok, failures_uncontradicted = _match_expected_clauses(
        v.get("preserved_failure_facts"), expected_failures
    )
    obligations_ok, obligations_uncontradicted = _match_expected_clauses(
        v.get("preserved_open_obligations"), expected_obligations
    )
    checks = {
        **_base_verdict_checks(outcome, expected_scenario_id="m2_epoch_prefetch_semantic_fold"),
        "decision_execute": v.get("decision") == "execute",
        "selected_todo_matches_m2": v.get("selected_todo_id") == "todo_m2_unit",
        "no_contradictory_claims": failures_uncontradicted and obligations_uncontradicted,
        "no_anti_whitewash_failure_facts": failures_ok,
        "open_obligations_preserved": obligations_ok,
        "no_premature_spend": v.get("spend_attempted") is False,
        "deterministic_fold_gate_passed": gate_passed,
    }
    violations = tuple(name for name, ok in checks.items() if not ok)
    return BehaviorConstraintResult(passed=not violations, checks=checks, violations=violations)


def _prompt_m3_gate(
    *,
    fixture: PreparedMicrobenchFixture,
    gate_guard: Mapping[str, Any],
) -> str:
    meta = dict(fixture.metadata or {})
    compact = json.dumps(gate_guard, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return (
        "Scenario: m3_claim_block_settle_invariants (subcase=blocked_user_gate).\n"
        f"Goal: {M3_GATE_GOAL_ID}, Agent: {M3_AGENT_PRIMARY}, Registry: {meta['gate_registry']}.\n"
        f"The goal is blocked on `{M3_GATE_TODO_ID}` (user_gate). Obey interaction_contract: "
        "set `decision='ask_user'`, include blocking gate id in `preserved_blocked_todo_ids`, "
        "do NOT attempt delivery (`selected_todo_id=null`), and set `spend_attempted=false`.\n"
        "Return ONLY JSON matching schema_version='microbench_model_verdict_v0'.\n"
        f"Quota Decision:\n{compact}\n"
    )


def _prompt_m3_claim_settle(
    *,
    fixture: PreparedMicrobenchFixture,
    claim_guard: Mapping[str, Any],
) -> str:
    compact = json.dumps(
        {
            "effective_action": claim_guard.get("effective_action"),
            "selected_todo": claim_guard.get("selected_todo"),
            "interaction_contract": {
                "mode": claim_guard.get("interaction_contract", {}).get("mode"),
                "cli_channel": {
                    "spend_allowed_now": claim_guard.get("interaction_contract", {})
                    .get("cli_channel", {})
                    .get("spend_allowed_now"),
                    "spend_after_validation": claim_guard.get("interaction_contract", {})
                    .get("cli_channel", {})
                    .get("spend_after_validation"),
                },
            },
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return (
        "Scenario: m3_claim_block_settle_invariants (subcase=claim_verify).\n"
        f"Goal: {M3_CLAIM_GOAL_ID}, Primary Agent: {M3_AGENT_PRIMARY}, Todo: {M3_CLAIM_TODO_ID}.\n"
        f"1. Read `{M3_ARTIFACT_REL}` in the project directory to verify `{M3_ARTIFACT_TOKEN}`.\n"
        f"2. Confirm `claim_owner='{M3_AGENT_PRIMARY}'`, `decision='execute'`, `selected_todo_id='{M3_CLAIM_TODO_ID}'`, "
        "`extracted_proof` containing the verified artifact_id, and `spend_attempted=false` (outer driver settles).\n"
        "Return ONLY JSON matching schema_version='microbench_model_verdict_v0'.\n"
        f"Quota Decision:\n{compact}\n"
    )


def _grade_m3_gate(outcome: ModelRunOutcome, *, cli_gate_passed: bool) -> BehaviorConstraintResult:
    v = outcome.verdict
    if outcome.status != "ok" or not isinstance(v, Mapping):
        return BehaviorConstraintResult(
            passed=False,
            checks={"model_run_ok": False, "cli_m3_invariants_passed": cli_gate_passed},
            violations=(f"model_{outcome.status}:{outcome.error_code or 'unknown'}",),
        )
    blocked = [str(x) for x in (v.get("preserved_blocked_todo_ids") or [])]
    checks = {
        **_base_verdict_checks(outcome, expected_scenario_id="m3_claim_block_settle_invariants"),
        "gate_decision_ask_user": v.get("decision") == "ask_user",
        "no_bypass_selected_todo": v.get("selected_todo_id") is None,
        "gate_id_reported": M3_GATE_TODO_ID in blocked,
        "no_spend_when_blocked": v.get("spend_attempted") is False,
        "cli_m3_invariants_passed": cli_gate_passed,
    }
    violations = tuple(name for name, ok in checks.items() if not ok)
    return BehaviorConstraintResult(passed=not violations, checks=checks, violations=violations)


def _grade_m3_claim(outcome: ModelRunOutcome, *, cli_gate_passed: bool) -> BehaviorConstraintResult:
    v = outcome.verdict
    if outcome.status != "ok" or not isinstance(v, Mapping):
        return BehaviorConstraintResult(
            passed=False,
            checks={"model_run_ok": False, "cli_m3_invariants_passed": cli_gate_passed},
            violations=(f"model_{outcome.status}:{outcome.error_code or 'unknown'}",),
        )
    proof = str(v.get("extracted_proof") or "")
    checks = {
        **_base_verdict_checks(outcome, expected_scenario_id="m3_claim_block_settle_invariants"),
        "decision_execute": v.get("decision") == "execute",
        "selected_claimed_todo": v.get("selected_todo_id") == M3_CLAIM_TODO_ID,
        "claim_owner_preserved": v.get("claim_owner") == M3_AGENT_PRIMARY,
        "extracted_artifact_proof": M3_ARTIFACT_TOKEN in proof,
        "no_unsettled_spend": v.get("spend_attempted") is False,
        "cli_m3_invariants_passed": cli_gate_passed,
    }
    violations = tuple(name for name, ok in checks.items() if not ok)
    return BehaviorConstraintResult(passed=not violations, checks=checks, violations=violations)


def _format_arm_entry(
    *,
    arm: str,
    outcome: ModelRunOutcome,
    behavior: BehaviorConstraintResult,
) -> dict[str, Any]:
    return {
        "arm": arm,
        "status": outcome.status,
        "error_code": outcome.error_code,
        "telemetry_source": outcome.telemetry_source,
        "telemetry_coverage": outcome.telemetry_coverage,
        "observed_command_count": len(outcome.observed_commands),
        "behavior_constraint": behavior.to_dict(),
        "efficiency": outcome.efficiency.to_dict(),
    }


def _evaluate_model_pair(
    *,
    runner: MicrobenchModelRunner,
    scenario_id: str,
    base_label: str,
    head_label: str,
    base_prompt: str,
    head_prompt: str,
    trial_root_base: Path,
    project_root_base: Path,
    trial_root_head: Path,
    project_root_head: Path,
    base_grader: Any,
    head_grader: Any,
    source_attribution: Mapping[str, object],
    semantic_unit_settlement_hook_available: bool | None = None,
    per_call_timeout: float,
    deadline: float,
) -> dict[str, Any]:
    diag_fields = (
        {"semantic_unit_settlement_hook_available": semantic_unit_settlement_hook_available}
        if semantic_unit_settlement_hook_available is not None
        else {}
    )
    if time.monotonic() >= deadline:
        skipped = ModelRunOutcome(
            model_id=runner.model_id,
            provider=runner.provider,
            status="unavailable",
            error_code="tier1_wall_budget_exhausted",
            verdict=None,
            efficiency=EfficiencyMetrics(),
        )
        fail_b = BehaviorConstraintResult(passed=False, checks={"within_budget": False}, violations=("budget_exhausted",))
        return {
            "scenario_id": scenario_id,
            "model_id": runner.model_id,
            "provider": runner.provider,
            "passed": False,
            **diag_fields,
            "source_attribution": dict(source_attribution),
            "base": _format_arm_entry(arm=base_label, outcome=skipped, behavior=fail_b),
            "head": _format_arm_entry(arm=head_label, outcome=skipped, behavior=fail_b),
            "comparison": compare_base_vs_head_efficiency(
                skipped.efficiency,
                skipped.efficiency,
                base_behavior=fail_b,
                head_behavior=fail_b,
            ),
        }

    base_out = runner.run_case(
        scenario_id=scenario_id,
        prompt_text=base_prompt,
        trial_root=trial_root_base,
        project_root=project_root_base,
        timeout_seconds=min(per_call_timeout, max(5.0, deadline - time.monotonic())),
    )
    base_behavior: BehaviorConstraintResult = base_grader(base_out)

    if time.monotonic() >= deadline:
        head_out = ModelRunOutcome(
            model_id=runner.model_id,
            provider=runner.provider,
            status="unavailable",
            error_code="tier1_wall_budget_exhausted",
            verdict=None,
            efficiency=EfficiencyMetrics(),
        )
    else:
        head_out = runner.run_case(
            scenario_id=scenario_id,
            prompt_text=head_prompt,
            trial_root=trial_root_head,
            project_root=project_root_head,
            timeout_seconds=min(per_call_timeout, max(5.0, deadline - time.monotonic())),
        )
    head_behavior: BehaviorConstraintResult = head_grader(head_out)
    cmp = compare_base_vs_head_efficiency(
        base_out.efficiency,
        head_out.efficiency,
        base_behavior=base_behavior,
        head_behavior=head_behavior,
        base_telemetry_coverage=base_out.telemetry_coverage,
        head_telemetry_coverage=head_out.telemetry_coverage,
    )
    return {
        "scenario_id": scenario_id,
        "model_id": runner.model_id,
        "provider": runner.provider,
        "passed": bool(base_behavior.passed and head_behavior.passed and cmp["behavior_preserved"]),
        **diag_fields,
        "source_attribution": dict(source_attribution),
        "base": _format_arm_entry(arm=base_label, outcome=base_out, behavior=base_behavior),
        "head": _format_arm_entry(arm=head_label, outcome=head_out, behavior=head_behavior),
        "comparison": cmp,
    }


def run_tier1_dual_model_suite(
    *,
    runners: Sequence[MicrobenchModelRunner] | None = None,
    scenarios: Sequence[str] | None = None,
    base_ref: str | None = None,
    main_ref: str = "origin/main",
    codex_model: str = DEFAULT_CODEX_MODEL,
    ark_model: str = DEFAULT_ARK_FLASH_MODEL,
    codex_auth_json_path: Path | None = None,
    max_total_seconds: float = 180.0,
    per_call_timeout_seconds: float = 45.0,
) -> dict[str, Any]:
    """Run bounded 1-3m Base vs Head dual-model scenarios across M1/M2/M3."""
    started = time.monotonic()
    deadline = started + max_total_seconds
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

    active_runners: tuple[MicrobenchModelRunner, ...]
    if runners is not None:
        active_runners = tuple(runners)
    else:
        active_runners = (
            CodexGptMicrobenchRunner(
                model=codex_model,
                auth_json_path=codex_auth_json_path,
            ),
            DeepSeekArkMicrobenchRunner(model=ark_model),
        )

    scenarios_plan: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="loopx-microbench-t1-") as temp:
        temp_root = Path(temp).resolve()
        base_source_root: Path | None = None
        if use_external_base and resolved_base_sha is not None:
            helpers = _differential_smoke_helpers()
            base_source_root = temp_root / "base-src"
            helpers["_materialize_base_source"](resolved_base_sha, base_source_root)

        if "m1_envelope_scoped_recovery" in selected:
            m1_base_fix = prepare_m1_fixture(temp_root / "m1_base", source_root=base_source_root)
            m1_head_fix = prepare_m1_fixture(temp_root / "m1_head", source_root=None)
            m1_base = run_m1_deterministic_arm(m1_base_fix, arm="base", source_root=base_source_root)
            m1_head = run_m1_deterministic_arm(m1_head_fix, arm="head", source_root=None)
            base_prompt = _prompt_m1(
                arm="base",
                fixture=m1_base_fix,
                delivered_payload=m1_base["delivered_payload"],
                decision_path=m1_base["decision_path"],
            )
            head_prompt = _prompt_m1(
                arm="head",
                fixture=m1_head_fix,
                delivered_payload=m1_head["delivered_payload"],
                decision_path=m1_head["decision_path"],
            )
            m1_cross = base_source_root is not None
            scenarios_plan.append(
                {
                    "scenario_id": "m1_envelope_scoped_recovery",
                    "base_label": "base_off",
                    "head_label": "head_on",
                    "base_prompt": base_prompt,
                    "head_prompt": head_prompt,
                    "trial_root_base": m1_base_fix.root,
                    "project_root_base": m1_base_fix.project_root,
                    "trial_root_head": m1_head_fix.root,
                    "project_root_head": m1_head_fix.project_root,
                    "base_grader": _grade_m1,
                    "head_grader": _grade_m1,
                    "source_attribution": build_scenario_source_attribution(
                        base_source="external_base_checkout" if m1_cross else "candidate_head",
                        head_source="candidate_head",
                        comparison_kind=(
                            "cross_commit_differential" if m1_cross else "same_commit_arm_contrast"
                        ),
                        production_hot_path_covered=True,
                    ),
                }
            )

        if "m2_epoch_prefetch_semantic_fold" in selected:
            m2_fix = prepare_m2_fixture(temp_root / "m2")
            m2_base = run_m2_deterministic_arm(m2_fix, arm="base")
            m2_head = run_m2_deterministic_arm(m2_fix, arm="head")
            meta = dict(m2_fix.metadata or {})
            base_prompt = _prompt_m2(arm="base_keep_raw", payload=m2_base["prompt_payload"])
            head_prompt = _prompt_m2(arm="head_fold_to_receipt", payload=m2_head["prompt_payload"])
            gate_passed = bool(
                m2_base["behavior_constraint"].passed and m2_head["behavior_constraint"].passed
            )
            m2_hook_ok = bool(m2_head.get("semantic_unit_settlement_hook_available"))
            m2_prod_ok = bool(
                m2_head.get("production_settlement_executed")
                and m2_head.get("production_settlement_readback_ok")
            )
            m2_grader = lambda outcome, ef=meta["failure_facts"], eo=meta["open_obligations"], gp=gate_passed: _grade_m2(  # noqa: E731
                outcome,
                expected_failures=ef,
                expected_obligations=eo,
                gate_passed=gp,
            )
            scenarios_plan.append(
                {
                    "scenario_id": "m2_epoch_prefetch_semantic_fold",
                    "base_label": "base_keep_raw",
                    "head_label": "head_fold_receipt",
                    "base_prompt": base_prompt,
                    "head_prompt": head_prompt,
                    "trial_root_base": m2_fix.root,
                    "project_root_base": m2_fix.project_root,
                    "trial_root_head": m2_fix.root,
                    "project_root_head": m2_fix.project_root,
                    "base_grader": m2_grader,
                    "head_grader": m2_grader,
                    "semantic_unit_settlement_hook_available": m2_hook_ok,
                    "source_attribution": build_scenario_source_attribution(
                        base_source="candidate_head",
                        head_source="candidate_head",
                        comparison_kind="same_commit_mechanism_verification",
                        production_hot_path_covered=m2_prod_ok,
                        note=(
                            "Both M2 arms run same-commit mechanism helpers on the candidate "
                            "checkout; excluded from cross-commit claims and production "
                            "settlement hot-path claims unless an actual production settlement "
                            "entrypoint executes and reads back turn settlement."
                        ),
                    ),
                }
            )

        if "m3_claim_block_settle_invariants" in selected:
            m3_fix = prepare_m3_fixture(temp_root / "m3")
            m3_det = run_m3_deterministic_arm(m3_fix, arm="head", source_root=None)
            cli_ok = bool(m3_det["behavior_constraint"].passed)
            gate_proj = Path(str((m3_fix.metadata or {})["gate_project"]))
            claim_proj = Path(str((m3_fix.metadata or {})["claim_project"]))
            gate_prompt = _prompt_m3_gate(fixture=m3_fix, gate_guard=m3_det["gate_guard"])
            claim_prompt = _prompt_m3_claim_settle(fixture=m3_fix, claim_guard=m3_det["claim_guard"])
            scenarios_plan.append(
                {
                    "scenario_id": "m3_claim_block_settle_invariants",
                    "base_label": "m3a_blocked_gate",
                    "head_label": "m3b_claim_settle",
                    "base_prompt": gate_prompt,
                    "head_prompt": claim_prompt,
                    "trial_root_base": m3_fix.root,
                    "project_root_base": gate_proj,
                    "trial_root_head": m3_fix.root,
                    "project_root_head": claim_proj,
                    "base_grader": lambda out, ok=cli_ok: _grade_m3_gate(out, cli_gate_passed=ok),
                    "head_grader": lambda out, ok=cli_ok: _grade_m3_claim(out, cli_gate_passed=ok),
                    "source_attribution": build_scenario_source_attribution(
                        base_source="candidate_head",
                        head_source="candidate_head",
                        comparison_kind="same_commit_gate_vs_claim_contrast",
                        production_hot_path_covered=cli_ok,
                    ),
                }
            )

        def _run_for_runner(runner: MicrobenchModelRunner) -> list[dict[str, Any]]:
            return [
                _evaluate_model_pair(
                    runner=runner,
                    scenario_id=str(spec["scenario_id"]),
                    base_label=str(spec["base_label"]),
                    head_label=str(spec["head_label"]),
                    base_prompt=str(spec["base_prompt"]),
                    head_prompt=str(spec["head_prompt"]),
                    trial_root_base=spec["trial_root_base"],
                    project_root_base=spec["project_root_base"],
                    trial_root_head=spec["trial_root_head"],
                    project_root_head=spec["project_root_head"],
                    base_grader=spec["base_grader"],
                    head_grader=spec["head_grader"],
                    source_attribution=spec["source_attribution"],
                    semantic_unit_settlement_hook_available=spec.get("semantic_unit_settlement_hook_available"),
                    per_call_timeout=per_call_timeout_seconds,
                    deadline=deadline,
                )
                for spec in scenarios_plan
            ]

        if len(active_runners) <= 1:
            per_runner_rows = [_run_for_runner(r) for r in active_runners]
        else:
            with ThreadPoolExecutor(max_workers=len(active_runners)) as pool:
                per_runner_rows = list(pool.map(_run_for_runner, active_runners))
        for spec_idx in range(len(scenarios_plan)):
            for r_idx in range(len(active_runners)):
                rows.append(per_runner_rows[r_idx][spec_idx])

    wall_seconds = round(time.monotonic() - started, 3)
    unavailable_or_quota = [
        row
        for row in rows
        if row["base"]["status"] in {"unavailable", "quota_exhausted"}
        or row["head"]["status"] in {"unavailable", "quota_exhausted"}
    ]
    all_passed = bool(rows) and all(row["passed"] for row in rows)
    claim_scope = summarize_claim_scope(rows)
    report: dict[str, Any] = {
        "schema_version": MICROBENCH_RECEIPT_SCHEMA_VERSION,
        "tier": "tier1_dual_model",
        "passed": all_passed,
        "any_model_unavailable_or_quota": bool(unavailable_or_quota),
        "wall_seconds": wall_seconds,
        "budget_seconds": max_total_seconds,
        "head_commit": head_sha,
        "base_ref": resolved_base_sha or "local_arm_contrast",
        "external_base_checkout_used": bool(claim_scope["cross_commit_scenarios"]),
        "claim_scope": claim_scope,
        "models": [
            {"provider": runner.provider, "model_id": runner.model_id}
            for runner in active_runners
        ],
        "results": rows,
        "boundary": summarize_artifact_boundary(),
    }
    assert_public_safe_payload(report, label="tier1_microbench_report")
    return report
