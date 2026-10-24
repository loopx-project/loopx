"""Guard the single owner of the periodic-report field rules.  Refs #4447 Track A.

`field_shapes` decides three things the package used to decide twice: the identity
rule that gates ``goal_id`` / ``agent_id`` / ``failure_code``, the BCP-47-like
language tag, and the content kinds that are supporting by nature.  Two modules
compiled the language rule, two compiled the identity rule, and two listed the
supporting kinds -- and the two kind-literals differed only in member order, which
a value-keyed duplicate scan reads as two unrelated values.

Two layers matter here because they catch different regressions.  A name-keyed scan,
like the semantic inventory, is blind to a copy that renames the binding, and
`re.compile` constants never enter the inventory budgets at all, so nothing in
`vocabulary_v0.json` would notice a second spelling either.  This guard therefore
compares compiled and set *values*, not names -- and that is how the third identity
site, bound as `_ID`, was found after the two `_IDENTITY_RE` copies were already
migrated.  The entry-function cases then prove each face
really reads the owner instead of keeping an equivalent local rule.

``incremental.py`` is declared, not migrated.  Its ``_IDENTITY_RE`` is a different
rule (case-insensitive, up to 160 characters, versus lowercase-led 3..128), so it is
bounded-context reuse of one name and merging it would change what the incremental
reader accepts.  The declaration pins both halves: it stays distinct, and it stays
the only such site.
"""

from __future__ import annotations

import ast
import pathlib
import re
from types import ModuleType

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE = REPO_ROOT / "loopx" / "capabilities" / "periodic_report"
OWNER_PATH = "loopx/capabilities/periodic_report/field_shapes.py"

CONSUMERS: dict[str, tuple[str, ...]] = {
    "loopx/capabilities/periodic_report/adapters.py": (
        "REPORT_LANGUAGE_PATTERN",
        "SUPPORTING_CONTENT_KINDS",
    ),
    "loopx/capabilities/periodic_report/project_progress.py": (
        "REPORT_LANGUAGE_PATTERN",
        "SUPPORTING_CONTENT_KINDS",
    ),
    "loopx/capabilities/periodic_report/cadence_journal.py": ("REPORT_IDENTITY_PATTERN",),
"loopx/capabilities/periodic_report/pending_intent.py": ("REPORT_IDENTITY_PATTERN",),
    "loopx/capabilities/periodic_report/request_action.py": ("REPORT_IDENTITY_PATTERN",),
}
# Bounded-context reuse of one name: a distinct rule, declared rather than merged.
DECLARED_DISTINCT_SITES: dict[str, int] = {
    "loopx/capabilities/periodic_report/incremental.py": 1,
}
RETIRED_LOCAL_NAMES = ("_IDENTITY_RE", "_LANGUAGE_RE", "_SUPPORTING_CONTENT_KINDS", "_ID")

def _rel(path: pathlib.PurePath) -> str:
    """Normalize a scanned site to the "/" spelling the tables in this file use.

    `str(Path.relative_to(root))` yields backslashes on Windows, which would make
    these ownership assertions reject a correct tree on that platform.  `as_posix()`
    is stable across both spellings, and a Windows-style relative path is pinned
    below so the substitution cannot be dropped again.
    """

    return path.as_posix()


def _package_sources() -> list[pathlib.Path]:
    return sorted(p for p in PACKAGE.glob("*.py") if "__pycache__" not in p.parts)


def _module_assignments(path: pathlib.Path) -> list[tuple[str, int, ast.expr]]:
    found: list[tuple[str, int, ast.expr]] = []
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(
            node.targets[0], ast.Name
        ):
            found.append((node.targets[0].id, node.lineno, node.value))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.value is not None:
                found.append((node.target.id, node.lineno, node.value))
    return found


def _compiled_pattern(node: ast.expr) -> str | None:
    """Fold a compiled pattern to its source text, however the call is spelled."""

    if not isinstance(node, ast.Call) or not node.args:
        return None
    func = node.func
    name = getattr(func, "attr", None) or getattr(func, "id", None)
    if name != "compile":
        return None
    first = node.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return ast.unparse(first)


def _set_members(node: ast.expr) -> tuple[str, ...] | None:
    if isinstance(node, ast.Call) and node.args:
        node = node.args[0]
    if not isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        return None
    return tuple(sorted(ast.unparse(element) for element in node.elts))


