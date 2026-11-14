"""Packaged UI against real Chat/Core; only provider identity/model are synthetic.

Everything lives in a disposable SQLite runtime. No personal App, source, Goal,
credentials or model account is read or contacted. Stdin controls source events.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import runpy
import socket
import sys
import tempfile
import threading
import time

import pytest

REPO = Path(__file__).resolve().parents[4]
BACKEND = Path(os.environ.get("LOOPX_CHAT_FIXTURE_BACKEND_ROOT", REPO)).resolve()
sys.path[:0] = [str(BACKEND), str(REPO), str(REPO / "tests/control_plane")]
from canonical_authority_fixture import isolate_sqlite_runtime, promoted_create_fixture  # noqa: E402
from loopx.chat_server import serve_chat  # noqa: E402


class SyntheticPrivateSource:
    transport_ref = "synthetic-private"
    cancelled = staticmethod(lambda: False)

    def __init__(self, root, *, steward=False, peer=False):
        self.root, self.verified = root, True
        self.steward, self.peer = steward, peer
        self.peer_provider = None
        default_compact_reply = "0" if os.environ.get("LOOPX_REPLY_BEFORE") == "1" else "1"
        self.compact_reply = os.environ.get("LOOPX_REPLY_COMPACT_EVENT", default_compact_reply) == "1"
        if peer:
            self.transport_ref = "synthetic-peer"

    def observe(self):
        provider = ("e" if self.peer else "c") * 24
        operator = ("f" if self.peer else "d") * 24
        if "--reply-context" in sys.argv and not self.peer:
            from loopx.extensions.lark.conversation_identity import identity_ref
            operator = identity_ref(provider, self.reply_event()["sender_id"])
        return {"transport_ref": self.transport_ref, "provider_ref": provider,
                "operator_ref": operator, "verified": self.verified, "bot_app_id": "cli_fixture"}

    def health_snapshot(self):
        return {"status": "listening"}

    def close(self):
        pass

    def start(self, server):
        runtime = server.runtime_controller
        runtime.codex_home = self.root / "synthetic-model-home"
        runtime.codex_home.mkdir(exist_ok=True)
        bindings = runtime.project_contexts.conversation_bindings
        fallback = "--steward-fallback" in sys.argv and not self.peer
        binding = bindings.configure(transport_ref=self.transport_ref,
            project_ref=runtime.project_contexts.available()[0]["project_ref"],
            executor_endpoint_id="antigravity" if fallback else "codex",
            context_kind="steward" if self.steward else "project", project_grant="workspace_read")
        source = {"source_ref": ("b" if self.peer else "a") * 24,
                  "sender_ref": ("f" if self.peer else "d") * 24, "private_human_message": True}
        if "--reply-context" in sys.argv and not self.peer:
            from loopx.extensions.lark.conversation_identity import lark_private_source
            source = lark_private_source(provider_ref=self.observe()["provider_ref"], event=self.reply_event())
        if fallback:
            scoped = bindings.resolve(binding_id=binding["binding_id"], **source)
            original = server.chat_store.create_session(goal_id="loopx-manager", agent_id="codex",
                adapter_kind="codex_app_server", upstream_thread_id="durable-thread", upstream_mode="chat",
                codex_home=str(runtime.codex_home), channel_id=scoped["channel_id"], steward_context=scoped["context"])
        else:
            original, _ = runtime.open_session(goal_id=None, agent_id="codex", objective="",
                work_dir=self.root / "project", mode="resume_latest",
                conversation_binding_id=binding["binding_id"], source_context=source)
        sid = original["session_id"]
        # Seed the canonical post-recovery state in this disposable fixture.
        # Core still checks its exact original context, allocation and history;
        # this scenario tests UI readback, not the quota transition worker.
        if fallback:
            original = server.chat_store.update_session(sid,
                manager_executor_allocation={"schema_version": "manager_executor_allocation_v0",
                    "selection_policy": "flexible", "configured_endpoint": "antigravity",
                    "executor_endpoint": "codex", "allocation_reason": "flexible_availability_fallback",
                    "executor_endpoint_source": "flexible_pool", "executor_endpoint_default_reason": "",
                    "model": "gpt-6.1-sol", "model_source": "vendor_default", "reasoning_effort": "high",
                    "configuration_revision": "synthetic-flexible-routing-v1",
                    "eligible_endpoints": ["antigravity", "codex"], "available": True},
                upstream_context_history=[{"agent_id": "antigravity", "thread_id": "synthetic-original-argon",
                    "agent_model_id": "gemini-4-argon-high", "manager_context_version": None}])
        if self.peer:
            server.chat_store.append_message(sid, role="agent", text="Different private steward: keep this source separate.")
            return
        server.chat_store.append_message(sid, role="user", text="Synthetic private conversation: review the research notes.")
        server.chat_store.append_message(sid, role="agent", text="The original conversation and its research notes are ready.")
        reply_evidence = {}
        if "--reply-context" in sys.argv:
            from loopx.control_plane.collaboration import conversation_reply_context
            from loopx.extensions.lark.conversation_identity import identity_ref
            from loopx.chat_store import _atomic_write_json
            event = self.reply_event()
            parent = "\n\n".join([
                "原要求：保留已有结果和失败记录。Original requirement: retain results and failed attempts.",
                "1. Inspect the evidence",
                "Keep the original records available.",
                "2. Compare the sources",
                "Read the relevant sections before deciding.",
                "3. Report the result",
                "An independent list can start later:",
                "7. A retained step\n42. The following step",
                "A zero-based list:",
                "0. Initial state",
                "- Preserve uncertainty.\n- Preserve failed attempts.",
                "```text\n9. This is literal code, not another list.\n```",
            ])
            body = "请比较这次修改，并保留原话题中的限制。Compare this change against the original constraints."
            request = identity_ref(self.observe()["provider_ref"], event["message_id"])
            record = {"event": event, "profile": self.transport_ref, "request_ref": request}
            if self.compact_reply:
                selected = bindings.resolve(binding_id=binding["binding_id"], **source, include_observation=True)
                frozen = self.capture_compact_reply(runtime, record, selected, body, parent)
            else:
                raw = {**event, "source_conversation_id": event["chat_id"], "reply_context": {
                    "message_id": event["parent_id"], "conversation_id": event["chat_id"], "content": parent},
                    "thread_context": {"conversation_id": event["chat_id"], "root_message_id": event["root_id"],
                        "thread_id": event["thread_id"], "messages": [
                            {"message_id": event["root_id"], "conversation_id": event["chat_id"], "thread_id": event["thread_id"],
                             "position": -1, "content": self.reply_background,
                             "sender": {"id": "ou_fixture_owner", "kind": "user"}},
                            {"message_id": event["parent_id"], "conversation_id": event["chat_id"], "thread_id": event["thread_id"],
                             "position": 0, "content": parent, "sender": {"id": "cli_fixture", "kind": "app"}},
                            {"message_id": event["message_id"], "conversation_id": event["chat_id"], "thread_id": event["thread_id"],
                             "position": 1, "content": body}]}}
                frozen = conversation_reply_context(raw)["context_text"] + "\n\nCurrent user message:\n" + body
                record.update(reply_context_message=frozen, reply_context_source_text=body)
            turn, _ = server.chat_store.create_turn(sid, client_turn_id=f"external-{request}", message=frozen, origin="lark")
            correlation = {"request_ref": request, "binding_id": binding["binding_id"], "source": source,
                           "session_id": sid, "turn_id": turn["turn_id"]}
            for directory, value in (("external-requests", {**correlation, "status": "accepted", "command": None, "message": frozen}),
                    ("lark-private-deliveries", {**record, **correlation, "status": "delivered"})):
                _atomic_write_json(server.chat_store.root / directory / f"{request}.json", value)
            server.chat_store.update_turn(sid, turn["turn_id"], status="completed")
            server.chat_store.release_active_turn(sid, turn["turn_id"])
            server.chat_store.append_message(sid, role="agent", text="已保留原要求，当前核验结果待进一步确认。", turn_id=turn["turn_id"])
            paths = [server.chat_store.root / directory / f"{request}.json"
                     for directory in ("external-requests", "lark-private-deliveries")]
            paths.extend([server.chat_store.sessions_root / sid / "messages.jsonl",
                          server.chat_store.sessions_root / sid / "turns" / f"{turn['turn_id']}.json"])
            reply_evidence = {path: path.read_bytes() for path in paths}
            bindings.select_app_steward(binding["binding_id"], expected_revision=bindings.read()["revision"])
        returned_files = {}
        if "--returned-files" in sys.argv:
            returned_files, reply_evidence = self.seed_returned_files(server, original)
            bindings.select_app_steward(binding["binding_id"], expected_revision=bindings.read()["revision"])
        history_images = {}
        if "--history-images" in sys.argv:
            def image(name, encoded):
                return server.chat_store.append_message(sid, role="user", text=f"Original {name}", origin="lark",
                    attachments=[{"id": name, "name": name + ".png", "mime_type": "image/png",
                                  "size": len(base64.b64decode(encoded)), "data_url": "data:image/png;base64," + encoded}])

            old = image("archived-image", "iVBORw0KGgoAAAANSUhEUgAAAAQAAAADCAIAAAA7ljmRAAAAEElEQVR4nGNQnLIKjhhwcgA6oBB17nqaVAAAAABJRU5ErkJggg==")
            for index in range(75):
                server.chat_store.append_message(sid, role="user", text=f"Original archived message {index:02d}.")
            current = image("latest-image", "iVBORw0KGgoAAAANSUhEUgAAAAMAAAACCAIAAAASFvFNAAAAEElEQVR4nGP4v0gdghjgLABwBAqxX1jWkgAAAABJRU5ErkJggg==")
            server.chat_store.append_message(sid, role="agent", text="Latest original result after archived images.")
            history_images = {"historyImages": {"old": old["message_id"], "current": current["message_id"]}}
            original_image_handler = server.RequestHandlerClass._serve_chat_history_image
            failed = False
            failure_lock = threading.Lock()

            def fail_first_image_read(handler, parts):
                nonlocal failed
                with failure_lock:
                    fail = parts[3] == sid and parts[5] == old["message_id"] and not failed
                    failed |= fail
                if fail:
                    handler._send_error("Fixture image temporarily unavailable", status=503)
                    return True
                return original_image_handler(handler, parts)

            server.RequestHandlerClass._serve_chat_history_image = fail_first_image_read
            journal = server.chat_store.sessions_root / sid / "messages.jsonl"
            reply_evidence[journal] = journal.read_bytes()
        other, _ = runtime.open_session(goal_id=None, agent_id="codex", objective="", mode="new",
            work_dir=self.root / "project", project_ref=binding["project_ref"])
        server.chat_store.append_message(other["session_id"], role="agent", text="Unrelated desktop conversation — must remain separate.")
        print(json.dumps({"origin": f"http://127.0.0.1:{server.server_port}",
            "bindingId": binding["binding_id"], "sessionId": sid, "ordinarySessionId": other["session_id"],
            **returned_files, **history_images}), flush=True)
        settings_handler = server.RequestHandlerClass._private_conversations
        history_handler = server.RequestHandlerClass._session_snapshot
        sessions_handler = server.RequestHandlerClass._list_sessions
        slow_page, slow_source = threading.Event(), threading.Event()

        def delayed_history(handler, selected_session):
            if selected_session == sid and slow_page.is_set():
                slow_page.clear()
                time.sleep(5.2)
            return history_handler(handler, selected_session)

        def delayed_sessions(handler):
            if slow_source.is_set() and "conversation_binding_id=" in handler.path:
                slow_source.clear()
                time.sleep(5.2)
            return sessions_handler(handler)

        server.RequestHandlerClass._session_snapshot = delayed_history
        server.RequestHandlerClass._list_sessions = delayed_sessions

        def control():
            for line in sys.stdin:
                command = line.strip()
                if command in {"deny", "recover"}:
                    self.verified = command == "recover"
                    result = {"source_verified": self.verified}
                elif command == "source":
                    accepted, _ = runtime.enqueue_turn(session_id=sid, message="A new synthetic message from the original private source.",
                        client_turn_id="synthetic-source-update", origin="lark", work_dir=self.root / "project", objective="")
                    turn = runtime.wait_for_turn(session_id=sid, turn_id=accepted["turn_id"], timeout_sec=15)
                    result = {"source_turn_status": turn["status"]}
                elif command == "close":
                    server.chat_store.update_session(sid, status="closed")
                    result = {"closed": True}
                elif command in {"slow-history", "slow-history-cancel"}:
                    slow_page.set()
                    result = {"slow_history": True}
                elif command == "slow-history-source":
                    slow_source.set()
                    result = {"slow_history_source": True}
                elif command == "replace-peer" and self.peer_provider is not None:
                    old = next(row for row in bindings.read()["bindings"] if row["transport_ref"] == self.peer_provider.transport_ref)
                    bindings.disconnect(old["binding_id"], expected_revision=bindings.read()["revision"])
                    self.peer_provider.start(server)
                    fresh = next(row for row in bindings.read()["bindings"] if row["transport_ref"] == self.peer_provider.transport_ref)
                    result = {"old_peer_binding_id": old["binding_id"], "new_peer_binding_id": fresh["binding_id"]}
                elif command == "revoke":
                    bindings.disconnect(binding["binding_id"], expected_revision=bindings.read()["revision"])
                    result = {"revoked": binding["binding_id"]}
                elif command in {"settings-unavailable", "settings-recover"}:
                    def unavailable(handler):
                        return handler._send_error("Synthetic connection settings are unavailable", status=503)
                    server.RequestHandlerClass._private_conversations = (
                        unavailable if command == "settings-unavailable" else settings_handler)
                    result = {"settings_available": command == "settings-recover"}
                elif fallback and command.startswith("fallback-"):
                    allocation = dict(original["manager_executor_allocation"])
                    if command == "fallback-invalid-allocation":
                        allocation["allocation_reason"] = "configured_preference"
                    elif command == "fallback-wrong-configured-endpoint":
                        allocation["configured_endpoint"] = "codex"
                    elif command != "fallback-restore":
                        raise ValueError("unknown synthetic fallback state")
                    server.chat_store.update_session(sid, manager_executor_allocation=allocation)
                    result = {"fallback_state": command}
                elif command == "populate":
                    for index in range(6):
                        server.chat_store.append_message(sid, role="user", text=f"Synthetic source request {index + 1}: 保留原任务与尚未确认的结果。")
                        server.chat_store.append_message(sid, role="agent", text=f"### 合成报告 {index + 1} / Synthetic report\n\n"
                            + "\n\n".join(f"**工作 {part + 1}**：已验证的结果保留来源；仍在处理的事项继续保留，不把监听状态当作完成。 "
                                "Verified results retain their source. Unconfirmed returns remain open for recovery."
                                for part in range(6)))
                    result = {"populated": True, "messages": len(server.chat_store.messages(sid))}
                elif command == "history":
                    for index in range(450):
                        server.chat_store.append_message(sid, role="agent",
                            text="Original full body. " * 1000 if index == 201 else f"Paged bound row {index}")
                    result = {"history_rows": len(server.chat_store.messages(sid))}
                elif command == "history-extra":
                    server.chat_store.append_message(sid, role="agent", text="A full new original result. " * 150)
                    result = {"history_rows": len(server.chat_store.messages(sid))}
                elif command == "history-rewrite":
                    journal = server.chat_store.sessions_root / sid / "messages.jsonl"
                    rows = [json.loads(line) for line in journal.read_text().splitlines()]
                    target = next(row for row in rows if row["text"] == "Paged bound row 10")
                    target["text"] = "Changed earlier original row 10"
                    journal.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
                    result = {"history_rows": len(rows)}
                elif command == "history-image-refresh" and history_images:
                    journal = server.chat_store.sessions_root / sid / "messages.jsonl"
                    rows = [json.loads(line) for line in journal.read_text().splitlines()]
                    archived = next(row for row in rows if row["message_id"] == history_images["historyImages"]["old"])
                    latest = next(row for row in rows if row["message_id"] == history_images["historyImages"]["current"])
                    for key in ("data_url", "size"):
                        archived["attachments"][0][key] = latest["attachments"][0][key]
                    journal.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
                    reply_evidence[journal] = journal.read_bytes()
                    result = {"history_image_refreshed": archived["message_id"]}
                elif command == "inspect":
                    saved = server.chat_store.load_session(sid)
                    result = {"sessions": len(server.chat_store.list_sessions()), "original_thread": saved["upstream_thread_id"],
                              "messages": server.chat_store.messages(sid),
                              "native_requests": len((self.root / "synthetic-requests.jsonl").read_text().splitlines())}
                    if reply_evidence:
                        result.update(model_turns=sum(json.loads(line).get("method") == "turn/start"
                            for line in (self.root / "synthetic-requests.jsonl").read_text().splitlines()),
                            reply_evidence_unchanged=all(path.read_bytes() == content for path, content in reply_evidence.items()))
                    if returned_files:
                        result["provider_sends"] = self.file_sender.calls
                elif command == "stop":
                    server.shutdown()
                    return
                else:
                    result = {"unknown_command": command}
                print(json.dumps(result), flush=True)
        threading.Thread(target=control, daemon=True).start()

    def seed_returned_files(self, server, session):
        """Publish once through the real result owner, then test read-only history."""
        from hashlib import sha256
        from loopx.capabilities.manager_context import _root, _write, POLICY_SCHEMA, register_ingress, deliver, acknowledge
        from loopx.capabilities.manager_context.roundtrip import report, drain

        root = server.runtime_controller.coordination_runtime_root
        store, registry = server.chat_store, server.registry_path
        sid = session["session_id"]
        turn, _ = store.create_turn(sid, client_turn_id="synthetic-files", origin="lark",
            message="请核对结果并返回原文件。Please return the original result files.")
        target = {"goal_id": "goal-a", "agent_id": "agent-a"}
        _write(_root(root) / "policy.json", {"schema_version": POLICY_SCHEMA, "sources": {
            session["channel_id"]: {"local_delivery_scope": "selected", "sender_ids": ["owner"], "targets": [target]}}})
        register_ingress(root, session_id=sid, client_turn_id=turn["client_turn_id"], channel=session["channel_id"],
            sender_id="owner", message=turn["message"], source_id="lark:om_fixture_files")
        receipt = deliver(root, registry, session=session, turn=turn, request=target, source_store=store)
        store.update_turn(sid, turn["turn_id"], status="completing", response={
            "message": "The receiver will return the original files.", "context_handoff_receipt": receipt})
        store.finalize_managed_turn_completion(sid, turn["turn_id"])
        acknowledge(root, "goal-a", "agent-a", receipt["request_id"], "adopt", "Verify the original files")
        files = {"结果.json": b'\xef\xbb\xbf{"result":"verified"}\r\n', "source.bin": b"\x00\xffbinary source\x00"}
        for name, raw in files.items():
            (self.root / "project" / name).write_bytes(raw)
        report(root, "goal-a", "agent-a", receipt["request_id"], "conclusion",
            "### 结果已核验 / Verified result\n\n原始结果和文件均已保存。The original results and files are saved.\n\n"
            "已核对结论、来源和失败记录。The result, sources and failed attempts were checked.",
            registry=registry, attachment_refs=list(files))

        class Sender:
            supports_result_files = True
            calls = 0

            def __call__(self, *_):
                self.calls += 1
                return {"ok": True, "reply_verified": True}

        self.file_sender = Sender()
        assert drain(root, registry, store, self.file_sender) == 1
        message = next(row for row in store.messages(sid) if row.get("origin") == "manager_followup")
        paths = list(_root(root).rglob("*.json")) + [store.sessions_root / sid / "messages.jsonl",
            store.sessions_root / sid / "turns" / f"{turn['turn_id']}.json"]
        return {"fileMessageId": message["message_id"], "files": [
            {"name": name, "sha256": sha256(raw).hexdigest(), "size": len(raw)} for name, raw in files.items()]}, {
                path: path.read_bytes() for path in paths}

    reply_background = ("### 话题背景 / Topic background\n\n"
                        + "完整上下文保持可读。Keep the original context readable. " * 35)

    def reply_event(self):
        parent = "om_fixture_parent"
        return {"message_id": "om_fixture_reply", "parent_id": parent,
                "root_id": parent if self.compact_reply else "om_fixture_root",
                **({} if self.compact_reply else {"thread_id": "omt_fixture_topic"}),
                "chat_id": "oc_fixture_private", "chat_type": "p2p",
                "sender_type": "user", "sender_id": "ou_fixture_owner", "message_type": "text"}

    def capture_compact_reply(self, runtime, record, selected, body, parent):
        from loopx.extensions.lark.private_conversations import LarkPrivateConversations

        event = record["event"]
        common = {"chat_id": event["chat_id"], "msg_type": "text", "thread_id": "omt_fixture_topic"}
        current = {**common, "message_id": event["message_id"], "content": body,
                   "parent_id": event["parent_id"], "root_id": event["root_id"],
                   "sender": {"id": event["sender_id"], "sender_type": "user"}, "thread_message_position": 1}
        earlier = {**common, "message_id": "om_fixture_earlier", "content": self.reply_background,
                   "sender": current["sender"], "thread_message_position": 0}
        quoted = {**common, "message_id": event["parent_id"], "content": parent,
                  "sender": {"id": "cli_fixture", "sender_type": "app"}, "thread_message_position": -1,
                  "thread_replies": [earlier, current]}

        def read(args, *_):
            if args[3:] == ["api", "GET", f"/open-apis/im/v1/messages/{event['message_id']}", "--as", "bot"]:
                payload = {"code": 0, "data": {"items": [current]}}
            else:
                assert args[3:7] == ["im", "+messages-mget", "--message-ids", event["parent_id"]], args
                payload = {"ok": True, "data": {"items": [quoted]}}
            return {"returncode": 0, "stdout": json.dumps(payload), "stderr": ""}

        transport = LarkPrivateConversations(controller=runtime, runtime_root=runtime.store.root.parent,
            runner=read, cli_bin="lark-cli")
        # Exercise the real provider-backed freeze before seeding Core history.
        # Opening the resulting page still does not require any model Turns.
        return transport._reply_context_message(transport.root / f"{record['request_ref']}.json",
            record, selected=selected, source_message=current, text=body)


def main():
    with tempfile.TemporaryDirectory(prefix="loopx-bound-ui-") as directory, pytest.MonkeyPatch.context() as patch:
        root = Path(directory)
        isolate_sqlite_runtime(root, patch)
        patch.setenv("CODEX_HOME", str(root / "synthetic-model-home"))
        registry, _, _ = promoted_create_fixture(root, provider="sqlite")
        fake = root / "synthetic-codex"
        model = runpy.run_path(str(REPO / "examples/loopx-chat-runtime-smoke.py"))["FAKE_CODEX"]
        model = model.replace('    method = request.get("method")',
            f'    with open({str(root / "synthetic-requests.jsonl")!r}, "a") as output:\n'
            '        output.write(json.dumps(request) + "\\n")\n    method = request.get("method")')
        fake.write_text(model)
        fake.chmod(0o700)
        steward = any(flag in sys.argv for flag in ("--steward", "--reply-context", "--steward-fallback", "--returned-files"))
        source = SyntheticPrivateSource(root, steward=steward)
        peer = SyntheticPrivateSource(root, steward=True, peer=True) if steward and not any(
            flag in sys.argv for flag in ("--reply-context", "--returned-files")) else None
        source.peer_provider = peer
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        serve_chat(registry_path=registry, runtime_root_override=root / "runtime", scan_roots=[root / "project"],
            codex_bin=str(fake), lark_cli_bin="missing-synthetic-lark", port=port,
            external_conversation_factories=((lambda _: source, lambda _: peer) if peer else (lambda _: source,)))


if __name__ == "__main__":
    main()
