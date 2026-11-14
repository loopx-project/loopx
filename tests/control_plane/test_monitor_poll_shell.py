"""Execute projected monitor commands in their host shell, including missing input."""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys

import pytest

from loopx.control_plane.quota import effect_program
from loopx.control_plane.scheduler.execution_context import (
    SchedulerRuntimeProfile,
    scheduler_execution_context_for_runtime_profile,
)
from loopx.control_plane.work_items.interaction_contract import build_interaction_contract
from tests.control_plane.test_auxiliary_monitor_poll_availability import _payload


@pytest.mark.parametrize("windows", [False, True], ids=["posix", "native-pwsh"])
@pytest.mark.parametrize(("kind", "hash_value"), [
    (kind, value) for kind in ("primary", "auxiliary", "material-change")
    for value in ("observation-digest", None, "")
] + [("binding", "observation-digest")])
def test_shell(tmp_path, monkeypatch, windows, kind, hash_value):
    if windows and os.name != "nt":
        pytest.skip("requires native Windows PowerShell")
    shell = shutil.which("pwsh" if windows else "bash")
    if shell is None:
        pytest.skip("host shell is unavailable")
    monkeypatch.setattr(effect_program, "_is_windows_host", lambda: windows)
    payload = _payload(portfolio_requires_explicit_binding=False, selected_todo_id="todo_selected")
    if kind in {"primary", "binding"}:
        payload["selected_todo"].update(task_class="continuous_monitor", target_key="input-ready")
        payload["work_lane_contract"].update(
            lane="continuous_monitor", obligation="attempt_due_monitor",
            must_attempt_work=True, selected_todo_id="todo_selected",
        )
        payload["work_lane_contract"].pop("auxiliary_monitor_poll")
    contract = build_interaction_contract(
        payload,
        scheduler_execution_context=scheduler_execution_context_for_runtime_profile(
            SchedulerRuntimeProfile.CODEX_APP_HEARTBEAT
        ),
        turn_instance_id=None if kind == "binding" else "host-turn",
    )
    channel = contract["cli_channel"]
    if kind in {"primary", "binding"}:
        command = channel["next_cli_actions"][0] if kind == "binding" else next(action for action in channel["next_cli_actions"] if " quota monitor-poll " in action)
        instruction = channel["next_cli_actions"][0]
    else:
        projection = channel["auxiliary_monitor_poll"]
        command = projection["material_change_command" if kind == "material-change" else "command"]
        instruction = projection.get("next_step", "")
    platform = "PowerShell" if windows else "POSIX"
    setup = re.search(rf"{platform}: `([^`]+)`", instruction)
    if kind == "binding":
        assignment = ""
    else:
        assert setup is not None, instruction
        assignment = setup[1].replace("<actual-observation-digest>", hash_value or "")
    if not windows and kind != "binding":
        assert '--result-hash "${LOOPX_MONITOR_RESULT_HASH:?}"' in command
        assert "--turn-instance-id host-turn" in command
    stub = tmp_path / "cli.py"
    marker = tmp_path / "called"
    stub.write_text(
        "import json, pathlib, sys\n"
        "pathlib.Path(__file__).with_name('called').touch()\n"
        "print(json.dumps(sys.argv[1:]))\n", encoding="utf-8",
    )
    python = sys.executable.replace("\\", "/")
    stub_path = stub.as_posix()
    if windows:
        prefix = (
            "function loopx { & '" + python.replace("'", "''")
            + "' '" + stub_path.replace("'", "''") + "' @args }\n"
            "$LOOPX_TURN = 'host-turn'\n"
        )
        shell_args = [shell, "-NoProfile", "-NonInteractive", "-Command"]
    else:
        prefix = (
            f"loopx() {{ {shlex.quote(python)} {shlex.quote(stub_path)} \"$@\"; }}\n"
            "export LOOPX_TURN='host-turn'\n"
        )
        shell_args = [shell, "--noprofile", "--norc", "-c"]
    env = os.environ.copy()
    env.pop("LOOPX_MONITOR_RESULT_HASH", None)
    result = subprocess.run(
        [*shell_args, prefix + (assignment + "\n" if hash_value is not None else "") + command],
        env=env, text=True, encoding="utf-8", capture_output=True, timeout=30,
    )
    if hash_value:
        assert result.returncode == 0, result.stderr
        argv = json.loads(result.stdout)
        assert argv[argv.index("--turn-instance-id") + 1] == "host-turn"
        if kind == "binding":
            assert "should-run" in argv
        else:
            assert argv[argv.index("--result-hash") + 1] == hash_value
        assert ("--material-change" in argv) == (kind == "material-change")
        if kind != "binding":
            assert "--use-current-task-lease" in argv
            assert "--execute" in argv
        assert marker.exists()
    else:
        assert result.returncode != 0, (result.stdout, result.stderr)
        assert "LOOPX_MONITOR_RESULT_HASH" in result.stderr
        assert not marker.exists(), "the CLI must not run without a non-empty hash"
