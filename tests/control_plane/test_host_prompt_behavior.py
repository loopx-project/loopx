"""Deterministic probe/oracle checks; no provider calls in pytest."""
import json
import runpy
from pathlib import Path

import pytest

from loopx.control_plane.effect_runtime import EffectRuntimeRejected
from loopx.control_plane.quota.turn_envelope import assert_complete_turn_decision
from loopx.control_plane.testing.action_portfolio_scenarios import (
    external_wait_fallback_scenario_source,
)
from loopx.control_plane.testing.host_prompt_behavior import cases, probe_messages, run_probe


ANSWERS = [
    {"action": "work", "notify": False, "finish_goal": False},
    {"action": "wait", "notify": True, "finish_goal": False},
    {"action": "wait", "notify": False, "finish_goal": False},
    {"action": "replan", "notify": False, "finish_goal": False},
    {"action": "work", "notify": True, "finish_goal": False},
]


def sent_packet(messages):
    heading, payload = messages[-1]["content"].split("\n", 1)
    assert heading == "Current quota result:"
    return json.loads(payload)


class ScriptedClient:
    actor_ref = "scripted-not-model-evidence"

    def __init__(self, mutation=None, repeats=1):
        self.count = 0
        self.mutation = mutation
        self.repeats = repeats

    def next_final_content(self, messages):
        assert "expected" not in sent_packet(messages)
        answer = dict(ANSWERS[(self.count // self.repeats) % len(ANSWERS)])
        self.count += 1
        if self.mutation:
            answer.update(self.mutation)
        return json.dumps(answer)


def test_probe_uses_current_production_prompts_and_hidden_independent_oracle():
    fallback = cases()[-1]
    assert fallback["packet"]["selected_todo"]["todo_id"] == "todo_external_wait_fallback"
    assert fallback["packet"]["execution_obligation"]["must_attempt_work"] is True
    assert fallback["packet"]["interaction_contract"]["user_channel"]["notify"] == "NOTIFY"
    assert "external_wait_observation" not in fallback["packet"]
    for mode in ("thin", "brief"):
        for case in cases():
            messages = probe_messages(mode, case["packet"])
            body = messages[1]["content"]
            assert "execution_obligation.must_attempt_work" in body
            assert "heartbeat_recommendation.agent_must_attempt" in body
            assert "projected monitor/continuation" in body
            assert "no invented successor" in body
            assert "no wait spend" in body.lower() or "no spend for waiting alone." in body.lower()
            assert "resume_at:<RFC3339>" in body
            assert "yield to host scheduler" in body
            assert "Fresh admission on resume." in body
            assert "--codex-app" in body
            assert "LOOPX_TURN=<current_time_iso>" in body
            assert case["id"] not in json.dumps(messages)
            assert sent_packet(messages) == case["packet"]
    client = ScriptedClient()
    report = run_probe(client, repeats=1)
    assert report["qualification_passed"]
    assert client.count == report["provider_call_count"] == 10
    assert not report["host_execution_qualified"]


def test_oracle_boundary_allows_version_preconditions_but_rejects_answer_leakage():
    packet = {"lease": {"expected_version": 7}}
    messages = probe_messages("thin", packet)
    assert sent_packet(messages) == packet
    assert json.loads(ScriptedClient().next_final_content(messages)) == ANSWERS[0]
    with pytest.raises(AssertionError):
        ScriptedClient().next_final_content(probe_messages("thin", cases()[0]))


def test_fallback_probe_delivers_one_complete_source_to_both_prompt_modes(monkeypatch):
    source = external_wait_fallback_scenario_source()
    stop = "保留完整材料和原权限。" * 60 + "\nStop when the original authority is revoked."
    task = source["selected_todo"]["text"] + "\n" + "不得遗漏验收要求。" * 60
    source["goal_boundary"]["stop_condition"] = stop
    source["selected_todo"]["text"] = task
    monkeypatch.setattr(
        "loopx.control_plane.testing.host_prompt_behavior.external_wait_fallback_scenario_source",
        lambda: source,
    )

    class CompleteSourceClient(ScriptedClient):
        def __init__(self):
            super().__init__()
            self.fallbacks = []

        def next_final_content(self, messages):
            packet = sent_packet(messages)
            if packet.get("selected_todo", {}).get("todo_id") == "todo_external_wait_fallback":
                assert_complete_turn_decision(packet)
                assert packet["goal_boundary"]["stop_condition"] == stop
                assert packet["selected_todo"]["text"] == task
                self.fallbacks.append(packet)
            return super().next_final_content(messages)

    client = CompleteSourceClient()
    report = run_probe(client, repeats=1)
    assert report["qualification_passed"]
    assert client.count == report["provider_call_count"] == 10
    assert len(client.fallbacks) == 2
    assert client.fallbacks[0] == client.fallbacks[1]
    assert source["goal_boundary"]["stop_condition"] == stop
    assert source["selected_todo"]["text"] == task


@pytest.mark.parametrize("reserved", [
    {"schema_version": "unknown_source_version"}, {"contract_binding": {}},
])
def test_unknown_source_binding_is_rejected_before_provider_call(monkeypatch, reserved):
    source = external_wait_fallback_scenario_source()
    source.update(reserved)
    monkeypatch.setattr(
        "loopx.control_plane.testing.host_prompt_behavior.external_wait_fallback_scenario_source",
        lambda: source,
    )
    client = ScriptedClient()
    with pytest.raises(EffectRuntimeRejected, match="reserved version/binding"):
        run_probe(client, repeats=1)
    assert client.count == 0


@pytest.mark.parametrize("mutation", [
    {"action": "wait"}, {"notify": True}, {"finish_goal": True}, {"notify": 0},
])
def test_oracle_rejects_quiet_noop_spurious_notification_early_finish_and_wrong_types(mutation):
    report = run_probe(ScriptedClient(mutation), repeats=1)
    assert not report["qualification_passed"]


def test_later_success_does_not_erase_a_failed_independent_attempt():
    class FailOnce(ScriptedClient):
        def next_final_content(self, messages):
            self.mutation = {"action": "wait"} if self.count == 0 else None
            return super().next_final_content(messages)

    report = run_probe(FailOnce(repeats=2), repeats=2)
    assert report["provider_call_count"] == 20
    assert not report["qualification_passed"]
    assert sum(not row["passed"] for row in report["results"]) == 1


def test_release_probe_is_default_off_and_missing_environment_is_not_a_live_pass(monkeypatch, capsys):
    script = Path(__file__).resolve().parents[2] / "scripts/qualify-host-prompt-release.py"
    main = runpy.run_path(str(script))["main"]
    monkeypatch.setenv("ARK_API_KEY", "test-only-unused")
    assert main([]) == 0
    assert json.loads(capsys.readouterr().out)["provider_call_count"] == 0
    monkeypatch.delenv("ARK_API_KEY")
    assert main(["--release-live"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "skipped"


def test_attempted_failure_is_not_reported_as_environment_skip(monkeypatch, capsys):
    script = Path(__file__).resolve().parents[2] / "scripts/qualify-host-prompt-release.py"
    main = runpy.run_path(str(script))["main"]
    def fail(*args, **kwargs):
        raise RuntimeError("private provider failure must not escape")
    monkeypatch.setitem(main.__globals__, "run_probe", fail)
    monkeypatch.setenv("ARK_API_KEY", "test-only-unused")
    assert main(["--release-live"]) == 1
    result = capsys.readouterr().out
    assert "private provider" not in result
    assert json.loads(result)["status"] == "failed"


def test_release_probe_supports_deepseek_provider_and_fails_closed(monkeypatch, capsys):
    script = Path(__file__).resolve().parents[2] / "scripts/qualify-host-prompt-release.py"
    main = runpy.run_path(str(script))["main"]
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    assert main(["--release-live", "--provider", "deepseek"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "skipped"

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only-unused")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://evil.example/v1")
    assert main(["--release-live", "--provider", "deepseek"]) == 1
    assert json.loads(capsys.readouterr().out) == {"status": "failed", "reason": "probe_execution_failed"}

    seen = []

    def fake_probe(client, *, repeats):
        seen.append((client.actor_ref, repeats))
        return {"qualification_passed": True, "actor_ref": client.actor_ref, "provider_call_count": 0}

    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setitem(main.__globals__, "run_probe", fake_probe)
    assert main(["--release-live", "--provider", "deepseek", "--model", "deepseek-v4-pro", "--repeats", "1"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["actor_ref"] == "deepseek:deepseek-v4-pro"
    assert seen == [("deepseek:deepseek-v4-pro", 1)]


def test_probe_handles_dual_global_roots_and_json_fences(monkeypatch, tmp_path):
    (tmp_path / ".loopx").mkdir()
    (tmp_path / ".loopx" / "registry.global.json").write_text("{}", encoding="utf-8")
    (tmp_path / ".codex" / "loopx").mkdir(parents=True)
    (tmp_path / ".codex" / "loopx" / "registry.global.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))

    class FencedClient(ScriptedClient):
        def next_final_content(self, messages):
            return f"```json\n{super().next_final_content(messages)}\n```"

    report = run_probe(FencedClient(), repeats=1)
    assert report["qualification_passed"] is True
    assert report["provider_call_count"] == 10
