"""Classify how each module uses a named payload field (RFC B3).

The retirement ledger used to count modules whose text contains the field
token. That metric answers "does this name appear here", which is not the
question retirement asks. Removing a legacy field requires knowing which
modules *read* it, which modules *write* it, and which merely mention it in
prose -- three populations the token count folds into one number.

What is measured here is syntactic use, not data flow. A module is a reader
when it performs a recognized literal-key read (``payload["legacy_field"]``,
``payload.get("legacy_field")``, ``"legacy_field" in payload``, or the
TypeScript property read). It is a writer when it performs a recognized literal
write (subscript store, dict-literal key, keyword argument, attribute store,
``setdefault``). Reader and writer are not exclusive: a projection module that
reads the legacy field and re-emits it is both.

These limits are part of the metric, not caveats around it:

* A computed key is **unresolved**. ``payload.get(name)`` may read any field, so
  no name-keyed scan -- lexical or syntactic -- can prove a module is not a
  reader. ``dynamic_mapping_key_sites`` counts those sites repository-wide, so a
  field measured at zero readers is measured against a stated unknown rather
  than declared dead, and a module that carries the field name as a bare string
  is reported as unresolved rather than as a mention. Subscripts with a computed
  key are deliberately *not* counted: ``rows[index]`` and ``payload[key]`` are
  the same syntax, and counting sequence indexing as an unresolved mapping read
  would inflate the unknown until it stopped carrying information.
* Same-prefix identifiers are different fields. ``legacy_field_repair`` is not
  a use of ``legacy_field``; both language AST scans compare whole keys.
* A field this module measures must not be spelled out here. The scan reads
  tracked sources under ``loopx/``, this file is one of them, and a field name
  in a docstring would add a mention to that field's own budget. The examples
  above use ``legacy_field`` for that reason; the real names live in the
  registry and in the smoke's anchors, outside the scanned Python/TypeScript
  sources.
* A mention is evidence of nothing. Prompt prose and module paths carry the
  token without a recognized access. Locals and parameters are instead bindings:
  they may carry the value through a signature that a migration must inspect.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import re
from typing import Any, Iterable

from .inventory import SourceFile, parse_python
from .production import run_typescript_scan

# ``dict``/``Mapping`` accessors whose first literal argument names a field.
MAPPING_READ_CALLS = frozenset({"get", "pop"})
MAPPING_WRITE_CALLS = frozenset({"setdefault"})

READ_FORMS = frozenset({
    "subscript_read", "mapping_call_read", "membership_read", "attribute_read",
    "property_read", "destructured_read",
})
WRITE_FORMS = frozenset({
    "subscript_write", "mapping_call_write", "dict_literal_key", "keyword_argument",
    "attribute_write", "property_write", "object_literal_key",
})
# The module names the field as a parameter, a local or its own definition. It
# handles the value without a recognized key access -- a pass-through consumer
# in the RFC's role hierarchy, and a signature the migration has to change.
BINDING_FORMS = frozenset({
    "local_binding", "local_reference", "parameter", "definition",
    "property_signature",
})
# This module's use of the field is not proven either way. ``name_constant`` is
# the field name travelling as data: a string constant that no recognized key
# position consumed -- a name in a field list a loop will index with, or a label
# in an emitted record. ``unparsed_module`` is a module the scan could not parse
# at all. Both are field-specific: someone has to open *this* module and decide
# before *this* field can be removed, which is why they join the migration
# surface rather than being counted as mentions. The repository-wide computed-key
# totals in ``UnresolvedKeySites`` are the other kind and stay out of it.
UNRESOLVED_FORMS = frozenset({"name_constant", "unparsed_module"})
MENTION_FORMS = frozenset({"module_import", "prose"})
USE_FORMS = READ_FORMS | WRITE_FORMS | BINDING_FORMS | UNRESOLVED_FORMS | MENTION_FORMS


@dataclass(frozen=True)
class UnresolvedKeySites:
    """Computed-key accesses that no name-keyed scan can attribute to a field.

    The two runtimes are counted apart because their exclusions differ, and a
    single total would hide that. ``python_mapping_calls`` counts only
    ``mapping.get(name)``-shaped calls with a computed argument, because
    ``rows[index]`` and ``payload[key]`` are the same subscript syntax in
    Python. TypeScript has no mapping-accessor convention, so the equivalent
    read *is* the computed member access: ``typescript_members`` counts those
    with a non-numeric argument and is therefore an upper bound that includes
    indexing an array by a variable.
    """

    python_mapping_calls: int = 0
    typescript_members: int = 0


@dataclass(frozen=True)
class FieldUse:
    """One module's recognized uses of one field."""

    field: str
    module: str
    forms: frozenset[str]

    @property
    def reads(self) -> bool:
        return bool(self.forms & READ_FORMS)

    @property
    def writes(self) -> bool:
        return bool(self.forms & WRITE_FORMS)

    @property
    def unresolved(self) -> bool:
        return bool(self.forms & UNRESOLVED_FORMS)

    @property
    def binds(self) -> bool:
        return bool(self.forms & BINDING_FORMS)

    @property
    def mention_only(self) -> bool:
        """True when nothing but prose or an import carries the name here."""
        return not (self.reads or self.writes or self.binds or self.unresolved)

    @property
    def role(self) -> str:
        """A single label for ordering and printing -- never a fact set.

        Facts overlap: a projection module both reads and writes. This label
        keeps only the first of ``reader > writer > binding > unresolved >
        mention``, so it can order a work queue and print one line per module,
        and it is the wrong thing to count a population with. ``reads``,
        ``writes``, ``binds``, ``unresolved`` and ``mention_only`` are the
        orthogonal facts; ``field_use_summary`` counts those beside it.
        """
        if self.reads:
            return "reader"
        if self.writes:
            return "writer"
        if self.binds:
            return "binding"
        if self.unresolved:
            return "unresolved"
        return "mention"

    @property
    def in_migration_surface(self) -> bool:
        """True when removing the field requires work in this module.

        Readers, writers and bindings have to change. ``unresolved`` modules
        have to be *investigated*: the field name is here as data, or the module
        did not parse, and nobody can say the field is absent without opening
        it. That is field-specific work, so it is counted. The repository-wide
        ``UnresolvedKeySites`` totals are not: they are attributable to no
        single field, so they stay a standing unknown beside every field's
        budget and can never authorize a deletion on their own.
        """
        return self.reads or self.writes or self.binds or self.unresolved


