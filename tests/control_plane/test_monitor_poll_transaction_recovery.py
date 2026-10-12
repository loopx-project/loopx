"""Legacy Monitor poll recovery at the public CLI boundary."""

from __future__ import annotations

import json
import base64
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from loopx.control_plane.testing.canary_harness import (
    read_run_index,
    write_fixture_registry,
)
from loopx.control_plane.coordination.legacy_writer_fence import (
    legacy_coordination_writer_fence_path,
)
from loopx.control_plane.coordination.local_authority import (
    read_canonical_todos_if_promoted,
)
from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)
from loopx.control_plane.coordination.local_authority_shadow_adapter import (
    read_local_authority_shadow,
)
from loopx.control_plane.effect_runtime import EffectRuntimeRejected, effect_runtime_result
from loopx.todos import add_goal_todo, list_goal_todos

from canonical_authority_fixture import (
    initialize_canonical_authority,
    isolate_sqlite_runtime,
)


GOAL = "synthetic-monitor-recovery"
AGENT = "codex-quality-qualification"
MONITOR_TARGET = "synthetic-target"
AGENT_TASK = "Validate observed synthetic head"
USER_TASK = "Review observed synthetic head"
REPO = Path(__file__).resolve().parents[2]

# A fresh CLI process dies at the one state-file install boundary. The process
# exits without Python cleanup, as a real interrupted writer would.
_FAULT_WRAPPER = """
import os, runpy, sys
original_replace = os.replace
def interrupted_replace(source, target, *args, **kwargs):
    if str(target).endswith('/ACTIVE_GOAL_STATE.md'):
        if os.environ['MONITOR_FAULT_PHASE'] == 'before':
            os._exit(83)
        original_replace(source, target, *args, **kwargs)
        os._exit(83)
    return original_replace(source, target, *args, **kwargs)
os.replace = interrupted_replace
sys.argv = ['loopx.cli', *sys.argv[1:]]
runpy.run_module('loopx.cli', run_name='__main__')
"""
_QUOTA_COMMIT_FAULT_WRAPPER = """
import os, runpy, sys
import loopx.control_plane.quota.monitor_poll as monitor
original = monitor._native_result
def interrupted_commit(request):
    if request.get('phase') == 'commit' and request.get('provider_receipt'):
        os._exit(73)
    return original(request)
monitor._native_result = interrupted_commit
sys.argv = ['loopx.cli', *sys.argv[1:]]
runpy.run_module('loopx.cli', run_name='__main__')
"""


def _cli(
    registry: Path, runtime: Path, *args: str, fault: str | None = None,
) -> tuple[int, dict | None]:
    if fault == "quota_commit":
        launcher = [sys.executable, "-c", _QUOTA_COMMIT_FAULT_WRAPPER]
    elif fault:
        launcher = [sys.executable, "-c", _FAULT_WRAPPER]
    else:
        launcher = [sys.executable, "-m", "loopx.cli"]
    result = subprocess.run(
        [*launcher, "--registry", str(registry), "--format", "json",
         "--runtime-root", str(runtime), *args],
        cwd=REPO,
        env={**os.environ, "LOOPX_USAGE_PING": "0",
             "MONITOR_FAULT_PHASE": fault or ""},
        capture_output=True, text=True, check=False, timeout=60,
    )
    return result.returncode, json.loads(result.stdout) if result.stdout.strip() else None


