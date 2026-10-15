"""A scan that cannot list a directory must not report a clean boundary.

`os.walk` swallows a listing failure, so `loopx check --scan-path <tree>` used to
print `public boundary scan clean: N files` and exit 0 for a tree whose only
private file sat in a directory the process could not enter. The sibling
`public_safe_outbound` scanner already fails closed on the same condition
(`directory_unreadable`, #6476); the boundary owner now records the directory
and keeps the scan from passing. A readable sibling is still scanned, and a
directory that only fails to yield a file keeps its existing warning.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from loopx.contract import check_contract, iter_scan_files, scan_public_boundary

POSIX_READ_DENIAL = pytest.mark.skipif(
    os.name == "nt" or os.geteuid() == 0,
    reason="mode 000 read denial needs a non-root POSIX host",
)


def _private_line() -> str:
    return "internal host " + ".".join(str(octet) for octet in (10, 0, 0, 7)) + "\n"


def _registry(tmp_path: Path) -> Path:
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(tmp_path / "runtime"),
                "goals": [],
            }
        ),
        encoding="utf-8",
    )
    return registry_path


@POSIX_READ_DENIAL
def test_scan_records_directory_it_cannot_list(tmp_path: Path) -> None:
    (tmp_path / "public.md").write_text("public text\n", encoding="utf-8")
    locked = tmp_path / "notes"
    locked.mkdir()
    (locked / "private.md").write_text(_private_line(), encoding="utf-8")
    locked.chmod(0o000)
    try:
        scanned = iter_scan_files(tmp_path, unreadable_directories=(unreadable := []))
        boundary = scan_public_boundary([tmp_path])
    finally:
        locked.chmod(0o755)

    assert scanned == sorted([tmp_path / "public.md"])
    # `iter_scan_files` keeps the absolute directory identity and the reason;
    # `scan_public_boundary` renders the "label: reason" form asserted below.
    assert len(unreadable) == 1
    assert unreadable[0] == (tmp_path / "notes", "Permission denied")
    assert boundary["unreadable_directories"] == ["notes: Permission denied"]
    assert boundary["unreadable_files"] == []
    assert boundary["hits"] == []
    assert boundary["ok"] is True
    assert boundary["scanned_files"] == 1


@POSIX_READ_DENIAL
def test_scan_keeps_readable_sibling_of_unreadable_directory(tmp_path: Path) -> None:
    locked = tmp_path / "notes"
    locked.mkdir()
    (locked / "private.md").write_text(_private_line(), encoding="utf-8")
    leaky = tmp_path / "leaky.md"
    leaky.write_text(_private_line(), encoding="utf-8")
    locked.chmod(0o000)
    try:
        boundary = scan_public_boundary([tmp_path])
    finally:
        locked.chmod(0o755)

    assert boundary["unreadable_directories"] == ["notes: Permission denied"]
    assert [hit.rsplit(": ", 1)[-1] for hit in boundary["hits"]] == ["private_ip"]


@POSIX_READ_DENIAL
def test_scan_records_unreadable_directory_named_as_dependency_root(tmp_path: Path) -> None:
    dependency = tmp_path / "node_modules"
    dependency.mkdir()
    (dependency / "private.md").write_text(_private_line(), encoding="utf-8")
    dependency.chmod(0o000)
    try:
        boundary = scan_public_boundary([dependency])
    finally:
        dependency.chmod(0o755)

    # Dependency pruning skips the walk, so the root itself is probed instead.
    assert boundary["unreadable_directories"] == [".: Permission denied"]
    assert iter_scan_files(dependency) == []


@POSIX_READ_DENIAL
def test_check_contract_fails_closed_on_unreadable_directory(tmp_path: Path) -> None:
    registry_path = _registry(tmp_path)
    (tmp_path / "public.md").write_text("public text\n", encoding="utf-8")
    locked = tmp_path / "notes"
    locked.mkdir()
    (locked / "private.md").write_text(_private_line(), encoding="utf-8")
    locked.chmod(0o000)
    try:
        contract = check_contract(
            registry_path=registry_path,
            runtime_root_override=str(tmp_path / "runtime"),
            scan_roots=[tmp_path],
            limit=20,
            allow_missing_registry=True,
        )
    finally:
        locked.chmod(0o755)

    assert contract["ok"] is False
    assert [
        diagnostic["code"] for diagnostic in contract["error_diagnostics"]
    ] == ["public_boundary_scan_directory_unreadable"]
    assert "notes: Permission denied" in contract["error_diagnostics"][0]["message"]
    assert contract["public_boundary_scan"]["unreadable_directories"] == [
        "notes: Permission denied"
    ]
    assert contract["public_boundary_scan"]["ok"] is True


def test_readable_tree_records_no_unreadable_directory(tmp_path: Path) -> None:
    (tmp_path / "public.md").write_text("public text\n", encoding="utf-8")

    boundary = scan_public_boundary([tmp_path])

    assert boundary["unreadable_directories"] == []
    assert boundary["ok"] is True


def test_iter_scan_files_reports_the_denied_directory_and_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The collector holds the absolute directory identity, not a rendered label.

    A directory the walk cannot list is reported through the walk's own `onerror`
    callback here, so the internal shape the POSIX `chmod 000` arms assert stays
    pinned on hosts that cannot set the mode bits.
    """
    from loopx import contract

    (tmp_path / "public.md").write_text("public text\n", encoding="utf-8")
    locked = tmp_path / "notes"
    locked.mkdir()
    (locked / "private.md").write_text(_private_line(), encoding="utf-8")
    real_scandir = contract.os.scandir

    def scandir_with_denied_directory(path, *args, **kwargs):
        if str(path) == str(locked):
            raise PermissionError(13, "Permission denied", str(locked))
        return real_scandir(path, *args, **kwargs)

    monkeypatch.setattr(contract.os, "scandir", scandir_with_denied_directory)
    unreadable: list[tuple[Path, str]] = []

    scanned = iter_scan_files(tmp_path, unreadable_directories=unreadable)

    assert (locked / "private.md").is_file()
    assert scanned == sorted([tmp_path / "public.md"])
    # The identity is the directory itself; only the rendering adds the label.
    assert [directory for directory, _ in unreadable] == [locked]
    assert [reason for _, reason in unreadable] == ["Permission denied"]


