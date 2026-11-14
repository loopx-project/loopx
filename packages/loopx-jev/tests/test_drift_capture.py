"""File identity and no-follow boundaries of scoped workspace capture."""

import json
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
        opened_paths = {}

        def descriptor(path, directory=False):
            fd = os.open(path, os.O_RDONLY)
            opened_paths[fd] = path.resolve()
            return fd

        monkeypatch.setattr(
            drift_capture,
            "_windows_descriptor",
            descriptor,
        )
        monkeypatch.setattr(
            drift_capture, "_windows_final_path", lambda fd: opened_paths[fd]
        )
    return platform


def test_windows_regular_file_without_nofollow(repo, windows):
    (repo / "file.txt").write_bytes(b"before\r\nafter\n")
    captured = drift_capture.stable_capture(repo, ["file.txt", "missing.txt"])
    assert captured["files"]["file.txt"]["text"] == "before\r\nafter\n"
    assert captured["files"]["missing.txt"] is None


def test_windows_stable_capture_allows_ctime_skew(repo, windows):
    (repo / "file.txt").write_bytes(b"ordinary file\n")
    lstat, fstat = windows.lstat, windows.fstat
    # A stable file's lstat/fstat ctime can differ; identity must not depend on it.
    windows.lstat = lambda path: stat_fields(lstat(path), st_ctime_ns=1)
    windows.fstat = lambda fd: stat_fields(fstat(fd), st_ctime_ns=2)
    assert (
        drift_capture.stable_capture(repo, ["file.txt"])["files"]["file.txt"]["text"]
        == "ordinary file\n"
    )


@pytest.mark.parametrize("name", [".GIT/config", ".gIt/config"])
def test_windows_rejects_case_insensitive_git_metadata(repo, windows, name):
    with pytest.raises(ValueError, match="invalid_scope_path"):
        drift_capture.capture(repo, [name])


@pytest.fixture
def unread_windows_file(monkeypatch):
    opened = drift_capture._windows_open

    def unread(path):
        stream = opened(path)
        monkeypatch.setattr(
            stream,
            "read",
            lambda *args: pytest.fail("read excluded or unverified bytes"),
        )
        return stream

    monkeypatch.setattr(drift_capture, "_windows_open", unread)


@pytest.mark.skipif(os.name != "nt", reason="native Windows path representations")
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("leaf", ["config", "missing.txt", "absent/missing.txt"])
@pytest.mark.parametrize(
    "representation", ["long", "case", "extended", "device", "short", "short_case"]
)
def test_windows_rejects_native_git_path_representations(
    repo, windows, unread_windows_file, representation, leaf, nested
):
    metadata = repo / "ordinary/.git" if nested else repo / ".git"
    if nested:
        metadata.mkdir(parents=True)
        (metadata / "config").write_bytes(b"excluded metadata")
    if representation.startswith("short"):
        set_short_name(metadata, "GIT~1")
    prefix = "ordinary/" if nested else ""
    parent = {
        "long": f"{prefix}.git",
        "case": f"{prefix}.GiT",
        "extended": f"//?/{metadata.as_posix()}",
        "device": f"//./{metadata.as_posix()}",
        "short": f"{prefix}GIT~1",
        "short_case": f"{prefix}git~1",
    }[representation]
    assert os.path.samefile(repo / parent, metadata)
    name = f"{parent}/{leaf.upper() if representation == 'short_case' else leaf}"
    assert (repo / name).exists() == (leaf == "config")
    with pytest.raises(ValueError, match="invalid_scope_path"):
        drift_capture.capture(repo, [name])


@pytest.mark.skipif(os.name != "nt", reason="native Windows legacy spellings")
@pytest.mark.parametrize("parent", [".git.", ".git ", ".git::$INDEX_ALLOCATION"])
@pytest.mark.parametrize("leaf", ["config", "missing.txt", "absent/missing.txt"])
def test_windows_rejects_git_legacy_spellings(
    repo, windows, unread_windows_file, parent, leaf
):
    with pytest.raises((ValueError, OSError)):
        drift_capture.capture(repo, [f"{parent}/{leaf}"])


@pytest.mark.parametrize(
    "name",
    [
        "ordinary/missing./file.txt",
        "ordinary/missing /file.txt",
        "ordinary/file.txt:stream",
        "C:ordinary/missing.txt",
    ],
)
def test_windows_rejects_ambiguous_missing_components(repo, windows, name):
    (repo / "ordinary").mkdir()
    with pytest.raises(ValueError, match="invalid_scope_path"):
        drift_capture.capture(repo, [name])


