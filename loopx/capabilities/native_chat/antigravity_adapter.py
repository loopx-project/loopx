"""Antigravity Language Server adapter for LoopX Native Chat and Steward."""

from __future__ import annotations

import ipaddress
import hashlib
import json
import math
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
from datetime import datetime

from ...chat import (
    CHAT_REVIEW_OPEN_TAG, CHAT_REVIEW_CLOSE_TAG,
    VisibleResponseStreamFilter,
    parse_agent_response,
)
from ...chat_agent import CodexChatAgentError, _turn_prompt
from .antigravity_activity import AntigravityActivity
from .manager_input import manager_input_matches


ANTIGRAVITY_DEFAULT_MODEL = "gemini-4-argon-high"
ANTIGRAVITY_DEFAULT_REASONING_EFFORT = "high"
ANTIGRAVITY_IDLE_TIMEOUT_SEC = 300.0

ANTIGRAVITY_MODEL_PLACEHOLDERS: dict[str, str] = {
    "gemini-4-argon-high": "MODEL_PLACEHOLDER_M417",
    "gemini-4-argon-medium": "MODEL_PLACEHOLDER_M418",
    "gemini-4-argon-low": "MODEL_PLACEHOLDER_M419",
}

_SETTLED_STEP_STATUSES = {
    "CORTEX_STEP_STATUS_DONE",
    "CORTEX_STEP_STATUS_ERROR",
    "CORTEX_STEP_STATUS_CANCELED",
    "CORTEX_STEP_STATUS_CLEARED",
}


def _step_allows_completed_turn(step: dict[str, Any], *, final: dict[str, Any]) -> bool:
    """Only a settled intermediate step may precede a final planner answer."""
    error = step.get("errorMessage") or {}
    return (
        step.get("status") in _SETTLED_STEP_STATUSES
        and not (step is final and step.get("status") != "CORTEX_STEP_STATUS_DONE")
        and not (
            step.get("type") == "CORTEX_STEP_TYPE_ERROR_MESSAGE"
            and (step is final or (isinstance(error, dict) and error.get("shouldShowUser") is True))
        )
    )


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


def _observed_turn_steps(steps: list[Any], start: int, total: int) -> tuple[list[tuple[int, dict[str, Any]]], int]:
    """Read absolute native indices; some LS versions ignore range arguments."""
    bases: set[int] = set()
    for offset, step in enumerate(steps):
        if not isinstance(step, dict):
            raise ValueError("Antigravity returned an invalid trajectory step")
        metadata = step.get("metadata") or {}
        source = metadata.get("sourceTrajectoryStepInfo") or {}
        index = source.get("stepIndex")
        if index is not None:
            if type(index) is not int or index < 0:
                raise ValueError("Antigravity returned an invalid trajectory index")
            bases.add(index - offset)
    if len(bases) == 1:
        base = bases.pop()
    elif not bases and len(steps) == total - start:
        base = start  # Older servers honor the requested range without metadata.
    elif not bases and len(steps) == total:
        base = 0
    else:
        raise ValueError("Antigravity trajectory coverage is unknown")
    current = [(base + offset, step) for offset, step in enumerate(steps) if start <= base + offset < total]
    if base < 0 or [index for index, _ in current] != list(range(start, total)):
        raise ValueError("Antigravity trajectory coverage is incomplete")
    return current, base + len(steps)


def _check_hard_timeout(value: float | None) -> None:
    if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value <= 0):
        raise ValueError("Antigravity hard timeout must be finite and positive")


