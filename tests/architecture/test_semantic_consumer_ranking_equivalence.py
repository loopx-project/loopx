"""Differential cover for the indexed ``consumer_ranking``.

The ranking used to compile one ``\\bNAME\\b`` pattern per carrier and search
every source with it. It now tokenises each source once and looks the name up.
The equivalence that makes this legal is narrow: a name made only of word
characters matches ``\\bNAME\\b`` exactly when it appears as a whole ``\\w+``
token. Names outside that shape keep the original search.

These tests hold the original implementation as the oracle and compare the
*whole* returned object, ordering included, rather than a total, so a change
that keeps the counts but reorders or drops a field still fails.
"""

from __future__ import annotations

import random
import re
from typing import Any

import pytest

from loopx.semantics.inventory import SourceFile, consumer_ranking

SECTIONS = (
    "python_enums",
    "typescript_const_arrays",
    "python_closed_sets",
    "python_literal_aliases",
)


def _oracle(inventory: dict[str, Any], sources: list[SourceFile]) -> list[dict[str, Any]]:
    """The per-symbol search this change replaces, kept verbatim as the oracle."""
    ranking: list[dict[str, Any]] = []
    for section in SECTIONS:
        for entry in inventory[section]:
            token = re.compile(rf"\b{re.escape(entry['name'])}\b")
            consumers = sum(
                1 for source in sources
                if source.path != entry["module"] and token.search(source.text)
            )
            ranking.append(
                {
                    "kind": section,
                    "name": entry["name"],
                    "module": entry["module"],
                    "values": len(entry["values"]),
                    "external_consumer_modules": consumers,
                }
            )
    return sorted(
        ranking,
        key=lambda item: (-item["external_consumer_modules"], item["module"], item["name"]),
    )


def _inventory(entries: list[tuple[str, str, str, int]]) -> dict[str, Any]:
    built: dict[str, Any] = {section: [] for section in SECTIONS}
    for section, name, module, values in entries:
        built[section].append({"name": name, "module": module, "values": ["v"] * values})
    return built


def _assert_matches_oracle(inventory: dict[str, Any], sources: list[SourceFile]) -> None:
    assert consumer_ranking(inventory, sources) == _oracle(inventory, sources)


def test_a_substring_of_a_longer_identifier_is_not_a_mention() -> None:
    inventory = _inventory([("python_enums", "Action", "a.py", 2)])
    sources = [
        SourceFile(path="a.py", suffix=".py", text="class Action: pass"),
        SourceFile(path="b.py", suffix=".py", text="ActionKind = 1\nmy_action_value = 2"),
        SourceFile(path="c.py", suffix=".py", text="from a import Action"),
    ]
    assert consumer_ranking(inventory, sources)[0]["external_consumer_modules"] == 1
    _assert_matches_oracle(inventory, sources)


def test_comments_and_string_contents_still_count_as_mentions() -> None:
    inventory = _inventory([("python_closed_sets", "MODES", "m.py", 3)])
    sources = [
        SourceFile(path="m.py", suffix=".py", text="MODES = {'a'}"),
        SourceFile(path="doc.py", suffix=".py", text="# MODES is documented here"),
        SourceFile(path="lit.py", suffix=".py", text='help = "see MODES for detail"'),
    ]
    assert consumer_ranking(inventory, sources)[0]["external_consumer_modules"] == 2
    _assert_matches_oracle(inventory, sources)


def test_repeated_mentions_inside_one_file_count_once() -> None:
    inventory = _inventory([("python_enums", "Kind", "k.py", 1)])
    sources = [
        SourceFile(path="k.py", suffix=".py", text="class Kind: pass"),
        SourceFile(path="u.py", suffix=".py", text="Kind, Kind, Kind"),
    ]
    assert consumer_ranking(inventory, sources)[0]["external_consumer_modules"] == 1
    _assert_matches_oracle(inventory, sources)


def test_a_duplicated_source_record_is_not_silently_deduplicated() -> None:
    """Two records for one path contributed twice before and still do."""
    inventory = _inventory([("python_enums", "Kind", "k.py", 1)])
    duplicate = SourceFile(path="u.py", suffix=".py", text="Kind")
    sources = [SourceFile(path="k.py", suffix=".py", text="class Kind: pass"), duplicate, duplicate]
    assert consumer_ranking(inventory, sources)[0]["external_consumer_modules"] == 2
    _assert_matches_oracle(inventory, sources)


def test_the_defining_module_is_excluded_even_when_it_mentions_itself() -> None:
    inventory = _inventory([("python_enums", "Kind", "k.py", 1)])
    sources = [SourceFile(path="k.py", suffix=".py", text="Kind = 1\nprint(Kind)")]
    assert consumer_ranking(inventory, sources)[0]["external_consumer_modules"] == 0
    _assert_matches_oracle(inventory, sources)


@pytest.mark.parametrize("name", ["with-dash", "dotted.name", "trailing-", "-leading", "sp ace"])
def test_a_name_outside_the_token_shape_falls_back_to_the_search(name: str) -> None:
    inventory = _inventory([("python_enums", name, "d.py", 1)])
    sources = [
        SourceFile(path="d.py", suffix=".py", text=f"X = {name!r}"),
        SourceFile(path="e.py", suffix=".py", text=f"use({name!r})"),
        SourceFile(path="f.py", suffix=".py", text="unrelated"),
    ]
    _assert_matches_oracle(inventory, sources)


@pytest.mark.parametrize("seed", range(40))
def test_random_corpora_rank_identically(seed: int) -> None:
    rng = random.Random(seed)
    alphabet = ["Alpha", "Beta", "Gamma", "alpha_case", "BETA", "Gamma2", "with-dash", "x"]
    names = rng.sample(alphabet, rng.randint(2, len(alphabet)))
    entries = [
        (rng.choice(SECTIONS), name, f"mod{rng.randint(0, 3)}.py", rng.randint(1, 5))
        for name in names
    ]
    inventory = _inventory(entries)
    sources = []
    for index in range(rng.randint(1, 8)):
        words = rng.choices(alphabet + ["noise", "Alphabet", "pre_Beta", "#", '"'], k=rng.randint(0, 12))
        separators = [" ", ".", "(", ")", "\n", "_", "-", ", "]
        text = "".join(word + rng.choice(separators) for word in words)
        sources.append(SourceFile(path=f"mod{index % 4}.py", suffix=".py", text=text))
    _assert_matches_oracle(inventory, sources)


def test_an_empty_corpus_ranks_every_symbol_at_zero() -> None:
    inventory = _inventory([("python_enums", "Kind", "k.py", 1)])
    assert consumer_ranking(inventory, [])[0]["external_consumer_modules"] == 0
    _assert_matches_oracle(inventory, [])
