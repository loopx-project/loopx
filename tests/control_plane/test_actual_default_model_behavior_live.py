"""Fixture preparation uses real authority; model/source identities are test doubles."""

from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from loopx.control_plane.testing.replan_semantic_action_behavior import (
    _build_fixture as build_replan_fixture,
)
from loopx.control_plane.testing.terminal_settlement_tool_behavior import (
    _build_fixture as build_terminal_fixture,
)


@pytest.mark.parametrize("use_alias", [False, True], ids=["physical", "symlink"])
def test_live_runner_prepares_canonical_fixtures_through_physical_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, use_alias: bool,
) -> None:
    physical = tmp_path / "physical"
    physical.mkdir()
    temporary = physical
    if use_alias:
        temporary = tmp_path / "alias"
        try:
            temporary.symlink_to(physical, target_is_directory=True)
        except OSError:
            pytest.skip("creating a directory symlink is unavailable")

    source = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "live_portfolio_fixture", source / "scripts/qualify-doubao-model-behavior-live.py",
    )
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    monkeypatch.setattr(runner, "collect_release_source_identity", lambda _: {"git_dirty": False})
    monkeypatch.setattr(sys, "argv", [str(spec.origin), "--qualification-id", "offline-path-fixture"])

    @contextmanager
    def temporary_directory(**_):
        yield str(temporary)

    monkeypatch.setattr(runner, "TemporaryDirectory", temporary_directory)
    monkeypatch.setattr(runner, "build_actual_default_model_behavior_scenario_inputs", lambda _: ({}, {}))
    prepared = []

    def factory(prepare=None):
        def qualify(*, fixture_root, qualification_id, **_):
            # Exercise the existing registry witness and canonical File creation,
            # rather than supplying the postcondition that rejected the alias.
            fixture = prepare(fixture_root)
            prepared.append(fixture.project_root)
            return {"qualification_passed": True}

        return lambda **_: SimpleNamespace(qualify=qualify)

    for name in (
        "DoubaoModelBehaviorActor", "DoubaoOnboardingModelBehaviorActor",
        "DoubaoSelectedTodoToolBehaviorActor", "DoubaoScopedGateSuccessorToolBehaviorActor",
        "DoubaoCapabilityMonitorRepairToolBehaviorActor",
    ):
        monkeypatch.setattr(getattr(runner, name), "from_environment", factory())
    monkeypatch.setattr(runner.DoubaoTerminalSettlementToolBehaviorActor,
                        "from_environment", factory(build_terminal_fixture))
    monkeypatch.setattr(runner.DoubaoReplanSemanticActionBehaviorActor,
                        "from_environment", factory(build_replan_fixture))

    def portfolio(*_, terminal_settlement_actor, replan_semantic_action_actor, **__):
        terminal_settlement_actor("terminal")
        replan_semantic_action_actor("replan")
        return {"qualification_passed": True}

    monkeypatch.setattr(runner, "run_actual_default_model_behavior_portfolio", portfolio)
    assert runner.main() == 0
    assert len(prepared) == 2
    assert all(path == path.resolve() and path.is_relative_to(physical.resolve()) for path in prepared)
    assert json.loads(capsys.readouterr().out)["provider_call_count"] == 0


@pytest.mark.parametrize("base_url", [None, "", "   ", "https://api.deepseek.com", "https://api.deepseek.com/", "https://api.deepseek.com/v1", "https://api.deepseek.com/v1/"])
def test_resolve_deepseek_endpoint_accepts_canonical_urls(base_url):
    from loopx.control_plane.testing.doubao_model_behavior_actor import (
        DEEPSEEK_CHAT_COMPLETIONS_ENDPOINT,
        resolve_deepseek_chat_completions_endpoint,
    )

    assert resolve_deepseek_chat_completions_endpoint(base_url) == DEEPSEEK_CHAT_COMPLETIONS_ENDPOINT