def _ok(registry: Path, runtime: Path, *args: str) -> dict:
    code, result = _cli(registry, runtime, *args)
    assert code == 0, result
    assert isinstance(result, dict)
    return result


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path, str, tuple[str, ...]]:
    project, runtime = tmp_path / "project", tmp_path / "runtime"
    state = project / ".codex" / "goals" / GOAL / "ACTIVE_GOAL_STATE.md"
    registry = project / ".loopx" / "registry.json"
    state.parent.mkdir(parents=True)
    state.write_text("---\nstatus: active\n---\n\n# Active Goal State\n\n## Agent Todo\n",
                     encoding="utf-8")
    write_fixture_registry(
        project=project, runtime_root=runtime, registry_path=registry,
        goal_id=GOAL, domain="loopx-platform",
        adapter_kind="harness_self_improvement", registered_agents=[AGENT],
        quota_allowed_slots=None,
    )
    monitor = add_goal_todo(
        registry_path=registry, goal_id=GOAL, role="agent",
        text="Observe public synthetic target", task_class="continuous_monitor",
        claimed_by=AGENT,
        monitor_metadata={"target_key": MONITOR_TARGET, "cadence": "1h",
                          "next_due_at": "2000-01-01T00:00:00+00:00",
                          "watch_only": "true"},
    )
    turn = "synthetic-monitor-turn-1"
    _ok(registry, runtime, "quota", "should-run", "--goal-id", GOAL,
        "--agent-id", AGENT, "--runtime-profile", "generic_cli",
        "--turn-instance-id", turn)
    poll = (
        "quota", "monitor-poll", "--goal-id", GOAL, "--agent-id", AGENT,
        "--runtime-profile", "generic_cli", "--turn-instance-id", turn,
        "--todo-id", monitor["todo_id"], "--target-key", MONITOR_TARGET,
        "--result-hash", "synthetic-head-1", "--material-change",
        "--next-agent-todo", AGENT_TASK, "--next-action-kind", "validate",
        "--next-user-todo", USER_TASK, "--next-user-task-class", "user_action",
        "--execute",
    )
    return registry, runtime, state, monitor["todo_id"], poll


def _successors(registry: Path, runtime: Path) -> dict[str, dict]:
    todos = _ok(registry, runtime, "todo", "list", "--goal-id", GOAL)["todos"]
    return {todo["text"]: todo for todo in todos
            if todo["text"] in (AGENT_TASK, USER_TASK)}


def _poll_events(runtime: Path) -> list[dict]:
    return [row for row in read_run_index(runtime, GOAL)
            if row.get("classification") == "quota_monitor_poll"]


def _quota_receipt(runtime: Path) -> Path:
    directory = (runtime / "goals" / GOAL / "runs" / ".transactions" /
                 "quota-monitor-poll")
    (receipt,) = directory.glob("*.json")
    return receipt


def _business_receipt(state: Path) -> dict:
    match = re.search(r"<!-- loopx:monitor-batch:[a-f0-9]+ ([A-Za-z0-9+/=]+) -->",
                      state.read_text(encoding="utf-8"))
    assert match is not None
    return json.loads(base64.b64decode(match[1]))


def _engage_fence(runtime: Path) -> None:
    fence = legacy_coordination_writer_fence_path(runtime_root=runtime, goal_id=GOAL)
    fence.parent.mkdir(parents=True, exist_ok=True)
    fence.write_text(json.dumps({
        "schema_version": "loopx_legacy_coordination_writer_fence_v0",
        "state": "engaged", "goal_id": GOAL, "fence_id": "synthetic-fence",
        "source_version": "synthetic-source",
        "source_projection_sha256": "a" * 64,
        "expected_shadow_provider_revision": "file:1:aaaaaaaaaaaaaaaaaaaaaaaa",
    }), encoding="utf-8")


@pytest.mark.parametrize("owner", [AGENT, "registered-builder", "unknown-builder"])
def test_public_monitor_successor_claim_checks_registered_peer_before_writing(
    tmp_path: Path, owner: str,
) -> None:
    registry, runtime, state, _, poll = _fixture(tmp_path)
    data = json.loads(registry.read_text(encoding="utf-8"))
    data["goals"][0]["coordination"]["registered_agents"].append("registered-builder")
    registry.write_text(json.dumps(data), encoding="utf-8")
    before_state = state.read_bytes()
    before_index = read_run_index(runtime, GOAL)
    transaction_dir = runtime / "goals" / GOAL / "runs" / ".transactions" / "quota-monitor-poll"
    before_receipts = {path.name: path.read_bytes() for path in transaction_dir.glob("*.json")}
    code, response = _cli(registry, runtime, *poll[:-1], "--next-claimed-by", owner, "--execute")
    assert response is not None
    if owner == "unknown-builder":
        assert code != 0
        assert "is not registered" in response["reason"]
        assert "quota_unexpected_collection_error" not in json.dumps(response)
        assert state.read_bytes() == before_state
        assert read_run_index(runtime, GOAL) == before_index
        assert {path.name: path.read_bytes() for path in transaction_dir.glob("*.json")} == before_receipts
        assert _poll_events(runtime) == []
    else:
        assert code == 0, json.dumps(response, indent=2)
        successor = response["todo_writeback"]["next_todos"][0]
        assert successor["claimed_by"] == owner
        assert _successors(registry, runtime)[AGENT_TASK]["claimed_by"] == owner
        assert len(_poll_events(runtime)) == 1
        if owner == "registered-builder":
            saved_state = state.read_bytes()
            saved_events = read_run_index(runtime, GOAL)
            data["goals"][0]["coordination"]["registered_agents"].remove(owner)
            registry.write_text(json.dumps(data), encoding="utf-8")
            replay_code, replay = _cli(registry, runtime, *poll[:-1], "--next-claimed-by", owner, "--execute")
            assert replay_code == 0, replay
            assert state.read_bytes() == saved_state
            assert read_run_index(runtime, GOAL) == saved_events


