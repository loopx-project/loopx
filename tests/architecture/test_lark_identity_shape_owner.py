"""Guard the single owner of the whole-value Lark identifier shapes.

Three questions, in the order a reviewer will ask them:

1. Is there a second definition of one of these identifier shapes?  Decided on
   the folded value of every regular-expression construction in the product
   tree, not on spelling, so ``re.compile(PATTERN)``, an inline ``re.fullmatch``
   and a concatenated literal are all offenders.
2. Is every consumer wired to the owner?  Decided by object identity plus an
   actual reference in the module body, so a kept-alive import that no longer
   makes the decision is still an offender.
3. Are the two *uses* of a shape kept apart?  A whole-value check and a search
   for an id inside larger text are different questions, so the owner states
   both spellings: four anchored patterns and three unanchored ones.  Either
   spelling anywhere else is an offender, and so is converting a declared site
   without retiring its declaration.
"""

from __future__ import annotations

import ast
import pathlib
from types import ModuleType

import pytest

from loopx.extensions.lark import (
    event_collector,
    event_collector_routes,
    event_inbox,
    goal_channel_delivery_contract,
    goal_channel_operation,
    goal_channel_transport,
    identity_shapes,
    reviewer_notification,
    team_plan_confirmation,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
LARK_PACKAGE = REPO_ROOT / "loopx" / "extensions" / "lark"
OWNER_MODULE = "loopx/extensions/lark/identity_shapes.py"
TRANSPORT_HUB = "loopx/extensions/lark/goal_channel_transport.py"
INBOX_HUB = "loopx/extensions/lark/event_inbox.py"

# The four identifier bodies as the owner states them, without anchors.  The
# substring in the second column is what a module has to contain to be worth
# parsing at all; it is load-bearing, so a test below re-checks the mapping.
WHOLE_VALUE_BODIES = {
    "event_id": r"[A-Za-z0-9._:-]{1,240}",
    "message_id": r"om_[A-Za-z0-9_-]+",
    "chat_id": r"oc_[A-Za-z0-9_-]+",
    "operator_id": r"ou_[A-Za-z0-9_-]+",
}
# A module has to import the regex machinery before it can decide a shape, and
# both spellings this scan accepts (``re.X(...)`` and a name from
# ``from re import X``) require one of these two lines.  Screening on the bodies
# themselves would be unsound: a pattern assembled from two literals contains no
# single body, which is one of the probe cases below.
PRESCREEN_TOKENS = ("import re", "from re import")
# Only these three bodies have a search spelling.  An event id is never looked
# for inside larger text.
SEARCH_IDENTIFIERS = {"chat_id", "message_id", "operator_id"}
ANCHORED_EXPORTS = {
    "event_id": "LARK_EVENT_ID_PATTERN",
    "message_id": "LARK_MESSAGE_ID_PATTERN",
    "chat_id": "LARK_CHAT_ID_PATTERN",
    "operator_id": "LARK_OPEN_ID_PATTERN",
}
SEARCH_EXPORTS = {
    "message_id": "LARK_MESSAGE_ID_SEARCH",
    "chat_id": "LARK_CHAT_ID_SEARCH",
    "operator_id": "LARK_OPEN_ID_SEARCH",
}

# Sites that still decide an identifier shape for themselves, with the exact
# number of constructions allowed there.  Converting one deletes its entry, and
# editing one past its budget is a review event.
# Regex constructions in a marker file whose pattern cannot be folded.  There
# are none today, so the list is closed: a future indirect construction has to
# state the file, the count and the shape it answers, instead of hiding inside
# a scan that cannot see it.
DECLARED_UNFOLDABLE_SITES: dict[str, int] = {
    # A mention lookup built from a runtime display name.  It searches text for
    # ``@handle`` and decides no identifier shape at all.
    "loopx/extensions/lark/event_inbox.py": 1,
    # Keyword routing matches a caller-supplied pattern against message content.
    # The pattern is data at this boundary, and no identifier shape is decided.
    "loopx/extensions/lark/goal_topic_routing.py": 1,
}
# A file only enters the unfoldable layer when it plausibly touches these ids.
# Without the gate the layer reports every regex built from data in the package
# (setup URLs, markdown headings) and the declaration becomes noise.
IDENTIFIER_RELEVANCE_TOKENS = (
    "oc_",
    "om_",
    "ou_",
    "chat_id",
    "message_id",
    "operator_id",
    "event_id",
)

DECLARED_INDIVIDUAL_SITES: dict[str, int] = {
    # ``re.fullmatch(r"[A-Za-z0-9._:-]{1,240}", ...)`` against an event id.  The
    # in-flight goal-channel claim work restructures this file, so the site is
    # declared here instead of racing that branch.
    "loopx/extensions/lark/event_collector_runtime.py": 1,
}

SHAPE_CONSUMERS: dict[str, tuple[ModuleType, str]] = {
    "loopx/extensions/lark/goal_channel_operation.py": (
        goal_channel_operation,
        "require_card_callback_identity",
    ),
    "loopx/extensions/lark/team_plan_confirmation.py": (
        team_plan_confirmation,
        "require_card_callback_identity",
    ),
    "loopx/extensions/lark/event_collector.py": (event_collector, "LARK_CHAT_ID_PATTERN"),
    "loopx/extensions/lark/goal_channel_delivery_contract.py": (
        goal_channel_delivery_contract,
        "LARK_CHAT_ID_PATTERN",
    ),
    "loopx/extensions/lark/event_collector_routes.py": (
        event_collector_routes,
        "LARK_CHAT_ID_PATTERN",
    ),
    "loopx/extensions/lark/reviewer_notification.py": (
        reviewer_notification,
        "LARK_OPEN_ID_PATTERN",
    ),
}
HUB_REEXPORTS: dict[str, tuple[ModuleType, tuple[str, ...]]] = {
    INBOX_HUB: (event_inbox, ("CHAT_ID_PATTERN", "MESSAGE_ID_PATTERN")),
    TRANSPORT_HUB: (
        goal_channel_transport,
        ("CHAT_ID_PATTERN", "MESSAGE_ID_PATTERN", "OPEN_ID_PATTERN"),
    ),
}
_RE_MODULE = "re"
_REGEX_METHODS = {
    "compile",
    "fullmatch",
    "match",
    "search",
    "findall",
    "finditer",
    "sub",
    "subn",
    "split",
}


FOLD_DEPTH_LIMIT = 4


def fold_string(node: ast.AST, constants: dict[str, str], depth: int = 0) -> str | None:
    """Fold a literal-valued expression, or return None when it is not literal.

    ``constants`` are the module-level string bindings of the file being read,
    so a pattern assembled out of same-file parts is judged on its folded value.
    Only module-level bindings are trusted, and only to a bounded depth: a
    function-local re-binding of the same name is deliberately not folded, and
    lands in the unfoldable declaration below instead of silently folding to the
    wrong value.
    """

    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
                continue
            return None
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = fold_string(node.left, constants, depth)
        right = fold_string(node.right, constants, depth)
        return None if left is None or right is None else left + right
    if isinstance(node, ast.Name) and depth < FOLD_DEPTH_LIMIT:
        return constants.get(node.id)
    return None


def untrusted_names(tree: ast.Module) -> set[str]:
    """Names this scan will not fold: anything re-bound or shadowed anywhere.

    A module constant whose name is also a parameter, a function, an import or a
    second assignment anywhere in the file cannot be trusted to hold one value,
    so a pattern built from it becomes an unfoldable site that has to be
    declared.  Folding it anyway is how a second owner hides.
    """

    module_targets: dict[str, int] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            if isinstance(node.targets[0], ast.Name):
                name = node.targets[0].id
                module_targets[name] = module_targets.get(name, 0) + 1
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
            module_targets[name] = module_targets.get(name, 0) + 1

    untrusted = {name for name, count in module_targets.items() if count > 1}
    for node in ast.walk(tree):
        if isinstance(node, ast.arg):
            untrusted.add(node.arg)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            untrusted.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            untrusted.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            if node.id not in module_targets:
                untrusted.add(node.id)
    return untrusted


def module_level_string_constants(tree: ast.Module) -> dict[str, str]:
    """Name -> folded value for module-level string constants, resolved bounded."""

    trusted = untrusted_names(tree)
    pending: list[tuple[str, ast.AST]] = []
    constants: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if (
                isinstance(target, ast.Name)
                and isinstance(target.id, str)
                and target.id not in trusted
            ):
                pending.append((target.id, node.value))
    for _ in range(FOLD_DEPTH_LIMIT):
        unresolved: list[tuple[str, ast.AST]] = []
        for name, value in pending:
            folded = fold_string(value, {k: v for k, v in constants.items() if k != name})
            if folded is None:
                unresolved.append((name, value))
            else:
                constants[name] = folded
        pending = unresolved
        if not pending:
            break
    return constants


def regex_pattern_argument(node: ast.Call) -> ast.AST | None:
    """Return this call's pattern argument when it builds or applies a regex."""

    func = node.func
    if isinstance(func, ast.Attribute):
        holder = func.value
        if not (isinstance(holder, ast.Name) and holder.id == _RE_MODULE):
            return None
        return func.attr if func.attr in _REGEX_METHODS else None
    if isinstance(func, ast.Name):
        # Covers ``from re import fullmatch`` style call sites.
        return func.id if func.id in _REGEX_METHODS else None
    return None


def regex_constructions(tree: ast.Module) -> list[ast.Call]:
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and node.args and regex_pattern_argument(node):
            calls.append(node)
    return calls


def shape_rows_in_source(source: str, name: str) -> list[dict[str, object]]:
    """Every whole-value identifier shape decided inside ``source``."""

    if not any(token in source for token in PRESCREEN_TOKENS):
        return []
    tree = ast.parse(source)
    constants = module_level_string_constants(tree)
    rows: list[dict[str, object]] = []
    for node in regex_constructions(tree):
        pattern = fold_string(node.args[0], constants)
        if pattern is None:
            continue
        body = pattern.removeprefix("^").removesuffix("$")
        identifier = next(
            (key for key, shape in WHOLE_VALUE_BODIES.items() if shape == body), None
        )
        if identifier is None:
            continue
        rows.append(
            {
                "file": name,
                "line": getattr(node, "lineno", 0),
                "identifier": identifier,
                "anchored": body != pattern,
            }
        )
    return sorted(rows, key=lambda row: (str(row["file"]), int(row["line"])))


def unfoldable_rows_in_source(source: str, name: str) -> list[dict[str, object]]:
    """Regex constructions in ``source`` whose pattern value cannot be folded."""

    if not any(token in source for token in PRESCREEN_TOKENS):
        return []
    if not any(token in source for token in IDENTIFIER_RELEVANCE_TOKENS):
        return []
    tree = ast.parse(source)
    constants = module_level_string_constants(tree)
    return [
        {"file": name, "line": node.lineno, "unfoldable": True}
        for node in regex_constructions(tree)
        if fold_string(node.args[0], constants) is None
    ]


def collect_source_rows(root: pathlib.Path) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        pairs.append((path.relative_to(REPO_ROOT).as_posix(), source))
    return pairs


def collect_shape_definitions(root: pathlib.Path) -> list[dict[str, object]]:
    return [
        row
        for name, source in collect_source_rows(root)
        for row in shape_rows_in_source(source, name)
    ]


def collect_unfoldable_definitions(root: pathlib.Path) -> list[dict[str, object]]:
    return [
        row
        for name, source in collect_source_rows(root)
        for row in unfoldable_rows_in_source(source, name)
    ]


def offender_rows(
    definitions: list[dict[str, object]],
    *,
    declared: dict[str, int] | None = None,
) -> list[str]:
    """Violations of the one-owner rule, as readable strings."""

    budget = DECLARED_INDIVIDUAL_SITES if declared is None else declared
    rows: list[str] = []
    counted: dict[str, int] = {}
    for item in definitions:
        file = str(item["file"])
        identifier = str(item["identifier"])
        anchored = bool(item["anchored"])
        if file in budget:
            counted[file] = counted.get(file, 0) + 1
            continue
        spelling = "whole-value" if anchored else "search"
        if file != OWNER_MODULE:
            rows.append(f"{file} restates the {identifier} {spelling} shape")
    for file, allowed in budget.items():
        found = counted.get(file, 0)
        if found != allowed:
            rows.append(
                f"{file} declares {allowed} individual shape site(s), found {found}"
            )
    return sorted(set(rows))


def unfoldable_offender_rows(
    definitions: list[dict[str, object]],
    *,
    declared: dict[str, int] | None = None,
) -> list[str]:
    budget = DECLARED_UNFOLDABLE_SITES if declared is None else declared
    counted: dict[str, int] = {}
    rows: list[str] = []
    for item in definitions:
        file = str(item["file"])
        if file in budget:
            counted[file] = counted.get(file, 0) + 1
            continue
        rows.append(
            f"{file}:{item['line']} builds a Lark identifier regex from a value "
            "this scan cannot fold; declare it or route the site to the owner"
        )
    for file, allowed in budget.items():
        found = counted.get(file, 0)
        if found != allowed:
            rows.append(
                f"{file} declares {allowed} unfoldable site(s), found {found}"
            )
    return sorted(set(rows))


def module_level_bindings(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names.update(
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def references(tree: ast.Module, name: str) -> bool:
    """True when ``name`` is read outside its own import clause.

    An import binds through ``ast.alias``, never ``ast.Name``, so a plain Name
    walk already ignores it.
    """

    return any(
        isinstance(node, ast.Name) and node.id == name for node in ast.walk(tree)
    )


def parse_module(module: ModuleType) -> ast.Module:
    path = pathlib.Path(str(module.__file__)).resolve()
    return ast.parse(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# 1. one owner per shape family, across the whole product tree
# --------------------------------------------------------------------------


def test_no_module_restates_a_lark_identifier_shape() -> None:
    assert offender_rows(collect_shape_definitions(REPO_ROOT / "loopx")) == []


def test_no_lark_module_hides_an_unfoldable_identifier_construction() -> None:
    # The unfoldable layer is bounded to the package that owns these shapes and
    # to modules whose text mentions an identifier.  Spread wider it reports
    # dozens of regexes built from data (credential alternations, setup URLs,
    # markdown headings) that answer a different question, and a declaration list
    # that wide is noise nobody keeps current.
    assert unfoldable_offender_rows(collect_unfoldable_definitions(LARK_PACKAGE)) == []


def test_unfoldable_probes_are_forced_into_the_open() -> None:
    probes = (
        'import re\n\n\ndef check(prefix, chat_id):\n'
        "    return re.compile(prefix + chat_id)\n",
        'import re\n\nCHAT_ID = "^oc_[A-Za-z0-9_-]+$"\n\n\ndef pick(CHAT_ID):\n'
        "    return re.compile(CHAT_ID)\n",
    )
    for source in probes:
        rows = unfoldable_rows_in_source(source, "loopx/extensions/lark/probe.py")
        assert rows, source
        assert shape_rows_in_source(source, "loopx/extensions/lark/probe.py") == []
        assert unfoldable_offender_rows(rows, declared={}) != []


def test_a_foldable_probe_is_not_also_reported_as_unfoldable() -> None:
    source = 'import re\n\nHEAD = "^oc_"\nTAIL = "[A-Za-z0-9_-]+$"\n'
    source += "CHAT = re.compile(HEAD + TAIL)\n"
    name = "loopx/extensions/lark/probe.py"
    assert shape_rows_in_source(name and source, name)
    assert unfoldable_rows_in_source(source, name) == []


def test_prescreen_still_leaves_the_scans_two_call_forms_reachable() -> None:
    # The prescreen is load-bearing: a token list that misses a call form makes
    # the scan blind to it.  Both accepted forms are probed here.
    for source in (
        'import re\n\nX = re.compile(r"^oc_[A-Za-z0-9_-]+$")\n',
        'from re import fullmatch\n\nX = fullmatch(r"^om_[A-Za-z0-9_-]+$", "om_x")\n',
    ):
        assert shape_rows_in_source(source, "loopx/extensions/lark/probe.py")

    def _bodies() -> list[str]:
        return list(WHOLE_VALUE_BODIES.values())

    assert _bodies()[0] in WHOLE_VALUE_BODIES.values()


def test_owner_states_each_shape_as_the_recorded_whole_value_body() -> None:
    # Stated against literals on purpose: if the owner renames or re-forms a
    # shape, this fails instead of the scan quietly following the new spelling.
    assert identity_shapes.LARK_EVENT_ID_PATTERN.pattern == (
        "^" + WHOLE_VALUE_BODIES["event_id"] + "$"
    )
    assert identity_shapes.LARK_MESSAGE_ID_PATTERN.pattern == (
        "^" + WHOLE_VALUE_BODIES["message_id"] + "$"
    )
    assert identity_shapes.LARK_CHAT_ID_PATTERN.pattern == (
        "^" + WHOLE_VALUE_BODIES["chat_id"] + "$"
    )
    assert identity_shapes.LARK_OPEN_ID_PATTERN.pattern == (
        "^" + WHOLE_VALUE_BODIES["operator_id"] + "$"
    )


def test_the_owner_is_the_only_module_that_defines_any_of_them() -> None:
    rows = collect_shape_definitions(REPO_ROOT / "loopx")
    assert {str(row["file"]) for row in rows} == {OWNER_MODULE, *DECLARED_INDIVIDUAL_SITES}


def test_owner_states_both_spellings_and_nothing_else() -> None:
    rows = [item for item in collect_shape_definitions(LARK_PACKAGE) if item["file"] == OWNER_MODULE]
    anchored = {str(row["identifier"]) for row in rows if row["anchored"]}
    searched = {str(row["identifier"]) for row in rows if not row["anchored"]}
    assert anchored == set(ANCHORED_EXPORTS)
    assert searched == SEARCH_IDENTIFIERS
    assert len(rows) == len(ANCHORED_EXPORTS) + len(SEARCH_EXPORTS)
    for export in ANCHORED_EXPORTS.values():
        pattern = getattr(identity_shapes, export)
        body = pattern.pattern[1:-1]
        assert pattern.pattern.startswith("^") and pattern.pattern.endswith("$")
        assert body in WHOLE_VALUE_BODIES.values(), export
    for export in SEARCH_EXPORTS.values():
        pattern = getattr(identity_shapes, export)
        assert pattern.pattern in WHOLE_VALUE_BODIES.values(), export


@pytest.mark.parametrize("hub", [TRANSPORT_HUB, INBOX_HUB])
def test_a_former_hub_defines_no_shape_of_its_own(hub: str) -> None:
    rows = [item for item in collect_shape_definitions(LARK_PACKAGE) if item["file"] == hub]
    assert rows == [], f"{hub} still decides an identifier shape locally"


# --------------------------------------------------------------------------
# 2. consumers hold the owner's object and actually apply it
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "entry"), sorted(SHAPE_CONSUMERS.items()), ids=sorted(SHAPE_CONSUMERS)
)
def test_shape_consumer_is_wired_to_the_owner(path: str, entry: tuple) -> None:
    module, attribute = entry
    tree = parse_module(module)
    assert references(tree, attribute), f"{path} never applies {attribute}"
    assert attribute not in module_level_bindings(tree), (
        f"{path} binds {attribute} locally instead of importing the owner"
    )
    assert getattr(module, attribute) is getattr(identity_shapes, attribute)


def test_each_hub_reexports_the_spelling_its_callers_need() -> None:
    # The transport's two search() call sites need the unanchored form; every
    # inbox caller applies fullmatch, so the inbox hands on the whole-value one.
    assert goal_channel_transport.CHAT_ID_PATTERN is identity_shapes.LARK_CHAT_ID_SEARCH
    assert goal_channel_transport.MESSAGE_ID_PATTERN is (
        identity_shapes.LARK_MESSAGE_ID_SEARCH
    )
    assert goal_channel_transport.OPEN_ID_PATTERN is identity_shapes.LARK_OPEN_ID_SEARCH
    assert event_inbox.CHAT_ID_PATTERN is identity_shapes.LARK_CHAT_ID_PATTERN
    assert event_inbox.MESSAGE_ID_PATTERN is identity_shapes.LARK_MESSAGE_ID_PATTERN
    assert (
        goal_channel_transport.CHAT_ID_PATTERN
        is not identity_shapes.LARK_CHAT_ID_PATTERN
    )


def test_hub_reexports_are_named_by_alias_import_not_local_compile() -> None:
    for path, (module, attributes) in sorted(HUB_REEXPORTS.items()):
        rows = shape_rows_in_source(
            pathlib.Path(str(module.__file__)).resolve().read_text("utf-8"), path
        )
        assert rows == [], path


def test_inbox_callers_still_receive_one_object_through_the_chain() -> None:
    for name in ("manager_reply_delivery", "turn_start_sync", "inbox_reply"):
        module = __import__(f"loopx.extensions.lark.{name}", fromlist=["MESSAGE_ID_PATTERN"])
        assert module.MESSAGE_ID_PATTERN is identity_shapes.LARK_MESSAGE_ID_PATTERN, name


def test_callback_validators_do_not_restate_the_identity_table() -> None:
    for module in (goal_channel_operation, team_plan_confirmation):
        tree = parse_module(module)
        tables = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Tuple)
            and any(
                isinstance(element, ast.Constant) and element.value == "operator_id"
                for element in node.elts
            )
        ]
        assert tables == [], f"{module.__name__} restates the callback table"


