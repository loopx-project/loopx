"""Host permission failures must not look like optional-capability failures."""
from __future__ import annotations

import errno
import json
import os
from pathlib import Path

import pytest

from loopx.cli_commands.quota_failure_report import quota_failure_payload
from loopx.cli import build_parser, main
from loopx.control_plane import effect_runtime


def _locator(tmp_path: Path, monkeypatch) -> tuple[Path, dict[str, object]]:
    locator = tmp_path / "locator.json"
    info = {"host": "127.0.0.1", "port": 1, "token": "private-token"}
    locator.write_text(json.dumps(info))
    monkeypatch.setattr(effect_runtime, "_runtime_fingerprint_for_request", lambda: "fixture")
    monkeypatch.setattr(effect_runtime, "_runtime_info_path", lambda _: locator)
    monkeypatch.setattr(effect_runtime, "_read_info", lambda *_a, **_k: info)
    monkeypatch.setattr(effect_runtime, "_start_runtime", lambda **_: pytest.fail("no replacement"))
    return locator, info


def test_connection_permission_denial_stops_before_dispatch_without_retry(
    tmp_path: Path, monkeypatch,
) -> None:
    locator, info = _locator(tmp_path, monkeypatch)
    calls: list[object] = []
    denial = PermissionError(errno.EPERM, "private permission details")

    def denied(address, **_kwargs):
        calls.append(address)
        raise denial

    monkeypatch.setattr(effect_runtime.socket, "create_connection", denied)
    with pytest.raises(effect_runtime.EffectRuntimeStartupError) as raised:
        effect_runtime.effect_runtime_request("capabilities.pr_review.configuration", {})
    assert raised.value.__cause__ is denial
    assert raised.value.diagnostic_code == "runtime_host_permission_denied"
    assert len(calls) == 1
    assert json.loads(locator.read_text()) == info
    assert "private" not in str(raised.value)
    assert "capabilities.pr_review" not in str(raised.value)


def test_startup_permission_denial_is_not_retried(tmp_path: Path, monkeypatch) -> None:
    _locator(tmp_path, monkeypatch)
    monkeypatch.setattr(effect_runtime, "_read_info", lambda *_a, **_k: None)
    calls: list[str] = []

    def denied(**_kwargs):
        calls.append("startup")
        raise PermissionError(errno.EACCES, "private startup path")

    monkeypatch.setattr(effect_runtime, "_start_runtime", denied)
    with pytest.raises(effect_runtime.EffectRuntimeHostPermissionError):
        effect_runtime.effect_runtime_request("runtime.ping", {})
    assert calls == ["startup"]


