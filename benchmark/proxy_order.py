"""Compare recorded proxy rankings with released native scores in one fixed cohort."""

import argparse
from dataclasses import dataclass
import json
import math
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


def _frontier_summary(matched, proxy_direction, native_direction):
    """Diagnose top-frontier alignment and high-end proxy cliffs.

    Global pairwise agreement and ``kendall_tau_b`` can stay positive even when
    an over-optimizing candidate wins the proxy frontier while regressing below
    the native best (such as a reward hack or tail timeout cliff). A matched
    candidate counts toward ``false_improvements_over_best_native`` iff it is
    not native-optimal (``candidate.native != best_native_score``) and strictly
    beats at least one native-optimal matched candidate on ``proxy``
    (``order(candidate.proxy, best.proxy, proxy_direction) > 0``).
    """
    if not matched:
        return dict(
            best_native_count=0,
            best_proxy_matches_best_native=None,
            false_improvements_over_best_native=0,
        )

    best_native_score = (max if native_direction == "maximize" else min)(
        candidate.native for candidate in matched
    )
    best_proxy_score = (max if proxy_direction == "maximize" else min)(
        candidate.proxy for candidate in matched
    )

    # Track indices so distinct candidates sharing (proxy, native, accepted_at) stay separate.
    best_native_indices = {
        index
        for index, candidate in enumerate(matched)
        if candidate.native == best_native_score
    }
    best_proxy_indices = {
        index
        for index, candidate in enumerate(matched)
        if candidate.proxy == best_proxy_score
    }
    best_proxy_matches_best_native = bool(best_native_indices & best_proxy_indices)

    # Strictly beating any native-optimal candidate on proxy equals beating the worst proxy among them.
    worst_best_native_proxy = (min if proxy_direction == "maximize" else max)(
        matched[index].proxy for index in best_native_indices
    )
    false_improvements = sum(
        1
        for index, candidate in enumerate(matched)
        if index not in best_native_indices
        and order(candidate.proxy, worst_best_native_proxy, proxy_direction) > 0
    )
    return dict(
        best_native_count=len(best_native_indices),
        best_proxy_matches_best_native=best_proxy_matches_best_native,
        false_improvements_over_best_native=false_improvements,
    )


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
    proxy_ties = 0
    for left, right in combinations(matched, 2):
        actual = order(left.native, right.native, native_direction)
        proxy_decision = order(left.proxy, right.proxy, proxy_direction)
        if proxy_decision == 0:
            proxy_ties += 1
        if actual == 0:
            native_ties += 1
            continue
        decisions = {
            "proxy": proxy_decision,
            "later_submission": sign(left.accepted_at, right.accepted_at),
        }
        for rule, prediction in decisions.items():
            if prediction == 0:
                rules[rule]["abstains"] += 1
            elif prediction == actual:
                rules[rule]["agrees"] += 1
            else:
                rules[rule]["reverses"] += 1

    pair_count = len(matched) * (len(matched) - 1) // 2
    tau_denominator = (pair_count - proxy_ties) * (pair_count - native_ties)
    kendall_tau_b = (
        (rules["proxy"]["agrees"] - rules["proxy"]["reverses"])
        / math.sqrt(tau_denominator)
        if tau_denominator > 0
        else None
    )
    frontier = _frontier_summary(matched, proxy_direction, native_direction)
    return dict(
        cohort=cohort,
        coverage=coverage,
        native_tied_pairs=native_ties,
        kendall_tau_b=kendall_tau_b,
        frontier=frontier,
        rules=rules,
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
