"""Read-only Antigravity cascade and quota observations for recovery and Steward."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time
from typing import Callable, Mapping
from urllib.parse import urlsplit
import uuid

from ...chat import parse_agent_response
from ...chat_agent import CodexChatAgentError
from .antigravity_cascade import (
    ManagerInputMatcher,
    _idle_total,
    _native_completion_facts,
    _observed_turn_steps,
    _open_idle_cascade,
    _parse_native_final_response,
    _read_cascade_steps,
    _turn_steps_allow_completion,
    _verify_anchored_user_step,
)
from .manager_input import manager_input_matches
from .antigravity_contracts import (
    AntigravityCompletedResultDict,
    AntigravityInterruptedAnswerDict,
    IndexedStep,
    JsonDict,
    JsonMap,
    as_completed_result,
    as_interrupted_answer,
)
from .antigravity_discovery import (
    ANTIGRAVITY_DEFAULT_MODEL,
    DiscoverFn,
    RpcPostFn,
    _extract_user_model_quota,
    _is_dict,
    _is_strict_int,
    _opt_dict,
    _rpc_post,
    _subdict,
    discover_antigravity_language_server,
    resolve_antigravity_model_placeholder,
)


def observe_interrupted_answer(
    *,
    cascade_id: str,
    request_ref: str,
    message: str,
    started_at: str,
    completed_at: str,
    input_anchor: JsonMap,
    discover_fn: DiscoverFn = discover_antigravity_language_server,
    rpc_post_fn: RpcPostFn = _rpc_post,
    manager_input_matches_fn: ManagerInputMatcher = manager_input_matches,
) -> AntigravityInterruptedAnswerDict | None:
    """Read a stable original segment for a read-only answer review only."""
    try:
        start = input_anchor.get("start_step_index")
        if input_anchor.get("thread_id") != cascade_id or not _is_strict_int(start) or start < 0:
            return None
        opened = _open_idle_cascade(
            cascade_id,
            timeout=10.0,
            discover_fn=discover_fn,
            rpc_post_fn=rpc_post_fn,
        )
        if opened is None:
            return None
        _, _, read, before, total = opened
        if total <= start + 2:
            return None
        first_read = _read_cascade_steps(read, start=start, total=total)
        if first_read is None:
            return None
        indexed, covered = first_read
        if covered != total or not indexed or indexed[0][0] != start:
            return None
        end = next(
            (i for i, s in indexed if i > start and s.get("type") == "CORTEX_STEP_TYPE_USER_INPUT"),
            total,
        )
        current = [s for i, s in indexed if start <= i < end]
        second_read = _read_cascade_steps(read, start=start, total=total) if len(current) <= 128 else None
        if (
            second_read is None
            or [s for _, s in second_read[0]] != [s for _, s in indexed]
            or before != read("GetCascadeTrajectory")
        ):
            return None
        user, terminal = current[0], current[-1]
        cleared_answer = (
            end < total
            and terminal.get("type") == "CORTEX_STEP_TYPE_PLANNER_RESPONSE"
            and all(step.get("status") == "CORTEX_STEP_STATUS_CLEARED" for step in current)
        )
        if not cleared_answer and terminal.get("type") != "CORTEX_STEP_TYPE_ERROR_MESSAGE":
            return None
        verified = _verify_anchored_user_step(
            user,
            cascade_id=cascade_id,
            start=start,
            message=message,
            request_ref=request_ref,
            started_at=started_at,
            completed_at=completed_at,
            input_anchor=input_anchor,
            end_created_at=_opt_dict(terminal["metadata"])["createdAt"],
            manager_input_matches_fn=manager_input_matches_fn,
        )
        if verified is None:
            return None
        execution, created_at, digest = verified
        expected_status = "CORTEX_STEP_STATUS_CLEARED" if cleared_answer else "CORTEX_STEP_STATUS_DONE"
        payloads = (
            {
                "CORTEX_STEP_TYPE_USER_INPUT": "userInput",
                "CORTEX_STEP_TYPE_PLANNER_RESPONSE": "plannerResponse",
                "CORTEX_STEP_TYPE_GENERIC": "generic",
                "CORTEX_STEP_TYPE_CHECKPOINT": "checkpoint",
                "CORTEX_STEP_TYPE_SYSTEM_MESSAGE": "systemMessage",
            }
            if cleared_answer
            else {
                "CORTEX_STEP_TYPE_USER_INPUT": "userInput",
                "CORTEX_STEP_TYPE_PLANNER_RESPONSE": "plannerResponse",
                "CORTEX_STEP_TYPE_RUN_COMMAND": "runCommand",
                "CORTEX_STEP_TYPE_VIEW_FILE": "viewFile",
                "CORTEX_STEP_TYPE_ERROR_MESSAGE": "errorMessage",
            }
        )
        results: list[JsonDict] = []
        for step in current:
            step_execution = _subdict(step, "metadata").get("executionId")
            kind = step.get("type")
            if (
                step.get("status") != expected_status
                or step_execution != execution
                and not (not cleared_answer and step is terminal and step_execution is None)
                or not isinstance(kind, str)
            ):
                return None
            payload = payloads.get(kind)
            if payload is None or set(step) - {"type", "status", "metadata", payload}:
                return None
            if cleared_answer:
                if step is terminal:
                    planner = _subdict(step, "plannerResponse")
                    text = planner.get("modifiedResponse") or planner.get("response")
                    if planner.get("toolCalls") or not isinstance(text, str) or not text.strip():
                        return None
                    results.append({"visible_response": text})
                continue
            if kind == "CORTEX_STEP_TYPE_RUN_COMMAND":
                command = _opt_dict(step["runCommand"])
                if command.get("blocking") is not True or not _is_strict_int(command.get("exitCode")):
                    return None
                results.append({
                    key: command[key]
                    for key in ("commandLine", "cwd", "exitCode", "combinedOutput")
                    if key in command
                })
            elif kind == "CORTEX_STEP_TYPE_VIEW_FILE":
                view = _opt_dict(step["viewFile"])
                uri, content = view.get("absolutePathUri"), view.get("content")
                if not isinstance(uri, str) or not isinstance(content, str):
                    return None
                parsed = urlsplit(uri)
                if (
                    parsed.scheme != "file"
                    or parsed.netloc
                    or not parsed.path.startswith("/")
                    or parsed.query
                    or parsed.fragment
                ):
                    return None
                results.append({"file_uri": uri, "content": content})
            elif kind == "CORTEX_STEP_TYPE_PLANNER_RESPONSE":
                planner = _subdict(step, "plannerResponse")
                if planner.get("response") or planner.get("modifiedResponse"):
                    return None
            elif step is not user and step is not terminal:
                return None
        if not results or len(json.dumps(results, ensure_ascii=False).encode()) > 256 * 1024:
            return None
        return as_interrupted_answer({
            "thread_id": cascade_id,
            "request_ref": request_ref,
            "execution_id": execution,
            "start_step_index": start,
            "end_step_index": end,
            "total_steps": total,
            "input_sha256": digest,
            "input_created_at": created_at,
            "read_only_review": True,
            "no_final_answer": True,
            "effects_observed": True,
            "visible_results": results,
        })
    except (KeyError, TypeError, ValueError, AttributeError, CodexChatAgentError):
        return None


def observe_quota_failure(
    *,
    cascade_id: str,
    request_ref: str,
    message: str,
    started_at: str,
    completed_at: str,
    input_anchor: JsonMap,
    discover_fn: DiscoverFn = discover_antigravity_language_server,
    rpc_post_fn: RpcPostFn = _rpc_post,
    now_epoch_fn: Callable[[], float] = time.time,
    manager_input_matches_fn: ManagerInputMatcher = manager_input_matches,
) -> JsonDict | None:
    """Prove one native input ended at quota before any model/tool output."""
    try:
        start = input_anchor.get("start_step_index")
        if input_anchor.get("thread_id") != cascade_id or not _is_strict_int(start) or start < 0:
            return None
        opened = _open_idle_cascade(
            cascade_id,
            timeout=5.0,
            discover_fn=discover_fn,
            rpc_post_fn=rpc_post_fn,
        )
        if opened is None:
            return None
        _, _, read, before, total = opened
        if total != start + 2:
            return None
        raw = read("GetCascadeTrajectorySteps", stepOffset=start, startIndex=start, endIndex=total)["steps"]
        if not isinstance(raw, list):
            return None
        indexed, covered = _observed_turn_steps(raw, start, total)
        again = read("GetCascadeTrajectorySteps", stepOffset=start, startIndex=start, endIndex=total)["steps"]
        if covered != total or raw != again or before != read("GetCascadeTrajectory") or len(indexed) != 2:
            return None
        user, terminal = indexed[0][1], indexed[1][1]
        if (
            terminal.get("type") != "CORTEX_STEP_TYPE_ERROR_MESSAGE"
            or any(step.get("status") != "CORTEX_STEP_STATUS_DONE" for step in (user, terminal))
            or set(user) - {"type", "status", "metadata", "userInput"}
            or set(terminal) - {"type", "status", "metadata", "errorMessage"}
        ):
            return None
        term_meta = _opt_dict(terminal["metadata"])
        verified = _verify_anchored_user_step(
            user,
            cascade_id=cascade_id,
            start=start,
            message=message,
            request_ref=request_ref,
            started_at=started_at,
            completed_at=completed_at,
            input_anchor=input_anchor,
            end_created_at=term_meta["createdAt"],
            manager_input_matches_fn=manager_input_matches_fn,
        )
        if verified is None or term_meta.get("executionId") not in (None, verified[0]):
            return None
        execution, created_at, digest = verified
        error = _opt_dict(_opt_dict(terminal["errorMessage"])["error"])
        raw_details = error.get("rpcErrorDetails", [])
        if not isinstance(raw_details, list):
            return None
        details = [json.loads(value) for value in raw_details]
        if any(not _is_dict(v) for v in details):
            return None
        quota = [
            v
            for v in details
            if _is_dict(v)
            and v.get("@type") == "type.googleapis.com/google.rpc.ErrorInfo"
            and v.get("domain") == "cloudcode-pa.googleapis.com"
            and v.get("reason") == "QUOTA_EXHAUSTED"
        ]
        quota_model = _subdict(quota[0], "metadata").get("model") if len(quota) == 1 else None
        if error.get("errorCode") != 429 or not isinstance(quota_model, str) or not quota_model:
            return None
        return {
            "thread_id": cascade_id,
            "request_ref": request_ref,
            "execution_id": execution,
            "input_created_at": created_at,
            "start_step_index": start,
            "total_steps": total,
            "input_sha256": digest,
            "model_id": quota_model,
            "pre_execution_quota_failure": True,
            "only_original_user_item": True,
            "latest_input_verified": True,
            "observed_at": now_epoch_fn(),
        }
    except (KeyError, TypeError, ValueError, AttributeError, CodexChatAgentError):
        return None


def observe_metadata_review_capacity(
    *,
    cascade_id: str,
    model_id: str,
    discover_fn: DiscoverFn = discover_antigravity_language_server,
    rpc_post_fn: RpcPostFn = _rpc_post,
    now_epoch_fn: Callable[[], float] = time.time,
) -> JsonDict | None:
    """Observe the existing model's quota; never probe it with generation."""
    try:
        canonical_model_id, _ = resolve_antigravity_model_placeholder(model_id)
        opened = _open_idle_cascade(
            cascade_id,
            timeout=5.0,
            discover_fn=discover_fn,
            rpc_post_fn=rpc_post_fn,
        )
        if opened is None:
            return None
        url, token, read, _, total = opened
        if total < 2:
            return None
        steps: list[IndexedStep] = []
        covered = 0
        inputs: list[int] = []
        for scan_start in (max(0, total - 128), 0):
            loaded = _read_cascade_steps(read, start=scan_start, total=total)
            if loaded is None:
                return None
            steps, covered = loaded
            inputs = [i for i, step in steps if step.get("type") == "CORTEX_STEP_TYPE_USER_INPUT"]
            if inputs or not scan_start:
                break
        if not inputs or covered != total:
            return None
        latest_indexed = [(i, step) for i, step in steps if i >= inputs[-1]]
        latest = [step for _, step in latest_indexed]
        exhausted = any(
            str(
                _subdict(_subdict(step, "errorMessage"), "error").get("userErrorMessage") or ""
            ).startswith("Individual quota reached.")
            for step in latest
        )
        planner = _subdict(latest[-1], "plannerResponse")
        execution = _subdict(latest[0], "metadata").get("executionId") if latest else None
        normal = (
            bool(latest)
            and latest[-1].get("type") == "CORTEX_STEP_TYPE_PLANNER_RESPONSE"
            and planner.get("stopReason") == "STOP_REASON_STOP_PATTERN"
            and not planner.get("toolCalls")
            and isinstance(execution, str)
            and bool(execution)
            and _turn_steps_allow_completion(latest_indexed, final=latest[-1], require_execution_id=execution)
        )
        if not normal and not exhausted:
            return None
        status = rpc_post_fn(url, token, "GetUserStatus", {}, timeout=5.0)
        extracted = _extract_user_model_quota(status.get("userStatus"), canonical_model_id)
        if extracted is None or _idle_total(read("GetCascadeTrajectory"), cascade_id) != total:
            return None
        account_ref, fraction, reset = extracted
        return {
            "model_id": canonical_model_id,
            "account_ref": account_ref,
            "remaining_fraction": fraction,
            "reset_time": reset,
            "execution_id": execution,
            "observed_at": now_epoch_fn(),
            "quota_exhausted": exhausted,
            "normal_completion": normal,
        }
    except (KeyError, TypeError, ValueError, AttributeError, CodexChatAgentError):
        return None


