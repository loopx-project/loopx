from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from ..effect_runtime import effect_runtime_projection_result, effect_runtime_result
from .contracts import (
    EffectRuntimeProjectionFn,
    EffectRuntimeResultFn,
    TurnContextHookRegistration,
    TurnContextSourceFn,
    as_string_dict,
)
from .request_scope import capability_hook_request_scope


def turn_context_source(payload: Mapping[str, object]) -> Mapping[str, object]:
    """Use the host envelope or the native quota identity, without new authority."""

    if "work_contract" in payload:
        from ..quota.turn_envelope import complete_turn_host_source, turn_plan_host_source

        source = complete_turn_host_source(
            turn_plan_host_source(payload),
            operation="plan_work",
        )
        assert source is not None
        return source
    envelope = payload.get("turn_envelope")
    if isinstance(envelope, Mapping):
        return envelope
    if "schema_version" in payload or "contract_binding" in payload:
        from ..quota.turn_envelope import quota_capability_automation_source

        # A declared native contract must pass the same complete-source owner
        # as participation. Never reinterpret a damaged version as raw quota.
        source = quota_capability_automation_source(payload)
        if isinstance(payload.get("heartbeat_receipt"), Mapping):
            return {**payload, "agent_id": source.get("agent_id")}
        return {"goal_id": source.get("goal_id"), "agent_id": source.get("agent_id")}
    agent_id = payload.get("agent_id")
    identity = payload.get("agent_identity")
    if isinstance(identity, Mapping):
        scoped_agent = identity.get("agent_id")
        if agent_id and scoped_agent and agent_id != scoped_agent:
            raise ValueError("context source has conflicting Agent identities")
        agent_id = agent_id or scoped_agent
    if isinstance(payload.get("heartbeat_receipt"), Mapping):
        # Keep the native source for the typed quota signature owner. Identity
        # alone cannot invalidate context when the admitted authority changes;
        # Python does not decode the quota boundary here.
        return {**payload, "agent_id": agent_id}
    return {"goal_id": payload.get("goal_id"), "agent_id": agent_id}


def turn_context_binding_source(
    payload: Mapping[str, object],
    *,
    from_journal: bool = False,
    turn_context_source_fn: TurnContextSourceFn | None = None,
) -> Mapping[str, object]:
    """Resolve the source record used to bind and project turn context."""

    from ..quota.turn_envelope import (
        COMPLETE_TURN_PLAN_SCHEMA_VERSION,
        complete_turn_host_source,
        turn_plan_host_source,
    )

    if payload.get("schema_version") == COMPLETE_TURN_PLAN_SCHEMA_VERSION:
        source = complete_turn_host_source(
            turn_plan_host_source(payload),
            operation="host_context_binding",
            recorded_capability_result=from_journal,
        )
        assert source is not None
        return source
    resolver = turn_context_source_fn or turn_context_source
    return resolver(payload)


