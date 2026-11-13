from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, NamedTuple

from ..control_plane.effect_runtime import effect_runtime_result


class ManagedCadenceStart(NamedTuple):
    """Owner-cadence callbacks for one managed Turn start.

    `admit` reserves (or resumes) the interval slot before any host or journal
    attempt; `confirm` marks that reservation as a real host attempt once the
    Turn journal is durable. Keeping them separate means a crash in between
    leaves a resumable reservation rather than a permanently rejected Turn.
    """

    admit: Callable[[Mapping[str, Any]], dict[str, Any]]
    confirm: Callable[[], None]


def managed_cadence_start(
    *,
    runtime_root: Path,
    goal_id: str,
    agent_id: str | None,
    automation_id: str | None,
    manual_reason: str | None,
    on_admitted: Callable[[], None] | None = None,
    request_id: str | None = None,
) -> ManagedCadenceStart:
    """Bind one managed Turn start to the TypeScript owner-cadence store."""

    admitted_request: dict[str, Any] = {}

    def admit(identity: Mapping[str, Any]) -> dict[str, Any]:
        now_ms = time.time_ns() // 1_000_000
        start_request_id = f"{request_id or identity['turn_key']}:{identity['attempt']}"
        def reserve(start_id: str) -> dict[str, Any]:
            return effect_runtime_result(
                "quota.automation_cadence.admit",
                {
                    "runtime_root": str(runtime_root),
                    "goal_id": goal_id,
                    "agent_id": agent_id,
                    "automation_id": automation_id,
                    "request_id": start_id,
                    "trigger_at_ms": now_ms,
                    "now_ms": now_ms,
                    "manual_reason": manual_reason,
                },
                retry_safe=False,
            )
        admission = reserve(start_request_id)
        legacy_request_id = f"{identity['turn_key']}:{identity['attempt']}" if "turn_key" in identity else None
        if (request_id is not None and legacy_request_id is not None
                and start_request_id != legacy_request_id
                and admission.get("admitted") is not True
                and admission.get("reason") == "minimum_interval_wait"):
            # Historical Codex journals reserved by transaction key. Retry only
            # this exact identity, after a no-write wait, so their unstarted
            # reservations remain recoverable; never bypass a started request.
            admission = reserve(legacy_request_id)
            start_request_id = legacy_request_id
        admitted_request.clear()
        if admission.get("admitted") is True:
            admitted_request["request_id"] = start_request_id
            admitted_request["reserved"] = admission.get("reserved") is True
            if on_admitted is not None:
                on_admitted()
        return {
            key: admission.get(key)
            for key in (
                "admitted", "reserved", "resumed", "reason", "next_eligible_at_ms",
                "min_interval_minutes", "pre_model_admission",
            )
        }

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

    return ManagedCadenceStart(admit=admit, confirm=confirm)
