"""Actual request forwarding follows canonical waits under the original Turn."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace
import urllib.error
import urllib.request
from urllib.parse import urlsplit

import pytest

from benchmark.runtime.research_request_guard import ResearchRequestGuard
from loopx.control_plane.goals.first_party_host_admission import FirstPartyHostGoalAdmission
from canonical_authority_fixture import isolate_sqlite_runtime
import test_quota_settlement_cli as fixture
from tests.capabilities.test_provider_gateway import _recording_upstream


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_wait_at_request_boundary_has_no_upstream_effect_and_retains_lineage(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = fixture._write_fixture(tmp_path)
    fixture._initialize_fixture_authority(project, runtime, provider=provider)

    def cli(*args):
        code, result = fixture._run_cli(registry, runtime, *args, cwd=project)
        assert code == 0, result
        return result

    turn = "request-boundary-turn"
    binding = ("--goal-id", fixture.GOAL_ID, "--agent-id", fixture.AGENT_ID)
    initial = cli("quota", "should-run", *binding, "--runtime-profile", "generic_cli",
        "--todo-id", fixture.TODO_ID, "--turn-instance-id", turn, "--scan-path", str(project))
    assert initial["should_run"]
    session = SimpleNamespace(session_id="native-session-fixture", goal_ref=None,
        admission=FirstPartyHostGoalAdmission.for_plan(registry_path=registry, goal_id=fixture.GOAL_ID,
                                                      planned_goal_ref=None))
    wake = tmp_path / "wake"
    wake.mkdir()
    receipt = {"turn_id": "wake-fixture"}
    with _recording_upstream() as (url, upstream):
        env = dict(os.environ, LOOPX_TURN=turn, LOOPX_REGISTRY=str(registry),
            LOOPX_RUNTIME_ROOT=str(runtime), LOOPX_PROJECT=str(project), LOOPX_GOAL_ID=fixture.GOAL_ID,
            LOOPX_AGENT_ID=fixture.AGENT_ID, LOOPX_PHASE_DEADLINE_EPOCH=str(time.time() + 180),
            OPENAI_BASE_URL=url, OPENAI_API_KEY="fixture-credential",
            NO_PROXY=urlsplit(url).netloc, HTTP_PROXY="http://127.0.0.1:1")
        guard = ResearchRequestGuard(env, receipt, wake)
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(180)"],
                                   start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            with guard.serve():
                guard.bind(process, env, session, original_quota=initial)

                def request():
                    return urllib.request.urlopen(urllib.request.Request(guard.base_url + "/responses",
                        data=b'{"input":"private fixture body"}', headers={"Content-Type": "application/json",
                        "session-id": "native-session-fixture", "thread-id": "native-thread-fixture",
                        "x-client-request-id": "client-request-fixture",
                        "x-codex-turn-metadata": json.dumps({"session_id": "native-session-fixture",
                            "thread_id": "native-thread-fixture", "turn_id": "native-turn-fixture",
                            "installation_id": "must-not-be-recorded"})}), timeout=40)

                with request() as response:
                    assert json.load(response) == {"ok": True}
                assert upstream["body"]
                upstream.clear()
                cli("todo", "update", *binding, "--todo-id", fixture.TODO_ID,
                    "--status", "deferred", "--resume-when", "resume_at:2099-01-01T00:00:00Z")
                with pytest.raises(urllib.error.HTTPError) as refused:
                    request()
                assert refused.value.code == 409
                assert upstream == {}, "no paid request may escape after the current wait"
                process.wait(timeout=10)
                assert guard.refused and receipt["request_admission_refused"]
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
    encoded = (wake / "provider-requests.jsonl").read_text()
    assert "fixture-credential" not in encoded and "private fixture body" not in encoded
    assert "must-not-be-recorded" not in encoded
    events = [json.loads(line) for line in encoded.splitlines()]
    finished = [event for event in events if event["event"] == "finished"]
    assert [event["forwarded"] for event in finished] == [True, False]
    assert len({event["request_id"] for event in finished}) == 2
    assert all(event["wake_id"] == "wake-fixture" and event["loopx_turn_id"] == turn
               and event["native_turn_id"] == "native-turn-fixture" for event in finished)
