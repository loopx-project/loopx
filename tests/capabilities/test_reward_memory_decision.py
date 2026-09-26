"""Production TS transport + original configured recall/application boundary."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from loopx.capabilities.context_providers.base import ContextProviderItem, ContextProviderRetrieval
from loopx.capabilities.reward_memory import assess_reward_memory_decision, run_reward_memory_decision
from loopx.capabilities.reward_memory import decision
from loopx.capabilities.reward_memory.experiment import load_reward_memory_experiment_config
from loopx.capabilities.reward_memory.runtime_hooks import run_reward_memory_automatic_recall_hook

SURFACE = "reviewer_artifact.summary"
REVISION = "revision:abc123"
FIXTURE = Path(__file__).resolve().parents[2] / "examples/fixtures/reward-memory-scoped-feedback-ingest.public.json"


class Provider:
    provider_id = "openviking"

    def __init__(self, records: dict[str, Any] | None = None, *, unavailable: bool = False):
        self.records = records or {}
        self.unavailable = unavailable
        self.calls = 0

    def retrieve(self, **kwargs):
        self.calls += 1
        if self.unavailable:
            raise RuntimeError("provider unavailable")
        scope = kwargs["scope_ref"]
        record = self.records.get(scope)
        return ContextProviderRetrieval(
            provider=self.provider_id, namespace=kwargs["namespace"], status="completed",
            query_summary=kwargs["query_summary"], observed_at=kwargs["observed_at"],
            search_performed=True, read_performed=True, requested_limit=kwargs["max_results"],
            items=(ContextProviderItem(resource_ref=f"{scope}/memory.json", summary="reviewed lesson",
                                       content=json.dumps(record), score=0.9),) if record else (),
        )


def context(tmp_path: Path, *, two_corpora: bool = False):
    fixture = json.loads(FIXTURE.read_text())
    entries, scopes, records, checkpoints = [], [], {}, {}
    for name in (["primary", "overlay"] if two_corpora else ["primary"]):
        corpus, policy = copy.deepcopy(fixture["corpus"]), copy.deepcopy(fixture["standing_policy"])
        scope = f"viking://resources/reward-memory/{name}"
        corpus["corpus_id"] = name
        corpus["provider_scope_ref_digest"] = hashlib.sha256(scope.encode()).hexdigest()[:16]
        policy["policy_id"] = f"policy:example:{name}"
        entries.append({"corpus": corpus, "standing_policy": policy})
        scopes.append({"corpus_id": name, "scope_ref": scope})
        checkpoints[name] = {"verified": True, "corpus_id": name, **{
            key: corpus["scope"][key] for key in ("workspace_ref", "project_ref")},
            "surface_id": SURFACE, "read_authority": corpus["read_authority"],
            "source_ref": policy["authority_source_ref"]}
        records[scope] = {"schema_version": "reward_memory_active_record_v0", "corpus_id": name,
            "candidate_ref": f"candidate:{name}", "target_class": "hard_policy",
            "content_summary": "Private reviewed summary language lesson.",
            "scope": {**corpus["scope"], "revision_ref": REVISION}, "lifecycle": {"state": "active"}}
    raw = {"schema_version": "reward_memory_experiment_config_v1", "corpora": entries,
        "project_provider_binding": {"provider_id": "openviking", "namespace": "reward_memory",
            "timeout_seconds": 30, "minimum_provider_version": "0.4.9", "corpus_scopes": scopes},
        "surfaces": [{"surface_id": SURFACE, "adapter": "scoped_feedback",
            "corpus_ids": [item["corpus"]["corpus_id"] for item in entries], "ingest_corpus_id": "primary",
            "recall_profile": {"profile_id": "summary", "mode": "function_boundary", "max_queries": 1, "limit": 4}}],
        "automation": {"automatic_recall": True, "automatic_ingest": False, "fail_open": True}}
    path = tmp_path / "config.json"
    path.write_text(json.dumps(raw))
    config = load_reward_memory_experiment_config(project=tmp_path, config_path=path.name)
    arguments = {"surface_id": SURFACE, "base_output": {"summary": "baseline"},
        "workspace_ref": "workspace:example", "project_ref": "repository:example", "revision_ref": REVISION,
        "queries": [{"query": "Which reviewed language lesson applies?", "query_summary": "summary language"}],
        "observed_at": "2026-07-17T03:00:00+08:00", "freshness_context": {
            "source_truth_current": True, "source_revision": REVISION}, "conflict_state": "clear",
        "read_authority_checkpoints": checkpoints, "application_id": "decision:summary",
        "artifact_ref": "artifact:current:abc123"}
    return config, arguments, records


def delivery(base, items):
    return {"outcome": "applied", "output": {**base, "private_context": [item.content_summary for item in items]},
        "memory_refs": [item.memory_ref for item in items], "current_artifact_verified": True,
        "reasoning_summary": "Delivered qualified context; semantic assessment is separate."}


def test_disabled_adds_no_packet_transport_or_provider(tmp_path, monkeypatch):
    config, arguments, records = context(tmp_path)
    config["automation"]["automatic_recall"] = False
    before = copy.deepcopy(config)
    provider = Provider(records)
    monkeypatch.setattr(decision, "effect_runtime_result", lambda *args: pytest.fail("disabled TS call"))
    for disabled in (None, {}, config):
        assert run_reward_memory_decision(disabled, query_ready=True, provider=provider, **arguments) is None
    assert config == before
    assert provider.calls == 0


@pytest.mark.parametrize("extra,reason", [
    ({"mode": "preview"}, None), ({"query_ready": False}, "query_not_ready"),
    ({}, "application_strategy_required"),
    ({"application_kind": "context_delivery"}, "application_strategy_required"),
    ({"apply_memory": delivery}, "application_strategy_required"),
    ({"application_kind": "context_delivery", "apply_memory": delivery, "artifact_ref": None}, "current_artifact_binding_required"),
])
def test_pre_provider_admission(tmp_path, extra, reason):
    config, arguments, records = context(tmp_path)
    provider = Provider(records)
    result = run_reward_memory_decision(config, **{**arguments, "query_ready": True, "provider": provider, **extra})
    assert result.public_packet["reason_code"] == reason
    assert result.public_packet["status"] == ("preview" if extra.get("mode") == "preview" else "incomplete")
    assert result.output == arguments["base_output"]
    assert not result.public_packet["decision_consumption_complete"]
    assert provider.calls == 0


@pytest.mark.parametrize("case,expected,calls,filtered", [
    ("empty", "empty", 1, 0), ("filtered", "empty", 1, 1),
    ("unavailable", "provider_unavailable", 1, 0), ("wrong_scope", "incomplete", 0, 0),
])
def test_failure_keeps_base_research_and_truthful_counters(tmp_path, case, expected, calls, filtered):
    config, arguments, records = context(tmp_path)
    if case == "filtered":
        next(iter(records.values()))["corpus_id"] = "unrelated"
    if case == "wrong_scope":
        arguments["workspace_ref"] = "workspace:unrelated"
    provider = Provider({} if case == "empty" else records, unavailable=case == "unavailable")
    result = run_reward_memory_decision(config, query_ready=True, application_kind="context_delivery",
                                       apply_memory=delivery, provider=provider, **arguments)
    assert result.public_packet["status"] == expected
    assert result.public_packet["provider_call_count"] == provider.calls == calls
    assert result.public_packet["filtered_count"] == filtered
    assert result.output == arguments["base_output"]
    assert result.public_packet["research_may_continue"]
    assert not result.public_packet["decision_consumption_complete"]


def test_recall_only_and_old_optional_callback_sdk_remain_legitimate(tmp_path):
    config, arguments, records = context(tmp_path)
    provider = Provider(records)
    result = run_reward_memory_decision(config, query_ready=True, mode="recall_only", provider=provider, **arguments)
    assert result.public_packet["status"] == "recalled"
    assert result.public_packet["semantic_disposition"] is None
    assert not result.public_packet["decision_consumption_complete"]
    assert result.output == arguments["base_output"]
    original = run_reward_memory_automatic_recall_hook(config, provider=provider, **arguments)
    assert original["status"] == "available_not_applied"
    assert original["application"]["receipt"]["reasoning_summary"] == "model_application_callback_not_supplied"


@pytest.mark.parametrize("disposition", ["applied", "applied_unchanged", "ignored", "refuted"])
def test_delivery_then_actual_bound_assessment_and_exact_replay(tmp_path, disposition):
    outcome = "applied" if disposition == "applied_unchanged" else disposition
    config, arguments, records = context(tmp_path, two_corpora=True)
    next(iter(records.values()))["corpus_id"] = "unrelated"
    provider = Provider(records)
    delivered = run_reward_memory_decision(config, query_ready=True, application_kind="context_delivery",
                                          apply_memory=delivery, provider=provider, **arguments)
    assert delivered.public_packet["status"] == "context_delivered"
    assert delivered.output["private_context"] == ["Private reviewed summary language lesson."]
    assert delivered.public_packet["semantic_disposition"] is None
    assert not delivered.public_packet["decision_consumption_complete"]
    assert delivered.public_packet["provider_call_count"] == 2
    assert delivered.public_packet["filtered_count"] == 1
    assessments = []

    def judge(base, items):
        assessments.append(items[0].content_summary)
        return {"outcome": outcome, "output": {"summary": "reviewed"} if disposition == "applied" else base,
            "memory_refs": [item.memory_ref for item in items], "current_artifact_verified": True,
            "reasoning_summary": "Compared returned lesson with the current artifact, not injection."}

    assessed = assess_reward_memory_decision(delivered, apply_memory=judge)
    assert assessed.public_packet["decision_consumption_complete"]
    assert assessed.public_packet["semantic_disposition"] == outcome
    assert assessed.public_packet["provider_call_count"] == provider.calls == 2
    assert assessed.public_packet["filtered_count"] == 1
    assert len(assessments) == 1
    assert assess_reward_memory_decision(assessed, apply_memory=judge) is assessed
    assert run_reward_memory_decision(config, query_ready=True, application_kind="context_delivery",
        apply_memory=delivery, previous_result=assessed, provider=provider, **arguments) is assessed
    changed = run_reward_memory_decision(config, query_ready=True, application_kind="context_delivery",
        apply_memory=delivery, previous_result=assessed, provider=provider,
        **{**arguments, "artifact_ref": "artifact:new"})
    assert changed.public_packet["reason_code"] == "replay_request_mismatch"
    assert provider.calls == 2
    packet = json.dumps(assessed.public_packet)
    for private in ("Private reviewed", "Which reviewed", "viking://", "reasoning_summary", "has_applier"):
        assert private not in packet
    for flag in ("utility_verified", "grants_new_action_authority", "external_writes_performed", "raw_content_captured"):
        assert assessed.public_packet[flag] is False


@pytest.mark.parametrize("bad", ["foreign_ref", "unverified_artifact", "unattributed", "throws"])
def test_invalid_semantic_evidence_is_not_adoption(tmp_path, bad):
    config, arguments, records = context(tmp_path)
    provider = Provider(records)

    def invalid(base, items):
        if bad == "throws":
            base["summary"] = "mutated"
            raise RuntimeError("model failure")
        return {"outcome": "ignored", "output": base,
            "memory_refs": ["foreign"] if bad == "foreign_ref" else [] if bad == "unattributed" else [items[0].memory_ref],
            "current_artifact_verified": bad != "unverified_artifact", "reasoning_summary": "Not verified."}

    result = run_reward_memory_decision(config, query_ready=True, application_kind="semantic_application",
                                       apply_memory=invalid, provider=provider, **arguments)
    assert result.public_packet["status"] == "incomplete"
    assert not result.public_packet["decision_consumption_complete"]
    assert result.output == arguments["base_output"] == {"summary": "baseline"}
    assert result.public_packet["provider_call_count"] == 1


def test_ts_projection_failure_after_provider_retains_call_evidence(tmp_path, monkeypatch):
    config, arguments, records = context(tmp_path)
    provider = Provider(records)
    transport = decision.effect_runtime_result

    def fail_projection(method, params):
        if method == "reward_memory.decision.project":
            assert "Which reviewed" not in json.dumps(params)
            raise RuntimeError("TS unavailable")
        return transport(method, params)

    monkeypatch.setattr(decision, "effect_runtime_result", fail_projection)
    result = run_reward_memory_decision(config, query_ready=True, application_kind="context_delivery",
                                       apply_memory=delivery, provider=provider, **arguments)
    assert result.public_packet["status"] == "incomplete"
    assert result.public_packet["provider_call_count"] == provider.calls == 1
    assert result.application_receipt["outcome"] == "applied"
    assert result.output == arguments["base_output"]
    assert run_reward_memory_decision(config, query_ready=True, application_kind="context_delivery",
        apply_memory=delivery, previous_result=result, provider=provider, **arguments) is result
    assert provider.calls == 1


def test_semantic_receipt_survives_ts_failure_without_claiming_completion(tmp_path, monkeypatch):
    config, arguments, records = context(tmp_path)
    provider = Provider(records)
    delivered = run_reward_memory_decision(config, query_ready=True, application_kind="context_delivery",
                                          apply_memory=delivery, provider=provider, **arguments)
    def reject_projection(*args):
        raise RuntimeError("TS unavailable")
    monkeypatch.setattr(decision, "effect_runtime_result", reject_projection)
    result = assess_reward_memory_decision(delivered, apply_memory=lambda base, items: {
        "outcome": "ignored", "output": base, "memory_refs": [items[0].memory_ref],
        "current_artifact_verified": True, "reasoning_summary": "Current evidence does not need this lesson."})
    assert result.public_packet["status"] == "incomplete"
    assert not result.public_packet["decision_consumption_complete"]
    assert result.application_receipt["outcome"] == "ignored"
    assert result.public_packet["provider_call_count"] == provider.calls == 1
    assert result.output == arguments["base_output"]
