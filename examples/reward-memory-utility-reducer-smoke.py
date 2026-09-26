#!/usr/bin/env python3
"""Smoke-test the Stage-2 utility reducer and its read-only CLI projection."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from loopx.capabilities.reward_memory import (  # noqa: E402
    build_reward_memory_utility_observation,
    reduce_reward_memory_utility_observations,
)


MEMORY_A = "0123456789abcdef"
MEMORY_B = "fedcba9876543210"
SCOPE = {
    "agent_id": "agent:stage2-smoke",
    "project_id": "project:loopx",
    "corpus_id": "reward-memory-corpus",
    "surface_id": "loopx.issue_fix",
}
RETRIEVAL = "retrieval:stage2-smoke"
POLICY = "policy:stage2-smoke"


def _observation(
    *,
    memories: list[str] | None = None,
    label: str = "unknown",
    level: str = "item",
    basis: str = "insufficient",
    confidence: float = 0.0,
    evidence_ref: str | None = None,
    version: str = "evaluation:stage2-smoke",
) -> dict[str, Any]:
    memories = memories or [MEMORY_A]
    application = {
        "schema_version": "reward_memory_application_receipt_v0",
        "application_id": "application:stage2-smoke",
        "artifact_ref": "artifact:stage2-smoke",
        "corpus_id": SCOPE["corpus_id"],
        "surface_id": SCOPE["surface_id"],
        "mode": "function_boundary",
        "query_kind": "business_recall",
        "query_evidence": [
            {
                "query_digest": "aabbccddeeff0011",
                "query_summary": "bounded smoke fixture",
                "exact_query_exposed": False,
            }
        ],
        "outcome": "applied",
        "memory_ref_digests": memories,
        "reasoning_summary": "Applied the compact smoke guidance.",
        "current_artifact_verified": True,
        "result_readback_verified": True,
        "provider_call_count": 1,
        "model_reasoning_preserved": True,
        "grants_new_action_authority": False,
        "external_writes_performed": False,
        "raw_content_captured": False,
    }
    outcome = {
        "verified": True,
        "outcome_ref": "effect:stage2-smoke",
        "artifact_ref": application["artifact_ref"],
        "outcome_status": "succeeded",
    }
    context = {
        "scope": deepcopy(SCOPE),
        "retrieval_snapshot_ref": RETRIEVAL,
        "policy_snapshot_ref": POLICY,
    }
    proposal = {
        "scope": deepcopy(SCOPE),
        "application_id": application["application_id"],
        "artifact_ref": outcome["artifact_ref"],
        "outcome_ref": outcome["outcome_ref"],
        "outcome_status": outcome["outcome_status"],
        "retrieval_snapshot_ref": RETRIEVAL,
        "policy_snapshot_ref": POLICY,
        "memory_ref_digests": memories,
        "utility_label": label,
        "attribution_level": level,
        "evidence_basis": basis,
        "confidence": confidence,
        "reason_codes": ["stage2_smoke"],
        "evidence_refs": [evidence_ref] if evidence_ref else [],
        "evaluator_ref": "evaluator:stage2-smoke",
        "evaluation_version": version,
    }
    return build_reward_memory_utility_observation(
        application,
        outcome,
        context,
        proposal,
        created_at="2026-08-15T00:00:00Z",
    )


def main() -> int:
    helpful = _observation(
        label="helpful",
        basis="evaluator_inference",
        confidence=0.4,
        evidence_ref="inference:stage2",
    )
    correction = _observation(
        label="harmful",
        basis="owner_correction",
        confidence=0.8,
        evidence_ref="owner:stage2",
        version="evaluation:stage2-correction",
    )
    set_observation = _observation(
        memories=[MEMORY_A, MEMORY_B],
        label="neutral",
        level="set",
        basis="deterministic_effect",
        confidence=0.7,
        evidence_ref="effect:stage2-set",
        version="evaluation:stage2-set",
    )
    observations = [helpful, correction, set_observation, helpful]
    projection = reduce_reward_memory_utility_observations(
        observations,
        scope=SCOPE,
        retrieval_snapshot_ref=RETRIEVAL,
        policy_snapshot_ref=POLICY,
    )
    assert projection["schema_version"] == "memory_utility_projection_v0", projection
    assert projection["status"] == "ready", projection
    assert projection["accepted_observation_count"] == 3, projection
    assert projection["duplicate_observation_count"] == 1, projection
    item = next(
        subject
        for subject in projection["subjects"]
        if subject["attribution_level"] == "item"
    )
    assert item["effective_utility_label"] == "harmful", projection
    assert item["review"]["automatic_deletion"] is False, projection
    assert projection["item_subject_count"] == 1, projection
    assert projection["set_subject_count"] == 1, projection
    serialized = json.dumps(projection, sort_keys=True)
    assert "provider_uri" not in serialized
    assert "raw_content" in serialized
    assert all(
        flag is False
        for flag in (
            projection["grants_new_action_authority"],
            projection["provider_write_performed"],
            projection["external_writes_performed"],
            projection["raw_content_captured"],
        )
    )

    with tempfile.TemporaryDirectory(prefix="loopx-reward-memory-smoke-") as directory:
        input_path = Path(directory) / "projection-input.json"
        input_path.write_text(
            json.dumps(
                {
                    "observations": observations,
                    "scope": SCOPE,
                    "retrieval_snapshot_ref": RETRIEVAL,
                    "policy_snapshot_ref": POLICY,
                }
            ),
            encoding="utf-8",
        )
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "loopx.cli",
                "--format",
                "json",
                "reward-memory",
                "utility-project",
                "--input",
                str(input_path),
            ],
            cwd=REPO_ROOT,
            env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr or completed.stdout
        cli_projection = json.loads(completed.stdout)
        assert cli_projection == projection, cli_projection
        assert str(input_path) not in json.dumps(cli_projection, sort_keys=True)

    print("reward-memory-utility-reducer-smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