def set_short_name(path, alias):
    result = subprocess.run(
        ["fsutil", "file", "setshortname", str(path), alias], capture_output=True
    )
    if result.returncode:
        pytest.skip(
            "8.3 alias creation unavailable: "
            + (result.stdout + result.stderr).decode(errors="replace").strip()
        )


@pytest.mark.skipif(os.name != "nt", reason="native Windows directory aliases")
@pytest.mark.parametrize("leaf", ["missing.txt", "absent/missing.txt"])
def test_windows_missing_git_alias_cannot_initialize(repo, windows, leaf, capsys):
    from loopx_jev.cli import main

    set_short_name(repo / ".git", "GIT~1")
    assert os.path.samefile(repo / "GIT~1", repo / ".git")
    config, basis, observer = (
        repo / "config.json",
        repo / "basis.json",
        repo / "observer",
    )
    config.write_text(
        json.dumps(
            {
                "schema_version": "loopx_jev_drift_config_v0",
                "mode": "shadow",
                "scenarios": ["progress_review"],
                "model": "fixture-v1",
                "allow_egress": False,
            }
        )
    )
    basis.write_text(
        json.dumps(
            {
                "goal_id": "alias-test",
                "objective": "Observe ordinary future files",
                "acceptance": ["New ordinary files produce evidence"],
                "evidence": [],
            }
        )
    )
    assert (
        main(
            [
                "drift",
                "init",
                "--state-dir",
                str(observer),
                "--workspace",
                str(repo),
                "--basis",
                str(basis),
                "--config",
                str(config),
                "--path",
                f"GIT~1/{leaf}",
            ]
        )
        == 2
    )
    assert "unavailable" in capsys.readouterr().err
    assert not observer.exists()


@pytest.mark.skipif(os.name != "nt", reason="native Windows directory aliases")
@pytest.mark.parametrize("leaf", ["missing.txt", "absent/missing.txt"])
def test_windows_missing_ordinary_alias_captures_when_created(repo, windows, leaf):
    parent = repo / "ordinary directory"
    parent.mkdir()
    set_short_name(parent, "PLAIN~1")
    name = f"plain~1/{leaf}"
    assert os.path.samefile(repo / "plain~1", parent)
    before = drift_capture.stable_capture(repo, [name])
    assert before["files"][name] is None
    target = parent / leaf
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"ordinary new evidence\n")
    after = drift_capture.stable_capture(repo, [name])
    assert after["files"][name]["text"] == "ordinary new evidence\n"
    assert "ordinary new evidence" in drift_capture.delta(before, after)


def test_windows_regular_hard_link_stays_capturable(repo, windows):
    original, link = repo / "file.txt", repo / "link.txt"
    original.write_bytes(b"ordinary linked file\n")
    link.hardlink_to(original)
    assert os.path.samefile(original, link)
    files = drift_capture.stable_capture(repo, ["file.txt", "link.txt"])["files"]
    assert (
        files["file.txt"]["text"]
        == files["link.txt"]["text"]
        == "ordinary linked file\n"
    )


@pytest.mark.skipif(os.name != "nt", reason="native Windows final-path API")
@pytest.mark.parametrize("result", [0, 32768])
@pytest.mark.parametrize(
    "missing,query", [(False, 1), (False, 2), (False, 3), (True, 1), (True, 2)]
)
def test_windows_fails_closed_without_final_path(
    repo, windows, unread_windows_file, monkeypatch, result, missing, query
):
    import ctypes
    from ctypes import wintypes

    (repo / "nested").mkdir()
    if not missing:
        (repo / "nested/file.txt").write_bytes(b"ordinary file")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    final_path = kernel.GetFinalPathNameByHandleW
    final_path.argtypes = (
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    )
    final_path.restype = wintypes.DWORD
    calls = 0

    def unavailable(*args):
        nonlocal calls
        calls += 1
        if calls == query:
            ctypes.set_last_error(5)
            return result
        return final_path(*args)

    monkeypatch.setattr(kernel, "GetFinalPathNameByHandleW", unavailable)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: kernel)
    with pytest.raises(ValueError, match="capture_path_unavailable"):
        drift_capture.capture(repo, ["nested/file.txt"])
    assert calls == query


def test_windows_missing_workspace_is_not_an_absent_scoped_file(repo, windows):
    saved = repo.with_name(repo.name + "-moved")
    original = windows.lstat

    def vanished(path):
        if path == repo:
            repo.rename(saved)
        return original(path)

    windows.lstat = vanished
    try:
        with pytest.raises(ValueError, match="capture_path_unavailable"):
            drift_capture._windows_capture(repo, repo / "missing.txt")
    finally:
        saved.rename(repo)


