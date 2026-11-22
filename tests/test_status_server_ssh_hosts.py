from __future__ import annotations

import http.client
import json
from pathlib import Path
import re
import threading

import pytest

from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.control_plane.effect_runtime import (
    EffectRuntimeRejected,
    effect_runtime_result,
)
from loopx.control_plane.status.ssh_host_catalog import (
    SSH_HOST_CATALOG_PATH,
    _SAFE_HOST_ALIAS,
    configured_ssh_host_aliases,
    project_ssh_host_catalog,
    ssh_host_catalog_payload,
)
from loopx.status_server import DEFAULT_SSH_HOSTS_PATH, StatusHTTPServer, StatusRequestHandler


def _write_config(ssh_directory: Path) -> Path:
    included = ssh_directory / "conf.d"
    included.mkdir(parents=True)
    (included / "jump.conf").write_text(
        "Host jump-box\n  HostName 192.0.2.12\n",
        encoding="utf-8",
    )
    config = ssh_directory / "config"
    config.write_text(
        "\n".join(
            [
                "Include conf.d/*.conf",
                "Host workstation *.example !blocked",
                "  HostName 192.0.2.10",
                "Host workstation",
                "Host -oProxyCommand=unsafe",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return config


def test_configured_ssh_host_aliases_exposes_only_explicit_safe_aliases(tmp_path: Path) -> None:
    config = _write_config(tmp_path / ".ssh")

    assert configured_ssh_host_aliases(config) == ["jump-box", "workstation"]


def test_ssh_hosts_endpoint_returns_aliases_without_config_details(tmp_path: Path) -> None:
    config = _write_config(tmp_path / ".ssh")
    server = StatusHTTPServer(("127.0.0.1", 0), StatusRequestHandler)
    server.ssh_config_path = config
    server.verbose = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        connection.request(
            "GET",
            DEFAULT_SSH_HOSTS_PATH,
            headers={"Origin": "http://127.0.0.1:5173"},
        )
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert response.status == 200
    assert payload == {
        "ok": True,
        "schema_version": "ssh_host_catalog_v0",
        "hosts": [{"alias": "jump-box"}, {"alias": "workstation"}],
    }
    assert "192.0.2" not in json.dumps(payload)


def test_ssh_hosts_endpoint_rejects_non_loopback_browser_origin(tmp_path: Path) -> None:
    server = StatusHTTPServer(("127.0.0.1", 0), StatusRequestHandler)
    server.ssh_config_path = _write_config(tmp_path / ".ssh")
    server.verbose = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        connection.request(
            "GET",
            DEFAULT_SSH_HOSTS_PATH,
            headers={"Origin": "https://example.com"},
        )
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert response.status == 403
    assert payload["ok"] is False


def test_packaged_chat_serves_ssh_hosts_on_its_custom_loopback_port(tmp_path: Path) -> None:
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.ssh_config_path = _write_config(tmp_path / ".ssh")
    server.verbose = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_address[1], timeout=5
        )
        connection.request("GET", SSH_HOST_CATALOG_PATH)
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert response.status == 200
    assert payload == {
        "ok": True,
        "schema_version": "ssh_host_catalog_v0",
        "hosts": [{"alias": "jump-box"}, {"alias": "workstation"}],
    }
    assert "192.0.2" not in json.dumps(payload)


def test_packaged_chat_ssh_hosts_rejects_non_loopback_origin(tmp_path: Path) -> None:
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.ssh_config_path = _write_config(tmp_path / ".ssh")
    server.verbose = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_address[1], timeout=5
        )
        connection.request(
            "GET",
            SSH_HOST_CATALOG_PATH,
            headers={"Origin": "https://example.com"},
        )
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert response.status == 403
    assert payload["ok"] is False


def test_ssh_host_catalog_feature_off_parity_and_sk_substring_aliases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _unexpected_runtime(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("feature-off ssh_host_catalog_payload must not start effect_runtime")

    monkeypatch.setattr(
        "loopx.control_plane.effect_runtime.effect_runtime_result",
        _unexpected_runtime,
    )
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir(parents=True)
    config = ssh_dir / "config"
    config.write_text(
        "Host jump-box lab.sk-integration desk-box task-worker disk-01 kiosk-2 sk-integration bad..alias\n"
        "  HostName 192.0.2.20\n",
        encoding="utf-8",
    )

    expected = {
        "ok": True,
        "schema_version": "ssh_host_catalog_v0",
        "hosts": [
            {"alias": "jump-box"},
            {"alias": "lab.sk-integration"},
            {"alias": "desk-box"},
            {"alias": "task-worker"},
            {"alias": "disk-01"},
            {"alias": "kiosk-2"},
            {"alias": "sk-integration"},
        ],
    }
    assert ssh_host_catalog_payload(config) == expected
    assert ssh_host_catalog_payload(config, observations=[]) == expected

    contract_ts = (
        Path(__file__).resolve().parents[1]
        / "loopx/control_plane/collaboration/session_quota_fabric_contract.ts"
    ).read_text(encoding="utf-8")
    match = re.search(r"export const SAFE_HOST_ALIAS = /([^/]+)/;", contract_ts)
    assert match is not None
    assert match.group(1) == _SAFE_HOST_ALIAS.pattern


def test_ssh_host_catalog_observation_round_trip_and_handover_handler(tmp_path: Path) -> None:
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir(parents=True)
    config = ssh_dir / "config"
    config.write_text("Host jump-box lab.sk-integration desk-box task-worker\n", encoding="utf-8")

    jump_observation = {
        "schema_version": "session_fabric_ssh_observation_v0",
        "alias": "jump-box",
        "connection_state": "disconnected",
        "failure_code": "unreachable",
        "checked_at": "2026-11-20T00:00:00Z",
        "tunnel_latency_ms": None,
    }
    projected = ssh_host_catalog_payload(
        config,
        observations=[jump_observation],
    )
    assert projected == {
        "ok": True,
        "schema_version": "ssh_host_catalog_v0",
        "hosts": [
            {
                "alias": "jump-box",
                "observation": jump_observation,
            },
            {"alias": "lab.sk-integration"},
            {"alias": "desk-box"},
            {"alias": "task-worker"},
        ],
    }

    lab_observation = {
        "schema_version": "session_fabric_ssh_observation_v0",
        "alias": "lab.sk-integration",
        "connection_state": "connected",
        "failure_code": None,
        "checked_at": "2026-11-22T00:00:00Z",
        "tunnel_latency_ms": 9,
    }
    projected_lab = ssh_host_catalog_payload(
        config,
        observations=[lab_observation],
    )
    assert projected_lab == {
        "ok": True,
        "schema_version": "ssh_host_catalog_v0",
        "hosts": [
            {"alias": "jump-box"},
            {
                "alias": "lab.sk-integration",
                "observation": lab_observation,
            },
            {"alias": "desk-box"},
            {"alias": "task-worker"},
        ],
    }

    with pytest.raises(ValueError, match="absent from ssh_host_catalog_v0"):
        project_ssh_host_catalog(
            ["desk-box"],
            [
                {
                    "schema_version": "session_fabric_ssh_observation_v0",
                    "alias": "phantom-box",
                    "connection_state": "connected",
                    "failure_code": None,
                    "checked_at": None,
                    "tunnel_latency_ms": 5,
                }
            ],
        )

    with pytest.raises(ValueError, match="duplicate SSH host observation"):
        project_ssh_host_catalog(
            ["jump-box"],
            [jump_observation, jump_observation],
        )

    handover = effect_runtime_result(
        "collaboration.session_fabric.evaluate_governed_session_handover",
        {
            "schema_version": "governed_session_handover_proposal_v0",
            "goal_id": "demo-goal",
            "todo_id": "todo_sample01",
            "agent_id": "antigravity",
            "from_profile_id": "profile-a",
            "to_profile_id": "profile-b",
            "from_session_mode": "managed_runtime",
            "to_session_mode": "managed_runtime",
            "quota_observation": {
                "schema_version": "session_quota_window_observation_v0",
                "profile_id": "profile-a",
                "availability": "available",
                "used_ratio": 0.95,
                "resets_at": None,
                "eligible_models": ["gpt-codex"],
            },
            "threshold_ratio": 0.9,
            "current_turn_fenced": True,
            "previous_lease_released": True,
            "ask_before_switch": False,
        },
    )
    assert handover["disposition"] == "no_handover_needed"
    assert handover["ready_for_reacquisition"] is False

    handover_exhausted = effect_runtime_result(
        "collaboration.session_fabric.evaluate_governed_session_handover",
        {
            "schema_version": "governed_session_handover_proposal_v0",
            "goal_id": "demo-goal",
            "todo_id": "todo_sample01",
            "agent_id": "antigravity",
            "from_profile_id": "profile-a",
            "to_profile_id": "profile-b",
            "from_session_mode": "attached_host",
            "to_session_mode": "attached_host",
            "quota_observation": {
                "schema_version": "session_quota_window_observation_v0",
                "profile_id": "profile-a",
                "availability": "exhausted",
                "used_ratio": 0.5,
                "resets_at": None,
                "eligible_models": ["gpt-codex"],
            },
            "threshold_ratio": 0.9,
            "current_turn_fenced": True,
            "previous_lease_released": True,
            "ask_before_switch": False,
        },
    )
    assert handover_exhausted["disposition"] == "ready_for_lease_reacquisition"
    assert handover_exhausted["ready_for_reacquisition"] is True

    sensitive_observation = {
        "schema_version": "session_fabric_ssh_observation_v0",
        "alias": "sk-" + "proj-abcdef12",
        "connection_state": "connected",
        "failure_code": None,
        "checked_at": None,
        "tunnel_latency_ms": 5,
    }
    with pytest.raises(ValueError, match="credentials"):
        project_ssh_host_catalog(["jump-box"], [sensitive_observation])

    with pytest.raises(EffectRuntimeRejected, match="credentials"):
        effect_runtime_result(
            "collaboration.session_fabric.project_ssh_host_catalog",
            {"configured_aliases": ["jump-box"], "observations": [sensitive_observation]},
        )

    with pytest.raises(ValueError, match="explicit safe OpenSSH Host alias"):
        project_ssh_host_catalog(["../bad"])


def _fetch_http_ssh_hosts(
    server_cls: type[StatusHTTPServer] | type[ChatHTTPServer],
    handler_cls: type[StatusRequestHandler] | type[ChatRequestHandler],
    config: Path,
    path: str,
) -> bytes:
    server = server_cls(("127.0.0.1", 0), handler_cls)
    server.ssh_config_path = config
    server.verbose = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        connection.request("GET", path)
        response = connection.getresponse()
        assert response.status == 200
        return response.read()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_ssh_hosts_http_servers_and_opt_in_observation_preserve_unobserved_aliases(
    tmp_path: Path,
) -> None:
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir(parents=True)
    config = ssh_dir / "config"
    config.write_text(
        "Host jump-box lab.sk-integration desk-box\n  HostName 192.0.2.30\n",
        encoding="utf-8",
    )

    status_raw = _fetch_http_ssh_hosts(
        StatusHTTPServer, StatusRequestHandler, config, DEFAULT_SSH_HOSTS_PATH,
    )
    chat_raw = _fetch_http_ssh_hosts(
        ChatHTTPServer, ChatRequestHandler, config, SSH_HOST_CATALOG_PATH,
    )
    expected_default = {
        "ok": True,
        "schema_version": "ssh_host_catalog_v0",
        "hosts": [
            {"alias": "jump-box"},
            {"alias": "lab.sk-integration"},
            {"alias": "desk-box"},
        ],
    }
    assert json.loads(status_raw.decode("utf-8")) == expected_default
    assert json.loads(chat_raw.decode("utf-8")) == expected_default
    assert status_raw == json.dumps(expected_default, indent=2).encode("utf-8")
    assert chat_raw == json.dumps(expected_default, separators=(",", ":")).encode("utf-8")

    jump_observation = {
        "schema_version": "session_fabric_ssh_observation_v0",
        "alias": "jump-box",
        "connection_state": "connected",
        "failure_code": None,
        "checked_at": "2026-11-22T00:00:00Z",
        "tunnel_latency_ms": 11,
    }
    projected = ssh_host_catalog_payload(config, observations=[jump_observation])
    assert projected == {
        "ok": True,
        "schema_version": "ssh_host_catalog_v0",
        "hosts": [
            {"alias": "jump-box", "observation": jump_observation},
            {"alias": "lab.sk-integration"},
            {"alias": "desk-box"},
        ],
    }
