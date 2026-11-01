"""Antigravity Language Server adapter for LoopX Native Chat and Steward."""

from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
from typing import Any, Callable, Mapping
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import uuid

from ...chat import (
    VisibleResponseStreamFilter,
    parse_agent_response,
)
from ...chat_agent import CodexChatAgentError, _turn_prompt


ANTIGRAVITY_DEFAULT_MODEL = "gemini-4-argon-high"
ANTIGRAVITY_DEFAULT_REASONING_EFFORT = "high"

ANTIGRAVITY_MODEL_PLACEHOLDERS: dict[str, str] = {
    "gemini-4-argon-high": "MODEL_PLACEHOLDER_M417",
    "gemini-4-argon-medium": "MODEL_PLACEHOLDER_M418",
    "gemini-4-argon-low": "MODEL_PLACEHOLDER_M419",
}

ANTIGRAVITY_MODEL_OPTIONS: tuple[dict[str, Any], ...] = tuple(
    {
        "id": f"gemini-4-argon-{effort}",
        "display_name": f"Gemini 4 Argon ({effort.title()})",
        "is_default": effort == "high",
        "default_reasoning_effort": effort,
        "supported_reasoning_efforts": [{"reasoning_effort": effort}],
    }
    for effort in ("high", "medium", "low")
)


class _LocalRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str,
                         headers: Any, newurl: str) -> None:
        # A local endpoint's CSRF token must never follow an HTTP redirect.
        return None


_NO_PROXY_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}), _LocalRedirectHandler(),
)


def _local_url(value: str) -> str:
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    try:
        loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if (parsed.scheme != "http" or not loopback or not parsed.port
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"}):
        raise ValueError("Antigravity requires an HTTP loopback endpoint with a port")
    return value.rstrip("/")


def _rpc_post(
    base_url: str,
    csrf_token: str,
    method: str,
    payload: Mapping[str, Any],
    *,
    timeout: float = 15.0,
) -> dict[str, Any]:
    url = f"{_local_url(base_url)}/exa.language_server_pb.LanguageServerService/{method}"
    req = urllib.request.Request(
        url,
        data=json.dumps(dict(payload)).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Connect-Protocol-Version": "1",
            "x-codeium-csrf-token": csrf_token,
        },
        method="POST",
    )
    try:
        with _NO_PROXY_OPENER.open(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw) if raw.strip() else {}
            return data if isinstance(data, dict) else {}
    except urllib.error.HTTPError as exc:
        raise CodexChatAgentError(
            f"Antigravity LanguageServerService/{method} failed with HTTP {exc.code}",
            gate=None,
            error_code="antigravity_rpc_error",
        ) from exc
    except Exception as exc:
        raise CodexChatAgentError(
            f"Antigravity LanguageServerService/{method} transport failed ({type(exc).__name__})",
            gate=None,
            error_code="antigravity_transport_error",
        ) from exc


