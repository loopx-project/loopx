import json
import math
import os
import subprocess
import sys

import pytest

from benchmark.proxy_order import compare


def panel(tmp_path, name, rows, direction, cohort="fixed"):
    clock = "observed_at" if name == "proxy" else "accepted_at"
    data = [
        dict(source_sha256=f"{source:064x}", score=score, **{clock: timestamp})
        for source, score, timestamp in rows
    ]
    path = tmp_path / name
    path.write_text(json.dumps(dict(cohort=cohort, direction=direction, rows=data)))
    return path


def test_compare_with_later_wins_on_the_same_pairs(tmp_path):
    proxy = panel(
        tmp_path,
        "proxy",
        [(1, 2, 1), (2, 1, 1), (3, 1, 1), (4, 2, 6), (6, 0, 1)],
        "minimize",
    )
    native = panel(
        tmp_path,
        "native",
        [(3, 11, 5), (2, 12, 3), (1, 11, 2), (4, 15, 6), (5, 16, 8), (7, None, 9)],
        "maximize",
    )
    result = compare(proxy, native)
    assert result["coverage"] == dict(
        native_candidates=6,
        matched=3,
        proxy_only=1,
        missing_proxy=1,
        late_or_simultaneous_proxy=1,
        native_unscored=1,
    )
    assert result["native_tied_pairs"] == 1
    assert result["rules"]["proxy"] == dict(agrees=1, reverses=0, abstains=1)
    assert result["rules"]["later_submission"] == dict(agrees=1, reverses=1, abstains=0)


def test_reversal_and_simultaneous_submissions(tmp_path):
    proxy = panel(tmp_path, "proxy", [(1, 0, 0), (2, 1, 0)], "maximize")
    native = panel(tmp_path, "native", [(1, 0, 1), (2, -1, 1)], "maximize")
    result = compare(proxy, native)
    assert result["rules"]["proxy"]["reverses"] == 1
    assert result["rules"]["later_submission"]["abstains"] == 1


def test_only_one_matching_forecast(tmp_path):
    proxy = panel(tmp_path, "proxy", [(1, 0, 0), (2, 1, 0)], "maximize")
    native = panel(tmp_path, "native", [(1, 0, 1)], "maximize")
    result = compare(proxy, native)
    assert result["coverage"]["proxy_only"] == 1
    assert all(sum(rule.values()) == 0 for rule in result["rules"].values())


@pytest.mark.parametrize(
    "rows",
    [
        [(1, 0, 0), (1, 1, 1)],
        [(1, float("nan"), 0)],
        [(1, False, 0)],
        [(1, 1, None)],
        [(1, None, 0)],
    ],
)
def test_ambiguous_or_invalid_local_evidence(tmp_path, rows):
    with pytest.raises(ValueError):
        compare(
            panel(tmp_path, "proxy", rows, "maximize"),
            panel(tmp_path, "native", [], "maximize"),
        )


def test_reject_mixed_cohorts(tmp_path):
    with pytest.raises(ValueError, match="same cohort"):
        compare(
            panel(tmp_path, "proxy", [], "maximize"),
            panel(tmp_path, "native", [], "maximize", "other"),
        )


def test_scores_below_float_precision_are_ordered(tmp_path):
    proxy = panel(tmp_path, "proxy", [(1, 1, 1), (2, 2, 1)], "maximize")
    native = panel(tmp_path, "native", [(1, 0, 2), (2, 0, 3)], "maximize")
    native.write_text(
        native.read_text()
        .replace('"score": 0', '"score": 1.0000000000000000002', 1)
        .replace('"score": 0', '"score": 1.0000000000000000001', 1)
    )
    result = compare(proxy, native)
    assert result["rules"]["proxy"]["reverses"] == 1
    assert result["kendall_tau_b"] == pytest.approx(-1.0)
    assert result["frontier"] == dict(
        best_native_count=1,
        best_proxy_matches_best_native=False,
        false_improvements_over_best_native=1,
    )


def test_kendall_tau_b_monotonic_and_pure_reversal(tmp_path):
    proxy = panel(tmp_path, "proxy", [(1, 30, 1), (2, 20, 1), (3, 10, 1)], "minimize")
    native_agree = panel(
        tmp_path, "native", [(1, 1, 2), (2, 2, 3), (3, 3, 4)], "maximize"
    )
    agree_result = compare(proxy, native_agree)
    assert agree_result["kendall_tau_b"] == pytest.approx(1.0)
    assert agree_result["frontier"] == dict(
        best_native_count=1,
        best_proxy_matches_best_native=True,
        false_improvements_over_best_native=0,
    )

    native_reverse = panel(
        tmp_path, "native", [(1, 3, 2), (2, 2, 3), (3, 1, 4)], "maximize"
    )
    reverse_result = compare(proxy, native_reverse)
    assert reverse_result["kendall_tau_b"] == pytest.approx(-1.0)
    assert reverse_result["frontier"] == dict(
        best_native_count=1,
        best_proxy_matches_best_native=False,
        false_improvements_over_best_native=2,
    )


