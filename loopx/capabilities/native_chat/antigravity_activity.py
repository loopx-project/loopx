"""Project observed native steps through the shared, redacted activity owner."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from ...presentation.codex_activity import CodexActivitySteps

_BOILERPLATE_THINKING_PREFIXES = (
    "i'm thinking through how to approach this.",
    "thinking through how to approach this.",
)


def _clean_thinking_summary(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    while lines and lines[0].lower() in _BOILERPLATE_THINKING_PREFIXES:
        lines.pop(0)
    return "\n\n".join(lines).strip()


class AntigravityActivity:
    def __init__(self, work_dir: Path) -> None:
        self.steps = CodexActivitySteps(protected_paths=[work_dir])
        self.started: set[str] = set()
        self.completed: set[str] = set()
        self.thinking_digests: dict[str, str] = {}
        self.output_digests: dict[str, str] = {}
        self.tool_calls: dict[str, dict[str, Any] | None] = {}

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
            summary = _clean_thinking_summary(planner.get("thinking"))
            if summary or planner.get("thinking"):
                # Project only the native thinking summary (`thinking`), never
                # `rawThinking`, through the shared path/secret redacting owner.
                item_id = f"agy_think_{index}"
                item: dict[str, Any] = {
                    "id": item_id,
                    "type": "reasoning",
                    **({"summary": [summary]} if summary else {}),
                }
                digest = hashlib.sha256(summary.encode("utf-8")).hexdigest() if summary else ""
                if item_id not in self.started:
                    if digest:
                        self.thinking_digests[item_id] = digest
                    start(item)
                elif digest and self.thinking_digests.get(item_id) != digest and item_id not in self.completed:
                    self.thinking_digests[item_id] = digest
                    emit(
                        "item/reasoning/summaryTextDelta",
                        self.steps._reasoning_step(item_id, "running", [summary]),
                    )
                if status == "CORTEX_STEP_STATUS_DONE":
                    finish(item)
            calls = planner.get("toolCalls")
            for offset, call in enumerate(calls if isinstance(calls, list) else []):
                if isinstance(call, dict) and isinstance(call.get("name"), str):
                    item = {"id": f"agy_call_{index}_{offset}", "type": "dynamicToolCall", "tool": call["name"]}
                    call_id = call.get("id")
                    if isinstance(call_id, str) and call_id:
                        previous = self.tool_calls.setdefault(call_id, item)
                        if previous != item:
                            self.tool_calls[call_id] = None  # Ambiguous IDs cannot close another call.
                    start(item)
            # A finished planner has only requested the tool. It is not proof
            # that the tool ran or completed; never fabricate that transition.
        elif kind == "CORTEX_STEP_TYPE_CODE_ACTION":
            if not isinstance(native.get("codeAction"), dict):
                return events
            metadata = native.get("metadata")
            call = metadata.get("toolCall") if isinstance(metadata, dict) else None
            call_id = call.get("id") if isinstance(call, dict) else None
            requested = self.tool_calls.get(call_id) if isinstance(call_id, str) else None
            # Match the provider's exact call identity, not its name, path or
            # arguments. An unlinked action has its own observed activity row.
            item = dict(requested) if requested else {
                "id": f"agy_edit_{index}", "type": "dynamicToolCall", "tool": "native.codeAction"}
            start(item)
            if status in {"CORTEX_STEP_STATUS_DONE", "CORTEX_STEP_STATUS_ERROR"}:
                item["status"] = "failed" if status == "CORTEX_STEP_STATUS_ERROR" else "completed"
                finish(item)
        elif kind == "CORTEX_STEP_TYPE_VIEW_FILE":
            view = native.get("viewFile")
            if not isinstance(view, dict):
                return events
            uri = view.get("absolutePathUri")
            name = unquote(uri[7:]) if isinstance(uri, str) and uri.startswith("file:///") else "[local-path]"
            item = {"id": f"agy_read_{index}", "type": "commandExecution", "command": "",
                    "commandActions": [{"type": "read", "name": name}]}
            start(item)
            if status in {"CORTEX_STEP_STATUS_DONE", "CORTEX_STEP_STATUS_ERROR"}:
                item["status"] = "failed" if status == "CORTEX_STEP_STATUS_ERROR" else "completed"
                finish(item)
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
