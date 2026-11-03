"""A terminated native reader can replay a partially supplied page."""
import json
import subprocess
import time
from pathlib import Path

import pytest

from loopx.control_plane.collaboration.goal_instance_scope import collaboration_goal_scope
from loopx.control_plane.collaboration.inbox import _root, pending
from loopx.control_plane.collaboration.peers import request
from loopx.control_plane.projects.registry_codec import source_session_registry_transaction
from loopx.control_plane.effect_runtime import _node_executable
from loopx.file_lock import exclusive_cross_runtime_file_lock


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("loss", ["source", "partial", "committed"])
def test_native_cli_keeps_exact_receipt_recovery_after_lost_read_response(tmp_path, monkeypatch, capsys, provider, loss):
    from canonical_authority_fixture import isolate_sqlite_runtime, promoted_create_fixture
    from loopx.cli import main
    from loopx.control_plane.collaboration import inbox
    from loopx.control_plane.effect_runtime import EffectRuntimeResponseAmbiguous

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, root, _ = promoted_create_fixture(tmp_path, provider=provider, registered_agents=["worker", "source"])
    brief = {"schema_version": "collaboration_brief_v0", "purpose": "Inspect the artifact",
        "context": "Private source body must not enter error metadata", "constraints": [], "inputs": [],
        "acceptance": ["Return checked findings"], "return_requirement": "Reply to the source"}
    ids = {request(root, registry, "goal-a", "source", "worker", operation, brief)["request_id"]
        for operation in ("one", "two")}
    transport = inbox.read_inbox_transaction
    attempts = []

    def lost_reply(runtime, query, **kwargs):
        stage = "receiver_read" if loss == "source" else "record_read"
        if query["operation"] == stage and not attempts:
            attempts.append(query)
            if loss != "source":
                commit = query if loss == "committed" else {**query, "items": query["items"][:1],
                    "deliveries": [], "work_observations": query["work_observations"][:1], "include_context": False}
                transport(runtime, commit, **kwargs)
            raise EffectRuntimeResponseAmbiguous("collaboration.inbox.read", timeout=10)
        return transport(runtime, query, **kwargs)

    monkeypatch.setattr(inbox, "read_inbox_transaction", lost_reply)
    route = ["--registry", str(registry), "--runtime-root", str(root), "--format", "json", "manager-inbox"]
    assert main([*route, "read", "--goal-id", "goal-a", "--agent-id", "worker"]) == 1
    error = json.loads(capsys.readouterr().out)
    assert error["ok"] is False and error["error_code"] == "runtime_response_ambiguous"
    assert error["read_complete"] is False
    recovery = error["recovery"]
    assert recovery["scope"] == {"goal_id": "goal-a", "agent_id": "worker", "goal_ref": None}
    assert recovery["read_arguments"] == {"cursor": None, "operation_cursor": None, "request_id": None}
    assert recovery["automatic_retry_performed"] is False and len(attempts) == 1
    if loss == "source":
        assert recovery["stage"] == "receiver_read"
        assert recovery["requests"] is None and recovery["peer_returns"] is None
    else:
        assert recovery["stage"] == "record_read"
        assert {row["request_id"] for row in recovery["requests"]} == ids
        assert recovery["peer_returns"] == []
    assert brief["context"] not in json.dumps(error)
    assert str(tmp_path) not in json.dumps(error)
    before = {p: p.read_bytes() for p in _root(root).rglob("*.json")}
    recorded = []
    for rid in ids:
        assert main([*route, "status", "--goal-id", "goal-a", "--agent-id", "worker", "--request-id", rid]) == 0
        status = json.loads(capsys.readouterr().out)["rows"][0]
        recorded.append(status["read"]["status"] == "supplied_to_receiver")
    assert sum(recorded) == {"source": 0, "partial": 1, "committed": 2}[loss]
    assert {p: p.read_bytes() for p in _root(root).rglob("*.json")} == before
    # Recover only after reading the exact durable statuses. The same request
    # page is supplied without replacing earlier receipts or consuming results.
    assert main([*route, "read", "--goal-id", "goal-a", "--agent-id", "worker"]) == 0
    assert {row["request_id"] for row in json.loads(capsys.readouterr().out)["items"]} == ids
    assert all(path.read_bytes() == value for path, value in before.items())
    assert len(list((_root(root) / "reads").glob("*.json"))) == 2
    assert not list((_root(root) / "replies").rglob("*.consumed.json"))


