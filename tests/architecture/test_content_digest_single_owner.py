"""One owner decides what a stored SHA-256 looks like, and this keeps it that way.

Four layers, and none of them substitutes for another:

1. a **stated-shape scan**: no module outside the owner may state either whole-value
   shape, judged by the value a piece of source denotes;
2. an **ownership manifest**: the modules that delegate the decision are pinned, they must
   name a canonical export, and every name they import has to be used;
3. a **readability rule**: a module that asks the owner may not also hand `re` a pattern
   this file cannot read, because that is where a shape could hide;
4. **behavioural cases** that enter through each surface's own reader, the only layer that
   can notice a surface wired to the wrong envelope.

Layer 1 judges values rather than spellings because the first two rounds of this pull
request, and of its TypeScript twin, each found a spelling a spelling rule had to miss: an
unanchored `re.fullmatch` argument, an anchored literal, a constant built with `+`, a name
bound elsewhere in the same file, a `from re import fullmatch` import and a pattern parked
in a tuple all state the same decision. A rule of the form "a literal that looks like
`^...$`" only ever closes the spellings someone has already thought of.

Two residues are named instead of claimed. A shape that reaches a matcher from data this
file cannot read (a configuration value, a provider payload) in a module that never imports
the owner stays invisible to static scanning, and a whole-value decision written without
any pattern at all (`len(text) == 64 and text in HEXDIGITS`) is not something a pattern
scan can see. Both are recorded as follow-up work here, not reported as forbidden.

Only whole-value shapes are owned. A hex digest inside a larger grammar (a `cadence_...`
identifier, a journal filename, a `40|64` Git object id, a compound cursor) answers that
grammar's question and stays with the surface that owns it, and producers that concatenate
`"sha256:"` by hand are the other half of the decision and are deliberately unchanged.
"""

from __future__ import annotations

import ast
import importlib
import json
import re
from pathlib import Path
from typing import Any

import pytest

from loopx.control_plane import content_digest
from loopx.control_plane.content_digest import (
    BARE_SHA256_PATTERN,
    ENVELOPED_SHA256_PATTERN,
)

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "loopx"
OWNER_MODULE = "loopx/control_plane/content_digest.py"
OWNER_DOTTED = "loopx.control_plane.content_digest"
CANONICAL_EXPORTS = ("BARE_SHA256_PATTERN", "ENVELOPED_SHA256_PATTERN")

# The two spellings of the same ten digits and six letters. Order inside a character class
# carries no meaning, so both denote one decision and neither is a second owner.
HEX64_CLASSES = ("[0-9a-f]", "[a-f0-9]")
ENVELOPE = "sha256:"
LEADING = ("^", r"\A")
TRAILING = ("$", r"\Z", r"\z")

HEX64 = "a" * 64
MIXED_HEX64 = "0123456789abcdef" * 4
ENVELOPED = f"{ENVELOPE}{HEX64}"
TODO_ID = f"todo_{'a' * 12}"

ENVELOPED_ACCEPTS = (ENVELOPED, f"{ENVELOPE}{MIXED_HEX64}")
ENVELOPED_REJECTS = (
    HEX64,  # the envelope is part of the stored shape
    f"{ENVELOPE}{HEX64[:-1]}",
    f"{ENVELOPE}{HEX64}0",
    f"{ENVELOPE}{HEX64.upper()}",
    f"{ENVELOPE}{'z' * 64}",
    f"prefix-{ENVELOPE}{HEX64}",
    f"{ENVELOPE}{HEX64} trailing",
    f"{ENVELOPE}{HEX64}\n",
    "",
)
BARE_ACCEPTS = (HEX64, MIXED_HEX64, "f" * 64)
BARE_REJECTS = (
    ENVELOPED,
    HEX64[:-1],
    f"{HEX64}0",
    HEX64.upper(),
    "z" * 64,
    f"x{HEX64}",
    f"{HEX64} ",
    f"{HEX64}\n",
    "",
    f"{ENVELOPE}{HEX64[:-1]}",
)

# Probes that stress the one difference between the spellings this branch collapses: `$`
# also matches in front of a trailing newline while `re.fullmatch` demands the end of the
# string, so a value ending in "\n" is the case that could have divided them.
PARITY_PROBES = (
    HEX64,
    MIXED_HEX64,
    "f" * 64,
    HEX64.upper(),
    HEX64[:-1],
    f"{HEX64}0",
    f"{HEX64}\n",
    f"{HEX64}\n\n",
    f"\n{HEX64}",
    f"{HEX64} ",
    f" {HEX64}",
    "",
    "z" * 64,
    ENVELOPED,
    f"{ENVELOPE}{MIXED_HEX64}",
    f"{ENVELOPE}{HEX64.upper()}",
    f"{ENVELOPE}{HEX64}\n",
    f"{ENVELOPE}{HEX64[:-1]}",
    f"{ENVELOPE}{HEX64}0",
    ENVELOPED.upper(),
    HEX64 * 2,
)

# Each shape that used to be stated at a call site, to be invoked the way that site invoked
# it. Every pair has to agree with the owner on every probe above, which is the evidence
# that reading one decision out of one module is not a behaviour change.
RETIRED_SPELLINGS = (
    ("bare, unanchored, through re.fullmatch", "[a-f0-9]{64}", BARE_SHA256_PATTERN),
    ("bare, anchored, compiled then .fullmatch", "^[0-9a-f]{64}$", BARE_SHA256_PATTERN),
    (r"bare, closed with \Z, compiled", "[0-9a-f]{64}\\Z", BARE_SHA256_PATTERN),
    (
        "enveloped, unanchored, through re.fullmatch",
        "sha256:[0-9a-f]{64}",
        ENVELOPED_SHA256_PATTERN,
    ),
    (
        "enveloped, anchored, compiled",
        "^sha256:[0-9a-f]{64}$",
        ENVELOPED_SHA256_PATTERN,
    ),
    (
        "enveloped, [a-f0-9] class order",
        "sha256:[a-f0-9]{64}",
        ENVELOPED_SHA256_PATTERN,
    ),
)

# Recorded instead of absorbed, each with a reason that stands on the field contract rather
# than on which other pull request happens to be open. An entry that stops being true fails
# its own test below instead of ageing quietly.
DEFERRED_WHOLE_VALUE_SITES: dict[str, str] = {
    "loopx/capabilities/manager_context/inspection.py": (
        "published as a JSON-schema `pattern` string, so it is schema data handed to a "
        "validator rather than a matcher this owner may replace; the schema would have to "
        "move for a verdict-for-verdict substitution to be safe"
    ),
}

