from __future__ import annotations

import json
import shutil
from copy import deepcopy
from pathlib import Path

import pytest

from loopx.control_plane.testing.turn_journal_characterization import (
    TURN_JOURNAL_CHARACTERIZATION_DIFFERENTIAL_VERSION,
    TURN_JOURNAL_CHARACTERIZATION_PROBE_VERSION,
    compare_turn_journal_characterization_receipts,
    evaluate_turn_journal_invariants,
    load_turn_journal_characterization_corpus,
    run_turn_journal_probe_command,
    validate_turn_journal_characterization_corpus,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_PATH = (
    REPO_ROOT
    / "tests"
    / "fixtures"
    / "control_plane"
    / "turn_journal_characterization_v0.json"
)


def _corpus() -> dict[str, object]:
    return load_turn_journal_characterization_corpus(CORPUS_PATH)


def _probe() -> dict[str, object]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("TypeScript candidate requires Node.js 22.22.3 or newer")
    return run_turn_journal_probe_command(
        _corpus(),
        command=[
            node,
            "--no-warnings",
            "--experimental-strip-types",
            str(
                REPO_ROOT
                / "tests"
                / "control_plane_ts"
                / "turn_journal_characterization_probe.mjs"
            ),
        ],
        cwd=REPO_ROOT,
        implementation_id="typescript-head",
    )


def test_characterization_corpus_is_public_safe_and_invariant_owned() -> None:
    corpus = _corpus()

    assert corpus["schema_version"] == ("loopx_turn_journal_characterization_corpus_v0")
    assert len(corpus["cases"]) == 10
    assert {case["invariant_id"] for case in corpus["cases"]} >= {
        "terminal-consistent-journal-is-replayable",
        "goal-identity-is-fenced",
        "settlement-phases-form-an-ordered-prefix",
        "phase-elements-are-typed-strings",
    }


@pytest.mark.parametrize(
    ("path", "value", "message"),
    (
        (("cases", 0, "request", "journal", "credential"), "value", "banned key"),
        (("cases", 0, "rationale"), "/private/run.json", "local path"),
    ),
)
def test_characterization_corpus_rejects_private_material(
    path: tuple[str | int, ...],
    value: str,
    message: str,
) -> None:
    corpus = deepcopy(_corpus())
    target: object = corpus
    for part in path[:-1]:
        target = target[part]  # type: ignore[index]
    target[path[-1]] = value  # type: ignore[index]

    with pytest.raises(ValueError, match=message):
        validate_turn_journal_characterization_corpus(corpus)


def test_typescript_owner_satisfies_the_independent_replay_invariants() -> None:
    receipt = _probe()

    assert receipt["schema_version"] == TURN_JOURNAL_CHARACTERIZATION_PROBE_VERSION
    assert len(receipt["rows"]) == 10
    assert evaluate_turn_journal_invariants(_corpus(), receipt) == {}
    rendered = json.dumps(receipt)
    assert "private_detail" not in rendered
    assert "receipt.body" not in rendered


def test_exact_base_candidate_receipts_pass_without_review() -> None:
    base = _probe()
    candidate = deepcopy(base)
    candidate["implementation_id"] = "candidate"

    result = compare_turn_journal_characterization_receipts(
        _corpus(),
        base,
        candidate,
    )

    assert result == {
        "schema_version": TURN_JOURNAL_CHARACTERIZATION_DIFFERENTIAL_VERSION,
        "ok": True,
        "exact_parity": True,
        "base_invariant_failures": {},
        "candidate_invariant_failures": {},
        "failed_case_count": 0,
        "review_required_case_count": 0,
        "cases": [
            {
                "case_id": case["case_id"],
                "status": "passed",
                "exact_match": True,
                "base_invariant_failures": [],
                "candidate_invariant_failures": [],
            }
            for case in _corpus()["cases"]
        ],
    }


def test_semantic_repair_requires_review_without_failing_invariants() -> None:
    base = _probe()
    candidate = deepcopy(base)
    candidate["implementation_id"] = "typed-candidate"
    row = next(
        row for row in candidate["rows"] if row["case_id"] == "non-string-phase-element"
    )
    row["completed_phases"] = []
    row["violations"] = ["completed_phases_invalid"]

    result = compare_turn_journal_characterization_receipts(
        _corpus(),
        base,
        candidate,
    )

    assert result["ok"] is True
    assert result["exact_parity"] is False
    assert result["failed_case_count"] == 0
    assert result["review_required_case_count"] == 1
    changed = next(
        case
        for case in result["cases"]
        if case["case_id"] == "non-string-phase-element"
    )
    assert changed["status"] == "review_required"
    assert changed["candidate_invariant_failures"] == []


def test_candidate_invariant_regression_fails_the_differential() -> None:
    base = _probe()
    candidate = deepcopy(base)
    row = next(
        row for row in candidate["rows"] if row["case_id"] == "owner-identity-mismatch"
    )
    row["decision"] = "replay_legal"
    row["replay_legal"] = True

    result = compare_turn_journal_characterization_receipts(
        _corpus(),
        base,
        candidate,
    )

    assert result["ok"] is False
    assert result["failed_case_count"] == 1
    assert result["candidate_invariant_failures"] == {
        "owner-identity-mismatch": [
            "decision: expected 'replay_blocked', got 'replay_legal'",
            "replay_legal: expected False, got True",
        ]
    }
