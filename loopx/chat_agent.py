from __future__ import annotations

import json
import os
import queue
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from .control_plane.turn_driver.codex_worker_identity import CodexWorkerIdentity
from .presentation.codex_activity import CodexActivitySteps
from .chat import (
    CHAT_REVIEW_CLOSE_TAG,
    CHAT_REVIEW_OPEN_TAG,
    VisibleResponseStreamFilter,
    _turn_prompt,
)


class CodexChatAgentError(RuntimeError):
    def __init__(
        self, message: str, *, gate: dict[str, str] | None, error_code: str = "host_gate"
    ) -> None:
        super().__init__(message)
        self.gate = gate
        self.error_code = error_code
        self.native_error_info: dict[str, Any] | str | None = None
        self.native_routing_timeout = False


class CodexChatTimeoutError(CodexChatAgentError):
    pass


class _LegacyModelCatalogSchemaError(RuntimeError):
    pass


def _host_tool_gate(summary: str, next_action: str) -> dict[str, str]:
    return {
        "kind": "host_tool_gate",
        "summary": summary,
        "next_action": next_action,
    }


# One typed endpoint-unavailability error plus the next step for each reason the
# executor readback can publish. A surface that cannot serve a request names the
# blocking fact and what clears it, so an operator never has to infer why an
# endpoint that is listed as a capability refused the session.
AGENT_ENDPOINT_UNAVAILABLE = "agent_endpoint_unavailable"
AGENT_ENDPOINT_NEXT_ACTIONS = {
    "antigravity_unavailable": (
        "Open Antigravity and sign in to its local Language Server, then retry this session."
    ),
    "dsh_runtime_unavailable": (
        "The LoopX Chat service interpreter cannot import deepseek_harness. "
        "Run `loopx doctor` in that service environment and check python.executable; "
        "install `loopx[deepseek-harness]` in the same environment, then restart LoopX Chat."
    ),
    "operator_credential_unconfigured": (
        "Set the managed executor credential (DEEPSEEK_API_KEY, with "
        "DEEPSEEK_BASE_URL when the endpoint is not the provider default) in the "
        "LoopX Chat service environment, then restart it."
    ),
    "invalid_reasoning_effort": (
        "Fix the configured reasoning effort (LOOPX_MANAGER_REASONING_EFFORT or "
        "LOOPX_TURN_REASONING_EFFORT) and restart LoopX Chat."
    ),
}


def agent_endpoint_error(agent_id: str, *, reason: str = "") -> ValueError:
    """Return the typed error for an Agent id this runtime cannot serve.

    A known blocking reason becomes a typed host-tool gate with an actionable
    next step; an unrestricted id keeps the existing untyped fallback.
    """

    if reason:
        return CodexChatAgentError(
            f"The Agent endpoint '{agent_id}' cannot serve this request: {reason}.",
            error_code=AGENT_ENDPOINT_UNAVAILABLE,
            gate=_host_tool_gate(
                f"'{agent_id}' is unavailable: {reason}.",
                AGENT_ENDPOINT_NEXT_ACTIONS.get(
                    reason,
                    "Select another Agent endpoint for this session.",
                ),
            ),
        )
    return ValueError(f"unknown Agent endpoint: {agent_id}")


def _approval_gate(summary: str) -> dict[str, str]:
    return {
        "kind": "approval_gate",
        "summary": summary,
        "next_action": "Review the request in the active host before continuing.",
    }


def _workspace_system_tools(config: dict[str, Any], profile_id: str) -> dict[str, Any]:
    """Adapt Core's workspace profile to public native macOS dependencies.

    Apple's /usr/bin/git dispatches through Xcode, outside Codex's minimal
    system roots. Discover the installed toolchain in the trusted host, never
    from project configuration, inherited DEVELOPER_DIR or the user's PATH.
    """
    if sys.platform != "darwin":
        return config
    try:
        selected = subprocess.run(
            ["/usr/bin/xcode-select", "--print-path"], cwd="/",
            env={"PATH": os.defpath}, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, encoding="utf-8", timeout=2, check=False,
        )
        root = Path(selected.stdout.strip())
        if selected.returncode or root not in {
            Path("/Library/Developer/CommandLineTools"),
            Path("/Applications/Xcode.app/Contents/Developer"),
        }:
            return config
        binary = root / "usr/bin/git"
        # A writable ancestor can replace an otherwise root-owned descendant.
        # Check the complete canonical chain, including effective ACL access.
        for path in (binary, *binary.parents):
            metadata = path.stat()
            expected_type = stat.S_ISREG if path == binary else stat.S_ISDIR
            if (path.resolve() != path or metadata.st_uid != 0
                    or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
                    or os.access(path, os.W_OK)
                    or not expected_type(metadata.st_mode)):
                return config
        if not os.access(binary, os.X_OK):
            return config
    except (OSError, subprocess.TimeoutExpired):
        # Optional native dependency discovery must not open a private path or
        # change the profile when developer tools are missing or unsupported.
        return config
    profiles = config["permissions"]
    profile = profiles[profile_id]
    environment = config["shell_environment_policy"]
    return {
        **config,
        "permissions": {**profiles, profile_id: {
            **profile, "filesystem": {**profile["filesystem"], str(root): "read"},
        }},
        "shell_environment_policy": {**environment, "set": {
            **environment["set"],
            "PATH": str(binary.parent) + os.pathsep + environment["set"]["PATH"],
        }},
    }


def _retry_error_details(error: Any) -> dict[str, Any]:
    """Keep the app-server v2 discriminator, not its private error prose.

    These are provider protocol values, not Core failure classifications. An
    unknown shape keeps the existing generic retry phase and retry behavior.
    """
    info = error.get("codexErrorInfo") if isinstance(error, dict) else None
    if isinstance(info, str):
        if info not in {
            "contextWindowExceeded", "sessionBudgetExceeded", "usageLimitExceeded",
            "rateLimitExceeded", "flexUnavailable", "serverOverloaded", "cyberPolicy",
            "misalignmentPolicyViolation", "tooManyDenials", "internalServerError",
            "unauthorized", "badRequest", "threadRollbackFailed", "sandboxError", "other",
        }:
            return {}
        safe_info: Any = info
    elif isinstance(info, dict) and len(info) == 1:
        variant, detail = next(iter(info.items()))
        if not isinstance(detail, dict):
            return {}
        if variant in {
            "httpConnectionFailed", "responseStreamConnectionFailed",
            "responseStreamDisconnected", "responseTooManyFailedAttempts",
        }:
            safe_detail: dict[str, Any] = {}
            status = detail.get("httpStatusCode")
            if "httpStatusCode" in detail and (
                status is None or (type(status) is int and 0 <= status <= 65535)
            ):
                safe_detail["httpStatusCode"] = status
        elif (
            variant == "activeTurnNotSteerable"
            and isinstance(detail.get("turnKind"), str)
            and detail["turnKind"] in {"review", "compact"}
        ):
            safe_detail = {"turnKind": detail["turnKind"]}
        else:
            return {}
        safe_info = {variant: safe_detail}
    else:
        return {}
    return {"retry": {"codex_error_info": safe_info}}


def _routing_discovery_timeout(error: Any) -> bool:
    # A fixed native provider diagnostic, never a substring/prose retry grant.
    return (isinstance(error, dict) and error.get("codexErrorInfo") == "other"
            and error.get("message") in (
                "workspace routing discovery timed out",
                "Error running remote compact task: workspace routing discovery timed out",
                "workspace routing discovery failed",
                "Error running remote compact task: workspace routing discovery failed",
            ))


