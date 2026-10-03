"""The work-lane contract's wire version is decided in exactly one module.

``work_lane_contract_v1`` is the identity consumers branch on, so a second
spelling - whether as a module-level constant or as an inline literal in a
payload - lets one producer advertise a version the owner never chose. The
generated inventory counts the constant-name form, so the drift smoke already
sees it; the inline form is invisible to a name-keyed scan, which is why the
literal census here is keyed by file and restatement count instead.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from loopx.control_plane.quota import task_orchestration
from loopx.control_plane.work_items import (
    capability_monitor_fallback,
    work_lane,
)
from loopx.semantics.inventory import build_inventory


REPO_ROOT = Path(__file__).resolve().parents[2]
OWNER_MODULE = "loopx/control_plane/work_items/work_lane.py"
CONSTANT_NAME = "WORK_LANE_CONTRACT_SCHEMA_VERSION"
WIRE_VALUE = "work_lane_contract_v1"
# Restatements the census still tolerates, keyed by file and count. These two
# modules are being rewritten by in-flight branches, so converting them here
# would collide; a new restatement, or one of these going quiet without its
# entry being deleted, fails the census.
DEFERRED_INLINE_RESTATEMENTS = {
    "loopx/control_plane/quota/live_decision.py": 1,
    "loopx/control_plane/quota/unsettled_host_turn.py": 1,
}


def _modules(root: Path) -> list[Path]:
    return sorted(path for path in (root / "loopx").rglob("*.py"))


def _constant_bindings(root: Path) -> dict[str, list[int]]:
    """Name every module that binds the constant at module level."""
    offenders: dict[str, list[int]] = {}
    for path in _modules(root):
        source = path.read_text(encoding="utf-8")
        if CONSTANT_NAME not in source:
            continue
        tree = ast.parse(source)
        lines = [
            node.lineno
            for node in tree.body
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            and _binds_name(node, CONSTANT_NAME)
        ]
        if lines:
            offenders[path.relative_to(root).as_posix()] = lines
    return offenders


def _binds_name(node: ast.stmt, name: str) -> bool:
    if isinstance(node, ast.AnnAssign):
        targets: list[ast.expr] = [node.target]
    else:
        targets = list(node.targets)
    return any(isinstance(target, ast.Name) and target.id == name for target in targets)


def _inline_restatements(root: Path) -> dict[str, int]:
    """Count payload literals, excluding the owner's own binding."""
    counts: dict[str, int] = {}
    for path in _modules(root):
        relative = path.relative_to(root).as_posix()
        if relative == OWNER_MODULE:
            continue
        source = path.read_text(encoding="utf-8")
        hits = source.count(f'"{WIRE_VALUE}"') + source.count(f"'{WIRE_VALUE}'")
        if hits:
            counts[relative] = hits
    return counts


def test_owner_module_is_the_only_binding_of_the_version_name() -> None:
    bindings = _constant_bindings(REPO_ROOT)
    assert set(bindings) == {OWNER_MODULE}
    assert len(bindings[OWNER_MODULE]) == 1


def test_owner_value_is_the_wire_value_the_protocol_documents() -> None:
    assert work_lane.WORK_LANE_CONTRACT_SCHEMA_VERSION == WIRE_VALUE


def test_inline_restatement_census_matches_the_declared_deferred_sites() -> None:
    assert _inline_restatements(REPO_ROOT) == DEFERRED_INLINE_RESTATEMENTS


