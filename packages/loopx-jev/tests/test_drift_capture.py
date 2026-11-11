"""File identity and no-follow boundaries of scoped workspace capture."""

import os
import stat
import subprocess
from types import SimpleNamespace

import pytest
from loopx_jev import drift_capture


@pytest.fixture
def repo(tmp_path):
    for args in (
        ("init", "-q"),
        (
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "baseline",
        ),
    ):
        subprocess.run(
            ["git", "-C", str(tmp_path), *args], check=True, capture_output=True
        )
    return tmp_path


def stat_fields(info, **changes):
    return SimpleNamespace(
        **{
            **{
                key: getattr(info, key)
                for key in (
                    "st_mode",
                    "st_dev",
                    "st_ino",
                    "st_size",
                    "st_mtime_ns",
                )
            },
            "st_file_attributes": getattr(info, "st_file_attributes", 0),
            **changes,
        }
    )


@pytest.fixture
def windows(monkeypatch):
    platform = SimpleNamespace(**vars(os))
    platform.name = "nt"
    if hasattr(platform, "O_NOFOLLOW"):
        del platform.O_NOFOLLOW
    platform.lstat = lambda path: stat_fields(os.lstat(path))
    platform.fstat = lambda fd: stat_fields(os.fstat(fd))
    monkeypatch.setattr(drift_capture, "os", platform)
    if os.name != "nt":
        # Native Windows exercises CreateFileW; other hosts simulate its IO boundary.
        monkeypatch.setattr(
            drift_capture,
            "_windows_descriptor",
            lambda path, directory=False: os.open(path, os.O_RDONLY),
        )
    return platform


def test_windows_regular_file_without_nofollow(repo, windows):
    (repo / "file.txt").write_bytes(b"before\r\nafter\n")
    captured = drift_capture.stable_capture(repo, ["file.txt", "missing.txt"])
    assert captured["files"]["file.txt"]["text"] == "before\r\nafter\n"
    assert captured["files"]["missing.txt"] is None


@pytest.mark.parametrize("name", [".GIT/config", ".gIt/config"])
def test_windows_rejects_case_insensitive_git_metadata(repo, windows, name):
    with pytest.raises(ValueError, match="invalid_scope_path"):
        drift_capture.capture(repo, [name])


@pytest.mark.parametrize("directory", [False, True])
def test_windows_rejects_symlink(repo, windows, directory):
    target = repo / "original"
    target.mkdir() if directory else target.write_text("original")
    link = repo / "link"
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError:
        # Symlink creation can require a Windows privilege; still test lstat policy.
        link.mkdir() if directory else link.write_text("original")
        original = windows.lstat
        windows.lstat = lambda path: (
            stat_fields(original(path), st_mode=stat.S_IFLNK)
            if path == link
            else original(path)
        )
    name = "link/missing.txt" if directory else "link"
    with pytest.raises(ValueError, match="scope_escapes_workspace"):
        drift_capture.capture(repo, [name])


@pytest.mark.parametrize("component", ["file.txt", "nested"])
def test_windows_rejects_reparse_point(repo, windows, component):
    (repo / "nested").mkdir()
    target = repo / "nested/file.txt"
    target.write_text("original")
    original = windows.lstat
    flagged = target if component == "file.txt" else repo / component
    windows.lstat = lambda path: (
        stat_fields(
            original(path), st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT
        )
        if path == flagged
        else original(path)
    )
    with pytest.raises(ValueError, match="scope_escapes_workspace"):
        drift_capture.capture(repo, ["nested/file.txt"])


@pytest.mark.skipif(os.name != "nt", reason="native Windows junction")
def test_windows_rejects_real_junction(repo, windows):
    target = repo / "directory"
    target.mkdir()
    (target / "file.txt").write_text("original")
    link = repo / "junction"
    # Junctions do not require the symbolic-link privilege.
    subprocess.run(
        ["cmd", "/d", "/c", "mklink", "/J", str(link), str(target)],
        check=True,
        capture_output=True,
    )
    try:
        assert link.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
        with pytest.raises(ValueError, match="scope_escapes_workspace"):
            drift_capture.capture(repo, ["junction/file.txt"])
    finally:
        link.rmdir()


@pytest.mark.parametrize("field", ["st_dev", "st_ino", "st_size", "st_mtime_ns"])
def test_windows_rejects_opened_identity_mismatch(repo, windows, monkeypatch, field):
    (repo / "file.txt").write_text("original")
    original = windows.fstat
    windows.fstat = lambda fd: stat_fields(
        original(fd), **{field: getattr(original(fd), field) + 1}
    )
    opened = drift_capture._windows_open

    def unread(path):
        stream = opened(path)
        monkeypatch.setattr(
            stream, "read", lambda *args: pytest.fail("read unverified bytes")
        )
        return stream

    monkeypatch.setattr(drift_capture, "_windows_open", unread)
    with pytest.raises(ValueError, match="workspace_changed_during_capture"):
        drift_capture.capture(repo, ["file.txt"])


