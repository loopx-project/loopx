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

Three scan layers, because grouping module-level constants by name is not enough
to call a change complete. The field sets and the two repository/explore patterns
(``TOKEN_PATTERN``, ``_REPO_PATTERN``) keep the original package-wide census:
bindings are matched on the **folded value** (a set's members, a compiled
pattern's body) so a same-name-different-value neighbour is never reported as a
duplicate, and a separate pass looks for any collection literal with the same
members or any string constant embedding the same regex body -- the latter is
what lets ``pr_description``'s three larger grammars be *declared* rather than
merged.

The two reward-memory matchers this change converges (``_OPAQUE_REF_RE``,
``_MEMORY_REF_DIGEST_RE``) are gated by a **scoped, compiled-rule** census
instead, for the reason the review of the first revision gave: a body-only
substring sweep over the whole package flags two things that are not this rule --
an independent ``re.compile(<same body>, re.IGNORECASE)`` importer that accepts
uppercase, and a plain help/display string that merely quotes the body and is
never compiled. So for these two the census (1) walks only
``capabilities/reward_memory`` -- the owner and its consumers, never an unrelated
package; (2) counts only confirmable ``re.compile`` sites -- the callee must be
the ``re`` module (or a tracked alias / ``from re import compile``), so an
independent object's ``.compile()`` that renders help text is not a rule, and the
body is read from the positional arg *or* the ``pattern=`` keyword so a real
``re.compile(pattern=...)`` duplicate is not missed; and (3) compares the
**compiled rule** ``(body, source_flags)``, so a flag-different compile is a
different rule and is left alone. A true restatement -- a reward-memory module
that re-compiles the identical flagless body instead of importing the owner -- is
still caught, and the accept/reject behaviour is pinned through both modules' own
validators below.

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

``RECALL_MODES`` is pinned by identity -- ``experiment`` borrows ``application``'s
set -- rather than by the member census, because ``reward_memory/architecture.py``
lists the same two modes in a fixed order inside a descriptive manifest, which an
unordered member fold would reorder. ``control_plane/status/goal_source.py``
states the opaque-reference body unanchored and inline for a ``todo_id`` in
another package; it sits outside the reward-memory scope of the compiled-rule
census and is a different (unanchored) rule in any case, so it is neither scanned
nor merged -- the same domain decision as ``CORPUS_ID_RE``.

Compiled patterns are real objects, so the ``is`` assertions below carry
information; the two field sets are compared by identity too, which only holds
because both modules now import the same object rather than spelling it out.
"""

from __future__ import annotations

import ast
import pathlib
import re
from types import ModuleType

import pytest

from loopx.capabilities.explore import todo_branch_plan, worker_branch_plan
from loopx.capabilities.issue_fix import pr_description
from loopx.capabilities.issue_fix import repository_commit_evidence as commit_evidence
from loopx.capabilities.issue_fix import repository_snapshot
from loopx.capabilities.issue_fix import reward_memory as issue_fix_reward_memory
from loopx.capabilities.reward_memory import architecture as reward_architecture
from loopx.capabilities.reward_memory import (
    application,
    experiment,
    memory_utility,
    scoped_feedback,
    utility_reducer,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "loopx"
REWARD_MEMORY_ROOT = PACKAGE_ROOT / "capabilities" / "reward_memory"

# These two matchers are reward-memory-internal rules. Their ownership census is
# scoped to REWARD_MEMORY_ROOT and compares compiled (body, flags) rules, not the
# package-wide body-substring sweep the repository/explore patterns below use.
SCOPED_COMPILED_MATCHERS = frozenset({"_OPAQUE_REF_RE", "_MEMORY_REF_DIGEST_RE"})

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
    "_OPAQUE_REF_RE": (
        r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$",
        "loopx/capabilities/reward_memory/memory_utility.py",
        memory_utility,
        [("loopx/capabilities/reward_memory/utility_reducer.py", utility_reducer)],
    ),
    "_MEMORY_REF_DIGEST_RE": (
        r"^(?:[0-9a-f]{16}|sha256:[0-9a-f]{64})$",
        "loopx/capabilities/reward_memory/memory_utility.py",
        memory_utility,
        [("loopx/capabilities/reward_memory/utility_reducer.py", utility_reducer)],
    ),
}

# The repository/explore patterns keep the package-wide embedded-body census; the
# reward-memory matchers are gated by the scoped compiled-rule census instead.
PACKAGE_WIDE_PATTERNS = sorted(
    name for name in PATTERN_DECISIONS if name not in SCOPED_COMPILED_MATCHERS
)

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


def _flag_value(node: ast.expr) -> int | None:
    """Statically evaluate an ``re`` flag expression to an int, or None if unknown."""

    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    if isinstance(node, ast.Attribute):
        value = getattr(re, node.attr, None)
        return value if isinstance(value, int) else None
    if isinstance(node, ast.Name):
        value = getattr(re, node.id, None)
        return value if isinstance(value, int) else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        left, right = _flag_value(node.left), _flag_value(node.right)
        if left is not None and right is not None:
            return left | right
    return None


def _compile_flags(node: ast.Call) -> int | None:
    """Source-level flags of an ``re.compile`` call: 0 when none are written.

    This is the flags argument *as written*, not the runtime ``Pattern.flags``
    (which always carries ``re.UNICODE`` for a str pattern). The owner compiles
    both matchers with no explicit flags, so a duplicate must also write none; an
    unresolvable flag expression returns None and is treated as a different rule.
    """

    flag_node = node.args[1] if len(node.args) >= 2 else None
    for keyword in node.keywords:
        if keyword.arg == "flags":
            flag_node = keyword.value
    if flag_node is None:
        return 0
    return _flag_value(flag_node)


def _re_bindings(tree: ast.AST) -> tuple[frozenset[str], frozenset[str]]:
    """Names confirmably bound to the ``re`` module and to ``re.compile``.

    ``re`` is recognised by its canonical stdlib name even in an isolated call
    fragment with no import line; ``import re as rx`` adds ``rx`` and
    ``from re import compile as c`` adds a direct ``c`` callee. Anything else --
    ``renderer.compile``, a bare builtin ``compile`` -- is not confirmably ``re``.
    """

    module_names = {"re"}
    compile_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "re":
                    module_names.add(alias.asname or "re")
        elif isinstance(node, ast.ImportFrom) and node.module == "re":
            for alias in node.names:
                if alias.name == "compile":
                    compile_names.add(alias.asname or "compile")
    return frozenset(module_names), frozenset(compile_names)


def _is_re_compile(
    node: ast.Call, module_names: frozenset[str], compile_names: frozenset[str]
) -> bool:
    """True only for a confirmable ``re.compile`` callee, never any ``.compile``."""

    func = node.func
    if isinstance(func, ast.Attribute) and func.attr == "compile":
        return isinstance(func.value, ast.Name) and func.value.id in module_names
    if isinstance(func, ast.Name):
        return func.id in compile_names
    return False


def _compile_body(node: ast.Call) -> str | None:
    """The pattern literal from the first positional arg or the ``pattern=`` keyword."""

    if node.args:
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
    for keyword in node.keywords:
        if keyword.arg == "pattern":
            value = keyword.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                return value.value
    return None


def compiled_rule(
    node: ast.expr,
    module_names: frozenset[str] = frozenset({"re"}),
    compile_names: frozenset[str] = frozenset(),
) -> tuple[str, int | None] | None:
    """Fold a confirmable ``re.compile(<literal>[, flags])`` to ``(body, source_flags)``.

    The callee must be ``re.compile`` (or a tracked alias / ``from re import
    compile``), so an independent object's ``.compile()`` that merely renders help
    text is not a rule. The body is read from the first positional arg *or* the
    ``pattern=`` keyword, so a real ``re.compile(pattern=...)`` duplicate is not
    missed. Unlike ``pattern_body`` this keeps the flags, so an ``re.IGNORECASE``
    variant of the same body folds to a different rule and is not a duplicate.
    """

    if isinstance(node, ast.Call) and _is_re_compile(node, module_names, compile_names):
        body = _compile_body(node)
        if body is not None:
            return (body, _compile_flags(node))
    return None


def scoped_matcher_sites(source: str, body: str) -> list[int]:
    """Lines that compile exactly the owner's flagless rule ``(body, 0)``.

    Confirmable ``re.compile`` only (an independent ``.compile()`` or a bare help
    string is never counted), exact-body (a larger grammar that merely embeds the
    body is a different rule), flag-aware, and covering the ``pattern=`` keyword.
    """

    tree = ast.parse(source)
    module_names, compile_names = _re_bindings(tree)
    return [
        node.lineno
        for node in ast.walk(tree)
        if compiled_rule(node, module_names, compile_names) == (body, 0)
    ]


def _reward_memory_matcher_scan() -> dict[str, list[str]]:
    """Census the reward-memory package for recompilations of each scoped rule."""

    findings: dict[str, list[str]] = {}
    for name in sorted(SCOPED_COMPILED_MATCHERS):
        body, owner_path, _owner_module, _consumers = PATTERN_DECISIONS[name]
        offenders: list[str] = []
        for path in sorted(REWARD_MEMORY_ROOT.rglob("*.py")):
            rel = path.relative_to(REPO_ROOT).as_posix()
            if rel == owner_path:
                continue
            offenders.extend(
                f"{rel}:{line} recompiles the {name} rule instead of importing it"
                for line in scoped_matcher_sites(path.read_text(encoding="utf-8"), body)
            )
        findings[name] = offenders
    return findings


def _repository_scan():
    queries = [(name, "members", spec[0], spec[1], {})
               for name, spec in SET_DECISIONS.items()]
    queries += [(name, "body", spec[0], spec[1], DECLARED_EMBEDDED_BODIES.get(spec[0], {}))
                for name, spec in PATTERN_DECISIONS.items()
                if name not in SCOPED_COMPILED_MATCHERS]
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


@pytest.fixture(scope="module")
def reward_memory_matcher_findings():
    return _reward_memory_matcher_scan()


@pytest.mark.parametrize("name", sorted(SET_DECISIONS))
def test_only_the_owner_module_binds_each_field_vocabulary(name: str, repository_findings) -> None:
    bindings, inline = repository_findings(name)
    assert bindings == [], "\n".join(bindings)
    assert inline == [], f"{name} is still spelled out somewhere: " + "\n".join(inline)


@pytest.mark.parametrize("name", PACKAGE_WIDE_PATTERNS)
def test_only_the_owner_module_compiles_each_pattern(name: str, repository_findings) -> None:
    bindings, embedded = repository_findings(name)
    assert bindings == [], "\n".join(bindings)
    assert embedded == [], f"{name} body appears undeclared: " + "\n".join(embedded)


@pytest.mark.parametrize("name", sorted(SCOPED_COMPILED_MATCHERS))
def test_only_the_owner_compiles_each_reward_memory_matcher(
    name: str, reward_memory_matcher_findings
) -> None:
    offenders = reward_memory_matcher_findings[name]
    assert offenders == [], f"{name} is recompiled outside its owner: " + "\n".join(offenders)


@pytest.mark.parametrize("name", sorted(SCOPED_COMPILED_MATCHERS))
def test_the_owner_compiles_each_reward_memory_matcher_exactly_once(name: str) -> None:
    # Without the owner exemption the scoped census would flag the owner itself;
    # this pins that the exemption is load-bearing and there is one definition.
    body, owner_path, owner_module, _consumers = PATTERN_DECISIONS[name]
    assert getattr(owner_module, name).pattern == body
    assert len(scoped_matcher_sites(module_source(owner_path), body)) == 1


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


def test_both_reference_validators_accept_the_same_opaque_refs() -> None:
    """Wiring through each module's own reference reader, not just the imported name."""

    for value in ("ref-1", "todo_2.3:4", "a" * 200):
        assert memory_utility._opaque_ref(value, "ref") == value
        assert utility_reducer._ref(value, "ref") == value
    # The bound and the class are the rule. The path-separator and anchor cases
    # are what separate this stricter shape from public_safe_text's slash-and-#
    # allowing reference owner, so a fold into that owner would fail here.
    for value in ("a" * 201, "has space", "a/b", "a#b", ""):
        for label, validate in (
            ("owner", memory_utility._opaque_ref),
            ("consumer", utility_reducer._ref),
        ):
            with pytest.raises(ValueError):
                validate(value, "ref")


def test_both_digest_validators_accept_the_same_two_alternatives() -> None:
    sixteen = "0123456789abcdef"
    enveloped = "sha256:" + "a" * 64
    for value in ([sixteen], [enveloped], [sixteen, enveloped]):
        assert memory_utility.normalize_reward_memory_ref_digests(value, "d") == value
        assert utility_reducer._memory_digests(value, "d") == value
    # A bare 64-hex is neither alternative; 15/17 hex and a short envelope fail.
    for value in (["a" * 64], ["a" * 15], ["a" * 17], ["sha256:" + "a" * 63]):
        for label, validate in (
            ("owner", memory_utility.normalize_reward_memory_ref_digests),
            ("consumer", utility_reducer._memory_digests),
        ):
            with pytest.raises(ValueError):
                validate(value, "d")


def test_experiment_borrows_the_application_recall_modes() -> None:
    # Pinned by identity, not by the member census: architecture.py lists the
    # same two modes in a fixed order inside a descriptive manifest, and folding
    # that list from an unordered set would reorder it. The validator's set is
    # the owner; the manifest's ordered list is a different question.
    assert experiment.RECALL_MODES is application.RECALL_MODES
    assert not hasattr(experiment, "_RECALL_MODES")
    assert application.RECALL_MODES == {"function_boundary", "bounded_agentic_search"}


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


@pytest.mark.parametrize("name", sorted(SCOPED_COMPILED_MATCHERS))
@pytest.mark.parametrize(
    ("label", "source", "expected"),
    [
        ("renamed flagless restatement", '_X = re.compile(r"__BODY__")\n', 1),
        ("ignorecase is a different rule", '_X = re.compile(r"__BODY__", re.IGNORECASE)\n', 0),
        ("short flag is a different rule", '_X = re.compile(r"__BODY__", re.I)\n', 0),
        ("keyword flag is a different rule", '_X = re.compile(r"__BODY__", flags=re.IGNORECASE)\n', 0),
        ("multiline is a different rule", '_X = re.compile(r"__BODY__", re.MULTILINE)\n', 0),
        ("combined flags are a different rule", '_X = re.compile(r"__BODY__", re.I | re.M)\n', 0),
        ("help text quoting the body is not compiled", 'HELP = "match __BODY__ to validate"\n', 0),
        ("larger grammar embedding the body", '_X = re.compile(r"^prefix__BODY__suffix$")\n', 0),
        ("different body", '_X = re.compile(r"^[a-z]+$")\n', 0),
        ("import from the owner", "from .memory_utility import _OPAQUE_REF_RE, _MEMORY_REF_DIGEST_RE\n", 0),
        # P2: a real re.compile is caught regardless of argument form or alias.
        ("pattern keyword restatement", '_X = re.compile(pattern=r"__BODY__")\n', 1),
        ("pattern keyword with flags=0", '_X = re.compile(pattern=r"__BODY__", flags=0)\n', 1),
        ("positional with flags=0", '_X = re.compile(r"__BODY__", flags=0)\n', 1),
        ("re alias restatement", 'import re as rx\n_X = rx.compile(r"__BODY__")\n', 1),
        ("from re import compile restatement", 'from re import compile\n_X = compile(r"__BODY__")\n', 1),
        ("from re import compile alias", 'from re import compile as c\n_X = c(r"__BODY__")\n', 1),
        # P2: a shared method name on a non-re object is not a regex source.
        ("independent compiler positional", '_X = renderer.compile(r"__BODY__")\n', 0),
        ("independent compiler keyword", '_X = renderer.compile(pattern=r"__BODY__")\n', 0),
        ("method on self is not re", '_X = self.compile(r"__BODY__")\n', 0),
        ("bare builtin compile is not re", '_X = compile(r"__BODY__")\n', 0),
        ("pattern keyword ignorecase is different", '_X = re.compile(pattern=r"__BODY__", flags=re.IGNORECASE)\n', 0),
        ("non-literal body is unknown", '_X = re.compile(some_var)\n', 0),
        ("non-literal pattern keyword is unknown", '_X = re.compile(pattern=some_var)\n', 0),
    ],
    ids=[
        "renamed-restatement",
        "ignorecase-neighbour",
        "short-flag-neighbour",
        "keyword-flag-neighbour",
        "multiline-neighbour",
        "combined-flags-neighbour",
        "help-text-neighbour",
        "larger-grammar-neighbour",
        "different-body",
        "import",
        "pattern-keyword-restatement",
        "pattern-keyword-flags0",
        "positional-flags0",
        "re-alias-restatement",
        "from-re-import-compile",
        "from-re-import-compile-alias",
        "independent-compiler-positional",
        "independent-compiler-keyword",
        "method-on-self",
        "bare-builtin-compile",
        "pattern-keyword-ignorecase",
        "non-literal-body",
        "non-literal-pattern-keyword",
    ],
)
def test_the_scoped_census_bites_a_restatement_and_leaves_the_neighbours(
    name: str, label: str, source: str, expected: int
) -> None:
    # The review counterexamples are the flag-different importer, the never-compiled
    # help string, an independent object's .compile(), and a real re.compile(pattern=)
    # duplicate. The first three must read as neighbours; a flagless recompilation of
    # the identical body -- under a new name, via pattern=, or through an re alias --
    # is caught.
    body = PATTERN_DECISIONS[name][0]
    sites = scoped_matcher_sites(source.replace("__BODY__", body), body)
    assert len(sites) == expected, label


def test_the_scoped_census_never_leaves_the_reward_memory_package(tmp_path, monkeypatch) -> None:
    # A restatement inside reward-memory is caught; the identical text in an
    # unrelated package is out of scope and never scanned. This is the review's
    # core ask: the new ownership constraint must not reach other packages.
    monkeypatch.setitem(globals(), "REPO_ROOT", tmp_path)
    monkeypatch.setitem(
        globals(), "REWARD_MEMORY_ROOT", tmp_path / "loopx/capabilities/reward_memory"
    )
    name = "_OPAQUE_REF_RE"
    body, owner_path, _owner_module, _consumers = PATTERN_DECISIONS[name]
    owner = tmp_path / owner_path
    owner.parent.mkdir(parents=True)
    owner.write_text(f'{name} = re.compile(r"{body}")\n')
    consumer = tmp_path / "loopx/capabilities/reward_memory/utility_reducer.py"
    consumer.write_text(f'{name} = re.compile(r"{body}")\n')  # a true restatement
    outside = tmp_path / "loopx/capabilities/control_plane/goal_source.py"
    outside.parent.mkdir(parents=True)
    outside.write_text(f'{name} = re.compile(r"{body}")\n')  # same text, out of scope
    findings = _reward_memory_matcher_scan()
    assert findings[name] == [
        "loopx/capabilities/reward_memory/utility_reducer.py:1 recompiles the "
        "_OPAQUE_REF_RE rule instead of importing it"
    ]


@pytest.mark.parametrize("name", sorted(SCOPED_COMPILED_MATCHERS))
def test_the_census_keys_on_real_re_compile_not_a_shared_method_name(
    name: str, tmp_path, monkeypatch
) -> None:
    """The review's P2 pair, executed: a real ``re.compile(pattern=...)`` restatement
    is the owner's rule and is caught, while an independent object's ``.compile()``
    of the same text only renders help text and is left alone. Keying on the method
    name alone got this backwards -- it flagged the renderer and missed the keyword
    duplicate -- so the census now confirms the callee is ``re`` and reads ``pattern=``.
    """

    monkeypatch.setitem(globals(), "REPO_ROOT", tmp_path)
    monkeypatch.setitem(
        globals(), "REWARD_MEMORY_ROOT", tmp_path / "loopx/capabilities/reward_memory"
    )
    body, owner_path, _owner_module, _consumers = PATTERN_DECISIONS[name]
    owner = tmp_path / owner_path
    owner.parent.mkdir(parents=True)
    owner.write_text(f'import re\n{name} = re.compile(r"{body}")\n')
    # A real consumer restatement via the pattern= keyword: same rule, caught.
    consumer = tmp_path / "loopx/capabilities/reward_memory/utility_reducer.py"
    consumer.write_text(f'import re\n_X = re.compile(pattern=r"{body}")\n')
    # An independent compiler that only renders help text: not re.compile, left alone.
    renderer = tmp_path / "loopx/capabilities/reward_memory/render.py"
    renderer.write_text(
        "class Renderer:\n"
        "    def compile(self, text):\n"
        '        return f"help: match {text} to validate"\n'
        "RENDERER = Renderer()\n"
        f'_HELP = RENDERER.compile(r"{body}")\n'
    )
    findings = _reward_memory_matcher_scan()
    assert findings[name] == [
        "loopx/capabilities/reward_memory/utility_reducer.py:2 recompiles the "
        f"{name} rule instead of importing it"
    ]

    # Execute both compilers. The keyword compile is the owner's regex -- same body,
    # same flags, same accept/reject behaviour -- while the independent .compile()
    # returns help text with no matching behaviour at all.
    owner_rule = re.compile(body)
    keyword_rule = re.compile(pattern=body)
    assert keyword_rule.pattern == owner_rule.pattern
    assert keyword_rule.flags == owner_rule.flags
    accept, reject = {
        "_OPAQUE_REF_RE": ("ref-1", "a/b"),
        "_MEMORY_REF_DIGEST_RE": ("0123456789abcdef", "a" * 64),
    }[name]
    assert owner_rule.fullmatch(accept) and keyword_rule.fullmatch(accept)
    assert owner_rule.fullmatch(reject) is None and keyword_rule.fullmatch(reject) is None

    class IndependentRenderer:
        def compile(self, text):
            return f"help: match {text} to validate"

    rendered = IndependentRenderer().compile(body)
    assert isinstance(rendered, str)
    assert not hasattr(rendered, "fullmatch")
