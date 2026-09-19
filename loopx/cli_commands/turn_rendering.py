from __future__ import annotations

from ..presentation.renderers.turn_envelope_markdown import (
    turn_envelope_budget_warning_lines,
)


def render_loopx_turn_plan_markdown(payload: dict[str, object]) -> str:
    if not payload.get("ok"):
        error = payload.get("error") or "invalid TurnEnvelope contract"
        return f"LoopX Turn plan failed: {error}"
    host = payload.get("host") if isinstance(payload.get("host"), dict) else {}
    route = payload.get("route") if isinstance(payload.get("route"), dict) else {}
    capability = payload.get("capability_action") if isinstance(payload.get("capability_action"), dict) else {}
    intent = capability.get("intent") if isinstance(capability.get("intent"), dict) else {}
    envelope = payload.get("turn_envelope")
    return "\n".join(
        [
            "# LoopX Turn Plan",
            f"- host: {host.get('kind')}",
            f"- execution_mode: {host.get('execution_mode')}",
            f"- route: {route.get('kind')}",
            f"- would_invoke_host: {route.get('would_invoke_host')}",
            "- side_effects: none",
            *(["- capability_action: required (not executed)",
               f"- next_command: {capability.get('command') or intent.get('command')}"] if capability else []),
            *turn_envelope_budget_warning_lines(
                envelope if isinstance(envelope, dict) else {}
            ),
        ]
    )


def render_loopx_turn_execution_markdown(payload: dict[str, object]) -> str:
    effects = payload.get("effects") if isinstance(payload.get("effects"), dict) else {}
    receipt = payload.get("receipt") if isinstance(payload.get("receipt"), dict) else {}
    validation = (
        payload.get("validation") if isinstance(payload.get("validation"), dict) else {}
    )
    recovery = (
        payload.get("recovery") if isinstance(payload.get("recovery"), dict) else {}
    )
    planned = (
        recovery.get("planned")
        if isinstance(recovery.get("planned"), dict)
        else {}
    )
    actual = (
        recovery.get("actual") if isinstance(recovery.get("actual"), dict) else {}
    )
    managed_executor = (
        payload.get("managed_executor")
        if isinstance(payload.get("managed_executor"), dict)
        else {}
    )
    output_token_budget = (
        managed_executor.get("output_token_budget")
        if isinstance(managed_executor.get("output_token_budget"), dict)
        else {}
    )
    host_failure = (
        payload.get("host_failure")
        if isinstance(payload.get("host_failure"), dict)
        else {}
    )
    return "\n".join(
        [
            "# LoopX Turn Run Once",
            f"- status: {payload.get('status')}",
            f"- result_kind: {payload.get('result_kind')}",
            *(
                [f"- execution_profile: {managed_executor['execution_profile']}"]
                if managed_executor.get("execution_profile") else []
            ),
            *(
                [f"- output_token_limit: {output_token_budget.get('max_tokens')}",
                 f"- output_token_limit_scope: {output_token_budget.get('scope')}"]
                if output_token_budget else []
            ),
            *(
                [f"- host_failure_kind: {host_failure.get('kind')}",
                 f"- host_failure_retryable: {host_failure.get('retryable')}",
                 f"- failure_reason: {payload.get('reason')}"]
                if host_failure.get("kind") == "output_budget_exhausted" else []
            ),
            f"- validation: {validation.get('status')}",
            f"- recovery_kind: {validation.get('recovery_kind')}",
            f"- next_phase: {receipt.get('next_phase')}",
            f"- host_invoked: {effects.get('host_invoked')}",
            f"- state_written: {effects.get('state_written')}",
            f"- quota_spent: {effects.get('quota_spent')}",
            *(
                [
                    f"- recovery_plan: {planned.get('action')}",
                    f"- recovery_from: {planned.get('resume_from')}",
                    f"- recovery_reinvoke_host: {planned.get('reinvoke_host')}",
                    f"- recovery_reason: {planned.get('reason')}",
                    f"- recovery_actual: {actual.get('status')}",
                    f"- recovery_result_status: {actual.get('journal_status')}",
                ]
                if planned
                else []
            ),
        ]
    )


