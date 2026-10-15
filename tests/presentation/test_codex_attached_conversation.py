"""Real HTTP + SQLite/JSONL + a disposable Desktop-owner Unix protocol peer."""

import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
from types import SimpleNamespace

import pytest

from loopx.capabilities.native_chat import codex_attached as provider
from loopx.capabilities.native_chat import codex_history as history

_spec = importlib.util.spec_from_file_location(
    "codex_attached_peer",
    Path(__file__).resolve().parents[2]
    / "examples/personal-workspace-browser/codex_attached_peer.py",
)
assert _spec and _spec.loader
_peer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_peer)
THREAD = _peer.THREAD
append_message = _peer.append_message
fixture = _peer.fixture


def target(request):
    status, result = request()
    assert status == 200
    binding = result["agents"][0]
    return {
        **binding,
        "goal_id": "sample-goal",
    }, f"?goal_id=sample-goal&agent_id=worker&binding_ref={binding['binding_ref']}"


def body(binding, text="Owner follow-up"):
    return {
        "goal_id": "sample-goal",
        "agent_id": "worker",
        "binding_ref": binding["binding_ref"],
        "operation_id": "same-operation",
        "text": text,
    }


def opener_spy(monkeypatch, *, platform="darwin", failure=None):
    calls = []

    def launch(argv, **options):
        calls.append((argv, options))
        if failure:
            raise failure

    monkeypatch.setattr(
        provider, "sys", SimpleNamespace(platform=platform), raising=False
    )
    monkeypatch.setattr(
        provider,
        "subprocess",
        SimpleNamespace(run=launch, SubprocessError=subprocess.SubprocessError),
        raising=False,
    )
    return calls


def open_body(binding):
    return {key: binding[key] for key in ("goal_id", "agent_id", "binding_ref")}


def test_explicit_open_uses_bound_local_thread_without_executing(tmp_path, monkeypatch):
    calls = opener_spy(monkeypatch)
    with fixture(tmp_path) as (request, _, methods, inputs, _, _):
        binding, query = target(request)
        assert request(query=query)[0] == 200
        assert calls == []  # Reading never opens another application.
        status, result = request(
            "POST", open_body(binding), query="", path="/api/chat/codex-attached/open"
        )
        assert status == 200
        assert result == {"ok": True, "requested": True}
        assert calls == [
            (
                [
                    "/usr/bin/open",
                    "-b",
                    "com.openai.codex",
                    f"codex://threads/{THREAD}",
                ],
                {"check": True, "timeout": 3, "capture_output": True},
            )
        ]
        assert inputs == []
        assert "thread-follower-start-turn" not in methods
        assert not (request.server.runtime_root / "native_chat").exists()


def test_reply_character_limit_accepts_full_unicode_input(tmp_path, monkeypatch):
    with fixture(tmp_path) as (request, _, _, inputs, _, _):
        binding, _ = target(request)
        text = "验" * 24000
        status, _ = request("POST", body(binding, text), query="")
        assert status == 200
        assert inputs[0]["turnStart"]["request"]["input"][0]["text"] == text
        assert request("POST", body(binding, text + "验"), query="")[0] == 409
        assert len(inputs) == 1


def test_open_rejects_forged_stale_archived_and_cross_origin(tmp_path, monkeypatch):
    calls = opener_spy(monkeypatch)
    with fixture(tmp_path) as (request, _, _, _, registry, home):
        binding, _ = target(request)
        payload = open_body(binding)
        for extra in ("url", "thread_id", "path", "command"):
            assert (
                request(
                    "POST",
                    {**payload, extra: "attacker"},
                    query="",
                    path="/api/chat/codex-attached/open",
                )[0]
                == 409
            )
        for changed in ({"agent_id": "other"}, {"binding_ref": "old"}):
            assert (
                request(
                    "POST",
                    {**payload, **changed},
                    query="",
                    path="/api/chat/codex-attached/open",
                )[0]
                == 409
            )
        assert (
            request(
                "POST",
                payload,
                query="",
                origin="https://foreign.example",
                path="/api/chat/codex-attached/open",
            )[0]
            == 403
        )
        with sqlite3.connect(home / "state_5.sqlite") as db:
            db.execute("UPDATE threads SET archived=1")
        assert (
            request("POST", payload, query="", path="/api/chat/codex-attached/open")[0]
            == 503
        )
        with sqlite3.connect(home / "state_5.sqlite") as db:
            db.execute("UPDATE threads SET archived=0")
        value = json.loads(registry.read_text())
        value["goals"][0]["coordination"]["thread_agent_bindings"][0]["thread_id"] = (
            "other"
        )
        registry.write_text(json.dumps(value))
        assert (
            request("POST", payload, query="", path="/api/chat/codex-attached/open")[0]
            == 409
        )
        assert calls == []


