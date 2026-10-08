"""Installed providers share native ownership; no HTTP registration or model."""
from types import SimpleNamespace
import threading
import json
from urllib.request import urlopen

import pytest
from test_chat_ordinary_project import ordinary  # noqa: F401

from loopx.capabilities.native_chat.transports import ChatConversationTransports


class Transport:
    supports_result_files = False

    def __init__(self, ref):
        self.transport_ref = ref
        self.calls = []
        self.available = True

    def observe(self):
        self.calls.append("observe")
        return {"transport_ref": self.transport_ref, "provider_ref": "a" * 24,
                "operator_ref": "b" * 24, "verified": self.available}

    def send_with_attempt(self, route, session, turn, text, record_attempt):
        self.calls.append(("send", route, session, turn, text))
        record_attempt({"message_ref": "frozen-provider-id"})
        return {"reply_verified": False, "verification_performed": False}

    def verify(self, route, session, turn, text, attempt):
        self.calls.append(("verify", route, attempt))
        return {"reply_verified": False, "verification_performed": True}

    def start(self, server):
        self.calls.append(("start", server))

    def close(self):
        self.calls.append("close")


def composition():
    default, external = Transport("lark-app"), Transport("external-owner")
    composed = ChatConversationTransports(observe_default=lambda ref: default.observe(), transports=(external,))
    composed.default_return = default
    session = {"session_id": "original-session", "channel_id": "original-channel",
               "steward_context": {"transport_ref": "external-owner"}}
    selected = {"binding": {"transport_ref": "external-owner"}, "channel_id": "original-channel"}
    composed.bindings = SimpleNamespace(session_context=lambda saved: selected)
    route = {"session_id": session["session_id"], "channel_id": session["channel_id"]}
    return composed, default, external, session, route


def test_observation_and_return_follow_original_transport_even_after_new_session():
    composed, default, external, session, route = composition()
    assert composed.observe("external-owner")["verified"] is True
    assert composed.observe("lark-app")["verified"] is True
    attempts = []
    composed.send_with_attempt(route, session, {}, "full late result", attempts.append)
    composed.verify(route, session, {}, "full late result", attempts[0])
    assert external.calls[-1] == ("verify", route, attempts[0])
    assert not any(isinstance(c, tuple) and c[0] == "send" for c in default.calls)
    web = {"session_id": "web", "channel_id": "legacy-group"}
    composed.send_with_attempt({}, web, {}, "legacy result", attempts.append)
    assert default.calls[-1][0] == "send"


@pytest.mark.parametrize("field", ["channel_id", "session_id"])
def test_changed_original_route_cannot_disclose_to_any_transport(field):
    composed, default, external, session, route = composition()
    route[field] = "other"
    with pytest.raises(ValueError, match="audience changed"):
        composed.send_with_attempt(route, session, {}, "private result", lambda value: None)
    assert default.calls == external.calls == []


def test_revocation_and_rotation_fence_send_and_readback():
    composed, default, external, session, route = composition()
    def revoked(saved):
        raise ValueError("revoked exact binding")
    composed.bindings.session_context = revoked
    for operation in [lambda: composed.send_with_attempt(route, session, {}, "result", lambda value: None),
                      lambda: composed.verify(route, session, {}, "result", {})]:
        with pytest.raises(ValueError, match="revoked"):
            operation()
    external.available = False
    with pytest.raises(ValueError, match="unverified"):
        composed.observe("external-owner")
    with pytest.raises(ValueError, match="unverified"):
        composed.observe("unknown-transport")
    assert default.calls == ["observe"]


def test_file_support_and_stop_are_provider_specific():
    composed, default, external, session, route = composition()
    route["result_attachments"] = [{"name": "private.txt"}]
    with pytest.raises(ValueError, match="does not support"):
        composed.send_with_attempt(route, session, {}, "result", lambda value: None)
    assert external.calls == []
    default.supports_result_files = True
    composed.bindings.session_context = lambda saved: {"binding": {"transport_ref": "lark-app"}, "channel_id": route["channel_id"]}
    composed.send_with_attempt(route, session, {}, "file result", lambda value: None)
    assert default.calls[-1][0] == "send"
    stopped = threading.Event()
    composed.cancelled = stopped.is_set
    stopped.set()
    assert external.cancelled() and default.cancelled()