# Pinned by review rather than derived: a module that starts asking the owner has to be
# added here, which turns widening the fan-out into a visible event instead of a quiet one.
CONSUMER_MODULES = (
    "loopx.capabilities.benchmark_toolkit.behavior_finding",
    "loopx.capabilities.benchmark_toolkit.continuation",
    "loopx.capabilities.benchmark_toolkit.factorial_contrast",
    "loopx.capabilities.benchmark_toolkit.runtime_continuity",
    "loopx.capabilities.benchmark_toolkit.study_projection",
    "loopx.capabilities.content_ops.item_lifecycle",
    "loopx.capabilities.deep_research.runtime",
    "loopx.capabilities.issue_fix.outcome_projection",
    "loopx.capabilities.issue_fix.reviewer_notification",
    "loopx.capabilities.machine_configuration.store",
    "loopx.capabilities.manager_context.roundtrip",
    "loopx.capabilities.manager_context.tracking",
    "loopx.capabilities.periodic_report.adapters",
    "loopx.capabilities.periodic_report.archive",
    "loopx.capabilities.periodic_report.bindings",
    "loopx.capabilities.periodic_report.cadence_journal",
    "loopx.capabilities.periodic_report.incremental",
    "loopx.capabilities.periodic_report.machine_defaults",
    "loopx.capabilities.progress_review.receipt",
    "loopx.chat_action_normalization",
    "loopx.configuration_transaction",
    "loopx.control_plane.agents.execution_facts",
    "loopx.control_plane.collaboration.delegation_inventory",
    "loopx.control_plane.collaboration.inbox",
    "loopx.control_plane.collaboration.links",
    "loopx.control_plane.collaboration.peers",
    "loopx.control_plane.coordination.local_authority_shadow_outbox",
    "loopx.control_plane.coordination.shadow_management",
    "loopx.control_plane.digest_envelope",
    "loopx.control_plane.effect_runtime",
    "loopx.control_plane.goals.activation_service",
    "loopx.control_plane.goals.deletion_service",
    "loopx.control_plane.goals.goal_amendment_proposal",
    "loopx.control_plane.projects.registry_codec",
    "loopx.control_plane.testing.cli_output_semantics",
    "loopx.control_plane.testing.release_commit_qualification",
    "loopx.control_plane.todos.completion_result",
    "loopx.control_plane.todos.completion_transaction",
    "loopx.control_plane.todos.completion_validation",
    "loopx.control_plane.todos.completion_validation_store",
    "loopx.control_plane.todos.machine_section_projection",
    "loopx.control_plane.work_items.governed_transition_proposal",
    "loopx.control_plane.work_items.progress_review_policy",
    "loopx.control_plane.work_items.task_lease",
    "loopx.domain_packs.issue_fix",
    "loopx.extensions.lark.document_comment_provider",
    "loopx.extensions.openviking_semantic_preference.history_export",
    "loopx.extensions.presentation",
    "loopx.presentation.chat_bundle",
)

# A consumer that hands `re` a pattern this file cannot fold. Pinned empty: folding reads
# every construction in every consumer today, so adding an entry has to be argued.
UNREADABLE_CONSUMER_CONSTRUCTIONS: dict[str, str] = {}

# Reading `.pattern` off the owner and recompiling it states the decision a second time
# while showing the scan no shape at all. Pinned empty; widening it is a review event.
OWNER_TEXT_READS: dict[str, str] = {}

RE_CONSTRUCTIONS = frozenset(
    {
        "compile",
        "fullmatch",
        "match",
        "search",
        "sub",
        "subn",
        "split",
        "findall",
        "finditer",
    }
)
NOT_A_PATTERN_ARGUMENT = frozenset({"escape"})
_SCOPE_ATTRIBUTE = "_digest_owner_scope"
MAX_FOLD_DEPTH = 12


class _Binding:
    """One name in one lexical scope, and whether its value can be read."""

    __slots__ = ("value", "scope", "writes")

    def __init__(self) -> None:
        self.value: ast.expr | None = None
        self.scope: _Scope | None = None
        self.writes = 0

    @property
    def foldable(self) -> bool:
        # A name written twice, or bound by a parameter, an import, an unpacking target, a
        # loop target, a `with` target, an exception name or a `global`/`nonlocal`
        # statement, does not have one value to read. The answer then is "no value", which
        # sends the site to the declaration layer instead of inventing a fold that could
        # hide one owner behind another owner's wrong value.
        return self.writes == 1 and self.value is not None and self.scope is not None


class _Scope:
    __slots__ = ("parent", "names")

    def __init__(self, parent: _Scope | None) -> None:
        self.parent = parent
        self.names: dict[str, _Binding] = {}

    def binding(self, name: str) -> _Binding | None:
        """The binding Python would resolve `name` to: the first scope that has one.

        Stopping at the first hit is the point. A flat table keyed by the name's text lets
        a later function's local of the same name overwrite an earlier one, and that is
        exactly how the third round of review on the TypeScript twin made a second owner
        invisible while every guard stayed green.
        """

        scope: _Scope | None = self
        while scope is not None:
            found = scope.names.get(name)
            if found is not None:
                return found
            scope = scope.parent
        return None

    def declare(self, name: str, value: ast.expr | None, scope: _Scope | None) -> None:
        found = self.names.setdefault(name, _Binding())
        found.writes += 1
        found.value = value
        found.scope = scope

    def block(self, name: str) -> None:
        """Record a name with no readable value, so nothing folds through it."""

        found = self.names.setdefault(name, _Binding())
        found.writes += 1
        found.value = None
        found.scope = None


def _argument_names(arguments: ast.arguments) -> list[str]:
    names = [argument.arg for argument in arguments.posonlyargs]
    names += [argument.arg for argument in arguments.args]
    names += [argument.arg for argument in arguments.kwonlyargs]
    if arguments.vararg is not None:
        names.append(arguments.vararg.arg)
    if arguments.kwarg is not None:
        names.append(arguments.kwarg.arg)
    return names


def _target_names(target: ast.expr) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        names: list[str] = []
        for element in target.elts:
            names.extend(_target_names(element))
        return names
    if isinstance(target, ast.Starred):
        return _target_names(target.value)
    return []