def render_loopx_turn_journal_inspection_markdown(
    payload: dict[str, object],
) -> str:
    if not payload.get("ok"):
        error = payload.get("error") or "Turn journal inspection failed"
        return f"LoopX Turn journal inspection failed: {error}"
    completed_phases = payload.get("completed_phases")
    violations = payload.get("violations")
    recovery = (
        payload.get("recovery_decision")
        if isinstance(payload.get("recovery_decision"), dict)
        else {}
    )
    last_recovery = (
        payload.get("last_recovery")
        if isinstance(payload.get("last_recovery"), dict)
        else {}
    )
    last_planned = (
        last_recovery.get("planned")
        if isinstance(last_recovery.get("planned"), dict)
        else {}
    )
    last_actual = (
        last_recovery.get("actual")
        if isinstance(last_recovery.get("actual"), dict)
        else {}
    )
    checks = recovery.get("checks") if isinstance(recovery.get("checks"), list) else []
    rendered_checks = ", ".join(
        ":".join(
            str(part)
            for part in (check.get("kind"), check.get("outcome"), check.get("reason"))
            if part
        )
        for check in checks
        if isinstance(check, dict)
    )
    return "\n".join(
        [
            "# LoopX Turn Journal Inspection",
            f"- recovery_action: {recovery.get('action')}",
            f"- recovery_can_continue: {recovery.get('can_continue')}",
            f"- recovery_from: {recovery.get('resume_from')}",
            f"- recovery_reinvoke_host: {recovery.get('reinvoke_host')}",
            f"- recovery_reason: {recovery.get('reason')}",
            f"- recovery_checks: {rendered_checks or 'none'}",
            f"- journal_consistent: {payload.get('journal_consistent')}",
            *(
                [
                    f"- last_recovery_plan: {last_planned.get('action')}",
                    f"- last_recovery_from: {last_planned.get('resume_from')}",
                    f"- last_recovery_actual: {last_actual.get('status')}",
                    f"- last_recovery_result_status: {last_actual.get('journal_status')}",
                    f"- last_recovery_host_invoked: {last_actual.get('host_invoked')}",
                ]
                if last_recovery
                else []
            ),
            f"- replay_decision: {payload.get('decision')}",
            f"- journal_status: {payload.get('journal_status')}",
            f"- replay_legal: {payload.get('replay_legal')}",
            f"- goal_matches: {payload.get('goal_matches')}",
            f"- owner_matches: {payload.get('owner_matches')}",
            f"- turn_key_matches: {payload.get('turn_key_matches')}",
            (
                "- phases_form_ordered_prefix: "
                f"{payload.get('phases_form_ordered_prefix')}"
            ),
            "- completed_phases: "
            + (
                ", ".join(str(value) for value in completed_phases)
                if isinstance(completed_phases, list) and completed_phases
                else "none"
            ),
            f"- tombstone_retained: {payload.get('tombstone_retained')}",
            "- violations: "
            + (
                ", ".join(str(value) for value in violations)
                if isinstance(violations, list) and violations
                else "none"
            ),
            "- effects: none",
        ]
    )


def render_loopx_turn_managed_step_markdown(payload: dict[str, object]) -> str:
    if not payload.get("ok"):
        error = payload.get("error") or "Turn managed step failed"
        return f"LoopX Turn managed step failed: {error}"
    continuation = (
        payload.get("retry_continuation")
        if isinstance(payload.get("retry_continuation"), dict)
        else {}
    )
    lineage = (
        payload.get("lineage") if isinstance(payload.get("lineage"), dict) else {}
    )
    return "\n".join(
        [
            "# LoopX Turn Managed Step",
            f"- disposition: {payload.get('disposition')}",
            f"- reason: {payload.get('reason')}",
            f"- turn_key: {payload.get('turn_key')}",
            f"- attempt: {payload.get('attempt')}"
            + (
                f"/{payload.get('max_attempts')}"
                if payload.get("max_attempts") is not None
                else ""
            ),
            f"- goal_id: {lineage.get('goal_id') or 'none'}",
            f"- agent_id: {lineage.get('agent_id') or 'none'}",
            f"- todo_id: {lineage.get('todo_id') or 'none'}",
            *(
                [
                    f"- retry_after_seconds: {continuation.get('retry_after_seconds')}",
                    f"- retry_strategy: {continuation.get('strategy')}",
                    "- same_turn: "
                    f"{continuation.get('same_turn')}; "
                    "retry_failed_turn: "
                    f"{continuation.get('retry_failed_turn')}",
                    "- fresh_envelope_required: "
                    f"{continuation.get('fresh_envelope_required')}; "
                    "model_fallback_allowed: "
                    f"{continuation.get('model_fallback_allowed')}",
                ]
                if continuation
                else ["- continuation: none"]
            ),
            "- execution_authority: none",
            "- effects: none",
        ]
    )
