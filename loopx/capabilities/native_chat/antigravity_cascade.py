"""Antigravity trajectory reading, turn progress, and idle observation helpers."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Callable, Mapping, Protocol, Sequence

from ...chat import (
    CHAT_REVIEW_CLOSE_TAG,
    CHAT_REVIEW_OPEN_TAG,
    VisibleResponseStreamFilter,
    _review_openings,
    parse_agent_response,
)
from ...chat_agent import CodexChatAgentError
from .antigravity_activity import AntigravityActivity
from .antigravity_contracts import (
    AntigravityRpcResponseDict,
    IndexedStep,
    JsonDict,
    JsonMap,
    JsonSeq,
)
from .antigravity_discovery import (
    DiscoverFn,
    EventSink,
    RpcPostFn,
    _is_dict,
    _is_strict_int,
    _opt_dict,
    _rpc_post,
    _subdict,
    discover_antigravity_language_server,
)
from .manager_input import manager_input_matches

ANTIGRAVITY_IDLE_TIMEOUT_SEC = 300.0
ANTIGRAVITY_LARGE_CASCADE_POLL_THRESHOLD = 200
ANTIGRAVITY_MAX_CASCADE_STEPS = 500
ANTIGRAVITY_ROLLOVER_MAX_MESSAGES = 24
ANTIGRAVITY_ROLLOVER_MAX_ITEM_CHARS = 4_000
ANTIGRAVITY_ROLLOVER_MAX_TOTAL_CHARS = 48_000

_SETTLED_STEP_STATUSES = {
    "CORTEX_STEP_STATUS_DONE",
    "CORTEX_STEP_STATUS_ERROR",
    "CORTEX_STEP_STATUS_CANCELED",
    "CORTEX_STEP_STATUS_CLEARED",
}

_MISSING_STEPS = object()

_RUNNING_STEP_BODIES = {
    "CORTEX_STEP_TYPE_PLANNER_RESPONSE": "plannerResponse",
    "CORTEX_STEP_TYPE_RUN_COMMAND": "runCommand",
    "CORTEX_STEP_TYPE_VIEW_FILE": "viewFile",
    "CORTEX_STEP_TYPE_CODE_ACTION": "codeAction",
    "CORTEX_STEP_TYPE_ERROR_MESSAGE": "errorMessage",
}

_NON_TOOL_STEP_TYPES = frozenset({
    "CORTEX_STEP_TYPE_USER_INPUT",
    "CORTEX_STEP_TYPE_PLANNER_RESPONSE",
    "CORTEX_STEP_TYPE_ERROR_MESSAGE",
    "CORTEX_STEP_TYPE_CHECKPOINT",
    "CORTEX_STEP_TYPE_SYSTEM_MESSAGE",
})


class CascadeReader(Protocol):
    def __call__(self, method: str, **payload: object) -> AntigravityRpcResponseDict: ...


class ManagerInputMatcher(Protocol):
    def __call__(self, prompt: str, *, message: str, request_ref: str) -> bool: ...


def _max_cascade_steps(env: Mapping[str, str] | None = None) -> int:
    raw = (env if env is not None else os.environ).get("LOOPX_ANTIGRAVITY_MAX_CASCADE_STEPS")
    if raw is None or not str(raw).strip():
        return ANTIGRAVITY_MAX_CASCADE_STEPS
    try:
        parsed = int(str(raw).strip())
    except ValueError:
        return ANTIGRAVITY_MAX_CASCADE_STEPS
    return parsed if parsed >= 0 else ANTIGRAVITY_MAX_CASCADE_STEPS


def _rollover_history(messages: Sequence[JsonMap]) -> list[dict[str, str]]:
    """Keep bounded recent visible messages when rolling over an overfull cascade."""
    kept: list[dict[str, str]] = []
    used = 0
    for item in reversed(list(messages)[-ANTIGRAVITY_ROLLOVER_MAX_MESSAGES:]):
        content = str(item.get("content") or "")
        if len(content) > ANTIGRAVITY_ROLLOVER_MAX_ITEM_CHARS:
            content = content[: ANTIGRAVITY_ROLLOVER_MAX_ITEM_CHARS - 3].rstrip() + "..."
        if kept and used + len(content) > ANTIGRAVITY_ROLLOVER_MAX_TOTAL_CHARS:
            break
        if len(content) > ANTIGRAVITY_ROLLOVER_MAX_TOTAL_CHARS:
            content = content[: ANTIGRAVITY_ROLLOVER_MAX_TOTAL_CHARS - 3].rstrip() + "..."
        kept.append({"role": str(item.get("role") or "user"), "content": content})
        used += len(content)
    kept.reverse()
    return kept


def _start_cascade(
    ls_url: str,
    csrf_token: str,
    work_dir: Path,
    *,
    rpc_post_fn: RpcPostFn = _rpc_post,
) -> str:
    started = rpc_post_fn(
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
    return cascade_id


def _step_allows_completed_turn(step: JsonMap, *, final: JsonMap) -> bool:
    """Only a settled intermediate step may precede a final planner answer."""
    error = step.get("errorMessage") or {}
    return (
        step.get("status") in _SETTLED_STEP_STATUSES
        and not (step is final and step.get("status") != "CORTEX_STEP_STATUS_DONE")
        and not (
            step.get("type") == "CORTEX_STEP_TYPE_ERROR_MESSAGE"
            and (step is final or (_is_dict(error) and error.get("shouldShowUser") is True))
        )
    )


def _turn_steps_allow_completion(
    current_steps: Sequence[IndexedStep],
    *,
    final: JsonMap,
    require_execution_id: str | None = None,
) -> bool:
    """Verify settled steps, tolerating pre-error aborted PENDING placeholders."""
    last_error_idx = max(
        (idx for idx, step in current_steps if step.get("type") == "CORTEX_STEP_TYPE_ERROR_MESSAGE"),
        default=None,
    )
    for idx, step in current_steps:
        if (
            step is not final
            and last_error_idx is not None
            and idx < last_error_idx
            and step.get("status") == "CORTEX_STEP_STATUS_PENDING"
            and step.get("type") != "CORTEX_STEP_TYPE_USER_INPUT"
        ):
            continue
        if not _step_allows_completed_turn(step, final=final):
            return False
        if (
            require_execution_id is not None
            and _subdict(step, "metadata").get("executionId") != require_execution_id
        ):
            return False
    return True


def _index_step_batch(
    steps: JsonSeq,
    requested_start: int,
    total: int,
) -> tuple[int, list[IndexedStep], int]:
    """Decode absolute step indices for one LS trajectory step page."""
    if requested_start < 0 or total < requested_start:
        raise ValueError("Antigravity trajectory coverage is incomplete")
    bases: set[int] = set()
    validated: list[JsonDict] = []
    for offset, step in enumerate(steps):
        if not _is_dict(step):
            raise ValueError("Antigravity returned an invalid trajectory step")
        metadata = step.get("metadata")
        if metadata is not None and not _is_dict(metadata):
            raise ValueError("Antigravity returned invalid trajectory step metadata")
        source = metadata.get("sourceTrajectoryStepInfo") if metadata else None
        if source is not None and not _is_dict(source):
            raise ValueError("Antigravity returned invalid trajectory step source metadata")
        index = source.get("stepIndex") if source else None
        if index is not None:
            if not _is_strict_int(index) or index < 0:
                raise ValueError("Antigravity returned an invalid trajectory index")
            bases.add(index - offset)
        validated.append(step)
    if not validated and requested_start < total:
        raise ValueError("Antigravity trajectory coverage is incomplete")
    if len(bases) == 1:
        base = bases.pop()
    elif not bases and len(validated) == total - requested_start:
        base = requested_start  # Older servers honor the requested range without metadata.
    elif not bases and len(validated) == total:
        base = 0
    else:
        raise ValueError("Antigravity trajectory coverage is unknown")
    if base < 0:
        raise ValueError("Antigravity trajectory coverage is incomplete")
    indexed = [(base + offset, step) for offset, step in enumerate(validated)]
    return base, indexed, base + len(validated)


def _observed_turn_steps(
    steps: JsonSeq,
    start: int,
    total: int,
) -> tuple[list[IndexedStep], int]:
    """Read absolute native indices; some LS versions ignore range arguments."""
    _, indexed, covered = _index_step_batch(steps, start, total)
    current = [(index, step) for index, step in indexed if start <= index < total]
    if [index for index, _ in current] != list(range(start, total)):
        raise ValueError("Antigravity trajectory coverage is incomplete")
    return current, covered


def _read_trajectory_steps(
    fetch_page: Callable[[int, int], object],
    *,
    start: int,
    total: int,
) -> tuple[list[IndexedStep], int] | None:
    """Fetch complete trajectory coverage across stepOffset-bounded LS pages."""
    if start < 0 or total < start:
        raise ValueError("Antigravity trajectory coverage is incomplete")
    if start == total:
        raw = fetch_page(start, total)
        if raw is None:
            return None
        if not isinstance(raw, list):
            raise ValueError("Antigravity returned no trajectory coverage")
        return _observed_turn_steps(raw, start, total)
    cursor = start
    collected: list[IndexedStep] = []
    max_covered = total
    while cursor < total:
        raw = fetch_page(cursor, total)
        if raw is None:
            return None
        if not isinstance(raw, list):
            raise ValueError("Antigravity returned no trajectory coverage")
        base, indexed, batch_end = _index_step_batch(raw, cursor, total)
        if base > cursor or batch_end <= cursor:
            raise ValueError("Antigravity trajectory coverage is incomplete")
        collected.extend((index, step) for index, step in indexed if cursor <= index < total)
        max_covered = max(max_covered, batch_end)
        cursor = min(batch_end, total)
    if [index for index, _ in collected] != list(range(start, total)):
        raise ValueError("Antigravity trajectory coverage is incomplete")
    return collected, max_covered


def _native_user_input_text(step: JsonMap) -> str | None:
    """Read the one native text item; any redundant response must agree."""
    content = step.get("userInput")
    if not _is_dict(content):
        return None
    items = content.get("items")
    prompt = (
        items[0].get("text")
        if isinstance(items, list) and len(items) == 1 and _is_dict(items[0])
        else None
    )
    if (
        not isinstance(prompt, str)
        or items != [{"text": prompt}]
        or ("userResponse" in content and content["userResponse"] != prompt)
    ):
        return None
    return prompt


def _running_snapshot_steps(
    snapshot: JsonMap,
    *,
    cascade_id: str,
    start: int,
    total: int,
    sent_prompt: str,
) -> list[IndexedStep] | None:
    """Reuse a complete running snapshot only for the input this adapter sent."""
    trajectory = snapshot.get("trajectory")
    if (
        snapshot.get("status") != "CASCADE_RUN_STATUS_RUNNING"
        or not _is_dict(trajectory)
        or trajectory.get("cascadeId") != cascade_id
    ):
        return None
    raw = trajectory.get("steps")
    if not isinstance(raw, list) or len(raw) != total:
        return None
    try:
        indexed, covered = _observed_turn_steps(raw, start, total)
        if covered != total or not indexed:
            return None
        first = indexed[0][1]
        if (
            first.get("type") != "CORTEX_STEP_TYPE_USER_INPUT"
            or _native_user_input_text(first) != sent_prompt
        ):
            return None
        for _, step in indexed:
            kind = step.get("type")
            if not isinstance(kind, str) or not isinstance(step.get("status"), str):
                return None
            body = _RUNNING_STEP_BODIES.get(kind)
            if body and (not _is_dict(step.get(body)) or not step[body]):
                return None
    except (AttributeError, TypeError, ValueError):
        return None
    return indexed


def _check_positive_timeout(value: float | None, label: str = "hard") -> None:
    if value is not None and (
        type(value) not in (int, float) or not math.isfinite(value) or value <= 0
    ):
        raise ValueError(f"Antigravity {label} timeout must be finite and positive")


def _check_hard_timeout(value: float | None) -> None:
    _check_positive_timeout(value, "hard")


def _turn_progress_snapshot(
    current_steps: Sequence[tuple[int, JsonMap]],
) -> list[tuple[object, ...]]:
    """Project turn progress while bounding repeated error -> thinking-only retry loops."""
    progress: list[tuple[object, ...]] = []
    error_fence_active, settled_thinking_only_retries = False, 0
    for index, step in current_steps:
        step_type, step_status = str(step.get("type") or ""), str(step.get("status") or "")
        if step_type == "CORTEX_STEP_TYPE_ERROR_MESSAGE":
            if step_status != "CORTEX_STEP_STATUS_CLEARED":
                error_fence_active = True
            continue
        planner = step.get("plannerResponse")
        planner_dict = planner if _is_dict(planner) else {}
        response_text = planner_dict.get("modifiedResponse") or planner_dict.get("response")
        if step_type == "CORTEX_STEP_TYPE_PLANNER_RESPONSE":
            if (isinstance(response_text, str) and response_text.strip()) or planner_dict.get("toolCalls"):
                error_fence_active, settled_thinking_only_retries = False, 0
            elif error_fence_active:
                if settled_thinking_only_retries >= 1:
                    continue
                if step_status not in {"CORTEX_STEP_STATUS_GENERATING", "CORTEX_STEP_STATUS_RUNNING"}:
                    settled_thinking_only_retries += 1
        elif step_type not in _NON_TOOL_STEP_TYPES:
            error_fence_active, settled_thinking_only_retries = False, 0
        elif (
            error_fence_active
            and settled_thinking_only_retries >= 1
            and step_type != "CORTEX_STEP_TYPE_USER_INPUT"
        ):
            continue
        run_cmd = step.get("runCommand")
        cmd_dict = run_cmd if _is_dict(run_cmd) else {}
        progress.append((
            index,
            step.get("status"),
            response_text,
            planner_dict.get("thinking"),
            cmd_dict.get("combinedOutput"),
            cmd_dict.get("exitCode"),
        ))
    return progress


def _native_completion_facts(cascade_id: str, current: Sequence[IndexedStep]) -> JsonDict:
    """One private completion projection, without tool outputs or reasoning."""
    facts: list[JsonDict] = []
    for index, step in current:
        metadata = _subdict(step, "metadata")
        fact: JsonDict = {
            "type": step.get("type"),
            "status": step.get("status"),
            "metadata": {
                "createdAt": metadata.get("createdAt"),
                "executionId": metadata.get("executionId"),
                "sourceTrajectoryStepInfo": {"stepIndex": index},
            },
        }
        if step.get("type") == "CORTEX_STEP_TYPE_USER_INPUT":
            fact["userInput"] = step.get("userInput")
        if step.get("type") == "CORTEX_STEP_TYPE_ERROR_MESSAGE":
            fact["errorMessage"] = {
                "shouldShowUser": _subdict(step, "errorMessage").get("shouldShowUser"),
            }
        if index == current[-1][0]:
            planner = _subdict(step, "plannerResponse")
            fact["plannerResponse"] = {
                key: planner[key]
                for key in ("response", "modifiedResponse", "stopReason", "toolCalls")
                if key in planner
            }
        facts.append(fact)
    return {
        "thread_id": cascade_id,
        "start_step_index": current[0][0],
        "total_steps": current[-1][0] + 1,
        "steps": facts,
    }


def _parse_native_final_response(
    raw: str,
    *,
    work_dir: Path,
    team_plan_context: JsonMap | None = None,
    parse_response_fn: Callable[..., JsonDict] = parse_agent_response,
) -> JsonDict:
    """Parse only after the caller verifies this input's normal native final."""
    parsed = parse_response_fn(raw, protected_paths=[work_dir], team_plan_context=team_plan_context)
    if not parsed.get("message") and not any(
        parsed.get(key)
        for key in (
            "proposals",
            "context_handoff",
            "protected_action",
            "gate",
            "goal_draft",
            "unfinished_work",
        )
    ):
        # Unlike a generic prefix, this is the verified final planner's prose.
        # Salvage text only: missing message never opts into compact metadata.
        openings, _, _ = _review_openings(raw)
        visible = raw[: openings[0]] if openings else ""
        if visible.strip():
            return parse_response_fn(
                visible,
                protected_paths=[work_dir],
                team_plan_context=team_plan_context,
            )
    return parsed