def observe_endpoint_model_capacity(
    *,
    model_id: str,
    reasoning_effort: str | None = None,
    env: Mapping[str, str] | None = None,
    discovered: JsonMap | None = None,
    discover_fn: DiscoverFn = discover_antigravity_language_server,
    rpc_post_fn: RpcPostFn = _rpc_post,
    now_epoch_fn: Callable[[], float] = time.time,
) -> JsonDict | None:
    """Observe the live LS account's quota for one offered model without opening a cascade."""
    try:
        canonical_model_id, _ = resolve_antigravity_model_placeholder(model_id, reasoning_effort)
        found = discovered or discover_fn(env=env, include_model_config=True)
        if found is None:
            return None
        status = rpc_post_fn(str(found["url"]), str(found["csrf_token"]), "GetUserStatus", {}, timeout=5.0)
        fallback_rows = _subdict(found, "model_config_data").get("clientModelConfigs") or []
        extracted = _extract_user_model_quota(
            status.get("userStatus"),
            canonical_model_id,
            fallback_rows=fallback_rows if isinstance(fallback_rows, list) else [],
        )
        if extracted is None:
            return None
        account_ref, fraction, reset = extracted
        exhausted = fraction <= 0
        return {
            "model_id": canonical_model_id,
            "account_ref": account_ref,
            "remaining_fraction": fraction,
            "reset_time": reset,
            "execution_id": f"endpoint-probe:{uuid.uuid4().hex}",
            "observed_at": now_epoch_fn(),
            "quota_exhausted": exhausted,
            "normal_completion": not exhausted,
        }
    except (KeyError, TypeError, ValueError, AttributeError, CodexChatAgentError):
        return None


