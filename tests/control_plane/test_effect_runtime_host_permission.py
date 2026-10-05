"""Host permission failures must not look like optional-capability failures."""
from __future__ import annotations

import errno
import json
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
