from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from ...file_lock import (
    cross_runtime_lock_witness,
    exclusive_cross_runtime_file_lock,
)
from ..coordination.shadow_management import runtime_artifact_lock_target
from ..effect_runtime import effect_runtime_result
from ..projects.registry_codec import (
    SOURCE_SESSION_PROFILE_ID,
    load_project_registry,
)
from ..effect_program import TurnProviderStepKind
from .source_session_registry_state import (
    exact_goal_ref,
    guard_path,
    require_goal_id,
)
from .source_session_turn_effects import (
    JournalPersist,
    SourceTurnEffect,
    SourceTurnEffectRejected,
    prepare_source_turn_effect,
    release_source_turn_effect,
    source_turn_effect_allows_absent_reexecute,
)


T = TypeVar("T")


class FirstPartyHostRuntimeRejected(RuntimeError):
    """Refusal to reuse Host state or accept a stale Host result."""

    def __init__(self, code: str) -> None:
        super().__init__(f"first-party Host runtime rejected: {code}")
        self.code = code


@dataclass(frozen=True, slots=True)
class FirstPartyHostTurnEffectAdmission:
    goal_admission: FirstPartyHostGoalAdmission
    turn_key: str
    journal_path: Path

    def _effect(
        self,
        step_kind: TurnProviderStepKind,
        effect_ref: str,
    ) -> SourceTurnEffect:
        goal_ref = self.goal_admission.planned_goal_ref
        if not isinstance(goal_ref, Mapping):
            raise FirstPartyHostRuntimeRejected("goal_instance_id_missing")
        goal_id = goal_ref.get("goal_id")
        goal_instance_id = goal_ref.get("goal_instance_id")
        if not isinstance(goal_id, str) or not isinstance(goal_instance_id, str):
            raise FirstPartyHostRuntimeRejected("goal_instance_id_missing")
        return SourceTurnEffect(
            goal_ref=exact_goal_ref(goal_id, goal_instance_id),
            turn_key=self.turn_key,
            step_kind=step_kind,
            effect_ref=effect_ref,
            journal_path=self.journal_path,
        )

    @contextmanager
    def _runtime_artifact_lifetime(self, *, operation: str) -> Iterator[None]:
        path = self.journal_path.expanduser().resolve()
        turns_dir = path.parent
        goal_dir = turns_dir.parent
        goals_dir = goal_dir.parent
        if (
            turns_dir.name != "turns"
            or goal_dir.name != self.goal_admission.goal_id
            or goals_dir.name != "goals"
        ):
            raise FirstPartyHostRuntimeRejected("journal_path_mismatch")
        with exclusive_cross_runtime_file_lock(
            runtime_artifact_lock_target(
                goals_dir.parent,
                self.goal_admission.goal_id,
            ),
            operation=operation,
        ):
            yield

    def prepare(
        self,
        step_kind: TurnProviderStepKind,
        effect_ref: str,
        persist_journal: JournalPersist,
    ) -> None:
        try:
            with self._runtime_artifact_lifetime(
                operation="source_turn_effect_runtime_artifact_admit",
            ):
                prepare_source_turn_effect(
                    registry_path=self.goal_admission.registry_path,
                    goal_id=self.goal_admission.goal_id,
                    effect=self._effect(step_kind, effect_ref),
                    source_admission=self.goal_admission.source_journal_admission_locked,
                    persist_journal=persist_journal,
                )
        except SourceTurnEffectRejected as exc:
            raise FirstPartyHostRuntimeRejected(exc.code) from exc

    def hold(
        self,
        step_kind: TurnProviderStepKind,
        effect_ref: str,
        persist_journal: JournalPersist,
    ) -> None:
        self.prepare(step_kind, effect_ref, persist_journal)

    def release(
        self,
        step_kind: TurnProviderStepKind,
        effect_ref: str,
        persist_journal: JournalPersist,
    ) -> None:
        try:
            with self._runtime_artifact_lifetime(
                operation="source_turn_effect_runtime_artifact_release",
            ):
                release_source_turn_effect(
                    registry_path=self.goal_admission.registry_path,
                    goal_id=self.goal_admission.goal_id,
                    effect=self._effect(step_kind, effect_ref),
                    source_admission=self.goal_admission.source_journal_admission_locked,
                    persist_journal=persist_journal,
                )
        except SourceTurnEffectRejected as exc:
            raise FirstPartyHostRuntimeRejected(exc.code) from exc

    def allows_absent_reexecute(
        self,
        step_kind: TurnProviderStepKind,
        effect_ref: str,
    ) -> bool:
        try:
            return source_turn_effect_allows_absent_reexecute(
                registry_path=self.goal_admission.registry_path,
                goal_id=self.goal_admission.goal_id,
                effect=self._effect(step_kind, effect_ref),
            )
        except SourceTurnEffectRejected as exc:
            raise FirstPartyHostRuntimeRejected(exc.code) from exc


def source_goal_authority(registry_path: Path, goal_id: str) -> dict[str, Any]:
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
        authority = source_goal_authority(requested_registry, goal_id)
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
                "authority": source_goal_authority(
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

    @contextmanager
    def current_lifetime(self, *, operation: str) -> Iterator[None]:
        """Keep a source-session side effect inside its planned Goal lifetime."""

        if not self.source_profile:
            yield
            return
        with exclusive_cross_runtime_file_lock(
            guard_path(self.registry_path, self.goal_id),
            operation=operation,
        ):
            self._decision("require_current")
            yield

    @contextmanager
    def source_journal_admission(
        self,
        *,
        runtime_root: Path,
    ) -> Iterator[dict[str, Any] | None]:
        """Hand one journal mutation to the TS owner under the source guard."""

        if not self.source_profile:
            yield None
            return
        target = guard_path(self.registry_path, self.goal_id)
        with exclusive_cross_runtime_file_lock(
            runtime_artifact_lock_target(
                runtime_root.expanduser().resolve(),
                self.goal_id,
            ),
            operation="first_party_host_journal_runtime_artifact_guard",
        ):
            with exclusive_cross_runtime_file_lock(
                target,
                operation="first_party_host_journal_commit",
            ):
                yield self.source_journal_admission_locked()

    def source_journal_admission_locked(self) -> dict[str, Any]:
        """Build a TS handoff while the caller holds this Goal's source guard."""

        target = guard_path(self.registry_path, self.goal_id)
        return {
            "schema_version": "loopx_turn_journal_source_admission_v0",
            "profile_id": SOURCE_SESSION_PROFILE_ID,
            "registry_path": str(self.registry_path),
            "planned_goal_ref": self.planned_goal_ref,
            "authority": source_goal_authority(
                self.registry_path,
                self.goal_id,
            ),
            "lock": cross_runtime_lock_witness(target),
        }

    def turn_effect_admission(
        self,
        *,
        turn_key: str,
        journal_path: Path,
    ) -> FirstPartyHostTurnEffectAdmission | None:
        if not self.source_profile:
            return None
        return FirstPartyHostTurnEffectAdmission(self, turn_key, journal_path)

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
