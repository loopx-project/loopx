"""The premerge boundary check fails on an input the scan could not read.

`_public_boundary_changed_files_run` reads `scan_public_boundary`'s result, so a
directory the scan could not list has to fail this check the way a recorded hit
does. The check is reached with directory inputs, which a `git diff` change set
does not normally carry, so the payload is driven directly here.
"""

from __future__ import annotations

import base64
import subprocess
from pathlib import Path

import pytest

from loopx.canary.premerge import (
    _public_boundary_changed_files_run,
    build_premerge_validation_gate,
    classify_premerge_surfaces,
    render_premerge_validation_gate_markdown,
)


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


def _image(root: Path, name: str = "capture.PNG") -> Path:
    # Public, synthetic one-pixel PNG; no operating screenshot or metadata.
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1s"
        "AAAAASUVORK5CYII="
    ))
    return path


def test_binary_capture_is_visible_but_never_qualified_by_text_scan(tmp_path: Path) -> None:
    _image(tmp_path)
    (tmp_path / "public.md").write_text("ordinary public prose\n")
    paths = ["capture.PNG", "public.md"]
    classification = classify_premerge_surfaces(paths, repo_root=tmp_path)
    run = _public_boundary_changed_files_run(
        changed_files=paths, execute=True, timeout_seconds=30, repo_root=tmp_path,
    )
    # Text coverage and outstanding visual coverage are independently observable.
    assert run["ok"] is True
    assert run["selection_inputs"]["scan_paths"] == ["public.md"]
    assert run["selection_inputs"]["visual_review_paths"] == ["capture.PNG"]
    assert run["selected_checks"][0]["boundary"]["scanned_files"] == 1
    assert [hold["path"] for hold in classification["manual_holds"]] == ["capture.PNG"]
    assert classification["public_boundary_scan_recommended"] is True


def _isolated_gate(tmp_path: Path, changed_files: list[str]) -> dict:
    # An isolated real checkout and empty catalog exercise the shipped gate
    # without unrelated catalog jobs; its Git hygiene checks still execute.
    catalog = tmp_path / "catalog.md"
    catalog.write_text("# Isolated canary catalog\n")
    for args in (
        ["init", "-q"], ["add", "catalog.md", *changed_files],
        ["-c", "user.name=Canary Fixture", "-c", "user.email=canary@example.invalid",
         "commit", "-qm", "fixture"],
    ):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)
    return build_premerge_validation_gate(
        repo_root=tmp_path, changed_files=changed_files, base_ref="HEAD",
        tier="quick", execute=True, catalog_path=catalog,
    )


@pytest.mark.parametrize("body", [None, b"ordinary text masquerading as an image"])
def test_actual_image_only_gate_keeps_existing_visual_review_hold(
    tmp_path: Path, body: bytes | None,
) -> None:
    image = _image(tmp_path)
    if body is not None:
        image.write_bytes(body)
    payload = _isolated_gate(tmp_path, ["capture.PNG"])
    assert payload["ok"] is False
    assert payload["gate"]["status"] == "manual_review_required"
    assert payload["gate"]["merge_gate_passed"] is False
    assert payload["gate"]["self_merge_validation_passed"] is False
    assert payload["gate"]["self_merge_allowed"] is False
    assert payload["gate"]["run_failure_count"] == 0
    assert payload["boundary_run"]["selection_inputs"]["visual_review_paths"] == ["capture.PNG"]
    assert "capture.PNG" in render_premerge_validation_gate_markdown(payload)


@pytest.mark.parametrize("name", ["docs/unknown.bin", "docs/unknown.svg", "docs/unknown.md"])
def test_visual_hold_cannot_hide_unknown_or_text_decode_failure(tmp_path: Path, name: str) -> None:
    _image(tmp_path)
    target = tmp_path / name
    target.parent.mkdir(parents=True)
    target.write_bytes(b"\xff\xfe")
    run = _public_boundary_changed_files_run(
        changed_files=["capture.PNG", name], execute=True, timeout_seconds=30, repo_root=tmp_path,
    )
    assert run["ok"] is False
    assert "invalid UTF-8" in run["selected_checks"][0]["stderr_tail"]


def test_image_named_directory_does_not_hide_text(tmp_path: Path) -> None:
    directory = tmp_path / "capture.png"
    directory.mkdir()
    (directory / "entry.md").write_bytes(b"\xff\xfe")
    run = _public_boundary_changed_files_run(
        changed_files=["capture.png"], execute=True, timeout_seconds=30, repo_root=tmp_path,
    )
    assert run["ok"] is False
    assert not run["selection_inputs"].get("visual_review_paths")


def test_deleted_capture_does_not_leave_a_phantom_visual_hold(tmp_path: Path) -> None:
    classification = classify_premerge_surfaces(["deleted.png"], repo_root=tmp_path)
    assert classification["manual_holds"] == []


def test_image_named_symlink_keeps_explicit_text_scan(tmp_path: Path) -> None:
    target = tmp_path / "entry.md"
    target.write_bytes(b"\xff\xfe")
    try:
        (tmp_path / "capture.png").symlink_to(target.name)
    except OSError:
        pytest.skip("host does not permit creating symbolic links")
    classification = classify_premerge_surfaces(["capture.png"], repo_root=tmp_path)
    run = _public_boundary_changed_files_run(
        changed_files=["capture.png"], execute=True, timeout_seconds=30, repo_root=tmp_path,
    )
    assert classification["manual_holds"] == []
    assert run["ok"] is False
    assert "invalid UTF-8" in run["selected_checks"][0]["stderr_tail"]
    payload = _isolated_gate(tmp_path, ["capture.png"])
    assert payload["ok"] is False
    assert payload["gate"]["status"] == "failed"
    assert payload["gate"]["run_failure_count"] == 1
