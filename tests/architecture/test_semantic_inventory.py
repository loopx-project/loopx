"""Semantics of the repository-wide vocabulary inventory scanner.

The inventory is the map behind the semantic vocabulary registry. These tests
pin the classification rules from the RFC rather than from scanner output:
which carriers count as closed sets, how duplicate constants split into
cross-runtime twins, same-runtime forks, and conflicting values, and that the
inventory is computed from the whole tracked tree without a committed snapshot.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from loopx.semantics.inventory import (
    multi_value_name_collisions,
    INVENTORY_SCHEMA_VERSION,
    SourceFile,
    build_inventory,
    load_sources,
    merge_candidate_groups,
    python_facts,
    registered_owner_symbol_sets,
    render_inventory,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _write(
        tmp_path,
        "loopx/a.py",
        'from enum import Enum\n'
        'from typing import Literal\n'
        'class Kind(str, Enum):\n    ONE = "one"\n    TWO = "two"\n    _private = "ignored"\n'
        'class Mixed(Enum):\n    A = "a"\n    B = 2\n'
        'STATES = frozenset({"open", "closed"})\n'
        'SINGLE = ("only",)\n'
        'NOT_STRINGS = (1, 2)\n'
        'Mode = Literal["fast", "slow"]\n'
        'FOO_SCHEMA_VERSION = "foo_v0"\n'
        'SHARED = "same"\n'
        'CLASHING = "left"\n',
    )
    _write(
        tmp_path,
        "loopx/b.py",
        'SHARED = "same"\n'
        'CLASHING = "right"\n'
        'FOO_SCHEMA_VERSION = "foo_v0"\n'
        'PAIRED_SCHEMA_VERSION = "paired_v0"\n',
    )
    _write(
        tmp_path,
        "loopx/b.ts",
        'export const KINDS = ["one", "two"] as const;\n'
        'export const PAIRED_SCHEMA_VERSION = "paired_v0";\n'
        'export const NOT_CLOSED = ["x"];\n',
    )
    _write(tmp_path, "loopx/__pycache__/junk.py", 'IGNORED = ("a", "b")\n')
    _write(tmp_path, "loopx/node_modules/dep.ts", 'export const IGNORED = ["a", "b"] as const;\n')
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "loopx/a.py", "loopx/b.py", "loopx/b.ts"],
        check=True,
    )
    return tmp_path


def test_python_carriers_are_classified_by_shape(repo: Path) -> None:
    inventory = build_inventory(repo)
    enums = {entry["name"]: entry["values"] for entry in inventory["python_enums"]}
    assert enums == {"Kind": ["one", "two"]}, "only string-valued members of string enums are vocabularies"
    closed = {entry["name"]: entry for entry in inventory["python_closed_sets"]}
    assert set(closed) == {"STATES"}, "a closed set needs two or more string members"
    assert closed["STATES"]["container"] == "frozenset"
    assert [entry["values"] for entry in inventory["python_literal_aliases"]] == [["fast", "slow"]]


def test_typescript_as_const_arrays_only(repo: Path) -> None:
    inventory = build_inventory(repo)
    assert [entry["name"] for entry in inventory["typescript_const_arrays"]] == ["KINDS"]


def test_duplicate_constants_split_into_twin_fork_conflict(repo: Path) -> None:
    inventory = build_inventory(repo)["duplicate_definitions"]
    assert [entry["name"] for entry in inventory["cross_runtime_twins"]] == ["PAIRED_SCHEMA_VERSION"]
    assert [entry["name"] for entry in inventory["same_runtime_forks"]] == ["FOO_SCHEMA_VERSION", "SHARED"]
    assert [entry["name"] for entry in inventory["conflicting_values"]] == ["CLASHING"]
    fork = inventory["same_runtime_forks"][0]
    assert fork["is_schema_version"] is True
    assert fork["modules"] == ["loopx/a.py", "loopx/b.py"]


def test_summary_counts_and_skipped_directories(repo: Path) -> None:
    summary = build_inventory(repo)["summary"]
    assert summary["source_files"] == 3, "__pycache__ and node_modules are never scanned"
    assert summary["schema_version_names"] == 2
    assert summary["schema_version_same_runtime_forks"] == 1
    assert summary["cross_runtime_twins"] == 1
    assert summary["same_runtime_forks"] == 2
    assert summary["conflicting_values"] == 1


@pytest.fixture
def collision_repo(tmp_path: Path) -> Path:
    """A tree where multi-value carriers collide and local names look like drift.

    ``STATES`` is a closed set under two divergent value sets, ``TWIN_SET`` is one
    closed set written twice, and ``SCHEMA_VERSION`` / ``FOO_SCHEMA_VERSION`` are
    per-module names that must not be counted as shared vocabulary.
    """
    _write(
        tmp_path,
        "loopx/a.py",
        'STATES = frozenset({"open", "closed"})\n'
        'TWIN_SET = ("p", "q")\n'
        'SCHEMA_VERSION = "a_v1"\n'
        'FOO_SCHEMA_VERSION = "foo_v0"\n'
        'SHARED = "same"\n',
    )
    _write(
        tmp_path,
        "loopx/b.py",
        'STATES = frozenset({"open", "shut"})\n'
        'TWIN_SET = ("p", "q")\n'
        'SCHEMA_VERSION = "b_v1"\n'
        'FOO_SCHEMA_VERSION = "foo_v0"\n'
        'SHARED = "same"\n',
    )
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "loopx/a.py", "loopx/b.py"], check=True,
    )
    return tmp_path


def test_multi_value_carriers_collide_like_string_constants(collision_repo: Path) -> None:
    """An enum or closed set carries vocabulary, so a duplicate name is drift.

    Before this rule the scanner listed multi-value carriers but never compared
    them, so a closed set defined in two modules with divergent values was
    invisible to every budget.
    """
    duplicates = build_inventory(collision_repo)["duplicate_definitions"]
    assert [entry["name"] for entry in duplicates["multi_value_forks"]] == ["STATES"]
    assert [entry["name"] for entry in duplicates["multi_value_twins"]] == ["TWIN_SET"]
    fork = duplicates["multi_value_forks"][0]
    assert [item["values"] for item in fork["definitions"]] == [
        ["open", "closed"],
        ["open", "shut"],
    ], "the fork keeps both value sets so a reviewer sees the divergence"
    assert [item["kind"] for item in fork["definitions"]] == [
        "python_closed_set",
        "python_closed_set",
    ]


def test_module_local_convention_names_stay_out_of_semantic_budgets(collision_repo: Path) -> None:
    """``SCHEMA_VERSION`` and ``FOO_SCHEMA_VERSION`` are per-module names, not drift.

    Counting them as conflicts measured local naming; the unfiltered totals stay
    visible, while only the shared-vocabulary subset is budgeted.
    """
    summary = build_inventory(collision_repo)["summary"]
    assert summary["conflicting_values"] == 1, "SCHEMA_VERSION still counts in the raw total"
    assert summary["conflicting_values_semantic"] == 0, "neither name is shared vocabulary"
    assert summary["same_runtime_forks"] == 2
    assert summary["same_runtime_forks_semantic"] == 1, "only SHARED is shared vocabulary"
    assert summary["multi_value_forks"] == 1
    assert summary["multi_value_twins"] == 1


@pytest.fixture
def merge_candidate_repo(tmp_path: Path) -> Path:
    """Three name pairs carry one value set each; only one pair is registered.

    ``Kind``/``KINDS`` are the two owners of a single registered vocabulary, so
    their equal value set is a naming convention across runtimes rather than
    duplication. ``ALPHA_STAGES``/``MIRROR_STAGES`` is unregistered and lives
    only in Python; ``OTHER_SIDES``/``ZED_SIDES`` is unregistered and spans both
    runtimes, which is the registry-gap shape. ``MIRROR_STAGES`` is a registered
    owner with no TypeScript counterpart, so its vocabulary explains no pair.
    """
    _write(
        tmp_path,
        "loopx/a.py",
        'from enum import Enum\n'
        'class Kind(str, Enum):\n    ONE = "one"\n    TWO = "two"\n'
        'ALPHA_STAGES = ("draft", "final")\n',
    )
    _write(tmp_path, "loopx/b.ts", 'export const KINDS = ["one", "two"] as const;\n')
    _write(
        tmp_path,
        "loopx/c.py",
        'MIRROR_STAGES = ("draft", "final")\nOTHER_SIDES = ("left", "right")\n',
    )
    _write(tmp_path, "loopx/d.ts", 'export const ZED_SIDES = ["left", "right"] as const;\n')
    _write(
        tmp_path,
        "loopx/semantics/vocabulary_v0.json",
        json.dumps(
            {
                "vocabularies": {
                    "kind": {
                        "owners": {
                            "python": "loopx/a.py::Kind",
                            "typescript": "loopx/b.ts::KINDS",
                        }
                    },
                    "mirror_stages": {
                        "owners": {"python": "loopx/c.py::MIRROR_STAGES", "typescript": None}
                    },
                }
            }
        ),
    )
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "loopx/a.py", "loopx/b.ts", "loopx/c.py", "loopx/d.ts"],
        check=True,
    )
    return tmp_path


def _registry(repo: Path) -> dict:
    return json.loads((repo / "loopx/semantics/vocabulary_v0.json").read_text(encoding="utf-8"))


def test_one_vocabulary_owning_two_spellings_explains_no_merge_candidate(
    merge_candidate_repo: Path,
) -> None:
    """A registered owner pair is a naming convention, not a merge candidate.

    Without the registry the advisory list mixed each cross-runtime vocabulary's
    own two owner symbols in with the groups nobody has ruled on, so the list
    read as duplication it was not. Filtering only hides the settled pairs; it
    retires nothing and classifies none of the groups that stay.
    """
    inventory = build_inventory(merge_candidate_repo)
    unfiltered = {tuple(group["names"]) for group in merge_candidate_groups(inventory)}
    assert ("KINDS", "Kind") in unfiltered, "the unfiltered audit still sees every value-set collision"
    filtered = merge_candidate_groups(inventory, _registry(merge_candidate_repo))
    assert {tuple(group["names"]) for group in filtered} == {
        ("ALPHA_STAGES", "MIRROR_STAGES"),
        ("OTHER_SIDES", "ZED_SIDES"),
    }, "an unregistered pair with the same value set is still reported"
    assert registered_owner_symbol_sets(_registry(merge_candidate_repo)) == {
        frozenset({"Kind", "KINDS"})
    }, "a vocabulary with one owner symbol explains nothing"


def test_merge_candidates_mark_the_pairs_that_span_both_runtimes(
    merge_candidate_repo: Path,
) -> None:
    """Cross-runtime is the registry gap; Python-only is a human question."""
    groups = merge_candidate_groups(
        build_inventory(merge_candidate_repo), _registry(merge_candidate_repo)
    )
    assert {tuple(group["names"]): group["cross_runtime"] for group in groups} == {
        ("ALPHA_STAGES", "MIRROR_STAGES"): False,
        ("OTHER_SIDES", "ZED_SIDES"): True,
    }


def test_report_prints_the_unexplained_merge_candidates_in_a_stable_order(
    merge_candidate_repo: Path, monkeypatch, capsys
) -> None:
    """``--report`` is where a reviewer sees the list, so it must not churn."""
    from scripts import generate_semantic_inventory as generator

    monkeypatch.setattr(generator, "ROOT", merge_candidate_repo)
    monkeypatch.setattr(sys, "argv", ["generate_semantic_inventory", "--report"])
    assert generator.main() == 0
    first = capsys.readouterr().out
    assert generator.main() == 0
    assert capsys.readouterr().out == first, "the same tree must print the same advisory list"
    listing = first.split("merge candidates", 1)[1]
    assert "2 to review, 1 explained by a registered vocabulary's own owner symbols, 3 raw groups" in listing
    assert "Kind" not in listing, "the explained owner pair is not printed"
    assert "  [cross-runtime] OTHER_SIDES, ZED_SIDES" in listing
    assert "      values:  left, right" in listing
    assert "      modules: loopx/c.py, loopx/d.ts" in listing
    assert "  [python-only] ALPHA_STAGES, MIRROR_STAGES" in listing
    assert listing.index("[cross-runtime]") < listing.index("[python-only]"), (
        "registry gaps sort ahead of the Python-only groups regardless of name order"
    )


def test_render_is_deterministic_valid_json(repo: Path) -> None:
    inventory = build_inventory(repo)
    rendered = render_inventory(inventory)
    assert rendered == render_inventory(build_inventory(repo))
    assert json.loads(rendered) == inventory
    assert inventory["schema_version"] == INVENTORY_SCHEMA_VERSION
    # one entry per line keeps diffs reviewable
    assert '    {"name": "Kind", "module": "loopx/a.py", "values": ["one", "two"]}' in rendered


def test_inventory_cli_defaults_to_json_without_writing(repo: Path, monkeypatch, capsys) -> None:
    from scripts import generate_semantic_inventory as generator

    legacy = repo / "loopx/semantics/inventory_v0.json"
    _write(repo, "loopx/semantics/inventory_v0.json", "obsolete report, not JSON")
    monkeypatch.setattr(generator, "ROOT", repo)
    monkeypatch.setattr(sys, "argv", ["generate_semantic_inventory"])
    assert generator.main() == 0
    emitted = json.loads(capsys.readouterr().out)
    assert emitted["schema_version"] == INVENTORY_SCHEMA_VERSION
    assert emitted["summary"]["source_files"] == 3
    assert legacy.read_text() == "obsolete report, not JSON"


def test_optional_inventory_report_check_never_repairs(repo: Path, monkeypatch, capsys) -> None:
    from scripts import generate_semantic_inventory as generator

    output = repo / ".local/reports/inventory.json"
    monkeypatch.setattr(generator, "ROOT", repo)
    args = ["generate_semantic_inventory", "--output", str(output)]
    monkeypatch.setattr(sys, "argv", args + ["--check"])
    assert generator.main() == 1
    assert not output.exists()
    monkeypatch.setattr(sys, "argv", args)
    assert generator.main() == 0
    saved = output.read_bytes()
    monkeypatch.setattr(sys, "argv", args + ["--check"])
    assert generator.main() == 0
    with (repo / "loopx/a.py").open("a") as stream:
        stream.write('ADDED = ("new", "carrier")\n')
    assert generator.main() == 1
    assert output.read_bytes() == saved
    assert "stale or missing" in capsys.readouterr().err


def test_snapshot_check_requires_an_explicit_report(monkeypatch, capsys) -> None:
    from scripts import generate_semantic_inventory as generator

    monkeypatch.setattr(sys, "argv", ["generate_semantic_inventory", "--check"])
    with pytest.raises(SystemExit) as exc:
        generator.main()
    assert exc.value.code == 2
    assert "--check requires --output" in capsys.readouterr().err


def test_full_tree_scan_detects_a_collision_with_an_unchanged_file(repo: Path) -> None:
    _write(repo, "loopx/old.py", 'Q9_SHARED = "existing"\n')
    subprocess.run(["git", "-C", str(repo), "add", "loopx/old.py"], check=True)
    assert not any(row["name"] == "Q9_SHARED" for row in build_inventory(repo)["duplicate_definitions"]["same_runtime_forks"])
    _write(repo, "loopx/new.py", 'Q9_SHARED = "existing"\n')
    subprocess.run(["git", "-C", str(repo), "add", "loopx/new.py"], check=True)
    fork = next(row for row in build_inventory(repo)["duplicate_definitions"]["same_runtime_forks"] if row["name"] == "Q9_SHARED")
    assert set(fork["modules"]) == {"loopx/old.py", "loopx/new.py"}


def test_untracked_sources_do_not_change_inventory(repo: Path) -> None:
    before = render_inventory(build_inventory(repo))
    _write(repo, "loopx/local_notes.py", 'PRIVATE_CONTEXT = ("synthetic", "local")\n')
    assert render_inventory(build_inventory(repo)) == before


def test_tracked_symlink_cannot_read_untracked_source(repo: Path) -> None:
    _write(repo, "private.py", 'PRIVATE_CONTEXT = ("synthetic", "local")\n')
    try:
        (repo / "loopx/link.py").symlink_to(repo / "private.py")
    except OSError as exc:
        pytest.skip(f"file symlinks unavailable: {exc}")
    subprocess.run(["git", "-C", str(repo), "add", "loopx/link.py"], check=True)
    with pytest.raises(ValueError, match="symlink"):
        load_sources(repo)


def test_invalid_python_source_is_not_reported_as_empty() -> None:
    with pytest.raises(ValueError, match="loopx/broken.py"):
        python_facts(SourceFile("loopx/broken.py", ".py", 'STATES = ("open",\n'))


def test_typescript_single_quoted_carriers_are_visible(repo: Path) -> None:
    _write(repo, "loopx/b.ts", "export const KINDS = ['one', 'two'] as const;\nexport const SHARED = 'same';\n")
    inventory = build_inventory(repo)
    assert inventory["typescript_const_arrays"][0]["values"] == ["one", "two"]
    assert any(item["name"] == "SHARED" for item in inventory["duplicate_definitions"]["same_runtime_forks"])


def _carrier(module: str, values: list[str], container: str = "set") -> dict:
    return {
        "kind": "python_closed_set", "name": "STATES", "module": module,
        "values": values, "container": container,
    }


def test_reordering_an_unordered_carrier_is_not_a_fork() -> None:
    """A ``set`` has no element order, so listing the same members differently
    is the same closed set. Before this rule, re-indenting a set literal
    manufactured a semantic fork and blocked the gate, while the divergence
    report computed from the same inventory correctly saw no divergence -- two
    outputs of one scan disagreeing, with the wrong one holding the gate.
    """
    twins, forks = multi_value_name_collisions([
        _carrier("loopx/a.py", ["open", "closed"]),
        _carrier("loopx/b.py", ["closed", "open"]),
    ])
    assert [row["name"] for row in twins] == ["STATES"]
    assert forks == []
    # Diagnostics keep source order; only the identity key is normalized.
    assert [item["values"] for item in twins[0]["definitions"]] == [
        ["open", "closed"], ["closed", "open"],
    ]


def test_changing_membership_of_an_unordered_carrier_is_still_a_fork() -> None:
    twins, forks = multi_value_name_collisions([
        _carrier("loopx/a.py", ["open", "closed"]),
        _carrier("loopx/b.py", ["open", "shut"]),
    ])
    assert twins == []
    assert [row["name"] for row in forks] == ["STATES"]


def test_reordering_an_ordered_carrier_is_still_a_fork() -> None:
    """``tuple`` and ``list`` carriers spend their order as meaning.

    ``LIFECYCLE_PRIORITY`` is a tuple defined in two modules: the order *is* the
    priority. Normalizing every carrier to sorted membership would trade a false
    positive for a false negative and hide that divergence, so orderedness is
    read from the container the source actually used.
    """
    for container in ("tuple", "list"):
        twins, forks = multi_value_name_collisions([
            _carrier("loopx/a.py", ["first", "second"], container),
            _carrier("loopx/b.py", ["second", "first"], container),
        ])
        assert twins == [], container
        assert [row["name"] for row in forks] == ["STATES"], container


def test_a_name_carried_by_mixed_containers_keeps_order_sensitive_identity() -> None:
    """Membership decides identity only when every definition is unordered.

    This keeps the rule a pure relaxation: it can only merge definitions the old
    rule split, never split a pair it merged, so no untouched tree starts
    failing because one side of a name is a tuple.
    """
    twins, forks = multi_value_name_collisions([
        _carrier("loopx/a.py", ["open", "closed"], "set"),
        _carrier("loopx/b.py", ["open", "closed"], "tuple"),
    ])
    assert [row["name"] for row in twins] == ["STATES"]
    assert forks == []


def test_a_carrier_without_a_container_stays_order_sensitive() -> None:
    """Enums, ``Literal`` aliases and ``as const`` arrays carry no ``container``.

    Their ordering semantics are not established here, so they keep the
    reporting behaviour rather than being silently normalized.
    """
    twins, forks = multi_value_name_collisions([
        {"kind": "python_enum", "name": "STATES", "module": "loopx/a.py",
         "values": ["open", "closed"]},
        {"kind": "python_enum", "name": "STATES", "module": "loopx/b.py",
         "values": ["closed", "open"]},
    ])
    assert twins == []
    assert [row["name"] for row in forks] == ["STATES"]
