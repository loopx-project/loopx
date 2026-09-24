"""Deterministic probe/oracle checks; no provider calls in pytest."""
import json
import runpy
from pathlib import Path

import pytest

from loopx.control_plane.testing.host_prompt_behavior import cases, probe_messages, run_probe


ANSWERS = [
    {"action": "work", "notify": False, "finish_goal": False},
    {"action": "wait", "notify": True, "finish_goal": False},
    {"action": "wait", "notify": False, "finish_goal": False},
    {"action": "replan", "notify": False, "finish_goal": False},
    {"action": "work", "notify": True, "finish_goal": False},
]


class ScriptedClient:
    actor_ref = "scripted-not-model-evidence"

    def __init__(self, mutation=None, repeats=1):
        self.count = 0
        self.mutation = mutation
        self.repeats = repeats

    def next_final_content(self, messages):
        assert "expected" not in json.dumps(messages)
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
            assert (
                "monitor_changed:<monitor>" in body
                or "wait->monitor+successor/work" in body
                or "外部等待须 open+monitor_changed+successor" in body
            )
            assert "--codex-app" in body
            assert "LOOPX_TURN=<current_time_iso>" in body
            assert case["id"] not in json.dumps(messages)
            assert "expected" not in json.dumps(messages)
    client = ScriptedClient()
    report = run_probe(client, repeats=1)
    assert report["qualification_passed"]
    assert client.count == report["provider_call_count"] == 10
    assert not report["host_execution_qualified"]


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
