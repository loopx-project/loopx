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
import math
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
from loopx.paths import global_registry_path  # noqa: E402

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


def _load_state(path: Path | None, *, strict: bool = False) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        if strict:
            raise ValueError("worker state is unreadable") from None
        return {}
    if strict and not isinstance(raw, dict):
        raise ValueError("worker state is not an object")
    return raw if isinstance(raw, dict) else {}


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
            "agent_id": str(args.agent_id),
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


def _save_state(path: Path | None, state: dict[str, Any], *, durable: bool = False) -> None:
    if path is None:
        return
    if durable:
        from loopx.file_lock import write_json_atomic

        write_json_atomic(path, state, mode=0o600, directory_mode=0o700,
                          fsync_directory=True, sort_keys=True)
        return
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    tmp.chmod(0o600)
    os.replace(tmp, path)


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


def _run_wake(command: str, *, timeout_seconds: float | None,
              environment: dict[str, str] | None = None) -> CappedProcessResult:
    return run_capped_process(
        _shell_argv(command),
        stdin=b"",
        timeout_seconds=None if timeout_seconds is None else max(0.01, timeout_seconds),
        output_limit_bytes=PROCESS_OUTPUT_LIMIT_BYTES,
        termination_grace_seconds=10,
        env=environment,
    )


def _host_scope(args: argparse.Namespace) -> str:
    """Private transport scope; Todo/reset-token changes cannot reset a failure."""
    source = [str(Path(args.registry).expanduser().resolve()), args.runtime_root,
              args.goal_id, args.agent_id, args.wake_cmd, getattr(args, "wake_stop_exit_code", None),
              *[os.environ.get(k, "") for k in ("CODEX_HOME", "MODEL_NAME",
                  "LOOPX_CODEX_HOME", "CODEX_BIN", "LOOPX_PROJECT", "CODEX_WIRE_API",
                  "LOOPX_ITERATION_CONTEXT", "LOOPX_CODEX_SANDBOX", "LOOPX_EXECUTION_MODE",
                  "REASONING_EFFORT", "OPENAI_BASE_URL", "LOOPX_EXPECTED_COMMIT",
                  "LOOPX_EXPECTED_RUNNER_COMMIT")]]
    return sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()


def _host_attempt(state: dict[str, Any], scope: str) -> dict[str, Any]:
    """Validate local IO state; retry kinds/limits stay with the Host owner."""
    from loopx.control_plane.turn_driver.host_failure import normalize_host_failure_record
    from loopx.control_plane.turn_driver.codex_sessions import _valid_session_id

    if "host_attempt" not in state:
        return {}
    prior = state["host_attempt"]
    if not isinstance(prior, dict) or prior.get("scope") != scope:
        raise ValueError("Host attempt scope is unresolved")
    count = prior.get("attempt")
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError("Host attempt count is invalid")
    if prior.get("status") == "started":
        return prior
    if prior.get("status") == "stopped":
        code = prior.get("stop_exit_code")
        if isinstance(code, bool) or not isinstance(code, int):
            raise ValueError("Host stop result is invalid")
        return prior
    if prior.get("status") != "failed":
        raise ValueError("Host attempt status is invalid")
    failure = normalize_host_failure_record(prior.get("host_failure"))
    due = prior.get("not_before")
    if (failure["attempt"] != count or isinstance(due, bool)
            or not isinstance(due, (int, float)) or not math.isfinite(due) or due < 0):
        raise ValueError("Host attempt result is invalid")
    if prior.get("session_id") is not None and _valid_session_id(prior["session_id"]) != prior["session_id"]:
        raise ValueError("Host attempt session is invalid")
    return prior


