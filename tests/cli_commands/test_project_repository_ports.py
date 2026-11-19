from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


def _project_cli_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("LOOPX_")}
    # Remove business overrides, but keep the suite's telemetry opt-out in children.
    env["LOOPX_USAGE_PING"] = "0"
    return env


@pytest.mark.parametrize("inherited_usage_ping", [None, "0", "1"])
def test_project_cli_environment_disables_telemetry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inherited_usage_ping: str | None
) -> None:
    if inherited_usage_ping is None:
        monkeypatch.delenv("LOOPX_USAGE_PING", raising=False)
    else:
        monkeypatch.setenv("LOOPX_USAGE_PING", inherited_usage_ping)
    monkeypatch.setenv("LOOPX_USAGE_PING_ENDPOINT", "https://collector.invalid/events")
    monkeypatch.setenv("LOOPX_RUNTIME_ROOT", str(tmp_path / "unwanted-runtime"))
    # Other opt-outs must not mask losing LOOPX_USAGE_PING during filtering.
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.delenv("CI", raising=False)
    env = _project_cli_env()
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent("""
            import json
            import os
            import socket
            import subprocess
            from unittest.mock import patch

            # Fail closed: this probe cannot open a socket or launch a sender,
            # even if a regression accidentally enables telemetry.
            with (
                patch.object(socket, "socket", side_effect=AssertionError("network forbidden")) as network,
                patch.object(subprocess, "Popen", side_effect=AssertionError("sender forbidden")) as sender,
                patch("loopx.usage_ping.control") as control,
                patch("loopx.usage_ping._detach") as detach,
            ):
                from loopx import usage_ping
                ticket = usage_ping.begin("project")
                usage_ping.finish(ticket, "project", 0)
                assert ticket is None
                control.assert_not_called()
                detach.assert_not_called()
                network.assert_not_called()
                sender.assert_not_called()
            print(json.dumps({k: v for k, v in os.environ.items() if k.startswith("LOOPX_")}))
        """)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"LOOPX_USAGE_PING": "0"}


@pytest.mark.parametrize(
    ("scheme", "default_port", "other_port"),
    [("ssh", 22, 443), ("https", 443, 22), ("git", 9418, 80)],
)
def test_project_cli_distinguishes_nondefault_repository_ports(
    tmp_path: Path, scheme: str, default_port: int, other_port: int
) -> None:
    registry = tmp_path / "registry.json"
    env = _project_cli_env()

    def run(*args: str) -> tuple[int, dict]:
        result = subprocess.run(
            [
                sys.executable, "-m", "loopx.cli", "--format", "json",
                "--registry", str(registry), "--runtime-root", str(tmp_path / "runtime"),
                "project", *args,
            ],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
        assert result.stdout, result.stderr
        return result.returncode, json.loads(result.stdout)

    ordinary = f"{scheme}://code.example.com/team/repo.git"
    separate = f"{scheme}://code.example.com:{other_port}/team/repo.git"
    for project_id, remote in [("ordinary", ordinary), ("separate", separate)]:
        code, payload = run(
            "register", "--project-id", project_id, "--project-kind", "work",
            "--knowledge-root", str(tmp_path / project_id), "--goal-id", project_id,
            "--objective", "Inspect the synthetic repository.",
            "--acceptance", "Resolve only the matching repository endpoint.",
            "--next-effect", "Read the repository identity.",
            "--stop-condition", "Stop before remote access.",
            "--repository", remote,
        )
        assert code == 0, payload

    before = registry.read_bytes()
    for remote, expected in [
        (ordinary, "ordinary"),
        (f"{scheme}://code.example.com:{default_port}/team/repo.git", "ordinary"),
        (separate, "separate"),
    ]:
        code, payload = run("resolve", "--repository", remote)
        assert code == 0, payload
        assert payload["resolution"] == "resolved"
        assert payload["project_id"] == expected
    assert registry.read_bytes() == before
    projects = json.loads(before)["projects"]
    assert [project["repository_bindings"] for project in projects] == [
        ["git:code.example.com/team/repo"],
        [f"git:code.example.com:{other_port}/team/repo"],
    ]