def dispatch_turn_context_hooks(
    payload: dict[str, object],
    registrations: Sequence[TurnContextHookRegistration],
    *,
    effect_runtime_result_fn: EffectRuntimeResultFn | None = None,
    turn_context_source_fn: TurnContextSourceFn | None = None,
) -> None:
    """Invoke and validate registered turn-context hooks without gating work."""

    invoke = effect_runtime_result_fn or effect_runtime_result
    contributions: list[dict[str, object]] = []
    failures: list[dict[str, str]] = []
    seen_hooks: set[str] = set()
    seen_slots: set[str] = set()
    with capability_hook_request_scope():
        for registration in registrations:
            try:
                if registration.hook_id in seen_hooks or seen_slots.intersection(
                    registration.readback_slots
                ):
                    raise ValueError("duplicate context registration")
                seen_hooks.add(registration.hook_id)
                seen_slots.update(registration.readback_slots)
                invoke(
                    "capability_hook.turn_context.validate_registration",
                    {"registration": registration.contract()},
                )
                if any(key in payload for key in registration.readback_slots):
                    raise ValueError("context readback slot already contributed")
                result = registration.producer()
                readback_only = (
                    result.get("context_fields") == {}
                    and result.get("instructions") == []
                    and result.get("result_fields") == {}
                )
                if readback_only:
                    binding: dict[str, object] = {"readback_only": True}
                else:
                    source = turn_context_binding_source(
                        payload,
                        turn_context_source_fn=turn_context_source_fn,
                    )
                    binding = {"source": source}
                    if isinstance(source.get("heartbeat_receipt"), Mapping):
                        from ..quota.turn_envelope import pack_turn_source

                        transport = pack_turn_source(source)
                        if transport is not None:
                            binding = {"source_transport": transport}
                raw_contribution = invoke(
                    "capability_hook.turn_context.validate",
                    {
                        "registration": registration.contract(),
                        "result": result,
                        **binding,
                    },
                )
                contribution = as_string_dict(
                    raw_contribution,
                    error_message="turn context validation returned invalid result",
                )
                readback = as_string_dict(
                    contribution.pop("readback"),
                    error_message="turn context validation returned invalid readback",
                )
                payload.update(readback)
                if (
                    contribution.get("context_fields")
                    or contribution.get("instructions")
                    or contribution.get("result_fields")
                ):
                    contributions.append(contribution)
            except Exception:  # Optional context failures never gate business work.
                failures.append(
                    {
                        "hook_id": registration.hook_id,
                        "error_code": "context_hook_failed",
                    }
                )
    if contributions:
        payload["turn_context_contributions"] = contributions
    if failures:
        payload["turn_context_hook_failures"] = failures


def project_turn_context(
    payload: Mapping[str, object],
    *,
    from_journal: bool = False,
    effect_runtime_projection_result_fn: EffectRuntimeProjectionFn | None = None,
    turn_context_source_fn: TurnContextSourceFn | None = None,
) -> dict[str, object]:
    """Project contributed turn context via the TS projection owner."""

    contributions = payload.get("turn_context_contributions")
    if not isinstance(contributions, list) or not contributions:
        return {}
    readback: dict[str, object] = {}
    for item in contributions:
        if not isinstance(item, Mapping):
            continue
        slots = item.get("readback_slots")
        if isinstance(slots, (list, tuple)):
            readback.update(
                {key: payload.get(key) for key in slots if isinstance(key, str)}
            )
    project_invoke = (
        effect_runtime_projection_result_fn or effect_runtime_projection_result
    )
    try:
        projected = project_invoke(
            "capability_hook.turn_context.project",
            {
                "contributions": contributions,
                "source": turn_context_binding_source(
                    payload,
                    from_journal=from_journal,
                    turn_context_source_fn=turn_context_source_fn,
                ),
                "readback": readback,
            },
        )
    except Exception:  # Optional context transport cannot gate the underlying work.
        return {}
    if not isinstance(projected, Mapping):
        return {}
    return {str(key): value for key, value in projected.items()}


def host_context_projection(request: Mapping[str, object]) -> dict[str, object]:
    """Extract capability host context fields, instructions, and result schema."""

    raw_fields = request.get("capability_context_fields")
    fields: Iterable[object] = (
        raw_fields
        if isinstance(raw_fields, Iterable)
        and not isinstance(raw_fields, (str, bytes, Mapping))
        else ()
    )
    projection: dict[str, object] = {
        key: request[key] for key in fields if isinstance(key, str) and key in request
    }
    raw_instructions = request.get("capability_instructions")
    raw_result_fields = request.get("capability_result_fields")
    if projection or raw_instructions or raw_result_fields:
        instructions_list = (
            list(raw_instructions)
            if isinstance(raw_instructions, Iterable)
            and not isinstance(raw_instructions, (str, bytes, Mapping))
            else []
        )
        projection["capability_instructions"] = instructions_list
        projection["capability_result_fields"] = (
            dict(raw_result_fields)
            if isinstance(raw_result_fields, Mapping)
            else {}
        )
    return projection


def capability_result_fields(
    request: Mapping[str, object] | None,
) -> dict[str, object]:
    """Return declared capability result fields from a host request packet."""

    value = (
        request.get("capability_result_fields")
        if isinstance(request, Mapping)
        else None
    )
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}
