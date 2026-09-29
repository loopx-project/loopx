"""Goal attention routing projections inside the `status` bounded context."""

from __future__ import annotations

from typing import Any

from ...operator_gate import (
    DEFAULT_OPERATOR_GATE,
    default_operator_question,
    normalize_operator_question,
)
from ..handoff.handoff_runs import (
    run_has_external_evidence_watch_signal as _run_has_external_evidence_watch_signal,
)
from ..runtime.public_safety import public_safe_compact_text
from ..runtime.session_runtime import (
    compact_session_runtime_projection_from_run,
    legacy_runtime_goal_attention as _legacy_runtime_goal_attention,
)
from ..runtime.status_classifications import (
    BLOCKING_CLASSIFICATIONS,
    CODEX_READY_CLASSIFICATIONS,
    USER_OR_CONTROLLER_CLASSIFICATIONS,
)
from ..work_items.attention_item import attention_item as _attention_item
from ..work_items.attention_routing import goal_attention as _goal_attention
from .adapter_status_vocabulary import (
    CONNECTED_ADAPTER_STATUSES,
    CONNECTED_DELIVERY_ADAPTER_STATUSES,
)
from .dreaming_projection import (
    compact_dreaming_lane_badge,
    dreaming_attention_fields,
)
from .lifecycle_projection import (
    goal_lifecycle_fields,
    operator_gate_attention_fields,
    readiness_attention_fields,
)
from .monitor_display_projection import (
    MONITOR_SIGNAL_WAITING_ON,
    build_project_asset,
)
from .run_projection import latest_run


PLANNED_CONTROLLER_OPT_IN_RECOMMENDED_ACTION = (
    "先在 LoopX 完成 operator 判断；同意后项目 Agent 只执行 read-only map dry-run"
)
REGISTRY_WAITING_ON_OVERRIDES = {
    "user_or_controller",
    "controller",
    "codex",
    "external_evidence",
}
LEGACY_EXTERNAL_EVIDENCE_CLASSIFICATION_PREFIXES = (
    "await_",
    "external_evidence_observation_",
)


def attention_item(
    *,
    goal_id: str,
    status: str,
    waiting_on: str,
    severity: str,
    recommended_action: str,
    source: str,
    operator_question: str | None = None,
    agent_command: str | None = None,
    controller_stage: str | None = None,
    missing_gates: list[str] | None = None,
    next_handoff_condition: str | None = None,
    lifecycle_phase: str | None = None,
    lifecycle_flags: list[str] | None = None,
    user_todos: dict[str, Any] | None = None,
    agent_todos: dict[str, Any] | None = None,
    todo_state_file: str | None = None,
    dreaming_proposal: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _attention_item(
        goal_id=goal_id,
        status=status,
        waiting_on=waiting_on,
        severity=severity,
        recommended_action=recommended_action,
        source=source,
        build_project_asset=build_project_asset,
        compact_dreaming_lane_badge=compact_dreaming_lane_badge,
        operator_question=operator_question,
        agent_command=agent_command,
        controller_stage=controller_stage,
        missing_gates=missing_gates,
        next_handoff_condition=next_handoff_condition,
        lifecycle_phase=lifecycle_phase,
        lifecycle_flags=lifecycle_flags,
        user_todos=user_todos,
        agent_todos=agent_todos,
        todo_state_file=todo_state_file,
        dreaming_proposal=dreaming_proposal,
    )


def run_has_external_evidence_watch_signal(run: dict[str, Any]) -> bool:
    return _run_has_external_evidence_watch_signal(
        run,
        legacy_external_evidence_classification_prefixes=(
            LEGACY_EXTERNAL_EVIDENCE_CLASSIFICATION_PREFIXES
        ),
    )


def legacy_runtime_goal_attention(
    goal: dict[str, Any],
    current_run: dict[str, Any] | None,
    readiness_fields: dict[str, Any],
) -> dict[str, Any] | None:
    return _legacy_runtime_goal_attention(
        goal,
        current_run,
        readiness_fields,
        attention_item=attention_item,
        goal_lifecycle_fields=goal_lifecycle_fields,
        blocking_classifications=BLOCKING_CLASSIFICATIONS,
        user_or_controller_classifications=USER_OR_CONTROLLER_CLASSIFICATIONS,
        codex_ready_classifications=CODEX_READY_CLASSIFICATIONS,
    )


def goal_attention(goal: dict[str, Any]) -> dict[str, Any] | None:
    return _goal_attention(
        goal,
        latest_run=latest_run,
        readiness_attention_fields=readiness_attention_fields,
        operator_gate_attention_fields=operator_gate_attention_fields,
        dreaming_attention_fields=dreaming_attention_fields,
        goal_lifecycle_fields=goal_lifecycle_fields,
        legacy_runtime_goal_attention=legacy_runtime_goal_attention,
        compact_session_runtime_projection_from_run=compact_session_runtime_projection_from_run,
        public_safe_compact_text=public_safe_compact_text,
        attention_item=attention_item,
        run_has_external_evidence_watch_signal=run_has_external_evidence_watch_signal,
        default_operator_question=default_operator_question,
        normalize_operator_question=normalize_operator_question,
        monitor_signal_waiting_on=MONITOR_SIGNAL_WAITING_ON,
        default_operator_gate=DEFAULT_OPERATOR_GATE,
        planned_controller_opt_in_recommended_action=PLANNED_CONTROLLER_OPT_IN_RECOMMENDED_ACTION,
        connected_adapter_statuses=CONNECTED_ADAPTER_STATUSES,
        connected_delivery_adapter_statuses=CONNECTED_DELIVERY_ADAPTER_STATUSES,
        registry_waiting_on_overrides=REGISTRY_WAITING_ON_OVERRIDES,
        blocking_classifications=BLOCKING_CLASSIFICATIONS,
        user_or_controller_classifications=USER_OR_CONTROLLER_CLASSIFICATIONS,
        codex_ready_classifications=CODEX_READY_CLASSIFICATIONS,
    )