def test_process_launch_permission_denial_preserves_type_and_releases_lock(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(effect_runtime, "_read_info", lambda *_a, **_k: None)
    monkeypatch.setattr(effect_runtime, "_node_executable", lambda: "node")
    denial = PermissionError(errno.EACCES, "private executable path")

    def denied(*_args, **_kwargs):
        raise denial

    monkeypatch.setattr(effect_runtime.subprocess, "Popen", denied)
    with pytest.raises(effect_runtime.EffectRuntimeHostPermissionError) as raised:
        effect_runtime._start_runtime(fingerprint="fixture", info_path=tmp_path / "locator.json")
    assert raised.value.__cause__ is denial
    assert not list(tmp_path.glob("start-*.lock"))


def test_cli_configuration_collection_reports_host_fault_without_review_execution(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    from loopx.cli_commands import quota as command
    from loopx.capabilities.pr_review_queue.goal_configuration import configuration_summary

    _locator(tmp_path, monkeypatch)
    registry = tmp_path / "registry.json"
    registry.write_text("{}")

    def denied(*_args, **_kwargs):
        raise PermissionError(errno.EPERM, "private sandbox details")

    monkeypatch.setattr(effect_runtime.socket, "create_connection", denied)

    def collect(**_kwargs):
        return configuration_summary({"control_plane": {"pull_request_review": {
            "schema_version": "pull_request_review_goal_configuration_v0",
            "wait_for_ci": True,
        }}})

    monkeypatch.setattr(command, "collect_status", collect)
    assert main(["--format", "json", "--registry", str(registry), "--runtime-root",
                 str(tmp_path), "quota", "status"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error_code"] == "quota_runtime_permission_denied"
    assert "host-approved" in payload["health_items"][0]["recommended_action"]
    assert "private sandbox" not in json.dumps(payload)
    assert registry.read_text() == "{}"


def test_permission_error_after_send_remains_ambiguous(tmp_path: Path, monkeypatch) -> None:
    _locator(tmp_path, monkeypatch)
    sends: list[bytes] = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def settimeout(self, _timeout):
            pass

        def sendall(self, data):
            sends.append(data)
            raise PermissionError(errno.EPERM, "may have sent a prefix")

    monkeypatch.setattr(effect_runtime.socket, "create_connection", lambda *_a, **_k: Connection())
    with pytest.raises(effect_runtime.EffectRuntimeResponseAmbiguous) as raised:
        effect_runtime.effect_runtime_request("todo.complete", {})
    assert raised.value.diagnostic_code == "runtime_response_ambiguous"
    assert len(sends) == 1


@pytest.mark.parametrize("command", ["should-run", "monitor-poll", "spend-slot", "status"])
def test_quota_projects_host_recovery_without_capability_activation(
    tmp_path: Path, command: str,
) -> None:
    error = effect_runtime.EffectRuntimeHostPermissionError()
    args = build_parser().parse_args(["quota", command, "--goal-id", "fixture-goal"])
    payload = quota_failure_payload(args, registry_path=tmp_path / "registry.json",
                                    runtime_root_arg=str(tmp_path), error=error)
    assert payload["ok"] is False
    assert payload["error_code"] == "quota_runtime_permission_denied"
    if command == "status":
        action = payload["health_items"][0]["recommended_action"]
    else:
        assert payload["should_run"] is False
        assert payload["decision"] == "skip"
        assert payload["waiting_on"] == "codex"
        assert payload["reason"] == str(error)
        action = payload["recommended_action"]
    assert "host-approved" in action
    assert "same" in action and "Turn" in action
    assert "optional capabilities" in action
    assert "requires_user_action" not in payload
    assert "verbose_debug" not in payload


@pytest.mark.parametrize("retry_safe", [True, False])
def test_real_locator_denial_never_launches_or_dispatches_and_recovers(
    tmp_path: Path, monkeypatch, capsys, retry_safe: bool,
) -> None:
    from loopx.cli_commands import quota as command
    from loopx.capabilities.pr_review_queue.goal_configuration import configuration_summary

    runtime_dir = tmp_path / "runtime"
    monkeypatch.setattr(effect_runtime, "_runtime_dir", lambda: runtime_dir)
    # Bound the historical swallowed-denial failure; successful startup is unaffected.
    monkeypatch.setattr(effect_runtime, "STARTUP_READY_TIMEOUT_SECONDS", 2)
    fingerprint = effect_runtime._runtime_fingerprint_for_request()
    locator = effect_runtime._runtime_info_path(fingerprint)
    registry = tmp_path / "registry.json"
    registry.write_text("{}")
    identity = {"goal_id": "fixture-goal", "agent_id": "fixture-agent"}
    reads = []
    launches = []
    dispatches = []
    original_read = Path.read_text
    original_popen = effect_runtime.subprocess.Popen
    original_request = effect_runtime._request_with_info
    denial = PermissionError(errno.EACCES, "private locator details")
    denied = True

    def read(path, *args, **kwargs):
        if path == locator:
            reads.append(path)
            if denied:
                raise denial
        return original_read(path, *args, **kwargs)

    def launch(args, *rest, **kwargs):
        if "--info" in args:
            launches.append(args)
        return original_popen(args, *rest, **kwargs)

    def dispatch(info, **kwargs):
        dispatches.append(kwargs["method"])
        return original_request(info, **kwargs)

    goal = {"control_plane": {"pull_request_review": {
        "schema_version": "pull_request_review_goal_configuration_v0", "wait_for_ci": False,
    }}}
    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setattr(effect_runtime.subprocess, "Popen", launch)
    monkeypatch.setattr(effect_runtime, "_request_with_info", dispatch)
    monkeypatch.setattr(command, "collect_status", lambda **_: configuration_summary(goal))
    # The real reader sees a denial before any startup, regardless of retry mode.
    with pytest.raises(effect_runtime.EffectRuntimeHostPermissionError) as raised:
        effect_runtime.effect_runtime_request("runtime.ping", {}, retry_safe=retry_safe)
    assert raised.value.__cause__ is denial
    assert len(reads) == 1
    reads.clear()
    assert main(["--format", "json", "--registry", str(registry), "--runtime-root",
                 str(tmp_path), "quota", "status", "--goal-id", identity["goal_id"],
                 "--agent-id", identity["agent_id"]]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error_code"] == "quota_runtime_permission_denied"
    assert "host-approved" in payload["health_items"][0]["recommended_action"]
    assert "private locator details" not in json.dumps(payload)
    assert len(reads) == 1 and not launches and not dispatches
    assert registry.read_text() == "{}" and not locator.exists()
    denied = False
    try:
        assert configuration_summary(goal) == {"wait_for_ci": False, "review_order": "forward"}
        assert len(launches) == 1
        assert dispatches == ["capabilities.pr_review.configuration"]
        assert registry.read_text() == "{}"
        assert effect_runtime._read_info(locator, fingerprint=fingerprint) is not None
    finally:
        effect_runtime.restart_effect_runtime()


@pytest.mark.parametrize("deep", [False, True])
def test_readiness_preserves_locator_permission_diagnostic_and_recovery(
    tmp_path: Path, monkeypatch, deep: bool,
) -> None:
    monkeypatch.setattr(effect_runtime, "_runtime_dir", lambda: tmp_path)
    monkeypatch.setattr(effect_runtime, "_probe_node", lambda: ("ready", "node", "24.0.0"))
    locator = effect_runtime._runtime_info_path(effect_runtime._runtime_fingerprint())
    original = Path.read_text

    def read(path, *args, **kwargs):
        if path == locator:
            raise PermissionError(errno.EACCES, "private locator details")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    result = effect_runtime.collect_effect_runtime_readiness(deep=deep)
    assert result["ready"] is False
    assert result["runtime_lifecycle"]["diagnostic_code"] == "runtime_host_permission_denied"
    assert "host-approved" in result["recommended_action"]
    assert "private locator details" not in json.dumps(result)


@pytest.mark.parametrize("contents", [None, "{", "[]", "{}"])
def test_real_locator_missing_or_invalid_still_allows_discovery(
    tmp_path: Path, contents: str | None,
) -> None:
    locator = tmp_path / "locator.json"
    if contents is not None:
        locator.write_text(contents)
    assert effect_runtime._read_info(locator, fingerprint="fixture") is None
    if contents is not None:
        assert locator.read_text() == contents


def test_directory_locator_is_unusable_even_when_open_reports_permission_denied(
    tmp_path: Path, monkeypatch,
) -> None:
    locator = tmp_path / "locator.json"
    locator.mkdir()
    original_read = Path.read_text

    def read(path, *args, **kwargs):
        if path == locator:
            raise PermissionError(errno.EACCES, "directory cannot be opened as a file")
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    assert effect_runtime._read_info(locator, fingerprint="fixture") is None
    assert locator.is_dir(), "discovery must not remove a foreign locator"


def test_real_locator_live_identity_is_unchanged(tmp_path: Path) -> None:
    locator = tmp_path / "locator.json"
    info = {"schema_version": effect_runtime.EFFECT_RUNTIME_INFO_SCHEMA_VERSION,
            "fingerprint": "fixture", "host": "127.0.0.1", "port": 1,
            "token": "fixture-token", "pid": os.getpid()}
    locator.write_text(json.dumps(info))
    assert effect_runtime._read_info(locator, fingerprint="fixture") == info
    assert effect_runtime._read_info(locator, fingerprint="other") is None


def test_locator_denied_during_real_startup_stops_owned_child(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(effect_runtime, "_runtime_dir", lambda: tmp_path)
    fingerprint = effect_runtime._runtime_fingerprint_for_request()
    locator = effect_runtime._runtime_info_path(fingerprint)
    original_read = Path.read_text
    original_popen = effect_runtime.subprocess.Popen
    children = []
    denied = True

    def launch(args, *rest, **kwargs):
        child = original_popen(args, *rest, **kwargs)
        if "--info" in args:
            children.append(child)
        return child

    def read(path, *args, **kwargs):
        if path == locator and children and denied:
            raise PermissionError(errno.EACCES, "private readiness locator details")
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(effect_runtime.subprocess, "Popen", launch)
    monkeypatch.setattr(Path, "read_text", read)
    try:
        with pytest.raises(effect_runtime.EffectRuntimeHostPermissionError):
            effect_runtime.effect_runtime_request("runtime.ping", {})
        assert len(children) == 1
        children[0].wait(timeout=5)
        assert not list(tmp_path.glob("start-*.lock"))
    finally:
        denied = False
        for child in children:
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=5)
        effect_runtime.restart_effect_runtime()


def test_deep_readiness_connection_denial_keeps_host_recovery(
    tmp_path: Path, monkeypatch,
) -> None:
    _locator(tmp_path, monkeypatch)
    monkeypatch.setattr(effect_runtime, "_probe_node", lambda: ("ready", "node", "24.0.0"))

    def denied(*_args, **_kwargs):
        raise PermissionError(errno.EPERM, "private connection details")

    monkeypatch.setattr(effect_runtime.socket, "create_connection", denied)
    result = effect_runtime.collect_effect_runtime_readiness(deep=True)
    assert result["ready"] is False and result["semantic_probe"] == "failed"
    assert result["runtime_lifecycle"]["diagnostic_code"] == "runtime_host_permission_denied"
    assert "host-approved" in result["recommended_action"]
    assert "reinstall" not in result["recommended_action"]
