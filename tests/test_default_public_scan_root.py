from __future__ import annotations

from pathlib import Path

import pytest

from loopx import paths
from loopx.cli import build_parser


DEFAULT_PUBLIC_SCAN_COMMANDS = (
    pytest.param(["serve-status"], id="serve-status"),
    pytest.param(["chat"], id="chat"),
    pytest.param(["dashboard"], id="dashboard"),
)


def _simulate_installed_package(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[Path, Path]:
    site_packages = tmp_path / "venv" / "lib" / "python3.12" / "site-packages"
    package_root = site_packages / "loopx"
    monkeypatch.setattr(paths, "__file__", str(package_root / "paths.py"))
    return site_packages, package_root


def test_default_public_scan_root_is_the_installed_loopx_package(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    site_packages, package_root = _simulate_installed_package(monkeypatch, tmp_path)

    resolved = Path(paths.default_public_scan_root())

    assert resolved == package_root
    assert resolved != site_packages


@pytest.mark.parametrize("argv", DEFAULT_PUBLIC_SCAN_COMMANDS)
def test_installed_command_defaults_never_escape_the_loopx_package(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    argv: list[str],
) -> None:
    site_packages, package_root = _simulate_installed_package(monkeypatch, tmp_path)
    parser = build_parser()

    default_args = parser.parse_args(argv)

    assert Path(default_args.scan_root) == package_root
    assert Path(default_args.scan_root) != site_packages

    explicit_root = tmp_path / "explicit-root"
    explicit_path = tmp_path / "explicit-file.md"
    explicit_args = parser.parse_args(
        [
            *argv,
            "--scan-root",
            str(explicit_root),
            "--scan-path",
            str(explicit_path),
        ]
    )

    assert explicit_args.scan_root == str(explicit_root)
    assert explicit_args.scan_path == [str(explicit_path)]