def _idle_total(snapshot: JsonMap, cascade_id: str) -> int | None:
    total = snapshot.get("numTotalSteps")
    if (
        snapshot.get("status") == "CASCADE_RUN_STATUS_IDLE"
        and _is_strict_int(total)
        and _subdict(snapshot, "trajectory").get("cascadeId") == cascade_id
    ):
        return total
    return None


def _open_idle_cascade(
    cascade_id: str,
    *,
    timeout: float,
    discover_fn: DiscoverFn = discover_antigravity_language_server,
    rpc_post_fn: RpcPostFn = _rpc_post,
) -> tuple[str, str, CascadeReader, AntigravityRpcResponseDict, int] | None:
    found = discover_fn()
    if found is None:
        return None
    url, token = str(found["url"]), str(found["csrf_token"])

    def read(method: str, **payload: object) -> AntigravityRpcResponseDict:
        return rpc_post_fn(
            url,
            token,
            method,
            {"cascadeId": cascade_id, **payload},
            timeout=timeout,
        )

    before = read("GetCascadeTrajectory")
    total = _idle_total(before, cascade_id)
    return (url, token, read, before, total) if total is not None else None


def _read_cascade_steps(
    read: CascadeReader,
    *,
    start: int,
    total: int,
) -> tuple[list[IndexedStep], int] | None:
    return _read_trajectory_steps(
        lambda offset, end: read(
            "GetCascadeTrajectorySteps",
            stepOffset=offset,
            startIndex=offset,
            endIndex=end,
        ).get("steps"),
        start=start,
        total=total,
    )