def _host_result(result: CappedProcessResult, attempt: dict[str, Any], now: float) -> dict[str, Any] | None:
    """Consume only the configured Host's content-free failure receipt."""
    from loopx.control_plane.turn_driver.codex_sessions import _valid_session_id
    from loopx.control_plane.turn_driver.host_failure import (
        build_host_failure_record, normalize_host_failure_record,
    )

    try:
        receipt = json.loads(result.stdout)
        if not isinstance(receipt, dict):
            raise ValueError("not an object")
    except (ValueError, UnicodeError):
        receipt = {}
    session = _mapping(receipt.get("session"))
    candidate = session.get("session_id")
    session_id = (_valid_session_id(candidate)
                  if session.get("binding_scope") == "agent" and isinstance(candidate, str) else None)
    if session_id != candidate:
        session_id = None
    same_session = not attempt.get("session_id") or attempt["session_id"] == session_id
    if (result.failure_kind is None and result.returncode == 0 and receipt.get("ok") is True
            and not receipt.get("timed_out") and not receipt.get("error_kind")
            and "host_failure" not in receipt and session_id and same_session):
        return None
    kind = "unknown"
    if (result.failure_kind is None and result.returncode != 0 and receipt.get("ok") is False
            and not receipt.get("timed_out") and not receipt.get("error_kind") and same_session):
        try:
            failure = normalize_host_failure_record(receipt.get("host_failure"))
            if failure["attempt"] == 1:
                kind = failure["kind"]
        except (ValueError, TypeError):
            pass
    failure = build_host_failure_record(kind, attempt=attempt["attempt"])
    return {**attempt, "status": "failed", "host_failure": failure,
            "session_id": attempt.get("session_id") or session_id,
            "not_before": now + failure.get("retry", {}).get("backoff_seconds", 0)}


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
    clock: Callable[[], float] = time.time,
) -> int:
    opted_in = bool(getattr(args, "wake_host_failure", False))
    retained = "host_attempt" in _load_state(resolve_state_path(args))
    if not opted_in and not retained:
        return _run_worker(args, sleep=sleep, clock=clock, host_guard=False)
    from loopx.file_lock import try_exclusive_file_lock

    try:
        with try_exclusive_file_lock(resolve_state_path(args).resolve(), agent_id=args.agent_id,
                                     operation="external_worker_host") as held:
            if held is None:
                _log("status=host_failure_hold kind=unknown reason=worker_already_running")
                return 2
            return _run_worker(args, sleep=sleep, clock=clock, host_guard=True)
    except (OSError, ValueError):
        _log("status=host_failure_hold kind=unknown reason=state_or_original_result_unresolved")
        return 2


