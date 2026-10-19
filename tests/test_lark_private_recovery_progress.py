"""Provider bodies, not unchanged local receipts, prove recovery presentation."""
import copy
import json
import time
from pathlib import Path

import pytest
from test_native_steward_private import steward, finish  # noqa: F401

from loopx.capabilities.native_chat.codex_adapter import CodexAppServerAdapter
from loopx.chat_store import _atomic_write_json, _read_json
from loopx.extensions.lark.conversation_identity import identity_ref
from loopx.extensions.lark.private_conversations import LarkPrivateConversations
from loopx.extensions.lark.private_progress import legacy_recovery_repairs


def body(provider, ref):
    return json.loads(provider.messages[ref]["body"]["content"])["zh_cn"]["content"][0][0]["text"]


def legacy_failure(fixture, monkeypatch):
    store, runtime, provider, transport, binding, capture, _ = fixture
    fake = Path(runtime.codex_bin)
    source = fake.read_text().replace('"codexErrorInfo": "cyberPolicy"', '"codexErrorInfo": "contextWindowExceeded"')
    source = source.replace('            print(json.dumps({"method": "item/agentMessage/delta", "params": {"threadId": "durable-thread", "turnId": active_turn, "delta": "Partial answer."}}), flush=True)\n', '')
    fake.write_text(source)
    event = provider.event("steward-app", "legacy-presentation", "typed terminal failure")
    transport.admit("steward-app", event)
    ref = identity_ref(binding["provider_ref"], event["message_id"])
    row = transport.core.read_request(ref)
    failed = finish(runtime, row)
    assert failed["status"] == "failed" and failed["delta_count"] == 0
    path = transport.root / f"{ref}.json"
    record = _read_json(path)
    # Seed the real pre-failure draft through the production sender. The old
    # terminal notice then replaces that actual provider message on reconcile.
    transport._edit_progress(path, record, "⏳ **正在处理**", inbox=lambda: transport._inbox(record))
    assert transport.reconcile() == 1
    original = _read_json(path)
    notice = original["deliveries"]["terminal"]
    assert notice["attempt"]["message_ref"] == original["deliveries"]["progress"]["attempt"]["message_ref"]
    assert body(provider, notice["attempt"]["message_ref"]) == notice["text"]
    store.update_session(row["session_id"], manager_context_version=20)
    runtime.adapters.pop(row["session_id"]).close_session()
    source = source.replace('"durable-thread"', '"recovered-thread"').replace(
        'if "typed terminal failure" in json.dumps(request.get("params") or {}):', 'if False:').replace(
        'if "wait for interrupt" in json.dumps(request.get("params") or {}) or "wait for steer" in json.dumps(request.get("params") or {}):',
        'if True:\n            print(json.dumps({"method": "item/agentMessage/delta", "params": {"threadId": "recovered-thread", "turnId": active_turn, "delta": "Continuation draft."}}), flush=True)')
    fake.write_text(source)
    monkeypatch.setattr(CodexAppServerAdapter, "failed_request_observation", staticmethod(lambda **_: {
        "thread_id": "durable-thread", "turn_id": failed["upstream_turn_id"],
        "only_original_user_item": True, "codex_error_info": "contextWindowExceeded"}))
    failed_path = store._turn_path(row["session_id"], row["turn_id"])
    failed_bytes = failed_path.read_bytes()
    transport.reconcile()  # Original scanner discovers the old delivered error.
    deadline = time.monotonic() + 10
    while True:
        current = transport.core.read_request(ref)
        turn = store.load_turn(current["session_id"], current["turn_id"])
        transport.reconcile()
        if (turn["status"] == "running" and turn.get("upstream_turn_id")
                and current["turn_id"] != row["turn_id"]
                and _read_json(path)["deliveries"].get(f"progress.recovery.{current['context_recovery']['version']}", {}).get("verified")):
            break
        assert time.monotonic() < deadline
        time.sleep(.01)
    return store, runtime, provider, transport, path, original, current, capture, failed_path, failed_bytes


def complete_recovery(runtime, transport, current):
    turn = runtime.store.load_turn(current["session_id"], current["turn_id"])
    runtime.adapters[current["session_id"]].steer_turn("finish", turn["upstream_turn_id"])
    assert finish(runtime, current)["status"] == "completed"
    assert transport.reconcile() == 1


def test_scanner_continuation_uses_fresh_draft_and_updates_it_to_final(steward, monkeypatch):  # noqa: F811
    store, runtime, provider, transport, path, old, current, capture, failed_path, failed_bytes = legacy_failure(steward, monkeypatch)
    live = _read_json(path)
    version = current["context_recovery"]["version"]
    key = f"progress.recovery.{version}"
    notice = old["deliveries"]["terminal"]
    old_ref = notice["attempt"]["message_ref"]
    assert body(provider, old_ref) == notice["text"]
    new_ref = live["deliveries"][key]["attempt"]["message_ref"]
    assert new_ref != old_ref and body(provider, old_ref) == notice["text"]
    assert live["stream"] == old["stream"]  # Original cursor/edit proof is historical.
    complete_recovery(runtime, transport, current)
    final = _read_json(path)
    assert final["deliveries"][f"terminal.recovery.{version}"]["attempt"]["message_ref"] == new_ref
    assert body(provider, new_ref) == "Steered response."
    assert body(provider, old_ref) == notice["text"]
    assert final["deliveries"]["terminal"] == notice
    assert final["recovery_streams"][str(version)]["closed"]
    assert failed_path.read_bytes() == failed_bytes and len(store.list_sessions()) == 1
    calls, captured = list(provider.calls), capture.read_bytes()
    restored = LarkPrivateConversations(controller=runtime, runtime_root=store.root.parent, runner=provider, cli_bin="lark-cli")
    assert restored.reconcile() == 0 and restored.reconcile_request(path) == 0
    assert capture.read_bytes() == captured
    assert not any("+messages-edit" in call or "+messages-send" in call for call in provider.calls[len(calls):])