def _verify_anchored_user_step(
    user: JsonMap,
    *,
    cascade_id: str,
    start: int,
    message: str,
    request_ref: str,
    started_at: str,
    completed_at: str,
    input_anchor: JsonMap | None = None,
    end_created_at: object = None,
    manager_input_matches_fn: ManagerInputMatcher = manager_input_matches,
) -> tuple[str, str, str] | None:
    if user.get("type") != "CORTEX_STEP_TYPE_USER_INPUT":
        return None
    meta = _opt_dict(user["metadata"])
    execution, created_at, prompt = meta.get("executionId"), meta["createdAt"], _native_user_input_text(user)
    if (
        not isinstance(execution, str)
        or not execution
        or not isinstance(created_at, str)
        or prompt is None
        or not manager_input_matches_fn(prompt, message=message, request_ref=request_ref)
    ):
        return None
    created = datetime.fromisoformat(created_at)
    started, completed = datetime.fromisoformat(started_at), datetime.fromisoformat(completed_at)
    if end_created_at is not None:
        if (
            not isinstance(end_created_at, str)
            or not started <= created <= datetime.fromisoformat(end_created_at) <= completed
        ):
            return None
    elif not started <= created <= completed:
        return None
    digest = hashlib.sha256(prompt.encode()).hexdigest()
    if input_anchor is not None and (
        input_anchor.get("thread_id") != cascade_id
        or input_anchor.get("start_step_index") != start
        or input_anchor.get("input_sha256") != digest
    ):
        return None
    return execution, created_at, digest


