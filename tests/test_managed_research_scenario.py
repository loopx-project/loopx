"""The example oracle must reject false conclusions and unadopted dependencies."""
from hashlib import sha256
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples" / "managed-research-team"))
from scenario import assignments, encoded, evidence, validate_worker, validate_report  # noqa: E402


def fixture(root: Path) -> dict:
    dependencies = {}
    for member in assignments(root):
        worker, revision = member["worker"], member["revision"]
        raw, normalized, stale, source = {
            "initial": (90, 40, False, "filing-initial"),
            "corrected": (75, 25, True, "filing-correction"),
        }[revision]
        work = root / worker / revision
        work.mkdir(parents=True, exist_ok=True)
        input_bytes = encoded(evidence(revision))
        (work / "input.json").write_bytes(input_bytes)
        output = {"revision": revision, "input_sha256": sha256(input_bytes).hexdigest(),
                  "raw_fcf": raw, "normalized_fcf": normalized, "period_comparable": False,
                  "growth_supported": False, "independent_source_families": 1, "repost_stale": stale,
                  "source_refs": [source, "prior-filing", "repost"], "reason": "Different periods; one source family."}
        if member.get("upstream"):
            previous = json.loads((root / member["upstream"] / "output.json").read_text())
            output["adopted_dependencies"] = {member["upstream"]: sha256(encoded(previous)).hexdigest()}
        (work / "output.json").write_bytes(encoded(output))
        (root / "accepted").mkdir(exist_ok=True)
        (root / "accepted" / (worker + "-" + revision + ".json")).write_bytes(encoded({"turn_status": "committed", "evidence": output}))
        dependencies[worker + "/" + revision] = sha256(encoded(output)).hexdigest()
    report = {"initial_normalized_fcf": 40, "corrected_normalized_fcf": 25, "revision_delta": -15,
              "growth_supported": False, "independent_source_families": 1,
              "repost_stale_after_correction": True, "dependencies": dependencies,
              "reason": "Correction reduces normalized cash; growth is unsupported."}
    (root / "lead").mkdir(exist_ok=True)
    (root / "lead" / "report.json").write_bytes(encoded(report))
    return report


def test_valid_dependency_adoption(tmp_path):
    fixture(tmp_path)
    assert validate_report(tmp_path)["revision_delta"] == -15


def test_prior_filing_must_be_cited_without_counting_it_as_current_corroboration(tmp_path):
    fixture(tmp_path)
    work = tmp_path / "analyst" / "initial"
    output = json.loads((work / "output.json").read_text())
    output["source_refs"].remove("prior-filing")
    (work / "output.json").write_bytes(encoded(output))
    with pytest.raises(ValueError, match="worker_source_refs_missing:prior-filing"):
        validate_worker(work, "initial")


@pytest.mark.parametrize("field,value", [("normalized_fcf", 75), ("period_comparable", True),
                                         ("growth_supported", True), ("independent_source_families", 2),
                                         ("independent_source_families", True), ("repost_stale", False)])
def test_worker_rejects_wrong_semantics(tmp_path, field, value):
    fixture(tmp_path)
    work = tmp_path / "reviewer" / "corrected"
    output = json.loads((work / "output.json").read_text())
    output[field] = value
    (work / "output.json").write_bytes(encoded(output))
    with pytest.raises(ValueError, match="rejected"):
        validate_worker(work, "corrected")


@pytest.mark.parametrize("mutation", ["stale_hash", "changed_input", "missing_output", "wrong_aggregate"])
def test_report_rejects_broken_dependencies(tmp_path, mutation):
    report = fixture(tmp_path)
    if mutation == "stale_hash":
        report["dependencies"]["analyst/corrected"] = report["dependencies"]["analyst/initial"]
    elif mutation == "wrong_aggregate":
        report["growth_supported"] = True
    elif mutation == "changed_input":
        (tmp_path / "analyst" / "corrected" / "input.json").write_text("{}")
    else:
        (tmp_path / "analyst" / "corrected" / "output.json").unlink()
    (tmp_path / "lead" / "report.json").write_bytes(encoded(report))
    with pytest.raises((ValueError, FileNotFoundError)):
        validate_report(tmp_path)


@pytest.mark.parametrize("counts", [(1, 1, 1), (3, 1, 2), (1, 3, 1)])
def test_mixed_team_consumes_every_configured_member(tmp_path, counts):
    from scenario import roster

    team = roster("mixed", counts)
    assert len({row["worker"] for row in team}) == sum(counts)
    assert [sum(row["host"] == host for row in team) for host in ("codex", "dsh", "ark")] == list(counts)
    (tmp_path / "project").mkdir()
    (tmp_path / "project" / "team.json").write_bytes(encoded(team))
    report = fixture(tmp_path)
    assert validate_report(tmp_path)["revision_delta"] == -15
    # Extra workers cannot become decorative: omitting ANY of their accepted
    # artifacts invalidates the aggregate, even when all numbers still agree.
    for member in team:
        identity = member["worker"] + "/" + member["revision"]
        omitted = {**report, "dependencies": {k: v for k, v in report["dependencies"].items() if k != identity}}
        (tmp_path / "lead" / "report.json").write_bytes(encoded(omitted))
        with pytest.raises(ValueError, match="lead_did_not_adopt_dependency"):
            validate_report(tmp_path)


@pytest.mark.parametrize("counts", [(0, 1, 1), (-1, 1, 1), (True, 1, 1), (1, 1)])
def test_invalid_counts_do_not_prepare_state(tmp_path, counts):
    import research_team as demo

    root = tmp_path / "invalid"
    with pytest.raises(ValueError, match="positive_luna_dsh_ark"):
        demo.prepare(root, topology="mixed", team_size=counts)
    assert not root.exists()
