"""Update the real store/activation boundary without stopping a real service."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import plistlib
import shlex
import subprocess
import threading
from unittest import mock

import pytest

from loopx.chat_store import ChatSessionStore
from loopx.chat_ingress import chat_runtime_activation_lock
from loopx.runtime_activation import restart_services_for_runtime_activation
from loopx.self_update import execute_update_plan, render_update_plan_markdown


@pytest.fixture
def services(tmp_path, monkeypatch):
    monkeypatch.setattr("loopx.runtime_activation.sys.platform", "darwin")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.delenv("LOOPX_BIN_DIR", raising=False)
    agents = tmp_path / "Library/LaunchAgents"
    agents.mkdir(parents=True)
    runtime = tmp_path / "runtime with spaces"
    route = ["loopx", "--runtime-root", str(runtime), "chat"]
    (agents / "com.loopx.chat.plist").write_bytes(
        plistlib.dumps({"ProgramArguments": route})
    )
    (agents / "com.loopx.status.plist").write_bytes(
        plistlib.dumps({"ProgramArguments": ["loopx", "serve-status"]})
    )
    calls = []
    original_run = subprocess.run

    def launchctl(args, **kwargs):
        if args[0] != "launchctl":
            return original_run(args, **kwargs)
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("loopx.runtime_activation.subprocess.run", launchctl)
    return runtime, agents, calls


def session(store):
    return store.create_session(
        goal_id="fixture",
        agent_id="fixture",
        adapter_kind="fake",
        upstream_thread_id="fixture",
        upstream_mode="managed",
    )["session_id"]


def activate():
    return restart_services_for_runtime_activation(
        changes_applied=True, runtime_ready=True
    )


def content_hashes(root):
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file() and p.suffix in {".json", ".jsonl"}
    }


@pytest.mark.parametrize(
    "state", ["queued", "starting", "running", "completing", "interrupting"]
)
def test_update_never_restarts_accepted_work_or_rewrites_its_input(services, state):
    runtime, _agents, calls = services
    store = ChatSessionStore(runtime)
    sid = session(store)
    turn, _ = store.create_turn(
        sid, client_turn_id="original", message="original request"
    )
    store.update_turn(sid, turn["turn_id"], status=state)
    before = content_hashes(store.root)
    result = activate()
    assert (
        result["activation_pending"] and result["restart_reason"] == "chat_work_pending"
    )
    assert result["restarted_services"] == [] and len(result["deferred_services"]) == 2
    assert not calls
    assert content_hashes(store.root) == before


def test_queue_without_an_active_pointer_still_defers_until_original_work_finishes(
    services,
):
    runtime, _agents, calls = services
    store = ChatSessionStore(runtime)
    sid = session(store)
    queued, _ = store.create_queued_turn(
        sid, client_turn_id="queued", message="later input"
    )
    assert not store.load_session(sid).get("active_turn_id")
    assert activate()["restart_status"] == "deferred"
    assert not calls
    # The original consumer, represented here by the real store's terminal write,
    # finishes the same Turn. Activation never creates or replays a business Turn.
    store.update_turn(sid, queued["turn_id"], status="completed")
    assert activate()["restart_status"] == "restarted"
    assert len(calls) == 2
    assert store.load_turn(sid, queued["turn_id"])["message"] == "later input"


@pytest.mark.parametrize(
    "damage", ["session", "turn", "active_missing", "unknown_status", "invalid_status"]
)
def test_unreadable_or_inconsistent_work_fails_closed(services, damage):
    runtime, _agents, calls = services
    store = ChatSessionStore(runtime)
    sid = session(store)
    turn, _ = store.create_turn(sid, client_turn_id="original", message="preserve me")
    if damage == "session":
        store._session_path(sid).write_text("{broken")
    elif damage == "turn":
        store._turn_path(sid, turn["turn_id"]).write_text("{broken")
    elif damage == "active_missing":
        store._turn_path(sid, turn["turn_id"]).unlink()
    elif damage == "unknown_status":
        store.update_turn(sid, turn["turn_id"], status="unrecognized")
    else:
        store.update_turn(sid, turn["turn_id"], status=[])
    before = content_hashes(store.root)
    result = activate()
    assert result["restart_status"] == "deferred"
    assert result["restart_reason"] == "chat_state_unverified"
    assert not calls and content_hashes(store.root) == before


@pytest.mark.parametrize("wrapper", [False, True])
def test_idle_service_uses_its_explicit_route_including_shell_wrapper(
    services, wrapper
):
    runtime, agents, calls = services
    store = ChatSessionStore(runtime)
    session(store)
    if wrapper:
        command = shlex.join(
            ["/synthetic/loopx", "--runtime-root", str(runtime), "chat"]
        )
        (agents / "com.loopx.chat.plist").write_bytes(
            plistlib.dumps(
                {
                    "ProgramArguments": [
                        "/bin/zsh",
                        "-c",
                        f"export PATH=/synthetic; exec {command}",
                    ],
                }
            )
        )
    assert activate()["activation_pending"] is False
    assert {call[-1].rsplit("/", 1)[-1] for call in calls} == {
        "com.loopx.chat",
        "com.loopx.status",
    }


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["loopx", "chat"],
        ["loopx", "--runtime-root", "$ROOT", "chat"],
        ["loopx", "--runtime-root", "relative", "chat"],
    ],
)
def test_unknown_service_route_never_falls_back_to_an_empty_default_store(
    services, arguments
):
    _runtime, agents, calls = services
    (agents / "com.loopx.chat.plist").write_bytes(
        plistlib.dumps({"ProgramArguments": arguments})
    )
    assert activate()["restart_reason"] == "chat_state_unverified"
    assert not calls


@pytest.mark.parametrize("queue", [False, True])
def test_restart_holds_admission_fence_until_process_replacement(
    services, monkeypatch, queue
):
    runtime, _agents, calls = services
    store = ChatSessionStore(runtime)
    sid = session(store)
    replacing, release, admitted, attempted = [threading.Event() for _ in range(4)]
    results, errors = [], []
    original_run = subprocess.run

    def launchctl(args, **kwargs):
        if args[0] != "launchctl":
            return original_run(args, **kwargs)
        calls.append(args)
        replacing.set()
        assert release.wait(5)
        return subprocess.CompletedProcess(args, 0, "", "")

    def accept_input():
        attempted.set()
        try:
            method = store.create_queued_turn if queue else store.create_turn
            method(sid, client_turn_id="during-update", message="do not lose input")
            admitted.set()
        except Exception as error:
            errors.append(error)

    monkeypatch.setattr("loopx.runtime_activation.subprocess.run", launchctl)
    update = threading.Thread(target=lambda: results.append(activate()))
    writer = threading.Thread(target=accept_input)
    update.start()
    try:
        assert replacing.wait(3)
        writer.start()
        assert attempted.wait(2)
        assert not admitted.wait(0.15)
        assert store.turn_for_client(sid, "during-update") is None
    finally:
        release.set()
        update.join(5)
        if writer.ident is not None:
            writer.join(5)
    assert not update.is_alive() and not writer.is_alive()
    assert not errors and admitted.is_set()
    assert results[0]["restart_status"] == "restarted"
    assert activate()["restart_reason"] == "chat_work_pending"


def test_contended_admission_is_not_treated_as_idle(services, monkeypatch):
    runtime, _agents, calls = services
    store = ChatSessionStore(runtime)
    session(store)
    original = chat_runtime_activation_lock
    monkeypatch.setattr(
        "loopx.chat_ingress.chat_runtime_activation_lock",
        lambda root, **_kwargs: original(root, timeout_seconds=0),
    )
    with original(runtime):
        assert activate()["restart_reason"] == "chat_state_unverified"
    assert not calls


@pytest.mark.parametrize("unknown", [False, True])
def test_partial_restart_failure_keeps_the_actual_success_and_unknown_outcome(
    services, monkeypatch, unknown
):
    runtime, _agents, calls = services
    ChatSessionStore(runtime)

    def launchctl(args, **_kwargs):
        calls.append(args)
        if len(calls) == 2 and unknown:
            raise subprocess.TimeoutExpired(args, 30)
        return subprocess.CompletedProcess(args, 1 if len(calls) == 2 else 0, "", "")

    monkeypatch.setattr("loopx.runtime_activation.subprocess.run", launchctl)
    result = activate()
    assert result["restart_status"] == "failed" and result["activation_pending"]
    assert result["restarted_services"] == ["com.loopx.chat"]
    assert result["deferred_services"] == ["com.loopx.status"]
    assert len(calls) == 2


@pytest.mark.parametrize("driver", ["archive_snapshot", "python_pip", "python_pipx"])
def test_update_keeps_install_readback_but_does_not_claim_deferred_service_is_serving(
    services, driver
):
    runtime, _agents, _calls = services
    store = ChatSessionStore(runtime)
    sid = session(store)
    store.create_turn(sid, client_turn_id="original", message="original work")
    payload = {
        "source": {"repo": "loopx-project/loopx", "ref": "f" * 40},
        "plan": {},
        "install_lifecycle": {"execution_driver": driver},
    }
    passed = subprocess.CompletedProcess([], 0, json.dumps({"ok": True}), "")
    with (
        mock.patch(
            "loopx.self_update.run_archive_installer", return_value=(passed, {})
        ),
        mock.patch("loopx.self_update.subprocess.run", return_value=passed),
    ):
        result = execute_update_plan(payload)
    assert result["changes_applied"] and not result["ok"]
    assert result["execution"]["doctor_returncode"] == 0
    assert result["execution"]["restart_status"] == "deferred"
    assert result["next_action"]["kind"] == "retry_runtime_activation"
    assert "f" * 40 in result["next_action"]["command"]
    assert "Service activation pending" in render_update_plan_markdown(result)
