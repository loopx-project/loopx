"""The goal-id traversal rule is decided in exactly one module.

Four modules used to restate the same three checks, and one normalized a
missing id while the others raised ``AttributeError``. ``loopx/paths.py`` owns
the rule: both public entrypoints already import path helpers from it, and it
imports no project module, so the direction is a leaf rather than a preference.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from loopx import history, paths, runtime
from loopx.control_plane.quota import slot_accounting

REPO_ROOT = Path(__file__).resolve().parents[2]
OWNER_MODULE = "loopx/paths.py"
RULE_ERRORS = frozenset(
    {
        "goal id is required",
        "goal id must be a single path segment",
        "goal id must not include path traversal",
    }
)
# quota.py still restates the rule while #6875 and #5677 rewrite that module.
# Converting it deletes this entry; an entry whose site has already been
# converted fails the census too.
DECLARED_SITES = {"loopx/quota.py": 1}

ENTRYPOINTS = (
    paths.validate_goal_id_path_segment,
    runtime.validate_goal_id_path_segment,
    history.validate_goal_id_path_segment,
    slot_accounting._validate_goal_id_path_segment,
)


def _states_traversal_rule(node: ast.AST) -> bool:
    texts = {
        child.value
        for child in ast.walk(node)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    }
    return RULE_ERRORS <= texts


def _sites(root: Path) -> dict[str, int]:
    found: dict[str, int] = {}
    for path in sorted((root / "loopx").rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        count = sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and _states_traversal_rule(node)
        )
        if count:
            found[relative] = count
    return found


def test_every_entrypoint_holds_the_owners_object() -> None:
    owner = paths.validate_goal_id_path_segment
    for entrypoint in ENTRYPOINTS:
        assert entrypoint is owner


def test_only_the_owner_and_declared_sites_state_the_rule() -> None:
    assert _sites(REPO_ROOT) == {OWNER_MODULE: 1, **DECLARED_SITES}


def test_the_census_sees_a_restated_rule(tmp_path: Path) -> None:
    package = tmp_path / "loopx"
    package.mkdir()
    (package / "restated.py").write_text(
        "def validate_goal_id_path_segment(goal_id: str) -> str:\n"
        "    if not goal_id:\n"
        "        raise ValueError('goal id is required')\n"
        "    if '/' in goal_id:\n"
        "        raise ValueError('goal id must be a single path segment')\n"
        "    if '..' in goal_id:\n"
        "        raise ValueError('goal id must not include path traversal')\n"
        "    return goal_id\n",
        encoding="utf-8",
    )
    (package / "consumer.py").write_text(
        "from loopx.paths import validate_goal_id_path_segment\n",
        encoding="utf-8",
    )
    assert _sites(tmp_path) == {"loopx/restated.py": 1}


def test_declared_sites_cannot_go_stale_silently() -> None:
    still_declared = {
        relative: count
        for relative, count in _sites(REPO_ROOT).items()
        if relative in DECLARED_SITES
    }
    assert still_declared == DECLARED_SITES


@pytest.mark.parametrize(
    "value",
    [".", "..", "a/b", "a\\b", " a/b ", "", "   ", None],
)
def test_every_entrypoint_rejects_the_same_invalid_values(value: object) -> None:
    for entrypoint in ENTRYPOINTS:
        with pytest.raises(ValueError):
            entrypoint(value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", ["loopx-meta", "goal-shared", "Goal_X", "a.b:c"])
def test_every_entrypoint_accepts_a_safe_single_segment(value: str) -> None:
    for entrypoint in ENTRYPOINTS:
        assert entrypoint(value) == value
