"""Local timing follows the existing native path, without granting authority."""

import hashlib
import json
import time

import pytest

from loopx.extensions.lark.goal_channel_transport import record_event_timing
from loopx.extensions.lark.goal_topic_runtime import stream_lark_goal_topic_profile
from test_chat_ordinary_project import ordinary  # noqa: F401
from test_lark_private_conversations import completed_group_stop, connect_group  # noqa: F401
from test_lark_realtime_dispatch import _start


def _timings(output):
    return [row for line in output.splitlines()
            if (row := json.loads(line)).get("schema_version") == "loopx_lark_event_timing_v0"]


def test_diagnostics_are_opt_in_and_never_copy_identity_or_content(monkeypatch, capsys):
    monkeypatch.delenv("LOOPX_LARK_EVENT_TIMING", raising=False)
    record_event_timing("private-profile", "om_private", "received")
    assert not capsys.readouterr().out
    monkeypatch.setenv("LOOPX_LARK_EVENT_TIMING", "1")
    record_event_timing("private-profile", "om_private", "received")
    output = capsys.readouterr().out
    assert "private-profile" not in output and "om_private" not in output
    row, = _timings(output)
    assert set(row) == {"schema_version", "event_ref", "stage", "wall_time", "monotonic_ns", "pid"}
    assert len(row["event_ref"]) == 64 and row["monotonic_ns"] > 0


@pytest.mark.parametrize("revoke", ["app", "sender", "grant"])
def test_timing_does_not_authorize_revoked_stop_delivery(completed_group_stop, monkeypatch, revoke):  # noqa: F811
    _, provider, transport, _, event, record = completed_group_stop
    monkeypatch.setenv("LOOPX_LARK_EVENT_TIMING", "1")
    if revoke == "app":
        provider.profile_apps["notes-app"] = "cli_other"
    elif revoke == "sender":
        provider.messages[event["message_id"]]["sender"] = {"id": "ou_other", "sender_type": "user"}
    else:
        transport.bindings.disconnect(record["binding_id"], expected_revision=transport.bindings.read()["revision"])
    assert transport.reconcile_request(transport.root / f"{record['request_ref']}.json") == 0
    assert not provider.writes and not provider.reaction_creates


@pytest.mark.parametrize("broken_sink", [False, True])
def test_native_topic_stop_retains_target_delivery_and_duplicate_safety(
    ordinary, tmp_path, monkeypatch, capsys, broken_sink,  # noqa: F811
):
    store, runtime, provider, transport = connect_group(ordinary)
    monkeypatch.setenv("LOOPX_LARK_EVENT_TIMING", "1")
    if broken_sink:
        def failed_print(*_args, **_kwargs):
            raise BrokenPipeError("synthetic unavailable diagnostic sink")
        monkeypatch.setattr("loopx.extensions.lark.goal_channel_transport.print", failed_print, raising=False)
    root = provider.topic("timed-root", "wait for interrupt")
    stop = provider.topic("timed-stop", "/stop", root=root["message_id"])
    snapshot = {"private_profiles": transport.profiles()}
    source_read = transport._group_source_pair

    def delayed_source(*args):
        time.sleep(.03)
        return source_read(*args)

    monkeypatch.setattr(transport, "_group_source_pair", delayed_source)

    def lines():
        yield json.dumps(root)
        deadline = time.monotonic() + 10
        while True:
            rows = transport.core.pending()
            if any(row.get("turn_id") and store.load_turn(row["session_id"], row["turn_id"])["status"] == "running" for row in rows):
                break
            assert time.monotonic() < deadline
            time.sleep(.01)
        yield json.dumps(stop)

    _consumer, options = _start(tmp_path, snapshot, lines(), lambda *_: pytest.fail("legacy model path"))
    options.update(profile="notes-app", private_admitter=lambda profile, event:
                   transport.admit(profile, event, defer_background_stop=True))
    try:
        result = stream_lark_goal_topic_profile(**options)
        assert result["event_count"] == 2
        native = next(row for row in transport.core.pending() if row.get("command") == "stop")
        assert native["status"] in {"prepared", "command_completed"} and native["target_recorded"] is True
        path = transport.root / f"{native['request_ref']}.json"
        assert transport.reconcile_request(path) == 1
        assert transport.reconcile_request(path) == 0
        assert json.loads(path.read_text())["status"] == "delivered"
        assert len([text for _, _, text in provider.topic_writes
                    if text == "已请求停止这条消息对应的执行。"]) == 1
        assert runtime.wait_for_turn(session_id=native["session_id"], turn_id=native["turn_id"], timeout_sec=5)["status"] == "interrupted"
        if not broken_sink:
            event_ref = hashlib.sha256(json.dumps(["notes-app", stop["message_id"]]).encode()).hexdigest()
            rows = [row for row in _timings(capsys.readouterr().out) if row["event_ref"] == event_ref]
            stages = {row["stage"]: row["monotonic_ns"] for row in rows}
            assert stages["received"] <= stages["lane_ready"] <= stages["dispatch_started"]
            assert stages["control_source_read_returned"] - stages["control_source_read_started"] >= 30_000_000
            assert stages["core_admission_returned"] <= stages["core_recovery_started"]
            assert stages["delivery_inbox_ready"] <= stages["outbound_source_check_started"]
            assert stages["outbound_source_check_returned"] <= stages["delivery_returned"]
            assert root["content"] not in json.dumps(rows)
    finally:
        runtime.close()
