"""Synthetic room transport, real canonical File/SQLite authority and source CLI."""
import json
import io
import multiprocessing
import os
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from functools import partial
from pathlib import Path
from unittest.mock import patch

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.extensions.lark import goal_channel_work as work
from loopx.extensions.lark.goal_channel_contracts import write_goal_channel_binding

ROOT = Path(__file__).resolve().parents[2]
GOAL = "room-fixture"
TODO = "todo_room_fixture"


class Room:
    """A synthetic Lark protocol responder, never an external room."""
    def __init__(self):
        self.messages = []
        self.fail_send = False
        self.authenticated = True

    def __call__(self, args, cwd, timeout):
        payload = {}
        if "auth" in args:
            payload = {"appId": "cli_room_fixture", "identities": {"bot": {
                "available": self.authenticated, "verified": self.authenticated, "appName": "Fixture Bot"}}}
        elif "+chat-members-list" in args:
            payload = {"data": {"bots": [{"app_id": "cli_room_fixture"}]}}
        elif "+messages-send" in args:
            card = json.loads(args[args.index("--content") + 1])
            message = {"message_id": f"om_room_{len(self.messages)}", "chat_id": "oc_room_fixture",
                "sender": {"sender_type": "app", "id": "cli_room_fixture"},
                "body": {"content": json.dumps(card)}}
            self.messages.append(message)
            if self.fail_send:
                self.fail_send = False
                return {"returncode": 1, "stdout": "", "stderr": "", "timed_out": True}
            payload = {"data": {"message_id": message["message_id"]}}
        elif "+messages-mget" in args:
            payload = {"data": {"items": self.messages}}
        elif "+chat-messages-list" in args:
            payload = {"data": {"items": self.messages, "has_more": False}}
        return {"returncode": 0, "stdout": json.dumps(payload), "stderr": ""}


@pytest.fixture(params=["file", "sqlite"])
def setup(tmp_path, monkeypatch, request):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime, registry, state = tmp_path / "runtime", tmp_path / ".loopx/registry.json", tmp_path / "state.md"
    registry.parent.mkdir()
    state.write_text("## Agent Todo\n")
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{
        "id": GOAL, "repo": str(tmp_path), "state_file": state.name,
        "coordination": {"registered_agents": ["agent-a", "agent-b"]}}]}))
    todos = [{"schema_version": "todo_item_v0", "todo_id": TODO, "role": "agent", "status": "open",
        "done": False, "text": "PRIVATE_ORGANIZATIONAL_CONTEXT", "archive_state": "active",
        "source_section": "Agent Todo", "index": 1, "task_class": "advancement_task"}]
    provider = request.param.split(":")[0]
    if request.param.endswith(":bound"):
        todos[0]["bound_agent"] = "agent-b"
    projection = build_todo_runtime_shadow_projection(goal_id=GOAL, todos=todos, handoff_mode="soft_claim")
    initialize_canonical_authority(runtime, GOAL, projection, state_path=state, provider=provider)
    binding_path = registry.parent / "goal-channel.json"
    binding = {"schema_version": "loopx_goal_channel_lark_binding_v0", "bindings": {GOAL: {
        "schema_version": "loopx_goal_channel_connection_set_v0", "connections": {
            actor: {"goal_id": GOAL, "agent_id": actor, "provider": "lark", "enabled": True,
                "channel": {"chat_id": "oc_room_fixture"}, "identity": {"mode": "project_bot",
                    "sender_identity": "bot", "sender_profile": "room-fixture",
                    "bot_app_id": "cli_room_fixture", "bot_display_name": "Fixture Bot", "cli_bin": "fixture-cli"}}
            for actor in ["agent-a", "agent-b"]}}}}
    write_goal_channel_binding(binding_path, binding)
    room = Room()
    monkeypatch.setattr(work, "resolve_extension_activation", lambda *a, **k: {"enabled": True})
    args = dict(registry_path=registry, runtime_root=runtime, binding_path=binding_path,
        target_path=runtime / "targets.json", goal_id=GOAL, runner=room)
    yield args, room, binding
    # Each fixture owns its disposable runtime; shut it down before its root vanishes.
    from loopx.control_plane.effect_runtime import effect_runtime_result
    effect_runtime_result("runtime.shutdown", {}, retry_safe=False)


