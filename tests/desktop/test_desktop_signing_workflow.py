"""Unit and workflow-structure tests for macOS desktop signing and notarization."""

from __future__ import annotations

import importlib
import importlib.util
from itertools import combinations
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile
from types import ModuleType
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
SIGNING_SCRIPT = REPO_ROOT / "scripts" / "desktop_macos_signing.py"
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "desktop-release-artifacts.yml"
UPDATER_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "desktop-updater.yml"


def load_signing_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("desktop_macos_signing", SIGNING_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


signing = load_signing_module()


def full_core_env(**overrides: str) -> dict[str, str]:
    env = {
        "APPLE_CERTIFICATE": "cGtjczEyLWRhdGE=",
        "APPLE_CERTIFICATE_PASSWORD": "secret-p12-password",
        "APPLE_ID": "ci-signer@example.com",
        "APPLE_PASSWORD": "abcd-efgh-ijkl-mnop",
        "APPLE_TEAM_ID": "ABCDE12345",
    }
    env.update(overrides)
    return env


def extract_job_block(workflow_text: str, job_name: str) -> str:
    marker = f"\n  {job_name}:\n"
    if marker not in workflow_text:
        raise AssertionError(f"missing job {job_name!r}")
    rest = workflow_text.split(marker, 1)[1]
    lines = rest.splitlines()
    collected: list[str] = []
    for line in lines:
        if line and not line.startswith("    ") and line.startswith("  ") and line.rstrip().endswith(":"):
            break
        collected.append(line)
    return "\n".join(collected)


def extract_steps(job_block: str) -> list[dict[str, str]]:
    lines = job_block.splitlines()
    steps: list[dict[str, str]] = []
    current: list[str] | None = None
    in_steps = False
    for line in lines:
        if line == "    steps:":
            in_steps = True
            continue
        if not in_steps:
            continue
        if line.startswith("      - "):
            if current is not None:
                steps.append(_parse_step_block(current))
            current = [line]
        elif current is not None:
            current.append(line)
    if current is not None:
        steps.append(_parse_step_block(current))
    return steps


def _parse_step_block(lines: list[str]) -> dict[str, str]:
    body = "\n".join(lines)
    name = ""
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("- name:"):
            name = stripped.split(":", 1)[1].strip()
            break
        if stripped.startswith("name:"):
            name = stripped.split(":", 1)[1].strip()
            break
    return {"name": name, "body": body}


class MacOSSigningHelperTests(unittest.TestCase):
    def test_all_core_secrets_absent_resolves_to_adhoc(self) -> None:
        for env in ({}, {name: "   \n" for name in signing.CORE_APPLE_SECRETS}):
            with self.subTest(env=env):
                resolved = signing.resolve_apple_signing(env)
                self.assertEqual(resolved.signing_mode, "adhoc")
                self.assertEqual(resolved.present_core_secrets, ())
                self.assertEqual(resolved.missing_core_secrets, signing.CORE_APPLE_SECRETS)
                self.assertIsNone(resolved.keychain_password)
                self.assertFalse(resolved.generated_keychain_password)
                self.assertIsNone(resolved.signing_identity)

    def test_all_core_secrets_present_resolves_to_developer_id_with_ephemeral_keychain_password(
        self,
    ) -> None:
        resolved = signing.resolve_apple_signing(full_core_env())
        self.assertEqual(resolved.signing_mode, "developer_id")
        self.assertEqual(resolved.present_core_secrets, signing.CORE_APPLE_SECRETS)
        self.assertEqual(resolved.missing_core_secrets, ())
        self.assertTrue(resolved.generated_keychain_password)
        self.assertIsNotNone(resolved.keychain_password)
        self.assertGreaterEqual(len(resolved.keychain_password), 32)
        self.assertIsNone(resolved.signing_identity)

        second = signing.resolve_apple_signing(full_core_env(KEYCHAIN_PASSWORD="   "))
        self.assertTrue(second.generated_keychain_password)
        self.assertNotEqual(resolved.keychain_password, second.keychain_password)

    def test_explicit_optional_keychain_password_and_signing_identity_are_preserved(
        self,
    ) -> None:
        resolved = signing.resolve_apple_signing(
            full_core_env(
                KEYCHAIN_PASSWORD="explicit-keychain-pass",
                APPLE_SIGNING_IDENTITY="  Developer ID Application: LoopX (ABCDE12345)  ",
            )
        )
        self.assertEqual(resolved.signing_mode, "developer_id")
        self.assertFalse(resolved.generated_keychain_password)
        self.assertEqual(resolved.keychain_password, "explicit-keychain-pass")
        self.assertEqual(
            resolved.signing_identity,
            "Developer ID Application: LoopX (ABCDE12345)",
        )

    def test_partial_core_secrets_fail_fast_without_leaking_values(self) -> None:
        base = full_core_env()
        for count in range(1, len(signing.CORE_APPLE_SECRETS)):
            for present_names in combinations(signing.CORE_APPLE_SECRETS, count):
                env = {name: base[name] for name in present_names}
                with self.subTest(present=present_names):
                    with self.assertRaises(signing.AppleSigningConfigError) as ctx:
                        signing.resolve_apple_signing(env)
                    message = str(ctx.exception)
                    for name in present_names:
                        self.assertIn(name, message)
                    for secret_val in base.values():
                        self.assertNotIn(secret_val, message)

        whitespace_env = full_core_env(APPLE_TEAM_ID="   ")
        with self.assertRaises(signing.AppleSigningConfigError) as ctx:
            signing.resolve_apple_signing(whitespace_env)
        self.assertIn("missing=[APPLE_TEAM_ID]", str(ctx.exception))

    def test_tauri_build_config_for_both_modes_and_updater(self) -> None:
        self.assertEqual(
            json.loads(signing.tauri_build_config("2.4.0", "developer_id")),
            {"version": "2.4.0"},
        )
        self.assertEqual(
            json.loads(
                signing.tauri_build_config(
                    "0.0.0-main.123.1",
                    "developer_id",
                    create_updater_artifacts=True,
                )
            ),
            {"version": "0.0.0-main.123.1", "bundle": {"createUpdaterArtifacts": True}},
        )
        self.assertEqual(
            json.loads(signing.tauri_build_config("2.4.0", "adhoc")),
            {"version": "2.4.0", "bundle": {"macOS": {"signingIdentity": "-"}}},
        )
        self.assertEqual(
            json.loads(
                signing.tauri_build_config(
                    "2.4.0", "adhoc", create_updater_artifacts=True
                )
            ),
            {
                "version": "2.4.0",
                "bundle": {"createUpdaterArtifacts": True, "macOS": {"signingIdentity": "-"}},
            },
        )
        for bad in ("v2.4.0", "preview-1234567890ab", "2.4.0;echo"):
            with self.assertRaises(signing.AppleSigningConfigError):
                signing.tauri_build_config(bad, "adhoc")

    def test_prepare_build_env_sanitizes_adhoc_and_empty_identity(self) -> None:
        adhoc_res = signing.resolve_apple_signing({})
        adhoc_env = signing.prepare_build_env(
            {"PATH": "/usr/bin", "APPLE_SIGNING_IDENTITY": "", "KEYCHAIN_PASSWORD": "old"},
            adhoc_res,
        )
        for var in signing.ALL_APPLE_VARS:
            self.assertNotIn(var, adhoc_env)

        dev_env_input = full_core_env(APPLE_SIGNING_IDENTITY="", PATH="/usr/bin")
        dev_res = signing.resolve_apple_signing(dev_env_input)
        dev_env = signing.prepare_build_env(dev_env_input, dev_res)
        self.assertEqual(dev_env["KEYCHAIN_PASSWORD"], dev_res.keychain_password)
        self.assertNotIn("APPLE_SIGNING_IDENTITY", dev_env)
        for var in signing.CORE_APPLE_SECRETS:
            self.assertEqual(dev_env[var], dev_env_input[var])


class MacOSSigningCLITests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()

    def _write_stub(self, name: str, script: str) -> Path:
        path = self.bin_dir / name
        path.write_text(f"#!/bin/sh\nset -eu\n{script}\n", encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    def _run_cli(self, args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        merged = {
            "PATH": f"{self.bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
            **env,
        }
        return subprocess.run(
            [sys.executable, str(SIGNING_SCRIPT), *args],
            env=merged,
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )

    def test_resolve_mode_cli_outputs_and_fails_on_partial(self) -> None:
        github_output = self.root / "github_output.txt"
        res_adhoc = self._run_cli(
            ["resolve-mode", "--github-output", str(github_output)],
            {},
        )
        self.assertEqual(res_adhoc.returncode, 0, res_adhoc.stderr)
        self.assertEqual(github_output.read_text(encoding="utf-8"), "signing-mode=adhoc\n")

        github_output.unlink()
        res_dev = self._run_cli(
            ["resolve-mode", "--github-output", str(github_output)],
            full_core_env(),
        )
        self.assertEqual(res_dev.returncode, 0, res_dev.stderr)
        self.assertEqual(
            github_output.read_text(encoding="utf-8"),
            "signing-mode=developer_id\n",
        )

        res_partial = self._run_cli(
            ["resolve-mode", "--github-output", str(github_output)],
            {"APPLE_ID": "ci@example.com", "APPLE_TEAM_ID": "TEAM123456"},
        )
        self.assertEqual(res_partial.returncode, 1)
        self.assertIn("::error title=Incomplete Apple signing configuration::", res_partial.stderr)
        self.assertNotIn("ci@example.com", res_partial.stderr)

    def test_build_cli_masks_generated_keychain_password_and_invokes_npm(self) -> None:
        captured = self.root / "npm_captured.json"
        npm_stub = self.bin_dir / "npm"
        npm_stub.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            "with open(os.environ['LOOPX_TEST_CAPTURED'], 'w', encoding='utf-8') as fh:\n"
            "    json.dump({\n"
            "        'argv': sys.argv[1:],\n"
            "        'keychain_password': os.environ.get('KEYCHAIN_PASSWORD'),\n"
            "        'apple_signing_identity': os.environ.get('APPLE_SIGNING_IDENTITY'),\n"
            "        'apple_id': os.environ.get('APPLE_ID'),\n"
            "    }, fh)\n",
            encoding="utf-8",
        )
        npm_stub.chmod(npm_stub.stat().st_mode | stat.S_IXUSR)
        res = self._run_cli(
            [
                "build",
                "--version",
                "2.4.0",
                "--bundles",
                "app",
                "--create-updater-artifacts",
                "--expected-mode",
                "developer_id",
            ],
            {
                **full_core_env(APPLE_SIGNING_IDENTITY=""),
                "GITHUB_ACTIONS": "true",
                "LOOPX_TEST_CAPTURED": str(captured),
            },
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        payload = json.loads(captured.read_text(encoding="utf-8"))
        self.assertEqual(
            payload["argv"],
            [
                "run",
                "build",
                "--",
                "--bundles",
                "app",
                "--config",
                '{"version":"2.4.0","bundle":{"createUpdaterArtifacts":true}}',
            ],
        )
        self.assertTrue(payload["keychain_password"])
        self.assertIn(f"::add-mask::{payload['keychain_password']}", res.stdout)
        self.assertIsNone(payload["apple_signing_identity"])

        res_adhoc = self._run_cli(
            ["build", "--version", "2.4.0", "--bundles", "dmg", "app", "--expected-mode", "adhoc"],
            {"APPLE_SIGNING_IDENTITY": "", "LOOPX_TEST_CAPTURED": str(captured)},
        )
        self.assertEqual(res_adhoc.returncode, 0, res_adhoc.stderr)
        adhoc_payload = json.loads(captured.read_text(encoding="utf-8"))
        self.assertEqual(
            adhoc_payload["argv"],
            [
                "run",
                "build",
                "--",
                "--bundles",
                "dmg",
                "app",
                "--config",
                '{"version":"2.4.0","bundle":{"macOS":{"signingIdentity":"-"}}}',
            ],
        )
        self.assertIsNone(adhoc_payload["keychain_password"])

    def test_notarize_dmg_cli_runs_notarytool_and_stapler_and_redacts_errors(self) -> None:
        log_file = self.root / "xcrun.log"
        dmg = self.root / "LoopX_2.4.0_aarch64.dmg"
        dmg.write_bytes(b"dmg")
        self._write_stub(
            "xcrun",
            f'printf "%s\\n" "$*" >> {str(log_file)!r}',
        )
        res = self._run_cli(
            ["notarize-dmg", "--dmg", str(dmg)],
            {
                "APPLE_ID": "ci@example.com",
                "APPLE_PASSWORD": "super-secret-password",
                "APPLE_TEAM_ID": "TEAM123456",
            },
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        lines = log_file.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn(f"notarytool submit {dmg}", lines[0])
        self.assertIn("--wait", lines[0])
        self.assertEqual(lines[1], f"stapler staple {dmg}")

        self._write_stub("xcrun", "exit 1")
        failed = self._run_cli(
            ["notarize-dmg", "--dmg", str(dmg)],
            {
                "APPLE_ID": "ci@example.com",
                "APPLE_PASSWORD": "super-secret-password",
                "APPLE_TEAM_ID": "TEAM123456",
            },
        )
        self.assertEqual(failed.returncode, 1)
        self.assertNotIn("super-secret-password", failed.stderr)

    def test_verify_cli_and_updater_tarball_contracts(self) -> None:
        app = self.root / "LoopX.app"
        (app / "Contents").mkdir(parents=True)
        dmg = self.root / "LoopX_2.4.0_aarch64.dmg"
        dmg.write_bytes(b"dmg")
        tar_gz = self.root / "LoopX.app.tar.gz"
        Path(f"{tar_gz}.sig").write_text("sig", encoding="utf-8")

        def build_updater_tar(include_ticket: bool) -> None:
            with tarfile.open(tar_gz, "w:gz") as archive:
                for rel in (
                    ["LoopX.app/Contents/Info.plist", "LoopX.app/Contents/_CodeSignature/CodeResources"]
                    + (["LoopX.app/Contents/CodeResources"] if include_ticket else [])
                ):
                    file_path = self.root / "src_tar" / rel
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                    file_path.write_text("stub", encoding="utf-8")
                    archive.add(file_path, arcname=rel)

        log_file = self.root / "verify.log"
        self._write_stub(
            "codesign",
            f'printf "codesign %s\\n" "$*" >> {str(log_file)!r}\n'
            'if [ "$1" = "--display" ]; then\n'
            '  echo "Authority=Developer ID Application: LoopX (ABCDE12345)"\n'
            "fi",
        )
        self._write_stub("spctl", f'printf "spctl %s\\n" "$*" >> {str(log_file)!r}')
        self._write_stub("xcrun", f'printf "xcrun %s\\n" "$*" >> {str(log_file)!r}')
        self._write_stub("hdiutil", f'printf "hdiutil %s\\n" "$*" >> {str(log_file)!r}')

        build_updater_tar(include_ticket=False)
        missing_ticket = self._run_cli(
            [
                "verify",
                "--signing-mode",
                "developer_id",
                "--app",
                str(app),
                "--dmg",
                str(dmg),
                "--updater-tar-gz",
                str(tar_gz),
            ],
            {},
        )
        self.assertEqual(missing_ticket.returncode, 1)
        self.assertIn("LoopX.app/Contents/CodeResources", missing_ticket.stderr)

        log_file.unlink()
        build_updater_tar(include_ticket=True)
        dev_ok = self._run_cli(
            [
                "verify",
                "--signing-mode",
                "developer_id",
                "--app",
                str(app),
                "--dmg",
                str(dmg),
                "--updater-tar-gz",
                str(tar_gz),
            ],
            {},
        )
        self.assertEqual(dev_ok.returncode, 0, dev_ok.stderr)
        dev_lines = log_file.read_text(encoding="utf-8").splitlines()
        self.assertEqual(
            dev_lines,
            [
                f"codesign --verify --deep --strict --verbose=2 {app}",
                f"codesign --display --verbose=4 {app}",
                f"spctl --assess --type execute --verbose=4 {app}",
                f"xcrun stapler validate {app}",
                f"codesign --verify --verbose=2 {dmg}",
                f"spctl --assess --type open --context context:primary-signature --verbose=4 {dmg}",
                f"xcrun stapler validate {dmg}",
                f"hdiutil verify {dmg}",
            ],
        )

        # Ad-hoc verification rejects Developer ID display output and accepts Signature=adhoc.
        bad_adhoc = self._run_cli(
            ["verify", "--signing-mode", "adhoc", "--app", str(app)],
            {},
        )
        self.assertEqual(bad_adhoc.returncode, 1)

        log_file.unlink()
        self._write_stub(
            "codesign",
            f'printf "codesign %s\\n" "$*" >> {str(log_file)!r}\n'
            'if [ "$1" = "--display" ]; then\n'
            '  echo "Signature=adhoc"\n'
            "fi",
        )
        adhoc_ok = self._run_cli(
            ["verify", "--signing-mode", "adhoc", "--app", str(app), "--dmg", str(dmg)],
            {},
        )
        self.assertEqual(adhoc_ok.returncode, 0, adhoc_ok.stderr)
        self.assertEqual(
            log_file.read_text(encoding="utf-8").splitlines(),
            [
                f"codesign --verify --deep --strict --verbose=2 {app}",
                f"codesign --display --verbose=4 {app}",
                f"hdiutil verify {dmg}",
            ],
        )


class WorkflowStructureTests(unittest.TestCase):
    def test_release_workflow_yaml_structure_and_signing_order(self) -> None:
        text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
        try:
            yaml = importlib.import_module("yaml")
        except ImportError:
            yaml = None
        if yaml is not None:
            parsed = yaml.safe_load(text)
            self.assertIn("build", parsed["jobs"])
            self.assertIn("upload-release", parsed["jobs"])

        build_job = extract_job_block(text, "build")
        steps = extract_steps(build_job)
        names = [step["name"] for step in steps]
        expected_order = [
            "Resolve macOS signing mode",
            "Build unsigned desktop preview bundles",
            "Build macOS release bundles",
            "Notarize macOS disk image",
            "Verify macOS desktop bundles",
            "Collect macOS desktop bundles",
        ]
        positions = [names.index(name) for name in expected_order]
        self.assertEqual(positions, sorted(positions))

        by_name = {step["name"]: step["body"] for step in steps}
        self.assertIn("if: github.event_name == 'pull_request'", by_name["Build unsigned desktop preview bundles"])
        self.assertIn("--no-sign", by_name["Build unsigned desktop preview bundles"])
        self.assertNotIn("secrets.", by_name["Build unsigned desktop preview bundles"])

        self.assertIn("id: macos-signing", by_name["Resolve macOS signing mode"])
        self.assertIn("scripts/desktop_macos_signing.py resolve-mode", by_name["Resolve macOS signing mode"])

        self.assertNotIn("--no-sign", by_name["Build macOS release bundles"])
        self.assertIn("scripts/desktop_macos_signing.py build", by_name["Build macOS release bundles"])
        self.assertIn(
            "steps.macos-signing.outputs.signing-mode == 'developer_id'",
            by_name["Notarize macOS disk image"],
        )
        self.assertIn("scripts/desktop_macos_signing.py notarize-dmg", by_name["Notarize macOS disk image"])
        self.assertIn("scripts/desktop_macos_signing.py verify", by_name["Verify macOS desktop bundles"])
        for secret in signing.CORE_APPLE_SECRETS:
            self.assertIn(f"secrets.{secret}", by_name["Resolve macOS signing mode"])
            self.assertIn(f"secrets.{secret}", by_name["Build macOS release bundles"])

    def test_updater_workflow_yaml_structure_and_macos_verification(self) -> None:
        text = UPDATER_WORKFLOW.read_text(encoding="utf-8")
        try:
            yaml = importlib.import_module("yaml")
        except ImportError:
            yaml = None
        if yaml is not None:
            parsed = yaml.safe_load(text)
            self.assertIn("build-macos", parsed["jobs"])
            self.assertIn("build-windows", parsed["jobs"])

        macos_job = extract_job_block(text, "build-macos")
        steps = extract_steps(macos_job)
        names = [step["name"] for step in steps]
        expected_order = [
            "Resolve macOS signing mode",
            "Build signed macOS App and exact bundled runtime",
            "Verify macOS updater bundle",
            "Collect native updater artifacts",
        ]
        positions = [names.index(name) for name in expected_order]
        self.assertEqual(positions, sorted(positions))

        by_name = {step["name"]: step["body"] for step in steps}
        self.assertIn("id: macos-signing", by_name["Resolve macOS signing mode"])
        self.assertIn("scripts/desktop_macos_signing.py resolve-mode", by_name["Resolve macOS signing mode"])
        self.assertIn("scripts/desktop_macos_signing.py build", by_name["Build signed macOS App and exact bundled runtime"])
        self.assertIn("--create-updater-artifacts", by_name["Build signed macOS App and exact bundled runtime"])
        self.assertIn("scripts/desktop_macos_signing.py verify", by_name["Verify macOS updater bundle"])
        self.assertIn("--updater-tar-gz", by_name["Verify macOS updater bundle"])
        for secret in signing.ALL_APPLE_VARS:
            self.assertIn(f"secrets.{secret}", by_name["Resolve macOS signing mode"])
            self.assertIn(f"secrets.{secret}", by_name["Build signed macOS App and exact bundled runtime"])


if __name__ == "__main__":
    unittest.main()