@pytest.mark.parametrize("name", ["ordinary", "ordinary/missing.txt"])
def test_windows_missing_path_requires_directory_ancestors_and_file_leaf(
    repo, windows, name
):
    parent = repo / "ordinary"
    parent.mkdir() if name == "ordinary" else parent.write_bytes(b"regular file")
    with pytest.raises(ValueError, match="scope_requires_regular_files"):
        drift_capture.capture(repo, [name])


def test_windows_missing_path_rejects_outside_canonical_ancestor(
    repo, windows, monkeypatch
):
    parent = repo / "nested"
    parent.mkdir()
    identity = os.stat(parent).st_ino
    final_path = drift_capture._windows_final_path
    monkeypatch.setattr(
        drift_capture,
        "_windows_final_path",
        lambda fd: repo.parent if os.fstat(fd).st_ino == identity else final_path(fd),
    )
    with pytest.raises(ValueError, match="scope_escapes_workspace"):
        drift_capture.capture(repo, ["nested/absent/file.txt"])


@pytest.mark.skipif(os.name != "nt", reason="native Windows directory sharing")
def test_windows_missing_path_fails_closed_on_directory_sharing_conflict(repo, windows):
    import ctypes
    from ctypes import wintypes

    parent = repo / "nested"
    parent.mkdir()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    # A real no-share handle makes the ancestor inaccessible to the collector.
    handle = create("\\\\?\\" + str(parent), 0x80000000, 0, None, 3, 0x02000000, None)
    assert handle != wintypes.HANDLE(-1).value
    try:
        with pytest.raises(PermissionError):
            drift_capture.capture(repo, ["nested/absent/file.txt"])
    finally:
        kernel.CloseHandle(handle)
    assert drift_capture.capture(repo, ["nested/absent/file.txt"])["files"] == {
        "nested/absent/file.txt": None,
    }


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
@pytest.mark.parametrize("leaf", ["file.txt", "missing.txt", "absent/missing.txt"])
def test_windows_rejects_real_junction(repo, windows, leaf):
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
            drift_capture.capture(repo, [f"junction/{leaf}"])
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
@pytest.mark.parametrize("name", ["file.txt", "absent/file.txt"])
def test_windows_fails_closed_without_identity(repo, windows, field, boundary, name):
    (repo / "file.txt").write_text("original")
    original = getattr(windows, boundary)
    setattr(windows, boundary, lambda value: stat_fields(original(value), **{field: 0}))
    with pytest.raises(ValueError, match="capture_identity_unavailable"):
        drift_capture.capture(repo, [name])


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
@pytest.mark.parametrize("leaf", ["file.txt", "absent/file.txt"])
def test_windows_rejects_workspace_swap_between_lstat_and_open(
    repo, windows, monkeypatch, leaf
):
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
            drift_capture.capture(repo, [f"nested/{leaf}"])
    finally:
        if repo.is_junction():
            repo.rmdir()
        if saved.exists():
            saved.rename(repo)


@pytest.mark.parametrize("alias", ["record.txt", "RECORD.txt"])
def test_index_only_change_is_seen_through_case_alias(repo, alias):
    def git(*args):
        return subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
        ).stdout

    if (
        git(
            "config", "--type=bool", "--default=false", "--get", "core.ignorecase"
        ).strip()
        != "true"
    ):
        pytest.skip("requires a case-insensitive workspace")
    if os.name != "nt" and not hasattr(os, "O_NOFOLLOW"):
        pytest.skip("unsupported capture platform")
    (repo / "record.txt").write_bytes(b"original\n")
    git("add", "record.txt")
    before = drift_capture.stable_capture(repo, [alias])
    assert before["files"][alias]["text"] == "original\n"
    # Stage different content, then restore the working file: only the index changed.
    (repo / "record.txt").write_bytes(b"staged\n")
    git("add", "record.txt")
    (repo / "record.txt").write_bytes(b"original\n")
    after = drift_capture.stable_capture(repo, [alias])
    assert after["content_digest"] == before["content_digest"]
    assert after["index_digest"] != before["index_digest"]
    # An untracked or absent neighbour keeps its ordinary empty index evidence.
    assert (
        drift_capture.stable_capture(repo, ["missing.txt"])["files"]["missing.txt"]
        is None
    )


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
