"""Compare recorded proxy rankings with released native scores in one fixed cohort."""

import argparse
from dataclasses import dataclass
import json
import re
from decimal import Decimal
from itertools import combinations
from pathlib import Path


def is_finite_number(value):
    # JSON floats are decoded as Decimal so close scores keep their ordering.
    return type(value) is int or (isinstance(value, Decimal) and value.is_finite())


def load_panel(path, *, clock, allow_unscored):
    panel = json.loads(path.read_bytes(), parse_float=Decimal)
    if not isinstance(panel, dict) or not isinstance(panel.get("rows"), list):
        raise ValueError("expected a panel with a rows array")
    if not isinstance(panel.get("cohort"), str) or not panel["cohort"].strip():
        raise ValueError("each panel needs a cohort identifier")
    if panel.get("direction") not in ("minimize", "maximize"):
        raise ValueError("each panel needs its metric direction")
    by_source = {}
    for row in panel["rows"]:
        if not isinstance(row, dict):
            raise ValueError("expected a row object")
        source = row.get("source_sha256")
        if not isinstance(source, str) or re.fullmatch(r"[0-9a-f]{64}", source) is None:
            raise ValueError("expected a lowercase candidate SHA-256")
        if source in by_source:
            raise ValueError(
                "multiple observations for one source: select a repeat policy first"
            )
        if not is_finite_number(row.get(clock)):
            raise ValueError(f"expected finite {clock} on a common clock")
        if "score" not in row or not (
            is_finite_number(row["score"]) or (allow_unscored and row["score"] is None)
        ):
            raise ValueError(
                "expected a finite score or an explicitly unscored submission"
            )
        by_source[source] = row
    return panel["cohort"], panel["direction"], by_source


@dataclass(frozen=True)
class MatchedScore:
    proxy: Decimal | int
    native: Decimal | int
    accepted_at: Decimal | int


def sign(left, right):
    return (left > right) - (left < right)


def order(left, right, direction):
    return sign(left, right) if direction == "maximize" else sign(right, left)


def compare(proxy_path, native_path):
    cohort, proxy_direction, predictions = load_panel(
        proxy_path, clock="observed_at", allow_unscored=False
    )
    native_cohort, native_direction, submissions = load_panel(
        native_path, clock="accepted_at", allow_unscored=True
    )
    if cohort != native_cohort:
        raise ValueError("proxy and native panels must describe the same cohort")
    coverage = dict(
        native_candidates=len(submissions),
        proxy_only=len(predictions.keys() - submissions.keys()),
        native_unscored=0,
        missing_proxy=0,
        late_or_simultaneous_proxy=0,
        matched=0,
    )
    matched = []
    for source, native in submissions.items():
        proxy = predictions.get(source)
        if native["score"] is None:
            coverage["native_unscored"] += 1
        elif proxy is None:
            coverage["missing_proxy"] += 1
        elif proxy["observed_at"] >= native["accepted_at"]:
            coverage["late_or_simultaneous_proxy"] += 1
        else:
            coverage["matched"] += 1
            matched.append(
                MatchedScore(proxy["score"], native["score"], native["accepted_at"])
            )

    # Both rules see exactly the same pairs. A flat proxy abstains on that pair.
    rules = {
        rule: dict(agrees=0, reverses=0, abstains=0)
        for rule in ("proxy", "later_submission")
    }
    native_ties = 0
    for left, right in combinations(matched, 2):
        actual = order(left.native, right.native, native_direction)
        if actual == 0:
            native_ties += 1
            continue
        decisions = {
            "proxy": order(left.proxy, right.proxy, proxy_direction),
            "later_submission": sign(left.accepted_at, right.accepted_at),
        }
        for rule, prediction in decisions.items():
            if prediction == 0:
                rules[rule]["abstains"] += 1
            elif prediction == actual:
                rules[rule]["agrees"] += 1
            else:
                rules[rule]["reverses"] += 1
    return dict(
        cohort=cohort, coverage=coverage, native_tied_pairs=native_ties, rules=rules
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "proxy", type=Path, help="local scores on one common proxy panel"
    )
    parser.add_argument(
        "native",
        type=Path,
        help="all accepted candidates in the selected native window",
    )
    args = parser.parse_args()
    try:
        print(json.dumps(compare(args.proxy, args.native), indent=2))
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Cannot compare panels: {exc}\n")


if __name__ == "__main__":
    main()