def test_neither_callback_validator_recompiles_a_shape() -> None:
    for module in (goal_channel_operation, team_plan_confirmation):
        source = pathlib.Path(str(module.__file__)).resolve().read_text("utf-8")
        assert shape_rows_in_source(source, module.__name__) == []


# --------------------------------------------------------------------------
# 3. the owner's decision
# --------------------------------------------------------------------------

VALID_IDS = {
    "event_id": "evt:1",
    "message_id": "om_control_public_fixture",
    "chat_id": "oc_abc-123",
    "operator_id": "ou_ABC_9",
}


def _event(**overrides: object) -> dict[str, object]:
    event: dict[str, object] = dict(VALID_IDS)
    event.update(overrides)
    return event


def test_callback_table_names_the_four_fields_in_validation_order() -> None:
    assert identity_shapes.CARD_CALLBACK_IDENTITY_SHAPES == (
        ("event_id", identity_shapes.LARK_EVENT_ID_PATTERN),
        ("message_id", identity_shapes.LARK_MESSAGE_ID_PATTERN),
        ("chat_id", identity_shapes.LARK_CHAT_ID_PATTERN),
        ("operator_id", identity_shapes.LARK_OPEN_ID_PATTERN),
    )


def test_require_identity_accepts_a_whole_identifier_per_field() -> None:
    for field, value in VALID_IDS.items():
        identity_shapes.require_card_callback_identity(
            _event(**{field: value}), error_prefix="operation callback"
        )