def _stream_planner_step(
    *,
    step_idx: int,
    step_status: str,
    text: str,
    event_sink: EventSink | None,
    stream_filter: VisibleResponseStreamFilter,
    emitted_text_by_step: dict[int, str],
) -> bool:
    prev_text = emitted_text_by_step.get(step_idx, "")
    delta_chunk = text[len(prev_text) :] if text.startswith(prev_text) else text
    emitted = False
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
                return False
            prior_texts = [
                val for idx, val in sorted(emitted_text_by_step.items()) if idx != step_idx and val
            ]
            if prior_texts and not delta_chunk.startswith("\n") and not continues_control_tag:
                last_prior = prior_texts[-1]
                delta_chunk = (
                    "\n\n"
                    if not last_prior.endswith("\n")
                    else "\n"
                    if not last_prior.endswith("\n\n")
                    else ""
                ) + delta_chunk
        visible_delta = stream_filter.feed(delta_chunk)
        if visible_delta:
            emitted = True
            event_sink("answer.delta", {"text": visible_delta})
    emitted_text_by_step[step_idx] = text
    return emitted


def _validate_review_envelope(latest_response_text: str) -> None:
    allowed = {
        "schema_version",
        "message",
        "context_handoff",
        "proposals",
        "gate",
        "goal_draft",
        "protected_action",
        "unfinished_work",
    }
    try:
        start = latest_response_text.rfind(CHAT_REVIEW_OPEN_TAG)
        end = latest_response_text.rfind(CHAT_REVIEW_CLOSE_TAG)
        if start < 0 or end <= start:
            raise ValueError("missing format review envelope")
        payload: object = json.loads(latest_response_text[start + len(CHAT_REVIEW_OPEN_TAG) : end])
        if (
            not _is_dict(payload)
            or set(payload) - allowed
            or payload.get("proposals") not in (None, [])
            or any(
                payload.get(key) is not None
                for key in ("gate", "goal_draft", "protected_action", "unfinished_work")
            )
        ):
            raise ValueError("format review contains other or unresolved actions")
    except (TypeError, ValueError) as exc:
        raise CodexChatAgentError(
            "The format review is incomplete or contains other actions; no actions were admitted.",
            gate=None,
            error_code="host_gate",
        ) from exc


