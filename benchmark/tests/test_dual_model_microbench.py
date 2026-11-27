"""Tests for Tier-0 (<15s differential) and Tier-1 (dual-model M1/M2/M3) microbenchmark."""

from __future__ import annotations

import json
from pathlib import Path
import stat
import subprocess
import sys
import types
from typing import Any

import jsonschema
import pytest

from benchmark.microbench.metrics import (
    BehaviorConstraintResult,
    EfficiencyMetrics,
    assert_public_safe_payload,
    collect_codex_rollout_metrics,
    compare_base_vs_head_efficiency,
)
from benchmark.microbench.models import (
    DEFAULT_ARK_FLASH_MODEL,
    MICROBENCH_VERDICT_SCHEMA,
    CodexGptMicrobenchRunner,
    DeepSeekArkMicrobenchRunner,
    ModelRunOutcome,
    validate_microbench_verdict,
)
from benchmark.microbench.run import main as microbench_cli_main
from benchmark.microbench.scenarios import (
    M1_BLOCKED_TODO_ID,
    M1_PROOF_TOKEN,
    M1_SELECTED_TODO_ID,
    M3_AGENT_PRIMARY,
    M3_ARTIFACT_TOKEN,
    M3_CLAIM_TODO_ID,
    M3_GATE_TODO_ID,
)
from benchmark.microbench import (
    metrics as microbench_metrics,
    models as microbench_models,
    scenarios as microbench_scenarios,
    tier0 as microbench_tier0,
    tier1 as microbench_tier1,
)
from benchmark.microbench.tier0 import run_tier0_differential
from benchmark.microbench.tier1 import (
    _grade_m1,
    _grade_m2,
    _grade_m3_claim,
    _grade_m3_gate,
    run_tier1_dual_model_suite,
)


def _write_fake_codex(bin_path: Path, *, mode: str) -> None:
    script = f"""#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
_ = sys.stdin.read()
schema_out = args[args.index("--output-last-message") + 1] if "--output-last-message" in args else None
home = pathlib.Path(os.environ["CODEX_HOME"])
sessions = home / "sessions" / "2026" / "11" / "27"
sessions.mkdir(parents=True, exist_ok=True)
rollout = sessions / "rollout-synthetic.jsonl"

if {mode!r} == "quota_exhausted":
    print(json.dumps({{"type": "turn.failed", "error": {{"message": "You’ve hit your usage limit. Try again later."}}}}))
    rollout.write_text("", encoding="utf-8")
    sys.exit(1)

rollout.write_text(
    json.dumps({{
        "type": "session_meta",
        "timestamp": "2026-11-27T00:00:00Z",
        "payload": {{"model": "gpt-6.2-sol", "session_id": "sess-fake-01"}},
    }}) + "\\n"
    + json.dumps({{
        "type": "turn_context",
        "timestamp": "2026-11-27T00:00:01Z",
        "payload": {{"model": "gpt-6.2-sol"}},
    }}) + "\\n"
    + json.dumps({{
        "type": "event_msg",
        "timestamp": "2026-11-27T00:00:02Z",
        "payload": {{
            "type": "token_count",
            "info": {{
                "total_token_usage": {{
                    "input_tokens": 420,
                    "cached_input_tokens": 120,
                    "output_tokens": 35,
                    "reasoning_output_tokens": 10,
                    "total_tokens": 455
                }}
            }}
        }},
    }}) + "\\n",
    encoding="utf-8",
)
verdict = {{
    "schema_version": "microbench_model_verdict_v0",
    "scenario_id": "m1_envelope_scoped_recovery",
    "decision": "execute",
    "selected_todo_id": {M1_SELECTED_TODO_ID!r},
    "preserved_blocked_todo_ids": [{M1_BLOCKED_TODO_ID!r}],
    "extracted_proof": {M1_PROOF_TOKEN!r},
    "preserved_failure_facts": [],
    "preserved_open_obligations": [],
    "claim_owner": None,
    "spend_attempted": False,
    "spend_classification": {{"observed": "none", "attempted": False, "confirmed": False}},
    "tool_actions_summary": ["cat fixture/m1-proof.json"],
}}
if schema_out:
    pathlib.Path(schema_out).write_text(json.dumps(verdict), encoding="utf-8")
print(json.dumps({{"type": "item.completed", "item": {{"type": "command_execution"}}}}))
print(json.dumps({{"type": "turn.completed", "usage": {{"input_tokens": 420, "output_tokens": 35}}}}))
"""
    bin_path.write_text(script, encoding="utf-8")
    bin_path.chmod(bin_path.stat().st_mode | stat.S_IXUSR)


def test_tier0_deterministic_differential_passes_m1_m2_m3_under_15s() -> None:
    report = run_tier0_differential()
    assert report["passed"] is True, report
    assert report["wall_seconds"] < 15.0
    assert report["cli_output_differential"]["ok"] is True
    by_id = {row["scenario_id"]: row for row in report["scenarios"]}
    assert set(by_id) == {
        "m1_envelope_scoped_recovery",
        "m2_epoch_prefetch_semantic_fold",
        "m3_claim_block_settle_invariants",
    }
    assert by_id["m1_envelope_scoped_recovery"]["comparison"]["deltas"]["wire_bytes"] < 0
    assert by_id["m2_epoch_prefetch_semantic_fold"]["comparison"]["deltas"]["prompt_tokens"] < 0
    assert by_id["m3_claim_block_settle_invariants"]["head"]["behavior_constraint"]["passed"] is True
    assert_public_safe_payload(report)


def test_efficiency_gain_never_masks_behavior_constraint_regression() -> None:
    cmp = compare_base_vs_head_efficiency(
        EfficiencyMetrics(wire_bytes=2000, prompt_tokens=500, tool_calls=2),
        EfficiencyMetrics(wire_bytes=500, prompt_tokens=120, tool_calls=1),
        base_behavior=BehaviorConstraintResult(passed=True, checks={"keep_blocked_p0": True}),
        head_behavior=BehaviorConstraintResult(
            passed=False,
            checks={"keep_blocked_p0": False},
            violations=("blocked_p0_dropped",),
        ),
    )
    assert cmp["deltas"]["wire_bytes"] == -1500
    assert cmp["deltas"]["prompt_tokens"] == -380
    assert cmp["behavior_preserved"] is False
    assert cmp["head_violations"] == ["blocked_p0_dropped"]


def test_codex_runner_extracts_rollout_usage_and_classifies_quota_exhaustion(tmp_path: Path) -> None:
    trial_root = tmp_path / "trial"
    project_root = trial_root / "project"
    project_root.mkdir(parents=True)
    auth_file = tmp_path / "auth.json"
    auth_file.write_text('{"tokens":{}}', encoding="utf-8")

    ok_bin = tmp_path / "fake-codex-ok"
    _write_fake_codex(ok_bin, mode="ok")
    runner_ok = CodexGptMicrobenchRunner(
        codex_bin=str(ok_bin),
        auth_json_path=auth_file,
        sandbox="read-only",
    )
    out_ok = runner_ok.run_case(
        scenario_id="m1_envelope_scoped_recovery",
        prompt_text="synthetic prompt",
        trial_root=trial_root,
        project_root=project_root,
        timeout_seconds=30.0,
    )
    assert out_ok.status == "ok", out_ok
    assert out_ok.efficiency.prompt_tokens == 420
    assert out_ok.efficiency.cached_tokens == 120
    assert out_ok.efficiency.completion_tokens == 35
    assert out_ok.efficiency.reasoning_tokens == 10
    assert out_ok.efficiency.total_tokens == 455
    assert out_ok.efficiency.tool_calls == 1
    assert out_ok.verdict is not None and out_ok.verdict["extracted_proof"] == M1_PROOF_TOKEN

    quota_bin = tmp_path / "fake-codex-quota"
    _write_fake_codex(quota_bin, mode="quota_exhausted")
    runner_quota = CodexGptMicrobenchRunner(
        codex_bin=str(quota_bin),
        auth_json_path=auth_file,
        sandbox="read-only",
    )
    out_quota = runner_quota.run_case(
        scenario_id="m1_envelope_scoped_recovery",
        prompt_text="synthetic prompt",
        trial_root=trial_root,
        project_root=project_root,
        timeout_seconds=30.0,
    )
    assert out_quota.status == "quota_exhausted"
    assert out_quota.error_code == "quota_exhausted"
    assert out_quota.efficiency.prompt_tokens is None
    assert out_quota.efficiency.completion_tokens is None

    unconfigured = CodexGptMicrobenchRunner(
        codex_bin=str(ok_bin),
        auth_json_path=tmp_path / "missing-auth.json",
    )
    out_missing = unconfigured.run_case(
        scenario_id="m1_envelope_scoped_recovery",
        prompt_text="synthetic prompt",
        trial_root=trial_root,
        project_root=project_root,
        timeout_seconds=30.0,
    )
    assert out_missing.status == "unavailable"
    assert out_missing.error_code == "missing_codex_auth"


def test_deepseek_ark_runner_executes_scoped_tools_blocks_escape_and_counts_usage(
    tmp_path: Path,
) -> None:
    trial_root = tmp_path / "trial"
    project_root = trial_root / "project"
    proof_file = project_root / "fixture" / "m1-proof.json"
    proof_file.parent.mkdir(parents=True)
    proof_file.write_text(json.dumps({"proof_token": M1_PROOF_TOKEN}), encoding="utf-8")
    outside_secret = tmp_path / "outside.txt"
    outside_secret.write_text("DO_NOT_READ_OUTSIDE", encoding="utf-8")

    step_index = 0
    seen_commands: list[str] = []

    def stub_transport(*, endpoint: str, headers: Any, body: bytes, timeout_seconds: float) -> dict[str, Any]:
        nonlocal step_index
        del endpoint, headers, timeout_seconds
        payload = json.loads(body.decode("utf-8"))
        assert payload["model"] == DEFAULT_ARK_FLASH_MODEL
        step_index += 1
        if step_index == 1:
            return {
                "usage": {
                    "prompt_tokens": 110,
                    "completion_tokens": 18,
                    "total_tokens": 128,
                    "prompt_tokens_details": {"cached_tokens": 30},
                    "completion_tokens_details": {"reasoning_tokens": 4},
                },
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call_escape",
                                    "type": "function",
                                    "function": {
                                        "name": "exec_command",
                                        "arguments": json.dumps({"cmd": f"cat {outside_secret}"}),
                                    },
                                }
                            ],
                        }
                    }
                ],
            }
        if step_index == 2:
            last_tool_msg = payload["messages"][-1]["content"]
            seen_commands.append(last_tool_msg)
            assert "escapes trial workspace" in last_tool_msg
            return {
                "usage": {
                    "prompt_tokens": 140,
                    "completion_tokens": 15,
                    "total_tokens": 155,
                    "prompt_tokens_details": {"cached_tokens": 0},
                    "completion_tokens_details": {"reasoning_tokens": 0},
                },
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call_read",
                                    "type": "function",
                                    "function": {
                                        "name": "exec_command",
                                        "arguments": json.dumps({"cmd": "cat fixture/m1-proof.json"}),
                                    },
                                }
                            ],
                        }
                    }
                ],
            }
        last_tool_msg = payload["messages"][-1]["content"]
        assert M1_PROOF_TOKEN in last_tool_msg
        verdict = {
            "schema_version": "microbench_model_verdict_v0",
            "scenario_id": "m1_envelope_scoped_recovery",
            "decision": "execute",
            "selected_todo_id": M1_SELECTED_TODO_ID,
            "preserved_blocked_todo_ids": [M1_BLOCKED_TODO_ID],
            "extracted_proof": M1_PROOF_TOKEN,
            "preserved_failure_facts": [],
            "preserved_open_obligations": [],
            "claim_owner": None,
            "spend_attempted": False,
            "spend_classification": {"observed": "none", "attempted": False, "confirmed": False},
            "tool_actions_summary": ["cat fixture/m1-proof.json"],
        }
        return {
            "usage": {
                "prompt_tokens": 180,
                "completion_tokens": 40,
                "total_tokens": 220,
                "prompt_tokens_details": {"cached_tokens": 0},
                "completion_tokens_details": {"reasoning_tokens": 0},
            },
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": (
                            "Evidence verified from fixture.\n\n"
                            f"```json\n{json.dumps(verdict)}\n```\n"
                        ),
                    }
                }
            ],
        }

    runner = DeepSeekArkMicrobenchRunner(
        api_key="synthetic-test-key",
        transport=stub_transport,
    )
    outcome = runner.run_case(
        scenario_id="m1_envelope_scoped_recovery",
        prompt_text="Read fixture/m1-proof.json",
        trial_root=trial_root,
        project_root=project_root,
        timeout_seconds=30.0,
    )
    assert outcome.status == "ok", outcome
    assert outcome.efficiency.prompt_tokens == 430
    assert outcome.efficiency.cached_tokens == 30
    assert outcome.efficiency.completion_tokens == 73
    assert outcome.efficiency.reasoning_tokens == 4
    assert outcome.efficiency.total_tokens == 503
    assert outcome.efficiency.tool_calls == 2
    assert outcome.efficiency.round_trips == 3
    assert outcome.observed_commands == ("blocked_path_escape", "read:cat")
    assert outcome.verdict is not None and outcome.verdict["extracted_proof"] == M1_PROOF_TOKEN


