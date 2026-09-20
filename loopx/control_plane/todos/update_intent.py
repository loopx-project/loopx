"""Build the one canonical Todo-update intent sent to the TS authority.

The CLI has a deliberately wide signature for backwards compatibility.  Keep
the translation in one place so a newly added option cannot accidentally take
the Markdown path while its siblings use the provider transaction.
"""

from __future__ import annotations

from typing import Any

from .monitor_metadata import MonitorPollObservation


def build_canonical_update_intent(
    *,
    status: str | None = None,
    evidence: str | None = None,
    reason: str | None = None,
    task_class: str | None = None,
    action_kind: str | None = None,
    task_domain: str | None = None,
    task_repository: str | None = None,
    continuation_policy: str | None = None,
    required_write_scopes: list[str] | None = None,
    required_capabilities: list[str] | None = None,
    target_capabilities: list[str] | None = None,
    explore_result_node_refs: list[str] | None = None,
    decision_scope: Any = None,
    required_decision_scopes: Any = None,
    claimed_by: str | None = None,
    bound_agent: str | None = None,
    goal_bound: bool = False,
    blocks_agent: str | None = None,
    clear_blocks_agent: bool = False,
    excluded_agents: list[str] | None = None,
    clear_excluded_agents: bool = False,
    global_gate: bool = False,
    clear_global_gate: bool = False,
    unblocks_todo_id: str | None = None,
    successor_todo_ids: list[str] | None = None,
    resume_when: str | None = None,
    clear_resume_when: bool = False,
    no_followup: bool | None = None,
    clear_claim: bool = False,
) -> dict[str, Any]:
    """Return only explicitly requested fields, retaining explicit clears.

    Empty lists are intentional clears.  Boolean clear switches are emitted as
    explicit values so the TypeScript transaction can reject contradictory
    intent before it opens a provider write.
    """

    values: dict[str, Any] = {
        "status": status,
        "evidence": evidence,
        "reason": reason,
        "task_class": task_class,
        "action_kind": action_kind,
        "task_domain": task_domain,
        "task_repository": task_repository,
        "continuation_policy": continuation_policy,
        "required_write_scopes": required_write_scopes,
        "required_capabilities": required_capabilities,
        "target_capabilities": target_capabilities,
        "explore_result_node_refs": explore_result_node_refs,
        "decision_scope": decision_scope,
        "required_decision_scopes": required_decision_scopes,
        "claimed_by": claimed_by,
        "bound_agent": bound_agent,
        "goal_bound": goal_bound if goal_bound else None,
        "blocks_agent": blocks_agent,
        "clear_blocks_agent": clear_blocks_agent if clear_blocks_agent else None,
        "excluded_agents": [] if clear_excluded_agents else excluded_agents,
        "global_gate": global_gate if global_gate else None,
        "clear_global_gate": clear_global_gate if clear_global_gate else None,
        "unblocks_todo_id": unblocks_todo_id,
        "successor_todo_ids": successor_todo_ids,
        "resume_when": resume_when,
        "clear_resume_when": clear_resume_when if clear_resume_when else None,
        "no_followup": no_followup,
        "clear_claim": clear_claim if clear_claim else None,
    }
    return {key: value for key, value in values.items() if value is not None}


def canonical_update_is_supported(
    *,
    text: str | None,
    note: str | None,
    intent: dict[str, Any],
    monitor_metadata: Any,
) -> bool:
    """Whether an ordinary update can use the canonical transaction.

    User completion is routed to the typed terminal owner; Monitor observations
    carry a versioned effect intent instead of a raw metadata patch.
    Ordinary metadata is validated by the typed transaction, including rejection
    of unsupported fields; this transport must not duplicate its field catalog.
    They must not silently fall back to Markdown after authority promotion.
    """

    if isinstance(monitor_metadata, MonitorPollObservation):
        return True
    # Empty notes are the long-standing compatibility spelling for omission;
    # routing them to the canonical adapter would produce an empty patch and a
    # less useful protocol error. Text still uses the normal non-empty text
    # validator at the provider boundary.
    return text is not None or (note is not None and bool(note.strip())) or bool(intent) or bool(monitor_metadata)
