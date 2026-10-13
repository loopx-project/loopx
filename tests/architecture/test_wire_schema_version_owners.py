"""Guard one owner per wire-version vocabulary for four schema decisions.

Each of these names answers exactly one question -- which document version a
payload carries -- and on the base revision each was stated more than once:

=========================================  ====================  =========================
decision                                   owner module          other modules that had it
==========================================  ====================  =========================
``TRIGGER_DECISION_SCHEMA``                 periodic_report core  triggers, periodic_report cli, catalog_entry
``DELIVERY_INTENT_SCHEMA``                  periodic_report bindings  lark miaoda_report, lark periodic_report_delivery, catalog_entry
``SNAPSHOT_SCHEMA_VERSION``                 issue_fix repository_snapshot  issue_fix metrics_projection, issue_fix cli (help text)
``REQUEST_SCHEMA`` / ``RESPONSE_SCHEMA``    semantic_preference contract  openviking provider, catalog_entry
=========================================  ====================  =========================

Two of those rows are the reason this guard scans twice.  ``periodic_report/cli.py``
compared ``payload["schema_version"]`` against a bare string literal and
``issue_fix/cli.py`` embedded the same value in argparse help text, so a scan
that groups module-level constants by name reports neither.  The guard therefore
runs a binding scan (module-level assignments of the guarded name) over all of
``loopx/`` and a separate literal census (the folded value itself), and any site
the census finds outside an owner has to be declared with its file and count.

Strings are interned, so ``consumer.NAME is owner.NAME`` proves nothing about
where a value is written -- that is what the binding scan is for.  The identity
asserts below are kept only for their wiring signal: a consumer that imports the
owner keeps answering through the owner's value.

Neighbours deliberately not merged, each pinned by a test: ``REQUEST_SCHEMA`` is
a *conflicting-value* name -- four other modules carry that same name with
different documents' values -- so the group's definition count drops here while
its name count must not.  ``PROJECTION_SCHEMA_VERSION`` shares a prefix with the
snapshot name but versions a different document.
"""

from __future__ import annotations

import argparse
import ast
import pathlib
from types import ModuleType

import pytest

