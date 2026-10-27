"""Private and group audiences share transport without sharing authority."""
import hashlib
import http.client
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from test_chat_ordinary_project import ordinary  # noqa: F401

from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.extensions.lark.conversation_identity import identity_ref
from loopx.extensions.lark.goal_topic_runtime_service import LarkGoalTopicRuntimeService


def test_app_id_selector_discovers_private_route_on_shared_group_listener(ordinary):  # noqa: F811
    from test_lark_private_conversations import connect
    from loopx.extensions.lark.goal_topic_runtime import poll_lark_goal_topic_profile_once

    _, runtime, provider, transport = connect(ordinary)
    selector = "cli_notes_app"
    binding = transport._binding("notes-app")
    transport.bindings.disconnect(binding["binding_id"], expected_revision=transport.bindings.read()["revision"])
    # The CLI accepts the App ID, while its inventory reports the named profile.
    provider.profile_apps[selector] = selector
    def runner(args, cwd=None, timeout=None):
        result = provider(args, cwd, timeout)
        if args[1:] == ["profile", "list"]:
            result["stdout"] = json.dumps([row for row in json.loads(result["stdout"])
                                          if row["name"] != selector])
        return result
    transport.runner = runner
    try:
        transport.bindings.configure(transport_ref=selector,
            project_ref=runtime.project_contexts.available()[0]["project_ref"], executor_endpoint_id="codex")
        legacy = {"target_payload": {"targets": {"group": {"enabled": True,
            "channel": {"chat_id": "oc_community"}, "identity": {"sender_profile": selector,
                "cli_bin": "lark-cli", "bot_app_id": selector}}}},
            "binding_payloads": {"goal": {"bindings": {"goal": {"goal_id": "goal", "enabled": True,
                "provider": "lark", "target_ref": "group", "topic": {"root_message_id": "om_root"}}}}}}
        profiles = transport.profiles()
        assert profiles[selector]["consumer_ref"] == hashlib.sha256(selector.encode()).hexdigest()[:32]
        event = provider.event(selector, "private_status", "/status")
        result = poll_lark_goal_topic_profile_once(profile=selector,
            snapshot={**legacy, "private_profiles": profiles}, runtime_root=transport.runtime_root,
            answer=lambda **_: pytest.fail("private input must not reach group execution"),
            consume_runner=lambda _: {"returncode": 0, "stdout": json.dumps(event)},
            private_admitter=transport.admit)
        assert result["event_statuses"] == ["command_recorded"]
        # A removed App must not remain discoverable through its ID selector.
        provider.profile_apps.pop("notes-app")
        assert selector not in transport.profiles()
        # An explicit profile with the same spelling must not be redirected
        # to a different App merely because that App exists elsewhere.
        provider.profile_apps["notes-app"] = selector
        provider.profile_apps[selector] = "cli_replacement"
        transport.runner = provider
        assert selector not in transport.profiles()
    finally:
        runtime.close()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX cross-process flock contract")
