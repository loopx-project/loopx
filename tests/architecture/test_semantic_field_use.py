"""Pin the B3 retirement metric against finite counterexamples.

The metric replaces a token count with a syntactic role, so the tests that
matter are the ones that separate populations the token count merged: a reader
from a writer, a same-prefix identifier from the field, prose from an access,
and a computed key from an absent one.
"""

from __future__ import annotations

import ast

import pytest

from loopx.semantics.field_use import (
    FACTS,
    ROLES,
    field_use_summary,
    lexical_module_count,
    python_module_scan,
    scan_field_uses,
)
from loopx.semantics.inventory import SourceFile

FIELD = "goal_boundary"
FIELDS = frozenset({FIELD})


@pytest.mark.parametrize("suffix, expected", [(".py", 3), (".ts", 1)])
def test_lexical_modules_preserve_boundaries_and_raw_source_text(suffix: str, expected: int) -> None:
    sources = [
        SourceFile("comment.py", ".py", "# goal_boundary goal_boundary"),
        SourceFile("prefix.py", ".py", "goal_boundary_repair repair_goal_boundary"),
        SourceFile("unicode.py", ".py", "汉goal_boundary字"),
        SourceFile("escaped.py", ".py", r'"goal_\u0062oundary"'),
        SourceFile("broken.py", ".py", "def broken(:\n# goal_boundary"),
        SourceFile("other.ts", ".ts", "// goal_boundary"),
        SourceFile("absent.py", ".py", "payload.get(dynamic_key)"),
    ]
    assert lexical_module_count(FIELD, suffix, iter(sources)) == expected


def python(text: str) -> dict[str, set[str]]:
    return python_module_scan(ast.parse(text), FIELDS)[0]


def source(text: str, suffix: str = ".py", path: str = "loopx/probe") -> SourceFile:
    return SourceFile(path + suffix, suffix, text)


def role(text: str, suffix: str = ".py") -> str:
    uses, _ = scan_field_uses([FIELD], [source(text, suffix)])
    assert len(uses) == 1, uses
    return uses[0].role


@pytest.mark.parametrize("text", [
    'value = payload["goal_boundary"]',
    'value = payload.get("goal_boundary")',
    'value = payload.pop("goal_boundary", None)',
    'present = "goal_boundary" in payload',
    'value = route.goal_boundary',
])
def test_literal_key_reads_are_readers(text: str) -> None:
    assert role(text) == "reader"


@pytest.mark.parametrize("text", [
    'payload["goal_boundary"] = built',
    'payload = {"goal_boundary": built}',
    'emit(goal_boundary=built)',
    'payload.setdefault("goal_boundary", {})',
    'route.goal_boundary = None',
])
def test_literal_key_writes_are_writers(text: str) -> None:
    assert role(text) == "writer"


def test_a_module_that_reads_and_writes_counts_as_a_reader_and_stays_in_the_surface() -> None:
    text = 'def project(payload):\n    payload["goal_boundary"] = payload.get("goal_boundary")\n'
    uses, _ = scan_field_uses([FIELD], [source(text)])
    assert uses[0].reads and uses[0].writes
    assert uses[0].role == "reader" and uses[0].in_migration_surface


@pytest.mark.parametrize("text", [
    'def build(goal_boundary):\n    return goal_boundary\n',
    'goal_boundary = collect()\nreturn goal_boundary\n',
])
def test_named_parameters_and_locals_are_bindings_in_the_migration_surface(text: str) -> None:
    uses, _ = scan_field_uses([FIELD], [source(text)])
    assert uses[0].role == "binding"
    assert uses[0].in_migration_surface, "a signature carrying the field still has to change"


@pytest.mark.parametrize("text", [
    '"""The quota guard publishes goal_boundary for the agent."""',
    '# goal_boundary is described in the should-run docs',
    'note = "read quota.goal_boundary.capabilities before spending"',
])
def test_prose_is_a_mention_and_never_enters_the_migration_surface(text: str) -> None:
    uses, _ = scan_field_uses([FIELD], [source(text)])
    assert uses[0].role == "mention"
    assert not uses[0].in_migration_surface