def test_tier1_dual_model_regression_blocks_broken_head_behavior() -> None:
    class _BrokenHeadRunner:
        def __init__(self, provider: str, model_id: str) -> None:
            self.provider = provider
            self.model_id = model_id

        def run_case(
            self,
            *,
            scenario_id: str,
            prompt_text: str,
            trial_root: Path,
            project_root: Path,
            timeout_seconds: float,
        ) -> ModelRunOutcome:
            del trial_root, project_root, timeout_seconds
            is_head = "arm=head" in prompt_text or "head_fold_to_receipt" in prompt_text
            # Head uses fewer tokens, but whitewashes / drops required facts -> must fail!
            is_m2 = scenario_id == "m2_epoch_prefetch_semantic_fold"
            verdict = {
                "schema_version": "microbench_model_verdict_v0",
                "scenario_id": scenario_id,
                "decision": "execute",
                "selected_todo_id": (
                    ("todo_m2_unit" if is_m2 else M1_SELECTED_TODO_ID)
                    if not is_head
                    else M1_BLOCKED_TODO_ID
                ),
                "preserved_blocked_todo_ids": [M1_BLOCKED_TODO_ID] if not is_head else [],
                "extracted_proof": M1_PROOF_TOKEN if not is_head else None,
                "preserved_failure_facts": (
                    ["lease retry duplicate spend"]
                    if is_head
                    else [
                        "regression: retry worker ignores expired lease backoff ceiling",
                        "guard: duplicate spend rejected with idempotent replay check",
                    ]
                ),
                "preserved_open_obligations": (
                    ["backoff settlement"]
                    if is_head
                    else [
                        "verify backoff ceiling test on sqlite provider",
                        "preserve terminal settlement receipt integrity",
                    ]
                ),
                "claim_owner": M3_AGENT_PRIMARY,
                "spend_attempted": False,
                "spend_classification": {"observed": "none", "attempted": False, "confirmed": False},
                "tool_actions_summary": ["stub"],
            }
            if scenario_id == "m3_claim_block_settle_invariants":
                is_gate = "subcase=blocked_user_gate" in prompt_text
                verdict["decision"] = "ask_user" if is_gate else "execute"
                verdict["selected_todo_id"] = None if is_gate else M3_CLAIM_TODO_ID
                verdict["preserved_blocked_todo_ids"] = [M3_GATE_TODO_ID] if is_gate else []
                verdict["extracted_proof"] = None if is_gate else M3_ARTIFACT_TOKEN
            return ModelRunOutcome(
                model_id=self.model_id,
                provider=self.provider,
                status="ok",
                error_code=None,
                verdict=verdict,
                efficiency=EfficiencyMetrics(
                    wire_bytes=len(prompt_text.encode("utf-8")),
                    prompt_tokens=100 if is_head else 300,
                    completion_tokens=20,
                    total_tokens=120 if is_head else 320,
                    tool_calls=1,
                    round_trips=1,
                    wall_seconds=0.05,
                ),
            )

    report = run_tier1_dual_model_suite(
        runners=(
            _BrokenHeadRunner("codex", "gpt-6.2-sol"),
            _BrokenHeadRunner("ark", DEFAULT_ARK_FLASH_MODEL),
        ),
        scenarios=("m1_envelope_scoped_recovery", "m2_epoch_prefetch_semantic_fold"),
    )
    assert report["passed"] is False
    assert len(report["results"]) == 4
    assert all(row["passed"] is False for row in report["results"])
    assert_public_safe_payload(report)


def test_public_safety_gate_and_missing_rollout_metrics(tmp_path: Path) -> None:
    missing = collect_codex_rollout_metrics(tmp_path / "no-sessions")
    assert missing["prompt_tokens"] is None
    assert missing["error"] == "missing_rollout"

    for unsafe in (
        {"path": "/Users/" + "developer/secret.json"},
        {"token": "ghp_" + "0123456789abcdef" * 2},
        {"api_key": "sk-" + "0123456789abcdef" * 2},
    ):
        with pytest.raises(ValueError, match="public safety boundary"):
            assert_public_safe_payload(unsafe)


def test_cli_dry_tier0_and_opt_in_smoke(capsys: pytest.CaptureFixture[str]) -> None:
    rc = microbench_cli_main(["--tier", "both", "--format", "json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["tier0"]["passed"] is True
    assert payload["tier1"]["skipped"] is True
    assert payload["tier1"]["reason"] == "live_opt_in_required"

    smoke = subprocess.run(
        [sys.executable, "examples/benchmark-dual-model-microbench-smoke.py"],
        cwd=Path(__file__).resolve().parents[2],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert smoke.returncode == 0, smoke.stdout + smoke.stderr
    assert "benchmark-dual-model-microbench-smoke ok" in smoke.stdout


def test_m2_grader_rejects_keyword_fragments_missing_wrong_todo_and_contradictions() -> None:
    expected_failures = [
        "regression: retry worker ignores expired lease backoff ceiling",
        "guard: duplicate spend rejected with idempotent replay check",
    ]
    expected_obligations = [
        "verify backoff ceiling test on sqlite provider",
        "preserve terminal settlement receipt integrity",
    ]

    def _outcome(verdict: dict[str, Any]) -> ModelRunOutcome:
        return ModelRunOutcome(
            model_id=DEFAULT_ARK_FLASH_MODEL,
            provider="ark",
            status="ok",
            error_code=None,
            verdict=verdict,
            efficiency=EfficiencyMetrics(wire_bytes=200, prompt_tokens=50),
        )

    valid_base = {
        "schema_version": "microbench_model_verdict_v0",
        "scenario_id": "m2_epoch_prefetch_semantic_fold",
        "decision": "execute",
        "selected_todo_id": "todo_m2_unit",
        "preserved_blocked_todo_ids": [],
        "extracted_proof": None,
        "preserved_failure_facts": list(expected_failures),
        "preserved_open_obligations": list(expected_obligations),
        "claim_owner": None,
        "spend_attempted": False,
        "spend_classification": {"observed": "none", "attempted": False, "confirmed": False},
        "tool_actions_summary": [],
    }
    assert _grade_m2(
        _outcome(valid_base),
        expected_failures=expected_failures,
        expected_obligations=expected_obligations,
        gate_passed=True,
    ).passed is True

    # Preserve existing label stripping + whitespace normalization.
    assert _grade_m2(
        _outcome(
            {
                **valid_base,
                "preserved_failure_facts": [
                    "retry worker   ignores expired lease backoff ceiling",
                    "duplicate spend   rejected with idempotent replay check",
                ],
                "preserved_open_obligations": [
                    "verify   backoff ceiling test on sqlite provider",
                    "preserve terminal settlement receipt integrity",
                ],
            }
        ),
        expected_failures=expected_failures,
        expected_obligations=expected_obligations,
        gate_passed=True,
    ).passed is True

    negative_cases: list[tuple[dict[str, Any], tuple[str, ...]]] = [
        (
            {
                **valid_base,
                "preserved_failure_facts": ["lease retry duplicate spend idempotent"],
                "preserved_open_obligations": ["backoff settlement receipt"],
            },
            ("no_anti_whitewash_failure_facts",),
        ),
        (
            {
                **valid_base,
                "preserved_failure_facts": [expected_failures[0]],
            },
            ("no_anti_whitewash_failure_facts",),
        ),
        (
            {
                **valid_base,
                "preserved_failure_facts": [expected_failures[0], expected_failures[0]],
            },
            ("no_anti_whitewash_failure_facts",),
        ),
        (
            {
                **valid_base,
                "preserved_open_obligations": [expected_obligations[0]],
            },
            ("open_obligations_preserved",),
        ),
        (
            {
                **valid_base,
                "selected_todo_id": "todo_wrong_lane",
            },
            ("selected_todo_matches_m2",),
        ),
        (
            {
                **valid_base,
                "selected_todo_id": None,
            },
            ("selected_todo_matches_m2",),
        ),
        (
            {
                **valid_base,
                "preserved_failure_facts": [
                    *expected_failures,
                    "no regression found",
                ],
            },
            ("no_contradictory_claims", "no_anti_whitewash_failure_facts"),
        ),
        (
            {
                **valid_base,
                "preserved_open_obligations": [
                    *expected_obligations,
                    "sqlite backoff test already completed",
                ],
            },
            ("open_obligations_preserved", "no_contradictory_claims"),
        ),
        (
            {
                **valid_base,
                "preserved_failure_facts": [
                    *expected_failures,
                    "sqlite backoff verification note",
                ],
            },
            ("no_anti_whitewash_failure_facts",),
        ),
        (
            {
                **valid_base,
                "preserved_open_obligations": [
                    *expected_obligations,
                    expected_obligations[0],
                ],
            },
            ("open_obligations_preserved",),
        ),
        (
            {
                **valid_base,
                "decision": "noop",
            },
            ("decision_execute",),
        ),
        (
            {
                **valid_base,
                "spend_attempted": True,
            },
            ("no_premature_spend",),
        ),
    ]
    for bad_verdict, expected_violations in negative_cases:
        res = _grade_m2(
            _outcome(bad_verdict),
            expected_failures=expected_failures,
            expected_obligations=expected_obligations,
            gate_passed=True,
        )
        assert res.passed is False, bad_verdict
        for expected_violation in expected_violations:
            assert expected_violation in res.violations, (expected_violation, res)

    class _ExtraObligationHeadRunner:
        provider = "synthetic"
        model_id = "synthetic-m2-extra"

        def run_case(
            self,
            *,
            scenario_id: str,
            prompt_text: str,
            trial_root: Path,
            project_root: Path,
            timeout_seconds: float,
        ) -> ModelRunOutcome:
            del scenario_id, trial_root, project_root, timeout_seconds
            is_head = "arm=head_fold_to_receipt" in prompt_text
            verdict = dict(valid_base)
            if is_head:
                verdict["preserved_open_obligations"] = [
                    *expected_obligations,
                    "sqlite backoff test already completed",
                ]
            return ModelRunOutcome(
                model_id=self.model_id,
                provider=self.provider,
                status="ok",
                error_code=None,
                verdict=verdict,
                efficiency=EfficiencyMetrics(wire_bytes=200, prompt_tokens=40 if is_head else 90),
            )

    t1_extra = run_tier1_dual_model_suite(
        runners=[_ExtraObligationHeadRunner()],
        scenarios=("m2_epoch_prefetch_semantic_fold",),
    )
    assert t1_extra["passed"] is False
    assert t1_extra["results"][0]["base"]["behavior_constraint"]["passed"] is True
    assert t1_extra["results"][0]["head"]["behavior_constraint"]["passed"] is False
    assert "open_obligations_preserved" in t1_extra["results"][0]["head"]["behavior_constraint"]["violations"]
    assert "no_contradictory_claims" in t1_extra["results"][0]["head"]["behavior_constraint"]["violations"]


def test_ark_usage_coverage_preserves_zero_keeps_missing_null_and_blocks_partial_deltas(
    tmp_path: Path,
) -> None:
    trial_root = tmp_path / "trial"
    project_root = trial_root / "project"
    project_root.mkdir(parents=True)
    verdict = {
        "schema_version": "microbench_model_verdict_v0",
        "scenario_id": "m1_envelope_scoped_recovery",
        "decision": "execute",
        "selected_todo_id": M1_SELECTED_TODO_ID,
        "preserved_blocked_todo_ids": [M1_BLOCKED_TODO_ID],
        "extracted_proof": M1_PROOF_TOKEN,
        "preserved_failure_facts": [],
        "preserved_open_obligations": [],
        "claim_owner": None,
        "spend_attempted": False,
        "spend_classification": {"observed": "none", "attempted": False, "confirmed": False},
        "tool_actions_summary": [],
    }

    # Case 1: Only prompt_tokens=7 returned -> missing fields remain None (never 0).
    def missing_fields_transport(*, endpoint: str, headers: Any, body: bytes, timeout_seconds: float) -> dict[str, Any]:
        del endpoint, headers, body, timeout_seconds
        return {
            "usage": {"prompt_tokens": 7},
            "choices": [{"message": {"role": "assistant", "content": json.dumps(verdict)}}],
        }

    r_missing = DeepSeekArkMicrobenchRunner(api_key="synthetic-key", transport=missing_fields_transport).run_case(
        scenario_id="m1_envelope_scoped_recovery",
        prompt_text="check",
        trial_root=trial_root,
        project_root=project_root,
        timeout_seconds=10.0,
    )
    assert r_missing.status == "ok"
    assert r_missing.efficiency.prompt_tokens == 7
    assert r_missing.efficiency.cached_tokens is None
    assert r_missing.efficiency.completion_tokens is None
    assert r_missing.efficiency.reasoning_tokens is None
    assert r_missing.efficiency.total_tokens is None
    assert r_missing.telemetry_coverage is not None
    assert r_missing.telemetry_coverage["metrics"]["cached_tokens"]["status"] == "missing"

    # Case 2: Genuine 0 reported cleanly stays 0, while partial multi-round cached_tokens becomes None.
    call_idx = 0

    def partial_transport(*, endpoint: str, headers: Any, body: bytes, timeout_seconds: float) -> dict[str, Any]:
        nonlocal call_idx
        del endpoint, headers, body, timeout_seconds
        call_idx += 1
        if call_idx == 1:
            return {
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 5,
                    "total_tokens": 25,
                    "prompt_tokens_details": {"cached_tokens": 10},
                    "completion_tokens_details": {"reasoning_tokens": 0},
                },
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call_ls",
                                    "type": "function",
                                    "function": {"name": "exec_command", "arguments": json.dumps({"cmd": "ls ."})},
                                }
                            ],
                        }
                    }
                ],
            }
        # Round 2 explicitly reports reasoning_tokens=0, omits cached_tokens -> cached_tokens is partial!
        return {
            "usage": {
                "prompt_tokens": 30,
                "completion_tokens": 8,
                "total_tokens": 38,
                "completion_tokens_details": {"reasoning_tokens": 0},
            },
            "choices": [{"message": {"role": "assistant", "content": json.dumps(verdict)}}],
        }

    r_partial = DeepSeekArkMicrobenchRunner(api_key="synthetic-key", transport=partial_transport).run_case(
        scenario_id="m1_envelope_scoped_recovery",
        prompt_text="check",
        trial_root=trial_root,
        project_root=project_root,
        timeout_seconds=10.0,
    )
    assert r_partial.status == "ok"
    assert r_partial.efficiency.prompt_tokens == 50
    assert r_partial.efficiency.completion_tokens == 13
    assert r_partial.efficiency.total_tokens == 63
    assert r_partial.efficiency.reasoning_tokens == 0
    assert r_partial.efficiency.cached_tokens is None
    assert r_partial.telemetry_coverage is not None
    assert r_partial.telemetry_coverage["has_partial_coverage"] is True
    assert r_partial.telemetry_coverage["partial_metrics"] == ["cached_tokens"]
    assert r_partial.telemetry_coverage["metrics"]["reasoning_tokens"]["status"] == "complete"

    cmp = compare_base_vs_head_efficiency(
        EfficiencyMetrics(wire_bytes=200, prompt_tokens=100, cached_tokens=40, completion_tokens=20),
        r_partial.efficiency,
        base_behavior=BehaviorConstraintResult(passed=True, checks={"ok": True}),
        head_behavior=BehaviorConstraintResult(passed=True, checks={"ok": True}),
        head_telemetry_coverage=r_partial.telemetry_coverage,
    )
    assert cmp["deltas"]["prompt_tokens"] == -50
    assert cmp["deltas"]["cached_tokens"] is None
    assert cmp["pct_changes"]["cached_tokens"] is None
    assert "cached_tokens" in cmp["excluded_partial_metrics"]


