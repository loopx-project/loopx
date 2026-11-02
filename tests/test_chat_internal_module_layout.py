"""M1 keeps ``loopx.chat`` public while nesting internal-only helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import loopx.chat as public_chat
from loopx.chat.event_cache import ChatEventCache
from loopx.chat.monitor_actions import ChatMonitorActionMixin
from loopx.chat.todo_actions import ChatTodoActionMixin
from loopx.chat.todo_detail import TodoDetailRequestMixin


def test_chat_internal_helpers_load_below_the_public_chat_namespace() -> None:
    namespace_root = Path(public_chat.__file__).with_suffix("").resolve()
    assert [Path(entry).resolve() for entry in public_chat.__path__] == [namespace_root]
    assert ChatEventCache.__module__ == "loopx.chat.event_cache"
    assert ChatMonitorActionMixin.__module__ == "loopx.chat.monitor_actions"
    assert ChatTodoActionMixin.__module__ == "loopx.chat.todo_actions"
    assert TodoDetailRequestMixin.__module__ == "loopx.chat.todo_detail"


def test_chat_internal_helpers_have_no_top_level_compatibility_shims() -> None:
    for old_module in (
        "loopx.chat_event_cache",
        "loopx.chat_monitor_actions",
        "loopx.chat_todo_actions",
        "loopx.chat_todo_detail",
    ):
        assert importlib.util.find_spec(old_module) is None
