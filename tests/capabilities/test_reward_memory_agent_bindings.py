"""Binding isolation at the shipped configuration and runtime boundaries."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from loopx.capabilities.reward_memory.configuration import (
    reward_memory_goal_configuration_summary,
)
from loopx.capabilities.reward_memory.experiment import resolve_reward_memory_experiment
from loopx.configure_goal import configure_goal
from loopx.control_plane.reward_memory import reward_memory_goal_policy


def fixture_registry(tmp_path: Path) -> Path:
    fixture = json.loads((Path(__file__).resolve().parents[2] / "examples/fixtures/reward-memory-ingest-event.public.json").read_text())
    project = tmp_path / "project"
    config_path = project / ".loopx/config/reward-memory/shared.json"
    config_path.parent.mkdir(parents=True)
    corpus_id = fixture["corpus"]["corpus_id"]
    surface_id = fixture["standing_policy"]["scope"]["surface_ids"][0]
    provider = {k: v for k, v in fixture["provider_binding"].items() if k not in {"corpus_id", "scope_ref"}}
    provider["corpus_scopes"] = [{"corpus_id": corpus_id, "scope_ref": fixture["provider_binding"]["scope_ref"]}]
    config_path.write_text(json.dumps({
        "schema_version": "reward_memory_experiment_config_v1",
        "project_provider_binding": provider,
        "corpora": [{"corpus": fixture["corpus"], "standing_policy": fixture["standing_policy"]}],
        "surfaces": [{"surface_id": surface_id, "adapter": fixture["adapter"],
            "corpus_ids": [corpus_id], "ingest_corpus_id": corpus_id,
            "recall_profile": {"profile_id": "fixture", "mode": "function_boundary", "max_queries": 1, "limit": 5}}],
        "automation": {"automatic_recall": True, "automatic_ingest": True, "fail_open": True},
    }))
    digest = "sha256:" + hashlib.sha256(config_path.read_bytes()).hexdigest()
    receipt = {"schema_version": "reward_memory_enablement_receipt_v0", "status": "verified",
        "goal_id": "binding-goal", "agent_id": "reviewer", "config_digest": digest,
        "provider_id": "openviking", "isolation_mode": "explicit_shared", "actor_binding_verified": False,
        "writability_verified": True, "exact_readback_verified": True}
    goal = {"id": "binding-goal", "repo": str(project),
        "coordination": {"registered_agents": ["reviewer", "writer"]},
        "control_plane": {"reward_memory": {"enabled": True, "experimental": True,
            "config_path": ".loopx/config/reward-memory/shared.json", "config_digest": digest,
            "enabled_agents": ["reviewer"], "enablement_receipts": {"reviewer": receipt},
            "automation": {"automatic_recall": True, "automatic_ingest": True, "fail_open": True}}}}
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"schema_version": 1, "goals": [goal]}))
    return registry


def read_goal(registry: Path) -> dict:
    return json.loads(registry.read_text())["goals"][0]


def test_legacy_shared_binding_is_exact_and_other_agents_are_off(tmp_path):
    registry = fixture_registry(tmp_path)
    policy = copy.deepcopy(read_goal(registry)["control_plane"]["reward_memory"])
    status, config = resolve_reward_memory_experiment(registry_path=registry, goal_id="binding-goal", agent_id="reviewer")
    assert status["available"] is True and config is not None
    status, config = resolve_reward_memory_experiment(registry_path=registry, goal_id="binding-goal", agent_id="writer")
    assert status["status"] == "agent_not_enabled" and config is None
    assert read_goal(registry)["control_plane"]["reward_memory"] == policy


def test_legacy_feature_off_skips_binding_and_provider_resolution(tmp_path, monkeypatch):
    registry = fixture_registry(tmp_path)
    goal = read_goal(registry)
    goal["control_plane"]["reward_memory"]["enabled"] = False
    from loopx.capabilities.reward_memory import configuration
    monkeypatch.setattr(configuration, "resolve_goal_reward_memory_experiment", lambda **_: pytest.fail("off path resolved provider binding"))
    assert reward_memory_goal_policy(goal)["enabled"] is False
    assert configuration.reward_memory_automatic_ingest_for_agent(goal, "reviewer") is False


@pytest.fixture
def independent(tmp_path, monkeypatch):
    # Reuse the existing normalized private-corpus fixtures and IO test double;
    # production preflight, configuration, digest and isolation rules stay real.
    from test_reward_memory_experiment import _EnablementProvider, _private_v1_config
    registry = fixture_registry(tmp_path)
    config = Path(read_goal(registry)["repo"]) / ".loopx/config/reward-memory/writer.json"
    config.write_text(json.dumps(_private_v1_config(goal_id="binding-goal", agent_id="writer")))
    provider = _EnablementProvider()
    monkeypatch.setattr("loopx.capabilities.reward_memory.experiment.build_context_provider", lambda _: provider)
    return registry, config, provider


def enable_writer(registry, *, execute=True):
    return configure_goal(registry_path=registry, goal_id="binding-goal",
        reward_memory_agent_id="writer", reward_memory_config=".loopx/config/reward-memory/writer.json", execute=execute)


def status_for(registry, agent):
    return resolve_reward_memory_experiment(registry_path=registry, goal_id="binding-goal", agent_id=agent)[0]


def test_independent_private_enable_preview_and_disable_preserve_shared_route(independent):
    registry, config, provider = independent
    before = registry.read_bytes()
    shared = read_goal(registry)["control_plane"]["reward_memory"]
    preview = enable_writer(registry, execute=False)
    assert preview["written"] is False and provider.write_calls == 0
    assert registry.read_bytes() == before
    assert enable_writer(registry)["written"] is True
    policy = read_goal(registry)["control_plane"]["reward_memory"]
    assert {k: v for k, v in policy.items() if k != "agent_bindings"} == shared
    assert policy["agent_bindings"]["writer"]["enabled_agents"] == ["writer"]
    assert status_for(registry, "writer")["available"] is True
    assert status_for(registry, "reviewer")["available"] is True
    calls = provider.write_calls
    summary = reward_memory_goal_configuration_summary(read_goal(registry))
    assert summary["enablement_verified_agents"] == ["reviewer", "writer"]
    assert summary["automatic_ingest"] is True
    projected = json.dumps(summary)
    assert str(config) not in projected and "writer.json" not in projected and "config_digest" not in projected
    configure_goal(registry_path=registry, goal_id="binding-goal", reward_memory_agent_id="writer", clear_reward_memory_config=True, execute=True)
    assert provider.write_calls == calls
    assert status_for(registry, "writer")["status"] == "disabled"
    assert status_for(registry, "reviewer")["available"] is True
    assert {k: v for k, v in read_goal(registry)["control_plane"]["reward_memory"].items() if k != "agent_bindings"} == shared


def test_override_drift_or_invalid_receipt_never_uses_shared_fallback(independent):
    registry, config, _ = independent
    enable_writer(registry)
    original = config.read_bytes()
    config.write_bytes(original + b"\n")
    stale = status_for(registry, "writer")
    assert stale["status"] == "enablement_stale"
    assert "--reward-memory-agent-id writer" in stale["repair"]["preview_command"]
    assert "--reward-memory-agent reviewer" not in stale["repair"]["preview_command"]
    assert status_for(registry, "reviewer")["available"] is True
    config.write_bytes(original)
    assert status_for(registry, "writer")["available"] is True
    data = json.loads(registry.read_text())
    data["goals"][0]["control_plane"]["reward_memory"]["agent_bindings"]["writer"]["enablement_receipts"]["writer"]["agent_id"] = "reviewer"
    registry.write_text(json.dumps(data))
    assert status_for(registry, "writer")["status"] == "enablement_unverified"
    assert status_for(registry, "reviewer")["available"] is True


@pytest.mark.parametrize("entry", [None, False, "invalid", {"enabled": False, "experimental": True}])
def test_explicit_bad_or_disabled_override_does_not_inherit(tmp_path, entry):
    registry = fixture_registry(tmp_path)
    data = json.loads(registry.read_text())
    data["goals"][0]["control_plane"]["reward_memory"]["agent_bindings"] = {"reviewer": entry}
    registry.write_text(json.dumps(data))
    assert status_for(registry, "reviewer")["status"] == "disabled"


def test_scoped_enable_works_when_default_off_and_legacy_edits_retain_override(independent):
    registry, _, _ = independent
    data = json.loads(registry.read_text())
    data["goals"][0]["control_plane"]["reward_memory"]["enabled"] = False
    registry.write_text(json.dumps(data))
    enable_writer(registry)
    before = copy.deepcopy(read_goal(registry)["control_plane"]["reward_memory"]["agent_bindings"])
    from loopx.capabilities.reward_memory.configuration import reward_memory_automatic_ingest_for_agent
    assert reward_memory_automatic_ingest_for_agent(read_goal(registry), "writer") is True
    assert reward_memory_automatic_ingest_for_agent(read_goal(registry), "reviewer") is False
    assert reward_memory_goal_configuration_summary(read_goal(registry))["enabled"] is True
    configure_goal(registry_path=registry, goal_id="binding-goal", reward_memory_agents=["reviewer"], execute=True)
    assert read_goal(registry)["control_plane"]["reward_memory"]["agent_bindings"] == before
    assert status_for(registry, "writer")["available"] is True
    configure_goal(registry_path=registry, goal_id="binding-goal", clear_reward_memory_config=True, execute=True)
    assert "reward_memory" not in read_goal(registry)["control_plane"]


def test_wrong_target_mixed_scope_and_failed_preflight_cannot_commit(independent, monkeypatch):
    registry, _, provider = independent
    before = registry.read_bytes()
    for options in [
        {"reward_memory_agent_id": "stranger", "clear_reward_memory_config": True},
        {"reward_memory_agent_id": "writer", "reward_memory_agents": ["reviewer"]},
        {"reward_memory_agent_id": "reviewer", "reward_memory_config": ".loopx/config/reward-memory/writer.json"},
    ]:
        with pytest.raises(ValueError):
            configure_goal(registry_path=registry, goal_id="binding-goal", execute=True, **options)
        assert registry.read_bytes() == before
    assert provider.write_calls == 0
    from loopx.capabilities.reward_memory import configuration
    monkeypatch.setattr(configuration, "preflight_reward_memory_experiment_config", lambda *a, **k: {"ok": False, "agent_receipts": {}})
    with pytest.raises(ValueError, match="verified provider write"):
        enable_writer(registry)
    assert registry.read_bytes() == before


def test_editor_api_preserves_target_and_rejects_ambiguous_off_requests():
    from loopx.chat_goal_configuration_api import _goal_capability_options
    assert _goal_capability_options("reward_memory", {"agent_id": "writer", "enabled": False}) == {
        "reward_memory_agent_id": "writer", "clear_reward_memory_config": True}
    enabled = _goal_capability_options("reward_memory", {"agent_id": "writer", "enabled": True, "config_path": ".loopx/config/writer.json"})
    assert enabled["reward_memory_agent_id"] == "writer" and enabled["reward_memory_agents"] is None
    for value in [{"enabled": False}, {"agent_id": 1}, {"agent_id": "writer", "enabled": "false"},
        {"agent_id": "writer", "enabled": False, "enabled_agents": ["reviewer"]}]:
        with pytest.raises((TypeError, ValueError)):
            _goal_capability_options("reward_memory", value)


def test_rebinding_same_turn_cannot_replay_the_previous_routes_context(independent):
    registry, config_path, _ = independent
    from tests.capabilities.test_agent_turn_recall import raw_config, quota_decision
    from loopx.capabilities.context_providers.base import ContextProviderRetrieval
    from loopx.capabilities.agent_turn_recall.runtime import run_configured_agent_turn_recall
    config_path.write_text(json.dumps(raw_config(goal_id="binding-goal", agent_id="writer", peer_ref="agent:writer")))
    enable_writer(registry)

    class EmptyProvider:
        calls = 0
        def retrieve(self, **kwargs):
            self.calls += 1
            return ContextProviderRetrieval(provider="openviking", namespace=kwargs["namespace"], status="empty",
                query_summary=kwargs["query_summary"], observed_at=kwargs["observed_at"], search_performed=True,
                read_performed=False, items=(), requested_limit=kwargs["max_results"])

    provider = EmptyProvider()
    kwargs = dict(registry_path=registry, goal_id="binding-goal", agent_id="writer", turn_instance_id="binding-test-turn",
        quota_decision=quota_decision(), execute=True, provider=provider)
    run_configured_agent_turn_recall(**kwargs)
    assert provider.calls == 1
    run_configured_agent_turn_recall(**kwargs)
    assert provider.calls == 1
    changed = json.loads(config_path.read_text())
    changed["surfaces"][0]["recall_profile"]["limit"] = 3
    config_path.write_text(json.dumps(changed))
    enable_writer(registry)
    after = run_configured_agent_turn_recall(**kwargs)
    assert after["status"] != "deduplicated"
    assert provider.calls == 2


def test_pending_settlement_cannot_follow_a_replacement_binding(independent):
    registry, config_path, _ = independent
    from tests.capabilities.test_agent_turn_recall import raw_config
    from tests.capabilities.test_reward_memory_outcome_lifecycle import AmbiguousOnceProvider, _reflection, _settlement_evidence
    from loopx.capabilities.reward_memory.outcome_lifecycle import run_configured_turn_outcome_ingest, reconcile_pending_turn_outcome_ingests
    config = raw_config(goal_id="binding-goal", agent_id="writer", peer_ref="agent:writer")
    config["automation"]["automatic_ingest"] = True
    config["corpora"][0]["standing_policy"].update(allowed_source_kinds=["research_review"], allowed_actor_roles=["validated_goal_agent"])
    config["corpora"][0]["corpus"]["maintenance"]["writeback_triggers"] = ["research_review"]
    config_path.write_text(json.dumps(config))
    enable_writer(registry)
    provider = AmbiguousOnceProvider()
    reflection = _reflection()
    request = dict(registry_path=registry, goal_id="binding-goal", agent_id="writer", turn_key="binding-settlement",
        host_result={"reward_memory_reflection_json": reflection}, settlement_evidence=_settlement_evidence(reflection), provider=provider)
    first = run_configured_turn_outcome_ingest(**request)
    assert first["reconciliation_state"] == "pending" and provider.sync_calls == 1
    config["surfaces"][0]["recall_profile"]["limit"] = 3
    config_path.write_text(json.dumps(config))
    enable_writer(registry)
    rejected = run_configured_turn_outcome_ingest(**request)
    assert rejected["status"] == "runtime_state_invalid" and provider.sync_calls == 1
    reconciled = reconcile_pending_turn_outcome_ingests(registry_path=registry, goal_id="binding-goal", agent_id="writer", provider=provider)
    assert reconciled["provider_sync_count"] == 0 and reconciled["completed_count"] == 0
    assert provider.sync_calls == 1


def test_editor_readback_accepts_verified_activation_and_keeps_partial_write_fact(independent):
    from loopx.chat_goal_configuration_api import GoalConfigurationRequestMixin
    registry, _, _ = independent
    preview = enable_writer(registry, execute=False)
    desired = preview["feature_summary"]["reward_memory"]
    applied = enable_writer(registry)
    actual = reward_memory_goal_configuration_summary(read_goal(registry))
    assert desired["enablement_verified_agents"] != actual["enablement_verified_agents"]
    assert GoalConfigurationRequestMixin._goal_configuration_readback_matches("reward_memory", actual, desired, applied)
    stale = {**actual, "binding_revision": "sha256:changed"}
    assert not GoalConfigurationRequestMixin._goal_configuration_readback_matches("reward_memory", stale, desired, applied)

    class Handler(GoalConfigurationRequestMixin):
        def _read_public_goal_configuration(self, _goal):
            return {"revision": "readback", "capability_catalog": {"capabilities": [{"capability_id": "reward_memory", "current": stale}]}}
        def _send_json(self, payload, *, status=200):
            self.response, self.http_status = payload, status
    handler = Handler()
    handler._send_applied_goal_configuration(applied=applied, capability_id="reward_memory", desired_configuration=desired,
        desired_public={"revision": "preview", "capability_catalog": {}}, goal_id="binding-goal", plan={"plan_revision": "one", "writes_required": True})
    assert handler.http_status == 207 and handler.response["status"] == "partial_write"
    assert handler.response["source_written"] is True and handler.response["readback_verified"] is False


def test_app_stage_resolves_the_registered_file_when_projection_omits_private_path(tmp_path, capsys):
    import sys
    from loopx.cli import main
    from loopx.capabilities.reward_memory.codex_app_outcome import stage_codex_app_turn_outcome_candidate
    from tests.capabilities.test_reward_memory_codex_app_outcome import _reflection
    registry = fixture_registry(tmp_path)
    data = json.loads(registry.read_text())
    goal = data["goals"][0]
    goal["state_file"] = "STATE.md"
    runtime = tmp_path / "runtime"
    data["common_runtime_root"] = str(runtime)
    registry.write_text(json.dumps(data))
    state = Path(goal["repo"]) / "STATE.md"
    state.write_text("# Active Goal State\n\n## Agent Todo\n")
    validator = [sys.executable, "-c", "import sys,json;r=json.load(sys.stdin);print(json.dumps({'schema_version':'reward_memory_reflection_validation_v0','status':'validated','reflection_digest':r['reflection_digest'],'evidence_refs':r['reflection']['evidence_refs']}))"]
    assert main(["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json", "todo", "add",
        "--goal-id", "binding-goal", "--role", "agent", "--text", "Verify the registered source", "--claimed-by", "writer",
        "--validation-command-json", json.dumps(validator)]) == 0
    added = json.loads(capsys.readouterr().out)
    assert added["ok"]
    staged = stage_codex_app_turn_outcome_candidate(registry_path=registry, runtime_root=runtime, goal_id="binding-goal",
        agent_id="writer", todo_id=added["todo_id"], turn_instance_id="binding-source-test", effect_id="effect:binding-source",
        state_file=Path("."), validation_workspace=Path(goal["repo"]), reflection_json=_reflection(), observed_at="2026-10-16T00:00:00Z")
    assert staged["validation_bound"] is True and staged["status"] == "validation_bound"


def test_committed_binding_with_unavailable_readback_is_partial_not_rollback(independent):
    from loopx.chat_goal_configuration_api import GoalConfigurationRequestMixin
    registry, _, _ = independent
    applied = enable_writer(registry)
    class Unavailable(GoalConfigurationRequestMixin):
        def _read_public_goal_configuration(self, _goal):
            raise RuntimeError("readback transport unavailable")
        def _send_json(self, payload, *, status=200):
            self.response, self.http_status = payload, status
    handler = Unavailable()
    handler._send_applied_goal_configuration(applied=applied, capability_id="reward_memory", desired_configuration={},
        desired_public={"revision": "preview", "capability_catalog": {}}, goal_id="binding-goal", plan={"plan_revision": "one", "writes_required": True})
    assert handler.http_status == 207 and handler.response["source_written"] is True
    assert handler.response["readback_verified"] is False
    assert "prior state was preserved" not in handler.response["error"]
    assert status_for(registry, "writer")["available"] is True


@pytest.mark.parametrize("limit", [1, 3])
def test_stale_binding_journals_cannot_starve_bounded_current_route_retries(independent, limit):
    from loopx.capabilities.context_providers.base import ContextProviderSync
    from loopx.capabilities.reward_memory.outcome_lifecycle import (
        reconcile_pending_turn_outcome_ingests,
        run_configured_turn_outcome_ingest,
        turn_outcome_ingest_sidecar_path,
    )
    from tests.capabilities.test_agent_turn_recall import raw_config
    from tests.capabilities.test_reward_memory_outcome_lifecycle import (
        OutcomeProvider,
        _reflection,
        _settlement_evidence,
    )

    class AmbiguousPerEventProvider(OutcomeProvider):
        def sync(self, **kwargs):
            result = super().sync(**kwargs)
            if result.write_count == 0:
                return result
            return ContextProviderSync(
                provider=self.provider_id, namespace=result.namespace,
                status="committed_pending", observed_at=result.observed_at,
                requested_count=1, completed_count=0, write_count=result.write_count,
                result_refs=result.result_refs, pending_count=1,
                reason_code="provider_commit_ambiguous", retry_disposition="retry_same_event",
            )

    registry, config_path, _ = independent
    config = raw_config(goal_id="binding-goal", agent_id="writer", peer_ref="agent:writer")
    config["automation"]["automatic_ingest"] = True
    config["corpora"][0]["standing_policy"].update(
        allowed_source_kinds=["research_review"], allowed_actor_roles=["validated_goal_agent"])
    config["corpora"][0]["corpus"]["maintenance"]["writeback_triggers"] = ["research_review"]
    config_path.write_text(json.dumps(config))
    enable_writer(registry)
    provider = AmbiguousPerEventProvider()
    reflection = _reflection()
    request = dict(registry_path=registry, goal_id="binding-goal", agent_id="writer",
        host_result={"reward_memory_reflection_json": reflection},
        settlement_evidence=_settlement_evidence(reflection), provider=provider)

    def stage(turn):
        result = run_configured_turn_outcome_ingest(**request, turn_key=turn)
        assert result["reconciliation_state"] == "pending"
        return turn_outcome_ingest_sidecar_path(Path(read_goal(registry)["repo"]),
            goal_id="binding-goal", agent_id="writer", source_event_id=result["source_event_id"])

    old_paths = [stage(f"retry-old-{index}") for index in range(3)]
    old_bytes = {path: path.read_bytes() for path in old_paths}
    original_provider = provider
    original_config = config_path.read_bytes()
    config["surfaces"][0]["recall_profile"]["limit"] = 3
    config["project_provider_binding"]["corpus_scopes"][0]["scope_ref"] += "/replacement"
    config["corpora"][0]["corpus"]["provider_scope_ref_digest"] = hashlib.sha256(
        config["project_provider_binding"]["corpus_scopes"][0]["scope_ref"].encode()).hexdigest()[:16]
    config_path.write_text(json.dumps(config))
    enable_writer(registry)
    provider = AmbiguousPerEventProvider()
    request["provider"] = provider
    # Fixed identities put every current journal after all three stale ones.
    current_paths = [stage(f"retry-new-{index}") for index in (5, 7)]
    assert max(path.name for path in old_paths) < min(path.name for path in current_paths)
    assert provider.sync_calls == 2 and len(provider.resources) == 2
    stale_replay = run_configured_turn_outcome_ingest(**request, turn_key="retry-old-0")
    assert stale_replay["reason_code"] == "outcome_sidecar_identity_mismatch"
    assert provider.sync_calls == 2

    remaining = 2
    while remaining:
        attempts = min(limit, remaining)
        before = provider.sync_calls
        result = reconcile_pending_turn_outcome_ingests(
            registry_path=registry, goal_id="binding-goal", agent_id="writer",
            provider=provider, limit=limit)
        assert result["pending_count"] == 3 + remaining
        assert result["attempted_count"] == result["completed_count"] == attempts, [
            (receipt["status"], receipt.get("reason_code")) for receipt in result["receipts"]]
        assert result["provider_sync_count"] == attempts
        assert provider.sync_calls == before + attempts
        assert all(receipt["exact_readback_verified"] and receipt["deduplicated"]
            for receipt in result["receipts"])
        assert all(path.read_bytes() == original for path, original in old_bytes.items())
        assert original_provider.sync_calls == 3
        remaining -= attempts

    calls = provider.sync_calls
    empty = reconcile_pending_turn_outcome_ingests(
        registry_path=registry, goal_id="binding-goal", agent_id="writer", provider=provider, limit=limit)
    assert empty["pending_count"] == 3 and empty["attempted_count"] == 0
    replay = run_configured_turn_outcome_ingest(**request, turn_key="retry-new-5")
    assert replay["sidecar_receipt_reused"] and replay["provider_sync_count"] == 0
    assert provider.sync_calls == calls and len(provider.resources) == 2
    assert all(json.loads(path.read_text())["status"] == "completed" for path in current_paths)
    assert all(path.read_bytes() == original for path, original in old_bytes.items())
    # Retention also preserves recovery through the original binding owner.
    config_path.write_bytes(original_config)
    enable_writer(registry)
    restored = reconcile_pending_turn_outcome_ingests(
        registry_path=registry, goal_id="binding-goal", agent_id="writer", provider=original_provider)
    assert restored["pending_count"] == restored["completed_count"] == 3
    assert original_provider.sync_calls == 6 and len(original_provider.resources) == 3
