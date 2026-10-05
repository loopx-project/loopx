"""Bounded direction inference using the existing Turn journal and Host IO.

Todo/run receipts remain the commit authority. This module records which read
was delivered to which inference, and never manufactures a fresh token for a
previous output. The journal's lane lock serializes inference; source and
provider locks are released before the Host is called.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ..effect_runtime import effect_runtime_result
from .journal_store import journal_committed_effect_id
from .settlement import invoke_result_effect, terminal_closeout_requirement


MAX_DIRECTION_ATTEMPTS = 2


def prepare_first_delivery(
    *, plan: Mapping[str, Any], request: Mapping[str, Any], result: dict[str, Any],
    journal: dict[str, Any], persist: Callable[[Mapping[str, Any]], None],
    read_context: Callable[[str], dict[str, Any]],
    invoke_host: Callable[[Mapping[str, Any]], dict[str, Any]],
    completion_intent: Callable[..., dict[str, Any]] | None,
    commit_result: Callable[..., dict[str, Any]] | None,
) -> dict[str, Any]:
    """Preserve native result/terminal ordering before bounded direction review."""
    original = journal.get("delivery_result_context")
    if not isinstance(original, dict):
        raise ValueError("Protected Turn has no result read identity; do not attach a new read to the previous result.")
    result = {**result, "delivery_read_context_id": original["read_context_id"]}
    terminal_needed = False
    if result.get("result_kind") in {"repair_required", "replan_required"}:
        raise ValueError("First delivery does not support compound repair or replan mutations; retain the candidate for explicit recovery.")
    if result.get("result_kind") == "validated_completion":
        if completion_intent is None or commit_result is None:
            raise ValueError("Protected completion adapter is unavailable")
        terminal_needed, intent_error = terminal_closeout_requirement(
            plan=plan, result=result, journal=journal, completion_intent=completion_intent)
        if intent_error:
            raise ValueError(intent_error)
        if not terminal_needed:
            completion = invoke_result_effect(commit_result, result,
                f"{journal_committed_effect_id(journal)}#durable_writeback")
            if completion.get("ok") is not True:
                raise ValueError(str(completion.get("reason") or "Protected result commit failed"))
            journal["delivery_completion"] = dict(completion)
            persist(journal)
            result["_delivery_completion"] = dict(completion)
    result = review_first_delivery(request=request, result=result, journal=journal,
        persist=persist, read_context=read_context, invoke_host=invoke_host)
    if terminal_needed and result.get("_direction_decision") != "terminal_ready":
        raise ValueError("Current direction does not authorize the requested no-followup closeout; retain the original Turn and resolve the remaining work.")
    return result


def direction_host_request(request: Mapping[str, Any], context: Mapping[str, Any], result: Mapping[str, Any]) -> dict[str, Any]:
    return {**request, "direction_review": {
        "context": dict(context),
        "candidate_result": {key: result.get(key) for key in (
            "result_kind", "summary", "classification", "delivery_outcome", "next_action")},
        "instructions": "Judge the direction from this current basis. Do not execute implementation, Todo writes, quota spend or external effects. "
            "Return read_context_id, decision (continue, revalidate_result, terminal_ready), and exactly one agent_vision or vision_unchanged_reason. "
            "Use revalidate_result when the current acceptance or dependency invalidates the prepared result. A committed result is retained; pending work is not Goal completion.",
    }}


def review_first_delivery(
    *, request: Mapping[str, Any], result: dict[str, Any], journal: dict[str, Any],
    persist: Callable[[Mapping[str, Any]], None], read_context: Callable[[str], dict[str, Any]],
    invoke_host: Callable[[Mapping[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    attempts = journal.setdefault("direction_reviews", [])
    current = attempts[-1] if attempts else None
    if current is None or current.get("decision") is None:
        if len(attempts) >= MAX_DIRECTION_ATTEMPTS:
            raise ValueError("Direction review budget exhausted; retain this Turn and its receipts for operator recovery.")
        context = read_context("first_delivery")
        current = {"read_context_id": context["read_context_id"], "versions": context["versions"],
            "purpose": "first_delivery", "attempt": len(attempts) + 1, "response": None}
        attempts.append(current)
        persist(journal)
        observation = invoke_host(direction_host_request(request, context, result))
        current["host_observation"] = {key: observation.get(key) for key in ("ok", "reason", "returncode")}
        persist(journal)
        if observation.get("ok") is not True:
            raise ValueError("Direction Host failed; result receipts remain committed and quota has not been spent.")
        decision = effect_runtime_result("turn.first_delivery.evaluate", {
            "response": observation["value"], "read_context_id": context["read_context_id"],
        })
        current.update(decision=decision["decision"], response=decision)
        persist(journal)
    if current["decision"] == "revalidate_result":
        raise ValueError("Current direction requires result revalidation; keep the original candidate and Turn identity.")
    response = current["response"]
    return {**result, "agent_vision": response["agent_vision"],
        "vision_unchanged_reason": response["vision_unchanged_reason"],
        "checkpoint_read_context_id": response["read_context_id"], "first_delivery": True,
        "_direction_decision": response["decision"]}
