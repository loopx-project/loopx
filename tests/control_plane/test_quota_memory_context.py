"""The App guard must deliver enabled guidance, not only a recall receipt."""
from __future__ import annotations

from contextlib import redirect_stdout
from copy import deepcopy
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
import shlex

import pytest

from loopx.cli import main
from loopx.capabilities.agent_turn_recall.runtime import (
    run_configured_agent_turn_recall_fail_open,
)
from loopx.capabilities.reward_memory.experiment import resolve_reward_memory_experiment
from loopx.capabilities.reward_memory.turn_hooks import memory_context_result
from loopx.control_plane.capability_hooks import (
    TurnContextHookRegistration, dispatch_turn_context_hooks, project_turn_context,
)
from loopx.control_plane.quota.turn_envelope import assert_complete_turn_decision
from tests.capabilities.test_agent_turn_recall import RecallProvider, active_record
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
from tests.control_plane.codex_checkpoint_fixture import read_cli_policy_payload
from tests.control_plane.reward_memory_host_fixture import enable_live_memory
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, _initialize_fixture_authority, _write_fixture,
)


def run_cli(args: list[str]) -> dict:
    output = StringIO()
    with redirect_stdout(output):
        code = main(args)
    assert code == 0, output.getvalue()[:1200]
    return read_cli_policy_payload(output.getvalue(), returncode=code)


def quota_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, *, retire: bool = False):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    text = state.read_text().replace(
        "action_kind=validate", "action_kind=validate continuation_policy=same_agent_non_delivery")
    if retire:
        basis = json.dumps({"retirement_basis": {"target_symbols": ["old_api"]}})
        text = text.replace("- [ ] [P1] Validate and settle the selected delivery.",
            f"- [ ] [P1] Retire old_api and settle the selected delivery. {basis}")
    state.write_text(text)
    _initialize_fixture_authority(project, runtime, provider=provider)
    monkeypatch.chdir(project)
    return registry, ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("entry", ["quota", "thin-heartbeat"])