def discover_antigravity_language_server(
    env: Mapping[str, str] | None = None,
) -> dict[str, Any] | None:
    """Discover a running local Antigravity language_server endpoint and CSRF token."""
    source_env = os.environ if env is None else env
    explicit_url = str(source_env.get("ANTIGRAVITY_LS_URL") or "").strip()
    explicit_token = str(source_env.get("ANTIGRAVITY_CSRF_TOKEN") or "").strip()
    if explicit_url or explicit_token:
        if not explicit_url or not explicit_token:
            return None
        try:
            explicit_url = _local_url(explicit_url)
            _rpc_post(
                explicit_url,
                explicit_token,
                "GetCascadeModelConfigData",
                {},
                timeout=2.5,
            )
            return {
                "url": explicit_url.rstrip("/"),
                "csrf_token": explicit_token,
                "pid": None,
                "source": "environment",
            }
        except (CodexChatAgentError, ValueError):
            return None

    try:
        ps_out = subprocess.check_output(
            ["ps", "-u", str(os.getuid()), "-o", "pid=,command="],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=3.0,
        )
    except Exception:
        return None

    candidates: list[tuple[int, str]] = []
    for line in ps_out.splitlines():
        if ("language_server" not in line or "antigravity" not in line.lower()
                or "--csrf_token" not in line):
            continue
        match = re.match(r"^\s*(\d+)\s+(.*)$", line)
        if not match:
            continue
        pid = int(match.group(1))
        cmd = match.group(2)
        token_match = re.search(r"--csrf_token(?:=|\s+)([A-Za-z0-9._-]+)", cmd)
        if token_match:
            candidates.append((pid, token_match.group(1)))

    deadline = time.monotonic() + 4.0
    for pid, token in candidates:
        if time.monotonic() >= deadline:
            break
        try:
            lsof_out = subprocess.check_output(
                ["lsof", "-nP", "-a", "-p", str(pid), "-iTCP", "-sTCP:LISTEN"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=max(0.1, deadline - time.monotonic()),
            )
        except Exception:
            continue
        ports: list[int] = []
        for line in lsof_out.splitlines():
            port_match = re.search(r":(\d+)\s+\(LISTEN\)", line)
            if port_match:
                ports.append(int(port_match.group(1)))
        for port in ports:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            url = f"http://127.0.0.1:{port}"
            try:
                _rpc_post(url, token, "GetCascadeModelConfigData", {}, timeout=min(1.5, remaining))
                return {
                    "url": url,
                    "csrf_token": token,
                    "pid": pid,
                    "source": "process_discovery",
                }
            except CodexChatAgentError:
                continue
    return None


def probe_antigravity_endpoint(
    env: Mapping[str, str] | None = None,
) -> tuple[bool, str, str]:
    """Return `(available, command_display, detail)` for `/api/chat/capabilities`."""
    discovered = discover_antigravity_language_server(env=env)
    if discovered is not None:
        return (
            True,
            "antigravity-ls",
            f"Antigravity Language Server ({discovered['url']}, default model {ANTIGRAVITY_DEFAULT_MODEL})",
        )
    app_ls = Path("/Applications/Antigravity.app/Contents/Resources/bin/language_server")
    agy_bin = shutil.which("agy")
    if app_ls.exists() or agy_bin:
        return (
            False,
            str(agy_bin or app_ls),
            "Antigravity is installed, but no active local Antigravity language_server process was found",
        )
    return (
        False,
        "antigravity",
        "Antigravity Language Server not found",
    )


def resolve_antigravity_model_placeholder(
    model_id: str | None,
    reasoning_effort: str | None = None,
    *,
    dynamic_configs: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    """Return `(canonical_model_id, model_placeholder)` for Antigravity."""
    raw_model = str(model_id or "").strip().lower()
    effort = str(reasoning_effort or "").strip().lower()
    if effort and effort not in {"low", "medium", "high"}:
        raise CodexChatAgentError("Choose low, medium, or high for Antigravity.",
                                  gate=None, error_code="unsupported_configuration")
    if not raw_model or raw_model in {"argon", "gemini-4-argon"}:
        canonical = f"gemini-4-argon-{effort or 'high'}"
    else:
        canonical = raw_model
    if (canonical not in ANTIGRAVITY_MODEL_PLACEHOLDERS
            or effort and canonical != f"gemini-4-argon-{effort}"):
        raise CodexChatAgentError("Choose a supported Argon model and its matching reasoning effort.",
                                  gate=None, error_code="unsupported_configuration")
    placeholder = ANTIGRAVITY_MODEL_PLACEHOLDERS[canonical]
    if dynamic_configs is not None:
        match = next((row for row in dynamic_configs
                      if row.get("modelId") == canonical
                      or (row.get("modelOrAlias") or {}).get("model") == placeholder), None)
        if match is None:
            raise CodexChatAgentError("The local Antigravity account did not offer this Argon model.",
                                      gate=None, error_code="unsupported_configuration")
        placeholder = str((match.get("modelOrAlias") or {}).get("model") or placeholder)
    return canonical, placeholder


class AntigravityAdapter:
    """Local text Chat and trusted-owner Steward over LanguageServerService.

    Restricted Chat disables native tools. Trusted-owner uses Antigravity's
    existing approvals; LoopX never accepts those approvals on the user's behalf.
    Governed Tasks and workspace grants retain their existing executor owner.
    """

    adapter_kind = "antigravity_ls"

    def __init__(
        self,
        *,
        work_dir: Path,
        objective: str,
        cascade_id: str,
        ls_url: str,
        csrf_token: str,
        model_id: str,
        model_placeholder: str,
        reasoning_effort: str | None = None,
        runtime_profile: str = "restricted",
        team_plan_context: dict[str, Any] | None = None,
        initial_history: list[dict[str, str]] | None = None,
        resumed: bool = False,
        hard_timeout_sec: float = 300.0,
    ) -> None:
        self.work_dir = work_dir.resolve()
        self.objective = objective
        self.cascade_id = cascade_id
        self.upstream_thread_id = cascade_id
        self.ls_url = ls_url
        self.csrf_token = csrf_token
        self.model_id = model_id
        self.model_placeholder = model_placeholder
        self.reasoning_effort = reasoning_effort
        self.runtime_profile = runtime_profile
        self.team_plan_context = team_plan_context
        self._pending_history: list[dict[str, str]] = (
            list(initial_history) if initial_history and not resumed else []
        )
        self._system_primed = False
        self._closed = False
        self.hard_timeout_sec = hard_timeout_sec
        self._interrupted = threading.Event()
        self._send_lock = threading.Lock()
        self._active_turn_id: str | None = None
        self._cancel_acknowledged = False

    @property
    def current_model_id(self) -> str:
        return self.model_id

    @property
    def model_settings(self) -> dict[str, str | None]:
        return {
            "agent_reasoning_effort": self.reasoning_effort,
            "agent_service_tier": None,
        }

    @classmethod
    def start(
        cls,
        *,
        work_dir: Path,
        objective: str,
        resume_thread_id: str | None = None,
        history: list[dict[str, str]] | None = None,
        runtime_profile: str = "restricted",
        team_plan_context: dict[str, Any] | None = None,
        executor_model: Mapping[str, Any] | None = None,
        env: Mapping[str, str] | None = None,
        hard_timeout_sec: float = 300.0,
    ) -> "AntigravityAdapter":
        if runtime_profile not in {"restricted", "trusted_owner"}:
            raise ValueError("unsupported Antigravity Chat runtime profile")
        # Validate user intent before creating an upstream cascade.
        requested = dict(executor_model or {})
        resolve_antigravity_model_placeholder(requested.get("model"), requested.get("reasoning_effort"))
        discovered = discover_antigravity_language_server(env=env)
        if discovered is None:
            raise CodexChatAgentError(
                "Antigravity local language_server is not running or reachable",
                gate=None,
                error_code="antigravity_unavailable",
            )
        ls_url = str(discovered["url"])
        csrf_token = str(discovered["csrf_token"])

        model_data = _rpc_post(ls_url, csrf_token, "GetCascadeModelConfigData", {}, timeout=3.0)
        raw_configs = model_data.get("clientModelConfigs")
        dynamic_configs = [item for item in raw_configs if isinstance(item, dict)] if isinstance(raw_configs, list) else []

        requested_model = (
            str(executor_model.get("model") or "").strip()
            if isinstance(executor_model, Mapping)
            else None
        )
        requested_effort = (
            str(executor_model.get("reasoning_effort") or "").strip()
            if isinstance(executor_model, Mapping)
            else None
        )
        model_id, model_placeholder = resolve_antigravity_model_placeholder(
            requested_model,
            requested_effort,
            dynamic_configs=dynamic_configs,
        )

        cascade_id = str(resume_thread_id or "").strip()
        resumed = False
        if cascade_id:
            try:
                traj = _rpc_post(
                    ls_url,
                    csrf_token,
                    "GetCascadeTrajectory",
                    {"cascadeId": cascade_id},
                    timeout=3.0,
                )
                if isinstance(traj, dict) and traj.get("trajectory"):
                    resumed = True
            except CodexChatAgentError:
                resumed = False

        if cascade_id and not resumed:
            raise CodexChatAgentError(
                "The original Antigravity conversation is unavailable. Restore it in Antigravity or start a new Chat explicitly.",
                gate=None, error_code="resume_failed",
            )

        if not resumed:
            started = _rpc_post(
                ls_url,
                csrf_token,
                "StartCascade",
                {
                    "source": "CORTEX_TRAJECTORY_SOURCE_CASCADE_CLIENT",
                    "workspaceUris": [work_dir.resolve().as_uri()],
                },
                timeout=10.0,
            )
            cascade_id = str(started.get("cascadeId") or "").strip()
            if not cascade_id:
                raise CodexChatAgentError(
                    f"StartCascade did not return a cascadeId: {started}",
                    gate=None,
                    error_code="antigravity_start_failed",
                )

        return cls(
            work_dir=work_dir,
            objective=objective,
            cascade_id=cascade_id,
            ls_url=ls_url,
            csrf_token=csrf_token,
            model_id=model_id,
            model_placeholder=model_placeholder,
            reasoning_effort=model_id.rsplit("-", 1)[-1],
            runtime_profile=runtime_profile,
            team_plan_context=team_plan_context,
            initial_history=history,
            resumed=resumed,
            hard_timeout_sec=hard_timeout_sec,
        )

    def _build_turn_prompt(self, message: str) -> str:
        parts: list[str] = []
        if not self._system_primed:
            if self.objective.strip():
                parts.append(self.objective.strip())
            if self._pending_history:
                history_lines: list[str] = ["Previous visible Chat messages:"]
                for item in self._pending_history[-16:]:
                    role = str(item.get("role") or "user")
                    content = str(item.get("content") or "").strip()
                    if content:
                        history_lines.append(f"{role}: {content}")
                if len(history_lines) > 1:
                    parts.append("\n".join(history_lines))
        parts.append(
            _turn_prompt(
                message,
                runtime_profile=self.runtime_profile,
            )
        )
        return "\n\n".join(parts)

    def _build_cascade_config(self) -> dict[str, Any]:
        # Never inherit an old cascade's tool permissions or userAgentSpec.
        # Restricted Chat is text-only; trusted_owner retains native host approval.
        restricted = self.runtime_profile == "restricted"
        return {"plannerConfig": {
            "planModel": self.model_placeholder,
            "requestedModel": {"model": self.model_placeholder},
            "conversational": {
                "plannerMode": ("CONVERSATIONAL_PLANNER_MODE_READ_ONLY" if restricted
                                else "CONVERSATIONAL_PLANNER_MODE_DEFAULT"),
                "agenticMode": not restricted,
            },
            **({"toolConfig": {"forceDisableToolCalling": True},
                "customizationDiscoveryConfig": {"hooks": {"inheritUser": False}}}
               if restricted else {}),
        }}

    def capabilities(self) -> dict[str, Any]:
        return {"adapter_kind": self.adapter_kind, "streaming": True,
                "resume": True, "interrupt": True, "steering": False}

    def start_turn(self, message: str,
                   event_sink: Callable[[str, dict[str, Any]], None] | None = None) -> dict[str, Any]:
        self._interrupted.clear()
        self._cancel_acknowledged = False
        try:
            return self._run_turn(message, event_sink)
        except BaseException:
            # A timeout/transport or permission failure is not a successful turn.
            # Retain an unconfirmed cancellation as an error for the caller fence.
            self.interrupt_turn()
            raise
        finally:
            self._active_turn_id = None

    def _run_turn(
        self,
        message: str,
        event_sink: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        if self._closed:
            raise CodexChatAgentError(
                "Antigravity session is closed",
                gate=None,
                error_code="session_closed",
            )

        turn_id = f"agy_turn_{uuid.uuid4().hex[:12]}"
        before_traj = _rpc_post(
            self.ls_url,
            self.csrf_token,
            "GetCascadeTrajectory",
            {"cascadeId": self.cascade_id},
            timeout=5.0,
        )
        if before_traj.get("status") != "CASCADE_RUN_STATUS_IDLE":
            raise CodexChatAgentError(
                "The original Antigravity conversation is still active. Stop it in Antigravity before retrying.",
                gate=None, error_code="resume_failed",
            )
        start_step_index = int(before_traj.get("numTotalSteps") or 0)
        user_config = self._build_cascade_config()
        full_prompt = self._build_turn_prompt(message)

        send_payload: dict[str, Any] = {
            "cascadeId": self.cascade_id,
            "items": [{"text": full_prompt}],
            "cascadeConfig": user_config,
        }
        with self._send_lock:
            self._check_interrupted()
            self._active_turn_id = turn_id
            if event_sink is not None:
                event_sink("turn.started", {"upstream_turn_id": turn_id, "threadId": self.cascade_id})
            _rpc_post(self.ls_url, self.csrf_token, "SendUserCascadeMessage", send_payload, timeout=15.0)
            self._system_primed = True
            self._pending_history.clear()

        stream_filter = VisibleResponseStreamFilter()
        emitted_visible_delta = False
        emitted_text_by_step: dict[int, str] = {}
        announced_phases: set[tuple[int, str]] = set()
        latest_response_text = ""
        deadline = time.monotonic() + self.hard_timeout_sec
        saw_running_or_new_step = False

        while time.monotonic() < deadline:
            self._check_interrupted()
            self._interrupted.wait(0.25)
            self._check_interrupted()
            traj = _rpc_post(
                self.ls_url,
                self.csrf_token,
                "GetCascadeTrajectory",
                {"cascadeId": self.cascade_id},
                timeout=2.0,
            )
            status = str(traj.get("status") or "")
            total_steps = int(traj.get("numTotalSteps") or 0)
            if status == "CASCADE_RUN_STATUS_RUNNING" or total_steps > start_step_index:
                saw_running_or_new_step = True

            if total_steps > start_step_index:
                self._check_interrupted()
                steps_resp = _rpc_post(
                    self.ls_url,
                    self.csrf_token,
                    "GetCascadeTrajectorySteps",
                    {
                        "cascadeId": self.cascade_id,
                        "startIndex": start_step_index,
                        "endIndex": total_steps,
                    },
                    timeout=2.0,
                )
                steps = steps_resp.get("steps") or []
                if isinstance(steps, list):
                    for offset, step in enumerate(steps):
                        if not isinstance(step, dict):
                            continue
                        step_idx = start_step_index + offset
                        step_type = str(step.get("type") or "")
                        step_status = str(step.get("status") or "")
                        if step_status == "CORTEX_STEP_STATUS_WAITING":
                            raise CodexChatAgentError(
                                "Antigravity requested native permission; this invocation was cancelled. Review its permissions, then retry.",
                                gate={"kind": "host_tool_gate", "summary": "Antigravity requires native permission.",
                                      "next_action": "Review permissions in Antigravity, then resend this request in LoopX."},
                                error_code="host_tool_gate",
                            )
                        if step_type == "CORTEX_STEP_TYPE_PLANNER_RESPONSE":
                            pr = step.get("plannerResponse") or {}
                            if not isinstance(pr, dict):
                                continue
                            thinking = str(pr.get("thinking") or "").strip()
                            if thinking and (step_idx, "thinking") not in announced_phases:
                                announced_phases.add((step_idx, "thinking"))
                                if event_sink is not None:
                                    event_sink(
                                        "agent.phase",
                                        {
                                            "phase": "thinking",
                                            "label": f"Gemini 4 Argon 思考中 ({self.model_id})",
                                        },
                                    )
                            tool_calls = pr.get("toolCalls") or []
                            if tool_calls and self.runtime_profile == "restricted":
                                raise CodexChatAgentError("Antigravity did not honor text-only Chat.",
                                                          gate=None, error_code="unsupported_configuration")
                            if isinstance(tool_calls, list) and tool_calls and (step_idx, "tool") not in announced_phases:
                                announced_phases.add((step_idx, "tool"))
                                first_tool = tool_calls[0] if isinstance(tool_calls[0], dict) else {}
                                tool_name = str(first_tool.get("name") or "tool")
                                if event_sink is not None:
                                    event_sink(
                                        "agent.phase",
                                        {
                                            "phase": "tool_call",
                                            "label": f"正在调用 {tool_name}",
                                        },
                                    )
                            text = str(pr.get("modifiedResponse") or pr.get("response") or "")
                            if text:
                                prev_text = emitted_text_by_step.get(step_idx, "")
                                if text.startswith(prev_text):
                                    delta_chunk = text[len(prev_text) :]
                                else:
                                    delta_chunk = text
                                emitted_text_by_step[step_idx] = text
                                latest_response_text = text
                                if delta_chunk and event_sink is not None:
                                    visible_delta = stream_filter.feed(delta_chunk)
                                    if visible_delta:
                                        emitted_visible_delta = True
                                        event_sink("answer.delta", {"text": visible_delta})
                        elif step_type == "CORTEX_STEP_TYPE_ERROR_MESSAGE":
                            err_obj = step.get("errorMessage") or {}
                            err_msg = str(
                                (err_obj.get("error") if isinstance(err_obj, dict) else None)
                                or err_obj
                                or "Antigravity cascade step error"
                            )
                            raise CodexChatAgentError(
                                err_msg,
                                gate=None,
                                error_code="antigravity_step_error",
                            )

            self._check_interrupted()
            if saw_running_or_new_step and status == "CASCADE_RUN_STATUS_IDLE":
                break
        else:
            raise CodexChatAgentError("Antigravity did not finish before the turn deadline.",
                                      gate=None, error_code="response_timeout")

        if event_sink is not None:
            tail = stream_filter.finish()
            if tail:
                emitted_visible_delta = True
                event_sink("answer.delta", {"text": tail})

        if not latest_response_text.strip():
            raise CodexChatAgentError(
                "Antigravity cascade finished without a planner response",
                gate=None,
                error_code="empty_agent_response",
            )

        parsed = parse_agent_response(
            latest_response_text,
            protected_paths=[self.work_dir],
            team_plan_context=self.team_plan_context,
        )
        if event_sink is not None:
            if not emitted_visible_delta and str(parsed.get("message") or "").strip():
                event_sink("answer.delta", {"text": str(parsed["message"])})
            event_sink("answer.final", {"response": parsed})
        return parsed

    def _check_interrupted(self) -> None:
        if self._closed or self._interrupted.is_set():
            raise CodexChatAgentError("Antigravity turn was interrupted.",
                                      gate=None, error_code="turn_interrupted")

    def interrupt_turn(self, turn_id: str | None = None) -> None:
        active = self._active_turn_id
        if active is not None and turn_id is not None and turn_id != active:
            raise ValueError("the active Antigravity Turn changed")
        self._interrupted.set()
        if active is None:
            return
        with self._send_lock:
            if not self._cancel_acknowledged:
                _rpc_post(self.ls_url, self.csrf_token, "CancelCascadeInvocation",
                          {"cascadeId": self.cascade_id}, timeout=5.0)
                deadline = time.monotonic() + 5.0
                while True:
                    trajectory = _rpc_post(self.ls_url, self.csrf_token, "GetCascadeTrajectory",
                                           {"cascadeId": self.cascade_id}, timeout=2.0)
                    if trajectory.get("status") == "CASCADE_RUN_STATUS_IDLE":
                        break
                    if time.monotonic() >= deadline:
                        raise CodexChatAgentError("Antigravity has not confirmed stop; retry stopping it.",
                                                  gate=None, error_code="response_timeout")
                    time.sleep(0.05)
                self._cancel_acknowledged = True

    def close_session(self) -> None:
        self._closed = True
        self.interrupt_turn()

    def healthcheck(self) -> bool:
        if self._closed:
            return False
        try:
            _rpc_post(self.ls_url, self.csrf_token, "GetCascadeModelConfigData", {}, timeout=2.0)
            return True
        except CodexChatAgentError:
            return False
