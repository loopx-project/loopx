from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from .doubao_model_behavior_actor import (
    ALLOWED_MODEL_BEHAVIOR_MODELS,
    DOUBAO_CHAT_COMPLETIONS_ENDPOINT,
    DoubaoActorTransport,
    DoubaoActorTransportError,
)

EXEC_COMMAND_TOOL = {
    "type": "function",
    "function": {
        "name": "exec_command",
        "description": (
            "Run a shell command in the current workspace and return its output."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "cmd": {
                    "type": "string",
                    "description": "The shell command to run.",
                }
            },
            "required": ["cmd"],
            "additionalProperties": False,
        },
    },
}

MAX_TOOL_ARGUMENT_BYTES = 8_192
MAX_PROVIDER_TOKENS = 2_048

QUOTA_FIRST_TOOL_INSTRUCTION = (
    "Before any workspace read, diagnostic, discovery, clock lookup, or state "
    "inspection, the first tool call must execute the exact quota guard command "
    "shown in the heartbeat task. It may set only LOOPX_TURN inline before that "
    "guard; do not prepend export, echo, or any other shell command. Treat the "
    "returned interaction_contract as the authority for every later action. "
)


@dataclass(frozen=True)
class ExecToolCall:
    call_id: str
    command: str
    provider_value: dict[str, Any]
    assistant_content: str | None = None


@dataclass(frozen=True)
class ExecToolStep:
    assistant_content: str | None
    tool_call: ExecToolCall | None


@dataclass(frozen=True)
class ScriptedExecToolAction:
    """One model-selected tool action for a hermetic behavior test."""

    command: str
    assistant_content: str | None = None
    call_id: str | None = None


@dataclass(frozen=True)
class ScriptedAssistantAction:
    """One model final response for a hermetic behavior test."""

    content: str


ScriptedModelAction = ScriptedExecToolAction | ScriptedAssistantAction
ScriptedModelActionFactory = Callable[[Mapping[str, Any]], ScriptedModelAction]


def scripted_doubao_response(
    action: ScriptedModelAction,
    *,
    default_call_id: str,
) -> dict[str, Any]:
    """Encode one model-like action through the provider response boundary."""

    if isinstance(action, ScriptedAssistantAction):
        return {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": action.content,
                    },
                }
            ]
        }
    call_id = action.call_id or default_call_id
    return {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": action.assistant_content,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": "exec_command",
                                "arguments": json.dumps(
                                    {"cmd": action.command},
                                    ensure_ascii=False,
                                    sort_keys=True,
                                    separators=(",", ":"),
                                ),
                            },
                        }
                    ],
                },
            }
        ]
    }


def scripted_exec_tool_response(
    call_id: str,
    command: str,
    *,
    content: str | None = None,
) -> dict[str, Any]:
    return scripted_doubao_response(
        ScriptedExecToolAction(
            command=command,
            assistant_content=content,
            call_id=call_id,
        ),
        default_call_id=call_id,
    )


def scripted_assistant_response(content: str) -> dict[str, Any]:
    return scripted_doubao_response(
        ScriptedAssistantAction(content=content),
        default_call_id="unused",
    )


