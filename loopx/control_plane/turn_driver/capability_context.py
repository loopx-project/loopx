"""Transport registered capability contributions without interpreting their domain."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..capability_hooks import TurnContextHookRegistration
from ..effect_runtime import effect_runtime_result


def _source(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    envelope = payload.get("turn_envelope")
    return (
        envelope
        if isinstance(envelope, Mapping)
        else {
            "goal_id": payload.get("goal_id"),
            "agent_id": payload.get("agent_id"),
        }
    )


def dispatch_turn_context_hooks(
    payload: dict[str, Any],
    registrations: Sequence[TurnContextHookRegistration],
) -> None:
    contributions: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    seen_hooks: set[str] = set()
    seen_slots: set[str] = set()
    for registration in registrations:
        try:
            if registration.hook_id in seen_hooks or seen_slots.intersection(
                registration.readback_slots
            ):
                raise ValueError("duplicate context registration")
            seen_hooks.add(registration.hook_id)
            seen_slots.update(registration.readback_slots)
            effect_runtime_result(
                "capability_hook.turn_context.validate_registration",
                {
                    "registration": registration.contract(),
                },
            )
            if any(key in payload for key in registration.readback_slots):
                raise ValueError("context readback slot already contributed")
            result = registration.producer()
            contribution = effect_runtime_result(
                "capability_hook.turn_context.validate",
                {
                    "registration": registration.contract(),
                    "result": result,
                    "source": _source(payload),
                },
            )
            readback = contribution.pop("readback")
            payload.update(readback)
            if (
                contribution["context_fields"]
                or contribution["instructions"]
                or contribution["result_fields"]
            ):
                contributions.append(contribution)
        except Exception:  # Optional context failures never gate business work.
            failures.append(
                {"hook_id": registration.hook_id, "error_code": "context_hook_failed"}
            )
    if contributions:
        payload["turn_context_contributions"] = contributions
    if failures:
        payload["turn_context_hook_failures"] = failures


def project_turn_context(payload: Mapping[str, Any]) -> dict[str, Any]:
    context: dict[str, Any] = {}
    instructions: list[str] = []
    result_fields: dict[str, Any] = {}
    contributions = payload.get("turn_context_contributions")
    if not isinstance(contributions, list):
        return {}
    for item in contributions:
        try:
            slots = item["readback_slots"]
            contribution = effect_runtime_result(
                "capability_hook.turn_context.project",
                {
                    "contribution": item,
                    "source": _source(payload),
                    "readback": {key: payload.get(key) for key in slots},
                },
            )
            if set(context) & set(contribution["context_fields"]) or set(
                result_fields
            ) & set(contribution["result_fields"]):
                raise ValueError("conflicting capability contribution")
            context.update(contribution["context_fields"])
            instructions.extend(contribution["instructions"])
            result_fields.update(contribution["result_fields"])
        except (
            Exception
        ):  # A stale/private contribution is omitted, never repaired from cached data.
            continue
    if not (context or instructions or result_fields):
        return {}
    return {
        **context,
        "capability_context_fields": list(context),
        "capability_instructions": instructions,
        "capability_result_fields": result_fields,
    }


def host_context_projection(request: Mapping[str, Any]) -> dict[str, Any]:
    fields = request.get("capability_context_fields") or ()
    projection = {key: request[key] for key in fields if key in request}
    if (
        projection
        or request.get("capability_instructions")
        or request.get("capability_result_fields")
    ):
        projection["capability_instructions"] = list(
            request.get("capability_instructions") or ()
        )
        projection["capability_result_fields"] = dict(
            request.get("capability_result_fields") or {}
        )
    return projection


def capability_result_fields(request: Mapping[str, Any] | None) -> dict[str, Any]:
    value = (
        request.get("capability_result_fields")
        if isinstance(request, Mapping)
        else None
    )
    return dict(value) if isinstance(value, Mapping) else {}
