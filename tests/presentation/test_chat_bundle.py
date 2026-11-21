"""Delivery invariants: coherent builds, bounded upgrade history and fail-closed inputs."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import zipfile

import pytest
from loopx.presentation.chat_bundle import MANIFEST, runtime_source_root, validate_bundle

spec = importlib.util.spec_from_file_location(
    "chat_builder", Path(__file__).resolve().parents[2] / "scripts/chat_bundle.py"
)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


@pytest.fixture
def source(tmp_path, monkeypatch):
    (tmp_path / "apps/presentation/dashboard").mkdir(parents=True)
    (tmp_path / "apps/presentation/dashboard/package.json").write_text("{}")
    monkeypatch.setattr(builder, "ROOT", tmp_path)
    monkeypatch.setattr(builder, "OUTPUT", tmp_path / "loopx/web/chat")
    monkeypatch.setattr(builder.shutil, "which", lambda _: "npm")
    generation = {"name": "one", "fail": False}

    def run(command, **kwargs):
        if command[0] == "git":
            return subprocess.CompletedProcess(command, 0, stdout="a" * 40 + "\n")
        if command[1] == "ci":
            return subprocess.CompletedProcess(command, 0)
        output = Path(kwargs["env"]["LOOPX_CHAT_OUT_DIR"])
        (output / "assets").mkdir(parents=True)
        name = generation["name"]
        (output / f"assets/{name}.js").write_text(f"export const generation = '{name}'")
        (output / "index.html").write_text(
            f'<script type="module" src="/chat/assets/{name}.js"></script>'
        )
        (output / "manifest.webmanifest").write_text("{}")
        if generation["fail"]:
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(builder.subprocess, "run", run)
    return generation


def test_only_previous_actual_delivery_survives_second_upgrade(source, tmp_path):
    builder.build(None)
    first = tmp_path / "first"
    shutil.copytree(builder.OUTPUT, first)
    source["name"] = "two"
    builder.build(first)
    second = tmp_path / "second"
    shutil.copytree(builder.OUTPUT, second)
    source["name"] = "three"
    builder.build(second)
    manifest = validate_bundle(builder.OUTPUT, source_root=tmp_path)
    assert manifest["current_assets"] == ["assets/three.js"]
    assert {p.name for p in (builder.OUTPUT / "assets").iterdir()} == {
        "two.js",
        "three.js",
    }
    assert (builder.OUTPUT / "assets/two.js").read_bytes() == (
        second / "assets/two.js"
    ).read_bytes()


def test_source_update_and_asset_corruption_are_rejected(source, tmp_path):
    builder.build(None)
    (tmp_path / "apps/presentation/dashboard/package.json").write_text(
        '{"changed": true}'
    )
    with pytest.raises(RuntimeError, match="source changed"):
        validate_bundle(builder.OUTPUT, source_root=tmp_path)
    validate_bundle(builder.OUTPUT)  # Installed wheels do not need a source tree.
    (builder.OUTPUT / "assets/one.js").write_text("corrupt")
    with pytest.raises(RuntimeError, match="bundle file changed"):
        validate_bundle(builder.OUTPUT)


def test_failed_build_preserves_existing_delivery(source):
    builder.build(None)
    original = (builder.OUTPUT / MANIFEST).read_bytes()
    source.update(name="two", fail=True)
    with pytest.raises(subprocess.CalledProcessError):
        builder.build(None)
    assert (builder.OUTPUT / MANIFEST).read_bytes() == original
    validate_bundle(builder.OUTPUT)
    source.update(name="recovered", fail=False)
    builder.build(None)
    assert validate_bundle(builder.OUTPUT)["current_assets"] == ["assets/recovered.js"]


def test_concurrent_cold_ensure_serializes_shared_build_and_reuses_delivery(tmp_path):
    """Separate installers share npm state; only one may build a cold checkout."""
    (tmp_path / "apps/presentation/dashboard").mkdir(parents=True)
    (tmp_path / "apps/presentation/dashboard/package.json").write_text("{}")
    backend = tmp_path / "backend.py"
    backend.write_text(
        """import os, pathlib, sys, time
