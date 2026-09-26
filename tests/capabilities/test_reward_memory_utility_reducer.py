from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from loopx.capabilities.reward_memory.memory_utility import (
    build_reward_memory_utility_observation,
)
from loopx.capabilities.reward_memory.utility_reducer import (
    MEMORY_UTILITY_PROJECTION_SCHEMA_VERSION,
    MEMORY_UTILITY_REDUCER_VERSION,
    build_reward_memory_utility_projection,
    reduce_reward_memory_utility_observations,
    validate_reward_memory_utility_projection,
)


CREATED_AT = "2026-08-15T00:00:00Z"
MEMORY_A = "0123456789abcdef"
MEMORY_B = "fedcba9876543210"


def _application(memories: list[str] | None = None) -> dict[str, Any]:
    return {
        "schema_version": "reward_memory_application_receipt_v0",
        "application_id": "application:stage2",
        "artifact_ref": "artifact:stage2",
        "corpus_id": "reward-memory-corpus",
        "surface_id": "loopx.issue_fix",
        "mode": "function_boundary",
        "query_kind": "business_recall",
        "query_evidence": [
            {
                "query_digest": "aabbccddeeff0011",
                "query_summary": "bounded utility fixture",
                "exact_query_exposed": False,
            }
        ],
        "outcome": "applied",
        "memory_ref_digests": memories or [MEMORY_A],
        "reasoning_summary": "Applied the recalled guidance to the fixture.",
        "current_artifact_verified": True,
        "result_readback_verified": True,
        "provider_call_count": 1,
        "model_reasoning_preserved": True,
        "grants_new_action_authority": False,
        "external_writes_performed": False,
        "raw_content_captured": False,
    }


def _outcome() -> dict[str, Any]:
    return {
        "verified": True,
        "outcome_ref": "effect:stage2",
        "artifact_ref": "artifact:stage2",
        "outcome_status": "succeeded",
    }


def _context() -> dict[str, Any]:
    return {
        "scope": {
            "agent_id": "agent:stage2",
            "project_id": "project:loopx",
            "corpus_id": "reward-memory-corpus",
            "surface_id": "loopx.issue_fix",
        },
        "retrieval_snapshot_ref": "retrieval:stage2",
        "policy_snapshot_ref": "policy:stage2",
    }


def _proposal(
    *,
    memories: list[str] | None = None,
    label: str = "unknown",
    level: str = "item",
    basis: str = "insufficient",
    confidence: float = 0.0,
    evidence_refs: list[str] | None = None,
    evaluator_ref: str = "evaluator:stage2",
    evaluation_version: str = "evaluation:stage2",
) -> dict[str, Any]:
    context = _context()
    outcome = _outcome()
    return {
        "scope": deepcopy(context["scope"]),
        "application_id": "application:stage2",
        "artifact_ref": outcome["artifact_ref"],
        "outcome_ref": outcome["outcome_ref"],
        "outcome_status": outcome["outcome_status"],
        "retrieval_snapshot_ref": context["retrieval_snapshot_ref"],
        "policy_snapshot_ref": context["policy_snapshot_ref"],
        "memory_ref_digests": memories or [MEMORY_A],
        "utility_label": label,
        "attribution_level": level,
        "evidence_basis": basis,
        "confidence": confidence,
        "reason_codes": ["stage2_fixture"],
        "evidence_refs": evidence_refs or [],
        "evaluator_ref": evaluator_ref,
        "evaluation_version": evaluation_version,
    }


def _observation(
    *,
    application: dict[str, Any] | None = None,
    proposal: dict[str, Any] | None = None,
    created_at: str = CREATED_AT,
) -> dict[str, Any]:
    context = _context()
    return build_reward_memory_utility_observation(
        application or _application(),
        _outcome(),
        context,
        proposal or _proposal(),
        created_at=created_at,
    )


