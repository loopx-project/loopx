#!/usr/bin/env python3
"""Offline public smoke for Tier-0 (<15s) and stubbed Tier-1 dual-model microbench."""

from __future__ import annotations

import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from benchmark.microbench.metrics import (  # noqa: E402
    EfficiencyMetrics,
    assert_public_safe_payload,
)
from benchmark.microbench.models import ModelRunOutcome  # noqa: E402
from benchmark.microbench.scenarios import (  # noqa: E402
    M1_BLOCKED_TODO_ID,
    M1_PROOF_TOKEN,
    M1_SELECTED_TODO_ID,
    M3_AGENT_PRIMARY,
    M3_ARTIFACT_TOKEN,
    M3_CLAIM_TODO_ID,
    M3_GATE_TODO_ID,
)
from benchmark.microbench.tier0 import run_tier0_differential  # noqa: E402
from benchmark.microbench.tier1 import run_tier1_dual_model_suite  # noqa: E402


class _ConformanceStubRunner:
    def __init__(self, *, provider: str, model_id: str) -> None:
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
        wire_bytes = len(prompt_text.encode("utf-8"))
        prompt_tokens = max(20, wire_bytes // 4)
        if scenario_id == "m1_envelope_scoped_recovery":
            verdict = {
                "schema_version": "microbench_model_verdict_v0",
                "scenario_id": scenario_id,
                "decision": "execute",
                "selected_todo_id": M1_SELECTED_TODO_ID,
                "preserved_blocked_todo_ids": [M1_BLOCKED_TODO_ID],
                "extracted_proof": M1_PROOF_TOKEN,
                "preserved_failure_facts": [],
                "preserved_open_obligations": [],
                "claim_owner": None,
                "spend_attempted": False,
                "tool_actions_summary": ["read_proof", "context_read"],
            }
        elif scenario_id == "m2_epoch_prefetch_semantic_fold":
            verdict = {
                "schema_version": "microbench_model_verdict_v0",
                "scenario_id": scenario_id,
                "decision": "execute",
                "selected_todo_id": "todo_m2_unit",
                "preserved_blocked_todo_ids": [],
                "extracted_proof": None,
                "preserved_failure_facts": [
                    "retry worker ignores expired lease backoff ceiling",
                    "duplicate spend rejected with idempotent replay check",
                ],
                "preserved_open_obligations": [
                    "verify backoff ceiling test on sqlite provider",
                    "preserve terminal settlement receipt integrity",
                ],
                "claim_owner": None,
                "spend_attempted": False,
                "tool_actions_summary": ["fold_check"],
            }
        else:
            is_gate = "subcase=blocked_user_gate" in prompt_text
            verdict = {
                "schema_version": "microbench_model_verdict_v0",
                "scenario_id": scenario_id,
                "decision": "ask_user" if is_gate else "execute",
                "selected_todo_id": None if is_gate else M3_CLAIM_TODO_ID,
                "preserved_blocked_todo_ids": [M3_GATE_TODO_ID] if is_gate else [],
                "extracted_proof": None if is_gate else M3_ARTIFACT_TOKEN,
                "preserved_failure_facts": [],
                "preserved_open_obligations": [],
                "claim_owner": None if is_gate else M3_AGENT_PRIMARY,
                "spend_attempted": False,
                "tool_actions_summary": ["check_gate" if is_gate else "verify_artifact"],
            }
        return ModelRunOutcome(
            model_id=self.model_id,
            provider=self.provider,
            status="ok",
            error_code=None,
            verdict=verdict,
            efficiency=EfficiencyMetrics(
                wire_bytes=wire_bytes,
                prompt_tokens=prompt_tokens,
                cached_tokens=0,
                completion_tokens=48,
                reasoning_tokens=0,
                total_tokens=prompt_tokens + 48,
                tool_calls=1,
                round_trips=1,
                wall_seconds=0.05,
            ),
            observed_commands=("stub_read",),
            telemetry_source="stub",
        )


def main() -> int:
    tier0 = run_tier0_differential()
    assert tier0["passed"] is True, json.dumps(tier0, indent=2)
    assert tier0["wall_seconds"] < 15.0, tier0["wall_seconds"]
    assert_public_safe_payload(tier0, label="tier0_smoke")

    tier1 = run_tier1_dual_model_suite(
        runners=(
            _ConformanceStubRunner(provider="codex", model_id="gpt-6.2-sol"),
            _ConformanceStubRunner(provider="ark", model_id="deepseek-v4-1-flash-260910"),
        ),
        max_total_seconds=60.0,
    )
    assert tier1["passed"] is True, json.dumps(tier1, indent=2)
    assert len(tier1["results"]) == 6
    assert_public_safe_payload(tier1, label="tier1_smoke")
    print(
        "benchmark-dual-model-microbench-smoke ok "
        f"tier0_wall={tier0['wall_seconds']}s rows={len(tier1['results'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