def test_monitor_batch_handler_classifies_expected_input_errors() -> None:
    request = {
        "schema_version": "loopx_monitor_batch_plan_request_v0", "legacy_batch_version": 1,
        "goal_id": "synthetic-monitor", "operation_id": "synthetic-effect",
        "actor_agent_id": "observer", "registered_agents": ["observer"],
        "dry_run": False, "observation": {
            "todo_id": "todo_watch", "target_key": "watch", "generated_at": "2026-09-01T00:00:00Z",
            "result_hash": "head", "material_change": True,
        },
        "intent": {"next_agent_todo": "Validate head", "next_action_kind": "validate",
                   "next_claimed_by": "unknown-builder"},
        "todos": [{"todo_id": "todo_watch", "role": "agent", "status": "open",
                   "done": False, "archive_state": "active", "text": "Observe head",
                   "task_class": "continuous_monitor", "target_key": "watch", "cadence": "1h"}],
    }
    with pytest.raises(EffectRuntimeRejected, match="claim owner is not registered") as rejected:
        effect_runtime_result("scheduler.monitor_batch.plan", request)
    assert rejected.value.error_kind == "request_rejected"
    assert rejected.value.diagnostic_code == "invalid_request"


@pytest.mark.parametrize("fault", ["before", "after"])
def test_interrupted_legacy_poll_retries_one_complete_batch(
    tmp_path: Path, fault: str,
) -> None:
    registry, runtime, state, monitor_id, poll = _fixture(tmp_path)
    before = state.read_bytes()

    code, result = _cli(registry, runtime, *poll, fault=fault)
    assert (code, result) == (83, None)
    if fault == "before":
        assert state.read_bytes() == before
        assert _successors(registry, runtime) == {}
    else:
        assert set(_successors(registry, runtime)) == {AGENT_TASK, USER_TASK}
    assert _poll_events(runtime) == []

    recovered = _ok(registry, runtime, *poll)
    successors = _successors(registry, runtime)
    assert set(successors) == {AGENT_TASK, USER_TASK}
    assert all(item["unblocks_todo_id"] == monitor_id for item in successors.values()
               if item["role"] == "agent")
    ids = {item["todo_id"] for item in successors.values()}
    assert set(recovered["successor_todo_ids"]) == ids
    assert len(_poll_events(runtime)) == 1

    state_after, index_after = state.read_bytes(), read_run_index(runtime, GOAL)
    replay = _ok(registry, runtime, *poll)
    assert set(replay["successor_todo_ids"]) == ids
    assert state.read_bytes() == state_after
    assert read_run_index(runtime, GOAL) == index_after


def test_identity_less_legacy_pending_cannot_guess_successors(tmp_path: Path) -> None:
    registry, runtime, state, _monitor_id, poll = _fixture(tmp_path)
    before = state.read_bytes()
    code, result = _cli(registry, runtime, *poll, fault="before")
    assert (code, result) == (83, None)
    pending_path = _quota_receipt(runtime)
    pending = json.loads(pending_path.read_text(encoding="utf-8"))
    assert pending["status"] == "provider_pending"
    # This represents an already persisted older pending admission. Its plan
    # did not freeze the complete legacy batch identity before any business
    # write, so recovery must preserve it for reconciliation.
    pending["provider_plan"].pop("legacy_batch_version", None)
    pending_path.write_text(json.dumps(pending), encoding="utf-8")

    code, rejected = _cli(registry, runtime, *poll)
    assert code != 0, rejected
    assert "reconciliation" in str(rejected.get("reason")), rejected
    assert state.read_bytes() == before
    assert _successors(registry, runtime) == {}
    assert _poll_events(runtime) == []
    assert _quota_receipt(runtime).exists()


