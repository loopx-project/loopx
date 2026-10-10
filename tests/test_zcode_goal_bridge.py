from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from loopx.zcode_goal_mode import bridge
from loopx.zcode_goal_mode.api import ZCodeGoalRequestMixin, public_zcode_goal_readback

INSTANCE_A = "ginst_" + "a" * 32
INSTANCE_B = "ginst_" + "b" * 32


@pytest.fixture
def authority(tmp_path: Path) -> tuple[Path, Path, dict]:
    project = tmp_path / "project"
    project.mkdir()
    registry = project / ".loopx" / "registry.json"
    registry.parent.mkdir()
    payload = {"schema_version": "0.2", "common_runtime_root": str(tmp_path / "runtime"), "goals": [{
        "id": "delivery", "goal_instance_id": INSTANCE_A, "status": "active", "repo": str(project),
        "coordination": {"registered_agents": ["zcode-worker"], "agent_profiles": {"zcode-worker": {"agent_type": "zcode"}}},
    }]}
    registry.write_text(json.dumps(payload), encoding="utf-8")
    return registry, project, payload


def _validate(authority, **extra):
    registry, _, _ = authority
    return bridge.validate_zcode_binding(registry_path=registry, goal_id="delivery", agent_id="zcode-worker", **extra)


def test_existing_exact_goal_and_zcode_agent_are_read_only(authority):
    registry, project, _ = authority
    before = registry.read_bytes()
    result = _validate(authority, project=project)
    assert result["goal_ref"] == {"goal_id": "delivery", "goal_instance_id": INSTANCE_A}
    assert result["agent_id"] == "zcode-worker"
    assert registry.read_bytes() == before
    assert not (registry.parent / "zcode-goal").exists()


@pytest.mark.parametrize("mutation", ["unregistered", "other_host", "invalid_instance", "stopped", "archived", "duplicate"])
def test_invalid_authority_cannot_launch_provider(authority, monkeypatch, mutation):
    registry, _, payload = authority
    goal = payload["goals"][0]
    if mutation == "unregistered":
        goal["coordination"]["registered_agents"] = []
    elif mutation == "other_host":
        goal["coordination"]["agent_profiles"]["zcode-worker"]["agent_type"] = "codex-cli"
    elif mutation == "invalid_instance":
        goal["goal_instance_id"] = 123
    elif mutation == "stopped":
        goal["activation_state"] = "stopped"
    elif mutation == "archived":
        goal["status"] = "archived"
    else:
        payload["goals"].append(dict(goal))
    registry.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(bridge, "_node_command", lambda: pytest.fail("invalid authority must fail before provider discovery"))
    with pytest.raises(ValueError):
        bridge.zcode_goal_operation(action="start", registry_path=registry, goal_id="delivery", agent_id="zcode-worker")


@pytest.mark.parametrize("profiles_as_list", [False, True])
def test_provider_eligibility_reuses_binding_rule_and_registration_fallbacks(authority, monkeypatch, profiles_as_list):
    registry, _, payload = authority
    goal = payload["goals"][0]
    profiles = {
        "zcode-looking": {"agent_type": "codex-cli"},
        "host-unset": {"scope_summary": "Advisory profile"},
        "build-worker": {"host_surface": "z-code"},
        "invalid-host": {"agent_type": 42},
        "unregistered": {"agent_type": "zcode"},
    }
    goal["coordination"] = {
        "registered_agents": ["zcode-looking", "host-unset"],
        "agent_profiles": [{"agent_id": agent_id, **profile} for agent_id, profile in profiles.items()]
        if profiles_as_list else profiles,
    }
    goal["registered_agents"] = ["build-worker", "invalid-host"]
    goal["spawn_policy"] = {"registered_agents": ["host-unset"]}
    registry.write_text(json.dumps(payload), encoding="utf-8")
    before = registry.read_bytes()
    monkeypatch.setattr(bridge, "_node_command", lambda: pytest.fail("metadata eligibility must not discover or launch a host"))
    assert bridge.zcode_goal_eligible_agent_ids(goal) == ["host-unset", "build-worker"]
    for agent_id in ("host-unset", "build-worker"):
        assert bridge.validate_zcode_binding(registry_path=registry, goal_id="delivery", agent_id=agent_id)["agent_id"] == agent_id
    for agent_id in ("zcode-looking", "invalid-host"):
        with pytest.raises(bridge.ZCodeGoalBridgeError, match="different host"):
            bridge.validate_zcode_binding(registry_path=registry, goal_id="delivery", agent_id=agent_id)
    with pytest.raises(ValueError, match="not registered"):
        bridge.validate_zcode_binding(registry_path=registry, goal_id="delivery", agent_id="unregistered")
    assert registry.read_bytes() == before


def test_pure_other_host_goal_has_no_provider_candidates(authority):
    _, _, payload = authority
    goal = payload["goals"][0]
    goal["coordination"]["agent_profiles"]["zcode-worker"]["agent_type"] = "codex-cli"
    assert bridge.zcode_goal_eligible_agent_ids(goal) == []