@pytest.mark.parametrize("bad_url", [
    "http://api.deepseek.com",
    "https://api.deepseek.com/anthropic",
    "https://api.deepseek.com/v2",
    "https://evil.example/v1",
])
def test_resolve_deepseek_endpoint_rejects_noncanonical_urls(bad_url):
    from loopx.control_plane.testing.doubao_model_behavior_actor import (
        DoubaoActorTransportError,
        resolve_deepseek_chat_completions_endpoint,
    )

    with pytest.raises(DoubaoActorTransportError) as exc_info:
        resolve_deepseek_chat_completions_endpoint(bad_url)
    assert exc_info.value.error_code == "noncanonical_endpoint"


def test_deepseek_actors_strip_markdown_fences_and_preserve_canonical_boundary(monkeypatch):
    from io import BytesIO
    from urllib.error import HTTPError
    from loopx.control_plane.testing.doubao_model_behavior_actor import (
        DEEPSEEK_CHAT_COMPLETIONS_ENDPOINT,
        DEEPSEEK_FLASH_MODEL,
        DEEPSEEK_V4_PRO_MODEL,
        DeepSeekModelBehaviorActor,
        DeepSeekOnboardingModelBehaviorActor,
        DoubaoActorTransportError,
        _direct_deepseek_transport,
    )
    from loopx.control_plane.testing.model_behavior_qualification import (
        build_model_behavior_actor_request,
        normalize_model_behavior_actor_result,
    )
    from loopx.control_plane.testing.onboarding_model_behavior_qualification import (
        ONBOARDING_MODEL_BEHAVIOR_DECISION_SCHEMA_VERSION,
        build_onboarding_model_behavior_actor_request,
        normalize_onboarding_model_behavior_actor_result,
        onboarding_entry_semantic_contract,
    )

    decision = {
        "schema_version": "model_behavior_decision_v0",
        "decision": "execute",
        "selected_todo_id": "todo_fixture001",
        "user_action_required": False,
        "must_attempt_work": True,
        "delivery_allowed": True,
        "quiet_noop_allowed": False,
        "external_write_requested": False,
        "intended_action_kinds": ["inspect", "test", "writeback"],
        "reason_codes": ["bounded_delivery"],
    }
    captured = {}

    def transport(*, endpoint, headers, body, timeout_seconds):
        captured.update(endpoint=endpoint, headers=dict(headers), body=json.loads(body), timeout_seconds=timeout_seconds)
        return {"choices": [{"message": {"content": f"```json\n{json.dumps(decision)}\n```"}}]}

    actor = DeepSeekModelBehaviorActor.from_environment(
        environ={"DEEPSEEK_API_KEY": "fixture-key-not-a-secret", "DEEPSEEK_BASE_URL": "https://api.deepseek.com/v1"},
        transport=transport,
    )
    req = build_model_behavior_actor_request(
        {"schema_version": "loopx_turn_envelope_v0", "action": {"selected_todo": {"todo_id": "todo_fixture001"}}},
        qualification_id="case-ds-001",
        arm="candidate_packet",
    )
    res = normalize_model_behavior_actor_result(actor(req))
    assert res["actor_ref"] == f"deepseek:{DEEPSEEK_FLASH_MODEL}"
    assert captured["endpoint"] == DEEPSEEK_CHAT_COMPLETIONS_ENDPOINT
    assert captured["body"]["model"] == DEEPSEEK_FLASH_MODEL

    onboarding_packet = {
        "schema_version": "loopx_start_goal_guided_v0",
        "goal_id": "fixture-goal",
        "agent_id": "codex-fixture",
        "guided_transaction": {"writes_now": False, "spends_quota_now": False, "ordered_steps": [{"id": "connect_if_needed"}]},
        "commands": {
            "goal_start_connect_if_needed": "loopx bootstrap",
            "goal_start_refresh_state": "loopx refresh-state",
            "goal_start_host_loop_activation": "loopx heartbeat-prompt --thin",
            "goal_start_quota_should_run": "loopx quota should-run",
        },
        "host_loop_activation": {"agent_id": "codex-fixture", "activation_required_after_todo_write": True},
    }
    onboarding_decision = {
        "schema_version": ONBOARDING_MODEL_BEHAVIOR_DECISION_SCHEMA_VERSION,
        "phase": "entry",
        "next_action": "connect_if_needed",
        "semantic_contract": onboarding_entry_semantic_contract(onboarding_packet),
        "reason_codes": ["source_aligned"],
    }
    onb_actor = DeepSeekOnboardingModelBehaviorActor.from_environment(
        environ={"DEEPSEEK_API_KEY": "fixture-key-not-a-secret", "LOOPX_DEEPSEEK_BEHAVIOR_MODEL": DEEPSEEK_V4_PRO_MODEL},
        transport=lambda **_: {"choices": [{"message": {"content": f"```\n{json.dumps(onboarding_decision)}\n```"}}]},
    )
    onb_res = normalize_onboarding_model_behavior_actor_result(
        onb_actor(build_onboarding_model_behavior_actor_request(onboarding_packet, qualification_id="onb-ds-001", phase="entry")),
        phase="entry",
    )
    assert onb_res["actor_ref"] == f"deepseek:{DEEPSEEK_V4_PRO_MODEL}"

    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        DeepSeekModelBehaviorActor.from_environment(environ={})
    with pytest.raises(ValueError, match="allowlisted"):
        DeepSeekModelBehaviorActor.from_environment(environ={"DEEPSEEK_API_KEY": "fixture", "LOOPX_DEEPSEEK_BEHAVIOR_MODEL": "unknown-ds"})
    with pytest.raises(DoubaoActorTransportError) as err:
        _direct_deepseek_transport(endpoint="https://evil.example/chat/completions", headers={}, body=b"{}", timeout_seconds=1.0)
    assert err.value.error_code == "noncanonical_endpoint"

    class UnauthorizedOpener:
        def open(self, *_, **__):
            raise HTTPError(DEEPSEEK_CHAT_COMPLETIONS_ENDPOINT, 401, "private detail", hdrs=None, fp=BytesIO(b'{"error":"secret"}'))

    monkeypatch.setattr("loopx.control_plane.testing.doubao_model_behavior_actor.build_opener", lambda *_: UnauthorizedOpener())
    with pytest.raises(DoubaoActorTransportError) as auth_err:
        _direct_deepseek_transport(endpoint=DEEPSEEK_CHAT_COMPLETIONS_ENDPOINT, headers={}, body=b"{}", timeout_seconds=1.0)
    assert auth_err.value.error_code == "provider_authentication_failed"
    assert "DEEPSEEK_API_KEY" in str(auth_err.value)
    assert "secret" not in str(auth_err.value)