def test_a_field_name_carried_as_data_is_unresolved_rather_than_absent() -> None:
    text = 'LEGACY = ["goal_boundary", "work_lane_contract"]\nfor field in LEGACY:\n    payload.get(field)\n'
    uses, _ = scan_field_uses([FIELD], [source(text)])
    assert uses[0].role == "unresolved"
    assert "name_constant" in uses[0].forms
    assert uses[0].in_migration_surface, (
        "the field name is in this module as data; someone has to read it before the field "
        "can be removed, and that is field-specific migration work whichever way it resolves"
    )


def test_the_same_prefix_identifier_is_not_a_use_of_the_field() -> None:
    text = (
        'value = payload["goal_boundary_repair"]\n'
        'other = payload.get("repair_goal_boundary")\n'
        'route.goal_boundary_repair = None\n'
    )
    assert python(text) == {}
    uses, _ = scan_field_uses([FIELD], [source(text)])
    assert uses == [], "goal_boundary_repair must not be counted as a reader of goal_boundary"


def test_computed_keys_are_counted_as_the_standing_unknown() -> None:
    tree = ast.parse('payload.get(name)\npayload.get("goal_boundary")\nrows[index]\npayload.pop(key, None)\n')
    assert python_module_scan(tree, FIELDS)[1] == 2, (
        "literal keys are attributable and sequence indexing is not a mapping access"
    )


def test_computed_keys_are_counted_in_modules_that_never_name_the_field() -> None:
    uses, unknown = scan_field_uses([FIELD], [source("payload.get(name)\n", path="loopx/other")])
    assert uses == [] and unknown.python_mapping_calls == 1, (
        "the standing unknown is repository-wide; narrowing it to modules that name the "
        "field would make a zero-reader field look proven"
    )


def test_an_unparseable_module_is_an_unknown_rather_than_a_mention() -> None:
    uses, _ = scan_field_uses([FIELD], [source('def broken(:\n    "goal_boundary"\n')])
    assert [use.role for use in uses] == ["unresolved"]
    assert uses[0].forms == frozenset({"unparsed_module"})
    assert uses[0].in_migration_surface, (
        "a module the scan could not read may hold readers; calling it a mention would "
        "shrink the surface on the strength of a parse failure"
    )


def test_an_unparseable_module_does_not_fail_the_scan_for_its_callers() -> None:
    uses, _ = scan_field_uses(
        [FIELD],
        [source('def broken(:\n    "goal_boundary"\n', path="loopx/broken"),
         source('value = payload["goal_boundary"]', path="loopx/reader")],
    )
    assert [use.role for use in uses] == ["unresolved", "reader"]


@pytest.mark.parametrize("text, expected", [
    ("const source = object(payload.goal_boundary);", "reader"),
    ('const source = payload["goal_boundary"];', "reader"),
    ("capsule.goal_boundary = projection;", "writer"),
    ('capsule["goal_boundary"] = projection;', "writer"),
    ("  goal_boundary: JsonObject;", "mention"),
    ('report("decision.goal_boundary", value);', "mention"),
    ("// goal_boundary is projected downstream", "mention"),
    ("const repaired = payload.goal_boundary_repair;", None),
])
def test_typescript_forms_are_classified_by_the_bounded_ast_scan(text: str, expected: str | None) -> None:
    uses, _ = scan_field_uses([FIELD], [source(text, ".ts")])
    assert [use.role for use in uses] == ([expected] if expected else [])


def test_typescript_string_paths_do_not_become_property_reads() -> None:
    assert role('log("decision.goal_boundary");', ".ts") == "mention"