class ScriptedDoubaoExecTransport:
    """Drive the real provider decoder from model-like actions, not receipts.

    A callable step may inspect the complete next provider request, including
    the latest real tool result, before choosing the next model action.  This
    keeps hermetic tests on the same message/tool protocol as live actors while
    avoiding duplicated hand-built Ark response dictionaries.
    """

    def __init__(
        self,
        steps: Sequence[ScriptedModelAction | ScriptedModelActionFactory],
    ) -> None:
        self._steps = tuple(steps)
        self.requests: list[dict[str, Any]] = []

    def __call__(
        self,
        *,
        endpoint: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        del endpoint, headers, timeout_seconds
        request = json.loads(body)
        if not isinstance(request, dict):
            raise ValueError("scripted Doubao request must be an object")
        self.requests.append(request)
        index = len(self.requests) - 1
        if index >= len(self._steps):
            raise ValueError("scripted Doubao transport exhausted its actions")
        step = self._steps[index]
        action = step(request) if callable(step) else step
        return scripted_doubao_response(
            action,
            default_call_id=f"call-{len(self.requests)}",
        )


class DoubaoExecToolClient:
    """One bounded Ark function-tool transport shared by real behavior gates."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float,
        transport: DoubaoActorTransport,
    ) -> None:
        if not api_key.strip():
            raise RuntimeError("Doubao actor requires a runtime-injected API key")
        if model not in ALLOWED_MODEL_BEHAVIOR_MODELS:
            raise ValueError("Doubao actor model must be explicitly allowlisted")
        if timeout_seconds <= 0 or timeout_seconds > 300:
            raise ValueError("Doubao actor timeout must be between 0 and 300 seconds")
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    @property
    def actor_ref(self) -> str:
        return f"ark:{self._model}"

    def next_tool_call(
        self,
        messages: list[dict[str, Any]],
        *,
        tool_description: str | None = None,
    ) -> ExecToolCall | None:
        return self.next_step(messages, tool_description=tool_description).tool_call

    def next_step(
        self,
        messages: list[dict[str, Any]],
        *,
        tool_description: str | None = None,
    ) -> ExecToolStep:
        body = {
            "model": self._model,
            "messages": messages,
            "tools": [EXEC_COMMAND_TOOL if tool_description is None else {
                **EXEC_COMMAND_TOOL,
                "function": {**EXEC_COMMAND_TOOL["function"], "description": tool_description},
            }],
            "tool_choice": "auto",
            "thinking": {"type": "disabled"},
            "temperature": 0,
            "max_tokens": MAX_PROVIDER_TOKENS,
            "stream": False,
        }
        try:
            response = self._transport(
                endpoint=DOUBAO_CHAT_COMPLETIONS_ENDPOINT,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                body=json.dumps(
                    body,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8"),
                timeout_seconds=self._timeout_seconds,
            )
            return provider_exec_tool_step(response)
        except DoubaoActorTransportError:
            raise
        except Exception:  # noqa: BLE001 - custom transports may raise any error.
            raise DoubaoActorTransportError(
                "Doubao tool behavior provider transport failed",
                error_code="provider_transport_failed",
            ) from None

    def next_final_content(
        self,
        messages: list[dict[str, Any]],
    ) -> str | None:
        """Request the host finalization turn after the tool action is done."""

        body = {
            "model": self._model,
            "messages": messages,
            "thinking": {"type": "disabled"},
            "temperature": 0,
            "max_tokens": MAX_PROVIDER_TOKENS,
            "stream": False,
        }
        try:
            response = self._transport(
                endpoint=DOUBAO_CHAT_COMPLETIONS_ENDPOINT,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                body=json.dumps(
                    body,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8"),
                timeout_seconds=self._timeout_seconds,
            )
            step = provider_exec_tool_step(response)
            if step.tool_call is not None:
                raise DoubaoActorTransportError(
                    "Doubao finalization returned an unavailable tool call",
                    error_code="provider_invalid_tool_call",
                )
            return step.assistant_content
        except DoubaoActorTransportError:
            raise
        except Exception:  # noqa: BLE001 - custom transports may raise any error.
            raise DoubaoActorTransportError(
                "Doubao finalization provider transport failed",
                error_code="provider_transport_failed",
            ) from None


def digest_text(value: str) -> str:
    return f"sha256:{sha256(value.encode('utf-8')).hexdigest()}"


def provider_exec_tool_step(response: Mapping[str, Any]) -> ExecToolStep:
    """Decode one assistant step while retaining only its bounded text."""

    choices = response.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise DoubaoActorTransportError(
            "Doubao tool behavior response must contain exactly one choice",
            error_code="provider_invalid_shape",
        )
    choice = choices[0]
    if not isinstance(choice, Mapping):
        raise DoubaoActorTransportError(
            "Doubao tool behavior choice must be an object",
            error_code="provider_invalid_shape",
        )
    message = choice.get("message")
    if not isinstance(message, Mapping):
        raise DoubaoActorTransportError(
            "Doubao tool behavior choice is missing its message",
            error_code="provider_invalid_shape",
        )
    assistant_content = message.get("content")
    if assistant_content is not None and not isinstance(assistant_content, str):
        raise DoubaoActorTransportError(
            "Doubao tool behavior assistant content must be text or null",
            error_code="provider_invalid_shape",
        )
    normalized_content = (
        assistant_content.strip() if isinstance(assistant_content, str) else None
    ) or None
    tool_calls = message.get("tool_calls")
    if tool_calls in (None, []):
        return ExecToolStep(
            assistant_content=normalized_content,
            tool_call=None,
        )
    tool_call = provider_exec_tool_call(response)
    return ExecToolStep(
        assistant_content=normalized_content,
        tool_call=tool_call,
    )


def provider_exec_tool_call(response: Mapping[str, Any]) -> ExecToolCall | None:
    """Decode one ordinary exec tool call without interpreting its semantics."""

    choices = response.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise DoubaoActorTransportError(
            "Doubao tool behavior response must contain exactly one choice",
            error_code="provider_invalid_shape",
        )
    choice = choices[0]
    if not isinstance(choice, Mapping):
        raise DoubaoActorTransportError(
            "Doubao tool behavior choice must be an object",
            error_code="provider_invalid_shape",
        )
    message = choice.get("message")
    if not isinstance(message, Mapping):
        raise DoubaoActorTransportError(
            "Doubao tool behavior choice is missing its message",
            error_code="provider_invalid_shape",
        )
    tool_calls = message.get("tool_calls")
    if tool_calls in (None, []):
        return None
    if not isinstance(tool_calls, list) or len(tool_calls) != 1:
        raise DoubaoActorTransportError(
            "Doubao tool behavior must choose exactly one tool per step",
            error_code="provider_invalid_tool_call",
        )
    raw_call = tool_calls[0]
    if not isinstance(raw_call, Mapping):
        raise DoubaoActorTransportError(
            "Doubao tool call must be an object",
            error_code="provider_invalid_tool_call",
        )
    function = raw_call.get("function")
    if (
        raw_call.get("type") != "function"
        or not isinstance(function, Mapping)
        or function.get("name") != "exec_command"
    ):
        raise DoubaoActorTransportError(
            "Doubao tool behavior selected an unsupported tool",
            error_code="provider_invalid_tool_call",
        )
    arguments_text = function.get("arguments")
    if (
        not isinstance(arguments_text, str)
        or len(arguments_text.encode("utf-8")) > MAX_TOOL_ARGUMENT_BYTES
    ):
        raise DoubaoActorTransportError(
            "Doubao tool arguments are missing or too large",
            error_code="provider_invalid_tool_call",
        )
    try:
        arguments = json.loads(arguments_text)
    except json.JSONDecodeError:
        raise DoubaoActorTransportError(
            "Doubao tool arguments are not valid JSON",
            error_code="provider_invalid_tool_call",
        ) from None
    if not isinstance(arguments, Mapping) or set(arguments) != {"cmd"}:
        raise DoubaoActorTransportError(
            "Doubao exec_command arguments must contain only cmd",
            error_code="provider_invalid_tool_call",
        )
    command = arguments.get("cmd")
    if not isinstance(command, str) or not command.strip():
        raise DoubaoActorTransportError(
            "Doubao exec_command cmd must be non-empty text",
            error_code="provider_invalid_tool_call",
        )
    call_id = str(raw_call.get("id") or "").strip()
    if not call_id:
        raise DoubaoActorTransportError(
            "Doubao tool call is missing its id",
            error_code="provider_invalid_tool_call",
        )
    assistant_content = message.get("content")
    if assistant_content is not None and not isinstance(assistant_content, str):
        raise DoubaoActorTransportError(
            "Doubao tool behavior assistant content must be text or null",
            error_code="provider_invalid_shape",
        )
    return ExecToolCall(
        call_id=call_id,
        command=command.strip(),
        provider_value={
            "id": call_id,
            "type": "function",
            "function": {
                "name": "exec_command",
                "arguments": arguments_text,
            },
        },
        assistant_content=(
            assistant_content.strip() if isinstance(assistant_content, str) else None
        )
        or None,
    )


def loopx_command_tokens(command: str) -> list[str] | None:
    """Return one bounded LoopX argv suffix, never a shell program."""

    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    for index, token in enumerate(tokens):
        if Path(token).name == "loopx":
            command_tokens = tokens[index:]
            if any(token in {";", "&&", "||", "|"} for token in command_tokens):
                return None
            return command_tokens
    return None


def argument_value(tokens: list[str], option: str) -> str | None:
    try:
        index = tokens.index(option)
    except ValueError:
        return None
    return tokens[index + 1] if index + 1 < len(tokens) else None


def replace_argument(tokens: list[str], option: str, value: str) -> None:
    try:
        index = tokens.index(option)
    except ValueError:
        return
    if index + 1 >= len(tokens):
        raise ValueError(f"missing value for {option}")
    tokens[index + 1] = value


def execute_loopx_cli(
    command: str,
    *,
    source_root: Path,
    project_root: Path,
    argument_overrides: Mapping[str, str] | None = None,
    accepted_return_codes: Sequence[int] = (0,),
    timeout_seconds: float = 60,
) -> str:
    """Execute only the parsed LoopX argv against an isolated project.

    Most behavior actors accept only successful CLI calls. A scenario that
    starts from a typed, actionable rejection may explicitly accept that
    command's documented non-zero code while still failing closed on every
    other result.
    """

    tokens = loopx_command_tokens(command)
    if not tokens:
        raise ValueError("command does not contain one bounded loopx invocation")
    argv = list(tokens[1:])
    for option, value in (argument_overrides or {}).items():
        replace_argument(argv, option, value)
    if "quota" in argv and "--scan-root" not in argv and "--scan-path" not in argv:
        argv.extend(["--scan-root", str(project_root)])
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(source_root)
        if not existing_pythonpath
        else os.pathsep.join((str(source_root), existing_pythonpath))
    )
    completed = subprocess.run(
        [sys.executable, "-P", "-m", "loopx.cli", *argv],
        cwd=project_root,
        env=env,
        check=False,
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        timeout=timeout_seconds,
    )
    if completed.returncode not in accepted_return_codes:
        detail = completed.stderr.strip() or completed.stdout.strip()
        bounded_detail = (
            detail if len(detail) <= 1_000 else detail[:500] + "\n...\n" + detail[-500:]
        )
        raise LoopxCliExecutionError(completed.returncode, bounded_detail)
    return completed.stdout


class LoopxCliExecutionError(RuntimeError):
    """An executed CLI returned nonzero; this does not imply state rollback."""

    def __init__(self, returncode: int, detail: str) -> None:
        super().__init__(f"LoopX CLI command failed with exit={returncode}: {detail}")
        self.returncode = returncode
