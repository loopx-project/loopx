from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from .time import parse_scheduler_timestamp

MONITOR_WAIT_PROGRESSION_MINUTES = [15, 30, 60]
MONITOR_WAIT_HOST_FLOOR_MINUTES = 15
MONITOR_WAIT_NEAR_WINDOW_LEAD_MINUTES = 60


class MonitorWaitPhase(str, Enum):
    EXPIRED = "expired"
    ACTIVE_WINDOW = "active_window"
    NEAR_WINDOW = "near_window"
    FAR_WINDOW = "far_window"
    CADENCE_ONLY = "cadence_only"


MONITOR_WAIT_PHASE_RANK = {
    MonitorWaitPhase.ACTIVE_WINDOW.value: 0,
    MonitorWaitPhase.NEAR_WINDOW.value: 1,
    MonitorWaitPhase.CADENCE_ONLY.value: 2,
    MonitorWaitPhase.FAR_WINDOW.value: 3,
}


def _parse_monitor_timestamp(value: Any) -> datetime | None:
    return parse_scheduler_timestamp(value)
