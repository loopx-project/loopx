"""Query-ready caller boundary over the existing config, recall and applier owners.

Python retains the provider/model adapters and transient private values. The
TypeScript owner decides admission and completion; no new store or opt-in.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any

from ...control_plane.effect_runtime import effect_runtime_result
from .application import (
    RewardMemoryApplier, RewardMemoryRecallItem, RewardMemoryRecallSession,
    apply_reward_memory_recall,
)
from .runtime_hooks import run_reward_memory_automatic_recall_hook


@dataclass(frozen=True)
class RewardMemoryDecisionResult:
    """Only public_packet is a projection. All other fields stay caller-private."""

    public_packet: dict[str, Any]
    output: Any
    base_output: Any
    request_digest: str
    request: dict[str, Any]
    recall_session: RewardMemoryRecallSession | None = None
    application_receipt: Mapping[str, Any] | None = None
    recall_telemetry: Mapping[str, Any] | None = None


def _transport_failure(
    request: Mapping[str, Any], reason: str, telemetry: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    # Transport cannot invent a successful TS decision when the kernel is unavailable.
    return {
        "schema_version": "reward_memory_decision_consumption_v0",
        "status": "incomplete", "reason_code": reason,
        **{key: request.get(key) for key in ("mode", "application_kind", "application_id", "artifact_ref", "surface_id")},
        "should_recall": False, "decision_consumption_complete": False,
        "context_delivery_verified": False, "semantic_disposition": None,
        "preserve_base_output": True, "research_may_continue": True,
        "grants_new_action_authority": False, "utility_verified": False,
        "external_writes_performed": False, "raw_content_captured": False,
        **dict(telemetry or {"provider_call_count": 0, "filtered_count": 0,
                            "result_readback_verified": False}),
    }


def _recall_telemetry(hook: Mapping[str, Any]) -> dict[str, Any]:
    telemetry = hook.get("telemetry") or {}
    attempts = hook.get("recall_attempts") or []
    return {
        "result_readback_verified": telemetry.get("result_readback_verified", False),
        "provider_call_count": telemetry.get("provider_call_count", 0),
        "filtered_count": sum(item.get("filtered_item_count", 0) for item in attempts),
        "recall_status": attempts[-1].get("status") if attempts else None,
        "boundary_reason_code": hook.get("reason_code"),
    }


def _project(
    request: dict[str, Any], status: str, telemetry: Mapping[str, Any],
    receipt: Mapping[str, Any] | None,
) -> dict[str, Any]:
    return effect_runtime_result("reward_memory.decision.project", {
        "request": request,
        "observation": {
            "status": status, **dict(telemetry),
            # No query, summary, lesson, model rationale or base artifact crosses into TS.
            "application_receipt": {
                key: value for key, value in (receipt or {}).items()
                if key in {"schema_version", "application_id", "artifact_ref", "surface_id",
                           "outcome", "memory_ref_digests", "current_artifact_verified",
                           "result_readback_verified"}
            },
        },
    })


def run_reward_memory_decision(
    config: Mapping[str, Any] | None,
    *,
    query_ready: bool,
    mode: str = "execute",
    application_kind: str | None = None,
    apply_memory: RewardMemoryApplier | None = None,
    previous_result: RewardMemoryDecisionResult | None = None,
    **hook_arguments: Any,
) -> RewardMemoryDecisionResult | None:
    """Consume an explicit decision question using the existing automatic hook.

