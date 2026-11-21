"""Disposable pytest namespaces must not retain managed runtime processes."""
from __future__ import annotations

import errno
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from managed_effect_runtime_fixture import (
    capture_disposable_effect_runtimes,
    stop_disposable_effect_runtimes,
)

from loopx.control_plane import effect_runtime


def test_capture_continues_after_a_runtime_directory_disappears(tmp_path, monkeypatch):
    disappearing = tmp_path / "retiring"
    removed = disappearing / "loopx-effect-runtime-gone" / "runtime-gone.json"
    retained = tmp_path / "remaining" / "loopx-effect-runtime-live" / "runtime-live.json"
    for locator in (removed, retained):
        locator.parent.mkdir(parents=True)
        locator.write_text("synthetic locator", encoding="utf-8")
    scandir = os.scandir
    removals = []

    def retire_before_scan(path):
        if isinstance(path, (str, bytes, os.PathLike)) and Path(path) == disappearing:
            if not removals:
                shutil.rmtree(disappearing)
                removals.append(disappearing)
        return scandir(path)

    monkeypatch.setattr(os, "scandir", retire_before_scan)
    captured = capture_disposable_effect_runtimes(tmp_path)
    assert removals == [disappearing]
    assert captured == [(retained, "synthetic locator")]
    assert retained.read_text(encoding="utf-8") == "synthetic locator"


def test_capture_preserves_unexpected_directory_read_errors(tmp_path, monkeypatch):
    scandir = os.scandir

    def unreadable(path):
        if isinstance(path, (str, bytes, os.PathLike)) and Path(path) == tmp_path:
            raise OSError(errno.EIO, "synthetic directory read failure")
        return scandir(path)

    with monkeypatch.context() as failing:
        failing.setattr(os, "scandir", unreadable)
        with pytest.raises(OSError, match="synthetic directory read failure"):
            capture_disposable_effect_runtimes(tmp_path)


@pytest.fixture
def nested_pytest_environment(tmp_path):
    repository = Path(__file__).resolve().parents[1]
    # The nested session's import-time leak census must not claim caller writes.
    home = tmp_path / "nested-home"
    home.mkdir()
    return {**os.environ, "HOME": str(home), "USERPROFILE": str(home), "PYTHONPATH": os.pathsep.join([
        str(repository / "tests"), str(repository),
    ])}


def test_pytest_reclaims_finished_cases_and_keeps_failed_phases(tmp_path, nested_pytest_environment):
    probe = tmp_path / "test_artifacts.py"
    evidence_file = tmp_path / "artifacts.json"
    probe.write_text(textwrap.dedent(f'''
        import json
        import os
        from pathlib import Path

        import pytest

        artifacts = {{}}

        @pytest.fixture
        def evidence(request, tmp_path, case_mktemp):
            paths = [tmp_path, case_mktemp("short"), Path(os.environ["HOME"])]
            for path in paths:
                (path / "witness").write_text(request.node.name)
            artifacts[request.node.name] = paths
            yield
            # Finalizers must run before per-case cleanup.
            assert all((path / "witness").exists() for path in paths)

        @pytest.fixture
        def broken_setup(evidence):
            pytest.fail("setup failed")

        @pytest.fixture
        def broken_teardown(evidence):
            yield
            pytest.fail("teardown failed")

        def test_success(evidence):
            pass

        def test_failed_setup(broken_setup):
            pass

        def test_failed_call(evidence):
            pytest.fail("call failed")

        def test_failed_teardown(broken_teardown):
            pass

        def test_observe():
            assert all(not path.exists() for path in artifacts.pop("test_success"))
            assert len(artifacts) == 3
            for name, paths in artifacts.items():
                assert all((path / "witness").read_text() == name for path in paths)
            Path({str(evidence_file)!r}).write_text(json.dumps({{
                name: [str(path) for path in paths] for name, paths in artifacts.items()
            }}))
    '''), encoding="utf-8")
    result = subprocess.run([
        sys.executable, "-m", "pytest", "-q", "-p", "conftest",
        "--basetemp", str(tmp_path / "cases"), str(probe),
    ], cwd=tmp_path, env=nested_pytest_environment, capture_output=True, text=True, timeout=60)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "1 failed, 3 passed, 2 errors" in result.stdout, result.stdout
    artifacts = json.loads(evidence_file.read_text(encoding="utf-8"))
    assert set(artifacts) == {"test_failed_setup", "test_failed_call", "test_failed_teardown"}
    for name, paths in artifacts.items():
        assert all((Path(path) / "witness").read_text() == name for path in paths)