def _process_turn_steps(
    current_steps: list[IndexedStep],
    *,
    runtime_profile: str,
    read_only_review: bool,
    event_sink: EventSink | None,
    activity: AntigravityActivity,
    stream_filter: VisibleResponseStreamFilter,
    emitted_text_by_step: dict[int, str],
    observed_native_errors: set[int],
    on_retire_active_turn: Callable[[], None],
) -> tuple[str, bool]:
    """Validate observed steps and emit bounded activity/delta events."""
    if sum(step.get("type") == "CORTEX_STEP_TYPE_USER_INPUT" for _, step in current_steps) > 1:
        on_retire_active_turn()
        raise CodexChatAgentError(
            "The native Antigravity input changed; no result was adopted or invocation cancelled.",
            gate=None,
            error_code="upstream_transport_failure",
        )
    latest_response_text = ""
    emitted_visible_delta = False
    for step_idx, step in current_steps:
        step_type, step_status = str(step.get("type") or ""), str(step.get("status") or "")
        if read_only_review and step_type not in {
            "CORTEX_STEP_TYPE_USER_INPUT",
            "CORTEX_STEP_TYPE_PLANNER_RESPONSE",
            "CORTEX_STEP_TYPE_CHECKPOINT",
        }:
            raise CodexChatAgentError(
                "The format review produced a native tool step; no actions were admitted.",
                gate=None,
                error_code="unsupported_configuration",
            )
        if step_status == "CORTEX_STEP_STATUS_WAITING":
            raise CodexChatAgentError(
                "Antigravity requested native permission; this invocation was cancelled. "
                "Review its permissions, then retry.",
                gate={
                    "kind": "host_tool_gate",
                    "summary": "Antigravity requires native permission.",
                    "next_action": "Review permissions in Antigravity, then resend this request in LoopX.",
                },
                error_code="host_tool_gate",
            )
        pr = step.get("plannerResponse") or {}
        if (
            step_type == "CORTEX_STEP_TYPE_PLANNER_RESPONSE"
            and _is_dict(pr)
            and pr.get("toolCalls")
            and (runtime_profile == "restricted" or read_only_review)
        ):
            raise CodexChatAgentError(
                "Antigravity did not honor text-only Chat.",
                gate=None,
                error_code="unsupported_configuration",
            )
        if event_sink is not None:
            raw_phases: object = activity.observe(step_idx, step)
            if isinstance(raw_phases, list):
                for phase in raw_phases:
                    if _is_dict(phase):
                        event_sink("agent.phase", phase)
        if step_type == "CORTEX_STEP_TYPE_PLANNER_RESPONSE":
            if not _is_dict(pr):
                continue
            text = str(pr.get("modifiedResponse") or pr.get("response") or "")
            if text:
                latest_response_text = text
                if _stream_planner_step(
                    step_idx=step_idx,
                    step_status=step_status,
                    text=text,
                    event_sink=event_sink,
                    stream_filter=stream_filter,
                    emitted_text_by_step=emitted_text_by_step,
                ):
                    emitted_visible_delta = True
        elif step_type == "CORTEX_STEP_TYPE_ERROR_MESSAGE":
            if (
                step_status != "CORTEX_STEP_STATUS_CLEARED"
                and step_idx not in observed_native_errors
            ):
                observed_native_errors.add(step_idx)
                if event_sink is not None:
                    event_sink(
                        "agent.phase",
                        {"method": "native/error", "label": "原生助手记录了调用错误"},
                    )
    return latest_response_text, emitted_visible_delta


