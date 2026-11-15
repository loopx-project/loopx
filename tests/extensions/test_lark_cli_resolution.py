from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from loopx.contract import scan_public_boundary
from loopx.extensions.lark.cli_resolution import (
    LarkCliResolution,
    build_lark_command_runner,
    resolve_lark_cli,
    run_lark_command_with_dispatch_guard,
)


def _executable(path: Path, *, version: str = "1.2.3") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then\n"
        f"  printf '%s\\n' 'lark-cli {version}'\n"
        "fi\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def test_resolution_precedence_is_explicit_then_target_then_path(tmp_path: Path) -> None:
    home = tmp_path / "fake-home"
    path_bin = tmp_path / "path-bin"
    explicit = _executable(tmp_path / "explicit" / "lark-cli", version="3.0.0")
    target = _executable(tmp_path / "target" / "lark-cli", version="2.0.0")
    _executable(path_bin / "lark-cli", version="1.0.0")

    resolved = resolve_lark_cli(
        explicit=str(explicit),
        target_cli_bin=str(target),
        environ={"PATH": str(path_bin)},
        home=home,
        system_dirs=(),
    )

    assert resolved.command == str(explicit)
    assert resolved.public_snapshot() == {
        "available": True,
        "source": "explicit",
        "version": "3.0.0",
        "error_code": None,
    }

    target_resolved = resolve_lark_cli(
        target_cli_bin=str(target),
        environ={"PATH": str(path_bin)},
        home=home,
        system_dirs=(),
    )
    assert target_resolved.command == str(target)
    assert target_resolved.source == "target"


def test_invalid_explicit_path_fails_closed_without_falling_back(tmp_path: Path) -> None:
    path_bin = tmp_path / "path-bin"
    _executable(path_bin / "lark-cli")
    unavailable = tmp_path / "explicit" / "lark-cli"
    unavailable.parent.mkdir(parents=True)
    unavailable.write_text("not executable\n", encoding="utf-8")

    resolved = resolve_lark_cli(
        explicit=str(unavailable),
        environ={"PATH": str(path_bin)},
        home=tmp_path / "home",
        system_dirs=(),
    )

    assert resolved.available is False
    assert resolved.source == "explicit"
    assert resolved.error_code == "lark_cli_not_executable"
    assert str(tmp_path) not in str(resolved.public_snapshot())


