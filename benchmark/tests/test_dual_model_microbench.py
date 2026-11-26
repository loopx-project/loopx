"""Tests for Tier-0 (<15s differential) and Tier-1 (dual-model M1/M2/M3) microbenchmark."""

from __future__ import annotations

import json
from pathlib import Path
import stat
import subprocess
import sys
from typing import Any

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
    CodexGptMicrobenchRunner,
    DeepSeekArkMicrobenchRunner,
    ModelRunOutcome,
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
from benchmark.microbench.tier0 import run_tier0_differential
from benchmark.microbench.tier1 import run_tier1_dual_model_suite


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
                "usage": {"prompt_tokens": 140, "completion_tokens": 15, "total_tokens": 155},
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
            "tool_actions_summary": ["cat fixture/m1-proof.json"],
        }
        return {
            "usage": {"prompt_tokens": 180, "completion_tokens": 40, "total_tokens": 220},
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
            verdict = {
                "schema_version": "microbench_model_verdict_v0",
                "scenario_id": scenario_id,
                "decision": "execute",
                "selected_todo_id": M1_SELECTED_TODO_ID if not is_head else M1_BLOCKED_TODO_ID,
                "preserved_blocked_todo_ids": [M1_BLOCKED_TODO_ID] if not is_head else [],
                "extracted_proof": M1_PROOF_TOKEN if not is_head else None,
                "preserved_failure_facts": [] if is_head else [
                    "retry worker ignores expired lease backoff ceiling",
                    "duplicate spend rejected with idempotent replay check",
                ],
                "preserved_open_obligations": [] if is_head else [
                    "verify backoff ceiling test on sqlite provider",
                    "preserve terminal settlement receipt integrity",
                ],
                "claim_owner": M3_AGENT_PRIMARY,
                "spend_attempted": False,
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