from loopx.capabilities.issue_fix import cli as issue_fix_cli
from loopx.capabilities.issue_fix import metrics_projection, repository_snapshot
from loopx.capabilities.issue_fix.cli import register_issue_fix_commands
from loopx.capabilities.periodic_report import bindings, triggers
from loopx.capabilities.periodic_report import catalog_entry as periodic_catalog
from loopx.capabilities.periodic_report import cli as periodic_cli
from loopx.capabilities.periodic_report import core as periodic_core
from loopx.capabilities.semantic_preference import catalog_entry as sp_catalog
from loopx.capabilities.semantic_preference import contract as sp_contract
from loopx.control_plane.handoff import review_batch
from loopx.extensions import openviking_semantic_preference
from loopx.extensions.lark import miaoda_report, periodic_report_delivery
from loopx.extensions.openviking_periodic_report import (
    provider as openviking_report_provider,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "loopx"

# name -> (owner path, owner module, folded value, [(consumer path, consumer module)])
DECISIONS: dict[str, tuple[str, ModuleType, str, list[tuple[str, ModuleType]]]] = {
    "TRIGGER_DECISION_SCHEMA": (
        "loopx/capabilities/periodic_report/core.py",
        periodic_core,
        "periodic_report_trigger_decision_v0",
        [
            ("loopx/capabilities/periodic_report/triggers.py", triggers),
            ("loopx/capabilities/periodic_report/cli.py", periodic_cli),
            ("loopx/capabilities/periodic_report/catalog_entry.py", periodic_catalog),
        ],
    ),
    "DELIVERY_INTENT_SCHEMA": (
        "loopx/capabilities/periodic_report/bindings.py",
        bindings,
        "periodic_report_delivery_intent_v0",
        [
            ("loopx/extensions/lark/miaoda_report.py", miaoda_report),
            ("loopx/extensions/lark/periodic_report_delivery.py", periodic_report_delivery),
            ("loopx/capabilities/periodic_report/catalog_entry.py", periodic_catalog),
        ],
    ),
    "SNAPSHOT_SCHEMA_VERSION": (
        "loopx/capabilities/issue_fix/repository_snapshot.py",
        repository_snapshot,
        "issue_fix_repository_reporting_snapshot_v0",
        [
            ("loopx/capabilities/issue_fix/metrics_projection.py", metrics_projection),
            ("loopx/capabilities/issue_fix/cli.py", issue_fix_cli),
        ],
    ),
    "REQUEST_SCHEMA": (
        "loopx/capabilities/semantic_preference/contract.py",
        sp_contract,
        "semantic_preference_provider_request_v0",
        [
            (
                "loopx/extensions/openviking_semantic_preference/provider.py",
                openviking_semantic_preference.provider,
            ),
            ("loopx/capabilities/semantic_preference/catalog_entry.py", sp_catalog),
        ],
    ),
    "RESPONSE_SCHEMA": (
        "loopx/capabilities/semantic_preference/contract.py",
        sp_contract,
        "semantic_preference_provider_response_v0",
        [
            (
                "loopx/extensions/openviking_semantic_preference/provider.py",
                openviking_semantic_preference.provider,
            ),
        ],
    ),
}

# Folded value -> (file, count) for sites this guard reports but does not convert.
DECLARED_LITERAL_SITES: dict[str, dict[str, int]] = {
    # `reward_memory/registry.py` stamps a packet field named
    # `source_inventory_schema` with the provider *response* document name.  The
    # field is written and never read anywhere in the tree, so folding it here
    # would endorse a second field carrying this vocabulary instead of settling
    # whether that field is even the right one -- an owner call, not a dedup.
    "semantic_preference_provider_response_v0": {
        "loopx/capabilities/reward_memory/registry.py": 1,
    },
}


def module_source(rel_path: str) -> str:
    return (REPO_ROOT / rel_path).read_text(encoding="utf-8")


def _analyse(source: str, names: frozenset[str]):
    """Derive named bindings and string sites without retaining syntax trees."""

    tree = ast.parse(source)
    bindings = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = frozenset(t.id for t in node.targets if isinstance(t, ast.Name))
            if targets & names:
                bindings.append((node.lineno, targets, ast.unparse(node.value)))
    strings = [(node.lineno, node.value) for node in ast.walk(tree)
               if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    return bindings, strings


def _binding_lines(bindings, name: str, value: str | None):
    return [line for line, names, expression in bindings
            if name in names and (value is None or expression == repr(value))]


def _sites(analysis, name: str, value: str | None):
    bindings, strings = analysis
    named = _binding_lines(bindings, name, value)
    inline = [] if value is None else [line for line, text in strings if value in text]
    return named, inline


def module_level_binds(source: str, name: str, value: str | None = None) -> list[int]:
    """Lines where this module restates the owner's *value* under this name.

    Matching is on the folded value, never on the spelling: ``REQUEST_SCHEMA`` is
    a legitimate module-local name in four other documents, and a scan that only
    looked at the name would report those as duplicates and invite someone to
    "collapse" values that are deliberately different.  ``value=None`` keeps the
    name-only view used by the neighbour probes.
    """

    return _binding_lines(_analyse(source, frozenset({name}))[0], name, value)


def literal_sites(source: str, value: str) -> list[int]:
    """Lines whose string constants state this document version.

    Substring rather than equality, because a restatement can hide in prose --
    argparse help text did exactly that before this guard's own change folded it
    into an f-string, and an f-string interpolation is not a literal at all.
    """

    return _sites(_analyse(source, frozenset()), "", value)[1]


def _repository_scan():
    bindings = {name: [] for name in DECISIONS}
    sites = {name: {} for name in DECISIONS}
    names = frozenset(DECISIONS)
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        analysis = _analyse(path.read_text(encoding="utf-8"), names)
        for name, (owner_path, _module, value, _consumers) in DECISIONS.items():
            if rel == owner_path:
                continue
            named, inline = _sites(analysis, name, value)
            bindings[name].extend(f"{rel}:{line} restates {name}={value!r}" for line in named)
            if inline:
                sites[name][rel] = inline
    result = {}
    for name, (_owner, _module, value, _consumers) in DECISIONS.items():
        declared = DECLARED_LITERAL_SITES.get(value, {})
        unexplained = [f"{rel}:{line} spells {value!r}"
                       for rel, lines in sites[name].items() if rel not in declared
                       for line in lines]
        for rel, count in declared.items():
            seen = len(sites[name].get(rel, []))
            if seen != count:
                unexplained.append(f"{rel} declares {count} site(s), found {seen}")
        result[name] = bindings[name], unexplained
    return result


def _repository_reader():
    # Cache only the completed module-test findings, never ASTs or raw source.
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


@pytest.mark.parametrize("name", sorted(DECISIONS))
def test_only_the_owner_module_defines_the_name(name: str, repository_findings) -> None:
    bindings_found, _ = repository_findings(name)
    assert bindings_found == [], "\n".join(bindings_found)


@pytest.mark.parametrize("name", sorted(DECISIONS))
def test_no_other_module_spells_the_value_out(name: str, repository_findings) -> None:
    _, literals = repository_findings(name)
    assert literals == [], "\n".join(literals)


@pytest.mark.parametrize("name", sorted(DECISIONS))
def test_the_owner_states_the_recorded_literal(name: str) -> None:
    owner_path, owner_module, value, _ = DECISIONS[name]
    assert getattr(owner_module, name) == value
    assert module_level_binds(module_source(owner_path), name), f"{owner_path} must define {name}"


@pytest.mark.parametrize("name", sorted(DECISIONS))
def test_each_consumer_reads_through_the_owner(name: str) -> None:
    owner_path, owner_module, value, consumers = DECISIONS[name]
    assert consumers, name
    assert getattr(owner_module, name) == value
    for rel, module in consumers:
        assert rel != owner_path, rel
        assert getattr(module, name) == value, rel
        assert module_level_binds(module_source(rel), name) == [], f"{rel} still binds {name}"
        assert literal_sites(module_source(rel), value) == [], f"{rel} still spells {value!r}"


def test_trigger_receipt_validation_still_refuses_a_foreign_document() -> None:
    """The owner's validator gates on its own constant, not a restated copy."""

    with pytest.raises(ValueError) as refused:
        periodic_core._normalize_trigger_receipt(
            {"schema_version": "periodic_report_trigger_decision_v1", "eligible": True}
        )
    assert str(refused.value) == (
        "trigger_receipt.schema_version must be "
        f"{periodic_core.TRIGGER_DECISION_SCHEMA!r}"
    )


def test_trigger_markdown_renders_for_the_owner_value() -> None:
    """`periodic_report/cli.py` branches on the owner's value, inline no more."""

    rendered = periodic_cli.render_periodic_report_markdown(
        {
            "ok": True,
            "schema_version": periodic_core.TRIGGER_DECISION_SCHEMA,
            "decision_id": "trig-1",
        }
    )
    assert rendered.splitlines()[0] == "# Periodic Report Trigger `trig-1`"
    assert module_level_binds(
        module_source("loopx/capabilities/periodic_report/cli.py"), "TRIGGER_DECISION_SCHEMA"
    ) == []


def test_snapshot_validation_and_help_text_share_the_owner_value() -> None:
    value = repository_snapshot.SNAPSHOT_SCHEMA_VERSION
    with pytest.raises(ValueError) as refused:
        metrics_projection._snapshot(
            {"schema_version": "issue_fix_repository_reporting_snapshot_v9"},
            expected_repo="loopx-project/loopx",
            role="baseline",
        )
    assert str(refused.value) == f"baseline snapshot must use {value}"

    action = issue_fix_metrics_action("--repository-baseline-json")
    assert action.help == f"Public-safe {value} at period start."
    current = issue_fix_metrics_action("--repository-current-json")
    assert current.help == (
        f"Public-safe {value} at current time, including flow_since_baseline."
    )


def issue_fix_metrics_action(flag: str) -> argparse.Action:
    """Build the real CLI and read the argparse action back out of it."""

    root = argparse.ArgumentParser(prog="loopx")
    subparsers = root.add_subparsers(dest="cmd")
    register_issue_fix_commands(subparsers, lambda parser: None)
    parser = subparsers.choices["issue-fix"]
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            parser = action.choices["metrics"]
            break
    else:
        raise AssertionError("issue-fix has no subcommands to reach metrics")
    for action in parser._actions:
        if flag in action.option_strings:
            return action
    raise AssertionError(f"issue-fix metrics no longer parses {flag}")


def test_projection_schema_version_is_a_different_document() -> None:
    """Same name prefix, different document: it must stay its own constant."""

    assert metrics_projection.PROJECTION_SCHEMA_VERSION == "issue_fix_metrics_projection_v0"
    assert metrics_projection.PROJECTION_SCHEMA_VERSION != (
        repository_snapshot.SNAPSHOT_SCHEMA_VERSION
    )
    assert module_level_binds(
        module_source("loopx/capabilities/issue_fix/metrics_projection.py"),
        "PROJECTION_SCHEMA_VERSION",
    )


def test_request_schema_name_keeps_its_other_documents() -> None:
    """`REQUEST_SCHEMA` is a conflicting-value name and must stay conflicting.

    Four modules own that name for four different documents.  This change only
    stopped the OpenViking semantic-preference provider from restating the
    semantic-preference contract's value, so the name count stays at 16 while
    the definition count drops by one.  Collapsing the name would be a rename,
    which the registry note forbids.
    """

    assert sp_contract.REQUEST_SCHEMA == "semantic_preference_provider_request_v0"
    others = {
        "loopx/capabilities/periodic_report/core.py": periodic_core.REQUEST_SCHEMA,
        "loopx/control_plane/handoff/review_batch.py": review_batch.REQUEST_SCHEMA,
        "loopx/extensions/openviking_periodic_report/provider.py": (
            openviking_report_provider.REQUEST_SCHEMA
        ),
    }
    assert all(value != sp_contract.REQUEST_SCHEMA for value in others.values()), others
    assert len(set(others.values())) == 3, others
    assert literal_sites(
        module_source("loopx/capabilities/reward_memory/registry.py"),
        sp_contract.RESPONSE_SCHEMA,
    ) == [601], "the declared site moved; update the declaration with its reason"


@pytest.mark.parametrize(
    ("label", "source", "reported_as"),
    [
        (
            "restated constant",
            'TRIGGER_DECISION_SCHEMA = "periodic_report_trigger_decision_v0"\n',
            "both",
        ),
        (
            "inline comparison",
            (
                "def render(payload):\n"
                '    return payload.get("schema_version") == "periodic_report_trigger_decision_v0"\n'
            ),
            "literal",
        ),
        (
            "inline help text",
            (
                "add_argument(\n"
                '    help="Public-safe issue_fix_repository_reporting_snapshot_v0 here"\n'
                ")\n"
            ),
            "literal",
        ),
        (
            "provider restates the contract response",
            'RESPONSE_SCHEMA = "semantic_preference_provider_response_v0"\n',
            "both",
        ),
    ],
    ids=["constant", "comparison", "help", "provider-copy"],
)
def test_the_scan_reports_a_restated_document(
    label: str, source: str, reported_as: str
) -> None:
    binding_hits = [
        name for name in DECISIONS if module_level_binds(source, name)
    ]
    literal_hits = [
        name for name, (_o, _m, folded, _c) in DECISIONS.items() if literal_sites(source, folded)
    ]
    if reported_as == "both":
        assert binding_hits, label
    assert literal_hits, label


@pytest.mark.parametrize(
    ("label", "source"),
    [
        ("import from owner", "from .core import TRIGGER_DECISION_SCHEMA\n"),
        (
            "folded into an f-string",
            'add_argument(help=f"Public-safe {SNAPSHOT_SCHEMA_VERSION} here")\n',
        ),
        ("a different document's version", 'PROJECTION_SCHEMA_VERSION = "issue_fix_metrics_projection_v0"\n'),
    ],
    ids=["import", "fstring", "other-document"],
)
def test_the_scan_leaves_a_folded_consumer_alone(label: str, source: str) -> None:
    assert [name for name in DECISIONS if module_level_binds(source, name)] == [], label
    assert [
        name for name, (_o, _m, folded, _c) in DECISIONS.items() if literal_sites(source, folded)
    ] == [], label


@pytest.mark.parametrize("source,owner,named,inline", [
    ('REQUEST_SCHEMA = "semantic_preference_provider_request_v0"\n', False, 1, 1),
    ('help = "Public-safe semantic_preference_provider_request_v0 here"\n', False, 0, 1),
    ('REQUEST_SCHEMA = "a_different_document_v0"\n', False, 0, 0),
    ('REQUEST_SCHEMA = "semantic_preference_provider_request_v0"\n', True, 0, 0),
])
def test_repository_findings_preserve_refusals_and_retry(tmp_path, monkeypatch, source, owner, named, inline):
    monkeypatch.setitem(globals(), "REPO_ROOT", tmp_path)
    monkeypatch.setitem(globals(), "PACKAGE_ROOT", tmp_path / "loopx")
    declared = tmp_path / "loopx/capabilities/reward_memory/registry.py"
    declared.parent.mkdir(parents=True)
    declared.write_text(f"SCHEMA = {DECISIONS['RESPONSE_SCHEMA'][2]!r}\n")
    path = tmp_path / (DECISIONS["REQUEST_SCHEMA"][0] if owner else "loopx/extra.py")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("BROKEN = (\n")
    read = _repository_reader()
    with pytest.raises(SyntaxError):
        read("REQUEST_SCHEMA")
    path.write_text(source)
    bindings, sites = read("REQUEST_SCHEMA")
    assert len(bindings) == named
    assert len(sites) == inline
    bindings.clear()
    sites.clear()
    assert tuple(map(len, read("REQUEST_SCHEMA"))) == (named, inline)
    assert read("RESPONSE_SCHEMA") == ([], [])
    if owner:
        declared.write_text("")
        _, rejected = _repository_reader()("RESPONSE_SCHEMA")
        assert rejected == [
            "loopx/capabilities/reward_memory/registry.py declares 1 site(s), found 0"
        ]
