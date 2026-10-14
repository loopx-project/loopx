"""File-mode contract of the writers that delegate to the kernel atomic writer.

Each writer kept its own mode policy when it moved onto `file_lock`: the goal
registry leaves the umask-narrowed `mkstemp` mode alone unless asked to
preserve an existing mode, while the Chat and Lark private-state writers force
0600. A restrictive umask is what tells the two policies apart.
"""
from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from loopx import chat_endpoints, chat_store, file_lock
from loopx.extensions.lark import private_json
from loopx.kunluncode_goal_mode.context import read_binding, write_binding
from loopx.registry import atomic_write_json


pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")

RESTRICTIVE_UMASK = 0o277


@contextmanager
def _umask(mask: int) -> Iterator[None]:
    previous = os.umask(mask)
    try:
        yield
    finally:
        os.umask(previous)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_kernel_writer_without_mode_keeps_the_umask_narrowed_temp_mode(
    tmp_path: Path,
) -> None:
    target = tmp_path / "state.json"
    with _umask(RESTRICTIVE_UMASK):
        file_lock.write_json_atomic(target, {"ok": True})
        assert _mode(target) == 0o400
        file_lock.write_json_atomic(target, {"ok": True}, mode=0o600)
        assert _mode(target) == 0o600


def test_registry_writer_does_not_force_a_mode_on_a_new_file(tmp_path: Path) -> None:
    target = tmp_path / "registry.json"
    with _umask(RESTRICTIVE_UMASK):
        atomic_write_json(target, {"goals": []})
    assert _mode(target) == 0o400


def test_registry_writer_preserves_or_replaces_an_existing_mode(tmp_path: Path) -> None:
    target = tmp_path / "registry.json"
    target.write_text("{}\n", encoding="utf-8")
    target.chmod(0o640)
    with _umask(RESTRICTIVE_UMASK):
        atomic_write_json(target, {"goals": []}, preserve_mode=True)
        assert _mode(target) == 0o640
        atomic_write_json(target, {"goals": []})
        assert _mode(target) == 0o400


def test_kunluncode_binding_keeps_the_registry_mode_and_reads_back(
    tmp_path: Path,
) -> None:
    (tmp_path / ".loopx").mkdir()
    with _umask(RESTRICTIVE_UMASK):
        path = write_binding(tmp_path, goal_id="goal-a", agent_id="agent-a")
    assert _mode(path) == 0o400
    assert read_binding(tmp_path) == {"goal_id": "goal-a", "agent_id": "agent-a"}


def test_private_state_writers_still_force_0600(tmp_path: Path) -> None:
    store = tmp_path / "store.json"
    endpoints = tmp_path / "endpoints.json"
    lark = tmp_path / "lark.json"
    with _umask(RESTRICTIVE_UMASK):
        chat_store._atomic_write_json(store, {"status": "ready"})
        chat_endpoints._atomic_write(endpoints, {"status": "ready"})
        private_json.write_private_json_atomic(lark, {"status": "ready"})
    assert [_mode(store), _mode(endpoints), _mode(lark)] == [0o600, 0o600, 0o600]


def test_chat_store_writer_preserves_an_existing_mode(tmp_path: Path) -> None:
    target = tmp_path / "store.json"
    target.write_text("{}\n", encoding="utf-8")
    target.chmod(0o640)
    with _umask(RESTRICTIVE_UMASK):
        chat_store._atomic_write_json(target, {"status": "ready"}, preserve_mode=True)
    assert _mode(target) == 0o640