def test_nvm_discovery_selects_highest_semantic_version(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _executable(home / ".nvm/versions/node/v20.19.9/bin/lark-cli", version="1.0.0")
    selected = _executable(
        home / ".nvm/versions/node/v22.12.10/bin/lark-cli",
        version="1.4.0",
    )
    _executable(home / ".nvm/versions/node/v22.9.99/bin/lark-cli", version="1.3.0")
    _executable(home / ".nvm/versions/node/not-a-version/bin/lark-cli", version="9.0.0")

    resolved = resolve_lark_cli(
        environ={"PATH": ""},
        home=home,
        system_dirs=(),
    )

    assert resolved.command == str(selected)
    assert resolved.source == "nvm"
    assert resolved.version == "1.4.0"


def test_nvm_bin_precedes_home_scan_and_user_local(tmp_path: Path) -> None:
    home = tmp_path / "home"
    nvm_bin = tmp_path / "active-nvm" / "bin"
    selected = _executable(nvm_bin / "lark-cli", version="2.1.0")
    _executable(home / ".nvm/versions/node/v99.0.0/bin/lark-cli", version="9.0.0")
    _executable(home / ".local/bin/lark-cli", version="8.0.0")

    resolved = resolve_lark_cli(
        environ={"PATH": "", "NVM_BIN": str(nvm_bin)},
        home=home,
        system_dirs=(),
    )

    assert resolved.command == str(selected)
    assert resolved.source == "nvm"


def test_missing_cli_has_safe_structured_diagnostic(tmp_path: Path) -> None:
    resolved = resolve_lark_cli(
        environ={"PATH": ""},
        home=tmp_path / "private-home",
        system_dirs=(),
    )

    assert resolved == LarkCliResolution(
        command=None,
        available=False,
        source="missing",
        version=None,
        error_code="lark_cli_not_installed",
    )
    assert str(tmp_path) not in str(resolved.public_snapshot())


def test_runner_injects_cli_parent_into_child_path(tmp_path: Path) -> None:
    cli = _executable(tmp_path / "node-runtime" / "bin" / "lark-cli")
    resolution = LarkCliResolution(
        command=str(cli),
        available=True,
        source="nvm",
        version="1.2.3",
        error_code=None,
    )
    calls: list[dict[str, Any]] = []

    def fake_run(args: list[str], **kwargs: Any) -> Any:
        calls.append({"args": args, **kwargs})
        return type("Completed", (), {"returncode": 0, "stdout": "{}", "stderr": ""})()

    runner = build_lark_command_runner(
        resolution,
        environ={"PATH": "/usr/bin:/bin"},
        subprocess_run=fake_run,
    )
    result = runner([str(cli), "profile", "list"], None, 30)

    assert result["returncode"] == 0
    assert calls[0]["env"]["PATH"].split(os.pathsep) == [str(cli.parent), "/usr/bin", "/bin"]


def test_runner_injects_target_command_parent_ahead_of_global_resolution(
    tmp_path: Path,
) -> None:
    global_cli = _executable(tmp_path / "global-node" / "bin" / "lark-cli")
    target_cli = _executable(tmp_path / "target-node" / "bin" / "lark-cli")
    resolution = LarkCliResolution(
        command=str(global_cli),
        available=True,
        source="nvm",
        version="1.2.3",
        error_code=None,
    )
    calls: list[dict[str, Any]] = []

    def fake_run(args: list[str], **kwargs: Any) -> Any:
        calls.append({"args": args, **kwargs})
        return type("Completed", (), {"returncode": 0, "stdout": "{}", "stderr": ""})()

    runner = build_lark_command_runner(
        resolution,
        environ={"PATH": "/usr/bin:/bin"},
        subprocess_run=fake_run,
    )
    runner([str(target_cli), "profile", "list"], None, 30)

    assert calls[0]["env"]["PATH"].split(os.pathsep) == [
        str(target_cli.parent),
        str(global_cli.parent),
        "/usr/bin",
        "/bin",
    ]


def test_guarded_cli_spawns_inside_authority_and_waits_outside(tmp_path, monkeypatch):
    import subprocess
    import sys

    real_popen = subprocess.Popen
    states = []
    held = False

    @contextmanager
    def guard():
        nonlocal held
        held = True
        try:
            yield
        finally:
            held = False

    class ObservedProcess:
        def __init__(self, *args, **kwargs):
            states.append(("spawn", held))
            self.process = real_popen(*args, **kwargs)

        def __enter__(self):
            self.process.__enter__()
            return self

        def __exit__(self, *args):
            return self.process.__exit__(*args)

        @property
        def returncode(self):
            return self.process.returncode

        def communicate(self, *, timeout):
            states.append(("wait", held))
            return self.process.communicate(timeout=timeout)

    monkeypatch.setattr(subprocess, "Popen", ObservedProcess)
    resolution = LarkCliResolution(sys.executable, True, "fixture", None, None)
    runner = build_lark_command_runner(resolution)
    result = run_lark_command_with_dispatch_guard(
        runner, [sys.executable, "-c", "print('result')"], tmp_path, 5, guard,
    )
    assert result["returncode"] == 0 and result["stdout"] == "result\n"
    assert states == [("spawn", True), ("wait", False)]


def test_resolver_source_preserves_public_boundary_contract() -> None:
    source = Path(__file__).resolve().parents[2] / "loopx/extensions/lark/cli_resolution.py"

    boundary = scan_public_boundary([source])

    assert boundary["ok"] is True, boundary
    assert boundary["hits"] == []