def test_malformed_business_receipt_is_not_treated_as_absent(tmp_path: Path) -> None:
    registry, runtime, state, _monitor_id, poll = _fixture(tmp_path)
    assert _cli(registry, runtime, *poll, fault="after") == (83, None)
    # A present JSON null receipt is corrupt history, not an unstarted effect.
    text = re.sub(r"(<!-- loopx:monitor-batch:[a-f0-9]+ )[^ ]+ -->",
                  r"\g<1>bnVsbA== -->", state.read_text(encoding="utf-8"))
    state.write_text(text, encoding="utf-8")
    code, rejected = _cli(registry, runtime, *poll)
    assert code != 0 and "receipt must be an object" in rejected["reason"]
    assert state.read_text(encoding="utf-8") == text
    assert _poll_events(runtime) == []


def test_enabled_shadow_ordinary_retry_recovers_one_prepared_batch(
    tmp_path: Path,
) -> None:
    registry, runtime, state, _monitor_id, poll = _fixture(tmp_path)
    config = json.loads(registry.read_text(encoding="utf-8"))
    config["goals"][0]["coordination"]["runtime_shadow"] = {
        "enabled": True,
        "schema_version": "loopx_coordination_runtime_shadow_config_v0",
        "provider": "file_v0",
    }
    registry.write_text(json.dumps(config), encoding="utf-8")
    _ok(registry, runtime, "coordination-shadow", "bootstrap", "--goal-id", GOAL,
        "--execute")

    assert _cli(registry, runtime, *poll, fault="after") == (83, None)
    outbox = runtime / "authority-shadow" / "outbox" / GOAL / "todos"
    assert len(list(outbox.glob("*.prepared.json"))) == 1
    assert list(outbox.glob("*.committed.json")) == []
    assert "coordination_runtime_shadow" not in _business_receipt(state)["writeback"]

    before = read_local_authority_shadow(runtime_root=runtime, goal_id=GOAL,
                                        scan_limit=20)
    assert {todo["text"] for todo in before["head"]["todos"]} == {
        "Observe public synthetic target"}
    state_after = state.read_bytes()
    retry = _ok(registry, runtime, *poll)
    assert {todo["todo"] for todo in retry["todo_writeback"]["next_todos"]} == {
        AGENT_TASK, USER_TASK}
    assert state.read_bytes() == state_after
    view = read_local_authority_shadow(runtime_root=runtime, goal_id=GOAL,
                                      scan_limit=20)
    assert {todo["text"] for todo in view["head"]["todos"]} >= {
        AGENT_TASK, USER_TASK}
    assert list(outbox.glob("*.prepared.json")) == []


def test_committed_receipt_without_batch_version_still_replays(tmp_path: Path) -> None:
    registry, runtime, state, _monitor_id, poll = _fixture(tmp_path)
    committed = _ok(registry, runtime, *poll)
    receipt = json.loads(_quota_receipt(runtime).read_text(encoding="utf-8"))
    assert receipt["status"] != "provider_pending"
    assert "legacy_batch_version" not in receipt
    assert "provider_plan" not in receipt
    state_after = state.read_bytes()
    index_after = read_run_index(runtime, GOAL)

    replay = _ok(registry, runtime, *poll)
    assert replay["replayed"] is True
    assert replay["successor_todo_ids"] == committed["successor_todo_ids"]
    original_writeback = dict(committed["todo_writeback"])
    repeated_writeback = dict(_business_receipt(state)["writeback"])
    for payload in (original_writeback, repeated_writeback):
        payload.pop("provider_replayed", None)
        payload.pop("monitor_effect_id", None)
    # One batch capture is reported at the original call. Its post-commit
    # drain receipt cannot be in the atomically installed business receipt.
    original_writeback.pop("coordination_runtime_shadow", None)
    for result in [original_writeback["todo_update"], *original_writeback["next_todos"]]:
        result.pop("coordination_runtime_shadow", None)
    assert repeated_writeback == original_writeback
    assert state.read_bytes() == state_after
    assert read_run_index(runtime, GOAL) == index_after


