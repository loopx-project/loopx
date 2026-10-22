"""The premerge boundary check fails on an input the scan could not read.

`_public_boundary_changed_files_run` reads `scan_public_boundary`'s result, so a
directory the scan could not list has to fail this check the way a recorded hit
does. The check is reached with directory inputs, which a `git diff` change set
does not normally carry, so the payload is driven directly here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from loopx.canary.premerge import _public_boundary_changed_files_run


def _boundary(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "ok": True,
        "scanned_files": 3,
        "hits": [],
        "skipped_private_state_files": [],
        "allowed_hits": [],
        "unreadable_directories": [],
    }
    payload.update(overrides)
    return payload


def test_unreadable_directory_fails_the_premerge_boundary_check(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "tracked.txt").write_text("ordinary\n", encoding="utf-8")
    monkeypatch.setattr(
        "loopx.canary.premerge.scan_public_boundary",
        lambda *_, **__: _boundary(unreadable_directories=["notes: Permission denied"]),
    )

    run = _public_boundary_changed_files_run(
        changed_files=["tracked.txt"],
        execute=True,
        timeout_seconds=30.0,
        repo_root=tmp_path,
    )

    check = run["selected_checks"][0]
    assert check["ok"] is False
    assert check["status"] == "failed"
    assert check["stdout_tail"] == ""
    assert check["stderr_tail"] == "notes: Permission denied"
    assert check["boundary"]["unreadable_directory_count"] == 1
    assert run["ok"] is False
    assert run["failure_count"] == 1


def test_clean_boundary_passes_the_premerge_boundary_check(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "tracked.txt").write_text("ordinary\n", encoding="utf-8")
    monkeypatch.setattr(
        "loopx.canary.premerge.scan_public_boundary",
        lambda *_, **__: _boundary(),
    )

    run = _public_boundary_changed_files_run(
        changed_files=["tracked.txt"],
        execute=True,
        timeout_seconds=30.0,
        repo_root=tmp_path,
    )

    check = run["selected_checks"][0]
    assert check["ok"] is True
    assert check["status"] == "passed"
    assert check["stdout_tail"] == "public boundary scan clean: 3 files"
    assert check["boundary"]["unreadable_directory_count"] == 0
    assert run["ok"] is True


def test_actual_premerge_keeps_example_warnings_visible(tmp_path: Path) -> None:
    (tmp_path / "example.md").write_text("Synthetic network: http://192.168.10.10\n")
    run = _public_boundary_changed_files_run(changed_files=["example.md"],
        execute=True, timeout_seconds=30.0, repo_root=tmp_path)
    check = run["selected_checks"][0]
    assert run["ok"] is True and check["ok"] is True
    assert "completed with warnings" in check["stdout_tail"]
    assert "example.md:1: private_ip" in check["stderr_tail"]


def test_actual_premerge_rejects_unread_content(tmp_path: Path) -> None:
    (tmp_path / "unknown.md").write_bytes(b"\xff\xfe")
    run = _public_boundary_changed_files_run(changed_files=["unknown.md"],
        execute=True, timeout_seconds=30.0, repo_root=tmp_path)
    assert run["ok"] is False
    check = run["selected_checks"][0]
    assert check["stdout_tail"] == ""
    assert "invalid UTF-8" in check["stderr_tail"]