def test_tier0_and_tier1_attribute_m2_same_commit_mechanism_separately_from_cross_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    head_sha = microbench_tier0._head_commit()
    fake_base_sha = "0" * 40
    assert fake_base_sha != head_sha

    monkeypatch.setattr(microbench_tier0, "_resolve_pinned_base_ref", lambda base_ref, main_ref="origin/main": fake_base_sha)
    monkeypatch.setattr(
        microbench_tier0,
        "_differential_smoke_helpers",
        lambda: {
            "_materialize_base_source": lambda sha, dst: dst.mkdir(parents=True, exist_ok=True),
        },
    )

    def _stub_m1_arm(fix: Any, *, arm: str, source_root: Path | None = None) -> dict[str, Any]:
        del fix, source_root
        return {
            "behavior_constraint": BehaviorConstraintResult(passed=True, checks={"ok": True}),
            "efficiency": EfficiencyMetrics(wire_bytes=1000 if arm == "base" else 400, prompt_tokens=200 if arm == "base" else 80),
        }

    def _stub_m3_arm(fix: Any, *, arm: str = "head", source_root: Path | None = None) -> dict[str, Any]:
        del fix, arm, source_root
        return {
            "behavior_constraint": BehaviorConstraintResult(passed=True, checks={"ok": True}),
            "efficiency": EfficiencyMetrics(wire_bytes=500, prompt_tokens=100),
        }

    monkeypatch.setattr(microbench_tier0, "prepare_m1_fixture", lambda root, source_root=None: root)
    monkeypatch.setattr(microbench_tier0, "run_m1_deterministic_arm", _stub_m1_arm)
    monkeypatch.setattr(microbench_tier0, "prepare_m3_fixture", lambda root: root)
    monkeypatch.setattr(microbench_tier0, "run_m3_deterministic_arm", _stub_m3_arm)

    stub_calls = 0
    fake_pkg = types.ModuleType("loopx.control_plane.turn_driver")
    fake_mod = types.ModuleType("loopx.control_plane.turn_driver.todo_unit_settlement")

    def _prepare_turn_semantic_unit_context(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal stub_calls
        del args, kwargs
        stub_calls += 1
        return {"ok": True}

    fake_mod.prepare_turn_semantic_unit_context = _prepare_turn_semantic_unit_context  # type: ignore[attr-defined]
    fake_pkg.todo_unit_settlement = fake_mod  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "loopx.control_plane.turn_driver", fake_pkg)
    monkeypatch.setitem(sys.modules, "loopx.control_plane.turn_driver.todo_unit_settlement", fake_mod)

    report = run_tier0_differential(base_ref="origin/main")
    assert report["passed"] is True
    assert report["external_base_checkout_used"] is True
    assert stub_calls == 0
    by_id = {row["scenario_id"]: row for row in report["scenarios"]}

    assert by_id["m1_envelope_scoped_recovery"]["source_attribution"]["comparison_kind"] == "cross_commit_differential"
    assert by_id["m3_claim_block_settle_invariants"]["source_attribution"]["comparison_kind"] == "cross_commit_differential"

    m2_row = by_id["m2_epoch_prefetch_semantic_fold"]
    m2_attr = m2_row["source_attribution"]
    assert m2_row["semantic_unit_settlement_hook_available"] is True
    assert m2_attr["base_source"] == "candidate_head"
    assert m2_attr["head_source"] == "candidate_head"
    assert m2_attr["comparison_kind"] == "same_commit_mechanism_verification"
    assert m2_attr["excluded_from_cross_commit_claims"] is True
    assert m2_attr["production_hot_path_covered"] is False
    assert m2_attr["excluded_from_production_hot_path_claims"] is True

    scope = report["claim_scope"]
    assert set(scope["cross_commit_scenarios"]) == {
        "m1_envelope_scoped_recovery",
        "m3_claim_block_settle_invariants",
    }
    assert "m2_epoch_prefetch_semantic_fold" in scope["same_commit_mechanism_scenarios"]
    assert "m2_epoch_prefetch_semantic_fold" in scope["excluded_from_cross_commit_claims"]
    assert "m2_epoch_prefetch_semantic_fold" not in scope["production_hot_path_scenarios"]
    assert "m2_epoch_prefetch_semantic_fold" in scope["excluded_from_production_hot_path_claims"]
    assert_public_safe_payload(report)

    class _ValidM2Runner:
        provider = "synthetic"
        model_id = "synthetic-m2-scope"

        def run_case(
            self,
            *,
            scenario_id: str,
            prompt_text: str,
            trial_root: Path,
            project_root: Path,
            timeout_seconds: float,
        ) -> ModelRunOutcome:
            del scenario_id, prompt_text, trial_root, project_root, timeout_seconds
            return ModelRunOutcome(
                model_id=self.model_id,
                provider=self.provider,
                status="ok",
                error_code=None,
                verdict={
                    "schema_version": "microbench_model_verdict_v0",
                    "scenario_id": "m2_epoch_prefetch_semantic_fold",
                    "decision": "execute",
                    "selected_todo_id": "todo_m2_unit",
                    "preserved_blocked_todo_ids": [],
                    "extracted_proof": None,
                    "preserved_failure_facts": [
                        "regression: retry worker ignores expired lease backoff ceiling",
                        "guard: duplicate spend rejected with idempotent replay check",
                    ],
                    "preserved_open_obligations": [
                        "verify backoff ceiling test on sqlite provider",
                        "preserve terminal settlement receipt integrity",
                    ],
                    "claim_owner": None,
                    "spend_attempted": False,
                    "spend_classification": {"observed": "none", "attempted": False, "confirmed": False},
                    "tool_actions_summary": [],
                },
                efficiency=EfficiencyMetrics(wire_bytes=200, prompt_tokens=50),
            )

    t1_scope_report = run_tier1_dual_model_suite(
        runners=[_ValidM2Runner()],
        scenarios=("m2_epoch_prefetch_semantic_fold",),
    )
    assert stub_calls == 0
    assert t1_scope_report["passed"] is True
    t1_m2 = t1_scope_report["results"][0]
    assert t1_m2["semantic_unit_settlement_hook_available"] is True
    assert t1_m2["source_attribution"]["production_hot_path_covered"] is False
    assert t1_m2["source_attribution"]["excluded_from_production_hot_path_claims"] is True
    assert "m2_epoch_prefetch_semantic_fold" not in t1_scope_report["claim_scope"]["production_hot_path_scenarios"]
    assert "m2_epoch_prefetch_semantic_fold" in t1_scope_report["claim_scope"]["excluded_from_production_hot_path_claims"]
    assert_public_safe_payload(t1_scope_report)


