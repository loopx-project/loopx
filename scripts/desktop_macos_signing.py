#!/usr/bin/env python3
"""Shared macOS Developer ID signing, notarization, and verification helper."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
from typing import Literal, Mapping, Sequence


SigningMode = Literal["developer_id", "adhoc"]

CORE_APPLE_SECRETS: tuple[str, ...] = (
    "APPLE_CERTIFICATE",
    "APPLE_CERTIFICATE_PASSWORD",
    "APPLE_ID",
    "APPLE_PASSWORD",
    "APPLE_TEAM_ID",
)
NOTARIZATION_APPLE_SECRETS: tuple[str, ...] = (
    "APPLE_ID",
    "APPLE_PASSWORD",
    "APPLE_TEAM_ID",
)
OPTIONAL_APPLE_VARS: tuple[str, ...] = (
    "KEYCHAIN_PASSWORD",
    "APPLE_SIGNING_IDENTITY",
)
ALL_APPLE_VARS: tuple[str, ...] = CORE_APPLE_SECRETS + OPTIONAL_APPLE_VARS

SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+([+-][0-9A-Za-z.-]+)?$")
DEVELOPER_ID_AUTHORITY_PATTERN = r"^Authority=Developer ID Application:"
ADHOC_SIGNATURE_PATTERN = r"^Signature=adhoc$"


class AppleSigningConfigError(ValueError):
    """Raised when Apple signing or notarization credentials are incomplete."""


class DesktopMacOSSigningError(RuntimeError):
    """Raised when macOS signing, notarization, or verification fails."""


@dataclass(frozen=True)
class AppleSigningResolution:
    """Resolved macOS signing configuration derived from environment secrets."""

    signing_mode: SigningMode
    present_core_secrets: tuple[str, ...]
    missing_core_secrets: tuple[str, ...]
    keychain_password: str | None = None
    generated_keychain_password: bool = False
    signing_identity: str | None = None


@dataclass(frozen=True)
class VerificationCheck:
    """Single external command check for a macOS bundle or disk image."""

    argv: tuple[str, ...]
    combine_output: bool = False
    required_line_pattern: str | None = None


def resolve_apple_signing(
    env: Mapping[str, str] | None = None,
) -> AppleSigningResolution:
    """Classify Apple signing mode from the 5 core secrets plus optional vars."""

    source = os.environ if env is None else env
    present = tuple(name for name in CORE_APPLE_SECRETS if source.get(name, "").strip())
    missing = tuple(name for name in CORE_APPLE_SECRETS if not source.get(name, "").strip())

    if not present:
        return AppleSigningResolution(
            signing_mode="adhoc",
            present_core_secrets=present,
            missing_core_secrets=missing,
        )

    if missing:
        raise AppleSigningConfigError(
            "Apple Developer ID signing requires all 5 core secrets "
            f"({', '.join(CORE_APPLE_SECRETS)}); "
            f"present=[{', '.join(present)}], missing=[{', '.join(missing)}]"
        )

    raw_keychain_password = source.get("KEYCHAIN_PASSWORD", "")
    if raw_keychain_password.strip():
        keychain_password = raw_keychain_password
        generated_keychain_password = False
    else:
        keychain_password = secrets.token_urlsafe(32)
        generated_keychain_password = True

    raw_identity = source.get("APPLE_SIGNING_IDENTITY", "").strip()
    signing_identity = raw_identity if raw_identity else None

    return AppleSigningResolution(
        signing_mode="developer_id",
        present_core_secrets=present,
        missing_core_secrets=missing,
        keychain_password=keychain_password,
        generated_keychain_password=generated_keychain_password,
        signing_identity=signing_identity,
    )


def tauri_build_config(
    version: str,
    signing_mode: SigningMode,
    *,
    create_updater_artifacts: bool = False,
) -> str:
    """Build the Tauri `--config` JSON payload for the resolved signing mode."""

    if not SEMVER_RE.fullmatch(version):
        raise AppleSigningConfigError(
            f"Invalid desktop release version {version!r}; expected semantic version"
        )
    bundle: dict[str, object] = {}
    if create_updater_artifacts:
        bundle["createUpdaterArtifacts"] = True
    if signing_mode == "adhoc":
        bundle["macOS"] = {"signingIdentity": "-"}
    elif signing_mode != "developer_id":
        raise AppleSigningConfigError(f"Unsupported signing mode: {signing_mode!r}")

    payload: dict[str, object] = {"version": version}
    if bundle:
        payload["bundle"] = bundle
    return json.dumps(payload, separators=(",", ":"))


def prepare_build_env(
    base_env: Mapping[str, str],
    resolution: AppleSigningResolution,
) -> dict[str, str]:
    """Return a sanitized subprocess environment for `tauri build`."""

    env = dict(base_env)
    if resolution.signing_mode == "adhoc":
        for name in ALL_APPLE_VARS:
            env.pop(name, None)
        return env

    if not resolution.keychain_password:
        raise AppleSigningConfigError("KEYCHAIN_PASSWORD could not be resolved")
    env["KEYCHAIN_PASSWORD"] = resolution.keychain_password
    if resolution.signing_identity:
        env["APPLE_SIGNING_IDENTITY"] = resolution.signing_identity
    else:
        # Unset empty GitHub Actions secret placeholders so Tauri auto-detects
        # the Developer ID certificate imported from APPLE_CERTIFICATE.
        env.pop("APPLE_SIGNING_IDENTITY", None)
    return env


def notarize_dmg_commands(
    dmg_path: Path,
    apple_id: str,
    apple_password: str,
    apple_team_id: str,
) -> tuple[tuple[str, ...], ...]:
    """Return the `notarytool` submission and `stapler` commands for a DMG."""

    target = str(dmg_path)
    return (
        (
            "xcrun",
            "notarytool",
            "submit",
            target,
            "--apple-id",
            apple_id,
            "--password",
            apple_password,
            "--team-id",
            apple_team_id,
            "--wait",
        ),
        ("xcrun", "stapler", "staple", target),
    )


def verification_commands(
    signing_mode: SigningMode,
    app_path: Path,
    dmg_path: Path | None = None,
) -> tuple[VerificationCheck, ...]:
    """Build the ordered codesign/Gatekeeper/stapler/disk-image checks."""

    app_str = str(app_path)
    if signing_mode == "developer_id":
        checks: list[VerificationCheck] = [
            VerificationCheck(("codesign", "--verify", "--deep", "--strict", "--verbose=2", app_str)),
            VerificationCheck(
                ("codesign", "--display", "--verbose=4", app_str),
                combine_output=True,
                required_line_pattern=DEVELOPER_ID_AUTHORITY_PATTERN,
            ),
            VerificationCheck(("spctl", "--assess", "--type", "execute", "--verbose=4", app_str)),
            VerificationCheck(("xcrun", "stapler", "validate", app_str)),
        ]
        if dmg_path is not None:
            dmg_str = str(dmg_path)
            checks.extend(
                [
                    VerificationCheck(("codesign", "--verify", "--verbose=2", dmg_str)),
                    VerificationCheck(
                        (
                            "spctl",
                            "--assess",
                            "--type",
                            "open",
                            "--context",
                            "context:primary-signature",
                            "--verbose=4",
                            dmg_str,
                        )
                    ),
                    VerificationCheck(("xcrun", "stapler", "validate", dmg_str)),
                    VerificationCheck(("hdiutil", "verify", dmg_str)),
                ]
            )
        return tuple(checks)

    if signing_mode == "adhoc":
        checks = [
            VerificationCheck(("codesign", "--verify", "--deep", "--strict", "--verbose=2", app_str)),
            VerificationCheck(
                ("codesign", "--display", "--verbose=4", app_str),
                combine_output=True,
                required_line_pattern=ADHOC_SIGNATURE_PATTERN,
            ),
        ]
        if dmg_path is not None:
            checks.append(VerificationCheck(("hdiutil", "verify", str(dmg_path))))
        return tuple(checks)

    raise AppleSigningConfigError(f"Unsupported signing mode: {signing_mode!r}")


def verify_updater_tar_gz(tar_gz_path: Path, signing_mode: SigningMode) -> None:
    """Verify updater archive presence, `.sig` pair, signature seal, and ticket."""

    if not tar_gz_path.is_file() or tar_gz_path.stat().st_size <= 0:
        raise DesktopMacOSSigningError(f"Updater archive is missing or empty: {tar_gz_path}")
    sig_path = Path(f"{tar_gz_path}.sig")
    if not sig_path.is_file() or sig_path.stat().st_size <= 0:
        raise DesktopMacOSSigningError(f"Updater signature is missing or empty: {sig_path}")

    with tarfile.open(tar_gz_path, "r:gz") as archive:
        members = {member.name.lstrip("./") for member in archive.getmembers()}

    required = {
        "LoopX.app/Contents/Info.plist",
        "LoopX.app/Contents/_CodeSignature/CodeResources",
    }
    if signing_mode == "developer_id":
        required.add("LoopX.app/Contents/CodeResources")

    missing = sorted(required - members)
    if missing:
        raise DesktopMacOSSigningError(
            f"Updater archive {tar_gz_path} is missing expected bundle files for {signing_mode}: {missing}"
        )


def _resolve_single_dmg(dmg: Path | None, dmg_dir: Path | None) -> Path | None:
    if dmg is not None:
        if not dmg.is_file():
            raise DesktopMacOSSigningError(f"macOS DMG does not exist: {dmg}")
        return dmg
    if dmg_dir is None:
        return None
    matches = sorted(dmg_dir.glob("*.dmg"))
    if len(matches) != 1:
        raise DesktopMacOSSigningError(
            f"Expected exactly 1 DMG in {dmg_dir}, found {len(matches)}"
        )
    return matches[0]


def _redact_argv(argv: Sequence[str]) -> str:
    redacted: list[str] = []
    redact_next = False
    for token in argv:
        if redact_next:
            redacted.append("***")
            redact_next = False
            continue
        redacted.append(token)
        if token in {"--password"}:
            redact_next = True
    return " ".join(redacted)


def _run_checked(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    combine_output: bool = False,
) -> str:
    run_env = dict(env) if env is not None else None
    if combine_output:
        completed_text = subprocess.run(
            list(argv),
            cwd=cwd,
            env=run_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        if completed_text.stdout:
            sys.stdout.write(completed_text.stdout)
        if completed_text.returncode != 0:
            raise DesktopMacOSSigningError(
                f"Command exited {completed_text.returncode}: {_redact_argv(argv)}"
            )
        return completed_text.stdout

    completed = subprocess.run(
        list(argv),
        cwd=cwd,
        env=run_env,
        check=False,
    )
    if completed.returncode != 0:
        raise DesktopMacOSSigningError(
            f"Command exited {completed.returncode}: {_redact_argv(argv)}"
        )
    return ""


def cmd_resolve_mode(args: argparse.Namespace) -> int:
    resolution = resolve_apple_signing(os.environ)
    output_path = args.github_output or os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with Path(output_path).open("a", encoding="utf-8") as stream:
            stream.write(f"signing-mode={resolution.signing_mode}\n")
    print(f"signing_mode={resolution.signing_mode}")
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    resolution = resolve_apple_signing(os.environ)
    if args.expected_mode and resolution.signing_mode != args.expected_mode:
        raise AppleSigningConfigError(
            f"Resolved signing_mode={resolution.signing_mode!r} does not match expected {args.expected_mode!r}"
        )
    if resolution.generated_keychain_password and os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::add-mask::{resolution.keychain_password}", flush=True)

    config_json = tauri_build_config(
        args.version,
        resolution.signing_mode,
        create_updater_artifacts=args.create_updater_artifacts,
    )
    build_env = prepare_build_env(os.environ, resolution)
    npm_bin = shutil.which("npm") or "npm"
    cmd = [
        npm_bin,
        "run",
        "build",
        "--",
        "--bundles",
        *args.bundles,
        "--config",
        config_json,
    ]
    print(f"macOS build signing_mode={resolution.signing_mode} config={config_json}", flush=True)
    _run_checked(cmd, cwd=args.working_directory, env=build_env)
    return 0


def cmd_notarize_dmg(args: argparse.Namespace) -> int:
    missing = [name for name in NOTARIZATION_APPLE_SECRETS if not os.environ.get(name, "").strip()]
    if missing:
        raise AppleSigningConfigError(
            f"DMG notarization requires {', '.join(NOTARIZATION_APPLE_SECRETS)}; missing={missing}"
        )
    dmg_path = _resolve_single_dmg(args.dmg, args.dmg_dir)
    if dmg_path is None:
        raise AppleSigningConfigError("notarize-dmg requires --dmg or --dmg-dir")

    commands = notarize_dmg_commands(
        dmg_path,
        os.environ["APPLE_ID"],
        os.environ["APPLE_PASSWORD"],
        os.environ["APPLE_TEAM_ID"],
    )
    for argv in commands:
        _run_checked(argv)
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    if not args.app.is_dir():
        raise DesktopMacOSSigningError(f"macOS app bundle does not exist: {args.app}")
    dmg_path = _resolve_single_dmg(args.dmg, args.dmg_dir)
    checks = verification_commands(args.signing_mode, args.app, dmg_path)
    for check in checks:
        output = _run_checked(check.argv, combine_output=check.combine_output)
        if check.required_line_pattern is not None:
            pattern = re.compile(check.required_line_pattern, re.MULTILINE)
            if not pattern.search(output):
                raise DesktopMacOSSigningError(
                    f"Output of {_redact_argv(check.argv)} did not match {check.required_line_pattern!r}"
                )
    if args.updater_tar_gz is not None:
        verify_updater_tar_gz(args.updater_tar_gz, args.signing_mode)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    resolve_cmd = subparsers.add_parser("resolve-mode")
    resolve_cmd.add_argument("--github-output", type=Path, default=None)

    build_cmd = subparsers.add_parser("build")
    build_cmd.add_argument("--version", required=True)
    build_cmd.add_argument("--bundles", nargs="+", required=True)
    build_cmd.add_argument("--create-updater-artifacts", action="store_true")
    build_cmd.add_argument(
        "--expected-mode",
        choices=("developer_id", "adhoc"),
        default=None,
    )
    build_cmd.add_argument("--working-directory", type=Path, default=None)

    notarize_cmd = subparsers.add_parser("notarize-dmg")
    notarize_cmd.add_argument("--dmg", type=Path, default=None)
    notarize_cmd.add_argument("--dmg-dir", type=Path, default=None)

    verify_cmd = subparsers.add_parser("verify")
    verify_cmd.add_argument(
        "--signing-mode",
        choices=("developer_id", "adhoc"),
        required=True,
    )
    verify_cmd.add_argument("--app", type=Path, required=True)
    verify_cmd.add_argument("--dmg", type=Path, default=None)
    verify_cmd.add_argument("--dmg-dir", type=Path, default=None)
    verify_cmd.add_argument("--updater-tar-gz", type=Path, default=None)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "resolve-mode":
            return cmd_resolve_mode(args)
        if args.command == "build":
            return cmd_build(args)
        if args.command == "notarize-dmg":
            return cmd_notarize_dmg(args)
        if args.command == "verify":
            return cmd_verify(args)
    except AppleSigningConfigError as exc:
        print(f"::error title=Incomplete Apple signing configuration::{exc}", file=sys.stderr)
        return 1
    except DesktopMacOSSigningError as exc:
        print(f"::error title=macOS signing or verification failed::{exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
