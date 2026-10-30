"""Real Turn -> Host stdin regression for anti-entropy task intent.

This drives the same path the reviewer used: a canonical File-authority Goal,
a Todo authored through the real CLI, and a real `turn run-once --execute`
whose Host is a synthetic `codex` binary that records argv/stdin and exits 9.
It proves the guidance and inspect descriptor reach Host input, not merely the
in-process composer.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_canonical_fixture():
    fixture_path = REPO_ROOT / "tests/control_plane/canonical_authority_fixture.py"
    spec = importlib.util.spec_from_file_location(
        "_anti_entropy_native_host_canonical_fixture", fixture_path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_cli(registry: Path, runtime: Path, *args: str, timeout: int = 240):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--registry",
            str(registry),
            "--runtime-root",
            str(runtime),
            *args,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="the synthetic Host fixture is a POSIX shell script",
)
def test_real_turn_host_stdin_carries_anti_entropy_guidance(tmp_path: Path) -> None:
    """A declared retirement task reaches the real Host input of turn run-once."""
    if shutil.which("node") is None:
        pytest.skip("canonical File authority fixture requires node")

    fixture = _load_canonical_fixture()
    goal_id = "anti-entropy-native-host"
    registry, runtime, _state = fixture.promoted_create_fixture(
        tmp_path, goal_id=goal_id, registered_agents=["codex-fixture"]
    )
    # The fixture Goal predates contract fields the status gate needs.
    registry_payload = json.loads(registry.read_text(encoding="utf-8"))
    registry_payload["goals"][0].update(
        {
            "status": "active",
            "domain": "software-development",
            "adapter": {"kind": "generic_project_goal_v0", "status": "connected"},
        }
    )
    registry.write_text(json.dumps(registry_payload), encoding="utf-8")

    basis = {"retirement_basis": {"target_symbols": ["old_api"]}}
    todo_text = (
        "Replace old_api with new_api and delete the legacy export. "
        + json.dumps(basis)
    )
    added = _run_cli(
        registry,
        runtime,
        "todo",
        "add",
        "--goal-id",
        goal_id,
        "--role",
        "agent",
        "--text",
        todo_text,
        "--note",
        json.dumps(basis),
        "--execute",
        "--format",
        "json",
    )
    assert added.returncode == 0, added.stdout + added.stderr
    assert json.loads(added.stdout)["status"] == "applied"

    capture = tmp_path / "host_capture.txt"
    fake_codex = tmp_path / "codex"
    fake_codex.write_text(
        "#!/bin/sh\n"
        "{\n"
        "  echo '=== ARGV ==='\n"
        "  printf '%s\\n' \"$@\"\n"
        "  echo '=== STDIN ==='\n"
        "  cat\n"
        f'}} > "{capture}"\n'
        "exit 9\n",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)

    turn = _run_cli(
        registry,
        runtime,
        "turn",
        "run-once",
        "--goal-id",
        goal_id,
        "--agent-id",
        "codex-fixture",
        "--host",
        "codex-cli",
        "--project",
        str(tmp_path / "project"),
        "--codex-bin",
        str(fake_codex),
        "--timeout-seconds",
        "30",
        "--execute",
        "--format",
        "json",
    )
    payload = json.loads(turn.stdout)
    # The Host failed on purpose (exit 9) after its input was captured.
    assert payload["effects"]["host_invoked"] is True
    assert payload["result_kind"] == "host_failure"
    assert capture.is_file(), turn.stdout + turn.stderr

    host_input = capture.read_text(encoding="utf-8", errors="replace")
    assert "Anti-entropy code governance is active for this task" in host_input
    assert "loopx anti-entropy inspect" in host_input
    assert '"anti_entropy_context"' in host_input
    assert '"inspect_tool"' in host_input
