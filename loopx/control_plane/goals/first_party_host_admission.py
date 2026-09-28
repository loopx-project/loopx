from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from ...file_lock import exclusive_cross_runtime_file_lock
from ..effect_runtime import effect_runtime_result
from ..projects.registry_codec import (
    SOURCE_SESSION_PROFILE_ID,
    load_project_registry,
)
from .source_session_registry_state import (
    exact_goal_ref,
    guard_path,
    require_goal_id,
)


T = TypeVar("T")


class FirstPartyHostRuntimeRejected(RuntimeError):
    """Refusal to reuse Host state or accept a stale Host result."""

    def __init__(self, code: str) -> None:
        super().__init__(f"first-party Host runtime rejected: {code}")
        self.code = code


def _source_authority(registry_path: Path, goal_id: str) -> dict[str, Any]:
    if not registry_path.is_file():
        return {"kind": "unavailable", "reason": "registry_missing"}
    try:
        registry = load_project_registry(registry_path)
    except (OSError, TypeError, ValueError):
        return {"kind": "unavailable", "reason": "registry_unreadable"}
    if registry.get("profile_id") != SOURCE_SESSION_PROFILE_ID:
        return {"kind": "unavailable", "reason": "registry_unreadable"}
    goals = registry.get("goals")
    if not isinstance(goals, list) or any(
        not isinstance(item, Mapping) for item in goals
    ):
        return {"kind": "unavailable", "reason": "registry_unreadable"}
    matches = [
        item
        for item in goals
        if item.get("id") == goal_id and item.get("status") == "active"
    ]
    if len(matches) != 1:
        return {"kind": "absent"}
    goal = matches[0]
    goal_ref = {"goal_id": goal.get("id")}
    if "goal_instance_id" in goal:
        goal_ref["goal_instance_id"] = goal.get("goal_instance_id")
    return {"kind": "present", "goal_ref": goal_ref}


def capture_first_party_host_goal_ref(
    *,
    registry_path: Path,
    goal_id: str,
) -> dict[str, str] | None:
    """Capture the current exact GoalRef, or preserve a legacy registry."""

    requested_registry = registry_path.expanduser().resolve()
    require_goal_id(goal_id)
    if not requested_registry.is_file():
        return None
    registry = load_project_registry(requested_registry)
    if registry.get("profile_id") != SOURCE_SESSION_PROFILE_ID:
        return None
    with exclusive_cross_runtime_file_lock(
        guard_path(requested_registry, goal_id),
        operation="first_party_host_goal_capture",
    ):
        authority = _source_authority(requested_registry, goal_id)
        if authority.get("kind") != "present":
            raise FirstPartyHostRuntimeRejected(
                "goal_not_registered"
                if authority.get("kind") == "absent"
                else "goal_authority_unavailable"
            )
        goal_ref = authority.get("goal_ref")
        if (
            not isinstance(goal_ref, Mapping)
            or not isinstance(goal_ref.get("goal_id"), str)
            or not isinstance(goal_ref.get("goal_instance_id"), str)
        ):
            raise FirstPartyHostRuntimeRejected("goal_instance_id_missing")
        return exact_goal_ref(
            goal_ref["goal_id"],
            goal_ref["goal_instance_id"],
        )


@dataclass(frozen=True, slots=True)
class FirstPartyHostGoalAdmission:
    """Keep exact Host-state I/O inside one source Goal lifetime guard."""

    registry_path: Path
    goal_id: str
    planned_goal_ref: object | None
    source_profile: bool

    @classmethod
    def for_plan(
        cls,
        *,
        registry_path: Path,
        goal_id: str,
        planned_goal_ref: object | None,
    ) -> FirstPartyHostGoalAdmission:
        requested_registry = registry_path.expanduser().resolve()
        require_goal_id(goal_id)
        source_profile = planned_goal_ref is not None
        if not source_profile and requested_registry.is_file():
            source_profile = (
                load_project_registry(requested_registry).get("profile_id")
                == SOURCE_SESSION_PROFILE_ID
            )
        return cls(
            registry_path=requested_registry,
            goal_id=goal_id,
            planned_goal_ref=planned_goal_ref,
            source_profile=source_profile,
        )

    @property
    def enabled(self) -> bool:
        return self.source_profile

    def _decision(
        self,
        operation: str,
        *,
        host_state: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        decision = effect_runtime_result(
            "goal.first_party_host_runtime.decide",
            {
                "profile_id": (
                    SOURCE_SESSION_PROFILE_ID if self.source_profile else None
                ),
                "operation": operation,
                "planned_goal_ref": self.planned_goal_ref,
                "authority": _source_authority(
                    self.registry_path,
                    self.goal_id,
                ),
                **({"host_state": host_state} if host_state is not None else {}),
            },
        )
        if not isinstance(decision, dict):
            raise RuntimeError("first-party Host runtime decision must be an object")
        if decision.get("kind") == "reject":
            raise FirstPartyHostRuntimeRejected(str(decision.get("code") or "invalid"))
        return decision

    def select_state(
        self,
        *,
        read_state: Callable[[], T | None],
        goal_ref_of: Callable[[T], object],
        initialize_state: Callable[[dict[str, str]], T] | None = None,
    ) -> T | None:
        if not self.source_profile:
            state = read_state()
            return state
        with exclusive_cross_runtime_file_lock(
            guard_path(self.registry_path, self.goal_id),
            operation="first_party_host_state_select",
        ):
            state = read_state()
            host_state = (
                {"kind": "absent"}
                if state is None
                else {"kind": "present", "goal_ref": goal_ref_of(state)}
            )
            decision = self._decision("select_state", host_state=host_state)
            if decision.get("kind") == "start_new" and initialize_state is not None:
                goal_ref = decision.get("goal_ref")
                if not isinstance(goal_ref, dict):
                    raise RuntimeError(
                        "first-party Host start decision omitted goal_ref"
                    )
                return initialize_state(goal_ref)
            return state

    def require_current(self) -> None:
        if not self.source_profile:
            return
        with exclusive_cross_runtime_file_lock(
            guard_path(self.registry_path, self.goal_id),
            operation="first_party_host_current_check",
        ):
            self._decision("require_current")

    def accept_result(self, commit_result: Callable[[], T]) -> T:
        if not self.source_profile:
            return commit_result()
        with exclusive_cross_runtime_file_lock(
            guard_path(self.registry_path, self.goal_id),
            operation="first_party_host_result_accept",
        ):
            decision = self._decision("accept_result")
            if decision.get("kind") != "accept_result":
                raise RuntimeError(
                    "first-party Host result decision did not accept a result"
                )
            return commit_result()