@pytest.mark.parametrize(("complete", "progressive", "delta", "retire"), [
    (False, "off", "off", False),
    (True, "off", "off", False),
    (True, "on", "off", False),
    (True, "off", "auto", False),
    (True, "on", "auto", True),
], ids=["ordinary", "complete-raw", "complete-progressive", "complete-delta", "complete-with-retirement"])
def test_real_app_guard_delivers_scoped_memory_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, entry: str,
    complete: bool, progressive: str, delta: str, retire: bool,
) -> None:
    registry, route = quota_fixture(tmp_path, monkeypatch, provider, retire=retire)
    enable_live_memory(registry, recall=True, ingest=True)
    _, config = resolve_reward_memory_experiment(
        registry_path=registry, goal_id=GOAL_ID, agent_id=AGENT_ID)
    memory = RecallProvider(active_record(config, expires_at="2099-01-01T00:00:00+00:00"))
    monkeypatch.setattr(
        "loopx.capabilities.agent_turn_recall.run_configured_agent_turn_recall_fail_open",
        lambda **kwargs: run_configured_agent_turn_recall_fail_open(**kwargs, provider=memory))
    turn = "app-memory-delivery"
    source_flags = ["--complete-turn"] if complete else []
    if entry == "thin-heartbeat":
        bootstrap = run_cli(route + ["heartbeat-prompt", "--thin", "--codex-app",
            "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
            "--available-capability", "filesystem_read", *source_flags])
        command = next(line for line in bootstrap["task_body"].splitlines()
            if line.startswith("loopx ") and "quota should-run" in line)
        args = shlex.split(command.replace('${LOOPX_TURN:?}', turn))[1:]
    else:
        args = route + ["quota", "should-run", "--goal-id", GOAL_ID,
            "--agent-id", AGENT_ID, "--codex-app", "--turn-instance-id", turn,
            "--available-capability", "filesystem_read", *source_flags]
    args += ["--context-delivery", progressive, "--delta-delivery", delta]
    first = run_cli(args)
    assert first["should_run"] is True
    assert_complete_turn_decision(first)
    plan = first["interaction_contract"]["cli_channel"]["settlement_plan"]
    # The admitted explicit Host owns composed settlement across presentations.
    spend = next(step for step in plan["ordered_steps"] if step["kind"] == "quota_spend")
    assert plan["schema_version"] == ("quota_settlement_plan_v2" if complete else "quota_settlement_plan_v1")
    assert spend["owner"] == ("runtime" if complete else "agent")
    assert first["reward_memory_recall"]["status"] == "applied"
    assert memory.calls == 1
    # Participation stays small. The complete private input has one carrier.
    assert first["reward_memory_recall"]["context"] is None
    contributions = {item["hook_id"]: item for item in first["turn_context_contributions"]}
    assert set(contributions) == ({"reward-memory.turn-context", "anti-entropy.turn-context"}
                                 if retire else {"reward-memory.turn-context"})
    assert all("basis_digest" not in item for item in contributions.values())
    contribution = contributions["reward-memory.turn-context"]
    guidance = contribution["context_fields"]["reward_memory_recall"]["context"]["guidance"]
    assert len(guidance) == 1 and guidance[0]["content_summary"]
    projected = project_turn_context(first)
    assert projected["reward_memory_recall"]["context"]["guidance"] == guidance
    if retire:
        assert first["anti_entropy_guidance"]["target_symbols"] == ["old_api"]
        assert projected["anti_entropy_context"] == (
            contributions["anti-entropy.turn-context"]["context_fields"]["anti_entropy_context"])
    changed = deepcopy(first)
    changed_plan = changed["interaction_contract"]["cli_channel"]["settlement_plan"]
    changed_plan["ordered_steps"][1]["command_template"] += " --evidence substituted"
    assert project_turn_context(changed) == {}
    assert contribution["result_fields"]["reward_memory_reflection_json"]["maxLength"] == 2400
    assert "private, non-authoritative" in contribution["instructions"][0]
    repeated = run_cli(args)
    assert repeated["ok"] and memory.calls == 1
    assert not repeated.get("reward_memory_recall", {}).get("external_writes_performed")


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("mode", ["unconfigured", "disabled", "stale", "provider-unavailable"])
def test_real_app_guard_keeps_base_work_without_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, mode: str,
) -> None:
    registry, route = quota_fixture(tmp_path, monkeypatch, provider)
    if mode != "unconfigured":
        config_path = enable_live_memory(registry)
        if mode == "disabled":
            data = json.loads(registry.read_text())
            data["goals"][0]["control_plane"]["reward_memory"]["enabled"] = False
            registry.write_text(json.dumps(data))
        elif mode == "stale":
            config_path.write_text(config_path.read_text() + "\n")
    memory = RecallProvider(unavailable=True)
    monkeypatch.setattr(
        "loopx.capabilities.agent_turn_recall.run_configured_agent_turn_recall_fail_open",
        lambda **kwargs: run_configured_agent_turn_recall_fail_open(**kwargs, provider=memory))
    guard = run_cli(route + ["quota", "should-run", "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID, "--codex-app", "--turn-instance-id", "memory-unavailable",
        "--available-capability", "filesystem_read"])
    assert guard["should_run"] is True
    assert guard["interaction_contract"]["agent_channel"]["must_attempt"] is True
    assert memory.calls == (1 if mode == "provider-unavailable" else 0)
    recall = guard["reward_memory_recall"]
    assert recall["status"] != "applied" and not recall["external_writes_performed"]
    for contribution in guard.get("turn_context_contributions", []):
        context = contribution["context_fields"]["reward_memory_recall"].get("context")
        assert not context or not context.get("guidance")
        # Provider degradation does not disable independently enabled reflection.
        assert mode == "provider-unavailable"
        assert contribution["result_fields"]["reward_memory_reflection_json"]["maxLength"] == 2400


def quota_context():
    policy = {"automatic_recall": True, "automatic_ingest": True}
    guard = {"ok": True, "should_run": True, "goal_id": "memory-fixture",
        "mode": "should-run", "effective_action": "normal_run",
        "interaction_contract": {"schema_version": "loopx_interaction_contract_v0",
            "mode": "bounded_delivery", "user_channel": {"action_required": False, "notify": "DONT_NOTIFY"},
            "agent_channel": {"must_attempt": True, "delivery_allowed": True,
                "quiet_noop_allowed": False}, "cli_channel": {}},
        "agent_identity": {"agent_id": "writer", "registered": True},
        "heartbeat_receipt": {"status": "committed", "turn_instance_id": "memory-turn"},
        "goal_boundary": {"capabilities": {"reward_memory": {
            "enabled": True, "configured_for_agent": True, "experiment_available": True,
            **policy}}}}
    recall = {"status": "applied", "context": {"guidance": [{"content_summary": "Scoped guidance."}]},
        "experiment": {"goal_id": "memory-fixture", "agent_id": "writer",
            "enabled": True, "available": True, "configured_for_agent": True, **policy}}
    return guard, recall


@pytest.mark.parametrize("changed", ["goal", "agent", "conflicting-agent", "unregistered",
    "missing-receipt", "uncommitted", "missing-turn", "failed", "skipped",
    "unavailable", "unconfigured", "policy-off", "malformed-envelope"])
