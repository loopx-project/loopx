from __future__ import annotations

import hashlib
import json
import argparse
from pathlib import Path
from typing import Any

import pytest

from loopx.capabilities.agent_turn_recall.cli import (
    _deduplicated_payload,
    _load_receipt,
    _resolve_session_ref,
    _validate_quota_identity,
    _write_receipt,
)
from loopx.capabilities.agent_turn_recall import runtime as recall_runtime
from loopx.capabilities.agent_turn_recall import cli as recall_cli
from loopx.capabilities.reward_memory import read_authority
from loopx.capabilities.agent_turn_recall.runtime import (
    agent_turn_recall_receipt_path,
    run_configured_agent_turn_recall,
)
from loopx.capabilities.agent_turn_recall.core import (
    build_agent_turn_recall_preview,
    build_agent_turn_situation,
    run_agent_turn_recall,
)
from loopx.capabilities.reward_memory.application import (
    build_active_reward_memory_record,
)
from loopx.capabilities.reward_memory.candidate_review import (
    build_reward_memory_candidate,
    review_reward_memory_candidate,
)
from loopx.capabilities.context_providers.base import (
    ContextProviderItem,
    ContextProviderRetrieval,
)
from loopx.capabilities.reward_memory.experiment import (
    canonical_reward_memory_actor_peer_id,
    load_reward_memory_experiment_config,
    resolve_reward_memory_surface_config,
)


SURFACE = "agent_workflow.turn_admission"
DEFAULT_GOAL_ID = "goal"
DEFAULT_AGENT_ID = "pilot"
DEFAULT_ACTOR_PEER_ID = canonical_reward_memory_actor_peer_id(
    goal_id=DEFAULT_GOAL_ID,
    agent_id=DEFAULT_AGENT_ID,
)
SCOPE_REF = (
    f"viking://user/example/peers/{DEFAULT_ACTOR_PEER_ID}/memories/"
    f"reward-memory/goals/{DEFAULT_GOAL_ID}/preferences"
)


class RecallProvider:
    provider_id = "openviking"

    def __init__(
        self, content: str | None = None, *, unavailable: bool = False
    ) -> None:
        self.content = content
        self.unavailable = unavailable
        self.calls = 0
        self.queries: list[str] = []

    def retrieve(self, **kwargs: Any) -> ContextProviderRetrieval:
        self.calls += 1
        self.queries.append(str(kwargs["query"]))
        if self.unavailable:
            raise RuntimeError("provider unavailable")
        items = (
            (
                ContextProviderItem(
                    resource_ref=f"{SCOPE_REF}/turn-habit.json",
                    summary="Scoped turn guidance.",
                    content=self.content,
                    score=0.97,
                ),
            )
            if self.content
            else ()
        )
        return ContextProviderRetrieval(
            provider=self.provider_id,
            namespace=str(kwargs["namespace"]),
            status="completed",
            query_summary=str(kwargs["query_summary"]),
            observed_at=str(kwargs["observed_at"]),
            search_performed=True,
            read_performed=bool(items),
            items=items,
            requested_limit=int(kwargs["max_results"]),
        )


def quota_decision(
    *, phase: str = "reviewing", target_key: str = "pr:123"
) -> dict[str, Any]:
    return {
        "ok": True,
        "status_health_ok": True,
        "decision": "run",
        "lifecycle_phase": phase,
        "recommended_action": "Review, refine, validate, and close the selected work.",
        "latest_run_recommended_action": "Inspect the exact diff before changing it.",
        "selected_todo": {
            "todo_id": "todo_123",
            "text": "Review and refine the selected pull request.",
            "action_kind": "review_refine_merge",
            "target_key": target_key,
            "claimed_by": "pilot",
        },
    }