def test_duplicate_reference_and_lifecycle_failure_do_not_leave_consumers():
    first, second = Transport("one"), Transport("two")
    with pytest.raises(ValueError, match="duplicate"):
        ChatConversationTransports(observe_default=lambda ref: {}, transports=(first, first))
    composed = ChatConversationTransports(observe_default=lambda ref: {}, transports=(first, second))
    def fail(server):
        raise OSError("provider startup failed")
    second.start = fail
    with pytest.raises(OSError, match="startup failed"):
        composed.start(object())
    composed.start(object())
    composed.close()
    assert first.calls[-1] == second.calls[-1] == "close"
    assert first.calls.count("close") == 1


def test_provider_close_failure_still_closes_other_consumers_and_preserves_start_error(caplog):
    first, second = Transport("one"), Transport("two")
    def fail_start(server):
        raise OSError("startup failure")
    def fail_close():
        second.calls.append("close")
        raise ValueError("private provider detail")
    second.start = fail_start
    second.close = fail_close
    composed = ChatConversationTransports(observe_default=lambda ref: {}, transports=(first, second))
    with pytest.raises(OSError, match="startup failure"):
        composed.start(object())
    composed.close()
    assert first.calls.count("close") == second.calls.count("close") == 1
    assert "ValueError" in caplog.text
    assert "private provider detail" not in caplog.text


def test_return_rechecks_actual_typed_binding_after_owner_disconnect(ordinary):  # noqa: F811
    from loopx.capabilities.native_chat.conversation_bindings import ChatConversationBindings
    store, runtime, contexts, _, _, _, workspace = ordinary
    external = Transport("external-owner")
    composed = ChatConversationTransports(observe_default=lambda ref: {}, transports=(external,))
    bindings = ChatConversationBindings(root=store.root, project_contexts=contexts, observe=composed.observe)
    composed.bindings = bindings
    contexts.conversation_bindings = bindings
    row = bindings.configure(transport_ref=external.transport_ref, project_ref=contexts.available()[0]["project_ref"],
        executor_endpoint_id="codex", context_kind="steward", goal_scope="selected")
    source = {"source_ref": "c" * 24, "sender_ref": row["operator_ref"], "private_human_message": True}
    selected = bindings.resolve(binding_id=row["binding_id"], **source)
    session = {"session_id": "exact-original", "channel_id": selected["channel_id"],
               "steward_context": selected["context"]}
    route = {"session_id": session["session_id"], "channel_id": session["channel_id"]}
    attempts = []
    composed.send_with_attempt(route, session, {}, "full result", attempts.append)
    assert attempts == [{"message_ref": "frozen-provider-id"}]
    bindings.disconnect(row["binding_id"], expected_revision=bindings.read()["revision"])
    before = len(external.calls)
    with pytest.raises(ValueError):
        composed.verify(route, session, {}, "full result", attempts[0])
    assert len(external.calls) == before


def test_real_chat_entrypoint_composes_one_store_controller_and_return_service(tmp_path, monkeypatch):
    import loopx.chat_server as chat
    from loopx.extensions.lark.cli_resolution import LarkCliResolution
    home = tmp_path / "home"
    registry = home / ".loopx" / "registry.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(json.dumps({"goals": []}))
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index.html").write_text("fixture")
    monkeypatch.setattr(chat.Path, "home", classmethod(lambda cls: home))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(chat, "build_lark_goal_topic_runtime_snapshot", lambda **kwargs: {})
    monkeypatch.setattr(chat, "resolve_lark_cli_for_runtime", lambda **kwargs: LarkCliResolution(None, False, "missing", None, "lark_cli_not_installed"))
    entered = threading.Event()
    captured = []
    external = Transport("external-owner")
    def start(server):
        captured.append(server)
        entered.set()
    external.start = start
    worker = threading.Thread(target=chat.serve_chat, kwargs={"registry_path": registry, "port": 0,
        "runtime_root_override": tmp_path / "runtime", "assets_dir": assets,
        "external_conversation_factories": (lambda server: external,), "codex_bin": "unavailable-fixture"}, daemon=True)
    worker.start()
    try:
        assert entered.wait(8)
        server = captured[0]
        assert server.manager_return_service.args[2] is server.chat_store
        assert server.manager_return_service.args[3] is server.conversation_transports
        assert server.conversation_transports.bindings is server.runtime_controller.project_contexts.conversation_bindings
        assert server.lark_private_conversations.core.controller is server.runtime_controller
        assert server.lark_private_conversations.core.actions is server.action_service
        with urlopen(f"http://127.0.0.1:{server.server_port}/healthz", timeout=2) as response:
            assert json.load(response)["ok"] is True
    finally:
        if captured:
            captured[0].shutdown()
        worker.join(8)
    assert not worker.is_alive()
    assert external.calls == ["close"]
