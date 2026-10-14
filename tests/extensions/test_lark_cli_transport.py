"""Characterize the provider command boundary with real local subprocesses."""

import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest

from loopx.extensions.lark.cli_transport import (
    command_error,
    run_command,
    default_subprocess_runner,
)


def test_preview_does_not_start_a_process(tmp_path: Path) -> None:
    marker = tmp_path / "started"
    command = [sys.executable, "-c", "from pathlib import Path; Path('started').touch()"]
    result = run_command(command, execute=False, cwd=tmp_path)
    assert result["ok"] is True
    assert result["executed"] is False
    assert result["command"] == shlex.join(command)
    assert not marker.exists()


@pytest.mark.parametrize("execute", [False, True])
@pytest.mark.parametrize("argument", ["--access-token", "--APP-SECRET=fixture"])
def test_inline_credentials_refuse_before_execution(argument: str, execute: bool) -> None:
    def forbidden(*_args):
        pytest.fail("credential refusal must precede provider execution")

    with pytest.raises(ValueError, match="must remain in lark-cli auth"):
        run_command(["lark-cli", argument], execute=execute, runner=forbidden)


def test_real_subprocess_preserves_cwd_and_decodes_utf8(tmp_path: Path) -> None:
    command = [sys.executable, "-c", (
        "import os,sys; from pathlib import Path; "
        "Path('started').touch(); "
        "os.write(1, b'{\"ok\":true,\"message\":\"caf\\xc3\\xa9 \\xff\"}'); "
        "os.write(2, '诊断'.encode('utf-8'))"
    )]
    result = run_command(command, execute=True, cwd=tmp_path, timeout_seconds=5)
    assert result["ok"] is True
    assert result["json"] == {"ok": True, "message": "café �"}
    assert result["stderr"] == "诊断"
    assert (tmp_path / "started").exists()
    raw = default_subprocess_runner([sys.executable, "-c", "print('complete')"])
    assert raw == {"returncode": 0, "stdout": "complete\n", "stderr": "", "timed_out": False}


@pytest.mark.parametrize(("body", "exit_code", "expected_ok"), [
    ('{"ok": false}', 0, False),
    ('{"code": 42, "msg": "provider rejected"}', 0, False),
    ('{"ok": true, "code": 0}', 3, False),
    ('{"code": 0}', 0, True),
    ('[1, 2]', 0, True),
    ('plain provider output', 0, True),
])
def test_process_and_provider_failures_remain_distinct(body, exit_code, expected_ok):
    result = run_command([
        sys.executable, "-c", "import sys; print(sys.argv[1]); sys.exit(int(sys.argv[2]))",
        body, str(exit_code),
    ], execute=True, timeout_seconds=5)
    assert result["ok"] is expected_ok
    assert result["returncode"] == exit_code
    assert result["stdout"].strip() == body
    assert result["json"] == (None if body == "plain provider output" else json.loads(body))


def test_timeout_preserves_started_observation_and_missing_process_is_not_timeout(tmp_path):
    command = [sys.executable, "-c", (
        "from pathlib import Path; import time; Path('started').touch(); "
        "print('provider started', flush=True); time.sleep(5)"
    )]
    timed_out = run_command(command, execute=True, cwd=tmp_path, timeout_seconds=0.3)
    assert (tmp_path / "started").exists()
    assert timed_out["ok"] is False
    assert timed_out["executed"] is True
    assert timed_out["returncode"] is None
    assert timed_out["timed_out"] is True
    assert "provider started" in timed_out["stdout"]
    missing = run_command([str(tmp_path / "missing-executable")], execute=True)
    assert missing["ok"] is False
    assert missing["returncode"] is None
    assert missing.get("timed_out", False) is False
    assert missing["stderr"]


@pytest.mark.parametrize(("result", "expected"), [
    ({"json": {"error": {"message": "provider error"}}, "stderr": "fallback"}, "provider error"),
    ({"json": {"error": "denied"}}, "denied"),
    ({"json": {"code": 42, "msg": "rejected"}, "stderr": "fallback"}, "rejected"),
    ({"json": {"code": 0, "msg": "not an error"}, "stderr": "  failure\n details "}, "failure details"),
    ({"stdout": " fallback\n output "}, "fallback output"),
    ({"stderr": "x" * 1900}, "x" * 1800),
])
def test_provider_error_precedence_and_bounded_fallback(result, expected):
    assert command_error(result) == expected


def test_channel_transport_runs_without_presentation_or_control_plane_sources(tmp_path):
    source = Path(__file__).resolve().parents[2] / "loopx"
    package = tmp_path / "loopx"
    for relative in (
        "__init__.py", "extensions/__init__.py", "extensions/lark/__init__.py",
        "extensions/lark/cli_transport.py", "extensions/lark/goal_channel_transport.py",
        "extensions/lark/identity_shapes.py",
    ):
        destination = package / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, destination)
    assert not (package / "extensions/lark/presentation").exists()
    assert not (package / "control_plane").exists()
    code = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from loopx.extensions.lark import cli_transport, goal_channel_transport
assert Path(cli_transport.__file__).is_relative_to(Path(sys.argv[1]))
result = goal_channel_transport.call(cli_transport.default_subprocess_runner,
    [sys.executable, '-c', 'import json; print(json.dumps(dict(ok=True, message="complete")))'])
assert result['returncode'] == 0
assert goal_channel_transport.json_payload(result) == {'ok': True, 'message': 'complete'}
assert not any(name.startswith(('loopx.control_plane', 'loopx.extensions.lark.presentation'))
    for name in sys.modules)
"""
    completed = subprocess.run(
        [sys.executable, "-I", "-c", code, str(tmp_path)],
        cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", timeout=10,
    )
    assert completed.returncode == 0, completed.stderr
