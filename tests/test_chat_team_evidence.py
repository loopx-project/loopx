"""Owner Chat reads exactly the original delegation, with live acceptance checks."""
import json
import shutil
import subprocess
import sys

import pytest

from loopx.chat_runtime import ChatRuntimeController
from loopx.chat_store import ChatSessionStore
from test_local_delegation import brief, service as delegation_service, wait

service = delegation_service


def test_chat_and_cli_read_same_accepted_artifact_and_reject_stale_scope(service):
    root, runner = service
    runner.start("analysis", "analysis-1", brief())
    accepted = wait(runner)
    assert accepted["status"] == "accepted"
    repo = root / "project"
    config = repo / ".loopx/config/delegations.json"
    config.parent.mkdir(parents=True)
    shutil.copyfile(runner.config, config)
    registry = json.loads(runner.registry.read_text())
    registry["goals"][0]["spawn_policy"] = {
        "mode": "multi_subagent", "allowed": True, "max_children": 2,
        "execution_config": ".loopx/config/delegations.json",
    }
    runner.registry.write_text(json.dumps(registry))
    store = ChatSessionStore(runner.root)
    controller = ChatRuntimeController(store=store, codex_bin="codex", registry_path=runner.registry)
    sid = store.create_session(goal_id=runner.goal_id, agent_id="codex",
        channel_id="goal." + runner.goal_id, upstream_thread_id="fixture",
        upstream_mode="chat", adapter_kind="codex_app_server")["session_id"]
    mode = controller.loopx_mode
    # Configure through the production API. Viewing is available before activation.
    mode.apply(sid, {"operation": "configure", "settings": {"agent_id": "lead", "token_budget": 1000}},
               work_dir=repo, objective="Inspect existing evidence")
    before = store.load_session(sid)
    def read(body=None):
        return mode.apply(sid, body or {"operation": "read", "operation_id": "analysis-1"},
                          work_dir=repo, objective="Inspect existing evidence")
    observed = read()
    cli = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(runner.registry),
        "--runtime-root", str(runner.root), "delegation", "read", "--goal-id", runner.goal_id,
        "--agent-id", "lead", "--execution-config", str(config), "--operation-id", "analysis-1"],
        capture_output=True, text=True, check=True)
    cli_result = json.loads(cli.stdout)
    assert observed["ok"] and cli_result["ok"]
    assert observed["status"] == cli_result["status"] == "accepted"
    # The worker lock may release between observations; compare durable evidence,
    # not momentary liveness sampled at different times.
    for key in ("operation_id", "request_id", "agent_id", "todo_id", "artifacts"):
        assert observed[key] == cli_result[key] == accepted[key]
    assert observed["artifacts"][0]["text"] == (root / "analyst/initial/output.json").read_text()
    assert store.load_session(sid) == before
    assert (root / "analyst/initial/host-invocations").read_text() == "1"
    for body in [
        {"operation": "read"}, {"operation": "read", "operation_id": ["analysis-1"]},
        {"operation": "read", "operation_id": "../analysis-1"},
        {"operation": "read", "operation_id": "analysis-1", "agent_id": "reviewer"},
        {"operation": "read", "operation_id": "analysis-1", "path": "output.json"},
        {"operation": "read", "operation_id": "unknown"},
    ]:
        with pytest.raises(ValueError):
            read(body)
    # A once-accepted file cannot be replayed after mutation.
    (root / "analyst/initial/output.json").write_text("{}")
    with pytest.raises(ValueError, match="acceptance rejected"):
        read()
    # A revoked binding clears access even if its journal still exists.
    config.write_text(json.dumps({"schema_version": "loopx_local_delegation_v0", "bindings": []}))
    with pytest.raises(ValueError, match="bindings changed"):
        read()
    assert (root / "analyst/initial/host-invocations").read_text() == "1"