def test_longest_accepted_event_id_is_240_characters() -> None:
    identity_shapes.require_card_callback_identity(
        _event(event_id="a" * 240), error_prefix="operation callback"
    )
    with pytest.raises(ValueError):
        identity_shapes.require_card_callback_identity(
            _event(event_id="a" * 241), error_prefix="operation callback"
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("event_id", ""),
        ("event_id", "id with space"),
        ("event_id", "id/slash"),
            ("message_id", "om_"),
        ("message_id", "prefix_om_real"),
        ("message_id", "oc_not_a_message"),
        ("message_id", "om_ dot"),
        ("chat_id", "oc_"),
        ("chat_id", "oc_not+plus"),
        ("operator_id", "ou_"),
        ("operator_id", "om_not_an_operator"),
    ],
)
def test_require_identity_rejects_values_that_are_not_whole_ids(
    field: str, value: str
) -> None:
    with pytest.raises(ValueError) as caught:
        identity_shapes.require_card_callback_identity(
            _event(**{field: value}), error_prefix="team plan callback"
        )
    assert caught.value.args[0] == f"team plan callback {field} is invalid"


def test_missing_identity_field_is_rejected() -> None:
    event = dict(VALID_IDS)
    del event["chat_id"]
    with pytest.raises(ValueError) as caught:
        identity_shapes.require_card_callback_identity(
            event, error_prefix="operation callback"
        )
    assert caught.value.args[0] == "operation callback chat_id is invalid"


