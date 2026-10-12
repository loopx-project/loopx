from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from loopx.contract import iter_scan_files, scan_public_boundary


def test_iter_scan_files_skips_dangling_symlink(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "ok.md").write_text("hello\n", encoding="utf-8")
    (package / "dangling.json").symlink_to("../missing-target.json")

    scanned = iter_scan_files(tmp_path)

    assert [path.name for path in scanned] == ["ok.md"]


def test_scan_public_boundary_survives_dangling_symlink(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "ok.md").write_text("hello\n", encoding="utf-8")
    (package / "dangling.json").symlink_to("../missing-target.json")

    payload = scan_public_boundary([tmp_path])

    assert payload["ok"] is True
    assert payload["scanned_files"] == 1
    assert payload["unreadable_files"] == []


def test_tracked_product_runtime_source_is_not_private_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = tmp_path / ".local" / "worktrees" / "repo"
    source = repo_root / "loopx" / "control_plane" / "runtime" / "example.py"
    source.parent.mkdir(parents=True)
    source.write_text("PUBLIC_CONTRACT = True\n", encoding="utf-8")
    subprocess.run(
        ["git", "init", "-q"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "add", str(source.relative_to(repo_root))],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )

    def fail_git_probe(_: Path) -> dict[str, object]:
        pytest.fail("product runtime source must not use private-state git probing")

    monkeypatch.setattr("loopx.contract._git_probe", fail_git_probe)

    payload = scan_public_boundary([repo_root])

    assert payload["ok"] is True
    assert payload["scanned_files"] == 1


@pytest.mark.skipif(
    os.name == "nt" or os.geteuid() == 0,
    reason="mode 000 read denial needs a non-root POSIX host",
)
def test_scan_public_boundary_reports_unreadable_file(tmp_path: Path) -> None:
    (tmp_path / "ok.md").write_text("hello\n", encoding="utf-8")
    locked = tmp_path / "locked.md"
    locked.write_text("hello\n", encoding="utf-8")
    locked.chmod(0o000)
    try:
        payload = scan_public_boundary([tmp_path])
    finally:
        locked.chmod(0o644)

    assert payload["ok"] is True
    assert payload["unreadable_files"] == ["locked.md: Permission denied"]


def test_virtualenv_pruning_preserves_tracked_and_explicit_scan_targets(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    env = repo / "custom-python"
    env.mkdir(parents=True)
    (env / "pyvenv.cfg").write_text("include-system-site-packages = false\n")
    payload = 'AUTH = "' + "tok" + 'en=syntheticsyntheticsynthetic"\n'
    dependency = env / "dependency.py"
    owned = env / "owned.py"
    unmarked = repo / "unmarked" / "dependency.py"
    unmarked.parent.mkdir()
    for path in (dependency, owned, unmarked):
        path.write_text(payload)
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "add", "custom-python/owned.py"], check=True)

    # Marked dependencies are omitted; owned content cannot hide behind a marker.
    assert iter_scan_files(repo) == sorted([owned, unmarked])
    assert iter_scan_files(env) == [owned]
    result = scan_public_boundary([repo])
    assert result["scanned_files"] == 2
    assert result["ok"] is False
    assert any("custom-python/owned.py" in hit for hit in result["hits"])
    # An explicit file remains an explicit request even inside a pruned tree.
    assert iter_scan_files(dependency) == [dependency]
    assert scan_public_boundary([dependency])["hits"]
    (env / "pyvenv.cfg").unlink()
    assert dependency in iter_scan_files(repo)