def _reduce(observations: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    context = _context()
    options = {
        "scope": context["scope"],
        "retrieval_snapshot_ref": context["retrieval_snapshot_ref"],
        "policy_snapshot_ref": context["policy_snapshot_ref"],
    }
    options.update(kwargs)
    return reduce_reward_memory_utility_observations(
        observations,
        **options,
    )


def test_labels_have_bounded_read_only_subjects() -> None:
    observations = [
        _observation(
            proposal=_proposal(
                label="helpful",
                basis="deterministic_effect",
                confidence=0.9,
                evidence_refs=["test:helpful"],
                evaluation_version="evaluation:helpful",
            )
        ),
        _observation(
            proposal=_proposal(
                label="harmful",
                basis="deterministic_effect",
                confidence=0.8,
                evidence_refs=["test:harmful"],
                evaluation_version="evaluation:harmful",
            )
        ),
        _observation(
            proposal=_proposal(
                label="neutral",
                basis="deterministic_effect",
                confidence=0.7,
                evidence_refs=["test:neutral"],
                evaluation_version="evaluation:neutral",
            )
        ),
        _observation(),
    ]
    projection = _reduce(observations)

    assert projection["schema_version"] == MEMORY_UTILITY_PROJECTION_SCHEMA_VERSION
    assert projection["accepted_observation_count"] == 4
    assert all(
        -1.0 <= subject["utility_estimate"] <= 1.0
        and 0.0 <= subject["confidence"] <= 1.0
        and 0.0 <= subject["uncertainty"] <= 1.0
        for subject in projection["subjects"]
    )
    assert projection["read_only"] is True
    assert projection["grants_new_action_authority"] is False
    assert projection["provider_write_performed"] is False
    assert projection["raw_content_captured"] is False


def test_exact_replay_is_a_noop_even_when_retry_timestamp_changes() -> None:
    first = _observation(created_at=CREATED_AT)
    retry = _observation(created_at="2026-08-15T00:05:00Z")
    assert first["observation_id"] == retry["observation_id"]

    projection = _reduce([first, retry])
    reversed_projection = _reduce([retry, first])

    assert projection["accepted_observation_count"] == 1
    assert projection["duplicate_observation_count"] == 1
    assert projection["conflicting_observation_count"] == 0
    assert len(projection["observation_history"]) == 1
    assert projection == reversed_projection


def test_conflict_counters_do_not_misclassify_replays() -> None:
    original = _observation(
        proposal=_proposal(
            label="helpful",
            basis="evaluator_inference",
            confidence=0.5,
            evidence_refs=["inference:counter"],
        )
    )
    retry = _observation(
        proposal=_proposal(
            label="helpful",
            basis="evaluator_inference",
            confidence=0.5,
            evidence_refs=["inference:counter"],
        ),
        created_at="2026-08-15T00:05:00Z",
    )
    conflicting = deepcopy(original)
    conflicting["utility_label"] = "harmful"

    projection = _reduce([original, retry, conflicting])

    assert projection["duplicate_observation_count"] == 1
    assert projection["conflicting_observation_count"] == 1
    assert projection["rejected_observation_count"] == 3


def test_equivalent_timezone_retry_is_order_independent() -> None:
    first = _observation(created_at="2026-08-15T00:00:00Z")
    equivalent = _observation(created_at="2026-08-15T01:00:00+01:00")
    assert first["observation_id"] == equivalent["observation_id"]

    forward = _reduce([first, equivalent])
    reverse = _reduce([equivalent, first])

    assert forward == reverse
    assert forward["observation_history"][0]["created_at"] == ("2026-08-15T00:00:00Z")


def test_same_identity_with_changed_judgment_is_quarantined() -> None:
    original = _observation(
        proposal=_proposal(
            label="helpful",
            basis="evaluator_inference",
            confidence=0.5,
            evidence_refs=["inference:one"],
        )
    )
    conflicting = deepcopy(original)
    conflicting["utility_label"] = "harmful"

    projection = _reduce([original, conflicting])

    assert projection["status"] == "review_required"
    assert projection["projection_ready"] is False
    assert projection["accepted_observation_count"] == 0
    assert projection["conflicting_observation_count"] == 1
    assert projection["rejected_observation_count"] == 2
    assert projection["rejections"][0]["reason_codes"] == [
        "conflicting_observation_delivery"
    ]
    assert projection["rejections"][0]["quarantine_proposed"] is True
    assert projection["rejections"][0]["automatic_deletion"] is False
    assert projection["rejections"][0]["action_authority_granted"] is False

    tampered = deepcopy(projection)
    tampered["rejections"][0]["quarantine_proposed"] = False
    with pytest.raises(ValueError, match="propose quarantine"):
        validate_reward_memory_utility_projection(tampered)


def test_strongest_unknown_blocks_weaker_directional_inference() -> None:
    weak = _observation(
        proposal=_proposal(
            label="helpful",
            basis="evaluator_inference",
            confidence=1.0,
            evidence_refs=["inference:weak"],
            evaluation_version="evaluation:weak-direction",
        )
    )
    explicit_unknown = _observation(
        proposal=_proposal(
            label="unknown",
            basis="owner_correction",
            confidence=1.0,
            evidence_refs=["owner:unknown"],
            evaluation_version="evaluation:owner-unknown",
        )
    )

    projection = _reduce([weak, explicit_unknown])
    subject = projection["subjects"][0]

    assert subject["effective_utility_label"] == "unknown"
    assert subject["effective_evidence_basis"] == "owner_correction"
    assert subject["utility_estimate"] == 0.0
    assert subject["review"]["state"] == "conflict"
    assert subject["review"]["reason_codes"] == ["strongest_evidence_unknown"]


def test_stronger_evidence_overrides_weaker_inference_without_erasing_history() -> None:
    weak = _observation(
        proposal=_proposal(
            label="helpful",
            basis="evaluator_inference",
            confidence=1.0,
            evidence_refs=["inference:helpful"],
            evaluation_version="evaluation:weak",
        )
    )
    strong = _observation(
        proposal=_proposal(
            label="harmful",
            basis="owner_correction",
            confidence=0.6,
            evidence_refs=["owner:correction"],
            evaluation_version="evaluation:strong",
        )
    )

    projection = _reduce([weak, strong])
    subject = projection["subjects"][0]

    assert subject["effective_utility_label"] == "harmful"
    assert subject["effective_evidence_basis"] == "owner_correction"
    assert subject["utility_estimate"] < 0
    assert subject["support"] == {
        "helpful": 1,
        "harmful": 1,
        "neutral": 0,
        "unknown": 0,
    }
    assert len(projection["observation_history"]) == 2


def test_same_tier_conflict_requires_review_and_is_order_independent() -> None:
    helpful = _observation(
        proposal=_proposal(
            label="helpful",
            basis="controlled_replay",
            confidence=0.8,
            evidence_refs=["replay:helpful"],
            evaluation_version="evaluation:helpful",
        )
    )
    harmful = _observation(
        proposal=_proposal(
            label="harmful",
            basis="controlled_replay",
            confidence=0.8,
            evidence_refs=["replay:harmful"],
            evaluation_version="evaluation:harmful",
        )
    )

    forward = _reduce([helpful, harmful])
    reverse = _reduce([harmful, helpful])

    assert forward == reverse
    assert forward["status"] == "review_required"
    assert forward["projection_ready"] is False
    assert forward["subjects"][0]["review"]["state"] == "conflict"
    assert forward["subjects"][0]["effective_utility_label"] == "unknown"


def test_set_attribution_never_creates_item_subjects() -> None:
    observation = _observation(
        application=_application([MEMORY_A, MEMORY_B]),
        proposal=_proposal(
            memories=[MEMORY_A, MEMORY_B],
            label="helpful",
            level="set",
            basis="deterministic_effect",
            confidence=0.9,
            evidence_refs=["effect:set"],
        ),
    )

    projection = _reduce([observation])

    assert projection["item_subject_count"] == 0
    assert projection["set_subject_count"] == 1
    assert projection["subjects"][0]["attribution_level"] == "set"
    assert projection["subjects"][0]["memory_ref_digests"] == [MEMORY_A, MEMORY_B]


def test_none_attribution_is_lineage_only() -> None:
    projection = _reduce([_observation()])
    subject = projection["subjects"][0]

    assert subject["attribution_level"] == "item"

    none_observation = _observation(
        proposal=_proposal(
            label="unknown",
            level="none",
            basis="insufficient",
            confidence=0.9,
        )
    )
    none_projection = _reduce([none_observation])
    none_subject = none_projection["subjects"][0]
    assert none_subject["attribution_level"] == "none"
    assert none_subject["utility_estimate"] == 0.0
    assert none_subject["review"]["state"] == "unresolved_attribution"


def test_scope_and_snapshot_mismatch_fail_closed() -> None:
    observation = _observation()
    wrong_scope = _reduce(
        [observation],
        scope={
            "agent_id": "agent:other",
            "project_id": "project:loopx",
            "corpus_id": "reward-memory-corpus",
            "surface_id": "loopx.issue_fix",
        },
    )
    wrong_snapshot = _reduce([observation], retrieval_snapshot_ref="retrieval:other")

    for rejected in (wrong_scope, wrong_snapshot):
        assert rejected["ok"] is False
        assert rejected["status"] == "rejected"
        assert rejected["projection_ready"] is False
        assert rejected["subjects"] == []
        assert rejected["read_only"] is True
    assert "scope_mismatch" in wrong_scope["rejections"][0]["reason_codes"]
    assert (
        "retrieval_snapshot_mismatch" in wrong_snapshot["rejections"][0]["reason_codes"]
    )


def test_previous_projection_identity_and_reducer_version_are_checked() -> None:
    observation = _observation()
    baseline = _reduce([observation])
    mismatch = _reduce(
        [observation],
        reducer_version="memory_utility_reducer_v1",
        previous_projection=baseline,
    )

    assert mismatch["ok"] is False
    assert mismatch["status"] == "rejected"
    assert mismatch["rejections"][0]["reason_codes"] == ["reducer_identity_mismatch"]
    assert baseline["reducer_version"] == MEMORY_UTILITY_REDUCER_VERSION


def test_malformed_observation_is_rejected_without_projection_state() -> None:
    malformed = _observation()
    malformed["schema_version"] = "memory_utility_observation_v99"

    projection = _reduce([malformed])

    assert projection["ok"] is False
    assert projection["status"] == "rejected"
    assert projection["accepted_observation_count"] == 0
    assert projection["subjects"] == []
    assert "observation_schema_mismatch" in projection["rejections"][0]["reason_codes"]


def test_overflowing_observation_number_is_rejected_fail_closed() -> None:
    malformed = _observation()
    malformed["confidence"] = 10**1000

    projection = _reduce([malformed])

    assert projection["ok"] is False
    assert projection["status"] == "rejected"
    assert projection["subjects"] == []
    assert projection["rejected_observation_count"] == 1
    assert projection["rejections"][0]["reason_codes"] == ["observation_malformed"]


def test_overflowing_projection_numbers_are_rejected_by_readback_validator() -> None:
    projection = _reduce([_observation()])

    for field in ("utility_estimate", "confidence", "uncertainty"):
        tampered = deepcopy(projection)
        tampered["subjects"][0][field] = 10**1000
        with pytest.raises(ValueError, match="numeric fields are invalid"):
            validate_reward_memory_utility_projection(tampered)

    history_tampered = deepcopy(projection)
    history_tampered["observation_history"][0]["confidence"] = 10**1000
    with pytest.raises(ValueError, match="history confidence is invalid"):
        validate_reward_memory_utility_projection(history_tampered)


def test_private_or_unknown_observation_fields_fail_closed() -> None:
    unsafe = _observation()
    unsafe["raw_prompt"] = "private prompt must never enter the projection"

    projection = _reduce([unsafe])

    assert projection["ok"] is False
    assert projection["status"] == "rejected"
    assert projection["subjects"] == []
    assert projection["rejections"][0]["reason_codes"] == ["observation_malformed"]


def test_harmful_utility_only_proposes_attenuation_not_deletion_or_authority() -> None:
    harmful = _observation(
        proposal=_proposal(
            label="harmful",
            basis="deterministic_effect",
            confidence=0.95,
            evidence_refs=["test:regression"],
        )
    )
    projection = _reduce([harmful])
    review = projection["subjects"][0]["review"]

    assert review["state"] == "attenuation_proposed"
    assert review["proposed_action"] == "attenuate_or_review"
    assert review["automatic_deletion"] is False
    assert review["action_authority_granted"] is False
    assert projection["review_proposals"][0]["automatic_deletion"] is False


def test_projection_is_deterministic_and_validator_rejects_tampering() -> None:
    first = _observation(
        proposal=_proposal(
            label="helpful",
            basis="deterministic_effect",
            confidence=0.7,
            evidence_refs=["effect:one"],
            evaluation_version="evaluation:one",
        )
    )
    second = _observation(
        proposal=_proposal(
            label="neutral",
            basis="evaluator_inference",
            confidence=0.4,
            evidence_refs=["inference:two"],
            evaluation_version="evaluation:two",
        ),
        created_at="2026-08-16T00:00:00Z",
    )
    left = _reduce([first, second])
    right = _reduce([second, first])
    assert json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)

    tampered = deepcopy(left)
    tampered["subjects"][0]["utility_estimate"] = 2.0
    with pytest.raises(ValueError, match="out of bounds"):
        validate_reward_memory_utility_projection(tampered)


