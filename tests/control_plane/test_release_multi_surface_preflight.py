from __future__ import annotations

import json
import runpy
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "release_multi_surface_preflight.py"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _create_fixture_repo(tmp_path: Path, *, core_version: str = "2.4.0", dsh_version: str = "0.1.1-beta.7") -> Path:
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", str(remote))

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Fixture User")
    _git(repo, "config", "user.email", "fixture@example.com")

    (repo / "pyproject.toml").write_text(
        f'[project]\nname = "loopx"\nversion = "{core_version}"\n',
        encoding="utf-8",
    )
    (repo / "loopx" / "control_plane" / "testing").mkdir(parents=True)
    (repo / "loopx" / "__init__.py").write_text(f'__version__ = "{core_version}"\n', encoding="utf-8")
    (repo / "loopx" / "control_plane" / "testing" / "release_metadata.py").write_text(
        (REPO_ROOT / "loopx" / "control_plane" / "testing" / "release_metadata.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (repo / "man").mkdir()
    (repo / "man" / "loopx.1").write_text(".TH LOOPX 1\n", encoding="utf-8")

    desktop = repo / "apps" / "desktop" / "loopx-control-plane" / "src-tauri"
    desktop.mkdir(parents=True)
    (repo / "apps" / "desktop" / "loopx-control-plane" / "package.json").write_text(
        '{"name": "@loopx/control-plane-desktop", "version": "0.1.0"}\n',
        encoding="utf-8",
    )
    (desktop / "Cargo.toml").write_text('[package]\nname = "loopx-control-plane"\nversion = "0.1.0"\n', encoding="utf-8")
    (desktop / "tauri.conf.json").write_text(
        json.dumps(
            {
                "version": "0.1.0",
                "plugins": {
                    "updater": {
                        "pubkey": "fixture-pubkey",
                        "endpoints": ["https://github.com/loopx-project/loopx/releases/download/desktop-stable/desktop-updater.json"],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (repo / "scripts").mkdir()
    (repo / "scripts" / "desktop_macos_signing.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    (repo / "scripts" / "desktop_release_artifacts.py").write_text(
        (REPO_ROOT / "scripts" / "desktop_release_artifacts.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    dsh = repo / "packages" / "dsh-loopx-plugin"
    dsh.mkdir(parents=True)
    (dsh / "package.json").write_text(
        json.dumps(
            {
                "name": "dsh-loopx-plugin",
                "version": dsh_version,
                "keywords": ["dsh-plugin"],
                "dsh": {"bundle": {"patch": "./cordis.patch.yml"}},
                "repository": {"type": "git", "url": "git+https://github.com/loopx-project/loopx.git"},
            }
        ),
        encoding="utf-8",
    )
    (dsh / "cordis.patch.yml").write_text("patches: {}\n", encoding="utf-8")
    (dsh / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")

    notes_dir = repo / "docs" / "product" / "releases"
    notes_dir.mkdir(parents=True)
    (notes_dir / f"v{core_version}.md").write_text(
        "## Release Decision\n\nGuide: https://my.feishu.cn/docx/FixtureDoc123\n\n### 升级决策\n\n验证完成。\n",
        encoding="utf-8",
    )

    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "fixture release candidate")
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-u", "origin", "HEAD:refs/heads/stable")
    _git(repo, "push", "-u", "origin", "HEAD:refs/heads/main")
    _git(repo, "fetch", "origin")
    return repo


def test_real_checkout_offline_preflight_passes() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--repo-root",
            str(REPO_ROOT),
            "--format",
            "json",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["ok"] is True
    assert payload["schema_version"] == "loopx_multi_surface_release_preflight_v1"
    assert payload["ready_for_local_qualification"] is True
    assert payload["published_complete"] is None
    assert set(payload["surfaces"]) == {"core", "desktop", "dsh", "notes"}


def test_fixture_offline_detects_dirty_tree_forbidden_note_path_and_version_mismatch(tmp_path: Path) -> None:
    mod = runpy.run_path(str(SCRIPT_PATH))
    run_preflight = mod["run_preflight"]
    repo = _create_fixture_repo(tmp_path)

    clean = run_preflight(repo_root=repo, selected_surfaces=("core", "desktop", "dsh", "notes"), require_clean=True)
    assert clean["ok"] is True

    # Dirty tracked file fails --require-clean
    (repo / "man" / "loopx.1").write_text(".TH LOOPX 1 modified\n", encoding="utf-8")
    # Forbidden local path string breaks notes local_ok
    (repo / "docs" / "product" / "releases" / "v2.4.0.md").write_text(
        "## Release Decision\nBad path /Users/example/work\n### 升级决策\n",
        encoding="utf-8",
    )
    bad = run_preflight(repo_root=repo, selected_surfaces=("core", "notes"), require_clean=True)
    assert bad["ok"] is False
    assert bad["surfaces"]["core"]["tracked_clean"] is False
    assert bad["surfaces"]["core"]["local_ok"] is False
    assert bad["surfaces"]["notes"]["public_boundary_clean"] is False
    assert bad["surfaces"]["notes"]["local_ok"] is False

    # Version mismatch fails metadata loader
    (repo / "loopx" / "__init__.py").write_text('__version__ = "2.5.0"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="version mismatch"):
        run_preflight(repo_root=repo, selected_surfaces=("core",))


@pytest.mark.parametrize(
    ("npm_status", "npm_payload", "expected_state", "expected_code", "expected_published"),
    [
        (404, {}, "bootstrap_required", "npm_bootstrap_required", False),
        (
            200,
            {"dist-tags": {"latest": "0.0.0-stage"}, "versions": {"0.0.0-stage": {}}, "maintainers": []},
            "staging_scan_in_progress",
            "npm_staging_scan_in_progress",
            False,
        ),
        (
            200,
            {
                "dist-tags": {"latest": "0.1.1-beta.7"},
                "versions": {"0.1.1-beta.7": {}},
                "maintainers": [{"name": "fixture-maintainer"}],
            },
            "published",
            None,
            True,
        ),
    ],
)
def test_fixture_online_classifies_npm_bootstrap_staging_and_published(
    tmp_path: Path,
    npm_status: int,
    npm_payload: dict[str, Any],
    expected_state: str,
    expected_code: str | None,
    expected_published: bool,
) -> None:
    mod = runpy.run_path(str(SCRIPT_PATH))
    run_preflight = mod["run_preflight"]
    repo = _create_fixture_repo(tmp_path)

    expected_dsh_tarball = "https://github.com/loopx-project/loopx/releases/download/dsh-loopx-plugin-v0.1.1-beta.7/dsh-loopx-plugin-0.1.1-beta.7.tgz"

    def fake_gh_release(_gh_repo: str, tag: str) -> dict[str, Any]:
        if tag == "v2.4.0":
            return {
                "available": True,
                "exists": True,
                "is_draft": False,
                "asset_names": [
                    "DESKTOP-SHA256SUMS",
                    "LoopX.app.tar.gz",
                    "LoopX.app.tar.gz.sig",
                    "LoopX.app.zip",
                    "LoopX_2.4.0_aarch64.dmg",
                    "LoopX_2.4.0_x64-setup.exe",
                    "LoopX_2.4.0_x64_en-US.msi",
                    "SHA256SUMS",
                    "desktop-updater.json",
                    "loopx-2.4.0-py3-none-any.whl",
                    "loopx-2.4.0.tar.gz",
                ],
            }
        if tag == "desktop-stable":
            return {
                "available": True,
                "exists": True,
                "is_draft": False,
                "asset_names": ["desktop-updater.json"],
            }
        if tag == "dsh-loopx-plugin-v0.1.1-beta.7":
            return {
                "available": True,
                "exists": True,
                "is_draft": False,
                "asset_names": ["dsh-loopx-plugin-0.1.1-beta.7.tgz"],
            }
        return {"available": True, "exists": False, "asset_names": []}

    def fake_http(url: str) -> tuple[int | None, str, str | None]:
        if "pypi.org/pypi/loopx/json" in url:
            return 200, json.dumps({"releases": {"2.4.0": []}}), None
        if "registry.npmjs.org/dsh-loopx-plugin" in url:
            return npm_status, json.dumps(npm_payload), None
        if "awesome-dsh-plugin" in url:
            return 200, f"tarball: {expected_dsh_tarball}\n", None
        if "feishu.cn/docx/" in url:
            return 200, "ok", None
        return 404, "", "HTTP 404"

    result = run_preflight(
        repo_root=repo,
        selected_surfaces=("core", "desktop", "dsh", "notes"),
        online=True,
        require_published=True,
        http_get=fake_http,
        gh_release=fake_gh_release,
    )
    assert result["surfaces"]["dsh"]["online"]["npm"]["state"] == expected_state
    assert result["surfaces"]["dsh"]["published_ok"] is expected_published
    assert result["published_complete"] is expected_published
    assert result["ok"] is expected_published
    codes = [item["code"] for item in result["remediations"]]
    if expected_code is None:
        assert codes == []
    else:
        assert expected_code in codes
        if expected_code == "npm_bootstrap_required":
            bootstrap_msg = next(
                item["action"] for item in result["remediations"] if item["code"] == "npm_bootstrap_required"
            )
            assert "Allow npm publish" in bootstrap_msg
