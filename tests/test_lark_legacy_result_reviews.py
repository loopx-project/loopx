"""Independent original-source returns through the existing worker and native protocol."""
from datetime import datetime, timedelta, timezone
from copy import deepcopy
import json
from pathlib import Path

import pytest

from test_chat_ordinary_project import ordinary  # noqa: F401
from test_lark_private_conversations import connect
from loopx.capabilities.native_chat import external_conversations
from loopx.extensions.lark.conversation_identity import identity_ref
from loopx.extensions.lark.private_conversations import LarkPrivateConversations
from loopx.extensions.lark.private_progress import presentation_phase


def historical_native(fake, workspace):
    """A protocol peer with two failed intakes and independently readable artifacts.

    First intake has a typed obligation; the second has only an old prose answer.
    Reviews read an existing artifact. Any writable replay changes a sentinel.
    """
    inventory = workspace / "native-inventory.json"
    artifact = workspace / "verified-result.txt"
    partial = 'Source unavailable.\n<loopx-review-json>' + json.dumps({"message": "", "unfinished_work": {
        "remaining": "File the item", "dependency": "Source outage", "effects_settled": True}}) + '</loopx-review-json>'
    source = fake.read_text().replace("turn_count = 0", f'''loaded = False
review_only = False
turn_count = len(json.load(open({str(inventory)!r}))["turns"]) if __import__("os").path.exists({str(inventory)!r}) else 0''')
    source = source.replace('    elif method in {"thread/start", "thread/resume"}:',
        '    elif method in {"thread/start", "thread/resume"}:\n'
        '        loaded = True\n'
        '        review_only = request["params"].get("permissions") == "loopx_workspace_only_read"')
    source = source.replace('        result = {"thread": {"id": "durable-thread"},',
        '        result = {"thread": {"id": "durable-thread"},\n'
        '                  "activePermissionProfile": {"id": request["params"].get("permissions")},\n'
        '                  "runtimeWorkspaceRoots": request["params"].get("runtimeWorkspaceRoots"),')
    start = source.index("        response = 'Visible runtime response.")
    end = source.index('        print(json.dumps({"method": "item/agentMessage/delta"', start)
    source = source[:start] + f'''        from pathlib import Path
        path = Path({str(inventory)!r})
        previous = json.loads(path.read_text()) if path.exists() else {{"turns": [], "items": []}}
        artifact = Path({str(artifact)!r})
        if review_only:
            response = "Verified existing result: " + artifact.read_text()
        else:
            artifact.write_text((artifact.read_text() if artifact.exists() else "") + "unexpected replay\\n")
            response = {partial!r} if not previous["turns"] else "The source outage prevented intake; no item was filed."
        path.write_text(json.dumps({{"turns": [{{"id": active_turn, "status": "completed"}}, *previous["turns"]],
            "items": [{{"turnId": active_turn, "item": {{"id": "user", "type": "userMessage"}}}},
                {{"turnId": active_turn, "item": {{"id": "answer", "type": "agentMessage", "phase": "final_answer", "text": response}}}},
                *previous["items"]]}}))
''' + source[end:]
    start = source.index('    elif method == "thread/turns/list":')
    end = source.index('    else:\n        result = {}', start)
    source = source[:start] + f'''    elif method in {{"thread/turns/list", "thread/items/list"}}:
        snapshot = json.load(open({str(inventory)!r}))
        result = {{"data": snapshot["turns"] if method == "thread/turns/list" else
            [row for row in snapshot["items"] if row["turnId"] == request["params"]["turnId"]]}}
    elif method == "thread/backgroundTerminals/list":
        if not loaded:
            print(json.dumps({{"id": request["id"], "error": {{"code": -32600, "message": "thread not found"}}}}), flush=True)
            continue
        result = {{"data": []}}
''' + source[end:]
    fake.write_text(source)
    return artifact


@pytest.mark.parametrize("fault", [None, "source-changed", "stop", "revoked", "reservation-crash", "enqueue-crash",
    "compacted-source", "precision-failure", "precision-reservation-crash", "precision-enqueue-crash"])
