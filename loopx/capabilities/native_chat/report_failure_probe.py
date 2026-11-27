from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
import subprocess
import time
from typing import Protocol, cast

from ...chat_agent import CodexChatAgentError

ProofObject = dict[str, object]
ProofMapping = Mapping[str, object]


class TurnLoader(Protocol):
    def load_turn(self, session_id: str, turn_id: str, /) -> ProofMapping | None: ...


class ReportProofStore(TurnLoader, Protocol):
    def load_session(self, session_id: str, /) -> ProofMapping | None: ...

    def events_after(
        self, session_id: str, turn_id: str, event_id: str | None, /
    ) -> Sequence[ProofMapping]: ...


class ReportProofController(Protocol):
    @property
    def coordination_runtime_root(self) -> Path: ...

    @property
    def codex_bin(self) -> str: ...

    @property
    def codex_home(self) -> Path: ...


def _mapping(value: object) -> ProofMapping:
    return cast(ProofMapping, value) if isinstance(value, Mapping) else {}


def _object_dict(value: ProofMapping) -> ProofObject:
    return cast(ProofObject, value) if isinstance(value, dict) else dict(value)


def _is_antigravity_turn_id(value: object) -> bool:
    return isinstance(value, str) and value.startswith("agy_turn_")


def _is_completed_host_failure_report(original: ProofMapping) -> object | None:
    result = _mapping(_mapping(original.get("response")).get("native_loopx"))
    request = _mapping(original.get("loopx_request"))
    upstream_turn_id = original.get("upstream_turn_id")
    if (
        original.get("status") != "completed"
        or request.get("work_kind") != "report"
        or result.get("result_kind") != "host_failure"
        or not upstream_turn_id
    ):
        return None
    return upstream_turn_id


def valid_report_failure_proof(
    original: ProofMapping,
    threads: Sequence[str],
    proof: ProofMapping | None,
) -> bool:
    """Return True when a live provider observation proves a terminal host failure with no generated answer."""
    upstream_turn_id = original.get("upstream_turn_id")
    if (
        not isinstance(proof, Mapping)
        or proof.get("contradicted") is True
        or proof.get("turn_id") != upstream_turn_id
        or proof.get("thread_id") not in threads
    ):
        return False
    if _is_antigravity_turn_id(upstream_turn_id):
        return (
            proof.get("adapter_kind") == "antigravity_ls"
            and proof.get("cascade_idle") is True
            and proof.get("no_final_response") is True
        )
    return proof.get("only_original_user_item") is True and bool(
        proof.get("codex_error_info")
    )


def proof_targets_original_turn(
    original: ProofMapping,
    threads: Sequence[str],
    proof: ProofMapping | None,
) -> bool:
    """Ensure an observation refers to this exact original thread/turn."""
    if not isinstance(proof, Mapping):
        return False
    thread_id = proof.get("thread_id")
    turn_id = proof.get("turn_id")
    return (
        isinstance(thread_id, str)
        and thread_id in threads
        and turn_id == original.get("upstream_turn_id")
    )