@pytest.mark.parametrize(
    "platform,failure",
    [
        ("linux", None),
        ("darwin", OSError("unavailable")),
        ("darwin", subprocess.TimeoutExpired("open", 3)),
        ("darwin", subprocess.CalledProcessError(1, "open")),
    ],
)
def test_open_host_failure_is_explicit(tmp_path, monkeypatch, platform, failure):
    calls = opener_spy(monkeypatch, platform=platform, failure=failure)
    with fixture(tmp_path) as (request, _, _, inputs, _, _):
        binding, _ = target(request)
        status, result = request(
            "POST", open_body(binding), query="", path="/api/chat/codex-attached/open"
        )
        assert status == 503
        assert result["ok"] is False
        assert inputs == []
        assert len(calls) == (0 if platform == "linux" else 1)


def test_open_validates_uuid_and_rechecks_binding_before_dispatch(
    tmp_path, monkeypatch
):
    calls = opener_spy(monkeypatch)
    for value in ("--args", "11111111111141118111111111111111", THREAD + "/other"):
        with pytest.raises((ValueError, provider.DesktopUnavailable)):
            provider.open_conversation(value)
    with fixture(tmp_path) as (request, _, _, _, registry, _):
        binding, _ = target(request)
        from loopx.presentation import codex_attached_api as api

        locate = api.locate_thread

        def changed_during_lookup(thread_id):
            result = locate(thread_id)
            value = json.loads(registry.read_text())
            value["goals"][0]["coordination"]["thread_agent_bindings"] = []
            registry.write_text(json.dumps(value))
            return result

        monkeypatch.setattr(api, "locate_thread", changed_during_lookup)
        assert (
            request(
                "POST",
                open_body(binding),
                query="",
                path="/api/chat/codex-attached/open",
            )[0]
            == 409
        )
        assert calls == []


def test_original_history_incremental_append_and_idempotent_owner_input(
    tmp_path, monkeypatch
):
    with fixture(tmp_path) as (
        request,
        rollout,
        methods,
        inputs,
        _,
        _home,
    ):
        binding, query = target(request)
        status, initial = request(query=query)
        assert status == 200 and initial["can_reply"]
        assert [
            message["text"]
            for message in initial["messages"]
            if message["role"] != "activity"
        ] == [
            "Original request",
            "Original result",
        ]
        assert request(query=query + "&revision=" + initial["revision"])[1]["unchanged"]
        append_message(rollout, "assistant", "Later result")
        assert (
            request(query=query + "&revision=" + initial["revision"])[1]["messages"][
                -1
            ]["text"]
            == "Later result"
        )
        assert request("POST", body(binding))[1]["status"] == "delivered"
        assert request("POST", body(binding))[1]["turn_id"] == "original-native-turn"
        assert (
            request(query=query + "&operation_id=same-operation")[1]["status"]
            == "delivered"
        )
        assert len(inputs) == 1
        assert inputs[0]["turnStart"]["context"] == {
            "attachments": [],
            "commentAttachments": [],
            "inheritThreadSettings": True,
            "useAppServerPermissionDefault": True,
        }
        assert set(methods) == {
            "initialize",
            "thread-owner-discovery",
            "thread-follower-start-turn",
        }
        status, rejected = request("POST", body(binding, "Changed content"))
        assert status == 409
        assert (
            "input_not_submitted" not in rejected
        )  # This id already caused an effect.


def test_uncertain_delivery_never_replays_the_effect(tmp_path, monkeypatch):
    with fixture(tmp_path, uncertain=True) as (
        request,
        _,
        _,
        inputs,
        _,
        _home,
    ):
        binding, query = target(request)
        assert request("POST", body(binding))[1]["status"] == "pending"
        assert request("POST", body(binding))[1]["status"] == "pending"
        assert (
            request(query=query + "&operation_id=same-operation")[1]["status"]
            == "pending"
        )
        assert len(inputs) == 1