def _pattern_definitions(pattern: re.Pattern[str]) -> list[str]:
    """Sites that compile this pattern, at any depth and under any binding name.

    A module-level scan misses both failure modes this decision actually has: a
    copy renamed to something else, and a copy inlined inside a function.
    """

    sites: set[str] = set()
    for path in _package_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _compiled_pattern(node) == pattern.pattern:
                sites.add(_rel(path.relative_to(REPO_ROOT)))
    return sorted(sites)


def _set_definitions(members: frozenset[str]) -> list[str]:
    """Sites listing these members at any depth, in any order."""

    wanted = tuple(sorted(f"'{value}'" for value in members))
    sites: set[str] = set()
    for path in _package_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if _set_members(node) == wanted:
                sites.add(_rel(path.relative_to(REPO_ROOT)))
    return sorted(sites)


def _consumer(rel: str) -> ModuleType:
    stem = pathlib.Path(rel).stem
    return __import__(f"loopx.capabilities.periodic_report.{stem}", fromlist=["_"])


@pytest.fixture(scope="module")
def owner() -> ModuleType:
    from loopx.capabilities.periodic_report import field_shapes

    return field_shapes


def test_scanned_sites_use_one_path_spelling(owner: ModuleType) -> None:
    """A Windows-style relative path must fold to the same site string as POSIX."""

    import ntpath
    import posixpath

    windows_relative = pathlib.PureWindowsPath(ntpath.join("loopx", "x.py"))
    posix_relative = pathlib.PurePosixPath(posixpath.join("loopx", "x.py"))
    assert str(pathlib.PureWindowsPath(ntpath.join("loopx", "x.py"))).count("\\") == 1
    assert _rel(windows_relative) == _rel(posix_relative) == "loopx/x.py"
    sites = _pattern_definitions(owner.REPORT_IDENTITY_PATTERN) + _set_definitions(
        owner.SUPPORTING_CONTENT_KINDS
    )
    assert all("\\" not in site for site in sites), sites
    assert OWNER_PATH in sites


def test_each_pattern_is_compiled_once_in_the_package(owner: ModuleType) -> None:
    for attribute in ("REPORT_IDENTITY_PATTERN", "REPORT_LANGUAGE_PATTERN"):
        pattern: re.Pattern[str] = getattr(owner, attribute)
        sites = _pattern_definitions(pattern)
        assert sites == [OWNER_PATH], (
            f"{attribute} is compiled in {sites}; only {OWNER_PATH} may state it"
        )


def test_supporting_kinds_are_listed_once_including_reordered_copies(
    owner: ModuleType,
) -> None:
    sites = _set_definitions(owner.SUPPORTING_CONTENT_KINDS)
    assert sites == [OWNER_PATH], (
        f"SUPPORTING_CONTENT_KINDS members are listed in {sites}; "
        "a copy that only reorders the members counts as a second owner too"
    )


@pytest.mark.parametrize("rel", sorted(CONSUMERS), ids=sorted(CONSUMERS))
def test_consumers_hold_the_owner_objects_not_copies(rel: str, owner: ModuleType) -> None:
    module = _consumer(rel)
    for attribute in CONSUMERS[rel]:
        assert getattr(module, attribute) is getattr(owner, attribute), (
            f"{pathlib.Path(rel).stem}.{attribute} is a second binding, not the owner's object"
        )


@pytest.mark.parametrize("rel", sorted(CONSUMERS), ids=sorted(CONSUMERS))
def test_each_consumer_uses_the_owner_name_beyond_the_import(rel: str) -> None:
    """An import that is never used would leave the local decision in place."""

    lines = (PACKAGE / pathlib.Path(rel).name).read_text(encoding="utf-8").splitlines()
    for attribute in CONSUMERS[rel]:
        used = [
            line
            for line in lines
            if attribute in line and not line.lstrip().startswith(("from ", "import "))
        ]
        assert used, f"{rel} imports {attribute} but never applies it"


@pytest.mark.parametrize("rel", sorted(CONSUMERS), ids=sorted(CONSUMERS))
def test_retired_local_names_are_not_reintroduced(rel: str) -> None:
    for name, _, _ in _module_assignments(PACKAGE / pathlib.Path(rel).name):
        assert name not in RETIRED_LOCAL_NAMES, (
            f"{rel} defines {name} again; import the owner's name instead"
        )


