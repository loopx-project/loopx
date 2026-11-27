"""Antigravity Language Server adapter for LoopX Native Chat and Steward."""

from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
import time
from typing import Callable, Mapping, Sequence
import uuid

from ...chat import (
    VisibleResponseStreamFilter,
    _turn_prompt,
    parse_agent_response as parse_agent_response,
)
from ...chat_agent import CodexChatAgentError
from .antigravity_activity import AntigravityActivity
from . import antigravity_cascade as _cascade
from . import antigravity_discovery as _discovery
from . import antigravity_observations as _obs
from .manager_input import manager_input_matches as manager_input_matches
from .antigravity_cascade import (
    ANTIGRAVITY_IDLE_TIMEOUT_SEC,
    ANTIGRAVITY_LARGE_CASCADE_POLL_THRESHOLD,
    ANTIGRAVITY_MAX_CASCADE_STEPS,
    ANTIGRAVITY_ROLLOVER_MAX_ITEM_CHARS,
    ANTIGRAVITY_ROLLOVER_MAX_MESSAGES,
    ANTIGRAVITY_ROLLOVER_MAX_TOTAL_CHARS,
    _MISSING_STEPS,
    _check_hard_timeout,
    _check_positive_timeout,
    _max_cascade_steps,
    _native_completion_facts,
    _read_trajectory_steps as _read_trajectory_steps,
    _rollover_history,
    _running_snapshot_steps,
    _turn_progress_snapshot as _turn_progress_snapshot,
    _validate_review_envelope,
)
from .antigravity_contracts import (
    AntigravityCompletedResultDict,
    AntigravityEndpointDict,
    AntigravityInterruptedAnswerDict,
    AntigravityModelOptionDict,
    AntigravityRpcResponseDict,
    IndexedStep,
    JsonDict,
    JsonMap,
)
from .antigravity_discovery import (
    ANTIGRAVITY_DEFAULT_MODEL,
    ANTIGRAVITY_DEFAULT_REASONING_EFFORT,
    ANTIGRAVITY_MODEL_OPTIONS,
    ANTIGRAVITY_MODEL_PLACEHOLDERS,
    EventSink,
    _as_int,
    _cascade_summary_entry,
    _is_dict,
    _local_url as _local_url,
    _rpc_post as _rpc_post,
    _subdict,
    resolve_antigravity_model_placeholder,
)

__all__ = [
    "ANTIGRAVITY_DEFAULT_MODEL",
    "ANTIGRAVITY_DEFAULT_REASONING_EFFORT",
    "ANTIGRAVITY_IDLE_TIMEOUT_SEC",
    "ANTIGRAVITY_LARGE_CASCADE_POLL_THRESHOLD",
    "ANTIGRAVITY_MAX_CASCADE_STEPS",
    "ANTIGRAVITY_MODEL_OPTIONS",
    "ANTIGRAVITY_MODEL_PLACEHOLDERS",
    "ANTIGRAVITY_ROLLOVER_MAX_ITEM_CHARS",
    "ANTIGRAVITY_ROLLOVER_MAX_MESSAGES",
    "ANTIGRAVITY_ROLLOVER_MAX_TOTAL_CHARS",
    "AntigravityActivity",
    "AntigravityAdapter",
    "discover_antigravity_language_server",
    "offered_antigravity_model_options",
    "probe_antigravity_endpoint",
    "probe_cascade_idle",
    "resolve_antigravity_model_placeholder",
]


def _current_uid() -> int | None:
    uid = os.getuid() if hasattr(os, "getuid") else None
    return uid if isinstance(uid, int) else None


def _run_command(cmd: list[str], *, timeout: float) -> str:
    return str(
        subprocess.check_output(
            cmd, text=True, encoding="utf-8", errors="replace",
            stderr=subprocess.DEVNULL, timeout=timeout,
        )
    )


def discover_antigravity_language_server(
    env: Mapping[str, str] | None = None, *,
    deadline: float | None = None, include_model_config: bool = False,
) -> AntigravityEndpointDict | None:
    """Discover a running local endpoint without reusing an availability verdict."""
    return _discovery.discover_antigravity_language_server(
        env=env, deadline=deadline, include_model_config=include_model_config,
        rpc_post_fn=_rpc_post, run_command_fn=_run_command,
        getuid_fn=_current_uid, monotonic_fn=lambda: time.monotonic(),
    )