def test_builder_alias_returns_valid_projection() -> None:
    observation = _observation()
    context = _context()
    projection = build_reward_memory_utility_projection(
        [observation],
        scope=context["scope"],
        retrieval_snapshot_ref=context["retrieval_snapshot_ref"],
        policy_snapshot_ref=context["policy_snapshot_ref"],
    )
    assert validate_reward_memory_utility_projection(projection) == projection


def test_projection_validator_cross_binds_subject_latest_and_memory_digests() -> None:
    projection = _reduce([_observation()])

    latest_tampered = deepcopy(projection)
    latest_tampered["subjects"][0]["last_observation_id"] = "muo_" + "0" * 64
    with pytest.raises(ValueError, match="latest observation fields"):
        validate_reward_memory_utility_projection(latest_tampered)

    digest_tampered = deepcopy(projection)
    digest_tampered["subjects"][0]["memory_ref_digests"] = [MEMORY_B]
    with pytest.raises(ValueError, match="memory digests"):
        validate_reward_memory_utility_projection(digest_tampered)


def test_projection_validator_cross_binds_review_proposals_to_subjects() -> None:
    projection = _reduce(
        [
            _observation(
                proposal=_proposal(
                    label="harmful",
                    basis="deterministic_effect",
                    confidence=0.8,
                    evidence_refs=["effect:harmful"],
                )
            )
        ]
    )
    assert projection["review_proposals"]

    orphan = deepcopy(projection)
    orphan["review_proposals"][0]["subject_id"] = "mui_orphan"
    with pytest.raises(ValueError, match="unknown subject"):
        validate_reward_memory_utility_projection(orphan)

    stale = deepcopy(projection)
    stale["review_proposals"][0]["memory_ref_digests"] = [MEMORY_B]
    with pytest.raises(ValueError, match="memory digests"):
        validate_reward_memory_utility_projection(stale)