def test_kendall_tau_b_with_proxy_only_and_joint_ties(tmp_path):
    proxy_only_tie = panel(
        tmp_path, "proxy", [(1, 1, 1), (2, 1, 1), (3, 2, 1)], "maximize"
    )
    native_strict = panel(
        tmp_path, "native", [(1, 1, 2), (2, 2, 3), (3, 3, 4)], "maximize"
    )
    proxy_tie_result = compare(proxy_only_tie, native_strict)
    assert proxy_tie_result["kendall_tau_b"] == pytest.approx(2 / math.sqrt(2 * 3))

    # Joint tie (1, 1) has abstains == 0, yet still enters T_p in tau-b.
    joint_tie_proxy = panel(
        tmp_path, "proxy", [(1, 1, 1), (2, 1, 1), (3, 2, 1)], "maximize"
    )
    joint_tie_native = panel(
        tmp_path, "native", [(1, 1, 2), (2, 1, 3), (3, 2, 4)], "maximize"
    )
    joint_result = compare(joint_tie_proxy, joint_tie_native)
    assert joint_result["native_tied_pairs"] == 1
    assert joint_result["rules"]["proxy"]["abstains"] == 0
    assert joint_result["kendall_tau_b"] == pytest.approx(1.0)


@pytest.mark.parametrize(
    "proxy_rows,native_rows,expected_frontier",
    [
        (
            [(1, 1, 5)],
            [(1, 1, 2)],
            dict(
                best_native_count=0,
                best_proxy_matches_best_native=None,
                false_improvements_over_best_native=0,
            ),
        ),
        (
            [(1, 2, 1)],
            [(1, 5, 2)],
            dict(
                best_native_count=1,
                best_proxy_matches_best_native=True,
                false_improvements_over_best_native=0,
            ),
        ),
        (
            [(1, 1, 1), (2, 1, 1), (3, 1, 1)],
            [(1, 1, 2), (2, 2, 3), (3, 3, 4)],
            dict(
                best_native_count=1,
                best_proxy_matches_best_native=True,
                false_improvements_over_best_native=0,
            ),
        ),
        (
            [(1, 1, 1), (2, 2, 1), (3, 3, 1)],
            [(1, 5, 2), (2, 5, 3), (3, 5, 4)],
            dict(
                best_native_count=3,
                best_proxy_matches_best_native=True,
                false_improvements_over_best_native=0,
            ),
        ),
    ],
)
def test_degenerate_tau_b_and_frontier_cases(
    tmp_path, proxy_rows, native_rows, expected_frontier
):
    result = compare(
        panel(tmp_path, "proxy", proxy_rows, "maximize"),
        panel(tmp_path, "native", native_rows, "maximize"),
    )
    assert result["kendall_tau_b"] is None
    assert result["frontier"] == expected_frontier


def test_high_end_proxy_cliff_counterexample(tmp_path):
    proxy = panel(
        tmp_path,
        "proxy",
        [(1, 1, 1), (2, 2, 1), (3, 3, 1), (4, 4, 1), (5, 5, 1)],
        "maximize",
    )
    native = panel(
        tmp_path,
        "native",
        [(1, 1, 2), (2, 2, 3), (3, 3, 4), (4, 10, 5), (5, 0, 6)],
        "maximize",
    )
    result = compare(proxy, native)
    assert result["rules"]["proxy"] == dict(agrees=6, reverses=4, abstains=0)
    assert result["kendall_tau_b"] == pytest.approx(0.2)
    assert result["frontier"] == dict(
        best_native_count=1,
        best_proxy_matches_best_native=False,
        false_improvements_over_best_native=1,
    )


def test_frontier_multiple_native_optima_and_strict_tie_boundary(tmp_path):
    # Minimize both: native-optimal candidates tie at native=1 with proxies 2 and 5.
    proxy = panel(
        tmp_path,
        "proxy",
        [(1, 2, 1), (2, 5, 1), (3, 4, 1), (4, 2, 1), (5, 6, 1)],
        "minimize",
    )
    native = panel(
        tmp_path,
        "native",
        [(1, 1, 2), (2, 1, 3), (3, 3, 4), (4, 4, 5), (5, 2, 6)],
        "minimize",
    )
    result = compare(proxy, native)
    assert result["frontier"] == dict(
        best_native_count=2,
        best_proxy_matches_best_native=True,
        false_improvements_over_best_native=2,
    )

    # Tying the sole native-optimal candidate on proxy is not a strict false improvement.
    tie_proxy = panel(tmp_path, "proxy", [(1, 10, 1), (2, 10, 1)], "maximize")
    tie_native = panel(tmp_path, "native", [(1, 10, 2), (2, 5, 3)], "maximize")
    tie_result = compare(tie_proxy, tie_native)
    assert tie_result["frontier"] == dict(
        best_native_count=1,
        best_proxy_matches_best_native=True,
        false_improvements_over_best_native=0,
    )


def test_cli_entrypoint_outputs_json_diagnostics(tmp_path):
    proxy = panel(
        tmp_path,
        "proxy",
        [(1, 1, 1), (2, 2, 1), (3, 3, 1), (4, 4, 1), (5, 5, 1)],
        "maximize",
    )
    native = panel(
        tmp_path,
        "native",
        [(1, 1, 2), (2, 2, 3), (3, 3, 4), (4, 10, 5), (5, 0, 6)],
        "maximize",
    )
    env = {**os.environ, "LOOPX_USAGE_PING": "0"}
    proc = subprocess.run(
        [sys.executable, "-m", "benchmark.proxy_order", str(proxy), str(native)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert set(payload) == {
        "cohort",
        "coverage",
        "native_tied_pairs",
        "kendall_tau_b",
        "frontier",
        "rules",
    }
    assert payload["kendall_tau_b"] == pytest.approx(0.2)
    assert payload["frontier"] == dict(
        best_native_count=1,
        best_proxy_matches_best_native=False,
        false_improvements_over_best_native=1,
    )
