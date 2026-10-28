"""A confirmed callback retains its original intent when the receiver cannot run.

Exercise the real File/SQLite preflight and native operation session. The local
app-server fixture supplies protocol responses; it is not a model or App proof.
"""

import json
from pathlib import Path

import pytest

from examples import operation_action_fixtures as fixtures
from loopx.chat_action_store import ChatActionStore
from loopx.cli import build_parser
from loopx.control_plane.collaboration.operation_wake import dispatch_confirmed_operation_wake
from loopx.control_plane.turn_driver.codex_cli import load_codex_cli_session
from loopx.control_plane.turn_driver.codex_operation_host import run_codex_operation_host
from loopx.control_plane.turn_driver.managed_operation_host import operation_tool_handler
from loopx.control_plane.turn_driver.lane_fence import turn_lane_singleflight
from loopx.file_lock import lock_holder_path
from test_codex_operation_host import FAKE_SERVER
from test_local_delegation import service, wait  # noqa: F401
from test_loopx_turn_codex_cli import _request


def confirmed_callback(delegation):
    root, runner = delegation
    executable = root / "fixture-codex"
    starts = root / "native-starts"
    executable.write_text(FAKE_SERVER.replace('thread = "owned-app-server-thread"',
        "from pathlib import Path\n"
        f"counter = Path({str(starts)!r})\n"
        "counter.write_text(str(int(counter.read_text()) + 1 if counter.exists() else 1))\n"
        'thread = "owned-app-server-thread"'))
    executable.chmod(0o700)
    config = json.loads(runner.config.read_text())
    binding = config["bindings"][0]
    binding["host_args"] = ["--host", "codex-cli", "--codex-bin", str(executable),
        "--codex-operation-tools", "--codex-model", "test-model", "--codex-reasoning-effort", "xhigh"]
    runner.config.write_text(json.dumps(config))
    selected = build_parser().parse_args(["turn", "run-once", "--goal-id", runner.goal_id,
        "--agent-id", binding["agent_id"], "--todo-id", binding["todo_id"],
        *runner._execution_arguments(binding, "preparation")])
    request = _request()
    request["turn_envelope"].update(goal_id=runner.goal_id, agent_id=binding["agent_id"])
    request["turn_envelope"]["action"]["selected_todo"]["todo_id"] = binding["todo_id"]
    run_codex_operation_host(request, runtime_root=runner.root, registry_path=runner.registry,
        project=root / "analyst/initial", codex_bin=str(executable), model="test-model",
        reasoning_effort="xhigh", mcp_server=selected.codex_mcp_server_json, timeout_seconds=5)
    lineage = {"goal_id": runner.goal_id, "agent_id": binding["agent_id"], "todo_id": binding["todo_id"]}
    session = load_codex_cli_session(runner.root, lineage=lineage)
    handler = operation_tool_handler(runtime_root=runner.root, registry_path=runner.registry,
        lineage=lineage, session_id=session["session_id"], profile_digest=session["operation_profile_digest"],
        model="test-model", reasoning_effort="xhigh")
    intent = fixtures.request(goal_id=runner.goal_id,
        payload={"schema_version": "qualification_v0", "marker": "synthetic"})
    terms = intent["normalized_parameters"]
    terms.pop("executor")
    terms.update(agent_id=binding["agent_id"], domain="qualification",
        operation_kind="qualification.observe", operation_schema="qualification_v0")
    terms["projection"].update(simulated=False, title="Synthetic callback qualification")
    prepared = handler("loopx_operation", {"action": "prepare", "request": intent},
        {"thread_id": session["session_id"], "host_turn_id": "preparing-turn"})
    assert prepared["ok"], prepared
    store = ChatActionStore(runner.root / "chat/actions")
    proposal = prepared["proposal"]
    delivered = store.record_operation_delivery(proposal["proposal_id"], delivery=fixtures.delivery(proposal))
    confirmed = store.decide_operation(proposal["proposal_id"], decision="confirm",
        confirmation=fixtures.confirmation(delivered))
    wake = {"registry_path": str(runner.registry), "goal_id": runner.goal_id,
        "requester_agent_id": runner.agent_id, "project": str(root),
        "execution_config": "delegations.json", "binding_id": binding["id"]}
    return runner, store, confirmed, wake, starts


