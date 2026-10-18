"""Moving original work must preserve its configured execution budget.

Use populated File/SQLite Goals, the existing settings writer and actual move
confirmation. Quota admission and receipt replay remain with their own owners.
"""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from loopx.control_plane.effect_runtime import restart_effect_runtime
from loopx.control_plane.effect_runtime import EffectRuntimeRemoteError
from loopx.control_plane.goals.planned_transfer_io import read_goal_transfer_host_context
from loopx.control_plane.projects.registry_codec import project_registry_transaction
from loopx.control_plane.quota.spend_commit import replay_quota_spend_by_effect_ref
from loopx.history import collect_history
from loopx.quota import quota_status
from tests.control_plane.test_planned_transfer_cancellation import source as source_fixture
from tests.control_plane.test_planned_transfer_cutover import OPERATION
from tests.control_plane.test_planned_transfer_execution import _prepare, _declare_original_checks
from tests.control_plane.test_planned_transfer_operation import _http
from tests.control_plane.test_planned_transfer_product import _goal_document, _prepared

source = source_fixture


@pytest.mark.parametrize("compute,hours,state", [(0.25, 2, "eligible"), (0, 2, "paused"), (0.01, 1, "throttled")])
def test_configured_budget_and_original_debit_survive_confirmation_and_restart(source, tmp_path, compute, hours, state):
    _goal_document(source)
    source["cli"]("configure-goal", "--goal-id", "report", "--quota-compute", str(compute),
        "--quota-window-hours", str(hours), "--execute")
    original = json.loads(source["registry"].read_text())["goals"][0]
    assert original["quota"] == {"compute": compute, "window_hours": hours}
    task = source["cli"]("todo", "list", "--goal-id", "report", "--todo-id", source["todo"])["todo"]
    before = collect_history(registry_path=source["registry"], runtime_root=source["runtime"], goal_id="report", limit=50)["goals"][0]["quota"]
    assert quota_status({**original, "quota": before}, waiting_on="codex")["state"] == state
    assert before["spent_slots"] == 1
    target, prepared = _prepared(source, tmp_path)
    review = prepared["prepared_cutover"]
    server, thread, call = _http(source)
    try:
        request = {"action": "confirm", "goal_id": "report", "operation_id": OPERATION,
            "preview_sha256": review["preview_sha256"]}
        status, confirmed = call(request)
        assert status == 200 and confirmed["recorded"] is True
        assert call(request)[0] == 200
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()
    restart_effect_runtime(wait_for_exit=True)
    current = json.loads(target["registry"].read_text())["goals"][0]
    assert current["quota"] == original["quota"]
    assert target["cli"]("todo", "list", "--goal-id", "report", "--todo-id", source["todo"])["todo"] == task
    context = target["cli"]("goal-transfer", "context", "--goal-id", "report",
        "--agent-id", "agent-a", "--todo-id", source["todo"])["context"]
    assert context["current_goal"]["quota"] == original["quota"]
    assert context["credentials_adopted"] is False
    after = collect_history(registry_path=target["registry"], runtime_root=target["runtime"], goal_id="report", limit=50)["goals"][0]["quota"]
    for key in ("compute", "window_hours", "slot_minutes", "allowed_slots", "spent_slots", "spend_event_count"):
        assert after[key] == before[key]
    assert quota_status({**current, "quota": after}, waiting_on="codex")["state"] == state
    replay = replay_quota_spend_by_effect_ref(target["runtime"], goal_id="report",
        effect_ref=source["debit"]["effect_id"], agent_id="agent-a", read_only=True, registry_path=target["registry"])
    assert replay["ok"] is True and replay["appended"] is False
    assert collect_history(registry_path=target["registry"], runtime_root=target["runtime"], goal_id="report", limit=50)["goals"][0]["quota"]["spent_slots"] == 1
    assert not (target["runtime"] / "goals/report/runs/index.jsonl").exists()


