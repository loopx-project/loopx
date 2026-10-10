"""Private configuration must preserve the existing group listener boundary."""
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

from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.extensions.lark.conversation_identity import identity_ref
from loopx.extensions.lark.goal_topic_runtime_service import LarkGoalTopicRuntimeService


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX cross-process flock contract")
def test_legacy_consumer_stays_standby_until_original_profile_owner_exits(tmp_path, monkeypatch):
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
    started = threading.Event()
    monkeypatch.setattr(runtime, "stream_lark_goal_topic_profile",
        lambda **_: started.set() or {"ok": True, "status": "stopped"})
    service = LarkGoalTopicRuntimeService(snapshot_provider=lambda: snapshot,
        runtime_root=tmp_path / "runtime", runtime_controller=object())
    stop = threading.Event()
    worker = threading.Thread(target=service._poll_profile, args=(profile, stop))
    try:
        worker.start()
        deadline = time.monotonic() + 2
        while service.health_snapshot().get(profile, {}).get("status") != "standby" and time.monotonic() < deadline:
            threading.Event().wait(.01)
        assert service.health_snapshot()[profile]["status"] == "standby"
        assert not started.is_set()
        stop.set()
        worker.join(2)
        owner.stdin.close()
        assert owner.wait(2) == 0
        resumed_stop = threading.Event()
        successor = threading.Thread(target=service._poll_profile, args=(profile, resumed_stop))
        successor.start()
        assert started.wait(2)
        resumed_stop.set()
        successor.join(2)
    finally:
        stop.set()
        worker.join(2)
        service.close()
        if owner.poll() is None:
            owner.stdin.close()
            owner.wait(2)


@pytest.mark.parametrize("private_rows", [[], [{"transport_ref": "other-app", "provider_ref": identity_ref("cli_other_fixture")}],
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
        if private_rows and private_rows[0]["provider_ref"] == identity_ref("cli_public_fixture"):
            assert code == 400 and "private listener" in result["error"]
        else:
            assert code == 200 and result["ok"], result
        assert not (tmp_path / "binding.json").exists()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(2)
