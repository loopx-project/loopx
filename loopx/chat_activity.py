"""Owner-visible work steps projected from Codex app-server items.

A step names what the executor is doing (a command, a tool, a search, a file
change, a thinking span) so the owner can follow a Turn without reading the
host transcript. Steps ride on the replay-only ``agent.phase`` event next to
its legacy ``label``:

``{"id", "kind", "state", "title", "verb"?, "detail"?, "duration_ms"?,
"exit_code"?, "count"?}``

Steps never carry command output, tool arguments or results, file diffs, or
raw item payloads. A step's ``completed`` state means the host item finished,
not that a check passed or a Goal advanced.
"""

from __future__ import annotations

import hashlib
import re
import shlex
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

STEP_KINDS = ("reasoning", "command", "tool", "search", "file_change")
STEP_STATES = ("running", "completed", "failed")
COMMAND_VERBS = ("read", "search", "list", "run")

TITLE_LIMIT = 160
DETAIL_LIMIT = 4000
REASONING_UPDATE_INTERVAL_SEC = 1.5

_ITEM_KINDS = {
    "reasoning": "reasoning",
    "commandExecution": "command",
    "mcpToolCall": "tool",
    "dynamicToolCall": "tool",
    "webSearch": "search",
    "fileChange": "file_change",
}
_FAILED_STATUSES = {"failed", "declined"}
_STEP_ID = re.compile(r"^[A-Za-z0-9._:-]{1,120}$")
_SHELL_WRAPPER = re.compile(r"^(?:/\S*/)?(?:ba|z|)sh\s+-l?c\s+(.+)$", re.DOTALL)
# Commands are shown to their owner, but a pasted credential should still not
# be replayed into the page. Masking is deliberately broad: hiding a harmless
# value costs nothing, while a missed secret would be stored for replay.
# A shell value may concatenate quoted, escaped and plain fragments. Consume
# the whole value, including an unfinished quote, before truncating display text.
_SHELL_VALUE = r"""(?:'[^']*(?:'|$)|"(?:\\.|[^"\\])*(?:"|$)|\\.|[^\s'"\\;&|])+"""
_HEADER_VALUE = r"""(?:'[^']*(?:'|$)|"(?:\\.|[^"\\])*(?:"|$)|[^\s'";&|]+)"""
_SECRET_PATTERNS = (
    re.compile(r"(?i)(\bauthorization\s*:\s*)(?:bearer\s+|basic\s+)?" + _HEADER_VALUE),
    re.compile(r"(?i)(\bbearer\s+)" + _HEADER_VALUE),
    re.compile(r"(?i)(--?[a-z0-9-]*(?:token|secret|password|passwd|api-?key)[a-z0-9-]*(?:\s*=\s*|\s+))" + _SHELL_VALUE),
    re.compile(r"(?i)(\b[a-z0-9_]*(?:token|secret|password|passwd|api_?key)[a-z0-9_]*\s*=\s*)" + _SHELL_VALUE),
    re.compile(r"()\b(?:sk|ghp|gho|ghs|github_pat|xox[abp])[-_][A-Za-z0-9_-]{12,}"),
)
# Network URLs and relative paths must survive. A complete path token, rather
# than a directory-name denylist, determines whether it belongs to this project.
_STEP_PATH = re.compile(
    r"(?P<url>\b[A-Za-z][A-Za-z0-9+.-]*://[^\s`'\"<>]+)"
    r"|(?P<quote>['\"])(?P<quoted_path>(?:/|[A-Za-z]:[\\/]).*?)(?P=quote)"
    r"|(?P<path>(?<![A-Za-z0-9_./\\])(?:/|[A-Za-z]:[\\/])[^\s`'\"<>;&|]*)"
)


def _step_id(value: Any) -> str:
    raw = str(value or "")
    if _STEP_ID.fullmatch(raw):
        return raw
    return "step_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _mask_secrets(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda match: f"{match.group(1)}***", text)
    return text


def _unwrap_shell(command: str) -> str:
    match = _SHELL_WRAPPER.match(command.strip())
    if not match:
        return command.strip()
    try:
        parts = shlex.split(match.group(1))
    except ValueError:
        return match.group(1).strip()
    return parts[0] if len(parts) == 1 else match.group(1).strip()