@pytest.mark.parametrize("shared_private", [False, True])
def test_legacy_consumer_stays_standby_until_original_profile_owner_exits(tmp_path, monkeypatch, shared_private):
    from loopx.extensions.lark import goal_topic_runtime as runtime
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    profile = "legacy-group"
    lease = tmp_path / ".loopx/lark-consumers" / hashlib.sha256(profile.encode()).hexdigest()[:32]
    lease.parent.mkdir(parents=True)
    # This is the actual prior owner key and an independent process, not a
    # synthetic result from the new key selection rule.
    owner = subprocess.Popen([sys.executable, "-u", "-c",
        "import fcntl,sys; f=open(sys.argv[1],'a'); fcntl.flock(f,fcntl.LOCK_EX); "
        "print('owned',flush=True); sys.stdin.read()", str(lease.with_name(lease.name + ".lock"))],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    assert owner.stdout.readline().strip() == "owned"
    snapshot = {"target_payload": {"targets": {"group": {"enabled": True,
        "channel": {"chat_id": "oc_public_fixture"},
        "identity": {"sender_profile": profile, "cli_bin": "fake-lark"}}}},
        "binding_payloads": {"goal": {"bindings": {"goal": {"goal_id": "goal", "enabled": True,
            "provider": "lark", "target_ref": "group", "topic": {"root_message_id": "om_public_fixture"}}}}},
        "private_profiles": {}, "goal_contexts": {}}
    if shared_private:
        snapshot["private_profiles"][profile] = {"cli_bin": "fake-lark", "audience": "owner",
            "provider_ref": identity_ref("cli_public_fixture"), "bot_app_id": "cli_public_fixture",
            "consumer_ref": hashlib.sha256(b"cli_public_fixture").hexdigest()[:32]}
    started = threading.Event()
    probes = []
    def stream(**_):
        if shared_private:
            app_lease = lease.parent / hashlib.sha256(b"cli_public_fixture").hexdigest()[:32]
            alias = subprocess.run([sys.executable, "-c",
                "import fcntl,sys\nf=open(sys.argv[1],'a')\ntry:\n "
                "fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)\nexcept BlockingIOError:\n sys.exit(7)",
                str(app_lease.with_name(app_lease.name + ".lock"))],
                capture_output=True, timeout=5)
            probes.append((alias.returncode, alias.stderr))
        started.set()
        return {"ok": True, "status": "stopped"}
    monkeypatch.setattr(runtime, "stream_lark_goal_topic_profile", stream)
    service = LarkGoalTopicRuntimeService(snapshot_provider=lambda: snapshot,
        runtime_root=tmp_path / "runtime", runtime_controller=object())
    successor = None
    try:
        service.refresh()
        deadline = time.monotonic() + 2
        while service.health_snapshot().get(profile, {}).get("status") != "standby" and time.monotonic() < deadline:
            threading.Event().wait(.01)
        assert service.health_snapshot()[profile]["status"] == "standby"
        assert not started.is_set()
        service.close()
        owner.stdin.close()
        assert owner.wait(2) == 0
        successor = LarkGoalTopicRuntimeService(snapshot_provider=lambda: snapshot,
            runtime_root=tmp_path / "runtime", runtime_controller=object())
        successor.refresh()
        assert started.wait(2), successor.health_snapshot()
        if shared_private:
            assert probes[0][0] == 7, probes[0]
    finally:
        service.close()
        if successor is not None:
            successor.close()
        if owner.poll() is None:
            owner.stdin.close()
            owner.wait(2)


@pytest.mark.parametrize("private_rows", [[], [{"transport_ref": "other-app", "provider_ref": identity_ref("cli_other_fixture")}],
    [{"transport_ref": "workspace-app", "provider_ref": identity_ref("cli_public_fixture")}],
    [{"transport_ref": "other-alias", "provider_ref": identity_ref("cli_public_fixture")}]] )
def test_group_http_preview_preserves_auth_cost_and_checks_real_private_aliases(tmp_path, private_rows):
    calls = []
    registry = {"schema_version": "0.1", "goals": [{"id": "goal", "repo": str(tmp_path),
        "coordination": {"registered_agents": ["codex"]}}]}
    class Handler(ChatRequestHandler):
        def _require_lark_cli(self):
            return "fake-lark"
        def _goal_channel_context(self, _goal):
            return registry, tmp_path / "binding.json"
        def _goal_channel_target_path(self):
            return tmp_path / "targets.json"
        def _refresh_lark_goal_topic_runtime(self):
            pass
        def _lark_runner(self):
            def run(args, _cwd, _timeout):
                calls.append(args)
                if args[-4:] == ["auth", "status", "--verify", "--json"]:
                    return {"returncode": 0, "stdout": json.dumps({"appId": "cli_public_fixture", "identities": {
                        "bot": {"openId": "ou_public_fixture", "appName": "Fixture", "available": True, "verified": True}}})}
                return {"returncode": 0, "stdout": "{}"}
            return run
    server = ChatHTTPServer(("127.0.0.1", 0), Handler)
    server.verbose = False
    server.runtime_controller = SimpleNamespace(close=lambda: None, project_contexts=SimpleNamespace(
        conversation_bindings=SimpleNamespace(read=lambda: {"bindings": private_rows})))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    def post(body):
        conn = http.client.HTTPConnection(*server.server_address, timeout=5)
        try:
            conn.request("POST", "/api/chat/lark/connections", json.dumps(body), {"Content-Type": "application/json"})
            result = conn.getresponse()
            return result.status, json.loads(result.read())
        finally:
            conn.close()
    try:
        assert post({"app_ref": "workspace-app"})[0] == 400
        assert not calls  # malformed structural input must never reach auth
        code, result = post({"app_ref": "workspace-app", "goal_id": "goal", "agent_id": "codex",
            "chat_id": "oc_public_fixture", "chat_name": "Fixture", "execute": False})
        auth = [args for args in calls if args[-4:] == ["auth", "status", "--verify", "--json"]]
        assert len(auth) == 1, (code, result, calls)
        if private_rows and private_rows[0]["provider_ref"] == identity_ref("cli_public_fixture") and private_rows[0]["transport_ref"] != "workspace-app":
            assert code == 400 and "private listener" in result["error"]
        else:
            assert code == 200 and result["ok"], result
        assert not (tmp_path / "binding.json").exists()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(2)


def test_shared_consumer_dispatch_preserves_audience_and_survives_one_route_removal(tmp_path, monkeypatch):
    from loopx.extensions.lark import goal_topic_runtime as runtime

    profile = "workspace-app"
    group = {"target_payload": {"targets": {"group": {"enabled": True,
        "channel": {"chat_id": "oc_public_fixture"}, "identity": {"sender_profile": profile,
            "cli_bin": "fake-lark", "bot_app_id": "cli_public_fixture"}}}},
        "binding_payloads": {"goal": {"bindings": {"goal": {"goal_id": "goal", "enabled": True,
            "provider": "lark", "target_ref": "group", "topic": {"root_message_id": "om_root"}}}}}}
    private = {"private_profiles": {profile: {"cli_bin": "fake-lark", "audience": "owner",
        "provider_ref": identity_ref("cli_public_fixture"), "bot_app_id": "cli_public_fixture"}}}
    both = {**group, **private}
    assert list(runtime._active_profile_configs(both)) == [profile]
    private_events, group_events = [], []
    def group_route(**kwargs):
        group_events.append(kwargs["event"])
        return None, "not_bound"
    monkeypatch.setattr(runtime, "_profile_event_route", group_route)
    events = [{"chat_type": kind, "message_type": "text", "message_id": f"om_{kind}"}
              for kind in ("p2p", "group", "unknown")]
    def poll(snapshot):
        return runtime.poll_lark_goal_topic_profile_once(profile=profile, snapshot=snapshot,
            runtime_root=tmp_path, answer=lambda *_: pytest.fail("no authorized model work"),
            consume_runner=lambda _: {"returncode": 0, "stdout": "\n".join(map(json.dumps, events))},
            private_admitter=lambda _, event: private_events.append(event) or {"status": "audience_rejected"})
    result = poll(both)
    assert result["event_statuses"] == ["audience_rejected", "not_bound", "audience_rejected"]
    assert group_events == [events[1]], "private refusal never retries under group authority"
    assert private_events == [events[0], events[2]], "group input never reaches the private owner"
    for survivor in (group, private):
        assert list(runtime._active_profile_configs(survivor)) == [profile]
        private_events.clear()
        group_events.clear()
        poll(survivor)
        assert len(private_events) == (3 if survivor is private else 0)
        assert len(group_events) == (3 if survivor is group else 0)
    for invalid in ({"provider_ref": identity_ref("cli_other_fixture")}, {"audience": "group"}):
        with pytest.raises(ValueError, match="shared listener"):
            runtime._active_profile_configs({**group, "private_profiles": {
                profile: {**private["private_profiles"][profile], **invalid}}})


@pytest.mark.parametrize("stored_app,retarget", [(None, False), ("cli_notes_app", False),
    ("cli_other_fixture", False), (None, True), (None, "owner")])
@pytest.mark.parametrize("app_id_selector", [False, True])
def test_private_http_setup_shares_existing_group_profile_without_changing_its_grants(ordinary, monkeypatch, stored_app, retarget, app_id_selector):  # noqa: F811
    from test_lark_private_conversations import connect
    from loopx.extensions.lark.cli_resolution import LarkCliResolution
    from loopx.extensions.lark.goal_topic_runtime import _active_profile_configs

    store, runtime, provider, transport = connect(ordinary)
    selector = "cli_notes_app" if app_id_selector else "notes-app"
    if app_id_selector:
        binding = transport._binding("notes-app")
        transport.bindings.disconnect(binding["binding_id"], expected_revision=transport.bindings.read()["revision"])
        provider.profile_apps[selector] = "cli_notes_app"
        observe = transport.bindings.observe
        def same_cli_identity(profile, **kwargs):
            return {**observe("notes-app" if profile == selector else profile, **kwargs),
                    "transport_ref": profile}
        transport.bindings.observe = same_cli_identity
        def named_inventory(args, cwd=None, timeout=None):
            result = provider(args, cwd, timeout)
            if args[1:] == ["profile", "list"]:
                result["stdout"] = json.dumps([row for row in json.loads(result["stdout"])
                                              if row["name"] != selector])
            return result
        transport.runner = named_inventory
    legacy = {"target_payload": {"targets": {"group": {"enabled": True,
        "channel": {"chat_id": "oc_community"}, "identity": {"sender_profile": selector,
            "cli_bin": "lark-cli", "bot_app_id": "cli_notes_app"}}}},
        "binding_payloads": {"goal": {"bindings": {"goal": {"goal_id": "goal", "enabled": True,
            "provider": "lark", "target_ref": "group", "topic": {"root_message_id": "om_root"}}}}}}
    identity = legacy["target_payload"]["targets"]["group"]["identity"]
    if stored_app is None:
        identity.pop("bot_app_id")
    else:
        identity["bot_app_id"] = stored_app
    frozen = json.dumps(legacy, sort_keys=True)
    monkeypatch.setattr("loopx.chat_lark_api.build_lark_goal_topic_runtime_snapshot", lambda **_: legacy)
    monkeypatch.setattr(ChatRequestHandler, "_lark_runner", lambda _: provider)
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.chat_store, server.runtime_controller = store, runtime
    server.verbose = False
    server.registry_path, server.runtime_root_override = runtime.registry_path, store.root.parent
    server.lark_cli_resolution = LarkCliResolution(command="lark-cli", available=True,
        source="explicit", version="fixture", error_code=None)
    server.lark_private_conversations = transport
    def snapshot():
        return {**legacy, "private_profiles": transport.profiles()}
    service = LarkGoalTopicRuntimeService(snapshot_provider=snapshot, runtime_root=store.root.parent,
        runtime_controller=runtime, profile_poller=lambda _profile, stop: stop.wait())
    server.lark_goal_topic_runtime = service
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def post(body):
        connection = http.client.HTTPConnection(*server.server_address, timeout=15)
        try:
            connection.request("POST", "/api/chat/lark/private-conversations", json.dumps(body),
                               headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()
    if retarget:
        capabilities = runtime.capabilities
        def retargeted_capabilities():
            if retarget == "owner":
                previous = transport.bindings.observe
                transport.bindings.observe = lambda profile, **kwargs: {
                    **previous(profile, **kwargs), "operator_ref": "f" * 24}
            else:
                provider.profile_apps["notes-app"] = "cli_replacement"
            return capabilities()
        monkeypatch.setattr(runtime, "capabilities", retargeted_capabilities)
    body = {"app_ref": "notes-app", "project_ref": runtime.project_contexts.available()[0]["project_ref"],
            "executor_endpoint_id": "codex", "context_kind": "steward"}
    try:
        provider.owner_ready = False
        before = transport.bindings.read()
        assert post(body)[0] == 400
        assert transport.bindings.read() == before
        provider.owner_ready = True
        status, result = post(body)
        if stored_app == "cli_other_fixture" or retarget:
            assert status == 400, result
            assert transport.bindings.read() == before
            return
        assert status == 200, result
        saved = next(row for row in result["connections"] if row["app_ref"] == selector)
        assert saved["audience"] == "owner" and saved["context_kind"] == "steward"
        assert selector in _active_profile_configs(snapshot())
        service.close()
        # Reload the committed Core binding and compose the production listener
        # again. A successful settings response must survive cold discovery.
        service = LarkGoalTopicRuntimeService(snapshot_provider=snapshot, runtime_root=store.root.parent,
            runtime_controller=runtime, profile_poller=lambda _profile, stop: stop.wait())
        server.lark_goal_topic_runtime = service
        service.refresh()
        assert set(service.health_snapshot()) == {selector, "steward-app"}
        assert post({**body, "audience": "group", "group_ids": ["oc_community"]})[0] == 400
        assert json.dumps(legacy, sort_keys=True) == frozen
        assert not provider.writes and not store.list_sessions()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
        service.close()
        runtime.close()