def test_app_memory_does_not_outlive_its_binding_or_admission(changed: str) -> None:
    guard, recall = quota_context()
    if changed == "goal":
        guard["goal_id"] = "another-goal"
    elif changed == "agent":
        guard["agent_identity"]["agent_id"] = "another-agent"
    elif changed == "conflicting-agent":
        guard["agent_id"] = "another-agent"
    elif changed == "unregistered":
        guard["agent_identity"]["registered"] = False
    elif changed == "missing-receipt":
        del guard["heartbeat_receipt"]
    elif changed == "uncommitted":
        guard["heartbeat_receipt"]["status"] = "failed"
    elif changed == "missing-turn":
        del guard["heartbeat_receipt"]["turn_instance_id"]
    elif changed == "failed":
        guard["ok"] = False
    elif changed == "skipped":
        guard["should_run"] = False
    elif changed == "unavailable":
        recall["experiment"]["available"] = False
    elif changed == "unconfigured":
        recall["experiment"]["configured_for_agent"] = False
    elif changed == "policy-off":
        guard["goal_boundary"]["capabilities"]["reward_memory"] = {}
    else:
        guard["turn_envelope"] = None
    result = memory_context_result(guard, recall)
    assert result["context_fields"] == {}
    assert result["instructions"] == [] and result["result_fields"] == {}
    assert result["readback"]["reward_memory_recall"]["context"] is None


@pytest.mark.parametrize("changed", ["agent", "policy", "write-scope"])
def test_quota_contribution_is_bound_to_the_projected_authority(changed: str) -> None:
    guard, recall = quota_context()
    result = memory_context_result(guard, recall)
    dispatch_turn_context_hooks(guard, (TurnContextHookRegistration(
        "reward-memory.turn-context", "reward-memory", ("reward_memory_recall",), lambda: result),))
    assert project_turn_context(guard)["reward_memory_recall"]["context"] == recall["context"]
    other = deepcopy(guard)
    if changed == "agent":
        other["agent_identity"]["agent_id"] = "another-agent"
    elif changed == "policy":
        other["goal_boundary"]["capabilities"]["reward_memory"]["automatic_recall"] = False
    else:
        other["goal_boundary"]["write_scope"] = ["another-scope"]
    assert project_turn_context(other) == {}


@pytest.mark.parametrize("size", [40_000, 70_000])
def test_quota_recall_size_limit_preserves_independently_enabled_ingest(size: int) -> None:
    guard, recall = quota_context()
    recall["context"]["guidance"][0]["content_summary"] = "x" * size
    result = memory_context_result(guard, recall)
    delivered = result["context_fields"]["reward_memory_recall"]
    assert (delivered["context"] is not None) is (size == 40_000)
    assert result["result_fields"]["reward_memory_reflection_json"]["maxLength"] == 2400
    assert len(json.dumps(result, separators=(",", ":")).encode()) <= 65536



def _complete_native_memory_source():
    from loopx.control_plane.quota.turn_envelope import compose_turn_decision

    guard, recall = quota_context()
    guard["retained_provider_evidence"] = {"revision": 1, "integer": 2**80, "float": -0.0}
    result = memory_context_result(guard, recall)
    dispatch_turn_context_hooks(guard, (TurnContextHookRegistration(
        "reward-memory.turn-context", "reward-memory", ("reward_memory_recall",), lambda: result),))
    return compose_turn_decision(guard), recall


def test_complete_native_memory_keeps_exact_source_and_existing_guidance() -> None:
    from loopx.control_plane.quota.turn_envelope import (
        assert_complete_turn_decision, quota_capability_automation_source,
    )

    source, recall = _complete_native_memory_source()
    before = json.dumps(source, ensure_ascii=False, sort_keys=True)
    assert_complete_turn_decision(source)
    assert quota_capability_automation_source(source)["agent_id"] == "writer"
    assert project_turn_context(source)["reward_memory_recall"]["context"] == recall["context"]
    result = memory_context_result(source, recall)
    assert result["context_fields"]["reward_memory_recall"]["context"] == recall["context"]
    assert json.dumps(source, ensure_ascii=False, sort_keys=True) == before


@pytest.mark.parametrize("consumer", ["participation", "guidance"])
@pytest.mark.parametrize("corruption", [
    "content-hash", "retained-evidence", "codec", "future-version", "missing-version", "missing-binding",
])
def test_complete_native_memory_never_uses_legacy_binding_for_damaged_source(
    consumer: str, corruption: str,
) -> None:
    from loopx.control_plane.quota.turn_envelope import (
        assert_complete_turn_decision, quota_capability_automation_source,
    )

    source, recall = _complete_native_memory_source()
    before = json.dumps(source, ensure_ascii=False, sort_keys=True)
    damaged = deepcopy(source)
    if corruption == "content-hash":
        damaged["contract_binding"]["content_hash"] = "sha256:" + "0" * 64
    elif corruption == "retained-evidence":
        # This field is outside the historical action-signature coverage.
        damaged["retained_provider_evidence"]["revision"] += 1
    elif corruption == "codec":
        damaged["contract_binding"]["hash_codec"] = "unsupported-codec"
    elif corruption == "future-version":
        damaged["schema_version"] = "unsupported-native-version"
    elif corruption == "missing-version":
        del damaged["schema_version"]
    else:
        del damaged["contract_binding"]
    with pytest.raises((RuntimeError, ValueError)):
        assert_complete_turn_decision(damaged)
    if consumer == "participation":
        with pytest.raises((RuntimeError, ValueError)):
            quota_capability_automation_source(damaged)
        result = memory_context_result(damaged, recall)
        assert result["context_fields"] == {} and result["result_fields"] == {}
    else:
        assert project_turn_context(damaged) == {}
    assert json.dumps(source, ensure_ascii=False, sort_keys=True) == before


