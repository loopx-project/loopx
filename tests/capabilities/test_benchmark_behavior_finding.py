"""Exploratory findings preserve provenance without acquiring score authority."""

import copy
import json

import pytest

from loopx.capabilities.benchmark_toolkit.behavior_finding import (
    build_benchmark_behavior_report,
    normalize_benchmark_behavior_finding,
)
from loopx.capabilities.benchmark_toolkit.study_projection import (
    build_benchmark_upload_envelope,
    read_benchmark_local_upload_records,
    read_benchmark_upload_receipt,
    simulate_benchmark_upload,
)
from tests.source_cli import run_source_cli


def finding():
    return {
        "schema_version": "benchmark_behavior_finding_v0",
        "benchmark_id": "fixture-bench",
        "study_id": "exploration",
        "finding_id": "validation-feedback",
        "title": "Retaining a counterexample",
        "claim_scope": "exploratory_behavior",
        "settings": "Fixed model and tool budget.",
        "selection": {
            "basis": "post_hoc",
            "rule": "Cases with reviewed counterexamples.",
            "unit": "case",
            "population_count": 8,
            "sample_count": 2,
            "cohort_digest": "a" * 64,
        },
        "observation": "A retained failing probe preceded a repair.",
        "interpretation": "Persisting evidence may support revisiting assumptions.",
        "measures": [
            {
                "name": "Successful tasks",
                "unit": "case",
                "aggregation": "count",
                "groups": [
                    {"label": "reference", "value": 1, "n": 2},
                    {"label": "candidate", "value": 2, "n": 2},
                ],
                "caveat": "Selected sample; no population estimate.",
            }
        ],
        "evidence": [
            {
                "kind": "case_insight",
                "digest": "b" * 64,
                "label": "Reviewed example",
                "relation": "supports",
                "summary": "The same probe failed, prompted an edit, and passed.",
            }
        ],
        "limitations": ["Selected cases do not establish a general effect."],
        "counterevidence": "Another task repeatedly tested an incorrect oracle.",
        "next_probe": "Fix the sample before varying the validation policy.",
        "privacy_classification": "public_safe",
        "producer_redaction_attested": True,
    }


def envelope(payload=None, **overrides):
    options = {
        "record_kind": "behavior_finding",
        "producer_id": "researcher",
        "producer_version": "v1",
        "benchmark_id": "fixture-bench",
        "study_id": "exploration",
        "idempotency_key": "finding-v1",
        "observed_at": "2026-01-01T00:00:00+00:00",
        "source_revision": "fixture-revision",
    }
    options.update(overrides)
    return build_benchmark_upload_envelope(payload or finding(), **options)


def test_finding_only_upload_preview_replay_readback_and_report(tmp_path):
    store = tmp_path / "findings.jsonl"
    e = envelope()
    assert simulate_benchmark_upload(store, e)["disposition"] == "preview_accepted"
    assert not store.exists()
    assert (
        simulate_benchmark_upload(store, e, execute=True)["disposition"] == "accepted"
    )
    assert (
        simulate_benchmark_upload(store, e, execute=True)["disposition"] == "replayed"
    )
    receipt = read_benchmark_upload_receipt(store, record_id=e["record_id"])
    assert receipt["payload_digest"] == e["payload_digest"]
    records = read_benchmark_local_upload_records(store)
    report = build_benchmark_behavior_report(
        records, benchmark_id="fixture-bench", study_id="exploration"
    )
    assert report["score_authority"] is False
    assert report["findings"][0]["finding"] == finding()
    assert len(records) == 1  # No manifest, run row, or full study is required.
    assert (
        build_benchmark_behavior_report(
            records, benchmark_id="fixture-bench", study_id="other"
        )["findings"]
        == []
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("claim_scope", "causal_effect"),
        ("producer_redaction_attested", False),
        ("limitations", []),
        ("counterevidence", ""),
        ("evidence", []),
        ("raw_trajectory", "private material"),
        ("score_countable", True),
    ],
)
def test_reject_missing_boundaries_or_score_and_raw_slots(field, value):
    p = finding()
    p[field] = value
    with pytest.raises((TypeError, ValueError)):
        normalize_benchmark_behavior_finding(p)


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p["selection"].update(sample_count=9),
        lambda p: p["selection"].update(sample_count=True),
        lambda p: p["selection"].update(cohort_digest="not-a-digest"),
        lambda p: p["measures"][0]["groups"][0].update(value=float("nan")),
        lambda p: p["measures"][0]["groups"][0].update(n=3),
        lambda p: p["measures"][0]["groups"][0].update(value=3),
        lambda p: p["measures"][0]["groups"][0].update(value=-1),
        lambda p: p["measures"][0].update(aggregation="rate"),
        lambda p: p["evidence"][0].update(raw_log="private"),
    ],
)
def test_reject_invalid_sampling_numeric_and_evidence_contract(change):
    p = finding()
    change(p)
    with pytest.raises((TypeError, ValueError)):
        normalize_benchmark_behavior_finding(p)


