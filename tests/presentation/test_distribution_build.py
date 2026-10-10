"""Real setuptools builds must reflect current source, including deletions.

Run with the project's build-system dependency installed. The tiny source tree
uses the shipped setup hook and bundle validator without installing LoopX.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def checkout(tmp_path):
    pytest.importorskip("setuptools", reason="requires the declared build backend")
    source = tmp_path / "source"
    source.mkdir()
    shutil.copyfile(ROOT / "setup.py", source / "setup.py")
    (source / "pyproject.toml").write_text(
        '[project]\nname="loopx-build-fixture"\nversion="0.0.0"\n'
        '[tool.setuptools.packages.find]\ninclude=["loopx*"]\n'
        '[tool.setuptools.package-data]\n"loopx"=["web/chat/**/*", "*.json"]\n'
        '"loopx.control_plane"=["*.ts", "*.json"]\n',
        encoding="utf-8",
    )
    for package in (
        "loopx",
        "loopx/presentation",
        "loopx/control_plane",
        "loopx/retired",
    ):
        directory = source / package
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "__init__.py").write_text("", encoding="utf-8")
    for name in (
        "loopx/presentation/chat_bundle.py",
        "loopx/control_plane/content_digest.py",
    ):
        shutil.copyfile(ROOT / name, source / name)
    for name, content in {
        "loopx/current.py": "VALUE = 'first'\n",
        "loopx/obsolete.py": "VALUE = 'retired'\n",
        "loopx/retired/writer.py": "VALUE = 'retired package'\n",
        "loopx/control_plane/current.ts": "export const current = true;\n",
        "loopx/control_plane/obsolete.ts": "export const obsolete = true;\n",
        "loopx/retained.json": '{"current":true}',
        "loopx/obsolete.json": '{"obsolete":true}',
    }.items():
        (source / name).write_text(content, encoding="utf-8")
    bundle = source / "loopx/web/chat"
    (bundle / "assets").mkdir(parents=True)
    files = {
        "index.html": '<script src="/chat/assets/current.js"></script>',
        "manifest.webmanifest": "{}",
        "assets/current.js": "current code",
        "assets/previous.js": "previous delivery code",
        "asset-retention.json": json.dumps(
            {
                "schema_version": "loopx_chat_asset_retention_v1",
                "generations": [["assets/current.js"], ["assets/previous.js"]],
            }
        ),
    }
    for name, content in files.items():
        (bundle / name).write_text(content, encoding="utf-8")
    (bundle / "bundle-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "loopx_chat_bundle_v1",
                "current_assets": ["assets/current.js"],
                "files": {
                    name: hashlib.sha256((bundle / name).read_bytes()).hexdigest()
                    for name in files
                },
            }
        ),
        encoding="utf-8",
    )
    return source


def run_setup(source, *arguments):
    environment = {
        key: value for key, value in os.environ.items() if key != "PYTHONPATH"
    }
    return subprocess.run(
        [sys.executable, "setup.py", *arguments],
        cwd=source,
        env=environment,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )


def wheel(source, output, *, keep_temp=False, skip_build=False):
    arguments = ["bdist_wheel", "--dist-dir", str(output)]
    if keep_temp:
        arguments.append("--keep-temp")
    if skip_build:
        arguments.append("--skip-build")
    result = run_setup(source, *arguments)
    assert result.returncode == 0, result.stdout + result.stderr
    with zipfile.ZipFile(next(output.glob("*.whl"))) as archive:
        return {
            name: archive.read(name)
            for name in archive.namelist()
            if name.startswith("loopx/")
        }


@pytest.mark.parametrize("keep_temp", [False, True])
def test_reused_build_removes_deleted_sources_and_keeps_current_resources(
    checkout, tmp_path, keep_temp
):
    first = wheel(checkout, tmp_path / "first", keep_temp=keep_temp)
    obsolete = {
        "loopx/obsolete.py",
        "loopx/control_plane/obsolete.ts",
        "loopx/obsolete.json",
        "loopx/retired/__init__.py",
        "loopx/retired/writer.py",
    }
    assert obsolete <= first.keys()
    for name in obsolete:
        (checkout / name).unlink()
    (checkout / "loopx/retired").rmdir()
    (checkout / "loopx/current.py").write_text("VALUE = 'second'\n", encoding="utf-8")
    # An unrelated build output is outside the LoopX package hook's ownership.
    unrelated = checkout / "build/lib/other-package.bin"
    unrelated.write_bytes(b"preserve unrelated build output")
    second = wheel(checkout, tmp_path / "second", keep_temp=keep_temp)
    assert not obsolete & second.keys()
    assert second["loopx/current.py"] == b"VALUE = 'second'\n"
    assert (
        second["loopx/control_plane/current.ts"]
        == first["loopx/control_plane/current.ts"]
    )
    assert second["loopx/retained.json"] == first["loopx/retained.json"]
    for name in first:
        if name.startswith("loopx/web/chat/"):
            assert second[name] == first[name]
    assert unrelated.read_bytes() == b"preserve unrelated build output"
    assert wheel(checkout, tmp_path / "third", keep_temp=keep_temp) == second


def test_invalid_frontend_does_not_clear_previous_build(checkout, tmp_path):
    first = wheel(checkout, tmp_path / "first")
    (checkout / "loopx/web/chat/assets/current.js").write_text(
        "corrupt", encoding="utf-8"
    )
    result = run_setup(checkout, "build_py")
    assert result.returncode != 0
    assert "bundle file changed" in result.stderr
    assert (checkout / "build/lib/loopx/current.py").read_bytes() == first[
        "loopx/current.py"
    ]


def test_explicit_skip_build_packages_the_selected_existing_build(checkout, tmp_path):
    first = wheel(checkout, tmp_path / "first", keep_temp=True)
    (checkout / "loopx/current.py").write_text(
        "VALUE = 'not built'\n", encoding="utf-8"
    )
    second = wheel(checkout, tmp_path / "second", skip_build=True)
    assert second == first


@pytest.mark.parametrize(
    "command,option", [("build_py", "--build-lib"), ("bdist_wheel", "--bdist-dir")]
)
def test_build_destination_cannot_overlap_source(checkout, command, option):
    original = (checkout / "loopx/current.py").read_bytes()
    result = run_setup(checkout, command, option, ".")
    assert result.returncode != 0
    assert "outside the LoopX source package" in result.stderr
    assert (checkout / "loopx/current.py").read_bytes() == original


def test_editable_build_does_not_require_assets_or_clear_outputs(checkout):
    shutil.rmtree(checkout / "loopx/web/chat")
    marker = checkout / "build/lib/loopx/marker"
    marker.parent.mkdir(parents=True)
    marker.write_bytes(b"preserve editable output")
    # Exercise setuptools' same declared command class in editable mode.
    script = (
        "import runpy; from setuptools import Distribution; import setuptools; "
        "setuptools.setup=lambda **kwargs: None; "
        "hook=runpy.run_path('setup.py')['BuildWithFrontend']; "
        "command=hook(Distribution({'packages':['loopx']})); "
        "command.ensure_finalized(); command.editable_mode=True; command.run()"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=checkout,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert marker.read_bytes() == b"preserve editable output"
