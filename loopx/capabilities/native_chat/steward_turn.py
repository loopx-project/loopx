"""Original-Session app-server transport for the existing governed Turn driver.

No scheduler, task store or message consumer is created here. The caller
selects native work; the existing Turn command owns fresh admission, validation,
writeback, quota and scheduler handoff. Provider metadata uses the existing
read-only native control client. Chat retains its own execution fence.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import contextmanager, ExitStack, nullcontext
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shlex
import time
from typing import Any

from ...control_plane.effect_runtime import EffectRuntimeRejected
from ...control_plane.goals.activation import goal_is_stopped
from ...control_plane.goals.first_party_host_admission import FirstPartyHostRuntimeRejected
from ...file_lock import exclusive_file_lock
from .steward_managed_mode import ManagedSessionTurn, ManagedTaskValidationUnavailable, StewardManagedMode

MONITOR_RECOVERY_RETRY_COOLDOWN_SECONDS = 300
_WORKER_EXITED_ERROR = (
    "The LoopX Chat execution worker exited before this turn finished. "
    "Your conversation is saved; execution may have occurred. This turn was not retried automatically."
)
_POST_CONTEXT_WATCH_ERRORS = frozenset({
    "steward monitor external signal source temporarily unavailable",
    "disk I/O error",
    _WORKER_EXITED_ERROR,
})
_USER_ACTION_DEDUPE_KEY = re.compile(r"^[a-z0-9][a-z0-9._:@/-]{0,119}$")
_REPORT_VALIDATOR_PREFIX = (
    "from pathlib import Path; from loopx.capabilities.periodic_report.private_generation import validate_private_generation; "
    "validate_private_generation(Path("
)


def _iso_timestamp_seconds(value: Any) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _same_report_validator_command(frozen_cmd: Any, request_cmd: Any) -> bool:
    """Allow report validation commands whose only drift is the host Python path."""
    return (isinstance(frozen_cmd, list) and isinstance(request_cmd, list)
        and len(frozen_cmd) == 3 and len(request_cmd) == 3
        and all(isinstance(part, str) and bool(part) for part in [*frozen_cmd, *request_cmd])
        and frozen_cmd[1] == "-c" and request_cmd[1] == "-c"
        and frozen_cmd[2] == request_cmd[2] and frozen_cmd[2].startswith(_REPORT_VALIDATOR_PREFIX)
        and frozen_cmd[2].endswith("))"))


def native_steward_client_id(actor: Mapping[str, Any], turn_instance_id: str) -> str:
    """The original native request identity, shared by dispatch and readback."""
    if actor.get("schema_version") == "loopx_managed_session_actor_v0":
        return "managed." + turn_instance_id
    return "native." + hashlib.sha256(json.dumps([actor, turn_instance_id], sort_keys=True).encode()).hexdigest()[:40]


def report_turn(store: Any, actor: Mapping[str, Any], window: Mapping[str, Any]) -> dict[str, Any] | None:
    """Read the one bounded continuation, retaining the original failed Turn."""
    operation = "report." + window["window_id"]
    original = store.turn_for_client(actor["session_id"], native_steward_client_id(actor, operation))
    continued = store.turn_for_client(actor["session_id"], native_steward_client_id(actor, operation + ".quota-recovery"))
    if continued is not None:
        request = continued.get("loopx_request") or {}
        if (not original or (request.get("report_quota_recovery") or {}).get("original_turn_id") != original["turn_id"]
                or request.get("turn_instance_id") != original["loopx_request"]["turn_instance_id"] + ".quota-recovery"):
            raise ValueError("the report quota continuation changed its original identity")
        return continued
    return original


def _normalize_report_kernel_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize a verified report candidate to canonical Turn settlement fields."""
    normalized = dict(candidate)
    normalized.pop("editorial_response", None)
    # This finite task's postcondition is the complete validated generation, not
    # the model's choice of progress wording or profile-minimum shorthand.
    normalized["result_kind"] = "validated_completion"
    if normalized.get("delivery_batch_scale") == "multi_surface_or_implementation":
        normalized["delivery_batch_scale"] = "multi_surface"
    return normalized


def _split_evidence_text_chunks(text: str, max_item_chars: int) -> list[str]:
    """Split a single normalized evidence string cleanly at natural boundaries."""
    if len(text) <= max_item_chars:
        return [text]
    chunks: list[str] = []
    rest = text
    delimiters = ("; ", ". ", "；", "。", ", ", "，", " ")
    while len(rest) > max_item_chars:
        split_at = -1
        matched_delim = ""
        for delim in delimiters:
            pos = rest.rfind(delim, 1, max_item_chars)
            if pos > 0:
                split_at = pos
                matched_delim = delim
                break
        if split_at <= 0:
            chunks.append(rest[:max_item_chars])
            rest = rest[max_item_chars:].lstrip()
        elif matched_delim in ("；", "。", "，"):
            end = min(split_at + len(matched_delim), max_item_chars)
            chunks.append(rest[:end].strip())
            rest = rest[end:].lstrip()
        else:
            head = rest[:split_at].rstrip()
            if matched_delim == ". " and len(head) + 1 <= max_item_chars:
                head += "."
            chunks.append(head)
            rest = rest[split_at + len(matched_delim) :].lstrip()
    if rest:
        chunks.append(rest)
    return [chunk for chunk in chunks if chunk]