@pytest.mark.parametrize("field", ["st_dev", "st_ino"])
@pytest.mark.parametrize("boundary", ["lstat", "fstat"])
def test_windows_fails_closed_without_identity(repo, windows, field, boundary):
    (repo / "file.txt").write_text("original")
    original = getattr(windows, boundary)
    setattr(windows, boundary, lambda value: stat_fields(original(value), **{field: 0}))
    with pytest.raises(ValueError, match="capture_identity_unavailable"):
        drift_capture.capture(repo, ["file.txt"])


def test_windows_rejects_mutation_during_read(repo, windows, monkeypatch):
    target = repo / "file.txt"
    target.write_text("original")
    opened = drift_capture._windows_open

    def replacing(path):
        stream = opened(path)
        original = stream.read

        def read(*args):
            raw = original(*args)
            target.write_text("changed during read")
            return raw

        monkeypatch.setattr(stream, "read", read)
        return stream

    monkeypatch.setattr(drift_capture, "_windows_open", replacing)
    with pytest.raises(ValueError, match="workspace_changed_during_capture"):
        drift_capture.capture(repo, ["file.txt"])


@pytest.mark.skipif(os.name != "nt", reason="native Windows no-follow handle")
def test_windows_does_not_follow_raced_in_link_to_same_file(repo, windows, monkeypatch):
    target = repo / "file.txt"
    target.write_text("original")
    saved = repo / "saved.txt"
    opened = drift_capture._windows_open

    def replacing(path):
        target.rename(saved)
        try:
            target.symlink_to(saved)
        except OSError:
            saved.rename(target)
            pytest.skip("symbolic-link creation requires Windows privilege")
        stream = opened(path)
        monkeypatch.setattr(
            stream, "read", lambda *args: pytest.fail("followed raced-in link")
        )
        return stream

    monkeypatch.setattr(drift_capture, "_windows_open", replacing)
    with pytest.raises((ValueError, OSError)):
        drift_capture.capture(repo, ["file.txt"])


@pytest.mark.skipif(os.name != "nt", reason="native Windows directory sharing")
def test_windows_pins_parent_until_file_read_finishes(repo, windows, monkeypatch):
    parent = repo / "nested"
    parent.mkdir()
    (parent / "file.txt").write_bytes(b"original")
    opened = drift_capture._windows_open

    def check_parent(path):
        with pytest.raises(PermissionError):
            parent.rename(repo / "moved")
        return opened(path)

    monkeypatch.setattr(drift_capture, "_windows_open", check_parent)
    assert (
        drift_capture.capture(repo, ["nested/file.txt"])["files"]["nested/file.txt"][
            "text"
        ]
        == "original"
    )
    parent.rename(repo / "moved")


@pytest.mark.skipif(os.name != "nt", reason="native Windows junction race")
def test_windows_rejects_workspace_swap_between_lstat_and_open(repo, windows, monkeypatch):
    parent = repo / "nested"
    parent.mkdir()
    (parent / "file.txt").write_text("inside")
    outside = repo.parent / (repo.name + "-outside")
    outside.mkdir()
    (outside / "nested").mkdir()
    (outside / "nested/file.txt").write_text("outside")
    saved = repo.parent / (repo.name + "-moved")
    original = windows.lstat

    def swapped(path):
        observed = original(path)
        if path == repo:
            repo.rename(saved)
            subprocess.run(
                ["cmd", "/d", "/c", "mklink", "/J", str(repo), str(outside)],
                check=True,
                capture_output=True,
            )
        return observed

    windows.lstat = swapped
    monkeypatch.setattr(
        drift_capture, "_windows_open", lambda path: pytest.fail("opened outside file")
    )
    try:
        with pytest.raises(
            ValueError, match="scope_escapes_workspace|workspace_changed_during_capture"
        ):
            drift_capture.capture(repo, ["nested/file.txt"])
    finally:
        if repo.is_junction():
            repo.rmdir()
        if saved.exists():
            saved.rename(repo)


def test_posix_keeps_nofollow_open(repo, monkeypatch):
    (repo / "file.txt").write_bytes(b"original\n")
    platform = SimpleNamespace(**vars(os))
    platform.name = "posix"
    platform.O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0x40000000)
    calls = []

    def opened(path, flags):
        calls.append(flags)
        actual = (
            flags if os.name != "nt" else (flags & ~platform.O_NOFOLLOW) | os.O_BINARY
        )
        return os.open(path, actual)

    platform.open = opened
    monkeypatch.setattr(drift_capture, "os", platform)
    monkeypatch.setattr(
        drift_capture,
        "_windows_open",
        lambda path: pytest.fail("Windows open on POSIX"),
    )
    assert (
        drift_capture.stable_capture(repo, ["file.txt"])["files"]["file.txt"]["text"]
        == "original\n"
    )
    assert len(calls) == 2 and all(flags & platform.O_NOFOLLOW for flags in calls)
