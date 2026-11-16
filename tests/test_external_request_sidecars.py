"""Request journals must not parse durable lock diagnostics as business rows."""

import json
import pytest
from loopx.capabilities.native_chat.external_conversations import (
    ChatExternalConversations,
)
from loopx.extensions.lark.private_conversations import LarkPrivateConversations
from loopx.file_lock import exclusive_file_lock
from test_chat_ordinary_project import ordinary  # noqa: F401
from test_lark_private_conversations import connect


@pytest.mark.parametrize("reader", ["core", "delivery", "health"])
def test_request_readers_ignore_lock_diagnostics_and_keep_business_rows(
    tmp_path, reader
):
    request = tmp_path / ("a" * 24 + ".json")
    row = {
        "request_ref": "a" * 24,
        "binding_id": "b" * 24,
        "status": "accepted",
        "deliveries": {},
    }
    request.write_text(json.dumps(row), encoding="utf-8")
    # A retained Windows sidecar is also readable on a later POSIX checkout.
    request.with_name(request.name + ".lock.holder.json").write_text(
        json.dumps({"schema_version": "file_lock_holder_v0", "pid": 1}),
        encoding="utf-8",
    )
    transport = object.__new__(LarkPrivateConversations)
    transport.root = tmp_path
    core = object.__new__(ChatExternalConversations)
    core.root = tmp_path
    with exclusive_file_lock(request, operation="request_journal_regression"):
        if reader == "core":
            assert core.pending() == [row]
        elif reader == "delivery":
            assert transport.pending_delivery_paths() == [request]
        else:
            assert transport.health() == {
                "b" * 24: {"pending_count": 1, "recovery_count": 0}
            }
    assert (
        request.exists()
        and request.with_name(request.name + ".lock.holder.json").exists()
    )


def test_private_status_admission_reconciles_original_native_request_with_sidecars(
    ordinary,  # noqa: F811
):  # noqa: F811
    store, runtime, provider, transport = connect(ordinary)
    try:
        event = provider.event("notes-app", "sidecar_status", "/status")
        assert transport.admit("steward-app", event)["status"] == "audience_rejected"
        assert transport.core.pending() == []
        admitted = transport.admit("notes-app", event)
        assert admitted["status"] == "command_recorded"
        requests = transport.core.pending()
        assert len(requests) == 1 and requests[0]["command"] == "status"
        original = requests[0]["request_ref"]
        assert transport.health()[requests[0]["binding_id"]]["pending_count"] == 1
        assert transport.reconcile() == 1
        assert transport.pending_delivery_paths() == []
        assert (
            len(transport.core.pending()) == 1
            and transport.core.pending()[0]["request_ref"] == original
        )
        assert store.list_sessions() == []
        before = list(provider.writes)
        assert transport.reconcile() == 0 and provider.writes == before
        assert not provider.calls or all(
            "app-server" not in call for call in provider.calls
        )
    finally:
        runtime.close()
