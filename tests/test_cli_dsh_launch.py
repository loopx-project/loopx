from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from loopx.cli_commands.dsh_launch import (
    handle_dsh_launch_command,
    resolve_dsh_launch,
)
from loopx.control_plane.operator_provider import write_operator_provider


def _args(tmp_path: Path, **overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "dsh_bin": sys.executable,
        "dsh_home": str(tmp_path / "dsh-home"),
        "dsh_agents_home": str(tmp_path / "dsh-agents"),
        "runtime_root": str(tmp_path / "runtime"),
        "dsh_args": ["--", "web", "--port", "0"],
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_dsh_launch_resolves_credential_into_child_environment_only(
    tmp_path: Path,
) -> None:
    secret = "synthetic-provider-key"
    write_operator_provider(
        runtime_root=tmp_path / "runtime",
        environ={},
        api_key=secret,
        base_url="https://provider.invalid/v1",
    )

    dsh_bin, dsh_args, environment = resolve_dsh_launch(
        _args(tmp_path),
        environ={},
    )

    assert dsh_bin == sys.executable
    assert dsh_args == ["web", "--port", "0"]
    assert environment["DEEPSEEK_API_KEY"] == secret
    assert environment["DEEPSEEK_BASE_URL"] == "https://provider.invalid/v1"
    assert environment["DSH_HOME"] == str(tmp_path / "dsh-home")
    assert environment["DSH_AGENTS_HOME"] == str(tmp_path / "dsh-agents")
    assert secret not in " ".join([dsh_bin, *dsh_args])


def test_dsh_launch_exec_preserves_arguments_and_defaults_to_web(
    tmp_path: Path,
) -> None:
    secret = "synthetic-provider-key"
    write_operator_provider(
        runtime_root=tmp_path / "runtime",
        environ={},
        api_key=secret,
    )
    calls: list[tuple[str, list[str], dict[str, str]]] = []

    def record(
        executable: str,
        argv: list[str],
        environment: dict[str, str],
    ) -> None:
        calls.append((executable, argv, environment))

    code = handle_dsh_launch_command(
        _args(tmp_path, dsh_args=[]),
        environ={},
        exec_environment=record,
    )

    assert code == 0
    assert calls[0][0] == sys.executable
    assert calls[0][1][1:] == ["web"]
    assert calls[0][2]["DEEPSEEK_API_KEY"] == secret
    assert secret not in " ".join(calls[0][1])


def test_dsh_launch_fails_closed_without_executable(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = handle_dsh_launch_command(
        _args(tmp_path, dsh_bin=str(tmp_path / "missing-dsh")),
        environ={},
        exec_environment=lambda *_: None,
    )

    assert code == 2
    assert "DSH executable not found" in capsys.readouterr().err


def _write_exec_capture_launcher(tmp_path: Path) -> Path:
    launcher = tmp_path / "capture-launcher.py"
    launcher.write_text(
        "import hashlib, json, os, sys\n"
        "from pathlib import Path\n"
        "api_key = os.environ.get('DEEPSEEK_API_KEY')\n"
        "Path(os.environ['LOOPX_TEST_CAPTURE']).write_text(json.dumps({\n"
        "    'argv': sys.argv,\n"
        "    'api_key_sha256': (\n"
        "        hashlib.sha256(api_key.encode()).hexdigest() if api_key else None\n"
        "    ),\n"
        "    'base_url': os.environ.get('DEEPSEEK_BASE_URL'),\n"
        "    'dsh_home': os.environ.get('DSH_HOME'),\n"
        "    'dsh_agents_home': os.environ.get('DSH_AGENTS_HOME'),\n"
        "}))\n"
        "print('synthetic-dsh-child')\n",
        encoding="utf-8",
    )
    return launcher


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _cli_environment(tmp_path: Path, capture_path: Path) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    excluded = {
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_BASE_URL",
        "DSH_BIN",
        "DSH_HOME",
        "DSH_AGENTS_HOME",
        "LOOPX_RUNTIME_ROOT",
        "LOOPX_TEST_CAPTURE",
    }
    environment = {
        key: value for key, value in os.environ.items() if key not in excluded
    }
    environment.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "CODEX_HOME": str(home / ".codex"),
            "LOOPX_USAGE_PING": "0",
            "LOOPX_TEST_CAPTURE": str(capture_path),
        }
    )
    return environment


