from __future__ import annotations

import hashlib
import json
import sys

import pytest

from loopx.control_plane.runtime import codex_app_delivery_observer as observer

BODY = "Do the synthetic work.\n先检查 quota。\n"
ENVELOPE = (
    "<heartbeat>\n  <automation_id>synthetic</automation_id>\n"
    "  <current_time_iso>2027-01-15T08:00:00.000Z</current_time_iso>\n"
    f"  <instructions>\n{BODY}\n  </instructions>\n</heartbeat>\n"
)
EXPECTED = dict(
    automation_id="synthetic",
    goal_id="goal",
    agent_id="agent",
    thread_id="thread",
    turn_id="turn",
)


def user(text):
    return {"type": "userMessage", "content": [{"type": "text", "text": text}]}


def observe(monkeypatch, items, **changes):
    turn = {"id": "turn", "startedAt": 1800000000, "items": items, **changes}
    monkeypatch.setattr(
        observer,
        "_read_thread",
        lambda *args: {"thread": {"id": "thread", "turns": [turn]}},
    )
    return observer.observe_codex_app_delivery(
        codex_bin="unused", expected=EXPECTED, observed_at_ms=1800000001000
    )


@pytest.mark.parametrize(
    "source",
    [
        user(ENVELOPE),
        {
            "type": "functionCallOutput",
            "namespace": "codex_app",
            "name": "automation_update",
            "output": ENVELOPE,
        },
    ],
)
def test_readback_hashes_received_body_and_observes_following_activity(
    monkeypatch, source
):
    receipt = observe(
        monkeypatch, [source, {"type": "agentMessage", "text": "working"}]
    )
    assert receipt["prompt_sha256"] == hashlib.sha256(BODY.encode()).hexdigest()
    assert receipt["agent_activity_observed"] is True
    assert receipt["turn_started_at_ms"] == 1800000000000
    assert BODY not in json.dumps(receipt, ensure_ascii=False)


@pytest.mark.parametrize(
    "items",
    [
        [],
        [user("ordinary message")],
        [user(ENVELOPE.replace("synthetic", "other"))],
        [user("original"), user(ENVELOPE)],
        [user(ENVELOPE), user(ENVELOPE)],
        [user("prefix" + ENVELOPE)],
        [
            {
                "type": "functionCallOutput",
                "namespace": "untrusted",
                "name": "automation_update",
                "output": ENVELOPE,
            }
        ],
    ],
)
def test_missing_wrong_or_ambiguous_initial_delivery_is_unverified(monkeypatch, items):
    receipt = observe(monkeypatch, items + [{"type": "agentMessage", "text": "done"}])
    assert receipt["prompt_sha256"] is None
    assert receipt["agent_activity_observed"] is False


@pytest.mark.parametrize(
    "items", [[user(ENVELOPE)], [{"type": "agentMessage"}, user(ENVELOPE)]]
)
def test_prior_activity_or_completed_status_does_not_prove_agent_started(
    monkeypatch, items
):
    assert (
        observe(monkeypatch, items, status="completed")["agent_activity_observed"]
        is False
    )


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"id": "other"}, "host_selected_turn_unavailable"),
        ({"itemsView": "notLoaded"}, "host_selected_turn_incomplete"),
    ],
)
def test_requires_selected_full_turn(monkeypatch, changes, reason):
    with pytest.raises(observer.HostObservationError, match=reason):
        observe(monkeypatch, [user(ENVELOPE)], **changes)


def test_transport_only_initializes_and_reads_selected_thread(tmp_path, monkeypatch):
    script = tmp_path / "fake.py"
    script.write_text("""import json, sys
first = json.loads(sys.stdin.readline())
assert first['method'] == 'initialize'
print(json.dumps({'id': 1, 'result': {}}), flush=True)
assert json.loads(sys.stdin.readline()) == {'method': 'initialized'}
request = json.loads(sys.stdin.readline())
assert request == {'id': 2, 'method': 'thread/read', 'params': {'threadId': 'thread', 'includeTurns': True}}
print(json.dumps({'id': 2, 'result': {'thread': {'id': 'thread', 'turns': []}}}), flush=True)
sys.stdin.read()
""")
    original = observer.subprocess.Popen
    monkeypatch.setattr(
        observer.subprocess,
        "Popen",
        lambda argv, **kwargs: original([sys.executable, str(script)], **kwargs),
    )
    assert observer._read_thread("codex", "thread")["thread"]["id"] == "thread"


