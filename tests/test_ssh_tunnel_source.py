from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from unittest import mock
from types import SimpleNamespace

import pytest

from loopx.chat_ssh_source_api import SshSourceRequestMixin
from loopx.control_plane.goals.ssh_lifecycle_transport import (
    apply_ssh_goal_lifecycle,
)
from loopx.control_plane.status.ssh_tunnel import ensure_ssh_source
from loopx.global_registry import sync_project_registry_to_global
from loopx.history import load_registry


def test_ensure_ssh_source_opens_tunnel_and_returns_status_url() -> None:
    calls: list[list[str]] = []
    probe_count = 0

    def fake_loopback(port: int, **kwargs: object) -> bool:
        nonlocal probe_count
        probe_count += 1
        return probe_count >= 2

    with mock.patch(
        "loopx.control_plane.status.ssh_tunnel.configured_ssh_host_aliases",
        return_value=["ark-devbox"],
    ), mock.patch(
        "loopx.control_plane.status.ssh_tunnel._loopback_status_ok", fake_loopback
    ), mock.patch(
        "loopx.control_plane.status.ssh_tunnel._remote_status_ok", return_value=True
    ), mock.patch(
        "loopx.control_plane.status.ssh_tunnel.subprocess.run",
        side_effect=lambda args, **kwargs: calls.append(list(args)),
    ):
        result = ensure_ssh_source("ark-devbox", 8877, wait_seconds=0.01)

    assert result == {
        "ok": True,
        "status_url": "http://127.0.0.1:8877/status.json",
        "tunnel_required": True,
        "remote_started": False,
    }
    tunnel_call = calls[0]
    assert tunnel_call[0] == "ssh"
    assert "-L" in tunnel_call
    assert "8877:127.0.0.1:8766" in tunnel_call
    assert "ark-devbox" in tunnel_call
    # The alias and port are passed as separate argv entries, never through a shell.
    assert "; " not in " ".join(tunnel_call)


def test_ensure_ssh_source_starts_remote_status_when_missing() -> None:
    probe_count = 0

    def fake_loopback(port: int, **kwargs: object) -> bool:
        nonlocal probe_count
        probe_count += 1
        return probe_count >= 4

    with mock.patch(
        "loopx.control_plane.status.ssh_tunnel.configured_ssh_host_aliases",
        return_value=["ark-devbox"],
    ), mock.patch(
        "loopx.control_plane.status.ssh_tunnel._loopback_status_ok", fake_loopback
    ), mock.patch(
        "loopx.control_plane.status.ssh_tunnel._remote_status_ok", return_value=False
    ), mock.patch(
        "loopx.control_plane.status.ssh_tunnel._start_remote_status", return_value=None
    ) as start_remote, mock.patch(
        "loopx.control_plane.status.ssh_tunnel.subprocess.run", return_value=None
    ):
        result = ensure_ssh_source("ark-devbox", 8877, wait_seconds=0.01)

    start_remote.assert_called_once_with("ark-devbox")
    assert result["remote_started"] is True


def test_ensure_ssh_source_rejects_unknown_alias() -> None:
    with mock.patch(
        "loopx.control_plane.status.ssh_tunnel.configured_ssh_host_aliases",
        return_value=["ark-devbox"],
    ):
        with pytest.raises(ValueError, match="unknown SSH host alias"):
            ensure_ssh_source("evil-host;id", 8877)


def test_ensure_ssh_source_rejects_invalid_port() -> None:
    with mock.patch(
        "loopx.control_plane.status.ssh_tunnel.configured_ssh_host_aliases",
        return_value=["ark-devbox"],
    ):
        with pytest.raises(ValueError, match="local tunnel port"):
            ensure_ssh_source("ark-devbox", 22)
        with pytest.raises(ValueError, match="local tunnel port"):
            ensure_ssh_source("ark-devbox", "8877")


class _SshSourceHandler(SshSourceRequestMixin):
    def __init__(
        self,
        *,
        body: dict[str, object] | None = None,
        host: str = "127.0.0.1",
    ) -> None:
        self.server = SimpleNamespace(server_address=(host, 8767), ssh_config_path=None)
        self.body = body or {"host_alias": "ark-devbox", "local_port": 8877}
        self.errors: list[tuple[str, int]] = []
        self.payloads: list[dict[str, object]] = []

    def _read_json(self) -> dict[str, object]:
        return self.body

    def _require_loopback_origin(self) -> bool:
        return True

    def _send_error(self, message: str, **kwargs: object) -> None:
        self.errors.append((message, int(kwargs.get("status") or 400)))

    def _send_json(
        self, payload: dict[str, object], *, status: int = 200
    ) -> None:
        self.payloads.append(payload)


