"""Benchmark execution options adapted to the shared Codex session owner."""
from __future__ import annotations

from loopx.control_plane.turn_driver.codex_agent_session import CodexAgentSession
from .codex import Execution


class BenchmarkSessionWake(CodexAgentSession):
    def __init__(self, env: dict[str, str], execution: Execution, receipt: dict) -> None:
        super().__init__(env, context=execution.context, sandbox=execution.sandbox, receipt=receipt)