@pytest.mark.parametrize("busy", [True, False], ids=["busy", "unknown"])
def test_pre_dispatch_rejection_proves_no_input_and_creates_no_receipt(tmp_path, busy):
    with fixture(tmp_path) as (request, rollout, methods, inputs, _, _):
        binding, query = target(request)
        rollout.write_text(
            json.dumps({"type": "event_msg", "payload": {"type": "task_started"}})
            + "\n"
            if busy
            else ""
        )
        status, rejected = request("POST", body(binding))
        assert status == 409
        assert rejected["ok"] is False
        assert rejected["input_not_submitted"] is True
        assert rejected["operation_id"] == "same-operation"
        assert request(query=query + "&operation_id=same-operation")[0] == 404
        assert inputs == [] and "thread-follower-start-turn" not in methods


def test_busy_stale_binding_foreign_origin_and_unbound_agent_create_no_input(
    tmp_path, monkeypatch
):
    with fixture(tmp_path) as (
        request,
        rollout,
        _,
        inputs,
        registry,
        _home,
    ):
        binding, query = target(request)
        assert request("POST", body(binding), origin="https://example.com")[0] == 403
        assert (
            request(query=query.replace("agent_id=worker", "agent_id=foreign"))[0]
            == 409
        )
        with rollout.open("a") as stream:
            stream.write(
                json.dumps(
                    {
                        "type": "event_msg",
                        "timestamp": "2026-01-01T00:00:02Z",
                        "payload": {"type": "task_started"},
                    }
                )
                + "\n"
            )
        assert request("POST", body(binding))[0] == 409
        saved = json.loads(registry.read_text())
        saved["goals"][0]["coordination"]["thread_agent_bindings"][0]["thread_id"] = (
            "22222222-2222-4222-8222-222222222222"
        )
        registry.write_text(json.dumps(saved))
        assert request(query=query)[0] == 409
        assert request("POST", body(binding))[0] == 409
        assert not inputs


def test_owner_history_includes_tools_with_bounded_pages_and_offline_reading(
    tmp_path, monkeypatch
):
    with fixture(tmp_path) as (request, rollout, _, _, _, home):
        binding, query = target(request)
        with rollout.open("a") as stream:
            for _ in range(400):
                stream.write(
                    json.dumps(
                        {
                            "type": "response_item",
                            "payload": {
                                "type": "function_call_output",
                                "output": "tool-result" * 512,
                            },
                        }
                    )
                    + "\n"
                )
        append_message(rollout, "assistant", "Visible recent result")
        status, result = request(query=query)
        assert status == 200 and result["bounded"] and result["before"] is not None
        assert result["messages"][-1]["text"] == "Visible recent result"
        assert result["messages"][0]["role"] == "tool"
        assert len(result["messages"]) == 100
        assert str(home) not in json.dumps(result)
        (home / "ipc" / "ipc.sock").chmod(0o666)
        status, readonly = request(query=query)
        assert status == 200 and not readonly["can_reply"] and not readonly["connected"]
        assert request("POST", body(binding))[0] == 503


def test_history_pages_do_not_skip_messages_at_the_display_limit(tmp_path):
    rollout = tmp_path / "history.jsonl"
    for number in range(235):
        append_message(rollout, "assistant", f"Message {number}")
    newest = provider.read_messages(rollout)
    middle = provider.read_messages(rollout, before=newest["before"])
    oldest = provider.read_messages(rollout, before=middle["before"])
    assert oldest["before"] is None
    assert [
        row["text"] for page in [oldest, middle, newest] for row in page["messages"]
    ] == [f"Message {number}" for number in range(235)]


def test_replaced_rollout_invalidates_revision_and_offset_identities(tmp_path):
    rollout = tmp_path / "history.jsonl"
    append_message(rollout, "assistant", "Old result")
    old = provider.read_messages(rollout)
    replacement = tmp_path / "replacement.jsonl"
    append_message(replacement, "assistant", "New result")
    replacement.replace(rollout)
    new = provider.read_messages(rollout)
    assert old["source_ref"] != new["source_ref"]
    assert old["revision"] != new["revision"]
    assert [row["text"] for row in new["messages"]] == ["New result"]