@pytest.mark.parametrize("text, expected", [
    ('// payload["goal_boundary"]', "mention"),
    ('/* payload["goal_boundary"] = value; */', "mention"),
    ('''const note = 'payload["goal_boundary"]';''', "mention"),
    ('const note = `payload["goal_boundary"]`;', "mention"),
    ('const url = "https://example.test"; const value = payload.goal_boundary;', "reader"),
    ('const marker = "/*"; payload["goal_boundary"] = value;', "writer"),
    ('// "unclosed quote\nconst value = payload["goal_boundary"];', "reader"),
    ('const pattern = /["\\\']+/; const value = payload.goal_boundary;', "reader"),
    ('const value = `${payload.goal_boundary}`;', "reader"),
    ('const pattern = /payload.goal_boundary/;', "mention"),
    ('const value = payload?.["goal_boundary"];', "reader"),
    ('payload.goal_boundary += 1;', "reader"),
])
def test_typescript_lexical_context_preserves_real_accesses(text: str, expected: str) -> None:
    assert role(text, ".ts") == expected


def test_commented_write_does_not_change_a_real_reader_into_a_writer() -> None:
    text = 'const value = payload["goal_boundary"]; // payload["goal_boundary"] = other;'
    uses, _ = scan_field_uses([FIELD], [source(text, ".ts")])
    assert uses[0].reads and not uses[0].writes


def test_roles_partition_the_token_count_so_the_metric_reclassifies_one_population() -> None:
    sources = [
        source('value = payload["goal_boundary"]', path="loopx/reader"),
        source('payload["goal_boundary"] = built', path="loopx/writer"),
        source('def build(goal_boundary):\n    return goal_boundary\n', path="loopx/binding"),
        source('LEGACY = ["goal_boundary"]', path="loopx/unresolved"),
        source('# goal_boundary', path="loopx/mention"),
        source('value = payload["goal_boundary_repair"]', path="loopx/unrelated"),
    ]
    summary = field_use_summary([FIELD], sources)["fields"][FIELD]
    classified = sum(summary[f"python_{role}_modules"] for role in ROLES)
    assert classified == summary["python_token_modules"] == 5
    # Reader, writer, binding and the unresolved name carrier; only the prose
    # mention is outside. The unresolved module is work whose shape is unknown,
    # not work that is known to be absent.
    assert summary["python_migration_surface"] == 4


# One access, written the way each runtime writes it. The metric exists to say
# where a retirement has to work, so the answer has to come from what the code
# does, not from which syntax it happens to use. A runtime that reads its half
# differently lets a port manufacture progress: move a dict literal to
# TypeScript and the migration surface shrinks with nothing migrated.
EQUIVALENT_ACCESSES = [
    ("read", 'value = payload["goal_boundary"]', 'const value = payload["goal_boundary"];'),
    ("read", 'value = payload.get("goal_boundary")', "const value = payload.goal_boundary;"),
    ("read", 'value = payload["goal_boundary"]', "const {goal_boundary} = payload;"),
    ("read", 'value = payload["goal_boundary"]', "const {goal_boundary: renamed} = payload;"),
    ("read", "def f(payload):\n    return payload['goal_boundary']\n",
     "function f({goal_boundary}: Payload) { return goal_boundary; }"),
    ("write", 'out = {"goal_boundary": built}', "const out = {goal_boundary: built};"),
    ("write", 'out["goal_boundary"] = built', "out.goal_boundary = built;"),
    ("declare", "def goal_boundary():\n    return 1\n",
     "interface Decision { goal_boundary: JsonObject; }"),
    ("unknown", 'LEGACY = ["goal_boundary"]', 'const legacy = ["goal_boundary"];'),
]


@pytest.mark.parametrize("meaning, python_source, typescript_source", EQUIVALENT_ACCESSES)
def test_an_equivalent_rewrite_does_not_change_the_answer(
    meaning: str, python_source: str, typescript_source: str,
) -> None:
    """Both runtimes must classify the same access the same way.

    This is the test that makes the surface safe to plan against. Without it
    the metric measures TypeScript idiom rather than TypeScript behaviour:
    destructuring is how TypeScript reads a payload and an object literal is
    how it writes one, and both once counted as prose.
    """
    python_role = role(python_source)
    typescript_role = role(typescript_source, ".ts")
    assert python_role == typescript_role, (
        f"{meaning}: python reads this as {python_role} and typescript as "
        f"{typescript_role}; a port between the runtimes would move the surface"
    )


