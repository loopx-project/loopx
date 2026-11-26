from __future__ import annotations

import json
import os
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
    installed = SimpleNamespace(name="loopx", version=manifest.__version__, files=items,
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
        "loopx/doctor_git.py": Path(manifest.__file__).with_name("doctor_git.py").read_text(),
        "loopx/python_install_owner.py": Path(manifest.__file__).with_name("python_install_owner.py").read_text(),
        "loopx/extensions/__init__.py": "",
        "loopx/extensions/process_runtime.py": Path(manifest.__file__).with_name(
            "extensions"
        ).joinpath("process_runtime.py").read_text(),
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
        venv.EnvBuilder(with_pip=True, symlinks=os.name != "nt").create(environment)
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


def test_record_root_alias_keeps_exact_physical_ownership(owned_package):
    package, installed = owned_package
    before = manifest.release_runtime_identity()
    alias = package.parent.parent / "site-alias"
    alias.symlink_to(package.parent, target_is_directory=True)
    for item in installed.files:
        name = item.as_posix()
        item.locate = lambda name=name: alias / name
    assert manifest.release_runtime_identity() == before


def test_core_companion_distributions_extend_and_fence_runtime_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "site-packages"
    package = root / "loopx"
    package.mkdir(parents=True)
    files = {
        "release_manifest.py": "core",
        "capabilities/issue_fix/cli.py": "issue-fix",
        "capabilities/pr_review_queue/core.py": "review",
    }
    for rel, content in files.items():
        target = package / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def _entry(rel: str):
        return SimpleNamespace(as_posix=lambda rel=rel: "loopx/" + rel,
                               locate=lambda rel=rel: package / rel)

    dists = {
        "loopx-core": SimpleNamespace(
            name="loopx-core",
            version=manifest.__version__,
            files=[_entry("release_manifest.py")],
            read_text=lambda _name: None,
        ),
        "loopx-capability-issue-fix": SimpleNamespace(
            name="loopx-capability-issue-fix",
            version=manifest.__version__,
            files=[_entry("capabilities/issue_fix/cli.py")],
            read_text=lambda _name: None,
        ),
        "loopx-capability-pull-request-review": SimpleNamespace(
            name="loopx-capability-pull-request-review",
            version=manifest.__version__,
            files=[_entry("capabilities/pr_review_queue/core.py")],
            read_text=lambda _name: None,
        ),
    }

    def _lookup(name: str):
        if name not in dists:
            raise manifest.PackageNotFoundError(name)
        return dists[name]

    monkeypatch.setattr(manifest, "__file__", str(package / "release_manifest.py"))
    monkeypatch.setattr(manifest, "distribution", _lookup)
    monkeypatch.delenv("LOOPX_RELEASE_ROOT", raising=False)

    both_fp = manifest.release_runtime_identity()["package_fingerprint"]
    assert both_fp.startswith("sha256:")

    # Mismatched companion version or overlapping RECORD entry must fail closed.
    dists["loopx-capability-pull-request-review"].version = "0.0.0"
    assert "package_fingerprint" not in manifest.release_runtime_identity()

    dists.pop("loopx-capability-pull-request-review")
    (package / "capabilities/pr_review_queue/core.py").unlink()
    single_fp = manifest.release_runtime_identity()["package_fingerprint"]
    assert single_fp.startswith("sha256:") and single_fp != both_fp

    (package / "capabilities/issue_fix/cli.py").write_text("tampered")
    tampered_fp = manifest.release_runtime_identity()["package_fingerprint"]
    assert tampered_fp != single_fp

    dists["loopx-capability-issue-fix"].files.append(_entry("release_manifest.py"))
    assert "package_fingerprint" not in manifest.release_runtime_identity()


@pytest.mark.parametrize("change", ["owned", "wrong_version", "overlap", "absent"])
def test_matching_workspace_owns_only_its_records(owned_package, monkeypatch, change):
    _package, core = owned_package
    original = manifest.release_runtime_identity()
    core.name = "loopx-core"
    asset = core.files.pop()
    workspace = SimpleNamespace(name="loopx-workspace", version=manifest.__version__,
                                files=[asset], read_text=lambda _name: None)
    if change == "wrong_version":
        workspace.version = "0.0.0"
    elif change == "overlap":
        workspace.files.append(core.files[0])

    def lookup(name):
        if name == "loopx-core":
            return core
        if name == "loopx-workspace" and change != "absent":
            return workspace
        raise manifest.PackageNotFoundError(name)

    monkeypatch.setattr(manifest, "distribution", lookup)
    result = manifest.release_runtime_identity()
    if change == "owned":
        assert result == original
    else:
        assert "package_fingerprint" not in result


def test_builtin_capability_requires_every_recorded_artifact(owned_package, monkeypatch):
    from loopx import python_install_owner as install_owner

    package, core = owned_package
    core.name = "loopx-core"
    owner_file = package / "python_install_owner.py"
    owner_file.write_text("python")
    asset = core.files.pop()
    store_file = package / "chat_store.py"
    store_file.write_text("store")
    store_item = SimpleNamespace(as_posix=lambda: "loopx/chat_store.py", locate=lambda: store_file)
    workspace = SimpleNamespace(
        name=install_owner.WORKSPACE_DISTRIBUTION,
        version=manifest.__version__,
        files=[asset, store_item],
        read_text=lambda _name: None,
    )
    monkeypatch.setattr(install_owner, "__file__", str(owner_file))
    monkeypatch.setattr(
        install_owner,
        "distribution",
        lambda name: core if name == "loopx-core" else workspace,
    )
    monkeypatch.setattr(
        install_owner,
        "find_spec",
        lambda module: SimpleNamespace(origin=str(store_file)) if module == "loopx.chat_store" else None,
    )
    assert install_owner.builtin_capability_installed(
        install_owner.WORKSPACE_DISTRIBUTION, ("loopx.chat_store",), builtin_cli_present=False,
    )
    (package / "web/chat/index.html").unlink()
    assert not install_owner.builtin_capability_installed(
        install_owner.WORKSPACE_DISTRIBUTION, ("loopx.chat_store",), builtin_cli_present=False,
    )


def test_real_record_missing_asset_fails_closed_despite_filtered_dist_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from importlib.metadata import Distribution, PackageNotFoundError
    from loopx import python_install_owner as install_owner

    site = tmp_path / "site-packages"
    package = site / "loopx"
    (package / "web/chat").mkdir(parents=True)
    files = {
        "loopx/release_manifest.py": "python",
        "loopx/python_install_owner.py": "owner",
        "loopx/chat_store.py": "store",
        "loopx/web/chat/bundle-manifest.json": '{"files":{}}',
    }
    for rel, content in files.items():
        (site / rel).write_text(content, encoding="utf-8")

    def _make_dist(name: str, owned: list[str]):
        dist_info = site / f"{name.replace('-', '_')}-{manifest.__version__}.dist-info"
        dist_info.mkdir(parents=True, exist_ok=True)
        (dist_info / "METADATA").write_text(
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {manifest.__version__}\n",
            encoding="utf-8",
        )
        (dist_info / "RECORD").write_text(
            "".join(f"{entry},,\n" for entry in owned), encoding="utf-8"
        )
        return Distribution.at(dist_info)

    core = _make_dist("loopx-core", ["loopx/release_manifest.py", "loopx/python_install_owner.py"])
    workspace = _make_dist(
        install_owner.WORKSPACE_DISTRIBUTION,
        ["loopx/chat_store.py", "loopx/web/chat/bundle-manifest.json"],
    )

    def _lookup(name: str):
        if name == "loopx-core":
            return core
        if name == install_owner.WORKSPACE_DISTRIBUTION:
            return workspace
        raise PackageNotFoundError(name)

    monkeypatch.setattr(manifest, "__file__", str(site / "loopx/release_manifest.py"))
    monkeypatch.setattr(manifest, "distribution", _lookup)
    monkeypatch.delenv("LOOPX_RELEASE_ROOT", raising=False)
    monkeypatch.setattr(install_owner, "__file__", str(site / "loopx/python_install_owner.py"))
    monkeypatch.setattr(install_owner, "distribution", _lookup)
    monkeypatch.setattr(
        install_owner,
        "find_spec",
        lambda module: SimpleNamespace(origin=str(site / "loopx/chat_store.py"))
        if module == "loopx.chat_store"
        else None,
    )

    before = manifest.release_runtime_identity()
    assert before["package_fingerprint"].startswith("sha256:")
    assert install_owner.builtin_capability_installed(
        install_owner.WORKSPACE_DISTRIBUTION, ("loopx.chat_store",), builtin_cli_present=False,
    )

    bundle_manifest = site / "loopx/web/chat/bundle-manifest.json"
    bundle_manifest.unlink()
    assert all(p.as_posix() != "loopx/web/chat/bundle-manifest.json" for p in workspace.files or ())
    assert not install_owner.builtin_capability_installed(
        install_owner.WORKSPACE_DISTRIBUTION, ("loopx.chat_store",), builtin_cli_present=False,
    )
    assert "package_fingerprint" not in manifest.release_runtime_identity()

    bundle_manifest.write_text('{"files":{}}', encoding="utf-8")
    assert install_owner.builtin_capability_installed(
        install_owner.WORKSPACE_DISTRIBUTION, ("loopx.chat_store",), builtin_cli_present=False,
    )
    assert manifest.release_runtime_identity() == before