def probe_antigravity_endpoint(env: Mapping[str, str] | None = None) -> tuple[bool, str, str]:
    """Return `(available, command_display, detail)` for `/api/chat/capabilities`."""
    return _discovery.probe_antigravity_endpoint(
        env=env, discover_fn=discover_antigravity_language_server,
        monotonic_fn=lambda: time.monotonic(),
    )


def probe_cascade_idle(
    url: str, csrf_token: str, cascade_id: str, *, timeout: float = 2.5,
) -> bool:
    """Return whether `cascade_id` is idle using summary polling with trajectory fallback."""
    return _discovery.probe_cascade_idle(
        url, csrf_token, cascade_id, timeout=timeout, rpc_post_fn=_rpc_post,
    )


def _discovered_model_configs(discovered: JsonMap) -> list[JsonDict]:
    return _discovery._discovered_model_configs(discovered, rpc_post_fn=_rpc_post)


def offered_antigravity_model_options(
    *, discovered: JsonMap | None = None,
) -> list[AntigravityModelOptionDict]:
    """Read registered choices from the local account without opening a cascade."""
    return _discovery.offered_antigravity_model_options(
        discovered=discovered, discover_fn=discover_antigravity_language_server, rpc_post_fn=_rpc_post,
    )


def _start_cascade(ls_url: str, csrf_token: str, work_dir: Path) -> str:
    return _cascade._start_cascade(ls_url, csrf_token, work_dir, rpc_post_fn=_rpc_post)


def _parse_native_final_response(
    raw: str, *, work_dir: Path, team_plan_context: JsonMap | None = None,
) -> JsonDict:
    return _cascade._parse_native_final_response(
        raw, work_dir=work_dir, team_plan_context=team_plan_context,
        parse_response_fn=parse_agent_response,
    )


