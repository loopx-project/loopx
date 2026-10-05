#!/usr/bin/env python3
"""Synthetic case F2 for the goal-direction baseline RFC (GH-C89b).

Implements the Appendix D F2 row of
``docs/architecture/rfcs/goal-direction-baseline-v0.md`` as a checked-in,
public-safe fixture ahead of M1: one authority revision changes after the
selected Agent's receipt, so the projection must report
``re_evaluation_required`` / ``material_revision_changed`` with the changed
item ``stale`` — while nothing mutates: the inputs stay byte-identical and no
Vision patch, Todo, wake, lease, Goal amendment, or path delta is emitted.

The builder here is fixture-local and synthetic on purpose. The RFC's M1
runtime builder is not authorized, so this file pins the F2 contract —
including a mutation arm that proves relaxed revision or Agent matching
fails — without adding runtime code. Ids and revisions are invented; the
projection carries no body, locator, URL, local path, prompt, reasoning,
transcript, trajectory, credential, or run-log field.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

SCHEMA_VERSION = "goal_direction_baseline_v0"

# Section 9 key allowlist: opaque ids, revisions, digests, counts, and typed
# tokens only. Anything outside this vocabulary is a public-boundary defect.
PROJECTION_KEYS = frozenset(
    {"schema_version", "goal_id", "agent_id", "generated_at", "baseline_digest",
     "direction_state", "reason_codes", "summary", "items", "advisory",
     "truth_contract", "effects"})
SUMMARY_KEYS = frozenset(
    {"required_count", "current_count", "stale_count", "blocked_count"})
ITEM_KEYS = frozenset(
    {"material_id", "bound_by", "required_revision", "observed_revision",
     "state", "receipt_ref"})
ADVISORY_KEYS = frozenset(
    {"kind", "creates_work", "rewrites_vision", "changes_goal_route"})
TRUTH_KEYS = frozenset(
    {"authority_is_goal_owned", "projection_is_read_only",
     "receipt_is_agent_scoped", "provider_is_not_write_authority",
     "raw_source_body_recorded"})
FORBIDDEN_KEY_TOKENS = (
    "body", "locator", "url", "path", "prompt", "reasoning", "transcript",
    "trajectory", "credential", "run_log", "run-log")
# `raw_source_body_recorded` is the RFC's own negation flag (Section 5.2); it
# names the absence of source material and carries only `False`.
BOUNDARY_FLAG_EXCEPTIONS = frozenset({"raw_source_body_recorded"})
MUTATION_EFFECT_KINDS = frozenset(
    {"vision_patch", "todo_write", "wake", "lease_write", "goal_amendment",
     "goal_path_delta"})


# --------------------------------------------------------------------------
# Synthetic fixtures: invented goal authority, declared requirements, and
# Agent-scoped receipts. Nothing here represents a real Goal or install.
# --------------------------------------------------------------------------

def invented_authority() -> dict[str, Any]:
    return {
        "goal_id": "goal-invented-1",
        "materials": {
            "material-invented-a": {
                "required_revision": "rev-6",
                "bound_by": ["direction:invented-a"],
            },
            "material-invented-b": {
                "required_revision": "rev-6",
                "bound_by": ["direction:invented-b"],
            },
        },
    }


def invented_receipts() -> list[dict[str, Any]]:
    return [
        {"agent_id": "agent-invented-1", "material_id": "material-invented-a",
         "observed_revision": "rev-6", "receipt_id": "receipt-invented-1"},
        {"agent_id": "agent-invented-1", "material_id": "material-invented-b",
         "observed_revision": "rev-6", "receipt_id": "receipt-invented-2"},
    ]


# --------------------------------------------------------------------------
# Synthetic pure builder over the RFC's Agent-scoped read model (Section 5).
# Item states and drift precedence follow Sections 5.2-5.3; `effects` is the
# only place a write effect could surface and must stay empty.
# --------------------------------------------------------------------------

def build_projection(
    authority: dict[str, Any],
    receipts: list[dict[str, Any]],
    *,
    agent_id: str,
    generated_at: str = "2026-10-04T00:00:00Z",
    require_exact_revision: bool = True,
    agent_scoped_receipts: bool = True,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    reason_codes: list[str] = []
    counts = {"required_count": 0, "current_count": 0, "stale_count": 0,
              "blocked_count": 0}
    for material_id in sorted(authority["materials"]):
        declaration = authority["materials"][material_id]
        current_revision = declaration["required_revision"]
        counts["required_count"] += 1
        candidates = [
            receipt for receipt in receipts
            if receipt["material_id"] == material_id
            and (agent_scoped_receipts is False
                 or receipt["agent_id"] == agent_id)
        ]
        if agent_scoped_receipts:
            receipt = candidates[0] if candidates else None
        else:
            # Mutation mode: optimistically trust the freshest receipt for the
            # material regardless of which Agent earned it.
            receipt = (max(candidates, key=lambda r: r["observed_revision"])
                       if candidates else None)
        if receipt is None:
            state = "required_unread"
            reason = "material_required_unread"
        elif require_exact_revision and receipt["observed_revision"] != current_revision:
            state = "stale"
            reason = "material_revision_changed"
        else:
            state = "current"
            reason = None
        if state == "current":
            counts["current_count"] += 1
        elif state == "stale":
            counts["stale_count"] += 1
        else:
            counts["blocked_count"] += 1
        if reason is not None and reason not in reason_codes:
            reason_codes.append(reason)
        items.append({
            "material_id": material_id,
            "bound_by": list(declaration["bound_by"]),
            "required_revision": current_revision,
            "observed_revision": (None if receipt is None
                                  else receipt["observed_revision"]),
            "state": state,
            "receipt_ref": None if receipt is None else receipt["receipt_id"],
        })

    if counts["blocked_count"]:
        direction_state = "blocked"
    elif "material_revision_changed" in reason_codes:
        direction_state = "re_evaluation_required"
    else:
        direction_state = "current"

    digest_basis = json.dumps(
        {"schema_version": SCHEMA_VERSION,
         "goal_id": authority["goal_id"],
         "requirements": [
             [material_id,
              authority["materials"][material_id]["required_revision"],
              authority["materials"][material_id]["bound_by"]]
             for material_id in sorted(authority["materials"])
         ]},
        sort_keys=True, separators=(",", ":"))
    projection: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "goal_id": authority["goal_id"],
        "agent_id": agent_id,
        "generated_at": generated_at,
        "baseline_digest": "sha256:" + hashlib.sha256(
            digest_basis.encode("utf-8")).hexdigest(),
        "direction_state": direction_state,
        "reason_codes": reason_codes,
        "summary": counts,
        "items": items,
        "advisory": ({
            "kind": "agent_vision_re_evaluation",
            "creates_work": False,
            "rewrites_vision": False,
            "changes_goal_route": False,
        } if direction_state == "re_evaluation_required" else None),
        "truth_contract": {
            "authority_is_goal_owned": True,
            "projection_is_read_only": True,
            "receipt_is_agent_scoped": True,
            "provider_is_not_write_authority": True,
            "raw_source_body_recorded": False,
        },
        "effects": [],
    }
    return projection


# --------------------------------------------------------------------------
# F2 contract assertions (Appendix D, F2 row).
# --------------------------------------------------------------------------

def assert_public_boundary(node: Any, *, where: str) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            lowered = str(key).lower()
            if lowered not in BOUNDARY_FLAG_EXCEPTIONS:
                for token in FORBIDDEN_KEY_TOKENS:
                    assert token not in lowered, f"{where}: forbidden key {key!r}"
            assert_public_boundary(value, where=f"{where}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            assert_public_boundary(value, where=f"{where}[{index}]")
    elif isinstance(node, str):
        assert "://" not in node, f"{where}: URL-like value {node!r}"
        assert not node.startswith(("/", "\\")), f"{where}: path-like value {node!r}"


def assert_f2_contract(
    projection: dict[str, Any],
    authority_before: dict[str, Any],
    receipts_before: list[dict[str, Any]],
    authority_after: dict[str, Any],
    receipts_after: list[dict[str, Any]],
    *,
    drifted_material: str,
    fresh_revision: str,
) -> None:
    assert projection["direction_state"] == "re_evaluation_required", (
        f"expected re_evaluation_required, got {projection['direction_state']!r}")
    assert projection["reason_codes"] == ["material_revision_changed"], (
        f"unexpected reason codes: {projection['reason_codes']!r}")
    drifted = next(item for item in projection["items"]
                   if item["material_id"] == drifted_material)
    assert drifted["state"] == "stale", f"drifted item is {drifted['state']!r}"
    assert drifted["required_revision"] == fresh_revision
    assert drifted["observed_revision"] != fresh_revision
    counts = projection["summary"]
    assert set(counts) == SUMMARY_KEYS
    assert counts == {"required_count": 2, "current_count": 1,
                      "stale_count": 1, "blocked_count": 0}, counts

    effects = projection["effects"]
    assert effects == [], f"mutation effects emitted: {effects!r}"
    assert not (MUTATION_EFFECT_KINDS & set(projection)), (
        "mutation-shaped keys present at the projection top level")

    advisory = projection["advisory"]
    assert advisory is not None, "re_evaluation_required must carry the advisory"
    assert set(advisory) <= ADVISORY_KEYS, f"unknown advisory keys: {set(advisory)}"
    assert advisory["kind"] == "agent_vision_re_evaluation"
    assert advisory["creates_work"] is False
    assert advisory["rewrites_vision"] is False
    assert advisory["changes_goal_route"] is False

    truth = projection["truth_contract"]
    assert set(truth) == TRUTH_KEYS
    assert truth["projection_is_read_only"] is True
    assert truth["provider_is_not_write_authority"] is True
    assert truth["raw_source_body_recorded"] is False

    # Byte-for-byte input immutability (canonical JSON comparison).
    for label, before, after in (
        ("authority", authority_before, authority_after),
        ("receipts", receipts_before, receipts_after),
    ):
        assert (json.dumps(before, sort_keys=True)
                == json.dumps(after, sort_keys=True)), (
            f"{label} inputs were mutated by the builder")

    assert set(projection) <= PROJECTION_KEYS, (
        f"unknown projection keys: {set(projection) - PROJECTION_KEYS}")
    for item in projection["items"]:
        assert set(item) <= ITEM_KEYS, f"unknown item keys: {set(item) - ITEM_KEYS}"
    assert_public_boundary(projection, where="projection")


def expect_f2_failure(projection: dict[str, Any], *, label: str) -> None:
    """The mutation arm: the relaxed builder must FAIL the F2 contract."""
    try:
        assert_f2_contract(projection, invented_authority(), [],
                           invented_authority(), [],
                           drifted_material="material-invented-b",
                           fresh_revision="rev-7")
    except AssertionError:
        return
    raise AssertionError(
        f"mutation {label!r} passed the F2 contract; the fixture cannot "
        "detect relaxed revision or Agent matching")


def main() -> int:
    agent_id = "agent-invented-1"
    drifted_material = "material-invented-b"
    fresh_revision = "rev-7"

    # Precondition: before the drift the baseline is `current`.
    authority = invented_authority()
    receipts = invented_receipts()
    baseline = build_projection(authority, receipts, agent_id=agent_id)
    assert baseline["direction_state"] == "current", baseline["direction_state"]
    assert baseline["reason_codes"] == [], baseline["reason_codes"]

    # F2: one authority revision changes AFTER the Agent's receipt.
    authority["materials"][drifted_material]["required_revision"] = fresh_revision
    authority_before = copy.deepcopy(authority)
    receipts_before = copy.deepcopy(receipts)
    projection = build_projection(authority, receipts, agent_id=agent_id)
    assert_f2_contract(projection, authority_before, receipts_before,
                       authority, receipts,
                       drifted_material=drifted_material,
                       fresh_revision=fresh_revision)
    print("F2: revision drift -> re_evaluation_required / "
          "material_revision_changed, inputs byte-identical, zero effects")

    # The digest tracks goal-owned authority, not receipts or the Agent.
    assert projection["baseline_digest"] != baseline["baseline_digest"], (
        "an authority revision change must move the baseline digest")
    peer_view = build_projection(authority, receipts, agent_id="agent-invented-2")
    assert peer_view["baseline_digest"] == projection["baseline_digest"], (
        "two Agents on the same authority must share the baseline digest")

    # Mutation arm (Appendix D closing note): relaxed matching must fail F2.
    relaxed_revision = build_projection(
        authority, receipts, agent_id=agent_id, require_exact_revision=False)
    expect_f2_failure(relaxed_revision, label="relaxed revision matching")

    peer_receipts = receipts + [
        {"agent_id": "agent-invented-2", "material_id": drifted_material,
         "observed_revision": fresh_revision,
         "receipt_id": "receipt-invented-3"}]
    relaxed_agent = build_projection(
        authority, peer_receipts, agent_id=agent_id,
        agent_scoped_receipts=False)
    expect_f2_failure(relaxed_agent, label="relaxed Agent scoping")
    print("mutation arm: relaxed revision and cross-Agent receipt matching "
          "both fail the F2 contract")

    print("OK goal-direction-baseline-f2-smoke")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
