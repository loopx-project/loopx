import os
from pathlib import Path
import subprocess
import sys

import pytest


def blocked_installer_fixture(tmp_path, name):
    source = Path(__file__).parents[1] / "scripts/install-local.sh"
    scripts = tmp_path / "checkout" / "scripts"
    scripts.mkdir(parents=True)
    installer = scripts / source.name
    installer.write_bytes(source.read_bytes())
    installer.chmod(0o755)
    marker = tmp_path / "chat-build-started"
    (scripts / "chat_bundle.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\nraise SystemExit(71)\n"
    )
    home = tmp_path / "home"
    blocked = home / ".local/bin" / name
    blocked.mkdir(parents=True)
    (blocked / "user.txt").write_text("preserve user directory")
    runtime = home / ".codex/loopx"
    runtime.mkdir(parents=True)
    (runtime / "registry.global.json").write_text('{"goals": []}\n')
    env = {**os.environ, "HOME": str(home), "CODEX_HOME": str(home / ".codex"),
           "LOOPX_BIN_DIR": str(blocked.parent), "LOOPX_RELEASES_DIR": str(home / "releases"),
           "LOOPX_PYTHON": sys.executable, "LOOPX_INSTALL_GUARD_HELD": "0"}
    return installer, home, marker, env


@pytest.mark.parametrize(
    ("promote", "canary", "name"),
    [("1", "0", "loopx"), ("1", "0", "loopx-apply-rrule"),
     ("1", "1", "loopx-canary"), ("0", "1", "loopx-canary")],
)
def test_blocked_entry_target_fails_before_build_or_state_changes(tmp_path, promote, canary, name):
    installer, home, marker, env = blocked_installer_fixture(tmp_path, name)
    before = {str(path.relative_to(home)): path.read_bytes()
              for path in home.rglob("*") if path.is_file()}
    result = subprocess.run([str(installer)],
        env={**env, "LOOPX_PROMOTE_DEFAULT": promote, "LOOPX_INSTALL_CANARY": canary},
        capture_output=True, text=True, timeout=10)
    assert result.returncode == 1, result.stderr
    assert "is a directory; remove it before installing" in result.stderr
    assert not marker.exists()
    assert not (home / "releases").exists()
    assert {str(path.relative_to(home)): path.read_bytes()
            for path in home.rglob("*") if path.is_file()} == before


def test_canary_only_does_not_reject_untouched_default_entry(tmp_path):
    installer, _, marker, env = blocked_installer_fixture(tmp_path, "loopx")
    result = subprocess.run([str(installer)],
        env={**env, "LOOPX_PROMOTE_DEFAULT": "0", "LOOPX_INSTALL_CANARY": "1"},
        capture_output=True, text=True, timeout=10)
    assert result.returncode == 71, result.stderr
    assert marker.exists()