def _terminal_turn_error(error: Any, fallback: str) -> CodexChatAgentError:
    """Project only the app-server's typed error, never its arbitrary prose."""
    info = error.get("codexErrorInfo") if isinstance(error, dict) else None
    if _routing_discovery_timeout(error):
        failure = CodexChatAgentError(
            "Codex 上游工作区路由暂时不可用，本轮未完成。",
            error_code="upstream_transport_failure",
            gate=_host_tool_gate(
                "Codex 上游工作区路由暂时不可用，本轮未完成。",
                "保留原请求；仅在独立核验原生执行项、原消息和授权后允许有界接续。",
            ),
        )
        failure.native_routing_timeout = True
        return failure
    # Some Responses providers return a structured error without an HTTP
    # status. The typed error still identifies a rejected request; arbitrary
    # nested prose may contain private request details and is never projected.
    if info == "other" and isinstance(error.get("message"), str):
        try:
            upstream = json.loads(error["message"])
        except (TypeError, ValueError):
            upstream = None
        if (
            isinstance(upstream, dict)
            and ("status" not in upstream or (
                type(upstream["status"]) is int and upstream["status"] == 400
            ))
            and isinstance(upstream.get("error"), dict)
            and upstream["error"].get("type") == "invalid_request_error"
        ):
            summary = "Codex 上游拒绝了本轮请求参数。"
            return CodexChatAgentError(
                summary,
                error_code="upstream_invalid_request",
                gate=_host_tool_gate(
                    summary,
                    "在设置中核对所选模型与 Codex 账户、服务商配置是否匹配，再重试；本次请求不会自动重放。",
                ),
            )
    # App-server v2 exposes camel-case discriminators. Unknown/new variants
    # retain the generic failure; message/additionalDetails may contain private
    # upstream content.
    known = {
        "cyberPolicy": ("cyber_policy", "Codex 上游返回了安全策略拦截，本轮未完成。"),
        "misalignmentPolicyViolation": (
            "misalignment_policy_violation",
            "Codex 上游返回了策略违规拦截，本轮未完成。",
        ),
        "usageLimitExceeded": (
            "usage_limit_exceeded",
            "Codex 上游用量已达限制，本轮未完成。",
        ),
        "rateLimitExceeded": (
            "rate_limit_exceeded",
            "Codex 上游请求频率受限，本轮未完成。",
        ),
        "serverOverloaded": (
            "server_overloaded",
            "当前模型繁忙，本轮未完成。",
        ),
        "contextWindowExceeded": (
            "context_window_exceeded",
            "Codex 上下文超过限制，本轮未完成。",
        ),
        "unauthorized": ("unauthorized", "Codex 上游身份验证失败，本轮未完成。"),
    }
    selected = known.get(info) if isinstance(info, str) else None
    # Reuse the v2 envelope validator. A prior retry is not the terminal
    # classification; only an explicit terminal transport variant selects this.
    safe_info = _retry_error_details(error).get("retry", {}).get("codex_error_info")
    if isinstance(safe_info, dict) and "activeTurnNotSteerable" not in safe_info:
        selected = ("upstream_transport_failure", "Codex 上游响应连接失败，本轮未完成。")
    if selected is None:
        failure = CodexChatAgentError(
            fallback,
            gate=_host_tool_gate(
                fallback, "Inspect the Codex host error before continuing."
            ),
        )
        failure.native_routing_timeout = _routing_discovery_timeout(error)
        return failure
    code, summary = selected
    policy = isinstance(info, str) and info in {"cyberPolicy", "misalignmentPolicyViolation"}
    return CodexChatAgentError(
        summary,
        error_code=code,
        gate={
            "kind": "policy_gate" if policy else "host_tool_gate",
            "summary": summary,
            "next_action": (
                "本轮已终止，不会自动重放；请查看上游说明。"
                if policy
                else "请先核对已有结果，再决定是否稍后重试；本次请求不会自动重放。"
                if code == "server_overloaded"
                else "保留原会话并核对已有结果；连接错误不证明本轮没有产生副作用，不自动重放。"
                if code == "upstream_transport_failure"
                else "请处理对应的上游限制后再继续。"
            ),
        },
    )


def _is_legacy_model_catalog_error(value: Any) -> bool:
    # App-server currently reports catalog schema failures only as JSON-RPC
    # prose. Keep this compatibility classifier bound to its three stable
    # schema tokens until the host exposes a typed configuration error code.
    if not isinstance(value, dict):
        return False
    message = str(value.get("message") or "").lower()
    return (
        "model_catalog_json" in message
        and "missing field" in message
        and "base_instructions" in message
    )


def _model_catalog_compatibility_error() -> CodexChatAgentError:
    return CodexChatAgentError(
        "Codex app-server model catalog compatibility retry failed",
        gate=_host_tool_gate(
            "Codex app-server could not load a compatible model catalog for LoopX Chat.",
            "Update the Codex CLI model catalog or remove its legacy override, then retry the session.",
        ),
    )


@contextmanager
def _current_builtin_model_catalog(codex_bin: str, *, environment: dict[str, str] | None = None) -> Iterator[Path]:
    """Materialize the selected Codex binary's current built-in catalog only."""

    with tempfile.TemporaryDirectory(prefix="loopx-chat-codex-home-") as codex_home:
        env = dict(os.environ if environment is None else environment)
        env["CODEX_HOME"] = codex_home
        try:
            result = subprocess.run(
                [codex_bin, "debug", "models"],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace",
                timeout=15,
                check=False,
            )
            payload = json.loads(result.stdout) if result.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
            raise _model_catalog_compatibility_error() from exc
        models = payload.get("models") if isinstance(payload, dict) else None
        if (
            not isinstance(models, list)
            or not models
            or any(
                not isinstance(model, dict)
                or not str(model.get("base_instructions") or "").strip()
                for model in models
            )
        ):
            raise _model_catalog_compatibility_error()
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix="loopx-chat-model-catalog-",
            suffix=".json",
            delete=False,
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False)
            catalog_path = Path(handle.name)
        try:
            yield catalog_path
        finally:
            catalog_path.unlink(missing_ok=True)


def _reader(
    stream: Any,
    messages: "queue.Queue[dict[str, Any] | Exception]",
    *,
    eof_message: str = "Codex app-server stream closed",
) -> None:
    try:
        for line in stream:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except Exception as exc:  # pragma: no cover - defensive transport path.
                messages.put(exc)
                continue
            if isinstance(payload, dict):
                messages.put(payload)
    finally:
        messages.put(EOFError(eof_message))


def _extract_id(result: dict[str, Any], container: str, fallback: str) -> str:
    nested = result.get(container)
    if isinstance(nested, dict):
        value = nested.get("id") or nested.get(fallback)
        return str(value or "")
    return str(result.get(fallback) or "")


def _event_turn_id(message: dict[str, Any]) -> str:
    params = message.get("params")
    if not isinstance(params, dict):
        return ""
    turn = params.get("turn")
    if isinstance(turn, dict):
        return str(turn.get("id") or turn.get("turnId") or "")
    return str(params.get("turnId") or "")


def _event_thread_id(message: dict[str, Any]) -> str:
    params = message.get("params")
    return str(params.get("threadId") or "") if isinstance(params, dict) else ""


def _agent_item_text(message: dict[str, Any]) -> str:
    params = message.get("params")
    if not isinstance(params, dict):
        return ""
    item = params.get("item")
    if not isinstance(item, dict) or item.get("type") != "agentMessage":
        return ""
    content = item.get("content")
    if isinstance(content, list):
        return "".join(
            str(part.get("text") or "")
            for part in content
            if isinstance(part, dict) and part.get("type") in {"text", "outputText"}
        )
    return str(item.get("text") or "")