@pytest.mark.parametrize(
    "code,reason",
    [
        ("import time; time.sleep(30)", "host_observer_timeout"),
        (
            "print('private-invalid-response', flush=True)",
            "host_observer_response_invalid",
        ),
        (
            "print('x' * (2 * 1024 * 1024 + 1), flush=True)",
            "host_observer_response_invalid",
        ),
        (
            """print('{"id": 1, "error": {"message": "private"}}', flush=True)""",
            "host_observer_read_failed",
        ),
    ],
)
def test_transport_is_bounded_and_errors_do_not_expose_host_data(
    monkeypatch, code, reason
):
    original = observer.subprocess.Popen
    children = []

    def launch(argv, **kwargs):
        child = original([sys.executable, "-c", code], **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(observer.subprocess, "Popen", launch)
    with pytest.raises(observer.HostObservationError) as exc:
        observer._read_thread("codex", "thread", timeout=0.3)
    assert str(exc.value) == reason
    assert all(child.poll() is not None for child in children)


@pytest.mark.parametrize("delivered", [True, False])
def test_native_app_server_cli_with_isolated_synthetic_session(
    tmp_path, monkeypatch, delivered
):
    """Opt-in real backend read; no model, scheduler or private session access."""
    import os
    import subprocess
    import time
    import uuid
    from datetime import datetime, timezone
    from pathlib import Path

    codex = os.environ.get("LOOPX_TEST_CODEX_BIN")
    if not codex:
        pytest.skip("set LOOPX_TEST_CODEX_BIN to qualify the installed app-server")
    home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(home))
    thread_id, turn_id = str(uuid.uuid4()), str(uuid.uuid4())
    now = int(time.time())
    timestamp = (
        datetime.fromtimestamp(now, timezone.utc).isoformat().replace("+00:00", "Z")
    )
    session = (
        home
        / "sessions"
        / datetime.fromtimestamp(now, timezone.utc).strftime("%Y/%m/%d")
        / f"rollout-{timestamp.replace(':', '-')}-{thread_id}.jsonl"
    )
    session.parent.mkdir(parents=True)
    received = ENVELOPE if delivered else "synthetic missing heartbeat"
    events = [
        (
            "session_meta",
            {
                "id": thread_id,
                "timestamp": timestamp,
                "cwd": str(tmp_path),
                "originator": "synthetic-canary",
                "cli_version": "0.153.4",
                "source": "cli",
                "model_provider": "openai",
            },
        ),
        (
            "event_msg",
            {
                "type": "task_started",
                "turn_id": turn_id,
                "started_at": now,
                "model_context_window": 100000,
                "collaboration_mode_kind": "default",
            },
        ),
        (
            "response_item",
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": received}],
            },
        ),
        (
            "event_msg",
            {
                "type": "user_message",
                "message": received,
                "images": [],
                "local_images": [],
                "text_elements": [],
            },
        ),
        (
            "response_item",
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "synthetic activity"}],
            },
        ),
        ("event_msg", {"type": "agent_message", "message": "synthetic activity"}),
        (
            "event_msg",
            {
                "type": "task_complete",
                "turn_id": turn_id,
                "last_agent_message": "synthetic activity",
            },
        ),
    ]
    session.write_text(
        "".join(
            json.dumps({"timestamp": timestamp, "type": kind, "payload": payload})
            + "\n"
            for kind, payload in events
        )
    )
    manifest = home / "automations" / "synthetic" / "automation.toml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        f'id = "synthetic"\nkind = "heartbeat"\nstatus = "ACTIVE"\ntarget_thread_id = "{thread_id}"\nprompt = {json.dumps(BODY)}\n'
    )
    before = manifest.read_bytes(), session.read_bytes()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.codex_app_apply_rrule",
            "--check-delivery",
            "--observe-host",
            "--codex-bin",
            codex,
            "--automation-id",
            "synthetic",
            "--goal-id",
            "goal",
            "--agent-id",
            "agent",
            "--scheduled-thread-id",
            thread_id,
            "--scheduled-turn-id",
            turn_id,
        ],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == (0 if delivered else 1), result.stdout
    assert json.loads(result.stdout)["ok"] is delivered
    assert BODY not in result.stdout
    assert result.stderr == ""
    assert before == (manifest.read_bytes(), session.read_bytes())
