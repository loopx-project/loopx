"""Machine-owner settings adapter; CLI and browser use the same TS owner."""
from __future__ import annotations

import subprocess
from typing import Any

from .usage_ping import control

CHAT_USAGE_STATISTICS_PATH = "/api/chat/usage-statistics"


class UsageStatisticsRequestMixin:
    def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
        raise NotImplementedError
    def _send_error(self, message: str, *, status: int, error_code: str) -> None:
        raise NotImplementedError
    def _read_json(self) -> dict[str, Any]:
        raise NotImplementedError

    def _usage_statistics_status(self) -> None:
        self._usage_statistics_request("status")

    def _usage_statistics_update(self) -> None:
        try:
            body = self._read_json()
            if set(body) != {"enabled"} or not isinstance(body["enabled"], bool):
                raise ValueError("enabled must be the only field and a boolean")
        except (TypeError, ValueError) as exc:
            self._send_error(str(exc), status=400, error_code="invalid_usage_settings")
            return
        self._usage_statistics_request("enable" if body["enabled"] else "disable")

    def _usage_statistics_request(self, action: str) -> None:
        try:
            projection = control(action)
        except (OSError, ValueError, RuntimeError, TimeoutError, subprocess.TimeoutExpired):
            self._send_error("Usage settings unavailable; use loopx usage-ping status in the terminal.",
                             status=503, error_code="usage_settings_unavailable")
            return
        self._send_json(projection)