class AntigravityAdapter:
    """Local text Chat and trusted-owner Steward over LanguageServerService.

    Restricted Chat disables native tools. Trusted-owner uses Antigravity's
    existing approvals; LoopX never accepts those approvals on the user's behalf.
    Governed Tasks and workspace grants retain their existing executor owner.
    Interactive invocations have no default idle or wall-clock timeout; explicit
    idle_timeout_sec and hard_timeout_sec bound idle and wall time when set.
    """

    adapter_kind = "antigravity_ls"

    @staticmethod
    def interrupted_answer_observation(
        *, cascade_id: str, request_ref: str, message: str,
        started_at: str, completed_at: str, input_anchor: JsonMap,
    ) -> AntigravityInterruptedAnswerDict | None:
        """Read a stable original segment for a read-only answer review only."""
        return _obs.observe_interrupted_answer(
            cascade_id=cascade_id, request_ref=request_ref, message=message,
            started_at=started_at, completed_at=completed_at, input_anchor=input_anchor,
            discover_fn=discover_antigravity_language_server, rpc_post_fn=_rpc_post,
            manager_input_matches_fn=manager_input_matches,
        )

    @staticmethod
    def quota_failure_observation(
        *, cascade_id: str, request_ref: str, message: str,
        started_at: str, completed_at: str, input_anchor: JsonMap,
    ) -> JsonDict | None:
        """Prove one native input ended at quota before any model/tool output."""
        return _obs.observe_quota_failure(
            cascade_id=cascade_id, request_ref=request_ref, message=message,
            started_at=started_at, completed_at=completed_at, input_anchor=input_anchor,
            discover_fn=discover_antigravity_language_server, rpc_post_fn=_rpc_post,
            now_epoch_fn=lambda: time.time(),
            manager_input_matches_fn=manager_input_matches,
        )

    @staticmethod
    def metadata_review_capacity(*, cascade_id: str, model_id: str) -> JsonDict | None:
        """Observe the existing model's quota; never probe it with generation."""
        return _obs.observe_metadata_review_capacity(
            cascade_id=cascade_id, model_id=model_id,
            discover_fn=discover_antigravity_language_server, rpc_post_fn=_rpc_post,
            now_epoch_fn=lambda: time.time(),
        )

    @staticmethod
    def failed_report_observation(
        *, cascade_id: str, turn_id: str, native_timeout: bool,
        model_id: str | None = None, reasoning_effort: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> JsonDict | None:
        """Read an idle Antigravity cascade and capacity without starting or mutating work."""
        return _obs.observe_failed_report(
            cascade_id=cascade_id, turn_id=turn_id, native_timeout=native_timeout,
            model_id=model_id, reasoning_effort=reasoning_effort, env=env,
            discover_fn=discover_antigravity_language_server, rpc_post_fn=_rpc_post,
            monotonic_fn=lambda: time.monotonic(), now_epoch_fn=lambda: time.time(),
            endpoint_capacity_fn=AntigravityAdapter.endpoint_model_capacity,
        )

    @staticmethod
    def endpoint_model_capacity(
        *, model_id: str, reasoning_effort: str | None = None,
        env: Mapping[str, str] | None = None, discovered: JsonMap | None = None,
    ) -> JsonDict | None:
        """Observe the live LS account's quota for one offered model without opening a cascade."""
        return _obs.observe_endpoint_model_capacity(
            model_id=model_id, reasoning_effort=reasoning_effort, env=env,
            discovered=discovered, discover_fn=discover_antigravity_language_server,
            rpc_post_fn=_rpc_post, now_epoch_fn=lambda: time.time(),
        )

    @staticmethod
    def completed_result_observation(
        *, cascade_id: str, request_ref: str, message: str, started_at: str,
        completed_at: str, work_dir: Path, input_anchor: JsonMap | None = None,
        include_raw_response: bool = False, retained_result: JsonMap | None = None,
        include_retained_result: bool = False,
    ) -> AntigravityCompletedResultDict | None:
        """Read an existing result; never start, send to or cancel a cascade."""
        return _obs.observe_completed_result(
            cascade_id=cascade_id, request_ref=request_ref, message=message,
            started_at=started_at, completed_at=completed_at, work_dir=work_dir,
            input_anchor=input_anchor, include_raw_response=include_raw_response,
            retained_result=retained_result, include_retained_result=include_retained_result,
            discover_fn=discover_antigravity_language_server, rpc_post_fn=_rpc_post,
            parse_response_fn=parse_agent_response,
            manager_input_matches_fn=manager_input_matches,
        )

    def __init__(
        self, *, work_dir: Path, objective: str, cascade_id: str, ls_url: str,
        csrf_token: str, model_id: str, model_placeholder: str,
        reasoning_effort: str | None = None, runtime_profile: str = "restricted",
        team_plan_context: JsonDict | None = None,
        initial_history: list[dict[str, str]] | None = None,
        resumed: bool = False, idle_timeout_sec: float | None = None,
        hard_timeout_sec: float | None = None, max_cascade_steps: int | None = None,
    ) -> None:
        self.work_dir = work_dir.resolve()
        _check_positive_timeout(idle_timeout_sec, "idle")
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
        self._recent_history: list[dict[str, str]] = (
            _rollover_history(initial_history) if initial_history else []
        )
        self._max_cascade_steps = (
            _max_cascade_steps() if max_cascade_steps is None else max_cascade_steps
        )
        self._system_primed = False
        self._closed = False
        self.idle_timeout_sec = idle_timeout_sec
        self.hard_timeout_sec = hard_timeout_sec
        self._interrupted = threading.Event()
        self._send_lock = threading.Lock()
        self._active_turn_id: str | None = None
        self._active_cascade_id: str | None = None
        self._cancel_acknowledged = False
        self._launch_guard: Callable[[], AbstractContextManager[None]] | None = None
        self._steward_report_reader: Callable[[str, object], JsonDict] | None = None

    @property
    def current_model_id(self) -> str:
        return self.model_id

    @property
    def model_settings(self) -> dict[str, str | None]:
        return {"agent_reasoning_effort": self.reasoning_effort, "agent_service_tier": None}

    def _post(self, method: str, payload: JsonMap, *, timeout: float) -> AntigravityRpcResponseDict:
        return _rpc_post(self.ls_url, self.csrf_token, method, payload, timeout=timeout)

    def _retire_active_turn(self) -> None:
        self._active_turn_id = None

    @classmethod
    def start(
        cls, *, work_dir: Path, objective: str, resume_thread_id: str | None = None,
        history: list[dict[str, str]] | None = None, runtime_profile: str = "restricted",
        team_plan_context: JsonDict | None = None, executor_model: JsonMap | None = None,
        env: Mapping[str, str] | None = None, idle_timeout_sec: float | None = None,
        hard_timeout_sec: float | None = None,
    ) -> "AntigravityAdapter":
        if runtime_profile not in {"restricted", "trusted_owner"}:
            raise ValueError("unsupported Antigravity Chat runtime profile")
        _check_positive_timeout(idle_timeout_sec, "idle")
        _check_hard_timeout(hard_timeout_sec)
        requested = dict(executor_model or {})
        req_model = str(requested.get("model") or "").strip() or None
        req_effort = str(requested.get("reasoning_effort") or "").strip() or None
        resolve_antigravity_model_placeholder(req_model, req_effort)
        discovered = discover_antigravity_language_server(env=env, include_model_config=True)
        if discovered is None:
            raise CodexChatAgentError(
                "Antigravity local language_server is not running or reachable",
                gate=None, error_code="antigravity_unavailable",
            )
        ls_url, csrf_token = str(discovered["url"]), str(discovered["csrf_token"])
        model_id, model_placeholder = resolve_antigravity_model_placeholder(
            req_model, req_effort, dynamic_configs=_discovered_model_configs(discovered),
        )

        cascade_id = str(resume_thread_id or "").strip()
        resumed = False
        rotated = False
        max_steps = _max_cascade_steps(env)
        if cascade_id:
            rotate_now = False
            try:
                traj = _rpc_post(
                    ls_url, csrf_token, "GetCascadeTrajectory",
                    {"cascadeId": cascade_id}, timeout=10.0,
                )
                if _is_dict(traj) and traj.get("trajectory"):
                    resumed = True
                    raw_steps = _subdict(traj, "trajectory").get("steps")
                    fallback_len = len(raw_steps) if isinstance(raw_steps, list) else 0
                    total_steps = _as_int(traj.get("numTotalSteps")) or fallback_len
                    rotate_now = (
                        max_steps > 0 and total_steps >= max_steps
                        and traj.get("status") != "CASCADE_RUN_STATUS_RUNNING"
                        and history is not None
                    )
            except CodexChatAgentError as exc:
                resumed = False
                if (
                    exc.error_code == "antigravity_transport_error"
                    and max_steps > 0 and history is not None
                ):
                    try:
                        entry = _cascade_summary_entry(
                            _rpc_post(ls_url, csrf_token, "GetAllCascadeTrajectories", {}, timeout=5.0),
                            cascade_id,
                        )
                    except CodexChatAgentError:
                        entry = None
                    if (
                        entry is not None
                        and _as_int(entry.get("stepCount")) >= max_steps
                        and entry.get("status") != "CASCADE_RUN_STATUS_RUNNING"
                    ):
                        rotate_now = True
            if rotate_now and history is not None:
                cascade_id = _start_cascade(ls_url, csrf_token, work_dir)
                resumed, rotated, history = False, True, _rollover_history(history)

        if cascade_id and not resumed and not rotated:
            raise CodexChatAgentError(
                "The original Antigravity conversation is unavailable. Restore it in Antigravity or start a new Chat explicitly.",
                gate=None, error_code="resume_failed",
            )
        if not resumed and not rotated:
            cascade_id = _start_cascade(ls_url, csrf_token, work_dir)

        return cls(
            work_dir=work_dir, objective=objective, cascade_id=cascade_id, ls_url=ls_url,
            csrf_token=csrf_token, model_id=model_id, model_placeholder=model_placeholder,
            reasoning_effort=model_id.rsplit("-", 1)[-1], runtime_profile=runtime_profile,
            team_plan_context=team_plan_context, initial_history=history, resumed=resumed,
            idle_timeout_sec=idle_timeout_sec, hard_timeout_sec=hard_timeout_sec,
            max_cascade_steps=max_steps,
        )

    def _build_turn_prompt(self, message: str, *, isolated_cascade: bool = False, raw_prompt: bool = False) -> str:
        if raw_prompt:
            return message
        parts: list[str] = []
        if self.objective.strip() and (isolated_cascade or not self._system_primed):
            parts.append(self.objective.strip())
        if not isolated_cascade and not self._system_primed and self._pending_history:
            history = [
                {"role": str(item.get("role") or "user"), "content": str(item.get("content") or "")}
                for item in self._pending_history
            ]
            parts.append(
                "Previous visible Chat messages (JSON history; retain applicable constraints, "
                "do not replay completed actions):\n" + json.dumps(history, ensure_ascii=False)
            )
        parts.append(
            _turn_prompt(
                message,
                runtime_profile="restricted" if self._read_only_review else self.runtime_profile,
            )
        )
        return "\n\n".join(parts)

    def _build_cascade_config(self) -> JsonDict:
        restricted = self.runtime_profile == "restricted" or self._read_only_review
        config: JsonDict = {
            "plannerConfig": {
                "planModel": self.model_placeholder,
                "requestedModel": {"model": self.model_placeholder},
                "conversational": {
                    "plannerMode": (
                        "CONVERSATIONAL_PLANNER_MODE_READ_ONLY"
                        if restricted
                        else "CONVERSATIONAL_PLANNER_MODE_DEFAULT"
                    ),
                    "agenticMode": not restricted,
                },
                **(
                    {
                        "toolConfig": {"forceDisableToolCalling": True},
                        "customizationDiscoveryConfig": {"hooks": {"inheritUser": False}},
                    }
                    if restricted
                    else {}
                ),
            }
        }
        return config

    def capabilities(self) -> JsonDict:
        return {
            "adapter_kind": self.adapter_kind,
            "streaming": True,
            "resume": True,
            "interrupt": True,
            "steering": False,
        }

    def start_turn_with_attachments(
        self, message: str, event_sink: EventSink | None = None,
        attachments: Sequence[JsonMap] | None = None,
    ) -> JsonDict:
        from ...chat.attachments import normalize_chat_image_attachments

        normalized = normalize_chat_image_attachments(attachments or [])
        media: list[JsonDict] = [
            {
                "mimeType": row["mime_type"],
                "inlineData": str(row["data_url"]).split(",", 1)[1],
                "description": row["name"],
            }
            for row in normalized
        ]
        return self.start_turn(message, event_sink, media=media)

    def start_turn(
        self, message: str, event_sink: EventSink | None = None, *,
        read_only_review: bool = False, media: list[JsonDict] | None = None,
        isolated_cascade: bool = False, raw_prompt: bool = False,
        idle_timeout_sec: float | None = None,
    ) -> JsonDict:
        _check_positive_timeout(idle_timeout_sec, "idle")
        if raw_prompt and read_only_review:
            raise ValueError("raw_prompt cannot be combined with read_only_review")
        self._read_only_review = read_only_review
        self._interrupted.clear()
        self._cancel_acknowledged = False
        try:
            return self._run_turn(
                message, event_sink, media=media,
                isolated_cascade=isolated_cascade, raw_prompt=raw_prompt,
                idle_timeout_sec=idle_timeout_sec,
            )
        except BaseException as failure:
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
            self._active_cascade_id = None
            self._read_only_review = False

    def _run_turn(
        self, message: str, event_sink: EventSink | None = None, *,
        media: list[JsonDict] | None = None, isolated_cascade: bool = False,
        raw_prompt: bool = False, idle_timeout_sec: float | None = None,
    ) -> JsonDict:
        if self._closed:
            raise CodexChatAgentError(
                "Antigravity session is closed", gate=None, error_code="session_closed",
            )

        turn_id = f"agy_turn_{uuid.uuid4().hex[:12]}"
        if isolated_cascade:
            target_cascade_id = _start_cascade(self.ls_url, self.csrf_token, self.work_dir)
            start_step_index = 0
        else:
            target_cascade_id = self.cascade_id
            before_traj = self._post(
                "GetCascadeTrajectory", {"cascadeId": target_cascade_id}, timeout=10.0,
            )
            if before_traj.get("status") != "CASCADE_RUN_STATUS_IDLE":
                if event_sink is not None:
                    event_sink("agent.phase", {
                        "phase": "native_conversation_active",
                        "method": "native/cascade_active",
                        "cascade_status": str(before_traj.get("status") or ""),
                    })
                raise CodexChatAgentError(
                    "The original Antigravity conversation is still active. Stop it in Antigravity before retrying.",
                    gate=None, error_code="resume_failed",
                )
            start_step_index = _as_int(before_traj.get("numTotalSteps"))
            if (
                not self._read_only_review
                and self._max_cascade_steps > 0
                and start_step_index >= self._max_cascade_steps
            ):
                target_cascade_id = _start_cascade(self.ls_url, self.csrf_token, self.work_dir)
                self.cascade_id = target_cascade_id
                self.upstream_thread_id = target_cascade_id
                start_step_index = 0
                self._system_primed = False
                self._pending_history = list(self._recent_history)
        full_prompt = self._build_turn_prompt(
            message, isolated_cascade=isolated_cascade, raw_prompt=raw_prompt,
        )
        send_payload: JsonDict = {
            "cascadeId": target_cascade_id,
            "items": [{"text": full_prompt}],
            "cascadeConfig": self._build_cascade_config(),
            **({"media": media} if media else {}),
        }
        with self._send_lock:
            with self._launch_guard() if self._launch_guard is not None else nullcontext():
                self._check_interrupted()
                self._active_turn_id = turn_id
                self._active_cascade_id = target_cascade_id
                if event_sink is not None:
                    event_sink("turn.started", {
                        "upstream_turn_id": turn_id, "threadId": target_cascade_id,
                        "native_input": {
                            "thread_id": target_cascade_id,
                            "start_step_index": start_step_index,
                            "input_sha256": hashlib.sha256(full_prompt.encode()).hexdigest(),
                        },
                    })
                self._post("SendUserCascadeMessage", send_payload, timeout=15.0)
                if not isolated_cascade:
                    self._system_primed = True
                    self._pending_history.clear()

        stream_filter = VisibleResponseStreamFilter()
        emitted_visible_delta = False
        emitted_text_by_step: dict[int, str] = {}
        activity = AntigravityActivity(self.work_dir)
        latest_response_text = ""
        effective_idle_timeout = (
            self.idle_timeout_sec if idle_timeout_sec is None else idle_timeout_sec
        )
        last_progress_at = time.monotonic()
        idle_status_since: float | None = None
        transport_error_since: float | None = None
        idle_grace = (
            effective_idle_timeout
            if effective_idle_timeout is not None
            else ANTIGRAVITY_IDLE_TIMEOUT_SEC
        )
        summary_probe_unsupported = False
        last_summary_key: tuple[object, ...] | None = None
        last_steps_poll_at = last_progress_at
        poll_count = 0
        deadline = (
            last_progress_at + self.hard_timeout_sec
            if self.hard_timeout_sec is not None
            else None
        )
        previous_progress: list[tuple[object, ...]] | None = None
        saw_running_or_new_step = False
        observed_native_errors: set[int] = set()
        incomplete_running_polls = 0
        observation_limit = 10.0

        def timeout(kind: str) -> None:
            limit = (
                self.hard_timeout_sec
                if kind == "hard"
                else (effective_idle_timeout if effective_idle_timeout is not None else idle_grace)
            )
            detail: JsonDict = {
                "kind": kind,
                "limit_seconds": limit,
                "native_error_count": len(observed_native_errors),
            }
            if event_sink is not None:
                event_sink("agent.phase", {
                    "method": "native/timeout", "timeout": detail,
                    "label": "原生助手达到执行时限" if kind == "hard" else "等待原生助手进展超时",
                })
            error = CodexChatAgentError(
                f"Antigravity did not finish before the turn deadline ({kind} timeout).",
                gate=None, error_code="response_timeout",
            )
            error.native_error_info = {"timeout": detail}
            raise error

        def observe(
            method: str, args: JsonMap, final_read_deadline: float | None,
            *, include_cascade_id: bool = True,
        ) -> JsonMap | None:
            nonlocal transport_error_since
            remaining = observation_limit
            if final_read_deadline is not None:
                now = time.monotonic()
                if deadline is not None and now >= deadline:
                    timeout("hard")
                remaining = final_read_deadline - now
                if remaining <= 0:
                    timeout("idle")
            payload: JsonDict = (
                {"cascadeId": target_cascade_id, **args} if include_cascade_id else dict(args)
            )
            try:
                result = self._post(method, payload, timeout=remaining)
                transport_error_since = None
                return result
            except CodexChatAgentError as exc:
                if exc.error_code != "antigravity_transport_error":
                    raise
                if final_read_deadline is not None:
                    self._check_interrupted()
                    timeout("hard" if deadline is not None and time.monotonic() >= deadline else "idle")
                err_now = time.monotonic()
                if transport_error_since is None:
                    transport_error_since = err_now
                elif err_now - transport_error_since >= (
                    effective_idle_timeout if effective_idle_timeout is not None else 60.0
                ):
                    raise
                self._interrupted.wait(1.0)
                return None

        while True:
            now = time.monotonic()
            if deadline is not None and now >= deadline:
                timeout("hard")
            self._check_interrupted()
            final_read_deadline = None
            idle_due = (
                effective_idle_timeout is not None and now - last_progress_at >= effective_idle_timeout
            ) or (idle_status_since is not None and now - idle_status_since >= idle_grace)
            if idle_due:
                final_read_deadline = (
                    min(now + observation_limit, deadline)
                    if deadline is not None
                    else now + observation_limit
                )
            else:
                poll_count += 1
                wait_sec = 0.25 if poll_count <= 8 else (0.5 if poll_count <= 24 else 1.0)
                self._interrupted.wait(wait_sec)
                self._check_interrupted()

            traj: JsonMap | None = None
            if (
                start_step_index >= ANTIGRAVITY_LARGE_CASCADE_POLL_THRESHOLD
                and final_read_deadline is None
                and not summary_probe_unsupported
            ):
                try:
                    summaries_resp = observe(
                        "GetAllCascadeTrajectories", {}, None, include_cascade_id=False,
                    )
                except CodexChatAgentError as exc:
                    if exc.error_code != "antigravity_rpc_error":
                        raise
                    summary_probe_unsupported, summaries_resp = True, None
                if summaries_resp is None and not summary_probe_unsupported:
                    continue
                summary = _cascade_summary_entry(summaries_resp, target_cascade_id)
                if summary is not None:
                    s_status = str(summary.get("status") or "")
                    s_total = _as_int(summary.get("stepCount") or summary.get("numTotalSteps"))
                    mod_key = (s_status, s_total, summary.get("lastModifiedTime"))
                    now = time.monotonic()
                    if s_status == "CASCADE_RUN_STATUS_RUNNING":
                        idle_status_since = None
                        if (
                            last_summary_key == mod_key
                            and saw_running_or_new_step
                            and now - last_steps_poll_at < 5.0
                        ):
                            if deadline is not None and now >= deadline:
                                timeout("hard")
                            continue
                        last_summary_key, last_steps_poll_at = mod_key, now
                        traj = {"status": s_status, "numTotalSteps": s_total}
                    else:
                        last_summary_key = mod_key
            if traj is None:
                traj = observe("GetCascadeTrajectory", {}, final_read_deadline)
                if traj is None:
                    continue
            status = str(traj.get("status") or "")
            total_steps = _as_int(traj.get("numTotalSteps"))
            if status == "CASCADE_RUN_STATUS_RUNNING" or total_steps > start_step_index:
                saw_running_or_new_step = True

            current_steps: list[IndexedStep] = []
            observed_total = total_steps
            if total_steps > start_step_index:
                self._check_interrupted()
                snapshot_steps = (
                    _running_snapshot_steps(
                        traj, cascade_id=target_cascade_id, start=start_step_index,
                        total=total_steps, sent_prompt=full_prompt,
                    )
                    if final_read_deadline is None else None
                )
                if snapshot_steps is not None:
                    current_steps = snapshot_steps
                else:
                    def _observe_steps_page(offset: int, end: int) -> object:
                        steps_resp = observe(
                            "GetCascadeTrajectorySteps",
                            {"stepOffset": offset, "startIndex": offset, "endIndex": end},
                            final_read_deadline,
                        )
                        return None if steps_resp is None else steps_resp.get("steps", _MISSING_STEPS)

                    try:
                        loaded = _read_trajectory_steps(
                            _observe_steps_page, start=start_step_index, total=total_steps,
                        )
                    except (AttributeError, TypeError, ValueError) as exc:
                        if (
                            str(exc) == "Antigravity trajectory coverage is incomplete"
                            and status == "CASCADE_RUN_STATUS_RUNNING"
                            and final_read_deadline is None
                            and incomplete_running_polls < 3
                        ):
                            incomplete_running_polls += 1
                            continue
                        raise CodexChatAgentError(
                            str(exc), gate=None, error_code="antigravity_rpc_error",
                        ) from exc
                    if loaded is None:
                        continue
                    incomplete_running_polls = 0
                    current_steps, observed_total = loaded
                if current_steps:
                    text_now, step_emitted = _cascade._process_turn_steps(
                        current_steps,
                        runtime_profile=self.runtime_profile,
                        read_only_review=self._read_only_review,
                        event_sink=event_sink,
                        activity=activity,
                        stream_filter=stream_filter,
                        emitted_text_by_step=emitted_text_by_step,
                        observed_native_errors=observed_native_errors,
                        on_retire_active_turn=self._retire_active_turn,
                    )
                    if text_now:
                        latest_response_text = text_now
                    if step_emitted:
                        emitted_visible_delta = True

            progress = _turn_progress_snapshot(current_steps)
            now = time.monotonic()
            if progress and progress != previous_progress:
                previous_progress, last_progress_at = progress, now
                idle_status_since = now if status == "CASCADE_RUN_STATUS_IDLE" else None
            elif status == "CASCADE_RUN_STATUS_IDLE":
                if idle_status_since is None:
                    idle_status_since = now
            else:
                idle_status_since = None
            self._check_interrupted()
            if deadline is not None and now >= deadline:
                timeout("hard")
            idle_expired = final_read_deadline is not None and (
                (effective_idle_timeout is not None and now - last_progress_at >= effective_idle_timeout)
                or (idle_status_since is not None and now - idle_status_since >= idle_grace)
            )
            if (
                saw_running_or_new_step
                and status == "CASCADE_RUN_STATUS_IDLE"
                and observed_total == total_steps
            ):
                if _cascade._verify_idle_terminal(
                    current_steps,
                    full_prompt=full_prompt,
                    read_only_review=self._read_only_review,
                    idle_expired=idle_expired,
                    timeout_fn=timeout,
                    on_retire_active_turn=self._retire_active_turn,
                ):
                    break
            elif idle_expired:
                timeout("idle")

        if event_sink is not None:
            self._check_interrupted()
            event_sink("turn.native_result", {
                **_native_completion_facts(target_cascade_id, current_steps),
                "upstream_turn_id": turn_id,
            })
            tail = stream_filter.finish()
            if tail:
                emitted_visible_delta = True
                event_sink("answer.delta", {"text": tail})

        if not latest_response_text.strip():
            raise CodexChatAgentError(
                "Antigravity cascade finished without a planner response",
                gate=None, error_code="empty_agent_response",
            )
        if self._read_only_review:
            _validate_review_envelope(latest_response_text)
        parsed = _parse_native_final_response(
            latest_response_text, work_dir=self.work_dir, team_plan_context=self.team_plan_context,
        )
        if not isolated_cascade and not self._read_only_review:
            self._recent_history = _rollover_history([
                *self._recent_history,
                {"role": "user", "content": message},
                {"role": "assistant", "content": str(parsed.get("message") or "")},
            ])
        if event_sink is not None:
            if not emitted_visible_delta and str(parsed.get("message") or "").strip():
                event_sink("answer.delta", {"text": str(parsed["message"])})
            event_sink("answer.final", {"response": parsed})
        return parsed

    def _check_interrupted(self) -> None:
        if self._closed or self._interrupted.is_set():
            raise CodexChatAgentError(
                "Antigravity turn was interrupted.", gate=None, error_code="turn_interrupted",
            )

    def interrupt_turn(self, turn_id: str | None = None) -> None:
        active = self._active_turn_id
        if active is not None and turn_id is not None and turn_id != active:
            raise ValueError("the active Antigravity Turn changed")
        self._interrupted.set()
        if active is None:
            return
        with self._send_lock:
            target_cascade_id = self._active_cascade_id or self.cascade_id
            if not self._cancel_acknowledged:
                self._post("CancelCascadeInvocation", {"cascadeId": target_cascade_id}, timeout=5.0)
                deadline = time.monotonic() + 5.0
                while True:
                    trajectory = self._post(
                        "GetCascadeTrajectory", {"cascadeId": target_cascade_id}, timeout=2.0,
                    )
                    if trajectory.get("status") == "CASCADE_RUN_STATUS_IDLE":
                        break
                    if time.monotonic() >= deadline:
                        raise CodexChatAgentError(
                            "Antigravity has not confirmed stop; retry stopping it.",
                            gate=None, error_code="response_timeout",
                        )
                    time.sleep(0.05)
                self._cancel_acknowledged = True

    def close_session(self) -> None:
        self._closed = True
        self.interrupt_turn()

    def healthcheck(self) -> bool:
        if self._closed:
            return False
        try:
            self._post("GetCascadeModelConfigData", {}, timeout=2.0)
            return True
        except CodexChatAgentError:
            return False