@pytest.mark.parametrize("dry_run", [True, False])
@pytest.mark.parametrize("user_class", ["user_action", "user_gate"])
def test_public_monitor_writeback_retains_todo_result_contract(
    tmp_path: Path, dry_run: bool, user_class: str,
) -> None:
    registry, runtime, state, monitor_id, poll = _fixture(tmp_path)
    before = state.read_bytes()
    args = list(poll)
    args[args.index("user_action")] = user_class
    if dry_run:
        args.remove("--execute")
    writeback = _ok(registry, runtime, *args)["todo_writeback"]
    update = writeback["todo_update"]
    assert update["dry_run"] is dry_run
    assert update["changed"] is True
    assert update["mutation_authority"]["actor_agent_id"] == AGENT
    assert update["agent_id"] == AGENT
    assert update["handoff_mode"] == "legacy"
    assert update["role"] == "agent" and update["section"] == "Agent Todo"
    assert update["status_changed"] is False
    assert update["text_changed"] is False
    assert update["metadata_updated"] is True
    assert update["required_capabilities"] == []
    assert update["state_file"] == str(state)
    assert update["project"] == str(state.parents[3])
    assert update["updated_at"] == writeback["last_checked_at"]
    assert len(writeback["next_todos"]) == 2
    agent, user = writeback["next_todos"]
    for item in (agent, user):
        assert item["ok"] is True and item["dry_run"] is dry_run
        assert item["added"] is True and item["already_exists"] is False
        assert item["metadata_updated"] is False
        assert item["status_changed"] is False
        assert item["goal_id"] == GOAL
        assert item["required_write_scopes"] == []
        assert item["required_capabilities"] == []
        assert item["excluded_agents"] == []
        assert item["state_file"] == str(state)
        assert item["project"] == str(state.parents[3])
        assert item["handoff_mode"] == "legacy"
        assert item["coordination_runtime_shadow"] == update["coordination_runtime_shadow"]
    assert agent["unblocks_todo_id"] == monitor_id
    assert agent["agent_id"] is None
    assert user["agent_id"] == AGENT
    assert user["task_class"] == user_class
    assert user["action_kind"] == ("gate" if user_class == "user_gate" else None)
    assert user["global_gate"] is None and user["goal_bound"] is None
    if user_class == "user_gate":
        assert user["blocks_agent"] == AGENT
        assert user["unblocks_todo_id"] == monitor_id
    else:
        assert user["blocks_agent"] is None
        assert user["unblocks_todo_id"] is None
    if dry_run:
        packet = update["local_state_write_correctness"]
        assert packet["write_intent"]["target_refs"]["todo_ids"] == [
            monitor_id, agent["todo_id"], user["todo_id"]]
        assert packet["write_intent"]["expected_revision"]["kind"] == "active_state_revision"
        assert packet["apply_result"]["status"] == "preview_only"
        assert agent["local_state_write_correctness"] == packet
        assert user["local_state_write_correctness"] == packet
        assert state.read_bytes() == before


@pytest.mark.parametrize("conflict", [False, True])
def test_existing_successor_semantics_are_checked_before_batch_write(
    tmp_path: Path, conflict: bool,
) -> None:
    registry, runtime, state, monitor_id, poll = _fixture(tmp_path)
    preview = _ok(registry, runtime, *poll[:-1])["todo_writeback"]
    route = preview["next_todos"][0]
    existing = add_goal_todo(
        registry_path=registry, goal_id=GOAL, role="agent", text=AGENT_TASK,
        task_class="advancement_task",
        action_kind="implement" if conflict else "validate",
        continuation_policy=route["continuation_policy"],
        unblocks_todo_id=monitor_id,
        monitor_metadata={"target_key": route["target_key"]},
    )
    before = state.read_bytes()
    if conflict:
        code, rejected = _cli(registry, runtime, *poll)
        assert code != 0 and "different action_kind" in rejected["reason"]
        assert state.read_bytes() == before
        assert _poll_events(runtime) == []
    else:
        writeback = _ok(registry, runtime, *poll)["todo_writeback"]
        agent = writeback["next_todos"][0]
        assert agent["todo_id"] == existing["todo_id"]
        assert agent["added"] is False and agent["already_exists"] is True
        assert agent["metadata_updated"] is False
        assert len(_poll_events(runtime)) == 1