def test_first_offending_field_is_reported_and_validation_is_ordered() -> None:
    with pytest.raises(ValueError) as caught:
        identity_shapes.require_card_callback_identity(
            _event(message_id="bad", chat_id="bad", operator_id="bad"),
            error_prefix="operation callback",
        )
    assert caught.value.args[0] == "operation callback message_id is invalid"


@pytest.mark.parametrize(
    "value",
    ["om_ok1", "om_ok1\n", "\nom_ok1", "om_a\nb", "", "om_", "om_\u00e9", "om1\n\n"],
)
def test_anchored_and_unanchored_fullmatch_agree_on_every_tricky_value(
    value: str,
) -> None:
    """Why this convergence cannot change a product answer.

    Every site this slice replaced applies ``fullmatch``, where the anchors are
    redundant: ``re.fullmatch`` already requires the whole string, so the owner's
    anchored spelling and the transport's unanchored extraction spelling accept
    and reject exactly the same values.  Only the ``search()`` sites, which stay
    with the transport, read a difference.
    """

    anchored = bool(identity_shapes.LARK_MESSAGE_ID_PATTERN.fullmatch(value))
    unanchored = bool(identity_shapes.LARK_MESSAGE_ID_SEARCH.fullmatch(value))
    assert anchored == unanchored
    accepted = True
    try:
        identity_shapes.require_card_callback_identity(
            _event(message_id=value), error_prefix="operation callback"
        )
    except ValueError:
        accepted = False
    assert accepted == anchored