def observe_failed_report(
    *,
    cascade_id: str,
    turn_id: str,
    native_timeout: bool,
    model_id: str | None = None,
    reasoning_effort: str | None = None,
    env: Mapping[str, str] | None = None,
    discover_fn: DiscoverFn = discover_antigravity_language_server,
    rpc_post_fn: RpcPostFn = _rpc_post,
    monotonic_fn: Callable[[], float] = time.monotonic,
    now_epoch_fn: Callable[[], float] = time.time,
    endpoint_capacity_fn: Callable[..., JsonDict | None] | None = None,
) -> JsonDict | None:
    """Read an idle Antigravity cascade and capacity without starting or mutating work."""
    found = discover_fn(env=env, include_model_config=True, deadline=monotonic_fn() + 3.0)
    if found is None:
        return None
    try:
        traj = rpc_post_fn(
            str(found["url"]),
            str(found["csrf_token"]),
            "GetCascadeTrajectory",
            {"cascadeId": cascade_id},
            timeout=2.5,
        )
    except CodexChatAgentError:
        return None
    if traj.get("status") != "CASCADE_RUN_STATUS_IDLE":
        return None
    cap_fn = endpoint_capacity_fn or observe_endpoint_model_capacity
    cap = cap_fn(
        model_id=str(model_id or ANTIGRAVITY_DEFAULT_MODEL),
        reasoning_effort=reasoning_effort,
        env=env,
        discovered=found,
    )
    return {
        "adapter_kind": "antigravity_ls",
        "turn_id": turn_id,
        "thread_id": cascade_id,
        "cascade_idle": True,
        "no_final_response": True,
        "native_timeout": native_timeout,
        "quota_available": bool(cap and cap.get("normal_completion") is True),
        "quota_observed_at": now_epoch_fn(),
    }


