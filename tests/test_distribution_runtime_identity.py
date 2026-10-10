from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys
import venv
import zipfile

import pytest

from loopx import release_manifest as manifest


@pytest.fixture
def owned_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "site-packages"
    package = root / "loopx"
    package.mkdir(parents=True)
    contents = {"release_manifest.py": "python", "control_plane/core.ts": "typed",
                "web/chat/index.html": "frontend"}
    for name, content in contents.items():
        path = package / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    items = [SimpleNamespace(as_posix=lambda name=name: "loopx/" + name,
                             locate=lambda name=name: package / name) for name in contents]
    installed = SimpleNamespace(version=manifest.__version__, files=items,
                                read_text=lambda _name: None)
    monkeypatch.setattr(manifest, "__file__", str(package / "release_manifest.py"))
    monkeypatch.setattr(manifest, "distribution", lambda _name: installed)
    monkeypatch.delenv("LOOPX_RELEASE_ROOT", raising=False)
    return package, installed


@pytest.mark.parametrize("changed", ["release_manifest.py", "control_plane/core.ts", "web/chat/index.html"])
def test_owned_wheel_identity_fences_changed_bytes_at_the_same_version(owned_package, changed):
    package, _installed = owned_package
    before = manifest.release_runtime_identity()
    assert before["release_id"] is None and before["source_revision"] is None
    assert before["package_fingerprint"].startswith("sha256:")
    (package / changed).write_text("replacement")
    after = manifest.release_runtime_identity()
    assert after["package_version"] == before["package_version"]
    assert after["package_fingerprint"] != before["package_fingerprint"]


def test_generated_bytecode_does_not_retag_the_installed_artifact(owned_package):
    package, _installed = owned_package
    before = manifest.release_runtime_identity()
    (package / "__pycache__").mkdir()
    (package / "__pycache__/release_manifest.cpython-312.pyc").write_bytes(b"cache")
    assert manifest.release_runtime_identity() == before


def test_real_pip_compiled_and_uncompiled_wheel_have_the_same_identity(tmp_path: Path):
    # Use the production identity module in a small valid wheel. pip, rather
    # than this fixture, generates bytecode and its installed RECORD entries.
    version = manifest.__version__
    metadata = f"loopx-{version}.dist-info"
    contents = {
        "loopx/__init__.py": Path(manifest.__file__).with_name("__init__.py").read_text(),
        "loopx/release_manifest.py": Path(manifest.__file__).read_text(),
        f"{metadata}/METADATA": f"Metadata-Version: 2.1\nName: loopx\nVersion: {version}\n",
        f"{metadata}/WHEEL": "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    contents[f"{metadata}/RECORD"] = "".join(f"{name},,\n" for name in [*contents, f"{metadata}/RECORD"])
    wheel = tmp_path / f"loopx-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, content in contents.items():
            archive.writestr(name, content)
    identities = []
    for compile_bytecode in (True, False):
        environment = tmp_path / ("compiled" if compile_bytecode else "uncompiled")
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        subprocess.run([str(python), "-I", "-m", "pip", "install", "--no-index", "--no-deps",
                        "--disable-pip-version-check", *([] if compile_bytecode else ["--no-compile"]),
                        str(wheel)], check=True, capture_output=True, text=True, timeout=60)
        observed = subprocess.run([str(python), "-I", "-c",
            "import json; from importlib.metadata import distribution; "
            "from loopx.release_manifest import release_runtime_identity; "
            "print(json.dumps({'identity': release_runtime_identity(), "
            "'recorded_bytecode': sum(p.suffix == '.pyc' for p in distribution('loopx').files)}))"],
            check=True, capture_output=True, text=True, timeout=60)
        result = json.loads(observed.stdout)
        assert bool(result["recorded_bytecode"]) is compile_bytecode
        assert result["identity"]["package_fingerprint"].startswith("sha256:")
        identities.append(result["identity"])
    assert identities[0] == identities[1]


@pytest.mark.parametrize("invalid", ["editable", "unowned", "extra", "missing", "symlink", "broken_symlink", "version"])
def test_incomplete_or_redirected_distribution_cannot_claim_an_artifact(owned_package, invalid):
    package, installed = owned_package
    if invalid == "editable":
        installed.read_text = lambda _name: json.dumps({"dir_info": {"editable": True}})
    elif invalid == "unowned":
        installed.files = []
    elif invalid == "extra":
        (package / "injected.py").write_text("extra")
    elif invalid == "missing":
        (package / "control_plane/core.ts").unlink()
    elif invalid == "symlink":
        target = package.parent / "foreign.py"
        target.write_text("foreign")
        (package / "release_manifest.py").unlink()
        (package / "release_manifest.py").symlink_to(target)
    elif invalid == "broken_symlink":
        (package / "unowned.py").symlink_to(package.parent / "absent.py")
    else:
        installed.version = "0.0.0"
    assert "package_fingerprint" not in manifest.release_runtime_identity()


def test_unrelated_source_root_cannot_adopt_an_installed_distribution(owned_package, tmp_path):
    assert "package_fingerprint" not in manifest.release_runtime_identity(tmp_path / "checkout")


def test_run_git_handles_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=0.1)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert manifest._run_git(tmp_path, ["status"], timeout=0.1) is None
