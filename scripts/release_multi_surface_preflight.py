#!/usr/bin/env python3
"""Preflight and readback checks across LoopX's four release surfaces.

Covers core CLI/PyPI, Desktop App/Tauri updater, DSH plugin/npm/awesome-dsh,
and bilingual release notes + Feishu guide without importing the candidate
``loopx`` package.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import runpy
import shutil
import subprocess
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

SCHEMA_VERSION = "loopx_multi_surface_release_preflight_v1"
ALL_SURFACES: tuple[str, ...] = ("core", "desktop", "dsh", "notes")
DEFAULT_AWESOME_DSH_REPO = "awesome-dsh-plugin/awesome-dsh-plugin"
DEFAULT_GITHUB_REPO = "loopx-project/loopx"
FORBIDDEN_NOTE_PATTERNS: tuple[str, ...] = (
    "/Users/",
    "/private/tmp/",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "raw_thread",
    "session_history",
    "verifier_output_tail",
    "ACTIVE_GOAL_STATE.md:",
)


def _run_git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def _http_get_text(url: str, *, timeout: float = 15.0, attempts: int = 2) -> tuple[int | None, str, str | None]:
    last_err: str | None = None
    for attempt in range(max(1, attempts)):
        req = urllib.request.Request(url, headers={"User-Agent": "loopx-release-preflight/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return int(resp.status), resp.read().decode("utf-8", errors="replace"), None
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if exc.fp is not None else ""
            return int(exc.code), body, f"HTTP {exc.code}"
        except Exception as exc:
            last_err = str(exc)
            if attempt + 1 < attempts:
                time.sleep(0.5)
    return None, "", last_err


def _gh_release_view(github_repo: str, tag: str) -> dict[str, Any]:
    if not shutil.which("gh"):
        return {"available": False, "error": "gh CLI not found"}
    proc = subprocess.run(
        [
            "gh",
            "release",
            "view",
            tag,
            "--repo",
            github_repo,
            "--json",
            "tagName,isDraft,isPrerelease,targetCommitish,assets",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return {
            "available": True,
            "exists": False,
            "error": (proc.stderr or proc.stdout).strip() or f"exit {proc.returncode}",
        }
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return {"available": True, "exists": False, "error": f"invalid gh json: {exc}"}
    assets = [
        {
            "name": item.get("name"),
            "size": item.get("size"),
            "url": item.get("url"),
        }
        for item in (payload.get("assets") or [])
        if isinstance(item, dict)
    ]
    return {
        "available": True,
        "exists": True,
        "tag_name": payload.get("tagName"),
        "is_draft": payload.get("isDraft"),
        "is_prerelease": payload.get("isPrerelease"),
        "asset_names": sorted(a["name"] for a in assets if isinstance(a.get("name"), str)),
        "assets": assets,
    }


def _load_core_metadata(repo_root: Path) -> dict[str, str]:
    metadata_script = repo_root / "loopx" / "control_plane" / "testing" / "release_metadata.py"
    if not metadata_script.is_file():
        raise ValueError(f"missing static metadata reader: {metadata_script}")
    ns = runpy.run_path(str(metadata_script))
    return dict(ns["checkout_release_metadata"](repo_root))


def _check_core(
    repo_root: Path,
    *,
    core_meta: dict[str, str],
    github_repo: str,
    online: bool,
    require_clean: bool,
    http_get: Callable[[str], tuple[int | None, str, str | None]],
    gh_release: Callable[[str, str], dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    remediations: list[dict[str, str]] = []
    version = core_meta["version"]
    expected_tag = f"v{version}"
    head = _run_git(repo_root, "rev-parse", "HEAD")
    head_sha = head.stdout.strip() if head.returncode == 0 else None

    status = _run_git(repo_root, "status", "--porcelain", "--untracked-files=no")
    tracked_clean = status.returncode == 0 and not status.stdout.strip()

    stable_ref = _run_git(repo_root, "rev-parse", "--verify", "origin/stable")
    stable_sha = stable_ref.stdout.strip() if stable_ref.returncode == 0 else None
    stable_is_ancestor: bool | None = None
    if stable_sha and head_sha:
        anc = _run_git(repo_root, "merge-base", "--is-ancestor", stable_sha, head_sha)
        stable_is_ancestor = anc.returncode == 0

    manpage_exists = (repo_root / "man" / "loopx.1").is_file()
    local_ok = bool(version) and manpage_exists and (stable_is_ancestor is not False) and (
        not require_clean or tracked_clean
    )
    if not tracked_clean:
        remediations.append(
            {
                "surface": "core",
                "code": "dirty_tracked_tree",
                "action": "Commit or isolate changes in a clean worktree before qualifying or tagging a release.",
            }
        )
    if not manpage_exists:
        remediations.append(
            {
                "surface": "core",
                "code": "missing_manpage",
                "action": "Run scripts/render-manpage.py --output man/loopx.1 and examples/cli-help-manpage-smoke.py.",
            }
        )
    if stable_is_ancestor is False:
        remediations.append(
            {
                "surface": "core",
                "code": "stable_not_ancestor",
                "action": "Reconcile divergent origin/stable history through a reviewed merge PR before tagging.",
            }
        )

    surface: dict[str, Any] = {
        "local_ok": local_ok,
        "published_ok": None,
        "package_name": core_meta["name"],
        "version": version,
        "expected_tag": expected_tag,
        "head_commit": head_sha,
        "tracked_clean": tracked_clean,
        "origin_stable_commit": stable_sha,
        "stable_is_ancestor_of_head": stable_is_ancestor,
        "manpage_present": manpage_exists,
    }
    if online:
        gh_info = gh_release(github_repo, expected_tag)
        asset_names = set(gh_info.get("asset_names") or [])
        expected_whl = f"loopx-{version}-py3-none-any.whl"
        expected_sdist = f"loopx-{version}.tar.gz"
        github_release_ok = bool(
            gh_info.get("exists")
            and gh_info.get("is_draft") is False
            and expected_whl in asset_names
            and expected_sdist in asset_names
            and "SHA256SUMS" in asset_names
        )

        code, body, err = http_get(f"https://pypi.org/pypi/{urllib.parse.quote(core_meta['name'])}/json")
        pypi_has_version = False
        if code == 200 and body:
            try:
                pypi_data = json.loads(body)
                pypi_has_version = version in (pypi_data.get("releases") or {})
            except json.JSONDecodeError:
                err = "invalid PyPI json"
        surface["online"] = {
            "github_release": {
                "exists": bool(gh_info.get("exists")),
                "is_draft": gh_info.get("is_draft"),
                "expected_assets_present": github_release_ok,
                "error": gh_info.get("error"),
            },
            "pypi": {
                "http_status": code,
                "version_present": pypi_has_version,
                "error": err,
            },
        }
        surface["published_ok"] = github_release_ok and pypi_has_version
        if not github_release_ok:
            remediations.append(
                {
                    "surface": "core",
                    "code": "github_core_release_incomplete",
                    "action": f"Publish non-draft tag {expected_tag} via release-artifacts.yml with wheel, sdist, and SHA256SUMS.",
                }
            )
        if not pypi_has_version:
            remediations.append(
                {
                    "surface": "core",
                    "code": "pypi_version_missing",
                    "action": f"Verify PYPI_PUBLISH_ENABLED=true and PyPI Trusted Publisher for loopx=={version}.",
                }
            )
    return surface, remediations


def _check_desktop(
    repo_root: Path,
    *,
    core_version: str,
    github_repo: str,
    online: bool,
    gh_release: Callable[[str, str], dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    remediations: list[dict[str, str]] = []
    desktop_root = repo_root / "apps" / "desktop" / "loopx-control-plane"
    pkg_path = desktop_root / "package.json"
    tauri_path = desktop_root / "src-tauri" / "tauri.conf.json"
    cargo_path = desktop_root / "src-tauri" / "Cargo.toml"
    signing_script = repo_root / "scripts" / "desktop_macos_signing.py"
    artifacts_script = repo_root / "scripts" / "desktop_release_artifacts.py"

    missing_files = [
        str(p.relative_to(repo_root))
        for p in (pkg_path, tauri_path, cargo_path, signing_script, artifacts_script)
        if not p.is_file()
    ]
    tauri_ok = False
    updater_endpoints: list[str] = []
    updater_pubkey_present = False
    tauri_version: str | None = None
    cargo_version: str | None = None
    expected_assets: list[str] = []

    if tauri_path.is_file():
        tauri_cfg = json.loads(tauri_path.read_text(encoding="utf-8"))
        tauri_version = tauri_cfg.get("version")
        updater = ((tauri_cfg.get("plugins") or {}).get("updater") or {})
        pubkey = updater.get("pubkey")
        endpoints = updater.get("endpoints")
        updater_pubkey_present = isinstance(pubkey, str) and bool(pubkey.strip())
        if isinstance(endpoints, list):
            updater_endpoints = [str(e) for e in endpoints if isinstance(e, str)]
        tauri_ok = bool(updater_pubkey_present and updater_endpoints)

    if cargo_path.is_file():
        cargo_data = tomllib.loads(cargo_path.read_text(encoding="utf-8"))
        cargo_version = (cargo_data.get("package") or {}).get("version")

    if artifacts_script.is_file():
        ns = runpy.run_path(str(artifacts_script))
        expected_assets = list(ns["expected_artifact_names"](core_version))

    local_ok = not missing_files and tauri_ok and bool(expected_assets)
    if not local_ok:
        remediations.append(
            {
                "surface": "desktop",
                "code": "desktop_config_incomplete",
                "action": "Check tauri.conf.json updater pubkey/endpoints and scripts/desktop_release_artifacts.py.",
            }
        )

    surface: dict[str, Any] = {
        "local_ok": local_ok,
        "published_ok": None,
        "tauri_config_version": tauri_version,
        "cargo_version": cargo_version,
        "release_tag_version": core_version,
        "updater_pubkey_present": updater_pubkey_present,
        "updater_endpoints": updater_endpoints,
        "expected_installer_assets": expected_assets,
        "missing_files": missing_files,
    }
    if online:
        expected_tag = f"v{core_version}"
        gh_info = gh_release(github_repo, expected_tag)
        asset_names = set(gh_info.get("asset_names") or [])
        present_assets = [name for name in expected_assets if name in asset_names]
        missing_assets = [name for name in expected_assets if name not in asset_names]
        checksum_present = "DESKTOP-SHA256SUMS" in asset_names
        updater_info = gh_release(github_repo, "desktop-stable")
        updater_assets = set(updater_info.get("asset_names") or [])
        updater_feed_ok = bool(
            updater_info.get("exists")
            and "desktop-updater.json" in updater_assets
            and "desktop-updater.json" in asset_names
            and "LoopX.app.tar.gz" in asset_names
            and "LoopX.app.tar.gz.sig" in asset_names
        )
        surface["online"] = {
            "release_tag": expected_tag,
            "present_installer_assets": present_assets,
            "missing_installer_assets": missing_assets,
            "checksum_manifest_present": checksum_present,
            "desktop_stable_feed_ok": updater_feed_ok,
        }
        surface["published_ok"] = (
            bool(gh_info.get("exists"))
            and not missing_assets
            and checksum_present
            and updater_feed_ok
        )
        if missing_assets or not checksum_present:
            remediations.append(
                {
                    "surface": "desktop",
                    "code": "desktop_release_assets_incomplete",
                    "action": f"Dispatch desktop-release-artifacts.yml for tag {expected_tag} (missing: {missing_assets}).",
                }
            )
        if not updater_feed_ok:
            remediations.append(
                {
                    "surface": "desktop",
                    "code": "desktop_updater_feed_incomplete",
                    "action": "Verify desktop-updater.yml published desktop-updater.json under desktop-stable and LoopX.app.tar.gz(.sig) + desktop-updater.json under the versioned release tag.",
                }
            )
    return surface, remediations


def _check_dsh(
    repo_root: Path,
    *,
    github_repo: str,
    awesome_repo: str,
    online: bool,
    http_get: Callable[[str], tuple[int | None, str, str | None]],
    gh_release: Callable[[str, str], dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    remediations: list[dict[str, str]] = []
    dsh_root = repo_root / "packages" / "dsh-loopx-plugin"
    pkg_path = dsh_root / "package.json"
    lock_path = dsh_root / "pnpm-lock.yaml"
    if not pkg_path.is_file():
        return {
            "local_ok": False,
            "published_ok": None,
            "error": "missing packages/dsh-loopx-plugin/package.json",
        }, [
            {
                "surface": "dsh",
                "code": "dsh_package_missing",
                "action": "Restore packages/dsh-loopx-plugin/package.json.",
            }
        ]

    pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
    name = str(pkg.get("name") or "")
    version = str(pkg.get("version") or "")
    expected_tag = f"{name}-v{version}"
    expected_tarball_name = f"{name}-{version}.tgz"
    expected_tarball_url = (
        f"https://github.com/{github_repo}/releases/download/{expected_tag}/{expected_tarball_name}"
    )
    keywords = pkg.get("keywords") or []
    patch_rel = str(((pkg.get("dsh") or {}).get("bundle") or {}).get("patch") or "")
    patch_ok = bool(patch_rel) and (dsh_root / patch_rel).is_file()
    lock_ok = lock_path.is_file()
    has_dsh_keyword = isinstance(keywords, list) and "dsh-plugin" in keywords
    local_ok = bool(name and version and patch_ok and lock_ok and has_dsh_keyword)
    if not local_ok:
        remediations.append(
            {
                "surface": "dsh",
                "code": "dsh_manifest_incomplete",
                "action": "Verify package name/version, dsh-plugin keyword, cordis.patch.yml, and pnpm-lock.yaml.",
            }
        )

    surface: dict[str, Any] = {
        "local_ok": local_ok,
        "published_ok": None,
        "package_name": name,
        "version": version,
        "expected_tag": expected_tag,
        "expected_tarball_name": expected_tarball_name,
        "expected_tarball_url": expected_tarball_url,
        "has_dsh_plugin_keyword": has_dsh_keyword,
        "patch_present": patch_ok,
        "pnpm_lock_present": lock_ok,
    }
    if online:
        gh_info = gh_release(github_repo, expected_tag)
        asset_names = set(gh_info.get("asset_names") or [])
        gh_tarball_ok = bool(gh_info.get("exists") and expected_tarball_name in asset_names)

        npm_code, npm_body, npm_err = http_get(f"https://registry.npmjs.org/{urllib.parse.quote(name)}")
        npm_state = "error"
        latest_tag: str | None = None
        npm_has_version = False
        npm_maintainers: list[str] = []
        if npm_code == 404:
            npm_state = "bootstrap_required"
        elif npm_code == 200 and npm_body:
            try:
                npm_data = json.loads(npm_body)
                versions = list((npm_data.get("versions") or {}).keys())
                latest_tag = (npm_data.get("dist-tags") or {}).get("latest")
                npm_has_version = version in versions
                npm_maintainers = [
                    str(m.get("name") or m.get("username"))
                    for m in (npm_data.get("maintainers") or [])
                    if isinstance(m, dict) and (m.get("name") or m.get("username"))
                ]
                if versions == ["0.0.0-stage"]:
                    npm_state = "staging_scan_in_progress"
                elif npm_has_version and latest_tag == version:
                    npm_state = "published"
                else:
                    npm_state = "tag_or_version_mismatch"
            except json.JSONDecodeError:
                npm_err = "invalid npm json"

        cat_url = (
            f"https://raw.githubusercontent.com/{awesome_repo}/main/data/plugins/"
            f"loopx-project__loopx--packages-dsh-loopx-plugin.yml"
        )
        cat_code, cat_body, cat_err = http_get(cat_url)
        cat_match = re.search(r"^tarball:\s*(.+)$", cat_body, flags=re.MULTILINE) if cat_code == 200 else None
        catalog_tarball = cat_match.group(1).strip() if cat_match else None
        catalog_ok = catalog_tarball == expected_tarball_url

        surface["online"] = {
            "github_release": {
                "exists": bool(gh_info.get("exists")),
                "tarball_present": gh_tarball_ok,
                "error": gh_info.get("error"),
            },
            "npm": {
                "state": npm_state,
                "http_status": npm_code,
                "latest_dist_tag": latest_tag,
                "target_version_present": npm_has_version,
                "maintainers": npm_maintainers,
                "error": npm_err,
            },
            "awesome_catalog": {
                "http_status": cat_code,
                "tarball": catalog_tarball,
                "matches_expected_url": catalog_ok,
                "error": cat_err,
            },
        }
        surface["published_ok"] = gh_tarball_ok and npm_state == "published" and catalog_ok

        if not gh_tarball_ok:
            remediations.append(
                {
                    "surface": "dsh",
                    "code": "dsh_github_release_missing",
                    "action": f"Publish GitHub Release {expected_tag} with {expected_tarball_name}.",
                }
            )
        if npm_state == "bootstrap_required":
            remediations.append(
                {
                    "surface": "dsh",
                    "code": "npm_bootstrap_required",
                    "action": (
                        f"First-time npm bootstrap required for {name}: enable npm 2FA (auth-and-writes), "
                        f"run one-time web-auth 'npm publish {expected_tarball_name} --access public --tag latest --ignore-scripts', "
                        "run 'npm logout', then bind Trusted Publisher (loopx-project / loopx / dsh-plugin-publish.yml / "
                        "blank Environment name / check Allow npm publish)."
                    ),
                }
            )
        elif npm_state != "published":
            remediations.append(
                {
                    "surface": "dsh",
                    "code": f"npm_{npm_state}",
                    "action": (
                        f"Dispatch dsh-plugin-publish.yml with release_tag={expected_tag} and verify Trusted Publisher "
                        "(Allow npm publish enabled) plus verify-distribution.mjs readback."
                    ),
                }
            )
        if not catalog_ok:
            remediations.append(
                {
                    "surface": "dsh",
                    "code": "awesome_catalog_stale",
                    "action": f"Update {awesome_repo} tarball URL to {expected_tarball_url}.",
                }
            )
    return surface, remediations


def _check_notes(
    repo_root: Path,
    *,
    version: str,
    online: bool,
    http_get: Callable[[str], tuple[int | None, str, str | None]],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    remediations: list[dict[str, str]] = []
    note_rel = f"docs/product/releases/v{version}.md"
    note_path = repo_root / note_rel
    exists = note_path.is_file()
    has_en_decision = False
    has_zh_decision = False
    boundary_hits: list[str] = []
    feishu_urls: list[str] = []

    if exists:
        text = note_path.read_text(encoding="utf-8")
        has_en_decision = "## Release Decision" in text
        has_zh_decision = "### 升级决策" in text
        boundary_hits = [needle for needle in FORBIDDEN_NOTE_PATTERNS if needle in text]
        feishu_urls = sorted(set(re.findall(r"https://[^\s)>\]]*feishu\.cn/docx/[A-Za-z0-9]+", text)))
    readiness_doc = repo_root / "docs" / "product" / "release-readiness.md"
    if not feishu_urls and readiness_doc.is_file():
        rtext = readiness_doc.read_text(encoding="utf-8")
        marker = f"`v{version}` release"
        idx = rtext.find(marker)
        if idx >= 0:
            snippet = rtext[idx : idx + 2000]
            feishu_urls = sorted(set(re.findall(r"https://[^\s)>\]]*feishu\.cn/docx/[A-Za-z0-9]+", snippet)))

    local_ok = exists and has_en_decision and has_zh_decision and not boundary_hits
    if not exists:
        remediations.append(
            {
                "surface": "notes",
                "code": "release_note_missing",
                "action": f"Create {note_rel} from docs/product/release-note-template.md and validate via release-readiness-doc-smoke.py.",
            }
        )
    elif boundary_hits:
        remediations.append(
            {
                "surface": "notes",
                "code": "release_note_public_boundary_hit",
                "action": f"Remove forbidden strings {boundary_hits} from {note_rel}.",
            }
        )

    surface: dict[str, Any] = {
        "local_ok": local_ok,
        "published_ok": None,
        "release_note_path": note_rel,
        "exists": exists,
        "has_english_release_decision": has_en_decision,
        "has_chinese_release_decision": has_zh_decision,
        "public_boundary_clean": not boundary_hits,
        "feishu_guide_urls": feishu_urls,
    }
    if online:
        feishu_checks: list[dict[str, Any]] = []
        for url in feishu_urls:
            code, _, err = http_get(url)
            feishu_checks.append({"url": url, "http_status": code, "reachable": bool(code and code < 400), "error": err})
        feishu_present = bool(feishu_checks)
        surface["online"] = {"feishu_guides": feishu_checks}
        surface["published_ok"] = local_ok and feishu_present and all(item["reachable"] for item in feishu_checks)
        if not feishu_present:
            remediations.append(
                {
                    "surface": "notes",
                    "code": "feishu_guide_link_missing",
                    "action": "Publish the companion Personal Feishu Release Guide and link its URL in bilingual sections.",
                }
            )
    return surface, remediations


def run_preflight(
    *,
    repo_root: Path,
    selected_surfaces: tuple[str, ...],
    online: bool = False,
    require_published: bool = False,
    require_clean: bool = False,
    github_repo: str = DEFAULT_GITHUB_REPO,
    awesome_dsh_repo: str = DEFAULT_AWESOME_DSH_REPO,
    http_get: Callable[[str], tuple[int | None, str, str | None]] = _http_get_text,
    gh_release: Callable[[str, str], dict[str, Any]] = _gh_release_view,
) -> dict[str, Any]:
    core_meta = _load_core_metadata(repo_root)
    surfaces: dict[str, Any] = {}
    remediations: list[dict[str, str]] = []

    if "core" in selected_surfaces:
        surf, rem = _check_core(
            repo_root,
            core_meta=core_meta,
            github_repo=github_repo,
            online=online,
            require_clean=require_clean,
            http_get=http_get,
            gh_release=gh_release,
        )
        surfaces["core"] = surf
        remediations.extend(rem)
    if "desktop" in selected_surfaces:
        surf, rem = _check_desktop(
            repo_root,
            core_version=core_meta["version"],
            github_repo=github_repo,
            online=online,
            gh_release=gh_release,
        )
        surfaces["desktop"] = surf
        remediations.extend(rem)
    if "dsh" in selected_surfaces:
        surf, rem = _check_dsh(
            repo_root,
            github_repo=github_repo,
            awesome_repo=awesome_dsh_repo,
            online=online,
            http_get=http_get,
            gh_release=gh_release,
        )
        surfaces["dsh"] = surf
        remediations.extend(rem)
    if "notes" in selected_surfaces:
        surf, rem = _check_notes(
            repo_root,
            version=core_meta["version"],
            online=online,
            http_get=http_get,
        )
        surfaces["notes"] = surf
        remediations.extend(rem)

    ready_for_local = all(bool(item.get("local_ok")) for item in surfaces.values())
    published_complete: bool | None = None
    if online:
        published_complete = all(bool(item.get("published_ok")) for item in surfaces.values())

    ok = ready_for_local and (not require_published or bool(published_complete))
    return {
        "ok": ok,
        "schema_version": SCHEMA_VERSION,
        "repo_root": repo_root.name,
        "mode": "online" if online else "offline",
        "selected_surfaces": list(selected_surfaces),
        "ready_for_local_qualification": ready_for_local,
        "published_complete": published_complete,
        "surfaces": surfaces,
        "remediations": remediations,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# Multi-Surface Release Preflight (`{payload['mode']}`)",
        "",
        f"- **Local qualification ready:** `{payload['ready_for_local_qualification']}`",
        f"- **Published complete:** `{payload['published_complete']}`",
        "",
        "| Surface | Local | Published | Key Detail |",
        "| --- | --- | --- | --- |",
    ]
    for name in payload["selected_surfaces"]:
        item = payload["surfaces"][name]
        detail = item.get("expected_tag") or item.get("release_note_path") or item.get("release_tag_version") or ""
        if name == "dsh" and isinstance(item.get("online"), dict):
            detail = f"{detail} (npm={item['online']['npm']['state']})"
        lines.append(f"| `{name}` | `{item.get('local_ok')}` | `{item.get('published_ok')}` | `{detail}` |")
    if payload["remediations"]:
        lines.extend(["", "## Remediations", ""])
        for rem in payload["remediations"]:
            lines.append(f"- **[{rem['surface']}] `{rem['code']}`:** {rem['action']}")
    return "\n".join(lines) + "\n"


def _parse_surfaces(raw: list[str] | None) -> tuple[str, ...]:
    if not raw or "all" in raw:
        return ALL_SURFACES
    seen: list[str] = []
    for item in raw:
        if item not in seen:
            seen.append(item)
    return tuple(seen)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--surface",
        action="append",
        choices=("all", *ALL_SURFACES),
        help="Target release surface to inspect (default: all).",
    )
    parser.add_argument("--online", action="store_true", help="Probe GitHub Releases, PyPI, npmjs, and awesome-dsh-plugin.")
    parser.add_argument(
        "--require-published",
        action="store_true",
        help="Exit non-zero unless all selected surfaces are also verified published online (implies --online).",
    )
    parser.add_argument(
        "--require-clean",
        action="store_true",
        help="Fail local preflight when tracked repository files have uncommitted modifications.",
    )
    parser.add_argument("--github-repo", default=DEFAULT_GITHUB_REPO)
    parser.add_argument("--awesome-dsh-repo", default=DEFAULT_AWESOME_DSH_REPO)
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    online = bool(args.online or args.require_published)
    # Strip ambient proxy vars unless caller opted out explicitly; public registries work direct.
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(key, None)
    payload = run_preflight(
        repo_root=args.repo_root.resolve(),
        selected_surfaces=_parse_surfaces(args.surface),
        online=online,
        require_published=bool(args.require_published),
        require_clean=bool(args.require_clean),
        github_repo=args.github_repo,
        awesome_dsh_repo=args.awesome_dsh_repo,
    )
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(payload), end="")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