def observe_completed_result(
    *,
    cascade_id: str,
    request_ref: str,
    message: str,
    started_at: str,
    completed_at: str,
    work_dir: Path,
    input_anchor: JsonMap | None = None,
    include_raw_response: bool = False,
    retained_result: JsonMap | None = None,
    include_retained_result: bool = False,
    discover_fn: DiscoverFn = discover_antigravity_language_server,
    rpc_post_fn: RpcPostFn = _rpc_post,
    parse_response_fn: Callable[..., JsonDict] = parse_agent_response,
    manager_input_matches_fn: ManagerInputMatcher = manager_input_matches,
) -> AntigravityCompletedResultDict | None:
    """Read an existing result; never start, send to or cancel a cascade."""
    try:
        anchored = input_anchor.get("start_step_index") if input_anchor is not None else None
        if retained_result is not None:
            if (
                input_anchor is None
                or retained_result.get("thread_id") != cascade_id
                or retained_result.get("start_step_index") != anchored
            ):
                return None
            total = retained_result.get("total_steps")
            raw_steps = retained_result["steps"]
            if (
                not _is_strict_int(total)
                or total < 2
                or not isinstance(raw_steps, list)
                or not _is_strict_int(anchored)
            ):
                return None
            indexed, covered = _observed_turn_steps(raw_steps, anchored, total)
            if covered != total:
                return None
        else:
            opened = _open_idle_cascade(
                cascade_id,
                timeout=10.0,
                discover_fn=discover_fn,
                rpc_post_fn=rpc_post_fn,
            )
            if opened is None:
                return None
            _, _, read, _, total = opened
            if total < 2:
                return None
            scan_start = 0
            if input_anchor is not None:
                if not _is_strict_int(anchored) or anchored < 0 or anchored >= total:
                    return None
                scan_start = anchored
            loaded = _read_cascade_steps(read, start=scan_start, total=total)
            if loaded is None:
                return None
            indexed, covered = loaded
            if covered != total or _idle_total(read("GetCascadeTrajectory"), cascade_id) != total:
                return None
        inputs = [(i, step) for i, step in indexed if step.get("type") == "CORTEX_STEP_TYPE_USER_INPUT"]
        if not inputs:
            return None
        if input_anchor is not None:
            matching = [item for item in inputs if item[0] == anchored]
            if len(matching) != 1:
                return None
            start, user = matching[0]
        else:
            start, user = inputs[-1]
        end = next((index for index, _ in inputs if index > start), total)
        verified = _verify_anchored_user_step(
            user,
            cascade_id=cascade_id,
            start=start,
            message=message,
            request_ref=request_ref,
            started_at=started_at,
            completed_at=completed_at,
            input_anchor=input_anchor,
            manager_input_matches_fn=manager_input_matches_fn,
        )
        if verified is None:
            return None
        execution, created_at, prompt_hash = verified
        current = [(index, step) for index, step in indexed if start <= index < end]
        if not current:
            return None
        final = current[-1][1]
        if not _turn_steps_allow_completion(current, final=final, require_execution_id=execution):
            return None
        planner = _subdict(final, "plannerResponse")
        answer = planner.get("modifiedResponse") or planner.get("response")
        if (
            final.get("type") != "CORTEX_STEP_TYPE_PLANNER_RESPONSE"
            or planner.get("toolCalls")
            or planner.get("stopReason") != "STOP_REASON_STOP_PATTERN"
            or not isinstance(answer, str)
            or not answer.strip()
        ):
            return None
        response = _parse_native_final_response(
            answer,
            work_dir=work_dir,
            parse_response_fn=parse_response_fn,
        )
        if not response.get("message"):
            return None
        return as_completed_result({
            "thread_id": cascade_id,
            "request_ref": request_ref,
            "execution_id": execution,
            "input_created_at": created_at,
            "start_step_index": start,
            "total_steps": total,
            "input_sha256": prompt_hash,
            "answer_sha256": hashlib.sha256(answer.encode()).hexdigest(),
            "latest_input_verified": start == inputs[-1][0],
            **({"observation_source": "retained_turn_result"} if retained_result is not None else {}),
            **({"end_step_index": end} if start != inputs[-1][0] else {}),
            "normal_terminal_verified": True,
            "response": response,
            **({"retained_result": _native_completion_facts(cascade_id, current)} if include_retained_result else {}),
            **({"raw_response": answer} if include_raw_response else {}),
        })
    except (KeyError, TypeError, ValueError, AttributeError, CodexChatAgentError):
        return None
