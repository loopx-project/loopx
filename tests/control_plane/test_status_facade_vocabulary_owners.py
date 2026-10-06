"""Refs #4447: status consumers stop restating what their projections own.

`loopx/status.py` is the public facade over `loopx.control_plane.status`, and it had
grown its own copy of fifteen module-level carriers that a projection already defined
with an identical value: the three status-contract schema numbers, the monitor display
schema version, the goal-attention override set and legacy evidence prefixes, and the
active-state, autonomous-replan, dead-monitor, backlog-hygiene and agent-lane carriers.

The same follow-up applies to status consumers outside that facade. Each value feeds
the same read-model contract as its owning projection, so consumers import the owner
instead of maintaining an equal-looking declaration.

This test pins three things: the facade declares none of these names itself, every
importing binding is the owner's object rather than an equal-looking re-typed literal,
and the merged values are what the projections shipped with.
"""

from __future__ import annotations

import ast
import functools
import inspect
from pathlib import Path

import pytest

from loopx import diagnose, history, state_projection, state_refresh, status
from loopx.control_plane import progress_scope
from loopx.control_plane.agents import agent_lane_recommendation
from loopx.control_plane.status import (
    active_state_projection,
    attention_projection,
    autonomous_replan_projection,
    contract_projection,
    goal_attention_projection,
    lifecycle_projection,
    monitor_display_projection,
    run_projection,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPOSITORY_ROOT / "loopx"
FACADE = "loopx/status.py"

# name -> (module holding the one remaining definition, other modules importing it).
# Each name has one owner and any public or internal consumers import that object.
FULLY_MERGED: dict[str, tuple[object, tuple[object, ...]]] = {
    "AGENT_LANE_PROGRESS_SCOPE": (
        progress_scope,
        (status, run_projection, agent_lane_recommendation, history, state_refresh),
    ),
    "AUTONOMOUS_REPLAN_PERIODIC_RUN_THRESHOLD": (
        autonomous_replan_projection,
        (status,),
    ),
    "AUTONOMOUS_REPLAN_SCHEMA_VERSION": (autonomous_replan_projection, (status,)),
    "BACKLOG_HYGIENE_BULLET_PATTERN": (active_state_projection, (status,)),
    "BACKLOG_HYGIENE_HINT_PATTERN": (active_state_projection, (status,)),
    "BACKLOG_HYGIENE_SECTION_HEADINGS": (active_state_projection, (status,)),
    "DEAD_MONITOR_REPEAT_SCHEMA_VERSION": (autonomous_replan_projection, (status,)),
    "DEAD_MONITOR_REPEAT_THRESHOLD": (autonomous_replan_projection, (status,)),
    "LEGACY_EXTERNAL_EVIDENCE_CLASSIFICATION_PREFIXES": (
        goal_attention_projection,
        (status,),
    ),
    "MINIMUM_DASHBOARD_STATUS_CONTRACT_SCHEMA_VERSION": (
        contract_projection,
        (status,),
    ),
    "MONITOR_DISPLAY_SCHEMA_VERSION": (monitor_display_projection, (status,)),
    "MONITOR_SIGNAL_WAITING_ON": (
        monitor_display_projection,
        (attention_projection, goal_attention_projection, status),
    ),
    "REGISTRY_WAITING_ON_OVERRIDES": (goal_attention_projection, (status,)),
    "SECTION_HEADING_PATTERN": (
        active_state_projection,
        (state_projection, status),
    ),
    "STATUS_CONTRACT_SCHEMA_VERSION": (contract_projection, (status,)),
    "STATUS_CONTRACT_SIGNAL_LIMIT": (
        contract_projection,
        (diagnose, status),
    ),
}

# Nothing is left forked outside the facade. The last recorded name,
# AGENT_LANE_PROGRESS_SCOPE, was single-sourced into
# ``loopx/control_plane/progress_scope.py``; it is asserted as a fully merged name
# above, so a new copy is caught there rather than by an entry here. Add a name back
# to this table only when a follow-up is genuinely gated on someone else's decision.
STILL_FORKED_ELSEWHERE: dict[str, list[str]] = {}

OWNERSHIPS: dict[str, tuple[object, tuple[object, ...]]] = {
    **FULLY_MERGED,
}

# The values those carriers already had in the projections before the merge.
EXPECTED_VALUES: dict[str, object] = {
    "AGENT_LANE_PROGRESS_SCOPE": "agent_lane",
    "AUTONOMOUS_REPLAN_PERIODIC_RUN_THRESHOLD": 20,
    "AUTONOMOUS_REPLAN_SCHEMA_VERSION": "autonomous_replan_obligation_v0",
    "BACKLOG_HYGIENE_SECTION_HEADINGS": ("Next Action", "Operating Lessons"),
    "DEAD_MONITOR_REPEAT_SCHEMA_VERSION": "dead_monitor_repeat_v0",
    "DEAD_MONITOR_REPEAT_THRESHOLD": 6,
    "LEGACY_EXTERNAL_EVIDENCE_CLASSIFICATION_PREFIXES": (
        "await_",
        "external_evidence_observation_",
    ),
    "MINIMUM_DASHBOARD_STATUS_CONTRACT_SCHEMA_VERSION": 2,
    "MONITOR_DISPLAY_SCHEMA_VERSION": "monitor_quiet_display_v0",
    "MONITOR_SIGNAL_WAITING_ON": "monitor_signal",
    "REGISTRY_WAITING_ON_OVERRIDES": {
        "user_or_controller",
        "controller",
        "codex",
        "external_evidence",
    },
    "STATUS_CONTRACT_SCHEMA_VERSION": 2,
    "STATUS_CONTRACT_SIGNAL_LIMIT": 3,
}
EXPECTED_PATTERNS: dict[str, str] = {
    "BACKLOG_HYGIENE_BULLET_PATTERN": r"^\s*(?:[-*]|\d+[.)])\s+(.+?)\s*$",
    "BACKLOG_HYGIENE_HINT_PATTERN": (
        r"(?i)(?:\[p[0-4]\]|todo|backlog|follow[- ]?up|queue|audit|regression|smoke|"
        r"cadence|mirror|monitor|sub-?agent|待办|回归|审计|修复|检查|推进)"
    ),
    "SECTION_HEADING_PATTERN": r"^##+\s+(.+?)\s*$",
}


def _module_level_names(path: Path) -> set[str]:
    """Collect the names a module assigns at module level."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names.update(
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _index(root: Path) -> dict[str, list[str]]:
    """Map every module-level name under ``root`` to the files that declare it.

    One parse per file: the tables here cover a dozen names, and re-walking the
    package per name would make this the slowest thing in the lane for no signal.
    """
    index: dict[str, list[str]] = {}
    for path in sorted(root.rglob("*.py")):
        try:
            relative = str(path.relative_to(REPOSITORY_ROOT))
        except ValueError:  # a seeded temporary tree is not inside the repository
            relative = str(path.relative_to(root))
        for name in _module_level_names(path):
            index.setdefault(name, []).append(relative)
    return index


@functools.lru_cache(maxsize=1)
def _package_index() -> dict[str, list[str]]:
    return _index(PACKAGE_ROOT)


def _declaring_modules(name: str, root: Path | None = None) -> list[str]:
    """Report every module under ``root`` that declares ``name`` at module level."""
    index = _package_index() if root is None else _index(root)
    return sorted(index.get(name, []))


def _relative_path(module: object) -> str:
    return str(Path(inspect.getfile(module)).relative_to(REPOSITORY_ROOT))


def test_the_facade_declares_none_of_these_names() -> None:
    for name in OWNERSHIPS:
        assert FACADE not in _declaring_modules(name), name


def test_each_fully_merged_name_has_one_declarer_left() -> None:
    for name, (owner, _importers) in FULLY_MERGED.items():
        assert _declaring_modules(name) == [_relative_path(owner)], name


def test_the_remaining_forks_are_exactly_the_recorded_ones() -> None:
    for name, expected in STILL_FORKED_ELSEWHERE.items():
        assert _declaring_modules(name) == sorted(expected), name


def test_the_facade_and_its_projections_share_one_object() -> None:
    """Identity, not equality: a re-typed literal would fork the carrier again."""
    for name, (owner, importers) in OWNERSHIPS.items():
        for importer in (*importers, owner):
            assert getattr(importer, name) is getattr(owner, name), (
                f"{name} via {importer}"
            )


def test_merging_the_copies_changed_no_value() -> None:
    for name, expected in EXPECTED_VALUES.items():
        assert getattr(OWNERSHIPS[name][0], name) == expected, name
    for name, expected in EXPECTED_PATTERNS.items():
        assert getattr(OWNERSHIPS[name][0], name).pattern == expected, name


def test_monitor_project_asset_uses_the_projection_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monitor_signal = "changed_monitor_signal"
    stop_condition = "stop for the changed monitor signal"
    monkeypatch.setattr(
        monitor_display_projection,
        "MONITOR_SIGNAL_WAITING_ON",
        monitor_signal,
    )
    monkeypatch.setattr(
        monitor_display_projection,
        "MONITOR_DISPLAY_STOP_CONDITION",
        stop_condition,
    )

    item = goal_attention_projection.attention_item(
        goal_id="goal-a",
        status="waiting",
        waiting_on=monitor_signal,
        severity="info",
        recommended_action="wait for monitor evidence",
        source="monitor",
        agent_command="loopx monitor poll",
    )

    assert item["project_asset"]["support_mode"] == "read_only_observer"
    assert item["project_asset"]["stop_condition"] == stop_condition


def test_the_lifecycle_priority_pair_is_left_in_place() -> None:
    """Deliberate exclusion, recorded so a later reader sees it was a decision.

    `LIFECYCLE_PRIORITY` is the sixteenth duplicate and the one this change could not
    clean up: no repository caller reaches it through the facade, so re-exporting it
    as an identity alias would add a `_PUBLIC_COMPAT_REEXPORTS` entry the boundary
    tests reject for want of a consumer, while dropping the facade binding would
    remove a public attribute. Its two copies are also the example the convergence RFC
    uses for order-sensitive identity, so retiring the pair needs an owner decision
    rather than a rename inside this PR.
    """
    assert _declaring_modules("LIFECYCLE_PRIORITY") == [
        _relative_path(lifecycle_projection),
        FACADE,
    ]


def test_the_scanner_reports_a_duplicated_name_and_not_an_empty_list(
    tmp_path: Path,
) -> None:
    """A guard that finds nothing in a seeded tree would pass every run by accident."""
    (tmp_path / "owner.py").write_text("CARRIER = 'one'\n", encoding="utf-8")
    (tmp_path / "facade.py").write_text(
        "from .owner import CARRIER\n\nCARRIER = 'one'\n", encoding="utf-8"
    )
    (tmp_path / "quiet.py").write_text("OTHER = 'one'\n", encoding="utf-8")
    assert _declaring_modules("CARRIER", tmp_path) == ["facade.py", "owner.py"]
    assert _declaring_modules("OTHER", tmp_path) == ["quiet.py"]