@pytest.mark.parametrize("lane", ["live", "foreign_host", "unreadable"])
def test_unavailable_callback_retains_confirmation_then_starts_original_once(service, monkeypatch, lane):  # noqa: F811
    runner, store, proposal, wake, starts = confirmed_callback(service)
    original = store.load(proposal["proposal_id"])
    journals = list((runner.root / "goals" / runner.goal_id / "turns").glob("*.json"))

    def dispatch():
        return dispatch_confirmed_operation_wake(proposal, runtime_root=runner.root, configuration=wake)

    submitted = []
    with monkeypatch.context() as no_worker, turn_lane_singleflight(runtime_root=runner.root, goal_id=runner.goal_id,
            plan={"turn_envelope": {"agent_id": "analyst"}}) as held:
        no_worker.setattr(type(runner), "_spawn", lambda self, operation, **kwargs: submitted.append(operation))
        holder = lock_holder_path(runner._lane_target(runner.binding("analysis")))
        if lane != "live":
            record = json.loads(holder.read_text())
            record["host"] = "different-fixture-host"
            holder.write_text(json.dumps(record) if lane == "foreign_host" else "{unreadable")
        retained = holder.read_bytes()
        for _ in range(2):
            result = dispatch()
            assert result["state"] == "blocked", result
            assert result["preflight"]["turn_lane"]["state"] == lane
            assert not result["native_start_verified"] and not result["execution_allowed"]
        assert held is not None and holder.read_bytes() == retained
        assert submitted == []
        assert store.load(proposal["proposal_id"]) == original
        assert not list((runner.root / "collaboration").rglob("operation-wake-*.json"))
        assert list((runner.root / "goals" / runner.goal_id / "turns").glob("*.json")) == journals
        assert starts.read_text() == "1"  # Only the original preparation session.

    result = dispatch()
    assert result["state"] == "delegation_requested", result
    completed = wait(runner, result["operation_id"])
    stored = store.load(proposal["proposal_id"])
    assert starts.read_text() == "2"
    assert stored["operation"]["host_start"] is not None, completed
    assert stored["operation"]["confirmation"] == original["operation"]["confirmation"]
    assert dispatch()["state"] == "existing_delegation"
    assert starts.read_text() == "2"
    # The fixture waits; native start is not a verified domain outcome.
    assert stored["operation"]["outcome"] is None


@pytest.mark.parametrize("fault", ["workspace_missing", "grant_revoked_during_preview"])
def test_callback_preflight_refusal_or_late_revocation_has_no_worker(service, monkeypatch, fault):  # noqa: F811
    runner, store, proposal, wake, starts = confirmed_callback(service)
    original = store.load(proposal["proposal_id"])
    if fault == "workspace_missing":
        binding = runner.binding("analysis")
        workspace = Path(binding["workspace"])
        workspace.rename(workspace.with_name("unavailable-workspace"))
    else:
        inspect = type(runner).inspect

        def revoke_after_preview(self, binding_id):
            result = inspect(self, binding_id)
            config = json.loads(self.config.read_text())
            config["bindings"][0]["requesters"] = []
            self.config.write_text(json.dumps(config))
            return result

        monkeypatch.setattr(type(runner), "inspect", revoke_after_preview)
    submitted = []
    monkeypatch.setattr(type(runner), "_spawn", lambda self, operation, **kwargs: submitted.append(operation))
    result = dispatch_confirmed_operation_wake(proposal, runtime_root=runner.root, configuration=wake)
    assert result["state"] == "blocked", result
    if fault == "workspace_missing":
        assert result["preflight"]["state"] == "workspace_unavailable"
    assert submitted == [] and starts.read_text() == "1"
    assert store.load(proposal["proposal_id"]) == original
    assert not list((runner.root / "collaboration").rglob("operation-wake-*.json"))