def snapshot(args):
    return read_canonical_todos_if_promoted(runtime_root=args["runtime_root"], goal_id=GOAL)


def claim(args, actor, revision, key, **extra):
    return work.run_goal_channel_work(**args, actor_id=actor, command="claim", execute=True,
        todo_id=TODO, expected_revision=revision, idempotency_key=key, **extra)


def _source_cli_claim(args, actor, revision, key, worker_root, results, barrier=None, messages=()):
    """Real CLI client/TS runtime; fixture extension config and synthetic Lark IO."""
    worker_root.mkdir()
    for variable in ("TMPDIR", "TEMP", "TMP"):
        os.environ[variable] = str(worker_root)
    tempfile.tempdir = str(worker_root)
    from loopx.cli import main
    from loopx.control_plane.effect_runtime import effect_runtime_result

    room = Room()
    room.messages = list(messages)
    argv = ["--registry", str(args["registry_path"]), "--runtime-root", str(args["runtime_root"]),
        "--format", "json", "goal-channel", "work", "claim", "--goal-id", GOAL,
        "--agent-id", actor, "--todo-id", TODO, "--expected-revision", revision,
        "--idempotency-key", key, "--execute"]
    try:
        if barrier is not None:
            barrier.wait(timeout=30)
        output = io.StringIO()
        # The parent fixture supplies an enabled extension. Spawned clients
        # independently reproduce that fixture fact and inject only room IO.
        with patch.object(work, "resolve_extension_activation", return_value={"enabled": True}), \
             patch.object(work, "run_goal_channel_work", partial(work.run_goal_channel_work, runner=room)), \
             redirect_stdout(output):
            exit_code = main(argv)
        runtime_info = list(worker_root.glob("loopx-effect-runtime-*/runtime-*.json"))
        assert len(runtime_info) == 1
        runtime_pid = json.loads(runtime_info[0].read_text())["pid"]
        results.put({"pid": os.getpid(), "runtime_pid": runtime_pid, "exit_code": exit_code,
            "packet": json.loads(output.getvalue()), "messages": room.messages})
    finally:
        effect_runtime_result("runtime.shutdown", {}, retry_safe=False)


def _independent_claims(args, revision, actors, root, messages=()):
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    barrier = context.Barrier(len(actors)) if len(actors) > 1 else None
    clients = [context.Process(target=_source_cli_claim,
        args=(args, actor, revision, "room-claim-" + actor, root / actor, results, barrier, messages))
        for actor in actors]
    try:
        for client in clients:
            client.start()
        observed = [results.get(timeout=90) for _ in clients]
        for client in clients:
            client.join(timeout=30)
            assert client.exitcode == 0
        return observed
    finally:
        for client in clients:
            if client.is_alive():
                client.terminate()
                client.join(timeout=10)
        results.close()
        results.join_thread()


def test_independent_source_cli_claims_retry_and_direct_readback(setup, tmp_path):
    args, room, _ = setup
    before = snapshot(args)
    project = work.run_goal_channel_work(**args, actor_id="agent-a", command="project", execute=False)
    assert project["ok"], project
    assert project["projection"]["counts"] == {"unclaimed": 1, "user_gates": 0}
    assert not room.messages and "PRIVATE_" not in json.dumps(project)
    revision = before["provider_revision"]
    process_args = {name: value for name, value in args.items() if name != "runner"}
    clients = _independent_claims(process_args, revision, ["agent-a", "agent-b"], tmp_path)
    assert len({client["pid"] for client in clients} | {os.getpid()}) == 3
    assert len({client["runtime_pid"] for client in clients}) == 2
    results = [client["packet"] for client in clients]
    assert sum(r["canonical_claim_accepted"] for r in results) == 1, results
    assert sorted(r["status"] for r in results) == ["applied", "conflict"], results
    winner = next(r["actor_id"] for r in results if r["canonical_claim_accepted"])
    committed = snapshot(args)
    winner_client = next(client for client in clients if client["packet"]["actor_id"] == winner)
    retry_root = tmp_path / "retry"
    retry_root.mkdir()
    retry = _independent_claims(process_args, revision, [winner], retry_root, winner_client["messages"])[0]
    assert sorted(client["exit_code"] for client in clients) == [0, 1]
    assert retry["exit_code"] == 0
    replay = retry["packet"]
    assert replay["status"] == "already_applied" and replay["readback_verified"], replay
    assert snapshot(args)["provider_revision"] == committed["provider_revision"]
    assert snapshot(args)["todos"][0]["claimed_by"] == winner
    assert len(retry["messages"]) == 1
    assert "PRIVATE_" not in json.dumps(clients + [retry])
    readback = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(args["registry_path"]),
        "--format", "json", "todo", "list", "--goal-id", GOAL], cwd=ROOT, text=True, capture_output=True)
    assert readback.returncode == 0, readback.stdout + readback.stderr
    assert json.loads(readback.stdout)["todos"][0]["claimed_by"] == winner


