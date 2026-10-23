"""Storage adapters for the typed team-plan transaction.

Canonical Goals use the existing AuthorityStore transaction and projection outbox.
Pre-cutoff Markdown receipts remain read-only historical results.
Source locks and historical receipt readers remain shared IO.
Neither adapter grants receiver adoption or execution authority.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from ...agent_registry import registered_agent_ids_for_goal
from ...history import load_registry
from ...file_lock import (
    LockAcquireTimeoutError, cross_runtime_lock_witness, exclusive_cross_runtime_file_lock,
)
from ...registry import find_registry_goal
from ...state_refresh import now_local
from ..effect_runtime import effect_runtime_result
from ..runtime.public_safety import validate_public_safe_value
from ..coordination.local_authority import (
    CanonicalAuthorityRequired,
    raise_canonical_write_failure,
    read_canonical_todos_if_promoted,
    require_canonical_authority,
)
from ...paths import effective_runtime_root
from ..coordination.legacy_writer_fence import legacy_coordination_todo_lock_path
from ..coordination.shadow_management import shadow_maintenance_lock_target
from ..todos.path_resolution import resolve_todo_state_path
from ..todos.contract import TODO_ACTION_KIND_ADVANCEMENT_VALUES
from ..todos.provider_projection import settle_canonical_todo_projection


class TeamPlanCommitError(ValueError):
    def __init__(self, code: str, message: str, current_fingerprint: str | None = None):
        super().__init__(message)
        self.code = code
        self.current_fingerprint = current_fingerprint


def team_plan_state_fingerprint(
    *, registry_path: Path, goal_id: str, basis_agent_id: str | None,
) -> str:
    """Bind every fact a team plan is reviewed and settled against.

    Registry bytes are not enough. A plan is reviewed against the Goal's own
    intent -- the objective its work advances -- and that intent lives in the
    active-state document and in the canonical source basis the lanes would be
    created against, neither of which the registry bytes cover. The canonical
    provider revision is bound too, so a promoted Goal whose display has not
    caught up cannot admit stale work.

    Every originator binds this same digest before its plan is reviewed (owner
    confirmation at preview, agent settlement at first journal write) and the
    settlement re-reads it; the typed owner refuses a commit without both.

    An unreadable fact is bound as its own explicit absence rather than dropped
    from the digest, so the precondition fails closed in both directions: a Goal
    whose intent becomes readable after the review asks for a new review
    instead of silently dropping the check.
    """

    from .governed_transition_proposal import steward_team_plan_source_basis

    registry_path = Path(registry_path).expanduser()
    try:
        registry_digest = hashlib.sha256(registry_path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ValueError("the active LoopX registry is unavailable") from exc
    goal = find_registry_goal(load_registry(registry_path), goal_id)
    if goal is None:
        raise ValueError("steward team plan names an unknown Goal")
    project = Path(str(goal.get("repo") or "")).expanduser()
    state_file = Path(str(goal.get("state_file") or ""))
    if not state_file.is_absolute():
        state_file = project / state_file
    try:
        state_digest: str | None = hashlib.sha256(state_file.read_bytes()).hexdigest()
    except OSError:
        state_digest = None
    try:
        canonical = read_canonical_todos_if_promoted(
            runtime_root=effective_runtime_root(registry_path, None), goal_id=goal_id)
    except CanonicalAuthorityRequired:
        require_canonical_authority(goal_id=goal_id, operation="team_plan")
    payload = {
        "registry": registry_digest,
        "provider_revision": canonical.get("provider_revision"),
        "goal_id": goal_id,
        "active_state": state_digest,
        "intent_basis": steward_team_plan_source_basis(
            goal_id=goal_id, goal=goal, registry_path=registry_path,
            agent_id=basis_agent_id),
    }
    encoded = json.dumps(payload, allow_nan=False, ensure_ascii=False,
                         sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def settle_team_plan(
    *, registry_path: Path, goal_id: str, agent_id: str | None,
    proposal: Mapping[str, Any], expected_state_fingerprint: str | None,
    basis_agent_id: str | None = None,
) -> dict[str, Any]:
    """Apply one reviewed plan against the basis its reviewer bound.

    ``expected_state_fingerprint`` is the digest the originator stored when the
    plan was reviewed; the settlement re-reads the same digest through
    :func:`team_plan_state_fingerprint` and the typed owner compares them. A
    missing basis is refused there as ``team_plan_basis_missing`` rather than
    skipping the check. ``basis_agent_id`` defaults to the settling agent.
    """

    registry_path = Path(registry_path).expanduser()
    basis_agent = basis_agent_id if basis_agent_id is not None else agent_id
    return apply_team_plan(
        registry_path=registry_path, goal_id=goal_id, agent_id=agent_id,
        proposal=proposal, expected_state_fingerprint=expected_state_fingerprint,
        read_fingerprint=lambda: team_plan_state_fingerprint(
            registry_path=registry_path, goal_id=goal_id, basis_agent_id=basis_agent),
    )


def apply_team_plan(
    *, registry_path: Path, goal_id: str, agent_id: str | None,
    proposal: Mapping[str, Any], expected_state_fingerprint: str | None = None,
    read_fingerprint: Callable[[], str] | None = None,
) -> dict[str, Any]:
    from .governed_transition_proposal import steward_team_plan_intent_basis

    validate_public_safe_value(dict(proposal), path="team_plan")
    registry_path = Path(registry_path).expanduser()
    runtime = effective_runtime_root(registry_path, None)
    if find_registry_goal(load_registry(registry_path), goal_id) is None:
        raise ValueError("steward team plan proposal names an unknown Goal")
    # The registered route is still required, but a missing display must not
    # prevent the typed owner from recovering an exact canonical receipt.
    project, state = resolve_todo_state_path(
        registry_path=registry_path, goal_id=goal_id, require_existing=False)
    request = {
        "goal_id": goal_id, "plan": dict(proposal),
        "actor_agent_id": agent_id,
        "supported_action_kinds": sorted(TODO_ACTION_KIND_ADVANCEMENT_VALUES),
        "observed_at": now_local(),
        "expected_state_fingerprint": expected_state_fingerprint,
    }

    def read_source_request() -> None:
        # Called under the registry and state locks, including on the legacy
        # path. A registry rebind while acquiring the guards must not redirect
        # the operation to an unguarded source or provider.
        goal = find_registry_goal(load_registry(registry_path), goal_id)
        if goal is None:
            raise ValueError("steward team plan proposal names an unknown Goal")
        _, current_state = resolve_todo_state_path(
            registry_path=registry_path, goal_id=goal_id, require_existing=False)
        if (effective_runtime_root(registry_path, None).resolve() != runtime.resolve()
                or current_state.resolve() != state.resolve()):
            raise TeamPlanCommitError("team_plan_preview_stale", "team plan source binding changed after preview")
        request.update(
            registered_agents=registered_agent_ids_for_goal(goal),
            intent_basis=steward_team_plan_intent_basis(
                goal_id=goal_id, goal=goal, registry_path=registry_path, plan=proposal),
        )

    identity = effect_runtime_result("work_items.team_plan.identity", request)
    marker = f"<!-- loopx:team-plan:{identity['operation_id'].split(':')[1]} "

    def previous_receipt(original: str) -> dict[str, Any] | None:
        matches = re.findall(re.escape(marker) + r"([A-Za-z0-9+/=]+) -->", original)
        if marker in original and not matches:
            raise ValueError("team plan operation receipt is malformed")
        if len(matches) > 1:
            raise ValueError("duplicate team plan operation receipts")
        return json.loads(base64.b64decode(matches[0], validate=True)) if matches else None

    targets = (
        shadow_maintenance_lock_target(runtime, goal_id),
        legacy_coordination_todo_lock_path(runtime_root=runtime, goal_id=goal_id),
        state, registry_path,
    )
    with ExitStack() as guards:
        for target in targets[:-1]:
            guards.enter_context(exclusive_cross_runtime_file_lock(target, operation="team_plan_commit"))
        try:
            # Registry administration can hold R before T/S. Like promotion,
            # never wait for R while holding source locks in reverse order.
            guards.enter_context(exclusive_cross_runtime_file_lock(
                registry_path, operation="team_plan_commit", timeout_seconds=0))
        except LockAcquireTimeoutError:
            raise TeamPlanCommitError("team_plan_commit_failed",
                "team plan registry is busy; retry the original confirmation") from None
        read_source_request()
        # Observe the selected provider under the transition/source locks:
        # provider loss never authorizes a fallback to historical source.
        try:
            canonical = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=goal_id)
        except CanonicalAuthorityRequired:
            # Only native unpromoted admission may defer to the retained
            # receipt reader. A selected-provider failure still propagates.
            canonical = None
        try:
            source_bytes = state.read_bytes()
        except FileNotFoundError:
            source_bytes = None
        try:
            source_text = source_bytes.decode("utf-8") if source_bytes is not None else None
        except UnicodeDecodeError:
            if canonical is None:
                raise
            # Canonical history can still be read, but these bytes cannot
            # establish fresh Goal intent or a historical Markdown receipt.
            source_text = None
        # An exact pre-cutoff receipt is read-only recovery, not fresh work.
        previous = previous_receipt(source_text) if source_text is not None else None
        if previous is not None:
            return dict(effect_runtime_result("work_items.team_plan.plan", {
                **request, "previous_receipt": previous})["result"])
        if canonical is None:
            require_canonical_authority(goal_id=goal_id, operation="team_plan")
        response = effect_runtime_result("work_items.team_plan.commit", {
            **request, "runtime_root": str(runtime.resolve()),
            "expected_provider_revision": canonical["provider_revision"],
            "current_state_fingerprint": read_fingerprint() if read_fingerprint else None,
            "source": {
                "registry": str(registry_path.resolve()), "state_file": str(state.resolve()),
                "registry_sha256": hashlib.sha256(registry_path.read_bytes()).hexdigest(),
                # Absence is an observation, never a fresh source witness.
                # The native owner checks receipts before source admission.
                "state_sha256": hashlib.sha256(source_bytes).hexdigest() if source_text is not None else None,
            },
            "locks": [cross_runtime_lock_witness(target) for target in targets],
        })
    raise_canonical_write_failure(response, goal_id=goal_id, operation="team_plan")
    if response.get("status") not in {"applied", "recovered", "replayed", "no_change"}:
        raise TeamPlanCommitError(str(response.get("reason_code") or "team_plan_commit_failed"),
            str(response.get("reason") or "team plan commit could not be verified"),
            read_fingerprint() if read_fingerprint else None)
    projected = settle_canonical_todo_projection(dict(response), registry_path=registry_path,
        runtime_root=runtime, goal_id=goal_id, project=project, state_file=state)
    if projected.get("projection_delivery") == "pending":
        raise TeamPlanCommitError("team_plan_projection_pending", "Tasks committed; display readback is pending")
    result = dict(response["result"])
    result["projection_delivery"] = projected.get("projection_delivery")
    return result
