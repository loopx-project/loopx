"""`loopx/` keeps the top-level module count it pinned; moves lower it.

Anchored on `docs/architecture/rfcs/monorepo-distribution-split-v0.md` milestone
M0 and Section 9: the import-boundary tests protect inward edges of
`loopx/control_plane`, and nothing at all bounded how many modules sit directly
under `loopx/`. The budget may only be lowered, in the PR that moves files.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
BUDGET_FIXTURE = Path(__file__).with_name("top_level_module_budget.json")
BUDGET_SCHEMA = "loopx_top_level_module_budget_v0"
_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def _top_level_modules(package_root: Path | None = None) -> list[str]:
    """Name the modules that sit directly under ``loopx/``."""
    root = package_root or (REPO_ROOT / "loopx")
    return sorted(path.name for path in root.glob("*.py"))


def _budget() -> dict[str, Any]:
    payload = json.loads(BUDGET_FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(payload, dict), f"{BUDGET_FIXTURE.name} must hold one object"
    assert payload["schema"] == BUDGET_SCHEMA, (
        f"{BUDGET_FIXTURE.name} schema is {payload['schema']!r}, expected {BUDGET_SCHEMA!r}"
    )
    assert _COMMIT_SHA.match(str(payload["baseline_commit"])), (
        "baseline_commit must be the full 40-character commit the count was measured at"
    )
    maximum = payload["max_top_level_modules"]
    assert isinstance(maximum, int) and maximum > 0, (
        f"max_top_level_modules must be a positive integer, got {maximum!r}"
    )
    allowlist = payload["allowlist"]
    assert isinstance(allowlist, list) and all(
        isinstance(name, str) for name in allowlist
    ), f"allowlist must be a list of module file names, got {allowlist!r}"
    assert len(allowlist) == len(set(allowlist)), (
        f"allowlist repeats a name: {allowlist}"
    )
    return payload


def _count_offenders(modules: Sequence[str], maximum: int) -> list[str]:
    """Return the modules that push ``modules`` past ``maximum``, newest path first."""
    if len(modules) <= maximum:
        return []
    return sorted(modules, reverse=True)[: len(modules) - maximum]


def test_top_level_module_count_stays_at_the_pinned_budget() -> None:
    budget = _budget()
    modules = _top_level_modules()
    maximum = int(str(budget["max_top_level_modules"]))
    assert not _count_offenders(modules, maximum), (
        f"loopx/ grew from {maximum} to {len(modules)} top-level modules; the RFC only "
        "allows a move into a subpackage, and new top-level modules need a recorded "
        "design decision first."
    )
    assert len(modules) == maximum, (
        f"loopx/ now holds {len(modules)} top-level modules but the budget pins {maximum}; "
        "a PR that moves files must lower max_top_level_modules by the number moved."
    )


def test_allowlisted_entry_points_stay_at_the_top_level() -> None:
    budget = _budget()
    allowlist = [str(name) for name in budget["allowlist"]]
    modules = set(_top_level_modules())
    missing = [name for name in allowlist if name not in modules]
    assert not missing, (
        f"allowlisted entry points left the top level: {sorted(missing)}"
    )


def test_guard_flags_a_tree_that_grew_past_its_budget(tmp_path: Path) -> None:
    for index in range(3):
        (tmp_path / f"module_{index}.py").write_text("", encoding="utf-8")
    assert _count_offenders(_top_level_modules(tmp_path), 2) == ["module_2.py"]


def test_guard_accepts_a_tree_exactly_at_its_budget(tmp_path: Path) -> None:
    for index in range(3):
        (tmp_path / f"module_{index}.py").write_text("", encoding="utf-8")
    assert _count_offenders(_top_level_modules(tmp_path), 3) == []


def test_guard_accepts_a_tree_under_its_budget(tmp_path: Path) -> None:
    for index in range(3):
        (tmp_path / f"module_{index}.py").write_text("", encoding="utf-8")
    assert _count_offenders(_top_level_modules(tmp_path), 5) == []


def test_guard_ignores_modules_below_the_top_level(tmp_path: Path) -> None:
    (tmp_path / "subpackage").mkdir()
    (tmp_path / "subpackage" / "deep.py").write_text("", encoding="utf-8")
    (tmp_path / "only.py").write_text("", encoding="utf-8")
    assert _top_level_modules(tmp_path) == ["only.py"]