def test_deepseek_tool_client_and_all_five_tool_actors_reuse_state_machines():
    from loopx.control_plane.testing.capability_monitor_repair_tool_behavior import DoubaoCapabilityMonitorRepairToolBehaviorActor
    from loopx.control_plane.testing.doubao_model_behavior_actor import (
        DEEPSEEK_CHAT_COMPLETIONS_ENDPOINT,
        DEEPSEEK_FLASH_MODEL,
        DEEPSEEK_V4_PRO_MODEL,
        DoubaoActorTransportError,
    )
    from loopx.control_plane.testing.model_tool_behavior import DeepSeekExecToolClient, DoubaoExecToolClient
    from loopx.control_plane.testing.replan_semantic_action_behavior import DoubaoReplanSemanticActionBehaviorActor
    from loopx.control_plane.testing.scoped_gate_successor_tool_behavior import DoubaoScopedGateSuccessorToolBehaviorActor
    from loopx.control_plane.testing.selected_todo_tool_behavior import DoubaoSelectedTodoToolBehaviorActor
    from loopx.control_plane.testing.terminal_settlement_tool_behavior import DoubaoTerminalSettlementToolBehaviorActor

    seen = []

    def transport(*, endpoint, headers, body, timeout_seconds):
        seen.append((endpoint, json.loads(body)["model"]))
        return {"choices": [{"message": {"role": "assistant", "content": "done"}}]}

    client = DeepSeekExecToolClient.from_environment(
        environ={"DEEPSEEK_API_KEY": "fixture-key-not-a-secret"},
        transport=transport,
    )
    assert client.actor_ref == f"deepseek:{DEEPSEEK_FLASH_MODEL}"
    assert client.next_final_content([{"role": "user", "content": "hi"}]) == "done"
    assert seen == [(DEEPSEEK_CHAT_COMPLETIONS_ENDPOINT, DEEPSEEK_FLASH_MODEL)]

    custom = DoubaoExecToolClient(
        api_key="fixture-key", model=DEEPSEEK_V4_PRO_MODEL, timeout_seconds=10,
        transport=transport, endpoint=DEEPSEEK_CHAT_COMPLETIONS_ENDPOINT,
        allowed_models={DEEPSEEK_V4_PRO_MODEL}, provider_prefix="deepseek",
    )
    assert custom.actor_ref == f"deepseek:{DEEPSEEK_V4_PRO_MODEL}"

    with pytest.raises(DoubaoActorTransportError) as bad_ep:
        DeepSeekExecToolClient(api_key="fixture-key", endpoint="https://evil.example/chat/completions")
    assert bad_ep.value.error_code == "noncanonical_endpoint"

    for actor_cls in (
        DoubaoSelectedTodoToolBehaviorActor,
        DoubaoReplanSemanticActionBehaviorActor,
        DoubaoScopedGateSuccessorToolBehaviorActor,
        DoubaoCapabilityMonitorRepairToolBehaviorActor,
        DoubaoTerminalSettlementToolBehaviorActor,
    ):
        actor = actor_cls.from_deepseek_environment(
            environ={"DEEPSEEK_API_KEY": "fixture-key-not-a-secret"},
            model=DEEPSEEK_V4_PRO_MODEL,
        )
        assert actor._client.actor_ref == f"deepseek:{DEEPSEEK_V4_PRO_MODEL}"
        with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
            actor_cls.from_deepseek_environment(environ={})


