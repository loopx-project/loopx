"""Per-goal handoff mode: which ownership authority governs todo handoffs.

Before promotion the active-state frontmatter declares ``handoff_mode``; afterward
the selected canonical provider owns it. Public show/set route by that authority:

* absent / ``legacy`` (default): today's dual soft-claim + hard-lease
  behavior, byte-for-byte. The known soft-claim/hard-lease split brain stays
  open in this mode by design; it is surfaced additively, never silently
  repaired.
* ``soft_claim``: the Todo claim is the only ownership record. Task-lease
  acquire/renew/transfer are typed-rejected; release and inspect stay allowed
  for cleanup and observability of legacy leftovers.
* ``hard_lease``: ownership changes on an existing todo require the acting
  agent to hold that todo's time-active task lease, and the completion fence
  becomes mandatory for both user-role and agent-role todos. An exact linked
  user-gate decision-scope override authorizes only the exact linked decision
  transition; it does not bypass the completion fence. The delegated
  ``coordination.todo_lifecycle_authority`` override is the one audited door
  through the gate.

Canonical mode, complete Todo/lease quiescence, CAS and replay share one TypeScript
transaction. Stale or missing Markdown and local lease files are not fallback
sources. The legacy mode below remains a frontmatter compatibility contract.

Unpromoted transitions read complete Markdown Todos and local leases under
their writer mutexes. The same typed quiescence rule governs both paths;
nonempty retired Todo event sources are refused, never treated as empty.
"""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from typing import Any

from ..coordination.authority_core import (
    HandoffMode,
    OwnershipGate,
    ownership_gate_requirement,
)
from ..goals.active_state_metadata import parse_state_frontmatter, split_state_frontmatter
from .contract import normalize_todo_claimed_by

HANDOFF_MODE_SCHEMA_VERSION = "goal_handoff_mode_v0"
HANDOFF_MODE_FRONTMATTER_KEY = "handoff_mode"
HANDOFF_MODE_LEGACY = "legacy"
HANDOFF_MODE_SOFT_CLAIM = "soft_claim"
HANDOFF_MODE_HARD_LEASE = "hard_lease"
HANDOFF_MODE_VALUES = (
    HANDOFF_MODE_LEGACY,
    HANDOFF_MODE_SOFT_CLAIM,
    HANDOFF_MODE_HARD_LEASE,
)
DELEGATED_AUTHORITY_MODE = "delegated_orchestration_override"


