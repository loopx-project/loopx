"""M1 keeps ``loopx.chat`` public while nesting internal-only helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import loopx.chat as public_chat
from loopx.chat.automation_cadence_api import AutomationCadenceRequestMixin
from loopx.chat.event_cache import ChatEventCache
from loopx.chat.goal_configuration_api import GoalConfigurationRequestMixin
from loopx.chat.machine_configuration_api import MachineConfigurationRequestMixin
from loopx.chat.monitor_actions import ChatMonitorActionMixin
from loopx.chat.todo_actions import ChatTodoActionMixin
from loopx.chat.todo_detail import TodoDetailRequestMixin
from loopx.chat.usage_statistics_api import UsageStatisticsRequestMixin
from loopx.chat.workspace_directory import workspace_goal_directory


def test_chat_internal_helpers_load_below_the_public_chat_namespace() -> None:
    namespace_root = Path(public_chat.__file__).with_suffix("").resolve()
    assert [Path(entry).resolve() for entry in public_chat.__path__] == [namespace_root]
    assert (
        AutomationCadenceRequestMixin.__module__
        == "loopx.chat.automation_cadence_api"
    )
    assert ChatEventCache.__module__ == "loopx.chat.event_cache"
    assert GoalConfigurationRequestMixin.__module__ == "loopx.chat.goal_configuration_api"
    assert (
        MachineConfigurationRequestMixin.__module__
        == "loopx.chat.machine_configuration_api"
    )
    assert ChatMonitorActionMixin.__module__ == "loopx.chat.monitor_actions"
    assert ChatTodoActionMixin.__module__ == "loopx.chat.todo_actions"
    assert TodoDetailRequestMixin.__module__ == "loopx.chat.todo_detail"
    assert UsageStatisticsRequestMixin.__module__ == "loopx.chat.usage_statistics_api"
    assert workspace_goal_directory.__module__ == "loopx.chat.workspace_directory"


def test_chat_internal_helpers_have_no_top_level_compatibility_shims() -> None:
    for old_module in (
        "loopx.chat_event_cache",
        "loopx.chat_automation_cadence_api",
        "loopx.chat_goal_configuration_api",
        "loopx.chat_machine_configuration_api",
        "loopx.chat_monitor_actions",
        "loopx.chat_todo_actions",
        "loopx.chat_todo_detail",
        "loopx.chat_usage_statistics_api",
        "loopx.chat_workspace_directory",
    ):
        assert importlib.util.find_spec(old_module) is None
