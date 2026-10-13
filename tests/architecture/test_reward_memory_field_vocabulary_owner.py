"""Guard one owner per reward-memory and repository field vocabulary.

Five constants decide which keys a structure may carry, and on the base revision
each was written out twice:

=================================  ==================================  ====================
vocabulary                          owner                             also stated in
=================================  ==================================  ====================
``_SOURCE_FIELDS``                  ``reward_memory/scoped_feedback.py:41``  ``issue_fix/reward_memory.py:67``
``_REASONING_FIELDS``               ``reward_memory/scoped_feedback.py:42``  ``issue_fix/reward_memory.py:68``
``_GUARD_FIELDS``                   ``reward_memory/scoped_feedback.py:43``  ``issue_fix/reward_memory.py:69``
``_SCOPE_FIELDS``                   ``reward_memory/memory_utility.py:76``   ``reward_memory/utility_reducer.py:42``
``TOKEN_PATTERN``                   ``explore/todo_branch_plan.py:46``       ``explore/worker_branch_plan.py:279``
``_REPO_PATTERN``                   ``issue_fix/repository_snapshot.py:19``  ``issue_fix/repository_commit_evidence.py:15``
=================================  ==================================  ====================

Owner choice is evidence, not taste.  ``issue_fix/reward_memory.py`` already
imports five names from ``..reward_memory.*``, and ``scoped_feedback.py`` is the
module that declares the event schema those field sets gate.
``utility_reducer.py:19`` and ``worker_branch_plan.py:33`` each *already import
from the module whose constant they restated* -- the strongest form of the
"duplicate knowledge" case in this repository's own #4447 rule.
``repository_snapshot.py`` builds and stamps the ``repo`` field, while
``repository_commit_evidence.py`` only checks a declared one against it.

Two scan layers, because grouping module-level constants by name is not enough
to call a change complete: bindings are matched on the **folded value** (a set's
members, a compiled pattern's body) so a same-name-different-value neighbour is
never reported as a duplicate, and a separate census looks for any collection
literal with the same members or any string constant embedding the same regex
body.

Neighbours this guard deliberately does not merge, each pinned below: the
``field_contracts`` table in ``reward_memory/architecture.py`` describes *which
fields each surface must carry* and lists ``observed_at`` instead of
``actor_role``; ``issue_fix/pr_description.py`` embeds the same owner/repo
character class inside three larger grammars that all require a trailing
``#<number>``; and ``control_plane/todos/contract.py:36`` compiles the identical
body as ``CORPUS_ID_RE`` for todo action kinds, which is why
``CORPUS_ID_RE`` itself is left as two owners here -- no import direction exists
between those two capability packages, so naming one of them the authority over
the other is a domain decision for a maintainer, not a deduplication.

Compiled patterns are real objects, so the ``is`` assertions below carry
information; the two field sets are compared by identity too, which only holds
because both modules now import the same object rather than spelling it out.
"""

from __future__ import annotations

import ast
import pathlib
from types import ModuleType

import pytest

from loopx.capabilities.explore import todo_branch_plan, worker_branch_plan
from loopx.capabilities.issue_fix import pr_description
from loopx.capabilities.issue_fix import repository_commit_evidence as commit_evidence
from loopx.capabilities.issue_fix import repository_snapshot
from loopx.capabilities.issue_fix import reward_memory as issue_fix_reward_memory
from loopx.capabilities.reward_memory import architecture as reward_architecture
from loopx.capabilities.reward_memory import memory_utility, scoped_feedback, utility_reducer

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "loopx"