def test_clone_failure_discards_only_partial_staging_target(tmp_path):
    script = (Path(__file__).parents[1] / "scripts/install-local.sh").read_text()
    function = script.split("copy_path() {", 1)[1].split("\n}\n", 1)[0]
    source, destination = tmp_path / "source tree", tmp_path / "staged tree"
    source.mkdir()
    (source / "keep.txt").write_text("original")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    copier = binaries / "cp"
    copier.write_text(
        '#!/bin/sh\nif [ "$1" = "-cR" ]; then mkdir -p "$3"; touch "$3/partial"; exit 1; fi\nexec /bin/cp "$@"\n'
    )
    copier.chmod(0o755)
    result = subprocess.run(
        [
            "bash",
            "-c",
            "set -eu\ncopy_platform=Darwin\ncopy_path() {"
            + function
            + '\n}\ncopy_path "$1" "$2"',
            "copy-test",
            str(source),
            str(destination),
        ],
        env={**os.environ, "PATH": f"{binaries}:{os.environ['PATH']}"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert sorted(p.name for p in destination.iterdir()) == ["keep.txt"]
    (destination / "keep.txt").write_text("changed")
    assert (source / "keep.txt").read_text() == "original"


def run_apps_copy(source, destination):
    script = (Path(__file__).parents[1] / "scripts/install-local.sh").read_text()
    functions = "\n".join(
        name + "() {" + script.split(name + "() {", 1)[1].split("\n}\n", 1)[0] + "\n}"
        for name in ("copy_path", "copy_apps")
    )
    return subprocess.run(
        ["bash", "-c", 'set -eu\ncopy_platform=other\n' + functions + '\ncopy_apps "$1" "$2"',
         "apps-copy-test", str(source), str(destination)],
        env={**os.environ, "LOOPX_PYTHON": sys.executable},
        capture_output=True,
        text=True,
    )


def test_apps_snapshot_excludes_developer_trees_without_changing_files(tmp_path):
    source, destination = tmp_path / "source apps", tmp_path / "release apps"
    project = source / "project"
    project.mkdir(parents=True)
    for name in ("node_modules", ".next", "dist", "build", "coverage"):
        tree = project / "nested" / name
        tree.mkdir(parents=True)
        (tree / "not-shipped.js").write_text("development output")
    (project / "nested" / "keep.ts").write_text("source")
    executable = project / "run.sh"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o751)
    (project / "source-link").symlink_to("nested/keep.ts")
    (project / "dangling-link").symlink_to("missing.ts")
    (project / "node_modules").symlink_to("missing-dependency")

    result = run_apps_copy(source, destination)
    assert result.returncode == 0, result.stderr
    assert sorted(p.name for p in (destination / "project" / "nested").iterdir()) == ["keep.ts"]
    delivered = destination / "project"
    assert sorted(p.name for p in delivered.iterdir()) == [
        "dangling-link", "nested", "run.sh", "source-link"
    ]
    assert (delivered / "run.sh").read_bytes() == executable.read_bytes()
    assert (delivered / "run.sh").stat().st_mode & 0o777 == 0o751
    assert (delivered / "source-link").readlink() == Path("nested/keep.ts")
    assert (delivered / "dangling-link").readlink() == Path("missing.ts")
    (delivered / "nested" / "keep.ts").write_text("changed release")
    assert (project / "nested" / "keep.ts").read_text() == "source"
    assert (project / "nested" / "node_modules" / "not-shipped.js").is_file()


def test_apps_snapshot_keeps_ordinary_files_with_excluded_directory_names(tmp_path):
    source, destination = tmp_path / "source", tmp_path / "release"
    source.mkdir()
    names = ("node_modules", ".next", "dist", "build", "coverage")
    for name in names:
        (source / name).write_text("ordinary source file")
    result = run_apps_copy(source, destination)
    assert result.returncode == 0, result.stderr
    assert sorted(p.name for p in destination.iterdir()) == sorted(names)
    assert all((destination / name).read_text() == "ordinary source file" for name in names)


@pytest.mark.parametrize("cache_kind", ["directory", "symlink", "dangling_symlink"])
def test_apps_snapshot_excludes_cargo_cache_without_excluding_target_assets(tmp_path, cache_kind):
    source, destination = tmp_path / "source apps", tmp_path / "release apps"
    crate = source / "desktop" / "control-plane" / "src-tauri"
    crate.mkdir(parents=True)
    (crate / "Cargo.toml").write_text('[package]\nname = "desktop"\nversion = "0.1.0"\n')
    (crate / "main.rs").write_text("fn main() {}\n")
    cache = crate / "target"
    if cache_kind == "directory":
        cache.mkdir()
        (cache / "compiler-output").write_text("development output")
    else:
        external = tmp_path / "compiler cache"
        if cache_kind == "symlink":
            external.mkdir()
            (external / "compiler-output").write_text("development output")
        cache.symlink_to(external, target_is_directory=True)
    asset = crate / "assets" / "target"
    asset.mkdir(parents=True)
    (asset / "keep.txt").write_text("ordinary target asset")

    result = run_apps_copy(source, destination)
    assert result.returncode == 0, result.stderr
    delivered = destination / "desktop" / "control-plane" / "src-tauri"
    assert not (delivered / "target").exists()
    assert not (delivered / "target").is_symlink()
    assert (delivered / "Cargo.toml").read_bytes() == (crate / "Cargo.toml").read_bytes()
    assert (delivered / "main.rs").read_bytes() == (crate / "main.rs").read_bytes()
    assert (delivered / "assets" / "target" / "keep.txt").read_text() == "ordinary target asset"
    assert cache.exists() or cache.is_symlink()


@pytest.mark.parametrize("manifest_kind", ["missing", "directory", "file"])
def test_apps_snapshot_preserves_ordinary_target_files_and_directories(tmp_path, manifest_kind):
    source, destination = tmp_path / "source", tmp_path / "release"
    source.mkdir()
    manifest = source / "Cargo.toml"
    if manifest_kind == "directory":
        manifest.mkdir()
    elif manifest_kind == "file":
        manifest.write_text('[package]\nname = "example"\nversion = "0.1.0"\n')
    target = source / "target"
    if manifest_kind == "file":
        target.write_text("ordinary target file")
    else:
        target.mkdir()
        (target / "keep.txt").write_text("ordinary target asset")
    result = run_apps_copy(source, destination)
    assert result.returncode == 0, result.stderr
    delivered = destination / "target"
    if target.is_file():
        assert delivered.read_bytes() == target.read_bytes()
    else:
        assert (delivered / "keep.txt").read_text() == "ordinary target asset"


def test_apps_snapshot_preserves_root_symlink_and_absent_source(tmp_path):
    source, destination = tmp_path / "source-link", tmp_path / "release-link"
    real_source = tmp_path / "real-source"
    real_source.mkdir()
    source.symlink_to("real-source", target_is_directory=True)
    result = run_apps_copy(source, destination)
    assert result.returncode == 0, result.stderr
    assert destination.is_symlink()
    assert destination.readlink() == Path("real-source")
    absent_destination = tmp_path / "absent-release"
    result = run_apps_copy(tmp_path / "absent-source", absent_destination)
    assert result.returncode == 0, result.stderr
    assert not absent_destination.exists()