def probe_report_failure(
    controller: ReportProofController,
    store: ReportProofStore,
    original: ProofMapping,
    *,
    include_contradiction: bool = False,
    contradiction_detail: bool = False,
    swallow_probe_errors: bool | None = None,
) -> tuple[list[str], ProofObject | None] | None:
    """Query the original Session's native threads without starting or resuming work."""
    upstream_turn_id = _is_completed_host_failure_report(original)
    if not upstream_turn_id or _is_antigravity_turn_id(upstream_turn_id):
        return None
    from .codex_adapter import CodexAppServerAdapter
    from .codex_context import checked_session_home

    session = store.load_session(cast(str, original["session_id"]))
    if not session or not session.get("codex_home") or not session.get("upstream_thread_id"):
        return None
    history = session.get("upstream_context_history") or []
    if not isinstance(history, list) or any(
        not isinstance(item, dict)
        or not isinstance(item.get("thread_id"), str)
        or not item["thread_id"]
        for item in history
    ):
        return None
    # Context replacement retains native provenance on this same Session.
    # Read the original refusal there; exclude non-Codex cascades.
    raw_threads: list[str] = [
        *(
            [cast(str, session["upstream_thread_id"])]
            if session.get("agent_id") != "antigravity"
            else []
        ),
        *(
            cast(str, item["thread_id"])
            for item in reversed(history)
            if item.get("agent_id") != "antigravity"
        ),
    ]
    threads = list(dict.fromkeys(raw_threads))
    if not threads:
        return None
    swallow = include_contradiction if swallow_probe_errors is None else swallow_probe_errors
    try:
        proof = CodexAppServerAdapter.failed_request_observation(
            codex_bin=controller.codex_bin,
            codex_home=Path(cast(str | Path, session["codex_home"])),
            thread_ids=threads,
            model_auth_home=checked_session_home(controller.codex_home, session),
            turn_id=cast(str, upstream_turn_id),
            include_contradiction=include_contradiction,
            contradiction_detail=contradiction_detail,
        )
    except (CodexChatAgentError, OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        if not swallow:
            raise
        proof = None
    return threads, cast(ProofObject | None, proof)


def _extract_antigravity_turn_cascade(
    store: ReportProofStore,
    original: ProofMapping,
) -> tuple[ProofObject, str, list[ProofObject]] | None:
    """Resolve one Antigravity cascade and stored events when no final answer exists."""
    upstream_turn_id = original.get("upstream_turn_id")
    session = store.load_session(cast(str, original["session_id"]))
    if (
        not session
        or original.get("delta_count") != 0
        or not _is_antigravity_turn_id(upstream_turn_id)
    ):
        return None
    try:
        events = store.events_after(
            cast(str, original["session_id"]), cast(str, original["turn_id"]), None
        )
    except (OSError, ValueError):
        return None
    started_id: object = None
    started_count = 0
    for row in events:
        if row.get("kind") in {"answer.final", "turn.native_result"}:
            return None
        payload = row.get("payload")
        if (
            row.get("kind") == "turn.started"
            and isinstance(payload, Mapping)
            and payload.get("upstream_turn_id") == upstream_turn_id
        ):
            started_count += 1
            started_id = payload.get("threadId")
    if started_count != 1 or not isinstance(started_id, str) or not started_id:
        return None
    return _object_dict(session), started_id, [_object_dict(row) for row in events]


def _antigravity_trajectory_steps(
    url: str,
    csrf_token: str,
    cascade_id: str,
    traj: ProofMapping,
) -> list[ProofObject] | None:
    """Load complete trajectory steps from an idle Antigravity cascade."""
    from .antigravity_adapter import _read_trajectory_steps, _rpc_post

    trajectory = _mapping(traj.get("trajectory"))
    if trajectory.get("cascadeId") != cascade_id:
        return None
    total = traj.get("numTotalSteps")
    inline_steps = trajectory.get("steps")
    if type(total) is int and total > 0:
        if (
            isinstance(inline_steps, list)
            and len(inline_steps) == total
            and all(isinstance(step, dict) for step in inline_steps)
        ):
            return [cast(ProofObject, step) for step in inline_steps]
        try:
            loaded = _read_trajectory_steps(
                lambda offset, end: _rpc_post(
                    url,
                    csrf_token,
                    "GetCascadeTrajectorySteps",
                    {
                        "cascadeId": cascade_id,
                        "stepOffset": offset,
                        "startIndex": offset,
                        "endIndex": end,
                    },
                    timeout=5.0,
                ).get("steps"),
                start=0,
                total=total,
            )
            if loaded is None:
                return None
            after = _rpc_post(
                url, csrf_token, "GetCascadeTrajectory", {"cascadeId": cascade_id}, timeout=2.5
            )
        except (CodexChatAgentError, OSError, ValueError):
            return None
        if (
            after.get("status") != "CASCADE_RUN_STATUS_IDLE"
            or after.get("numTotalSteps") != total
            or _mapping(after.get("trajectory")).get("cascadeId") != cascade_id
        ):
            return None
        return [cast(ProofObject, step) for _, step in loaded[0]]
    if total == 0 or (total is None and isinstance(inline_steps, list) and len(inline_steps) == 0):
        return []
    return None


def _antigravity_has_running_step(steps: Sequence[ProofMapping]) -> bool:
    return any(step.get("status") == "CORTEX_STEP_STATUS_RUNNING" for step in steps)


def _antigravity_has_final_response(steps: Sequence[ProofMapping]) -> bool:
    for step in steps:
        if step.get("type") != "CORTEX_STEP_TYPE_PLANNER_RESPONSE":
            continue
        planner = step.get("plannerResponse")
        if not isinstance(planner, Mapping) or planner.get("toolCalls"):
            continue
        answer = planner.get("modifiedResponse") or planner.get("response")
        if isinstance(answer, str) and answer.strip():
            return True
    return False


def _probe_antigravity_report_failure(
    store: ReportProofStore,
    original: ProofMapping,
) -> tuple[list[str], ProofObject | None] | None:
    """Query the Antigravity LS trajectory without starting or resuming work."""
    upstream_turn_id = _is_completed_host_failure_report(original)
    if not _is_antigravity_turn_id(upstream_turn_id):
        return None
    extracted = _extract_antigravity_turn_cascade(store, original)
    if extracted is None:
        return None
    _, cascade_id, _ = extracted
    from .antigravity_adapter import _rpc_post, discover_antigravity_language_server

    found = discover_antigravity_language_server(
        include_model_config=False,
        deadline=time.monotonic() + 3.0,
    )
    if found is None:
        return [cascade_id], None
    url = str(found["url"])
    csrf_token = str(found["csrf_token"])
    try:
        traj = _rpc_post(
            url, csrf_token, "GetCascadeTrajectory", {"cascadeId": cascade_id}, timeout=2.5
        )
    except (CodexChatAgentError, OSError, ValueError):
        return [cascade_id], None
    if _mapping(traj.get("trajectory")).get("cascadeId") != cascade_id:
        return [cascade_id], None
    base_proof: ProofObject = {
        "adapter_kind": "antigravity_ls",
        "turn_id": upstream_turn_id,
        "thread_id": cascade_id,
    }
    if traj.get("status") != "CASCADE_RUN_STATUS_IDLE":
        return [cascade_id], {
            **base_proof,
            "in_progress": True,
            "target_in_progress": True,
            "contradicted": True,
        }
    steps = _antigravity_trajectory_steps(url, csrf_token, cascade_id, traj)
    if steps is None:
        return [cascade_id], None
    if _antigravity_has_running_step(steps):
        return [cascade_id], {
            **base_proof,
            "in_progress": True,
            "target_in_progress": True,
            "contradicted": True,
        }
    if _antigravity_has_final_response(steps):
        return [cascade_id], {
            **base_proof,
            "cascade_idle": True,
            "no_final_response": False,
            "contradicted": True,
            "explicit_contradiction": True,
        }
    return [cascade_id], {**base_proof, "cascade_idle": True, "no_final_response": True}


def observe_antigravity_report_failure(
    store: ReportProofStore,
    original: ProofMapping,
) -> ProofObject | None:
    """Read an Antigravity report timeout/failure observation without starting work."""
    extracted = _extract_antigravity_turn_cascade(store, original)
    if extracted is None:
        return None
    session, cascade_id, events = extracted
    upstream_turn_id = str(original["upstream_turn_id"])
    native_timeout = any(
        row.get("kind") == "agent.phase"
        and isinstance(row.get("payload"), Mapping)
        and cast(ProofMapping, row["payload"]).get("method") == "native/timeout"
        for row in events
    )
    from .antigravity_adapter import AntigravityAdapter

    return cast(
        ProofObject | None,
        AntigravityAdapter.failed_report_observation(
            cascade_id=cascade_id,
            turn_id=upstream_turn_id,
            native_timeout=native_timeout,
            model_id=cast(str | None, session.get("agent_model_id")),
            reasoning_effort=cast(str | None, session.get("agent_reasoning_effort")),
        ),
    )
