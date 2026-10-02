"""The smoke runner owns its isolated environment and child lifecycle."""
import json
import runpy
import subprocess
import os
import signal
import sys
import threading
import time
from pathlib import Path

import pytest

from loopx.canary import runner


@pytest.mark.skipif(os.name != "posix", reason="POSIX owned process-group regression")
@pytest.mark.parametrize("outcome", ["timeout", "exited_leader", "cancelled"])
def test_smoke_failure_reaps_children_before_cleaning_fixture(tmp_path, monkeypatch, outcome):
    ready, release, effect = (tmp_path / name for name in ("ready", "release", "effect"))
    examples = tmp_path / "examples"
    examples.mkdir()
    child = (
        "import os,time; from pathlib import Path\n"
        f"Path({str(ready)!r}).write_text(str(os.getpid()))\n"
        f"while not Path({str(release)!r}).exists(): time.sleep(.01)\n"
        f"Path({str(effect)!r}).write_text('late effect')\n"
    )
    script = examples / "owned.py"
    script.write_text(
        "import subprocess,sys,time\n"
        f"subprocess.Popen([sys.executable,'-c',{child!r}]"
        + (",stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL" if outcome != "exited_leader" else "")
        + ")\n"
        + ("time.sleep(60)\n" if outcome != "exited_leader" else ""),
        encoding="utf-8",
    )
    real_popen = subprocess.Popen
    timers = []

    def spawn(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        if str(script) not in args[0]:
            return process
        # Synchronize real child startup before exercising the communicate
        # deadline; this tests cleanup rather than machine scheduling speed.
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert ready.exists(), "fixture child did not reach its startup barrier"
        if outcome == "exited_leader":
            timer = threading.Timer(.7, lambda: release.write_text("release"))
            timer.start()
            timers.append(timer)
        if outcome == "cancelled":
            def cancelled(*_, **__):
                raise KeyboardInterrupt
            process.communicate = cancelled
        return process

    monkeypatch.setattr(runner, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(subprocess, "Popen", spawn)
    with real_popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True) as unrelated:
        try:
            if outcome == "cancelled":
                with pytest.raises(KeyboardInterrupt):
                    runner._run_check({"command": "python examples/owned.py"}, timeout_seconds=.1)
            else:
                result = runner._run_check({"command": "python examples/owned.py"}, timeout_seconds=.1)
                assert result['status'] == 'timed_out' and not result['ok']
            assert unrelated.poll() is None
            child_pid = int(ready.read_text())
            status = subprocess.run(["ps", "-p", str(child_pid), "-o", "stat="], capture_output=True, text=True).stdout.strip()
            assert not status or status.startswith("Z"), "owned child survived cleanup"
            assert not effect.exists(), "child performed a late effect before cleanup returned"
        finally:
            for timer in timers:
                timer.cancel()
                timer.join()
            if ready.exists():
                try:
                    os.kill(int(ready.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass
            unrelated.kill()
            unrelated.wait()


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