def test_unbound_goal_does_not_probe_native_conversations(tmp_path, monkeypatch):
    with fixture(tmp_path) as (request, _, methods, inputs, registry, _):
        saved = json.loads(registry.read_text())
        saved["goals"][0]["coordination"]["thread_agent_bindings"] = []
        registry.write_text(json.dumps(saved))
        assert request()[1] == {"ok": True, "agents": []}
        assert not methods and not inputs


def append_record(path, kind, payload):
    with path.open("a") as stream:
        stream.write(json.dumps({"type": kind, "payload": payload}) + "\n")


def test_work_records_cover_tools_and_lifecycle_without_hidden_context(tmp_path):
    path = tmp_path / "history.jsonl"
    for role in ("system", "developer"):
        append_message(path, role, "hidden instruction")
    append_record(
        path,
        "response_item",
        {
            "type": "message",
            "role": "assistant",
            "channel": "analysis",
            "content": [{"type": "output_text", "text": "hidden reasoning"}],
        },
    )
    append_record(
        path, "response_item", {"type": "reasoning", "summary": "hidden reasoning"}
    )
    append_record(path, "event_msg", {"type": "task_started"})
    append_record(
        path,
        "response_item",
        {
            "type": "function_call",
            "name": "exec",
            "call_id": "call-1",
            "arguments": "run validation",
        },
    )
    append_record(
        path,
        "response_item",
        {
            "type": "function_call_output",
            "call_id": "call-1",
            "output": "exit 1: test failed",
        },
    )
    append_record(
        path,
        "response_item",
        {"type": "custom_tool_call", "name": "patch", "input": "repair input"},
    )
    append_record(
        path,
        "response_item",
        {"type": "custom_tool_call_output", "output": "repair saved"},
    )
    append_record(path, "event_msg", {"type": "turn_aborted"})
    rows = history.read_messages(path)["messages"]
    assert [row["kind"] for row in rows] == [
        "task_started",
        "function_call",
        "function_call_output",
        "custom_tool_call",
        "custom_tool_call_output",
        "turn_aborted",
    ]
    assert rows[1]["call_id"] == rows[2]["call_id"] == "call-1"
    assert rows[2]["text"] == "exit 1: test failed"
    assert "hidden" not in json.dumps(rows)


def test_full_unicode_content_is_available_in_bounded_http_pages(tmp_path):
    with fixture(tmp_path) as (request, rollout, methods, inputs, _, _):
        _, query = target(request)
        # Larger than the history window, including astral Unicode characters.
        text = "🧭验证\n" * 120000 + "END OF COMPLETE RESULT"
        append_message(rollout, "assistant", text)
        _, view = request(query=query)
        row = view["messages"][-1]
        assert row["truncated"] and len(row["text"]) == history.CONTENT_CHARS
        offset, parts = len(row["text"]), [row["text"]]
        detail = f"&entry_id={row['id']}&source_ref={view['source_ref']}&content_ref={row['content_ref']}"
        while True:
            status, page = request(query=query + detail + f"&content_offset={offset}")
            assert status == 200
            parts.append(page["text"])
            offset = page["next_offset"]
            if not page["has_more"]:
                break
        assert "".join(parts) == text
        assert (
            request(query=query + detail.replace(view["source_ref"], "stale"))[0] == 409
        )
        assert (
            request(query=query + detail.replace(row["content_ref"], "stale"))[0] == 409
        )
        assert (
            request(
                query=query.replace("agent_id=worker", "agent_id=foreign") + detail
            )[0]
            == 409
        )
        assert not inputs and "thread-follower-start-turn" not in methods


def test_forward_catchup_has_no_burst_gap_and_retries_partial_append(tmp_path):
    path = tmp_path / "history.jsonl"
    append_message(path, "assistant", "initial")
    first = history.read_messages(path)
    for index in range(235):
        append_message(path, "assistant", f"new {index}")
    raw = json.dumps(
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "partial complete"}],
            },
        }
    ).encode()
    with path.open("ab") as stream:
        stream.write(raw[:40])
    cursor, rows = first["after"], []
    while True:
        page = history.read_messages(
            path, after=cursor, expected_source=first["source_ref"]
        )
        rows.extend(page["messages"])
        cursor = page["after"]
        if not page["has_more"]:
            break
    assert [row["text"] for row in rows] == [f"new {i}" for i in range(235)]
    assert len({row["id"] for row in rows}) == 235
    with path.open("ab") as stream:
        stream.write(raw[40:] + b"\n")
    assert [
        row["text"] for row in history.read_messages(path, after=cursor)["messages"]
    ] == ["partial complete"]
    replacement = tmp_path / "new.jsonl"
    append_message(replacement, "assistant", "replacement")
    replacement.replace(path)
    page = history.read_messages(
        path, after=cursor, expected_source=first["source_ref"]
    )
    assert page["reset"] and [row["text"] for row in page["messages"]] == [
        "replacement"
    ]