def test_source_free_status_observation_survives_damaged_complete_native_source() -> None:
    source, _ = _complete_native_memory_source()
    source["contract_binding"]["content_hash"] = "sha256:" + "0" * 64
    status = {"status": "unavailable"}
    dispatch_turn_context_hooks(source, (TurnContextHookRegistration(
        "observation.status", "observation", ("context_status",), lambda: {
            "context_fields": {}, "instructions": [], "result_fields": {},
            "readback": {"context_status": status},
        }),))
    assert source["context_status"] == status
    assert project_turn_context(source) == {}


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("entry", ["quota", "thin-heartbeat"])
def test_real_guard_complete_memory_rejects_changed_source_without_provider_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, entry: str,
) -> None:
    from loopx.control_plane.quota.turn_envelope import (
        assert_complete_turn_decision, compose_turn_decision, quota_capability_automation_source,
    )

    registry, route = quota_fixture(tmp_path, monkeypatch, provider)
    enable_live_memory(registry, recall=True, ingest=True)
    _, config = resolve_reward_memory_experiment(
        registry_path=registry, goal_id=GOAL_ID, agent_id=AGENT_ID)
    memory = RecallProvider(active_record(config, expires_at="2099-01-01T00:00:00+00:00"))
    monkeypatch.setattr(
        "loopx.capabilities.agent_turn_recall.run_configured_agent_turn_recall_fail_open",
        lambda **kwargs: run_configured_agent_turn_recall_fail_open(**kwargs, provider=memory))
    turn = "complete-memory-source-check"
    capture_root = tmp_path / "native-memory-captures"
    capture_root.mkdir(mode=0o700)
    if entry == "thin-heartbeat":
        bootstrap = run_cli(route + ["heartbeat-prompt", "--thin", "--codex-app", "--complete-turn",
            "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
            "--available-capability", "filesystem_read",
            "--decision-output-root", str(capture_root)])
        command = next(line for line in bootstrap["task_body"].splitlines()
            if line.startswith("loopx ") and "quota should-run" in line)
        args = shlex.split(command.replace('${LOOPX_TURN:?}', turn))[1:]
    else:
        args = route + ["quota", "should-run", "--goal-id", GOAL_ID,
            "--agent-id", AGENT_ID, "--codex-app", "--turn-instance-id", turn,
            "--complete-turn", "--available-capability", "filesystem_read", "--decision-output-root", str(capture_root)]
    source = run_cli(args + ["--context-delivery", "off", "--delta-delivery", "off"])
    assert_complete_turn_decision(source)
    assert project_turn_context(source)["reward_memory_recall"]["context"]["guidance"]
    [capture] = capture_root.glob("decision-*/decision.json")
    captured = capture.read_bytes()
    saved = json.loads(captured)
    assert saved["contract_binding"] == source["contract_binding"]
    assert saved["heartbeat_receipt"] == source["heartbeat_receipt"]
    contribution = saved["turn_context_contributions"][0]
    assert "basis_digest" not in contribution

    page = run_cli(route + ["quota", "context-read", "--capture-file", str(capture),
        "--capture-sha256", sha256(captured).hexdigest(), "--select", "/turn_context_contributions"])
    assert page["eof"] is True
    [delivered] = json.loads(page["content"])["selections"]
    assert delivered["value"] == [contribution]

    # Even both missing version markers cannot make a captured contribution
    # reusable as an unsigned quota input or rebind it to a new decision.
    unsealed = {key: value for key, value in saved.items() if key not in {"schema_version", "contract_binding"}}
    assert project_turn_context(unsealed) == {}
    with pytest.raises(RuntimeError, match="requires the original quota composition"):
        compose_turn_decision(unsealed)
    assert capture.read_bytes() == captured
    before = json.dumps(source, ensure_ascii=False, sort_keys=True)
    damaged = deepcopy(source)
    damaged["retained_provider_evidence"] = {"revision": 2}
    assert project_turn_context(damaged) == {}
    with pytest.raises((RuntimeError, ValueError)):
        quota_capability_automation_source(damaged)
    assert memory.calls == 1
    assert json.dumps(source, ensure_ascii=False, sort_keys=True) == before