def test_ssh_source_request_mixin_delegates_validated_loopback_request() -> None:
    handler = _SshSourceHandler()
    receipt = {"ok": True, "status_url": "http://127.0.0.1:8877/status.json"}

    with mock.patch(
        "loopx.chat_ssh_source_api.ensure_ssh_source", return_value=receipt
    ) as ensure:
        handler._ssh_source_ensure()

    ensure.assert_called_once_with(
        "ark-devbox", 8877, ssh_config_path=None
    )
    assert handler.payloads == [receipt]
    assert handler.errors == []


def test_ssh_source_request_mixin_rejects_non_loopback_server() -> None:
    handler = _SshSourceHandler(host="0.0.0.0")

    with mock.patch("loopx.chat_ssh_source_api.ensure_ssh_source") as ensure:
        handler._ssh_source_ensure()

    ensure.assert_not_called()
    assert handler.payloads == []
    assert handler.errors == [
        ("SSH source management requires a loopback LoopX Chat server.", 403)
    ]


def test_apply_ssh_goal_lifecycle_uses_remote_typed_contract_without_local_fallback() -> None:
    completed = SimpleNamespace(
        returncode=0,
        stderr="",
        stdout=(
            '{"ok":true,"schema_version":"loopx_goal_activation_transition_v1",'
            '"execute":true,"goal_id":"remote-goal","after_state":"stopped",'
            '"changed":true,"readback":{"verified":true}}'
        ),
    )
    with mock.patch(
        "loopx.control_plane.goals.ssh_lifecycle_transport.configured_ssh_host_aliases",
        return_value=["ark-devbox"],
    ), mock.patch(
        "loopx.control_plane.goals.ssh_lifecycle_transport.subprocess.run",
        return_value=completed,
    ) as run:
        result = apply_ssh_goal_lifecycle(
            host_alias="ark-devbox",
            goal_id="remote-goal",
            operation="stop",
            reason="Stopped from the owner workspace",
        )

    argv = run.call_args.args[0]
    assert argv[:4] == ["ssh", "-o", "ConnectTimeout=5", "ark-devbox"]
    assert "goal-lifecycle" in argv[4]
    assert "--actor-kind owner" in argv[4]
    assert "--registry @host-global" in argv[4]
    assert result == {
        "ok": True,
        "schema_version": "loopx_remote_goal_lifecycle_v1",
        "host_alias": "ark-devbox",
        "goal_id": "remote-goal",
        "operation": "stop",
        "activation_state": "stopped",
        "changed": True,
        "projection_verified": True,
    }