def test_provider_adapters_and_graders_reject_missing_mismatched_schema_and_spend_contradictions(
    tmp_path: Path,
) -> None:
    expected_failures = [
        "regression: retry worker ignores expired lease backoff ceiling",
        "guard: duplicate spend rejected with idempotent replay check",
    ]
    expected_obligations = [
        "verify backoff ceiling test on sqlite provider",
        "preserve terminal settlement receipt integrity",
    ]
    base_m2_verdict: dict[str, Any] = {
        "schema_version": "microbench_model_verdict_v0",
        "scenario_id": "m2_epoch_prefetch_semantic_fold",
        "decision": "execute",
        "selected_todo_id": "todo_m2_unit",
        "preserved_blocked_todo_ids": [],
        "extracted_proof": None,
        "preserved_failure_facts": list(expected_failures),
        "preserved_open_obligations": list(expected_obligations),
        "claim_owner": None,
        "spend_attempted": False,
        "spend_classification": {
            "observed": "none",
            "attempted": False,
            "confirmed": False,
        },
        "tool_actions_summary": ["verified fold receipt"],
    }

    # 1. Pure-Python verdict schema validator matches jsonschema + rejects extra/missing/wrong-scenario.
    jsonschema.validate(instance=base_m2_verdict, schema=MICROBENCH_VERDICT_SCHEMA)
    assert validate_microbench_verdict(
        base_m2_verdict,
        expected_scenario_id="m2_epoch_prefetch_semantic_fold",
    ) == (True, ())

    missing_sid = {k: v for k, v in base_m2_verdict.items() if k != "scenario_id"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=missing_sid, schema=MICROBENCH_VERDICT_SCHEMA)
    ok_missing, errs_missing = validate_microbench_verdict(
        missing_sid,
        expected_scenario_id="m2_epoch_prefetch_semantic_fold",
    )
    assert ok_missing is False
    assert any("missing_fields:scenario_id" in e for e in errs_missing)

    missing_sc = {k: v for k, v in base_m2_verdict.items() if k != "spend_classification"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=missing_sc, schema=MICROBENCH_VERDICT_SCHEMA)
    ok_sc, errs_sc = validate_microbench_verdict(
        missing_sc,
        expected_scenario_id="m2_epoch_prefetch_semantic_fold",
    )
    assert ok_sc is False and any("spend_classification" in e for e in errs_sc)

    extra_prop = {**base_m2_verdict, "unexpected": True}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=extra_prop, schema=MICROBENCH_VERDICT_SCHEMA)
    ok_extra, errs_extra = validate_microbench_verdict(
        extra_prop,
        expected_scenario_id="m2_epoch_prefetch_semantic_fold",
    )
    assert ok_extra is False and "unexpected_fields:unexpected" in errs_extra

    wrong_sid = {**base_m2_verdict, "scenario_id": "m1_envelope_scoped_recovery"}
    ok_wrong, errs_wrong = validate_microbench_verdict(
        wrong_sid,
        expected_scenario_id="m2_epoch_prefetch_semantic_fold",
    )
    assert ok_wrong is False and "verdict_scenario_mismatch" in errs_wrong

    # 2. Real DeepSeekArkMicrobenchRunner + Tier-1 M2 suite must fail missing/mismatched scenario_id & typed spend faults.
    def _ark_suite_with_head_payload(head_verdict: dict[str, Any]) -> dict[str, Any]:
        def transport(*, endpoint: str, headers: Any, body: bytes, timeout_seconds: float) -> dict[str, Any]:
            del endpoint, headers, timeout_seconds
            payload = json.loads(body.decode("utf-8"))
            user_prompt = str(payload["messages"][-1]["content"])
            verdict = head_verdict if "arm=head_fold_to_receipt" in user_prompt else base_m2_verdict
            return {
                "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
                "choices": [{"message": {"role": "assistant", "content": json.dumps(verdict)}}],
            }

        runner = DeepSeekArkMicrobenchRunner(api_key="synthetic-key", transport=transport)
        return run_tier1_dual_model_suite(
            runners=[runner],
            scenarios=("m2_epoch_prefetch_semantic_fold",),
        )

    rep_missing_sid = _ark_suite_with_head_payload(missing_sid)
    assert rep_missing_sid["passed"] is False
    assert rep_missing_sid["results"][0]["head"]["status"] == "error"
    assert rep_missing_sid["results"][0]["head"]["error_code"] == "invalid_verdict_schema"
    assert rep_missing_sid["results"][0]["head"]["behavior_constraint"]["passed"] is False

    rep_wrong_sid = _ark_suite_with_head_payload(wrong_sid)
    assert rep_wrong_sid["passed"] is False
    assert rep_wrong_sid["results"][0]["head"]["status"] == "error"
    assert rep_wrong_sid["results"][0]["head"]["error_code"] == "verdict_scenario_mismatch"
    assert rep_wrong_sid["results"][0]["head"]["behavior_constraint"]["passed"] is False

    # Read-only receipt narrative with typed read_only_receipt classification must pass (unverified prose ignored).
    receipt_read_verdict = {
        **base_m2_verdict,
        "spend_classification": {
            "observed": "read_only_receipt",
            "attempted": False,
            "confirmed": False,
        },
        "tool_actions_summary": ["read spend-slot receipt and verified spend_allowed_now=false"],
    }
    rep_receipt_read = _ark_suite_with_head_payload(receipt_read_verdict)
    assert rep_receipt_read["passed"] is True

    # Structured spend_classification reporting observed/attempted/confirmed spend must fail even if narrative denies it.
    typed_spend_mismatch = {
        **base_m2_verdict,
        "spend_attempted": False,
        "spend_classification": {
            "observed": "quota_spend_slot",
            "attempted": True,
            "confirmed": False,
        },
        "tool_actions_summary": ["did not call spend-slot"],
    }
    rep_typed_spend = _ark_suite_with_head_payload(typed_spend_mismatch)
    assert rep_typed_spend["passed"] is False
    head_behavior = rep_typed_spend["results"][0]["head"]["behavior_constraint"]
    assert head_behavior["passed"] is False
    assert "spend_classification_consistent" in head_behavior["violations"]
    assert "no_typed_spend_action" in head_behavior["violations"]

    typed_confirmed_spend = {
        **base_m2_verdict,
        "spend_attempted": True,
        "spend_classification": {
            "observed": "quota_spend_slot",
            "attempted": True,
            "confirmed": True,
        },
        "tool_actions_summary": ["checked without spending"],
    }
    rep_confirmed = _ark_suite_with_head_payload(typed_confirmed_spend)
    assert rep_confirmed["passed"] is False
    confirmed_violations = rep_confirmed["results"][0]["head"]["behavior_constraint"]["violations"]
    assert "no_typed_spend_action" in confirmed_violations
    assert "no_premature_spend" in confirmed_violations

    # 3. Direct grader coverage for M1 / M3 + separated observed command execution evidence.
    m1_verdict = {
        "schema_version": "microbench_model_verdict_v0",
        "scenario_id": "m1_envelope_scoped_recovery",
        "decision": "execute",
        "selected_todo_id": M1_SELECTED_TODO_ID,
        "preserved_blocked_todo_ids": [M1_BLOCKED_TODO_ID],
        "extracted_proof": M1_PROOF_TOKEN,
        "preserved_failure_facts": [],
        "preserved_open_obligations": [],
        "claim_owner": None,
        "spend_attempted": False,
        "spend_classification": {
            "observed": "none",
            "attempted": False,
            "confirmed": False,
        },
        "tool_actions_summary": ["cat fixture/m1-proof.json"],
    }
    res_m1_wrong_sid = _grade_m1(
        ModelRunOutcome(
            model_id="stub",
            provider="ark",
            status="ok",
            error_code=None,
            verdict={**m1_verdict, "scenario_id": "m2_epoch_prefetch_semantic_fold"},
            efficiency=EfficiencyMetrics(wire_bytes=100, prompt_tokens=20),
        )
    )
    assert res_m1_wrong_sid.passed is False
    assert "scenario_id_matches" in res_m1_wrong_sid.violations

    m3_gate_verdict = {
        "schema_version": "microbench_model_verdict_v0",
        "scenario_id": "m3_claim_block_settle_invariants",
        "decision": "ask_user",
        "selected_todo_id": None,
        "preserved_blocked_todo_ids": [M3_GATE_TODO_ID],
        "extracted_proof": None,
        "preserved_failure_facts": [],
        "preserved_open_obligations": [],
        "claim_owner": None,
        "spend_attempted": False,
        "spend_classification": {
            "observed": "none",
            "attempted": True,
            "confirmed": False,
        },
        "tool_actions_summary": ["checked user_gate"],
    }
    res_m3_gate_summary = _grade_m3_gate(
        ModelRunOutcome(
            model_id="stub",
            provider="ark",
            status="ok",
            error_code=None,
            verdict=m3_gate_verdict,
            efficiency=EfficiencyMetrics(wire_bytes=100, prompt_tokens=20),
        ),
        cli_gate_passed=True,
    )
    assert res_m3_gate_summary.passed is False
    assert "spend_classification_consistent" in res_m3_gate_summary.violations
    assert "no_typed_spend_action" in res_m3_gate_summary.violations

    m3_claim_verdict = {
        "schema_version": "microbench_model_verdict_v0",
        "scenario_id": "m3_claim_block_settle_invariants",
        "decision": "execute",
        "selected_todo_id": M3_CLAIM_TODO_ID,
        "preserved_blocked_todo_ids": [],
        "extracted_proof": M3_ARTIFACT_TOKEN,
        "preserved_failure_facts": [],
        "preserved_open_obligations": [],
        "claim_owner": M3_AGENT_PRIMARY,
        "spend_attempted": False,
        "spend_classification": {
            "observed": "read_only_receipt",
            "attempted": False,
            "confirmed": False,
        },
        "tool_actions_summary": ["cat fixture/m3-claim-artifact.json"],
    }
    res_m3_observed_spend = _grade_m3_claim(
        ModelRunOutcome(
            model_id="stub",
            provider="codex",
            status="ok",
            error_code=None,
            verdict=m3_claim_verdict,
            efficiency=EfficiencyMetrics(wire_bytes=100, prompt_tokens=20),
            observed_commands=("loopx:quota spend-slot",),
        ),
        cli_gate_passed=True,
    )
    assert res_m3_observed_spend.passed is False
    assert "no_observed_spend_command" in res_m3_observed_spend.violations

    # 4. CodexGptMicrobenchRunner: harmless printf containing 'spend-slot' and read-only receipt stay unclassified/safe;
    # genuine loopx quota spend-slot invocations and typed spend tools trigger no_observed_spend_command.
    trial_root = tmp_path / "codex_trial"
    project_root = trial_root / "project"
    project_root.mkdir(parents=True)
    auth_file = tmp_path / "auth.json"
    auth_file.write_text('{"tokens":{}}', encoding="utf-8")

    harmless_printf_bin = tmp_path / "fake-codex-printf"
    harmless_printf_bin.write_text(
        f"""#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
_ = sys.stdin.read()
out_path = args[args.index("--output-last-message") + 1]
home = pathlib.Path(os.environ["CODEX_HOME"])
(home / "sessions").mkdir(parents=True, exist_ok=True)
pathlib.Path(out_path).write_text(json.dumps({base_m2_verdict!r}), encoding="utf-8")
print(json.dumps({{"type": "item.completed", "item": {{"type": "command_execution", "command": "printf '%s\\\\n' spend-slot"}}}}))
print(json.dumps({{"type": "item.completed", "item": {{"type": "command_execution", "command": "/bin/zsh -lc 'printf spend-slot'"}}}}))
print(json.dumps({{"type": "item.completed", "item": {{"type": "command_execution", "command": "loopx --registry reg.json quota receipt --goal-id g"}}}}))
""",
        encoding="utf-8",
    )
    harmless_printf_bin.chmod(harmless_printf_bin.stat().st_mode | stat.S_IXUSR)
    codex_printf = CodexGptMicrobenchRunner(
        codex_bin=str(harmless_printf_bin),
        auth_json_path=auth_file,
        sandbox="read-only",
    )
    printf_outcome = codex_printf.run_case(
        scenario_id="m2_epoch_prefetch_semantic_fold",
        prompt_text="synthetic",
        trial_root=trial_root,
        project_root=project_root,
        timeout_seconds=20.0,
    )
    assert printf_outcome.status == "ok"
    assert printf_outcome.observed_commands == (
        "command_execution",
        "command_execution",
        "loopx:quota receipt",
    )
    assert _grade_m2(
        printf_outcome,
        expected_failures=expected_failures,
        expected_obligations=expected_obligations,
        gate_passed=True,
    ).passed is True

    fake_codex = tmp_path / "fake-codex-spend-and-m1"
    fake_codex.write_text(
        f"""#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
_ = sys.stdin.read()
out_path = args[args.index("--output-last-message") + 1]
home = pathlib.Path(os.environ["CODEX_HOME"])
(home / "sessions").mkdir(parents=True, exist_ok=True)
pathlib.Path(out_path).write_text(json.dumps({m1_verdict!r}), encoding="utf-8")
print(json.dumps({{"type": "item.completed", "item": {{"type": "command_execution", "command": "loopx --registry reg.json quota spend-slot --goal-id g"}}}}))
print(json.dumps({{"type": "item.completed", "item": {{"type": "function_call", "name": "quota_spend_slot"}}}}))
""",
        encoding="utf-8",
    )
    fake_codex.chmod(fake_codex.stat().st_mode | stat.S_IXUSR)
    codex_runner = CodexGptMicrobenchRunner(
        codex_bin=str(fake_codex),
        auth_json_path=auth_file,
        sandbox="read-only",
    )
    codex_wrong_scenario = codex_runner.run_case(
        scenario_id="m2_epoch_prefetch_semantic_fold",
        prompt_text="synthetic",
        trial_root=trial_root,
        project_root=project_root,
        timeout_seconds=20.0,
    )
    assert codex_wrong_scenario.status == "error"
    assert codex_wrong_scenario.error_code == "verdict_scenario_mismatch"
    assert codex_wrong_scenario.verdict is None

    codex_observed_spend = codex_runner.run_case(
        scenario_id="m1_envelope_scoped_recovery",
        prompt_text="synthetic",
        trial_root=trial_root,
        project_root=project_root,
        timeout_seconds=20.0,
    )
    assert codex_observed_spend.status == "ok"
    assert codex_observed_spend.observed_commands == (
        "loopx:quota spend-slot",
        "tool:quota_spend_slot",
    )
    codex_grade = _grade_m1(codex_observed_spend)
    assert codex_grade.passed is False
    assert "no_observed_spend_command" in codex_grade.violations


