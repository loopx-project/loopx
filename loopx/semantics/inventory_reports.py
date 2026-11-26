"""Reporting, rendering, and typed record definitions for semantic inventories."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import re
from typing import NotRequired, TypedDict, cast


@dataclass(frozen=True)
class SourceFile:
    """Tracked repository source file eligible for semantic inventory."""

    path: str
    suffix: str
    text: str


class FactEntry(TypedDict):
    """Typed structural inventory entry produced by Python/TS AST scanners."""

    name: str
    module: str
    values: NotRequired[list[str]]
    value: NotRequired[str]
    container: NotRequired[str]


class CarrierEntry(TypedDict):
    """Multi-value carrier tagged with its carrier kind."""

    kind: str
    name: str
    module: str
    values: list[str]
    container: NotRequired[str]


class CarrierDefinition(TypedDict):
    """Source-ordered definition entry preserved in collision diagnostics."""

    kind: str
    module: str
    values: list[str]


class MultiValueDuplicateEntry(TypedDict):
    """Twin or fork grouping across multi-value semantic carriers."""

    name: str
    definitions: list[CarrierDefinition]
    values: NotRequired[list[str]]
    modules: NotRequired[list[str]]


class StringConstantEntry(TypedDict):
    """Single named string constant emitted by Python or TypeScript scans."""

    name: str
    module: str
    value: str


class StringDefinition(TypedDict):
    """Single module/value pair under a named string constant."""

    module: str
    value: str


class StringDuplicateEntry(TypedDict):
    """Duplicate named string constant entry in the computed inventory."""

    name: str
    is_schema_version: bool
    value: NotRequired[str]
    modules: NotRequired[list[str]]
    definitions: NotRequired[list[StringDefinition]]


class MergeCandidateGroup(TypedDict):
    """Advisory group of distinct symbol names carrying identical value sets."""

    names: list[str]
    values: list[str]
    modules: list[str]
    cross_runtime: bool


class DivergentValueSetEntry(TypedDict):
    """Advisory summary of a forked symbol and its disagreeing value sets."""

    name: str
    value_sets: int
    definition_modules: list[str]


class ConsumerRankingEntry(TypedDict):
    """Advisory external consumer-mention ranking for one inventory symbol."""

    kind: str
    name: str
    module: str
    values: int
    external_consumer_modules: int


def _str_list(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [item if isinstance(item, str) else str(item) for item in value]


def render_inventory(inventory: Mapping[str, object]) -> str:
    """Two-space indented JSON with one line per inventory entry for readable diffs."""

    def one_line(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))

    lines = ["{"]
    keys = list(inventory)
    for index, key in enumerate(keys):
        value = inventory[key]
        comma = "," if index < len(keys) - 1 else ""
        if isinstance(value, list):
            if not value:
                lines.append(f'  "{key}": []{comma}')
                continue
            lines.append(f'  "{key}": [')
            for position, entry in enumerate(value):
                lines.append(
                    "    " + one_line(entry) + ("," if position < len(value) - 1 else "")
                )
            lines.append(f"  ]{comma}")
        elif (
            isinstance(value, dict)
            and value
            and all(isinstance(item, list) for item in value.values())
        ):
            lines.append(f'  "{key}": {{')
            inner_keys = list(value)
            for inner_index, inner_key in enumerate(inner_keys):
                entries = value[inner_key]
                inner_comma = "," if inner_index < len(inner_keys) - 1 else ""
                if not entries:
                    lines.append(f'    "{inner_key}": []{inner_comma}')
                    continue
                lines.append(f'    "{inner_key}": [')
                for position, entry in enumerate(entries):
                    lines.append(
                        "      "
                        + one_line(entry)
                        + ("," if position < len(entries) - 1 else "")
                    )
                lines.append(f"    ]{inner_comma}")
            lines.append(f"  }}{comma}")
        else:
            rendered = json.dumps(value, ensure_ascii=False, indent=2)
            rendered = "\n".join(
                ("  " + line) if i else line
                for i, line in enumerate(rendered.splitlines())
            )
            lines.append(f'  "{key}": {rendered}{comma}')
    lines.append("}")
    return "\n".join(lines) + "\n"


def registered_owner_symbol_sets(registry: Mapping[str, object]) -> set[frozenset[str]]:
    """Symbol sets that one registered vocabulary already binds into one concept.

    A cross-runtime vocabulary names its owner twice because the runtimes spell
    it differently: ``EffectiveAction`` in Python and ``EFFECTIVE_ACTIONS`` in
    TypeScript are the registered owners of ``effective_action``. The registry
    entry is the decision that the two spellings are one vocabulary, so their
    equal value sets carry no review question. Only sets with more than one
    distinct symbol are returned; a vocabulary with a single owner, or with the
    same symbol on both sides, explains nothing.
    """
    sets: set[frozenset[str]] = set()
    vocabularies = registry["vocabularies"]
    if not isinstance(vocabularies, Mapping):
        return sets
    for vocabulary in vocabularies.values():
        if not isinstance(vocabulary, Mapping):
            continue
        owners_obj = vocabulary.get("owners")
        owners = owners_obj if isinstance(owners_obj, Mapping) else {}
        symbols = frozenset(
            str(owner).split("::")[-1] for owner in owners.values() if owner
        )
        if len(symbols) > 1:
            sets.add(symbols)
    return sets


def merge_candidate_groups(
    inventory: Mapping[str, object],
    registry: Mapping[str, object] | None = None,
) -> list[MergeCandidateGroup]:
    """Advisory: distinct names carrying an identical multi-value set.

    Value-set equality is a candidate signal, not proof of one concept:
    ``CONFIDENCE_LEVELS`` and ``EDGE_CASE_COMPLEXITIES`` share ``high/low/medium``
    while meaning different things. Each group is for review, never auto-merged,
    and is printed on demand rather than committed so it cannot be mistaken for a
    ratified decision.

    ``registry`` drops the groups whose names are exactly the owner symbols of
    one registered vocabulary. Those are a naming convention, not duplication:
    the registry has already ruled the two spellings one concept, and leaving
    them in the list buries the groups that nobody has ruled on. Passing
    ``None`` keeps the unfiltered list, which is what an audit of the raw
    value-set collisions wants. Filtering removes noise from a review list; it
    retires nothing and settles no group that stays.

    ``cross_runtime`` marks a group whose modules span both runtimes. Such a
    group is a registry gap by construction, because a value set living in
    Python and TypeScript with no vocabulary binding them is exactly what the
    registry exists to record.
    """
    explained = (
        registered_owner_symbol_sets(registry) if registry is not None else set()
    )
    by_values: dict[tuple[str, ...], list[Mapping[str, object]]] = defaultdict(list)
    for section in (
        "python_enums",
        "python_closed_sets",
        "python_literal_aliases",
        "typescript_const_arrays",
    ):
        raw_section = inventory[section]
        if not isinstance(raw_section, list):
            continue
        for entry in raw_section:
            if not isinstance(entry, Mapping):
                continue
            entry_values = _str_list(entry.get("values"))
            if len(entry_values) >= 2:
                by_values[tuple(sorted(entry_values))].append(entry)
    groups: list[MergeCandidateGroup] = []
    for values, entries in by_values.items():
        names = sorted({str(entry["name"]) for entry in entries})
        if len(names) < 2 or frozenset(names) in explained:
            continue
        modules = sorted({str(entry["module"]) for entry in entries})
        groups.append(
            {
                "names": names,
                "values": list(values),
                "modules": modules,
                "cross_runtime": any(module.endswith(".py") for module in modules)
                and any(module.endswith(".ts") for module in modules),
            }
        )
    return sorted(groups, key=lambda group: (-len(group["names"]), group["names"]))


def divergent_value_sets(
    inventory: Mapping[str, object],
) -> list[DivergentValueSetEntry]:
    """Advisory: forks, keyed by name, with how many value sets each carries.

    Complements ``merge_candidate_groups``, which groups *different* names with
    *identical* value sets and therefore never lists a fork at all: a fork is
    *one* name whose modules disagree, so the two reports answer different
    questions and the merge-candidate grouping cannot substitute for this one.

    What this does **not** do is detect a rename. Renaming one side of a fork
    leaves that name with a single definition, so it stops being a fork and
    drops out of this report exactly as it drops out of the budget -- measured,
    not assumed. The RFC Section 9 limit therefore stands unclosed; this report
    only makes the *surviving* forks visible by name, with the count of
    disagreeing value sets, where before they were visible only as a number.
    A rename of one side is still caught when the name is declared, because the
    declaration names every defining module and the renamed side no longer
    matches.

    Nothing here is a second authority: advisory output over the same computed
    inventory, never committed, never a budget input.
    """
    entries: list[DivergentValueSetEntry] = []
    duplicates = inventory["duplicate_definitions"]
    if not isinstance(duplicates, Mapping):
        return entries
    raw_forks = duplicates["multi_value_forks"]
    if not isinstance(raw_forks, list):
        return entries
    for entry in raw_forks:
        if not isinstance(entry, Mapping):
            continue
        raw_definitions = entry["definitions"]
        if not isinstance(raw_definitions, list):
            continue
        definitions = [
            cast(Mapping[str, object], item)
            for item in raw_definitions
            if isinstance(item, Mapping)
        ]
        value_sets = {tuple(sorted(_str_list(item["values"]))) for item in definitions}
        if len(value_sets) < 2:
            continue
        entries.append(
            {
                "name": str(entry["name"]),
                "value_sets": len(value_sets),
                "definition_modules": sorted(
                    {str(item["module"]) for item in definitions}
                ),
            }
        )
    return sorted(entries, key=lambda item: (-item["value_sets"], item["name"]))


_word_token = re.compile(r"\w+")


def _word_token_mentions(sources: Sequence[SourceFile]) -> dict[str, set[int]]:
    """Map each word token to the source positions whose text contains it.

    Tokenising is deliberately lexical, over the raw text: comments and string
    contents counted as mentions before and still do. The value is a set of
    positions in ``sources`` so a repeated occurrence inside one file counts
    once, exactly as a single ``search`` did.
    """
    mentions: dict[str, set[int]] = {}
    for position, source in enumerate(sources):
        for token in set(_word_token.findall(source.text)):
            mentions.setdefault(token, set()).add(position)
    return mentions


def consumer_ranking(
    inventory: Mapping[str, object],
    sources: Sequence[SourceFile],
) -> list[ConsumerRankingEntry]:
    """Advisory ranking: modules outside the definer that mention each symbol.

    Not stored in the committed inventory because it changes with every consumer
    edit; the generator prints it on demand so retirement work can be ordered.
    """
    # One lexical pass over the corpus instead of one search per symbol. A name
    # made only of word characters matches ``\bNAME\b`` exactly when it appears
    # as a whole ``\w+`` token, so the index answers the same question the
    # per-symbol regex did. Anything else -- a name carrying a non-word
    # character, where the boundary assertions no longer line up with token
    # edges -- keeps the original search rather than assume the equivalence.
    mentions = _word_token_mentions(sources)
    ranking: list[ConsumerRankingEntry] = []
    for section in (
        "python_enums",
        "typescript_const_arrays",
        "python_closed_sets",
        "python_literal_aliases",
    ):
        raw_section = inventory[section]
        if not isinstance(raw_section, list):
            continue
        for entry in raw_section:
            if not isinstance(entry, Mapping):
                continue
            name = str(entry["name"])
            module = str(entry["module"])
            entry_values = _str_list(entry.get("values"))
            if _word_token.fullmatch(name):
                # Positions, not paths: a corpus that lists the same path twice
                # still contributes twice, as the per-source search did.
                consumers = sum(
                    1
                    for position in mentions.get(name, ())
                    if sources[position].path != module
                )
            else:
                token = re.compile(rf"\b{re.escape(name)}\b")
                consumers = sum(
                    1
                    for source in sources
                    if source.path != module and token.search(source.text)
                )
            ranking.append(
                {
                    "kind": section,
                    "name": name,
                    "module": module,
                    "values": len(entry_values),
                    "external_consumer_modules": consumers,
                }
            )
    return sorted(
        ranking,
        key=lambda item: (
            -item["external_consumer_modules"],
            item["module"],
            item["name"],
        ),
    )