def test_reconnect_after_unknown_delivery_reuses_canonical_receipt(setup):
    args, room, _ = setup
    revision = snapshot(args)["provider_revision"]
    room.fail_send = True
    first = claim(args, "agent-a", revision, "room-lost-response")
    assert first["canonical_claim_accepted"] and not first["ok"], first
    assert first["external_write_performed"] and first["blocker"] == "delivery_outcome_unknown"
    committed = snapshot(args)["provider_revision"]
    recovered = claim(args, "agent-a", revision, "room-lost-response")
    assert recovered["ok"] and recovered["status"] == "already_applied", recovered
    assert len(room.messages) == 1
    assert snapshot(args)["provider_revision"] == committed


def test_revocation_and_auth_failure_cannot_create_or_replay_claim(setup):
    args, room, binding = setup
    before = snapshot(args)["provider_revision"]
    room.authenticated = False
    denied = claim(args, "agent-a", before, "room-auth-denied")
    assert not denied["canonical_claim_accepted"] and not room.messages
    assert snapshot(args)["provider_revision"] == before
    room.authenticated = True
    assert claim(args, "agent-a", before, "room-revocation")["canonical_claim_accepted"]
    committed = snapshot(args)["provider_revision"]
    binding["bindings"][GOAL]["connections"]["agent-a"]["enabled"] = False
    write_goal_channel_binding(args["binding_path"], binding)
    replay = claim(args, "agent-a", before, "room-revocation")
    assert not replay["canonical_claim_accepted"] and replay["failure_stage"] == "scope", replay
    assert snapshot(args)["provider_revision"] == committed


def test_real_room_cli_preview_and_stale_revision(setup):
    args, room, _ = setup
    proc = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(args["registry_path"]),
        "--format", "json", "goal-channel", "work", "project", "--goal-id", GOAL,
        "--agent-id", "agent-a"], cwd=ROOT, text=True, capture_output=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    packet = json.loads(proc.stdout)
    assert packet["projection"]["selected_todo"]["todo_id"] == TODO
    assert packet["external_write_performed"] is False and "PRIVATE_" not in proc.stdout
    revision = snapshot(args)["provider_revision"]
    rejected = claim(args, "agent-a", "stale-revision", "room-stale")
    assert rejected["status"] == "conflict", rejected
    assert snapshot(args)["provider_revision"] == revision and not room.messages


def test_provider_loss_does_not_fall_back_or_disclose(setup, monkeypatch):
    args, room, _ = setup
    def unavailable(**kwargs):
        raise RuntimeError("PRIVATE_CREDENTIAL_AND_BACKEND_PATH")
    monkeypatch.setattr(work, "read_canonical_todos_if_promoted", unavailable)
    packet = work.run_goal_channel_work(**args, actor_id="agent-a", command="project", execute=True)
    assert not packet["ok"] and packet["failure_stage"] == "projection"
    assert "PRIVATE_" not in json.dumps(packet) and not room.messages


