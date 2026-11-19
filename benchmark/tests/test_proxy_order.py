import json

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
    assert compare(proxy, native)["rules"]["proxy"]["reverses"] == 1
