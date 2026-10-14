#!/usr/bin/env python3
"""Validate the desktop workflow's signing and full-release contracts."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "desktop-release-artifacts.yml"
ARTIFACT_SCRIPT = REPO_ROOT / "scripts" / "desktop_release_artifacts.py"
SPEC = importlib.util.spec_from_file_location("desktop_release_artifacts", ARTIFACT_SCRIPT)
assert SPEC is not None and SPEC.loader is not None
desktop_release_artifacts = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = desktop_release_artifacts
SPEC.loader.exec_module(desktop_release_artifacts)


def assert_contains(text: str, needle: str) -> None:
    if needle not in text:
        raise AssertionError(f"missing desktop release contract: {needle}")


def assert_absent(text: str, needle: str) -> None:
    if needle in text:
        raise AssertionError(f"desktop preview release must not require {needle}")


def main() -> int:
    text = WORKFLOW.read_text(encoding="utf-8")

    for required in [
        "github.event_name == 'pull_request'",
        "github.event_name != 'pull_request'",
        "- name: Validate desktop release workflow\n        if: github.event_name == 'pull_request'",
        'npm run build -- --bundles ${{ matrix.bundles }} --no-sign',
        'npm run build -- --bundles ${{ matrix.bundles }}',
        'RELEASE_TAG: ${{ steps.identity.outputs.release-tag }}',
        'release_version="${RELEASE_TAG#v}"',
        r'\"signingIdentity\":\"-\"',
        'codesign --verify --deep --strict --verbose=2',
        "grep -q '^Signature=adhoc$'",
        'hdiutil verify',
        'bundle/macos/LoopX.app',
        'dist/desktop/LoopX.app.zip',
        'Check out release workflow source',
        'Back up existing desktop release assets',
        'Preserve previous desktop release assets',
        'PREVIOUS-DESKTOP-SHA256SUMS',
        'Validate complete rebuild and generate desktop checksums',
        'Replace complete desktop binary set',
        'gh release delete-asset',
        'Replace desktop checksum manifest last',
        'Verify published desktop release',
        'scripts/desktop_release_artifacts.py write-checksums',
        'scripts/desktop_release_artifacts.py verify-checksums',
        'DESKTOP-SHA256SUMS',
    ]:
        assert_contains(text, required)

    signed_step = text.split("- name: Build integrity-signed macOS release bundles", 1)[1].split(
        "- name:", 1
    )[0]
    if "--no-sign" in signed_step:
        raise AssertionError("integrity-signed macOS release build must not use --no-sign")

    for forbidden in [
        "APPLE_CERTIFICATE",
        "APPLE_ID",
        "KEYCHAIN_PASSWORD",
        "notarytool",
        "stapler",
        "spctl",
    ]:
        assert_absent(text, forbidden)

    binary_upload = text.split("- name: Replace complete desktop binary set", 1)[1].split(
        "- name:", 1
    )[0]
    assert "*.dmg" in binary_upload
    assert "*.zip" in binary_upload
    assert "*.exe" in binary_upload
    assert "*.msi" in binary_upload
    assert "DESKTOP-SHA256SUMS" not in binary_upload.split("gh release upload", 1)[1]

    manifest_upload = text.split("- name: Replace desktop checksum manifest last", 1)[1]
    assert "dist/desktop/DESKTOP-SHA256SUMS" in manifest_upload
    assert text.index("- name: Replace complete desktop binary set") < text.index(
        "- name: Replace desktop checksum manifest last"
    )

    # Opt-in is a read-only CI provider mode; ordinary publishing keeps its owner.
    assert text.count("contents: write") == 1
    assert_contains(text, "contents: read\n      id-token: write\n      attestations: write")
    assert_contains(text, "publication_probe:")
    assert_contains(text, "type: boolean\n        default: false")
    assert_contains(
        text,
        "if: github.event_name != 'pull_request' && !(github.event_name == 'workflow_dispatch' && inputs.publication_probe == true)",
    )
    assert_contains(text, "ref: ${{ github.workflow_sha }}")
    assert_contains(text, "path: .release-tooling")
    assert_contains(text, "persist-credentials: false")
    assert_contains(text, "name: desktop-source-")
    assert_contains(text, "timeout-minutes: 30")
    assert_contains(text, "timeout-minutes: 10")
    assert_absent(text, "continue-on-error")
    assert_absent(text, "actions: write")

    with tempfile.TemporaryDirectory(prefix="loopx-desktop-release-") as temporary:
        dist_dir = Path(temporary)
        version = "0.5.4"
        for name in desktop_release_artifacts.expected_artifact_names(version):
            (dist_dir / name).write_bytes(f"fixture:{name}\n".encode("ascii"))
        manifest = dist_dir / desktop_release_artifacts.MANIFEST_NAME
        desktop_release_artifacts.write_manifest(dist_dir, version, manifest)
        desktop_release_artifacts.verify_manifest(dist_dir, version, manifest)
        assert len(manifest.read_text(encoding="ascii").splitlines()) == 4

        missing = dist_dir / f"LoopX_{version}_x64-setup.exe"
        missing.unlink()
        try:
            desktop_release_artifacts.desktop_artifacts(dist_dir, version)
        except desktop_release_artifacts.DesktopReleaseArtifactError:
            pass
        else:
            raise AssertionError("desktop release accepted a missing Windows installer")
        missing.write_bytes(b"restored installer\n")
        unexpected = dist_dir / "unexpected-desktop-asset.bin"
        unexpected.write_bytes(b"unexpected\n")
        try:
            desktop_release_artifacts.desktop_artifacts(dist_dir, version)
        except desktop_release_artifacts.DesktopReleaseArtifactError:
            pass
        else:
            raise AssertionError("desktop release accepted an unexpected artifact")
        unexpected.unlink()

        desktop_release_artifacts.write_manifest(dist_dir, version, manifest)
        missing.write_bytes(b"tampered installer\n")
        try:
            desktop_release_artifacts.verify_manifest(dist_dir, version, manifest)
        except desktop_release_artifacts.DesktopReleaseArtifactError:
            pass
        else:
            raise AssertionError("desktop release accepted a stale checksum manifest")

    subprocess.run(
        ["node", str(REPO_ROOT / "examples/desktop-release-preflight-smoke.mjs")],
        cwd=REPO_ROOT,
        env={**os.environ, "LOOPX_TEST_PYTHON": sys.executable},
        check=True,
        timeout=30,
    )
    print("desktop-release-workflow-smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