def test_original_worker_returns_each_legacy_message_without_replaying_business(ordinary, monkeypatch, fault):  # noqa: F811
    store, runtime, contexts, _, capture, fake, workspace = ordinary
    contexts.workspace_grant = "workspace_write"
    artifact = historical_native(fake, workspace)
    store, runtime, provider, transport = connect(ordinary)
    binding = next(row for row in transport.bindings.read()["bindings"] if row["transport_ref"] == "notes-app")
    try:
        originals = []
        for name in ["typed-original", "legacy-original"]:
            event = provider.event("notes-app", name, "File this synthetic item.")
            assert transport.admit("notes-app", event)["status"] == "durably_accepted"
            ref = identity_ref(binding["provider_ref"], event["message_id"])
            row = transport.core.read_request(ref)
            ended = runtime.wait_for_turn(session_id=row["session_id"], turn_id=row["turn_id"], timeout_sec=10)
            assert ended["status"] == "completed", ended
            path = transport.root / f"{ref}.json"
            assert transport.reconcile_request(path) == 1
            originals.append((event, row, ended, path, json.loads(path.read_text())["deliveries"]["terminal"]))
        earlier, legacy = originals
        assert earlier[2]["response"]["unfinished_work"] and not legacy[2]["response"].get("unfinished_work")
        assert earlier[1]["request_ref"] != legacy[1]["request_ref"]
        original_bytes = store._turn_path(legacy[1]["session_id"], legacy[1]["turn_id"]).read_bytes()
        # An independent historical writer has since produced the artifact.
        # No original native effects are retroactively declared settled.
        artifact.write_text("catalog-item:verified")
        Path(store.load_session(earlier[1]["session_id"])["codex_home"]).mkdir(parents=True, exist_ok=True)
        runtime.adapters.pop(earlier[1]["session_id"]).close_session()
        clock = datetime.now(timezone.utc) + timedelta(seconds=61)
        class AfterBackoff(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock
        monkeypatch.setattr(external_conversations, "datetime", AfterBackoff)
        transport.reconcile_request(earlier[3])
        reviewed = transport.core.read_request(earlier[1]["request_ref"])
        assert reviewed["context_recovery"]["read_only_review"] is True
        assert runtime.wait_for_turn(session_id=reviewed["session_id"], turn_id=reviewed["turn_id"], timeout_sec=10)["status"] == "completed"
        transport.reconcile_request(earlier[3])
        assert transport.core.read_request(reviewed["request_ref"])["delivery_verified"] is True
        if fault == "compacted-source" or str(fault).startswith("precision-"):
            # Actual old event compaction leaves nanosecond filesystem stamps.
            # They exceed JSON/JS integer precision but do not change an answer.
            peer_path = transport.core.root / f"{reviewed['request_ref']}.json"
            peer = json.loads(peer_path.read_text())
            peer["context_recovery"]["original_turn"]["event_compaction_revision"] = [7, 123, 2**60 + 123]
            peer_path.write_text(json.dumps(peer))
            turn_path = store._turn_path(reviewed["session_id"], reviewed["turn_id"])
            peer_turn = json.loads(turn_path.read_text())
            peer_turn["event_compaction_revision"] = [8, 456, 2**60 + 789]
            turn_path.write_text(json.dumps(peer_turn))
        if fault == "source-changed":
            provider.messages[legacy[0]["message_id"]]["content"] = "A changed instruction"
        elif fault == "stop":
            transport.admit("notes-app", provider.event("notes-app", "stop", "/stop"))
        elif fault == "revoked":
            transport.bindings.disconnect(binding["binding_id"], expected_revision=transport.bindings.read()["revision"])
        def worker():
            return LarkPrivateConversations(controller=runtime, runtime_root=store.root.parent,
                runner=provider, cli_bin="lark-cli")
        failed_preparation = None
        if str(fault).startswith("precision-"):
            # Deliberately restore the released serialization defect at the
            # real worker/Core boundary. No synthetic success receipt is added.
            with monkeypatch.context() as old_release:
                old_release.setattr(external_conversations.result_review_evidence,
                    "related_review_basis", deepcopy)
                old_worker = worker()
                old_worker.reconcile()
                failed_request = old_worker.core.read_request(legacy[1]["request_ref"])
                failed_preparation = runtime.wait_for_turn(session_id=failed_request["session_id"],
                    turn_id=failed_request["turn_id"], timeout_sec=10)
                assert failed_preparation["status"] == "failed"
                assert failed_preparation["error_code"] == "host_gate" and failed_preparation["upstream_turn_id"] is None
                assert failed_preparation["delta_count"] == 0 and failed_preparation["response"] is None
                assert [e["kind"] for e in store.events_after(failed_request["session_id"], failed_request["turn_id"], None)] == [
                    "turn.queued", "turn.failed"]
                old_worker.reconcile_request(legacy[3])
            clock += timedelta(seconds=61)
        if fault in {"reservation-crash", "enqueue-crash", "precision-reservation-crash", "precision-enqueue-crash"}:
            enqueue = runtime.enqueue_turn
            def crash(**kwargs):
                if fault in {"enqueue-crash", "precision-enqueue-crash"}:
                    enqueue(**kwargs)
                raise OSError("synthetic lost admission acknowledgement")
            monkeypatch.setattr(runtime, "enqueue_turn", crash)
            with pytest.raises(OSError, match="lost admission acknowledgement"):
                worker().reconcile()
            monkeypatch.setattr(runtime, "enqueue_turn", enqueue)
        selected = worker()
        selected.reconcile()
        returned = selected.core.read_request(legacy[1]["request_ref"])
        if fault in {"source-changed", "stop", "revoked"}:
            assert returned["turn_id"] == legacy[1]["turn_id"] and not returned.get("context_recovery")
        else:
            assert returned["turn_id"] != legacy[1]["turn_id"]
            assert returned["session_id"] == legacy[1]["session_id"]
            recovery = returned["context_recovery"]
            assert recovery["legacy_result_review"] and recovery["read_only_review"]
            assert "unfinished_work" not in recovery and "work_attempt" not in recovery
            if failed_preparation is not None:
                assert recovery["review_preparation_attempt"] == 1
                assert recovery["client_turn_id"].endswith("-preparation-1")
                assert "preparation_attempt" not in recovery
                assert recovery["preparation_failed_turn_id"] == failed_preparation["turn_id"]
                assert len(returned["context_recovery_history"]) == 1
                old_failure = store.load_turn(returned["session_id"], failed_preparation["turn_id"])
                assert old_failure["status"] == "failed" and old_failure["upstream_turn_id"] is None
                assert old_failure["error"] == failed_preparation["error"]
            review = runtime.wait_for_turn(session_id=returned["session_id"], turn_id=returned["turn_id"], timeout_sec=10)
            assert review["status"] == "completed", review
            assert review["response"]["message"] == "Verified existing result: catalog-item:verified"
            selected.reconcile_request(legacy[3])
            record = json.loads(legacy[3].read_text())
            assert record["deliveries"]["terminal"] == legacy[4]
            delivered = record["deliveries"][presentation_phase(returned, "terminal")]
            assert delivered["verified"] and "catalog-item:verified" in delivered["text"]
            assert record["event"]["message_id"] == legacy[0]["message_id"]
            assert returned["source"] == legacy[1]["source"]
            before = list(provider.writes)
            worker().reconcile()
            assert provider.writes == before, "A restart must not create another review or reply"
            wire = [json.loads(line) for line in capture.read_text().splitlines()]
            assert sum(x["method"] == "turn/start" for x in wire) == 4
            resumes = [x["params"] for x in wire if x["method"] == "thread/resume"]
            assert resumes and all(x["permissions"] == "loopx_workspace_only_read" for x in resumes)
            for params in resumes:
                profile = params["config"]["permissions"]["loopx_workspace_only_read"]
                assert profile["filesystem"][":workspace_roots"] == {".": "read"}
                assert profile["network"]["enabled"] is False
            assert "related_answer_evidence" in capture.read_text()
        assert artifact.read_text() == "catalog-item:verified"
        assert store._turn_path(legacy[1]["session_id"], legacy[1]["turn_id"]).read_bytes() == original_bytes
    finally:
        runtime.close()
