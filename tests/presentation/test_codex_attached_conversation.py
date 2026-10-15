"""Real HTTP + SQLite/JSONL + a disposable Desktop-owner Unix protocol peer."""

import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
from types import SimpleNamespace

import pytest

from loopx.capabilities.native_chat import codex_attached as provider

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
        assert [message["text"] for message in initial["messages"]] == [
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
        assert request("POST", body(binding, "Changed content"))[0] == 409


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


def test_history_is_bounded_ignores_tools_and_private_paths_do_not_escape(
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
                                "output": "private-tool-output" * 512,
                            },
                        }
                    )
                    + "\n"
                )
        append_message(rollout, "assistant", "Visible recent result")
        status, result = request(query=query)
        assert status == 200 and result["bounded"] and result["before"] is not None
        assert [row["text"] for row in result["messages"]] == ["Visible recent result"]
        assert "private-tool-output" not in json.dumps(result)
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