def _collect_scopes(tree: ast.AST) -> _Scope:
    """Record every name against the scope that declared it, in one traversal.

    Functions, lambdas, classes and comprehensions open a scope the way the compiler does;
    a class body is kept in the lookup chain even though Python resolves nested functions
    past it, because that only ever folds *more*, and every value it could fold to is a
    literal this file reads by value anyway. Statements that bind something which can
    change between runs are blocked rather than guessed at.
    """

    module = _Scope(None)

    def visit(node: ast.AST, scope: _Scope) -> None:
        setattr(node, _SCOPE_ATTRIBUTE, scope)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                visit(decorator, scope)
            for default in [*node.args.defaults, *node.args.kw_defaults]:
                if default is not None:
                    visit(default, scope)
            for argument in [
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
            ]:
                if argument.annotation is not None:
                    visit(argument.annotation, scope)
            if node.returns is not None:
                visit(node.returns, scope)
            inner = _Scope(scope)
            for name in _argument_names(node.args):
                inner.declare(name, None, None)
            for statement in node.body:
                visit(statement, inner)
            return
        if isinstance(node, ast.Lambda):
            for default in [*node.args.defaults, *node.args.kw_defaults]:
                if default is not None:
                    visit(default, scope)
            inner = _Scope(scope)
            for name in _argument_names(node.args):
                inner.declare(name, None, None)
            visit(node.body, inner)
            return
        if isinstance(node, ast.ClassDef):
            for base in [*node.bases, *node.keywords, *node.decorator_list]:
                visit(base, scope)
            inner = _Scope(scope)
            for statement in node.body:
                visit(statement, inner)
            return
        if isinstance(
            node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)
        ):
            inner = _Scope(scope)
            for index, comprehension in enumerate(node.generators):
                for name in _target_names(comprehension.target):
                    inner.block(name)
                visit(comprehension.target, inner)
                # The first iterable is evaluated where the comprehension is written.
                visit(comprehension.iter, scope if index == 0 else inner)
                for condition in comprehension.ifs:
                    visit(condition, inner)
            if isinstance(node, ast.DictComp):
                visit(node.key, inner)
                visit(node.value, inner)
            else:
                visit(node.elt, inner)
            return
        if isinstance(node, (ast.For, ast.AsyncFor)):
            for name in _target_names(node.target):
                scope.block(name)
            visit(node.target, scope)
            visit(node.iter, scope)
            for statement in [*node.body, *node.orelse]:
                visit(statement, scope)
            return
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                visit(item.context_expr, scope)
                if item.optional_vars is not None:
                    for name in _target_names(item.optional_vars):
                        scope.block(name)
                    visit(item.optional_vars, scope)
            for statement in node.body:
                visit(statement, scope)
            return
        if isinstance(node, ast.ExceptHandler):
            if node.name is not None:
                scope.block(node.name)
            if node.type is not None:
                visit(node.type, scope)
            for statement in node.body:
                visit(statement, scope)
            return
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            for name in node.names:
                scope.block(name)
            return
        if isinstance(node, ast.Import):
            for alias in node.names:
                scope.block(alias.asname or alias.name.split(".")[0])
            return
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                scope.block(alias.asname or alias.name)
            return
        if isinstance(node, ast.Delete):
            for target in node.targets:
                for name in _target_names(target):
                    scope.block(name)
                visit(target, scope)
            return
        if isinstance(node, ast.AugAssign):
            if isinstance(node.target, ast.Name):
                scope.declare(node.target.id, None, None)
            visit(node.target, scope)
            visit(node.value, scope)
            return
        if isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                if node.value is None:
                    scope.block(node.target.id)
                else:
                    scope.declare(node.target.id, node.value, scope)
            visit(node.target, scope)
            if node.annotation is not None:
                visit(node.annotation, scope)
            if node.value is not None:
                visit(node.value, scope)
            return
        if isinstance(node, ast.NamedExpr):
            if isinstance(node.target, ast.Name):
                scope.declare(node.target.id, None, None)
            visit(node.target, scope)
            visit(node.value, scope)
            return
        if isinstance(node, ast.Assign):
            single = len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
            for target in node.targets:
                if isinstance(target, ast.Name):
                    if single:
                        scope.declare(target.id, node.value, scope)
                    else:
                        scope.block(target.id)
                else:
                    for name in _target_names(target):
                        scope.block(name)
                visit(target, scope)
            visit(node.value, scope)
            return
        if isinstance(node, ast.MatchAs) and node.name is not None:
            scope.block(node.name)
        for child in ast.iter_child_nodes(node):
            visit(child, scope)

    for statement in tree.body:
        visit(statement, module)
    return module


def _scope_of(node: ast.AST, fallback: _Scope) -> _Scope:
    return getattr(node, _SCOPE_ATTRIBUTE, fallback)


def _fold_text(node: ast.expr | None, scope: _Scope, depth: int = 0) -> str | None:
    """The text an expression denotes, or None when it does not denote exactly one.

    Reads through `+` concatenation, same-file constant bindings, single-element lists and
    f-strings that carry no substitution. A bytes literal is decoded, because a pattern
    written as bytes is still a pattern stated in the source. Anything else answers
    "unknown", which is a different claim from "not a digest shape".
    """

    if node is None or depth > MAX_FOLD_DEPTH:
        return None
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            return node.value
        if isinstance(node.value, bytes):
            try:
                return node.value.decode("ascii")
            except UnicodeDecodeError:
                return None
        return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _fold_text(node.left, scope, depth + 1)
        right = _fold_text(node.right, scope, depth + 1)
        return left + right if left is not None and right is not None else None
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            else:
                return None
        return "".join(parts)
    if isinstance(node, ast.List) and len(node.elts) == 1:
        return _fold_text(node.elts[0], scope, depth + 1)
    if isinstance(node, ast.Name):
        binding = scope.binding(node.id)
        if binding is None or not binding.foldable:
            return None
        # The initializer is read in the scope that declared it, not in the scope that is
        # asking, which is what keeps a nested local from answering for an outer name.
        return _fold_text(binding.value, binding.scope, depth + 1)  # type: ignore[arg-type]
    return None


def _whole_value_shape(text: Any) -> str | None:
    """Classify a value as a whole-value digest shape, ignoring how it is anchored.

    Anchoring belongs to the call rather than to the value: `re.fullmatch` gives
    whole-string semantics to a literal carrying no anchors, and `\\Z` is `$` for that
    purpose. Anything with more grammar - a prefix of its own, an alternation, a suffix, a
    wider class - answers a different question and is left with the surface that wrote it.
    """

    if not isinstance(text, str) or "{64}" not in text:
        return None
    body = text
    if body.startswith(LEADING):
        body = body[1:]
    for tail in TRAILING:
        if body.endswith(tail):
            body = body[: -len(tail)]
            break
    enveloped = body.startswith(ENVELOPE)
    remainder = body[len(ENVELOPE) :] if enveloped else body
    for character_class in HEX64_CLASSES:
        if remainder == f"{character_class}{{64}}":
            return "enveloped" if enveloped else "bare"
    return None


