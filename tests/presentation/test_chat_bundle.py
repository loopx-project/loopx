"""Delivery invariants: coherent builds, bounded upgrade history and fail-closed inputs."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

import pytest
from loopx.presentation.chat_bundle import MANIFEST, validate_bundle

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
def test_release_predecessor_is_stable_older_and_checksum_verified(
    tmp_path, monkeypatch, checksum_valid
):
    def release(tag, *, draft=False, prerelease=False):
        return {
            "tag_name": tag,
            "draft": draft,
            "prerelease": prerelease,
            "assets": [{"name": "loopx-1.0.5-py3-none-any.whl"}],
        }

    releases = [
        [
            release("v1.0.4"),
            release("v1.0.5"),
            release("v1.1.0"),
            release("v1.2.0"),
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
        builder.release_build("v1.1.0", "fixture/repository")
        assert builds == ["old code"]
    else:
        with pytest.raises(RuntimeError, match="checksum mismatch"):
            builder.release_build("v1.1.0", "fixture/repository")
        assert builds == []
    assert downloads == ["v1.0.5"]


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