def _literal_key(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def python_module_scan(tree: ast.AST, fields: frozenset[str]) -> tuple[dict[str, set[str]], int]:
    """Recognized forms per field, and the module's computed-key site count.

    Both come from one walk. The scan runs over every tracked Python module on
    every pull request that touches ``loopx/``, so a second traversal is a cost
    paid by everyone. Parsing errors share the inventory's ``parse_python``
    boundary; ASTs are deliberately not retained in a cache.
    """
    found: dict[str, set[str]] = {}
    # Constants consumed as a literal key. Whatever is left over is the field
    # name travelling as data, which is how a computed access is written.
    keyed: set[int] = set()
    seen_constants: list[tuple[int, str]] = []
    dynamic_sites = 0

    def record(field: str, form: str) -> None:
        found.setdefault(field, set()).add(form)

    for node in ast.walk(tree):
        key = _literal_key(node)
        if key is not None:
            if key in fields:
                seen_constants.append((id(node), key))
            continue
        if isinstance(node, ast.Subscript):
            key = _literal_key(node.slice)
            if key in fields:
                keyed.add(id(node.slice))
                record(key, "subscript_write" if isinstance(node.ctx, (ast.Store, ast.Del)) else "subscript_read")
        elif isinstance(node, ast.Call):
            function = node.func
            if isinstance(function, ast.Attribute) and node.args:
                accessor = function.attr in MAPPING_READ_CALLS or function.attr in MAPPING_WRITE_CALLS
                key = _literal_key(node.args[0])
                if accessor and key is None:
                    dynamic_sites += 1
                elif key in fields and function.attr in MAPPING_READ_CALLS:
                    keyed.add(id(node.args[0]))
                    record(key, "mapping_call_read")
                elif key in fields and function.attr in MAPPING_WRITE_CALLS:
                    keyed.add(id(node.args[0]))
                    record(key, "mapping_call_write")
            for keyword in node.keywords:
                if keyword.arg in fields:
                    record(keyword.arg, "keyword_argument")
        elif isinstance(node, ast.Dict):
            for key_node in node.keys:
                key = _literal_key(key_node) if key_node is not None else None
                if key in fields:
                    keyed.add(id(key_node))
                    record(key, "dict_literal_key")
        elif isinstance(node, ast.Compare):
            key = _literal_key(node.left)
            if key in fields and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops):
                keyed.add(id(node.left))
                record(key, "membership_read")
        elif isinstance(node, ast.Attribute):
            if node.attr in fields:
                record(node.attr, "attribute_write" if isinstance(node.ctx, (ast.Store, ast.Del)) else "attribute_read")
        elif isinstance(node, ast.arg):
            if node.arg in fields:
                record(node.arg, "parameter")
        elif isinstance(node, ast.Name):
            if node.id in fields:
                record(node.id, "local_binding" if isinstance(node.ctx, (ast.Store, ast.Del)) else "local_reference")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in fields:
                record(node.name, "definition")
        elif isinstance(node, ast.ImportFrom):
            parts = set((node.module or "").split("."))
            for field in fields:
                if field in parts or any(alias.name == field for alias in node.names):
                    record(field, "module_import")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = set(alias.name.split("."))
                for field in fields & parts:
                    record(field, "module_import")
    for identity, key in seen_constants:
        if identity not in keyed:
            record(key, "name_constant")
    return found, dynamic_sites