def test_canonical_source_observer_reuses_existing_read_and_runs_on_caller(authority, tmp_path, monkeypatch):
    import threading
    from loopx.control_plane.runtime import runtime_projection_route as routes

    registry, project, payload = authority
    source_goal = payload["goals"][0]
    source_goal["coordination"] = {"agent_profiles": {"plain-worker": {"scope_summary": "Advisory"}}}
    source_goal["registered_agents"] = ["plain-worker"]
    source_goal["spawn_policy"] = {"registered_agents": ["fallback-worker"]}
    payload["goals"].append({**source_goal, "id": "second"})
    registry.write_text(json.dumps(payload), encoding="utf-8")
    shared = {"registry_role": "global-local", "common_runtime_root": str(tmp_path / "shared-runtime"), "goals": [
        {"id": goal_id, "source_registry": str(registry), "repo": str(project),
         "coordination": {"registered_agents": ["stale-other"], "agent_profiles": {"stale-other": {"agent_type": "codex-cli"}}}}
        for goal_id in ("delivery", "second")
    ]}
    reads = []
    read_source = routes._read_source_registry_with_deadline
    def read(path, **kwargs):
        reads.append(path)
        return read_source(path, **kwargs)
    monkeypatch.setattr(routes, "_read_source_registry_with_deadline", read)
    monkeypatch.setattr(bridge, "_node_command", lambda: pytest.fail("eligibility must not probe a host"))
    kwargs = {"registry_path": tmp_path / "shared.json", "runtime_root": tmp_path / "shared-runtime", "goal_id": None, "registry": shared}
    baseline = routes._source_routes_for_registry(**kwargs)
    assert reads == [registry]
    reads.clear()
    observed = {}
    caller = threading.get_ident()
    def observe(goal_id, goal):
        assert threading.get_ident() == caller
        observed[goal_id] = bridge.zcode_goal_eligible_agent_ids(goal) if goal is not None else []
    projected = routes._source_routes_for_registry(**kwargs, source_goal_observer=observe)
    assert projected == baseline
    assert reads == [registry], "Chat eligibility must reuse the existing once-per-source read"
    assert observed == {"delivery": ["plain-worker", "fallback-worker"], "second": ["plain-worker", "fallback-worker"]}
    for agent_id in observed["delivery"]:
        assert bridge.validate_zcode_binding(registry_path=registry, goal_id="delivery", agent_id=agent_id)["agent_id"] == agent_id


@pytest.mark.parametrize("failure", ["missing", "unreadable", "timeout", "duplicate", "absent"])
def test_canonical_source_observer_never_uses_stale_projected_profiles(authority, tmp_path, monkeypatch, failure):
    from loopx.control_plane.runtime import runtime_projection_route as routes

    registry, _, payload = authority
    shared = {"registry_role": "global-local", "common_runtime_root": str(tmp_path / "shared-runtime"), "goals": [{
        "id": "delivery", "source_registry": str(registry),
        "coordination": {"registered_agents": ["projected-worker"]},
    }]}
    if failure in ("missing", "unreadable", "timeout"):
        monkeypatch.setattr(routes, "_read_source_registry_with_deadline", lambda *args, **kwargs: (None, "source_registry_" + failure))
    else:
        payload["goals"] = payload["goals"] * 2 if failure == "duplicate" else []
        registry.write_text(json.dumps(payload), encoding="utf-8")
    seen = []
    routes._source_routes_for_registry(
        registry_path=tmp_path / "shared.json", runtime_root=tmp_path / "shared-runtime", goal_id=None,
        registry=shared, source_goal_observer=lambda goal_id, goal: seen.append((goal_id, goal)),
    )
    assert seen == [("delivery", None)]