class HandoffModeError(ValueError):
    """Typed handoff-mode failure; mirrors TaskLeaseError's code/payload shape."""

    def __init__(
        self, message: str, *, code: str, payload: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.payload = payload or {}


def normalize_handoff_mode(value: Any) -> str:
    candidate = str(value or "").strip()
    if not candidate:
        return HANDOFF_MODE_LEGACY
    if candidate not in HANDOFF_MODE_VALUES:
        raise HandoffModeError(
            f"unsupported handoff_mode {candidate!r}; expected one of: "
            + ", ".join(HANDOFF_MODE_VALUES),
            code="invalid_handoff_mode",
            payload={"handoff_mode": candidate, "supported": list(HANDOFF_MODE_VALUES)},
        )
    return candidate


def goal_handoff_mode(state_text: str) -> str:
    """Read the goal handoff mode from active-state text; absent means legacy."""

    front_matter = parse_state_frontmatter(state_text)
    return normalize_handoff_mode(front_matter.get(HANDOFF_MODE_FRONTMATTER_KEY))


def _resolve_state(
    *,
    registry_path: Path,
    goal_id: str,
    project: Path | None = None,
    state_file: Path | None = None,
) -> tuple[Path | None, Path]:
    from ...todos import resolve_todo_state_path

    return resolve_todo_state_path(
        registry_path=registry_path,
        goal_id=goal_id,
        project=project,
        state_file=state_file,
    )


def goal_handoff_mode_for_goal(
    *,
    registry_path: Path,
    goal_id: str,
    project: Path | None = None,
    state_file: Path | None = None,
) -> str:
    return str(show_goal_handoff_mode(registry_path=registry_path, goal_id=goal_id,
        project=project, state_file=state_file)["handoff_mode"])


def enter_todo_ownership_handoff_gate(
    stack: ExitStack,
    *,
    state_text: str,
    registry_path: Path,
    goal_id: str,
    todo_id: str,
    mutation_authority: dict[str, Any],
    actor_agent_id: str | None,
    ownership_mutation: bool,
    runtime_root: Path | None = None,
) -> dict[str, Any]:
    """Gate one claimed_by mutation on an existing todo behind the goal mode.

    Returns additive payload fields (always ``handoff_mode``; plus the door
    marker or the held holder-gate receipt in hard_lease mode). In hard_lease
    mode the per-goal lease lock is entered on ``stack`` so it stays held
    through the markdown commit, matching the completion fence's
    state-lock-then-lease-lock order.
    """

    mode = goal_handoff_mode(state_text)
    extras: dict[str, Any] = {"handoff_mode": mode}
    gate = ownership_gate_requirement(
        handoff_mode=HandoffMode(mode),
        ownership_mutation=ownership_mutation,
        authority_mode=str(mutation_authority.get("mode") or "") or None,
    )
    if gate is OwnershipGate.NOT_REQUIRED:
        return extras
    if gate is OwnershipGate.DELEGATED_OVERRIDE:
        extras["handoff_gate_overridden"] = True
        return extras
    from ..work_items.task_lease import hold_handoff_lease_holder_gate

    extras["task_lease_holder_gate"] = stack.enter_context(
        hold_handoff_lease_holder_gate(
            registry_path=registry_path,
            goal_id=goal_id,
            todo_id=todo_id,
            actor_agent_id=actor_agent_id,
            runtime_root=runtime_root,
        )
    )
    return extras


def enter_added_todo_ownership_handoff_gate(
    stack: ExitStack,
    *,
    lines: list[str],
    state_text: str,
    registry_path: Path,
    goal_id: str,
    role: str,
    text: str,
    claimed_by: str | None,
    actor_agent_id: str | None,
    runtime_root: Path | None = None,
) -> dict[str, Any]:
    """Gate the ``todo add`` path when it would reassign an existing todo.

    ``todo add`` matches an open todo by text and upserts its metadata, so a
    ``claimed_by`` argument on that branch changes the claim of a todo that
    may already hold a lease. Creating a todo stays ungated in every mode: a
    todo id that does not exist yet cannot hold one. Re-adding a todo with the
    claim it already carries is not an ownership change and is not gated.

    The delegated-authority door is not offered here. Reassignment under a
    ``coordination.todo_lifecycle_authority`` grant goes through
    ``todo update --claimed-by``, which is the verb that owns that action.
    """

    from ...todos import matching_todo_block  # loopx.todos, deferred: import cycle
    from .active_state_editing import section_bounds

    bounds = section_bounds(lines, role)
    existing = (
        matching_todo_block(
            lines, bounds[0], bounds[1], text, role=role, source_section=bounds[2]
        )
        if bounds
        else None
    )
    requested = normalize_todo_claimed_by(claimed_by) if claimed_by else None
    current = normalize_todo_claimed_by(existing.get("claimed_by")) if existing else None
    return enter_todo_ownership_handoff_gate(
        stack,
        state_text=state_text,
        registry_path=registry_path,
        goal_id=goal_id,
        todo_id=str(existing.get("todo_id") or "") if existing else "",
        mutation_authority={},
        actor_agent_id=actor_agent_id,
        ownership_mutation=(
            role == "agent"
            and existing is not None
            and requested is not None
            and requested != current
        ),
        runtime_root=runtime_root,
    )


def resolve_todo_completion_handoff(
    *,
    state_text: str,
    mutation_authority: dict[str, Any],
) -> dict[str, Any]:
    """Resolve mode plus the delegated-authority door for one todo completion."""

    mode = goal_handoff_mode(state_text)
    extras: dict[str, Any] = {"handoff_mode": mode}
    if (
        mode == HANDOFF_MODE_HARD_LEASE
        and mutation_authority.get("mode") == DELEGATED_AUTHORITY_MODE
    ):
        extras["handoff_gate_overridden"] = True
    return extras


def show_goal_handoff_mode(
    *,
    registry_path: Path,
    goal_id: str,
    project: Path | None = None,
    state_file: Path | None = None,
    runtime_root_arg: str | None = None,
) -> dict[str, Any]:
    from ..work_items.task_lease import runtime_root_from_registry
    from .provider_handoff_mode import read_canonical_handoff_mode

    canonical = read_canonical_handoff_mode(
        runtime_root=runtime_root_from_registry(registry_path, runtime_root_arg), goal_id=goal_id)
    if canonical is not None:
        return {"ok": True, "schema_version": HANDOFF_MODE_SCHEMA_VERSION, "action": "show",
                "goal_id": goal_id, **canonical,
                "handoff_mode": normalize_handoff_mode(canonical["handoff_mode"]), "source": "canonical_provider"}
    _project, resolved_state_file = _resolve_state(
        registry_path=registry_path,
        goal_id=goal_id,
        project=project,
        state_file=state_file,
    )
    front_matter = parse_state_frontmatter(
        resolved_state_file.read_text(encoding="utf-8")
    )
    raw = front_matter.get(HANDOFF_MODE_FRONTMATTER_KEY)
    return {
        "ok": True,
        "schema_version": HANDOFF_MODE_SCHEMA_VERSION,
        "action": "show",
        "goal_id": goal_id,
        "handoff_mode": normalize_handoff_mode(raw),
        "source": "frontmatter" if str(raw or "").strip() else "default",
        "state_file": str(resolved_state_file),
    }


def _plan_legacy_mode(request: dict[str, Any]) -> dict[str, Any]:
    from ..effect_runtime import effect_runtime_result

    result = effect_runtime_result("coordination.handoff_mode.legacy_plan", request)
    if not isinstance(result, dict) or result.get("schema_version") != "loopx_legacy_handoff_mode_plan_result_v0":
        raise HandoffModeError("invalid typed handoff plan", code="handoff_mode_plan_unavailable")
    if result.get("outcome") == "rejected":
        raise HandoffModeError(
            "handoff mode change rejected; resolve the reported state or ownership blockers",
            code=result["code"], payload={key: value for key, value in result.items()
                if key not in {"schema_version", "outcome", "code", "changed"}},
        )
    return result


def set_goal_handoff_mode(
    *,
    registry_path: Path,
    goal_id: str,
    mode: str,
    project: Path | None = None,
    state_file: Path | None = None,
    runtime_root_arg: str | None = None,
    operation_id: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Set the authoritative goal mode; changed modes require quiescence.

    Promoted Goals use one provider transaction; the remaining text below
    describes the unpromoted compatibility writer.

    Changed modes require complete unclaimed Todo state and no time-active
    leases. Event source locks remain held through the frontmatter replacement.
    Hand-editing frontmatter bypasses this check and is outside the contract.
    """

    from ..work_items.task_lease import runtime_root_from_registry
    from .handoff_mode_source import handoff_mode_source
    from ..coordination.legacy_writer_fence import (
        LegacyCoordinationWriterFenced,
        legacy_todo_write_transaction,
        require_legacy_coordination_write_allowed,
    )
    from ..coordination.local_authority import (
        LocalCoordinationAuthorityRejection,
        LocalCoordinationAuthorityUnavailable,
    )
    from ..coordination.runtime_shadow_writer_adapter import (
        write_captured_todo_state,
        begin_todo_runtime_shadow_capture,
        settle_todo_runtime_shadow_capture,
    )

    requested = normalize_handoff_mode(mode)
    if not str(mode or "").strip():
        raise HandoffModeError(
            "handoff-mode set requires an explicit --mode value",
            code="invalid_handoff_mode",
        )
    from .provider_handoff_mode import set_canonical_handoff_mode

    runtime_root = runtime_root_from_registry(registry_path, runtime_root_arg)
    try:
        canonical = set_canonical_handoff_mode(
            runtime_root=runtime_root,
            goal_id=goal_id,
            mode=requested,
            operation_id=operation_id,
            dry_run=dry_run,
        )
    except LocalCoordinationAuthorityRejection:
        raise
    except LocalCoordinationAuthorityUnavailable:
        # A present legacy fence is the admission boundary for this caller.
        # Re-check it when canonical dispatch is unavailable so an outage cannot
        # turn a fenced legacy writer into an attempted Markdown mutation.
        try:
            require_legacy_coordination_write_allowed(
                runtime_root=runtime_root,
                goal_id=goal_id,
            )
        except LegacyCoordinationWriterFenced:
            raise
        raise
    if canonical is not None:
        return canonical
    if operation_id is not None:
        raise HandoffModeError("--operation-id requires canonical authority", code="handoff_mode_operation_id_unsupported")
    _project, resolved_state_file = _resolve_state(
        registry_path=registry_path,
        goal_id=goal_id,
        project=project,
        state_file=state_file,
    )
    # The already resolved root also governs the legacy lock, scan and capture.
    with legacy_todo_write_transaction(
        registry_path, goal_id, resolved_state_file, None, "handoff_mode_set",
        dry_run, runtime_root=runtime_root,
    ):
        with resolved_state_file.open(encoding="utf-8", newline="") as source:
            original = source.read()
        metadata, body = split_state_frontmatter(original)
        frontmatter = original[:len(original) - len(body)]
        request = {
            "schema_version": "loopx_legacy_handoff_mode_plan_request_v0",
            "previous_value": metadata.get(HANDOFF_MODE_FRONTMATTER_KEY),
            "requested_mode": requested, "frontmatter_text": frontmatter,
            "todos": None, "leases": None,
        }
        plan = _plan_legacy_mode(request)
        payload = {
            "ok": True, "schema_version": HANDOFF_MODE_SCHEMA_VERSION,
            "action": "set", "goal_id": goal_id, "state_file": str(resolved_state_file),
            **{key: value for key, value in plan.items() if key.startswith("previous_mode")},
            "handoff_mode": requested, "changed": False,
            **({"dry_run": True} if dry_run else {}),
        }
        if plan["outcome"] == "no_change":
            return payload
        if plan["outcome"] != "snapshot_required":
            raise HandoffModeError("unexpected handoff planning phase", code="handoff_mode_plan_unavailable")
        with handoff_mode_source(registry_path=registry_path, goal_id=goal_id,
            state_path=resolved_state_file, state_text=original, runtime_root=runtime_root) as facts:
            try:
                plan = _plan_legacy_mode({**request, **facts})
            except HandoffModeError as error:
                error.payload.update(goal_id=goal_id, requested_mode=requested)
                raise
            if plan.get("outcome") != "apply" or not isinstance(plan.get("next_frontmatter_text"), str):
                raise HandoffModeError("incomplete handoff mutation plan", code="handoff_mode_plan_unavailable")
            if dry_run:
                return {**payload, "changed": True}
            capture = begin_todo_runtime_shadow_capture(
                registry_path=registry_path, runtime_root=runtime_root, goal_id=goal_id,
                state_path=resolved_state_file, write_class="handoff_mode_set", original_text=original)
            write_captured_todo_state(capture, runtime_root=runtime_root, goal_id=goal_id,
                state_path=resolved_state_file, text=plan["next_frontmatter_text"] + body)
    payload["changed"] = True
    return settle_todo_runtime_shadow_capture(
        payload, registry_path=registry_path, runtime_root=runtime_root,
        goal_id=goal_id, capture=capture,
        emit_disabled=False,
    )