def _native_completion_facts(cascade_id: str, current: list[tuple[int, dict[str, Any]]]) -> dict[str, Any]:
    """One private completion projection, without tool outputs or reasoning."""
    facts = []
    for index, step in current:
        metadata = step.get("metadata") or {}
        fact = {"type": step.get("type"), "status": step.get("status"), "metadata": {
            "createdAt": metadata.get("createdAt"), "executionId": metadata.get("executionId"),
            "sourceTrajectoryStepInfo": {"stepIndex": index}}}
        if step.get("type") == "CORTEX_STEP_TYPE_USER_INPUT":
            fact["userInput"] = step.get("userInput")
        if step.get("type") == "CORTEX_STEP_TYPE_ERROR_MESSAGE":
            fact["errorMessage"] = {"shouldShowUser": (step.get("errorMessage") or {}).get("shouldShowUser")}
        if index == current[-1][0]:
            planner = step.get("plannerResponse") or {}
            fact["plannerResponse"] = {key: planner[key] for key in
                ("response", "modifiedResponse", "stopReason", "toolCalls") if key in planner}
        facts.append(fact)
    return {"thread_id": cascade_id, "start_step_index": current[0][0],
        "total_steps": current[-1][0] + 1, "steps": facts}


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
    *, deadline: float | None = None,
) -> dict[str, Any] | None:
    """Discover a running local endpoint without reusing an availability verdict.

    Readiness can bound the whole observation, including process listing. An
    execution caller without that deadline retains the existing phase limits.
    """
    source_env = os.environ if env is None else env
    explicit_url = str(source_env.get("ANTIGRAVITY_LS_URL") or "").strip()
    explicit_token = str(source_env.get("ANTIGRAVITY_CSRF_TOKEN") or "").strip()
    if explicit_url or explicit_token:
        if not explicit_url or not explicit_token:
            return None
        try:
            explicit_url = _local_url(explicit_url)
            timeout = 2.5 if deadline is None else min(2.5, deadline - time.monotonic())
            if timeout <= 0:
                return None
            _rpc_post(
                explicit_url,
                explicit_token,
                "GetCascadeModelConfigData",
                {},
                timeout=timeout,
            )
            return {
                "url": explicit_url.rstrip("/"),
                "csrf_token": explicit_token,
                "pid": None,
                "source": "environment",
            }
        except (CodexChatAgentError, ValueError):
            return None

    process_timeout = 3.0 if deadline is None else min(3.0, deadline - time.monotonic())
    if process_timeout <= 0:
        return None
    try:
        ps_out = subprocess.check_output(
            ["ps", "-u", str(os.getuid()), "-o", "pid=,command="],
            text=True,
            encoding="utf-8",
            errors="replace",
            stderr=subprocess.DEVNULL,
            timeout=process_timeout,
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

    discovery_deadline = time.monotonic() + 4.0
    deadline = min(discovery_deadline, deadline) if deadline is not None else discovery_deadline
    for pid, token in candidates:
        if time.monotonic() >= deadline:
            break
        try:
            lsof_out = subprocess.check_output(
                ["lsof", "-nP", "-a", "-p", str(pid), "-iTCP", "-sTCP:LISTEN"],
                text=True,
                encoding="utf-8",
                errors="replace",
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
    # The four-second discovery allowance covers this whole advisory read,
    # rather than beginning again after a slow process-listing phase.
    discovered = discover_antigravity_language_server(env=env, deadline=time.monotonic() + 4.0)
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


def _native_user_input_text(step: dict[str, Any]) -> str | None:
    """Read the one native text item; any redundant response must agree."""
    content = step.get("userInput")
    if not isinstance(content, dict):
        return None
    items = content.get("items")
    prompt = (items[0].get("text") if isinstance(items, list) and len(items) == 1
              and isinstance(items[0], dict) else None)
    if (not isinstance(prompt, str) or items != [{"text": prompt}]
            or ("userResponse" in content and content["userResponse"] != prompt)):
        return None
    return prompt


class AntigravityAdapter:
    """Local text Chat and trusted-owner Steward over LanguageServerService.

    Restricted Chat disables native tools. Trusted-owner uses Antigravity's
    existing approvals; LoopX never accepts those approvals on the user's behalf.
    Governed Tasks and workspace grants retain their existing executor owner.
    By default, five minutes without native progress stops an invocation; an
    explicit hard_timeout_sec also bounds wall time even while it progresses.
    """

    adapter_kind = "antigravity_ls"

    @staticmethod
    def interrupted_answer_observation(*, cascade_id: str, request_ref: str, message: str,
                                       started_at: str, completed_at: str,
                                       input_anchor: dict[str, Any]) -> dict[str, Any] | None:
        """Read a stable original segment for a read-only answer review only.

        This proves neither absence of effects nor permission to repeat them.
        Retain bounded visible tool results, never private reasoning. Unknown,
        asynchronous, live, truncated or normally answered segments stay held.
        """
        try:
            found = discover_antigravity_language_server()
            start = input_anchor.get("start_step_index")
            if found is None or input_anchor.get("thread_id") != cascade_id or type(start) is not int or start < 0:
                return None
            def read(method: str, **payload: Any) -> dict[str, Any]:
                return _rpc_post(found["url"], found["csrf_token"], method,
                    {"cascadeId": cascade_id, **payload}, timeout=10.0)
            before = read("GetCascadeTrajectory")
            total = before.get("numTotalSteps")
            if (before.get("status") != "CASCADE_RUN_STATUS_IDLE" or type(total) is not int or total <= start + 2
                    or (before.get("trajectory") or {}).get("cascadeId") != cascade_id):
                return None
            raw = read("GetCascadeTrajectorySteps", startIndex=start, endIndex=total).get("steps")
            if not isinstance(raw, list):
                return None
            indexed, covered = _observed_turn_steps(raw, start, total)
            if covered != total or not indexed or indexed[0][0] != start:
                return None
            end = next((i for i, s in indexed if i > start and s.get("type") == "CORTEX_STEP_TYPE_USER_INPUT"), total)
            current = [s for i, s in indexed if start <= i < end]
            if len(current) > 128 or raw != read("GetCascadeTrajectorySteps", startIndex=start, endIndex=total).get("steps") or before != read("GetCascadeTrajectory"):
                return None
            user, terminal = current[0], current[-1]
            # A cleared historical segment can retain visible answer text while
            # losing its completion marker and tool payloads. It is not a
            # verified final or evidence that effects did not occur. Only the
            # existing restricted answer review may consume this text.
            cleared_answer = (end < total
                and terminal.get("type") == "CORTEX_STEP_TYPE_PLANNER_RESPONSE"
                and all(step.get("status") == "CORTEX_STEP_STATUS_CLEARED" for step in current))
            if (user.get("type") != "CORTEX_STEP_TYPE_USER_INPUT"
                    or not cleared_answer and terminal.get("type") != "CORTEX_STEP_TYPE_ERROR_MESSAGE"):
                return None
            meta = user["metadata"]
            execution = meta.get("executionId")
            prompt = _native_user_input_text(user)
            if (not isinstance(execution, str) or not execution or prompt is None
                    or not manager_input_matches(prompt, message=message, request_ref=request_ref)
                    or hashlib.sha256(prompt.encode()).hexdigest() != input_anchor.get("input_sha256")
                    or not datetime.fromisoformat(started_at) <= datetime.fromisoformat(meta["createdAt"])
                    <= datetime.fromisoformat(terminal["metadata"]["createdAt"]) <= datetime.fromisoformat(completed_at)):
                return None
            results = []
            for step in current:
                step_execution = (step.get("metadata") or {}).get("executionId")
                if (step.get("status") != ("CORTEX_STEP_STATUS_CLEARED" if cleared_answer else "CORTEX_STEP_STATUS_DONE")
                        or step_execution != execution and not (not cleared_answer and step is terminal and step_execution is None)):
                    return None
                kind = step.get("type")
                payload = {"CORTEX_STEP_TYPE_USER_INPUT": "userInput", "CORTEX_STEP_TYPE_PLANNER_RESPONSE": "plannerResponse",
                    "CORTEX_STEP_TYPE_RUN_COMMAND": "runCommand", "CORTEX_STEP_TYPE_VIEW_FILE": "viewFile",
                    "CORTEX_STEP_TYPE_ERROR_MESSAGE": "errorMessage"}.get(kind)
                if cleared_answer:
                    payload = {"CORTEX_STEP_TYPE_USER_INPUT": "userInput",
                        "CORTEX_STEP_TYPE_PLANNER_RESPONSE": "plannerResponse",
                        "CORTEX_STEP_TYPE_GENERIC": "generic", "CORTEX_STEP_TYPE_CHECKPOINT": "checkpoint",
                        "CORTEX_STEP_TYPE_SYSTEM_MESSAGE": "systemMessage"}.get(kind)
                if payload is None or set(step) - {"type", "status", "metadata", payload}:
                    return None
                if cleared_answer:
                    if step is terminal:
                        planner = step.get("plannerResponse") or {}
                        text = planner.get("modifiedResponse") or planner.get("response")
                        if planner.get("toolCalls") or not isinstance(text, str) or not text.strip():
                            return None
                        results.append({"visible_response": text})
                    continue
                if kind == "CORTEX_STEP_TYPE_RUN_COMMAND":
                    command = step["runCommand"]
                    if command.get("blocking") is not True or type(command.get("exitCode")) is not int:
                        return None
                    results.append({key: command[key] for key in ("commandLine", "cwd", "exitCode", "combinedOutput")
                                    if key in command})
                elif kind == "CORTEX_STEP_TYPE_VIEW_FILE":
                    # The original provider's visible file result is evidence,
                    # never a request to open that URI or repeat an operation.
                    view = step["viewFile"]
                    uri, content = view.get("absolutePathUri"), view.get("content")
                    if not isinstance(uri, str) or not isinstance(content, str):
                        return None
                    parsed = urlsplit(uri)
                    if (parsed.scheme != "file" or parsed.netloc or not parsed.path.startswith("/")
                            or parsed.query or parsed.fragment):
                        return None
                    results.append({"file_uri": uri, "content": content})
                elif kind == "CORTEX_STEP_TYPE_PLANNER_RESPONSE":
                    if (step.get("plannerResponse") or {}).get("response") or (step.get("plannerResponse") or {}).get("modifiedResponse"):
                        return None  # A visible answer requires the existing result adoption owner.
                elif step is not user and step is not terminal:
                    return None
            if not results or len(json.dumps(results, ensure_ascii=False).encode()) > 256 * 1024:
                return None
            return {"thread_id": cascade_id, "request_ref": request_ref, "execution_id": execution,
                "start_step_index": start, "end_step_index": end, "total_steps": total,
                "input_sha256": input_anchor["input_sha256"], "input_created_at": meta["createdAt"],
                "read_only_review": True, "no_final_answer": True, "effects_observed": True,
                "visible_results": results}
        except (KeyError, TypeError, ValueError, AttributeError, CodexChatAgentError):
            return None

    @staticmethod
    def quota_failure_observation(*, cascade_id: str, request_ref: str, message: str,
                                  started_at: str, completed_at: str,
                                  input_anchor: dict[str, Any]) -> dict[str, Any] | None:
        """Prove one native input ended at quota before any model/tool output.

        An unconfirmed cancel is not proof of effects or of their absence. Read
        the exact original input twice from a stable idle cascade instead. Only
        the latest two-step input/quota-error sequence qualifies; any output,
        tool, later input, cleared step or unknown native state fails closed.
        """
        try:
            found = discover_antigravity_language_server()
            start = input_anchor.get("start_step_index")
            if found is None or input_anchor.get("thread_id") != cascade_id or type(start) is not int or start < 0:
                return None
            def read(method: str, **payload: Any) -> dict[str, Any]:
                return _rpc_post(found["url"], found["csrf_token"], method,
                                 {"cascadeId": cascade_id, **payload}, timeout=5.0)
            before = read("GetCascadeTrajectory")
            total = before.get("numTotalSteps")
            if (before.get("status") != "CASCADE_RUN_STATUS_IDLE" or type(total) is not int or total != start + 2
                    or (before.get("trajectory") or {}).get("cascadeId") != cascade_id):
                return None
            raw = read("GetCascadeTrajectorySteps", startIndex=start, endIndex=total)["steps"]
            indexed, covered = _observed_turn_steps(raw, start, total)
            again = read("GetCascadeTrajectorySteps", startIndex=start, endIndex=total)["steps"]
            after = read("GetCascadeTrajectory")
            if covered != total or raw != again or before != after or len(indexed) != 2:
                return None
            user, terminal = [step for _, step in indexed]
            if (user.get("type") != "CORTEX_STEP_TYPE_USER_INPUT"
                    or terminal.get("type") != "CORTEX_STEP_TYPE_ERROR_MESSAGE"
                    or any(step.get("status") != "CORTEX_STEP_STATUS_DONE" for step in (user, terminal))
                    or set(user) - {"type", "status", "metadata", "userInput"}
                    or set(terminal) - {"type", "status", "metadata", "errorMessage"}):
                return None
            metadata = user["metadata"]
            execution = metadata.get("executionId")
            created = datetime.fromisoformat(metadata["createdAt"])
            ended = datetime.fromisoformat(terminal["metadata"]["createdAt"])
            prompt = _native_user_input_text(user)
            if (not isinstance(execution, str) or not execution
                    or terminal["metadata"].get("executionId") not in (None, execution)
                    or not datetime.fromisoformat(started_at) <= created <= ended <= datetime.fromisoformat(completed_at)
                    or prompt is None or not manager_input_matches(prompt, message=message, request_ref=request_ref)):
                return None
            digest = hashlib.sha256(prompt.encode()).hexdigest()
            if input_anchor.get("input_sha256") != digest:
                return None
            error = terminal["errorMessage"]["error"]
            details = [json.loads(value) for value in error.get("rpcErrorDetails", [])]
            quota = [value for value in details if value.get("@type") == "type.googleapis.com/google.rpc.ErrorInfo"
                     and value.get("domain") == "cloudcode-pa.googleapis.com" and value.get("reason") == "QUOTA_EXHAUSTED"]
            if error.get("errorCode") != 429 or len(quota) != 1 or not quota[0].get("metadata", {}).get("model"):
                return None
            return {"thread_id": cascade_id, "request_ref": request_ref, "execution_id": execution,
                "input_created_at": metadata["createdAt"], "start_step_index": start, "total_steps": total,
                "input_sha256": digest, "model_id": quota[0]["metadata"]["model"],
                "pre_execution_quota_failure": True, "only_original_user_item": True,
                "latest_input_verified": True, "observed_at": time.time()}
        except (KeyError, TypeError, ValueError, AttributeError, CodexChatAgentError):
            return None

    @staticmethod
    def metadata_review_capacity(*, cascade_id: str, model_id: str) -> dict[str, Any] | None:
        """Observe the existing model's quota; never probe it with generation.

        A positive fraction alone is insufficient: an invocation can have
        reached its limit with a small advertised remainder. Preserve that
        native failure alongside the exact model's quota epoch.
        """
        try:
            found = discover_antigravity_language_server()
            if found is None:
                return None
            def read(method: str, payload: dict[str, Any]) -> dict[str, Any]:
                return _rpc_post(found["url"], found["csrf_token"], method, payload, timeout=5.0)
            before = read("GetCascadeTrajectory", {"cascadeId": cascade_id})
            total = before.get("numTotalSteps")
            if (before.get("status") != "CASCADE_RUN_STATUS_IDLE" or type(total) is not int or total < 2
                    or (before.get("trajectory") or {}).get("cascadeId") != cascade_id):
                return None
            start = max(0, total - 128)
            steps, covered = _observed_turn_steps(read("GetCascadeTrajectorySteps",
                {"cascadeId": cascade_id, "startIndex": start, "endIndex": total})["steps"], start, total)
            inputs = [i for i, step in steps if step.get("type") == "CORTEX_STEP_TYPE_USER_INPUT"]
            if not inputs and start:
                steps, covered = _observed_turn_steps(read("GetCascadeTrajectorySteps",
                    {"cascadeId": cascade_id, "startIndex": 0, "endIndex": total})["steps"], 0, total)
                inputs = [i for i, step in steps if step.get("type") == "CORTEX_STEP_TYPE_USER_INPUT"]
            if not inputs or covered != total:
                return None
            latest = [step for i, step in steps if i >= inputs[-1]]
            exhausted = any(str(((step.get("errorMessage") or {}).get("error") or {}).get(
                "userErrorMessage") or "").startswith("Individual quota reached.")
                for step in latest)
            planner = latest[-1].get("plannerResponse") or {}
            normal = (latest[-1].get("type") == "CORTEX_STEP_TYPE_PLANNER_RESPONSE"
                and planner.get("stopReason") == "STOP_REASON_STOP_PATTERN" and not planner.get("toolCalls")
                and all(_step_allows_completed_turn(step, final=latest[-1]) for step in latest)
                and isinstance((latest[0].get("metadata") or {}).get("executionId"), str)
                and bool((latest[0].get("metadata") or {}).get("executionId"))
                and len({(step.get("metadata") or {}).get("executionId") for step in latest}) == 1)
            if not normal and not exhausted:
                return None  # An unknown native failure is not capacity evidence.
            status = read("GetUserStatus", {})
            user = status["userStatus"]
            account = user.get("email")
            if not isinstance(account, str) or not account.strip():
                return None
            rows = user["cascadeModelConfigData"]["clientModelConfigs"]
            matching = [row for row in rows if row.get("modelId") == model_id]
            if len(matching) != 1:
                return None
            quota = matching[0]["quotaInfo"]
            fraction, reset = quota.get("remainingFraction", 0), quota.get("resetTime")
            if (type(fraction) not in (int, float) or not math.isfinite(fraction)
                    or not 0 <= fraction <= 1 or not isinstance(reset, str)
                    or datetime.fromisoformat(reset).tzinfo is None):
                return None
            after = read("GetCascadeTrajectory", {"cascadeId": cascade_id})
            if (after.get("status") != before["status"] or after.get("numTotalSteps") != total
                    or (after.get("trajectory") or {}).get("cascadeId") != cascade_id):
                return None
            return {"model_id": model_id, "account_ref": hashlib.sha256(account.strip().casefold().encode()).hexdigest(),
                "remaining_fraction": fraction, "reset_time": reset,
                "execution_id": (latest[0].get("metadata") or {}).get("executionId"),
                "observed_at": time.time(), "quota_exhausted": exhausted, "normal_completion": normal}
        except (KeyError, TypeError, ValueError, AttributeError, CodexChatAgentError):
            return None

    @staticmethod
    def completed_result_observation(*, cascade_id: str, request_ref: str, message: str,
                                     started_at: str, completed_at: str,
                                     work_dir: Path, input_anchor: dict[str, Any] | None = None,
                                     include_raw_response: bool = False,
                                     retained_result: dict[str, Any] | None = None,
                                     include_retained_result: bool = False) -> dict[str, Any] | None:
        """Read an existing result; never start, send to or cancel a cascade.

        Legacy owner requests require the exact Core-generated source context,
        input time inside the failed Turn, and one native execution identity.
        New Turns additionally freeze their prompt hash before provider send.
        """
        try:
            if retained_result is not None:
                # Core retained this exact invocation before parsing/delivery.
                # It is not a fresh claim about the provider's latest input.
                if (input_anchor is None or retained_result.get("thread_id") != cascade_id
                        or retained_result.get("start_step_index") != input_anchor.get("start_step_index")):
                    return None
                total = retained_result.get("total_steps")
                if type(total) is not int or total < 2:
                    return None
                indexed, covered = _observed_turn_steps(retained_result["steps"], input_anchor["start_step_index"], total)
                if covered != total:
                    return None
            else:
                found = discover_antigravity_language_server()
                if found is None:
                    return None
                def read(method: str, **payload: Any) -> dict[str, Any]:
                    return _rpc_post(found["url"], found["csrf_token"], method,
                                     {"cascadeId": cascade_id, **payload}, timeout=10.0)

                before = read("GetCascadeTrajectory")
                total = before.get("numTotalSteps")
                if (before.get("status") != "CASCADE_RUN_STATUS_IDLE" or type(total) is not int or total < 2
                        or (before.get("trajectory") or {}).get("cascadeId") != cascade_id):
                    return None
                raw = read("GetCascadeTrajectorySteps", startIndex=0, endIndex=total).get("steps")
                if not isinstance(raw, list):
                    return None
                indexed, covered = _observed_turn_steps(raw, 0, total)
                after = read("GetCascadeTrajectory")
                if (covered != total or after.get("status") != before["status"]
                        or after.get("numTotalSteps") != total
                        or (after.get("trajectory") or {}).get("cascadeId") != cascade_id):
                    return None
            inputs = [(i, step) for i, step in indexed if step.get("type") == "CORTEX_STEP_TYPE_USER_INPUT"]
            if not inputs:
                return None
            # Historical results are usable only through the original Core
            # input anchor. Legacy records still require the latest input.
            if input_anchor is not None:
                anchored = input_anchor.get("start_step_index")
                if type(anchored) is not int:
                    return None
                matching = [item for item in inputs if item[0] == anchored]
                if len(matching) != 1:
                    return None
                start, user = matching[0]
            else:
                start, user = inputs[-1]
            end = next((index for index, _ in inputs if index > start), total)
            metadata = user.get("metadata") or {}
            created = datetime.fromisoformat(metadata["createdAt"])
            if not datetime.fromisoformat(started_at) <= created <= datetime.fromisoformat(completed_at):
                return None
            prompt = _native_user_input_text(user)
            if prompt is None:
                return None
            if not manager_input_matches(prompt, message=message, request_ref=request_ref):
                return None
            prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()
            if input_anchor is not None and (input_anchor.get("thread_id") != cascade_id
                    or input_anchor.get("start_step_index") != start or input_anchor.get("input_sha256") != prompt_hash):
                return None
            execution = metadata.get("executionId")
            current = [(index, step) for index, step in indexed if start <= index < end]
            if not current:
                return None
            final = current[-1][1]
            if not isinstance(execution, str) or not execution or any(
                    not _step_allows_completed_turn(step, final=final)
                    or (step.get("metadata") or {}).get("executionId") != execution
                    for _, step in current):
                return None
            planner = final.get("plannerResponse") or {}
            answer = planner.get("modifiedResponse") or planner.get("response")
            if (final.get("type") != "CORTEX_STEP_TYPE_PLANNER_RESPONSE" or planner.get("toolCalls")
                    or planner.get("stopReason") != "STOP_REASON_STOP_PATTERN"
                    or not isinstance(answer, str) or not answer.strip()):
                return None
            response = parse_agent_response(answer, protected_paths=[work_dir])
            if not response.get("message"):
                return None
            return {"thread_id": cascade_id, "request_ref": request_ref, "execution_id": execution,
                "input_created_at": metadata["createdAt"], "start_step_index": start, "total_steps": total,
                "input_sha256": prompt_hash, "answer_sha256": hashlib.sha256(answer.encode()).hexdigest(),
                "latest_input_verified": start == inputs[-1][0],
                **({"observation_source": "retained_turn_result"} if retained_result is not None else {}),
                **({"end_step_index": end} if start != inputs[-1][0] else {}),
                "normal_terminal_verified": True, "response": response,
                **({"retained_result": _native_completion_facts(cascade_id, current)} if include_retained_result else {}),
                **({"raw_response": answer} if include_raw_response else {})}
        except (KeyError, TypeError, ValueError, AttributeError, CodexChatAgentError):
            return None

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
        hard_timeout_sec: float | None = None,
    ) -> None:
        self.work_dir = work_dir.resolve()
        _check_hard_timeout(hard_timeout_sec)
        self.objective = objective
        self.cascade_id = cascade_id
        self.upstream_thread_id = cascade_id
        self.ls_url = ls_url
        self.csrf_token = csrf_token
        self.model_id = model_id
        self.model_placeholder = model_placeholder
        self.reasoning_effort = reasoning_effort
        self.runtime_profile = runtime_profile
        self._read_only_review = False
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
        hard_timeout_sec: float | None = None,
    ) -> "AntigravityAdapter":
        if runtime_profile not in {"restricted", "trusted_owner"}:
            raise ValueError("unsupported Antigravity Chat runtime profile")
        _check_hard_timeout(hard_timeout_sec)
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
                # The Session owner already chose this visible history. A
                # recency cutoff can drop an earlier constraint during migration.
                # JSON preserves roles, whitespace and quoted frame markers.
                history = [{"role": str(item.get("role") or "user"),
                            "content": str(item.get("content") or "")}
                           for item in self._pending_history]
                parts.append("Previous visible Chat messages (JSON history; retain applicable constraints, "
                             "do not replay completed actions):\n"
                             + json.dumps(history, ensure_ascii=False))
        parts.append(
            _turn_prompt(
                message,
                runtime_profile="restricted" if self._read_only_review else self.runtime_profile,
            )
        )
        return "\n\n".join(parts)

    def _build_cascade_config(self) -> dict[str, Any]:
        # Never inherit an old cascade's tool permissions or userAgentSpec.
        # Restricted Chat is text-only; trusted_owner retains native host approval.
        restricted = self.runtime_profile == "restricted" or self._read_only_review
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

    def start_turn_with_attachments(
        self,
        message: str,
        event_sink: Callable[[str, dict[str, Any]], None] | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        from ...chat_attachments import normalize_chat_image_attachments

        normalized = normalize_chat_image_attachments(attachments or [])
        media = [
            {
                "mimeType": row["mime_type"],
                "inlineData": row["data_url"].split(",", 1)[1],
                "description": row["name"],
            }
            for row in normalized
        ]
        return self.start_turn(message, event_sink, media=media)

    def start_turn(self, message: str,
                   event_sink: Callable[[str, dict[str, Any]], None] | None = None,
                   *, read_only_review: bool = False,
                   media: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        self._read_only_review = read_only_review
        self._interrupted.clear()
        self._cancel_acknowledged = False
        try:
            return self._run_turn(message, event_sink, media=media)
        except BaseException as failure:
            # A timeout/transport or permission failure is not a successful turn.
            # Retain an unconfirmed cancellation as an error for the caller fence.
            try:
                self.interrupt_turn()
            except Exception as cancellation:
                note = f"Antigravity cancellation remains unconfirmed ({type(cancellation).__name__})."
                failure.add_note(note)
                if isinstance(failure, CodexChatAgentError):
                    failure.args = (f"{failure} {note}",)
            raise
        finally:
            self._active_turn_id = None
            self._read_only_review = False

    def _run_turn(
        self,
        message: str,
        event_sink: Callable[[str, dict[str, Any]], None] | None = None,
        *,
        media: list[dict[str, Any]] | None = None,
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
            timeout=10.0,
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
            **({"media": media} if media else {}),
        }
        with self._send_lock:
            self._check_interrupted()
            self._active_turn_id = turn_id
            if event_sink is not None:
                event_sink("turn.started", {"upstream_turn_id": turn_id, "threadId": self.cascade_id,
                    "native_input": {"thread_id": self.cascade_id, "start_step_index": start_step_index,
                                     "input_sha256": hashlib.sha256(full_prompt.encode()).hexdigest()}})
            _rpc_post(self.ls_url, self.csrf_token, "SendUserCascadeMessage", send_payload, timeout=15.0)
            self._system_primed = True
            self._pending_history.clear()

        stream_filter = VisibleResponseStreamFilter()
        emitted_visible_delta = False
        emitted_text_by_step: dict[int, str] = {}
        activity = AntigravityActivity(self.work_dir)
        latest_response_text = ""
        last_progress_at = time.monotonic()
        deadline = last_progress_at + self.hard_timeout_sec if self.hard_timeout_sec is not None else None
        previous_progress: Any = None
        saw_running_or_new_step = False
        observed_native_errors: set[int] = set()

        def timeout(kind: str) -> None:
            limit = self.hard_timeout_sec if kind == "hard" else ANTIGRAVITY_IDLE_TIMEOUT_SEC
            detail = {"kind": kind, "limit_seconds": limit,
                      "native_error_count": len(observed_native_errors)}
            if event_sink is not None:
                event_sink("agent.phase", {"method": "native/timeout", "timeout": detail,
                    "label": "原生助手达到执行时限" if kind == "hard" else "等待原生助手进展超时"})
            error = CodexChatAgentError(
                f"Antigravity did not finish before the turn deadline ({kind} timeout).",
                gate=None, error_code="response_timeout")
            error.native_error_info = {"timeout": detail}
            raise error

        while True:
            now = time.monotonic()
            if deadline is not None and now >= deadline:
                timeout("hard")
            if now - last_progress_at >= ANTIGRAVITY_IDLE_TIMEOUT_SEC:
                timeout("idle")
            self._check_interrupted()
            self._interrupted.wait(0.25)
            self._check_interrupted()
            try:
                traj = _rpc_post(
                    self.ls_url,
                    self.csrf_token,
                    "GetCascadeTrajectory",
                    {"cascadeId": self.cascade_id},
                    timeout=10.0,
                )
            except CodexChatAgentError as exc:
                # Losing an idempotent observation does not mean that the
                # native invocation failed. Never resend its input, refresh its
                # progress clock, or bypass the existing stop/deadline checks.
                if exc.error_code != "antigravity_transport_error":
                    raise
                self._interrupted.wait(1.0)
                continue
            status = str(traj.get("status") or "")
            total_steps = int(traj.get("numTotalSteps") or 0)
            if status == "CASCADE_RUN_STATUS_RUNNING" or total_steps > start_step_index:
                saw_running_or_new_step = True

            current_steps: list[tuple[int, dict[str, Any]]] = []
            observed_total = total_steps
            if total_steps > start_step_index:
                self._check_interrupted()
                try:
                    steps_resp = _rpc_post(
                        self.ls_url,
                        self.csrf_token,
                        "GetCascadeTrajectorySteps",
                        {
                            "cascadeId": self.cascade_id,
                            "startIndex": start_step_index,
                            "endIndex": total_steps,
                        },
                        timeout=10.0,
                    )
                except CodexChatAgentError as exc:
                    if exc.error_code != "antigravity_transport_error":
                        raise
                    self._interrupted.wait(1.0)
                    continue
                steps = steps_resp.get("steps")
                try:
                    if not isinstance(steps, list):
                        raise ValueError("Antigravity returned no trajectory coverage")
                    current_steps, observed_total = _observed_turn_steps(steps, start_step_index, total_steps)
                except (AttributeError, TypeError, ValueError) as exc:
                    raise CodexChatAgentError(str(exc), gate=None, error_code="antigravity_rpc_error") from exc
                if current_steps:
                    if sum(step.get("type") == "CORTEX_STEP_TYPE_USER_INPUT" for _, step in current_steps) > 1:
                        # Another native input is not this invocation's result or
                        # cancellation target. Preserve it for its current owner.
                        self._active_turn_id = None
                        raise CodexChatAgentError("The native Antigravity input changed; no result was adopted or invocation cancelled.",
                                                  gate=None, error_code="upstream_transport_failure")
                    for step_idx, step in current_steps:
                        step_type = str(step.get("type") or "")
                        step_status = str(step.get("status") or "")
                        if self._read_only_review and step_type not in {
                                "CORTEX_STEP_TYPE_USER_INPUT", "CORTEX_STEP_TYPE_PLANNER_RESPONSE",
                                "CORTEX_STEP_TYPE_CHECKPOINT"}:
                            raise CodexChatAgentError("The format review produced a native tool step; no actions were admitted.",
                                                      gate=None, error_code="unsupported_configuration")
                        if step_status == "CORTEX_STEP_STATUS_WAITING":
                            raise CodexChatAgentError(
                                "Antigravity requested native permission; this invocation was cancelled. Review its permissions, then retry.",
                                gate={"kind": "host_tool_gate", "summary": "Antigravity requires native permission.",
                                      "next_action": "Review permissions in Antigravity, then resend this request in LoopX."},
                                error_code="host_tool_gate",
                            )
                        planner = step.get("plannerResponse")
                        if (step_type == "CORTEX_STEP_TYPE_PLANNER_RESPONSE"
                                and isinstance(planner, dict) and planner.get("toolCalls")
                                and (self.runtime_profile == "restricted" or self._read_only_review)):
                            raise CodexChatAgentError("Antigravity did not honor text-only Chat.",
                                                      gate=None, error_code="unsupported_configuration")
                        if event_sink is not None:
                            for phase in activity.observe(step_idx, step):
                                event_sink("agent.phase", phase)
                        if step_type == "CORTEX_STEP_TYPE_PLANNER_RESPONSE":
                            pr = step.get("plannerResponse") or {}
                            if not isinstance(pr, dict):
                                continue
                            text = str(pr.get("modifiedResponse") or pr.get("response") or "")
                            if text:
                                prev_text = emitted_text_by_step.get(step_idx, "")
                                if text.startswith(prev_text):
                                    delta_chunk = text[len(prev_text) :]
                                else:
                                    delta_chunk = text
                                latest_response_text = text
                                if delta_chunk and event_sink is not None:
                                    if not prev_text:
                                        combined_marker = stream_filter.marker_pending + delta_chunk.lstrip()
                                        continues_control_tag = bool(
                                            combined_marker
                                            and (
                                                combined_marker.startswith(CHAT_REVIEW_OPEN_TAG)
                                                or CHAT_REVIEW_OPEN_TAG.startswith(combined_marker)
                                            )
                                        )
                                        if (
                                            continues_control_tag
                                            and not combined_marker.startswith(CHAT_REVIEW_OPEN_TAG)
                                            and step_status != "CORTEX_STEP_STATUS_DONE"
                                        ):
                                            continue
                                        prior_texts = [
                                            val
                                            for idx, val in sorted(emitted_text_by_step.items())
                                            if idx != step_idx and val
                                        ]
                                        if (
                                            prior_texts
                                            and not delta_chunk.startswith("\n")
                                            and not continues_control_tag
                                        ):
                                            last_prior = prior_texts[-1]
                                            if not last_prior.endswith("\n"):
                                                delta_chunk = "\n\n" + delta_chunk
                                            elif not last_prior.endswith("\n\n"):
                                                delta_chunk = "\n" + delta_chunk
                                    visible_delta = stream_filter.feed(delta_chunk)
                                    if visible_delta:
                                        emitted_visible_delta = True
                                        event_sink("answer.delta", {"text": visible_delta})
                                emitted_text_by_step[step_idx] = text
                        elif step_type == "CORTEX_STEP_TYPE_ERROR_MESSAGE":
                            # The native trajectory also records recoverable
                            # API attempts and cleared errors. The native host
                            # owns retry: only its settled terminal error ends
                            # this turn. Do not cancel its in-flight backoff.
                            if (step_status != "CORTEX_STEP_STATUS_CLEARED"
                                    and step_idx not in observed_native_errors):
                                observed_native_errors.add(step_idx)
                                if event_sink is not None:
                                    # Error bodies can contain credentials, URLs
                                    # and private host paths. Publish only the
                                    # occurrence, never that native payload.
                                    event_sink("agent.phase", {"method": "native/error",
                                        "label": "原生助手记录了调用错误"})
                            continue

            # A running status alone is not progress. Observe only this input's
            # steps, including streamed text and command output; old history or
            # a stuck RUNNING flag cannot keep an invocation alive indefinitely.
            progress = [(index, step.get("status"),
                         (step.get("plannerResponse") or {}).get("modifiedResponse")
                         or (step.get("plannerResponse") or {}).get("response"),
                         (step.get("plannerResponse") or {}).get("thinking"),
                         (step.get("runCommand") or {}).get("combinedOutput"),
                         (step.get("runCommand") or {}).get("exitCode"))
                        for index, step in current_steps
                        if step.get("type") != "CORTEX_STEP_TYPE_ERROR_MESSAGE"]
            now = time.monotonic()
            if progress and progress != previous_progress:
                previous_progress = progress
                last_progress_at = now
            self._check_interrupted()
            if deadline is not None and now >= deadline:
                timeout("hard")
            if saw_running_or_new_step and status == "CASCADE_RUN_STATUS_IDLE" and observed_total == total_steps:
                final = current_steps[-1][1] if current_steps else {}
                if final.get("type") == "CORTEX_STEP_TYPE_ERROR_MESSAGE":
                    detail = (final.get("errorMessage") or {}).get("error") or {}
                    summary = (detail.get("userErrorMessage") or detail.get("shortError")
                               if isinstance(detail, dict) else str(detail))
                    error = CodexChatAgentError(summary or "Antigravity cascade step error",
                                                gate=None, error_code="antigravity_step_error")
                    error.native_error_info = detail
                    if (final.get("status") == "CORTEX_STEP_STATUS_DONE"
                            and all(step.get("status") in _SETTLED_STEP_STATUSES for _, step in current_steps)):
                        # Retire only this settled cancellation target. The
                        # original failure and possible effects remain unknown.
                        self._active_turn_id = None
                    raise error
                planner = final.get("plannerResponse") or {}
                if (final.get("status") == "CORTEX_STEP_STATUS_CANCELED"
                        and all(step.get("status") in {"CORTEX_STEP_STATUS_DONE", "CORTEX_STEP_STATUS_CANCELED"}
                                for _, step in current_steps)):
                    # A settled cancellation cannot produce a final answer.
                    # Do not cancel this already-idle invocation again or replay
                    # its unknown effects; let Core record the original failure.
                    self._active_turn_id = None
                    raise CodexChatAgentError("Antigravity ended with a cancelled step; execution may have occurred.",
                                              gate=None, error_code="upstream_transport_failure")
                # Native IDLE can occur between input, tool and planner steps.
                # Keep observing this invocation; only a finished answer after
                # all current work settles can close it. The existing idle and
                # hard deadlines, permission gates and cancellation still apply.
                if (planner.get("stopReason") in {None, "STOP_REASON_STOP_PATTERN"}
                        and (final.get("type") != "CORTEX_STEP_TYPE_PLANNER_RESPONSE" or planner.get("toolCalls")
                        or (planner.get("stopReason") is None and not self._read_only_review)
                        or any(step.get("status") in {"CORTEX_STEP_STATUS_GENERATING", "CORTEX_STEP_STATUS_RUNNING"}
                               for _, step in current_steps))):
                    continue
                if self._read_only_review:
                    inputs = [step for _, step in current_steps if step.get("type") == "CORTEX_STEP_TYPE_USER_INPUT"]
                    execution = (inputs[0].get("metadata") or {}).get("executionId") if len(inputs) == 1 else None
                    if (not execution or _native_user_input_text(inputs[0]) != full_prompt
                            or planner.get("stopReason") != "STOP_REASON_STOP_PATTERN"
                            or any(step.get("status") != "CORTEX_STEP_STATUS_DONE"
                                or (step.get("metadata") or {}).get("executionId") != execution for _, step in current_steps)):
                        raise CodexChatAgentError("The format review lacks exact normal text-only completion; no actions were admitted.",
                                                  gate=None, error_code="upstream_transport_failure")
                if (final.get("type") != "CORTEX_STEP_TYPE_PLANNER_RESPONSE"
                        or final.get("status") != "CORTEX_STEP_STATUS_DONE" or planner.get("toolCalls")
                        or any(
                            not _step_allows_completed_turn(step, final=final)
                            for _, step in current_steps
                        )
                        or not str(planner.get("modifiedResponse") or planner.get("response") or "").strip()
                        or planner.get("stopReason") != "STOP_REASON_STOP_PATTERN"):
                    raise CodexChatAgentError("Antigravity ended without a verified final response; execution may have occurred.",
                                              gate=None, error_code="upstream_transport_failure")
                break

        if event_sink is not None:
            # Keep only completion facts and the original input/final text,
            # never native tool output or raw reasoning. Core consumes this
            # privately and durably before parsing can fail or lose text.
            self._check_interrupted()
            event_sink("turn.native_result", {**_native_completion_facts(self.cascade_id, current_steps),
                "upstream_turn_id": turn_id})
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

        if self._read_only_review:
            try:
                start = latest_response_text.rfind(CHAT_REVIEW_OPEN_TAG)
                end = latest_response_text.rfind(CHAT_REVIEW_CLOSE_TAG)
                if start < 0 or end <= start:
                    raise ValueError("missing format review envelope")
                payload = json.loads(latest_response_text[start + len(CHAT_REVIEW_OPEN_TAG):end])
                if (not isinstance(payload, dict)
                        or set(payload) - {"schema_version", "message", "context_handoff", "proposals", "gate",
                                               "goal_draft", "protected_action", "unfinished_work"}
                        or payload.get("proposals") not in (None, [])
                        or any(payload.get(key) is not None for key in
                            ("gate", "goal_draft", "protected_action", "unfinished_work"))):
                    raise ValueError("format review contains other or unresolved actions")
            except (TypeError, ValueError) as exc:
                raise CodexChatAgentError("The format review is incomplete or contains other actions; no actions were admitted.",
                                          gate=None, error_code="host_gate") from exc
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
