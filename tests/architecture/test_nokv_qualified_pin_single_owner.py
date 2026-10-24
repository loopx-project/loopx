"""Guard the single owner of the qualified NoKV SDK and API versions.  Refs #4447 Track A.

Which NoKV release LoopX admits as qualified is one decision, and the base
revision stated it twice as literals: ``control_plane/coordination/nokv_jsonl_helper.py``
admits an installed SDK by comparing both names, while
``control_plane/testing/authority_e2e_ladder.py`` restated the same two values to
judge the live-qualification report.  The ladder drives the helper as a *file
path* (``NOKV_HELPER`` / ``NOKV_QUALIFICATION_SCRIPT``) and never imported it, so
a bump landing in one of the two left the other behind with no import to follow.
The one test between them compared values, which is exactly why the drift stayed
invisible: an equality assertion cannot record which module decided.

The TypeScript probe at ``examples/nokv-authority-store/live-qualification.ts``
keeps its own spelling.  It is a second runtime and cannot import a Python
literal, so it is declared here by file and count instead of silently allowed;
the registry carries the same pair under ``dual_runtime_twins``.  The declaration
fails in both directions, so it cannot rot into a list of files that no longer
restate the value.

``tests/**`` is out of census scope on purpose.  ``tests/fixtures/nokv_fake_sdk``
and ``tests/test_nokv_jsonl_helper.py`` stage ``0.11.0`` and an unfenced
``0.11.1`` wheel shape to prove the refusal paths; binding those doubles to the
owner would delete the very values under test.
"""

from __future__ import annotations

import ast
import pathlib
import sys
import types
from types import ModuleType

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
OWNER_MODULE = "loopx/control_plane/coordination/nokv_jsonl_helper.py"
OWNED_NAMES = ("QUALIFIED_NOKV_SDK_VERSION", "QUALIFIED_NOKV_API_VERSION")
DECLARED_CROSS_RUNTIME_SITES: dict[str, int] = {
    "examples/nokv-authority-store/live-qualification.ts": 2,
}

def _rel(path: pathlib.PurePath) -> str:
    """Normalize a scanned site to the "/" spelling the tables in this file use.

    `str(Path.relative_to(root))` yields backslashes on Windows, which would make
    these ownership assertions reject a correct tree on that platform.  `as_posix()`
    is stable across both spellings, and a Windows-style relative path is pinned
    below so the substitution cannot be dropped again.
    """

    return path.as_posix()


def _product_sources() -> list[pathlib.Path]:
    return sorted(
        path
        for path in (REPO_ROOT / "loopx").rglob("*.py")
        if "__pycache__" not in path.parts
    )


def _assigned_names(node: ast.stmt) -> tuple[list[str], str]:
    if isinstance(node, ast.Assign):
        return (
            [target.id for target in node.targets if isinstance(target, ast.Name)],
            ast.unparse(node.value),
        )
    if (
        isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.value
    ):
        return ([node.target.id], ast.unparse(node.value))
    return ([], "")


def _module_level_definitions() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {name: [] for name in OWNED_NAMES}
    for path in _product_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            names, _ = _assigned_names(node)
            for name in names:
                if name in found:
                    found[name].append(_rel(path.relative_to(REPO_ROOT)))
    return found


def _any_depth_definitions() -> list[tuple[str, int, str]]:
    """Catch a restatement hidden in a function body or an inline dict value."""

    hits: list[tuple[str, int, str]] = []
    for path in _product_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names, value = _assigned_names(node)
            rel = _rel(path.relative_to(REPO_ROOT))
            hits.extend(
                (rel, node.lineno, f"{name} = {value}")
                for name in names
                if name in OWNED_NAMES
            )
    return hits


def _sdk_literal_sites(value: object) -> list[str]:
    """Modules that spell the owner's qualified release as a literal.

    Keyed on the value the owner exports, so a reviewed bump in the owner keeps
    this green while an inline copy anywhere else cannot hide.
    """

    sites: set[str] = set()
    for path in _product_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value == value:
                sites.add(_rel(path.relative_to(REPO_ROOT)))
    return sorted(sites)


