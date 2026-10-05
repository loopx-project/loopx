"""Normalization owner for built-in typed Chat actions."""

from __future__ import annotations

from datetime import timedelta
import re
from typing import Any, Mapping

from .agent_registry import registered_agent_ids_for_goal
from .control_plane.content_digest import BARE_SHA256_PATTERN
from .control_plane.runtime.time import now_utc, parse_timestamp, utc_isoformat
from .control_plane.todos.contract import require_supported_todo_resume_when
from .registry import registry_goals


_AUTHORITY_PRINCIPAL = re.compile(r"^[a-z][a-z0-9._-]{0,30}:[A-Za-z0-9._:-]{1,200}$")


class ChatActionNormalizationMixin:
    """Normalize typed action inputs without owning effects or persistence."""

    def _normalize(
        self, action_kind: str, parameters: Mapping[str, Any]
    ) -> dict[str, Any]:
        # Imported lazily to retain the compatibility helpers in chat_actions
        # without introducing a module initialization cycle.
        from .chat_actions import (
            ProtectedActionGate,
            _digest,
            _normalize_cadence,
            _opaque,
            _text,
        )

        if action_kind == "operation.execute":
            values = self._allowed_parameters(
                parameters,
                allowed={
                    "schema_version",
                    "goal_id",
                    "agent_id",
                    "domain",
                    "operation_kind",
                    "operation_schema",
                    "payload_ref",
                    "payload",
                    "payload_digest",
                    "projection",
                    "destination_account_ref",
                    "expires_at",
                    "authorized_principals",
                    "executor",
                    "source_route",
                },
            )
            if values.get("schema_version") != "loopx_operation_request_v0":
                raise ValueError(
                    "operation.execute requires loopx_operation_request_v0"
                )
            goal_id = _opaque(values.get("goal_id"), field="goal_id")
            goal = self._goal(goal_id)
            agent_id = _opaque(values.get("agent_id"), field="agent_id")
            if agent_id not in registered_agent_ids_for_goal(goal):
                raise ValueError("operation agent_id must be registered for the Goal")
            payload = values.get("payload")
            if not isinstance(payload, Mapping):
                raise ValueError("operation payload must be an object")
            payload_digest = str(values.get("payload_digest") or "").strip()
            if not BARE_SHA256_PATTERN.fullmatch(payload_digest):
                raise ValueError("operation payload_digest must be lowercase SHA-256")
            if _digest(payload) != payload_digest:
                raise ValueError("operation payload_digest does not match payload")

            projection = values.get("projection")
            if not isinstance(projection, Mapping):
                raise ValueError("operation projection must be an object")
            projection_values = self._allowed_parameters(
                projection,
                allowed={
                    "schema_version",
                    "title",
                    "subtitle",
                    "focus",
                    "fields",
                    "warning",
                    "simulated",
                },
            )
            if (
                projection_values.get("schema_version")
                != "loopx_operation_projection_v0"
            ):
                raise ValueError(
                    "operation projection requires loopx_operation_projection_v0"
                )
            normalized_projection: dict[str, Any] = {
                "schema_version": "loopx_operation_projection_v0",
                "title": _text(
                    projection_values.get("title"),
                    field="projection.title",
                    limit=80,
                ),
                "subtitle": _text(
                    projection_values.get("subtitle"),
                    field="projection.subtitle",
                    limit=120,
                ),
                "focus": _text(
                    projection_values.get("focus"),
                    field="projection.focus",
                    limit=120,
                ),
                "warning": _text(
                    projection_values.get("warning"),
                    field="projection.warning",
                    limit=300,
                ),
            }
            if not isinstance(projection_values.get("simulated"), bool):
                raise ValueError("operation projection simulated must be true or false")
            normalized_projection["simulated"] = projection_values["simulated"]
            raw_fields = projection_values.get("fields")
            if not isinstance(raw_fields, list) or not 1 <= len(raw_fields) <= 12:
                raise ValueError("operation projection fields must contain 1-12 items")
            normalized_fields: list[dict[str, str]] = []
            for index, raw_field in enumerate(raw_fields):
                if not isinstance(raw_field, Mapping) or set(raw_field) != {
                    "label",
                    "value",
                }:
                    raise ValueError(
                        f"operation projection field {index + 1} is invalid"
                    )
                normalized_fields.append(
                    {
                        "label": _text(
                            raw_field.get("label"),
                            field=f"projection.fields[{index}].label",
                            limit=40,
                        ),
                        "value": _text(
                            raw_field.get("value"),
                            field=f"projection.fields[{index}].value",
                            limit=120,
                        ),
                    }
                )
            normalized_projection["fields"] = normalized_fields

            raw_executor = values.get("executor")
            origin_goal_ref = None
            if isinstance(raw_executor, Mapping) and raw_executor.get("kind") in {
                "agent_session",
                "managed_turn",
            }:
                from .control_plane.collaboration.goal_instance_scope import (
                    collaboration_goal_scope,
                )
                from .control_plane.effect_runtime import (
                    EffectRuntimeRejected,
                    effect_runtime_result,
                )
                from .control_plane.goals.activation import goal_is_stopped
                from .thread_agent_binding import resolve_registry_thread_agent_binding

                try:
                    executor = dict(
                        effect_runtime_result(
                            "operation.agent_executor.normalize",
                            {"executor": dict(raw_executor)},
                        )
                    )
                except EffectRuntimeRejected as exc:
                    raise ValueError(str(exc)) from exc
                binding = (
                    resolve_registry_thread_agent_binding(
                        registry_path=self.registry_path,
                        host_surface=str(executor.get("host_surface", "")),
                        thread_id=str(executor.get("thread_id", "")),
                    )
                    if executor["kind"] == "agent_session"
                    else None
                )
                if executor["kind"] == "agent_session" and (
                    binding.get("status") != "bound"
                    or (
                        binding.get("goal_id"),
                        binding.get("agent_id"),
                    )
                    != (goal_id, agent_id)
                ):
                    raise ValueError(
                        "agent operation requires the original registered session"
                    )
                if normalized_projection["simulated"] is not False:
                    raise ValueError(
                        "agent execution handoff cannot masquerade as simulation"
                    )
                with collaboration_goal_scope(
                    self.registry_path,
                    goal_id=goal_id,
                    agents=(agent_id,),
                    require_active=True,
                ) as scope:
                    if goal_is_stopped(scope.goal):
                        raise ValueError("agent operation Goal is stopped")
                    origin_goal_ref = scope.current_goal_ref
                    if executor["kind"] == "managed_turn":
                        from .control_plane.collaboration.operation_handoff import (
                            managed_operation_binding_current,
                        )

                        if not managed_operation_binding_current(
                            self.store.root.parent.parent,
                            {
                                "goal_id": goal_id,
                                "agent_id": agent_id,
                                "executor": executor,
                                "origin_goal_ref": origin_goal_ref,
                            },
                        ):
                            raise ValueError(
                                "managed operation requires its current Turn session and profile"
                            )
            elif not isinstance(raw_executor, Mapping) or set(raw_executor) != {
                "extension_id",
                "protocol",
                "permission",
                "revision",
            }:
                raise ValueError("operation executor binding is invalid")
            else:
                executor = {
                    field: _opaque(raw_executor.get(field), field=f"executor.{field}")
                    for field in ("extension_id", "protocol", "permission", "revision")
                }
            managed_source = executor.get("kind") == "managed_turn"
            if not managed_source and "source_route" in values:
                raise ValueError("source route selection requires a managed executor")
            source_route = None
            if managed_source:
                from .control_plane.effect_runtime import effect_runtime_result
                from .thread_agent_binding import (
                    collect_accepted_bindings,
                    resolve_thread_agent_binding,
                )

                selected_route = values.get("source_route")
                if isinstance(selected_route, Mapping):
                    selected_binding = resolve_thread_agent_binding(
                        goal,
                        host_surface=selected_route.get("host_surface"),
                        thread_id=selected_route.get("thread_id"),
                    )
                    selected_route = {
                        **selected_route,
                        "host_surface": selected_binding["host_surface"],
                        "thread_id": selected_binding["thread_id"],
                    }

                source_route = effect_runtime_result(
                    "operation.source_route.resolve",
                    {
                        "goal_id": goal_id,
                        "agent_id": agent_id,
                        "bindings": collect_accepted_bindings([goal]),
                        "selected_route": selected_route,
                    },
                )["source_route"]
            expires_at = parse_timestamp(
                _text(values.get("expires_at"), field="expires_at", limit=80)
            )
            if expires_at is None:
                raise ValueError("operation expires_at must be an ISO-8601 timestamp")
            current = now_utc()
            if not current < expires_at <= current + timedelta(days=7):
                raise ValueError("operation expiry must be within the next seven days")
            raw_principals = values.get("authorized_principals")
            if (
                not isinstance(raw_principals, list)
                or not 1 <= len(raw_principals) <= 20
            ):
                raise ValueError(
                    "operation authorized_principals must contain 1-20 identities"
                )
            principals: list[str] = []
            for raw_principal in raw_principals:
                principal = str(raw_principal or "").strip()
                if not _AUTHORITY_PRINCIPAL.fullmatch(principal):
                    raise ValueError(
                        "operation authorized_principals must use provider:subject values"
                    )
                if principal not in principals:
                    principals.append(principal)
            return {
                "schema_version": "loopx_operation_request_v0",
                "goal_id": goal_id,
                "agent_id": agent_id,
                "domain": _opaque(values.get("domain"), field="domain"),
                "operation_kind": _opaque(
                    values.get("operation_kind"), field="operation_kind"
                ),
                "operation_schema": _opaque(
                    values.get("operation_schema"), field="operation_schema"
                ),
                "payload_ref": _opaque(values.get("payload_ref"), field="payload_ref"),
                "payload": dict(payload),
                "payload_digest": payload_digest,
                "projection": normalized_projection,
                "projection_digest": _digest(normalized_projection),
                "destination_account_ref": _opaque(
                    values.get("destination_account_ref"),
                    field="destination_account_ref",
                ),
                "expires_at": utc_isoformat(expires_at),
                "authorized_principals": principals,
                "executor": executor,
                **(
                    {"source_route": source_route}
                    if executor.get("kind") == "managed_turn"
                    else {}
                ),
                **(
                    {"origin_goal_ref": origin_goal_ref}
                    if origin_goal_ref is not None
                    else {}
                ),
            }
        if action_kind == "todo.create":
            values = self._allowed_parameters(
                parameters,
                allowed={
                    "goal_id",
                    "text",
                    "priority",
                    "agent_id",
                    "endpoint_id",
                    "start_execution",
                },
            )
            goal_id = _opaque(values.get("goal_id"), field="goal_id")
            self._goal(goal_id)
            result = {
                "goal_id": goal_id,
                "text": _text(values.get("text"), field="text", limit=400),
            }
            if "priority" in values:
                result["priority"] = values["priority"]
            if values.get("endpoint_id"):
                endpoint_id = _opaque(values.get("endpoint_id"), field="endpoint_id")
                result["endpoint_id"] = endpoint_id
                result["agent_id"] = self._resolve_goal_agent(goal_id, endpoint_id)
            elif values.get("agent_id"):
                agent_id = _opaque(values.get("agent_id"), field="agent_id")
                if agent_id not in registered_agent_ids_for_goal(self._goal(goal_id)):
                    raise ProtectedActionGate(
                        "agent.bind",
                        gate={
                            "kind": "agent_binding_required",
                            "summary": "所选 Agent 身份尚未绑定到这个 Goal。",
                            "next_action": "先确认 Agent 绑定预览，再继续创建 Todo。",
                            "agent_id": agent_id,
                            "goal_id": goal_id,
                        },
                    )
                result["agent_id"] = agent_id
            if values.get("start_execution") is not None:
                if not isinstance(values["start_execution"], bool):
                    raise ValueError("start_execution must be true or false")
                result["start_execution"] = values["start_execution"]
            if result.get("start_execution") and not result.get("agent_id"):
                raise ValueError("start_execution requires an assigned Agent")
            return result
        if action_kind == "todo.update":
            values = self._allowed_parameters(
                parameters,
                allowed={
                    "goal_id",
                    "todo_id",
                    "text",
                    "priority",
                    "clear_priority",
                    "status",
                    "note",
                    "agent_id",
                    "endpoint_id",
                    "operation",
                    "resume_when",
                    "successor_todo_ids",
                    "no_followup",
                },
            )
            goal_id = _opaque(values.get("goal_id"), field="goal_id")
            self._goal(goal_id)
            result: dict[str, Any] = {
                "goal_id": goal_id,
                "todo_id": _opaque(values.get("todo_id"), field="todo_id"),
            }
            operation = str(values.get("operation") or "edit").strip().lower()
            if operation not in {
                "edit",
                "reassign",
                "block",
                "defer",
                "complete",
                "successor",
            }:
                raise ValueError(
                    "todo.update operation must be edit, reassign, block, defer, complete, or successor"
                )
            result["operation"] = operation
            if values.get("text"):
                result["text"] = _text(values["text"], field="text", limit=400)
            if values.get("status"):
                status = str(values["status"]).strip().lower()
                if status not in {"open", "blocked", "deferred"}:
                    raise ValueError(
                        "todo.update status must be open, blocked, or deferred"
                    )
                result["status"] = status
            if values.get("note"):
                result["note"] = _text(values["note"], field="note", limit=600)
            if "priority" in values:
                result["priority"] = values["priority"]
            if values.get("endpoint_id"):
                endpoint_id = _opaque(values["endpoint_id"], field="endpoint_id")
                result["endpoint_id"] = endpoint_id
                result["agent_id"] = self._resolve_goal_agent(goal_id, endpoint_id)
            elif values.get("agent_id"):
                result["agent_id"] = _opaque(values["agent_id"], field="agent_id")
            if values.get("resume_when"):
                result["resume_when"] = require_supported_todo_resume_when(
                    _text(values["resume_when"], field="resume_when", limit=240)
                )
            if values.get("successor_todo_ids") is not None:
                if not isinstance(values["successor_todo_ids"], list):
                    raise ValueError("successor_todo_ids must be a list")
                result["successor_todo_ids"] = [
                    _opaque(item, field="successor_todo_ids")
                    for item in values["successor_todo_ids"][:20]
                ]
            if values.get("no_followup") is not None:
                if not isinstance(values["no_followup"], bool):
                    raise ValueError("no_followup must be true or false")
                result["no_followup"] = values["no_followup"]
            if "clear_priority" in values:
                if not isinstance(values["clear_priority"], bool):
                    raise ValueError("clear_priority must be true or false")
                result["clear_priority"] = values["clear_priority"]
            if operation != "edit" and ("priority" in result or result.get("clear_priority")):
                raise ValueError("priority changes require todo.update operation=edit")
            required_by_operation = {
                "reassign": "agent_id",
                "defer": "resume_when",
                "successor": "successor_todo_ids",
            }
            required = required_by_operation.get(operation)
            if required and not result.get(required):
                raise ValueError(f"todo.update {operation} requires {required}")
            if operation == "block" and not result.get("note"):
                raise ValueError("todo.update block requires note")
            if operation == "edit" and len(result) == 3:
                raise ValueError("todo.update requires text, priority, status, or note")
            return result
        if action_kind == "run.correct":
            values = self._allowed_parameters(
                parameters,
                allowed={"goal_id", "session_id", "message", "client_turn_id"},
            )
            goal_id = _opaque(values.get("goal_id"), field="goal_id")
            self._goal(goal_id)
            session_id = _opaque(values.get("session_id"), field="session_id")
            client_turn_id = values.get("client_turn_id")
            normalized = {
                "goal_id": goal_id,
                "session_id": session_id,
                "message": _text(values.get("message"), field="message", limit=4000),
            }
            if client_turn_id:
                normalized["client_turn_id"] = _opaque(
                    client_turn_id, field="client_turn_id"
                )
            return normalized
        if action_kind == "goal.create":
            values = self._allowed_parameters(
                parameters,
                allowed={
                    "goal_id",
                    "title",
                    "objective",
                    "completion_criteria",
                    "execution_boundary",
                    "agent_id",
                    "workspace_ref",
                    "permission",
                    "heartbeat",
                    "stop_condition",
                    "initial_todos",
                    "native_token_budget",
                },
            )
            goal_id = _opaque(values.get("goal_id"), field="goal_id")
            if any(
                str(goal.get("id") or "") == goal_id
                for goal in registry_goals(self._registry())
            ):
                raise ValueError("goal_id already exists in the active LoopX registry")
            result: dict[str, Any] = {
                "goal_id": goal_id,
                "title": _text(values.get("title"), field="title", limit=200),
            }
            if values.get("native_token_budget") is not None:
                budget = values["native_token_budget"]
                if type(budget) is not int or not 1 <= budget <= 999_999_999:
                    raise ValueError("native_token_budget must be an explicit positive token allowance")
                if values.get("agent_id") != "codex":
                    raise ValueError("native continuation requires the Codex endpoint")
                result["native_token_budget"] = budget
            for field in (
                "objective",
                "completion_criteria",
                "execution_boundary",
                "permission",
                "stop_condition",
            ):
                if values.get(field):
                    result[field] = _text(values[field], field=field, limit=1000)
            for field in ("agent_id", "workspace_ref"):
                if values.get(field):
                    result[field] = _opaque(values[field], field=field)
            if result.get("agent_id"):
                self._agent_eligibility(str(result["agent_id"]))
            if values.get("heartbeat") is not None:
                if not isinstance(values["heartbeat"], Mapping):
                    raise ValueError("heartbeat must be an object")
                heartbeat = self._allowed_parameters(
                    values["heartbeat"], allowed={"enabled", "cadence", "timezone"}
                )
                enabled = heartbeat.get("enabled")
                if not isinstance(enabled, bool):
                    raise ValueError("heartbeat.enabled must be true or false")
                normalized_heartbeat: dict[str, Any] = {"enabled": enabled}
                if heartbeat.get("cadence"):
                    normalized_heartbeat["cadence"] = _normalize_cadence(
                        heartbeat["cadence"]
                    )
                if heartbeat.get("timezone"):
                    normalized_heartbeat["timezone"] = _text(
                        heartbeat["timezone"], field="heartbeat.timezone", limit=80
                    )
                result["heartbeat"] = normalized_heartbeat
            if values.get("initial_todos") is not None:
                if not isinstance(values["initial_todos"], list):
                    raise ValueError("initial_todos must be a list")
                result["initial_todos"] = [
                    _text(item, field="initial_todos", limit=400)
                    for item in values["initial_todos"][:20]
                ]
            return result
        if action_kind == "goal.update":
            values = self._allowed_parameters(
                parameters,
                allowed={"goal_id", "title", "objective", "status", "write_scope"},
            )
            goal_id = _opaque(values.get("goal_id"), field="goal_id")
            self._goal(goal_id)
            result = {"goal_id": goal_id}
            for field in ("title", "objective", "status"):
                if values.get(field):
                    result[field] = _text(values[field], field=field, limit=1000)
            if values.get("write_scope") is not None:
                if not isinstance(values["write_scope"], list):
                    raise ValueError("write_scope must be a list")
                result["write_scope"] = [
                    _text(item, field="write_scope", limit=160)
                    for item in values["write_scope"][:20]
                ]
            if len(result) == 1:
                raise ValueError("goal.update requires at least one change")
            return result
        if action_kind == "goal.lifecycle":
            return self._normalize_goal_lifecycle(parameters)
        if action_kind == "agent.bind":
            values = self._allowed_parameters(
                parameters, allowed={"goal_id", "agent_id"}
            )
            goal_id = _opaque(values.get("goal_id"), field="goal_id")
            self._goal(goal_id)
            agent_id = _opaque(values.get("agent_id"), field="agent_id")
            self._agent_eligibility(agent_id)
            return {
                "goal_id": goal_id,
                "agent_id": agent_id,
            }
        if action_kind == "heartbeat.bind":
            values = self._allowed_parameters(
                parameters,
                allowed={
                    "goal_id",
                    "agent_id",
                    "cadence",
                    "timezone",
                    "stop_condition",
                    "notification_policy",
                    "operation",
                },
            )
            goal_id = _opaque(values.get("goal_id"), field="goal_id")
            self._goal(goal_id)
            operation = str(values.get("operation") or "bind").strip().lower()
            if operation not in {"bind", "edit", "pause", "resume", "stop"}:
                raise ValueError(
                    "heartbeat operation must be bind, edit, pause, resume, or stop"
                )
            agent_id = _opaque(values.get("agent_id"), field="agent_id")
            self._agent_eligibility(agent_id)
            result: dict[str, Any] = {
                "goal_id": goal_id,
                "agent_id": agent_id,
                "operation": operation,
            }
            if values.get("cadence"):
                result["cadence"] = _normalize_cadence(values["cadence"])
            if values.get("timezone"):
                result["timezone"] = _text(
                    values["timezone"], field="timezone", limit=80
                )
            if values.get("stop_condition"):
                result["stop_condition"] = _text(
                    values["stop_condition"], field="stop_condition", limit=160
                ).lower()
            if values.get("notification_policy"):
                result["notification_policy"] = _opaque(
                    values["notification_policy"], field="notification_policy"
                )
            if operation == "bind" and not all(
                result.get(field) for field in ("cadence", "timezone", "stop_condition")
            ):
                raise ValueError(
                    "heartbeat bind requires cadence, timezone, and stop_condition"
                )
            if operation == "edit" and len(result) == 3:
                raise ValueError("heartbeat edit requires a configuration change")
            return result
        if action_kind == "team.plan":
            from .control_plane.todos.contract import (
                TODO_ACTION_KIND_ADVANCEMENT_VALUES,
            )
            from .control_plane.work_items.governed_transition_proposal import (
                validate_steward_team_plan_preview,
            )

            values = self._allowed_parameters(
                parameters,
                allowed={"goal_id", "plan", "requested_by"},
            )
            goal_id = _opaque(values.get("goal_id"), field="goal_id")
            goal = self._goal(goal_id)
            plan = values.get("plan")
            if not isinstance(plan, Mapping):
                raise ValueError("team.plan requires the validated plan object")
            if str(plan.get("goal_id") or "") != goal_id:
                raise ValueError("team.plan Goal must match the plan's own Goal")
            # The plan is validated here against this Goal's registered Agents
            # and the host's shipped action kinds, and the apply re-validates the
            # same payload with the host's own facts before it creates anything,
            # so the stored parameters are never the thing that authorizes work.
            admitted = validate_steward_team_plan_preview(
                plan,
                registered_agent_ids=registered_agent_ids_for_goal(goal),
                supported_action_kinds=sorted(TODO_ACTION_KIND_ADVANCEMENT_VALUES),
            )
            return {
                "goal_id": goal_id,
                "plan": admitted,
                "requested_by": _opaque(
                    values.get("requested_by") or "owner", field="requested_by"
                ),
            }
        if action_kind == "monitor.create":
            values = self._allowed_parameters(
                parameters,
                allowed={
                    "goal_id",
                    "agent_id",
                    "target",
                    "target_key",
                    "cadence",
                    "timezone",
                    "stop_condition",
                    "notification_rule",
                },
            )
            goal_id = _opaque(values.get("goal_id"), field="goal_id")
            self._goal(goal_id)
            result = {
                "goal_id": goal_id,
                "agent_id": _opaque(values.get("agent_id"), field="agent_id"),
                "target": _text(values.get("target"), field="target", limit=400),
                "target_key": _opaque(values.get("target_key"), field="target_key"),
                "cadence": _normalize_cadence(values.get("cadence")),
                "timezone": _text(values.get("timezone"), field="timezone", limit=80),
            }
            stop_cond_raw = values.get("stop_condition")
            if stop_cond_raw:
                raw_text = _text(stop_cond_raw, field="stop_condition", limit=160)
                parsed_ts = parse_timestamp(raw_text)
                result["stop_condition"] = (
                    utc_isoformat(parsed_ts)
                    if parsed_ts is not None
                    else raw_text.lower()
                )
            if values.get("notification_rule"):
                result["notification_rule"] = _text(
                    values["notification_rule"], field="notification_rule", limit=400
                )
            return result
        if action_kind == "monitor.update":
            values = self._allowed_parameters(
                parameters,
                allowed={
                    "goal_id",
                    "todo_id",
                    "agent_id",
                    "operation",
                    "target",
                    "target_key",
                    "cadence",
                    "stop_condition",
                    "session_id",
                    "endpoint_id",
                },
            )
            goal_id = _opaque(values.get("goal_id"), field="goal_id")
            self._goal(goal_id)
            operation = str(values.get("operation") or "").strip().lower()
            if operation not in {"pause", "resume", "stop", "run_now", "edit"}:
                raise ValueError(
                    "monitor.update operation must be pause, resume, stop, run_now, or edit"
                )
            result = {
                "goal_id": goal_id,
                "todo_id": _opaque(values.get("todo_id"), field="todo_id"),
                "agent_id": _opaque(values.get("agent_id"), field="agent_id"),
                "operation": operation,
            }
            if values.get("endpoint_id"):
                endpoint_id = _opaque(values["endpoint_id"], field="endpoint_id")
                result["endpoint_id"] = endpoint_id
                result["agent_id"] = self._resolve_goal_agent(goal_id, endpoint_id)
            if values.get("target"):
                result["target"] = _text(values["target"], field="target", limit=400)
            if values.get("target_key"):
                result["target_key"] = _opaque(values["target_key"], field="target_key")
            if values.get("cadence"):
                result["cadence"] = _normalize_cadence(values["cadence"])
            if values.get("stop_condition"):
                raw_stop = _text(
                    values["stop_condition"], field="stop_condition", limit=160
                )
                parsed_ts = parse_timestamp(raw_stop)
                result["stop_condition"] = (
                    utc_isoformat(parsed_ts)
                    if parsed_ts is not None
                    else raw_stop.lower()
                )
            if values.get("session_id"):
                result["session_id"] = _opaque(values["session_id"], field="session_id")
            if operation == "edit" and len(result) == 4:
                raise ValueError(
                    "monitor edit requires target, target_key, cadence, or stop_condition"
                )
            return result
        if action_kind == "gate.resolve":
            values = self._allowed_parameters(
                parameters,
                allowed={"goal_id", "todo_id", "decision", "note", "agent_id"},
            )
            goal_id = _opaque(values.get("goal_id"), field="goal_id")
            self._goal(goal_id)
            decision = str(values.get("decision") or "").strip().lower()
            if decision not in {"approve", "reject", "cancel", "defer"}:
                raise ValueError(
                    "gate decision must be approve, reject, cancel, or defer"
                )
            result = {
                "goal_id": goal_id,
                "todo_id": _opaque(values.get("todo_id"), field="todo_id"),
                "decision": decision,
            }
            if values.get("note"):
                result["note"] = _text(values["note"], field="note", limit=600)
            if values.get("agent_id"):
                result["agent_id"] = _opaque(values["agent_id"], field="agent_id")
            return result
        raise ValueError(f"unsupported action_kind: {action_kind}")
