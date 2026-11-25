"""Steward followup/governance context projection, action validation, and prompt helpers."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import json
import re
import shlex
from typing import Protocol, cast

from ...control_plane.effect_runtime import EffectRuntimeRejected
from .steward_turn_common import (
    StewardTurnHostProtocol,
    _USER_ACTION_DEDUPE_KEY,
    as_dict,
    as_dict_list,
    as_mapping,
    as_optional_dict,
    as_str,
)

_GOVERNANCE_CONTRACT_REMINDER = (
    "\nGovernance contract reminder: Settle the pending autonomous replan by returning `result_kind='replan_required'` (or `'wait'` only if required evidence is genuinely unavailable). "
    "If you emit `delivery_outcome='outcome_gap'`, you MUST also supply `progress_result_class='blocked'`, `progress_blocker_id`, and `progress_evidence_id`; "
    "otherwise use `'outcome_progress'` or omit `delivery_outcome`. "
    "Keep each `work_evidence` and `validation_evidence` item <=200 chars (1-8 public-safe items, <=1200 chars total per field) with zero local file paths. "
    "When `Steward governance replan context` supplies `writeback`: set `agent_vision_json` (JSON string of `goal_vision_replan_contract_v0`) keeping total counted text <=1400 chars (hard cap 1800; keep each `stride_decision` plan/reason/probe field <=60 chars), and omit `vision_unchanged_reason`. "
    "If keeping `current_agent_vision.vision_patch` identical, set `path_delta_mode='unchanged'` and `path_delta.outcome='continue'` (or `'no_change'`); if updating any `vision_patch` field or creating an initial baseline, set `path_delta_mode='material_replan'` and `path_delta.outcome='replan'`. "
    "Populate `path_delta.evidence_refs` from `review.recent_evidence` (1-3 IDs), `todo_delta` using `retain:<todo_id>` only for verified IDs in `open_agent_todos` (<=8 items), and when `writeback.stride_control.required_on_periodic_review` is true include `agent_vision.stride_decision` with exact `schema_version`, `strategy_version`, `reference_context_id`, and `reference_obligation_id` from `stride_control` (plus `previous_review` when `previous_decision_ref` is non-null)."
)

_STEWARD_ADVANCEMENT_INSTRUCTIONS = (
    "\nSteward advancement instructions: Evaluate the monitor prompt and signal snapshot against authorized recipients (`context_delegation.targets`), prior receipts (`prior_handoff_receipts`, avoid duplicate delivery for unchanged PR@headRefOid), and open user todos (`open_user_todos`). "
    "When `signal_snapshot.repository_snapshot.items` is present, each PR carries `mergeable`, `checks_summary`, `bypassed_gates`, `stalled_since`, `author_replied_after_request_changes`, optional `stalled_bucket` (`awaiting_maintainer_reply` | `under_24h` | `24h_to_72h` | `72h_plus` | `7d_plus`), and `review_summary` (`effective_verdict`, `has_review_conflict`, `stale_review_count`, `latest_request_changes_review_id`, `stalled_since`, `author_replied_after_request_changes`, `exact_head_reviews` with `reviewer_declaration`, `valid_verdict_review`, `author_owned_fallback`). Keep triage read-only; never execute merges here. "
    "You may include optional top-level JSON fields alongside the normal kernel fields: "
    "`context_handoff` (single object or array <=32 of {goal_id, agent_id, brief} with `schema_version='collaboration_brief_v0'` and `inputs=[]` when referencing URLs/PRs, restricted to `context_delegation.targets`), "
    "`steward_owner_report` (optional non-empty Markdown string <=24000 chars or `{markdown}` delivered once directly to the bound Steward App and Feishu conversation; omit or set `null` when silent/no report should be sent), "
    "and `steward_user_actions` (array <=16 of {dedupe_key, priority:'P0'|'P1'|'P2', text}; do NOT emit per-PR `steward_user_actions` when the monitor prompt asks for an aggregated report). Keep each `work_evidence` and `validation_evidence` item <=200 chars (1-8 items, <=1200 chars total per field). Set `result_kind='validated_completion'` when triage/handoff/report/user-action preparation is complete.\n"
    "Steward monitor & dedupe context (data):\n"
)


class _CalendarTasksProtocol(Protocol):
    def admission(self, request: Mapping[str, object]) -> dict[str, object]: ...


class ExecutionReportServiceProtocol(Protocol):
    """Periodic report service surface used during Turn execution and delivery."""

    def current_window(self, owner_key: str) -> tuple[dict[str, object] | None, dict[str, object] | None]: ...

    def prepare(
        self,
        request: Mapping[str, object],
        adapter: object,
        *,
        authorization_scope_id: object,
    ) -> str: ...

    def freeze(self, request: Mapping[str, object], candidate: Mapping[str, object]) -> object: ...

    def publish(self, session_id: str, window: Mapping[str, object]) -> bool: ...


def steward_followup_context(
    host: StewardTurnHostProtocol,
    actor: Mapping[str, object],
    session_id: str,
    request: Mapping[str, object],
    authority: Mapping[str, object],
) -> dict[str, object]:
    """Assemble selected-todo, monitor snapshot, open user todos, and handoff receipt dedupe context."""
    from ...control_plane.todos.list_readback import list_goal_todos
    from .steward_monitor import FOLLOWUP_TARGET_PREFIX, steward_monitor_snapshot_path

    goal_id = str(actor["goal_id"])
    agent_id = str(actor["agent_id"])
    todo_id = as_mapping(authority.get("_work_identity")).get("todo_id") or request.get("todo_id")
    agent_todos = as_dict_list(
        as_mapping(
            list_goal_todos(
                registry_path=host.controller.registry_path,
                runtime_root_arg=str(host.controller.coordination_runtime_root),
                goal_id=goal_id,
                agent_id=agent_id,
                role="agent",
            )
        ).get("todos")
    )
    selected = next((row for row in agent_todos if row.get("todo_id") == todo_id), None)
    payload: dict[str, object] = {"selected_todo": selected}
    target_key = as_str((selected or {}).get("target_key"))
    if target_key.startswith(FOLLOWUP_TARGET_PREFIX):
        source_id = target_key.removeprefix(FOLLOWUP_TARGET_PREFIX)
        payload["source_monitor"] = next((row for row in agent_todos if row.get("todo_id") == source_id), None)
        snap = steward_monitor_snapshot_path(host.controller.coordination_runtime_root, goal_id, source_id)
        if snap.is_file():
            try:
                payload["signal_snapshot"] = json.loads(snap.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                payload["signal_snapshot"] = None
    user_todos = as_dict_list(
        as_mapping(
            list_goal_todos(
                registry_path=host.controller.registry_path,
                runtime_root_arg=str(host.controller.coordination_runtime_root),
                goal_id=goal_id,
                role="user",
            )
        ).get("todos")
    )
    payload["open_user_todos"] = [
        {k: row.get(k) for k in ("todo_id", "status", "priority", "text", "updated_at")}
        for row in user_todos
        if row.get("status") == "open"
    ][:40]
    prior: list[dict[str, object]] = []
    for msg in reversed(host.store.messages(session_id)[-30:]):
        tid = msg.get("turn_id")
        if not isinstance(tid, str) or not tid:
            continue
        turn_row = host.store.load_turn(session_id, tid)
        resp = as_mapping((turn_row or {}).get("response"))
        receipts: list[dict[str, object]] = as_dict_list(resp.get("context_handoff_receipts"))
        single = as_optional_dict(resp.get("context_handoff_receipt"))
        if single is not None:
            receipts.append(single)
        for receipt in receipts:
            prior.append({k: receipt.get(k) for k in ("goal_id", "agent_id", "source_id", "request_id", "delivered_at")})
    payload["prior_handoff_receipts"] = prior[:40]
    return payload


def governance_replan_context(
    host: StewardTurnHostProtocol,
    actor: Mapping[str, object],
    authority: Mapping[str, object],
) -> dict[str, object]:
    """Project public-safe review/writeback/vision context for text-only Steward replanning."""
    from ...cli import build_parser
    from ...cli_commands.quota_native_context import read_replan_view
    from ...control_plane.goals.goal_frontier.semantic_history import latest_agent_vision_from_status_payload
    from ...control_plane.todos.list_readback import list_goal_todos
    from .steward_commands import NativeStewardCommandError, native_command

    model_input = as_optional_dict(authority.get("_model_input"))
    detail_ref = as_optional_dict(model_input.get("detail_ref")) if model_input is not None else None
    detail_packet = as_optional_dict(detail_ref.get("replan_action_packet")) if detail_ref is not None else None
    auth_packet = as_optional_dict(authority.get("replan_action_packet"))
    packet = (
        detail_packet
        if detail_packet is not None and ("review" in detail_packet or "writeback" in detail_packet)
        else auth_packet
    )

    def _read_stage(cmd_str: object) -> dict[str, object] | None:
        if not isinstance(cmd_str, str) or not cmd_str.strip():
            return None
        try:
            tokens = shlex.split(cmd_str)
            q_idx = tokens.index("quota")
            parsed = build_parser().parse_args(tokens[q_idx:])
            view = read_replan_view(parsed)
            if isinstance(view, Mapping) and view.get("ok") is True:
                return as_dict(view)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, EffectRuntimeRejected, SystemExit):
            return None
        return None

    payload: dict[str, object] = {}
    if packet is not None:
        review_view = _read_stage(packet.get("review"))
        if review_view is not None:
            core_goal = as_optional_dict(review_view.get("core_goal"))
            evidence_raw = review_view.get("evidence")
            recent_evidence = (
                list(evidence_raw)[:12]
                if isinstance(evidence_raw, Sequence) and not isinstance(evidence_raw, (str, bytes))
                else []
            )
            payload["review"] = {
                "obligation_id": review_view.get("obligation_id"),
                "context_id": review_view.get("context_id"),
                "objective": core_goal.get("objective") if core_goal is not None else None,
                "triggers": review_view.get("triggers"),
                "recent_evidence": recent_evidence,
                "decision_followthrough": review_view.get("decision_followthrough"),
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
    goal_id = str(actor["goal_id"])
    agent_id = str(actor["agent_id"])
    try:
        status_payload = native_command(host.controller, ["status", "--goal-id", goal_id])
        latest_vision = latest_agent_vision_from_status_payload(status_payload, goal_id=goal_id, agent_id=agent_id)
        if isinstance(latest_vision, Mapping):
            typed_vision = as_dict(latest_vision)
            payload["current_agent_vision"] = {
                k: typed_vision.get(k)
                for k in ("schema_version", "state", "vision_patch", "todo_delta")
                if typed_vision.get(k) is not None
            }
    except (NativeStewardCommandError, OSError, ValueError, KeyError, TypeError, RuntimeError):
        pass
    try:
        agent_todos = as_dict_list(
            as_mapping(
                list_goal_todos(
                    registry_path=host.controller.registry_path,
                    runtime_root_arg=str(host.controller.coordination_runtime_root),
                    goal_id=goal_id,
                    role="agent",
                )
            ).get("todos")
        )
        payload["open_agent_todos"] = [
            {k: row.get(k) for k in ("todo_id", "task_class", "priority", "target_key", "title")}
            for row in agent_todos
            if row.get("status") == "open"
        ][:16]
    except (NativeStewardCommandError, OSError, ValueError, KeyError, TypeError, RuntimeError):
        pass
    return payload


def normalize_steward_actions(
    candidate: Mapping[str, object],
    context: Mapping[str, object],
) -> dict[str, object]:
    """Validate optional context_handoff, steward_user_actions, and steward_owner_report fields."""
    from ...control_plane.collaboration.inbox import normalize_handoff

    state: dict[str, object] = {
        "context_handoff": None,
        "user_actions": [],
        "owner_report": None,
        "validation_error": None,
    }
    delegation = as_mapping(context.get("context_delegation"))
    allowed_targets = {
        (str(row["goal_id"]), str(row["agent_id"]))
        for row in as_dict_list(delegation.get("targets"))
        if row.get("goal_id") and row.get("agent_id")
    }
    raw_handoff = candidate.get("context_handoff")
    if raw_handoff is not None:
        try:
            normalized = normalize_handoff(raw_handoff)
        except ValueError as exc:
            state["validation_error"] = f"invalid context_handoff: {exc}"
            return state
        if normalized:
            items = as_dict_list(normalized) if isinstance(normalized, list) else [as_dict(normalized)]
            for item in items:
                pair = (str(item.get("goal_id") or ""), str(item.get("agent_id") or ""))
                if pair not in allowed_targets:
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
        for raw_item in cast(list[object], raw_actions):
            if not isinstance(raw_item, Mapping):
                state["validation_error"] = "each steward_user_actions entry must be an object"
                return state
            action_item = as_mapping(raw_item)
            key = str(action_item.get("dedupe_key") or "").strip().lower()
            text = " ".join(str(action_item.get("text") or "").split()).strip()
            prio = str(action_item.get("priority") or "P1").strip().upper()
            if (
                not _USER_ACTION_DEDUPE_KEY.fullmatch(key)
                or key in seen_keys
                or not text
                or len(text) > 800
                or prio not in {"P0", "P1", "P2"}
            ):
                state["validation_error"] = "invalid steward_user_actions entry"
                return state
            seen_keys.add(key)
            cleaned.append({"dedupe_key": key, "priority": prio, "text": text})
        state["user_actions"] = cleaned
    raw_report = candidate.get("steward_owner_report")
    if raw_report is not None:
        markdown = as_mapping(raw_report).get("markdown") if isinstance(raw_report, Mapping) else raw_report
        if not isinstance(markdown, str):
            state["validation_error"] = "steward_owner_report must be a markdown string or {markdown}"
            return state
        normalized_report = markdown.strip()
        if not normalized_report or len(normalized_report) > 24000:
            state["validation_error"] = "steward_owner_report markdown must be 1..24000 characters"
            return state
        state["owner_report"] = normalized_report
    return state


def apply_steward_user_actions(
    host: StewardTurnHostProtocol,
    actor: Mapping[str, object],
    actions: Sequence[Mapping[str, str]],
    turn_instance_id: str,
) -> list[dict[str, object]]:
    """Idempotently add or update dedupe-keyed user_action Todos via native_command."""
    if not actions:
        return []
    from ...control_plane.todos.list_readback import list_goal_todos
    from .steward_commands import native_command

    goal_id = str(actor["goal_id"])
    agent_id = str(actor["agent_id"])
    existing = as_dict_list(
        as_mapping(
            list_goal_todos(
                registry_path=host.controller.registry_path,
                runtime_root_arg=str(host.controller.coordination_runtime_root),
                goal_id=goal_id,
                role="user",
            )
        ).get("todos")
    )
    open_by_key: dict[str, tuple[dict[str, object], str]] = {}
    for row in existing:
        if row.get("status") != "open":
            continue
        title = str(row.get("title") or "").strip() or re.sub(
            r"^\[p\d\]\s*", "", str(row.get("text") or ""), flags=re.IGNORECASE
        ).strip()
        if title.startswith("[") and "]" in title:
            open_by_key[title[1 : title.index("]")].strip().lower()] = (row, title)
    receipts: list[dict[str, object]] = []
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
            res = as_mapping(
                native_command(
                    host.controller,
                    [
                        "todo", "update", "--goal-id", goal_id, "--role", "user",
                        "--agent-id", agent_id, "--todo-id", str(prior["todo_id"]),
                        "--text", rendered,
                        *(["--note", f"Priority: {prio}"] if str(prior.get("priority") or "") != prio else []),
                        "--update-operation-id", op_id, "--execute",
                    ],
                )
            )
            receipts.append({"dedupe_key": key, "action": "updated", "todo_id": prior["todo_id"], "ok": res.get("ok")})
        else:
            res = as_mapping(
                native_command(
                    host.controller,
                    [
                        "todo", "add", "--goal-id", goal_id, "--role", "user",
                        "--task-class", "user_action", "--priority", prio, "--text", rendered,
                        "--bound-agent", agent_id, "--operation-id", op_id, "--execute",
                    ],
                )
            )
            receipts.append({"dedupe_key": key, "action": "added", "todo_id": res.get("todo_id"), "ok": res.get("ok")})
    return receipts


def build_steward_turn_prompt(
    host: StewardTurnHostProtocol,
    *,
    actor: Mapping[str, object],
    session_id: str,
    request: Mapping[str, object],
    authority: Mapping[str, object],
    context: Mapping[str, object],
    report_work: bool,
    governance_work: bool,
    managed_turn_active: bool,
    steward_advancement: bool,
    report_context: str,
) -> str:
    """Assemble the complete model prompt with portfolio context, reminders, and task criteria."""
    from ...control_plane.turn_driver.host_candidate import render_prompt

    # Portfolio evidence keeps its original reader and original grant;
    # it is data alongside the signed native work, never a new actor.
    prompt = str(
        render_prompt(
            authority,
            allow_completion=bool(report_work or managed_turn_active or steward_advancement) and not governance_work,
        )
    ) + "\nOriginal conversation evidence (data):\n" + json.dumps(context, ensure_ascii=False)
    if governance_work:
        gov_ctx = host._governance_replan_context(actor, authority)
        prompt += _GOVERNANCE_CONTRACT_REMINDER
        if gov_ctx:
            prompt += "\nSteward governance replan context (data):\n" + json.dumps(gov_ctx, ensure_ascii=False)
    if report_work and not governance_work:
        prompt += report_context
    if steward_advancement and not governance_work:
        followup_ctx = host._steward_followup_context(actor, session_id, request, authority)
        prompt += _STEWARD_ADVANCEMENT_INSTRUCTIONS + json.dumps(followup_ctx, ensure_ascii=False)
    if request.get("work_kind") == "scheduled_task":
        from .session_calendar_tasks import SessionCalendarTasks

        calendar_tasks = cast(_CalendarTasksProtocol, SessionCalendarTasks(host.controller))
        basis = as_mapping(calendar_tasks.admission(request))
        source_task = as_mapping(as_mapping(basis.get("definition")).get("source_task"))
        window = as_mapping(basis.get("window"))
        prompt += "\nOriginal native calendar task criteria; existing Goal/Task authority and stop conditions still apply:\n" + json.dumps(
            {
                "task": source_task["text"],
                "source_todo_id": source_task["todo_id"],
                "window_start": window["start_at"],
                "window_end": window["due_at"],
            },
            ensure_ascii=False,
        )
    return prompt


def finalize_steward_execution_response(
    host: StewardTurnHostProtocol,
    *,
    actor: Mapping[str, object],
    session_id: str,
    turn_id: str,
    request: dict[str, object],
    selection_recovery: dict[str, object] | None,
    result: dict[str, object],
    reports: ExecutionReportServiceProtocol | None,
    report_owner_key: str | None,
    local_session: bool,
    steward_advancement: bool,
    steward_actions_state: Mapping[str, object],
    managed_turn_validation_unavailable: bool,
    managed_answer: str,
    managed_lease_request: Mapping[str, object] | None,
) -> dict[str, object]:
    """Finalize report publication or Steward user-action/owner-report queuing and build Chat response."""
    if request.get("work_kind") == "report" and result.get("status") == "committed":
        from ..periodic_report.private_generation import attempt_directory, validate_private_generation

        assert reports is not None and report_owner_key is not None
        subscription, window = reports.current_window(report_owner_key)
        if not subscription or not window or window.get("window_id") != request.get("report_window_id"):
            raise ValueError("report publication was cancelled after native settlement")
        folder = attempt_directory(host.controller.coordination_runtime_root, window)
        generation = as_mapping(validate_private_generation(folder))
        delivered = reports.publish(session_id, window) if local_session else False
        if local_session and not delivered:
            return {
                "schema_version": "loopx_chat_agent_response_v0",
                "message": "本期报告已生成，原会话送达条件尚未满足；保留原结果等待核验。",
                "proposals": [],
                "gate": None,
                "native_loopx": result,
            }
        return {
            "schema_version": "loopx_chat_agent_response_v0",
            "message": (folder / "report.md").read_text(encoding="utf-8"),
            "proposals": [],
            "gate": None,
            "native_loopx": result,
            "periodic_report": {
                "window_id": window["window_id"],
                "generation_id": generation["generation_id"],
                "delivery_verified": delivered,
            },
        }
    user_action_receipts: list[dict[str, object]] = []
    raw_user_actions = steward_actions_state.get("user_actions")
    typed_user_actions: list[dict[str, str]] = (
        [
            {str(k): str(v) for k, v in as_mapping(item).items()}
            for item in cast(list[object], raw_user_actions)
            if isinstance(item, Mapping)
        ]
        if isinstance(raw_user_actions, list)
        else []
    )
    if steward_advancement and result.get("status") == "committed" and typed_user_actions:
        observed = host._observe(actor)
        with host.controller.lock:
            host._admit(
                request,
                turn=host.store.load_turn(session_id, turn_id),
                observed=observed,
                selection_recovery=selection_recovery,
            )
            user_action_receipts = host._apply_steward_user_actions(
                actor,
                typed_user_actions,
                str(request["turn_instance_id"]),
            )
    raw_owner_report = steward_actions_state.get("owner_report")
    owner_report = (
        raw_owner_report
        if steward_advancement and result.get("status") == "committed" and isinstance(raw_owner_report, str) and raw_owner_report
        else None
    )
    if owner_report:
        from ...registry import atomic_write_json
        from .steward_monitor import steward_monitor_delivery_dir

        delivery_path = (
            steward_monitor_delivery_dir(host.controller.coordination_runtime_root, str(actor["goal_id"])) / f"{turn_id}.json"
        )
        atomic_write_json(
            delivery_path,
            {
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
            },
        )
    committed_handoff = (
        steward_actions_state.get("context_handoff")
        if steward_advancement and result.get("status") == "committed"
        else None
    )
    return {
        "schema_version": "loopx_chat_agent_response_v0",
        "message": (
            "当前没有可执行且已配置独立验收的原生任务，尚未启动模型；请检查原任务的验收配置。"
            if managed_turn_validation_unavailable
            else (owner_report or managed_answer or "本轮任务已完成结果回写。")
            if result.get("status") == "committed"
            else "本轮等待后续执行条件。"
            if result.get("status") == "stopped" and result.get("result_kind") == "wait"
            else "本轮任务尚未完成；执行状态已保留。"
        ),
        "proposals": [],
        "gate": None,
        "native_loopx": result,
        **({"context_handoff": committed_handoff} if committed_handoff else {}),
        **({"steward_owner_report_queued": True} if owner_report else {}),
        **({"steward_user_action_receipts": user_action_receipts} if user_action_receipts else {}),
    }