@pytest.mark.parametrize("text, expected", [
    ("const {goal_boundary} = payload;", "reader"),
    ("const {goal_boundary: renamed} = payload;", "reader"),
    ("const {outer: {goal_boundary}} = payload;", "reader"),
    ("function build({goal_boundary}: Decision) { return goal_boundary; }", "reader"),
    ("const out = {goal_boundary: built};", "writer"),
    ("const out = {goal_boundary};", "writer"),
    ('const out = {"goal_boundary": built};', "writer"),
    ("interface Decision { goal_boundary: JsonObject; }", "binding"),
    ("type Decision = { goal_boundary: JsonObject };", "binding"),
    ("class Decision { goal_boundary: JsonObject; }", "binding"),
    ('const names = ["goal_boundary"];', "unresolved"),
    ('const key = "goal_boundary"; const value = payload[key];', "unresolved"),
])
def test_typescript_recognizes_the_forms_its_own_idiom_uses(text: str, expected: str) -> None:
    assert role(text, ".ts") == expected


def test_a_typescript_computed_member_is_the_standing_unknown_its_runtime_has() -> None:
    """A computed member read is TypeScript's `mapping.get(name)`.

    Python excludes `payload[key]` because `rows[index]` is the same syntax;
    TypeScript has no mapping accessor, so the computed member *is* the access
    and the count is an upper bound. Counting nothing at all was the worse
    error: it let the smoke claim a stated unknown that covered one runtime.
    """
    _, unknown = scan_field_uses([FIELD], [source("const value = payload[key];", ".ts")])
    assert unknown.typescript_members == 1
    _, indexed = scan_field_uses([FIELD], [source("const first = rows[0];", ".ts")])
    assert indexed.typescript_members == 0, "a numeric literal index is not a mapping read"


def test_the_typescript_unknown_covers_modules_that_never_name_the_field() -> None:
    """The standing unknown has to be repository-wide on both sides.

    Scanning only the modules that spell a field would measure a zero-reader
    field against an unknown drawn from the modules least likely to hide a
    reader. Python already counts every tracked module; this is the same
    obligation for the runtime whose population the substring filter used to
    narrow from 145 modules to 4.
    """
    _, unknown = scan_field_uses(
        [FIELD], [source("const value = payload[key];", ".ts", path="loopx/other")],
    )
    assert unknown.typescript_members == 1


def test_a_module_that_reads_and_writes_is_counted_in_both() -> None:
    """The role partition answers a different question than the producer count.

    A projection module reads the legacy field and re-emits it. The partition
    calls it a reader, because reader is first in ROLES, and it disappears from
    the writer count -- so a retirement looking for every producer would miss
    it. The overlapping counts are the ones that answer that.
    """
    text = ('value = payload["goal_boundary"]\n'
            'out = {"goal_boundary": value}\n')
    summary = field_use_summary([FIELD], [source(text, path="loopx/projection")])["fields"][FIELD]
    assert summary["python_reader_modules"] == 1 and summary["python_writer_modules"] == 0
    assert summary["python_reads_modules"] == 1 and summary["python_writes_modules"] == 1
    assert summary["python_migration_surface"] == 1


def test_the_roles_still_partition_the_token_count_over_the_typescript_forms() -> None:
    """The new TypeScript forms must reclassify carriers, not add or drop any."""
    sources = [
        source("const {goal_boundary} = payload;", ".ts", path="loopx/reader"),
        source("const out = {goal_boundary: built};", ".ts", path="loopx/writer"),
        source("interface D { goal_boundary: JsonObject }", ".ts", path="loopx/binding"),
        source('const names = ["goal_boundary"];', ".ts", path="loopx/unresolved"),
        source("// goal_boundary", ".ts", path="loopx/mention"),
    ]
    summary = field_use_summary([FIELD], sources)["fields"][FIELD]
    classified = sum(summary[f"typescript_{role}_modules"] for role in ROLES)
    assert classified == summary["typescript_token_modules"] == 5
    assert summary["typescript_migration_surface"] == 4, (
        "reader, writer, binding and the unresolved name carrier; only the comment is out"
    )


