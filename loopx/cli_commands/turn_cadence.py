from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from pathlib import Path
from dataclasses import dataclass
from typing import Any

from ..control_plane.effect_runtime import effect_runtime_result


@dataclass(slots=True)
class ManagedCadenceStart:
    """Owner-cadence callbacks for one managed Turn start.

    `admit` reserves (or resumes) the interval slot before any host or journal
    attempt; `confirm` marks that reservation as a real host attempt once the
    Turn journal is durable. Keeping them separate means a crash in between
    leaves a resumable reservation rather than a permanently rejected Turn.
    `accepted` returns the most recent admission so a caller that admits ahead
    of the ordinary claim can hand the same reservation to the driver.
    """

    admit: Callable[[Mapping[str, Any]], dict[str, Any]]
    confirm: Callable[[], None]
    accepted: Callable[[], dict[str, Any] | None]
    set_on_admitted: Callable[[Callable[[], None] | None], None]


def managed_cadence_start(
    *,
    runtime_root: Path,
    goal_id: str,
    agent_id: str | None,
    automation_id: str | None,
    manual_reason: str | None,
    on_admitted: Callable[[], None] | None = None,
) -> ManagedCadenceStart:
    """Bind one managed Turn start to the TypeScript owner-cadence store."""

    admitted_request: dict[str, Any] = {}
    latest_admission: dict[str, Any] | None = None
    admission_hook: list[Callable[[], None] | None] = [on_admitted]
    hook_pending = [False]

    def set_on_admitted(callback: Callable[[], None] | None) -> None:
        admission_hook[0] = callback
        if hook_pending[0] and callback is not None:
            # A reservation admitted ahead of the surrounding Turn preparation
            # delivers its receipt writer as soon as the Turn adopts it.
            hook_pending[0] = False
            callback()

    def admit(identity: Mapping[str, Any]) -> dict[str, Any]:
        nonlocal latest_admission
        now_ms = time.time_ns() // 1_000_000
        base_request_id = f"{identity['turn_key']}:{identity['attempt']}"

        def request_admission(request_id: str) -> dict[str, Any]:
            return effect_runtime_result(
                "quota.automation_cadence.admit",
                {
                    "runtime_root": str(runtime_root),
                    "goal_id": goal_id,
                    "agent_id": agent_id,
                    "automation_id": automation_id,
                    "request_id": request_id,
                    "trigger_at_ms": now_ms,
                    "now_ms": now_ms,
                    "manual_reason": manual_reason,
                },
                retry_safe=False,
            )

        request_id = base_request_id
        admission = request_admission(request_id)
        if admission.get("admitted") is not True and (
            admission.get("reason") == "duplicate_or_stale_trigger"
        ):
            # A durable earlier attempt keeps its own request identity
            # fail-closed.  Once the owner interval has elapsed, this Turn may
            # start again under a fresh attempt identity, exactly as the
            # attempt counter did before admission moved ahead of the claim.
            request_id = f"{base_request_id}:{now_ms}"
            admission = request_admission(request_id)
        admitted_request.clear()
        if admission.get("admitted") is True:
            admitted_request["request_id"] = request_id
            admitted_request["reserved"] = admission.get("reserved") is True
            hook = admission_hook[0]
            if hook is not None:
                hook()
            else:
                hook_pending[0] = True
        latest_admission = {
            key: admission.get(key)
            for key in (
                "admitted", "reserved", "resumed", "reason", "next_eligible_at_ms",
                "min_interval_minutes", "pre_model_admission",
            )
        }
        return dict(latest_admission)

    def accepted() -> dict[str, Any] | None:
        return dict(latest_admission) if latest_admission is not None else None

    def confirm() -> None:
        request_id = admitted_request.get("request_id")
        if admitted_request.get("reserved") is not True or not isinstance(request_id, str):
            return
        confirmation = effect_runtime_result(
            "quota.automation_cadence.confirm_start",
            {
                "runtime_root": str(runtime_root),
                "goal_id": goal_id,
                "agent_id": agent_id,
                "automation_id": automation_id,
                "request_id": request_id,
            },
            retry_safe=True,
        )
        if confirmation.get("confirmed") is not True:
            raise ValueError(
                "managed Turn start could not be confirmed against the owner cadence store"
            )

    return ManagedCadenceStart(
        admit=admit, confirm=confirm, accepted=accepted,
        set_on_admitted=set_on_admitted,
    )