@pytest.mark.parametrize("read_mode", ["history", "content", "unchanged"])
def test_reads_recheck_binding_after_native_lookup(tmp_path, monkeypatch, read_mode):
    from loopx.presentation import codex_attached_api as api

    with fixture(tmp_path) as (request, _, _, inputs, registry, _):
        _, query = target(request)
        _, view = request(query=query)
        if read_mode == "content":
            row = view["messages"][-1]
            query += f"&entry_id={row['id']}&source_ref={view['source_ref']}&content_ref={row['content_ref']}"
        elif read_mode == "unchanged":
            query += "&revision=" + view["revision"]
        locate = api.locate_thread

        def changed_during_lookup(thread_id):
            result = locate(thread_id)
            saved = json.loads(registry.read_text())
            saved["goals"][0]["coordination"]["thread_agent_bindings"] = []
            registry.write_text(json.dumps(saved))
            return result

        monkeypatch.setattr(api, "locate_thread", changed_during_lookup)
        status, result = request(query=query)
        assert status == 409 and result["ok"] is False
        assert "Original result" not in json.dumps(result)
        assert not inputs


def test_credential_fields_withheld_and_oversize_gap_is_not_silent(
    tmp_path, monkeypatch
):
    path = tmp_path / "history.jsonl"
    append_record(
        path,
        "response_item",
        {"type": "function_call_output", "output": 'api_key="synthetic-value"'},
    )
    page = history.read_messages(path)
    row = page["messages"][0]
    assert row["redacted"] and "synthetic-value" not in json.dumps(row)
    detail = history.read_content(
        path,
        entry_id=int(row["id"]),
        offset=0,
        expected_source=page["source_ref"],
        content_ref=row["content_ref"],
    )
    assert detail["redacted"] and "synthetic-value" not in detail["text"]
    monkeypatch.setattr(history, "RECORD_BYTES", 100)
    page = history.read_messages(path)
    assert page["messages"][0]["unavailable"] == "record_exceeds_read_limit"
    assert page["after"] == path.stat().st_size
    with pytest.raises(ValueError, match="unavailable"):
        history.read_content(
            path,
            entry_id=int(row["id"]),
            offset=0,
            expected_source=page["source_ref"],
            content_ref=row["content_ref"],
        )


def test_native_tool_text_blocks_are_readable_and_content_pages_are_lossless(tmp_path):
    path = tmp_path / "history.jsonl"
    text = "🧭验证\n" * 7000
    append_record(
        path,
        "response_item",
        {
            "type": "custom_tool_call_output",
            "output": [
                {"type": "text", "text": text},
                {"type": "text", "text": "Complete result"},
            ],
        },
    )
    page = history.read_messages(path)
    row = page["messages"][0]
    assert row["text"] == text[: history.CONTENT_CHARS]
    remaining = history.read_content(
        path,
        entry_id=int(row["id"]),
        offset=len(row["text"]),
        expected_source=page["source_ref"],
        content_ref=row["content_ref"],
    )
    assert row["text"] + remaining["text"] == text + "\nComplete result"
    # Unknown blocks or extra metadata must not disappear in text normalization.
    output = [
        {"type": "text", "text": "visible", "annotations": ["source"]},
        {"type": "image", "description": "unrendered"},
    ]
    append_record(
        path, "response_item", {"type": "custom_tool_call_output", "output": output}
    )
    assert json.loads(history.read_messages(path)["messages"][-1]["text"]) == output
    append_record(
        path,
        "response_item",
        {
            "type": "custom_tool_call_output",
            "output": [{"type": "text", "text": "api_key=sk-" + "a" * 40}],
        },
    )
    assert history.read_messages(path)["messages"][-1]["redacted"] is True