@pytest.mark.parametrize(
    ("placement", "expected_credential"),
    [
        ("default", "default"),
        ("global", "explicit"),
        ("subcommand", "explicit"),
    ],
)
def test_dsh_launch_normal_cli_preserves_the_selected_runtime_root(
    tmp_path: Path,
    placement: str,
    expected_credential: str,
) -> None:
    home = tmp_path / "home"
    explicit_runtime = tmp_path / "explicit-runtime"
    default_runtime = home / ".loopx"
    credentials = {
        "default": ("default-synthetic-key", "https://default.invalid/v1"),
        "explicit": ("explicit-synthetic-key", "https://explicit.invalid/v1"),
    }
    for owner, runtime_root in (
        ("default", default_runtime),
        ("explicit", explicit_runtime),
    ):
        api_key, base_url = credentials[owner]
        write_operator_provider(
            runtime_root=runtime_root,
            environ={},
            api_key=api_key,
            base_url=base_url,
        )

    capture_path = tmp_path / "capture.json"
    launcher = _write_exec_capture_launcher(tmp_path)
    command = [sys.executable, "-m", "loopx.cli"]
    if placement == "global":
        command += ["--runtime-root", str(explicit_runtime)]
    command += [
        "dsh-launch",
        "--dsh-bin",
        sys.executable,
        "--dsh-home",
        str(tmp_path / "dsh-home"),
        "--dsh-agents-home",
        str(tmp_path / "dsh-agents"),
    ]
    if placement == "subcommand":
        command += ["--runtime-root", str(explicit_runtime)]
    command += [
        "--",
        str(launcher),
        "web",
        "--port",
        "0",
    ]

    completed = subprocess.run(
        command,
        cwd=Path(__file__).parents[1],
        env=_cli_environment(tmp_path, capture_path),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "synthetic-dsh-child"
    assert completed.stderr == ""
    captured = json.loads(capture_path.read_text(encoding="utf-8"))
    expected_key, expected_endpoint = credentials[expected_credential]
    assert captured["api_key_sha256"] == _sha256(expected_key)
    assert captured["base_url"] == expected_endpoint
    assert captured["dsh_home"] == str(tmp_path / "dsh-home")
    assert captured["dsh_agents_home"] == str(tmp_path / "dsh-agents")
    assert captured["argv"][1:] == ["web", "--port", "0"]
    assert all(
        "synthetic-key" not in text
        for text in (completed.stdout, completed.stderr, json.dumps(captured["argv"]))
    )


@pytest.mark.parametrize("placement", ["global", "subcommand"])
def test_dsh_launch_missing_explicit_credential_never_falls_back(
    tmp_path: Path,
    placement: str,
) -> None:
    default_runtime = tmp_path / "home" / ".loopx"
    explicit_runtime = tmp_path / "explicit-runtime"
    explicit_runtime.mkdir()
    write_operator_provider(
        runtime_root=default_runtime,
        environ={},
        api_key="default-synthetic-key",
        base_url="https://default.invalid/v1",
    )

    capture_path = tmp_path / "capture.json"
    launcher = _write_exec_capture_launcher(tmp_path)
    command = [sys.executable, "-m", "loopx.cli"]
    if placement == "global":
        command += ["--runtime-root", str(explicit_runtime)]
    command += ["dsh-launch", "--dsh-bin", sys.executable]
    if placement == "subcommand":
        command += ["--runtime-root", str(explicit_runtime)]
    command += ["--", str(launcher), "web"]

    completed = subprocess.run(
        command,
        cwd=Path(__file__).parents[1],
        env=_cli_environment(tmp_path, capture_path),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    captured = json.loads(capture_path.read_text(encoding="utf-8"))
    assert captured["api_key_sha256"] is None
    assert captured["base_url"] is None
    assert "default-synthetic-key" not in (
        completed.stdout + completed.stderr + json.dumps(captured["argv"])
    )