def test_pytest_reclaims_read_only_trees_without_warning_errors(tmp_path, nested_pytest_environment):
    probe = tmp_path / "test_read_only_tree.py"
    evidence_file = tmp_path / "read-only.json"
    probe.write_text(textwrap.dedent(f'''
        import json
        import os
        from pathlib import Path

        import pytest

        @pytest.mark.filterwarnings("error")
        def test_read_only(tmp_path):
            read_only = tmp_path / "read-only"
            read_only.mkdir()
            target = read_only / "git-object"
            target.write_text("synthetic")
            os.chmod(target, 0o444)
            os.chmod(read_only, 0o555)
            Path({str(evidence_file)!r}).write_text(json.dumps({{
                "root": str(tmp_path), "target": str(target)
            }}))
    '''), encoding="utf-8")
    result = subprocess.run([
        sys.executable, "-m", "pytest", "-q", "-W", "error", "-p", "conftest",
        "--basetemp", str(tmp_path / "cases"), str(probe),
    ], cwd=tmp_path, env=nested_pytest_environment, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(evidence_file.read_text(encoding="utf-8"))
    assert not Path(evidence["root"]).exists()
    assert not Path(evidence["target"]).exists()


@pytest.mark.parametrize("workers", [0, 2], ids=["serial", "xdist"])
def test_pytest_stops_before_fixtures_on_low_disk(tmp_path, nested_pytest_environment, workers):
    probe = tmp_path / "test_disk_guard.py"
    probe.write_text(textwrap.dedent("""
        def test_first():
            raise AssertionError("case must not start")

        def test_second():
            raise AssertionError("case must not start")
    """), encoding="utf-8")
    base = tmp_path / "low-space"
    parallel = []
    if workers:
        pytest.importorskip("xdist")
        parallel = ["-n", str(workers)]
    result = subprocess.run([
        sys.executable, "-m", "pytest", "-q", "-p", "conftest",
        "--basetemp", str(base), "--loopx-min-free-tmp-mib=1099511627776", *parallel, str(probe),
    ], cwd=tmp_path, env=nested_pytest_environment, capture_output=True, text=True, timeout=60)
    assert result.returncode == pytest.ExitCode.INTERRUPTED, result.stdout + result.stderr
    assert "LoopX pytest temporary storage is low" in result.stdout
    assert "case must not start" not in result.stdout
    assert not list(base.glob("**/effect-runtime*"))


def _has_exited(pid):
    if not effect_runtime._pid_is_alive(pid):
        return True
    if sys.platform == "linux":
        # A detached child's zombie has exited but can await the container
        # init's reap. kill(pid, 0) alone incorrectly calls it a live runtime.
        try:
            state = (Path("/proc") / str(pid) / "status").read_text(encoding="utf-8")
        except FileNotFoundError:
            return True
        return "\nState:\tZ" in "\n" + state
    return False


@pytest.mark.parametrize("failing", [False, True])
@pytest.mark.parametrize("retention", ["all", "none"])
def test_pytest_retires_source_and_copied_package_runtimes(
    tmp_path, failing, retention, nested_pytest_environment,
):
    repository = Path(__file__).resolve().parents[1]
    records = tmp_path / "runtimes.jsonl"
    probe = tmp_path / "test_disposable_namespace.py"
    child_script = """
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from loopx.control_plane import effect_runtime
effect_runtime.effect_runtime_result("runtime.ping", {})
locator = effect_runtime._runtime_info_path(effect_runtime._runtime_fingerprint())
print(json.dumps({"path": str(locator), "info": json.loads(locator.read_text(encoding="utf-8"))}))
"""
    probe.write_text(textwrap.dedent(f'''
        import json, os, shutil, subprocess, sys
        from pathlib import Path
        import pytest

        @pytest.mark.parametrize("copied", [False, True])
        def test_owned_runtime(tmp_path, copied):
            source = Path({str(repository)!r})
            if copied:
                receiver = tmp_path / "receiver"
                shutil.copytree(source / "loopx", receiver / "loopx",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
                source = receiver
            script = {child_script!r}
            environment = {{**os.environ, "TMPDIR": str(tmp_path), "TEMP": str(tmp_path),
                "TMP": str(tmp_path), "PYTHONPATH": str(source)}}
            child = subprocess.run([sys.executable, "-c", script, str(source)],
                cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=60)
            assert child.returncode == 0, child.stdout + child.stderr
            with Path({str(records)!r}).open("a", encoding="utf-8") as stream:
                stream.write(child.stdout.strip() + "\\n")
            assert not {failing!r}, "fixture failure retained"
    '''), encoding="utf-8")
    observed = []
    try:
        result = subprocess.run([
            sys.executable, "-m", "pytest", "-q", "-p", "conftest",
            "-o", f"tmp_path_retention_policy={retention}", str(probe),
        ], cwd=tmp_path, env=nested_pytest_environment, capture_output=True, text=True, timeout=120)
        assert result.returncode == (1 if failing else 0), result.stdout + result.stderr
        if failing:
            assert "2 failed" in result.stdout and "fixture failure retained" in result.stdout
        observed = [json.loads(line) for line in records.read_text(encoding="utf-8").splitlines()]
        assert len(observed) == 2
        assert all(_has_exited(row["info"]["pid"]) for row in observed), (
            "pytest retained a source or copied-package runtime after its test namespace ended"
        )
    finally:
        # Keep the failing baseline bounded too; these exact locators came from
        # the two child fixtures above, never from a developer's runtime.
        if records.exists() and not observed:
            observed = [json.loads(line) for line in records.read_text(encoding="utf-8").splitlines()]
        for row in observed:
            effect_runtime._stop_effect_runtime(
                Path(row["path"]), row["info"], timeout=5, wait_for_process_exit=True,
            )


@pytest.mark.parametrize("writer_home", ["caller", "nested"])
def test_nested_pytest_attributes_default_route_writes_to_its_own_home(
    tmp_path, nested_pytest_environment, writer_home,
):
    home = Path(os.environ["HOME"] if writer_home == "caller" else nested_pytest_environment["HOME"])
    leaked_route = home / ".loopx"
    assert not leaked_route.exists()
    probe = tmp_path / "test_default_route_writer.py"
    probe.write_text(textwrap.dedent(f'''
        from pathlib import Path

        def test_writer():
            Path({str(leaked_route)!r}).mkdir()
    '''), encoding="utf-8")
    result = subprocess.run([
        sys.executable, "-m", "pytest", "-q", "-p", "conftest", str(probe),
    ], cwd=tmp_path, env=nested_pytest_environment, capture_output=True, text=True, timeout=120)
    owns_write = writer_home == "nested"
    assert result.returncode == (1 if owns_write else 0), result.stdout + result.stderr
    assert ("loopx default runtime route leak" in result.stdout) == owns_write


def test_cleanup_preserves_foreign_namespace_and_rejects_wrong_pid(tmp_path, monkeypatch):
    foreign = tmp_path / "foreign"
    owned = tmp_path / "owned"
    foreign.mkdir()
    owned.mkdir()
    with monkeypatch.context() as isolated:
        isolated.setattr(effect_runtime.tempfile, "tempdir", str(foreign))
        effect_runtime.effect_runtime_result("runtime.ping", {})
        locator = effect_runtime._runtime_info_path(effect_runtime._runtime_fingerprint())
        info = json.loads(locator.read_text(encoding="utf-8"))
        try:
            # A directory link must not turn an unrelated namespace into ours.
            (owned / locator.parent.name).symlink_to(locator.parent, target_is_directory=True)
            stop_disposable_effect_runtimes(owned)
            assert effect_runtime.effect_runtime_result("runtime.ping", {})["pid"] == info["pid"]
            (owned / locator.parent.name).unlink()
            forged = owned / locator.parent.name / locator.name
            forged.parent.mkdir()
            forged.write_text(json.dumps({**info, "pid": os.getpid()}), encoding="utf-8")
            stop_disposable_effect_runtimes(owned)
            assert effect_runtime.effect_runtime_result("runtime.ping", {})["pid"] == info["pid"]
            forged.write_text("not JSON", encoding="utf-8")
            stop_disposable_effect_runtimes(owned)
            forged.write_bytes(b"\xff")
            stop_disposable_effect_runtimes(owned)
            assert effect_runtime.effect_runtime_result("runtime.ping", {})["pid"] == info["pid"]
        finally:
            assert effect_runtime.restart_effect_runtime(wait_for_exit=True)["stopped"]


def test_cleanup_does_not_hide_an_unfinished_shutdown(tmp_path, monkeypatch):
    fingerprint = "a" * 64
    locator = tmp_path / "loopx-effect-runtime-fixture" / f"runtime-{fingerprint[:16]}.json"
    locator.parent.mkdir()
    locator.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(effect_runtime, "_read_info", lambda *_a, **_k: {
        "pid": 123, "fingerprint": fingerprint,
    })
    monkeypatch.setattr(effect_runtime, "_request_with_info", lambda *_a, **_k: {
        "result": {"ready": True, "pid": 123, "fingerprint": fingerprint,
                   "info_path": str(locator.resolve())},
    })
    monkeypatch.setattr(effect_runtime, "_stop_effect_runtime", lambda *_a, **_k: {"stopped": False})
    with pytest.raises(AssertionError, match="Disposable runtime did not exit"):
        stop_disposable_effect_runtimes(tmp_path)


@pytest.mark.parametrize("removed", [False, True])
def test_regular_locator_copy_cannot_authorize_foreign_runtime_shutdown(tmp_path, monkeypatch, removed):
    foreign, owned = tmp_path / "foreign", tmp_path / "owned"
    foreign.mkdir()
    owned.mkdir()
    with monkeypatch.context() as isolated:
        isolated.setattr(effect_runtime.tempfile, "tempdir", str(foreign))
        effect_runtime.effect_runtime_result("runtime.ping", {})
        locator = effect_runtime._runtime_info_path(effect_runtime._runtime_fingerprint())
        info = json.loads(locator.read_text(encoding="utf-8"))
        try:
            copied = owned / locator.parent.name / locator.name
            copied.parent.mkdir()
            # Even a claimed publication path in the untrusted JSON is not
            # proof of where the authenticated server originally published.
            copied.write_text(json.dumps({**info, "info_path": str(copied)}), encoding="utf-8")
            captured = capture_disposable_effect_runtimes(owned)
            assert len(captured) == 1 and not copied.is_symlink()
            if removed:
                copied.unlink()
                copied.parent.rmdir()
                owned.rmdir()
            stop_disposable_effect_runtimes(owned, captured if removed else None)
            # Do not use normal ping: it could restart a killed server and
            # obscure the original runtime's loss.
            response = effect_runtime._request_with_info(info, request_id="foreign-survives",
                method="runtime.ping", params={}, timeout=2)
            assert response["result"]["pid"] == info["pid"]
        finally:
            effect_runtime._stop_effect_runtime(locator, info, timeout=5, wait_for_process_exit=True)