@pytest.mark.parametrize("case", ["same-draft", "rollover", "changed-body", "changed-source", "revoked", "foreign-turn", "unknown-proof", "edit-limit", "lost-ack", "lost-readback"])
def test_existing_worker_repairs_legacy_reuse_without_work_or_new_messages(steward, monkeypatch, case):  # noqa: F811
    store, runtime, provider, transport, path, old, current, capture, failed_path, failed_bytes = legacy_failure(steward, monkeypatch)
    complete_recovery(runtime, transport, current)
    record = _read_json(path)
    recovery = record.pop("context_recovery")
    # Reproduce the prior release's actual edits and durable proofs, including
    # a rollover which archived the old notice and left a newer draft behind.
    transport._edit_progress(path, record, "⏳ **正在生成回答**", inbox=lambda: transport._inbox(record))
    if case == "rollover":
        assert transport._continue_progress(path, record, inbox=lambda: transport._inbox(record))
        transport._edit_progress(path, record, "⏳ **后续进展**", inbox=lambda: transport._inbox(record))
    record["context_recovery"] = recovery
    _atomic_write_json(path, record)
    notice = old["deliveries"]["terminal"]
    old_ref = notice["attempt"]["message_ref"]
    assert body(provider, old_ref) != notice["text"] and legacy_recovery_repairs(record)
    if case == "changed-body":
        provider.messages[old_ref]["body"]["content"] = json.dumps({"zh_cn": {"content": [[{"tag": "text", "text": "External edit; do not overwrite."}]]}})
    elif case == "changed-source":
        provider.messages[record["event"]["message_id"]]["content"] = "Edited original request"
    elif case == "revoked":
        transport.bindings.disconnect(record["binding_id"], expected_revision=transport.bindings.read()["revision"])
    elif case == "foreign-turn":
        altered = {**record, "turn_id": old["turn_id"]}
        _atomic_write_json(path, altered)
    elif case == "unknown-proof":
        record["stream"]["confirmed_attempt"]["intent_digest"] = "sha256:" + "f" * 64
        _atomic_write_json(path, record)
    original_runner, applied, hidden = provider, [], []
    def runner(args, cwd=None, timeout=None):
        if (hidden and "+messages-mget" in args
                and args[args.index("--message-ids") + 1] == old_ref):
            hidden.pop()
            return {"returncode": 1, "stderr": "unavailable readback after applied edit"}
        if "+messages-edit" in args and "--dry-run" not in args:
            if case == "edit-limit":
                return {"returncode": 1, "stderr": json.dumps({"error": {"type": "api", "code": 230072}})}
            result = original_runner(args, cwd=cwd, timeout=timeout)
            applied.append(args)
            if case == "lost-readback":
                hidden.append(True)
            if case in {"lost-ack", "lost-readback"}:
                return {"returncode": 1, "stderr": "lost acknowledgement after applied edit"}
            return result
        return original_runner(args, cwd=cwd, timeout=timeout)
    restored = LarkPrivateConversations(controller=runtime, runtime_root=store.root.parent, runner=runner, cli_bin="lark-cli")
    before_requests, before_writes = capture.read_bytes(), copy.deepcopy(provider.writes)
    before_final = record["deliveries"][f"terminal.recovery.{recovery['version']}"]
    # A delivered source enters the existing scanner solely for presentation
    # debt. Core recovery/enqueue must not even be consulted on this route.
    monkeypatch.setattr(restored.core, "recover_failed_request", lambda *_a, **_k: pytest.fail("business replay"))
    monkeypatch.setattr(restored.core, "recover", lambda *_a, **_k: pytest.fail("business replay"))
    assert restored.reconcile() == 0
    after = _read_json(path)
    if case == "lost-readback":
        assert legacy_recovery_repairs(after) and len(applied) == 1
        assert body(provider, old_ref) == notice["text"]
        restarted = LarkPrivateConversations(controller=runtime, runtime_root=store.root.parent, runner=runner, cli_bin="lark-cli")
        restarted.reconcile()
        after = _read_json(path)
    held = case in {"changed-body", "changed-source", "revoked", "foreign-turn", "unknown-proof", "edit-limit"}
    if held:
        assert not applied and legacy_recovery_repairs(after)
    else:
        assert body(provider, old_ref) == notice["text"] and not legacy_recovery_repairs(after)
        if case == "rollover":
            assert body(provider, record["deliveries"]["progress"]["attempt"]["message_ref"]) == "本轮回复已回传，后续事项以完整回复为准。"
        restored.reconcile_request(path)
        assert len(applied) == (2 if case == "rollover" else 1)
    assert after["deliveries"]["terminal"] == notice
    assert after["deliveries"][f"terminal.recovery.{recovery['version']}"] == before_final
    assert provider.writes == before_writes and capture.read_bytes() == before_requests
    assert failed_path.read_bytes() == failed_bytes
