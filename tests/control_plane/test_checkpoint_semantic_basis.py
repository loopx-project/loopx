"""Original work reads survive upkeep; current execution authority remains fenced."""
from __future__ import annotations

import json

import pytest

from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.goals import checkpoint_context_io as context_io
from loopx.control_plane.quota.settlement import SettlementIdentity
from tests.control_plane.test_default_result_freshness import _default_fixture
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, TODO_ID, TURN_ID, _run_cli, _spend_run_count,
)


@pytest.fixture(params=["file", "sqlite"])
def admitted(request, tmp_path, monkeypatch):
    project, runtime, registry, state = _default_fixture(
        request.param, tmp_path, monkeypatch, handoff_mode="hard_lease")
    task = ("--goal-id", GOAL_ID, "--todo-id", TODO_ID)
    binding = (*task, "--agent-id", AGENT_ID, "--turn-instance-id", TURN_ID)

    def cli(*args):
        return _run_cli(registry, runtime, *args, cwd=project)

    rc, acquired = cli("task-lease", "acquire", *task, "--owner", AGENT_ID,
        "--idempotency-key", "original-execution", "--ttl-seconds", "1800")
    assert rc == 0 and acquired["acquired"], acquired
    rc, guard = cli("quota", "should-run", "--codex-app", *binding)
    assert rc == 0, guard
    context = guard["delivery_result_context"]
    identity = SettlementIdentity(goal_id=GOAL_ID, agent_id=AGENT_ID, todo_id=TODO_ID, turn_instance_id=TURN_ID)
    receipt = context_io._receipt_path(runtime, identity, "delivery_result")
    index = runtime / "goals" / GOAL_ID / "runs/index.jsonl"

    def snapshot():
        return (read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID),
                index.read_bytes() if index.exists() else None, _spend_run_count(runtime))

    def complete(version, key="original-execution"):
        return cli("todo", "complete", *binding, "--delivery-read-context", context["read_context_id"],
            "--task-lease-idempotency-key", key, "--task-lease-expected-version", str(version),
            "--evidence", "evidence:checked-original-output")

    return cli, task, binding, acquired["lease"], context, receipt, state, registry, runtime, snapshot, complete


@pytest.mark.parametrize("upkeep", ["renew", "display"])
def test_original_read_commits_after_upkeep_with_current_proof(admitted, upkeep):
    cli, task, binding, lease, context, receipt, _, registry, runtime, _, complete = admitted
    if upkeep == "renew":
        rc, renewed = cli("task-lease", "renew", *task, "--owner", AGENT_ID,
            "--idempotency-key", lease["idempotency_key"], "--expected-version", str(lease["version"]),
            "--ttl-seconds", "2400")
        assert rc == 0, renewed
        current = renewed["lease"]
        for field in ("owner", "idempotency_key", "lease_epoch", "write_repository", "write_scopes"):
            assert current.get(field) == lease.get(field)
        assert current["version"] == lease["version"] + 1
    else:
        payload = json.loads(registry.read_text(encoding="utf-8"))
        payload["goals"][0]["display_name"] = "A different synthetic label"
        registry.write_text(json.dumps(payload), encoding="utf-8")
        current = lease
    # No second work read or candidate judgment. Native completion receives the
    # current lease proof and checks the original, still-current semantic basis.
    rc, saved = complete(current["version"])
    assert rc == 0 and saved["completed"], json.dumps(saved, indent=2)
    persisted = json.loads(receipt.read_text(encoding="utf-8"))
    assert persisted["schema_version"] == "checkpoint_read_context_v3"
    assert persisted["read_context_id"] == context["read_context_id"]
    rc, replay = complete(current["version"])
    assert rc == 0 and replay["idempotent_replay"], replay
    assert _spend_run_count(runtime) == 0
    # The new strict observation reader must recognize the new post-result
    # direction receipt after business lease retirement.
    rc, direction = cli("checkpoint-context", *binding, "--purpose", "first_delivery")
    assert rc == 0 and direction["basis"]["todo"]["status"] == "done", direction
    rc, observed = cli("status", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--limit", "1")
    assert rc == 0 and not observed["contract_errors"], observed
    assert "checkpoint_receipt_invalid" not in json.dumps(observed)


def test_unchanged_semantics_cannot_reuse_old_proof_version(admitted):
    cli, task, _, lease, _, _, _, _, _, snapshot, complete = admitted
    rc, renewed = cli("task-lease", "renew", *task, "--owner", AGENT_ID,
        "--idempotency-key", lease["idempotency_key"], "--expected-version", str(lease["version"]),
        "--ttl-seconds", "2400")
    assert rc == 0, renewed
    before = snapshot()
    rc, rejected = complete(lease["version"])
    assert rc == 1 and rejected["error_code"] == "version_mismatch", rejected
    assert snapshot() == before
    rc, accepted = complete(renewed["lease"]["version"])
    assert rc == 0 and accepted["completed"], accepted


@pytest.mark.parametrize("change", ["goal", "release", "registration", "epoch"])
def test_semantic_or_authority_change_rejects_without_effects(admitted, change):
    cli, task, _, lease, _, _, state, registry, runtime, snapshot, complete = admitted
    if change == "goal":
        state.write_text(state.read_text(encoding="utf-8") +
            "\n## Additional acceptance\n\nVerify the additional synthetic output.\n", encoding="utf-8")
    elif change in ("release", "epoch"):
        rc, released = cli("task-lease", "release", *task, "--owner", AGENT_ID,
            "--idempotency-key", lease["idempotency_key"], "--expected-version", str(lease["version"]))
        assert rc == 0 and released["lease"]["status"] == "released", released
        assert released["lease"]["version"] == lease["version"]
        if change == "epoch":
            rc, reacquired = cli("task-lease", "acquire", *task, "--owner", AGENT_ID,
                "--idempotency-key", "next-execution", "--expected-version", str(lease["version"]),
                "--ttl-seconds", "1800")
            assert rc == 0 and reacquired["acquired"], reacquired
            assert reacquired["lease"]["lease_epoch"] > lease["lease_epoch"]
    else:
        payload = json.loads(registry.read_text(encoding="utf-8"))
        payload["goals"][0]["coordination"]["registered_agents"] = []
        registry.write_text(json.dumps(payload), encoding="utf-8")
    before = snapshot()
    rc, rejected = (complete(reacquired["lease"]["version"], "next-execution")
                    if change == "epoch" else complete(lease["version"]))
    assert rc == 1, rejected
    if change != "registration":
        assert rejected["error_code"] == "checkpoint_read_context_stale", rejected
        assert ("goal" if change == "goal" else "execution_lease") in rejected["checkpoint_read_context"]["changed_components"]
    assert snapshot() == before
    assert _spend_run_count(runtime) == 0
