"""Disposable pytest namespaces must not retain managed runtime processes."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

from managed_effect_runtime_fixture import stop_disposable_effect_runtimes
from loopx.control_plane import effect_runtime


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
def test_pytest_retires_source_and_copied_package_runtimes(tmp_path, failing, retention):
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
    environment = {**os.environ, "PYTHONPATH": os.pathsep.join([
        str(repository / "tests"), str(repository),
    ])}
    observed = []
    try:
        result = subprocess.run([
            sys.executable, "-m", "pytest", "-q", "-p", "conftest",
            "-o", f"tmp_path_retention_policy={retention}", str(probe),
        ], cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=120)
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
        "result": {"ready": True, "pid": 123, "fingerprint": fingerprint},
    })
    monkeypatch.setattr(effect_runtime, "_stop_effect_runtime", lambda *_a, **_k: {"stopped": False})
    with pytest.raises(AssertionError, match="Disposable runtime did not exit"):
        stop_disposable_effect_runtimes(tmp_path)