Pass its existing keyword arguments unchanged. No configured automatic recall
means None (no added packet, TS call or provider call). Execute requires an
explicit context_delivery or semantic_application callback. Retain the result
privately to replay the exact request or assess delivered context without recall.
"""
    automation = config.get("automation") if isinstance(config, Mapping) else None
    if not isinstance(automation, Mapping) or automation.get("automatic_recall") is not True:
        return None
    base = hook_arguments.get("base_output")
    request = {"mode": mode, "query_ready": query_ready, "application_kind": application_kind,
               "has_applier": callable(apply_memory),
               **{key: hook_arguments.get(key) for key in ("application_id", "artifact_ref", "surface_id")}}
    digest = ""
    session = None
    receipt = None
    telemetry = None
    try:
        digest = hashlib.sha256(json.dumps({
            "config": config, "request": request,
            "context": {key: value for key, value in hook_arguments.items() if key != "provider"},
        }, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        if previous_result is not None:
            if previous_result.request_digest != digest:
                return RewardMemoryDecisionResult(
                    _transport_failure(request, "replay_request_mismatch"), base, base, digest, request,
                )
            return previous_result
        plan = effect_runtime_result("reward_memory.decision.plan", request)
        if not plan["should_recall"]:
            return RewardMemoryDecisionResult(plan, base, base, digest, request)
        captured: tuple[RewardMemoryRecallItem, ...] = ()

        def apply(original: Any, items: tuple[RewardMemoryRecallItem, ...]) -> Mapping[str, Any]:
            nonlocal captured
            captured = items
            assert apply_memory is not None
            return apply_memory(original, items)

        # Fail-open preserves the original baseline even if a model mutates its input.
        arguments = {**hook_arguments, "base_output": deepcopy(base)}
        hook = run_reward_memory_automatic_recall_hook(
            config, **arguments,
            apply_memory=apply if mode == "execute" else None,
        )
        application = hook.get("application") or {}
        attempts = hook.get("recall_attempts") or []
        session = RewardMemoryRecallSession(attempts[-1], captured) if captured and attempts else None
        receipt = application.get("receipt")
        telemetry = _recall_telemetry(hook)
        packet = _project(request, hook["status"], telemetry, receipt)
        return RewardMemoryDecisionResult(
            packet, base if packet["preserve_base_output"] else hook["output"],
            base, digest, request, session, receipt, telemetry,
        )
    except (KeyError, OSError, RuntimeError, TypeError, ValueError):
        return RewardMemoryDecisionResult(
            _transport_failure(request, "consumer_input_or_runtime_failed", telemetry),
            base, base, digest, request, session, receipt, telemetry,
        )


def assess_reward_memory_decision(
    delivered: RewardMemoryDecisionResult,
    *,
    apply_memory: RewardMemoryApplier,
) -> RewardMemoryDecisionResult:
    """Actual caller reasoning over exact retained items; never queries a provider.

    The callback returns the existing SDK's output, applied/ignored/refuted,
    current_artifact_verified, memory_refs and reasoning_summary fields. A
    previously assessed result is already the receipt, not another model call.
    """
    if delivered.public_packet.get("decision_consumption_complete") is True:
        return delivered
    request = {**delivered.request, "application_kind": "semantic_application",
               "has_applier": callable(apply_memory)}
    receipt = delivered.application_receipt
    try:
        if delivered.recall_session is None or delivered.public_packet.get("context_delivery_verified") is not True:
            return replace(delivered, public_packet=_transport_failure(request, "verified_context_delivery_required", delivered.recall_telemetry), output=delivered.base_output)
        application = apply_reward_memory_recall(
            deepcopy(delivered.base_output), delivered.recall_session,
            application_id=request["application_id"], artifact_ref=request["artifact_ref"],
            apply_memory=apply_memory,
        )
        receipt = application["receipt"]
        # Reassessment is not recall: retain every corpus's original cumulative counters.
        packet = _project(request, application["status"], delivered.recall_telemetry or {},
                          application["receipt"])
        return replace(delivered, public_packet=packet, request=request,
                       output=delivered.base_output if packet["preserve_base_output"] else application["output"],
                       application_receipt=application["receipt"])
    except (KeyError, OSError, RuntimeError, TypeError, ValueError):
        return replace(delivered, public_packet=_transport_failure(request, "consumer_input_or_runtime_failed", delivered.recall_telemetry), output=delivered.base_output,
                       request=request, application_receipt=receipt)
