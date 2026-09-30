"""The smoke runner suppresses telemetry in actual child processes."""
import json
import runpy
import subprocess
from pathlib import Path

from loopx.canary import runner


def test_smoke_subprocess_overrides_parent_telemetry_enable(tmp_path, monkeypatch):
    examples = tmp_path / "examples"
    examples.mkdir()
    (examples / "environment.py").write_text(
        "import json,os\nprint(json.dumps({k:os.environ.get(k) for k in "
        "['LOOPX_USAGE_PING','CI','SYNTHETIC_VALUE']}))\n", encoding="utf-8",
    )
    monkeypatch.setattr(runner, "REPO_ROOT", tmp_path)
    monkeypatch.setenv("LOOPX_USAGE_PING", "1")
    monkeypatch.setenv("SYNTHETIC_VALUE", "preserved")
    monkeypatch.delenv("CI", raising=False)
    result = runner._run_check({"command": "python examples/environment.py"}, timeout_seconds=10)
    assert result["ok"], result
    assert json.loads(result["stdout_tail"]) == {
        "LOOPX_USAGE_PING": "0", "CI": None, "SYNTHETIC_VALUE": "preserved",
    }


def test_update_smoke_minimal_environment_keeps_opt_out(monkeypatch):
    smoke = runpy.run_path(str(Path(__file__).parents[2] / 'examples/loopx-update-smoke.py'))
    original_run = subprocess.run
    environments = []

    def actual_run(*args, **kwargs):
        if 'env' in kwargs:
            environments.append(kwargs['env'])
        return original_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, 'run', actual_run)
    monkeypatch.setenv('LOOPX_USAGE_PING', '1')
    smoke['test_cli_rollback_previous_with_temp_home']()
    assert environments and all(env.get('LOOPX_USAGE_PING') == '0' for env in environments)