def test_projection_validator_rejects_inconsistent_delivery_and_state_counters() -> (
    None
):
    projection = _reduce([_observation()])
    duplicate_tampered = deepcopy(projection)
    duplicate_tampered["duplicate_observation_count"] = 1024
    with pytest.raises(ValueError, match="duplicate_observation_count"):
        validate_reward_memory_utility_projection(duplicate_tampered)

    empty = _reduce([])
    assert empty["status"] == "empty"
    assert validate_reward_memory_utility_projection(empty) == empty

    malformed = _observation()
    malformed["schema_version"] = "memory_utility_observation_v99"
    rejected = _reduce([malformed])
    assert rejected["status"] == "rejected"
    assert validate_reward_memory_utility_projection(rejected) == rejected


def test_projection_validator_rejects_private_or_unhashable_tampering() -> None:
    projection = _reduce([_observation()])

    private_ref = deepcopy(projection)
    private_ref["observation_history"][0]["outcome_ref"] = "viking://private/outcome"
    with pytest.raises(ValueError, match="canonical|opaque|fingerprint"):
        validate_reward_memory_utility_projection(private_ref)

    unhashable = deepcopy(projection)
    unhashable["subjects"][0]["effective_utility_label"] = ["helpful"]
    with pytest.raises(ValueError):
        validate_reward_memory_utility_projection(unhashable)


