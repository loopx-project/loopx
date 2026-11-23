#!/usr/bin/env python3
"""Validate the desktop workflow's signing and full-release contracts."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from types import ModuleType
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "desktop-release-artifacts.yml"
ARTIFACT_SCRIPT = REPO_ROOT / "scripts" / "desktop_release_artifacts.py"
SIGNING_SCRIPT = REPO_ROOT / "scripts" / "desktop_macos_signing.py"


def load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


desktop_release_artifacts = load_module("desktop_release_artifacts", ARTIFACT_SCRIPT)
desktop_macos_signing = load_module("desktop_macos_signing", SIGNING_SCRIPT)


def assert_contains(text: str, needle: str) -> None:
    if needle not in text:
        raise AssertionError(f"missing desktop release contract: {needle}")


def assert_absent(text: str, needle: str) -> None:
    if needle in text:
        raise AssertionError(f"unexpected desktop release contract token: {needle}")


def workflow_step(text: str, step_name: str) -> str:
    marker = f"- name: {step_name}"
    if marker not in text:
        raise AssertionError(f"missing workflow step: {step_name}")
    after = text.split(marker, 1)[1]
    for terminator in ("\n      - name:", "\n  upload-release:"):
        if terminator in after:
            after = after.split(terminator, 1)[0]
    return after


def verify_macos_signing_contracts(text: str) -> None:
    pr_step = workflow_step(text, "Build unsigned desktop preview bundles")
    assert_contains(pr_step, "if: github.event_name == 'pull_request'")
    assert_contains(pr_step, "npm run build -- --bundles ${{ matrix.bundles }} --no-sign")
    for forbidden in (
        "secrets.",
        "APPLE_CERTIFICATE",
        "APPLE_ID",
        "APPLE_PASSWORD",
        "APPLE_TEAM_ID",
        "KEYCHAIN_PASSWORD",
    ):
        assert_absent(pr_step, forbidden)

    resolve_step = workflow_step(text, "Resolve macOS signing mode")
    assert_contains(resolve_step, "id: macos-signing")
    assert_contains(resolve_step, "if: github.event_name != 'pull_request' && runner.os == 'macOS'")
    assert_contains(resolve_step, "scripts/desktop_macos_signing.py resolve-mode")
    for var in desktop_macos_signing.ALL_APPLE_VARS:
        assert_contains(resolve_step, f"{var}: ${{{{ secrets.{var} }}}}")

    build_step = workflow_step(text, "Build macOS release bundles")
    assert_contains(build_step, "if: github.event_name != 'pull_request' && runner.os == 'macOS'")
    assert_absent(build_step, "--no-sign")
    assert_contains(build_step, "scripts/desktop_macos_signing.py build")
    assert_contains(build_step, "--bundles ${{ matrix.bundles }}")
    assert_contains(build_step, '--expected-mode "${EXPECTED_SIGNING_MODE}"')
    assert_contains(build_step, "EXPECTED_SIGNING_MODE: ${{ steps.macos-signing.outputs.signing-mode }}")
    for var in desktop_macos_signing.ALL_APPLE_VARS:
        assert_contains(build_step, f"{var}: ${{{{ secrets.{var} }}}}")

    notarize_step = workflow_step(text, "Notarize macOS disk image")
    assert_contains(
        notarize_step,
        "if: github.event_name != 'pull_request' && runner.os == 'macOS' "
        "&& steps.macos-signing.outputs.signing-mode == 'developer_id'",
    )
    assert_contains(notarize_step, "scripts/desktop_macos_signing.py notarize-dmg")
    for var in desktop_macos_signing.NOTARIZATION_APPLE_SECRETS:
        assert_contains(notarize_step, f"{var}: ${{{{ secrets.{var} }}}}")

    verify_step = workflow_step(text, "Verify macOS desktop bundles")
    assert_contains(verify_step, "if: github.event_name != 'pull_request' && runner.os == 'macOS'")
    assert_contains(verify_step, "SIGNING_MODE: ${{ steps.macos-signing.outputs.signing-mode }}")
    assert_contains(verify_step, "scripts/desktop_macos_signing.py verify")
    assert_contains(verify_step, '--signing-mode "${SIGNING_MODE}"')
    assert_contains(verify_step, '--app "${app_path}"')
    assert_contains(verify_step, '--dmg "${dmg_path}"')

    assert (
        text.index("- name: Resolve macOS signing mode")
        < text.index("- name: Build macOS release bundles")
        < text.index("- name: Notarize macOS disk image")
        < text.index("- name: Verify macOS desktop bundles")
        < text.index("- name: Collect macOS desktop bundles")
    )

    # Developer ID contract: all 5 core secrets -> developer_id, no ad-hoc identity,
    # ephemeral KEYCHAIN_PASSWORD fallback, notarization + Gatekeeper + stapler checks.
    dev_env = {
        "APPLE_CERTIFICATE": "base64-p12",
        "APPLE_CERTIFICATE_PASSWORD": "p12-password",
        "APPLE_ID": "release@example.com",
        "APPLE_PASSWORD": "app-specific-password",
        "APPLE_TEAM_ID": "TEAM123456",
        "APPLE_SIGNING_IDENTITY": "",
    }
    dev_res = desktop_macos_signing.resolve_apple_signing(dev_env)
    assert dev_res.signing_mode == "developer_id"
    assert dev_res.generated_keychain_password is True
    assert dev_res.keychain_password
    assert dev_res.signing_identity is None
    dev_cfg = json.loads(desktop_macos_signing.tauri_build_config("0.5.4", "developer_id"))
    assert dev_cfg == {"version": "0.5.4"}
    sanitized_dev_env = desktop_macos_signing.prepare_build_env(dev_env, dev_res)
    assert sanitized_dev_env["KEYCHAIN_PASSWORD"] == dev_res.keychain_password
    assert "APPLE_SIGNING_IDENTITY" not in sanitized_dev_env

    app_path = Path("LoopX.app")
    dmg_path = Path("LoopX_0.5.4_aarch64.dmg")
    dev_checks = desktop_macos_signing.verification_commands("developer_id", app_path, dmg_path)
    dev_joined = "\n".join(" ".join(c.argv) for c in dev_checks)
    for required in (
        "codesign --verify --deep --strict --verbose=2 LoopX.app",
        "codesign --display --verbose=4 LoopX.app",
        "spctl --assess --type execute --verbose=4 LoopX.app",
        "xcrun stapler validate LoopX.app",
        "codesign --verify --verbose=2 LoopX_0.5.4_aarch64.dmg",
        "spctl --assess --type open --context context:primary-signature --verbose=4 LoopX_0.5.4_aarch64.dmg",
        "xcrun stapler validate LoopX_0.5.4_aarch64.dmg",
        "hdiutil verify LoopX_0.5.4_aarch64.dmg",
    ):
        assert_contains(dev_joined, required)
    assert any(
        c.required_line_pattern == desktop_macos_signing.DEVELOPER_ID_AUTHORITY_PATTERN
        for c in dev_checks
    )

    notarize_cmds = desktop_macos_signing.notarize_dmg_commands(
        dmg_path, dev_env["APPLE_ID"], dev_env["APPLE_PASSWORD"], dev_env["APPLE_TEAM_ID"]
    )
    assert notarize_cmds[0][:4] == ("xcrun", "notarytool", "submit", str(dmg_path))
    assert "--wait" in notarize_cmds[0]
    assert notarize_cmds[1] == ("xcrun", "stapler", "staple", str(dmg_path))

    # Ad-hoc contract: all 5 core secrets absent -> adhoc with signingIdentity="-".
    adhoc_res = desktop_macos_signing.resolve_apple_signing({})
    assert adhoc_res.signing_mode == "adhoc"
    adhoc_cfg = json.loads(desktop_macos_signing.tauri_build_config("0.5.4", "adhoc"))
    assert adhoc_cfg == {"version": "0.5.4", "bundle": {"macOS": {"signingIdentity": "-"}}}
    adhoc_checks = desktop_macos_signing.verification_commands("adhoc", app_path, dmg_path)
    adhoc_joined = "\n".join(" ".join(c.argv) for c in adhoc_checks)
    assert_contains(adhoc_joined, "codesign --verify --deep --strict --verbose=2 LoopX.app")
    assert_contains(adhoc_joined, "codesign --display --verbose=4 LoopX.app")
    assert_contains(adhoc_joined, "hdiutil verify LoopX_0.5.4_aarch64.dmg")
    assert_absent(adhoc_joined, "notarytool")
    assert_absent(adhoc_joined, "stapler")
    assert_absent(adhoc_joined, "spctl")
    assert any(
        c.required_line_pattern == desktop_macos_signing.ADHOC_SIGNATURE_PATTERN
        for c in adhoc_checks
    )

    # Partial configuration (1-4 of 5 core secrets) must fail closed.
    partial_env = {
        "APPLE_CERTIFICATE": "base64-p12",
        "APPLE_TEAM_ID": "TEAM123456",
    }
    proc = subprocess.run(
        [sys.executable, str(SIGNING_SCRIPT), "resolve-mode"],
        env={"PATH": os.environ.get("PATH", ""), **partial_env},
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert proc.returncode == 1
    assert "::error title=Incomplete Apple signing configuration::" in proc.stderr


def main() -> int:
    text = WORKFLOW.read_text(encoding="utf-8")

    for required in [
        "github.event_name == 'pull_request'",
        "github.event_name != 'pull_request'",
        "- name: Validate desktop release workflow\n        if: github.event_name == 'pull_request'",
        'npm run build -- --bundles ${{ matrix.bundles }} --no-sign',
        'RELEASE_TAG: ${{ steps.identity.outputs.release-tag }}',
        'release_version="${RELEASE_TAG#v}"',
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

    verify_macos_signing_contracts(text)

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
