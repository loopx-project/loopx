"""Host contract for the /loopx-deepresearch command.

Deep research drifts exactly the way long-horizon coding work does: the
question blurs, "I read it somewhere" replaces evidence, and nothing says when
to stop. These tests pin the three guards the command exists to enforce: claims
must come from recorded sources, answers must cite recorded claims, and the
stop decision belongs to the packet, not the model's mood.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from loopx.capabilities.deep_research.runtime import add_source, load_state, start_research


def _run_cli(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "loopx.cli", "--format", "json", *args],
        cwd=cwd,
        check=False,
        text=True,
        capture_output=True,
    )


def _payload(result: subprocess.CompletedProcess[str]) -> dict:
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["ok"] is True, data.get("error")
    return data


def _start(tmp_path: Path, *, question: str = "How does loopx gate agent loops?") -> None:
    result = _run_cli(
        "deepresearch", "start", "--project", ".", "--question", question, cwd=tmp_path
    )
    assert _payload(result)["packet"]["next_expedition"]["question_id"] == "q1"


def _add_source(
    tmp_path: Path, ref: str, claims: list[dict], *, tool: str = "web_fetch"
) -> dict:
    return _payload(
        _run_cli(
            "deepresearch",
            "add-source",
            "--project",
            ".",
            "--url-or-path",
            ref,
            "--tool",
            tool,
            "--claims-json",
            json.dumps(claims),
            cwd=tmp_path,
        )
    )


def _seed_contradiction(tmp_path: Path) -> None:
    """c1 (supports) then c2 (contradicts c1) — leaves open contradiction x1."""
    _add_source(
        tmp_path, "https://a.example/x", [{"text": "gate runs per turn", "stance": "supports"}]
    )
    _add_source(
        tmp_path,
        "https://b.example/y",
        [
            {
                "text": "gate runs per hour only",
                "stance": "contradicts",
                "relates_claim": "c1",
            }
        ],
    )


def _resolve_question(
    tmp_path: Path,
    *,
    question_id: str,
    answer: str,
    evidence: list[str],
    resolutions: list[str] | None = None,
):
    args = [
        "deepresearch",
        "resolve-question",
        "--project",
        ".",
        "--question-id",
        question_id,
        "--answer",
        answer,
        "--evidence-claims",
        *evidence,
    ]
    for resolution in resolutions or []:
        args += ["--contradiction-resolution", resolution]
    return _run_cli(*args, cwd=tmp_path)


def test_full_round_trip_resolves_question_and_writes_report(tmp_path: Path) -> None:
    _start(tmp_path)
    added = _payload(
        _run_cli(
            "deepresearch",
            "add-source",
            "--project",
            ".",
            "--url-or-path",
            "docs/quota.md",
            "--tool",
            "local_read",
            "--claims-json",
            json.dumps(
                [
                    {"text": "every continuation enters through quota should-run", "stance": "supports"},
                ]
            ),
            cwd=tmp_path,
        )
    )
    assert added["source_id"] == "s1"
    assert added["claim_ids"] == ["c1"]

    resolved = _payload(
        _run_cli(
            "deepresearch",
            "resolve-question",
            "--project",
            ".",
            "--question-id",
            "q1",
            "--answer",
            "Every agent-loop continuation is admitted by quota should-run.",
            "--evidence-claims",
            "c1",
            cwd=tmp_path,
        )
    )
    stop = resolved["packet"]["stop_conditions"]
    assert stop["stopped"] is True
    assert "all questions answered" in stop["reasons"]

    report = _payload(_run_cli("deepresearch", "report", "--project", ".", cwd=tmp_path))
    report_path = Path(report["report_path"])
    assert report_path.is_file()
    content = report_path.read_text(encoding="utf-8")
    assert "docs/quota.md" in content
    assert "`c1`" in content


def test_status_without_state_reports_exact_gate(tmp_path: Path) -> None:
    result = _run_cli("deepresearch", "status", "--project", ".", cwd=tmp_path)
    data = json.loads(result.stdout)
    assert data["ok"] is False
    assert "deepresearch start" in data["error"]


def test_duplicate_source_is_rejected_so_claims_are_reused(tmp_path: Path) -> None:
    _start(tmp_path)
    args = (
        "deepresearch",
        "add-source",
        "--project",
        ".",
        "--url-or-path",
        "https://example.com/a/",
        "--tool",
        "web_fetch",
    )
    _payload(_run_cli(*args, "--claims-json", "[]", cwd=tmp_path))
    duplicate = _run_cli(*args, "--claims-json", "[]", cwd=tmp_path)
    data = json.loads(duplicate.stdout)
    assert data["ok"] is False
    assert "already recorded as s1" in data["error"]


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("https://example.org/Report", "https://example.org/report"),
        ("https://example.org/?Key=value", "https://example.org/?key=value"),
        ("https://example.org/?key=Value", "https://example.org/?key=value"),
        ("https://example.org/#Section", "https://example.org/#section"),
        ("https://example.org/report", "https://example.org/report/"),
        ("https://example.org/?key=value", "https://example.org/?key=value/"),
        ("https://example.org/#section", "https://example.org/#section/"),
        ("https://User@example.org/report", "https://user@example.org/report"),
        ("https://example.org/report", "https://example.org/report?"),
        ("https://example.org/report", "https://example.org/report#"),
    ],
)
def test_distinct_http_sources_preserve_locator_and_claim_lineage(
    tmp_path: Path, first: str, second: str
) -> None:
    start_research(tmp_path, question="Compare sources", max_sources=3, max_subquestions=0)
    for index, ref in enumerate((first, second), start=1):
        result = add_source(
            tmp_path, url_or_path=ref, tool="fixture", title=None,
            claims=[{"text": f"Observation {index}"}],
        )
        assert result["source_id"] == f"s{index}"
        assert result["claim_ids"] == [f"c{index}"]
    state = load_state(tmp_path)
    assert state is not None
    assert [source["url_or_path"] for source in state["sources"]] == [first, second]
    assert [claim["source_id"] for claim in state["claims"]] == ["s1", "s2"]


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("HTTPS://EXAMPLE.ORG/Report", "https://example.org/Report"),
        ("HTTP://EXAMPLE.ORG/Report", "http://example.org/Report"),
        ("https://example.org", "https://example.org/"),
        ("https://example.org?Key=Value", "https://EXAMPLE.ORG/?Key=Value"),
        ("https://example.org#Section", "https://EXAMPLE.ORG/#Section"),
        ("https://User@EXAMPLE.ORG:8443/Report", "https://User@example.org:8443/Report"),
        ("https://example.org/report?", "https://example.org/report?"),
        ("https://example.org/report#", "https://example.org/report#"),
        ("Docs/Report.md/", "docs/report.md"),
    ],
)
def test_equivalent_sources_are_rejected_without_mutating_the_ledger(
    tmp_path: Path, first: str, second: str
) -> None:
    start_research(tmp_path, question="Compare sources", max_sources=3, max_subquestions=0)
    add_source(
        tmp_path, url_or_path=first, tool="fixture", title=None,
        claims=[{"text": "Original observation"}],
    )
    before = load_state(tmp_path)
    with pytest.raises(ValueError, match="already recorded as s1"):
        add_source(
            tmp_path, url_or_path=second, tool="fixture", title=None,
            claims=[{"text": "Duplicate observation"}],
        )
    assert load_state(tmp_path) == before


def test_cli_records_case_sensitive_sources_in_the_report(tmp_path: Path) -> None:
    _start(tmp_path)
    refs = ("https://example.org/Report", "https://example.org/report")
    for index, ref in enumerate(refs, start=1):
        added = _add_source(tmp_path, ref, [{"text": f"Observation {index}"}])
        assert added["source_id"] == f"s{index}"
    _payload(_resolve_question(
        tmp_path, question_id="q1", answer="The sources are distinct.",
        evidence=["c1", "c2"],
    ))
    report = _payload(_run_cli("deepresearch", "report", "--project", ".", cwd=tmp_path))
    content = Path(report["report_path"]).read_text(encoding="utf-8")
    assert all(ref in content for ref in refs)
    state = load_state(tmp_path)
    assert state is not None
    assert [claim["source_id"] for claim in state["claims"]] == ["s1", "s2"]


def test_answers_without_ledger_evidence_are_rejected(tmp_path: Path) -> None:
    _start(tmp_path)
    result = _run_cli(
        "deepresearch",
        "resolve-question",
        "--project",
        ".",
        "--question-id",
        "q1",
        "--answer",
        "I remember reading something once.",
        "--evidence-claims",
        "c99",
        cwd=tmp_path,
    )
    data = json.loads(result.stdout)
    assert data["ok"] is False
    assert "not recorded" in data["error"]


def test_subquestions_must_derive_from_recorded_claims(tmp_path: Path) -> None:
    _start(tmp_path)
    free_roam = _run_cli(
        "deepresearch",
        "add-subquestion",
        "--project",
        ".",
        "--text",
        "something unrelated I got curious about",
        "--priority",
        "P1",
        "--from-claim",
        "c7",
        cwd=tmp_path,
    )
    assert "not a recorded claim" in json.loads(free_roam.stdout)["error"]

    # Omitting --from-claim must fail too: the argparse layer must not offer a
    # lineage bypass the domain layer rejects.
    omitted = _run_cli(
        "deepresearch",
        "add-subquestion",
        "--project",
        ".",
        "--text",
        "no lineage provided",
        "--priority",
        "P1",
        cwd=tmp_path,
    )
    assert omitted.returncode != 0

    _payload(
        _run_cli(
            "deepresearch",
            "add-source",
            "--project",
            ".",
            "--url-or-path",
            "docs/skills.md",
            "--tool",
            "local_read",
            "--claims-json",
            json.dumps([{"text": "skills are discovered from host roots", "stance": "neutral"}]),
            cwd=tmp_path,
        )
    )
    derived = _payload(
        _run_cli(
            "deepresearch",
            "add-subquestion",
            "--project",
            ".",
            "--text",
            "which roots exist per host?",
            "--priority",
            "P1",
            "--from-claim",
            "c1",
            cwd=tmp_path,
        )
    )
    assert derived["question_id"] == "q2"

    # Python callers must hit the same wall, not just argparse users.
    from loopx.capabilities.deep_research.runtime import (
        add_subquestion as domain_add_subquestion,
    )

    try:
        domain_add_subquestion(tmp_path, text="bypass attempt", priority="P1", from_claim=None)
    except ValueError as error:
        assert "from_claim is required" in str(error)
    else:
        raise AssertionError("domain layer accepted a subquestion without lineage")


def test_contradiction_blocks_resolution_until_sides_with_rationale(tmp_path: Path) -> None:
    _start(tmp_path)
    _seed_contradiction(tmp_path)
    status = _payload(_run_cli("deepresearch", "status", "--project", ".", cwd=tmp_path))
    assert status["packet"]["ledger_summary"]["open_contradictions"] == 1

    blocked = _resolve_question(
        tmp_path, question_id="q1", answer="something", evidence=["c1"]
    )
    assert "open contradiction x1" in json.loads(blocked.stdout)["error"]

    resolved = _payload(
        _resolve_question(
            tmp_path,
            question_id="q1",
            answer="The gate runs per turn; the hourly source described a different cadence.",
            evidence=["c1"],
            resolutions=[
                "contradiction-id=x1 sides-with=c1 rationale='primary source shows per-turn admission'"
            ],
        )
    )
    assert resolved["packet"]["ledger_summary"]["open_contradictions"] == 0


def test_in_call_resolution_rejects_citing_both_sides_of_contradiction(tmp_path: Path) -> None:
    # Reviewer probe (r6): resolving q1 with evidence citing both c1 and c2
    # while adjudicating sides-with=c1 must be rejected before state mutation.
    _start(tmp_path)
    _seed_contradiction(tmp_path)
    result = _resolve_question(
        tmp_path,
        question_id="q1",
        answer="The gate runs per turn.",
        evidence=["c1", "c2"],
        resolutions=[
            "contradiction-id=x1 sides-with=c1 rationale='primary source shows per-turn admission'"
        ],
    )
    data = json.loads(result.stdout)
    assert data["ok"] is False
    assert "resolved against c2" in data["error"]
    state = json.loads(
        (tmp_path / ".loopx" / "deepresearch" / "research.json").read_text(encoding="utf-8")
    )
    assert state["questions"][0]["status"] == "open"
    assert state["contradictions"][0]["status"] == "open"


def test_pre_resolved_contradiction_rejects_citing_both_sides(tmp_path: Path) -> None:
    _start(tmp_path)
    _seed_contradiction(tmp_path)
    _payload(
        _run_cli(
            "deepresearch",
            "resolve-contradiction",
            "--project",
            ".",
            "--contradiction-id",
            "x1",
            "--sides-with",
            "c1",
            "--rationale",
            "primary source is authoritative",
            cwd=tmp_path,
        )
    )
    result = _resolve_question(
        tmp_path,
        question_id="q1",
        answer="Per turn.",
        evidence=["c1", "c2"],
    )
    data = json.loads(result.stdout)
    assert data["ok"] is False
    assert "resolved against c2" in data["error"]
    state = json.loads(
        (tmp_path / ".loopx" / "deepresearch" / "research.json").read_text(encoding="utf-8")
    )
    assert state["questions"][0]["status"] == "open"


def test_coverage_stop_when_recent_sources_add_no_claims(tmp_path: Path) -> None:
    _start(tmp_path)
    for n in range(3):
        _payload(
            _run_cli(
                "deepresearch",
                "add-source",
                "--project",
                ".",
                "--url-or-path",
                f"https://empty.example/{n}",
                "--tool",
                "web_fetch",
                "--claims-json",
                "[]",
                cwd=tmp_path,
            )
        )
    status = _payload(_run_cli("deepresearch", "status", "--project", ".", cwd=tmp_path))
    stop = status["packet"]["stop_conditions"]
    assert stop["stopped"] is True
    assert any("coverage stop" in reason for reason in stop["reasons"])
    assert status["packet"]["next_expedition"] is None


def test_open_contradiction_blocks_completion_until_resolved(tmp_path: Path) -> None:
    # Reviewer repro: c1, a claim contradicting it (c2), then an unrelated c3;
    # resolving q1 with c3 only must NOT let the packet claim completion.
    _start(tmp_path)
    _seed_contradiction(tmp_path)
    _add_source(
        tmp_path, "https://c.example/z", [{"text": "unrelated detail", "stance": "neutral"}]
    )
    _payload(
        _resolve_question(
            tmp_path,
            question_id="q1",
            answer="Something answered on unrelated evidence.",
            evidence=["c3"],
        )
    )
    status = _payload(_run_cli("deepresearch", "status", "--project", ".", cwd=tmp_path))
    stop = status["packet"]["stop_conditions"]
    assert stop["stopped"] is False
    assert any("open contradiction" in reason for reason in stop["reasons"])
    expedition = status["packet"]["next_expedition"]
    assert expedition is not None
    assert expedition["kind"] == "contradiction_resolution"

    resolved = _payload(
        _run_cli(
            "deepresearch",
            "resolve-contradiction",
            "--project",
            ".",
            "--contradiction-id",
            "x1",
            "--sides-with",
            "c1",
            "--rationale",
            "primary source shows per-turn admission",
            cwd=tmp_path,
        )
    )
    assert resolved["packet"]["stop_conditions"]["stopped"] is True


def test_resolution_cannot_side_with_claim_outside_answer_evidence(tmp_path: Path) -> None:
    # Reviewer repro: x1 records c2 contradicting c1; resolving q1 citing only
    # c1 while adjudicating sides-with=c2 used to succeed, so the citation
    # report could back the answer with the side the same transition ruled
    # against. The adjudicated side must be part of the answer's evidence.
    _start(tmp_path)
    _seed_contradiction(tmp_path)
    result = _resolve_question(
        tmp_path,
        question_id="q1",
        answer="Per hour.",
        evidence=["c1"],
        resolutions=["contradiction-id=x1 sides-with=c2 rationale='hourly source is primary'"],
    )
    data = json.loads(result.stdout)
    assert data["ok"] is False
    assert "not among this question's evidence claims" in data["error"]
    state = json.loads(
        (tmp_path / ".loopx" / "deepresearch" / "research.json").read_text(encoding="utf-8")
    )
    assert state["questions"][0]["status"] == "open"


def test_pre_resolved_contradiction_rejects_overruled_evidence(tmp_path: Path) -> None:
    # Same hole through two calls: adjudicate x1 standalone (sides-with c2),
    # then answer citing only the overruled c1.
    _start(tmp_path)
    _seed_contradiction(tmp_path)
    _payload(
        _run_cli(
            "deepresearch",
            "resolve-contradiction",
            "--project",
            ".",
            "--contradiction-id",
            "x1",
            "--sides-with",
            "c2",
            "--rationale",
            "hourly source is primary",
            cwd=tmp_path,
        )
    )
    result = _resolve_question(
        tmp_path, question_id="q1", answer="Per turn.", evidence=["c1"]
    )
    data = json.loads(result.stdout)
    assert data["ok"] is False
    assert "resolved against c1" in data["error"]


def test_standalone_resolution_cannot_overrule_answered_evidence(tmp_path: Path) -> None:
    # Reviewer probe (r5): answer q1 citing c1 first, THEN add contradicting
    # c2 and adjudicate x1 standalone toward c2. That used to succeed and left
    # the ledger claiming the answer rests on a side it ruled against.
    _start(tmp_path)
    _add_source(
        tmp_path,
        "https://a.example/x",
        [{"text": "gate runs per turn", "stance": "supports"}],
    )
    _payload(_resolve_question(tmp_path, question_id="q1", answer="Per turn.", evidence=["c1"]))
    _add_source(
        tmp_path,
        "https://b.example/y",
        [
            {
                "text": "gate runs per hour only",
                "stance": "contradicts",
                "relates_claim": "c1",
            }
        ],
    )

    overruled = _run_cli(
        "deepresearch",
        "resolve-contradiction",
        "--project",
        ".",
        "--contradiction-id",
        "x1",
        "--sides-with",
        "c2",
        "--rationale",
        "hourly source is primary",
        cwd=tmp_path,
    )
    data = json.loads(overruled.stdout)
    assert data["ok"] is False
    assert "cited as evidence by answered question q1" in data["error"]
    state = json.loads(
        (tmp_path / ".loopx" / "deepresearch" / "research.json").read_text(encoding="utf-8")
    )
    assert state["contradictions"][0]["status"] == "open"

    # Siding with the cited side stays legal and unblocks completion.
    consistent = _payload(
        _run_cli(
            "deepresearch",
            "resolve-contradiction",
            "--project",
            ".",
            "--contradiction-id",
            "x1",
            "--sides-with",
            "c1",
            "--rationale",
            "primary source shows per-turn admission",
            cwd=tmp_path,
        )
    )
    assert consistent["packet"]["stop_conditions"]["stopped"] is True


def test_in_call_resolution_cannot_overrule_other_answered_question(tmp_path: Path) -> None:
    # The same invariant on the resolve-question path: adjudicating x1 toward
    # c2 while answering q2 must not overrule c1 already cited by answered q1.
    _start(tmp_path)
    _add_source(
        tmp_path,
        "https://a.example/x",
        [{"text": "gate runs per turn", "stance": "supports"}],
    )
    _payload(_resolve_question(tmp_path, question_id="q1", answer="Per turn.", evidence=["c1"]))
    _add_source(
        tmp_path,
        "https://b.example/y",
        [
            {
                "text": "gate runs per hour only",
                "stance": "contradicts",
                "relates_claim": "c1",
            }
        ],
    )
    _payload(
        _run_cli(
            "deepresearch",
            "add-subquestion",
            "--project",
            ".",
            "--text",
            "Which cadence is correct?",
            "--from-claim",
            "c2",
            cwd=tmp_path,
        )
    )
    result = _resolve_question(
        tmp_path,
        question_id="q2",
        answer="Hourly.",
        evidence=["c2"],
        resolutions=["contradiction-id=x1 sides-with=c2 rationale='hourly source is primary'"],
    )
    data = json.loads(result.stdout)
    assert data["ok"] is False
    assert "cited as evidence by answered question q1" in data["error"]


def test_self_contradiction_is_rejected(tmp_path: Path) -> None:
    _start(tmp_path)
    result = _run_cli(
        "deepresearch",
        "add-source",
        "--project",
        ".",
        "--url-or-path",
        "https://self.example/a",
        "--tool",
        "web_fetch",
        "--claims-json",
        json.dumps(
            [{"text": "claims contradiction with itself", "stance": "contradicts", "relates_claim": "c1"}]
        ),
        cwd=tmp_path,
    )
    data = json.loads(result.stdout)
    assert data["ok"] is False
    assert "not a recorded claim" in data["error"]


def test_invalid_batch_persists_nothing(tmp_path: Path) -> None:
    _start(tmp_path)
    result = _run_cli(
        "deepresearch",
        "add-source",
        "--project",
        ".",
        "--url-or-path",
        "https://partial.example/a",
        "--tool",
        "web_fetch",
        "--claims-json",
        json.dumps(
            [
                {"text": "valid claim first", "stance": "supports"},
                {"text": "invalid stance second", "stance": "sideways"},
            ]
        ),
        cwd=tmp_path,
    )
    assert json.loads(result.stdout)["ok"] is False
    state = json.loads(
        (tmp_path / ".loopx" / "deepresearch" / "research.json").read_text(encoding="utf-8")
    )
    assert state["sources"] == []
    assert state["claims"] == []


def test_concurrent_add_source_keeps_every_update(tmp_path: Path) -> None:
    # The reviewer's probe: unlocked read-modify-write lost 17/20 concurrent
    # updates. Under the project lock every source must survive.
    _start(tmp_path, question="concurrency contract")
    processes = [
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "loopx.cli",
                "--format",
                "json",
                "deepresearch",
                "add-source",
                "--project",
                ".",
                "--url-or-path",
                f"https://race.example/{index}",
                "--tool",
                "web_fetch",
                "--claims-json",
                json.dumps([{"text": f"claim {index}", "stance": "neutral"}]),
            ],
            cwd=tmp_path,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for index in range(8)
    ]
    outputs = [process.communicate()[0] for process in processes]
    for process, output in zip(processes, outputs):
        assert process.returncode == 0, output
        assert json.loads(output)["ok"] is True
    state = json.loads(
        (tmp_path / ".loopx" / "deepresearch" / "research.json").read_text(encoding="utf-8")
    )
    assert len(state["sources"]) == 8
    assert len(state["claims"]) == 8


def test_second_run_lifecycle_close_then_start(tmp_path: Path) -> None:
    # Reviewer repro: after one completed run, a second start used to demand
    # deleting internal state files by hand. The typed lifecycle must keep the
    # capability runnable per project forever.
    _start(tmp_path, question="first question")
    _payload(
        _run_cli(
            "deepresearch",
            "add-source",
            "--project",
            ".",
            "--url-or-path",
            "docs/one.md",
            "--tool",
            "local_read",
            "--claims-json",
            json.dumps([{"text": "first claim", "stance": "supports"}]),
            cwd=tmp_path,
        )
    )
    _payload(
        _run_cli(
            "deepresearch",
            "resolve-question",
            "--project",
            ".",
            "--question-id",
            "q1",
            "--answer",
            "First answer.",
            "--evidence-claims",
            "c1",
            cwd=tmp_path,
        )
    )
    _payload(_run_cli("deepresearch", "report", "--project", ".", cwd=tmp_path))
    state_file = tmp_path / ".loopx" / "deepresearch" / "research.json"
    assert state_file.is_file()

    # While stopped-but-open, a plain start is refused with a typed pointer…
    blocked = _run_cli(
        "deepresearch", "start", "--project", ".", "--question", "second question", cwd=tmp_path
    )
    blocked_data = json.loads(blocked.stdout)
    assert blocked_data["ok"] is False
    assert "deepresearch close" in blocked_data["error"]

    # …while an ACTIVE run points at closing first, not at deleting files.
    second = _run_cli(
        "deepresearch",
        "start",
        "--project",
        ".",
        "--question",
        "second question",
        "--new-run",
        cwd=tmp_path,
    )
    assert json.loads(second.stdout)["ok"] is True
    fresh = json.loads(state_file.read_text(encoding="utf-8"))
    assert fresh["question"] == "second question"
    assert fresh["status"] == "active"
    archive_dirs = list((tmp_path / ".loopx" / "deepresearch" / "archive").iterdir())
    assert len(archive_dirs) == 1
    assert (archive_dirs[0] / "research.json").is_file()
    assert (archive_dirs[0] / "report.md").is_file()

    # An active (not stopped) run requires an explicit close.
    active = _run_cli(
        "deepresearch",
        "start",
        "--project",
        ".",
        "--question",
        "third question",
        "--new-run",
        cwd=tmp_path,
    )
    active_data = json.loads(active.stdout)
    assert active_data["ok"] is False
    assert "active research run exists" in active_data["error"]

    closed = _payload(
        _run_cli(
            "deepresearch", "close", "--project", ".", "--note", "moving on", cwd=tmp_path
        )
    )
    assert closed["run_status"] == "closed"
    restart = _payload(
        _run_cli(
            "deepresearch",
            "start",
            "--project",
            ".",
            "--question",
            "third question",
            cwd=tmp_path,
        )
    )
    assert restart["packet"]["run_status"] == "active"
    assert len(list((tmp_path / ".loopx" / "deepresearch" / "archive").iterdir())) == 2


def test_lock_contention_returns_typed_json_not_traceback(tmp_path: Path) -> None:
    # Reviewer probe: holding the state lock made the CLI print a traceback
    # under --format json. The JSON contract must survive lock timeouts.
    from loopx.capabilities.deep_research.runtime import state_path
    from loopx.file_lock import exclusive_file_lock

    _start(tmp_path)
    with exclusive_file_lock(state_path(tmp_path)):
        result = _run_cli(
            "deepresearch",
            "status",
            "--project",
            ".",
            cwd=tmp_path,
        )
        contended = _run_cli(
            "deepresearch",
            "add-source",
            "--project",
            ".",
            "--url-or-path",
            "https://locked.example/a",
            "--tool",
            "web_fetch",
            "--claims-json",
            "[]",
            cwd=tmp_path,
        )
    # status is a read-only path and stays usable; the mutation hits the lock.
    assert json.loads(result.stdout)["ok"] is True
    data = json.loads(contended.stdout)
    assert data["ok"] is False
    assert data["error_code"] == "lock_acquire_timeout"
    assert "Traceback" not in contended.stdout
    assert "Traceback" not in contended.stderr


def test_deep_research_is_a_registered_capability() -> None:
    from loopx.capabilities.catalog import BUILTIN_CAPABILITIES

    entry = next(
        (item for item in BUILTIN_CAPABILITIES if item["id"] == "deep-research"), None
    )
    assert entry is not None
    assert entry["entry_command"] == "loopx deepresearch start --question <text>"
    boundaries = " ".join(entry["boundaries"])
    # The ownership question the reviewer asked must be answered in the catalog
    # itself: how this capability differs from auto-research.
    assert "auto-research" in boundaries
    assert entry["next_real_step"]


def test_closed_run_rejects_every_ledger_mutation(tmp_path: Path) -> None:
    # Reviewer probe: `start -> close -> add-source` used to return ok=true and
    # silently mutate a terminal run's ledger. Every mutation must fail closed.
    _start(tmp_path)
    _payload(
        _run_cli(
            "deepresearch",
            "add-source",
            "--project",
            ".",
            "--url-or-path",
            "docs/before-close.md",
            "--tool",
            "local_read",
            "--claims-json",
            json.dumps(
                [
                    {"text": "seed claim", "stance": "supports"},
                    {"text": "seed claim two", "stance": "neutral"},
                ]
            ),
            cwd=tmp_path,
        )
    )
    _payload(_run_cli("deepresearch", "close", "--project", ".", cwd=tmp_path))

    mutations = [
        ("deepresearch", "add-source", "--project", ".",
         "--url-or-path", "docs/after-close.md", "--tool", "local_read",
         "--claims-json", json.dumps([{"text": "late claim", "stance": "neutral"}])),
        ("deepresearch", "add-subquestion", "--project", ".",
         "--text", "late question", "--from-claim", "c1"),
        ("deepresearch", "resolve-question", "--project", ".",
         "--question-id", "q1", "--answer", "late answer", "--evidence-claims", "c1"),
        ("deepresearch", "resolve-contradiction", "--project", ".",
         "--contradiction-id", "x1", "--sides-with", "c1", "--rationale", "late"),
    ]
    for args in mutations:
        result = _run_cli(*args, cwd=tmp_path)
        data = json.loads(result.stdout)
        assert data["ok"] is False, args
        assert "closed" in data["error"], args
    state = json.loads(
        (tmp_path / ".loopx" / "deepresearch" / "research.json").read_text(encoding="utf-8")
    )
    assert state["status"] == "closed"
    assert len(state["sources"]) == 1
    assert len(state["claims"]) == 2
    assert state["questions"][0]["status"] == "open"


def test_closed_packet_projects_terminal_guidance_not_expedition(tmp_path: Path) -> None:
    # Reviewer repro: `start -> early close -> status` used to return
    # run_status=closed with stop_conditions.stopped=false and a runnable
    # next_expedition — an instruction the same ledger would reject. The
    # closed packet must project the terminal state instead.
    _start(tmp_path)
    closed = _payload(_run_cli("deepresearch", "close", "--project", ".", cwd=tmp_path))
    packet = closed["packet"]
    assert packet["run_status"] == "closed"
    assert packet["stop_conditions"]["stopped"] is False  # q1 is still open
    assert packet["next_expedition"] is None
    guidance = packet["terminal_guidance"]
    assert guidance["ledger_immutable"] is True
    assert "deepresearch start" in guidance["next_start"]

    status = _payload(_run_cli("deepresearch", "status", "--project", ".", cwd=tmp_path))
    assert status["packet"]["next_expedition"] is None
    assert status["packet"]["terminal_guidance"]["ledger_immutable"] is True


def test_report_generation_is_serialized_with_run_rotation(tmp_path: Path) -> None:
    # Reviewer barrier probe: a report that read the previous run must never
    # land next to the next run's state file. Report and close/start rotation
    # share one lifecycle lock, so the paused-report interleaving resolves by
    # serialization, not by luck.
    import threading

    from loopx.capabilities.deep_research import runtime as deepresearch
    from loopx.capabilities.deep_research.runtime import start_research, write_report

    _start(tmp_path, question="old question")
    # Make the old run stopped so the interleaved start (--new-run) can rotate
    # it once the report releases the lifecycle lock.
    _payload(
        _run_cli(
            "deepresearch",
            "add-source",
            "--project",
            ".",
            "--url-or-path",
            "docs/old.md",
            "--tool",
            "local_read",
            "--claims-json",
            json.dumps([{"text": "old claim", "stance": "supports"}]),
            cwd=tmp_path,
        )
    )
    _payload(
        _run_cli(
            "deepresearch",
            "resolve-question",
            "--project",
            ".",
            "--question-id",
            "q1",
            "--answer",
            "Old answer.",
            "--evidence-claims",
            "c1",
            cwd=tmp_path,
        )
    )

    render_entered = threading.Event()
    release_render = threading.Event()
    original_render = deepresearch.render_report

    def paused_render(state: dict) -> str:
        if state["question"] == "old question":
            render_entered.set()
            assert release_render.wait(timeout=10)
        return original_render(state)

    deepresearch.render_report = paused_render
    try:
        report_done: list[dict] = []

        def run_report() -> None:
            report_done.append(write_report(tmp_path))

        thread = threading.Thread(target=run_report)
        thread.start()
        assert render_entered.wait(timeout=10)

        # While the paused report holds the lifecycle lock, rotation must wait.
        start_done: list[dict] = []

        def run_start() -> None:
            start_done.append(
                start_research(
                    tmp_path,
                    question="new question",
                    max_sources=12,
                    max_subquestions=8,
                    new_run=True,
                )
            )

        starter = threading.Thread(target=run_start)
        starter.start()
        starter.join(timeout=1.5)
        assert not start_done, "rotation completed while the report held the lifecycle lock"

        release_render.set()
        thread.join(timeout=10)
        starter.join(timeout=10)
        assert report_done and start_done
    finally:
        deepresearch.render_report = original_render

    root_state = json.loads(
        (tmp_path / ".loopx" / "deepresearch" / "research.json").read_text(encoding="utf-8")
    )
    assert root_state["question"] == "new question"
    # The serialized outcome: the paused report — rendered from the old run —
    # travelled WITH the old run into the archive. The root never shows a
    # report from a different run than its state (here: no report at all
    # until the new run generates one).
    root_report = tmp_path / ".loopx" / "deepresearch" / "report.md"
    if root_report.exists():
        assert "new question" in root_report.read_text(encoding="utf-8")
        assert "old question" not in root_report.read_text(encoding="utf-8")
    archive_dirs = list((tmp_path / ".loopx" / "deepresearch" / "archive").iterdir())
    assert len(archive_dirs) == 1
    archived_report = (archive_dirs[0] / "report.md").read_text(encoding="utf-8")
    assert "old question" in archived_report


def test_skill_facade_installs_for_skill_facade_surfaces(tmp_path: Path) -> None:
    from loopx.slash_command_install import install_slash_commands

    # The facade spec is host-generic: every skill-facade surface (gemini,
    # cursor, and agy once its surface PR merges) installs it from the same
    # specs list, so proving one surface proves the wiring.
    home = tmp_path / "gemini-home"
    payload = install_slash_commands(execute=True, surfaces=["gemini"], gemini_home=str(home))
    assert payload["ok"] is True
    skill = home / "skills" / "loopx-deepresearch" / "SKILL.md"
    assert skill.is_file()
    body = skill.read_text(encoding="utf-8")
    assert "deepresearch status" in body
    assert "never fabricate URLs" in body