def test_tier1_m1_propagates_deterministic_recovery_failure_and_blocks_production_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_run_m1 = microbench_tier1.run_m1_deterministic_arm

    def _corrupt_head_recovery(
        fixture: Any,
        *,
        arm: str,
        source_root: Path | None = None,
    ) -> dict[str, Any]:
        result = real_run_m1(fixture, arm=arm, source_root=source_root)
        if arm == "head":
            broken_checks = {
                **result["behavior_constraint"].checks,
                "blocked_p0_preserved": False,
                "blocked_fallback_readback_parity": False,
            }
            violations = tuple(name for name, ok in broken_checks.items() if not ok)
            result = {
                **result,
                "behavior_constraint": BehaviorConstraintResult(
                    passed=False,
                    checks=broken_checks,
                    violations=violations,
                ),
            }
        return result

    monkeypatch.setattr(microbench_tier1, "run_m1_deterministic_arm", _corrupt_head_recovery)

    class _ValidM1Runner:
        provider = "synthetic"
        model_id = "synthetic-m1-valid"

        def run_case(
            self,
            *,
            scenario_id: str,
            prompt_text: str,
            trial_root: Path,
            project_root: Path,
            timeout_seconds: float,
        ) -> ModelRunOutcome:
            del scenario_id, prompt_text, trial_root, project_root, timeout_seconds
            return ModelRunOutcome(
                model_id=self.model_id,
                provider=self.provider,
                status="ok",
                error_code=None,
                verdict={
                    "schema_version": "microbench_model_verdict_v0",
                    "scenario_id": "m1_envelope_scoped_recovery",
                    "decision": "execute",
                    "selected_todo_id": M1_SELECTED_TODO_ID,
                    "preserved_blocked_todo_ids": [M1_BLOCKED_TODO_ID],
                    "extracted_proof": M1_PROOF_TOKEN,
                    "preserved_failure_facts": [],
                    "preserved_open_obligations": [],
                    "claim_owner": None,
                    "spend_attempted": False,
                    "spend_classification": {
                        "observed": "none",
                        "attempted": False,
                        "confirmed": False,
                    },
                    "tool_actions_summary": ["cat fixture/m1-proof.json"],
                },
                efficiency=EfficiencyMetrics(wire_bytes=200, prompt_tokens=50),
            )

    report = run_tier1_dual_model_suite(
        runners=[_ValidM1Runner()],
        scenarios=("m1_envelope_scoped_recovery",),
    )
    assert report["passed"] is False
    row = report["results"][0]
    assert row["passed"] is False
    assert row["base"]["behavior_constraint"]["passed"] is True
    assert row["head"]["behavior_constraint"]["passed"] is False
    assert "deterministic_m1_recovery_passed" in row["head"]["behavior_constraint"]["violations"]
    assert row["source_attribution"]["production_hot_path_covered"] is False
    assert row["source_attribution"]["excluded_from_production_hot_path_claims"] is True
    assert "m1_envelope_scoped_recovery" not in report["claim_scope"]["production_hot_path_scenarios"]
    assert "m1_envelope_scoped_recovery" in report["claim_scope"]["excluded_from_production_hot_path_claims"]
    assert_public_safe_payload(report)


