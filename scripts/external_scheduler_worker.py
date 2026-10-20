#!/usr/bin/env python3
"""Scheduler-hint-aware external worker for generic visible CLI loops.

This is a `shell_worker` connector (cron/launchd/service timer). It does NOT
replace the host TUI. On every tick it asks LoopX whether the registered agent
should run, projects a one-line public-safe status, and either:

  * observe-only (default): sleeps until the scheduler hint says to re-check;
  * with --wake-cmd: runs the configured command when should_run is true.

The generic CLI scheduler hint carries only the *initial* interval plus a
progression ladder and an unchanged-poll limit. Unlike codex-app's stateful
host backoff, the local progression index is not persisted server-side, so this
worker tracks consecutive unchanged polls in a small state file and advances
through the ladder exactly as a visible TUI loop would.

No raw transcripts, task text, credentials, or local absolute project paths are
emitted. The status line is the liveness signal.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
import shlex
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.extensions.process_runtime import (  # noqa: E402
    CappedProcessResult,
    run_capped_process,
)
from loopx.file_lock import try_exclusive_file_lock, write_json_atomic  # noqa: E402
from loopx.paths import global_registry_path  # noqa: E402
from loopx.control_plane.goals.goal_ref_validation import exact_goal_ref  # noqa: E402
from loopx.control_plane.quota.effective_action import EffectiveAction  # noqa: E402
from loopx.control_plane.quota.effect_program import SettlementIdentity  # noqa: E402
from loopx.control_plane.quota.settlement import read_heartbeat_settlement  # noqa: E402
from loopx.control_plane.todos.contract import (  # noqa: E402
    normalize_todo_claimed_by,
    normalize_todo_replan_obligation_id,
)

SCHEDULER_DETAIL_KEY = "local_scheduler"
LOCAL_SCHEDULER_STOP_DIRECTIVE = "stop"
PROCESS_OUTPUT_LIMIT_BYTES = 1_000_000


@dataclass(frozen=True)
class TickDecision:
    should_run: bool
    action: str
    cadence_class: str
    reason: str
    interval_minutes: int
    progression: tuple[int, ...]
    unchanged_limit: int | None
    after_limit: str
    final_probe_enabled: bool
    final_probe_action: str
    reset_token: str
    terminal: bool

    @property
    def sleep_seconds(self) -> int:
        return max(5, int(self.interval_minutes) * 60)


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _extract_local_scheduler(payload: dict[str, Any]) -> dict[str, Any]:
    hint = _mapping(payload.get("scheduler_hint"))
    detail = _mapping(hint.get("cold_path_detail"))
    local = _mapping(detail.get(SCHEDULER_DETAIL_KEY))
    if local:
        return local
    # Without --include-detail scheduler only the hot-path limit strings are
    # present; fail closed instead of inventing a cadence.
    raise ValueError(
        "scheduler_hint.cold_path_detail.local_scheduler missing; "
        "run quota should-run with --include-detail scheduler"
    )


def _extract_reset_token(payload: dict[str, Any]) -> str:
    reset_policy = _mapping(_mapping(payload.get("scheduler_hint")).get("reset_policy"))
    return str(reset_policy.get("reset_token") or "").strip()


def _local_scheduler_directive(hint: dict[str, Any]) -> str:
    unchanged_poll = _mapping(hint.get("unchanged_poll"))
    return str(unchanged_poll.get(SCHEDULER_DETAIL_KEY) or "").strip()


def parse_tick(payload: dict[str, Any]) -> TickDecision:
    hint = _mapping(payload.get("scheduler_hint"))
    action = str(hint.get("action") or "").strip()
    cadence_class = str(hint.get("cadence_class") or "").strip()
    reason = str(hint.get("reason") or payload.get("state") or "").strip()
    should_run = payload.get("should_run")
    if _local_scheduler_directive(hint) == LOCAL_SCHEDULER_STOP_DIRECTIVE:
        # Terminal packets intentionally omit cold-path cadence detail: no
        # further wake is legal, so an interval cannot affect the decision.
        # The producer-owned directive covers every stop action without a
        # second consumer-side action vocabulary.
        return TickDecision(
            should_run=False,
            action=action,
            cadence_class=cadence_class,
            reason=reason,
            interval_minutes=1,
            progression=(1,),
            unchanged_limit=None,
            after_limit="stop_tick_loop",
            final_probe_enabled=False,
            final_probe_action="",
            reset_token=_extract_reset_token(payload),
            terminal=True,
        )
    # Consume the Core boolean, never Python truthiness (e.g. "false" or 1).
    # The terminal directive above wins even over a malformed admission value.
    if payload.get("ok") is False:
        raise ValueError("quota decision failed; refusing model wake")
    if not isinstance(should_run, bool):
        raise ValueError("quota should_run must be a boolean; refusing model wake")
    local = _extract_local_scheduler(payload)

    progression_values = local.get("example_progression_minutes")
    progression: tuple[int, ...] = ()
    if isinstance(progression_values, (list, tuple)):
        progression = tuple(
            max(1, int(value)) for value in progression_values if str(value).strip()
        )
    initial = max(1, int(local.get("recommended_interval_minutes") or 1))
    if not progression:
        progression = (initial,)

    unchanged_limit_raw = local.get("unchanged_poll_limit")
    unchanged_limit = (
        int(unchanged_limit_raw)
        if unchanged_limit_raw is not None and str(unchanged_limit_raw).strip()
        else None
    )
    after_limit = str(local.get("after_limit") or "continue").strip() or "continue"
    final_probe = _mapping(local.get("final_quota_replan_check"))
    if (
        unchanged_limit is not None
        and after_limit == "stop_tick_loop"
        and (
            final_probe.get("enabled") is not True
            or final_probe.get("action") != "rerun_quota_should_run_once"
        )
    ):
        raise ValueError(
            "stop_tick_loop requires final_quota_replan_check "
            "action rerun_quota_should_run_once"
        )

    return TickDecision(
        should_run=should_run,
        action=action,
        cadence_class=cadence_class,
        reason=reason,
        interval_minutes=initial,
        progression=progression,
        unchanged_limit=unchanged_limit,
        after_limit=after_limit,
        final_probe_enabled=bool(final_probe.get("enabled")),
        final_probe_action=str(final_probe.get("action") or "").strip(),
        reset_token=_extract_reset_token(payload),
        terminal=False,
    )


def run_quota_should_run(
    command_prefix: Sequence[str],
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    completed = run_capped_process(
        list(command_prefix),
        stdin=b"",
        timeout_seconds=max(0.01, timeout_seconds),
        output_limit_bytes=PROCESS_OUTPUT_LIMIT_BYTES,
    )
    if completed.failure_kind is not None:
        raise RuntimeError(f"quota should-run failed: {completed.failure_kind}")
    if completed.returncode != 0:
        raise RuntimeError(f"quota should-run exited {completed.returncode}")
    try:
        payload = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"quota should-run returned invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("quota should-run returned a non-object payload")
    return payload


def _load_state(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("worker state unreadable; recover the original state before waking") from exc
    if not isinstance(raw, dict):
        raise ValueError("worker state must be an object")
    count = raw.get("unchanged_count", 0)
    if type(count) is not int or count < 0:
        raise ValueError("worker unchanged_count must be a non-negative integer")
    attempts = raw.get("replan_attempts", [])
    if not isinstance(attempts, list):
        raise ValueError("worker replan_attempts must be a list")
    for attempt in attempts:
        if (not isinstance(attempt, dict) or not isinstance(attempt.get("status"), str)
                or attempt["status"] not in {"started", "returned", "unknown"}):
            raise ValueError("worker replan attempt is invalid; recover the original work")
        _attempt_scope(attempt)
        if not normalize_todo_replan_obligation_id(attempt.get("obligation_id")):
            raise ValueError("worker replan attempt is missing its original obligation")
        if attempt.get("settlement_identity") is not None:
            _attempt_settlement_identity(attempt)
    return raw


def _default_state_path(args: argparse.Namespace) -> Path:
    configured_root = str(os.environ.get("XDG_STATE_HOME") or "").strip()
    state_root = (
        Path(configured_root).expanduser()
        if configured_root
        else Path.home() / ".local" / "state"
    )
    if not state_root.is_absolute():
        state_root = Path.home() / ".local" / "state"
    identity = json.dumps(
        {
            "registry": str(args.registry),
            "goal_id": str(args.goal_id),
            "agent_id": normalize_todo_claimed_by(args.agent_id) or str(args.agent_id),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    filename = sha256(identity).hexdigest()[:24] + ".json"
    return state_root / "loopx" / "external-worker" / filename


def resolve_state_path(args: argparse.Namespace) -> Path:
    if args.state_file:
        return Path(args.state_file).expanduser()
    return _default_state_path(args)


def _save_state(path: Path | None, state: dict[str, Any]) -> None:
    if path is None:
        return
    # Dispatch intent must survive a process crash. Reuse the atomic writer's
    # fsync and unique temporary file; a failed write cannot permit a wake.
    write_json_atomic(path, state, mode=0o600, directory_mode=0o700,
                      fsync_directory=True, indent=None, sort_keys=True)


def _attempt_scope(value: dict[str, Any]) -> dict[str, Any]:
    goal_id = value.get("goal_id")
    registry = value.get("registry")
    runtime_root = value.get("runtime_root")
    if (not isinstance(goal_id, str) or not goal_id.strip()
            or any(not isinstance(route, str) or not Path(route).is_absolute() for route in (registry, runtime_root))):
        raise ValueError("worker replan attempt is missing its original runtime, registry and Goal")
    goal_ref = None
    if value.get("goal_ref") is not None:
        ref = _mapping(value["goal_ref"])
        goal_ref = exact_goal_ref(str(ref.get("goal_id") or ""), str(ref.get("goal_instance_id") or ""))
        if goal_ref["goal_id"] != goal_id:
            raise ValueError("worker replan GoalRef does not match its original Goal")
    agent_id = value.get("agent_id")
    if not isinstance(agent_id, str) or not agent_id.strip():
        raise ValueError("worker replan attempt is missing its original agent")
    agent_id = normalize_todo_claimed_by(agent_id)
    if agent_id is None:
        raise ValueError("worker replan attempt has an invalid original agent")
    return {"registry": registry, "runtime_root": runtime_root,
            "goal_id": goal_id, "goal_ref": goal_ref, "agent_id": agent_id}


def _admission_scope(payload: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    agent_id = _mapping(payload.get("agent_identity")).get("agent_id")
    expected_agent = normalize_todo_claimed_by(args.agent_id)
    if (not expected_agent or agent_id != expected_agent
            or payload.get("goal_id", args.goal_id) != args.goal_id):
        raise ValueError("worker scope does not match current admission")
    runtime_root = payload.get("runtime_root")
    if (not isinstance(runtime_root, str) or not Path(runtime_root).is_absolute()
            or (args.runtime_root and Path(args.runtime_root).expanduser().resolve() != Path(runtime_root).resolve())):
        raise ValueError("worker runtime does not match current admission")
    # Ordinary executable registries currently provide no exact GoalRef. Keep
    # their real alias scope; never invent a lifetime or reset it on log/id churn.
    return _attempt_scope({"registry": str(Path(args.registry).expanduser().resolve()),
                           "runtime_root": str(Path(runtime_root).resolve()),
                           "goal_id": args.goal_id, "goal_ref": payload.get("goal_ref"),
                           "agent_id": agent_id})


def _same_replan_scope(left: dict[str, Any], right: dict[str, Any]) -> bool:
    a, b = _attempt_scope(left), _attempt_scope(right)
    if any(a[key] != b[key] for key in ("registry", "runtime_root", "goal_id", "agent_id")):
        return False
    # Losing or gaining an exact identity is not proof that old work ended.
    # Only two different, positively known lifetimes can separate this route.
    return a["goal_ref"] is None or b["goal_ref"] is None or a["goal_ref"] == b["goal_ref"]


def _attempt_settlement_identity(attempt: dict[str, Any]) -> SettlementIdentity:
    try:
        identity = SettlementIdentity.from_runtime_payload(attempt["settlement_identity"])
    except (KeyError, RuntimeError) as exc:
        raise ValueError("worker original settlement identity is invalid") from exc
    if (identity.goal_id != attempt["goal_id"] or identity.agent_id != attempt["agent_id"]
            or identity.todo_id is not None or identity.replan_obligation_id != attempt["obligation_id"]):
        raise ValueError("worker original settlement identity does not match its replan")
    return identity


def _replan_attempt(payload: dict[str, Any], args: argparse.Namespace) -> dict[str, Any] | None:
    if payload.get("effective_action") != EffectiveAction.AUTONOMOUS_REPLAN_REQUIRED.value:
        return None
    obligation = _mapping(payload.get("autonomous_replan_obligation"))
    # Original lifecycle settlement has its own journal and is not a new
    # research decision. Do not substitute this local retry brake for it.
    if obligation.get("resolution_mode") in {"todo_lifecycle_settlement", "receipt_bound_replan_settlement"}:
        return None
    obligation_id = normalize_todo_replan_obligation_id(obligation.get("obligation_id"))
    scope = _admission_scope(payload, args)
    if (obligation.get("required") is not True or not obligation_id
            or obligation.get("agent_id") not in {None, scope["agent_id"]}):
        raise ValueError("automatic replan requires its current Goal, agent and obligation identity")
    attempt = {**scope, "obligation_id": obligation_id}
    identity = _mapping(payload.get("heartbeat_receipt")).get("settlement_identity")
    if identity is not None:
        attempt["settlement_identity"] = identity
        _attempt_settlement_identity(attempt)
    return attempt


def _pending_replan_attempt(state: dict[str, Any], payload: dict[str, Any], args: argparse.Namespace) -> bool:
    """Unknown original effects cannot acquire another wake under a new id."""
    unresolved = [attempt for attempt in state.get("replan_attempts", [])
                  if attempt["status"] in {"started", "unknown"}]
    if not unresolved:
        return False
    # Absence of a fresh lifecycle is not evidence that the old one ended.
    scope = _admission_scope(payload, args)
    return any(_same_replan_scope(attempt, scope) for attempt in unresolved)


def _read_resolved_replan_attempts(state: dict[str, Any], payload: dict[str, Any], args: argparse.Namespace) -> bool:
    """Only the original settlement owner can resolve a known lost response.

    An opaque unbound shell command has no such proof. Leave it visibly unknown
    instead of inferring a Turn from the latest row or dispatching a new command.
    """
    unresolved = [attempt for attempt in state.get("replan_attempts", [])
                  if attempt["status"] in {"started", "unknown"} and attempt.get("settlement_identity")]
    if not unresolved:
        return False
    scope = _admission_scope(payload, args)
    changed = False
    for attempt in unresolved:
        if not _same_replan_scope(attempt, scope):
            continue
        original = _attempt_settlement_identity(attempt)
        try:
            readback = read_heartbeat_settlement(
                Path(attempt["runtime_root"]), goal_id=original.goal_id, agent_id=original.agent_id, todo_id=None,
                turn_instance_id=original.turn_instance_id, replan_obligation_id=original.replan_obligation_id,
                resolve_original_binding=True, registry_path=Path(attempt["registry"]), goal_ref=attempt.get("goal_ref"))
        except (OSError, RuntimeError, ValueError) as exc:
            raise ValueError("original settlement readback unavailable; preserve the attempt and repair its source") from exc
        if (readback is not None and readback.identity.failure is None
                and readback.identity.value is not None and readback.identity.value.as_dict() == original.as_dict()
                and readback.replay_phase == "settled" and readback.settlement.failure is None
                and readback.settlement.value is not None):
            attempt.update(status="returned", recovery="original_settlement_verified")
            changed = True
    return changed


def _observe_worker_replan(
    payload: dict[str, Any], args: argparse.Namespace,
    attempt: dict[str, Any] | None, status: str | None,
) -> None:
    """Publish the driver's observation through its existing poll receipt.

    This is readback only. Admission and original-result recovery never read
    this receipt, and failure to publish cannot erase the durable attempt.
    """
    from loopx.agent_registry import load_goal_from_registry
    from loopx.control_plane.goals.path_resolution import resolve_goal_local_path
    from loopx.control_plane.quota.host_poll_receipts import record_host_poll_receipt

    try:
        registry = Path(args.registry).expanduser().resolve()
        goal = load_goal_from_registry(registry, args.goal_id)
        if goal is None:
            return
        state_path = resolve_goal_local_path(goal.get("state_file"), goal, fallback_base=registry.parent)
        observation = None
        if attempt is not None:
            identity = _mapping(attempt.get("settlement_identity"))
            observation = {
                "status": status, "obligation_id": attempt["obligation_id"],
                "turn_instance_id": identity.get("turn_instance_id"),
                "runtime_root": attempt["runtime_root"],
                "registry_goal_digest": attempt.get("registry_goal_digest"),
            }
            if "source_goal_observation_digest" in attempt:
                observation["source_goal_observation_digest"] = attempt["source_goal_observation_digest"]
            else:
                # Older attempts bound only the selected registry view. Add
                # source association only while that original view still
                # matches; never rebind an old outcome to changed work.
                current = _worker_goal_observation_digests(args)
                if current.get("registry_goal_digest") and current["registry_goal_digest"] == attempt.get("registry_goal_digest"):
                    observation["source_goal_observation_digest"] = current["source_goal_observation_digest"]
        record_host_poll_receipt(goal, state_path, agent_id=normalize_todo_claimed_by(args.agent_id),
                                 decision=payload, worker_observation=observation)
    except (OSError, RuntimeError, TypeError, ValueError):
        _log("status=worker_observation_unavailable next=inspect_original_worker_state")


def _worker_goal_observation_digests(args: argparse.Namespace) -> dict[str, str | None]:
    """Capture an observation witness before dispatch, never an admission."""
    from loopx.agent_registry import load_goal_from_registry
    from loopx.history import goal_registry_digest, host_worker_goal_observation_digest

    result: dict[str, str | None] = {"registry_goal_digest": None, "source_goal_observation_digest": None}
    try:
        registry = Path(args.registry).expanduser().resolve()
        goal = load_goal_from_registry(registry, args.goal_id)
        if goal is not None:
            result.update(registry_goal_digest=goal_registry_digest(goal),
                          source_goal_observation_digest=host_worker_goal_observation_digest(goal, registry_path=registry))
    except (OSError, RuntimeError, TypeError, ValueError):
        pass
    return result


def select_interval(
    decision: TickDecision,
    *,
    unchanged_count: int,
) -> tuple[int, str]:
    if decision.should_run:
        return decision.interval_minutes, "active"
    index = min(max(0, unchanged_count), len(decision.progression) - 1)
    return decision.progression[index], f"unchanged#{index + 1}"


def _log(line: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"{stamp} {line}", flush=True)


def _shell_argv(command: str) -> list[str]:
    if os.name == "nt":  # pragma: no cover - exercised on Windows hosts.
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", command]
    return ["/bin/sh", "-c", command]


def _run_wake(command: str, *, timeout_seconds: float | None) -> CappedProcessResult:
    return run_capped_process(
        _shell_argv(command),
        stdin=b"",
        timeout_seconds=None if timeout_seconds is None else max(0.01, timeout_seconds),
        output_limit_bytes=PROCESS_OUTPUT_LIMIT_BYTES,
        termination_grace_seconds=10,
    )


def build_should_run_command(args: argparse.Namespace) -> list[str]:
    command = [
        args.cli_bin,
        "--format",
        "json",
        "--registry",
        str(args.registry),
    ]
    if args.runtime_root:
        command.extend(["--runtime-root", str(args.runtime_root)])
    command.extend(
        [
            "quota",
            "should-run",
            "--goal-id",
            args.goal_id,
            "--agent-id",
            args.agent_id,
            "--runtime-profile",
            args.runtime_profile,
            "--include-detail",
            "scheduler",
        ]
    )
    return command


def _scheduler_contract_identity(
    payload: dict[str, Any],
    decision: TickDecision,
) -> tuple[Any, ...]:
    selected = _mapping(payload.get("selected_todo"))
    return (
        decision.should_run,
        decision.action,
        decision.cadence_class,
        decision.reset_token,
        str(payload.get("effective_action") or "").strip(),
        str(selected.get("todo_id") or "").strip(),
    )


def run_worker(
    args: argparse.Namespace,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    state_path = resolve_state_path(args)
    try:
        state_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # One configured state route has one live synchronous consumer. A
        # second service/cron invocation observes the owner and does no work.
        with try_exclusive_file_lock(state_path, agent_id=normalize_todo_claimed_by(args.agent_id),
                                     operation="external_scheduler_worker") as lock:
            if lock is None:
                _log("status=already_running next=observe_current_worker")
                return 0
            return _run_worker_locked(args, state_path=state_path, sleep=sleep)
    except (OSError, ValueError) as exc:
        reason = "worker state I/O failed; preserve original work" if isinstance(exc, OSError) else str(exc)
        _log(f"status=state_error error={shlex.quote(reason)}")
        return 2


def _run_worker_locked(
    args: argparse.Namespace,
    *,
    state_path: Path,
    sleep: Callable[[float], None],
) -> int:
    should_run_command = build_should_run_command(args)
    once = bool(args.once)

    while True:
        state = _load_state(state_path)
        previous_token = str(state.get("reset_token") or "")
        unchanged_count = int(state.get("unchanged_count") or 0)

        try:
            payload = run_quota_should_run(
                should_run_command,
                timeout_seconds=float(getattr(args, "quota_timeout_seconds", 30.0)),
            )
            decision = parse_tick(payload)
        except (RuntimeError, ValueError) as exc:
            _log(f"status=tick_error error={shlex.quote(str(exc))}")
            if once:
                return 2
            sleep(max(5, args.error_backoff_seconds))
            continue

        retry_after_error = False
        wake_completed = False
        while True:
            if decision.reset_token and decision.reset_token != previous_token:
                unchanged_count = 0

            interval_minutes, phase = select_interval(
                decision, unchanged_count=unchanged_count
            )

            effective_action = str(payload.get("effective_action") or "").strip()
            selected = _mapping(payload.get("selected_todo"))
            selected_id = str(selected.get("todo_id") or "").strip()

            if decision.terminal:
                _log(
                    "status=terminal "
                    f"action={decision.action} class={decision.cadence_class} "
                    f"reason={shlex.quote(decision.reason)}"
                )
                _save_state(
                    state_path,
                    {**state, "reset_token": decision.reset_token, "unchanged_count": 0},
                )
                return 0

            if decision.should_run:
                wake_status = "would_wake"
                wake_rc = None
                wake_failure_kind = None
                wake_failed = False
                if args.wake_cmd:
                    if _read_resolved_replan_attempts(state, payload, args):
                        _save_state(state_path, state)
                    if _pending_replan_attempt(state, payload, args):
                        scope = _admission_scope(payload, args)
                        original = next(previous for previous in state["replan_attempts"]
                                        if previous["status"] in {"started", "unknown"}
                                        and _same_replan_scope(previous, scope))
                        _observe_worker_replan(payload, args, original, "replan_outcome_unknown")
                        _log("status=replan_outcome_unknown next=use_original_host_recovery "
                             "goal_complete=false new_wake=false")
                        return 2
                    attempt = _replan_attempt(payload, args)
                    if attempt is not None:
                        attempts = state.setdefault("replan_attempts", [])
                        original = next((previous for previous in attempts
                                         if _same_replan_scope(previous, attempt)
                                         and previous["obligation_id"] == attempt["obligation_id"]), None)
                        if original is not None:
                            _observe_worker_replan(payload, args, original, "replan_paused")
                            _log("status=replan_paused reason=unresolved_original_obligation "
                                 f"obligation={attempt['obligation_id']} "
                                 "next=inspect_original_work_or_supply_concrete_direction "
                                 "goal_complete=false new_wake=false")
                            return 0
                        attempt["status"] = "started"
                        attempt.update(_worker_goal_observation_digests(args))
                        attempts.append(attempt)
                        _save_state(state_path, {**state, "reset_token": decision.reset_token,
                                                "unchanged_count": unchanged_count})
                    _observe_worker_replan(payload, args, None, None)
                    wake_result = _run_wake(
                        args.wake_cmd,
                        timeout_seconds=getattr(args, "wake_timeout_seconds", None),
                    )
                    wake_rc = wake_result.returncode
                    wake_failure_kind = wake_result.failure_kind
                    stop_code = getattr(args, "wake_stop_exit_code", None)
                    expected_stop = (stop_code is not None and wake_failure_kind is None
                                     and wake_rc == stop_code)
                    if attempt is not None:
                        # This records process transport only. It is neither a
                        # semantic ACK nor a completion/spend receipt.
                        attempt.update(status="returned" if wake_failure_kind is None and
                                       (wake_rc == 0 or expected_stop) else "unknown",
                                       wake_rc=wake_rc, failure_kind=wake_failure_kind)
                        _save_state(state_path, state)
                        if attempt["status"] == "unknown":
                            _observe_worker_replan(payload, args, attempt, "replan_outcome_unknown")
                    if (stop_code is not None and wake_failure_kind is None
                            and wake_rc == stop_code):
                        _log(f"status=wake_requested_stop wake_rc={wake_rc}")
                        _save_state(state_path, {
                            **state,
                            "reset_token": decision.reset_token, "unchanged_count": 0,
                        })
                        return 0
                    wake_failed = wake_failure_kind is not None or wake_rc != 0
                    wake_status = "wake_failed" if wake_failed else "wake_ok"
                _log(
                    f"status=should_run {wake_status} "
                    f"action={decision.action} class={decision.cadence_class} "
                    f"effective_action={effective_action or '-'} "
                    f"todo={selected_id or '-'} "
                    f"reason={shlex.quote(decision.reason)}"
                    + (f" wake_rc={wake_rc}" if wake_rc is not None else "")
                )
                if wake_failed:
                    _save_state(
                        state_path,
                        {
                            **state,
                            "reset_token": decision.reset_token,
                            "unchanged_count": unchanged_count,
                            "last_wake_rc": wake_rc,
                            "last_wake_status": "wake_failed",
                            "last_wake_failure_kind": wake_failure_kind,
                        },
                    )
                    if once:
                        return (
                            int(wake_rc)
                            if wake_failure_kind is None and wake_rc
                            else 2
                        )
                    _log(
                        "status=wake_failed_backoff "
                        f"backoff_seconds={max(5, int(args.error_backoff_seconds))}"
                    )
                    sleep(max(5, args.error_backoff_seconds))
                    retry_after_error = True
                else:
                    unchanged_count = 0
                    wake_completed = bool(args.wake_cmd)
                break

            _log(
                "status=waiting "
                f"phase={phase} class={decision.cadence_class} "
                f"effective_action={effective_action or '-'} "
                f"next_check_minutes={interval_minutes} "
                f"unchanged_count={unchanged_count}"
                + (
                    f" unchanged_limit={decision.unchanged_limit}"
                    if decision.unchanged_limit is not None
                    else ""
                )
                + f" reason={shlex.quote(decision.reason)}"
            )
            unchanged_count += 1
            at_limit = (
                decision.unchanged_limit is not None
                and unchanged_count >= decision.unchanged_limit
            )
            if not (at_limit and decision.after_limit == "stop_tick_loop"):
                break

            if (
                decision.final_probe_enabled
                and decision.final_probe_action == "rerun_quota_should_run_once"
            ):
                try:
                    final_payload = run_quota_should_run(
                        should_run_command,
                        timeout_seconds=float(
                            getattr(args, "quota_timeout_seconds", 30.0)
                        ),
                    )
                    final_decision = parse_tick(final_payload)
                except (RuntimeError, ValueError) as exc:
                    _log(f"status=tick_error error={shlex.quote(str(exc))}")
                    if once:
                        return 2
                    sleep(max(5, args.error_backoff_seconds))
                    retry_after_error = True
                    break
                changed = _scheduler_contract_identity(
                    final_payload, final_decision
                ) != _scheduler_contract_identity(payload, decision)
                _log(
                    "status=final_quota_replan_probe "
                    f"changed={str(changed).lower()} "
                    f"action={final_decision.action or '-'} "
                    f"should_run={str(final_decision.should_run).lower()}"
                )
                if changed:
                    previous_token = decision.reset_token
                    payload = final_payload
                    decision = final_decision
                    continue

            _save_state(
                state_path,
                {
                    **state,
                    "reset_token": decision.reset_token,
                    "unchanged_count": unchanged_count,
                },
            )
            _log(
                "status=stop_after_unchanged_limit "
                f"unchanged_limit={decision.unchanged_limit} "
                "next=explicit_resume_or_fresh_evidence"
            )
            return 0

        if retry_after_error:
            continue

        _save_state(
            state_path,
            {
                **state,
                "reset_token": decision.reset_token,
                "unchanged_count": unchanged_count,
            },
        )

        if once:
            return 0
        # A completed synchronous wake is an event to recheck admission now,
        # not a reason to add an idle polling interval. The fresh quota decision
        # still owns continuation; waiting/observe-only ticks retain their
        # cadence and failures use the independent backoff path above.
        if not wake_completed:
            sleep(max(5, interval_minutes * 60))


def main(argv: Sequence[str] | None = None, *, sleep: Callable[[float], None] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scheduler-hint-aware external shell_worker for generic CLI loops.",
    )
    parser.add_argument("--cli-bin", default="loopx", help="LoopX CLI binary.")
    parser.add_argument(
        "--registry",
        help="Path to the LoopX registry; defaults to the selected current or legacy route.",
    )
    parser.add_argument("--runtime-root", help="Override registry common_runtime_root.")
    parser.add_argument(
        "--runtime-profile",
        default="generic_cli",
        help="Quota runtime profile that emits the local_scheduler hint "
        "(generic_cli for generic visible CLI loops such as TraeX/OpenCode).",
    )
    parser.add_argument("--goal-id", required=True)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument(
        "--state-file",
        help="Where to persist the unchanged-poll progression index "
        "(defaults to a private hashed file under XDG_STATE_HOME or ~/.local/state).",
    )
    parser.add_argument(
        "--wake-cmd",
        help="Optional shell command to run when should_run is true. "
        "Without it the worker is observe-only.",
    )
    parser.add_argument(
        "--wake-stop-exit-code",
        type=int,
        help="Opt-in nonzero wake exit code that ends this worker normally. "
        "Does not complete a Todo or spend quota; transport failures still fail.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single tick and exit instead of sleeping and looping.",
    )
    parser.add_argument(
        "--error-backoff-seconds",
        type=float,
        default=60.0,
        help="Sleep between failed quota should-run invocations when looping.",
    )
    parser.add_argument(
        "--quota-timeout-seconds",
        type=float,
        default=30.0,
        help="Maximum duration of one quota should-run probe.",
    )
    parser.add_argument(
        "--wake-timeout-seconds",
        type=float,
        default=None,
        help="Optional wake execution deadline; default waits for completion or cancellation.",
    )
    args = parser.parse_args(argv)
    if args.wake_stop_exit_code is not None and not 1 <= args.wake_stop_exit_code <= 255:
        parser.error("--wake-stop-exit-code must be between 1 and 255")
    if args.registry is None:
        try:
            args.registry = str(global_registry_path())
        except ValueError as exc:
            parser.error(str(exc))
    return run_worker(args) if sleep is None else run_worker(args, sleep=sleep)


if __name__ == "__main__":
    def cancelled(signum, frame):
        raise KeyboardInterrupt("scheduler cancelled")

    signal.signal(signal.SIGTERM, cancelled)
    raise SystemExit(main())