def situation(
    *,
    phase: str = "reviewing",
    target_key: str = "pr:123",
    turn: str = "2026-08-02T10:00:00+00:00",
    agent_id: str = "pilot",
) -> dict[str, Any]:
    return build_agent_turn_situation(
        quota_decision(phase=phase, target_key=target_key),
        goal_id="goal",
        agent_id=agent_id,
        turn_instance_id=turn,
        workspace_ref="workspace:example",
        project_ref="github:example/repo",
        user_ref="user:example",
    )


def raw_config(
    *,
    peer_ref: str = "agent:pilot",
    goal_id: str = DEFAULT_GOAL_ID,
    agent_id: str = DEFAULT_AGENT_ID,
) -> dict[str, Any]:
    actor_peer_id = canonical_reward_memory_actor_peer_id(
        goal_id=goal_id,
        agent_id=agent_id,
    )
    scope_ref = (
        f"viking://user/example/peers/{actor_peer_id}/memories/reward-memory/"
        f"goals/{goal_id}/preferences"
    )
    scope = {
        "workspace_ref": "workspace:example",
        "project_ref": "github:example/repo",
        "user_ref": "user:example",
        "peer_ref": peer_ref,
        "surface_ids": [SURFACE],
    }
    corpus = {
        "corpus_id": "agent_turn_preferences",
        "class_id": "soft_preference",
        "provider_id": "openviking",
        "owner_ref": "example_owner",
        "source_of_truth": "reviewed_scoped_feedback",
        "read_authority": "actor_scoped",
        "write_authority": "provider_managed",
        "scope": scope,
        "freshness": {"mode": "source_truth_bound"},
        "lifecycle": {"state": "active", "supersedes": []},
        "retrieval": {
            "index_required": True,
            "readback_required": True,
            "application_receipt_required": True,
        },
        "maintenance": {
            "writeback_triggers": ["reviewed_scoped_feedback"],
            "closure_policy": "write_exact_readback_then_recall",
            "retirement_authority": "example_owner",
        },
        "privacy": {"visibility": "private", "raw_content_in_registry": False},
        "provider_scope_ref_digest": hashlib.sha256(
            scope_ref.encode("utf-8")
        ).hexdigest()[:16],
    }
    policy = {
        "schema_version": "reward_memory_standing_policy_v0",
        "policy_id": "policy:example:agent-turn",
        "enabled": True,
        "auto_activate": True,
        "owner_ref": "example_owner",
        "reviewer_ref": "agent:example-reviewer",
        "authority_source_ref": "policy:example:agent-turn",
        "scope": scope,
        "allowed_target_classes": ["soft_preference"],
        "allowed_source_kinds": ["explicit_user_instruction"],
        "allowed_actor_roles": ["verified_project_owner_or_operator"],
        "allowed_action_scopes": ["agent_turn:guidance"],
        "raw_content_captured": False,
    }
    return {
        "schema_version": "reward_memory_experiment_config_v1",
        "project_provider_binding": {
            "provider_id": "openviking",
            "namespace": "reward_memory",
            "timeout_seconds": 30,
            "minimum_provider_version": "0.4.18",
            "actor_peer_id": actor_peer_id,
            "corpus_scopes": [
                {"corpus_id": corpus["corpus_id"], "scope_ref": scope_ref}
            ],
        },
        "corpora": [{"corpus": corpus, "standing_policy": policy}],
        "surfaces": [
            {
                "surface_id": SURFACE,
                "adapter": "scoped_feedback",
                "corpus_ids": [corpus["corpus_id"]],
                "ingest_corpus_id": corpus["corpus_id"],
                "recall_profile": {
                    "profile_id": "agent_turn_v1",
                    "mode": "bounded_agentic_search",
                    "max_queries": 1,
                    "limit": 4,
                },
            }
        ],
        "automation": {
            "automatic_recall": True,
            "automatic_ingest": False,
            "fail_open": True,
        },
    }


def normalized_config(
    tmp_path: Path, *, peer_ref: str = "agent:pilot"
) -> dict[str, Any]:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(raw_config(peer_ref=peer_ref)), encoding="utf-8")
    return load_reward_memory_experiment_config(
        project=tmp_path,
        config_path="config.json",
    )


