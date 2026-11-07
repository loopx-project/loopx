from __future__ import annotations

import json
import os
import shlex
import stat
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

EXEC_COMMAND_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "exec_command",
        "description": (
            "Run a shell command in the current workspace and return its output. "
            "This host accepts at most one tool call per assistant response. "
            "Wait for that call's result before choosing the next command. "
            "Shell composition within a command remains available."
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
        tool = EXEC_COMMAND_TOOL
        if tool_description is not None:
            tool = {
                **tool,
                "function": {
                    **tool["function"],
                    "description": f"{tool['function']['description']} {tool_description}",
                },
            }
        body = {
            "model": self._model,
            "messages": messages,
            "tools": [tool],
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
    reader_tokens = _module_reader_tokens(tokens)
    if reader_tokens is not None:
        return reader_tokens
    for index, token in enumerate(tokens):
        if Path(token).name == "loopx":
            command_tokens = tokens[index:]
            if any(token in {";", "&&", "||", "|"} for token in command_tokens):
                return None
            return command_tokens
    return None


def _module_reader_tokens(tokens: list[str]) -> list[str] | None:
    """Normalize native readers while keeping module writes outside the host."""

    if (
        len(tokens) < 5
        or not Path(tokens[0]).name.startswith("python")
        or tokens[1] != "-m"
        or tokens[2] not in {"loopx.entrypoint", "loopx.cli"}
        or any(token in {";", "&&", "||", "|"} for token in tokens)
    ):
        return None
    normalized = ["loopx", *tokens[3:]]
    if "quota" not in normalized:
        return None
    quota = normalized.index("quota")
    if normalized[quota : quota + 2] not in (
        ["quota", "context-read"], ["quota", "replan-view"],
    ):
        return None
    return normalized


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


def is_context_read_command(command: str) -> bool:
    tokens = loopx_command_tokens(command)
    if not tokens or "quota" not in tokens:
        return False
    index = tokens.index("quota")
    return tokens[index:index + 2] == ["quota", "context-read"]


def replan_view_command_tokens(command: str) -> list[str] | None:
    """Normalize one captured replan reader without broadening shell authority.

    The production reader is rendered through ``python -m loopx.entrypoint``
    so it remains independent of the caller's installed console script.  Model
    behavior hosts otherwise accept only the bounded ``loopx`` console form.
    Share module normalization with the captured context reader and return a
    console-shaped argv for the existing isolated CLI executor.
    """

    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if any(token in {";", "&&", "||", "|"} for token in tokens):
        return None
    normalized: list[str] | None = None
    if tokens and Path(tokens[0]).name == "loopx":
        normalized = list(tokens)
    else:
        normalized = _module_reader_tokens(tokens)
    if normalized is None or "quota" not in normalized:
        return None
    quota = normalized.index("quota")
    if normalized[quota : quota + 2] != ["quota", "replan-view"]:
        return None
    return normalized


@dataclass(frozen=True)
class QuotaToolContext:
    """Grade the current capture separately from the model's actual stdout.

    This private oracle read is not a model read, a retained baseline or a
    receipt. Native actors still receive the untouched navigation document;
    model-selected reads count against the original tool-call budget.
    """

    packet: dict[str, Any]
    output: str
    capture_path: Path | None = None
    capture_sha256: str | None = None

    @classmethod
    def from_output(cls, output: str) -> QuotaToolContext:
        from ..turn_driver.decision_capture import (
            read_context_capture_file, read_context_read_reference,
        )

        packet = json.loads(output)
        if not isinstance(packet, dict):
            raise ValueError("quota result must be an object")
        if isinstance(packet.get("interaction_contract"), dict):
            return cls(packet, output)
        recovery = packet.get("recovery")
        if not (packet.get("ok") is True and packet.get("mode") in {"full", "delta"}
                and isinstance(recovery, dict)):
            return cls(packet, output)  # The existing semantic oracle rejects it.
        try:
            reference = recovery
            if "reference_file" in recovery:
                _, reference = read_context_read_reference(recovery["reference_file"])
                if "selection" in reference:
                    raise ValueError("quota recovery must identify the complete current capture")
            if not isinstance(reference.get("path"), str) or not isinstance(reference.get("sha256"), str):
                raise ValueError("quota recovery requires the original capture and digest")
            path, _, current = read_context_capture_file(reference["path"], reference["sha256"])
        except OSError as exc:
            raise ValueError("the original current quota capture could not be read") from exc
        # Always resolve current recovery, never the delta's saved baseline.
        return cls(current, output, path, reference["sha256"])

    def read(self, command: str, *, source_root: Path, project_root: Path) -> str:
        from ..turn_driver.decision_capture import read_context_read_reference

        tokens = loopx_command_tokens(command)
        if not is_context_read_command(command) or not tokens or self.capture_path is None:
            raise ValueError("context read requires the current quota capture")
        # Bind the host's explicit source, as the other behavior hosts do. The
        # real CLI below still owns complete argument and selector validation.
        # Do not import its full presentation/parser graph into the kernel.
        for option in ("--format", "--capture-file", "--capture-sha256", "--reference-file"):
            if tokens.count(option) > 1 or any(token.startswith(option + "=") for token in tokens):
                raise ValueError("context reader requires one literal source argument")
        if (argument_value(tokens, "--format") != "json"
                or any(token.partition("=")[0] in {"--codex-context-dir", "--codex-context-root"} for token in tokens)):
            raise ValueError("context reader must use the original explicit capture")
        path, digest = argument_value(tokens, "--capture-file"), argument_value(tokens, "--capture-sha256")
        reference_file = argument_value(tokens, "--reference-file")
        if reference_file:
            if path or digest:
                raise ValueError("context reader cannot mix reference and capture arguments")
            reader = Path(reference_file).expanduser()
            _, reference = read_context_read_reference(str(reader if reader.is_absolute() else project_root / reader))
            path, digest = reference["path"], reference["sha256"]
        source = Path(path).expanduser() if path else None
        source = (source if source.is_absolute() else project_root / source) if source else None
        if not source or source.resolve() != self.capture_path or digest != self.capture_sha256:
            raise ValueError("context reader does not match the current quota capture")
        return execute_loopx_cli(command, source_root=source_root, project_root=project_root)


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
        raise LoopxCliExecutionError(
            completed.returncode, bounded_detail, stdout=completed.stdout,
        )
    return completed.stdout


class LoopxCliExecutionError(RuntimeError):
    """An executed CLI returned nonzero; this does not imply state rollback."""

    def __init__(self, returncode: int, detail: str, *, stdout: str = "") -> None:
        super().__init__(f"LoopX CLI command failed with exit={returncode}: {detail}")
        self.returncode = returncode
        # Transient tool feedback only. Durable receipts must never serialize
        # this raw output or infer rollback from the presence of valid JSON.
        self.stdout = stdout


def snapshot_disposable_fixture(root: Path) -> dict[str, tuple[Any, ...]]:
    """Observe all fixture files, never infer production rollback from stdout.

    Contents remain transient hashes. Metadata catches replacement, permission
    changes and same-byte rewrites; access times do not make reads into writes.
    Unknown nodes and links fail closed rather than hide effects outside root.
    """
    root = root.resolve(strict=True)
    observed: dict[str, tuple[Any, ...]] = {}
    for path in (root, *root.rglob("*")):
        metadata = path.lstat()
        mode = metadata.st_mode
        identity = (mode, metadata.st_uid, metadata.st_gid, metadata.st_ino,
                    metadata.st_mtime_ns, metadata.st_ctime_ns)
        value: tuple[Any, ...]
        if stat.S_ISDIR(mode):
            value = identity
        elif stat.S_ISREG(mode):
            value = (*identity, sha256(path.read_bytes()).hexdigest())
        else:
            raise ValueError("Disposable fixture contains an unsupported node")
        observed[str(path.relative_to(root))] = value
    return observed