def test_actual_chat_status_projects_canonical_host_eligibility_without_extra_reads(authority, tmp_path, monkeypatch):
    import threading
    from http.server import ThreadingHTTPServer
    from urllib.request import urlopen
    from loopx.chat_server import ChatRequestHandler
    from loopx.control_plane.runtime import runtime_projection_route as routes
    from loopx.status import collect_status

    registry, project, payload = authority
    goal = payload["goals"][0]
    goal["coordination"]["agent_profiles"]["zcode-worker"]["agent_type"] = "codex-cli"
    registry.write_text(json.dumps(payload), encoding="utf-8")
    runtime = tmp_path / "runtime"
    runtime.mkdir(exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    for key, value in {"registry_path": registry, "runtime_root_override": None, "runtime_root": runtime,
                       "scan_roots": [project], "limit": 8, "selected_goal_id": None, "verbose": False}.items():
        setattr(server, key, value)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(bridge, "_node_command", lambda: pytest.fail("Chat metadata must not discover or launch ZCode"))
    def read_candidates():
        with urlopen(f"http://127.0.0.1:{server.server_port}/status.json", timeout=30) as response:
            status = json.load(response)
        return next(row for row in status["run_history"]["goals"] if row["id"] == "delivery")["zcode_goal_eligible_agent_ids"]
    try:
        assert read_candidates() == []
        goal["registered_agents"] = ["native-worker", "host-unset"]
        goal["coordination"]["agent_profiles"].update({
            "native-worker": {"host_surface": "z-code"}, "host-unset": {"scope_summary": "Advisory"},
        })
        registry.write_text(json.dumps(payload), encoding="utf-8")
        assert read_candidates() == ["native-worker", "host-unset"]
        for agent_id in ("native-worker", "host-unset"):
            assert bridge.validate_zcode_binding(registry_path=registry, goal_id="delivery", agent_id=agent_id)["agent_id"] == agent_id
        shared = tmp_path / "shared.json"
        shared.write_text(json.dumps({"schema_version": "0.2", "registry_role": "global-local",
            "common_runtime_root": str(tmp_path / "shared-runtime"), "goals": [{
                **goal, "source_registry": str(registry), "registered_agents": ["forged-native"],
                "coordination": {"registered_agents": ["forged-native"], "agent_profiles": {"forged-native": {"agent_type": "zcode"}}},
            }]}), encoding="utf-8")
        server.registry_path = shared
        assert read_candidates() == ["native-worker", "host-unset"]
        reads = []
        original_read = routes._read_source_registry_with_deadline
        def counted(path, **kwargs):
            reads.append(path)
            return original_read(path, **kwargs)
        monkeypatch.setattr(routes, "_read_source_registry_with_deadline", counted)
        kwargs = {"registry_path": shared, "runtime_root_override": None, "scan_roots": [project], "limit": 8, "include_public_boundary_scan": False}
        baseline = collect_status(**kwargs)
        baseline_reads = list(reads)
        assert all("zcode_goal_eligible_agent_ids" not in row for row in baseline["run_history"]["goals"])
        reads.clear()
        current = collect_status(**kwargs, include_zcode_goal_eligibility=True)
        assert reads == baseline_reads, "The Chat-only projection adds zero canonical source reads"
        assert next(row for row in current["run_history"]["goals"] if row["id"] == "delivery")["zcode_goal_eligible_agent_ids"] == ["native-worker", "host-unset"]
        registry.write_text("{invalid-json", encoding="utf-8")
        assert read_candidates() == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
    assert not thread.is_alive()


def test_project_and_goal_instance_are_consistency_assertions(authority, tmp_path):
    with pytest.raises(ValueError, match="canonical"):
        _validate(authority, project=tmp_path)
    with pytest.raises(bridge.ZCodeGoalBridgeError, match="instance changed"):
        _validate(authority, goal_ref={"goal_id": "delivery", "goal_instance_id": INSTANCE_B})


def test_shared_projection_follows_canonical_source_registry(authority, tmp_path):
    registry, project, payload = authority
    projection = tmp_path / "shared.json"
    projection.write_text(json.dumps({"schema_version": "0.2", "registry_role": "global-local", "common_runtime_root": str(tmp_path / "shared-runtime"), "goals": [{**payload["goals"][0], "source_registry": str(registry), "repo": str(tmp_path / "wrong")}]}), encoding="utf-8")
    result = bridge.validate_zcode_binding(registry_path=projection, goal_id="delivery", agent_id="zcode-worker", project=project)
    assert result["registry"] == str(registry.resolve())
    assert result["project"] == str(project.resolve())
    assert result["runtime_root"] == payload["common_runtime_root"]


def _stub_operation(monkeypatch, *, callback=None):
    requests = []
    monkeypatch.setattr(bridge, "_node_command", lambda: "qualified-node")
    def run(command, *, project, request=None):
        requests.append((command, request))
        if request is None:
            return {"ok": True, "task_body": "Canonical thin heartbeat instructions"}
        if callback:
            callback(request)
        return {"ok": True, "available": False, "goal_id": request["goal_id"], "goal_ref": request["goal_ref"], "goal_creation_operation_id": request["goal_creation_operation_id"], "agent_id": request["agent_id"], "actions": ["bind", "status"]}
    monkeypatch.setattr(bridge, "_run_json", run)
    return requests


def test_cli_bridge_pins_interpreter_and_scopes_runtime_state(authority, monkeypatch, tmp_path):
    registry, project, _ = authority
    cli = tmp_path / "zcode.mjs"
    cli.write_text("", encoding="utf-8")
    requests = _stub_operation(monkeypatch)
    result = bridge.zcode_goal_operation(action="bind", registry_path=registry, goal_id="delivery", agent_id="zcode-worker", project=project, cli_path=str(cli))
    heartbeat_command, _ = requests[0]
    command, native_request = requests[1]
    assert heartbeat_command[:3] == [sys.executable, "-m", "loopx.cli"]
    assert "--thin" in heartbeat_command
    assert heartbeat_command[heartbeat_command.index("--runtime-profile") + 1] == "generic_cli"
    assert native_request["loopx_command"] == [sys.executable, "-m", "loopx.cli"]
    assert native_request["validation_command"] == [sys.executable, "-m", "loopx.zcode_goal_mode.bridge", "--validate-binding"]
    assert native_request["cli_command"] == ["qualified-node", str(cli.resolve()), "app-server"]
    assert native_request["cli_path"] == str(cli.resolve())
    assert Path(native_request["state_path"]).parent == tmp_path / "runtime" / "zcode-goal"
    assert native_request["task_body"] == "Canonical thin heartbeat instructions"
    assert result["goal_ref"]["goal_instance_id"] == INSTANCE_A


def test_status_does_not_generate_prompt_or_select_cli(authority, monkeypatch):
    registry, _, _ = authority
    requests = _stub_operation(monkeypatch)
    bridge.zcode_goal_operation(action="status", registry_path=registry, goal_id="delivery", agent_id="zcode-worker")
    assert len(requests) == 1
    assert "task_body" not in requests[0][1]
    assert "cli_command" not in requests[0][1]
    with pytest.raises(ValueError, match="only.*bind"):
        bridge.zcode_goal_operation(action="resume", registry_path=registry, goal_id="delivery", agent_id="zcode-worker", cli_path="another-cli")


def test_replaced_goal_after_operation_is_not_reported_as_success(authority, monkeypatch):
    registry, _, payload = authority
    def replace(request):
        payload["goals"][0]["goal_instance_id"] = INSTANCE_B
        registry.write_text(json.dumps(payload), encoding="utf-8")
    _stub_operation(monkeypatch, callback=replace)
    with pytest.raises(bridge.ZCodeGoalBridgeError, match="instance changed"):
        bridge.zcode_goal_operation(action="status", registry_path=registry, goal_id="delivery", agent_id="zcode-worker")


def test_foreign_provider_readback_is_rejected(authority, monkeypatch):
    registry, _, _ = authority
    _stub_operation(monkeypatch)
    monkeypatch.setattr(bridge, "_run_json", lambda *args, **kwargs: {"ok": True, "goal_id": "another", "goal_ref": {"goal_id": "delivery", "goal_instance_id": INSTANCE_A}, "agent_id": "zcode-worker"})
    with pytest.raises(bridge.ZCodeGoalBridgeError, match="did not match"):
        bridge.zcode_goal_operation(action="status", registry_path=registry, goal_id="delivery", agent_id="zcode-worker")


def test_internal_binding_guard_runs_on_selected_interpreter(authority):
    registry, project, payload = authority
    request = {"action": "start", "registry": str(registry), "project": str(project), "goal_id": "delivery", "agent_id": "zcode-worker", "goal_ref": {"goal_id": "delivery", "goal_instance_id": INSTANCE_A}}
    command = [sys.executable, "-X", "utf8", "-B", "-m", "loopx.zcode_goal_mode.bridge", "--validate-binding"]
    valid = subprocess.run(command, input=json.dumps(request), encoding="utf-8", capture_output=True, timeout=15)
    assert valid.returncode == 0, valid.stderr
    assert json.loads(valid.stdout)["goal_ref"] == request["goal_ref"]
    payload["goals"][0]["goal_instance_id"] = INSTANCE_B
    registry.write_text(json.dumps(payload), encoding="utf-8")
    stale = subprocess.run(command, input=json.dumps(request), encoding="utf-8", capture_output=True, timeout=15)
    assert stale.returncode == 1
    assert json.loads(stale.stdout)["error_code"] == "zcode_goal_authority_changed"


def test_cli_parser_and_forwarding(authority, monkeypatch, capsys):
    from loopx import cli
    from loopx.cli_commands import zcode_goal
    registry, project, _ = authority
    seen = []
    monkeypatch.setattr(zcode_goal, "zcode_goal_operation", lambda **kwargs: seen.append(kwargs) or {"ok": True, "actions": ["status"]})
    assert cli.main(["--registry", str(registry), "zcode-goal", "bind", "--goal-id", "delivery", "--agent-id", "zcode-worker", "--project", str(project), "--zcode-cli", "bundle.mjs", "--format", "json"]) == 0
    assert seen == [{"action": "bind", "registry_path": registry, "goal_id": "delivery", "agent_id": "zcode-worker", "project": str(project), "cli_path": "bundle.mjs", "runtime_root": None, "model_selection": None}]
    assert json.loads(capsys.readouterr().out)["ok"] is True


class Handler(ZCodeGoalRequestMixin):
    def __init__(self, registry, *, host="127.0.0.1", allowed=True, body=None):
        self.server = SimpleNamespace(registry_path=registry, runtime_root_override=None, server_address=(host, 4321))
        self.path = "/api/goals/delivery/agents/zcode-worker/zcode-goal"
        self.allowed = allowed
        self.body = body or {}
        self.sent = []
    def _require_loopback_origin(self):
        if not self.allowed:
            self._send_error("forbidden origin", status=403)
        return self.allowed
    def _read_json(self):
        return self.body
    def _send_json(self, payload, **kwargs):
        self.sent.append((payload, kwargs))
    def _send_error(self, message, **kwargs):
        self.sent.append(({"ok": False, "error": message}, kwargs))


def test_api_public_projection_keeps_same_session_evidence():
    public = public_zcode_goal_readback({"ok": True, "goal_id": "delivery", "agent_id": "zcode-worker", "goal_ref": {"goal_id": "delivery", "goal_instance_id": INSTANCE_A}, "project": "private", "state_path": "private", "binding": {"mode": "managed_cli", "connected": True, "cli_path": "private", "protocol": "zcode-ndjson-session-goal"}, "native": {"session_id": "native-session", "target_id": "native-target", "status": "paused", "running": False, "transcript": "private"}, "quota": {"should_run": False, "reason": "no slots"}, "actions": ["resume", "stop"]})
    assert public["native"]["session_id"] == "native-session"
    assert public["native"]["target_id"] == "native-target"
    assert public["binding"]["cli_path"] == "private"
    assert "transcript" not in public["native"]
    assert "project" not in public and "state_path" not in public


@pytest.mark.parametrize("case", ["remote_server", "foreign_origin", "project_injection", "missing_action", "invalid_cli", "query"])
def test_api_rejects_outside_authority_before_provider(authority, monkeypatch, case):
    from loopx.zcode_goal_mode import api
    registry, _, _ = authority
    handler = Handler(registry, host="0.0.0.0" if case == "remote_server" else "127.0.0.1", allowed=case != "foreign_origin", body={"action": "bind"})
    if case == "project_injection":
        handler.body["project"] = "elsewhere"
    if case == "missing_action":
        handler.body = {}
    if case == "invalid_cli":
        handler.body["cli_path"] = None
    if case == "query":
        handler.path += "?registry=elsewhere"
    monkeypatch.setattr(api, "zcode_goal_operation", lambda **kwargs: pytest.fail("rejected API request reached provider"))
    assert handler._dispatch_zcode_goal(handler.path.split("?")[0], apply=True)
    assert handler.sent[0][1]["status"] in {400, 403}


@pytest.mark.parametrize("injected", [
    {"project_ref": "ordinary-project-ref"},
    {"project_context": {"kind": "project_workspace", "grant": "workspace_read"}},
    {"conversation_binding_id": "ordinary-conversation-binding"},
    {"source_context": {"source_ref": "a" * 24, "sender_ref": "b" * 24, "private_human_message": True}},
])
def test_api_cannot_borrow_ordinary_project_chat_authority(authority, monkeypatch, injected):
    registry, _, _ = authority
    handler = Handler(registry, body={
        "action": "start",
        "expected_binding": {
            "goal_ref": {"goal_id": "delivery", "goal_instance_id": INSTANCE_A},
            "goal_creation_operation_id": None,
        },
        **injected,
    })
    monkeypatch.setattr(bridge, "_node_command", lambda: pytest.fail("ordinary Chat authority reached native Node discovery"))
    monkeypatch.setattr(bridge, "_run_json", lambda *args, **kwargs: pytest.fail("ordinary Chat authority reached native effects"))
    assert handler._dispatch_zcode_goal(handler.path, apply=True)
    assert handler.sent[0][1]["status"] == 400
    assert "ZCode Goal accepts" in handler.sent[0][0]["error"]


def test_api_get_and_post_share_canonical_bridge(authority, monkeypatch):
    from loopx.zcode_goal_mode import api
    registry, _, _ = authority
    seen = []
    def operation(**kwargs):
        seen.append(kwargs)
        return {"ok": True, "available": False, "goal_id": "delivery", "agent_id": "zcode-worker", "goal_ref": {"goal_id": "delivery", "goal_instance_id": INSTANCE_A}, "binding": None, "native": None, "quota": None, "actions": ["bind", "status"]}
    monkeypatch.setattr(api, "zcode_goal_operation", operation)
    get = Handler(registry)
    assert get._dispatch_zcode_goal(get.path)
    post = Handler(registry, body={"action": "bind", "cli_path": "bundle.mjs", "expected_binding": {"goal_ref": {"goal_id": "delivery", "goal_instance_id": INSTANCE_A}, "goal_creation_operation_id": None}})
    assert post._dispatch_zcode_goal(post.path, apply=True)
    assert seen[0]["action"] == "status" and seen[1]["action"] == "bind"
    assert seen[1]["cli_path"] == "bundle.mjs"
    assert all(item["registry_path"] == registry and "project" not in item for item in seen)
    assert not get._dispatch_zcode_goal("/api/chat/status")


@pytest.mark.parametrize("kind", ["missing", "desktop", "windows_shim"])
def test_cli_binding_does_not_launch_unsupported_surface(tmp_path, kind):
    path = tmp_path / ("zcode.cmd" if kind == "windows_shim" else "zcode.exe")
    if kind != "missing":
        path.write_text("", encoding="utf-8")
    if kind == "desktop":
        bundle = tmp_path / "resources/glm/zcode.cjs"
        bundle.parent.mkdir(parents=True)
        bundle.write_text("", encoding="utf-8")
    with pytest.raises(bridge.ZCodeGoalBridgeError):
        bridge._cli_command(str(path), "node")


def test_real_chat_http_route_preserves_scoped_readback(authority, monkeypatch):
    import threading
    from http.server import ThreadingHTTPServer
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError
    from loopx.chat_server import ChatRequestHandler
    from loopx.zcode_goal_mode import api
    registry, _, _ = authority
    seen = []
    def operation(**kwargs):
        seen.append(kwargs)
        return {"ok": True, "available": False, "goal_id": "delivery", "agent_id": "zcode-worker", "goal_ref": {"goal_id": "delivery", "goal_instance_id": INSTANCE_A}, "binding": None, "native": None, "quota": None, "actions": ["bind", "status"]}
    monkeypatch.setattr(api, "zcode_goal_operation", operation)
    server = ThreadingHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.registry_path = registry
    server.runtime_root_override = None
    server.verbose = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/api/goals/delivery/agents/zcode-worker/zcode-goal"
    try:
        with urlopen(url, timeout=10) as response:
            assert json.load(response)["goal_ref"]["goal_instance_id"] == INSTANCE_A
        with urlopen(Request(url, data=json.dumps({"action": "bind", "cli_path": "bundle.mjs", "expected_binding": {"goal_ref": {"goal_id": "delivery", "goal_instance_id": INSTANCE_A}, "goal_creation_operation_id": None}}).encode(), headers={"Content-Type": "application/json"}), timeout=10) as response:
            assert json.load(response)["actions"] == ["bind", "status"]
        with pytest.raises(HTTPError) as failure:
            urlopen(Request(url, data=b'{"action":"start"}', headers={"Origin": "https://outside.example"}), timeout=10)
        assert failure.value.code == 403
        assert [entry["action"] for entry in seen] == ["status", "bind"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_real_provider_status_is_read_only_with_exact_identity(authority):
    registry, _, _ = authority
    try:
        bridge._node_command()
    except bridge.ZCodeGoalBridgeError as exc:
        pytest.skip(str(exc))
    result = bridge.zcode_goal_operation(action="status", registry_path=registry, goal_id="delivery", agent_id="zcode-worker")
    assert result["ok"] is True
    assert result["available"] is False
    assert result["binding"] is None
    assert result["goal_ref"] == {"goal_id": "delivery", "goal_instance_id": INSTANCE_A}
    assert not (Path(authority[2]["common_runtime_root"]) / "zcode-goal").exists()


@pytest.mark.parametrize("action", ["status", "pause", "stop"])
def test_same_exact_inactive_binding_can_be_read_or_cleaned(authority, monkeypatch, action):
    registry, _, payload = authority
    payload["goals"][0]["activation_state"] = "stopped"
    registry.write_text(json.dumps(payload), encoding="utf-8")
    requests = _stub_operation(monkeypatch)
    result = bridge.zcode_goal_operation(action=action, registry_path=registry, goal_id="delivery", agent_id="zcode-worker")
    assert result["goal_ref"]["goal_instance_id"] == INSTANCE_A
    assert requests[0][1]["action"] == action
    with pytest.raises(bridge.ZCodeGoalBridgeError, match="no longer active"):
        _validate(authority)


@pytest.mark.parametrize("action", ["bind", "start", "resume"])
def test_inactive_binding_cannot_admit_execution(authority, monkeypatch, action):
    registry, _, payload = authority
    payload["goals"][0]["activation_state"] = "stopped"
    registry.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(bridge, "_node_command", lambda: pytest.fail("inactive Goal reached provider"))
    with pytest.raises(bridge.ZCodeGoalBridgeError, match="no longer active"):
        bridge.zcode_goal_operation(action=action, registry_path=registry, goal_id="delivery", agent_id="zcode-worker")


def test_inactive_cleanup_still_rejects_changed_instance_or_unregistered_agent(authority, monkeypatch):
    registry, _, payload = authority
    payload["goals"][0]["activation_state"] = "stopped"
    registry.write_text(json.dumps(payload), encoding="utf-8")
    def replace(request):
        payload["goals"][0]["goal_instance_id"] = INSTANCE_B
        registry.write_text(json.dumps(payload), encoding="utf-8")
    _stub_operation(monkeypatch, callback=replace)
    with pytest.raises(bridge.ZCodeGoalBridgeError, match="instance changed"):
        bridge.zcode_goal_operation(action="pause", registry_path=registry, goal_id="delivery", agent_id="zcode-worker")
    payload["goals"][0]["coordination"]["registered_agents"] = []
    registry.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="Register"):
        bridge.zcode_goal_operation(action="stop", registry_path=registry, goal_id="delivery", agent_id="zcode-worker")



def test_legacy_goal_retains_existing_alias_and_creation_witness(authority, monkeypatch):
    registry, _, payload = authority
    payload.pop("profile_id", None)
    del payload["goals"][0]["goal_instance_id"]
    payload["goals"][0]["creation_operation_id"] = "goal-create:first"
    registry.write_text(json.dumps(payload), encoding="utf-8")
    binding = _validate(authority)
    assert binding["goal_ref"] == {"goal_id": "delivery"}
    assert binding["identity_scope"] == "legacy_goal_alias"
    assert binding["goal_creation_operation_id"] == "goal-create:first"
    assert "goal_instance_id" not in json.loads(registry.read_text(encoding="utf-8"))["goals"][0]
    requests = _stub_operation(monkeypatch)
    bridge.zcode_goal_operation(action="status", registry_path=registry, goal_id="delivery", agent_id="zcode-worker")
    assert requests[0][1]["goal_creation_operation_id"] == "goal-create:first"
    with pytest.raises(bridge.ZCodeGoalBridgeError, match="creation witness changed"):
        _validate(authority, goal_ref={"goal_id": "delivery"}, goal_creation_operation_id="goal-create:other")


def test_legacy_recreated_alias_cannot_return_old_binding_success(authority, monkeypatch):
    registry, _, payload = authority
    payload.pop("profile_id", None)
    del payload["goals"][0]["goal_instance_id"]
    payload["goals"][0]["creation_operation_id"] = "goal-create:first"
    registry.write_text(json.dumps(payload), encoding="utf-8")
    def replace(request):
        payload["goals"][0]["creation_operation_id"] = "goal-create:second"
        registry.write_text(json.dumps(payload), encoding="utf-8")
    _stub_operation(monkeypatch, callback=replace)
    with pytest.raises(bridge.ZCodeGoalBridgeError, match="creation witness changed"):
        bridge.zcode_goal_operation(action="status", registry_path=registry, goal_id="delivery", agent_id="zcode-worker")



def test_registered_actor_needs_no_advisory_host_field(authority):
    registry, _, payload = authority
    payload["goals"][0]["coordination"]["agent_profiles"] = {}
    registry.write_text(json.dumps(payload), encoding="utf-8")
    assert _validate(authority)["agent_id"] == "zcode-worker"


def test_lifecycle_only_source_profile_stays_outside_business_runtime(authority, monkeypatch):
    registry, _, payload = authority
    payload["profile_id"] = "source_session_v1"
    registry.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(bridge, "_node_command", lambda: pytest.fail("lifecycle-only profile reached runtime"))
    with pytest.raises(ValueError, match="lifecycle-only"):
        bridge.zcode_goal_operation(action="bind", registry_path=registry, goal_id="delivery", agent_id="zcode-worker")


@pytest.mark.parametrize("selection", [None, {}, {"providerId": "local", "modelId": "m", "apiKey": "secret"}, {"providerId": "local", "modelId": "m", "options": {"unknown": "x"}}])
def test_model_selection_validates_transport_without_credentials(authority, monkeypatch, selection):
    registry, _, _ = authority
    monkeypatch.setattr(bridge, "_node_command", lambda: pytest.fail("invalid selection reached runtime"))
    with pytest.raises(ValueError):
        bridge.zcode_goal_operation(action="select_model", registry_path=registry, goal_id="delivery", agent_id="zcode-worker", model_selection=selection)


def test_model_selection_forwards_existing_model_only(authority, monkeypatch):
    registry, _, _ = authority
    requests = _stub_operation(monkeypatch)
    selection = {"providerId": "local", "modelId": "synthetic", "options": {"reasoningLevel": "low"}}
    bridge.zcode_goal_operation(action="select_model", registry_path=registry, goal_id="delivery", agent_id="zcode-worker", model_selection=selection)
    assert requests[0][1]["model_selection"] == selection
    assert "task_body" not in requests[0][1]
    with pytest.raises(ValueError, match="only.*select_model"):
        bridge.zcode_goal_operation(action="start", registry_path=registry, goal_id="delivery", agent_id="zcode-worker", model_selection=selection)



def test_internal_guard_requires_known_action_and_validates_inactive_cleanup(authority):
    registry, project, payload = authority
    payload["goals"][0]["activation_state"] = "stopped"
    registry.write_text(json.dumps(payload), encoding="utf-8")
    request = {"registry": str(registry), "project": str(project), "goal_id": "delivery", "agent_id": "zcode-worker", "goal_ref": {"goal_id": "delivery", "goal_instance_id": INSTANCE_A}}
    command = [sys.executable, "-X", "utf8", "-B", "-m", "loopx.zcode_goal_mode.bridge", "--validate-binding"]
    def invoke(action):
        body = dict(request)
        if action is not None:
            body["action"] = action
        result = subprocess.run(command, input=json.dumps(body), encoding="utf-8", capture_output=True, timeout=15)
        return result.returncode, json.loads(result.stdout)
    for action in (None, "unknown", "bind", "start", "resume", "select_model"):
        code, result = invoke(action)
        assert code == 1 and result["ok"] is False
    for action in ("status", "pause", "stop"):
        code, result = invoke(action)
        assert code == 0 and result["goal_ref"] == request["goal_ref"]
    payload["goals"][0]["goal_instance_id"] = INSTANCE_B
    registry.write_text(json.dumps(payload), encoding="utf-8")
    code, result = invoke("stop")
    assert code == 1 and result["error_code"] == "zcode_goal_authority_changed"


def test_cli_select_model_maps_alias_and_preserves_selection(authority, monkeypatch, capsys):
    from loopx import cli
    from loopx.cli_commands import zcode_goal
    registry, _, _ = authority
    seen = []
    monkeypatch.setattr(zcode_goal, "zcode_goal_operation", lambda **kwargs: seen.append(kwargs) or {"ok": True})
    assert cli.main(["--registry", str(registry), "--format", "json", "zcode-goal", "select-model", "--goal-id", "delivery", "--agent-id", "zcode-worker", "--provider-id", "local", "--model-id", "synthetic", "--reasoning-level", "disabled"]) == 0
    assert seen[0]["action"] == "select_model"
    assert seen[0]["model_selection"] == {"providerId": "local", "modelId": "synthetic", "options": {"reasoningLevel": "disabled"}}
    assert json.loads(capsys.readouterr().out)["ok"] is True



@pytest.mark.parametrize("identity", ["exact", "legacy_creation"])
def test_stale_expected_binding_rejects_before_any_provider_effect(authority, monkeypatch, identity):
    registry, _, payload = authority
    expected = {"goal_ref": {"goal_id": "delivery", "goal_instance_id": INSTANCE_A}, "goal_creation_operation_id": None}
    if identity == "exact":
        payload["goals"][0]["goal_instance_id"] = INSTANCE_B
    else:
        del payload["goals"][0]["goal_instance_id"]
        payload["goals"][0]["creation_operation_id"] = "goal-create:new"
        expected = {"goal_ref": {"goal_id": "delivery"}, "goal_creation_operation_id": "goal-create:old"}
    registry.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(bridge, "_node_command", lambda: pytest.fail("stale expectation reached Node discovery"))
    monkeypatch.setattr(bridge, "_run_json", lambda *args, **kwargs: pytest.fail("stale expectation reached provider effect"))
    with pytest.raises(bridge.ZCodeGoalBridgeError) as failure:
        bridge.zcode_goal_operation(action="start", registry_path=registry, goal_id="delivery", agent_id="zcode-worker", expected_binding=expected)
    assert failure.value.status == 409


def test_api_mutations_require_readback_expectation(authority, monkeypatch):
    from loopx.zcode_goal_mode import api
    registry, _, _ = authority
    handler = Handler(registry, body={"action": "start"})
    monkeypatch.setattr(api, "zcode_goal_operation", lambda **kwargs: pytest.fail("missing expectation reached provider"))
    assert handler._dispatch_zcode_goal(handler.path, apply=True)
    assert handler.sent[0][1]["status"] == 400


def test_api_creation_replacement_is_conflict_before_provider(authority, monkeypatch):
    registry, _, payload = authority
    del payload["goals"][0]["goal_instance_id"]
    payload["goals"][0]["creation_operation_id"] = "goal-create:new"
    registry.write_text(json.dumps(payload), encoding="utf-8")
    handler = Handler(registry, body={"action": "start", "expected_binding": {"goal_ref": {"goal_id": "delivery"}, "goal_creation_operation_id": "goal-create:old"}})
    monkeypatch.setattr(bridge, "_node_command", lambda: pytest.fail("old panel reached Node"))
    assert handler._dispatch_zcode_goal(handler.path, apply=True)
    assert handler.sent[0][1]["status"] == 409


@pytest.mark.parametrize("expected", [{}, {"goal_ref": {}}, {"goal_ref": {"goal_id": "delivery", "other": "x"}, "goal_creation_operation_id": None}, {"goal_ref": {"goal_id": "delivery"}, "goal_creation_operation_id": True}])
def test_expected_binding_has_one_exact_transport_shape(authority, monkeypatch, expected):
    registry, _, _ = authority
    monkeypatch.setattr(bridge, "_node_command", lambda: pytest.fail("malformed expectation reached provider"))
    with pytest.raises(ValueError):
        bridge.zcode_goal_operation(action="bind", registry_path=registry, goal_id="delivery", agent_id="zcode-worker", expected_binding=expected)



def test_provider_must_echo_creation_witness_even_when_null(authority, monkeypatch):
    registry, _, _ = authority
    _stub_operation(monkeypatch)
    monkeypatch.setattr(bridge, "_run_json", lambda *args, **kwargs: {"ok": True, "goal_id": "delivery", "goal_ref": {"goal_id": "delivery", "goal_instance_id": INSTANCE_A}, "agent_id": "zcode-worker"})
    with pytest.raises(bridge.ZCodeGoalBridgeError, match="did not match"):
        bridge.zcode_goal_operation(action="status", registry_path=registry, goal_id="delivery", agent_id="zcode-worker")
