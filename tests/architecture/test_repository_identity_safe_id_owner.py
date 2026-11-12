"""Guard one owner for the safe project-id grammar (#4447).

``_SAFE_PROJECT_ID`` in ``repository_identity.py`` is the compiled grammar for a
stable project identifier (``^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$``). The project
registry restated the *same* compiled regex under a different name (``_SAFE_ID``)
to validate its identifiers. ``registry.py`` *already imports*
``normalize_repository_identity`` from ``repository_identity`` -- a stdlib-only
leaf module -- so borrowing the owner's compiled pattern adds no new import edge
and cannot introduce a cycle. The restated regex is retired and the registry's
``_identifier`` gate now references the single owner.

The by-name semantic inventory cannot see this: both bindings are private
``_``-names (the inventory only catalogues ``UPPER_NAME`` carriers) and they differ
by name. This guard keys on the **compiled pattern literal** instead, so a renamed
restatement is caught, while the genuinely different neighbour -- the stricter
``static_site._SAFE_ID`` grammar (no colon, 63 chars) -- is never swept.

Two layers: a whole-tree compiled-pattern census (only the owner compiles it), and
behavioural parity through the registry's own ``_identifier`` gate plus the owner's
``resolve_project_identity`` fallback, so a migration that silently changed which
identifiers are accepted fails here.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from loopx import repository_identity as owner
from loopx.control_plane.projects import registry as registry_mod
from loopx.presentation import static_site as static_site_mod

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "loopx"
OWNER_PATH = "loopx/repository_identity.py"
REGISTRY_PATH = "loopx/control_plane/projects/registry.py"

PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$"
STATIC_SITE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"


def _compiled_pattern_sites(source: str, pattern: str) -> list[int]:
    """Every ``re.compile(<literal>)`` call in the source whose pattern matches.

    Keys on the pattern literal, so a restatement under any name (or with extra
    flags) is still one site; a bare pattern string that is never compiled is not
    a compiled-regex restatement and is left alone.
    """

    tree = ast.parse(source)
    sites: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fname = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if fname != "compile":
            continue
        if (
            node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == pattern
        ):
            sites.append(node.lineno)
    return sites


@pytest.fixture(scope="module")
def tree_offenders() -> dict[str, list[int]]:
    offenders: dict[str, list[int]] = {}
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative == OWNER_PATH:
            continue
        sites = _compiled_pattern_sites(path.read_text(encoding="utf-8"), PATTERN)
        if sites:
            offenders[relative] = sites
    return offenders


def test_only_the_owner_compiles_the_pattern(tree_offenders) -> None:
    assert tree_offenders == {}, (
        f"a restatement of the safe project-id grammar remains: {tree_offenders}"
    )


def test_the_owner_compiles_the_recorded_pattern_once() -> None:
    assert owner._SAFE_PROJECT_ID.pattern == PATTERN
    source = (REPO_ROOT / OWNER_PATH).read_text(encoding="utf-8")
    assert len(_compiled_pattern_sites(source, PATTERN)) == 1


def test_registry_borrows_the_owner_object() -> None:
    assert registry_mod._SAFE_PROJECT_ID is owner._SAFE_PROJECT_ID


def test_registry_retired_its_restated_regex() -> None:
    source = (REPO_ROOT / REGISTRY_PATH).read_text(encoding="utf-8")
    assert _compiled_pattern_sites(source, PATTERN) == []
    assert "_SAFE_PROJECT_ID" in source
    assert "_SAFE_ID" not in source


def test_the_static_site_id_is_a_different_grammar() -> None:
    # The site-id grammar is stricter (no colon, 63 chars); it stays its own
    # owner in static_site.py and is deliberately left untouched.
    assert static_site_mod._SAFE_ID.pattern == STATIC_SITE_PATTERN
    assert STATIC_SITE_PATTERN != PATTERN


# --- behavioural parity through both gates that share the owner -------------------------

CANDIDATES = (
    "abc",
    "A1",
    "a.b",
    "a:b",
    "a-b",
    "a_b",
    "a1.b:c-d_e",
    "1",
    "a" * 256,
    "a" * 257,
    "1" + "a" * 255,
    "",
    ".x",
    "-x",
    ":x",
    "_x",
    "a/b",
    "a b",
    "a@b",
    "  abc  ",
    "héllo",
)


@pytest.mark.parametrize("value", CANDIDATES)
def test_registry_identifier_accepts_exactly_the_owner_grammar(value: str) -> None:
    compact = str(value or "").strip()
    if owner._SAFE_PROJECT_ID.fullmatch(compact) is not None:
        assert registry_mod._identifier(value, field="project_id") == compact
    else:
        with pytest.raises(ValueError):
            registry_mod._identifier(value, field="project_id")


def test_owner_project_id_fallback_shares_the_grammar() -> None:
    # An unusable remote forces the loopx project-id fallback, which the owner
    # validates with the very regex the registry now borrows.
    resolved = owner.resolve_project_identity(
        "/nonexistent-checkout",
        loopx_project_id="valid.id:1",
        remote_url="::invalid::",
    )
    assert resolved == "loopx:valid.id:1"
    with pytest.raises(ValueError):
        owner.resolve_project_identity(
            "/nonexistent-checkout",
            loopx_project_id="bad/id",
            remote_url="::invalid::",
        )


# --- the census bites every restatement form and leaves the others ----------------------

POISON_CORPUS = (
    ("re.compile of the pattern", 'X = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")\n', 1),
    ("bare compile from re", 'from re import compile\nX = compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")\n', 1),
    ("nested in a function", 'def f():\n    return re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")\n', 1),
    ("flags do not hide the restated pattern", 'X = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$", re.IGNORECASE)\n', 1),
    ("the static-site grammar is different", 'X = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")\n', 0),
    ("a shorter max length is different", 'X = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")\n', 0),
    ("a bare pattern string is not a compiled regex", 'X = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$"\n', 0),
    ("importing the owner is not a restatement", "from ...repository_identity import _SAFE_PROJECT_ID\n", 0),
    ("alias rebind is not a restatement", "_SAFE_ID = _SAFE_PROJECT_ID\n", 0),
)


@pytest.mark.parametrize(
    "label,source,expected", POISON_CORPUS, ids=[case[0] for case in POISON_CORPUS]
)
def test_the_census_names_every_form_and_leaves_the_others(
    label: str, source: str, expected: int
) -> None:
    assert len(_compiled_pattern_sites(source, PATTERN)) == expected, label