def test_deepseek_live_runner_wires_all_actors_and_defaults_cleanly(tmp_path, monkeypatch, capsys):
    source = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "live_deepseek_portfolio_fixture", source / "scripts/qualify-deepseek-model-behavior-live.py",
    )
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    assert runner._parser().parse_args(["--qualification-id", "q1"]).max_workers == 6
    assert runner._parser().parse_args(["--qualification-id", "q1"]).model == "deepseek-flash"

    monkeypatch.setattr(runner, "collect_release_source_identity", lambda _: {"git_dirty": False})
    monkeypatch.setattr(runner, "build_actual_default_model_behavior_scenario_inputs", lambda _: ({}, {}))
    seen_models = []

    def factory(*, model=None, **_):
        seen_models.append(model)
        return SimpleNamespace(qualify=lambda **__: {"qualification_passed": True})

    for name in ("DeepSeekModelBehaviorActor", "DeepSeekOnboardingModelBehaviorActor"):
        monkeypatch.setattr(getattr(runner, name), "from_environment", factory)
    for name in (
        "DoubaoSelectedTodoToolBehaviorActor",
        "DoubaoReplanSemanticActionBehaviorActor",
        "DoubaoScopedGateSuccessorToolBehaviorActor",
        "DoubaoCapabilityMonitorRepairToolBehaviorActor",
        "DoubaoTerminalSettlementToolBehaviorActor",
    ):
        monkeypatch.setattr(getattr(runner, name), "from_deepseek_environment", factory)
    monkeypatch.setattr(runner, "run_actual_default_model_behavior_portfolio", lambda *_, max_workers, **__: {"qualification_passed": True, "max_workers": max_workers})

    assert runner.main(["--qualification-id", "ds-offline", "--model", "deepseek-v4-pro", "--max-workers", "4"]) == 0
    assert seen_models == ["deepseek-v4-pro"] * 7
    payload = json.loads(capsys.readouterr().out)
    assert payload["provider_call_count"] == 0 and payload["max_workers"] == 4