def test_a_denied_scan_root_is_reported_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The root probe and the walk both observe the root; the caller sees one entry."""
    import loopx.contract as contract

    denied = tmp_path / "notes"
    denied.mkdir()
    (denied / "notes.md").write_text("ordinary text\n", encoding="utf-8")
    real_scandir = contract.os.scandir
    real_walk = contract.os.walk

    def failing_scandir(path, *args, **kwargs):
        if str(path) == str(denied):
            raise PermissionError(13, "Permission denied", str(path))
        return real_scandir(path, *args, **kwargs)

    def walk_with_listing_failure(root: str, **kwargs):
        onerror = kwargs.get("onerror")
        if callable(onerror):
            onerror(PermissionError(13, "Permission denied", str(root)))
        return real_walk(root, **kwargs)

    monkeypatch.setattr(contract.os, "scandir", failing_scandir)
    monkeypatch.setattr(contract.os, "walk", walk_with_listing_failure)

    boundary = scan_public_boundary([denied, denied])

    assert boundary["unreadable_directories"] == [".: Permission denied"]


def test_two_roots_report_their_own_unlistable_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two roots can each hold an unlistable `notes/`; both are reported."""
    import loopx.contract as contract

    one = tmp_path / "one"
    two = tmp_path / "two"
    for root in (one, two):
        (root / "notes").mkdir(parents=True)
        (root / "public.md").write_text("public text\n", encoding="utf-8")
    real_walk = contract.os.walk

    def walk_with_listing_failure(root: str, **kwargs):
        onerror = kwargs.get("onerror")
        denied = Path(root) / "notes"
        if callable(onerror) and denied.is_dir():
            onerror(PermissionError(13, "Permission denied", str(denied)))
        return real_walk(root, **kwargs)

    monkeypatch.setattr(contract.os, "walk", walk_with_listing_failure)

    boundary = scan_public_boundary([one, two])

    assert boundary["unreadable_directories"] == [
        "notes: Permission denied",
        "notes (2): Permission denied",
    ]


def test_walk_listing_failure_fails_the_scan_on_every_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The walk error callback is the signal; a host without mode bits uses it too."""
    import loopx.contract as contract

    (tmp_path / "public.md").write_text("public text\n", encoding="utf-8")
    locked = tmp_path / "notes"
    locked.mkdir()
    (locked / "notes.md").write_text("ordinary text\n", encoding="utf-8")
    real_walk = contract.os.walk

    def walk_with_listing_failure(root: str, *, onerror: object = None):
        if str(root) == str(tmp_path) and callable(onerror):
            onerror(PermissionError(13, "Permission denied", str(locked)))
        return real_walk(root)

    monkeypatch.setattr(contract.os, "walk", walk_with_listing_failure)

    boundary = scan_public_boundary([tmp_path])

    assert boundary["unreadable_directories"] == ["notes: Permission denied"]
    assert boundary["unreadable_files"] == []
    assert boundary["ok"] is True


def test_check_contract_fails_closed_on_a_walk_listing_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import loopx.contract as contract

    (tmp_path / "public.md").write_text("public text\n", encoding="utf-8")
    locked = tmp_path / "notes"
    locked.mkdir()
    (locked / "notes.md").write_text("ordinary text\n", encoding="utf-8")
    real_walk = contract.os.walk

    def walk_with_listing_failure(root: str, *, onerror: object = None):
        if str(root) == str(tmp_path) and callable(onerror):
            onerror(PermissionError(13, "Permission denied", str(locked)))
        return real_walk(root)

    monkeypatch.setattr(contract.os, "walk", walk_with_listing_failure)

    contract_payload = check_contract(
        registry_path=_registry(tmp_path),
        runtime_root_override=str(tmp_path / "runtime"),
        scan_roots=[tmp_path],
        limit=20,
        allow_missing_registry=True,
    )

    assert contract_payload["ok"] is False
    assert [
        diagnostic["code"] for diagnostic in contract_payload["error_diagnostics"]
    ] == ["public_boundary_scan_directory_unreadable"]
    assert contract_payload["public_boundary_scan"]["unreadable_directories"] == [
        "notes: Permission denied"
    ]
