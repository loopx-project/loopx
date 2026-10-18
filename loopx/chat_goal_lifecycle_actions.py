"""Typed Chat actions for Goal lifecycle transitions."""

from __future__ import annotations

from typing import Any

from .control_plane.goals.activation import GoalActivationState, goal_activation_state
from .control_plane.goals.activation_service import set_goal_activation_state
from .control_plane.goals.deletion_service import delete_stopped_goal


GOAL_LIFECYCLE_SOURCE_BASIS_SCHEMA_VERSION = "loopx_goal_lifecycle_source_basis_v1"


class ChatGoalLifecycleActionMixin:
    """Keep Goal activation policy separate from general action orchestration."""

    def _goal_lifecycle_preview(
        self,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        operation = str(parameters["operation"])
        if operation == "delete":
            preview = delete_stopped_goal(
                registry_path=self.registry_path,
                goal_id=str(parameters["goal_id"]),
                execute=False,
            )
            fingerprint = str(preview.get("observed_state_fingerprint") or "")
            source_basis = preview.get("source_basis")
            if (
                not preview.get("ok")
                or not fingerprint
                or not isinstance(source_basis, dict)
            ):
                raise ValueError(
                    str(
                        preview.get("error")
                        or "Goal deletion source basis is unavailable"
                    )
                )
            return {
                "state_fingerprint": fingerprint,
                "source_basis": source_basis,
            }
        target_state = (
            GoalActivationState.STOPPED
            if operation == "stop"
            else GoalActivationState.ACTIVE
        )
        preview = set_goal_activation_state(
            registry_path=self.registry_path,
            goal_id=str(parameters["goal_id"]),
            state=target_state,
            reason=parameters.get("reason"),
            execute=False,
        )
        fingerprint = str(preview.get("observed_state_fingerprint") or "")
        source_identity = str(preview.get("source_identity") or "")
        if not preview.get("ok") or not fingerprint or not source_identity:
            raise ValueError(
                str(
                    preview.get("error")
                    or "Goal lifecycle source identity is unavailable"
                )
            )
        return {
            "state_fingerprint": fingerprint,
            "source_basis": {
                "schema_version": GOAL_LIFECYCLE_SOURCE_BASIS_SCHEMA_VERSION,
                "source_identity": source_identity,
            },
        }

    def _normalize_goal_lifecycle(
        self, parameters: dict[str, Any]
    ) -> dict[str, Any]:
        from .chat_actions import _opaque, _text

        values = self._allowed_parameters(
            parameters,
            allowed={"goal_id", "operation", "reason"},
        )
        goal_id = _opaque(values.get("goal_id"), field="goal_id")
        self._goal(goal_id)
        operation = str(values.get("operation") or "").strip().lower()
        if operation not in {"stop", "resume", "delete"}:
            raise ValueError("goal.lifecycle operation must be stop, resume, or delete")
        if operation == "delete" and goal_activation_state(self._goal(goal_id)) is not GoalActivationState.STOPPED:
            raise ValueError("stop the Goal before deleting it")
        result = {"goal_id": goal_id, "operation": operation}
        if values.get("reason"):
            result["reason"] = _text(values["reason"], field="reason", limit=600)
        return result

    def _apply_goal_delete(
        self,
        proposal_id: str,
        proposal: dict[str, Any],
        goal_id: str,
        current_fingerprint: str,
    ) -> dict[str, Any]:
        from .chat_actions import _digest

        expected_fingerprint = str(proposal.get("expected_state_fingerprint") or "")
        if current_fingerprint != expected_fingerprint:
            stale = self.store.apply(
                proposal_id,
                current_state_fingerprint=current_fingerprint,
                receipt={},
            )
            return {"proposal": stale, "turn": None}

        result = delete_stopped_goal(
            registry_path=self.registry_path,
            goal_id=goal_id,
            execute=True,
            expected_state_fingerprint=expected_fingerprint,
            expected_source_basis=(
                proposal.get("canonical_update_basis")
                if isinstance(proposal.get("canonical_update_basis"), dict)
                else None
            ),
        )
        if result.get("stale"):
            stale = self.store.apply(
                proposal_id,
                current_state_fingerprint=str(
                    result.get("current_state_fingerprint") or current_fingerprint
                ),
                receipt={},
            )
            return {"proposal": stale, "turn": None}
        if not result.get("ok"):
            error = ValueError(
                str(result.get("error") or "Goal deletion did not complete")
            )
            if not result.get("written") and not result.get("partial_write"):
                return self._goal_delete_failed(proposal_id, error)
            raise error
        if not (result.get("readback") or {}).get("verified"):
            raise ValueError("Goal deletion did not verify")
        receipt = {
            "receipt_id": _digest(
                {
                    "proposal_id": proposal_id,
                    "goal_id": goal_id,
                    "operation": "delete",
                }
            )[:32],
            "outcome": "goal_deleted",
            "projection_verified": True,
            "resource_ids": {"goal_id": goal_id},
        }
        stored = self.store.apply(
            proposal_id,
            current_state_fingerprint=current_fingerprint,
            receipt=receipt,
        )
        return {"proposal": stored, "turn": None}

    def _goal_delete_failed(
        self,
        proposal_id: str,
        error: OSError | ValueError,
    ) -> dict[str, Any]:
        failed = self.store.mark_failed(
            proposal_id,
            error_code="goal_delete_unavailable",
            message="Goal deletion could not safely acquire or update its registries.",
            details={"exception_type": type(error).__name__},
        )
        return {"proposal": failed, "turn": None}

    def _apply_goal_lifecycle(
        self, proposal_id: str, proposal: dict[str, Any], parameters: dict[str, Any]
    ) -> dict[str, Any]:
        from .chat_actions import _digest

        goal_id = str(parameters["goal_id"])
        operation = str(parameters["operation"])
        if operation == "delete":
            expected_fingerprint = str(
                proposal.get("expected_state_fingerprint") or ""
            )
            expected_source_basis = (
                proposal.get("canonical_update_basis")
                if isinstance(proposal.get("canonical_update_basis"), dict)
                else None
            )
            try:
                current = delete_stopped_goal(
                    registry_path=self.registry_path,
                    goal_id=goal_id,
                    execute=False,
                    expected_state_fingerprint=expected_fingerprint,
                    expected_source_basis=expected_source_basis,
                )
            except (OSError, ValueError) as exc:
                return self._goal_delete_failed(proposal_id, exc)
            current_fingerprint = str(current.get("observed_state_fingerprint") or "")
            if not current.get("ok") or not current_fingerprint:
                if current.get("stale") and current_fingerprint:
                    stale = self.store.apply(
                        proposal_id,
                        current_state_fingerprint=current_fingerprint,
                        receipt={},
                    )
                    return {"proposal": stale, "turn": None}
                return self._goal_delete_failed(
                    proposal_id,
                    ValueError(
                        str(
                            current.get("error")
                            or "Goal deletion source basis is unavailable"
                        )
                    ),
                )
            return self._apply_goal_delete(
                proposal_id, proposal, goal_id, current_fingerprint
            )

        target_state = (
            GoalActivationState.STOPPED
            if operation == "stop"
            else GoalActivationState.ACTIVE
        )
        expected_fingerprint = str(proposal.get("expected_state_fingerprint") or "")
        current = set_goal_activation_state(
            registry_path=self.registry_path,
            goal_id=goal_id,
            state=target_state,
            reason=parameters.get("reason"),
            execute=False,
        )
        current_fingerprint = str(current.get("observed_state_fingerprint") or "")
        current_state = GoalActivationState(str(current.get("before_state") or ""))
        source_basis = proposal.get("canonical_update_basis")
        expected_source_identity = (
            str(source_basis.get("source_identity") or "")
            if isinstance(source_basis, dict)
            and source_basis.get("schema_version")
            == GOAL_LIFECYCLE_SOURCE_BASIS_SCHEMA_VERSION
            else ""
        )
        source_route_matches = bool(
            expected_source_identity
            and expected_source_identity == current.get("source_identity")
        )
        idempotent_reapply = (
            source_route_matches
            and current_state is target_state
            and current_fingerprint != expected_fingerprint
        )
        if current_fingerprint != expected_fingerprint and not idempotent_reapply:
            stale = self.store.apply(
                proposal_id,
                current_state_fingerprint=current_fingerprint,
                receipt={},
            )
            return {"proposal": stale, "turn": None}
        result = set_goal_activation_state(
            registry_path=self.registry_path,
            goal_id=goal_id,
            state=target_state,
            reason=parameters.get("reason"),
            actor_kind="owner",
            expected_state_fingerprint=(
                current_fingerprint
                if idempotent_reapply
                else expected_fingerprint
            ),
            execute=True,
        )
        if result.get("error_kind") == "goal_action_stale":
            stale = self.store.apply(
                proposal_id,
                current_state_fingerprint=str(
                    result.get("observed_state_fingerprint")
                    or current_fingerprint
                ),
                receipt={},
            )
            return {"proposal": stale, "turn": None}
        if not result.get("ok") or not (result.get("readback") or {}).get(
            "verified"
        ):
            raise ValueError(
                str(result.get("error") or "Goal lifecycle projection did not verify")
            )
        receipt = {
            "receipt_id": _digest(
                {
                    "proposal_id": proposal_id,
                    "goal_id": goal_id,
                    "operation": operation,
                }
            )[:32],
            "outcome": (
                f"goal_{target_state.value}"
                if result.get("changed")
                else f"goal_already_{target_state.value}"
            ),
            "projection_verified": True,
            "resource_ids": {
                "goal_id": goal_id,
                "activation_state": target_state.value,
            },
        }
        stored = self.store.apply(
            proposal_id,
            current_state_fingerprint=(
                expected_fingerprint if idempotent_reapply else current_fingerprint
            ),
            receipt=receipt,
        )
        return {"proposal": stored, "turn": None}
