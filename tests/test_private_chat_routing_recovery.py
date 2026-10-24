"""The original delivery worker, not a manual replay, recovers routing outages."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from test_native_steward_private import steward, finish  # noqa: F401
from loopx.capabilities.native_chat import external_conversations
from loopx.capabilities.native_chat.codex_adapter import CodexAppServerAdapter
from loopx.extensions.lark.conversation_identity import identity_ref
from loopx.extensions.lark.private_conversations import LarkPrivateConversations
from loopx.extensions.lark.private_progress import presentation_phase


@pytest.mark.parametrize("fault", [None, "source-changed", "revoked", "stop", "stop-intent", "steered",
    "active", "native-final", "unknown-item", "reservation-crash", "post-enqueue-crash", "review-failed",
    "review-routing-failed", "review-quota-failed", "steward"])
def test_original_worker_reviews_interrupted_effects_once_without_replaying_business(steward, monkeypatch, tmp_path, fault):  # noqa: F811
    store, runtime, provider, transport, _, capture, workspace = steward
    profile = "steward-app" if fault == "steward" else "notes-app"
    binding = next(row for row in transport.bindings.read()["bindings"] if row["transport_ref"] == profile)
    event = provider.event(profile, "interrupted-effects", "typed terminal failure")
    transport.admit(profile, event)
    ref = identity_ref(binding["provider_ref"], event["message_id"])
    original = transport.core.read_request(ref)
    failed = finish(runtime, original)
    assert failed["status"] == "failed"
    # Model a durable host-restart record after observed output/tool work.
    # Native effects below are independently read over the original protocol.
    store.update_turn(original["session_id"], original["turn_id"], error_code="server_restarted",
        delta_count=7,
        completed_at=(datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat())
    failed = store.load_turn(original["session_id"], original["turn_id"])
    artifact = workspace / "existing-result.md"
    artifact.write_text("Already written by the original execution.\n")
    result_bytes = artifact.read_bytes()
    transport.reconcile()
    path = transport.root / f"{ref}.json"
    old_notice = json.loads(path.read_text())["deliveries"]["terminal"]
    failed_path = store._turn_path(original["session_id"], original["turn_id"])
    failed_bytes = failed_path.read_bytes()
    original_session = store.load_session(original["session_id"])
    runtime.adapters.pop(original["session_id"]).close_session()
    Path(original_session["codex_home"]).mkdir(parents=True, exist_ok=True)
    native_id = failed["upstream_turn_id"]
    inventory = tmp_path / "interrupted-native.json"
    items = [{"turnId": native_id, "item": {"id": "user", "type": "userMessage"}},
        {"turnId": native_id, "item": {"id": "write", "type": "fileChange", "status": "completed"}}]
    if fault in {"native-final", "unknown-item"}:
        items.append({"turnId": native_id, "item": {"id": "extra",
            "type": "agentMessage" if fault == "native-final" else "futureItem", "phase": "final_answer"}})
    inventory.write_text(json.dumps({"turns": [{"id": native_id, "status": "interrupted"}], "items": items}))
    fake = Path(runtime.codex_bin)
    source = fake.read_text()
    source = source.replace('        result = {"thread": {"id": "durable-thread"}}', '''        result = {"thread": {"id": "durable-thread"}}
        permissions = request.get("params", {}).get("permissions")
        if permissions:
            result.update(activePermissionProfile={"id": permissions}, runtimeWorkspaceRoots=[request["params"]["cwd"]])''')
    begin = source.index('    elif method == "thread/turns/list":')
    end = source.index('    else:\n        result = {}', begin)
    source = source[:begin] + f'''    elif method in {{"thread/turns/list", "thread/items/list"}}:
        with open({str(inventory)!r}) as observed:
            snapshot = json.load(observed)
        result = {{"data": snapshot["turns" if method == "thread/turns/list" else "items"]}}
    elif method == "thread/backgroundTerminals/list":
        result = {{"data": []}}
''' + source[end:]
    if fault not in {"review-failed", "review-routing-failed", "review-quota-failed"}:
        source = source.replace('if "typed terminal failure" in json.dumps(request.get("params") or {}):', 'if False:')
    if fault in {"review-routing-failed", "review-quota-failed"}:
        source = source.replace('"codexErrorInfo": "cyberPolicy"', '"codexErrorInfo": "' + (
            "other" if fault == "review-routing-failed" else "usageLimitExceeded") + '"')
        source = source.replace('"private-fixture-upstream-detail"', '"workspace routing discovery failed"')
    fake.write_text(source)
    if fault == "source-changed":
        provider.messages[event["message_id"]]["content"] = "Revised requirement"
    elif fault == "revoked":
        transport.bindings.disconnect(binding["binding_id"], expected_revision=transport.bindings.read()["revision"])
    elif fault in {"steered", "stop-intent"}:
        store.append_event(original["session_id"], original["turn_id"],
            kind="turn.steered" if fault == "steered" else "turn.interrupt_requested", payload={})
    elif fault == "stop":
        transport.admit(profile, provider.event(profile, "later-stop", "/stop"))
    elif fault == "active":
        store.update_session(original["session_id"], status="busy", active_turn_id="some-other-turn")

    def worker():
        return LarkPrivateConversations(controller=runtime, runtime_root=store.root.parent,
            runner=provider, cli_bin="lark-cli")
    selected = worker()
    if fault in {"reservation-crash", "post-enqueue-crash"}:
        enqueue = runtime.enqueue_turn
        def crash(**kw):
            if fault == "post-enqueue-crash":
                enqueue(**kw)
            raise OSError("review admission crash")
        monkeypatch.setattr(runtime, "enqueue_turn", crash)
        with pytest.raises(OSError, match="review admission crash"):
            selected.reconcile()
        monkeypatch.setattr(runtime, "enqueue_turn", enqueue)
        selected = worker()
    selected.reconcile()
    recovered = selected.core.read_request(ref)
    if fault in {"source-changed", "revoked", "stop", "stop-intent", "steered", "active", "native-final", "unknown-item", "steward"}:
        assert recovered["turn_id"] == original["turn_id"] and not recovered.get("context_recovery")
        assert failed_path.read_bytes() == failed_bytes and artifact.read_bytes() == result_bytes
        return
    assert recovered["session_id"] == original["session_id"] and recovered["turn_id"] != original["turn_id"]
    assert recovered["context_recovery"]["read_only_review"] is True
    assert "-review-1" in recovered["context_recovery"]["client_turn_id"]
    result = finish(runtime, recovered)
    assert result["status"] == ("failed" if str(fault).startswith("review-") else "completed")
    if str(fault).startswith("review-"):
        # Cross routing/quota backoff: immediate rejection alone could hide a
        # later writable replay of the original request after review failure.
        store.update_turn(recovered["session_id"], recovered["turn_id"],
            completed_at=(datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat())
    selected.reconcile_request(path)
    assert not selected.core.context_recovery_candidate(ref)
    delivered = json.loads(path.read_text())
    assert delivered["deliveries"]["terminal"] == old_notice
    assert delivered["deliveries"][presentation_phase(recovered, "terminal")]["verified"]
    assert failed_path.read_bytes() == failed_bytes and artifact.read_bytes() == result_bytes
    before = list(provider.writes)
    worker().reconcile()
    assert provider.writes == before
    session = store.load_session(original["session_id"])
    assert session["project_context"] == original_session["project_context"]
    assert session["upstream_thread_id"] == original_session["upstream_thread_id"]
    assert session["codex_home"] == original_session["codex_home"]
    packets = [json.loads(line) for line in capture.read_text().splitlines()]
    starts = [p["params"] for p in packets if p.get("method") == "turn/start"]
    assert len(starts) == 2 and starts[-1]["permissions"] == "loopx_workspace_only_read"
    assert "do not re-execute" in starts[-1]["input"][0]["text"]
    resumes = [p["params"] for p in packets if p.get("method") == "thread/resume"]
    assert resumes[-1]["config"]["permissions"]["loopx_workspace_only_read"]["network"]["enabled"] is False
    next_event = provider.event(profile, "later-normal", "A separate normal request")
    selected.admit(profile, next_event)
    later = selected.core.read_request(identity_ref(binding["provider_ref"], next_event["message_id"]))
    assert finish(runtime, later)["status"] == "completed"
    assert runtime.adapters[original["session_id"]].session.read_only_review is False
    assert runtime.adapters[original["session_id"]].session.permissions_profile is None


@pytest.mark.parametrize("fault", [None, "missing-policy", "missing-correlation"])
def test_queued_result_review_cannot_fall_back_to_write_access_without_its_journal(steward, fault):  # noqa: F811
    store, runtime, provider, transport, _, _, _ = steward
    event = provider.event("notes-app", "journal-policy", "A normal original request")
    transport.admit("notes-app", event)
    binding = next(row for row in transport.bindings.read()["bindings"] if row["transport_ref"] == "notes-app")
    ref = identity_ref(binding["provider_ref"], event["message_id"])
    row = transport.core.read_request(ref)
    assert finish(runtime, row)["status"] == "completed"
    client = f"external-{ref}-recovery-21-review-1"
    row["context_recovery"] = {"client_turn_id": client,
        **({} if fault == "missing-policy" else {"read_only_review": True})}
    if fault == "missing-correlation":
        row["context_recovery"]["client_turn_id"] = "another"
    (transport.core.root / f"{ref}.json").write_text(json.dumps(row))
    session = store.load_session(row["session_id"])
    queued = {"turn_id": "review", "status": "queued", "client_turn_id": client}
    if fault is None:
        assert transport.core.queued_recovery_context(session, queued, "review")["read_only_review"] is True
    else:
        with pytest.raises(ValueError, match="reserved result review"):
            transport.core.queued_recovery_context(session, queued, "review")


@pytest.mark.parametrize("recovery_kind", ["routing", "quota"])
@pytest.mark.parametrize("profile", ["notes-app", "steward-app"])
@pytest.mark.parametrize("fault", [None, "changed-source", "revoked", "unknown-effects",
    "reservation-crash", "post-enqueue-crash"])
def test_original_scanner_recovers_across_another_failure_and_restart(steward, monkeypatch, profile, fault, recovery_kind):  # noqa: F811
    store, runtime, provider, transport, _, capture, _ = steward
    fake = Path(runtime.codex_bin)
    native_error = "usageLimitExceeded" if recovery_kind == "quota" else "other"
    source = fake.read_text().replace('"codexErrorInfo": "cyberPolicy"', f'"codexErrorInfo": "{native_error}"')
    source = source.replace('"private-fixture-upstream-detail"',
        '"Subscription allowance exhausted"' if recovery_kind == "quota" else
        '"Error running remote compact task: workspace routing discovery timed out"')
    source = source.replace('            print(json.dumps({"method": "item/agentMessage/delta", "params": {"threadId": "durable-thread", "turnId": active_turn, "delta": "Partial answer."}}), flush=True)\n', '')
    fake.write_text(source)
    binding = next(row for row in transport.bindings.read()["bindings"] if row["transport_ref"] == profile)
    event = provider.event(profile, "routing-outage", "typed terminal failure")
    transport.admit(profile, event)
    ref = identity_ref(binding["provider_ref"], event["message_id"])
    original = transport.core.read_request(ref)
    assert finish(runtime, original)["status"] == "failed"
    failed_path = store._turn_path(original["session_id"], original["turn_id"])
    original_bytes = failed_path.read_bytes()
    transport.reconcile()
    path = transport.root / f"{ref}.json"
    old_notice = json.loads(path.read_text())["deliveries"]["terminal"]
    old_body = dict(provider.messages[old_notice["attempt"]["message_ref"]])

    clock = [datetime.now(timezone.utc) + timedelta(seconds=61)]
    class AfterBackoff(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]

    monkeypatch.setattr(external_conversations, "datetime", AfterBackoff)
    monkeypatch.setattr(CodexAppServerAdapter, "failed_request_observation", staticmethod(lambda **kw: None
        if fault == "unknown-effects" else {"thread_id": "durable-thread", "turn_id": kw["turn_id"],
            "codex_error_info": native_error, "pre_execution_routing_timeout": recovery_kind == "routing",
            "only_pre_execution_items": recovery_kind == "routing", "only_original_user_item": recovery_kind == "quota",
            "quota_available": True, "quota_observed_at": int(clock[0].timestamp() * 1000) / 1000}))
    if fault == "changed-source":
        provider.messages[event["message_id"]]["content"] = "Changed requirement"
    elif fault == "revoked":
        transport.bindings.disconnect(binding["binding_id"], expected_revision=transport.bindings.read()["revision"])

    def restarted_worker():
        return LarkPrivateConversations(controller=runtime, runtime_root=store.root.parent,
            runner=provider, cli_bin="lark-cli")

    first = restarted_worker()
    if fault in {"reservation-crash", "post-enqueue-crash"}:
        enqueue = runtime.enqueue_turn
        def crash(**kwargs):
            if fault == "post-enqueue-crash":
                enqueue(**kwargs)
            raise OSError("simulated admission crash")
        monkeypatch.setattr(runtime, "enqueue_turn", crash)
        with pytest.raises(OSError, match="admission crash"):
            first.reconcile()
        monkeypatch.setattr(runtime, "enqueue_turn", enqueue)
        first = restarted_worker()
    first.reconcile()
    continued = first.core.read_request(ref)
    if fault in {"changed-source", "revoked", "unknown-effects"}:
        assert continued["turn_id"] == original["turn_id"]
        assert not continued.get("context_recovery")
        assert failed_path.read_bytes() == original_bytes
        return
    assert continued["turn_id"] != original["turn_id"], first.core._context_recovery_plan(original, observation=CodexAppServerAdapter.failed_request_observation(turn_id="active"), source_verified=True)
    assert continued["context_recovery"][f"{recovery_kind}_attempt"] == 1
    assert finish(runtime, continued)["status"] == "failed"
    first.reconcile_request(path)
    assert first.core.read_request(ref)["delivery_verified"]
    assert not first.core.context_recovery_candidate(ref)  # The next backoff survives a scanner restart.
    first_notice = json.loads(path.read_text())["deliveries"][presentation_phase(continued, "terminal")]
    second_failed = store._turn_path(continued["session_id"], continued["turn_id"])
    second_bytes = second_failed.read_bytes()
    runtime.adapters.pop(original["session_id"]).close_session()
    fake.write_text(source.replace('if "typed terminal failure" in json.dumps(request.get("params") or {}):', 'if False:'))
    clock[0] += timedelta(minutes=5)
    second = restarted_worker()
    second.reconcile()
    recovered = second.core.read_request(ref)
    assert recovered["context_recovery"][f"{recovery_kind}_attempt"] == 2, second.core._context_recovery_plan(recovered)
    assert recovered["context_recovery_history"] == [continued["context_recovery"]]
    assert recovered["session_id"] == original["session_id"]
    assert finish(runtime, recovered)["status"] == "completed"
    second.reconcile_request(path)
    delivered = json.loads(path.read_text())
    assert delivered["deliveries"]["terminal"] == old_notice
    assert delivered["deliveries"][presentation_phase(continued, "terminal")] == first_notice
    assert delivered["deliveries"][presentation_phase(recovered, "terminal")]["verified"]
    assert provider.messages[old_notice["attempt"]["message_ref"]] == old_body
    assert failed_path.read_bytes() == original_bytes and second_failed.read_bytes() == second_bytes
    before = list(provider.writes)
    second.reconcile_request(path)
    restarted_worker().reconcile()
    assert provider.writes == before
    starts = [json.loads(line) for line in capture.read_text().splitlines()
        if json.loads(line).get("method") == "turn/start"]
    assert len(starts) == 3 and len(store.list_sessions()) == 1


@pytest.mark.parametrize("profile", ["notes-app", "steward-app"])
@pytest.mark.parametrize("fault", [None, "tool", "compaction", "active", "changed-source", "delta", "policy"])
def test_original_worker_recovers_historical_user_only_routing_failure(steward, monkeypatch, tmp_path, profile, fault):  # noqa: F811
    """Read the persisted native inventory over stdio, then use the original queue.

    A later successful request cannot prove an earlier untouched request ran.
    Tools, compaction on that older Turn, changed sources and unknown local
    effects still prevent recovery. No test substitutes a recovery proof.
    """
    store, runtime, provider, transport, _, capture, _ = steward
    fake = Path(runtime.codex_bin)
    source = fake.read_text().replace('"codexErrorInfo": "cyberPolicy"', '"codexErrorInfo": "other"')
    source = source.replace('"private-fixture-upstream-detail"',
        '"Error running remote compact task: workspace routing discovery failed"')
    source = source.replace('            print(json.dumps({"method": "item/agentMessage/delta", "params": {"threadId": "durable-thread", "turnId": active_turn, "delta": "Partial answer."}}), flush=True)\n', '')
    fake.write_text(source)
    binding = next(row for row in transport.bindings.read()["bindings"] if row["transport_ref"] == profile)
    event = provider.event(profile, "unstarted-request", "typed terminal failure")
    transport.admit(profile, event)
    ref = identity_ref(binding["provider_ref"], event["message_id"])
    original = transport.core.read_request(ref)
    failed = finish(runtime, original)
    assert failed["status"] == "failed" and failed["delta_count"] == 0
    assert failed["error_code"] == "upstream_transport_failure"
    transport.reconcile()
    failed_path = store._turn_path(original["session_id"], original["turn_id"])
    if fault == "delta":
        store.update_turn(original["session_id"], original["turn_id"], delta_count=1)
    original_bytes = failed_path.read_bytes()
    transport_path = transport.root / f"{ref}.json"
    old_notice = json.loads(transport_path.read_text())["deliveries"]["terminal"]
    later_event = provider.event(profile, "separate-request", "Complete this independent request.")
    transport.admit(profile, later_event)
    later = transport.core.read_request(identity_ref(binding["provider_ref"], later_event["message_id"]))
    assert later["session_id"] == original["session_id"] and finish(runtime, later)["status"] == "completed"
    transport.reconcile()
    runtime.adapters.pop(original["session_id"]).close_session()
    Path(store.load_session(original["session_id"])["codex_home"]).mkdir(parents=True, exist_ok=True)

    # A fresh native stdio process reads a durable provider snapshot. The model
    # fixture now succeeds; only the existing transport worker may admit it.
    failed_native = failed["upstream_turn_id"]
    later_native = store.load_turn(later["session_id"], later["turn_id"])["upstream_turn_id"]
    inventory = tmp_path / "native-inventory.json"
    error = {"codexErrorInfo": "cyberPolicy" if fault == "policy" else "other",
        "message": "Error running remote compact task: workspace routing discovery failed"}
    entries = [{"turnId": failed_native, "item": {"type": "userMessage", "id": "original-user"}}]
    if fault in {"tool", "compaction"}:
        entries.append({"turnId": failed_native, "item": {"type":
            "commandExecution" if fault == "tool" else "contextCompaction", "id": "additional-item"}})
    inventory.write_text(json.dumps({"turns": [{"id": later_native,
        "status": "inProgress" if fault == "active" else "completed"},
        {"id": failed_native, "status": "failed", "error": error}], "items": entries}))
    begin = source.index('    elif method == "thread/turns/list":')
    end = source.index('    else:\n        result = {}', begin)
    reader = f'''    elif method in {{"thread/turns/list", "thread/items/list"}}:
        with open({str(inventory)!r}) as observed:
            snapshot = json.load(observed)
        result = {{"data": snapshot["turns" if method == "thread/turns/list" else "items"]}}
    elif method == "thread/backgroundTerminals/list":
        result = {{"data": []}}
'''
    source = source[:begin] + reader + source[end:]
    fake.write_text(source.replace('if "typed terminal failure" in json.dumps(request.get("params") or {}):', 'if False:'))
    if fault == "changed-source":
        provider.messages[event["message_id"]]["content"] = "Changed requirement"
    clock = datetime.now(timezone.utc) + timedelta(seconds=61)
    class AfterBackoff(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock
    monkeypatch.setattr(external_conversations, "datetime", AfterBackoff)
    worker = LarkPrivateConversations(controller=runtime, runtime_root=store.root.parent,
        runner=provider, cli_bin="lark-cli")
    worker.reconcile()
    recovered = worker.core.read_request(ref)
    if fault is not None:
        assert recovered["turn_id"] == original["turn_id"] and not recovered.get("context_recovery")
        assert failed_path.read_bytes() == original_bytes
        return
    assert recovered["turn_id"] != original["turn_id"]
    assert recovered["session_id"] == original["session_id"]
    assert recovered["context_recovery"]["routing_attempt"] == 1
    assert finish(runtime, recovered)["status"] == "completed"
    worker.reconcile()
    delivered = json.loads(transport_path.read_text())
    assert delivered["deliveries"]["terminal"] == old_notice
    assert delivered["deliveries"][presentation_phase(recovered, "terminal")]["verified"]
    assert failed_path.read_bytes() == original_bytes
    before = list(provider.writes)
    LarkPrivateConversations(controller=runtime, runtime_root=store.root.parent,
        runner=provider, cli_bin="lark-cli").reconcile()
    assert provider.writes == before
    requests = [json.loads(line) for line in capture.read_text().splitlines()]
    assert sum(item.get("method") == "turn/start" for item in requests) == 3
    assert any(item.get("method") == "thread/items/list" for item in requests)
    assert len(store.list_sessions()) == 1