def test_history_budget_retains_each_subject_latest_observation() -> None:
    observations = [
        _observation(
            proposal=_proposal(
                label="helpful",
                basis=("deterministic_effect" if index == 0 else "evaluator_inference"),
                confidence=0.1,
                evidence_refs=[
                    f"effect:{index}" if index == 0 else f"inference:{index}"
                ],
                evaluation_version=f"evaluation:{index}",
            ),
            created_at=f"2026-08-15T00:{index // 60:02d}:{index % 60:02d}Z",
        )
        for index in range(257)
    ]
    projection = _reduce(observations)
    assert projection["accepted_observation_count"] == 257
    assert projection["observation_history_truncated"] is True
    assert len(projection["observation_history"]) == 256
    assert projection["subjects"][0]["last_observation_id"] in {
        entry["observation_id"] for entry in projection["observation_history"]
    }
    assert validate_reward_memory_utility_projection(projection) == projection

    weaker_basis = deepcopy(projection)
    weaker_basis["subjects"][0]["effective_evidence_basis"] = "evaluator_inference"
    with pytest.raises(ValueError, match="effective_evidence_basis is inconsistent"):
        validate_reward_memory_utility_projection(weaker_basis)

    unsupported_label = deepcopy(projection)
    unsupported_label["subjects"][0]["effective_utility_label"] = "neutral"
    unsupported_label["subjects"][0]["utility_estimate"] = 0.0
    with pytest.raises(ValueError, match="has no supporting observation"):
        validate_reward_memory_utility_projection(unsupported_label)


def test_projection_validator_rejects_noncanonical_timestamp_whitespace() -> None:
    projection = _reduce([_observation()])
    tampered = deepcopy(projection)
    tampered["observation_history"][0]["created_at"] = (
        f" {tampered['observation_history'][0]['created_at']} "
    )
    with pytest.raises(ValueError, match="ISO timestamp"):
        validate_reward_memory_utility_projection(tampered)


def test_cli_rejects_explicit_empty_or_null_reducer_version(tmp_path: Path) -> None:
    context = _context()
    for value in (None, ""):
        input_path = tmp_path / ("null.json" if value is None else "empty.json")
        input_path.write_text(
            json.dumps(
                {
                    "observations": [],
                    "scope": context["scope"],
                    "retrieval_snapshot_ref": context["retrieval_snapshot_ref"],
                    "policy_snapshot_ref": context["policy_snapshot_ref"],
                    "reducer_version": value,
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
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 2
        payload = json.loads(completed.stdout)
        assert payload["status"] == "invalid_request"
        assert "reducer_version" in payload["error"]