root = pathlib.Path(sys.argv[1])
mode = sys.argv[2]
busy = root / 'backend-busy'
fd = os.open(busy, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
try:
    with (root / 'backend-calls').open('a') as calls:
        calls.write(mode + '\\n')
    time.sleep(0.2)
    if mode == 'build':
        output = pathlib.Path(sys.argv[3])
        (output / 'assets').mkdir(parents=True)
        (output / 'assets/current.js').write_text('export const ready = true')
        (output / 'index.html').write_text('<script src="/chat/assets/current.js"></script>')
        (output / 'manifest.webmanifest').write_text('{}')
finally:
    os.close(fd)
    busy.unlink()
""",
        encoding="utf-8",
    )
    driver = tmp_path / "driver.py"
    driver.write_text(
        """import importlib.util, pathlib, subprocess, sys, time
spec = importlib.util.spec_from_file_location('builder', sys.argv[1])
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
root = pathlib.Path(sys.argv[2])
builder.ROOT = root
builder.OUTPUT = root / 'loopx/web/chat'
builder.shutil.which = lambda _: sys.executable
original_run = subprocess.run
def run(command, **kwargs):
    if command[0] == 'git':
        return subprocess.CompletedProcess(command, 0, stdout='a' * 40)
    mode = 'ci' if command[1] == 'ci' else 'build'
    output = kwargs.get('env', {}).get('LOOPX_CHAT_OUT_DIR', '')
    return original_run([sys.executable, str(root / 'backend.py'), str(root), mode, output], check=True)
builder.subprocess.run = run
(root / ('ready-' + sys.argv[3])).touch()
deadline = time.monotonic() + 10
while not (root / 'start').exists():
    if time.monotonic() > deadline:
        raise RuntimeError('test start barrier timed out')
    time.sleep(0.01)
builder.ensure()
""",
        encoding="utf-8",
    )
    processes = [
        subprocess.Popen(
            [sys.executable, str(driver), str(spec.origin), str(tmp_path), str(index)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for index in range(2)
    ]
    try:
        deadline = time.monotonic() + 10
        while not all((tmp_path / f"ready-{index}").exists() for index in range(2)):
            assert time.monotonic() < deadline, "workers did not reach the start barrier"
            time.sleep(0.01)
        (tmp_path / "start").touch()
        for process in processes:
            stdout, stderr = process.communicate(timeout=20)
            assert process.returncode == 0, (stdout, stderr)
        assert (tmp_path / "backend-calls").read_text().splitlines() == ["ci", "build"]
        assert validate_bundle(tmp_path / "loopx/web/chat", source_root=tmp_path)[
            "current_assets"
        ] == ["assets/current.js"]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.communicate()


def test_explicit_installed_predecessor_overrides_local_history(source, tmp_path):
    builder.build(None)
    installed = tmp_path / "installed"
    shutil.copytree(builder.OUTPUT, installed)
    source["name"] = "two"
    builder.build(None)
    source["name"] = "three"
    builder.build(None)
    builder.ensure(installed)
    assert {p.name for p in (builder.OUTPUT / "assets").iterdir()} == {
        "one.js",
        "three.js",
    }


@pytest.mark.parametrize(
    "payload", [[], {}, {"schema_version": "loopx_chat_bundle_v1", "files": []}]
)
def test_malformed_manifest_has_actionable_error(source, payload):
    builder.build(None)
    (builder.OUTPUT / MANIFEST).write_text(json.dumps(payload))
    with pytest.raises(RuntimeError, match="npm run build:chat"):
        validate_bundle(builder.OUTPUT)


def test_wheel_extraction_rejects_escape_and_duplicates(tmp_path):
    for index, names in enumerate([["../../escaped.js"], ["index.html", "index.html"]]):
        wheel = tmp_path / f"invalid-{index}.whl"
        with zipfile.ZipFile(wheel, "w") as archive:
            for name in names:
                archive.writestr("loopx/web/chat/" + name, "invalid")
        with pytest.raises(RuntimeError):
            builder.extract_wheel(wheel, tmp_path / f"extract-{index}")
    assert not (tmp_path / "escaped.js").exists()


def test_legacy_wheel_assets_are_retained_on_first_upgrade(source, tmp_path):
    legacy = tmp_path / "legacy"
    (legacy / "assets").mkdir(parents=True)
    (legacy / "index.html").write_text('<script src="/chat/assets/old.js"></script>')
    (legacy / "assets/old.js").write_text("old code")
    builder.build(legacy)
    assert (builder.OUTPUT / "assets/old.js").read_text() == "old code"
    validate_bundle(builder.OUTPUT)


def test_same_asset_name_different_bytes_cannot_overwrite_current(source, tmp_path):
    legacy = tmp_path / "legacy"
    (legacy / "assets").mkdir(parents=True)
    (legacy / "index.html").write_text('<script src="/chat/assets/one.js"></script>')
    (legacy / "assets/one.js").write_text("different bytes")
    (legacy / "assets/extra.js").write_text("force history")
    with pytest.raises(RuntimeError, match="different content"):
        builder.build(legacy)
    assert not builder.OUTPUT.exists()


@pytest.mark.parametrize("checksum_valid", [True, False])
@pytest.mark.parametrize(
    ("target", "predecessor"),
    [("v1.1.0", "v1.0.5"), ("v2.0.0a1", "v1.2.0")],
)
def test_release_predecessor_is_stable_older_and_checksum_verified(
    tmp_path, monkeypatch, checksum_valid, target, predecessor
):
    def release(tag, *, draft=False, prerelease=False):
        return {
            "tag_name": tag,
            "draft": draft,
            "prerelease": prerelease,
            "assets": [{"name": "loopx-1.0.5-py3-none-any.whl"}],
        }

    releases = [
        [release(f"runtime-main-{index:040x}", prerelease=True) for index in range(100)],
        [release(f"runtime-main-{index:040x}", prerelease=True) for index in range(100, 200)],
        [
            release("v1.0.4"),
            release("v1.0.5"),
            release("v1.1.0"),
            release("v1.2.0"),
            release("v2.0.0"),
            release("v1.0.9", draft=True),
            release("v1.0.8", prerelease=True),
            release("desktop-main-123"),
        ]
    ]
    monkeypatch.setattr(
        builder.subprocess, "check_output", lambda *a, **k: json.dumps(releases)
    )
    downloads = []

    def download(command, **kwargs):
        downloads.append(command[3])
        directory = Path(command[command.index("--dir") + 1])
        wheel = directory / "loopx-1.0.5-py3-none-any.whl"
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr(
                "loopx/web/chat/index.html",
                '<script src="/chat/assets/old.js"></script>',
            )
            archive.writestr("loopx/web/chat/assets/old.js", "old code")
        digest = builder.contract.digest(wheel) if checksum_valid else "0" * 64
        (directory / "SHA256SUMS").write_text(digest + "  " + wheel.name + "\n")

    monkeypatch.setattr(builder.subprocess, "run", download)
    builds = []
    monkeypatch.setattr(
        builder,
        "build",
        lambda previous, **kwargs: builds.append(
            (previous / "assets/old.js").read_text()
        ),
    )
    if checksum_valid:
        builder.release_build(target, "fixture/repository")
        assert builds == ["old code"]
    else:
        with pytest.raises(RuntimeError, match="checksum mismatch"):
            builder.release_build(target, "fixture/repository")
        assert builds == []
    assert downloads == [predecessor]


def test_release_predecessor_supports_prerelease_target(monkeypatch):
    releases = [[
        {
            "tag_name": "v1.4.0",
            "draft": False,
            "prerelease": False,
            "assets": [{"name": "loopx-1.4.0-py3-none-any.whl"}],
        },
        {
            "tag_name": "v2.0.0",
            "draft": False,
            "prerelease": False,
            "assets": [{"name": "loopx-2.0.0-py3-none-any.whl"}],
        },
    ]]
    monkeypatch.setattr(
        builder.subprocess,
        "check_output",
        lambda *args, **kwargs: json.dumps(releases),
    )
    downloads = []

    def download(command, **kwargs):
        downloads.append(command[3])
        directory = Path(command[command.index("--dir") + 1])
        wheel = directory / "loopx-1.4.0-py3-none-any.whl"
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr(
                "loopx/web/chat/index.html",
                '<script src="/chat/assets/old.js"></script>',
            )
            archive.writestr("loopx/web/chat/assets/old.js", "old code")
        (directory / "SHA256SUMS").write_text(
            f"{builder.contract.digest(wheel)}  {wheel.name}\n"
        )

    monkeypatch.setattr(builder.subprocess, "run", download)
    builds = []
    monkeypatch.setattr(
        builder,
        "build",
        lambda previous, **kwargs: builds.append(
            (previous / "assets/old.js").read_text()
        ),
    )

    builder.release_build("v2.0.0a1", "fixture/repository")

    assert downloads == ["v1.4.0"]
    assert builds == ["old code"]


def test_windows_drive_paths_are_not_portable_bundle_names():
    assert not builder.contract.safe_relative("C:/escape.js")
    assert not builder.contract.safe_relative("C:escape.js")


def test_source_fingerprints_normalize_windows_text_but_not_binary(tmp_path):
    manifest = tmp_path / "manifest.webmanifest"
    assert builder.contract.source_digest(
        manifest, b"{}\r\n"
    ) == builder.contract.source_digest(manifest, b"{}\n")
    placeholder = tmp_path / ".gitkeep"
    assert builder.contract.source_digest(
        placeholder, b"\r\n"
    ) == builder.contract.source_digest(placeholder, b"\n")
    image = tmp_path / "image.png"
    assert builder.contract.source_digest(
        image, b"image\r\n"
    ) != builder.contract.source_digest(image, b"image\n")


def test_frontend_source_inputs_ignore_placeholder_checkout_line_endings(tmp_path):
    public = tmp_path / "apps/presentation/dashboard/public"
    public.mkdir(parents=True)
    placeholder = public / ".gitkeep"
    asset = public / "icon.svg"
    placeholder.write_bytes(b"\n")
    asset.write_bytes(b"<svg/>\n")
    original = builder.contract.source_inputs(tmp_path)

    placeholder.write_bytes(b"\r\n")
    assert builder.contract.source_inputs(tmp_path) == original
    assert "apps/presentation/dashboard/public/.gitkeep" not in original

    asset.write_bytes(b"<svg><path/></svg>\n")
    assert builder.contract.source_inputs(tmp_path) != original


@pytest.mark.parametrize("name", ["contract.ts", "contract.json"])
def test_provider_contract_update_invalidates_packaged_frontend(source, tmp_path, name):
    # A changed caller contract must not ship with previously built controls.
    contract = tmp_path / "loopx/zcode_goal_mode" / name
    contract.parent.mkdir(parents=True)
    contract.write_text("original contract", encoding="utf-8")
    builder.build(None)
    contract.write_text("changed contract", encoding="utf-8")
    with pytest.raises(RuntimeError, match="source changed"):
        validate_bundle(builder.OUTPUT, source_root=tmp_path)


def test_runtime_source_root_skips_source_scan_only_for_immutable_installed_release(
    source, tmp_path, monkeypatch,
):
    builder.build(None)
    (tmp_path / "release.json").write_text(
        '{"release_id": "20261120T000000Z"}\n',
        encoding="utf-8",
    )

    assert runtime_source_root(tmp_path) is None

    def fail_source_inputs(_root):
        raise AssertionError("installed releases must not re-hash source inputs")

    monkeypatch.setattr("loopx.presentation.chat_bundle.source_inputs", fail_source_inputs)
    manifest = validate_bundle(builder.OUTPUT, source_root=runtime_source_root(tmp_path))
    assert manifest["schema_version"] == "loopx_chat_bundle_v1"

    # Served asset integrity checks remain active even when source scanning is skipped.
    index = builder.OUTPUT / "index.html"
    index.write_text("tampered", encoding="utf-8")
    with pytest.raises(RuntimeError, match="bundle file changed: index.html"):
        validate_bundle(builder.OUTPUT, source_root=runtime_source_root(tmp_path))


@pytest.mark.parametrize("git_kind", ["dir", "file"])
def test_runtime_source_root_retains_checkout_verification_when_git_present(tmp_path, git_kind):
    (tmp_path / "release.json").write_text(
        '{"release_id": "20261120T000000Z"}\n',
        encoding="utf-8",
    )
    git_marker = tmp_path / ".git"
    if git_kind == "dir":
        git_marker.mkdir()
    else:
        git_marker.write_text("gitdir: /tmp/worktree\n", encoding="utf-8")

    assert runtime_source_root(tmp_path) == tmp_path.resolve()


@pytest.mark.parametrize(
    "release_json",
    [None, "{invalid json", "[]", "{}", '{"release_id": ""}', '{"release_id": "   "}'],
)
def test_runtime_source_root_fails_closed_without_valid_release_manifest(tmp_path, release_json):
    if release_json is not None:
        (tmp_path / "release.json").write_text(release_json, encoding="utf-8")

    assert runtime_source_root(tmp_path) == tmp_path.resolve()


def test_release_build_retries_transient_gh_errors_with_clean_download_dir(monkeypatch):
    releases = [[{
        "tag_name": "v2.3.0",
        "draft": False,
        "prerelease": False,
        "assets": [{"name": "loopx-2.3.0-py3-none-any.whl"}],
    }]]
    sleeps = []
    monkeypatch.setattr(builder.time, "sleep", sleeps.append)

    api_attempts = 0

    def flaky_check_output(command, **kwargs):
        nonlocal api_attempts
        api_attempts += 1
        if api_attempts < 2:
            raise subprocess.CalledProcessError(1, command, stderr="HTTP 500")
        return json.dumps(releases)

    monkeypatch.setattr(builder.subprocess, "check_output", flaky_check_output)

    download_attempts = 0
    saw_partial_marker = []

    def flaky_download(command, **kwargs):
        nonlocal download_attempts
        download_attempts += 1
        directory = Path(command[command.index("--dir") + 1])
        saw_partial_marker.append((directory / "partial.tmp").exists())
        if download_attempts < 4:
            (directory / "partial.tmp").write_bytes(b"corrupt partial download")
            raise subprocess.CalledProcessError(1, command)
        wheel = directory / "loopx-2.3.0-py3-none-any.whl"
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr(
                "loopx/web/chat/index.html",
                '<script src="/chat/assets/prev.js"></script>',
            )
            archive.writestr("loopx/web/chat/assets/prev.js", "prev code")
        (directory / "SHA256SUMS").write_text(
            f"{builder.contract.digest(wheel)}  {wheel.name}\n"
        )

    monkeypatch.setattr(builder.subprocess, "run", flaky_download)
    builds = []
    monkeypatch.setattr(
        builder,
        "build",
        lambda previous, **kwargs: builds.append((previous / "assets/prev.js").read_text()),
    )

    builder.release_build("v2.4.0", "fixture/repository")

    assert api_attempts == 2
    assert download_attempts == 4
    assert saw_partial_marker == [False, False, False, False]
    assert sleeps == [1.0, 1.0, 2.0, 4.0]
    assert builds == ["prev code"]


def test_release_build_exhausts_bounded_gh_download_retries(monkeypatch):
    releases = [[{
        "tag_name": "v2.3.0",
        "draft": False,
        "prerelease": False,
        "assets": [{"name": "loopx-2.3.0-py3-none-any.whl"}],
    }]]
    sleeps = []
    monkeypatch.setattr(builder.time, "sleep", sleeps.append)
    monkeypatch.setattr(
        builder.subprocess, "check_output", lambda *a, **k: json.dumps(releases)
    )

    download_attempts = 0

    def always_fail(command, **kwargs):
        nonlocal download_attempts
        download_attempts += 1
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(builder.subprocess, "run", always_fail)

    with pytest.raises(subprocess.CalledProcessError):
        builder.release_build("v2.4.0", "fixture/repository")

    assert download_attempts == 4
    assert sleeps == [1.0, 2.0, 4.0]