def test_pending_legacy_receipt_replays_after_canonical_cutover(tmp_path: Path) -> None:
    registry, runtime, state, _monitor_id, poll = _fixture(tmp_path)
    code, result = _cli(registry, runtime, *poll, fault="after")
    assert (code, result) == (83, None)
    original_ids = {todo["todo_id"] for todo in _successors(registry, runtime).values()}
    state_after = state.read_bytes()
    assert _poll_events(runtime) == []
    pending_path = _quota_receipt(runtime)
    pending = json.loads(pending_path.read_text(encoding="utf-8"))
    pending["provider_plan"].pop("legacy_batch_version", None)
    pending_path.write_text(json.dumps(pending), encoding="utf-8")
    items = list_goal_todos(registry_path=registry, goal_id=GOAL)["todos"]
    projection = build_todo_runtime_shadow_projection(
        goal_id=GOAL, todos=items, handoff_mode="soft_claim")
    initialize_canonical_authority(runtime, GOAL, projection, state_path=state)
    before_provider = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL)

    replay = _ok(registry, runtime, *poll)
    assert set(replay["successor_todo_ids"]) == original_ids
    assert state.read_bytes() == state_after
    assert len(_poll_events(runtime)) == 1
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL) == before_provider
    index_after = read_run_index(runtime, GOAL)
    repeated = _ok(registry, runtime, *poll)
    assert repeated["replayed"] is True
    assert set(repeated["successor_todo_ids"]) == original_ids
    assert state.read_bytes() == state_after
    assert read_run_index(runtime, GOAL) == index_after
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL) == before_provider


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_historical_canonical_pending_plan_settles_existing_business_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, state, _monitor_id, poll = _fixture(tmp_path)
    items = list_goal_todos(registry_path=registry, goal_id=GOAL)["todos"]
    projection = build_todo_runtime_shadow_projection(
        goal_id=GOAL, todos=items, handoff_mode="soft_claim")
    initialize_canonical_authority(runtime, GOAL, projection,
                                   state_path=state, provider=provider)
    _ok(registry, runtime, "quota", "should-run", "--goal-id", GOAL,
        "--agent-id", AGENT, "--runtime-profile", "generic_cli",
        "--turn-instance-id", "synthetic-monitor-turn-1")

    code, result = _cli(registry, runtime, *poll, fault="quota_commit")
    assert (code, result) == (73, None)
    committed = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL)
    successor_ids = {todo["todo_id"] for todo in committed["todos"]
                     if todo["text"] in (AGENT_TASK, USER_TASK)}
    assert len(successor_ids) == 2
    assert _poll_events(runtime) == []
    pending_path = _quota_receipt(runtime)
    pending = json.loads(pending_path.read_text(encoding="utf-8"))
    assert pending["status"] == "provider_pending"
    # Historical canonical plans predate the legacy batch version. The
    # already committed native provider receipt remains the recovery source.
    pending["provider_plan"].pop("legacy_batch_version", None)
    pending_path.write_text(json.dumps(pending), encoding="utf-8")

    settled = _ok(registry, runtime, *poll)
    assert set(settled["successor_todo_ids"]) == successor_ids
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL) == committed
    assert len(_poll_events(runtime)) == 1
    index_after = read_run_index(runtime, GOAL)
    repeated = _ok(registry, runtime, *poll)
    assert repeated["replayed"] is True
    assert set(repeated["successor_todo_ids"]) == successor_ids
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL) == committed
    assert read_run_index(runtime, GOAL) == index_after


def test_legacy_writer_fence_rejects_full_monitor_batch(tmp_path: Path) -> None:
    registry, runtime, state, _monitor_id, poll = _fixture(tmp_path)
    before = state.read_bytes()
    _engage_fence(runtime)

    code, rejected = _cli(registry, runtime, *poll)
    assert code != 0, rejected
    assert state.read_bytes() == before
    assert _poll_events(runtime) == []


