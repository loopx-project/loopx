"""Contract, efficiency, and paired origin/main vs HEAD tests for material_frontier."""

from __future__ import annotations

import ast
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
import re
import subprocess
import sys
import types
from types import MappingProxyType

import pytest

from loopx.control_plane.agents import material_frontier as head_frontier
from loopx.control_plane.agents.material_frontier import PayloadView
from loopx.control_plane.runtime.time import parse_timestamp as real_parse_timestamp

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FRONTIER_PATH = _REPO_ROOT / "loopx" / "control_plane" / "agents" / "material_frontier.py"
_FIXED_TS = "2026-10-12T00:30:00Z"


@lru_cache(maxsize=1)
def _baseline_frontier() -> types.ModuleType:
    completed = subprocess.run(
        ["git", "-C", str(_REPO_ROOT), "show", "origin/main:loopx/control_plane/agents/material_frontier.py"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if completed.returncode != 0:
        pytest.skip(f"origin/main unavailable for paired check: {completed.stderr.strip()}")
    module = types.ModuleType("_baseline_origin_main_material_frontier")
    module.__package__ = "loopx.control_plane.agents"
    module.__file__ = str(_FRONTIER_PATH)
    sys.modules[module.__name__] = module
    exec(
        compile(completed.stdout, "origin/main:loopx/control_plane/agents/material_frontier.py", "exec"),
        module.__dict__,
    )
    return module


def test_module_loc_zero_any_and_exports() -> None:
    source = _FRONTIER_PATH.read_text(encoding="utf-8")
    lines = source.splitlines()
    assert len(lines) <= 550, f"expected <= 550 LOC, found {len(lines)}"
    tree = ast.parse(source)
    any_lines = [
        node.lineno
        for node in ast.walk(tree)
        if (isinstance(node, ast.Name) and node.id == "Any")
        or (isinstance(node, ast.Attribute) and node.attr == "Any")
    ]
    assert any_lines == []
    ignores = [
        idx + 1
        for idx, line in enumerate(lines)
        if re.search(r"#\s*(type:\s*ignore|noqa)", line)
    ]
    assert ignores == []
    assert head_frontier.AGENT_MATERIAL_FRONTIER_SCHEMA_VERSION == "agent_material_frontier_v0"
    assert head_frontier.MATERIAL_USAGE_RECEIPT_SCHEMA_VERSION == "material_usage_receipt_v0"
    assert callable(head_frontier.authority_topic_index)
    assert callable(head_frontier.build_agent_material_frontier)


def test_immutable_mapping_views_and_malformed_inputs() -> None:
    registry = MappingProxyType(
        {
            "project_materials": MappingProxyType(
                {
                    "mat-1": MappingProxyType(
                        {"revision": "rev-1", "boundary": "public_safe", "gate_status": "ready"}
                    )
                }
            ),
            "topic_authority": MappingProxyType(
                {"architecture": MappingProxyType({"id": "mat-1"})}
            ),
        }
    )
    by_topic, by_material = head_frontier.authority_topic_index(registry)
    assert by_topic == {"architecture": ["mat-1"]}
    assert by_material == {"mat-1": ["architecture"]}

    frontier = head_frontier.build_agent_material_frontier(
        goal_id="goal-alpha",
        agent_id="agent-a",
        authority_registry=registry,
        agent_profile=MappingProxyType({"agent_id": "agent-a", "material_topics": "architecture"}),
        receipts=MappingProxyType(
            {
                "schema_version": "material_usage_receipt_v0",
                "goal_id": "goal-alpha",
                "agent_id": "agent-a",
                "material_id": "mat-1",
                "observed_revision": "rev-1",
                "outcome": "verified",
                "receipt_id": "rcpt-1",
                "recorded_at": "2026-10-12T00:15:00Z",
            }
        ),
        generated_at=_FIXED_TS,
    )
    assert frontier["summary"] == {
        "required_count": 1,
        "current_count": 1,
        "stale_count": 0,
        "missing_count": 0,
        "inaccessible_count": 0,
        "required_unread_count": 0,
    }
    assert frontier["required_reads"] == []

    with pytest.raises(ValueError, match="values must be objects"):
        head_frontier.build_agent_material_frontier(
            goal_id="g1",
            agent_id="a1",
            authority_registry={"project_materials": {"bad-first": {"id": "   "}, "bad-val": "oops"}},
        )
    with pytest.raises(ValueError, match="entries must be objects"):
        head_frontier.build_agent_material_frontier(
            goal_id="g1",
            agent_id="a1",
            authority_registry={"project_materials": [{"id": "mat-1"}, 42]},
        )
    with pytest.raises(ValueError, match="ISO timestamp"):
        head_frontier.build_agent_material_frontier(
            goal_id="g1",
            agent_id="a1",
            authority_registry={"project_materials": {}},
            generated_at="not-a-timestamp",
        )


def test_single_pass_receipt_indexing_and_timestamp_parse_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def counting_parse(value: object) -> datetime | None:
        nonlocal calls
        calls += 1
        return real_parse_timestamp(value)

    monkeypatch.setattr(head_frontier, "parse_timestamp", counting_parse)

    materials = {
        f"mat-{idx:03d}": {"id": f"mat-{idx:03d}", "revision": "r2", "boundary": "public_safe"}
        for idx in range(64)
    }
    profile = {"agent_id": "agent-a", "material_refs": list(materials)}
    receipts: list[dict[str, object]] = []
    # Noise receipts for unrelated materials/goals/agents must not parse timestamps per material.
    for idx in range(960):
        receipts.append(
            {
                "schema_version": "material_usage_receipt_v0",
                "goal_id": "goal-1" if idx % 2 == 0 else "other-goal",
                "agent_id": "agent-a",
                "material_id": f"unused-{idx}",
                "observed_revision": "r2",
                "outcome": "read",
                "receipt_id": f"noise-{idx}",
                "recorded_at": "2026-10-12T00:10:00Z",
            }
        )
    for idx in range(64):
        receipts.append(
            {
                "schema_version": "material_usage_receipt_v0",
                "goal_id": "goal-1",
                "agent_id": "agent-a",
                "material_id": f"mat-{idx:03d}",
                "observed_revision": "r2",
                "outcome": "read",
                "receipt_id": f"hit-{idx}",
                "recorded_at": "2026-10-12T00:20:00Z",
            }
        )

    frontier = head_frontier.build_agent_material_frontier(
        goal_id="goal-1",
        agent_id="agent-a",
        authority_registry={"project_materials": materials},
        agent_profile=profile,
        receipts=receipts,
        generated_at=_FIXED_TS,
    )
    assert frontier["summary"] == {
        "required_count": 64,
        "current_count": 64,
        "stale_count": 0,
        "missing_count": 0,
        "inaccessible_count": 0,
        "required_unread_count": 0,
    }
    # 1 call for generated_at + 64 calls for matching receipts (not 64 * 1,024 = 65,536).
    assert calls == 65


@dataclass(frozen=True)
class _EquivalenceCase:
    goal_id: str
    agent_id: str
    authority_registry: PayloadView
    agent_profile: PayloadView | None = None
    vision: PayloadView | None = None
    todos: Sequence[PayloadView] | PayloadView | None = None
    handoffs: Sequence[PayloadView] | PayloadView | None = None
    receipts: Sequence[PayloadView] | PayloadView | None = None
    available_boundaries: tuple[str, ...] = ()
    generated_at: str = _FIXED_TS


def _rich_equivalence_cases() -> list[_EquivalenceCase]:
    authority_map: dict[str, object] = {
        "project_materials": {
            "m-current": {
                "revision": "rev-2",
                "boundary": "public_safe",
                "gate_status": "ready",
                "freshness": "current",
            },
            "m-stale-rev": {
                "id": "m-stale-rev",
                "revision": "rev-3",
                "boundary": "public",
                "gate_status": "approved",
                "freshness": "current",
            },
            "m-stale-fresh": {
                "source_id": "m-stale-fresh",
                "revision": "rev-1",
                "boundary": "public_safe",
                "status": "needs_refresh",
            },
            "m-blocked-gate": {
                "revision": "rev-1",
                "boundary": "public",
                "gate_status": "owner_review_required",
            },
            "m-private-unauth": {
                "revision": "rev-1",
                "boundary": "private_owner",
                "gate_status": "ready",
            },
            "m-private-allowed": {
                "revision": "rev-5",
                "boundary": "team_scoped",
                "gate_status": "ready",
            },
            "m-unavail-receipt": {
                "revision": "rev-1",
                "boundary": "public",
            },
            "m-unread": {
                "revision": "rev-1",
                "boundary": "public_safe",
            },
        },
        "topic_authority": {
            "core": "m-current",
            "ops": [{"material_id": "m-stale-rev"}, {"source_id": "m-current"}],
            "gated": {"id": "m-private-allowed"},
        },
    }
    authority_list: dict[str, object] = {
        "project_materials": [
            {"id": "m-list-1", "revision": "rev-a", "boundary": "public"},
            {"source_id": "m-list-2", "boundary": "public_safe"},
        ],
        "topic_authority": {"single": "m-list-1"},
    }
    return [
        _EquivalenceCase(
            goal_id="goal-eq",
            agent_id="agent-1",
            authority_registry=authority_map,
            agent_profile={
                "agent_id": "agent-1",
                "material_refs": [
                    {"material_id": "m-missing", "relation": "reviewer", "purpose": "Initial review"},
                    "m-unread",
                ],
                "material_topics": ["core"],
                "topic_defaults": "ops",
            },
            vision={
                "vision_id": "vis-1",
                "agent_id": "agent-1",
                "required_material_refs": [
                    {"id": "m-stale-fresh", "required_revision": "rev-1"},
                    "m-blocked-gate",
                ],
                "required_material_topics": "gated",
            },
            todos=[
                {
                    "todo_id": "todo-1",
                    "claimed_by": "agent-1",
                    "required_materials": [
                        {"material_id": "m-current", "relation": "producer", "todo_id": "todo-1"},
                        {"material_id": "m-missing", "relation": "required"},
                        "m-private-unauth",
                        "m-unavail-receipt",
                    ],
                },
                {
                    "todo_id": "todo-whitespace",
                    "claimed_by": "   ",
                    "agent_id": "agent-2",
                    "material_refs": ["m-stale-rev"],
                },
                {
                    "todo_id": "todo-other",
                    "claimed_by": "agent-2",
                    "material_refs": ["m-unread"],
                },
            ],
            handoffs=[
                {
                    "handoff_id": "ho-1",
                    "to_agent": "agent-1",
                    "material_refs": [
                        {"source_id": "m-private-allowed", "purpose": "Handoff check"},
                    ],
                },
                {
                    "handoff_id": "ho-other",
                    "to_agent": "agent-2",
                    "material_refs": ["m-missing"],
                },
            ],
            receipts=[
                # Earlier tie-break and newer timestamps for m-current with todo_id.
                {
                    "schema_version": "material_usage_receipt_v0",
                    "goal_id": "goal-eq",
                    "agent_id": "agent-1",
                    "material_id": "m-current",
                    "todo_id": "todo-1",
                    "observed_revision": "rev-1",
                    "outcome": "read",
                    "receipt_id": "r-a",
                    "recorded_at": "2026-10-12T00:10:00Z",
                },
                {
                    "schema_version": "material_usage_receipt_v0",
                    "goal_id": "goal-eq",
                    "agent_id": "agent-1",
                    "material_id": "m-current",
                    "todo_id": "todo-1",
                    "observed_revision": "rev-2",
                    "outcome": "used",
                    "receipt_id": "r-b",
                    "recorded_at": "2026-10-12T00:20:00+00:00",
                },
                # Identical timestamp/id tie must preserve the first matching receipt.
                {
                    "schema_version": "material_usage_receipt_v0",
                    "goal_id": "goal-eq",
                    "agent_id": "agent-1",
                    "material_id": "m-current",
                    "todo_id": "todo-1",
                    "observed_revision": "rev-wrong-later",
                    "outcome": "read",
                    "receipt_id": "r-b",
                    "recorded_at": "2026-10-12T00:20:00Z",
                },
                {
                    "schema_version": "material_usage_receipt_v0",
                    "goal_id": "goal-eq",
                    "agent_id": "agent-1",
                    "material_id": "m-stale-rev",
                    "observed_revision": "rev-1",
                    "outcome": "read",
                    "receipt_id": "r-stale",
                    "recorded_at": "2026-10-12T00:05:00Z",
                },
                {
                    "schema_version": "material_usage_receipt_v0",
                    "goal_id": "goal-eq",
                    "agent_id": "agent-1",
                    "material_id": "m-stale-fresh",
                    "observed_revision": "rev-1",
                    "outcome": "verified",
                    "receipt_id": "r-fresh",
                    "recorded_at": "2026-10-12T00:06:00Z",
                },
                {
                    "schema_version": "material_usage_receipt_v0",
                    "goal_id": "goal-eq",
                    "agent_id": "agent-1",
                    "material_id": "m-private-allowed",
                    "observed_revision": "rev-5",
                    "outcome": "verified",
                    "receipt_id": "r-priv",
                    "recorded_at": "2026-10-12T00:12:00Z",
                },
                {
                    "schema_version": "material_usage_receipt_v0",
                    "goal_id": "goal-eq",
                    "agent_id": "agent-1",
                    "material_id": "m-unavail-receipt",
                    "observed_revision": "rev-1",
                    "outcome": "unavailable",
                    "receipt_id": "r-unavail",
                    "recorded_at": "2026-10-12T00:12:00Z",
                },
                # Invalid timestamps / unknown outcomes / empty receipt_ids are ignored.
                {
                    "schema_version": "material_usage_receipt_v0",
                    "goal_id": "goal-eq",
                    "agent_id": "agent-1",
                    "material_id": "m-unread",
                    "observed_revision": "rev-1",
                    "outcome": "read",
                    "receipt_id": "r-bad-ts",
                    "recorded_at": "bad-ts",
                },
                {
                    "schema_version": "material_usage_receipt_v0",
                    "goal_id": "goal-eq",
                    "agent_id": "agent-1",
                    "material_id": "m-unread",
                    "observed_revision": "rev-1",
                    "outcome": "read",
                    "receipt_id": "   ",
                    "recorded_at": "2026-10-12T00:12:00Z",
                },
            ],
            available_boundaries=("team_scoped",),
        ),
        _EquivalenceCase(
            goal_id="goal-list",
            agent_id="agent-list",
            authority_registry=authority_list,
            todos={
                "todo_id": "t1",
                "required_material_topics": "single",
                "material_refs": [{"id": "m-list-2", "required_revision": "hint-r1"}],
            },
            receipts=[
                {
                    "schema_version": "material_usage_receipt_v0",
                    "goal_id": "goal-list",
                    "agent_id": "agent-list",
                    "material_id": "m-list-2",
                    "todo_id": "t1",
                    "observed_revision": "hint-r1",
                    "outcome": "read",
                    "receipt_id": "r-hint",
                    "recorded_at": "2026-10-12T00:10:00Z",
                }
            ],
        ),
    ]


def test_paired_origin_main_vs_head_equivalence() -> None:
    base = _baseline_frontier()
    for case in _rich_equivalence_cases():
        assert (
            base.authority_topic_index(case.authority_registry)
            == head_frontier.authority_topic_index(case.authority_registry)
        )
        base_out = base.build_agent_material_frontier(
            goal_id=case.goal_id,
            agent_id=case.agent_id,
            authority_registry=case.authority_registry,
            agent_profile=case.agent_profile,
            todos=case.todos,
            vision=case.vision,
            handoffs=case.handoffs,
            receipts=case.receipts,
            available_boundaries=case.available_boundaries,
            generated_at=case.generated_at,
        )
        head_out = head_frontier.build_agent_material_frontier(
            goal_id=case.goal_id,
            agent_id=case.agent_id,
            authority_registry=case.authority_registry,
            agent_profile=case.agent_profile,
            todos=case.todos,
            vision=case.vision,
            handoffs=case.handoffs,
            receipts=case.receipts,
            available_boundaries=case.available_boundaries,
            generated_at=case.generated_at,
        )
        assert base_out == head_out

    error_calls: list[Callable[[types.ModuleType], object]] = [
        lambda mod: mod.build_agent_material_frontier(
            goal_id="g1", agent_id="a1", authority_registry={}
        ),
        lambda mod: mod.build_agent_material_frontier(
            goal_id="g1", agent_id="a1", authority_registry={"project_materials": "bad"}
        ),
        lambda mod: mod.build_agent_material_frontier(
            goal_id="g1", agent_id="a1", authority_registry={"project_materials": {"x": 1}}
        ),
        lambda mod: mod.build_agent_material_frontier(
            goal_id="g1",
            agent_id="a1",
            authority_registry={"project_materials": [{"id": "m1"}, {"source_id": "m1"}]},
        ),
        lambda mod: mod.build_agent_material_frontier(
            goal_id="g1",
            agent_id="a1",
            authority_registry={"project_materials": {}, "topic_authority": {"t": []}},
        ),
        lambda mod: mod.build_agent_material_frontier(
            goal_id="g1",
            agent_id="a1",
            authority_registry={"project_materials": {}},
            agent_profile={"agent_id": "other"},
        ),
        lambda mod: mod.build_agent_material_frontier(
            goal_id="g1",
            agent_id="a1",
            authority_registry={"project_materials": {}},
            vision={"agent_id": "other"},
        ),
        lambda mod: mod.build_agent_material_frontier(
            goal_id="g1",
            agent_id="a1",
            authority_registry={"project_materials": {}},
            todos=[{"todo_id": "t1", "material_refs": [{"id": "m1", "relation": "bogus"}]}],
        ),
        lambda mod: mod.build_agent_material_frontier(
            goal_id="g1",
            agent_id="a1",
            authority_registry={"project_materials": {}},
            todos=[{"todo_id": "t1", "material_topics": ["unregistered"]}],
        ),
        lambda mod: mod.build_agent_material_frontier(
            goal_id="g1",
            agent_id="a1",
            authority_registry={"project_materials": {}},
            generated_at="not-an-iso",
        ),
        # Compound diagnostic precedence parity cases (PR #9467 PRRT_kwDOStEbEs68NEYt).
        lambda mod: mod.build_agent_material_frontier(
            goal_id="g1",
            agent_id="a1",
            authority_registry={
                "project_materials": {
                    "m1": {"revision": "r" * 200, "boundary": "public"},
                    "m2": {"revision": "r1", "boundary": "public"},
                }
            },
            todos=[{"todo_id": "t1", "material_refs": ["m1", "m2"]}],
            receipts=[
                {
                    "schema_version": "material_usage_receipt_v0",
                    "goal_id": "g1",
                    "agent_id": "a1",
                    "material_id": "m2",
                    "todo_id": "t1",
                    "receipt_id": "rcpt-" + ("x" * 200),
                    "outcome": "read",
                    "recorded_at": "2026-10-12T00:10:00Z",
                }
            ],
        ),
        lambda mod: mod.build_agent_material_frontier(
            goal_id="g1",
            agent_id="a1",
            authority_registry={
                "project_materials": {
                    "m1": {"revision": "r1", "boundary": "public"},
                    "m2": {"revision": "r1", "boundary": "public"},
                }
            },
            todos=[{"todo_id": "t1", "material_refs": ["m1", "m2"]}],
            receipts=[
                {
                    "schema_version": "material_usage_receipt_v0",
                    "goal_id": "g1",
                    "agent_id": "a1",
                    "material_id": "m1",
                    "todo_id": "t1",
                    "receipt_id": "rcpt-1",
                    "observed_revision": "obs-" + ("x" * 200),
                    "outcome": "read",
                    "recorded_at": "2026-10-12T00:10:00Z",
                },
                {
                    "schema_version": "material_usage_receipt_v0",
                    "goal_id": "g1",
                    "agent_id": "a1",
                    "material_id": "m2",
                    "todo_id": "t1",
                    "receipt_id": "rcpt-" + ("y" * 200),
                    "outcome": "read",
                    "recorded_at": "2026-10-12T00:10:00Z",
                },
            ],
        ),
        lambda mod: mod.build_agent_material_frontier(
            goal_id="g1",
            agent_id="a1",
            authority_registry={"project_materials": {}},
            todos=[
                {
                    "todo_id": "t" * 200,
                    "material_refs": [
                        {
                            "id": "m1",
                            "purpose": "p" * 240,
                            "required_revision": "rev-" + ("z" * 200),
                        }
                    ],
                }
            ],
        ),
        lambda mod: mod.build_agent_material_frontier(
            goal_id="g1",
            agent_id="a1",
            authority_registry={"project_materials": {}},
            handoffs=[
                {
                    "handoff_id": "h" * 200,
                    "to_agent": "a1",
                    "material_refs": [
                        {
                            "id": "m1",
                            "todo_id": "t1",
                            "purpose": "p" * 240,
                            "required_revision": "rev-" + ("z" * 200),
                        }
                    ],
                }
            ],
        ),
        lambda mod: mod.build_agent_material_frontier(
            goal_id="g1",
            agent_id="a1",
            authority_registry={"project_materials": {}},
            todos=[
                {
                    "todo_id": "t1",
                    "material_refs": [
                        {"id": "m1", "purpose": "p" * 240, "required_revision": "rev-" + ("z" * 200)}
                    ],
                }
            ],
        ),
    ]
    for call in error_calls:
        with pytest.raises(ValueError) as base_exc:
            call(base)
        with pytest.raises(ValueError) as head_exc:
            call(head_frontier)
        assert str(base_exc.value) == str(head_exc.value)