# --------------------------------------------------------------------------
# 4. the scan sees the spellings a future edit will actually use
# --------------------------------------------------------------------------

OFFENDING_SNIPPETS: tuple[tuple[str, str], ...] = (
    (
        "module level anchored compile",
        'import re\n\nCHAT = re.compile(r"^oc_[A-Za-z0-9_-]+$")\n',
    ),
    (
        "module level unanchored compile",
        'import re\n\nCHAT = re.compile(r"oc_[A-Za-z0-9_-]+")\n',
    ),
    (
        "literal concatenated from a same-file constant",
        'import re\n\nHEAD = "^oc_"\nTAIL = "[A-Za-z0-9_-]+$"\n'
        "CHAT = re.compile(HEAD + TAIL)\n",
    ),
    (
        "inline fullmatch against the event id body",
        'import re\n\n\ndef check(value):\n'
        '    return re.fullmatch(r"[A-Za-z0-9._:-]{1,240}", value)\n',
    ),
    (
        "fullmatch imported straight from re",
        'from re import fullmatch\n\n'
        'CHECKED = fullmatch(r"^om_[A-Za-z0-9_-]+$", "om_x")\n',
    ),
    (
        "non-raw literal with the same body",
        'import re\n\nCHAT = re.compile("^oc_[A-Za-z0-9_-]+$")\n',
    ),
    (
        "concatenated through a module-level constant chain",
        'import re\n\n_PREFIX = "^ou_"\n_BODY = "[A-Za-z0-9_-]+$"\n'
        "OPERATOR = re.compile(_PREFIX + _BODY)\n",
    ),
    (
        "operator id restated under an unrelated name",
        'import re\n\nOPERATOR = re.compile(r"^ou_[A-Za-z0-9_-]+$")\n',
    ),
    (
        "event id inside a function body, not at module level",
        'import re\n\n\ndef pick(value):\n'
        '    pattern = re.compile(r"^ou_[A-Za-z0-9_-]+$")\n'
        "    return pattern.match(value)\n",
    ),
)
NON_OFFENDING_SNIPPETS: tuple[tuple[str, str], ...] = (
    (
        "a goal id shape, which is a different decision",
        'import re\n\nGOAL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")\n',
    ),
    (
        "a chat id embedded in a larger route grammar",
        'import re\n\nROUTE = re.compile(r"^route:oc_[A-Za-z0-9_-]+:v1$")\n',
    ),
    (
        "an app id, which this slice does not own",
        'import re\n\nAPP = re.compile(r"^cli_[A-Za-z0-9_-]+$")\n',
    ),
    (
        "a pattern built from runtime data, which cannot be folded",
        'import re\n\nCHAT = re.compile(prefix + "[A-Za-z0-9_-]+")\n',
    ),
    (
        "a module constant rebound locally before use, which is not trusted",
        'import re\n\nCHAT_ID = "^oc_[A-Za-z0-9_-]+$"\n\n\ndef pick(CHAT_ID):\n'
        "    return re.compile(CHAT_ID)\n",
    ),
    (
        "an id-shaped data value matched by another module\'s pattern",
        'import re\n\nMATCHED = re.compile(r"^x+$").fullmatch("oc_abc")\n',
    ),
)