# name -> (kind, payload, owner path, owner module, [(consumer path, consumer module)])
SET_DECISIONS: dict[str, tuple[frozenset[str], str, ModuleType, list[tuple[str, ModuleType]]]] = {
    "_SOURCE_FIELDS": (
        frozenset({"source_kind", "source_ref", "actor_ref", "actor_role"}),
        "loopx/capabilities/reward_memory/scoped_feedback.py",
        scoped_feedback,
        [("loopx/capabilities/issue_fix/reward_memory.py", issue_fix_reward_memory)],
    ),
    "_REASONING_FIELDS": (
        frozenset({"summary", "confidence"}),
        "loopx/capabilities/reward_memory/scoped_feedback.py",
        scoped_feedback,
        [("loopx/capabilities/issue_fix/reward_memory.py", issue_fix_reward_memory)],
    ),
    "_GUARD_FIELDS": (
        frozenset({"source_freshness", "conflict_state", "current_artifact_verified"}),
        "loopx/capabilities/reward_memory/scoped_feedback.py",
        scoped_feedback,
        [("loopx/capabilities/issue_fix/reward_memory.py", issue_fix_reward_memory)],
    ),
    "_SCOPE_FIELDS": (
        frozenset({"agent_id", "project_id", "corpus_id", "surface_id"}),
        "loopx/capabilities/reward_memory/memory_utility.py",
        memory_utility,
        [("loopx/capabilities/reward_memory/utility_reducer.py", utility_reducer)],
    ),
}

PATTERN_DECISIONS: dict[str, tuple[str, str, ModuleType, list[tuple[str, ModuleType]]]] = {
    "TOKEN_PATTERN": (
        r"[A-Za-z0-9_:\-]{3,}",
        "loopx/capabilities/explore/todo_branch_plan.py",
        todo_branch_plan,
        [("loopx/capabilities/explore/worker_branch_plan.py", worker_branch_plan)],
    ),
    "_REPO_PATTERN": (
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",
        "loopx/capabilities/issue_fix/repository_snapshot.py",
        repository_snapshot,
        [
            (
                "loopx/capabilities/issue_fix/repository_commit_evidence.py",
                commit_evidence,
            )
        ],
    ),
}

# Regex bodies that are legitimately embedded in a larger grammar elsewhere.
# file -> count of string constants carrying the body, with the reason recorded.
DECLARED_EMBEDDED_BODIES: dict[str, dict[str, int]] = {
    r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+": {
        "loopx/capabilities/issue_fix/pr_description.py": 3,
    },
}


def module_source(rel_path: str) -> str:
    return (REPO_ROOT / rel_path).read_text(encoding="utf-8")


def collection_members(node: ast.expr) -> frozenset[str] | None:
    """Fold a set / frozenset / list / tuple of plain string literals to a member set."""

    if isinstance(node, ast.Call):
        fname = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if fname in {"frozenset", "set"} and len(node.args) == 1:
            return collection_members(node.args[0])
        return None
    if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        values = [
            element.value
            for element in node.elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        ]
        if len(values) == len(node.elts):
            return frozenset(values)
    return None


def pattern_body(node: ast.expr) -> str | None:
    """Fold ``re.compile(<literal>)`` to the pattern string it carries."""

    if isinstance(node, ast.Call):
        fname = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if fname == "compile" and node.args:
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                return first.value
    return None


def folded_value(node: ast.expr) -> tuple[str, object] | None:
    members = collection_members(node)
    if members is not None:
        return ("members", members)
    body = pattern_body(node)
    if body is not None:
        return ("body", body)
    return None


def _analyse(source: str):
    """Derive the consumed sites once; syntax trees remain temporary."""

    tree = ast.parse(source)
    bindings = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = frozenset(t.id for t in node.targets if isinstance(t, ast.Name))
            bindings.append((node.lineno, names, folded_value(node.value)))
    members, strings = [], []
    for node in ast.walk(tree):
        value = collection_members(node)
        if value is not None:
            members.append((node.lineno, value))
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            strings.append((node.lineno, node.value))
    return bindings, members, strings


def _binding_lines(bindings, name, kind, payload):
    return [line for line, names, value in bindings
            if name in names and value == (kind, payload)]


def _sites(analysis, name, kind, payload):
    bindings, members, strings = analysis
    named = _binding_lines(bindings, name, kind, payload)
    inline = ([line for line, value in members if value == payload] if kind == "members"
              else [line for line, value in strings if payload in value])
    return named, inline