@lru_cache(maxsize=256)
def _token_pattern(field: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![A-Za-z0-9_]){re.escape(field)}(?![A-Za-z0-9_])")


def lexical_module_count(field: str, suffix: str, sources: Iterable[SourceFile]) -> int:
    """The pre-B3 metric: modules whose text contains the standalone token."""
    token = _token_pattern(field)
    # Literal containment is a prerequisite of the same boundary regex.
    return sum(
        1 for source in sources
        if source.suffix == suffix and field in source.text and token.search(source.text)
    )


def scan_field_uses(
    fields: Iterable[str], sources: Iterable[SourceFile],
) -> tuple[list[FieldUse], UnresolvedKeySites]:
    """Classify every module's use of each field, beside the standing unknown."""
    wanted = frozenset(fields)
    uses: list[FieldUse] = []
    dynamic_sites = 0
    materialized = list(sources)
    # Every TypeScript module is scanned, not only those that name a field:
    # the standing unknown is repository-wide on both sides, and narrowing it
    # to modules that spell the field would measure a zero-reader field against
    # an unknown that excludes the modules most able to hide a reader. The
    # scanner records a form only for a requested field, so the wider
    # population costs one parse and adds no per-field work. Measured on 145
    # tracked modules: 0.86s narrowed against 0.52s more for all of them, and
    # the unknown it reports rises from 82 sites to 395.
    ts_sources = [source for source in materialized if source.suffix == ".ts"]
    # One AST request for the complete TS population, not one Node process per
    # module/field. Reuse the producer scanner's parser and safe error boundary.
    ts_rows = {row["path"]: row for row in run_typescript_scan(
        Path(__file__).resolve().parents[2], ts_sources,
        {"mode": "field_uses", "fields": sorted(wanted)},
    )}
    ts_dynamic_sites = sum(int(row.get("dynamic_member_sites") or 0) for row in ts_rows.values())
    for source in materialized:
        # Every Python module contributes to the computed-key total whether or
        # not it names a field, so the cheap substring filter only narrows the
        # per-field work, never the standing unknown.
        present = frozenset(field for field in wanted if field in source.text)
        if source.suffix == ".py":
            try:
                tree = parse_python(source)
            except (SyntaxError, ValueError):
                # An unparseable tracked module is a measurement gap, and a gap
                # may hold readers. Calling it a mention would shrink the
                # migration surface on the strength of a parse failure, so it is
                # recorded as this field's unknown and stays in the surface
                # until someone reads the module. Failing closed here is worse:
                # a direct caller scanning a work-in-progress tree would get an
                # exception instead of a measurement. The inventory scan rejects
                # such a module first, so the drift smoke never reaches this.
                for field in wanted:
                    if lexical_module_count(field, ".py", [source]):
                        uses.append(FieldUse(field=field, module=source.path,
                                             forms=frozenset({"unparsed_module"})))
                continue
            forms, module_dynamic_sites = python_module_scan(tree, present)
            dynamic_sites += module_dynamic_sites
        elif source.suffix == ".ts":
            row = ts_rows.get(source.path) or {}
            forms = {field: set(observed) for field, observed in (row.get("fields") or {}).items()}
        else:
            continue
        for field in present:
            recognized = forms.get(field, set())
            if not recognized and _token_pattern(field).search(source.text):
                # The token is present but no recognized form carries it: a
                # comment, a docstring, or prompt prose.
                recognized = {"prose"}
            if not recognized:
                continue
            unclassified = recognized - USE_FORMS
            if unclassified:
                # A form with no role would disappear from the role counts while
                # still carrying the token, breaking the partition the ledger
                # check relies on. Fail where the form was added, not there.
                raise ValueError(f"unclassified field-use form(s): {sorted(unclassified)}")
            uses.append(FieldUse(field=field, module=source.path, forms=frozenset(recognized)))
    return sorted(uses, key=lambda use: (use.field, use.module)), UnresolvedKeySites(
        python_mapping_calls=dynamic_sites, typescript_members=ts_dynamic_sites,
    )