def _verify_idle_terminal(
    current_steps: list[IndexedStep],
    *,
    full_prompt: str,
    read_only_review: bool,
    idle_expired: bool,
    timeout_fn: Callable[[str], None],
    on_retire_active_turn: Callable[[], None],
) -> bool:
    """Evaluate terminal idle status, tolerating cleared retries until answer settles."""
    final = current_steps[-1][1] if current_steps else {}
    final_is_error = final.get("type") == "CORTEX_STEP_TYPE_ERROR_MESSAGE"
    error_message = _subdict(final, "errorMessage")
    cleared_retry = (
        final_is_error
        and final.get("status") == "CORTEX_STEP_STATUS_CLEARED"
        and error_message.get("shouldShowUser") is not True
    )
    if final_is_error and not cleared_retry:
        detail: object = error_message.get("error") or {}
        summary = (
            detail.get("userErrorMessage") or detail.get("shortError")
            if _is_dict(detail)
            else str(detail)
        )
        error = CodexChatAgentError(
            str(summary or "Antigravity cascade step error"),
            gate=None,
            error_code="antigravity_step_error",
        )
        error.native_error_info = detail if _is_dict(detail) else str(detail)
        if (
            final.get("status") == "CORTEX_STEP_STATUS_DONE"
            and all(step.get("status") in _SETTLED_STEP_STATUSES for _, step in current_steps)
        ):
            on_retire_active_turn()
        raise error
    planner = _subdict(final, "plannerResponse")
    if (
        final.get("status") == "CORTEX_STEP_STATUS_CANCELED"
        and all(
            step.get("status") in {"CORTEX_STEP_STATUS_DONE", "CORTEX_STEP_STATUS_CANCELED"}
            for _, step in current_steps
        )
    ):
        on_retire_active_turn()
        raise CodexChatAgentError(
            "Antigravity ended with a cancelled step; execution may have occurred.",
            gate=None,
            error_code="upstream_transport_failure",
        )
    if cleared_retry or (
        planner.get("stopReason") in {None, "STOP_REASON_STOP_PATTERN"}
        and (
            final.get("type") != "CORTEX_STEP_TYPE_PLANNER_RESPONSE"
            or planner.get("toolCalls")
            or (planner.get("stopReason") is None and not read_only_review)
            or any(
                step.get("status")
                in {"CORTEX_STEP_STATUS_GENERATING", "CORTEX_STEP_STATUS_RUNNING"}
                for _, step in current_steps
            )
        )
    ):
        if idle_expired:
            timeout_fn("idle")
        return False
    if read_only_review:
        inputs = [
            step for _, step in current_steps if step.get("type") == "CORTEX_STEP_TYPE_USER_INPUT"
        ]
        execution = _subdict(inputs[0], "metadata").get("executionId") if len(inputs) == 1 else None
        if (
            not execution
            or _native_user_input_text(inputs[0]) != full_prompt
            or planner.get("stopReason") != "STOP_REASON_STOP_PATTERN"
            or any(
                step.get("status") != "CORTEX_STEP_STATUS_DONE"
                or _subdict(step, "metadata").get("executionId") != execution
                for _, step in current_steps
            )
        ):
            raise CodexChatAgentError(
                "The format review lacks exact normal text-only completion; no actions were admitted.",
                gate=None,
                error_code="upstream_transport_failure",
            )
    if (
        final.get("type") != "CORTEX_STEP_TYPE_PLANNER_RESPONSE"
        or final.get("status") != "CORTEX_STEP_STATUS_DONE"
        or planner.get("toolCalls")
        or not _turn_steps_allow_completion(current_steps, final=final)
        or not str(planner.get("modifiedResponse") or planner.get("response") or "").strip()
        or planner.get("stopReason") != "STOP_REASON_STOP_PATTERN"
    ):
        raise CodexChatAgentError(
            "Antigravity ended without a verified final response; execution may have occurred.",
            gate=None,
            error_code="upstream_transport_failure",
        )
    return True