def binding_sites(source: str, name: str, kind: str, payload: object) -> list[int]:
    """Module-level assignments that bind this name to the owner's folded value."""

    return _binding_lines(_analyse(source)[0], name, kind, payload)


def member_literal_sites(source: str, members: frozenset[str]) -> list[int]:
    return _sites(_analyse(source), "", "members", members)[1]


def body_literal_sites(source: str, body: str) -> list[int]:
    return _sites(_analyse(source), "", "body", body)[1]


def _repository_scan():
    queries = [(name, "members", spec[0], spec[1], {})
               for name, spec in SET_DECISIONS.items()]
    queries += [(name, "body", spec[0], spec[1], DECLARED_EMBEDDED_BODIES.get(spec[0], {}))
                for name, spec in PATTERN_DECISIONS.items()]
    bindings = {name: [] for name, *_ in queries}
    sites = {name: {} for name, *_ in queries}
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        analysis = _analyse(path.read_text(encoding="utf-8"))
        for name, kind, payload, owner_path, _declared in queries:
            if rel == owner_path:
                continue
            named, inline = _sites(analysis, name, kind, payload)
            bindings[name].extend(f"{rel}:{line} rebinds {name} to the same {kind}"
                                  for line in named)
            if inline:
                sites[name][rel] = inline
    result = {}
    for name, kind, _payload, _owner, declared in queries:
        unexplained = [f"{rel}:{line} states the same {kind} inline"
                       for rel, lines in sites[name].items() if rel not in declared
                       for line in lines]
        for rel, count in declared.items():
            seen = len(sites[name].get(rel, []))
            if seen != count:
                unexplained.append(f"{rel} declares {count} embedded site(s), found {seen}")
        result[name] = bindings[name], unexplained
    return result


def _repository_reader():
    # Source is fixed for a module's tests. Retain only a complete findings view;
    # failures leave the reader empty so a repaired source can be retried.
    result = None

    def read(name):
        nonlocal result
        if result is None:
            result = _repository_scan()
        bindings, inline = result[name]
        return list(bindings), list(inline)

    return read


@pytest.fixture(scope="module")
def repository_findings():
    return _repository_reader()


@pytest.mark.parametrize("name", sorted(SET_DECISIONS))
def test_only_the_owner_module_binds_each_field_vocabulary(name: str, repository_findings) -> None:
    bindings, inline = repository_findings(name)
    assert bindings == [], "\n".join(bindings)
    assert inline == [], f"{name} is still spelled out somewhere: " + "\n".join(inline)


@pytest.mark.parametrize("name", sorted(PATTERN_DECISIONS))
def test_only_the_owner_module_compiles_each_pattern(name: str, repository_findings) -> None:
    bindings, embedded = repository_findings(name)
    assert bindings == [], "\n".join(bindings)
    assert embedded == [], f"{name} body appears undeclared: " + "\n".join(embedded)


@pytest.mark.parametrize(("name", "spec"), sorted(SET_DECISIONS.items()))
def test_the_owner_states_the_recorded_members(name: str, spec) -> None:
    members, owner_path, owner_module, _consumers = spec
    assert frozenset(getattr(owner_module, name)) == members
    assert binding_sites(module_source(owner_path), name, "members", members), (
        f"{owner_path} must be where {name} is written out"
    )


@pytest.mark.parametrize(("name", "spec"), sorted(SET_DECISIONS.items()))
def test_each_consumer_reads_the_owner_object(name: str, spec) -> None:
    members, owner_path, owner_module, consumers = spec
    owner_object = getattr(owner_module, name)
    for rel, module in consumers:
        assert rel != owner_path
        assert getattr(module, name) is owner_object, f"{rel} {name}"
        assert binding_sites(module_source(rel), name, "members", members) == [], rel


