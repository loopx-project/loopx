from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from loopx.capabilities.decision_context import private_state
from loopx.capabilities.benchmark_toolkit import native_codex_isolation
from loopx.control_plane.heartbeat import automation_upgrade
from loopx.control_plane.goals import botmux_runtime
from loopx.extensions import presentation
from loopx import file_lock
from loopx.extensions.lark import private_json


class _WindowsOs:
    name = "nt"

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def __getattr__(self, name: str) -> Any:
        # Native Windows Python has neither directory fsync nor fchmod; an
        # attribute that exists only on POSIX must stay missing here, or the
        # POSIX lane stops covering the Windows surface.
        if name in {"O_DIRECTORY", "fchmod"}:
            raise AttributeError(name)
        return getattr(os, name)

    def open(self, path: str | bytes | os.PathLike[str], flags: int, *args: Any) -> int:
        if Path(path) == self._directory and flags == os.O_RDONLY:
            raise PermissionError(13, "Permission denied", str(path))
        return os.open(path, flags, *args)


def _write_lark_private_json(path: Path) -> None:
    private_json.write_private_json_atomic(path, {"status": "ready"})


def _write_decision_context(path: Path) -> None:
    private_state.write_private_decision_cursors_atomic(path, {"source": "cursor"})


def _write_extension_projection(path: Path) -> None:
    presentation._atomic_write_projection(path, {"status": "ready"})


def _write_heartbeat_automation(path: Path) -> None:
    automation_upgrade._atomic(path, 'prompt = "ready"\n')


def _write_botmux_binding(path: Path) -> None:
    botmux_runtime._write_private_json_atomic(path, {"status": "ready"})


def _write_native_isolation(path: Path) -> None:
    native_codex_isolation._atomic_write_text(path, "prompt = \"ready\"\n")


@pytest.mark.parametrize(
    ("module", "writer", "expected"),
    [
        pytest.param(
            private_json,
            _write_lark_private_json,
            {"status": "ready"},
            id="lark-private-json",
        ),
        pytest.param(
            private_state,
            _write_decision_context,
            {"source": "cursor"},
            id="decision-context",
        ),
        pytest.param(
            presentation,
            _write_extension_projection,
            {"status": "ready"},
            id="extension-projection",
        ),
        pytest.param(
            automation_upgrade,
            _write_heartbeat_automation,
            'prompt = "ready"\n',
            id="heartbeat-automation",
        ),
        pytest.param(
            botmux_runtime,
            _write_botmux_binding,
            {"status": "ready"},
            id="botmux-binding",
        ),
        pytest.param(
            native_codex_isolation,
            _write_native_isolation,
            'prompt = "ready"\n',
            id="native-isolation",
        ),
    ],
)
def test_atomic_writers_skip_unsupported_windows_directory_fsync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType,
    writer: Callable[[Path], None],
    expected: object,
) -> None:
    target = tmp_path / f"{module.__name__.rsplit('.', 1)[-1]}.json"
    if os.name != "nt":
        # `private_json` delegates to the kernel owner, so its Windows-unsafe
        # `O_DIRECTORY`/`fchmod` calls now live in `file_lock.os`.
        owner = file_lock if module is private_json else module
        monkeypatch.setattr(owner, "os", _WindowsOs(target.parent))

    writer(target)

    content = target.read_text(encoding="utf-8")
    actual = json.loads(content) if isinstance(expected, dict) else content
    assert actual == expected