def test_interrupted_page_preserves_first_receipt_and_finishes_remaining_reads(tmp_path):
    registry = tmp_path / "registry.json"
    with source_session_registry_transaction(registry, operation="create_inbox_fixture", create=lambda: {
        "profile_id": "source_session_v1", "goals": [{"id": "research", "repo": str(tmp_path),
        "status": "active", "goal_instance_id": "ginst_" + "a" * 32,
        "coordination": {"registered_agents": ["source", "worker"]}}],
        "session_bindings": [], "session_receipts": [], "lifetime_receipts": [],
    }) as transaction:
        transaction.commit(transaction.payload_copy())
    brief = {"schema_version": "collaboration_brief_v0", "purpose": "Inspect the current artifact",
        "context": "Keep existing authority", "constraints": [], "inputs": [],
        "acceptance": ["Return the findings"], "return_requirement": "Reply to the requester"}
    for operation in ("first", "second"):
        request(tmp_path, registry, "research", "source", "worker", operation, brief)
    owner = _root(tmp_path)
    before = {p: p.read_bytes() for p in (owner / "entries").rglob("*.json")}
    module = (Path(__file__).resolve().parents[2] / "loopx/control_plane/collaboration/inbox_read.ts").as_uri()
    script = f"""import {{readCollaborationInbox}} from {json.dumps(module)};
let input = ''; for await (const chunk of process.stdin) input += chunk;
process.stdout.write(JSON.stringify(await readCollaborationInbox(JSON.parse(input))));"""
    command = [_node_executable(), "--no-warnings", "--experimental-strip-types", "--input-type=module", "-e", script]
    with collaboration_goal_scope(registry, goal_id="research", agents=("worker",)) as scope:
        items = pending(tmp_path, "research", "worker", scope=scope)["items"]
        first = owner / "reads" / (items[0]["request_id"] + ".json")
        second = owner / "reads" / (items[1]["request_id"] + ".json")
        payload = {"operation": "record_read", "runtime_root": str(tmp_path),
            "goal_id": "research", "agent_id": "worker", "items": items, "deliveries": [],
            "scope": {"profile_id": scope.profile_id, "caller_goal_ref": scope.caller_goal_ref,
                "current_goal_ref": scope.current_goal_ref}, "observed_at": "2026-10-19T00:00:00+00:00"}
        with exclusive_cross_runtime_file_lock(owner / "request-locks" / items[1]["request_id"]):
            child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding="utf-8")
            try:
                child.stdin.write(json.dumps(payload))
                child.stdin.close()
                # Interrupt between request transactions. A published receipt can
                # precede lock retirement; killing there can leave a partial claim
                # subject to the lock owner's separate stale-claim recovery grace.
                first_lock = owner / "request-locks" / (items[0]["request_id"] + ".ts-effect.lock")
                deadline = time.monotonic() + 15
                while (not first.exists() or first_lock.exists()) and child.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                assert first.exists(), child.stderr.read() if child.poll() is not None else "first read was not committed"
                assert not first_lock.exists(), "first request transaction did not release its lock"
                assert not second.exists()
                committed = first.read_bytes()
            finally:
                if child.poll() is None:
                    child.terminate()
                child.wait(timeout=15)
                child.stdout.close()
                child.stderr.close()
        payload["observed_at"] = "2026-10-19T00:00:01+00:00"
        replay = subprocess.run(command, input=json.dumps(payload), capture_output=True,
            text=True, encoding="utf-8", timeout=30)
        assert replay.returncode == 0, replay.stderr
        assert json.loads(replay.stdout)["read_receipts"] == [None, None]
        assert first.read_bytes() == committed
        assert json.loads(second.read_text(encoding="utf-8"))["read_at"] == payload["observed_at"]
    assert {p: p.read_bytes() for p in before} == before