@pytest.mark.parametrize(("name", "spec"), sorted(PATTERN_DECISIONS.items()))
def test_each_consumer_reads_the_owner_pattern(name: str, spec) -> None:
    body, owner_path, owner_module, consumers = spec
    owner_object = getattr(owner_module, name)
    for rel, module in consumers:
        assert getattr(module, name) is owner_object, f"{rel} {name}"
        assert owner_object.pattern == body, rel
        assert binding_sites(module_source(rel), name, "body", body) == [], rel


def test_both_scope_validators_accept_the_same_four_keys() -> None:
    """Wiring through each module's own entry, not just the imported name."""

    scope = {"agent_id": "a", "project_id": "p", "corpus_id": "c", "surface_id": "s"}
    assert memory_utility._scope(dict(scope), "scope") == scope
    assert sorted(utility_reducer._scope(dict(scope), "scope")) == sorted(scope)
    for label, validate in (
        ("owner", memory_utility._scope),
        ("consumer", utility_reducer._scope),
    ):
        with pytest.raises(ValueError) as extra:
            validate({**scope, "extra": "x"}, "scope")
        assert "missing=[], unknown=['extra']" in str(extra.value), label
        with pytest.raises(ValueError) as missing:
            validate({"agent_id": "a"}, "scope")
        assert "missing=['corpus_id', 'project_id', 'surface_id']" in str(missing.value), label


def test_repo_shape_is_checked_by_the_producer_and_the_verifier() -> None:
    assert repository_snapshot._repo_parts("loopx-project/loopx") == ("loopx-project", "loopx")
    with pytest.raises(ValueError) as refused:
        repository_snapshot._repo_parts("loopx-project-loopx")
    assert str(refused.value) == "repo must use owner/name"
    assert commit_evidence._REPO_PATTERN is repository_snapshot._REPO_PATTERN
    assert not commit_evidence._REPO_PATTERN.fullmatch("loopx-project/loopx/two")


def test_tokenizer_and_worker_share_one_token_rule() -> None:
    """The bound and the character class are the rule; both files must read one copy."""

    tokens = todo_branch_plan._tokenize("sync loopx_core:v2 across agents")
    assert {"sync", "across", "agents", "loopx_core:v2"} == tokens
    assert todo_branch_plan._tokenize("ab cd") == set()  # the {3,} bound, not the class
    assert todo_branch_plan._tokenize("abc de") == {"abc"}
    assert worker_branch_plan.TOKEN_PATTERN is todo_branch_plan.TOKEN_PATTERN
    assert worker_branch_plan.TOKEN_PATTERN.findall("ab") == []
    assert worker_branch_plan.TOKEN_PATTERN.findall("abc") == ["abc"]


def test_architecture_field_contracts_are_a_different_question() -> None:
    """The surface contract table lists ``observed_at``; the envelope allows ``actor_role``."""

    packet = reward_architecture.build_reward_memory_architecture_packet()
    tables = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            contracts = node.get("field_contracts")
            if isinstance(contracts, dict):
                tables.append(contracts)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(packet)
    assert tables, "the architecture packet no longer carries a field_contracts table"
    source_contract = tables[0]["source"]
    assert set(source_contract) == {
        "source_kind",
        "source_ref",
        "actor_ref",
        "observed_at",
    }
    assert set(source_contract) != set(scoped_feedback._SOURCE_FIELDS)
    assert "observed_at" not in scoped_feedback._SOURCE_FIELDS
    assert "actor_role" in scoped_feedback._SOURCE_FIELDS