def test_tier1_enforces_strict_remaining_deadline_without_5s_clamp_and_fails_overrun(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_failures = [
        "regression: retry worker ignores expired lease backoff ceiling",
        "guard: duplicate spend rejected with idempotent replay check",
    ]
    expected_obligations = [
        "verify backoff ceiling test on sqlite provider",
        "preserve terminal settlement receipt integrity",
    ]
    valid_m2_verdict = {
        "schema_version": "microbench_model_verdict_v0",
        "scenario_id": "m2_epoch_prefetch_semantic_fold",
        "decision": "execute",
        "selected_todo_id": "todo_m2_unit",
        "preserved_blocked_todo_ids": [],
        "extracted_proof": None,
        "preserved_failure_facts": expected_failures,
        "preserved_open_obligations": expected_obligations,
        "claim_owner": None,
        "spend_attempted": False,
        "spend_classification": {
            "observed": "none",
            "attempted": False,
            "confirmed": False,
        },
        "tool_actions_summary": [],
    }

    # Subcase 1: remaining=0.15s passes exact 0.15s (not 5.0s); overrun after head (2.85s > 1.0s) fails row & report.
    now = {"t": 100.0}
    seen_timeouts: list[float] = []
    monkeypatch.setattr(microbench_tier1.time, "monotonic", lambda: now["t"])

    class _ClockAdvancingRunner:
        provider = "synthetic"
        model_id = "synthetic-clock"

        def run_case(
            self,
            *,
            scenario_id: str,
            prompt_text: str,
            trial_root: Path,
            project_root: Path,
            timeout_seconds: float,
        ) -> ModelRunOutcome:
            del scenario_id, trial_root, project_root
            seen_timeouts.append(timeout_seconds)
            if "arm=base_keep_raw" in prompt_text:
                now["t"] = 100.85
            else:
                now["t"] = 102.85
            return ModelRunOutcome(
                model_id=self.model_id,
                provider=self.provider,
                status="ok",
                error_code=None,
                verdict=dict(valid_m2_verdict),
                efficiency=EfficiencyMetrics(wire_bytes=200, prompt_tokens=50),
            )

    overrun_report = run_tier1_dual_model_suite(
        runners=[_ClockAdvancingRunner()],
        scenarios=("m2_epoch_prefetch_semantic_fold",),
        max_total_seconds=1.0,
        per_call_timeout_seconds=45.0,
    )
    assert seen_timeouts == [pytest.approx(1.0), pytest.approx(0.15)]
    assert overrun_report["within_budget"] is False
    assert overrun_report["passed"] is False
    row = overrun_report["results"][0]
    assert row["within_budget"] is False
    assert row["passed"] is False
    assert row["head"]["behavior_constraint"]["checks"]["within_budget"] is False
    assert "tier1_wall_budget_exhausted" in row["head"]["behavior_constraint"]["violations"]

    # Subcase 2: base consumes beyond deadline -> head is not invoked at all & returns unavailable budget failure.
    now["t"] = 200.0
    base_overrun_calls: list[float] = []

    class _BaseOverrunRunner:
        provider = "synthetic"
        model_id = "synthetic-base-overrun"

        def run_case(
            self,
            *,
            scenario_id: str,
            prompt_text: str,
            trial_root: Path,
            project_root: Path,
            timeout_seconds: float,
        ) -> ModelRunOutcome:
            del scenario_id, prompt_text, trial_root, project_root
            base_overrun_calls.append(timeout_seconds)
            now["t"] = 201.05
            return ModelRunOutcome(
                model_id=self.model_id,
                provider=self.provider,
                status="ok",
                error_code=None,
                verdict=dict(valid_m2_verdict),
                efficiency=EfficiencyMetrics(wire_bytes=200, prompt_tokens=50),
            )

    stop_report = run_tier1_dual_model_suite(
        runners=[_BaseOverrunRunner()],
        scenarios=("m2_epoch_prefetch_semantic_fold",),
        max_total_seconds=1.0,
        per_call_timeout_seconds=45.0,
    )
    assert len(base_overrun_calls) == 1
    assert stop_report["within_budget"] is False
    assert stop_report["passed"] is False
    stop_row = stop_report["results"][0]
    assert stop_row["passed"] is False
    assert stop_row["head"]["status"] == "unavailable"
    assert stop_row["head"]["error_code"] == "tier1_wall_budget_exhausted"
    assert stop_row["base"]["behavior_constraint"]["checks"]["within_budget"] is False
    assert stop_row["head"]["behavior_constraint"]["checks"]["within_budget"] is False

    # Subcase 3: finite positive budget expires before first arm runs -> zero provider invocations.
    ticks = iter((300.0, 302.0))
    monkeypatch.setattr(
        microbench_tier1.time,
        "monotonic",
        lambda: next(ticks, 302.0),
    )
    zero_calls: list[float] = []

    class _ZeroCallRunner:
        provider = "synthetic"
        model_id = "synthetic-zero-call"

        def run_case(
            self,
            *,
            scenario_id: str,
            prompt_text: str,
            trial_root: Path,
            project_root: Path,
            timeout_seconds: float,
        ) -> ModelRunOutcome:
            del scenario_id, prompt_text, trial_root, project_root
            zero_calls.append(timeout_seconds)
            raise AssertionError("provider should not be called once budget is exhausted")

    zero_report = run_tier1_dual_model_suite(
        runners=[_ZeroCallRunner()],
        scenarios=("m2_epoch_prefetch_semantic_fold",),
        max_total_seconds=1.0,
        per_call_timeout_seconds=45.0,
    )
    assert zero_calls == []
    assert zero_report["within_budget"] is False
    assert zero_report["passed"] is False
    assert zero_report["results"][0]["base"]["error_code"] == "tier1_wall_budget_exhausted"
    assert zero_report["results"][0]["head"]["error_code"] == "tier1_wall_budget_exhausted"


def test_cli_and_suite_reject_non_finite_or_non_positive_budgets_and_unhashable_observed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatched: list[str] = []

    class _NeverCalledRunner:
        provider = "synthetic"
        model_id = "synthetic-never-called"

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            del args, kwargs

        def run_case(self, **kwargs: Any) -> ModelRunOutcome:
            dispatched.append(str(kwargs.get("scenario_id")))
            raise AssertionError("runner must not be called on invalid timeout budget")

    monkeypatch.setattr("benchmark.microbench.run.CodexGptMicrobenchRunner", _NeverCalledRunner)
    monkeypatch.setattr("benchmark.microbench.run.DeepSeekArkMicrobenchRunner", _NeverCalledRunner)

    # 1. Public CLI rejects non-finite / non-positive budgets before dispatching runners or emitting non-JSON.
    for bad_flag in ("--max-total-seconds", "--per-call-timeout-seconds"):
        for bad_val in ("inf", "-inf", "nan", "0", "-1.5"):
            capsys.readouterr()
            with pytest.raises(SystemExit) as exc_info:
                microbench_cli_main(["--tier", "1", "--live", f"{bad_flag}={bad_val}"])
            assert exc_info.value.code != 0
            captured = capsys.readouterr()
            assert captured.out == ""
            assert "finite positive number" in captured.err
            assert dispatched == []

    # 2. Programmatic entrypoints (run_tier1_dual_model_suite and adapters) reject non-finite/non-positive values.
    for bad_num in (float("inf"), float("-inf"), float("nan"), 0.0, -2.0):
        with pytest.raises(ValueError, match="finite positive number"):
            run_tier1_dual_model_suite(
                runners=[_NeverCalledRunner()],
                scenarios=("m2_epoch_prefetch_semantic_fold",),
                max_total_seconds=bad_num,
            )
        with pytest.raises(ValueError, match="finite positive number"):
            run_tier1_dual_model_suite(
                runners=[_NeverCalledRunner()],
                scenarios=("m2_epoch_prefetch_semantic_fold",),
                per_call_timeout_seconds=bad_num,
            )
        ark_runner = DeepSeekArkMicrobenchRunner(api_key="synthetic-key")
        with pytest.raises(ValueError, match="finite positive number"):
            ark_runner.run_case(
                scenario_id="m2_epoch_prefetch_semantic_fold",
                prompt_text="check",
                trial_root=tmp_path,
                project_root=tmp_path,
                timeout_seconds=bad_num,
            )
        codex_runner = CodexGptMicrobenchRunner(auth_json_path=tmp_path / "missing-auth.json")
        with pytest.raises(ValueError, match="finite positive number"):
            codex_runner.run_case(
                scenario_id="m2_epoch_prefetch_semantic_fold",
                prompt_text="check",
                trial_root=tmp_path,
                project_root=tmp_path,
                timeout_seconds=bad_num,
            )
    assert dispatched == []

    # 3. Malformed unhashable spend_classification.observed & non-list preserved_blocked_todo_ids fail cleanly without TypeError.
    expected_failures = [
        "regression: retry worker ignores expired lease backoff ceiling",
        "guard: duplicate spend rejected with idempotent replay check",
    ]
    expected_obligations = [
        "verify backoff ceiling test on sqlite provider",
        "preserve terminal settlement receipt integrity",
    ]

    class _MalformedObservedRunner:
        provider = "synthetic"
        model_id = "synthetic-malformed-observed"

        def run_case(
            self,
            *,
            scenario_id: str,
            prompt_text: str,
            trial_root: Path,
            project_root: Path,
            timeout_seconds: float,
        ) -> ModelRunOutcome:
            del trial_root, project_root, timeout_seconds
            is_head = "arm=head" in prompt_text or "head_fold_to_receipt" in prompt_text
            if scenario_id == "m1_envelope_scoped_recovery":
                return ModelRunOutcome(
                    model_id=self.model_id,
                    provider=self.provider,
                    status="ok",
                    error_code=None,
                    verdict={
                        "schema_version": "microbench_model_verdict_v0",
                        "scenario_id": scenario_id,
                        "decision": "execute",
                        "selected_todo_id": M1_SELECTED_TODO_ID,
                        "preserved_blocked_todo_ids": 123 if is_head else [M1_BLOCKED_TODO_ID],
                        "extracted_proof": M1_PROOF_TOKEN,
                        "preserved_failure_facts": [],
                        "preserved_open_obligations": [],
                        "claim_owner": None,
                        "spend_attempted": False,
                        "spend_classification": {
                            "observed": {"nested": "bad"} if is_head else "none",
                            "attempted": False,
                            "confirmed": False,
                        },
                        "tool_actions_summary": [],
                    },
                    efficiency=EfficiencyMetrics(wire_bytes=200, prompt_tokens=50),
                )
            return ModelRunOutcome(
                model_id=self.model_id,
                provider=self.provider,
                status="ok",
                error_code=None,
                verdict={
                    "schema_version": "microbench_model_verdict_v0",
                    "scenario_id": "m2_epoch_prefetch_semantic_fold",
                    "decision": "execute",
                    "selected_todo_id": "todo_m2_unit",
                    "preserved_blocked_todo_ids": [],
                    "extracted_proof": None,
                    "preserved_failure_facts": list(expected_failures),
                    "preserved_open_obligations": list(expected_obligations),
                    "claim_owner": None,
                    "spend_attempted": False,
                    "spend_classification": {
                        "observed": ["none"] if is_head else "none",
                        "attempted": False,
                        "confirmed": False,
                    },
                    "tool_actions_summary": [],
                },
                efficiency=EfficiencyMetrics(wire_bytes=200, prompt_tokens=50),
            )

    malformed_report = run_tier1_dual_model_suite(
        runners=[_MalformedObservedRunner()],
        scenarios=("m1_envelope_scoped_recovery", "m2_epoch_prefetch_semantic_fold"),
    )
    assert malformed_report["passed"] is False
    assert len(malformed_report["results"]) == 2
    for bad_row in malformed_report["results"]:
        assert bad_row["passed"] is False
        assert bad_row["base"]["behavior_constraint"]["passed"] is True
        assert bad_row["head"]["behavior_constraint"]["passed"] is False
        assert "schema_valid" in bad_row["head"]["behavior_constraint"]["violations"]
        assert "no_typed_spend_action" in bad_row["head"]["behavior_constraint"]["violations"]
    assert_public_safe_payload(malformed_report)


def test_tier1_stops_deterministic_cli_and_runners_when_budget_expires_during_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time as std_time

    # Case 1: 0.10s total budget expires before any deterministic CLI process spawns.
    now = {"t": 500.0}

    def _fake_monotonic() -> float:
        now["t"] += 0.15
        return now["t"]

    cli_calls: list[tuple[Any, Any]] = []
    runner_calls: list[str] = []
    real_sub_run = subprocess.run

    def _spy_sub_run(*args: Any, **kwargs: Any) -> Any:
        cmd = args[0] if args else kwargs.get("args", [])
        if any("loopx.cli" in str(part) for part in (cmd or [])):
            cli_calls.append((cmd, kwargs.get("timeout")))
        return real_sub_run(*args, **kwargs)

    class _NeverDispatchedRunner:
        provider = "synthetic"
        model_id = "synthetic-never-dispatched"

        def run_case(self, **kwargs: Any) -> ModelRunOutcome:
            runner_calls.append(str(kwargs.get("scenario_id")))
            raise AssertionError("runner must not execute when setup exhausted deadline")

    monkeypatch.setattr(std_time, "monotonic", _fake_monotonic)
    monkeypatch.setattr(microbench_scenarios.subprocess, "run", _spy_sub_run)

    report = run_tier1_dual_model_suite(
        runners=[_NeverDispatchedRunner()],
        max_total_seconds=0.10,
        per_call_timeout_seconds=45.0,
    )
    assert cli_calls == []
    assert runner_calls == []
    assert report["within_budget"] is False
    assert report["passed"] is False
    assert len(report["results"]) == 3
    for row in report["results"]:
        assert row["passed"] is False
        assert row["within_budget"] is False
        assert row["base"]["status"] == "unavailable"
        assert row["base"]["error_code"] == "tier1_wall_budget_exhausted"
        assert row["head"]["error_code"] == "tier1_wall_budget_exhausted"

    # Case 2: real subprocess boundary passes shrinking remaining timeout (< 60s) and stops next CLI after expiry.
    now2 = {"t": 800.0}
    seen_cli_timeouts: list[tuple[str, float | None]] = []

    def _step_monotonic() -> float:
        return now2["t"]

    def _first_cli_expires(*args: Any, **kwargs: Any) -> Any:
        cmd = args[0] if args else kwargs.get("args", [])
        if not any("loopx.cli" in str(part) for part in (cmd or [])):
            return real_sub_run(*args, **kwargs)
        seen_cli_timeouts.append((" ".join(str(x) for x in cmd), kwargs.get("timeout")))
        result = real_sub_run(*args, **kwargs)
        if "should-run" in seen_cli_timeouts[-1][0]:
            now2["t"] = 815.0  # expire 10.0s suite budget as base `quota should-run` finishes
        else:
            now2["t"] += 1.0  # shrink remaining budget across pre-arm CLIs
        return result

    monkeypatch.setattr(std_time, "monotonic", _step_monotonic)
    monkeypatch.setattr(microbench_scenarios.subprocess, "run", _first_cli_expires)

    report2 = run_tier1_dual_model_suite(
        runners=[_NeverDispatchedRunner()],
        scenarios=("m1_envelope_scoped_recovery",),
        max_total_seconds=10.0,
        per_call_timeout_seconds=45.0,
    )
    assert len(seen_cli_timeouts) >= 2, "expected deterministic CLIs before expiry"
    assert seen_cli_timeouts[0][1] == pytest.approx(10.0)
    assert seen_cli_timeouts[-1][1] is not None and seen_cli_timeouts[-1][1] < seen_cli_timeouts[0][1]
    assert any("should-run" in cmd for cmd, _ in seen_cli_timeouts)
    assert not any("context-read" in cmd for cmd, _ in seen_cli_timeouts)
    assert runner_calls == []
    assert report2["within_budget"] is False
    assert report2["passed"] is False
    assert report2["results"][0]["base"]["error_code"] == "tier1_wall_budget_exhausted"


def test_ark_runner_clamps_every_http_round_and_tool_to_arm_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time as std_time

    trial_root = tmp_path / "trial"
    project_root = trial_root / "project"
    proof_file = project_root / "fixture" / "m1-proof.json"
    proof_file.parent.mkdir(parents=True)
    proof_file.write_text(json.dumps({"proof_token": M1_PROOF_TOKEN}), encoding="utf-8")

    verdict = {
        "schema_version": "microbench_model_verdict_v0",
        "scenario_id": "m1_envelope_scoped_recovery",
        "decision": "execute",
        "selected_todo_id": M1_SELECTED_TODO_ID,
        "preserved_blocked_todo_ids": [M1_BLOCKED_TODO_ID],
        "extracted_proof": M1_PROOF_TOKEN,
        "preserved_failure_facts": [],
        "preserved_open_obligations": [],
        "claim_owner": None,
        "spend_attempted": False,
        "spend_classification": {"observed": "none", "attempted": False, "confirmed": False},
        "tool_actions_summary": ["cat fixture/m1-proof.json"],
    }

    # Subcase 1: 1.00s arm budget; round 1 takes 0.85s -> round 2 receives exact 0.15s and passes at 0.92s.
    clock = {"t": 100.0}
    monkeypatch.setattr(std_time, "monotonic", lambda: clock["t"])
    seen_timeouts_1: list[float] = []

    def _transport_tight(*, endpoint: str, headers: Any, body: bytes, timeout_seconds: float) -> dict[str, Any]:
        del endpoint, headers, body
        seen_timeouts_1.append(round(timeout_seconds, 4))
        if len(seen_timeouts_1) == 1:
            clock["t"] = 100.85
            return {
                "usage": {"prompt_tokens": 110, "completion_tokens": 10, "total_tokens": 120},
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "exec_command",
                                        "arguments": json.dumps({"cmd": "cat fixture/m1-proof.json"}),
                                    },
                                }
                            ],
                        }
                    }
                ],
            }
        clock["t"] = 100.92
        return {
            "usage": {"prompt_tokens": 140, "completion_tokens": 20, "total_tokens": 160},
            "choices": [{"message": {"role": "assistant", "content": json.dumps(verdict)}}],
        }

    runner_tight = DeepSeekArkMicrobenchRunner(api_key="synthetic-key", transport=_transport_tight)
    out_tight = runner_tight.run_case(
        scenario_id="m1_envelope_scoped_recovery",
        prompt_text="Read fixture/m1-proof.json",
        trial_root=trial_root,
        project_root=project_root,
        timeout_seconds=1.0,
    )
    assert out_tight.status == "ok", out_tight
    assert seen_timeouts_1 == [pytest.approx(1.0), pytest.approx(0.15)]
    assert out_tight.efficiency.initial_turn_context_tokens == 110
    assert out_tight.efficiency.per_turn_context_tokens == (110, 140)
    assert out_tight.efficiency.peak_context_tokens == 140
    assert out_tight.efficiency.occupancy_measurement_kind == "per_request_input_proxy"

    # Subcase 2: 1.00s arm budget; round 1 advances clock to 1.10s -> 0 tool executions, 0 round-2 HTTP requests.
    clock["t"] = 200.0
    seen_timeouts_2: list[float] = []
    tool_calls_2: list[str] = []
    monkeypatch.setattr(
        microbench_models,
        "_execute_scoped_command",
        lambda cmd, **kwargs: (tool_calls_2.append(cmd), ("read:cat", "ok"))[1],
    )

    def _transport_expired(*, endpoint: str, headers: Any, body: bytes, timeout_seconds: float) -> dict[str, Any]:
        del endpoint, headers, body
        seen_timeouts_2.append(round(timeout_seconds, 4))
        clock["t"] = 201.10
        return {
            "usage": {"prompt_tokens": 115, "completion_tokens": 12, "total_tokens": 127},
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "exec_command",
                                    "arguments": json.dumps({"cmd": "cat fixture/m1-proof.json"}),
                                },
                            }
                        ],
                    }
                }
            ],
        }

    runner_expired = DeepSeekArkMicrobenchRunner(api_key="synthetic-key", transport=_transport_expired)
    out_expired = runner_expired.run_case(
        scenario_id="m1_envelope_scoped_recovery",
        prompt_text="Read fixture/m1-proof.json",
        trial_root=trial_root,
        project_root=project_root,
        timeout_seconds=1.0,
    )
    assert seen_timeouts_2 == [pytest.approx(1.0)]
    assert tool_calls_2 == []
    assert out_expired.status == "error"
    assert out_expired.error_code == "ark_budget_exhausted"
    assert out_expired.verdict is None
    assert out_expired.efficiency.round_trips == 1
    assert out_expired.efficiency.per_turn_context_tokens == (115,)