def test_large_legal_inbox_page_keeps_complete_source_and_records_supplied_reads(tmp_path):
    from loopx.control_plane.collaboration.inbox import (
        ENTRY_SCHEMA, _hash, _write, normalize_request, normalize_source_context, plan_request_publication,
    )
    from loopx.control_plane.collaboration.peers import read_inbox
    from loopx.control_plane.effect_runtime import MAX_RESPONSE_BYTES

    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"goals": [{"id": "research", "repo": str(tmp_path),
        "coordination": {"registered_agents": ["worker"]}}]}), encoding="utf-8")
    message = normalize_source_context("😀" * 24_000)
    target = {"goal_id": "research", "agent_id": "worker"}
    normalized = normalize_request({**target, "brief": {"schema_version": "collaboration_brief_v0",
        "purpose": "p" * 500, "context": "c" * 6000, "constraints": ["x" * 1000] * 8,
        "inputs": [], "acceptance": ["a" * 1000], "return_requirement": "Return the findings"}})
    folder = _root(tmp_path) / "entries" / _hash(target)
    for index in range(20):
        source_id = f"synthetic-source:{index}"
        rid = _hash([source_id, target])
        source = {"schema_version": ENTRY_SCHEMA, "request_id": rid, "source_id": source_id,
            **normalized, "message": message}
        publication = plan_request_publication(source, entry=None)
        _write(folder / (rid + ".json"), publication["entry"])
    before = {p: p.read_bytes() for p in folder.glob("*.json")}

    response = read_inbox(tmp_path, registry, "research", "worker", workspace=tmp_path)

    assert len(response["items"]) == 20
    # Keep the original legal page; it now fits the larger ordinary wire.
    assert 2 * 1024 * 1024 < len(json.dumps(response, ensure_ascii=False).encode()) <= MAX_RESPONSE_BYTES
    assert all(item["message"] == message and item["brief"] == normalized["brief"] for item in response["items"])
    assert all(item["recorded_decision"] is None for item in response["items"])
    assert {p: p.read_bytes() for p in before} == before
    assert len(list((_root(tmp_path) / "reads").glob("*.json"))) == 20


def test_operation_source_appearing_after_committed_read_is_observed_on_next_read(tmp_path, monkeypatch):
    import pytest
    from loopx.chat_store import ChatSessionStore
    from loopx.capabilities.manager_context import deliver
    from loopx.control_plane.collaboration import inbox as inbox_read
    from loopx.control_plane.collaboration.peers import read_inbox

    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"goals": [{"id": "research", "repo": str(tmp_path),
        "coordination": {"registered_agents": ["worker"]}}]}), encoding="utf-8")
    store = ChatSessionStore(tmp_path)
    session = store.create_session(goal_id="loopx-manager", agent_id="codex", adapter_kind="codex_app_server",
        upstream_thread_id="synthetic", channel_id="manager")
    turn, _ = store.create_turn(session["session_id"], client_turn_id="source", message="Inspect the artifact", origin="web")
    receipt = deliver(tmp_path, registry, session=session, turn=turn,
        request={"goal_id": "research", "agent_id": "worker"})
    transport = inbox_read.read_inbox_transaction

    def source_appears(root, query, **kwargs):
        response = transport(root, query, **kwargs)
        if query["operation"] == "receiver_read":
            path = root / "chat" / "actions" / "actions.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{broken", encoding="utf-8")
        return response

    monkeypatch.setattr(inbox_read, "read_inbox_transaction", source_appears)
    observed = read_inbox(tmp_path, registry, "research", "worker")
    assert [item["request_id"] for item in observed["items"]] == [receipt["request_id"]]
    reads = {p: p.read_bytes() for p in (_root(tmp_path) / "reads").glob("*.json")}
    monkeypatch.setattr(inbox_read, "read_inbox_transaction", transport)
    with pytest.raises(ValueError):
        read_inbox(tmp_path, registry, "research", "worker")
    assert {p: p.read_bytes() for p in reads} == reads
