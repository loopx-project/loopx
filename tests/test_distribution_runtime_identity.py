from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

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