@dataclass
class CodexChatAgentSession:
    process: subprocess.Popen[str]
    messages: "queue.Queue[dict[str, Any] | Exception]"
    thread_id: str
    work_dir: Path
    context_summary: str = ""
    execution_mode: bool = False
    process_tree_owned: bool = False
    runtime_profile: str = "restricted"
    sandbox: str = "read-only"
    permissions_profile: str | None = None
    project_context: dict[str, str] | None = None
    model: str | None = None
    resolved_model: str | None = None
    native_model_provider: str | None = None
    reasoning_effort: str | None = None
    resolved_reasoning_effort: str | None = None
    service_tier: str | None = None
    response_timeout_sec: float = 30.0
    idle_timeout_sec: float | None = None
    hard_timeout_sec: float | None = None
    next_request_id: int = 5
    current_turn_id: str = ""
    model_catalog_compatibility_applied: bool = False
    team_plan_context: Mapping[str, Any] | None = field(default=None, repr=False)
    read_tool_handler: Callable[[str, Any], dict[str, Any]] | None = field(
        default=None, repr=False
    )
    bound_tool_handler: Callable[[str, Any, dict[str, Any]], dict[str, Any]] | None = (
        field(default=None, repr=False)
    )
    _pending_events: "queue.Queue[dict[str, Any]]" = field(
        default_factory=queue.Queue, repr=False
    )
    _response_waiters: dict[int, "queue.Queue[dict[str, Any]]"] = field(
        default_factory=dict,
        repr=False,
    )
    _write_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _request_id_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _response_waiters_lock: threading.Lock = field(
        default_factory=threading.Lock, repr=False
    )
    _message_dispatch_lock: threading.Lock = field(
        default_factory=threading.Lock, repr=False
    )
    _command_items: dict[str, set[str]] = field(default_factory=dict, repr=False)
    _command_items_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _host_model_auth: Any = field(default=None, repr=False)
    _public_source_read_context: str = field(default="", repr=False)
    read_only_review: bool = False
    answer_review: bool = False
    metadata_review: bool = False
    _launch_guard: Callable[[], AbstractContextManager[None]] | None = field(
        default=None, repr=False
    )
    _native_task_policy_used: bool = field(default=False, repr=False)
    _managed_goal_read_default: bool = field(default=False, repr=False)

    @classmethod
    def start(
        cls,
        *,
        codex_bin: str,
        work_dir: Path,
        goal_id: str | None,
        objective: str,
        response_timeout_sec: float = 30.0,
        idle_timeout_sec: float | None = None,
        hard_timeout_sec: float | None = None,
        resume_thread_id: str | None = None,
        fork_thread_id: str | None = None,
        expected_native_provider: str | None = None,
        execution_mode: bool = False,
        isolate_process_tree: bool = False,
        runtime_profile: str = "restricted",
        sandbox: str | None = None,
        project_context: dict[str, str] | None = None,
        read_only_review: bool = False,
        metadata_review_context: dict[str, Any] | None = None,
        answer_review_context: dict[str, Any] | None = None,
        answer_review_host_read: bool = False,
        codex_home: Path | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        dynamic_tools: list[dict[str, Any]] | None = None,
        host_config: dict[str, Any] | None = None,
        launch_guard: Callable[[], AbstractContextManager[None]] | None = None,
        account_runtime_root: Path | None = None,
        account_consumer: str = "worker",
        worker_identity: CodexWorkerIdentity | None = None,
        _compatibility_catalog_path: Path | None = None,
    ) -> "CodexChatAgentSession":
        resolved = shutil.which(codex_bin)
        if not resolved:
            raise CodexChatAgentError(
                "Codex executable is unavailable",
                gate=_host_tool_gate(
                    "Codex host tool is unavailable for LoopX Chat.",
                    "Install or select a working Codex CLI, then start LoopX Chat again.",
                ),
            )
        root = work_dir.resolve()
        if runtime_profile not in {"restricted", "trusted_owner"}:
            raise ValueError("unsupported Codex Chat runtime profile")
        if fork_thread_id and (
            resume_thread_id or execution_mode or read_only_review or project_context
            or metadata_review_context or answer_review_context or not expected_native_provider
        ):
            raise ValueError("Codex history fork requires an authorized ordinary native thread")
        existing_thread_id = resume_thread_id or fork_thread_id
        from .capabilities.native_chat import codex_context
        policy, selected_sandbox = codex_context.native_conversation_policy(
            root=root, metadata_review_context=metadata_review_context, answer_review_context=answer_review_context,
            answer_review_host_read=answer_review_host_read, read_only_review=read_only_review,
            project_context=project_context, goal_id=goal_id, execution_mode=execution_mode,
            runtime_profile=runtime_profile, dynamic_tools=dynamic_tools, resume_thread_id=existing_thread_id,
            sandbox=sandbox)
        # Core owns the optional narrower project filesystem policy. A model,
        # transport or project config cannot substitute a profile of its choice.
        permissions_profile = policy.get("permissions_profile") if project_context is not None or metadata_review_context is not None or answer_review_context is not None else None
        if permissions_profile:
            host_config = {**(host_config or {}), **policy["host_config"]}
            # Git must not consult private account/system configuration from
            # workspace-only tools. Keep repository-local config available;
            # ordinary Chat retains its existing account configuration.
            environment = host_config["shell_environment_policy"]
            host_config["shell_environment_policy"] = {**environment, "set": {
                **environment["set"],
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_NOSYSTEM": "1",
            }, "include_only": [
                *environment["include_only"], "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM",
            ]}
            host_config = _workspace_system_tools(host_config, permissions_profile)
            # The native filesystem helper re-executes this binary. A symlink
            # under the user's home must not require opening that directory.
            resolved = str(Path(resolved).resolve())
        owner_git_write = (project_context is not None and not permissions_profile
            and selected_sandbox == "workspace-write"
            and (root / ".git").is_dir() and not (root / ".git").is_symlink())
        owner_git_write_roots = [str(root / ".git")] if owner_git_write else []
        # Repository skills are project content, but Codex protects all of
        # .codex recursively by default. Map only the physical skills subtree
        # for ordinary project writes; never open project config or follow a
        # linked instruction directory into another workspace/account.
        if owner_git_write and all(path.is_dir() and not path.is_symlink()
                                   for path in (root / ".codex", root / ".codex" / "skills")):
            owner_git_write_roots.append(str(root / ".codex" / "skills"))
        # Pin the host store explicitly, including compatibility retries. Never
        # redirect an existing thread by inheriting a different launch context.
        base_home, runtime_home, runtime_env = codex_context.process_profile(
            codex_home, policy if project_context is not None else None, bool(permissions_profile), worker_identity)
        # Select an operator-defined native provider for ordinary Chat only.
        # Codex owns its configuration/authentication; never copy credentials
        # or replace a resumed thread to change its upstream transport.
        provider_override = (
            runtime_env.get("LOOPX_CHAT_CODEX_MODEL_PROVIDER", "").strip()
            if not execution_mode else ""
        )
        account_base_config = host_config
        from .capabilities.native_chat import codex_auth
        host_config, provider_override, api_transport, retain_transport = codex_auth.prepare_conversation_transport(
            account_runtime_root, base_home, runtime_home, existing_thread_id, current=host_config or {},
            provider=provider_override, eligible=not execution_mode, consumer=account_consumer,
            model=model, environment=runtime_env)
        command = [resolved, "app-server"]
        if permissions_profile or worker_identity is not None:
            # Keep this native store independent even when the account's
            # default credential backend is a shared OS keychain.
            command.extend(["-c", 'cli_auth_credentials_store="file"'])
        if api_transport is not None or retain_transport:
            command.extend(codex_auth.transport_arguments(host_config))
        if _compatibility_catalog_path is not None:
            command.extend(
                [
                    "-c",
                    f"model_catalog_json={json.dumps(str(_compatibility_catalog_path))}",
                ]
            )
        command.extend(["--listen", "stdio://"])
        if worker_identity is not None:
            command = [worker_identity.setpriv, "--no-new-privs", "--", *command]
        process = None
        try:
            # The admitting caller owns this short launch fence. Native startup
            # RPCs and model execution must not hold the Goal writer lock.
            with launch_guard() if launch_guard is not None else nullcontext():
                try:
                    process = subprocess.Popen(
                        command,
                        cwd=str(root),
                        env=runtime_env,
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                        text=True,
                        encoding="utf-8",
                        bufsize=1,
                        start_new_session=isolate_process_tree and os.name == "posix",
                        **(worker_identity.process_options if worker_identity is not None else {}),
                    )
                except OSError as exc:
                    raise CodexChatAgentError(
                        "Codex app-server could not start",
                        gate=_host_tool_gate(
                            "Codex app-server could not start for LoopX Chat.",
                            "Verify the Codex CLI installation and app-server support.",
                        ),
                    ) from exc
        except BaseException:
            # A guard can fail on exit after Popen succeeded, before a Session
            # exists to own cleanup. Reap that same process on this path too.
            if process is not None:
                cls._close_process(process, process_tree_owned=isolate_process_tree)
            raise

        messages: "queue.Queue[dict[str, Any] | Exception]" = queue.Queue()
        assert process.stdout is not None
        reader = threading.Thread(
            target=_reader, args=(process.stdout, messages), daemon=True
        )
        reader.start()
        session = cls(
            process=process,
            messages=messages,
            thread_id="",
            work_dir=root,
            context_summary=f"{goal_id}: {objective}".strip() if goal_id is not None else objective.strip(),
            response_timeout_sec=response_timeout_sec,
            idle_timeout_sec=idle_timeout_sec,
            hard_timeout_sec=hard_timeout_sec,
            execution_mode=execution_mode,
            process_tree_owned=isolate_process_tree,
            runtime_profile=runtime_profile,
            sandbox=selected_sandbox,
            permissions_profile=permissions_profile,
            read_only_review=read_only_review,
            answer_review=answer_review_context is not None,
            metadata_review=metadata_review_context is not None,
            project_context=policy["context"] if project_context is not None else None,
            model=model,
            reasoning_effort=reasoning_effort,
            model_catalog_compatibility_applied=_compatibility_catalog_path is not None,
            _launch_guard=launch_guard,
        )
        try:
            session._request(
                "initialize",
                {
                    "clientInfo": {
                        "name": "loopx_chat",
                        "title": "LoopX Chat",
                        "version": "0.1.0",
                    },
                    "capabilities": {"experimentalApi": True},
                },
                request_id=1,
            )
            session._notify("initialized", {})
            codex_auth.authenticate_api_transport(session, api_transport)
            if permissions_profile:
                session._host_model_auth = codex_auth.authenticate_isolated(session, base_home, runtime_home, resolved, api_transport=api_transport)
            read_project_defaults = project_context is not None and bool(resume_thread_id) and (
                model is None or reasoning_effort is None
            )
            # A resumed native thread can retain yesterday's API provider after
            # the account owner has switched back to a subscription (or vice
            # versa). Follow Codex's effective route without selecting accounts,
            # copying auth, restarting clients or replacing the original thread.
            follow_native_provider = bool(existing_thread_id and not execution_mode
                and (not permissions_profile or metadata_review_context is not None) and not provider_override)
            read_native_config = read_project_defaults or bool(permissions_profile) or owner_git_write or follow_native_provider or api_transport is not None or retain_transport
            if read_native_config:
                # Resume otherwise inherits the old thread's model/effort, even
                # when the owner's effective workspace configuration has changed.
                # Let Codex resolve trusted layers; never copy sandbox or approval
                # settings from project files over the existing Core grant.
                config_result = session._request(
                    "config/read", {"cwd": str(root), "includeLayers": bool(api_transport or retain_transport)},
                    request_id=3,
                )
                effective = config_result.get("config", {})
                if not isinstance(effective, dict):
                    raise session._runtime_error("Codex returned an invalid project configuration.")
                lower_transport = codex_auth.lower_transport_configuration(config_result)
                if api_transport is not None:
                    host_config = api_transport.configuration(host_config or {}, lower_transport)
                elif retain_transport and provider_override in lower_transport.get("model_providers", {}):
                    raise session._runtime_error("Native host ChatGPT transport conflicts with project configuration.")
                if owner_git_write:
                    # Preserve the native workspace policy (including network
                    # and temporary-directory settings). A separate named
                    # profile does not inherit these legacy host settings.
                    configured = effective.get("sandbox_workspace_write")
                    requested = (host_config or {}).get("sandbox_workspace_write")
                    configured = {} if configured is None else configured
                    requested = {} if requested is None else requested
                    if not isinstance(configured, dict) or not isinstance(requested, dict):
                        raise session._runtime_error("Invalid native workspace-write configuration.")
                    settings = {**configured, **requested}
                    writable = settings.get("writable_roots", [])
                    if not isinstance(writable, list) or any(not isinstance(path, str) for path in writable):
                        raise session._runtime_error("Invalid native workspace write roots.")
                    host_config = {**(host_config or {}), "sandbox_workspace_write": {
                        **settings, "writable_roots": list(dict.fromkeys([*writable, *owner_git_write_roots]))}}
                if permissions_profile:
                    host_config = codex_context.disable_mcp_servers(effective, host_config or {})
                    if not read_only_review:
                        try:
                            host_config = codex_context.public_source_reader(effective, host_config)
                        except ValueError as exc:
                            raise session._runtime_error(str(exc)) from None
                    if session._host_model_auth is not None:
                        # Use public native transport defaults, not the
                        # account's configuration or environment. Reject a
                        # lower-layer alias collision before model dispatch.
                        try:
                            host_config = codex_context.shared_chatgpt_transport(host_config, effective)
                        except ValueError:
                            raise session._runtime_error(
                                "Native host ChatGPT transport conflicts with project configuration."
                            ) from None
                        provider_override = host_config["model_provider"]
                selected = {**effective, **(host_config or {})}
                if follow_native_provider:
                    provider = selected.get("model_provider")
                    if provider is not None and (not isinstance(provider, str) or not provider.strip()):
                        raise session._runtime_error("Codex returned an invalid native provider.")
                    provider_override = provider or ""
                if read_project_defaults and model is None:
                    model = selected.get("model")
                if read_project_defaults and reasoning_effort is None:
                    reasoning_effort = selected.get("model_reasoning_effort")
                if any(value is not None and (not isinstance(value, str) or not value.strip())
                       for value in (model, reasoning_effort)):
                    raise session._runtime_error("Codex returned invalid project model settings.")
            if expected_native_provider and provider_override and provider_override != expected_native_provider:
                raise session._runtime_error("Codex native history belongs to a different model provider.")
            thread_method = "thread/fork" if fork_thread_id else "thread/resume" if resume_thread_id else "thread/start"
            thread_result = session._request(
                thread_method,
                {
                    **(
                        {"threadId": existing_thread_id, "excludeTurns": True}
                        if existing_thread_id
                        else {}
                    ),
                    "cwd": str(root),
                    # An ordinary native thread can retain Git/skill roots
                    # from earlier writable Turns. Replace, then verify the
                    # exact root rather than inheriting that broader inventory.
                    **({"runtimeWorkspaceRoots": [str(root)]} if read_only_review else {}),
                    **({"model": model} if model else {}),
                    **({"modelProvider": provider_override} if provider_override else {}),
                    **(
                        {
                            "config": {
                                **(host_config or {}),
                                **(
                                    {"model_reasoning_effort": reasoning_effort}
                                    if reasoning_effort
                                    else {}
                                ),
                            }
                        }
                        if reasoning_effort or host_config
                        else {}
                    ),
                    **({"permissions": permissions_profile} if permissions_profile
                       else {"sandbox": selected_sandbox}),
                    "approvalPolicy": "never",
                    **(
                        {"dynamicTools": dynamic_tools}
                        if dynamic_tools and not existing_thread_id
                        else {}
                    ),
                },
                request_id=2,
            )
            codex_context.verify_conversation_provider(session, provider_override, thread_result)
            native_provider = thread_result.get("modelProvider")
            session.native_model_provider = native_provider if isinstance(native_provider, str) and native_provider else None
            if expected_native_provider is not None and session.native_model_provider != expected_native_provider:
                raise session._runtime_error("Codex native history belongs to a different model provider.")
            for requested_value, result_key, label in (
                (model, "model", "model"),
                (reasoning_effort, "reasoningEffort", "reasoning effort"),
            ):
                if requested_value and thread_result.get(result_key) not in {None, requested_value}:
                    raise CodexChatAgentError(
                        f"Codex did not apply the requested conversation {label}.",
                        error_code="unsupported_configuration", gate=None,
                    )
            session.model = thread_result.get("model") or model
            session.resolved_model = thread_result.get("model")
            session.reasoning_effort = thread_result.get("reasoningEffort") or reasoning_effort
            session.resolved_reasoning_effort = thread_result.get("reasoningEffort")
            session.service_tier = thread_result.get("serviceTier")
            requested_tier = (host_config or {}).get("service_tier")
            # Native Fast mode may report its equivalent API tier, priority.
            if requested_tier and session.service_tier not in {None, requested_tier} and not (
                requested_tier == "fast" and session.service_tier == "priority"
            ):
                raise session._runtime_error(f"Codex did not apply the requested conversation service tier (reported {session.service_tier}).")
            session.thread_id = _extract_id(thread_result, "thread", "threadId")
            if permissions_profile:
                active = thread_result.get("activePermissionProfile")
                if not isinstance(active, dict) or active.get("id") != permissions_profile:
                    raise session._runtime_error("Codex did not apply the project filesystem permissions.")
                roots = thread_result.get("runtimeWorkspaceRoots")
                if not isinstance(roots, list) or roots != [str(root)]:
                    raise session._runtime_error("Codex did not apply the exact project workspace root.")
            if owner_git_write:
                applied = thread_result.get("sandbox")
                applied_roots = applied.get("writableRoots") if isinstance(applied, dict) else None
                if (not isinstance(applied, dict) or applied.get("type") != "workspaceWrite"
                        or not isinstance(applied_roots, list)
                        or any(path not in applied_roots for path in owner_git_write_roots)):
                    raise session._runtime_error("Codex did not apply the project Git write roots.")
            if not session.thread_id:
                raise session._runtime_error(
                    "Codex app-server did not return a thread id."
                )
            if resume_thread_id and session.thread_id != resume_thread_id:
                raise session._runtime_error(
                    "Codex app-server resumed an unexpected thread."
                )
            if fork_thread_id:
                native_thread = thread_result.get("thread")
                sandbox_type = {
                    "read-only": "readOnly",
                    "workspace-write": "workspaceWrite",
                    "danger-full-access": "dangerFullAccess",
                }.get(selected_sandbox)
                applied_sandbox = thread_result.get("sandbox")
                if (session.thread_id == fork_thread_id or not isinstance(native_thread, dict)
                        or native_thread.get("forkedFromId") != fork_thread_id
                        or thread_result.get("cwd") != str(root) or thread_result.get("approvalPolicy") != "never"
                        or sandbox_type is None or not isinstance(applied_sandbox, dict)
                        or applied_sandbox.get("type") != sandbox_type):
                    raise session._runtime_error("Codex did not verify the original thread and policy of its history fork.")
            if permissions_profile:
                session._public_source_read_context = codex_context.public_source_read_context(host_config or {})
            if read_only_review and session._background_terminals(time.monotonic() + response_timeout_sec):
                raise session._runtime_error("The interrupted conversation still has background work; result review stopped.")
            # Chat keeps its Goal binding in LoopX's local Session state and supplies
            # that public-safe context in each Turn prompt. Codex Goal mode is reserved
            # for autonomous execution; enabling it here causes conversational messages
            # to be treated as continuation ticks instead of the current user task.
            session.next_request_id = 4 if read_native_config else 3
            if session._host_model_auth is not None or api_transport is not None and api_transport.provider == "openai":
                session.next_request_id = 5
            if read_only_review:
                session.next_request_id = max(session.next_request_id, 6)
            return session
        except _LegacyModelCatalogSchemaError as exc:
            session.close()
            if _compatibility_catalog_path is not None or fork_thread_id or worker_identity is not None:
                raise _model_catalog_compatibility_error() from exc
            with _current_builtin_model_catalog(resolved, environment=runtime_env) as catalog_path:
                return cls.start(
                    codex_bin=resolved,
                    work_dir=root,
                    goal_id=goal_id,
                    objective=objective,
                    response_timeout_sec=response_timeout_sec,
                    idle_timeout_sec=idle_timeout_sec,
                    hard_timeout_sec=hard_timeout_sec,
                    resume_thread_id=resume_thread_id,
                    expected_native_provider=expected_native_provider,
                    execution_mode=execution_mode,
                    isolate_process_tree=isolate_process_tree,
                    runtime_profile=runtime_profile,
                    sandbox=selected_sandbox,
                    project_context=project_context,
                    read_only_review=read_only_review,
                    metadata_review_context=metadata_review_context,
                    answer_review_context=answer_review_context,
                    answer_review_host_read=answer_review_host_read,
                    codex_home=base_home,
                    model=model,
                    reasoning_effort=reasoning_effort,
                    dynamic_tools=dynamic_tools,
                    host_config=account_base_config if api_transport is not None or retain_transport else host_config,
                    launch_guard=launch_guard,
                    account_runtime_root=account_runtime_root,
                    account_consumer=account_consumer,
                    _compatibility_catalog_path=catalog_path,
                )
        except Exception:
            session.close()
            raise

    def _runtime_error(self, summary: str) -> CodexChatAgentError:
        return CodexChatAgentError(
            summary,
            gate=_host_tool_gate(
                summary,
                "Check the Codex app-server host capability, then retry the session.",
            ),
        )

    def _write(
        self, payload: dict[str, Any], *,
        write_guard: Callable[[], AbstractContextManager[None]] | None = None,
        deadline: float | None = None,
    ) -> None:
        if self.process.stdin is None:
            raise self._runtime_error("Codex app-server input stream is closed.")
        frame = json.dumps(payload, ensure_ascii=False) + "\n"
        try:
            if write_guard is not None:
                self._guarded_write(frame, write_guard, deadline=deadline)
                return
            with self._write_lock:
                self.process.stdin.write(frame)
                self.process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise self._runtime_error(
                "Codex app-server input stream closed unexpectedly."
            ) from exc

    def _guarded_write(
        self, frame: str, write_guard: Callable[[], AbstractContextManager[None]], *,
        deadline: float | None,
    ) -> None:
        from .extensions.process_runtime import force_terminate_process, write_process_stdin_before_deadline

        # Serialization, delivery and response share the original RPC/Turn
        # deadline. No shorter send budget may reject an otherwise valid frame.
        dispatch_deadline = deadline if deadline is not None else time.monotonic() + self.response_timeout_sec
        if not self._write_lock.acquire(timeout=max(0, dispatch_deadline - time.monotonic())):
            raise self._timeout_error("response_timeout", "Codex input dispatch timed out.")
        try:
            with write_guard():
                process = self.process
                try:
                    if time.monotonic() >= dispatch_deadline:
                        # Canonical admission itself can consume the remaining
                        # budget. No writer started, but this Session still
                        # retires its peer before releasing the launch fence.
                        try:
                            force_terminate_process(process, process_tree_owned=self.process_tree_owned)
                        except Exception as error:
                            raise RuntimeError("native input receiver cleanup unconfirmed") from error
                        raise TimeoutError("native input dispatch deadline expired")
                    write_process_stdin_before_deadline(
                        process, frame, deadline=dispatch_deadline,
                        cancel_and_reap=lambda: force_terminate_process(
                            process, process_tree_owned=self.process_tree_owned,
                        ),
                    )
                except TimeoutError as exc:
                    raise self._timeout_error(
                        "response_timeout", "Codex input dispatch timed out."
                    ) from exc
                except RuntimeError as exc:
                    raise self._runtime_error("Codex native input cleanup could not be confirmed.") from exc
        finally:
            self._write_lock.release()

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._write({"method": method, "params": params})

    def _unwrap_stream_message(self, raw: dict[str, Any] | Exception) -> dict[str, Any]:
        if isinstance(raw, EOFError):
            raise self._runtime_error(
                "Codex app-server closed before completing the request."
            )
        if isinstance(raw, Exception):
            raise self._runtime_error(
                "Codex app-server returned an unreadable response."
            )
        return raw

    def _next_message(self, *, deadline: float | None = None, check: Callable[[], None] | None = None) -> dict[str, Any]:
        while True:
            if check is not None:
                check()
            if deadline is None:
                timeout = 0.5
            else:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise self._runtime_error("Codex app-server timed out.")
                timeout = min(0.5, remaining)
            try:
                message = self.messages.get(timeout=timeout)
            except queue.Empty:
                continue
            return self._unwrap_stream_message(message)

    def _route_response(self, message: dict[str, Any]) -> bool:
        response_id = message.get("id")
        if message.get("method") or not isinstance(response_id, int):
            return False
        with self._response_waiters_lock:
            waiter = self._response_waiters.get(response_id)
        if waiter is None:
            return False
        waiter.put(message)
        return True

    def _next_event(self, *, deadline: float | None = None, check: Callable[[], None] | None = None) -> dict[str, Any]:
        while True:
            if check is not None:
                check()
            with self._message_dispatch_lock:
                try:
                    return self._pending_events.get_nowait()
                except queue.Empty:
                    pass
                message = self._next_message(deadline=deadline, **({"check": check} if check is not None else {}))
                if self._route_response(message):
                    continue
                self._observe_command_item(message)
                return message

    def _observe_command_item(self, message: dict[str, Any]) -> None:
        # Native history can omit an aborted command. Keep its wire identity,
        # including completed items, until the next Turn binds. This is not an
        # OS PID or an inference from a command string.
        if _event_thread_id(message) != self.thread_id:
            return
        turn_id = _event_turn_id(message)
        params = message.get("params")
        if not turn_id or not isinstance(params, dict):
            return
        method = message.get("method")
        item = params.get("item")
        item_id = None
        if method in {"item/started", "item/completed"} and isinstance(item, dict) and item.get("type") == "commandExecution":
            item_id = item.get("id")
        elif method == "item/commandExecution/outputDelta":
            item_id = params.get("itemId")
        if isinstance(item_id, str) and item_id:
            with self._command_items_lock:
                self._command_items.setdefault(turn_id, set()).add(item_id)

    def _check_server_gate(self, message: dict[str, Any]) -> bool:
        if message.get("id") is not None and message.get("method") == "account/chatgptAuthTokens/refresh" and self._host_model_auth is not None:
            try:
                params = message.get("params") or {}
                if not isinstance(params, dict) or params.get("reason") != "unauthorized":
                    raise ValueError("invalid native refresh request")
                previous = params.get("previousAccountId")
                if not isinstance(previous, str) or not previous:
                    raise ValueError("missing native account identity")
                result = self._host_model_auth.read(refresh=True, previous_account_id=previous)
                self._write({"id": message["id"], "result": result})
            except Exception:
                self._write({"id": message["id"], "error": {
                    "code": -32000, "message": "Trusted-host model authentication unavailable."}})
            return True
        if (
            message.get("id") is not None
            and message.get("method") == "item/tool/call"
            and (
                self.read_tool_handler
                or (self.execution_mode and self.bound_tool_handler)
            )
        ):
            params = message.get("params") or {}
            valid = (
                isinstance(params, dict)
                and params.get("threadId") == self.thread_id
                and bool(self.current_turn_id)
                and params.get("turnId") == self.current_turn_id
                and params.get("namespace") is None
            )
            try:
                result = (
                    (
                        self.bound_tool_handler(
                            params.get("tool", ""),
                            params.get("arguments"),
                            {
                                "thread_id": params["threadId"],
                                "host_turn_id": params["turnId"],
                                "call_id": params.get("callId"),
                            },
                        )
                        if self.execution_mode and self.bound_tool_handler
                        else self.read_tool_handler(
                            params.get("tool", ""), params.get("arguments")
                        )
                    )
                    if valid
                    else {
                        "ok": False,
                        "error": "tool_turn_mismatch",
                    }
                )
            except Exception:
                result = {"ok": False, "error": "read_tool_unavailable"}
            self._write(
                {
                    "id": message["id"],
                    "result": {
                        "contentItems": [
                            {
                                "type": "inputText",
                                "text": json.dumps(result, ensure_ascii=False),
                            }
                        ],
                        "success": result.get("ok") is True,
                    },
                }
            )
            return True
        if message.get("id") is not None and message.get("method"):
            raise CodexChatAgentError(
                "Codex app-server requested host approval",
                gate=_approval_gate(
                    "Codex requested host approval beyond this Chat session's configured grant."
                ),
            )
        return False

    def _request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        request_id: int | None = None,
        deadline: float | None = None,
        write_guard: Callable[[], AbstractContextManager[None]] | None = None,
    ) -> dict[str, Any]:
        if request_id is None:
            with self._request_id_lock:
                request_id = self.next_request_id
                self.next_request_id += 1
        waiter: "queue.Queue[dict[str, Any]]" = queue.Queue(maxsize=1)
        with self._response_waiters_lock:
            self._response_waiters[request_id] = waiter
        try:
            response_deadline = time.monotonic() + self.response_timeout_sec
            deadline = min(deadline, response_deadline) if deadline is not None else response_deadline
            # A per-Turn guard replaces the session default. Acquire it only
            # inside the actual write, after waiting for the native write lock.
            if write_guard is None and method == "turn/start":
                write_guard = self._launch_guard
            self._write(
                {"id": request_id, "method": method, "params": params},
                **({"write_guard": write_guard, "deadline": deadline} if write_guard is not None else {}),
            )
            while True:
                if time.monotonic() >= deadline:
                    raise self._timeout_error("response_timeout", "Codex app-server timed out.")
                try:
                    message = waiter.get_nowait()
                except queue.Empty:
                    # The reader can route our response while retaining the
                    # dispatch fence for its next event. Wait on the response,
                    # which wakes immediately, rather than on that reader's lock.
                    if not self._message_dispatch_lock.acquire(blocking=False):
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            continue
                        try:
                            message = waiter.get(timeout=min(0.1, remaining))
                        except queue.Empty:
                            continue
                        break
                    try:
                        try:
                            message = waiter.get_nowait()
                        except queue.Empty:
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                raise self._timeout_error(
                                    "response_timeout", "Codex app-server timed out."
                                )
                            try:
                                raw = self.messages.get(timeout=min(0.1, remaining))
                            except queue.Empty:
                                continue
                            message = self._unwrap_stream_message(raw)
                            if message.get("method") and self._check_server_gate(
                                message
                            ):
                                continue
                            if message.get("id") != request_id and self._route_response(
                                message
                            ):
                                continue
                            if message.get("id") != request_id:
                                self._observe_command_item(message)
                                self._pending_events.put(message)
                                continue
                    finally:
                        self._message_dispatch_lock.release()
                break
            if message.get("id") == request_id:
                if message.get("error"):
                    if method in {
                        "thread/start",
                        "thread/resume",
                    } and _is_legacy_model_catalog_error(message.get("error")):
                        raise _LegacyModelCatalogSchemaError
                    raise self._runtime_error(
                        f"Codex app-server rejected {method}."
                    )
                result = message.get("result")
                return result if isinstance(result, dict) else {}
            raise self._runtime_error(
                "Codex app-server returned an unexpected response."
            )
        finally:
            with self._response_waiters_lock:
                self._response_waiters.pop(request_id, None)

    def compact_history(self, *, check: Callable[[], None] | None = None) -> int | None:
        from .capabilities.native_chat.codex_adapter import compact_native_history
        return compact_native_history(self, check=check)

    def steer(self, user_message: str, *, expected_turn_id: str,
              client_message_id: str | None = None) -> str:
        """Inject one user message into the exact active Codex Turn."""

        text = str(user_message or "").strip()
        selected_turn_id = str(expected_turn_id or "").strip()
        if not text or not selected_turn_id:
            raise ValueError("steering requires a message and expected active turn id")
        result = self._request(
            "turn/steer",
            {
                "threadId": self.thread_id,
                "expectedTurnId": selected_turn_id,
                **({"clientUserMessageId": client_message_id} if client_message_id is not None else {}),
                # The native Turn already has its task and policy. Replaying
                # the start prompt would reframe this update as a new task.
                "input": [{"type": "text", "text": text}],
            },
        )
        turn_id = _extract_id(result, "turn", "turnId")
        if turn_id != selected_turn_id:
            raise self._runtime_error("Codex app-server steered an unexpected turn.")
        return turn_id

    def interrupt(self, turn_id: str | None = None) -> None:
        selected_turn_id = str(turn_id or self.current_turn_id or "").strip()
        if not selected_turn_id:
            return
        deadline = time.monotonic() + self.response_timeout_sec
        interrupted = False
        while True:
            native_turn = next((turn for turn in self._control_items(
                "thread/turns/list", {"sortDirection": "desc", "itemsView": "summary"}, deadline
            ) if turn.get("id") == selected_turn_id), None)
            if native_turn is None:
                raise self._runtime_error("Codex could not confirm the selected Turn.")
            if native_turn.get("status") in {"completed", "interrupted", "failed"}:
                break
            if native_turn.get("status") != "inProgress":
                raise self._runtime_error("Codex returned an unknown Turn status.")
            if not interrupted:
                self._request("turn/interrupt", {
                    "threadId": self.thread_id, "turnId": selected_turn_id,
                }, deadline=deadline)
                interrupted = True
            # Wait for the producer to stop before inventorying its terminals;
            # an acknowledged interrupt alone still allows late command items.
            self._control_wait(deadline)

        owned_items = self._command_item_ids(selected_turn_id, deadline)

        def owned_terminals() -> list[dict[str, Any]]:
            return [terminal for terminal in self._background_terminals(deadline)
                    if terminal["itemId"] in owned_items]

        for terminal in owned_terminals():
            result = self._request("thread/backgroundTerminals/terminate", {
                "threadId": self.thread_id, "processId": terminal["processId"],
            }, deadline=deadline)
            if not isinstance(result.get("terminated"), bool):
                raise self._runtime_error("Codex did not acknowledge terminal cancellation.")
        # A false receipt can mean the process exited concurrently. Only the
        # native inventory readback, never transport closure, confirms stop.
        while owned_terminals():
            self._control_wait(deadline)

    def _validated_command_item_id(self, item: dict[str, Any]) -> str | None:
        if item.get("type") != "commandExecution":
            return None
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id:
            raise self._runtime_error("Codex returned an unbound command item.")
        return item_id

    def background_turn_ids(self, turn_ids: list[str]) -> list[str]:
        """Observe live terminals owned by the supplied canonical native Turns."""
        candidates = list(dict.fromkeys(turn_ids))
        if not candidates:
            return []
        deadline = time.monotonic() + self.response_timeout_sec
        live_items = {terminal["itemId"] for terminal in self._background_terminals(deadline)}
        if not live_items:
            return []
        # The native endpoint can return all items in this exact thread. Scan
        # its complete pagination once instead of one round trip per historical
        # Turn; newer/foreign Turns never enter the frozen candidate set.
        owners: dict[str, str] = {}
        commands: dict[str, set[str]] = {turn_id: set() for turn_id in candidates}

        def record_command(turn_id: str, item_id: str, *, filter_candidates: bool) -> None:
            if item_id in owners and owners[item_id] != turn_id:
                raise self._runtime_error("Codex returned conflicting command ownership.")
            owners[item_id] = turn_id
            if not filter_candidates or turn_id in commands:
                commands[turn_id].add(item_id)

        for entry in self._control_items("thread/items/list", {}, deadline):
            turn_id, item = entry.get("turnId"), entry.get("item")
            if not isinstance(turn_id, str) or not turn_id or not isinstance(item, dict):
                raise self._runtime_error("Codex returned an unbound thread item.")
            if (item_id := self._validated_command_item_id(item)) is not None:
                record_command(turn_id, item_id, filter_candidates=True)
        # RPC dispatch can observe command events omitted by native history.
        # Keep their existing exact thread/Turn proof, within this observation.
        with self._command_items_lock:
            for turn_id in commands:
                for item_id in self._command_items.get(turn_id, set()):
                    record_command(turn_id, item_id, filter_candidates=False)
        return [turn_id for turn_id in candidates if live_items & commands[turn_id]]

    def _command_item_ids(self, turn_id: str, deadline: float) -> set[str]:
        with self._command_items_lock:
            owned_items = set(self._command_items.get(turn_id, set()))
        for entry in self._control_items("thread/items/list", {"turnId": turn_id}, deadline):
            item = entry.get("item")
            if entry.get("turnId") != turn_id or not isinstance(item, dict):
                raise self._runtime_error("Codex returned items outside the selected Turn.")
            if (item_id := self._validated_command_item_id(item)) is not None:
                # Completed command items can still own a background terminal.
                owned_items.add(item_id)
        return owned_items

    def _background_terminals(self, deadline: float) -> list[dict[str, Any]]:
        terminals = list(self._control_items("thread/backgroundTerminals/list", {}, deadline))
        for terminal in terminals:
            if any(not isinstance(terminal.get(key), str) or not terminal[key]
                   for key in ("itemId", "processId")):
                raise self._runtime_error("Codex returned an unbound background terminal.")
        return terminals

    def _control_items(self, method: str, params: dict[str, Any], deadline: float) -> Iterator[dict[str, Any]]:
        cursor = None
        seen_cursors: set[str] = set()
        while True:
            result = self._request(method, {
                "threadId": self.thread_id, "limit": 100, **params,
                **({"cursor": cursor} if cursor is not None else {}),
            }, deadline=deadline)
            data = result.get("data")
            if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
                raise self._runtime_error("Codex returned an unreadable control inventory.")
            yield from data
            cursor = result.get("nextCursor")
            if cursor is None:
                return
            if not isinstance(cursor, str) or not cursor or cursor in seen_cursors:
                raise self._runtime_error("Codex returned an invalid control inventory cursor.")
            seen_cursors.add(cursor)

    def transport_retry_readback(self, turn_id: str) -> dict[str, Any] | None:
        """Read native terminal/effect facts; notification history is insufficient.

        Only a failed, latest Turn with a known connection/overload error and a complete
        text-only inventory is eligible. Unknown items, tools (including reads),
        delivery, additional user input and unreadable inventories stop recovery.
        This is adapter IO evidence, never permission to reopen a Core Turn.
        """
        deadline = time.monotonic() + min(self.response_timeout_sec, 5.0)

        def failed_turn(*, await_terminal: bool = False) -> dict[str, Any] | None:
            while True:
                turns = list(self._control_items("thread/turns/list", {
                    "sortDirection": "desc", "itemsView": "summary",
                }, deadline))
                ids = [row.get("id") for row in turns]
                if (not ids or any(not isinstance(value, str) or not value for value in ids)
                        or len(set(ids)) != len(ids) or ids[0] != turn_id
                        or any(row.get("status") not in {"completed", "interrupted", "failed"}
                               for row in turns[1:])):
                    return None
                if turns[0].get("status") == "failed":
                    break
                # The native error notification can precede its persisted
                # failed Turn. A provisional interrupted snapshot has no
                # completion timestamp or error yet. Wait within the same
                # readback deadline; never dispatch from this interim state.
                if (not await_terminal or turns[0].get("status") not in {"inProgress", "interrupted"}
                        or turns[0].get("completedAt") is not None or turns[0].get("error") is not None):
                    return None
                self._control_wait(deadline)
            error = turns[0].get("error")
            info = _retry_error_details(error).get("retry", {}).get("codex_error_info")
            if info == "serverOverloaded":
                return {"codex_error_info": info}
            if _routing_discovery_timeout(error):
                return {"codex_error_info": info, "pre_execution_routing_timeout": True}
            if not isinstance(info, dict) or len(info) != 1:
                return None
            variant, detail = next(iter(info.items()))
            if not isinstance(detail, dict) or "httpStatusCode" not in detail:
                return None
            status = detail["httpStatusCode"]
            if status is not None and not (type(status) is int and 500 <= status <= 599):
                return None
            return {"codex_error_info": {variant: {"httpStatusCode": status}}}

        try:
            terminal = failed_turn(await_terminal=True)
            if terminal is None:
                return None
            with self._command_items_lock:
                if self._command_items.get(turn_id):
                    return None
            items = list(self._control_items("thread/items/list", {"turnId": turn_id}, deadline))
            item_ids: set[str] = set()
            user_count = 0
            for entry in items:
                item = entry.get("item")
                if entry.get("turnId") != turn_id or not isinstance(item, dict):
                    return None
                item_id = item.get("id")
                if not isinstance(item_id, str) or not item_id or item_id in item_ids:
                    return None
                item_ids.add(item_id)
                if item.get("type") not in {"userMessage", "agentMessage", "reasoning", "plan"}:
                    return None
                if item.get("type") == "userMessage":
                    user_count += 1
                if item.get("type") == "agentMessage" and (
                    item.get("delivery") is not None or item.get("questions")
                ):
                    return None
            # An accepted user item binds the complete inventory to this native
            # request. A second user item can be a correction; never drop it.
            if user_count != 1 or failed_turn() != terminal:
                return None
            if terminal.get("pre_execution_routing_timeout") and len(items) != 1:
                return None
            return {"thread_id": self.thread_id, "turn_id": turn_id,
                    "item_count": len(items), **terminal}
        except (CodexChatAgentError, OSError, TypeError, ValueError):
            return None

    def _control_wait(self, deadline: float) -> None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise self._timeout_error("response_timeout", "Codex stop was not confirmed before the deadline.")
        time.sleep(min(0.05, remaining))

    def send(
        self,
        user_message: str,
        *,
        attachments: list[dict[str, Any]] | None = None,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
        output_schema: dict[str, Any] | None = None,
        project_collaboration: bool = False,
        on_native_item: Callable[[dict[str, Any]], None] | None = None,
        turn_start_guard: Callable[[], AbstractContextManager[None]] | None = None,
        check_running: Callable[[], None] | None = None,
        reply_image_turn_id: str | None = None,
        _native_task: bool = False,
        sandbox_policy: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        if not _native_task and (self._native_task_policy_used or self._managed_goal_read_default) and sandbox_policy is None:
            sandbox_policy = {"type": "readOnly", "networkAccess": False}
        from .capabilities.native_chat.codex_adapter import native_turn_response, prepare_native_turn_text
        text, output_schema = prepare_native_turn_text(self, user_message, output_schema=output_schema,
            project_collaboration=project_collaboration, native_task=_native_task,
            sandbox_policy=sandbox_policy, turn_start_guard=turn_start_guard)
        with self._request_id_lock:
            request_id = self.next_request_id
            self.next_request_id += 1
        image_dir = None
        if reply_image_turn_id is not None:
            from .capabilities.native_chat.file_discovery import reply_image_directory

            image_dir = self.work_dir.resolve() / reply_image_directory(reply_image_turn_id)
        if output_schema is None:
            text = _turn_prompt(
                text,
                context_summary=self.context_summary,
                execution_mode=self.execution_mode or _native_task,
                runtime_profile=self.runtime_profile,
                project_work=self.project_context is not None and self.sandbox == "workspace-write",
                public_source_read_context=self._public_source_read_context,
                reply_image_directory=image_dir,
            )
        turn_input: list[dict[str, Any]] = [{"type": "text", "text": text}]
        from .chat.attachments import native_attachment_inputs
        turn_input.extend(native_attachment_inputs(attachments))
        # A running app-server is not permission for a new native Turn. Hold
        # the caller's fence through its write, then release before waiting for
        # the native acknowledgement or model output.
        started_at = time.monotonic()
        start_limits = [v for v in (self.idle_timeout_sec, self.hard_timeout_sec) if v is not None]
        start_deadline = started_at + min(start_limits) if start_limits else None
        if _native_task:
            # Publication may be uncertain. Every subsequent ordinary send
            # must narrow the sticky native policy, even after an exception.
            self._native_task_policy_used = True
        turn_result = self._request(
            "turn/start",
            {
                "threadId": self.thread_id,
                "input": turn_input,
                "cwd": str(self.work_dir),
                **({"model": self.model} if self.model else {}),
                **({"effort": self.reasoning_effort} if self.reasoning_effort else {}),
                # Chat renders native summaries, but a resumed thread may have
                # disabled them. Request the public summary on each Chat Turn;
                # execution hosts retain their own verbosity configuration.
                **({"summary": "concise"} if not (self.execution_mode or _native_task) else {}),
                "approvalPolicy": "never",
                **({"permissions": self.permissions_profile} if self.permissions_profile else {}),
                **({"sandboxPolicy": sandbox_policy} if sandbox_policy is not None else {}),
                **({"runtimeWorkspaceRoots": [str(self.work_dir.resolve())]}
                   if _native_task and sandbox_policy is not None
                   and sandbox_policy.get("type") == "workspaceWrite" else {}),
                **(
                    {"outputSchema": output_schema} if output_schema is not None else {}
                ),
            },
            request_id=request_id,
            deadline=start_deadline,
            **({"write_guard": turn_start_guard} if turn_start_guard is not None else {}),
        )
        turn_id = _extract_id(turn_result, "turn", "turnId")
        self.current_turn_id = turn_id
        with self._command_items_lock:
            self._command_items = {turn_id: self._command_items.get(turn_id, set())}
        if on_event:
            on_event("turn.started", {"upstream_turn_id": turn_id})
        parts: list[str] = []
        completed_agent_response: str | None = None
        display_filter = VisibleResponseStreamFilter(protected_paths=[self.work_dir])
        steps = CodexActivitySteps(protected_paths=[self.work_dir])
        visible_delta_count = 0

        def emit_visible(chunk: str) -> None:
            nonlocal visible_delta_count
            if chunk and on_event:
                visible_delta_count += 1
                on_event("answer.delta", {"text": chunk})

        def raise_turn_failure(error_payload: Any, fallback: str) -> None:
            error = _terminal_turn_error(error_payload, fallback)
            error.native_error_info = _retry_error_details(error_payload).get("retry", {}).get("codex_error_info")
            raise error

        last_activity_at = time.monotonic()
        while True:
            deadline = self._turn_deadline(started_at, last_activity_at)
            try:
                message = self._next_event(
                    deadline=deadline, **({"check": check_running} if check_running is not None else {}),
                )
            except CodexChatAgentError:
                self._turn_deadline(started_at, last_activity_at)
                raise
            last_activity_at = time.monotonic()
            if self._check_server_gate(message):
                continue
            event_thread_id = _event_thread_id(message)
            event_turn_id = _event_turn_id(message)
            if event_thread_id and event_thread_id != self.thread_id:
                continue
            # A restored native Goal can leave historical turn notifications.
            # turn/start already returned the exact turn owned by this send.
            if event_turn_id and turn_id and event_turn_id != turn_id:
                continue
            if message.get("method") == "turn/started" and event_turn_id:
                turn_id = event_turn_id
                self.current_turn_id = turn_id
            method = str(message.get("method") or "")
            params = message.get("params")
            if method == "item/completed" and isinstance(params, dict) and on_native_item:
                native_item = params.get("item")
                if isinstance(native_item, dict) and native_item.get("type") == "collabAgentToolCall":
                    on_native_item(native_item)
            if on_event:
                phase = {
                    "turn/started": "Agent 已开始处理",
                    "item/completed": "Agent 返回了处理状态",
                    "turn/completed": "Agent 回合已结束",
                }.get(method)
                item = params.get("item") if isinstance(params, dict) else None
                step = None
                if method == "item/completed":
                    step = steps.completed(item)
                    if isinstance(item, dict) and item.get("type") == "contextCompaction":
                        phase = "Agent 会话上下文压缩已结束"
                elif method in {"item/reasoning/summaryTextDelta", "item/reasoning/textDelta"} and isinstance(params, dict):
                    step = steps.reasoning_delta(
                        params.get("itemId"), params.get("delta"),
                        summary=method.endswith("summaryTextDelta"),
                        index=params.get("summaryIndex", params.get("contentIndex")),
                    )
                    if step:
                        phase = "Agent 正在思考"
                elif method in {"item/commandExecution/outputDelta", "item/mcpToolCall/progress"} and isinstance(params, dict):
                    command_output = method == "item/commandExecution/outputDelta"
                    step = steps.activity_delta(
                        params.get("itemId"), params.get("delta" if command_output else "message"),
                        kind="command" if command_output else "tool",
                    )
                    if step:
                        phase = "Agent 收到命令新输出" if command_output else "Agent 收到工具进展"
                if method == "item/started":
                    item_type = (
                        str(item.get("type") or "") if isinstance(item, dict) else ""
                    )
                    step = steps.started(item)
                    # The label stays typed; the step names
                    # the command, tool or path through redacted fields only.
                    phase = {
                        "userMessage": "Agent 已收到消息",
                        "agentMessage": "Agent 正在生成回答",
                        "reasoning": "Agent 正在思考",
                        "contextCompaction": "Agent 正在压缩会话上下文",
                        "commandExecution": "Agent 正在执行命令",
                        "mcpToolCall": "Agent 正在调用工具",
                        "dynamicToolCall": "Agent 正在调用工具",
                        "webSearch": "Agent 正在检索",
                        "fileChange": "Agent 正在修改文件",
                    }.get(item_type, "Agent 正在处理")
                if phase:
                    on_event("agent.phase", {"label": phase, "method": method, **({"step": step} if step else {})})
            if method == "item/agentMessage/delta" and isinstance(params, dict):
                delta = params.get("delta")
                if isinstance(delta, str):
                    parts.append(delta)
                    emit_visible(display_filter.feed(delta))
            elif method == "item/completed":
                item_text = _agent_item_text(message)
                if isinstance(params, dict):
                    item = params.get("item")
                    if isinstance(item, dict) and item.get("type") == "agentMessage":
                        # Completed answer items are authoritative. Joining
                        # commentary and partial deltas would pollute the
                        # visible-answer prefix or promote nonfinal metadata.
                        phase = item.get("phase")
                        if phase is None or phase == "final_answer":
                            completed_agent_response = item_text
                        elif phase != "commentary" or completed_agent_response is None:
                            completed_agent_response = ""
                if item_text and not parts:
                    parts.append(item_text)
                    emit_visible(display_filter.feed(item_text))
            elif method == "turn/completed":
                turn = params.get("turn") if isinstance(params, dict) else None
                turn_status = (
                    str(turn.get("status") or "") if isinstance(turn, dict) else ""
                )
                if turn_status == "failed":
                    raise_turn_failure(
                        turn.get("error"),
                        "Codex app-server reported a terminal turn failure.",
                    )
                if turn_status == "interrupted":
                    raise CodexChatAgentError(
                        "Codex app-server reported an interrupted turn.",
                        error_code="interrupted",
                        gate=_host_tool_gate(
                            "The Codex Chat turn was interrupted.",
                            "Send a new message to continue in the same session.",
                        ),
                    )
                break
            elif method == "error":
                if isinstance(params, dict) and params.get("willRetry") is True:
                    if on_event:
                        on_event(
                            "agent.phase",
                            {
                                "label": "Codex 正在重试",
                                "method": method,
                                **_retry_error_details(params.get("error")),
                            },
                        )
                    continue
                raise_turn_failure(
                    params.get("error") if isinstance(params, dict) else None,
                    "Codex app-server reported a turn error.",
                )
        emit_visible(display_filter.finish())
        raw_response = completed_agent_response if completed_agent_response is not None else "".join(parts)
        response = native_turn_response(self, raw_response, output_schema=output_schema,
                                        reply_image_turn_id=reply_image_turn_id)
        if output_schema is None and on_event:
            if (
                CHAT_REVIEW_OPEN_TAG not in raw_response
                or CHAT_REVIEW_CLOSE_TAG not in raw_response
            ):
                on_event("protocol.warning", {"error_code": "missing_review_envelope"})
            if visible_delta_count == 0:
                on_event("answer.delta", {"text": str(response.get("message") or "")})
            on_event("answer.final", {"response": response})
        self.current_turn_id = ""
        return response

    def _turn_deadline(self, started_at: float, last_activity_at: float) -> float | None:
        now = time.monotonic()
        if self.hard_timeout_sec is not None and now - started_at >= self.hard_timeout_sec:
            raise self._timeout_error("hard_timeout", "Codex Chat turn reached its hard time limit.")
        if self.idle_timeout_sec is not None and now - last_activity_at >= self.idle_timeout_sec:
            raise self._timeout_error("idle_timeout", "Codex Chat turn stopped producing activity.")
        deadlines = [
            *([started_at + self.hard_timeout_sec] if self.hard_timeout_sec is not None else []),
            *([last_activity_at + self.idle_timeout_sec] if self.idle_timeout_sec is not None else []),
        ]
        return min(deadlines) if deadlines else None

    def _timeout_error(self, error_code: str, summary: str) -> CodexChatTimeoutError:
        return CodexChatTimeoutError(
            summary,
            error_code=error_code,
            gate=_host_tool_gate(
                summary, "Interrupt the turn or retry in the same session."
            ),
        )

    def close(self) -> None:
        self._close_process(self.process, process_tree_owned=self.process_tree_owned)

    @staticmethod
    def _close_process(process: subprocess.Popen[str], *, process_tree_owned: bool) -> None:
        if process_tree_owned:
            from .extensions.process_runtime import terminate_process_tree

            terminate_process_tree(process, grace_seconds=0.1)
            return
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)

    def __enter__(self) -> "CodexChatAgentSession":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