def _owner_import_map(tree: ast.AST) -> dict[str, str | None]:
    """local name -> canonical export name, over imports of the owner module only.

    `None` marks a binding this file cannot attribute: a wildcard import, the module object
    itself, or some other attribute taken off the owner.
    """

    imported: dict[str, str | None] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module != "content_digest" and not module.endswith(
                "control_plane.content_digest"
            ):
                continue
            for alias in node.names:
                bound = alias.asname or alias.name
                imported[bound] = (
                    alias.name if alias.name in CANONICAL_EXPORTS else None
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.endswith("control_plane.content_digest"):
                    imported[alias.asname or "content_digest"] = None
    return imported


def _loaded_names(tree: ast.AST) -> set[str]:
    """Names this module reads, with the import clauses themselves excluded.

    This is what catches a consumer that keeps `BARE_SHA256_PATTERN` imported while
    checking the field with something else: the import leaves the name in the module
    dictionary, so an identity check on the attribute cannot see the substitution.
    """

    loaded: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            loaded.add(node.id)
        elif isinstance(node, ast.Attribute):
            base = node
            while isinstance(base, ast.Attribute):
                base = base.value
            if isinstance(base, ast.Name):
                loaded.add(base.id)
    return loaded


def _shape_statements(tree: ast.AST, root: _Scope) -> list[dict[str, Any]]:
    """Every whole-value digest shape this module states, by value, not by spelling."""

    found: dict[tuple[int, str], dict[str, Any]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
            text = _fold_text(node, root)
            shape = _whole_value_shape(text)
            if shape:
                found[(node.lineno, text or "")] = {
                    "line": node.lineno,
                    "value": text,
                    "shape": shape,
                    "how": "literal",
                }
            continue
        if isinstance(node, (ast.BinOp, ast.JoinedStr, ast.List, ast.Name)):
            value = _fold_text(node, _scope_of(node, root))
            shape = _whole_value_shape(value)
            if shape:
                found[(node.lineno, value or "")] = {
                    "line": node.lineno,
                    "value": value,
                    "shape": shape,
                    "how": f"folded from {type(node).__name__}",
                }
    return sorted(found.values(), key=lambda site: (site["line"], str(site["value"])))


def _re_names_bound(tree: ast.AST) -> tuple[set[str], set[str]]:
    """(names bound to the `re` module, names bound to one of its pattern functions)."""

    modules: set[str] = set()
    functions: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "re" or alias.name.endswith(".re"):
                    modules.add(alias.asname or "re")
        elif isinstance(node, ast.ImportFrom):
            if node.module == "re" or (node.module or "").endswith(".re"):
                for alias in node.names:
                    if alias.name in RE_CONSTRUCTIONS:
                        functions.add(alias.asname or alias.name)
    return modules, functions


def _regex_constructions(tree: ast.AST, root: _Scope) -> list[dict[str, Any]]:
    """Every regex construction, and the value it is handed when that can be read.

    Constructions only: `re.<name>(...)`, a bare `<name>(...)` bound by `from re import
    <name>`, and a `getattr(re, ...)` hop. A method call on a compiled pattern
    (`PATTERN.fullmatch(value)`) selects with a pattern that already exists, so counting
    those would sweep in every field read in the package.
    """

    modules, functions = _re_names_bound(tree)
    found: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        callable_node = node.func
        construction: str | None = None
        if isinstance(callable_node, ast.Attribute):
            base = callable_node.value
            if (
                callable_node.attr in RE_CONSTRUCTIONS
                and isinstance(base, ast.Name)
                and base.id in modules
            ):
                construction = callable_node.attr
            elif isinstance(base, ast.Call):
                inner = base.func
                hop = base.args[0] if base.args else None
                if (
                    isinstance(inner, ast.Name)
                    and inner.id == "getattr"
                    and isinstance(hop, ast.Name)
                    and hop.id in modules
                ):
                    construction = "getattr"
        elif isinstance(callable_node, ast.Name) and callable_node.id in functions:
            construction = callable_node.id
        if construction is None or construction in NOT_A_PATTERN_ARGUMENT:
            continue
        pattern = node.args[0]
        value = _fold_text(pattern, _scope_of(pattern, root))
        found.append(
            {
                "line": node.lineno,
                "construction": construction,
                "value": value,
                "shape": _whole_value_shape(value),
                "readable": value is not None,
                "argument": type(pattern).__name__,
            }
        )
    return found


def _owner_text_reads(tree: ast.AST) -> list[int]:
    """Lines reading `.pattern` / `.source` / `.flags` off a name bound to the owner."""

    imported = _owner_import_map(tree)
    lines = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr in {"pattern", "source", "flags"}
            and isinstance(node.value, ast.Name)
            and node.value.id in imported
        ):
            lines.append(node.lineno)
    return lines


def _analyse(source: str, name: str = "<module>") -> dict[str, Any]:
    tree = ast.parse(source, filename=name)
    root = _collect_scopes(tree)
    return {
        "tree": tree,
        "root": root,
        "statements": _shape_statements(tree, root),
        "constructions": _regex_constructions(tree, root),
        "owner_imports": _owner_import_map(tree),
        "owner_text_reads": _owner_text_reads(tree),
        "loaded": _loaded_names(tree),
    }


_REPO_SCAN: list[dict[str, Any]] | None = None


def _repo_scan() -> list[dict[str, Any]]:
    """One pass over every module under `loopx/`, shared by all four layers.

    1,167 modules parse and analyse in about four seconds, so this runs once per test
    session rather than once per layer, and no layer is given a different view of the tree.
    """

    global _REPO_SCAN
    if _REPO_SCAN is None:
        entries: list[dict[str, Any]] = []
        for path in sorted(
            candidate
            for candidate in PACKAGE_ROOT.rglob("*.py")
            if "__pycache__" not in candidate.parts
        ):
            source = path.read_text(encoding="utf-8")
            entry = _analyse(source, str(path))
            entry["path"] = path
            entry["source"] = source
            entry["relative"] = f"loopx/{path.relative_to(PACKAGE_ROOT).as_posix()}"
            parts = list(path.relative_to(PACKAGE_ROOT.parent).parts)
            parts[-1] = parts[-1][: -len(".py")]
            entry["dotted"] = ".".join(parts)
            entries.append(entry)
        _REPO_SCAN = entries
    return _REPO_SCAN


def _module_of(dotted: str) -> dict[str, Any]:
    for entry in _repo_scan():
        if entry["dotted"] == dotted:
            return entry
    raise AssertionError(f"{dotted} is no longer a module under loopx/")


def _runtime_whole_value_patterns(module: Any) -> list[tuple[str, str]]:
    """Patterns a loaded module holds that state a whole-value digest shape.

    Module attributes, and the values one container level deep, because the shape that
    hides best is parked in a tuple or a dict rather than bound to an obvious name.
    Identity is the requirement, not equality: a copy carrying the same text is a second
    owner, and so is one assembled at run time from pieces this file could not fold.
    """

    owned = {id(BARE_SHA256_PATTERN), id(ENVELOPED_SHA256_PATTERN)}
    found: list[tuple[str, str]] = []

    def consider(label: str, value: Any) -> None:
        if isinstance(value, re.Pattern) and id(value) not in owned:
            shape = _whole_value_shape(value.pattern)
            if shape:
                found.append((label, f"{shape}: {value.pattern!r}"))

    for attribute, value in list(vars(module).items()):
        if attribute.startswith("__"):
            continue
        consider(attribute, value)
        if isinstance(value, (list, tuple, set, frozenset)):
            for index, element in enumerate(value):
                consider(f"{attribute}[{index}]", element)
        elif isinstance(value, dict):
            for key, element in value.items():
                consider(f"{attribute}[{key!r}]", element)
    return found


# --- layer 1: no second statement of the shape -----------------------------------------


def test_only_the_owner_module_states_a_whole_value_digest_shape() -> None:
    offenders = {}
    for entry in _repo_scan():
        relative = entry["relative"]
        if relative in DEFERRED_WHOLE_VALUE_SITES or relative == OWNER_MODULE:
            continue
        if entry["statements"]:
            offenders[relative] = entry["statements"]
    assert not offenders, f"second owner(s) of the digest shape: {offenders}"


def test_owner_module_defines_each_shape_exactly_once() -> None:
    statements = _module_of(OWNER_DOTTED)["statements"]
    assert sorted({item["shape"] for item in statements}) == ["bare", "enveloped"], (
        statements
    )
    values = [item["value"] for item in statements]
    assert len(values) == len(set(values)), f"one shape stated twice: {statements}"


def test_deferred_sites_are_still_the_ones_this_branch_recorded() -> None:
    for relative, reason in DEFERRED_WHOLE_VALUE_SITES.items():
        assert reason, relative
        entry = next(
            (item for item in _repo_scan() if item["relative"] == relative), None
        )
        assert entry is not None, f"{relative} moved or vanished; update the allowlist"
        assert entry["statements"], f"{relative} no longer restates the shape"


# --- layer 1 self-check: the scan is judged by value, never by spelling -----------------

# Each entry is (label, source, expectation). `stated` has to be reported as a shape this
# module states; `other-question` has to be left alone; the rest name the layer that catches
# a shape which is not written as a plain anchored literal.
BYPASS_CORPUS = (
    (
        "anchored literal, the only spelling the first scan knew",
        'import re\n\n\nP = re.compile(r"^[0-9a-f]{64}$")\n',
        "stated",
    ),
    (
        "unanchored literal through re.fullmatch, reviewer round one",
        'import re\n\n\ndef check(value):\n    return re.fullmatch(r"[0-9a-f]{64}", value)\n',
        "stated",
    ),
    (
        r"closed with \Z instead of $",
        'import re\n\n\nP = re.compile(r"[0-9a-f]{64}\\Z")\n',
        "stated",
    ),
    (
        "the other character-class order",
        'import re\n\n\nP = re.compile(r"^[a-f0-9]{64}$")\n',
        "stated",
    ),
    (
        "two literals joined by +",
        'import re\n\n\nP = re.compile("^[0-9a-f]" + "{64}$")\n',
        "stated",
    ),
    (
        "the marker itself split across two literals",
        'import re\n\n\nP = re.compile("^[0-9a-f]{" + "64}$")\n',
        "stated",
    ),
    (
        "envelope and body joined from three pieces",
        'import re\n\n\nP = re.compile("sha256:" + r"[0-9a-f]{64}" + "$")\n',
        "stated",
    ),
    (
        "same-file constant, unanchored, reached through re.fullmatch",
        'import re\n\n\nHEAD = r"[0-9a-f]{64}"\n\n\ndef check(value):\n'
        "    return re.fullmatch(HEAD, value)\n",
        "stated",
    ),
    (
        "same-file constant built by + and compiled through the name",
        'import re\n\n\nSHAPE = "^" + "[0-9a-f]" + "{64}" + "$"\nP = re.compile(SHAPE)\n',
        "stated",
    ),
    (
        "f-string carrying no substitution",
        'import re\n\n\nP = re.compile(f"^[0-9a-f]{{64}}$")\n',
        "stated",
    ),
    (
        "pattern written as bytes",
        "import re\n\n\nP = re.compile(rb'^[0-9a-f]{64}$')\n",
        "stated",
    ),
    (
        "from re import fullmatch, no module prefix",
        "from re import fullmatch\n\n\ndef check(value):\n"
        '    return fullmatch(r"[0-9a-f]{64}", value)\n',
        "stated",
    ),
    (
        "import re as rx, an aliased module",
        'import re as rx\n\n\nP = rx.compile(r"sha256:[0-9a-f]{64}")\n',
        "stated",
    ),
    (
        "getattr(re, ...) hop",
        'import re\n\n\nP = getattr(re, "compile")(r"sha256:[0-9a-f]{64}")\n',
        "stated",
    ),
    (
        "pattern parked in a tuple and used through the loop variable",
        'import re\n\n\nfor key, pattern in [("evidence", r"sha256:[a-f0-9]{64}")]:\n'
        "    re.fullmatch(pattern, key)\n",
        "stated",
    ),
    (
        "pattern inside a dict of field rules",
        'import re\n\n\nRULES = {"digest": r"^[0-9a-f]{64}$"}\n',
        "stated",
    ),
    (
        "handle compiled from a foldable name, matched later",
        'import re\n\n\nSHAPE = r"[a-f0-9]{64}"\nP = re.compile(SHAPE)\n\n\ndef check(value):'
        "\n    return P.fullmatch(value)\n",
        "stated",
    ),
    (
        "the owner's text under IGNORECASE states it a second time, so it must be said",
        "import re\n"
        "from loopx.control_plane.content_digest import BARE_SHA256_PATTERN\n\n"
        'P = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)\n',
        "stated",
    ),
    (
        "compound id that merely contains a hex64",
        'import re\n\n\nP = re.compile(r"cadence_[0-9a-f]{64}")\n',
        "other-question",
    ),
    (
        "git object id alternation",
        'import re\n\n\nP = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")\n',
        "other-question",
    ),
    (
        "two hex64 fields inside one cursor",
        'import re\n\n\nP = re.compile(r"1:[0-9a-f]{64}:[0-9a-f]{64}")\n',
        "other-question",
    ),
    (
        "journal filename grammar",
        'import re\n\n\nP = re.compile(r"prq_[0-9a-f]{64}\\.json$")\n',
        "other-question",
    ),
    (
        "a wider class that also accepts uppercase is its own policy",
        'import re\n\n\nP = re.compile(r"^[0-9a-fA-F]{64}$")\n',
        "other-question",
    ),
    (
        "inline case-insensitive group is its own policy",
        'import re\n\n\nP = re.compile(r"(?i)^[0-9a-f]{64}$")\n',
        "other-question",
    ),
    (
        "twelve-hex identifier, not a digest",
        'import re\n\n\nP = re.compile(r"todo_[a-f0-9]{12}")\n',
        "other-question",
    ),
    (
        "a consumer that asks the owner states nothing itself",
        "from loopx.control_plane.content_digest import BARE_SHA256_PATTERN\n\n\n"
        "def check(value):\n    return BARE_SHA256_PATTERN.fullmatch(value)\n",
        "consumer",
    ),
    (
        "a consumer that aliases the owner object",
        "from loopx.control_plane.content_digest import ENVELOPED_SHA256_PATTERN\n\n\n"
        "RECEIPT_PATTERN = ENVELOPED_SHA256_PATTERN\n",
        "consumer",
    ),
    (
        "a consumer that rebuilds the owner's text",
        "import re\nfrom loopx.control_plane.content_digest import BARE_SHA256_PATTERN\n\n\n"
        "P = re.compile(BARE_SHA256_PATTERN.pattern)\n",
        "text-read",
    ),
    (
        "a consumer that keeps the import and checks something else",
        "import re\nfrom loopx.control_plane.content_digest import BARE_SHA256_PATTERN\n\n\n"
        "def check(value):\n    return len(value) == 64 and value.isalnum()\n",
        "unused-import",
    ),
    (
        "a pattern that arrives from data the scan cannot read",
        "import re\nfrom loopx.control_plane.content_digest import BARE_SHA256_PATTERN\n\n\n"
        "def check(value, shape):\n    return re.fullmatch(shape, value)\n",
        "unreadable",
    ),
)


@pytest.mark.parametrize(
    "label,source,expectation",
    BYPASS_CORPUS,
    ids=[entry[0] for entry in BYPASS_CORPUS],
)
def test_the_scan_judges_the_value_and_not_the_spelling(
    label: str, source: str, expectation: str
) -> None:
    analysis = _analyse(source, "fixture")
    unfoldable = [item for item in analysis["constructions"] if not item["readable"]]

    if expectation == "stated":
        assert analysis["statements"], (
            f"{label}: a second owner in this spelling escaped"
        )
        assert {item["shape"] for item in analysis["statements"]} <= {
            "bare",
            "enveloped",
        }
    elif expectation == "other-question":
        assert not analysis["statements"], f"{label}: a different question was absorbed"
    elif expectation == "consumer":
        assert not analysis["statements"], (
            f"{label}: asking the owner read as stating one"
        )
        assert not unfoldable, f"{label}: a consumer was left unreadable"
        assert not analysis["owner_text_reads"], (
            f"{label}: owner text read off the object"
        )
    elif expectation == "text-read":
        assert len(analysis["owner_text_reads"]) == 1, analysis["owner_text_reads"]
        assert not analysis["statements"], (
            "expected the static scan alone to miss this one"
        )
    elif expectation == "unused-import":
        assert analysis["owner_imports"] == {
            "BARE_SHA256_PATTERN": "BARE_SHA256_PATTERN"
        }, analysis["owner_imports"]
        assert "BARE_SHA256_PATTERN" not in analysis["loaded"], (
            f"{label}: the dangling import looked used"
        )
    elif expectation == "unreadable":
        assert not analysis["statements"], (
            f"{label}: expected nothing for the scan to read"
        )
        assert unfoldable, f"{label}: a pattern from data passed as readable"
    else:  # pragma: no cover - protects the matrix against a mistyped expectation
        raise AssertionError(f"unknown expectation {expectation!r} for {label!r}")


def test_a_later_local_of_the_same_name_does_not_answer_for_an_outer_fold() -> None:
    """The defect the third round of review found on the TypeScript twin, in Python.

    `first()` asks for a module constant; `second()` binds a local of the same name to
    something that is not a shape. Read through a flat, text-keyed table, the later
    binding wins and the digest shape disappears from the scan. Read through scopes, the
    call site in `first()` still resolves to the module value, and it has to be reported.
    """

    source = (
        "import re\n"
        "\n"
        'HEAD = "[0-9a-f]"\n'
        'SHAPE = "^" + HEAD + "{64}$"\n'
        "\n"
        "\n"
        "def first(value):\n"
        "    return re.fullmatch(SHAPE, value)\n"
        "\n"
        "\n"
        "def second():\n"
        '    SHAPE = "a prose label"\n'
        "    return SHAPE\n"
    )
    statements = _analyse(source)["statements"]
    assert statements, "a shape reached only through a name escaped the scan entirely"
    assert any(item["how"] == "folded from Name" for item in statements), (
        f"the call site did not fold to the outer binding: {statements}"
    )
    assert {item["value"] for item in statements} == {"^[0-9a-f]{64}$"}, statements


def test_a_local_shape_does_not_leak_out_to_the_module_scope() -> None:
    """The other direction: an inner binding must not answer for an outer name.

    `uses_it` refers to a `SHAPE` the module never declares, so the scan must not find the
    shape that `makes_it` built inside its own frame at the outer call site - and must
    still report the shape `makes_it` states.
    """

    source = (
        "import re\n"
        "\n"
        "\n"
        "def makes_it():\n"
        '    SHAPE = "^" + "[0-9a-f]" + "{64}$"\n'
        "    return SHAPE\n"
        "\n"
        "\n"
        "def uses_it(value):\n"
        "    return re.fullmatch(SHAPE, value)\n"
    )
    statements = _analyse(source)["statements"]
    # Both reported sites are inside `makes_it`: the binding it wrote, and the read of that
    # binding. Nothing at the `uses_it` line, whose `SHAPE` the module never declares.
    assert [item["line"] for item in statements] == [5, 6], statements
    assert [item["how"] for item in statements] == [
        "folded from BinOp",
        "folded from Name",
    ]
    assert all(item["shape"] == "bare" for item in statements), statements


def test_the_bypass_matrix_names_every_layer_it_relies_on() -> None:
    expectations = {entry[2] for entry in BYPASS_CORPUS}
    assert expectations == {
        "stated",
        "other-question",
        "consumer",
        "text-read",
        "unused-import",
        "unreadable",
    }, expectations
    labels = [entry[0] for entry in BYPASS_CORPUS]
    assert len(labels) == len(set(labels)), (
        "two fixtures share a label; test ids would collide"
    )


# --- layer 2: who may depend on the owner, and how --------------------------------------


def test_the_consumer_manifest_is_exactly_the_pinned_set() -> None:
    derived = {entry["dotted"] for entry in _repo_scan() if entry["owner_imports"]}
    pinned = set(CONSUMER_MODULES)
    assert derived == pinned, (
        f"imports the owner but is not pinned: {sorted(derived - pinned)}; "
        f"pinned but imports nothing from it any more: {sorted(pinned - derived)}"
    )


def test_consumers_name_the_canonical_exports_and_use_them() -> None:
    unnamed = {}
    dangling = {}
    for entry in _repo_scan():
        imported = entry["owner_imports"]
        if not imported:
            continue
        for local, canonical in imported.items():
            if canonical is None:
                unnamed.setdefault(entry["dotted"], []).append(local)
            elif local not in entry["loaded"]:
                dangling.setdefault(entry["dotted"], []).append(local)
    assert not unnamed, f"wildcard or module-object import of the owner: {unnamed}"
    assert not dangling, f"imported from the owner but never referenced: {dangling}"


def test_every_consumer_holds_the_owner_object_not_an_equal_copy() -> None:
    for dotted in CONSUMER_MODULES:
        module = importlib.import_module(dotted)
        copies = _runtime_whole_value_patterns(module)
        assert not copies, (
            f"{dotted} holds a copy of a shape it should borrow: {copies}"
        )


def test_no_module_rebuilds_the_shape_from_the_owner_text() -> None:
    offenders = {}
    for entry in _repo_scan():
        if entry["relative"] == OWNER_MODULE or entry["relative"] in OWNER_TEXT_READS:
            continue
        if entry["owner_text_reads"]:
            offenders[entry["relative"]] = entry["owner_text_reads"]
    assert not offenders, f"owner text read off the object and recompiled: {offenders}"


# --- layer 3: a consumer has to stay readable --------------------------------------------


def test_no_consumer_holds_a_regex_this_file_cannot_read() -> None:
    offenders = {}
    for dotted in CONSUMER_MODULES:
        unreadable = [
            item for item in _module_of(dotted)["constructions"] if not item["readable"]
        ]
        if unreadable:
            offenders[dotted] = unreadable
    for dotted in UNREADABLE_CONSUMER_CONSTRUCTIONS:
        offenders.pop(dotted, None)
    assert not offenders, (
        "a consumer handed `re` a pattern this scan cannot fold; fold it back into a "
        f"literal or record it with the question it answers: {offenders}"
    )


def test_every_declared_unreadable_consumer_site_is_still_there() -> None:
    for dotted, reason in UNREADABLE_CONSUMER_CONSTRUCTIONS.items():
        assert reason, dotted
        unreadable = [
            item for item in _module_of(dotted)["constructions"] if not item["readable"]
        ]
        assert unreadable, f"{dotted} has no unreadable construction left to declare"


# --- layer 4: behaviour, per spelling and per surface -------------------------------------


@pytest.mark.parametrize("value", ENVELOPED_ACCEPTS)
def test_enveloped_pattern_accepts_a_prefixed_digest(value: str) -> None:
    assert ENVELOPED_SHA256_PATTERN.fullmatch(value) is not None


@pytest.mark.parametrize("value", ENVELOPED_REJECTS)
def test_enveloped_pattern_rejects_every_other_shape(value: object) -> None:
    assert ENVELOPED_SHA256_PATTERN.fullmatch(value) is None


@pytest.mark.parametrize("value", BARE_ACCEPTS)
def test_bare_pattern_accepts_lowercase_hex(value: str) -> None:
    assert BARE_SHA256_PATTERN.fullmatch(value) is not None


@pytest.mark.parametrize("value", BARE_REJECTS)
def test_bare_pattern_rejects_everything_else(value: object) -> None:
    assert BARE_SHA256_PATTERN.fullmatch(value) is None


@pytest.mark.parametrize(
    "label,spelling,owner",
    RETIRED_SPELLINGS,
    ids=[entry[0] for entry in RETIRED_SPELLINGS],
)
@pytest.mark.parametrize("value", PARITY_PROBES)
def test_each_retired_spelling_and_the_owner_split_the_same_strings(
    label: str, spelling: str, owner: re.Pattern[str], value: str
) -> None:
    """A migration changes behaviour only if some probe divides the two.

    Each retired spelling is invoked the way its site invoked it - `re.fullmatch` on the
    text for the sites that used the module function, `.fullmatch` on a compiled pattern for
    the sites that held a handle - and both are compared with the owner object. The probe set
    includes the trailing newline, which is where `$` and `\\Z` could have disagreed.
    """

    assert bool(re.fullmatch(spelling, value)) is bool(owner.fullmatch(value)), (
        label,
        value,
    )
    assert bool(re.compile(spelling).fullmatch(value)) is bool(
        owner.fullmatch(value)
    ), (label, value)


def test_the_owner_is_a_leaf_and_exports_only_the_two_shapes() -> None:
    public = {name for name in vars(content_digest) if not name.startswith("_")}
    assert public == {"annotations", "re", *CANONICAL_EXPORTS}, public
    patterns = {
        name
        for name, value in vars(content_digest).items()
        if isinstance(value, re.Pattern)
    }
    assert patterns == set(CANONICAL_EXPORTS), patterns


def test_periodic_report_generation_receipt_checks_both_digest_fields() -> None:
    """The site a reviewer named: two digest fields, checked by one reader.

    Both are the enveloped shape, and swapping either for the bare one has to be visible
    here rather than only in the text of the module, because an envelope swap leaves the
    owner object in place and therefore no trace for the value scan.
    """

    from loopx.capabilities.periodic_report import bindings

    def artifact(content: str, document: str) -> dict[str, str]:
        return {
            "artifact_id": "art-1",
            "renderer_id": "renderer-1",
            "renderer_kind": "markdown",
            "artifact_ref": "report://document/1",
            "content_digest": content,
            "document_digest": document,
        }

    def receipt(document: str, content: str | None = None) -> dict[str, Any]:
        normalized = artifact(content or document, document)
        return {
            "schema_version": bindings.GENERATION_RECEIPT_SCHEMA,
            "status": "succeeded",
            "generation_id": bindings._identity(
                {"document_digest": document, "artifacts": [normalized]},
                prefix="report_generation",
            ),
            "document_digest": document,
            "artifact_receipts": [normalized],
            "artifact_count": 1,
            "provider_required": False,
            "external_writes_performed": False,
        }

    assert (
        bindings._generation_receipt(receipt(ENVELOPED))["document_digest"] == ENVELOPED
    )
    with pytest.raises(ValueError, match="document_digest must use sha256"):
        bindings._generation_receipt(receipt(HEX64))
    with pytest.raises(ValueError, match="content_digest must use sha256"):
        bindings._generation_receipt(receipt(ENVELOPED, HEX64))


def test_schema_string_site_is_the_same_question_as_the_owner_bare_shape() -> None:
    statements = _module_of("loopx.capabilities.manager_context.inspection")[
        "statements"
    ]
    assert statements, "the recorded schema site no longer states the shape"
    for statement in statements:
        assert statement["shape"] == "bare"
        declared = re.compile(statement["value"])
        for value in PARITY_PROBES:
            assert bool(declared.fullmatch(value)) is bool(
                BARE_SHA256_PATTERN.fullmatch(value)
            ), value


# --- per-surface wiring: every case enters through that surface's own reader -------------


def test_periodic_report_archive_requires_the_envelope() -> None:
    from loopx.capabilities.periodic_report import archive

    assert archive._sha256(ENVELOPED, "revision") == ENVELOPED
    with pytest.raises(ValueError, match="must use sha256"):
        archive._sha256(HEX64, "revision")


def test_periodic_report_incremental_requires_the_envelope() -> None:
    from loopx.capabilities.periodic_report import incremental

    assert incremental._digest(ENVELOPED, "fact_fingerprint") == ENVELOPED
    with pytest.raises(ValueError, match="must use sha256"):
        incremental._digest(HEX64, "fact_fingerprint")


def test_progress_review_receipt_rejects_the_envelope_it_never_stored() -> None:
    from loopx.capabilities.progress_review import receipt

    assert receipt._hex64(HEX64, field="basis_digest") == HEX64
    with pytest.raises(ValueError, match="must be a sha256 hex digest"):
        receipt._hex64(ENVELOPED, field="basis_digest")


def test_presentation_extension_keeps_its_own_message_and_bare_shape() -> None:
    from loopx.extensions import presentation

    assert presentation._sha256(HEX64, context="artifact") == HEX64
    with pytest.raises(ValueError, match="must be a lowercase SHA-256"):
        presentation._sha256("z" * 64, context="artifact")
    # This surface also caps the field at 64 characters, so an enveloped digest never
    # reaches the shape check here. That limit is the surface's own policy and is left
    # alone; it is why the rejected probe above is same-length.
    with pytest.raises(ValueError, match="at most 64 characters"):
        presentation._sha256(ENVELOPED, context="artifact")


def test_release_commit_qualification_normalises_before_the_owner_check() -> None:
    from loopx.control_plane.testing import release_commit_qualification

    assert release_commit_qualification._digest(ENVELOPED, field="commit") == ENVELOPED
    with pytest.raises(ValueError, match="must be a sha256 digest"):
        release_commit_qualification._digest(HEX64, field="commit")


def test_configuration_revision_allows_the_absent_sentinel() -> None:
    from loopx.configuration_transaction import _validated_revision

    assert _validated_revision("absent", label="revision") == "absent"
    assert _validated_revision(ENVELOPED, label="revision") == ENVELOPED
    with pytest.raises(ValueError, match="must be absent or a sha256 revision"):
        _validated_revision(HEX64, label="revision")


def test_progress_review_policy_keeps_clearing_and_null_as_distinct_values() -> None:
    from loopx.control_plane.work_items.progress_review_policy import (
        normalize_progress_review_contract_revision,
    )

    assert normalize_progress_review_contract_revision(None) is None
    assert normalize_progress_review_contract_revision("") == ""
    assert (
        normalize_progress_review_contract_revision(HEX64) == HEX64
    )  # positive control
    with pytest.raises(ValueError, match="must be a sha256 hex digest"):
        normalize_progress_review_contract_revision(ENVELOPED)


def test_deletion_source_basis_checks_both_digest_fields() -> None:
    from loopx.control_plane.goals.deletion_service import (
        GOAL_DELETION_SOURCE_BASIS_SCHEMA_VERSION,
        _normalize_source_basis,
    )

    basis: dict[str, Any] = {
        "schema_version": GOAL_DELETION_SOURCE_BASIS_SCHEMA_VERSION,
        "source_identity": HEX64,
        "source_content_sha256": MIXED_HEX64,
        "route_mode": "source_to_global",
    }
    assert _normalize_source_basis(basis)["source_content_sha256"] == MIXED_HEX64
    with pytest.raises(ValueError, match="source identity"):
        _normalize_source_basis({**basis, "source_identity": ENVELOPED})
    with pytest.raises(ValueError, match="content digest"):
        _normalize_source_basis({**basis, "source_content_sha256": HEX64.upper()})


def test_periodic_report_delivery_authority_checks_its_effective_revision() -> None:
    from loopx.capabilities.periodic_report.machine_defaults import (
        DELIVERY_AUTHORITY_SCHEMA,
        normalize_periodic_report_delivery_authority,
    )

    authority = {
        "schema_version": DELIVERY_AUTHORITY_SCHEMA,
        "kind": "enabled_periodic_report_subscription",
        "goal_id": "goal-1",
        "source": "machine_default",
        "effective_revision": ENVELOPED,
        "route_ref": "route-1",
    }
    assert (
        normalize_periodic_report_delivery_authority(authority)["effective_revision"]
        == ENVELOPED
    )
    with pytest.raises(ValueError, match="effective_revision is invalid"):
        normalize_periodic_report_delivery_authority(
            {**authority, "effective_revision": HEX64}
        )


def test_governed_transition_receipt_checks_the_intent_basis_field_only() -> None:
    from loopx.control_plane.work_items.governed_transition_proposal import (
        GOVERNED_TRANSITION_RECEIPT_SCHEMA_VERSION as RECEIPT_SCHEMA,
        validate_governed_transition_receipts,
    )

    receipt = {
        "schema_version": RECEIPT_SCHEMA,
        "kind": "continuous_monitor_upsert",
        "status": "committed",
        "proposal_id": "prop-1",
        "proposal_digest": ENVELOPED,
        "action": "upsert",
        "todo_id": "todo-1",
        "monitor_key": "monitor-1",
        "target_key": "target-1",
        "intent_basis": ENVELOPED,
    }
    assert (
        validate_governed_transition_receipts([receipt])[0]["intent_basis"] == ENVELOPED
    )
    with pytest.raises(ValueError, match="intent_basis is invalid"):
        validate_governed_transition_receipts([{**receipt, "intent_basis": HEX64}])


def test_shadow_outbox_cursor_keeps_its_envelope_and_its_absent_option() -> None:
    from loopx.control_plane.coordination.local_authority_shadow_outbox import (
        DRAIN_CURSOR_SCHEMA,
        OutboxError,
        decode_cursor,
    )
    from loopx.control_plane.coordination.local_authority_shadow_projection import (
        PARTITIONS,
    )

    def cursor(digest: object) -> dict[str, Any]:
        return {
            "schema_version": DRAIN_CURSOR_SCHEMA,
            "partition": PARTITIONS[0],
            "last_seq": 1,
            "last_entry_id": f"local-shadow-tx-{HEX64}",
            "last_partition_digest": digest,
            "last_cursor": "cursor-opaque",
            "last_provider_revision": "revision-opaque",
            "updated_at": "2026-09-28T00:00:00+00:00",
        }

    partition = PARTITIONS[0]
    assert decode_cursor(cursor(ENVELOPED), partition=partition)["last_seq"] == 1
    assert (
        decode_cursor(cursor(None), partition=partition) is not None
    )  # an unbound cursor is legal on this surface
    with pytest.raises(OutboxError, match="cursor binding"):
        decode_cursor(cursor(HEX64), partition=partition)


# --- the four surfaces this round moved, entered through their own reader ----------------


def test_inbox_linked_references_keep_their_two_shapes(tmp_path: Path) -> None:
    """The site whose shape reached `re.fullmatch` through a loop variable, not a literal.

    Both fields of a links receipt are checked by the same loop, so this case is also the
    evidence that `todo_` identifiers kept their own twelve-character shape while the
    evidence ids moved onto the owner's envelope.
    """

    from loopx.control_plane.collaboration import inbox

    row = {"request_id": HEX64}

    def read(value: dict[str, Any]) -> tuple[dict, str | None]:
        path = inbox._root(tmp_path) / "links" / f"{row['request_id']}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
        return inbox._receipt(tmp_path, "links", row)

    accepted = {
        "request_id": HEX64,
        "todo_ids": [TODO_ID],
        "evidence_ids": [ENVELOPED],
    }
    assert read(accepted) == (accepted, None)
    # The todo shape stayed where it was, so an uppercase one is still not a todo id.
    assert read({**accepted, "todo_ids": [TODO_ID.upper()]})[1] == (
        "links_unreadable_or_conflicting"
    )
    # A bare digest is no longer accepted as evidence: this field carries the envelope.
    assert read({**accepted, "evidence_ids": [HEX64]}) == (
        {},
        "links_unreadable_or_conflicting",
    )
    assert read({**accepted, "todo_ids": [HEX64]}) == (
        {},
        "links_unreadable_or_conflicting",
    )


def test_outcome_projection_fingerprint_requires_the_envelope() -> None:
    from loopx.capabilities.issue_fix import outcome_projection

    pattern = outcome_projection._REPOSITORY_FINGERPRINT_PATTERN
    assert pattern.fullmatch(ENVELOPED) is not None
    assert pattern.fullmatch(HEX64) is None
    assert pattern is ENVELOPED_SHA256_PATTERN, "the surface kept a copy, not the owner"


def test_reviewer_notification_receipt_requires_the_envelope() -> None:
    from loopx.domain_packs import issue_fix

    pattern = issue_fix.REVIEWER_NOTIFICATION_RECEIPT_PATTERN
    assert pattern.fullmatch(ENVELOPED) is not None
    assert pattern.fullmatch(f"{ENVELOPE}{HEX64.upper()}") is None
    assert pattern is ENVELOPED_SHA256_PATTERN, "the domain pack restated the envelope"


def test_lark_idempotency_key_requires_the_envelope() -> None:
    from loopx.extensions.lark import document_comment_provider

    pattern = document_comment_provider.IDEMPOTENCY_KEY_PATTERN
    assert pattern.fullmatch(ENVELOPED) is not None
    assert pattern.fullmatch(HEX64) is None
    assert pattern is ENVELOPED_SHA256_PATTERN, "the provider kept its own copy"


def test_chat_bundle_source_digest_uses_the_owner_bare_shape() -> None:
    from loopx.presentation import chat_bundle

    assert chat_bundle.BARE_SHA256_PATTERN is BARE_SHA256_PATTERN
