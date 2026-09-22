"""Typed boundary between PR inventory selection and review execution."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from .review_contract import build_review_plan, build_review_template
from .readiness_observation import (
    observation_key,
    observation_matches_material_state,
    readiness_material_fingerprint,
    readiness_material_state,
)


EXACT_HEAD_PATTERN = re.compile(
    r"^(?P<number>[1-9][0-9]*)@(?P<head>[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$"
)


def review_action_kind(item: Mapping[str, Any]) -> str | None:
    state = str(item.get("state") or "").upper()
    if item.get("is_draft") is True or state == "CLOSED":
        return None
    conclusion = item.get("review_conclusion")
    conclusion = conclusion if isinstance(conclusion, Mapping) else {}
    if conclusion.get("valid") is True:
        # Merge readiness is owed by the typed verdict, not by GitHub's review
        # state: the platform blocks self-approval, so an author-owned approval
        # is recorded as COMMENTED and would otherwise sit in the concluded
        # lane forever, even after the head stops being mergeable.
        if state == "OPEN" and str(conclusion.get("verdict") or "").upper() == "APPROVE":
            return "qualify_pull_request_merge_readiness"
        return None
    if state == "MERGED":
        return "audit_merged_pull_request_exact_head"
    if state != "OPEN":
        return None
    if str(item.get("review_decision") or "").upper() == "CHANGES_REQUESTED":
        return "rereview_pull_request_exact_head"
    return "review_pull_request_exact_head"


def normalize_fresh_audit_exact_heads(values: Sequence[str]) -> set[str]:
    normalized: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        match = EXACT_HEAD_PATTERN.fullmatch(text)
        if match is None:
            raise ValueError(
                "fresh audit exact head must use NUMBER@HEAD_OID with a full "
                "40- or 64-character hexadecimal head"
            )
        normalized.add(f"{int(match.group('number'))}@{match.group('head').casefold()}")
    return normalized


def exact_head_key(item: Mapping[str, Any]) -> str | None:
    number = item.get("number")
    head_oid = str(item.get("head_oid") or "").strip().casefold()
    if not isinstance(number, int) or number < 1:
        return None
    if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", head_oid) is None:
        return None
    return f"{number}@{head_oid}"


def materialize_review_execution(
    item: Mapping[str, Any],
    *,
    fresh_audit_exact_heads: set[str],
    readiness_observations: Mapping[str, Mapping[str, Any]] | None = None,
    repository: str | None = None,
    review_threads: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    action_kind = review_action_kind(item)
    key = exact_head_key(item)
    readiness_observation: dict[str, Any] | None = None
    if action_kind == "qualify_pull_request_merge_readiness" and key and repository:
        observed = (readiness_observations or {}).get(observation_key(repository, key))
        if observed:
            material_state = readiness_material_state(
                repository=repository,
                item=item,
                review_threads=review_threads or {},
                wait_for_ci=item.get("wait_for_ci") is not False,
            )
            matched = observation_matches_material_state(observed, material_state)
            readiness_observation = {
                "schema_version": "pull_request_merge_readiness_queue_match_v0",
                "observation_state": (
                    "observed_unchanged" if matched else "material_transition"
                ),
                "material_fingerprint": readiness_material_fingerprint(material_state),
                "previous_material_fingerprint": observed.get("material_fingerprint"),
            }
            if matched:
                action_kind = None
    fresh_audit_requested = key in fresh_audit_exact_heads
    conclusion = item.get("review_conclusion")
    conclusion = conclusion if isinstance(conclusion, Mapping) else {}
    if fresh_audit_requested:
        if action_kind is not None:
            raise ValueError(f"fresh audit exact head {key} is already actionable")
        if conclusion.get("valid") is not True:
            raise ValueError(f"fresh audit exact head {key} requires a valid prior conclusion")
        action_kind = "audit_pull_request_exact_head"

    result: dict[str, Any] = {
        "review_action_kind": action_kind,
        "fresh_audit_requested": fresh_audit_requested,
        "merge_readiness_observation": readiness_observation,
    }
    if not action_kind:
        return result | {
            "review_goal": (
                "Read back the existing exact-head conclusion; no full evidence review "
                "is authorized."
            ),
            "evidence_commands": [],
            "review_plan": None,
            "review_template": None,
        }

    number = item.get("number")
    actionable_item = dict(item) | result
    return result | {
        "review_goal": (
            "Run a fresh five-block exact-head audit despite the valid prior conclusion."
            if fresh_audit_requested
            else "Fill the five-block review template after reading the PR body and diff."
        ),
        "evidence_commands": [
            f"gh pr view {number} --json title,body,files,commits,headRefOid,updatedAt" + (",statusCheckRollup" if item.get("wait_for_ci", True) else ""),
            f"gh pr diff {number} --name-only",
            f"gh pr diff {number} --patch",
            f"gh pr view {number} --json headRefOid,updatedAt",
        ],
        "review_plan": build_review_plan(actionable_item),
        "review_template": build_review_template(actionable_item),
    }