def test_pr_description_reference_grammars_stay_their_own_decision() -> None:
    """Three sites embed the owner/repo class but each demands a trailing issue number.

    Asserted by matching real strings rather than by reading the pattern source: a
    substring check is satisfied by any *other* alternative in the same grammar, so
    dropping the number requirement from the repository branch would slip through.
    """

    body = PATTERN_DECISIONS["_REPO_PATTERN"][0]
    reference = pr_description._ISSUE_REFERENCE_PATTERN
    for pattern in (
        reference,
        pr_description._CLOSING_LINE_PATTERN,
        pr_description._RELATED_LINE_PATTERN,
    ):
        assert body in pattern.pattern
        assert pattern is not repository_snapshot._REPO_PATTERN
    assert reference.fullmatch("octo/repo#7")
    assert reference.fullmatch("#7")
    assert reference.fullmatch("octo/repo") is None
    assert commit_evidence._REPO_PATTERN.fullmatch("octo/repo")  # the owner's own answer
    assert pr_description._CLOSING_LINE_PATTERN.search("Closes octo/repo#7")
    assert pr_description._CLOSING_LINE_PATTERN.search("Closes octo/repo") is None
    assert pr_description._RELATED_LINE_PATTERN.search("related to octo/repo#7")
    assert pr_description._RELATED_LINE_PATTERN.search("related to octo/repo") is None


@pytest.mark.parametrize(
    ("label", "source", "expect"),
    [
        (
            "member list restated inline",
            'ALLOWED = ["source_kind", "source_ref", "actor_ref", "actor_role"]\n',
            "members",
        ),
        (
            "pattern restated inline",
            '_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")\n',
            "body",
        ),
        (
            "same name, different value",
            '_SCOPE_FIELDS = frozenset({"agent_id", "project_id"})\n',
            "none",
        ),
        (
            "import from the owner",
            "from .memory_utility import _SCOPE_FIELDS\n",
            "none",
        ),
    ],
    ids=["members-inline", "pattern-inline", "different-value", "import"],
)
def test_the_two_scan_layers_see_exactly_what_they_should(label: str, source: str, expect: str) -> None:
    members = SET_DECISIONS["_SOURCE_FIELDS"][0]
    body = PATTERN_DECISIONS["_REPO_PATTERN"][0]
    hits_members = member_literal_sites(source, members)
    hits_body = body_literal_sites(source, body)
    if expect == "members":
        assert hits_members, label
    elif expect == "body":
        assert hits_body, label
    else:
        assert not hits_members and not hits_body, label


@pytest.mark.parametrize("source,owner,named,inline", [
    ('_SOURCE_FIELDS = ["source_kind", "source_ref", "actor_ref", "actor_role"]\n', False, 1, 1),
    ('ALLOWED = ["source_kind", "source_ref", "actor_ref", "actor_role"]\n', False, 0, 1),
    ('_SOURCE_FIELDS = ["other"]\n', False, 0, 0),
    ('_SOURCE_FIELDS = ["source_kind", "source_ref", "actor_ref", "actor_role"]\n', True, 0, 0),
])
def test_repository_findings_preserve_refusals_and_retry(tmp_path, monkeypatch, source, owner, named, inline):
    monkeypatch.setitem(globals(), "REPO_ROOT", tmp_path)
    monkeypatch.setitem(globals(), "PACKAGE_ROOT", tmp_path / "loopx")
    declared = tmp_path / "loopx/capabilities/issue_fix/pr_description.py"
    declared.parent.mkdir(parents=True)
    body = PATTERN_DECISIONS["_REPO_PATTERN"][0]
    declared.write_text("\n".join(f"P{index} = {body!r}" for index in range(3)))
    path = tmp_path / (SET_DECISIONS["_SOURCE_FIELDS"][1] if owner else "loopx/extra.py")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("BROKEN = (\n")
    read = _repository_reader()
    with pytest.raises(SyntaxError):
        read("_SOURCE_FIELDS")
    path.write_text(source)
    bindings, sites = read("_SOURCE_FIELDS")
    assert len(bindings) == named
    assert len(sites) == inline
    bindings.clear()
    sites.clear()
    assert tuple(map(len, read("_SOURCE_FIELDS"))) == (named, inline)
    # Other decisions and the declared embedded sites are part of this same scan.
    assert read("_REPO_PATTERN") == ([], [])
    if owner:
        declared.write_text("ONE = " + repr(body))
        _, rejected = _repository_reader()("_REPO_PATTERN")
        assert rejected == [
            "loopx/capabilities/issue_fix/pr_description.py declares 3 embedded site(s), found 1"
        ]
