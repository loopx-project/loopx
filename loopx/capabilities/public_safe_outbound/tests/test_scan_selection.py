"""The root scan must reach credential-shaped files and skip vendored trees."""

from __future__ import annotations

from pathlib import Path

from loopx.capabilities.public_safe_outbound.scan_cli import (
    BINARY_SUFFIXES,
    TOOL_MANAGED_DIRECTORY_NAMES,
    VCS_METADATA_NAMES,
    _iter_text_files,
)
from loopx.capabilities.public_safe_outbound.scanner import compact_json, scan_paths

# A synthetic credential, assembled at runtime so this source carries no
# literal secret. The assignment label is deliberately neutral: the scanner
# finds the value by its own shape, whatever variable holds it.
SECRET = "sk" + "-abcdefghijklmnopqrstuvwxyz012345"
ASSIGNMENT = "SERVICE_KEY=" + SECRET


def _write(root: Path, relative: str, body: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _scanned_names(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in _iter_text_files(root)}


def test_credential_shaped_and_extensionless_files_are_scanned(tmp_path: Path) -> None:
    """These were the files an allow-list skipped, and they carry the secret."""
    for name in (
        ".env",
        ".env.local",
        "Dockerfile",
        "Makefile",
        "id_rsa",
        ".npmrc",
        ".netrc",
        "credentials",
        "deploy/secrets.pem",
        "notes.txt",
        "src/main.py",
    ):
        _write(tmp_path, name, "placeholder")
    assert _scanned_names(tmp_path) == {
        ".env",
        ".env.local",
        "Dockerfile",
        "Makefile",
        "id_rsa",
        ".npmrc",
        ".netrc",
        "credentials",
        "deploy/secrets.pem",
        "notes.txt",
        "src/main.py",
    }


def test_vendored_and_vcs_trees_are_not_walked(tmp_path: Path) -> None:
    _write(tmp_path, "src/main.py", "ok")
    for relative in (
        ".git/config",
        "node_modules/pkg/index.js",
        ".venv/lib/site.py",
        "__pycache__/mod.pyc.py",
        "dist/bundle.js",
        ".ruff_cache/x.json",
    ):
        _write(tmp_path, relative, "hidden")
    assert _scanned_names(tmp_path) == {"src/main.py"}
    # the deny-lists are not silently emptied
    assert TOOL_MANAGED_DIRECTORY_NAMES and VCS_METADATA_NAMES


def test_tool_managed_names_prune_directories_not_files(tmp_path: Path) -> None:
    """`build` the directory is output; `build` the file is authored text."""
    _write(tmp_path, "build", "a file that happens to be called build")
    _write(tmp_path, "pkg/dist", "a file that happens to be called dist")
    _write(tmp_path, "pkg/build/out.js", "generated")
    assert _scanned_names(tmp_path) == {"build", "pkg/dist"}


def test_a_root_below_a_vcs_named_directory_still_walks_its_subdirectories(tmp_path: Path) -> None:
    """Only the path below the root decides; where the root lives does not."""
    root = tmp_path / ".git" / "hooks-copy"
    _write(root, "top.sh", "echo")
    _write(root, "nested/inner.sh", "echo")
    assert _scanned_names(root) == {"top.sh", "nested/inner.sh"}


def test_binary_suffixes_are_skipped_but_svg_is_scanned(tmp_path: Path) -> None:
    _write(tmp_path, "logo.png", "not really an image")
    _write(tmp_path, "archive.zip", "not really an archive")
    _write(tmp_path, "font.woff2", "not really a font")
    _write(tmp_path, "diagram.svg", "<svg/>")
    assert _scanned_names(tmp_path) == {"diagram.svg"}
    assert ".png" in BINARY_SUFFIXES


def test_a_secret_in_a_dotfile_now_fails_the_scan(tmp_path: Path) -> None:
    """End-to-end: the whole point of widening the selection."""
    _write(tmp_path, ".env", ASSIGNMENT)
    result = scan_paths(_iter_text_files(tmp_path), root=tmp_path)
    assert result.ok is False
    assert [hit.location for hit in result.hits] == [".env"]


def test_vendored_secret_is_neither_read_nor_reported(tmp_path: Path) -> None:
    """Skipping a vendored tree must not register as unscanned content."""
    _write(tmp_path, "node_modules/pkg/index.js", ASSIGNMENT)
    result = scan_paths(_iter_text_files(tmp_path), root=tmp_path)
    assert result.ok is True
    assert result.unscanned == []


def test_worktree_git_pointer_file_is_skipped(tmp_path: Path) -> None:
    """A linked worktree's `.git` is a file holding an absolute gitdir path.

    Scanning it would report this tool's own checkout as a private-path hit,
    which is why VCS names are matched against a path's final component too.
    """
    (tmp_path / ".git").write_text(
        "gitdir: /Users/example/loopx/.git/worktrees/branch\n", encoding="utf-8"
    )
    (tmp_path / ".gitignore").write_text("build/\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("ok", encoding="utf-8")

    selected = _scanned_names(tmp_path)
    assert selected == {".gitignore", "src/main.py"}
    assert str(tmp_path) not in compact_json(scan_paths(_iter_text_files(tmp_path), root=tmp_path))


def test_vcs_pointer_inside_a_nested_directory_is_skipped(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / ".git").write_text("gitdir: /abs/path", encoding="utf-8")
    (tmp_path / "sub" / "keep.py").write_text("ok", encoding="utf-8")
    assert _scanned_names(tmp_path) == {"sub/keep.py"}


def test_a_file_scan_root_scans_that_file(tmp_path: Path) -> None:
    """A file root must not silently select nothing and report a clean scan."""
    target = tmp_path / "one.py"
    target.write_text(ASSIGNMENT, encoding="utf-8")

    selected = list(_iter_text_files(target))
    assert selected == [target]

    result = scan_paths(selected, root=target)
    assert result.ok is False
    assert [hit.location for hit in result.hits] == ["one.py"]


def test_a_clean_file_scan_root_is_still_clean(tmp_path: Path) -> None:
    target = tmp_path / "clean.py"
    target.write_text("nothing sensitive here", encoding="utf-8")
    result = scan_paths(_iter_text_files(target), root=target)
    assert result.ok is True
    assert result.hits == []