@pytest.mark.parametrize(
    ("label", "source"), OFFENDING_SNIPPETS, ids=[row[0] for row in OFFENDING_SNIPPETS]
)
def test_the_scan_reports_a_restated_shape(label: str, source: str) -> None:
    rows = shape_rows_in_source(source, "loopx/extensions/lark/probe.py")
    assert rows, label
    assert offender_rows(rows, declared={}) != [], label


@pytest.mark.parametrize(
    ("label", "source"),
    NON_OFFENDING_SNIPPETS,
    ids=[row[0] for row in NON_OFFENDING_SNIPPETS],
)
def test_the_scan_leaves_a_different_decision_alone(
    label: str, source: str
) -> None:
    assert shape_rows_in_source(source, "loopx/extensions/lark/probe.py") == []


def test_declared_site_count_is_enforced_in_both_directions() -> None:
    rows = collect_shape_definitions(LARK_PACKAGE)
    declared_file = "loopx/extensions/lark/event_collector_runtime.py"
    assert any(item["file"] == declared_file for item in rows)
    assert offender_rows(rows) == []
    retired = [item for item in rows if item["file"] != declared_file]
    assert offender_rows(retired) == [
        f"{declared_file} declares 1 individual shape site(s), found 0"
    ]


def test_a_converted_declared_site_is_not_silently_reintroduced() -> None:
    declared_file = "loopx/extensions/lark/event_collector_runtime.py"
    converted = [
        {
            "file": declared_file,
            "line": 145,
            "identifier": "event_id",
            "anchored": False,
        },
        {
            "file": "loopx/extensions/lark/other_module.py",
            "line": 9,
            "identifier": "event_id",
            "anchored": True,
        },
    ]
    assert offender_rows(converted) == [
        "loopx/extensions/lark/other_module.py restates the event_id whole-value shape"
    ]