def test_every_consumer_site_projects_the_owner_value() -> None:
    """Each producer reaches its payload through its own entry point."""
    gate = {"action": "skip", "blocked_candidates": []}

    due_contract, _ = (
        capability_monitor_fallback.build_capability_skip_monitor_fallback_contract(
            gate,
            {
                "monitor_open_items": [{"todo_id": "todo-monitor-1"}],
                "monitor_due_count": 1,
                "monitor_due_items": [
                    {"todo_id": "todo-monitor-1", "next_due_at": "past"}
                ],
            },
        )
    )
    gap_contract, _ = (
        capability_monitor_fallback.build_capability_skip_monitor_fallback_contract(
            gate,
            {
                "monitor_open_items": [{"todo_id": "todo-monitor-2"}],
                "monitor_schedule_gap_count": 1,
                "monitor_schedule_gap_items": [{"todo_id": "todo-monitor-2"}],
            },
        )
    )
    quiet_contract, _ = (
        capability_monitor_fallback.build_capability_skip_monitor_fallback_contract(
            gate,
            {"monitor_open_items": [{"todo_id": "todo-monitor-3"}]},
        )
    )
    orchestration_contract = task_orchestration._task_orchestration_work_lane_contract(
        {
            "eligible_peer_lanes": [],
            "coordinator_obligation": "coordinate_task_bundle",
        }
    )
    projected = {
        "monitor_due": due_contract,
        "monitor_schedule_gap": gap_contract,
        "monitor_quiet_wait": quiet_contract,
        "task_orchestration": orchestration_contract,
    }
    assert all(isinstance(payload, dict) for payload in projected.values()), projected
    for label, payload in projected.items():
        assert payload is not None
        assert (
            payload["schema_version"] == work_lane.WORK_LANE_CONTRACT_SCHEMA_VERSION
        ), label
    # The fallback module must not keep a private copy to project with.
    assert CONSTANT_NAME in vars(capability_monitor_fallback)
    assert (
        capability_monitor_fallback.WORK_LANE_CONTRACT_SCHEMA_VERSION
        is work_lane.WORK_LANE_CONTRACT_SCHEMA_VERSION
    )


def test_generated_inventory_no_longer_counts_the_version_as_a_fork() -> None:
    inventory = build_inventory(REPO_ROOT)
    forks = inventory["duplicate_definitions"]["same_runtime_forks"]
    assert CONSTANT_NAME not in {entry["name"] for entry in forks}


@pytest.fixture
def planted_tree(tmp_path: Path) -> Path:
    """A miniature tree holding the owner plus two new spellings."""
    owner_dir = tmp_path / "loopx" / "control_plane" / "work_items"
    owner_dir.mkdir(parents=True)
    (owner_dir / "work_lane.py").write_text(
        f'WORK_LANE_CONTRACT_SCHEMA_VERSION = "{WIRE_VALUE}"\n', encoding="utf-8"
    )
    (tmp_path / "loopx" / "copy.py").write_text(
        f'WORK_LANE_CONTRACT_SCHEMA_VERSION = "{WIRE_VALUE}"\n', encoding="utf-8"
    )
    (tmp_path / "loopx" / "payload.py").write_text(
        f'CONTRACT = {{"schema_version": "{WIRE_VALUE}"}}\n', encoding="utf-8"
    )
    return tmp_path


def test_census_flags_a_planted_second_owner(planted_tree: Path) -> None:
    bindings = _constant_bindings(planted_tree)
    assert set(bindings) == {
        "loopx/control_plane/work_items/work_lane.py",
        "loopx/copy.py",
    }
    # A private copy is caught twice over: as a second binding and as a literal,
    # because the census must still see it if only the binding is deleted.
    assert _inline_restatements(planted_tree) == {
        "loopx/copy.py": 1,
        "loopx/payload.py": 1,
    }


def test_census_is_quiet_when_only_the_owner_spells_the_value(
    planted_tree: Path,
) -> None:
    (planted_tree / "loopx" / "copy.py").unlink()
    (planted_tree / "loopx" / "payload.py").write_text(
        "from .control_plane.work_items.work_lane import (\n"
        "    WORK_LANE_CONTRACT_SCHEMA_VERSION as WORK_LANE_CONTRACT_SCHEMA_VERSION,\n"
        ")\n\n"
        'CONTRACT = {"schema_version": WORK_LANE_CONTRACT_SCHEMA_VERSION}\n',
        encoding="utf-8",
    )
    assert _constant_bindings(planted_tree) == {
        "loopx/control_plane/work_items/work_lane.py": [1]
    }
    assert _inline_restatements(planted_tree) == {}


def test_deferred_entries_cannot_go_stale_silently() -> None:
    """A declared file that no longer restates the value must be removed here."""
    still_restating = {
        relative: count
        for relative, count in DEFERRED_INLINE_RESTATEMENTS.items()
        if f'"{WIRE_VALUE}"' in (REPO_ROOT / relative).read_text(encoding="utf-8")
    }
    assert still_restating == DEFERRED_INLINE_RESTATEMENTS
