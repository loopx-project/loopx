#!/usr/bin/env python3
"""Compute the full tracked-tree inventory; optionally export a local report.

Usage:
  uv run python scripts/generate_semantic_inventory.py             # JSON to stdout, no writes
  uv run python scripts/generate_semantic_inventory.py --output .local/inventory.json
  uv run python scripts/generate_semantic_inventory.py --output .local/inventory.json --check
  uv run python scripts/generate_semantic_inventory.py --report    # advisory consumer ranking + merge candidates
  uv run python scripts/generate_semantic_inventory.py --report --consumer-evidence   # + per-site consumer roles
  uv run python scripts/generate_semantic_inventory.py --changed-from HEAD
  uv run python scripts/generate_semantic_inventory.py --changed-from HEAD --include-untracked loopx/new.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from loopx.semantics.consumer_report import (  # noqa: E402
    collect_consumer_evidence,
    render_consumer_evidence,
)
from loopx.semantics.development_probe import (  # noqa: E402
    DevelopmentProbeError,
    build_development_probe,
    collect_changed_sources,
    load_registry,
    render_development_probe,
)
from loopx.semantics.inventory import (  # noqa: E402
    build_inventory,
    consumer_ranking,
    divergent_value_sets,
    load_sources,
    merge_candidate_groups,
    render_inventory,
)

REGISTRY_RELATIVE = "loopx/semantics/vocabulary_v0.json"


def print_merge_candidates(inventory: dict) -> None:
    """Print the merge candidates the registry does not already explain.

    Advisory, like the ranking above it: an equal value set is a question for a
    reviewer, never an auto-merge. Groups that are one registered vocabulary's
    own Python and TypeScript owner symbols are dropped because the registry
    has already ruled them one concept; hiding them retires nothing and
    classifies nothing, it only leaves the unruled groups readable. A group
    whose modules span both runtimes is a registry gap: the same value set
    lives in two runtimes with no vocabulary binding them.
    """
    registry = json.loads((ROOT / REGISTRY_RELATIVE).read_text(encoding="utf-8"))
    groups = merge_candidate_groups(inventory, registry)
    raw = len(merge_candidate_groups(inventory))
    print(
        f"merge candidates (advisory, never auto-merged): {len(groups)} to review, "
        f"{raw - len(groups)} explained by a registered vocabulary's own owner symbols, "
        f"{raw} raw groups"
    )
    for group in sorted(groups, key=lambda item: (not item["cross_runtime"], item["names"])):
        scope = "cross-runtime" if group["cross_runtime"] else "python-only"
        print(f"  [{scope}] {', '.join(group['names'])}")
        print(f"      values:  {', '.join(group['values'])}")
        print(f"      modules: {', '.join(group['modules'])}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    destination = parser.add_mutually_exclusive_group()
    destination.add_argument("--output", type=Path, help="write an optional report to this path instead of stdout")
    destination.add_argument(
        "--report", action="store_true",
        help="print the advisory consumer ranking and the merge candidates the registry does not explain",
    )
    destination.add_argument(
        "--changed-from",
        metavar="REVISION",
        help="advisory: inspect supported vocabulary carriers changed from a named Git revision",
    )
    parser.add_argument(
        "--include-untracked",
        action="append",
        default=[],
        metavar="PATH",
        help="with --changed-from, explicitly include one untracked loopx/*.py or *.ts source",
    )
    parser.add_argument(
        "--consumer-evidence", action="store_true",
        help="with --report, also classify each consuming site as read/interpret/pass-through/unknown "
             "(RFC B5; advisory evidence over a bounded scan reach, never a gate)",
    )
    parser.add_argument("--check", action="store_true", help="compare an explicit --output report without writing")
    parser.add_argument("--top", type=int, default=25, help="rows to print with --report")
    args = parser.parse_args()
    if args.consumer_evidence and not args.report:
        parser.error("--consumer-evidence extends --report; it is advisory evidence, not a check")
    if args.include_untracked and args.changed_from is None:
        parser.error("--include-untracked requires --changed-from")
    if args.check and args.output is None:
        parser.error("--check requires --output; inventories are no longer committed. "
                     "Run examples/semantic-vocabulary-drift-smoke.py for semantic validation.")

    if args.changed_from is not None:
        try:
            baseline, changes = collect_changed_sources(
                ROOT,
                baseline=args.changed_from,
                explicit_untracked=args.include_untracked,
            )
            report = build_development_probe(
                baseline=baseline,
                changes=changes,
                registry=load_registry(ROOT / REGISTRY_RELATIVE),
            )
        except DevelopmentProbeError as error:
            print(f"semantic coinage probe failed: {error}", file=sys.stderr)
            return 1
        print(render_development_probe(report), end="")
        return 0

    inventory = build_inventory(ROOT)
    content = render_inventory(inventory)
    if args.report:
        rows = consumer_ranking(inventory, load_sources(ROOT))[: args.top]
        width = max((len(row["name"]) for row in rows), default=0)
        print("external_consumer_modules  values  name  module")
        for row in rows:
            print(f"{row['external_consumer_modules']:>25}  {row['values']:>6}  {row['name']:<{width}}  {row['module']}")
        divergent = divergent_value_sets(inventory)[: args.top]
        print()
        print("value_sets  name  definition_modules")
        for row in divergent:
            print(f"{row['value_sets']:>10}  {row['name']}  {', '.join(row['definition_modules'])}")
        print()
        print_merge_candidates(inventory)
        if args.consumer_evidence:
            # Off by default: the per-site scan costs seconds on the full tree,
            # and the ranking above answers the cheaper question. Nothing here
            # gates anything, so a reviewer opts in when they want the roles.
            print()
            registry = json.loads((ROOT / REGISTRY_RELATIVE).read_text(encoding="utf-8"))
            evidence = collect_consumer_evidence(ROOT, registry, load_sources(ROOT))
            print("\n".join(render_consumer_evidence(evidence, top=args.top)))
        return 0
    if args.output is None:
        print(content, end="")
        return 0
    if args.check:
        current = args.output.read_text(encoding="utf-8") if args.output.exists() else None
        if current != content:
            print(f"stale or missing semantic inventory report: {args.output}; "
                  "rerun with the same --output path without --check", file=sys.stderr)
            return 1
        print(f"semantic inventory report up to date: {args.output}")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(content, encoding="utf-8")
    print(f"generated semantic inventory report: {args.output}")
    print(json.dumps(inventory["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