@pytest.mark.parametrize("compute,refusal", [(0, "turn_todo_selection_unavailable"), (0.01, "turn_todo_selection_deferred")])
def test_confirmation_does_not_dispatch_or_claim_paused_or_exhausted_work(source, tmp_path, monkeypatch, compute, refusal):
    _goal_document(source)
    _declare_original_checks(source)
    source["cli"]("configure-goal", "--goal-id", "report", "--quota-compute", str(compute),
        "--quota-window-hours", "1", "--execute")
    marker = tmp_path / "unexpected-host-call"
    sentinel = tmp_path / "unused-codex"
    sentinel.write_text(f"#!{sys.executable}\nfrom pathlib import Path\nPath({str(marker)!r}).touch()\nraise SystemExit(90)\n")
    sentinel.chmod(0o700)
    monkeypatch.setenv("CODEX_BIN", str(sentinel))
    destination, prepared = _prepare(source, tmp_path)
    server, thread, call = _http(source)
    try:
        status, result = call({"action": "confirm", "goal_id": "report", "operation_id": "move-a",
            "preview_sha256": prepared["prepared_cutover"]["preview_sha256"]})
        assert status == 200 and result["recorded"] is True
        # The native explicit-Task selection refuses this work before producing
        # a TurnEnvelope. Transfer remains recorded; that is not execution.
        assert result["execution"]["ok"] is False
        assert result["execution"]["turn_count"] == 0
        assert result["execution"]["reason_code"] == refusal
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()
    assert not marker.exists()
    registry, runtime = destination / "workspace/.loopx/registry.json", destination / "runtime"
    command = [sys.executable, "-I", "-m", "loopx.cli", "--format", "json", "--registry", str(registry),
        "--runtime-root", str(runtime), "task-lease", "inspect", "--goal-id", "report", "--todo-id", source["todo"]]
    observed = subprocess.run(command, cwd=destination / "agent-workspace", text=True, capture_output=True, check=True)
    lease = json.loads(observed.stdout).get("lease")
    assert lease is None or lease["status"] != "active"
    assert collect_history(registry_path=registry, runtime_root=runtime, goal_id="report", limit=50)["goals"][0]["quota"]["spent_slots"] == 1
    assert not list((runtime / "goals/report/turns").glob("*.json"))


def test_legacy_budget_fields_retain_policy_but_never_replace_the_original_ledger(source, tmp_path):
    _goal_document(source)
    policy = {"window_hours": 2, "slot_minutes": 10, "allowed_slots": 1,
        "spent_slots": 999, "next_eligible_at": "2099-01-01T00:00:00+00:00"}
    with project_registry_transaction(source["registry"], operation="fixture-retained-budget") as transaction:
        registry = transaction.payload_copy()
        registry["goals"][0].update(quota=policy, compute_quota=0.25)
        transaction.commit(registry)
    target, prepared = _prepared(source, tmp_path)
    source["cli"]("goal-transfer", "confirm", "--goal-id", "report", "--operation-id", OPERATION,
        "--expected-preview-sha256", prepared["prepared_cutover"]["preview_sha256"], "--execute")
    current = json.loads(target["registry"].read_text())["goals"][0]
    assert current["quota"] == policy and current["compute_quota"] == 0.25
    actual = collect_history(registry_path=target["registry"], runtime_root=target["runtime"], goal_id="report", limit=50)["goals"][0]["quota"]
    assert actual["compute"] == 0.25 and actual["allowed_slots"] == 1
    assert actual["spent_slots"] == 1  # Cached configuration is not accounting.
    assert actual["next_eligible_at"] == policy["next_eligible_at"]
    assert quota_status({**current, "quota": actual}, waiting_on="codex")["state"] == "throttled"
    # The current reviewed context cannot silently accept an altered budget.
    # Reconfiguration needs its existing Goal owner, not a stale transfer grant.
    with project_registry_transaction(target["registry"], operation="fixture-unreviewed-budget-drift") as transaction:
        registry = transaction.payload_copy()
        registry["goals"][0]["quota"]["allowed_slots"] = 100
        transaction.commit(registry)
    with pytest.raises(EffectRuntimeRemoteError, match="registration no longer matches"):
        read_goal_transfer_host_context(registry_path=target["registry"], goal_id="report", agent_id="agent-a", todo_id=source["todo"])


@pytest.mark.parametrize("quota", [{"compute": 0.25, "credential": "not-a-budget-field"}, {"compute": {"value": 1}}, "invalid"])
def test_unknown_budget_configuration_is_rejected_before_fencing(source, tmp_path, quota):
    _goal_document(source)
    with project_registry_transaction(source["registry"], operation="fixture-unqualified-budget") as transaction:
        registry = transaction.payload_copy()
        registry["goals"][0]["quota"] = quota
        transaction.commit(registry)
    before = source["registry"].read_bytes(), source["fence"].read_bytes()
    destination = tmp_path / "unprepared-budget"
    rejected = source["cli"]("goal-transfer", "prepare-local", "--goal-id", "report", "--operation-id", OPERATION,
        "--destination-root", str(destination), "--execute", expected_code=1)
    assert "budget" in rejected["error"].lower()
    assert before == (source["registry"].read_bytes(), source["fence"].read_bytes())
    assert not destination.exists()