def test_all_available_requires_the_complete_declared_population():
    p = finding()
    p["selection"]["basis"] = "all_available"
    with pytest.raises(ValueError, match="sample_count == population_count"):
        normalize_benchmark_behavior_finding(p)

    p["selection"]["sample_count"] = p["selection"]["population_count"]
    assert normalize_benchmark_behavior_finding(p)["selection"]["basis"] == "all_available"


@pytest.mark.parametrize(
    "path,value",
    [
        (("benchmark_id",), 7),
        (("study_id",), True),
        (("finding_id",), {"not": "a token"}),
        (("title",), {"not": "text"}),
        (("settings",), 7),
        (("selection", "rule"), False),
        (("selection", "unit"), 7),
        (("observation",), {"not": "text"}),
        (("interpretation",), 7),
        (("measures", 0, "name"), False),
        (("measures", 0, "unit"), {"not": "a token"}),
        (("measures", 0, "groups", 0, "label"), 7),
        (("measures", 0, "caveat"), False),
        (("evidence", 0, "label"), {"not": "text"}),
        (("evidence", 0, "summary"), 7),
        (("limitations", 0), {"not": "text"}),
        (("counterevidence",), False),
        (("next_probe",), 7),
    ],
)
def test_text_and_token_fields_reject_non_string_json(path, value):
    p = finding()
    target = p
    for segment in path[:-1]:
        target = target[segment]
    target[path[-1]] = value
    with pytest.raises(TypeError, match="must be a string"):
        normalize_benchmark_behavior_finding(p)


def test_revision_requires_explicit_supersession_preserving_finding(tmp_path):
    store = tmp_path / "records.jsonl"
    first = envelope()
    simulate_benchmark_upload(store, first, execute=True)
    changed = finding()
    changed["interpretation"] = (
        "A narrower interpretation after reviewing another case."
    )
    second = envelope(changed, idempotency_key="finding-v2")
    with pytest.raises(ValueError, match="explicit supersession"):
        simulate_benchmark_upload(store, second, execute=True)
    second = envelope(
        changed, idempotency_key="finding-v2", supersedes_record_id=first["record_id"]
    )
    simulate_benchmark_upload(store, second, execute=True)
    report = build_benchmark_behavior_report(
        read_benchmark_local_upload_records(store),
        benchmark_id="fixture-bench",
        study_id="exploration",
    )
    assert len(report["findings"]) == 1
    assert (
        report["findings"][0]["finding"]["interpretation"] == changed["interpretation"]
    )
    wrong = copy.deepcopy(changed)
    wrong["finding_id"] = "unrelated"
    with pytest.raises(ValueError, match="finding identity"):
        simulate_benchmark_upload(
            store,
            envelope(
                wrong, idempotency_key="v3", supersedes_record_id=second["record_id"]
            ),
            execute=True,
        )


def test_cli_projects_finding_store(tmp_path):
    store = tmp_path / "records.jsonl"
    simulate_benchmark_upload(store, envelope(), execute=True)
    result = run_source_cli(
        [
            "benchmark",
            "behavior-report",
            "--store",
            str(store),
            "--benchmark-id",
            "fixture-bench",
            "--study-id",
            "exploration",
            "--format",
            "json",
        ],
        check=True,
    )
    assert (
        json.loads(result.stdout)["findings"][0]["finding"]["finding_id"]
        == "validation-feedback"
    )


def test_documented_example_upload_envelope_cli():
    result = run_source_cli(
        [
            "benchmark",
            "upload-envelope",
            "--payload-json",
            "examples/benchmark-behavior-finding.json",
            "--record-kind",
            "behavior_finding",
            "--producer-id",
            "researcher",
            "--producer-version",
            "v1",
            "--benchmark-id",
            "fixture-bench",
            "--study-id",
            "exploration",
            "--idempotency-key",
            "finding-v1",
            "--observed-at",
            "2026-01-01T00:00:00+00:00",
            "--source-revision",
            "fixture-revision",
            "--format",
            "json",
        ],
        check=True,
    )
    assert json.loads(result.stdout) == envelope()