@pytest.mark.parametrize("setup", ["file:bound", "sqlite:bound"], indirect=True)
def test_bound_agent_guard_applies_to_room_and_existing_direct_claim(setup):
    args, room, _ = setup
    before = snapshot(args)["provider_revision"]
    project = work.run_goal_channel_work(**args, actor_id="agent-a", command="project", execute=False)
    assert project["projection"]["counts"]["unclaimed"] == 0
    denied = claim(args, "agent-a", before, "room-bound-denied")
    assert denied["status"] == "rejected" and not denied["canonical_claim_accepted"], denied
    direct = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(args["registry_path"]),
        "--format", "json", "todo", "claim", "--goal-id", GOAL, "--todo-id", TODO,
        "--agent-id", "agent-a", "--claimed-by", "agent-a"], cwd=ROOT, text=True, capture_output=True)
    assert direct.returncode == 1
    assert json.loads(direct.stdout)["error_code"] == "bound_agent_mismatch"
    assert snapshot(args)["provider_revision"] == before and not room.messages


def test_changed_key_intent_and_revoked_identity_cannot_reapply(setup):
    args, room, _ = setup
    before = snapshot(args)["provider_revision"]
    assert claim(args, "agent-a", before, "room-exact-intent")["canonical_claim_accepted"]
    committed = snapshot(args)["provider_revision"]
    changed = claim(args, "agent-a", committed, "room-exact-intent")
    assert not changed["canonical_claim_accepted"]
    registry = json.loads(args["registry_path"].read_text())
    registry["goals"][0]["coordination"]["registered_agents"] = ["agent-b"]
    args["registry_path"].write_text(json.dumps(registry))
    revoked = claim(args, "agent-a", before, "room-exact-intent")
    assert not revoked["canonical_claim_accepted"] and revoked["failure_stage"] == "scope"
    assert snapshot(args)["provider_revision"] == committed and len(room.messages) == 1


def test_scope_revoked_during_provider_preflight_is_rechecked_before_claim(setup):
    args, room, _ = setup
    before = snapshot(args)["provider_revision"]
    revoked = False
    def revoke_then_respond(argv, cwd, timeout):
        nonlocal revoked
        if "auth" in argv and not revoked:
            registry = json.loads(args["registry_path"].read_text())
            registry["goals"][0]["coordination"]["registered_agents"] = ["agent-b"]
            args["registry_path"].write_text(json.dumps(registry))
            revoked = True
        return room(argv, cwd, timeout)
    changed_args = {**args, "runner": revoke_then_respond}
    denied = claim(changed_args, "agent-a", before, "room-preflight-revoked")
    assert revoked and not denied["canonical_claim_accepted"], denied
    assert snapshot(args)["provider_revision"] == before and not room.messages


@pytest.mark.parametrize("has_receipt", [False, True])
@pytest.mark.parametrize("goal_ref", [None, "invalid", {"goal_id": GOAL, "goal_instance_id": "ginst_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}])
@pytest.mark.parametrize("wire_version", [0, 1])
def test_legacy_claim_wire_cannot_silently_accept_goal_ref(setup, has_receipt, goal_ref, wire_version):
    import hashlib
    from loopx.control_plane.effect_runtime import effect_runtime_result
    args, room, _ = setup
    original_revision = snapshot(args)["provider_revision"]
    key = "room-unqualified-goal-ref"
    if has_receipt:
        assert claim(args, "agent-a", original_revision, key)["canonical_claim_accepted"]
    before = snapshot(args)["provider_revision"]
    packet = {"schema_version": "loopx_local_coordination_todo_claim_request_v1",
        "runtime_root": str(args["runtime_root"]), "goal_id": GOAL, "todo_id": TODO,
        "role": "agent", "claimed_by": "agent-a", "actor_agent_id": "agent-a",
        "registered_agents": ["agent-a", "agent-b"],
        "registry_source": {"path": str(args["registry_path"]),
            "sha256": hashlib.sha256(args["registry_path"].read_bytes()).hexdigest()},
        "operation_id": key, "expected_provider_revision": original_revision,
        "lease_request": None, "observed_at": "2026-09-29T00:00:00Z", "dry_run": False,
        "goal_ref": goal_ref}
    if wire_version == 0:
        packet["schema_version"] = "loopx_local_coordination_todo_claim_request_v0"
        packet.pop("registry_source")
    result = effect_runtime_result("coordination.local_authority.todo_claim", packet)
    assert result["status"] == "failed" and result["reason_code"] == "goal_ref_claim_contract_unqualified", result
    assert result["decision_read_from_provider"] is False
    assert snapshot(args)["provider_revision"] == before