def test_atomic_successor_reconciles_existing_typed_next_action(tmp_path: Path) -> None:
    registry, runtime, state, _monitor_id, poll = _fixture(tmp_path)
    lower = _ok(registry, runtime, "todo", "add", "--goal-id", GOAL,
        "--role", "agent", "--text", "[P1] Existing lower priority task",
        "--task-class", "advancement_task", "--action-kind", "validate")
    with state.open("a", encoding="utf-8") as handle:
        handle.write("\n## Next Action\n\n- [P1] Existing lower priority task\n"
            "<!-- loopx:next-action schema=loopx_next_action_binding_v0 "
            f"todo_id={lower['todo_id']} -->\n")
    command = list(poll)
    command[command.index(AGENT_TASK)] = "[P0] Validate urgent observed result"
    code, result = _cli(registry, runtime, *command, fault="after")
    assert (code, result) == (83, None)
    successor = next(todo for todo in _ok(registry, runtime, "todo", "list",
        "--goal-id", GOAL)["todos"] if todo.get("unblocks_todo_id") == _monitor_id)
    expected = ("<!-- loopx:next-action schema=loopx_next_action_binding_v0 "
                f"todo_id={successor['todo_id']} -->")
    assert expected in state.read_text(encoding="utf-8")
    committed = state.read_bytes()
    retry = _ok(registry, runtime, *command)
    assert successor["todo_id"] in retry["successor_todo_ids"]
    assert state.read_bytes() == committed


@pytest.mark.parametrize("change", ["rename", "complete", "archive"])
def test_pending_retry_keeps_original_successor_identity_after_public_edit(
    tmp_path: Path, change: str,
) -> None:
    registry, runtime, state, _monitor_id, poll = _fixture(tmp_path)
    code, result = _cli(registry, runtime, *poll, fault="after")
    assert (code, result) == (83, None)
    first = _successors(registry, runtime)
    assert set(first) == {AGENT_TASK, USER_TASK}
    original_ids = {item["todo_id"] for item in first.values()}
    agent_id = first[AGENT_TASK]["todo_id"]

    if change == "rename":
        _ok(registry, runtime, "todo", "update", "--goal-id", GOAL,
            "--todo-id", agent_id, "--role", "agent", "--agent-id", AGENT,
            "--text", "Validate observed synthetic head with revised scope")
    else:
        _ok(registry, runtime, "todo", "complete", "--goal-id", GOAL,
            "--todo-id", agent_id, "--role", "agent", "--agent-id", AGENT,
            "--no-follow-up", "--evidence", "Synthetic validation complete")
        if change == "archive":
            _ok(registry, runtime, "todo", "archive-completed", "--goal-id", GOAL,
                "--role", "agent", "--max-active-done", "0", "--execute")
            archived = _ok(registry, runtime, "todo", "list", "--goal-id", GOAL,
                           "--role", "agent", "--todo-id", agent_id)
            assert archived["todo"]["archive_state"] == "archive"
    edited_state = state.read_bytes()
    assert len(_poll_events(runtime)) == 0

    retry = _ok(registry, runtime, *poll)
    writeback = retry["todo_writeback"]
    assert writeback["todo_update"]["todo"] == "Observe public synthetic target"
    assert [todo["todo"] for todo in writeback["next_todos"]] == [AGENT_TASK, USER_TASK]
    assert all(todo["added"] is True for todo in writeback["next_todos"])
    assert set(retry["successor_todo_ids"]) == original_ids
    assert state.read_bytes() == edited_state
    assert len(_poll_events(runtime)) == 1
    index_after = read_run_index(runtime, GOAL)

    replay = _ok(registry, runtime, *poll)
    receipt = _business_receipt(state)
    assert receipt["writeback"]["next_todos"] == [
        {key: value for key, value in todo.items()
         if key != "coordination_runtime_shadow"}
        for todo in writeback["next_todos"]]
    assert set(replay["successor_todo_ids"]) == original_ids
    assert state.read_bytes() == edited_state
    assert read_run_index(runtime, GOAL) == index_after

    changed_intent = list(poll)
    changed_intent[changed_intent.index(USER_TASK)] = "Request a different review"
    code, conflict = _cli(registry, runtime, *changed_intent)
    assert code != 0, conflict
    assert state.read_bytes() == edited_state
    assert read_run_index(runtime, GOAL) == index_after