def _run_worker(
    args: argparse.Namespace, *, sleep: Callable[[float], None], clock: Callable[[], float],
    host_guard: bool,
) -> int:
    should_run_command = build_should_run_command(args)
    state_path = resolve_state_path(args)
    if host_guard:
        state_path = state_path.resolve()
    once = bool(args.once)
    scope = _host_scope(args) if host_guard else ""

    while True:
        state_unreadable = False
        try:
            state = _load_state(state_path, strict=host_guard)
            previous_token = str(state.get("reset_token") or "")
            unchanged_count = int(state.get("unchanged_count") or 0)
            prior = _host_attempt(state, scope) if host_guard else {}
        except (ValueError, TypeError, OverflowError):
            if not host_guard:
                raise
            # Unknown local evidence forbids dispatch, not Core observation.
            state, previous_token, unchanged_count, prior = {}, "", 0, {}
            state_unreadable = True

        def save(fields: dict[str, Any]) -> None:
            _save_state(state_path, {**state, **fields} if host_guard else fields, durable=host_guard)

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
                if not state_unreadable:
                    save({"reset_token": decision.reset_token, "unchanged_count": 0})
                return 0

            if state_unreadable:
                _log("status=host_failure_hold kind=unknown reason=state_unreadable "
                     "goal_complete=false new_wake=false")
                return 2

            if prior.get("status") == "stopped":
                _log(f"status=wake_requested_stop wake_rc={prior['stop_exit_code']} "
                     "replayed=true goal_complete=false new_wake=false")
                return 0

            if decision.should_run:
                wake_status = "would_wake"
                wake_rc = None
                wake_failure_kind = None
                wake_failed = False
                if args.wake_cmd:
                    environment = None
                    if host_guard:
                        from loopx.control_plane.turn_driver.host_failure import host_failure_retry_available

                        if prior and (not getattr(args, "wake_host_failure", False)
                                      or prior["status"] == "started" or not prior.get("session_id")
                                      or not host_failure_retry_available(prior["host_failure"])):
                            kind = _mapping(prior.get("host_failure")).get("kind", "unknown")
                            _log(f"status=host_failure_hold kind={kind} attempt={prior['attempt']} "
                                 "next=original_host_recovery goal_complete=false new_wake=false")
                            return 2
                        delay = max(0, prior.get("not_before", 0) - clock())
                        if delay:
                            _log(f"status=host_failure_backoff seconds={delay:.3f} new_wake=false")
                            if once:
                                return 2
                            sleep(min(delay, 60.0))
                            retry_after_error = True
                            break
                        state["host_attempt"] = {"scope": scope, "status": "started",
                            "attempt": prior.get("attempt", 0) + 1,
                            "session_id": prior.get("session_id")}
                        save({})
                        environment = {**os.environ, "LOOPX_HOST_RETRY_SESSION_ID": prior.get("session_id") or ""}
                    wake_result = _run_wake(
                        args.wake_cmd,
                        timeout_seconds=getattr(args, "wake_timeout_seconds", None),
                        **({"environment": environment} if host_guard else {}),
                    )
                    wake_rc = wake_result.returncode
                    wake_failure_kind = wake_result.failure_kind
                    stop_code = getattr(args, "wake_stop_exit_code", None)
                    if (stop_code is not None and wake_failure_kind is None
                            and wake_rc == stop_code):
                        _log(f"status=wake_requested_stop wake_rc={wake_rc}")
                        if host_guard:
                            state["host_attempt"].update(status="stopped", stop_exit_code=wake_rc)
                        save({
                            "reset_token": decision.reset_token, "unchanged_count": 0,
                            **({"last_wake_rc": wake_rc, "last_wake_status": "wake_requested_stop"}
                               if host_guard else {}),
                        })
                        return 0
                    wake_failed = wake_failure_kind is not None or wake_rc != 0
                    if host_guard:
                        failure = _host_result(wake_result, state["host_attempt"], clock())
                        if failure is None:
                            state.pop("host_attempt", None)
                        else:
                            state["host_attempt"] = failure
                            wake_failed = True
                        state.update(last_wake_rc=wake_rc,
                                     last_wake_status="wake_failed" if wake_failed else "wake_ok",
                                     last_wake_failure_kind=wake_failure_kind)
                        save({})
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
                    save({
                            "reset_token": decision.reset_token,
                            "unchanged_count": unchanged_count,
                            "last_wake_rc": wake_rc,
                            "last_wake_status": "wake_failed",
                            "last_wake_failure_kind": wake_failure_kind,
                        })
                    if once:
                        return (
                            int(wake_rc)
                            if wake_failure_kind is None and wake_rc
                            else 2
                        )
                    if host_guard:
                        retry_after_error = True
                        break
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

            save({
                    "reset_token": decision.reset_token,
                    "unchanged_count": unchanged_count,
                })
            _log(
                "status=stop_after_unchanged_limit "
                f"unchanged_limit={decision.unchanged_limit} "
                "next=explicit_resume_or_fresh_evidence"
            )
            return 0

        if retry_after_error:
            continue

        save({
                "reset_token": decision.reset_token,
                "unchanged_count": unchanged_count,
            })

        if once:
            return 0
        # A completed synchronous wake is an event to recheck admission now,
        # not a reason to add an idle polling interval. The fresh quota decision
        # still owns continuation; waiting/observe-only ticks retain their
        # cadence and failures use the independent backoff path above.
        if not wake_completed:
            sleep(max(5, interval_minutes * 60))


def main(argv: Sequence[str] | None = None) -> int:
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
        "--wake-host-failure", action="store_true",
        help="Opt in to the configured Host JSON receipt (ok, session, host_failure). "
             "Persist bounded same-session retries; unknown/hard failures require original Host recovery. "
             "No model/account switching or Goal settlement is performed.",
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
    return run_worker(args)


if __name__ == "__main__":
    def cancelled(signum, frame):
        raise KeyboardInterrupt("scheduler cancelled")

    signal.signal(signal.SIGTERM, cancelled)
    raise SystemExit(main())