# Print and ordering labels. Single-valued by construction, so they partition
# the classified population -- useful for a work queue, useless for asking how
# many modules read the field.
ROLES = ("reader", "writer", "binding", "unresolved", "mention")
# The orthogonal facts: each is a property of ``FieldUse``, and a module is
# counted in every one that holds of it. They overlap on purpose, so their
# counts do not sum to the population; only ``mention_only`` is disjoint from
# the rest. The summary key for each is ``{runtime}_{key}_modules``.
FACTS = (
    ("reads", "reads"),
    ("writes", "writes"),
    ("binds", "binds"),
    ("unresolved", "unresolved_use"),
    ("mention_only", "mention_only"),
)


def field_use_summary(fields: Iterable[str], sources: Iterable[SourceFile]) -> dict[str, Any]:
    """Per-field use counts and migration surface, beside the old token count.

    ``migration_surface`` is the number of modules that must be changed or at
    least investigated before the field can be removed: every reader, writer
    and binding, plus the field-specific unknowns. Only mentions -- prose and
    imports -- are outside it. The repository-wide ``UnresolvedKeySites``
    totals are reported beside the budgets and are part of no field's surface,
    because they are attributable to no field and emptying one can never retire
    them.

    Two families of count are reported per runtime and they answer different
    questions.

    ``*_reads_modules``, ``*_writes_modules``, ``*_binds_modules``,
    ``*_unresolved_use_modules`` and ``*_mention_only_modules`` are the
    orthogonal facts, one per entry in ``FACTS``: a module is counted in every
    set it belongs to, because a retirement has to fix every site it has. They
    overlap, so they do not sum to the population; ``*_classified_modules`` is
    their union and equals the token count.

    ``*_{role}_modules`` is instead a partition by the first role in ``ROLES``
    that a module matches, so those five counts do sum to the token count and
    the ledger can assert that the roles reclassify that population rather than
    sample a smaller one. A reader that also writes is a ``reader`` there and
    invisible in ``writer``, which is why the partition must not be read as a
    producer count.
    """
    materialized = list(sources)
    ordered = sorted(fields)
    uses, unknown = scan_field_uses(ordered, materialized)
    summary: dict[str, Any] = {
        "fields": {},
        "dynamic_mapping_key_sites": unknown.python_mapping_calls,
        "typescript_dynamic_member_sites": unknown.typescript_members,
    }
    for field in ordered:
        entry: dict[str, Any] = {}
        for suffix, runtime in ((".py", "python"), (".ts", "typescript")):
            selected = [use for use in uses if use.field == field and use.module.endswith(suffix)]
            roles = [use.role for use in selected]
            for role in ROLES:
                entry[f"{runtime}_{role}_modules"] = roles.count(role)
            for fact, label in FACTS:
                entry[f"{runtime}_{label}_modules"] = sum(1 for use in selected if getattr(use, fact))
            # One FieldUse per (field, module), and every use carries at least
            # one fact, so this is the union of the overlapping sets above.
            entry[f"{runtime}_classified_modules"] = len(selected)
            entry[f"{runtime}_migration_surface"] = sum(1 for use in selected if use.in_migration_surface)
            entry[f"{runtime}_token_modules"] = lexical_module_count(field, suffix, materialized)
        summary["fields"][field] = entry
    return summary


def render_field_uses(uses: Iterable[FieldUse]) -> list[str]:
    """One reviewable line per module, for ``--report``."""
    return [
        f"{use.field} {use.role} {use.module} [{','.join(sorted(use.forms))}]"
        for use in uses
    ]
