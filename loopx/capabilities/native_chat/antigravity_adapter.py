"""Antigravity Language Server adapter for LoopX Native Chat and Steward."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Any, Callable, Mapping
import urllib.error
import urllib.request
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

ANTIGRAVITY_MODEL_OPTIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "gemini-4-argon-high",
        "display_name": "Gemini 4 Argon (High)",
        "description": "Antigravity Gemini 4 Argon with high reasoning effort",
        "is_default": True,
        "default_reasoning_effort": "high",
        "supported_reasoning_efforts": [
            {"reasoning_effort": "low", "description": "Fast lower-latency thinking"},
            {"reasoning_effort": "medium", "description": "Balanced thinking"},
            {"reasoning_effort": "high", "description": "Deep reasoning"},
        ],
    },
    {
        "id": "gemini-4-argon-medium",
        "display_name": "Gemini 4 Argon (Medium)",
        "description": "Antigravity Gemini 4 Argon with medium reasoning effort",
        "is_default": False,
        "default_reasoning_effort": "medium",
        "supported_reasoning_efforts": [
            {"reasoning_effort": "low", "description": "Fast lower-latency thinking"},
            {"reasoning_effort": "medium", "description": "Balanced thinking"},
            {"reasoning_effort": "high", "description": "Deep reasoning"},
        ],
    },
    {
        "id": "gemini-4-argon-low",
        "display_name": "Gemini 4 Argon (Low)",
        "description": "Antigravity Gemini 4 Argon with low reasoning effort",
        "is_default": False,
        "default_reasoning_effort": "low",
        "supported_reasoning_efforts": [
            {"reasoning_effort": "low", "description": "Fast lower-latency thinking"},
            {"reasoning_effort": "medium", "description": "Balanced thinking"},
            {"reasoning_effort": "high", "description": "Deep reasoning"},
        ],
    },
    {
        "id": "gemini-4-argon",
        "display_name": "Gemini 4 Argon",
        "description": "Antigravity Gemini 4 Argon (selects tier from reasoning effort)",
        "is_default": False,
        "default_reasoning_effort": "high",
        "supported_reasoning_efforts": [
            {"reasoning_effort": "low", "description": "Maps to Gemini 4 Argon (Low)"},
            {"reasoning_effort": "medium", "description": "Maps to Gemini 4 Argon (Medium)"},
            {"reasoning_effort": "high", "description": "Maps to Gemini 4 Argon (High)"},
        ],
    },
)

_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _rpc_post(
    base_url: str,
    csrf_token: str,
    method: str,
    payload: Mapping[str, Any],
    *,
    timeout: float = 15.0,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/exa.language_server_pb.LanguageServerService/{method}"
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
        body = exc.read().decode("utf-8", errors="replace")[:400]
        raise CodexChatAgentError(
            f"Antigravity LanguageServerService/{method} failed with HTTP {exc.code}: {body}",
            gate=None,
            error_code="antigravity_rpc_error",
        ) from exc
    except Exception as exc:
        raise CodexChatAgentError(
            f"Antigravity LanguageServerService/{method} transport failed: {exc}",
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
    if explicit_url and explicit_token:
        try:
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
        except CodexChatAgentError:
            pass

    try:
        ps_out = subprocess.check_output(
            ["ps", "-ax", "-o", "pid=,command="],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=3.0,
        )
    except Exception:
        return None

    candidates: list[tuple[int, str]] = []
    for line in ps_out.splitlines():
        if "language_server" not in line or "--csrf_token" not in line:
            continue
        match = re.match(r"^\s*(\d+)\s+(.*)$", line)
        if not match:
            continue
        pid = int(match.group(1))
        cmd = match.group(2)
        token_match = re.search(r"--csrf_token(?:=|\s+)([A-Za-z0-9._-]+)", cmd)
        if token_match:
            candidates.append((pid, token_match.group(1)))

    for pid, token in candidates:
        try:
            lsof_out = subprocess.check_output(
                ["lsof", "-nP", "-a", "-p", str(pid), "-iTCP", "-sTCP:LISTEN"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=3.0,
            )
        except Exception:
            continue
        ports: list[int] = []
        for line in lsof_out.splitlines():
            port_match = re.search(r":(\d+)\s+\(LISTEN\)", line)
            if port_match:
                ports.append(int(port_match.group(1)))
        for port in ports:
            url = f"http://127.0.0.1:{port}"
            try:
                _rpc_post(url, token, "GetCascadeModelConfigData", {}, timeout=1.5)
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
    app_ls = Path("/Applications/Antigravity.app/Contents/Resources/bin/language_server_macos_arm")
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
    raw_model = str(model_id or "").strip()
    effort = str(reasoning_effort or "").strip().lower()
    lowered = raw_model.lower()

    if not lowered or lowered in {"argon", "gemini-4-argon"}:
        if effort == "low":
            return "gemini-4-argon-low", ANTIGRAVITY_MODEL_PLACEHOLDERS["gemini-4-argon-low"]
        if effort == "medium":
            return "gemini-4-argon-medium", ANTIGRAVITY_MODEL_PLACEHOLDERS["gemini-4-argon-medium"]
        return "gemini-4-argon-high", ANTIGRAVITY_MODEL_PLACEHOLDERS["gemini-4-argon-high"]

    if lowered in ANTIGRAVITY_MODEL_PLACEHOLDERS:
        if lowered.startswith("gemini-4-argon-") and effort in {"low", "medium", "high"}:
            candidate = f"gemini-4-argon-{effort}"
            if candidate in ANTIGRAVITY_MODEL_PLACEHOLDERS and lowered == ANTIGRAVITY_DEFAULT_MODEL:
                return candidate, ANTIGRAVITY_MODEL_PLACEHOLDERS[candidate]
        return lowered, ANTIGRAVITY_MODEL_PLACEHOLDERS[lowered]

    if raw_model.startswith("MODEL_PLACEHOLDER_"):
        for canonical, placeholder in ANTIGRAVITY_MODEL_PLACEHOLDERS.items():
            if placeholder == raw_model:
                return canonical, placeholder
        return raw_model, raw_model

    for cfg in dynamic_configs or []:
        label = str(cfg.get("label") or "").strip()
        placeholder = str((cfg.get("modelOrAlias") or {}).get("model") or "").strip()
        if not placeholder:
            continue
        if label.lower() == lowered or placeholder.lower() == lowered:
            return raw_model, placeholder

    return ANTIGRAVITY_DEFAULT_MODEL, ANTIGRAVITY_MODEL_PLACEHOLDERS[ANTIGRAVITY_DEFAULT_MODEL]


class AntigravityAdapter:
    """Session adapter backed by Antigravity's local LanguageServerService."""

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
        self._system_primed = resumed
        self._closed = False

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
    ) -> "AntigravityAdapter":
        discovered = discover_antigravity_language_server(env=env)
        if discovered is None:
            raise CodexChatAgentError(
                "Antigravity local language_server is not running or reachable",
                gate=None,
                error_code="antigravity_unavailable",
            )
        ls_url = str(discovered["url"])
        csrf_token = str(discovered["csrf_token"])

        dynamic_configs: list[dict[str, Any]] = []
        try:
            model_data = _rpc_post(ls_url, csrf_token, "GetCascadeModelConfigData", {}, timeout=3.0)
            raw_configs = model_data.get("clientModelConfigs")
            if isinstance(raw_configs, list):
                dynamic_configs = [item for item in raw_configs if isinstance(item, dict)]
        except CodexChatAgentError:
            pass

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
            reasoning_effort=requested_effort or ANTIGRAVITY_DEFAULT_REASONING_EFFORT,
            runtime_profile=runtime_profile,
            team_plan_context=team_plan_context,
            initial_history=history,
            resumed=resumed,
        )

    def _refresh_ls_connection_if_needed(self) -> None:
        try:
            _rpc_post(self.ls_url, self.csrf_token, "GetCascadeModelConfigData", {}, timeout=2.0)
            return
        except CodexChatAgentError:
            discovered = discover_antigravity_language_server()
            if discovered is not None:
                self.ls_url = str(discovered["url"])
                self.csrf_token = str(discovered["csrf_token"])

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
                self._pending_history.clear()
            self._system_primed = True
        parts.append(
            _turn_prompt(
                message,
                runtime_profile=self.runtime_profile,
            )
        )
        return "\n\n".join(parts)

    def _build_cascade_config(self) -> tuple[dict[str, Any], dict[str, Any]]:
        user_config: dict[str, Any] = {}
        user_agent_spec: dict[str, Any] = {}
        try:
            traj = _rpc_post(
                self.ls_url,
                self.csrf_token,
                "GetCascadeTrajectory",
                {"cascadeId": self.cascade_id},
                timeout=4.0,
            )
            metadata = (traj.get("trajectory") or {}).get("generatorMetadata") or []
            if isinstance(metadata, list):
                for entry in reversed(metadata):
                    if not isinstance(entry, dict):
                        continue
                    if not user_config and isinstance(entry.get("userConfig"), dict):
                        user_config = json.loads(json.dumps(entry["userConfig"]))
                    if not user_agent_spec and isinstance(entry.get("userAgentSpec"), dict):
                        user_agent_spec = json.loads(json.dumps(entry["userAgentSpec"]))
                    if user_config and user_agent_spec:
                        break
        except CodexChatAgentError:
            pass

        planner_cfg = user_config.setdefault("plannerConfig", {})
        if not isinstance(planner_cfg, dict):
            planner_cfg = {}
            user_config["plannerConfig"] = planner_cfg
        planner_cfg["planModel"] = self.model_placeholder
        planner_cfg["requestedModel"] = {"model": self.model_placeholder}
        conversational = planner_cfg.setdefault("conversational", {})
        if isinstance(conversational, dict):
            conversational.setdefault("plannerMode", "CONVERSATIONAL_PLANNER_MODE_DEFAULT")
            conversational.setdefault("agenticMode", True)
        return user_config, user_agent_spec

    def start_turn(
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

        self._refresh_ls_connection_if_needed()
        turn_id = f"agy_turn_{uuid.uuid4().hex[:12]}"
        if event_sink is not None:
            event_sink(
                "turn.started",
                {"upstream_turn_id": turn_id, "threadId": self.cascade_id},
            )

        before_traj = _rpc_post(
            self.ls_url,
            self.csrf_token,
            "GetCascadeTrajectory",
            {"cascadeId": self.cascade_id},
            timeout=5.0,
        )
        start_step_index = int(before_traj.get("numTotalSteps") or 0)
        user_config, user_agent_spec = self._build_cascade_config()
        full_prompt = self._build_turn_prompt(message)

        send_payload: dict[str, Any] = {
            "cascadeId": self.cascade_id,
            "items": [{"text": full_prompt}],
            "cascadeConfig": user_config,
        }
        if user_agent_spec:
            send_payload["userAgentSpec"] = user_agent_spec

        _rpc_post(
            self.ls_url,
            self.csrf_token,
            "SendUserCascadeMessage",
            send_payload,
            timeout=15.0,
        )

        stream_filter = VisibleResponseStreamFilter()
        emitted_visible_delta = False
        emitted_text_by_step: dict[int, str] = {}
        announced_phases: set[tuple[int, str]] = set()
        latest_response_text = ""
        deadline = time.monotonic() + 300.0
        saw_running_or_new_step = False

        while time.monotonic() < deadline:
            if self._closed:
                raise CodexChatAgentError(
                    "Antigravity turn was interrupted",
                    gate=None,
                    error_code="turn_interrupted",
                )
            time.sleep(0.25)
            traj = _rpc_post(
                self.ls_url,
                self.csrf_token,
                "GetCascadeTrajectory",
                {"cascadeId": self.cascade_id},
                timeout=6.0,
            )
            status = str(traj.get("status") or "")
            total_steps = int(traj.get("numTotalSteps") or 0)
            if status == "CASCADE_RUN_STATUS_RUNNING" or total_steps > start_step_index:
                saw_running_or_new_step = True

            if total_steps > start_step_index:
                steps_resp = _rpc_post(
                    self.ls_url,
                    self.csrf_token,
                    "GetCascadeTrajectorySteps",
                    {
                        "cascadeId": self.cascade_id,
                        "startIndex": start_step_index,
                        "endIndex": total_steps,
                    },
                    timeout=8.0,
                )
                steps = steps_resp.get("steps") or []
                if isinstance(steps, list):
                    for offset, step in enumerate(steps):
                        if not isinstance(step, dict):
                            continue
                        step_idx = start_step_index + offset
                        step_type = str(step.get("type") or "")
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

            if saw_running_or_new_step and status != "CASCADE_RUN_STATUS_RUNNING" and latest_response_text.strip():
                break

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

    def interrupt_turn(self, turn_id: str | None = None) -> None:
        del turn_id
        try:
            _rpc_post(
                self.ls_url,
                self.csrf_token,
                "CancelCascadeInvocation",
                {"cascadeId": self.cascade_id},
                timeout=5.0,
            )
        except CodexChatAgentError:
            pass

    def close_session(self) -> None:
        self._closed = True

    def healthcheck(self) -> bool:
        if self._closed:
            return False
        try:
            _rpc_post(self.ls_url, self.csrf_token, "GetCascadeModelConfigData", {}, timeout=2.0)
            return True
        except CodexChatAgentError:
            return False