def _cross_runtime_counts() -> dict[str, int]:
    total: dict[str, int] = {}
    for path in (REPO_ROOT / "examples").rglob("*.ts"):
        found = sum(
            1
            for line in path.read_text(encoding="utf-8").splitlines()
            if any(f"export const {name} = " in line for name in OWNED_NAMES)
        )
        if found:
            total[_rel(path.relative_to(REPO_ROOT))] = found
    return total


@pytest.fixture(scope="module")
def helper() -> ModuleType:
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from loopx.control_plane.coordination import nokv_jsonl_helper

    return nokv_jsonl_helper


@pytest.fixture(scope="module")
def ladder() -> ModuleType:
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from loopx.control_plane.testing import authority_e2e_ladder

    return authority_e2e_ladder


def test_scanned_sites_use_one_path_spelling(helper: ModuleType) -> None:
    """A Windows-style relative path must fold to the same site string as POSIX."""

    import ntpath
    import posixpath

    windows_relative = pathlib.PureWindowsPath(ntpath.join("loopx", "y.py"))
    posix_relative = pathlib.PurePosixPath(posixpath.join("loopx", "y.py"))
    assert str(windows_relative).count("\\") == 1
    assert _rel(windows_relative) == _rel(posix_relative) == "loopx/y.py"
    sites = _module_level_definitions()
    assert all("\\" not in mod for mods in sites.values() for mod in mods), sites
    assert OWNER_MODULE in sites["QUALIFIED_NOKV_SDK_VERSION"]



def test_one_module_defines_each_qualified_version_name() -> None:
    definitions = _module_level_definitions()
    for name in OWNED_NAMES:
        assert definitions[name] == [OWNER_MODULE], (
            f"{name} is declared in {definitions[name]}; only {OWNER_MODULE} may state it"
        )


def test_no_restatement_survives_at_any_depth() -> None:
    every_hit = _any_depth_definitions()
    owner_names = {
        assignment.split(" = ")[0]
        for rel, _, assignment in every_hit
        if rel == OWNER_MODULE
    }
    assert owner_names == set(OWNED_NAMES), (
        f"the scanner never reached the owner's own bindings: {sorted(owner_names)}"
    )
    outside_owner = [hit for hit in every_hit if hit[0] != OWNER_MODULE]
    assert outside_owner == [], (
        f"qualified-version restatements remain: {outside_owner}"
    )


def test_the_qualified_release_literal_is_spelled_in_one_module(helper: ModuleType) -> None:
    value = helper.QUALIFIED_NOKV_SDK_VERSION
    assert isinstance(value, str) and value, f"owner exports {value!r}, not a release string"
    sites = _sdk_literal_sites(value)
    assert sites == [OWNER_MODULE], (
        f"the qualified release {value!r} is spelled in {sites}; only {OWNER_MODULE} may state it"
    )


def test_ladder_readbacks_are_the_owner_objects(
    helper: ModuleType, ladder: ModuleType
) -> None:
    """Identity, not equality: a private copy compares equal and still drifts."""

    for name in OWNED_NAMES:
        assert getattr(ladder, name) is getattr(helper, name), (
            f"{name} reached the ladder as a copy, not as the owner's object"
        )


def test_admission_names_the_owner_value_when_the_sdk_differs(
    monkeypatch: pytest.MonkeyPatch,
    helper: ModuleType,
) -> None:
    """The refusal text is interpolated from the owner, so the product face reads it live."""

    monkeypatch.setitem(
        sys.modules,
        "nokv",
        types.SimpleNamespace(
            __version__="0.11.0",
            API_VERSION=helper.QUALIFIED_NOKV_API_VERSION,
        ),
    )
    with pytest.raises(helper.RequestError) as raised:
        helper.build_client(
            {
                "root_id": "a" * 32,
                "routing": {
                    "kind": "static",
                    "endpoint": "127.0.0.1:7000",
                    "logical_shard_id": "b" * 32,
                    "object_namespace_id": "c" * 32,
                    "placement_generation": 1,
                    "owner_epoch": 1,
                },
                "object_store": {"kind": "memory"},
            }
        )

    assert helper.QUALIFIED_NOKV_SDK_VERSION in str(raised.value)
    assert "0.11.0" not in str(raised.value)


def test_cross_runtime_restatements_stay_declared() -> None:
    counts = _cross_runtime_counts()
    assert counts == DECLARED_CROSS_RUNTIME_SITES, (
        f"cross-runtime spellings are {counts}; this guard and the registry move together"
    )
