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


def test_smokes_isolate_routes_and_home_in_serial_and_parallel(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    examples = tmp_path / "examples"
    examples.mkdir()
    (examples / "environment.py").write_text(
        "import json,os,pathlib\n"
        "home=pathlib.Path.home(); assert not (home/'previous-smoke').exists()\n"
        "(home/'previous-smoke').write_text('synthetic')\n"
        "print(json.dumps({**{k:os.environ.get(k) for k in "
        "['HOME','CODEX_HOME','LOOPX_REGISTRY','LOOPX_RUNTIME_ROOT']}, "
        "'temp_equal':os.environ['TMPDIR']==os.environ['TEMP']==os.environ['TMP'], "
        "'path_digest':__import__('hashlib').sha256(os.environ['PATH'].encode()).hexdigest()}))\n",
        encoding="utf-8",
    )
    owner_home = tmp_path / "owner-home"
    owner_home.mkdir()
    marker = owner_home / "previous-smoke"
    marker.write_text("owner data")
    monkeypatch.setattr(runner, "REPO_ROOT", tmp_path)
    monkeypatch.setenv("HOME", str(owner_home))
    monkeypatch.setenv("CODEX_HOME", str(owner_home / ".custom-host"))
    monkeypatch.setenv("LOOPX_REGISTRY", str(owner_home / "registry.json"))
    monkeypatch.setenv("LOOPX_RUNTIME_ROOT", str(owner_home / "runtime"))
    command = {"command": "python examples/environment.py"}
    def execute(_):
        result = runner._run_check(command, timeout_seconds=10)
        assert result["ok"], result
        return json.loads(result["stdout_tail"])
    values = [execute(0), execute(1)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        values.extend(pool.map(execute, range(2)))
    assert len({value["HOME"] for value in values}) == 4
    for value in values:
        assert value["HOME"] != str(owner_home)
        assert value["CODEX_HOME"] == value["HOME"] + "/.codex"
        assert value["temp_equal"] is True
        assert value["LOOPX_REGISTRY"] is value["LOOPX_RUNTIME_ROOT"] is None
        assert value["path_digest"] == __import__("hashlib").sha256(__import__("os").environ["PATH"].encode()).hexdigest()
        assert not Path(value["HOME"]).exists()
    assert marker.read_text() == "owner data"


def test_grouped_canary_preserves_explicit_path_priority(monkeypatch):
    import os
    smoke = runpy.run_path(str(Path(__file__).parents[2] / "examples/canary/canary-promotion-readiness-smoke.py"))
    monkeypatch.setenv("PATH", "/qualified/toolchain/bin")
    environment = smoke["build_env"]()
    assert environment["PATH"].split(os.pathsep)[0] == "/qualified/toolchain/bin"


def test_runtime_started_by_smoke_is_stopped_before_fixture_cleanup(tmp_path, monkeypatch):
    import os
    import sys
    import tempfile
    examples = tmp_path / "examples"
    examples.mkdir()
    (examples / "runtime.py").write_text(
        "import json,os\nfrom loopx.control_plane.effect_runtime import collect_effect_runtime_readiness\n"
        "r=collect_effect_runtime_readiness(deep=True); assert r['ready'], r\n"
        "print(json.dumps({'temp':os.environ['TMPDIR']}))\n", encoding="utf-8",
    )
    monkeypatch.setattr(runner, "REPO_ROOT", tmp_path)
    with tempfile.TemporaryDirectory(prefix="loopx-owner-runtime-") as owner:
        env = {**os.environ, 'TMPDIR': owner, 'TEMP': owner, 'TMP': owner}
        subprocess.run([sys.executable, '-c',
            "from loopx.control_plane.effect_runtime import collect_effect_runtime_readiness; "
            "r=collect_effect_runtime_readiness(deep=True); assert r['ready'], r"],
            env=env,check=True,capture_output=True,text=True)
        try:
            result = runner._run_check({"command": "python examples/runtime.py"}, timeout_seconds=30)
            assert result['ok'], result
            isolated_temp = Path(json.loads(result['stdout_tail'])['temp'])
            assert str(isolated_temp) != owner and not isolated_temp.exists()
            # The independently scoped owner's process still serves requests.
            probe = subprocess.run([sys.executable, '-c',
                "import json; from loopx.control_plane.effect_runtime import collect_effect_runtime_readiness; "
                "print(json.dumps(collect_effect_runtime_readiness()))"],
                env=env,check=True,capture_output=True,text=True)
            assert json.loads(probe.stdout)['runtime_lifecycle']['state'] == 'running'
        finally:
            subprocess.run([sys.executable, '-c',
                "from loopx.control_plane.effect_runtime import restart_effect_runtime; "
                "r=restart_effect_runtime(); assert r['status'] != 'shutdown_pending', r"],
                env=env,check=True,capture_output=True,text=True)
