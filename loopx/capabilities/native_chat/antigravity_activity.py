"""Project observed native steps through the shared, redacted activity owner."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ...presentation.codex_activity import CodexActivitySteps


class AntigravityActivity:
    def __init__(self, work_dir: Path) -> None:
        self.steps = CodexActivitySteps(protected_paths=[work_dir])
        self.started: set[str] = set()
        self.completed: set[str] = set()
        self.output_digests: dict[str, str] = {}

    def observe(self, index: int, native: dict[str, Any]) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []

        def emit(method: str, step: dict[str, Any] | None) -> None:
            if step:
                label = ({"reasoning": "Agent 正在思考", "command": "Agent 正在执行命令",
                          "tool": "Agent 正在调用工具"}[step["kind"]]
                         if method != "item/completed" else "Agent 返回了处理状态")
                events.append({"method": method, "label": label, "step": step})

        def start(item: dict[str, Any]) -> None:
            if item["id"] not in self.started:
                self.started.add(item["id"])
                emit("item/started", self.steps.started(item))

        def finish(item: dict[str, Any]) -> None:
            if item["id"] not in self.completed:
                self.completed.add(item["id"])
                emit("item/completed", self.steps.completed(item))

        kind = native.get("type")
        status = native.get("status")
        if kind == "CORTEX_STEP_TYPE_PLANNER_RESPONSE":
            planner = native.get("plannerResponse")
            if not isinstance(planner, dict):
                return events
            if planner.get("thinking"):
                # The native field is not a qualified public summary. Report
                # the observed thinking span without publishing its contents.
                item: dict[str, Any] = {"id": f"agy_think_{index}", "type": "reasoning"}
                start(item)
                if status == "CORTEX_STEP_STATUS_DONE":
                    finish(item)
            calls = planner.get("toolCalls")
            for offset, call in enumerate(calls if isinstance(calls, list) else []):
                if isinstance(call, dict) and isinstance(call.get("name"), str):
                    start({"id": f"agy_call_{index}_{offset}", "type": "dynamicToolCall",
                           "tool": call["name"]})
            # A finished planner has only requested the tool. It is not proof
            # that the tool ran or completed; never fabricate that transition.
        elif kind == "CORTEX_STEP_TYPE_RUN_COMMAND":
            command = native.get("runCommand")
            if not isinstance(command, dict):
                return events
            item = {"id": f"agy_cmd_{index}", "type": "commandExecution",
                    "command": command.get("commandLine") or command.get("command") or "command"}
            start(item)
            output = command.get("combinedOutput")
            if isinstance(output, str) and output and item["id"] not in self.completed:
                digest = hashlib.sha256(output.encode()).hexdigest()
                if self.output_digests.get(item["id"]) != digest:
                    self.output_digests[item["id"]] = digest
                    # Only the observation travels, never stdout/stderr.
                    emit("item/commandExecution/outputDelta", self.steps.activity_delta(
                        item["id"], "observed", kind="command"))
            if status in {"CORTEX_STEP_STATUS_DONE", "CORTEX_STEP_STATUS_ERROR"}:
                exit_code = command.get("exitCode")
                if type(exit_code) is int:
                    item["exitCode"] = exit_code
                item["status"] = "failed" if status == "CORTEX_STEP_STATUS_ERROR" else "completed"
                finish(item)
        return events
