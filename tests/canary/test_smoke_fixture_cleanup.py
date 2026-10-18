from __future__ import annotations

from pathlib import Path
import os
import stat
import sys
from typing import Any

import pytest

from loopx.canary import runner
from loopx.extensions.process_runtime import remove_owned_tree


def _read_only_fixture_argv() -> list[str]:
    """argv for a check that leaves a read-only git-shaped object behind.

    The runner points every temp variable at its own per-check root, so the child
    writes the object exactly where the runner later has to remove it. ``0o444``
    sets the Windows read-only attribute, as git does for every loose object.
    """
    return [
        sys.executable,
        "-c",
        "import os, pathlib\n"
        "root = pathlib.Path(os.environ['TMPDIR'])\n"
        "fixture = root / 'loopx-catalog-canary-smoke-fixture' / 'selector-repo'\n"
        "(fixture / '.git' / 'objects' / '39').mkdir(parents=True)\n"
        "object_file = fixture / '.git' / 'objects' / '39' / '6a2d80dac5ee96f7f8e1b120f2358ae0fcda'\n"
        "object_file.write_bytes(b'blob')\n"
        "os.chmod(object_file, 0o444)\n"
        "print(root)\n",
    ]


def _read_only_fixture_check(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    argv = _read_only_fixture_argv()
    normalized: dict[str, Any] = {
        "ok": True,
        "command": "python3 examples/read-only-fixture-smoke.py",
        "argv": argv,
        "display_argv": ["python3", "examples/read-only-fixture-smoke.py"],
        "injected_args": [],
        "script": "examples/read-only-fixture-smoke.py",
    }
    monkeypatch.setattr(
        runner,
        "normalize_canary_command",
        lambda command, repo_root=None: dict(normalized),
    )
    return {"command": normalized["command"], "reason": "read-only fixture check"}


def _fixture_root(result: dict[str, Any]) -> Path:
    root = Path(str(result["stdout_tail"]).strip().splitlines()[-1])
    assert root.name.startswith("loopx-smoke-")
    return root


def test_a_passing_check_survives_a_read_only_fixture_member(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A check whose command exited 0 stays passed, and its fixture is removed.

    A plain ``shutil.rmtree`` of the fixture root failed on native Windows with
    ``PermissionError`` [WinError 5], so the runner recorded ``returncode: 0``
    together with ``status: failed`` and retained the tree.
    """
    check = _read_only_fixture_check(monkeypatch)

    result = runner.run_canary_smoke_check(check, timeout_seconds=60)

    assert result["returncode"] == 0
    assert "fixture cleanup failed" not in str(result.get("reason") or "")
    assert result["status"] == "passed"
    assert result["ok"] is True
    assert not _fixture_root(result).exists()


def test_the_runner_still_reports_cleanup_that_cannot_be_confirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The teardown seam stays fail-closed: an unconfirmed removal retains the tree.

    This pins the contract the fix must not loosen — if the owned tree cannot be
    removed, the check is reported failed rather than passed and its fixture is
    kept for diagnosis.
    """
    check = _read_only_fixture_check(monkeypatch)

    def unconfirmed(_root: Path) -> None:
        raise RuntimeError("managed runtime still exiting")

    monkeypatch.setattr(runner, "remove_owned_tree", unconfirmed)

    result = runner.run_canary_smoke_check(check, timeout_seconds=60)

    assert result["returncode"] == 0
    assert result["ok"] is False
    assert str(result.get("reason") or "").startswith("fixture cleanup failed; retained")
    retained = _fixture_root(result)
    assert retained.exists()
    remove_owned_tree(retained)


def _hard_link_fixture_check(
    monkeypatch: pytest.MonkeyPatch,
    external_object: Path,
) -> dict[str, Any]:
    """A check that hard-links an owned fixture entry to an external object.

    ``git clone --local`` does the same for loose objects, so removing the fixture
    must not clear the read-only attribute of the shared file.
    """
    monkeypatch.setenv("LOOPX_TEST_EXTERNAL_OBJECT", str(external_object))
    argv = [
        sys.executable,
        "-c",
        "import os, pathlib\n"
        "root = pathlib.Path(os.environ['TMPDIR'])\n"
        "os.link(os.environ['LOOPX_TEST_EXTERNAL_OBJECT'], root / 'object')\n"
        "print(root)\n",
    ]
    normalized: dict[str, Any] = {
        "ok": True,
        "command": "python3 examples/hard-link-fixture-smoke.py",
        "argv": argv,
        "display_argv": ["python3", "examples/hard-link-fixture-smoke.py"],
        "injected_args": [],
        "script": "examples/hard-link-fixture-smoke.py",
    }
    monkeypatch.setattr(
        runner,
        "normalize_canary_command",
        lambda command, repo_root=None: dict(normalized),
    )
    return {"command": normalized["command"], "reason": "hard link fixture check"}


def test_a_shared_hard_link_is_not_rewritten_outside_the_fixture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A cleanup that cannot avoid touching a shared object fails closed.

    Windows cannot remove a read-only hard link without clearing the attribute of
    the shared inode, so the only safe outcome is a reported cleanup failure with
    the tree retained; the external object keeps its bytes and read-only bit.
    """
    source = tmp_path / "source-object"
    source.write_bytes(b"blob")
    os.chmod(source, stat.S_IREAD)
    check = _hard_link_fixture_check(monkeypatch, source)

    result = runner.run_canary_smoke_check(check, timeout_seconds=60)

    try:
        assert result["returncode"] == 0
        if os.name == "nt":
            assert result["ok"] is False
            assert str(result.get("reason") or "").startswith(
                "fixture cleanup failed; retained"
            )
        else:
            assert result["status"] == "passed"
            assert not _fixture_root(result).exists()
        assert source.read_bytes() == b"blob"
        assert os.stat(source).st_mode & stat.S_IWRITE == 0
    finally:
        if os.name == "nt":
            retained = _fixture_root(result)
            if retained.exists():
                # Release the shared attribute only for test cleanup, after the
                # assertions above proved teardown did not do it.
                os.chmod(source, stat.S_IWRITE)
                remove_owned_tree(retained)
