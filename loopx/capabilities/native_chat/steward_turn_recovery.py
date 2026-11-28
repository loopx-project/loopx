"""Monitor restart and frozen/stopped report recovery helpers for StewardTurnHost."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import nullcontext
import json
from pathlib import Path
import time
from typing import Protocol, cast

from ...file_lock import LOCK_ACQUIRE_TIMEOUT_ERROR_CODE, exclusive_file_lock
from .steward_monitor import (
    actor_steward_binding_id,
    load_steward_monitor_turn_by_id,
    lookup_steward_monitor_turn,
)
from .steward_turn_common import (
    MONITOR_RECOVERY_RETRY_COOLDOWN_SECONDS,
    MonitorRestartCarrier,
    StewardStoreProtocol,
    StewardTurnHostProtocol,
    _FILE_LOCK_TIMEOUT_ERROR_RE,
    _POST_CONTEXT_WATCH_ERRORS,
    as_dict,
    as_dict_list,
    as_mapping,
    as_optional_dict,
    as_optional_str,
    iso_timestamp_seconds,
    monitor_store_for,
    native_steward_client_id,
    steward_monitor_for,
)

_MONITOR_PRE_POLL_ERROR = (
    "the native steward operation was not committed: quota_action_selection_rejected: "
    "explicit action selection is not currently eligible: auxiliary_monitor_not_selectable_in_advancement_lane"
)


class _ReportServiceProtocol(Protocol):
    @property
    def root(self) -> Path: ...

    def current_window(self, owner_key: str) -> tuple[dict[str, object] | None, dict[str, object] | None]: ...

    def freeze(self, request: Mapping[str, object], candidate: Mapping[str, object]) -> object: ...

    def delivery_current(self, owner_key: str, window: Mapping[str, object]) -> bool: ...


def _complete_failed_monitor_journal(journal: Sequence[Mapping[str, object]]) -> bool:
    return bool(journal) and (
        journal[0].get("kind") == "turn.queued"
        and journal[-1].get("kind") == "turn.failed"
        and all(
            type(row.get("sequence")) is int
            and row["sequence"] == expected_sequence
            and isinstance(row.get("kind"), str)
            and row["kind"] != ""
            for expected_sequence, row in enumerate(journal, start=1)
        )
    )


def monitor_selection_failed_before_poll(
    store: StewardStoreProtocol,
    turn: Mapping[str, object],
) -> bool:
    """Recognize only the legacy pre-poll monitor selection failure at the Chat boundary."""
    if not (
        turn.get("status") == "failed"
        and turn.get("error_code") == "runtime_error"
        and turn.get("error") == _MONITOR_PRE_POLL_ERROR
        and turn.get("delta_count") == 0
        and turn.get("upstream_turn_id") is None
        and turn.get("response") is None
    ):
        return False
    from ...chat_store import _read_jsonl

    try:
        journal = _read_jsonl(
            store._event_path(str(turn["session_id"]), str(turn["turn_id"])), raise_on_error=True, strict=True,
        )
    except (OSError, ValueError):
        return False
    return _complete_failed_monitor_journal(journal) and all(
        row["kind"] != "native_loopx.monitor_poll" for row in journal
    )


def monitor_restart_basis(
    host: StewardTurnHostProtocol,
    request: Mapping[str, object],
) -> dict[str, object]:
    """Read original worker/journals without dispatching or repairing effects.

    Any pending, committed, unreadable or legacy poll evidence withholds a
    new carrier. The shared typed admission owner interprets these facts.
    """
    recovery = as_optional_dict(request.get("monitor_restart_recovery"))
    if recovery is None:
        return {}
    from ...chat_store import _read_jsonl
    from ...control_plane.quota.monitor_poll import _monitor_poll_effect_id, _monitor_poll_receipt_path
    from .steward_commands import native_command
    from .steward_monitor import TARGET

    actor = as_mapping(request["actor"])
    original_raw = load_steward_monitor_turn_by_id(monitor_store_for(host.store), actor, str(recovery["original_turn_id"]))
    if original_raw is None:
        raise ValueError("the original stopped watch is unavailable")
    original = as_dict(original_raw)
    original_session_id = str(original["session_id"])
    original_turn_id = str(original["turn_id"])
    root = host.controller.coordination_runtime_root
    goal_id = str(actor["goal_id"])
    agent_id = str(actor["agent_id"])
    todo_id = request["todo_id"]
    turn_instance_id = str(request["turn_instance_id"])
    with exclusive_file_lock(
        host.store.turn_execution_lock_path(original_session_id, original_turn_id),
        timeout_seconds=0,
        operation="observe_stopped_native_watch",
    ):
        # Strict reads are essential: an unreadable/compacted journal is
        # unknown, never an empty observation or proof that work did not run.
        events = as_dict_list(
            _read_jsonl(
                host.store._event_path(original_session_id, original_turn_id),
                raise_on_error=True,
                strict=True,
            )
        )
        rows = as_dict_list(
            _read_jsonl(
                root / "goals" / goal_id / "runs" / "index.jsonl",
                raise_on_error=True,
                strict=True,
            )
        )
        present = any(
            row.get("classification") == "quota_monitor_poll"
            and row.get("turn_instance_id") == turn_instance_id
            for row in rows
        )
        target = (
            "managed-session-watch." + str(actor["session_id"])
            if actor.get("schema_version") == "loopx_managed_session_actor_v0"
            else TARGET
        )
        if actor.get("schema_version") != "loopx_managed_session_actor_v0":
            matched = next(
                (
                    row
                    for row in as_dict_list(steward_monitor_for(host.controller).todos(dict(actor)))
                    if row.get("todo_id") == todo_id
                ),
                None,
            )
            if matched and matched.get("target_key"):
                target = str(matched["target_key"])
        for probe_todo, probe_key in [(todo_id, target), (None, target), (None, None)]:
            effect = _monitor_poll_effect_id(
                runtime_root=root,
                goal_id=goal_id,
                agent_id=agent_id,
                turn_instance_id=turn_instance_id,
                todo_id=as_optional_str(probe_todo),
                target_key=probe_key,
                goal_ref=None,
            )
            present = present or _monitor_poll_receipt_path(root, goal_id, effect).exists()
        lease = as_dict(
            native_command(
                host.controller,
                ["task-lease", "inspect", "--goal-id", goal_id, "--todo-id", str(todo_id)],
            )
        )
        held = as_mapping(lease.get("lease"))
        available = lease.get("active") is False or (
            held.get("owner") == agent_id
            and held.get("idempotency_key") == "monitor-recovery." + original_turn_id
        )
        return {
            "monitor_restart_basis": {
                "original": original,
                "event_journal_complete": _complete_failed_monitor_journal(events),
                "event_kinds": [row.get("kind") for row in events],
                "poll_absent": not present,
                "contexts": [row["payload"] for row in events if row.get("kind") == "manager.context"],
                "phases": [row["payload"] for row in events if row.get("kind") == "agent.phase"],
                "old_worker_excluded": True,
                "lease_available": available,
                "observed_at": time.time(),
            }
        }


def monitor_restart_request(
    original: Mapping[str, object],
    *,
    now: float | None = None,
) -> dict[str, object] | None:
    """Prepare correlation only; current typed admission decides recovery."""
    prior = as_dict(original.get("loopx_request"))
    error = original.get("error_code")
    if (
        original.get("status") != "failed"
        or error not in {"server_restarted", "runtime_error", "runtime_unavailable", LOCK_ACQUIRE_TIMEOUT_ERROR_CODE}
        or prior.get("work_kind") != "monitor"
    ):
        return None
    if prior.get("monitor_restart_recovery") is not None and error != "server_restarted":
        msg = original.get("error")
        post_context_error = error in {"runtime_unavailable", LOCK_ACQUIRE_TIMEOUT_ERROR_CODE} or (
            error == "runtime_error"
            and (
                msg in _POST_CONTEXT_WATCH_ERRORS
                or (isinstance(msg, str) and _FILE_LOCK_TIMEOUT_ERROR_RE.fullmatch(msg) is not None)
            )
        )
        completed_ts = iso_timestamp_seconds(original.get("completed_at"))
        current_ts = time.time() if now is None else now
        if (
            not post_context_error
            or completed_ts is None
            or current_ts - completed_ts < MONITOR_RECOVERY_RETRY_COOLDOWN_SECONDS
        ):
            return None
    return {**prior, "monitor_restart_recovery": {"original_turn_id": original["turn_id"]}}


def monitor_restart_carrier(
    host: StewardTurnHostProtocol,
    original: Mapping[str, object],
    client_id: str,
) -> MonitorRestartCarrier | None:
    """Find the latest carrier for the same observation; typed admission proves a safe restart."""
    request = monitor_restart_request(original)
    if request is None:
        return None
    actor = as_optional_dict(as_mapping(original.get("loopx_request")).get("actor"))
    binding_id = actor_steward_binding_id(actor) if actor is not None else None
    base = client_id + ".restart-recovery"
    next_client_id = base
    session_id = str(original["session_id"])
    while True:
        raw_carrier = lookup_steward_monitor_turn(monitor_store_for(host.store), binding_id, session_id, next_client_id)
        if raw_carrier is None:
            break
        carrier = as_dict(raw_carrier)
        if as_mapping(carrier.get("loopx_request")).get("monitor_restart_recovery") != request["monitor_restart_recovery"]:
            raise ValueError("native watch continuation identity conflict")
        request = monitor_restart_request(carrier)
        if request is None:
            return {"turn": carrier}
        next_client_id = base + "." + str(carrier["turn_id"])
    return {"request": request, "client_id": next_client_id}


def report_continuation_basis(
    host: StewardTurnHostProtocol,
    request: Mapping[str, object],
    *,
    recovery_key: str,
    basis_key: str,
) -> dict[str, object]:
    """Transport original journal observations; the typed owner admits work."""
    recovery = as_optional_dict(request.get(recovery_key))
    if recovery is None:
        return {}
    from ...control_plane.turn_driver import inspect_loopx_turn_journal
    from ..periodic_report.private_generation import attempt_directory
    from .steward_reports import report_service

    actor = as_mapping(request["actor"])
    original = host.store.load_turn(str(actor["session_id"]), str(recovery["original_turn_id"]))
    result = as_mapping(as_mapping((original or {}).get("response")).get("native_loopx"))
    reports, owner = report_service(host.controller, dict(actor))
    _, window = reports.current_window(owner)
    folder = attempt_directory(reports.root, window) if window else None
    return {
        basis_key: {
            "original": original,
            "inspection": inspect_loopx_turn_journal(
                reports.root,
                goal_id=actor["goal_id"],
                agent_id=actor["agent_id"],
                turn_key=result["resume_turn_key"],
            ),
            "generation_present": folder is None
            or any((folder / name).exists() for name in ("editorial-response.json", "private-generation.json", "private-delivery.json")),
            "observed_at": time.time(),
        }
    }


def cleared_replan_guard(
    host: StewardTurnHostProtocol,
    actor: Mapping[str, object],
) -> dict[str, object] | None:
    """Read-only guard check proving an earlier autonomous_replan obligation was cleared."""
    from ...control_plane.quota.effective_action import EffectiveAction
    from .steward_commands import NativeStewardCommandError, native_command

    try:
        raw = native_command(
            host.controller,
            ["quota", "should-run", "--goal-id", str(actor["goal_id"]), "--agent-id", str(actor["agent_id"]), "--dry-run"],
        )
    except (NativeStewardCommandError, OSError, ValueError, RuntimeError):
        return None
    payload = as_mapping(raw)
    ref_file = payload.get("reference_file")
    if payload.get("effective_action") is None and isinstance(ref_file, str):
        try:
            ref = as_mapping(json.loads(Path(ref_file).read_text(encoding="utf-8")))
            target = Path(str(ref.get("path") or ref_file))
            loaded = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if isinstance(loaded, Mapping):
            payload = as_mapping(loaded)
    current: dict[str, object] = {
        "effective_action": payload.get("effective_action"),
        "replan_action_packet": payload.get("replan_action_packet"),
    }
    if current["effective_action"] != EffectiveAction.NORMAL_RUN.value or current["replan_action_packet"] is not None:
        return None
    return current


def report_selection_recovery(
    host: StewardTurnHostProtocol,
    original: Mapping[str, object],
    *,
    probe_turn: Mapping[str, object] | None = None,
) -> dict[str, object] | None:
    """The typed owner qualifies this observation before any new worker."""
    result = as_mapping(as_mapping(original.get("response")).get("native_loopx"))
    refusal = as_optional_dict(result.get("selection_rejection"))
    if (
        result.get("error_code") != "turn_todo_selection_deferred"
        or refusal is None
        or refusal.get("reason_code") != "autonomous_replan"
        or refusal.get("recovery_action") != "reenter_guard_without_selection"
    ):
        return None
    from ..periodic_report.private_generation import attempt_directory
    from .steward_reports import report_service

    request = as_mapping(original["loopx_request"])
    actor = as_mapping(request["actor"])
    reports, owner = report_service(host.controller, dict(actor))
    _, window = reports.current_window(owner)
    proof: dict[str, object] = {
        "original": original,
        "generation_present": window is None
        or window["window_id"] != request["report_window_id"]
        or (attempt_directory(reports.root, window) / "private-generation.json").exists(),
    }
    if probe_turn is not None:
        from ...control_plane.effect_runtime import source_number_tokens
        from ...control_plane.turn_driver.executor import inspect_loopx_turn_journal
        from ...control_plane.turn_driver.journal_store import load_turn_journal, turn_journal_path

        keys = {
            as_mapping(row.get("payload")).get("resume_turn_key")
            for row in host.store.events_after(str(actor["session_id"]), str(probe_turn["turn_id"]), None)
            if row.get("kind") == "native_loopx.replan_result"
        }
        if len(keys) != 1:
            return None
        key_obj = keys.pop()
        if not isinstance(key_obj, str):
            return None
        goal_id = str(actor["goal_id"])
        agent_id = str(actor["agent_id"])
        journal = as_optional_dict(load_turn_journal(turn_journal_path(reports.root, goal_id=goal_id, turn_key=key_obj)))
        if journal is None:
            return None
        current_guard: dict[str, object] | None = None
        journal_status = journal.get("status")
        if (
            journal_status == "failed"
            and journal.get("result_kind") == "validation_failed"
            and journal.get("validation_stage") in {"host_result_contract", "task_postcondition"}
        ):
            current_guard = host._cleared_replan_guard(actor)
            if current_guard is None:
                return None
        elif journal_status != "stopped":
            return None
        # Transport canonical observations to the shared typed admission owner.
        proof["probe"] = {
            "turn": probe_turn,
            "inspection": inspect_loopx_turn_journal(reports.root, goal_id=goal_id, agent_id=agent_id, turn_key=key_obj),
            "journal": journal,
            "source_number_tokens": source_number_tokens(as_mapping(journal.get("plan")).get("work_contract")),
            **({"current_guard": current_guard} if current_guard is not None else {}),
        }
    return proof


def report_launch_recovery(
    host: StewardTurnHostProtocol,
    original: Mapping[str, object],
) -> dict[str, object] | None:
    """Read the original typed launch refusal and its canonical transaction."""
    result = as_mapping(as_mapping(original.get("response")).get("native_loopx"))
    if as_mapping(result.get("host_failure")).get("kind") != "contract_rejected":
        return None
    from ...control_plane.turn_driver import inspect_loopx_turn_journal
    from ...control_plane.turn_driver.journal_store import (
        find_loopx_turn_key_by_settlement_identity,
        load_turn_journal,
        turn_journal_path,
    )
    from ..periodic_report.private_generation import attempt_directory
    from .steward_reports import StewardReports

    request = as_mapping(original["loopx_request"])
    actor = as_mapping(request["actor"])
    goal_id = str(actor["goal_id"])
    agent_id = str(actor["agent_id"])
    root = host.controller.coordination_runtime_root
    key = find_loopx_turn_key_by_settlement_identity(
        root,
        goal_id=goal_id,
        agent_id=agent_id,
        todo_id=as_optional_str(request.get("todo_id")),
        turn_instance_id=str(request["turn_instance_id"]),
    )
    if key is None:
        raise ValueError("the original launch rejection has no canonical Turn")
    inspection = inspect_loopx_turn_journal(root, goal_id=goal_id, agent_id=agent_id, turn_key=key, retry_failed=True)
    journal = as_optional_dict(load_turn_journal(turn_journal_path(root, goal_id=goal_id, turn_key=key)))
    _, window = StewardReports(host.controller).current_window(goal_id.removeprefix("steward-agent-"))
    return {
        "original": original,
        "inspection": inspection,
        "journal": journal,
        "turn_key": key,
        "host_attempt_count": (journal or {}).get("host_attempt_count"),
        "generation_present": window is None or (attempt_directory(root, window) / "private-generation.json").exists(),
    }


def _find_matching_editorial_candidate(
    events: Sequence[Mapping[str, object]],
    *,
    expected_digest: object,
) -> dict[str, object] | None:
    from ...control_plane.turn_driver.host_candidate import parse_model_json

    for row in reversed(events):
        if row.get("kind") != "answer.final":
            continue
        message = as_mapping(as_mapping(row.get("payload")).get("response")).get("message")
        parsed_raw = parse_model_json(str(message or ""))
        if not isinstance(parsed_raw, dict):
            continue
        parsed = as_dict(parsed_raw)
        if parsed.get("result_kind") not in {"validated_progress", "validated_completion"}:
            continue
        editorial = as_optional_dict(parsed.get("editorial_response"))
        if editorial is None or editorial.get("request_digest") != expected_digest:
            continue
        return parsed
    return None


def frozen_report_contract_recovery(
    host: StewardTurnHostProtocol,
    actor: Mapping[str, object],
    window: Mapping[str, object],
    original: Mapping[str, object],
    *,
    key: str,
    generation: Mapping[str, object],
    root: Path,
    normalize_report_candidate: Callable[[Mapping[str, object]], dict[str, object]],
) -> tuple[dict[str, object], dict[str, object]] | None:
    """Recover only an already-frozen report whose host candidate hit host_result_contract."""
    from ...control_plane.turn_driver.journal_store import load_turn_journal, turn_journal_path

    goal_id = str(actor["goal_id"])
    session_id = str(actor["session_id"])
    window_id = str(window["window_id"])
    journal = as_optional_dict(load_turn_journal(turn_journal_path(root, goal_id=goal_id, turn_key=key)))
    if (
        journal is None
        or journal.get("status") != "failed"
        or journal.get("result_kind") != "validation_failed"
        or journal.get("validation_stage") != "host_result_contract"
        or journal.get("host_attempt_count") != 1
    ):
        return None
    resumed = host.store.turn_for_client(session_id, native_steward_client_id(actor, "report." + window_id + ".admission-resume"))
    retry = host.store.turn_for_client(session_id, native_steward_client_id(actor, "report." + window_id + ".admission-retry"))
    seen_turn_ids: set[str] = set()
    candidates: list[Mapping[str, object]] = []
    for candidate_turn in (resumed, retry, original):
        if isinstance(candidate_turn, Mapping):
            tid = candidate_turn.get("turn_id")
            if isinstance(tid, str) and tid not in seen_turn_ids:
                seen_turn_ids.add(tid)
                candidates.append(candidate_turn)
    for candidate_turn in candidates:
        events = host.store.events_after(session_id, str(candidate_turn["turn_id"]), None)
        native_failed = any(
            row.get("kind") == "native_loopx.result"
            and isinstance(row.get("payload"), Mapping)
            and as_mapping(row["payload"]).get("resume_turn_key") == key
            and as_mapping(row["payload"]).get("status") == "failed"
            and as_mapping(row["payload"]).get("result_kind") == "validation_failed"
            and as_mapping(row["payload"]).get("validation_stage") == "host_result_contract"
            for row in events
        )
        if not native_failed:
            continue
        parsed = _find_matching_editorial_candidate(events, expected_digest=generation.get("request_digest"))
        if parsed is None:
            continue
        proof: dict[str, object] = {
            "turn_key": key,
            "result_kind": "validation_failed",
            "validation_stage": "host_result_contract",
            "host_attempt_count": 1,
            "generation_verified": True,
            "saved_candidate_verified": True,
        }
        return proof, normalize_report_candidate(parsed)
    return None


def stopped_report_candidate_recovery(
    host: StewardTurnHostProtocol,
    actor: Mapping[str, object],
    window: Mapping[str, object],
    original: Mapping[str, object],
    *,
    key: str,
    folder: Path,
    root: Path,
) -> tuple[dict[str, object], dict[str, object]] | None:
    """Recover a stopped report Turn only when its saved final answer already holds a valid candidate."""
    from ...control_plane.turn_driver.journal_store import load_turn_journal, turn_journal_path

    del window
    req_path = folder / "editorial-request.json"
    if not req_path.is_file() or not (folder / "source.json").is_file():
        return None
    if (folder / "editorial-response.json").exists() and not (folder / "private-generation.json").exists():
        return None
    try:
        request_digest = as_mapping(json.loads(req_path.read_text(encoding="utf-8"))).get("request_digest")
    except (OSError, ValueError):
        return None
    if not isinstance(request_digest, str) or not request_digest:
        return None
    journal = as_optional_dict(load_turn_journal(turn_journal_path(root, goal_id=str(actor["goal_id"]), turn_key=key)))
    if (
        journal is None
        or journal.get("status") != "stopped"
        or journal.get("result_kind") != "wait"
        or journal.get("host_attempt_count") != 1
        or as_mapping(journal.get("host_result")).get("classification") != "no_typed_host_result"
    ):
        return None
    events = host.store.events_after(str(actor["session_id"]), str(original["turn_id"]), None)
    stopped_event = any(
        row.get("kind") == "native_loopx.result"
        and isinstance(row.get("payload"), Mapping)
        and as_mapping(row["payload"]).get("resume_turn_key") == key
        and as_mapping(row["payload"]).get("status") == "stopped"
        and as_mapping(row["payload"]).get("result_kind") == "wait"
        for row in events
    )
    if not stopped_event:
        return None
    parsed = _find_matching_editorial_candidate(events, expected_digest=request_digest)
    if parsed is None:
        return None
    proof: dict[str, object] = {
        "turn_key": key,
        "result_kind": "wait",
        "classification": "no_typed_host_result",
        "host_attempt_count": 1,
        "generation_verified": True,
        "saved_candidate_verified": True,
    }
    return proof, dict(parsed)


def has_recoverable_stopped_report(
    host: StewardTurnHostProtocol,
    actor: Mapping[str, object],
    window: Mapping[str, object],
    todo: Mapping[str, object],
    *,
    report_turn_fn: Callable[[StewardStoreProtocol, Mapping[str, object], Mapping[str, object]], dict[str, object] | None],
) -> bool:
    """Read-only gate check before acquiring the recovery fence."""
    if todo.get("status") != "open":
        return False
    original = report_turn_fn(host.store, actor, window)
    if not original:
        return False
    if any(
        row.get("kind") == "native_loopx.result" and as_mapping(row.get("payload")).get("status") == "committed"
        for row in host.store.events_after(str(actor["session_id"]), str(original["turn_id"]), None)
    ):
        return False
    request = as_mapping(original.get("loopx_request"))
    turn_instance_id = request.get("turn_instance_id")
    if not isinstance(turn_instance_id, str) or not turn_instance_id:
        return False
    from ...control_plane.turn_driver.journal_store import find_loopx_turn_key_by_settlement_identity
    from ..periodic_report.private_generation import attempt_directory
    from .steward_reports import report_service

    reports, _ = report_service(host.controller, dict(actor))
    folder = attempt_directory(reports.root, dict(window))
    goal_id = str(actor["goal_id"])
    agent_id = str(actor["agent_id"])
    todo_id = str(todo["todo_id"])
    saved_key = find_loopx_turn_key_by_settlement_identity(
        reports.root,
        goal_id=goal_id,
        agent_id=agent_id,
        todo_id=todo_id,
        turn_instance_id=turn_instance_id + ".saved-candidate",
    )
    if saved_key is not None and (folder / "private-generation.json").exists():
        return True
    key = find_loopx_turn_key_by_settlement_identity(
        reports.root,
        goal_id=goal_id,
        agent_id=agent_id,
        todo_id=todo_id,
        turn_instance_id=turn_instance_id,
    )
    return bool(
        key is not None
        and host._stopped_report_candidate_recovery(
            actor,
            window,
            original,
            key=str(key),
            folder=folder,
            root=reports.root,
        )
        is not None
    )


def build_replay_runner(
    normalized_candidate: Mapping[str, object],
) -> Callable[[Mapping[str, object], object], dict[str, object]]:
    """Build a once-only host callback replaying an already-frozen report candidate."""
    from ...control_plane.turn_driver.host_candidate import build_result

    replayed = False

    def replay_frozen_candidate(native_request: Mapping[str, object], _launch_scope: object) -> dict[str, object]:
        nonlocal replayed
        if replayed:
            raise ValueError("frozen report candidate replay may execute at most once")
        replayed = True
        built = build_result(native_request, dict(normalized_candidate), host_name="Codex app-server", allow_completion=True)
        return as_dict(built)

    return replay_frozen_candidate


def settle_stopped_report_candidate(
    host: StewardTurnHostProtocol,
    *,
    actor: Mapping[str, object],
    window: Mapping[str, object],
    todo: Mapping[str, object],
    original: Mapping[str, object],
    request: dict[str, object],
    observed: dict[str, object] | None,
    reports: object,
    owner_key: str,
    folder: Path,
    original_key: str,
    stopped_proof: dict[str, object],
    raw_candidate: dict[str, object],
    normalize_report_candidate: Callable[[Mapping[str, object]], dict[str, object]],
) -> dict[str, object]:
    """Freeze and settle an already-generated stopped report candidate without invoking a model."""
    from ...cli_runtime import _build_selected_parser
    from ...cli_commands.turn import handle_turn_command
    from ...control_plane.turn_driver import inspect_loopx_turn_journal
    from ..periodic_report.private_generation import validate_private_generation

    typed_reports = cast(_ReportServiceProtocol, reports)
    goal_id = str(actor["goal_id"])
    agent_id = str(actor["agent_id"])
    inspection = as_dict(
        inspect_loopx_turn_journal(
            typed_reports.root,
            goal_id=goal_id,
            agent_id=agent_id,
            turn_key=original_key,
            retry_failed=True,
        )
    )
    admitted = host._admit(
        request,
        observed=observed,
        settlement_recovery=inspection,
        frozen_contract_recovery=stopped_proof,
    )
    if not (folder / "private-generation.json").exists():
        typed_reports.freeze(request, dict(raw_candidate))
    generation = as_mapping(validate_private_generation(folder))
    if as_mapping(raw_candidate.get("editorial_response")).get("request_digest") != generation.get("request_digest"):
        raise ValueError("the recovered candidate digest does not match the frozen report")
    if not typed_reports.delivery_current(owner_key, window):
        raise ValueError("the original report authorization changed before settlement recovery")
    saved_instance_id = str(request["turn_instance_id"]) + ".saved-candidate"
    argv = [
        "turn",
        "run-once",
        "--goal-id",
        goal_id,
        "--agent-id",
        agent_id,
        "--project",
        str(admitted["workspace_path"]),
        "--host",
        "generic-cli",
        "--turn-instance-id",
        saved_instance_id,
        "--todo-id",
        str(todo["todo_id"]),
        "--execute",
    ]
    if request.get("validation_command") is not None:
        argv += ["--validation-command-json", json.dumps(request["validation_command"])]
    args = _build_selected_parser("turn").parse_args(argv)
    args.host, args.session_scope = "codex-app-server", "agent"
    result: dict[str, object] = {}
    host_runner = host._build_replay_runner(normalize_report_candidate(raw_candidate))
    with host._task_execution_lease(request, str(original["turn_id"]), recovery=True) as lease:
        handle_turn_command(
            args,
            registry_path=cast(Path, host.controller.registry_path),
            runtime_root_arg=str(typed_reports.root),
            output_format=lambda *_a, **_kw: "json",
            print_payload=lambda value, *_a: result.update(as_mapping(value)),
            native_host_runner=host_runner,
            native_session_binding_resolver=lambda _envelope: {
                "schema_version": "loopx_turn_session_binding_v0",
                "goal_id": goal_id,
                "agent_id": agent_id,
            },
            native_task_validator=host._report_validator(request),
            native_completion_lease_resolver=lambda: host._task_lease_proof(request, lease),
        )
    return result


def recover_report_settlement(
    host: StewardTurnHostProtocol,
    actor: dict[str, object],
    window: dict[str, object],
    todo: dict[str, object],
    *,
    report_turn_fn: Callable[[StewardStoreProtocol, Mapping[str, object], Mapping[str, object]], dict[str, object] | None],
) -> None:
    """Resume only cached, validated native effects of the original report."""
    from ...cli_runtime import _build_selected_parser
    from ...cli_commands.turn import handle_turn_command
    from ...control_plane.turn_driver import inspect_loopx_turn_journal
    from ...control_plane.turn_driver.journal_store import find_loopx_turn_key_by_settlement_identity
    from ..periodic_report.private_generation import attempt_directory, validate_private_generation
    from .steward_reports import report_service

    observed = host._observe(actor)
    session_id = str(actor["session_id"])
    goal_id = str(actor["goal_id"])
    agent_id = str(actor["agent_id"])
    with host.controller.lock, host._report_fence({"actor": actor}):
        original = report_turn_fn(host.store, actor, window)
        if not original:
            return
        original_turn_id = str(original["turn_id"])
        request = as_dict(original.get("loopx_request"))
        if (
            request.get("actor") != actor
            or request.get("report_window_id") != window["window_id"]
            or request.get("todo_id") != todo["todo_id"]
        ):
            raise ValueError("the original report settlement identity changed")
        if any(
            row.get("kind") == "native_loopx.result" and as_mapping(row.get("payload")).get("status") == "committed"
            for row in host.store.events_after(session_id, original_turn_id, None)
        ):
            return
        reports, owner_key = report_service(host.controller, actor)
        folder = attempt_directory(reports.root, window)
        turn_instance_id = str(request["turn_instance_id"])
        saved_key = find_loopx_turn_key_by_settlement_identity(
            reports.root,
            goal_id=goal_id,
            agent_id=agent_id,
            todo_id=str(todo["todo_id"]),
            turn_instance_id=turn_instance_id + ".saved-candidate",
        )
        key = saved_key or find_loopx_turn_key_by_settlement_identity(
            reports.root,
            goal_id=goal_id,
            agent_id=agent_id,
            todo_id=str(todo["todo_id"]),
            turn_instance_id=turn_instance_id,
        )
        if key is None:
            return
        if saved_key is None:
            stopped_recovery = host._stopped_report_candidate_recovery(
                actor,
                window,
                original,
                key=str(key),
                folder=folder,
                root=reports.root,
            )
            if stopped_recovery is not None:
                stopped_result = host._settle_stopped_report_candidate(
                    actor=actor,
                    window=window,
                    todo=todo,
                    original=original,
                    request=request,
                    observed=observed,
                    reports=reports,
                    owner_key=str(owner_key),
                    folder=folder,
                    original_key=str(key),
                    stopped_proof=stopped_recovery[0],
                    raw_candidate=stopped_recovery[1],
                )
                host.store.append_event(session_id, original_turn_id, kind="native_loopx.result", payload=stopped_result)
                return
        generation = as_mapping(validate_private_generation(folder))
        if not reports.delivery_current(owner_key, window):
            raise ValueError("the original report authorization changed before settlement recovery")
        inspection = as_dict(
            inspect_loopx_turn_journal(reports.root, goal_id=goal_id, agent_id=agent_id, turn_key=str(key), retry_failed=True)
        )
        contract_recovery = (
            host._frozen_report_contract_recovery(
                actor,
                window,
                original,
                key=str(key),
                generation=generation,
                root=reports.root,
            )
            if as_mapping(inspection.get("recovery_decision")).get("reinvoke_host") is True
            else None
        )
        admitted = host._admit(
            request,
            observed=observed,
            settlement_recovery=inspection,
            frozen_contract_recovery=contract_recovery[0] if contract_recovery else None,
        )

        def no_model(*_args: object) -> dict[str, object]:
            raise ValueError("report settlement recovery must never invoke a model")

        host_runner: Callable[[Mapping[str, object], object], dict[str, object]] = (
            host._build_replay_runner(contract_recovery[1]) if contract_recovery is not None else no_model
        )
        args = _build_selected_parser("turn").parse_args(
            [
                "turn",
                "run-once",
                "--goal-id",
                goal_id,
                "--agent-id",
                agent_id,
                "--project",
                str(admitted["workspace_path"]),
                "--host",
                "generic-cli",
                "--resume-turn-key",
                str(key),
                "--retry-failed-turn",
                "--execute",
            ]
        )
        args.host, args.session_scope = "codex-app-server", "agent"
        result: dict[str, object] = {}
        with (
            host._task_execution_lease(request, original_turn_id, recovery=True)
            if todo.get("status") == "open"
            else nullcontext(None)
        ) as lease:
            handle_turn_command(
                args,
                registry_path=cast(Path, host.controller.registry_path),
                runtime_root_arg=str(reports.root),
                output_format=lambda *_a, **_kw: "json",
                print_payload=lambda value, *_a: result.update(as_mapping(value)),
                native_host_runner=host_runner,
                native_session_binding_resolver=lambda _envelope: {
                    "schema_version": "loopx_turn_session_binding_v0",
                    "goal_id": goal_id,
                    "agent_id": agent_id,
                },
                native_task_validator=host._report_validator(request),
                native_completion_lease_resolver=(
                    (lambda: host._task_lease_proof(request, lease)) if lease else None
                ),
            )
        # Retain the historical outer failure. The actual native owner
        # supplies the new event; no status or publication proof is forged.
        host.store.append_event(session_id, original_turn_id, kind="native_loopx.result", payload=result)
