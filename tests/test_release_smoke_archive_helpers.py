from __future__ import annotations

import runpy
import tarfile
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_SMOKES = (
    Path("examples/codex-cli-packaged-install-smoke.py"),
    Path("examples/release/codex-cli-no-clone-release-verification-smoke.py"),
)


@pytest.mark.parametrize("script_path", ARCHIVE_SMOKES)
def test_add_tree_excludes_nested_python_cache(
    script_path: Path,
    tmp_path: Path,
) -> None:
    add_tree = runpy.run_path(str(REPOSITORY_ROOT / script_path))["add_tree"]
    source_root = tmp_path / "source"
    nested = source_root / "package" / "nested"
    cache = nested / "__pycache__"
    cache.mkdir(parents=True)
    (nested / "kept.txt").write_text("kept\n", encoding="utf-8")
    (cache / "volatile.pyc").write_bytes(b"volatile")

    archive_path = tmp_path / "release.tar"
    with tarfile.open(archive_path, "w") as archive:
        add_tree(archive, source_root, "package")

    with tarfile.open(archive_path) as archive:
        archived_names = archive.getnames()

    assert "loopx-main/package/nested/kept.txt" in archived_names
    assert all("__pycache__" not in Path(name).parts for name in archived_names)