def _append_bounded_ellipsis(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if limit <= 3:
        return text[:limit]
    if len(text) + 3 <= limit and not text.endswith("..."):
        return text + "..."
    trimmed = text[: max(1, limit - 3)].rstrip()
    return (trimmed or text[: max(1, limit - 3)]) + "..."


def _split_bounded_evidence_items(
    raw: Any,
    *,
    max_items: int = 8,
    max_item_chars: int = 240,
    max_total_chars: int = 1_200,
) -> Any:
    """Split over-long Steward evidence strings without masking invalid shapes."""
    if not isinstance(raw, list) or not raw:
        return raw
    if not all(isinstance(item, str) and item.strip() for item in raw):
        return raw
    chunks: list[str] = []
    for item in raw:
        normalized = " ".join(item.split()).strip()
        chunks.extend(_split_evidence_text_chunks(normalized, max_item_chars))
    if len(chunks) > max_items:
        merged: list[str] = []
        for chunk in chunks:
            if merged and len(merged[-1]) + 2 + len(chunk) <= max_item_chars:
                merged[-1] = f"{merged[-1]}; {chunk}"
            else:
                merged.append(chunk)
        chunks = merged
    clipped = len(chunks) > max_items
    if clipped:
        chunks = chunks[:max_items]
    bounded: list[str] = []
    used = 0
    for idx, chunk in enumerate(chunks):
        remaining = max_total_chars - used
        if remaining <= 0:
            clipped = True
            break
        if len(chunk) > remaining or len(chunk) > max_item_chars:
            cap = min(max_item_chars, remaining)
            bounded.append(_append_bounded_ellipsis(chunk, cap))
            used += len(bounded[-1])
            clipped = True
            break
        bounded.append(chunk)
        used += len(chunk)
    if clipped and bounded and not bounded[-1].endswith("..."):
        room = min(max_item_chars, len(bounded[-1]) + (max_total_chars - used))
        bounded[-1] = _append_bounded_ellipsis(bounded[-1], room)
    return bounded


def _normalize_steward_advancement_kernel_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Keep Steward advancement evidence within kernel per-item and total limits."""
    normalized = dict(candidate)
    if normalized.get("delivery_batch_scale") == "multi_surface_or_implementation":
        normalized["delivery_batch_scale"] = "multi_surface"
    for field in ("work_evidence", "validation_evidence"):
        if field in normalized:
            normalized[field] = _split_bounded_evidence_items(normalized[field])
    return normalized


def _is_zero_effect_replan_selection_deferral(result: Mapping[str, Any]) -> bool:
    """Identify a zero-effect pre-host autonomous_replan selection refusal."""
    if result.get("ok") is not False or result.get("error_code") != "turn_todo_selection_deferred":
        return False
    refusal = result.get("selection_rejection")
    if (not isinstance(refusal, Mapping) or refusal.get("state") != "deferred"
            or refusal.get("reason_code") != "autonomous_replan"
            or refusal.get("recovery_action") != "reenter_guard_without_selection"):
        return False
    effects = result.get("effects")
    if not isinstance(effects, Mapping):
        return False
    return all(
        effects.get(key) is False
        for key in ("host_invoked", "state_written", "quota_spent", "scheduler_acknowledged")
    )


class StewardTurnHost:
    def __init__(self, controller: Any) -> None:
        self.controller = controller
        self.store = controller.store
        self.bindings = controller.project_contexts.conversation_bindings
        self.managed = StewardManagedMode(controller, self.bindings)

    def _monitor_selection_failed_before_poll(self, turn: dict[str, Any]) -> bool:
        # The earlier adapter lost the command's structured rejection at the
        # Chat boundary. Recognize only this exact pre-poll failure, never an
        # unknown execution/commit failure or a report/business Turn.
        expected = ("the native steward operation was not committed: quota_action_selection_rejected: "
            "explicit action selection is not currently eligible: auxiliary_monitor_not_selectable_in_advancement_lane")
        return (turn.get("status") == "failed" and turn.get("error_code") == "runtime_error"
            and turn.get("error") == expected and turn.get("delta_count") == 0
            and turn.get("upstream_turn_id") is None and turn.get("response") is None
            and not any(row["kind"] == "native_loopx.monitor_poll"
                for row in self.store.events_after(turn["session_id"], turn["turn_id"], None)))

    def _observe(self, actor: Mapping[str, Any]) -> dict[str, Any] | None:
        """Verify provider identity before entering publication/effect fences."""
        if actor.get("schema_version") == "loopx_managed_session_actor_v0":
            return None
        if self.bindings is None:
            raise ValueError("the original steward binding is unavailable")
        session = self.store.load_session(actor["session_id"])
        if not session or not session.get("steward_context"):
            raise ValueError("the original steward Session is unavailable")
        saved = session["steward_context"]
        return self.bindings.resolve(binding_id=saved["binding_id"], session=session,
            include_observation=True, origin="web", source_ref=saved["source_ref"],
            sender_ref=saved["operator_ref"], private_human_message=False, session_context=saved)

    def _admit(self, request: dict[str, Any], *, turn: dict[str, Any] | None = None,
               observed: dict[str, Any] | None = None,
               prepare_context: bool = False, settlement_recovery: dict[str, Any] | None = None,
               frozen_contract_recovery: dict[str, Any] | None = None,
               launch_recovery: dict[str, Any] | None = None,
               selection_recovery: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.controller.closed.is_set():
            raise ValueError("the original Chat host is stopped")
        actor = request["actor"]
        if actor.get("schema_version") == "loopx_managed_session_actor_v0":
            return ManagedSessionTurn(self.controller).admit(request, turn=turn,
                settlement_recovery=settlement_recovery,
                frozen_contract_recovery=frozen_contract_recovery,
                selection_recovery=selection_recovery)
        if self.bindings is None:
            raise ValueError("the original steward binding is unavailable")
        observed = observed if observed is not None else self._observe(actor)
        session = self.store.load_session(actor["session_id"])
        if not session or not session.get("steward_context"):
            raise ValueError("the original steward Session is unavailable")
        saved = session["steward_context"]
        current = self.bindings.read()
        binding = next((row for row in current["bindings"] if row["binding_id"] == saved["binding_id"]), None)
        if binding != observed["binding"]:
            raise ValueError("the original steward authorization changed during identity verification")
        # Identity IO may have waited.

        selected = {**self.bindings._resolve_observed(current, binding, observed["observation"], {
            "source_ref": saved["source_ref"], "sender_ref": saved["operator_ref"],
            "private_human_message": False, "origin": "web", "session_context": saved,
            "session": self.bindings._session_observation(session)}), "observation": observed["observation"]}
        from ...agent_registry import load_goal_from_registry
        goal = load_goal_from_registry(self.controller.registry_path, actor["goal_id"])
        from .steward_monitor import StewardMonitor
        canonical_actor = selected["binding"].get("managed_actor")
        monitor = (next((row for row in StewardMonitor(self.controller).todos(canonical_actor)
                         if row["todo_id"] == request.get("todo_id") and row.get("task_class") == "continuous_monitor"), None)
                   if canonical_actor and request.get("work_kind") == "monitor" and request.get("todo_id") else None)
        report = {}
        if request.get("work_kind") == "report":
            from .steward_reports import StewardReports
            subscription, window = StewardReports(self.controller).current_window(saved["binding_id"])
            todos = StewardMonitor(self.controller).todos(canonical_actor) if canonical_actor else []
            report = {"report_window": window, "report_subscription": subscription,
                "report_todo": next((row for row in todos if row["todo_id"] == request.get("todo_id")), None)}
        scope = None
        if turn is not None:
            from ...chat_manager_context import manager_authorization_scope_id_for_registry
            goal_ids = self.bindings.steward_scope(session)
            if goal_ids is None:
                raise ValueError("the original portfolio scope is unavailable")
            def scope_id(ids):
                return manager_authorization_scope_id_for_registry(self.controller.registry_path, ids,
                    runtime_root=self.controller.coordination_runtime_root, channel_id=session["channel_id"])
            scope = {"current": scope_id(goal_ids),
                "without_actor": scope_id([value for value in goal_ids if value != actor["goal_id"]])}
        admitted = self.bindings._core("collaboration.steward.managed_turn.admit", {
            "binding": selected["binding"], "session": session, "observation": selected["observation"],
            "workspace_path": str(self.managed._workspace(session)),
            "queued_turn_count": len(self.store.queued_turns(session["session_id"])),
            "goal": self.managed._goal(actor["goal_id"]), "goal_active": bool(goal and goal.get("status") == "active" and not goal_is_stopped(goal)),
            "request": request, "turn": turn, "scope": scope, "monitor_todo": monitor,
            **({"report_settlement_recovery": settlement_recovery} if settlement_recovery is not None else {}),
            **({"report_frozen_contract_recovery": frozen_contract_recovery} if frozen_contract_recovery is not None else {}),
            **({"report_launch_recovery": launch_recovery} if launch_recovery is not None else {}),
            **({"report_selection_recovery": selection_recovery} if selection_recovery is not None else {}),
            **self.report_quota_basis(request),
            **self.monitor_restart_basis(request),
            **({"preparing_model_context": True} if prepare_context else {}), **report})
        if admitted["refresh_original_scope"]:
            self.store.update_session(session["session_id"], manager_authorization_scope_id=scope["current"])
        return {**admitted, "observed_scope_id": scope["current"] if scope else None}

    def monitor_restart_basis(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Read original worker/journals without dispatching or repairing effects.

        Any pending, committed, unreadable or legacy poll evidence withholds a
        new carrier. The shared typed admission owner interprets these facts.
        """
        recovery = request.get("monitor_restart_recovery")
        if recovery is None:
            return {}
        from ...chat_store import _read_jsonl
        from ...control_plane.quota.monitor_poll import _monitor_poll_effect_id, _monitor_poll_receipt_path
        from .steward_commands import native_command
        actor = request["actor"]
        original = self.store.load_turn(actor["session_id"], recovery["original_turn_id"])
        if original is None:
            raise ValueError("the original stopped watch is unavailable")
        root = self.controller.coordination_runtime_root
        with exclusive_file_lock(self.store.turn_execution_lock_path(actor["session_id"], original["turn_id"]),
                timeout_seconds=0, operation="observe_stopped_native_watch"):
            # Strict reads are essential: an unreadable/compacted journal is
            # unknown, never an empty observation or proof that work did not run.
            events = _read_jsonl(self.store._event_path(actor["session_id"], original["turn_id"]),
                raise_on_error=True, strict=True)
            rows = _read_jsonl(root / "goals" / actor["goal_id"] / "runs" / "index.jsonl",
                raise_on_error=True, strict=True)
            present = any(row.get("classification") == "quota_monitor_poll"
                and row.get("turn_instance_id") == request["turn_instance_id"] for row in rows)
            from .steward_monitor import TARGET, StewardMonitor
            target = ("managed-session-watch." + actor["session_id"]
                if actor.get("schema_version") == "loopx_managed_session_actor_v0" else TARGET)
            if actor.get("schema_version") != "loopx_managed_session_actor_v0":
                matched = next((row for row in StewardMonitor(self.controller).todos(actor)
                                if row.get("todo_id") == request.get("todo_id")), None)
                if matched and matched.get("target_key"):
                    target = matched["target_key"]
            for todo, key in [(request["todo_id"], target), (None, target), (None, None)]:
                effect = _monitor_poll_effect_id(runtime_root=root, goal_id=actor["goal_id"],
                    agent_id=actor["agent_id"], turn_instance_id=request["turn_instance_id"],
                    todo_id=todo, target_key=key, goal_ref=None)
                present = present or _monitor_poll_receipt_path(root, actor["goal_id"], effect).exists()
            lease = native_command(self.controller, ["task-lease", "inspect", "--goal-id", actor["goal_id"],
                "--todo-id", request["todo_id"]])
            held = lease.get("lease") or {}
            available = lease.get("active") is False or (held.get("owner") == actor["agent_id"]
                and held.get("idempotency_key") == "monitor-recovery." + original["turn_id"])
            return {"monitor_restart_basis": {"original": original,
                "event_kinds": [row["kind"] for row in events], "poll_absent": not present,
                "contexts": [row["payload"] for row in events if row["kind"] == "manager.context"],
                "phases": [row["payload"] for row in events if row["kind"] == "agent.phase"],
                "old_worker_excluded": True, "lease_available": available,
                "observed_at": time.time()}}

    @staticmethod
    def monitor_restart_request(original: Mapping[str, Any], *, now: float | None = None) -> dict[str, Any] | None:
        """Prepare correlation only; current typed admission decides recovery."""
        prior = original.get("loopx_request") or {}
        error = original.get("error_code")
        if (original.get("status") != "failed"
                or error not in {"server_restarted", "runtime_error", "runtime_unavailable"}
                or prior.get("work_kind") != "monitor"):
            return None
        if prior.get("monitor_restart_recovery") is not None and error != "server_restarted":
            post_context_error = error == "runtime_unavailable" or (
                error == "runtime_error" and original.get("error") in _POST_CONTEXT_WATCH_ERRORS
            )
            completed_ts = _iso_timestamp_seconds(original.get("completed_at"))
            current_ts = time.time() if now is None else now
            if (not post_context_error or completed_ts is None
                    or current_ts - completed_ts < MONITOR_RECOVERY_RETRY_COOLDOWN_SECONDS):
                return None
        return {**prior, "monitor_restart_recovery": {"original_turn_id": original["turn_id"]}}

    def monitor_restart_carrier(self, original: dict[str, Any], client_id: str) -> dict[str, Any] | None:
        """Find the latest carrier for the same observation; typed admission proves a safe restart."""
        request = self.monitor_restart_request(original)
        if request is None:
            return None
        base = client_id + ".restart-recovery"
        next_client_id = base
        while (carrier := self.store.turn_for_client(original["session_id"], next_client_id)) is not None:
            if ((carrier.get("loopx_request") or {}).get("monitor_restart_recovery")
                    != request["monitor_restart_recovery"]):
                raise ValueError("native watch continuation identity conflict")
            request = self.monitor_restart_request(carrier)
            if request is None:
                return {"turn": carrier}
            next_client_id = base + "." + carrier["turn_id"]
        return {"request": request, "client_id": next_client_id}

    def report_quota_basis(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Transport original journal observations; the typed owner admits work."""
        recovery = request.get("report_quota_recovery")
        if recovery is None:
            return {}
        from ...control_plane.turn_driver import inspect_loopx_turn_journal
        from ..periodic_report.private_generation import attempt_directory
        from .steward_reports import report_service
        actor = request["actor"]
        original = self.store.load_turn(actor["session_id"], recovery["original_turn_id"])
        result = ((original or {}).get("response") or {}).get("native_loopx") or {}
        reports, owner = report_service(self.controller, actor)
        _, window = reports.current_window(owner)
        folder = attempt_directory(reports.root, window) if window else None
        return {"report_quota_basis": {"original": original,
            "inspection": inspect_loopx_turn_journal(reports.root, goal_id=actor["goal_id"],
                agent_id=actor["agent_id"], turn_key=result["resume_turn_key"]),
            "generation_present": folder is None or any((folder / name).exists() for name in
                ("editorial-response.json", "private-generation.json", "private-delivery.json")),
            "observed_at": time.time()}}

    def report_failure_observation(self, original: Mapping[str, Any]) -> dict[str, Any] | None:
        """Read the original native failure without starting or resuming work."""
        result = (original.get("response") or {}).get("native_loopx") or {}
        request = original.get("loopx_request") or {}
        if (original.get("status") != "completed" or request.get("work_kind") != "report"
                or result.get("result_kind") != "host_failure" or not original.get("upstream_turn_id")):
            return None
        from .codex_adapter import CodexAppServerAdapter
        from .codex_context import checked_session_home
        session = self.store.load_session(original["session_id"])
        if not session or not session.get("codex_home") or not session.get("upstream_thread_id"):
            return None
        history = session.get("upstream_context_history") or []
        if not isinstance(history, list) or any(not isinstance(item, dict)
                or not isinstance(item.get("thread_id"), str) or not item["thread_id"]
                for item in history):
            return None
        # Context replacement retains native provenance on this same Session.
        # Read the original refusal there; never resume a historical thread.
        threads = [session["upstream_thread_id"], *(item["thread_id"] for item in reversed(history))]
        proof = CodexAppServerAdapter.failed_request_observation(codex_bin=self.controller.codex_bin,
            codex_home=Path(session["codex_home"]),
            model_auth_home=checked_session_home(self.controller.codex_home, session),
            thread_ids=list(dict.fromkeys(threads)), turn_id=original["upstream_turn_id"])
        if (not proof or proof.get("turn_id") != original["upstream_turn_id"]
                or proof.get("thread_id") not in threads or proof.get("only_original_user_item") is not True
                or not proof.get("codex_error_info")):
            return None
        return proof

    def report_quota_request(self, original: Mapping[str, Any]) -> dict[str, Any] | None:
        """Observe quota recovery, never resume or rewrite the failed native Turn."""
        request = original.get("loopx_request") or {}
        if request.get("report_quota_recovery") is not None:
            return None
        proof = self.report_failure_observation(original)
        if (not proof or proof.get("only_original_user_item") is not True
                or proof.get("codex_error_info") != "usageLimitExceeded" or proof.get("quota_available") is not True):
            return None
        return {**request, "turn_instance_id": request["turn_instance_id"] + ".quota-recovery",
            "report_quota_recovery": {"original_turn_id": original["turn_id"], "native_observation": proof}}

    def report_selection_recovery(self, original: dict[str, Any], *,
                                  probe_turn: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """The typed owner qualifies this observation before any new worker."""
        result = (original.get("response") or {}).get("native_loopx") or {}
        refusal = result.get("selection_rejection")
        if (result.get("error_code") != "turn_todo_selection_deferred" or not isinstance(refusal, Mapping)
                or refusal.get("reason_code") != "autonomous_replan"
                or refusal.get("recovery_action") != "reenter_guard_without_selection"):
            return None
        from ..periodic_report.private_generation import attempt_directory
        from .steward_reports import report_service
        request = original["loopx_request"]
        reports, owner = report_service(self.controller, request["actor"])
        _, window = reports.current_window(owner)
        proof = {"original": original, "generation_present": window is None
            or window["window_id"] != request["report_window_id"]
            or (attempt_directory(reports.root, window) / "private-generation.json").exists()}
        if probe_turn is not None:
            from ...control_plane.turn_driver.executor import inspect_loopx_turn_journal
            from ...control_plane.turn_driver.journal_store import (
                load_turn_journal, turn_journal_path,
            )
            actor = request["actor"]
            keys = {row["payload"].get("resume_turn_key")
                for row in self.store.events_after(actor["session_id"], probe_turn["turn_id"], None)
                if row.get("kind") == "native_loopx.replan_result"}
            if len(keys) != 1 or not isinstance(next(iter(keys)), str):
                return None
            key = keys.pop()
            from ...control_plane.effect_runtime import source_number_tokens

            journal = load_turn_journal(turn_journal_path(reports.root, goal_id=actor["goal_id"], turn_key=key))
            current_guard = None
            if (journal.get("status") == "failed"
                    and journal.get("result_kind") == "validation_failed"
                    and journal.get("validation_stage") == "host_result_contract"):
                current_guard = self._cleared_replan_guard(actor)
                if current_guard is None:
                    return None
            # Transport canonical observations to the shared typed admission
            # owner.
            proof["probe"] = {"turn": probe_turn,
                "inspection": inspect_loopx_turn_journal(reports.root, goal_id=actor["goal_id"],
                    agent_id=actor["agent_id"], turn_key=key),
                "journal": journal,
                "source_number_tokens": source_number_tokens((journal.get("plan") or {}).get("work_contract")),
                **({"current_guard": current_guard} if current_guard is not None else {})}
        return proof

    def _cleared_replan_guard(self, actor: Mapping[str, Any]) -> dict[str, Any] | None:
        """Read-only guard check proving an earlier autonomous_replan obligation was cleared."""
        from ...control_plane.quota.effective_action import EffectiveAction
        from .steward_commands import NativeStewardCommandError, native_command
        try:
            raw = native_command(self.controller, [
                "quota", "should-run", "--goal-id", actor["goal_id"], "--agent-id", actor["agent_id"], "--dry-run",
            ])
        except (NativeStewardCommandError, OSError, ValueError, RuntimeError):
            return None
        payload: Mapping[str, Any] = raw
        if payload.get("effective_action") is None and isinstance(payload.get("reference_file"), str):
            try:
                ref = json.loads(Path(payload["reference_file"]).read_text(encoding="utf-8"))
                target = Path(str(ref.get("path") or payload["reference_file"]))
                loaded = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
            if isinstance(loaded, Mapping):
                payload = loaded
        current = {
            "effective_action": payload.get("effective_action"),
            "replan_action_packet": payload.get("replan_action_packet"),
        }
        if (current["effective_action"] != EffectiveAction.NORMAL_RUN.value
                or current["replan_action_packet"] is not None):
            return None
        return current

    def _report_launch_recovery(self, original: dict[str, Any]) -> dict[str, Any] | None:
        """Read the original typed launch refusal and its canonical transaction.

        Missing model events alone prove nothing. Only the native launch
        owner's specific pre-publication rejection can qualify this path;
        the typed conversation owner checks it with the current journal.
        """
        result = (original.get("response") or {}).get("native_loopx") or {}
        if (result.get("host_failure") or {}).get("kind") != "contract_rejected":
            return None
        from ...control_plane.turn_driver import inspect_loopx_turn_journal
        from ...control_plane.turn_driver.journal_store import (
            find_loopx_turn_key_by_settlement_identity, load_turn_journal, turn_journal_path,
        )
        from ..periodic_report.private_generation import attempt_directory
        from .steward_reports import StewardReports
        request = original["loopx_request"]
        actor = request["actor"]
        root = self.controller.coordination_runtime_root
        key = find_loopx_turn_key_by_settlement_identity(root, goal_id=actor["goal_id"],
            agent_id=actor["agent_id"], todo_id=request["todo_id"], turn_instance_id=request["turn_instance_id"])
        if key is None:
            raise ValueError("the original launch rejection has no canonical Turn")
        inspection = inspect_loopx_turn_journal(root, goal_id=actor["goal_id"],
            agent_id=actor["agent_id"], turn_key=key, retry_failed=True)
        journal = load_turn_journal(turn_journal_path(root, goal_id=actor["goal_id"], turn_key=key))
        _, window = StewardReports(self.controller).current_window(actor["goal_id"].removeprefix("steward-agent-"))
        return {"original": original, "inspection": inspection, "journal": journal, "turn_key": key,
            "host_attempt_count": (journal or {}).get("host_attempt_count"),
            "generation_present": window is None or (attempt_directory(root, window) / "private-generation.json").exists()}

    def _report_validator(self, request: Mapping[str, Any]):
        """Validate with the owning installed package, outside model execution.

        An embedded host may import LoopX through its bootstrap rather than the
        Python interpreter's site-packages. Its bare child interpreter is not
        an independent installation of the same package.
        """
        def validate(_plan: Mapping[str, Any], _result: Mapping[str, Any]) -> dict[str, Any]:
            from ..periodic_report.private_generation import attempt_directory, validate_private_generation
            from .steward_reports import report_service
            reports, owner_key = report_service(self.controller, request["actor"])
            try:
                subscription, window = reports.current_window(owner_key)
                if (not subscription or not window or window["window_id"] != request["report_window_id"]
                        or window["subscription_revision"] != request["report_subscription_revision"]):
                    raise ValueError("the original report window changed")
                validate_private_generation(attempt_directory(reports.root, window))
                if not reports.delivery_current(owner_key, window):
                    raise ValueError("the original report authorization changed")
            except (OSError, ValueError, RuntimeError):
                return {"status": "failed", "validator_kind": "callback",
                    "summary": "independent original report validation failed", "recovery_kind": "repair_required"}
            return {"status": "passed", "validator_kind": "callback",
                "summary": "independent original report validation passed"}
        return validate

    def _report_fence(self, request: Mapping[str, Any]):
        if request["actor"].get("schema_version") == "loopx_managed_session_actor_v0":
            from ...attached_session import managed_session_goal_commit
            return managed_session_goal_commit(store=self.store, registry_path=self.controller.registry_path,
                session_id=request["actor"]["session_id"])()
        return exclusive_file_lock(self.bindings.path, operation="original_report_effect")

    def _task_lease_proof(self, request: Mapping[str, Any], lease: Mapping[str, Any] | None) -> dict[str, Any]:
        from .steward_commands import native_command
        observed = native_command(self.controller, ["task-lease", "inspect", "--goal-id", request["actor"]["goal_id"],
            "--todo-id", request["todo_id"]]).get("lease")
        if (not lease or not observed or observed.get("status") != "active"
                or any(observed.get(key) != lease.get(key) for key in
                    ("owner", "idempotency_key", "lease_epoch", "version"))):
            raise ValueError("the original task execution lease changed")
        return {"task_lease_idempotency_key": observed["idempotency_key"],
            "task_lease_expected_version": observed["version"]}

    @contextmanager
    def _task_execution_lease(self, request: Mapping[str, Any], turn_id: str, *, recovery: bool = False,
                              admission_retry: bool = False, claim_args: list[str] | None = None):
        """Both generation and cached validation use the original Todo owner."""
        from .steward_commands import native_command
        actor = request["actor"]
        if claim_args is not None:
            operation_id = f"todo-claim:native-turn:{turn_id}"
            claim = native_command(self.controller, [*claim_args, "--claim-operation-id", operation_id])
            if claim.get("claim_operation_id") != operation_id:
                raise ValueError("native task preparation did not return its original claim")
            receipts = native_command(self.controller, ["todo", "receipt", "--goal-id", actor["goal_id"],
                "--operation-id", operation_id])["receipts"]
            lease = receipts[0]["result"].get("lease") if len(receipts) == 1 else None
            if not isinstance(lease, dict):
                raise ValueError("native task preparation could not read back its original lease")
        else:
            retained = native_command(self.controller, ["task-lease", "inspect", "--goal-id", actor["goal_id"],
                "--todo-id", request["todo_id"]]).get("lease")
            version = retained["version"] if retained else 0
            suffix = (".validation-recovery." + str(version) if recovery else
                ".admission-retry." + turn_id if admission_retry else
                ".admission-retry" if request.get("resume_turn_key") else "")
            lease = native_command(self.controller, ["task-lease", "acquire", "--goal-id", actor["goal_id"],
                "--todo-id", request["todo_id"], "--owner", actor["agent_id"],
                "--idempotency-key", request["turn_instance_id"] + suffix, "--ttl-seconds", "3600",
                "--expected-version", str(version)])["lease"]
        try:
            yield lease
        finally:
            # Completion retires this lease. Never release a later owner's.
            try:
                retained = native_command(self.controller, ["task-lease", "inspect", "--goal-id", actor["goal_id"],
                    "--todo-id", request["todo_id"]]).get("lease")
                if retained and retained.get("status") == "active":
                    proof = self._task_lease_proof(request, lease)
                    native_command(self.controller, ["task-lease", "release", "--goal-id", actor["goal_id"],
                        "--todo-id", request["todo_id"], "--owner", actor["agent_id"],
                        "--idempotency-key", proof["task_lease_idempotency_key"],
                        "--expected-version", str(proof["task_lease_expected_version"])])
            except (OSError, ValueError, RuntimeError):
                self.store.append_event(actor["session_id"], turn_id, kind="native_loopx.task_lease_release_unconfirmed",
                    payload={"todo_id": request["todo_id"], "lease_epoch": lease["lease_epoch"]})

    def _frozen_report_contract_recovery(
        self,
        actor: Mapping[str, Any],
        window: Mapping[str, Any],
        original: Mapping[str, Any],
        *,
        key: str,
        generation: Mapping[str, Any],
        root: Path,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        """Recover only a already-frozen report whose host candidate hit host_result_contract."""
        from ...control_plane.turn_driver.host_candidate import parse_model_json
        from ...control_plane.turn_driver.journal_store import load_turn_journal, turn_journal_path

        journal = load_turn_journal(turn_journal_path(root, goal_id=actor["goal_id"], turn_key=key))
        if (
            not isinstance(journal, Mapping)
            or journal.get("status") != "failed"
            or journal.get("result_kind") != "validation_failed"
            or journal.get("validation_stage") != "host_result_contract"
            or journal.get("host_attempt_count") != 1
        ):
            return None
        resumed = self.store.turn_for_client(
            actor["session_id"],
            native_steward_client_id(actor, "report." + window["window_id"] + ".admission-resume"),
        )
        retry = self.store.turn_for_client(
            actor["session_id"],
            native_steward_client_id(actor, "report." + window["window_id"] + ".admission-retry"),
        )
        seen_turn_ids: set[str] = set()
        candidates: list[Mapping[str, Any]] = []
        for candidate_turn in (resumed, retry, original):
            if isinstance(candidate_turn, Mapping):
                tid = candidate_turn.get("turn_id")
                if isinstance(tid, str) and tid not in seen_turn_ids:
                    seen_turn_ids.add(tid)
                    candidates.append(candidate_turn)
        for candidate_turn in candidates:
            events = self.store.events_after(actor["session_id"], candidate_turn["turn_id"], None)
            native_failed = any(
                row.get("kind") == "native_loopx.result"
                and isinstance(row.get("payload"), Mapping)
                and row["payload"].get("resume_turn_key") == key
                and row["payload"].get("status") == "failed"
                and row["payload"].get("result_kind") == "validation_failed"
                and row["payload"].get("validation_stage") == "host_result_contract"
                for row in events
            )
            if not native_failed:
                continue
            for row in reversed(events):
                if row.get("kind") != "answer.final":
                    continue
                message = (((row.get("payload") or {}).get("response") or {}).get("message"))
                parsed = parse_model_json(str(message or ""))
                if not isinstance(parsed, dict):
                    continue
                if parsed.get("result_kind") not in {"validated_progress", "validated_completion"}:
                    continue
                editorial = parsed.get("editorial_response")
                if not isinstance(editorial, Mapping) or editorial.get("request_digest") != generation.get("request_digest"):
                    continue
                proof = {
                    "turn_key": key,
                    "result_kind": "validation_failed",
                    "validation_stage": "host_result_contract",
                    "host_attempt_count": 1,
                    "generation_verified": True,
                    "saved_candidate_verified": True,
                }
                return proof, _normalize_report_kernel_candidate(parsed)
        return None

    def _stopped_report_candidate_recovery(
        self,
        actor: Mapping[str, Any],
        window: Mapping[str, Any],
        original: Mapping[str, Any],
        *,
        key: str,
        folder: Path,
        root: Path,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        """Recover a stopped report Turn only when its saved final answer already holds a valid candidate."""
        from ...control_plane.turn_driver.host_candidate import parse_model_json
        from ...control_plane.turn_driver.journal_store import load_turn_journal, turn_journal_path

        req_path = folder / "editorial-request.json"
        if not req_path.is_file() or not (folder / "source.json").is_file():
            return None
        if (folder / "editorial-response.json").exists() and not (folder / "private-generation.json").exists():
            return None
        try:
            request_digest = json.loads(req_path.read_text(encoding="utf-8")).get("request_digest")
        except (OSError, ValueError):
            return None
        if not isinstance(request_digest, str) or not request_digest:
            return None
        journal = load_turn_journal(turn_journal_path(root, goal_id=actor["goal_id"], turn_key=key))
        if (
            not isinstance(journal, Mapping)
            or journal.get("status") != "stopped"
            or journal.get("result_kind") != "wait"
            or journal.get("host_attempt_count") != 1
            or (journal.get("host_result") or {}).get("classification") != "no_typed_host_result"
        ):
            return None
        events = self.store.events_after(actor["session_id"], original["turn_id"], None)
        stopped_event = any(
            row.get("kind") == "native_loopx.result"
            and isinstance(row.get("payload"), Mapping)
            and row["payload"].get("resume_turn_key") == key
            and row["payload"].get("status") == "stopped"
            and row["payload"].get("result_kind") == "wait"
            for row in events
        )
        if not stopped_event:
            return None
        for row in reversed(events):
            if row.get("kind") != "answer.final":
                continue
            message = ((row.get("payload") or {}).get("response") or {}).get("message")
            parsed = parse_model_json(str(message or ""))
            if not isinstance(parsed, dict) or parsed.get("result_kind") not in {"validated_progress", "validated_completion"}:
                continue
            editorial = parsed.get("editorial_response")
            if not isinstance(editorial, Mapping) or editorial.get("request_digest") != request_digest:
                continue
            proof = {
                "turn_key": key,
                "result_kind": "wait",
                "classification": "no_typed_host_result",
                "host_attempt_count": 1,
                "generation_verified": True,
                "saved_candidate_verified": True,
            }
            return proof, dict(parsed)
        return None

    def has_recoverable_stopped_report(
        self,
        actor: Mapping[str, Any],
        window: Mapping[str, Any],
        todo: Mapping[str, Any],
    ) -> bool:
        """Read-only gate check before acquiring the recovery fence."""
        if todo.get("status") != "open":
            return False
        original = report_turn(self.store, actor, window)
        if not original:
            return False
        if any(row.get("kind") == "native_loopx.result" and (row.get("payload") or {}).get("status") == "committed"
               for row in self.store.events_after(actor["session_id"], original["turn_id"], None)):
            return False
        request = original.get("loopx_request") or {}
        turn_instance_id = request.get("turn_instance_id")
        if not isinstance(turn_instance_id, str) or not turn_instance_id:
            return False
        from ...control_plane.turn_driver.journal_store import find_loopx_turn_key_by_settlement_identity
        from ..periodic_report.private_generation import attempt_directory
        from .steward_reports import report_service
        reports, _ = report_service(self.controller, dict(actor))
        folder = attempt_directory(reports.root, dict(window))
        saved_key = find_loopx_turn_key_by_settlement_identity(
            reports.root, goal_id=actor["goal_id"], agent_id=actor["agent_id"],
            todo_id=todo["todo_id"], turn_instance_id=turn_instance_id + ".saved-candidate",
        )
        if saved_key is not None and (folder / "private-generation.json").exists():
            return True
        key = find_loopx_turn_key_by_settlement_identity(
            reports.root, goal_id=actor["goal_id"], agent_id=actor["agent_id"],
            todo_id=todo["todo_id"], turn_instance_id=turn_instance_id,
        )
        return bool(key is not None and self._stopped_report_candidate_recovery(
            actor, window, original, key=key, folder=folder, root=reports.root,
        ) is not None)

    @staticmethod
    def _build_replay_runner(normalized_candidate: Mapping[str, Any]):
        from ...control_plane.turn_driver.host_candidate import build_result

        replayed = False

        def replay_frozen_candidate(native_request: Mapping[str, Any], _launch_scope: Any) -> dict[str, Any]:
            nonlocal replayed
            if replayed:
                raise ValueError("frozen report candidate replay may execute at most once")
            replayed = True
            return build_result(native_request, dict(normalized_candidate), host_name="Codex app-server", allow_completion=True)

        return replay_frozen_candidate

    def _settle_stopped_report_candidate(
        self,
        *,
        actor: Mapping[str, Any],
        window: Mapping[str, Any],
        todo: Mapping[str, Any],
        original: Mapping[str, Any],
        request: dict[str, Any],
        observed: dict[str, Any] | None,
        reports: Any,
        owner_key: str,
        folder: Path,
        original_key: str,
        stopped_proof: dict[str, Any],
        raw_candidate: dict[str, Any],
    ) -> dict[str, Any]:
        from ...cli import build_parser
        from ...cli_commands.turn import handle_turn_command
        from ...control_plane.turn_driver import inspect_loopx_turn_journal
        from ..periodic_report.private_generation import validate_private_generation

        inspection = inspect_loopx_turn_journal(
            reports.root, goal_id=actor["goal_id"], agent_id=actor["agent_id"],
            turn_key=original_key, retry_failed=True,
        )
        admitted = self._admit(
            request, observed=observed, settlement_recovery=inspection,
            frozen_contract_recovery=stopped_proof,
        )
        if not (folder / "private-generation.json").exists():
            reports.freeze(request, dict(raw_candidate))
        generation = validate_private_generation(folder)
        if raw_candidate["editorial_response"]["request_digest"] != generation["request_digest"]:
            raise ValueError("the recovered candidate digest does not match the frozen report")
        if not reports.delivery_current(owner_key, window):
            raise ValueError("the original report authorization changed before settlement recovery")
        saved_instance_id = request["turn_instance_id"] + ".saved-candidate"
        argv = ["turn", "run-once", "--goal-id", actor["goal_id"], "--agent-id", actor["agent_id"],
                "--project", admitted["workspace_path"], "--host", "generic-cli",
                "--turn-instance-id", saved_instance_id, "--todo-id", todo["todo_id"], "--execute"]
        if request.get("validation_command") is not None:
            argv += ["--validation-command-json", json.dumps(request["validation_command"])]
        args = build_parser().parse_args(argv)
        args.host, args.session_scope = "codex-app-server", "agent"
        result: dict[str, Any] = {}
        host_runner = self._build_replay_runner(_normalize_report_kernel_candidate(raw_candidate))
        with self._task_execution_lease(request, original["turn_id"], recovery=True) as lease:
            handle_turn_command(
                args, registry_path=self.controller.registry_path,
                runtime_root_arg=str(reports.root), output_format=lambda *_a, **_kw: "json",
                print_payload=lambda value, *_a: result.update(value), native_host_runner=host_runner,
                native_session_binding_resolver=lambda _envelope: {
                    "schema_version": "loopx_turn_session_binding_v0",
                    "goal_id": actor["goal_id"], "agent_id": actor["agent_id"],
                },
                native_task_validator=self._report_validator(request),
                native_completion_lease_resolver=lambda: self._task_lease_proof(request, lease),
            )
        return result

    def recover_report_settlement(self, actor: dict[str, Any], window: dict[str, Any], todo: dict[str, Any]) -> None:
        """Resume only cached, validated native effects of the original report.

        Neither Todo DONE nor outer Chat completion is a native commit. The
        journal owner must authorize a no-host continuation; its existing
        effect readbacks reconcile ambiguous writeback/debit outcomes.
        """
        from ...cli import build_parser
        from ...cli_commands.turn import handle_turn_command
        from ...control_plane.turn_driver import inspect_loopx_turn_journal
        from ...control_plane.turn_driver.journal_store import find_loopx_turn_key_by_settlement_identity
        from ..periodic_report.private_generation import attempt_directory, validate_private_generation
        from .steward_reports import report_service
        observed = self._observe(actor)
        with self.controller.lock, self._report_fence({"actor": actor}):
            original = report_turn(self.store, actor, window)
            if not original:
                return
            request = original.get("loopx_request") or {}
            if (request.get("actor") != actor or request.get("report_window_id") != window["window_id"]
                    or request.get("todo_id") != todo["todo_id"]):
                raise ValueError("the original report settlement identity changed")
            if any(row["kind"] == "native_loopx.result" and row["payload"].get("status") == "committed"
                   for row in self.store.events_after(actor["session_id"], original["turn_id"], None)):
                return
            reports, owner_key = report_service(self.controller, actor)
            folder = attempt_directory(reports.root, window)
            saved_key = find_loopx_turn_key_by_settlement_identity(
                reports.root, goal_id=actor["goal_id"], agent_id=actor["agent_id"],
                todo_id=todo["todo_id"], turn_instance_id=request["turn_instance_id"] + ".saved-candidate",
            )
            key = saved_key or find_loopx_turn_key_by_settlement_identity(
                reports.root, goal_id=actor["goal_id"], agent_id=actor["agent_id"],
                todo_id=todo["todo_id"], turn_instance_id=request["turn_instance_id"],
            )
            if key is None:
                return
            if saved_key is None:
                stopped_recovery = self._stopped_report_candidate_recovery(
                    actor, window, original, key=key, folder=folder, root=reports.root,
                )
                if stopped_recovery is not None:
                    result = self._settle_stopped_report_candidate(
                        actor=actor, window=window, todo=todo, original=original, request=request,
                        observed=observed, reports=reports, owner_key=owner_key, folder=folder,
                        original_key=key, stopped_proof=stopped_recovery[0], raw_candidate=stopped_recovery[1],
                    )
                    self.store.append_event(actor["session_id"], original["turn_id"], kind="native_loopx.result", payload=result)
                    return
            generation = validate_private_generation(folder)
            if not reports.delivery_current(owner_key, window):
                raise ValueError("the original report authorization changed before settlement recovery")
            inspection = inspect_loopx_turn_journal(reports.root, goal_id=actor["goal_id"],
                agent_id=actor["agent_id"], turn_key=key, retry_failed=True)
            contract_recovery = (
                self._frozen_report_contract_recovery(
                    actor, window, original, key=key, generation=generation, root=reports.root,
                )
                if (inspection.get("recovery_decision") or {}).get("reinvoke_host") is True
                else None
            )
            admitted = self._admit(
                request,
                observed=observed,
                settlement_recovery=inspection,
                frozen_contract_recovery=contract_recovery[0] if contract_recovery else None,
            )
            def no_model(*_args):
                raise ValueError("report settlement recovery must never invoke a model")
            host_runner = (
                self._build_replay_runner(contract_recovery[1])
                if contract_recovery is not None
                else no_model
            )
            args = build_parser().parse_args(["turn", "run-once", "--goal-id", actor["goal_id"],
                "--agent-id", actor["agent_id"], "--project", admitted["workspace_path"],
                "--host", "generic-cli", "--resume-turn-key", key, "--retry-failed-turn", "--execute"])
            args.host, args.session_scope = "codex-app-server", "agent"
            result: dict[str, Any] = {}
            with (self._task_execution_lease(request, original["turn_id"], recovery=True)
                    if todo["status"] == "open" else nullcontext(None)) as lease:
                handle_turn_command(args, registry_path=self.controller.registry_path,
                    runtime_root_arg=str(reports.root), output_format=lambda *_a, **_kw: "json",
                    print_payload=lambda value, *_a: result.update(value), native_host_runner=host_runner,
                    native_session_binding_resolver=lambda _envelope: {"schema_version": "loopx_turn_session_binding_v0",
                        "goal_id": actor["goal_id"], "agent_id": actor["agent_id"]},
                    native_task_validator=self._report_validator(request),
                    **({"native_completion_lease_resolver": lambda: self._task_lease_proof(request, lease)} if lease else {}))
            # Retain the historical outer failure. The actual native owner
            # supplies the new event; no status or publication proof is forged.
            self.store.append_event(actor["session_id"], original["turn_id"], kind="native_loopx.result", payload=result)

    def _submission_binding(self, binding_id: str) -> dict[str, Any] | None:
        return next((row for row in self.bindings.read()["bindings"] if row["binding_id"] == binding_id), None)

    def _prepare_submission(self, binding: dict[str, Any] | None, *, turn_instance_id: str, todo_id: str | None,
                            validation_command: Sequence[str] | None, work_kind: str,
                            monitor_deadline: str | None, report_window: dict[str, Any] | None,
                            quota_observation: dict[str, Any] | None = None) -> dict[str, Any]:
        """Read original request/recovery identity without accepting any work."""
        if binding is None:
            raise ValueError("the original steward binding is unavailable")
        actor = binding.get("managed_actor")
        if not actor:
            raise ValueError("prepare the original steward identity first")
        request = {"operation": "native_turn", "host": "codex-app-server", "actor": actor,
            "turn_instance_id": turn_instance_id, "todo_id": todo_id,
            "work_kind": work_kind,
            "validation_command": list(validation_command) if validation_command is not None else None}
        if work_kind == "report":
            if not report_window:
                raise ValueError("a report requires its original admitted calendar window")
            request.update(report_window_id=report_window["window_id"],
                report_subscription_revision=report_window["subscription_revision"])
        client_id = native_steward_client_id(actor, turn_instance_id)
        existing = self.store.turn_for_client(actor["session_id"], client_id)
        if quota_observation is not None:
            if binding != quota_observation["binding"]:
                raise ValueError("the original steward authorization changed during quota observation")
            # Refence execution evidence after slow metadata IO; ordinary SSE
            # read counters do not change the original request or its effects.
            original = quota_observation["original"]
            if existing is None or any(existing.get(key) != original.get(key) for key in (
                    "turn_id", "client_turn_id", "status", "loopx_request", "response",
                    "upstream_turn_id", "delta_count", "error_code", "error", "loopx_execution")):
                raise ValueError("the original report changed during quota observation")
        launch_recovery = None
        selection_recovery = None
        if existing is not None:
            # Never replay a failed/unknown model Turn. A committed Chat
            # request is observed; native journal recovery remains separate.
            frozen = {key: value for key, value in (existing.get("loopx_request") or {}).items()
                if key not in {"monitor_lease_version", "monitor_deadline"}}
            frozen.setdefault("work_kind", "native_turn")
            comparable_frozen = (
                {**frozen, "validation_command": request["validation_command"]}
                if work_kind == "report" and _same_report_validator_command(
                    frozen.get("validation_command"), request.get("validation_command"))
                else frozen
            )
            if comparable_frozen != request or work_kind == "monitor" and existing["loopx_request"].get("monitor_deadline") != monitor_deadline:
                raise ValueError("native steward request identity conflict")
            if work_kind == "report":
                retained = report_turn(self.store, actor, report_window)
                if retained is not None and retained["turn_id"] != existing["turn_id"]:
                    return {"turn": retained}
                if quota_observation is None:
                    return {"turn": existing, "quota_probe": {"original": existing, "binding": binding}}
                quota_request = quota_observation["request"]
                if quota_request is not None:
                    if _same_report_validator_command(quota_request.get("validation_command"), request.get("validation_command")):
                        quota_request = {**quota_request, "validation_command": request["validation_command"]}
                    request = quota_request
                    client_id = native_steward_client_id(actor, request["turn_instance_id"])
                    existing = None
        if existing is not None:
            # Keep the separately proven legacy admission-refusal route
            # below ahead of broader runtime-error observation candidates.
            recovery = (self.monitor_restart_carrier(existing, client_id)
                if work_kind == "monitor" and not self._monitor_selection_failed_before_poll(existing) else None)
            if recovery is not None:
                if "turn" in recovery:
                    return recovery
                request, client_id, existing = recovery["request"], recovery["client_id"], None
        if existing is not None:
            monitor_retry = work_kind == "monitor" and self._monitor_selection_failed_before_poll(existing)
            if not monitor_retry and work_kind != "report":
                return {"turn": existing}
            # One separately correlated retry after the proven admission
            # failure. Retain the failed Chat Turn and original native
            # observation identity; that poll's owner remains idempotent.
            client_id = native_steward_client_id(actor, turn_instance_id + ".admission-retry")
            retry = self.store.turn_for_client(actor["session_id"], client_id)
            if retry is not None:
                if work_kind != "report":
                    return {"turn": retry}
                selection_recovery = self.report_selection_recovery(existing, probe_turn=retry)
                if selection_recovery is None or "probe" not in selection_recovery:
                    return {"turn": retry}
                client_id = native_steward_client_id(actor, turn_instance_id + ".admission-resume")
                resumed = self.store.turn_for_client(actor["session_id"], client_id)
                if resumed is not None:
                    return {"turn": resumed}
            if work_kind == "report" and selection_recovery is None:
                selection_recovery = self.report_selection_recovery(existing)
                if selection_recovery is None:
                    launch_recovery = self._report_launch_recovery(existing)
                    if launch_recovery is None:
                        return {"turn": existing}
                    request["resume_turn_key"] = existing["response"]["native_loopx"].get("resume_turn_key")
        return {"request": request, "client_id": client_id,
            "launch_recovery": launch_recovery, "selection_recovery": selection_recovery}

    def submit(self, binding_id: str, *, turn_instance_id: str, todo_id: str | None = None,
               validation_command: Sequence[str] | None = None,
               work_kind: str = "native_turn", monitor_deadline: str | None = None,
               report_window: dict[str, Any] | None = None) -> tuple[dict[str, Any], bool]:
        """Internal host entrypoint; busy conversations retain work with caller.

        No HTTP or model tool exposes this method. Calendar/monitor admission
        remains with its native owner; preparing identity never calls submit.
        """
        controller = self.controller
        parameters = dict(turn_instance_id=turn_instance_id, todo_id=todo_id,
            validation_command=validation_command, work_kind=work_kind,
            monitor_deadline=monitor_deadline, report_window=report_window)

        def inspect_submission(quota_observation: dict[str, Any] | None = None) -> dict[str, Any]:
            # History and recovery reads can be slow. Inspect one atomic
            # binding snapshot without blocking input, and check it again even
            # if this only returns an existing Turn. Admission is fenced below.
            binding = self._submission_binding(binding_id)
            inspected = self._prepare_submission(binding, **parameters, quota_observation=quota_observation)
            if binding != self._submission_binding(binding_id):
                raise ValueError("the original steward authorization changed during submission inspection")
            return inspected

        prepared = inspect_submission()
        quota_observation = None
        if "quota_probe" in prepared:
            quota_observation = prepared["quota_probe"]
            quota_observation["request"] = self.report_quota_request(quota_observation["original"])
            prepared = inspect_submission(quota_observation)
        if "turn" in prepared:
            return prepared["turn"], False
        observed = self._observe(prepared["request"]["actor"])
        retained = None
        if work_kind == "monitor":
            from .steward_commands import native_command
            if not todo_id or not monitor_deadline:
                raise ValueError("a native watch requires its original Todo and deadline")
            retained = native_command(controller, ["task-lease", "inspect", "--goal-id", prepared["request"]["actor"]["goal_id"],
                "--todo-id", todo_id]).get("lease")
        with controller.lock, exclusive_file_lock(self.bindings.path, operation="admit_native_steward_turn"):
            current = self._prepare_submission(self._submission_binding(binding_id), **parameters,
                quota_observation=quota_observation)
            if "turn" in current:
                return current["turn"], False
            if current["request"] != prepared["request"] or current["client_id"] != prepared["client_id"]:
                raise ValueError("the original native steward submission changed during identity verification")
            request, client_id = current["request"], current["client_id"]
            actor = request["actor"]
            if work_kind == "monitor":
                request.update(monitor_deadline=monitor_deadline,
                    monitor_lease_version=retained["version"] if retained else 0)
            admitted = self._admit(request, observed=observed,
                launch_recovery=current["launch_recovery"], selection_recovery=current["selection_recovery"])
            session = self.store.load_session(actor["session_id"])
            accepted = self.store.accept_managed_turn(actor["session_id"], client_turn_id=client_id,
                message="Advance the currently admitted native LoopX work.",
                display_message="检查授权工作状态。" if work_kind == "monitor" else
                    "生成本期授权项目日报。" if work_kind == "report" else "推进管家的 LoopX 任务。", origin="native_loopx",
                loopx_execution=True, loopx_request=request)
            try:
                if (not admitted["observation_only"] and work_kind != "report"
                        and actor.get("schema_version") == "loopx_steward_managed_actor_v0"
                        and session and session.get("steward_context")):
                    from ..manager_context import register_ingress

                    register_ingress(self.controller.coordination_runtime_root,
                        session_id=actor["session_id"], client_turn_id=client_id,
                        channel=session["channel_id"], sender_id=session["steward_context"]["operator_ref"],
                        message=accepted.turn["message"],
                        source_id=f"native_steward:{actor['goal_id']}:{request['turn_instance_id']}",
                        origin="native_loopx")
                if admitted["observation_only"]:
                    from .steward_monitor import StewardObservationAdapter
                    adapter = StewardObservationAdapter(session.get("upstream_thread_id"))
                else:
                    adapter = controller._ensure_adapter_locked(session,
                        work_dir=Path(admitted["workspace_path"]),
                        objective="Maintain the original steward's authorized monitoring work.",
                        accepted_turn_id=accepted.turn["turn_id"])
                controller._start_accepted_turn_worker(session_id=actor["session_id"],
                    turn_id=accepted.turn["turn_id"], message=accepted.turn["message"],
                    attachments=[], adapter=adapter, loopx_execution=True)
            except Exception as exc:
                controller._fail_queue_preparation(actor["session_id"], accepted.turn["turn_id"], exc)
                raise
            return accepted.turn, accepted.created

    def _steward_followup_context(self, actor: Mapping[str, Any], session_id: str,
                                  request: Mapping[str, Any], authority: Mapping[str, Any]) -> dict[str, Any]:
        from ...control_plane.todos.list_readback import list_goal_todos
        from .steward_monitor import FOLLOWUP_TARGET_PREFIX, steward_monitor_snapshot_path
        todo_id = (authority.get("_work_identity") or {}).get("todo_id") or request.get("todo_id")
        agent_todos = list_goal_todos(registry_path=self.controller.registry_path,
            runtime_root_arg=str(self.controller.coordination_runtime_root),
            goal_id=actor["goal_id"], agent_id=actor["agent_id"], role="agent").get("todos", [])
        selected = next((row for row in agent_todos if row.get("todo_id") == todo_id), None)
        payload: dict[str, Any] = {"selected_todo": selected}
        target_key = str((selected or {}).get("target_key") or "")
        if target_key.startswith(FOLLOWUP_TARGET_PREFIX):
            source_id = target_key.removeprefix(FOLLOWUP_TARGET_PREFIX)
            payload["source_monitor"] = next((row for row in agent_todos if row.get("todo_id") == source_id), None)
            snap = steward_monitor_snapshot_path(self.controller.coordination_runtime_root, actor["goal_id"], source_id)
            if snap.is_file():
                try:
                    payload["signal_snapshot"] = json.loads(snap.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    payload["signal_snapshot"] = None
        user_todos = list_goal_todos(registry_path=self.controller.registry_path,
            runtime_root_arg=str(self.controller.coordination_runtime_root),
            goal_id=actor["goal_id"], role="user").get("todos", [])
        payload["open_user_todos"] = [
            {k: row.get(k) for k in ("todo_id", "status", "priority", "text", "updated_at")}
            for row in user_todos if row.get("status") == "open"
        ][:40]
        prior: list[dict[str, Any]] = []
        for msg in reversed(self.store.messages(session_id)[-30:]):
            tid = msg.get("turn_id")
            if not tid:
                continue
            turn_row = self.store.load_turn(session_id, tid)
            resp = (turn_row or {}).get("response") or {}
            receipts = list(resp.get("context_handoff_receipts") or [])
            if resp.get("context_handoff_receipt"):
                receipts.append(resp["context_handoff_receipt"])
            for receipt in receipts:
                if isinstance(receipt, dict):
                    prior.append({k: receipt.get(k) for k in ("goal_id", "agent_id", "source_id", "request_id", "delivered_at")})
        payload["prior_handoff_receipts"] = prior[:40]
        return payload

    def _governance_replan_context(self, actor: Mapping[str, Any], authority: Mapping[str, Any]) -> dict[str, Any]:
        """Project public-safe review/writeback/vision context for text-only Steward replanning."""
        from ...cli import build_parser
        from ...cli_commands.quota_native_context import read_replan_view
        from ...control_plane.goals.goal_frontier import latest_agent_vision_from_status_payload
        from ...control_plane.todos.list_readback import list_goal_todos
        from .steward_commands import NativeStewardCommandError, native_command

        model_input = authority.get("_model_input")
        detail_ref = model_input.get("detail_ref") if isinstance(model_input, Mapping) else None
        packet = authority.get("replan_action_packet") or (
            detail_ref.get("replan_action_packet") if isinstance(detail_ref, Mapping) else None
        )

        def _read_stage(cmd_str: Any) -> dict[str, Any] | None:
            if not isinstance(cmd_str, str) or not cmd_str.strip():
                return None
            try:
                tokens = shlex.split(cmd_str)
                q_idx = tokens.index("quota")
                parsed = build_parser().parse_args(tokens[q_idx:])
                view = read_replan_view(parsed)
                if isinstance(view, Mapping) and view.get("ok") is True:
                    return dict(view)
            except (OSError, ValueError, KeyError, TypeError, RuntimeError, EffectRuntimeRejected, SystemExit):
                return None
            return None

        payload: dict[str, Any] = {}
        if isinstance(packet, Mapping):
            review_view = _read_stage(packet.get("review"))
            if review_view is not None:
                core_goal = review_view.get("core_goal")
                payload["review"] = {
                    "obligation_id": review_view.get("obligation_id"),
                    "context_id": review_view.get("context_id"),
                    "objective": core_goal.get("objective") if isinstance(core_goal, Mapping) else None,
                    "triggers": review_view.get("triggers"),
                    "recent_evidence": list(review_view.get("evidence") or [])[:12],
                    "decision_followthrough": review_view.get("decision_followthrough"),
                    **({"todo_meta_goals": review_view["todo_meta_goals"]} if "todo_meta_goals" in review_view else {}),
                }
            writeback_view = _read_stage(packet.get("writeback"))
            if writeback_view is not None:
                payload["writeback"] = {
                    "obligation_id": writeback_view.get("obligation_id"),
                    "context_id": writeback_view.get("context_id"),
                    "required_outcome": writeback_view.get("required_outcome"),
                    "writeback_contract": writeback_view.get("writeback_contract"),
                    "stride_control": writeback_view.get("stride_control"),
                }
        try:
            status_payload = native_command(self.controller, ["status", "--goal-id", actor["goal_id"]])
            latest_vision = latest_agent_vision_from_status_payload(
                status_payload, goal_id=actor["goal_id"], agent_id=actor["agent_id"],
            )
            if isinstance(latest_vision, Mapping):
                payload["current_agent_vision"] = {
                    k: latest_vision.get(k)
                    for k in ("schema_version", "state", "vision_patch", "todo_delta")
                    if latest_vision.get(k) is not None
                }
        except (NativeStewardCommandError, OSError, ValueError, KeyError, TypeError, RuntimeError):
            pass
        try:
            agent_todos = list_goal_todos(
                registry_path=self.controller.registry_path,
                runtime_root_arg=str(self.controller.coordination_runtime_root),
                goal_id=actor["goal_id"],
                role="agent",
            ).get("todos", [])
            payload["open_agent_todos"] = [
                {k: row.get(k) for k in ("todo_id", "task_class", "priority", "target_key", "title")}
                for row in agent_todos
                if row.get("status") == "open"
            ][:16]
        except (NativeStewardCommandError, OSError, ValueError, KeyError, TypeError, RuntimeError):
            pass
        return payload

    @staticmethod
    def _normalize_steward_actions(candidate: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
        from ...control_plane.collaboration.inbox import normalize_handoff
        state: dict[str, Any] = {"context_handoff": None, "user_actions": [], "owner_report": None, "validation_error": None}
        allowed_targets = {
            (row["goal_id"], row["agent_id"])
            for row in ((context.get("context_delegation") or {}).get("targets") or [])
            if isinstance(row, Mapping) and row.get("goal_id") and row.get("agent_id")
        }
        raw_handoff = candidate.get("context_handoff")
        if raw_handoff is not None:
            try:
                normalized = normalize_handoff(raw_handoff)
            except ValueError as exc:
                state["validation_error"] = f"invalid context_handoff: {exc}"
                return state
            if normalized:
                items = normalized if isinstance(normalized, list) else [normalized]
                for item in items:
                    if (item["goal_id"], item["agent_id"]) not in allowed_targets:
                        state["validation_error"] = f"unauthorized context_handoff recipient: {item['goal_id']}/{item['agent_id']}"
                        return state
                state["context_handoff"] = normalized
        raw_actions = candidate.get("steward_user_actions")
        if raw_actions is not None:
            if not isinstance(raw_actions, list) or len(raw_actions) > 16:
                state["validation_error"] = "steward_user_actions must be a bounded list of at most 16 items"
                return state
            cleaned: list[dict[str, str]] = []
            seen_keys: set[str] = set()
            for item in raw_actions:
                if not isinstance(item, Mapping):
                    state["validation_error"] = "each steward_user_actions entry must be an object"
                    return state
                key = str(item.get("dedupe_key") or "").strip().lower()
                text = " ".join(str(item.get("text") or "").split()).strip()
                prio = str(item.get("priority") or "P1").strip().upper()
                if (not _USER_ACTION_DEDUPE_KEY.fullmatch(key) or key in seen_keys
                        or not text or len(text) > 800 or prio not in {"P0", "P1", "P2"}):
                    state["validation_error"] = "invalid steward_user_actions entry"
                    return state
                seen_keys.add(key)
                cleaned.append({"dedupe_key": key, "priority": prio, "text": text})
            state["user_actions"] = cleaned
        raw_report = candidate.get("steward_owner_report")
        if raw_report is not None:
            markdown = raw_report.get("markdown") if isinstance(raw_report, Mapping) else raw_report
            if not isinstance(markdown, str):
                state["validation_error"] = "steward_owner_report must be a markdown string or {markdown}"
                return state
            normalized_report = markdown.strip()
            if not normalized_report or len(normalized_report) > 24000:
                state["validation_error"] = "steward_owner_report markdown must be 1..24000 characters"
                return state
            state["owner_report"] = normalized_report
        return state

    def _apply_steward_user_actions(self, actor: Mapping[str, Any], actions: Sequence[Mapping[str, str]],
                                    turn_instance_id: str) -> list[dict[str, Any]]:
        if not actions:
            return []
        from ...control_plane.todos.list_readback import list_goal_todos
        from .steward_commands import native_command
        existing = list_goal_todos(registry_path=self.controller.registry_path,
            runtime_root_arg=str(self.controller.coordination_runtime_root),
            goal_id=actor["goal_id"], role="user").get("todos", [])
        open_by_key: dict[str, tuple[dict[str, Any], str]] = {}
        for row in existing:
            if row.get("status") != "open":
                continue
            title = str(row.get("title") or "").strip() or re.sub(
                r"^\[p\d\]\s*", "", str(row.get("text") or ""), flags=re.IGNORECASE,
            ).strip()
            if title.startswith("[") and "]" in title:
                open_by_key[title[1:title.index("]")].strip().lower()] = (row, title)
        receipts: list[dict[str, Any]] = []
        for idx, action in enumerate(actions):
            key, prio, body = action["dedupe_key"], action["priority"], action["text"]
            rendered = f"[{key}] {body}"
            op_id = "steward-ua." + hashlib.sha256(f"{turn_instance_id}:{idx}:{key}".encode()).hexdigest()[:24]
            matched = open_by_key.get(key)
            if matched is not None:
                prior, prior_title = matched
                if prior_title == rendered and str(prior.get("priority") or "") == prio:
                    receipts.append({"dedupe_key": key, "action": "unchanged", "todo_id": prior["todo_id"]})
                    continue
                res = native_command(self.controller, ["todo", "update", "--goal-id", actor["goal_id"],
                    "--role", "user", "--agent-id", actor["agent_id"], "--todo-id", prior["todo_id"],
                    "--text", rendered,
                    *(["--note", f"Priority: {prio}"] if str(prior.get("priority") or "") != prio else []),
                    "--update-operation-id", op_id, "--execute"])
                receipts.append({"dedupe_key": key, "action": "updated", "todo_id": prior["todo_id"], "ok": res.get("ok")})
            else:
                res = native_command(self.controller, ["todo", "add", "--goal-id", actor["goal_id"],
                    "--role", "user", "--task-class", "user_action", "--priority", prio,
                    "--text", rendered, "--bound-agent", actor["agent_id"],
                    "--operation-id", op_id, "--execute"])
                receipts.append({"dedupe_key": key, "action": "added", "todo_id": res.get("todo_id"), "ok": res.get("ok")})
        return receipts

    def execute(self, *, session_id: str, turn_id: str, adapter: Any,
                event_sink: Any, context: dict[str, Any]) -> dict[str, Any]:
        """Run in the original Chat worker, through all native settlement owners."""
        from ...cli import build_parser
        from ...cli_commands.turn import handle_turn_command
        from ...control_plane.turn_driver.host_candidate import extract_turn_authority, render_prompt, parse_model_json, build_result
        from .antigravity_adapter import AntigravityAdapter
        from .codex_adapter import CodexAppServerAdapter

        turn = self.store.load_turn(session_id, turn_id)
        request = turn["loopx_request"]
        original_client = ("managed." + request["turn_instance_id"].removeprefix("managed-session.")
            if request["actor"].get("schema_version") == "loopx_managed_session_actor_v0"
            else native_steward_client_id(request["actor"], request["turn_instance_id"]))
        original = self.store.turn_for_client(session_id, original_client) if request.get("work_kind") == "report" else None
        selection_recovery = (self.report_selection_recovery(original)
            if original and original["turn_id"] != turn_id else None)
        resumed_client = (original_client + ".admission-resume"
            if request["actor"].get("schema_version") == "loopx_managed_session_actor_v0"
            else native_steward_client_id(request["actor"], request["turn_instance_id"] + ".admission-resume"))
        if selection_recovery and turn["client_turn_id"] == resumed_client:
            retry_client = (original_client + ".admission-retry"
                if request["actor"].get("schema_version") == "loopx_managed_session_actor_v0"
                else native_steward_client_id(request["actor"], request["turn_instance_id"] + ".admission-retry"))
            retry = self.store.turn_for_client(session_id, retry_client)
            if retry is None:
                raise ValueError("the original admission probe is unavailable")
            selection_recovery = self.report_selection_recovery(original, probe_turn=retry)
            if selection_recovery is None:
                raise ValueError("the original admission probe has no canonical journal")
        admitted = self._admit(request, turn=turn, selection_recovery=selection_recovery)
        actor = admitted["actor"]
        local_session = actor.get("schema_version") == "loopx_managed_session_actor_v0"
        report_work = request.get("work_kind") == "report"
        steward_advancement = not local_session and not report_work
        managed_turn = ManagedSessionTurn(self.controller) if local_session and not report_work else None
        if admitted["observation_only"]:
            from .steward_monitor import StewardMonitor
            monitor = StewardMonitor(self.controller)
            todo = next((row for row in monitor.todos(actor) if row["todo_id"] == request["todo_id"]), None)
            return monitor.observe(host=self, request=request, turn=turn, context=context, todo=todo)
        if local_session and not isinstance(adapter, CodexAppServerAdapter):
            raise ValueError("native steward work requires the original Codex app-server adapter")
        if not local_session and not isinstance(adapter, (CodexAppServerAdapter, AntigravityAdapter)):
            raise ValueError("native steward work requires the original Codex app-server or Antigravity adapter")
        original_thread = adapter.upstream_thread_id
        from .steward_reports import report_service
        reports, report_owner_key = report_service(self.controller, actor) if report_work else (None, None)
        report_context = ""
        governance_work = False
        managed_answer = ""
        managed_lease, managed_lease_request = None, None
        preparation_status = None
        steward_actions_state: dict[str, Any] = {"context_handoff": None, "user_actions": [], "owner_report": None, "validation_error": None}

        def report_lease_proof():
            return self._task_lease_proof(request, report_lease)

        def current(observed=None):
            fresh = self.store.load_turn(session_id, turn_id)
            self._admit(request, turn=fresh, observed=observed, selection_recovery=selection_recovery)
            if adapter.upstream_thread_id != original_thread:
                raise ValueError("the original steward native thread changed")
            if report_lease:
                report_lease_proof()
            if managed_lease:
                self._task_lease_proof(managed_lease_request, managed_lease)

        def session_binding(envelope: Mapping[str, Any]) -> dict[str, Any]:
            current()
            identity = (envelope.get("agent_identity") if envelope.get("schema_version") == "loopx_turn_envelope_v1"
                        else envelope)
            if (envelope.get("goal_id") != actor["goal_id"] or not isinstance(identity, Mapping)
                    or identity.get("agent_id") != actor["agent_id"]):
                raise ValueError("native Turn belongs to another actor")
            return {"schema_version": "loopx_turn_session_binding_v0",
                "goal_id": actor["goal_id"], "agent_id": actor["agent_id"]}

        def prepare_native_task(decision: Mapping[str, Any]) -> str | None:
            nonlocal managed_lease, managed_lease_request, preparation_status
            from ...control_plane.turn_driver.codex_execution_preparation import compact_execution_preparation_request

            preparation = compact_execution_preparation_request(decision)
            if preparation is None:
                return None
            todo_id = preparation["todo_id"]
            if (preparation["goal_id"] != actor["goal_id"] or preparation["agent_id"] != actor["agent_id"]
                    or preparation.get("turn_instance_id", request["turn_instance_id"]) != request["turn_instance_id"]
                    or (request["todo_id"] is not None and todo_id != request["todo_id"])):
                raise ValueError("native task preparation belongs to another Turn or Todo")
            current()
            if managed_turn is not None:
                try:
                    managed_turn.prepare_validation(request, {"work_contract": decision})
                except ManagedTaskValidationUnavailable:
                    managed_turn.validation_unavailable = True
                    preparation_status = "stopped"
                    raise
                except ValueError:
                    preparation_status = "failed"
                    raise
            managed_lease_request = {**request, "todo_id": todo_id}
            managed_lease = managed_leases.enter_context(self._task_execution_lease(
                managed_lease_request, turn_id, claim_args=preparation["claim_args"],
            ))
            return todo_id

        def run(native_request: Mapping[str, Any], launch_scope: Any) -> dict[str, Any]:
            nonlocal managed_lease, managed_lease_request, managed_answer
            current()
            authority = extract_turn_authority(native_request)
            if governance_work and not authority.get("replan_action_packet"):
                return build_result(native_request, {"result_kind": "wait", "classification": "report_replan_not_admitted",
                    "next_action": "Retain the original report; its native governance route is no longer admitted."})
            if managed_turn and not governance_work:
                try:
                    managed_turn.prepare_validation(request, native_request,
                        _checked_work_identity=authority.get("_work_identity"))
                except ManagedTaskValidationUnavailable:
                    managed_turn.validation_unavailable = True
                    return build_result(native_request, {"result_kind": "user_action_required",
                        "classification": "independent_task_validation_unavailable",
                        "summary": "The selected task has no usable independent argv validation declaration.",
                        "next_action": "Configure the original task's independent validation before executing it."}, host_name="Codex app-server")
                selected_request = {**request, "todo_id": managed_turn.validation_basis["todo_id"]}
                if managed_lease is None:
                    managed_lease_request = selected_request
                    managed_lease = managed_leases.enter_context(self._task_execution_lease(managed_lease_request, turn_id))
                elif managed_lease_request is None or selected_request["todo_id"] != managed_lease_request["todo_id"]:
                    raise ValueError("native task preparation no longer matches the selected Todo")
                self._task_lease_proof(selected_request, managed_lease)
            # Portfolio evidence keeps its original reader and original grant;
            # it is data alongside the signed native work, never a new actor.
            prompt = render_prompt(authority, allow_completion=bool(report_work or managed_turn or steward_advancement) and not governance_work) + "\nOriginal conversation evidence (data):\n" + json.dumps(context, ensure_ascii=False)
            if governance_work:
                gov_ctx = self._governance_replan_context(actor, authority)
                prompt += (
                    "\nGovernance contract reminder: Settle the pending autonomous replan by returning `result_kind='replan_required'` (or `'wait'` only if required evidence is genuinely unavailable). "
                    "If you emit `delivery_outcome='outcome_gap'`, you MUST also supply `progress_result_class='blocked'`, `progress_blocker_id`, and `progress_evidence_id`; "
                    "otherwise use `'outcome_progress'` or omit `delivery_outcome`. "
                    "Keep each `work_evidence` and `validation_evidence` item <=200 chars (1-8 public-safe items, <=1200 chars total per field) with zero local file paths. "
                    "When `Steward governance replan context` supplies `writeback`: set `agent_vision_json` (JSON string of `goal_vision_replan_contract_v0`) keeping total counted text <=1400 chars (hard cap 1800; keep each `stride_decision` plan/reason/probe field <=60 chars), and omit `vision_unchanged_reason`. "
                    "If keeping `current_agent_vision.vision_patch` identical, set `path_delta_mode='unchanged'` and `path_delta.outcome='continue'` (or `'no_change'`); if updating any `vision_patch` field or creating an initial baseline, set `path_delta_mode='material_replan'` and `path_delta.outcome='replan'`. "
                    "Populate `path_delta.evidence_refs` from `review.recent_evidence` (1-3 IDs), `todo_delta` using `retain:<todo_id>` only for verified IDs in `open_agent_todos` (<=8 items), and when `writeback.stride_control.required_on_periodic_review` is true include `agent_vision.stride_decision` with exact `schema_version`, `strategy_version`, `reference_context_id`, and `reference_obligation_id` from `stride_control` (plus `previous_review` when `previous_decision_ref` is non-null)."
                )
                if gov_ctx:
                    prompt += "\nSteward governance replan context (data):\n" + json.dumps(gov_ctx, ensure_ascii=False)
            if report_work and not governance_work:
                prompt += report_context
            if steward_advancement and not governance_work:
                selected_todo_id = (authority.get("_work_identity") or {}).get("todo_id") or request.get("todo_id")
                if selected_todo_id and managed_lease is None:
                    managed_lease_request = {**request, "todo_id": selected_todo_id}
                    managed_lease = managed_leases.enter_context(
                        self._task_execution_lease(managed_lease_request, turn_id))
                followup_ctx = self._steward_followup_context(actor, session_id, request, authority)
                prompt += (
                    "\nSteward advancement instructions: Evaluate the monitor prompt and signal snapshot against authorized recipients (`context_delegation.targets`), prior receipts (`prior_handoff_receipts`, avoid duplicate delivery for unchanged PR@headRefOid), and open user todos (`open_user_todos`). "
                    "When `signal_snapshot.repository_snapshot.items` is present, each PR carries `mergeable`, `checks_summary`, `bypassed_gates`, and `review_summary` (`effective_verdict`, `has_review_conflict`, `stale_review_count`, `exact_head_reviews` with `reviewer_declaration`, `valid_verdict_review`, `author_owned_fallback`). Keep triage read-only; never execute merges here. "
                    "You may include optional top-level JSON fields alongside the normal kernel fields: "
                    "`context_handoff` (single object or array <=32 of {goal_id, agent_id, brief} with `schema_version='collaboration_brief_v0'` and `inputs=[]` when referencing URLs/PRs, restricted to `context_delegation.targets`), "
                    "`steward_owner_report` (optional non-empty Markdown string <=24000 chars or `{markdown}` delivered once directly to the bound Steward App and Feishu conversation; omit or set `null` when silent/no report should be sent), "
                    "and `steward_user_actions` (array <=16 of {dedupe_key, priority:'P0'|'P1'|'P2', text}; do NOT emit per-PR `steward_user_actions` when the monitor prompt asks for an aggregated report). Keep each `work_evidence` and `validation_evidence` item <=200 chars (1-8 items, <=1200 chars total per field). Set `result_kind='validated_completion'` when triage/handoff/report/user-action preparation is complete.\n"
                    "Steward monitor & dedupe context (data):\n" + json.dumps(followup_ctx, ensure_ascii=False)
                )
            if request.get("work_kind") == "scheduled_task":
                from .session_calendar_tasks import SessionCalendarTasks
                basis = SessionCalendarTasks(self.controller).admission(request)
                # Read the completed source Task's criteria and referenced permitted
                # artifacts. Calendar configuration cannot install host argv
                # or enlarge the original Task's execution scope.
                prompt += "\nOriginal native calendar task criteria; existing Goal/Task authority and stop conditions still apply:\n" + json.dumps({
                    "task": basis["definition"]["source_task"]["text"],
                    "source_todo_id": basis["definition"]["source_task"]["todo_id"],
                    "window_start": basis["window"]["start_at"], "window_end": basis["window"]["due_at"],
                }, ensure_ascii=False)
            guard_owner = adapter.session if isinstance(adapter, CodexAppServerAdapter) else adapter
            previous_guard = guard_owner._launch_guard

            @contextmanager
            def launch():
                # The existing app-server request owner holds this fence only
                # through turn/start publication, never through model waiting.
                observed = self._observe(actor)
                with ExitStack() as stack:
                    try:
                        if previous_guard is not None:
                            stack.enter_context(previous_guard())
                        stack.enter_context(launch_scope())
                        stack.enter_context(nullcontext() if actor.get("schema_version") == "loopx_managed_session_actor_v0"
                            else exclusive_file_lock(self.bindings.path, operation="native_steward_launch"))
                        current(observed)
                    except FirstPartyHostRuntimeRejected as exc:
                        if not report_work or exc.code != "stale_goal_authority":
                            raise
                        # Only guard entry is before publication. Let the
                        # existing native executor durably record its typed
                        # failure; exceptions after yield remain unknown.
                        from ...control_plane.turn_driver.host_failure import BuiltInHostError
                        raise BuiltInHostError(str(exc), failure_kind="contract_rejected") from exc
                    yield

            guard_owner._launch_guard = launch
            try:
                if managed_turn and not governance_work:
                    from ...control_plane.effect_runtime import effect_runtime_result
                    policy = effect_runtime_result("collaboration.session.managed_turn.sandbox_policy", {
                        "host": request["host"], "actor": actor, "authority": authority,
                        "validation": managed_turn.validation_basis["plan"], "task_lease": managed_lease})
                    response = adapter.start_native_task_turn(prompt, event_sink, policy)
                else:
                    # Inline governance (.replan) shares the outer Chat turn_id; suppress
                    # terminal answer and native-result capture so the primary turn keeps
                    # the sole immutable native-results/{turn_id}.json and visible stream.
                    invocation_sink = (
                        (
                            lambda kind, payload: event_sink(kind, payload)
                            if kind not in {"answer.delta", "answer.final", "turn.native_result"}
                            else None
                        )
                        if (governance_work and event_sink is not None)
                        else event_sink
                    )
                    response = adapter.start_turn(prompt, invocation_sink)
            finally:
                guard_owner._launch_guard = previous_guard
            current()  # Revocation/stop/retarget after a long model call wins.
            candidate = parse_model_json(str(response.get("message") or ""))
            kernel_candidate = dict(candidate) if candidate is not None else None
            if steward_advancement and not governance_work and kernel_candidate is not None:
                steward_actions_state.update(self._normalize_steward_actions(kernel_candidate, context))
                kernel_candidate.pop("context_handoff", None)
                kernel_candidate.pop("steward_user_actions", None)
                kernel_candidate.pop("steward_owner_report", None)
                kernel_candidate = _normalize_steward_advancement_kernel_candidate(kernel_candidate)
            elif governance_work and kernel_candidate is not None:
                kernel_candidate.pop("context_handoff", None)
                kernel_candidate.pop("steward_user_actions", None)
                kernel_candidate.pop("steward_owner_report", None)
                kernel_candidate = _normalize_steward_advancement_kernel_candidate(kernel_candidate)
            if governance_work and kernel_candidate and kernel_candidate.get("result_kind") not in {"replan_required", "wait", "user_action_required", "iteration_failed"}:
                kernel_candidate = {"result_kind": "wait", "classification": "report_replan_not_settled",
                    "next_action": "Retain the original report until its native semantic replan is settled."}
            if report_work and not governance_work and kernel_candidate and kernel_candidate.get("result_kind") in {"validated_progress", "validated_completion"}:
                observed = self._observe(actor)
                with self.controller.lock, self._report_fence(request):
                    current(observed)
                    reports.freeze(request, kernel_candidate)
                    # This finite task's postcondition is the complete validated
                    # generation, not the model's choice of progress wording.
                    # The kernel still runs its independent validator and lease
                    # fence before closing only this window's generation Todo.
                    kernel_candidate = _normalize_report_kernel_candidate(kernel_candidate)
            built = build_result(native_request, kernel_candidate,
                host_name="Antigravity" if isinstance(adapter, AntigravityAdapter) else "Codex app-server",
                **({"allow_completion": True} if (report_work or managed_turn or steward_advancement) and not governance_work else {}))
            managed_answer = str(built.get("summary") or "") if (managed_turn or steward_advancement) else ""
            return built

        argv = ["turn", "run-once", "--goal-id", actor["goal_id"], "--agent-id", actor["agent_id"],
            "--project", admitted["workspace_path"], "--host", "generic-cli", "--execute"]
        if request.get("resume_turn_key"):
            # Keep the original plan, Todo, native Turn and settlement identity.
            # The kernel rechecks its journal and current launch authority.
            argv += ["--resume-turn-key", request["resume_turn_key"], "--retry-failed-turn"]
        else:
            argv += ["--turn-instance-id", request["turn_instance_id"]]
        if request["todo_id"] is not None and not request.get("resume_turn_key"):
            argv += ["--todo-id", request["todo_id"]]
        if request["validation_command"] is not None:
            argv += ["--validation-command-json", json.dumps(request["validation_command"])]
        args = build_parser().parse_args(argv)
        # This connection is host-owned, not a command path supplied by a model.
        # The kernel records the actual app-server host and independent profile.
        args.host, args.session_scope = "codex-app-server", "agent"
        result: dict[str, Any] = {}
        with ExitStack() as managed_leases, (self._task_execution_lease(request, turn_id,
                admission_retry=bool(selection_recovery)) if report_work else nullcontext(None)) as report_lease:
            if not report_work and request.get("todo_id") and not request.get("resume_turn_key"):
                # The selected Task's existing lease must precede the kernel's
                # delivery guard. Taking it in the model callback is too late:
                # the guard correctly withholds a Host launch without it.
                from .steward_commands import native_command
                selected = native_command(self.controller, ["todo", "list", "--goal-id", actor["goal_id"],
                    "--todo-id", request["todo_id"], "--agent-id", actor["agent_id"]])
                # Unknown work still reaches the kernel's structured selection
                # refusal. An unavailable authority never looks like a miss.
                if selected.get("matched") is True:
                    managed_lease_request = dict(request)
                    managed_lease = managed_leases.enter_context(self._task_execution_lease(request, turn_id))
            if selection_recovery and "probe" not in selection_recovery:
                governance_work = True

                def probe_governance(todo_id: str | None) -> dict[str, Any]:
                    governance_args = type(args)(**vars(args))
                    governance_args.todo_id, governance_args.validation_command_json = todo_id, None
                    governance_args.turn_instance_id = request["turn_instance_id"] + ".replan"
                    observed: dict[str, Any] = {}
                    handle_turn_command(governance_args, registry_path=self.controller.registry_path,
                        runtime_root_arg=str(self.controller.coordination_runtime_root),
                        output_format=lambda *_a, **_kw: "json", print_payload=lambda value, *_a: observed.update(value),
                        native_host_runner=run, native_session_binding_resolver=session_binding)
                    return observed

                # Probe the original report first. Unbound selection can
                # otherwise stop on an unrelated task needing its own lease.
                # Only a current typed replan refusal opens the unbound route.
                governance = probe_governance(request["todo_id"])
                if (governance.get("error_code") == "turn_todo_selection_deferred"
                        and (governance.get("selection_rejection") or {}).get("reason_code") == "autonomous_replan"):
                    governance = probe_governance(None)
                self.store.append_event(session_id, turn_id, kind="native_loopx.replan_result", payload=governance)
                if governance.get("status") != "committed" or governance.get("result_kind") != "replan_required":
                    probe = self.report_selection_recovery(original, probe_turn=self.store.load_turn(session_id, turn_id))
                    try:
                        if probe is None or "probe" not in probe:
                            raise ValueError("no canonical admission probe")
                        self._admit(request, turn=self.store.load_turn(session_id, turn_id), selection_recovery=probe)
                    except (OSError, ValueError, RuntimeError):
                        retained = selection_recovery["original"]["response"]["native_loopx"]
                        self.store.append_event(session_id, turn_id, kind="native_loopx.result", payload=retained)
                        return {"schema_version": "loopx_chat_agent_response_v0", "message": "原生重规划尚未完成；保留原报告及执行证据。",
                            "proposals": [], "gate": None, "native_loopx": retained}
                    selection_recovery = probe
                governance_work = False
            if report_work:
                # Portfolio collection can take minutes and legitimately see
                # registry updates. Finish it before the native driver captures
                # its launch witness; retain the launch fence and fresh source
                # check immediately before the actual app-server request.
                current()
                report_context = reports.prepare(request, adapter,
                    authorization_scope_id=admitted.get("observed_scope_id"))
                current()
            def steward_validator(_plan: Mapping[str, Any], candidate_result: Mapping[str, Any]) -> dict[str, Any]:
                if steward_actions_state.get("validation_error"):
                    return {"status": "failed", "validator_kind": "callback",
                        "summary": str(steward_actions_state["validation_error"]),
                        "recovery_kind": "repair_required"}
                if candidate_result.get("result_kind") in {"validated_progress", "validated_completion"}:
                    return {"status": "passed", "validator_kind": "callback",
                        "summary": "steward advancement actions passed validation"}
                return {"status": "passed", "validator_kind": "callback",
                    "summary": "steward advancement non-completion result recorded"}

            def run_primary_turn() -> None:
                result.clear()
                handle_turn_command(args, registry_path=self.controller.registry_path,
                    runtime_root_arg=str(self.controller.coordination_runtime_root),
                    output_format=lambda *_a, **_kw: "json",
                    print_payload=lambda value, *_a: result.update(value),
                    native_host_runner=run, native_session_binding_resolver=session_binding,
                    native_execution_preparer=prepare_native_task if not report_work else None,
                    **({"native_completion_lease_resolver": report_lease_proof,
                        "native_task_validator": self._report_validator(request)} if report_work else
                       {"native_task_validator": managed_turn.validator(request, turn_id=turn_id),
                        "native_completion_lease_resolver": lambda: self._task_lease_proof(managed_lease_request, managed_lease)} if managed_turn else
                       {"native_task_validator": steward_validator,
                        "native_completion_lease_resolver": lambda: self._task_lease_proof(managed_lease_request, managed_lease)} if steward_advancement else {}))

            run_primary_turn()
            if (steward_advancement and request.get("todo_id") and not request.get("resume_turn_key")
                    and _is_zero_effect_replan_selection_deferral(result)):
                governance_args = type(args)(**vars(args))
                governance_args.todo_id, governance_args.validation_command_json = None, None
                governance_args.turn_instance_id = request["turn_instance_id"] + ".replan"
                governance: dict[str, Any] = {}
                governance_work = True
                try:
                    handle_turn_command(governance_args, registry_path=self.controller.registry_path,
                        runtime_root_arg=str(self.controller.coordination_runtime_root),
                        output_format=lambda *_a, **_kw: "json",
                        print_payload=lambda value, *_a: governance.update(value),
                        native_host_runner=run, native_session_binding_resolver=session_binding)
                finally:
                    governance_work = False
                self.store.append_event(session_id, turn_id, kind="native_loopx.replan_result", payload=governance)
                replan_cleared = (
                    governance.get("status") == "committed"
                    and governance.get("result_kind") == "replan_required"
                ) or (
                    governance.get("status") == "stopped"
                    and isinstance(governance.get("host_result"), Mapping)
                    and governance["host_result"].get("classification") == "report_replan_not_admitted"
                    and self._cleared_replan_guard(actor) is not None
                )
                if replan_cleared:
                    run_primary_turn()
                elif isinstance(governance.get("effects"), Mapping):
                    merged_effects = dict(result.get("effects") or {})
                    for key in ("host_invoked", "state_written", "quota_spent", "scheduler_acknowledged"):
                        if governance["effects"].get(key) is True:
                            merged_effects[key] = True
                    result["effects"] = merged_effects
        if preparation_status is not None:
            # Validation stopped preparation before the claim or model call.
            result["status"] = preparation_status
            if preparation_status == "stopped":
                result.update(ok=True, result_kind="user_action_required")
        try:
            self.store.append_event(session_id, turn_id, kind="native_loopx.result", payload=result)
        except OSError:
            # The native transaction already owns execution and settlement.
            # A lost Chat append acknowledgment must not discard its answer
            # when this exact committed event is independently readable.
            # Reuse the store's ordered readback without re-entering native work.
            if result.get("ok") is not True or result.get("status") != "committed":
                raise
            recorded = [row for row in self.store.events_after(session_id, turn_id, None)
                if row["kind"] == "native_loopx.result"]
            if len(recorded) != 1 or recorded[0]["payload"] != result:
                raise
        if request.get("work_kind") == "report" and result.get("status") == "committed":
            from ..periodic_report.private_generation import attempt_directory, validate_private_generation
            subscription, window = reports.current_window(report_owner_key)
            if not subscription or not window or window["window_id"] != request["report_window_id"]:
                raise ValueError("report publication was cancelled after native settlement")
            folder = attempt_directory(self.controller.coordination_runtime_root, window)
            generation = validate_private_generation(folder)
            delivered = reports.publish(session_id, window) if local_session else False
            if local_session and not delivered:
                return {"schema_version": "loopx_chat_agent_response_v0", "message": "本期报告已生成，原会话送达条件尚未满足；保留原结果等待核验。",
                    "proposals": [], "gate": None, "native_loopx": result}
            return {"schema_version": "loopx_chat_agent_response_v0", "message": (folder / "report.md").read_text(encoding="utf-8"),
                "proposals": [], "gate": None, "native_loopx": result,
                "periodic_report": {"window_id": window["window_id"], "generation_id": generation["generation_id"],
                    "delivery_verified": delivered}}
        user_action_receipts: list[dict[str, Any]] = []
        if steward_advancement and result.get("status") == "committed" and steward_actions_state["user_actions"]:
            observed = self._observe(actor)
            with self.controller.lock:
                self._admit(request, turn=self.store.load_turn(session_id, turn_id),
                            observed=observed, selection_recovery=selection_recovery)
                user_action_receipts = self._apply_steward_user_actions(
                    actor, steward_actions_state["user_actions"], request["turn_instance_id"])
        owner_report = (steward_actions_state["owner_report"]
            if steward_advancement and result.get("status") == "committed" else None)
        if owner_report:
            from ...registry import atomic_write_json
            from .steward_monitor import steward_monitor_delivery_dir
            delivery_path = steward_monitor_delivery_dir(self.controller.coordination_runtime_root, actor["goal_id"]) / f"{turn_id}.json"
            atomic_write_json(delivery_path, {
                "schema_version": "steward_monitor_report_delivery_v0",
                "goal_id": actor["goal_id"],
                "agent_id": actor["agent_id"],
                "session_id": actor["session_id"],
                "turn_id": turn_id,
                "turn_instance_id": request["turn_instance_id"],
                "todo_id": (managed_lease_request or request).get("todo_id"),
                "markdown": owner_report,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "delivery_status": "pending",
                "deliveries": {},
            })
        committed_handoff = (steward_actions_state["context_handoff"]
            if steward_advancement and result.get("status") == "committed" else None)
        return {"schema_version": "loopx_chat_agent_response_v0", "message":
            "当前没有可执行且已配置独立验收的原生任务，尚未启动模型；请检查原任务的验收配置。" if managed_turn and managed_turn.validation_unavailable else
            (owner_report or managed_answer or "本轮任务已完成结果回写。") if result.get("status") == "committed" else
            "本轮等待后续执行条件。" if result.get("status") == "stopped" and result.get("result_kind") == "wait" else
            "本轮任务尚未完成；执行状态已保留。",
            "proposals": [], "gate": None, "native_loopx": result,
            **({"context_handoff": committed_handoff} if committed_handoff else {}),
            **({"steward_owner_report_queued": True} if owner_report else {}),
            **({"steward_user_action_receipts": user_action_receipts} if user_action_receipts else {})}