def active_record(config: dict[str, Any], *, expires_at: str) -> str:
    corpus = resolve_reward_memory_surface_config(config, SURFACE)["corpus"]
    return json.dumps(
        {
            "schema_version": "reward_memory_active_record_v0",
            "corpus_id": corpus["corpus_id"],
            "candidate_ref": "candidate:turn-habit",
            "target_class": corpus["class_id"],
            "content_summary": (
                "After merging an origin-task change, report the refinements and "
                "ask that task to update its integration branch from main."
            ),
            "scope": corpus["scope"],
            "lifecycle": {"state": "active", "expires_at": expires_at},
        }
    )


def checkpoints(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    route = resolve_reward_memory_surface_config(config, SURFACE)
    return {
        item["corpus"]["corpus_id"]: {
            "verified": True,
            "corpus_id": item["corpus"]["corpus_id"],
            "workspace_ref": item["corpus"]["scope"]["workspace_ref"],
            "project_ref": item["corpus"]["scope"]["project_ref"],
            "user_ref": item["corpus"]["scope"]["user_ref"],
            "peer_ref": item["corpus"]["scope"]["peer_ref"],
            "surface_id": SURFACE,
            "read_authority": item["corpus"]["read_authority"],
            "source_ref": "registry:goal:reward-memory",
        }
        for item in route["recall_corpora"]
    }


def test_situation_is_prompt_independent_and_fingerprints_material_changes() -> None:
    first = situation(turn="2026-08-02T10:00:00+00:00")
    retry = situation(turn="2026-08-02T10:00:00+00:00")
    next_turn = situation(turn="2026-08-02T10:03:00+00:00")
    phase_change = situation(phase="merged")
    target_change = situation(target_key="pr:124")

    assert first["user_prompt_included"] is False
    assert first["situation_fingerprint"] == retry["situation_fingerprint"]
    assert first["turn_recall_id"] == retry["turn_recall_id"]
    assert first["situation_fingerprint"] == next_turn["situation_fingerprint"]
    assert first["turn_recall_id"] != next_turn["turn_recall_id"]
    assert first["situation_fingerprint"] != phase_change["situation_fingerprint"]
    assert first["situation_fingerprint"] != target_change["situation_fingerprint"]
    preview = build_agent_turn_recall_preview(first)
    assert preview["query_evidence"]["exact_query_exposed"] is False
    assert preview["provider_call_count"] == 0


def test_recall_injects_private_guidance_without_granting_authority(
    tmp_path: Path,
) -> None:
    config = normalized_config(tmp_path)
    provider = RecallProvider(
        active_record(config, expires_at="2026-08-03T00:00:00+00:00")
    )

    result = run_agent_turn_recall(
        config,
        situation(),
        observed_at="2026-08-02T10:00:00+00:00",
        read_authority_checkpoints=checkpoints(config),
        provider=provider,
    )

    assert result["status"] == "applied"
    assert result["provider_call_count"] == 1
    assert len(result["context"]["guidance"]) == 1
    assert "integration branch" in result["context"]["guidance"][0]["content_summary"]
    assert result["grants_new_action_authority"] is False
    assert result["quota_spend_performed"] is False
    assert result["suppress_external_sinks"] is True
    assert "Review and refine" in provider.queries[0]


def test_wrong_peer_blocks_before_provider_call(tmp_path: Path) -> None:
    config = normalized_config(tmp_path, peer_ref="agent:other")
    provider = RecallProvider()

    result = run_agent_turn_recall(
        config,
        situation(),
        observed_at="2026-08-02T10:00:00+00:00",
        read_authority_checkpoints=checkpoints(config),
        provider=provider,
    )

    assert result["status"] == "not_available"
    assert provider.calls == 0
    assert result["recall_attempts"][0]["status"] == "guard_blocked"
    assert "peer_ref_scope_mismatch" in result["recall_attempts"][0]["reason_codes"]


def test_expired_memory_is_not_injected(tmp_path: Path) -> None:
    config = normalized_config(tmp_path)
    provider = RecallProvider(
        active_record(config, expires_at="2026-08-02T09:59:59+00:00")
    )

    result = run_agent_turn_recall(
        config,
        situation(),
        observed_at="2026-08-02T10:00:00+00:00",
        read_authority_checkpoints=checkpoints(config),
        provider=provider,
    )

    assert result["status"] == "not_available"
    assert result["context"]["guidance"] == []
    assert provider.calls == 1


def test_provider_failure_fails_open_without_user_gate(tmp_path: Path) -> None:
    config = normalized_config(tmp_path)
    provider = RecallProvider(unavailable=True)

    result = run_agent_turn_recall(
        config,
        situation(),
        observed_at="2026-08-02T10:00:00+00:00",
        read_authority_checkpoints=checkpoints(config),
        provider=provider,
    )

    assert result["status"] == "provider_unavailable"
    assert result["context"]["guidance"] == []
    assert result["provider_failure_is_user_gate"] is False
    assert result["grants_new_action_authority"] is False


def test_quota_identity_requires_exact_agent_and_turn_receipt() -> None:
    turn = "2026-08-02T10:00:00+00:00"
    decision = quota_decision() | {
        "goal_id": "goal",
        "agent_identity": {"agent_id": "pilot"},
        "heartbeat_receipt": {
            "turn_instance_id": turn,
            "status": "committed",
        },
    }

    _validate_quota_identity(
        decision,
        goal_id="goal",
        agent_id="pilot",
        turn_instance_id=turn,
    )
    with pytest.raises(ValueError, match="agent_id"):
        _validate_quota_identity(
            decision,
            goal_id="goal",
            agent_id="other",
            turn_instance_id=turn,
        )
    with pytest.raises(ValueError, match="turn_instance_id"):
        _validate_quota_identity(
            decision,
            goal_id="goal",
            agent_id="pilot",
            turn_instance_id="2026-08-02T10:01:00+00:00",
        )
    with pytest.raises(ValueError, match="not committed"):
        _validate_quota_identity(
            decision
            | {
                "heartbeat_receipt": {
                    "turn_instance_id": turn,
                    "status": "write_failed",
                }
            },
            goal_id="goal",
            agent_id="pilot",
            turn_instance_id=turn,
        )


def test_configured_session_scope_is_canonical() -> None:
    identity = {"session_ref": "session:configured"}

    assert _resolve_session_ref(identity, None) == "session:configured"
    assert _resolve_session_ref(identity, "session:configured") == "session:configured"
    with pytest.raises(ValueError, match="configured corpus scope"):
        _resolve_session_ref(identity, "session:other")


def test_same_turn_receipt_round_trip_reuses_private_context(tmp_path: Path) -> None:
    receipt_path = tmp_path / "pilot.json"
    receipt = {
        "schema_version": "agent_turn_recall_receipt_v1",
        "goal_id": "goal",
        "agent_id": "pilot",
        "turn_recall_id": "sha256:turn",
        "situation_fingerprint": "sha256:situation",
        "source_status": "applied",
        "context": {
            "schema_version": "agent_turn_recall_context_v0",
            "guidance": [{"content_summary": "Use the scoped workflow."}],
        },
    }

    _write_receipt(receipt_path, receipt)
    loaded = _load_receipt(receipt_path)
    assert loaded == receipt
    deduplicated = _deduplicated_payload(loaded or {}, goal_id="goal", agent_id="pilot")
    assert deduplicated is not None
    assert deduplicated["status"] == "deduplicated"
    assert deduplicated["provider_call_count"] == 0
    assert deduplicated["same_turn_receipt_reused"] is True


def test_recall_receipt_identity_includes_goal_and_agent(tmp_path: Path) -> None:
    first = agent_turn_recall_receipt_path(
        tmp_path,
        goal_id="goal-one",
        agent_id="researcher",
    )
    second = agent_turn_recall_receipt_path(
        tmp_path,
        goal_id="goal-two",
        agent_id="researcher",
    )
    sibling = agent_turn_recall_receipt_path(
        tmp_path,
        goal_id="goal-one",
        agent_id="reviewer",
    )

    assert first != second
    assert first != sibling
    assert "/.local/loopx/agent-turn-recall/goal-one-" in first.as_posix()
    assert first.name.startswith("researcher-")

    normalized_collision = agent_turn_recall_receipt_path(
        tmp_path,
        goal_id="goal+one",
        agent_id="researcher",
    )
    punctuation_variant = agent_turn_recall_receipt_path(
        tmp_path,
        goal_id="goal one",
        agent_id="researcher",
    )
    assert normalized_collision != punctuation_variant


def test_configured_turn_recall_injects_content_and_deduplicates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = normalized_config(tmp_path)
    provider = RecallProvider(
        active_record(config, expires_at="2027-08-03T00:00:00+00:00")
    )
    status = {
        "ok": True,
        "status": "available",
        "available": True,
        "automatic_recall": True,
        "automatic_ingest": False,
    }
    monkeypatch.setattr(
        recall_runtime,
        "resolve_reward_memory_experiment",
        lambda **_kwargs: (status, config),
    )
    monkeypatch.setattr(recall_runtime, "_goal_repo", lambda *_args, **_kwargs: tmp_path)

    first = run_configured_agent_turn_recall(
        registry_path=tmp_path / "registry.json",
        goal_id="goal",
        agent_id="pilot",
        quota_decision=quota_decision(),
        turn_instance_id="turn-1",
        observed_at="2026-08-02T10:00:00+00:00",
        execute=True,
        provider=provider,
    )
    second = run_configured_agent_turn_recall(
        registry_path=tmp_path / "registry.json",
        goal_id="goal",
        agent_id="pilot",
        quota_decision=quota_decision(),
        turn_instance_id="turn-1",
        observed_at="2026-08-02T10:00:00+00:00",
        execute=True,
        provider=provider,
    )

    assert first["status"] == "applied"
    assert first["context"]["guidance"][0]["content_summary"]
    assert first["provider_call_count"] == 1
    assert second["status"] == "deduplicated"
    assert second["context"] == first["context"]
    assert second["provider_call_count"] == 0
    assert provider.calls == 1


def test_checkpoint_transport_failure_preserves_turn_and_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = normalized_config(tmp_path)
    status = {"ok": True, "status": "available", "available": True}
    monkeypatch.setattr(recall_runtime, "resolve_reward_memory_experiment", lambda **_kwargs: (status, config))
    monkeypatch.setattr(recall_runtime, "_goal_repo", lambda *_args, **_kwargs: tmp_path)
    provider = RecallProvider()
    original_transport = read_authority.effect_runtime_result

    def unavailable(operation: str, _params: Any) -> Any:
        assert operation == "reward_memory.read_authority.surface_checkpoints"
        raise RuntimeError("private runtime startup detail")

    monkeypatch.setattr(read_authority, "effect_runtime_result", unavailable)
    kwargs = dict(registry_path=tmp_path / "registry.json", goal_id="goal", agent_id="pilot",
                  quota_decision=quota_decision(), turn_instance_id="turn-recovery", execute=True, provider=provider)
    result = recall_runtime.run_configured_agent_turn_recall_fail_open(**kwargs)
    assert result["ok"] and result["fail_open"]
    assert result["status"] == "runtime_unavailable" and result["context"] is None
    assert result["provider_call_count"] == provider.calls == 0
    assert not result["provider_failure_is_user_gate"] and not result["quota_spend_performed"]
    receipt = agent_turn_recall_receipt_path(tmp_path, goal_id="goal", agent_id="pilot")
    assert not receipt.exists()
    assert "private runtime" not in json.dumps(result)
    monkeypatch.setattr(read_authority, "effect_runtime_result", original_transport)
    recovered = recall_runtime.run_configured_agent_turn_recall_fail_open(**kwargs)
    assert recovered["status"] == "not_available"
    assert recovered["provider_call_count"] == provider.calls == 1
    assert recovered["same_turn_receipt_written"] and receipt.exists()


def test_explicit_cli_checkpoint_transport_failure_returns_safe_packet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = normalized_config(tmp_path)
    monkeypatch.setattr(recall_cli, "resolve_reward_memory_experiment", lambda **_kwargs: ({"status": "available"}, config))
    monkeypatch.setattr(recall_cli, "_goal_repo", lambda *_args, **_kwargs: tmp_path)
    turn = "turn-cli-failure"
    decision = quota_decision() | {"goal_id": "goal", "agent_identity": {"agent_id": "pilot"},
        "heartbeat_receipt": {"turn_instance_id": turn, "status": "committed"}}
    monkeypatch.setattr(recall_cli, "load_turn_quota_decision", lambda *_args: decision)

    def unavailable(operation: str, _params: Any) -> Any:
        assert operation == "reward_memory.read_authority.surface_checkpoints"
        raise RuntimeError("private runtime startup detail")

    monkeypatch.setattr(read_authority, "effect_runtime_result", unavailable)
    monkeypatch.setattr(recall_cli, "run_agent_turn_recall", lambda *_args, **_kwargs: pytest.fail("provider boundary reached"))
    payloads: list[dict[str, Any]] = []
    result = recall_cli.handle_agent_turn_recall_command(
        argparse.Namespace(command="agent-turn-recall", goal_id="goal", agent_id="pilot", turn_instance_id=turn,
                           quota_decision_json="unused", session_ref=None, force_refresh=False, execute=True),
        registry_path=tmp_path / "registry.json", output_format=lambda *_args: "json",
        print_payload=lambda payload, *_args: payloads.append(payload),
    )
    assert result == 2 and len(payloads) == 1
    payload = payloads[0]
    assert not payload["ok"] and payload["status"] == "runtime_unavailable"
    assert payload["reason_code"] == "automatic_recall_runtime_failed"
    assert payload["provider_call_count"] == 0 and not payload["quota_spend_performed"]
    assert "private runtime" not in json.dumps(payload)
    assert not agent_turn_recall_receipt_path(tmp_path, goal_id="goal", agent_id="pilot").exists()


def test_expiry_survives_candidate_review_and_activation(tmp_path: Path) -> None:
    config = normalized_config(tmp_path)
    corpus = resolve_reward_memory_surface_config(config, SURFACE)["corpus"]
    expires_at = "2026-08-03T00:00:00+00:00"
    candidate = build_reward_memory_candidate(
        {
            "target_class": "soft_preference",
            "content_summary": "Use the scoped workflow for this temporary task.",
            "expires_at": expires_at,
            "source": {
                "source_kind": "explicit_user_instruction",
                "source_ref": "user:instruction",
                "actor_ref": "user:example",
                "actor_role": "verified_project_owner_or_operator",
            },
            "scope": corpus["scope"],
            "reasoning": {
                "summary": "Explicit temporary preference.",
                "confidence": "high",
            },
            "guard_context": {
                "source_freshness": "current",
                "conflict_state": "clear",
                "current_artifact_verified": True,
            },
            "requested_action_scopes": [],
            "raw_content_captured": False,
        }
    )
    reviewed = review_reward_memory_candidate(
        candidate,
        {
            "decision": "accept",
            "reviewer_ref": "agent:reviewer",
            "review_ref": "review:temporary-preference",
            "reasoning_summary": "The preference is explicit and bounded.",
        },
    )
    active = build_active_reward_memory_record(
        reviewed,
        corpus,
        activated_at="2026-08-02T10:00:00+00:00",
    )

    assert active["lifecycle"] == {"state": "active", "expires_at": expires_at}
