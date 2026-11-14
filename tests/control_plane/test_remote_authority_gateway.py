"""The real local CLI does not admit remote work or alter default-off decisions."""

from __future__ import annotations

import json

import pytest

from examples.control_plane.quota_plan_fixtures import SCOPED_AGENT_ID, write_cli_fixture
from loopx.control_plane.testing.canary_harness import run_json_cli


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_local_cli_ignores_disabled_and_other_goal_remote_binding(tmp_path, monkeypatch, provider):
    registry, runtime, _ = write_cli_fixture(tmp_path, scoped_agents=True, provider=provider)
    # Old environment-only canary inputs cannot activate an unconfigured target.
    monkeypatch.setenv("LOOPX_REMOTE_AUTHORITY_CANARY_ENABLED", "1")
    monkeypatch.setenv("LOOPX_AUTHORITY_CREDENTIAL", "unused-synthetic-credential-" + "a" * 32)
    goal_id = "throttled-half"
    scope = ("--goal-id", goal_id, "--agent-id", SCOPED_AGENT_ID)

    def cli(*args):
        return run_json_cli(*args, registry_path=registry, runtime_root=runtime)

    def read():
        prompt = cli("heartbeat-prompt", "--thin", "--codex-app", *scope)
        assert "remote_authority" not in prompt
        decision = cli("quota", "should-run", "--runtime-profile", "outer_controller", *scope)
        assert "remote_gateway" not in decision
        assert decision["ok"] is True
        return tuple(decision.get(key) for key in ("decision", "effective_action", "should_run", "reason"))

    local = read()
    assert local[:3] == ("skip", "throttled_skip", False), local
    document = json.loads(registry.read_text())
    cfg = {"enabled": True, "endpoint": "https://127.0.0.1:1", "tenant_id": "other-synthetic-tenant",
           "actor_id": SCOPED_AGENT_ID, "store_identity": "postgresql:" + "a" * 32, "probe_todo_id": "todo_probe"}
    for goal in document["goals"]:
        if goal["id"] == goal_id:
            goal["remote_authority"] = {**cfg, "enabled": False}
        if goal["id"] == "full-speed":
            goal["remote_authority"] = cfg
    registry.write_text(json.dumps(document), encoding="utf-8")
    assert read() == local

    other = cli("heartbeat-prompt", "--full", "--codex-app",
                "--goal-id", "full-speed", "--agent-id", SCOPED_AGENT_ID)
    assert other["remote_authority"]["status"] == "unverified"
    for key in ("endpoint", "tenant_id", "actor_id", "store_identity", "probe_todo_id"):
        assert key not in other["remote_authority"]
    assert cfg["endpoint"] not in other["task_body"] and cfg["tenant_id"] not in other["task_body"]