def _limit(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


class CodexActivitySteps:
    """Fold one Turn's host items into compact, redacted owner steps."""

    def __init__(
        self,
        *,
        protected_paths: Iterable[Path | str] = (),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._protected = sorted(
            (str(path).rstrip("/\\") for path in protected_paths if str(path).rstrip("/\\")),
            key=len, reverse=True,
        )
        self._clock = clock
        self._started: dict[str, float] = {}
        self._reasoning: dict[str, dict[str, list[str]]] = {}
        self._reasoning_emitted: dict[str, float] = {}

    def _clean(self, value: Any) -> str:
        def clean_path(match: re.Match[str]) -> str:
            url = match.group("url")
            if url:
                return "[local-path]" if url.lower().startswith("file://") else url
            quoted = match.group("quoted_path")
            raw = quoted if quoted is not None else match.group("path")
            candidate = raw if quoted is not None else raw.rstrip(".,;:!?)]}")
            suffix = raw[len(candidate):]
            replacement = "[local-path]"
            for root in self._protected:
                if candidate == root:
                    replacement = "."
                    break
                if candidate.startswith((root + "/", root + "\\")):
                    replacement = candidate[len(root) + 1:]
                    break
            quote = match.group("quote") or ""
            return f"{quote}{replacement}{suffix}{quote}"

        return _STEP_PATH.sub(clean_path, _mask_secrets(str(value or "")))

    def _title(self, value: Any) -> str:
        return _limit(" ".join(self._clean(value).split()), TITLE_LIMIT)

    def _detail(self, value: Any) -> str:
        return _limit(self._clean(value).strip(), DETAIL_LIMIT)

    def started(self, item: Any) -> dict[str, Any] | None:
        if not isinstance(item, dict) or item.get("type") not in _ITEM_KINDS:
            return None
        step_id = _step_id(item.get("id"))
        now = self._clock()
        self._started.setdefault(step_id, now)
        if item.get("type") == "reasoning":
            # The first update waits one interval so it carries a phrase, not a token.
            self._reasoning_emitted.setdefault(step_id, now)
        return self._step(item, step_id, "running")

    def completed(self, item: Any) -> dict[str, Any] | None:
        if not isinstance(item, dict) or item.get("type") not in _ITEM_KINDS:
            return None
        step_id = _step_id(item.get("id"))
        status = str(item.get("status") or "")
        exit_code = item.get("exitCode")
        failed = status in _FAILED_STATUSES or item.get("success") is False or (
            isinstance(exit_code, int) and exit_code != 0
        )
        step = self._step(item, step_id, "failed" if failed else "completed")
        started = self._started.pop(step_id, None)
        duration = item.get("durationMs")
        if isinstance(duration, int) and duration >= 0:
            step["duration_ms"] = duration
        elif started is not None:
            step["duration_ms"] = max(0, int((self._clock() - started) * 1000))
        self._reasoning.pop(step_id, None)
        self._reasoning_emitted.pop(step_id, None)
        return step

    def reasoning_delta(
        self, item_id: Any, delta: Any, *, summary: bool, index: Any = 0
    ) -> dict[str, Any] | None:
        """Accumulate streamed thinking text; return a throttled update."""

        if not isinstance(delta, str) or not delta:
            return None
        step_id = _step_id(item_id)
        parts = self._reasoning.setdefault(step_id, {"summary": [], "content": []})
        bucket = parts["summary" if summary else "content"]
        position = index if isinstance(index, int) and 0 <= index < 64 else len(bucket) - 1
        while len(bucket) <= max(position, 0):
            bucket.append("")
        bucket[max(position, 0)] += delta
        now = self._clock()
        if now - self._reasoning_emitted.get(step_id, float("-inf")) < REASONING_UPDATE_INTERVAL_SEC:
            return None
        self._reasoning_emitted[step_id] = now
        self._started.setdefault(step_id, now)
        return self._reasoning_step(step_id, "running", parts["summary"], parts["content"])

    def _reasoning_step(
        self, step_id: str, state: str, summary: list[str], content: list[str]
    ) -> dict[str, Any]:
        # A model summary is the host's intended explanation; raw thinking text
        # is shown only when that is all the host exposes.
        text = "\n\n".join(part.strip() for part in (summary if any(summary) else content) if part.strip())
        step: dict[str, Any] = {"id": step_id, "kind": "reasoning", "state": state, "title": ""}
        if text:
            first = next(line for line in text.splitlines() if line.strip())
            step["title"] = self._title(first.strip().strip("*#").strip())
            step["detail"] = self._detail(text)
        return step

    def _step(self, item: dict[str, Any], step_id: str, state: str) -> dict[str, Any]:
        kind = _ITEM_KINDS[str(item.get("type"))]
        if kind == "reasoning":
            summary = [str(part) for part in item.get("summary") or [] if isinstance(part, str)]
            content = [str(part) for part in item.get("content") or [] if isinstance(part, str)]
            streamed = self._reasoning.get(step_id, {"summary": [], "content": []})
            return self._reasoning_step(
                step_id, state, summary or streamed["summary"], content or streamed["content"]
            )
        step: dict[str, Any] = {"id": step_id, "kind": kind, "state": state, "title": ""}
        if kind == "command":
            command = _unwrap_shell(str(item.get("command") or ""))
            verb, target = "run", command
            actions = [action for action in item.get("commandActions") or [] if isinstance(action, dict)]
            if len(actions) == 1:
                action = actions[0]
                action_type = action.get("type")
                if action_type == "read" and action.get("name"):
                    verb, target = "read", str(action.get("name"))
                elif action_type == "search" and action.get("query"):
                    verb, target = "search", str(action.get("query"))
                elif action_type == "listFiles":
                    verb, target = "list", str(action.get("path") or ".")
            step["verb"] = verb
            step["title"] = self._title(target)
            if command and (verb != "run" or len(command) > TITLE_LIMIT):
                step["detail"] = self._detail(command)
            exit_code = item.get("exitCode")
            if isinstance(exit_code, int):
                step["exit_code"] = exit_code
        elif kind == "tool":
            namespace = item.get("server") or item.get("namespace")
            name = str(item.get("tool") or "")
            step["title"] = self._title(f"{namespace} · {name}" if namespace else name)
        elif kind == "search":
            step["title"] = self._title(item.get("query"))
        elif kind == "file_change":
            paths = [
                self._title(change.get("path"))
                for change in item.get("changes") or []
                if isinstance(change, dict) and change.get("path")
            ]
            if paths:
                step["title"] = paths[0]
                step["count"] = len(paths)
                if len(paths) > 1:
                    step["detail"] = _limit("\n".join(paths), DETAIL_LIMIT)
        return step