def test_declared_distinct_site_stays_distinct_and_stays_declared(
    owner: ModuleType,
) -> None:
    from loopx.capabilities.periodic_report import incremental

    assert incremental._IDENTITY_RE.pattern != owner.REPORT_IDENTITY_PATTERN.pattern, (
        "incremental's identity rule now equals the owner's: migrate it and drop the declaration"
    )
    declared = sorted(
        _rel(path.relative_to(REPO_ROOT))
        for path in _package_sources()
        for name, _, _ in _module_assignments(path)
        if name == "_IDENTITY_RE"
    )
    assert declared == sorted(DECLARED_DISTINCT_SITES), (
        f"_IDENTITY_RE is declared in {declared}; the declaration is {sorted(DECLARED_DISTINCT_SITES)}"
    )


def test_language_face_in_project_progress_reads_the_owner(owner: ModuleType) -> None:
    from loopx.capabilities.periodic_report import project_progress

    assert project_progress._language("EN-US") == "en-us"
    for rejected in ("en!", "e", "en-", "language with spaces"):
        assert not owner.REPORT_LANGUAGE_PATTERN.fullmatch(rejected)
        with pytest.raises(ValueError, match="BCP-47-like"):
            project_progress._language(rejected)


def test_language_face_in_adapters_reads_the_owner(owner: ModuleType) -> None:
    from loopx.capabilities.periodic_report import adapters

    assert adapters._normalize_editorial({"language": "zh-Hans"}) == {"language": "zh-Hans"}
    assert adapters._normalize_editorial({"language": "en", "kicker": "k"}) == {
        "kicker": "k",
        "language": "en",
    }
    with pytest.raises(ValueError, match="BCP-47-like"):
        adapters._normalize_editorial({"language": "zh_Hans"})


def test_identity_face_in_cadence_journal_rejects_what_the_owner_rejects(
    tmp_path: pathlib.Path,
    owner: ModuleType,
) -> None:
    from loopx.capabilities.periodic_report import cadence_journal

    # The site this guard found only under a different binding name: ``_ID``.
    assert cadence_journal.cadence_journal_path(tmp_path, "goal-1").is_relative_to(tmp_path)
    with pytest.raises(ValueError, match="Goal identity is invalid"):
        cadence_journal.cadence_journal_path(tmp_path, "Goal-1")
    assert not owner.REPORT_IDENTITY_PATTERN.fullmatch("Goal-1")


def test_supporting_kind_membership_is_enforced_through_the_adapters_face(
    owner: ModuleType,
) -> None:
    from loopx.capabilities.periodic_report import adapters

    for kind in sorted(owner.SUPPORTING_CONTENT_KINDS):
        with pytest.raises(ValueError, match="must be supporting"):
            adapters._normalize_item(
                {
                    "item_id": "item1",
                    "title": "t",
                    "summary": "s",
                    "content_kind": kind,
                    "visibility": "primary",
                },
                label="item",
            )
    accepted = adapters._normalize_item(
        {
            "item_id": "item1",
            "title": "t",
            "summary": "s",
            "content_kind": sorted(owner.SUPPORTING_CONTENT_KINDS)[0],
            "visibility": "supporting",
        },
        label="item",
    )
    assert accepted["visibility"] == "supporting"


def test_identity_face_rejects_what_the_owner_rejects(
    tmp_path: pathlib.Path,
    owner: ModuleType,
) -> None:
    from loopx.capabilities.periodic_report import pending_intent, request_action

    assert pending_intent.pending_periodic_report_intents(
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path,
        goal_id="Uppercase-led-identity",
        agent_id="agent-a",
    ) == []
    with pytest.raises(ValueError, match="identity is invalid"):
        request_action.record_periodic_report_request(
            registry_path=tmp_path / "registry.json",
            runtime_root=tmp_path,
            goal_id="Uppercase-led-identity",
            agent_id="agent-a",
            source_ref="source-ref",
            request_ports=None,
            source_adapter_id=None,
            execute=False,
        )
    assert not owner.REPORT_IDENTITY_PATTERN.fullmatch("Uppercase-led-identity")


def test_incremental_face_keeps_its_own_wider_rule(owner: ModuleType) -> None:
    from loopx.capabilities.periodic_report import incremental

    accepted_by_incremental = "Abc1"
    assert (
        incremental._identity_text(accepted_by_incremental, "surface")
        == accepted_by_incremental
    )
    assert not owner.REPORT_IDENTITY_PATTERN.fullmatch(accepted_by_incremental), (
        "the two rules no longer differ; remove the declaration above and migrate it"
    )