@pytest.mark.parametrize("legacy", [False, True])
def test_ssh_lifecycle_uses_remote_global_registry_with_project_cwd(
    tmp_path: Path, legacy: bool,
) -> None:
    home = tmp_path / "home"
    runtime_root = home / (".codex/loopx" if legacy else ".loopx")
    project = tmp_path / "target-project"
    source_registry = project / ".loopx" / "registry.json"
    source_registry.parent.mkdir(parents=True)
    source_registry.write_text(
        json.dumps({
            "schema_version": "0.1",
            "common_runtime_root": str(runtime_root),
            "goals": [{
                "id": "shared",
                "display_name": "Target Goal",
                "repo": str(project),
                "quota": {"compute": 1, "allowed_slots": 4, "spent_slots": 0},
            }],
        }),
        encoding="utf-8",
    )
    synced = sync_project_registry_to_global(
        registry_path=source_registry,
        runtime_root_override=str(runtime_root),
        goal_id="shared",
        dry_run=False,
    )
    assert synced["ok"] is True
    global_registry = runtime_root / "registry.global.json"

    # A project-local registry in the remote HOME used to win implicit CLI
    # selection. The host-global selector must ignore this unrelated Goal.
    local_registry = home / ".loopx" / "registry.json"
    local_registry.parent.mkdir(parents=True, exist_ok=True)
    local_registry.write_text(
        json.dumps({
            "schema_version": "0.1",
            "common_runtime_root": str(home / "unrelated-runtime"),
            "goals": [{"id": "shared", "repo": str(home)}],
        }),
        encoding="utf-8",
    )
    local_before = local_registry.read_bytes()
    bin_path = home / ".local" / "bin" / "loopx"
    bin_path.parent.mkdir(parents=True)
    bin_path.write_text(
        f"#!/bin/sh\nexec {shlex.quote(sys.executable)} -m loopx.cli \"$@\"\n",
        encoding="utf-8",
    )
    bin_path.chmod(0o755)
    env = dict(os.environ, HOME=str(home), LOOPX_USAGE_PING="0")
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    env.pop("LOOPX_REGISTRY", None)
    env.pop("LOOPX_RUNTIME_ROOT", None)
    real_run = subprocess.run

    def execute_remote(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert "--registry @host-global" in argv[-1]
        return real_run(["/bin/sh", "-c", argv[-1]], cwd=home, env=env, **kwargs)

    with mock.patch(
        "loopx.control_plane.goals.ssh_lifecycle_transport.configured_ssh_host_aliases",
        return_value=["fixture-host"],
    ), mock.patch(
        "loopx.control_plane.goals.ssh_lifecycle_transport.subprocess.run",
        side_effect=execute_remote,
    ):
        result = apply_ssh_goal_lifecycle(
            host_alias="fixture-host", goal_id="shared", operation="stop",
        )

    assert result["activation_state"] == "stopped"
    assert local_registry.read_bytes() == local_before
    goal = load_registry(global_registry)["goals"][0]
    assert goal["activation"]["state"] == "stopped"


def test_apply_ssh_goal_lifecycle_fails_closed_for_unconfigured_host() -> None:
    with mock.patch(
        "loopx.control_plane.goals.ssh_lifecycle_transport.configured_ssh_host_aliases",
        return_value=["ark-devbox"],
    ), mock.patch(
        "loopx.control_plane.goals.ssh_lifecycle_transport.subprocess.run"
    ) as run:
        with pytest.raises(ValueError, match="unknown SSH host alias"):
            apply_ssh_goal_lifecycle(
                host_alias="other-host",
                goal_id="remote-goal",
                operation="stop",
            )

    run.assert_not_called()


def test_apply_ssh_goal_lifecycle_quotes_remote_goal_identity() -> None:
    goal_id = "goal; touch /tmp/not-created"
    completed = SimpleNamespace(
        returncode=0,
        stderr="",
        stdout=(
            '{"ok":true,"schema_version":"loopx_goal_activation_transition_v1",'
            '"execute":true,"goal_id":"goal; touch /tmp/not-created",'
            '"after_state":"stopped","changed":true,'
            '"readback":{"verified":true}}'
        ),
    )
    with mock.patch(
        "loopx.control_plane.goals.ssh_lifecycle_transport.configured_ssh_host_aliases",
        return_value=["ark-devbox"],
    ), mock.patch(
        "loopx.control_plane.goals.ssh_lifecycle_transport.subprocess.run",
        return_value=completed,
    ) as run:
        apply_ssh_goal_lifecycle(
            host_alias="ark-devbox",
            goal_id=goal_id,
            operation="stop",
        )

    command = run.call_args.args[0][4]
    assert "--goal-id 'goal; touch /tmp/not-created'" in command


def test_ssh_goal_lifecycle_handler_returns_verified_remote_receipt() -> None:
    handler = _SshSourceHandler(body={
        "goal_id": "remote-goal",
        "host_alias": "ark-devbox",
        "operation": "stop",
        "reason": "Stopped from the owner workspace",
    })
    receipt = {
        "ok": True,
        "schema_version": "loopx_remote_goal_lifecycle_v1",
        "projection_verified": True,
    }
    with mock.patch(
        "loopx.chat_ssh_source_api.apply_ssh_goal_lifecycle",
        return_value=receipt,
    ) as apply:
        handler._ssh_goal_lifecycle()

    apply.assert_called_once_with(
        host_alias="ark-devbox",
        goal_id="remote-goal",
        operation="stop",
        reason="Stopped from the owner workspace",
        ssh_config_path=None,
    )
    assert handler.payloads == [receipt]
    assert handler.errors == []