def test_codex_rollout_occupancy_extraction_validation_and_compaction(tmp_path: Path) -> None:
    def _token_event(
        *,
        total_in: int,
        total_cached: int,
        total_out: int,
        last_in: int | None,
        last_cached: int = 0,
        last_out: int = 0,
        window: int | None = 475000,
    ) -> dict[str, Any]:
        info: dict[str, Any] = {
            "total_token_usage": {
                "input_tokens": total_in,
                "cached_input_tokens": total_cached,
                "output_tokens": total_out,
                "reasoning_output_tokens": 0,
                "total_tokens": total_in + total_out,
            },
        }
        if last_in is not None:
            info["last_token_usage"] = {
                "input_tokens": last_in,
                "cached_input_tokens": last_cached,
                "output_tokens": last_out,
                "reasoning_output_tokens": 0,
                "total_tokens": last_in + last_out,
            }
        if window is not None:
            info["model_context_window"] = window
        return {
            "type": "event_msg",
            "timestamp": "2026-11-27T00:00:05Z",
            "payload": {"type": "token_count", "info": info},
        }

    # Valid multi-turn rollout with duplicate token_count snapshot, compaction, and truncation event.
    valid_dir = tmp_path / "valid"
    valid_dir.mkdir()
    rollout = valid_dir / "rollout-valid.jsonl"
    rows = [
        {
            "type": "session_meta",
            "timestamp": "2026-11-27T00:00:00Z",
            "payload": {"id": "sess-occ-1"},
        },
        {
            "type": "turn_context",
            "timestamp": "2026-11-27T00:00:01Z",
            "payload": {"model": "gpt-6.2-sol"},
        },
        _token_event(total_in=43607, total_cached=0, total_out=181, last_in=43607, last_cached=0, last_out=181),
        _token_event(total_in=43607, total_cached=0, total_out=181, last_in=43607, last_cached=0, last_out=181),
        {"type": "compacted", "timestamp": "2026-11-27T00:00:02Z", "payload": {"compaction_response_id": "c1"}},
        _token_event(total_in=92793, total_cached=43392, total_out=438, last_in=49186, last_cached=43392, last_out=257),
        {"type": "event_msg", "timestamp": "2026-11-27T00:00:03Z", "payload": {"type": "context_truncated"}},
        _token_event(total_in=144795, total_cached=92288, total_out=610, last_in=52002, last_cached=48896, last_out=172),
    ]
    rollout.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    metrics = microbench_metrics.collect_codex_rollout_metrics(valid_dir)
    assert metrics["prompt_tokens"] == 144795
    assert metrics["initial_turn_context_tokens"] == 43607
    assert metrics["per_turn_context_tokens"] == (43607, 49186, 52002)
    assert metrics["peak_context_tokens"] == 52002
    assert metrics["effective_context_window"] == 475000
    assert metrics["min_headroom_tokens"] == 475000 - 52002
    assert metrics["peak_window_ratio"] == pytest.approx(round(52002 / 475000, 6))
    assert metrics["compaction_events"] == 1
    assert metrics["truncation_events"] == 1
    assert metrics["occupancy_measurement_kind"] == "per_request_input_proxy"
    assert metrics["occupancy_window_source"] == "codex_model_context_window"

    # Negative case: missing last_token_usage -> unknown occupancy (never fabricates per-request numbers).
    no_last_dir = tmp_path / "no-last"
    no_last_dir.mkdir()
    (no_last_dir / "rollout.jsonl").write_text(
        "\n".join(
            json.dumps(r)
            for r in [
                rows[0],
                rows[1],
                _token_event(total_in=1000, total_cached=0, total_out=20, last_in=None),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    m_nolast = microbench_metrics.collect_codex_rollout_metrics(no_last_dir)
    assert m_nolast["prompt_tokens"] == 1000
    assert m_nolast["occupancy_measurement_kind"] == "unknown"
    assert m_nolast["peak_context_tokens"] is None
    assert m_nolast["per_turn_context_tokens"] is None

    # Negative case: total delta != last_token_usage (counter gap) -> fails closed to unknown.
    gap_dir = tmp_path / "gap"
    gap_dir.mkdir()
    (gap_dir / "rollout.jsonl").write_text(
        "\n".join(
            json.dumps(r)
            for r in [
                rows[0],
                rows[1],
                _token_event(total_in=100, total_cached=0, total_out=10, last_in=100, last_out=10),
                _token_event(total_in=300, total_cached=0, total_out=20, last_in=150, last_out=10),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    m_gap = microbench_metrics.collect_codex_rollout_metrics(gap_dir)
    assert m_gap["occupancy_measurement_kind"] == "unknown"
    assert m_gap["peak_context_tokens"] is None
    assert m_gap["occupancy_error"] == "counter_gap"


def test_ark_partial_prompt_blocks_occupancy_and_cli_pin_with_output_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    trial_root = tmp_path / "trial"
    project_root = trial_root / "project"
    project_root.mkdir(parents=True)
    call_idx = 0
    verdict = {
        "schema_version": "microbench_model_verdict_v0",
        "scenario_id": "m1_envelope_scoped_recovery",
        "decision": "execute",
        "selected_todo_id": M1_SELECTED_TODO_ID,
        "preserved_blocked_todo_ids": [M1_BLOCKED_TODO_ID],
        "extracted_proof": M1_PROOF_TOKEN,
        "preserved_failure_facts": [],
        "preserved_open_obligations": [],
        "claim_owner": None,
        "spend_attempted": False,
        "spend_classification": {"observed": "none", "attempted": False, "confirmed": False},
        "tool_actions_summary": [],
    }

    def _partial_prompt_transport(*, endpoint: str, headers: Any, body: bytes, timeout_seconds: float) -> dict[str, Any]:
        nonlocal call_idx
        del endpoint, headers, body, timeout_seconds
        call_idx += 1
        if call_idx == 1:
            return {
                "usage": {"prompt_tokens": 120, "completion_tokens": 10, "total_tokens": 130},
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call_ls",
                                    "type": "function",
                                    "function": {"name": "exec_command", "arguments": json.dumps({"cmd": "ls ."})},
                                }
                            ],
                        }
                    }
                ],
            }
        return {
            "usage": {"completion_tokens": 15},
            "choices": [{"message": {"role": "assistant", "content": json.dumps(verdict)}}],
        }

    r_partial = DeepSeekArkMicrobenchRunner(api_key="synthetic-key", transport=_partial_prompt_transport).run_case(
        scenario_id="m1_envelope_scoped_recovery",
        prompt_text="check",
        trial_root=trial_root,
        project_root=project_root,
        timeout_seconds=10.0,
    )
    assert r_partial.status == "ok"
    assert r_partial.efficiency.occupancy_measurement_kind == "unknown"
    assert r_partial.efficiency.peak_context_tokens is None
    assert r_partial.efficiency.per_turn_context_tokens is None

    cmp = compare_base_vs_head_efficiency(
        EfficiencyMetrics(
            wire_bytes=200,
            prompt_tokens=100,
            initial_turn_context_tokens=100,
            peak_context_tokens=100,
            occupancy_measurement_kind="per_request_input_proxy",
        ),
        r_partial.efficiency,
        base_behavior=BehaviorConstraintResult(passed=True, checks={"ok": True}),
        head_behavior=BehaviorConstraintResult(passed=True, checks={"ok": True}),
        head_telemetry_coverage=r_partial.telemetry_coverage,
    )
    assert cmp["deltas"]["peak_context_tokens"] is None
    assert cmp["deltas"]["initial_turn_context_tokens"] is None

    # CLI --context-delivery on + --output-json writes sanitized JSON while rendering Markdown stdout.
    out_json = tmp_path / "nested" / "tier0-pinned.json"
    rc = microbench_cli_main(
        [
            "--tier",
            "0",
            "--scenario",
            "m1_envelope_scoped_recovery",
            "--context-delivery",
            "on",
            "--format",
            "markdown",
            "--output-json",
            str(out_json),
        ]
    )
    assert rc == 0
    stdout_md = capsys.readouterr().out
    assert "## Dual-Model Microbenchmark (M1/M2/M3)" in stdout_md
    saved = json.loads(out_json.read_text(encoding="utf-8"))
    assert saved["context_delivery_mode"] == "on"
    assert saved["tier0"]["context_delivery_mode"] == "on"
    m1_row = saved["tier0"]["scenarios"][0]
    assert m1_row["passed"] is True
    assert m1_row["source_attribution"]["comparison_kind"] == "same_commit_aa"
    assert_public_safe_payload(saved)


def test_tier0_fails_and_blocks_hot_path_attribution_when_m1_baseline_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_run_arm = microbench_scenarios.run_m1_deterministic_arm

    def _failing_base_arm(
        fixture: Any,
        *,
        arm: str,
        source_root: Path | None = None,
        context_delivery: str | None = None,
        deadline_monotonic: float | None = None,
    ) -> dict[str, Any]:
        result = real_run_arm(
            fixture,
            arm=arm,
            source_root=source_root,
            context_delivery=context_delivery,
            deadline_monotonic=deadline_monotonic,
        )
        if arm == "base":
            checks = dict(result["behavior_constraint"].checks)
            checks["blocked_p0_preserved"] = False
            result["behavior_constraint"] = BehaviorConstraintResult(
                passed=False,
                checks=checks,
                violations=("blocked_p0_preserved",),
            )
        return result

    monkeypatch.setattr(microbench_tier0, "run_m1_deterministic_arm", _failing_base_arm)
    report = run_tier0_differential(scenarios=("m1_envelope_scoped_recovery",))

    assert report["passed"] is False
    assert report["cli_output_differential"]["ok"] is False
    assert report["cli_output_differential"]["baseline_valid"] is False
    assert report["cli_output_differential"]["failed_row_count"] >= 1
    m1_row = report["scenarios"][0]
    assert m1_row["passed"] is False
    assert m1_row["base"]["behavior_constraint"]["passed"] is False
    assert m1_row["head"]["behavior_constraint"]["passed"] is True
    assert m1_row["comparison"]["baseline_valid"] is False
    assert m1_row["comparison"]["behavior_preserved"] is False
    assert m1_row["comparison"]["base_violations"] == ["blocked_p0_preserved"]
    assert m1_row["comparison"]["deltas"]["wire_bytes"] < 0
    assert m1_row["source_attribution"]["production_hot_path_covered"] is False
    assert m1_row["source_attribution"]["excluded_from_production_hot_path_claims"] is True
    assert "m1_envelope_scoped_recovery" in report["claim_scope"]["excluded_from_production_hot_path_claims"]
    assert "m1_envelope_scoped_recovery" not in report["claim_scope"]["production_hot_path_scenarios"]


def test_codex_runner_enforces_single_arm_deadline_across_setup_and_subprocess(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time as std_time

    trial_root = tmp_path / "trial"
    project_root = trial_root / "project"
    project_root.mkdir(parents=True)
    auth_file = tmp_path / "auth.json"
    auth_file.write_text('{"tokens":{}}', encoding="utf-8")

    # Marker-writing fake codex script based on _write_fake_codex.
    codex_bin = tmp_path / "fake-codex-marker"
    _write_fake_codex(codex_bin, mode="ok")
    marker_file = tmp_path / "codex-invoked.marker"
    codex_text = codex_bin.read_text(encoding="utf-8")
    codex_bin.write_text(
        codex_text.replace(
            "_ = sys.stdin.read()",
            f"pathlib.Path({str(marker_file)!r}).write_text('started', encoding='utf-8')\n"
            f"if os.environ.get('FAKE_CODEX_SLEEP_SECONDS'):\n"
            f"    import time; time.sleep(float(os.environ['FAKE_CODEX_SLEEP_SECONDS']))\n"
            "_ = sys.stdin.read()",
        ),
        encoding="utf-8",
    )

    # Subcase A: prepare_codex_home exhausts 0.05s budget -> 0 child processes launched.
    clock_a = {"t": 100.0}
    monkeypatch.setattr(std_time, "monotonic", lambda: clock_a["t"])
    real_prepare = microbench_models.prepare_codex_home

    def _slow_prepare_exhausted(*args: Any, **kwargs: Any) -> Any:
        clock_a["t"] += 0.08
        return real_prepare(*args, **kwargs)

    monkeypatch.setattr(microbench_models, "prepare_codex_home", _slow_prepare_exhausted)
    runner = CodexGptMicrobenchRunner(
        codex_bin=str(codex_bin),
        auth_json_path=auth_file,
        sandbox="read-only",
    )
    out_exhausted = runner.run_case(
        scenario_id="m1_envelope_scoped_recovery",
        prompt_text="Read fixture/m1-proof.json",
        trial_root=trial_root,
        project_root=project_root,
        timeout_seconds=0.05,
    )
    assert marker_file.exists() is False
    assert out_exhausted.status == "error"
    assert out_exhausted.error_code == "codex_budget_exhausted"
    assert out_exhausted.verdict is None

    # Subcase B: prepare_codex_home consumes 0.35s of 1.00s budget -> subprocess receives ~0.65s remaining timeout.
    clock_b = {"t": 200.0}
    monkeypatch.setattr(std_time, "monotonic", lambda: clock_b["t"])
    seen_timeouts: list[float | None] = []
    real_sub_run = subprocess.run

    def _slow_prepare_partial(*args: Any, **kwargs: Any) -> Any:
        clock_b["t"] += 0.35
        return real_prepare(*args, **kwargs)

    def _spy_codex_sub_run(*args: Any, **kwargs: Any) -> Any:
        cmd = args[0] if args else kwargs.get("args", [])
        if cmd and str(cmd[0]) == str(codex_bin):
            seen_timeouts.append(kwargs.get("timeout"))
        return real_sub_run(*args, **kwargs)

    monkeypatch.setattr(microbench_models, "prepare_codex_home", _slow_prepare_partial)
    monkeypatch.setattr(microbench_models.subprocess, "run", _spy_codex_sub_run)
    out_partial = runner.run_case(
        scenario_id="m1_envelope_scoped_recovery",
        prompt_text="Read fixture/m1-proof.json",
        trial_root=trial_root,
        project_root=project_root,
        timeout_seconds=1.00,
    )
    assert out_partial.status == "ok", out_partial
    assert marker_file.exists() is True
    assert seen_timeouts == [pytest.approx(0.65, abs=0.02)]

    # Subcase C: child process exceeding remaining timeout fails as codex_timeout.
    monkeypatch.undo()
    monkeypatch.setenv("FAKE_CODEX_SLEEP_SECONDS", "0.25")
    out_timeout = runner.run_case(
        scenario_id="m1_envelope_scoped_recovery",
        prompt_text="Read fixture/m1-proof.json",
        trial_root=trial_root,
        project_root=project_root,
        timeout_seconds=0.05,
    )
    assert out_timeout.status == "error"
    assert out_timeout.error_code == "codex_timeout"


def test_tier1_stops_git_base_resolution_and_export_stages_after_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time as std_time

    class _NeverDispatchedRunner:
        provider = "synthetic"
        model_id = "synthetic-never-dispatched"

        def run_case(self, **kwargs: Any) -> ModelRunOutcome:
            raise AssertionError(f"runner should not be called: {kwargs.get('scenario_id')}")

    real_sub_run = subprocess.run

    # Case A: git read-tree crosses 1.25s deadline by 0.30s (t += 1.55s) -> 0 ls-files, 0 checkout-index, 0 CLI/runner calls.
    clock_a = {"t": 1000.0}
    git_calls_a: list[tuple[list[str], float | None]] = []
    cli_calls_a: list[list[str]] = []

    def _intercept_export_run(*args: Any, **kwargs: Any) -> Any:
        cmd = [str(x) for x in (args[0] if args else kwargs.get("args", []))]
        if any("loopx.cli" in part for part in cmd):
            cli_calls_a.append(cmd)
        if cmd and cmd[0] == "git":
            git_calls_a.append((cmd, kwargs.get("timeout")))
        result = real_sub_run(*args, **kwargs)
        if len(cmd) >= 2 and cmd[0] == "git" and cmd[1] == "read-tree":
            clock_a["t"] += 1.55
        return result

    monkeypatch.setattr(std_time, "monotonic", lambda: clock_a["t"])
    monkeypatch.setattr(subprocess, "run", _intercept_export_run)
    report_a = run_tier1_dual_model_suite(
        runners=[_NeverDispatchedRunner()],
        scenarios=("m1_envelope_scoped_recovery",),
        base_ref="origin/main",
        main_ref="origin/main",
        max_total_seconds=1.25,
        per_call_timeout_seconds=10.0,
    )
    assert report_a["within_budget"] is False
    assert report_a["passed"] is False
    assert cli_calls_a == []
    assert any(len(cmd) >= 2 and cmd[1] == "read-tree" for cmd, _ in git_calls_a)
    assert not any(len(cmd) >= 2 and cmd[1] == "ls-files" for cmd, _ in git_calls_a)
    assert not any(len(cmd) >= 2 and cmd[1] == "checkout-index" for cmd, _ in git_calls_a)
    for cmd, timeout in git_calls_a:
        assert timeout is not None and timeout <= 1.25, (cmd, timeout)
    assert report_a["results"][0]["base"]["error_code"] == "tier1_wall_budget_exhausted"
    assert report_a["results"][0]["head"]["error_code"] == "tier1_wall_budget_exhausted"

    # Case B: git merge-base --is-ancestor crosses 0.50s deadline -> 0 pin / export / CLI / runner calls.
    head_sha = microbench_tier0._head_commit()
    clock_b = {"t": 2000.0}
    git_calls_b: list[tuple[list[str], float | None]] = []

    def _intercept_pin_run(*args: Any, **kwargs: Any) -> Any:
        cmd = [str(x) for x in (args[0] if args else kwargs.get("args", []))]
        if cmd and cmd[0] == "git":
            git_calls_b.append((cmd, kwargs.get("timeout")))
        result = real_sub_run(*args, **kwargs)
        if len(cmd) >= 3 and cmd[0] == "git" and cmd[1] == "merge-base" and "--is-ancestor" in cmd:
            clock_b["t"] += 0.80
        return result

    monkeypatch.setattr(std_time, "monotonic", lambda: clock_b["t"])
    monkeypatch.setattr(subprocess, "run", _intercept_pin_run)
    report_b = run_tier1_dual_model_suite(
        runners=[_NeverDispatchedRunner()],
        scenarios=("m1_envelope_scoped_recovery",),
        base_ref=head_sha,
        main_ref="origin/main",
        max_total_seconds=0.50,
        per_call_timeout_seconds=10.0,
    )
    assert report_b["within_budget"] is False
    assert report_b["passed"] is False
    assert any("--is-ancestor" in cmd for cmd, _ in git_calls_b)
    assert not any(len(cmd) >= 2 and cmd[1] == "read-tree" for cmd, _ in git_calls_b)
    assert not any(len(cmd) >= 2 and cmd[1] == "ls-files" for cmd, _ in git_calls_b)
    assert not any(len(cmd) >= 2 and cmd[1] == "checkout-index" for cmd, _ in git_calls_b)
    assert report_b["results"][0]["base"]["error_code"] == "tier1_wall_budget_exhausted"