def test_the_five_fact_sets_overlap_and_their_union_is_the_whole_population() -> None:
    """Every module carrying the token owes at least one fact, and may owe several.

    The role label is single-valued, so counting labels under-reports whichever
    fact sorted second. The fact sets are the answer to "how many modules do
    X", they overlap, and only their union has to match the token count.
    """
    sources = [
        source('payload["goal_boundary"] = payload.get("goal_boundary")', path="loopx/projection"),
        source('value = payload["goal_boundary"]', path="loopx/reader"),
        source('payload["goal_boundary"] = built', path="loopx/writer"),
        source('def build(goal_boundary):\n    return goal_boundary\n', path="loopx/binding"),
        source('LEGACY = ["goal_boundary"]', path="loopx/unresolved"),
        source('# goal_boundary', path="loopx/mention"),
    ]
    entry = field_use_summary([FIELD], sources)["fields"][FIELD]
    assert entry["python_reads_modules"] == 2 and entry["python_writes_modules"] == 2
    assert entry["python_reader_modules"] == 2 and entry["python_writer_modules"] == 1, (
        "the role label keeps only the first fact, which is why it cannot be the count"
    )
    # Overlapping sets over-count the population; that is what makes them facts.
    assert sum(entry[f"python_{label}_modules"] for _, label in FACTS) == 7
    assert entry["python_classified_modules"] == entry["python_token_modules"] == 6
    assert entry["python_migration_surface"] == 5

    uses, _ = scan_field_uses([FIELD], sources)
    members = {fact: {use.module for use in uses if getattr(use, fact)} for fact, _ in FACTS}
    assert members["reads"] & members["writes"] == {"loopx/projection.py"}
    assert set().union(*members.values()) == {use.module for use in uses}
    assert not members["mention_only"] & (
        members["reads"] | members["writes"] | members["binds"] | members["unresolved"]
    )


# One logical access spelled every way TypeScript spells it, plus the type
# declaration that is not an access. `test_an_equivalent_rewrite_does_not_change_
# the_answer` pins the two runtimes to the same answer; this pins what that
# answer may be. Agreement alone is not enough: if both runtimes read a
# destructuring as prose they would agree and the surface would still shrink
# every time someone reformatted a reader.
EQUIVALENT_TYPESCRIPT_WRITINGS = [
    ("dotted_read", "const value = payload.goal_boundary;", "reader"),
    ("subscript_read", 'const value = payload["goal_boundary"];', "reader"),
    ("destructured_read", "const {goal_boundary} = payload;", "reader"),
    ("aliased_destructured_read", "const {goal_boundary: bound} = payload;\nuse(bound);", "reader"),
    ("object_literal_write", "const outbound = {goal_boundary: value};", "writer"),
    ("type_declaration", "type Payload = {goal_boundary: JsonObject};", "binding"),
    ("name_carried_to_a_subscript", 'const key = "goal_boundary";\nconst value = payload[key];', "unresolved"),
]


@pytest.mark.parametrize("name, text, expected", EQUIVALENT_TYPESCRIPT_WRITINGS)
def test_equivalent_writings_do_not_manufacture_retirement_progress(
    name: str, text: str, expected: str,
) -> None:
    uses, _ = scan_field_uses([FIELD], [source(text, ".ts")])
    assert len(uses) == 1, (name, uses)
    assert uses[0].role == expected, (name, sorted(uses[0].forms))
    assert uses[0].role != "mention", (
        f"{name} degraded to a mention; rewriting an access would shrink the migration "
        "surface with nothing migrated"
    )
    assert uses[0].in_migration_surface, (name, sorted(uses[0].forms))


def test_every_equivalent_writing_of_one_access_is_still_one_module_of_work() -> None:
    text = "\n".join(text for _, text, _ in EQUIVALENT_TYPESCRIPT_WRITINGS)
    uses, _ = scan_field_uses([FIELD], [source(text, ".ts")])
    assert len(uses) == 1, uses
    assert uses[0].reads and uses[0].writes and uses[0].binds and uses[0].unresolved
    assert uses[0].role == "reader" and uses[0].in_migration_surface
